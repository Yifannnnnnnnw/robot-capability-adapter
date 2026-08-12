from __future__ import annotations

import copy
import json
from pathlib import Path

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.blue_line.runner import _guards, _measurements, _policy, _standards
from autoadapter2.generation import FixtureJsonGenerator, Stage1Config, Stage1Runner
from autoadapter2.implementation import validate_implementation_bundle
from autoadapter2.libraries import TasksLibrary
from autoadapter2.orchestration.direct_general_demo import load_direct_task_adapter
from autoadapter2.orchestration.direct_mujoco_run import resolve_morphology_record
from autoadapter2.orchestration.first_g2_demo import G2_PROFILE


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_ROOT = PROJECT_ROOT / "general_demo/private_governance/blue_line/inputs/franka_panda/1.0.0"


def _read(name: str) -> dict:
    return json.loads((INPUT_ROOT / name).read_text(encoding="utf-8"))


def test_franka_direct_inputs_bind_all_five_tasks_and_metrics() -> None:
    adapter = load_direct_task_adapter(PROJECT_ROOT, "franka_panda")
    snapshot = _read("standards_snapshot.json")
    catalog = _read("measurement_catalog.json")
    policy = _read("blue_line_policy.json")
    projection = _read("robot_public_projection.json")
    bundle = _read("implementation_bundle.json")

    assert [task.task_id for task in adapter.tasks] == ["F01", "F02", "F03", "F04", "F05"]
    assert _standards(snapshot)
    assert _measurements(catalog)
    assert _guards(catalog)
    assert _policy(policy)
    validated_bundle = validate_implementation_bundle(bundle)

    package = resolve_morphology_record("franka_panda", "1.0.0", PROJECT_ROOT, PROJECT_ROOT)
    facts = bundle["robot_implementation_facts"]
    assert package.config.joint_names == tuple(facts["joint_names"])
    assert package.config.actuator_names == tuple(facts["actuator_names"])
    assert package.config.body_names == ("hand",)
    assert validated_bundle.bundle_hash.startswith("sha256:")

    effect_ids = {record["effect_id"] for record in snapshot["standards"]}
    assert effect_ids == set(projection["effect_allowlist"])
    catalog_metrics = {
        metric
        for measurement in catalog["measurements"]
        for metric in measurement["metrics"]
    }
    adapter_metrics = {
        declaration["metric"]
        for task in adapter.tasks
        for declaration in task.measurement_declarations
    }
    assert adapter_metrics <= catalog_metrics


def test_franka_blue_line_can_bind_all_five_effects() -> None:
    snapshot = _read("standards_snapshot.json")
    catalog = _read("measurement_catalog.json")
    policy = _read("blue_line_policy.json")
    projection = _read("robot_public_projection.json")
    stage1_projection = copy.deepcopy(projection)
    stage1_projection.pop("execution_route")
    stage1_projection["unit_allowlist"] = [
        unit for unit in stage1_projection["unit_allowlist"] if "mujoco" not in unit.lower()
    ]
    task_package = TasksLibrary(PROJECT_ROOT / "general_demo/libraries/tasks").load(
        "franka_panda", "1.0.0"
    )
    tasks = task_package.stage1_projection("franka-blue-line-test")
    capabilities = []
    for task, effect in zip(tasks, projection["effect_allowlist"], strict=True):
        capabilities.append({
            "capability_id": f"capability-{task['requirement_id'][-8:]}",
            "kind": "action",
            "requirement_ids": [task["requirement_id"]],
            "inputs": [],
            "outputs": [],
            "effect": effect,
            "preconditions": [],
            "invocation_semantics": "Invoke once with the public task inputs.",
            "temporal_semantics": "Return after a bounded physical observation window.",
            "invariants": [],
            "required_action_affordances": ["named actuator command"],
            "required_observation_affordances": ["body state"],
            "errors": [{"code": "TARGET_REJECTED", "message": "The public target cannot be accepted."}],
            "unsupported_scope": [],
        })
    stage1 = Stage1Runner(FixtureJsonGenerator([{
        "capabilities": capabilities,
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }]), Stage1Config(max_correction_calls=0)).run(
        "franka-blue-line-test", stage1_projection, tasks, G2_PROFILE
    )
    assert stage1.status == "SEALED" and stage1.capability_design and stage1.seal

    standard_by_effect = {record["effect_id"]: record for record in snapshot["standards"]}
    specs = []
    for capability in stage1.capability_design["capabilities"]:
        standard = standard_by_effect[capability["effect"]]
        guards = standard["required_guard_ids"]
        specs.append({
            "capability_id": capability["capability_id"],
            "criteria": [
                {**copy.deepcopy(criterion), "guard_ids": list(guards)}
                for criterion in standard["criteria"]
            ],
            "cases": [{"case_id": "nominal", "initial_state": {}, "inputs": {}}],
            "lineage": {"kind": "COPIED", "standard_id": standard["standard_id"], "material": False},
            "false_pass_analysis": [{"risk_id": guard, "guard_id": guard} for guard in guards],
        })
    blue = BlueLineRunner(FixtureJsonGenerator([{"capability_specs": specs}])).run(
        stage1.capability_design,
        stage1.seal,
        snapshot,
        catalog,
        policy,
    )
    assert blue.status == "READY"
    assert len(blue.validation_suite["capability_cases"]) == 5
