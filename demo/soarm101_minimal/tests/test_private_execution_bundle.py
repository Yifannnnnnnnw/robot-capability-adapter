from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from soarm_demo.private_execution_bundle import (
    PrivateExecutionBundleError,
    materialize_private_execution_bundle,
    private_partition_bundle_sha256,
    verify_private_execution_bundle,
)
from soarm_demo.libraries import load_structured


ROOT = Path(__file__).resolve().parents[1]


def _bundle(tmp_path: Path):
    return materialize_private_execution_bundle(
        private_task_root=(
            ROOT / "private/task_library/soarm101_tabletop/v1"
        ),
        visible_tasks_path=(
            ROOT / "libraries/tasks/soarm101_tabletop/v1/visible_tasks.jsonl"
        ),
        destination=tmp_path / "private_execution_bundle",
        schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
    )


def test_private_execution_bundle_is_schema_valid_and_reopenable(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    freeze = json.loads(bundle.freeze_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "schemas/private_execution_bundle.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(freeze, schema)
    assert freeze["manifest"]["partition_counts"] == {
        "generation_visible_templates": 9,
        "pilot_heldout_templates": 3,
        "authored_instances": 12,
        "selected_visible_instances": 3,
        "selected_pilot_heldout_instances": 3,
        "selected_instances": 6,
    }
    assert len(freeze["manifest"]["files"]) == 23
    assert not any(
        "soarm101_p0_" in item["path"] for item in freeze["manifest"]["files"]
    )

    reopened = verify_private_execution_bundle(
        bundle.freeze_path,
        expected_freeze_sha256=bundle.freeze_sha256,
        schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
    )
    assert reopened.read_json("demo_batch")["status"] == "fixed_before_generation"
    assert len(reopened.read_jsonl("visible_tasks")) == 9
    assert len(reopened.read_jsonl("heldout_tasks")) == 3
    authored = [
        reopened.read_json(f"authored_instance_{ordinal:02d}")
        for ordinal in range(1, 13)
    ]
    assert len({item["task_id"] for item in authored}) == 12
    task_manifest = load_structured(
        ROOT / "libraries/tasks/soarm101_tabletop/v1/manifest.yaml"
    )
    expected_private_hash = task_manifest["content_hashes"][
        "private_partition_bundle"
    ]
    assert bundle.private_partition_source_sha256 == expected_private_hash
    assert private_partition_bundle_sha256(
        ROOT / "private/task_library/soarm101_tabletop/v1"
    ) == expected_private_hash


def test_private_execution_bundle_rejects_stale_library_partition_hash(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        PrivateExecutionBundleError,
        match="library-manifest hash",
    ):
        materialize_private_execution_bundle(
            private_task_root=ROOT / "private/task_library/soarm101_tabletop/v1",
            visible_tasks_path=(
                ROOT / "libraries/tasks/soarm101_tabletop/v1/visible_tasks.jsonl"
            ),
            destination=tmp_path / "private_execution_bundle",
            schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
            expected_private_partition_sha256="0" * 64,
        )


def test_private_execution_bundle_unselected_authored_state_tamper_fails_closed(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    selected_hashes = {
        bundle.sha256_for_role(f"instance_{ordinal:02d}")
        for ordinal in range(1, 7)
    }
    unselected_role = next(
        f"authored_instance_{ordinal:02d}"
        for ordinal in range(1, 13)
        if bundle.sha256_for_role(f"authored_instance_{ordinal:02d}")
        not in selected_hashes
    )
    payload = bundle.path_for_role(unselected_role)
    os.chmod(payload, 0o644)
    payload.write_bytes(payload.read_bytes() + b"\nUNSELECTED_PRIVATE_TAMPER")

    with pytest.raises(PrivateExecutionBundleError, match="payload hash mismatch"):
        bundle.verify()


def test_private_execution_bundle_payload_tamper_fails_closed(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    payload = bundle.path_for_role("instance_01")
    os.chmod(payload, 0o644)
    payload.write_bytes(payload.read_bytes() + b"\nPRIVATE_TAMPER")

    with pytest.raises(PrivateExecutionBundleError, match="payload hash mismatch"):
        bundle.verify()
    with pytest.raises(PrivateExecutionBundleError):
        bundle.read_json("instance_01")


def test_private_execution_bundle_freeze_tamper_fails_before_payload_read(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    os.chmod(bundle.freeze_path, 0o644)
    freeze = json.loads(bundle.freeze_path.read_text(encoding="utf-8"))
    freeze["manifest"]["partition_counts"]["visible"] = 4
    bundle.freeze_path.write_text(json.dumps(freeze), encoding="utf-8")

    with pytest.raises(PrivateExecutionBundleError, match="freeze hash mismatch"):
        verify_private_execution_bundle(
            bundle.freeze_path,
            expected_freeze_sha256=bundle.freeze_sha256,
            schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
        )
