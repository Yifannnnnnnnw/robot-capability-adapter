"""Implementation-blind Blue Line compiler for the AutoAdapter 1.0 validator."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .precision_policy import policy_record, validate_position_standard
from .stage1_scope import DecisionScope


BLUE_LINE_PROMPT = """
Return one JSON object and no Markdown, with exactly this shape:
{"capability_tests": [...]}

Emit exactly one test for every capability in capability_design. Each test has exactly:
capability_id, method, standard_id, case_id, inputs, measurement, comparator, threshold.

Match a capability to the frozen standard and case by its exact `effect`. Copy the standard's
measurement, comparator, threshold, and ID exactly. Copy the case ID and complete inputs exactly.
Set method to the capability's exact effect. Do not invent or change an input, threshold, method,
measurement, or capability. Do not mention an implementation, driver, skeleton, candidate,
execution, or outcome. On correction calls, repair and return the complete object again.
""".strip()


@dataclass(frozen=True)
class LegacyBlueLineResult:
    status: str
    suite: dict[str, Any] | None
    calls: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def reference_blue_body(
    capability_design: Mapping[str, Any],
    private_evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    standards = {
        item["effect"]: item for item in private_evaluation["standards"]
    }
    tests: list[dict[str, Any]] = []
    for capability in capability_design["capabilities"]:
        effect = capability["effect"]
        standard = standards[effect]
        case = private_evaluation["cases"][effect]
        tests.append({
            "capability_id": capability["capability_id"],
            "method": effect,
            "standard_id": standard["standard_id"],
            "case_id": case["case_id"],
            "inputs": copy.deepcopy(case["inputs"]),
            "measurement": standard["measurement"],
            "comparator": standard["comparator"],
            "threshold": standard["threshold"],
        })
    return {"capability_tests": tests}


def _diagnostics(
    output: Mapping[str, Any],
    design: Mapping[str, Any],
    private_evaluation: Mapping[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if set(output) != {"capability_tests"}:
        issues.append({"code": "OUTPUT_FIELDS", "message": "output must contain only capability_tests"})
    tests = output.get("capability_tests")
    if not isinstance(tests, list):
        return issues + [{"code": "TESTS_TYPE", "message": "capability_tests must be an array"}]
    expected = reference_blue_body(design, private_evaluation)["capability_tests"]
    expected_by_id = {item["capability_id"]: item for item in expected}
    seen: set[str] = set()
    fields = {
        "capability_id", "method", "standard_id", "case_id", "inputs",
        "measurement", "comparator", "threshold",
    }
    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            issues.append({"code": "TEST_TYPE", "message": f"test[{index}] must be an object"})
            continue
        if set(test) != fields:
            issues.append({"code": "TEST_FIELDS", "message": f"test[{index}] has an invalid closed shape"})
            continue
        capability_id = test.get("capability_id")
        if not isinstance(capability_id, str) or capability_id not in expected_by_id:
            issues.append({"code": "CAPABILITY", "message": f"test[{index}] references an unknown capability"})
            continue
        if capability_id in seen:
            issues.append({"code": "CAPABILITY", "message": f"capability {capability_id} is duplicated"})
        seen.add(capability_id)
        if test != expected_by_id[capability_id]:
            issues.append({
                "code": "FROZEN_INPUT_OR_STANDARD",
                "message": f"test for {capability_id} must exactly copy its frozen input and standard",
            })
    missing = set(expected_by_id) - seen
    if missing:
        issues.append({"code": "COVERAGE", "message": f"missing capabilities: {sorted(missing)}"})
    return issues


class LegacyBlueLineRunner:
    """Three-call isolated generator; it never sees the generated driver."""

    def __init__(self, generator: Any) -> None:
        self.generator = generator

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_hash: str,
        private_evaluation: Mapping[str, Any],
    ) -> LegacyBlueLineResult:
        base_inputs = {
            "capability_design": copy.deepcopy(dict(capability_design)),
            "design_hash": design_hash,
            "standards": copy.deepcopy(private_evaluation["standards"]),
            "cases": copy.deepcopy(private_evaluation["cases"]),
        }
        calls: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        working: dict[str, Any] | None = None
        for attempt in range(3):
            inputs = copy.deepcopy(base_inputs)
            if working is not None:
                inputs["working_suite"] = working
                inputs["diagnostics"] = copy.deepcopy(diagnostics)
            output = self.generator.generate_json("blue_line", BLUE_LINE_PROMPT, inputs)
            diagnostics = _diagnostics(output, capability_design, private_evaluation)
            calls.append({
                "call": attempt + 1,
                "diagnostics": copy.deepcopy(diagnostics),
            })
            if not diagnostics:
                suite = {
                    "artifact_type": "legacy_direct_validation_suite",
                    "schema_version": "1.0.0",
                    "design_hash": design_hash,
                    "suite_id": private_evaluation["suite_id"],
                    "capability_tests": copy.deepcopy(output["capability_tests"]),
                }
                return LegacyBlueLineResult("READY", suite, tuple(calls), ())
            working = copy.deepcopy(output)
        return LegacyBlueLineResult("FAILED", None, tuple(calls), tuple(diagnostics))


TASK_BLUE_LINE_PROMPT = """
Return one JSON object and no Markdown, with exactly this shape:
{"requirement_tests": [...]}

Compile exactly one requirement test for every covered requirement in
decision_scope, in covered_requirement_ids order.  Join only through the exact
requirement_to_task and requirement_to_capability maps.  For each task, copy its
fixed case, scene, reset, invocation, complete criteria, measurement declarations,
dwell values and guards exactly from the supplied private source objects.  The
method and effect must equal the selected capability effect and the adapter
invocation effect.  Do not invent, omit, merge, relax or paraphrase any value.
Do not mention or inspect a driver, skeleton, implementation, execution or result.
On a correction call, return the complete corrected object again.
""".strip()


@dataclass(frozen=True)
class TaskBlueLineResult:
    status: str
    suite: dict[str, Any] | None
    calls: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]


def _items_by_id(values: list[Any], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{label}[{index}] must be an object")
        identifier = value.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"{label} requires unique non-empty {key} values")
        result[identifier] = copy.deepcopy(dict(value))
    return result


def _criteria_for_task(
    criterion: Mapping[str, Any],
    measurements: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    groups = (
        ("full", "checks", float(criterion.get("continuous_dwell_s", 0.0) or 0.0)),
        ("motion", "motion_checks", 0.0),
        (
            "terminal",
            "terminal_checks",
            float(criterion.get("terminal_continuous_dwell_s", 0.0) or 0.0),
        ),
    )
    task_id = str(criterion["task_id"])
    for phase, field, dwell_s in groups:
        checks = criterion.get(field, [])
        if not isinstance(checks, list):
            raise ValueError(f"private criterion {task_id}.{field} must be an array")
        for check in checks:
            if not isinstance(check, Mapping):
                raise ValueError(f"private criterion {task_id}.{field} has an invalid check")
            metric = check.get("metric")
            measurement = measurements.get(str(metric))
            if measurement is None:
                raise ValueError(f"task {task_id} has no direct measurement for {metric!r}")
            if measurement.get("status") != "RESOLVED":
                raise ValueError(f"task {task_id} measurement {metric!r} is unresolved")
            validate_position_standard(check, measurement, dwell_s=dwell_s)
            result.append({
                "criterion_id": f"{task_id}:{phase}:{metric}",
                "metric": metric,
                "comparator": check.get("comparator"),
                "threshold": copy.deepcopy(check.get("value")),
                "phase": phase,
                "dwell_s": dwell_s,
                "measurement": copy.deepcopy(dict(measurement)),
            })
    if not result:
        raise ValueError(f"task {task_id} has no executable criteria")
    return result


def requirement_blue_body(
    capability_design: Mapping[str, Any],
    scope: DecisionScope,
    public_tasks: list[Mapping[str, Any]],
    private_criteria: list[Mapping[str, Any]],
    task_instances: Mapping[str, Any],
    direct_adapter: Mapping[str, Any],
    common_guards: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deterministically compile the private expected suite for fail-closed checking."""

    public_by_task = _items_by_id(list(public_tasks), "task_id", "public tasks")
    criteria_by_task = _items_by_id(list(private_criteria), "task_id", "private criteria")
    raw_instances = task_instances.get("instances")
    if not isinstance(raw_instances, list):
        raise ValueError("task_instances_private.instances must be an array")
    instances_by_task = _items_by_id(raw_instances, "task_id", "task instances")
    raw_adapter_tasks = direct_adapter.get("tasks")
    if not isinstance(raw_adapter_tasks, list):
        raise ValueError("direct_mujoco_adapter.tasks must be an array")
    adapter_by_task = _items_by_id(raw_adapter_tasks, "task_id", "direct adapter tasks")
    capabilities = {
        str(item["capability_id"]): dict(item)
        for item in capability_design["capabilities"]
    }
    guard_by_id = _items_by_id(list(common_guards), "guard_id", "common guards")

    tests: list[dict[str, Any]] = []
    for requirement_id in scope.covered_requirement_ids:
        task_id = scope.requirement_to_task[requirement_id]
        capability_id = scope.requirement_to_capability[requirement_id]
        capability = capabilities[capability_id]
        effect = str(capability["effect"])
        if task_id not in public_by_task or task_id not in criteria_by_task:
            raise ValueError(f"covered task {task_id!r} is not in the canonical Tasks Library")
        if task_id not in instances_by_task or task_id not in adapter_by_task:
            raise ValueError(f"covered task {task_id!r} lacks an executable direct binding")
        criterion = criteria_by_task[task_id]
        instance = instances_by_task[task_id]
        adapter = adapter_by_task[task_id]
        invocation = adapter.get("invocation")
        if not isinstance(invocation, Mapping) or invocation.get("effect") != effect:
            raise ValueError(
                f"covered task {task_id!r} does not bind selected effect {effect!r}"
            )
        measurements_raw = adapter.get("measurements")
        if not isinstance(measurements_raw, list):
            raise ValueError(f"direct task {task_id!r} measurements must be an array")
        measurements = _items_by_id(measurements_raw, "metric", f"{task_id} measurements")
        guard_ids = criterion.get("guard_ids")
        if not isinstance(guard_ids, list) or any(value not in guard_by_id for value in guard_ids):
            raise ValueError(f"task {task_id!r} references an unknown guard")
        scene = adapter.get("scene_entrypoint")
        if not isinstance(scene, str) or not scene:
            raise ValueError(f"task {task_id!r} lacks a scene entrypoint")
        if scene.startswith("assets/mjcf/"):
            scene = scene.removeprefix("assets/mjcf/")
        case_id = instance.get("instance_id", f"{task_id}-fixed")
        tests.append({
            "requirement_id": requirement_id,
            "task_id": task_id,
            "capability_id": capability_id,
            "effect": effect,
            "method": effect,
            "case_id": case_id,
            "scene_entrypoint": scene,
            "reset": copy.deepcopy(adapter.get("reset", {})),
            "parameters": copy.deepcopy(instance.get("parameters", adapter.get("parameters", {}))),
            "invocation": copy.deepcopy(dict(invocation)),
            "criteria": _criteria_for_task(criterion, measurements),
            "guards": [copy.deepcopy(guard_by_id[value]) for value in guard_ids],
        })
    return {"requirement_tests": tests}


def _task_diagnostics(
    output: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> list[dict[str, str]]:
    if set(output) != {"requirement_tests"}:
        return [{"code": "OUTPUT_FIELDS", "message": "output must contain only requirement_tests"}]
    tests = output.get("requirement_tests")
    if not isinstance(tests, list):
        return [{"code": "TESTS_TYPE", "message": "requirement_tests must be an array"}]
    if output != expected:
        return [{
            "code": "FROZEN_REQUIREMENT_SUITE",
            "message": "requirement tests must exactly match every covered frozen task binding",
        }]
    return []


class TaskBlueLineRunner:
    """Isolated task-suite compiler; it never receives the generated driver."""

    def __init__(self, generator: Any) -> None:
        self.generator = generator

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_hash: str,
        scope: DecisionScope,
        public_tasks: list[Mapping[str, Any]],
        private_evaluation: Mapping[str, Any],
        task_instances: Mapping[str, Any],
        direct_adapter: Mapping[str, Any],
    ) -> TaskBlueLineResult:
        criteria = private_evaluation.get("criteria")
        guards = private_evaluation.get("common_guards")
        if not isinstance(criteria, list) or not isinstance(guards, list):
            raise ValueError("private evaluation requires criteria and common_guards arrays")
        expected = requirement_blue_body(
            capability_design,
            scope,
            public_tasks,
            criteria,
            task_instances,
            direct_adapter,
            guards,
        )
        base_inputs = {
            "capability_design": copy.deepcopy(dict(capability_design)),
            "design_hash": design_hash,
            "decision_scope": scope.to_dict(),
            "public_tasks": copy.deepcopy([dict(value) for value in public_tasks]),
            "private_criteria": copy.deepcopy(criteria),
            "task_instances": copy.deepcopy(dict(task_instances)),
            "direct_adapter": copy.deepcopy(dict(direct_adapter)),
            "common_guards": copy.deepcopy(guards),
        }
        calls: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        working: dict[str, Any] | None = None
        for attempt in range(3):
            inputs = copy.deepcopy(base_inputs)
            if working is not None:
                inputs["working_suite"] = working
                inputs["diagnostics"] = copy.deepcopy(diagnostics)
            output = self.generator.generate_json("blue_line", TASK_BLUE_LINE_PROMPT, inputs)
            diagnostics = _task_diagnostics(output, expected)
            calls.append({"call": attempt + 1, "diagnostics": copy.deepcopy(diagnostics)})
            if not diagnostics:
                suite = {
                    "artifact_type": "demo2_direct_validation_suite",
                    "schema_version": "2.0.0",
                    "design_hash": design_hash,
                    "robot_configuration_id": direct_adapter["robot_configuration_id"],
                    "precision_policy": policy_record(),
                    "requirement_tests": copy.deepcopy(output["requirement_tests"]),
                }
                return TaskBlueLineResult("READY", suite, tuple(calls), ())
            working = copy.deepcopy(output)
        return TaskBlueLineResult("FAILED", None, tuple(calls), tuple(diagnostics))
