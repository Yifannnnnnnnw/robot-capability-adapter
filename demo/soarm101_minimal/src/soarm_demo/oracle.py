"""Private validation and Demo oracle adapters.

Nothing in this module is copied into the generation snapshot or exposed as a
Demo tool.  Generated functions receive only each environment's ``runtime``.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import signal
from threading import Lock, current_thread, main_thread
import time
from typing import Any, Callable, Mapping, Sequence
from weakref import WeakKeyDictionary

from .audit import atomic_write_json
from .bridge.deterministic_tabletop import DeterministicTabletopRuntime
from .bridge.lerobot_mujoco import SO101MujocoRobot
from .bridge.mujoco_tabletop import SO101MujocoTabletopRuntime
from .bridge.scene_catalog import SceneAssetCatalog, SceneAssetCatalogError
from .direct_validation import FrameworkEvidenceError
from .task_oracle_contract import (
    FrozenOracleCondition,
    FrozenTaskOracleContract,
    FrozenTaskPredicate,
    contract_from_environment_context,
    load_default_task_oracle_contract,
)


_DEMO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MODEL_PATH = _DEMO_ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
_DEFAULT_PRIVATE_STATES = (
    _DEMO_ROOT / "private/task_library/soarm101_tabletop/v1/initial_states"
)
_DEFAULT_COMMON_RESET = _DEFAULT_PRIVATE_STATES / "_common_reset.json"
_DEFAULT_SCENE_CATALOG = (
    _DEMO_ROOT
    / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
)


class _SceneCatalogUnset:
    __slots__ = ()


_SCENE_CATALOG_UNSET = _SceneCatalogUnset()

# These values are deliberately scoped to generated validation cases.  They
# are not substitutes for the dimensions/masses in the six frozen Demo
# instances and are never added to those private instances.
P0_VALIDATION_BODY_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "cube": {"size_m": [0.03, 0.03, 0.03], "mass_kg": 0.020},
    "cylinder": {"radius_m": 0.015, "height_m": 0.040, "mass_kg": 0.020},
}

_FIXED_DEMO_INSTANCE_IDS = {
    "soarm101_p0_push_cube_to_region": "soarm101_p0_push_cube_to_region__seed_1201",
    "soarm101_p0_push_cylinder_lateral": "soarm101_p0_push_cylinder_lateral__seed_2101",
    "soarm101_p0_place_cube_in_tray": "soarm101_p0_place_cube_in_tray__seed_1601",
    "soarm101_p0_place_cube_in_bowl_new_region": (
        "soarm101_p0_place_cube_in_bowl_new_region__seed_2201"
    ),
    "soarm101_p0_place_two_objects_in_tray": (
        "soarm101_p0_place_two_objects_in_tray__seed_1901"
    ),
    "soarm101_p0_sort_two_cubes_matching_trays": (
        "soarm101_p0_sort_two_cubes_matching_trays__seed_2301"
    ),
}

_PHYSICAL_FORBIDDEN_CONDITIONS = (
    "action_clipped",
    "object_outside_workspace",
    "object_outside_table_support_polygon",
    "non_gripper_robot_table_collision",
    "actuator_or_joint_limit_exceeded",
    "simulation_nan_or_instability",
    "infrastructure_error",
)

_OBJECT_SIGNIFICANT_MOTION_M = 0.002
_GRIPPER_CONTACT_BODIES = frozenset(
    {"gripper", "moving_jaw_so101_v1", "camera_mount"}
)


class _FacadeBinding:
    __slots__ = ("backend", "after_public_call", "evidence_error")

    def __init__(
        self,
        backend: SO101MujocoRobot,
        after_public_call: Callable[[], None],
    ) -> None:
        self.backend = backend
        self.after_public_call = after_public_call
        self.evidence_error: FrameworkEvidenceError | None = None


class _DeterministicMujocoTime:
    """Generated-module time view backed by one real ``MjData`` clock.

    Validation and Demo must not let host scheduling decide how many MuJoCo
    steps occur between two generated commands.  ``sleep`` therefore advances
    the actual model/data pair and samples framework evidence at every physics
    tick, while ``monotonic`` reads simulation time.  This object replaces only
    the generated module's global ``time`` binding; harness deadlines continue
    to use the host monotonic clock.
    """

    __slots__ = ("_binding",)

    def __init__(self, binding: _FacadeBinding) -> None:
        self._binding = binding

    def monotonic(self) -> float:
        backend = self._binding.backend
        with backend._lock:
            return float(backend.data.time)

    def sleep(self, seconds: float) -> None:
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0.0 or duration > 60.0:
            raise ValueError(
                "generated sleep must be a finite duration between 0 and 60 seconds"
            )
        if duration == 0.0:
            return
        _invoke_framework_callback(
            lambda: _advance_with_sampling(
                self._binding.backend,
                duration,
                self._binding.after_public_call,
            )
        )


def prepare_generated_callable_for_mujoco(
    function: Callable[..., Any], runtime: Any
) -> None:
    """Bind generated ``time`` calls to deterministic MuJoCo simulation time.

    The runtime remains the same closed LeRobot facade seen by generated code.
    This framework-only hook is called after static validation and before each
    direct/tool invocation.  It never exposes the backend or privileged state
    through function arguments or globals.
    """

    binding = _facade_binding(runtime)
    if binding.backend.auto_step:
        raise FrameworkEvidenceError(
            "generated MuJoCo calls require deterministic auto_step=False"
        )
    globals_view = getattr(function, "__globals__", None)
    if not isinstance(globals_view, dict):
        raise FrameworkEvidenceError("generated callable has no writable globals")
    for imported_name in ("sleep", "monotonic"):
        if globals_view.get(imported_name) is getattr(time, imported_name):
            raise FrameworkEvidenceError(
                "generated time functions must be accessed through `import time`"
            )
    existing = globals_view.get("time")
    if existing is None:
        return
    if existing is not time and not isinstance(existing, _DeterministicMujocoTime):
        raise FrameworkEvidenceError(
            "generated module time binding is not the standard time module"
        )
    globals_view["time"] = _DeterministicMujocoTime(binding)


_FACADE_BINDINGS: WeakKeyDictionary["_LeRobotControlFacade", _FacadeBinding] = (
    WeakKeyDictionary()
)
_FACADE_BINDINGS_LOCK = Lock()


def _framework_evidence_exception(
    message: str, cause: BaseException
) -> FrameworkEvidenceError:
    if isinstance(cause, FrameworkEvidenceError):
        return cause
    error = FrameworkEvidenceError(message)
    error.__cause__ = cause
    return error


def _invoke_framework_callback(callback: Callable[[], None]) -> None:
    """Run evidence work outside an active generated-function wall timer."""

    paused_timer: tuple[float, float] | None = None
    timer_supported = all(
        hasattr(signal, name)
        for name in ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
    )
    if current_thread() is main_thread() and timer_supported:
        try:
            active_timer = signal.getitimer(signal.ITIMER_REAL)
            if active_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                paused_timer = active_timer
        except (OSError, ValueError) as exc:
            raise FrameworkEvidenceError(
                "could not pause the generated-function deadline for evidence capture"
            ) from exc
    try:
        try:
            callback()
        except FrameworkEvidenceError:
            raise
        except BaseException as exc:
            raise FrameworkEvidenceError(
                "framework evidence callback failed"
            ) from exc
    finally:
        if paused_timer is not None:
            try:
                signal.setitimer(signal.ITIMER_REAL, *paused_timer)
            except (OSError, ValueError) as exc:
                raise FrameworkEvidenceError(
                    "could not restore the generated-function deadline after evidence capture"
                ) from exc


class _LeRobotControlFacade:
    """Closed generated-code view of the pinned LeRobot control contract.

    The underlying simulator is held in a slot whose name is denied by
    ``__getattribute__``.  Static validation is the first boundary; this
    runtime object is the independent dynamic boundary.
    """

    __slots__ = ("__weakref__",)
    _PUBLIC_MEMBERS = frozenset(
        {
            "send_action",
            "get_observation",
            "action_features",
            "observation_features",
            "is_connected",
            "is_calibrated",
            "name",
        }
    )

    def __init__(
        self,
        backend: SO101MujocoRobot,
        after_public_call: Callable[[], None],
    ) -> None:
        with _FACADE_BINDINGS_LOCK:
            _FACADE_BINDINGS[self] = _FacadeBinding(backend, after_public_call)

    def __getattribute__(self, name: str) -> Any:
        if name not in _LeRobotControlFacade._PUBLIC_MEMBERS:
            raise AttributeError(
                f"generated runtime exposes only the LeRobot control contract; {name!r} is unavailable"
            )
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        del value
        raise AttributeError(f"generated runtime facade is immutable; cannot set {name!r}")

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        binding = _facade_binding(self)
        result = binding.backend.send_action(action)
        try:
            _invoke_framework_callback(binding.after_public_call)
        except FrameworkEvidenceError as exc:
            binding.evidence_error = exc
            raise
        return result

    def get_observation(self) -> dict[str, float]:
        binding = _facade_binding(self)
        result = binding.backend.get_observation()
        try:
            _invoke_framework_callback(binding.after_public_call)
        except FrameworkEvidenceError as exc:
            binding.evidence_error = exc
            raise
        return result

    @property
    def action_features(self) -> dict[str, type]:
        return dict(_facade_binding(self).backend.action_features)

    @property
    def observation_features(self) -> dict[str, type]:
        return dict(_facade_binding(self).backend.observation_features)

    @property
    def is_connected(self) -> bool:
        return bool(_facade_binding(self).backend.is_connected)

    @property
    def is_calibrated(self) -> bool:
        return bool(_facade_binding(self).backend.is_calibrated)

    @property
    def name(self) -> str:
        return str(_facade_binding(self).backend.name)


def _facade_binding(
    facade: _LeRobotControlFacade,
) -> _FacadeBinding:
    with _FACADE_BINDINGS_LOCK:
        try:
            return _FACADE_BINDINGS[facade]
        except KeyError as exc:
            raise RuntimeError("generated runtime facade is closed") from exc


def _release_facade(facade: _LeRobotControlFacade) -> None:
    with _FACADE_BINDINGS_LOCK:
        binding = _FACADE_BINDINGS.pop(facade, None)
    if binding is not None and binding.evidence_error is not None:
        raise binding.evidence_error


class _ObservedSO101MujocoTabletopRuntime(SO101MujocoTabletopRuntime):
    """Private backend that samples forbidden evidence after every clock tick."""

    def __init__(self, *args: Any, step_observer: Callable[[], None], **kwargs: Any) -> None:
        self._step_observer = step_observer
        self._observer_error: BaseException | None = None
        super().__init__(*args, **kwargs)

    def _clock_loop(self) -> None:
        period = 1.0 / self.simulation_hz
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            next_tick += period
            with self._lock:
                self._mj.mj_step(self.model, self.data)
                try:
                    self._step_observer()
                except BaseException as exc:
                    self._observer_error = _framework_evidence_exception(
                        "framework background evidence observer failed", exc
                    )
                    self._stop_event.set()
                    return
            wait = next_tick - time.monotonic()
            if wait > 0:
                self._stop_event.wait(wait)
            else:
                next_tick = time.monotonic()


class _MujocoEpisodeVideoRecorder:
    """Framework-only MP4 recorder rendered from the actual ``MjModel/MjData``.

    Frames come from :class:`mujoco.Renderer`; there is deliberately no fake
    or software-diagram fallback.  If the framework launcher has not provided
    a valid graphics context, requesting video fails as infrastructure.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        common_reset: Mapping[str, Any],
        fps: float = 20.0,
        width: int = 640,
        height: int = 480,
    ) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() != ".mp4":
            raise ValueError("framework simulation video_path must end in .mp4")
        self.fps = float(fps)
        if not math.isfinite(self.fps) or self.fps <= 0:
            raise ValueError("video_fps must be a finite positive number")
        self.width = int(width)
        self.height = int(height)
        if self.width < 160 or self.height < 120:
            raise ValueError("video dimensions must be at least 160x120")
        self._common_reset = deepcopy(dict(common_reset))
        self._writer: Any = None
        self._renderer: Any = None
        self._camera: Any = None
        self._cv2: Any = None
        self._lock = Lock()
        self._next_frame_time = 0.0
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._resolved_scene_binding: dict[str, Any] | None = None
        self.frames = 0

    @property
    def metadata_path(self) -> Path:
        return self.path.with_suffix(".metadata.json")

    def start(self, runtime: SO101MujocoTabletopRuntime) -> None:
        self.close()
        try:
            import cv2
        except BaseException as exc:  # pragma: no cover - dependency preflight
            raise FrameworkEvidenceError("OpenCV is required for MP4 recording") from exc
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(
                str(self.path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                self.fps,
                (self.width, self.height),
            )
        except BaseException as exc:
            raise FrameworkEvidenceError("could not initialize the MP4 writer") from exc
        try:
            writer_opened = bool(writer.isOpened())
        except BaseException as exc:
            try:
                writer.release()
            except BaseException:
                pass
            raise FrameworkEvidenceError(
                "could not inspect the framework MP4 writer"
            ) from exc
        if not writer_opened:
            try:
                writer.release()
            except BaseException as exc:
                raise FrameworkEvidenceError(
                    "failed to release an unopened framework MP4 writer"
                ) from exc
            raise FrameworkEvidenceError("could not open the framework MP4 writer")
        renderer: Any = None
        try:
            renderer = runtime._mj.Renderer(
                runtime.model,
                height=self.height,
                width=self.width,
            )
            camera = runtime._mj.MjvCamera()
            table = self._common_reset.get("table", {})
            center = [
                float(value)
                for value in table.get("center_m", [0.35, 0.0, 0.01])
            ]
            camera.lookat[:] = [center[0], center[1], max(0.08, center[2])]
            camera.distance = 0.85
            camera.azimuth = 135.0
            camera.elevation = -50.0
        except BaseException as exc:
            try:
                writer.release()
            except BaseException:
                pass
            if renderer is not None:
                try:
                    renderer.close()
                except BaseException:
                    pass
            raise FrameworkEvidenceError(
                "MuJoCo video renderer initialization failed; run through the "
                "framework graphics-enabled launcher"
            ) from exc
        self._cv2 = cv2
        self._writer = writer
        self._renderer = renderer
        self._camera = camera
        self.frames = 0
        self._resolved_scene_binding = _resolved_scene_binding_projection(
            runtime.resolved_instance_evidence
        )
        now = float(runtime.data.time)
        self._start_time = now
        self._end_time = now
        self._next_frame_time = now
        self.capture(runtime, force=True)

    def capture(
        self, runtime: SO101MujocoTabletopRuntime, *, force: bool = False
    ) -> None:
        writer = self._writer
        if writer is None:
            return
        # Lock order is always MuJoCo state -> encoder.  The real-time clock
        # invokes this while already holding the backend's RLock, while facade
        # calls acquire it here after their public operation completes.
        try:
            with runtime._lock:
                simulation_time = float(runtime.data.time)
                if not force and simulation_time + 1e-12 < self._next_frame_time:
                    return
                with self._lock:
                    if self._writer is None:
                        return
                    frame = self._render_mujoco(runtime)
                    self._writer.write(frame)
                    self.frames += 1
                    self._end_time = simulation_time
                    self._next_frame_time = simulation_time + 1.0 / self.fps
        except FrameworkEvidenceError:
            raise
        except BaseException as exc:
            raise FrameworkEvidenceError("MuJoCo video capture failed") from exc

    def _render_mujoco(self, runtime: SO101MujocoTabletopRuntime) -> Any:
        renderer = self._renderer
        if renderer is None:
            raise FrameworkEvidenceError("MuJoCo video renderer is not initialized")
        try:
            renderer.update_scene(runtime.data, camera=self._camera)
            rgb = renderer.render()
        except BaseException as exc:
            raise FrameworkEvidenceError("MuJoCo video frame rendering failed") from exc
        # OpenCV's encoder consumes BGR. Copy removes the negative stride from
        # channel reversal before handing memory to the native writer.
        return rgb[:, :, ::-1].copy()

    def close(self) -> None:
        writer = self._writer
        renderer = self._renderer
        if writer is None and renderer is None:
            return
        failure: FrameworkEvidenceError | None = None
        with self._lock:
            writer = self._writer
            renderer = self._renderer
            self._writer = None
            self._renderer = None
            self._camera = None
            if writer is not None:
                try:
                    writer.release()
                except BaseException as exc:
                    failure = _framework_evidence_exception(
                        "framework MP4 writer release failed", exc
                    )
            if renderer is not None:
                try:
                    renderer.close()
                except BaseException as exc:
                    failure = failure or _framework_evidence_exception(
                        "MuJoCo video renderer close failed", exc
                    )
        try:
            atomic_write_json(
                self.metadata_path,
                {
                    "schema_version": "robot_capability.mujoco_video_evidence.v1",
                    "video_path": self.path.name,
                    "renderer_kind": "mujoco.Renderer",
                    "codec": "mp4v",
                    "fps": self.fps,
                    "frames": self.frames,
                    "width": self.width,
                    "height": self.height,
                    "simulation_start_s": self._start_time,
                    "simulation_end_s": self._end_time,
                    "resolved_scene_binding": self._resolved_scene_binding,
                },
            )
        except BaseException as exc:
            failure = failure or _framework_evidence_exception(
                "MuJoCo video metadata write failed", exc
            )
        if failure is not None:
            raise failure


_FIXTURE_SCENE_ROLES = {
    "bodies": frozenset({"dynamic_object", "receptacle"}),
    "markers": frozenset({"marker"}),
}


def _fixture_plain_value(value: Any) -> Any:
    """Detach immutable catalog values for the ephemeral fixture projection."""

    if isinstance(value, Mapping):
        return {str(key): _fixture_plain_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_fixture_plain_value(item) for item in value]
    return value


def _project_catalog_scene_for_deterministic_fixture(
    initial_state: Mapping[str, Any],
    catalog: SceneAssetCatalog,
) -> dict[str, Any]:
    """Build a detached legacy view for the explicitly nonphysical fixture.

    Formal task instances remain asset-ref-only and untouched.  The tiny
    deterministic runtime intentionally has no catalog dependency, so this
    private oracle boundary supplies only the resolved legacy ``kind`` and
    geometry/pose fields it understands.  Material and contact physics are
    deliberately omitted because fixture outcomes are not physical evidence.
    """

    catalog.verify_unchanged()
    projected = deepcopy(dict(initial_state))
    for collection, allowed_roles in _FIXTURE_SCENE_ROLES.items():
        raw_items = projected.get(collection, [])
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise TypeError(f"fixture initial_state.{collection} must be an array")
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, Mapping):
                raise TypeError(
                    f"fixture initial_state.{collection}[{index}] must be an object"
                )
            item = deepcopy(dict(raw))
            if "asset_ref" in raw:
                resolved = catalog.resolve(raw, expected_role=None)
                if resolved.role not in allowed_roles:
                    raise SceneAssetCatalogError(
                        f"asset_ref role {resolved.role!r} is invalid in {collection}"
                    )
                item["kind"] = resolved.runtime_kind
                fixture_parameters = _fixture_plain_value(resolved.parameters)
                fixture_parameters.pop("mass_kg", None)
                item.update(fixture_parameters)
            items.append(item)
        if collection in projected or items:
            projected[collection] = items
    return projected


class FixtureValidationEnvironment:
    """Private oracle for the deterministic no-LLM orchestration fixture."""

    def __init__(self, _: Mapping[str, Any] | None = None) -> None:
        self._scene_catalog = SceneAssetCatalog(_DEFAULT_SCENE_CATALOG)
        self.runtime = DeterministicTabletopRuntime()

    def reset(self, initial_state: Mapping[str, Any]) -> None:
        self.runtime.reset(
            _project_catalog_scene_for_deterministic_fixture(
                initial_state,
                self._scene_catalog,
            )
        )

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
        snapshot = self.runtime.snapshot()
        return _select_measurements(snapshot, requests)

    def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]:
        snapshot = self.runtime.snapshot()
        evidence: dict[str, Any] = {}
        for condition in conditions:
            if condition == "action_clipped":
                evidence[condition] = bool(snapshot["action_clipped"])
            elif condition == "object_outside_workspace":
                evidence[condition] = any(
                    not (0.18 <= point[0] <= 0.50 and -0.18 <= point[1] <= 0.18)
                    for point in snapshot["object_positions_m"].values()
                )
            else:
                evidence[condition] = False
        return evidence

    def close(self) -> None:
        self.runtime.disconnect()


class FixtureDemoEnvironment:
    """Per-task fixture world with private deterministic task scoring."""

    def __init__(self, context: Mapping[str, Any] | None = None) -> None:
        self._scene_catalog = SceneAssetCatalog(_DEFAULT_SCENE_CATALOG)
        self._oracle_contract = (
            contract_from_environment_context(context)
            or load_default_task_oracle_contract()
        )
        self.runtime = DeterministicTabletopRuntime()
        self._resolved_instance: dict[str, Any] = {}

    def reset(self, instance: Mapping[str, Any]) -> None:
        task_id = str(instance.get("task_id", ""))
        self._oracle_contract.predicate_for_task(task_id)
        self._resolved_instance = _project_catalog_scene_for_deterministic_fixture(
            instance,
            self._scene_catalog,
        )
        self.runtime.reset(self._resolved_instance)

    def score(self, task: Mapping[str, Any], instance: Mapping[str, Any]) -> Mapping[str, Any]:
        task_id = str(instance["task_id"])
        if task.get("task_id") not in (None, task_id):
            raise ValueError("task and private instance IDs do not match")
        if self._resolved_instance.get("task_id") != task_id:
            raise ValueError("score instance does not match the reset instance")
        predicate = self._oracle_contract.predicate_for_task(task_id)
        snapshot = self.runtime.snapshot()
        positions = snapshot["object_positions_m"]
        agent_input = instance["agent_input"]
        measurements: dict[str, Any] = {}
        if task_id in {"soarm101_p0_push_cube_to_region", "soarm101_p0_push_cylinder_lateral"}:
            distance_condition = predicate.condition(
                "object_xy_distance_to_target_center_m"
            )
            object_id = str(distance_condition.fields["object_id"])
            tolerance = distance_condition.number("tolerance")
            speed_tolerance = predicate.condition(
                "object_linear_speed_m_s"
            ).number("tolerance")
            lift_tolerance = predicate.condition(
                "maximum_object_lift_above_initial_m"
            ).number("tolerance")
            tilt_tolerance = (
                predicate.condition("object_axis_tilt_rad").number("tolerance")
                if task_id == "soarm101_p0_push_cylinder_lateral"
                else None
            )
            target = _resolve_agent_input_ref(
                agent_input, str(distance_condition.fields["target_ref"])
            )
            if not isinstance(target, Sequence) or isinstance(target, (str, bytes)):
                raise ValueError("push target_ref must resolve to a position array")
            point = positions[object_id]
            distance = math.hypot(point[0] - target[0], point[1] - target[1])
            measurements = {
                "object_id": object_id,
                "xy_distance_to_target_m": distance,
                "tolerance_m": tolerance,
                "object_linear_speed_m_s": 0.0,
                "speed_tolerance_m_s": speed_tolerance,
                "maximum_object_lift_above_initial_m": float(
                    snapshot["maximum_object_lift_above_initial_m"].get(
                        object_id, 0.0
                    )
                ),
                "maximum_lift_tolerance_m": lift_tolerance,
            }
            if tilt_tolerance is not None:
                measurements["object_axis_tilt_rad"] = 0.0
                measurements["axis_tilt_tolerance_rad"] = tilt_tolerance
            passed = (
                _within(distance, tolerance)
                and _within(0.0, speed_tolerance)
                and _within(
                    measurements["maximum_object_lift_above_initial_m"],
                    lift_tolerance,
                )
                and (
                    tilt_tolerance is None or _within(0.0, tilt_tolerance)
                )
            )
        elif task_id in {"soarm101_p0_place_cube_in_tray", "soarm101_p0_place_cube_in_bowl_new_region"}:
            if task_id == "soarm101_p0_place_cube_in_tray":
                containment = predicate.condition(
                    "object_footprint_contained_by_receptacle"
                )
                object_id = str(containment.fields["object_id"])
                receptacle_id = str(containment.fields["receptacle_id"])
                contained = _fixture_inside_tray(
                    snapshot,
                    self._resolved_instance,
                    object_id,
                    receptacle_id,
                    margin_m=_condition_parameter_number(
                        containment, "minimum_wall_margin_m"
                    ),
                )
            else:
                containment = predicate.condition("object_contained_by_bowl")
                object_id = str(containment.fields["object_id"])
                receptacle_id = str(containment.fields["receptacle_id"])
                contained = _fixture_inside_bowl(
                    snapshot,
                    self._resolved_instance,
                    object_id,
                    receptacle_id,
                    maximum_radial_center_distance_m=_condition_parameter_number(
                        containment, "maximum_radial_center_distance_m"
                    ),
                    maximum_center_height_above_rim_m=_condition_parameter_number(
                        containment, "maximum_center_height_above_rim_m"
                    ),
                )
            measurements = {
                "object_id": object_id,
                "receptacle_id": receptacle_id,
                "contained": contained,
                "released": snapshot["attached_object"] is None,
                "object_linear_speed_m_s": 0.0,
                "speed_tolerance_m_s": predicate.condition(
                    "object_linear_speed_m_s"
                ).number("tolerance"),
            }
            passed = (
                contained
                and snapshot["attached_object"] is None
                and _within(0.0, measurements["speed_tolerance_m_s"])
            )
        elif task_id == "soarm101_p0_place_two_objects_in_tray":
            event_condition = predicate.condition(
                "successful_containment_event_order"
            )
            expected_value = _resolve_agent_input_ref(
                agent_input,
                str(event_condition.fields["expected_object_ids_ref"]),
            )
            if not isinstance(expected_value, Sequence) or isinstance(
                expected_value, (str, bytes)
            ):
                raise ValueError("expected_object_ids_ref must resolve to an array")
            expected = list(expected_value)
            containment = predicate.condition(
                "all_object_footprints_contained_by_receptacle"
            )
            receptacle_id = str(containment.fields["receptacle_id"])
            margin = _condition_parameter_number(
                containment, "minimum_wall_margin_m"
            )
            event_margin = _condition_parameter_number(
                event_condition, "minimum_wall_margin_m"
            )
            event_window = _finite_contract_number(
                event_condition.fields["parameters"]["stable_placement_event"][
                    "continuous_window_s"
                ],
                label="stable placement event window",
            )
            speed_tolerance = predicate.condition(
                "max_object_linear_speed_m_s"
            ).number("tolerance")
            contained = {
                name: _fixture_inside_tray(
                    snapshot,
                    self._resolved_instance,
                    name,
                    receptacle_id,
                    margin_m=margin,
                )
                for name in expected
            }
            measurements = {
                "contained": contained,
                "expected_event_order": expected,
                "observed_event_order": snapshot["containment_event_order"],
                "containment_margin_m": margin,
                "event_margin_m": event_margin,
                "stable_placement_event_window_s": event_window,
                "max_object_linear_speed_m_s": 0.0,
                "speed_tolerance_m_s": speed_tolerance,
            }
            passed = (
                all(contained.values())
                and snapshot["containment_event_order"] == expected
                and _within(0.0, speed_tolerance)
            )
        elif task_id == "soarm101_p0_sort_two_cubes_matching_trays":
            assignment_condition = predicate.condition(
                "object_receptacle_assignment"
            )
            expected_value = _resolve_agent_input_ref(
                agent_input,
                str(assignment_condition.fields["expected_mapping_ref"]),
            )
            if not isinstance(expected_value, Mapping):
                raise ValueError("expected_mapping_ref must resolve to an object")
            expected_map = dict(expected_value)
            containment = predicate.condition(
                "all_object_footprints_contained_by_assigned_receptacle"
            )
            margin = _condition_parameter_number(
                containment, "minimum_wall_margin_m"
            )
            speed_tolerance = predicate.condition(
                "max_object_linear_speed_m_s"
            ).number("tolerance")
            assignment = {
                object_id: receptacle_id
                for object_id, receptacle_id in expected_map.items()
                if _fixture_inside_tray(
                    snapshot,
                    self._resolved_instance,
                    object_id,
                    receptacle_id,
                    margin_m=margin,
                )
            }
            measurements = {
                "expected_assignment": expected_map,
                "observed_assignment": assignment,
                "minimum_wall_margin_m": margin,
                "max_object_linear_speed_m_s": 0.0,
                "speed_tolerance_m_s": speed_tolerance,
            }
            passed = (
                assignment == expected_map
                and snapshot["attached_object"] is None
                and _within(0.0, speed_tolerance)
            )
        else:
            measurements = {"unsupported_task": task_id}
            passed = False
        return {
            "schema_version": "robot_capability.private_demo_oracle_result.v1",
            "passed": passed,
            "task_id": task_id,
            "measurements": measurements,
            "runtime_kind": "deterministic_orchestration_fixture_not_physical_evidence",
            "oracle_contract_sha256": self._oracle_contract.source_sha256,
            "evaluation_window_s": predicate.evaluation_window_s,
        }

    def close(self) -> None:
        self.runtime.disconnect()


class MujocoJointValidationEnvironment:
    """Direct-validation environment for physical joint/site measurements."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        trace_path: str | Path | None = None,
        auto_step: bool = True,
    ) -> None:
        self._ever_seen = {
            "action_clipped": False,
            "actuator_or_joint_limit_exceeded": False,
            "simulation_nan_or_instability": False,
        }
        self._mujoco_runtime = SO101MujocoRobot(
            model_path,
            auto_step=auto_step,
            trace_path=trace_path,
        )
        self.runtime = _LeRobotControlFacade(
            self._mujoco_runtime, self._sample_episode_evidence
        )
        self._mujoco_runtime.connect(calibrate=False)

    def reset(self, initial_state: Mapping[str, Any]) -> None:
        qpos = initial_state.get("qpos_rad", {})
        self._mujoco_runtime.reset(qpos=qpos if isinstance(qpos, Mapping) else None)
        self._ever_seen = {name: False for name in self._ever_seen}
        self._sample_episode_evidence()

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
        observation = self._mujoco_runtime.get_observation()
        available = {
            "joint_positions": observation,
            "end_effector_position_m": list(self._mujoco_runtime.site_position()),
        }
        self._sample_episode_evidence()
        return _select_measurements(available, requests)

    def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]:
        self._sample_episode_evidence()
        result: dict[str, Any] = {}
        for name in conditions:
            if name not in self._ever_seen:
                raise KeyError(f"joint oracle does not implement forbidden condition {name!r}")
            result[name] = self._ever_seen[name]
        return result

    def _sample_episode_evidence(self) -> None:
        receipt = self._mujoco_runtime.last_receipt
        self._ever_seen["action_clipped"] |= bool(
            receipt and any(receipt.clipped.values())
        )
        self._ever_seen["actuator_or_joint_limit_exceeded"] |= _joint_limit_evidence(
            self._mujoco_runtime, margin_rad=0.0
        )
        with self._mujoco_runtime._lock:
            finite = bool(
                self._mujoco_runtime._np.isfinite(self._mujoco_runtime.data.qpos).all()
                and self._mujoco_runtime._np.isfinite(self._mujoco_runtime.data.qvel).all()
            )
        self._ever_seen["simulation_nan_or_instability"] |= not finite

    def close(self) -> None:
        self._mujoco_runtime.disconnect()
        _release_facade(self.runtime)


class MujocoTabletopValidationEnvironment:
    """Private direct-call environment backed by an actual tabletop ``MjData``.

    The constructor accepts the validation case so a framework-owned trace
    path can be injected through ``framework_metadata``.  That metadata is
    consumed here and is not copied into the initial state or passed to the
    generated function.
    """

    def __init__(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        model_path: str | Path = _DEFAULT_MODEL_PATH,
        common_reset: Mapping[str, Any] | str | Path = _DEFAULT_COMMON_RESET,
        trace_path: str | Path | None = None,
        video_path: str | Path | None = None,
        video_fps: float = 20.0,
        video_width: int = 640,
        video_height: int = 480,
        auto_step: bool = True,
        reset_settle_s: float = 0.10,
        measurement_settle_s: float = 0.10,
        scene_catalog: (
            Mapping[str, Any] | str | Path | None | _SceneCatalogUnset
        ) = _SCENE_CATALOG_UNSET,
        scene_freeze: Mapping[str, Any] | str | Path | None = None,
        require_scene_catalog: bool = False,
        require_explicit_asset_refs: bool = False,
    ) -> None:
        self._common_reset = _read_mapping(common_reset, label="common_reset")
        self._reset_settle_s = _nonnegative(reset_settle_s, label="reset_settle_s")
        self._measurement_settle_s = _nonnegative(
            measurement_settle_s, label="measurement_settle_s"
        )
        self._evidence_enabled = False
        self._episode_ever_seen = {
            name: False for name in _PHYSICAL_FORBIDDEN_CONDITIONS
        }
        self._episode_initial_object_z: dict[str, float] = {}
        self._episode_maximum_object_z: dict[str, float] = {}
        self._diagnostic_episode_start_time_s = 0.0
        self._diagnostic_lock = Lock()
        self._object_motion_diagnostics: dict[str, dict[str, Any]] = {}
        self._limit_violation_diagnostics: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        self._diagnostic_contact_pairs_ever: set[tuple[str, str]] = set()
        self._resolved_instance: dict[str, Any] = {}
        private_trace = _framework_trace_path(context, explicit=trace_path)
        private_video = _framework_video_path(context, explicit=video_path)
        self._video = (
            None
            if private_video is None
            else _MujocoEpisodeVideoRecorder(
                private_video,
                common_reset=self._common_reset,
                fps=video_fps,
                width=video_width,
                height=video_height,
            )
        )
        selected_scene_catalog = (
            _DEFAULT_SCENE_CATALOG
            if scene_catalog is _SCENE_CATALOG_UNSET
            else scene_catalog
        )
        self._mujoco_runtime = _ObservedSO101MujocoTabletopRuntime(
            model_path,
            common_reset=self._common_reset,
            auto_step=auto_step,
            trace_path=private_trace,
            scene_catalog=selected_scene_catalog,
            scene_freeze=scene_freeze,
            require_scene_catalog=require_scene_catalog,
            require_explicit_asset_refs=require_explicit_asset_refs,
            # Native OpenGL rendering must stay on the framework/Python main
            # thread on macOS.  The real-time clock samples safety evidence on
            # every tick; facade and harness callbacks record video frames.
            step_observer=self._sample_episode_evidence,
        )
        self.runtime = _LeRobotControlFacade(
            self._mujoco_runtime, self._observe_simulation_state
        )
        self._mujoco_runtime.connect(calibrate=False)
        self._trace_start = 0

    def reset(self, initial_state: Mapping[str, Any]) -> None:
        self._evidence_enabled = False
        self._mujoco_runtime._observer_error = None
        scene, qpos_rad = _validation_scene(initial_state)
        self._mujoco_runtime.reset(scene)
        self._resolved_instance = _oracle_instance_from_resolved_scene(
            scene,
            self._mujoco_runtime.resolved_instance_evidence,
        )
        if qpos_rad:
            self._mujoco_runtime.reset(qpos=qpos_rad)
        if self._video is not None:
            self._video.start(self._mujoco_runtime)
        if self._reset_settle_s:
            _advance_with_sampling(
                self._mujoco_runtime,
                self._reset_settle_s,
                self._observe_simulation_state,
            )
        self._episode_ever_seen = {
            name: False for name in _PHYSICAL_FORBIDDEN_CONDITIONS
        }
        initial_snapshot = self._mujoco_runtime.world_snapshot()
        self._diagnostic_episode_start_time_s = float(
            initial_snapshot["simulation_time_s"]
        )
        with self._diagnostic_lock:
            self._object_motion_diagnostics = {}
            self._limit_violation_diagnostics = {}
            self._diagnostic_contact_pairs_ever = set()
        self._evidence_enabled = True
        self._sample_episode_evidence(initial_snapshot)
        self._trace_start = len(self._mujoco_runtime.trace_events)

    def prepare_generated_function(self, function: Callable[..., Any]) -> None:
        """Bind generated waits to this case's deterministic MuJoCo clock."""

        prepare_generated_callable_for_mujoco(function, self.runtime)

    def measure(self, requests: Mapping[str, Any]) -> Mapping[str, Any]:
        _raise_observer_failure(self._mujoco_runtime)
        if self._measurement_settle_s:
            _advance_with_sampling(
                self._mujoco_runtime,
                self._measurement_settle_s,
                self._observe_simulation_state,
            )
        _raise_observer_failure(self._mujoco_runtime)
        snapshot = self._mujoco_runtime.world_snapshot()
        self._sample_episode_evidence(snapshot)
        contacts = deepcopy(snapshot["contacts"])
        available = {
            "joint_positions": snapshot["joint_positions"],
            "end_effector_position_m": snapshot["end_effector_position_m"],
            "object_positions_m": {
                name: state["position_m"] for name, state in snapshot["objects"].items()
            },
            # ``contact`` is the simple boolean measurement; ``contacts`` and
            # ``contact_pairs`` retain inspectable physical evidence for suites
            # that need more than existence.
            "contact": bool(contacts),
            "contacts": contacts,
            "contact_pairs": [
                [contact["body1"], contact["body2"]] for contact in contacts
            ],
            "finite": bool(snapshot["finite_state"]),
            "finite_state": bool(snapshot["finite_state"]),
        }
        return _select_measurements(available, requests)

    def forbidden(self, conditions: Sequence[str]) -> Mapping[str, Any]:
        _raise_observer_failure(self._mujoco_runtime)
        self._sample_episode_evidence()
        evidence: dict[str, Any] = {}
        for condition in conditions:
            if condition not in self._episode_ever_seen:
                raise KeyError(
                    f"physical oracle does not implement forbidden condition {condition!r}"
                )
            evidence[condition] = self._episode_ever_seen[condition]
        return evidence

    def diagnostics(self) -> Mapping[str, Any]:
        """Return compact physical repair evidence without private oracle data."""

        _raise_observer_failure(self._mujoco_runtime)
        terminal = self._mujoco_runtime.world_snapshot()
        self._record_episode_diagnostics(terminal)
        events = self._mujoco_runtime.trace_events[self._trace_start :]
        snapshots = [
            event["snapshot"]
            for event in events
            if event.get("event") == "world_state"
            and isinstance(event.get("snapshot"), Mapping)
        ]
        if not snapshots:
            snapshots = [terminal]
        end_effector_path = [
            tuple(float(value) for value in snapshot["end_effector_position_m"])
            for snapshot in snapshots
        ]
        path_min = [min(point[axis] for point in end_effector_path) for axis in range(3)]
        path_max = [max(point[axis] for point in end_effector_path) for axis in range(3)]
        object_ids = sorted(terminal["objects"])
        minimum_center_distance: dict[str, float] = {}
        for object_id in object_ids:
            distances: list[float] = []
            for snapshot in snapshots:
                state = snapshot.get("objects", {}).get(object_id)
                if not isinstance(state, Mapping):
                    continue
                object_position = state.get("position_m")
                end_effector_position = snapshot.get("end_effector_position_m")
                if (
                    isinstance(object_position, Sequence)
                    and isinstance(end_effector_position, Sequence)
                    and len(object_position) == 3
                    and len(end_effector_position) == 3
                ):
                    distances.append(
                        math.sqrt(
                            sum(
                                (float(end_effector_position[axis]) - float(object_position[axis]))
                                ** 2
                                for axis in range(3)
                            )
                        )
                    )
            if distances:
                minimum_center_distance[object_id] = min(distances)

        action_events = [event for event in events if event.get("event") == "action"]
        clipped_action_count = sum(
            any(bool(value) for value in event.get("clipped", {}).values())
            for event in action_events
            if isinstance(event.get("clipped"), Mapping)
        )
        receipt = self._mujoco_runtime.last_receipt
        terminal_simulation_time_s = float(terminal["simulation_time_s"])
        if (
            not math.isfinite(terminal_simulation_time_s)
            or terminal_simulation_time_s < 0.0
        ):
            raise FrameworkEvidenceError(
                "terminal MuJoCo simulation time must be finite and non-negative"
            )
        simulation_time_since_last_action_s: float | None = None
        if receipt is not None:
            last_action_simulation_time_s = float(receipt.simulation_time)
            if (
                not math.isfinite(last_action_simulation_time_s)
                or last_action_simulation_time_s < 0.0
                or last_action_simulation_time_s > terminal_simulation_time_s + 1e-9
            ):
                raise FrameworkEvidenceError(
                    "last-action MuJoCo simulation time must be finite, non-negative, "
                    "and no later than terminal simulation time"
                )
            simulation_time_since_last_action_s = max(
                0.0,
                terminal_simulation_time_s - last_action_simulation_time_s,
            )
            if simulation_time_since_last_action_s > terminal_simulation_time_s:
                raise FrameworkEvidenceError(
                    "time since the last action exceeded terminal MuJoCo simulation time"
                )
        with self._diagnostic_lock:
            motion_diagnostics = deepcopy(self._object_motion_diagnostics)
            limit_diagnostics = deepcopy(self._limit_violation_diagnostics)
            diagnostic_contact_pairs = set(self._diagnostic_contact_pairs_ever)
        contact_pairs_ever = sorted(
            diagnostic_contact_pairs
            | {
                tuple(sorted((str(contact["body1"]), str(contact["body2"]))))
                for snapshot in snapshots
                for contact in snapshot.get("contacts", [])
                if isinstance(contact, Mapping)
                and "body1" in contact
                and "body2" in contact
            }
        )
        terminal_contact_pairs = sorted(
            {
                tuple(sorted((str(contact["body1"]), str(contact["body2"]))))
                for contact in terminal.get("contacts", [])
                if isinstance(contact, Mapping)
                and "body1" in contact
                and "body2" in contact
            }
        )
        object_motion_summaries = {
            object_id: _render_object_motion_summary(summary)
            for object_id, summary in sorted(motion_diagnostics.items())
        }
        action_clipping_details = _action_clipping_details(
            self._mujoco_runtime,
            action_events,
            episode_start_time_s=self._diagnostic_episode_start_time_s,
        )
        joint_or_actuator_limit_violations = [
            value
            for _, value in sorted(limit_diagnostics.items(), key=lambda item: item[0])
        ]
        return {
            "source": "trusted_mujoco_mjdata_and_bridge_trace",
            "resolved_scene_binding": _resolved_scene_binding_projection(
                self._mujoco_runtime.resolved_instance_evidence
            ),
            "trajectory_summary_scope": {
                "target_aware": False,
                "time_origin": "post_reset_settle",
                "significant_motion_threshold_m": _OBJECT_SIGNIFICANT_MOTION_M,
                "maximum_keyframes_per_object": 5,
            },
            "action_count": len(action_events),
            "clipped_action_count": clipped_action_count,
            "terminal_simulation_time_s": terminal_simulation_time_s,
            "simulation_time_since_last_action_s": (
                simulation_time_since_last_action_s
            ),
            "action_clipping_details": action_clipping_details,
            "joint_or_actuator_limit_violations": (
                joint_or_actuator_limit_violations
            ),
            "final_joint_positions": terminal["joint_positions"],
            "final_end_effector_position_m": terminal["end_effector_position_m"],
            "end_effector_path_bounds_m": {"min": path_min, "max": path_max},
            "minimum_end_effector_to_object_center_distance_m": minimum_center_distance,
            "object_motion_summaries": object_motion_summaries,
            "final_object_positions_m": {
                name: state["position_m"] for name, state in terminal["objects"].items()
            },
            "final_object_linear_velocities_m_s": {
                name: state["linear_velocity_m_s"]
                for name, state in terminal["objects"].items()
            },
            "contact_pairs_ever": [list(pair) for pair in contact_pairs_ever],
            "terminal_contact_pairs": [list(pair) for pair in terminal_contact_pairs],
            "last_action": None
            if receipt is None
            else {
                "requested": dict(receipt.requested),
                "accepted": dict(receipt.accepted),
                "clipped": dict(receipt.clipped),
                "simulation_time_s": float(receipt.simulation_time),
            },
        }

    def _record_episode_diagnostics(self, snapshot: Mapping[str, Any]) -> None:
        """Accumulate bounded trajectory and limit evidence at every observed tick."""

        simulation_time_s = float(snapshot.get("simulation_time_s", 0.0))
        episode_time_s = max(
            0.0, simulation_time_s - self._diagnostic_episode_start_time_s
        )
        violations = _joint_or_actuator_limit_violations(
            self._mujoco_runtime,
            margin_rad=0.0,
            episode_time_s=episode_time_s,
            receipt=self._mujoco_runtime.last_receipt,
        )
        with self._diagnostic_lock:
            self._record_episode_diagnostics_locked(
                snapshot, episode_time_s, violations
            )

    def _record_episode_diagnostics_locked(
        self,
        snapshot: Mapping[str, Any],
        episode_time_s: float,
        violations: Sequence[dict[str, Any]],
    ) -> None:
        """Update diagnostic accumulators while ``_diagnostic_lock`` is held."""
        contacts = snapshot.get("contacts", [])
        if isinstance(contacts, Sequence) and not isinstance(contacts, (str, bytes)):
            for contact in contacts:
                if not isinstance(contact, Mapping):
                    continue
                if "body1" in contact and "body2" in contact:
                    self._diagnostic_contact_pairs_ever.add(
                        tuple(
                            sorted(
                                (str(contact["body1"]), str(contact["body2"]))
                            )
                        )
                    )

        objects = snapshot.get("objects", {})
        if isinstance(objects, Mapping):
            for raw_object_id, raw_state in objects.items():
                object_id = str(raw_object_id)
                if not isinstance(raw_state, Mapping):
                    continue
                raw_position = raw_state.get("position_m")
                if (
                    not isinstance(raw_position, Sequence)
                    or isinstance(raw_position, (str, bytes))
                    or len(raw_position) != 3
                    or not all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                        for value in raw_position
                    )
                ):
                    continue
                position = [float(value) for value in raw_position]
                summary = self._object_motion_diagnostics.get(object_id)
                if summary is None:
                    summary = {
                        "initial_position_m": list(position),
                        "last_position_m": list(position),
                        "significant_anchor_position_m": list(position),
                        "sample_count": 0,
                        "last_sample_time_s": episode_time_s,
                        "maximum_displacement_from_initial_m": 0.0,
                        "maximum_displacement_position_m": list(position),
                        "maximum_displacement_time_s": episode_time_s,
                        "maximum_height_m": position[2],
                        "maximum_height_position_m": list(position),
                        "maximum_height_time_s": episode_time_s,
                        "position_min_m": list(position),
                        "position_max_m": list(position),
                        "significant_path_length_m": 0.0,
                        "significant_motion_count": 0,
                        "last_significant_motion_time_s": None,
                        "last_significant_motion_position_m": None,
                        "gripper_contact_ever": False,
                        "first_gripper_contact_time_s": None,
                        "last_gripper_contact_time_s": None,
                        "ever_grasped_by_opposing_jaws": False,
                        "first_opposing_grasp_time_s": None,
                        "last_opposing_grasp_time_s": None,
                    }
                    self._object_motion_diagnostics[object_id] = summary

                summary["sample_count"] += 1
                summary["last_sample_time_s"] = episode_time_s
                initial = summary["initial_position_m"]
                displacement = _distance3(position, initial)
                if displacement > summary["maximum_displacement_from_initial_m"]:
                    summary["maximum_displacement_from_initial_m"] = displacement
                    summary["maximum_displacement_position_m"] = list(position)
                    summary["maximum_displacement_time_s"] = episode_time_s
                if position[2] > summary["maximum_height_m"]:
                    summary["maximum_height_m"] = position[2]
                    summary["maximum_height_position_m"] = list(position)
                    summary["maximum_height_time_s"] = episode_time_s
                for axis in range(3):
                    summary["position_min_m"][axis] = min(
                        summary["position_min_m"][axis], position[axis]
                    )
                    summary["position_max_m"][axis] = max(
                        summary["position_max_m"][axis], position[axis]
                    )
                significant_delta = _distance3(
                    position, summary["significant_anchor_position_m"]
                )
                if significant_delta >= _OBJECT_SIGNIFICANT_MOTION_M:
                    summary["significant_path_length_m"] += significant_delta
                    summary["significant_motion_count"] += 1
                    summary["last_significant_motion_time_s"] = episode_time_s
                    summary["last_significant_motion_position_m"] = list(position)
                    summary["significant_anchor_position_m"] = list(position)
                summary["last_position_m"] = list(position)

                gripper_contact = any(
                    isinstance(contact, Mapping)
                    and _contact_has_object(contact, object_id)
                    and bool(
                        _GRIPPER_CONTACT_BODIES
                        & {str(contact.get("body1")), str(contact.get("body2"))}
                    )
                    for contact in contacts
                )
                if gripper_contact:
                    if summary["first_gripper_contact_time_s"] is None:
                        summary["first_gripper_contact_time_s"] = episode_time_s
                    summary["last_gripper_contact_time_s"] = episode_time_s
                    summary["gripper_contact_ever"] = True
                grasped = _object_grasped(snapshot, object_id)
                if grasped:
                    if summary["first_opposing_grasp_time_s"] is None:
                        summary["first_opposing_grasp_time_s"] = episode_time_s
                    summary["last_opposing_grasp_time_s"] = episode_time_s
                    summary["ever_grasped_by_opposing_jaws"] = True

        for violation in violations:
            key = (
                str(violation["joint_key"]),
                str(violation["kind"]),
                str(violation["side"]),
            )
            event = violation.pop("event")
            existing = self._limit_violation_diagnostics.get(key)
            if existing is None:
                self._limit_violation_diagnostics[key] = {
                    **violation,
                    "first_event": dict(event),
                    "maximum_excess_event": dict(event),
                }
            elif float(event["excess"]) > float(
                existing["maximum_excess_event"]["excess"]
            ):
                existing["maximum_excess_event"] = dict(event)

    def _sample_episode_evidence(
        self, snapshot: Mapping[str, Any] | None = None
    ) -> None:
        if not self._evidence_enabled:
            return
        observed = (
            self._mujoco_runtime.world_snapshot() if snapshot is None else snapshot
        )
        current = _physical_forbidden(
            runtime=self._mujoco_runtime,
            snapshot=observed,
            instance=self._resolved_instance,
            common_reset=self._common_reset,
            conditions=_PHYSICAL_FORBIDDEN_CONDITIONS,
        )
        self._record_episode_diagnostics(observed)
        for name, value in current.items():
            self._episode_ever_seen[name] |= bool(value)

    def _observe_simulation_state(self) -> None:
        try:
            _raise_observer_failure(self._mujoco_runtime)
            self._sample_episode_evidence()
            if self._video is not None:
                self._video.capture(self._mujoco_runtime)
        except FrameworkEvidenceError:
            raise
        except BaseException as exc:
            raise FrameworkEvidenceError(
                "validation evidence sampling failed"
            ) from exc

    def close(self) -> None:
        self._evidence_enabled = False
        failure: FrameworkEvidenceError | None = None
        try:
            _raise_observer_failure(self._mujoco_runtime)
        except BaseException as exc:
            failure = _framework_evidence_exception(
                "validation background evidence observer failed", exc
            )
        try:
            self._mujoco_runtime.disconnect()
        except BaseException as exc:
            failure = failure or _framework_evidence_exception(
                "validation MuJoCo runtime disconnect failed", exc
            )
        if self._video is not None:
            try:
                self._video.capture(self._mujoco_runtime, force=True)
            except BaseException as exc:
                failure = failure or _framework_evidence_exception(
                    "validation final video capture failed", exc
                )
            finally:
                try:
                    self._video.close()
                except BaseException as exc:
                    failure = failure or _framework_evidence_exception(
                        "validation video close failed", exc
                    )
        try:
            _release_facade(self.runtime)
        except BaseException as exc:
            failure = failure or _framework_evidence_exception(
                "validation runtime facade release failed", exc
            )
        finally:
            if failure is not None:
                raise failure


class MujocoTabletopDemoEnvironment:
    """Private six-instance Demo oracle using actual MuJoCo state only.

    It never teleports or attaches an object.  The harness may inspect state,
    contacts, and the private bridge trace, while the Consumer Agent receives
    only ``runtime`` through generated tools.  G2/G3 reachability is not
    inferred from a reset or from this terminal-state scorer.
    """

    def __init__(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        model_path: str | Path = _DEFAULT_MODEL_PATH,
        common_reset: Mapping[str, Any] | str | Path = _DEFAULT_COMMON_RESET,
        trace_path: str | Path | None = None,
        video_path: str | Path | None = None,
        video_fps: float = 20.0,
        video_width: int = 640,
        video_height: int = 480,
        auto_step: bool = True,
        scene_catalog: (
            Mapping[str, Any] | str | Path | None | _SceneCatalogUnset
        ) = _SCENE_CATALOG_UNSET,
        scene_freeze: Mapping[str, Any] | str | Path | None = None,
        require_scene_catalog: bool = False,
        require_explicit_asset_refs: bool = False,
    ) -> None:
        self._common_reset = _read_mapping(common_reset, label="common_reset")
        self._oracle_contract: FrozenTaskOracleContract = (
            contract_from_environment_context(context)
            or load_default_task_oracle_contract()
        )
        self._evidence_enabled = False
        self._episode_ever_seen = {
            name: False for name in _PHYSICAL_FORBIDDEN_CONDITIONS
        }
        private_trace = _framework_trace_path(context, explicit=trace_path)
        private_video = _framework_video_path(context, explicit=video_path)
        self._video = (
            None
            if private_video is None
            else _MujocoEpisodeVideoRecorder(
                private_video,
                common_reset=self._common_reset,
                fps=video_fps,
                width=video_width,
                height=video_height,
            )
        )
        selected_scene_catalog = (
            _DEFAULT_SCENE_CATALOG
            if scene_catalog is _SCENE_CATALOG_UNSET
            else scene_catalog
        )
        self._mujoco_runtime = _ObservedSO101MujocoTabletopRuntime(
            model_path,
            common_reset=self._common_reset,
            auto_step=auto_step,
            trace_path=private_trace,
            scene_catalog=selected_scene_catalog,
            scene_freeze=scene_freeze,
            require_scene_catalog=require_scene_catalog,
            require_explicit_asset_refs=require_explicit_asset_refs,
            # See validation environment: the clock records evidence only;
            # rendering occurs after public calls and harness-owned stepping.
            step_observer=self._sample_episode_evidence,
        )
        self.runtime = _LeRobotControlFacade(
            self._mujoco_runtime, self._observe_simulation_state
        )
        self._mujoco_runtime.connect(calibrate=False)
        self._instance: dict[str, Any] = {}
        self._initial_snapshot: dict[str, Any] = {}
        self._trace_start = 0

    def reset(self, instance: Mapping[str, Any]) -> None:
        task_id = str(instance.get("task_id", ""))
        self._oracle_contract.predicate_for_task(task_id)
        expected_instance_id = _FIXED_DEMO_INSTANCE_IDS.get(task_id)
        if expected_instance_id is None:
            raise ValueError(f"actual MuJoCo Demo does not contain fixed task {task_id!r}")
        if instance.get("instance_id") != expected_instance_id:
            raise ValueError(
                f"task {task_id!r} must use fixed instance {expected_instance_id!r}"
            )
        self._evidence_enabled = False
        self._mujoco_runtime._observer_error = None
        requested_instance = deepcopy(dict(instance))
        self._mujoco_runtime.reset(requested_instance)
        self._instance = _oracle_instance_from_resolved_scene(
            requested_instance,
            self._mujoco_runtime.resolved_instance_evidence,
        )
        if self._video is not None:
            self._video.start(self._mujoco_runtime)
        settle = _nonnegative(
            self._common_reset.get("settle_before_task_s", 0.5),
            label="settle_before_task_s",
        )
        if settle:
            _advance_with_sampling(
                self._mujoco_runtime, settle, self._observe_simulation_state
            )
        self._initial_snapshot = self._mujoco_runtime.world_snapshot()
        self._trace_start = len(self._mujoco_runtime.trace_events)
        self._episode_initial_object_z = {
            name: float(state["position_m"][2])
            for name, state in self._initial_snapshot["objects"].items()
        }
        self._episode_maximum_object_z = dict(self._episode_initial_object_z)
        self._episode_ever_seen = {
            name: False for name in _PHYSICAL_FORBIDDEN_CONDITIONS
        }
        self._evidence_enabled = True
        self._sample_episode_evidence(self._initial_snapshot)

    def prepare_generated_function(self, function: Callable[..., Any]) -> None:
        """Bind a packaged tool invocation to this task's MuJoCo clock."""

        prepare_generated_callable_for_mujoco(function, self.runtime)

    def score(self, task: Mapping[str, Any], instance: Mapping[str, Any]) -> Mapping[str, Any]:
        _raise_observer_failure(self._mujoco_runtime)
        task_id = str(instance.get("task_id", ""))
        if task_id != self._instance.get("task_id"):
            raise ValueError("score instance does not match the reset instance")
        declared_task_id = task.get("task_id")
        if declared_task_id is not None and declared_task_id != task_id:
            raise ValueError("task and private instance IDs do not match")
        predicate = self._oracle_contract.predicate_for_task(task_id)
        settle = predicate.evaluation_window_s
        if settle:
            _advance_with_sampling(
                self._mujoco_runtime, settle, self._observe_simulation_state
            )
        _raise_observer_failure(self._mujoco_runtime)
        snapshot = self._mujoco_runtime.world_snapshot()
        self._sample_episode_evidence(snapshot)
        measurements, predicate_passed = self._task_predicates(
            predicate, snapshot, self._instance
        )
        forbidden = {
            name: self._episode_ever_seen[name]
            for name in (
                "object_outside_table_support_polygon",
                "non_gripper_robot_table_collision",
                "actuator_or_joint_limit_exceeded",
                "simulation_nan_or_instability",
            )
        }
        passed = predicate_passed and not any(bool(value) for value in forbidden.values())
        return {
            "schema_version": "robot_capability.private_demo_oracle_result.v1",
            "passed": passed,
            "task_id": task_id,
            "measurements": measurements,
            "forbidden_evidence": forbidden,
            "runtime_kind": "actual_mujoco",
            "evidence_source": "mujoco.MjData terminal state and private bridge trace",
            # Framework-only provenance.  It is recorded after the Consumer
            # Agent has finished and is never included in its prompt or tools.
            "resolved_scene_binding": _resolved_scene_binding_projection(
                self._mujoco_runtime.resolved_instance_evidence
            ),
            "reachability_claim": "not_evaluated",
            "oracle_contract_sha256": self._oracle_contract.source_sha256,
            "evaluation_window_s": predicate.evaluation_window_s,
        }

    def _task_predicates(
        self,
        predicate: FrozenTaskPredicate,
        snapshot: Mapping[str, Any],
        instance: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        task_id = predicate.task_id
        agent_input = instance["agent_input"]
        if task_id == "soarm101_p0_push_cube_to_region":
            distance = predicate.condition("object_xy_distance_to_target_center_m")
            speed = predicate.condition("object_linear_speed_m_s")
            lift = predicate.condition("maximum_object_lift_above_initial_m")
            object_id = str(distance.fields["object_id"])
            return _score_push(
                snapshot,
                instance,
                object_id,
                _resolved_target_position(agent_input, distance),
                tolerance_m=distance.number("tolerance"),
                speed_tolerance_m_s=speed.number("tolerance"),
                tilt_tolerance_rad=None,
                maximum_lift_above_initial_m=max(
                    0.0,
                    self._episode_maximum_object_z[object_id]
                    - self._episode_initial_object_z[object_id],
                ),
                maximum_lift_tolerance_m=lift.number("tolerance"),
            )
        if task_id == "soarm101_p0_push_cylinder_lateral":
            distance = predicate.condition("object_xy_distance_to_target_center_m")
            speed = predicate.condition("object_linear_speed_m_s")
            tilt = predicate.condition("object_axis_tilt_rad")
            lift = predicate.condition("maximum_object_lift_above_initial_m")
            object_id = str(distance.fields["object_id"])
            return _score_push(
                snapshot,
                instance,
                object_id,
                _resolved_target_position(agent_input, distance),
                tolerance_m=distance.number("tolerance"),
                speed_tolerance_m_s=speed.number("tolerance"),
                tilt_tolerance_rad=tilt.number("tolerance"),
                maximum_lift_above_initial_m=max(
                    0.0,
                    self._episode_maximum_object_z[object_id]
                    - self._episode_initial_object_z[object_id],
                ),
                maximum_lift_tolerance_m=lift.number("tolerance"),
            )
        if task_id == "soarm101_p0_place_cube_in_tray":
            containment = predicate.condition(
                "object_footprint_contained_by_receptacle"
            )
            supported_condition = predicate.condition(
                "object_supported_inside_receptacle"
            )
            grasp_condition = predicate.condition("object_grasped")
            speed_condition = predicate.condition("object_linear_speed_m_s")
            object_id = str(containment.fields["object_id"])
            receptacle_id = str(containment.fields["receptacle_id"])
            if (
                supported_condition.fields["object_id"] != object_id
                or supported_condition.fields["receptacle_id"] != receptacle_id
                or grasp_condition.fields["object_id"] != object_id
                or speed_condition.fields["object_id"] != object_id
            ):
                raise ValueError("tray oracle conditions do not share one object/receptacle")
            margin = _condition_parameter_number(
                containment, "minimum_wall_margin_m"
            )
            speed_tolerance = speed_condition.number("tolerance")
            contained = _inside_tray(
                snapshot,
                instance,
                object_id,
                receptacle_id,
                margin_m=margin,
            )
            supported = contained and _supported_by_table(snapshot, object_id)
            grasped = _object_grasped(snapshot, object_id)
            speed = _object_speed(snapshot, object_id)
            measurements = {
                "object_footprint_contained_by_receptacle": contained,
                "object_supported_inside_receptacle": supported,
                "object_grasped": grasped,
                "object_linear_speed_m_s": speed,
                "minimum_wall_margin_m": margin,
                "speed_tolerance_m_s": speed_tolerance,
            }
            return (
                measurements,
                contained
                and supported
                and not grasped
                and _within(speed, speed_tolerance),
            )
        if task_id == "soarm101_p0_place_cube_in_bowl_new_region":
            containment = predicate.condition("object_contained_by_bowl")
            grasp_condition = predicate.condition("object_grasped")
            speed_condition = predicate.condition("object_linear_speed_m_s")
            object_id = str(containment.fields["object_id"])
            receptacle_id = str(containment.fields["receptacle_id"])
            if (
                grasp_condition.fields["object_id"] != object_id
                or speed_condition.fields["object_id"] != object_id
            ):
                raise ValueError("bowl oracle conditions do not share one object")
            radial_limit = _condition_parameter_number(
                containment, "maximum_radial_center_distance_m"
            )
            height_limit = _condition_parameter_number(
                containment, "maximum_center_height_above_rim_m"
            )
            speed_tolerance = speed_condition.number("tolerance")
            state = snapshot["objects"][object_id]
            bowl = _body_spec(instance, receptacle_id)
            center = snapshot["receptacles"][receptacle_id]["position_m"]
            point = state["position_m"]
            radial = math.hypot(point[0] - center[0], point[1] - center[1])
            height_above_rim = point[2] - (center[2] + float(bowl["rim_height_m"]))
            contained = _within(radial, radial_limit) and _within(
                height_above_rim, height_limit
            )
            grasped = _object_grasped(snapshot, object_id)
            speed = _object_speed(snapshot, object_id)
            measurements = {
                "object_contained_by_bowl": contained,
                "radial_center_distance_m": radial,
                "maximum_radial_center_distance_m": radial_limit,
                "center_height_above_rim_m": height_above_rim,
                "maximum_center_height_above_rim_m": height_limit,
                "object_grasped": grasped,
                "object_linear_speed_m_s": speed,
                "speed_tolerance_m_s": speed_tolerance,
            }
            return measurements, contained and not grasped and _within(
                speed, speed_tolerance
            )
        if task_id == "soarm101_p0_place_two_objects_in_tray":
            order_condition = predicate.condition(
                "successful_containment_event_order"
            )
            containment_condition = predicate.condition(
                "all_object_footprints_contained_by_receptacle"
            )
            grasp_condition = predicate.condition("any_object_grasped")
            speed_condition = predicate.condition("max_object_linear_speed_m_s")
            expected_value = _resolve_agent_input_ref(
                agent_input,
                str(order_condition.fields["expected_object_ids_ref"]),
            )
            if not isinstance(expected_value, Sequence) or isinstance(
                expected_value, (str, bytes)
            ):
                raise ValueError("expected_object_ids_ref must resolve to an array")
            object_ids = list(expected_value)
            if (
                tuple(containment_condition.fields["object_ids"])
                != tuple(object_ids)
                or tuple(grasp_condition.fields["object_ids"]) != tuple(object_ids)
                or tuple(speed_condition.fields["object_ids"]) != tuple(object_ids)
            ):
                raise ValueError("multi-object oracle ordering does not match agent input")
            receptacle_id = str(containment_condition.fields["receptacle_id"])
            if order_condition.fields["receptacle_id"] != receptacle_id:
                raise ValueError("multi-object oracle receptacles do not match")
            containment_margin = _condition_parameter_number(
                containment_condition, "minimum_wall_margin_m"
            )
            event_margin = _condition_parameter_number(
                order_condition, "minimum_wall_margin_m"
            )
            event_parameters = order_condition.fields["parameters"]
            event_window = _finite_contract_number(
                event_parameters["stable_placement_event"]["continuous_window_s"],
                label="stable placement event window",
            )
            speed_tolerance = speed_condition.number("tolerance")
            contained = {
                name: _inside_tray(
                    snapshot,
                    instance,
                    name,
                    receptacle_id,
                    margin_m=containment_margin,
                )
                for name in object_ids
            }
            order = _prove_containment_event_order(
                trace_events=self._mujoco_runtime.trace_events[self._trace_start :],
                initial_snapshot=self._initial_snapshot,
                instance=instance,
                object_ids=object_ids,
                receptacle_id=receptacle_id,
                margin_m=event_margin,
                stable_window_s=event_window,
            )
            grasped = {name: _object_grasped(snapshot, name) for name in object_ids}
            speeds = {name: _object_speed(snapshot, name) for name in object_ids}
            measurements = {
                "contained": contained,
                "expected_event_order": object_ids,
                "observed_event_order": order["observed_event_order"],
                "event_order_proven": order["proven"],
                "event_order_failure_reason": order["failure_reason"],
                "object_grasped": grasped,
                "object_linear_speed_m_s": speeds,
                "containment_margin_m": containment_margin,
                "event_margin_m": event_margin,
                "stable_placement_event_window_s": event_window,
                "speed_tolerance_m_s": speed_tolerance,
            }
            passed = (
                all(contained.values())
                and bool(order["proven"])
                and not any(grasped.values())
                and all(
                    _within(speed, speed_tolerance) for speed in speeds.values()
                )
            )
            return measurements, passed
        if task_id == "soarm101_p0_sort_two_cubes_matching_trays":
            containment = predicate.condition(
                "all_object_footprints_contained_by_assigned_receptacle"
            )
            grasp_condition = predicate.condition("any_object_grasped")
            speed_condition = predicate.condition("max_object_linear_speed_m_s")
            assignment = predicate.condition("object_receptacle_assignment")
            expected_value = _resolve_agent_input_ref(
                agent_input, str(assignment.fields["expected_mapping_ref"])
            )
            if not isinstance(expected_value, Mapping):
                raise ValueError("expected_mapping_ref must resolve to an object")
            if (
                containment.fields["expected_mapping_ref"]
                != assignment.fields["expected_mapping_ref"]
            ):
                raise ValueError("sort oracle mapping references do not match")
            expected = dict(expected_value)
            expected_object_ids = tuple(expected)
            if (
                tuple(grasp_condition.fields["object_ids"]) != expected_object_ids
                or tuple(speed_condition.fields["object_ids"]) != expected_object_ids
            ):
                raise ValueError("sort oracle object ordering does not match agent input")
            margin = _condition_parameter_number(
                containment, "minimum_wall_margin_m"
            )
            speed_tolerance = speed_condition.number("tolerance")
            assignment = {
                name: receptacle
                for name, receptacle in expected.items()
                if _inside_tray(
                    snapshot, instance, name, receptacle, margin_m=margin
                )
            }
            grasped = {name: _object_grasped(snapshot, name) for name in expected}
            speeds = {name: _object_speed(snapshot, name) for name in expected}
            measurements = {
                "expected_assignment": expected,
                "observed_assignment": assignment,
                "all_footprints_contained": assignment == expected,
                "object_grasped": grasped,
                "object_linear_speed_m_s": speeds,
                "minimum_wall_margin_m": margin,
                "speed_tolerance_m_s": speed_tolerance,
            }
            passed = (
                assignment == expected
                and not any(grasped.values())
                and all(
                    _within(speed, speed_tolerance) for speed in speeds.values()
                )
            )
            return measurements, passed
        raise ValueError(f"unsupported fixed Demo task {task_id!r}")

    def _sample_episode_evidence(
        self, snapshot: Mapping[str, Any] | None = None
    ) -> None:
        if not self._evidence_enabled:
            return
        observed = (
            self._mujoco_runtime.world_snapshot() if snapshot is None else snapshot
        )
        objects = observed.get("objects", {})
        if isinstance(objects, Mapping):
            for name, state in objects.items():
                if not isinstance(state, Mapping):
                    continue
                position = state.get("position_m")
                if (
                    isinstance(position, Sequence)
                    and not isinstance(position, (str, bytes))
                    and len(position) == 3
                    and name in self._episode_maximum_object_z
                ):
                    self._episode_maximum_object_z[str(name)] = max(
                        self._episode_maximum_object_z[str(name)],
                        float(position[2]),
                    )
        current = _physical_forbidden(
            runtime=self._mujoco_runtime,
            snapshot=observed,
            instance=self._instance,
            common_reset=self._common_reset,
            conditions=_PHYSICAL_FORBIDDEN_CONDITIONS,
            joint_limit_margin_rad=self._oracle_contract.forbidden_parameter(
                "actuator_or_joint_limit_exceeded", "minimum_limit_margin_rad"
            ),
        )
        for name, value in current.items():
            self._episode_ever_seen[name] |= bool(value)

    def _observe_simulation_state(self) -> None:
        try:
            _raise_observer_failure(self._mujoco_runtime)
            self._sample_episode_evidence()
            if self._video is not None:
                self._video.capture(self._mujoco_runtime)
        except FrameworkEvidenceError:
            raise
        except BaseException as exc:
            raise FrameworkEvidenceError("Demo evidence sampling failed") from exc

    def close(self) -> None:
        self._evidence_enabled = False
        failure: FrameworkEvidenceError | None = None
        try:
            _raise_observer_failure(self._mujoco_runtime)
        except BaseException as exc:
            failure = _framework_evidence_exception(
                "Demo background evidence observer failed", exc
            )
        try:
            self._mujoco_runtime.disconnect()
        except BaseException as exc:
            failure = failure or _framework_evidence_exception(
                "Demo MuJoCo runtime disconnect failed", exc
            )
        if self._video is not None:
            try:
                self._video.capture(self._mujoco_runtime, force=True)
            except BaseException as exc:
                failure = failure or _framework_evidence_exception(
                    "Demo final video capture failed", exc
                )
            finally:
                try:
                    self._video.close()
                except BaseException as exc:
                    failure = failure or _framework_evidence_exception(
                        "Demo video close failed", exc
                    )
        try:
            _release_facade(self.runtime)
        except BaseException as exc:
            failure = failure or _framework_evidence_exception(
                "Demo runtime facade release failed", exc
            )
        finally:
            if failure is not None:
                raise failure


def _read_mapping(
    value: Mapping[str, Any] | str | Path, *, label: str
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    parsed = json.loads(Path(value).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError(f"{label} must contain an object")
    return parsed


def _nonnegative(value: object, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return result


def _advance_with_sampling(
    runtime: SO101MujocoRobot,
    seconds: float,
    observer: Callable[[], None],
) -> None:
    """Deterministically step and sample every physics tick owned by the harness."""

    duration = _nonnegative(seconds, label="advance seconds")
    steps = max(0, int(math.ceil(duration / float(runtime.model.opt.timestep))))
    timestep = float(runtime.model.opt.timestep)
    for _ in range(steps):
        runtime.advance(timestep)
        observer()


def _raise_observer_failure(runtime: _ObservedSO101MujocoTabletopRuntime) -> None:
    error = runtime._observer_error
    if error is not None:
        if isinstance(error, FrameworkEvidenceError):
            raise error
        raise FrameworkEvidenceError(
            "framework MuJoCo evidence/video observer failed"
        ) from error


def _framework_trace_path(
    context: Mapping[str, Any] | None, *, explicit: str | Path | None
) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    metadata = context.get("framework_metadata", {}) if isinstance(context, Mapping) else {}
    if not isinstance(metadata, Mapping):
        raise TypeError("framework_metadata must be an object")
    value = metadata.get("trace_path", metadata.get("simulation_trace_path"))
    return None if value is None else Path(str(value))


def _framework_video_path(
    context: Mapping[str, Any] | None, *, explicit: str | Path | None
) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    metadata = context.get("framework_metadata", {}) if isinstance(context, Mapping) else {}
    if not isinstance(metadata, Mapping):
        raise TypeError("framework_metadata must be an object")
    value = metadata.get("video_path", metadata.get("simulation_video_path"))
    return None if value is None else Path(str(value))


def _validation_scene(
    initial_state: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Normalize suite-authored state without weakening Demo instance data."""

    scene = deepcopy(dict(initial_state))
    # Framework metadata controls the harness and must never become scene data.
    scene.pop("framework_metadata", None)
    raw_qpos = scene.pop("qpos_rad", None)
    legacy_qpos = scene.pop("qpos", None)
    if raw_qpos is not None and legacy_qpos not in (None, {}):
        raise ValueError("initial_state may contain only one of qpos_rad and qpos")
    qpos_value = raw_qpos if raw_qpos is not None else legacy_qpos
    if qpos_value is None:
        qpos_rad: dict[str, float] = {}
    elif not isinstance(qpos_value, Mapping):
        raise TypeError("initial_state.qpos_rad must be an object")
    else:
        qpos_rad = {
            str(name).removesuffix(".pos"): float(value)
            for name, value in qpos_value.items()
        }
        if not all(math.isfinite(value) for value in qpos_rad.values()):
            raise ValueError("initial_state.qpos_rad must contain finite values")
    bodies = scene.setdefault("bodies", [])
    markers = scene.setdefault("markers", [])
    if not isinstance(bodies, list) or not isinstance(markers, list):
        raise TypeError("initial_state bodies and markers must be arrays")
    for index, raw in enumerate(bodies):
        if not isinstance(raw, Mapping):
            raise TypeError(f"initial_state.bodies[{index}] must be an object")
        body = deepcopy(dict(raw))
        if isinstance(body.get("asset_ref"), str):
            # Formal validation resolves every physical fact from the frozen
            # Morphology scene catalog. Injecting legacy defaults here would
            # create a second geometry authority and is rejected by the strict
            # bridge compiler.
            bodies[index] = body
            continue
        kind = str(body.get("kind"))
        defaults = P0_VALIDATION_BODY_DEFAULTS.get(kind, {})
        for name, value in defaults.items():
            body.setdefault(name, deepcopy(value))
        if kind in P0_VALIDATION_BODY_DEFAULTS:
            body.setdefault("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0])
        bodies[index] = body
    return scene, qpos_rad


def _resolved_scene_binding_projection(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose only immutable scene identity, never catalog contents."""

    catalog = evidence.get("scene_catalog")
    return {
        "binding_sha256": evidence.get("binding_sha256"),
        "source_scene_request_sha256": evidence.get(
            "source_scene_request_sha256"
        ),
        "scene_catalog": deepcopy(dict(catalog))
        if isinstance(catalog, Mapping)
        else None,
    }


def _oracle_instance_from_resolved_scene(
    requested: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Join task semantics with catalog-owned physical facts for trusted oracles.

    The task file stays compact and authoritative only for identity/pose.  This
    detached framework view recreates the legacy body shape expected by the
    geometric oracle helpers without copying geometry back into Tasks or
    exposing the privileged evidence through the generated runtime facade.
    """

    result = deepcopy(dict(requested))
    for collection in ("bodies", "markers"):
        records = evidence.get(collection, [])
        if not isinstance(records, list):
            raise TypeError(f"resolved scene evidence {collection} must be an array")
        expanded: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise TypeError(
                    f"resolved scene evidence {collection}[{index}] must be an object"
                )
            geometry = record.get("geometry_profile", {})
            pose = record.get("pose", {})
            if not isinstance(geometry, Mapping) or not isinstance(pose, Mapping):
                raise TypeError("resolved scene evidence geometry/pose is malformed")
            expanded.append(
                {
                    "id": record.get("id"),
                    "asset_ref": record.get("asset_ref"),
                    "kind": record.get("runtime_kind"),
                    **deepcopy(dict(geometry)),
                    **deepcopy(dict(pose)),
                }
            )
        result[collection] = expanded
    result["framework_resolved_scene_binding"] = (
        _resolved_scene_binding_projection(evidence)
    )
    return result


def _body_spec(instance: Mapping[str, Any], identifier: str) -> Mapping[str, Any]:
    for item in instance.get("bodies", []):
        if isinstance(item, Mapping) and item.get("id") == identifier:
            return item
    raise KeyError(f"private instance lacks body {identifier!r}")


def _rotation_from_quaternion(quaternion: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    w, x, y, z = (float(value) for value in quaternion)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 1e-12:
        raise ValueError("MuJoCo object quaternion is zero")
    w, x, y, z = (value / norm for value in (w, x, y, z))
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def _footprint_half_extents(
    object_spec: Mapping[str, Any], object_state: Mapping[str, Any]
) -> tuple[float, float]:
    rotation = _rotation_from_quaternion(object_state["quaternion_wxyz"])
    kind = str(object_spec["kind"])
    if kind == "cube":
        half = [float(value) / 2.0 for value in object_spec["size_m"]]
        return tuple(
            sum(abs(rotation[axis][column]) * half[column] for column in range(3))
            for axis in (0, 1)
        )
    if kind == "cylinder":
        radius = float(object_spec["radius_m"])
        half_height = float(object_spec["height_m"]) / 2.0
        # Projection of an oriented circular cylinder on each world axis.
        return tuple(
            radius * math.sqrt(rotation[axis][0] ** 2 + rotation[axis][1] ** 2)
            + half_height * abs(rotation[axis][2])
            for axis in (0, 1)
        )
    raise ValueError(f"body {object_spec.get('id')!r} is not a dynamic object")


def _inside_tray(
    snapshot: Mapping[str, Any],
    instance: Mapping[str, Any],
    object_id: str,
    receptacle_id: str,
    *,
    margin_m: float,
) -> bool:
    object_state = snapshot["objects"][object_id]
    object_spec = _body_spec(instance, object_id)
    receptacle_spec = _body_spec(instance, receptacle_id)
    if receptacle_spec.get("kind") != "tray":
        raise ValueError(f"{receptacle_id!r} is not a tray")
    center = snapshot["receptacles"][receptacle_id]["position_m"]
    point = object_state["position_m"]
    extent_x, extent_y = _footprint_half_extents(object_spec, object_state)
    inner_x, inner_y = (float(value) for value in receptacle_spec["inner_size_m"])
    return (
        abs(point[0] - center[0]) + extent_x + margin_m <= inner_x / 2.0 + 1e-9
        and abs(point[1] - center[1]) + extent_y + margin_m <= inner_y / 2.0 + 1e-9
    )


def _object_speed(snapshot: Mapping[str, Any], object_id: str) -> float:
    velocity = snapshot["objects"][object_id]["linear_velocity_m_s"]
    return math.sqrt(sum(float(value) ** 2 for value in velocity))


def _cylinder_axis_tilt(snapshot: Mapping[str, Any], object_id: str) -> float:
    rotation = _rotation_from_quaternion(snapshot["objects"][object_id]["quaternion_wxyz"])
    # A cylinder is symmetric under a 180-degree axis reversal.
    cosine = min(1.0, max(-1.0, abs(rotation[2][2])))
    return math.acos(cosine)


def _contact_has_object(contact: Mapping[str, Any], object_id: str) -> bool:
    return object_id in {contact.get("body1"), contact.get("body2")}


def _supported_by_table(snapshot: Mapping[str, Any], object_id: str) -> bool:
    return any(
        _contact_has_object(contact, object_id)
        and "tabletop_table" in {contact.get("geom1"), contact.get("geom2")}
        for contact in snapshot["contacts"]
    )


def _object_grasped(snapshot: Mapping[str, Any], object_id: str) -> bool:
    opposing = set()
    for contact in snapshot["contacts"]:
        if not _contact_has_object(contact, object_id):
            continue
        other = contact["body2"] if contact["body1"] == object_id else contact["body1"]
        if other == "moving_jaw_so101_v1":
            opposing.add("moving")
        elif other in {"gripper", "camera_mount"}:
            opposing.add("fixed")
    return opposing == {"fixed", "moving"}


def _within(value: float, limit: float) -> bool:
    return float(value) <= float(limit) + 1e-9


def _finite_contract_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return result


def _condition_parameter_number(
    condition: FrozenOracleCondition, name: str
) -> float:
    return _finite_contract_number(
        condition.parameter(name), label=f"{condition.measure}.{name}"
    )


def _resolve_agent_input_ref(agent_input: Mapping[str, Any], reference: str) -> Any:
    parts = reference.split(".")
    if not parts or parts[0] != "agent_input" or any(not part for part in parts[1:]):
        raise ValueError("oracle reference must start with agent_input")
    value: Any = agent_input
    for part in parts[1:]:
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"oracle reference {reference!r} does not resolve")
        value = value[part]
    return value


def _resolved_target_position(
    agent_input: Mapping[str, Any], condition: FrozenOracleCondition
) -> Sequence[float]:
    value = _resolve_agent_input_ref(
        agent_input, str(condition.fields["target_ref"])
    )
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        raise ValueError("oracle target_ref must resolve to a position array")
    return value


def _fixture_inside_tray(
    snapshot: Mapping[str, Any],
    resolved_instance: Mapping[str, Any],
    object_id: str,
    receptacle_id: str,
    *,
    margin_m: float,
) -> bool:
    object_spec = _body_spec(resolved_instance, object_id)
    receptacle_spec = _body_spec(resolved_instance, receptacle_id)
    if receptacle_spec.get("kind") != "tray":
        raise ValueError(f"{receptacle_id!r} is not a tray")
    point = snapshot["object_positions_m"][object_id]
    center = snapshot["receptacles"][receptacle_id]["position_m"]
    if object_spec.get("kind") == "cube":
        extent_x, extent_y = (
            float(object_spec["size_m"][0]) / 2.0,
            float(object_spec["size_m"][1]) / 2.0,
        )
    elif object_spec.get("kind") == "cylinder":
        extent_x = extent_y = float(object_spec["radius_m"])
    else:
        raise ValueError(f"{object_id!r} has unsupported fixture geometry")
    inner_x, inner_y = (float(value) for value in receptacle_spec["inner_size_m"])
    return (
        abs(float(point[0]) - float(center[0])) + extent_x + margin_m
        <= inner_x / 2.0 + 1e-9
        and abs(float(point[1]) - float(center[1])) + extent_y + margin_m
        <= inner_y / 2.0 + 1e-9
    )


def _fixture_inside_bowl(
    snapshot: Mapping[str, Any],
    resolved_instance: Mapping[str, Any],
    object_id: str,
    receptacle_id: str,
    *,
    maximum_radial_center_distance_m: float,
    maximum_center_height_above_rim_m: float,
) -> bool:
    bowl = _body_spec(resolved_instance, receptacle_id)
    if bowl.get("kind") != "bowl":
        raise ValueError(f"{receptacle_id!r} is not a bowl")
    point = snapshot["object_positions_m"][object_id]
    center = snapshot["receptacles"][receptacle_id]["position_m"]
    radial = math.hypot(
        float(point[0]) - float(center[0]),
        float(point[1]) - float(center[1]),
    )
    height_above_rim = float(point[2]) - (
        float(center[2]) + float(bowl["rim_height_m"])
    )
    return _within(radial, maximum_radial_center_distance_m) and _within(
        height_above_rim, maximum_center_height_above_rim_m
    )


def _score_push(
    snapshot: Mapping[str, Any],
    instance: Mapping[str, Any],
    object_id: str,
    target_center: Sequence[float],
    *,
    tolerance_m: float,
    speed_tolerance_m_s: float,
    tilt_tolerance_rad: float | None,
    maximum_lift_above_initial_m: float,
    maximum_lift_tolerance_m: float,
) -> tuple[dict[str, Any], bool]:
    point = snapshot["objects"][object_id]["position_m"]
    distance = math.hypot(
        point[0] - target_center[0], point[1] - target_center[1]
    )
    supported = _supported_by_table(snapshot, object_id)
    speed = _object_speed(snapshot, object_id)
    tilt = (
        _cylinder_axis_tilt(snapshot, object_id)
        if tilt_tolerance_rad is not None
        else None
    )
    measurements = {
        "object_id": object_id,
        "object_xy_distance_to_target_center_m": distance,
        "target_tolerance_m": tolerance_m,
        "object_supported_by_table": supported,
        "object_linear_speed_m_s": speed,
        "speed_tolerance_m_s": speed_tolerance_m_s,
        "maximum_object_lift_above_initial_m": maximum_lift_above_initial_m,
        "maximum_lift_tolerance_m": maximum_lift_tolerance_m,
    }
    if tilt is not None:
        measurements["object_axis_tilt_rad"] = tilt
        measurements["axis_tilt_tolerance_rad"] = tilt_tolerance_rad
    passed = (
        _within(distance, tolerance_m)
        and supported
        and _within(speed, speed_tolerance_m_s)
        and _within(maximum_lift_above_initial_m, maximum_lift_tolerance_m)
        and (
            tilt is None
            or tilt_tolerance_rad is not None
            and _within(tilt, tilt_tolerance_rad)
        )
    )
    return measurements, passed


def _prove_containment_event_order(
    *,
    trace_events: Sequence[Mapping[str, Any]],
    initial_snapshot: Mapping[str, Any],
    instance: Mapping[str, Any],
    object_ids: Sequence[str],
    receptacle_id: str,
    margin_m: float,
    stable_window_s: float,
) -> dict[str, Any]:
    initially_contained = {
        name: _inside_tray(
            initial_snapshot,
            instance,
            name,
            receptacle_id,
            margin_m=margin_m,
        )
        for name in object_ids
    }
    if any(initially_contained.values()):
        return {
            "proven": False,
            "observed_event_order": [],
            "failure_reason": "an object was already contained at the scored episode baseline",
        }

    # A placement-success event is deliberately stronger than a 2-D footprint
    # crossing.  In particular, carrying an object above a receptacle can make
    # its projected footprint look contained even though it is still grasped
    # and unsupported.  Latch the first continuously stable placement instead:
    # contained, table-supported, and released for the complete dwell window.
    stable_since: dict[str, float | None] = {name: None for name in object_ids}
    latched: set[str] = set()
    observed: list[str] = []
    for event in trace_events:
        if event.get("event") != "world_state" or not isinstance(
            event.get("snapshot"), Mapping
        ):
            continue
        event_snapshot = event["snapshot"]
        simulation_time = event_snapshot.get("simulation_time_s")
        if (
            not isinstance(simulation_time, (int, float))
            or isinstance(simulation_time, bool)
            or not math.isfinite(float(simulation_time))
        ):
            return {
                "proven": False,
                "observed_event_order": observed,
                "failure_reason": "private simulation trace lacks finite simulation time",
            }
        now = float(simulation_time)
        newly_stable: list[str] = []
        for name in object_ids:
            if name in latched:
                continue
            is_stable_placement = (
                _inside_tray(
                    event_snapshot,
                    instance,
                    name,
                    receptacle_id,
                    margin_m=margin_m,
                )
                and _supported_by_table(event_snapshot, name)
                and not _object_grasped(event_snapshot, name)
            )
            if not is_stable_placement:
                stable_since[name] = None
                continue
            if stable_since[name] is None:
                stable_since[name] = now
            if now - stable_since[name] + 1e-12 >= stable_window_s:
                newly_stable.append(name)

        if len(newly_stable) > 1:
            return {
                "proven": False,
                "observed_event_order": observed,
                "failure_reason": (
                    "multiple stable placement events occurred between two trace samples"
                ),
            }
        if newly_stable:
            name = newly_stable[0]
            latched.add(name)
            observed.append(name)

    if observed != list(object_ids):
        return {
            "proven": False,
            "observed_event_order": observed,
            "failure_reason": "private simulation trace does not prove the required event order",
        }
    return {"proven": True, "observed_event_order": observed, "failure_reason": None}


def _distance3(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        sum((float(left[axis]) - float(right[axis])) ** 2 for axis in range(3))
    )


def _render_object_motion_summary(
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Render one fixed-size, target-agnostic trajectory summary."""

    initial = list(summary["initial_position_m"])
    final = list(summary["last_position_m"])
    maximum_displacement = list(summary["maximum_displacement_position_m"])
    maximum_height = list(summary["maximum_height_position_m"])
    keyframes = [
        {
            "kind": "initial",
            "episode_time_s": 0.0,
            "position_m": initial,
        },
        {
            "kind": "maximum_displacement_from_initial",
            "episode_time_s": float(summary["maximum_displacement_time_s"]),
            "position_m": maximum_displacement,
        },
        {
            "kind": "maximum_height",
            "episode_time_s": float(summary["maximum_height_time_s"]),
            "position_m": maximum_height,
        },
    ]
    if summary["last_significant_motion_time_s"] is not None:
        keyframes.append(
            {
                "kind": "last_significant_motion",
                "episode_time_s": float(summary["last_significant_motion_time_s"]),
                "position_m": list(summary["last_significant_motion_position_m"]),
            }
        )
    keyframes.append(
        {
            "kind": "final",
            "episode_time_s": float(summary["last_sample_time_s"]),
            "position_m": final,
        }
    )
    return {
        "trajectory_sample_count": int(summary["sample_count"]),
        "initial_position_m": initial,
        "final_position_m": final,
        "maximum_displacement_from_initial_m": float(
            summary["maximum_displacement_from_initial_m"]
        ),
        "maximum_height_m": float(summary["maximum_height_m"]),
        "maximum_lift_above_initial_m": max(
            0.0, float(summary["maximum_height_m"]) - float(initial[2])
        ),
        "position_bounds_m": {
            "min": list(summary["position_min_m"]),
            "max": list(summary["position_max_m"]),
        },
        "significant_path_length_m": float(summary["significant_path_length_m"]),
        "significant_motion_count": int(summary["significant_motion_count"]),
        "last_significant_motion_time_s": summary[
            "last_significant_motion_time_s"
        ],
        "gripper_contact_ever": bool(summary["gripper_contact_ever"]),
        "first_gripper_contact_time_s": summary["first_gripper_contact_time_s"],
        "last_gripper_contact_time_s": summary["last_gripper_contact_time_s"],
        "ever_grasped_by_opposing_jaws": bool(
            summary["ever_grasped_by_opposing_jaws"]
        ),
        "first_opposing_grasp_time_s": summary["first_opposing_grasp_time_s"],
        "last_opposing_grasp_time_s": summary["last_opposing_grasp_time_s"],
        "keyframes": keyframes,
    }


def _public_position_units(runtime: SO101MujocoRobot, name: str) -> str:
    if name == "gripper":
        return "normalized_0_100"
    return "degrees" if runtime.use_degrees else "normalized_minus100_to_100"


def _public_model_range(
    runtime: SO101MujocoRobot, name: str, low: float, high: float
) -> dict[str, Any]:
    public = sorted(
        (float(runtime._from_model(name, low)), float(runtime._from_model(name, high)))
    )
    return {
        "low": public[0],
        "high": public[1],
        "units": _public_position_units(runtime, name),
    }


def _action_clipping_details(
    runtime: SO101MujocoRobot,
    action_events: Sequence[Mapping[str, Any]],
    *,
    episode_start_time_s: float,
) -> list[dict[str, Any]]:
    """Aggregate an unbounded action trace into at most one record per joint."""

    summaries: dict[str, dict[str, Any]] = {}
    for action in action_events:
        clipped = action.get("clipped")
        requested = action.get("requested")
        accepted = action.get("accepted")
        if not all(isinstance(value, Mapping) for value in (clipped, requested, accepted)):
            continue
        simulation_time_s = float(action.get("simulation_time", 0.0))
        episode_time_s = max(0.0, simulation_time_s - episode_start_time_s)
        for raw_key, raw_clipped in clipped.items():
            if not bool(raw_clipped):
                continue
            joint_key = str(raw_key)
            name = joint_key.removesuffix(".pos")
            if name not in runtime._actuator_ids:
                continue
            requested_value = float(requested[joint_key])
            accepted_value = float(accepted[joint_key])
            actuator_id = runtime._actuator_ids[name]
            low_model, high_model = (
                float(value) for value in runtime.model.actuator_ctrlrange[actuator_id]
            )
            allowed_range = _public_model_range(
                runtime, name, low_model, high_model
            )
            outside_by = max(
                float(allowed_range["low"]) - requested_value,
                requested_value - float(allowed_range["high"]),
                0.0,
            )
            event = {
                "episode_time_s": episode_time_s,
                "requested_value": requested_value,
                "accepted_value": accepted_value,
                "requested_to_accepted_delta": abs(
                    requested_value - accepted_value
                ),
                "requested_outside_actuator_range_by": outside_by,
            }
            existing = summaries.get(joint_key)
            if existing is None:
                summaries[joint_key] = {
                    "joint_key": joint_key,
                    "allowed_actuator_target_range": allowed_range,
                    "first_event": dict(event),
                    "maximum_clipping_event": dict(event),
                    "event_count": 1,
                }
                continue
            existing["event_count"] += 1
            if event["requested_to_accepted_delta"] > existing[
                "maximum_clipping_event"
            ]["requested_to_accepted_delta"]:
                existing["maximum_clipping_event"] = dict(event)
    return [summaries[key] for key in sorted(summaries)]


def _joint_or_actuator_limit_violations(
    runtime: SO101MujocoRobot,
    *,
    margin_rad: float,
    episode_time_s: float,
    receipt: Any,
) -> list[dict[str, Any]]:
    """Describe current joint/actuator violations in public control units."""

    violations: list[dict[str, Any]] = []
    with runtime._lock:
        for name, joint_id in runtime._joint_ids.items():
            joint_key = f"{name}.pos"
            requested_value = (
                None
                if receipt is None or joint_key not in receipt.requested
                else float(receipt.requested[joint_key])
            )
            accepted_value = (
                None
                if receipt is None or joint_key not in receipt.accepted
                else float(receipt.accepted[joint_key])
            )
            if bool(runtime.model.jnt_limited[joint_id]):
                address = int(runtime.model.jnt_qposadr[joint_id])
                observed_model = float(runtime.data.qpos[address])
                low_model, high_model = (
                    float(value) for value in runtime.model.jnt_range[joint_id]
                )
                effective_low = low_model + margin_rad
                effective_high = high_model - margin_rad
                side = (
                    "low"
                    if observed_model < effective_low - 1e-9
                    else "high"
                    if observed_model > effective_high + 1e-9
                    else None
                )
                if side is not None:
                    observed_value = float(runtime._from_model(name, observed_model))
                    limit = _public_model_range(
                        runtime, name, effective_low, effective_high
                    )
                    boundary = float(limit[side])
                    violations.append(
                        {
                            "joint_key": joint_key,
                            "kind": "joint_position",
                            "side": side,
                            "limit_source": "mujoco.model.jnt_range",
                            "limit": limit,
                            "margin_model_rad": float(margin_rad),
                            "event": {
                                "episode_time_s": float(episode_time_s),
                                "requested_value": requested_value,
                                "accepted_value": accepted_value,
                                "observed_value": observed_value,
                                "excess": abs(observed_value - boundary),
                                "observed_model_value": observed_model,
                                "model_excess": abs(
                                    observed_model
                                    - (effective_low if side == "low" else effective_high)
                                ),
                            },
                        }
                    )

            actuator_id = runtime._actuator_ids[name]
            observed_model = float(runtime.data.ctrl[actuator_id])
            low_model, high_model = (
                float(value) for value in runtime.model.actuator_ctrlrange[actuator_id]
            )
            side = (
                "low"
                if observed_model < low_model - 1e-9
                else "high"
                if observed_model > high_model + 1e-9
                else None
            )
            if side is not None:
                observed_value = float(runtime._from_model(name, observed_model))
                limit = _public_model_range(runtime, name, low_model, high_model)
                boundary = float(limit[side])
                violations.append(
                    {
                        "joint_key": joint_key,
                        "kind": "actuator_target",
                        "side": side,
                        "limit_source": "mujoco.model.actuator_ctrlrange",
                        "limit": limit,
                        "margin_model_rad": 0.0,
                        "event": {
                            "episode_time_s": float(episode_time_s),
                            "requested_value": requested_value,
                            "accepted_value": accepted_value,
                            "observed_value": observed_value,
                            "excess": abs(observed_value - boundary),
                            "observed_model_value": observed_model,
                            "model_excess": abs(
                                observed_model
                                - (low_model if side == "low" else high_model)
                            ),
                        },
                    }
                )
    return violations


def _joint_limit_evidence(
    runtime: SO101MujocoRobot, *, margin_rad: float
) -> bool:
    return bool(
        _joint_or_actuator_limit_violations(
            runtime,
            margin_rad=margin_rad,
            episode_time_s=0.0,
            receipt=runtime.last_receipt,
        )
    )


def _object_outside_table(
    snapshot: Mapping[str, Any],
    instance: Mapping[str, Any],
    common_reset: Mapping[str, Any],
) -> bool:
    table = common_reset.get("table", {})
    center = [float(value) for value in table.get("center_m", [0.35, 0.0, 0.01])]
    half = [float(value) for value in table.get("half_size_m", [0.35, 0.25, 0.01])]
    for name, state in snapshot["objects"].items():
        extent_x, extent_y = _footprint_half_extents(_body_spec(instance, name), state)
        point = state["position_m"]
        if (
            abs(point[0] - center[0]) + extent_x > half[0] + 1e-9
            or abs(point[1] - center[1]) + extent_y > half[1] + 1e-9
            or point[2] < center[2] - half[2] - 0.05
        ):
            return True
    return False


def _non_gripper_table_collision(snapshot: Mapping[str, Any]) -> bool:
    allowed = {"gripper", "moving_jaw_so101_v1", "camera_mount"}
    dynamic = set(snapshot["objects"])
    receptacles = set(snapshot["receptacles"])
    for contact in snapshot["contacts"]:
        geoms = {contact.get("geom1"), contact.get("geom2")}
        if "tabletop_table" not in geoms:
            continue
        bodies = {contact.get("body1"), contact.get("body2")}
        other = next((body for body in bodies if body != "world"), None)
        if other is not None and other not in allowed | dynamic | receptacles:
            return True
    return False


def _physical_forbidden(
    *,
    runtime: SO101MujocoTabletopRuntime,
    snapshot: Mapping[str, Any],
    instance: Mapping[str, Any],
    common_reset: Mapping[str, Any],
    conditions: Sequence[str],
    joint_limit_margin_rad: float = 0.0,
) -> dict[str, Any]:
    receipt = runtime.last_receipt
    available = {
        "action_clipped": bool(receipt and any(receipt.clipped.values())),
        "object_outside_workspace": _object_outside_table(snapshot, instance, common_reset),
        "object_outside_table_support_polygon": _object_outside_table(
            snapshot, instance, common_reset
        ),
        "non_gripper_robot_table_collision": _non_gripper_table_collision(snapshot),
        "actuator_or_joint_limit_exceeded": _joint_limit_evidence(
            runtime, margin_rad=joint_limit_margin_rad
        ),
        "simulation_nan_or_instability": not bool(snapshot["finite_state"]),
        "infrastructure_error": False,
    }
    evidence: dict[str, Any] = {}
    for condition in conditions:
        if condition not in available:
            raise KeyError(f"physical oracle does not implement forbidden condition {condition!r}")
        evidence[condition] = available[condition]
    return evidence


def _select_measurements(available: Mapping[str, Any], requests: Mapping[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for name, requested_shape in requests.items():
        if name not in available:
            raise KeyError(f"oracle measurement is not available: {name}")
        value = available[name]
        if isinstance(requested_shape, Mapping) and isinstance(value, Mapping):
            selected[name] = {
                key: value[key]
                for key in requested_shape
                if key in value
            }
        else:
            selected[name] = value
    return selected
