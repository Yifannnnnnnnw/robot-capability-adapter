from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autoadapter2_sdk.unitree_go2.bridge import (
    ACTIVE_MOTOR_NAMES,
    INACTIVE_SAFE_FIELDS,
    LOWCMD_GPIO,
    LOWCMD_HEAD,
    LOWCMD_LEVEL_FLAG,
    LowStateFrame,
    MuJoCoSensorFrame,
    SportModeStateFrame,
)
from autoadapter2_sdk.unitree_go2.route_check import (
    Go2RouteCheckError,
    _real_sdk_identity,
    run_route_check,
)


@dataclass
class Slot:
    mode: int = 1
    q: float = 0.0
    dq: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    tau: float = 0.0


@dataclass
class Command:
    motor_cmd: list[Slot]
    head: list[int] = field(default_factory=lambda: list(LOWCMD_HEAD))
    level_flag: int = LOWCMD_LEVEL_FLAG
    gpio: int = LOWCMD_GPIO
    crc: int = 123


class FakeBackend:
    actuator_names = ACTIVE_MOTOR_NAMES
    timestep = 0.01

    def __init__(self) -> None:
        self.time = 0.0
        self.q = [0.0] * 12
        self.dq = [0.0] * 12
        self.ctrl = [0.0] * 12
        self.closed = False

    @property
    def simulation_time(self):
        return self.time

    def reset(self):
        self.time = 0.0
        self.q[:] = [0.0] * 12
        self.dq[:] = [0.0] * 12

    def sensors(self):
        return MuJoCoSensorFrame(
            self.q,
            self.dq,
            self.ctrl,
            (1.0, 0.0, 0.0, 0.0),
            (0.1, 0.2, 0.3),
            (1.0, 2.0, 3.0),
            (4.0, 5.0, 6.0),
            (7.0, 8.0, 9.0),
        )

    def set_controls(self, controls):
        self.ctrl[:] = list(controls)

    def step(self):
        self.time += self.timestep
        self.q[0] += self.ctrl[0] * self.timestep
        self.dq[0] = self.ctrl[0]

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.command = None
        self.lowstate = None
        self.sportstate = None
        self.closed = False

    def start(self):
        return None

    def take_lowcmd(self):
        command, self.command = self.command, None
        return command

    def is_lowcmd_type(self, message):
        return isinstance(message, Command)

    def compute_crc(self, message):
        return 123

    def publish_lowstate(self, state):
        self.lowstate = state

    def publish_sportmodestate(self, state):
        self.sportstate = state

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, transport: FakeTransport):
        self.transport = transport
        self.closed = False

    def start(self):
        return None

    def send_probe(self, state):
        slots = []
        for index in range(20):
            if index < 12:
                slots.append(
                    Slot(
                        q=state.q[index] + (0.1 if index == 0 else 0.0),
                        kp=20.0 if index == 0 else 0.0,
                        kd=0.5 if index == 0 else 0.0,
                    )
                )
            else:
                slots.append(Slot(**INACTIVE_SAFE_FIELDS))
        self.transport.command = Command(slots)

    def clear_observations(self):
        self.transport.lowstate = None
        self.transport.sportstate = None

    def wait_lowstate(self, timeout_s):
        del timeout_s
        assert isinstance(self.transport.lowstate, LowStateFrame)
        return self.transport.lowstate

    def wait_sportmodestate(self, timeout_s):
        del timeout_s
        assert isinstance(self.transport.sportstate, SportModeStateFrame)
        return self.transport.sportstate

    def close(self):
        self.closed = True


def test_injected_route_exercises_mechanics_but_is_not_sdk_grounded(tmp_path: Path) -> None:
    backend = FakeBackend()
    transport = FakeTransport()
    client = FakeClient(transport)
    result = run_route_check(
        model_path=tmp_path / "scene.xml",
        identity_checker=lambda: {"identity": "TEST_ONLY"},
        backend_factory=lambda: backend,
        transport_factory=lambda: transport,
        sdk_factory=lambda: client,
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
    assert result["joint_0_change_rad"] >= 1e-5
    assert result["max_observation_error"] == 0.0
    assert backend.closed is True
    assert transport.closed is True
    assert client.closed is True


def test_real_identity_rejects_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    with pytest.raises(Go2RouteCheckError, match="Linux amd64"):
        _real_sdk_identity()
