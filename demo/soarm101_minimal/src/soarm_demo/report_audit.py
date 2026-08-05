"""Run-report semantic checks that JSON Schema cannot express.

Schemas prove local shape.  This module proves cross-field budget arithmetic
and binds sealed or terminal video evidence back to files in the run root.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audit import sha256_file, sha256_json
from .run_manifest import DEMO_ORACLE_GATE_FAILURE_REASON
from .schema_validation import validate_json_schema
from .sdk_activation import frozen_sdk_activation_semantic_errors
from .tool_packager import package_tree_sha256
from .task_oracle_contract import (
    DEFAULT_TASK_ORACLE_SCHEMA,
    TaskOracleContractError,
    parse_task_oracle_contract,
)


_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SCENE_CATALOG_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SCENE_CATALOG_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def demo_report_structure_semantic_errors(
    value: Any,
    *,
    freeze: Mapping[str, Any] | None = None,
    freeze_sha256: str | None = None,
) -> tuple[str, ...]:
    """Validate Demo report arithmetic and its optional frozen-task binding.

    These checks intentionally do not require success.  They separate a valid
    six-task report containing an oracle miss (a Demo outcome) from a malformed
    runner/framework artifact (an infrastructure failure).
    """

    demo = _mapping(value)
    tasks = list(_sequence(demo.get("tasks")))
    errors: list[str] = []
    if len(tasks) != 6:
        errors.append("demo.tasks must contain exactly six tasks")

    task_ids: list[str] = []
    ordinals: list[int] = []
    partitions: list[str] = []
    passed_by_partition = {"visible": 0, "pilot-held-out": 0}
    for index, raw_task in enumerate(tasks):
        task = _mapping(raw_task)
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_ids.append(task_id)
        else:
            errors.append(f"demo.tasks[{index}].task_id must be a non-empty string")
        ordinal = task.get("ordinal")
        if isinstance(ordinal, int) and not isinstance(ordinal, bool):
            ordinals.append(ordinal)
        else:
            errors.append(f"demo.tasks[{index}].ordinal must be an integer")
        partition = task.get("partition")
        if partition in passed_by_partition:
            partitions.append(str(partition))
        else:
            errors.append(
                f"demo.tasks[{index}].partition must be visible or pilot-held-out"
            )
        passed = task.get("passed")
        if not isinstance(passed, bool):
            errors.append(f"demo.tasks[{index}].passed must be boolean")
        elif partition in passed_by_partition and passed:
            passed_by_partition[str(partition)] += 1
        oracle_passed = _mapping(task.get("oracle")).get("passed")
        if not isinstance(oracle_passed, bool):
            errors.append(f"demo.tasks[{index}].oracle.passed must be boolean")
        elif isinstance(passed, bool) and passed != oracle_passed:
            errors.append(
                f"demo.tasks[{index}].passed must equal oracle.passed"
            )

    if len(task_ids) != len(set(task_ids)):
        errors.append("demo task IDs must be unique")
    if ordinals != list(range(1, 7)):
        errors.append("demo task ordinals must be exactly 1..6 in order")
    if partitions.count("visible") != 3 or partitions.count("pilot-held-out") != 3:
        errors.append("demo tasks must contain three visible and three pilot-held-out tasks")

    summary = _mapping(demo.get("summary"))
    expected_summary = {
        "visible": (passed_by_partition["visible"], 3),
        "pilot-held-out": (passed_by_partition["pilot-held-out"], 3),
        "overall": (sum(passed_by_partition.values()), 6),
    }
    for name, (expected_passed, expected_total) in expected_summary.items():
        item = _mapping(summary.get(name))
        if item.get("passed") != expected_passed or isinstance(item.get("passed"), bool):
            errors.append(
                f"demo.summary.{name}.passed must equal task-derived {expected_passed}"
            )
        if item.get("total") != expected_total or isinstance(item.get("total"), bool):
            errors.append(f"demo.summary.{name}.total must equal {expected_total}")
        expected_rate = expected_passed / expected_total
        rate = item.get("success_rate")
        if (
            not isinstance(rate, (int, float))
            or isinstance(rate, bool)
            or float(rate) != expected_rate
        ):
            errors.append(
                f"demo.summary.{name}.success_rate must equal task-derived {expected_rate}"
            )

    if freeze is not None:
        frozen_tasks = list(_sequence(freeze.get("tasks")))
        frozen_ids = [_mapping(task).get("task_id") for task in frozen_tasks]
        frozen_ordinals = [_mapping(task).get("ordinal") for task in frozen_tasks]
        frozen_partitions = [_mapping(task).get("partition") for task in frozen_tasks]
        if task_ids != frozen_ids:
            errors.append("Demo report task IDs must match the frozen order")
        if ordinals != frozen_ordinals:
            errors.append("Demo report ordinals must match the freeze")
        if partitions != frozen_partitions:
            errors.append("Demo report partitions must match the freeze")
        for field in ("run_id", "catalog_sha256", "package_sha256"):
            if demo.get(field) != freeze.get(field):
                errors.append(f"Demo report {field} must match the freeze")
        if freeze_sha256 is not None and demo.get("demo_freeze_sha256") != freeze_sha256:
            errors.append("Demo report demo_freeze_sha256 must match demo/freeze.json")
    return tuple(errors)


def demo_success_semantic_errors(value: Any) -> tuple[str, ...]:
    """Require the exact six-task, oracle-confirmed Demo success contract."""

    demo = _mapping(value)
    tasks = list(_sequence(demo.get("tasks")))
    errors: list[str] = []
    if len(tasks) != 6:
        errors.append("demo.tasks must contain exactly six tasks")

    partition_counts = {"visible": 0, "pilot-held-out": 0}
    task_ids: list[str] = []
    for index, raw_task in enumerate(tasks):
        task = _mapping(raw_task)
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_ids.append(task_id)
        else:
            errors.append(f"demo.tasks[{index}].task_id must be a non-empty string")
        partition = task.get("partition")
        if isinstance(partition, str) and partition in partition_counts:
            partition_counts[partition] += 1
        else:
            errors.append(
                f"demo.tasks[{index}].partition must be visible or pilot-held-out"
            )
        if task.get("passed") is not True:
            errors.append(f"demo.tasks[{index}].passed must be true")
        if _mapping(task.get("oracle")).get("passed") is not True:
            errors.append(f"demo.tasks[{index}].oracle.passed must be true")
    if len(task_ids) != len(set(task_ids)):
        errors.append("demo task IDs must be unique")
    if partition_counts != {"visible": 3, "pilot-held-out": 3}:
        errors.append("demo tasks must contain three visible and three pilot-held-out tasks")

    summary = _mapping(demo.get("summary"))
    for partition, expected in (
        ("visible", 3),
        ("pilot-held-out", 3),
        ("overall", 6),
    ):
        item = _mapping(summary.get(partition))
        if item.get("passed") != expected or isinstance(item.get("passed"), bool):
            errors.append(f"demo.summary.{partition}.passed must equal {expected}")
        if item.get("total") != expected or isinstance(item.get("total"), bool):
            errors.append(f"demo.summary.{partition}.total must equal {expected}")
        success_rate = item.get("success_rate")
        if (
            not isinstance(success_rate, (int, float))
            or isinstance(success_rate, bool)
            or float(success_rate) != 1.0
        ):
            errors.append(f"demo.summary.{partition}.success_rate must equal 1.0")
    return tuple(errors)


def _known_file_evidence(
    run_root: Path, relative: str, *, label: str
) -> tuple[Mapping[str, Any] | None, list[str]]:
    """Hash one fixed run artifact without following a symlink outside the run."""

    try:
        root = run_root.resolve()
        target = (run_root / relative).resolve()
        target.relative_to(root)
        if not target.is_file():
            return None, [f"{label} is missing"]
        return {
            "path": relative,
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
        }, []
    except (OSError, RuntimeError, ValueError):
        return None, [f"{label} could not be read within the run root"]


def private_execution_preflight_semantic_errors(
    report: Mapping[str, Any],
    *,
    run_root: str | Path,
    required: bool,
) -> tuple[str, ...]:
    """Re-open and bind the redacted all-12 authored-record static gate."""

    root = Path(run_root)
    relative = "framework/private_execution_preflight.json"
    path = root / relative
    strict_required = (
        required
        and report.get("schema_version")
        == "robot_capability.sealed_run_report.v2"
    )
    has_binding = (
        report.get("artifact_hashes", {}).get("private_execution_preflight")
        if isinstance(report.get("artifact_hashes"), Mapping)
        else None
    )
    if not path.is_file() and has_binding is None:
        return (
            ("private execution preflight evidence is missing",)
            if strict_required
            else ()
        )
    evidence_record, file_errors = _known_file_evidence(
        root,
        relative,
        label="private execution preflight evidence",
    )
    errors = list(file_errors)
    if evidence_record is None:
        return tuple(errors)
    try:
        evidence = _read_json(path)
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return (*errors, "private execution preflight evidence is unreadable")
    if not isinstance(evidence, Mapping):
        return (*errors, "private execution preflight evidence must be an object")

    artifact_hashes = _mapping(report.get("artifact_hashes"))
    if artifact_hashes.get("private_execution_preflight") != evidence_record.get(
        "sha256"
    ):
        errors.append("private execution preflight artifact hash mismatch")
    report_evidence = _mapping(_mapping(report.get("evidence")).get(
        "private_execution_preflight"
    ))
    if report_evidence and report_evidence != evidence_record:
        errors.append("sealed evidence does not bind the private execution preflight")

    if evidence.get("schema_version") != "robot_capability.private_execution_preflight.v2":
        errors.append("private execution preflight schema_version is invalid")
    counts = _mapping(evidence.get("partition_counts"))
    if counts != {
        "catalog_visible": 9,
        "catalog_pilot_heldout": 3,
        "authored_instances": 12,
        "selected_visible": 3,
        "selected_pilot_heldout": 3,
        "selected_overlap": 0,
    }:
        errors.append("private execution preflight does not prove the 9/3 and 3/3 policy")
    checks = _mapping(evidence.get("authored_scene_checks"))
    if len(checks) != 8 or any(value is not True for value in checks.values()):
        errors.append("private execution preflight authored-scene checks are incomplete")
    privacy = _mapping(evidence.get("privacy"))
    if len(privacy) != 6 or any(value is not False for value in privacy.values()):
        errors.append("private execution preflight privacy boundary is invalid")
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    if "soarm101_p0_" in serialized or "initial_states/" in serialized:
        errors.append("private execution preflight leaks task IDs or source paths")

    hashes = _mapping(report.get("input_hashes"))
    if evidence.get("private_execution_bundle_freeze_sha256") != hashes.get(
        "private_execution_bundle_freeze"
    ):
        errors.append("private execution preflight bundle-freeze binding mismatch")
    if evidence.get("private_execution_bundle_manifest_sha256") != hashes.get(
        "private_execution_bundle_manifest"
    ):
        errors.append("private execution preflight bundle-manifest binding mismatch")
    sources = _mapping(evidence.get("source_bindings"))
    for evidence_name, input_name in (
        ("private_partition_source_sha256", "task_private_partition_bundle"),
        ("visible_task_catalog_sha256", "task_visible_catalog"),
        ("pilot_heldout_task_catalog_sha256", "task_pilot_heldout_catalog"),
        ("common_reset_sha256", "demo_common_reset"),
        (
            "scene_catalog_sha256",
            "generation_environment_source:morphology:scene_asset_catalog",
        ),
        ("scene_freeze_manifest_sha256", "generation_environment_manifest"),
    ):
        if sources.get(evidence_name) != hashes.get(input_name):
            errors.append(
                f"private execution preflight source binding {evidence_name} mismatch"
            )
    authored_hashes = [
        hashes.get(f"authored_initial_state:{ordinal:02d}")
        for ordinal in range(1, 13)
    ]
    if not all(
        isinstance(value, str) and _SHA256_PATTERN.fullmatch(value)
        for value in authored_hashes
    ):
        errors.append("run input hashes do not bind all twelve authored states")
    elif sources.get("authored_instance_binding_sha256") != sha256_json(
        sorted(authored_hashes)
    ):
        errors.append("private execution preflight authored-state aggregate mismatch")

    if evidence.get("mujoco_worlds_created") != 0:
        errors.append("private execution preflight must not create MuJoCo worlds")
    return tuple(errors)


def _demo_artifacts(
    run_root: Path,
) -> tuple[Mapping[str, Any] | None, list[str]]:
    errors: list[str] = []
    freeze_evidence, freeze_errors = _known_file_evidence(
        run_root, "demo/freeze.json", label="demo/freeze.json"
    )
    report_evidence, report_errors = _known_file_evidence(
        run_root, "demo/demo_report.json", label="demo/demo_report.json"
    )
    errors.extend(freeze_errors)
    errors.extend(report_errors)
    if freeze_evidence is None or report_evidence is None:
        return None, errors
    try:
        freeze = _read_json(run_root / "demo/freeze.json")
        demo_report = _read_json(run_root / "demo/demo_report.json")
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None, [*errors, "Demo freeze/report JSON could not be read"]
    if not isinstance(freeze, Mapping) or not isinstance(demo_report, Mapping):
        return None, [*errors, "Demo freeze/report JSON must contain objects"]
    return {
        "freeze": freeze,
        "report": demo_report,
        "freeze_evidence": freeze_evidence,
        "report_evidence": report_evidence,
    }, errors


def _counter_errors(
    value: Any, *, path: str, expected_name: str, expected_limit: int
) -> list[str]:
    counter = _mapping(value)
    errors: list[str] = []
    if counter.get("name") != expected_name:
        errors.append(f"{path}.name must be {expected_name!r}")
    if counter.get("limit") != expected_limit:
        errors.append(f"{path}.limit must be {expected_limit}")
    used = counter.get("used")
    remaining = counter.get("remaining")
    if (
        not isinstance(used, int)
        or isinstance(used, bool)
        or not isinstance(remaining, int)
        or isinstance(remaining, bool)
        or used < 0
        or remaining < 0
        or used + remaining != expected_limit
    ):
        errors.append(f"{path} must satisfy used + remaining == {expected_limit}")
    return errors


def budget_semantic_errors(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Check independent generation/repair/Demo accounting windows."""

    errors: list[str] = []
    budgets = _mapping(report.get("budgets"))
    errors.extend(
        _counter_errors(
            budgets.get("generation_stage1"),
            path="budgets.generation_stage1",
            expected_name="generation_stage1",
            expected_limit=3,
        )
    )
    errors.extend(
        _counter_errors(
            budgets.get("generation_stage2_initial"),
            path="budgets.generation_stage2_initial",
            expected_name="generation_stage2_initial",
            expected_limit=30,
        )
    )
    errors.extend(
        _counter_errors(
            budgets.get("validation_suite"),
            path="budgets.validation_suite",
            expected_name="validation_suite",
            expected_limit=3,
        )
    )
    suite_budget = _mapping(budgets.get("validation_suite"))
    if suite_budget.get("used") != 3:
        errors.append("a sealed validation suite must use exactly 3 agent turns")

    demo = _mapping(budgets.get("demo"))
    per_task = list(_sequence(demo.get("per_task")))
    used_per_task = list(_sequence(demo.get("used_per_task")))
    demo_tasks = list(_sequence(_mapping(report.get("demo")).get("tasks")))
    if demo.get("limit_per_task") != 30 or len(per_task) != 6 or len(used_per_task) != 6:
        errors.append("budgets.demo must contain six independent 30-call task windows")
    if len(demo_tasks) != 6:
        errors.append("report.demo.tasks must contain exactly six tasks")
    counter_names: list[str] = []
    for index, counter in enumerate(per_task):
        item = _mapping(counter)
        name = item.get("name")
        if not isinstance(name, str) or not name.startswith("demo:"):
            errors.append(f"budgets.demo.per_task[{index}].name must start with 'demo:'")
            name = "demo:invalid"
        else:
            counter_names.append(name)
        errors.extend(
            _counter_errors(
                item,
                path=f"budgets.demo.per_task[{index}]",
                expected_name=name,
                expected_limit=30,
            )
        )
        if index < len(used_per_task) and used_per_task[index] != item.get("used"):
            errors.append(
                f"budgets.demo.used_per_task[{index}] must equal per_task[{index}].used"
            )
        if index < len(demo_tasks):
            task = _mapping(demo_tasks[index])
            task_id = task.get("task_id")
            if isinstance(task_id, str) and item.get("name") != f"demo:{task_id}":
                errors.append(
                    f"budgets.demo.per_task[{index}].name must identify demo task {task_id!r}"
                )
            if task.get("agent_turn_budget") != item:
                errors.append(
                    f"report.demo.tasks[{index}].agent_turn_budget must equal "
                    f"budgets.demo.per_task[{index}]"
                )
            if task.get("model_calls") != item.get("used"):
                errors.append(
                    f"report.demo.tasks[{index}].model_calls must equal "
                    f"budgets.demo.per_task[{index}].used"
                )
            if task.get("agent_turns") != item.get("used"):
                errors.append(
                    f"report.demo.tasks[{index}].agent_turns must equal "
                    f"budgets.demo.per_task[{index}].used"
                )
    if len(counter_names) != len(set(counter_names)):
        errors.append("budgets.demo.per_task counter names must be unique")

    repair_budget = _mapping(budgets.get("repair"))
    repair = _mapping(report.get("repair"))
    for name, expected in (
        ("round_limit", 10),
        ("model_call_limit_per_round", 6),
    ):
        if repair_budget.get(name) != expected or repair.get(name) != expected:
            errors.append(f"repair.{name} and budgets.repair.{name} must equal {expected}")
    rounds_used = repair_budget.get("rounds_used")
    rounds_remaining = repair_budget.get("rounds_remaining")
    budget_results = list(_sequence(repair_budget.get("results")))
    repair_results = list(_sequence(repair.get("results")))
    history = list(_sequence(repair.get("history")))
    if (
        not isinstance(rounds_used, int)
        or isinstance(rounds_used, bool)
        or rounds_used < 0
        or rounds_used > 10
        or rounds_remaining != 10 - rounds_used
        or len(budget_results) != rounds_used
        or repair.get("rounds_used") != rounds_used
        or repair.get("rounds_remaining") != rounds_remaining
        or repair_results != budget_results
        or len(history) != rounds_used
    ):
        errors.append(
            "repair rounds/results/history must be equal-length and satisfy remaining=10-used"
        )
    for field in (
        "round_limit",
        "rounds_used",
        "rounds_remaining",
        "model_call_limit_per_round",
        "results",
    ):
        if repair.get(field) != repair_budget.get(field):
            errors.append(f"repair.{field} must equal budgets.repair.{field}")
    for index, raw_result in enumerate(budget_results, start=1):
        result = _mapping(raw_result)
        if result.get("round") != index:
            errors.append(f"repair result {index} must have contiguous round={index}")
        errors.extend(
            _counter_errors(
                result.get("agent_turn_budget"),
                path=f"budgets.repair.results[{index - 1}].agent_turn_budget",
                expected_name=f"generation_repair_round_{index:02d}",
                expected_limit=6,
            )
        )
        if result.get("agent_turns") != _mapping(result.get("agent_turn_budget")).get(
            "used"
        ):
            errors.append(
                f"repair result {index} agent_turns must equal agent_turn_budget.used"
            )
        if index <= len(history):
            feedback = _mapping(history[index - 1])
            if feedback.get("repair_round") != index:
                errors.append(
                    f"repair.history[{index - 1}].repair_round must equal {index}"
                )

    phases = _mapping(_mapping(report.get("model_accounting")).get("generation_phases"))
    stage1_phase = _mapping(phases.get("stage1"))
    stage2_phase = _mapping(phases.get("stage2_initial"))
    if stage1_phase.get("agent_turn_budget") != budgets.get("generation_stage1"):
        errors.append("Stage 1 phase budget must equal the top-level Stage 1 budget")
    if stage2_phase.get("agent_turn_budget") != budgets.get("generation_stage2_initial"):
        errors.append("Stage 2 phase budget must equal the top-level initial Stage 2 budget")
    if stage1_phase.get("agent_turns") != _mapping(
        budgets.get("generation_stage1")
    ).get("used"):
        errors.append("Stage 1 agent_turns must equal the Stage 1 budget used count")
    if stage2_phase.get("agent_turns") != _mapping(
        budgets.get("generation_stage2_initial")
    ).get("used"):
        errors.append("Stage 2 agent_turns must equal the Stage 2 budget used count")
    if list(_sequence(phases.get("repair_rounds"))) != budget_results:
        errors.append("repair phase accounting must equal budgets.repair.results")

    if "repairs" in report and list(_sequence(report.get("repairs"))) != history:
        errors.append("top-level repairs must equal repair.history")

    if "generation_agent" in report:
        generation_agent = _mapping(report.get("generation_agent"))
        if generation_agent.get("stage2_initial_budget") != budgets.get(
            "generation_stage2_initial"
        ):
            errors.append(
                "generation_agent.stage2_initial_budget must equal the Stage 2 budget"
            )
        if generation_agent.get("repair_rounds") != rounds_used:
            errors.append(
                "generation_agent.repair_rounds must equal budgets.repair.rounds_used"
            )
        if list(_sequence(generation_agent.get("repair_results"))) != budget_results:
            errors.append(
                "generation_agent.repair_results must equal budgets.repair.results"
            )
        if (
            "stage1_model_calls" in generation_agent
            and generation_agent.get("stage1_model_calls")
            != _mapping(budgets.get("generation_stage1")).get("used")
        ):
            errors.append(
                "generation_agent.stage1_model_calls must equal the Stage 1 budget used count"
            )
    return tuple(errors)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_evidence_errors(
    evidence: Any, *, run_root: Path, path: str
) -> list[str]:
    item = _mapping(evidence)
    relative = item.get("path")
    if not isinstance(relative, str):
        return [f"{path}.path must be a string"]
    try:
        root = run_root.resolve()
        target = (run_root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        return [f"{path}.path could not be resolved"]
    try:
        target.relative_to(root)
    except ValueError:
        return [f"{path}.path escapes the run root"]
    try:
        if not target.is_file():
            return [f"{path}.path is missing"]
        size = target.stat().st_size
        digest = sha256_file(target)
    except (OSError, RuntimeError, ValueError):
        return [f"{path}.path could not be read"]
    errors: list[str] = []
    if item.get("bytes") != size:
        errors.append(f"{path}.bytes does not match the file")
    if item.get("sha256") != digest:
        errors.append(f"{path}.sha256 does not match the file")
    return errors


def _evidence_collection_errors(
    *,
    required: Sequence[Mapping[str, Any]],
    actual: Any,
    path: str,
    exact: bool,
) -> list[str]:
    """Compare file evidence by path without silently collapsing duplicates."""

    errors: list[str] = []
    expected_by_path: dict[str, Mapping[str, Any]] = {}
    for index, raw_item in enumerate(required):
        item = _mapping(raw_item)
        relative = item.get("path")
        if not isinstance(relative, str):
            errors.append(f"required evidence[{index}].path must be a string")
            continue
        previous = expected_by_path.get(relative)
        if previous is not None and previous != item:
            errors.append(f"indexed evidence for {relative!r} is contradictory")
            continue
        expected_by_path[relative] = item

    actual_by_path: dict[str, Mapping[str, Any]] = {}
    for index, raw_item in enumerate(_sequence(actual)):
        item = _mapping(raw_item)
        relative = item.get("path")
        if not isinstance(relative, str):
            errors.append(f"{path}[{index}].path must be a string")
            continue
        if relative in actual_by_path:
            errors.append(f"{path} contains duplicate path {relative!r}")
            continue
        actual_by_path[relative] = item

    for relative, expected in expected_by_path.items():
        if actual_by_path.get(relative) != expected:
            errors.append(f"{path} must contain exact evidence for {relative!r}")
    if exact:
        for relative in sorted(set(actual_by_path) - set(expected_by_path)):
            errors.append(f"{path} contains unindexed evidence {relative!r}")
    return errors


def demo_artifact_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Bind a completed six-task Demo, files, and package evidence to disk.

    Oracle pass rate is an observed performance result, not the plumbing seal
    criterion in the authoritative Demo plan.  Structural consistency and six
    completed, infrastructure-clean task records remain mandatory.
    """

    root = Path(run_root)
    errors = list(
        demo_report_structure_semantic_errors(_mapping(report.get("demo")))
    )
    artifacts, artifact_errors = _demo_artifacts(root)
    errors.extend(artifact_errors)
    if artifacts is None:
        return tuple(errors)

    freeze = _mapping(artifacts["freeze"])
    demo_report = _mapping(artifacts["report"])
    freeze_evidence = _mapping(artifacts["freeze_evidence"])
    report_evidence = _mapping(artifacts["report_evidence"])
    errors.extend(
        demo_report_structure_semantic_errors(
            demo_report,
            freeze=freeze,
            freeze_sha256=str(freeze_evidence.get("sha256", "")),
        )
    )

    embedded_demo = _mapping(report.get("demo"))
    if embedded_demo.get("summary") != demo_report.get("summary"):
        errors.append("sealed demo.summary must equal demo/demo_report.json")
    if embedded_demo.get("tasks") != demo_report.get("tasks"):
        errors.append("sealed demo.tasks must equal demo/demo_report.json")

    artifact_hashes = _mapping(report.get("artifact_hashes"))
    if artifact_hashes.get("demo_freeze") != freeze_evidence.get("sha256"):
        errors.append("artifact_hashes.demo_freeze must equal demo/freeze.json")
    if artifact_hashes.get("demo_report") != report_evidence.get("sha256"):
        errors.append("artifact_hashes.demo_report must equal demo/demo_report.json")
    errors.extend(
        _evidence_collection_errors(
            required=[freeze_evidence, report_evidence],
            actual=_mapping(report.get("evidence")).get("demo"),
            path="evidence.demo",
            exact=True,
        )
    )

    freeze_package = freeze.get("package_sha256")
    report_package = demo_report.get("package_sha256")
    if (
        not isinstance(freeze_package, str)
        or _SHA256_PATTERN.fullmatch(freeze_package) is None
        or report_package != freeze_package
    ):
        errors.append("Demo freeze/report package_sha256 values must match")
    if demo_report.get("demo_freeze_sha256") != freeze_evidence.get("sha256"):
        errors.append("Demo report demo_freeze_sha256 must equal demo/freeze.json")

    freeze_ids = [
        _mapping(task).get("task_id") for task in _sequence(freeze.get("tasks"))
    ]
    report_ids = [
        _mapping(task).get("task_id")
        for task in _sequence(demo_report.get("tasks"))
    ]
    if len(freeze_ids) != 6 or freeze_ids != report_ids:
        errors.append("Demo freeze/report must bind the same six ordered task IDs")

    # Formal runs bind the private oracle role in input_hashes. Re-parse those
    # exact durable bytes here so a well-shaped Demo report cannot substitute a
    # different threshold source, parser, or task coverage after execution.
    input_hashes = _mapping(report.get("input_hashes"))
    frozen_oracle_sha256 = input_hashes.get("task_private_oracles")
    if frozen_oracle_sha256 is not None:
        oracle_path = root / "private_execution_bundle/data/task_oracles.yaml"
        oracle_freeze = _mapping(freeze.get("oracle"))
        if (
            not isinstance(frozen_oracle_sha256, str)
            or _SHA256_PATTERN.fullmatch(frozen_oracle_sha256) is None
            or not oracle_path.is_file()
            or oracle_path.is_symlink()
        ):
            errors.append("formal Demo frozen task oracle payload is missing or unsafe")
        else:
            try:
                oracle_path.resolve().relative_to(root.resolve())
                payload = oracle_path.read_bytes()
            except (OSError, RuntimeError, ValueError):
                errors.append("formal Demo frozen task oracle payload cannot be read safely")
            else:
                if sha256_file(oracle_path) != frozen_oracle_sha256:
                    errors.append("formal Demo task oracle payload hash mismatch")
                if oracle_freeze.get("definitions_sha256") != frozen_oracle_sha256:
                    errors.append("Demo freeze oracle definition hash is not the frozen role")
                try:
                    contract = parse_task_oracle_contract(payload)
                except TaskOracleContractError:
                    errors.append("formal Demo task oracle contract no longer parses")
                else:
                    if oracle_freeze.get("oracle_set_id") != contract.oracle_set_id:
                        errors.append("Demo freeze oracle_set_id is not the parsed contract")
                    if oracle_freeze.get("contract_version") != contract.version:
                        errors.append("Demo freeze oracle version is not the parsed contract")
                    if any(
                        task_id not in contract.predicates_by_task
                        for task_id in report_ids
                        if isinstance(task_id, str)
                    ):
                        errors.append("Demo freeze selects a task absent from the oracle contract")
                    for index, raw_task in enumerate(_sequence(demo_report.get("tasks"))):
                        result = _mapping(_mapping(raw_task).get("oracle"))
                        if result.get("oracle_contract_sha256") != contract.source_sha256:
                            errors.append(
                                f"Demo task {index} result is not bound to the frozen oracle contract"
                            )
                parser_path = Path(__file__).resolve().parent / "task_oracle_contract.py"
                if oracle_freeze.get("parser_source_sha256") != sha256_file(parser_path):
                    errors.append("Demo freeze task oracle parser source hash is stale")
                schema_hash = sha256_file(DEFAULT_TASK_ORACLE_SCHEMA)
                if oracle_freeze.get("contract_schema_sha256") != schema_hash:
                    errors.append("Demo freeze task oracle schema hash is stale")
                if input_hashes.get("schema:private_task_oracles.schema.json") != schema_hash:
                    errors.append("run inputs do not bind the task oracle contract schema")

    package_root = root / "generated_package"
    try:
        package_root.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        errors.append("generated_package escapes the run root")
        return tuple(errors)
    if not package_root.is_dir():
        errors.append("generated_package is missing")
        return tuple(errors)
    try:
        actual_package = package_tree_sha256(package_root)
    except (OSError, RuntimeError, ValueError):
        errors.append("generated_package tree could not be hashed")
        return tuple(errors)
    if artifact_hashes.get("generated_package") != actual_package:
        errors.append(
            "artifact_hashes.generated_package must equal the actual generated package tree"
        )
    if freeze_package != actual_package or report_package != actual_package:
        errors.append("Demo freeze/report package hash must equal the actual package tree")

    generated_evidence = _mapping(
        _mapping(report.get("evidence")).get("generated_package")
    )
    if generated_evidence.get("tree_sha256") != actual_package:
        errors.append("evidence.generated_package.tree_sha256 must equal the package tree")
    support_evidence, support_errors = _known_file_evidence(
        root,
        "generated_package/generated_capability_package/_kinematics.py",
        label="generated package _kinematics.py",
    )
    errors.extend(support_errors)
    if support_evidence is not None:
        errors.extend(
            _evidence_collection_errors(
                required=[support_evidence],
                actual=generated_evidence.get("files"),
                path="evidence.generated_package.files",
                exact=False,
            )
        )
    return tuple(errors)


_DIRECT_REPORT_NAME = re.compile(r"^direct_round_(\d{2})\.json$")


def _resolved_evidence_path(evidence: Any, *, run_root: Path) -> Path | None:
    """Resolve evidence only when it names a regular file inside ``run_root``.

    ``_file_evidence_errors`` remains the source of user-facing path/hash errors.
    This helper lets the semantic verifier safely inspect the same file without
    following an escaping symlink or duplicating those errors.
    """

    relative = _mapping(evidence).get("path")
    if not isinstance(relative, str):
        return None
    try:
        root = run_root.resolve()
        target = (run_root / relative).resolve()
        target.relative_to(root)
        if not target.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _safe_video_stem(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return name[:160] or None


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _numbers_equal(left: Any, right: Any, *, tolerance: float = 1e-9) -> bool:
    return _finite_number(left) and _finite_number(right) and math.isclose(
        float(left), float(right), rel_tol=0.0, abs_tol=tolerance
    )


def _expected_video_record_paths(
    record: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """Return the canonical video path, sidecar path, and record id."""

    phase = record.get("phase")
    if phase == "validation":
        round_index = record.get("round")
        case_stem = _safe_video_stem(record.get("case_id"))
        if (
            not isinstance(round_index, int)
            or isinstance(round_index, bool)
            or round_index < 0
            or case_stem is None
        ):
            return None, None, case_stem
        video = f"validation/videos/round_{round_index:02d}/{case_stem}.mp4"
        return video, str(Path(video).with_suffix(".metadata.json")), case_stem
    if phase == "demo":
        task_stem = _safe_video_stem(record.get("task_id"))
        if task_stem is None:
            return None, None, None
        video = f"demo/videos/{task_stem}.mp4"
        return video, str(Path(video).with_suffix(".metadata.json")), task_stem
    return None, None, None


def _formal_video_scene_expectation(
    report: Mapping[str, Any],
    *,
    run_root: Path,
) -> tuple[dict[str, str] | None, list[str]]:
    """Rebuild the resolver-selected scene identity from durable run inputs."""

    inputs = _mapping(report.get("input_hashes"))
    freeze_key = "generation_environment_freeze"
    manifest_key = "generation_environment_manifest"
    catalog_key = "generation_environment_source:morphology:scene_asset_catalog"
    values = {
        freeze_key: inputs.get(freeze_key),
        manifest_key: inputs.get(manifest_key),
        catalog_key: inputs.get(catalog_key),
    }
    present = [name for name, value in values.items() if value is not None]
    if not present:
        return None, []
    errors: list[str] = []
    if len(present) != len(values):
        return None, [
            "input_hashes must contain the complete resolver video-scene binding"
        ]
    for name, value in values.items():
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            errors.append(f"input_hashes.{name} must be a SHA-256 digest")
    if errors:
        return None, errors

    freeze_path = run_root / "generation/environment_freeze.json"
    if not freeze_path.is_file():
        return None, errors + ["generation environment freeze is missing for video evidence"]
    if sha256_file(freeze_path) != values[freeze_key]:
        errors.append(
            "input_hashes.generation_environment_freeze must equal the freeze file"
        )
    try:
        envelope = _read_json(freeze_path)
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None, errors + ["generation environment freeze could not be read"]
    manifest = envelope.get("manifest") if isinstance(envelope, Mapping) else None
    detached = (
        envelope.get("manifest_sha256") if isinstance(envelope, Mapping) else None
    )
    if (
        not isinstance(manifest, Mapping)
        or detached != values[manifest_key]
        or sha256_json(manifest) != values[manifest_key]
    ):
        return None, errors + [
            "generation environment manifest hash is invalid for video evidence"
        ]
    bindings = manifest.get("bindings")
    morphology = bindings.get("morphology") if isinstance(bindings, Mapping) else None
    catalog = (
        morphology.get("scene_asset_catalog")
        if isinstance(morphology, Mapping)
        else None
    )
    if not isinstance(catalog, Mapping):
        return None, errors + [
            "generation environment freeze lacks a video scene catalog binding"
        ]
    catalog_id = catalog.get("catalog_id")
    version = catalog.get("version")
    source_sha256 = catalog.get("sha256")
    if (
        not isinstance(catalog_id, str)
        or _SCENE_CATALOG_ID_PATTERN.fullmatch(catalog_id) is None
        or not isinstance(version, str)
        or _SCENE_CATALOG_VERSION_PATTERN.fullmatch(version) is None
        or source_sha256 != values[catalog_key]
    ):
        return None, errors + [
            "generation environment video scene catalog binding is invalid"
        ]
    return {
        "freeze_sha256": str(values[freeze_key]),
        "manifest_sha256": str(values[manifest_key]),
        "catalog_source_sha256": str(source_sha256),
        "catalog_id": catalog_id,
        "catalog_version": version,
    }, errors


def _resolved_scene_binding_errors(
    value: Any,
    *,
    path: str,
    expected: Mapping[str, str] | None,
) -> list[str]:
    binding = _mapping(value)
    errors: list[str] = []
    required = {
        "binding_sha256",
        "source_scene_request_sha256",
        "scene_catalog",
    }
    if set(binding) != required:
        return [f"{path} must contain the exact resolved-scene binding fields"]
    for name in ("binding_sha256", "source_scene_request_sha256"):
        raw = binding.get(name)
        if not isinstance(raw, str) or _SHA256_PATTERN.fullmatch(raw) is None:
            errors.append(f"{path}.{name} must be a SHA-256 digest")
    catalog = _mapping(binding.get("scene_catalog"))
    catalog_required = {
        "catalog_id",
        "version",
        "source_sha256",
        "content_sha256",
    }
    if set(catalog) != catalog_required:
        errors.append(f"{path}.scene_catalog must contain the exact catalog fields")
        return errors
    if (
        not isinstance(catalog.get("catalog_id"), str)
        or _SCENE_CATALOG_ID_PATTERN.fullmatch(str(catalog.get("catalog_id"))) is None
    ):
        errors.append(f"{path}.scene_catalog.catalog_id is invalid")
    if (
        not isinstance(catalog.get("version"), str)
        or _SCENE_CATALOG_VERSION_PATTERN.fullmatch(str(catalog.get("version")))
        is None
    ):
        errors.append(f"{path}.scene_catalog.version is invalid")
    for name in ("source_sha256", "content_sha256"):
        raw = catalog.get(name)
        if not isinstance(raw, str) or _SHA256_PATTERN.fullmatch(raw) is None:
            errors.append(f"{path}.scene_catalog.{name} must be a SHA-256 digest")
    if expected is not None:
        comparisons = {
            "catalog_id": expected["catalog_id"],
            "version": expected["catalog_version"],
            "source_sha256": expected["catalog_source_sha256"],
        }
        for name, wanted in comparisons.items():
            if catalog.get(name) != wanted:
                errors.append(
                    f"{path}.scene_catalog.{name} must equal the resolver freeze"
                )
    return errors


def _video_record_content_errors(
    raw_record: Any,
    *,
    index: Mapping[str, Any],
    run_root: Path,
    path: str,
    expected_scene: Mapping[str, str] | None = None,
) -> list[str]:
    """Re-open one indexed MP4 and independently verify its trusted sidecar."""

    record = _mapping(raw_record)
    errors: list[str] = []
    video_evidence = _mapping(record.get("video"))
    metadata_evidence = _mapping(record.get("metadata"))
    video_path = _resolved_evidence_path(video_evidence, run_root=run_root)
    metadata_path = _resolved_evidence_path(metadata_evidence, run_root=run_root)

    expected_video, expected_metadata, expected_id = _expected_video_record_paths(record)
    if expected_video is None:
        errors.append(f"{path} does not identify a canonical phase/case/task video")
    elif video_evidence.get("path") != expected_video:
        errors.append(f"{path}.video.path must equal {expected_video!r}")
    if expected_metadata is not None and metadata_evidence.get("path") != expected_metadata:
        errors.append(f"{path}.metadata.path must equal {expected_metadata!r}")
    if expected_id is not None and record.get("id") != expected_id:
        errors.append(f"{path}.id must equal {expected_id!r}")

    metadata: Mapping[str, Any] | None = None
    if metadata_path is not None:
        try:
            parsed_metadata = _read_json(metadata_path)
        except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
            errors.append(f"{path}.metadata sidecar could not be read as JSON")
        else:
            if isinstance(parsed_metadata, Mapping):
                metadata = parsed_metadata
            else:
                errors.append(f"{path}.metadata sidecar must contain an object")

    metadata_frames: int | None = None
    metadata_width: int | None = None
    metadata_height: int | None = None
    metadata_fps: float | None = None
    simulation_start: float | None = None
    simulation_end: float | None = None
    if metadata is not None:
        if metadata.get("schema_version") != "robot_capability.mujoco_video_evidence.v1":
            errors.append(f"{path}.metadata has an invalid schema_version")
        expected_name = video_path.name if video_path is not None else None
        if expected_name is not None and metadata.get("video_path") != expected_name:
            errors.append(f"{path}.metadata.video_path must equal the MP4 basename")
        if metadata.get("renderer_kind") != "mujoco.Renderer":
            errors.append(f"{path}.metadata.renderer_kind must be 'mujoco.Renderer'")
        if metadata.get("codec") != "mp4v":
            errors.append(f"{path}.metadata.codec must be 'mp4v'")

        raw_frames = metadata.get("frames")
        if (
            isinstance(raw_frames, int)
            and not isinstance(raw_frames, bool)
            and raw_frames > 0
        ):
            metadata_frames = raw_frames
        else:
            errors.append(f"{path}.metadata.frames must be a positive integer")
        raw_width = metadata.get("width")
        raw_height = metadata.get("height")
        if (
            isinstance(raw_width, int)
            and not isinstance(raw_width, bool)
            and raw_width >= 160
        ):
            metadata_width = raw_width
        else:
            errors.append(f"{path}.metadata.width must be an integer >= 160")
        if (
            isinstance(raw_height, int)
            and not isinstance(raw_height, bool)
            and raw_height >= 120
        ):
            metadata_height = raw_height
        else:
            errors.append(f"{path}.metadata.height must be an integer >= 120")
        if _finite_number(metadata.get("fps")) and float(metadata["fps"]) > 0:
            metadata_fps = float(metadata["fps"])
        else:
            errors.append(f"{path}.metadata.fps must be finite and positive")
        if _finite_number(metadata.get("simulation_start_s")):
            simulation_start = float(metadata["simulation_start_s"])
        else:
            errors.append(f"{path}.metadata.simulation_start_s must be finite")
        if _finite_number(metadata.get("simulation_end_s")):
            simulation_end = float(metadata["simulation_end_s"])
        else:
            errors.append(f"{path}.metadata.simulation_end_s must be finite")
        if (
            simulation_start is not None
            and simulation_end is not None
            and simulation_end < simulation_start
        ):
            errors.append(f"{path}.metadata simulation interval is reversed")

        for field in ("frames", "width", "height"):
            if not isinstance(record.get(field), int) or isinstance(
                record.get(field), bool
            ):
                errors.append(f"{path}.{field} must be an integer")
        for field in ("renderer_kind", "codec", "frames", "width", "height"):
            if record.get(field) != metadata.get(field):
                errors.append(f"{path}.{field} must equal its metadata sidecar")
        for field in ("fps", "simulation_start_s", "simulation_end_s"):
            if not _numbers_equal(record.get(field), metadata.get(field)):
                errors.append(f"{path}.{field} must equal its metadata sidecar")

        metadata_binding = metadata.get("resolved_scene_binding")
        record_binding = record.get("resolved_scene_binding")
        if metadata_binding is not None or record_binding is not None:
            errors.extend(
                _resolved_scene_binding_errors(
                    metadata_binding,
                    path=f"{path}.metadata.resolved_scene_binding",
                    expected=expected_scene,
                )
            )
            errors.extend(
                _resolved_scene_binding_errors(
                    record_binding,
                    path=f"{path}.resolved_scene_binding",
                    expected=expected_scene,
                )
            )
            if record_binding != metadata_binding:
                errors.append(
                    f"{path}.resolved_scene_binding must equal its metadata sidecar"
                )

        if expected_scene is not None:
            if metadata_binding is None or record_binding is None:
                errors.append(
                    f"{path} must bind resolver-frozen scene evidence"
                )
            for name, expected_value in (
                (
                    "generation_environment_freeze_sha256",
                    expected_scene["freeze_sha256"],
                ),
                (
                    "generation_environment_manifest_sha256",
                    expected_scene["manifest_sha256"],
                ),
            ):
                if record.get(name) != expected_value:
                    errors.append(f"{path}.{name} must equal the resolver freeze")
    for field in ("renderer_kind", "codec", "width", "height"):
        if record.get(field) != index.get(field):
            errors.append(f"{path}.{field} must equal video_index.{field}")
    if not _numbers_equal(record.get("fps"), index.get("fps")):
        errors.append(f"{path}.fps must equal video_index.fps")

    if (
        metadata_frames is not None
        and metadata_fps is not None
        and simulation_start is not None
        and simulation_end is not None
        and simulation_end >= simulation_start
    ):
        maximum_reasonable = (
            math.floor((simulation_end - simulation_start) * metadata_fps + 1e-6) + 2
        )
        if metadata_frames > maximum_reasonable:
            errors.append(f"{path}.metadata frame count is impossible for its time interval")

    if video_path is None:
        return errors
    try:
        import cv2
    except ImportError:
        errors.append(f"{path}.video cannot be verified because OpenCV is unavailable")
        return errors

    capture = cv2.VideoCapture(str(video_path))
    decoded_frames = 0
    decoder_fps = float("nan")
    reported_frames = float("nan")
    try:
        if not capture.isOpened():
            errors.append(f"{path}.video MP4 cannot be opened by the decoder")
            return errors
        decoder_fps = float(capture.get(cv2.CAP_PROP_FPS))
        reported_frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        while True:
            decoded, frame = capture.read()
            if not decoded:
                break
            if frame is None or len(frame.shape) < 2:
                errors.append(f"{path}.video contains an invalid decoded frame")
                continue
            if (
                metadata_width is not None
                and metadata_height is not None
                and (
                    int(frame.shape[1]) != metadata_width
                    or int(frame.shape[0]) != metadata_height
                )
            ):
                errors.append(f"{path}.video decoded frame dimensions disagree with metadata")
            decoded_frames += 1
    except Exception:
        errors.append(f"{path}.video MP4 decoding failed")
    finally:
        capture.release()

    if decoded_frames <= 0:
        errors.append(f"{path}.video MP4 contains no decodable frames")
    if metadata_frames is not None and decoded_frames != metadata_frames:
        errors.append(f"{path}.video decoded frame count disagrees with metadata")
    if (
        not math.isfinite(decoder_fps)
        or decoder_fps <= 0
        or metadata_fps is None
        or not math.isclose(
            decoder_fps, metadata_fps, rel_tol=0.01, abs_tol=0.01
        )
    ):
        errors.append(f"{path}.video decoder fps disagrees with metadata")
    if (
        math.isfinite(reported_frames)
        and reported_frames > 0
        and not math.isclose(
            reported_frames, decoded_frames, rel_tol=0.0, abs_tol=0.5
        )
    ):
        errors.append(f"{path}.video container frame count is inconsistent")
    if math.isfinite(decoder_fps) and decoder_fps > 0 and decoded_frames > 0:
        encoded_duration = decoded_frames / decoder_fps
        if not _numbers_equal(
            record.get("encoded_duration_s"), encoded_duration, tolerance=1e-6
        ):
            errors.append(f"{path}.encoded_duration_s must equal decoded frames / fps")
    return errors


def _validation_video_binding_errors(
    report: Mapping[str, Any],
    *,
    index: Mapping[str, Any],
    records: Sequence[Any],
    run_root: Path,
    expected_scene: Mapping[str, str] | None = None,
) -> list[str]:
    """Rebuild direct-report round/case bindings independently from the index."""

    # Offline runs deliberately execute direct validation without physical
    # videos. Their complete empty index must remain valid.
    if index.get("required") is False and not records:
        return []
    # A partial failure index may contain no bindable Validation recordings
    # even though direct reports or unfinished encoder files exist.
    if index.get("status") != "complete" and not records:
        return []

    errors: list[str] = []
    direct_by_round: dict[int, dict[str, Any]] = {}
    for direct_path in sorted((run_root / "validation").glob("direct_round_*.json")):
        match = _DIRECT_REPORT_NAME.fullmatch(direct_path.name)
        if match is None:
            continue
        round_index = int(match.group(1))
        relative = f"validation/{direct_path.name}"
        evidence, evidence_errors = _known_file_evidence(
            run_root, relative, label=relative
        )
        errors.extend(evidence_errors)
        if evidence is None:
            continue
        try:
            direct = _read_json(direct_path)
        except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
            errors.append(f"{relative} could not be read as JSON")
            continue
        if not isinstance(direct, Mapping):
            errors.append(f"{relative} must contain an object")
            continue
        raw_cases = direct.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            errors.append(f"{relative}.cases must be a non-empty array")
            continue
        case_ids = [_mapping(case).get("case_id") for case in raw_cases]
        valid_case_ids = [case_id for case_id in case_ids if isinstance(case_id, str) and case_id]
        if len(valid_case_ids) != len(case_ids):
            errors.append(f"{relative}.cases must have non-empty string case_id values")
        if len(valid_case_ids) != len(set(valid_case_ids)):
            errors.append(f"{relative}.cases must have unique case_id values")
        safe_stems = [_safe_video_stem(case_id) for case_id in valid_case_ids]
        if any(stem is None for stem in safe_stems):
            errors.append(f"{relative}.cases contain a case_id with no safe filename")
        if len(safe_stems) != len(set(safe_stems)):
            errors.append(f"{relative}.cases collide after safe filename mapping")
        scene_binding_by_case: dict[str, Mapping[str, Any]] = {}
        for case_number, raw_case in enumerate(raw_cases):
            case = _mapping(raw_case)
            case_id = case.get("case_id")
            diagnostics = _mapping(case.get("framework_diagnostics"))
            resolved_scene = diagnostics.get("resolved_scene_binding")
            if isinstance(case_id, str) and isinstance(resolved_scene, Mapping):
                scene_binding_by_case[case_id] = resolved_scene
                errors.extend(
                    _resolved_scene_binding_errors(
                        resolved_scene,
                        path=(
                            f"{relative}.cases[{case_number}].framework_diagnostics."
                            "resolved_scene_binding"
                        ),
                        expected=expected_scene,
                    )
                )
        summary = _mapping(direct.get("summary"))
        if summary.get("total") != len(raw_cases) or isinstance(summary.get("total"), bool):
            errors.append(f"{relative}.summary.total must equal its executed cases")
        suite_sha256 = direct.get("suite_sha256")
        package_sha256 = direct.get("package_sha256")
        if not isinstance(suite_sha256, str) or not _SHA256_PATTERN.fullmatch(suite_sha256):
            errors.append(f"{relative}.suite_sha256 is invalid")
        if not isinstance(package_sha256, str) or not _SHA256_PATTERN.fullmatch(package_sha256):
            errors.append(f"{relative}.package_sha256 is invalid")
        direct_by_round[round_index] = {
            "case_ids": set(valid_case_ids),
            "suite_sha256": suite_sha256,
            "package_sha256": package_sha256,
            "evidence": evidence,
            "scene_binding_by_case": scene_binding_by_case,
        }

    indexed_by_round: dict[int, set[str]] = {}
    for number, raw_record in enumerate(records):
        record = _mapping(raw_record)
        item_path = f"video_recording.validation[{number}]"
        round_index = record.get("round")
        case_id = record.get("case_id")
        if (
            not isinstance(round_index, int)
            or isinstance(round_index, bool)
            or round_index < 0
        ):
            errors.append(f"{item_path}.round must be a non-negative integer")
            continue
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{item_path}.case_id must be a non-empty string")
            continue
        cases = indexed_by_round.setdefault(round_index, set())
        if case_id in cases:
            errors.append(f"{item_path} duplicates case_id {case_id!r} in its round")
        cases.add(case_id)
        binding = direct_by_round.get(round_index)
        if binding is None:
            errors.append(f"{item_path} is orphaned from direct_round_{round_index:02d}.json")
            continue
        if case_id not in binding["case_ids"]:
            errors.append(f"{item_path}.case_id is absent from its direct report")
        if record.get("suite_sha256") != binding["suite_sha256"]:
            errors.append(f"{item_path}.suite_sha256 must equal its direct report")
        if record.get("package_sha256") != binding["package_sha256"]:
            errors.append(f"{item_path}.package_sha256 must equal its direct report")
        if record.get("direct_report") != binding["evidence"]:
            errors.append(f"{item_path}.direct_report must equal exact file evidence")
        report_scene_binding = binding["scene_binding_by_case"].get(case_id)
        record_scene_binding = record.get("resolved_scene_binding")
        if (
            expected_scene is not None
            or report_scene_binding is not None
            or record_scene_binding is not None
        ) and report_scene_binding != record_scene_binding:
            errors.append(
                f"{item_path}.resolved_scene_binding must equal its direct report case"
            )
        errors.extend(
            _file_evidence_errors(
                record.get("direct_report"),
                run_root=run_root,
                path=f"{item_path}.direct_report",
            )
        )

    if index.get("required") is True and index.get("status") == "complete":
        if not direct_by_round or not records:
            errors.append(
                "complete Validation video evidence requires direct reports and records"
            )
        if set(indexed_by_round) != set(direct_by_round):
            errors.append(
                "complete Validation video rounds must exactly match direct reports"
            )
        for round_index, binding in direct_by_round.items():
            if indexed_by_round.get(round_index, set()) != binding["case_ids"]:
                errors.append(
                    f"complete Validation round {round_index:02d} videos must exactly "
                    "match direct-report case IDs"
                )

    expected_suite = _mapping(report.get("validation")).get("suite_sha256")
    artifact_suite = _mapping(report.get("artifact_hashes")).get("validation_suite")
    for round_index, binding in direct_by_round.items():
        if isinstance(expected_suite, str) and binding["suite_sha256"] != expected_suite:
            errors.append(
                f"direct_round_{round_index:02d}.suite_sha256 must equal sealed validation"
            )
        if isinstance(artifact_suite, str) and binding["suite_sha256"] != artifact_suite:
            errors.append(
                f"direct_round_{round_index:02d}.suite_sha256 must equal "
                "artifact_hashes.validation_suite"
            )

    report_evidence = _mapping(report.get("evidence"))
    if "direct_validation" in report_evidence:
        errors.extend(
            _evidence_collection_errors(
                required=[binding["evidence"] for binding in direct_by_round.values()],
                actual=report_evidence.get("direct_validation"),
                path="evidence.direct_validation",
                exact=True,
            )
        )
    return errors


def _video_index_semantic_errors(
    report: Mapping[str, Any],
    *,
    run_root: str | Path,
    evidence: Any,
    evidence_path: str,
    exact_evidence: bool,
) -> tuple[str, ...]:
    root = Path(run_root)
    errors: list[str] = []
    index_path = root / "video_index.json"
    try:
        if not index_path.is_file():
            return ("video_index.json is missing",)
        index = _read_json(index_path)
        index_size = index_path.stat().st_size
        expected_index_hash = sha256_file(index_path)
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return ("video_index.json could not be read",)

    if report.get("video_recording") != index:
        errors.append("embedded video_recording must equal video_index.json")
    if _mapping(report.get("artifact_hashes")).get("video_index") != expected_index_hash:
        errors.append("artifact_hashes.video_index must equal video_index.json")

    required_evidence: list[Mapping[str, Any]] = [
        {
            "path": "video_index.json",
            "sha256": expected_index_hash,
            "bytes": index_size,
        }
    ]
    index_mapping = _mapping(index)
    expected_scene, scene_expectation_errors = _formal_video_scene_expectation(
        report,
        run_root=root,
    )
    errors.extend(scene_expectation_errors)
    records: list[Any] = []
    validation_records = list(_sequence(index_mapping.get("validation")))
    for number, record in enumerate(validation_records):
        if _mapping(record).get("phase") != "validation":
            errors.append(
                f"video_recording.validation[{number}].phase must be 'validation'"
            )
    records.extend(validation_records)
    demo_records = list(_sequence(index_mapping.get("demo")))
    for number, record in enumerate(demo_records):
        if _mapping(record).get("phase") != "demo":
            errors.append(f"video_recording.demo[{number}].phase must be 'demo'")
    records.extend(demo_records)

    record_file_paths: dict[str, str] = {}
    for number, raw_record in enumerate(records):
        record = _mapping(raw_record)
        for field in ("video", "metadata"):
            if field not in record:
                errors.append(f"video_recording.records[{number}].{field} is missing")
                continue
            item_path = f"video_recording.records[{number}].{field}"
            file_evidence = _mapping(record[field])
            required_evidence.append(file_evidence)
            errors.extend(
                _file_evidence_errors(
                    file_evidence,
                    run_root=root,
                    path=item_path,
                )
            )
            relative = file_evidence.get("path")
            if isinstance(relative, str):
                previous = record_file_paths.get(relative)
                if previous is not None:
                    errors.append(
                        f"indexed video path {relative!r} is reused by {previous} and {item_path}"
                    )
                else:
                    record_file_paths[relative] = item_path
        errors.extend(
            _video_record_content_errors(
                record,
                index=index_mapping,
                run_root=root,
                path=f"video_recording.records[{number}]",
                expected_scene=expected_scene,
            )
        )

    errors.extend(
        _validation_video_binding_errors(
            report,
            index=index_mapping,
            records=validation_records,
            run_root=root,
            expected_scene=expected_scene,
        )
    )

    if demo_records:
        artifacts, artifact_errors = _demo_artifacts(root)
        errors.extend(artifact_errors)
        if artifacts is not None:
            freeze = _mapping(artifacts["freeze"])
            demo_report = _mapping(artifacts["report"])
            freeze_evidence = _mapping(artifacts["freeze_evidence"])
            report_evidence = _mapping(artifacts["report_evidence"])
            package_sha256 = freeze.get("package_sha256")
            if (
                not isinstance(package_sha256, str)
                or _SHA256_PATTERN.fullmatch(package_sha256) is None
                or demo_report.get("package_sha256") != package_sha256
            ):
                errors.append("Demo video binding freeze/report package hashes must match")
            if demo_report.get("demo_freeze_sha256") != freeze_evidence.get(
                "sha256"
            ):
                errors.append(
                    "Demo video binding report.demo_freeze_sha256 must equal freeze file"
                )
            frozen_task_ids = [
                _mapping(task).get("task_id")
                for task in _sequence(freeze.get("tasks"))
            ]
            report_task_ids = [
                _mapping(task).get("task_id")
                for task in _sequence(demo_report.get("tasks"))
            ]
            report_scene_binding_by_task: dict[str, Mapping[str, Any]] = {}
            for task_number, raw_task in enumerate(
                _sequence(demo_report.get("tasks"))
            ):
                task = _mapping(raw_task)
                task_id = task.get("task_id")
                resolved_scene = _mapping(task.get("oracle")).get(
                    "resolved_scene_binding"
                )
                if isinstance(task_id, str) and isinstance(
                    resolved_scene, Mapping
                ):
                    report_scene_binding_by_task[task_id] = resolved_scene
                    errors.extend(
                        _resolved_scene_binding_errors(
                            resolved_scene,
                            path=(
                                f"demo/demo_report.json.tasks[{task_number}].oracle."
                                "resolved_scene_binding"
                            ),
                            expected=expected_scene,
                        )
                    )
            if frozen_task_ids != report_task_ids:
                errors.append("Demo video binding task IDs disagree between freeze/report")
            indexed_task_ids: list[str] = []
            for number, raw_record in enumerate(demo_records):
                record = _mapping(raw_record)
                task_id = record.get("task_id")
                if isinstance(task_id, str):
                    indexed_task_ids.append(task_id)
                else:
                    errors.append(
                        f"video_recording.demo[{number}].task_id must be a string"
                    )
                if record.get("package_sha256") != package_sha256:
                    errors.append(
                        f"video_recording.demo[{number}].package_sha256 must equal Demo freeze/report"
                    )
                report_scene_binding = report_scene_binding_by_task.get(str(task_id))
                record_scene_binding = record.get("resolved_scene_binding")
                if (
                    expected_scene is not None
                    or report_scene_binding is not None
                    or record_scene_binding is not None
                ) and report_scene_binding != record_scene_binding:
                    errors.append(
                        f"video_recording.demo[{number}].resolved_scene_binding must "
                        "equal its Demo oracle"
                    )
                for field, expected in (
                    ("demo_freeze", freeze_evidence),
                    ("demo_report", report_evidence),
                ):
                    item_path = f"video_recording.demo[{number}].{field}"
                    actual = record.get(field)
                    if actual != expected:
                        errors.append(f"{item_path} must equal the exact Demo file evidence")
                    errors.extend(
                        _file_evidence_errors(
                            actual,
                            run_root=root,
                            path=item_path,
                        )
                    )
            if len(indexed_task_ids) != len(set(indexed_task_ids)):
                errors.append("Demo video records must have unique task IDs")
            frozen_string_ids = {
                task_id for task_id in frozen_task_ids if isinstance(task_id, str)
            }
            unknown_task_ids = set(indexed_task_ids) - frozen_string_ids
            if unknown_task_ids:
                errors.append("Demo video records contain task IDs absent from the freeze")
            if (
                index_mapping.get("status") == "complete"
                and (
                    len(indexed_task_ids) != len(frozen_task_ids)
                    or set(indexed_task_ids) != frozen_string_ids
                )
            ):
                errors.append(
                    "complete Demo video records must bind all six frozen task IDs"
                )

    errors.extend(
        _evidence_collection_errors(
            required=required_evidence,
            actual=evidence,
            path=evidence_path,
            exact=exact_evidence,
        )
    )
    return tuple(errors)


def video_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Bind embedded video records, artifact hashes, and evidence to disk."""

    return _video_index_semantic_errors(
        report,
        run_root=run_root,
        evidence=_mapping(report.get("evidence")).get("videos"),
        evidence_path="evidence.videos",
        exact_evidence=True,
    )


def terminal_video_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Audit failure-run video evidence without requiring sealed-run budgets."""

    video_recording = _mapping(report.get("video_recording"))
    status = video_recording.get("status")
    schema_version = video_recording.get("schema_version")
    if (
        schema_version == "robot_capability.mujoco_video_status.v1"
        and status in {"unavailable", "error"}
    ):
        return ()
    if schema_version != "robot_capability.mujoco_video_index.v1":
        return ("terminal video_recording has an unsupported schema_version",)
    errors = list(
        _video_index_semantic_errors(
            report,
            run_root=run_root,
            evidence=report.get("evidence_files"),
            evidence_path="evidence_files",
            exact_evidence=False,
        )
    )
    root = Path(run_root)
    for index, evidence in enumerate(_sequence(report.get("evidence_files"))):
        errors.extend(
            _file_evidence_errors(
                evidence,
                run_root=root,
                path=f"evidence_files[{index}]",
            )
        )
    return tuple(errors)


def terminal_state_semantic_errors(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Keep the Demo business-failure reason and terminal state inseparable."""

    terminal_state = report.get("terminal_state")
    terminal_reason = report.get("terminal_reason")
    if terminal_state == "DEMO_FAILED":
        if terminal_reason != DEMO_ORACLE_GATE_FAILURE_REASON:
            return (
                "DEMO_FAILED terminal_state requires the Demo oracle gate reason code",
            )
    elif terminal_reason == DEMO_ORACLE_GATE_FAILURE_REASON:
        return (
            "Demo oracle gate reason code requires DEMO_FAILED terminal_state",
        )
    return ()


def terminal_manifest_snapshot_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Bind the failure snapshot to final budgets and the live manifest cut.

    The semantic audit runs once before ``terminal_report.json`` exists and can
    also be re-run later.  Post-write, the live manifest may differ from the
    snapshot only by ``updated_at``, the terminal-report hash, and its single
    append-only event.
    """

    root = Path(run_root)
    snapshot_path = root / "run_manifest_terminal_snapshot.json"
    current_path = root / "run_manifest.json"
    # Very early input failures may happen before provenance can be snapshotted;
    # preserve their sanitized terminal reporting path.  Normal pipeline
    # failures always create this file before calling the semantic audit.
    if not snapshot_path.is_file():
        return ()
    errors: list[str] = []
    try:
        snapshot_raw = _read_json(snapshot_path)
        current_raw = _read_json(current_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ("terminal run-manifest snapshot or live manifest is unreadable",)
    snapshot = _mapping(snapshot_raw)
    current = _mapping(current_raw)
    if not snapshot or not current:
        return ("terminal run-manifest snapshot and live manifest must be objects",)

    bindings = (
        ("run_id", "run_id"),
        ("state", "terminal_state"),
        ("terminal_reason", "terminal_reason"),
        ("input_hashes", "input_hashes"),
        ("artifact_hashes", "artifact_hashes"),
        ("budgets", "budgets"),
    )
    for manifest_field, report_field in bindings:
        if snapshot.get(manifest_field) != report.get(report_field):
            errors.append(
                f"terminal snapshot {manifest_field} must equal report {report_field}"
            )

    snapshot_events = list(_sequence(snapshot.get("events")))
    accounting_event = _mapping(snapshot_events[-1]) if snapshot_events else {}
    aggregate = _mapping(_mapping(report.get("model_accounting")).get("aggregate"))
    if accounting_event.get("event") != "terminal_accounting_finalized":
        errors.append("terminal snapshot must end with terminal_accounting_finalized")
    else:
        if accounting_event.get("budget_names") != sorted(
            _mapping(report.get("budgets"))
        ):
            errors.append("terminal accounting event budget_names are stale")
        for field in ("logical_requests", "provider_http_attempts", "provider_retries"):
            if accounting_event.get(field) != aggregate.get(field):
                errors.append(f"terminal accounting event {field} is stale")

    normalized_current = dict(current)
    normalized_snapshot = dict(snapshot)
    normalized_current.pop("updated_at", None)
    normalized_snapshot.pop("updated_at", None)
    current_artifacts = dict(_mapping(normalized_current.get("artifact_hashes")))
    report_digest = current_artifacts.pop("terminal_report", None)
    normalized_current["artifact_hashes"] = current_artifacts
    current_events = list(_sequence(normalized_current.get("events")))
    if report_digest is not None:
        terminal_path = root / "terminal_report.json"
        if not terminal_path.is_file() or report_digest != sha256_file(terminal_path):
            errors.append("live manifest terminal_report hash is invalid")
        if not current_events or _mapping(current_events[-1]).get("event") != (
            "terminal_report_written"
        ):
            errors.append("live manifest is missing terminal_report_written")
        else:
            event = _mapping(current_events.pop())
            if event.get("report_sha256") != report_digest:
                errors.append("terminal_report_written hash is stale")
    normalized_current["events"] = current_events
    if normalized_current != normalized_snapshot:
        errors.append(
            "live manifest differs from terminal snapshot beyond the report hash/event"
        )
    return tuple(errors)


def terminal_demo_failure_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Bind DEMO_FAILED to one valid, non-infrastructure six-task report."""

    if report.get("terminal_state") != "DEMO_FAILED":
        return ()
    root = Path(run_root)
    artifacts, artifact_errors = _demo_artifacts(root)
    errors = list(artifact_errors)
    if artifacts is None:
        errors.append("DEMO_FAILED requires demo/freeze.json and demo/demo_report.json")
        return tuple(errors)

    freeze = _mapping(artifacts["freeze"])
    demo_report = _mapping(artifacts["report"])
    freeze_evidence = _mapping(artifacts["freeze_evidence"])
    report_evidence = _mapping(artifacts["report_evidence"])
    schema_root = Path(__file__).resolve().parents[2] / "schemas"
    for label, artifact, schema_name in (
        ("Demo freeze", freeze, "demo_freeze.schema.json"),
        ("Demo report", demo_report, "demo_report.schema.json"),
    ):
        schema_path = schema_root / schema_name
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"canonical {label} schema is unavailable")
            continue
        for issue in validate_json_schema(
            artifact,
            schema,
            instance_path=f"$terminal_{label.lower().replace(' ', '_')}",
        ):
            errors.append(f"{label} schema: {issue.path}: {issue.message}")
    errors.extend(
        demo_report_structure_semantic_errors(
            demo_report,
            freeze=freeze,
            freeze_sha256=str(freeze_evidence.get("sha256", "")),
        )
    )
    tasks = [_mapping(task) for task in _sequence(demo_report.get("tasks"))]
    if any(
        task.get("termination_reason") == "infrastructure_failure"
        or task.get("agent_status") == "infrastructure_failed"
        or "infrastructure_error" in _mapping(task.get("oracle"))
        for task in tasks
    ):
        errors.append("DEMO_FAILED report must not contain task infrastructure markers")
    oracle_misses = [
        task for task in tasks if _mapping(task.get("oracle")).get("passed") is False
    ]
    if not oracle_misses:
        errors.append("DEMO_FAILED report requires at least one oracle miss")

    evidence_by_path = {
        item.get("path"): item
        for item in (_mapping(raw) for raw in _sequence(report.get("evidence_files")))
        if isinstance(item.get("path"), str)
    }
    for relative, expected in (
        ("demo/freeze.json", freeze_evidence),
        ("demo/demo_report.json", report_evidence),
    ):
        if evidence_by_path.get(relative) != expected:
            errors.append(f"DEMO_FAILED evidence_files must bind exact {relative}")
    return tuple(errors)


def terminal_report_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    """Audit failure classification and any retained video evidence."""

    return (
        terminal_state_semantic_errors(report)
        + terminal_manifest_snapshot_semantic_errors(report, run_root=run_root)
        + terminal_demo_failure_semantic_errors(report, run_root=run_root)
        + terminal_video_semantic_errors(report, run_root=run_root)
        + private_execution_preflight_semantic_errors(
            report,
            run_root=run_root,
            required=False,
        )
    )


def sealed_report_semantic_errors(
    report: Mapping[str, Any], *, run_root: str | Path
) -> tuple[str, ...]:
    return (
        budget_semantic_errors(report)
        + demo_artifact_semantic_errors(report, run_root=run_root)
        + video_semantic_errors(report, run_root=run_root)
        + private_execution_preflight_semantic_errors(
            report,
            run_root=run_root,
            required=True,
        )
        + frozen_sdk_activation_semantic_errors(
            report.get("sdk_activation"),
            run_root=run_root,
            input_hashes=report.get("input_hashes"),
            artifact_hashes=report.get("artifact_hashes"),
            expected_mode=report.get("mode"),
        )
    )
