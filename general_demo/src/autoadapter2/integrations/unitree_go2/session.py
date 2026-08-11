"""Production Unitree Go2 EvaluationRobotSession.

This module is the small Session-Runner layer above the frozen Go2 bridge.  It
owns the real SDK2 low-level DDS façade, reset and evidence bookkeeping, and
the private MuJoCo truth adapter used by the evaluation Harness.  It does not
contain posture, gait, trajectory, balance, or task behavior.

The session deliberately keeps the SDK object narrow.  Candidate code can
publish a low-level command and read the two SDK state topics; the session
advances MuJoCo only after the candidate invocation through the existing
``Go2DDSMuJoCoBridge``.
"""

from __future__ import annotations

import copy
import importlib
import json
import math
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...demo import ValidationEvidence
from ...evaluation import FrozenVideoProfile, RGBFrame
from ...validation import HarnessInvocation, MeasurementSample, ValidatedCandidateHandle
from .bridge import (
    ACTIVE_MODE,
    ACTIVE_MOTOR_COUNT,
    DDS_MOTOR_SLOT_COUNT,
    Go2Backend,
    Go2DDSMuJoCoBridge,
    Go2Transport,
    INACTIVE_SAFE_FIELDS,
    MuJoCoGo2Backend,
    UnitreeSDK2Transport,
)


ROBOT_MODEL_ID = "unitree-go2"
ROBOT_CONFIGURATION_ID = "unitree-go2-stock-12dof"
RESET_ABSOLUTE_TOLERANCE = 1e-9
DEFAULT_ROLLOUT_STEPS = 1
DEFAULT_STATE_WAIT_S = 2.0
DEFAULT_MAX_ROLLOUT_STEPS = 5000
DEFAULT_FLOOR_Z_M = 0.0
DEFAULT_STANDING_HEIGHT_M = 0.34


class Go2SessionError(RuntimeError):
    """A lifecycle, evidence, or SDK-session contract failure."""


class Go2SDKError(Go2SessionError):
    """The narrow SDK2 façade could not construct or use a real endpoint."""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Go2SessionError(f"{label} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise Go2SessionError(f"{label} must be finite")
    return result


def _vector(values: Sequence[Any], length: int, label: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise Go2SessionError(f"{label} must be a numeric sequence")
    try:
        normalized = tuple(values)
    except TypeError as exc:
        raise Go2SessionError(f"{label} must be a numeric sequence") from exc
    if len(normalized) != length:
        raise Go2SessionError(f"{label} must contain exactly {length} values")
    return tuple(_finite(value, f"{label}[{index}]") for index, value in enumerate(normalized))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Go2SessionError(f"{label} must be non-empty text")
    return value.strip()


def _unit(value: Sequence[float], label: str) -> tuple[float, ...]:
    norm = math.sqrt(sum(item * item for item in value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise Go2SessionError(f"{label} cannot be normalized")
    return tuple(item / norm for item in value)


def _quaternion_axes(quaternion: Sequence[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return the body x and z axes from a MuJoCo/SDK ``w,x,y,z`` quaternion."""

    w, x, y, z = _unit(_vector(quaternion, 4, "body quaternion"), "body quaternion")
    body_x = (
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y + w * z),
        2.0 * (x * z - w * y),
    )
    body_z = (
        2.0 * (x * z + w * y),
        2.0 * (y * z - w * x),
        1.0 - 2.0 * (x * x + y * y),
    )
    return body_x, body_z


def _matrix_axes(rotation_matrix: Sequence[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return axes from MuJoCo's row-major 3x3 body orientation matrix."""

    matrix = _vector(rotation_matrix, 9, "body rotation matrix")
    return (
        (matrix[0], matrix[3], matrix[6]),
        (matrix[2], matrix[5], matrix[8]),
    )


def upright_score(body_z_axis: Sequence[float]) -> float:
    """Compute the trusted positive-body-z dot world-positive-z score."""

    z_axis = _unit(_vector(body_z_axis, 3, "body z axis"), "body z axis")
    return float(z_axis[2])


def initial_body_yaw_frame(
    body_x_axis: Sequence[float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return normalized forward and left axes in the initial horizontal frame."""

    x_axis = _vector(body_x_axis, 3, "body x axis")
    horizontal = (x_axis[0], x_axis[1])
    norm = math.hypot(*horizontal)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise Go2SessionError("body x axis has no horizontal yaw direction")
    forward = (horizontal[0] / norm, horizontal[1] / norm)
    return forward, (-forward[1], forward[0])


def start_frame_displacement(
    start_position: Sequence[float],
    position: Sequence[float],
    forward_axis: Sequence[float],
    lateral_axis: Sequence[float],
) -> tuple[float, float, float]:
    """Return forward, left, and horizontal-drift displacement from reset."""

    start = _vector(start_position, 3, "start body position")
    current = _vector(position, 3, "body position")
    forward = _vector(forward_axis, 2, "forward axis")
    lateral = _vector(lateral_axis, 2, "lateral axis")
    delta = (current[0] - start[0], current[1] - start[1])
    return (
        delta[0] * forward[0] + delta[1] * forward[1],
        delta[0] * lateral[0] + delta[1] * lateral[1],
        math.hypot(*delta),
    )


@dataclass(frozen=True)
class Go2TruthSample:
    """Private physical state captured from MuJoCo for one simulation instant."""

    time_s: float
    body_position: tuple[float, float, float]
    body_x_axis: tuple[float, float, float]
    body_z_axis: tuple[float, float, float]
    body_linear_velocity: tuple[float, float, float]
    body_height_m: float
    upright_score: float
    planar_speed_m_s: float
    body_floor_contact: bool | None
    head_floor_contact: bool | None
    finite_required_state: bool
    contact_observation_available: bool
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Go2ValidationEvidence(ValidationEvidence):
    """ValidationEvidence plus the private route proof retained by this session."""

    route_evidence: Mapping[str, Any] = field(default_factory=dict, repr=False)


class Go2TruthProvider(Protocol):
    def __call__(self, backend: Go2Backend) -> Mapping[str, Any] | Go2TruthSample: ...


class FrameCaptureFactory(Protocol):
    def __call__(
        self,
        profile: FrozenVideoProfile,
        render_rgb: Callable[[], Any],
        simulation_time: float,
    ) -> Any: ...


def _profile(value: FrozenVideoProfile | Mapping[str, Any] | None) -> FrozenVideoProfile:
    if value is None:
        return FrozenVideoProfile(
            profile_id="general-demo-external-evaluation",
            profile_version="1.0.0",
            camera="external-evaluation",
            view="robot-and-task-scene",
            fps=30,
            width=640,
            height=480,
            container="matroska",
            codec="ffv1",
        )
    if isinstance(value, FrozenVideoProfile):
        return value
    if not isinstance(value, Mapping):
        raise Go2SessionError("video profile must be a FrozenVideoProfile or mapping")
    resolution = value.get("resolution", {})
    if not isinstance(resolution, Mapping):
        raise Go2SessionError("video profile resolution must be an object")
    return FrozenVideoProfile(
        profile_id=str(value.get("profile_id", "general-demo-external-evaluation")),
        profile_version=str(value.get("profile_version", "1.0.0")),
        camera=str(value.get("camera", "external-evaluation")),
        view=str(value.get("view", "robot-and-task-scene")),
        fps=int(value.get("fps", 30)),
        width=int(value.get("width", resolution.get("width", 640))),
        height=int(value.get("height", resolution.get("height", 480))),
        container=str(value.get("container", "matroska")),
        codec=str(value.get("codec", "ffv1")),
    )


def _compat_frame_capture(
    profile: FrozenVideoProfile,
    render_rgb: Callable[[], Any],
    simulation_time: float,
) -> RGBFrame:
    """Compatibility call for the shared ``MuJoCoFrameCapture`` contract.

    The normal path resolves the shared helper from the evaluation package.  A
    small RGBFrame-only adapter is retained for this checkout because the
    shared helper is intentionally supplied by the evaluation infrastructure,
    not by a robot integration.  It does not own recording, encoding, or
    frame-integrity policy.
    """

    raw = render_rgb()
    if isinstance(raw, RGBFrame):
        if raw.simulation_time_s != simulation_time:
            return RGBFrame(simulation_time, raw.width, raw.height, raw.rgb)
        return raw
    if isinstance(raw, Mapping):
        width = int(raw.get("width", profile.width))
        height = int(raw.get("height", profile.height))
        pixels = raw.get("rgb")
    else:
        width = profile.width
        height = profile.height
        pixels = raw
    if hasattr(pixels, "tobytes") and callable(pixels.tobytes):
        pixels = pixels.tobytes()
    if not isinstance(pixels, bytes):
        raise Go2SessionError("render_rgb must return RGB bytes or an RGBFrame")
    return RGBFrame(simulation_time, width, height, pixels)


def _resolve_shared_frame_capture() -> FrameCaptureFactory:
    """Resolve the shared renderer helper when one is installed by the Demo."""

    for module_name in (
        "autoadapter2.evaluation.mujoco_capture",
        "autoadapter2.evaluation.capture",
        "autoadapter2.evaluation",
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        helper = getattr(module, "MuJoCoFrameCapture", None)
        if callable(helper):
            return helper
    return _compat_frame_capture


# Keep the shared helper injectable and discoverable for the evaluation
# assembly.  The fallback is only the RGBFrame adapter above; recording and
# integrity policy remain in ``EvaluationVideoRecorder``.
MuJoCoFrameCapture = _resolve_shared_frame_capture()


class UnitreeGo2LowLevelSDK:
    """Real SDK2 DDS façade admitted to candidate capability code.

    Only the low-level command/state methods are part of this façade.  The
    endpoint construction deliberately uses the pinned SDK2Py symbols and
    CRC implementation; it does not import or wrap any high-level client.
    """

    is_real_sdk = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._low_state: object | None = None
        self._sport_mode_state: object | None = None
        self._low_state_publications = 0
        self._sport_mode_state_publications = 0
        self._command_count = 0
        self._last_command_type_verified = False
        self._last_command_crc_verified = False
        self._last_command: object | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def command_count(self) -> int:
        return self._command_count

    @property
    def low_state_publications(self) -> int:
        return self._low_state_publications

    @property
    def sport_mode_state_publications(self) -> int:
        return self._sport_mode_state_publications

    @property
    def last_command_evidence(self) -> Mapping[str, Any]:
        return {
            "command_count": self._command_count,
            "type_verified": self._last_command_type_verified,
            "crc_verified": self._last_command_crc_verified,
            "message_type": type(self._last_command).__name__ if self._last_command is not None else None,
        }

    def start(self) -> None:  # pragma: no cover - exercised by Linux route
        if self._started or self._closed:
            raise Go2SDKError("invalid SDK2 façade lifecycle")
        try:
            from unitree_sdk2py.core.channel import (
                ChannelFactoryInitialize,
                ChannelPublisher,
                ChannelSubscriber,
            )
            from unitree_sdk2py.idl.default import unitree_go_msg_dds__LowCmd_
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import (
                LowCmd_,
                LowState_,
                SportModeState_,
            )
            from unitree_sdk2py.utils.crc import CRC
        except ImportError as exc:
            raise Go2SDKError(
                "pinned unitree_sdk2py/CycloneDDS symbols are unavailable"
            ) from exc

        ChannelFactoryInitialize(1, "lo")
        self._command_factory = unitree_go_msg_dds__LowCmd_
        self._lowcmd_type = LowCmd_
        self._crc = CRC()
        self._publisher = ChannelPublisher("rt/lowcmd", LowCmd_)
        self._lowstate_subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        self._sport_subscriber = ChannelSubscriber("rt/sportmodestate", SportModeState_)
        try:
            self._publisher.Init()
            self._lowstate_subscriber.Init(self._on_low_state, 10)
            self._sport_subscriber.Init(self._on_sport_mode_state, 10)
        except Exception:
            self._close_endpoints()
            raise
        self._started = True

    def write_low_command(
        self,
        q: Sequence[float],
        dq: Sequence[float],
        kp: Sequence[float],
        kd: Sequence[float],
        tau: Sequence[float],
    ) -> None:  # pragma: no cover - exercised by Linux route
        self._require_started()
        values = {
            "q": _vector(q, ACTIVE_MOTOR_COUNT, "q"),
            "dq": _vector(dq, ACTIVE_MOTOR_COUNT, "dq"),
            "kp": _vector(kp, ACTIVE_MOTOR_COUNT, "kp"),
            "kd": _vector(kd, ACTIVE_MOTOR_COUNT, "kd"),
            "tau": _vector(tau, ACTIVE_MOTOR_COUNT, "tau"),
        }
        command = self._command_factory()
        slots = tuple(command.motor_cmd)
        if len(slots) != DDS_MOTOR_SLOT_COUNT:
            raise Go2SDKError("pinned LowCmd_ factory did not create 20 motor slots")
        for index, slot in enumerate(slots):
            slot.mode = ACTIVE_MODE
            if index < ACTIVE_MOTOR_COUNT:
                for field_name, field_values in values.items():
                    setattr(slot, field_name, field_values[index])
            else:
                for field_name, value in INACTIVE_SAFE_FIELDS.items():
                    setattr(slot, field_name, value)
        command.crc = int(self._crc.Crc(command)) & 0xFFFFFFFF
        calculated_crc = int(self._crc.Crc(command)) & 0xFFFFFFFF
        self._last_command_type_verified = isinstance(command, self._lowcmd_type)
        self._last_command_crc_verified = command.crc == calculated_crc
        if not self._last_command_type_verified or not self._last_command_crc_verified:
            raise Go2SDKError("constructed LowCmd_ failed pinned type or CRC verification")
        self._publisher.Write(command)
        self._last_command = command
        self._command_count += 1

    def get_low_state(self) -> object | None:
        self._require_started()
        with self._lock:
            return self._low_state

    def get_sport_mode_state(self) -> object | None:
        self._require_started()
        with self._lock:
            return self._sport_mode_state

    def _on_low_state(self, message: object) -> None:  # pragma: no cover - DDS callback
        with self._lock:
            self._low_state = message
            self._low_state_publications += 1

    def _on_sport_mode_state(self, message: object) -> None:  # pragma: no cover - DDS callback
        with self._lock:
            self._sport_mode_state = message
            self._sport_mode_state_publications += 1

    def _require_started(self) -> None:
        if not self._started or self._closed:
            raise Go2SDKError("SDK2 façade is not started")

    def _close_endpoints(self) -> None:
        for name in ("_publisher", "_lowstate_subscriber", "_sport_subscriber"):
            endpoint = getattr(self, name, None)
            close = getattr(endpoint, "Close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def close(self) -> None:  # pragma: no cover - exercised by Linux route
        if self._closed:
            return
        self._close_endpoints()
        self._started = False
        self._closed = True

    def __enter__(self) -> "UnitreeGo2LowLevelSDK":
        if not self._started:
            self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        self.close()
        return False


def _mapping_value(value: Any, names: Sequence[str], default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return default


def _safe_name(mj: Any, model: Any, kind: Any, index: int) -> str:
    try:
        value = mj.mj_id2name(model, kind, index)
    except Exception:
        value = None
    return str(value or "")


class UnitreeGo2EvaluationRobotSession:
    """One real SDK2 → DDS → Go2 bridge → MuJoCo evaluation session."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        backend: Go2Backend | None = None,
        transport: Go2Transport | None = None,
        bridge: Go2DDSMuJoCoBridge | None = None,
        sdk: Any | None = None,
        backend_factory: Callable[[str | Path], Go2Backend] | None = None,
        transport_factory: Callable[[], Go2Transport] | None = None,
        bridge_factory: Callable[[Go2Backend, Go2Transport], Go2DDSMuJoCoBridge] | None = None,
        truth_provider: Go2TruthProvider | None = None,
        frame_capture_factory: FrameCaptureFactory | None = None,
        render_rgb: Callable[[], Any] | None = None,
        video_profile: FrozenVideoProfile | Mapping[str, Any] | None = None,
        task_instances_path: str | Path | None = None,
        rollout_steps: int = DEFAULT_ROLLOUT_STEPS,
        max_rollout_steps: int = DEFAULT_MAX_ROLLOUT_STEPS,
        state_wait_s: float = DEFAULT_STATE_WAIT_S,
        reset_tolerance: float = RESET_ABSOLUTE_TOLERANCE,
        auto_start: bool = True,
    ) -> None:
        if backend is None and model_path is None:
            model_path = os.environ.get("AUTOADAPTER_GO2_MODEL")
        if backend is None and model_path is None:
            raise Go2SessionError("Go2 session requires the pinned MuJoCo scene model path")
        if isinstance(rollout_steps, bool) or not isinstance(rollout_steps, int) or rollout_steps <= 0:
            raise Go2SessionError("rollout_steps must be a positive integer")
        if isinstance(max_rollout_steps, bool) or not isinstance(max_rollout_steps, int) or max_rollout_steps < rollout_steps:
            raise Go2SessionError("max_rollout_steps must cover rollout_steps")
        state_wait_s = _finite(state_wait_s, "state_wait_s")
        reset_tolerance = _finite(reset_tolerance, "reset_tolerance")
        if state_wait_s < 0 or reset_tolerance < 0:
            raise Go2SessionError("state_wait_s and reset_tolerance must be non-negative")

        if bridge is not None:
            if backend is None:
                backend = getattr(bridge, "_backend", None)
            if transport is None:
                transport = getattr(bridge, "transport", None)
        if backend is None:
            factory = backend_factory or (lambda path: MuJoCoGo2Backend(path))
            backend = factory(model_path)  # type: ignore[arg-type]
        if transport is None:
            transport = (transport_factory or UnitreeSDK2Transport)()
        if bridge is None:
            bridge = (bridge_factory or Go2DDSMuJoCoBridge)(backend, transport)
        if sdk is None:
            sdk = UnitreeGo2LowLevelSDK()

        self._model_path = Path(model_path).resolve() if model_path is not None else None
        self._backend = backend
        self._transport = transport
        self._bridge = bridge
        self._sdk = sdk
        self._truth_provider = truth_provider
        self._video_profile = _profile(video_profile)
        self._frame_capture_factory = frame_capture_factory or MuJoCoFrameCapture
        self._render_rgb = render_rgb or self._build_renderer()
        self._renderer: Any | None = getattr(self, "_renderer", None)
        self._task_instances = self._load_task_instances(task_instances_path)
        self._rollout_steps = rollout_steps
        self._max_rollout_steps = max_rollout_steps
        self._state_wait_s = state_wait_s
        self._reset_tolerance = reset_tolerance

        self._started = False
        self._closed = False
        self._reset_time_s = 0.0
        self._execution_id: str | None = None
        self._phase: str | None = None
        self._task_id: str | None = None
        self._initial_state: dict[str, Any] = {}
        self._start_truth: Go2TruthSample | None = None
        self._standing_height_m = DEFAULT_STANDING_HEIGHT_M
        self._forward_axis = (1.0, 0.0)
        self._lateral_axis = (0.0, 1.0)
        self._reset_verification: dict[str, Any] = {}
        self._truth_samples: list[Go2TruthSample] = []
        self._last_bridge_result: dict[str, Any] = {}
        self._accepted_baseline = 0
        self._command_baseline = 0
        self._lowstate_baseline = 0
        self._sportstate_baseline = 0
        self._recording = False
        self._recording_phase: str | None = None
        self._recording_execution_id: str | None = None
        self._recording_frames: list[RGBFrame] = []
        self._next_frame_time_s: float | None = None
        self._last_route_evidence: dict[str, Any] = {}

        if auto_start:
            try:
                self.start()
            except Exception:
                self.close()
                raise

    @property
    def sdk(self) -> object:
        return self._sdk

    @property
    def simulation_time_s(self) -> float:
        value = float(self._backend.simulation_time)
        if not math.isfinite(value) or value < 0:
            raise Go2SessionError("Go2 simulation time must be finite and non-negative")
        return value

    @property
    def evidence_scope(self) -> str:
        return "SDK_GROUNDED_SIMULATION"

    @property
    def robot_model_id(self) -> str:
        return ROBOT_MODEL_ID

    @property
    def robot_configuration_id(self) -> str:
        return ROBOT_CONFIGURATION_ID

    @property
    def reset_verification(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._reset_verification)

    @property
    def start_position(self) -> tuple[float, float, float] | None:
        return self._start_truth.body_position if self._start_truth is not None else None

    @property
    def start_yaw_frame(self) -> Mapping[str, tuple[float, float]]:
        return {"forward": self._forward_axis, "lateral": self._lateral_axis}

    @property
    def standing_height_m(self) -> float:
        return self._standing_height_m

    @property
    def route_evidence(self) -> Mapping[str, Any]:
        return copy.deepcopy(self._last_route_evidence)

    def start(self) -> None:
        if self._closed:
            raise Go2SessionError("Go2 session is closed")
        if self._started:
            return
        bridge_started = bool(getattr(self._bridge, "_started", False))
        try:
            if not bridge_started:
                self._bridge.start()
            sdk_started = bool(getattr(self._sdk, "started", False))
            if not sdk_started:
                start = getattr(self._sdk, "start", None)
                if not callable(start):
                    raise Go2SessionError("SDK façade does not implement start")
                start()
            self._started = True
        except Exception:
            self._started = False
            raise

    def reset(
        self,
        *,
        phase: str,
        execution_id: str,
        initial_state: Mapping[str, Any],
    ) -> None:
        self._require_started()
        phase = _text(phase, "phase")
        execution_id = _text(execution_id, "execution_id")
        if not isinstance(initial_state, Mapping):
            raise Go2SessionError("initial_state must be a mapping")
        if self._recording:
            raise Go2SessionError("reset cannot interrupt an active external recording")

        self._phase = phase
        self._execution_id = execution_id
        self._initial_state = copy.deepcopy(dict(initial_state))
        task_id = self._initial_state.get("task_id")
        self._task_id = task_id if isinstance(task_id, str) and task_id in self._task_instances else None
        instance = self._task_instances.get(self._task_id or "")
        self._standing_height_m = self._instance_number(
            instance, "standing_height_m", DEFAULT_STANDING_HEIGHT_M
        )

        # The bridge reset is the only state mutation performed by this layer.
        self._bridge.reset()
        first_qpos, first_qvel = self._complete_state()
        self._bridge.reset()
        second_qpos, second_qvel = self._complete_state()
        qpos_error = self._max_abs_difference(first_qpos, second_qpos)
        qvel_error = self._max_abs_difference(first_qvel, second_qvel)
        if qpos_error > self._reset_tolerance or qvel_error > self._reset_tolerance:
            raise Go2SessionError(
                "repeated Go2 reset states differ beyond the frozen tolerance"
            )
        if abs(self.simulation_time_s) > self._reset_tolerance:
            raise Go2SessionError("Go2 reset did not restore simulation time to zero")
        self._reset_verification = {
            "verified": True,
            "qpos_length": len(first_qpos),
            "qvel_length": len(first_qvel),
            "max_qpos_error": qpos_error,
            "max_qvel_error": qvel_error,
            "simulation_time_s": self.simulation_time_s,
        }

        self._reset_time_s = self.simulation_time_s
        self._truth_samples.clear()
        self._last_bridge_result = {}
        self._accepted_baseline = self._bridge_accepted_count()
        self._command_baseline = self._sdk_count("command_count")
        self._lowstate_baseline = self._sdk_count("low_state_publications")
        self._sportstate_baseline = self._sdk_count("sport_mode_state_publications")
        self._last_route_evidence = {}
        start = self._record_truth()
        self._start_truth = start
        self._forward_axis, self._lateral_axis = initial_body_yaw_frame(start.body_x_axis)

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        self._require_started()
        phase = _text(phase, "recording phase")
        execution_id = _text(execution_id, "recording execution_id")
        if self._recording:
            raise Go2SessionError("external recording is already active")
        if self._start_truth is None:
            raise Go2SessionError("recording requires a verified reset")
        self._recording = True
        self._recording_phase = phase
        self._recording_execution_id = execution_id
        self._recording_frames.clear()
        self._next_frame_time_s = self.simulation_time_s
        self._capture_frame(force=True)

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        if not self._recording:
            return ()
        self._capture_frame(force=True)
        frames = tuple(self._recording_frames)
        self._recording = False
        self._recording_phase = None
        self._recording_execution_id = None
        self._recording_frames.clear()
        self._next_frame_time_s = None
        return frames

    def invoke(
        self,
        candidate: ValidatedCandidateHandle,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self._require_started()
        if not isinstance(candidate, ValidatedCandidateHandle) and not callable(
            getattr(candidate, "_invoke", None)
        ):
            raise Go2SessionError("invoke requires an A-validated candidate handle")
        capability_id = _text(capability_id, "capability_id")
        if not isinstance(arguments, Mapping):
            raise Go2SessionError("capability arguments must be a mapping")
        invoke = getattr(candidate, "_invoke", None)
        if not callable(invoke):
            raise Go2SessionError("candidate does not expose the Framework invocation boundary")
        result = invoke(capability_id, copy.deepcopy(dict(arguments)), self._sdk)
        if not isinstance(result, Mapping):
            raise Go2SessionError("candidate result must be a mapping")

        steps = self._steps_for_invocation(arguments)
        for _ in range(steps):
            self._step_physics()
            if (
                getattr(self._sdk, "is_real_sdk", False)
                and not self._last_bridge_result.get("accepted", False)
                and self.simulation_time_s - self._reset_time_s < 0.1
            ):
                # DDS delivery is asynchronous.  A bounded poll advances only
                # the existing bridge clock; it never fabricates a command.
                deadline = time.monotonic() + self._state_wait_s
                while (
                    not self._last_bridge_result.get("accepted", False)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.001)
                    self._step_physics()
        return copy.deepcopy(dict(result))

    def validation_evidence(self, invocation: HarnessInvocation) -> ValidationEvidence:
        self._require_started()
        if not isinstance(invocation, HarnessInvocation):
            raise Go2SessionError("validation evidence requires a typed Harness invocation")
        if self._start_truth is None:
            raise Go2SessionError("validation evidence requires a verified reset")
        self._wait_for_sdk_state()
        if not self._truth_samples:
            self._record_truth()
        route = self._route_evidence()
        metric = invocation.metric if isinstance(invocation.metric, str) else "body_height_m"
        samples = tuple(
            MeasurementSample(
                max(0.0, sample.time_s - self._reset_time_s),
                self._metric_value(sample, metric),
            )
            for sample in self._truth_samples
        )
        finite = all(sample.finite_required_state for sample in self._truth_samples)
        no_contact = all(
            sample.body_floor_contact is False and sample.head_floor_contact is False
            for sample in self._truth_samples
        )
        guards = self._guard_results(
            requested=invocation.guard_ids,
            finite=finite,
            no_contact=no_contact,
            route_verified=bool(route["verified"]),
        )
        self._last_route_evidence = route
        return Go2ValidationEvidence(
            samples=samples,
            elapsed_s=max(0.0, self.simulation_time_s - self._reset_time_s),
            guard_results=guards,
            sdk_route_verified=bool(route["verified"]),
            route_evidence=copy.deepcopy(route),
        )

    def demo_evidence(self, task_id: str) -> Mapping[str, Any]:
        self._require_started()
        task_id = _text(task_id, "task_id")
        if task_id not in {"G01", "G02", "G03", "G04", "G05"}:
            raise Go2SessionError("Go2 fixed Demo evidence only supports G01 through G05")
        if self._start_truth is None:
            raise Go2SessionError("Demo evidence requires a verified reset")
        # The fixed task identity is supplied by the trusted Harness.  It is
        # safe to select only immutable private parameters here; it never
        # changes the reset state or candidate command route.
        self._task_id = task_id
        self._standing_height_m = self._instance_number(
            self._task_instances.get(task_id),
            "standing_height_m",
            self._standing_height_m,
        )
        if not self._truth_samples:
            self._record_truth()
        route = self._route_evidence()
        finite = all(sample.finite_required_state for sample in self._truth_samples)
        no_contact = all(
            sample.body_floor_contact is False and sample.head_floor_contact is False
            for sample in self._truth_samples
        )
        sample_records = [self._sample_record(sample) for sample in self._truth_samples]
        result: dict[str, Any] = {
            "task_id": task_id,
            "standing_height_m": self._standing_height_m,
            "start_position_m": list(self._start_truth.body_position),
            "start_frame": {
                "forward": list(self._forward_axis),
                "lateral": list(self._lateral_axis),
            },
            "samples": sample_records,
            "guards": {
                "finite_required_state": finite,
                "no_body_or_head_floor_contact": no_contact,
                "sdk_route_verified": bool(route["verified"]),
            },
            "route_evidence": copy.deepcopy(route),
            "simulation_time_start_s": self._reset_time_s,
            "simulation_time_end_s": self.simulation_time_s,
        }
        if task_id == "G04":
            instance = self._task_instances.get(task_id, {})
            dwell = self._instance_number(instance, "final_stop_dwell_s", 0.5)
            end_time = self._truth_samples[-1].time_s
            result["motion_samples"] = sample_records[1:]
            result["terminal_stop_samples"] = [
                item for item in sample_records
                if float(item["simulation_time_s"]) >= end_time - dwell - 1e-12
            ]
            result["final_stop_dwell_s"] = dwell
        if task_id == "G05":
            instance = self._task_instances.get(task_id, {})
            result["target_body_height_m"] = self._instance_number(
                instance, "target_body_height_m", self._standing_height_m
            )
        return result

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self._recording:
            try:
                self.stop_external_recording()
            except BaseException as exc:
                errors.append(exc)
        try:
            close_sdk = getattr(self._sdk, "close", None)
            if callable(close_sdk):
                close_sdk()
        except BaseException as exc:
            errors.append(exc)
        finally:
            try:
                self._bridge.close()
            except BaseException as exc:
                errors.append(exc)
            renderer = self._renderer
            close_renderer = getattr(renderer, "close", None)
            if callable(close_renderer):
                try:
                    close_renderer()
                except BaseException as exc:
                    errors.append(exc)
            self._started = False
            self._closed = True
        if errors:
            raise Go2SessionError("Go2 session cleanup failed") from errors[0]

    def __enter__(self) -> "UnitreeGo2EvaluationRobotSession":
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        self.close()
        return False

    def _require_started(self) -> None:
        if not self._started or self._closed:
            raise Go2SessionError("Go2 session is not started")

    def _complete_state(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        full_state = getattr(self._backend, "full_state", None)
        if callable(full_state):
            qpos, qvel = full_state()
            return tuple(_finite(value, "qpos") for value in qpos), tuple(
                _finite(value, "qvel") for value in qvel
            )
        sensors = self._backend.sensors()
        q = _mapping_value(sensors, ("q", "position", "joint_position"))
        dq = _mapping_value(sensors, ("dq", "velocity", "joint_velocity"))
        if q is None or dq is None:
            raise Go2SessionError("Go2 backend does not expose a complete reset state")
        return tuple(_finite(value, "qpos") for value in q), tuple(
            _finite(value, "qvel") for value in dq
        )

    @staticmethod
    def _max_abs_difference(first: Sequence[float], second: Sequence[float]) -> float:
        if len(first) != len(second):
            raise Go2SessionError("repeated reset state vector lengths differ")
        differences = [abs(float(a) - float(b)) for a, b in zip(first, second)]
        if not all(math.isfinite(value) for value in differences):
            raise Go2SessionError("repeated reset state contains non-finite values")
        return max(differences, default=0.0)

    def _steps_for_invocation(self, arguments: Mapping[str, Any]) -> int:
        duration = None
        for name in ("duration_s", "horizon_s", "rollout_s"):
            if name in arguments:
                duration = _finite(arguments[name], name)
                if duration < 0:
                    raise Go2SessionError(f"{name} must be non-negative")
                break
        if duration is None:
            steps = self._rollout_steps
        else:
            steps = max(1, math.ceil(duration / float(self._backend.timestep)))
        if steps > self._max_rollout_steps:
            raise Go2SessionError("candidate rollout exceeds the frozen session limit")
        return steps

    def _step_physics(self) -> None:
        result = self._bridge.step()
        self._last_bridge_result = dict(result) if isinstance(result, Mapping) else {}
        self._record_truth()
        self._capture_frame(force=False)

    def _record_truth(self) -> Go2TruthSample:
        raw = None
        if self._truth_provider is not None:
            try:
                raw = self._truth_provider(self._backend)
            except TypeError:
                raw = self._truth_provider()  # type: ignore[call-arg]
        if raw is None:
            provider = getattr(self._backend, "truth_snapshot", None)
            if callable(provider):
                raw = provider()
        sample = self._coerce_truth(raw) if raw is not None else self._mujoco_truth()
        self._truth_samples.append(sample)
        return sample

    def _coerce_truth(self, raw: Mapping[str, Any] | Go2TruthSample) -> Go2TruthSample:
        if isinstance(raw, Go2TruthSample):
            return raw
        if not isinstance(raw, Mapping):
            raise Go2SessionError("truth provider must return a mapping or Go2TruthSample")
        position_raw = _mapping_value(raw, ("body_position", "position"))
        velocity_raw = _mapping_value(raw, ("body_linear_velocity", "linear_velocity", "velocity"))
        if position_raw is None or velocity_raw is None:
            raise Go2SessionError("truth provider omitted body position or velocity")
        position = _vector(position_raw, 3, "body position")
        velocity = _vector(velocity_raw, 3, "body linear velocity")
        x_axis_raw = _mapping_value(raw, ("body_x_axis", "x_axis"))
        z_axis_raw = _mapping_value(raw, ("body_z_axis", "z_axis"))
        quaternion = _mapping_value(raw, ("body_quaternion", "quaternion"))
        rotation = _mapping_value(raw, ("body_rotation_matrix", "rotation_matrix", "xmat"))
        if x_axis_raw is None or z_axis_raw is None:
            if rotation is not None:
                x_axis_raw, z_axis_raw = _matrix_axes(rotation)
            elif quaternion is not None:
                x_axis_raw, z_axis_raw = _quaternion_axes(quaternion)
            else:
                raise Go2SessionError("truth provider omitted body orientation")
        x_axis = _unit(_vector(x_axis_raw, 3, "body x axis"), "body x axis")
        z_axis = _unit(_vector(z_axis_raw, 3, "body z axis"), "body z axis")
        floor_z = self._floor_z()
        height_raw = _mapping_value(raw, ("body_height_m", "body_height"))
        height = (
            _finite(height_raw, "body height")
            if height_raw is not None
            else _finite(position[2] - floor_z, "body height")
        )
        upright = _finite(
            _mapping_value(raw, ("upright_score",), upright_score(z_axis)),
            "upright score",
        )
        speed_raw = _mapping_value(raw, ("planar_speed_m_s", "planar_speed"))
        speed = (
            _finite(speed_raw, "planar speed")
            if speed_raw is not None
            else math.hypot(velocity[0], velocity[1])
        )
        body_contact = _mapping_value(raw, ("body_floor_contact", "body_contact"))
        head_contact = _mapping_value(raw, ("head_floor_contact", "head_contact"))
        if body_contact is not None and not isinstance(body_contact, bool):
            raise Go2SessionError("body_floor_contact must be boolean or unavailable")
        if head_contact is not None and not isinstance(head_contact, bool):
            raise Go2SessionError("head_floor_contact must be boolean or unavailable")
        finite = bool(_mapping_value(raw, ("finite_required_state",), True))
        contacts_available = bool(
            _mapping_value(raw, ("contact_observation_available",), body_contact is not None and head_contact is not None)
        )
        return Go2TruthSample(
            time_s=self.simulation_time_s,
            body_position=(position[0], position[1], position[2]),
            body_x_axis=(x_axis[0], x_axis[1], x_axis[2]),
            body_z_axis=(z_axis[0], z_axis[1], z_axis[2]),
            body_linear_velocity=(velocity[0], velocity[1], velocity[2]),
            body_height_m=height,
            upright_score=upright,
            planar_speed_m_s=speed,
            body_floor_contact=body_contact,
            head_floor_contact=head_contact,
            finite_required_state=finite,
            contact_observation_available=contacts_available,
            extra=copy.deepcopy(dict(raw)),
        )

    def _mujoco_truth(self) -> Go2TruthSample:  # pragma: no cover - exercised by Linux route
        model = getattr(self._backend, "model", None)
        data = getattr(self._backend, "data", None)
        mj = getattr(self._backend, "_mj", None)
        if model is None or data is None or mj is None:
            sensors = self._backend.sensors()
            return self._coerce_truth({
                "body_position": sensors.frame_position,
                "body_linear_velocity": sensors.frame_linear_velocity,
                "body_quaternion": sensors.imu_quaternion,
                "body_floor_contact": None,
                "head_floor_contact": None,
                "contact_observation_available": False,
            })
        body_id = self._root_body_id(mj, model)
        position = tuple(float(value) for value in data.xpos[body_id])
        xmat = tuple(float(value) for value in data.xmat[body_id])
        velocity = tuple(float(value) for value in data.qvel[:3])
        floor_ids = self._floor_geom_ids(mj, model)
        body_ids, head_ids = self._guard_geom_ids(mj, model, body_id)
        body_contact, head_contact = self._floor_contacts(data, floor_ids, body_ids, head_ids)
        floor_z = self._floor_z(model=model, data=data, floor_ids=floor_ids)
        return self._coerce_truth({
            "body_position": position,
            "body_linear_velocity": velocity,
            "body_rotation_matrix": xmat,
            "body_height_m": position[2] - floor_z,
            "body_floor_contact": body_contact,
            "head_floor_contact": head_contact,
            "contact_observation_available": bool(floor_ids and body_ids),
        })

    def _root_body_id(self, mj: Any, model: Any) -> int:
        for name in ("base", "trunk", "body"):
            try:
                identifier = int(mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, name))
            except Exception:
                identifier = -1
            if identifier >= 0:
                return identifier
        body_ids = getattr(model, "jnt_bodyid", ())
        if len(body_ids):
            return int(body_ids[0])
        raise Go2SessionError("Go2 MuJoCo model has no root body")

    def _floor_geom_ids(self, mj: Any, model: Any) -> set[int]:
        result: set[int] = set()
        geom_count = int(getattr(model, "ngeom", 0))
        plane_type = getattr(getattr(mj, "mjtGeom", object()), "mjGEOM_PLANE", None)
        for index in range(geom_count):
            name = _safe_name(mj, model, mj.mjtObj.mjOBJ_GEOM, index).lower()
            body_id = int(getattr(model, "geom_bodyid", [0] * geom_count)[index])
            geom_type = getattr(model, "geom_type", [None] * geom_count)[index]
            if body_id == 0 and (
                any(token in name for token in ("floor", "ground", "plane"))
                or plane_type is not None and geom_type == plane_type
            ):
                result.add(index)
        return result

    def _guard_geom_ids(self, mj: Any, model: Any, root_body_id: int) -> tuple[set[int], set[int]]:
        geom_count = int(getattr(model, "ngeom", 0))
        body_count = int(getattr(model, "nbody", 0))
        parent = getattr(model, "body_parentid", [0] * body_count)
        descendants: set[int] = set()
        for body_id in range(body_count):
            current = body_id
            while current >= 0:
                if current == root_body_id:
                    descendants.add(body_id)
                    break
                if current >= len(parent):
                    break
                next_body = int(parent[current])
                if next_body == current:
                    break
                current = next_body
        body_ids: set[int] = set()
        head_ids: set[int] = set()
        for index in range(geom_count):
            geom_body = int(getattr(model, "geom_bodyid", [0] * geom_count)[index])
            if geom_body not in descendants:
                continue
            name = _safe_name(mj, model, mj.mjtObj.mjOBJ_GEOM, index).lower()
            if any(token in name for token in ("head", "neck")):
                head_ids.add(index)
            elif any(token in name for token in ("foot", "toe", "calf", "ankle")):
                continue
            elif any(token in name for token in ("base", "body", "trunk", "chest")) or geom_body == root_body_id:
                body_ids.add(index)
        return body_ids, head_ids

    @staticmethod
    def _floor_contacts(
        data: Any,
        floor_ids: set[int],
        body_ids: set[int],
        head_ids: set[int],
    ) -> tuple[bool | None, bool | None]:
        if not floor_ids or not body_ids:
            return None, None
        body_contact = False
        head_contact = False
        for index in range(int(getattr(data, "ncon", 0))):
            contact = data.contact[index]
            geom_pair = {int(contact.geom1), int(contact.geom2)}
            if not geom_pair.intersection(floor_ids):
                continue
            if geom_pair.intersection(body_ids):
                body_contact = True
            if geom_pair.intersection(head_ids):
                head_contact = True
        return body_contact, head_contact

    def _floor_z(
        self,
        *,
        model: Any | None = None,
        data: Any | None = None,
        floor_ids: set[int] | None = None,
    ) -> float:
        instance = self._task_instances.get(self._task_id or "", {})
        configured = self._instance_number(instance, "floor_z_m", DEFAULT_FLOOR_Z_M)
        if model is None or data is None or not floor_ids:
            return configured
        try:
            values = [float(data.geom_xpos[index][2]) for index in floor_ids]
            if values and all(math.isfinite(value) for value in values):
                return values[0]
        except Exception:
            pass
        return configured

    def _instance_number(self, instance: Mapping[str, Any] | None, key: str, default: float) -> float:
        if not isinstance(instance, Mapping):
            return default
        parameters = instance.get("parameters", instance)
        if not isinstance(parameters, Mapping) or key not in parameters:
            return default
        value = _finite(parameters[key], f"task instance {key}")
        return value

    def _load_task_instances(self, path: str | Path | None) -> dict[str, dict[str, Any]]:
        if path is None:
            path = (
                Path(__file__).resolve().parents[4]
                / "libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
            )
        path = Path(path)
        if not path.is_file():
            raise Go2SessionError(f"Go2 private task instances are missing: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Go2SessionError("Go2 private task instances are not valid JSON") from exc
        if not isinstance(value, Mapping) or value.get("artifact_type") != "task_instances_private":
            raise Go2SessionError("Go2 private task instances have the wrong artifact type")
        raw_instances = value.get("instances")
        if not isinstance(raw_instances, list):
            raise Go2SessionError("Go2 private task instances must contain instances")
        result: dict[str, dict[str, Any]] = {}
        for item in raw_instances:
            if not isinstance(item, Mapping) or not isinstance(item.get("task_id"), str):
                raise Go2SessionError("Go2 private task instance is malformed")
            task_id = item["task_id"]
            if task_id in result:
                raise Go2SessionError("Go2 private task instance IDs must be unique")
            result[task_id] = copy.deepcopy(dict(item))
        expected = {"G01", "G02", "G03", "G04", "G05"}
        if set(result) != expected:
            raise Go2SessionError("Go2 private task instances must cover exactly G01 through G05")
        return result

    def _build_renderer(self) -> Callable[[], Any]:  # pragma: no cover - exercised by Linux route
        custom = getattr(self._backend, "render_rgb", None)
        if callable(custom):
            return custom
        model = getattr(self._backend, "model", None)
        data = getattr(self._backend, "data", None)
        if model is None or data is None:
            # Explicit injected test backends may exercise reset, route, or
            # close without opening a renderer.  Formal sessions always take
            # the MuJoCo branch above; the test-only byte source is never
            # used by the real SDK route.
            return lambda: bytes(self._video_profile.width * self._video_profile.height * 3)
        try:
            import mujoco
        except ImportError as exc:
            raise Go2SessionError("MuJoCo rendering requires the pinned mujoco runtime") from exc
        try:
            renderer = mujoco.Renderer(
                model,
                height=self._video_profile.height,
                width=self._video_profile.width,
            )
        except Exception as exc:
            raise Go2SessionError("could not construct the MuJoCo external renderer") from exc
        self._renderer = renderer

        def render() -> bytes:
            try:
                renderer.update_scene(data, camera=self._video_profile.camera)
            except Exception:
                renderer.update_scene(data)
            pixels = renderer.render()
            if hasattr(pixels, "tobytes"):
                return pixels.tobytes()
            if isinstance(pixels, bytes):
                return pixels
            raise Go2SessionError("MuJoCo renderer did not return RGB bytes")

        return render

    def _capture_frame(self, *, force: bool) -> None:
        if not self._recording:
            return
        current = self.simulation_time_s
        if not force and self._next_frame_time_s is not None and current + 1e-12 < self._next_frame_time_s:
            return
        if self._recording_frames and self._recording_frames[-1].simulation_time_s == current:
            return
        result = self._frame_capture_factory(self._video_profile, self._render_rgb, current)
        if hasattr(result, "capture") and callable(result.capture):
            result = result.capture()
        if callable(result) and not isinstance(result, (bytes, bytearray)):
            result = result()
        frame = self._coerce_frame(result, current)
        self._recording_frames.append(frame)
        self._next_frame_time_s = current + 1.0 / self._video_profile.fps

    def _coerce_frame(self, value: Any, simulation_time: float) -> RGBFrame:
        if isinstance(value, RGBFrame):
            if value.simulation_time_s == simulation_time:
                return value
            return RGBFrame(simulation_time, value.width, value.height, value.rgb)
        if isinstance(value, Mapping):
            width = int(value.get("width", self._video_profile.width))
            height = int(value.get("height", self._video_profile.height))
            rgb = value.get("rgb")
        else:
            width = self._video_profile.width
            height = self._video_profile.height
            rgb = value
        if hasattr(rgb, "tobytes") and callable(rgb.tobytes):
            rgb = rgb.tobytes()
        if not isinstance(rgb, bytes):
            raise Go2SessionError("MuJoCoFrameCapture did not return an RGBFrame")
        return RGBFrame(simulation_time, width, height, rgb)

    def _capture_metrics(self, sample: Go2TruthSample) -> tuple[float, float, float]:
        if self._start_truth is None:
            return 0.0, 0.0, 0.0
        return start_frame_displacement(
            self._start_truth.body_position,
            sample.body_position,
            self._forward_axis,
            self._lateral_axis,
        )

    def _sample_record(self, sample: Go2TruthSample) -> dict[str, Any]:
        forward, lateral, drift = self._capture_metrics(sample)
        return {
            "simulation_time_s": max(0.0, sample.time_s - self._reset_time_s),
            "body_height_m": sample.body_height_m,
            "upright_score": sample.upright_score,
            "planar_speed_m_s": sample.planar_speed_m_s,
            "forward_displacement_m": forward,
            "lateral_displacement_m": lateral,
            "absolute_lateral_displacement_m": abs(lateral),
            "horizontal_drift_m": drift,
            "body_height_to_standing_height_ratio": sample.body_height_m / self._standing_height_m,
            "absolute_body_height_error_m": abs(
                sample.body_height_m
                - self._instance_number(
                    self._task_instances.get(self._task_id or ""),
                    "target_body_height_m",
                    self._standing_height_m,
                )
            ),
            "body_floor_contact": sample.body_floor_contact,
            "head_floor_contact": sample.head_floor_contact,
            "finite_required_state": sample.finite_required_state,
            "contact_observation_available": sample.contact_observation_available,
        }

    def _metric_value(self, sample: Go2TruthSample, metric: str) -> float:
        forward, lateral, drift = self._capture_metrics(sample)
        values: dict[str, float] = {
            "body_height_m": sample.body_height_m,
            "upright_score": sample.upright_score,
            "planar_speed_m_s": sample.planar_speed_m_s,
            "forward_displacement_m": forward,
            "lateral_displacement_m": lateral,
            "absolute_lateral_displacement_m": abs(lateral),
            "horizontal_drift_m": drift,
            "body_height_to_standing_height_ratio": sample.body_height_m / self._standing_height_m,
            "absolute_body_height_error_m": abs(
                sample.body_height_m
                - self._instance_number(
                    self._task_instances.get(self._task_id or ""),
                    "target_body_height_m",
                    self._standing_height_m,
                )
            ),
        }
        if metric in values:
            return float(values[metric])
        if metric in sample.extra:
            return _finite(sample.extra[metric], f"truth metric {metric}")
        raise Go2SessionError(f"unsupported private Go2 truth metric {metric!r}")

    def _sample_finite(self, sample: Go2TruthSample) -> bool:
        values = (
            sample.time_s,
            *sample.body_position,
            *sample.body_x_axis,
            *sample.body_z_axis,
            *sample.body_linear_velocity,
            sample.body_height_m,
            sample.upright_score,
            sample.planar_speed_m_s,
        )
        return sample.finite_required_state and all(math.isfinite(float(value)) for value in values)

    def _guard_results(
        self,
        *,
        requested: Sequence[str],
        finite: bool,
        no_contact: bool,
        route_verified: bool,
    ) -> dict[str, bool]:
        known = {
            "trusted-external-verdict": True,
            "finite-required-state": finite,
            "no-body-or-head-floor-contact": no_contact,
            "sdk-route": route_verified,
            "sdk-command-accepted": route_verified,
            "sdk-state-publication": route_verified,
        }
        result = dict(known)
        for guard_id in requested:
            if not isinstance(guard_id, str) or not guard_id:
                continue
            result[guard_id] = known.get(
                guard_id,
                finite and no_contact and route_verified,
            )
        return result

    def _bridge_accepted_count(self) -> int:
        value = self._last_bridge_result.get("accepted_commands")
        if value is None:
            value = getattr(self._bridge, "_accepted_commands", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _sdk_count(self, name: str) -> int:
        value = getattr(self._sdk, name, 0)
        if callable(value):
            value = value()
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _sdk_command_evidence(self) -> Mapping[str, Any]:
        value = getattr(self._sdk, "last_command_evidence", {})
        if callable(value):
            value = value()
        return dict(value) if isinstance(value, Mapping) else {}

    def _wait_for_sdk_state(self) -> None:
        low = getattr(self._sdk, "get_low_state", None)
        sport = getattr(self._sdk, "get_sport_mode_state", None)
        if not callable(low) or not callable(sport):
            return
        deadline = time.monotonic() + self._state_wait_s
        while time.monotonic() < deadline:
            try:
                if low() is not None and sport() is not None:
                    return
            except Exception:
                return
            if not getattr(self._sdk, "is_real_sdk", False):
                return
            time.sleep(0.001)

    def _state_observed(self) -> bool:
        low_count = self._sdk_count("low_state_publications")
        sport_count = self._sdk_count("sport_mode_state_publications")
        if low_count > self._lowstate_baseline and sport_count > self._sportstate_baseline:
            return True
        try:
            low = self._sdk.get_low_state()
            sport = self._sdk.get_sport_mode_state()
        except Exception:
            low = sport = None
        if low is not None and sport is not None:
            return True
        for name in ("lowstates", "low_states", "sportstates", "sport_states"):
            values = getattr(self._transport, name, None)
            if isinstance(values, Sequence) and len(values) > 0:
                if "sport" in name:
                    sport = values[-1]
                else:
                    low = values[-1]
        return low is not None and sport is not None

    def _route_evidence(self) -> dict[str, Any]:
        accepted_count = self._bridge_accepted_count()
        accepted_delta = accepted_count - self._accepted_baseline
        command_evidence = self._sdk_command_evidence()
        type_verified = bool(command_evidence.get("type_verified"))
        crc_verified = bool(command_evidence.get("crc_verified"))
        if accepted_delta > 0:
            # The bridge's accepted counter is only incremented after exact
            # DDS type, width, mode, finite-field, and CRC validation.
            type_verified = True if "type_verified" not in command_evidence else type_verified
            crc_verified = True if "crc_verified" not in command_evidence else crc_verified
        state_observed = self._state_observed()
        progress = self.simulation_time_s > self._reset_time_s + 1e-15
        verified = bool(
            accepted_delta > 0
            and type_verified
            and crc_verified
            and state_observed
            and progress
        )
        return {
            "verified": verified,
            "accepted_command_count": accepted_delta,
            "bridge_accepted_command_counter": accepted_count,
            "accepted_command_type_verified": type_verified,
            "accepted_command_crc_verified": crc_verified,
            "low_state_publications": max(0, self._sdk_count("low_state_publications") - self._lowstate_baseline),
            "sport_mode_state_publications": max(0, self._sdk_count("sport_mode_state_publications") - self._sportstate_baseline),
            "state_publication_observed": state_observed,
            "simulation_time_start_s": self._reset_time_s,
            "simulation_time_end_s": self.simulation_time_s,
            "simulation_time_progressed": progress,
            "lowcmd_message_type": command_evidence.get("message_type"),
        }


# Short aliases used by integration assembly and tests.
UnitreeGo2EvaluationSession = UnitreeGo2EvaluationRobotSession
Go2EvaluationRobotSession = UnitreeGo2EvaluationRobotSession
UnitreeGo2SDK = UnitreeGo2LowLevelSDK
UnitreeGo2SDKFacade = UnitreeGo2LowLevelSDK
Go2SDKFacade = UnitreeGo2LowLevelSDK


__all__ = [
    "Go2EvaluationRobotSession",
    "Go2SDKFacade",
    "Go2SessionError",
    "Go2SDKError",
    "Go2TruthSample",
    "Go2ValidationEvidence",
    "Go2Transport",
    "MuJoCoFrameCapture",
    "UnitreeGo2EvaluationRobotSession",
    "UnitreeGo2EvaluationSession",
    "UnitreeGo2LowLevelSDK",
    "UnitreeGo2SDK",
    "UnitreeGo2SDKFacade",
    "initial_body_yaw_frame",
    "start_frame_displacement",
    "upright_score",
]
