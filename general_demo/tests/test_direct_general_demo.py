from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoadapter2.orchestration.direct_general_demo import (
    DirectGeneralDemoResolutionError,
    load_direct_task_adapter,
)


def _task(task_id: str, metric: str = "state_error") -> dict[str, object]:
    return {
        "task_id": task_id,
        "scene_entrypoint": "assets/scene.xml",
        "reset": {"mode": "source_scene_default"},
        "parameters": {"target": task_id},
        "frames": {"body_names": ["base"], "site_names": [], "sensor_names": []},
        "measurements": [{
            "task_id": task_id,
            "metric": metric,
            "observation_path": "bodies.base.position",
        }],
    }


def _write_adapter(root: Path, value: dict[str, object]) -> Path:
    path = root / "general_demo/libraries/tasks/generic/1.0.0/direct_mujoco_adapter.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _adapter_value() -> dict[str, object]:
    return {
        "artifact_type": "direct_mujoco_task_adapter",
        "schema_version": "1.0.0",
        "robot_configuration_id": "generic",
        "execution_route": "DIRECT_MUJOCO_EXPERIMENTAL",
        "tasks": [_task(f"T{index}") for index in range(1, 6)],
    }


def test_loader_consumes_generic_five_task_adapter_shape(tmp_path: Path) -> None:
    _write_adapter(tmp_path, _adapter_value())

    loaded = load_direct_task_adapter(tmp_path, "generic")

    assert [task.task_id for task in loaded.tasks] == ["T1", "T2", "T3", "T4", "T5"]
    assert loaded.by_task_id["T3"].parameters == {"target": "T3"}
    assert loaded.by_task_id["T3"].measurement_declarations[0]["metric"] == "state_error"
    assert loaded.content_hash.startswith("sha256:")


def test_loader_fails_clearly_when_metric_mapping_is_missing(tmp_path: Path) -> None:
    value = _adapter_value()
    tasks = value["tasks"]
    assert isinstance(tasks, list)
    broken = _task("T3")
    broken["measurements"] = [{}]
    tasks[2] = broken
    _write_adapter(tmp_path, value)

    with pytest.raises(DirectGeneralDemoResolutionError, match="missing metric"):
        load_direct_task_adapter(tmp_path, "generic")


def test_loader_reports_missing_adapter_at_the_exact_library_path(tmp_path: Path) -> None:
    (tmp_path / "general_demo/libraries/tasks").mkdir(parents=True)

    with pytest.raises(DirectGeneralDemoResolutionError, match="regular file"):
        load_direct_task_adapter(tmp_path, "generic")
