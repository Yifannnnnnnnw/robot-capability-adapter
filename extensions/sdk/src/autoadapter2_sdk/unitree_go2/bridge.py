"""SDK-extension-private SDK2 DDS to MuJoCo bridge for the pinned Go2 route.

The bridge contains transport validation and mechanical field mapping only.  It
has no pose, gait, stand/sit/move, IK, trajectory, balance, or task behavior.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Protocol


ACTIVE_MOTOR_COUNT = 12
DDS_MOTOR_SLOT_COUNT = 20
ACTIVE_MODE = 0x01
STALE_LIMIT_SIMULATION_S = 0.100
LOWCMD_HEAD = (0xFE, 0xEF)
LOWCMD_LEVEL_FLAG = 0xFF
LOWCMD_GPIO = 0

LOWCMD_TOPIC = "rt/lowcmd"
LOWSTATE_TOPIC = "rt/lowstate"
SPORTMODESTATE_TOPIC = "rt/sportmodestate"

ACTIVE_MOTOR_NAMES = (
    "FR_hip",
    "FR_thigh",
    "FR_calf",
    "FL_hip",
    "FL_thigh",
    "FL_calf",
    "RR_hip",
    "RR_thigh",
    "RR_calf",
    "RL_hip",
    "RL_thigh",
    "RL_calf",
)
ACTIVE_JOINT_NAMES = tuple(f"{name}_joint" for name in ACTIVE_MOTOR_NAMES)
INACTIVE_SAFE_FIELDS = {
    "mode": ACTIVE_MODE,
    "q": 2.146e9,
    "dq": 16000.0,
    "kp": 0.0,
    "kd": 0.0,
    "tau": 0.0,
}


class Go2BridgeError(RuntimeError):
    """Base error for a lifecycle or bridge-contract violation."""


class LowCmdRejected(Go2BridgeError):
    """A LowCmd failed the frozen fail-closed validation boundary."""


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if name not in value:
            raise LowCmdRejected(f"missing field {name!r}")
        return value[name]
    try:
        return getattr(value, name)
    except AttributeError as exc:
        raise LowCmdRejected(f"missing field {name!r}") from exc


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise LowCmdRejected(f"{label} must be a real number")
    number = float(value)
    if not math.isfinite(number):
        raise LowCmdRejected(f"{label} must be finite")
    return number


def _finite_vector(values: Sequence[Real], length: int, label: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise Go2BridgeError(f"{label} must be a numeric sequence")
    try:
        normalized = tuple(values)
    except TypeError as exc:
        raise Go2BridgeError(f"{label} must be a numeric sequence") from exc
    if len(normalized) != length:
        raise Go2BridgeError(f"{label} must contain exactly {length} values")
    result: list[float] = []
    for index, value in enumerate(normalized):
        try:
            result.append(_finite(value, f"{label}[{index}]"))
        except LowCmdRejected as exc:
            raise Go2BridgeError(str(exc)) from exc
    return tuple(result)


@dataclass(frozen=True)
class MuJoCoSensorFrame:
    q: Sequence[Real]
    dq: Sequence[Real]
    actuator_force: Sequence[Real]
    imu_quaternion: Sequence[Real]
    imu_gyroscope: Sequence[Real]
    imu_accelerometer: Sequence[Real]
    frame_position: Sequence[Real]
    frame_linear_velocity: Sequence[Real]

    def __post_init__(self) -> None:
        object.__setattr__(self, "q", _finite_vector(self.q, 12, "q"))
        object.__setattr__(self, "dq", _finite_vector(self.dq, 12, "dq"))
        object.__setattr__(self, "actuator_force", _finite_vector(self.actuator_force, 12, "actuator_force"))
        object.__setattr__(self, "imu_quaternion", _finite_vector(self.imu_quaternion, 4, "imu_quaternion"))
        object.__setattr__(self, "imu_gyroscope", _finite_vector(self.imu_gyroscope, 3, "imu_gyroscope"))
        object.__setattr__(self, "imu_accelerometer", _finite_vector(self.imu_accelerometer, 3, "imu_accelerometer"))
        object.__setattr__(self, "frame_position", _finite_vector(self.frame_position, 3, "frame_position"))
        object.__setattr__(self, "frame_linear_velocity", _finite_vector(self.frame_linear_velocity, 3, "frame_linear_velocity"))


@dataclass(frozen=True)
class MotorStateFrame:
    q: float = 0.0
    dq: float = 0.0
    tau_est: float = 0.0


@dataclass(frozen=True)
class IMUStateFrame:
    quaternion: tuple[float, ...]
    gyroscope: tuple[float, ...]
    accelerometer: tuple[float, ...]


@dataclass(frozen=True)
class LowStateFrame:
    motor_state: tuple[MotorStateFrame, ...]
    imu_state: IMUStateFrame

    def __post_init__(self) -> None:
        if len(self.motor_state) != DDS_MOTOR_SLOT_COUNT:
            raise Go2BridgeError("LowState must retain all 20 DDS motor slots")


@dataclass(frozen=True)
class SportModeStateFrame:
    position: tuple[float, ...]
    velocity: tuple[float, ...]


class Go2Backend(Protocol):
    timestep: float
    actuator_names: tuple[str, ...]

    @property
    def simulation_time(self) -> float: ...

    def reset(self) -> None: ...
    def full_state(self) -> tuple[tuple[float, ...], tuple[float, ...]]: ...
    def sensors(self) -> MuJoCoSensorFrame: ...
    def set_controls(self, controls: Sequence[Real]) -> None: ...
    def step(self) -> None: ...
    def close(self) -> None: ...


class Go2Transport(Protocol):
    """The only admitted transport role: lowcmd input and two state outputs."""

    def start(self) -> None: ...
    def take_lowcmd(self) -> object | None: ...
    def is_lowcmd_type(self, message: object) -> bool: ...
    def compute_crc(self, message: object) -> int: ...
    def publish_lowstate(self, state: LowStateFrame) -> None: ...
    def publish_sportmodestate(self, state: SportModeStateFrame) -> None: ...
    def close(self) -> None: ...


def _lowstate_from_sensors(sensors: MuJoCoSensorFrame) -> LowStateFrame:
    active = tuple(
        MotorStateFrame(sensors.q[index], sensors.dq[index], sensors.actuator_force[index])
        for index in range(ACTIVE_MOTOR_COUNT)
    )
    inactive = tuple(MotorStateFrame() for _ in range(DDS_MOTOR_SLOT_COUNT - ACTIVE_MOTOR_COUNT))
    return LowStateFrame(
        motor_state=active + inactive,
        imu_state=IMUStateFrame(
            tuple(sensors.imu_quaternion),
            tuple(sensors.imu_gyroscope),
            tuple(sensors.imu_accelerometer),
        ),
    )


class Go2DDSMuJoCoBridge:
    """Headless, Session-Runner-clocked Go2 transport bridge.

    The intended public lifecycle surface is exactly ``start``, ``reset``,
    ``step``, ``close``, and the injected ``transport`` boundary.
    """

    def __init__(self, backend: Go2Backend, transport: Go2Transport) -> None:
        if tuple(backend.actuator_names) != ACTIVE_MOTOR_NAMES:
            raise Go2BridgeError("backend actuator order must be FR/FL/RR/RL with hip/thigh/calf")
        if not math.isfinite(float(backend.timestep)) or float(backend.timestep) <= 0:
            raise Go2BridgeError("backend timestep must be finite and positive")
        self._backend = backend
        self.transport = transport
        self._started = False
        self._closed = False
        self._last_valid_command_time: float | None = None
        self._command_health = "RESET"
        self._accepted_commands = 0
        self._rejected_commands = 0
        self._controls = (0.0,) * ACTIVE_MOTOR_COUNT

    def start(self) -> dict[str, object]:
        if self._closed:
            raise Go2BridgeError("bridge is closed")
        if self._started:
            raise Go2BridgeError("bridge is already started")
        self.transport.start()
        self._backend.set_controls(self._controls)
        self._started = True
        return {"started": True, "command_health": self._command_health}

    def reset(self) -> dict[str, object]:
        self._require_started()
        self._backend.reset()
        self._controls = (0.0,) * ACTIVE_MOTOR_COUNT
        self._backend.set_controls(self._controls)
        self._last_valid_command_time = None
        self._command_health = "RESET"
        return {"simulation_time": self._simulation_time(), "command_health": self._command_health}

    def step(self) -> dict[str, object]:
        self._require_started()
        rejected_reason: str | None = None
        accepted = False
        message = self.transport.take_lowcmd()
        if message is not None:
            try:
                controls = self._validated_controls(message, self._backend.sensors())
                self._backend.set_controls(controls)
            except (LowCmdRejected, Go2BridgeError, OverflowError, ValueError) as exc:
                self._rejected_commands += 1
                rejected_reason = str(exc)
            else:
                self._controls = controls
                self._last_valid_command_time = self._simulation_time()
                self._command_health = "VALID"
                self._accepted_commands += 1
                accepted = True

        self._enforce_stale()
        self._backend.step()
        self._enforce_stale()
        sensors = self._backend.sensors()
        self.transport.publish_lowstate(_lowstate_from_sensors(sensors))
        self.transport.publish_sportmodestate(
            SportModeStateFrame(
                position=tuple(sensors.frame_position),
                velocity=tuple(sensors.frame_linear_velocity),
            )
        )
        return {
            "simulation_time": self._simulation_time(),
            "accepted": accepted,
            "rejected_reason": rejected_reason,
            "command_health": self._command_health,
            "accepted_commands": self._accepted_commands,
            "rejected_commands": self._rejected_commands,
        }

    def close(self) -> dict[str, object]:
        if self._closed:
            return {"closed": True}
        try:
            if self._started:
                self._controls = (0.0,) * ACTIVE_MOTOR_COUNT
                self._backend.set_controls(self._controls)
        finally:
            try:
                self.transport.close()
            finally:
                self._backend.close()
                self._closed = True
                self._started = False
        return {"closed": True}

    def _require_started(self) -> None:
        if self._closed:
            raise Go2BridgeError("bridge is closed")
        if not self._started:
            raise Go2BridgeError("bridge is not started")

    def _simulation_time(self) -> float:
        value = float(self._backend.simulation_time)
        if not math.isfinite(value) or value < 0:
            raise Go2BridgeError("backend simulation time must be finite and non-negative")
        return value

    def _enforce_stale(self) -> None:
        now = self._simulation_time()
        origin = 0.0 if self._last_valid_command_time is None else self._last_valid_command_time
        if now - origin + 1e-15 >= STALE_LIMIT_SIMULATION_S:
            zero = (0.0,) * ACTIVE_MOTOR_COUNT
            if self._controls != zero:
                self._backend.set_controls(zero)
                self._controls = zero
            self._command_health = "STALE"

    def _validated_controls(
        self, message: object, sensors: MuJoCoSensorFrame
    ) -> tuple[float, ...]:
        try:
            correct_type = self.transport.is_lowcmd_type(message)
        except Exception as exc:
            raise LowCmdRejected("DDS type validation failed") from exc
        if not correct_type:
            raise LowCmdRejected("wrong DDS type; expected pinned Go2 LowCmd_")
        head_value = _field(message, "head")
        if isinstance(head_value, (str, bytes, bytearray)):
            raise LowCmdRejected("LowCmd_.head must equal [0xFE, 0xEF]")
        try:
            head = tuple(head_value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise LowCmdRejected("LowCmd_.head must equal [0xFE, 0xEF]") from exc
        if (
            len(head) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in head)
            or head != LOWCMD_HEAD
        ):
            raise LowCmdRejected("LowCmd_.head must equal [0xFE, 0xEF]")
        level_flag = _field(message, "level_flag")
        if (
            isinstance(level_flag, bool)
            or not isinstance(level_flag, int)
            or level_flag != LOWCMD_LEVEL_FLAG
        ):
            raise LowCmdRejected("LowCmd_.level_flag must equal 0xFF")
        gpio = _field(message, "gpio")
        if isinstance(gpio, bool) or not isinstance(gpio, int) or gpio != LOWCMD_GPIO:
            raise LowCmdRejected("LowCmd_.gpio must equal 0")
        slots_value = _field(message, "motor_cmd")
        if isinstance(slots_value, (str, bytes, bytearray)):
            raise LowCmdRejected("motor_cmd must be a 20-slot sequence")
        try:
            slots = tuple(slots_value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise LowCmdRejected("motor_cmd must be a 20-slot sequence") from exc
        if len(slots) != DDS_MOTOR_SLOT_COUNT:
            raise LowCmdRejected("LowCmd_.motor_cmd must contain exactly 20 slots")

        received_crc = _field(message, "crc")
        if isinstance(received_crc, bool) or not isinstance(received_crc, int):
            raise LowCmdRejected("crc must be an unsigned 32-bit integer")
        if not 0 <= received_crc <= 0xFFFFFFFF:
            raise LowCmdRejected("crc must be an unsigned 32-bit integer")
        try:
            expected_crc = self.transport.compute_crc(message)
        except Exception as exc:
            raise LowCmdRejected("LowCmd CRC calculation failed") from exc
        if isinstance(expected_crc, bool) or not isinstance(expected_crc, int):
            raise LowCmdRejected("transport CRC calculator returned an invalid value")
        if received_crc != expected_crc & 0xFFFFFFFF:
            raise LowCmdRejected("LowCmd CRC mismatch")

        active_fields: list[tuple[float, float, float, float, float]] = []
        for index, slot in enumerate(slots[:ACTIVE_MOTOR_COUNT]):
            mode = _field(slot, "mode")
            if isinstance(mode, bool) or not isinstance(mode, int) or mode != ACTIVE_MODE:
                raise LowCmdRejected(f"motor_cmd[{index}].mode must be 0x01")
            active_fields.append(
                tuple(
                    _finite(_field(slot, field), f"motor_cmd[{index}].{field}")
                    for field in ("q", "dq", "kp", "kd", "tau")
                )
            )

        for index, slot in enumerate(slots[ACTIVE_MOTOR_COUNT:], start=ACTIVE_MOTOR_COUNT):
            for field, expected in INACTIVE_SAFE_FIELDS.items():
                actual = _field(slot, field)
                if field == "mode":
                    if isinstance(actual, bool) or not isinstance(actual, int) or actual != expected:
                        raise LowCmdRejected(f"inactive motor_cmd[{index}].mode changed")
                elif _finite(actual, f"motor_cmd[{index}].{field}") != expected:
                    raise LowCmdRejected(f"inactive motor_cmd[{index}].{field} changed")

        controls: list[float] = []
        for index, (q_target, dq_target, kp, kd, tau) in enumerate(active_fields):
            value = tau + kp * (q_target - sensors.q[index]) + kd * (dq_target - sensors.dq[index])
            if not math.isfinite(value):
                raise LowCmdRejected(f"computed ctrl[{index}] is non-finite")
            controls.append(value)
        return tuple(controls)


class MuJoCoGo2Backend:
    """Exact named-sensor/actuator adapter for MuJoCo 3.3.6."""

    actuator_names = ACTIVE_MOTOR_NAMES

    def __init__(self, model_path: str | Path) -> None:
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - Linux integration dependency
            raise Go2BridgeError("mujoco==3.3.6 is required") from exc
        if getattr(mujoco, "__version__", None) != "3.3.6":
            raise Go2BridgeError(
                f"mujoco==3.3.6 is required, found {getattr(mujoco, '__version__', 'unknown')}"
            )
        self._mj = mujoco
        self.model_path = Path(model_path).resolve()
        self.model = mujoco.MjModel.from_xml_path(str(self.model_path))
        self.data = mujoco.MjData(self.model)
        self.timestep = float(self.model.opt.timestep)
        actual_actuator_names = tuple(
            mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, index)
            for index in range(self.model.nu)
        )
        if actual_actuator_names != ACTIVE_MOTOR_NAMES:
            raise Go2BridgeError(
                "MuJoCo actuator order must be the official FR/FL/RR/RL 12-actuator order"
            )
        self._actuator_ids = tuple(self._require_id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ACTIVE_MOTOR_NAMES)
        self._sensor_names = (
            tuple(f"{name}_pos" for name in ACTIVE_MOTOR_NAMES)
            + tuple(f"{name}_vel" for name in ACTIVE_MOTOR_NAMES)
            + tuple(f"{name}_torque" for name in ACTIVE_MOTOR_NAMES)
            + ("imu_quat", "imu_gyro", "imu_acc", "frame_pos", "frame_vel")
        )
        actual_sensor_names = tuple(
            mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_SENSOR, index)
            for index in range(self.model.nsensor)
        )
        if actual_sensor_names != self._sensor_names:
            raise Go2BridgeError("MuJoCo sensors do not match the official Go2 sensor names and order")
        expected_sensor_dimensions = (1,) * 36 + (4, 3, 3, 3, 3)
        actual_sensor_dimensions = tuple(int(value) for value in self.model.sensor_dim)
        if actual_sensor_dimensions != expected_sensor_dimensions:
            raise Go2BridgeError("MuJoCo sensors do not match the official Go2 sensor dimensions")
        for name in self._sensor_names:
            self._require_id(mujoco.mjtObj.mjOBJ_SENSOR, name)
        self._home_keyframe_id = self._require_id(mujoco.mjtObj.mjOBJ_KEY, "home")
        for actuator_id, joint_name in zip(self._actuator_ids, ACTIVE_JOINT_NAMES):
            joint_id = self._require_id(mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            if int(self.model.actuator_trnid[actuator_id, 0]) != joint_id:
                raise Go2BridgeError(f"MuJoCo actuator does not target frozen joint {joint_name!r}")
        self.reset()

    def _require_id(self, kind: object, name: str) -> int:
        identifier = int(self._mj.mj_name2id(self.model, kind, name))
        if identifier < 0:
            raise Go2BridgeError(f"MuJoCo model is missing {name!r}")
        return identifier

    def _sensor(self, name: str, length: int) -> tuple[float, ...]:
        sensor_id = self._require_id(self._mj.mjtObj.mjOBJ_SENSOR, name)
        address = int(self.model.sensor_adr[sensor_id])
        dimension = int(self.model.sensor_dim[sensor_id])
        if dimension != length:
            raise Go2BridgeError(f"MuJoCo sensor {name!r} must have dimension {length}")
        return _finite_vector(self.data.sensordata[address : address + dimension], length, name)

    @property
    def simulation_time(self) -> float:
        return float(self.data.time)

    def reset(self) -> None:
        self._mj.mj_resetDataKeyframe(self.model, self.data, self._home_keyframe_id)
        self._mj.mj_forward(self.model, self.data)

    def full_state(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Return the complete free-base plus joint state for reset qualification."""

        return tuple(float(value) for value in self.data.qpos), tuple(
            float(value) for value in self.data.qvel
        )

    def sensors(self) -> MuJoCoSensorFrame:
        return MuJoCoSensorFrame(
            q=tuple(self._sensor(f"{name}_pos", 1)[0] for name in ACTIVE_MOTOR_NAMES),
            dq=tuple(self._sensor(f"{name}_vel", 1)[0] for name in ACTIVE_MOTOR_NAMES),
            actuator_force=tuple(self._sensor(f"{name}_torque", 1)[0] for name in ACTIVE_MOTOR_NAMES),
            imu_quaternion=self._sensor("imu_quat", 4),
            imu_gyroscope=self._sensor("imu_gyro", 3),
            imu_accelerometer=self._sensor("imu_acc", 3),
            frame_position=self._sensor("frame_pos", 3),
            frame_linear_velocity=self._sensor("frame_vel", 3),
        )

    def set_controls(self, controls: Sequence[Real]) -> None:
        values = _finite_vector(controls, ACTIVE_MOTOR_COUNT, "controls")
        before_qpos = self.data.qpos.copy()
        before_qvel = self.data.qvel.copy()
        for actuator_id, value in zip(self._actuator_ids, values):
            self.data.ctrl[actuator_id] = value
        if not (self.data.qpos == before_qpos).all() or not (self.data.qvel == before_qvel).all():
            raise Go2BridgeError("ordinary control application changed qpos/qvel directly")

    def step(self) -> None:
        self._mj.mj_step(self.model, self.data)

    def close(self) -> None:
        return None


class UnitreeSDK2Transport:
    """Real SDK2/CycloneDDS domain-1 loopback transport, loaded only on Linux."""

    def __init__(self) -> None:
        self._latest: object | None = None
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    def start(self) -> None:  # pragma: no cover - explicit Linux integration route
        if self._started or self._closed:
            raise Go2BridgeError("invalid DDS transport lifecycle")
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelPublisher, ChannelSubscriber
            from unitree_sdk2py.idl.default import (
                unitree_go_msg_dds__LowState_,
                unitree_go_msg_dds__SportModeState_,
            )
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowCmd_, LowState_, SportModeState_
            from unitree_sdk2py.utils.crc import CRC
        except ImportError as exc:
            raise Go2BridgeError("pinned unitree_sdk2py/CycloneDDS runtime is unavailable") from exc
        ChannelFactoryInitialize(1, "lo")
        self._lowcmd_type = LowCmd_
        self._crc = CRC()
        self._lowstate_factory = unitree_go_msg_dds__LowState_
        self._sport_factory = unitree_go_msg_dds__SportModeState_
        self._lowcmd_subscriber = ChannelSubscriber(LOWCMD_TOPIC, LowCmd_)
        self._lowstate_publisher = ChannelPublisher(LOWSTATE_TOPIC, LowState_)
        self._sport_publisher = ChannelPublisher(SPORTMODESTATE_TOPIC, SportModeState_)
        self._lowcmd_subscriber.Init(self._receive_lowcmd, 10)
        self._lowstate_publisher.Init()
        self._sport_publisher.Init()
        self._started = True

    def _receive_lowcmd(self, message: object) -> None:  # pragma: no cover - DDS callback
        with self._lock:
            self._latest = message

    def take_lowcmd(self) -> object | None:
        with self._lock:
            message = self._latest
            self._latest = None
        return message

    def is_lowcmd_type(self, message: object) -> bool:
        return self._started and isinstance(message, self._lowcmd_type)

    def compute_crc(self, message: object) -> int:
        if not self._started:
            raise Go2BridgeError("DDS transport is not started")
        return int(self._crc.Crc(message))

    def publish_lowstate(self, state: LowStateFrame) -> None:  # pragma: no cover - DDS route
        message = self._lowstate_factory()
        if len(message.motor_state) != DDS_MOTOR_SLOT_COUNT:
            raise Go2BridgeError("real LowState_ does not have 20 motor slots")
        for target, source in zip(message.motor_state, state.motor_state):
            target.q = source.q
            target.dq = source.dq
            target.tau_est = source.tau_est
        message.imu_state.quaternion[:] = state.imu_state.quaternion
        message.imu_state.gyroscope[:] = state.imu_state.gyroscope
        message.imu_state.accelerometer[:] = state.imu_state.accelerometer
        self._lowstate_publisher.Write(message)

    def publish_sportmodestate(self, state: SportModeStateFrame) -> None:  # pragma: no cover - DDS route
        message = self._sport_factory()
        message.position[:] = state.position
        message.velocity[:] = state.velocity
        self._sport_publisher.Write(message)

    def close(self) -> None:
        if self._closed:
            return
        for endpoint_name in ("_lowcmd_subscriber", "_lowstate_publisher", "_sport_publisher"):
            endpoint = getattr(self, endpoint_name, None)
            close = getattr(endpoint, "Close", None)
            if callable(close):
                close()
        self._closed = True
        self._started = False
