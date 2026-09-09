# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the AA1 Go2 retained NumPy policy adapter."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from auto_adapter.skeletons.base import SkeletonBase
from auto_adapter.skeletons.go2_velocity_policy import (
    POLICY_ARTIFACT_ID,
    POLICY_SOURCE_REVISION,
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
)


ROOT = Path(__file__).resolve().parents[2]
SCENE = ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml"
DATA_ROOT = ROOT / "auto_adapter" / "skeletons" / "data"
POLICY_SOURCE = ROOT / "auto_adapter" / "skeletons" / "go2_velocity_policy.py"


def _spec() -> Go2VelocityPolicySpec:
    names = tuple(
        f"{leg}_{suffix}_joint"
        for leg in ("FL", "FR", "RL", "RR")
        for suffix in ("hip", "thigh", "calf")
    )
    return Go2VelocityPolicySpec(
        base_body_name="base_link",
        joint_names=names,
        actuator_names=tuple(name.removesuffix("_joint") for name in names),
        default_joint_angles=(
            0.1,
            0.8,
            -1.5,
            -0.1,
            0.8,
            -1.5,
            0.1,
            1.0,
            -1.5,
            -0.1,
            1.0,
            -1.5,
        ),
    )


def _policy() -> Go2VelocityPolicySkeleton:
    model = mujoco.MjModel.from_xml_path(str(SCENE))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return Go2VelocityPolicySkeleton(model=model, data=data, spec=_spec())


def test_go2_policy_source_metadata_and_golden_numpy_actions() -> None:
    metadata = json.loads(
        (DATA_ROOT / "go2_velocity_policy.json").read_text(encoding="utf-8")
    )
    assert metadata["artifact_id"] == POLICY_ARTIFACT_ID == "go2-cts-150k"
    assert metadata["source_revision"] == POLICY_SOURCE_REVISION
    assert metadata["source_path"] == "deploy/pre_train/go2/go2_cts_150k.pt"
    assert metadata["torch_runtime_required"] is False
    assert (DATA_ROOT / "go2_velocity_policy.npz").stat().st_size > 1_000_000
    license_text = (DATA_ROOT / "go2_velocity_policy_LICENSE.txt").read_text(
        encoding="utf-8"
    )
    assert "MIT License" in license_text
    assert "BSD-3-Clause" in license_text

    source = POLICY_SOURCE.read_text(encoding="utf-8")
    assert "import torch" not in source
    assert "SessionBoundSkeleton" not in source
    assert "autoadapter2" not in source

    policy = _policy()
    policy._resolve()
    zero_action = policy._infer(np.zeros(45, dtype=np.float32))
    ramp_action = policy._infer(np.linspace(-0.4, 0.4, 45, dtype=np.float32))
    np.testing.assert_allclose(
        zero_action,
        (
            -0.603626132,
            0.591176212,
            -0.389862686,
            0.688495457,
            -0.199814022,
            -0.587446034,
            -0.082616895,
            -0.352849066,
            -0.466575384,
            -0.093064792,
            -0.074538678,
            0.010981951,
        ),
        rtol=0.0,
        atol=2.0e-6,
    )
    np.testing.assert_allclose(
        ramp_action,
        (
            -0.416912884,
            0.287532449,
            -1.090850353,
            -0.432435423,
            0.370840341,
            -0.073182568,
            -0.638892651,
            -0.481605887,
            1.041430831,
            0.065453477,
            -0.318036497,
            0.160008147,
        ),
        rtol=0.0,
        atol=2.0e-6,
    )


def test_go2_policy_uses_shared_aa1_session_and_basic_joint_targets() -> None:
    model = mujoco.MjModel.from_xml_path(str(SCENE))
    data = mujoco.MjData(model)
    policy = Go2VelocityPolicySkeleton(model=model, data=data, spec=_spec())

    assert isinstance(policy, SkeletonBase)
    # Exercise the inherited AA1 path before policy index/weight resolution.
    policy.step(1)
    assert np.isclose(data.time, model.opt.timestep, atol=1.0e-12)
    qpos_before = np.asarray(data.qpos).copy()
    assert np.isfinite(qpos_before).all()
    assert policy.get_joint_positions().shape == (12,)
    targets = np.asarray(_spec().default_joint_angles, dtype=np.float32)
    policy.set_joint_targets(targets)
    np.testing.assert_allclose(policy.get_joint_targets(), targets, atol=0.0)
    np.testing.assert_array_equal(data.qpos, qpos_before)
    policy.apply_joint_targets()
    assert np.isfinite(data.ctrl).all()
    assert np.any(np.abs(np.asarray(data.ctrl)) > 0.0)
    description = policy.describe()
    assert description["skeleton_type"] == "Go2VelocityPolicySkeleton"
    assert description["base_type"] == "SkeletonBase"
    assert description["dof"] == 12
    assert description["policy"]["artifact_id"] == POLICY_ARTIFACT_ID


def test_go2_policy_command_runs_real_mujoco_steps() -> None:
    policy = _policy()
    position_before, _ = policy.get_base_pose()
    ctrl_before = np.asarray(policy.data.ctrl, dtype=float).copy()

    result = policy.command_planar_velocity(0.25, 0.0, 0.0, duration=0.10)

    position_after, rotation_after = policy.get_base_pose()
    assert result["physics_steps"] == 50
    assert result["policy_inference_count"] >= 1
    assert np.isclose(policy.data.time, 0.10, atol=1.0e-12)
    assert np.isfinite(policy.data.qpos).all()
    assert np.isfinite(policy.data.qvel).all()
    assert np.isfinite(policy.data.ctrl).all()
    assert not np.array_equal(ctrl_before, np.asarray(policy.data.ctrl, dtype=float))
    assert np.isfinite(position_after).all()
    assert np.isfinite(rotation_after).all()
    assert np.linalg.norm(position_after - position_before) > 1.0e-4
    twist = policy.get_base_twist()
    assert np.isfinite(twist["linear_world_m_s"]).all()
    assert np.isfinite(twist["linear_body_yaw_m_s"]).all()
    assert np.isfinite(twist["angular_world_rad_s"]).all()
    assert np.isfinite(twist["yaw_rate_rad_s"])
