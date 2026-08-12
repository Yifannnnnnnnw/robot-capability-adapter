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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DirectMuJoCoLibraryConfig:
    """Resolved, robot-independent view of one morphology Library record.

    The canonical record extension consumed here is:

    ``mujoco.entrypoint``
        Relative path to the canonical MJCF entrypoint under ``asset_root``.
    ``mujoco.reset``
        Exactly one of ``keyframe`` or full-model ``qpos``; optional ``qvel``
        accompanies a qpos reset.
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
    reset_keyframe: str | None
    reset_qpos: tuple[float, ...] | None
    reset_qvel: tuple[float, ...] | None
    render_camera: str
    render_width: int
    render_height: int
    render_fps: int
    mujoco_version: str | None = None

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
        joint_names = _names(record.get("joint_names"), "morphology.joint_names")
        actuator_names = _names(record.get("actuator_names"), "morphology.actuator_names")
        if len(joint_names) != len(actuator_names):
            raise DirectMuJoCoConfigurationError(
                "morphology.actuator_names must have the same length as morphology.joint_names"
            )

        mujoco = _required_mapping(record.get("mujoco"), "morphology.mujoco")
        entrypoint = _required_text(mujoco.get("entrypoint"), "morphology.mujoco.entrypoint")
        if "\\" in entrypoint or Path(entrypoint).is_absolute() or ".." in Path(entrypoint).parts:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.entrypoint must be a safe relative path"
            )

        reset = _required_mapping(mujoco.get("reset"), "morphology.mujoco.reset")
        has_keyframe = "keyframe" in reset
        has_qpos = "qpos" in reset
        if has_keyframe == has_qpos:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.reset must contain exactly one of keyframe or qpos"
            )
        reset_keyframe = _required_text(reset.get("keyframe"), "morphology.mujoco.reset.keyframe") if has_keyframe else None
        reset_qpos = _number_list(reset.get("qpos"), "morphology.mujoco.reset.qpos") if has_qpos else None
        reset_qvel = _number_list(reset.get("qvel"), "morphology.mujoco.reset.qvel") if "qvel" in reset else None
        if reset_qvel is not None and reset_qpos is None:
            raise DirectMuJoCoConfigurationError(
                "morphology.mujoco.reset.qvel is only valid with morphology.mujoco.reset.qpos"
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
        sensor_names = _names(sensor_value, "morphology.mujoco.sensor_names")

        render = _required_mapping(mujoco.get("render"), "morphology.mujoco.render")
        render_camera = _required_text(render.get("camera"), "morphology.mujoco.render.camera")
        render_width = _positive_int(render.get("width"), "morphology.mujoco.render.width")
        render_height = _positive_int(render.get("height"), "morphology.mujoco.render.height")
        render_fps = _positive_int(render.get("fps"), "morphology.mujoco.render.fps")
        mujoco_version = render.get("mujoco_version", mujoco.get("version"))
        if mujoco_version is not None:
            mujoco_version = _required_text(mujoco_version, "morphology.mujoco.version")

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
            reset_keyframe=reset_keyframe,
            reset_qpos=reset_qpos,
            reset_qvel=reset_qvel,
            render_camera=render_camera,
            render_width=render_width,
            render_height=render_height,
            render_fps=render_fps,
            mujoco_version=mujoco_version,
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
    "load_morphology_record",
]
