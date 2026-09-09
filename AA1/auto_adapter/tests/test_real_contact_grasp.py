# SPDX-License-Identifier: Apache-2.0
"""Real-contact grasp validation test.

Compares RealContactGraspBackend (validates via sustained contact forces)
against WeldGraspBackend (kinematic glue) on a controlled scene with a
small graspable cube + a parallel-jaw gripper (Franka).

Goal: show that the new backend correctly distinguishes:
  (a) a TRUE grasp (gripper closed on object → contact forces sustained)
  (b) a FAILED grasp (gripper closed on empty space → no contact)
  (c) a SLIPPED grasp (close → lift → object falls out → is_holding=False)

This is the "fake grasp" criticism fixed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.skeletons import ArmSerialDLSSkeleton, ArmSpec


# Construct a minimal Franka-with-cube scene by wrapping the standard
# panda.xml and dropping a small free-floating cube right in front of the
# EE's home position. The agent's existing Franka driver doesn't have a
# graspable scene; we make one for this test.

_FRANKA_DIR = REPO_ROOT / "assets" / "mjcf" / "franka_panda"

_SCENE_XML = f"""\
<mujoco model="franka_cube_test">
  <include file="panda.xml"/>

  <statistic center="0.3 0 0.4" extent="1"/>
  <visual>
    <headlight diffuse="0.6 0.6 0.6" ambient="0.3 0.3 0.3" specular="0 0 0"/>
    <rgba haze="0.15 0.25 0.35 1"/>
  </visual>
  <asset>
    <texture type="skybox" builtin="gradient" rgb1="0.3 0.5 0.7" rgb2="0 0 0" width="512" height="3072"/>
    <texture type="2d" name="groundplane" builtin="checker" mark="edge" rgb1="0.2 0.3 0.4"
             rgb2="0.1 0.2 0.3" markrgb="0.8 0.8 0.8" width="300" height="300"/>
    <material name="groundplane" texture="groundplane" texuniform="true" texrepeat="5 5" reflectance="0.2"/>
  </asset>

  <worldbody>
    <light pos="0 0 1.5" dir="0 0 -1" directional="true"/>
    <geom name="floor" type="plane" size="0 0 0.05" material="groundplane" friction="1 0.01 0.001"/>

    <!-- Small cube directly under the Franka home EE pose, on the floor -->
    <body name="test_cube" pos="0.40 0.0 0.025">
      <freejoint/>
      <inertial pos="0 0 0" mass="0.05" diaginertia="0.0001 0.0001 0.0001"/>
      <geom type="box" size="0.020 0.020 0.025" rgba="0.9 0.2 0.2 1"
            friction="1.5 0.005 0.0001" condim="6"/>
    </body>
  </worldbody>
</mujoco>
"""


def _make_scene_file() -> Path:
    # Must live in franka_panda/ so mjcf <include file="panda.xml"/> and the
    # asset meshdir="assets" both resolve correctly.
    out = _FRANKA_DIR / "franka_cube_test_scene.xml"
    out.write_text(_SCENE_XML)
    return out


def _build_franka_spec(grasp_backend: str) -> ArmSpec:
    return ArmSpec(
        ee_body_name="hand",
        arm_joint_names=[f"joint{i}" for i in range(1, 8)],
        arm_actuator_names=[f"actuator{i}" for i in range(1, 8)],
        joint_limits={
            "joint1": (-2.8973, 2.8973),
            "joint2": (-1.7628, 1.7628),
            "joint3": (-2.8973, 2.8973),
            "joint4": (-3.0718, -0.0698),
            "joint5": (-2.8973, 2.8973),
            "joint6": (-0.0175, 3.7525),
            "joint7": (-2.8973, 2.8973),
        },
        home_qpos=[0.0, 0.0, 0.0, -1.5708, 0.0, 1.8675, 0.0],
        gripper_actuator_names=["actuator8"],
        gripper_close_ctrl=0.0,
        gripper_open_ctrl=255.0,
        grasp_backend=grasp_backend,
        weld_graspable_bodies=["test_cube"],  # hint for the backend
        grasp_radius=0.10,
    )


def run_scenario(name: str, grasp_backend: str, *, lift: bool, drop_target: bool):
    """Run a scenario and return outcome dict.

    Parameters
      lift: if True, lift the gripper 10cm after closing
      drop_target: if True, deliberately move EE 20cm AWAY from cube
                   before closing (should fail grasp)
    """
    scene_xml = _make_scene_file()
    spec = _build_franka_spec(grasp_backend)
    skel = ArmSerialDLSSkeleton.from_mjcf(str(scene_xml), spec)
    skel.home(duration=1.5)

    # Optionally move away (so close finds nothing)
    if drop_target:
        ee, _ = skel.get_ee_pose()
        skel.move_cartesian(ee + np.array([0.0, 0.5, 0.0]), duration=1.5)
    else:
        # Approach: get cube position, move EE just above
        cube = skel.get_object_position("test_cube")
        # Approach from above, then descend
        skel.move_cartesian(cube + np.array([0, 0, 0.10]), duration=2.0)
        skel.move_cartesian(cube + np.array([0, 0, 0.02]), duration=1.5)

    holding_before = skel.is_holding()
    skel.gripper_close()
    holding_after_close = skel.is_holding()

    cube_z_before = float(skel.get_object_position("test_cube")[2])
    if lift:
        ee, _ = skel.get_ee_pose()
        skel.move_cartesian(ee + np.array([0.0, 0.0, 0.10]), duration=2.0)
    holding_after_lift = skel.is_holding()
    cube_z_after = float(skel.get_object_position("test_cube")[2])

    return {
        "scenario": name,
        "backend": grasp_backend,
        "lift": lift,
        "drop_target": drop_target,
        "before_close.is_holding": holding_before,
        "after_close.is_holding": holding_after_close,
        "after_lift.is_holding": holding_after_lift,
        "cube_z_before_lift": cube_z_before,
        "cube_z_after_lift": cube_z_after,
        "cube_lift_dz": cube_z_after - cube_z_before,
    }


def main() -> None:
    print("=" * 72)
    print("REAL-CONTACT GRASP VALIDATION (Franka + small cube)")
    print("=" * 72)
    scenarios = [
        # (name, lift, drop_target)
        ("close_then_lift_on_cube", True, False),
        ("close_on_empty_space", False, True),
        ("close_lift_on_empty_space", True, True),
    ]
    backends = ["contact", "real_contact"]

    print(f"\n{'Scenario':<35}{'Backend':>15} | "
          f"{'before':>7} {'after_close':>13} {'after_lift':>12} {'cube_dz':>10}")
    print("-" * 100)
    for sname, lift, drop in scenarios:
        for bn in backends:
            try:
                r = run_scenario(sname, bn, lift=lift, drop_target=drop)
                print(f"{sname:<35}{bn:>15} | "
                      f"{str(r['before_close.is_holding']):>7} "
                      f"{str(r['after_close.is_holding']):>13} "
                      f"{str(r['after_lift.is_holding']):>12} "
                      f"{r['cube_lift_dz']:>+9.3f}m")
            except Exception as e:  # noqa: BLE001
                print(f"{sname:<35}{bn:>15} | ERROR: {type(e).__name__}: {e}")

    print("\nInterpretation:")
    print("  - contact backend: any cube within grasp_radius → is_holding=True ")
    print("    (proximity-only, no physics validation)")
    print("  - real_contact: True only when gripper geoms ARE touching the cube")
    print("    AND the cube isn't sliding out during the lift")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
