#!/usr/bin/env python3
"""Run one secret-free structured-output connectivity check for each B2 model."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autoadapter2.b2.model_client import ReCAPJsonModelClient  # noqa: E402
from autoadapter2.task_demo.recap import _parse_model_output  # noqa: E402

from validate_manifest import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    ProviderManifestError,
    load_and_validate_manifest,
    provider_model_config,
    provider_route,
)


DEFAULT_ENV_PATHS = (
    REPOSITORY_ROOT / ".env",
    REPOSITORY_ROOT / ".env.holisticai-api",
)
ClientFactory = Callable[..., Any]


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
    return {
        "git_commit": result.stdout.strip() if result.returncode == 0 else None,
        "runner_path": str(Path(__file__).resolve()),
        "validator_path": str(Path(__file__).with_name("validate_manifest.py")),
    }


def _load_env_files(paths: Sequence[Path]) -> dict[str, str]:
    """Load explicit dotenv files into a mapping without mutating ``os.environ``."""

    values: dict[str, str] = {}
    for raw_path in paths:
        path = raw_path.resolve()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            raise ProviderManifestError(f"credential env file is absent: {path}") from exc
        for line_number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                raise ProviderManifestError(
                    f"invalid dotenv assignment at {path}:{line_number}"
                )
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ProviderManifestError(f"empty dotenv key at {path}:{line_number}")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            previous = values.get(key)
            if previous is not None and previous != value:
                raise ProviderManifestError(
                    f"conflicting dotenv value for {key} across selected files"
                )
            values[key] = value
    return values


def _error_record(exc: BaseException, *, credentials: Sequence[str]) -> dict[str, str]:
    message = str(exc)
    for credential in credentials:
        if credential:
            message = message.replace(credential, "<redacted>")
    return {"type": type(exc).__name__, "message": message[:1000]}


def _records(client: Any, name: str) -> list[dict[str, Any]]:
    value = getattr(client, name, ())
    return [dict(record) for record in value]


def run_connectivity(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    env_paths: Sequence[Path] = DEFAULT_ENV_PATHS,
    output_path: Path | None = None,
    backbone_ids: Sequence[str] | None = None,
    client_factory: ClientFactory = ReCAPJsonModelClient,
) -> dict[str, Any]:
    resolved = load_and_validate_manifest(manifest_path)
    manifest = resolved["manifest"]
    available = list(resolved["providers"])
    selected = available if backbone_ids is None else list(backbone_ids)
    if not selected or len(set(selected)) != len(selected):
        raise ProviderManifestError("selected backbone IDs must be unique and non-empty")
    unknown = [backbone_id for backbone_id in selected if backbone_id not in available]
    if unknown:
        raise ProviderManifestError(f"unknown B2 backbone IDs: {unknown}")

    environment = _load_env_files(env_paths)
    credential_names = {
        str(
            provider_route(resolved, backbone_id=backbone_id)["resolved_route"][
                "credential_env"
            ]
        )
        for backbone_id in selected
    }
    credentials = [environment.get(name, "") for name in credential_names]
    contract = manifest["common_controller_contract"]
    diagnostic = manifest["connectivity_diagnostic"]
    started_at = _utc_now()
    started = time.monotonic()
    levels: list[dict[str, Any]] = []

    for backbone_id in selected:
        source = resolved["providers"][backbone_id]
        route = provider_route(resolved, backbone_id=backbone_id)
        credential_env = str(route["resolved_route"]["credential_env"])
        credential = environment.get(credential_env, "")
        client: Any | None = None
        parsed_output: dict[str, Any] | None = None
        error: dict[str, str] | None = None
        if not credential:
            error = {
                "type": "CredentialUnavailable",
                "message": f"{credential_env} is absent from the selected env files",
            }
        else:
            try:
                client = client_factory(
                    provider_config=provider_model_config(
                        resolved,
                        backbone_id=backbone_id,
                    ),
                    credential=credential,
                )
                raw_output = client.generate_recap_json(
                    stage="b2_provider_connectivity",
                    system_prompt=contract["system_prompt"],
                    messages=[diagnostic["message"]],
                    response_schema=contract["response_schema"],
                )
                parsed_output = _parse_model_output(
                    raw_output,
                    max_subtasks=int(contract["budgets"]["max_subtasks_per_plan"]),
                )
            except Exception as exc:
                error = _error_record(exc, credentials=credentials)

        calls = _records(client, "provider_call_records") if client is not None else []
        exchanges = (
            _records(client, "provider_exchange_records") if client is not None else []
        )
        successful_calls = [call for call in calls if call.get("status") == "success"]
        returned_models = [call.get("returned_model") for call in successful_calls]
        returned_model_matches_pin = bool(successful_calls) and all(
            model == source["exact_model_id"] for model in returned_models
        )
        structured_output_valid = parsed_output is not None
        connectivity_succeeded = bool(
            error is None
            and structured_output_valid
            and returned_model_matches_pin
            and len(exchanges) == 1
            and exchanges[0].get("status") == "success"
        )
        levels.append(
            {
                "backbone_id": backbone_id,
                "family": source["family"],
                "vendor": source["vendor"],
                "source_provider_config": str(resolved["provider_paths"][backbone_id]),
                "requested_model": source["exact_model_id"],
                "declared_provider_model_revision": source["provider_model_revision"],
                "upstream_revision_status": manifest["identity_evidence_policy"][
                    "upstream_revision_status"
                ],
                "holisticai_route_profile": route["holisticai_route_profile"],
                "resolved_route": route["resolved_route"],
                "endpoint_region": route["resolved_route"].get("endpoint_region"),
                "credential_env": credential_env,
                "credential_value_recorded": False,
                "logical_model_requests": 1 if client is not None else 0,
                "physical_model_requests": len(calls),
                "returned_models": returned_models,
                "returned_model_matches_pin": returned_model_matches_pin,
                "structured_output_valid": structured_output_valid,
                "connectivity_succeeded": connectivity_succeeded,
                "parsed_output": parsed_output,
                "price_snapshot": source["price_snapshot"],
                "provider_calls": calls,
                "raw_secret_free_exchanges": exchanges,
                "error": error,
            }
        )

    summary = {
        "declared_backbone_count": len(available),
        "selected_backbone_count": len(selected),
        "connectivity_succeeded_count": sum(
            1 for level in levels if level["connectivity_succeeded"]
        ),
        "all_selected_connected": all(
            level["connectivity_succeeded"] for level in levels
        ),
        "all_returned_models_match_pin": all(
            level["returned_model_matches_pin"] for level in levels
        ),
        "all_structured_outputs_valid": all(
            level["structured_output_valid"] for level in levels
        ),
    }
    report = {
        "artifact_type": "b2_recap_provider_connectivity_diagnostic",
        "schema_version": "1.0",
        "formal_episode": False,
        "formal_denominator_entry": False,
        "authority": manifest["authority"],
        "code_version": _code_version(),
        "manifest_path": str(resolved["manifest_path"]),
        "started_at_utc": started_at,
        "ended_at_utc": _utc_now(),
        "elapsed_s": max(0.0, time.monotonic() - started),
        "common_controller_contract": contract,
        "common_transport_policy": manifest["common_transport_policy"],
        "identity_evidence_policy": manifest["identity_evidence_policy"],
        "connectivity_diagnostic": diagnostic,
        "selected_backbone_ids": selected,
        "summary": summary,
        "levels": levels,
        "known_evidence_limit": (
            "This is a provider connectivity and structured-output diagnostic only. "
            "It is not a robot episode, does not clear the other AA2-B2 Section 7 "
            "prerequisites, and cannot enter the 210-episode denominator."
        ),
    }
    serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
    for credential in credentials:
        if credential and credential in serialized:
            raise RuntimeError("refusing to persist a report containing a credential")
    if output_path is not None:
        resolved_output = output_path.resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(serialized, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--env-file", action="append", type=Path)
    parser.add_argument("--backbone", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_connectivity(
        manifest_path=args.manifest,
        env_paths=tuple(args.env_file) if args.env_file else DEFAULT_ENV_PATHS,
        output_path=args.output,
        backbone_ids=args.backbone,
    )
    console = {
        **report["summary"],
        "elapsed_s": report["elapsed_s"],
        "report_path": str(args.output.resolve()),
        "failures": [
            {
                "backbone_id": level["backbone_id"],
                "error": level["error"],
            }
            for level in report["levels"]
            if not level["connectivity_succeeded"]
        ],
    }
    print(json.dumps(console, allow_nan=False))
    return 0 if report["summary"]["all_selected_connected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
