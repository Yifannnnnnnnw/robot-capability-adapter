from __future__ import annotations

import copy
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from experiment.experiment3 import runner


def _model_pin() -> dict[str, Any]:
    return {
        "vendor": "Anthropic",
        "api_protocol": "openai-compatible",
        "model_id": runner.MODEL_ID,
        "revision": "test-sonnet-4-6-pin",
        "base_url": "https://model.invalid/v1",
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
        "endpoint_region": "test-region",
        "request_timeout_s": 120,
        "retry_policy": copy.deepcopy(runner.EXPECTED_RETRY_POLICY),
        "history_char_budget": 80_000,
        "credential_env": "TEST_MODEL_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
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
    manifest["runtime"] = {
        "producer_model": _model_pin(),
        "producer_transport": _transport(),
        "resources": {
            "development_probe": {
                "max_requests_per_stage": 12,
                "wall_timeout_s_per_request": 30,
                "max_output_chars_per_request": 12_000,
            },
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
        "reference_positive_controls": {
            robot: _evidence(
                tmp_path,
                f"reference-{index}",
                {
                    "artifact_type": "experiment3_reference_positive_control",
                    "robot_configuration_id": robot,
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


def test_checked_in_manifest_expands_exact_replicate_major_33_cells() -> None:
    cells = runner.expand_cells(runner.load_manifest())

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
        "final_capability_validation_passed": True,
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
            "TGCD": {"completed": True},
            "IVC": {"completed": True},
        },
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

def test_formal_dispatch_predeclares_rows_then_blocks_on_so101_reference_control(
    tmp_path: Path,
) -> None:
    client_calls: list[str] = []

    with pytest.raises(
        runner.Experiment3RunnerError,
        match="reference_positive_controls.robotstudio_so101 has not passed",
    ):
        runner.run_formal(
            Path(__file__).resolve().parents[2] / "autoadapter",
            manifest=runner.load_manifest(),
            output_dir=tmp_path / "blocked-run",
            client_factory=lambda *_: client_calls.append("called"),
            run_experiment_fn=lambda *_args, **_kwargs: {},
        )

    assert client_calls == []
    record = json.loads(
        (tmp_path / "blocked-run" / "experiment3_run_record.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["denominator"] == 33
    assert record["dispatch_started"] is False
    assert len(record["cells"]) == 33
    assert {row["status"] for row in record["cells"]} == {"predeclared"}


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


def test_formal_runner_uses_one_fresh_singleton_call_per_cell_and_retains_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _executable_manifest(tmp_path)
    run_calls: list[tuple[str, str]] = []
    clients: list[Any] = []
    hook_objects: list[Any] = []
    monkeypatch.setenv("TEST_MODEL_KEY", "manifest-pinned-secret")
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
                    "pass@0": not truthful_not_run,
                    "final_capability_validation_passed": not truthful_not_run,
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
        hooks_factory=hooks_factory,
        check_self_containment=False,
    )

    expected = [
        (replicate, robot)
        for replicate in runner.REPLICATE_IDS
        for robot in runner.ROBOT_CONFIGURATIONS
    ]
    assert run_calls == expected
    assert len(clients) == 33
    assert len({id(client) for client in clients}) == 33
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
    assert identity_failed["task_demo"] == {"status": "executed", "passed": True}
    assert identity_failed["resource_summary"]["producer"]["call_count"] == 1
    assert result["denominator"] == 33
    assert result["all_declared_cells_retained"] is True
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
    env_file = tmp_path / "company-api.env"
    env_file.write_text(
        "export EXPERIMENT3_CLI_FILE_KEY='formal-file-secret'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPERIMENT3_CLI_FILE_KEY", raising=False)

    def fake_preflight(root: str, **kwargs: Any) -> dict[str, Any]:
        events.append(("preflight", {"root": root, **kwargs}))
        return {
            "producer_model": {"model_id": runner.MODEL_ID},
            "readiness_evidence": {
                "packages": {robot: {} for robot in runner.ROBOT_CONFIGURATIONS},
                "reference_positive_controls": {
                    robot: {} for robot in runner.ROBOT_CONFIGURATIONS
                },
            },
            "current_package_check": {"package_check_passed": True},
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

    monkeypatch.setattr(runner, "run_preflight", fake_preflight)
    monkeypatch.setattr(runner, "run_formal", fake_formal)

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
    assert events == [
        (
            "preflight",
            {"root": str(tmp_path), "manifest_path": str(manifest_path)},
        ),
        (
            "formal",
            {
                "root": str(tmp_path),
                "manifest_path": str(manifest_path),
                "output_dir": str(formal_output),
            },
        ),
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
