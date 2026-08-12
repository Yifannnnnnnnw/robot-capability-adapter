"""Source-coupled public Go2 development probes.

Each call executes the submitted capability source against a small in-memory
SDK facade.  SDK commands cross the same DDS-to-physics bridge used by the
real route, and ``time.sleep`` advances that bridge without sleeping the host
process.
"""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import math
import inspect
import sys
import types
import zlib
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .bridge import (
    ACTIVE_MOTOR_NAMES,
    DDS_MOTOR_SLOT_COUNT,
    Go2DDSMuJoCoBridge,
    IMUStateFrame,
    LowStateFrame,
    LOWCMD_TOPIC,
    LOWSTATE_TOPIC,
    MotorStateFrame,
    SPORTMODESTATE_TOPIC,
    SportModeStateFrame,
    MuJoCoGo2Backend,
)


GO2_MOTOR_ORDER = tuple(ACTIVE_MOTOR_NAMES)
GO2_JOINT_ORDER = tuple(f"{name}_joint" for name in GO2_MOTOR_ORDER)
GO2_PROBE_FIELDS = ("probe_id", "capability_id", "arguments", "horizon_s")
GO2_MAX_STEPS = 2_000
GO2_MAX_ID_LENGTH = 128
GO2_SOURCE_MAX_CHARS = 200_000
GO2_MAX_EXECUTION_EVENTS = 20_000

GO2_OBSERVATION_NAMES = (
    "probe_id",
    "capability_id",
    "source_hash",
    "accepted_command_count",
    "rejected_command_count",
    "state_read_count",
    "feedback_cycle_count",
    "simulation_time_s",
    "joint_positions_rad",
    "joint_velocities_rad_s",
    "initial_frame_position_m",
    "final_frame_position_m",
    "frame_displacement_m",
    "frame_displacement_norm_m",
    "frame_position_m",
    "frame_linear_velocity_m_s",
    "terminal_velocity_m_s",
    "orientation_alignment",
    "terminal_orientation_alignment",
    "finite_observation_available",
    "contact_available",
)

GO2_DEVELOPMENT_PROBE_CONTRACT: dict[str, Any] = {
    "contract_id": "unitree-go2-development-probe",
    "version": "3.0.0",
    "robot": "unitree-go2",
    "mode": "source_coupled_sdk_probe",
    "backend": "bridge",
    "capability_source": {
        "syntax_check": True,
        "execution": True,
        "imports": ["math", "time", "unitree_sdk2py"],
        "max_chars": GO2_SOURCE_MAX_CHARS,
    },
    "probe_fields": list(GO2_PROBE_FIELDS),
    "fields": {
        "probe_id": {"type": "string", "max_length": GO2_MAX_ID_LENGTH},
        "capability_id": {"type": "string", "max_length": GO2_MAX_ID_LENGTH},
        "arguments": {"type": "object"},
        "horizon_s": {"type": "number", "exclusive_min": 0.0},
    },
    "motor_order": list(GO2_MOTOR_ORDER),
    "joint_order": list(GO2_JOINT_ORDER),
    "observations": {
        "names": list(GO2_OBSERVATION_NAMES),
        "units": {
            "probe_id": "identifier",
            "capability_id": "identifier",
            "source_hash": "sha256",
            "accepted_command_count": "commands",
            "rejected_command_count": "commands",
            "state_read_count": "reads",
            "feedback_cycle_count": "cycles",
            "simulation_time_s": "s",
            "joint_positions_rad": "rad",
            "joint_velocities_rad_s": "rad/s",
            "initial_frame_position_m": "m",
            "final_frame_position_m": "m",
            "frame_displacement_m": "m",
            "frame_displacement_norm_m": "m",
            "frame_position_m": "m",
            "frame_linear_velocity_m_s": "m/s",
            "terminal_velocity_m_s": "m/s",
            "orientation_alignment": "unitless",
            "terminal_orientation_alignment": "unitless",
            "finite_observation_available": "boolean",
            "contact_available": "boolean",
        },
    },
    "limits": {
        "max_steps": GO2_MAX_STEPS,
        "max_id_length": GO2_MAX_ID_LENGTH,
    },
}

GO2_PROBE_CONTRACT = GO2_DEVELOPMENT_PROBE_CONTRACT
PUBLIC_GO2_DEVELOPMENT_PROBE_CONTRACT = GO2_DEVELOPMENT_PROBE_CONTRACT

_FORBIDDEN_PUBLIC_TERMS = (
    "private",
    "criterion",
    "validation",
    "blue line",
    "suite",
    "threshold",
    "mujoco",
    "translation",
    "simulator",
    "raw_state",
    "score",
    "target_error",
)


class Go2DevelopmentProbeError(ValueError):
    """Internal input or execution error converted to public feedback."""


class _TrackedFloat(float):
    """Numeric SDK state value that retains generic state-dependence taint."""

    def __new__(cls, value: object, state_token: int) -> "_TrackedFloat":
        result = float.__new__(cls, float(value))
        result._state_token = state_token
        return result

    @property
    def state_dependent(self) -> bool:
        return True

    def _combine(self, other: object, operation: Callable[[float, float], float]) -> "_TrackedFloat":
        other_value = float(other)
        return _TrackedFloat(operation(float(self), other_value), self._state_token)

    def __add__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: left + right)

    def __radd__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: right + left)

    def __sub__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: left - right)

    def __rsub__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: right - left)

    def __mul__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: left * right)

    def __rmul__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: right * left)

    def __truediv__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: left / right)

    def __rtruediv__(self, other: object) -> "_TrackedFloat":
        return self._combine(other, lambda left, right: right / left)

    def __neg__(self) -> "_TrackedFloat":
        return _TrackedFloat(-float(self), self._state_token)

    def __pos__(self) -> "_TrackedFloat":
        return _TrackedFloat(float(self), self._state_token)


def get_go2_development_probe_contract() -> dict[str, Any]:
    return copy.deepcopy(GO2_DEVELOPMENT_PROBE_CONTRACT)


def _error(
    summary: str,
    exception: str,
    *,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "summary": summary,
        "observations": dict(observations or {}),
        "exception": exception,
    }


def _feedback(
    status: str,
    summary: str,
    observations: Mapping[str, Any],
    exception: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "observations": dict(observations),
        "exception": exception,
    }


def _parse_source(capability_source: object) -> object:
    if not isinstance(capability_source, str) or not capability_source.strip():
        raise Go2DevelopmentProbeError("source_required")
    if len(capability_source.encode("utf-8")) > GO2_SOURCE_MAX_CHARS:
        raise Go2DevelopmentProbeError("source_too_large")
    try:
        tree = ast.parse(capability_source, filename="<capability.py>", mode="exec")
        return compile(tree, "<capability.py>", "exec")
    except (SyntaxError, ValueError, TypeError, UnicodeError) as exc:
        raise Go2DevelopmentProbeError("source_syntax_error") from exc


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > GO2_MAX_ID_LENGTH:
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    lowered = value.lower()
    if any(term in lowered for term in _FORBIDDEN_PUBLIC_TERMS):
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    return value


def _probe_values(probe: Mapping[str, Any]) -> tuple[str, str, dict[str, Any], float]:
    if not isinstance(probe, Mapping):
        raise Go2DevelopmentProbeError("probe_object_required")
    if set(probe) != set(GO2_PROBE_FIELDS):
        raise Go2DevelopmentProbeError("probe_fields_invalid")
    probe_id = _identifier(probe["probe_id"], "probe_id")
    capability_id = _identifier(probe["capability_id"], "capability_id")
    arguments = probe["arguments"]
    if not isinstance(arguments, Mapping):
        raise Go2DevelopmentProbeError("arguments_invalid")
    try:
        arguments_copy = copy.deepcopy(dict(arguments))
    except Exception as exc:
        raise Go2DevelopmentProbeError("arguments_invalid") from exc
    horizon = probe["horizon_s"]
    if isinstance(horizon, bool) or not isinstance(horizon, Real):
        raise Go2DevelopmentProbeError("horizon_invalid")
    horizon_s = float(horizon)
    if not math.isfinite(horizon_s) or horizon_s <= 0.0:
        raise Go2DevelopmentProbeError("horizon_invalid")
    return probe_id, capability_id, arguments_copy, horizon_s


class _FakeMotorCommand:
    def __init__(self) -> None:
        self.mode = 0
        self.q = 0.0
        self.dq = 0.0
        self.kp = 0.0
        self.kd = 0.0
        self.tau = 0.0


class _FakeLowCmd:
    def __init__(self) -> None:
        self.motor_cmd = [_FakeMotorCommand() for _ in range(DDS_MOTOR_SLOT_COUNT)]
        self.crc = 0


class _FakeLowCmdIDL:
    def __new__(cls) -> object:
        raise TypeError("LowCmd_ is an IDL type; use unitree_go_msg_dds__LowCmd_()")


class _FakeMotorState:
    def __init__(self) -> None:
        self.q = 0.0
        self.dq = 0.0
        self.tau_est = 0.0


class _FakeIMUState:
    def __init__(self) -> None:
        self.quaternion = [1.0, 0.0, 0.0, 0.0]
        self.gyroscope = [0.0, 0.0, 0.0]
        self.accelerometer = [0.0, 0.0, 0.0]


class _FakeLowState:
    def __init__(self) -> None:
        self.motor_state = [_FakeMotorState() for _ in range(DDS_MOTOR_SLOT_COUNT)]
        self.imu_state = _FakeIMUState()


class _FakeSportModeState:
    def __init__(self) -> None:
        self.position = [0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0]


def _crc_payload(message: object) -> bytes:
    slots = getattr(message, "motor_cmd")
    values = []
    for slot in slots:
        values.append(tuple(getattr(slot, field) for field in ("mode", "q", "dq", "kp", "kd", "tau")))
    return repr(tuple(values)).encode("utf-8")


class _FakeCRC:
    def Crc(self, message: object) -> int:
        return zlib.crc32(_crc_payload(message)) & 0xFFFFFFFF


class _InMemoryGo2Transport:
    def __init__(self) -> None:
        self._lowcmd_type = _FakeLowCmd
        self._crc = _FakeCRC()
        self._latest_command: object | None = None
        self._lowstate_callbacks: list[Callable[[object], None]] = []
        self._sport_callbacks: list[Callable[[object], None]] = []
        self._latest_lowstate: object | None = None
        self._latest_sportstate: object | None = None
        self._started = False
        self._closed = False
        self.published_command_count = 0
        self.state_read_count = 0
        self._state_read_epoch = 0
        self._pending_command_read_epoch = 0
        self._last_feedback_read_epoch = 0
        self.feedback_cycle_count = 0
        self._pending_command_state_dependent = False

    def start(self) -> None:
        if self._started or self._closed:
            raise Go2DevelopmentProbeError("transport_lifecycle_error")
        self._started = True

    def take_lowcmd(self) -> object | None:
        message = self._latest_command
        self._latest_command = None
        return message

    def is_lowcmd_type(self, message: object) -> bool:
        return self._started and type(message) is self._lowcmd_type

    def compute_crc(self, message: object) -> int:
        if not self._started:
            raise Go2DevelopmentProbeError("transport_not_started")
        return self._crc.Crc(message)

    def publish_lowstate(self, state: object) -> None:
        message = _FakeLowState()
        for target, source in zip(message.motor_state, state.motor_state):
            target.q = source.q
            target.dq = source.dq
            target.tau_est = source.tau_est
        message.imu_state.quaternion[:] = state.imu_state.quaternion
        message.imu_state.gyroscope[:] = state.imu_state.gyroscope
        message.imu_state.accelerometer[:] = state.imu_state.accelerometer
        self._latest_lowstate = message
        for callback in tuple(self._lowstate_callbacks):
            callback(message)

    def publish_sportmodestate(self, state: object) -> None:
        message = _FakeSportModeState()
        message.position[:] = state.position
        message.velocity[:] = state.velocity
        self._latest_sportstate = message
        for callback in tuple(self._sport_callbacks):
            callback(message)

    def close(self) -> None:
        self._latest_command = None
        self._closed = True
        self._started = False

    def queue_command(self, message: object) -> None:
        if not self._started:
            raise Go2DevelopmentProbeError("transport_not_started")
        self.published_command_count += 1
        self._pending_command_read_epoch = self._state_read_epoch
        slots = getattr(message, "motor_cmd", ())
        self._pending_command_state_dependent = any(
            bool(getattr(getattr(slot, field, None), "state_dependent", False))
            for slot in tuple(slots)[: len(GO2_MOTOR_ORDER)]
            for field in ("q", "dq", "kp", "kd", "tau")
        )
        self._latest_command = message

    def record_command_result(self, result: Mapping[str, Any]) -> None:
        if result.get("accepted") is True:
            read_epoch = self._pending_command_read_epoch
            if (
                self._pending_command_state_dependent
                and read_epoch > 0
                and read_epoch > self._last_feedback_read_epoch
            ):
                self.feedback_cycle_count += 1
                self._last_feedback_read_epoch = read_epoch
        self._pending_command_read_epoch = 0
        self._pending_command_state_dependent = False

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> None:
        if topic == LOWSTATE_TOPIC:
            self._lowstate_callbacks.append(callback)
        elif topic == SPORTMODESTATE_TOPIC:
            self._sport_callbacks.append(callback)

    def read(self, topic: str) -> object | None:
        if topic == LOWSTATE_TOPIC:
            message = self._latest_lowstate
        elif topic == SPORTMODESTATE_TOPIC:
            message = self._latest_sportstate
        else:
            message = None
        if message is not None:
            self.state_read_count += 1
            self._state_read_epoch += 1
            state_token = self._state_read_epoch
            for motor_state in getattr(message, "motor_state", ()):
                for field in ("q", "dq", "tau_est"):
                    setattr(
                        motor_state,
                        field,
                        _TrackedFloat(getattr(motor_state, field), state_token),
                    )
            imu_state = getattr(message, "imu_state", None)
            if imu_state is not None:
                for field in ("quaternion", "gyroscope", "accelerometer"):
                    values = getattr(imu_state, field, ())
                    setattr(
                        imu_state,
                        field,
                        [_TrackedFloat(value, state_token) for value in values],
                    )
            for field in ("position", "velocity"):
                if hasattr(message, field):
                    setattr(
                        message,
                        field,
                        [_TrackedFloat(value, state_token) for value in getattr(message, field)],
                    )
        return message

    def seed(self, sensors: object) -> None:
        self.publish_lowstate(
            LowStateFrame(
                motor_state=tuple(
                    MotorStateFrame(sensors.q[index], sensors.dq[index], sensors.actuator_force[index])
                    for index in range(len(sensors.q))
                ) + tuple(MotorStateFrame() for _ in range(DDS_MOTOR_SLOT_COUNT - len(sensors.q))),
                imu_state=IMUStateFrame(
                    tuple(sensors.imu_quaternion),
                    tuple(sensors.imu_gyroscope),
                    tuple(sensors.imu_accelerometer),
                ),
            )
        )
        self.publish_sportmodestate(
            SportModeStateFrame(tuple(sensors.frame_position), tuple(sensors.frame_linear_velocity))
        )


class _FakeChannelPublisher:
    def __init__(self, transport: _InMemoryGo2Transport, topic: str, message_type: type[object]) -> None:
        self._transport = transport
        self._topic = topic
        self._message_type = message_type
        self._initialized = False

    def Init(self) -> None:
        self._initialized = True

    def Write(self, message: object) -> None:
        if not self._initialized:
            raise Go2DevelopmentProbeError("publisher_not_initialized")
        if self._topic == LOWCMD_TOPIC:
            self._transport.queue_command(message)


class _FakeChannelSubscriber:
    def __init__(self, transport: _InMemoryGo2Transport, topic: str, message_type: type[object]) -> None:
        self._transport = transport
        self._topic = topic
        self._message_type = message_type
        self._initialized = False

    def Init(self, callback: Callable[[object], None] | None = None, _queue_size: int = 10) -> None:
        if callback is not None:
            self._transport.subscribe(self._topic, callback)
        self._initialized = True

    def Read(self) -> object | None:
        if not self._initialized:
            raise Go2DevelopmentProbeError("subscriber_not_initialized")
        return self._transport.read(self._topic)


class _FakeSDKFacade:
    ChannelPublisher: Any
    ChannelSubscriber: Any
    LowCmd_: Any
    LowState_: Any
    SportModeState_: Any
    unitree_go_msg_dds__LowCmd_: Any
    CRC: Any


def _fake_sdk_modules(transport: _InMemoryGo2Transport) -> tuple[dict[str, types.ModuleType], _FakeSDKFacade]:
    channel = types.ModuleType("unitree_sdk2py.core.channel")
    channel.ChannelPublisher = lambda topic, message_type: _FakeChannelPublisher(transport, topic, message_type)
    channel.ChannelSubscriber = lambda topic, message_type: _FakeChannelSubscriber(transport, topic, message_type)

    defaults = types.ModuleType("unitree_sdk2py.idl.default")
    defaults.unitree_go_msg_dds__LowCmd_ = _FakeLowCmd

    dds = types.ModuleType("unitree_sdk2py.idl.unitree_go.msg.dds_")
    dds.LowCmd_ = _FakeLowCmdIDL
    dds.LowState_ = _FakeLowState
    dds.SportModeState_ = _FakeSportModeState

    crc_module = types.ModuleType("unitree_sdk2py.utils.crc")
    crc_module.CRC = _FakeCRC

    modules = {
        "unitree_sdk2py.core.channel": channel,
        "unitree_sdk2py.idl.default": defaults,
        "unitree_sdk2py.idl.unitree_go.msg.dds_": dds,
        "unitree_sdk2py.utils.crc": crc_module,
    }
    facade = _FakeSDKFacade()
    facade.ChannelPublisher = channel.ChannelPublisher
    facade.ChannelSubscriber = channel.ChannelSubscriber
    facade.LowCmd_ = _FakeLowCmdIDL
    facade.LowState_ = _FakeLowState
    facade.SportModeState_ = _FakeSportModeState
    facade.unitree_go_msg_dds__LowCmd_ = _FakeLowCmd
    facade.CRC = _FakeCRC
    return modules, facade


class _SimulationClock:
    def __init__(self, bridge: Go2DDSMuJoCoBridge, timestep: float, max_steps: int) -> None:
        self._bridge = bridge
        self._timestep = timestep
        self._max_steps = max_steps
        self.steps = 0
        self.accepted_commands = 0
        self.rejected_commands = 0

    def time(self) -> float:
        """Return monotonic simulated time without consulting the host clock."""

        return float(self.steps * self._timestep)

    def sleep(self, seconds: object) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, Real):
            raise TypeError("sleep duration must be numeric")
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("sleep duration must be finite and non-negative")
        if duration == 0.0:
            return
        requested_steps = max(1, math.ceil(duration / self._timestep - 1e-12))
        remaining = self._max_steps - self.steps
        for _ in range(min(requested_steps, remaining)):
            result = self._bridge.step()
            self.accepted_commands = int(result["accepted_commands"])
            self.rejected_commands = int(result["rejected_commands"])
            record_result = getattr(self._bridge.transport, "record_command_result", None)
            if callable(record_result):
                record_result(result)
            self.steps += 1


def _safe_execution_globals(
    transport: _InMemoryGo2Transport,
    clock: _SimulationClock,
) -> dict[str, Any]:
    modules, facade = _fake_sdk_modules(transport)
    time_module = types.ModuleType("time")
    time_module.sleep = clock.sleep
    time_module.time = clock.time
    time_module.monotonic = clock.time
    time_module.perf_counter = clock.time
    time_module.process_time = clock.time

    def safe_import(name: str, globals_: object = None, locals_: object = None, fromlist: tuple[str, ...] = (), level: int = 0) -> object:
        del globals_, locals_
        if level:
            raise ImportError("relative imports are unavailable")
        if name == "math":
            return math
        if name == "time":
            return time_module
        if name in modules:
            return modules[name]
        if name.startswith("unitree_sdk2py"):
            raise ImportError("unsupported SDK import")
        raise ImportError(f"unsupported import: {name}")

    builtin_names = (
        "__build_class__", "abs", "all", "any", "ArithmeticError", "AttributeError",
        "bool", "dict", "enumerate", "Exception", "float", "int", "isinstance",
        "KeyError", "len", "list", "max", "min", "NameError", "object", "OverflowError",
        "print", "range", "RuntimeError", "set", "sorted", "str", "sum", "tuple", "TypeError",
        "ValueError", "zip",
    )
    safe_builtins = {name: getattr(builtins, name) for name in builtin_names}
    safe_builtins["__import__"] = safe_import
    return {
        "__name__": "capability_probe",
        "__builtins__": safe_builtins,
        "_sdk": facade,
    }


def _state_vector(state: object, name: str, length: int) -> list[float]:
    value = getattr(state, name)
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != length:
        raise Go2DevelopmentProbeError("observation_shape_invalid")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real) or not math.isfinite(float(item)):
            raise Go2DevelopmentProbeError("observation_value_invalid")
        result.append(float(item))
    return result


def _observations(
    backend: object,
    clock: _SimulationClock,
    transport: _InMemoryGo2Transport,
    *,
    probe_id: str,
    capability_id: str,
    source_hash: str,
    initial_frame_position_m: Sequence[float],
) -> dict[str, Any]:
    simulation_time_s = float(getattr(backend, "simulation_time"))
    state = backend.sensors()
    quaternion = _state_vector(state, "imu_quaternion", 4)
    norm = math.sqrt(sum(value * value for value in quaternion))
    if norm == 0.0 or not math.isfinite(norm):
        raise Go2DevelopmentProbeError("observation_value_invalid")
    _, x, y, z = (value / norm for value in quaternion)
    orientation_alignment = 1.0 - 2.0 * (x * x + y * y)
    final_frame_position_m = _state_vector(state, "frame_position", 3)
    frame_linear_velocity_m_s = _state_vector(state, "frame_linear_velocity", 3)
    initial_position = _state_vector(
        SimpleNamespace(frame_position=initial_frame_position_m),
        "frame_position",
        3,
    )
    frame_displacement_m = [
        final - initial
        for final, initial in zip(final_frame_position_m, initial_position)
    ]
    return {
        "probe_id": probe_id,
        "capability_id": capability_id,
        "source_hash": source_hash,
        "accepted_command_count": clock.accepted_commands,
        "rejected_command_count": clock.rejected_commands,
        "state_read_count": transport.state_read_count,
        "feedback_cycle_count": transport.feedback_cycle_count,
        "simulation_time_s": simulation_time_s,
        "joint_positions_rad": _state_vector(state, "q", 12),
        "joint_velocities_rad_s": _state_vector(state, "dq", 12),
        "initial_frame_position_m": initial_position,
        "final_frame_position_m": final_frame_position_m,
        "frame_displacement_m": frame_displacement_m,
        "frame_displacement_norm_m": math.sqrt(sum(value * value for value in frame_displacement_m)),
        "frame_position_m": final_frame_position_m,
        "frame_linear_velocity_m_s": frame_linear_velocity_m_s,
        "terminal_velocity_m_s": frame_linear_velocity_m_s,
        "orientation_alignment": float(orientation_alignment),
        "terminal_orientation_alignment": float(orientation_alignment),
        "finite_observation_available": True,
        "contact_available": False,
    }


def _healthy_terminal_observation(observations: Mapping[str, Any]) -> bool:
    velocity = observations.get("terminal_velocity_m_s")
    if (
        not isinstance(velocity, list)
        or len(velocity) != 3
        or any(isinstance(value, bool) or not isinstance(value, Real) for value in velocity)
    ):
        return False
    terminal_speed = math.sqrt(sum(float(value) * float(value) for value in velocity))
    orientation = observations.get("terminal_orientation_alignment")
    return bool(
        observations.get("finite_observation_available") is True
        and isinstance(observations.get("state_read_count"), int)
        and observations["state_read_count"] >= 2
        and isinstance(observations.get("feedback_cycle_count"), int)
        and observations["feedback_cycle_count"] >= 2
        and isinstance(observations.get("accepted_command_count"), int)
        and observations["accepted_command_count"] >= 2
        and isinstance(orientation, Real)
        and not isinstance(orientation, bool)
        and math.isfinite(float(orientation))
        and float(orientation) >= 0.0
        and math.isfinite(terminal_speed)
        and terminal_speed <= 1.0
    )


class Go2DevelopmentProbe:
    """Execute one candidate capability against a fresh bridged Go2 scene."""

    contract = GO2_DEVELOPMENT_PROBE_CONTRACT

    def __init__(
        self,
        model_path: str | Path,
        *,
        backend_factory: Callable[[str | Path], Any] | None = None,
    ) -> None:
        self.model_path = model_path
        self._backend_factory = backend_factory or MuJoCoGo2Backend

    def __call__(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        return self.run(capability_source, probe)

    def run(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        try:
            code = _parse_source(capability_source)
            probe_id, capability_id, arguments, horizon_s = _probe_values(probe)
        except Go2DevelopmentProbeError as exc:
            return _error("Go2 probe input rejected.", str(exc))
        except Exception:
            return _error("Go2 probe input rejected.", "probe_input_error")

        backend: Any | None = None
        bridge: Go2DDSMuJoCoBridge | None = None
        transport: _InMemoryGo2Transport | None = None
        source_hash = "sha256:" + hashlib.sha256(capability_source.encode("utf-8")).hexdigest()
        observations = {
            "probe_id": probe_id,
            "capability_id": capability_id,
            "source_hash": source_hash,
        }
        try:
            backend = self._backend_factory(self.model_path)
            transport = _InMemoryGo2Transport()
            bridge = Go2DDSMuJoCoBridge(backend, transport)
            timestep = float(backend.timestep)
            if horizon_s >= GO2_MAX_STEPS * timestep:
                max_steps = GO2_MAX_STEPS
            else:
                max_steps = max(0, int(horizon_s / timestep + 1e-12))
            clock = _SimulationClock(bridge, timestep, max_steps)
            bridge.start()
            bridge.reset()
            initial_sensors = backend.sensors()
            initial_frame_position_m = _state_vector(initial_sensors, "frame_position", 3)
            transport.seed(initial_sensors)
            namespace = _safe_execution_globals(transport, clock)
            previous_trace = sys.gettrace()
            events = 0

            def execution_trace(frame: types.FrameType, event: str, arg: object) -> Any:
                nonlocal events
                del arg
                if frame.f_code.co_filename == "<capability.py>" and event in {"line", "call"}:
                    events += 1
                    if events > GO2_MAX_EXECUTION_EVENTS:
                        raise Go2DevelopmentProbeError("execution_limit")
                return execution_trace

            sys.settrace(execution_trace)
            try:
                exec(code, namespace, namespace)
                function_name = f"capability_{capability_id}"
                function = namespace.get(function_name)
                if not callable(function):
                    raise Go2DevelopmentProbeError("capability_function_missing")
                parameters = inspect.signature(function).parameters
                call_arguments = {}
                for public_name, value in arguments.items():
                    generated_name = f"arg_{public_name}"
                    if generated_name in parameters and public_name not in parameters:
                        call_arguments[generated_name] = value
                    else:
                        call_arguments[public_name] = value
                function(**call_arguments, _sdk=namespace["_sdk"])
            finally:
                sys.settrace(previous_trace)
            final_observations = _observations(
                backend,
                clock,
                transport,
                probe_id=probe_id,
                capability_id=capability_id,
                source_hash=source_hash,
                initial_frame_position_m=initial_frame_position_m,
            )
            observations = final_observations
            if (
                float(final_observations["simulation_time_s"]) > 0.0
                and _healthy_terminal_observation(final_observations)
            ):
                return _feedback(
                    "OK",
                    "Go2 probe completed with repeated SDK feedback.",
                    final_observations,
                )
            return _feedback(
                "INCONCLUSIVE",
                "Go2 probe produced bounded public feedback without a repeated stable loop.",
                final_observations,
            )
        except Go2DevelopmentProbeError as exc:
            return _error("Go2 probe execution failed.", str(exc), observations=observations)
        except AttributeError as exc:
            name = getattr(exc, "name", None)
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                or len(name) > GO2_MAX_ID_LENGTH
                or any(term in name.lower() for term in _FORBIDDEN_PUBLIC_TERMS)
            ):
                exception = "attribute_error"
            else:
                exception = f"attribute_error:{name}"
            return _error("Go2 probe execution failed.", exception, observations=observations)
        except Exception:
            return _error("Go2 probe execution failed.", "probe_execution_error", observations=observations)
        finally:
            if bridge is not None:
                try:
                    bridge.close()
                except Exception:
                    pass
            elif transport is not None:
                transport.close()


Go2DevelopmentSandbox = Go2DevelopmentProbe


def create_go2_development_probe(
    model_path: str | Path,
    *,
    backend_factory: Callable[[str | Path], Any] | None = None,
) -> Go2DevelopmentProbe:
    return Go2DevelopmentProbe(model_path, backend_factory=backend_factory)


def create_go2_development_sandbox(
    model_path: str | Path,
    *,
    backend_factory: Callable[[str | Path], Any] | None = None,
) -> Go2DevelopmentProbe:
    return create_go2_development_probe(model_path, backend_factory=backend_factory)


go2_development_probe_callback = create_go2_development_probe


__all__ = [
    "GO2_DEVELOPMENT_PROBE_CONTRACT",
    "GO2_JOINT_ORDER",
    "GO2_MAX_STEPS",
    "GO2_MOTOR_ORDER",
    "GO2_OBSERVATION_NAMES",
    "GO2_PROBE_CONTRACT",
    "GO2_PROBE_FIELDS",
    "Go2DevelopmentProbe",
    "Go2DevelopmentProbeError",
    "Go2DevelopmentSandbox",
    "PUBLIC_GO2_DEVELOPMENT_PROBE_CONTRACT",
    "create_go2_development_probe",
    "create_go2_development_sandbox",
    "get_go2_development_probe_contract",
    "go2_development_probe_callback",
]
