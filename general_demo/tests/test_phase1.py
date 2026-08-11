import json
from pathlib import Path

import pytest

from autoadapter2.artifact_store import LocalArtifactStore
from autoadapter2.contracts import (
    ProfileRegistry,
    RecordRegistry,
    VisibilityGuard,
    validate_fixture_schema,
)
from autoadapter2.foundation import (
    ExactReference,
    IntegrityError,
    ImmutableError,
    ReferenceResolutionError,
    SchemaValidationError,
    VisibilityError,
    canonical_bytes,
    content_hash,
    create_seal,
    verify_seal,
)
from autoadapter2.foundation.jsonl import AppendOnlyJSONL
from autoadapter2.orchestration import (
    RunIndex,
    RunSelection,
    RunSelectionGate,
    GateReceipt,
    RunState,
    RunStateMachine,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_canonical_hash_and_seal_are_stable():
    assert canonical_bytes({"b": 2, "a": 1}) == canonical_bytes({"a": 1, "b": 2})
    digest = content_hash(canonical_bytes({"value": 1}))
    seal = create_seal("fixture.record", digest, ["sha256:" + "1" * 64])
    assert verify_seal(seal)
    seal["artifact_hash"] = content_hash(b"changed")
    with pytest.raises(IntegrityError):
        verify_seal(seal)


def test_exact_reference_resolves_two_fixture_records(tmp_path):
    registry = RecordRegistry(tmp_path / "records", "rim")
    alpha = registry.publish("fixture-alpha", "1.0.0", json.loads((FIXTURES / "fixture_alpha.json").read_text()))
    beta = registry.publish("fixture-beta", "1.0.0", json.loads((FIXTURES / "fixture_beta.json").read_text()))
    assert registry.resolve(alpha.ref).payload["id"] == "fixture-alpha"
    assert registry.resolve(beta.ref).payload["id"] == "fixture-beta"
    with pytest.raises(Exception):
        ExactReference.from_value({"kind": "rim", "id": "fixture-alpha", "version": "1.0.0"})


def test_published_version_cannot_be_overwritten(tmp_path):
    registry = RecordRegistry(tmp_path / "records", "rim")
    registry.publish("fixture-alpha", "1.0.0", {"value": 1})
    with pytest.raises(ImmutableError):
        registry.publish("fixture-alpha", "1.0.0", {"value": 2})


@pytest.mark.parametrize("field, value", [("status", "FROZEN"), ("version", "9.9.9")])
def test_registry_manifest_status_or_version_tamper_is_rejected(tmp_path, field, value):
    registry = RecordRegistry(tmp_path / "records", "rim")
    entry = registry.publish("fixture-alpha", "1.0.0", {"value": 1})
    manifest_path = registry.root / "fixture-alpha" / "1.0.0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReferenceResolutionError):
        registry.resolve(entry.ref)


def test_registry_record_hash_rejects_recomputed_manifest_tamper(tmp_path):
    registry = RecordRegistry(tmp_path / "records", "rim")
    entry = registry.publish(
        "fixture-alpha", "1.0.0",
        {"fixture_only": True, "authority_status": "OPEN"},
    )
    manifest_path = registry.root / "fixture-alpha" / "1.0.0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "FROZEN"
    core = {
        "kind": manifest["kind"],
        "id": manifest["id"],
        "version": manifest["version"],
        "status": manifest["status"],
        "payload_hash": manifest["payload_hash"],
        "freeze_record": {**manifest["freeze_record"], "status": "FROZEN"},
    }
    manifest["record_hash"] = content_hash(canonical_bytes(core))
    manifest["manifest_hash"] = manifest["record_hash"]
    manifest["seal"] = create_seal(
        "registry.manifest", manifest["record_hash"], [manifest["payload_hash"]]
    )
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReferenceResolutionError):
        registry.resolve(entry.ref, require_frozen=True)
    with pytest.raises(ReferenceResolutionError):
        registry.reference("fixture-alpha", "1.0.0")


def test_registry_rejects_directory_payload_and_manifest_symlinks(tmp_path):
    registry = RecordRegistry(tmp_path / "records", "rim")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.json").write_text("{}")
    (outside / "manifest.json").write_text("{}")
    registry.root.mkdir(parents=True)
    (registry.root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception):
        registry.publish("escape", "1.0.0", {"value": 1})

    entry = registry.publish("fixture-alpha", "1.0.0", {"value": 1})
    payload_path = registry.root / "fixture-alpha" / "1.0.0" / "payload.json"
    payload_path.unlink()
    payload_path.symlink_to(outside / "payload.json")
    with pytest.raises(Exception):
        registry.resolve(entry.ref)

    payload_path.unlink()
    payload_path.write_text("{}")
    manifest_path = registry.root / "fixture-alpha" / "1.0.0" / "manifest.json"
    manifest_path.unlink()
    manifest_path.symlink_to(outside / "manifest.json")
    with pytest.raises(Exception):
        registry.reference("fixture-alpha", "1.0.0")


@pytest.mark.parametrize("bad_id", ["../escape", "nested/../../escape", "bad id", ""])
def test_publish_rejects_path_traversal_and_invalid_ids(tmp_path, bad_id):
    registry = RecordRegistry(tmp_path / "records", "rim")
    with pytest.raises(Exception):
        registry.publish(bad_id, "1.0.0", {"value": 1})
    with pytest.raises(Exception):
        registry.publish("safe", "../escape", {"value": 1})


def test_registry_kind_is_validated():
    with pytest.raises(Exception):
        RecordRegistry("unused", "../rim")


def test_content_addressed_store_is_immutable(tmp_path):
    store = LocalArtifactStore(tmp_path / "objects")
    artifact = store.put_bytes(b"stable")
    assert store.get_bytes(artifact.content_hash) == b"stable"
    store._path(artifact.content_hash).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        store.get_bytes(artifact.content_hash)
    with pytest.raises(ValueError):
        store.get_bytes("sha256:" + "a" * 63)


def test_content_addressed_store_rejects_symlink_escape(tmp_path):
    store = LocalArtifactStore(tmp_path / "objects")
    artifact = store.put_bytes(b"stable")
    object_path = store._path(artifact.content_hash)
    object_path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    object_path.symlink_to(outside)
    with pytest.raises(Exception):
        store.get_bytes(artifact.content_hash)


def test_artifact_random_temp_does_not_follow_precreated_tmp_symlink(tmp_path):
    store = LocalArtifactStore(tmp_path / "objects")
    data = b"new-content"
    digest = content_hash(data)
    object_path = store._path(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external"
    external.write_bytes(b"must-not-change")
    predictable_tmp = object_path.with_suffix(".tmp")
    predictable_tmp.symlink_to(external)
    artifact = store.put_bytes(data)
    assert store.get_bytes(artifact.content_hash) == data
    assert external.read_bytes() == b"must-not-change"
    assert predictable_tmp.is_symlink()


def test_json_schema_validation_and_visibility_guard():
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
        "additionalProperties": False,
    }
    validate_fixture_schema({"id": "fixture-alpha"}, schema)
    with pytest.raises(Exception):
        validate_fixture_schema({"id": "fixture-alpha", "private": 1}, schema)
    guard = VisibilityGuard({"stage1": ["public.id", "public.summary"]})
    guard.assert_allowed("stage1", "public.id")
    with pytest.raises(VisibilityError):
        guard.assert_allowed("stage1", "private.criteria")


def test_first_campaign_accepts_only_frozen_g2_fixture(tmp_path):
    rim_registry = RecordRegistry(tmp_path / "rims", "rim")
    rim = rim_registry.publish(
        "fixture-alpha", "1.0.0",
        {"id": "fixture-alpha", "fixture_only": True, "authority_status": "OPEN"},
    )
    registry = ProfileRegistry(tmp_path / "profiles")
    g2 = registry.publish(
        "g2", "1.0.0",
        {"profile_family": "granularity", "granularity": "G2",
         "fixture_only": True, "authority_status": "OPEN"},
        "FROZEN_FIXTURE",
    )
    g1 = registry.publish(
        "g1", "1.0.0",
        {"profile_family": "granularity", "granularity": "G1",
         "fixture_only": True, "authority_status": "OPEN"},
        "FROZEN_FIXTURE",
    )
    g3 = registry.publish(
        "g3", "1.0.0",
        {"profile_family": "granularity", "granularity": "G3",
         "fixture_only": True, "authority_status": "OPEN"},
        "FROZEN_FIXTURE",
    )
    selection = RunSelection("run-1", rim.ref, g2.ref)
    run_index = RunIndex(tmp_path / "runs.jsonl")
    selection_hash = run_index.register(selection)
    gate = RunSelectionGate(rim_registry, registry, run_index)
    assert gate.admit(selection)[1].payload["granularity"] == "G2"
    receipts = gate.receipts(selection)
    assert receipts[0].selection_hash == selection_hash
    machine = RunStateMachine(selection.run_id, selection_hash)
    assert machine.transition(RunState.RIM_RESOLVED, receipts[0]) == RunState.RIM_RESOLVED
    assert machine.transition(RunState.READY_FOR_STAGE1, receipts[1]) == RunState.READY_FOR_STAGE1
    for bad_profile in [
        g1.ref,
        g3.ref,
        {"kind": "profile", "id": "g2", "version": "1.0.0"},
    ]:
        with pytest.raises(Exception):
            gate.admit({
                "run_id": "bad",
                "rim_ref": rim.ref,
                "granularity_profile_ref": bad_profile,
            })
    with pytest.raises(Exception):
        gate.admit({
            "run_id": "run-1",
            "campaign": "arbitrary",
            "rim_ref": rim.ref,
            "granularity_profile_ref": g2.ref,
        })
    for bad_run_id in ["../escape", "run/id", ""]:
        with pytest.raises(Exception):
            gate.admit({
                "run_id": bad_run_id,
                "rim_ref": rim.ref,
                "granularity_profile_ref": g2.ref,
            })
    draft_rim_registry = RecordRegistry(tmp_path / "draft-rims", "rim")
    draft_rim = draft_rim_registry.publish(
        "fixture-draft", "1.0.0",
        {"fixture_only": True, "authority_status": "OPEN"},
        "DRAFT",
    )
    draft_index = RunIndex(tmp_path / "draft-runs.jsonl")
    draft_selection = RunSelection("run-1", draft_rim.ref, g2.ref)
    draft_index.register(draft_selection)
    with pytest.raises(Exception):
        RunSelectionGate(draft_rim_registry, registry, draft_index).admit(draft_selection)
    with pytest.raises(Exception):
        gate.admit({
            "run_id": "bad",
            "rim_refs": [rim.ref.to_dict(), rim.ref.to_dict()],
            "granularity_profile_ref": g2.ref,
        })
    with pytest.raises(Exception):
        gate.admit({
            "run_id": "bad",
            "rim_ref": rim.ref,
            "granularity_profile_refs": [g2.ref.to_dict(), g2.ref.to_dict()],
        })


def test_run_index_detects_tamper_and_truncation(tmp_path):
    index = RunIndex(tmp_path / "runs.jsonl")
    selection = {"run_id": "run-1", "rim_ref": "rim:fixture-alpha@1.0.0#" + "sha256:" + "1" * 64,
                 "granularity_profile_ref": "profile:g2@1.0.0#" + "sha256:" + "2" * 64}
    index.register(selection)
    assert index.get("run-1")["run_id"] == "run-1"
    lines = index.log.path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["event"]["run_id"] = "run-x"
    index.log.path.write_text(json.dumps(tampered) + "\n")
    with pytest.raises(IntegrityError):
        index.records()

    second = RunIndex(tmp_path / "truncated.jsonl")
    second.register(selection)
    second.log.append({"type": "run_registered", "run_id": "run-2", "selection": selection})
    raw = second.log.path.read_bytes()
    second.log.path.write_bytes(raw.splitlines(keepends=True)[0])
    with pytest.raises(IntegrityError):
        second.records()


def test_gate_receipts_are_required_for_state_transitions(tmp_path):
    selection_hash = content_hash(b"registered-selection")
    gate = RunStateMachine(
        run_id="run-1",
        selection_hash=selection_hash,
        initial=RunState.RIM_RESOLVED,
    )
    with pytest.raises(Exception):
        gate.transition(RunState.READY_FOR_STAGE1)
    forged = GateReceipt(
        "run-1",
        "ready_for_stage1",
        content_hash(b"evidence"),
        content_hash(b"receipt"),
        selection_hash,
    )
    with pytest.raises(Exception):
        gate.transition(
            RunState.READY_FOR_STAGE1,
            forged,
        )


def test_closed_run_index_rejects_new_events(tmp_path):
    index = RunIndex(tmp_path / "runs.jsonl")
    selection = {
        "run_id": "run-1",
        "rim_ref": "rim:fixture-alpha@1.0.0#" + "sha256:" + "1" * 64,
        "granularity_profile_ref": "profile:g2@1.0.0#" + "sha256:" + "2" * 64,
    }
    index.register(selection)
    with pytest.raises(KeyError):
        index.append_event("unregistered", {"late": True})
    index.close("run-1")
    with pytest.raises(ImmutableError):
        index.close("run-1")
    with pytest.raises(ImmutableError):
        index.append_event("run-1", {"late": True})


def test_run_state_machine_rejects_illegal_transition():
    machine = RunStateMachine("run", content_hash(b"selection"), RunState.READY_FOR_STAGE1)
    with pytest.raises(Exception):
        machine.transition(RunState.CREATED)
