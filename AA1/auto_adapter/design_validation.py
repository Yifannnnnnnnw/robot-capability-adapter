# SPDX-License-Identifier: Apache-2.0
"""Fresh-process validation for a task-grounded capability design.

The parent process owns the case suite and starts one clean worker for every
case. A worker stages the current candidate, loads the prepared scene, runs
the declared method against one MuJoCo model/data pair, records the actual
physics samples, and evaluates those samples with the shared DESIGN runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .design_measurements import (
    capture_sample,
    evaluate_measurements,
    validate_measurement,
)
from .scene_runtime import apply_initial_state, load_scene_cases


def _json(value: Any) -> Any:
    """Return a JSON-safe value, including numpy values from MuJoCo."""
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {str(key): _json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(child) for child in value]
    try:
        return _json(value.item())
    except Exception:  # noqa: BLE001
        pass
    try:
        return [_json(child) for child in value.tolist()]
    except Exception:  # noqa: BLE001
        return str(value)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _case_id(case: Mapping[str, Any], index: int = 0) -> str:
    value = case.get("case_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scene_cases[{index}].case_id must be non-empty text")
    return value.strip()


def _capability_map(design: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("design.capabilities must be a non-empty array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(capabilities):
        capability = _mapping(raw, f"design.capabilities[{index}]")
        capability_id = capability.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ValueError(
                f"design.capabilities[{index}].capability_id must be non-empty text"
            )
        if capability_id in result:
            raise ValueError(f"duplicate capability_id {capability_id!r}")
        result[capability_id] = capability
    return result


def _capability_for_case(
    design: Mapping[str, Any], case: Mapping[str, Any]
) -> Mapping[str, Any]:
    capability_id = case.get("capability_id")
    capabilities = _capability_map(design)
    capability = capabilities.get(capability_id)
    if capability is None:
        raise ValueError(
            f"case {_case_id(case)!r} references unknown capability_id "
            f"{capability_id!r}"
        )
    return capability


def _method_name(capability: Mapping[str, Any], case: Mapping[str, Any]) -> str:
    value = capability.get("method_name")
    if not isinstance(value, str) or not value.strip() or not value.isidentifier():
        raise ValueError(
            f"capability {capability.get('capability_id')!r} has no valid method_name"
        )
    return value


def _execution(case: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(case.get("execution"), "case.execution")
    for key in ("max_sim_time_s", "wall_timeout_s"):
        value = execution.get(key)
        if isinstance(value, bool):
            raise ValueError(f"case.execution.{key} must be positive")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"case.execution.{key} must be finite") from None
        if not math.isfinite(number) or number <= 0.0:
            raise ValueError(f"case.execution.{key} must be positive")
    return dict(execution)


def _cases(suite: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = suite.get("cases")
    if not isinstance(raw, list) or not raw:
        raise ValueError("scene-case suite requires a non-empty cases array")
    return [
        dict(_mapping(case, f"scene_cases[{index}]"))
        for index, case in enumerate(raw)
    ]


def _scene_paths(value: Any, suite: Mapping[str, Any]) -> dict[str, Path]:
    """Resolve the exact scene-id to prepared-MJCF map supplied by the host."""
    if not isinstance(value, Mapping):
        raise ValueError("scene_paths must be a mapping keyed by scene id")
    scenes = suite.get("scenes")
    if not isinstance(scenes, Mapping) or not scenes:
        raise ValueError("scene-case suite requires a scenes mapping")
    paths: dict[str, Path] = {}
    for scene_id in scenes:
        if scene_id not in value:
            raise ValueError(f"scene_paths is missing scene {scene_id!r}")
        raw_path = value[scene_id]
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            raise ValueError(f"scene_paths[{scene_id!r}] must be a path")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"prepared scene for {scene_id!r} does not exist: {path}"
            )
        paths[str(scene_id)] = path
    return paths


def _criterion_for_measurement(
    design: Mapping[str, Any],
    case: Mapping[str, Any],
    measurement: Mapping[str, Any],
) -> Mapping[str, Any]:
    capability = _capability_for_case(design, case)
    criteria = capability.get("criteria")
    index = measurement.get("criterion_index")
    if (
        not isinstance(criteria, list)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(criteria)
        or not isinstance(criteria[index], Mapping)
    ):
        raise ValueError(
            f"case {_case_id(case)!r} has invalid criterion_index {index!r}"
        )
    return criteria[index]


def _finite_state(model: Any, data: Any) -> bool:
    import numpy as np  # noqa: PLC0415

    arrays = (data.qpos, data.qvel, data.act, data.ctrl, data.qacc)
    return bool(
        all(np.all(np.isfinite(array)) for array in arrays)
        and math.isfinite(float(data.time))
    )


class _Recorder:
    """Record and render the same model/data pair used by the candidate."""

    def __init__(
        self,
        model: Any,
        data: Any,
        execution: Mapping[str, Any],
        output: Path,
    ) -> None:
        import numpy as np  # noqa: PLC0415
        import mujoco  # noqa: PLC0415

        self.model = model
        self.data = data
        self.mujoco = mujoco
        self.max_sim = float(execution["max_sim_time_s"])
        max_steps = execution.get("max_steps", 0)
        try:
            max_steps = int(max_steps)
        except (TypeError, ValueError):
            raise ValueError("case.execution.max_steps must be an integer") from None
        if max_steps < 0:
            raise ValueError("case.execution.max_steps must be non-negative")
        timestep = float(model.opt.timestep)
        if not math.isfinite(timestep) or timestep <= 0:
            raise ValueError("model timestep must be positive and finite")
        self.max_steps = max_steps or max(
            1, int(math.ceil(self.max_sim / timestep))
        )
        self.samples: list[dict[str, Any]] = [_json(capture_sample(model, data))]
        self.initial_time = float(data.time)
        self.initial_qpos = np.array(data.qpos, copy=True)
        self.initial_qvel = np.array(data.qvel, copy=True)
        self.original_step: Any = None
        self.step_count = 0
        self.errors: list[str] = []
        self.frames: list[Any] = []
        self.output = output
        self.renderer: Any = None
        fps = float(execution.get("video_fps", 10.0))
        self.capture_every = max(
            1, int(round(1.0 / max(timestep * fps, 1e-12)))
        )
        try:
            width = int(execution.get("video_width", 320))
            height = int(execution.get("video_height", 240))
            self.renderer = mujoco.Renderer(model, height=height, width=width)
            self._capture_frame(0)
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"renderer initialization failed: {exc}")
            self.renderer = None

    def _capture_frame(self, step_count: int) -> None:
        if self.renderer is None or (step_count and step_count % self.capture_every):
            return
        try:
            self.renderer.update_scene(self.data)
            frame = self.renderer.render()
            if getattr(frame, "ndim", 0) != 3 or frame.shape[2] < 3:
                raise RuntimeError("renderer returned a non-image frame")
            self.frames.append(frame[:, :, :3].copy())
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"frame capture failed: {exc}")

    def install(self) -> None:
        if self.original_step is not None:
            raise RuntimeError("step recorder is already installed")
        self.original_step = self.mujoco.mj_step

        def tracked_step(model: Any, data: Any, nstep: int = 1) -> None:
            if model is not self.model or data is not self.data:
                raise RuntimeError(
                    "candidate attempted to step a different model/data pair"
                )
            try:
                count = int(nstep)
            except (TypeError, ValueError):
                raise ValueError("mj_step nstep must be an integer") from None
            if count <= 0:
                raise ValueError("mj_step nstep must be positive")
            for _ in range(count):
                if self.step_count >= self.max_steps:
                    raise RuntimeError("case exceeded max_steps")
                elapsed = float(self.data.time) - self.initial_time
                timestep = float(self.model.opt.timestep)
                if elapsed + timestep > self.max_sim + 1e-9:
                    raise RuntimeError("case exceeded max_sim_time_s")
                if not _finite_state(self.model, self.data):
                    raise RuntimeError("non-finite state before physics step")
                self.original_step(self.model, self.data, 1)
                self.step_count += 1
                if not _finite_state(self.model, self.data):
                    raise RuntimeError("non-finite state after physics step")
                self.mujoco.mj_forward(self.model, self.data)
                self.samples.append(
                    _json(capture_sample(self.model, self.data))
                )
                self._capture_frame(self.step_count)

        self.mujoco.mj_step = tracked_step

    def uninstall(self) -> None:
        if self.original_step is not None:
            self.mujoco.mj_step = self.original_step
            self.original_step = None

    def finish(self) -> dict[str, Any]:
        if self.renderer is not None:
            try:
                self.renderer.close()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"renderer close failed: {exc}")
        info: dict[str, Any] = {
            "path": str(self.output),
            "ok": False,
            "frame_count": len(self.frames),
            "errors": list(self.errors),
        }
        if not self.frames:
            info["errors"].append("no video frames were captured")
            return info
        try:
            import imageio.v2 as imageio  # noqa: PLC0415

            self.output.parent.mkdir(parents=True, exist_ok=True)
            imageio.mimsave(str(self.output), self.frames, fps=self.output_fps)
            usable = self.output.is_file() and self.output.stat().st_size > 0
            if usable and not info["errors"]:
                info["ok"] = True
            elif not usable:
                info["errors"].append("video output was not usable")
        except Exception as exc:  # noqa: BLE001
            info["errors"].append(f"video encoding failed: {exc}")
        return info

    @property
    def output_fps(self) -> float:
        return max(
            1.0,
            1.0
            / max(float(self.model.opt.timestep) * self.capture_every, 1e-12),
        )


def _load_driver(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import driver from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    case = _mapping(payload.get("case"), "worker.case")
    design = _mapping(payload.get("design"), "worker.design")
    output = Path(str(payload["output_dir"])).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    case_id = _case_id(case)
    result: dict[str, Any] = {
        "test": case_id,
        "case_id": case_id,
        "capability_id": case.get("capability_id"),
        "method_name": None,
        "ok": False,
        "error": None,
        "detail": "",
        "errors": [],
        "physics_steps": 0,
        "sim_elapsed_s": 0.0,
        "metrics": {"measurements": []},
        "video": {
            "ok": False,
            "path": str(output / "video.mp4"),
            "errors": [],
        },
        "paths": {
            "output_dir": str(output),
            "result": str(output / "result.json"),
        },
    }
    stage = "preparation"
    recorder: _Recorder | None = None
    try:
        driver_source = Path(str(payload["driver_path"])).expanduser().resolve()
        scene_source = Path(str(payload["scene_path"])).expanduser().resolve()
        if not driver_source.is_file():
            raise FileNotFoundError(f"driver does not exist: {driver_source}")
        if not scene_source.is_file():
            raise FileNotFoundError(
                f"prepared scene does not exist: {scene_source}"
            )

        target_name = (
            "driver_from_scratch.py"
            if bool(payload.get("from_scratch"))
            else "driver.py"
        )
        target_driver = output / target_name
        shutil.copy2(driver_source, target_driver)
        mjcf_link = output / "mjcf.xml"
        if mjcf_link.exists() or mjcf_link.is_symlink():
            mjcf_link.unlink()
        mjcf_link.symlink_to(scene_source)
        os.chdir(output)

        stage = "build"
        if bool(payload.get("from_scratch")):
            module = _load_driver(
                target_driver, "design_validation_driver_from_scratch"
            )
            robot_cls = getattr(module, "Robot", None)
            build_from_mjcf = getattr(robot_cls, "build_from_mjcf", None)
            if robot_cls is None or not callable(build_from_mjcf):
                raise AttributeError(
                    "scratch driver must expose Robot.build_from_mjcf"
                )
            robot = build_from_mjcf(str(scene_source))
        else:
            module = _load_driver(target_driver, "design_validation_driver")
            build = getattr(module, "build", None)
            if not callable(build):
                raise AttributeError("standard driver must expose build()")
            robot = build()

        model = getattr(robot, "model", getattr(robot, "_model", None))
        data = getattr(robot, "data", getattr(robot, "_data", None))
        if model is None or data is None:
            raise ValueError("constructed driver must expose model and data")
        import mujoco  # noqa: PLC0415

        capability = _capability_for_case(design, case)
        method_name = _method_name(capability, case)
        result["method_name"] = method_name
        request = _mapping(case.get("request"), "case.request")
        execution = _execution(case)
        measurements = case.get("measurements")
        if not isinstance(measurements, list) or not measurements:
            raise ValueError("case.measurements must be a non-empty array")
        stage = "bindings"
        for raw_measurement in measurements:
            measurement = _mapping(raw_measurement, "case.measurements[]")
            criterion = _criterion_for_measurement(design, case, measurement)
            # This checks the actual constructed model's named sites, joints,
            # and geoms as well as the request bindings.
            validate_measurement(model, measurement, criterion, request)

        stage = "initial_state"
        reset = getattr(robot, "reset", None)
        if callable(reset):
            reset()
        apply_initial_state(model, data, dict(case))
        if not _finite_state(model, data):
            raise ValueError("initial state is not finite")

        stage = "driver"
        recorder = _Recorder(model, data, execution, output / "video.mp4")
        recorder.install()
        try:
            method = getattr(robot, method_name, None)
            if not callable(method):
                raise AttributeError(
                    f"driver has no callable capability method {method_name!r}"
                )
            signature = inspect.signature(method)
            request_parameter = signature.parameters.get("request")
            if request_parameter is None or (
                request_parameter.kind is inspect.Parameter.POSITIONAL_ONLY
            ):
                raise TypeError(f"{method_name} must accept keyword request=...")
            method(request=request)
        finally:
            recorder.uninstall()

        stage = "measurement"
        if not _finite_state(model, data):
            raise ValueError("final state is not finite")
        result["physics_steps"] = recorder.step_count
        result["sim_elapsed_s"] = max(
            0.0, float(data.time) - float(recorder.initial_time)
        )
        result["initial_sample"] = recorder.samples[0]
        result["final_sample"] = recorder.samples[-1]
        result["initial_state"] = recorder.samples[0]
        result["final_state"] = recorder.samples[-1]
        result["video"] = recorder.finish()
        measurements_result = [
            _json(item)
            for item in evaluate_measurements(design, case, recorder.samples)
        ]
        result["metrics"]["measurements"] = measurements_result
        result["measurement_results"] = measurements_result
        result["ok"] = bool(
            all(item.get("ok") is True for item in measurements_result)
            and bool(measurements_result)
            and result["video"].get("ok") is True
        )
        if not result["ok"]:
            if not all(item.get("ok") is True for item in measurements_result):
                result["errors"].append("measurement criteria failed")
            if not result["video"].get("ok"):
                result["errors"].append("video capture failed")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        if recorder is not None:
            try:
                recorder.uninstall()
                result["physics_steps"] = recorder.step_count
                result["sim_elapsed_s"] = max(
                    0.0, float(recorder.data.time) - float(recorder.initial_time)
                )
                result["initial_sample"] = recorder.samples[0]
                result["final_sample"] = recorder.samples[-1]
                result["initial_state"] = recorder.samples[0]
                result["final_state"] = recorder.samples[-1]
                result["video"] = recorder.finish()
            except Exception as video_exc:  # noqa: BLE001
                result["errors"].append(
                    f"video finalization failed: {type(video_exc).__name__}: {video_exc}"
                )

    result["error"] = result["errors"][0] if result["errors"] else None
    result["detail"] = "; ".join(result["errors"]) or "measurement criteria passed"
    # Preparation failures describe an unusable scene/binding/runtime. They
    # are reported separately so the generation repair loop cannot chase them.
    result["validation_error"] = bool(
        stage in {"preparation", "bindings", "initial_state"}
        or result.get("video", {}).get("errors")
    )
    result["repairable"] = not result["validation_error"]
    samples_path = output / "samples.json"
    try:
        samples_path.write_text(
            json.dumps(
                {
                    "initial": result.get("initial_sample"),
                    "final": result.get("final_sample"),
                    "samples": (
                        recorder.samples if recorder is not None else []
                    ),
                },
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        result["paths"]["samples"] = str(samples_path)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"sample write failed: {exc}")
        result["ok"] = False
        result["error"] = result["errors"][0]
    result["error_stage"] = stage if result["errors"] else None
    result["detail"] = "; ".join(result["errors"]) or "measurement criteria passed"
    (output / "result.json").write_text(
        json.dumps(_json(result), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return _json(result)


def _worker_main(payload: Mapping[str, Any]) -> int:
    try:
        result = _worker_result(payload)
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "error": f"worker bootstrap failed: {type(exc).__name__}: {exc}",
            "validation_error": True,
            "repairable": False,
        }
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") is True else 1


def _worker_payload(
    *,
    driver_path: Path,
    design: Mapping[str, Any],
    case: Mapping[str, Any],
    scene_path: Path,
    from_scratch: bool,
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "driver_path": str(driver_path.resolve()),
        "design": _json(design),
        "case": _json(case),
        "scene_path": str(scene_path.resolve()),
        "from_scratch": bool(from_scratch),
        "output_dir": str(output_dir.resolve()),
    }


def validate_design_driver(
    *,
    driver_path: str | Path,
    design: Mapping[str, Any],
    scene_cases_path: str | Path,
    scene_paths: Mapping[str, str | Path],
    from_scratch: bool = False,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Validate each design case in a fresh subprocess."""
    started = time.time()
    driver = Path(driver_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "artifact_type": "design_validation_result",
        "schema_version": "1.0",
        "all_ok": False,
        "tests": [],
        "errors": [],
        "paths": {"output_dir": str(output)},
        "from_scratch": bool(from_scratch),
    }
    try:
        if not driver.is_file():
            raise FileNotFoundError(f"driver does not exist: {driver}")
        case_path = Path(scene_cases_path).expanduser().resolve()
        suite = load_scene_cases(case_path, design=dict(design))
        cases = _cases(suite)
        prepared_paths = _scene_paths(scene_paths, suite)
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "error": f"scene preparation failed: {type(exc).__name__}: {exc}",
                "validation_error": True,
                "repairable": False,
                "duration_sec": max(0.0, time.time() - started),
            }
        )
        return _json(report)

    report["paths"].update(
        {
            "driver": str(driver),
            "scene_cases": str(case_path),
            "scene_paths": {
                key: str(value) for key, value in prepared_paths.items()
            },
        }
    )
    for index, raw_case in enumerate(cases):
        case = dict(raw_case)
        case_label = _case_id(case, index)
        attempt = output / f"attempt-{index + 1:03d}-{uuid.uuid4().hex[:10]}"
        case_dir = attempt / case_label
        case_dir.mkdir(parents=True, exist_ok=False)
        test: dict[str, Any]
        try:
            _capability_for_case(design, case)
            execution = _execution(case)
            scene_id = case.get("scene")
            if not isinstance(scene_id, str) or scene_id not in prepared_paths:
                raise ValueError(
                    f"case {case_label!r} has no prepared scene for scene "
                    f"{scene_id!r}"
                )
            payload = _worker_payload(
                driver_path=driver,
                design=design,
                case=case,
                scene_path=prepared_paths[scene_id],
                from_scratch=from_scratch,
                output_dir=case_dir,
            )
            command = [
                sys.executable,
                "-m",
                "auto_adapter.design_validation",
                "worker",
                json.dumps(payload, allow_nan=False),
            ]
            env = os.environ.copy()
            package_root = str(Path(__file__).resolve().parents[1])
            current_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = package_root + (
                os.pathsep + current_pythonpath if current_pythonpath else ""
            )
            t0 = time.time()
            try:
                completed = subprocess.run(
                    command,
                    cwd=case_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=float(execution["wall_timeout_s"]),
                )
                result_path = case_dir / "result.json"
                if completed.returncode in (0, 1) and result_path.is_file():
                    loaded = json.loads(result_path.read_text(encoding="utf-8"))
                    test = dict(_mapping(loaded, "worker result"))
                else:
                    test = {
                        "test": case_label,
                        "case_id": case_label,
                        "capability_id": case.get("capability_id"),
                        "ok": False,
                        "error": "worker did not produce a current result",
                        "detail": str(
                            completed.stderr or completed.stdout or ""
                        )[-1000:],
                        "errors": ["worker process failed"],
                        "repairable": True,
                    }
                test.setdefault("test", case_label)
                test.setdefault("case_id", case_label)
                test.setdefault("capability_id", case.get("capability_id"))
                test.setdefault("ok", False)
                test["paths"] = {
                    **dict(test.get("paths") or {}),
                    "attempt_dir": str(attempt),
                    "case_dir": str(case_dir),
                }
                test["worker"] = {
                    "returncode": completed.returncode,
                    "duration_sec": max(0.0, time.time() - t0),
                    "stdout_tail": str(completed.stdout or "")[-2000:],
                    "stderr_tail": str(completed.stderr or "")[-2000:],
                }
            except subprocess.TimeoutExpired as exc:
                test = {
                    "test": case_label,
                    "case_id": case_label,
                    "capability_id": case.get("capability_id"),
                    "ok": False,
                    "error": (
                        f"case exceeded wall timeout "
                        f"{float(execution['wall_timeout_s']):.3g}s"
                    ),
                    "detail": "worker terminated after wall timeout",
                    "errors": ["wall timeout"],
                    "repairable": True,
                    "paths": {
                        "attempt_dir": str(attempt),
                        "case_dir": str(case_dir),
                    },
                    "worker": {
                        "timed_out": True,
                        "stdout_tail": str(exc.stdout or "")[-1000:],
                    },
                }
        except Exception as exc:  # noqa: BLE001
            test = {
                "test": case_label,
                "case_id": case_label,
                "capability_id": case.get("capability_id"),
                "ok": False,
                "error": f"case preparation failed: {type(exc).__name__}: {exc}",
                "detail": "case was rejected before candidate execution",
                "errors": ["case preparation failed"],
                "validation_error": True,
                "repairable": False,
                "paths": {
                    "attempt_dir": str(attempt),
                    "case_dir": str(case_dir),
                },
            }
        report["tests"].append(test)

    report["n_passed"] = sum(
        1 for test in report["tests"] if test.get("ok") is True
    )
    report["n_total"] = len(report["tests"])
    preparation_failure = any(
        test.get("validation_error") is True for test in report["tests"]
    )
    if preparation_failure:
        report["validation_error"] = True
        first = next(
            test
            for test in report["tests"]
            if test.get("validation_error") is True
        )
        report["error"] = str(
            first.get("error") or "scene or measurement preparation failed"
        )
    report["all_ok"] = bool(report["tests"]) and (
        report["n_passed"] == report["n_total"] and not report.get("error")
    )
    report["repairable"] = bool(
        not report.get("validation_error") and not report.get("error")
    )
    report["duration_sec"] = max(0.0, time.time() - started)
    return _json(report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("payload")
    args = parser.parse_args(argv)
    if args.command != "worker":
        return 2
    try:
        payload = json.loads(args.payload)
        return _worker_main(_mapping(payload, "worker payload"))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"worker bootstrap failed: {type(exc).__name__}: {exc}",
                    "validation_error": True,
                    "repairable": False,
                }
            )
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["validate_design_driver"]
