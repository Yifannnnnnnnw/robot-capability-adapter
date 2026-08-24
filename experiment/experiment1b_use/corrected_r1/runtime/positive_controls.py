#!/usr/bin/env python3
"""Run the seven fixed-driver positive controls that gate corrected dispatch."""

from __future__ import annotations

import argparse
import copy
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoadapter2.b2.recap import RecapBudgets
from autoadapter2.libraries import load_robot_package

from .episode_runner import CorrectedEpisodeConfig, run_corrected_episode
from .harness import AUDIT_ID, AUDIT_REVISION


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CORRECTED_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = CORRECTED_ROOT / "config"
DEFAULT_OUTPUT = CORRECTED_ROOT / "runs" / "positive-controls"
REQUIRED_TASK_IDS = (
    "GO2-T02",
    "GO2-T03",
    "GO2-T06",
    "GO2-T16",
    "GO2-T17",
    "mw_pick_place_wall",
    "mw_dial_turn",
)
R23_REQUIRED_TASK_IDS = (
    "mw_push_to_goal",
    "mw_sweep_into_goal",
    "mw_pick_place",
    *REQUIRED_TASK_IDS,
)
TASK_ROBOT = {
    "mw_push_to_goal": "robotstudio_so101",
    "mw_sweep_into_goal": "robotstudio_so101",
    "mw_pick_place": "robotstudio_so101",
    "GO2-T02": "unitree-go2-stock-12dof",
    "GO2-T03": "unitree-go2-stock-12dof",
    "GO2-T06": "unitree-go2-stock-12dof",
    "GO2-T16": "unitree-go2-stock-12dof",
    "GO2-T17": "unitree-go2-stock-12dof",
    "mw_pick_place_wall": "robotstudio_so101",
    "mw_dial_turn": "robotstudio_so101",
}


def _leaf(capability_name: str, request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reasoning_summary": "Execute the next fixed positive-control leaf.",
        "subtasks": [
            {
                "kind": "capability",
                "capability_name": capability_name,
                "request": copy.deepcopy(dict(request)),
            }
        ],
    }


def _twist(x: float, y: float, duration_s: float) -> dict[str, Any]:
    return _leaf(
        "track_planar_twist",
        {
            "linear_velocity_body_m_s": [x, y],
            "yaw_rate_rad_s": 0.0,
            "duration_s": duration_s,
        },
    )


def _hold(duration_s: float = 2.0) -> dict[str, Any]:
    return _leaf("hold_stable_stance", {"duration_s": duration_s})


def _trace_planar(
    waypoints: Sequence[Sequence[float]], max_duration_s: float
) -> dict[str, Any]:
    return _leaf(
        "trace_planar_path",
        {
            "waypoints_initial_yaw_m": [list(point) for point in waypoints],
            "max_duration_s": max_duration_s,
        },
    )


def _plans(
    artifact_prefix: str = "b2_corrected_r1",
) -> dict[str, list[dict[str, Any]]]:
    wall_pregrasp = (0.35 + 0.17453) / 1.91986
    wall_grasp = (0.25 + 0.17453) / 1.91986
    plans = {
        "mw_push_to_goal": [
            _leaf("set_gripper_opening", {"opening_fraction": 0.0, "max_duration_s": 0.5}),
            _leaf(
                "approach_until_contact",
                {
                    "precontact_position_m": [0.38, 0.03, 0.188],
                    "approach_direction_unit": [0.0, 1.0, 0.0],
                    "max_travel_m": 0.08,
                    "max_approach_speed_m_s": 0.04,
                    "max_duration_s": 3.0,
                },
            ),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [[0.38, 0.12, 0.188], [0.38, 0.18, 0.188]],
                    "max_duration_per_segment_s": 3.0,
                },
            ),
        ],
        "mw_sweep_into_goal": [
            _leaf("set_gripper_opening", {"opening_fraction": 0.0, "max_duration_s": 0.5}),
            _leaf(
                "approach_until_contact",
                {
                    "precontact_position_m": [0.38, 0.03, 0.2],
                    "approach_direction_unit": [0.0, 1.0, 0.0],
                    "max_travel_m": 0.08,
                    "max_approach_speed_m_s": 0.04,
                    "max_duration_s": 3.0,
                },
            ),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [[0.38, 0.14, 0.2], [0.38, 0.21, 0.2]],
                    "max_duration_per_segment_s": 3.0,
                },
            ),
        ],
        "mw_pick_place": [
            _leaf("set_gripper_opening", {"opening_fraction": 1.0, "max_duration_s": 0.5}),
            _leaf(
                "set_wrist_roll",
                {"target_roll_rad": -math.pi / 2.0, "max_duration_s": 2.0},
            ),
            _leaf(
                "move_end_effector_to_position",
                {"target_position_m": [0.34, 0.08, 0.255], "max_duration_s": 4.0},
            ),
            _leaf(
                "move_end_effector_to_position",
                {"target_position_m": [0.34, 0.08, 0.195], "max_duration_s": 3.0},
            ),
            _leaf("set_gripper_opening", {"opening_fraction": 0.26, "max_duration_s": 1.5}),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [
                        [0.34, 0.08, 0.28],
                        [0.36, 0.03, 0.29],
                        [0.371, -0.03, 0.29],
                        [0.371, -0.078, 0.272],
                    ],
                    "max_duration_per_segment_s": 5.0,
                },
            ),
            _leaf("set_gripper_opening", {"opening_fraction": 1.0, "max_duration_s": 0.5}),
        ],
        "GO2-T02": [_twist(0.20, 0.0, 3.0), _twist(0.20, 0.0, 3.0), _twist(0.20, 0.0, 2.0)],
        "GO2-T03": [_twist(0.20, 0.0, 3.0), _twist(0.20, 0.0, 3.0), _twist(0.20, 0.0, 2.0)],
        "GO2-T06": [
            _twist(0.35, 0.0, 3.5),
            _twist(0.38, 0.0, 3.5),
            _twist(0.38, 0.0, 3.5),
            _twist(0.38, 0.0, 3.5),
            _twist(0.38, 0.0, 3.5),
            _twist(0.35, 0.0, 2.0),
        ],
        "GO2-T16": [
            _twist(0.40, 0.0, 4.0),
            _twist(0.40, 0.0, 2.0),
            _hold(),
            _hold(),
            _hold(),
        ],
        # Five closed-loop velocity leaves target the five public alternating
        # waypoints.  This deterministic transport is only a positive control;
        # the real models receive the ordinary public ReCAP observation.
        "GO2-T17": [
            _twist(0.334, 0.234, 2.0),
            _twist(0.308, -0.400, 2.7),
            _twist(0.319, 0.400, 2.7),
            _twist(0.120, -0.360, 3.2),
            _twist(0.130, 0.000, 2.0),
            _twist(0.320, 0.300, 3.0),
            _hold(1.0),
        ],
        "mw_pick_place_wall": [
            _leaf("set_gripper_opening", {"opening_fraction": 1.0, "max_duration_s": 0.5}),
            _leaf("set_wrist_roll", {"target_roll_rad": -math.pi / 2.0, "max_duration_s": 1.0}),
            _leaf("move_end_effector_to_position", {"target_position_m": [0.29, 0.08, 0.29], "max_duration_s": 3.0}),
            _leaf("set_gripper_opening", {"opening_fraction": wall_pregrasp, "max_duration_s": 0.75}),
            _leaf(
                "approach_until_contact",
                {
                    "precontact_position_m": [0.29, 0.08, 0.255],
                    "approach_direction_unit": [0.0, 0.0, -1.0],
                    "max_travel_m": 0.075,
                    "max_approach_speed_m_s": 0.03,
                    "max_duration_s": 4.0,
                },
            ),
            _leaf("set_gripper_opening", {"opening_fraction": wall_grasp, "max_duration_s": 1.0}),
            _leaf("move_end_effector_to_position", {"target_position_m": [0.29, 0.08, 0.29], "max_duration_s": 3.0}),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [[0.36, 0.08, 0.30], [0.411, 0.082, 0.272]],
                    "max_duration_per_segment_s": 4.0,
                },
            ),
            _leaf("set_gripper_opening", {"opening_fraction": 1.0, "max_duration_s": 0.75}),
        ],
        "mw_dial_turn": [
            _leaf("set_gripper_opening", {"opening_fraction": 0.0, "max_duration_s": 1.0}),
            _leaf("move_end_effector_to_position", {"target_position_m": [0.397, 0.07, 0.37], "max_duration_s": 3.0}),
            _leaf(
                "approach_until_contact",
                {
                    "precontact_position_m": [0.397, 0.07, 0.31],
                    "approach_direction_unit": [0.0, 0.0, -1.0],
                    "max_travel_m": 0.04,
                    "max_approach_speed_m_s": 0.03,
                    "max_duration_s": 3.0,
                },
            ),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [[0.397, 0.0875, 0.2335], [0.397, 0.134, 0.197]],
                    "max_duration_per_segment_s": 4.0,
                },
            ),
        ],
    }
    if artifact_prefix == "b2_corrected_r123_v2":
        # The corrected-R123-v2 fixtures are isolated package revisions.  Keep
        # the older corrected-R1/R23 controls byte-for-byte equivalent while
        # moving only the two revised positive-control trajectories.
        plans["GO2-T17"] = [
            _trace_planar([[0.38, 0.18], [0.40, 0.20]], 2.0),
            _trace_planar([[0.23, -0.02], [0.25, 0.0]], 1.25),
            _trace_planar([[0.38, -0.33], [0.40, -0.35]], 2.0),
            _trace_planar([[0.38, -0.13], [0.40, -0.15]], 2.0),
            _trace_planar([[0.38, 0.38], [0.40, 0.40]], 2.0),
            _trace_planar([[0.38, 0.22], [0.40, 0.24]], 2.0),
            _trace_planar([[0.38, -0.38], [0.40, -0.40]], 2.0),
            _trace_planar([[0.38, -0.22], [0.40, -0.24]], 2.0),
            _trace_planar([[0.38, 0.38], [0.40, 0.40]], 2.0),
            _trace_planar([[0.38, 0.10], [0.40, 0.12]], 2.0),
            _trace_planar([[0.18, -0.02], [0.20, 0.0]], 0.75),
        ]
        plans["mw_dial_turn"] = [
            _leaf("set_gripper_opening", {"opening_fraction": 0.0, "max_duration_s": 1.0}),
            _leaf("move_end_effector_to_position", {"target_position_m": [0.347, 0.07, 0.37], "max_duration_s": 3.0}),
            _leaf(
                "approach_until_contact",
                {
                    "precontact_position_m": [0.347, 0.07, 0.31],
                    "approach_direction_unit": [0.0, 0.0, -1.0],
                    "max_travel_m": 0.04,
                    "max_approach_speed_m_s": 0.03,
                    "max_duration_s": 3.0,
                },
            ),
            _leaf(
                "trace_cartesian_path",
                {
                    "waypoints_m": [[0.347, 0.0875, 0.2335], [0.347, 0.134, 0.197]],
                    "max_duration_per_segment_s": 4.0,
                },
            ),
        ]
    return plans


class ScriptedPositiveControlModel:
    """Transport fixed typed leaves through the real ReCAP/worker path."""

    def __init__(self, responses: Sequence[Mapping[str, Any]]) -> None:
        self._responses = [copy.deepcopy(dict(item)) for item in responses]
        self._responses.append(
            {"reasoning_summary": "Positive control complete.", "subtasks": []}
        )
        self.call_count = 0

    def generate_recap_json(self, **_: Any) -> dict[str, Any]:
        if self.call_count >= len(self._responses):
            raise RuntimeError("positive control made an unexpected model call")
        result = copy.deepcopy(self._responses[self.call_count])
        self.call_count += 1
        return result


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _selection(robot_id: str, *, selection_path: Path) -> dict[str, Any]:
    selection = _read_object(selection_path)
    matches = [
        item
        for item in selection.get("robots", [])
        if isinstance(item, Mapping)
        and item.get("robot_configuration_id") == robot_id
    ]
    if len(matches) != 1:
        raise ValueError(f"reference selection does not resolve {robot_id}")
    return dict(matches[0])


def _resolve_repository_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a repository-relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"{label} must be repository-relative")
    result = (REPOSITORY_ROOT / relative).resolve()
    result.relative_to(REPOSITORY_ROOT)
    return result


def run_task(
    task_id: str,
    *,
    output_root: Path,
    record_video: bool,
    wall_timeout_s: float,
    task_suite_path: Path = CONFIG_ROOT / "task_suite.json",
    selection_path: Path = CONFIG_ROOT / "reference" / "selection.json",
    replicate_id: str = "R1",
    audit_identity: Mapping[str, str] | None = None,
    artifact_prefix: str = "b2_corrected_r1",
) -> dict[str, Any]:
    plans = _plans(artifact_prefix)
    if task_id not in plans:
        raise ValueError(f"unknown corrected positive-control task {task_id!r}")
    robot_id = TASK_ROBOT[task_id]
    selection = _selection(robot_id, selection_path=selection_path)
    package = load_robot_package(
        _resolve_repository_path(selection.get("package_root"), "package_root")
    )
    model = ScriptedPositiveControlModel(plans[task_id])
    task_output = (output_root / "work" / task_id).resolve()
    result = run_corrected_episode(
        config=CorrectedEpisodeConfig(
            task_suite_path=task_suite_path,
            robot_configuration_id=robot_id,
            task_id=task_id,
            replicate_id=replicate_id,
            driver_path=_resolve_repository_path(
                selection.get("driver_path"), "driver_path"
            ),
            capability_design_path=_resolve_repository_path(
                selection.get("capability_design_path"), "capability_design_path"
            ),
            output_dir=task_output,
            record_video=record_video,
            wall_timeout_s=wall_timeout_s,
        ),
        package=package,
        model=model,
        budgets=RecapBudgets(
            max_model_calls=len(plans[task_id]) + 1,
            max_capability_calls=len(plans[task_id]),
            max_depth=1,
            max_subtasks_per_plan=1,
            max_invalid_outputs=1,
            max_history_chars=100_000,
        ),
    )
    harness = result.get("harness")
    worker = result.get("worker")
    video = worker.get("video") if isinstance(worker, Mapping) else None
    trusted_harness = bool(
        isinstance(harness, Mapping)
        and harness.get("physical_harness_verdict") == "PASS"
    )
    video_complete = bool(
        isinstance(video, Mapping)
        and video.get("requested") is True
        and video.get("complete") is True
    )
    passed = trusted_harness and video_complete
    record_path = output_root / "records" / f"{task_id}.json"
    record = {
        "artifact_type": f"{artifact_prefix}_positive_control_record",
        "schema_version": "1.0",
        "audit_identity": dict(
            audit_identity
            or {"document_id": AUDIT_ID, "revision": AUDIT_REVISION}
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "formal_episode": False,
        "control_replicate_id": replicate_id,
        "task_id": task_id,
        "robot_configuration_id": robot_id,
        "driver_kind": "fixed_capability_driver",
        "scripted_recap_transport": True,
        "model_calls": model.call_count,
        "trusted_harness": trusted_harness,
        "video_complete": video_complete,
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "video_path": result.get("episode", {}).get("video_path"),
        "episode": result,
    }
    _write_object(record_path, record)
    return {
        "task_id": task_id,
        "replicate_id": replicate_id,
        "status": record["status"],
        "passed": passed,
        "trusted_harness": trusted_harness,
        "video_complete": video_complete,
        "record_path": str(record_path.resolve()),
        "video_path": record["video_path"],
    }


def _index(
    output_root: Path,
    *,
    required_task_ids: Sequence[str] = REQUIRED_TASK_IDS,
    audit_identity: Mapping[str, str] | None = None,
    artifact_prefix: str = "b2_corrected_r1",
    control_replicate_id: str = "R1",
    covered_replicates: Sequence[str] = ("R1",),
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for task_id in required_task_ids:
        record_path = output_root / "records" / f"{task_id}.json"
        if not record_path.is_file():
            continue
        record = _read_object(record_path)
        results.append(
            {
                "task_id": task_id,
                "replicate_id": record.get("control_replicate_id"),
                "status": record.get("status"),
                "passed": record.get("passed"),
                "trusted_harness": record.get("trusted_harness"),
                "video_complete": record.get("video_complete"),
                "record_path": str(record_path.resolve()),
                "video_path": record.get("video_path"),
            }
        )
    passed = len(results) == len(required_task_ids) and all(
        item.get("replicate_id") == control_replicate_id
        and item.get("status") == "PASS"
        and item.get("passed") is True
        and item.get("trusted_harness") is True
        and item.get("video_complete") is True
        for item in results
    )
    return {
        "artifact_type": f"{artifact_prefix}_positive_control_index",
        "schema_version": "1.0",
        "audit_identity": dict(
            audit_identity
            or {"document_id": AUDIT_ID, "revision": AUDIT_REVISION}
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "formal_episode": False,
        "control_replicate_id": control_replicate_id,
        "covered_replicates": list(covered_replicates),
        "required_task_ids": list(required_task_ids),
        "results": results,
        "gate_passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--replicate-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--task", action="append")
    parser.add_argument("--wall-timeout-s", type=float, default=180.0)
    parser.add_argument("--no-video", action="store_true")
    args = parser.parse_args()
    if args.wall_timeout_s <= 0.0:
        parser.error("wall timeout must be positive")
    if args.manifest is None:
        required_task_ids = REQUIRED_TASK_IDS
        task_suite_path = CONFIG_ROOT / "task_suite.json"
        selection_path = CONFIG_ROOT / "reference" / "selection.json"
        audit_identity = {"document_id": AUDIT_ID, "revision": AUDIT_REVISION}
        artifact_prefix = "b2_corrected_r1"
        replicate_id = args.replicate_id or "R1"
        covered_replicates = ("R1",)
        default_output = DEFAULT_OUTPUT
    else:
        from .dispatch import resolve_corrected_manifest

        manifest = resolve_corrected_manifest(args.manifest)
        required_task_ids = manifest.expected_task_order
        task_suite_path = manifest.task_suite_path
        selection_path = manifest.reference_selection_path
        audit_identity = manifest.audit_identity
        artifact_prefix = manifest.evidence_prefix
        declared_control_replicate = manifest.positive_control_replicate_id
        replicate_id = args.replicate_id or declared_control_replicate or "R1"
        if (
            declared_control_replicate is not None
            and replicate_id != declared_control_replicate
        ):
            parser.error(
                "positive controls are fixed to replicate "
                f"{declared_control_replicate}"
            )
        covered_replicates = (
            manifest.positive_control_covered_replicates or ("R1",)
        )
        default_output = manifest.manifest_path.parent.parent / "runs/positive-controls"
    selected = list(required_task_ids) if args.task is None else args.task
    if any(task_id not in required_task_ids for task_id in selected):
        parser.error("task selection is outside the manifest profile")
    if len(selected) != len(set(selected)):
        parser.error("each task may be selected only once")
    output_root = (args.output or default_output).resolve()
    for task_id in selected:
        result = run_task(
            task_id,
            output_root=output_root,
            record_video=not args.no_video,
            wall_timeout_s=args.wall_timeout_s,
            task_suite_path=task_suite_path,
            selection_path=selection_path,
            replicate_id=replicate_id,
            audit_identity=audit_identity,
            artifact_prefix=artifact_prefix,
        )
        print(
            f"{task_id}: harness={result['trusted_harness']} "
            f"video={result['video_complete']} status={result['status']}"
        )
    index = _index(
        output_root,
        required_task_ids=required_task_ids,
        audit_identity=audit_identity,
        artifact_prefix=artifact_prefix,
        control_replicate_id=replicate_id,
        covered_replicates=covered_replicates,
    )
    _write_object(output_root / "positive_control_index.json", index)
    print(f"gate_passed={index['gate_passed']}")
    return 0 if all(item["passed"] for item in index["results"] if item["task_id"] in selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
