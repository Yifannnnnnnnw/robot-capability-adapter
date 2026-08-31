"""Thin formal runner for the fixed Experiment 3 cohort.

Design validation is deliberately separate from executable preflight. Formal
dispatch is authorised only through the checked-in manifest and still cannot
construct a model client until all eleven fixed design/suite pairs and the
remaining executable readiness pins pass the zero-model preflight.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
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

from autoadapter2.provider_config import (
    HolisticAIRouteProfile,
    ProviderConfigError,
    reject_inline_holisticai_route_fields,
    resolve_holisticai_route_profile,
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
DEFAULT_HOLISTICAI_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.holisticai-api"
AUTHORITY_REVISION = "0.3.0"
MANIFEST_REVISION = "0.3.0"
PROTOCOL_REVISION = "0.3.0"
FIXED_INPUT_INDEX = "experiment/experiment3/fixed_inputs/index.json"
FIXED_INPUT_SMOKE_REPORT = "experiment/experiment3/fixed_inputs/smoke_report.json"
FIXED_INPUT_CALIBRATION_REPORT = (
    "experiment/experiment3/fixed_inputs/calibration_report.json"
)
NUMERIC_TOLERANCE_RULE = (
    "64 * IEEE-754 binary64 epsilon * max(1, abs(lower), abs(upper), span)"
)
PUBLIC_OR_TRACKED_CRITERION_SOURCE_IDS = frozenset(
    {
        "b1_driver_validation_criteria",
        "leap_hand_if_tip_public_reach",
        "leap_hand_mf_tip_public_reach",
        "leap_hand_rf_tip_public_reach",
        "leap_hand_th_tip_public_reach",
        "leap_hand_public_sixteen_joint_pose",
        "hello_robot_stretch_2_tracked_st1_base_translation",
        "aloha_2_tracked_al1_left_endpoint",
        "aloha_2_tracked_al1_right_endpoint",
        *{
            f"{robot}_public_reach_and_real_reset"
            for robot in (
                "franka_panda",
                "kinova_gen3_robotiq_2f85",
                "ufactory_xarm7",
                "universal_robots_ur5e_robotiq_2f85",
                "piper",
                "kuka_iiwa_14",
                "hello_robot_stretch_2",
            )
        },
        *{
            f"{robot}_real_reset_to_target_displacement"
            for robot in (
                "franka_panda",
                "kinova_gen3_robotiq_2f85",
                "ufactory_xarm7",
                "universal_robots_ur5e_robotiq_2f85",
                "piper",
                "kuka_iiwa_14",
                "hello_robot_stretch_2",
            )
        },
    }
)
PHASE_TURN_BUDGETS = {
    "study": 16,
    "generate_skeleton": 22,
    "repair_skeleton": 22,
}
RECAP_BUDGET = {
    "max_planning_turns_per_task": 16,
    "max_capability_calls_per_task": 12,
}
FORMAL_GIT_PATHS = (
    "AUTOADAPTER_2_AUTHORITY.md",
    "experiment/experiment3/EXPERIMENT_3_AUTHORITY.md",
    "experiment/experiment3/PROTOCOL.md",
    "experiment/experiment3/manifest.json",
    "experiment/experiment3/runner.py",
    "experiment/experiment3/fixed_inputs",
    "autoadapter/src/autoadapter2",
    ":(exclude)autoadapter/src/autoadapter2/b2",
    "autoadapter/libraries/robots/index.json",
    "autoadapter/libraries/robots/robotstudio_so101/1.0.4",
    "autoadapter/libraries/robots/unitree-go2-stock-12dof/1.0.0",
    "autoadapter/libraries/robots/franka_panda/1.0.0",
    "autoadapter/libraries/robots/kinova_gen3_robotiq_2f85/1.0.0",
    "autoadapter/libraries/robots/ufactory_xarm7/1.0.0",
    "autoadapter/libraries/robots/universal_robots_ur5e_robotiq_2f85/1.0.0",
    "autoadapter/libraries/robots/piper/1.0.0",
    "autoadapter/libraries/robots/kuka_iiwa_14/1.0.0",
    "autoadapter/libraries/robots/leap_hand/1.0.0",
    "autoadapter/libraries/robots/hello_robot_stretch_2/1.0.0",
    "autoadapter/libraries/robots/aloha_2/1.0.0",
    "autoadapter/pyproject.toml",
)


class Experiment3RunnerError(RuntimeError):
    """Raised when the fixed design or formal dispatch boundary is violated."""


class Experiment3SystemicError(Experiment3RunnerError):
    """Raised when continuing would make untouched rows incomparable or unsafe."""


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

    selected_paths = paths
    if not selected_paths and DEFAULT_HOLISTICAI_ENV_FILE.is_file():
        selected_paths = [str(DEFAULT_HOLISTICAI_ENV_FILE)]
    for path_value in selected_paths:
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
    _require(manifest.get("schema_version") == 2, "schema_version must be 2")
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
        manifest.get("status") == "formal-authorised"
        and manifest.get("formal_dispatch_authorised") is True,
        "the current Authority requires the explicitly authorised formal route",
    )
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
    _require(manifest.get("maximum_frozen_driver_attempts") == 3, "maximum frozen drivers must be three")
    _require(manifest.get("repair_attempts_after_initial") == 2, "Repair allowance must be two after attempt 0")
    _require(manifest.get("run_task_demo") is True, "Task Demo must be enabled")
    _require(manifest.get("stop_after") == "ReCAP-Task-Demo", "Experiment 3 must stop after ReCAP Task Demo")
    _require(
        manifest.get("fresh_end_to_end_per_cell")
        == {
            "study": True,
            "tgcd": False,
            "ivc": False,
            "workspace": True,
            "model_conversation": True,
            "canonical_reset": True,
        },
        "every cell must use fresh STUDY/workspace/conversation/reset and skip TGCD/IVC",
    )
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

    workflow = manifest.get("workflow")
    _require(isinstance(workflow, Mapping), "workflow must be pinned")
    assert isinstance(workflow, Mapping)
    _require(
        workflow.get("order")
        == [
            "STUDY",
            "GENERATE",
            "Capability-Validation",
            "Repair",
            "ReCAP-Task-Demo",
        ],
        "workflow must use the fixed-input STUDY-to-Generate route",
    )
    _require(
        workflow.get("aggregate_tool_call_limit") is None,
        "workflow must not impose an aggregate tool-call limit",
    )
    _require(
        workflow.get("maximum_frozen_driver_attempts") == 3,
        "workflow must freeze at most three drivers",
    )

    _require(
        manifest.get("fixed_input_set") == FIXED_INPUT_INDEX,
        f"fixed_input_set must point to {FIXED_INPUT_INDEX}",
    )
    _require(
        manifest.get("fixed_input_smoke_report") == FIXED_INPUT_SMOKE_REPORT,
        f"fixed_input_smoke_report must point to {FIXED_INPUT_SMOKE_REPORT}",
    )
    _require(
        manifest.get("fixed_input_calibration_report")
        == FIXED_INPUT_CALIBRATION_REPORT,
        "fixed_input_calibration_report must point to "
        f"{FIXED_INPUT_CALIBRATION_REPORT}",
    )
    fixed_contract = manifest.get("fixed_input_contract")
    _require(isinstance(fixed_contract, Mapping), "fixed_input_contract must be pinned")
    assert isinstance(fixed_contract, Mapping)
    _require(
        dict(fixed_contract)
        == {
            "one_design_and_suite_per_robot": True,
            "same_pair_across_replicates": True,
            "validated_before_model_calls": True,
            "real_mujoco_smoke_required": True,
            "real_threshold_calibration_required": True,
            "minimum_capabilities_per_robot": 5,
            "design_protocol": "capability-v2",
            "cases_per_capability": ["nominal", "calibrated_boundary"],
            "suite_candidate_blind": True,
            "inline_measurement_binding_required": True,
            "binding_id_forbidden": True,
            "candidate_task_id_dispatch_forbidden": True,
        },
        "fixed inputs must provide one validated candidate-blind capability-v2 pair per robot",
    )
    _require(
        manifest.get("authoring_stages")
        == {
            "tgcd": {"mode": "skipped-fixed-input", "model_call_budget": 0},
            "ivc": {"mode": "skipped-fixed-input", "model_call_budget": 0},
        },
        "TGCD and IVC must both be fixed-input zero-call stages",
    )
    _require(
        "reporting_groups" not in manifest,
        "reference-seen and transfer reporting groups are not part of Experiment 3",
    )

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


def _pipeline_model(
    value: Any, *, label: str, route: HolisticAIRouteProfile
) -> dict[str, Any]:
    from autoadapter2.pipeline import ExperimentConfig, PipelineError

    _require(isinstance(value, Mapping), f"runtime.{label} must be a pipeline model manifest")
    assert isinstance(value, Mapping)
    route_fields = dict(value)
    route_fields.pop("api_protocol", None)
    try:
        reject_inline_holisticai_route_fields(route_fields, label=f"runtime.{label}")
    except ProviderConfigError as exc:
        raise Experiment3RunnerError(str(exc)) from exc
    _require(
        value.get("api_protocol") == route.api_protocol,
        f"runtime.{label}.api_protocol must match the holisticai route profile",
    )
    resolved_model = dict(value)
    resolved_model["base_url"] = route.base_url
    # ExperimentConfig is the single runtime authority for this exact shape,
    # including HTTPS endpoint and dated price fields.
    try:
        checked = ExperimentConfig.from_mapping(
            {
                "experiment_id": "experiment3-model-pin-check",
                "robots": [ROBOT_CONFIGURATIONS[0]],
                "generation_conditions": [CONDITION],
                "model": resolved_model,
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


def _transport_pin(
    value: Any, *, label: str, route: HolisticAIRouteProfile
) -> dict[str, Any]:
    required = {
        "request_timeout_s",
        "retry_policy",
        "history_char_budget",
    }
    _require(isinstance(value, Mapping), f"runtime.{label} must be pinned")
    assert isinstance(value, Mapping)
    try:
        reject_inline_holisticai_route_fields(value, label=f"runtime.{label}")
    except ProviderConfigError as exc:
        raise Experiment3RunnerError(str(exc)) from exc
    _require(set(value) == required, f"runtime.{label} must pin exactly {sorted(required)}")
    timeout = value.get("request_timeout_s")
    _require(
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and float(timeout) == float(route.maximum_request_timeout_s),
        f"runtime.{label}.request_timeout_s must equal the holisticai profile limit of {route.maximum_request_timeout_s}",
    )
    history = value.get("history_char_budget")
    _require(isinstance(history, int) and not isinstance(history, bool) and history > 0, f"runtime.{label}.history_char_budget must be positive")
    _require(value.get("retry_policy") == EXPECTED_RETRY_POLICY, f"runtime.{label}.retry_policy must pin the current model client policy")
    return {
        **copy.deepcopy(dict(value)),
        "endpoint_path": route.endpoint_path,
        "endpoint_region": route.endpoint_region,
        "credential_env": route.credential_env,
        "auth_header": route.auth_header,
        "auth_prefix": route.auth_prefix,
    }


def _resources(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "runtime.resources must be pinned")
    assert isinstance(value, Mapping)
    _require(
        set(value) == {"phase_turn_budgets", "execute_python", "recap", "validation"},
        "runtime.resources must pin phase turns, execute_python, ReCAP and validation",
    )
    phase_turns = value.get("phase_turn_budgets")
    execute_python = value.get("execute_python")
    recap = value.get("recap")
    validation = value.get("validation")
    _require(
        phase_turns == PHASE_TURN_BUDGETS,
        "runtime.resources.phase_turn_budgets must match the file-workflow budgets",
    )
    _require(isinstance(execute_python, Mapping), "runtime.resources.execute_python must be an object")
    _require(recap == RECAP_BUDGET, "runtime.resources.recap must be 16 planning turns and 12 capability calls")
    _require(isinstance(validation, Mapping), "runtime.resources.validation must be an object")
    assert isinstance(execute_python, Mapping) and isinstance(validation, Mapping)
    _require(
        set(execute_python)
        == {
            "wall_timeout_s_per_call",
            "max_output_chars_per_call",
            "max_steps_per_phase",
            "max_sim_time_s_per_phase",
        },
        "execute_python resource fields are incomplete",
    )
    for key in execute_python:
        number = execute_python[key]
        _require(
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and float(number) > 0,
            f"execute_python.{key} must be positive",
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
        set(runtime)
        == {
            "holisticai_route_profile",
            "producer_model",
            "producer_transport",
            "resources",
        },
        "runtime must pin one holisticai route profile, producer model/transport, and resources",
    )
    try:
        route = resolve_holisticai_route_profile(
            runtime.get("holisticai_route_profile"),
            _repository_root(root),
        )
    except ProviderConfigError as exc:
        raise Experiment3RunnerError(
            f"runtime.holisticai_route_profile is invalid: {exc}"
        ) from exc
    producer_model = _pipeline_model(
        runtime.get("producer_model"), label="producer_model", route=route
    )
    producer_transport = _transport_pin(
        runtime.get("producer_transport"), label="producer_transport", route=route
    )
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
        set(evidence) == {"packages", *required_global},
        "readiness_evidence fields are incomplete",
    )
    packages = evidence.get("packages")
    _require(isinstance(packages, Mapping) and set(packages) == set(ROBOT_CONFIGURATIONS), "package evidence must cover the exact eleven configurations")
    assert isinstance(packages, Mapping)
    checked_evidence: dict[str, Any] = {
        "packages": {
            robot: _evidence_item(packages[robot], label=f"packages.{robot}", root=root)
            for robot in ROBOT_CONFIGURATIONS
        },
    }
    for key in sorted(required_global):
        checked_evidence[key] = _evidence_item(evidence[key], label=key, root=root)
    return {
        "holisticai_route_profile": route.to_evidence_dict(),
        "producer_model": producer_model,
        "producer_transport": producer_transport,
        "resources": resources,
        "readiness_evidence": checked_evidence,
    }


def _repository_root(mainline_root: str | Path) -> Path:
    """Resolve the repository root from the normal ``--root autoadapter`` input."""

    root = Path(mainline_root).resolve()
    if (root / "src" / "autoadapter2").is_dir():
        return root.parent
    if (root / "autoadapter" / "src" / "autoadapter2").is_dir():
        return root
    # Tests may inject package loaders for a synthetic mainline root.  Relative
    # manifest paths still have one deterministic interpretation.
    return root.parent if root.name == "autoadapter" else root


def _fixed_input_index_path(
    manifest: Mapping[str, Any], *, mainline_root: str | Path
) -> Path:
    value = manifest.get("fixed_input_set")
    _require(value == FIXED_INPUT_INDEX, f"fixed_input_set must equal {FIXED_INPUT_INDEX}")
    return (_repository_root(mainline_root) / str(value)).resolve()


def _fixed_input_smoke_path(
    manifest: Mapping[str, Any], *, mainline_root: str | Path
) -> Path:
    value = manifest.get("fixed_input_smoke_report")
    _require(
        value == FIXED_INPUT_SMOKE_REPORT,
        f"fixed_input_smoke_report must equal {FIXED_INPUT_SMOKE_REPORT}",
    )
    return (_repository_root(mainline_root) / str(value)).resolve()


def _fixed_input_calibration_path(
    manifest: Mapping[str, Any], *, mainline_root: str | Path
) -> Path:
    value = manifest.get("fixed_input_calibration_report")
    _require(
        value == FIXED_INPUT_CALIBRATION_REPORT,
        "fixed_input_calibration_report must equal "
        f"{FIXED_INPUT_CALIBRATION_REPORT}",
    )
    return (_repository_root(mainline_root) / str(value)).resolve()


def _read_fixed_index(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Experiment3RunnerError(f"cannot read fixed-input index {path}") from exc
    _require(isinstance(value, Mapping), "fixed-input index must be one object")
    return copy.deepcopy(dict(value))


def _check_fixed_input_smoke_report(
    path: str | Path, *, input_set_id: str
) -> dict[str, Any]:
    """Require the retained all-robot real MuJoCo wiring smoke."""

    source = Path(path).resolve()
    report = _read_fixed_index(source)
    _require(
        set(report)
        == {"artifact_type", "schema_version", "input_set_id", "passed", "robots"},
        "fixed-input smoke report has unexpected or missing top-level fields",
    )
    _require(
        report.get("artifact_type") == "experiment3_fixed_input_mujoco_smoke"
        and report.get("schema_version") == "1.0"
        and report.get("input_set_id") == input_set_id
        and report.get("passed") is True,
        "fixed-input smoke identity, input_set_id, or overall verdict is invalid",
    )
    robots = report.get("robots")
    _require(
        isinstance(robots, Mapping)
        and tuple(robots) == ROBOT_CONFIGURATIONS
        and set(robots) == set(ROBOT_CONFIGURATIONS),
        "fixed-input smoke must contain the exact eleven robots in Authority order",
    )
    expected_fields = {
        "instance_id",
        "scene_entrypoint",
        "case_id",
        "operator_kind",
        "scene_loaded",
        "reset_applied",
        "trusted_operator_executed",
        "measurement_finite",
        "measurement_value",
        "passed",
    }
    checked_robots: dict[str, Any] = {}
    for robot in ROBOT_CONFIGURATIONS:
        item = robots[robot]
        _require(
            isinstance(item, Mapping) and set(item) == expected_fields,
            f"fixed-input smoke record for {robot!r} has the wrong fields",
        )
        for field in ("instance_id", "scene_entrypoint", "case_id", "operator_kind"):
            _require(
                isinstance(item.get(field), str) and bool(str(item[field]).strip()),
                f"fixed-input smoke {field} for {robot!r} must be non-empty text",
            )
        for field in (
            "scene_loaded",
            "reset_applied",
            "trusted_operator_executed",
            "measurement_finite",
            "passed",
        ):
            _require(
                item.get(field) is True,
                f"fixed-input smoke {field} did not pass for {robot!r}",
            )
        measurement = item.get("measurement_value")
        _require(
            isinstance(measurement, (int, float))
            and not isinstance(measurement, bool)
            and math.isfinite(float(measurement)),
            f"fixed-input smoke measurement for {robot!r} must be finite numeric evidence",
        )
        checked_robots[robot] = copy.deepcopy(dict(item))
    return {
        "path": str(source),
        "artifact_type": report["artifact_type"],
        "input_set_id": input_set_id,
        "passed": True,
        "robots": checked_robots,
    }


def _finite_number(value: Any, *, label: str) -> float:
    _require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{label} must be a finite number",
    )
    return float(value)


def _private_instance_scene(package: Any, *, instance_id: str) -> str:
    root = getattr(package, "root", None)
    _require(isinstance(root, Path), "fixed-input package root is unavailable")
    matches: list[str] = []
    for directory in (
        root / "capability_validation" / "private",
        getattr(package, "private_dir", root / "tasks" / "private"),
    ):
        path = directory / "instances.json"
        if not path.is_file():
            continue
        value = _read_fixed_index(path)
        instances = value.get("instances")
        _require(isinstance(instances, list), f"private instances are invalid in {path}")
        for item in instances:
            if isinstance(item, Mapping) and item.get("instance_id") == instance_id:
                scene = item.get("scene_entrypoint")
                _require(
                    isinstance(scene, str) and bool(scene.strip()),
                    f"private instance {instance_id!r} has no scene entrypoint",
                )
                matches.append(scene)
    _require(matches, f"cannot resolve private instance {instance_id!r}")
    _require(
        len(set(matches)) == 1,
        f"private instance {instance_id!r} resolves to conflicting scenes",
    )
    scene = matches[0]
    _require(
        (root / scene).resolve().is_file(),
        f"private instance {instance_id!r} scene does not exist",
    )
    return scene


def _forbidden_threshold_protocol(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_forbidden_threshold_protocol(item) for item in value.values())
    if isinstance(value, list):
        return any(_forbidden_threshold_protocol(item) for item in value)
    return isinstance(value, str) and (
        "_mjcf_range_protocol" in value or "0.01 * span" in value
    )


def _boundary_target(value: Any, *, label: str) -> float:
    if isinstance(value, Mapping):
        value = value.get("target")
    return _finite_number(value, label=label)


def _check_calibrated_capability(
    *,
    robot: str,
    capability: Mapping[str, Any],
    cases: list[Mapping[str, Any]],
    record: Mapping[str, Any],
    package: Any,
    criterion_source_id: str,
) -> dict[str, Any]:
    capability_id = str(capability.get("capability_id", ""))
    label = f"fixed threshold calibration {robot}/{capability_id}"
    required_record_fields = {
        "instance_id",
        "scene_entrypoint",
        "method_name",
        "criterion_source_id",
        "metric",
        "unit",
        "joint_name",
        "actuator_name",
        "controller_kind",
        "timestep_s",
        "repetitions",
        "numeric_tolerance_rule",
        "reset_position",
        "joint_lower",
        "joint_upper",
        "boundary_candidates",
        "selected_boundary",
        "cases",
        "numeric_tolerance",
        "sealed_threshold",
    }
    _require(
        set(record) == required_record_fields,
        f"{label} has unexpected or missing fields",
    )
    for field in (
        "instance_id",
        "scene_entrypoint",
        "method_name",
        "criterion_source_id",
        "metric",
        "unit",
        "joint_name",
        "actuator_name",
    ):
        _require(
            isinstance(record.get(field), str) and bool(str(record[field]).strip()),
            f"{label}.{field} must be non-empty text",
        )
    _require(
        record.get("method_name") == capability.get("method_name")
        and record.get("criterion_source_id") == criterion_source_id,
        f"{label} does not match the sealed capability/source identity",
    )
    criteria = capability.get("criteria")
    _require(
        isinstance(criteria, list) and len(criteria) == 1,
        f"{label} must resolve exactly one calibrated criterion",
    )
    criterion = criteria[0]
    _require(
        isinstance(criterion, Mapping)
        and record.get("metric") == criterion.get("metric")
        and record.get("unit") == criterion.get("unit"),
        f"{label} metric/unit differs from the sealed criterion",
    )
    _require(
        record.get("controller_kind") == "trusted_constant_position_control"
        and record.get("repetitions") == 3
        and record.get("numeric_tolerance_rule") == NUMERIC_TOLERANCE_RULE,
        f"{label} does not retain the required trusted three-repeat controller rule",
    )
    timestep = _finite_number(record.get("timestep_s"), label=f"{label}.timestep_s")
    _require(timestep > 0.0, f"{label}.timestep_s must be positive")

    reset = _finite_number(record.get("reset_position"), label=f"{label}.reset_position")
    lower = _finite_number(record.get("joint_lower"), label=f"{label}.joint_lower")
    upper = _finite_number(record.get("joint_upper"), label=f"{label}.joint_upper")
    _require(lower < upper and lower <= reset <= upper, f"{label} has invalid joint/reset bounds")
    candidates = record.get("boundary_candidates")
    _require(
        isinstance(candidates, list) and len(candidates) == 2,
        f"{label}.boundary_candidates must contain the two closed joint bounds",
    )
    expected_candidate_fields = {
        "target",
        "control",
        "terminal",
        "error",
        "requested_displacement",
        "actual_displacement",
        "executable",
    }
    for index, item in enumerate(candidates):
        _require(
            isinstance(item, Mapping) and set(item) == expected_candidate_fields,
            f"{label}.boundary_candidates[{index}] has the wrong fields",
        )
        target = _finite_number(item.get("target"), label=f"{label}.boundary_candidates[{index}].target")
        _finite_number(item.get("control"), label=f"{label}.boundary_candidates[{index}].control")
        terminal = _finite_number(item.get("terminal"), label=f"{label}.boundary_candidates[{index}].terminal")
        error = _finite_number(item.get("error"), label=f"{label}.boundary_candidates[{index}].error")
        requested_displacement = _finite_number(
            item.get("requested_displacement"),
            label=f"{label}.boundary_candidates[{index}].requested_displacement",
        )
        actual_displacement = _finite_number(
            item.get("actual_displacement"),
            label=f"{label}.boundary_candidates[{index}].actual_displacement",
        )
        executable = (
            requested_displacement > 0.0
            and actual_displacement > 0.0
            and error < 0.5 * requested_displacement
        )
        _require(
            error == abs(terminal - target)
            and requested_displacement == abs(target - reset)
            and actual_displacement == abs(terminal - reset)
            and item.get("executable") is executable,
            f"{label}.boundary_candidates[{index}] is inconsistent with its real terminal state",
        )
    candidate_targets = [
        _boundary_target(item, label=f"{label}.boundary_candidates[{index}]")
        for index, item in enumerate(candidates)
    ]
    _require(
        set(candidate_targets) == {lower, upper},
        f"{label}.boundary_candidates must be the exact lower/upper joint bounds",
    )
    selected_raw = record.get("selected_boundary")
    selected = _boundary_target(selected_raw, label=f"{label}.selected_boundary")
    _require(
        selected in {lower, upper},
        f"{label}.selected_boundary must be one exact closed joint bound",
    )
    _require(
        any(
            item.get("target") == selected and item.get("executable") is True
            for item in candidates
        ),
        f"{label}.selected_boundary must retain an executable real-control result",
    )

    by_role: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        role = case.get("case_role")
        _require(
            role in {"nominal", "calibrated_boundary"} and role not in by_role,
            f"{label} suite roles are invalid",
        )
        by_role[str(role)] = case
    _require(
        set(by_role) == {"nominal", "calibrated_boundary"},
        f"{label} must have one nominal and one calibrated-boundary case",
    )
    report_cases = record.get("cases")
    _require(
        isinstance(report_cases, Mapping)
        and set(report_cases) == {"nominal", "calibrated_boundary"},
        f"{label}.cases must contain the two exact roles",
    )
    instance_id = str(record["instance_id"])
    scene_entrypoint = str(record["scene_entrypoint"])
    _require(
        _private_instance_scene(package, instance_id=instance_id) == scene_entrypoint,
        f"{label} scene does not match its private instance",
    )
    expected_case_fields = {
        "case_id",
        "target",
        "max_duration_s",
        "terminal_errors",
        "max_terminal_error",
        "trusted_actuator_executed",
        "physics_stepped",
    }
    targets: dict[str, float] = {}
    for role in ("nominal", "calibrated_boundary"):
        suite_case = by_role[role]
        report_case = report_cases[role]
        _require(
            isinstance(report_case, Mapping) and set(report_case) == expected_case_fields,
            f"{label}.{role} calibration case has the wrong fields",
        )
        _require(
            suite_case.get("instance_id") == instance_id
            and report_case.get("case_id") == suite_case.get("case_id"),
            f"{label}.{role} case/instance identity differs from the fixed suite",
        )
        binding = suite_case.get("measurement_binding")
        parameters = binding.get("parameters") if isinstance(binding, Mapping) else None
        _require(
            isinstance(binding, Mapping)
            and binding.get("metric") == record.get("metric")
            and binding.get("unit") == record.get("unit")
            and binding.get("kind") == "final_joint_position_error"
            and isinstance(parameters, Mapping)
            and parameters.get("joint_name") == record.get("joint_name")
            and parameters.get("target_argument") == "request.target_position",
            f"{label}.{role} does not use the matching trusted joint measurement",
        )
        request = suite_case.get("request")
        _require(isinstance(request, Mapping), f"{label}.{role} request is invalid")
        target = _finite_number(
            request.get("target_position"), label=f"{label}.{role}.request.target_position"
        )
        duration = _finite_number(
            request.get("max_duration_s"), label=f"{label}.{role}.request.max_duration_s"
        )
        _require(duration > 0.0, f"{label}.{role} duration must be positive")
        report_target = _finite_number(report_case.get("target"), label=f"{label}.{role}.target")
        report_duration = _finite_number(
            report_case.get("max_duration_s"), label=f"{label}.{role}.max_duration_s"
        )
        _require(
            target == report_target and duration == report_duration,
            f"{label}.{role} real calibration target/duration differs from the fixed suite",
        )
        terminal_errors = report_case.get("terminal_errors")
        _require(
            isinstance(terminal_errors, list) and len(terminal_errors) == 3,
            f"{label}.{role} must retain three terminal errors",
        )
        checked_errors = [
            _finite_number(error, label=f"{label}.{role}.terminal_errors")
            for error in terminal_errors
        ]
        _require(
            all(error >= 0.0 for error in checked_errors)
            and report_case.get("trusted_actuator_executed") is True
            and report_case.get("physics_stepped") is True,
            f"{label}.{role} lacks real trusted actuator/physics evidence",
        )
        max_error = _finite_number(
            report_case.get("max_terminal_error"),
            label=f"{label}.{role}.max_terminal_error",
        )
        _require(
            max_error == max(checked_errors),
            f"{label}.{role}.max_terminal_error does not match its repeats",
        )
        targets[role] = target

    nominal = targets["nominal"]
    boundary = targets["calibrated_boundary"]
    _require(nominal != boundary, f"{label} nominal and boundary targets must differ")
    _require(boundary == selected, f"{label} suite boundary is not the selected real joint bound")
    _require(
        nominal == reset + 0.5 * (boundary - reset),
        f"{label} nominal target must be the exact reset-to-boundary midpoint",
    )
    target_schema = capability.get("request_schema", {}).get("properties", {}).get(
        "target_position"
    )
    _require(isinstance(target_schema, Mapping), f"{label} lacks a closed target_position schema")
    schema_lower = _finite_number(target_schema.get("minimum"), label=f"{label}.schema.minimum")
    schema_upper = _finite_number(target_schema.get("maximum"), label=f"{label}.schema.maximum")
    _require(
        schema_lower <= nominal <= schema_upper
        and boundary in {schema_lower, schema_upper},
        f"{label} boundary must be one exact closed-schema edge",
    )

    span = upper - lower
    numeric_tolerance = _finite_number(
        record.get("numeric_tolerance"), label=f"{label}.numeric_tolerance"
    )
    expected_tolerance = (
        64.0
        * sys.float_info.epsilon
        * max(1.0, abs(lower), abs(upper), span)
    )
    _require(
        numeric_tolerance == expected_tolerance,
        f"{label}.numeric_tolerance does not match the declared IEEE-754 rule",
    )
    sealed_threshold = _finite_number(
        record.get("sealed_threshold"), label=f"{label}.sealed_threshold"
    )
    expected_threshold = max(
        float(report_cases["nominal"]["max_terminal_error"]),
        float(report_cases["calibrated_boundary"]["max_terminal_error"]),
    ) + numeric_tolerance
    _require(
        sealed_threshold == expected_threshold
        and criterion.get("threshold") == sealed_threshold,
        f"{label} threshold does not equal real max error plus numeric tolerance",
    )
    _require(
        sealed_threshold < abs(nominal - reset),
        f"{label} threshold would permit a no-op to pass the nominal case",
    )
    return copy.deepcopy(dict(record))


def _check_fixed_input_calibration_report(
    path: str | Path,
    *,
    input_set_id: str,
    robot_artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit every non-public threshold against retained real MuJoCo evidence."""

    source = Path(path).resolve()
    report = _read_fixed_index(source)
    _require(
        set(report)
        == {"artifact_type", "schema_version", "input_set_id", "passed", "robots"},
        "fixed-input calibration report has unexpected or missing top-level fields",
    )
    _require(
        report.get("artifact_type") == "experiment3_fixed_threshold_calibration"
        and report.get("schema_version") == "1.0"
        and report.get("input_set_id") == input_set_id
        and report.get("passed") is True,
        "fixed-input calibration identity, input_set_id, or overall verdict is invalid",
    )
    robots = report.get("robots")
    _require(
        isinstance(robots, Mapping)
        and tuple(robots) == ROBOT_CONFIGURATIONS
        and set(robots) == set(ROBOT_CONFIGURATIONS),
        "fixed-input calibration must contain the exact eleven robots in Authority order",
    )
    checked_robots: dict[str, Any] = {}
    for robot in ROBOT_CONFIGURATIONS:
        artifacts = robot_artifacts.get(robot)
        _require(isinstance(artifacts, Mapping), f"fixed artifacts are missing for {robot!r}")
        design = artifacts.get("design")
        suite = artifacts.get("suite")
        package = artifacts.get("package")
        _require(
            isinstance(design, Mapping) and isinstance(suite, Mapping),
            f"fixed artifacts are invalid for {robot!r}",
        )
        _require(
            not _forbidden_threshold_protocol(design),
            f"fixed design for {robot!r} retains an artificial MJCF-span threshold protocol",
        )
        robot_report = robots[robot]
        _require(
            isinstance(robot_report, Mapping)
            and set(robot_report) == {"instance_id", "scene_entrypoint", "capabilities"},
            f"fixed-input calibration robot record for {robot!r} has the wrong fields",
        )
        for field in ("instance_id", "scene_entrypoint"):
            _require(
                isinstance(robot_report.get(field), str)
                and bool(str(robot_report[field]).strip()),
                f"fixed-input calibration {robot!r}.{field} must be non-empty text",
            )
        records = robot_report.get("capabilities")
        _require(isinstance(records, Mapping), f"fixed-input calibration capabilities for {robot!r} must be an object")
        capabilities = design.get("capabilities")
        suite_cases = suite.get("cases")
        _require(isinstance(capabilities, list) and isinstance(suite_cases, list), f"fixed pair is incomplete for {robot!r}")
        required_records: dict[str, tuple[Mapping[str, Any], str]] = {}
        for capability in capabilities:
            _require(isinstance(capability, Mapping), f"fixed capability is invalid for {robot!r}")
            capability_id = str(capability.get("capability_id", ""))
            criteria = capability.get("criteria")
            _require(isinstance(criteria, list) and criteria, f"fixed capability {robot!r}/{capability_id} lacks criteria")
            source_ids: list[str] = []
            for criterion in criteria:
                refs = criterion.get("source_refs") if isinstance(criterion, Mapping) else None
                _require(isinstance(refs, list) and refs, f"fixed criterion {robot!r}/{capability_id} lacks source refs")
                for ref in refs:
                    source_id = ref.get("source_id") if isinstance(ref, Mapping) else None
                    _require(isinstance(source_id, str) and bool(source_id.strip()), f"fixed criterion {robot!r}/{capability_id} has an invalid source ID")
                    source_ids.append(source_id)
            non_public = [
                source_id
                for source_id in source_ids
                if source_id not in PUBLIC_OR_TRACKED_CRITERION_SOURCE_IDS
            ]
            if non_public:
                _require(
                    len(source_ids) == 1 and len(non_public) == 1,
                    f"fixed criterion {robot!r}/{capability_id} mixes unresolved threshold sources",
                )
                required_records[capability_id] = (capability, non_public[0])
        _require(
            set(records) == set(required_records),
            f"fixed-input calibration records for {robot!r} do not exactly cover non-public thresholds",
        )
        checked_capabilities: dict[str, Any] = {}
        for capability_id, (capability, source_id) in required_records.items():
            cases = [
                case
                for case in suite_cases
                if isinstance(case, Mapping)
                and case.get("capability_id") == capability_id
            ]
            _require(len(cases) == 2, f"fixed suite lacks two cases for {robot!r}/{capability_id}")
            checked_capabilities[capability_id] = _check_calibrated_capability(
                robot=robot,
                capability=capability,
                cases=cases,
                record=records[capability_id],
                package=package,
                criterion_source_id=source_id,
            )
        checked_robots[robot] = {
            "instance_id": str(robot_report["instance_id"]),
            "scene_entrypoint": str(robot_report["scene_entrypoint"]),
            "capabilities": checked_capabilities,
        }
    return {
        "path": str(source),
        "artifact_type": report["artifact_type"],
        "input_set_id": input_set_id,
        "passed": True,
        "robots": checked_robots,
        "calibrated_capability_count": sum(
            len(item["capabilities"]) for item in checked_robots.values()
        ),
    }


def _check_directional_endpoint_contract(
    *, robot: str, design: Mapping[str, Any], suite: Mapping[str, Any]
) -> None:
    """Keep a directional capability on the same observed endpoint as reach."""

    capabilities = design.get("capabilities")
    cases = suite.get("cases")
    _require(isinstance(capabilities, list) and isinstance(cases, list), f"fixed pair is incomplete for {robot!r}")
    directional = [
        item
        for item in capabilities
        if isinstance(item, Mapping)
        and item.get("method_name") == "move_observed_tool_along_direction"
    ]
    if not directional:
        return
    reach = [
        item
        for item in capabilities
        if isinstance(item, Mapping)
        and item.get("method_name") == "move_observed_tool_to_position"
    ]
    _require(
        len(directional) == 1 and len(reach) == 1,
        f"fixed directional endpoint contract is ambiguous for {robot!r}",
    )
    directional_capability = directional[0]
    preconditions = directional_capability.get("preconditions")
    invariants = directional_capability.get("invariants")
    _require(
        isinstance(preconditions, list)
        and any(
            isinstance(statement, str)
            and (
                "unit" in statement.casefold()
                or "norm 1" in statement.casefold()
            )
            and "1e-6" in statement.casefold()
            for statement in preconditions
        ),
        f"fixed directional capability for {robot!r} must state a 1e-6 unit-vector precondition",
    )
    _require(
        isinstance(invariants, list)
        and any(
            isinstance(statement, str)
            and "unit" in statement.casefold()
            and (
                "1e-6" in statement.casefold()
                or "precondition" in statement.casefold()
            )
            for statement in invariants
        ),
        f"fixed directional capability for {robot!r} must preserve the 1e-6 unit-vector precondition",
    )
    reach_id = reach[0].get("capability_id")
    directional_id = directional_capability.get("capability_id")
    reach_cases = [
        item
        for item in cases
        if isinstance(item, Mapping) and item.get("capability_id") == reach_id
    ]
    directional_cases = [
        item
        for item in cases
        if isinstance(item, Mapping) and item.get("capability_id") == directional_id
    ]
    _require(
        len(reach_cases) == 2 and len(directional_cases) == 2,
        f"fixed directional endpoint cases are incomplete for {robot!r}",
    )
    reach_binding = reach_cases[0].get("measurement_binding")
    _require(isinstance(reach_binding, Mapping), f"fixed reach binding is missing for {robot!r}")
    reach_kind = str(reach_binding.get("kind", ""))
    reach_parameters = reach_binding.get("parameters")
    _require(isinstance(reach_parameters, Mapping), f"fixed reach parameters are missing for {robot!r}")
    if reach_kind.startswith("final_site_"):
        expected_kind = "final_site_directional_displacement_error"
        entity_field = "site_name"
    elif reach_kind == "final_body_xyz_position_error":
        expected_kind = "final_body_directional_displacement_error"
        entity_field = "body_name"
    else:
        raise Experiment3RunnerError(
            f"fixed reach endpoint operator for {robot!r} is not a trusted body/site position operator"
        )
    endpoint = reach_parameters.get(entity_field)
    _require(
        isinstance(endpoint, str) and bool(endpoint.strip()),
        f"fixed reach endpoint entity is missing for {robot!r}",
    )
    for case in directional_cases:
        binding = case.get("measurement_binding")
        parameters = binding.get("parameters") if isinstance(binding, Mapping) else None
        _require(
            isinstance(binding, Mapping)
            and binding.get("kind") == expected_kind
            and isinstance(parameters, Mapping)
            and parameters.get(entity_field) == endpoint,
            f"fixed directional capability for {robot!r} does not measure its observed {entity_field[:-5]} endpoint",
        )
        _require(
            parameters.get("direction_x_argument") == "request.direction.x"
            and parameters.get("direction_y_argument") == "request.direction.y"
            and parameters.get("direction_z_argument") == "request.direction.z"
            and parameters.get("target_distance_argument") == "request.distance_m",
            f"fixed directional binding paths are invalid for {robot!r}",
        )
        request = case.get("request")
        direction = request.get("direction") if isinstance(request, Mapping) else None
        _require(isinstance(direction, Mapping), f"fixed directional request is invalid for {robot!r}")
        components = [
            _finite_number(direction.get(axis), label=f"fixed direction {robot!r}.{axis}")
            for axis in ("x", "y", "z")
        ]
        norm = math.sqrt(sum(component * component for component in components))
        _require(
            abs(norm - 1.0) <= 1.0e-6,
            f"fixed directional request for {robot!r} is not unit norm within 1e-6",
        )


def _fixed_artifact_path(
    *, index_root: Path, robot: str, label: str, value: Any
) -> Path:
    _require(
        isinstance(value, str) and bool(value.strip()),
        f"fixed-input index {label} for {robot!r} must be non-empty text",
    )
    relative = Path(str(value))
    _require(
        not relative.is_absolute(),
        f"fixed-input index {label} for {robot!r} must be relative",
    )
    path = (index_root / relative).resolve()
    try:
        path.relative_to(index_root)
    except ValueError as exc:
        raise Experiment3RunnerError(
            f"fixed-input index {label} for {robot!r} escapes its root"
        ) from exc
    return path


def _check_fixed_input_set(
    mainline_root: str | Path,
    *,
    index_path: str | Path,
    smoke_report_path: str | Path,
    calibration_report_path: str | Path,
    package_loader: Callable[[str | Path, str], Any] | None = None,
    design_validator: Callable[..., Mapping[str, Any]] | None = None,
    suite_validator: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load and audit all eleven fixed design/suite pairs without a model."""

    if package_loader is None:
        from autoadapter2.libraries import load_indexed_robot_package

        package_loader = load_indexed_robot_package
    if design_validator is None:
        from autoadapter2.capability_design import validate_capability_design

        design_validator = validate_capability_design
    if suite_validator is None:
        from autoadapter2.validation_compiler import (
            validate_capability_validation_suite,
        )

        suite_validator = validate_capability_validation_suite

    path = Path(index_path).resolve()
    index = _read_fixed_index(path)
    _require(
        set(index)
        == {
            "artifact_type",
            "schema_version",
            "capability_protocol_version",
            "input_set_id",
            "calibration_report",
            "smoke_report",
            "robots",
        },
        "fixed-input index has unexpected or missing top-level fields",
    )
    _require(
        index.get("schema_version") == "1.0",
        "fixed-input index schema_version must be '1.0'",
    )
    _require(
        index.get("artifact_type") == "experiment3_fixed_capability_input_set"
        and index.get("capability_protocol_version") == "capability-v2"
        and isinstance(index.get("input_set_id"), str)
        and bool(str(index["input_set_id"]).strip()),
        "fixed-input index identity or capability protocol is invalid",
    )
    root = path.parent.resolve()
    declared_smoke_path = _fixed_artifact_path(
        index_root=root,
        robot="input-set",
        label="smoke_report",
        value=index.get("smoke_report"),
    )
    declared_calibration_path = _fixed_artifact_path(
        index_root=root,
        robot="input-set",
        label="calibration_report",
        value=index.get("calibration_report"),
    )
    _require(
        declared_smoke_path == root / "smoke_report.json"
        and declared_smoke_path == Path(smoke_report_path).resolve(),
        "fixed-input index smoke_report must match the canonical manifest path",
    )
    _require(
        declared_calibration_path == root / "calibration_report.json"
        and declared_calibration_path == Path(calibration_report_path).resolve(),
        "fixed-input index calibration_report must match the canonical manifest path",
    )
    entries = index.get("robots")
    _require(
        isinstance(entries, Mapping)
        and tuple(entries) == ROBOT_CONFIGURATIONS
        and set(entries) == set(ROBOT_CONFIGURATIONS),
        "fixed-input index must contain the exact eleven robots in Authority order",
    )
    summaries: dict[str, Any] = {}
    robot_artifacts: dict[str, Any] = {}
    total_capabilities = 0
    total_cases = 0
    for robot in ROBOT_CONFIGURATIONS:
        try:
            entry = entries[robot]
            _require(
                isinstance(entry, Mapping),
                f"fixed-input index entry for {robot!r} must be an object",
            )
            expected_fields = {
                "package_version",
                "task_snapshot_id",
                "capability_design",
                "capability_validation_suite",
                "capability_count",
                "case_count",
                "source_kind",
            }
            _require(
                set(entry) == expected_fields,
                f"fixed-input index entry for {robot!r} must contain exactly {sorted(expected_fields)}",
            )
            design_path = _fixed_artifact_path(
                index_root=root,
                robot=robot,
                label="capability_design",
                value=entry.get("capability_design"),
            )
            suite_path = _fixed_artifact_path(
                index_root=root,
                robot=robot,
                label="capability_validation_suite",
                value=entry.get("capability_validation_suite"),
            )
            # The shared pipeline receives the root and loads this canonical
            # layout.  Require the index to name exactly the same files audited
            # here so its path declarations cannot become decorative.
            _require(
                design_path == root / robot / "capability_design.json"
                and suite_path
                == root / robot / "capability_validation_suite.json",
                f"fixed-input paths for {robot!r} must use the canonical per-robot layout",
            )
            package = package_loader(Path(mainline_root).resolve(), robot)
            package_robot = getattr(package, "robot_configuration_id", None)
            package_version = getattr(package, "package_version", None)
            task_snapshot_id = getattr(package, "snapshot_id", None)
            _require(package_robot == robot, f"indexed package identity differs for {robot!r}")
            _require(
                package_version == entry.get("package_version")
                and task_snapshot_id == entry.get("task_snapshot_id"),
                f"fixed-input package identity differs for {robot!r}",
            )
            _require(
                isinstance(entry.get("source_kind"), str)
                and bool(str(entry["source_kind"]).strip()),
                f"fixed-input source_kind for {robot!r} must be non-empty",
            )
            design = design_validator(
                _read_fixed_index(design_path),
                package,
            )
            suite = suite_validator(
                _read_fixed_index(suite_path),
                package=package,
                design=design,
            )
            _check_directional_endpoint_contract(
                robot=robot,
                design=design,
                suite=suite,
            )
            capabilities = design.get("capabilities")
            cases = suite.get("cases")
            _require(
                isinstance(capabilities, list) and len(capabilities) >= 5,
                f"fixed design for {robot!r} must contain at least five capabilities",
            )
            _require(
                isinstance(cases, list) and len(cases) == 2 * len(capabilities),
                f"fixed suite for {robot!r} is not exactly two cases per capability",
            )
            _require(
                entry.get("capability_count") == len(capabilities)
                and entry.get("case_count") == len(cases),
                f"fixed-input index counts differ from artifacts for {robot!r}",
            )
        except Exception as exc:
            detail = str(exc) if isinstance(exc, Experiment3RunnerError) else f"{type(exc).__name__}: {exc}"
            raise Experiment3RunnerError(
                f"fixed input for {robot!r} failed before model calls: {detail}"
            ) from exc
        summaries[robot] = {
            "robot_configuration_id": robot,
            "package_version": package_version,
            "directory": str(design_path.parent),
            "capability_design_path": str(design_path),
            "capability_validation_suite_path": str(suite_path),
            "capability_count": len(capabilities),
            "validation_case_count": len(cases),
            "source_kind": str(entry["source_kind"]),
            "validated_before_model_calls": True,
        }
        total_capabilities += len(capabilities)
        total_cases += len(cases)
        robot_artifacts[robot] = {
            "package": package,
            "design": design,
            "suite": suite,
        }
    smoke = _check_fixed_input_smoke_report(
        smoke_report_path,
        input_set_id=str(index["input_set_id"]),
    )
    calibration = _check_fixed_input_calibration_report(
        calibration_report_path,
        input_set_id=str(index["input_set_id"]),
        robot_artifacts=robot_artifacts,
    )
    return {
        "passed": True,
        "index_path": str(path),
        "input_set_id": str(index["input_set_id"]),
        "root_directory": str(root),
        "robots": summaries,
        "capability_count": total_capabilities,
        "validation_case_count": total_cases,
        "smoke_report": smoke,
        "calibration_report": calibration,
    }


def _assert_client_pin(client: Any, *, model: Mapping[str, Any], transport: Mapping[str, Any]) -> None:
    config = getattr(client, "config", None)
    _require(config is not None, "client_factory must return a client with a pinned config")
    expected = {
        "provider": model["vendor"],
        "model": model["model_id"],
        "base_url": str(model["base_url"]).rstrip("/"),
        "endpoint_path": transport["endpoint_path"],
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
    _require(bool(api_key), f"holisticai profile credential environment variable {credential_env!r} is missing")
    _require(model["temperature"] == 0.0, "built-in JsonModelClient supports temperature 0 only")
    return JsonModelClient(
        ModelConfig(
            provider=str(model["vendor"]),
            model=str(model["model_id"]),
            base_url=str(model["base_url"]),
            api_key=api_key,
            endpoint_path=str(transport["endpoint_path"]),
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
    frozen_attempt_count = cell.get("frozen_driver_attempt_count")
    _require(
        isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and 0 <= attempt_count <= 3,
        "cell recorded more than three attempts or omitted its attempt count",
    )
    _require(
        isinstance(frozen_attempt_count, int)
        and not isinstance(frozen_attempt_count, bool)
        and 0 <= frozen_attempt_count <= 3,
        "cell froze more than three drivers or omitted its frozen-attempt count",
    )
    _require(
        attempt_count == frozen_attempt_count,
        "only frozen Driver revisions may consume the formal attempt count",
    )
    return cell


def _passed_capability_whitelist(cell: Mapping[str, Any]) -> tuple[str, ...]:
    value = cell.get("passed_capability_whitelist")
    _require(
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in value),
        "cell omitted its nominal-and-boundary-passed capability whitelist",
    )
    assert isinstance(value, list)
    normalised = tuple(item.strip() for item in value)
    _require(
        len(normalised) == len(set(normalised)),
        "passed capability whitelist contains duplicates",
    )
    return normalised


def _task_demo_terminal(cell: Mapping[str, Any]) -> dict[str, Any]:
    passed_capabilities = _passed_capability_whitelist(cell)
    executed = cell.get("task_demo_executed") is True
    if passed_capabilities:
        _require(
            executed,
            "a final Driver with a nominal-and-boundary-passed capability requires an executed Task Demo",
        )
        return {
            "status": "executed",
            "passed": cell.get("task_demo_passed") is True,
            "capability_whitelist": list(passed_capabilities),
        }
    _require(
        not executed,
        "Task Demo executed without a nominal-and-boundary-passed capability",
    )
    task_demo = cell.get("task_demo")
    if (
        isinstance(task_demo, Mapping)
        and task_demo.get("skipped") is True
        and isinstance(task_demo.get("skip_reason"), str)
        and bool(str(task_demo["skip_reason"]).strip())
    ):
        return {"status": "not-run", "reason": str(task_demo["skip_reason"]).strip()}
    raise Experiment3RunnerError(
        "a cell without a nominal-and-boundary-passed capability requires a truthful Task Demo not-run reason"
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
    study_evidence = outcomes.get("STUDY")
    _require(
        isinstance(study_evidence, Mapping)
        and study_evidence.get("attempted") is True
        and isinstance(study_evidence.get("model_call_count"), int),
        "singleton result lacks truthful cell-local STUDY evidence",
    )
    if study_evidence.get("completed") is not True:
        _require(
            study_evidence.get("completed") is False
            and isinstance(study_evidence.get("error"), Mapping)
            and cell.get("frozen_driver_attempt_count") == 0
            and cell.get("capability_validation_executed") is False,
            "an incomplete STUDY must be a truthful terminal with no frozen Driver or validation",
        )
    for stage in ("TGCD", "IVC"):
        evidence = outcomes.get(stage)
        _require(
            isinstance(evidence, Mapping)
            and evidence.get("attempted") is False
            and evidence.get("completed") is False
            and evidence.get("skipped") is True
            and evidence.get("model_call_count") == 0
            and isinstance(evidence.get("fixed_input_provenance"), Mapping),
            f"singleton result lacks zero-call fixed-input {stage} skip evidence",
        )
    _require(
        cell.get("upstream_artifact_mode") == "fixed-per-robot"
        and isinstance(cell.get("fixed_input_provenance"), Mapping),
        "singleton result lacks fixed per-robot input provenance",
    )
    if cell.get("capability_validation_executed") is True:
        _require(
            cell.get("video_required") is True and cell.get("video_complete") is True,
            "executed capability validation lacks complete required video evidence",
        )
    passed_capabilities = _passed_capability_whitelist(cell)
    if passed_capabilities:
        task_counts = cell.get("task_demo_task_counts")
        task_total = task_counts.get("total") if isinstance(task_counts, Mapping) else None
        _require(
            isinstance(task_total, int)
            and not isinstance(task_total, bool)
            and 1 <= task_total <= 5,
            "a passed capability whitelist requires one to five Task Demo tasks",
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
        "formal": True,
        "max_driver_attempts_per_condition": 3,
        "phase_turn_budgets": copy.deepcopy(resources["phase_turn_budgets"]),
        "execute_python": copy.deepcopy(resources["execute_python"]),
        "recap": copy.deepcopy(resources["recap"]),
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
    package_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    fixed_input_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    check_self_containment: bool = True,
) -> dict[str, Any]:
    """Audit all eleven fixed pairs and retained pins without a model call."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    if package_check_fn is None:
        from autoadapter2.pipeline import check_packages as package_check_fn
    preflight = validate_executable_preflight(source, mainline_root=mainline_root)
    fixed_input_check = copy.deepcopy(
        dict(
            (fixed_input_check_fn or _check_fixed_input_set)(
                mainline_root,
                index_path=_fixed_input_index_path(
                    source, mainline_root=mainline_root
                ),
                smoke_report_path=_fixed_input_smoke_path(
                    source, mainline_root=mainline_root
                ),
                calibration_report_path=_fixed_input_calibration_path(
                    source, mainline_root=mainline_root
                ),
            )
        )
    )
    _require(
        fixed_input_check.get("passed") is True
        and isinstance(fixed_input_check.get("robots"), Mapping)
        and tuple(fixed_input_check["robots"]) == ROBOT_CONFIGURATIONS
        and isinstance(fixed_input_check.get("smoke_report"), Mapping)
        and fixed_input_check["smoke_report"].get("passed") is True
        and fixed_input_check["smoke_report"].get("input_set_id")
        == fixed_input_check.get("input_set_id")
        and isinstance(fixed_input_check.get("calibration_report"), Mapping)
        and fixed_input_check["calibration_report"].get("passed") is True
        and fixed_input_check["calibration_report"].get("input_set_id")
        == fixed_input_check.get("input_set_id")
        and isinstance(fixed_input_check.get("capability_count"), int)
        and fixed_input_check["capability_count"] >= 5 * len(ROBOT_CONFIGURATIONS)
        and fixed_input_check.get("validation_case_count")
        == 2 * fixed_input_check["capability_count"],
        "fixed-input validation did not pass for the exact eleven configurations",
    )
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
    return {
        **preflight,
        "fixed_input_check": fixed_input_check,
        "current_package_check": package_check,
    }


def _update_record_counts(record: dict[str, Any]) -> None:
    rows = record["cells"]
    record["completed_cells"] = sum(row["status"] == "completed" for row in rows)
    record["failed_cells"] = sum(row["status"] == "failed" for row in rows)
    record["predeclared_cells"] = sum(
        row["status"] == "predeclared" for row in rows
    )
    record["all_declared_cells_retained"] = len(rows) == 33


def _systemic_failure_reason(
    exc: BaseException,
    *,
    failure_stage: str,
    client: Any | None,
) -> str | None:
    """Recognise only explicit authentication/provider/infrastructure failures."""

    if isinstance(exc, Experiment3SystemicError):
        return str(exc).strip() or type(exc).__name__
    if failure_stage in {"cell-preflight", "client-construction", "client-preflight"}:
        return str(exc).strip() or type(exc).__name__
    calls = getattr(client, "calls", None)
    if isinstance(calls, list) and calls:
        latest = calls[-1]
        if isinstance(latest, Mapping) and latest.get("status") in {
            "http_error",
            "timeout",
            "transport_error",
        }:
            return (
                f"provider call ended as {latest.get('status')}"
                + (
                    f" (HTTP {latest.get('http_status')})"
                    if latest.get("http_status") is not None
                    else ""
                )
            )
    if isinstance(exc, (OSError, ImportError, subprocess.SubprocessError)):
        return str(exc).strip() or type(exc).__name__
    message = str(exc).casefold()
    explicit_markers = (
        "authentication",
        "unauthorised",
        "unauthorized",
        "credential",
        "provider transport",
        "transport error",
        "connection refused",
        "connection reset",
        "infrastructure failure",
        "http 401",
        "http 403",
        "api retry budget exhausted",
    )
    if any(marker in message for marker in explicit_markers):
        return str(exc).strip() or type(exc).__name__
    return None


def _raise_if_result_is_systemic(
    result: Mapping[str, Any], cell: Mapping[str, Any]
) -> None:
    for label, value in (
        ("result", result),
        ("cell", cell),
        ("capability validation", cell.get("capability_validation")),
        ("Task Demo", cell.get("task_demo")),
    ):
        if isinstance(value, Mapping) and value.get("infrastructure_failure") is True:
            raise Experiment3SystemicError(
                f"{label} reported infrastructure_failure=true"
            )


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
    record["dispatch_stopped"] = False
    record.pop("systemic_stop", None)
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
        systemic_reason: str | None = None
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
                "fixed_inputs_from": preflight["fixed_input_check"][
                    "root_directory"
                ],
            }
            if hooks_factory is not None:
                kwargs["hooks"] = hooks_factory(copy.deepcopy(cell))
            failure_stage = "pipeline"
            result = copy.deepcopy(dict(run_experiment_fn(mainline_root, **kwargs)))
            row["result"] = result
            failure_stage = "result-postcheck"
            reported_cell = _assert_attempt_ceiling(result)
            _raise_if_result_is_systemic(result, reported_cell)
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
            systemic_reason = _systemic_failure_reason(
                exc, failure_stage=failure_stage, client=client
            )
            row["status"] = "failed"
            reason = str(exc).strip() or type(exc).__name__
            row["failure_stage"] = failure_stage
            row["failure"] = {
                "stage": failure_stage,
                "type": type(exc).__name__,
                "message": reason,
                "systemic": systemic_reason is not None,
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
        if systemic_reason is not None:
            record["dispatch_stopped"] = True
            record["systemic_stop"] = {
                "cell_id": cell["cell_id"],
                "stage": failure_stage,
                "reason": systemic_reason,
            }
            _write_json(destination / "experiment3_run_record.json", record)
            break

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
    run_experiment_fn: Callable[..., Mapping[str, Any]] | None = None,
    package_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    fixed_input_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    hooks_factory: Callable[[Mapping[str, str]], Any] | None = None,
    check_self_containment: bool = True,
    git_commit_fn: Callable[[str | Path], str] | None = None,
) -> dict[str, Any]:
    """Execute each predeclared cell once, retaining failures in denominator 33."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    _require(
        source.get("formal_dispatch_authorised") is True,
        "formal Experiment 3 dispatch is not authorised by the current manifest",
    )
    if run_experiment_fn is None or package_check_fn is None:
        from autoadapter2.pipeline import check_packages, run_experiment

        run_experiment_fn = run_experiment_fn or run_experiment
        package_check_fn = package_check_fn or check_packages
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
        fixed_input_check_fn=fixed_input_check_fn,
        check_self_containment=check_self_containment,
    )
    record["dispatch_started"] = True
    record["holisticai_route_profile"] = copy.deepcopy(
        preflight["holisticai_route_profile"]
    )
    record["readiness_evidence"] = copy.deepcopy(preflight["readiness_evidence"])
    record["fixed_input_check"] = preflight["fixed_input_check"]
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
    run_experiment_fn: Callable[..., Mapping[str, Any]] | None = None,
    package_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    fixed_input_check_fn: Callable[..., Mapping[str, Any]] | None = None,
    hooks_factory: Callable[[Mapping[str, str]], Any] | None = None,
    check_self_containment: bool = True,
    git_commit_fn: Callable[[str | Path], str] | None = None,
) -> dict[str, Any]:
    """Continue untouched cells in the same fixed formal run without retries."""

    source = load_manifest(manifest_path) if manifest is None else copy.deepcopy(dict(manifest))
    _require(
        source.get("formal_dispatch_authorised") is True,
        "formal Experiment 3 resume is not authorised by the current manifest",
    )
    if run_experiment_fn is None or package_check_fn is None:
        from autoadapter2.pipeline import check_packages, run_experiment

        run_experiment_fn = run_experiment_fn or run_experiment
        package_check_fn = package_check_fn or check_packages
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
        fixed_input_check_fn=fixed_input_check_fn,
        check_self_containment=check_self_containment,
    )
    record["dispatch_started"] = True
    record["holisticai_route_profile"] = copy.deepcopy(
        preflight["holisticai_route_profile"]
    )
    record["readiness_evidence"] = copy.deepcopy(preflight["readiness_evidence"])
    record["fixed_input_check"] = preflight["fixed_input_check"]
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
        "analysis": (
            "descriptive per exact robot configuration; "
            "no morphology-effect, quadruped-transfer, or causal statistic"
        ),
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
                "holisticai_route_profile": checked["holisticai_route_profile"],
                "retained_package_evidence_count": len(
                    checked["readiness_evidence"]["packages"]
                ),
                "fixed_input_count": len(
                    checked["fixed_input_check"]["robots"]
                ),
                "fixed_capability_count": checked["fixed_input_check"][
                    "capability_count"
                ],
                "fixed_validation_case_count": checked["fixed_input_check"][
                    "validation_case_count"
                ],
                "fixed_input_check_passed": checked["fixed_input_check"][
                    "passed"
                ],
                "fixed_input_smoke_passed": checked["fixed_input_check"][
                    "smoke_report"
                ]["passed"],
                "fixed_input_calibration_passed": checked["fixed_input_check"][
                    "calibration_report"
                ]["passed"],
                "fixed_calibrated_capability_count": checked[
                    "fixed_input_check"
                ]["calibration_report"]["calibrated_capability_count"],
                "current_package_check_passed": checked[
                    "current_package_check"
                ]["package_check_passed"],
            }
        elif args.command in {"formal", "resume"}:
            manifest = load_manifest(args.manifest)
            validate_executable_preflight(manifest, mainline_root=args.root)
            _load_env_files(args.env_file)
            action = run_formal if args.command == "formal" else run_resume
            record = action(
                args.root,
                manifest=manifest,
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
