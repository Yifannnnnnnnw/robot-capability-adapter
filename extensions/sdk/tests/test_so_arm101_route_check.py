from __future__ import annotations

import math
import os
import select
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoadapter2_sdk.so_arm101.feetech_protocol import (
    INST_PING,
    MOTOR_IDS,
    MOTOR_NAMES,
    PRESENT_POSITION,
    decode_packet,
    encode_packet,
    encode_sync_read,
    encode_sync_write,
    pop_frames,
)
from autoadapter2_sdk.so_arm101.route_check import (
    SO101RouteCheckError,
    public_positions_to_ticks,
    run_route_check,
)


class FakeBackend:
    timestep = 0.005

    def __init__(self) -> None:
        self.qpos = [0.0] * 6
        self.qvel = [0.0] * 6
        self.ctrl = [0.0] * 6
        self.closed = False

    @staticmethod
    def _tick_to_value(motor_id: int, tick: int) -> float:
        fraction = tick / 4095.0
        if motor_id == MOTOR_IDS["gripper"]:
            return -0.17453 + fraction * (1.74533 + 0.17453)
        return (fraction * 2.0 - 1.0) * math.pi

    @staticmethod
    def _value_to_tick(motor_id: int, value: float) -> int:
        if motor_id == MOTOR_IDS["gripper"]:
            fraction = (value + 0.17453) / (1.74533 + 0.17453)
        else:
            fraction = (value / math.pi + 1.0) / 2.0
        return min(4095, max(0, round(fraction * 4095)))

    def set_goal_ticks(self, values):
        for motor_id, tick in values.items():
            self.ctrl[motor_id - 1] = self._tick_to_value(motor_id, tick)

    def present_ticks(self, motor_ids):
        return {
            motor_id: self._value_to_tick(motor_id, self.qpos[motor_id - 1])
            for motor_id in motor_ids
        }

    def step(self, seconds):
        del seconds
        self.qpos = list(self.ctrl)
        self.qvel = [0.0] * 6

    def reset(self):
        self.qpos = [0.0] * 6
        self.qvel = [0.0] * 6
        self.ctrl = [0.0] * 6

    def state(self):
        return {
            "qpos": list(self.qpos),
            "qvel": list(self.qvel),
            "ctrl": list(self.ctrl),
            "named_qpos": dict(zip(MOTOR_NAMES, self.qpos, strict=True)),
            "named_ctrl": dict(zip(MOTOR_NAMES, self.ctrl, strict=True)),
            "gripper_control_range": [-0.17453, 1.74533],
        }

    def close(self):
        self.closed = True


class FakeFollower:
    def __init__(self, port: str) -> None:
        self.port = port
        self.fd: int | None = None
        self.bus = SimpleNamespace(is_connected=False)

    def _responses(self, expected: int) -> list[bytes]:
        assert self.fd is not None
        buffer = bytearray()
        frames: list[bytes] = []
        while len(frames) < expected:
            readable, _, _ = select.select([self.fd], [], [], 1.0)
            if not readable:
                raise TimeoutError("fake SDK timed out waiting for PTY status")
            buffer.extend(os.read(self.fd, 4096))
            frames.extend(pop_frames(buffer))
        return frames

    def connect(self, calibrate=False):
        assert calibrate is False
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY)
        os.write(self.fd, encode_packet(1, INST_PING))
        assert decode_packet(self._responses(1)[0]).motor_id == 1
        self.bus.is_connected = True

    def send_action(self, action):
        assert self.fd is not None
        os.write(self.fd, encode_sync_write(42, 2, public_positions_to_ticks(dict(action))))
        return dict(action)

    def get_observation(self):
        assert self.fd is not None
        os.write(self.fd, encode_sync_read(PRESENT_POSITION[0], 2, MOTOR_IDS.values()))
        ticks = {
            packet.motor_id: int.from_bytes(packet.params, "little")
            for packet in (decode_packet(frame) for frame in self._responses(6))
        }
        return {
            f"{name}.pos": (
                ticks[MOTOR_IDS[name]] * 100.0 / 4095
                if name == "gripper"
                else ticks[MOTOR_IDS[name]] * 360.0 / 4095 - 180.0
            )
            for name in MOTOR_NAMES
        }

    def disconnect(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.bus.is_connected = False


def test_injected_route_exercises_mechanics_but_is_not_sdk_grounded(tmp_path: Path) -> None:
    backend = FakeBackend()
    result = run_route_check(
        model_path=tmp_path / "model.xml",
        gripper_tick_increases_qpos=True,
        identity_checker=lambda: {"identity": "TEST_ONLY"},
        backend_factory=lambda: backend,
        follower_factory=lambda port, _calibration: FakeFollower(port),
    )

    assert result["passed"] is True
    assert result["sdk_grounded"] is False
    assert result["test_dependencies_injected"] is True
    assert result["checks"] == [
        "sdk_identity",
        "translation_install",
        "real_sdk_application",
        "sdk_to_mujoco_control",
        "mujoco_to_sdk_observation",
        "reset",
    ]
    assert result["max_observation_tick_error"] <= 1
    assert backend.closed is True


def test_public_position_conversion_requires_exact_finite_fields() -> None:
    positions = {f"{name}.pos": 0.0 for name in MOTOR_NAMES}
    positions["gripper.pos"] = 50.0
    assert set(public_positions_to_ticks(positions)) == set(MOTOR_IDS.values())

    with pytest.raises(SO101RouteCheckError, match="exactly"):
        public_positions_to_ticks({"shoulder_pan.pos": 0.0})
    positions["wrist_roll.pos"] = float("nan")
    with pytest.raises(SO101RouteCheckError, match="non-finite"):
        public_positions_to_ticks(positions)
