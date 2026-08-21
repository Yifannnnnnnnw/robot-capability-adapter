#!/usr/bin/env python3
"""Eight-process company-API concurrency check for Experiment 1."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autoadapter2.model_api import JsonModelClient, ModelConfig  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _worker(worker_id: int) -> dict[str, object]:
    os.environ["AUTOADAPTER_MODEL_PROVIDER"] = "openai-compatible"
    config = ModelConfig.from_env()
    if config.model != "deepseek-v4-pro":
        raise RuntimeError("the M5 concurrency canary requires deepseek-v4-pro")
    client = JsonModelClient(config)
    started = time.monotonic()
    status = "success"
    error = None
    response = None
    try:
        response = client.generate_json(
            stage="experiment1_concurrency_canary",
            prompt=(
                "Return exactly one JSON object with keys ok and worker_id. "
                "Set ok to true and copy the supplied integer worker_id."
            ),
            inputs={"worker_id": worker_id},
        )
    except Exception as exc:  # The call ledger is the primary error evidence.
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    return {
        "worker_id": worker_id,
        "status": status,
        "elapsed_s": max(0.0, time.monotonic() - started),
        "response": response,
        "error": error,
        "provider_calls": client.calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=REPOSITORY_ROOT / ".env")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    _load_env(args.env_file.resolve())
    started_utc = _utc_now()
    started = time.monotonic()
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_worker, range(1, args.workers + 1)))
    report = {
        "artifact_type": "experiment1_company_api_concurrency_canary",
        "formal_experiment_cell": False,
        "backbone_id": "M5",
        "requested_workers": args.workers,
        "started_at_utc": started_utc,
        "ended_at_utc": _utc_now(),
        "elapsed_s": max(0.0, time.monotonic() - started),
        "success_count": sum(item["status"] == "success" for item in results),
        "failure_count": sum(item["status"] != "success" for item in results),
        "workers": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "requested_workers", "elapsed_s", "success_count", "failure_count"
    )}))
    return 0 if report["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
