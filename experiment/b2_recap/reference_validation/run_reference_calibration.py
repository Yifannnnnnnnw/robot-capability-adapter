#!/usr/bin/env python3
"""Run the real MuJoCo/Harness reference calibration for B2."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from build_suites import REPOSITORY_ROOT, build, load_selection


def _import_mainline() -> tuple[Any, Any]:
    source_root = REPOSITORY_ROOT / "autoadapter" / "src"
    source = str(source_root)
    if source not in sys.path:
        sys.path.insert(0, source)
    from autoadapter2.harness.runner import run_private_suite
    from autoadapter2.libraries import load_robot_package

    return run_private_suite, load_robot_package


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="directory that receives per-robot reports and videos",
    )
    parser.add_argument(
        "--robot",
        action="append",
        help="robot_configuration_id to run; repeat as needed (default: both)",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="diagnostic smoke only; formal calibration records video by default",
    )
    parser.add_argument("--wall-timeout-s", type=float, default=120.0)
    parser.add_argument("--run-id", default="b2-reference-calibration")
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()

    # Do not calibrate against silently stale generated inputs.
    build(check=True)
    run_private_suite, load_robot_package = _import_mainline()

    selections = load_selection()
    known = {str(item["robot_configuration_id"]): item for item in selections}
    selected_ids = list(known) if args.robot is None else args.robot
    unknown = sorted(set(selected_ids) - set(known))
    if unknown:
        parser.error(f"unknown robot_configuration_id: {', '.join(unknown)}")

    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    record_video = not args.no_video

    for robot_id in selected_ids:
        selection = known[robot_id]
        resolved_dir = REPOSITORY_ROOT / str(selection["resolved_bundle_dir"])
        package = load_robot_package(
            REPOSITORY_ROOT / str(selection["package_root"])
        )
        robot_output = output_root / robot_id
        report = run_private_suite(
            package=package,
            design=_read_json(resolved_dir / "capability_design.json"),
            suite=_read_json(resolved_dir / "capability_validation_suite.json"),
            driver_path=REPOSITORY_ROOT / str(selection["driver_path"]),
            condition=str(selection["condition"]),
            output_dir=robot_output,
            record_video=record_video,
            wall_timeout_s=args.wall_timeout_s,
            run_id=args.run_id,
            attempt=args.attempt,
        )
        report_path = robot_output / "reference_calibration_report.json"
        _write_json(report_path, report)
        summaries.append(
            {
                "robot_configuration_id": robot_id,
                "report": str(report_path.relative_to(output_root)),
                "validation_passed": bool(report.get("validation_passed")),
                "video_complete": bool(report.get("video_complete")),
                "passed_capability_count": report.get("passed_capability_count"),
                "capability_count": report.get("capability_count"),
            }
        )
        print(
            f"{robot_id}: validation_passed={report.get('validation_passed')} "
            f"video_complete={report.get('video_complete')}"
        )

    index = {
        "artifact_type": "b2_reference_calibration_index",
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": args.run_id,
        "attempt": args.attempt,
        "record_video": record_video,
        "robots": summaries,
    }
    _write_json(output_root / "reference_calibration_index.json", index)
    return 0 if all(item["validation_passed"] for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
