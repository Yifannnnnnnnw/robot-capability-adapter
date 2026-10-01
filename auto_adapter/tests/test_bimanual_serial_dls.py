# SPDX-License-Identifier: Apache-2.0
"""Focused real-physics checks for the shared-world ALOHA skeleton."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from auto_adapter.skeletons.base import ArmSpec
from auto_adapter.skeletons.bimanual_serial_dls import (
    BimanualSerialDLSSkeleton,
    BimanualSerialDLSSpec,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = REPO_ROOT / "assets" / "mjcf" / "aloha_2" / "scene.xml"

_JOINT_LIMITS = {
    "waist": (-3.14158, 3.14158),
    "shoulder": (-1.85005, 1.25664),
    "elbow": (-1.76278, 1.6057),
    "forearm_roll": (-3.14158, 3.14158),
    "wrist_angle": (-1.8675, 2.23402),
    "wrist_rotate": (-3.14158, 3.14158),
}
_HOME = [0.0, -0.96, 1.16, 0.0, -0.30, 0.0]

# These are FK points from the collision-free neutral home with a small,
# symmetric joint offset [waist, shoulder, elbow, forearm_roll, wrist_angle,
# wrist_rotate] = [+.08, +.04, -.05, 0, +.06, 0].
LEFT_TARGET = np.array([-0.178291, 0.004306, 0.326503], dtype=np.float64)
RIGHT_TARGET = np.array([0.178291, -0.042306, 0.326503], dtype=np.float64)


def _arm_spec(side: str) -> ArmSpec:
    suffixes = list(_JOINT_LIMITS)
    names = [f"{side}/{suffix}" for suffix in suffixes]
    return ArmSpec(
        ee_site_name=f"{side}/gripper",
        arm_joint_names=names,
        arm_actuator_names=names,
        joint_limits={name: _JOINT_LIMITS[name.split("/", 1)[1]] for name in names},
        home_qpos=list(_HOME),
        gripper_actuator_names=[f"{side}/gripper"],
        gripper_open_ctrl=0.037,
        gripper_close_ctrl=0.002,
        grasp_backend="contact",
        ik_max_iter=100,
        ik_tolerance=1e-4,
    )


def _spec() -> BimanualSerialDLSSpec:
    return BimanualSerialDLSSpec(left=_arm_spec("left"), right=_arm_spec("right"),
                               initial_keyframe="neutral_pose")


def _make_skeleton() -> BimanualSerialDLSSkeleton:
    assert SCENE_PATH.is_file(), f"ALOHA MJCF missing at {SCENE_PATH}"
    return BimanualSerialDLSSkeleton.from_mjcf(str(SCENE_PATH), _spec())


def _task_gate_passes(
    skeleton: BimanualSerialDLSSkeleton,
    before_left: np.ndarray,
    before_right: np.ndarray,
) -> bool:
    left_moved = float(
        np.linalg.norm(skeleton.get_joint_positions(arm="left") - before_left)
    ) > 0.03
    right_moved = float(
        np.linalg.norm(skeleton.get_joint_positions(arm="right") - before_right)
    ) > 0.03
    left_error = float(
        np.linalg.norm(skeleton.get_ee_pose(arm="left")[0] - LEFT_TARGET)
    )
    right_error = float(
        np.linalg.norm(skeleton.get_ee_pose(arm="right")[0] - RIGHT_TARGET)
    )
    return left_moved and right_moved and left_error < 0.03 and right_error < 0.03


def test_aloha_bimanual_reaches_both_targets_and_holds() -> None:
    skeleton = _make_skeleton()
    assert skeleton.left.model is skeleton.model is skeleton.right.model
    assert skeleton.left.data is skeleton.data is skeleton.right.data
    assert skeleton.describe()["dof"] == 12

    # Both children start with their own home actuator targets and no collision.
    np.testing.assert_allclose(
        skeleton.data.ctrl[skeleton.left._arm_actuator_ids], _HOME, atol=1e-12
    )
    np.testing.assert_allclose(
        skeleton.data.ctrl[skeleton.right._arm_actuator_ids], _HOME, atol=1e-12
    )
    assert skeleton.data.ncon == 0

    skeleton.home(duration=2.0)
    before_left = skeleton.get_joint_positions(arm="left").copy()
    before_right = skeleton.get_joint_positions(arm="right").copy()

    skeleton.move_cartesian(LEFT_TARGET, duration=2.0, arm="left")
    skeleton.move_cartesian(RIGHT_TARGET, duration=2.0, arm="right")
    ctrl_before_hold = skeleton.data.ctrl.copy()
    time_before_hold = float(skeleton.data.time)
    skeleton.hold(duration=0.5)

    assert float(skeleton.data.time) - time_before_hold == pytest.approx(0.5, abs=1e-9)
    np.testing.assert_allclose(skeleton.data.ctrl, ctrl_before_hold, atol=1e-12)
    assert _task_gate_passes(skeleton, before_left, before_right)
    assert np.isfinite(skeleton.data.qpos).all()
    assert np.isfinite(skeleton.data.qvel).all()

    left_error = float(np.linalg.norm(skeleton.get_ee_pose(arm="left")[0] - LEFT_TARGET))
    right_error = float(np.linalg.norm(skeleton.get_ee_pose(arm="right")[0] - RIGHT_TARGET))
    print(
        "ALOHA calibrated targets:",
        f"left={LEFT_TARGET.tolist()} err={left_error:.6f}",
        f"right={RIGHT_TARGET.tolist()} err={right_error:.6f}",
    )

    skeleton.gripper_close(arm="left")
    assert skeleton.data.ctrl[skeleton.left._gripper_actuator_ids[0]] == pytest.approx(0.002)
    assert skeleton.data.ctrl[skeleton.right._gripper_actuator_ids[0]] == pytest.approx(0.037)
    skeleton.gripper_open(arm="left")
    with pytest.raises(ValueError, match="left.*right"):
        skeleton.get_ee_pose(arm="both")


def test_omitting_one_arm_does_not_pass_bimanual_gate() -> None:
    skeleton = _make_skeleton()
    skeleton.home(duration=2.0)
    before_left = skeleton.get_joint_positions(arm="left").copy()
    before_right = skeleton.get_joint_positions(arm="right").copy()

    # A single successful child action is insufficient for the bimanual task.
    skeleton.move_cartesian(LEFT_TARGET, duration=2.0, arm="left")
    skeleton.hold(duration=0.5)
    assert not _task_gate_passes(skeleton, before_left, before_right)


def test_overlapping_arm_bindings_are_rejected() -> None:
    left = _arm_spec("left")
    right = _arm_spec("right")
    right.arm_joint_names = list(left.arm_joint_names)
    right.arm_actuator_names = list(left.arm_actuator_names)
    with pytest.raises(ValueError, match="overlap"):
        BimanualSerialDLSSkeleton.from_mjcf(
            str(SCENE_PATH), BimanualSerialDLSSpec(left=left, right=right)
        )


def test_omitted_home_uses_selected_model_keyframe() -> None:
    spec = _spec()
    spec.left.home_qpos = spec.right.home_qpos = None
    skeleton = BimanualSerialDLSSkeleton.from_mjcf(str(SCENE_PATH), spec)
    for arm in (skeleton.left, skeleton.right):
        np.testing.assert_allclose(arm._home_q, _HOME)
    skeleton.home()
    assert skeleton.data.ncon == 0
