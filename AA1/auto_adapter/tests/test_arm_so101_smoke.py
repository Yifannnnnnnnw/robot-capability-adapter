# SPDX-License-Identifier: Apache-2.0
"""Smoke test for ArmSerialDLSSkeleton on SO-101 MJCF.

Validates the production-tested skeleton works end-to-end before the agent
ever fills any ArmSpec. This is the framework-side test; it does NOT use
LLM. If this passes, the agent's Phase-3 behavior tests (T1-T5) on SO-101
should have a chance of passing too.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np


def main() -> None:
    # Hand-built spec — this is what the agent will produce in Phase 2.
    from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec

    SO101_MJCF = "assets/mjcf/so101_mujoco.xml"

    spec = ArmSpec(
        ee_site_name="ee_site",
        arm_joint_names=[
            "shoulder_pan", "shoulder_lift", "elbow_flex",
            "wrist_flex", "wrist_roll",
        ],
        arm_actuator_names=[
            "act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
            "act_wrist_flex", "act_wrist_roll",
        ],
        joint_limits={
            "shoulder_pan":  (-1.92, 1.92),
            "shoulder_lift": (-1.75, 1.75),
            "elbow_flex":    (-1.69, 1.69),
            "wrist_flex":    (-1.66, 1.66),
            "wrist_roll":    (-2.74, 2.84),
        },
        home_qpos=[0.0, 0.0, 0.0, 0.0, 0.0],
        gripper_actuator_names=["act_jaw_visual"],
        gripper_open_ctrl=1.5,
        gripper_close_ctrl=-0.1,
        weld_graspable_bodies=[
            "banana", "mug", "bottle", "screwdriver", "duck", "lego",
        ],
        grasp_radius=0.06,
    )

    arm = ArmSerialDLSSkeleton.from_mjcf(SO101_MJCF, spec)
    print(f"[OK] from_mjcf  dof={arm.dof}  ee_site_id={arm._ee_site_id}")

    # T1: FK at home returns finite (x, y, z)
    pos, R = arm.get_ee_pose()
    assert np.all(np.isfinite(pos)), f"FK home returned NaN/inf: {pos}"
    print(f"[T1] FK at home: ee_pos={pos.round(3).tolist()}")

    # T2: IK round-trip — pick a near-target inside the workspace,
    #     solve IK, check resulting EE pos vs target.
    target = pos + np.array([0.02, 0.02, 0.05])
    q_t = arm.ik(target)
    out = arm.fk(q_t)
    err = float(np.linalg.norm(out["pos"] - target))
    print(f"[T2] IK round-trip: target={target.round(3).tolist()} "
          f"actual={out['pos'].round(3).tolist()} ‖err‖={err:.4f}")
    assert err < 0.02, f"IK round-trip MAE too high: {err:.4f}"

    # T3: IK to banana — workspace check.
    banana = arm.get_object_position("banana")
    above_banana = banana + np.array([0.0, 0.0, 0.08])
    q_above = arm.ik(above_banana)
    actual_above = arm.fk(q_above)["pos"]
    err_above = float(np.linalg.norm(actual_above - above_banana))
    print(f"[T3] IK to banana+8cm: banana={banana.round(3).tolist()} "
          f"target_above={above_banana.round(3).tolist()} ‖err‖={err_above:.4f}")
    assert err_above < 0.03, f"IK to banana-above too far: {err_above:.4f}"

    # Move there for real (step physics)
    arm.move_cartesian(above_banana, duration=1.0)
    ee_after, _ = arm.get_ee_pose()
    err_real = float(np.linalg.norm(ee_after - above_banana))
    print(f"[T3b] After move_cartesian: actual EE={ee_after.round(3).tolist()} "
          f"‖err‖={err_real:.4f}")

    # T4: descend close to banana and gripper_close → verify grasp
    arm.move_cartesian(banana + np.array([0.0, 0.0, 0.01]), duration=1.0)
    ok = arm.gripper_close()
    held = arm.is_holding()
    print(f"[T4] gripper_close → returned {ok}, is_holding={held}, "
          f"held_body={arm._held_body}")

    # T5: lift and verify banana goes up with the EE
    banana_before = arm.get_object_position("banana")[2]
    arm.move_cartesian(banana + np.array([0.0, 0.0, 0.20]), duration=1.5)
    banana_after = arm.get_object_position("banana")[2]
    lift = banana_after - banana_before
    print(f"[T5] banana_z lifted by {lift*100:.1f} cm "
          f"({banana_before:.3f} → {banana_after:.3f})")
    assert lift > 0.05, f"banana not lifted: Δz={lift:.3f}"

    # describe — what the agent will see when introspecting the skeleton
    print("\n[describe]")
    import json
    print(json.dumps(arm.describe(), indent=2))

    arm.close()
    print("\n[ALL OK]  ArmSerialDLSSkeleton smoke test passed.")


if __name__ == "__main__":
    main()
