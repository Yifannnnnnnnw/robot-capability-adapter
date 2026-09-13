# SPDX-License-Identifier: Apache-2.0
"""Execute an explicitly supplied driver and task in one fresh MuJoCo world."""
from __future__ import annotations

from dataclasses import asdict
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import time

import mujoco
import numpy as np

from auto_adapter.scene_runtime import apply_initial_state
from .recap import run_recap
from .recap_demo import AA1CapabilityAdapter, AA1RecapModel, passed_design
from .task_planner import _FrameCapture, _to_jsonable


def _write_json(path, value):
    path.write_text(json.dumps(_to_jsonable(value), indent=2, allow_nan=False) + "\n")


def _world(driver):
    model = getattr(driver, "_model", None)
    data = getattr(driver, "_data", None)
    if model is None:
        model = getattr(driver, "model", None)
    if data is None:
        data = getattr(driver, "data", None)
    if not isinstance(model, mujoco.MjModel) or not isinstance(data, mujoco.MjData):
        raise ValueError("driver must expose real MuJoCo MjModel and MjData")
    return model, data


def _same_scene(model, expected):
    # Direct structural comparison catches loading a different robot or dropping task objects.
    for name in ("nq", "nv", "nu", "nbody", "njnt", "ngeom", "nsite", "ntendon", "names"):
        if getattr(model, name) != getattr(expected, name):
            raise ValueError(f"driver loaded a different scene ({name} differs)")
    for name in ("body_parentid", "body_pos", "jnt_type", "jnt_bodyid", "geom_bodyid",
                 "geom_type", "geom_size", "geom_pos"):
        if not np.array_equal(getattr(model, name), getattr(expected, name)):
            raise ValueError(f"driver loaded a different scene ({name} differs)")


def _finite_world(data):
    return all(np.all(np.isfinite(getattr(data, name)))
               for name in ("time", "qpos", "qvel", "qacc", "ctrl"))


def _frame_robot(capture, model, data):
    # Some fixed scenes publish a tiny statistic extent that crops the arm.
    # Use articulated bodies and their ancestors, excluding distant static targets.
    joints = np.flatnonzero(model.jnt_type != mujoco.mjtJoint.mjJNT_FREE)
    if not len(joints):
        joints = np.arange(model.njnt)
    bodies = set()
    for joint in joints:
        body = int(model.jnt_bodyid[joint])
        while body:
            bodies.add(body)
            body = int(model.body_parentid[body])
    if bodies:
        positions = data.xpos[sorted(bodies)]
        lower, upper = positions.min(axis=0), positions.max(axis=0)
        capture._cam.lookat[:] = (lower + upper) / 2
        capture._cam.distance = max(float(capture._cam.distance), float(np.linalg.norm(upper - lower)) * 2.5)
        if capture._track_free:
            capture._track_offset = np.asarray(capture._cam.lookat).copy() - data.qpos[:3]


def _public_observations(driver, data):
    values = {"sim_time_s": float(data.time)}
    for name in sorted(dir(driver)):
        if not name.startswith("get_"):
            continue
        method = getattr(driver, name)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (ValueError, TypeError):
            continue
        required = [p for p in signature.parameters.values()
                    if p.default is inspect.Parameter.empty and p.kind in (
                        inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY)]
        requests = [{}] if not required else ([{"arm": side} for side in ("left", "right")]
                                             if [p.name for p in required] == ["arm"] else [])
        for request in requests:
            label = name + (f"[{request['arm']}]" if request else "")
            try:
                observed = _to_jsonable(method(**request))
                # Non-JSON getters do not expose arbitrary internal Python objects.
                json.dumps(observed, allow_nan=False)
                values[label] = observed
            except Exception as exc:
                values[label] = {"available": False, "error_type": type(exc).__name__}
    return values


def run_task(*, driver_path, robot_id, capability_design, validation_suite,
             validation_report, task_description, scene_path, initial_state, parameters,
             output_dir, model, provider="holistic", region="us-east-1", max_tokens=6000,
             from_scratch=False, required_capabilities=(), model_client=None):
    """Run a supplied task without generating, repairing, or reselecting driver capabilities.

    ``model_client`` is an injection point for explicitly named test fixtures. Real runs
    construct AA1RecapModel with the supplied provider configuration.
    """
    import imageio.v2 as imageio

    started = time.monotonic()
    source = Path(driver_path).resolve()
    scene = Path(scene_path).resolve()
    destination = Path(output_dir).resolve()
    # Do not overwrite previous inputs, recordings or results, including a generation workspace.
    destination.mkdir(parents=True, exist_ok=False)
    trace_path = destination / "trace.jsonl"
    model_trace_path = destination / "model_turns.jsonl"
    video_path = destination / "video.mp4"
    trace_path.write_text("")
    model_trace_path.write_text("")
    report = {"ok": False, "status": "UNAVAILABLE", "robot_id": robot_id,
              "task_description": task_description,
              "controller": "auto_adapter.agent.vendor.recap.chatbot.chatbot",
              "controller_source_commit": "2fb112ffad685c7c6f7de86d5487ecca6f566fcc",
              "controller_result": None, "physical_task_success": None,
              "scope": "diagnostic task execution; no independent task predicate evaluated",
              "driver_path": str(source), "scene_path": str(scene), "from_scratch": from_scratch,
              "capability_whitelist": [], "required_capabilities": list(required_capabilities),
              "tool_call_log": [], "sim_time_start": None, "sim_time_end": None,
              "sim_advanced": False, "n_frames": 0, "video_path": None,
              "trace_path": str(trace_path), "model_trace_path": str(model_trace_path),
              "report_path": str(destination / "task_report.json")}
    driver = capture = None
    original_cwd, original_path = Path.cwd(), list(sys.path)
    previous_driver_module = sys.modules.get("driver")
    controller_started = False
    try:
        if not source.is_file() or not scene.is_file():
            raise ValueError("driver_path and scene_path must both exist")
        if capability_design.get("robot_configuration_id") != robot_id:
            raise ValueError("capability design robot_configuration_id does not match robot_id")
        for label, artifact in (("validation suite", validation_suite), ("validation report", validation_report)):
            identity = artifact.get("robot_configuration_id", artifact.get("robot_id"))
            if identity is not None and identity != robot_id:
                raise ValueError(f"{label} robot identity does not match robot_id")
        selected = passed_design(capability_design, validation_suite, validation_report)
        names = [cap["method_name"] for cap in selected["capabilities"]]
        missing = sorted(set(required_capabilities) - set(names))
        if missing:
            raise ValueError(f"required capabilities are missing or not fully validated: {missing}")
        report["capability_whitelist"] = names
        copied_driver = destination / ("driver_from_scratch.py" if from_scratch else "driver.py")
        shutil.copy2(source, copied_driver)
        (destination / "mjcf.xml").symlink_to(scene)
        for filename, value in (("capability_design.json", capability_design),
                                ("validation_suite.json", validation_suite),
                                ("validation_report.json", validation_report)):
            _write_json(destination / filename, value)
        _write_json(destination / "task_inputs.json", {
            "robot_id": robot_id, "task_description": task_description,
            "scene_path": str(scene), "initial_state": initial_state,
            "parameters": parameters, "required_capabilities": list(required_capabilities)})

        expected_model = mujoco.MjModel.from_xml_path(str(scene))
        os.chdir(destination)
        sys.path[:0] = [str(destination), str(source.parent)]
        spec = importlib.util.spec_from_file_location("driver", copied_driver)
        module = importlib.util.module_from_spec(spec)
        sys.modules["driver"] = module
        spec.loader.exec_module(module)
        if from_scratch:
            # MuJoCo resolves XML includes relative to the path supplied to its loader.
            driver = module.Robot.build_from_mjcf(str((destination / "mjcf.xml").resolve()))
        else:
            driver = module.build()
        world_model, world_data = _world(driver)
        _same_scene(world_model, expected_model)
        del expected_model
        if any(not callable(getattr(driver, name, None)) for name in names):
            raise ValueError("driver is missing a method from its validated capability design")
        apply_initial_state(world_model, world_data, {"initial_state": initial_state or {}})
        if not _finite_world(world_data):
            raise ValueError("initial simulation state is not finite")
        report["sim_time_start"] = float(world_data.time)
        capture = _FrameCapture(driver, capture_every=16)
        if capture._mode != "mj_step":
            raise ValueError("real MuJoCo offscreen recording is unavailable")
        _frame_robot(capture, world_model, world_data)
        capture.snapshot()

        def invoke(name, envelope):
            t0, sim_before = time.monotonic(), float(world_data.time)
            native_request = json.loads(json.dumps(envelope["request"], allow_nan=False))
            call = {"tool": name, "request": json.loads(json.dumps(native_request)), "ok": False,
                    "sim_time_before": sim_before, "return_value": None}
            status = "EXECUTED"
            try:
                call["return_value"] = _to_jsonable(getattr(driver, name)(request=native_request))
                json.dumps(call["return_value"], allow_nan=False)
                call["ok"] = True
            except Exception as exc:
                status = "ERROR"
                call.update(error_type=type(exc).__name__, error=str(exc))
                # A non-JSON result cannot prevent recording the diagnostic exception.
                call["return_value"] = None
            try:
                actual_model, actual_data = _world(driver)
                if actual_model is not world_model or actual_data is not world_data:
                    raise RuntimeError("driver replaced the persistent MuJoCo model or data")
                if not _finite_world(world_data) or float(world_data.time) < sim_before:
                    raise RuntimeError("simulation became nonfinite or reset its time")
                observations = _public_observations(driver, world_data)
                capture.snapshot()
            except Exception as exc:
                status = "WORKER_ABORTED"
                call.update(ok=False, error_type=type(exc).__name__, error=str(exc))
                observations = {"available": False}
            sim_after = float(world_data.time)
            call.update(status=status, observations=observations,
                        sim_time_after=sim_after if np.isfinite(sim_after) else None,
                        duration_sec=time.monotonic() - t0)
            report["tool_call_log"].append(call)
            operation = {"status": status, "return_value": call["return_value"]}
            if "error" in call:
                operation.update(error=call["error"], error_type=call["error_type"])
            return {"operation": operation, "observations": observations}

        client = model_client or AA1RecapModel(model=model, provider=provider, region=region,
                                               max_tokens=max_tokens, trace_path=model_trace_path)
        if model_client is not None:
            class RecordedFixtureModel:
                def generate_json(self, *, messages):
                    response = model_client.generate_json(messages=messages)
                    with model_trace_path.open("a") as stream:
                        stream.write(json.dumps({"messages": messages, "response": response}) + "\n")
                    return response
            client = RecordedFixtureModel()
        controller_started = True
        result = run_recap(public_task={"description": task_description, "parameters": parameters or {}},
                           adapter=AA1CapabilityAdapter(selected, invoke), model=client,
                           initial_public_state=_public_observations(driver, world_data),
                           log_dir=destination / "recap")
        report.update(status=result.status, controller_result=asdict(result))
        trace_path.write_text("".join(json.dumps(item, allow_nan=False) + "\n" for item in result.trace))
        report["sim_time_end"] = float(world_data.time) if np.isfinite(world_data.time) else None
        report["sim_advanced"] = any(
            call["sim_time_after"] is not None and call["sim_time_after"] > call["sim_time_before"]
            for call in report["tool_call_log"])
    except Exception as exc:
        report.update(status="RUNTIME_ERROR" if controller_started else "UNAVAILABLE",
                      error_type=type(exc).__name__, error=str(exc))
    finally:
        if capture is not None:
            capture.uninstall()
            report["n_frames"] = len(capture.frames)
            if len(capture.frames) > 1:
                try:
                    imageio.mimsave(str(video_path), capture.frames, format="FFMPEG", fps=30,
                                   codec="libx264", pixelformat="yuv420p")
                    with imageio.get_reader(str(video_path)) as reader:
                        if reader.get_data(0).size and reader.get_data(1).size:
                            report["video_path"] = str(video_path)
                except Exception as exc:
                    report.update(video_error=f"{type(exc).__name__}: {exc}")
        if driver is not None and callable(getattr(driver, "close", None)):
            try:
                driver.close()
            except Exception as exc:
                report["close_error"] = f"{type(exc).__name__}: {exc}"
        os.chdir(original_cwd)
        sys.path[:] = original_path
        if previous_driver_module is None:
            sys.modules.pop("driver", None)
        else:
            sys.modules["driver"] = previous_driver_module
    report["ok"] = (report["status"] == "CONTROLLER_FINISHED" and report["sim_advanced"]
                    and report["video_path"] is not None
                    and any(call["status"] == "EXECUTED" for call in report["tool_call_log"]))
    report["duration_sec"] = time.monotonic() - started
    _write_json(destination / "task_report.json", report)
    return report
