from __future__ import annotations

import copy
import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from experiment.experiment3 import runner


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _model_pin() -> dict[str, Any]:
    return {
        "vendor": "Anthropic",
        "api_protocol": "openai-compatible",
        "model_id": runner.MODEL_ID,
        "revision": "test-sonnet-4-6-pin",
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 16_384,
        "temperature": 0.0,
        "thinking": None,
        "tool_history_mode": "native",
        "price_snapshot": {
            "date": "2026-08-23",
            "currency": "TEST",
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        },
    }


def _transport() -> dict[str, Any]:
    return {
        "request_timeout_s": 120,
        "retry_policy": copy.deepcopy(runner.EXPECTED_RETRY_POLICY),
        "history_char_budget": 80_000,
    }


def _install_route_profile(tmp_path: Path, reference: Mapping[str, Any]) -> None:
    relative_path = str(reference["path"])
    source = REPOSITORY_ROOT / relative_path
    destination = tmp_path / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _passing_ivc_context_check() -> dict[str, Any]:
    return {
        "passed": True,
        "robots": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
    }


def _evidence(
    tmp_path: Path, label: str, retained: dict[str, Any]
) -> dict[str, Any]:
    path = tmp_path / "evidence" / f"{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": True, **retained}) + "\n", encoding="utf-8")
    return {"path": str(path), "passed": True}


def _executable_manifest(tmp_path: Path) -> dict[str, Any]:
    manifest = runner.load_manifest()
    route_reference = copy.deepcopy(manifest["runtime"]["holisticai_route_profile"])
    _install_route_profile(tmp_path, route_reference)
    manifest["status"] = "formal-authorised"
    manifest["formal_dispatch_authorised"] = True
    manifest["runtime"] = {
        "holisticai_route_profile": route_reference,
        "producer_model": _model_pin(),
        "producer_transport": _transport(),
        "resources": {
            "phase_turn_budgets": copy.deepcopy(runner.PHASE_TURN_BUDGETS),
            "execute_python": {
                "wall_timeout_s_per_call": 30,
                "max_output_chars_per_call": 12_000,
                "max_steps_per_phase": 4_000,
                "max_sim_time_s_per_phase": 20.0,
            },
            "recap": copy.deepcopy(runner.RECAP_BUDGET),
            "validation": {"record_video": True, "worker_wall_timeout_s": 120},
        },
    }
    recorder_name = "franka-recorder.json"
    _evidence(
        tmp_path,
        "franka-recorder",
        {
            "artifact_type": "experiment3_franka_recorder_canary",
            "video_complete": True,
            "video": {"decodable": True},
        },
    )
    boundary = _evidence(
        tmp_path,
        "framework-boundaries",
        {
            "artifact_type": "experiment3_framework_harness_boundary_checks",
            "checks": {
                "candidate_isolation": {"passed": True},
                "canonical_physics": {"passed": True},
                "independent_harness": {"passed": True},
                "recorder": {
                    "passed": True,
                    "real_mujoco_evidence": recorder_name,
                },
            },
        },
    )
    manifest["readiness_evidence"] = {
        "packages": {
            robot: _evidence(
                tmp_path,
                f"package-{index}",
                {
                    "artifact_type": "experiment3_package_check_evidence",
                    "robot_configuration_ids": [robot],
                },
            )
            for index, robot in enumerate(runner.ROBOT_CONFIGURATIONS)
        },
        "producer_model_canary": _evidence(
            tmp_path,
            "producer-model",
            {
                "artifact_type": "experiment3_exact_sonnet_runtime_pin_canary",
                "requested_model": runner.MODEL_ID,
                "returned_model": runner.MODEL_ID,
                "returned_model_matches_pin": True,
                "request_settings": {
                    "temperature": 0.0,
                    "context_limit_tokens": 1_000_000,
                    "max_output_tokens": 16_384,
                },
            },
        ),
        "candidate_isolation": copy.deepcopy(boundary),
        "canonical_physics": copy.deepcopy(boundary),
        "independent_harness": copy.deepcopy(boundary),
        "recorder": copy.deepcopy(boundary),
    }
    return manifest


def _resume_record(
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    git_commit: str,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    revisions = {
        "authority_revision": manifest["authority_revision"],
        "manifest_revision": manifest["manifest_revision"],
        "protocol_revision": manifest["protocol_revision"],
        "git_commit": git_commit,
    }
    declared = runner.expand_cells(manifest)
    row_statuses = statuses or ["failed"] * len(declared)
    rows = [
        {
            **cell,
            **revisions,
            "status": status,
            "result": None,
            "failure": None,
        }
        for cell, status in zip(declared, row_statuses, strict=True)
    ]
    record = {
        "experiment_id": runner.EXPERIMENT_ID,
        "denominator": 33,
        **revisions,
        "dispatch_started": True,
        "completed_cells": sum(status == "completed" for status in row_statuses),
        "failed_cells": sum(status == "failed" for status in row_statuses),
        "predeclared_cells": sum(
            status == "predeclared" for status in row_statuses
        ),
        "all_declared_cells_retained": True,
        "cells": rows,
    }
    output_dir.mkdir(parents=True)
    (output_dir / "experiment3_run_record.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def test_checked_in_manifest_expands_exact_replicate_major_33_cells() -> None:
    manifest = runner.load_manifest()
    cells = runner.expand_cells(manifest)

    assert len(cells) == 33
    assert len({cell["cell_id"] for cell in cells}) == 33
    assert [(cell["replicate_id"], cell["robot_configuration_id"]) for cell in cells] == [
        (replicate, robot)
        for replicate in runner.REPLICATE_IDS
        for robot in runner.ROBOT_CONFIGURATIONS
    ]
    assert all(cell["generation_condition"] == "skeleton-assisted" for cell in cells)
    assert all(
        cell["morphology_label"]
        == runner.MORPHOLOGY_LABELS[cell["robot_configuration_id"]]
        for cell in cells
    )
    assert all(
        cell["experiment_id"] in cell["run_id"]
        and cell["replicate_id"] in cell["run_id"]
        and cell["robot_configuration_id"] in cell["run_id"]
        for cell in cells
    )
    assert manifest["runtime"]["resources"]["phase_turn_budgets"] == (
        runner.PHASE_TURN_BUDGETS
    )
    assert manifest["runtime"]["resources"]["recap"] == runner.RECAP_BUDGET


def test_design_validation_rejects_an_extra_condition_or_enabled_evolution() -> None:
    manifest = runner.load_manifest()
    manifest["conditions"] = ["skeleton-assisted", "from-scratch"]
    with pytest.raises(runner.Experiment3RunnerError, match="skeleton-assisted only"):
        runner.validate_design_manifest(manifest)

    manifest = runner.load_manifest()
    manifest["evolution"]["enabled"] = True
    with pytest.raises(runner.Experiment3RunnerError, match="must all be disabled"):
        runner.validate_design_manifest(manifest)


def test_returned_identity_check_allows_a_failed_physical_retry_before_success() -> None:
    client = SimpleNamespace(
        calls=[
            {"status": "http_error", "http_status": 503, "returned_model": None},
            {"status": "success", "http_status": 200, "returned_model": runner.MODEL_ID},
        ]
    )

    runner._assert_returned_identity(client, runner.MODEL_ID)

    client.calls[-1]["returned_model"] = "wrong.model"
    with pytest.raises(runner.Experiment3RunnerError, match="differs"):
        runner._assert_returned_identity(client, runner.MODEL_ID)

    client.calls = [
        {"status": "success", "http_status": 200, "returned_model": runner.MODEL_ID},
        {"status": "success", "http_status": 200, "returned_model": None},
    ]
    with pytest.raises(runner.Experiment3RunnerError, match="omitted"):
        runner._assert_returned_identity(client, runner.MODEL_ID)


def test_task_demo_and_formal_evidence_postchecks_reject_false_terminal_success() -> None:
    contradictory = {
        "passed_capability_whitelist": ["capability-a"],
        "task_demo_executed": False,
        "task_demo": {
            "skipped": True,
            "skip_reason": "no final driver was admitted",
        },
    }
    with pytest.raises(runner.Experiment3RunnerError, match="requires an executed"):
        runner._task_demo_terminal(contradictory)

    robot = runner.ROBOT_CONFIGURATIONS[0]
    cell = {
        "cell_id": f"{robot}::{runner.CONDITION}",
        "robot_configuration_id": robot,
        "condition": runner.CONDITION,
        "outcomes": {
            "STUDY": {"completed": True},
            "TGCD": {"completed": True},
            "IVC": {"completed": True},
        },
        "passed_capability_whitelist": [],
        "capability_validation_executed": True,
        "video_required": True,
        "video_complete": False,
        "final_capability_validation_passed": False,
    }
    with pytest.raises(runner.Experiment3RunnerError, match="video evidence"):
        runner._assert_formal_cell_evidence(
            {"run_id": "declared-run"},
            cell,
            robot=robot,
            run_id="declared-run",
        )

def test_checked_in_preflight_accepts_the_current_inline_ivc_contract() -> None:
    mainline_root = Path(__file__).resolve().parents[2] / "autoadapter"

    manifest = runner.load_manifest()
    checked = runner.validate_executable_preflight(manifest, mainline_root=mainline_root)

    assert checked["resources"]["phase_turn_budgets"] == runner.PHASE_TURN_BUDGETS
    assert checked["resources"]["recap"] == runner.RECAP_BUDGET
    route_evidence = checked["holisticai_route_profile"]
    assert route_evidence["reference"] == manifest["runtime"]["holisticai_route_profile"]
    assert route_evidence["resolved_route"]["endpoint_url"].endswith(
        "/v1/chat/completions"
    )
    assert (
        route_evidence["resolved_route"]["credential_env"]
        == "AUTOADAPTER_HOLISTICAI_API_KEY"
    )
    assert "base_url" not in manifest["runtime"]["producer_model"]
    assert set(manifest["runtime"]["producer_transport"]) == {
        "request_timeout_s",
        "retry_policy",
        "history_char_budget",
    }
    assert manifest["runtime"]["producer_transport"]["request_timeout_s"] == 120
    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["resources"]["phase_turn_budgets"]["ivc"] = 5
    with pytest.raises(runner.Experiment3RunnerError, match="file-workflow budgets"):
        runner.validate_executable_preflight(drifted, mainline_root=mainline_root)

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["resources"]["recap"][
        "max_capability_calls_per_task"
    ] = 13
    with pytest.raises(runner.Experiment3RunnerError, match="16 planning turns and 12"):
        runner.validate_executable_preflight(drifted, mainline_root=mainline_root)

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["producer_model"]["base_url"] = "https://inline.invalid/v1"
    with pytest.raises(runner.Experiment3RunnerError, match="must not inline"):
        runner.validate_executable_preflight(drifted, mainline_root=mainline_root)

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["producer_transport"]["auth_header"] = "Authorization"
    with pytest.raises(runner.Experiment3RunnerError, match="must not inline"):
        runner.validate_executable_preflight(drifted, mainline_root=mainline_root)

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["producer_transport"]["request_timeout_s"] = 121
    with pytest.raises(runner.Experiment3RunnerError, match="profile limit of 120"):
        runner.validate_executable_preflight(drifted, mainline_root=mainline_root)

    assert len(checked["readiness_evidence"]["packages"]) == 11
    assert manifest["ivc_contract"]["inline_measurement_binding_required"] is True
    assert manifest["ivc_contract"]["binding_id_forbidden"] is True
    assert manifest["ivc_contract"]["worked_reference_case_count"] == 22


def test_readiness_rejects_passed_evidence_with_the_wrong_semantic_scope(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    wrong = _evidence(
        tmp_path,
        "wrong-package-scope",
        {
            "artifact_type": "experiment3_package_check_evidence",
            "robot_configuration_ids": ["franka_panda"],
        },
    )
    manifest["readiness_evidence"]["packages"]["robotstudio_so101"] = wrong

    with pytest.raises(runner.Experiment3RunnerError, match="does not prove that package"):
        runner.validate_executable_preflight(manifest, mainline_root=tmp_path)


def test_default_holisticai_env_file_supplies_only_the_new_credential_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env.holisticai-api"
    env_file.write_text(
        "AUTOADAPTER_HOLISTICAI_API_KEY=default-holisticai-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "DEFAULT_HOLISTICAI_ENV_FILE", env_file)
    monkeypatch.delenv("AUTOADAPTER_HOLISTICAI_API_KEY", raising=False)

    runner._load_env_files([])

    assert os.environ["AUTOADAPTER_HOLISTICAI_API_KEY"] == (
        "default-holisticai-secret"
    )


@pytest.mark.parametrize("command", ("formal", "resume"))
def test_cli_rejects_the_route_before_reading_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(runner, "load_manifest", lambda _path: {"loaded": True})

    def reject_route(
        _manifest: Mapping[str, Any], *, mainline_root: str | Path
    ) -> dict[str, Any]:
        events.append(f"preflight:{mainline_root}")
        raise runner.Experiment3RunnerError("invalid holisticai route profile")

    monkeypatch.setattr(runner, "validate_executable_preflight", reject_route)
    monkeypatch.setattr(
        runner,
        "_load_env_files",
        lambda _paths: events.append("credentials-read"),
    )

    rc = runner.main(
        [
            command,
            "--root",
            str(tmp_path),
            "--manifest",
            "unused-manifest.json",
            "--output",
            str(tmp_path / "unused-output"),
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert rc == 2
    assert error["error"] == "invalid holisticai route profile"
    assert events == [f"preflight:{tmp_path}"]


def test_preflight_loads_all_package_private_ivc_contexts_before_package_check(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    missing_robot = "unitree-go2-stock-12dof"
    package_check_calls: list[Path] = []

    def package_for(robot: str) -> SimpleNamespace:
        return SimpleNamespace(
            root=tmp_path / "packages" / robot,
            robot_configuration_id=robot,
            package_version="1.0.0",
            snapshot_id=f"{robot}-snapshot",
        )

    def write_context(
        robot: str,
        *,
        omit: str | None = None,
    ) -> None:
        package = package_for(robot)
        # Deliberately exercise the real loader's package-private task-context
        # projection.  This remains context for IVC authorship, not a suite of
        # prewritten capability cases.
        destination = package.root / "tasks" / "private"
        destination.mkdir(parents=True, exist_ok=True)
        for name, id_field in {
            "instances": "instance_id",
            "bindings": "binding_id",
            "guards": "guard_id",
        }.items():
            if name == omit:
                continue
            record: dict[str, Any] = {id_field: f"{robot}-{name}"}
            if name == "instances":
                record["public_arguments"] = {
                    "request": {
                        "task_id": f"{robot}-private-task",
                        "task_parameters": {"distance_m": 0.25},
                    }
                }
            (destination / f"{name}.json").write_text(
                json.dumps(
                    {
                        "robot_configuration_id": robot,
                        "package_version": package.package_version,
                        "task_snapshot_id": package.snapshot_id,
                        name: [record],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

    for robot in runner.ROBOT_CONFIGURATIONS:
        write_context(robot, omit="bindings" if robot == missing_robot else None)

    def ivc_context_check(root: str | Path) -> Mapping[str, Any]:
        return runner._check_package_ivc_contexts(
            root,
            package_loader=lambda _root, robot: package_for(robot),
        )

    def package_check(root: str | Path, **_kwargs: Any) -> Mapping[str, Any]:
        package_check_calls.append(Path(root))
        return {
            "package_check_passed": True,
            "robots": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
        }

    with pytest.raises(
        runner.Experiment3RunnerError,
        match=r"unitree-go2-stock-12dof.*bindings\.json",
    ):
        runner.run_preflight(
            tmp_path,
            manifest=manifest,
            package_check_fn=package_check,
            ivc_context_check_fn=ivc_context_check,
            check_self_containment=False,
        )
    assert package_check_calls == []

    write_context(missing_robot)
    checked = runner.run_preflight(
        tmp_path,
        manifest=manifest,
        package_check_fn=package_check,
        ivc_context_check_fn=ivc_context_check,
        check_self_containment=False,
    )

    assert checked["package_ivc_context_check"]["passed"] is True
    assert set(checked["package_ivc_context_check"]["robots"]) == set(
        runner.ROBOT_CONFIGURATIONS
    )
    assert {
        item["private_namespace"]
        for item in checked["package_ivc_context_check"]["robots"].values()
    } == {"task"}
    assert package_check_calls == [tmp_path]


def test_formal_runner_uses_one_fresh_singleton_call_per_cell_and_retains_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _executable_manifest(tmp_path)
    run_calls: list[tuple[str, str]] = []
    clients: list[Any] = []
    hook_objects: list[Any] = []
    monkeypatch.setenv("AUTOADAPTER_HOLISTICAI_API_KEY", "manifest-pinned-secret")
    monkeypatch.setenv("AUTOADAPTER_MODEL_API_KEY", "must-not-be-read")

    def hooks_factory(cell: dict[str, str]) -> Any:
        hook = SimpleNamespace(cell_id=cell["cell_id"])
        hook_objects.append(hook)
        return hook

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        config = kwargs["config"]
        robot = config["robots"][0]
        run_id = kwargs["run_id"]
        replicate = next(item for item in runner.REPLICATE_IDS if item in run_id)
        run_calls.append((replicate, robot))
        clients.append(kwargs["producer_client"])
        assert config["robots"] == [robot]
        assert config["generation_conditions"] == ["skeleton-assisted"]
        assert config["formal"] is True
        assert config["max_driver_attempts_per_condition"] == 3
        assert config["experience"]["input"] == []
        assert config["evolution"] == {"enabled": False}
        assert kwargs["skip_reference_calibration"] is True
        assert kwargs["producer_client"].config.api_key == "manifest-pinned-secret"
        assert kwargs["hooks"].cell_id.endswith(f"::{replicate}::{robot}")
        if replicate == "r02" and robot == "piper":
            raise RuntimeError("retained synthetic cell failure")
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {
                "returned_model": (
                    "wrong.model"
                    if replicate == "r01" and robot == "franka_panda"
                    else runner.MODEL_ID
                ),
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 3,
                "cache_write_tokens": 4,
                "reasoning_tokens": 5,
                "total_tokens": 120,
                "elapsed_s": 0.25,
            }
        )
        truthful_not_run = replicate == "r03" and robot == "aloha_2"
        return {
            "run_id": run_id,
            "evolution_enabled": False,
            "evolution_model": None,
            "experience_input_ids": {robot: []},
            "cells": [
                {
                    "cell_id": f"{robot}::{runner.CONDITION}",
                    "robot_configuration_id": robot,
                    "condition": runner.CONDITION,
                    "attempt_count": 1,
                    "frozen_driver_attempt_count": 1,
                    "pass@0": not truthful_not_run,
                    "final_capability_validation_passed": not truthful_not_run,
                    "passed_capability_whitelist": (
                        [] if truthful_not_run else ["capability-a"]
                    ),
                    "capability_validation_executed": True,
                    "video_required": True,
                    "video_complete": True,
                    "task_demo_executed": not truthful_not_run,
                    "task_demo_passed": not truthful_not_run,
                    "task_demo_video_complete": not truthful_not_run,
                    "task_demo_task_counts": {
                        "passed": 5 if not truthful_not_run else 0,
                        "total": 5 if not truthful_not_run else 0,
                    },
                    "task_demo": (
                        {
                            "skipped": True,
                            "skip_reason": "no final driver was admitted",
                        }
                        if truthful_not_run
                        else {"skipped": False}
                    ),
                    "outcomes": {
                        "STUDY": {"completed": True},
                        "TGCD": {"completed": True},
                        "IVC": {"completed": True},
                        "Evolution": None,
                    },
                }
            ],
        }

    result = runner.run_formal(
        tmp_path,
        manifest=manifest,
        output_dir=tmp_path / "formal-run",
        run_experiment_fn=fake_run,
        package_check_fn=lambda *_args, **_kwargs: {
            "package_check_passed": True,
            "robots": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
        },
        ivc_context_check_fn=lambda _root: _passing_ivc_context_check(),
        hooks_factory=hooks_factory,
        check_self_containment=False,
        git_commit_fn=lambda _root: "a" * 40,
    )

    expected = [
        (replicate, robot)
        for replicate in runner.REPLICATE_IDS
        for robot in runner.ROBOT_CONFIGURATIONS
    ]
    assert run_calls == expected
    assert len(clients) == 33
    assert len({id(client) for client in clients}) == 33
    assert all(client.config.endpoint_path == "/chat/completions" for client in clients)
    assert len(hook_objects) == 33
    assert len({id(hook) for hook in hook_objects}) == 33
    assert Counter(row["status"] for row in result["cells"]) == {
        "completed": 31,
        "failed": 2,
    }
    failed = next(
        row
        for row in result["cells"]
        if row["replicate_id"] == "r02"
        and row["robot_configuration_id"] == "piper"
    )
    assert failed["replicate_id"] == "r02"
    assert failed["robot_configuration_id"] == "piper"
    assert failed["failure_stage"] == "pipeline"
    assert failed["failure"]["stage"] == "pipeline"
    assert failed["task_demo"]["status"] == "not-run"
    assert failed["task_demo"]["reason"]
    failed_resources = failed["resource_summary"]["producer"]
    assert failed_resources["call_count"] == 0
    assert failed_resources["token_categories"]["input_tokens"] is None
    assert failed_resources["token_categories"]["output_tokens"] is None
    assert failed_resources["token_observed_call_counts"]["input_tokens"] == 0
    assert failed_resources["estimated_cost"]["amount"] is None
    assert failed_resources["model_elapsed_time_s"] == 0
    identity_failed = next(
        row
        for row in result["cells"]
        if row["replicate_id"] == "r01"
        and row["robot_configuration_id"] == "franka_panda"
    )
    assert identity_failed["status"] == "failed"
    assert identity_failed["failure_stage"] == "result-postcheck"
    assert identity_failed["result"] is not None
    assert identity_failed["task_demo"] == {
        "status": "executed",
        "passed": True,
        "capability_whitelist": ["capability-a"],
    }
    assert identity_failed["resource_summary"]["producer"]["call_count"] == 1
    assert result["denominator"] == 33
    assert result["authority_revision"] == runner.AUTHORITY_REVISION
    assert result["manifest_revision"] == runner.MANIFEST_REVISION
    assert result["protocol_revision"] == runner.PROTOCOL_REVISION
    assert result["git_commit"] == "a" * 40
    assert all(
        row["authority_revision"] == runner.AUTHORITY_REVISION
        and row["manifest_revision"] == runner.MANIFEST_REVISION
        and row["protocol_revision"] == runner.PROTOCOL_REVISION
        and row["git_commit"] == "a" * 40
        for row in result["cells"]
    )
    assert result["all_declared_cells_retained"] is True
    assert (
        result["holisticai_route_profile"]["resolved_route"]["endpoint_url"]
        .endswith("/v1/chat/completions")
    )
    assert "manifest-pinned-secret" not in json.dumps(result, sort_keys=True)
    assert result["completed_cells"] == 31
    assert result["failed_cells"] == 2
    completed = next(row for row in result["cells"] if row["status"] == "completed")
    resource = completed["resource_summary"]
    assert resource["runner_wall_time_s"] >= 0
    assert resource["producer"]["call_count"] == 1
    assert resource["producer"]["token_categories"]["total_tokens"] == 120
    assert resource["producer"]["model_elapsed_time_s"] == 0.25
    assert resource["producer"]["estimated_cost"]["is_estimate"] is True
    assert resource["producer"]["estimated_cost"]["amount"] == pytest.approx(
        0.00014
    )
    truthful_not_run = next(
        row
        for row in result["cells"]
        if row["replicate_id"] == "r03"
        and row["robot_configuration_id"] == "aloha_2"
    )
    assert truthful_not_run["status"] == "completed"
    assert truthful_not_run["task_demo"] == {
        "status": "not-run",
        "reason": "no final driver was admitted",
    }
    assert not list((tmp_path / "formal-run").rglob("experience_review_queue.json"))

    summary = runner.summarise_results(result)
    assert summary["denominator"] == 33
    assert len(summary["case_rows"]) == 33
    assert summary["reporting_groups"]["reference_seen_controls"]["denominator"] == 6
    assert summary["reporting_groups"]["transfer_cells"]["denominator"] == 27
    assert summary["reporting_groups"]["effect_claim"] is False
    assert summary["reporting_groups"]["quadruped_transfer_claim"] is False
    assert "morphology" not in summary["configurations"]
    so101 = summary["configurations"]["robotstudio_so101"]
    assert so101["morphology_label"] == "fixed serial arm"
    assert all(
        case["morphology_label"]
        == runner.MORPHOLOGY_LABELS[case["robot_configuration_id"]]
        for case in summary["case_rows"]
    )
    assert so101["counts_and_proportions"]["pass@0"] == {
        "count": 3,
        "denominator": 3,
        "proportion": 1.0,
    }
    assert so101["median_and_range"]["attempts"] == {
        "observed": 3,
        "median": 1,
        "range": [1, 1],
    }
    assert so101["median_and_range"]["model_calls"]["median"] == 1
    assert so101["median_and_range"]["total_tokens"]["median"] == 120
    assert so101["median_and_range"]["estimated_cost"]["is_estimate"] is True
    piper = summary["configurations"]["piper"]
    assert piper["counts_and_proportions"]["failures"]["count"] == 1
    aloha = summary["configurations"]["aloha_2"]
    assert aloha["counts_and_proportions"]["task_demo_not_run"]["count"] == 1
    failure_case = next(
        case
        for case in summary["case_rows"]
        if case["replicate_id"] == "r02"
        and case["robot_configuration_id"] == "piper"
    )
    assert failure_case["failure_stage"] == "pipeline"

    malformed = copy.deepcopy(result)
    malformed["cells"][0]["replicate_id"] = "r02"
    with pytest.raises(runner.Experiment3RunnerError, match="r01-r03 once"):
        runner.summarise_results(malformed)


def test_resume_skips_terminal_rows_fails_a_partial_workspace_and_runs_only_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "b" * 40
    statuses = ["completed", "predeclared", "predeclared"] + ["failed"] * 30
    output = tmp_path / "resume-run"
    original = _resume_record(
        output, manifest, git_commit=commit, statuses=statuses
    )
    terminal_before = copy.deepcopy(original["cells"][0])
    partial = original["cells"][1]
    partial_workspace = (
        output
        / "cells"
        / partial["replicate_id"]
        / partial["robot_configuration_id"]
    )
    partial_workspace.mkdir(parents=True)
    client_cells: list[str] = []
    run_cells: list[str] = []
    monkeypatch.setenv("AUTOADAPTER_HOLISTICAI_API_KEY", "resume-test-secret")

    def client_factory(
        _role: str,
        model: Mapping[str, Any],
        transport: Mapping[str, Any],
        cell: Mapping[str, str],
    ) -> Any:
        client_cells.append(cell["cell_id"])
        return SimpleNamespace(
            config=SimpleNamespace(
                provider=model["vendor"],
                model=model["model_id"],
                base_url=model["base_url"],
                endpoint_path=transport["endpoint_path"],
                api_protocol=model["api_protocol"],
                thinking=None,
                timeout_s=float(transport["request_timeout_s"]),
                max_tokens=model["max_output_tokens"],
                tool_history_mode=model["tool_history_mode"],
                history_char_budget=transport["history_char_budget"],
                auth_header=transport["auth_header"],
                auth_prefix=transport["auth_prefix"],
            ),
            calls=[],
        )

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        robot = kwargs["config"]["robots"][0]
        run_cells.append(robot)
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {
                "status": "success",
                "returned_model": runner.MODEL_ID,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "elapsed_s": 0.1,
            }
        )
        return {
            "run_id": kwargs["run_id"],
            "evolution_enabled": False,
            "evolution_model": None,
            "experience_input_ids": {robot: []},
            "cells": [
                {
                    "cell_id": f"{robot}::{runner.CONDITION}",
                    "robot_configuration_id": robot,
                    "condition": runner.CONDITION,
                    "attempt_count": 1,
                    "frozen_driver_attempt_count": 1,
                    "pass@0": True,
                    "final_capability_validation_passed": True,
                    "passed_capability_whitelist": ["capability-a"],
                    "capability_validation_executed": True,
                    "video_required": True,
                    "video_complete": True,
                    "task_demo_executed": True,
                    "task_demo_passed": True,
                    "task_demo_video_complete": True,
                    "task_demo_task_counts": {"passed": 5, "total": 5},
                    "task_demo": {"skipped": False},
                    "outcomes": {
                        "STUDY": {"completed": True},
                        "TGCD": {"completed": True},
                        "IVC": {"completed": True},
                        "Evolution": None,
                    },
                }
            ],
        }

    result = runner.run_resume(
        tmp_path,
        manifest=manifest,
        output_dir=output,
        client_factory=client_factory,
        run_experiment_fn=fake_run,
        package_check_fn=lambda *_args, **_kwargs: {
            "package_check_passed": True,
            "robots": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
        },
        ivc_context_check_fn=lambda _root: _passing_ivc_context_check(),
        check_self_containment=False,
        git_commit_fn=lambda _root: commit,
    )

    untouched = original["cells"][2]
    assert client_cells == [untouched["cell_id"]]
    assert run_cells == [untouched["robot_configuration_id"]]
    assert result["cells"][0] == terminal_before
    interrupted = result["cells"][1]
    assert interrupted["status"] == "failed"
    assert interrupted["failure_stage"] == "resume-partial-workspace"
    assert interrupted["failure"]["type"] == "InterruptedCellWorkspace"
    assert interrupted["resource_summary"]["producer"] is None
    assert result["cells"][2]["status"] == "completed"
    assert result["denominator"] == 33
    assert result["completed_cells"] == 2
    assert result["failed_cells"] == 31
    assert result["predeclared_cells"] == 0


@pytest.mark.parametrize(
    "field",
    [
        "authority_revision",
        "manifest_revision",
        "protocol_revision",
        "git_commit",
    ],
)
def test_resume_rejects_each_revision_or_commit_mismatch(
    tmp_path: Path, field: str
) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "c" * 40
    output = tmp_path / f"mismatch-{field}"
    record = _resume_record(output, manifest, git_commit=commit)
    record[field] = "d" * 40 if field == "git_commit" else "mismatch"
    (output / "experiment3_run_record.json").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )

    with pytest.raises(runner.Experiment3RunnerError, match=field):
        runner.run_resume(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            git_commit_fn=lambda _root: commit,
        )


def test_resume_rejects_a_cell_level_revision_mismatch(tmp_path: Path) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "d" * 40
    output = tmp_path / "cell-mismatch"
    record = _resume_record(output, manifest, git_commit=commit)
    record["cells"][0]["protocol_revision"] = "mismatch"
    (output / "experiment3_run_record.json").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )

    with pytest.raises(runner.Experiment3RunnerError, match="protocol_revision"):
        runner.run_resume(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            git_commit_fn=lambda _root: commit,
        )


def test_resume_rejects_an_unknown_cell_status(tmp_path: Path) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "e" * 40
    output = tmp_path / "unknown-status"
    record = _resume_record(output, manifest, git_commit=commit)
    record["cells"][0]["status"] = "running"
    (output / "experiment3_run_record.json").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )

    with pytest.raises(runner.Experiment3RunnerError, match="unknown status"):
        runner.run_resume(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            git_commit_fn=lambda _root: commit,
        )


def test_resume_of_an_all_terminal_record_is_a_no_op(tmp_path: Path) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "f" * 40
    output = tmp_path / "terminal-run"
    expected = _resume_record(output, manifest, git_commit=commit)
    record_path = output / "experiment3_run_record.json"
    retained_before = record_path.read_text(encoding="utf-8")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("all-terminal resume must not execute dependencies")

    result = runner.run_resume(
        tmp_path,
        manifest=manifest,
        output_dir=output,
        client_factory=forbidden,
        run_experiment_fn=forbidden,
        package_check_fn=forbidden,
        git_commit_fn=lambda _root: commit,
    )

    assert result == expected
    assert record_path.read_text(encoding="utf-8") == retained_before


def test_atomic_record_write_preserves_previous_json_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_path = tmp_path / "experiment3_run_record.json"
    runner._write_json(record_path, {"version": "retained"})
    retained_before = record_path.read_text(encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interruption before atomic replace")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        runner._write_json(record_path, {"version": "partial"})

    assert record_path.read_text(encoding="utf-8") == retained_before
    assert not (tmp_path / ".experiment3_run_record.json.tmp").exists()


def test_current_git_commit_rejects_dirty_formal_execution_input(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    mainline = repository / "autoadapter"
    runner_path = repository / "experiment" / "experiment3" / "runner.py"
    mainline.mkdir(parents=True)
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text("clean = True\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "add", "experiment/experiment3/runner.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Experiment Test",
            "-c",
            "user.email=experiment@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )

    clean_commit = runner._current_git_commit(mainline)
    assert runner.GIT_COMMIT_PATTERN.fullmatch(clean_commit)

    runner_path.write_text("clean = False\n", encoding="utf-8")
    with pytest.raises(runner.Experiment3RunnerError, match="committed Experiment 3 inputs"):
        runner._current_git_commit(mainline)


def test_resume_rejects_record_counts_that_disagree_with_rows(tmp_path: Path) -> None:
    manifest = _executable_manifest(tmp_path)
    commit = "1" * 40
    output = tmp_path / "count-mismatch"
    record = _resume_record(output, manifest, git_commit=commit)
    record["failed_cells"] = 32
    (output / "experiment3_run_record.json").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )

    with pytest.raises(runner.Experiment3RunnerError, match="failed_cells"):
        runner.run_resume(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            git_commit_fn=lambda _root: commit,
        )


def test_formal_rejects_missing_git_commit_before_creating_a_record(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    output = tmp_path / "missing-commit"

    with pytest.raises(runner.Experiment3RunnerError, match="full Git commit"):
        runner.run_formal(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            git_commit_fn=lambda _root: "",
        )

    assert not output.exists()


def test_cli_dispatches_preflight_formal_and_summarise_without_a_test_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = Path(runner.__file__).with_name("manifest.json")
    assert runner.main(["design-check", "--manifest", str(manifest_path)]) == 0
    design = json.loads(capsys.readouterr().out)
    assert design["ok"] is True
    assert design["cell_count"] == 33

    events: list[tuple[str, dict[str, Any]]] = []
    env_file = tmp_path / "holisticai-api.env"
    env_file.write_text(
        "export EXPERIMENT3_CLI_FILE_KEY='formal-file-secret'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPERIMENT3_CLI_FILE_KEY", raising=False)

    def fake_preflight(root: str, **kwargs: Any) -> dict[str, Any]:
        events.append(("preflight", {"root": root, **kwargs}))
        return {
            "producer_model": {"model_id": runner.MODEL_ID},
            "holisticai_route_profile": {
                "reference": {
                    "path": "autoadapter/configs/providers/holisticai-gateway-long-request-eu-west-2-v1.json",
                    "profile_id": "holisticai-gateway-long-request-eu-west-2-v1",
                },
                "resolved_route": {"endpoint_url": "https://model.invalid/v1/chat/completions"},
            },
            "readiness_evidence": {
                "packages": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
            },
            "current_package_check": {"package_check_passed": True},
            "package_ivc_context_check": _passing_ivc_context_check(),
        }

    def fake_formal(root: str, **kwargs: Any) -> dict[str, Any]:
        assert os.environ["EXPERIMENT3_CLI_FILE_KEY"] == "formal-file-secret"
        events.append(("formal", {"root": root, **kwargs}))
        assert "client_factory" not in kwargs
        return {
            "denominator": 33,
            "completed_cells": 30,
            "failed_cells": 3,
            "all_declared_cells_retained": True,
        }

    def fake_resume(root: str, **kwargs: Any) -> dict[str, Any]:
        assert os.environ["EXPERIMENT3_CLI_FILE_KEY"] == "formal-file-secret"
        events.append(("resume", {"root": root, **kwargs}))
        assert "client_factory" not in kwargs
        return {
            "denominator": 33,
            "completed_cells": 31,
            "failed_cells": 2,
            "all_declared_cells_retained": True,
        }

    monkeypatch.setattr(runner, "run_preflight", fake_preflight)
    monkeypatch.setattr(runner, "run_formal", fake_formal)
    monkeypatch.setattr(runner, "run_resume", fake_resume)
    manifest = runner.load_manifest(manifest_path)
    credential_preflight_calls: list[tuple[dict[str, Any], str]] = []
    monkeypatch.setattr(
        runner,
        "validate_executable_preflight",
        lambda value, *, mainline_root: credential_preflight_calls.append(
            (copy.deepcopy(dict(value)), str(mainline_root))
        ),
    )

    assert runner.main(
        [
            "preflight",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
        ]
    ) == 0
    preflight_payload = json.loads(capsys.readouterr().out)
    assert preflight_payload["current_package_check_passed"] is True

    formal_output = tmp_path / "formal-cli"
    assert runner.main(
        [
            "formal",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(formal_output),
            "--env-file",
            str(env_file),
        ]
    ) == 0
    formal_capture = capsys.readouterr()
    formal_payload = json.loads(formal_capture.out)
    assert formal_payload["denominator"] == 33
    assert "formal-file-secret" not in formal_capture.out + formal_capture.err

    monkeypatch.delenv("EXPERIMENT3_CLI_FILE_KEY")
    assert runner.main(
        [
            "resume",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(formal_output),
            "--env-file",
            str(env_file),
        ]
    ) == 0
    resume_capture = capsys.readouterr()
    resume_payload = json.loads(resume_capture.out)
    assert resume_payload["command"] == "resume"
    assert resume_payload["completed_cells"] == 31
    assert "formal-file-secret" not in resume_capture.out + resume_capture.err
    assert events == [
        (
            "preflight",
            {"root": str(tmp_path), "manifest_path": str(manifest_path)},
        ),
        (
            "formal",
            {
                "root": str(tmp_path),
                "manifest": manifest,
                "output_dir": str(formal_output),
            },
        ),
        (
            "resume",
            {
                "root": str(tmp_path),
                "manifest": manifest,
                "output_dir": str(formal_output),
            },
        ),
    ]
    assert credential_preflight_calls == [
        (manifest, str(tmp_path)),
        (manifest, str(tmp_path)),
    ]

    record_path = tmp_path / "record.json"
    record_path.write_text('{"denominator": 33}\n', encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "summarise_results",
        lambda record: {
            "experiment_id": runner.EXPERIMENT_ID,
            "denominator": record["denominator"],
            "configurations": {
                robot: {} for robot in runner.ROBOT_CONFIGURATIONS
            },
            "case_rows": [],
        },
    )
    summary_path = tmp_path / "summary.json"
    assert runner.main(
        [
            "summarise",
            "--record",
            str(record_path),
            "--output",
            str(summary_path),
        ]
    ) == 0
    summary_payload = json.loads(capsys.readouterr().out)
    assert summary_payload["configuration_count"] == 11
    assert json.loads(summary_path.read_text(encoding="utf-8"))["denominator"] == 33


def test_cli_runner_error_is_nonzero_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = Path(runner.__file__).with_name("manifest.json")
    monkeypatch.setattr(
        runner,
        "run_preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runner.Experiment3RunnerError("all-eleven evidence is incomplete")
        ),
    )
    rc = runner.main(
        [
            "preflight",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "all-eleven evidence is incomplete"


def test_resource_summary_retains_failed_call_time_without_inventing_usage() -> None:
    client = SimpleNamespace(
        calls=[{"status": "timeout", "elapsed_s": 1.25, "input_tokens": None}]
    )
    resources = runner._resource_summary(client, _model_pin())

    assert resources["call_count"] == 1
    assert resources["model_elapsed_time_s"] == 1.25
    assert resources["token_categories"]["input_tokens"] is None
    assert resources["token_observed_call_counts"]["input_tokens"] == 0
    assert resources["estimated_cost"]["amount"] is None
