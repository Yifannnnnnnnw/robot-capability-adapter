#!/usr/bin/env python3
"""Run the approved fixed-family diagnostic, one independent robot at a time.

Reference control is local and mandatory before any model calls for a robot.
No candidate is edited by this runner. Missing preparation and failed controls
remain explicit results, never successful candidate cells.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.model_api import JsonModelClient, ModelConfig
from autoadapter2.pipeline import (
    ExperimentConfig, PipelineHooks, _load_fixed_inputs,
    _run_reference_positive_control, run_experiment,
)

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def model_client(config: ExperimentConfig) -> JsonModelClient:
    # Read simple dotenv assignments without executing shell code or logging secrets.
    env_file = ROOT.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.removeprefix("export ").split("=", 1)
            if key.strip().startswith("AUTOADAPTER_"):
                values = shlex.split(raw, comments=True)
                if len(values) == 1:
                    os.environ.setdefault(key.strip(), values[0])
    manifest = config.model_manifest
    assert manifest is not None
    runtime = replace(
        ModelConfig.from_env(), provider="deepseek", api_protocol="openai-compatible",
        model=manifest["model_id"], base_url=manifest["base_url"],
        thinking="disabled", max_tokens=manifest["max_output_tokens"],
    )
    return JsonModelClient(runtime)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robots", nargs="+")
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = ExperimentConfig.from_path(ROOT / "configs/diagnostics/fixed-family-v1.json")
    robots = args.robots or list(config.robots)
    if not set(robots) <= set(config.robots):
        parser.error("robot is outside the approved diagnostic cohort")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "runs/diagnostic" / f"fixed-family-v1-{stamp}"
    if output.exists():
        parser.error("output must be fresh; existing cells must not be overwritten")
    output.mkdir(parents=True)
    write(output / "diagnostic_config.json", config.as_dict())
    summary = {"formal": False, "reference_only": args.reference_only, "robots": {}}
    for robot in robots:
        result = {"prepared": False, "reference_passed": False, "model_started": False}
        summary["robots"][robot] = result
        client = None
        stage = "preparation"
        try:
            package = load_indexed_robot_package(ROOT, robot)
            fixed, _ = _load_fixed_inputs(
                ROOT / "references/fixed_family_v1", packages={robot: package},
                hooks=PipelineHooks(), require_task_support=False,
            )
            result["prepared"] = True
            one = replace(config, robots=(robot,))
            stage = "reference"
            print(f"{robot}: reference control", flush=True)
            _run_reference_positive_control(
                package=package, design=fixed[robot]["design"], suite=fixed[robot]["suite"],
                config=one, hooks=PipelineHooks(), output_dir=output / robot / "reference",
                run_id=f"fixed-family-reference-{stamp}-{robot}",
            )
            result["reference_passed"] = True
            if not args.reference_only:
                stage = "model"
                client = model_client(one)
                print(f"{robot}: real model synthesis", flush=True)
                result["model_started"] = True
                report = run_experiment(
                    ROOT, config=one, output_dir=output / robot / "candidate",
                    run_id=f"fixed-family-{stamp}-{robot}", client=client,
                    fixed_inputs_from=ROOT / "references/fixed_family_v1",
                    skip_reference_calibration=True,
                )
                result["pipeline_report"] = str(output / robot / "candidate/experiment_report.json")
                result["pipeline_success_claim"] = report.get("success_claim")
        except Exception as exc:
            result["failure_stage"] = stage
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{robot}: {result['error']}", flush=True)
        finally:
            if client is not None:
                write(output / robot / "model_calls.json", client.calls)
                result["model_call_count"] = len(client.calls)
            write(output / "diagnostic_summary.json", summary)
    print(output, flush=True)


if __name__ == "__main__":
    main()
