# SPDX-License-Identifier: Apache-2.0
"""Small server-side runtime used by an exported AA1 MCP server.

The exported server owns the MCP tool surface.  This module owns the one live
robot instance, the optional task initialisation/recording boundary, and the
read-only observation snapshot exposed to MCP resources.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import mujoco
import numpy as np

from .agent.task_execution import _TaskRecorder, _to_jsonable
from .scene_runtime import apply_initial_state


def _world(driver: Any) -> tuple[mujoco.MjModel, mujoco.MjData]:
    """Return the driver's real MuJoCo model and data pair."""

    model = getattr(driver, "_model", None)
    data = getattr(driver, "_data", None)
    if model is None:
        model = getattr(driver, "model", None)
    if data is None:
        data = getattr(driver, "data", None)
    if not isinstance(model, mujoco.MjModel) or not isinstance(data, mujoco.MjData):
        raise ValueError("driver must expose real MuJoCo MjModel and MjData")
    return model, data


def _same_scene(model: mujoco.MjModel, expected: mujoco.MjModel) -> None:
    """Reject a driver which loaded a different compiled scene."""

    for name in ("nq", "nv", "nu", "nbody", "njnt", "ngeom", "nsite", "ntendon", "names"):
        try:
            actual_value = getattr(model, name)
            expected_value = getattr(expected, name)
        except AttributeError as exc:
            raise ValueError(f"cannot compare scene structure ({name} is unavailable)") from exc
        if actual_value != expected_value:
            raise ValueError(f"driver loaded a different scene ({name} differs)")
    for name in (
        "body_parentid",
        "body_pos",
        "jnt_type",
        "jnt_bodyid",
        "geom_bodyid",
        "geom_type",
        "geom_size",
        "geom_pos",
    ):
        if not np.array_equal(getattr(model, name), getattr(expected, name)):
            raise ValueError(f"driver loaded a different scene ({name} differs)")


def _finite_world(data: mujoco.MjData) -> bool:
    """Return whether the public MuJoCo state is finite."""

    return all(
        np.all(np.isfinite(np.asarray(getattr(data, name))))
        for name in ("time", "qpos", "qvel", "qacc", "ctrl")
    )


def _frame_robot(capture: Any, model: mujoco.MjModel, data: mujoco.MjData) -> None:
    """Aim an offscreen capture at articulated bodies and their ancestors."""

    joints = np.flatnonzero(model.jnt_type != mujoco.mjtJoint.mjJNT_FREE)
    if not len(joints):
        joints = np.arange(model.njnt)
    bodies: set[int] = set()
    for joint in joints:
        body = int(model.jnt_bodyid[joint])
        while body:
            bodies.add(body)
            body = int(model.body_parentid[body])
    if bodies and getattr(capture, "_cam", None) is not None:
        positions = data.xpos[sorted(bodies)]
        lower, upper = positions.min(axis=0), positions.max(axis=0)
        capture._cam.lookat[:] = (lower + upper) / 2
        capture._cam.distance = max(
            float(capture._cam.distance), float(np.linalg.norm(upper - lower)) * 2.5
        )
        if getattr(capture, "_track_free", False):
            capture._track_offset = np.asarray(capture._cam.lookat).copy() - data.qpos[:3]


def _json_snapshot(value: Any) -> Any:
    """Convert a value into detached, finite JSON-compatible Python values."""

    converted = _to_jsonable(value)
    # Round-tripping also detaches nested values returned by custom getters and
    # rejects NaN/Infinity before they can cross the MCP boundary.
    return json.loads(json.dumps(converted, allow_nan=False))


def _public_observations(driver: Any, data: mujoco.MjData) -> dict[str, Any]:
    """Take a detached observation snapshot from MuJoCo and public getters."""

    values: dict[str, Any] = {
        "sim_time_s": float(data.time),
        "qpos": _json_snapshot(np.array(data.qpos, dtype=float, copy=True)),
        "qvel": _json_snapshot(np.array(data.qvel, dtype=float, copy=True)),
    }
    for name in sorted(dir(driver)):
        if not name.startswith("get_"):
            continue
        try:
            method = getattr(driver, name)
        except Exception:
            continue
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (ValueError, TypeError):
            continue
        required = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ]
        requests = [{}] if not required else (
            [{"arm": side} for side in ("left", "right")]
            if [parameter.name for parameter in required] == ["arm"]
            else []
        )
        for request in requests:
            label = name + (f"[{request['arm']}]" if request else "")
            try:
                values[label] = _json_snapshot(method(**request))
            except Exception as exc:  # a failed optional getter stays observable
                values[label] = {"available": False, "error_type": type(exc).__name__}
    return _json_snapshot(values)


def _load_runtime_config() -> dict[str, Any] | None:
    """Load the optional task runtime config selected by the environment."""

    raw_path = os.environ.get("AA1_TASK_RUNTIME_CONFIG", "").strip()
    if not raw_path:
        return None
    config_path = Path(raw_path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ValueError(f"AA1_TASK_RUNTIME_CONFIG does not name a file: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read AA1 task runtime config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("AA1 task runtime config must be a JSON object")
    required = ("scene_path", "initial_state", "output_dir", "record_video")
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"AA1 task runtime config is missing {missing}")
    scene_value = payload["scene_path"]
    output_value = payload["output_dir"]
    if not isinstance(scene_value, str) or not scene_value.strip():
        raise ValueError("AA1 task runtime config scene_path must be a non-empty string")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ValueError("AA1 task runtime config output_dir must be a non-empty string")
    if not isinstance(payload["initial_state"], dict):
        raise ValueError("AA1 task runtime config initial_state must be an object")
    if not isinstance(payload["record_video"], bool):
        raise ValueError("AA1 task runtime config record_video must be a boolean")

    def resolve_path(value: str) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else config_path.parent / path).resolve()

    return {
        "scene_path": resolve_path(scene_value),
        "initial_state": copy.deepcopy(payload["initial_state"]),
        "output_dir": resolve_path(output_value),
        "record_video": payload["record_video"],
        "config_path": config_path,
    }


class ExportRuntime:
    """Own one lazily built generated robot for one exported MCP session."""

    def __init__(self, builder: Callable[[], Any]) -> None:
        if not callable(builder):
            raise TypeError("ExportRuntime builder must be callable")
        self._builder = builder
        self._config = _load_runtime_config()
        self._robot: Any | None = None
        self._model: mujoco.MjModel | None = None
        self._data: mujoco.MjData | None = None
        self._capture: Any | None = None
        self._build_attempted = False
        self._build_error: BaseException | None = None
        self._closed = False
        self._sim_time_start: float | None = None
        self._last_observed_time: float | None = None
        self._errors: list[str] = []
        self._report: dict[str, Any] | None = None

    @property
    def robot(self) -> Any:
        """Lazily construct and initialise the one persistent robot instance."""

        if self._closed:
            raise RuntimeError("export runtime is closed")
        # A failed scene/state initialisation must stay failed.  The robot is
        # assigned before that setup so ``close()`` can still release it, but
        # callers must never receive that partially initialised instance.
        if self._build_error is not None:
            raise RuntimeError("export runtime robot build failed") from self._build_error
        if self._robot is not None:
            return self._robot
        if self._build_attempted:
            raise RuntimeError("export runtime robot is unavailable")

        self._build_attempted = True
        try:
            robot = self._builder()
            model, data = _world(robot)
            self._robot = robot
            self._model, self._data = model, data
            if self._config is not None:
                expected = mujoco.MjModel.from_xml_path(str(self._config["scene_path"]))
                try:
                    _same_scene(model, expected)
                finally:
                    del expected
                apply_initial_state(
                    model,
                    data,
                    {"initial_state": copy.deepcopy(self._config["initial_state"])},
                )
                if not _finite_world(data):
                    raise ValueError("initial simulation state is not finite")
            if not _finite_world(data):
                raise ValueError("simulation state is not finite")
            self._sim_time_start = float(data.time)
            self._last_observed_time = self._sim_time_start
            if self._config is not None and self._config["record_video"]:
                self._install_capture(robot, model, data)
            return robot
        except BaseException as exc:
            self._build_error = exc
            self._append_error("runtime setup", exc)
            raise

    def _install_capture(
        self, robot: Any, model: mujoco.MjModel, data: mujoco.MjData
    ) -> None:
        capture = None
        try:
            capture = _TaskRecorder(model, data, capture_every=16)
            self._capture = capture
            _frame_robot(capture, model, data)
            capture.snapshot()
        except Exception as exc:  # rendering is optional on headless hosts
            if capture is not None:
                try:
                    capture.uninstall()
                except Exception:
                    pass
            self._capture = None
            self._errors.append(f"recording setup: {type(exc).__name__}: {exc}")

    def observe(self) -> dict[str, Any]:
        """Return a detached, read-only public state snapshot."""

        robot = self.robot
        model, data = _world(robot)
        if model is not self._model or data is not self._data:
            raise RuntimeError("driver replaced the persistent MuJoCo model or data")
        if not _finite_world(data):
            raise RuntimeError("simulation state is not finite")
        sim_time = float(data.time)
        if self._last_observed_time is not None and sim_time < self._last_observed_time:
            raise RuntimeError("simulation time moved backwards")
        self._last_observed_time = sim_time
        if self._capture is not None:
            # Preserve the endpoint of calls shorter than the capture interval.
            self._capture.snapshot()
        return _public_observations(robot, data)

    def _append_error(self, prefix: str, exc: BaseException) -> None:
        self._errors.append(f"{prefix}: {type(exc).__name__}: {exc}")

    def _runtime_report(self, *, video_path: str | None, n_frames: int,
                        sim_time_end: float | None) -> dict[str, Any]:
        return {
            "sim_time_start": self._sim_time_start,
            "sim_time_end": sim_time_end,
            "frame_count": int(n_frames),
            "n_frames": int(n_frames),
            "video_path": video_path,
            "error": "; ".join(self._errors) if self._errors else None,
        }

    def close(self) -> dict[str, Any]:
        """Stop recording, write the optional report, and close the robot."""

        if self._report is not None:
            return copy.deepcopy(self._report)
        self._closed = True

        sim_time_end: float | None = None
        if self._data is not None:
            try:
                candidate = float(self._data.time)
                if np.isfinite(candidate):
                    sim_time_end = candidate
            except Exception as exc:
                self._append_error("read final simulation time", exc)

        n_frames = len(self._capture.frames) if self._capture is not None else 0
        if self._capture is not None:
            try:
                self._capture.uninstall()
            except Exception as exc:
                self._append_error("recording cleanup", exc)

        video_path: str | None = None
        if self._config is not None and self._config["record_video"] and n_frames:
            output_dir = self._config["output_dir"]
            candidate_path = output_dir / "video.mp4"
            try:
                import imageio.v2 as imageio

                output_dir.mkdir(parents=True, exist_ok=True)
                imageio.mimsave(
                    str(candidate_path),
                    self._capture.frames,
                    format="FFMPEG",
                    fps=30,
                    codec="libx264",
                    pixelformat="yuv420p",
                )
                if candidate_path.is_file() and candidate_path.stat().st_size:
                    video_path = str(candidate_path)
            except Exception as exc:
                self._append_error("write video", exc)

        if self._robot is not None:
            close_method = getattr(self._robot, "close", None)
            if callable(close_method):
                try:
                    close_method()
                except Exception as exc:
                    self._append_error("close robot", exc)

        report = self._runtime_report(
            video_path=video_path, n_frames=n_frames, sim_time_end=sim_time_end
        )
        if self._config is not None:
            try:
                output_dir = self._config["output_dir"]
                output_dir.mkdir(parents=True, exist_ok=True)
                report_path = output_dir / "runtime_report.json"
                report_path.write_text(
                    json.dumps(report, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                self._append_error("write runtime report", exc)
                report = self._runtime_report(
                    video_path=video_path, n_frames=n_frames, sim_time_end=sim_time_end
                )
        self._report = copy.deepcopy(report)
        return copy.deepcopy(report)

    @asynccontextmanager
    async def lifespan(self, server: Any):
        """FastMCP lifespan hook which always closes the exported runtime."""

        del server
        try:
            yield {}
        finally:
            self.close()


__all__ = ["ExportRuntime"]
