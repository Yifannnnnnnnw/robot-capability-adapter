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
import hashlib
import json
import math
import multiprocessing
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Protocol

from ...demo import ValidationEvidence
from ...evaluation import FrozenVideoProfile, RGBFrame
from ...validation import HarnessInvocation, MeasurementSample, ValidatedCandidateHandle
from .bridge import (
    Go2Backend,
    Go2DDSMuJoCoBridge,
    Go2Transport,
    MuJoCoGo2Backend,
    UnitreeSDK2Transport,
)


ROBOT_MODEL_ID = "unitree-go2"
ROBOT_CONFIGURATION_ID = "unitree-go2-stock-12dof"
RESET_ABSOLUTE_TOLERANCE = 1e-9
DEFAULT_ROLLOUT_STEPS = 1
DEFAULT_STATE_WAIT_S = 2.0
DEFAULT_MAX_ROLLOUT_STEPS = 5000
DEFAULT_INVOCATION_WINDOW_S = 0.25
DEFAULT_CANDIDATE_TIMEOUT_S = 8.0
DEFAULT_CANDIDATE_CANCEL_GRACE_S = 0.1
DEFAULT_FLOOR_Z_M = 0.0
DEFAULT_STANDING_HEIGHT_M = 0.34
GO2_DDS_DOMAIN = 1
GO2_DDS_INTERFACE = "lo"

# These are the immutable upstream and task/video bindings selected by the
# production Go2 factory.  The manifest and runtime lock are deliberately not
# constants here: a trusted launcher supplies the selected external files or
# their hashes, and this module verifies their current bytes and cross-file
# binding before constructing a live session.
GO2_TASK_INSTANCES_SHA256 = "b9755ee0b3e6f2317cceed08706e1f7883ee70d0fd96fbd7b99a08c6fb2d134a"
GO2_SCENE_SHA256 = "6c1fda780e7883665d1c84113b9275b6d448f586a8b1c110e438a37417cbccd0"
GO2_XML_SHA256 = "2014a3d76e30f17ab9447d8a67bd015291f74fa4d71ae30d005f1a32bd693d4b"
GO2_ASSET_CLOSURE_SHA256 = "f9966ae2644b65dd555cb6dde33fb0175bb5b3e18c3faf2a6f8ed95d5533e5f7"
GO2_RUNTIME_ID = "unitree-go2-linux-amd64"
GO2_RUNTIME_VERSION = "1.0.0"
GO2_EXPERIMENTAL_ARM64_RUNTIME_ID = "unitree-go2-linux-arm64-experimental"
GO2_EXPERIMENTAL_ARM64_RUNTIME_VERSION = "1.0.0"
GO2_EXPERIMENTAL_ARM64_RUNTIME_STATUS = "EXPERIMENTAL_FROZEN_FROM_VERIFIED_LINUX_ARM64_BUILD"
GO2_EXPERIMENTAL_ARM64_ENV = "AUTOADAPTER_GO2_EXPERIMENTAL_ARM64"
GO2_EXPERIMENTAL_ARM64_LOCK_ENV = "AUTOADAPTER_GO2_EXPERIMENTAL_RUNTIME_LOCK"
GO2_TASK_RELATIVE_PATH = (
    "general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
)
GO2_READINESS_PROFILE_RELATIVE_PATH = (
    "general_demo/contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json"
)


class Go2SessionError(RuntimeError):
    """A lifecycle, evidence, or SDK-session contract failure."""


class Go2SDKError(Go2SessionError):
    """The narrow SDK2 façade could not construct or use a real endpoint."""


def _bound_channel_factory_initialize(
    channel_factory_initialize: Callable[..., Any],
    *,
    domain: Any = GO2_DDS_DOMAIN,
    interface: Any = GO2_DDS_INTERFACE,
) -> Callable[[], Any]:
    """Bind the SDK initializer to the one approved Go2 DDS route."""

    if domain != GO2_DDS_DOMAIN or interface != GO2_DDS_INTERFACE:
        raise Go2SDKError("Go2 DDS ChannelFactoryInitialize route must be domain 1 on lo")

    def initialize() -> Any:
        return channel_factory_initialize(GO2_DDS_DOMAIN, GO2_DDS_INTERFACE)

    return initialize


def _experimental_arm64_enabled() -> bool:
    """Return true only for the explicit native arm64 experimental route."""

    return (
        os.environ.get(GO2_EXPERIMENTAL_ARM64_ENV) == "1"
        and sys.platform == "linux"
        and platform.machine().lower() == "aarch64"
    )


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


def _load_frame_capture_factory() -> FrameCaptureFactory:
    """Load the shared capture helper only when a recording is requested."""

    try:
        from ..session_support import MuJoCoFrameCapture
    except ImportError as exc:  # pragma: no cover - packaging/runtime failure
        raise Go2SessionError(
            "the shared integrations.session_support.MuJoCoFrameCapture is required"
        ) from exc
    return MuJoCoFrameCapture


class _UnitreeGo2SDKConnection:
    """Private owner of the real SDK2 DDS endpoints.

    The object itself never crosses into generated capability code.  Its
    ``binding`` is a plain namespace containing only upstream SDK symbols and
    the connected upstream publisher/subscriber/CRC instances.
    """

    is_real_sdk = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._low_state_publications = 0
        self._sport_mode_state_publications = 0
        self._binding: SimpleNamespace | None = None
        self._lowcmd_type: type[Any] | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def binding(self) -> object:
        self._require_started()
        assert self._binding is not None
        return self._binding

    @property
    def lowcmd_type(self) -> type[Any] | None:
        return self._lowcmd_type

    @property
    def low_state_publications(self) -> int:
        return self._low_state_publications

    @property
    def sport_mode_state_publications(self) -> int:
        return self._sport_mode_state_publications

    @property
    def candidate_worker_config(self) -> Mapping[str, Any]:
        return {"kind": "unitree_sdk2", "domain": 1, "interface": "lo"}

    def start(self) -> None:  # pragma: no cover - exercised by Linux route
        if self._started or self._closed:
            raise Go2SDKError("invalid SDK2 endpoint lifecycle")
        try:
            from unitree_sdk2py.core.channel import (
                ChannelFactoryInitialize,
                ChannelPublisher,
                ChannelSubscriber,
            )
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

        # The parent owns only Framework audit readers.  Candidate publishers
        # and polling readers are constructed in the separate worker process;
        # no C-backed SDK2/CycloneDDS endpoint crosses a process boundary.
        lowstate_audit_subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        sport_audit_subscriber = ChannelSubscriber("rt/sportmodestate", SportModeState_)
        try:
            lowstate_audit_subscriber.Init(self._on_low_state, 10)
            sport_audit_subscriber.Init(self._on_sport_mode_state, 10)
        except Exception:
            self._lowstate_audit_subscriber = lowstate_audit_subscriber
            self._sport_audit_subscriber = sport_audit_subscriber
            self._close_endpoints()
            raise
        self._lowstate_audit_subscriber = lowstate_audit_subscriber
        self._sport_audit_subscriber = sport_audit_subscriber
        self._lowcmd_type = LowCmd_
        self._binding = SimpleNamespace(
            ChannelFactoryInitialize=_bound_channel_factory_initialize(ChannelFactoryInitialize),
            ChannelPublisher=ChannelPublisher,
            ChannelSubscriber=ChannelSubscriber,
            LowCmd_=LowCmd_,
            LowState_=LowState_,
            SportModeState_=SportModeState_,
            CRC=CRC,
        )
        self._started = True

    def _on_low_state(self, _message: object) -> None:  # pragma: no cover - DDS callback
        with self._lock:
            self._low_state_publications += 1

    def _on_sport_mode_state(self, _message: object) -> None:  # pragma: no cover - DDS callback
        with self._lock:
            self._sport_mode_state_publications += 1

    def _require_started(self) -> None:
        if not self._started or self._closed:
            raise Go2SDKError("SDK2 endpoints are not started")

    def _close_endpoints(self) -> None:
        for name in (
            "_publisher",
            "_lowstate_audit_subscriber",
            "_sport_audit_subscriber",
        ):
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

    def __enter__(self) -> "_UnitreeGo2SDKConnection":
        if not self._started:
            self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        self.close()
        return False


def _candidate_binding_from_config(config: Mapping[str, Any]) -> tuple[object, Callable[[], None]]:
    """Construct candidate DDS objects inside the worker process only."""

    kind = config.get("kind")
    if kind == "unitree_sdk2":
        try:
            from unitree_sdk2py.core.channel import (
                ChannelFactoryInitialize,
                ChannelPublisher,
                ChannelSubscriber,
            )
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import (
                LowCmd_,
                LowState_,
                SportModeState_,
            )
            from unitree_sdk2py.utils.crc import CRC
        except ImportError as exc:  # pragma: no cover - Linux production route
            raise Go2SDKError("pinned worker SDK2 symbols are unavailable") from exc
        configured_factory_initialize = _bound_channel_factory_initialize(
            ChannelFactoryInitialize,
            domain=config.get("domain", GO2_DDS_DOMAIN),
            interface=config.get("interface", GO2_DDS_INTERFACE),
        )
        configured_factory_initialize()
        publisher = ChannelPublisher("rt/lowcmd", LowCmd_)
        lowstate = ChannelSubscriber("rt/lowstate", LowState_)
        sport = ChannelSubscriber("rt/sportmodestate", SportModeState_)
        endpoints = (publisher, lowstate, sport)
        try:
            publisher.Init()
            lowstate.Init()
            sport.Init()
        except BaseException:
            for endpoint in endpoints:
                close = getattr(endpoint, "Close", None)
                if callable(close):
                    close()
            raise
        binding = SimpleNamespace(
            ChannelFactoryInitialize=configured_factory_initialize,
            ChannelPublisher=ChannelPublisher,
            ChannelSubscriber=ChannelSubscriber,
            LowCmd_=LowCmd_,
            LowState_=LowState_,
            SportModeState_=SportModeState_,
            CRC=CRC,
            lowcmd_publisher=publisher,
            lowstate_subscriber=lowstate,
            sport_mode_state_subscriber=sport,
            crc=CRC(),
        )

        def close() -> None:
            for endpoint in endpoints:
                endpoint_close = getattr(endpoint, "Close", None)
                if callable(endpoint_close):
                    endpoint_close()

        return binding, close
    if kind == "fixture":
        factory = config.get("factory")
        if not callable(factory):
            raise Go2SDKError("test fixture candidate binding factory is missing")
        value = factory(config)
        if not isinstance(value, tuple) or len(value) != 2 or not callable(value[1]):
            raise Go2SDKError("test fixture candidate binding factory returned an invalid binding")
        return value[0], value[1]
    raise Go2SDKError(f"unknown candidate worker binding kind: {kind!r}")


def _candidate_invoke_from_spec(spec: Mapping[str, Any]) -> Callable[[str, Mapping[str, Any], object], Any]:
    if spec.get("kind") == "validated_handle":
        from ...validation.validation_a import ValidatedCandidateHandle, _HANDLE_TOKEN

        payload = spec.get("payload")
        if not isinstance(payload, Mapping):
            raise Go2SessionError("candidate handle payload is missing")
        handle = ValidatedCandidateHandle(
            _HANDLE_TOKEN,
            source=payload["source"],
            source_hash=payload["source_hash"],
            implementation_manifest_hash=payload["implementation_manifest_hash"],
            implementation_bundle_hash=payload["implementation_bundle_hash"],
            contracts=payload["contracts"],
            a_report_hash=payload["validation_a_report_hash"],
            overlay=payload.get("overlay"),
        )
        return handle._invoke
    invoke = spec.get("invoke")
    if not callable(invoke):
        raise Go2SessionError("candidate worker invocation is not callable")
    return invoke


def _candidate_process_entry(
    candidate_spec: Mapping[str, Any],
    capability_id: str,
    arguments: Mapping[str, Any],
    worker_config: Mapping[str, Any],
    result_sender: Any,
) -> None:
    """Execute one candidate in the killable per-invocation worker boundary."""

    close_binding: Callable[[], None] | None = None
    try:
        invoke = _candidate_invoke_from_spec(candidate_spec)
        binding, close_binding = _candidate_binding_from_config(worker_config)
        result_sender.send({"ready": True})
        result = invoke(capability_id, arguments, binding)
        payload = {
            "ok": True,
            "result": result,
            "finished_at": time.monotonic(),
        }
    except BaseException as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "finished_at": time.monotonic(),
        }
    finally:
        if close_binding is not None:
            try:
                close_binding()
            except BaseException:
                pass
    try:
        result_sender.send(payload)
    except BaseException:
        # A non-picklable candidate result is reported by the parent as an
        # unexpected worker exit; no candidate object crosses the public API.
        pass
    finally:
        try:
            result_sender.close()
        except BaseException:
            pass


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
        injected_backend = backend is not None
        injected_transport = transport is not None
        injected_bridge = bridge is not None
        injected_sdk = sdk is not None
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
            sdk = _UnitreeGo2SDKConnection()

        self._model_path = Path(model_path).resolve() if model_path is not None else None
        self._backend = backend
        self._transport = transport
        self._bridge = bridge
        self._sdk_connection = sdk
        self._sdk_binding: object | None = None
        self._candidate_worker_config: Mapping[str, Any] | None = None
        self._truth_provider = truth_provider
        self._video_profile = _profile(video_profile)
        self._frame_capture_factory = frame_capture_factory
        self._render_rgb = render_rgb or self._build_renderer()
        self._renderer: Any | None = getattr(self, "_renderer", None)
        self._task_instances = self._load_task_instances(task_instances_path)
        self._rollout_steps = rollout_steps
        self._max_rollout_steps = max_rollout_steps
        self._state_wait_s = state_wait_s
        self._reset_tolerance = reset_tolerance
        self._injected_dependencies = any(
            (
                injected_backend,
                injected_transport,
                injected_bridge,
                injected_sdk,
                backend_factory is not None,
                transport_factory is not None,
                bridge_factory is not None,
                truth_provider is not None,
                frame_capture_factory is not None,
                render_rgb is not None,
            )
        )

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
        self._physics_steps = 0
        self._accepted_baseline = 0
        self._lowstate_baseline = 0
        self._sportstate_baseline = 0
        self._recording = False
        self._recording_phase: str | None = None
        self._recording_execution_id: str | None = None
        self._capture: Any | None = None
        self._last_invocation_start_s = 0.0
        self._last_invocation_end_s = 0.0
        self._candidate_invocation_count = 0
        self._trial_action_start_s = 0.0
        self._last_route_evidence: dict[str, Any] = {}

        if auto_start:
            try:
                self.start()
            except Exception:
                self.close()
                raise

    @property
    def sdk(self) -> object:
        self._require_started()
        if self._sdk_binding is None:
            raise Go2SessionError("real SDK2 binding is unavailable")
        return self._sdk_binding

    @property
    def simulation_time_s(self) -> float:
        value = float(self._backend.simulation_time)
        if not math.isfinite(value) or value < 0:
            raise Go2SessionError("Go2 simulation time must be finite and non-negative")
        return value

    @property
    def evidence_scope(self) -> str:
        if not self._started or self._closed or self._sdk_binding is None:
            return "UNAVAILABLE"
        return "TEST_FIXTURE_ONLY" if self._injected_dependencies else "SDK_GROUNDED_SIMULATION"

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
            sdk_started = bool(getattr(self._sdk_connection, "started", False))
            if not sdk_started:
                start = getattr(self._sdk_connection, "start", None)
                if not callable(start):
                    raise Go2SessionError("SDK2 connection does not implement start")
                start()
            binding = getattr(self._sdk_connection, "binding", None)
            worker_config = getattr(self._sdk_connection, "candidate_worker_config", None)
            if callable(worker_config):
                worker_config = worker_config()
            if not isinstance(worker_config, Mapping):
                raise Go2SessionError("SDK2 connection did not expose a worker DDS binding")
            self._candidate_worker_config = dict(worker_config)
            if binding is None:
                binding = SimpleNamespace()
            self._sdk_binding = binding
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
        self._physics_steps = 0
        self._accepted_baseline = self._bridge_accepted_count()
        self._lowstate_baseline = self._sdk_count("low_state_publications")
        self._sportstate_baseline = self._sdk_count("sport_mode_state_publications")
        self._last_route_evidence = {}
        start = self._record_truth()
        self._start_truth = start
        self._forward_axis, self._lateral_axis = initial_body_yaw_frame(start.body_x_axis)
        self._trial_action_start_s = self._reset_time_s
        self._last_invocation_start_s = self._reset_time_s
        self._last_invocation_end_s = self._reset_time_s
        self._candidate_invocation_count = 0

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        self._require_started()
        phase = _text(phase, "recording phase")
        execution_id = _text(execution_id, "recording execution_id")
        if self._recording:
            raise Go2SessionError("external recording is already active")
        if self._start_truth is None:
            raise Go2SessionError("recording requires a verified reset")
        factory = self._frame_capture_factory or _load_frame_capture_factory()
        try:
            capture = factory(self._video_profile, self._render_rgb)
            if not all(callable(getattr(capture, name, None)) for name in ("start", "on_step", "stop")):
                raise Go2SessionError("MuJoCoFrameCapture does not implement start/on_step/stop")
            self._capture_call(capture, "start")
            self._recording = True
            self._recording_phase = phase
            self._recording_execution_id = execution_id
            self._capture = capture
            self._capture_call(capture, "on_step")
        except BaseException:
            self._recording = False
            self._capture = None
            raise

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        self._require_started()
        if not self._recording or self._capture is None:
            raise Go2SessionError("no external recording is active")
        capture = self._capture
        self._capture = None
        self._recording = False
        self._recording_phase = None
        self._recording_execution_id = None
        frames = self._capture_call(capture, "stop")
        if not isinstance(frames, tuple):
            frames = tuple(frames) if isinstance(frames, Sequence) else ()
        if not all(isinstance(frame, RGBFrame) for frame in frames):
            raise Go2SessionError("MuJoCoFrameCapture returned invalid RGB frames")
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
        if self._candidate_worker_config is None:
            raise Go2SessionError("candidate worker DDS binding is unavailable")
        candidate_spec: dict[str, Any]
        if isinstance(candidate, ValidatedCandidateHandle):
            payload = candidate._framework_payload_snapshot()
            candidate_spec = {
                "kind": "validated_handle",
                "payload": {
                    "source": payload.source,
                    "source_hash": payload.source_hash,
                    "implementation_manifest_hash": payload.implementation_manifest_hash,
                    "implementation_bundle_hash": payload.implementation_bundle_hash,
                    "contracts": copy.deepcopy(payload.contracts),
                    "validation_a_report_hash": payload.validation_a_report_hash,
                    "overlay": copy.deepcopy(payload.overlay),
                },
            }
        else:
            candidate_spec = {"kind": "callable", "invoke": invoke}
        action_start = self.simulation_time_s
        steps = self._steps_for_invocation(arguments)
        self._last_invocation_start_s = action_start
        self._candidate_invocation_count += 1
        result = self._invoke_with_clock(
            invoke,
            candidate_spec,
            capability_id,
            copy.deepcopy(dict(arguments)),
            steps,
        )
        if not isinstance(result, Mapping):
            raise Go2SessionError("candidate result must be a mapping")
        self._last_invocation_end_s = self.simulation_time_s
        self._wait_for_sdk_state()
        self._last_route_evidence = self._route_evidence()
        return copy.deepcopy(dict(result))

    def validation_evidence(self, invocation: HarnessInvocation) -> ValidationEvidence:
        self._require_started()
        if not isinstance(invocation, HarnessInvocation):
            raise Go2SessionError("validation evidence requires a typed Harness invocation")
        if self._start_truth is None:
            raise Go2SessionError("validation evidence requires a verified reset")
        # Validation B has already invoked the candidate through
        # ``session.invoke``.  Collection owns only this criterion's trusted
        # observation/dwell window; it does not pretend to own the candidate
        # call or manufacture a command receipt for it.
        action_start = self._last_invocation_end_s if self._last_invocation_end_s > self._reset_time_s else self._reset_time_s
        duration = max(DEFAULT_INVOCATION_WINDOW_S, float(invocation.dwell_s))
        duration = min(duration, float(invocation.timeout_s))
        if duration < 0:
            raise Go2SessionError("validation timeout/dwell is invalid")
        self._last_invocation_start_s = action_start
        self._advance(duration, wait_for_command=True)
        self._last_invocation_end_s = self.simulation_time_s
        self._wait_for_sdk_state()
        route = self._route_evidence()
        selected = [
            sample for sample in self._truth_samples
            if sample.time_s > action_start + 1e-12
        ]
        if not selected:
            selected = [self._truth_samples[-1]]
        metric = invocation.metric if isinstance(invocation.metric, str) else "body_height_m"
        samples = tuple(
            MeasurementSample(
                max(0.0, sample.time_s - action_start),
                self._metric_value(sample, metric),
            )
            for sample in selected
        )
        finite = all(self._sample_finite(sample) for sample in selected)
        no_contact = all(
            sample.body_floor_contact is False and sample.head_floor_contact is False
            for sample in selected
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
            elapsed_s=max(0.0, self.simulation_time_s - action_start),
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
        instance = self._task_instances.get(task_id, {})
        dwell = self._instance_number(instance, "dwell_s", 1.0)
        if task_id == "G04":
            dwell = self._instance_number(instance, "final_stop_dwell_s", 0.5)
        terminal_start = self.simulation_time_s
        self._advance(dwell, wait_for_command=False)
        self._wait_for_sdk_state()
        route = self._route_evidence()
        route_guard = bool(
            route.get("verified") and route.get("candidate_invocation_observed")
        )
        self._last_route_evidence = route
        terminal_samples = [
            sample for sample in self._truth_samples
            if sample.time_s > self._reset_time_s + 1e-12
            and sample.time_s >= terminal_start - 1e-12
        ]
        if not terminal_samples:
            terminal_samples = [self._truth_samples[-1]]
        if task_id == "G04":
            motion_start = self._last_invocation_start_s
            motion_samples = [
                sample for sample in self._truth_samples
                if sample.time_s > self._reset_time_s + 1e-12
                and sample.time_s >= motion_start - 1e-12
                and sample.time_s <= terminal_start + 1e-12
            ]
            if motion_start <= self._reset_time_s + 1e-12 and terminal_start > motion_start:
                motion_samples.insert(0, self._start_truth)
            motion_records = [self._sample_record(sample, phase="motion") for sample in motion_samples]
            terminal_records = [self._sample_record(sample, phase="terminal") for sample in terminal_samples]
            all_records = motion_records + terminal_records
            result: dict[str, Any] = {
                "task_id": task_id,
                "samples": all_records,
                "motion_samples": motion_records,
                "terminal_samples": terminal_records,
                "motion_duration_s": self._sample_duration(motion_samples),
                "terminal_duration_s": self._sample_duration(terminal_samples),
                "terminal_dwell_s": self._sample_duration(terminal_samples),
                "elapsed_s": self._sample_duration(terminal_samples),
                "duration_s": self._sample_duration(terminal_samples),
                "sdk_route_guard": route_guard,
                "sdk_route_evidence": copy.deepcopy(route),
                "guard_results": self._demo_guard_results(
                    [*motion_samples, *terminal_samples], route, route_guard=route_guard
                ),
            }
            return result

        records = [self._sample_record(sample, phase="terminal") for sample in terminal_samples]
        terminal_duration = self._sample_duration(terminal_samples)
        return {
            "task_id": task_id,
            "samples": records,
            "terminal_samples": records,
            "terminal_duration_s": terminal_duration,
            "elapsed_s": terminal_duration,
            "duration_s": terminal_duration,
            "sdk_route_guard": route_guard,
            "sdk_route_evidence": copy.deepcopy(route),
            "guard_results": self._demo_guard_results(
                terminal_samples, route, route_guard=route_guard
            ),
        }

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self._recording and self._capture is not None:
            try:
                self._capture_call(self._capture, "stop")
            except BaseException as exc:
                errors.append(exc)
            self._capture = None
            self._recording = False
        try:
            close_sdk = getattr(self._sdk_connection, "close", None)
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

    def _discard_pending_lowcmd(self) -> None:
        """Drop commands received outside the active runner clock window.

        The real transport retains only its latest DDS callback value.  Test
        transports may expose a FIFO, so drain the complete pre-window set
        before the candidate worker starts and again after it ends.  No value
        is replayed into future simulation time: only a message received while
        the worker-owned clock is stepping can reach ``bridge.step()``.
        """

        take = getattr(self._transport, "take_lowcmd", None)
        if not callable(take):
            return
        for _ in range(max(64, self._max_rollout_steps)):
            try:
                message = take()
            except Exception as exc:
                raise Go2SessionError("could not drain the Go2 DDS command boundary") from exc
            if message is None:
                return
        raise Go2SessionError("Go2 DDS command boundary did not quiesce")

    def _invoke_with_clock(
        self,
        invoke: Callable[[str, Mapping[str, Any], object], Any],
        candidate_spec: Mapping[str, Any],
        capability_id: str,
        arguments: Mapping[str, Any],
        planned_steps: int,
    ) -> Any:
        """Run candidate code beside the sole deterministic physics clock.

        Candidate code remains responsible for constructing and publishing
        every real ``LowCmd_``.  The runner only steps the existing bridge;
        it neither queues, repeats, nor synthesizes commands.  Draining before
        and after the bounded window prevents an old DDS/FIFO value from being
        made fresh merely because it was dequeued later.
        """

        self._discard_pending_lowcmd()
        if self._candidate_worker_config is None:
            raise Go2SessionError("candidate worker DDS binding is unavailable")
        context = multiprocessing.get_context("spawn")
        result_receiver, result_sender = context.Pipe(duplex=False)
        worker = context.Process(
            target=_candidate_process_entry,
            args=(candidate_spec, capability_id, arguments, self._candidate_worker_config, result_sender),
            name="autoadapter2-go2-candidate",
        )
        # This is a killable process boundary, not the old unkillable daemon
        # thread.  Candidates are not allowed to create child workers here.
        worker.daemon = True
        try:
            worker.start()
        except BaseException:
            result_receiver.close()
            result_sender.close()
            raise
        result_sender.close()
        # The candidate cannot choose the wall deadline.  It is a frozen
        # Session-Runner bound, independent of capability arguments.
        deadline = time.monotonic() + DEFAULT_CANDIDATE_TIMEOUT_S
        steps = 0
        timed_out = False
        runner_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        payload: Mapping[str, Any] | None = None
        worker_alive_after_cleanup = True
        candidate_ready = False
        candidate_done = False

        def receive_worker_messages() -> None:
            nonlocal candidate_ready, candidate_done, payload
            if not result_receiver.poll():
                return
            try:
                message = result_receiver.recv()
            except EOFError:
                return
            if not isinstance(message, Mapping):
                return
            if message.get("ready") is True:
                candidate_ready = True
            else:
                payload = message
                candidate_done = True

        try:
            # A zero-duration yield gives the real DDS callback and candidate
            # worker a chance to publish before the first bridge step without
            # making wall time the simulation clock.
            time.sleep(min(0.002, max(0.0, deadline - time.monotonic())))
            while not candidate_done or steps < planned_steps:
                receive_worker_messages()
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if candidate_done and steps >= planned_steps:
                    break
                if not candidate_ready:
                    # DDS endpoint construction belongs to the child.  Do not
                    # advance simulated time while that child is starting.
                    time.sleep(0.001)
                    continue
                if steps >= self._max_rollout_steps:
                    timed_out = True
                    break
                self._step_physics()
                steps += 1
                if self._should_wait_for_command_delivery():
                    delivery_deadline = time.monotonic() + self._state_wait_s
                    while (
                        not self._last_bridge_result.get("accepted", False)
                        and self.simulation_time_s - self._reset_time_s < 0.1
                        and time.monotonic() < delivery_deadline
                        and time.monotonic() < deadline
                        and steps < self._max_rollout_steps
                    ):
                        time.sleep(0.001)
                        self._step_physics()
                        steps += 1
                # Yield only for delivery/thread scheduling.  Simulation time
                # advances exclusively through the bridge step; the bounded
                # wall yield lets a low-level candidate publish its next DDS
                # sample before the next deterministic tick.
                if worker.is_alive():
                    time.sleep(0.001)
                else:
                    time.sleep(0)
                receive_worker_messages()
        except BaseException as exc:
            runner_error = exc
        finally:
            timed_out = timed_out or (worker.is_alive() and time.monotonic() >= deadline)
            if worker.is_alive():
                # A result payload means the candidate call completed; the
                # still-live process is only waiting for its interpreter to
                # unwind and must be reaped without turning success into a
                # timeout.
                if not candidate_done and runner_error is None:
                    timed_out = True
                try:
                    self._terminate_candidate_process(worker)
                except BaseException as exc:
                    cleanup_error = exc
            elif timed_out or runner_error is not None:
                pass
            else:
                try:
                    worker.join(timeout=0)
                    if worker.is_alive():
                        self._terminate_candidate_process(worker)
                        timed_out = True
                except BaseException as exc:
                    cleanup_error = exc
            if cleanup_error is None and payload is None and not worker.is_alive():
                try:
                    if result_receiver.poll(0.2):
                        candidate_payload = result_receiver.recv()
                        if isinstance(candidate_payload, Mapping):
                            payload = candidate_payload
                except (EOFError, OSError) as exc:
                    # A terminated worker closes the result pipe without a
                    # payload; timeout handling intentionally treats that as
                    # the expected kill path.
                    if not timed_out:
                        cleanup_error = exc
            try:
                # The worker is joined before this drain.  A queued command
                # cannot become fresh after the invocation has ended.
                self._discard_pending_lowcmd()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            try:
                result_receiver.close()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            worker_alive_after_cleanup = worker.is_alive()
            if not worker_alive_after_cleanup:
                try:
                    worker.close()
                except ValueError:
                    pass

        if cleanup_error is not None:
            try:
                self.close()
            except BaseException:
                pass
            raise Go2SessionError("candidate worker cleanup failed") from cleanup_error
        if worker_alive_after_cleanup:
            try:
                self.close()
            except BaseException:
                pass
            raise Go2SessionError("candidate worker cancellation did not complete")
        if runner_error is not None:
            raise runner_error
        if timed_out:
            raise Go2SessionError("candidate invocation exceeded its bounded clock window")
        if payload is None:
            raise Go2SessionError("candidate worker exited without a result")
        finished_at = payload.get("finished_at")
        if isinstance(finished_at, (int, float)) and finished_at > deadline:
            raise Go2SessionError("candidate invocation exceeded its bounded clock window")
        if payload.get("ok") is not True:
            error_type = payload.get("error_type", "candidate error")
            error_text = payload.get("error", "candidate invocation failed")
            raise Go2SessionError(f"candidate invocation failed: {error_type}: {error_text}")
        return payload.get("result")

    @staticmethod
    def _terminate_candidate_process(worker: Any) -> None:
        """Terminate and join the worker before the session can return."""

        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=DEFAULT_CANDIDATE_CANCEL_GRACE_S)
        if worker.is_alive():
            kill = getattr(worker, "kill", None)
            if not callable(kill):
                raise Go2SessionError("candidate worker has no kill operation")
            kill()
            worker.join(timeout=DEFAULT_CANDIDATE_CANCEL_GRACE_S)
        if worker.is_alive():
            raise Go2SessionError("candidate worker cancellation did not complete")

    def _advance(self, seconds: float, *, wait_for_command: bool) -> None:
        self._require_started()
        seconds = _finite(seconds, "physics advance duration")
        if seconds < 0:
            raise Go2SessionError("physics advance duration must be non-negative")
        timestep = _finite(self._backend.timestep, "MuJoCo timestep")
        if timestep <= 0:
            raise Go2SessionError("MuJoCo timestep must be positive")
        # Treat an exact integral number of MuJoCo steps as exact despite
        # binary floating-point representation (for example 3 * 0.1).
        steps = int(math.ceil(max(0.0, seconds / timestep - 1e-12))) if seconds else 0
        if steps > self._max_rollout_steps:
            raise Go2SessionError("physics advance exceeds the frozen session limit")
        for _ in range(steps):
            self._step_physics()
            if wait_for_command and self._should_wait_for_command_delivery():
                deadline = time.monotonic() + self._state_wait_s
                while (
                    not self._last_bridge_result.get("accepted", False)
                    and self.simulation_time_s - self._reset_time_s < 0.1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.001)
                    self._step_physics()

    def _should_wait_for_command_delivery(self) -> bool:
        return bool(
            getattr(self._sdk_connection, "is_real_sdk", False)
            and not self._last_bridge_result.get("accepted", False)
            and self.simulation_time_s - self._reset_time_s < 0.1
        )

    def _step_physics(self) -> None:
        result = self._bridge.step()
        self._last_bridge_result = dict(result) if isinstance(result, Mapping) else {}
        self._physics_steps += 1
        self._record_truth()
        if self._recording and self._capture is not None:
            self._capture_call(self._capture, "on_step")

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
            "contact_observation_available": bool(floor_ids and body_ids and head_ids),
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

    def _capture_call(self, capture: Any, method_name: str) -> Any:
        method = getattr(capture, method_name, None)
        if not callable(method):
            raise Go2SessionError(f"MuJoCoFrameCapture is missing {method_name}()")
        return method(self.simulation_time_s)

    def _capture_metrics(self, sample: Go2TruthSample) -> tuple[float, float, float]:
        if self._start_truth is None:
            return 0.0, 0.0, 0.0
        return start_frame_displacement(
            self._start_truth.body_position,
            sample.body_position,
            self._forward_axis,
            self._lateral_axis,
        )

    def _sample_record(self, sample: Go2TruthSample, *, phase: str) -> dict[str, Any]:
        phase = _text(phase, "sample phase")
        forward, lateral, drift = self._capture_metrics(sample)
        return {
            "time_s": max(0.0, sample.time_s - self._reset_time_s),
            "phase": phase,
            "metrics": {
                "body_height_m": float(sample.body_height_m),
                "upright_score": float(sample.upright_score),
                "planar_speed_m_s": float(sample.planar_speed_m_s),
                "forward_displacement_m": float(forward),
                "lateral_displacement_m": float(lateral),
                "absolute_lateral_displacement_m": float(abs(lateral)),
                "horizontal_drift_m": float(drift),
                "body_height_to_standing_height_ratio": float(
                    sample.body_height_m / self._standing_height_m
                ),
                "absolute_body_height_error_m": float(
                    abs(
                        sample.body_height_m
                        - self._instance_number(
                            self._task_instances.get(self._task_id or ""),
                            "target_body_height_m",
                            self._standing_height_m,
                        )
                    )
                ),
                "body_floor_contact": sample.body_floor_contact is True,
                "head_floor_contact": sample.head_floor_contact is True,
                "finite_required_state": self._sample_finite(sample),
                "contact_observation_available": sample.contact_observation_available,
            },
        }

    @staticmethod
    def _sample_duration(samples: Sequence[Go2TruthSample]) -> float:
        if not samples:
            return 0.0
        return max(0.0, float(samples[-1].time_s) - float(samples[0].time_s))

    def _demo_guard_results(
        self,
        samples: Sequence[Go2TruthSample],
        _route: Mapping[str, Any],
        *,
        route_guard: bool,
    ) -> dict[str, bool]:
        finite = bool(samples) and all(self._sample_finite(sample) for sample in samples)
        no_contact = bool(samples) and all(
            sample.body_floor_contact is False
            and sample.head_floor_contact is False
            and sample.contact_observation_available
            for sample in samples
        )
        return {
            "trusted-external-verdict": bool(
                route_guard and self._started and not self._closed and self._start_truth is not None
            ),
            "finite-required-state": finite,
            "no-body-or-head-floor-contact": no_contact,
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
        return bool(
            sample.finite_required_state
            and sample.contact_observation_available
            and isinstance(sample.body_floor_contact, bool)
            and isinstance(sample.head_floor_contact, bool)
            and all(math.isfinite(float(value)) for value in values)
        )

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
        value = getattr(self._sdk_connection, name, 0)
        if callable(value):
            value = value()
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _wait_for_sdk_state(self) -> None:
        deadline = time.monotonic() + self._state_wait_s
        while time.monotonic() < deadline:
            if self._state_observed():
                return
            if not getattr(self._sdk_connection, "is_real_sdk", False):
                return
            time.sleep(0.001)

    def _state_observed(self) -> bool:
        low_count = self._sdk_count("low_state_publications")
        sport_count = self._sdk_count("sport_mode_state_publications")
        if low_count > self._lowstate_baseline and sport_count > self._sportstate_baseline:
            return True
        low: object | None = None
        sport: object | None = None
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
        lowcmd_type = getattr(self._sdk_connection, "lowcmd_type", None)
        type_verified = bool(accepted_delta > 0 and lowcmd_type is not None)
        # The bridge increments its accepted counter only after exact DDS
        # type, width, finite-field, and CRC validation.  That counter is the
        # source of this truth; no candidate-visible receipt is consulted.
        crc_verified = bool(accepted_delta > 0)
        state_observed = self._state_observed()
        progress = self.simulation_time_s > self._reset_time_s + 1e-15
        verified = bool(
            accepted_delta > 0
            and type_verified
            and crc_verified
            and state_observed
            and progress
        )
        candidate_invocation_observed = self._candidate_invocation_count > 0
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
            "candidate_invocation_count": self._candidate_invocation_count,
            "candidate_invocation_observed": candidate_invocation_observed,
            "lowcmd_message_type": getattr(lowcmd_type, "__name__", None),
        }


def _factory_path(root: Path, value: str | Path, relative_path: str, label: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    expected = (root / relative_path).resolve()
    if resolved != expected:
        raise Go2SessionError(f"{label} must use the frozen path {relative_path}")
    if candidate.is_symlink() or not resolved.is_file():
        raise Go2SessionError(f"{label} is not a regular frozen file: {resolved}")
    return resolved


def _factory_selected_path(
    root: Path,
    value: str | Path | None,
    default_relative_path: str,
    label: str,
    *,
    require_under_root: bool = False,
) -> Path:
    """Resolve a launcher-selected regular file without trusting env paths."""

    candidate = Path(value) if value is not None else root / default_relative_path
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink() or not candidate.is_file():
        raise Go2SessionError(f"{label} is not a regular selected file: {candidate}")
    resolved = candidate.resolve()
    if require_under_root:
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise Go2SessionError(f"{label} escapes the Go2 project root") from exc
    return resolved


def _factory_hash_argument(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise Go2SessionError(f"{label} must be a lowercase or uppercase SHA-256 hex digest")
    return value.lower()


def _factory_runner_inputs(
    *,
    robot: str | Mapping[str, Any] | None,
    manifest_path: str | Path | None,
    run_directory: str | Path | None,
    project_root: str | Path | None,
    integration_manifest_path: str | Path | None,
    integration_manifest_sha256: str | None,
    runtime_lock_path: str | Path | None,
    runtime_lock_sha256: str | None,
) -> tuple[Path, Path | None, str, Path | None, str | None, Path | None]:
    """Adapt the first-G2 runner call into verified factory inputs.

    The runner deliberately passes only its selected robot, manifest, and new
    run directory.  This adapter derives the project root and the external
    runtime-lock reference from those selected artifacts; it never chooses a
    model or accepts an environment-only path.
    """

    if robot is not None:
        if isinstance(robot, str):
            robot_model_id = robot
            robot_configuration_id = None
        elif isinstance(robot, Mapping):
            robot_model_id = robot.get("robot_model_id", robot.get("model_id"))
            robot_configuration_id = robot.get("robot_configuration_id", robot.get("configuration_id"))
        else:
            raise Go2SessionError("Go2 production factory robot selection is malformed")
        if robot_model_id != ROBOT_MODEL_ID or (
            robot_configuration_id is not None
            and robot_configuration_id != ROBOT_CONFIGURATION_ID
        ):
            raise Go2SessionError("Go2 production factory received a non-Go2 robot selection")

    if manifest_path is not None and integration_manifest_path is not None:
        first = Path(manifest_path)
        second = Path(integration_manifest_path)
        if first.resolve() != second.resolve():
            raise Go2SessionError("conflicting Go2 integration manifest paths")
    selected_manifest_value = integration_manifest_path or manifest_path
    if selected_manifest_value is None:
        return (
            Path(project_root).resolve() if project_root is not None else Path(__file__).resolve().parents[5],
            None,
            "",
            None,
            None,
            None,
        )

    selected_manifest = Path(selected_manifest_value)
    if not selected_manifest.is_absolute():
        selected_manifest = (
            Path(project_root).resolve() / selected_manifest
            if project_root is not None
            else Path.cwd() / selected_manifest
        )
    selected_manifest = selected_manifest.resolve()
    if selected_manifest.is_symlink() or not selected_manifest.is_file():
        raise Go2SessionError("selected Go2 integration manifest is missing or symlinked")

    if project_root is not None:
        root = Path(project_root).resolve()
    else:
        root = None
        for ancestor in (selected_manifest.parent, *selected_manifest.parents):
            if (ancestor / "general_demo").is_dir():
                root = ancestor.resolve()
                break
        if root is None:
            raise Go2SessionError("could not derive Go2 project root from the selected manifest")

    selected_manifest_hash = integration_manifest_sha256 or _factory_sha256(
        selected_manifest, "selected Go2 integration manifest"
    )
    _factory_hash_argument(selected_manifest_hash, "Go2 integration manifest hash")
    if runtime_lock_path is not None and runtime_lock_sha256 is not None:
        selected_lock = Path(runtime_lock_path)
        if not selected_lock.is_absolute():
            selected_lock = root / selected_lock
        return (
            root,
            selected_manifest,
            selected_manifest_hash,
            selected_lock.resolve(),
            _factory_hash_argument(runtime_lock_sha256, "Go2 runtime lock hash"),
            None,
        )

    try:
        manifest_value = json.loads(selected_manifest.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise Go2SessionError("selected Go2 integration manifest could not be read") from exc
    if not isinstance(manifest_value, Mapping):
        raise Go2SessionError("selected Go2 integration manifest is not an object")
    runtime_value = manifest_value.get("runtime")
    if not isinstance(runtime_value, Mapping):
        raise Go2SessionError("selected Go2 integration manifest has no runtime binding")
    manifest_lock_hash = _factory_hash_argument(
        runtime_value.get("lock_sha256"),
        "Go2 integration manifest runtime lock reference",
    )
    if manifest_lock_hash is None:
        raise Go2SessionError("selected Go2 integration manifest has no runtime lock hash")

    experimental_arm64 = _experimental_arm64_enabled()
    snapshot: Mapping[str, Any] | None = None
    selected_run_directory = None
    if run_directory is not None:
        selected_run_directory = Path(run_directory)
        if not selected_run_directory.is_absolute():
            selected_run_directory = root / selected_run_directory
        selected_run_directory = selected_run_directory.resolve()
        snapshot_path = selected_run_directory / "run_snapshot.json"
        if snapshot_path.is_file() and not snapshot_path.is_symlink():
            try:
                value = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError) as exc:
                raise Go2SessionError("selected first-G2 run snapshot could not be read") from exc
            if not isinstance(value, Mapping):
                raise Go2SessionError("selected first-G2 run snapshot is not an object")
            snapshot = value
            manifest_ref = snapshot.get("integration_manifest_ref")
            if isinstance(manifest_ref, Mapping):
                snapshot_manifest_hash = _factory_hash_argument(
                    manifest_ref.get("sha256"),
                    "first-G2 run snapshot manifest hash",
                )
                if snapshot_manifest_hash != selected_manifest_hash.lower():
                    raise Go2SessionError("first-G2 run snapshot does not bind the selected manifest")
            from ...integration.artifacts import stable_json_sha256

            snapshot_runtime_hash = snapshot.get("runtime_sha256")
            if snapshot_runtime_hash is not None and snapshot_runtime_hash != stable_json_sha256(runtime_value):
                raise Go2SessionError("first-G2 run snapshot does not bind the selected runtime")

    selected_lock = runtime_lock_path
    if selected_lock is None and snapshot is not None and not experimental_arm64:
        for key in ("runtime_lock_ref", "runtime_lock", "runtime_lock_path"):
            candidate = snapshot.get(key)
            if isinstance(candidate, Mapping):
                selected_lock = candidate.get("path")
                snapshot_lock_hash = _factory_hash_argument(
                    candidate.get("sha256"), "first-G2 run snapshot runtime lock hash"
                )
                if snapshot_lock_hash != manifest_lock_hash:
                    raise Go2SessionError("first-G2 run snapshot does not bind the manifest runtime lock")
                break
            if isinstance(candidate, (str, Path)):
                selected_lock = candidate
                break
    if selected_lock is None and experimental_arm64:
        selected_lock = os.environ.get(GO2_EXPERIMENTAL_ARM64_LOCK_ENV)
        if selected_lock is None:
            selected_lock = (
                root
                / "general_demo/environments"
                / GO2_EXPERIMENTAL_ARM64_RUNTIME_ID
                / GO2_EXPERIMENTAL_ARM64_RUNTIME_VERSION
                / "runtime-lock.json"
            )
    if selected_lock is None:
        translation_ref = manifest_value.get("translation_ref")
        if translation_ref is not None:
            translation = _factory_json_reference(
                root,
                translation_ref,
                label="selected Go2 Translation record",
                require_under_root=True,
            )
            translation_lock_ref = translation.get("runtime_lock_ref")
            if translation_lock_ref is not None:
                if not isinstance(translation_lock_ref, Mapping):
                    raise Go2SessionError(
                        "selected Go2 Translation runtime lock reference is malformed"
                    )
                translation_lock_path = translation_lock_ref.get("path")
                translation_lock_hash = _factory_hash_argument(
                    translation_lock_ref.get("sha256"),
                    "selected Go2 Translation runtime lock hash",
                )
                if not isinstance(translation_lock_path, str) or translation_lock_hash is None:
                    raise Go2SessionError(
                        "selected Go2 Translation runtime lock reference is incomplete"
                    )
                if translation_lock_hash != manifest_lock_hash:
                    raise Go2SessionError(
                        "selected Go2 Translation runtime lock hash does not match the manifest runtime lock reference"
                    )
                selected_lock = _factory_selected_path(
                    root,
                    translation_lock_path,
                    translation_lock_path,
                    "selected Go2 Translation runtime lock",
                    require_under_root=True,
                )
    if selected_lock is None:
        runtime_id = runtime_value.get("id")
        runtime_version = runtime_value.get("version")
        if not isinstance(runtime_id, str) or not isinstance(runtime_version, str):
            raise Go2SessionError("selected Go2 runtime binding has no lock path derivation")
        selected_lock = root / "general_demo/environments" / runtime_id / runtime_version / "runtime-lock.json"
    selected_lock_path = Path(selected_lock)
    if not selected_lock_path.is_absolute():
        selected_lock_path = root / selected_lock_path
    selected_lock_path = selected_lock_path.resolve()
    selected_lock_hash = manifest_lock_hash
    if experimental_arm64 and runtime_lock_sha256 is None:
        selected_lock_hash = _factory_sha256(
            selected_lock_path,
            "selected experimental arm64 Go2 runtime lock",
        )
    return (
        root,
        selected_manifest,
        selected_manifest_hash,
        selected_lock_path,
        selected_lock_hash,
        selected_run_directory,
    )


def _factory_json_reference(
    root: Path,
    reference: Any,
    *,
    label: str,
    require_under_root: bool = False,
) -> dict[str, Any]:
    from ...integration.artifacts import load_json_artifact

    if not isinstance(reference, Mapping):
        raise Go2SessionError(f"{label} is not a file reference")
    reference_path = reference.get("path")
    expected_sha256 = _factory_hash_argument(reference.get("sha256"), f"{label} hash")
    if not isinstance(reference_path, str) or expected_sha256 is None:
        raise Go2SessionError(f"{label} is not a complete selected file reference")
    path = _factory_selected_path(
        root,
        reference_path,
        reference_path,
        label,
        require_under_root=require_under_root,
    )
    try:
        artifact = load_json_artifact(path)
    except Exception as exc:
        raise Go2SessionError(f"{label} could not be loaded") from exc
    if artifact.sha256 != expected_sha256:
        raise Go2SessionError(f"{label} hash does not match the selected manifest bytes")
    return artifact.value


def _factory_sha256(path: Path, label: str) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise Go2SessionError(f"{label} could not be read") from exc
    return hashlib.sha256(payload).hexdigest()


def _factory_verify_asset_closure(
    *,
    model_path: Path,
    runtime_entrypoint: Mapping[str, Any],
) -> None:
    entrypoint = runtime_entrypoint.get("path")
    if not isinstance(entrypoint, str):
        raise Go2SessionError("runtime lock MuJoCo entrypoint is malformed")
    entry_relative = PurePosixPath(entrypoint)
    if entry_relative.is_absolute() or any(
        part in {"", ".", ".."} for part in entry_relative.parts
    ):
        raise Go2SessionError("runtime lock MuJoCo entrypoint is not a safe relative path")
    asset_root = model_path
    for _ in entry_relative.parts:
        asset_root = asset_root.parent
    expected_model = (asset_root / Path(*entry_relative.parts)).resolve()
    if expected_model != model_path:
        raise Go2SessionError("selected model path is not the frozen runtime entrypoint")

    asset_files = runtime_entrypoint.get("asset_files")
    if not isinstance(asset_files, list) or not asset_files:
        raise Go2SessionError("runtime lock MuJoCo asset closure is missing")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(asset_files):
        if not isinstance(item, Mapping):
            raise Go2SessionError(f"runtime asset record {index} is malformed")
        relative = item.get("path")
        expected_sha = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_sha, str):
            raise Go2SessionError(f"runtime asset record {index} is malformed")
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ) or relative in seen:
            raise Go2SessionError(f"runtime asset path is unsafe or duplicated: {relative}")
        seen.add(relative)
        asset_candidate = asset_root / Path(*relative_path.parts)
        if asset_candidate.is_symlink() or not asset_candidate.is_file():
            raise Go2SessionError(f"runtime asset is missing or symlinked: {relative}")
        asset_path = asset_candidate.resolve()
        try:
            asset_path.relative_to(asset_root.resolve())
        except ValueError as exc:
            raise Go2SessionError("runtime asset escapes the selected closure root") from exc
        actual_sha = _factory_sha256(asset_path, f"runtime asset {relative}")
        if actual_sha != expected_sha:
            raise Go2SessionError(f"runtime asset hash mismatch: {relative}")
        records.append(
            {"path": relative, "size": asset_path.stat().st_size, "sha256": actual_sha}
        )

    if entrypoint not in seen:
        raise Go2SessionError("runtime asset closure does not include scene.xml")
    go2_relative = "unitree_robots/go2/go2.xml"
    if go2_relative not in seen:
        raise Go2SessionError("runtime asset closure does not include go2.xml")
    if _factory_sha256(model_path, "selected MuJoCo scene") != GO2_SCENE_SHA256:
        raise Go2SessionError("selected MuJoCo scene hash is not the official frozen scene")
    go2_path = (asset_root / Path(*PurePosixPath(go2_relative).parts)).resolve()
    if _factory_sha256(go2_path, "included Go2 XML") != GO2_XML_SHA256:
        raise Go2SessionError("included Go2 XML hash is not the official frozen asset")
    from ...foundation.canonical import canonical_bytes

    closure_sha = hashlib.sha256(
        canonical_bytes(sorted(records, key=lambda item: item["path"]))
    ).hexdigest()
    if closure_sha != GO2_ASSET_CLOSURE_SHA256:
        raise Go2SessionError("MuJoCo asset closure hash does not match the frozen runtime")


def _verify_production_inputs(
    *,
    root: Path,
    integration_manifest_path: str | Path | None,
    integration_manifest_sha256: str | None,
    runtime_lock_path: str | Path | None,
    runtime_lock_sha256: str | None,
    model_path: str | Path | None,
    task_instances_path: str | Path | None,
    video_profile: FrozenVideoProfile | Mapping[str, Any] | None,
) -> tuple[Path, Path, FrozenVideoProfile]:
    """Verify the complete Go2 selection before constructing any live endpoint."""

    from ...integration.artifacts import (
        load_integration_manifest,
        load_json_artifact,
        validate_readiness_profile,
    )
    from ...integration.robot_facts import validate_robot_facts

    selected_manifest_sha256 = _factory_hash_argument(
        integration_manifest_sha256, "Go2 integration manifest hash"
    )
    selected_runtime_lock_sha256 = _factory_hash_argument(
        runtime_lock_sha256, "Go2 runtime lock hash"
    )
    if integration_manifest_path is None or selected_manifest_sha256 is None:
        raise Go2SessionError(
            "production Go2 factory requires an external manifest path and hash"
        )
    if runtime_lock_path is None or selected_runtime_lock_sha256 is None:
        raise Go2SessionError(
            "production Go2 factory requires an external runtime lock path and hash"
        )
    manifest_path = _factory_selected_path(
        root,
        integration_manifest_path,
        str(integration_manifest_path),
        "Go2 integration manifest",
    )
    try:
        manifest_artifact = load_integration_manifest(manifest_path)
    except Exception as exc:
        raise Go2SessionError("Go2 integration manifest failed strict validation") from exc
    if (
        selected_manifest_sha256 is not None
        and manifest_artifact.sha256 != selected_manifest_sha256
    ):
        raise Go2SessionError("Go2 integration manifest hash does not match the selected bytes")
    manifest = manifest_artifact.value
    if (
        manifest.get("manifest_id"),
        manifest.get("version"),
        manifest.get("status"),
        manifest.get("robot_model_id"),
        manifest.get("robot_configuration_id"),
    ) != (
        "unitree-go2-stock-12dof-mujoco",
        "1.0.0",
        "READY",
        ROBOT_MODEL_ID,
        ROBOT_CONFIGURATION_ID,
    ):
        raise Go2SessionError("Go2 integration manifest is not the READY stock-12dof selection")
    if manifest.get("unresolved_gaps") != []:
        raise Go2SessionError("Go2 integration manifest contains unresolved gaps")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping) or (
        runtime.get("id"),
        runtime.get("version"),
        runtime.get("mujoco"),
        runtime.get("architecture"),
    ) != (GO2_RUNTIME_ID, GO2_RUNTIME_VERSION, "3.3.6", "amd64"):
        raise Go2SessionError("Go2 integration manifest runtime binding is not frozen")
    experimental_arm64 = _experimental_arm64_enabled()
    manifest_runtime_lock_sha256 = _factory_hash_argument(
        runtime.get("lock_sha256"), "Go2 integration manifest runtime lock reference"
    )
    if manifest_runtime_lock_sha256 is None:
        raise Go2SessionError("Go2 integration manifest has no runtime lock reference")
    if (
        selected_runtime_lock_sha256 is not None
        and selected_runtime_lock_sha256 != manifest_runtime_lock_sha256
        and not experimental_arm64
    ):
        raise Go2SessionError(
            "selected runtime lock hash does not match the manifest runtime lock reference"
        )
    selected_runtime_lock_sha256 = (
        selected_runtime_lock_sha256 or manifest_runtime_lock_sha256
    )

    morphology = _factory_json_reference(
        root,
        manifest.get("morphology_ref"),
        label="Go2 morphology record",
    )
    sdk = _factory_json_reference(
        root,
        manifest.get("sdk_ref"),
        label="Go2 SDK record",
    )
    translation = _factory_json_reference(
        root,
        manifest.get("translation_ref"),
        label="Go2 Translation record",
    )
    readiness = _factory_json_reference(
        root,
        manifest.get("readiness_profile_ref"),
        label="Go2 readiness profile",
    )
    try:
        validate_readiness_profile(readiness)
        if experimental_arm64:
            # The formal Translation record hashes the formal amd64 source
            # snapshot.  Keep its mechanical robot facts checks for this
            # route, but do not claim those READY source hashes for the
            # experimental arm64 branch; its native Runtime Lock captures the
            # actual implementation sources instead.
            experimental_facts_manifest = copy.deepcopy(manifest)
            experimental_facts_manifest["status"] = "DRAFT"
            validate_robot_facts(root, experimental_facts_manifest)
        else:
            validate_robot_facts(root, manifest)
    except Exception as exc:
        raise Go2SessionError("Go2 manifest dependencies failed the robot readiness gate") from exc
    if morphology.get("robot_configuration_id") != ROBOT_CONFIGURATION_ID:
        raise Go2SessionError("Go2 morphology configuration is not frozen")
    if sdk.get("robot_configuration_id") != ROBOT_CONFIGURATION_ID:
        raise Go2SessionError("Go2 SDK configuration is not frozen")
    if translation.get("status") != "READY" or translation.get("conformance_status") != "PASS":
        raise Go2SessionError("Go2 Translation is not READY/PASS")

    selected_runtime_lock_path = _factory_selected_path(
        root,
        runtime_lock_path,
        str(runtime_lock_path),
        "Go2 runtime lock",
    )
    try:
        runtime_lock_artifact = load_json_artifact(selected_runtime_lock_path)
    except Exception as exc:
        raise Go2SessionError("Go2 runtime lock failed strict loading") from exc
    if runtime_lock_artifact.sha256 != selected_runtime_lock_sha256:
        raise Go2SessionError(
            "Go2 runtime lock hash does not match the manifest-selected current bytes"
        )
    runtime_lock = runtime_lock_artifact.value
    expected_runtime_lock = (
        (
            GO2_EXPERIMENTAL_ARM64_RUNTIME_ID,
            GO2_EXPERIMENTAL_ARM64_RUNTIME_VERSION,
            GO2_EXPERIMENTAL_ARM64_RUNTIME_STATUS,
        )
        if experimental_arm64
        else (GO2_RUNTIME_ID, GO2_RUNTIME_VERSION, "FROZEN_FROM_VERIFIED_LINUX_BUILD")
    )
    if (
        runtime_lock.get("runtime_id"),
        runtime_lock.get("version"),
        runtime_lock.get("status"),
    ) != expected_runtime_lock:
        if experimental_arm64:
            raise Go2SessionError(
                "experimental Go2 runtime lock is not the verified native arm64 build"
            )
        raise Go2SessionError("Go2 runtime lock is not the frozen verified build")
    if experimental_arm64:
        lock_platform = runtime_lock.get("platform")
        if not isinstance(lock_platform, Mapping) or (
            lock_platform.get("os"),
            lock_platform.get("architecture"),
            lock_platform.get("python"),
        ) != ("Ubuntu 22.04", "arm64", "3.10"):
            raise Go2SessionError("experimental Go2 runtime lock is not native Linux arm64")
        native_libraries = runtime_lock.get("native_library_fingerprints")
        if not isinstance(native_libraries, list) or not any(
            isinstance(item, Mapping)
            and isinstance(item.get("path"), str)
            and item["path"].endswith("/crc_aarch64.so")
            for item in native_libraries
        ):
            raise Go2SessionError("experimental Go2 runtime lock does not bind crc_aarch64.so")
    if runtime_lock.get("unresolved") != []:
        raise Go2SessionError("Go2 runtime lock contains unresolved items")
    entrypoint = runtime_lock.get("mujoco_entrypoint")
    if not isinstance(entrypoint, Mapping) or (
        entrypoint.get("path"),
        entrypoint.get("sha256"),
        entrypoint.get("asset_closure_sha256"),
        entrypoint.get("complete_asset_closure_verified"),
    ) != ("unitree_robots/go2/scene.xml", GO2_SCENE_SHA256, GO2_ASSET_CLOSURE_SHA256, True):
        raise Go2SessionError("Go2 runtime lock MuJoCo entrypoint is not the official closure")

    selected_model = model_path
    if selected_model is None:
        selected_model = os.environ.get("AUTOADAPTER_GO2_MODEL")
    if selected_model is None:
        selected_model = Path("/opt/unitree_mujoco") / str(entrypoint["path"])
    selected_model_path = Path(selected_model)
    if not selected_model_path.is_absolute():
        selected_model_path = root / selected_model_path
    if selected_model_path.is_symlink() or not selected_model_path.is_file():
        raise Go2SessionError("selected Go2 MuJoCo scene is missing or symlinked")
    selected_model_path = selected_model_path.resolve()
    if _factory_sha256(selected_model_path, "selected MuJoCo scene") != GO2_SCENE_SHA256:
        raise Go2SessionError("selected MuJoCo scene hash is not the official frozen scene")
    _factory_verify_asset_closure(model_path=selected_model_path, runtime_entrypoint=entrypoint)
    morphology_mujoco = morphology.get("mujoco")
    if not isinstance(morphology_mujoco, Mapping) or (
        morphology_mujoco.get("simulation_entrypoint"),
        morphology_mujoco.get("simulation_entrypoint_sha256"),
        morphology_mujoco.get("asset_closure_sha256"),
    ) != ("unitree_robots/go2/scene.xml", GO2_SCENE_SHA256, GO2_ASSET_CLOSURE_SHA256):
        raise Go2SessionError("Go2 morphology does not bind the official scene closure")

    task_path = _factory_path(
        root,
        task_instances_path or GO2_TASK_RELATIVE_PATH,
        GO2_TASK_RELATIVE_PATH,
        "Go2 private task instances",
    )
    try:
        task_artifact = load_json_artifact(task_path)
    except Exception as exc:
        raise Go2SessionError("Go2 private task instances failed strict loading") from exc
    if task_artifact.sha256 != GO2_TASK_INSTANCES_SHA256:
        raise Go2SessionError("Go2 private task instances hash is not the frozen selection")
    if (
        task_artifact.value.get("artifact_type"),
        task_artifact.value.get("robot_configuration_id"),
        task_artifact.value.get("visibility"),
    ) != (
        "task_instances_private",
        ROBOT_CONFIGURATION_ID,
        "DEMO_EVALUATION_HARNESS_ONLY",
    ):
        raise Go2SessionError("Go2 private task instances are not the frozen harness-only set")

    expected_profile = _profile(None)
    if video_profile is not None and not isinstance(video_profile, FrozenVideoProfile):
        if not isinstance(video_profile, Mapping) or dict(video_profile) != expected_profile.to_dict():
            raise Go2SessionError("Go2 video profile input is not the exact frozen profile")
    selected_profile = _profile(video_profile)
    if selected_profile != expected_profile:
        raise Go2SessionError("Go2 video profile is not the frozen external-evaluation profile")
    return selected_model_path, task_path, selected_profile


def create_evaluation_robot_session(
    robot: str | Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    run_directory: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
    integration_manifest_path: str | Path | None = None,
    integration_manifest_sha256: str | None = None,
    runtime_lock_path: str | Path | None = None,
    runtime_lock_sha256: str | None = None,
    model_path: str | Path | None = None,
    task_instances_path: str | Path | None = None,
    video_profile: FrozenVideoProfile | Mapping[str, Any] | None = None,
) -> UnitreeGo2EvaluationRobotSession:
    """Construct the verified production Go2 EvaluationRobotSession.

    This factory intentionally exposes no dependency injection.  Test doubles
    belong to the direct fixture constructor; the runner-visible factory only
    returns a live, SDK2/CycloneDDS/bridge/MuJoCo session.
    """

    (
        root,
        selected_manifest_path,
        selected_manifest_hash,
        selected_runtime_lock_path,
        selected_runtime_lock_hash,
        _selected_run_directory,
    ) = _factory_runner_inputs(
        robot=robot,
        manifest_path=manifest_path,
        run_directory=run_directory,
        project_root=project_root,
        integration_manifest_path=integration_manifest_path,
        integration_manifest_sha256=integration_manifest_sha256,
        runtime_lock_path=runtime_lock_path,
        runtime_lock_sha256=runtime_lock_sha256,
    )
    if not root.is_dir():
        raise Go2SessionError(f"Go2 project root is missing: {root}")
    if selected_manifest_path is not None:
        integration_manifest_path = selected_manifest_path
    if selected_manifest_hash:
        integration_manifest_sha256 = selected_manifest_hash
    if selected_runtime_lock_path is not None:
        runtime_lock_path = selected_runtime_lock_path
    if selected_runtime_lock_hash is not None:
        runtime_lock_sha256 = selected_runtime_lock_hash
    selected_model, task_path, selected_profile = _verify_production_inputs(
        root=root,
        integration_manifest_path=integration_manifest_path,
        integration_manifest_sha256=integration_manifest_sha256,
        runtime_lock_path=runtime_lock_path,
        runtime_lock_sha256=runtime_lock_sha256,
        model_path=model_path,
        task_instances_path=task_instances_path,
        video_profile=video_profile,
    )
    try:
        session = UnitreeGo2EvaluationRobotSession(
            selected_model,
            task_instances_path=task_path,
            video_profile=selected_profile,
        )
    except Exception as exc:
        raise Go2SessionError("verified Go2 production session could not be constructed") from exc
    if session.evidence_scope != "SDK_GROUNDED_SIMULATION":
        try:
            session.close()
        except Exception:
            pass
        raise Go2SessionError("production Go2 factory did not produce SDK_GROUNDED_SIMULATION")
    return session


# Short aliases used by integration assembly and tests.
UnitreeGo2EvaluationSession = UnitreeGo2EvaluationRobotSession
Go2EvaluationRobotSession = UnitreeGo2EvaluationRobotSession


__all__ = [
    "Go2EvaluationRobotSession",
    "Go2SessionError",
    "Go2SDKError",
    "Go2TruthSample",
    "Go2ValidationEvidence",
    "Go2Transport",
    "create_evaluation_robot_session",
    "UnitreeGo2EvaluationRobotSession",
    "UnitreeGo2EvaluationSession",
    "initial_body_yaw_frame",
    "start_frame_displacement",
    "upright_score",
]
