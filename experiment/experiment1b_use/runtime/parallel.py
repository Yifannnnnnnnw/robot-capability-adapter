"""Bounded subprocess scheduling for selected B2 formal units."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .b2 import (
    AUTHORITY_DOCUMENT_ID,
    AUTHORITY_REVISION,
    DEFAULT_MANIFEST_PATH,
    B2FormalError,
    B2Unit,
    ResolvedB2Manifest,
    _atomic_write_json,
    _code_version,
    assert_dispatch_enabled,
    episode_output_for_unit,
    resolve_manifest,
    terminal_path_for_unit,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_one(
    *,
    unit: B2Unit,
    expected_model: str,
    manifest_path: Path,
    output_root: Path,
    env_file: Path | None,
) -> dict[str, Any]:
    unit_id = unit.unit_id
    terminal_path = terminal_path_for_unit(output_root, unit_id)
    episode_output = episode_output_for_unit(output_root, unit_id)
    if terminal_path.exists() or episode_output.exists():
        return {
            "unit_id": unit_id,
            "status": "infrastructure_absence",
            "reason": "formal unit output was not fresh before subprocess spawn",
        }
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "run_b2.py"),
        "--manifest",
        str(manifest_path),
        "--unit-id",
        unit_id,
        "--output",
        str(output_root),
    ]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    try:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        reason = f"scheduler could not spawn unit process: {type(exc).__name__}"
        return _write_scheduler_failure_terminal(
            unit=unit,
            expected_model=expected_model,
            terminal_path=terminal_path,
            reason=reason,
            process_returncode=None,
        )

    if terminal_path.is_file():
        try:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            terminal = None
        if isinstance(terminal, dict):
            return {
                "unit_id": unit_id,
                "status": "terminal",
                "terminal_path": str(terminal_path),
                "terminal_classification": terminal.get("classification"),
                "process_returncode": completed.returncode,
            }
    reason = (
        "unit process exited without a readable terminal record "
        f"(returncode={completed.returncode})"
    )
    if terminal_path.exists():
        return {
            "unit_id": unit_id,
            "status": "infrastructure_absence",
            "reason": reason,
            "process_returncode": completed.returncode,
        }
    return _write_scheduler_failure_terminal(
        unit=unit,
        expected_model=expected_model,
        terminal_path=terminal_path,
        reason=reason,
        process_returncode=completed.returncode,
    )


def _write_scheduler_failure_terminal(
    *,
    unit: B2Unit,
    expected_model: str,
    terminal_path: Path,
    reason: str,
    process_returncode: int | None,
) -> dict[str, Any]:
    terminal = {
        "artifact_type": "b2_formal_unit_terminal",
        "authority": {
            "document_id": AUTHORITY_DOCUMENT_ID,
            "revision": AUTHORITY_REVISION,
        },
        "manifest_revision": AUTHORITY_REVISION,
        "formal_episode": True,
        "unit_id": unit.unit_id,
        **unit.as_dict(),
        "code_version": _code_version(),
        "utc_started_at": None,
        "utc_finished_at": _utc_now(),
        "terminal_status": "INFRASTRUCTURE_FAILURE",
        "classification": "infrastructure_failure",
        "evaluable": False,
        "success": False,
        "formal_dispatch_enabled": True,
        "model_identity": {
            "requested_model": expected_model,
            "returned_models": [],
            "exact_match": False,
        },
        "controller": {},
        "harness": {},
        "error": {
            "type": "SchedulerSubprocessFailure",
            "message": reason,
            "process_returncode": process_returncode,
        },
        "terminal_path": str(terminal_path),
    }
    _atomic_write_json(terminal_path, terminal)
    return {
        "unit_id": unit.unit_id,
        "status": "terminal",
        "terminal_path": str(terminal_path),
        "terminal_classification": "infrastructure_failure",
        "process_returncode": process_returncode,
    }


def run_scheduler(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    output_root: str | Path,
    unit_ids: list[str] | None = None,
    max_workers: int = 1,
    env_file: str | Path | None = None,
) -> dict[str, Any]:
    """Spawn a fresh CLI process for each selected planned unit."""

    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        raise B2FormalError("max_workers must be a positive integer")
    manifest = resolve_manifest(manifest_path)
    # This check deliberately precedes creation of any subprocess.
    assert_dispatch_enabled(manifest)
    selected = list(unit_ids) if unit_ids is not None else [unit.unit_id for unit in manifest.units]
    if not selected:
        raise B2FormalError("scheduler selection is empty")
    if len(selected) != len(set(selected)):
        raise B2FormalError("scheduler selection contains duplicate unit IDs")
    for unit_id in selected:
        manifest.unit(unit_id)

    manifest_path_resolved = Path(manifest_path).resolve()
    output_path = Path(output_root).resolve()
    env_path = Path(env_file).resolve() if env_file is not None else None
    scheduler_path = output_path / "scheduler.json"
    if scheduler_path.exists():
        raise B2FormalError(f"refusing to overwrite B2 scheduler: {scheduler_path}")
    for unit_id in selected:
        if terminal_path_for_unit(output_path, unit_id).exists() or episode_output_for_unit(
            output_path, unit_id
        ).exists():
            raise B2FormalError(f"formal scheduler output is not fresh for {unit_id}")
    started_at = _utc_now()
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(selected))) as pool:
        futures = {
            pool.submit(
                _run_one,
                unit=manifest.unit(unit_id),
                expected_model=str(
                    manifest.provider_pins[manifest.unit(unit_id).model_id][
                        "exact_model_id"
                    ]
                ),
                manifest_path=manifest_path_resolved,
                output_root=output_path,
                env_file=env_path,
            ): unit_id
            for unit_id in selected
        }
        for future in as_completed(futures):
            unit_id = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "unit_id": unit_id,
                    "status": "infrastructure_absence",
                    "reason": f"scheduler worker failed: {type(exc).__name__}",
                }
            records.append(record)
    records.sort(key=lambda item: selected.index(item["unit_id"]))
    scheduler = {
        "artifact_type": "b2_formal_scheduler",
        "schema_version": "1.0",
        "authority": {
            "document_id": AUTHORITY_DOCUMENT_ID,
            "revision": AUTHORITY_REVISION,
        },
        "manifest_path": str(manifest.manifest_path),
        "manifest_revision": AUTHORITY_REVISION,
        "planned_unit_ids": selected,
        "records": records,
        "utc_started_at": started_at,
        "utc_finished_at": _utc_now(),
    }
    _atomic_write_json(scheduler_path, scheduler)
    scheduler["scheduler_path"] = str(scheduler_path)
    return scheduler


__all__ = ["run_scheduler"]
