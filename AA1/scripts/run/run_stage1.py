"""Thin launcher for the bounded Stage 1 capability diagnostic.

The pipeline owns generation, Framework validation, repair admission and all
per-robot artifacts.  This module only selects the allowed robots, forwards
the public pipeline arguments, records one compact invocation aggregate, and
prints a short handoff line for each selected robot.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


AA1_ROOT = Path(__file__).resolve().parents[2]
if str(AA1_ROOT) not in sys.path:
    sys.path.insert(0, str(AA1_ROOT))
DEFAULT_BATCH_PARENT = (
    AA1_ROOT / "autoadapter_bench" / "diagnostics" / "capability_update_20260909"
).resolve()
DEFAULT_MODEL = "eu.anthropic.claude-opus-4-8"
DEFAULT_ROBOTS = (
    "franka",
    "so101",
    "kuka_iiwa14",
    "ufactory_xarm7",
    "kinova_gen3_robotiq_2f85",
    "universal_robots_ur5e_robotiq_2f85",
    "go2",
    "unitree_a1",
    "anymal_c",
    "h1",
)
PASSED_ROBOTS = frozenset(
    {"piper", "leap_hand", "hello_robot_stretch_2", "aloha_2", "skydio_x2"}
)
ALLOWED_ROBOTS = frozenset(DEFAULT_ROBOTS)


def run_stage1(
    *,
    robot_id: str,
    workspace_root: Path,
    model: str,
    max_repairs: int = 3,
) -> Any:
    """Resolve and call the pipeline entrypoint lazily.

    Lazy import keeps this launcher unit-testable before the pipeline function
    is present in a partial checkout, while preserving the exact public call
    requested by the Stage 1 contract.
    """
    from auto_adapter.orchestrator import run_stage1 as pipeline_run_stage1

    return pipeline_run_stage1(
        robot_id=robot_id,
        workspace_root=workspace_root,
        model=model,
        max_repairs=max_repairs,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fresh, bounded Stage 1 diagnostic for selected robots."
    )
    parser.add_argument(
        "--robots",
        nargs="+",
        metavar="ROBOT",
        default=list(DEFAULT_ROBOTS),
        help="one or more remaining canonical robot IDs (default: Stage 1 set)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--max-repairs",
        type=int,
        choices=range(4),
        default=3,
        metavar="0..3",
        help="Framework repair attempts after initial generation (default: 3)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="existing or new batch parent for disjoint robot submissions",
    )
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _wall_duration_sec(started: datetime, finished: datetime) -> float:
    return round((finished - started).total_seconds(), 3)


def _compact_result(robot_id: str, value: Any, duration_sec: float) -> dict[str, Any]:
    """Translate the fixed pipeline result into a small launch row."""
    if not isinstance(value, dict):
        return {
            "robot_id": robot_id,
            "status": "error",
            "duration_sec": round(float(duration_sec), 3),
            "error": "run_stage1 returned a non-dict result",
        }
    external_blocked = value.get("external_blocked") is True
    stage1_ok = value.get("stage1_ok") is True
    if stage1_ok and not external_blocked:
        status = "stage1_ok"
    elif external_blocked:
        status = "external_blocked"
    else:
        status = "failed"
    error = value.get("error")
    result: dict[str, Any] = {
        "robot_id": robot_id,
        "status": status,
        "duration_sec": round(float(duration_sec), 3),
    }
    if error:
        result["error"] = str(error)
    for name in ("workspace", "summary_path"):
        item = value.get(name)
        if item is not None:
            result[name] = str(item)
    return result


def _new_launch_path(output_root: Path, started: datetime) -> Path:
    stamp = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
    return output_root / f"launch_{stamp}.json"


def _create_launch_aggregate(path: Path, payload: dict[str, Any]) -> None:
    """Create invocation metadata before the first model call."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _update_launch_aggregate(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _validate_robot_selection(robots: list[str]) -> None:
    if not robots:
        raise ValueError("at least one robot must be selected")
    duplicates = sorted({robot for robot in robots if robots.count(robot) > 1})
    if duplicates:
        raise ValueError("duplicate robot IDs are not allowed: " + ", ".join(duplicates))
    rejected = [robot for robot in robots if robot in PASSED_ROBOTS]
    if rejected:
        raise ValueError(
            "already-passed robots are excluded from this Stage 1 diagnostic "
            "and cannot be selected: "
            + ", ".join(rejected)
        )
    unknown = [robot for robot in robots if robot not in ALLOWED_ROBOTS]
    if unknown:
        raise ValueError(
            "unknown or unsupported Stage 1 robot ID(s): " + ", ".join(unknown)
        )


def run_selected(
    *,
    robots: list[str],
    model: str,
    max_repairs: int,
    output_root: Path | None,
    pipeline_runner: Callable[..., Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run selected robots sequentially and write one invocation aggregate."""
    _validate_robot_selection(robots)
    if max_repairs not in range(4):
        raise ValueError("max_repairs must be between 0 and 3")

    started = _utc_now()
    if output_root is None:
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        root = DEFAULT_BATCH_PARENT / f"stage1_full_opus48_{stamp}"
    else:
        # A shared existing root is intentional: the pipeline decides whether
        # a particular robot workspace is safe to reuse.
        root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    launch_path = _new_launch_path(root, started)
    aggregate: dict[str, Any] = {
        "batch": root.name,
        "started_at_utc": _utc_text(started),
        "finished_at_utc": None,
        "duration_sec": None,
        "model": model,
        "max_repairs": max_repairs,
        "output_root": str(root),
        "selected_robots": list(robots),
        "results": [],
        "all_stage1_ok": False,
        "exit_code": 1,
        "launch_path": str(launch_path),
    }
    _create_launch_aggregate(launch_path, aggregate)
    invoke = pipeline_runner or run_stage1
    results: list[dict[str, Any]] = []
    for index, robot_id in enumerate(robots):
        robot_started = _utc_now()
        t0 = time.monotonic()
        pipeline_result: Any = None
        pipeline_error: Exception | None = None
        try:
            pipeline_result = invoke(
                robot_id=robot_id,
                workspace_root=root,
                model=model,
                max_repairs=max_repairs,
            )
        except Exception as exc:  # noqa: BLE001
            pipeline_error = exc
        robot_finished = _utc_now()
        wall_duration_sec = _wall_duration_sec(robot_started, robot_finished)
        monotonic_duration_sec = round(time.monotonic() - t0, 3)
        if pipeline_error is not None:
            result = {
                "robot_id": robot_id,
                "status": "error",
                "duration_sec": wall_duration_sec,
                "monotonic_duration_sec": monotonic_duration_sec,
                "error": f"{type(pipeline_error).__name__}: {pipeline_error}",
            }
        else:
            result = _compact_result(robot_id, pipeline_result, wall_duration_sec)
            result["monotonic_duration_sec"] = monotonic_duration_sec
        result["started_at_utc"] = _utc_text(robot_started)
        result["finished_at_utc"] = _utc_text(robot_finished)
        results.append(result)
        aggregate["results"] = results
        _update_launch_aggregate(launch_path, aggregate)
        print(
            f"{robot_id}: {result['status']} "
            f"({result['duration_sec']:.1f}s)"
            + (f" — {result['error']}" if result.get("error") else ""),
            flush=True,
        )

        if result["status"] == "external_blocked":
            blocked_error = result.get("error") or "external model/API block"
            for remaining in robots[index + 1 :]:
                results.append(
                    {
                        "robot_id": remaining,
                        "status": "not_run_external_blocked",
                        "duration_sec": 0.0,
                        "monotonic_duration_sec": 0.0,
                        "error": blocked_error,
                    }
                )
                print(
                    f"{remaining}: not_run_external_blocked — {blocked_error}",
                    flush=True,
                )
            aggregate["results"] = results
            _update_launch_aggregate(launch_path, aggregate)
            break

    finished = _utc_now()
    all_ok = bool(results) and len(results) == len(robots) and all(
        item["status"] == "stage1_ok" for item in results
    )
    aggregate["finished_at_utc"] = _utc_text(finished)
    aggregate["duration_sec"] = round((finished - started).total_seconds(), 3)
    aggregate["results"] = results
    aggregate["all_stage1_ok"] = all_ok
    aggregate["exit_code"] = 0 if all_ok else 1
    _update_launch_aggregate(launch_path, aggregate)
    return (0 if all_ok else 1), aggregate


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_robot_selection(list(args.robots))
        code, _aggregate = run_selected(
            robots=list(args.robots),
            model=args.model,
            max_repairs=args.max_repairs,
            output_root=args.output_root,
        )
        return code
    except ValueError as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
