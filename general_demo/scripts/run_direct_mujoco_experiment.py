#!/usr/bin/env python3
"""Run one explicit DIRECT_MUJOCO_EXPERIMENTAL action or probe."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # Make the documented repository-local command work without installation.
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from autoadapter2.orchestration.direct_mujoco_run import (
    DirectMuJoCoExperiment,
    create_direct_mujoco_experiment,
)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _write_and_print(result: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one library-selected direct MuJoCo experiment."
    )
    parser.add_argument("--robot-configuration-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root containing general_demo/libraries/morphology",
    )
    parser.add_argument("--asset-cache-root", required=True, type=Path)
    parser.add_argument(
        "--action-json",
        help="JSON object mapping actuator names to controls; defaults to zero controls",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.01,
        help="short physics duration for an action (default: 0.01)",
    )
    parser.add_argument(
        "--probe-source",
        type=Path,
        help="Python source file containing capability_<id>(..., _sdk=...)",
    )
    parser.add_argument(
        "--probe-json",
        type=Path,
        help="JSON file containing the sandbox probe fields",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON result to this file")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not math.isfinite(args.seconds) or args.seconds < 0:
        raise ValueError("--seconds must be a finite non-negative number")
    if (args.probe_source is None) != (args.probe_json is None):
        raise ValueError("--probe-source and --probe-json must be supplied together")
    if args.action_json is not None and args.probe_source is not None:
        raise ValueError("choose --action-json or a probe, not both")

    experiment: DirectMuJoCoExperiment | None = None
    try:
        experiment = create_direct_mujoco_experiment(
            args.robot_configuration_id,
            args.version,
            args.repo_root,
            args.asset_cache_root,
        )
        if args.probe_source is not None and args.probe_json is not None:
            source = args.probe_source.read_text(encoding="utf-8")
            probe = json.loads(args.probe_json.read_text(encoding="utf-8"))
            if not isinstance(probe, dict):
                raise ValueError("--probe-json must contain a JSON object")
            return experiment.run_probe(source, probe)

        action = None if args.action_json is None else _json_object(args.action_json, "--action-json")
        return experiment.run_action(action, seconds=args.seconds)
    finally:
        if experiment is not None:
            experiment.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
        _write_and_print(result, args.output)
    except Exception as exc:
        print(f"experimental direct MuJoCo run failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
