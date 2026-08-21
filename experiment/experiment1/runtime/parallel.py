"""Small subprocess launcher for isolated Experiment 1 B1 cells."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .b1 import (
    B1RunError,
    EXPERIMENT_ROOT,
    REPOSITORY_ROOT,
    _read_json,
    _write_json,
    resolve_experiment_manifest,
)


DEFAULT_ORDER = EXPERIMENT_ROOT / "components" / "execution-order-r1-r5.json"
DEFAULT_RUNNER = EXPERIMENT_ROOT / "run_b1.py"
MAX_WORKERS = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _backbone_from_pair(pair_id: str) -> str:
    parts = pair_id.split("::")
    if len(parts) != 4 or parts[0] != "b1":
        raise B1RunError(f"invalid condition pair ID in execution order: {pair_id}")
    return parts[2]


def materialized_units(
    *,
    manifest_path: Path,
    order_path: Path,
    wave_id: str,
    backbone_ids: Sequence[str],
    robot_ids: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = resolve_experiment_manifest(manifest_path)
    runtime_paths = resolved.get("backbone_runtime_config_paths", {})
    admitted = tuple(dict.fromkeys(backbone_ids))
    if not admitted:
        raise B1RunError("at least one explicit --backbone-id is required")
    unavailable = [
        backbone for backbone in admitted if backbone not in runtime_paths
    ]
    if unavailable:
        raise B1RunError(
            "no pinned runtime config for admitted backbone(s): "
            + ", ".join(unavailable)
        )
    selected_robots = set(robot_ids)
    manifest_units = {
        str(unit["unit_id"]): dict(unit) for unit in resolved["units"]
    }
    order = _read_json(order_path.resolve(), label="materialized execution order")
    waves = order.get("waves")
    if not isinstance(waves, list):
        raise B1RunError(f"{order_path}: waves must be a list")
    matches = [wave for wave in waves if isinstance(wave, Mapping) and wave.get("wave_id") == wave_id]
    if len(matches) != 1:
        raise B1RunError(f"execution order must contain wave {wave_id!r} exactly once")
    blocks = matches[0].get("blocks")
    if not isinstance(blocks, list):
        raise B1RunError(f"execution wave {wave_id!r} has no blocks")
    result: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            raise B1RunError("materialized execution order contains an invalid block")
        pair_id = block.get("condition_pair_id")
        conditions = block.get("condition_order")
        if not isinstance(pair_id, str) or not isinstance(conditions, list):
            raise B1RunError("materialized execution block is incomplete")
        if _backbone_from_pair(pair_id) not in admitted:
            continue
        for condition_index, condition in enumerate(conditions):
            unit_id = f"{pair_id}::{condition}"
            unit = manifest_units.get(unit_id)
            if unit is None:
                continue
            if selected_robots and unit["robot_configuration_id"] not in selected_robots:
                continue
            result.append(
                {
                    **unit,
                    "materialized_block_index": block_index,
                    "within_block_index": condition_index,
                }
            )
    if not result:
        raise B1RunError("execution selection contains no Experiment 1 units")
    return result, resolved


def _safe_name(unit_id: str) -> str:
    return unit_id.replace("::", "__").replace("/", "_")


def _select_phases(
    units: Sequence[Mapping[str, Any]],
    *,
    exact_unit_ids: Sequence[str],
    ramp_canary: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    if ramp_canary and exact_unit_ids:
        raise B1RunError("--ramp-canary and --unit-id are mutually exclusive")
    if exact_unit_ids:
        requested = list(exact_unit_ids)
        if len(requested) != len(set(requested)):
            raise B1RunError("--unit-id values must be distinct")
        available = {str(unit["unit_id"]) for unit in units}
        missing = [unit_id for unit_id in requested if unit_id not in available]
        if missing:
            raise B1RunError(
                "selected unit IDs are absent from the admitted materialized order: "
                + ", ".join(missing)
            )
        selected = [unit for unit in units if unit["unit_id"] in set(requested)]
        return [{"phase_id": "exact", "max_workers": None, "units": selected}]
    if ramp_canary:
        # Use one first-condition cell from each distinct block so each ramp width
        # is actual concurrent process count rather than two cells sharing a pair.
        first_by_pair = []
        seen_pairs: set[str] = set()
        for unit in units:
            pair = str(unit["condition_pair_id"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                first_by_pair.append(unit)
        if len(first_by_pair) < 15:
            raise B1RunError("1->2->4->8 ramp requires 15 distinct admitted pairs")
        phases = []
        offset = 0
        for width in (1, 2, 4, 8):
            phases.append(
                {
                    "phase_id": f"ramp-{width}",
                    "max_workers": width,
                    "units": first_by_pair[offset : offset + width],
                }
            )
            offset += width
        return phases
    selected = list(units[:limit] if limit is not None else units)
    if not selected:
        raise B1RunError("--limit selects no units")
    return [{"phase_id": "run", "max_workers": None, "units": selected}]


def launch_parallel(
    *,
    manifest_path: str | Path,
    order_path: str | Path,
    output_dir: str | Path,
    backbone_ids: Sequence[str],
    robot_ids: Sequence[str] = (),
    max_workers: int = 8,
    wave_id: str = "core-r3",
    exact_unit_ids: Sequence[str] = (),
    ramp_canary: bool = False,
    limit: int | None = None,
    use_existing_fixed_route: bool = False,
    runner_path: str | Path = DEFAULT_RUNNER,
    python_executable: str = sys.executable,
) -> dict[str, Any]:
    """Launch isolated one-cell processes and retain scheduler-only telemetry."""

    if not 1 <= max_workers <= MAX_WORKERS:
        raise B1RunError("--max-workers must be between 1 and 8")
    if ramp_canary and max_workers != 8:
        raise B1RunError("--ramp-canary requires --max-workers 8")
    if limit is not None and limit < 1:
        raise B1RunError("--limit must be positive")
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise B1RunError(f"scheduler output directory must be new or empty: {output}")
    units, resolved = materialized_units(
        manifest_path=Path(manifest_path).resolve(),
        order_path=Path(order_path).resolve(),
        wave_id=wave_id,
        backbone_ids=backbone_ids,
        robot_ids=robot_ids,
    )
    phases = _select_phases(
        units,
        exact_unit_ids=exact_unit_ids,
        ramp_canary=ramp_canary,
        limit=limit,
    )
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    logs.mkdir()
    cells_root = output / "cells"
    cells_root.mkdir()
    record_path = output / "scheduler_record.json"
    scheduler_started = time.monotonic()
    record: dict[str, Any] = {
        "schema_version": 1,
        "scheduler_run_id": f"b1-scheduler::{uuid.uuid4().hex[:12]}",
        "experiment_id": resolved.get("experiment_id"),
        "manifest_path": str(Path(manifest_path).resolve()),
        "execution_order_path": str(Path(order_path).resolve()),
        "wave_id": wave_id,
        "admitted_backbone_ids": list(dict.fromkeys(backbone_ids)),
        "selected_robot_ids": list(dict.fromkeys(robot_ids)),
        "max_workers": max_workers,
        "use_existing_fixed_route": use_existing_fixed_route,
        "utc_started_at": _utc_now(),
        "utc_finished_at": None,
        "elapsed_s": None,
        "phases": [],
        "units": [],
    }
    sequence = 0
    for phase in phases:
        phase_record = {
            "phase_id": phase["phase_id"],
            "max_workers": phase["max_workers"] or max_workers,
            "utc_started_at": None,
            "utc_finished_at": None,
            "unit_ids": [],
        }
        record["phases"].append(phase_record)
        for unit in phase["units"]:
            sequence += 1
            unit_id = str(unit["unit_id"])
            cell_output = cells_root / f"{sequence:03d}-{_safe_name(unit_id)}"
            stdout_path = logs / f"{sequence:03d}.stdout.log"
            stderr_path = logs / f"{sequence:03d}.stderr.log"
            item = {
                "sequence": sequence,
                "phase_id": phase["phase_id"],
                "unit_id": unit_id,
                "condition_pair_id": unit["condition_pair_id"],
                "robot_configuration_id": unit["robot_configuration_id"],
                "backbone_id": unit["backbone_id"],
                "replicate_id": unit["replicate_id"],
                "generation_condition": unit["generation_condition"],
                "queued_at_utc": _utc_now(),
                "started_at_utc": None,
                "finished_at_utc": None,
                "queue_elapsed_s": None,
                "process_elapsed_s": None,
                "exit_code": None,
                "cell_output": str(cell_output),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "cell_record_exists": False,
            }
            record["units"].append(item)
            phase_record["unit_ids"].append(unit_id)

    lock = threading.Lock()

    def persist() -> None:
        with lock:
            _write_json(record_path, record)

    persist()
    by_unit = {item["unit_id"]: item for item in record["units"]}

    def run_one(unit: Mapping[str, Any]) -> None:
        item = by_unit[str(unit["unit_id"])]
        started = time.monotonic()
        command = [
            python_executable,
            str(Path(runner_path).resolve()),
            "--manifest",
            str(Path(manifest_path).resolve()),
            "--unit-id",
            str(unit["unit_id"]),
            "--output",
            item["cell_output"],
        ]
        if use_existing_fixed_route:
            command.append("--use-existing-fixed-route")
        with lock:
            item["started_at_utc"] = _utc_now()
            item["queue_elapsed_s"] = max(0.0, started - scheduler_started)
            item["command"] = command
            _write_json(record_path, record)
        environment = os.environ.copy()
        environment["AUTOADAPTER_EXPERIMENT1_BACKBONE_ID"] = str(unit["backbone_id"])
        with open(item["stdout_path"], "w", encoding="utf-8") as stdout, open(
            item["stderr_path"], "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=stdout,
                stderr=stderr,
            )
            exit_code = process.wait()
        with lock:
            item["finished_at_utc"] = _utc_now()
            item["process_elapsed_s"] = max(0.0, time.monotonic() - started)
            item["exit_code"] = exit_code
            item["cell_record_exists"] = (
                Path(item["cell_output"]) / "cell_record.json"
            ).is_file()
            _write_json(record_path, record)

    offset = 0
    for phase, phase_record in zip(phases, record["phases"], strict=True):
        phase_record["utc_started_at"] = _utc_now()
        persist()
        width = int(phase["max_workers"] or max_workers)
        with ThreadPoolExecutor(max_workers=width) as executor:
            list(executor.map(run_one, phase["units"]))
        phase_record["utc_finished_at"] = _utc_now()
        offset += len(phase["units"])
        persist()
    record["utc_finished_at"] = _utc_now()
    record["elapsed_s"] = max(0.0, time.monotonic() - scheduler_started)
    record["exit_summary"] = {
        "process_count": len(record["units"]),
        "successful_process_count": sum(
            1 for item in record["units"] if item["exit_code"] == 0
        ),
        "failed_process_count": sum(
            1 for item in record["units"] if item["exit_code"] != 0
        ),
    }
    persist()
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch isolated Experiment 1 B1 cell processes (maximum 8)."
    )
    parser.add_argument("--manifest", type=Path, default=EXPERIMENT_ROOT / "manifest.json")
    parser.add_argument("--execution-order", type=Path, default=DEFAULT_ORDER)
    parser.add_argument("--wave", default="core-r3")
    parser.add_argument("--backbone-id", action="append", required=True)
    parser.add_argument("--robot-id", action="append", default=[])
    parser.add_argument("--unit-id", action="append", default=[])
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ramp-canary", action="store_true")
    parser.add_argument("--use-existing-fixed-route", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = launch_parallel(
            manifest_path=args.manifest,
            order_path=args.execution_order,
            output_dir=args.output,
            backbone_ids=args.backbone_id,
            robot_ids=args.robot_id,
            max_workers=args.max_workers,
            wave_id=args.wave,
            exact_unit_ids=args.unit_id,
            ramp_canary=args.ramp_canary,
            limit=args.limit,
            use_existing_fixed_route=args.use_existing_fixed_route,
        )
    except B1RunError as exc:
        print(f"run_b1_parallel: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "scheduler_record": str(Path(args.output).resolve() / "scheduler_record.json"),
                **record["exit_summary"],
            },
            sort_keys=True,
        )
    )
    return 0 if record["exit_summary"]["failed_process_count"] == 0 else 1


__all__ = ["launch_parallel", "materialized_units", "main"]
