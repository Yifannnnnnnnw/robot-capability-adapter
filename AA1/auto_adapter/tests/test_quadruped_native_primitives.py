# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the shared native quadruped position primitives."""
from __future__ import annotations

import os
from pathlib import Path

import mujoco
import numpy as np
import pytest

from auto_adapter.skeletons import QuadrupedPDGaitSkeleton, QuadrupedSpec


ROOT = Path(__file__).resolve().parents[2]


NATIVE_FIXTURE_BINDINGS = {'unitree_a1': {'base_body_name': 'trunk', 'joint_names': ['FL_hip_joint', 'FL_thigh_joint', 'FL_calf_joint', 'FR_hip_joint', 'FR_thigh_joint', 'FR_calf_joint', 'RL_hip_joint', 'RL_thigh_joint', 'RL_calf_joint', 'RR_hip_joint', 'RR_thigh_joint', 'RR_calf_joint'], 'actuator_names': ['FL_hip', 'FL_thigh', 'FL_calf', 'FR_hip', 'FR_thigh', 'FR_calf', 'RL_hip', 'RL_thigh', 'RL_calf', 'RR_hip', 'RR_thigh', 'RR_calf'], 'leg_order': ['FL', 'FR', 'RL', 'RR'], 'nominal_home_qpos_rad': [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8], 'home_keyframe': 'fixed_settled'}, 'anymal_c': {'base_body_name': 'base', 'joint_names': ['LF_HAA', 'LF_HFE', 'LF_KFE', 'RF_HAA', 'RF_HFE', 'RF_KFE', 'LH_HAA', 'LH_HFE', 'LH_KFE', 'RH_HAA', 'RH_HFE', 'RH_KFE'], 'actuator_names': ['LF_HAA', 'LF_HFE', 'LF_KFE', 'RF_HAA', 'RF_HFE', 'RF_KFE', 'LH_HAA', 'LH_HFE', 'LH_KFE', 'RH_HAA', 'RH_HFE', 'RH_KFE'], 'leg_order': ['LF', 'RF', 'LH', 'RH'], 'nominal_home_qpos_rad': [0.0, 0.7, -1.4, 0.0, 0.7, -1.4, 0.0, -0.7, 1.4, 0.0, -0.7, 1.4], 'home_keyframe': 'fixed_settled'}}


@pytest.mark.parametrize(
    ("robot", "scene"),
    (
        ("unitree_a1", "assets/mjcf/demo_scenes/unitree_a1/fixed_scene.xml"),
        ("anymal_c", "assets/mjcf/demo_scenes/anymal_c/fixed_scene.xml"),
    ),
)
def test_native_planar_primitives_use_one_real_session(robot: str, scene: str) -> None:
    binding = NATIVE_FIXTURE_BINDINGS[robot]
    model = mujoco.MjModel.from_xml_path(os.path.realpath(ROOT / scene))
    data = mujoco.MjData(model)
    key = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_KEY, binding["home_keyframe"]
    )
    assert key >= 0
    mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    legs = {
        leg: binding["joint_names"][3 * index : 3 * index + 3]
        for index, leg in enumerate(binding["leg_order"])
    }
    actuators = {
        leg: binding["actuator_names"][3 * index : 3 * index + 3]
        for index, leg in enumerate(binding["leg_order"])
    }
    skeleton = QuadrupedPDGaitSkeleton(
        model=model,
        data=data,
        spec=QuadrupedSpec(
            base_body_name=binding["base_body_name"],
            leg_joint_names=legs,
            leg_actuator_names=actuators,
            home_qpos=binding["nominal_home_qpos_rad"],
            actuation="joint_position",
            body_height_target=float(binding.get("body_height_target", 0.3)),
        ),
    )

    assert set(("angular", "linear")) == set(skeleton.get_base_velocity())
    before = float(data.time)
    skeleton.command_planar_velocity(0.03, 0.01, 0.02, duration=model.opt.timestep)
    skeleton.walk_forward(speed=0.03, duration=model.opt.timestep)
    skeleton.walk_lateral(speed=0.02, duration=model.opt.timestep)
    skeleton.turn_in_place(yaw_rate=0.03, duration=model.opt.timestep)

    assert float(data.time) > before
    assert np.all(np.isfinite(data.qpos))
    assert np.all(np.isfinite(data.qvel))
    for actuator_id in skeleton._actuator_ids:
        assert float(model.actuator_ctrlrange[actuator_id, 0]) - 1e-12 <= float(
            data.ctrl[actuator_id]
        ) <= float(model.actuator_ctrlrange[actuator_id, 1]) + 1e-12

    # High-level methods remain generated-driver concerns; the public
    # skeleton exposes only the short native primitives and observations.
    for high_level_name in (
        "track_planar_twist",
        "move_body_relative_pose",
        "trace_planar_path",
        "set_body_height",
        "hold_stable_stance",
    ):
        assert not hasattr(skeleton, high_level_name)
