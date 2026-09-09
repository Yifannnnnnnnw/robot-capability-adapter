# SPDX-License-Identifier: Apache-2.0
"""Gymnasium env wrapper for our skeleton, used to train PPO baselines.

Each task = a goal-conditioned env. The PPO policy learns a SINGLE task; we
compare per-task success + training cost against our LLM agent which does
ANY task with zero training.

Tasks supported (start small — paper needs comparison, not breadth):

  reach_xyz:
    Move the arm's EE to a specified XYZ target in world frame.
    obs: [joint_pos, joint_vel, ee_xyz, target_xyz, ee_xyz - target_xyz]
    act: arm joint position deltas (continuous, clipped to joint limits)
    reward: -||ee_xyz - target_xyz||  + step penalty
    done: error < tol (success) OR n_steps > max

  stand_up:
    Quadruped: reach standing pose from initial drop pose.
    obs: [joint_pos (12), joint_vel (12), base_xyz, base_quat]
    act: joint torques (12)
    reward: -|target_body_height - body_height| + stability bonus
    done: body_height < min_floor (failed) OR n_steps > max
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces


def _load_skeleton_from_workspace(workspace: Path):
    """Side-load the driver from a workspace + build the robot."""
    sys.modules.pop("driver", None)
    for nm in ("driver.py", "driver_from_scratch.py"):
        target = workspace / nm
        if target.exists():
            break
    spec = importlib.util.spec_from_file_location("driver", str(target))
    mod = importlib.util.module_from_spec(spec)
    orig_cwd = os.getcwd()
    os.chdir(workspace)
    try:
        spec.loader.exec_module(mod)
        if hasattr(mod, "build") and callable(getattr(mod, "build")):
            return mod.build()
        if hasattr(mod, "Robot"):
            return mod.Robot.build_from_mjcf("mjcf.xml")
    finally:
        os.chdir(orig_cwd)
    raise RuntimeError(f"can't load driver from {workspace}")


# ──────────────────────────────────────────────────────────────────────────
# Reach task (arms)
# ──────────────────────────────────────────────────────────────────────────


class ReachXYZEnv(gym.Env):
    """PPO learns to move the EE +5cm in X from home."""

    metadata = {"render_modes": ["rgb_array"]}

    # Class-level task identifier; PickBananaEnv uses TASK_ID=1.
    TASK_ID = 0
    N_TASKS = 2   # reach=0, pick=1 — defines the one-hot dimension prepended to obs

    def __init__(
        self,
        workspace: Path,
        *,
        target_offset_xyz: tuple[float, float, float] = (0.05, 0.0, 0.0),
        # Multi-task mode: if `target_pool` is set, reset() samples a target
        # uniformly from it instead of always using `target_offset_xyz`.
        target_pool: Optional[list[tuple[float, float, float]]] = None,
        max_steps: int = 200,
        ctrl_scale: float = 0.05,    # max per-step joint delta (rad)
        success_tol: float = 0.02,   # success if EE within tol of target
        with_gripper: bool = False,  # if True, expose +1 action dim (gripper, ignored)
        with_task_id: bool = True,   # if True, prepend task-one-hot to obs (default True)
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.workspace = Path(workspace).resolve()
        self.target_offset = np.array(target_offset_xyz, dtype=np.float64)
        self.target_pool = (
            [np.array(o, dtype=np.float64) for o in target_pool]
            if target_pool else None
        )
        self.max_steps = int(max_steps)
        self.ctrl_scale = float(ctrl_scale)
        self.success_tol = float(success_tol)
        self.with_gripper = bool(with_gripper)
        self.with_task_id = bool(with_task_id)
        self._step_count = 0
        self._target_xyz: Optional[np.ndarray] = None
        self._rng = np.random.default_rng(seed)

        # Lazy-build skel to extract DOF
        self.skel = _load_skeleton_from_workspace(self.workspace)
        if not (hasattr(self.skel, "get_ee_pose") and hasattr(self.skel, "set_arm_actuators")):
            raise RuntimeError(
                "ReachXYZEnv requires arm skel with get_ee_pose + set_arm_actuators"
            )
        self.dof = int(self.skel.dof)

        # Action: per-arm-joint delta (clipped); will be added to current actuator setpoint
        # If with_gripper=True, add one gripper-command dim (ignored by this reach env)
        # so the same trained policy can be used in PickEnv without padding.
        act_dim = self.dof + (1 if self.with_gripper else 0)
        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(act_dim,), dtype=np.float32
        )
        # Observation: [task_onehot(N_TASKS)?] + q (dof) + qdot (dof) + ee_xyz (3) + target_xyz (3) + err (3)
        # task_onehot is identical within a single env (one-hot at self.TASK_ID).
        # With with_task_id=True, ReachXYZEnv and PickBananaEnv have identical
        # obs shapes — but the task_onehot field tells the policy which task
        # it's in, eliminating obs-slot aliasing between Reach and Pick.
        obs_dim = 2 * self.dof + 9 + (self.N_TASKS if self.with_task_id else 0)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )

        if seed is not None:
            self.reset(seed=seed)

    def _get_obs(self) -> np.ndarray:
        q = self.skel.get_joint_positions()
        qd = self.skel.get_joint_velocities()
        ee_xyz, _ = self.skel.get_ee_pose()
        err = self._target_xyz - ee_xyz
        parts = [q, qd, ee_xyz, self._target_xyz, err]
        if self.with_task_id:
            onehot = np.zeros(self.N_TASKS, dtype=np.float32)
            onehot[self.TASK_ID] = 1.0
            parts.insert(0, onehot)
        return np.concatenate(parts).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        # Rebuild skel for clean state
        self.skel = _load_skeleton_from_workspace(self.workspace)
        self.skel.home()
        ee_xyz, _ = self.skel.get_ee_pose()
        if self.target_pool is not None:
            offset = self.target_pool[int(self._rng.integers(len(self.target_pool)))]
        else:
            offset = self.target_offset
        self._target_xyz = ee_xyz + offset
        self._step_count = 0
        return self._get_obs(), {}

    def step(self, action: np.ndarray):
        action = np.clip(action, -1.0, 1.0).astype(np.float64)
        # Strip gripper dim if present (this env ignores gripper command)
        joint_action = action[: self.dof]
        cur_q = self.skel.get_joint_positions()
        target_q = cur_q + joint_action * self.ctrl_scale
        # Clip to skel joint limits
        if hasattr(self.skel, "_q_lo") and hasattr(self.skel, "_q_hi"):
            target_q = np.clip(target_q, self.skel._q_lo, self.skel._q_hi)
        self.skel.set_arm_actuators(target_q)
        self.skel.step(5)  # ~10ms sim time per env step
        self._step_count += 1

        ee_xyz, _ = self.skel.get_ee_pose()
        err = float(np.linalg.norm(self._target_xyz - ee_xyz))
        reward = -err - 0.001  # small step penalty
        terminated = err < self.success_tol
        if terminated:
            reward += 10.0  # success bonus
        truncated = self._step_count >= self.max_steps
        obs = self._get_obs()
        return obs, float(reward), bool(terminated), bool(truncated), {
            "err_m": err, "ee_xyz": ee_xyz.tolist(),
            "target_xyz": self._target_xyz.tolist(),
        }

    def render(self):
        if hasattr(self.skel, "render"):
            return self.skel.render()
        return None
