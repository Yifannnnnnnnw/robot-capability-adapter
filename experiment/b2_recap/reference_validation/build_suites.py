#!/usr/bin/env python3
"""Refresh or validate the fixed B2 reference-driver suite snapshots."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
SELECTION_PATH = HERE / "selection.json"

EXPECTED_SELECTIONS = {
    "robotstudio_so101": {
        "robot_configuration_id": "robotstudio_so101",
        "source_bundle_dir": (
            "experiment/experiment1/fixed_validation_bundles/robotstudio_so101"
        ),
        "resolved_bundle_dir": (
            "experiment/b2_recap/reference_validation/resolved/robotstudio_so101"
        ),
        "package_root": (
            "autoadapter/libraries/robots/robotstudio_so101/1.0.0"
        ),
        "driver_path": (
            "autoadapter/libraries/robots/robotstudio_so101/1.0.0/"
            "reference/fixed_capability_driver.py"
        ),
        "condition": "skeleton-assisted",
    },
    "unitree-go2-stock-12dof": {
        "robot_configuration_id": "unitree-go2-stock-12dof",
        "source_bundle_dir": (
            "experiment/experiment1/fixed_validation_bundles/"
            "unitree-go2-stock-12dof"
        ),
        "resolved_bundle_dir": (
            "experiment/b2_recap/reference_validation/resolved/"
            "unitree-go2-stock-12dof"
        ),
        "package_root": (
            "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0"
        ),
        "driver_path": (
            "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0/"
            "reference/fixed_capability_driver.py"
        ),
        "condition": "skeleton-assisted",
    },
}

# The calibrated SO-101 contact group contains the named distal collision
# primitives and the two distal collision meshes.  In particular, geom_37 and
# the proximal jaw boxes are intentionally absent.
SO101_A4_TOOL_GEOMS = [
    "fixed_jaw_sph_tip1",
    "fixed_jaw_sph_tip2",
    "fixed_jaw_sph_tip3",
    "fixed_jaw_box3",
    "fixed_jaw_box4",
    "fixed_jaw_box5",
    "fixed_jaw_box6",
    "fixed_jaw_box7",
    "geom_47",
    "geom_48",
    "moving_jaw_sph_tip1",
    "moving_jaw_sph_tip2",
    "moving_jaw_sph_tip3",
    "moving_jaw_box2",
    "moving_jaw_box3",
]

SO101_A3_VALIDATION_FRACTIONS = {
    "A3-H1": [0.0, 1.0, 0.2],
    "A3-H2": [1.0, 0.0, 0.8],
    "A3-H3": [0.0, 1.0, 0.5],
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _render_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def load_selection() -> list[dict[str, Any]]:
    document = _read_json(SELECTION_PATH)
    robots = document.get("robots")
    if not isinstance(robots, list) or not robots:
        raise ValueError("selection.json must contain a non-empty robots list")
    if not all(isinstance(robot, dict) for robot in robots):
        raise ValueError("selection.json contains an invalid robot selection")
    observed = {
        str(robot.get("robot_configuration_id")): robot for robot in robots
    }
    if len(observed) != len(robots) or set(observed) != set(EXPECTED_SELECTIONS):
        raise ValueError("selection.json must contain the exact two B2 robots once")
    for robot_id, expected in EXPECTED_SELECTIONS.items():
        if observed[robot_id] != expected:
            raise ValueError(f"selection.json changed the fixed {robot_id} paths")
    return robots


def _resolve_so101_suite(source: Mapping[str, Any]) -> dict[str, Any]:
    suite = copy.deepcopy(dict(source))
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise ValueError("SO-101 source suite has no cases list")

    a3_count = 0
    a4_count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("SO-101 source suite contains an invalid case")
        case_id = case.get("case_id")
        if case_id in SO101_A3_VALIDATION_FRACTIONS:
            request = case.get("request")
            if not isinstance(request, dict):
                raise ValueError(f"{case_id} has no request object")
            duration = request.get("max_duration_s")
            case["validation_request_sequence"] = [
                {
                    "opening_fraction": fraction,
                    "max_duration_s": duration,
                }
                for fraction in SO101_A3_VALIDATION_FRACTIONS[str(case_id)]
            ]
            case["validation_timeout_sim_s"] = max(
                float(case["timeout_sim_s"]),
                3.0 * float(duration) + 0.5,
            )
            a3_count += 1

        if case_id == "A3-H3":
            reset = case.get("reset")
            if not isinstance(reset, dict):
                raise ValueError("A3-H3 has no reset object")
            for field in ("joint_positions", "actuator_controls"):
                values = reset.get(field)
                if not isinstance(values, dict) or "gripper" not in values:
                    raise ValueError(f"A3-H3 reset has no {field}.gripper")
                values["gripper"] = 1.7453292

        if case.get("capability_id") == "A4":
            binding = case.get("binding")
            if not isinstance(binding, dict):
                raise ValueError(f"{case_id} has no binding object")
            parameters = binding.get("parameters")
            if not isinstance(parameters, dict):
                raise ValueError(f"{case_id} has no binding parameters")
            parameters.pop("tool_body_names", None)
            parameters["tool_geom_names"] = list(SO101_A4_TOOL_GEOMS)
            parameters["precontact_gate"] = "held_window_then_ray"
            a4_count += 1

    if a3_count != 3 or a4_count != 3:
        raise ValueError(
            f"expected three A3 and three A4 cases; got {a3_count} and {a4_count}"
        )
    if "geom_37" in SO101_A4_TOOL_GEOMS:
        raise AssertionError("geom_37 must remain outside the B2 A4 contact group")
    return suite


def source_documents(robot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    source_dir = REPOSITORY_ROOT / str(robot["source_bundle_dir"])
    design = _read_json(source_dir / "capability_design.json")
    source_suite = _read_json(source_dir / "capability_validation_suite.json")
    robot_id = str(robot["robot_configuration_id"])
    expected_source_design_id = (
        f"experiment1-b1-fixed-interface::{robot_id}::v1"
    )
    if design.get("capability_design_id") != expected_source_design_id:
        raise ValueError(
            f"{robot_id} source design changed; a prospective B2 snapshot "
            "revision is required before refresh"
        )
    suite = (
        _resolve_so101_suite(source_suite)
        if robot_id == "robotstudio_so101"
        else copy.deepcopy(source_suite)
    )
    source_suite_id = suite.get("suite_id")
    expected_source_suite_id = f"experiment1-b1-fixed-suite::{robot_id}::v1"
    if source_suite_id != expected_source_suite_id:
        raise ValueError(
            f"{robot_id} source suite changed; a prospective B2 snapshot "
            "revision is required before refresh"
        )
    suite["source_suite_id"] = source_suite_id
    suite["suite_id"] = f"b2-fixed-reference-suite::{robot_id}::v1"
    suite["pass_standard_id"] = "experiment1-b1-driver-validation-criteria-v2"
    return {
        "capability_design.json": design,
        "capability_validation_suite.json": suite,
    }


def refresh_from_experiment1() -> list[Path]:
    """Explicitly replace the B2 snapshots from the current B1 bundle state."""

    prepared = [
        (
            REPOSITORY_ROOT / str(robot["resolved_bundle_dir"]),
            source_documents(robot),
        )
        for robot in load_selection()
    ]
    written: list[Path] = []
    for destination, documents in prepared:
        for filename, document in documents.items():
            path = destination / filename
            rendered = _render_json(document)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            written.append(path)
    return written


def validate_resolved() -> None:
    """Validate the committed B2 snapshots without following mutable B1 inputs."""

    expected_methods = {
        "robotstudio_so101": {
            "A1": "move_end_effector_to_position",
            "A2": "trace_cartesian_path",
            "A3": "set_gripper_opening",
            "A4": "approach_until_contact",
            "A5": "move_cartesian_offset_and_return",
        },
        "unitree-go2-stock-12dof": {
            "G1": "track_planar_twist",
            "G2": "move_body_relative_pose",
            "G3": "trace_planar_path",
            "G4": "set_body_height",
            "G5": "hold_stable_stance",
        },
    }
    for robot in load_selection():
        robot_id = str(robot["robot_configuration_id"])
        resolved_dir = REPOSITORY_ROOT / str(robot["resolved_bundle_dir"])
        design = _read_json(resolved_dir / "capability_design.json")
        suite = _read_json(resolved_dir / "capability_validation_suite.json")
        if design.get("robot_configuration_id") != robot_id:
            raise ValueError(f"{robot_id} resolved design has the wrong robot identity")
        if design.get("capability_design_id") != (
            f"experiment1-b1-fixed-interface::{robot_id}::v1"
        ):
            raise ValueError(f"{robot_id} resolved design identity changed")
        if suite.get("robot_configuration_id") != robot_id:
            raise ValueError(f"{robot_id} resolved suite has the wrong robot identity")
        if suite.get("suite_id") != f"b2-fixed-reference-suite::{robot_id}::v1":
            raise ValueError(f"{robot_id} resolved suite has the wrong B2 identity")
        if suite.get("source_suite_id") != (
            f"experiment1-b1-fixed-suite::{robot_id}::v1"
        ):
            raise ValueError(f"{robot_id} resolved suite provenance changed")
        if suite.get("pass_standard_id") != (
            "experiment1-b1-driver-validation-criteria-v2"
        ):
            raise ValueError(f"{robot_id} resolved pass standard changed")

        capabilities = design.get("capabilities")
        if not isinstance(capabilities, list):
            raise ValueError(f"{robot_id} resolved design has no capabilities")
        observed_methods = {
            str(item.get("capability_id")): str(item.get("method_name"))
            for item in capabilities
            if isinstance(item, Mapping)
        }
        if observed_methods != expected_methods[robot_id]:
            raise ValueError(f"{robot_id} resolved capability methods changed")

        cases = suite.get("cases")
        if not isinstance(cases, list) or len(cases) != 15:
            raise ValueError(f"{robot_id} resolved suite must contain 15 cases")
        _validate_case_set(robot_id, cases, expected_methods[robot_id])

        if robot_id == "robotstudio_so101":
            _validate_so101_corrections(cases)


def _validate_case_set(
    robot_id: str,
    cases: Sequence[Any],
    methods: Mapping[str, str],
) -> None:
    expected_cases = {
        f"{capability_id}-H{variant}": (capability_id, method_name)
        for capability_id, method_name in methods.items()
        for variant in (1, 2, 3)
    }
    observed_cases: dict[str, tuple[str, str]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError(f"{robot_id} resolved suite has an invalid case")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or case_id in observed_cases:
            raise ValueError(f"{robot_id} resolved suite has duplicate case IDs")
        observed_cases[case_id] = (
            str(case.get("capability_id")),
            str(case.get("method_name")),
        )
    if observed_cases != expected_cases:
        raise ValueError(f"{robot_id} resolved case set or mapping changed")


def _validate_so101_corrections(cases: list[Any]) -> None:
    for case_id, fractions in SO101_A3_VALIDATION_FRACTIONS.items():
        matches = [case for case in cases if case.get("case_id") == case_id]
        if len(matches) != 1:
            raise ValueError(f"SO-101 resolved suite must contain one {case_id}")
        request = matches[0].get("request")
        sequence = matches[0].get("validation_request_sequence")
        if not isinstance(request, Mapping) or not isinstance(sequence, list):
            raise ValueError(f"SO-101 {case_id} has no validation sequence")
        expected = [
            {
                "opening_fraction": fraction,
                "max_duration_s": request.get("max_duration_s"),
            }
            for fraction in fractions
        ]
        if sequence != expected or sequence[-1] != request:
            raise ValueError(f"SO-101 {case_id} bidirectional sequence changed")
        expected_timeout = max(
            float(matches[0]["timeout_sim_s"]),
            sum(float(value["max_duration_s"]) for value in expected) + 0.5,
        )
        if matches[0].get("validation_timeout_sim_s") != expected_timeout:
            raise ValueError(f"SO-101 {case_id} sequence timeout changed")

    a3_h3 = [case for case in cases if case.get("case_id") == "A3-H3"]
    if len(a3_h3) != 1:
        raise ValueError("SO-101 resolved suite must contain exactly one A3-H3")
    reset = a3_h3[0].get("reset")
    if not isinstance(reset, Mapping):
        raise ValueError("SO-101 A3-H3 has no reset")
    for field in ("joint_positions", "actuator_controls"):
        values = reset.get(field)
        if not isinstance(values, Mapping) or values.get("gripper") != 1.7453292:
            raise ValueError(f"SO-101 A3-H3 {field}.gripper is not fixed open")

    a4_cases = [case for case in cases if case.get("capability_id") == "A4"]
    if len(a4_cases) != 3:
        raise ValueError("SO-101 resolved suite must contain three A4 cases")
    for case in a4_cases:
        binding = case.get("binding")
        parameters = binding.get("parameters") if isinstance(binding, Mapping) else None
        if not isinstance(parameters, Mapping):
            raise ValueError("SO-101 A4 case has no binding parameters")
        if "tool_body_names" in parameters:
            raise ValueError("SO-101 B2 A4 must not use subtree geom expansion")
        if parameters.get("tool_geom_names") != SO101_A4_TOOL_GEOMS:
            raise ValueError("SO-101 B2 A4 distal geom whitelist changed")
        if parameters.get("precontact_gate") != "held_window_then_ray":
            raise ValueError("SO-101 B2 A4 precontact gate changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-from-experiment1",
        action="store_true",
        help=(
            "explicitly replace the fixed snapshots from the current Experiment 1 "
            "bundles before validating them"
        ),
    )
    args = parser.parse_args()
    paths = refresh_from_experiment1() if args.refresh_from_experiment1 else []
    validate_resolved()
    if paths:
        for path in paths:
            print(path.relative_to(REPOSITORY_ROOT))
    print("fixed B2 reference suite snapshots are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
