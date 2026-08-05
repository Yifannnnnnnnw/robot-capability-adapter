from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from soarm_demo.oracle import FixtureDemoEnvironment, MujocoTabletopDemoEnvironment
from soarm_demo.demo_runner import FrozenDemoRunner
from soarm_demo.model_client import ScriptedModelClient
from soarm_demo.private_execution_bundle import materialize_private_execution_bundle
from soarm_demo.task_oracle_contract import (
    DEFAULT_TASK_ORACLE_SCHEMA,
    EXPECTED_MEASURES_BY_TASK,
    TaskOracleContractError,
    contract_from_environment_context,
    parse_task_oracle_contract,
    trusted_environment_context,
)


ROOT = Path(__file__).resolve().parents[1]
ORACLES = ROOT / "private/task_library/soarm101_tabletop/v1/task_oracles.yaml"
STATES = ROOT / "private/task_library/soarm101_tabletop/v1/initial_states"
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
COMMON = STATES / "_common_reset.json"


def _source_mapping() -> dict[str, Any]:
    value = yaml.safe_load(ORACLES.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _payload(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(value), sort_keys=False, allow_unicode=True
    ).encode("utf-8")


def _contract_with_push_thresholds(
    *, distance_m: float, settle_s: float | None = None
):
    value = _source_mapping()
    predicate = value["predicates"]["oracle.push_cube_to_region"]
    predicate["all_of"][0]["tolerance"] = distance_m
    if settle_s is not None:
        predicate["settle_window_s"] = settle_s
    return parse_task_oracle_contract(_payload(value))


def _state(task_id: str) -> dict[str, Any]:
    return json.loads((STATES / f"{task_id}.json").read_text(encoding="utf-8"))


def _set_fixture_object_xy(
    environment: FixtureDemoEnvironment, object_id: str, x: float, y: float
) -> None:
    environment.runtime._objects[object_id]["position_m"][0] = x
    environment.runtime._objects[object_id]["position_m"][1] = y


def _set_mujoco_object_xy(runtime: Any, object_id: str, x: float, y: float) -> None:
    with runtime._lock:
        joint_id = runtime._name_id(
            runtime._mj.mjtObj.mjOBJ_JOINT, f"{object_id}_freejoint"
        )
        qpos_address = int(runtime.model.jnt_qposadr[joint_id])
        dof_address = int(runtime.model.jnt_dofadr[joint_id])
        runtime.data.qpos[qpos_address] = x
        runtime.data.qpos[qpos_address + 1] = y
        runtime.data.qvel[dof_address : dof_address + 6] = 0.0
        runtime._mj.mj_forward(runtime.model, runtime.data)


def test_frozen_contract_is_strict_immutable_and_covers_exactly_all_12_tasks() -> None:
    contract = parse_task_oracle_contract(ORACLES.read_bytes())

    assert contract.schema_version == "robot_capability.private_task_oracles.v1"
    assert contract.version == "1.0.1"
    assert contract.raw["status"] == "p0_frozen"
    assert contract.raw["freeze_gate"]["reachability"] == "not_predeclared_or_scored"
    assert set(contract.predicates_by_task) == set(EXPECTED_MEASURES_BY_TASK)
    assert len(contract.predicates_by_task) == 12
    assert DEFAULT_TASK_ORACLE_SCHEMA.is_file()
    with pytest.raises(TypeError):
        contract.predicates_by_task["new_task"] = contract.predicate_for_task(
            "soarm101_p0_push_cube_to_region"
        )
    with pytest.raises(TypeError):
        contract.raw["status"] = "pending"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda value: value.__setitem__("status", "threshold_freeze_pending"),
            "schema validation failed",
        ),
        (
            lambda value: value["predicates"].pop("oracle.reach_target_pose"),
            "schema validation failed",
        ),
        (
            lambda value: value["predicates"]["oracle.push_cube_to_region"][
                "all_of"
            ][0].__setitem__("measure", "unknown_measure"),
            "measures must be exactly",
        ),
        (
            lambda value: value["predicates"]["oracle.push_cube_to_region"][
                "all_of"
            ][0].__setitem__("operator", "equals"),
            "operator must be",
        ),
        (
            lambda value: value["predicates"]["oracle.push_cube_to_region"][
                "all_of"
            ][0].__setitem__("object_id", "not_in_task"),
            "not an object in this task",
        ),
        (
            lambda value: value["predicates"]["oracle.push_cube_to_region"][
                "all_of"
            ][0].__setitem__("tolerance", math.nan),
            "non-finite",
        ),
    ),
)
def test_nonfrozen_missing_unknown_and_nonfinite_contracts_fail_closed(
    mutation, message: str
) -> None:
    value = _source_mapping()
    mutation(value)
    with pytest.raises(TaskOracleContractError, match=message):
        parse_task_oracle_contract(_payload(value))


def test_duplicate_yaml_key_fails_closed() -> None:
    payload = ORACLES.read_bytes() + b"\nstatus: p0_frozen\n"
    with pytest.raises(TaskOracleContractError, match="duplicate YAML key"):
        parse_task_oracle_contract(payload)


def test_fixture_oracle_uses_contract_at_pass_boundary_fail_and_threshold_change() -> None:
    task_id = "soarm101_p0_push_cube_to_region"
    instance = _state(task_id)
    target = instance["agent_input"]["targets"]["push_region"]["center_m"]
    contract = _contract_with_push_thresholds(distance_m=0.025)
    environment = FixtureDemoEnvironment(
        trusted_environment_context(instance, contract)
    )
    try:
        environment.reset(instance)
        _set_fixture_object_xy(environment, "red_cube", target[0], target[1])
        assert environment.score({"task_id": task_id}, instance)["passed"] is True

        environment.reset(instance)
        _set_fixture_object_xy(
            environment, "red_cube", target[0] + 0.025, target[1]
        )
        boundary = environment.score({"task_id": task_id}, instance)
        assert boundary["passed"] is True
        assert boundary["measurements"]["tolerance_m"] == pytest.approx(0.025)

        environment.reset(instance)
        _set_fixture_object_xy(
            environment, "red_cube", target[0] + 0.0252, target[1]
        )
        assert environment.score({"task_id": task_id}, instance)["passed"] is False
    finally:
        environment.close()

    stricter = _contract_with_push_thresholds(distance_m=0.010)
    environment = FixtureDemoEnvironment(
        trusted_environment_context(instance, stricter)
    )
    try:
        environment.reset(instance)
        _set_fixture_object_xy(
            environment, "red_cube", target[0] + 0.015, target[1]
        )
        result = environment.score({"task_id": task_id}, instance)
        assert result["passed"] is False
        assert result["measurements"]["tolerance_m"] == pytest.approx(0.010)
        assert result["oracle_contract_sha256"] == stricter.source_sha256
    finally:
        environment.close()


def test_actual_mujoco_oracle_uses_changed_contract_threshold_and_settle_window() -> None:
    pytest.importorskip("mujoco")
    task_id = "soarm101_p0_push_cube_to_region"
    instance = _state(task_id)
    target = instance["agent_input"]["targets"]["push_region"]["center_m"]
    contract = _contract_with_push_thresholds(distance_m=0.010, settle_s=0.01)
    environment = MujocoTabletopDemoEnvironment(
        trusted_environment_context(instance, contract),
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        _set_mujoco_object_xy(
            environment._mujoco_runtime,
            "red_cube",
            target[0] + 0.010,
            target[1],
        )
        before = float(environment._mujoco_runtime.data.time)
        boundary = environment.score({"task_id": task_id}, instance)
        elapsed = float(environment._mujoco_runtime.data.time) - before
        assert boundary["passed"] is True
        assert boundary["measurements"]["target_tolerance_m"] == pytest.approx(0.010)
        assert boundary["evaluation_window_s"] == pytest.approx(0.01)
        assert elapsed == pytest.approx(0.01, abs=1e-9)

        environment.reset(instance)
        _set_mujoco_object_xy(
            environment._mujoco_runtime,
            "red_cube",
            target[0] + 0.0102,
            target[1],
        )
        failed = environment.score({"task_id": task_id}, instance)
        assert failed["passed"] is False
        assert failed["oracle_contract_sha256"] == contract.source_sha256
    finally:
        environment.close()


def test_frozen_demo_runner_parses_bundle_bytes_before_first_agent_turn_and_injects_contract(
    tmp_path: Path,
) -> None:
    bundle = materialize_private_execution_bundle(
        private_task_root=ROOT / "private/task_library/soarm101_tabletop/v1",
        visible_tasks_path=ROOT
        / "libraries/tasks/soarm101_tabletop/v1/visible_tasks.jsonl",
        destination=tmp_path / "private_bundle",
        schema_path=ROOT / "schemas/private_execution_bundle.schema.json",
    )
    observed_contracts = []

    class Environment:
        runtime = object()

        def __init__(self, context: Mapping[str, Any]) -> None:
            contract = contract_from_environment_context(context)
            assert contract is not None
            observed_contracts.append(contract)

        def reset(self, instance: Mapping[str, Any]) -> None:
            del instance

        def score(
            self, task: Mapping[str, Any], instance: Mapping[str, Any]
        ) -> Mapping[str, Any]:
            del task, instance
            raise AssertionError("budget-exhausted scripted Agent must not score")

        def close(self) -> None:
            return None

    runner = FrozenDemoRunner(
        client=ScriptedModelClient(["not-json"] * 6, model="contract-injection-test"),
        system_prompt="Use validated tools.",
        combined_catalog={"package_sha256": "0" * 64, "tools": []},
        tool_factory=lambda runtime: {},
        environment_factory=Environment,
        output_dir=tmp_path / "demo",
        max_model_calls_per_task=1,
        oracle_evaluator_id="test:Environment.score",
        oracle_evaluator_path=Path(__file__),
    )
    report = runner.run(run_id="contract-bound", private_execution_bundle=bundle)

    assert len(observed_contracts) == 6
    assert {
        contract.source_sha256 for contract in observed_contracts
    } == {bundle.sha256_for_role("task_oracles")}
    assert all(task["agent_turns"] == 1 for task in report["tasks"])
    freeze = json.loads((tmp_path / "demo/freeze.json").read_text(encoding="utf-8"))
    assert freeze["oracle"]["definitions_sha256"] == bundle.sha256_for_role(
        "task_oracles"
    )
    assert freeze["oracle"]["oracle_set_id"] == "soarm101_tabletop_p0"
    assert freeze["oracle"]["contract_version"] == "1.0.1"
