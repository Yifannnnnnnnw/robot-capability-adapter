from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2 import __main__ as cli
from autoadapter2.driver_synthesis.generation import (
    DriverSourceAuditError,
    GenerationError,
    ModelCallEvidence,
    StudyResult,
)
from autoadapter2.libraries import RobotPackageError
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineError,
    PipelineHooks,
    _validate_model_preflight,
    _stage_evidence,
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


def test_config_accepts_a_single_robot_single_condition_canary() -> None:
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "single-cell-canary",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        }
    )

    assert config.robots == ("r-arm",)
    assert config.generation_conditions == ("skeleton-assisted",)


def test_config_can_disable_task_demo_for_a_pre_recap_diagnostic() -> None:
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "pre-recap-diagnostic",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "task_demo": {"enabled": False},
        }
    )

    assert config.task_demo_enabled is False
    assert config.as_dict()["task_demo"] == {"enabled": False}

    with pytest.raises(PipelineError, match="task_demo.enabled must be boolean"):
        ExperimentConfig.from_mapping(
            {
                "experiment_id": "invalid-pre-recap-diagnostic",
                "robots": ["r-arm"],
                "generation_conditions": ["skeleton-assisted"],
                "task_demo": {"enabled": "false"},
            }
        )


def test_mainline_manifest_pins_model_empty_experience_and_seed_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    config = ExperimentConfig.from_path(root / "configs" / "experiments" / "mainline.json")

    assert config.model_manifest == {
        "vendor": "mistral",
        "api_protocol": "openai-compatible",
        "model_id": "ministral-8b-2512",
        "revision": "25.12",
        "base_url": "https://api.mistral.ai/v1",
        "context_window_tokens": 262144,
        "max_output_tokens": 16384,
        "temperature": 0.0,
        "thinking": "disabled",
        "tool_history_mode": "native",
        "price_snapshot": {
            "date": "2026-08-20",
            "currency": "EUR",
            "input_per_million_tokens": 0.13,
            "output_per_million_tokens": 0.13,
        },
    }
    assert config.experience_declared
    assert config.experience_input == ()
    assert config.evolution_declared
    assert config.task_demo_seed_template == "{run_id}:{robot_configuration_id}"
    assert len(config.robots) == 11
    assert "unitree_g1" not in config.robots
    assert len(config.robots) * len(config.generation_conditions) == 22


def test_formal_robot_canaries_mirror_mainline_manifest_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    config_dir = root / "configs" / "experiments"
    mainline = json.loads((config_dir / "mainline.json").read_text(encoding="utf-8"))
    expected_robots = {
        "robotstudio_so101",
        "unitree-go2-stock-12dof",
        "franka_panda",
        "kinova_gen3_robotiq_2f85",
        "ufactory_xarm7",
        "universal_robots_ur5e_robotiq_2f85",
        "piper",
        "kuka_iiwa_14",
        "leap_hand",
        "hello_robot_stretch_2",
        "aloha_2",
    }
    canary_paths = sorted(config_dir.glob("*-canary.json"))
    canaries = [
        json.loads(path.read_text(encoding="utf-8")) for path in canary_paths
    ]
    canary_robots = [canary["robots"][0] for canary in canaries]

    assert len(canaries) == len(expected_robots)
    assert set(canary_robots) == expected_robots
    assert len(canary_robots) == len(set(canary_robots))
    assert {"unitree_g1", "google_barkour_vb"}.isdisjoint(canary_robots)
    for path, canary in zip(canary_paths, canaries):
        assert len(canary["robots"]) == 1, path.name
        assert canary["generation_conditions"] == ["skeleton-assisted"], path.name
        for key in ("model", "experience", "evolution", "seeds"):
            assert canary[key] == mainline[key], path.name


def test_model_preflight_rejects_a_runtime_model_substitution() -> None:
    manifest = ExperimentConfig.from_mapping(
        {
            "experiment_id": "model-preflight",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "model": {
                "vendor": "mistral",
                "api_protocol": "openai-compatible",
                "model_id": "ministral-8b-2512",
                "revision": "25.12",
                "base_url": "https://api.mistral.ai/v1",
                "context_window_tokens": 262144,
                "max_output_tokens": 16384,
                "temperature": 0.0,
                "thinking": "disabled",
                "tool_history_mode": "native",
                "price_snapshot": {
                    "date": "2026-08-20",
                    "currency": "EUR",
                    "input_per_million_tokens": 0.13,
                    "output_per_million_tokens": 0.13,
                },
            },
        }
    ).model_manifest
    assert manifest is not None
    matching = SimpleNamespace(
        config=SimpleNamespace(
            provider="mistral",
            api_protocol="openai-compatible",
            model="ministral-8b-2512",
            base_url="https://api.mistral.ai/v1/",
            max_tokens=16384,
            thinking="disabled",
            tool_history_mode="native",
        )
    )
    assert _validate_model_preflight(matching, manifest)["matched"] is True

    matching.config.model = "deepseek-v4-pro"
    with pytest.raises(PipelineError, match="model_id"):
        _validate_model_preflight(matching, manifest)


def test_declared_empty_experience_rejects_same_round_input(tmp_path: Path) -> None:
    config = {
        "experiment_id": "empty-experience",
        "robots": ["r-arm"],
        "generation_conditions": ["skeleton-assisted"],
        "experience": {
            "input": [],
            "review_queue_output": "experience_review_queue.json",
            "snapshot_output": "experience_snapshot.json",
        },
    }
    with pytest.raises(PipelineError, match="requires empty Experience"):
        run_experiment(
            tmp_path,
            config=config,
            experience={"r-arm": [{"lesson": "same-round leak"}]},
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("robots", [], "robots must be a non-empty list"),
        (
            "generation_conditions",
            [],
            "generation_conditions must be a non-empty list",
        ),
        (
            "generation_conditions",
            ["skeleton-assisted", "unknown"],
            "unsupported generation_conditions",
        ),
    ),
)
def test_config_rejects_empty_or_unsupported_matrix_values(
    field: str, value: list[str], message: str
) -> None:
    raw = {
        "experiment_id": "invalid-config",
        "robots": ["r-arm"],
        "generation_conditions": ["skeleton-assisted"],
    }
    raw[field] = value

    with pytest.raises(PipelineError, match=message):
        ExperimentConfig.from_mapping(raw)


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
    tasks = tuple(
        {
            "task_id": f"task-{index}",
            "scoring": [
                {
                    "clause_id": "terminal",
                    "metric": "terminal_error",
                    "unit": "m",
                    "comparator": "<=",
                    "threshold": 0.1,
                    "temporal": {"kind": "terminal"},
                    "aggregation": {"kind": "single_trial"},
                    "source_refs": [{"source_id": "source"}],
                }
            ],
        }
        for index in range(20)
    )
    private = package_root / "tasks" / "private"
    private.mkdir(parents=True, exist_ok=True)
    (private / "instances.json").write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": f"instance-{index}",
                        "task_id": f"task-{index}",
                        "clause_bindings": {"terminal": "binding"},
                        "guard_ids": ["guard"],
                        "repetitions": 1,
                        "timeout_sim_s": 1.0,
                    }
                    for index in range(20)
                ]
            }
        ),
        encoding="utf-8",
    )
    (private / "bindings.json").write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "binding_id": "binding",
                        "metric": "terminal_error",
                        "unit": "m",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (private / "guards.json").write_text(
        json.dumps({"guards": [{"guard_id": "guard"}]}),
        encoding="utf-8",
    )
    return SimpleNamespace(
        root=package_root,
        robot_configuration_id=robot,
        package_version="1.0.0",
        snapshot_id=f"{robot}-tasks",
        morphology={"robot_configuration_id": robot},
        sources=({"source_id": "source"},),
        tasks=tasks,
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
            {
                "capability_id": "cap-0",
                "method_name": "move_effect",
                "covered_task_ids": [f"task-{index}" for index in range(20)],
            }
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
    generation_raises: bool = False,
    in_conversation_study_probe: bool = False,
    task_demo_pass: bool = True,
) -> tuple[PipelineHooks, dict[str, Any]]:
    packages = {robot: _package(tmp_path, robot) for robot in ("r-arm", "r-quad")}
    suites: dict[str, dict[str, Any]] = {}
    harness_inputs: list[dict[str, Any]] = []
    reference_inputs: list[dict[str, Any]] = []
    repair_calls: list[tuple[str, int]] = []
    repair_reports: list[dict[str, Any]] = []
    study_designs: list[Any] = []
    generate_designs: list[Any] = []
    repair_public_inputs: list[dict[str, Any]] = []
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
            "artifact_type": "capability_validation_suite",
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
        study_designs.append(design)
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
        generate_designs.append(design)
        if generation_raises:
            candidate = (
                Path(kwargs["workspace"]) / "generate-development" / "driver.py"
            )
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("# unfinished model-authored candidate\n", encoding="utf-8")
            raise GenerationError(
                "interactive GENERATE did not submit",
                react_trace=({"turn": 40, "event": "final_submission_turn"},),
                probe_results=({"probe_id": "smoke", "physics_steps": 1},),
                candidate_path=candidate,
                model_turns=40,
                tool_calls=30,
            )
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
        repair_reports.append(dict(kwargs["candidate_report"]))
        repair_public_inputs.append(dict(kwargs["public_inputs"]))
        events.append(("repair", condition, previous_attempt))
        return _generated_driver(Path(kwargs["workspace"]), previous_attempt + 1)

    def harness_runner(**kwargs: Any) -> dict[str, Any]:
        package = kwargs["package"]
        condition = kwargs["condition"]
        attempt = int(kwargs["attempt"])
        output_dir = Path(kwargs["output_dir"])
        role = (
            "task_demo" if output_dir.name == "task-demo" else "capability_validation"
        )
        events.append(
            ("harness", package.robot_configuration_id, condition, attempt, role)
        )
        harness_inputs.append(
            {
                "robot": package.robot_configuration_id,
                "condition": condition,
                "attempt": attempt,
                "workspace": str(kwargs["driver_path"].parent),
                "output_dir": str(output_dir),
                "role": role,
                "suite": kwargs["suite"],
                "controller_client": kwargs.get("controller_client"),
            }
        )
        passed = (
            task_demo_pass
            if role == "task_demo"
            else validation_pass_at is not None and attempt >= validation_pass_at - 1
        )
        case_count = len(kwargs["suite"]["cases"])
        trials = [
            {
                "case_id": case["case_id"],
                "task_id": f"task-{index}",
                "source_clause_id": f"clause-{index}",
                "trial_passed": passed,
            }
            for index, case in enumerate(kwargs["suite"]["cases"])
        ]
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
            "trials": trials,
            "video_manifest": [],
        }

    def evolution(model: Any, report: Any) -> dict[str, Any]:
        events.append(("evolution", report["robot_configuration_id"], report["condition"]))
        if evolution_raises:
            raise RuntimeError("evolution fixture failed")
        return {"non_blocking": True, "evolution_completed": True, "proposal": None}

    hooks = PipelineHooks(
        package_loader=load,
        capability_design_validator=lambda design, package: dict(design),
        capability_suite_validator=lambda suite, **kwargs: dict(suite),
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
        "repair_reports": repair_reports,
        "study_designs": study_designs,
        "generate_designs": generate_designs,
        "repair_public_inputs": repair_public_inputs,
    }


def test_pipeline_orders_ivc_and_reference_diagnostics_before_dynamic_cells(
    tmp_path: Path,
) -> None:
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
        skip_reference_calibration=False,
    )

    assert success_claim(result)
    assert [item[0] for item in events[:4]] == ["load", "load", "tgcd", "ivc"]
    last_reference = max(index for index, item in enumerate(events) if item[0] == "reference")
    first_study = min(index for index, item in enumerate(events) if item[0] == "study")
    assert last_reference < first_study
    for robot in ("r-arm", "r-quad"):
        assert events.index(("ivc", robot)) < first_study


def test_pipeline_runs_a_single_robot_single_condition_canary(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "single-cell-canary",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        }
    )

    result = run_experiment(
        tmp_path,
        config=config,
        output_dir=tmp_path / "run",
        run_id="single-cell",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert success_claim(result)
    assert [cell["cell_id"] for cell in result["cells"]] == [
        "r-arm::skeleton-assisted"
    ]
    assert result["upstream_artifact_mode"] == "fresh-per-cell"
    assert result["fixed_input_provenance"] is None
    assert ("tgcd", "r-arm") in events
    assert ("ivc", "r-arm") in events
    assert result["paired_report"]["summary"]["expected_cell_count"] == 1
    assert result["paired_report"]["summary"]["all_expected_cells_reported"]


def test_conditions_share_sealed_capability_and_task_demo_suites(tmp_path: Path) -> None:
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
        capability_inputs = [
            item for item in inputs if item["role"] == "capability_validation"
        ]
        task_demo_inputs = [item for item in inputs if item["role"] == "task_demo"]
        assert {item["condition"] for item in inputs} == {
            "skeleton-assisted",
            "from-scratch",
        }
        assert len(capability_inputs) == 2
        assert len(task_demo_inputs) == 2
        assert len({item["workspace"] for item in inputs}) == 2
        assert capability_inputs[0]["suite"] == capability_inputs[1]["suite"]
        assert len(capability_inputs[0]["suite"]["cases"]) == 8
        assert task_demo_inputs[0]["suite"] == task_demo_inputs[1]["suite"]
        assert len(task_demo_inputs[0]["suite"]["cases"]) == 5
        assert all(
            item["controller_client"] is state["client"]
            for item in task_demo_inputs
        )
        assert all(
            item["controller_client"] is None
            for item in capability_inputs
        )
        reference = next(
            item for item in state["reference_inputs"] if item["robot"] == robot
        )
        assert reference["suite"] == capability_inputs[0]["suite"]
        assert len(state["suites"][robot]["cases"]) == 8
        private_dir = tmp_path / "run" / "private" / robot
        capability = json.loads(
            (private_dir / "capability_validation_suite.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(capability["cases"]) == 8
        task_demo = json.loads(
            (private_dir / "task_demo_suite.json").read_text(encoding="utf-8")
        )
        assert len(task_demo["cases"]) == 5
        assert task_demo["selection"]["selected_case_count"] == 5
    private_root = (tmp_path / "run" / "private").resolve()
    for item in state["harness_inputs"]:
        workspace = Path(item["workspace"]).resolve()
        assert private_root not in workspace.parents
        assert not (workspace / "capability_validation_suite.json").exists()
        assert not (workspace / "task_demo_suite.json").exists()
        assert not any(workspace.rglob("capability_validation_suite.json"))
        assert not any(workspace.rglob("task_demo_suite.json"))


def test_task_demo_runs_only_after_admission_and_never_drives_repair(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=2,
        task_demo_pass=False,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="validation-then-demo",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert success_claim(result)
    assert result["final_capability_validation_passed"] is True
    assert result["task_demo_executed"] is True
    assert result["task_demo_passed"] is False
    assert all(
        cell["outcomes"]["TaskDemoController"]["stage"]
        == "task_demo_controller"
        for cell in result["cells"]
    )
    assert len(state["repair_calls"]) == 4
    assert all(
        report["evaluation_role"] == "capability_validation"
        for report in state["repair_reports"]
    )
    for robot in ("r-arm", "r-quad"):
        for condition in ("skeleton-assisted", "from-scratch"):
            roles = [
                event[4]
                for event in events
                if event[0] == "harness"
                and event[1] == robot
                and event[2] == condition
            ]
            assert roles == [
                "capability_validation",
                "capability_validation",
                "task_demo",
            ]


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
    assert not any(
        item[4] == "task_demo" for item in events if item[0] == "harness"
    )
    assert len(state["repair_calls"]) == 8
    assert all(
        cell["outcomes"]["Evolution"] is None
        or cell["outcomes"]["Evolution"].get("non_blocking") is True
        for cell in result["cells"]
    )


def test_run_writes_deterministic_review_queue_without_changing_driver_success(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    output_dir = tmp_path / "run"
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=output_dir,
        run_id="queue-success",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    queue = json.loads(
        (output_dir / "experience_review_queue.json").read_text(encoding="utf-8")
    )
    assert success_claim(result)
    assert result["success"] is True
    assert result["cell_pipeline_completed"] is True
    assert result["expected_evolution_outcome_count"] == 4
    assert result["retained_evolution_outcome_count"] == 4
    assert result["all_evolution_outcomes_retained"] is True
    assert result["reviewed_disposition_count"] == 0
    assert result["dispositions_complete"] is False
    assert queue["run_id"] == "queue-success"
    assert queue["cell_pipeline_completed"] is True
    assert [record["cell_id"] for record in queue["records"]] == [
        "r-arm::skeleton-assisted",
        "r-arm::from-scratch",
        "r-quad::skeleton-assisted",
        "r-quad::from-scratch",
    ]
    assert queue["expected_evolution_outcome_count"] == 4
    assert queue["retained_evolution_outcome_count"] == 4
    assert queue["all_evolution_outcomes_retained"] is True
    assert queue["reviewed_disposition_count"] == 0
    assert queue["dispositions_complete"] is False
    assert all(
        record["disposition"] is None and record["reason"] is None
        for record in queue["records"]
    )


def test_evolution_failure_is_retained_without_changing_driver_success(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=1,
        evolution_raises=True,
    )
    output_dir = tmp_path / "run"
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=output_dir,
        run_id="queue-evolution-failure",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    queue = json.loads(
        (output_dir / "experience_review_queue.json").read_text(encoding="utf-8")
    )
    assert success_claim(result)
    assert result["success"] is True
    assert queue["retained_evolution_outcome_count"] == 4
    assert queue["all_evolution_outcomes_retained"] is True
    assert all(
        record["evolution"]["evolution_completed"] is False
        and record["evolution"]["failure"]["type"] == "RuntimeError"
        for record in queue["records"]
    )
    assert queue["dispositions_complete"] is False


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
        assert cell["capability_validation_executed"] is False
        assert cell["final_capability_validation_passed"] is False
        assert cell["task_demo_executed"] is False


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


def test_unsubmitted_generation_preserves_trace_and_candidate(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        generation_raises=True,
        in_conversation_study_probe=True,
    )

    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="generation-trace",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )

    assert not any(item[0] == "harness" for item in events)
    for cell in result["cells"]:
        assert cell["attempt_count"] == 0
        assert cell["driver_generated_in_run"] is True
        rejected = cell["development_rejections"][0]
        assert rejected["formal_attempt_submitted"] is False
        assert rejected["candidate_preserved"] is True
        evidence = cell["outcomes"]["GENERATE"]
        assert evidence["react_model_turns"] == 40
        assert evidence["react_tool_calls"] == 30
        assert evidence["react_trace"][-1]["event"] == "final_submission_turn"
        assert evidence["probe_results"][0]["physics_steps"] == 1


def test_stage_evidence_carries_react_failure_details(tmp_path: Path) -> None:
    error = GenerationError(
        "did not submit",
        react_trace=({"turn": 2, "event": "submission_required"},),
        probe_results=({"probe_id": "p", "physics_steps": 1},),
        candidate_path=tmp_path / "driver.py",
        model_turns=2,
        tool_calls=1,
    )

    evidence = _stage_evidence(
        SimpleNamespace(calls=[]),
        stage="generate",
        before=0,
        completed=False,
        error=error,
    )

    assert evidence["react_model_turns"] == 2
    assert evidence["react_tool_calls"] == 1
    assert evidence["react_trace"][0]["event"] == "submission_required"
    assert evidence["probe_results"][0]["physics_steps"] == 1
    assert evidence["candidate_path"].endswith("driver.py")


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

    assert len(result["cells"]) == 4
    assert len([item for item in events if item[0] == "study"]) == 4
    assert not any(item[0] in {"tgcd", "ivc", "generate", "harness"} for item in events)
    assert all(cell["dynamic_model_called"] is True for cell in result["cells"])
    assert all(cell["driver_generated_in_run"] is False for cell in result["cells"])
    assert all(cell["attempt_count"] == 0 for cell in result["cells"])
    assert all(cell["frozen_driver_attempt_count"] == 0 for cell in result["cells"])
    assert all(cell["failure"]["stage"] == "study" for cell in result["cells"])
    assert all(cell["outcomes"]["STUDY"]["completed"] is False for cell in result["cells"])
    for cell in result["cells"]:
        path = (
            tmp_path
            / "run"
            / "cells"
            / cell["robot_configuration_id"]
            / cell["condition"]
            / "cell_report.json"
        )
        assert path.is_file()


@pytest.mark.parametrize("failed_stage", ("tgcd", "ivc"))
def test_failed_pre_driver_cell_does_not_abort_later_cells(
    tmp_path: Path,
    failed_stage: str,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    hook_values = dict(vars(hooks))
    calls = 0

    if failed_stage == "tgcd":
        original_tgcd = hooks.tgcd_runner

        def fail_first_tgcd(
            model: Any,
            package: Any,
            *,
            experience: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("first TGCD artifact is invalid")
            return original_tgcd(model, package, experience=experience)

        hook_values["tgcd_runner"] = fail_first_tgcd
    else:
        original_ivc = hooks.ivc_runner

        def fail_first_ivc(
            model: Any,
            *,
            package: Any,
            design: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("first IVC artifact is invalid")
            return original_ivc(model, package=package, design=design)

        hook_values["ivc_runner"] = fail_first_ivc

    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id=f"{failed_stage}-failure-continues",
        client=state["client"],
        hooks=PipelineHooks(**hook_values),
        check_self_containment=False,
        skip_reference_calibration=True,
    )

    assert len(result["cells"]) == 4
    failed = result["cells"][0]
    assert failed["cell_id"] == "r-arm::skeleton-assisted"
    assert failed["failure"]["stage"] == failed_stage
    assert failed["pipeline_completed"] is False
    assert failed["attempt_count"] == 0
    assert all(cell["pipeline_completed"] is True for cell in result["cells"][1:])
    assert len([item for item in events if item[0] == "generate"]) == 3


def test_failed_reference_diagnostic_does_not_block_dynamic_cells(tmp_path: Path) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        reference_pass=False,
        validation_pass_at=1,
    )
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="reference-fail",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
        skip_reference_calibration=False,
    )

    assert result["reference_calibration_passed"] is False
    assert len(result["cells"]) == 4
    assert len([item for item in events if item[0] == "study"]) == 4
    assert all(
        reference["evaluation_role"] == "diagnostic_reference"
        for reference in result["references"].values()
    )
    assert result["success"] is True
    assert success_claim(result) is True


def test_inline_ivc_runs_formal_dynamic_cells_without_reference_diagnostic(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    formal_config = _config().as_dict()
    formal_config["formal"] = True
    result = run_experiment(
        tmp_path,
        config=formal_config,
        output_dir=tmp_path / "run",
        run_id="dynamic-only",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
        skip_reference_calibration=True,
    )

    assert not any(item[0] in {"render_reference", "reference"} for item in events)
    assert len(result["cells"]) == 4
    assert result["pipeline_completed"] is True
    assert result["reference_calibration_skipped"] is True
    assert result["reference_calibration_passed"] is False
    assert result["final_capability_validation_passed"] is True
    assert all(
        cell["outcomes"]["IVC"]["completed"] is True for cell in result["cells"]
    )


def test_reference_skip_does_not_call_incomplete_cells_completed(
    tmp_path: Path,
) -> None:
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, study_raises=True)
    result = run_experiment(
        tmp_path,
        config=_config(),
        output_dir=tmp_path / "run",
        run_id="dynamic-only-incomplete",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
        skip_reference_calibration=True,
    )

    assert len(result["cells"]) == 4
    assert result["pipeline_completed"] is False
    assert result["success"] is False
    assert result["claim"] == (
        "configured experiment ended before all cells completed; named cell failures remain"
    )


def _write_fixed_inputs(
    root: Path,
    *,
    robot: str = "r-arm",
    design: dict[str, Any] | None = None,
    suite: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixed_design = design or {
        "artifact_type": "capability_design",
        "robot_configuration_id": robot,
        "invocation_abi": {
            "kind": "capability_request",
            "method_call": "method(request=request)",
            "request_required": ["request"],
        },
        "capabilities": [
            {
                "capability_id": "cap-a",
                "method_name": "move_a",
                "covered_task_ids": ["task-0"],
            },
            {
                "capability_id": "cap-b",
                "method_name": "move_b",
                "covered_task_ids": ["task-1"],
            },
        ],
        "task_support": [
            {"task_id": "task-0", "capability_id": "cap-a"},
            {"task_id": "task-1", "capability_id": "cap-b"},
        ],
    }
    fixed_suite = suite or {
        "artifact_type": "capability_validation_suite",
        "robot_configuration_id": robot,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": [
            {
                "case_id": "cap-a-nominal",
                "capability_id": "cap-a",
                "case_role": "nominal",
            },
            {
                "case_id": "cap-a-boundary",
                "capability_id": "cap-a",
                "case_role": "calibrated_boundary",
            },
            {
                "case_id": "cap-b-nominal",
                "capability_id": "cap-b",
                "case_role": "nominal",
            },
            {
                "case_id": "cap-b-boundary",
                "capability_id": "cap-b",
                "case_role": "calibrated_boundary",
            },
        ],
    }
    robot_root = root / robot
    robot_root.mkdir(parents=True, exist_ok=True)
    (robot_root / "capability_design.json").write_text(
        json.dumps(fixed_design), encoding="utf-8"
    )
    (robot_root / "capability_validation_suite.json").write_text(
        json.dumps(fixed_suite), encoding="utf-8"
    )
    return fixed_design, fixed_suite


@pytest.mark.parametrize("invalid_artifact", ("design", "suite"))
def test_fixed_inputs_fail_validation_before_any_model_or_authoring_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_artifact: str,
) -> None:
    monkeypatch.setattr("autoadapter2.pipeline.check_environment", lambda: {})
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    fixed_root = tmp_path / "fixed"
    _write_fixed_inputs(
        fixed_root,
        **(
            {"design": {"artifact_type": "invalid"}}
            if invalid_artifact == "design"
            else {"suite": {"artifact_type": "invalid"}}
        ),
    )

    def reject_design(design: Any, package: Any) -> dict[str, Any]:
        if invalid_artifact == "design":
            raise ValueError("not a current capability-v2 design")
        return dict(design)

    def reject_suite(suite: Any, **kwargs: Any) -> dict[str, Any]:
        if invalid_artifact == "suite":
            raise ValueError("not a current capability-v2 suite")
        return dict(suite)

    def forbidden_authoring(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("TGCD/IVC must not run for fixed inputs")

    hooks = replace(
        hooks,
        capability_design_validator=reject_design,
        capability_suite_validator=reject_suite,
        tgcd_runner=forbidden_authoring,
        ivc_runner=forbidden_authoring,
    )
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "fixed-invalid",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        }
    )

    with pytest.raises(PipelineError, match="failed validation"):
        run_experiment(
            tmp_path,
            config=config,
            output_dir=tmp_path / "run",
            run_id="fixed-invalid",
            client=state["client"],
            hooks=hooks,
            check_self_containment=False,
            fixed_inputs_from=fixed_root,
        )

    assert state["client"].calls == []
    assert events == [("load", "r-arm")]


def test_fixed_inputs_skip_authoring_stay_private_and_preserve_whitelist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autoadapter2.pipeline.check_environment", lambda: {})
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=None)
    fixed_root = tmp_path / "fixed"
    fixed_design, fixed_suite = _write_fixed_inputs(fixed_root)
    base_harness = hooks.harness_runner
    task_demo_calls: list[dict[str, Any]] = []

    def forbidden_authoring(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("TGCD/IVC must not run for fixed inputs")

    def selective_harness(**kwargs: Any) -> dict[str, Any]:
        report = dict(base_harness(**kwargs))
        for trial in report["trials"]:
            trial["trial_passed"] = trial["case_id"] != "cap-b-boundary"
        report["validation_passed"] = False
        return report

    def task_demo_runner(**kwargs: Any) -> dict[str, Any]:
        task_demo_calls.append(
            {
                "design": kwargs["design"],
                "capability_whitelist": kwargs["capability_whitelist"],
            }
        )
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": True,
            "video_complete": True,
            "trials": [
                {
                    "case_id": "task-demo-0",
                    "task_id": "task-0",
                    "source_clause_id": "all-task-clauses",
                    "trial_passed": True,
                }
            ],
            "video_manifest": [],
            "high_level_controller": {"completed": True},
        }

    hooks = replace(
        hooks,
        tgcd_runner=forbidden_authoring,
        ivc_runner=forbidden_authoring,
        harness_runner=selective_harness,
        task_demo_runner=task_demo_runner,
    )
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "fixed-route",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "max_driver_attempts_per_condition": 2,
        }
    )
    destination = tmp_path / "run"

    result = run_experiment(
        tmp_path,
        config=config,
        output_dir=destination,
        run_id="fixed-route",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
        fixed_inputs_from=fixed_root,
    )

    assert not any(event[0] in {"tgcd", "ivc"} for event in events)
    cell = result["cells"][0]
    assert result["upstream_artifact_mode"] == "fixed-per-robot"
    assert cell["upstream_artifact_mode"] == "fixed-per-robot"
    for stage in ("TGCD", "IVC"):
        assert cell["outcomes"][stage]["attempted"] is False
        assert cell["outcomes"][stage]["completed"] is False
        assert cell["outcomes"][stage]["skipped"] is True
        assert cell["outcomes"][stage]["model_call_count"] == 0
        assert isinstance(
            cell["outcomes"][stage]["fixed_input_provenance"], dict
        )
        assert "fixed_inputs_from" in cell["outcomes"][stage]["reason"]
    assert cell["outcomes"]["IVC"]["candidate_driver_visible"] is False
    assert state["study_designs"] == [fixed_design]
    assert state["generate_designs"] == [fixed_design]
    assert state["repair_public_inputs"][0]["sealed_capability_design"] == fixed_design
    assert cell["passed_capability_whitelist"] == ["cap-a"]
    assert task_demo_calls[0]["capability_whitelist"] == ("cap-a",)
    assert [
        item["capability_id"] for item in task_demo_calls[0]["design"]["capabilities"]
    ] == ["cap-a"]
    copied_design = json.loads(
        (
            destination
            / "cells"
            / "r-arm"
            / "skeleton-assisted"
            / "design"
            / "capability_design.json"
        ).read_text(encoding="utf-8")
    )
    copied_suite = json.loads(
        (
            destination
            / "cells"
            / "r-arm"
            / "skeleton-assisted"
            / "private"
            / "capability_validation_suite.json"
        ).read_text(encoding="utf-8")
    )
    assert copied_design == fixed_design
    assert copied_suite == fixed_suite
    model_workspace = (
        destination / "cells" / "r-arm" / "skeleton-assisted" / "files"
    )
    assert not any(model_workspace.rglob("capability_validation_suite.json"))
    assert all(item["suite"] == fixed_suite for item in state["harness_inputs"])


@pytest.mark.parametrize(
    ("validation_pass_at", "expected_success"),
    ((1, True), (None, False)),
)
def test_disabled_task_demo_stops_after_capability_validation_without_weakening_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validation_pass_at: int | None,
    expected_success: bool,
) -> None:
    monkeypatch.setattr("autoadapter2.pipeline.check_environment", lambda: {})
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(
        tmp_path,
        events,
        validation_pass_at=validation_pass_at,
    )
    task_demo_calls: list[dict[str, Any]] = []
    evolution_calls: list[dict[str, Any]] = []

    def forbidden_task_demo(**kwargs: Any) -> dict[str, Any]:
        task_demo_calls.append(dict(kwargs))
        raise AssertionError("Task Demo must be disabled")

    def forbidden_evolution(model: Any, report: Any) -> dict[str, Any]:
        del model
        evolution_calls.append(dict(report))
        raise AssertionError("Evolution must be disabled")

    hooks = replace(
        hooks,
        task_demo_runner=forbidden_task_demo,
        evolution_runner=forbidden_evolution,
    )
    fixed_root = tmp_path / "fixed"
    _write_fixed_inputs(fixed_root)
    config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "pre-recap-runtime",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "task_demo": {"enabled": False},
            "max_driver_attempts_per_condition": 1,
            "evolution": {"enabled": False, "max_attempts": 1},
        }
    )
    destination = tmp_path / "run"

    result = run_experiment(
        tmp_path,
        config=config,
        output_dir=destination,
        run_id="pre-recap-runtime",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
        fixed_inputs_from=fixed_root,
    )

    cell = result["cells"][0]
    assert result["pipeline_completed"] is True
    assert cell["pipeline_completed"] is True
    assert cell["final_capability_validation_passed"] is expected_success
    assert result["success"] is expected_success
    assert success_claim(result) is expected_success
    assert cell["task_demo_executed"] is False
    assert cell["task_demo_passed"] is False
    assert cell["task_demo_pipeline_completed"] is False
    assert cell["task_demo"]["skipped"] is True
    assert (
        cell["task_demo"]["skip_reason"]
        == "disabled by diagnostic configuration"
    )
    assert cell["outcomes"]["TaskDemoController"] is None
    assert task_demo_calls == []
    assert evolution_calls == []
    assert not (
        destination
        / "cells"
        / "r-arm"
        / "skeleton-assisted"
        / "task-demo"
    ).exists()


def test_historical_sealed_input_reuse_remains_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autoadapter2.pipeline.check_environment", lambda: {})
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)

    with pytest.raises(PipelineError, match="sealed TGCD/IVC reuse is incompatible"):
        run_experiment(
            tmp_path,
            config=_config(),
            output_dir=tmp_path / "run",
            run_id="sealed-rejected",
            client=state["client"],
            hooks=hooks,
            check_self_containment=False,
            sealed_inputs_from=tmp_path / "old-run",
        )

    assert state["client"].calls == []
    assert events == [("load", "r-arm"), ("load", "r-quad")]


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
    config_path = tmp_path / "configs" / "experiments" / "mainline.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
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


@pytest.mark.parametrize(
    ("extra_arguments", "expected_skip"),
    (([], True), (["--run-reference-positive-controls"], False)),
)
def test_full_cli_forwards_reference_positive_control_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_arguments: list[str],
    expected_skip: bool,
) -> None:
    config_path = tmp_path / "configs" / "experiments" / "mainline.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "experiment_id": "cli-reference-choice",
                "robots": ["r-arm"],
                "generation_conditions": ["skeleton-assisted"],
            }
        ),
        encoding="utf-8",
    )
    received: list[dict[str, Any]] = []

    def fake_run_experiment(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        received.append(dict(kwargs))
        return {
            "experiment_id": "cli-reference-choice",
            "run_id": "cli-reference-choice",
            "pipeline_completed": True,
            "final_capability_validation_passed": False,
            "success": False,
            "cells": [],
        }

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)

    assert (
        cli.main(["full", "--root", str(tmp_path), *extra_arguments])
        == 1
    )
    assert received[0]["skip_reference_calibration"] is expected_skip


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
