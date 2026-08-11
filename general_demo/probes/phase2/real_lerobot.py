"""Opt-in runner for the real pinned LeRobot and Feetech SDK.

The imports are intentionally lazy.  The repository has no LeRobot or
``scservo_sdk`` dependency, so the normal unit-test path remains stdlib-only.
When called on Linux with the pinned packages installed, this module constructs
the real ``SO101Follower`` and verifies that its bus is the real
``FeetechMotorsBus`` before connect/configure/send_action/get_observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .feetech_wire import SO101_MOTOR_NAMES

LEROBOT_VERSION = "0.6.0"
LEROBOT_COMMIT = "30da8e687a6dfc617fcd94afc367ac7071c376ce"


class RealProbeUnavailable(RuntimeError):
    """Raised when the optional real hardware stack is not installed."""


@dataclass(frozen=True, slots=True)
class RealProbeResult:
    sent_action: dict[str, Any]
    observation: dict[str, Any]


def _load_real_symbols():
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


def run_real_so101_probe(port: str) -> RealProbeResult:
    """Run real LeRobot calls against a caller-provided serial/PTY path."""

    SO101Follower, SO101FollowerConfig, FeetechMotorsBus = _load_real_symbols()
    # Keep the real class from trying to create or read a user's global
    # calibration cache.  The probe is disposable and calibration is disabled.
    with TemporaryDirectory(prefix="autoadapter-phase2-calibration-") as calibration_dir:
        config = SO101FollowerConfig(
            port=port,
            id="phase2-source-probe",
            calibration_dir=Path(calibration_dir),
        )
        follower = SO101Follower(config)
        if not isinstance(follower.bus, FeetechMotorsBus):
            raise RuntimeError("SO101Follower did not construct the real FeetechMotorsBus")

        connected = False
        try:
            follower.connect(calibrate=False)
            connected = True
            action = {f"{motor}.pos": 0.0 for motor in SO101_MOTOR_NAMES}
            sent_action = dict(follower.send_action(action))
            observation = dict(follower.get_observation())
            return RealProbeResult(sent_action=sent_action, observation=observation)
        finally:
            if connected:
                follower.disconnect()
