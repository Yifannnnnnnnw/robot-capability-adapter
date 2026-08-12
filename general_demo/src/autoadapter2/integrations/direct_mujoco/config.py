"""Library-driven configuration for the experimental direct MuJoCo adapter.

The adapter deliberately consumes one morphology record and one fixed MJCF
asset root.  It does not infer names from the model, copy robot-specific
configuration, or maintain a registry.  A record that is not complete enough
to drive the generic session fails before MuJoCo is opened.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class DirectMuJoCoConfigurationError(ValueError):
    """A morphology record cannot configure the direct experimental route."""


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DirectMuJoCoConfigurationError(f"{label} is required and must be an object")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DirectMuJoCoConfigurationError(f"{label} is required and must be non-empty text")
    return value.strip()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DirectMuJoCoConfigurationError(f"{label} must be a finite number")
    return float(value)


def _number_list(value: Any, label: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise DirectMuJoCoConfigurationError(f"{label} must be an array of finite numbers")
    return tuple(_finite_number(item, f"{label}[{index}]") for index, item in enumerate(value))


def _names(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise DirectMuJoCoConfigurationError(f"{label} is required and must be a string array")
    result = tuple(_required_text(item, f"{label}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not result:
        raise DirectMuJoCoConfigurationError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise DirectMuJoCoConfigurationError(f"{label} must not contain duplicate names")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DirectMuJoCoConfigurationError(f"{label} must be a positive integer")
    return value


def _safe_relative_path(value: Any, label: str) -> str:
    path_text = _required_text(value, label)
    path = Path(path_text)
    if "\\" in path_text or path.is_absolute() or ".." in path.parts:
        raise DirectMuJoCoConfigurationError(f"{label} must be a safe relative path")
    return path_text


def _declarations(value: Any, label: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    result: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        items = value.items()
        for name, declaration in items:
            if not isinstance(name, str) or not name.strip():
                raise DirectMuJoCoConfigurationError(
                    f"{label} declaration names must be non-empty text"
                )
            if not isinstance(declaration, Mapping):
                raise DirectMuJoCoConfigurationError(f"{label} declarations must be objects")
            item = dict(declaration)
            item.setdefault("metric", name.strip())
            result.append(item)
        return tuple(result)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, declaration in enumerate(value):
            if not isinstance(declaration, Mapping):
                raise DirectMuJoCoConfigurationError(f"{label}[{index}] must be an object")
            result.append(dict(declaration))
        return tuple(result)
    raise DirectMuJoCoConfigurationError(f"{label} must be an object or array")


@dataclass(frozen=True)
class DirectMuJoCoTaskConfig:
    """One declared task-instance binding for the shared direct route.

    Paths are expressed in the configured external asset-root namespace.  The
    task instance can provide private parameters used by Framework-owned
    measurement declarations; candidates never receive this object.
    """

    task_id: str
    scene_entrypoint: str | None
    reset: Mapping[str, Any]
    parameters: Mapping[str, Any]
    initial_state: Mapping[str, Any]
    body_names: tuple[str, ...] = ()
    site_names: tuple[str, ...] = ()
    sensor_names: tuple[str, ...] = ()
    measurement_declarations: tuple[dict[str, Any], ...] = ()
    robot_configuration_id: str | None = None

    @classmethod
    def from_value(
        cls,
        value: Mapping[str, Any] | str | Path,
        *,
        task_id: str | None = None,
    ) -> "DirectMuJoCoTaskConfig":
        if isinstance(value, Mapping):
            raw = dict(value)
        else:
            path = Path(value).expanduser().resolve()
            if not path.is_file() or path.is_symlink():
                raise DirectMuJoCoConfigurationError(
                    f"direct MuJoCo task config is not a regular file: {path}"
                )
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DirectMuJoCoConfigurationError(
                    f"could not read direct MuJoCo task config: {path}"
                ) from exc
            if not isinstance(loaded, Mapping):
                raise DirectMuJoCoConfigurationError(
                    "direct MuJoCo task config JSON must contain an object"
                )
            raw = dict(loaded)

        entries = raw.get("tasks", raw.get("instances"))
        selected: Mapping[str, Any]
        if entries is not None:
            if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
                raise DirectMuJoCoConfigurationError(
                    "direct MuJoCo task config tasks must be an array"
                )
            candidates = [item for item in entries if isinstance(item, Mapping)]
            if len(candidates) != len(entries) or not candidates:
                raise DirectMuJoCoConfigurationError(
                    "direct MuJoCo task config tasks must contain objects"
                )
            requested = task_id
            if requested is None and len(candidates) == 1:
                requested = candidates[0].get("task_id")
            selected_items = [item for item in candidates if item.get("task_id") == requested]
            if len(selected_items) != 1:
                raise DirectMuJoCoConfigurationError(
                    "direct MuJoCo task config requires one matching task_id"
                )
            selected = selected_items[0]
        else:
            selected = raw

        selected_task_id = selected.get("task_id", task_id)
        if not isinstance(selected_task_id, str) or not selected_task_id.strip():
            raise DirectMuJoCoConfigurationError("direct MuJoCo task config task_id is required")
        if task_id is not None and selected_task_id != task_id:
            raise DirectMuJoCoConfigurationError(
                "direct MuJoCo task config task_id does not match the requested task"
            )

        scene_value = selected.get(
            "asset_entrypoint",
            selected.get("scene_entrypoint", selected.get("simulation_entrypoint")),
        )
        scene_entrypoint = (
            _safe_relative_path(scene_value, "task.scene_entrypoint")
            if scene_value is not None
            else None
        )
        reset = selected.get("reset", {})
        if not isinstance(reset, Mapping):
            raise DirectMuJoCoConfigurationError("task.reset must be an object")
        parameters = selected.get("parameters", selected.get("inputs", {}))
        if not isinstance(parameters, Mapping):
            raise DirectMuJoCoConfigurationError("task.parameters must be an object")
        initial_state = selected.get("initial_state", {})
        if not isinstance(initial_state, Mapping):
            raise DirectMuJoCoConfigurationError("task.initial_state must be an object")
        frame_source = selected.get("frames", selected.get("mujoco", {}))
        if not isinstance(frame_source, Mapping):
            raise DirectMuJoCoConfigurationError("task.frames must be an object")
        if "frames" in frame_source and isinstance(frame_source["frames"], Mapping):
            frame_source = frame_source["frames"]
        body_names = _names(frame_source.get("body_names", []), "task.frames.body_names", allow_empty=True)
        site_names = _names(frame_source.get("site_names", []), "task.frames.site_names", allow_empty=True)
        sensor_names = _names(frame_source.get("sensor_names", []), "task.frames.sensor_names", allow_empty=True)
        declarations = selected.get(
            "measurements",
            selected.get("validation", {}).get("measurements")
            if isinstance(selected.get("validation"), Mapping)
            else None,
        )
        measurement_declarations = _declarations(declarations, "task.measurements")
        unresolved = tuple(
            str(item.get("metric", item.get("name", "<unnamed>")))
            for item in measurement_declarations
            if item.get("unresolved") is True
            or str(item.get("status", "")).strip().upper() == "UNRESOLVED"
        )
        if unresolved:
            joined = ", ".join(unresolved)
            raise DirectMuJoCoConfigurationError(
                f"direct task {selected_task_id.strip()} has unresolved measurement mappings: {joined}"
            )
        return cls(
            task_id=selected_task_id.strip(),
            scene_entrypoint=scene_entrypoint,
            reset=dict(reset),
            parameters=dict(parameters),
            initial_state=dict(initial_state),
            body_names=body_names,
            site_names=site_names,
            sensor_names=sensor_names,
            measurement_declarations=measurement_declarations,
            robot_configuration_id=(
                selected.get("robot_configuration_id")
                if selected.get("robot_configuration_id") is None
                else _required_text(selected.get("robot_configuration_id"), "task.robot_configuration_id")
            ),
        )


@dataclass(frozen=True)
class DirectMuJoCoLibraryConfig:
    """Resolved, robot-independent view of one morphology Library record.

    The canonical record extension consumed here is:

    ``mujoco.entrypoint``
        Relative path to the canonical MJCF entrypoint under ``asset_root``.
    ``mujoco.reset``
        Exactly one closed form: ``{"policy": "model_default"}``,
        ``{"keyframe": name}``, or full-model ``qpos`` with optional
        ``qvel``.
    ``mujoco.frames``
        ``body_names`` and ``site_names`` used for public frame observations.
    ``mujoco.sensor_names`` or top-level ``sensor_names``
        Named MuJoCo sensors projected to public observations.
    ``mujoco.render``
        Camera and RGB dimensions/fps used by the shared frame capture.
    """

    record_id: str
    record_version: str
    robot_model_id: str
    robot_configuration_id: str
    asset_root: Path
    entrypoint: str
    joint_names: tuple[str, ...]
    actuator_names: tuple[str, ...]
    body_names: tuple[str, ...]
    site_names: tuple[str, ...]
    sensor_names: tuple[str, ...]
    reset_policy: str | None
    reset_keyframe: str | None
    reset_qpos: tuple[float, ...] | None
    reset_qvel: tuple[float, ...] | None
    render_camera: str
    render_width: int
    render_height: int
    render_fps: int
    mujoco_version: str | None = None
    measurement_declarations: tuple[dict[str, Any], ...] = ()
    simulation_entrypoint: str | None = None
    bound_task: DirectMuJoCoTaskConfig | None = None

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, Any],
        *,
        asset_root: str | Path,
    ) -> "DirectMuJoCoLibraryConfig":
        if not isinstance(record, Mapping):
            raise DirectMuJoCoConfigurationError("morphology record must be an object")
        if record.get("record_type") not in (None, "morphology"):
            raise DirectMuJoCoConfigurationError("morphology.record_type must be 'morphology'")

        record_id = _required_text(record.get("id"), "morphology.id")
        record_version = _required_text(record.get("version"), "morphology.version")
        robot_model_id = _required_text(record.get("robot_model_id"), "morphology.robot_model_id")
        robot_configuration_id = _required_text(
            record.get("robot_configuration_id"),
            "morphology.robot_configuration_id",
        )
        joint_names = _names(
            record.get("joint_names"),
            "morphology.joint_names",
            allow_empty=True,
        )
        actuator_names = _names(record.get("actuator_names"), "morphology.actuator_names")

        mujoco = _required_mapping(record.get("mujoco"), "morphology.mujoco")
        entrypoint = _safe_relative_path(
            mujoco.get("entrypoint"), "morphology.mujoco.entrypoint"
        )
        simulation_entrypoint_value = mujoco.get("simulation_entrypoint")
        simulation_entrypoint = (
            _safe_relative_path(
                simulation_entrypoint_value,
                "morphology.mujoco.simulation_entrypoint",
            )
            if simulation_entrypoint_value is not None
            else None
        )

        reset = _required_mapping(mujoco.get("reset"), "morphology.mujoco.reset")
        reset_policy: str | None = None
        reset_keyframe: str | None = None
        reset_qpos: tuple[float, ...] | None = None
        reset_qvel: tuple[float, ...] | None = None
        if "policy" in reset:
            if set(reset) != {"policy"} or reset.get("policy") != "model_default":
                raise DirectMuJoCoConfigurationError(
                    "morphology.mujoco.reset.policy must be 'model_default' without extra fields"
                )
            reset_policy = "model_default"
        elif "keyframe" in reset:
            if set(reset) != {"keyframe"}:
                raise DirectMuJoCoConfigurationError(
                    "morphology.mujoco.reset.keyframe cannot be combined with extra fields"
                )
            reset_keyframe = _required_text(reset.get("keyframe"), "morphology.mujoco.reset.keyframe")
        elif "qpos" in reset:
            if set(reset) not in ({"qpos"}, {"qpos", "qvel"}):
                raise DirectMuJoCoConfigurationError(
                    "morphology.mujoco.reset.qpos accepts only the optional qvel field"
                )
            reset_qpos = _number_list(reset.get("qpos"), "morphology.mujoco.reset.qpos")
            reset_qvel = _number_list(reset.get("qvel"), "morphology.mujoco.reset.qvel") if "qvel" in reset else None
        else:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.reset must be policy:model_default, keyframe, or qpos"
            )

        frames = _required_mapping(mujoco.get("frames"), "morphology.mujoco.frames")
        body_names = _names(
            frames.get("body_names"),
            "morphology.mujoco.frames.body_names",
            allow_empty=True,
        )
        site_names = _names(
            frames.get("site_names"),
            "morphology.mujoco.frames.site_names",
            allow_empty=True,
        )
        if not body_names and not site_names:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.frames must declare at least one body_names or site_names entry"
            )

        sensor_value = mujoco.get("sensor_names", record.get("sensor_names"))
        sensor_names = _names(
            sensor_value,
            "morphology.mujoco.sensor_names",
            allow_empty=True,
        )

        render = _required_mapping(mujoco.get("render"), "morphology.mujoco.render")
        render_camera = _required_text(render.get("camera"), "morphology.mujoco.render.camera")
        render_width = _positive_int(render.get("width"), "morphology.mujoco.render.width")
        render_height = _positive_int(render.get("height"), "morphology.mujoco.render.height")
        render_fps = _positive_int(render.get("fps"), "morphology.mujoco.render.fps")
        mujoco_version = render.get("mujoco_version", mujoco.get("version"))
        if mujoco_version is not None:
            mujoco_version = _required_text(mujoco_version, "morphology.mujoco.version")

        validation = mujoco.get("validation", record.get("direct_mujoco_validation"))
        if validation is not None and not isinstance(validation, Mapping):
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.validation must be an object when supplied"
            )
        validation = validation or {}
        declarations = validation.get(
            "measurements",
            mujoco.get("measurements", record.get("measurements")),
        )
        measurement_declarations = _declarations(declarations, "morphology.measurements")

        root = Path(asset_root).expanduser().resolve()
        if not root.is_dir():
            raise DirectMuJoCoConfigurationError(f"direct MuJoCo asset_root is not a directory: {root}")
        return cls(
            record_id=record_id,
            record_version=record_version,
            robot_model_id=robot_model_id,
            robot_configuration_id=robot_configuration_id,
            asset_root=root,
            entrypoint=entrypoint,
            joint_names=joint_names,
            actuator_names=actuator_names,
            body_names=body_names,
            site_names=site_names,
            sensor_names=sensor_names,
            reset_policy=reset_policy,
            reset_keyframe=reset_keyframe,
            reset_qpos=reset_qpos,
            reset_qvel=reset_qvel,
            render_camera=render_camera,
            render_width=render_width,
            render_height=render_height,
            render_fps=render_fps,
            mujoco_version=mujoco_version,
            measurement_declarations=measurement_declarations,
            simulation_entrypoint=simulation_entrypoint,
        )

    def bind_task(self, task: DirectMuJoCoTaskConfig) -> "DirectMuJoCoLibraryConfig":
        """Bind one declared task scene and its public observation projection."""

        if not isinstance(task, DirectMuJoCoTaskConfig):
            raise DirectMuJoCoConfigurationError("direct MuJoCo task binding requires DirectMuJoCoTaskConfig")
        if (
            task.robot_configuration_id is not None
            and task.robot_configuration_id != self.robot_configuration_id
        ):
            raise DirectMuJoCoConfigurationError(
                "task.robot_configuration_id does not match the morphology record"
            )
        entrypoint = task.scene_entrypoint or self.simulation_entrypoint or self.entrypoint
        return replace(
            self,
            entrypoint=_safe_relative_path(entrypoint, "task.scene_entrypoint"),
            body_names=tuple(dict.fromkeys((*self.body_names, *task.body_names))),
            site_names=tuple(dict.fromkeys((*self.site_names, *task.site_names))),
            sensor_names=tuple(dict.fromkeys((*self.sensor_names, *task.sensor_names))),
            measurement_declarations=(
                *self.measurement_declarations,
                *task.measurement_declarations,
            ),
            bound_task=task,
        )

    @property
    def model_path(self) -> Path:
        """Return the fixed MJCF entrypoint after safe asset-root resolution."""

        candidate = (self.asset_root / self.entrypoint).resolve()
        try:
            candidate.relative_to(self.asset_root)
        except ValueError as exc:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.entrypoint escapes the configured asset_root"
            ) from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise DirectMuJoCoConfigurationError(
                f"morphology.mujoco.entrypoint does not resolve to a regular file: {candidate}"
            )
        return candidate


def load_morphology_record(value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], Path | None]:
    """Load a mapping or JSON record and return it with its source path."""

    if isinstance(value, Mapping):
        return dict(value), None
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise DirectMuJoCoConfigurationError(f"morphology record is not a regular file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DirectMuJoCoConfigurationError(f"could not read morphology record: {path}") from exc
    if not isinstance(raw, Mapping):
        raise DirectMuJoCoConfigurationError("morphology record JSON must contain an object")
    return dict(raw), path


__all__ = [
    "DirectMuJoCoConfigurationError",
    "DirectMuJoCoLibraryConfig",
    "DirectMuJoCoTaskConfig",
    "load_direct_mujoco_task_config",
    "load_morphology_record",
]


def load_direct_mujoco_task_config(
    value: Mapping[str, Any] | str | Path,
    *,
    task_id: str | None = None,
) -> DirectMuJoCoTaskConfig:
    """Load one generic task instance or adapter config."""

    return DirectMuJoCoTaskConfig.from_value(value, task_id=task_id)
