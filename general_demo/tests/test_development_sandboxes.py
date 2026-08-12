from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2.implementation import CallbackSandbox
from autoadapter2.integrations.so_arm101.development_sandbox import (
    SO_ARM101_DEVELOPMENT_PROBE_CONTRACT,
    SO_ARM101_JOINT_ORDER,
    SO_ARM101_MAX_DURATION_S,
    SO_ARM101_MOTOR_IDS,
    SOArm101DevelopmentProbe,
    public_positions_to_ticks,
)
from autoadapter2.integrations.unitree_go2.development_sandbox import (
    GO2_DEVELOPMENT_PROBE_CONTRACT,
    GO2_JOINT_ORDER,
    GO2_MAX_STEPS,
    GO2_MOTOR_ORDER,
    Go2DevelopmentProbe,
)


def _go2_probe(**overrides: Any) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "probe_id": "probe-1",
        "capability_id": "capability_1",
        "arguments": {"target": 1.0},
        "horizon_s": 0.2,
    }
    probe.update(overrides)
    return probe


def _go2_source(command: str = "target") -> str:
    return f"""
from unitree_sdk2py.core.channel import ChannelPublisher
from unitree_sdk2py.idl.default import unitree_go_msg_dds__LowCmd_
from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowCmd_
from unitree_sdk2py.utils.crc import CRC
import time

def capability_capability_1(target, *, _sdk):
    publisher = ChannelPublisher('rt/lowcmd', LowCmd_)
    publisher.Init()
    command = unitree_go_msg_dds__LowCmd_()
    for index, slot in enumerate(command.motor_cmd):
        slot.mode = 1
        if index == 0:
            slot.q = {command}
            slot.kp = 10.0
        else:
            slot.q = 2146000000.0
            slot.dq = 16000.0
            slot.kp = 0.0
            slot.kd = 0.0
            slot.tau = 0.0
    command.crc = CRC().Crc(command)
    publisher.Write(command)
    time.sleep(0.2)
"""


class _Go2Backend:
    def __init__(self, instances: list["_Go2Backend"], path: object) -> None:
        self.instances = instances
        self.path = path
        self.time = 0.0
        self.timestep = 0.1
        self.actuator_names = GO2_MOTOR_ORDER
        self.q = [0.0 for _ in range(12)]
        self.dq = [0.0 for _ in range(12)]
        self.controls: list[tuple[float, ...]] = []
        self.events: list[str] = []
        self.closed = False
        self.fail_step = False
        instances.append(self)

    @property
    def simulation_time(self) -> float:
        return self.time

    def reset(self) -> None:
        self.events.append("reset")
        self.time = 0.0

    def sensors(self) -> SimpleNamespace:
        self.events.append("sensors")
        return SimpleNamespace(
            q=tuple(self.q),
            dq=tuple(self.dq),
            actuator_force=tuple(self.controls[-1]) if self.controls else (0.0,) * 12,
            frame_position=(1.0, 2.0, 3.0),
            frame_linear_velocity=(4.0, 5.0, 6.0),
            imu_quaternion=(1.0, 0.0, 0.0, 0.0),
            imu_gyroscope=(0.0, 0.0, 0.0),
            imu_accelerometer=(0.0, 0.0, 0.0),
        )

    def set_controls(self, controls: tuple[float, ...]) -> None:
        self.events.append("set_controls")
        self.controls.append(tuple(controls))

    def step(self) -> None:
        self.events.append("step")
        if self.fail_step:
            raise RuntimeError("step failed")
        self.dq[0] = self.controls[-1][0] if self.controls else 0.0
        self.q[0] += self.dq[0] * self.timestep
        self.time += self.timestep

    def close(self) -> None:
        self.events.append("close")
        self.closed = True


def test_contracts_are_json_compatible_and_declare_closed_public_surfaces() -> None:
    for contract, expected_fields, expected_motor_order, expected_joint_order in (
        (
            GO2_DEVELOPMENT_PROBE_CONTRACT,
            {"probe_id", "capability_id", "arguments", "horizon_s"},
            GO2_MOTOR_ORDER,
            GO2_JOINT_ORDER,
        ),
        (
            SO_ARM101_DEVELOPMENT_PROBE_CONTRACT,
            {"probe_id", "capability_id", "target_position", "duration_s"},
            SO_ARM101_JOINT_ORDER,
            SO_ARM101_JOINT_ORDER,
        ),
    ):
        json.dumps(contract)
        assert set(contract["probe_fields"]) == expected_fields
        assert contract["motor_order"] == list(expected_motor_order)
        assert contract["joint_order"] == list(expected_joint_order)
        assert contract["capability_source"]["execution"] is (contract is GO2_DEVELOPMENT_PROBE_CONTRACT)
        text = json.dumps(contract).lower()
        for forbidden in ("private", "criterion", "validation", "threshold", "mujoco", "simulator", "raw_state", "score", "target_error"):
            assert forbidden not in text


def test_go2_executes_source_and_distinct_sources_have_distinct_physical_outcomes() -> None:
    instances: list[_Go2Backend] = []
    path = "/pinned/go2/scene.xml"

    def factory(model_path: object) -> _Go2Backend:
        return _Go2Backend(instances, model_path)

    callback = Go2DevelopmentProbe(path, backend_factory=factory)
    probe = _go2_probe()
    first = callback(_go2_source("target"), probe)
    second = callback(_go2_source("target * 2.0"), probe)

    assert first["status"] == second["status"] == "OK"
    assert first["observations"]["accepted_command_count"] == 1
    assert first["observations"]["simulation_time_s"] == 0.2
    assert first["observations"]["frame_position_m"] == [1.0, 2.0, 3.0]
    assert first["observations"]["frame_linear_velocity_m_s"] == [4.0, 5.0, 6.0]
    assert first["observations"]["orientation_alignment"] == 1.0
    assert first["observations"]["finite_observation_available"] is True
    assert first["observations"]["contact_available"] is False
    assert first["observations"] != second["observations"]

    assert all(item.closed for item in instances)


def test_go2_rejects_bad_probe_before_backend_creation() -> None:
    created: list[object] = []
    callback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda _path: created.append(object()),
    )
    for probe in (
        _go2_probe(arguments=[]),
        _go2_probe(horizon_s=0.0),
        _go2_probe(extra=True),
    ):
        feedback = callback("pass", probe)
        assert feedback["status"] == "ERROR"
        assert isinstance(feedback["exception"], str)
    assert created == []


def test_go2_sandbox_feedback_is_accepted_by_public_callback_boundary() -> None:
    instances: list[_Go2Backend] = []
    callback = CallbackSandbox(
        Go2DevelopmentProbe(
            "/pinned/go2/scene.xml",
            backend_factory=lambda path: _Go2Backend(instances, path),
        )
    )
    feedback = callback.run(_go2_source(), _go2_probe(horizon_s=0.1))
    assert feedback["status"] == "OK"
    assert feedback["observations"]["capability_id"] == "capability_1"


def test_go2_no_command_is_inconclusive_and_backend_failure_is_error() -> None:
    no_command = "def capability_capability_1(target, *, _sdk):\n    return target\n"
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(no_command, _go2_probe())
    assert feedback["status"] == "INCONCLUSIVE"
    assert feedback["observations"]["accepted_command_count"] == 0

    instances: list[_Go2Backend] = []

    def factory(path: object) -> _Go2Backend:
        backend = _Go2Backend(instances, path)
        backend.fail_step = True
        return backend

    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=factory,
    )(_go2_source(), _go2_probe(horizon_s=0.1))
    assert feedback["status"] == "ERROR"
    assert feedback["exception"] == "probe_execution_error"
    assert instances[0].closed


@pytest.mark.parametrize("mutation", ["crc", "width", "finite"])
def test_go2_bridge_rejects_bad_crc_width_and_nonfinite_commands(mutation: str) -> None:
    source = _go2_source()
    if mutation == "crc":
        source = source.replace("publisher.Write(command)", "command.crc += 1\n    publisher.Write(command)")
    elif mutation == "width":
        source = source.replace("publisher.Write(command)", "command.motor_cmd = command.motor_cmd[:19]\n    publisher.Write(command)")
    else:
        source = source.replace("command.crc = CRC().Crc(command)", "command.motor_cmd[0].q = float('nan')\n    command.crc = CRC().Crc(command)")
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(source, _go2_probe())
    assert feedback["status"] == "INCONCLUSIVE"
    assert feedback["observations"]["accepted_command_count"] == 0
    assert feedback["observations"]["rejected_command_count"] == 1


def test_go2_fake_sleep_advances_without_wall_delay_and_stale_clears() -> None:
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(_go2_source(), _go2_probe(horizon_s=0.3))
    assert feedback["status"] == "OK"
    assert feedback["observations"]["simulation_time_s"] == 0.2
    assert instances[0].controls[-1] == (0.0,) * 12


def test_go2_time_time_uses_monotonic_simulated_time() -> None:
    instances: list[_Go2Backend] = []
    source = _go2_source().replace(
        "time.sleep(0.2)",
        "start = time.time()\n    time.sleep(0.2)\n    elapsed = time.time() - start\n    if elapsed != 0.2:\n        raise RuntimeError(\"unexpected simulated time\")",
    )
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(source, _go2_probe())

    assert feedback["status"] == "OK"
    assert feedback["observations"]["simulation_time_s"] == 0.2


def test_go2_attribute_error_feedback_has_bounded_public_detail() -> None:
    source = "import time\ndef capability_capability_1(target, *, _sdk):\n    return time.missing()\n"
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend([], path),
    )(source, _go2_probe())

    assert feedback["status"] == "ERROR"
    assert feedback["exception"] == "attribute_error:missing"


def test_go2_execution_is_bounded_for_a_tight_loop() -> None:
    source = "def capability_capability_1(target, *, _sdk):\n    while True:\n        pass\n"
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(source, _go2_probe())
    assert feedback["status"] == "ERROR"
    assert feedback["exception"] == "execution_limit"
    assert instances[0].closed


def test_go2_transport_keeps_only_latest_command() -> None:
    source = _go2_source().replace(
        "publisher.Write(command)\n    time.sleep",
        "publisher.Write(command)\n    command.motor_cmd[0].q = 2.0\n    command.crc = CRC().Crc(command)\n    publisher.Write(command)\n    time.sleep",
    )
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(source, _go2_probe())
    assert feedback["status"] == "OK"
    assert feedback["observations"]["accepted_command_count"] == 1
    assert feedback["observations"]["joint_positions_rad"][0] == 2.0


def test_go2_lowcmd_idl_type_is_not_a_default_constructor() -> None:
    source = _go2_source().replace(
        "command = unitree_go_msg_dds__LowCmd_()",
        "command = LowCmd_()",
    )
    instances: list[_Go2Backend] = []
    feedback = Go2DevelopmentProbe(
        "/pinned/go2/scene.xml",
        backend_factory=lambda path: _Go2Backend(instances, path),
    )(source, _go2_probe())
    assert feedback["status"] == "ERROR"
    assert feedback["exception"] == "probe_execution_error"


def _so_probe(**overrides: Any) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "probe_id": "probe-1",
        "capability_id": "capability-1",
        "target_position": [0.0, -180.0, 180.0, 0.0, 90.0, 50.0],
        "duration_s": 0.25,
    }
    probe.update(overrides)
    return probe


class _SOBackend:
    def __init__(self, calls: list[tuple[str, Any]], path: object, direction: bool) -> None:
        self.calls = calls
        self.path = path
        self.direction = direction
        self.state_calls = 0
        self.closed = False
        self.named_qpos = {name: float(index) for index, name in enumerate(SO_ARM101_JOINT_ORDER)}
        self.named_ctrl = {name: float(index) + 0.5 for index, name in enumerate(SO_ARM101_JOINT_ORDER)}

    def reset(self) -> None:
        self.calls.append(("reset", None))

    def set_goal_ticks(self, values: dict[int, int]) -> None:
        self.calls.append(("set_goal_ticks", dict(values)))

    def step(self, seconds: float) -> None:
        self.calls.append(("step", seconds))

    def state(self) -> dict[str, Any]:
        self.state_calls += 1
        return {
            "named_qpos": dict(self.named_qpos),
            "named_ctrl": dict(self.named_ctrl),
            "gripper_control_range": [-0.2, 1.7],
        }

    def close(self) -> None:
        self.calls.append(("close", None))
        self.closed = True


def test_so_converts_public_positions_in_order_and_closes_backend() -> None:
    calls: list[tuple[str, Any]] = []
    instances: list[_SOBackend] = []

    def factory(path: object, *, gripper_tick_increases_qpos: bool) -> _SOBackend:
        backend = _SOBackend(calls, path, gripper_tick_increases_qpos)
        instances.append(backend)
        return backend

    callback = SOArm101DevelopmentProbe(
        "/pinned/so101/model.xml",
        False,
        backend_factory=factory,
    )
    feedback = callback("raise RuntimeError('must not run')", _so_probe())

    assert feedback["status"] == "OK"
    assert feedback["observations"]["probe_id"] == "probe-1"
    assert feedback["observations"]["capability_id"] == "capability-1"
    assert feedback["observations"]["joint_positions_rad"] == [float(index) for index in range(6)]
    assert feedback["observations"]["named_joint_positions_rad"]["gripper"] == 5.0
    assert feedback["observations"]["named_control_positions_rad"]["wrist_roll"] == 4.5
    assert feedback["observations"]["gripper_control_range_rad"] == [-0.2, 1.7]

    backend = instances[0]
    assert backend.path == "/pinned/so101/model.xml"
    assert backend.direction is False
    assert backend.closed
    assert calls == [
        ("reset", None),
        ("set_goal_ticks", public_positions_to_ticks(_so_probe()["target_position"])),
        ("step", 0.25),
        ("close", None),
    ]


def test_so_tick_conversion_and_bad_inputs_are_bounded() -> None:
    assert public_positions_to_ticks([0.0, -180.0, 180.0, 0.0, 90.0, 50.0]) == {
        SO_ARM101_MOTOR_IDS[0]: 2047,
        SO_ARM101_MOTOR_IDS[1]: 0,
        SO_ARM101_MOTOR_IDS[2]: 4095,
        SO_ARM101_MOTOR_IDS[3]: 2047,
        SO_ARM101_MOTOR_IDS[4]: 3071,
        SO_ARM101_MOTOR_IDS[5]: 2047,
    }

    callback = SOArm101DevelopmentProbe(
        "/pinned/so101/model.xml",
        True,
        backend_factory=lambda *_args, **_kwargs: pytest.fail("backend must not be created"),
    )
    for probe in (
        _so_probe(target_position=[0.0] * 5),
        _so_probe(target_position=[0.0, 0.0, 0.0, 0.0, 0.0, float("inf")]),
        _so_probe(duration_s=SO_ARM101_MAX_DURATION_S + 0.01),
        _so_probe(extra=True),
    ):
        feedback = callback("pass", probe)
        assert feedback["status"] == "ERROR"
        assert feedback["observations"] == {}
        assert isinstance(feedback["exception"], str)
