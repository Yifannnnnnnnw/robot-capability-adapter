"""AutoAdapter 1.0-style Go2 driver assembled from the trusted PD-gait skeleton."""
from __future__ import annotations

from pathlib import Path
import types

import mujoco
import numpy as np

from auto_adapter.skeletons import QuadrupedPDGaitSkeleton, QuadrupedSpec


LEG_JOINTS = {
    "FL": ["FL_hip_joint", "FL_thigh_joint", "FL_calf_joint"],
    "FR": ["FR_hip_joint", "FR_thigh_joint", "FR_calf_joint"],
    "RL": ["RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"],
    "RR": ["RR_hip_joint", "RR_thigh_joint", "RR_calf_joint"],
}
LEG_ACTUATORS = {
    leg: [name.removesuffix("_joint") for name in names]
    for leg, names in LEG_JOINTS.items()
}


def _duration(value) -> float:
    duration = float(value)
    if not np.isfinite(duration) or duration <= 0.0:
        raise ValueError("duration must be a finite positive number of seconds")
    return duration


def build(*, model=None, data=None, mjcf_path=None):
    if model is None:
        path = Path(mjcf_path) if mjcf_path else Path("mjcf.xml")
        model = mujoco.MjModel.from_xml_path(str(path.resolve()))
    if data is None:
        data = mujoco.MjData(model)
        key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        mujoco.mj_resetDataKeyframe(model, data, key)
        mujoco.mj_forward(model, data)
    spec = QuadrupedSpec(
        base_body_name="base_link",
        leg_joint_names={name: list(values) for name, values in LEG_JOINTS.items()},
        leg_actuator_names={name: list(values) for name, values in LEG_ACTUATORS.items()},
        home_qpos=[0.0, 0.9, -1.8] * 4,
        body_height_target=0.27,
        kp=80.0,
        kd=4.0,
    )
    robot = QuadrupedPDGaitSkeleton(model, data, spec)

    def hold_stable(self, duration):
        """PD-hold the current leg configuration without resetting base state."""
        hold_duration = _duration(duration)
        q_target = self.get_joint_positions()
        n_steps = max(1, int(hold_duration / float(self.model.opt.timestep)))
        for _ in range(n_steps):
            self._apply_pd(q_target)
            self.step(1)
        position, rotation = self.get_base_pose()
        return bool(np.isfinite(position).all() and np.isfinite(rotation).all())

    def walk_forward(self, duration, speed=0.3):
        """Run the proven 1.0 Go2 diagonal-trot targets through skeleton PD."""
        walk_duration = _duration(duration)
        walk_speed = float(speed)
        if not np.isfinite(walk_speed) or walk_speed < 0.0:
            raise ValueError("speed must be a finite non-negative number in m/s")
        if walk_speed == 0.0:
            return self.hold_stable(walk_duration)

        period = float(np.clip(0.40 * (0.2 / max(walk_speed, 0.05)), 0.25, 0.80))
        swing_fraction = 0.40
        start_position, _ = self.get_base_pose()
        n_steps = max(1, int(walk_duration / float(self.model.opt.timestep)))
        for step_index in range(n_steps):
            phase = (
                step_index * float(self.model.opt.timestep) / period
            ) % 1.0
            q_desired = self._home_q.copy()
            for leg_index in range(len(self._leg_order)):
                # In FL, FR, RL, RR order the diagonal groups are 0+3 and 1+2.
                phase_offset = 0.0 if leg_index in (0, 3) else 0.5
                leg_phase = (phase + phase_offset) % 1.0
                if leg_phase < swing_fraction:
                    thigh, calf = 1.30, -1.30
                else:
                    thigh, calf = 0.75, -1.80
                base_index = leg_index * self._joints_per_leg
                q_desired[base_index] = 0.0
                q_desired[base_index + 1] = thigh
                q_desired[base_index + 2] = calf
            self._apply_pd(q_desired)
            self.step(1)

        end_position, end_rotation = self.get_base_pose()
        return bool(
            np.isfinite(end_position).all()
            and np.isfinite(end_rotation).all()
            and end_position[0] > start_position[0]
        )

    def set_body_height(self, target_height, duration=2.0):
        """Track a symmetric-leg pose derived from Go2's standing kinematics.

        The mapping preserves the stock relation ``calf = -2 * thigh`` and
        scales leg extension relative to the reference standing height. It
        drives only the twelve joint actuators through the skeleton's PD loop;
        the floating base is never written directly.
        """
        target = float(target_height)
        if not np.isfinite(target) or target <= 0.0:
            raise ValueError("target_height must be a finite positive value in metres")
        motion_duration = _duration(duration)

        q_start = self.get_joint_positions()
        q_target = self._home_q.copy()
        standing_height = float(self.spec.body_height_target)
        home_thigh = abs(float(self._home_q[1]))
        desired_cosine = np.cos(home_thigh) * target / standing_height
        if not 0.05 <= desired_cosine <= 0.98:
            raise ValueError(
                f"target_height {target:.3f} m is outside the reference leg's "
                "kinematic range"
            )
        desired_thigh = float(np.arccos(desired_cosine))
        for leg_index in range(len(self._leg_order)):
            thigh_index = leg_index * self._joints_per_leg + 1
            calf_index = leg_index * self._joints_per_leg + 2
            q_target[thigh_index] = np.copysign(
                desired_thigh, self._home_q[thigh_index]
            )
            q_target[calf_index] = np.copysign(
                2.0 * desired_thigh, self._home_q[calf_index]
            )

        n_steps = max(1, int(motion_duration / float(self.model.opt.timestep)))
        for step_index in range(n_steps):
            alpha = (step_index + 1) / n_steps
            q_desired = (1.0 - alpha) * q_start + alpha * q_target
            self._apply_pd(q_desired)
            self.step(1)
        position, rotation = self.get_base_pose()
        return bool(np.isfinite(position).all() and np.isfinite(rotation).all())

    robot.hold_stable = types.MethodType(hold_stable, robot)
    robot.walk_forward = types.MethodType(walk_forward, robot)
    robot.set_body_height = types.MethodType(set_body_height, robot)
    return robot
