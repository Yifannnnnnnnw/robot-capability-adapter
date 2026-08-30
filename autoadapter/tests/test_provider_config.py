from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from autoadapter2.model_api import ModelConfig, ModelInvocationError
from autoadapter2.provider_config import (
    HOLISTICAI_ROUTE_PROFILE_ID,
    HOLISTICAI_ROUTE_PROFILE_PATH,
    ProviderConfigError,
    reject_inline_holisticai_route_fields,
    resolve_holisticai_route_profile,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_REFERENCE = {
    "path": HOLISTICAI_ROUTE_PROFILE_PATH,
    "profile_id": HOLISTICAI_ROUTE_PROFILE_ID,
}


def _canonical_profile() -> dict[str, object]:
    return json.loads(
        (REPOSITORY_ROOT / HOLISTICAI_ROUTE_PROFILE_PATH).read_text(encoding="utf-8")
    )


def _write_profile(repository_root: Path, profile: dict[str, object]) -> dict[str, str]:
    repository_root.mkdir()
    profile_path = repository_root / HOLISTICAI_ROUTE_PROFILE_PATH
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return dict(PROFILE_REFERENCE)


def test_canonical_holisticai_profile_resolves_to_one_secret_free_route() -> None:
    route = resolve_holisticai_route_profile(PROFILE_REFERENCE, REPOSITORY_ROOT)

    assert route.path == HOLISTICAI_ROUTE_PROFILE_PATH
    assert route.profile_path == HOLISTICAI_ROUTE_PROFILE_PATH
    assert route.profile_id == HOLISTICAI_ROUTE_PROFILE_ID
    assert route.endpoint_url == (
        "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou."
        "lambda-url.eu-west-2.on.aws/v1/chat/completions"
    )
    assert route.endpoint_region == "eu-west-2"
    assert route.credential_env == "AUTOADAPTER_HOLISTICAI_API_KEY"
    assert route.auth_header == "X-Api-Key"
    assert route.auth_prefix == ""
    assert route.maximum_request_timeout_s == 120
    evidence = route.to_evidence_dict()
    assert evidence["reference"] == PROFILE_REFERENCE
    assert evidence["resolved_route"]["endpoint_url"] == route.endpoint_url
    assert "api_key" not in evidence["resolved_route"]


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("schema_version", 2, "schema_version"),
        ("profile_id", "wrong-profile", "profile.profile_id"),
        ("base_url", "http://gateway.example/v1", "HTTPS URL"),
        ("endpoint_path", "/responses", "endpoint_path"),
        ("endpoint_region", "us-east-1", "endpoint_region"),
        ("credential_env", "INLINE_KEY", "credential_env"),
        ("auth_header", "Authorization", "auth_header"),
        ("auth_prefix", "Bearer ", "auth_prefix"),
        ("maximum_request_timeout_s", 121, "maximum_request_timeout_s"),
    ],
)
def test_profile_rejects_noncanonical_route_values(
    tmp_path: Path,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    profile = _canonical_profile()
    profile[field] = invalid_value
    repository_root = tmp_path / "repository"
    reference = _write_profile(repository_root, profile)

    with pytest.raises(ProviderConfigError, match=message):
        resolve_holisticai_route_profile(reference, repository_root)


def test_profile_rejects_schema_drift_and_wrong_profile_id(tmp_path: Path) -> None:
    profile = _canonical_profile()
    profile["extra_route_field"] = "not-versioned"
    repository_root = tmp_path / "repository"
    reference = _write_profile(repository_root, profile)
    with pytest.raises(ProviderConfigError, match="schema is invalid"):
        resolve_holisticai_route_profile(reference, repository_root)

    with pytest.raises(ProviderConfigError, match="reference.profile_id"):
        resolve_holisticai_route_profile(
            {"path": HOLISTICAI_ROUTE_PROFILE_PATH, "profile_id": "wrong-profile"},
            repository_root,
        )


def test_profile_path_must_remain_inside_repository(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_canonical_profile()), encoding="utf-8")

    with pytest.raises(ProviderConfigError, match="inside repository_root"):
        resolve_holisticai_route_profile(
            {"path": "../outside.json", "profile_id": HOLISTICAI_ROUTE_PROFILE_ID},
            repository_root,
        )


def test_profile_requires_the_exact_versioned_repository_path(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    alternate_path = repository_root / "alternate.json"
    alternate_path.write_text(json.dumps(_canonical_profile()), encoding="utf-8")

    with pytest.raises(ProviderConfigError, match="profile path must be"):
        resolve_holisticai_route_profile(
            {"path": "alternate.json", "profile_id": HOLISTICAI_ROUTE_PROFILE_ID},
            repository_root,
        )


def test_inline_route_fields_are_rejected_but_classification_and_timeout_are_allowed() -> None:
    reject_inline_holisticai_route_fields(
        {
            "deployment_mode": "holisticai-hosted-api",
            "api_route_kind": "holisticai-gateway",
            "transport": "openai-compatible",
            "request_timeout_s": 120,
        },
        label="provider pin",
    )

    with pytest.raises(ProviderConfigError, match="must not inline"):
        reject_inline_holisticai_route_fields(
            {"endpoint_base_url": "https://override.example/v1"},
            label="provider pin",
        )
    with pytest.raises(ProviderConfigError, match="deployment_mode"):
        reject_inline_holisticai_route_fields(
            {"deployment_mode": "company-hosted-api"},
            label="provider pin",
        )
    with pytest.raises(ProviderConfigError, match="must not inline"):
        resolve_holisticai_route_profile(
            {**PROFILE_REFERENCE, "auth_header": "Authorization"},
            REPOSITORY_ROOT,
        )


def test_model_config_uses_custom_endpoint_path_exactly_once() -> None:
    custom = ModelConfig(
        provider="holisticai",
        model="model",
        base_url="https://gateway.example/v1",
        api_key="secret-value",
        endpoint_path="/responses",
    )
    already_complete = ModelConfig(
        provider="holisticai",
        model="model",
        base_url="https://gateway.example/v1/responses/",
        api_key="secret-value",
        endpoint_path="/responses",
    )

    assert custom.endpoint_url == "https://gateway.example/v1/responses"
    assert already_complete.endpoint_url == "https://gateway.example/v1/responses"
    assert ModelConfig(
        provider="holisticai",
        model="model",
        base_url="https://gateway.example/v1",
        api_key="secret-value",
    ).endpoint_url == "https://gateway.example/v1/chat/completions"


def test_model_config_from_env_reads_and_validates_endpoint_path() -> None:
    environment = {
        "AUTOADAPTER_MODEL_PROVIDER": "openai-compatible",
        "AUTOADAPTER_MODEL_ID": "model",
        "AUTOADAPTER_MODEL_API_BASE_URL": "https://gateway.example/v1",
        "AUTOADAPTER_MODEL_API_KEY": "secret-value",
        "AUTOADAPTER_MODEL_API_ENDPOINT_PATH": "/responses",
    }
    with mock.patch.dict(os.environ, environment, clear=True):
        config = ModelConfig.from_env()
    assert config.endpoint_path == "/responses"
    assert config.endpoint_url == "https://gateway.example/v1/responses"

    environment["AUTOADAPTER_MODEL_API_ENDPOINT_PATH"] = "/chat//completions"
    with mock.patch.dict(os.environ, environment, clear=True):
        with pytest.raises(ModelInvocationError, match="ENDPOINT_PATH"):
            ModelConfig.from_env()
