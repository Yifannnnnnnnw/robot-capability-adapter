"""Private pure-math FK/IK support for the deterministic offline fixture."""


def _solve_ik(
    position_m: list[float],
    gripper_position: float,
) -> dict[str, float]:
    """Map the fixture Cartesian state into its deterministic joint state."""

    return {
        "shoulder_pan.pos": float(position_m[1]) / 0.002,
        "shoulder_lift.pos": (float(position_m[0]) - 0.30) / 0.002,
        "elbow_flex.pos": (float(position_m[2]) - 0.05) / 0.001,
        "gripper.pos": float(gripper_position),
    }


def _forward_kinematics(observation: dict[str, float]) -> list[float]:
    """Map the fixture joint state back to its deterministic Cartesian state."""

    return [
        0.30 + float(observation["shoulder_lift.pos"]) * 0.002,
        float(observation["shoulder_pan.pos"]) * 0.002,
        0.05 + float(observation["elbow_flex.pos"]) * 0.001,
    ]
