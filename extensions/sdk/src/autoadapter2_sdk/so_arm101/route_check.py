"""Minimal real-SDK route check for the SO-ARM101 extension."""

from __future__ import annotations

import importlib.metadata
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from .feetech_protocol import MOTOR_IDS, MOTOR_NAMES
from .translation import FeetechPTYTranslation, MuJoCoSO101Backend, PositionBackend

EXPECTED_PACKAGES = {
    "lerobot": "0.6.0",
    "feetech-servo-sdk": "1.0.0",
    "mujoco": "3.3.6",
}
PROBE_ACTION = {
    "shoulder_pan.pos": -30.0,
    "shoulder_lift.pos": -10.0,
    "elbow_flex.pos": 10.0,
    "wrist_flex.pos": 30.0,
    "wrist_roll.pos": 50.0,
    "gripper.pos": 25.0,
}
VIRTUAL_PTY_EMPTY_READ_YIELD_S = 0.0005


class SO101RouteCheckError(RuntimeError):
    """The declared SO-101 SDK route did not complete."""


def public_positions_to_ticks(values: dict[str, Any]) -> dict[int, int]:
    expected = {f"{name}.pos" for name in MOTOR_NAMES}
    if set(values) != expected:
        raise SO101RouteCheckError("SDK position fields do not exactly match SO-101")
    result: dict[int, int] = {}
    for name in MOTOR_NAMES:
        value = float(values[f"{name}.pos"])
        if not math.isfinite(value):
            raise SO101RouteCheckError("SDK position is non-finite")
        fraction = min(100.0, max(0.0, value)) / 100.0 if name == "gripper" else (value + 180.0) / 360.0
        result[MOTOR_IDS[name]] = min(4095, max(0, int(fraction * 4095)))
    return result


def _real_sdk_identity() -> dict[str, Any]:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise SO101RouteCheckError("the real SO-101 route requires Linux amd64")
    if sys.version_info[:2] != (3, 12):
        raise SO101RouteCheckError(
            f"the pinned route requires CPython 3.12, found {platform.python_version()}"
        )
    installed = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    if installed != EXPECTED_PACKAGES:
        raise SO101RouteCheckError(
            f"package versions differ from the pinned route: expected {EXPECTED_PACKAGES}, got {installed}"
        )
    return {
        "platform": "linux-amd64",
        "python": platform.python_version(),
        "packages": installed,
    }


def _write_calibration(directory: Path) -> None:
    calibration = {
        name: {
            "id": MOTOR_IDS[name],
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 0,
            "range_max": 4095,
        }
        for name in MOTOR_NAMES
    }
    (directory / "autoadapter-sdk-route.json").write_text(
        json.dumps(calibration, sort_keys=True), encoding="utf-8"
    )


def _create_real_follower(port: str, calibration_dir: Path) -> Any:
    if not port.startswith("/dev/pts/"):
        raise SO101RouteCheckError(f"expected a project PTY, got {port}")
    try:
        from lerobot.motors.feetech import FeetechMotorsBus
        try:
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        except ImportError:
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        import scservo_sdk  # noqa: F401
    except ImportError as exc:
        raise SO101RouteCheckError(f"pinned LeRobot/Feetech imports failed: {exc}") from exc

    _write_calibration(calibration_dir)
    follower = SO101Follower(
        SO101FollowerConfig(
            port=port,
            id="autoadapter-sdk-route",
            calibration_dir=calibration_dir,
        )
    )
    if not isinstance(follower.bus, FeetechMotorsBus):
        raise SO101RouteCheckError("SO101Follower did not construct FeetechMotorsBus")

    port_handler = follower.bus.port_handler
    original_read_port = port_handler.readPort

    def cooperative_read_port(length: int) -> Any:
        data = original_read_port(length)
        if not data:
            time.sleep(VIRTUAL_PTY_EMPTY_READ_YIELD_S)
        return data

    port_handler.readPort = cooperative_read_port
    return follower


def _expected_ticks_from_state(
    named_qpos: dict[str, float],
    *,
    gripper_range: tuple[float, float],
    gripper_tick_increases_qpos: bool,
) -> dict[int, int]:
    result: dict[int, int] = {}
    for name in MOTOR_NAMES:
        value = float(named_qpos[name])
        if name == "gripper":
            low, high = gripper_range
            fraction = (value - low) / (high - low)
            if not gripper_tick_increases_qpos:
                fraction = 1.0 - fraction
        else:
            fraction = (math.degrees(value) + 180.0) / 360.0
        result[MOTOR_IDS[name]] = min(4095, max(0, int(round(fraction * 4095))))
    return result


def run_route_check(
    *,
    model_path: str | Path,
    gripper_tick_increases_qpos: bool,
    command_timeout_s: float = 2.0,
    simulation_s: float = 0.25,
    identity_checker: Callable[[], dict[str, Any]] | None = None,
    backend_factory: Callable[[], PositionBackend] | None = None,
    follower_factory: Callable[[str, Path], Any] | None = None,
) -> dict[str, Any]:
    """Exercise the bidirectional SDK route once without retries."""
    if command_timeout_s <= 0 or simulation_s <= 0:
        raise SO101RouteCheckError("timeouts and simulation duration must be positive")
    injected = any(
        value is not None for value in (identity_checker, backend_factory, follower_factory)
    )
    identity = (identity_checker or _real_sdk_identity)()
    backend_factory = backend_factory or (
        lambda: MuJoCoSO101Backend(
            model_path,
            gripper_tick_increases_qpos=gripper_tick_increases_qpos,
        )
    )
    follower_factory = follower_factory or _create_real_follower

    backend: PositionBackend | None = None
    translation: FeetechPTYTranslation | None = None
    follower: Any | None = None
    completed: list[str] = ["sdk_identity"]
    result: dict[str, Any] | None = None
    failure: Exception | None = None
    cleanup_errors: list[str] = []

    try:
        backend = backend_factory()
        translation = FeetechPTYTranslation(backend)
        port = translation.install()
        completed.append("translation_install")

        with TemporaryDirectory(prefix="autoadapter-so101-route-") as calibration_dir:
            follower = follower_factory(port, Path(calibration_dir))
            follower.connect(calibrate=False)
            completed.append("real_sdk_application")

            before = backend.state()
            before_goal_writes = translation.accepted_goal_writes
            accepted_action = dict(follower.send_action(PROBE_ACTION))
            required_goal_writes = before_goal_writes + len(MOTOR_NAMES)
            if not translation.wait_for_goal_writes(required_goal_writes, command_timeout_s):
                raise SO101RouteCheckError("timed out waiting for SDK goal writes")
            after_command = backend.state()
            if before["ctrl"] == after_command["ctrl"]:
                raise SO101RouteCheckError("SDK command did not change MuJoCo controls")
            if before["qpos"] != after_command["qpos"] or before["qvel"] != after_command["qvel"]:
                raise SO101RouteCheckError("SDK command changed state without physics stepping")
            completed.append("sdk_to_mujoco_control")

            translation.step(simulation_s)
            observation = dict(follower.get_observation())
            observed_ticks = public_positions_to_ticks(observation)
            state = backend.state()
            expected_ticks = _expected_ticks_from_state(
                state["named_qpos"],
                gripper_range=tuple(state["gripper_control_range"]),
                gripper_tick_increases_qpos=gripper_tick_increases_qpos,
            )
            tick_errors = {
                str(motor_id): abs(observed_ticks[motor_id] - expected_ticks[motor_id])
                for motor_id in expected_ticks
            }
            if max(tick_errors.values()) > 1:
                raise SO101RouteCheckError(
                    f"SDK observation differs from MuJoCo state by more than one tick: {tick_errors}"
                )
            completed.append("mujoco_to_sdk_observation")

            translation.reset()
            first_reset = backend.state()
            translation.step(min(simulation_s, 0.01))
            translation.reset()
            second_reset = backend.state()
            for field in ("qpos", "qvel"):
                if first_reset[field] != second_reset[field]:
                    raise SO101RouteCheckError(f"consecutive reset {field} values differ")
            completed.append("reset")

            result = {
                "passed": True,
                "sdk_grounded": not injected,
                "robot_configuration_id": "so-arm101-follower-stock-gripper",
                "route": "SO101Follower -> Feetech PTY -> MuJoCo -> Feetech PTY -> SO101Follower",
                "sdk": identity,
                "model_path": str(Path(model_path).resolve()),
                "checks": completed,
                "accepted_action_fields": sorted(accepted_action),
                "observation_fields": sorted(observation),
                "max_observation_tick_error": max(tick_errors.values()),
                "test_dependencies_injected": injected,
            }
    except Exception as exc:
        failure = exc
    finally:
        if follower is not None:
            try:
                follower.disconnect()
            except Exception as exc:
                cleanup_errors.append(f"follower: {exc}")
        if translation is not None:
            try:
                translation.close()
            except Exception as exc:
                cleanup_errors.append(f"translation: {exc}")
        elif backend is not None:
            try:
                backend.close()
            except Exception as exc:
                cleanup_errors.append(f"backend: {exc}")

    if failure is not None:
        detail = str(failure)
        if cleanup_errors:
            detail += f"; cleanup errors: {cleanup_errors}"
        raise SO101RouteCheckError(detail) from failure
    if cleanup_errors:
        raise SO101RouteCheckError(f"route cleanup failed: {cleanup_errors}")
    assert result is not None
    return result
