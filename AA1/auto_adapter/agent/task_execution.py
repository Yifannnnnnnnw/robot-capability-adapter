# SPDX-License-Identifier: Apache-2.0
"""Execute an explicitly supplied task through its exported MCP server."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
import time
from collections.abc import Mapping
from typing import Any

from .recap import UPSTREAM_CONTROLLER, UPSTREAM_REVISION, run_recap
from .recap import AA1CapabilityAdapter, AA1RecapModel, RecapBudgets, passed_design
import mujoco
import numpy as np


def _to_jsonable(value):
    """Convert task observations and reports to JSON-compatible values."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


class _TaskRecorder:
    """Record this task's real MuJoCo steps with its own offscreen renderer."""

    def __init__(self, model, data, *, capture_every=16, max_frames=3000):
        self.frames = []
        self._data = data
        self._capture_every = capture_every
        self._max_frames = max_frames
        self._step_count = 0
        self._cam = mujoco.MjvCamera()
        mujoco.mjv_defaultFreeCamera(model, self._cam)
        self._cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self._cam.azimuth = float(model.vis.global_.azimuth)
        self._cam.elevation = float(model.vis.global_.elevation)
        self._cam.lookat[:] = model.stat.center
        self._cam.distance = float(max(model.stat.extent, 0.3)) * 1.8
        self._track_free = bool(model.njnt and model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE)
        self._track_offset = (np.asarray(self._cam.lookat).copy() - data.qpos[:3]
                              if self._track_free else np.zeros(3))
        self._renderer = mujoco.Renderer(model, height=360, width=480)
        self._orig_mj_step = mujoco.mj_step

        def record_step(current_model, current_data, nstep=1):
            # Leave other worlds' stepping untouched while recording this task.
            if current_model is not model or current_data is not data:
                return self._orig_mj_step(current_model, current_data, nstep)
            for _ in range(int(nstep)):
                self._orig_mj_step(model, data, 1)
                self._step_count += 1
                if self._step_count % self._capture_every == 0:
                    self.snapshot()

        mujoco.mj_step = record_step

    def snapshot(self):
        if len(self.frames) >= self._max_frames:
            return
        try:
            if self._track_free:
                self._cam.lookat[:] = self._data.qpos[:3] + self._track_offset
            self._renderer.update_scene(self._data, camera=self._cam)
            self.frames.append(self._renderer.render())
        except Exception:
            pass

    def uninstall(self):
        mujoco.mj_step = self._orig_mj_step
        try:
            self._renderer.close()
        except Exception:
            pass


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_to_jsonable(value), indent=2, allow_nan=False) + "\n")


def _copy_export_inputs(*, source: Path, server: Path, destination: Path,
                        from_scratch: bool, scene: Path) -> tuple[Path, Path]:
    """Copy the exported server and its local Python inputs into a fresh task dir."""

    if not source.is_file():
        raise ValueError(f"driver_path does not exist: {source}")
    if not server.is_file():
        raise ValueError(f"export_server_path does not exist: {server}")
    if not scene.is_file():
        raise ValueError(f"scene_path does not exist: {scene}")

    driver_name = "driver_from_scratch.py" if from_scratch else "driver.py"
    shutil.copy2(source, destination / driver_name)
    shutil.copy2(server, destination / "mcp_server.py")

    # Generated standard wrappers commonly import a sibling scratch module. Copy
    # local Python dependencies without copying design or validation artifacts.
    for candidate in sorted(source.parent.glob("*.py")):
        if candidate.name in {source.name, server.name}:
            continue
        target = destination / candidate.name
        if not target.exists():
            shutil.copy2(candidate, target)

    (destination / "mjcf.xml").symlink_to(scene)
    return destination / "mcp_server.py", destination / driver_name


def _runtime_config(*, scene: Path, initial_state: Any, output_dir: Path,
                    success_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "scene_path": str(scene),
        "initial_state": initial_state or {},
        "output_dir": str(output_dir),
        "record_video": True,
        "success": success_spec,
    }


async def _run_exported_task(*, server_path: Path, destination: Path, robot_id: str,
                             task_description: str, parameters: Mapping[str, Any],
                             required_capabilities: tuple[str, ...], model: Any,
                             model_client: Any,
                             provider: str, region: str, max_tokens: int,
                             recap_budgets: RecapBudgets,
                             model_trace_path: Path,
                             scene_path: Path, initial_state: Any,
                             success_spec: Mapping[str, Any] | None,
                             tool_call_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the real SDK client and synchronous ReCAP controller in one event loop."""

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    aa1_root = Path(__file__).resolve().parents[2]
    config_path = destination / "task_runtime_config.json"
    runtime_config = _runtime_config(
        scene=scene_path,
        initial_state=initial_state,
        output_dir=destination,
        success_spec=success_spec,
    )
    config_path.write_text(json.dumps(runtime_config, indent=2) + "\n")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [
            str(aa1_root), str(destination), os.environ.get("PYTHONPATH", ""),
        ])),
        "AA1_TASK_RUNTIME_CONFIG": str(config_path),
    }
    errlog_path = destination / "mcp_server.stderr.log"
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        env=environment,
        cwd=str(destination),
    )

    result: Any = None
    deferred_error: Exception | None = None
    initial_observation: Any = {"available": False, "error_type": "NotRead"}
    with errlog_path.open("w") as errlog:
        async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tools_dump = (tools_result.model_dump(mode="json")
                              if hasattr(tools_result, "model_dump")
                              else _to_jsonable(tools_result))
                _write_json(destination / "mcp_tools.json", tools_dump)
                adapter = AA1CapabilityAdapter(
                    session=session,
                    loop=asyncio.get_running_loop(),
                    tools_result=tools_result,
                    robot_configuration_id=robot_id,
                    tool_call_log=tool_call_log,
                )
                missing = sorted(set(required_capabilities) - adapter.tool_names)
                if missing:
                    deferred_error = ValueError(
                        "required capabilities are not advertised by the exported MCP "
                        f"server: {missing}"
                    )
                else:
                    initial_observation = await asyncio.to_thread(adapter.read_observation)
                    if (not isinstance(initial_observation, Mapping)
                            or initial_observation.get("available") is False):
                        deferred_error = RuntimeError(
                            "initial robot://state observation is unavailable: "
                            f"{initial_observation}"
                        )
                    else:
                        client = model_client
                        if client is None:
                            client = AA1RecapModel(
                                model=model, provider=provider, region=region,
                                max_tokens=max_tokens, trace_path=model_trace_path,
                            )
                        if model_client is not None:
                            # Named fixture models exercise the same official JSON
                            # controller while leaving a complete request/response
                            # record alongside the compact controller trace.
                            class RecordedModel:
                                def generate_json(self, *, messages):
                                    response = model_client.generate_json(messages=messages)
                                    record = {
                                        "system_prompt": "",
                                        "messages": messages,
                                        "converted_messages": messages,
                                        "tools": [],
                                        "response": response,
                                        "usage": None,
                                    }
                                    with model_trace_path.open("a") as stream:
                                        stream.write(json.dumps(
                                            {"messages": messages, "response": response},
                                            ensure_ascii=False, allow_nan=False) + "\n")
                                    messages_path = model_trace_path.with_name("model_messages.jsonl")
                                    with messages_path.open("a") as stream:
                                        stream.write(json.dumps(
                                            record, ensure_ascii=False, allow_nan=False) + "\n")
                                    return response

                            client = RecordedModel()
                        result = await asyncio.to_thread(
                            run_recap,
                            public_task={"description": task_description, "parameters": parameters or {}},
                            adapter=adapter,
                            model=client,
                            budgets=recap_budgets,
                            initial_public_state=initial_observation,
                            log_dir=destination / "recap",
                        )

    if deferred_error is not None:
        raise deferred_error

    runtime_report = _read_runtime_report(destination)
    return {
        "result": result,
        "initial_observation": initial_observation,
        "capability_whitelist": sorted(adapter.tool_names),
        "runtime_report": runtime_report,
    }


def _read_runtime_report(destination: Path) -> dict[str, Any] | None:
    path = destination / "runtime_report.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__, "error": str(exc)}
    return value if isinstance(value, dict) else {"available": False, "error_type": "InvalidReport"}


def run_task(*, driver_path, robot_id, capability_design, validation_suite,
             validation_report, task_description, scene_path, initial_state, parameters,
             output_dir, model, provider="holistic", region="us-east-1", max_tokens=6000,
             from_scratch=False, required_capabilities=(), model_client=None,
             export_server_path=None, success_spec=None, task_evaluator=None,
             recap_budgets=None):
    """Run one task through a copied, exported MCP server.

    ``model_client`` remains an explicitly named fixture injection point. It is
    used only as the synchronous ReCAP model; robot control always goes through
    the official MCP ClientSession protocol.

    ``task_evaluator`` is an optional caller-owned metric function, applied
    after the complete physical trace has been checked. It stays in the caller
    process and is never sent to the model or exported server.
    """

    started = time.monotonic()
    budgets = RecapBudgets(**(recap_budgets or {}))
    source = Path(driver_path).resolve()
    scene = Path(scene_path).resolve()
    server = Path(export_server_path).resolve() if export_server_path else source.parent / "mcp_server.py"
    destination = Path(output_dir).resolve()
    required = tuple(required_capabilities or ())
    trace_path = destination / "trace.jsonl"
    model_trace_path = destination / "model_turns.jsonl"
    report_path = destination / "task_report.json"
    report: dict[str, Any] = {
        "ok": False, "status": "UNAVAILABLE", "robot_id": robot_id,
        "task_description": task_description,
        "parameters": parameters or {}, "success": success_spec,
        "execution_ok": False, "task_metrics": [], "evaluation_error": None,
        "controller": UPSTREAM_CONTROLLER,
        "controller_source_commit": UPSTREAM_REVISION,
        "controller_result": None, "physical_task_success": None,
        "model": model, "recap_budgets": asdict(budgets), "max_tokens": max_tokens,
        "scope": "diagnostic task execution; no independent task predicate evaluated",
        "driver_path": str(source), "export_server_path": str(server),
        "scene_path": str(scene), "from_scratch": from_scratch,
        "capability_whitelist": [], "required_capabilities": list(required),
        "tool_call_log": [], "initial_observation": None,
        "runtime_report": None, "sim_time_start": None, "sim_time_end": None,
        "sim_advanced": False, "n_frames": 0, "video_path": None,
        "trace_path": str(trace_path), "model_trace_path": str(model_trace_path),
        "model_messages_path": str(destination / "model_messages.jsonl"),
        "mcp_tools_path": str(destination / "mcp_tools.json"),
        "report_path": str(report_path),
    }
    destination_created = False
    try:
        destination.mkdir(parents=True, exist_ok=False)
        destination_created = True
        trace_path.write_text("")
        model_trace_path.write_text("")
        # Resolve missing-export failures before any attempt to load or invoke a
        # driver. Existing standalone calls derive the sibling export path above.
        if not server.is_file():
            raise ValueError(f"exported MCP server is unavailable: {server}")
        if not source.is_file() or not scene.is_file():
            raise ValueError("driver_path and scene_path must both exist")
        _copy_export_inputs(source=source, server=server, destination=destination,
                            from_scratch=from_scratch, scene=scene)

        if capability_design.get("robot_configuration_id") != robot_id:
            raise ValueError("capability design robot_configuration_id does not match robot_id")
        for label, artifact in (("validation suite", validation_suite),
                                ("validation report", validation_report)):
            identity = artifact.get("robot_configuration_id", artifact.get("robot_id"))
            if identity is not None and identity != robot_id:
                raise ValueError(f"{label} robot identity does not match robot_id")

        # When a required name is also present in the submitted design, retain
        # the Framework validation gate. It never supplies the runtime catalog:
        # list_tools() remains authoritative, including for newly exported names.
        design_names = {
            cap.get("method_name") for cap in capability_design.get("capabilities", [])
            if isinstance(cap, Mapping)
        }
        if required and set(required) <= design_names:
            selected = passed_design(capability_design, validation_suite, validation_report)
            validated = {cap["method_name"] for cap in selected["capabilities"]}
            missing_validation = sorted(set(required) - validated)
            if missing_validation:
                raise ValueError(
                    "required capabilities are missing or not fully validated: "
                    f"{missing_validation}"
                )

        outcome = asyncio.run(_run_exported_task(
            server_path=destination / "mcp_server.py", destination=destination,
            robot_id=robot_id, task_description=task_description,
            parameters=parameters or {},
            required_capabilities=required, model=model, model_client=model_client,
            provider=provider, region=region, max_tokens=max_tokens,
            recap_budgets=budgets,
            model_trace_path=model_trace_path,
            scene_path=scene, initial_state=initial_state,
            success_spec=success_spec,
            tool_call_log=report["tool_call_log"],
        ))
        recap_result = outcome["result"]
        report.update(
            status=recap_result.status,
            controller_result=asdict(recap_result),
            initial_observation=outcome["initial_observation"],
            capability_whitelist=outcome["capability_whitelist"],
            runtime_report=outcome["runtime_report"],
        )
        trace_path.write_text("".join(
            json.dumps(item, allow_nan=False) + "\n" for item in recap_result.trace
        ))
        runtime = outcome["runtime_report"] or {}
        report["sim_time_start"] = runtime.get("sim_time_start")
        report["sim_time_end"] = runtime.get("sim_time_end")
        report["sim_advanced"] = bool(
            runtime.get("sim_time_start") is not None
            and runtime.get("sim_time_end") is not None
            and runtime["sim_time_end"] > runtime["sim_time_start"]
        )
        report["n_frames"] = runtime.get("n_frames", 0)
        report["video_path"] = runtime.get("video_path")
    except Exception as exc:
        report.update(
            status="RUNTIME_ERROR" if report["controller_result"] is not None else "UNAVAILABLE",
            error_type=type(exc).__name__, error=str(exc),
        )
    finally:
        if destination_created:
            if report["runtime_report"] is None:
                report["runtime_report"] = _read_runtime_report(destination)
            runtime = report["runtime_report"] or {}
            report["sim_time_start"] = runtime.get("sim_time_start")
            report["sim_time_end"] = runtime.get("sim_time_end")
            report["n_frames"] = runtime.get("n_frames", report["n_frames"])
            report["video_path"] = runtime.get("video_path", report["video_path"])
            report["sim_advanced"] = bool(
                report["sim_time_start"] is not None
                and report["sim_time_end"] is not None
                and report["sim_time_end"] > report["sim_time_start"]
            )
            _write_json(report_path, report)
    successful_calls = sum(
        1 for call in report["tool_call_log"] if call.get("status") == "EXECUTED"
    )
    report["successful_mcp_calls"] = successful_calls
    runtime_error = (report["runtime_report"] or {}).get("error")
    report["execution_ok"] = bool(
        report["status"] == "CONTROLLER_FINISHED"
        and successful_calls > 0
        and report["sim_advanced"]
        and report["video_path"]
        and not runtime_error
    )
    if success_spec is not None and destination_created:
        report["scope"] = "diagnostic task execution with independent physical task evaluation"
        evaluation_path = destination / "task_evaluation.json"
        physics_path = destination / "physics_samples.jsonl"
        report["evaluation_path"] = str(evaluation_path)
        report["physics_samples_path"] = str(physics_path)
        try:
            from auto_adapter.demo_evaluation import evaluate_demo_task

            trace = (report["runtime_report"] or {}).get("physics_trace")
            if not trace or trace.get("error"):
                raise ValueError((trace or {}).get("error") or "physical trace is unavailable")
            samples = [json.loads(line) for line in physics_path.read_text().splitlines()]
            if len(samples) < 2 or len(samples) != trace.get("sample_count"):
                raise ValueError("physical trace is incomplete or simulation did not advance")
            if (abs(samples[0]["time"] - report["sim_time_start"]) > 1e-8
                    or abs(samples[-1]["time"] - report["sim_time_end"]) > 1e-8):
                raise ValueError("physical trace does not cover the complete task")
            evaluation = evaluate_demo_task(success_spec, parameters or {}, samples,
                                            evaluator=task_evaluator)
        except Exception as exc:
            evaluation = {"physical_task_success": None, "task_metrics": [],
                          "evaluation_error": f"{type(exc).__name__}: {exc}"}
        report.update(evaluation)
        _write_json(evaluation_path, evaluation)
    report["ok"] = bool(report["execution_ok"] and (
        success_spec is None or report["physical_task_success"] is True
    ))
    report["duration_sec"] = time.monotonic() - started
    if destination_created:
        _write_json(report_path, report)
    return report
