"""G1 joint-space capability."""

import time


def move_joints(
    runtime: object,
    targets: dict[str, float],
    tolerance: float = 1.0,
    max_steps: int = 100,
) -> dict[str, object]:
    accepted = runtime.send_action(targets)
    final_observation = runtime.get_observation()
    for step in range(1, max_steps + 1):
        final_observation = runtime.get_observation()
        if all(abs(float(final_observation[key]) - float(value)) <= tolerance for key, value in accepted.items()):
            return {
                "status": "succeeded",
                "steps": step,
                "accepted": accepted,
                "final_observation": final_observation,
            }
        time.sleep(0.02)
    return {
        "status": "timed_out",
        "steps": max_steps,
        "accepted": accepted,
        "final_observation": final_observation,
    }
