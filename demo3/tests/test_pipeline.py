from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2 import __main__ as cli
from autoadapter2.driver_synthesis.generation import (
    DriverSourceAuditError,
    ModelCallEvidence,
    StudyResult,
)
from autoadapter2.libraries import RobotPackageError
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineError,
    PipelineHooks,
    _public_experience,
    render_reference_driver,
    run_experiment,
    success_claim,
)
from autoadapter2.driver_synthesis.repair import repair_with_probes


def _config() -> ExperimentConfig:
    return ExperimentConfig.from_mapping(
        {
            "experiment_id": "pipeline-test",
            "robots": ["r-arm", "r-quad"],
            "generation_conditions": ["skeleton-assisted", "from-scratch"],
            "max_driver_attempts_per_condition": 3,
            "development_probe": {
                "max_requests_per_stage": 2,
                "wall_timeout_s_per_request": 1,
                "max_output_chars_per_request": 1000,
            },
            "validation": {"record_video": True, "worker_wall_timeout_s": 2},
        }
    )


def _package(root: Path, robot: str) -> Any:
    package_root = root / robot
    package_root.mkdir(parents=True, exist_ok=True)
    assets = package_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "scene.xml").write_text(
        "<mujoco><worldbody><body name=\"body\"/></worldbody></mujoco>\n",
        encoding="utf-8",
    )
    skeleton = package_root / "skeleton"
    skeleton.mkdir(parents=True, exist_ok=True)
    (skeleton / "fixture.py").write_text("class FixtureSkeleton: pass\n", encoding="utf-8")
    return SimpleNamespace(
        root=package_root,
        robot_configuration_id=robot,
        package_version="1.0.0",
        snapshot_id=f"{robot}-tasks",
        morphology={"robot_configuration_id": robot},
        sources=({"source_id": "source"},),
        tasks=tuple({"task_id": f"task-{index}"} for index in range(20)),
        skeleton_dir=package_root / "skeleton",
        reference_driver=package_root / "reference" / "driver.py",
        private_dir=package_root / "tasks" / "private",
        mjcf_path=package_root / "assets" / "scene.xml",
    )


def _design(robot: str) -> dict[str, Any]:
    return {
        "artifact_type": "capability_design",
        "robot_configuration_id": robot,
        "capabilities": [
            {"capability_id": "cap-0", "method_name": "move_effect"}
        ],
    }


def _fake_hooks(
    tmp_path: Path,
    events: list[tuple[Any, ...]],
    *,
    validation_pass_at: int | None = 3,
    reference_pass: bool = True,
    evolution_raises: bool = False,
    reject_initial_source: bool = False,
    fail_model_probe: bool = False,
    study_raises: bool = False,
    in_conversation_study_probe: bool = False,
) -> tuple[PipelineHooks, dict[str, Any]]:
    packages = {robot: _package(tmp_path, robot) for robot in ("r-arm", "r-quad")}
    suites: dict[str, dict[str, Any]] = {}
    harness_inputs: list[dict[str, Any]] = []
    reference_inputs: list[dict[str, Any]] = []
    repair_calls: list[tuple[str, int]] = []
    client = SimpleNamespace(calls=[])

    def load(root: Path, robot: str) -> Any:
        events.append(("load", robot))
        return packages[robot]

    def tgcd(model: Any, package: Any, *, experience: Any) -> dict[str, Any]:
        events.append(("tgcd", package.robot_configuration_id))
        return _design(package.robot_configuration_id)

    def ivc(model: Any, *, package: Any, design: Any) -> dict[str, Any]:
        events.append(("ivc", package.robot_configuration_id))
        suite = {
            "artifact_type": "private_validation_suite",
            "robot_configuration_id": package.robot_configuration_id,
            "whole_suite_aggregation": {"kind": "all_cases"},
            "cases": [
                {"case_id": f"{package.robot_configuration_id}-case-{index}"}
                for index in range(8)
            ],
        }
        suites[package.robot_configuration_id] = suite
        return suite

    def render(design: Any, destination: Path) -> Path:
        events.append(("render_reference", destination.parent.name))
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "driver.py"
        path.write_text("# reference fixture\n", encoding="utf-8")
        return path

    def reference_runner(**kwargs: Any) -> dict[str, Any]:
        robot = kwargs["package"].robot_configuration_id
        events.append(("reference", robot))
        reference_inputs.append({"robot": robot, "suite": kwargs["suite"]})
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": reference_pass,
            "video_complete": True,
            "trials": [],
        }

    def study_runner(model: Any, package: Any, design: Any, **kwargs: Any) -> StudyResult:
        events.append(("study", package.robot_configuration_id, kwargs["condition"]))
        if study_raises:
            raise RuntimeError("model output failed STUDY validation")
        return StudyResult(
            condition=kwargs["condition"],
            output={
                "condition": kwargs["condition"],
                "findings": [],
                "implementation_plan": [],
                "skeleton_inspection": {},
                "probe_requests": [
                    {"probe_id": "canonical-step", "script": "import mujoco"}
                ],
            },
            probe_requests=(
                {"probe_id": "canonical-step", "script": "import mujoco"},
            ),
            call_evidence=ModelCallEvidence(
                stage="study", prompt="test", inputs={}, output={}
            ),
            probe_results=(
                (
                    {
                        "probe_id": "canonical-step",
                        "exit_code": 0,
                        "timed_out": False,
                        "spawn_error": None,
                        "physics_steps": 1,
                    },
                )
                if in_conversation_study_probe
                else ()
            ),
        )

    def probe_runner(requests: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
        events.append(
            ("probe", kwargs["package"].robot_configuration_id, kwargs["condition"])
        )
        probe_id = str(requests[0]["probe_id"])
        if fail_model_probe and probe_id != "framework-canonical-liveness":
            return (
                {
                    "probe_id": probe_id,
                    "exit_code": 1,
                    "timed_out": False,
                    "spawn_error": None,
                    "physics_steps": 0,
                    "stderr": "diagnostic failed before stepping",
                },
            )
        return (
            {
                "probe_id": probe_id,
                "exit_code": 0,
                "timed_out": False,
                "spawn_error": None,
                "physics_steps": 1,
            },
        )

    def _generated_driver(workspace: Path, attempt: int) -> Any:
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "driver.py"
        path.write_text("def build(*, model, data):\n    return object()\n", encoding="utf-8")
        return SimpleNamespace(
            driver_path=path,
            driver_source=path.read_text(encoding="utf-8"),
            output={"attempt": attempt},
            source_audit=None,
        )

    def generate_runner(model: Any, package: Any, design: Any, study: Any, **kwargs: Any) -> Any:
        events.append(("generate", package.robot_configuration_id, kwargs["condition"], 0))
        if reject_initial_source:
            source = "def build(*, model, data):\n    return getattr(data, 'qpos')\n"
            raise DriverSourceAuditError(
                "generated driver failed source audit: getattr is forbidden",
                driver_source=source,
                model_output={
                    "driver_filename": "driver.py",
                    "driver_source": source,
                },
            )
        return _generated_driver(Path(kwargs["workspace"]), 0)

    def repair_runner(model: Any, **kwargs: Any) -> Any:
        previous_attempt = int(kwargs["previous_attempt"])
        condition = kwargs["condition"]
        repair_calls.append((condition, previous_attempt))
        events.append(("repair", condition, previous_attempt))
        return _generated_driver(Path(kwargs["workspace"]), previous_attempt + 1)

    def harness_runner(**kwargs: Any) -> dict[str, Any]:
        package = kwargs["package"]
        condition = kwargs["condition"]
        attempt = int(kwargs["attempt"])
        events.append(("harness", package.robot_configuration_id, condition, attempt))
        harness_inputs.append(
            {
                "robot": package.robot_configuration_id,
                "condition": condition,
                "attempt": attempt,
                "workspace": str(kwargs["driver_path"].parent),
                "suite": kwargs["suite"],
            }
        )
        passed = validation_pass_at is not None and attempt >= validation_pass_at - 1
        case_count = len(kwargs["suite"]["cases"])
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": passed,
            "video_complete": True,
            "passed_task_count": case_count if passed else 0,
            "task_count": case_count,
            "passed_source_clause_count": case_count if passed else 0,
            "source_clause_count": case_count,
            "passed_private_case_count": case_count if passed else 0,
            "private_case_count": case_count,
            "trials": [],
            "video_manifest": [],
        }

    def evolution(model: Any, report: Any) -> dict[str, Any]:
        events.append(("evolution", report["robot_configuration_id"], report["condition"]))
        if evolution_raises:
            raise RuntimeError("evolution fixture failed")
        return {"non_blocking": True, "evolution_completed": True, "proposal": None}

    hooks = PipelineHooks(
        package_loader=load,
        tgcd_runner=tgcd,
        ivc_runner=ivc,
        study_runner=study_runner,
        probe_runner=probe_runner,
        generate_runner=generate_runner,
        repair_runner=repair_runner,
        harness_runner=harness_runner,
        reference_renderer=render,
        reference_runner=reference_runner,
        evolution_runner=evolution,
    )
    return hooks, {
        "client": client,
        "suites": suites,
        "harness_inputs": harness_inputs,
        "reference_inputs": reference_inputs,
        "repair_calls": repair_calls,
    }


def test_pipeline_orders_ivc_and_reference_gate_before_dynamic_cells(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="ordering",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert success_claim(result)
    assert [item[0] for item in events[:4]] == ["load", "load", "tgcd", "ivc"]
    last_reference = max(index for index, item in enumerate(events) if item[0] == "reference")
    first_study = min(index for index, item in enumerate(events) if item[0] == "study")
    assert last_reference < first_study
    for robot in ("r-arm", "r-quad"):
        assert events.index(("ivc", robot)) < first_study


def test_conditions_are_isolated_and_share_only_the_sealed_suite(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="isolation",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert result["paired_report"]["summary"]["reported_cell_count"] == 4
    for robot in ("r-arm", "r-quad"):
        inputs = [item for item in state["harness_inputs"] if item["robot"] == robot]
        assert {item["condition"] for item in inputs} == {
            "skeleton-assisted",
            "from-scratch",
        }
        assert len({item["workspace"] for item in inputs}) == 2
        assert inputs[0]["suite"] == inputs[1]["suite"]
        assert len(inputs[0]["suite"]["cases"]) == 5
        reference = next(
            item for item in state["reference_inputs"] if item["robot"] == robot
        )
        assert reference["suite"] == inputs[0]["suite"]
        assert len(state["suites"][robot]["cases"]) == 8
        private_dir = tmp_path / "run" / "private" / robot
        pool = json.loads(
            (private_dir / "private_case_pool.json").read_text(encoding="utf-8")
        )
        assert len(pool["cases"]) == 8
        selected = json.loads(
            (private_dir / "private_validation_suite.json").read_text(encoding="utf-8")
        )
        assert len(selected["cases"]) == 5
        assert selected["selection"]["selected_case_count"] == 5
    private_root = (tmp_path / "run" / "private").resolve()
    for item in state["harness_inputs"]:
        workspace = Path(item["workspace"]).resolve()
        assert private_root not in workspace.parents
        assert not (workspace / "private_validation_suite.json").exists()
        assert not any(workspace.rglob("private_validation_suite.json"))


def test_driver_attempts_are_capped_at_three_and_evolution_is_nonblocking(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=None,
        evolution_raises=True,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="bounded",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert result["pipeline_completed"] is True
    assert result["success"] is False
    assert all(cell["attempt_count"] == 3 for cell in result["cells"])
    assert len([item for item in events if item[0] == "harness"]) == 12
    assert len(state["repair_calls"]) == 8
    assert all(
        cell["outcomes"]["Evolution"] is None
        or cell["outcomes"]["Evolution"].get("non_blocking") is True
        for cell in result["cells"]
    )


def test_source_audit_rejection_does_not_consume_a_formal_attempt(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=2,
        reject_initial_source=True,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="source-repair",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert not success_claim(result)
    assert all(cell["attempt_count"] == 0 for cell in result["cells"])
    assert not any(item[0] == "harness" for item in events)
    assert len(state["repair_calls"]) == 0
    for cell in result["cells"]:
        rejected = cell["development_rejections"][0]
        assert rejected["formal_attempt_submitted"] is False
        assert rejected["source_audit_passed"] is False
        assert cell["physical_validation_executed"] is False
        assert cell["final_validation_passed"] is False


def test_pipeline_does_not_rerun_an_in_conversation_study_probe(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=1,
        in_conversation_study_probe=True,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="interactive-study-probe",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert success_claim(result)
    assert not any(item[0] == "probe" for item in events)
    for cell in result["cells"]:
        assert cell["development_probe"]["successful_physics_probe"] is True
        assert len(cell["development_probe"]["results"]) == 1


def test_failed_model_probe_keeps_diagnostics_and_uses_canonical_liveness(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=1,
        fail_model_probe=True,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="probe-fallback",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert success_claim(result)
    assert len([item for item in events if item[0] == "probe"]) == 8
    for cell in result["cells"]:
        probes = cell["development_probe"]["results"]
        assert probes[0]["exit_code"] == 1
        assert probes[0]["stderr"] == "diagnostic failed before stepping"
        assert probes[1]["framework_canonical_liveness"] is True
        assert probes[1]["physics_steps"] == 1
        assert cell["development_probe"]["successful_physics_probe"] is True


def test_failed_study_still_records_that_the_dynamic_model_was_called(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, study_raises=True)
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="study-failure",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert all(cell["dynamic_model_called"] is True for cell in result["cells"])
    assert all(cell["driver_generated_in_run"] is False for cell in result["cells"])


def test_failed_reference_gate_starts_no_dynamic_cell(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, reference_pass=False)
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="reference-fail",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert result["reference_calibration_passed"] is False
    assert result["cells"] == []
    assert not any(item[0] == "study" for item in events)


def test_package_failure_happens_before_model_calls(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    client = SimpleNamespace(calls=[])

    def load(root: Path, robot: str) -> Any:
        events.append(("load", robot))
        raise RobotPackageError("incomplete package")

    hooks = PipelineHooks(package_loader=load)
    with pytest.raises(PipelineError, match="failed closed"):
        run_experiment(
            tmp_path,
            config=_config(),
            output_dir=tmp_path / "run",
            run_id="package-fail",
            client=client,
            hooks=hooks,
            check_self_containment=False,
        )
    assert client.calls == []
    assert events == [("load", "r-arm")]


def test_default_repair_hook_is_probe_aware_and_root_rendering_is_discoverable(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path, "r-arm")
    (package.root / "rendering.py").write_text(
        "def render_reference_driver(design, destination):\n"
        "    path = destination / 'driver.py'\n"
        "    path.write_text('# generated reference\\n')\n"
        "    return path\n",
        encoding="utf-8",
    )
    rendered = render_reference_driver(
        package,
        _design("r-arm"),
        tmp_path / "reference-workspace",
    )
    assert rendered.is_file()
    assert PipelineHooks().repair_runner is repair_with_probes


def test_full_cli_returns_nonzero_for_completed_failed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "experiment_id": "cli-test",
        "robots": ["r-arm", "r-quad"],
        "generation_conditions": ["skeleton-assisted", "from-scratch"],
    }
    (tmp_path / "experiment.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    monkeypatch.setattr(
        cli,
        "run_experiment",
        lambda *args, **kwargs: {
            "experiment_id": "cli-test",
            "run_id": "cli-run",
            "pipeline_completed": True,
            "reference_calibration_passed": True,
            "dynamic_model_called": True,
            "driver_generated_in_run": True,
            "physical_validation_executed": True,
            "initial_validation_passed": False,
            "final_validation_passed": False,
            "success": False,
            "claim": "paired experiment completed; a cell failed",
            "cells": [],
        },
    )
    assert cli.main(["full", "--root", str(tmp_path)]) == 1


def test_experience_ingress_accepts_only_reviewed_public_records() -> None:
    valid = {
        "r-arm": [
            {
                "experience_id": "exp-1",
                "reviewed": True,
                "observation": "A public controller saturated its actuator command.",
                "lesson": "Clip commands to the public actuator range.",
                "recommendation": "Inspect morphology actuator limits before control.",
                "scope": "serial arm control",
                "evidence": ["public run summary"],
                "source_run_id": "run-1",
            }
        ]
    }
    assert len(_public_experience(valid, "r-arm")) == 1

    for leaked in (
        {"reviewed": True, "reference_driver_source": "sentinel"},
        {"reviewed": True, "private_report": "sentinel"},
        {"reviewed": True, "path": "/tmp/private"},
        {"reviewed": False, "observation": "not admitted"},
    ):
        with pytest.raises(PipelineError):
            _public_experience({"r-arm": [leaked]}, "r-arm")
