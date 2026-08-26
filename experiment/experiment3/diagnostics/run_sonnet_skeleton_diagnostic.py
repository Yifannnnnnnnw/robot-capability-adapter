#!/usr/bin/env python3
"""Run one non-formal Sonnet skeleton cell through the real AA2 mainline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from autoadapter2.driver_synthesis.generation import ModelCallEvidence, StudyResult
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineHooks,
    check_packages,
    run_experiment,
)
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


def _write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _report_calls(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    stages = report.get("stage_evidence")
    if not isinstance(stages, list):
        return calls
    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        records = stage.get("model_calls")
        if isinstance(records, list):
            calls.extend(dict(record) for record in records if isinstance(record, Mapping))
    return calls


def _resource_summary_from_calls(
    calls: list[dict[str, Any]], model: Mapping[str, Any]
) -> dict[str, Any]:
    return experiment3_runner._resource_summary(SimpleNamespace(calls=calls), model)


def _combine_resource_summaries(
    source: Mapping[str, Any], continuation: Mapping[str, Any]
) -> dict[str, Any]:
    token_keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    source_tokens = source.get("token_categories")
    continuation_tokens = continuation.get("token_categories")
    source_tokens = source_tokens if isinstance(source_tokens, Mapping) else {}
    continuation_tokens = (
        continuation_tokens if isinstance(continuation_tokens, Mapping) else {}
    )

    def total(left: Any, right: Any) -> int | None:
        values = [value for value in (left, right) if isinstance(value, int)]
        return sum(values) if values else None

    source_cost = source.get("estimated_cost")
    continuation_cost = continuation.get("estimated_cost")
    source_cost = source_cost if isinstance(source_cost, Mapping) else {}
    continuation_cost = continuation_cost if isinstance(continuation_cost, Mapping) else {}
    return {
        "model_id": continuation.get("model_id", source.get("model_id")),
        "call_count": int(source.get("call_count", 0))
        + int(continuation.get("call_count", 0)),
        "token_categories": {
            key: total(source_tokens.get(key), continuation_tokens.get(key))
            for key in token_keys
        },
        "model_elapsed_time_s": float(source.get("model_elapsed_time_s", 0.0))
        + float(continuation.get("model_elapsed_time_s", 0.0)),
        "estimated_cost": {
            "is_estimate": True,
            "amount": float(source_cost.get("amount", 0.0))
            + float(continuation_cost.get("amount", 0.0)),
            "currency": continuation_cost.get("currency", source_cost.get("currency")),
            "price_snapshot_date": continuation_cost.get(
                "price_snapshot_date", source_cost.get("price_snapshot_date")
            ),
            "basis": "source diagnostic plus post-fix continuation",
        },
    }


def _load_reused_study(
    cell_dir: Path,
    *,
    robot: str,
    condition: str,
    run_id: str,
    config: ExperimentConfig,
    source_failed_stage: str = "tgcd",
    restarted_stage: str = "TGCD",
) -> tuple[StudyResult, dict[str, Any]]:
    """Load one completed real STUDY from a failed diagnostic cell."""

    source_cell = cell_dir.resolve()
    source_output = source_cell.parents[2]
    cell_report = _read_object(source_cell / "cell_report.json")
    source_report = _read_object(source_output / "experiment_report.json")
    study_output = _read_object(source_cell / "files" / "study.json")
    study_evidence = _read_object(source_cell / "study_evidence.json")
    tgcd_inputs = _read_object(source_cell / "design" / "workspace" / "tgcd_inputs.json")
    canonical_study = tgcd_inputs.get("completed_public_study")
    if isinstance(canonical_study, Mapping):
        study_output = dict(canonical_study)

    if cell_report.get("robot_configuration_id") != robot:
        raise DiagnosticRunError("reused STUDY robot differs from the requested robot")
    if cell_report.get("condition") != condition:
        raise DiagnosticRunError("reused STUDY condition differs from the requested condition")
    failure = cell_report.get("failure")
    if not isinstance(failure, Mapping) or failure.get("stage") != source_failed_stage:
        raise DiagnosticRunError(
            f"reused STUDY source must be an {source_failed_stage.upper()}-failed cell"
        )
    outcomes = cell_report.get("outcomes")
    study_outcome = outcomes.get("STUDY") if isinstance(outcomes, Mapping) else None
    if not isinstance(study_outcome, Mapping) or study_outcome.get("completed") is not True:
        raise DiagnosticRunError("reused STUDY source has no completed STUDY evidence")
    if source_report.get("run_id") != run_id:
        raise DiagnosticRunError("reused STUDY source run_id differs from the continuation")
    source_configuration = source_report.get("configuration")
    if not isinstance(source_configuration, Mapping):
        raise DiagnosticRunError("reused STUDY source has no configuration")
    if source_configuration.get("model") != config.model_manifest:
        raise DiagnosticRunError("reused STUDY source model differs from the Sonnet pin")
    source_experience = source_configuration.get("experience")
    if not isinstance(source_experience, Mapping) or source_experience.get("input") != []:
        raise DiagnosticRunError("reused STUDY source does not have empty Experience")
    if source_configuration.get("max_driver_attempts_per_condition") != 2:
        raise DiagnosticRunError("reused STUDY source does not use the one-Repair budget")

    if study_output.get("condition") != condition:
        raise DiagnosticRunError("reused study.json condition is invalid")
    for field in ("findings", "implementation_plan"):
        value = study_output.get(field)
        if value in (None, "", [], {}):
            raise DiagnosticRunError(f"reused study.json has no non-empty {field}")
    probe_requests = study_output.get("probe_requests")
    if not isinstance(probe_requests, list) or not probe_requests:
        raise DiagnosticRunError("reused study.json has no non-empty probe_requests")
    if any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("probe_id"), str)
        or not item["probe_id"].strip()
        or not isinstance(item.get("script"), str)
        or not item["script"].strip()
        for item in probe_requests
    ):
        raise DiagnosticRunError("reused study.json has an invalid probe request")
    raw_probe_results = study_evidence.get("probe_results")
    if not isinstance(raw_probe_results, list):
        raise DiagnosticRunError("reused STUDY source has no probe results")
    probe_results = tuple(
        dict(item) for item in raw_probe_results if isinstance(item, Mapping)
    )
    if not any(
        item.get("successful") is True
        and isinstance(item.get("physics_steps"), int)
        and item["physics_steps"] > 0
        for item in probe_results
    ):
        raise DiagnosticRunError("reused STUDY source has no successful physics probe")

    source_model = source_configuration["model"]
    source_calls = _report_calls(source_report)
    if not source_calls or any(
        call.get("returned_model") != config.model_manifest.get("model_id")
        for call in source_calls
    ):
        raise DiagnosticRunError("reused STUDY source lacks exact returned-model evidence")
    prior_cumulative = source_report.get("cumulative_resource_summary")
    if isinstance(prior_cumulative, Mapping):
        source_resources = dict(prior_cumulative)
    else:
        source_resources = _resource_summary_from_calls(source_calls, source_model)
    result = StudyResult(
        condition=condition,
        output=dict(study_output),
        probe_requests=tuple(dict(item) for item in probe_requests),
        probe_results=probe_results,
        call_evidence=ModelCallEvidence(
            stage=f"study-reused-after-{source_failed_stage}-framework-fix",
            prompt="",
            inputs={"reused_from": str(source_cell)},
            output=dict(study_output),
        ),
    )
    provenance = {
        "artifact_type": "diagnostic_stage_continuation",
        "formal": False,
        "source_output": str(source_output),
        "source_cell": str(source_cell),
        "source_experiment_report": str(source_output / "experiment_report.json"),
        "source_run_id": source_report.get("run_id"),
        "reused_stage": "STUDY",
        "restarted_stage": restarted_stage,
        "source_failed_stage": source_failed_stage,
        "source_was_continuation": isinstance(
            source_report.get("diagnostic_continuation"), Mapping
        ),
        "source_resource_summary": source_resources,
        "claim_boundary": (
            f"diagnostic continuation across an {source_failed_stage.upper()} framework fix; "
            f"cumulative {source_failed_stage.upper()} turns "
            "are not a single budget-compliant formal cell"
        ),
    }
    return result, provenance


def _load_reused_through_tgcd(
    cell_dir: Path,
    *,
    robot: str,
    condition: str,
    run_id: str,
    config: ExperimentConfig,
) -> tuple[StudyResult, dict[str, Any], dict[str, Any]]:
    """Load sealed STUDY and TGCD artifacts from an IVC-failed diagnostic cell."""

    source_cell = cell_dir.resolve()
    source_output = source_cell.parents[2]
    reused_study, provenance = _load_reused_study(
        source_cell,
        robot=robot,
        condition=condition,
        run_id=run_id,
        config=config,
        source_failed_stage="ivc",
        restarted_stage="IVC",
    )
    cell_report = _read_object(source_cell / "cell_report.json")
    source_report = _read_object(source_output / "experiment_report.json")
    source_configuration = source_report.get("configuration")
    if not isinstance(source_configuration, Mapping):
        raise DiagnosticRunError("reused TGCD source has no configuration")
    if source_configuration.get("formal") is not False:
        raise DiagnosticRunError("reused TGCD source must be non-formal")

    outcomes = cell_report.get("outcomes")
    tgcd_outcome = outcomes.get("TGCD") if isinstance(outcomes, Mapping) else None
    if not isinstance(tgcd_outcome, Mapping) or tgcd_outcome.get("completed") is not True:
        raise DiagnosticRunError("reused TGCD source has no completed TGCD evidence")
    if tgcd_outcome.get("condition") != condition:
        raise DiagnosticRunError("reused TGCD outcome condition differs from the request")

    design_path = source_cell / "design" / "capability_design.json"
    design = _read_object(design_path)
    expected_identity = {
        "robot_configuration_id": robot,
        "package_version": cell_report.get("robot_package_version"),
        "task_snapshot_id": cell_report.get("task_snapshot_id"),
    }
    for field, expected in expected_identity.items():
        if not isinstance(expected, str) or not expected.strip():
            raise DiagnosticRunError(f"reused TGCD source has no {field} identity")
        if design.get(field) != expected:
            raise DiagnosticRunError(
                f"reused capability_design.json {field} differs from the source cell"
            )
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise DiagnosticRunError("reused capability_design.json has no capabilities")

    provenance.update(
        {
            "reused_stage": "STUDY+TGCD",
            "reused_stages": ["STUDY", "TGCD"],
            "restarted_stage": "IVC",
            "reused_capability_design": str(design_path),
        }
    )
    return reused_study, design, provenance


def _summary(
    report: Mapping[str, Any], *, client: Any, output_dir: Path
) -> dict[str, Any]:
    cells = report.get("cells")
    cell = cells[0] if isinstance(cells, list) and cells else {}
    if not isinstance(cell, Mapping):
        cell = {}
    failure = cell.get("failure")
    failed_stage = cell.get("failed_stage")
    if failed_stage is None and isinstance(failure, Mapping):
        failed_stage = failure.get("stage")
    return {
        "experiment_id": report.get("experiment_id"),
        "run_id": report.get("run_id"),
        "formal": False,
        "reference_calibration_skipped": report.get(
            "reference_calibration_skipped"
        ),
        "pipeline_completed": report.get("pipeline_completed"),
        "robot_configuration_id": cell.get("robot_configuration_id"),
        "failed_stage": failed_stage,
        "frozen_driver_attempt_count": cell.get("frozen_driver_attempt_count"),
        "final_capability_validation_passed": cell.get(
            "final_capability_validation_passed"
        ),
        "passed_capability_whitelist": cell.get("passed_capability_whitelist", []),
        "task_demo_executed": cell.get("task_demo_executed"),
        "task_demo_passed": cell.get("task_demo_passed"),
        "model_usage": _client_usage(client),
        "resource_summary": report.get("resource_summary"),
        "cumulative_resource_summary": report.get("cumulative_resource_summary"),
        "diagnostic_continuation": report.get("diagnostic_continuation"),
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
    reuse_group = parser.add_mutually_exclusive_group()
    reuse_group.add_argument("--reuse-study-cell", type=Path)
    reuse_group.add_argument("--reuse-through-tgcd-cell", type=Path)
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
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DiagnosticRunError("diagnostic output directory is not empty")
    hooks = None
    continuation: dict[str, Any] | None = None
    reuse_cell = args.reuse_study_cell or args.reuse_through_tgcd_cell
    reused_design: dict[str, Any] | None = None
    if reuse_cell is not None:
        if args.reuse_through_tgcd_cell is not None:
            reused_study, reused_design, continuation = _load_reused_through_tgcd(
                reuse_cell,
                robot=args.robot,
                condition="skeleton-assisted",
                run_id=args.run_id,
                config=config,
            )
        else:
            reused_study, continuation = _load_reused_study(
                reuse_cell,
                robot=args.robot,
                condition="skeleton-assisted",
                run_id=args.run_id,
                config=config,
            )

        source_cell_report = _read_object(reuse_cell.resolve() / "cell_report.json")
        cell_report_version = source_cell_report.get("robot_package_version")
        cell_report_snapshot = source_cell_report.get("task_snapshot_id")

        def verify_reused_package(package: Any) -> None:
            if package.robot_configuration_id != args.robot:
                raise DiagnosticRunError("runtime package differs from reused STUDY robot")
            if package.package_version != cell_report_version:
                raise DiagnosticRunError("runtime package version differs from reused STUDY")
            if package.snapshot_id != cell_report_snapshot:
                raise DiagnosticRunError("runtime task snapshot differs from reused STUDY")

        def reuse_study_runner(
            _client: Any,
            package: Any,
            design: Any = None,
            *,
            condition: str,
            **_kwargs: Any,
        ) -> StudyResult:
            del design
            verify_reused_package(package)
            if condition != reused_study.condition:
                raise DiagnosticRunError("runtime condition differs from reused STUDY")
            return reused_study

        if reused_design is None:
            hooks = PipelineHooks(study_runner=reuse_study_runner)
        else:

            def reuse_tgcd_runner(
                _client: Any,
                package: Any,
                *,
                study: Mapping[str, Any] | None = None,
                **_kwargs: Any,
            ) -> Mapping[str, Any]:
                verify_reused_package(package)
                if study != reused_study.output:
                    raise DiagnosticRunError("runtime STUDY differs from reused TGCD input")
                return dict(reused_design)

            hooks = PipelineHooks(
                study_runner=reuse_study_runner,
                tgcd_runner=reuse_tgcd_runner,
            )
    report = run_experiment(
        MAINLINE_ROOT,
        config=config,
        output_dir=output_dir,
        run_id=args.run_id,
        client=client,
        hooks=hooks,
        skip_reference_calibration=True,
    )
    model_manifest = config.model_manifest or {}
    continuation_resources = _resource_summary_from_calls(
        list(getattr(client, "calls", [])), model_manifest
    )
    report["resource_summary"] = continuation_resources
    if continuation is not None:
        continuation["continuation_output"] = str(output_dir)
        continuation["continuation_resource_summary"] = continuation_resources
        cumulative = _combine_resource_summaries(
            continuation["source_resource_summary"], continuation_resources
        )
        continuation["cumulative_resource_summary"] = cumulative
        report["diagnostic_continuation"] = continuation
        report["cumulative_resource_summary"] = cumulative
        _write_object(output_dir / "continuation_provenance.json", continuation)
    _write_object(output_dir / "experiment_report.json", report)
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
