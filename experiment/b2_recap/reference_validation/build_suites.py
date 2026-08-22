#!/usr/bin/env python3
"""Resolve the B2 reference-driver suites from the Experiment 1 bundles."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
SELECTION_PATH = HERE / "selection.json"

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
    return robots


def _resolve_so101_suite(source: Mapping[str, Any]) -> dict[str, Any]:
    suite = copy.deepcopy(dict(source))
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise ValueError("SO-101 source suite has no cases list")

    a3_h3_count = 0
    a4_count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("SO-101 source suite contains an invalid case")
        case_id = case.get("case_id")
        if case_id == "A3-H3":
            reset = case.get("reset")
            if not isinstance(reset, dict):
                raise ValueError("A3-H3 has no reset object")
            for field in ("joint_positions", "actuator_controls"):
                values = reset.get(field)
                if not isinstance(values, dict) or "gripper" not in values:
                    raise ValueError(f"A3-H3 reset has no {field}.gripper")
                values["gripper"] = 1.7453292
            a3_h3_count += 1

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

    if a3_h3_count != 1 or a4_count != 3:
        raise ValueError(
            f"expected one A3-H3 and three A4 cases; got {a3_h3_count} and {a4_count}"
        )
    if "geom_37" in SO101_A4_TOOL_GEOMS:
        raise AssertionError("geom_37 must remain outside the B2 A4 contact group")
    return suite


def resolved_documents(robot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    source_dir = REPOSITORY_ROOT / str(robot["source_bundle_dir"])
    design = _read_json(source_dir / "capability_design.json")
    source_suite = _read_json(source_dir / "capability_validation_suite.json")
    robot_id = str(robot["robot_configuration_id"])
    suite = (
        _resolve_so101_suite(source_suite)
        if robot_id == "robotstudio_so101"
        else copy.deepcopy(source_suite)
    )
    return {
        "capability_design.json": design,
        "capability_validation_suite.json": suite,
    }


def build(*, check: bool = False) -> list[Path]:
    stale: list[Path] = []
    written: list[Path] = []
    for robot in load_selection():
        destination = REPOSITORY_ROOT / str(robot["resolved_bundle_dir"])
        for filename, document in resolved_documents(robot).items():
            path = destination / filename
            rendered = _render_json(document)
            if check:
                if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                    stale.append(path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            written.append(path)
    if stale:
        names = "\n".join(f"- {path.relative_to(REPOSITORY_ROOT)}" for path in stale)
        raise RuntimeError(f"resolved reference suites are stale:\n{names}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if committed resolved files differ from a fresh mechanical build",
    )
    args = parser.parse_args()
    paths = build(check=args.check)
    if args.check:
        print("resolved reference suites are current")
    else:
        for path in paths:
            print(path.relative_to(REPOSITORY_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
