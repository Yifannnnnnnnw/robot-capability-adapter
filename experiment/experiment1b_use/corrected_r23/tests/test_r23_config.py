from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r1.runtime.dispatch import (  # noqa: E402
    resolve_corrected_manifest,
    select_units,
)
from experiment.experiment1b_use.corrected_r1.runtime import dispatch  # noqa: E402
from experiment.experiment1b_use.corrected_r1.runtime.harness import (  # noqa: E402
    corrected_suite_identity,
)


R1_MANIFEST = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/config/manifest.json"
)
R23_ROOT = REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r23"
R23_MANIFEST = R23_ROOT / "config/manifest.json"
R23_SUITE = R23_ROOT / "config/task_suite.json"
R1_SUITE = (
    REPOSITORY_ROOT
    / "experiment/experiment1b_use/corrected_r1/config/task_suite.json"
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_r23_manifest_is_the_balanced_140_unit_fresh_block() -> None:
    manifest = resolve_corrected_manifest(R23_MANIFEST)
    assert len(manifest.units) == 140
    assert len({unit.unit_id for unit in manifest.units}) == 140
    assert {unit.execution_origin for unit in manifest.units} == {"fresh_corrected"}
    assert Counter(unit.replicate_id for unit in manifest.units) == {"R2": 70, "R3": 70}
    assert set(Counter(unit.model_id for unit in manifest.units).values()) == {20}
    assert set(Counter(unit.task_id for unit in manifest.units).values()) == {14}
    assert Counter(unit.robot_configuration_id for unit in manifest.units) == {
        "robotstudio_so101": 70,
        "unitree-go2-stock-12dof": 70,
    }


def test_task_and_replicate_filter_selects_exactly_seven_models() -> None:
    manifest = resolve_corrected_manifest(R23_MANIFEST)
    selected = select_units(
        manifest,
        task_ids=["mw_pick_place"],
        replicate_ids=["R3"],
    )
    assert len(selected) == 7
    assert {unit.model_id for unit in selected} == {
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M8",
    }
    assert {unit.replicate_id for unit in selected} == {"R3"}
    assert {unit.task_id for unit in selected} == {"mw_pick_place"}


def test_r1_r2_r3_inputs_are_identical_beyond_replicate_id() -> None:
    suite = _read(R23_SUITE)
    source_suite = _read(R1_SUITE)
    assert corrected_suite_identity(suite) == {
        "document_id": "AA2-B2-CORRECTED-R23",
        "revision": "1.0.0",
    }
    source_inputs = {
        task["task_id"]: task["replicate_inputs"][0]
        for robot in source_suite["robot_suites"]
        for task in robot["tasks"]
    }
    for robot in suite["robot_suites"]:
        for task in robot["tasks"]:
            inputs = task["replicate_inputs"]
            assert [item["replicate_id"] for item in inputs] == ["R2", "R3"]
            normalized = []
            for item in inputs:
                clone = copy.deepcopy(item)
                clone.pop("replicate_id")
                normalized.append(clone)
            source = copy.deepcopy(source_inputs[task["task_id"]])
            source.pop("replicate_id")
            assert normalized[0] == normalized[1] == source
            assert inputs[0]["reset_seed"] is None
            assert inputs[0]["reset_seed_applied"] is False


def test_r1_profile_still_resolves_unchanged() -> None:
    manifest = resolve_corrected_manifest(R1_MANIFEST)
    assert len(manifest.units) == 51
    assert manifest.audit_identity == {
        "document_id": "AA2-B2-CORRECTED-R1",
        "revision": "1.0.0",
    }
    assert {unit.replicate_id for unit in manifest.units} == {"R1"}


def test_r23_scheduler_keeps_profile_identity_and_seven_unit_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = resolve_corrected_manifest(R23_MANIFEST)
    evidence = tmp_path / "control.json"
    evidence.write_text("{}\n", encoding="utf-8")
    video = tmp_path / "control.mp4"
    video.write_bytes(b"video")
    gate = tmp_path / "positive_control_index.json"
    gate.write_text(
        json.dumps(
            {
                "artifact_type": "b2_corrected_r23_positive_control_index",
                "schema_version": "1.0",
                "audit_identity": manifest.audit_identity,
                "control_replicate_id": "R2",
                "covered_replicates": ["R2", "R3"],
                "required_task_ids": list(manifest.expected_task_order),
                "results": [
                    {
                        "task_id": task_id,
                        "replicate_id": "R2",
                        "status": "PASS",
                        "passed": True,
                        "trusted_harness": True,
                        "video_complete": True,
                        "record_path": str(evidence),
                        "video_path": str(video),
                    }
                    for task_id in manifest.expected_task_order
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = replace(manifest, positive_control_index_path=gate)
    monkeypatch.setattr(dispatch, "resolve_corrected_manifest", lambda _path: manifest)
    company_env = tmp_path / "company.env"
    company_env.write_text("KEY=company\n", encoding="utf-8")
    m5_env = tmp_path / "m5.env"
    m5_env.write_text("KEY=m5\n", encoding="utf-8")

    def process_runner(command, **_kwargs):
        unit_id = command[command.index("--unit-id") + 1]
        output = Path(command[command.index("--output") + 1])
        terminal_path = dispatch.corrected_terminal_path(output, unit_id)
        terminal_path.parent.mkdir(parents=True, exist_ok=True)
        terminal_path.write_text(
            json.dumps(
                {
                    "artifact_type": "b2_corrected_r23_unit_terminal",
                    "audit_identity": manifest.audit_identity,
                    "formal_episode": False,
                    "formal_denominator_entry": False,
                    "unit_id": unit_id,
                    "execution_origin": "fresh_corrected",
                    "classification": "harness_fail",
                    "evaluable": True,
                    "success": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    scheduler = dispatch.run_corrected_scheduler(
        manifest_path=manifest.manifest_path,
        output_root=tmp_path / "scheduler",
        company_env_file=company_env,
        m5_env_file=m5_env,
        task_ids=["GO2-T02"],
        replicate_ids=["R3"],
        process_runner=process_runner,
    )
    assert scheduler["artifact_type"] == "b2_corrected_r23_scheduler"
    assert scheduler["audit_identity"] == manifest.audit_identity
    assert scheduler["replicate_filters"] == ["R3"]
    assert len(scheduler["planned_unit_ids"]) == 7
    assert len(scheduler["records"]) == 7
    assert {record["provider_lane"] for record in scheduler["records"]} == {
        "company",
        "m5",
    }
