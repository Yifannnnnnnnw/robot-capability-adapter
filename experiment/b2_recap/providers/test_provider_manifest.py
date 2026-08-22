from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.b2.model_client import ReCAPJsonModelClient

from run_connectivity import _load_env_files, run_connectivity
from validate_manifest import (
    DEFAULT_MANIFEST_PATH,
    ProviderManifestError,
    load_and_validate_manifest,
    provider_model_config,
    validate_manifest_document,
)


def test_manifest_pins_all_levels_and_overrides_source_runtime_differences() -> None:
    resolved = load_and_validate_manifest()

    assert list(resolved["providers"]) == [f"M{index}" for index in range(1, 8)]
    common = resolved["common_transport_policy"]
    assert common["temperature"] == 0.0
    assert common["max_tokens"] == 4096
    assert common["timeout_s"] == 120
    assert common["tool_history_mode"] == "native"
    assert common["history_char_budget"] == 80000
    assert common["semantic_retry"] is False
    assert common["retry"] == {
        "maximum_physical_requests_per_model_turn": 2,
        "retryable_http_statuses": [429, 500, 502, 503, 504],
        "backoff_s": 1.0,
    }

    assert resolved["providers"]["M5"]["inference_settings"]["timeout_s"] == 600
    assert resolved["providers"]["M6"]["inference_settings"][
        "tool_history_mode"
    ] == "text-observation"
    assert resolved["providers"]["M7"]["inference_settings"][
        "tool_history_mode"
    ] == "text-observation"
    for backbone_id in ("M5", "M6", "M7"):
        config = provider_model_config(resolved, backbone_id=backbone_id)
        client = ReCAPJsonModelClient(
            provider_config=config,
            credential="test-parent-credential",
        )
        assert config.max_tokens == 4096
        assert config.timeout_s == 120
        assert config.history_char_budget == 80000
        assert client._client.config.tool_history_mode == "native"


def test_manifest_rejects_common_policy_drift() -> None:
    document = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    changed = copy.deepcopy(document)
    changed["common_transport_policy"]["max_tokens"] = 8192

    with pytest.raises(ProviderManifestError, match="common B2 transport policy"):
        validate_manifest_document(changed)


def test_m7_source_pin_contains_only_the_verified_limit_region_and_price_facts() -> None:
    source = load_and_validate_manifest()["providers"]["M7"]

    assert source["endpoint_region"] == "eu-west-2"
    assert source["context_limit_tokens"] == 32768
    assert source["provider_max_output_tokens"] == 8192
    assert source["model_limit_sources"] == [
        {
            "facts": ["context_limit_tokens", "provider_max_output_tokens"],
            "query_date": "2026-08-22",
            "source": (
                "https://docs.aws.amazon.com/bedrock/latest/userguide/"
                "model-card-qwen-qwen3-32b.html"
            ),
        },
        {
            "facts": ["endpoint_region"],
            "query_date": "2026-08-22",
            "source": (
                "https://docs.aws.amazon.com/bedrock/latest/userguide/"
                "models-region-compatibility.html"
            ),
        },
    ]
    assert source["price_snapshot"] == {
        "snapshot_date": "2026-08-22",
        "source_publication_date": "2026-08-20",
        "query_date": "2026-08-22",
        "currency": "USD",
        "unit": "per_1m_tokens",
        "input_cache_miss": 0.23,
        "output": 0.93,
        "pricing_scope": "eu-west-2 standard on-demand",
        "source": (
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
            "AmazonBedrock/current/eu-west-2/index.json"
        ),
    }


def test_env_loader_is_parent_local_and_rejects_conflicts(tmp_path: Path) -> None:
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("FIRST='one'\nSHARED=same\n", encoding="utf-8")
    second.write_text("SECOND=two\nSHARED=same\n", encoding="utf-8")
    before = dict(os.environ)

    assert _load_env_files((first, second)) == {
        "FIRST": "one",
        "SHARED": "same",
        "SECOND": "two",
    }
    assert dict(os.environ) == before

    second.write_text("SHARED=different\n", encoding="utf-8")
    with pytest.raises(ProviderManifestError, match="conflicting dotenv value"):
        _load_env_files((first, second))


class _FakeClient:
    seen: list[dict[str, Any]] = []
    fail_model: str | None = None

    def __init__(self, *, provider_config: Any, credential: str) -> None:
        self.config = provider_config
        self.credential = credential
        self.provider_call_records: tuple[dict[str, Any], ...] = ()
        self.provider_exchange_records: tuple[dict[str, Any], ...] = ()

    def generate_recap_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.seen.append(
            {
                "config": self.config,
                "credential": self.credential,
                "stage": stage,
                "system_prompt": system_prompt,
                "messages": messages,
                "response_schema": response_schema,
            }
        )
        if self.config.model == self.fail_model:
            self.provider_call_records = (
                {"status": "transport_error", "returned_model": None},
            )
            raise RuntimeError("diagnostic transport failure")
        output = {
            "reasoning_summary": "Structured transport is available.",
            "subtasks": [],
        }
        response_message = {"role": "assistant", "content": json.dumps(output)}
        self.provider_call_records = (
            {"status": "success", "returned_model": self.config.model},
        )
        self.provider_exchange_records = (
            {
                "status": "success",
                "request_body": {
                    "model": self.config.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                },
                "response_payload": {
                    "model": self.config.model,
                    "choices": [{"message": response_message}],
                },
                "response_message": response_message,
            },
        )
        return output


def _credential_file(path: Path) -> Path:
    path.write_text(
        "AUTOADAPTER_COMPANY_API_KEY=company-parent-secret\n"
        "AUTOADAPTER_MODEL_API_KEY=direct-parent-secret\n",
        encoding="utf-8",
    )
    return path


def test_all_level_connectivity_runner_uses_one_common_contract_without_secrets(
    tmp_path: Path,
) -> None:
    _FakeClient.seen = []
    _FakeClient.fail_model = None
    output = tmp_path / "report.json"
    report = run_connectivity(
        env_paths=(_credential_file(tmp_path / "credentials.env"),),
        output_path=output,
        client_factory=_FakeClient,
    )

    assert report["formal_episode"] is False
    assert report["formal_denominator_entry"] is False
    assert report["summary"] == {
        "declared_backbone_count": 7,
        "selected_backbone_count": 7,
        "connectivity_succeeded_count": 7,
        "all_selected_connected": True,
        "all_returned_models_match_pin": True,
        "all_structured_outputs_valid": True,
    }
    assert len(_FakeClient.seen) == 7
    assert all(call["stage"] == "b2_provider_connectivity" for call in _FakeClient.seen)
    assert len({call["system_prompt"] for call in _FakeClient.seen}) == 1
    assert all(
        call["response_schema"] == report["common_controller_contract"]["response_schema"]
        for call in _FakeClient.seen
    )
    assert all(call["config"].max_tokens == 4096 for call in _FakeClient.seen)
    serialized = output.read_text(encoding="utf-8")
    assert "company-parent-secret" not in serialized
    assert "direct-parent-secret" not in serialized
    assert all(
        len(level["raw_secret_free_exchanges"]) == 1 for level in report["levels"]
    )


def test_connectivity_failure_remains_visible_and_does_not_stop_later_levels(
    tmp_path: Path,
) -> None:
    resolved = load_and_validate_manifest()
    _FakeClient.seen = []
    _FakeClient.fail_model = resolved["providers"]["M3"]["exact_model_id"]
    try:
        report = run_connectivity(
            env_paths=(_credential_file(tmp_path / "credentials.env"),),
            client_factory=_FakeClient,
        )
    finally:
        _FakeClient.fail_model = None

    assert len(_FakeClient.seen) == 7
    assert report["summary"]["connectivity_succeeded_count"] == 6
    assert report["summary"]["all_selected_connected"] is False
    m3 = next(level for level in report["levels"] if level["backbone_id"] == "M3")
    assert m3["connectivity_succeeded"] is False
    assert m3["error"]["type"] == "RuntimeError"
    assert report["levels"][-1]["backbone_id"] == "M7"
