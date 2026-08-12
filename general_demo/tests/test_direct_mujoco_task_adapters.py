from __future__ import annotations

import json
import math
from pathlib import Path
from xml.etree import ElementTree

import pytest

from autoadapter2.integrations.direct_mujoco import (
    DirectMuJoCoConfigurationError,
    load_direct_mujoco_task_config,
)


ROOT = Path(__file__).resolve().parents[1]
ROBOTS = (
    "franka_panda",
    "kuka_iiwa_14",
    "piper",
    "pushbench",
    "robotstudio_so101",
    "universal_robots_ur5e",
)
SOURCE_COMMIT = "585eb1f1fde33f17f5f9a1e169a18dd41f97b586"
RESOLVED_OPERATORS = {
    "distance",
    "path",
    "read",
    "value",
    "component",
    "vector_component",
    "xy",
    "project_xy",
    "xy_projection",
    "norm",
    "contact_count",
    "contact_boolean",
    "contact_to_bool",
    "boolean_contact",
    "trace_baseline_delta",
    "baseline_delta",
    "history_waypoint_max_error",
    "history_max_distance",
    "history_waypoint_max_distance",
    "history_final_return_error",
    "final_return_error",
    "history_waypoint_order",
    "history_order",
    "history_directional_delta",
    "directional_delta",
    "offset_error",
    "baseline_offset_error",
    "invocation_count",
    "attempt_count",
    "action_count",
}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _adapter(robot: str) -> dict:
    return _json(ROOT / "libraries/tasks" / robot / "1.0.0" / "direct_mujoco_adapter.json")


def _approved(robot: str) -> tuple[dict, dict, dict]:
    task_root = ROOT / "libraries/tasks" / robot / "1.0.0"
    catalog = _json(task_root / "catalog.json")
    evaluation = _json(task_root / "evaluation_private.json")
    instances = _json(task_root / "task_instances_private.json")
    return catalog, evaluation, instances


def _scene_names(path: Path) -> dict[str, set[str]]:
    seen: set[Path] = set()
    names = {kind: set() for kind in ("body", "site", "geom", "joint", "actuator")}

    def visit(current: Path) -> None:
        current = current.resolve()
        if current in seen:
            return
        seen.add(current)
        root = ElementTree.parse(current).getroot()
        for element in root.iter():
            if element.tag == "include" and element.get("file"):
                visit(current.parent / element.attrib["file"])
            elif element.tag in names and element.get("name"):
                names[element.tag].add(element.attrib["name"])

    visit(path)
    return names


def _path_value(root: object, path: str) -> object:
    current = root
    for component in path.split("."):
        if not isinstance(current, dict) or component not in current:
            raise AssertionError(f"missing declared parameter path: {path}")
        current = current[component]
    return current


def _cache_scene_path(robot: str, source_scene: str) -> str:
    return f"assets/mjcf/{robot}/{Path(source_scene).name}"


def _load_for_execution(adapter: dict, task_id: str):
    """The data-loader gate used before a direct task can be executed."""

    return load_direct_mujoco_task_config(adapter, task_id=task_id)


@pytest.mark.parametrize("robot", ROBOTS)
def test_direct_task_adapter_mirrors_approved_instances_and_scene_closures(robot: str) -> None:
    adapter = _adapter(robot)
    catalog, evaluation, instances = _approved(robot)
    assert adapter["artifact_type"] == "direct_mujoco_task_adapter"
    assert adapter["schema_version"] == "1.0.0"
    assert adapter["robot_configuration_id"] == robot
    assert adapter["execution_route"] == "DIRECT_MUJOCO_EXPERIMENTAL"
    assert adapter["source_commit"] == SOURCE_COMMIT

    expected_ids = [item["task_id"] for item in catalog["tasks"]]
    assert [item["task_id"] for item in adapter["tasks"]] == expected_ids
    criteria_by_id = {item["task_id"]: item for item in evaluation["criteria"]}
    instances_by_id = {item["task_id"]: item for item in instances["instances"]}

    for task in adapter["tasks"]:
        task_id = task["task_id"]
        criterion = criteria_by_id[task_id]
        instance = instances_by_id[task_id]
        assert task["robot_configuration_id"] == robot
        assert task["source_scene_entrypoint"] == instance["scene_entrypoint"]
        assert task["scene_entrypoint"] == _cache_scene_path(robot, instance["scene_entrypoint"])
        assert task["reset"] == instance["reset"]
        assert task["parameters"] == instance["parameters"]
        assert len(task["measurements"]) == len(criterion["checks"])
        assert {
            item["metric"] for item in task["measurements"]
        } == {item["metric"] for item in criterion["checks"]}

        local_scene = ROOT.parent / task["source_scene_entrypoint"]
        assert local_scene.is_file()
        scene_names = _scene_names(local_scene)
        for name in task["frames"]["body_names"]:
            assert name in scene_names["body"]
        for name in task["frames"]["site_names"]:
            assert name in scene_names["site"]

        for measurement in task["measurements"]:
            if measurement.get("unresolved") is True:
                assert measurement["status"] == "UNRESOLVED"
                assert measurement["operator"]
                assert measurement["reason"]
                continue
            assert measurement["status"] == "RESOLVED"
            assert measurement["operator"] in RESOLVED_OPERATORS
            observation_path = measurement.get("observation_path")
            if measurement["operator"] in {
                "contact_count",
                "contact_boolean",
                "contact_to_bool",
                "boolean_contact",
                "invocation_count",
                "attempt_count",
                "action_count",
            }:
                if measurement["operator"].startswith("contact") or measurement["operator"] == "boolean_contact":
                    assert measurement.get("contact_groups") or measurement.get("contact_body_names") or measurement.get("contact_geom_names") or measurement.get("contact_geom_ids")
                continue
            assert isinstance(observation_path, str)
            root_name, object_name, *_ = observation_path.split(".")
            if root_name == "bodies":
                assert object_name in scene_names["body"]
            elif root_name == "sites":
                assert object_name in scene_names["site"]
            elif root_name == "joint_positions":
                assert object_name in scene_names["joint"]
            else:
                raise AssertionError(f"unsupported public observation root: {observation_path}")

            for key in ("reference_path", "reference_offset_path"):
                if key in measurement:
                    assert isinstance(measurement[key], str)
                    assert measurement[key].startswith("parameters.")
                    value = _path_value({"parameters": task["parameters"]}, measurement[key])
                    assert value is not None
            for key in ("waypoints_path", "side_path", "offset_path"):
                if key in measurement:
                    assert isinstance(measurement[key], str)
                    assert measurement[key].startswith("parameters.")
                    assert _path_value({"parameters": task["parameters"]}, measurement[key]) is not None
            reference = measurement.get("reference")
            if isinstance(reference, dict) and "observation_path" in reference:
                ref_root, ref_name, *_ = reference["observation_path"].split(".")
                assert ref_root in {"bodies", "sites"}
                assert ref_name in scene_names["body" if ref_root == "bodies" else "site"]
            if isinstance(reference, dict) and "value" in reference:
                assert all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in reference["value"]
                )


@pytest.mark.parametrize("robot", ROBOTS)
def test_resolved_direct_task_adapters_load_and_unresolved_metrics_fail_closed(robot: str) -> None:
    adapter = _adapter(robot)
    for task in adapter["tasks"]:
        task_id = task["task_id"]
        unresolved = any(item.get("unresolved") is True for item in task["measurements"])
        if unresolved:
            with pytest.raises(
                DirectMuJoCoConfigurationError,
                match=f"direct task {task_id} has unresolved measurement mappings",
            ):
                _load_for_execution(adapter, task_id)
        else:
            loaded = _load_for_execution(adapter, task_id)
            assert loaded.task_id == task_id
            assert loaded.scene_entrypoint == task["scene_entrypoint"]
            assert loaded.parameters == task["parameters"]
            assert loaded.reset == task["reset"]
