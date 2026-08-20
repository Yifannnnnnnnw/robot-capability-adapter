"""Persistent candidate worker used by one ReAct-controlled Task Demo trial."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, TextIO

import mujoco

from autoadapter2.driver_synthesis import validate_explicit_capability_methods

from .session import TrackedMuJoCoSession, apply_framework_reset
from .worker import _bounded_log, _candidate_runtime_boundary, _load_candidate


def _send(stream: TextIO, value: Mapping[str, Any]) -> None:
    stream.write(json.dumps(dict(value), ensure_ascii=True) + "\n")
    stream.flush()


def _read_command(stream: TextIO) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise RuntimeError("controller protocol ended before finish")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("controller command must be an object")
    return value


def execute_react_session(
    payload: Mapping[str, Any],
    *,
    command_stream: TextIO,
    protocol_stream: TextIO,
) -> dict[str, Any]:
    """Keep one candidate and canonical MuJoCo session alive across tool calls."""

    driver_path = Path(str(payload["driver_path"])).resolve()
    scene_path = Path(str(payload["scene_path"])).resolve()
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    logs = io.StringIO()
    frames: list[Any] = []
    render_value = payload.get("render", {})
    render = dict(render_value) if isinstance(render_value, Mapping) else {}
    renderer = None
    video_error = None
    if render.get("enabled"):
        try:
            renderer = mujoco.Renderer(
                model,
                height=int(render.get("height", 120)),
                width=int(render.get("width", 160)),
            )
        except Exception as exc:
            video_error = f"renderer initialization failed: {type(exc).__name__}: {exc}"

    def capture_frame() -> None:
        nonlocal video_error
        if renderer is None or video_error is not None:
            return
        try:
            renderer.update_scene(data, camera=render.get("camera", -1))
            frames.append(renderer.render().copy())
        except Exception as exc:
            video_error = f"frame capture failed: {type(exc).__name__}: {exc}"

    tracker = TrackedMuJoCoSession(
        mujoco=mujoco,
        model=model,
        data=data,
        max_steps=int(payload.get("max_steps", 10000)),
        max_sim_time_s=float(payload.get("max_sim_time_s", 20.0)),
        sample_hz=float(payload.get("sample_hz", 20.0)),
        capture_frame=capture_frame if render.get("enabled") else None,
    )
    method_invoked = False
    successful_method_invocations = 0
    capability_errors: list[dict[str, Any]] = []
    fatal_exception = None
    protocol_completed = False
    capability_methods = tuple(str(item) for item in payload["capability_methods"])

    try:
        with tracker, contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            with _candidate_runtime_boundary():
                module = _load_candidate(driver_path)
                build = getattr(module, "build", None)
                if not callable(build):
                    raise RuntimeError("candidate driver.py does not expose build()")
                driver = build(model=model, data=data)
                validate_explicit_capability_methods(driver.__class__, capability_methods)
                apply_framework_reset(mujoco, model, data, payload.get("reset"))
                tracker.reset_evidence()
                capture_frame()
                _send(protocol_stream, {"type": "ready"})

                while True:
                    command = _read_command(command_stream)
                    command_type = command.get("type")
                    if command_type == "finish":
                        protocol_completed = True
                        break
                    if command_type != "invoke":
                        raise ValueError("unsupported controller command")
                    method_name = command.get("method_name")
                    arguments = command.get("arguments")
                    if method_name not in capability_methods:
                        _send(
                            protocol_stream,
                            {
                                "type": "observation",
                                "ok": False,
                                "error": {"code": "UNAUTHORIZED_CAPABILITY"},
                            },
                        )
                        continue
                    if not isinstance(arguments, Mapping):
                        _send(
                            protocol_stream,
                            {
                                "type": "observation",
                                "ok": False,
                                "error": {"code": "INVALID_ARGUMENTS"},
                            },
                        )
                        continue

                    method_invoked = True
                    start_steps = tracker.step_count
                    try:
                        method = getattr(driver, str(method_name))
                        method(**dict(arguments))
                    except Exception as exc:
                        capability_errors.append(
                            {
                                "method_name": method_name,
                                "type": type(exc).__name__,
                                "message": str(exc)[:1000],
                                "traceback": traceback.format_exc(limit=8)[-6000:],
                            }
                        )
                        _send(
                            protocol_stream,
                            {
                                "type": "observation",
                                "ok": False,
                                "error": {"code": "CAPABILITY_EXCEPTION"},
                                "sim_step_count": tracker.step_count,
                            },
                        )
                        continue

                    successful_method_invocations += 1
                    _send(
                        protocol_stream,
                        {
                            "type": "observation",
                            "ok": True,
                            "sim_step_count": tracker.step_count,
                            "steps_added": tracker.step_count - start_steps,
                        },
                    )
                tracker.finish()
    except Exception as exc:
        fatal_exception = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "traceback": traceback.format_exc(limit=8)[-6000:],
        }
        try:
            tracker.finish()
        except Exception:
            pass
    finally:
        if renderer is not None:
            renderer.close()

    video = {
        "requested": bool(render.get("enabled")),
        "frame_count": len(frames),
        "complete": False,
        "error": video_error,
    }
    if render.get("enabled") and video_error is None:
        try:
            from .video import encode_rgb_video, inspect_video

            output_path = Path(str(payload["video_path"])).resolve()
            encode_rgb_video(frames, output_path, fps=float(render.get("fps", 10.0)))
            inspection = inspect_video(output_path)
            video.update(asdict(inspection))
            video["path"] = str(output_path)
        except Exception as exc:
            video["error"] = f"video encoding failed: {type(exc).__name__}: {exc}"

    return {
        "worker_completed": True,
        "controller_protocol_completed": protocol_completed,
        "method_invoked": method_invoked,
        "successful_method_invocations": successful_method_invocations,
        "canonical_model_data": tracker.step_count > 0,
        "candidate_exception": fatal_exception,
        "capability_errors": capability_errors,
        "candidate_log": _bounded_log(logs.getvalue()),
        "physical_evidence": tracker.evidence(),
        "video": video,
    }


def main() -> int:
    try:
        first_line = sys.stdin.readline()
        payload = json.loads(first_line)
        if not isinstance(payload, dict):
            raise ValueError("worker payload must be an object")
        result = execute_react_session(
            payload,
            command_stream=sys.stdin,
            protocol_stream=sys.stdout,
        )
    except Exception as exc:
        result = {
            "worker_completed": False,
            "worker_error": {
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        }
    _send(sys.stdout, {"type": "final", "result": result})
    return 0 if result.get("worker_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
