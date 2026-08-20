"""One-process execution worker that never receives a private criterion."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import mujoco

from autoadapter2.driver_synthesis import validate_explicit_capability_methods

from .session import TrackedMuJoCoSession, apply_framework_reset


_CANDIDATE_ENV_ALLOWLIST = {
    "PATH",
    "TMPDIR",
    "MUJOCO_GL",
    "DYLD_LIBRARY_PATH",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
}


@contextlib.contextmanager
def _candidate_runtime_boundary() -> Any:
    """Hide private Framework modules and unrelated environment while code runs.

    The worker itself starts from the Framework package, but the submitted module
    executes with only already-loaded trusted skeleton modules exposed.  This is
    the experiment isolation boundary required by the Authority, not a general
    operating-system sandbox.
    """

    import autoadapter2
    import autoadapter2.driver_synthesis
    import autoadapter2.driver_synthesis.skeleton_contract
    import autoadapter2.trusted_skeletons
    import autoadapter2.trusted_skeletons.arm_serial_dls
    import autoadapter2.trusted_skeletons.quadruped_pd_gait
    import autoadapter2.trusted_skeletons.quadruped_position_policy

    allowed_modules = {
        "autoadapter2",
        "autoadapter2.driver_synthesis",
        "autoadapter2.driver_synthesis.skeleton_contract",
        "autoadapter2.trusted_skeletons",
        "autoadapter2.trusted_skeletons.arm_serial_dls",
        "autoadapter2.trusted_skeletons.quadruped_pd_gait",
        "autoadapter2.trusted_skeletons.quadruped_position_policy",
    }
    framework_modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == "autoadapter2" or name.startswith("autoadapter2.")
    }
    package_paths: dict[str, list[str]] = {}
    for name in ("autoadapter2", "autoadapter2.driver_synthesis"):
        module = sys.modules.get(name)
        path = getattr(module, "__path__", None)
        if path is not None:
            package_paths[name] = list(path)
            module.__path__ = []
    for name in tuple(framework_modules):
        if name not in allowed_modules:
            sys.modules.pop(name, None)
    removed_attributes: list[tuple[Any, str, Any]] = []
    for name, module in framework_modules.items():
        if name in allowed_modules or "." not in name:
            continue
        parent_name, attribute = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, attribute, None) is module:
            removed_attributes.append((parent, attribute, module))
            delattr(parent, attribute)

    framework_root = Path(__file__).resolve().parents[2]
    original_sys_path = list(sys.path)
    restricted_sys_path: list[str] = []
    for value in original_sys_path:
        try:
            resolved = Path(value or os.getcwd()).resolve()
        except OSError:
            restricted_sys_path.append(value)
            continue
        if resolved == framework_root or framework_root in resolved.parents:
            continue
        restricted_sys_path.append(value)
    original_environment = dict(os.environ)
    allowed_environment = {
        name: value
        for name, value in original_environment.items()
        if name in _CANDIDATE_ENV_ALLOWLIST
    }
    sys.path[:] = restricted_sys_path
    os.environ.clear()
    os.environ.update(allowed_environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        sys.path[:] = original_sys_path
        for name in tuple(sys.modules):
            if name == "autoadapter2" or name.startswith("autoadapter2."):
                sys.modules.pop(name, None)
        sys.modules.update(framework_modules)
        for parent, attribute, module in removed_attributes:
            setattr(parent, attribute, module)
        for name, path in package_paths.items():
            module = sys.modules.get(name)
            if module is not None:
                module.__path__ = path


def _load_candidate(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("candidate_driver", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot create candidate module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["candidate_driver"] = module
    spec.loader.exec_module(module)
    return module


def _bounded_log(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n<truncated>"


def execute_case(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one method and return trusted observations, not a verdict."""

    driver_path = Path(str(payload["driver_path"])).resolve()
    scene_path = Path(str(payload["scene_path"])).resolve()
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    logs = io.StringIO()
    frames: list[Any] = []
    renderer = None
    video_error = None
    render = payload.get("render", {})
    if isinstance(render, Mapping) and render.get("enabled"):
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
    candidate_exception = None
    return_value_type = None
    canonical_objects = False
    try:
        with tracker, contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            with _candidate_runtime_boundary():
                module = _load_candidate(driver_path)
                build = getattr(module, "build", None)
                if not callable(build):
                    raise RuntimeError("candidate driver.py does not expose build()")
                driver = build(model=model, data=data)
                capability_methods = tuple(payload["capability_methods"])
                validate_explicit_capability_methods(driver.__class__, capability_methods)

                apply_framework_reset(mujoco, model, data, payload.get("reset"))
                tracker.reset_evidence()
                capture_frame()
                method = getattr(driver, str(payload["method_name"]))
                arguments = payload.get("public_arguments", {})
                if not isinstance(arguments, Mapping):
                    raise ValueError("public_arguments must be an object")
                method_invoked = True
                returned = method(**dict(arguments))
                return_value_type = type(returned).__name__
                tracker.finish()
                canonical_objects = tracker.step_count > 0
    except Exception as exc:
        candidate_exception = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
            "traceback": traceback.format_exc(limit=8)[-6000:],
        }
        try:
            tracker.finish()
        except Exception:
            pass
        canonical_objects = tracker.step_count > 0
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
        "method_invoked": method_invoked,
        "canonical_model_data": canonical_objects,
        "candidate_exception": candidate_exception,
        "candidate_return_type": return_value_type,
        "candidate_log": _bounded_log(logs.getvalue()),
        "physical_evidence": tracker.evidence(),
        "video": video,
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("worker payload must be an object")
        result = execute_case(payload)
    except Exception as exc:
        result = {
            "worker_completed": False,
            "worker_error": {
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    return 0 if result.get("worker_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
