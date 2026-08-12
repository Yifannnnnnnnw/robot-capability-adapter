"""Minimal library-driven direct MuJoCo experimental session.

This module is intentionally not an SDK adapter.  It opens the fixed MJCF
entrypoint named by a morphology Library record and exposes only direct
actuator controls plus public physics observations.  Candidate code sees a
``DirectMuJoCoFacade``; the MuJoCo model and data remain private to the
session.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any

from ...demo import ValidationEvidence
from ...evaluation import FrozenVideoProfile, RGBFrame
from ..session_support import MuJoCoFrameCapture
from ...validation import HarnessInvocation, MeasurementSample
from .config import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoLibraryConfig,
    DirectMuJoCoTaskConfig,
    load_direct_mujoco_task_config,
    load_morphology_record,
)


class DirectMuJoCoSessionError(RuntimeError):
    """The experimental direct MuJoCo session could not execute."""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise DirectMuJoCoSessionError(f"{label} must be a finite number")
    return float(value)


def _vector(values: Any, label: str) -> list[float]:
    try:
        result = [_finite(value, f"{label}[{index}]") for index, value in enumerate(values)]
    except TypeError as exc:
        raise DirectMuJoCoSessionError(f"{label} must be a numeric vector") from exc
    return result


def _copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_value(item) for item in value]
    return value


_MISSING = object()


def _path_tokens(path: Any, label: str) -> tuple[str | int, ...]:
    if isinstance(path, (list, tuple)) and not isinstance(path, (str, bytes, bytearray)):
        tokens: list[str | int] = []
        for index, item in enumerate(path):
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise DirectMuJoCoSessionError(f"{label}[{index}] must be text or an integer")
            if isinstance(item, str) and not item.strip():
                raise DirectMuJoCoSessionError(f"{label}[{index}] must be non-empty text")
            tokens.append(item.strip() if isinstance(item, str) else item)
        return tuple(tokens)
    if not isinstance(path, str) or not path.strip():
        raise DirectMuJoCoSessionError(f"{label} must be a non-empty observation path")
    tokens = []
    for component in path.strip().removeprefix("$.").split("."):
        if not component:
            raise DirectMuJoCoSessionError(f"{label} contains an empty path component")
        remainder = component
        while remainder:
            if "[" not in remainder:
                tokens.append(remainder)
                break
            prefix, remainder = remainder.split("[", 1)
            if prefix:
                tokens.append(prefix)
            if "]" not in remainder:
                raise DirectMuJoCoSessionError(f"{label} contains an unterminated index")
            index, remainder = remainder.split("]", 1)
            if not index.isdigit():
                raise DirectMuJoCoSessionError(f"{label} contains a non-integer index")
            tokens.append(int(index))
    return tuple(tokens)


def _path_value(root: Any, path: Any, label: str) -> Any:
    current = root
    for token in _path_tokens(path, label):
        if isinstance(token, int):
            if isinstance(current, (str, bytes, bytearray)) or not isinstance(current, Sequence):
                return _MISSING
            if token >= len(current):
                return _MISSING
            current = current[token]
        elif isinstance(current, Mapping):
            if token not in current:
                return _MISSING
            current = current[token]
        else:
            return _MISSING
    return current


def _find_named_value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        for nested in value.values():
            found = _find_named_value(nested, name)
            if found is not _MISSING:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            found = _find_named_value(nested, name)
            if found is not _MISSING:
                return found
    return _MISSING


def _finite_scalar(value: Any, label: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    raise DirectMuJoCoSessionError(f"{label} must resolve to a finite scalar")


def _finite_vector(value: Any, label: str) -> list[float]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise DirectMuJoCoSessionError(f"{label} must resolve to a numeric vector")
    return [_finite_scalar(item, f"{label}[{index}]") for index, item in enumerate(value)]


class DirectMuJoCoFacade:
    """Candidate-facing direct physics facade; it is not an official SDK."""

    __slots__ = ("_session",)

    def __init__(self, session: "DirectMuJoCoEvaluationRobotSession") -> None:
        object.__setattr__(self, "_session", session)

    def __getattribute__(self, name: str) -> Any:
        # Keep the owner, model, and data out of the candidate-facing surface.
        # Public methods use object.__getattribute__ directly for their owner.
        if name.startswith("_"):
            raise AttributeError(f"DirectMuJoCoFacade has no public attribute {name!r}")
        return object.__getattribute__(self, name)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("DirectMuJoCoFacade is read-only")

    @property
    def mode(self) -> str:
        return "DIRECT_MUJOCO_EXPERIMENTAL"

    @property
    def actuator_names(self) -> tuple[str, ...]:
        owner = object.__getattribute__(self, "_session")
        return owner.actuator_names

    @property
    def joint_names(self) -> tuple[str, ...]:
        owner = object.__getattribute__(self, "_session")
        return owner.joint_names

    def send_action(self, action: Mapping[str, Real]) -> dict[str, Any]:
        """Set named actuator controls without writing MuJoCo state directly."""

        owner = object.__getattribute__(self, "_session")
        return owner._send_action(action)

    def set_action(self, action: Mapping[str, Real]) -> dict[str, Any]:
        """Alias used by small Stage 2 implementation projections."""

        return self.send_action(action)

    def state(self) -> dict[str, Any]:
        owner = object.__getattribute__(self, "_session")
        return owner._candidate_observation()

    def get_observation(self) -> dict[str, Any]:
        return self.state()

    def step(self, seconds: float | None = None, *, steps: int | None = None) -> dict[str, Any]:
        """Advance physics and return the resulting public observation."""

        owner = object.__getattribute__(self, "_session")
        observation = owner._step(seconds, steps=steps)
        owner._candidate_observation_reads += 1
        return observation


class DirectMuJoCoEvaluationRobotSession:
    """Evaluation-session-shaped owner for one direct MuJoCo experiment.

    The lifecycle and ``sdk``/``invoke`` methods are compatible with the
    narrow evaluation-session boundary.  Evidence remains explicitly
    experimental and never asserts an SDK-grounded route.
    """

    evidence_scope = "DIRECT_MUJOCO_EXPERIMENTAL"

    def __init__(
        self,
        morphology_record: Mapping[str, Any] | str | Path | DirectMuJoCoLibraryConfig,
        *,
        asset_root: str | Path | None = None,
        video_profile: FrozenVideoProfile | None = None,
        mujoco_module: Any | None = None,
        renderer_factory: Callable[[Any, int, int], Any] | None = None,
        task_config: Mapping[str, Any] | str | Path | DirectMuJoCoTaskConfig | None = None,
        task_id: str | None = None,
    ) -> None:
        if isinstance(morphology_record, DirectMuJoCoLibraryConfig):
            config = morphology_record
        else:
            record, record_path = load_morphology_record(morphology_record)
            resolved_asset_root = asset_root if asset_root is not None else (
                record_path.parent if record_path is not None else None
            )
            if resolved_asset_root is None:
                raise DirectMuJoCoConfigurationError(
                    "asset_root is required when morphology_record is an in-memory record"
                )
            config = DirectMuJoCoLibraryConfig.from_record(record, asset_root=resolved_asset_root)
        if task_config is None:
            selected_task = config.bound_task
        elif isinstance(task_config, DirectMuJoCoTaskConfig):
            selected_task = task_config
        else:
            selected_task = load_direct_mujoco_task_config(task_config, task_id=task_id)
        if selected_task is not None and config.bound_task != selected_task:
            config = config.bind_task(selected_task)
        self.task_config = selected_task
        self.config = config
        self._mj = mujoco_module if mujoco_module is not None else self._import_mujoco()
        required_version = config.mujoco_version
        actual_version = getattr(self._mj, "__version__", None)
        if required_version is not None and actual_version is not None and actual_version != required_version:
            raise DirectMuJoCoSessionError(
                f"morphology.mujoco.version requires {required_version}, found {actual_version}"
            )
        try:
            self._model_path = config.model_path
            self._model = self._mj.MjModel.from_xml_path(str(self._model_path))
            self._data = self._mj.MjData(self._model)
        except DirectMuJoCoConfigurationError:
            raise
        except Exception as exc:
            raise DirectMuJoCoSessionError(
                f"could not load fixed MJCF asset closure from {config.model_path}"
            ) from exc
        self._renderer_factory = renderer_factory
        self._renderer: Any | None = None
        self._capture: MuJoCoFrameCapture | None = None
        self._closed = False
        self._accepted_action_count = 0
        self._physics_step_count = 0
        self._candidate_invocation_count = 0
        self._candidate_observation_reads = 0
        self._truth_trace: list[tuple[float, dict[str, Any]]] = []
        self._last_invocation: dict[str, Any] | None = None
        self._joint_ids: dict[str, int] = {}
        self._actuator_ids: dict[str, int] = {}
        self._body_ids: dict[str, int] = {}
        self._site_ids: dict[str, int] = {}
        self._sensor_ids: dict[str, int] = {}
        self._camera_id = self._resolve_ids()
        self._validate_reset()
        self._facade = DirectMuJoCoFacade(self)
        self.reset()
        self._video_profile = video_profile or FrozenVideoProfile(
            profile_id=f"{config.record_id}-direct-mujoco-experimental",
            profile_version=config.record_version,
            camera=config.render_camera,
            view="robot-and-physics",
            fps=config.render_fps,
            width=config.render_width,
            height=config.render_height,
            container="raw",
            codec="rgb",
        )
        if self._video_profile.camera != config.render_camera:
            raise DirectMuJoCoSessionError(
                "video_profile.camera must match morphology.mujoco.render.camera"
            )
        if (self._video_profile.width, self._video_profile.height) != (
            config.render_width,
            config.render_height,
        ):
            raise DirectMuJoCoSessionError(
                "video_profile resolution must match morphology.mujoco.render width and height"
            )

    @staticmethod
    def _import_mujoco() -> Any:
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - depends on integration runtime
            raise DirectMuJoCoSessionError("the MuJoCo Python package is unavailable") from exc
        return mujoco

    @property
    def sdk(self) -> DirectMuJoCoFacade:
        """Return the explicitly non-SDK candidate facade."""

        return self._facade

    @property
    def robot_model_id(self) -> str:
        return self.config.robot_model_id

    @property
    def robot_configuration_id(self) -> str:
        return self.config.robot_configuration_id

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def development_model_path(self) -> Path:
        return self._model_path

    @property
    def composed_model_path(self) -> Path:
        return self._model_path

    @property
    def joint_names(self) -> tuple[str, ...]:
        return self.config.joint_names

    @property
    def actuator_names(self) -> tuple[str, ...]:
        return self.config.actuator_names

    @property
    def simulation_time_s(self) -> float:
        self._require_open()
        return float(self._data.time)

    @property
    def accepted_action_count(self) -> int:
        return self._accepted_action_count

    @property
    def physics_step_count(self) -> int:
        return self._physics_step_count

    @property
    def video_profile(self) -> FrozenVideoProfile:
        return self._video_profile

    def _require_open(self) -> None:
        if self._closed:
            raise DirectMuJoCoSessionError("direct MuJoCo session is closed")

    def _id(self, kind: Any, name: str, label: str) -> int:
        identifier = int(self._mj.mj_name2id(self._model, kind, name))
        if identifier < 0:
            raise DirectMuJoCoSessionError(f"{label} {name!r} is missing from the MJCF model")
        return identifier

    def _resolve_ids(self) -> int:
        obj = self._mj.mjtObj
        for name in self.config.joint_names:
            self._joint_ids[name] = self._id(obj.mjOBJ_JOINT, name, "morphology.joint_names entry")
        for name in self.config.actuator_names:
            self._actuator_ids[name] = self._id(
                obj.mjOBJ_ACTUATOR,
                name,
                "morphology.actuator_names entry",
            )
        for name in self.config.body_names:
            self._body_ids[name] = self._id(obj.mjOBJ_BODY, name, "morphology.mujoco.frames.body_names entry")
        for name in self.config.site_names:
            self._site_ids[name] = self._id(obj.mjOBJ_SITE, name, "morphology.mujoco.frames.site_names entry")
        for name in self.config.sensor_names:
            self._sensor_ids[name] = self._id(obj.mjOBJ_SENSOR, name, "morphology.mujoco.sensor_names entry")
        if self.config.render_camera == "free":
            return -1
        return self._id(obj.mjOBJ_CAMERA, self.config.render_camera, "morphology.mujoco.render.camera")

    def _joint_dimensions(self, joint_id: int) -> tuple[int, int]:
        joint_type = int(self._model.jnt_type[joint_id])
        joint_enum = self._mj.mjtJoint
        if joint_type == int(joint_enum.mjJNT_FREE):
            return 7, 6
        if joint_type == int(joint_enum.mjJNT_BALL):
            return 4, 3
        return 1, 1

    def _validate_reset(self) -> None:
        if self.config.reset_policy == "model_default":
            return
        if self.config.reset_keyframe is not None:
            keyframe_id = self._id(
                self._mj.mjtObj.mjOBJ_KEY,
                self.config.reset_keyframe,
                "morphology.mujoco.reset.keyframe",
            )
            if keyframe_id >= int(self._model.nkey):
                raise DirectMuJoCoSessionError(
                    f"morphology.mujoco.reset.keyframe {self.config.reset_keyframe!r} is invalid"
                )
            return
        assert self.config.reset_qpos is not None
        if len(self.config.reset_qpos) != int(self._model.nq):
            raise DirectMuJoCoSessionError(
                "morphology.mujoco.reset.qpos length must equal the loaded MJCF nq"
            )
        if self.config.reset_qvel is not None and len(self.config.reset_qvel) != int(self._model.nv):
            raise DirectMuJoCoSessionError(
                "morphology.mujoco.reset.qvel length must equal the loaded MJCF nv"
            )

    def reset(
        self,
        *,
        phase: str = "DIRECT_MUJOCO_EXPERIMENTAL",
        execution_id: str = "reset",
        initial_state: Mapping[str, Any] | None = None,
    ) -> None:
        del phase, execution_id
        self._require_open()
        if initial_state is not None and not isinstance(initial_state, Mapping):
            raise DirectMuJoCoSessionError("initial_state must be a mapping when supplied")
        if self._capture is not None:
            raise DirectMuJoCoSessionError("cannot reset while external recording is active")
        if self.config.reset_policy == "model_default":
            self._mj.mj_resetData(self._model, self._data)
        elif self.config.reset_keyframe is not None:
            keyframe_id = self._id(
                self._mj.mjtObj.mjOBJ_KEY,
                self.config.reset_keyframe,
                "morphology.mujoco.reset.keyframe",
            )
            self._mj.mj_resetDataKeyframe(self._model, self._data, keyframe_id)
        else:
            assert self.config.reset_qpos is not None
            self._mj.mj_resetData(self._model, self._data)
            for index, value in enumerate(self.config.reset_qpos):
                self._data.qpos[index] = value
            qvel = self.config.reset_qvel or (0.0,) * int(self._model.nv)
            for index, value in enumerate(qvel):
                self._data.qvel[index] = value
        if self.task_config is not None:
            task_state = dict(self.task_config.initial_state)
            task_state.setdefault("scene_entrypoint", self.task_config.scene_entrypoint)
            task_state.setdefault("reset", dict(self.task_config.reset))
            self._apply_initial_state(task_state)
        self._apply_initial_state(initial_state)
        self._mj.mj_forward(self._model, self._data)
        self._accepted_action_count = 0
        self._physics_step_count = 0
        self._candidate_invocation_count = 0
        self._candidate_observation_reads = 0
        self._truth_trace = []
        self._last_invocation = None
        self._record_truth_sample()

    def _apply_initial_state(self, initial_state: Mapping[str, Any] | None) -> None:
        """Apply only Framework-owned, declarative task reset values.

        Public task state is intentionally ignored.  A task may provide a
        complete ``qpos``/``qvel`` reset (directly or under ``reset``), and a
        scene entrypoint assertion; arbitrary fields never write MuJoCo state.
        """

        if not initial_state:
            return
        declared_scene = initial_state.get("scene_entrypoint")
        nested_task = initial_state.get("task_instance", initial_state.get("task"))
        if declared_scene is None and isinstance(nested_task, Mapping):
            declared_scene = nested_task.get("scene_entrypoint")
        if declared_scene is not None:
            if not isinstance(declared_scene, str) or not declared_scene.strip():
                raise DirectMuJoCoSessionError("initial_state.scene_entrypoint must be non-empty text")
            allowed_scenes = {self.config.entrypoint}
            if self.task_config is not None:
                allowed_scenes.add(self.task_config.scene_entrypoint)
            if declared_scene not in allowed_scenes:
                raise DirectMuJoCoSessionError(
                    "task scene_entrypoint does not match the session's resolved MJCF scene"
                )
        reset = initial_state.get("reset")
        if reset is None and isinstance(nested_task, Mapping):
            reset = nested_task.get("reset")
        if reset is None:
            reset = initial_state.get("mujoco")
        if reset is None:
            reset = initial_state
        if not isinstance(reset, Mapping):
            raise DirectMuJoCoSessionError("initial_state.reset must be an object when supplied")
        qpos = reset.get("qpos")
        qvel = reset.get("qvel")
        if qpos is None and qvel is None:
            return
        if qpos is None:
            raise DirectMuJoCoSessionError("initial_state.reset.qpos is required with qvel")
        qpos_values = _vector(qpos, "initial_state.reset.qpos")
        if len(qpos_values) != int(self._model.nq):
            raise DirectMuJoCoSessionError("initial_state.reset.qpos length must equal loaded MJCF nq")
        qvel_values = (
            _vector(qvel, "initial_state.reset.qvel")
            if qvel is not None
            else [0.0] * int(self._model.nv)
        )
        if len(qvel_values) != int(self._model.nv):
            raise DirectMuJoCoSessionError("initial_state.reset.qvel length must equal loaded MJCF nv")
        for index, value in enumerate(qpos_values):
            self._data.qpos[index] = value
        for index, value in enumerate(qvel_values):
            self._data.qvel[index] = value

    def _send_action(self, action: Mapping[str, Real]) -> dict[str, Any]:
        self._require_open()
        if not isinstance(action, Mapping) or not action:
            raise DirectMuJoCoSessionError("direct actuator action must be a non-empty mapping")
        unknown = [str(name) for name in action if name not in self._actuator_ids]
        if unknown:
            raise DirectMuJoCoSessionError(f"action contains unknown actuator names: {unknown}")
        values: dict[str, float] = {}
        for name, value in action.items():
            if not isinstance(name, str) or not name.strip():
                raise DirectMuJoCoSessionError("actuator action names must be non-empty text")
            values[name] = _finite(value, f"action[{name!r}]")
        for name, value in values.items():
            self._data.ctrl[self._actuator_ids[name]] = value
        self._accepted_action_count += 1
        return {
            "mode": "DIRECT_MUJOCO_EXPERIMENTAL",
            "accepted_actuator_names": list(values),
            "control": dict(values),
        }

    def _step(self, seconds: float | None = None, *, steps: int | None = None) -> dict[str, Any]:
        self._require_open()
        if seconds is not None and steps is not None:
            raise DirectMuJoCoSessionError("step accepts seconds or steps, not both")
        timestep = float(self._model.opt.timestep)
        if not math.isfinite(timestep) or timestep <= 0:
            raise DirectMuJoCoSessionError("loaded MJCF has an invalid positive timestep")
        if steps is not None:
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
                raise DirectMuJoCoSessionError("step steps must be a non-negative integer")
            count = steps
        else:
            duration = timestep if seconds is None else _finite(seconds, "step seconds")
            if duration < 0:
                raise DirectMuJoCoSessionError("step seconds must be non-negative")
            count = int(math.ceil(duration / timestep)) if duration else 0
        for _ in range(count):
            self._mj.mj_step(self._model, self._data)
            self._physics_step_count += 1
            self._record_truth_sample()
        if self._capture is not None:
            self._capture.on_step(float(self._data.time))
        return self._observation()

    def step(self, seconds: float | None = None, *, steps: int | None = None) -> dict[str, Any]:
        return self._step(seconds, steps=steps)

    def send_action(self, action: Mapping[str, Real]) -> dict[str, Any]:
        return self._send_action(action)

    def _joint_value(self, name: str, *, velocity: bool) -> float | list[float]:
        joint_id = self._joint_ids[name]
        qdim, vdim = self._joint_dimensions(joint_id)
        if velocity:
            start, width = int(self._model.jnt_dofadr[joint_id]), vdim
            values = _vector(self._data.qvel[start : start + width], f"joint {name} velocity")
        else:
            start, width = int(self._model.jnt_qposadr[joint_id]), qdim
            values = _vector(self._data.qpos[start : start + width], f"joint {name} position")
        return values[0] if len(values) == 1 else values

    def _sensor_value(self, name: str) -> float | list[float]:
        sensor_id = self._sensor_ids[name]
        start = int(self._model.sensor_adr[sensor_id])
        width = int(self._model.sensor_dim[sensor_id])
        values = _vector(self._data.sensordata[start : start + width], f"sensor {name}")
        return values[0] if len(values) == 1 else values

    def _observation(self) -> dict[str, Any]:
        self._require_open()
        joints = {
            name: {
                "position": self._joint_value(name, velocity=False),
                "velocity": self._joint_value(name, velocity=True),
            }
            for name in self.joint_names
        }
        bodies = {
            name: {
                "position": _vector(self._data.xpos[body_id], f"body {name} position"),
                "orientation_quat": _vector(self._data.xquat[body_id], f"body {name} orientation"),
            }
            for name, body_id in self._body_ids.items()
        }
        sites = {
            name: {
                "position": _vector(self._data.site_xpos[site_id], f"site {name} position"),
                "orientation_matrix": _vector(self._data.site_xmat[site_id], f"site {name} orientation"),
            }
            for name, site_id in self._site_ids.items()
        }
        sensors = {name: self._sensor_value(name) for name in self._sensor_ids}
        return {
            "mode": "DIRECT_MUJOCO_EXPERIMENTAL",
            "status": "EXPERIMENTAL",
            "simulation_time_s": float(self._data.time),
            "actuators": {
                name: float(self._data.ctrl[actuator_id])
                for name, actuator_id in self._actuator_ids.items()
            },
            "joints": joints,
            "joint_positions": {name: value["position"] for name, value in joints.items()},
            "joint_velocities": {name: value["velocity"] for name, value in joints.items()},
            "bodies": bodies,
            "sites": sites,
            "sensors": sensors,
        }

    def _record_truth_sample(self) -> None:
        self._truth_trace.append((float(self._data.time), copy.deepcopy(self._observation())))

    def _candidate_observation(self) -> dict[str, Any]:
        self._candidate_observation_reads += 1
        return self._observation()

    def state(self) -> dict[str, Any]:
        return self._observation()

    def get_observation(self) -> dict[str, Any]:
        return self._observation()

    def joint_observation(self) -> dict[str, Any]:
        return copy.deepcopy(self._observation()["joints"])

    def body_observation(self) -> dict[str, Any]:
        return copy.deepcopy(self._observation()["bodies"])

    def sensor_observation(self) -> dict[str, Any]:
        return copy.deepcopy(self._observation()["sensors"])

    def _render_rgb(self, *_args: Any, **_kwargs: Any) -> bytes:
        self._require_open()
        if self._renderer is None:
            if self._renderer_factory is not None:
                self._renderer = self._renderer_factory(
                    self._model,
                    self.config.render_height,
                    self.config.render_width,
                )
            else:
                try:
                    self._renderer = self._mj.Renderer(
                        self._model,
                        height=self.config.render_height,
                        width=self.config.render_width,
                    )
                except Exception as exc:
                    raise DirectMuJoCoSessionError("could not create the configured RGB renderer") from exc
        try:
            self._renderer.update_scene(self._data, camera=self._camera_id)
            rendered = self._renderer.render()
            payload = rendered.tobytes() if hasattr(rendered, "tobytes") else bytes(rendered)
        except Exception as exc:
            raise DirectMuJoCoSessionError("configured RGB renderer failed") from exc
        expected = self.config.render_width * self.config.render_height * 3
        if len(payload) != expected:
            raise DirectMuJoCoSessionError(
                f"configured RGB renderer returned {len(payload)} bytes; expected {expected}"
            )
        return bytes(payload)

    def render_rgb(self) -> bytes:
        return self._render_rgb()

    def capture_frame(self) -> RGBFrame:
        return RGBFrame(
            self.simulation_time_s,
            self.config.render_width,
            self.config.render_height,
            self._render_rgb(),
        )

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        self._require_open()
        if not isinstance(phase, str) or not phase.strip() or not isinstance(execution_id, str) or not execution_id.strip():
            raise DirectMuJoCoSessionError("recording phase and execution_id must be non-empty text")
        if self._capture is not None:
            raise DirectMuJoCoSessionError("external recording is already active")
        try:
            capture = MuJoCoFrameCapture(self._video_profile, self._render_rgb)
            capture.start(self.simulation_time_s)
        except Exception as exc:
            raise DirectMuJoCoSessionError("could not start configured RGB recording") from exc
        self._capture = capture

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        self._require_open()
        capture = self._capture
        if capture is None:
            raise DirectMuJoCoSessionError("no external recording is active")
        self._capture = None
        try:
            frames = capture.stop(self.simulation_time_s)
        except Exception as exc:
            raise DirectMuJoCoSessionError("configured RGB recording could not close") from exc
        if not isinstance(frames, tuple) or not all(isinstance(frame, RGBFrame) for frame in frames):
            raise DirectMuJoCoSessionError("configured RGB recording returned invalid frames")
        return frames

    def invoke(self, candidate: Any, capability_id: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._require_open()
        invoke = getattr(candidate, "_invoke", None)
        if not callable(invoke):
            raise DirectMuJoCoSessionError("candidate does not expose the Framework invocation boundary")
        if not isinstance(arguments, Mapping):
            raise DirectMuJoCoSessionError("capability arguments must be a mapping")
        start_time = self.simulation_time_s
        start_actions = self._accepted_action_count
        start_steps = self._physics_step_count
        start_reads = self._candidate_observation_reads
        trace_start = max(0, len(self._truth_trace) - 1)
        self._candidate_invocation_count += 1
        try:
            result = invoke(capability_id, copy.deepcopy(dict(arguments)), self._facade)
            if not isinstance(result, Mapping):
                raise DirectMuJoCoSessionError("candidate result must be a mapping")
            return copy.deepcopy(dict(result))
        finally:
            self._last_invocation = {
                "capability_id": capability_id,
                "start_time_s": start_time,
                "end_time_s": self.simulation_time_s,
                "start_actions": start_actions,
                "end_actions": self._accepted_action_count,
                "start_steps": start_steps,
                "end_steps": self._physics_step_count,
                "start_reads": start_reads,
                "end_reads": self._candidate_observation_reads,
                "trace_start": trace_start,
            }

    def _direct_route_detail(self) -> tuple[dict[str, Any], bool]:
        invocation = self._last_invocation
        observed = invocation is not None
        if invocation is None:
            start_time = float(self._truth_trace[0][0]) if self._truth_trace else self.simulation_time_s
            end_time = self.simulation_time_s
            accepted = 0
            candidate_steps = 0
            state_reads = 0
        else:
            start_time = float(invocation["start_time_s"])
            end_time = float(invocation["end_time_s"])
            accepted = int(invocation["end_actions"]) - int(invocation["start_actions"])
            candidate_steps = int(invocation["end_steps"]) - int(invocation["start_steps"])
            state_reads = int(invocation["end_reads"]) - int(invocation["start_reads"])
        time_progressed = end_time > start_time + 1e-12
        route_verified = bool(
            observed
            and accepted > 0
            and candidate_steps > 0
            and time_progressed
            and state_reads > 0
        )
        detail = {
            "mode": "DIRECT_MUJOCO_EXPERIMENTAL",
            "status": "EXPERIMENTAL",
            "evidence_scope": self.evidence_scope,
            "robot_model_id": self.robot_model_id,
            "robot_configuration_id": self.robot_configuration_id,
            "candidate_invocation_observed": observed,
            "accepted_command_count": accepted,
            "candidate_physics_steps": candidate_steps,
            "physics_steps": candidate_steps,
            "simulation_time_progressed": time_progressed,
            "state_route_observed": state_reads > 0,
            "physics_progress": candidate_steps > 0 and time_progressed,
            "direct_route_verified": route_verified,
            "verified": route_verified,
            "sdk_grounded": False,
        }
        return detail, route_verified

    @staticmethod
    def _criterion_list(invocation: HarnessInvocation) -> tuple[dict[str, Any], ...]:
        raw = tuple(invocation.criteria)
        if not raw:
            criterion_id = invocation.criterion_id or "direct-mujoco-criterion"
            return ({
                "criterion_id": criterion_id,
                "metric": invocation.metric,
                "measurement": copy.deepcopy(dict(invocation.measurement)),
                "dwell_s": invocation.dwell_s,
                "timeout_s": invocation.timeout_s,
            },)
        criteria: list[dict[str, Any]] = []
        for index, criterion in enumerate(raw):
            if not isinstance(criterion, Mapping):
                raise DirectMuJoCoSessionError(f"validation criterion {index} must be an object")
            item = copy.deepcopy(dict(criterion))
            item.setdefault("criterion_id", invocation.criterion_id)
            item.setdefault("metric", invocation.metric)
            item.setdefault("measurement", copy.deepcopy(dict(invocation.measurement)))
            item.setdefault("dwell_s", invocation.dwell_s)
            item.setdefault("timeout_s", invocation.timeout_s)
            if not isinstance(item.get("criterion_id"), str) or not item["criterion_id"].strip():
                raise DirectMuJoCoSessionError(f"validation criterion {index} has no criterion_id")
            if not isinstance(item.get("metric"), str) or not item["metric"].strip():
                raise DirectMuJoCoSessionError(f"validation criterion {index} has no metric")
            criteria.append(item)
        return tuple(criteria)

    @staticmethod
    def _context_value(
        path: Any,
        inputs: Mapping[str, Any],
        initial_state: Mapping[str, Any],
        label: str,
    ) -> Any:
        context = {"inputs": inputs, "initial_state": initial_state}
        value = _path_value(context, path, label)
        if value is not _MISSING:
            return value
        for root in (inputs, initial_state):
            value = _path_value(root, path, label)
            if value is not _MISSING:
                return value
        return _MISSING

    def _task_input_context(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if self.task_config is not None:
            context["parameters"] = copy.deepcopy(dict(self.task_config.parameters))
            context["task_id"] = self.task_config.task_id
        context.update(copy.deepcopy(dict(inputs)))
        return context

    @staticmethod
    def _observation_operand(observation: Mapping[str, Any], source: Any, label: str) -> Any:
        if isinstance(source, Mapping):
            return DirectMuJoCoEvaluationRobotSession._metric_spec_value(
                observation, source, {}, {}, label
            )
        value = _path_value(observation, source, label)
        if value is _MISSING and isinstance(source, str):
            value = _find_named_value(observation, source)
        return value

    @classmethod
    def _reference_value(
        cls,
        observation: Mapping[str, Any],
        spec: Mapping[str, Any],
        inputs: Mapping[str, Any],
        initial_state: Mapping[str, Any],
        label: str,
    ) -> Any:
        for key in ("target_observation_path", "reference_observation_path"):
            if key in spec:
                value = cls._observation_operand(observation, spec[key], f"{label}.{key}")
                if value is not _MISSING:
                    return value
                raise DirectMuJoCoSessionError(f"{label}.{key} is unavailable")
        for key in ("target_path", "reference_path", "input_path", "target_input_path"):
            if key in spec:
                value = cls._context_value(spec[key], inputs, initial_state, f"{label}.{key}")
                if value is not _MISSING:
                    return value
                raise DirectMuJoCoSessionError(f"{label}.{key} does not resolve in task inputs")
        for key in ("target", "reference"):
            if key in spec:
                reference = spec[key]
                if isinstance(reference, Mapping):
                    if "observation_path" in reference:
                        value = cls._observation_operand(
                            observation,
                            reference["observation_path"],
                            f"{label}.{key}.observation_path",
                        )
                        if value is not _MISSING:
                            return value
                        raise DirectMuJoCoSessionError(
                            f"{label}.{key}.observation_path is unavailable"
                        )
                    if "input_path" in reference or "path" in reference:
                        path = reference.get("input_path", reference.get("path"))
                        value = cls._context_value(path, inputs, initial_state, f"{label}.{key}")
                        if value is not _MISSING:
                            return value
                    if "value" in reference:
                        return reference["value"]
                else:
                    return reference
        raise DirectMuJoCoSessionError(f"{label} requires a declared target or reference")

    @classmethod
    def _metric_spec_value(
        cls,
        observation: Mapping[str, Any],
        spec: Mapping[str, Any],
        inputs: Mapping[str, Any],
        initial_state: Mapping[str, Any],
        label: str,
    ) -> Any:
        operation = str(spec.get("operator", spec.get("op", "path"))).strip().lower()
        source = next(
            (spec[key] for key in ("observation_path", "path", "source_path", "source") if key in spec),
            None,
        )
        if source is None and "metric" in spec:
            source = spec["metric"]
        if operation in {"path", "read", "value"}:
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no declared observation path")
            value = cls._observation_operand(observation, source, label)
            if value is _MISSING:
                raise DirectMuJoCoSessionError(f"{label} observation path is unavailable")
            if "index" in spec:
                index = spec["index"]
                if isinstance(index, bool) or not isinstance(index, int):
                    raise DirectMuJoCoSessionError(f"{label}.index must be an integer")
                values = _finite_vector(value, label)
                if index < 0 or index >= len(values):
                    raise DirectMuJoCoSessionError(f"{label}.index is outside the observation vector")
                return values[index]
            return value
        if operation == "component":
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no component source")
            index = spec.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise DirectMuJoCoSessionError(f"{label}.index must be an integer")
            values = _finite_vector(cls._observation_operand(observation, source, label), label)
            if index < 0 or index >= len(values):
                raise DirectMuJoCoSessionError(f"{label}.index is outside the observation vector")
            return values[index]
        if operation == "norm":
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no norm source")
            return math.sqrt(sum(value * value for value in _finite_vector(
                cls._observation_operand(observation, source, label), label
            )))
        if operation in {"distance", "difference", "delta", "absolute_difference"}:
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no difference source")
            left = cls._observation_operand(observation, source, label)
            right = cls._reference_value(observation, spec, inputs, initial_state, label)
            offset_path = next(
                (
                    spec[key]
                    for key in ("reference_offset_path", "target_offset_path", "offset_path")
                    if key in spec
                ),
                _MISSING,
            )
            if offset_path is not _MISSING:
                offset = cls._context_value(offset_path, inputs, initial_state, f"{label}.offset")
                if offset is _MISSING:
                    raise DirectMuJoCoSessionError(f"{label}.offset does not resolve in task inputs")
                if isinstance(right, Sequence) and not isinstance(right, (str, bytes, bytearray)):
                    right_values = _finite_vector(right, f"{label}.reference")
                    offset_values = _finite_vector(offset, f"{label}.offset")
                    if len(right_values) != len(offset_values):
                        raise DirectMuJoCoSessionError(f"{label} reference and offset dimensions differ")
                    right = [right_value + offset_value for right_value, offset_value in zip(right_values, offset_values, strict=True)]
                else:
                    right = _finite_scalar(right, f"{label}.reference") + _finite_scalar(offset, f"{label}.offset")
            if isinstance(left, Sequence) and not isinstance(left, (str, bytes, bytearray)):
                left_values = _finite_vector(left, f"{label}.source")
                right_values = _finite_vector(right, f"{label}.reference")
                if len(left_values) != len(right_values):
                    raise DirectMuJoCoSessionError(f"{label} source and reference dimensions differ")
                deltas = [left_value - right_value for left_value, right_value in zip(left_values, right_values, strict=True)]
                if operation == "difference" and spec.get("absolute") is False:
                    return deltas[0] if len(deltas) == 1 else deltas
                return math.sqrt(sum(delta * delta for delta in deltas))
            delta = _finite_scalar(left, f"{label}.source") - _finite_scalar(right, f"{label}.reference")
            return abs(delta) if operation != "difference" or spec.get("absolute", True) else delta
        if operation in {"sum", "total"}:
            if "paths" in spec:
                values = [
                    cls._observation_operand(observation, path, f"{label}.paths[{index}]")
                    for index, path in enumerate(spec["paths"])
                ]
            elif source is not None:
                values = cls._observation_operand(observation, source, label)
            else:
                raise DirectMuJoCoSessionError(f"{label} has no sum source")
            return sum(_finite_vector(values, label))
        if operation == "count":
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no count source")
            value = cls._observation_operand(observation, source, label)
            if isinstance(value, Mapping) or (isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))):
                return float(len(value))
            raise DirectMuJoCoSessionError(f"{label} count source is not a collection")
        if operation in {"any", "all"}:
            if source is None:
                raise DirectMuJoCoSessionError(f"{label} has no boolean source")
            value = cls._observation_operand(observation, source, label)
            values = value.values() if isinstance(value, Mapping) else value
            if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
                raise DirectMuJoCoSessionError(f"{label} boolean source is not a collection")
            return bool(any(values) if operation == "any" else all(values))
        raise DirectMuJoCoSessionError(f"{label} uses unsupported measurement operator {operation!r}")

    def _measurement_spec(
        self,
        criterion: Mapping[str, Any],
        *,
        metric: str,
        measurement: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        for candidate in (measurement, criterion):
            if isinstance(candidate, Mapping) and any(
                key in candidate
                for key in ("path", "observation_path", "source_path", "operator", "op")
            ):
                return candidate
        measurement_id = measurement.get("measurement_id")
        for declaration in self.config.measurement_declarations:
            names = {
                declaration.get("metric"),
                declaration.get("name"),
                declaration.get("measurement_id"),
            }
            if metric in names or measurement_id in names:
                return declaration
        return {"path": metric}

    def _trace_window(
        self,
        invocation: HarnessInvocation,
        criteria: Sequence[Mapping[str, Any]],
    ) -> tuple[tuple[tuple[float, dict[str, Any]], ...], float]:
        if not self._truth_trace:
            raise DirectMuJoCoSessionError("direct MuJoCo truth trace is empty")
        last = self._last_invocation
        start_index = int(last["trace_start"]) if last is not None else 0
        available = self._truth_trace[start_index:]
        if not available:
            available = self._truth_trace[-1:]
        start_time = float(last["start_time_s"]) if last is not None else float(available[0][0])
        end_time = float(last["end_time_s"]) if last is not None else float(available[-1][0])
        dwell = max(
            [_finite(float(item.get("dwell_s", invocation.dwell_s)), "validation dwell") for item in criteria]
            or [_finite(invocation.dwell_s, "validation dwell")]
        )
        timeout = min(
            [_finite(float(item.get("timeout_s", invocation.timeout_s)), "validation timeout") for item in criteria]
            or [_finite(invocation.timeout_s, "validation timeout")]
        )
        if dwell < 0 or timeout < 0:
            raise DirectMuJoCoSessionError("validation dwell/timeout must be non-negative")
        window_start = max(start_time, end_time - min(max(dwell, 0.0), timeout))
        selected = [item for item in available if item[0] >= window_start - 1e-12 and item[0] <= end_time + 1e-12]
        preceding = [item for item in available if item[0] <= window_start + 1e-12]
        if preceding and (not selected or selected[0] is not preceding[-1]):
            selected.insert(0, preceding[-1])
        if not selected:
            selected = [available[-1]]
        origin = float(selected[0][0])
        return tuple(selected), origin

    def _guard_results(
        self,
        invocation: HarnessInvocation,
        route: Mapping[str, Any],
        finite: bool,
    ) -> dict[str, bool]:
        direct_verified = route.get("direct_route_verified") is True
        base = {
            "direct-route-verified": direct_verified,
            "sdk-route-verified": False,
            "trusted-external-verdict": direct_verified,
            "finite-required-state": finite,
            "finite-physical-state": finite,
            "scene-entrypoint-bound": self._model_path.is_file(),
            "candidate-invocation-observed": route.get("candidate_invocation_observed") is True,
            "accepted-command-observed": bool(route.get("accepted_command_count", 0) > 0),
            "physics-progress-observed": route.get("physics_progress") is True,
            "state-route-observed": route.get("state_route_observed") is True,
        }
        for guard_id in invocation.guard_ids:
            if guard_id in base:
                continue
            lowered = guard_id.lower()
            if "finite" in lowered or "state" in lowered and "route" not in lowered:
                base[guard_id] = finite
            elif "scene" in lowered or "entrypoint" in lowered:
                base[guard_id] = base["scene-entrypoint-bound"]
            elif "route" in lowered or "command" in lowered or "action" in lowered:
                base[guard_id] = direct_verified
            elif "trusted" in lowered or "external" in lowered:
                base[guard_id] = direct_verified
            else:
                base[guard_id] = False
        return base

    def validation_evidence(self, invocation: HarnessInvocation) -> ValidationEvidence:
        self._require_open()
        if not isinstance(invocation, HarnessInvocation):
            raise DirectMuJoCoSessionError("validation evidence requires a typed Harness invocation")
        criteria = self._criterion_list(invocation)
        selected, origin = self._trace_window(invocation, criteria)
        route, _direct_verified = self._direct_route_detail()
        inputs = self._task_input_context(
            invocation.inputs if isinstance(invocation.inputs, Mapping) else {}
        )
        initial_state = invocation.initial_state if isinstance(invocation.initial_state, Mapping) else {}
        samples_by_criterion: dict[str, tuple[MeasurementSample, ...]] = {}
        finite = True
        for criterion in criteria:
            criterion_id = str(criterion["criterion_id"])
            metric = str(criterion["metric"])
            measurement = criterion.get("measurement")
            if not isinstance(measurement, Mapping):
                raise DirectMuJoCoSessionError(f"criterion {criterion_id} measurement declaration is missing")
            spec = self._measurement_spec(criterion, metric=metric, measurement=measurement)
            samples: list[MeasurementSample] = []
            for time_s, observation in selected:
                value = self._metric_spec_value(
                    observation,
                    spec,
                    inputs,
                    initial_state,
                    f"criterion {criterion_id} metric {metric!r}",
                )
                scalar = _finite_scalar(value, f"criterion {criterion_id} metric {metric!r}")
                samples.append(MeasurementSample(max(0.0, float(time_s) - origin), scalar))
            if not samples:
                raise DirectMuJoCoSessionError(f"criterion {criterion_id} has no physical samples")
            samples_by_criterion[criterion_id] = tuple(samples)
            finite = finite and all(
                math.isfinite(sample.time_s) and sample.time_s >= 0 and math.isfinite(sample.value)
                for sample in samples
            )
        if not finite:
            raise DirectMuJoCoSessionError("direct MuJoCo criterion measurements are not finite")
        primary = samples_by_criterion[criteria[0]["criterion_id"]]
        elapsed_s = max(0.0, float(selected[-1][0]) - origin)
        return ValidationEvidence(
            samples=primary,
            elapsed_s=elapsed_s,
            guard_results=self._guard_results(invocation, route, finite),
            sdk_route_verified=_direct_verified,
            route_evidence=route,
            criterion_samples=samples_by_criterion,
        )

    def demo_evidence(self, task_id: str) -> Mapping[str, Any]:
        self._require_open()
        if not isinstance(task_id, str) or not task_id.strip():
            raise DirectMuJoCoSessionError("task_id must be non-empty text")
        if self.task_config is not None and self.task_config.task_id != task_id:
            raise DirectMuJoCoSessionError(
                f"task_id {task_id!r} does not match the bound task {self.task_config.task_id!r}"
            )
        declarations = []
        for declaration in self.config.measurement_declarations:
            task_ids = declaration.get("task_ids", declaration.get("task_id"))
            if task_ids is None:
                declarations.append(declaration)
            elif isinstance(task_ids, str) and task_ids == task_id:
                declarations.append(declaration)
            elif isinstance(task_ids, Sequence) and not isinstance(task_ids, (str, bytes, bytearray)) and task_id in task_ids:
                declarations.append(declaration)
        if not declarations:
            raise DirectMuJoCoSessionError(
                f"no declared direct MuJoCo Demo metric mapping exists for task {task_id!r}"
            )
        route, _direct_verified = self._direct_route_detail()
        trace = tuple(self._truth_trace)
        if not trace:
            raise DirectMuJoCoSessionError("direct MuJoCo Demo truth trace is empty")
        origin = float(trace[0][0])
        samples: list[dict[str, Any]] = []
        latest: dict[str, float] = {}
        task_inputs = self._task_input_context({})
        for time_s, observation in trace:
            metrics: dict[str, float] = {}
            for declaration in declarations:
                metric = declaration.get("metric", declaration.get("name"))
                if not isinstance(metric, str) or not metric.strip():
                    raise DirectMuJoCoSessionError("direct MuJoCo Demo measurement has no metric name")
                value = self._metric_spec_value(
                    observation,
                    declaration,
                    task_inputs,
                    {},
                    f"Demo task {task_id} metric {metric!r}",
                )
                metrics[metric] = _finite_scalar(value, f"Demo task {task_id} metric {metric!r}")
            latest.update(metrics)
            samples.append({
                "time_s": max(0.0, float(time_s) - origin),
                "phase": "motion" if float(time_s) > origin else "reset",
                "metrics": metrics,
            })
        return {
            "mode": "DIRECT_MUJOCO_EXPERIMENTAL",
            "status": "EXPERIMENTAL",
            "task_id": task_id,
            "samples": samples,
            "metrics": latest,
            "elapsed_s": max(0.0, float(trace[-1][0]) - origin),
            "duration_s": max(0.0, float(trace[-1][0]) - origin),
            "guard_results": {
                "direct-route-verified": route["direct_route_verified"],
                "trusted-external-verdict": route["direct_route_verified"],
                "finite-required-state": True,
                "scene-entrypoint-bound": self._model_path.is_file(),
            },
            "route_evidence": route,
            "direct_route_verified": route["direct_route_verified"],
            "accepted_action_count": route["accepted_command_count"],
            "physics_step_count": route["physics_steps"],
        }

    def close(self) -> None:
        if self._closed:
            return
        errors: list[str] = []
        if self._capture is not None:
            try:
                self._capture.stop(float(self._data.time))
            except Exception as exc:
                errors.append(f"recording: {exc}")
            self._capture = None
        if self._renderer is not None:
            close = getattr(self._renderer, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    errors.append(f"renderer: {exc}")
            self._renderer = None
        self._closed = True
        if errors:
            raise DirectMuJoCoSessionError("direct MuJoCo session cleanup failed: " + "; ".join(errors))

    def __enter__(self) -> "DirectMuJoCoEvaluationRobotSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def create_direct_mujoco_session(
    morphology_record: Mapping[str, Any] | str | Path,
    *,
    asset_root: str | Path | None = None,
    video_profile: FrozenVideoProfile | None = None,
    task_config: Mapping[str, Any] | str | Path | DirectMuJoCoTaskConfig | None = None,
    task_id: str | None = None,
) -> DirectMuJoCoEvaluationRobotSession:
    """Create a session from a morphology Library record, not a robot name."""

    return DirectMuJoCoEvaluationRobotSession(
        morphology_record,
        asset_root=asset_root,
        video_profile=video_profile,
        task_config=task_config,
        task_id=task_id,
    )


create_direct_mujoco_evaluation_session = create_direct_mujoco_session
DirectMuJoCoSession = DirectMuJoCoEvaluationRobotSession


__all__ = [
    "DirectMuJoCoEvaluationRobotSession",
    "DirectMuJoCoFacade",
    "DirectMuJoCoSession",
    "DirectMuJoCoSessionError",
    "create_direct_mujoco_evaluation_session",
    "create_direct_mujoco_session",
]
