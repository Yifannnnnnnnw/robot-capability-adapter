#!/usr/bin/env python3
"""Build the isolated, non-formal corrected R2/R3 inputs."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
R1_CONFIG = REPOSITORY_ROOT / "experiment/experiment1b_use/corrected_r1/config"

AUDIT_ID = "AA2-B2-CORRECTED-R23"
AUDIT_REVISION = "1.0.0"
MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M8")
TASK_ORDER = (
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)
TASK_ROBOT = {
    "mw_push_to_goal": "robotstudio_so101",
    "mw_sweep_into_goal": "robotstudio_so101",
    "mw_pick_place": "robotstudio_so101",
    "mw_pick_place_wall": "robotstudio_so101",
    "mw_dial_turn": "robotstudio_so101",
    "GO2-T02": "unitree-go2-stock-12dof",
    "GO2-T03": "unitree-go2-stock-12dof",
    "GO2-T06": "unitree-go2-stock-12dof",
    "GO2-T16": "unitree-go2-stock-12dof",
    "GO2-T17": "unitree-go2-stock-12dof",
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPOSITORY_ROOT))


def _task_suite() -> dict[str, Any]:
    suite = copy.deepcopy(_read_object(R1_CONFIG / "task_suite.json"))
    if (
        suite.get("artifact_type") != "b2_corrected_r1_task_suite"
        or suite.get("audit_identity")
        != {"document_id": "AA2-B2-CORRECTED-R1", "revision": "1.0.0"}
        or suite.get("replicate_plan") != {"replicate_ids": ["R1"]}
    ):
        raise ValueError("source corrected-R1 task suite identity changed")
    seen: set[str] = set()
    for robot in suite.get("robot_suites", []):
        if not isinstance(robot, dict):
            raise ValueError("source corrected robot suite is invalid")
        for task in robot.get("tasks", []):
            if not isinstance(task, dict):
                raise ValueError("source corrected task is invalid")
            task_id = task.get("task_id")
            if task_id not in TASK_ROBOT or TASK_ROBOT[task_id] != robot.get(
                "robot_configuration_id"
            ):
                raise ValueError(f"unexpected corrected task/robot pair: {task_id}")
            inputs = task.get("replicate_inputs")
            if not isinstance(inputs, list) or len(inputs) != 1:
                raise ValueError(f"{task_id} does not contain exactly one R1 input")
            r1 = inputs[0]
            if (
                not isinstance(r1, dict)
                or r1.get("replicate_id") != "R1"
                or r1.get("reset_seed") is not None
                or r1.get("reset_seed_applied") is not False
            ):
                raise ValueError(f"{task_id} R1 reset declaration changed")
            task["replicate_inputs"] = []
            for replicate_id in ("R2", "R3"):
                replicated = copy.deepcopy(r1)
                replicated["replicate_id"] = replicate_id
                task["replicate_inputs"].append(replicated)
            lineage = task.get("protocol_lineage")
            if isinstance(lineage, dict):
                lineage["corrected_audit_replicate_count"] = 3
                lineage["coverage_note"] = (
                    "Corrected R1-R3 use the exact same canonical scene, reset, "
                    "and initial state in fresh episodes; they test repeatability only."
                )
            seen.add(str(task_id))
    if seen != set(TASK_ORDER):
        raise ValueError("source corrected suite does not contain the ten fixed tasks")
    suite["artifact_type"] = "b2_corrected_r23_task_suite"
    suite["audit_identity"] = {
        "document_id": AUDIT_ID,
        "revision": AUDIT_REVISION,
    }
    suite["replicate_plan"] = {"replicate_ids": ["R2", "R3"]}
    suite["source_corrected_r1"] = {
        "artifact_type": "b2_corrected_r1_task_suite",
        "audit_identity": {
            "document_id": "AA2-B2-CORRECTED-R1",
            "revision": "1.0.0",
        },
        "inputs_changed_beyond_replicate_identity": False,
        "equivalence_basis": (
            "Each emitted R2/R3 input is a deep copy of the sealed corrected-R1 "
            "input with only replicate_id changed."
        ),
    }
    return suite


def _units() -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    for replicate_id in ("R2", "R3"):
        for task_id in TASK_ORDER:
            robot_id = TASK_ROBOT[task_id]
            for model_id in MODELS:
                units.append(
                    {
                        "unit_id": (
                            f"b2-corrected-r23::{robot_id}::{task_id}::"
                            f"{model_id}::{replicate_id}"
                        ),
                        "robot_configuration_id": robot_id,
                        "task_id": task_id,
                        "model_id": model_id,
                        "replicate_id": replicate_id,
                        "execution_origin": "fresh_corrected",
                    }
                )
    return units


def _manifest(selection: dict[str, Any]) -> dict[str, Any]:
    units = _units()
    if len(units) != 140 or len({item["unit_id"] for item in units}) != 140:
        raise AssertionError("corrected R2/R3 block is not 140 unique units")
    return {
        "artifact_type": "b2_corrected_r23_manifest",
        "schema_version": "1.0",
        "audit_identity": {"document_id": AUDIT_ID, "revision": AUDIT_REVISION},
        "source_formal_authority": {"document_id": "AA2-B2", "revision": "0.1.4"},
        "formal_episode": False,
        "formal_denominator_entry": False,
        "models": list(MODELS),
        "replicate_ids": ["R2", "R3"],
        "fresh_execution_units": 140,
        "retained_rejudication_units": 0,
        "replacement_units": 0,
        "execution_origin_counts": {"fresh_corrected": 140},
        "corrected_combined_plan": 210,
        "source_formal_plan_modified": False,
        "claim_boundary": (
            "R1 plus R2/R3 form a non-formal corrected 210-unit audit and do "
            "not replace the source AA2-B2 formal cohort."
        ),
        "paths": {
            "task_suite": _relative(HERE / "task_suite.json"),
            "provider_manifest": (
                "experiment/experiment1b_use/config/providers/manifest.json"
            ),
            "reference_selection": _relative(R1_CONFIG / "reference/selection.json"),
            "positive_control_index": _relative(
                CORRECTED_ROOT / "runs/positive-controls/positive_control_index.json"
            ),
        },
        "reference_selection": selection,
        "fresh_units": units,
        "dispatch_order": list(TASK_ORDER),
        "retry_policy": {
            "controller_or_model_failure": "no_retry",
            "confirmed_infrastructure_failure": "same_input_once",
        },
    }


def build() -> None:
    suite = _task_suite()
    selection = _read_object(R1_CONFIG / "reference/selection.json")
    _write_object(HERE / "task_suite.json", suite)
    _write_object(HERE / "manifest.json", _manifest(selection))


if __name__ == "__main__":
    build()
    print(_relative(HERE / "manifest.json"))
