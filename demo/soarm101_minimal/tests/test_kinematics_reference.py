from __future__ import annotations

import importlib.util
import math
import random
from pathlib import Path

import mujoco


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "libraries/morphology/soarm101/v1/kinematics_reference.py"
MODEL_PATH = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"


def _reference_module():
    spec = importlib.util.spec_from_file_location(
        "soarm101_generation_kinematics_reference", REFERENCE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _site_position(model: mujoco.MjModel, data: mujoco.MjData) -> tuple[float, ...]:
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
    return tuple(float(value) for value in data.site_xpos[site_id])


def test_pure_python_fk_matches_compiled_mujoco() -> None:
    reference = _reference_module()
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    generator = random.Random(101)

    maximum_axis_error = 0.0
    maximum_rotation_error = 0.0
    for _ in range(250):
        joints = tuple(
            generator.uniform(
                float(model.jnt_range[index, 0]),
                float(model.jnt_range[index, 1]),
            )
            for index in range(5)
        )
        data.qpos[:5] = joints
        mujoco.mj_forward(model, data)
        expected = _site_position(model, data)
        actual = reference._forward_position_rad(joints)
        pose_position, pose_rotation = reference._forward_pose_rad(joints)
        maximum_axis_error = max(
            maximum_axis_error,
            *(abs(actual[index] - expected[index]) for index in range(3)),
            *(abs(pose_position[index] - expected[index]) for index in range(3)),
        )
        compiled_rotation = data.site_xmat[
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
        ].reshape(3, 3)
        maximum_rotation_error = max(
            maximum_rotation_error,
            *(
                abs(pose_rotation[row][column] - float(compiled_rotation[row, column]))
                for row in range(3)
                for column in range(3)
            ),
        )

    assert maximum_axis_error < 1e-12
    assert maximum_rotation_error < 1e-12


def test_reference_ik_reaches_generic_workspace_probes_in_mujoco() -> None:
    reference = _reference_module()
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    generic_targets = (
        (0.34, -0.04, 0.15),
        (0.29, 0.07, 0.10),
        (0.37, 0.00, 0.18),
    )

    for target in generic_targets:
        joints, internal_error, converged = reference._solve_position_ik_rad(target)
        assert converged is True
        assert internal_error <= 0.0025
        data.qpos[:5] = joints
        mujoco.mj_forward(model, data)
        observed = _site_position(model, data)
        physical_error = math.sqrt(
            sum((observed[index] - target[index]) ** 2 for index in range(3))
        )
        assert physical_error <= 0.0025


def test_reference_ik_supports_horizontal_tabletop_pinch_wrist_constraint() -> None:
    reference = _reference_module()
    target = (0.31, 0.03, 0.09)

    joints, error, converged = reference._solve_position_ik_rad(
        target,
        fixed_wrist_roll_rad=-math.pi / 2.0,
    )

    assert converged is True
    assert error <= 0.0025
    assert math.isclose(joints[4], -math.pi / 2.0, abs_tol=1e-12)


def test_reference_ik_can_control_compiled_closed_jaw_tool_center() -> None:
    reference = _reference_module()
    target = (0.31, -0.02, 0.055)
    closed_tool_center = (
        -0.0008769592416551508,
        0.00008466666666667,
        -0.018428429965433475,
    )

    joints, error, converged = reference._solve_position_ik_rad(
        target,
        fixed_wrist_roll_rad=-math.pi / 2.0,
        tool_point_in_gripperframe_m=closed_tool_center,
    )

    assert converged is True
    assert error <= 0.0025
    controlled = reference._forward_tool_point_rad(joints, closed_tool_center)
    assert math.dist(controlled, target) <= 0.0025
