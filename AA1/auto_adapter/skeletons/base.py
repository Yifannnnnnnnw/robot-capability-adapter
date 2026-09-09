# SPDX-License-Identifier: Apache-2.0
"""Shared Spec dataclasses and SkeletonBase for auto_adapter.

LLM agent fills these specs from an MJCF / URDF capability graph;
the skeleton subclass uses them to implement production-tested motion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class ArmSpec:
    """Spec for a serial-chain manipulator. Agent fills this from MJCF analysis.

    Required fields are the minimum the skeleton needs to do FK + DLS IK.
    Gripper / weld fields are optional (None → skeleton's grasp ops become no-ops).
    """

    # ─── Required: structural mapping ────────────────────────────────────
    # ONE of ee_site_name / ee_body_name must be set. ee_site_name is preferred
    # (more precise reference frame inside the body) but many MJCFs ship without
    # an explicit site — fall back to the body itself via ee_body_name.
    ee_site_name: Optional[str] = None
    """MJCF site that is the end-effector (used by mj_jacSite + site_xpos).
    Preferred when available."""

    ee_body_name: Optional[str] = None
    """MJCF body that is the end-effector (used by mj_jacBody + xpos when no
    site is defined). Use the gripper / hand / tool body, e.g. 'hand' for
    Franka or 'gripper_link' for SO-101."""

    arm_joint_names: list[str] = field(default_factory=list)
    """Hinge / slide joints that the IK should move. Order = chain order."""

    arm_actuator_names: list[str] = field(default_factory=list)
    """Actuators that drive arm_joint_names. Skeleton sets data.ctrl[these]."""

    joint_limits: dict[str, tuple[float, float]] = field(default_factory=dict)
    """name → (lower, upper) rad. Skeleton clips IK output to these."""

    # ─── Optional: pose / motion config ──────────────────────────────────
    home_qpos: Optional[list[float]] = None
    """Default safe joint config. If None, skeleton uses model.qpos0 slice."""

    ik_damping: float = 1e-3
    """DLS Tikhonov damping (smaller = closer to pseudoinverse, less stable)."""

    ik_max_iter: int = 30
    """Max Jacobian iteration steps before giving up."""

    ik_tolerance: float = 1e-3
    """Convergence threshold ‖target - ee_pos‖ in meters."""

    ik_step_clamp: float = 0.2
    """Max joint angle delta per IK iter (rad) to keep linearization valid."""

    ik_raise_on_unreachable: bool = True
    """If True (default), ik() raises IKUnreachableError on non-convergence.
    Codex review F5: silent best-effort returns cause downstream skill failures
    that look like spec / weld bugs but are actually unreachable targets."""

    # ─── Optional: gripper ────────────────────────────────────────────────
    gripper_actuator_names: Optional[list[str]] = None
    """Actuators for the gripper. If None, gripper ops are no-ops."""

    gripper_close_ctrl: float = 0.0
    """data.ctrl[gripper_actuators] when closing the gripper."""

    gripper_open_ctrl: float = 1.0
    """data.ctrl[gripper_actuators] when opening the gripper."""

    gripper_settle_steps: int = 30
    """Sim steps to run after setting gripper ctrl, to let it physically reach
    the commanded position before checking grasp status."""

    # ─── Optional: grasp backend (codex F2) ───────────────────────────────
    # Selects HOW grasping is realized in sim:
    #   - "weld"    : MuJoCo equality constraint glues object to gripper body
    #                 (kinematic; good for SO-101 and demo MJCFs)
    #   - "contact" : pure physics — close gripper, check object is still close
    #                 (good for Franka/UR5 where the gripper actually closes)
    #   - "noop"    : no gripper at all (gripper ops are no-ops)
    #   - None      : auto-pick (see grasp_backends.make_grasp_backend)
    grasp_backend: Optional[str] = None

    weld_graspable_bodies: Optional[list[str]] = None
    """Bodies the WeldGraspBackend or ContactGraspBackend may attach to.
    For weld backend: the skeleton finds the matching weld eq for each name.
    For contact backend: a hint list; if empty, contact backend auto-discovers
    all free-joint bodies in the scene."""

    grasp_radius: float = 0.08
    """Max distance (m) from EE site to body for grasp to succeed.
    8cm is a forgiving default — typical tabletop arms can approach within
    1–5cm before closing, but small IK errors + the approach-vs-finger-tip
    distance both eat into the margin."""

    # ─── Optional: motion timing ──────────────────────────────────────────
    sim_dt: float = 0.002
    """Physics timestep (also used for `move_joints` interpolation density)."""

    def __post_init__(self) -> None:
        if not (self.ee_site_name or self.ee_body_name):
            raise ValueError(
                "ArmSpec requires either ee_site_name or ee_body_name to be set "
                "(use ee_site_name when the MJCF has an explicit <site>; use "
                "ee_body_name (e.g. 'hand', 'gripper_link') otherwise)"
            )
        if not self.arm_joint_names:
            raise ValueError("ArmSpec.arm_joint_names is empty")
        if not self.arm_actuator_names:
            raise ValueError("ArmSpec.arm_actuator_names is empty")
        if len(self.arm_joint_names) != len(self.arm_actuator_names):
            raise ValueError(
                f"arm_joint_names ({len(self.arm_joint_names)}) and "
                f"arm_actuator_names ({len(self.arm_actuator_names)}) must "
                "have the same length"
            )


@dataclass
class QuadrupedSpec:
    """Spec for a 12-DoF quadruped (4 legs × 3 joints).

    Designed for Unitree Go1/Go2, Spot, ANYmal, MIT Mini Cheetah, etc. The agent
    fills this from MJCF analysis. The skeleton
    (`QuadrupedPDGaitSkeleton`) uses joint-space PD for torque actuators and
    the native joint-position servo when requested, with no IK or training.
    """

    # ─── Required: structural mapping ────────────────────────────────────
    base_body_name: str
    """The free-floating torso (typically 'base_link', 'trunk', or 'base')."""

    leg_joint_names: dict[str, list[str]]
    """{'FL': [hip, thigh, calf], 'FR': [...], 'RL': [...], 'RR': [...]}.
    Use these exact leg keys. Joint order MUST be hip → thigh → calf."""

    leg_actuator_names: dict[str, list[str]]
    """Same shape and order as leg_joint_names. The skeleton sets
    data.ctrl[these] = torque per leg per joint."""

    # ─── Required: standing pose ─────────────────────────────────────────
    home_qpos: list[float]
    """Joint configuration for a stable standing pose. ORDER MUST MATCH the
    flattened leg_joint_names: [FL_hip, FL_thigh, FL_calf, FR_hip, …, RR_calf].
    For Go2 a sensible default is [0, 0.9, -1.8] × 4 (slight hip splay, knees
    bent), giving body height ~0.30 m."""

    # ─── Optional: PD gains (torque mode) ────────────────────────────────
    kp: float = 30.0
    """Proportional gain — torque per radian of position error."""

    kd: float = 1.5
    """Derivative gain — torque per (rad/s) of velocity error."""

    # ─── Optional: gait config (hand-tuned periodic trot) ────────────────
    gait_freq_hz: float = 1.5
    """Trot stride frequency."""

    swing_amp_thigh: float = 0.12
    """Thigh angle amplitude during swing (rad). Lower = more stable but
    smaller steps. The v1 trot is fragile; keep amplitudes conservative."""

    swing_amp_calf: float = 0.18
    """Extra knee bend during swing (rad)."""

    # +1 = thigh angle INCREASES during forward swing; -1 = opposite. Depends
    # on MJCF joint axis convention. Agent should infer or empirically probe.
    thigh_forward_sign: float = -1.0

    # Per-leg gait phase offset in radians. If None, the skeleton auto-picks
    # a trot pattern using the spec's leg-key order: legs at positions 0 and
    # 3 share phase 0; legs at positions 1 and 2 share phase π. This works
    # when the agent lists legs in diagonal-pair-adjacent order (FL, FR,
    # RL, RR → diagonals are FL+RR vs FR+RL, which matches).
    gait_phases: Optional[dict[str, float]] = None

    body_height_target: float = 0.30
    """Target torso height (used by stand_up to interpolate from current pose)."""

    # ─── Optional: cmd_vel limits ────────────────────────────────────────
    vx_max: float = 0.4
    vy_max: float = 0.2
    vyaw_max: float = 1.0

    # ─── Optional: motion timing ──────────────────────────────────────────
    sim_dt: float = 0.002

    # ─── Actuation semantics ─────────────────────────────────────────────
    actuation: str = "joint_torque"
    """How the declared actuators consume commands.

    ``joint_torque`` preserves the original skeleton behavior and expects
    torque / motor actuators.  ``joint_position`` is for MJCF position
    actuators whose native servo consumes a joint-position target.
    """

    def __post_init__(self) -> None:
        if self.actuation not in {"joint_torque", "joint_position"}:
            raise ValueError(
                "actuation must be 'joint_torque' or 'joint_position'"
            )


class SkeletonBase:
    """Base class shared by every concrete skeleton.

    Concrete subclasses (ArmSerialDLSSkeleton, QuadrupedMPCGaitSkeleton, …)
    provide robot-class-specific motion / IK / gait code. The base only handles
    construction, common MuJoCo model loading, and the shared rendering helper.
    """

    def __init__(self, model, data, spec):
        # mujoco imported lazily so the dataclasses above remain import-safe
        # in environments that do not have mujoco installed (e.g. agent code-gen).
        import mujoco  # noqa: PLC0415

        self._mj = mujoco
        self.model = model
        self.data = data
        self.spec = spec
        self._renderer = None  # lazy

    # ─── Class methods for common construction patterns ─────────────────────

    @classmethod
    def from_mjcf(cls, mjcf_path: str, spec):
        """Load model + data from an MJCF file path and instantiate the skeleton.

        Resolves symlinks before passing to MuJoCo so that relative mesh paths
        inside the MJCF (e.g. `<mesh file="../urdf/meshes/X.stl"/>`) resolve
        against the file's REAL location, not the symlink's location.
        """
        import os  # noqa: PLC0415
        import mujoco  # noqa: PLC0415

        real_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(real_path)
        data = mujoco.MjData(model)
        # Settle the initial qpos: mj_forward populates derived state used by FK.
        mujoco.mj_forward(model, data)
        return cls(model, data, spec)

    # ─── Common low-level state access ──────────────────────────────────────

    def step(self, n: int = 1) -> None:
        """Advance physics by n steps."""
        for _ in range(n):
            self._mj.mj_step(self.model, self.data)

    def settle(self, secs: float) -> None:
        """Step physics for `secs` real-world seconds (using model timestep)."""
        n = max(1, int(secs / self.model.opt.timestep))
        self.step(n)

    # ─── Shared rendering helper (works for any robot class) ────────────────

    def render(self, camera: str | int = -1, height: int = 480, width: int = 640):
        """Offscreen render. Lazily creates a single Renderer (EGL when on GPU)."""
        if self._renderer is None or self._renderer.height != height or self._renderer.width != width:
            if self._renderer is not None:
                try:
                    self._renderer.close()
                except Exception:
                    pass
            self._renderer = self._mj.Renderer(self.model, height=height, width=width)
        self._renderer.update_scene(self.data, camera=camera)
        return self._renderer.render().copy()

    def close(self) -> None:
        """Release the renderer if it was created."""
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass
            self._renderer = None
