# SPDX-License-Identifier: Apache-2.0
"""Private Go2 calibration controller with velocity feedback.

The public AA1 Go2 skeleton intentionally exposes only a short raw policy
command.  This module retains the PI wrapper used by the calibration
reference, so generated drivers cannot accidentally depend on an upper-level
feedback controller hidden inside the public skeleton.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def track_planar_velocity(
    motion: Any,
    vx: float,
    vy: float,
    yaw_rate: float,
    duration: float = 1.0,
    *,
    feedback_period_s: float = 0.04,
    planar_feedback_kp: float = 0.4,
    planar_feedback_ki: float = 0.3,
    yaw_feedback_kp: float = 0.4,
    yaw_feedback_ki: float = 0.3,
    maximum_planar_correction_m_s: float = 0.4,
    maximum_yaw_correction_rad_s: float = 0.8,
) -> dict[str, Any]:
    """Run the private bounded PI wrapper around the public raw command.

    ``motion`` is an already-bound ``Go2VelocityPolicySkeleton`` (or a
    compatible reference motion object) sharing the caller's model/data.
    Every physical step still goes through its public
    ``command_planar_velocity`` primitive.  The returned values are diagnostic
    only; this helper is not part of the public skeleton API.
    """

    spec = motion.spec
    requested = np.asarray(
        (
            _finite(vx, "vx"),
            _finite(vy, "vy"),
            _finite(yaw_rate, "yaw_rate"),
        ),
        dtype=float,
    )
    maximum_planar_speed = _finite(
        spec.maximum_planar_speed_m_s, "maximum_planar_speed_m_s"
    )
    maximum_yaw_rate = _finite(spec.maximum_yaw_rate_rad_s, "maximum_yaw_rate_rad_s")
    maximum_duration = _finite(
        spec.maximum_command_duration_s, "maximum_command_duration_s"
    )
    if float(np.linalg.norm(requested[:2])) > maximum_planar_speed:
        raise ValueError("planar velocity request exceeds the policy bound")
    if abs(float(requested[2])) > maximum_yaw_rate:
        raise ValueError("yaw-rate request exceeds the policy bound")
    duration_s = _finite(duration, "duration")
    if duration_s <= 0.0 or duration_s > maximum_duration:
        raise ValueError("duration is outside the policy bound")

    timestep = _finite(motion.model.opt.timestep, "model timestep")
    feedback_period = _finite(feedback_period_s, "feedback_period_s")
    if feedback_period <= 0.0:
        raise ValueError("feedback_period_s must be positive")
    feedback_ratio = feedback_period / timestep
    feedback_steps = int(round(feedback_ratio))
    if feedback_steps < 1 or not math.isclose(
        feedback_ratio, feedback_steps, rel_tol=0.0, abs_tol=1.0e-9
    ):
        raise ValueError("feedback_period_s must be an integer number of physics steps")
    if feedback_period > 0.05:
        raise ValueError("feedback_period_s must not exceed 0.05 s")

    planar_kp = _finite(planar_feedback_kp, "planar_feedback_kp")
    planar_ki = _finite(planar_feedback_ki, "planar_feedback_ki")
    yaw_kp = _finite(yaw_feedback_kp, "yaw_feedback_kp")
    yaw_ki = _finite(yaw_feedback_ki, "yaw_feedback_ki")
    if min(planar_kp, planar_ki, yaw_kp, yaw_ki) < 0.0:
        raise ValueError("feedback gains must be non-negative")
    if planar_kp == 0.0 and planar_ki == 0.0:
        raise ValueError("planar feedback requires a positive gain")
    if yaw_kp == 0.0 and yaw_ki == 0.0:
        raise ValueError("yaw feedback requires a positive gain")

    planar_correction_limit = _finite(
        maximum_planar_correction_m_s, "maximum_planar_correction_m_s"
    )
    yaw_correction_limit = _finite(
        maximum_yaw_correction_rad_s, "maximum_yaw_correction_rad_s"
    )
    if planar_correction_limit <= 0.0 or planar_correction_limit > maximum_planar_speed:
        raise ValueError("maximum planar correction is outside the policy bound")
    if yaw_correction_limit <= 0.0 or yaw_correction_limit > maximum_yaw_rate:
        raise ValueError("maximum yaw correction is outside the policy bound")

    total_steps = max(1, int(math.ceil(duration_s / timestep)))
    integral = np.zeros(3, dtype=float)
    remaining_steps = total_steps
    feedback_cycles = 0
    physics_steps = 0
    last_policy_command = requested.copy()

    while remaining_steps > 0:
        chunk_steps = min(feedback_steps, remaining_steps)
        chunk_duration = chunk_steps * timestep
        twist = motion.get_base_twist()
        measured = np.asarray(
            (
                twist["linear_body_yaw_m_s"][0],
                twist["linear_body_yaw_m_s"][1],
                twist["yaw_rate_rad_s"],
            ),
            dtype=float,
        )
        error = requested - measured
        integral += error * chunk_duration
        if planar_ki > 0.0:
            planar_integral_limit = planar_correction_limit / planar_ki
            integral[:2] = np.clip(
                integral[:2], -planar_integral_limit, planar_integral_limit
            )
        else:
            integral[:2] = 0.0
        if yaw_ki > 0.0:
            yaw_integral_limit = yaw_correction_limit / yaw_ki
            integral[2] = float(
                np.clip(integral[2], -yaw_integral_limit, yaw_integral_limit)
            )
        else:
            integral[2] = 0.0

        correction = np.asarray(
            (
                planar_kp * error[0] + planar_ki * integral[0],
                planar_kp * error[1] + planar_ki * integral[1],
                yaw_kp * error[2] + yaw_ki * integral[2],
            ),
            dtype=float,
        )
        correction_norm = float(np.linalg.norm(correction[:2]))
        if correction_norm > planar_correction_limit:
            correction[:2] *= planar_correction_limit / correction_norm
        correction[2] = float(
            np.clip(correction[2], -yaw_correction_limit, yaw_correction_limit)
        )
        policy_command = requested + correction
        policy_speed = float(np.linalg.norm(policy_command[:2]))
        safe_planar_limit = float(
            np.nextafter(np.float32(maximum_planar_speed), np.float32(0.0))
        )
        if policy_speed > safe_planar_limit:
            policy_command[:2] *= safe_planar_limit / policy_speed
        safe_yaw_limit = float(
            np.nextafter(np.float32(maximum_yaw_rate), np.float32(0.0))
        )
        policy_command[2] = float(
            np.clip(policy_command[2], -safe_yaw_limit, safe_yaw_limit)
        )
        result = motion.command_planar_velocity(
            float(policy_command[0]),
            float(policy_command[1]),
            float(policy_command[2]),
            duration=chunk_duration,
        )
        physics_steps += int(result["physics_steps"])
        remaining_steps -= chunk_steps
        feedback_cycles += 1
        last_policy_command = policy_command

    final_twist = motion.get_base_twist()
    return {
        "requested_velocity_body_yaw_m_s": requested[:2].copy(),
        "requested_yaw_rate_rad_s": float(requested[2]),
        "requested_duration_s": duration_s,
        "physics_steps": physics_steps,
        "feedback_cycles": feedback_cycles,
        "last_policy_command": last_policy_command.copy(),
        "final_twist": final_twist,
    }


__all__ = ["track_planar_velocity"]
