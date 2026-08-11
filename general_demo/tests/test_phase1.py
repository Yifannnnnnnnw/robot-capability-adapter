import json
from pathlib import Path

import pytest

from autoadapter2.artifact_store import LocalArtifactStore
from autoadapter2.contracts import ProfileRegistry, RecordRegistry, VisibilityGuard, validate_json
from autoadapter2.foundation import (
    ExactReference,
    IntegrityError,
    ImmutableError,
    SchemaValidationError,
    VisibilityError,
    canonical_bytes,
    content_hash,
    create_seal,
    verify_seal,
)
from autoadapter2.orchestration import RunState, RunStateMachine


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


def test_content_addressed_store_is_immutable(tmp_path):
    store = LocalArtifactStore(tmp_path / "objects")
    artifact = store.put_bytes(b"stable")
    assert store.get_bytes(artifact.content_hash) == b"stable"
    store._path(artifact.content_hash).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        store.get_bytes(artifact.content_hash)


def test_json_schema_validation_and_visibility_guard():
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
        "additionalProperties": False,
    }
    validate_json({"id": "fixture-alpha"}, schema)
    with pytest.raises(SchemaValidationError):
        validate_json({"id": "fixture-alpha", "private": 1}, schema)
    guard = VisibilityGuard({"stage1": ["public.id", "public.summary"]})
    guard.assert_allowed("stage1", "public.id")
    with pytest.raises(VisibilityError):
        guard.assert_allowed("stage1", "private.criteria")


def test_first_campaign_accepts_only_frozen_g2_fixture(tmp_path):
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
    assert registry.resolve(g2.ref, require_frozen=True).payload["granularity"] == "G2"
    assert registry.resolve(g1.ref, require_frozen=True).payload["granularity"] != "G2"


def test_run_state_machine_rejects_illegal_transition():
    machine = RunStateMachine()
    assert machine.transition(RunState.RIM_RESOLVED) == RunState.RIM_RESOLVED
    assert machine.transition(RunState.READY_FOR_STAGE1) == RunState.READY_FOR_STAGE1
    with pytest.raises(Exception):
        machine.transition(RunState.CREATED)
