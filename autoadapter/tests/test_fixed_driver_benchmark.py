from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "configs" / "benchmarks" / "fixed_driver_v1.json"
ROBOT_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
ROBOT_ROOT = ROBOT_INDEX_PATH.parent
STANDARD_FIELDS = (
    "metric",
    "unit",
    "comparator",
    "threshold",
    "temporal",
    "aggregation",
)
ARM_BENCHMARK_THRESHOLDS = {
    "reach": 0.02,
    "contact": 0.02,
    "object": 0.03,
    "fixture": 0.015,
    "rotation": 0.03,
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_capabilities(contract: dict, robot: dict) -> list[dict]:
    profile = contract["capability_profiles"][robot["capability_profile_id"]]
    by_id = {
        capability["capability_id"]: capability
        for capability in profile["capabilities"]
    }
    return [by_id[capability_id] for capability_id in robot["capability_ids"]]


def test_fixed_driver_contract_is_exactly_the_mainline_cohort() -> None:
    contract = _read(CONTRACT_PATH)
    index = _read(ROBOT_INDEX_PATH)["robots"]
    robot_ids = [robot["robot_configuration_id"] for robot in contract["robots"]]

    assert contract["artifact_type"] == "fixed_driver_benchmark_contract"
    assert contract["scope"]["replaces_formal_tgcd"] is False
    assert contract["scope"]["reference_measurements_define_thresholds"] is False
    assert robot_ids == list(index)
    assert "unitree_g1" not in robot_ids
    assert "google_barkour_vb" not in robot_ids


def test_fixed_methods_and_primary_standards_resolve_exactly() -> None:
    contract = _read(CONTRACT_PATH)
    index = _read(ROBOT_INDEX_PATH)["robots"]

    for robot in contract["robots"]:
        robot_id = robot["robot_configuration_id"]
        package_root = ROBOT_ROOT / index[robot_id]
        tasks = _read(package_root / "tasks" / "catalog.json")["tasks"]
        task_by_id = {task["task_id"]: task for task in tasks}
        capabilities = _selected_capabilities(contract, robot)
        methods = [capability["method_name"] for capability in capabilities]

        assert len(methods) == len(set(methods))
        assert all(method.isidentifier() for method in methods)

        for capability in capabilities:
            selected = capability["primary_standard"]
            source_task = task_by_id[selected["source_task_id"]]
            source_clause = next(
                clause
                for clause in source_task["scoring"]
                if clause["clause_id"] == selected["source_clause_id"]
            )
            assert {
                field: selected[field] for field in STANDARD_FIELDS
            } == {field: source_clause[field] for field in STANDARD_FIELDS}


def test_arm_benchmark_gates_are_approved_and_stricter_than_source() -> None:
    contract = _read(CONTRACT_PATH)
    profile = contract["capability_profiles"]["metaworld_operations"]
    capabilities = {
        capability["capability_id"]: capability
        for capability in profile["capabilities"]
    }

    assert contract["acceptance"]["source_standard_pass"] == "reported_separately"
    assert set(capabilities) == set(ARM_BENCHMARK_THRESHOLDS)
    for capability_id, expected_threshold in ARM_BENCHMARK_THRESHOLDS.items():
        capability = capabilities[capability_id]
        source = capability["primary_standard"]
        gate = capability["benchmark_gate"]
        assert source["comparator"] == "<="
        assert gate == {
            "threshold": expected_threshold,
            "threshold_provenance": "pre_registered_human_approved_study_threshold",
        }
        assert gate["threshold"] < source["threshold"]


def test_reference_controls_exist_and_are_marked_framework_only() -> None:
    contract = _read(CONTRACT_PATH)
    index = _read(ROBOT_INDEX_PATH)["robots"]

    assert contract["scope"]["reference_source_visibility"] == "framework_only"
    for robot in contract["robots"]:
        package_root = ROBOT_ROOT / index[robot["robot_configuration_id"]]
        reference_path = package_root / "reference" / "driver.py"
        tree = ast.parse(reference_path.read_text(encoding="utf-8"))
        class_nodes = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        reference = robot["reference_control"]
        assert reference["class_name"] in class_nodes

        if reference["mode"] == "framework_renderer":
            renderer_path = package_root / "reference" / "rendering.py"
            renderer_tree = ast.parse(renderer_path.read_text(encoding="utf-8"))
            assert any(
                isinstance(node, ast.FunctionDef)
                and node.name == "render_reference_driver"
                for node in renderer_tree.body
            )
            continue

        assert reference["mode"] == "direct_fixed_interface"
        method_names = {
            node.name
            for node in class_nodes[reference["class_name"]].body
            if isinstance(node, ast.FunctionDef)
        }
        required_methods = {
            capability["method_name"]
            for capability in _selected_capabilities(contract, robot)
        }
        assert required_methods <= method_names
