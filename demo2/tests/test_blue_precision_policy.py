from __future__ import annotations

import pytest

from demo2.blue_line import TaskBlueLineRunner, requirement_blue_body
from demo2.pipeline import FixtureJsonGenerator, _run_inputs, resolve_package
from demo2.precision_policy import (
    POSITION_ERROR_OPERATORS,
    TERMINAL_DWELL_OPERATORS,
    policy_record,
    validate_position_standard,
)
from demo2.stage1_scope import decision_scope, reference_design_body


MANIPULATION_ROBOTS = (
    "robotstudio_so101",
    "franka_panda",
    "kuka_iiwa_14",
    "piper",
    "universal_robots_ur5e",
    "pushbench",
)


def test_broad_or_non_dwelling_position_standard_is_rejected() -> None:
    measurement = {"operator": "distance"}

    with pytest.raises(ValueError, match="cap 0.01 m"):
        validate_position_standard(
            {"comparator": "<=", "value": 0.011},
            measurement,
            dwell_s=0.5,
        )
    with pytest.raises(ValueError, match="continuous_dwell_s >= 0.5"):
        validate_position_standard(
            {"comparator": "<=", "value": 0.01},
            measurement,
            dwell_s=0.49,
        )


@pytest.mark.parametrize("robot_id", MANIPULATION_ROBOTS)
def test_migrated_task_package_compiles_under_frozen_precision_policy(
    robot_id: str,
) -> None:
    package = resolve_package(robot_id)
    run_id = f"precision-policy-{robot_id}"
    stage1_tasks, public_tasks, projection = _run_inputs(package, run_id)
    design = reference_design_body(projection, stage1_tasks)
    scope = decision_scope(design, public_tasks, package.stage1_policy)
    expected = requirement_blue_body(
        design,
        scope,
        public_tasks,
        package.tasks.demo_private_criteria(),
        package.task_instances,
        package.direct_adapter,
        package.private_evaluation["common_guards"],
    )
    result = TaskBlueLineRunner(FixtureJsonGenerator([expected])).run(
        design,
        "test-design-hash",
        scope,
        public_tasks,
        package.private_evaluation,
        package.task_instances,
        package.direct_adapter,
    )

    assert result.status == "READY"
    assert result.suite is not None
    assert result.suite["precision_policy"] == policy_record()
    assert len(result.suite["requirement_tests"]) == len(public_tasks) == 5
    for requirement in result.suite["requirement_tests"]:
        for criterion in requirement["criteria"]:
            operator = criterion["measurement"]["operator"]
            if operator not in POSITION_ERROR_OPERATORS:
                continue
            if criterion["comparator"] in {"<", "<="}:
                assert 0.0 < float(criterion["threshold"]) <= 0.01
            if operator in TERMINAL_DWELL_OPERATORS:
                assert float(criterion["dwell_s"]) >= 0.5


def test_compiled_blue_artifact_contains_exact_terminal_position_standard() -> None:
    package = resolve_package("franka_panda")
    run_id = "precision-policy-tamper"
    stage1_tasks, public_tasks, projection = _run_inputs(package, run_id)
    design = reference_design_body(projection, stage1_tasks)
    scope = decision_scope(design, public_tasks, package.stage1_policy)
    expected = requirement_blue_body(
        design,
        scope,
        public_tasks,
        package.tasks.demo_private_criteria(),
        package.task_instances,
        package.direct_adapter,
        package.private_evaluation["common_guards"],
    )
    first = expected["requirement_tests"][0]["criteria"][0]
    assert first["threshold"] == pytest.approx(0.01)
    assert first["dwell_s"] == pytest.approx(0.5)
