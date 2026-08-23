"""Thin formal runner for the fixed Experiment 3 cohort.

Design validation is deliberately separate from executable preflight.  The
checked-in prospective manifest can therefore prove the fixed 33-cell design
while formal dispatch remains blocked until endpoint, price, resource, and
readiness evidence pins have been added prospectively.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineError,
    check_packages,
    run_experiment,
)


EXPERIMENT_ID = "experiment3-direct-mujoco-cohort-r3"
MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
CONDITION = "skeleton-assisted"
ROBOT_CONFIGURATIONS = (
    "robotstudio_so101",
    "unitree-go2-stock-12dof",
    "franka_panda",
    "kinova_gen3_robotiq_2f85",
    "ufactory_xarm7",
    "universal_robots_ur5e_robotiq_2f85",
    "piper",
    "kuka_iiwa_14",
    "leap_hand",
    "hello_robot_stretch_2",
    "aloha_2",
)
MORPHOLOGY_LABELS = {
    "robotstudio_so101": "fixed serial arm",
    "unitree-go2-stock-12dof": "quadruped",
    "franka_panda": "fixed serial arm",
    "kinova_gen3_robotiq_2f85": "fixed serial arm",
    "ufactory_xarm7": "fixed serial arm",
    "universal_robots_ur5e_robotiq_2f85": "fixed serial arm",
    "piper": "fixed serial arm",
    "kuka_iiwa_14": "fixed serial arm",
    "leap_hand": "dexterous hand",
    "hello_robot_stretch_2": "mobile manipulator",
    "aloha_2": "bimanual manipulator",
}
REPLICATE_IDS = ("r01", "r02", "r03")
EXPECTED_RETRY_POLICY = {
    "maximum_physical_requests": 2,
    "backoff_seconds": 1.0,
    "retryable_http_statuses": [429, 500, 502, 503, 504],
}
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
GIT_COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
AUTHORITY_REVISION = "0.1.2"
MANIFEST_REVISION = "0.1.1"
PROTOCOL_REVISION = "0.1.1"
DRIVER_PROBE_CALLS_PER_STAGE = 25
COMPLETE_DRIVER_CHECKS_PER_STAGE = 2
FORMAL_GIT_PATHS = (
    "AUTOADAPTER_2_AUTHORITY.md",
    "experiment/experiment3",
    "autoadapter/src/autoadapter2",
    ":(exclude)autoadapter/src/autoadapter2/b2",
    "autoadapter/libraries/robots",
    "autoadapter/research/robots",
    "autoadapter/pyproject.toml",
)


class Experiment3RunnerError(RuntimeError):
    """Raised when the fixed design or formal dispatch boundary is violated."""


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else Path(__file__).with_name("manifest.json")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment3RunnerError(f"cannot read Experiment 3 manifest {source}") from exc
    if not isinstance(value, Mapping):
        raise Experiment3RunnerError("Experiment 3 manifest must be one object")
    return copy.deepcopy(dict(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Experiment3RunnerError(message)


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
            raise Experiment3RunnerError(f"cannot read env file {path}") from exc
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
    """Validate only the prospectively fixed 33-cell experimental design."""

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
    _require(
        tuple(manifest.get("robot_configurations", ())) == ROBOT_CONFIGURATIONS,
        "robot_configurations must be the exact declared eleven in authority order",
    )
    _require(
        tuple(manifest.get("replicate_ids", ())) == REPLICATE_IDS,
        "replicate_ids must be exactly r01, r02, r03",
    )
    _require(manifest.get("generation_condition") == CONDITION, "generation condition must be skeleton-assisted")
    _require(manifest.get("conditions") == [CONDITION], "conditions must contain skeleton-assisted only")
    _require(manifest.get("experience_input") == "empty", "Experience input must be empty")
    _require(manifest.get("maximum_submitted_driver_attempts") == 3, "maximum submitted drivers must be three")
    _require(manifest.get("repair_attempts_after_initial") == 2, "Repair allowance must be two after attempt 0")
    _require(manifest.get("run_task_demo") is True, "Task Demo must be enabled")
    _require(manifest.get("stop_after") == "Task-Demo", "Experiment 3 must stop after Task Demo")
    _require(manifest.get("morphology_role") == "descriptive-only", "morphology must be descriptive-only")
    _require(
        manifest.get("morphology_labels") == MORPHOLOGY_LABELS,
        "morphology_labels must be the fixed descriptive labels for the eleven configurations",
    )

    model = manifest.get("model")
    _require(isinstance(model, Mapping), "model must be pinned")
    assert isinstance(model, Mapping)
    _require(model.get("family") == "Sonnet 4.6", "model family must be Sonnet 4.6")
    _require(model.get("exact_model_id") == MODEL_ID, "model ID must be the fixed Sonnet 4.6 ID")
    _require(model.get("temperature") == 0.0, "temperature must be 0")
    _require(model.get("context_limit_tokens") == 1_000_000, "context limit must be 1,000,000")
    _require(model.get("max_output_tokens") == 16_384, "max output must be 16,384")

    evolution = manifest.get("evolution")
    _require(isinstance(evolution, Mapping), "evolution exclusion must be explicit")
    assert isinstance(evolution, Mapping)
    _require(
        evolution.get("enabled") is False
        and evolution.get("terminal_call") is False
        and evolution.get("human_disposition") is False
        and evolution.get("experience_output") is False,
        "Evolution, review, and Experience output must all be disabled",
    )
    denominator = manifest.get("denominator")
    _require(isinstance(denominator, Mapping), "denominator must be declared")
    assert isinstance(denominator, Mapping)
    _require(
        denominator.get("cells") == 33
        and denominator.get("robot_count") == 11
        and denominator.get("replicate_count") == 3
        and denominator.get("condition_count") == 1
        and denominator.get("backbone_count") == 1,
        "denominator must be exactly 11 x 3 x 1 x 1 = 33",
    )
    return copy.deepcopy(dict(manifest))


def expand_cells(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """Predeclare all cells in replicate-major order without executing them."""

    checked = validate_design_manifest(manifest)
    experiment_id = str(checked["experiment_id"])
    return [
        {
            "cell_id": f"{experiment_id}::{replicate_id}::{robot}",
            "experiment_id": experiment_id,
            "replicate_id": replicate_id,
            "robot_configuration_id": robot,
            "morphology_label": MORPHOLOGY_LABELS[robot],
            "generation_condition": CONDITION,
            "run_id": f"{experiment_id}-{replicate_id}-{robot}",
        }
        for replicate_id in REPLICATE_IDS
        for robot in ROBOT_CONFIGURATIONS
    ]


def _pipeline_model(value: Any, *, label: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"runtime.{label} must be a pipeline model manifest")
    assert isinstance(value, Mapping)
    # ExperimentConfig is the single runtime authority for this exact shape,
    # including HTTPS endpoint and dated price fields.
    try:
        checked = ExperimentConfig.from_mapping(
            {
                "experiment_id": "experiment3-model-pin-check",
                "robots": [ROBOT_CONFIGURATIONS[0]],
                "generation_conditions": [CONDITION],
                "model": dict(value),
            }
        ).model_manifest
    except PipelineError as exc:
        raise Experiment3RunnerError(f"runtime.{label} is incomplete: {exc}") from exc
    assert checked is not None
    _require(checked["model_id"] == MODEL_ID, f"runtime.{label}.model_id must be {MODEL_ID}")
    _require(checked["context_window_tokens"] == 1_000_000, f"runtime.{label} context must be 1,000,000")
    _require(checked["max_output_tokens"] == 16_384, f"runtime.{label} max output must be 16,384")
    _require(checked["temperature"] == 0.0, f"runtime.{label} temperature must be 0")
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
    _require(
        set(development)
        == {
            "max_requests_per_stage",
            "max_complete_driver_checks",
            "wall_timeout_s_per_request",
            "max_output_chars_per_request",
        },
        "development_probe resource fields are incomplete",
    )
    for key in development:
        number = development[key]
        _require(isinstance(number, (int, float)) and not isinstance(number, bool) and float(number) > 0, f"development_probe.{key} must be positive")
    _require(
        development.get("max_requests_per_stage") == DRIVER_PROBE_CALLS_PER_STAGE,
        "development_probe.max_requests_per_stage must reserve 25 calls",
    )
    _require(
        development.get("max_complete_driver_checks")
        == COMPLETE_DRIVER_CHECKS_PER_STAGE,
        "development_probe.max_complete_driver_checks must be 2",
    )
    _require(set(validation) == {"record_video", "worker_wall_timeout_s"}, "validation resource fields are incomplete")
    _require(validation.get("record_video") is True, "formal Experiment 3 requires video")
    timeout = validation.get("worker_wall_timeout_s")
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and float(timeout) > 0, "validation.worker_wall_timeout_s must be positive")
    return copy.deepcopy(dict(value))


def _evidence_covers_robot(retained: Mapping[str, Any], robot: str) -> bool:
    direct = retained.get("robot_configuration_id")
    if direct == robot:
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
    if label.startswith("packages."):
        robot = label.removeprefix("packages.")
        _require(
            artifact_type == "experiment3_package_check_evidence"
            and _evidence_covers_robot(retained, robot),
            f"readiness_evidence.{label} does not prove that package",
        )
        return
    if label.startswith("reference_positive_controls."):
        robot = label.removeprefix("reference_positive_controls.")
        _require(
            isinstance(artifact_type, str)
            and artifact_type.startswith("experiment3_reference_positive_control")
            and _evidence_covers_robot(retained, robot),
            f"readiness_evidence.{label} does not prove that reference control",
        )
        return
    if label == "producer_model_canary":
        settings = retained.get("request_settings")
        _require(
            artifact_type == "experiment3_exact_sonnet_runtime_pin_canary"
            and retained.get("requested_model") == MODEL_ID
            and retained.get("returned_model") == MODEL_ID
            and retained.get("returned_model_matches_pin") is True
            and isinstance(settings, Mapping)
            and settings.get("temperature") == 0.0
            and settings.get("context_limit_tokens") == 1_000_000
            and settings.get("max_output_tokens") == 16_384,
            "readiness_evidence.producer_model_canary does not prove the exact Sonnet pin",
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
                raise Experiment3RunnerError(
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
    raise Experiment3RunnerError(f"unsupported readiness evidence role: {label}")


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
        raise Experiment3RunnerError(
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
    """Require all runtime pins and all-eleven evidence before any model call."""

    checked = validate_design_manifest(manifest)
    root = Path(mainline_root).resolve()
    runtime = checked.get("runtime")
    _require(isinstance(runtime, Mapping), "formal dispatch requires a pinned runtime block")
    assert isinstance(runtime, Mapping)
    _require(
        set(runtime) == {"producer_model", "producer_transport", "resources"},
        "runtime must pin producer_model, producer_transport, and resources",
    )
    producer_model = _pipeline_model(runtime.get("producer_model"), label="producer_model")
    producer_transport = _transport_pin(runtime.get("producer_transport"), label="producer_transport")
    resources = _resources(runtime.get("resources"))

    evidence = checked.get("readiness_evidence")
    _require(isinstance(evidence, Mapping), "formal dispatch requires readiness_evidence")
    assert isinstance(evidence, Mapping)
    required_global = {
        "producer_model_canary",
        "candidate_isolation",
        "canonical_physics",
        "independent_harness",
        "recorder",
    }
    _require(
        set(evidence) == {"packages", "reference_positive_controls", *required_global},
        "readiness_evidence fields are incomplete",
    )
    packages = evidence.get("packages")
    controls = evidence.get("reference_positive_controls")
    _require(isinstance(packages, Mapping) and set(packages) == set(ROBOT_CONFIGURATIONS), "package evidence must cover the exact eleven configurations")
    _require(isinstance(controls, Mapping) and set(controls) == set(ROBOT_CONFIGURATIONS), "reference positive-control evidence must cover the exact eleven configurations")
    assert isinstance(packages, Mapping) and isinstance(controls, Mapping)
    checked_evidence: dict[str, Any] = {
        "packages": {
            robot: _evidence_item(packages[robot], label=f"packages.{robot}", root=root)
            for robot in ROBOT_CONFIGURATIONS
        },
        "reference_positive_controls": {
            robot: _evidence_item(controls[robot], label=f"reference_positive_controls.{robot}", root=root)
            for robot in ROBOT_CONFIGURATIONS
        },
    }
    for key in sorted(required_global):
        checked_evidence[key] = _evidence_item(evidence[key], label=key, root=root)
    return {
        "producer_model": producer_model,
        "producer_transport": producer_transport,
        "resources": resources,
        "readiness_evidence": checked_evidence,
    }


def _assert_client_pin(client: Any, *, model: Mapping[str, Any], transport: Mapping[str, Any]) -> None:
    config = getattr(client, "config", None)
    _require(config is not None, "client_factory must return a client with a pinned config")
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
        _require(actual == value, f"Producer client {field} does not match the formal pin")


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
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _current_git_commit(mainline_root: str | Path) -> str:
    """Return HEAD after rejecting dirty Experiment 3 execution inputs."""

    try:
        root_result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(mainline_root).resolve()),
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Experiment3RunnerError("cannot read the current Git commit") from exc
    _require(root_result.returncode == 0, "cannot read the current Git commit")
    repository_root = root_result.stdout.strip()
    try:
        commit_result = subprocess.run(
            ["git", "-C", repository_root, "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status_result = subprocess.run(
            [
                "git",
                "-C",
                repository_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *FORMAL_GIT_PATHS,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Experiment3RunnerError("cannot verify the current Git commit") from exc
    _require(commit_result.returncode == 0, "cannot read the current Git commit")
    _require(status_result.returncode == 0, "cannot verify committed Experiment 3 inputs")
    dirty = status_result.stdout.strip()
    _require(
        not dirty,
        "formal dispatch requires committed Experiment 3 inputs; dirty paths: "
        + dirty.replace("\n", "; "),
    )
    return commit_result.stdout.strip()


def _revision_evidence(
    manifest: Mapping[str, Any], *, git_commit: str
) -> dict[str, str]:
    evidence = {
        "authority_revision": str(manifest.get("authority_revision", "")),
        "manifest_revision": str(manifest.get("manifest_revision", "")),
        "protocol_revision": str(manifest.get("protocol_revision", "")),
        "git_commit": git_commit.strip(),
    }
    _require(
        GIT_COMMIT_PATTERN.fullmatch(evidence["git_commit"]) is not None,
        "formal dispatch requires a non-empty full Git commit",
    )
    return evidence


def _assert_returned_identity(client: Any, expected_model_id: str) -> None:
    calls = getattr(client, "calls", None)
    _require(isinstance(calls, list) and bool(calls), "Producer returned-model evidence is missing")
    exact_identity_seen = False
    for index, call in enumerate(calls):
        _require(isinstance(call, Mapping), f"Producer call {index} is not an evidence record")
        returned = call.get("returned_model")
        if returned is None:
            missing_identity_allowed = (
                call.get("status") == "http_error"
                and call.get("http_status")
                in EXPECTED_RETRY_POLICY["retryable_http_statuses"]
            )
            _require(
                missing_identity_allowed,
                "Producer successful or non-retry call omitted returned model identity",
            )
            continue
        _require(
            returned == expected_model_id,
            "Producer returned model identity differs from the formal Sonnet pin",
        )
        exact_identity_seen = True
    _require(exact_identity_seen, "Producer returned-model evidence is missing")


def _assert_attempt_ceiling(result: Mapping[str, Any]) -> Mapping[str, Any]:
    cells = result.get("cells")
    _require(isinstance(cells, list) and len(cells) == 1, "singleton run must retain exactly one cell")
    cell = cells[0]
    _require(isinstance(cell, Mapping), "singleton cell result must be an object")
    attempt_count = cell.get("attempt_count")
    _require(
        isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and 0 <= attempt_count <= 3,
        "cell submitted more than three drivers or omitted its attempt count",
    )
    return cell


def _task_demo_terminal(cell: Mapping[str, Any]) -> dict[str, Any]:
    admitted = cell.get("final_capability_validation_passed")
    _require(
        isinstance(admitted, bool),
        "cell omitted its final capability-admission verdict",
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
    raise Experiment3RunnerError(
        "a cell without an admitted final driver requires a truthful Task Demo not-run reason"
    )


def _assert_formal_cell_evidence(
    result: Mapping[str, Any],
    cell: Mapping[str, Any],
    *,
    robot: str,
    run_id: str,
) -> None:
    """Check only the evidence fields needed to keep one formal row truthful."""

    _require(result.get("run_id") == run_id, "singleton result run_id differs from its declared cell")
    _require(
        cell.get("cell_id") == f"{robot}::{CONDITION}"
        and cell.get("robot_configuration_id") == robot
        and cell.get("condition") == CONDITION,
        "singleton result cell identity differs from its declared robot and condition",
    )
    outcomes = cell.get("outcomes")
    _require(isinstance(outcomes, Mapping), "singleton result lacks stage outcomes")
    assert isinstance(outcomes, Mapping)
    for stage in ("TGCD", "IVC"):
        evidence = outcomes.get(stage)
        _require(
            isinstance(evidence, Mapping) and evidence.get("completed") is True,
            f"singleton result lacks completed cell-local {stage} evidence",
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


def _cell_config(preflight: Mapping[str, Any], robot: str) -> dict[str, Any]:
    resources = preflight["resources"]
    return {
        "experiment_id": EXPERIMENT_ID,
        "robots": [robot],
        "generation_conditions": [CONDITION],
        "max_driver_attempts_per_condition": 3,
        "development_probe": copy.deepcopy(resources["development_probe"]),
        "validation": copy.deepcopy(resources["validation"]),
        "model": copy.deepcopy(preflight["producer_model"]),
        "experience": {
            "input": [],
            "review_queue_output": "experience_review_queue.json",
            "snapshot_output": "experience_snapshot.json",
        },
        "evolution": {"enabled": False},
        "seeds": {"task_demo_selection": "{run_id}:{robot_configuration_id}"},
    }


def run_preflight(
    mainline_root: str | Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    package_check_fn: Callable[..., Mapping[str, Any]] = check_packages,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Validate retained pins and execute the current all-eleven package check."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    preflight = validate_executable_preflight(source, mainline_root=mainline_root)
    all_robot_config = _cell_config(preflight, ROBOT_CONFIGURATIONS[0])
    all_robot_config["robots"] = list(ROBOT_CONFIGURATIONS)
    package_check = copy.deepcopy(
        dict(
            package_check_fn(
                mainline_root,
                config=all_robot_config,
                check_self_containment=check_self_containment,
            )
        )
    )
    _require(
        package_check.get("package_check_passed") is True,
        "current all-11 package check did not pass",
    )
    package_robots = package_check.get("robots")
    _require(
        isinstance(package_robots, Mapping)
        and set(package_robots) == set(ROBOT_CONFIGURATIONS),
        "current package check did not cover the exact eleven configurations",
    )
    return {**preflight, "current_package_check": package_check}


def _update_record_counts(record: dict[str, Any]) -> None:
    rows = record["cells"]
    record["completed_cells"] = sum(row["status"] == "completed" for row in rows)
    record["failed_cells"] = sum(row["status"] == "failed" for row in rows)
    record["predeclared_cells"] = sum(
        row["status"] == "predeclared" for row in rows
    )
    record["all_declared_cells_retained"] = len(rows) == 33


def _dispatch_predeclared_rows(
    mainline_root: str | Path,
    *,
    destination: Path,
    record: dict[str, Any],
    preflight: Mapping[str, Any],
    client_factory: Callable[
        [str, Mapping[str, Any], Mapping[str, Any], Mapping[str, str]], Any
    ]
    | None,
    run_experiment_fn: Callable[..., Mapping[str, Any]],
    hooks_factory: Callable[[Mapping[str, str]], Any] | None,
    check_self_containment: bool,
) -> dict[str, Any]:
    """Run only untouched rows; terminal rows are deliberately invisible here."""

    rows = record["cells"]
    seen_clients: list[Any] = []
    for row in rows:
        if row["status"] in {"completed", "failed"}:
            continue
        _require(row["status"] == "predeclared", "run record contains an unknown cell status")
        cell_started = time.monotonic()
        cell = {
            key: str(row[key])
            for key in (
                "cell_id",
                "experiment_id",
                "replicate_id",
                "robot_configuration_id",
                "morphology_label",
                "generation_condition",
                "run_id",
            )
        }
        robot = cell["robot_configuration_id"]
        workspace = destination / "cells" / cell["replicate_id"] / robot
        failure_stage = "cell-preflight"
        client: Any | None = None
        try:
            _require(not workspace.exists(), f"cell workspace already exists: {workspace}")
            failure_stage = "client-construction"
            client = (
                client_factory(
                    "producer",
                    copy.deepcopy(preflight["producer_model"]),
                    copy.deepcopy(preflight["producer_transport"]),
                    copy.deepcopy(cell),
                )
                if client_factory is not None
                else _built_in_client(
                    preflight["producer_model"], preflight["producer_transport"]
                )
            )
            _require(not any(client is prior for prior in seen_clients), "client_factory reused a prior cell client")
            failure_stage = "client-preflight"
            _assert_client_pin(client, model=preflight["producer_model"], transport=preflight["producer_transport"])
            seen_clients.append(client)
            kwargs: dict[str, Any] = {
                "config": _cell_config(preflight, robot),
                "output_dir": workspace,
                "run_id": cell["run_id"],
                "producer_client": client,
                "check_self_containment": check_self_containment,
                "skip_reference_calibration": True,
            }
            if hooks_factory is not None:
                kwargs["hooks"] = hooks_factory(copy.deepcopy(cell))
            failure_stage = "pipeline"
            result = copy.deepcopy(dict(run_experiment_fn(mainline_root, **kwargs)))
            row["result"] = result
            failure_stage = "result-postcheck"
            reported_cell = _assert_attempt_ceiling(result)
            task_demo_status = _task_demo_terminal(reported_cell)
            row["task_demo"] = task_demo_status
            _assert_formal_cell_evidence(
                result,
                reported_cell,
                robot=robot,
                run_id=cell["run_id"],
            )
            _assert_returned_identity(client, MODEL_ID)
            _require(result.get("evolution_enabled") is False, "Experiment 3 result enabled Evolution")
            _require(result.get("evolution_model") is None, "Experiment 3 result retained an Evolution model")
            experience_ids = result.get("experience_input_ids")
            _require(isinstance(experience_ids, Mapping) and experience_ids.get(robot) == [], "Experiment 3 cell consumed Experience")
            outcomes = reported_cell.get("outcomes")
            _require(isinstance(outcomes, Mapping) and outcomes.get("Evolution") is None, "Experiment 3 cell produced Evolution output")
            _require(not (workspace / "experience_review_queue.json").exists(), "Experiment 3 created an Experience review queue")
            row["status"] = "completed"
        except Exception as exc:  # retain this declared cell; never retry or replace it
            row["status"] = "failed"
            reason = str(exc).strip() or type(exc).__name__
            row["failure_stage"] = failure_stage
            row["failure"] = {
                "stage": failure_stage,
                "type": type(exc).__name__,
                "message": reason,
            }
            if "task_demo" not in row:
                row["task_demo"] = {
                    "status": "not-run",
                    "reason": f"cell failed at {failure_stage}: {reason}",
                }
        row["resource_summary"] = {
            "runner_wall_time_s": time.monotonic() - cell_started,
            "producer": (
                _resource_summary(client, preflight["producer_model"])
                if client is not None
                else None
            ),
        }
        _update_record_counts(record)
        _write_json(destination / "experiment3_run_record.json", record)

    _update_record_counts(record)
    _write_json(destination / "experiment3_run_record.json", record)
    return copy.deepcopy(record)


def run_formal(
    mainline_root: str | Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    output_dir: str | Path,
    client_factory: Callable[
        [str, Mapping[str, Any], Mapping[str, Any], Mapping[str, str]], Any
    ]
    | None = None,
    run_experiment_fn: Callable[..., Mapping[str, Any]] = run_experiment,
    package_check_fn: Callable[..., Mapping[str, Any]] = check_packages,
    hooks_factory: Callable[[Mapping[str, str]], Any] | None = None,
    check_self_containment: bool = True,
    git_commit_fn: Callable[[str | Path], str] | None = None,
) -> dict[str, Any]:
    """Execute each predeclared cell once, retaining failures in denominator 33."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    declared = expand_cells(source)
    commit = (git_commit_fn or _current_git_commit)(mainline_root)
    revisions = _revision_evidence(source, git_commit=commit)
    destination = Path(output_dir).resolve()
    _require(not destination.exists() or not any(destination.iterdir()), "Experiment 3 output directory must be new or empty")
    rows: list[dict[str, Any]] = [
        {
            **cell,
            **revisions,
            "status": "predeclared",
            "result": None,
            "failure": None,
        }
        for cell in declared
    ]
    record: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "denominator": 33,
        **revisions,
        "dispatch_started": False,
        "cells": rows,
    }
    _update_record_counts(record)
    _write_json(destination / "experiment3_run_record.json", record)

    # This is deliberately after predeclaration and before client construction.
    preflight = run_preflight(
        mainline_root,
        manifest=source,
        package_check_fn=package_check_fn,
        check_self_containment=check_self_containment,
    )
    record["dispatch_started"] = True
    record["readiness_evidence"] = copy.deepcopy(preflight["readiness_evidence"])
    record["current_package_check"] = preflight["current_package_check"]
    _write_json(destination / "experiment3_run_record.json", record)
    return _dispatch_predeclared_rows(
        mainline_root,
        destination=destination,
        record=record,
        preflight=preflight,
        client_factory=client_factory,
        run_experiment_fn=run_experiment_fn,
        hooks_factory=hooks_factory,
        check_self_containment=check_self_containment,
    )


def _validate_resume_record(
    record: Mapping[str, Any],
    *,
    declared: list[dict[str, str]],
    revisions: Mapping[str, str],
) -> list[dict[str, Any]]:
    _require(record.get("experiment_id") == EXPERIMENT_ID, "resume record has the wrong experiment_id")
    _require(record.get("denominator") == 33, "resume record denominator must remain 33")
    for field, expected in revisions.items():
        _require(record.get(field) == expected, f"resume record {field} differs from the current pin")
    rows = record.get("cells")
    _require(isinstance(rows, list) and len(rows) == 33, "resume record must retain exactly 33 cells")
    identity_fields = (
        "cell_id",
        "experiment_id",
        "replicate_id",
        "robot_configuration_id",
        "morphology_label",
        "generation_condition",
        "run_id",
    )
    checked_rows: list[dict[str, Any]] = []
    for index, (raw_row, expected_cell) in enumerate(zip(rows, declared, strict=True)):
        _require(isinstance(raw_row, dict), f"resume cell {index} must be an object")
        row = raw_row
        for field in identity_fields:
            _require(
                row.get(field) == expected_cell[field],
                f"resume cell {index} {field} differs from the declared design",
            )
        for field, expected in revisions.items():
            _require(
                row.get(field) == expected,
                f"resume cell {index} {field} differs from the current pin",
            )
        _require(
            row.get("status") in {"predeclared", "completed", "failed"},
            f"resume cell {index} has an unknown status",
        )
        checked_rows.append(row)
    expected_counts = {
        "completed_cells": sum(row["status"] == "completed" for row in checked_rows),
        "failed_cells": sum(row["status"] == "failed" for row in checked_rows),
        "predeclared_cells": sum(
            row["status"] == "predeclared" for row in checked_rows
        ),
    }
    for field, expected in expected_counts.items():
        _require(
            record.get(field) == expected,
            f"resume record {field} differs from its cell rows",
        )
    _require(
        record.get("all_declared_cells_retained") is True,
        "resume record must retain all declared cells",
    )
    return checked_rows


def run_resume(
    mainline_root: str | Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
    output_dir: str | Path,
    client_factory: Callable[
        [str, Mapping[str, Any], Mapping[str, Any], Mapping[str, str]], Any
    ]
    | None = None,
    run_experiment_fn: Callable[..., Mapping[str, Any]] = run_experiment,
    package_check_fn: Callable[..., Mapping[str, Any]] = check_packages,
    hooks_factory: Callable[[Mapping[str, str]], Any] | None = None,
    check_self_containment: bool = True,
    git_commit_fn: Callable[[str | Path], str] | None = None,
) -> dict[str, Any]:
    """Continue untouched cells in the same fixed formal run without retries."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    declared = expand_cells(source)
    commit = (git_commit_fn or _current_git_commit)(mainline_root)
    revisions = _revision_evidence(source, git_commit=commit)
    destination = Path(output_dir).resolve()
    record_path = destination / "experiment3_run_record.json"
    record = _read_record(record_path)
    rows = _validate_resume_record(record, declared=declared, revisions=revisions)

    # An already terminal record is a true no-op: no package check and no client.
    if all(row["status"] in {"completed", "failed"} for row in rows):
        return copy.deepcopy(record)

    partials_found = False
    for row in rows:
        if row["status"] != "predeclared":
            continue
        workspace = (
            destination
            / "cells"
            / str(row["replicate_id"])
            / str(row["robot_configuration_id"])
        )
        if not workspace.exists():
            continue
        partials_found = True
        stage = "resume-partial-workspace"
        reason = (
            "predeclared cell workspace already exists; retained as an "
            "interrupted infrastructure failure without rerun"
        )
        row["status"] = "failed"
        row["failure_stage"] = stage
        row["failure"] = {
            "stage": stage,
            "type": "InterruptedCellWorkspace",
            "message": reason,
        }
        row["task_demo"] = {"status": "not-run", "reason": reason}
        row["resource_summary"] = {
            "runner_wall_time_s": 0.0,
            "producer": None,
        }
    if partials_found:
        _update_record_counts(record)
        _write_json(record_path, record)

    if all(row["status"] in {"completed", "failed"} for row in rows):
        return copy.deepcopy(record)

    preflight = run_preflight(
        mainline_root,
        manifest=source,
        package_check_fn=package_check_fn,
        check_self_containment=check_self_containment,
    )
    record["dispatch_started"] = True
    record["readiness_evidence"] = copy.deepcopy(preflight["readiness_evidence"])
    record["current_package_check"] = preflight["current_package_check"]
    _write_json(record_path, record)
    return _dispatch_predeclared_rows(
        mainline_root,
        destination=destination,
        record=record,
        preflight=preflight,
        client_factory=client_factory,
        run_experiment_fn=run_experiment_fn,
        hooks_factory=hooks_factory,
        check_self_containment=check_self_containment,
    )


def _distribution(values: list[float | int]) -> dict[str, Any]:
    if not values:
        return {"observed": 0, "median": None, "range": None}
    return {
        "observed": len(values),
        "median": median(values),
        "range": [min(values), max(values)],
    }


def _count_metric(count: int, denominator: int = 3) -> dict[str, Any]:
    return {"count": count, "denominator": denominator, "proportion": count / denominator}


def summarise_results(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return descriptive per-configuration summaries for the fixed 33 cells."""

    _require(record.get("denominator") == 33, "result denominator must be exactly 33")
    rows = record.get("cells")
    _require(isinstance(rows, list) and len(rows) == 33, "result must retain exactly 33 cell rows")
    pairs: list[tuple[str, str]] = []
    for row in rows:
        _require(isinstance(row, Mapping), "every result row must be an object")
        assert isinstance(row, Mapping)
        robot = row.get("robot_configuration_id")
        replicate = row.get("replicate_id")
        _require(robot in ROBOT_CONFIGURATIONS, "result row has an undeclared configuration")
        _require(replicate in REPLICATE_IDS, "result row has an undeclared replicate")
        _require(row.get("generation_condition") == CONDITION, "result row has the wrong generation condition")
        _require(
            row.get("morphology_label") == MORPHOLOGY_LABELS[str(robot)],
            "result row has the wrong descriptive morphology label",
        )
        _require(row.get("status") in {"completed", "failed"}, "result row is not terminal")
        pairs.append((str(robot), str(replicate)))
    pair_counts = Counter(pairs)
    expected_pairs = {
        (robot, replicate)
        for robot in ROBOT_CONFIGURATIONS
        for replicate in REPLICATE_IDS
    }
    _require(set(pair_counts) == expected_pairs and all(value == 1 for value in pair_counts.values()), "results must contain exactly r01-r03 once per configuration")

    configuration_summaries: dict[str, Any] = {}
    case_rows: list[dict[str, Any]] = []
    for row in rows:
        assert isinstance(row, Mapping)
        case_rows.append(
            {
                "cell_id": row.get("cell_id"),
                "robot_configuration_id": row.get("robot_configuration_id"),
                "morphology_label": row.get("morphology_label"),
                "replicate_id": row.get("replicate_id"),
                "status": row.get("status"),
                "failure_stage": row.get("failure_stage"),
                "failure": copy.deepcopy(row.get("failure")),
                "task_demo": copy.deepcopy(row.get("task_demo")),
            }
        )

    for robot in ROBOT_CONFIGURATIONS:
        robot_rows = [row for row in rows if row.get("robot_configuration_id") == robot]
        initial_passes = 0
        final_passes = 0
        task_demo_executed = 0
        task_demo_passed = 0
        task_demo_not_run = 0
        failures = 0
        attempts: list[int] = []
        model_calls: list[int] = []
        tokens: list[int] = []
        estimated_costs: list[float] = []
        wall_times: list[float] = []
        currencies: set[str] = set()
        for row in robot_rows:
            assert isinstance(row, Mapping)
            failures += int(row.get("status") == "failed")
            result = row.get("result")
            reported_cell: Mapping[str, Any] | None = None
            if isinstance(result, Mapping):
                reported = result.get("cells")
                if (
                    isinstance(reported, list)
                    and len(reported) == 1
                    and isinstance(reported[0], Mapping)
                ):
                    reported_cell = reported[0]
            if reported_cell is not None:
                initial_passes += int(
                    reported_cell.get("pass@0") is True
                    or reported_cell.get("initial_capability_validation_passed")
                    is True
                )
                final_passes += int(
                    reported_cell.get("final_capability_validation_passed") is True
                    or reported_cell.get("pass@k") is True
                )
                attempt_count = reported_cell.get("attempt_count")
                if isinstance(attempt_count, int) and not isinstance(attempt_count, bool):
                    attempts.append(attempt_count)
            demo = row.get("task_demo")
            if isinstance(demo, Mapping):
                task_demo_executed += int(demo.get("status") == "executed")
                task_demo_passed += int(
                    demo.get("status") == "executed" and demo.get("passed") is True
                )
                task_demo_not_run += int(demo.get("status") == "not-run")
            resources = row.get("resource_summary")
            if isinstance(resources, Mapping):
                wall = resources.get("runner_wall_time_s")
                if isinstance(wall, (int, float)) and not isinstance(wall, bool):
                    wall_times.append(float(wall))
                producer = resources.get("producer")
                if isinstance(producer, Mapping):
                    calls = producer.get("call_count")
                    if isinstance(calls, int) and not isinstance(calls, bool):
                        model_calls.append(calls)
                    categories = producer.get("token_categories")
                    total = categories.get("total_tokens") if isinstance(categories, Mapping) else None
                    if isinstance(total, int) and not isinstance(total, bool):
                        tokens.append(total)
                    cost = producer.get("estimated_cost")
                    amount = cost.get("amount") if isinstance(cost, Mapping) else None
                    currency = cost.get("currency") if isinstance(cost, Mapping) else None
                    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                        estimated_costs.append(float(amount))
                    if isinstance(currency, str) and currency:
                        currencies.add(currency)
        _require(len(currencies) <= 1, f"{robot} estimated costs use mixed currencies")
        configuration_summaries[robot] = {
            "morphology_label": MORPHOLOGY_LABELS[robot],
            "replicates": list(REPLICATE_IDS),
            "counts_and_proportions": {
                "pass@0": _count_metric(initial_passes),
                "final_post_repair_pass": _count_metric(final_passes),
                "task_demo_executed": _count_metric(task_demo_executed),
                "task_demo_passed": _count_metric(task_demo_passed),
                "task_demo_not_run": _count_metric(task_demo_not_run),
                "failures": _count_metric(failures),
            },
            "median_and_range": {
                "attempts": _distribution(attempts),
                "model_calls": _distribution(model_calls),
                "total_tokens": _distribution(tokens),
                "estimated_cost": {
                    **_distribution(estimated_costs),
                    "is_estimate": True,
                    "currency": next(iter(currencies), None),
                },
                "runner_wall_time_s": _distribution(wall_times),
            },
        }
    return {
        "experiment_id": EXPERIMENT_ID,
        "denominator": 33,
        "analysis": "descriptive per exact robot configuration; no morphology-effect or causal statistic",
        "configurations": configuration_summaries,
        "case_rows": case_rows,
    }


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiment.experiment3.runner",
        description="Validate, execute, and summarise the fixed 33-cell Experiment 3.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    design = commands.add_parser("design-check")
    design.add_argument("--manifest", required=True)

    preflight = commands.add_parser("preflight")
    preflight.add_argument("--root", required=True)
    preflight.add_argument("--manifest", required=True)

    formal = commands.add_parser("formal")
    formal.add_argument("--root", required=True)
    formal.add_argument("--manifest", required=True)
    formal.add_argument("--output", required=True)
    formal.add_argument("--env-file", action="append", default=[])

    resume = commands.add_parser("resume")
    resume.add_argument("--root", required=True)
    resume.add_argument("--manifest", required=True)
    resume.add_argument("--output", required=True)
    resume.add_argument("--env-file", action="append", default=[])

    summarise = commands.add_parser("summarise")
    summarise.add_argument("--record", required=True)
    summarise.add_argument("--output", required=True)
    return parser


def _print_cli(value: Mapping[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(value, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
    )


def _read_record(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment3RunnerError(f"cannot read Experiment 3 run record {source}") from exc
    _require(isinstance(value, Mapping), "Experiment 3 run record must be one object")
    return copy.deepcopy(dict(value))


def main(argv: list[str] | None = None) -> int:
    """Run one CLI action and emit only a compact, secret-free JSON result."""

    args = _cli_parser().parse_args(argv)
    try:
        if args.command == "design-check":
            cells = expand_cells(load_manifest(args.manifest))
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": EXPERIMENT_ID,
                "cell_count": len(cells),
                "replicate_ids": list(REPLICATE_IDS),
                "robot_configuration_count": len(ROBOT_CONFIGURATIONS),
                "generation_condition": CONDITION,
            }
        elif args.command == "preflight":
            checked = run_preflight(
                args.root,
                manifest_path=args.manifest,
            )
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": EXPERIMENT_ID,
                "model_id": checked["producer_model"]["model_id"],
                "retained_package_evidence_count": len(
                    checked["readiness_evidence"]["packages"]
                ),
                "retained_reference_control_count": len(
                    checked["readiness_evidence"]["reference_positive_controls"]
                ),
                "current_package_check_passed": checked[
                    "current_package_check"
                ]["package_check_passed"],
            }
        elif args.command in {"formal", "resume"}:
            _load_env_files(args.env_file)
            action = run_formal if args.command == "formal" else run_resume
            record = action(
                args.root,
                manifest_path=args.manifest,
                output_dir=args.output,
            )
            output = Path(args.output).resolve()
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": EXPERIMENT_ID,
                "output_dir": str(output),
                "record_path": str(output / "experiment3_run_record.json"),
                "denominator": record.get("denominator"),
                "completed_cells": record.get("completed_cells"),
                "failed_cells": record.get("failed_cells"),
                "all_declared_cells_retained": record.get(
                    "all_declared_cells_retained"
                ),
            }
        else:
            summary = summarise_results(_read_record(args.record))
            output = Path(args.output).resolve()
            _write_json(output, summary)
            payload = {
                "ok": True,
                "command": args.command,
                "experiment_id": EXPERIMENT_ID,
                "summary_path": str(output),
                "denominator": summary["denominator"],
                "configuration_count": len(summary["configurations"]),
            }
    except Experiment3RunnerError as exc:
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
    "AUTHORITY_REVISION",
    "CONDITION",
    "EXPERIMENT_ID",
    "Experiment3RunnerError",
    "MANIFEST_REVISION",
    "MODEL_ID",
    "MORPHOLOGY_LABELS",
    "PROTOCOL_REVISION",
    "REPLICATE_IDS",
    "ROBOT_CONFIGURATIONS",
    "expand_cells",
    "load_manifest",
    "main",
    "run_formal",
    "run_preflight",
    "run_resume",
    "summarise_results",
    "validate_design_manifest",
    "validate_executable_preflight",
]


if __name__ == "__main__":
    raise SystemExit(main())
