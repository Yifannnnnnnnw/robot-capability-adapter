"""Opt-in runner for the real pinned LeRobot and Feetech SDK.

The imports are intentionally lazy.  The repository has no LeRobot or
``scservo_sdk`` dependency, so the normal unit-test path remains stdlib-only.
When called on Linux with the pinned packages installed, this module constructs
the real ``SO101Follower`` and verifies that its bus is the real
``FeetechMotorsBus``.  It also verifies the installed distribution versions
before connect/configure/send_action/get_observation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .feetech_wire import SO101_MOTOR_IDS, SO101_MOTOR_NAMES
from .virtual_feetech import VirtualFeetechPTY

LEROBOT_VERSION = "0.6.0"
LEROBOT_COMMIT = "30da8e687a6dfc617fcd94afc367ac7071c376ce"
FEETECH_SDK_DISTRIBUTION = "feetech-servo-sdk"
FEETECH_SDK_VERSION = "1.0.0"
SO101_PROBE_ID = "phase2-source-probe"
SO101_PROBE_ACTION = {
    "shoulder_pan.pos": -30.0,
    "shoulder_lift.pos": -10.0,
    "elbow_flex.pos": 10.0,
    "wrist_flex.pos": 30.0,
    "wrist_roll.pos": 50.0,
    "gripper.pos": 25.0,
}


class RealProbeUnavailable(RuntimeError):
    """Raised when the optional real hardware stack is not installed."""


class RealProbeIdentityError(RuntimeError):
    """Raised when an installed optional distribution is not the pinned one."""


@dataclass(frozen=True, slots=True)
class RealProbeResult:
    sent_action: dict[str, Any]
    observation: dict[str, Any]


def build_so101_calibration() -> dict[str, dict[str, int]]:
    """Return the disposable six-motor calibration consumed by LeRobot."""

    return {
        motor_name: {
            "id": SO101_MOTOR_IDS[motor_name],
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 0,
            "range_max": 4095,
        }
        for motor_name in SO101_MOTOR_NAMES
    }


def so101_action_to_raw_ticks(action: Mapping[str, float]) -> dict[int, int]:
    """Mirror the pinned LeRobot normalization for this probe's action."""

    expected_keys = {f"{motor_name}.pos" for motor_name in SO101_MOTOR_NAMES}
    if set(action) != expected_keys:
        raise ValueError(f"SO-101 action keys must be exactly {sorted(expected_keys)}")

    raw_ticks: dict[int, int] = {}
    for motor_name in SO101_MOTOR_NAMES:
        value = float(action[f"{motor_name}.pos"])
        motor_id = SO101_MOTOR_IDS[motor_name]
        if motor_name == "gripper":
            bounded_value = min(100.0, max(0.0, value))
            raw_ticks[motor_id] = int((bounded_value / 100.0) * 4095)
        else:
            raw_ticks[motor_id] = int((value * 4095 / 360.0) + (4095 / 2))
    return raw_ticks


def _write_so101_calibration(calibration_dir: Path) -> None:
    calibration_path = calibration_dir / f"{SO101_PROBE_ID}.json"
    calibration_path.write_text(
        json.dumps(build_so101_calibration(), sort_keys=True),
        encoding="utf-8",
    )


def verify_pinned_distribution_identity() -> dict[str, str]:
    """Require the exact distributions named by this source-evidence probe."""

    expected = {
        "lerobot": LEROBOT_VERSION,
        FEETECH_SDK_DISTRIBUTION: FEETECH_SDK_VERSION,
    }
    installed: dict[str, str] = {}
    for distribution, expected_version in expected.items():
        try:
            installed_version = metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            raise RealProbeUnavailable(
                f"required distribution {distribution!r} is not installed"
            ) from exc
        if installed_version != expected_version:
            raise RealProbeIdentityError(
                f"pinned probe requires {distribution}=={expected_version}, "
                f"found {installed_version}"
            )
        installed[distribution] = installed_version
    return installed


def _load_real_symbols():
    verify_pinned_distribution_identity()
    try:
        from lerobot.motors.feetech import FeetechMotorsBus
        try:
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        except ImportError:
            # LeRobot 0.6.0 source layouts seen in the wild expose the same
            # public classes through ``so_follower``.
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        import scservo_sdk  # noqa: F401 - prove the real SDK import is present
    except (ImportError, ModuleNotFoundError) as exc:
        raise RealProbeUnavailable(
            "real LeRobot 0.6.0/scservo_sdk is not installed; Linux integration was not run: "
            f"{exc}"
        ) from exc
    return SO101Follower, SO101FollowerConfig, FeetechMotorsBus


def _resource_is_connected(follower: Any) -> bool:
    """Conservatively inspect both the follower and its underlying bus."""

    state_error = False
    try:
        bus = follower.bus
    except BaseException:
        bus = None
        state_error = True

    for resource in (follower, bus):
        if resource is None:
            continue
        try:
            if bool(resource.is_connected):
                return True
        except BaseException:
            state_error = True

    # If state cannot be read, attempt cleanup rather than risk leaking an
    # opened port.  A primary exception still controls propagation below.
    return state_error


def _cleanup_real_follower(follower: Any, primary_error: BaseException | None) -> None:
    """Disconnect when resources may be open without masking a primary error."""

    if not _resource_is_connected(follower):
        return
    try:
        follower.disconnect()
    except BaseException as cleanup_error:
        if primary_error is None:
            raise
        primary_error.add_note(f"real probe cleanup failed: {cleanup_error!r}")


def run_real_so101_probe(virtual_pty: VirtualFeetechPTY) -> RealProbeResult:
    """Run real LeRobot calls only against this project's opened virtual PTY."""

    if type(virtual_pty) is not VirtualFeetechPTY:
        raise TypeError("real source probe accepts only a VirtualFeetechPTY instance")
    if not virtual_pty.is_open:
        raise ValueError("VirtualFeetechPTY must be opened with its context manager")
    try:
        port = virtual_pty.port
    except (OSError, RuntimeError) as exc:
        raise ValueError("VirtualFeetechPTY port is not available") from exc

    SO101Follower, SO101FollowerConfig, FeetechMotorsBus = _load_real_symbols()
    # Keep the real class from trying to create or read a user's global
    # calibration cache.  The probe is disposable and calibration is disabled.
    with TemporaryDirectory(prefix="autoadapter-phase2-calibration-") as calibration_dir:
        calibration_root = Path(calibration_dir)
        _write_so101_calibration(calibration_root)
        config = SO101FollowerConfig(
            port=port,
            id=SO101_PROBE_ID,
            calibration_dir=calibration_root,
        )
        follower = SO101Follower(config)
        if not isinstance(follower.bus, FeetechMotorsBus):
            raise RuntimeError("SO101Follower did not construct the real FeetechMotorsBus")

        primary_error: BaseException | None = None
        try:
            follower.connect(calibrate=False)
            sent_action = dict(follower.send_action(SO101_PROBE_ACTION))
            observation = dict(follower.get_observation())
            return RealProbeResult(sent_action=sent_action, observation=observation)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            _cleanup_real_follower(follower, primary_error)
