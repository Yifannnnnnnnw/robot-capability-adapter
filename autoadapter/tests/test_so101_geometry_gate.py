from __future__ import annotations

import json
from pathlib import Path

from autoadapter2.geometry_gate import validate_so101_geometry
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.4"
OLD_PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.0"


def test_so101_104_geometry_gate_rejects_the_v6_false_ready_layout() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    result = validate_so101_geometry(package)

    assert result.passed
    assert result.margins["mw_pick_place_wall.strict_chain_margin_m"] >= 0.0
    assert result.margins["mw_lever_pull.strict_chain_margin_m"] >= 0.0
    assert result.margins["mw_pick_place_wall.worst_ik_residual_m"] <= 0.005
    assert result.margins["mw_lever_pull.worst_ik_residual_m"] <= 0.005
    assert result.margins["mw_pick_place_wall.worst_joint_limit_margin_deg"] >= 5.0
    assert result.margins["mw_lever_pull.worst_joint_limit_margin_deg"] >= 5.0
    assert result.margins["wall_object_route_clearance_m"] >= 0.010
    assert result.margins["wall_gripper_route_clearance_m"] >= 0.010
    assert result.margins["lever_gripper_route_clearance_m"] >= 0.010
    assert result.margins["mw_pick_place_wall.reset_criterion_margin"] > 0.0
    assert result.margins["mw_lever_pull.reset_criterion_margin"] > 0.0

    instances = json.loads(
        (PACKAGE_ROOT / "tasks" / "private" / "instances.json").read_text(
            encoding="utf-8"
        )
    )["instances"]
    by_task = {item["task_id"]: item for item in instances}
    wall_parameters = by_task["mw_pick_place_wall"]["public_arguments"]["request"][
        "task_parameters"
    ]
    assert wall_parameters["start_position"] == [0.30, 0.08, 0.18]
    assert wall_parameters["grasp_position"] == [0.30, 0.07, 0.195]
    assert wall_parameters["target_position"] == [0.42, 0.08, 0.26]
    assert wall_parameters["route_position"] == [0.36, 0.08, 0.30]
    assert wall_parameters["release_position"] == [0.411, 0.082, 0.272]
    assert wall_parameters["tool_target_position"] == [0.411, 0.082, 0.272]

    lever_parameters = by_task["mw_lever_pull"]["public_arguments"]["request"][
        "task_parameters"
    ]
    assert lever_parameters["contact_position"] == [0.327, 0.12, 0.30]
    assert lever_parameters["route_position"] == [0.395, 0.12, 0.255]
    assert lever_parameters["target_position"] == [0.43, 0.12, 0.18]
    assert lever_parameters["tool_target_position"] == [0.445, 0.12, 0.17]

    old_result = validate_so101_geometry(OLD_PACKAGE_ROOT)
    assert not old_result.passed
    assert not old_result.checks[
        "mw_pick_place_wall.wall_side_gap_below_object_diameter"
    ]
    assert not old_result.checks["mw_pick_place_wall.wall_pedestal_tangent"]
