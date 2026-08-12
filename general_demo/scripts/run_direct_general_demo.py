#!/usr/bin/env python3
"""Run the generic DIRECT_MUJOCO_EXPERIMENTAL General Demo entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - exercised by the CLI smoke test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoadapter2.orchestration.direct_general_demo import (  # noqa: E402
    DirectGeneralDemoConfig,
    DirectGeneralDemoResolutionError,
    run_direct_general_demo,
)
from autoadapter2.foundation.errors import ContractError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Run the five-task General Demo through the approved "
            "DIRECT_MUJOCO_EXPERIMENTAL no-SDK route."
        )
    )
    parser.add_argument("--root", type=Path, default=default_root, help="project root")
    parser.add_argument("--robot-configuration-id", required=True)
    parser.add_argument("--asset-cache-root", required=True, type=Path)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--task-catalog-version", default="1.0.0")
    parser.add_argument("--task-adapter", type=Path, help="generic direct_mujoco_adapter.json")
    parser.add_argument("--run-id", default="direct-general-demo")
    parser.add_argument("--run-snapshot", type=Path)
    parser.add_argument("--task-set", type=Path)
    parser.add_argument("--robot-public-projection", type=Path)
    parser.add_argument("--g2-profile", type=Path)
    parser.add_argument("--standards-snapshot", type=Path)
    parser.add_argument("--measurement-catalog", type=Path)
    parser.add_argument("--blue-line-policy", type=Path)
    parser.add_argument("--implementation-bundle", type=Path)
    parser.add_argument("--validation-a-profile", type=Path)
    parser.add_argument("--public-state-schema", type=Path)
    parser.add_argument("--validation-harness-config", type=Path)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--model-prompt-config", type=Path)
    parser.add_argument("--output-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = run_direct_general_demo(
            DirectGeneralDemoConfig(
                root=args.root,
                robot_configuration_id=args.robot_configuration_id,
                asset_cache_root=args.asset_cache_root,
                version=args.version,
                task_catalog_version=args.task_catalog_version,
                run_id=args.run_id,
                task_adapter_path=args.task_adapter,
                run_snapshot_path=args.run_snapshot,
                task_set=args.task_set,
                robot_public_projection=args.robot_public_projection,
                g2_profile=args.g2_profile,
                standards_snapshot=args.standards_snapshot,
                measurement_catalog=args.measurement_catalog,
                blue_line_policy=args.blue_line_policy,
                implementation_bundle=args.implementation_bundle,
                validation_a_profile=args.validation_a_profile,
                public_state_schema=args.public_state_schema,
                validation_harness_config=args.validation_harness_config,
                budget=args.budget,
                model_prompt_config=args.model_prompt_config,
                output_root=args.output_root,
            )
        )
    except (ContractError, DirectGeneralDemoResolutionError, OSError, ValueError) as exc:
        print(f"direct General Demo could not start: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result.status,
        "execution_route": "DIRECT_MUJOCO_EXPERIMENTAL",
        "sdk_grounded_simulation_claim": False,
        "run_id": result.run_id,
        "robot_configuration_id": result.robot_configuration_id,
        "run_directory": str(result.run_directory),
        "summary": str(result.summary_path),
        "closure": str(result.run_closure_path),
    }, sort_keys=True))
    return 0 if result.status == "COMPLETE" else 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
