"""Minimal LeRobot v0.6.0 SO-101 follower API example.

This example is for real hardware. It demonstrates command-return semantics:
``send_action`` returns the accepted target receipt, not proof of arrival.
"""

from __future__ import annotations

import time

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig


POSITION_KEYS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
)


def main() -> None:
    config = SO101FollowerConfig(
        port="/dev/tty.usbmodem00000000",  # Replace with lerobot-find-port output.
        id="so101_demo_follower",
        cameras={},
        use_degrees=True,
        max_relative_target=5.0,
    )
    robot = SO101Follower(config)
    robot.connect(calibrate=True)
    try:
        observation = robot.get_observation()
        action = {key: float(observation[key]) for key in POSITION_KEYS}
        action["shoulder_pan.pos"] += 2.0  # degrees with use_degrees=True

        sent = robot.send_action(action)
        print("target receipt:", sent)

        # LeRobot does not wait for arrival. A real application must poll and
        # apply its own tolerance and timeout policy.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            current = robot.get_observation()
            if abs(float(current["shoulder_pan.pos"]) - sent["shoulder_pan.pos"]) <= 1.0:
                break
            time.sleep(0.02)
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
