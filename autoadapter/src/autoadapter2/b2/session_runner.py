"""Parent-side ReCAP integration with one persistent capability worker."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoadapter2.b2.capability_adapter import CapabilityAdapter
from autoadapter2.b2.recap import (
    RecapBudgets,
    RecapModelClient,
    run_recap,
)
from autoadapter2.b2.worker_protocol import (
    B2WorkerProtocolError,
    abort_observation,
    initial_public_state,
    public_observation,
)
from autoadapter2.harness.runner import (
    HarnessError,
    _read_protocol,
    _worker_environment,
    _write_protocol,
)


@dataclass(frozen=True)
class RecapWorkerSessionConfig:
    driver_path: Path
    scene_path: Path
    robot_configuration_id: str
    reset: Mapping[str, Any] = field(default_factory=lambda: {"kind": "default"})
    max_steps: int = 10_000
    max_sim_time_s: float = 20.0
    sample_hz: float = 20.0
    wall_timeout_s: float = 120.0
    render: Mapping[str, Any] = field(
        default_factory=lambda: {"enabled": False}
    )
    video_path: Path | None = None


def run_recap_worker_session(
    *,
    config: RecapWorkerSessionConfig,
    capability_design: Mapping[str, Any],
    public_task: Mapping[str, Any],
    model: RecapModelClient,
    budgets: RecapBudgets | None = None,
) -> dict[str, Any]:
    """Run ReCAP and all of its leaves in one driver/model/data session.

    The returned worker evidence is intentionally unevaluated.  A B2 task
    Harness must compute the separate terminal physical verdict after this
    function returns.  ``wall_timeout_s`` limits each worker response; model
    inference happens while the worker is idle and the supplied synchronous
    model client must enforce its own per-call timeout.
    """

    driver_path = config.driver_path.resolve()
    scene_path = config.scene_path.resolve()
    if not driver_path.is_file():
        raise HarnessError(f"B2 driver is absent: {driver_path}")
    if not scene_path.is_file():
        raise HarnessError(f"B2 scene is absent: {scene_path}")
    if (
        not isinstance(config.max_steps, int)
        or isinstance(config.max_steps, bool)
        or config.max_steps <= 0
        or not math.isfinite(config.max_sim_time_s)
        or config.max_sim_time_s <= 0
        or not math.isfinite(config.sample_hz)
        or config.sample_hz <= 0
        or not math.isfinite(config.wall_timeout_s)
        or config.wall_timeout_s <= 0
    ):
        raise HarnessError("B2 session budgets must be positive")
    if config.render.get("enabled") and config.video_path is None:
        raise HarnessError("B2 video_path is required when rendering is enabled")
    video_path = config.video_path.resolve() if config.video_path is not None else None
    if video_path is not None:
        video_path.parent.mkdir(parents=True, exist_ok=True)

    process: subprocess.Popen[str]
    worker_result: dict[str, Any] | None = None
    early_final: dict[str, Any] | None = None
    transport_aborted = False
    worker_response_timeout_s = float(config.wall_timeout_s)

    def invoke(method_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal early_final, transport_aborted
        if transport_aborted:
            return abort_observation(error_code="WORKER_TRANSPORT_ABORTED")
        try:
            _write_protocol(
                process,
                {
                    "type": "invoke",
                    "method_name": method_name,
                    "arguments": dict(arguments),
                },
            )
            response = _read_protocol(
                process,
                wall_timeout_s=worker_response_timeout_s,
            )
            if response.get("type") == "final":
                raw_final = response.get("result")
                if isinstance(raw_final, Mapping):
                    early_final = dict(raw_final)
                transport_aborted = True
                return abort_observation(error_code="WORKER_STOPPED_EARLY")
            return public_observation(response)
        except (HarnessError, B2WorkerProtocolError):
            transport_aborted = True
            if process.poll() is None:
                process.kill()
            return abort_observation(error_code="WORKER_TRANSPORT_ERROR")

    adapter = CapabilityAdapter(capability_design, invoke)
    if adapter.robot_configuration_id != config.robot_configuration_id:
        raise HarnessError("B2 capability design does not match the configured robot")

    source_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="autoadapter2-recap-eval-") as temporary:
        evaluation_workspace = Path(temporary)
        staged_driver = evaluation_workspace / "driver.py"
        shutil.copyfile(driver_path, staged_driver)
        worker_payload = {
            "driver_path": str(staged_driver),
            "scene_path": str(scene_path),
            "capability_methods": [
                contract.method_name for contract in adapter.contracts
            ],
            "public_observation_robot_id": config.robot_configuration_id,
            "reset": dict(config.reset),
            "max_steps": config.max_steps,
            "max_sim_time_s": config.max_sim_time_s,
            "sample_hz": config.sample_hz,
            "render": dict(config.render),
        }
        if "projection_revision" in public_task:
            worker_payload["public_task_projection"] = dict(public_task)
        if video_path is not None:
            worker_payload["video_path"] = str(video_path)
        worker_payload = _json_object(worker_payload)
        process = subprocess.Popen(
            [sys.executable, "-m", "autoadapter2.harness.react_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=evaluation_workspace,
            env=_worker_environment(source_root),
        )

        try:
            _write_protocol(process, worker_payload)
            first = _read_protocol(
                process,
                wall_timeout_s=worker_response_timeout_s,
            )
            if first.get("type") == "final":
                raw_result = first.get("result")
                if not isinstance(raw_result, Mapping):
                    raise HarnessError("B2 worker final result is invalid")
                worker_result = dict(raw_result)
                raise HarnessError("B2 worker stopped before the controller started")

            initial_state = initial_public_state(first)
            controller_result = run_recap(
                public_task=public_task,
                adapter=adapter,
                model=model,
                budgets=budgets,
                initial_public_state=initial_state,
            )

            if early_final is not None:
                worker_result = early_final
            elif transport_aborted:
                worker_result = {
                    "worker_completed": False,
                    "worker_error": {"type": "B2_WORKER_TRANSPORT_ABORT"},
                }
            else:
                _write_protocol(process, {"type": "finish"})
                final = _read_protocol(
                    process,
                    wall_timeout_s=worker_response_timeout_s,
                )
                raw_result = final.get("result")
                if final.get("type") != "final" or not isinstance(
                    raw_result, Mapping
                ):
                    raise HarnessError("B2 worker final result is invalid")
                worker_result = dict(raw_result)
        finally:
            if worker_result is None and process.poll() is None:
                process.kill()
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=min(worker_response_timeout_s, 5.0))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    if worker_result is None:
        raise HarnessError("B2 worker produced no result")
    return _json_object(
        {
            "controller": asdict(controller_result),
            "initial_public_state": initial_state,
            "worker": worker_result,
        }
    )

def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HarnessError("B2 value must be finite JSON") from exc
    if not isinstance(copied, dict):
        raise HarnessError("B2 session result must be an object")
    return copied


__all__ = ["RecapWorkerSessionConfig", "run_recap_worker_session"]
