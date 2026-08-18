"""Command-line entry point for the Demo3 experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .pipeline import (
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
        ("full", "run reference calibration and the four dynamic cells"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--root", type=Path, default=None)
        command.add_argument("--config", type=Path, default=None)
        if name == "full":
            command.add_argument("--output", type=Path, default=None)
            command.add_argument("--run-id", default=None)
            command.add_argument(
                "--skip-reference-calibration",
                action="store_true",
                help="run dynamic cells diagnostically without the formal reference gate",
            )
            command.add_argument(
                "--reuse-sealed-inputs-from",
                type=Path,
                default=None,
                help="reuse audited TGCD/IVC artifacts from a prior demo3 run",
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
        config = ExperimentConfig.from_path(args.config or root / "experiment.json")
        if args.command in {"package", "check-only"}:
            result = check_packages(root, config=config)
            _print(result)
            return 0

        result = run_experiment(
            root,
            config=config,
            output_dir=args.output,
            run_id=args.run_id,
            skip_reference_calibration=args.skip_reference_calibration,
            sealed_inputs_from=args.reuse_sealed_inputs_from,
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
                "physical_validation_executed": result.get(
                    "physical_validation_executed"
                ),
                "initial_validation_passed": result.get("initial_validation_passed"),
                "final_validation_passed": result.get("final_validation_passed"),
                "success": success_claim(result),
                "claim": result.get("claim"),
                "cell_results": [
                    {
                        "cell_id": cell.get("cell_id"),
                        "pipeline_completed": cell.get("pipeline_completed"),
                        "initial_validation_passed": cell.get(
                            "initial_validation_passed"
                        ),
                        "final_validation_passed": cell.get(
                            "final_validation_passed"
                        ),
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
