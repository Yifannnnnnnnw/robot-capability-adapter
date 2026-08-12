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

from ...evaluation import FrozenVideoProfile, RGBFrame
from ...foundation.errors import ContractError
from ..session_support import MuJoCoFrameCapture
from .config import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoLibraryConfig,
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
        return owner._observation()

    def get_observation(self) -> dict[str, Any]:
        return self.state()

    def step(self, seconds: float | None = None, *, steps: int | None = None) -> dict[str, Any]:
        """Advance physics and return the resulting public observation."""

        owner = object.__getattribute__(self, "_session")
        return owner._step(seconds, steps=steps)


class DirectMuJoCoEvaluationRobotSession:
    """Evaluation-session-shaped owner for one direct MuJoCo experiment.

    The lifecycle and ``sdk``/``invoke`` methods are compatible with the
    narrow evaluation-session boundary.  Formal Validation evidence is
    intentionally unsupported: this route is explicitly experimental and
    returns no READY/PASS state.
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
        if self.config.reset_keyframe is not None:
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
        self._mj.mj_forward(self._model, self._data)
        self._accepted_action_count = 0
        self._physics_step_count = 0

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
        result = invoke(capability_id, copy.deepcopy(dict(arguments)), self._facade)
        if not isinstance(result, Mapping):
            raise DirectMuJoCoSessionError("candidate result must be a mapping")
        return copy.deepcopy(dict(result))

    def validation_evidence(self, _invocation: Any) -> Any:
        raise DirectMuJoCoSessionError(
            "formal Validation evidence is not implemented for DIRECT_MUJOCO_EXPERIMENTAL"
        )

    def demo_evidence(self, _task_id: str) -> Mapping[str, Any]:
        return self._observation()

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
) -> DirectMuJoCoEvaluationRobotSession:
    """Create a session from a morphology Library record, not a robot name."""

    return DirectMuJoCoEvaluationRobotSession(
        morphology_record,
        asset_root=asset_root,
        video_profile=video_profile,
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
