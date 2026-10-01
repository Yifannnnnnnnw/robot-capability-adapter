# SPDX-License-Identifier: Apache-2.0
"""PickBananaEnv — second skill for cross-task generalization comparison.

This is intentionally a DIFFERENT skill class than ReachXYZ:
  - obs includes banana XYZ (visual prior baked into state)
  - action includes a gripper command (6-dim instead of 5)
  - reward incentivizes approaching banana + closing gripper + lifting

A PPO/DP trained on Reach has never seen the banana_xyz field or the
gripper action — it CANNOT solve this task. Likewise PPO/DP trained on
Pick has a different reward and won't reach an EE-only target.

The LLM agent uses the same skel.move_cartesian + skel.gripper_close
primitives that the auto-synthesized driver provides; no retraining.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from autoadapter_bench.baselines.rl.rl_env import _load_skeleton_from_workspace


class PickBananaEnv(gym.Env):
    """Reach the banana, close gripper, lift it 2 cm.

    Observation: [q (5), qdot (5), ee_xyz (3), banana_xyz (3), err (3)] = 19
    Action:      [joint_delta (5), gripper (1)]
                 joint_delta ∈ [-1, 1] scaled by ctrl_scale.
                 gripper > 0 → close; gripper ≤ 0 → open.
    Reward:
      - shaped:   -||ee - banana_xyz||           (approach signal)
      - bonus:    +5 if ||ee - banana|| < 4 cm AND gripper closed
      - sparse:   +20 if banana lifted ≥ 2 cm above its start z
      - step:     -0.001 (efficiency)
    Done: success OR n_steps > max_steps.
    """

    metadata = {"render_modes": ["rgb_array"]}

    # Task-id ONE-hot dimension shared with ReachXYZEnv. Reach=0, Pick=1.
    TASK_ID = 1
    N_TASKS = 2

    def __init__(
        self,
        workspace: Path,
        *,
        max_steps: int = 200,
        ctrl_scale: float = 0.05,
        success_lift_m: float = 0.02,
        approach_tol_m: float = 0.04,
        # Multi-task / randomization: sample banana XY from this range each reset.
        # Set both to None for fixed-banana (legacy behavior).
        banana_xy_range: Optional[tuple[tuple[float, float], tuple[float, float]]]
            = ((0.10, 0.22), (0.04, 0.20)),
        with_task_id: bool = True,  # prepend task one-hot to obs (default True)
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.workspace = Path(workspace).resolve()
        self.max_steps = int(max_steps)
        self.ctrl_scale = float(ctrl_scale)
        self.success_lift_m = float(success_lift_m)
        self.approach_tol_m = float(approach_tol_m)
        self.banana_xy_range = banana_xy_range
        self.with_task_id = bool(with_task_id)
        self._step_count = 0
        self._banana_start_z = None
        self._rng = np.random.default_rng(seed)
        self._banana_qpos_adr: Optional[int] = None

        self.skel = _load_skeleton_from_workspace(self.workspace)
        if not hasattr(self.skel, "gripper_close"):
            raise RuntimeError("PickBananaEnv requires skel.gripper_close()")
        self.dof = int(self.skel.dof)

        # Action: 5 joint deltas + 1 gripper (same shape as ReachXYZEnv with_gripper=True)
        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(self.dof + 1,), dtype=np.float32
        )
        # Obs: [task_onehot(N_TASKS)?] + q + qdot + ee + banana + (banana - ee)
        obs_dim = 2 * self.dof + 9 + (self.N_TASKS if self.with_task_id else 0)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )

    def _get_banana_pos(self) -> np.ndarray:
        return np.array(self.skel.get_object_position("banana"), dtype=np.float64)

    def _get_obs(self) -> np.ndarray:
        q = self.skel.get_joint_positions()
        qd = self.skel.get_joint_velocities()
        ee_xyz, _ = self.skel.get_ee_pose()
        banana_xyz = self._get_banana_pos()
        err = banana_xyz - ee_xyz
        parts = [q, qd, ee_xyz, banana_xyz, err]
        if self.with_task_id:
            onehot = np.zeros(self.N_TASKS, dtype=np.float32)
            onehot[self.TASK_ID] = 1.0
            parts.insert(0, onehot)
        return np.concatenate(parts).astype(np.float32)

    def _find_banana_qpos_adr(self) -> int:
        """Locate the qpos slot for the banana freejoint."""
        import mujoco
        m = self.skel.model
        for ji in range(m.njnt):
            jname = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, ji)
            if jname and "banana" in jname:
                return int(m.jnt_qposadr[ji])
        raise RuntimeError("banana freejoint not found in MJCF")

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.skel = _load_skeleton_from_workspace(self.workspace)
        self.skel.home()
        self.skel.gripper_open()
        # Randomize banana XY if requested. Write to data.qpos directly, then
        # forward dynamics to commit the change before settle().
        if self.banana_xy_range is not None:
            import mujoco
            adr = self._find_banana_qpos_adr()
            (x_lo, x_hi), (y_lo, y_hi) = self.banana_xy_range
            x = float(self._rng.uniform(x_lo, x_hi))
            y = float(self._rng.uniform(y_lo, y_hi))
            self.skel.data.qpos[adr] = x
            self.skel.data.qpos[adr + 1] = y
            self.skel.data.qpos[adr + 2] = 0.06  # drop height; settle puts it on table
            # zero velocities so the banana doesn't fly away
            self.skel.data.qvel[adr : adr + 6] = 0.0
            mujoco.mj_forward(self.skel.model, self.skel.data)
        self.skel.settle(0.3)  # let banana drop onto table + come to rest
        self._banana_start_z = float(self._get_banana_pos()[2])
        self._step_count = 0
        return self._get_obs(), {}

    def step(self, action: np.ndarray):
        action = np.clip(action, -1.0, 1.0).astype(np.float64)
        joint_action = action[: self.dof]
        gripper_cmd = float(action[-1])
        cur_q = self.skel.get_joint_positions()
        target_q = cur_q + joint_action * self.ctrl_scale
        if hasattr(self.skel, "_q_lo") and hasattr(self.skel, "_q_hi"):
            target_q = np.clip(target_q, self.skel._q_lo, self.skel._q_hi)
        self.skel.set_arm_actuators(target_q)
        if gripper_cmd > 0:
            self.skel.gripper_close()
        else:
            self.skel.gripper_open()
        self.skel.step(5)
        self._step_count += 1

        ee_xyz, _ = self.skel.get_ee_pose()
        banana_xyz = self._get_banana_pos()
        dist = float(np.linalg.norm(banana_xyz - ee_xyz))
        lift = float(banana_xyz[2] - self._banana_start_z)

        reward = -dist - 0.001
        gripper_closed = gripper_cmd > 0
        if dist < self.approach_tol_m and gripper_closed:
            reward += 5.0
        if lift >= self.success_lift_m:
            reward += 20.0
        truncated = self._step_count >= self.max_steps
        terminated = lift >= self.success_lift_m
        info = {
            "dist_m": dist, "lift_m": lift,
            "banana_xyz": banana_xyz.tolist(),
            "ee_xyz": ee_xyz.tolist(),
        }
        return self._get_obs(), float(reward), bool(terminated), bool(truncated), info

    def render(self):
        return self.skel.render() if hasattr(self.skel, "render") else None
