#!/usr/bin/env python3
"""Run one actual first-Demo G2 robot configuration.

The robot Session Runner is intentionally supplied by an importable factory.
This script never creates a fixture physical session and never accepts an API
key on the command line or in a run-input file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT / "src"))

from autoadapter2.evaluation import FrozenVideoProfile  # noqa: E402
from autoadapter2.foundation.errors import ContractError  # noqa: E402
from autoadapter2.orchestration.first_g2_demo import (  # noqa: E402
    FirstG2DemoConfig,
    ROBOT_CONFIGURATIONS,
    load_factory,
    run_first_g2_demo,
)


def _json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON input: {path}") from exc
    if not isinstance(value, Mapping):
        raise ContractError(f"JSON input must be an object: {path}")
    return dict(value)


def _video_profile(path: Path | None) -> FrozenVideoProfile | None:
    if path is None:
        return None
    value = _json_object(path)
    resolution = value.get("resolution")
    if not isinstance(resolution, Mapping):
        raise ContractError("video profile resolution must be an object")
    return FrozenVideoProfile(
        profile_id=value.get("profile_id"),
        profile_version=value.get("profile_version"),
        camera=value.get("camera"),
        view=value.get("view"),
        fps=value.get("fps"),
        width=resolution.get("width"),
        height=resolution.get("height"),
        container=value.get("container"),
        codec=value.get("codec"),
    )


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Run one robot-scoped first-Demo G2 path through the frozen gate, "
            "ModelApiClient, GeneralDemoRunner, and FFmpeg evidence store."
        )
    )
    parser.add_argument("--root", type=Path, default=project_root)
    parser.add_argument("--robot", choices=tuple(ROBOT_CONFIGURATIONS), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-snapshot", type=Path, required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument(
        "--robot-session-factory",
        required=True,
        metavar="MODULE:ATTRIBUTE",
        help="importable real Session Runner factory (robot, manifest_path, run_directory)",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--standards-snapshot", type=Path)
    parser.add_argument("--measurement-catalog", type=Path)
    parser.add_argument("--blue-line-policy", type=Path)
    parser.add_argument("--validation-a-profile", type=Path)
    parser.add_argument("--validation-harness-config", type=Path)
    parser.add_argument("--video-profile", type=Path)
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
                robot_session_factory=load_factory(args.robot_session_factory),
                output_root=args.output_root,
                blue_line_input_paths=blue_line_paths,
                validation_a_profile=(
                    _json_object(args.validation_a_profile)
                    if args.validation_a_profile is not None
                    else None
                ),
                validation_harness_config=(
                    _json_object(args.validation_harness_config)
                    if args.validation_harness_config is not None
                    else None
                ),
                video_profile=_video_profile(args.video_profile),
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
                "summary_hash": result.summary_hash,
            },
            sort_keys=True,
        )
    )
    return 0 if result.status == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
