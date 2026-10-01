# SPDX-License-Identifier: Apache-2.0
"""Focused checks for native MuJoCo quadruped position control."""
from __future__ import annotations

import unittest
from pathlib import Path

import mujoco
import numpy as np

from auto_adapter.skeletons import QuadrupedPDGaitSkeleton, QuadrupedSpec


ROOT = Path(__file__).resolve().parents[2]


def _native_case(robot: str):
    if robot == "a1":
        scene = ROOT / "assets" / "mjcf" / "unitree_a1" / "scene.xml"
        legs = ("FR", "FL", "RR", "RL")
        joints = {
            leg: [
                f"{leg}_hip_joint",
                f"{leg}_thigh_joint",
                f"{leg}_calf_joint",
            ]
            for leg in legs
        }
        actuators = {
            leg: [f"{leg}_hip", f"{leg}_thigh", f"{leg}_calf"]
            for leg in legs
        }
        body = "trunk"
        key_name = "home"
        body_height_target = 0.27
    elif robot == "anymal":
        scene = ROOT / "assets" / "mjcf" / "anybotics_anymal_c" / "scene.xml"
        legs = ("LF", "RF", "LH", "RH")
        joints = {
            leg: [f"{leg}_HAA", f"{leg}_HFE", f"{leg}_KFE"] for leg in legs
        }
        actuators = {leg: list(joints[leg]) for leg in legs}
        body = "base"
        key_name = None
        body_height_target = 0.62
    else:  # pragma: no cover - callers use the two real scenes above
        raise ValueError(f"unknown native case {robot!r}")

    model = mujoco.MjModel.from_xml_path(str(scene.resolve()))
    data = mujoco.MjData(model)
    if key_name is not None:
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, key_name)
        if key_id < 0:
            raise AssertionError(f"{robot} keyframe {key_name!r} is missing")
        mujoco.mj_resetDataKeyframe(model, data, key_id)

    joint_names = [name for leg in legs for name in joints[leg]]
    qpos_addresses = [
        int(
            model.jnt_qposadr[
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            ]
        )
        for name in joint_names
    ]
    home = (
        np.asarray(model.key_qpos[key_id, qpos_addresses], dtype=float)
        if key_name is not None
        else np.asarray(model.qpos0[qpos_addresses], dtype=float)
    )
    mujoco.mj_forward(model, data)
    spec = QuadrupedSpec(
        base_body_name=body,
        leg_joint_names=joints,
        leg_actuator_names=actuators,
        home_qpos=home.tolist(),
        actuation="joint_position",
        kp=30.0,
        kd=1.5,
        body_height_target=body_height_target,
    )
    return model, data, spec


class NativeQuadrupedControlTests(unittest.TestCase):
    def test_actuation_mode_is_explicit_and_validated(self) -> None:
        self.assertEqual(QuadrupedSpec.__dataclass_fields__["actuation"].default, "joint_torque")
        with self.assertRaisesRegex(ValueError, "joint_torque.*joint_position"):
            QuadrupedSpec(
                base_body_name="base",
                leg_joint_names={},
                leg_actuator_names={},
                home_qpos=[],
                actuation="position_servo",
            )

    def test_position_mode_writes_native_targets_and_clips_to_model_ranges(self) -> None:
        for robot in ("a1", "anymal"):
            with self.subTest(robot=robot):
                model, data, spec = _native_case(robot)
                skeleton = QuadrupedPDGaitSkeleton(model, data, spec)
                self.assertEqual(skeleton.describe()["actuation"], "joint_position")
                q_before = data.qpos.copy()
                qvel_before = data.qvel.copy()
                time_before = float(data.time)

                raw_target = skeleton.get_joint_positions()
                raw_target[0] = 100.0
                raw_target[1] = -100.0
                raw_target[2] = 100.0
                expected = np.clip(
                    np.clip(raw_target, skeleton._joint_lo, skeleton._joint_hi),
                    skeleton._ctrl_lo,
                    skeleton._ctrl_hi,
                )

                skeleton.set_joint_targets(raw_target)

                np.testing.assert_allclose(
                    data.ctrl[skeleton._actuator_ids], expected, atol=1.0e-12
                )
                np.testing.assert_array_equal(data.qpos, q_before)
                np.testing.assert_array_equal(data.qvel, qvel_before)
                self.assertEqual(float(data.time), time_before)

                legacy_pd = np.clip(
                    spec.kp * (raw_target - skeleton.get_joint_positions()),
                    skeleton._ctrl_lo,
                    skeleton._ctrl_hi,
                )
                self.assertGreater(float(np.max(np.abs(expected - legacy_pd))), 1.0e-2)

    def test_torque_default_rejects_native_position_actuators(self) -> None:
        for robot in ("a1", "anymal"):
            with self.subTest(robot=robot):
                model, data, position_spec = _native_case(robot)
                torque_spec = QuadrupedSpec(
                    base_body_name=position_spec.base_body_name,
                    leg_joint_names=position_spec.leg_joint_names,
                    leg_actuator_names=position_spec.leg_actuator_names,
                    home_qpos=position_spec.home_qpos,
                )
                with self.assertRaisesRegex(ValueError, "joint_torque"):
                    QuadrupedPDGaitSkeleton(model, data, torque_spec)

    def test_real_native_stand_short_step(self) -> None:
        for robot in ("a1", "anymal"):
            with self.subTest(robot=robot):
                model, data, spec = _native_case(robot)
                skeleton = QuadrupedPDGaitSkeleton(model, data, spec)
                result = skeleton.stand_up(duration=0.15)
                self.assertTrue(result)
                self.assertGreater(float(data.time), 0.0)
                self.assertTrue(np.all(np.isfinite(data.qpos)))
                self.assertTrue(np.all(np.isfinite(data.qvel)))
                self.assertGreater(
                    skeleton.get_body_height(), spec.body_height_target - 0.05
                )
                self.assertLess(
                    float(np.linalg.norm(skeleton.get_joint_positions() - spec.home_qpos)),
                    0.20,
                )


if __name__ == "__main__":
    unittest.main()
