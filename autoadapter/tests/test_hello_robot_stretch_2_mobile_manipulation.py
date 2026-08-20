from __future__ import annotations

import ast
import importlib.util
import math
import sys
from pathlib import Path

import mujoco
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
)
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"
PACKAGE_SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "mobile_manipulation.py"
TRUSTED_SKELETON_PATH = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "stretch_mobile_manipulation.py"
)


def _load_package_skeleton():
    module_name = "stretch_mobile_manipulation_inventory"
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE_SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _session():
    module = _load_package_skeleton()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = module.StretchMobileManipulationSkeleton(
        model=model,
        data=data,
        spec=module.STRETCH_MOBILE_MANIPULATION_SPEC,
    )
    return module, model, data, skeleton


def test_stretch_mobile_skeleton_is_task_neutral_and_actuator_only() -> None:
    source = TRUSTED_SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "task_id",
        "scoring",
        "tasks/private",
        "reference/driver",
        "from_xml_path",
        "mj_resetdata",
    ):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mj_step" in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            assert not any(
                isinstance(child, ast.Attribute)
                and child.attr in {"qpos", "qvel", "mocap_pos", "mocap_quat"}
                for child in ast.walk(target)
            )


def test_stretch_base_turns_and_drives_with_feedback() -> None:
    _, _, _, skeleton = _session()
    turn = skeleton.turn_to_yaw(math.pi / 4.0)
    drive = skeleton.drive_distance(0.15)

    assert turn["final_error_rad"] < 0.01
    assert drive["final_error_m"] < 0.01
    assert 0.14 < drive["travelled_distance_m"] < 0.16
    state = skeleton.get_state()
    assert np.isfinite(state["base_position"]).all()


def test_stretch_side_arm_reaches_world_target_and_moves_gripper() -> None:
    _, _, data, skeleton = _session()
    target = np.asarray((0.20, -0.45, 0.75), dtype=float)
    reach = skeleton.move_tool_to_position(target)
    grip = skeleton.set_gripper(0.03)

    assert reach["final_error_m"] < 0.01
    assert reach["minimum_error_m"] < 0.01
    assert np.linalg.norm(reach["final_position"] - target) < 0.01
    assert abs(grip["final_position_m"] - 0.03) < 0.002
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()


def test_stretch_side_arm_rejects_unreachable_target() -> None:
    module, _, _, skeleton = _session()
    with pytest.raises(module.StretchUnreachableError):
        skeleton.move_tool_to_position((2.0, -2.0, 0.75), maximum_steps=100)
