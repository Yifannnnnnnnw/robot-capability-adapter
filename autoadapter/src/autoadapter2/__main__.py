"""Command-line entry point for the AutoAdapter 2.0 mainline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .pipeline import (
    DEFAULT_CONFIG_PATH,
    ExperimentConfig,
    PipelineError,
    check_packages,
    run_experiment,
    success_claim,
)


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m autoadapter2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("package", "validate the indexed robot packages"),
        ("check-only", "validate self-containment and indexed packages"),
        (
            "full",
            "run fresh configured cells",
        ),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--root", type=Path, default=None)
        command.add_argument("--config", type=Path, default=None)
        if name == "full":
            command.add_argument("--output", type=Path, default=None)
            command.add_argument("--run-id", default=None)
            command.add_argument(
                "--run-reference-positive-controls",
                action="store_true",
                help="run each fresh IVC suite against its private reference driver",
            )
    return parser


def _root(value: Path | None) -> Path:
    return (value or _default_root()).resolve()


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, default=str))


def _error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "pipeline_completed": False,
        "success": False,
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _root(args.root)
    try:
        config = ExperimentConfig.from_path(args.config or root / DEFAULT_CONFIG_PATH)
        if args.command in {"package", "check-only"}:
            result = check_packages(root, config=config)
            _print(result)
            return 0

        result = run_experiment(
            root,
            config=config,
            output_dir=args.output,
            run_id=args.run_id,
            skip_reference_calibration=not args.run_reference_positive_controls,
        )
        _print(
            {
                "experiment_id": result.get("experiment_id"),
                "run_id": result.get("run_id"),
                "pipeline_completed": result.get("pipeline_completed"),
                "reference_calibration_passed": result.get(
                    "reference_calibration_passed"
                ),
                "dynamic_model_called": result.get("dynamic_model_called"),
                "driver_generated_in_run": result.get("driver_generated_in_run"),
                "capability_validation_executed": result.get(
                    "capability_validation_executed"
                ),
                "initial_capability_validation_passed": result.get(
                    "initial_capability_validation_passed"
                ),
                "final_capability_validation_passed": result.get(
                    "final_capability_validation_passed"
                ),
                "task_demo_executed": result.get("task_demo_executed"),
                "task_demo_passed": result.get("task_demo_passed"),
                "success": success_claim(result),
                "claim": result.get("claim"),
                "cell_results": [
                    {
                        "cell_id": cell.get("cell_id"),
                        "pipeline_completed": cell.get("pipeline_completed"),
                        "initial_capability_validation_passed": cell.get(
                            "initial_capability_validation_passed"
                        ),
                        "final_capability_validation_passed": cell.get(
                            "final_capability_validation_passed"
                        ),
                        "task_demo_executed": cell.get("task_demo_executed"),
                        "task_demo_passed": cell.get("task_demo_passed"),
                        "attempt_count": cell.get("attempt_count"),
                    }
                    for cell in result.get("cells", [])
                    if isinstance(cell, dict)
                ],
            }
        )
        return 0 if success_claim(result) else 1
    except (PipelineError, OSError, ValueError) as exc:
        _print(_error_payload(exc))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
