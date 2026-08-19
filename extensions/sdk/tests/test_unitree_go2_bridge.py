from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

from autoadapter2_sdk.unitree_go2.bridge import (
    ACTIVE_MOTOR_NAMES,
    DDS_MOTOR_SLOT_COUNT,
    Go2BridgeError,
    Go2DDSMuJoCoBridge,
    INACTIVE_SAFE_FIELDS,
    LOWCMD_GPIO,
    LOWCMD_HEAD,
    LOWCMD_LEVEL_FLAG,
    LowStateFrame,
    MuJoCoSensorFrame,
    SportModeStateFrame,
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
class LowCmd:
    motor_cmd: list[Slot]
    head: list[int] = field(default_factory=lambda: list(LOWCMD_HEAD))
    level_flag: int = LOWCMD_LEVEL_FLAG
    gpio: int = LOWCMD_GPIO
    crc: int = 0


class WrongType:
    motor_cmd: list[Slot]
    crc: int


def command() -> LowCmd:
    slots = [Slot(q=1.0, dq=2.0, kp=3.0, kd=4.0, tau=5.0) for _ in range(12)]
    slots.extend(Slot(**INACTIVE_SAFE_FIELDS) for _ in range(8))
    message = LowCmd(slots)
    message.crc = FakeTransport.crc_for(message)
    return message


class FakeBackend:
    actuator_names = ACTIVE_MOTOR_NAMES
    timestep = 0.025

    def __init__(self) -> None:
        self.time = 0.0
        self.controls = [0.0] * 12
        self.q = [0.25] * 12
        self.dq = [0.5] * 12
        self.set_history: list[tuple[float, ...]] = []
        self.closed = False

    @property
    def simulation_time(self) -> float:
        return self.time

    def reset(self) -> None:
        self.time = 0.0
        self.q = [0.25] * 12
        self.dq = [0.5] * 12

    def sensors(self) -> MuJoCoSensorFrame:
        return MuJoCoSensorFrame(
            q=self.q,
            dq=self.dq,
            actuator_force=self.controls,
            imu_quaternion=(1.0, 0.0, 0.0, 0.0),
            imu_gyroscope=(0.1, 0.2, 0.3),
            imu_accelerometer=(1.1, 1.2, 1.3),
            frame_position=(2.0, 3.0, 4.0),
            frame_linear_velocity=(5.0, 6.0, 7.0),
        )

    def set_controls(self, controls) -> None:
        values = tuple(float(value) for value in controls)
        assert len(values) == 12
        self.controls[:] = values
        self.set_history.append(values)

    def step(self) -> None:
        self.time += self.timestep
        self.q[0] += self.controls[0] * 0.001

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self) -> None:
        self.queue: list[object] = []
        self.lowstates: list[LowStateFrame] = []
        self.sportstates: list[SportModeStateFrame] = []
        self.started = False
        self.closed = False

    @staticmethod
    def crc_for(message: LowCmd) -> int:
        total = 0
        for slot in message.motor_cmd:
            total += int(slot.mode)
            total += sum(int(float(getattr(slot, field)) * 1000) for field in ("q", "dq", "kp", "kd", "tau"))
        return total & 0xFFFFFFFF

    def start(self) -> None:
        self.started = True

    def take_lowcmd(self):
        return self.queue.pop(0) if self.queue else None

    def is_lowcmd_type(self, message: object) -> bool:
        return isinstance(message, LowCmd)

    def compute_crc(self, message: object) -> int:
        assert isinstance(message, LowCmd)
        return self.crc_for(message)

    def publish_lowstate(self, state: LowStateFrame) -> None:
        self.lowstates.append(state)

    def publish_sportmodestate(self, state: SportModeStateFrame) -> None:
        self.sportstates.append(state)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def route():
    backend = FakeBackend()
    transport = FakeTransport()
    bridge = Go2DDSMuJoCoBridge(backend, transport)
    bridge.start()
    bridge.reset()
    yield bridge, backend, transport
    bridge.close()


def test_exact_order_and_pd_tau_equation_before_step(route) -> None:
    bridge, backend, transport = route
    transport.queue.append(command())
    result = bridge.step()
    assert result["accepted"] is True
    assert backend.set_history[-1] == pytest.approx((13.25,) * 12)
    assert backend.q[0] > 0.25


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.head.__setitem__(0, 0),
        lambda value: setattr(value, "level_flag", 0),
        lambda value: setattr(value, "gpio", 1),
        lambda value: setattr(value, "crc", value.crc + 1),
        lambda value: value.motor_cmd.pop(),
        lambda value: setattr(value.motor_cmd[0], "mode", 0),
        lambda value: setattr(value.motor_cmd[12], "q", 0.0),
        lambda value: setattr(value.motor_cmd[0], "q", math.nan),
        lambda value: setattr(value.motor_cmd[0], "tau", math.inf),
    ],
)
def test_invalid_lowcmd_never_changes_ctrl_or_refreshes_stale(route, mutate) -> None:
    bridge, backend, transport = route
    good = command()
    transport.queue.append(good)
    bridge.step()
    before = tuple(backend.controls)
    bad = command()
    mutate(bad)
    transport.queue.append(bad)
    result = bridge.step()
    assert result["accepted"] is False
    assert result["rejected_reason"]
    assert tuple(backend.controls) == before
    while backend.time < 0.100:
        result = bridge.step()
    assert result["command_health"] == "STALE"
    assert backend.controls == [0.0] * 12


def test_wrong_dds_type_is_rejected_without_control_mutation(route) -> None:
    bridge, backend, transport = route
    value = command()
    wrong = WrongType()
    wrong.motor_cmd = value.motor_cmd
    wrong.crc = value.crc
    transport.queue.append(wrong)
    before = tuple(backend.controls)
    result = bridge.step()
    assert result["accepted"] is False
    assert tuple(backend.controls) == before


def test_crc_calculator_failure_is_fail_closed() -> None:
    class BrokenCRCTransport(FakeTransport):
        def compute_crc(self, message: object) -> int:
            raise TypeError("malformed SDK payload")

    backend = FakeBackend()
    transport = BrokenCRCTransport()
    bridge = Go2DDSMuJoCoBridge(backend, transport)
    bridge.start()
    bridge.reset()
    transport.queue.append(command())
    before = tuple(backend.controls)
    result = bridge.step()
    assert result["accepted"] is False
    assert result["rejected_reason"] == "LowCmd CRC calculation failed"
    assert tuple(backend.controls) == before
    bridge.close()


def test_state_publication_keeps_20_slots_and_read_only_sport_projection(route) -> None:
    bridge, _, transport = route
    bridge.step()
    low = transport.lowstates[-1]
    sport = transport.sportstates[-1]
    assert len(low.motor_state) == DDS_MOTOR_SLOT_COUNT
    assert low.motor_state[0].q == 0.25
    assert low.motor_state[12].q == 0.0
    assert low.imu_state.quaternion == (1.0, 0.0, 0.0, 0.0)
    assert sport.position == (2.0, 3.0, 4.0)
    assert sport.velocity == (5.0, 6.0, 7.0)


def test_stale_uses_simulation_time_not_wall_time(route, monkeypatch) -> None:
    bridge, backend, transport = route
    transport.queue.append(command())
    bridge.step()
    monkeypatch.setattr("time.monotonic", lambda: 10_000_000.0)
    for _ in range(2):
        result = bridge.step()
    assert backend.time == pytest.approx(0.075)
    assert result["command_health"] == "VALID"
    result = bridge.step()
    assert backend.time == pytest.approx(0.100)
    assert result["command_health"] == "STALE"
    assert backend.controls == [0.0] * 12


def test_backend_order_fails_closed() -> None:
    backend = FakeBackend()
    backend.actuator_names = tuple(reversed(ACTIVE_MOTOR_NAMES))
    with pytest.raises(Go2BridgeError, match="actuator order"):
        Go2DDSMuJoCoBridge(backend, FakeTransport())


def test_translation_contains_no_capability_behavior_symbols() -> None:
    public = set(dir(Go2DDSMuJoCoBridge))
    assert not public.intersection({"sit", "stand", "stand_up", "move", "gait", "ik", "trajectory"})
