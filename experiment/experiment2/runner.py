"""Thin, explicitly separated runner boundaries for Experiment 2.

The source run, human review, and manually launched later run are three
different function calls.  Nothing in this module automatically chains the
later run from the source run or makes a source proposal visible same-round.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoadapter2.evolution import (
    EvolutionError,
    apply_experience_review,
    build_experience_review_queue,
    build_experience_snapshot,
    load_experience_snapshot,
    run_evolution,
)
from autoadapter2.pipeline import ExperimentConfig, PipelineError, run_experiment


EXPERIMENT_ID = "experiment2-so101-cross-run-closure"
AUTHORITY_REVISION = "0.1.1"
MANIFEST_REVISION = "0.1.0"
PROTOCOL_REVISION = "0.1.0"
ROBOT_CONFIGURATION = "robotstudio_so101"
CONDITION = "skeleton-assisted"
SOURCE_RUN_ID = "exp2-so101-source"
LATER_RUN_ID = "exp2-so101-later"
SONNET_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
OPUS_MODEL_ID = "eu.anthropic.claude-opus-5"
SOURCE_CELL_ID = f"{ROBOT_CONFIGURATION}::{CONDITION}"
EXPERIENCE_ID = f"{SOURCE_RUN_ID}:{SOURCE_CELL_ID}"
EXPECTED_RETRY_POLICY = {
    "maximum_physical_requests": 2,
    "backoff_seconds": 1.0,
    "retryable_http_statuses": [429, 500, 502, 503, 504],
}
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class Experiment2RunnerError(RuntimeError):
    """Raised when a cross-run boundary or formal pin is not satisfied."""


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else Path(__file__).with_name("manifest.json")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment2RunnerError(f"cannot read Experiment 2 manifest {source}") from exc
    if not isinstance(value, Mapping):
        raise Experiment2RunnerError("Experiment 2 manifest must be one object")
    return copy.deepcopy(dict(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Experiment2RunnerError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dotenv_value(raw: str, *, path: Path, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        for index, character in enumerate(value):
            if character == "#" and (index == 0 or value[index - 1].isspace()):
                return value[:index].rstrip()
        return value

    quote = value[0]
    result: list[str] = []
    escaped = False
    closing_index: int | None = None
    for index, character in enumerate(value[1:], start=1):
        if quote == '"' and escaped:
            result.append({"n": "\n", "r": "\r", "t": "\t"}.get(character, character))
            escaped = False
        elif quote == '"' and character == "\\":
            escaped = True
        elif character == quote:
            closing_index = index
            break
        else:
            result.append(character)
    _require(
        closing_index is not None and not escaped,
        f"invalid quoted dotenv value at {path}:{line_number}",
    )
    assert closing_index is not None
    trailing = value[closing_index + 1 :].strip()
    _require(
        not trailing or trailing.startswith("#"),
        f"unexpected text after dotenv value at {path}:{line_number}",
    )
    return "".join(result)


def _load_env_files(paths: list[str]) -> None:
    """Load explicit dotenv files without expansion and without replacing values."""

    for path_value in paths:
        path = Path(path_value).resolve()
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise Experiment2RunnerError(f"cannot read env file {path}") from exc
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, raw_value = line.partition("=")
            key = key.strip()
            _require(
                bool(separator) and ENV_NAME_PATTERN.fullmatch(key) is not None,
                f"invalid dotenv assignment at {path}:{line_number}",
            )
            os.environ.setdefault(
                key, _dotenv_value(raw_value, path=path, line_number=line_number)
            )


def validate_design_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the fixed two-run design without claiming executable readiness."""

    _require(isinstance(manifest, Mapping), "manifest must be one object")
    _require(manifest.get("schema_version") == 1, "schema_version must be 1")
    _require(
        manifest.get("authority_revision") == AUTHORITY_REVISION,
        f"authority_revision must be {AUTHORITY_REVISION}",
    )
    _require(
        manifest.get("manifest_revision") == MANIFEST_REVISION,
        f"manifest_revision must be {MANIFEST_REVISION}",
    )
    _require(
        manifest.get("protocol_revision") == PROTOCOL_REVISION,
        f"protocol_revision must be {PROTOCOL_REVISION}",
    )
    _require(manifest.get("experiment_id") == EXPERIMENT_ID, "unexpected experiment_id")
    _require(manifest.get("robot_configuration") == ROBOT_CONFIGURATION, "Experiment 2 robot must be SO-101")
    _require(manifest.get("generation_condition") == CONDITION, "Experiment 2 must be skeleton-assisted")

    models = manifest.get("models")
    _require(isinstance(models, Mapping) and set(models) == {"producer", "terminal_evolution"}, "models must pin Producer and terminal Evolution separately")
    assert isinstance(models, Mapping)
    producer = models["producer"]
    evolution = models["terminal_evolution"]
    _require(isinstance(producer, Mapping), "Producer model must be an object")
    _require(isinstance(evolution, Mapping), "Evolution model must be an object")
    assert isinstance(producer, Mapping) and isinstance(evolution, Mapping)
    _require(producer.get("family") == "Sonnet 4.6", "Producer family must be Sonnet 4.6")
    _require(producer.get("exact_model_id") == SONNET_MODEL_ID, f"Producer model ID must be {SONNET_MODEL_ID}")
    _require(producer.get("temperature") == 0.0, "Sonnet temperature must be 0")
    _require(producer.get("context_limit_tokens") == 1_000_000, "Sonnet context must be 1,000,000")
    _require(producer.get("max_output_tokens") == 16_384, "Sonnet max output must be 16,384")
    _require(evolution.get("family") == "Opus 5", "terminal Evolution family must be Opus 5")
    opus_id = evolution.get("exact_model_id")
    if opus_id is None:
        _require(
            evolution.get("exact_model_id_status") == "pending_pre_dispatch_pin"
            and evolution.get("pre_dispatch_pin_required") is True
            and evolution.get("unresolved_pin") == "infrastructure_blocker"
            and evolution.get("temperature") is None
            and evolution.get("context_limit_tokens") is None
            and evolution.get("max_output_tokens") is None,
            "unresolved Opus 5 settings must remain an explicit pre-dispatch blocker",
        )
    else:
        _require(
            opus_id == OPUS_MODEL_ID,
            f"pinned Opus 5 model ID must be {OPUS_MODEL_ID}",
        )
        temperature = evolution.get("temperature")
        context = evolution.get("context_limit_tokens")
        max_output = evolution.get("max_output_tokens")
        _require(
            temperature == 0.0,
            "pinned Opus 5 temperature must be 0",
        )
        _require(
            context == 1_000_000,
            "pinned Opus 5 context must be 1,000,000",
        )
        _require(
            max_output == 32_768,
            "pinned Opus 5 max output must be 32,768",
        )
    _require(evolution.get("maximum_calls") == 1, "source terminal Evolution must have a one-call ceiling")

    roles = manifest.get("run_roles")
    _require(isinstance(roles, Mapping) and set(roles) == {"source", "later"}, "run_roles must be source and later only")
    assert isinstance(roles, Mapping)
    source = roles["source"]
    later = roles["later"]
    _require(isinstance(source, Mapping) and isinstance(later, Mapping), "run roles must be objects")
    assert isinstance(source, Mapping) and isinstance(later, Mapping)
    _require(source.get("run_id") == SOURCE_RUN_ID, "source run ID is not fixed")
    _require(source.get("manual_launch") is True, "source must be explicitly launched")
    _require(source.get("experience_input") == "empty", "source Experience must be empty")
    _require(source.get("maximum_submitted_driver_attempts") == 3, "source submission ceiling must be three")
    source_evolution = source.get("evolution")
    _require(isinstance(source_evolution, Mapping) and source_evolution.get("enabled") is True and source_evolution.get("terminal_only") is True and source_evolution.get("same_run_input") is False, "source Evolution must be terminal-only with no same-run input")
    _require(later.get("run_id") == LATER_RUN_ID, "later run ID is not fixed")
    _require(later.get("manual_launch") is True and later.get("fresh_independent_run") is True, "later run must be a fresh manual launch")
    _require(
        later.get("experience_input")
        == "accepted-frozen-reviewed-source-snapshot",
        "later run must consume only the accepted frozen source snapshot",
    )
    _require(later.get("maximum_submitted_driver_attempts") == 3, "later submission ceiling must be three")
    later_evolution = later.get("evolution")
    _require(isinstance(later_evolution, Mapping) and later_evolution.get("enabled") is False, "later Evolution must be disabled")

    disposition = manifest.get("human_disposition")
    _require(isinstance(disposition, Mapping), "human_disposition must be explicit")
    assert isinstance(disposition, Mapping)
    _require(disposition.get("allowed_values") == ["accept", "reject"], "human disposition must be accept/reject only")
    _require(disposition.get("content_editing") is False, "human proposal editing must be disabled")
    _require(disposition.get("reason_required") is True, "human review reason is required")
    _require(
        disposition.get("later_run_requires") == "accept_for_experience_input",
        "later Experience input must require acceptance",
    )
    denominator = manifest.get("denominator")
    _require(isinstance(denominator, Mapping) and denominator.get("declared_closure_runs") == 2, "Experiment 2 denominator must be two declared runs")
    assert isinstance(denominator, Mapping)
    _require(denominator.get("improvement_claim") is False and denominator.get("matched_no_experience_control") is False, "Experiment 2 cannot claim improvement or a matched control")
    return copy.deepcopy(dict(manifest))


def _pipeline_model(
    value: Any,
    *,
    label: str,
    expected_id: str,
    declared_settings: Mapping[str, Any],
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"runtime.{label} must be a pipeline model manifest")
    assert isinstance(value, Mapping)
    try:
        checked = ExperimentConfig.from_mapping(
            {
                "experiment_id": "experiment2-model-pin-check",
                "robots": [ROBOT_CONFIGURATION],
                "generation_conditions": [CONDITION],
                "model": dict(value),
            }
        ).model_manifest
    except PipelineError as exc:
        raise Experiment2RunnerError(f"runtime.{label} is incomplete: {exc}") from exc
    assert checked is not None
    _require(checked["model_id"] == expected_id, f"runtime.{label}.model_id must be {expected_id}")
    _require(
        checked["context_window_tokens"]
        == declared_settings.get("context_limit_tokens"),
        f"runtime.{label} context differs from its prospective pin",
    )
    _require(
        checked["max_output_tokens"] == declared_settings.get("max_output_tokens"),
        f"runtime.{label} max output differs from its prospective pin",
    )
    _require(
        checked["temperature"] == declared_settings.get("temperature"),
        f"runtime.{label} temperature differs from its prospective pin",
    )
    return copy.deepcopy(dict(checked))


def _transport_pin(value: Any, *, label: str) -> dict[str, Any]:
    required = {
        "endpoint_region",
        "request_timeout_s",
        "retry_policy",
        "history_char_budget",
        "credential_env",
        "auth_header",
        "auth_prefix",
    }
    _require(isinstance(value, Mapping), f"runtime.{label} must be pinned")
    assert isinstance(value, Mapping)
    _require(set(value) == required, f"runtime.{label} must pin exactly {sorted(required)}")
    for key in ("endpoint_region", "credential_env", "auth_header"):
        _require(isinstance(value.get(key), str) and bool(str(value[key]).strip()), f"runtime.{label}.{key} must be non-empty")
    _require(isinstance(value.get("auth_prefix"), str), f"runtime.{label}.auth_prefix must be text")
    timeout = value.get("request_timeout_s")
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and float(timeout) > 0, f"runtime.{label}.request_timeout_s must be positive")
    history = value.get("history_char_budget")
    _require(isinstance(history, int) and not isinstance(history, bool) and history > 0, f"runtime.{label}.history_char_budget must be positive")
    _require(value.get("retry_policy") == EXPECTED_RETRY_POLICY, f"runtime.{label}.retry_policy must pin the current model client policy")
    return copy.deepcopy(dict(value))


def _resources(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "runtime.resources must be pinned")
    assert isinstance(value, Mapping)
    _require(set(value) == {"development_probe", "validation"}, "runtime.resources must pin development_probe and validation")
    development = value.get("development_probe")
    validation = value.get("validation")
    _require(isinstance(development, Mapping), "runtime.resources.development_probe must be an object")
    _require(isinstance(validation, Mapping), "runtime.resources.validation must be an object")
    assert isinstance(development, Mapping) and isinstance(validation, Mapping)
    _require(set(development) == {"max_requests_per_stage", "wall_timeout_s_per_request", "max_output_chars_per_request"}, "development_probe resource fields are incomplete")
    for key in development:
        number = development[key]
        _require(isinstance(number, (int, float)) and not isinstance(number, bool) and float(number) > 0, f"development_probe.{key} must be positive")
    _require(set(validation) == {"record_video", "worker_wall_timeout_s"}, "validation resource fields are incomplete")
    _require(validation.get("record_video") is True, "formal Experiment 2 requires video")
    timeout = validation.get("worker_wall_timeout_s")
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and float(timeout) > 0, "validation.worker_wall_timeout_s must be positive")
    return copy.deepcopy(dict(value))


def _evidence_covers_robot(retained: Mapping[str, Any], robot: str) -> bool:
    if retained.get("robot_configuration_id") == robot:
        return True
    for field in (
        "robot_configuration_ids",
        "robot_configurations",
        "passed_robot_configurations",
    ):
        values = retained.get(field)
        if isinstance(values, list) and robot in values:
            return True
    return False


def _validate_evidence_semantics(
    retained: Mapping[str, Any], *, label: str, evidence_path: Path
) -> None:
    artifact_type = retained.get("artifact_type")
    if label == "package":
        _require(
            artifact_type == "experiment3_package_check_evidence"
            and _evidence_covers_robot(retained, ROBOT_CONFIGURATION),
            "readiness_evidence.package does not prove the SO-101 package",
        )
        return
    if label == "reference_positive_control":
        _require(
            isinstance(artifact_type, str)
            and artifact_type.startswith("experiment3_reference_positive_control")
            and _evidence_covers_robot(retained, ROBOT_CONFIGURATION),
            "readiness_evidence.reference_positive_control does not prove SO-101",
        )
        return
    if label == "producer_model_canary":
        settings = retained.get("request_settings")
        _require(
            artifact_type == "experiment3_exact_sonnet_runtime_pin_canary"
            and retained.get("requested_model") == SONNET_MODEL_ID
            and retained.get("returned_model") == SONNET_MODEL_ID
            and retained.get("returned_model_matches_pin") is True
            and isinstance(settings, Mapping)
            and settings.get("temperature") == 0.0
            and settings.get("context_limit_tokens") == 1_000_000
            and settings.get("max_output_tokens") == 16_384,
            "readiness_evidence.producer_model_canary does not prove the exact Sonnet pin",
        )
        return
    if label == "evolution_model_canary":
        canaries = retained.get("canaries")
        opus = next(
            (
                item
                for item in canaries
                if isinstance(item, Mapping)
                and item.get("role") == "terminal_evolution"
            ),
            None,
        ) if isinstance(canaries, list) else None
        _require(
            artifact_type == "experiment2_company_api_model_identity_canaries"
            and isinstance(opus, Mapping)
            and opus.get("requested_model") == OPUS_MODEL_ID
            and opus.get("returned_model") == OPUS_MODEL_ID
            and opus.get("returned_model_matches_pin") is True
            and opus.get("structured_output_valid") is True,
            "readiness_evidence.evolution_model_canary does not prove the exact Opus pin",
        )
        return
    if label == "evolution_contract":
        contract = retained.get("contract")
        allowed_fields = (
            contract.get("allowed_model_fields")
            if isinstance(contract, Mapping)
            else None
        )
        _require(
            artifact_type == "experiment2_opus_evolution_contract_canary"
            and retained.get("requested_model") == OPUS_MODEL_ID
            and retained.get("returned_model") == OPUS_MODEL_ID
            and retained.get("returned_model_matches_pin") is True
            and isinstance(contract, Mapping)
            and isinstance(allowed_fields, list)
            and all(isinstance(field, str) for field in allowed_fields)
            and set(allowed_fields)
            == {"observation", "lesson", "recommendation", "scope", "evidence"}
            and contract.get("returned_exact_five_field_object") is True
            and contract.get("framework_wrapper_model_authored") is False,
            "readiness_evidence.evolution_contract does not prove the five-field Opus contract",
        )
        return
    if label in {
        "candidate_isolation",
        "canonical_physics",
        "independent_harness",
        "recorder",
    }:
        checks = retained.get("checks")
        role = checks.get(label) if isinstance(checks, Mapping) else None
        _require(
            artifact_type == "experiment3_framework_harness_boundary_checks"
            and isinstance(role, Mapping)
            and role.get("passed") is True,
            f"readiness_evidence.{label} does not prove its named boundary",
        )
        if label == "recorder":
            real_name = role.get("real_mujoco_evidence")
            _require(
                isinstance(real_name, str) and bool(real_name.strip()),
                "recorder boundary evidence lacks real MuJoCo evidence",
            )
            real_path = evidence_path.with_name(real_name)
            try:
                real = json.loads(real_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Experiment2RunnerError(
                    f"real recorder evidence is not readable JSON: {real_path}"
                ) from exc
            video = real.get("video") if isinstance(real, Mapping) else None
            _require(
                isinstance(real, Mapping)
                and real.get("artifact_type") == "experiment3_franka_recorder_canary"
                and real.get("passed") is True
                and real.get("video_complete") is True
                and isinstance(video, Mapping)
                and video.get("decodable") is True,
                "real recorder evidence does not retain a complete decodable video",
            )
        return
    if label == "experience_boundary":
        covered = retained.get("covered_boundaries")
        _require(
            artifact_type == "experiment2_runtime_experience_boundary_checks"
            and isinstance(covered, list)
            and "Evolution disabled creates no Opus client or review queue" in covered
            and "exact Experience ID tracing into later TGCD and GENERATE" in covered,
            "readiness_evidence.experience_boundary does not prove the cross-run boundary",
        )
        return
    raise Experiment2RunnerError(f"unsupported readiness evidence role: {label}")


def _evidence_item(value: Any, *, label: str, root: Path) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"readiness_evidence.{label} must be an object")
    assert isinstance(value, Mapping)
    _require(set(value) == {"path", "passed"}, f"readiness_evidence.{label} must contain path and passed")
    _require(value.get("passed") is True, f"readiness_evidence.{label} has not passed")
    path_value = value.get("path")
    _require(isinstance(path_value, str) and bool(path_value.strip()), f"readiness_evidence.{label}.path must be non-empty")
    evidence_path = Path(path_value)
    if not evidence_path.is_absolute():
        evidence_path = root / evidence_path
    _require(evidence_path.is_file(), f"readiness evidence file is missing: {evidence_path}")
    try:
        retained = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment2RunnerError(
            f"readiness evidence is not readable JSON: {evidence_path}"
        ) from exc
    _require(
        isinstance(retained, Mapping) and retained.get("passed") is True,
        f"retained readiness evidence has no true passed verdict: {evidence_path}",
    )
    assert isinstance(retained, Mapping)
    _validate_evidence_semantics(
        retained, label=label, evidence_path=evidence_path
    )
    return {
        "path": str(evidence_path.resolve()),
        "passed": True,
        "artifact_type": retained.get("artifact_type"),
    }


def validate_executable_preflight(
    manifest: Mapping[str, Any], *, mainline_root: str | Path
) -> dict[str, Any]:
    """Require endpoint, price, resource, and retained readiness pins."""

    checked = validate_design_manifest(manifest)
    root = Path(mainline_root).resolve()
    runtime = checked.get("runtime")
    _require(isinstance(runtime, Mapping), "formal dispatch requires a pinned runtime block")
    assert isinstance(runtime, Mapping)
    required_runtime = {
        "producer_model",
        "producer_transport",
        "evolution_model",
        "evolution_transport",
        "resources",
    }
    _require(set(runtime) == required_runtime, "runtime must separately pin Producer, Evolution, transports, and resources")
    declared_opus_id = checked["models"]["terminal_evolution"].get("exact_model_id")
    _require(
        isinstance(declared_opus_id, str) and bool(declared_opus_id.strip()),
        "formal dispatch is blocked until the provider-qualified Opus 5 ID and settings are pinned in the manifest",
    )
    producer_model = _pipeline_model(
        runtime.get("producer_model"),
        label="producer_model",
        expected_id=SONNET_MODEL_ID,
        declared_settings=checked["models"]["producer"],
    )
    evolution_model = _pipeline_model(
        runtime.get("evolution_model"),
        label="evolution_model",
        expected_id=declared_opus_id,
        declared_settings=checked["models"]["terminal_evolution"],
    )
    _require(producer_model["model_id"] != evolution_model["model_id"], "Producer and Evolution models must be distinct")
    producer_transport = _transport_pin(runtime.get("producer_transport"), label="producer_transport")
    evolution_transport = _transport_pin(runtime.get("evolution_transport"), label="evolution_transport")
    resources = _resources(runtime.get("resources"))

    evidence = checked.get("readiness_evidence")
    _require(isinstance(evidence, Mapping), "formal dispatch requires readiness_evidence")
    assert isinstance(evidence, Mapping)
    required = {
        "package",
        "reference_positive_control",
        "candidate_isolation",
        "canonical_physics",
        "independent_harness",
        "recorder",
        "producer_model_canary",
        "evolution_model_canary",
        "evolution_contract",
        "experience_boundary",
    }
    _require(set(evidence) == required, "readiness_evidence fields are incomplete")
    checked_evidence = {
        key: _evidence_item(evidence[key], label=key, root=root) for key in sorted(required)
    }
    return {
        "producer_model": producer_model,
        "producer_transport": producer_transport,
        "evolution_model": evolution_model,
        "evolution_transport": evolution_transport,
        "resources": resources,
        "readiness_evidence": checked_evidence,
    }


def _assert_client_pin(
    client: Any,
    *,
    role: str,
    model: Mapping[str, Any],
    transport: Mapping[str, Any],
) -> None:
    config = getattr(client, "config", None)
    _require(config is not None, f"{role} client must expose its pinned config")
    expected = {
        "provider": model["vendor"],
        "model": model["model_id"],
        "base_url": str(model["base_url"]).rstrip("/"),
        "api_protocol": model["api_protocol"],
        "thinking": (
            None if model["thinking"] in {None, "disabled"} else model["thinking"]
        ),
        "timeout_s": float(transport["request_timeout_s"]),
        "max_tokens": model["max_output_tokens"],
        "tool_history_mode": model["tool_history_mode"],
        "history_char_budget": transport["history_char_budget"],
        "auth_header": transport["auth_header"],
        "auth_prefix": transport["auth_prefix"],
    }
    for field, value in expected.items():
        actual = getattr(config, field, None)
        if field == "base_url":
            actual = str(actual).rstrip("/")
        _require(actual == value, f"{role} client {field} does not match the formal pin")


def _built_in_client(
    model: Mapping[str, Any], transport: Mapping[str, Any]
) -> Any:
    from autoadapter2.model_api import JsonModelClient, ModelConfig

    credential_env = str(transport["credential_env"])
    api_key = os.environ.get(credential_env, "")
    _require(bool(api_key), f"manifest-pinned credential environment variable {credential_env!r} is missing")
    _require(model["temperature"] == 0.0, "built-in JsonModelClient supports temperature 0 only")
    return JsonModelClient(
        ModelConfig(
            provider=str(model["vendor"]),
            model=str(model["model_id"]),
            base_url=str(model["base_url"]),
            api_key=api_key,
            api_protocol=str(model["api_protocol"]),
            auth_header=str(transport["auth_header"]),
            auth_prefix=str(transport["auth_prefix"]),
            thinking=(
                None
                if model["thinking"] in {None, "disabled"}
                else str(model["thinking"])
            ),
            timeout_s=float(transport["request_timeout_s"]),
            max_tokens=int(model["max_output_tokens"]),
            tool_history_mode=str(model["tool_history_mode"]),
            history_char_budget=int(transport["history_char_budget"]),
        )
    )


def _resource_summary(client: Any, model: Mapping[str, Any]) -> dict[str, Any]:
    calls = getattr(client, "calls", None)
    call_records = calls if isinstance(calls, list) else []
    token_fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    observed_token_values = {
        field: [
            int(call[field])
            for call in call_records
            if isinstance(call, Mapping)
            and isinstance(call.get(field), int)
            and not isinstance(call.get(field), bool)
        ]
        for field in token_fields
    }
    tokens: dict[str, Any] = {
        field: sum(values) if values else None
        for field, values in observed_token_values.items()
    }
    other_tokens: dict[str, int] = {}
    for call in call_records:
        other = (
            call.get("other_provider_token_categories")
            if isinstance(call, Mapping)
            else None
        )
        if isinstance(other, Mapping):
            for key, value in other.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    other_tokens[str(key)] = other_tokens.get(str(key), 0) + value
    tokens["other_provider_token_categories"] = other_tokens or None
    model_elapsed = sum(
        float(call["elapsed_s"])
        for call in call_records
        if isinstance(call, Mapping)
        and isinstance(call.get("elapsed_s"), (int, float))
        and not isinstance(call.get("elapsed_s"), bool)
    )
    price = model["price_snapshot"]
    estimated_cost = None
    if tokens["input_tokens"] is not None and tokens["output_tokens"] is not None:
        estimated_cost = (
            tokens["input_tokens"]
            * float(price["input_per_million_tokens"])
            + tokens["output_tokens"]
            * float(price["output_per_million_tokens"])
        ) / 1_000_000
    return {
        "model_id": model["model_id"],
        "call_count": len(call_records),
        "token_categories": tokens,
        "token_observed_call_counts": {
            field: len(values) for field, values in observed_token_values.items()
        },
        "model_elapsed_time_s": model_elapsed,
        "estimated_cost": {
            "is_estimate": True,
            "amount": estimated_cost,
            "currency": price["currency"],
            "price_snapshot_date": price["date"],
            "basis": "reported input_tokens and output_tokens; cache and other token categories are retained but not separately priced",
        },
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _assert_returned_identity(client: Any, *, role: str, expected_model_id: str) -> None:
    calls = getattr(client, "calls", None)
    _require(isinstance(calls, list) and bool(calls), f"{role} returned-model evidence is missing")
    exact_identity_seen = False
    for index, call in enumerate(calls):
        _require(isinstance(call, Mapping), f"{role} call {index} is not an evidence record")
        returned = call.get("returned_model")
        if returned is None:
            missing_identity_allowed = (
                call.get("status") == "http_error"
                and call.get("http_status")
                in EXPECTED_RETRY_POLICY["retryable_http_statuses"]
            )
            _require(
                missing_identity_allowed,
                f"{role} successful or non-retry call omitted returned model identity",
            )
            continue
        _require(
            returned == expected_model_id,
            f"{role} returned model identity differs from its formal pin",
        )
        exact_identity_seen = True
    _require(exact_identity_seen, f"{role} returned-model evidence is missing")


def _assert_attempt_ceiling(result: Mapping[str, Any]) -> Mapping[str, Any]:
    cells = result.get("cells")
    _require(isinstance(cells, list) and len(cells) == 1, "run must retain exactly one SO-101 cell")
    cell = cells[0]
    _require(isinstance(cell, Mapping), "SO-101 cell result must be an object")
    attempt_count = cell.get("attempt_count")
    _require(
        isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and 0 <= attempt_count <= 3,
        "SO-101 run submitted more than three drivers or omitted its attempt count",
    )
    return cell


def _task_demo_terminal(cell: Mapping[str, Any]) -> dict[str, Any]:
    admitted = cell.get("final_capability_validation_passed")
    _require(
        isinstance(admitted, bool),
        "SO-101 run omitted its final capability-admission verdict",
    )
    executed = cell.get("task_demo_executed") is True
    if admitted:
        _require(executed, "an admitted final driver requires an executed Task Demo")
        return {"status": "executed", "passed": cell.get("task_demo_passed") is True}
    _require(not executed, "Task Demo executed without an admitted final driver")
    task_demo = cell.get("task_demo")
    if (
        isinstance(task_demo, Mapping)
        and task_demo.get("skipped") is True
        and isinstance(task_demo.get("skip_reason"), str)
        and bool(str(task_demo["skip_reason"]).strip())
    ):
        return {"status": "not-run", "reason": str(task_demo["skip_reason"]).strip()}
    raise Experiment2RunnerError(
        "a run without an admitted final driver requires a truthful Task Demo not-run reason"
    )


def _assert_formal_cell_evidence(
    result: Mapping[str, Any],
    cell: Mapping[str, Any],
    *,
    run_id: str,
) -> None:
    """Check only the evidence fields needed to keep one closure run truthful."""

    _require(result.get("run_id") == run_id, "SO-101 result run_id differs from its declared role")
    _require(
        cell.get("cell_id") == SOURCE_CELL_ID
        and cell.get("robot_configuration_id") == ROBOT_CONFIGURATION
        and cell.get("condition") == CONDITION,
        "SO-101 result cell identity differs from the declared robot and condition",
    )
    outcomes = cell.get("outcomes")
    _require(isinstance(outcomes, Mapping), "SO-101 result lacks stage outcomes")
    assert isinstance(outcomes, Mapping)
    for stage in ("TGCD", "IVC"):
        evidence = outcomes.get(stage)
        _require(
            isinstance(evidence, Mapping) and evidence.get("completed") is True,
            f"SO-101 result lacks completed run-local {stage} evidence",
        )
    if cell.get("capability_validation_executed") is True:
        _require(
            cell.get("video_required") is True and cell.get("video_complete") is True,
            "executed capability validation lacks complete required video evidence",
        )
    if cell.get("final_capability_validation_passed") is True:
        task_counts = cell.get("task_demo_task_counts")
        _require(
            isinstance(task_counts, Mapping) and task_counts.get("total") == 5,
            "an admitted final driver requires an exact five-task Task Demo",
        )
        _require(
            cell.get("task_demo_video_complete") is True,
            "executed Task Demo lacks complete required video evidence",
        )


def _new_output(path: str | Path, *, role: str) -> Path:
    destination = Path(path).resolve()
    _require(not destination.exists() or not any(destination.iterdir()), f"{role} output directory must be new or empty; existing evidence is never overwritten")
    return destination


def _retain_runner_failure(
    *,
    destination: Path,
    result: Mapping[str, Any],
    role: str,
    stage: str,
    error: BaseException,
    runner_started: float,
    producer_client: Any,
    producer_model: Mapping[str, Any],
    manual_launch_event: str,
    launched_at_utc: str,
    evolution_client: Any | None = None,
    evolution_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    retained = copy.deepcopy(dict(result))
    retained["experiment2_runner_status"] = "failed"
    retained["experiment2_run_role"] = role
    retained["experiment2_manual_launch_event"] = manual_launch_event.strip()
    retained["experiment2_manual_launched_at_utc"] = launched_at_utc
    retained["experiment2_runner_failure"] = {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error).strip() or type(error).__name__,
    }
    resources: dict[str, Any] = {
        "runner_wall_time_s": time.monotonic() - runner_started,
        "producer": _resource_summary(producer_client, producer_model),
    }
    if evolution_client is not None and evolution_model is not None:
        resources["terminal_evolution"] = _resource_summary(
            evolution_client, evolution_model
        )
    retained["resource_summary"] = resources
    _write_json(destination / "experiment_report.json", retained)
    return retained


def _base_config(preflight: Mapping[str, Any], *, experience_input: Any, evolution_enabled: bool) -> dict[str, Any]:
    resources = preflight["resources"]
    return {
        "experiment_id": EXPERIMENT_ID,
        "robots": [ROBOT_CONFIGURATION],
        "generation_conditions": [CONDITION],
        "max_driver_attempts_per_condition": 3,
        "development_probe": copy.deepcopy(resources["development_probe"]),
        "validation": copy.deepcopy(resources["validation"]),
        "model": copy.deepcopy(preflight["producer_model"]),
        "experience": {
            "input": copy.deepcopy(experience_input),
            "review_queue_output": "experience_review_queue.json",
            "snapshot_output": "experience_snapshot.json",
        },
        "evolution": {"enabled": evolution_enabled},
        "seeds": {"task_demo_selection": "{run_id}:{robot_configuration_id}"},
    }


def run_source(
    mainline_root: str | Path,
    *,
    output_dir: str | Path,
    manual_launch_event: str,
    client_factory: Callable[[str, Mapping[str, Any], Mapping[str, Any]], Any]
    | None = None,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    run_experiment_fn: Callable[..., Mapping[str, Any]] = run_experiment,
    evolution_runner_fn: Callable[[Any, Mapping[str, Any]], Mapping[str, Any]] = run_evolution,
    hooks: Any | None = None,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Run Sonnet through Task Demo, then and only then call terminal Opus."""

    runner_started = time.monotonic()
    launched_at_utc = _utc_now()
    _require(isinstance(manual_launch_event, str) and bool(manual_launch_event.strip()), "source manual_launch_event is required")
    destination = _new_output(output_dir, role="source")
    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    preflight = validate_executable_preflight(source, mainline_root=mainline_root)
    producer_client = (
        client_factory("producer", copy.deepcopy(preflight["producer_model"]), copy.deepcopy(preflight["producer_transport"]))
        if client_factory is not None
        else _built_in_client(preflight["producer_model"], preflight["producer_transport"])
    )
    _assert_client_pin(producer_client, role="Producer", model=preflight["producer_model"], transport=preflight["producer_transport"])

    # The generic call is deliberately Evolution-disabled.  This guarantees
    # that Opus cannot be constructed or called until Task Demo is observed.
    kwargs: dict[str, Any] = {
        "config": _base_config(preflight, experience_input=[], evolution_enabled=False),
        "output_dir": destination,
        "run_id": SOURCE_RUN_ID,
        "producer_client": producer_client,
        "check_self_containment": check_self_containment,
        "skip_reference_calibration": True,
    }
    if hooks is not None:
        kwargs["hooks"] = hooks
    result = copy.deepcopy(dict(run_experiment_fn(mainline_root, **kwargs)))
    result["experiment2_manual_launch_event"] = manual_launch_event.strip()
    result["experiment2_manual_launched_at_utc"] = launched_at_utc
    try:
        _assert_returned_identity(
            producer_client, role="source Producer", expected_model_id=SONNET_MODEL_ID
        )
        cell = copy.deepcopy(dict(_assert_attempt_ceiling(result)))
        task_demo_status = _task_demo_terminal(cell)
        _assert_formal_cell_evidence(result, cell, run_id=SOURCE_RUN_ID)
        _require(
            result.get("evolution_enabled") is False
            and result.get("evolution_model") is None,
            "source Producer path enabled Evolution before terminal Opus",
        )
        experience_ids = result.get("experience_input_ids")
        _require(
            isinstance(experience_ids, Mapping)
            and experience_ids.get(ROBOT_CONFIGURATION) == [],
            "source run did not retain empty Experience input",
        )
        _require(
            not (destination / "experience_review_queue.json").exists(),
            "source Producer path created a premature review queue",
        )
        result["experiment2_task_demo_stage"] = task_demo_status
    except Exception as exc:
        _retain_runner_failure(
            destination=destination,
            result=result,
            role="source",
            stage="source-result-postcheck",
            error=exc,
            runner_started=runner_started,
            producer_client=producer_client,
            producer_model=preflight["producer_model"],
            manual_launch_event=manual_launch_event,
            launched_at_utc=launched_at_utc,
        )
        raise Experiment2RunnerError(
            f"source result postcheck failed; Opus was not called: {exc}"
        ) from exc

    evolution_client: Any | None = None
    try:
        evolution_client = (
            client_factory("terminal_evolution", copy.deepcopy(preflight["evolution_model"]), copy.deepcopy(preflight["evolution_transport"]))
            if client_factory is not None
            else _built_in_client(preflight["evolution_model"], preflight["evolution_transport"])
        )
        _require(evolution_client is not producer_client, "Producer and terminal Evolution clients must be distinct")
        _assert_client_pin(evolution_client, role="Evolution", model=preflight["evolution_model"], transport=preflight["evolution_transport"])
        evolution = copy.deepcopy(dict(evolution_runner_fn(evolution_client, cell)))
    except Exception as exc:
        _retain_runner_failure(
            destination=destination,
            result=result,
            role="source",
            stage="terminal-evolution",
            error=exc,
            runner_started=runner_started,
            producer_client=producer_client,
            producer_model=preflight["producer_model"],
            manual_launch_event=manual_launch_event,
            launched_at_utc=launched_at_utc,
            evolution_client=evolution_client,
            evolution_model=preflight["evolution_model"],
        )
        raise Experiment2RunnerError(
            f"terminal Evolution failed before a valid review queue: {exc}"
        ) from exc

    terminal_errors: list[str] = []
    if evolution.get("model_call_count") != 1:
        terminal_errors.append(
            "source terminal Evolution must contain exactly one logical Opus model call"
        )
    raw_evolution_calls = getattr(evolution_client, "calls", None)
    physical_requests = (
        len(raw_evolution_calls) if isinstance(raw_evolution_calls, list) else 0
    )
    if not 1 <= physical_requests <= EXPECTED_RETRY_POLICY["maximum_physical_requests"]:
        terminal_errors.append(
            "source terminal Evolution exceeded its bounded physical-request policy"
        )
    identity_verified = True
    try:
        _assert_returned_identity(
            evolution_client,
            role="terminal Evolution",
            expected_model_id=str(preflight["evolution_model"]["model_id"]),
        )
    except Experiment2RunnerError as exc:
        identity_verified = False
        terminal_errors.append(str(exc))
    if (
        evolution.get("terminal_report_read") is not True
        or evolution.get("evolution_completed") is not True
        or evolution.get("failure") is not None
    ):
        terminal_errors.append(
            "source terminal Evolution did not complete the public proposal contract"
        )
    if terminal_errors:
        underlying_failure = copy.deepcopy(evolution.get("failure"))
        evolution["evolution_completed"] = False
        evolution["proposal_created"] = False
        evolution["proposal"] = None
        evolution["failure"] = {
            "type": "Experiment2TerminalEvolutionError",
            "message": "; ".join(terminal_errors),
            "underlying_failure": underlying_failure,
        }
    outcomes = cell.get("outcomes")
    _require(isinstance(outcomes, Mapping), "source cell is missing terminal outcomes")
    cell["outcomes"] = copy.deepcopy(dict(outcomes))
    cell["outcomes"]["Evolution"] = evolution
    result["cells"] = [cell]
    result["experiment2_terminal_evolution"] = {
        "called_after_task_demo": True,
        "model_id": preflight["evolution_model"]["model_id"],
        "maximum_logical_calls": 1,
        "logical_call_count": evolution.get("model_call_count"),
        "physical_request_count": physical_requests,
        "returned_identity_verified": identity_verified,
        "terminal_contract_verified": not terminal_errors,
    }
    result["resource_summary"] = {
        "runner_wall_time_s": time.monotonic() - runner_started,
        "producer": _resource_summary(producer_client, preflight["producer_model"]),
        "terminal_evolution": _resource_summary(
            evolution_client, preflight["evolution_model"]
        ),
    }
    if terminal_errors:
        retained_error = Experiment2RunnerError("; ".join(terminal_errors))
        _retain_runner_failure(
            destination=destination,
            result=result,
            role="source",
            stage="terminal-evolution-postcheck",
            error=retained_error,
            runner_started=runner_started,
            producer_client=producer_client,
            producer_model=preflight["producer_model"],
            manual_launch_event=manual_launch_event,
            launched_at_utc=launched_at_utc,
            evolution_client=evolution_client,
            evolution_model=preflight["evolution_model"],
        )
        raise Experiment2RunnerError(
            "terminal Evolution failure was retained without a review queue; "
            "source closure is blocked: " + "; ".join(terminal_errors)
        )
    queue = build_experience_review_queue(
        run_id=SOURCE_RUN_ID,
        experiment_id=EXPERIMENT_ID,
        expected_robots=(ROBOT_CONFIGURATION,),
        expected_conditions=(CONDITION,),
        cells=(cell,),
    )
    _write_json(destination / "experience_review_queue.json", queue)
    result["experiment2_runner_status"] = "completed"
    result["experiment2_run_role"] = "source"
    _write_json(destination / "experiment_report.json", result)
    return copy.deepcopy(result)


def review_source(
    source_output_dir: str | Path,
    *,
    disposition: str,
    reason: str,
) -> dict[str, Any]:
    """Freeze one accept/reject disposition without accepting proposal edits."""

    source_dir = Path(source_output_dir).resolve()
    queue_path = source_dir / "experience_review_queue.json"
    reviewed_path = source_dir / "experience_reviewed_queue.json"
    snapshot_path = source_dir / "experience_snapshot.json"
    _require(not reviewed_path.exists() and not snapshot_path.exists(), "source proposal already has a frozen review artifact")
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment2RunnerError("source review queue is unavailable") from exc
    try:
        reviewed = apply_experience_review(
            queue,
            {SOURCE_CELL_ID: {"disposition": disposition, "reason": reason}},
        )
    except EvolutionError as exc:
        raise Experiment2RunnerError(str(exc)) from exc
    snapshot: dict[str, Any] | None = None
    if disposition.strip().lower() == "accept":
        try:
            snapshot = build_experience_snapshot(reviewed)
        except EvolutionError as exc:
            raise Experiment2RunnerError(str(exc)) from exc
        records = snapshot.get("records")
        _require(isinstance(records, list) and len(records) == 1, "accepted source must freeze exactly one Experience record")
        _require(records[0].get("experience_id") == EXPERIENCE_ID, "accepted Experience ID is not the fixed source ID")
        _write_json(reviewed_path, reviewed)
        _write_json(snapshot_path, snapshot)
    else:
        _write_json(reviewed_path, reviewed)
    return {
        "disposition": disposition.strip().lower(),
        "reason": reason,
        "reviewed_queue_path": str(reviewed_path),
        "snapshot_path": str(snapshot_path) if snapshot is not None else None,
        "later_run_eligible": snapshot is not None,
    }


def _accepted_snapshot(path: str | Path) -> dict[str, Any]:
    try:
        snapshot = load_experience_snapshot(path)
    except (OSError, EvolutionError) as exc:
        raise Experiment2RunnerError("later run requires an accepted frozen Experience snapshot") from exc
    _require(snapshot.get("source_run_id") == SOURCE_RUN_ID, "Experience source run ID does not match Experiment 2")
    records = snapshot.get("records")
    _require(isinstance(records, list) and len(records) == 1, "later run requires exactly one accepted Experience record")
    record = records[0]
    _require(isinstance(record, Mapping), "accepted Experience record must be an object")
    assert isinstance(record, Mapping)
    _require(record.get("experience_id") == EXPERIENCE_ID, "later run Experience ID is not the fixed source ID")
    _require(record.get("reviewed") is True and record.get("review_decision") == "accept", "later run Experience is not accepted")
    _require(record.get("source_robot") == ROBOT_CONFIGURATION, "later run Experience source robot is wrong")
    _require(
        record.get("generation_condition") == CONDITION,
        "later run Experience generation condition is wrong",
    )
    _require(
        record.get("terminal_outcome_label") in {"positive", "negative"},
        "later run Experience terminal outcome label is indeterminate",
    )
    return snapshot


def run_later(
    mainline_root: str | Path,
    *,
    snapshot_path: str | Path,
    output_dir: str | Path,
    manual_launch_event: str,
    client_factory: Callable[[str, Mapping[str, Any], Mapping[str, Any]], Any]
    | None = None,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    run_experiment_fn: Callable[..., Mapping[str, Any]] = run_experiment,
    hooks: Any | None = None,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Manually launch the independent later Sonnet run from one frozen snapshot."""

    runner_started = time.monotonic()
    _require(isinstance(manual_launch_event, str) and bool(manual_launch_event.strip()), "later manual_launch_event is required")
    # Rejection, a null proposal, or a missing snapshot is rejected before a
    # client is constructed and therefore before any model call.
    snapshot = _accepted_snapshot(snapshot_path)
    loaded_at_utc = _utc_now()
    destination = _new_output(output_dir, role="later")
    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    preflight = validate_executable_preflight(source, mainline_root=mainline_root)
    producer_client = (
        client_factory("producer", copy.deepcopy(preflight["producer_model"]), copy.deepcopy(preflight["producer_transport"]))
        if client_factory is not None
        else _built_in_client(preflight["producer_model"], preflight["producer_transport"])
    )
    _assert_client_pin(producer_client, role="later Producer", model=preflight["producer_model"], transport=preflight["producer_transport"])

    kwargs: dict[str, Any] = {
        "config": _base_config(preflight, experience_input=snapshot, evolution_enabled=False),
        "output_dir": destination,
        "run_id": LATER_RUN_ID,
        "producer_client": producer_client,
        "check_self_containment": check_self_containment,
        "skip_reference_calibration": True,
    }
    if hooks is not None:
        kwargs["hooks"] = hooks
    result = copy.deepcopy(dict(run_experiment_fn(mainline_root, **kwargs)))
    result["experiment2_manual_launch_event"] = manual_launch_event.strip()
    result["experiment2_manual_launched_at_utc"] = loaded_at_utc
    result["experiment2_loaded_experience_id"] = EXPERIENCE_ID
    result["experiment2_experience_load"] = {
        "experience_id": EXPERIENCE_ID,
        "snapshot_id": snapshot["snapshot_id"],
        "source_run_id": snapshot["source_run_id"],
        "loaded_at_utc": loaded_at_utc,
        "manual_launch_event": manual_launch_event.strip(),
    }
    try:
        _assert_returned_identity(
            producer_client, role="later Producer", expected_model_id=SONNET_MODEL_ID
        )
        cell = _assert_attempt_ceiling(result)
        result["experiment2_task_demo_stage"] = _task_demo_terminal(cell)
        _assert_formal_cell_evidence(result, cell, run_id=LATER_RUN_ID)
        ids = result.get("experience_input_ids")
        _require(isinstance(ids, Mapping) and ids.get(ROBOT_CONFIGURATION) == [EXPERIENCE_ID], "later trace did not consume the exact Experience ID")
        _require(result.get("evolution_enabled") is False and result.get("evolution_model") is None, "later run must not invoke Evolution")
        outcomes = cell.get("outcomes")
        assert isinstance(outcomes, Mapping)
        for stage in ("TGCD", "GENERATE"):
            _require(
                EXPERIENCE_ID in json.dumps(outcomes.get(stage), sort_keys=True),
                f"later {stage} trace does not contain the exact Experience ID",
            )
        _require(outcomes.get("Evolution") is None, "later cell produced Evolution output")
        _require(not (destination / "experience_review_queue.json").exists(), "later run created an Experience review queue")
    except Exception as exc:
        _retain_runner_failure(
            destination=destination,
            result=result,
            role="later",
            stage="later-result-postcheck",
            error=exc,
            runner_started=runner_started,
            producer_client=producer_client,
            producer_model=preflight["producer_model"],
            manual_launch_event=manual_launch_event,
            launched_at_utc=loaded_at_utc,
        )
        raise Experiment2RunnerError(
            f"later result postcheck failed and was retained: {exc}"
        ) from exc
    result["resource_summary"] = {
        "runner_wall_time_s": time.monotonic() - runner_started,
        "producer": _resource_summary(producer_client, preflight["producer_model"]),
    }
    result["experiment2_runner_status"] = "completed"
    result["experiment2_run_role"] = "later"
    _write_json(destination / "experiment_report.json", result)
    return copy.deepcopy(result)


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiment.experiment2.runner",
        description="Run the explicitly separated Experiment 2 boundaries.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    design = commands.add_parser("design-check")
    design.add_argument("--manifest", required=True)

    preflight = commands.add_parser("preflight")
    preflight.add_argument("--root", required=True)
    preflight.add_argument("--manifest", required=True)

    source = commands.add_parser("source")
    source.add_argument("--root", required=True)
    source.add_argument("--manifest", required=True)
    source.add_argument("--output", required=True)
    source.add_argument("--manual-event", required=True)
    source.add_argument("--env-file", action="append", default=[])

    review = commands.add_parser("review")
    review.add_argument("--source-output", required=True)
    review.add_argument("--disposition", required=True, choices=("accept", "reject"))
    review.add_argument("--reason", required=True)

    later = commands.add_parser("later")
    later.add_argument("--root", required=True)
    later.add_argument("--manifest", required=True)
    later.add_argument("--snapshot", required=True)
    later.add_argument("--output", required=True)
    later.add_argument("--manual-event", required=True)
    later.add_argument("--env-file", action="append", default=[])
    return parser


def _print_cli(value: Mapping[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(value, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    """Execute one Experiment 2 boundary and print a compact, secret-free record."""

    args = _cli_parser().parse_args(argv)
    try:
        if args.command == "design-check":
            checked = validate_design_manifest(load_manifest(args.manifest))
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": checked["experiment_id"],
                "robot_configuration": checked["robot_configuration"],
                "source_run_id": checked["run_roles"]["source"]["run_id"],
                "later_run_id": checked["run_roles"]["later"]["run_id"],
            }
        elif args.command == "preflight":
            checked = validate_executable_preflight(
                load_manifest(args.manifest), mainline_root=args.root
            )
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": EXPERIMENT_ID,
                "producer_model_id": checked["producer_model"]["model_id"],
                "terminal_evolution_model_id": checked["evolution_model"]["model_id"],
                "readiness_evidence_count": len(checked["readiness_evidence"]),
            }
        elif args.command == "source":
            _load_env_files(args.env_file)
            result = run_source(
                args.root,
                manifest_path=args.manifest,
                output_dir=args.output,
                manual_launch_event=args.manual_event,
            )
            output = Path(args.output).resolve()
            payload = {
                "ok": True,
                "command": args.command,
                "run_id": SOURCE_RUN_ID,
                "output_dir": str(output),
                "report_path": str(output / "experiment_report.json"),
                "review_queue_path": str(output / "experience_review_queue.json"),
                "task_demo": result.get("experiment2_task_demo_stage"),
                "resource_summary": result.get("resource_summary"),
            }
        elif args.command == "review":
            review = review_source(
                args.source_output,
                disposition=args.disposition,
                reason=args.reason,
            )
            payload = {"ok": True, "command": args.command, **review}
        else:
            _load_env_files(args.env_file)
            result = run_later(
                args.root,
                manifest_path=args.manifest,
                snapshot_path=args.snapshot,
                output_dir=args.output,
                manual_launch_event=args.manual_event,
            )
            output = Path(args.output).resolve()
            payload = {
                "ok": True,
                "command": args.command,
                "run_id": LATER_RUN_ID,
                "output_dir": str(output),
                "report_path": str(output / "experiment_report.json"),
                "experience_id": result.get("experiment2_loaded_experience_id"),
                "task_demo": result.get("experiment2_task_demo_stage"),
                "resource_summary": result.get("resource_summary"),
            }
    except Experiment2RunnerError as exc:
        _print_cli(
            {
                "ok": False,
                "command": args.command,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            error=True,
        )
        return 2
    except Exception as exc:  # do not echo provider payloads or credentials
        _print_cli(
            {
                "ok": False,
                "command": args.command,
                "error_type": type(exc).__name__,
                "error": "unexpected runner failure; inspect retained artifacts",
            },
            error=True,
        )
        return 3
    _print_cli(payload)
    return 0


__all__ = [
    "CONDITION",
    "EXPERIENCE_ID",
    "EXPERIMENT_ID",
    "Experiment2RunnerError",
    "LATER_RUN_ID",
    "OPUS_MODEL_ID",
    "ROBOT_CONFIGURATION",
    "SONNET_MODEL_ID",
    "SOURCE_RUN_ID",
    "load_manifest",
    "main",
    "review_source",
    "run_later",
    "run_source",
    "validate_design_manifest",
    "validate_executable_preflight",
]


if __name__ == "__main__":
    raise SystemExit(main())
