# SPDX-License-Identifier: Apache-2.0
"""Same-API learned baselines: PPO whose ACTION SPACE is the high-level tool
layer the LLM agent / Code-as-Policies use (Cartesian EE target + gripper),
not low-level joint deltas.

This answers the fairness objection (Codex Axis D) that the joint-space PPO
in rl_env.py / pick_env.py is a missing-abstraction ablation, not a
like-for-like baseline. Here the learned policy and the LLM operate the SAME
interface: each step commands a Cartesian delta resolved by the driver's IK
(the exact machinery move_cartesian uses) plus an open/close gripper bit.

Reach and Pick share an IDENTICAL observation and action layout:
    obs    = [ee_xyz(3), gripper_open_frac(1), goal_xyz(3)]   (7,)
    action = [dx, dy, dz, gripper_cmd]                          (4,)
with goal = reach target (reach) or object position (pick). No task-one-hot,
no obs-slot aliasing: a policy trained on one skill can be run verbatim on
the other (cross-skill transfer), and the only thing that differs is the
learned weights. This is the clean same-API cross-skill comparison.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from autoadapter_bench.baselines.rl.rl_env import _load_skeleton_from_workspace


class _CartesianArmBase(gym.Env):
    """Shared obs/action/Cartesian-step machinery for the same-API baselines."""

    metadata = {"render_modes": ["rgb_array"]}

    def __init__(self, workspace: Path, *, max_steps: int, cart_scale: float,
                 seed: Optional[int]) -> None:
        super().__init__()
        self.workspace = Path(workspace).resolve()
        self.max_steps = int(max_steps)
        self.cart_scale = float(cart_scale)   # max per-step Cartesian delta (m)
        self._rng = np.random.default_rng(seed)
        self._step_count = 0
        self._gripper_open = True
        self.skel = _load_skeleton_from_workspace(self.workspace)
        # action: dx, dy, dz, gripper_cmd
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(4,), dtype=np.float32)
        # obs: ee_xyz(3), gripper_open_frac(1), goal_xyz(3)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(7,),
                                            dtype=np.float32)

    # subclasses set self._goal_xyz and define reward/termination
    def _goal(self) -> np.ndarray:
        raise NotImplementedError

    def _get_obs(self) -> np.ndarray:
        ee_xyz, _ = self.skel.get_ee_pose()
        g = np.array([1.0 if self._gripper_open else 0.0], dtype=np.float64)
        return np.concatenate([ee_xyz, g, self._goal()]).astype(np.float32)

    def _apply_cartesian(self, action: np.ndarray) -> None:
        """Command a Cartesian delta via the driver's IK + an open/close bit."""
        action = np.clip(action, -1.0, 1.0).astype(np.float64)
        ee_xyz, _ = self.skel.get_ee_pose()
        next_ee = ee_xyz + action[:3] * self.cart_scale
        cur_q = self.skel.get_joint_positions()
        q_des = self.skel.ik(next_ee, q_init=cur_q, raise_on_unreachable=False)
        self.skel.set_arm_actuators(q_des)
        if action[3] > 0:
            if self._gripper_open:
                self.skel.gripper_close()
                self._gripper_open = False
        else:
            if not self._gripper_open:
                self.skel.gripper_open()
                self._gripper_open = True
        self.skel.step(5)
        self._step_count += 1

    def render(self):
        return self.skel.render() if hasattr(self.skel, "render") else None


class CartesianReachEnv(_CartesianArmBase):
    """Reach a Cartesian target through the same-API (Cartesian) action space."""

    def __init__(self, workspace: Path, *,
                 target_offset_xyz=(0.05, 0.0, 0.0),
                 target_pool: Optional[list] = None,
                 max_steps: int = 60, cart_scale: float = 0.03,
                 success_tol: float = 0.02, seed: Optional[int] = None) -> None:
        super().__init__(workspace, max_steps=max_steps, cart_scale=cart_scale, seed=seed)
        self.target_offset = np.array(target_offset_xyz, dtype=np.float64)
        self.target_pool = [np.array(o, dtype=np.float64) for o in target_pool] if target_pool else None
        self.success_tol = float(success_tol)
        self._target_xyz: Optional[np.ndarray] = None

    def _goal(self) -> np.ndarray:
        return self._target_xyz

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        import mujoco
        mujoco.mj_resetData(self.skel.model, self.skel.data)  # in-place reset (fast)
        mujoco.mj_forward(self.skel.model, self.skel.data)
        self.skel.home()
        self.skel.gripper_open()
        self._gripper_open = True
        ee0, _ = self.skel.get_ee_pose()
        offset = (self.target_pool[int(self._rng.integers(len(self.target_pool)))]
                  if self.target_pool is not None else self.target_offset)
        self._target_xyz = ee0 + offset
        self._step_count = 0
        return self._get_obs(), {}

    def step(self, action):
        self._apply_cartesian(action)
        ee_xyz, _ = self.skel.get_ee_pose()
        err = float(np.linalg.norm(self._target_xyz - ee_xyz))
        reward = -err - 0.001
        terminated = err < self.success_tol
        if terminated:
            reward += 10.0
        truncated = self._step_count >= self.max_steps
        return self._get_obs(), float(reward), bool(terminated), bool(truncated), {
            "err_m": err, "ee_xyz": ee_xyz.tolist()}


class MultiTaskCartesianEnv(_CartesianArmBase):
    """ONE policy, BOTH skills: each episode samples reach or pick. Same Cartesian
    + gripper action as the LLM; obs adds a task-id bit so the shared policy knows
    which skill. This is the strongest fair amortization baseline (Codex audit):
    RL *can* do both skills with one policy, but only after being TRAINED on both
    -- whereas the LLM does both zero-shot. obs = [task_bit(1), ee(3),
    gripper_open_frac(1), goal(3)] (8,); action = [dx,dy,dz,gripper] (4,).
    task_bit: 0.0 = reach, 1.0 = pick.
    """

    def __init__(self, workspace: Path, *,
                 reach_pool=None, max_steps: int = 80, cart_scale: float = 0.03,
                 success_tol: float = 0.02, success_lift_m: float = 0.02,
                 approach_tol_m: float = 0.04,
                 banana_xy_range=((0.10, 0.22), (0.04, 0.20)),
                 p_pick: float = 0.5, seed: Optional[int] = None,
                 force_task: Optional[str] = None) -> None:
        super().__init__(workspace, max_steps=max_steps, cart_scale=cart_scale, seed=seed)
        self.reach_pool = [np.array(o, dtype=np.float64) for o in
                           (reach_pool or [(0.05, 0, 0), (-0.05, 0, 0), (0, 0.05, 0),
                                           (0, 0, 0.05), (0, 0, -0.05), (0, -0.05, 0)])]
        self.success_tol = float(success_tol)
        self.success_lift_m = float(success_lift_m)
        self.approach_tol_m = float(approach_tol_m)
        self.banana_xy_range = banana_xy_range
        self.p_pick = float(p_pick)
        self.force_task = force_task  # 'reach'|'pick'|None (eval pins a task)
        self._task = "reach"
        self._target_xyz: Optional[np.ndarray] = None
        self._banana_start_z: Optional[float] = None
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(8,),
                                            dtype=np.float32)

    def _banana(self) -> np.ndarray:
        return np.array(self.skel.get_object_position("banana"), dtype=np.float64)

    def _goal(self) -> np.ndarray:
        return self._target_xyz if self._task == "reach" else self._banana()

    def _get_obs(self) -> np.ndarray:
        ee_xyz, _ = self.skel.get_ee_pose()
        bit = np.array([0.0 if self._task == "reach" else 1.0], dtype=np.float64)
        g = np.array([1.0 if self._gripper_open else 0.0], dtype=np.float64)
        return np.concatenate([bit, ee_xyz, g, self._goal()]).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        import mujoco
        if self.force_task is not None:
            self._task = self.force_task
        else:
            self._task = "pick" if self._rng.random() < self.p_pick else "reach"
        mujoco.mj_resetData(self.skel.model, self.skel.data)
        mujoco.mj_forward(self.skel.model, self.skel.data)
        self.skel.home()
        self.skel.gripper_open()
        self._gripper_open = True
        ee0, _ = self.skel.get_ee_pose()
        if self._task == "reach":
            self._target_xyz = ee0 + self.reach_pool[int(self._rng.integers(len(self.reach_pool)))]
        else:
            if self.banana_xy_range is not None:
                m = self.skel.model
                adr = None
                for ji in range(m.njnt):
                    jn = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, ji)
                    if jn and "banana" in jn:
                        adr = int(m.jnt_qposadr[ji]); break
                (x_lo, x_hi), (y_lo, y_hi) = self.banana_xy_range
                self.skel.data.qpos[adr] = float(self._rng.uniform(x_lo, x_hi))
                self.skel.data.qpos[adr + 1] = float(self._rng.uniform(y_lo, y_hi))
                self.skel.data.qpos[adr + 2] = 0.06
                self.skel.data.qvel[adr:adr + 6] = 0.0
                mujoco.mj_forward(self.skel.model, self.skel.data)
            self.skel.settle(0.3)
            self._banana_start_z = float(self._banana()[2])
        self._step_count = 0
        return self._get_obs(), {}

    def step(self, action):
        self._apply_cartesian(action)
        ee_xyz, _ = self.skel.get_ee_pose()
        if self._task == "reach":
            err = float(np.linalg.norm(self._target_xyz - ee_xyz))
            reward = -err - 0.001
            terminated = err < self.success_tol
            if terminated:
                reward += 10.0
            info = {"task": "reach", "err_m": err}
        else:
            banana = self._banana()
            dist = float(np.linalg.norm(banana - ee_xyz))
            lift = float(banana[2] - self._banana_start_z)
            reward = -dist - 0.001 + 30.0 * max(0.0, lift)
            terminated = lift >= self.success_lift_m
            if terminated:
                reward += 50.0
            info = {"task": "pick", "dist_m": dist, "lift_m": lift}
        truncated = self._step_count >= self.max_steps
        return self._get_obs(), float(reward), bool(terminated), bool(truncated), info


class CartesianPickEnv(_CartesianArmBase):
    """Pick a banana through the same-API (Cartesian + gripper) action space."""

    def __init__(self, workspace: Path, *,
                 max_steps: int = 80, cart_scale: float = 0.03,
                 success_lift_m: float = 0.02, approach_tol_m: float = 0.04,
                 banana_xy_range=((0.10, 0.22), (0.04, 0.20)),
                 seed: Optional[int] = None) -> None:
        super().__init__(workspace, max_steps=max_steps, cart_scale=cart_scale, seed=seed)
        self.success_lift_m = float(success_lift_m)
        self.approach_tol_m = float(approach_tol_m)
        self.banana_xy_range = banana_xy_range
        self._banana_start_z: Optional[float] = None

    def _banana(self) -> np.ndarray:
        return np.array(self.skel.get_object_position("banana"), dtype=np.float64)

    def _goal(self) -> np.ndarray:
        return self._banana()

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        import mujoco
        mujoco.mj_resetData(self.skel.model, self.skel.data)  # in-place reset (fast)
        mujoco.mj_forward(self.skel.model, self.skel.data)
        self.skel.home()
        self.skel.gripper_open()
        self._gripper_open = True
        if self.banana_xy_range is not None:
            # locate banana freejoint qpos slot
            m = self.skel.model
            adr = None
            for ji in range(m.njnt):
                jn = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, ji)
                if jn and "banana" in jn:
                    adr = int(m.jnt_qposadr[ji]); break
            if adr is None:
                raise RuntimeError("banana freejoint not found")
            (x_lo, x_hi), (y_lo, y_hi) = self.banana_xy_range
            self.skel.data.qpos[adr] = float(self._rng.uniform(x_lo, x_hi))
            self.skel.data.qpos[adr + 1] = float(self._rng.uniform(y_lo, y_hi))
            self.skel.data.qpos[adr + 2] = 0.06
            self.skel.data.qvel[adr:adr + 6] = 0.0
            mujoco.mj_forward(self.skel.model, self.skel.data)
        self.skel.settle(0.3)
        self._banana_start_z = float(self._banana()[2])
        self._step_count = 0
        return self._get_obs(), {}

    def step(self, action):
        self._apply_cartesian(action)
        ee_xyz, _ = self.skel.get_ee_pose()
        banana = self._banana()
        dist = float(np.linalg.norm(banana - ee_xyz))
        lift = float(banana[2] - self._banana_start_z)
        # Lift-dominant shaping: reward raising the banana DIRECTLY (continuous),
        # so the policy cannot farm reward by hovering with the gripper closed.
        # A one-time grasp nudge (small, only while actually lifting) avoids the
        # per-step approach-bonus hack that the first training run exploited.
        reward = -dist - 0.001 + 30.0 * max(0.0, lift)
        if lift >= self.success_lift_m:
            reward += 50.0
        terminated = lift >= self.success_lift_m
        truncated = self._step_count >= self.max_steps
        return self._get_obs(), float(reward), bool(terminated), bool(truncated), {
            "dist_m": dist, "lift_m": lift}
