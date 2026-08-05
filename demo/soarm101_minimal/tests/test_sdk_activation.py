from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml

import soarm_demo.pipeline as pipeline_module
from soarm_demo.pipeline import PipelineError, run_pipeline
from soarm_demo.sdk_activation import (
    SDKActivationError,
    evaluate_sdk_activation,
    freeze_sdk_activation,
    frozen_sdk_activation_semantic_errors,
)


ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0"
SCHEMAS = ROOT / "schemas"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sdk_copy(tmp_path: Path) -> Path:
    target = tmp_path / "sdk"
    shutil.copytree(SDK, target)
    return target


def _read_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refresh_payload_hash(sdk: Path, relative: str) -> None:
    manifest_path = sdk / "manifest.yaml"
    manifest = _read_yaml(manifest_path)
    manifest["content_hashes"][relative] = _sha256(sdk / relative)
    _write_yaml(manifest_path, manifest)


def _failed_probe(probe: dict) -> dict:
    return {
        "schema_version": "robot_capability.sdk_api_probe_result.v1",
        "status": "fail",
        "target": copy.deepcopy(probe["target"]),
        "runtime_id": probe["runtime_id"],
        "environment": copy.deepcopy(probe["environment"]),
        "fixture": probe["fixture"],
        "executed_on": probe["executed_on"],
        "hardware_or_serial_opened": False,
        "error_type": "AssertionError",
        "error": "public surface mismatch",
    }


def test_committed_probe_strictly_activates_aws_and_offline_is_reference_only(
    tmp_path: Path,
) -> None:
    aws = evaluate_sdk_activation(sdk_root=SDK, schemas_root=SCHEMAS, mode="aws")
    offline = evaluate_sdk_activation(
        sdk_root=SDK, schemas_root=SCHEMAS, mode="offline"
    )

    assert aws["live_gate_satisfied"] is True
    assert aws["activation_scope"] == "live_aws_provider_gate"
    assert offline["live_gate_satisfied"] is False
    assert offline["activation_scope"] == "offline_reference_only"
    assert offline["probe_status"] == "pass"
    assert offline["target"]["package"] == "lerobot"
    assert offline["target"]["version"] == "0.6.0"
    assert offline["verified"] == aws["verified"]
    assert offline["verified"]["required_methods"] == [
        "connect",
        "disconnect",
        "send_action",
        "get_observation",
    ]
    assert len(offline["verified"]["action_keys"]) == 6
    assert offline["verified"]["action_keys"] == offline["verified"][
        "observation_keys"
    ]
    assert offline["verified"]["send_action_nonblocking"] is True

    run_root = tmp_path / "run"
    run_root.mkdir()
    frozen = freeze_sdk_activation(
        sdk_root=SDK,
        run_root=run_root,
        decision=offline,
        schema_path=SCHEMAS / "sdk_activation.schema.json",
    )
    jsonschema.validate(
        frozen["decision"],
        json.loads((SCHEMAS / "sdk_activation.schema.json").read_text(encoding="utf-8")),
    )
    assert not (run_root / "framework/sdk_activation_inputs").exists()
    assert frozen_sdk_activation_semantic_errors(
        frozen,
        run_root=run_root,
        input_hashes={},
        artifact_hashes={"sdk_activation": frozen["evidence"]["sha256"]},
        expected_mode="offline",
    ) == ()


def test_sdk_gate_is_independent_of_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_MODEL_API_KEY", "dummy-secret-a")
    first = evaluate_sdk_activation(sdk_root=SDK, schemas_root=SCHEMAS, mode="aws")
    monkeypatch.setenv("AWS_MODEL_API_KEY", "dummy-secret-b")
    second = evaluate_sdk_activation(sdk_root=SDK, schemas_root=SCHEMAS, mode="aws")

    assert first == second
    encoded = json.dumps(first, sort_keys=True)
    assert "dummy-secret" not in encoded
    assert "api_key" not in encoded.lower()


def test_probe_fail_status_cannot_activate_aws(tmp_path: Path) -> None:
    sdk = _sdk_copy(tmp_path)
    probe_path = sdk / "api_probe.json"
    _write_json(probe_path, _failed_probe(_read_json(probe_path)))
    _refresh_payload_hash(sdk, "api_probe.json")

    with pytest.raises(SDKActivationError, match="probe_passed"):
        evaluate_sdk_activation(sdk_root=sdk, schemas_root=SCHEMAS, mode="aws")


@pytest.mark.parametrize(
    ("mutation", "expected_check"),
    [
        (lambda probe: probe["target"].__setitem__("package", "not-lerobot"), "pinned_lerobot_target"),
        (lambda probe: probe.__setitem__("runtime_id", "wrong_runtime"), "runtime_identity"),
        (
            lambda probe: probe["alias_targets"].__setitem__(
                "SO101Follower", "WrongAlias"
            ),
            "canonical_import_and_aliases",
        ),
        (
            lambda probe: probe["public_methods"].remove("connect"),
            "required_public_methods",
        ),
        (lambda probe: probe["action_keys"].reverse(), "six_feature_keys"),
        (
            lambda probe: probe["observation_keys"].reverse(),
            "six_feature_keys",
        ),
        (
            lambda probe: probe["unit_modes"].__setitem__(
                "use_degrees_true_arm", "radians"
            ),
            "unit_semantics",
        ),
        (
            lambda probe: probe["command_trace"].__setitem__(
                "waits_for_arrival", True
            ),
            "nonblocking_send_action",
        ),
    ],
)
def test_contract_mismatches_cannot_activate_even_with_refreshed_manifest_hash(
    tmp_path: Path, mutation, expected_check: str
) -> None:
    sdk = _sdk_copy(tmp_path)
    probe_path = sdk / "api_probe.json"
    probe = _read_json(probe_path)
    mutation(probe)
    _write_json(probe_path, probe)
    _refresh_payload_hash(sdk, "api_probe.json")

    with pytest.raises(SDKActivationError, match=expected_check):
        evaluate_sdk_activation(sdk_root=sdk, schemas_root=SCHEMAS, mode="aws")


def test_undeclared_probe_tamper_is_rejected_by_manifest_hash(tmp_path: Path) -> None:
    sdk = _sdk_copy(tmp_path)
    probe_path = sdk / "api_probe.json"
    probe = _read_json(probe_path)
    probe["executed_on"] = "2026-08-06"
    _write_json(probe_path, probe)

    with pytest.raises(SDKActivationError, match="manifest_payload_hashes"):
        evaluate_sdk_activation(sdk_root=sdk, schemas_root=SCHEMAS, mode="aws")


def test_source_change_between_decision_and_freeze_is_rejected(tmp_path: Path) -> None:
    sdk = _sdk_copy(tmp_path)
    decision = evaluate_sdk_activation(sdk_root=sdk, schemas_root=SCHEMAS, mode="aws")
    probe_path = sdk / "api_probe.json"
    probe_path.write_bytes(probe_path.read_bytes() + b"\n")
    run_root = tmp_path / "run"
    run_root.mkdir()

    with pytest.raises(SDKActivationError, match="changed after evaluation"):
        freeze_sdk_activation(
            sdk_root=sdk,
            run_root=run_root,
            decision=decision,
            schema_path=SCHEMAS / "sdk_activation.schema.json",
        )


def test_activation_record_tamper_breaks_evidence_sha(tmp_path: Path) -> None:
    decision = evaluate_sdk_activation(
        sdk_root=SDK, schemas_root=SCHEMAS, mode="offline"
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    frozen = freeze_sdk_activation(
        sdk_root=SDK,
        run_root=run_root,
        decision=decision,
        schema_path=SCHEMAS / "sdk_activation.schema.json",
    )
    (run_root / "framework/sdk_activation.json").write_bytes(
        (run_root / "framework/sdk_activation.json").read_bytes() + b"\n"
    )

    errors = frozen_sdk_activation_semantic_errors(
        frozen,
        run_root=run_root,
        input_hashes={},
        artifact_hashes={"sdk_activation": frozen["evidence"]["sha256"]},
        expected_mode="offline",
    )
    assert any("evidence hash is stale" in error for error in errors)


def test_pipeline_rejects_failed_probe_before_model_clients_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("configs", "libraries", "private", "fixtures", "prompts", "schemas"):
        shutil.copytree(ROOT / name, demo / name)
    sdk = demo / "libraries/sdk_runtime/lerobot_soarm101/0.6.0"
    probe_path = sdk / "api_probe.json"
    _write_json(probe_path, _failed_probe(_read_json(probe_path)))
    _refresh_payload_hash(sdk, "api_probe.json")
    constructed = 0

    def forbidden_clients(*args, **kwargs):
        nonlocal constructed
        constructed += 1
        raise AssertionError("model clients must not be constructed")

    monkeypatch.setattr(pipeline_module, "_model_clients", forbidden_clients)
    monkeypatch.setenv("SOARM_MUJOCO_VIDEO_LAUNCHER", "1")

    with pytest.raises(PipelineError, match="hardware_free_probe_passed"):
        run_pipeline(demo_root=demo, mode="aws", run_id="sdk-gate-fail")
    assert constructed == 0
    assert not (demo / "runs/sdk-gate-fail").exists()
