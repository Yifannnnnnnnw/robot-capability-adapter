#!/usr/bin/env python3
"""Run one actual first-Demo G2 robot configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT / "src"))

from autoadapter2.foundation.errors import ContractError  # noqa: E402
from autoadapter2.orchestration.first_g2_demo import (  # noqa: E402
    FirstG2DemoConfig,
    ROBOT_CONFIGURATIONS,
    run_first_g2_demo,
)


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Run one robot-scoped first-Demo G2 path through the frozen gate, "
            "Framework-owned session adapter, ModelApiClient, GeneralDemoRunner, "
            "and FFmpeg evidence store."
        )
    )
    parser.add_argument("--root", type=Path, default=project_root)
    parser.add_argument("--robot", choices=tuple(ROBOT_CONFIGURATIONS), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-snapshot", type=Path, required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--standards-snapshot", type=Path)
    parser.add_argument("--measurement-catalog", type=Path)
    parser.add_argument("--blue-line-policy", type=Path)
    args = parser.parse_args(argv)

    blue_line_paths = None
    explicit_blue_line = (
        args.standards_snapshot,
        args.measurement_catalog,
        args.blue_line_policy,
    )
    if any(path is not None for path in explicit_blue_line):
        if not all(path is not None for path in explicit_blue_line):
            parser.error(
                "--standards-snapshot, --measurement-catalog, and --blue-line-policy "
                "must be supplied together"
            )
        blue_line_paths = tuple(explicit_blue_line)

    try:
        result = run_first_g2_demo(
            FirstG2DemoConfig(
                root=args.root,
                robot=args.robot,
                integration_manifest_path=args.manifest,
                run_snapshot_path=args.run_snapshot,
                readiness_report_path=args.readiness_report,
                output_root=args.output_root,
                blue_line_input_paths=blue_line_paths,
            )
        )
    except (ContractError, OSError, ValueError) as exc:
        print(f"first G2 Demo rejected: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "robot": result.robot,
                "run_id": result.run_id,
                "status": result.status,
                "run_directory": str(result.run_directory),
                "summary": str(result.summary_path),
                "summary_seal": str(result.seal_path),
                "validation_video_references": str(result.validation_video_references_path),
                "demo_video_references": str(result.demo_video_references_path),
                "stage_artifacts": str(result.stage_artifacts_path),
                "model_call_log": str(result.model_call_log_path),
                "summary_hash": result.summary_hash,
            },
            sort_keys=True,
        )
    )
    return 0 if result.status == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
