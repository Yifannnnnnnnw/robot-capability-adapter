#!/usr/bin/env python3
"""Run one non-formal Sonnet skeleton cell through the real AA2 mainline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from autoadapter2.pipeline import ExperimentConfig, check_packages, run_experiment
from experiment.experiment3 import runner as experiment3_runner


MAINLINE_ROOT = REPOSITORY_ROOT / "autoadapter"
DEFAULT_CONFIG = (
    MAINLINE_ROOT
    / "configs"
    / "experiments"
    / "sonnet-exp3-remaining-skeleton-diagnostic.json"
)
DEFAULT_MANIFEST = REPOSITORY_ROOT / "experiment" / "experiment3" / "manifest.json"
DEFAULT_ENV_FILE = REPOSITORY_ROOT / ".env.company-api"


class DiagnosticRunError(RuntimeError):
    """Raised when the diagnostic pin or requested robot is invalid."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticRunError(f"cannot read JSON object {path}") from exc
    if not isinstance(value, dict):
        raise DiagnosticRunError(f"{path} must contain one JSON object")
    return value


def _single_robot_config(path: Path, robot: str) -> ExperimentConfig:
    raw = _read_object(path)
    allowed = raw.get("robots")
    if not isinstance(allowed, list) or robot not in allowed:
        raise DiagnosticRunError(
            f"robot {robot!r} is not one of the remaining diagnostic configurations"
        )
    raw["experiment_id"] = f"autoadapter2-diagnostic-sonnet-{robot}-skeleton"
    raw["robots"] = [robot]
    config = ExperimentConfig.from_mapping(raw)
    if config.formal or config.evolution_enabled:
        raise DiagnosticRunError("Sonnet skeleton diagnostic must remain non-formal with Evolution off")
    return config


def _client_usage(client: Any) -> dict[str, Any]:
    calls = getattr(client, "calls", None)
    records = calls if isinstance(calls, list) else []
    token_fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    return {
        "model_call_count": len(records),
        "tokens": {
            field: sum(
                int(record[field])
                for record in records
                if isinstance(record, Mapping)
                and isinstance(record.get(field), int)
                and not isinstance(record.get(field), bool)
            )
            for field in token_fields
        },
        "requested_models": sorted(
            {
                str(record["requested_model"])
                for record in records
                if isinstance(record, Mapping) and record.get("requested_model")
            }
        ),
        "returned_models": sorted(
            {
                str(record["returned_model"])
                for record in records
                if isinstance(record, Mapping) and record.get("returned_model")
            }
        ),
    }


def _summary(
    report: Mapping[str, Any], *, client: Any, output_dir: Path
) -> dict[str, Any]:
    cells = report.get("cells")
    cell = cells[0] if isinstance(cells, list) and cells else {}
    if not isinstance(cell, Mapping):
        cell = {}
    return {
        "experiment_id": report.get("experiment_id"),
        "run_id": report.get("run_id"),
        "formal": False,
        "reference_calibration_skipped": report.get(
            "reference_calibration_skipped"
        ),
        "pipeline_completed": report.get("pipeline_completed"),
        "robot_configuration_id": cell.get("robot_configuration_id"),
        "failed_stage": cell.get("failed_stage"),
        "frozen_driver_attempt_count": cell.get("frozen_driver_attempt_count"),
        "final_capability_validation_passed": cell.get(
            "final_capability_validation_passed"
        ),
        "passed_capability_whitelist": cell.get("passed_capability_whitelist", []),
        "task_demo_executed": cell.get("task_demo_executed"),
        "task_demo_passed": cell.get("task_demo_passed"),
        "model_usage": _client_usage(client),
        "evidence_dir": str(output_dir),
        "experiment_report": str(output_dir / "experiment_report.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    config = _single_robot_config(args.config.resolve(), args.robot)
    manifest = _read_object(args.manifest.resolve())
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise DiagnosticRunError("Experiment 3 manifest has no runtime pins")
    model = runtime.get("producer_model")
    transport = runtime.get("producer_transport")
    if not isinstance(model, Mapping) or not isinstance(transport, Mapping):
        raise DiagnosticRunError("Experiment 3 producer model/transport pins are incomplete")
    if dict(model) != dict(config.model_manifest or {}):
        raise DiagnosticRunError("diagnostic model differs from the Experiment 3 Sonnet pin")

    experiment3_runner._load_env_files([str(args.env_file.resolve())])
    client = experiment3_runner._built_in_client(model, transport)
    package_check = check_packages(MAINLINE_ROOT, config=config)
    if package_check.get("package_check_passed") is not True:
        raise DiagnosticRunError("zero-model package check did not pass")

    output_dir = args.output.resolve()
    report = run_experiment(
        MAINLINE_ROOT,
        config=config,
        output_dir=output_dir,
        run_id=args.run_id,
        client=client,
        skip_reference_calibration=True,
    )
    print(
        json.dumps(
            _summary(report, client=client, output_dir=output_dir),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
