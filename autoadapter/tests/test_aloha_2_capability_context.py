from __future__ import annotations

import json
from pathlib import Path

import mujoco

from autoadapter2.harness.operators import audit_inline_measurement_binding
from autoadapter2.harness.runner import _merged_private_index
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package
from autoadapter2.validation_compiler.ivc import (
    _private_inputs_from_package,
    _project_private_inputs_for_model,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "aloha_2" / "1.0.0"
PRIVATE_ROOT = PACKAGE_ROOT / "capability_validation" / "private"


def _read(name: str) -> dict:
    return json.loads((PRIVATE_ROOT / name).read_text(encoding="utf-8"))


def _criterion(binding: dict) -> dict:
    return {
        "metric": binding["metric"],
        "unit": binding["unit"],
        "comparator": "<=",
        "threshold": 0.0,
        "temporal": {"kind": "terminal_state"},
        "aggregation": {"kind": "single_trial"},
        "source_refs": ["capability-context-operator-smoke"],
    }


def test_aloha_capability_context_is_symmetric_private_and_executable() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    merged = _private_inputs_from_package(package)
    projected = _project_private_inputs_for_model(merged)
    instances = _read("instances.json")["instances"]
    bindings = _read("bindings.json")["bindings"]
    guards = {guard["guard_id"]: guard for guard in _read("guards.json")["guards"]}

    assert [instance["instance_id"] for instance in instances] == [
        "aloha-2-capability-left-side",
        "aloha-2-capability-right-side",
    ]
    assert instances[0]["guard_ids"][-1] == "aloha-capability-right-side-neutral"
    assert instances[1]["guard_ids"][-1] == "aloha-capability-left-side-neutral"
    assert all(
        name.startswith("right/")
        for name in guards["aloha-capability-right-side-neutral"]["joint_tolerances"]
    )
    assert all(
        name.startswith("left/")
        for name in guards["aloha-capability-left-side-neutral"]["joint_tolerances"]
    )
    for instance in instances:
        assert not {
            "task_id",
            "public_arguments",
            "request",
            "request_anchors",
            "task_plan",
            "clause_bindings",
        }.intersection(instance)

    capability_instances = [
        item
        for item in merged["instances"]["instances"]
        if item["context_namespace"] == "capability"
    ]
    assert {item["instance_id"] for item in capability_instances} == {
        instance["instance_id"] for instance in instances
    }
    projected_capability_instances = [
        item
        for item in projected["instances"]["instances"]
        if item["context_namespace"] == "capability"
    ]
    assert all("reset" in item for item in projected_capability_instances)
    assert set(
        _merged_private_index(
            package, "instances", "instance_id", capability_v2=True
        )
    ).issuperset({instance["instance_id"] for instance in instances})

    scene_path = PACKAGE_ROOT / "assets" / "reach_scene.xml"
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    joint_names = {
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, index)
        for index in range(model.njnt)
    }
    for instance in instances:
        apply_framework_reset(mujoco, model, data, instance["reset"])
    for guard in guards.values():
        if guard["kind"] == "named_joints_remain_near_reset":
            assert set(guard["joint_tolerances"]).issubset(joint_names)

    endpoint_schema = {
        "type": "object",
        "properties": {
            "target_position_m": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "unit": "m",
                "frame": "world",
                "items": {
                    "type": "number",
                    "unit": "m",
                    "frame": "world",
                    "minimum": -1.0,
                    "maximum": 1.0,
                },
            }
        },
        "required": ["target_position_m"],
        "additionalProperties": False,
    }
    gripper_schema = {
        "type": "object",
        "properties": {
            "target_position": {
                "type": "number",
                "unit": "m",
                "frame": "none",
                "minimum": 0.002,
                "maximum": 0.037,
            }
        },
        "required": ["target_position"],
        "additionalProperties": False,
    }
    wrist_schema = {
        "type": "object",
        "properties": {
            "target_position": {
                "type": "number",
                "unit": "rad",
                "frame": "none",
                "minimum": -3.14158,
                "maximum": 3.14158,
            }
        },
        "required": ["target_position"],
        "additionalProperties": False,
    }
    for binding_with_id in bindings:
        binding = {
            key: binding_with_id[key]
            for key in ("metric", "unit", "kind", "parameters")
        }
        schema = (
            endpoint_schema
            if binding["kind"] == "final_site_position_error"
            else wrist_schema
            if binding["unit"] == "rad"
            else gripper_schema
        )
        assert audit_inline_measurement_binding(
            binding,
            criterion=_criterion(binding),
            request_schema=schema,
            scene_path=scene_path,
        ) == binding
