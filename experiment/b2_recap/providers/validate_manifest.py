#!/usr/bin/env python3
"""Validate the B2-local seven-backbone pin and common ReCAP transport."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autoadapter2.b2.model_client import (  # noqa: E402
    B2ModelProviderConfig,
    _SCHEMA_INSTRUCTION,
)
from autoadapter2.b2.recap import (  # noqa: E402
    RECAP_B2_SYSTEM_PROMPT,
    RECAP_RESPONSE_SCHEMA,
    RecapBudgets,
)
from autoadapter2.model_api import (  # noqa: E402
    _MAX_PHYSICAL_REQUESTS,
    _RETRYABLE_HTTP_STATUSES,
    _RETRY_BACKOFF_S,
)


DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest.json")
_PROVIDER_ROOT = REPOSITORY_ROOT / "experiment" / "experiment1" / "providers"
_EXPECTED_CONFIGS = {
    "M1": "M1-company-api-sonnet-4-6.json",
    "M2": "M2-company-api-opus-5.json",
    "M3": "M3-company-api-haiku-4-5.json",
    "M4": "M4-company-api-nova-pro.json",
    "M5": "M5-deepseek-v4-pro.json",
    "M6": "M6-company-api-ministral-3-8b.json",
    "M7": "M7-company-api-qwen3-32b.json",
}
_EXPECTED_FAMILIES = {
    "M1": "Sonnet 4.6",
    "M2": "Opus 5",
    "M3": "Haiku 4.5",
    "M4": "Nova Pro",
    "M5": "DeepSeek V4 Pro",
    "M6": "Ministral 3 8B",
    "M7": "Qwen3 32B",
}


class ProviderManifestError(ValueError):
    """Raised when a B2 provider pin or its common policy is incomplete."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ProviderManifestError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ProviderManifestError(f"{label} must contain one JSON object")
    return value


def _required_string(value: Any, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ProviderManifestError(f"{label} must be a string")
    return value


def _positive_integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProviderManifestError(f"{label} must be a positive integer")
    return value


def _positive_number(value: Any, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ProviderManifestError(f"{label} must be positive and finite")
    return float(value)


def _expected_common_transport() -> dict[str, Any]:
    return {
        "transport": "openai-compatible",
        "endpoint_path": "/chat/completions",
        "temperature": 0.0,
        "max_tokens": 4096,
        "timeout_s": 120,
        "tool_history_mode": "native",
        "history_char_budget": 80000,
        "response_format": {"type": "json_object"},
        "schema_transport": "fixed_final_user_message",
        "schema_instruction": _SCHEMA_INSTRUCTION,
        "strict_validation_boundary": "autoadapter2.b2.recap._parse_model_output",
        "semantic_retry": False,
        "retry": {
            "maximum_physical_requests_per_model_turn": _MAX_PHYSICAL_REQUESTS,
            "retryable_http_statuses": sorted(_RETRYABLE_HTTP_STATUSES),
            "backoff_s": _RETRY_BACKOFF_S,
        },
        "provider_thinking_control": "source-pinned mechanical transport only",
    }


def _validate_provider_source(
    *,
    backbone_id: str,
    raw_path: Any,
    manifest_path: Path,
    common: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    relative = Path(_required_string(raw_path, label=f"{backbone_id} source path"))
    if relative.is_absolute():
        raise ProviderManifestError(f"{backbone_id} source path must be relative")
    path = (manifest_path.parent / relative).resolve()
    try:
        path.relative_to(_PROVIDER_ROOT.resolve())
    except ValueError as exc:
        raise ProviderManifestError(
            f"{backbone_id} source path escapes Experiment 1 providers"
        ) from exc
    if path.name != _EXPECTED_CONFIGS[backbone_id]:
        raise ProviderManifestError(f"{backbone_id} points to the wrong source pin")

    source = _read_object(path, label=f"{backbone_id} source provider config")
    if source.get("schema_version") != 1:
        raise ProviderManifestError(f"{backbone_id} source schema is unsupported")
    if source.get("backbone_id") != backbone_id:
        raise ProviderManifestError(f"{backbone_id} source identity does not match")
    if source.get("family") != _EXPECTED_FAMILIES[backbone_id]:
        raise ProviderManifestError(f"{backbone_id} family does not match AA2-B2")
    for field in (
        "vendor",
        "deployment_mode",
        "api_route_kind",
        "exact_model_id",
        "provider_model_revision",
        "credential_env",
        "auth_header",
    ):
        _required_string(source.get(field), label=f"{backbone_id}.{field}")
    _required_string(
        source.get("auth_prefix"),
        label=f"{backbone_id}.auth_prefix",
        allow_empty=True,
    )
    if source.get("transport") != common["transport"]:
        raise ProviderManifestError(f"{backbone_id} transport is not compatible")
    if source.get("endpoint_path") != common["endpoint_path"]:
        raise ProviderManifestError(f"{backbone_id} endpoint path is not compatible")
    base_url = _required_string(
        source.get("endpoint_base_url"), label=f"{backbone_id}.endpoint_base_url"
    )
    if urlparse(base_url).scheme != "https":
        raise ProviderManifestError(f"{backbone_id} endpoint must use HTTPS")
    if source.get("deployment_mode") == "company-hosted-api":
        _required_string(
            source.get("endpoint_region"), label=f"{backbone_id}.endpoint_region"
        )

    context_limit = _positive_integer(
        source.get("context_limit_tokens"),
        label=f"{backbone_id}.context_limit_tokens",
    )
    output_limit = _positive_integer(
        source.get("provider_max_output_tokens"),
        label=f"{backbone_id}.provider_max_output_tokens",
    )
    if common["max_tokens"] > output_limit:
        raise ProviderManifestError(
            f"common max_tokens exceeds {backbone_id} provider output limit"
        )
    if common["max_tokens"] >= context_limit:
        raise ProviderManifestError(
            f"common max_tokens leaves no input context for {backbone_id}"
        )

    inference = source.get("inference_settings")
    if not isinstance(inference, dict):
        raise ProviderManifestError(f"{backbone_id} has no inference_settings")
    if inference.get("thinking") is not None and not isinstance(
        inference.get("thinking"), str
    ):
        raise ProviderManifestError(f"{backbone_id}.thinking is invalid")

    price = source.get("price_snapshot")
    if not isinstance(price, dict):
        raise ProviderManifestError(f"{backbone_id} has no price snapshot")
    for field in ("snapshot_date", "currency", "unit", "source"):
        _required_string(price.get(field), label=f"{backbone_id}.price_snapshot.{field}")
    for field in ("input_cache_miss", "output"):
        value = price.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise ProviderManifestError(
                f"{backbone_id}.price_snapshot.{field} must be non-negative"
            )

    return path, source


def validate_manifest_document(
    document: dict[str, Any], *, manifest_path: Path = DEFAULT_MANIFEST_PATH
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if document.get("artifact_type") != "b2_recap_provider_manifest":
        raise ProviderManifestError("B2 provider manifest artifact_type is invalid")
    if document.get("schema_version") != "1.0":
        raise ProviderManifestError("B2 provider manifest schema_version is invalid")
    authority = document.get("authority")
    if authority != {
        "document_id": "AA2-B2",
        "revision": "0.1.1",
        "sections": ["2.1", "4", "7.3"],
    }:
        raise ProviderManifestError("B2 provider authority pin is invalid")
    if document.get("diagnostic_only") is not True:
        raise ProviderManifestError("B2 provider preparation must remain diagnostic")
    if document.get("formal_dispatch_enabled") is not False:
        raise ProviderManifestError("B2 formal dispatch must remain disabled")

    expected_controller = {
        "system_prompt": RECAP_B2_SYSTEM_PROMPT,
        "response_schema": RECAP_RESPONSE_SCHEMA,
        "budgets": asdict(RecapBudgets()),
    }
    if document.get("common_controller_contract") != expected_controller:
        raise ProviderManifestError("common ReCAP prompt/schema/budgets do not match code")

    common = document.get("common_transport_policy")
    if common != _expected_common_transport():
        raise ProviderManifestError("common B2 transport policy does not match runtime")

    identity = document.get("identity_evidence_policy")
    if not isinstance(identity, dict):
        raise ProviderManifestError("identity evidence policy is absent")
    if identity.get("require_returned_model_exact_match") is not True:
        raise ProviderManifestError("returned model identity must match its pin")
    if identity.get("upstream_revision_status") != "not_independently_verifiable":
        raise ProviderManifestError("upstream revision limitation must remain explicit")
    _required_string(identity.get("limitation"), label="identity limitation")

    diagnostic = document.get("connectivity_diagnostic")
    if not isinstance(diagnostic, dict):
        raise ProviderManifestError("connectivity diagnostic definition is absent")
    if diagnostic.get("logical_requests_per_backbone") != 1:
        raise ProviderManifestError("connectivity must use one logical request per backbone")
    if diagnostic.get("formal_episode") is not False or diagnostic.get(
        "formal_denominator_entry"
    ) is not False:
        raise ProviderManifestError("connectivity checks cannot be formal episodes")
    message = diagnostic.get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "user"
        or not isinstance(message.get("content"), str)
        or not message["content"]
    ):
        raise ProviderManifestError("connectivity diagnostic message is invalid")

    sources = document.get("provider_runtime_configs")
    if not isinstance(sources, dict) or list(sources) != list(_EXPECTED_CONFIGS):
        raise ProviderManifestError("B2 provider set must be ordered M1 through M7")
    providers: dict[str, dict[str, Any]] = {}
    provider_paths: dict[str, Path] = {}
    for backbone_id, raw_path in sources.items():
        source_path, source = _validate_provider_source(
            backbone_id=backbone_id,
            raw_path=raw_path,
            manifest_path=manifest_path,
            common=common,
        )
        providers[backbone_id] = source
        provider_paths[backbone_id] = source_path

    return {
        "manifest_path": manifest_path,
        "manifest": document,
        "providers": providers,
        "provider_paths": provider_paths,
        "common_transport_policy": common,
    }


def load_and_validate_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    resolved = path.resolve()
    return validate_manifest_document(
        _read_object(resolved, label="B2 provider manifest"),
        manifest_path=resolved,
    )


def provider_model_config(
    resolved: dict[str, Any], *, backbone_id: str
) -> B2ModelProviderConfig:
    providers = resolved["providers"]
    if backbone_id not in providers:
        raise ProviderManifestError(f"unknown B2 backbone {backbone_id!r}")
    source = providers[backbone_id]
    common = resolved["common_transport_policy"]
    inference = source["inference_settings"]
    return B2ModelProviderConfig(
        provider=str(source["vendor"]).strip().lower(),
        model=str(source["exact_model_id"]),
        base_url=str(source["endpoint_base_url"]).rstrip("/"),
        api_protocol=str(common["transport"]),
        auth_header=str(source["auth_header"]),
        auth_prefix=str(source["auth_prefix"]),
        thinking=inference.get("thinking"),
        timeout_s=float(common["timeout_s"]),
        max_tokens=int(common["max_tokens"]),
        history_char_budget=int(common["history_char_budget"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args()
    resolved = load_and_validate_manifest(args.manifest)
    output = {
        "manifest_path": str(resolved["manifest_path"]),
        "backbone_ids": list(resolved["providers"]),
        "common_transport_policy": resolved["common_transport_policy"],
        "formal_dispatch_enabled": False,
    }
    print(json.dumps(output, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
