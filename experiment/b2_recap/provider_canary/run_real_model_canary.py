#!/usr/bin/env python3
"""Run one diagnostic B2 episode with a real pinned provider model.

This runner is deliberately outside the formal 210-episode dispatcher.  It
loads one credential into the Framework parent, keeps the candidate worker
credential-free, and records only secret-free provider-call evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autoadapter2.b2.episode_runner import (  # noqa: E402
    B2DiagnosticEpisodeConfig,
    run_b2_diagnostic_episode,
)
from autoadapter2.b2.model_client import (  # noqa: E402
    B2ModelProviderConfig,
    ReCAPJsonModelClient,
)
from autoadapter2.b2.recap import (  # noqa: E402
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RecapBudgets,
)
from autoadapter2.libraries import load_robot_package  # noqa: E402


DEFAULT_ENV_PATH = REPOSITORY_ROOT / ".env.company-api"
DEFAULT_PROVIDER_PATH = (
    REPOSITORY_ROOT
    / "experiment/experiment1/providers/M1-company-api-sonnet-4-6.json"
)
TASK_SUITE_PATH = (
    REPOSITORY_ROOT / "experiment/b2_recap/task_suite/task_suite.json"
)
ROBOT_ID = "robotstudio_so101"
TASK_ID = "mw_sweep_into_goal"
PACKAGE_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.0"
)
DRIVER_PATH = PACKAGE_ROOT / "reference/fixed_capability_driver.py"
CAPABILITY_DESIGN_PATH = (
    REPOSITORY_ROOT
    / "experiment/b2_recap/reference_validation/resolved/robotstudio_so101"
    / "capability_design.json"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _code_version() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    commit = result.stdout.strip() if result.returncode == 0 else None
    return {
        "git_commit": commit,
        "runner_path": str(Path(__file__).resolve()),
        "model_adapter_path": str(
            REPOSITORY_ROOT / "autoadapter/src/autoadapter2/b2/model_client.py"
        ),
        "controller_path": str(
            REPOSITORY_ROOT / "autoadapter/src/autoadapter2/b2/recap.py"
        ),
    }


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _load_env_values(path: Path) -> dict[str, str]:
    """Read a small dotenv file without mutating or exposing the process env."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError(f"credential env file is absent: {path}") from exc
    values: dict[str, str] = {}
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid dotenv assignment at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in values:
            raise ValueError(f"invalid or duplicate dotenv key at line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _provider_inputs(
    *,
    provider_pin: Mapping[str, Any],
    env_values: Mapping[str, str],
) -> tuple[B2ModelProviderConfig, str, str]:
    if provider_pin.get("transport") != "openai-compatible":
        raise ValueError("B2 provider transport must be openai-compatible")
    if provider_pin.get("endpoint_path") != "/chat/completions":
        raise ValueError("B2 provider endpoint path must be /chat/completions")
    settings = provider_pin.get("inference_settings")
    if not isinstance(settings, Mapping):
        raise ValueError("B2 provider pin has no inference settings")
    if settings.get("temperature") != 0.0:
        raise ValueError("B2 provider temperature must be fixed at 0.0")
    if settings.get("tool_history_mode") != "native":
        raise ValueError("B2 provider tool history mode must be native")

    required_strings = {
        "model": provider_pin.get("exact_model_id"),
        "base_url": provider_pin.get("endpoint_base_url"),
        "credential_env": provider_pin.get("credential_env"),
        "auth_header": provider_pin.get("auth_header"),
        "auth_prefix": provider_pin.get("auth_prefix"),
    }
    for name, value in required_strings.items():
        if not isinstance(value, str) or (name != "auth_prefix" and not value.strip()):
            raise ValueError(f"B2 provider pin has invalid {name}")
    credential_env = required_strings["credential_env"].strip()
    credential = env_values.get(credential_env, "")
    if not credential:
        raise ValueError(f"{credential_env} is absent from the selected env file")

    thinking = settings.get("thinking")
    if thinking is not None and not isinstance(thinking, str):
        raise ValueError("B2 provider thinking setting must be a string or null")
    config = B2ModelProviderConfig(
        provider=str(provider_pin.get("vendor") or "company").strip().lower(),
        model=required_strings["model"].strip(),
        base_url=required_strings["base_url"].strip().rstrip("/"),
        api_protocol="openai-compatible",
        auth_header=required_strings["auth_header"].strip(),
        auth_prefix=required_strings["auth_prefix"],
        thinking=thinking,
        timeout_s=float(settings.get("timeout_s")),
        max_tokens=int(settings.get("max_tokens")),
        history_char_budget=int(settings.get("history_char_budget")),
    )
    return config, credential, credential_env


def _provider_summary(
    *,
    provider_pin: Mapping[str, Any],
    provider_path: Path,
    calls: tuple[Mapping[str, Any], ...],
    exchanges: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    requested_model = provider_pin.get("exact_model_id")
    successful = [call for call in calls if call.get("status") == "success"]
    return {
        "provider_config_path": str(provider_path),
        "backbone_id": provider_pin.get("backbone_id"),
        "family": provider_pin.get("family"),
        "vendor": provider_pin.get("vendor"),
        "requested_model": requested_model,
        "provider_model_revision": provider_pin.get("provider_model_revision"),
        "returned_models": [call.get("returned_model") for call in successful],
        "returned_model_matches_pin": bool(successful)
        and all(call.get("returned_model") == requested_model for call in successful),
        "successful_model_requests": len(successful),
        "physical_model_requests": len(calls),
        "input_tokens": sum(
            int(call.get("input_tokens") or 0) for call in successful
        ),
        "output_tokens": sum(
            int(call.get("output_tokens") or 0) for call in successful
        ),
        "price_snapshot": provider_pin.get("price_snapshot"),
        "calls": [dict(call) for call in calls],
        "raw_secret_free_exchanges": [dict(exchange) for exchange in exchanges],
    }


def run(
    *,
    env_path: Path,
    provider_path: Path,
    output_path: Path,
    wall_timeout_s: float,
    record_video: bool = False,
) -> dict[str, Any]:
    provider_path = provider_path.resolve()
    env_path = env_path.resolve()
    output_path = output_path.resolve()
    pin = _read_object(provider_path, label="provider pin")
    env_values = _load_env_values(env_path)
    provider_config, credential, credential_env = _provider_inputs(
        provider_pin=pin,
        env_values=env_values,
    )
    model = ReCAPJsonModelClient(
        provider_config=provider_config,
        credential=credential,
    )
    budgets = RecapBudgets()
    started_at_utc = _utc_now()
    started = time.monotonic()
    episode: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    try:
        package = load_robot_package(PACKAGE_ROOT)
        episode = run_b2_diagnostic_episode(
            config=B2DiagnosticEpisodeConfig(
                task_suite_path=TASK_SUITE_PATH,
                robot_configuration_id=ROBOT_ID,
                task_id=TASK_ID,
                replicate_id="R1",
                driver_path=DRIVER_PATH,
                capability_design_path=CAPABILITY_DESIGN_PATH,
                output_dir=output_path.parent,
                record_video=record_video,
                wall_timeout_s=wall_timeout_s,
            ),
            package=package,
            model=model,
            budgets=budgets,
        )
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
        }

    calls = model.provider_call_records
    exchanges = model.provider_exchange_records
    provider = _provider_summary(
        provider_pin=pin,
        provider_path=provider_path,
        calls=calls,
        exchanges=exchanges,
    )
    controller = episode.get("controller", {}) if episode is not None else {}
    worker = episode.get("worker", {}) if episode is not None else {}
    harness = episode.get("harness", {}) if episode is not None else {}
    capability_calls = controller.get("capability_calls")
    summary = {
        "real_model_request_succeeded": provider["successful_model_requests"] > 0,
        "returned_model_matches_pin": provider["returned_model_matches_pin"],
        "controller_status": controller.get("status"),
        "model_calls": controller.get("model_calls"),
        "capability_calls": capability_calls,
        "reference_driver_was_invoked": isinstance(capability_calls, int)
        and capability_calls > 0,
        "worker_completed": worker.get("worker_completed"),
        "task_metric_passed": harness.get("task_metric_passed"),
        "physical_execution_passed": harness.get("physical_execution_passed"),
        "physical_integrity_passed": harness.get("physical_integrity_passed"),
        "video_complete": harness.get("video_complete"),
        "independent_harness_verdict": harness.get("physical_harness_verdict"),
    }
    summary["video_requirement_satisfied"] = bool(
        not record_video or summary["video_complete"] is True
    )
    summary["diagnostic_chain_completed"] = bool(
        error is None
        and summary["real_model_request_succeeded"]
        and summary["returned_model_matches_pin"]
        and summary["reference_driver_was_invoked"]
        and summary["worker_completed"] is True
        and summary["video_requirement_satisfied"]
    )
    report = {
        "artifact_type": "b2_recap_real_model_diagnostic",
        "formal_episode": False,
        "formal_denominator_entry": False,
        "video_requested": record_video,
        "authority": {"document_id": "AA2-B2", "revision": "0.1.0"},
        "code_version": _code_version(),
        "started_at_utc": started_at_utc,
        "ended_at_utc": _utc_now(),
        "elapsed_s": max(0.0, time.monotonic() - started),
        "credential_handling": {
            "source_file": str(env_path),
            "credential_env": credential_env,
            "credential_value_recorded": False,
            "candidate_worker_receives_credentials": False,
        },
        "fixed_controller_budgets": asdict(budgets),
        "fixed_controller_contract": {
            "system_prompt": RECAP_B2_SYSTEM_PROMPT,
            "response_schema": RECAP_RESPONSE_SCHEMA,
            "provider_response_format": {"type": "json_object"},
            "strict_validation_boundary": "local_recap_parser",
        },
        "provider": provider,
        "summary": summary,
        "error": error,
        "episode": episode,
        "known_evidence_limit": (
            "This provider canary is diagnostic and cannot enter the AA2-B2 "
            "210-episode formal denominator while Section 7 remains uncleared."
        ),
    }
    serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if credential in serialized:
        raise RuntimeError("refusing to persist a report containing the credential")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--provider-config", type=Path, default=DEFAULT_PROVIDER_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-timeout-s", type=float, default=900.0)
    parser.add_argument("--record-video", action="store_true")
    args = parser.parse_args()
    report = run(
        env_path=args.env_file,
        provider_path=args.provider_config,
        output_path=args.output,
        wall_timeout_s=args.wall_timeout_s,
        record_video=args.record_video,
    )
    console = {
        **report["summary"],
        "elapsed_s": report["elapsed_s"],
        "report_path": str(args.output.resolve()),
        "error": report["error"],
    }
    print(json.dumps(console, allow_nan=False))
    return 0 if report["summary"]["diagnostic_chain_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
