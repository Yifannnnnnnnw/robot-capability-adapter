"""Small Unitree Go2 low-level source-evidence probe.

This module deliberately models only the boundary visible in the pinned
``unitree_mujoco`` bridge.  It is not a controller, a robot shim, a
capability implementation, or a DDS runner.  Normal tests use only the
standard library; the optional symbol probe imports the real SDK lazily and
never initializes a DDS domain.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata as metadata
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any


ACTIVE_MOTOR_COUNT = 12
DDS_MOTOR_SLOT_COUNT = 20

# This order is taken from unitree_sdk2py/example/go2/low_level/
# unitree_legged_const.py and the actuator block in go2.xml.  It is not the
# worldbody traversal order (the worldbody starts with FL).
GO2_MOTOR_NAMES = (
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
GO2_MOTOR_JOINT_NAMES = tuple(f"{name}_joint" for name in GO2_MOTOR_NAMES)
GO2_LEG_IDS = (
    "FR_0",
    "FR_1",
    "FR_2",
    "FL_0",
    "FL_1",
    "FL_2",
    "RR_0",
    "RR_1",
    "RR_2",
    "RL_0",
    "RL_1",
    "RL_2",
)
GO2_MOTOR_INDEX = {name: index for index, name in enumerate(GO2_MOTOR_NAMES)}

LOWCMD_TOPIC = "rt/lowcmd"
LOWSTATE_TOPIC = "rt/lowstate"
SPORT_MODE_STATE_TOPIC = "rt/sportmodestate"

# The pinned bridge computes dim_motor_sensor = 3 * num_motor and then reads
# these fields in this exact order from go2.xml.
MUJOCO_MOTOR_SENSOR_BLOCK = ACTIVE_MOTOR_COUNT * 3
MUJOCO_SENSOR_DATA_LENGTH = MUJOCO_MOTOR_SENSOR_BLOCK + 4 + 3 + 3 + 3 + 3

UNITREE_SDK2PY_DISTRIBUTION = "unitree_sdk2py"
UNITREE_SDK2PY_VERSION = "1.0.1"  # setup.py at the pinned source commit
CYCLONEDDS_DISTRIBUTION = "cyclonedds"
CYCLONEDDS_VERSION = "0.10.2"

UNITREE_MUJOCO_KEY_FILE_HASHES = {
    "simulate_python/unitree_sdk2py_bridge.py":
    "3ddb54ddddc6a20255e9bb77760537774b2eb77ce50073bf1f4a69bfaa77b599",
    "unitree_robots/go2/go2.xml":
    "2014a3d76e30f17ab9447d8a67bd015291f74fa4d71ae30d005f1a32bd693d4b",
}


class Go2ProbeError(ValueError):
    """Invalid data for this fail-closed source-evidence probe."""


class Go2SourceEvidenceError(Go2ProbeError):
    """A caller-supplied pinned source checkout is not the expected source."""


class Go2ProbeScopeError(Go2ProbeError):
    """A request crossed the low-level-only probe boundary."""


class Go2ProbeUnavailable(RuntimeError):
    """The optional real Linux SDK probe cannot run in this environment."""


class Go2ProbeIdentityError(RuntimeError):
    """An installed optional dependency or symbol does not match the pin."""


def _finite_vector(name: str, values: Sequence[Real], length: int) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise Go2ProbeError(f"{name} must be a numeric sequence")
    try:
        vector = tuple(values)
    except TypeError as exc:
        raise Go2ProbeError(f"{name} must be a numeric sequence") from exc
    if len(vector) != length:
        raise Go2ProbeError(f"{name} must contain exactly {length} values")

    normalized: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise Go2ProbeError(f"{name} contains a non-real value")
        number = float(value)
        if not math.isfinite(number):
            raise Go2ProbeError(f"{name} contains a non-finite value")
        normalized.append(number)
    return tuple(normalized)


def _validate_motor_indices(indices: Sequence[int]) -> tuple[int, ...]:
    try:
        normalized = tuple(indices)
    except TypeError as exc:
        raise Go2ProbeError("motor indices must be a sequence") from exc
    if normalized != tuple(range(ACTIVE_MOTOR_COUNT)):
        raise Go2ProbeError("motor indices must be the fixed 0..11 active order")
    if any(isinstance(index, bool) or not isinstance(index, int) for index in normalized):
        raise Go2ProbeError("motor indices must be integers")
    return normalized


def validate_dds_motor_slots(slot_count: int) -> int:
    """Require the real Go2 LowCmd/LowState container width (20 slots)."""

    if isinstance(slot_count, bool) or not isinstance(slot_count, int):
        raise Go2ProbeError("DDS motor slot count must be an integer")
    if slot_count != DDS_MOTOR_SLOT_COUNT:
        raise Go2ProbeError(
            f"Go2 DDS LowCmd/LowState has {DDS_MOTOR_SLOT_COUNT} slots; "
            f"{slot_count} is not a valid container width"
        )
    return slot_count


def validate_active_motor_destinations(
    destinations: Sequence[str],
) -> tuple[str, ...]:
    """Validate the fixed active actuator destination order.

    The check is intentionally exact: a reordered leg or duplicate destination
    must fail instead of being silently reindexed.
    """

    try:
        normalized = tuple(destinations)
    except TypeError as exc:
        raise Go2ProbeError("motor destinations must be a sequence") from exc
    if len(normalized) != ACTIVE_MOTOR_COUNT:
        raise Go2ProbeError("motor destinations must contain exactly 12 entries")
    if len(set(normalized)) != ACTIVE_MOTOR_COUNT:
        raise Go2ProbeError("motor destinations must not contain duplicates")
    if normalized != GO2_MOTOR_NAMES:
        raise Go2ProbeError("motor destinations do not match the pinned Go2 order")
    return normalized


@dataclass(frozen=True)
class Go2LowCmdLike:
    """The five motor fields consumed by the pinned bridge for active motors.

    The real DDS message remains a 20-slot ``LowCmd_``.  This record is only
    its validated first-12 active projection and does not model mode, head,
    level_flag, gpio, or CRC fields.
    """

    q: Sequence[Real]
    dq: Sequence[Real]
    kp: Sequence[Real]
    kd: Sequence[Real]
    tau: Sequence[Real]
    motor_names: Sequence[str] = GO2_MOTOR_NAMES
    motor_indices: Sequence[int] = tuple(range(ACTIVE_MOTOR_COUNT))
    dds_motor_slots: int = DDS_MOTOR_SLOT_COUNT

    def __post_init__(self) -> None:
        validate_dds_motor_slots(self.dds_motor_slots)
        object.__setattr__(self, "q", _finite_vector("q", self.q, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "dq", _finite_vector("dq", self.dq, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "kp", _finite_vector("kp", self.kp, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "kd", _finite_vector("kd", self.kd, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "tau", _finite_vector("tau", self.tau, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "motor_names", validate_active_motor_destinations(self.motor_names))
        object.__setattr__(self, "motor_indices", _validate_motor_indices(self.motor_indices))


def translate_bridge_control_equation(
    command: Go2LowCmdLike,
    current_q: Sequence[Real],
    current_dq: Sequence[Real],
) -> tuple[float, ...]:
    """Evaluate the pinned bridge boundary equation, without controller logic.

    ``ctrl = tau + kp * (q_target - q) + kd * (dq_target - dq)``

    No clipping, watchdog, trajectory generation, gain selection, CRC, or
    command-header validation is added here.
    """

    if not isinstance(command, Go2LowCmdLike):
        raise Go2ProbeError("command must be Go2LowCmdLike")
    q_now = _finite_vector("current_q", current_q, ACTIVE_MOTOR_COUNT)
    dq_now = _finite_vector("current_dq", current_dq, ACTIVE_MOTOR_COUNT)
    return tuple(
        tau + kp * (q_target - q) + kd * (dq_target - dq)
        for q_target, dq_target, kp, kd, tau, q, dq in zip(
            command.q,
            command.dq,
            command.kp,
            command.kd,
            command.tau,
            q_now,
            dq_now,
        )
    )


@dataclass(frozen=True)
class MuJoCoLikeState:
    """The exact Go2 sensor projection used by the pinned bridge probe."""

    q: Sequence[Real]
    dq: Sequence[Real]
    actuator_force: Sequence[Real]
    imu_quaternion: Sequence[Real]
    imu_gyroscope: Sequence[Real]
    imu_accelerometer: Sequence[Real]
    base_position: Sequence[Real]
    base_velocity: Sequence[Real]

    def __post_init__(self) -> None:
        object.__setattr__(self, "q", _finite_vector("q", self.q, ACTIVE_MOTOR_COUNT))
        object.__setattr__(self, "dq", _finite_vector("dq", self.dq, ACTIVE_MOTOR_COUNT))
        object.__setattr__(
            self,
            "actuator_force",
            _finite_vector("actuator_force", self.actuator_force, ACTIVE_MOTOR_COUNT),
        )
        object.__setattr__(
            self, "imu_quaternion", _finite_vector("imu_quaternion", self.imu_quaternion, 4)
        )
        object.__setattr__(
            self, "imu_gyroscope", _finite_vector("imu_gyroscope", self.imu_gyroscope, 3)
        )
        object.__setattr__(
            self,
            "imu_accelerometer",
            _finite_vector("imu_accelerometer", self.imu_accelerometer, 3),
        )
        object.__setattr__(
            self, "base_position", _finite_vector("base_position", self.base_position, 3)
        )
        object.__setattr__(self, "base_velocity", _finite_vector("base_velocity", self.base_velocity, 3))

    @classmethod
    def from_sensor_data(cls, sensor_data: Sequence[Real]) -> "MuJoCoLikeState":
        """Parse ``sensordata`` using the pinned 36+16 layout.

        The first 12 values are position, the next 12 velocity, and the next
        12 actuator force.  They are followed by quaternion, gyro,
        accelerometer, frame position, and frame linear velocity.
        """

        values = _finite_vector("sensordata", sensor_data, MUJOCO_SENSOR_DATA_LENGTH)
        offset = MUJOCO_MOTOR_SENSOR_BLOCK
        return cls(
            q=values[0:12],
            dq=values[12:24],
            actuator_force=values[24:36],
            imu_quaternion=values[offset : offset + 4],
            imu_gyroscope=values[offset + 4 : offset + 7],
            imu_accelerometer=values[offset + 7 : offset + 10],
            base_position=values[offset + 10 : offset + 13],
            base_velocity=values[offset + 13 : offset + 16],
        )

    @classmethod
    def from_mapping(cls, state: Mapping[str, Any]) -> "MuJoCoLikeState":
        required = {
            "q",
            "dq",
            "actuator_force",
            "imu_quaternion",
            "imu_gyroscope",
            "imu_accelerometer",
            "base_position",
            "base_velocity",
        }
        if not isinstance(state, Mapping):
            raise Go2ProbeError("MuJoCo state must be a mapping")
        missing = required.difference(state)
        extra = set(state).difference(required)
        if missing or extra:
            raise Go2ProbeError(
                f"MuJoCo state fields must be exact; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return cls(**{field: state[field] for field in required})


@dataclass(frozen=True)
class MotorStateProbe:
    index: int
    name: str
    q: float
    dq: float
    tau_est: float


@dataclass(frozen=True)
class IMUStateProbe:
    quaternion: tuple[float, ...]
    gyroscope: tuple[float, ...]
    accelerometer: tuple[float, ...]


@dataclass(frozen=True)
class LowStateProbe:
    """Active first-12 view of the real 20-slot LowState_ container."""

    motor_state: tuple[MotorStateProbe, ...]
    imu_state: IMUStateProbe | None
    dds_motor_slots: int = DDS_MOTOR_SLOT_COUNT

    def __post_init__(self) -> None:
        validate_dds_motor_slots(self.dds_motor_slots)
        if len(self.motor_state) != ACTIVE_MOTOR_COUNT:
            raise Go2ProbeError("LowState active motor projection must contain 12 entries")
        validate_active_motor_destinations(tuple(item.name for item in self.motor_state))
        _validate_motor_indices(tuple(item.index for item in self.motor_state))


@dataclass(frozen=True)
class SportModeStateProbe:
    """Only the three position and three velocity fields filled by the bridge."""

    position: tuple[float, ...]
    velocity: tuple[float, ...]
    topic: str = SPORT_MODE_STATE_TOPIC

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", _finite_vector("position", self.position, 3))
        object.__setattr__(self, "velocity", _finite_vector("velocity", self.velocity, 3))
        if self.topic != SPORT_MODE_STATE_TOPIC:
            raise Go2ProbeError("SportModeState topic does not match the pinned bridge")


@dataclass(frozen=True)
class Go2MappedState:
    low_state: LowStateProbe
    sport_mode_state: SportModeStateProbe


def map_mujoco_state_to_probe(
    state: MuJoCoLikeState,
    *,
    imu_available: bool = True,
    motor_destinations: Sequence[str] = GO2_MOTOR_NAMES,
) -> Go2MappedState:
    """Map only the bridge-visible state fields into probe records."""

    if not isinstance(state, MuJoCoLikeState):
        raise Go2ProbeError("state must be MuJoCoLikeState")
    if not isinstance(imu_available, bool):
        raise Go2ProbeError("imu_available must be boolean")
    validate_active_motor_destinations(motor_destinations)
    motor_state = tuple(
        MotorStateProbe(index, name, state.q[index], state.dq[index], state.actuator_force[index])
        for index, name in enumerate(GO2_MOTOR_NAMES)
    )
    imu_state = (
        IMUStateProbe(state.imu_quaternion, state.imu_gyroscope, state.imu_accelerometer)
        if imu_available
        else None
    )
    return Go2MappedState(
        low_state=LowStateProbe(motor_state=motor_state, imu_state=imu_state),
        sport_mode_state=SportModeStateProbe(
            position=state.base_position,
            velocity=state.base_velocity,
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pinned_unitree_mujoco_checkout(root: str | Path) -> dict[str, str]:
    """Verify key files in a caller-provided, already checked-out source tree.

    This function never downloads, runs the simulator, or follows a symlink.
    The caller still supplies the exact pinned checkout; this check binds the
    bridge and Go2 MJCF bytes to the recorded source evidence hashes.
    """

    root_path = Path(root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise Go2SourceEvidenceError("unitree_mujoco root must be a real directory")
    root_path = root_path.resolve()
    verified: dict[str, str] = {}
    for relative, expected in UNITREE_MUJOCO_KEY_FILE_HASHES.items():
        path = root_path / relative
        if path.is_symlink():
            raise Go2SourceEvidenceError(f"source key file must not be a symlink: {relative}")
        resolved = path.resolve()
        if root_path != resolved and root_path not in resolved.parents:
            raise Go2SourceEvidenceError(f"source key file escapes checkout: {relative}")
        if not path.is_file():
            raise Go2SourceEvidenceError(f"missing source key file: {relative}")
        actual = _sha256_file(path)
        if actual != expected:
            raise Go2SourceEvidenceError(
                f"source hash mismatch for {relative}: expected {expected}, got {actual}"
            )
        verified[relative] = actual
    return verified


def verify_pinned_unitree_distribution_identity() -> dict[str, str]:
    """Check the exact optional SDK and CycloneDDS distributions."""

    expected = {
        UNITREE_SDK2PY_DISTRIBUTION: UNITREE_SDK2PY_VERSION,
        CYCLONEDDS_DISTRIBUTION: CYCLONEDDS_VERSION,
    }
    actual: dict[str, str] = {}
    for distribution, version in expected.items():
        try:
            installed = metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            raise Go2ProbeUnavailable(f"optional distribution is missing: {distribution}") from exc
        if installed != version:
            raise Go2ProbeIdentityError(
                f"{distribution}=={version} is required, installed {installed!r}"
            )
        actual[distribution] = installed
    return actual


_REAL_SYMBOLS = {
    "unitree_sdk2py.core.channel": (
        "ChannelFactoryInitialize",
        "ChannelPublisher",
        "ChannelSubscriber",
    ),
    "unitree_sdk2py.idl.default": (
        "unitree_go_msg_dds__LowCmd_",
        "unitree_go_msg_dds__LowState_",
        "unitree_go_msg_dds__SportModeState_",
    ),
    "unitree_sdk2py.idl.unitree_go.msg.dds_": (
        "LowCmd_",
        "LowState_",
        "SportModeState_",
    ),
    "unitree_sdk2py.go2.sport.sport_client": ("SportClient",),
}


def probe_real_unitree_sdk_symbols() -> dict[str, Any]:
    """Lazily inspect real SDK symbols without initializing DDS.

    A real DDS/MuJoCo roundtrip is intentionally not attempted by this probe.
    """

    if sys.platform != "linux":
        raise Go2ProbeUnavailable("real Unitree SDK/DDS probe requires Linux")
    versions = verify_pinned_unitree_distribution_identity()
    imported: list[str] = []
    for module_name, symbols in _REAL_SYMBOLS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise Go2ProbeUnavailable(f"real Unitree symbol module unavailable: {module_name}") from exc
        imported.append(module_name)
        missing = [symbol for symbol in symbols if not hasattr(module, symbol)]
        if missing:
            raise Go2ProbeIdentityError(
                f"real Unitree module {module_name} is missing symbols: {', '.join(missing)}"
            )
    return {
        "distribution_versions": versions,
        "modules": tuple(imported),
        "dds_initialized": False,
        "route": "LOW_LEVEL_ONLY",
    }


_HIGH_LEVEL_SPORT_METHODS = frozenset(
    {
        "Damp",
        "StandUp",
        "StandDown",
        "Move",
        "StopMove",
        "Sit",
        "RiseSit",
    }
)


def require_low_level_probe_route(route: str) -> str:
    """Reject high-level SportClient or any route outside this probe."""

    if route != "LOW_LEVEL_ONLY":
        raise Go2ProbeScopeError(
            "only LOW_LEVEL_ONLY is in scope; SportClient methods are not admitted by this probe"
        )
    return route


def reject_high_level_sport_method(method: str) -> None:
    """Fail closed if a high-level SDK method is presented as probe output."""

    method_name = method.rsplit(".", 1)[-1]
    if method_name in _HIGH_LEVEL_SPORT_METHODS or method.startswith("SportClient."):
        raise Go2ProbeScopeError(
            f"SportClient high-level method is outside the low-level probe: {method}"
        )
    raise Go2ProbeScopeError(f"unrecognized non-low-level route: {method}")
