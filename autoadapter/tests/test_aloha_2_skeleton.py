from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.harness.measurements import evaluate_guards
from autoadapter2.harness.session import TrackedMuJoCoSession
from autoadapter2.trusted_skeletons import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py"
HOME_QPOS = (0.0, -0.96, 1.16, 0.0, -0.3, 0.0)
ARM_SUFFIXES = (
    "waist",
    "shoulder",
    "elbow",
    "forearm_roll",
    "wrist_angle",
    "wrist_rotate",
)
NEUTRAL_TOLERANCES = {
    "waist": 0.002,
    "shoulder": 0.003,
    "elbow": 0.030,
    "forearm_roll": 0.002,
    "wrist_angle": 0.035,
    "wrist_rotate": 0.002,
    "left_finger": 0.0005,
    "right_finger": 0.0005,
}


def _load_package_skeleton():
    spec = importlib.util.spec_from_file_location("aloha_2_package_skeleton", SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _name_id(model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str) -> int:
    identifier = mujoco.mj_name2id(model, object_type, name)
    assert identifier >= 0, name
    return identifier


def _build_skeleton(module, side: str, model: mujoco.MjModel, data: mujoco.MjData):
    morphology = json.loads((PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8"))
    joints = tuple(
        morphology["public_control"]["arm_joint_names_by_side"][side]
    )
    limits = morphology["public_control"]["arm_joint_limits_rad"]
    return module.ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=module.ArmSpec(
            ee_site_name=f"{side}/gripper",
            arm_joint_names=joints,
            arm_actuator_names=joints,
            joint_limits={name: limits[name] for name in joints},
            home_qpos=HOME_QPOS,
            ik_max_iter=1000,
            ik_tolerance=0.002,
            ik_step_clamp=0.12,
            gripper_actuator_names=(f"{side}/gripper",),
            gripper_close_ctrl=0.002,
            gripper_open_ctrl=0.037,
        ),
    )


def test_aloha_2_package_exports_only_the_capability_neutral_arm_family() -> None:
    module = _load_package_skeleton()
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError
    assert module.__all__ == ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]

    source = SKELETON_PATH.read_text(encoding="utf-8")
    for forbidden in ("task_id", "trajectory", "target_position", "reference"):
        assert forbidden not in source.lower()


@pytest.mark.parametrize(
    ("side", "target"),
    (("left", (-0.20, 0.10, 0.20)), ("right", (0.20, 0.10, 0.20))),
)
def test_aloha_2_selected_arm_skeleton_is_live_and_leaves_other_arm_neutral(
    side: str,
    target: tuple[float, float, float],
) -> None:
    module = _load_package_skeleton()
    model = mujoco.MjModel.from_xml_path(str(PACKAGE_ROOT / "assets" / "reach_scene.xml"))
    data = mujoco.MjData(model)
    neutral_id = _name_id(model, mujoco.mjtObj.mjOBJ_KEY, "neutral_pose")
    mujoco.mj_resetDataKeyframe(model, data, neutral_id)
    mujoco.mj_forward(model, data)
    skeleton = _build_skeleton(module, side, model, data)

    site_id = _name_id(model, mujoco.mjtObj.mjOBJ_SITE, f"{side}/gripper")
    position, rotation = skeleton.get_ee_pose()
    np.testing.assert_allclose(position, data.site_xpos[site_id], atol=0.0)
    assert rotation.shape == (3, 3)

    initial_qpos = np.asarray(data.qpos, dtype=float).copy()
    solved = skeleton.ik(target, q_init=HOME_QPOS)
    np.testing.assert_allclose(skeleton.fk(solved)["pos"], target, atol=0.002)
    np.testing.assert_array_equal(data.qpos, initial_qpos)

    selected_finger_addresses = [
        int(
            model.jnt_qposadr[
                _name_id(
                    model,
                    mujoco.mjtObj.mjOBJ_JOINT,
                    f"{side}/{finger}_finger",
                )
            ]
        )
        for finger in ("left", "right")
    ]
    initial_fingers = np.asarray(data.qpos[selected_finger_addresses]).copy()
    with TrackedMuJoCoSession(
        mujoco=mujoco,
        model=model,
        data=data,
        max_steps=5000,
        max_sim_time_s=5.0,
        sample_hz=20.0,
    ) as tracked:
        assert skeleton.move_cartesian(
            target,
            duration=4.0,
            gain=1.8,
            max_joint_delta=0.08,
            residual_tolerance=0.02,
        )
        assert skeleton.gripper_open(settle_steps=100)
        opened_fingers = np.asarray(data.qpos[selected_finger_addresses]).copy()
        assert skeleton.gripper_close(settle_steps=100)
        closed_fingers = np.asarray(data.qpos[selected_finger_addresses]).copy()
        tracked.finish()

    final_position, _ = skeleton.get_ee_pose()
    assert np.linalg.norm(final_position - np.asarray(target)) <= 0.015
    assert np.all(opened_fingers > initial_fingers + 0.005)
    assert np.all(closed_fingers < opened_fingers - 0.005)
    np.testing.assert_allclose(opened_fingers[0], opened_fingers[1], atol=0.001)
    np.testing.assert_allclose(closed_fingers[0], closed_fingers[1], atol=0.001)

    evidence = tracked.evidence()
    assert evidence["canonical_model_data"] is True
    assert evidence["step_count"] >= 200
    assert evidence["ctrl_observed_before_step"] is True
    assert evidence["ctrl_changed_from_reset"] is True
    assert evidence["direct_state_write_detected"] is False
    selected_deviations = evidence["joint_max_abs_deviation_from_reset"]
    assert max(selected_deviations[f"{side}/{suffix}"] for suffix in ARM_SUFFIXES) > 0.1

    other_side = "right" if side == "left" else "left"
    joint_tolerances = {
        f"{other_side}/{suffix}": tolerance
        for suffix, tolerance in NEUTRAL_TOLERANCES.items()
    }
    guard = {
        "guard_id": "other-arm-neutral",
        "kind": "named_joints_remain_near_reset",
        "joint_tolerances": joint_tolerances,
    }
    assert evaluate_guards(
        [guard], worker_result={"physical_evidence": evidence}
    ) == {"other-arm-neutral": True}

    runnable_index = json.loads(
        (ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    assert "aloha_2" not in runnable_index["robots"]
