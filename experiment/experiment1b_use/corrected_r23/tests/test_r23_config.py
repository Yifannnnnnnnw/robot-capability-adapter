from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r1.runtime.dispatch import (  # noqa: E402
    resolve_corrected_manifest,
    select_units,
)
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
    assert corrected_suite_identity(suite) == {
        "document_id": "AA2-B2-CORRECTED-R23",
        "revision": "1.0.0",
    }
    for robot in suite["robot_suites"]:
        for task in robot["tasks"]:
            inputs = task["replicate_inputs"]
            assert [item["replicate_id"] for item in inputs] == ["R1", "R2", "R3"]
            normalized = []
            for item in inputs:
                clone = copy.deepcopy(item)
                clone.pop("replicate_id")
                normalized.append(clone)
            assert normalized[0] == normalized[1] == normalized[2]
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
