"""Hardware-free contract probe for pinned LeRobot v0.6.0.

Run this inside the demo's pinned Python 3.12 environment after installing
``lerobot[core_scripts,feetech]==0.6.0``. The fixture never opens a serial port.
It instantiates the follower, replaces the unconnected bus with a recording
fake, and verifies the public imports, fields, six-key feature surface,
command receipt, optional clipping, and no-wait-for-arrival call pattern.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.metadata
import inspect
import json
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_VERSION = "0.6.0"
EXPECTED_COMMIT = "30da8e687a6dfc617fcd94afc367ac7071c376ce"
EXPECTED_RUNTIME_ID = "lerobot_soarm101_0_6_0"
CANONICAL_MODULE = "lerobot.robots.so_follower"
CANONICAL_SYMBOLS = ("SO101Follower", "SO101FollowerConfig")
PUBLIC_METHODS = (
    "setup_motors",
    "connect",
    "calibrate",
    "configure",
    "get_observation",
    "send_action",
    "disconnect",
)
EXPECTED_KEYS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
)
EXPECTED_CONFIG_FIELDS = {
    "id",
    "calibration_dir",
    "port",
    "disable_torque_on_disconnect",
    "max_relative_target",
    "cameras",
    "use_degrees",
}


class RecordingBus:
    def __init__(self) -> None:
        self.motors = {key.removesuffix(".pos"): object() for key in EXPECTED_KEYS}
        self.is_connected = True
        self.is_calibrated = True
        self.present = {name: 0.0 for name in self.motors}
        self.reads: list[str] = []
        self.writes: list[tuple[str, dict[str, float]]] = []

    def sync_read(self, register: str) -> dict[str, float]:
        self.reads.append(register)
        return dict(self.present)

    def sync_write(self, register: str, values: dict[str, float]) -> None:
        self.writes.append((register, dict(values)))

    def disconnect(self, disable_torque: bool) -> None:
        del disable_torque
        self.is_connected = False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_probe() -> dict[str, Any]:
    installed_version = importlib.metadata.version("lerobot")
    require(installed_version == EXPECTED_VERSION, f"expected lerobot {EXPECTED_VERSION}, got {installed_version}")

    from lerobot.robots.so_follower import (  # noqa: PLC0415
        SO101Follower,
        SO101FollowerConfig,
        SOFollower,
        SOFollowerRobotConfig,
    )
    from lerobot.motors import MotorNormMode  # noqa: PLC0415

    require(SO101Follower is SOFollower, "SO101Follower alias changed")
    require(SO101FollowerConfig is SOFollowerRobotConfig, "SO101FollowerConfig alias changed")
    require(SO101Follower.config_class is SOFollowerRobotConfig, "config_class changed")
    require(
        all(callable(getattr(SO101Follower, name, None)) for name in PUBLIC_METHODS),
        "public method surface changed",
    )

    config_fields = {field.name for field in dataclasses.fields(SO101FollowerConfig)}
    require(EXPECTED_CONFIG_FIELDS <= config_fields, f"missing config fields: {EXPECTED_CONFIG_FIELDS - config_fields}")

    with tempfile.TemporaryDirectory(prefix="so101-api-probe-") as temp_dir:
        config = SO101FollowerConfig(
            port="probe://not-opened",
            id="api_probe",
            calibration_dir=Path(temp_dir),
            cameras={},
            use_degrees=True,
            max_relative_target=None,
        )
        robot = SO101Follower(config)

        degree_modes = {name: robot.bus.motors[name].norm_mode for name in robot.bus.motors}
        require(
            all(degree_modes[name] is MotorNormMode.DEGREES for name in degree_modes if name != "gripper"),
            f"use_degrees=True arm modes changed: {degree_modes}",
        )
        require(
            degree_modes["gripper"] is MotorNormMode.RANGE_0_100,
            f"gripper mode changed: {degree_modes['gripper']}",
        )

        normalized_config = SO101FollowerConfig(
            port="probe://not-opened",
            id="normalized_api_probe",
            calibration_dir=Path(temp_dir),
            cameras={},
            use_degrees=False,
        )
        normalized_robot = SO101Follower(normalized_config)
        normalized_modes = {
            name: normalized_robot.bus.motors[name].norm_mode for name in normalized_robot.bus.motors
        }
        require(
            all(
                normalized_modes[name] is MotorNormMode.RANGE_M100_100
                for name in normalized_modes
                if name != "gripper"
            ),
            f"use_degrees=False arm modes changed: {normalized_modes}",
        )
        require(
            normalized_modes["gripper"] is MotorNormMode.RANGE_0_100,
            f"normalized gripper mode changed: {normalized_modes['gripper']}",
        )

        action_features = tuple(robot.action_features)
        observation_features = tuple(robot.observation_features)
        require(action_features == EXPECTED_KEYS, f"unexpected action keys: {action_features}")
        require(observation_features == EXPECTED_KEYS, f"unexpected observation keys: {observation_features}")
        require(all(robot.action_features[key] is float for key in EXPECTED_KEYS), "action feature types changed")
        require(all(robot.observation_features[key] is float for key in EXPECTED_KEYS), "observation feature types changed")

        fake = RecordingBus()
        robot.bus = fake
        action = {key: float(index) for index, key in enumerate(EXPECTED_KEYS, start=1)}
        receipt = robot.send_action(action)

        expected_bare = {key.removesuffix(".pos"): value for key, value in action.items()}
        require(fake.reads == [], "send_action read state even though max_relative_target is disabled")
        require(fake.writes == [("Goal_Position", expected_bare)], f"unexpected write trace: {fake.writes}")
        require(receipt == action, f"receipt differs from the un-clipped action: {receipt}")
        require(fake.present == {name: 0.0 for name in fake.motors}, "send_action waited for or mutated observed state")

        robot.config.max_relative_target = 5.0
        fake.reads.clear()
        fake.writes.clear()
        clipped_receipt = robot.send_action({"shoulder_pan.pos": 10.0})
        require(fake.reads == ["Present_Position"], f"clipping read trace changed: {fake.reads}")
        require(fake.writes == [("Goal_Position", {"shoulder_pan": 5.0})], f"clipping write changed: {fake.writes}")
        require(clipped_receipt == {"shoulder_pan.pos": 5.0}, f"clipped receipt changed: {clipped_receipt}")

        result: dict[str, Any] = {
            "schema_version": "robot_capability.sdk_api_probe_result.v1",
            "status": "pass",
            "target": {
                "package": "lerobot",
                "distribution": "lerobot",
                "version": installed_version,
                "commit": EXPECTED_COMMIT,
            },
            "runtime_id": EXPECTED_RUNTIME_ID,
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "environment_policy": "demo_local_venv",
            },
            "canonical_import": {
                "module": CANONICAL_MODULE,
                "symbols": list(CANONICAL_SYMBOLS),
            },
            "alias_targets": {
                "SO101Follower": "SOFollower",
                "SO101FollowerConfig": "SOFollowerRobotConfig",
            },
            "canonical_aliases": {
                "SO101Follower_is_SOFollower": True,
                "SO101FollowerConfig_is_SOFollowerRobotConfig": True,
            },
            "config_fields": sorted(config_fields),
            "public_methods": list(PUBLIC_METHODS),
            "action_keys": list(action_features),
            "observation_keys": list(observation_features),
            "signatures": {
                name: str(inspect.signature(getattr(SO101Follower, name)))
                for name in PUBLIC_METHODS
            },
            "command_trace": {
                "unclipped_read_count": 0,
                "unclipped_write_count": 1,
                "clipped_read_count": 1,
                "clipped_write_count": 1,
                "waits_for_arrival": False,
            },
            "unit_modes": {
                "use_degrees_true_arm": MotorNormMode.DEGREES.value,
                "use_degrees_false_arm": MotorNormMode.RANGE_M100_100.value,
                "gripper_in_both_profiles": MotorNormMode.RANGE_0_100.value,
            },
            "fixture": "probes/probe_api.py",
            "executed_on": datetime.date.today().isoformat(),
            "hardware_or_serial_opened": False,
        }

        fake.is_connected = False
        return result


if __name__ == "__main__":
    try:
        probe_result = run_probe()
    except Exception as error:  # Probe failures must remain machine-readable.
        print(
            json.dumps(
                {
                    "schema_version": "robot_capability.sdk_api_probe_result.v1",
                    "status": "fail",
                    "target": {
                        "package": "lerobot",
                        "distribution": "lerobot",
                        "version": EXPECTED_VERSION,
                        "commit": EXPECTED_COMMIT,
                    },
                    "runtime_id": EXPECTED_RUNTIME_ID,
                    "environment": {
                        "python_version": platform.python_version(),
                        "platform": platform.platform(),
                        "environment_policy": "demo_local_venv",
                    },
                    "fixture": "probes/probe_api.py",
                    "executed_on": datetime.date.today().isoformat(),
                    "hardware_or_serial_opened": False,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                indent=2,
                sort_keys=True,
            )
        )
        sys.exit(1)
    print(json.dumps(probe_result, indent=2, sort_keys=True))
