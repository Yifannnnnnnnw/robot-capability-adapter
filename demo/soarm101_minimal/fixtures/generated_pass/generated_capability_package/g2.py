"""G2 Cartesian motion capability for the deterministic P0 fixture mapping."""

import time

from ._kinematics import _forward_kinematics, _solve_ik


def move_to_cartesian(
    runtime: object,
    position_m: list[float],
    gripper_position: float = 80.0,
    tolerance_m: float = 0.005,
    max_steps: int = 100,
) -> dict[str, object]:
    accepted = runtime.send_action(_solve_ik(position_m, gripper_position))
    observation = runtime.get_observation()
    estimated = _forward_kinematics(observation)
    for step in range(1, max_steps + 1):
        observation = runtime.get_observation()
        estimated = _forward_kinematics(observation)
        error = sum((estimated[index] - float(position_m[index])) ** 2 for index in range(3)) ** 0.5
        if error <= tolerance_m:
            return {
                "status": "succeeded",
                "steps": step,
                "accepted": accepted,
                "estimated_position_m": estimated,
                "position_error_m": error,
            }
        time.sleep(0.02)
    return {
        "status": "timed_out",
        "steps": max_steps,
        "accepted": accepted,
        "estimated_position_m": estimated,
        "position_error_m": error,
    }
