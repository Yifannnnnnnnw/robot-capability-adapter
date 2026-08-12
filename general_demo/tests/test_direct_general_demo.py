from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.demo import DemoTask, EvaluationRoute
from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.generation import FixtureJsonGenerator, Stage1Config
from autoadapter2.integrations.direct_mujoco import DirectMuJoCoLibraryConfig
from autoadapter2.libraries import TasksLibrary
from autoadapter2.orchestration.direct_general_demo import (
    DIRECT_MUJOCO_EXPERIMENTAL,
    DirectGeneralDemoResolutionError,
    DirectGeneralDemoSession,
    load_direct_task_adapter,
)
from autoadapter2.orchestration.demo_runner import DemoModelAdapters, DemoRunPlan, GeneralDemoRunner


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _lifecycle_config(tmp_path: Path) -> DirectMuJoCoLibraryConfig:
    return DirectMuJoCoLibraryConfig.from_record(
        {
            "record_type": "morphology",
            "id": "lifecycle-franka",
            "version": "1.0.0",
            "robot_model_id": "franka_panda",
            "robot_configuration_id": "franka_panda",
            "joint_names": ["joint"],
            "actuator_names": ["actuator"],
            "sensor_names": [],
            "mujoco": {
                "entrypoint": "assets/robot.xml",
                "reset": {"qpos": [0.0], "qvel": [0.0]},
                "frames": {"body_names": ["base"], "site_names": []},
                "render": {"camera": "free", "width": 16, "height": 12, "fps": 10},
            },
        },
        asset_root=tmp_path,
    )


class _LifecycleSession:
    def __init__(self, config: DirectMuJoCoLibraryConfig) -> None:
        self.config = config
        self.sdk = object()
        self.simulation_time_s = 0.0
        self.evidence_scope = DIRECT_MUJOCO_EXPERIMENTAL
        self.robot_model_id = config.robot_model_id
        self.robot_configuration_id = config.robot_configuration_id
        self.reset_calls: list[dict[str, Any]] = []
        self.closed = False

    def reset(self, *, phase: str, execution_id: str, initial_state: dict[str, Any]) -> None:
        self.reset_calls.append({
            "phase": phase,
            "execution_id": execution_id,
            "initial_state": dict(initial_state),
        })

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        del phase, execution_id

    def stop_external_recording(self) -> tuple[Any, ...]:
        return ()

    def validation_evidence(self, invocation: Any) -> Any:
        return invocation

    def demo_evidence(self, task_id: str) -> dict[str, str]:
        return {"task_id": task_id}

    def invoke(self, candidate: Any, capability_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"candidate": candidate, "capability_id": capability_id, "arguments": dict(arguments)}

    def close(self) -> None:
        self.closed = True


def _lifecycle_plan() -> DemoRunPlan:
    package = TasksLibrary(PROJECT_ROOT / "general_demo/libraries/tasks").load(
        "franka_panda", "1.0.0"
    )
    public_tasks = package.demo_public_tasks("direct-lifecycle")
    private_criteria = package.demo_private_criteria()
    tasks = tuple(
        DemoTask(
            requirement_id=public["requirement_id"],
            task_id=public["task_id"],
            description=public["description"],
            public_state={},
            private_criterion=criterion,
        )
        for public, criterion in zip(public_tasks, private_criteria, strict=True)
    )
    digest = lambda letter: f"sha256:{letter * 64}"
    return DemoRunPlan(
        run_id="direct-lifecycle",
        integration_manifest_path=None,
        run_snapshot_path=None,
        readiness_report_path=None,
        robot_public_projection={
            "robot_model_id": "franka_panda",
            "robot_configuration_id": "franka_panda",
            "execution_route": DIRECT_MUJOCO_EXPERIMENTAL,
            "action_affordances": ["named actuator command"],
            "observation_affordances": ["body state"],
            "unit_allowlist": ["rad", "native_mujoco_control"],
            "frame_allowlist": ["body"],
        },
        g2_profile={"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"},
        tasks=tasks,
        standards_snapshot={},
        measurement_catalog={},
        blue_line_policy={},
        implementation_bundle={},
        validation_a_profile=None,
        public_state_schema={},
        validation_harness_config={},
        task_catalog_version="1.0.0",
        stage1_config=Stage1Config(max_correction_calls=0),
        execution_route=DIRECT_MUJOCO_EXPERIMENTAL,
        evaluation_route=EvaluationRoute(
            run_id="direct-lifecycle",
            integration_manifest_hash=digest("a"),
            sdk_entry_hash=digest("b"),
            runtime_hash=digest("c"),
            simulation_profile_hash=digest("d"),
        ),
        route_metadata={
            "robot_model_id": "franka_panda",
            "robot_configuration_id": "franka_panda",
        },
    )


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


def test_direct_entry_reaches_stage1_before_task_reset_and_later_binds_exact_task(
    tmp_path: Path,
) -> None:
    _write_adapter(tmp_path, _adapter_value())
    adapter = load_direct_task_adapter(tmp_path, "generic")
    base_config = _lifecycle_config(tmp_path)
    created: list[_LifecycleSession] = []

    def session_factory(config: DirectMuJoCoLibraryConfig) -> _LifecycleSession:
        session = _LifecycleSession(config)
        created.append(session)
        return session

    session = DirectGeneralDemoSession(
        base_config,
        adapter,
        video_profile=FrozenVideoProfile("direct-test", "1.0.0", "free", "test", 10, 16, 12, "raw", "rgb"),
        session_factory=session_factory,
    )
    development_sdk = session.sdk
    assert development_sdk is created[0].sdk
    assert created[0].config.bound_task is None
    assert created[0].reset_calls == []
    stage1_models = FixtureJsonGenerator([{}])
    models = DemoModelAdapters(
        stage1=stage1_models,
        blue_line=FixtureJsonGenerator([{}]),
        stage2=FixtureJsonGenerator([{}]),
        repair=lambda _request: {},
        consumer=lambda _inputs: {},
    )

    result = GeneralDemoRunner(
        PROJECT_ROOT,
        models,
        session,
        FrozenVideoProfile("direct-test", "1.0.0", "free", "test", 10, 16, 12, "raw", "rgb"),
        lambda *_args: None,
        lambda *_args: True,
    ).run(_lifecycle_plan())

    assert result.status == "STAGE1_FAILED"
    assert len(stage1_models.calls) == 1
    assert session.task_id is None
    assert len(created) == 1
    assert created[0].config.bound_task is None
    assert created[0].reset_calls == []

    with pytest.raises(DirectGeneralDemoResolutionError, match="requires initial_state.task_id"):
        session.reset(phase="VALIDATION_B", execution_id="ambiguous", initial_state={})
    assert len(created) == 1
    assert session.task_id is None

    session.reset(
        phase="VALIDATION_B",
        execution_id="task-t3",
        initial_state={"task_id": "T3"},
    )
    assert created[0].closed is True
    assert len(created) == 2
    assert created[1].config.bound_task is not None
    assert created[1].config.bound_task.task_id == "T3"
    assert created[1].reset_calls == [{
        "phase": "VALIDATION_B",
        "execution_id": "task-t3",
        "initial_state": {"task_id": "T3"},
    }]
    evidence_marker = object()
    assert session.validation_evidence(evidence_marker) is evidence_marker
    session.close()
