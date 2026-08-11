from __future__ import annotations

import os
import select
from pathlib import Path
from types import SimpleNamespace

from autoadapter2.integrations.so_arm101.feetech_protocol import (
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
from autoadapter2.integrations.so_arm101.readiness import (
    CHECK_IDS,
    public_positions_to_ticks,
    run_readiness,
)
from autoadapter2.integration.artifacts import validate_readiness_report


ROOT = Path(__file__).resolve().parents[1]


class FakeBackend:
    timestep = 0.005

    def __init__(self) -> None:
        self.qpos = [0.0] * 6
        self.qvel = [0] * 6
        self.ctrl = [0.0] * 6

    @staticmethod
    def _tick_to_value(motor_id, tick):
        fraction = tick / 4095.0
        if motor_id == 6:
            return -0.17453 + fraction * (1.74533 + 0.17453)
        return (fraction * 2.0 - 1.0) * 3.141592653589793

    @staticmethod
    def _value_to_tick(motor_id, value):
        if motor_id == 6:
            fraction = (value + 0.17453) / (1.74533 + 0.17453)
        else:
            fraction = (value / 3.141592653589793 + 1.0) / 2.0
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
        self.qvel = [0] * 6

    def reset(self):
        self.qpos = [0.0] * 6
        self.qvel = [0] * 6
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
        pass


class FakeFollower:
    def __init__(self, port: str) -> None:
        self.port = port
        self.fd = None
        self.bus = SimpleNamespace(is_connected=False)
        self.is_connected = False

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
        self.is_connected = True
        self.bus.is_connected = True

    def send_action(self, action):
        ticks = public_positions_to_ticks(dict(action))
        assert self.fd is not None
        os.write(self.fd, encode_sync_write(42, 2, ticks))
        return dict(action)

    def get_observation(self):
        assert self.fd is not None
        os.write(self.fd, encode_sync_read(PRESENT_POSITION[0], 2, MOTOR_IDS.values()))
        ticks = {
            packet.motor_id: int.from_bytes(packet.params, "little")
            for packet in (decode_packet(frame) for frame in self._responses(6))
        }
        result = {}
        for name in MOTOR_NAMES:
            tick = ticks[MOTOR_IDS[name]]
            result[f"{name}.pos"] = tick * 100.0 / 4095 if name == "gripper" else tick * 360.0 / 4095 - 180.0
        return result

    def disconnect(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.is_connected = False
        self.bus.is_connected = False


def test_injected_mechanics_exercise_all_six_checks_but_cannot_claim_formal_pass(tmp_path: Path) -> None:
    manifest = tmp_path / "integration_manifest.json"
    profile = tmp_path / "profile.json"
    runtime_lock = tmp_path / "runtime-lock.json"
    manifest.write_bytes((ROOT / "integrations/so-arm101/integration_manifest.json").read_bytes())
    profile.write_bytes((ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json").read_bytes())
    runtime_lock.write_bytes((ROOT / "environments/so-arm101-linux-amd64/1.0.0/runtime-lock.json").read_bytes())
    model = tmp_path / "model.xml"
    model.write_text("<mujoco/>", encoding="utf-8")
    backend = FakeBackend()
    report = run_readiness(
        run_id="unit-fake-route",
        manifest_path=manifest,
        profile_path=profile,
        runtime_lock_path=runtime_lock,
        model_path=model,
        gripper_tick_increases_qpos=True,
        report_path=tmp_path / "report.json",
        reference_root=tmp_path,
        identity_checker=lambda _lock: {"identity": "TEST_ONLY"},
        backend_factory=lambda: backend,
        sdk_factory=lambda port, _calibration: FakeFollower(port),
    )
    assert [item["check_id"] for item in report["checks"]] == list(CHECK_IDS)
    assert report["checks"][0]["verdict"] == "FAIL"
    assert report["checks"][0]["infrastructure_category"] == "test_dependency_injection"
    assert all(item["verdict"] == "PASS" for item in report["checks"][1:]), report
    assert report["cleanup"]["verdict"] == "PASS"
    assert report["verdict"] == "FAIL"
    validate_readiness_report(report)


def test_public_position_conversion_requires_exact_six_finite_fields() -> None:
    values = {f"{name}.pos": 0.0 for name in MOTOR_NAMES}
    values["gripper.pos"] = 50.0
    ticks = public_positions_to_ticks(values)
    assert set(ticks) == set(MOTOR_IDS.values())
    assert ticks[6] == 2047
    values.pop("wrist_roll.pos")
    try:
        public_positions_to_ticks(values)
    except Exception as exc:
        assert "exactly match" in str(exc)
    else:
        raise AssertionError("missing SDK field was accepted")
