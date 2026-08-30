"""Shared provider-route configuration for formal AutoAdapter experiments."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HOLISTICAI_ROUTE_PROFILE_ID = "holisticai-gateway-long-request-eu-west-2-v1"
HOLISTICAI_ROUTE_PROFILE_PATH = (
    "autoadapter/configs/providers/"
    "holisticai-gateway-long-request-eu-west-2-v1.json"
)
HOLISTICAI_MAXIMUM_REQUEST_TIMEOUT_S = 120

_PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "profile_id",
        "deployment_mode",
        "api_protocol",
        "base_url",
        "endpoint_path",
        "endpoint_region",
        "credential_env",
        "auth_header",
        "auth_prefix",
        "maximum_request_timeout_s",
    }
)
_PROFILE_VALUES = {
    "profile_id": HOLISTICAI_ROUTE_PROFILE_ID,
    "deployment_mode": "holisticai-hosted-api",
    "api_protocol": "openai-compatible",
    "base_url": (
        "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou."
        "lambda-url.eu-west-2.on.aws/v1"
    ),
    "endpoint_path": "/chat/completions",
    "endpoint_region": "eu-west-2",
    "credential_env": "AUTOADAPTER_HOLISTICAI_API_KEY",
    "auth_header": "X-Api-Key",
    "auth_prefix": "",
    "maximum_request_timeout_s": HOLISTICAI_MAXIMUM_REQUEST_TIMEOUT_S,
}
_INLINE_ROUTE_FIELDS = frozenset(
    {
        "base_url",
        "endpoint_base_url",
        "endpoint_url",
        "endpoint_path",
        "endpoint_region",
        "region",
        "credential_env",
        "credential_file",
        "api_key",
        "auth_header",
        "auth_prefix",
    }
)
_CLASSIFICATION_FIELDS = {
    "deployment_mode": "holisticai-hosted-api",
    "api_route_kind": "holisticai-gateway",
    "api_protocol": "openai-compatible",
    "transport": "openai-compatible",
}


class ProviderConfigError(ValueError):
    """Raised when a shared provider profile or its reference is invalid."""


@dataclass(frozen=True)
class HolisticAIRouteProfile:
    """Validated, secret-free view of the holisticai gateway route."""

    path: str
    profile_id: str
    deployment_mode: str
    api_protocol: str
    base_url: str
    endpoint_path: str
    endpoint_region: str
    credential_env: str
    auth_header: str
    auth_prefix: str
    maximum_request_timeout_s: int

    @property
    def profile_path(self) -> str:
        return self.path

    @property
    def endpoint_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint_path.lstrip("/")

    def to_reference_dict(self) -> dict[str, str]:
        return {"path": self.path, "profile_id": self.profile_id}

    def to_route_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "deployment_mode": self.deployment_mode,
            "api_protocol": self.api_protocol,
            "base_url": self.base_url,
            "endpoint_path": self.endpoint_path,
            "endpoint_url": self.endpoint_url,
            "endpoint_region": self.endpoint_region,
            "credential_env": self.credential_env,
            "auth_header": self.auth_header,
            "auth_prefix": self.auth_prefix,
            "maximum_request_timeout_s": self.maximum_request_timeout_s,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "reference": self.to_reference_dict(),
            "resolved_route": self.to_route_dict(),
        }


def reject_inline_holisticai_route_fields(
    owner_mapping: Mapping[str, Any],
    *,
    label: str = "holisticai route owner",
) -> None:
    """Reject route data that must come only from the shared profile."""

    if not isinstance(owner_mapping, Mapping):
        raise ProviderConfigError(f"{label} must be an object")
    inline = sorted(_INLINE_ROUTE_FIELDS.intersection(owner_mapping))
    if inline:
        raise ProviderConfigError(
            f"{label} must not inline holisticai route fields: {', '.join(inline)}"
        )
    for field, expected_value in _CLASSIFICATION_FIELDS.items():
        if field in owner_mapping and owner_mapping[field] != expected_value:
            raise ProviderConfigError(f"{label}.{field} must be {expected_value!r}")


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ProviderConfigError(f"{label} schema is invalid: {'; '.join(details)}")


def resolve_holisticai_route_profile(
    reference: Mapping[str, Any],
    repository_root: str | Path,
) -> HolisticAIRouteProfile:
    """Load and validate one repository-contained holisticai route profile."""

    if not isinstance(reference, Mapping):
        raise ProviderConfigError("holisticai route profile reference must be an object")
    reject_inline_holisticai_route_fields(
        reference, label="holisticai route profile reference"
    )
    _require_exact_keys(
        reference,
        frozenset({"path", "profile_id"}),
        label="holisticai route profile reference",
    )

    profile_id = reference["profile_id"]
    if profile_id != HOLISTICAI_ROUTE_PROFILE_ID:
        raise ProviderConfigError(
            "holisticai route profile reference.profile_id must be "
            f"{HOLISTICAI_ROUTE_PROFILE_ID!r}"
        )
    raw_path = reference["path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ProviderConfigError(
            "holisticai route profile reference.path must be a non-empty string"
        )

    try:
        root = Path(repository_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProviderConfigError("repository_root does not resolve") from exc
    if not root.is_dir():
        raise ProviderConfigError("repository_root must be a directory")
    try:
        profile_path = (root / raw_path).resolve()
        relative_path = profile_path.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProviderConfigError(
            "holisticai route profile path must remain inside repository_root"
        ) from exc
    if relative_path != HOLISTICAI_ROUTE_PROFILE_PATH:
        raise ProviderConfigError(
            "holisticai route profile path must be "
            f"{HOLISTICAI_ROUTE_PROFILE_PATH!r}"
        )
    if not profile_path.is_file():
        raise ProviderConfigError("holisticai route profile path must name a file")

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderConfigError("holisticai route profile must be valid JSON") from exc
    if not isinstance(profile, dict):
        raise ProviderConfigError("holisticai route profile must be a JSON object")
    _require_exact_keys(profile, _PROFILE_KEYS, label="holisticai route profile")

    schema_version = profile["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise ProviderConfigError(
            "holisticai route profile.schema_version must be integer 1"
        )
    if profile["profile_id"] != profile_id:
        raise ProviderConfigError(
            "holisticai route profile.profile_id does not match its reference"
        )

    base_url = profile["base_url"]
    if not isinstance(base_url, str):
        raise ProviderConfigError("holisticai route profile.base_url must be text")
    try:
        parsed_base_url = urlsplit(base_url)
        invalid_base_url = (
            parsed_base_url.scheme != "https"
            or not parsed_base_url.netloc
            or parsed_base_url.query
            or parsed_base_url.fragment
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
        )
    except ValueError:
        invalid_base_url = True
    if invalid_base_url:
        raise ProviderConfigError(
            "holisticai route profile.base_url must be an HTTPS URL without "
            "credentials, query, or fragment"
        )

    for field, expected_value in _PROFILE_VALUES.items():
        actual_value = profile[field]
        if field == "maximum_request_timeout_s" and type(actual_value) is not int:
            raise ProviderConfigError(
                "holisticai route profile.maximum_request_timeout_s must be integer 120"
            )
        if actual_value != expected_value:
            raise ProviderConfigError(
                f"holisticai route profile.{field} must be {expected_value!r}"
            )

    return HolisticAIRouteProfile(
        path=relative_path,
        profile_id=profile["profile_id"],
        deployment_mode=profile["deployment_mode"],
        api_protocol=profile["api_protocol"],
        base_url=profile["base_url"],
        endpoint_path=profile["endpoint_path"],
        endpoint_region=profile["endpoint_region"],
        credential_env=profile["credential_env"],
        auth_header=profile["auth_header"],
        auth_prefix=profile["auth_prefix"],
        maximum_request_timeout_s=profile["maximum_request_timeout_s"],
    )


def load_holisticai_gateway_profile(
    reference: Mapping[str, Any],
    repository_root: str | Path,
) -> HolisticAIRouteProfile:
    """Compatibility name for consumers that load the shared gateway profile."""

    return resolve_holisticai_route_profile(reference, repository_root)
