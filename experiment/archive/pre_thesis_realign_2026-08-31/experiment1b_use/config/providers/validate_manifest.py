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


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
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
from autoadapter2.provider_config import (  # noqa: E402
    reject_inline_holisticai_route_fields,
    resolve_holisticai_route_profile,
)


DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest.json")
_PROVIDER_ROOT = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1a_generation"
    / "config"
    / "providers"
)
_EXPECTED_CONFIGS = {
    "M1": "M1-holisticai-sonnet-4-6.json",
    "M2": "M2-holisticai-opus-5.json",
    "M3": "M3-holisticai-haiku-4-5.json",
    "M4": "M4-holisticai-nova-pro.json",
    "M5": "M5-deepseek-v4-pro.json",
    "M6": "M6-holisticai-ministral-3-8b.json",
    "M8": "M8-holisticai-gpt-5-6-sol.json",
}
_EXPECTED_FAMILIES = {
    "M1": "Sonnet 4.6",
    "M2": "Opus 5",
    "M3": "Haiku 4.5",
    "M4": "Nova Pro",
    "M5": "DeepSeek V4 Pro",
    "M6": "Ministral 3 8B",
    "M8": "GPT-5.6 Sol",
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
) -> tuple[Path, dict[str, Any], Any | None]:
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
    ):
        _required_string(source.get(field), label=f"{backbone_id}.{field}")
    if backbone_id == "M8":
        if source.get("provider_model_revision") is not None:
            raise ProviderManifestError(
                "M8.provider_model_revision must remain explicitly unresolved"
            )
        if source.get("upstream_revision_status") != "not_independently_verifiable":
            raise ProviderManifestError(
                "M8 upstream revision limitation must remain explicit"
            )
        if source.get("expected_returned_model_id") != source.get("exact_model_id"):
            raise ProviderManifestError(
                "M8 expected returned model must equal its requested model pin"
            )
    else:
        _required_string(
            source.get("provider_model_revision"),
            label=f"{backbone_id}.provider_model_revision",
        )
    if source.get("transport") != common["transport"]:
        raise ProviderManifestError(f"{backbone_id} transport is not compatible")
    route_profile = None
    if backbone_id == "M5":
        if source.get("deployment_mode") != "vendor-direct-hosted-api":
            raise ProviderManifestError("M5 must remain vendor-direct-hosted-api")
        for field in (
            "endpoint_base_url",
            "endpoint_path",
            "credential_env",
            "auth_header",
        ):
            _required_string(source.get(field), label=f"{backbone_id}.{field}")
        _required_string(
            source.get("auth_prefix"),
            label=f"{backbone_id}.auth_prefix",
            allow_empty=True,
        )
        if source.get("endpoint_path") != "/chat/completions":
            raise ProviderManifestError("M5 endpoint path is not compatible")
        if urlparse(str(source["endpoint_base_url"])).scheme != "https":
            raise ProviderManifestError("M5 endpoint must use HTTPS")
    else:
        if source.get("deployment_mode") != "holisticai-hosted-api":
            raise ProviderManifestError(
                f"{backbone_id} must use holisticai-hosted-api"
            )
        if source.get("api_route_kind") != "holisticai-gateway":
            raise ProviderManifestError(
                f"{backbone_id} must use the holisticai gateway"
            )
        try:
            reject_inline_holisticai_route_fields(
                source, label=f"{backbone_id} provider pin"
            )
            route_profile = resolve_holisticai_route_profile(
                source.get("holisticai_route_profile"), REPOSITORY_ROOT
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ProviderManifestError(
                f"{backbone_id} holisticai route profile is invalid: {exc}"
            ) from exc
        if (
            route_profile.deployment_mode != source.get("deployment_mode")
            or route_profile.api_protocol != source.get("transport")
        ):
            raise ProviderManifestError(
                f"{backbone_id} classifications differ from the holisticai profile"
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
    if backbone_id == "M8" and (
        context_limit != 1_050_000
        or output_limit != 128_000
        or source.get("limits_scope")
        != (
            "OpenAI public model specification; holisticai-gateway enforcement "
            "not independently verified"
        )
        or source.get("limits_source")
        != "https://developers.openai.com/api/docs/models/gpt-5.6-sol"
    ):
        raise ProviderManifestError("M8 public model limits are not the fixed pin")

    inference = source.get("inference_settings")
    if not isinstance(inference, dict):
        raise ProviderManifestError(f"{backbone_id} has no inference_settings")
    if inference.get("thinking") is not None and not isinstance(
        inference.get("thinking"), str
    ):
        raise ProviderManifestError(f"{backbone_id}.thinking is invalid")
    if route_profile is not None:
        timeout = inference.get("timeout_s")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or float(timeout) <= 0
            or float(timeout) > route_profile.maximum_request_timeout_s
            or float(common["timeout_s"])
            > route_profile.maximum_request_timeout_s
        ):
            raise ProviderManifestError(
                f"{backbone_id} timeout exceeds the holisticai profile maximum"
            )

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
    if backbone_id == "M8" and price != {
        "snapshot_date": "2026-08-23",
        "currency": "USD",
        "unit": "per_1m_tokens",
        "input_cache_hit": 0.5,
        "input_cache_miss": 5.0,
        "output": 30.0,
        "long_context": {
            "applies_when_input_tokens_gt": 272000,
            "input_cache_hit": 1.0,
            "input_cache_miss": 10.0,
            "output": 45.0,
        },
        "cost_basis": "public_standard_reference_estimate",
        "pricing_scope": (
            "OpenAI public Standard API reference; "
            "holisticai-gateway billing not independently verified"
        ),
        "source": "https://platform.openai.com/pricing",
    }:
        raise ProviderManifestError("M8 public reference price pin is invalid")

    return path, source, route_profile


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
        "revision": "0.1.4",
        "sections": ["2.1", "4", "7.3"],
    }:
        raise ProviderManifestError("B2 provider authority pin is invalid")
    if document.get("diagnostic_only") is not False:
        raise ProviderManifestError("B2 provider pins must be formal inputs")
    if document.get("formal_dispatch_enabled") is not True:
        raise ProviderManifestError("B2 provider dispatch must be enabled")
    if document.get("unresolved_price_backbones") != []:
        raise ProviderManifestError("B2 provider price blockers must be empty")

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
        raise ProviderManifestError("B2 provider set must be ordered M1 through M6 and M8")
    providers: dict[str, dict[str, Any]] = {}
    provider_paths: dict[str, Path] = {}
    route_profiles: dict[str, Any | None] = {}
    for backbone_id, raw_path in sources.items():
        source_path, source, route_profile = _validate_provider_source(
            backbone_id=backbone_id,
            raw_path=raw_path,
            manifest_path=manifest_path,
            common=common,
        )
        providers[backbone_id] = source
        provider_paths[backbone_id] = source_path
        route_profiles[backbone_id] = route_profile
    unresolved_price_backbones: list[str] = []
    if unresolved_price_backbones != document["unresolved_price_backbones"]:
        raise ProviderManifestError(
            "B2 provider unresolved price blocker does not match source pins"
        )

    return {
        "manifest_path": manifest_path,
        "manifest": document,
        "providers": providers,
        "provider_paths": provider_paths,
        "route_profiles": route_profiles,
        "common_transport_policy": common,
        "unresolved_price_backbones": unresolved_price_backbones,
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
    route_profile = resolved["route_profiles"][backbone_id]
    if route_profile is None:
        base_url = str(source["endpoint_base_url"]).rstrip("/")
        endpoint_path = str(source["endpoint_path"])
        auth_header = str(source["auth_header"])
        auth_prefix = str(source["auth_prefix"])
    else:
        base_url = route_profile.base_url.rstrip("/")
        endpoint_path = route_profile.endpoint_path
        auth_header = route_profile.auth_header
        auth_prefix = route_profile.auth_prefix
    return B2ModelProviderConfig(
        provider=str(source["vendor"]).strip().lower(),
        model=str(source["exact_model_id"]),
        base_url=base_url,
        endpoint_path=endpoint_path,
        api_protocol=str(common["transport"]),
        auth_header=auth_header,
        auth_prefix=auth_prefix,
        thinking=inference.get("thinking"),
        timeout_s=float(common["timeout_s"]),
        max_tokens=int(common["max_tokens"]),
        history_char_budget=int(common["history_char_budget"]),
    )


def provider_route(
    resolved: dict[str, Any], *, backbone_id: str
) -> dict[str, Any]:
    """Return the resolved non-secret transport for one B2 provider."""

    providers = resolved["providers"]
    if backbone_id not in providers:
        raise ProviderManifestError(f"unknown B2 backbone {backbone_id!r}")
    profile = resolved["route_profiles"][backbone_id]
    if profile is not None:
        evidence = profile.to_evidence_dict()
        return {
            "holisticai_route_profile": evidence["reference"],
            "resolved_route": evidence["resolved_route"],
        }
    source = providers[backbone_id]
    base_url = str(source["endpoint_base_url"]).rstrip("/")
    endpoint_path = str(source["endpoint_path"])
    return {
        "holisticai_route_profile": None,
        "resolved_route": {
            "deployment_mode": source["deployment_mode"],
            "api_protocol": source["transport"],
            "base_url": base_url,
            "endpoint_path": endpoint_path,
            "endpoint_url": base_url + endpoint_path,
            "endpoint_region": source.get("endpoint_region"),
            "credential_env": source["credential_env"],
            "auth_header": source["auth_header"],
            "auth_prefix": source["auth_prefix"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args()
    resolved = load_and_validate_manifest(args.manifest)
    output = {
        "manifest_path": str(resolved["manifest_path"]),
        "backbone_ids": list(resolved["providers"]),
        "common_transport_policy": resolved["common_transport_policy"],
        "unresolved_price_backbones": resolved["unresolved_price_backbones"],
        "formal_dispatch_enabled": True,
    }
    print(json.dumps(output, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
