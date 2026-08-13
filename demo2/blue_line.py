"""Implementation-blind Blue Line compiler for the AutoAdapter 1.0 validator."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping


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
