#!/usr/bin/env python3
"""Diagnose an existing scratch candidate with native validation, without generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from run import read_json, write_json  # noqa: E402; also exposes AA1 imports
from auto_adapter.design_validation import validate_design_driver  # noqa: E402


def validate_existing(source: Path, output: Path) -> dict:
    """Keep the failed generation intact and validate its unchanged candidate."""
    if output.exists():
        raise FileExistsError(f"use a fresh diagnostic workspace: {output}")
    names = ("driver_from_scratch.py", "study.json",
             "design/capability_design.json", "design/scene_cases.yaml")
    original = {name: (source / name).read_bytes() for name in names}
    preparation = read_json(source / "design/capability_preparation.json")
    scene_paths = {key: Path(value) for key, value in preparation["scene_paths"].items()}
    for name in names:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    (output / "mjcf.xml").symlink_to((source / "mjcf.xml").resolve())
    started = time.monotonic()
    record = {
        "source_workspace": str(source), "diagnostic_workspace": str(output),
        "model_requests": 0,
        "note": "Post-run native validation only; the original generation outcome is unchanged.",
    }
    try:
        report = validate_design_driver(
            driver_path=output / "driver_from_scratch.py",
            design=read_json(output / "design/capability_design.json"),
            scene_cases_path=output / "design/scene_cases.yaml",
            scene_paths=scene_paths, from_scratch=True,
            output_dir=output / "validation",
        )
        write_json(output / "validate_report.json", report)
        record.update({key: report.get(key) for key in ("all_ok", "n_passed", "n_total", "error")})
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["duration_seconds"] = time.monotonic() - started
        record["source_and_candidate_unchanged"] = all(
            (source / name).read_bytes() == content == (output / name).read_bytes()
            for name, content in original.items()
        )
        write_json(output / "diagnostic_validation.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-workspace", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    record = validate_existing(args.generation_workspace.resolve(), args.output_root.resolve())
    print(json.dumps(record))
    return 0 if record.get("all_ok") and record["source_and_candidate_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
