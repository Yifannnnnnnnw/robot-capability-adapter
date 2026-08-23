from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from experiment.experiment2 import runner


TEST_GIT_COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def _committed_experiment2_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_current_git_commit", lambda _root: TEST_GIT_COMMIT)


def _model_pin(
    model_id: str, revision: str, *, max_output_tokens: int = 16_384
) -> dict[str, Any]:
    return {
        "vendor": "Anthropic",
        "api_protocol": "openai-compatible",
        "model_id": model_id,
        "revision": revision,
        "base_url": "https://model.invalid/v1",
        "context_window_tokens": 1_000_000,
        "max_output_tokens": max_output_tokens,
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


def _client(model: dict[str, Any], transport: dict[str, Any], role: str) -> Any:
    return SimpleNamespace(
        role=role,
        calls=[],
        config=SimpleNamespace(
            provider=model["vendor"],
            model=model["model_id"],
            base_url=model["base_url"],
            api_protocol=model["api_protocol"],
            thinking=model["thinking"],
            timeout_s=float(transport["request_timeout_s"]),
            max_tokens=model["max_output_tokens"],
            tool_history_mode=model["tool_history_mode"],
            history_char_budget=transport["history_char_budget"],
            auth_header=transport["auth_header"],
            auth_prefix=transport["auth_prefix"],
        ),
    )


def _evidence(
    tmp_path: Path, label: str, retained: dict[str, Any]
) -> dict[str, Any]:
    path = tmp_path / "evidence" / f"{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": True, **retained}) + "\n", encoding="utf-8")
    return {"path": str(path), "passed": True}


def _executable_manifest(tmp_path: Path) -> dict[str, Any]:
    manifest = runner.load_manifest()
    test_opus_id = runner.OPUS_MODEL_ID
    declared_opus = manifest["models"]["terminal_evolution"]
    declared_opus["exact_model_id"] = test_opus_id
    declared_opus["temperature"] = 0.0
    declared_opus["context_limit_tokens"] = 1_000_000
    declared_opus["max_output_tokens"] = 32_768
    for key in (
        "exact_model_id_status",
        "pre_dispatch_pin_required",
        "callable_identity_required",
        "unresolved_pin",
    ):
        declared_opus.pop(key, None)
    manifest["runtime"] = {
        "producer_model": _model_pin(runner.SONNET_MODEL_ID, "test-sonnet-pin"),
        "producer_transport": _transport(),
        "evolution_model": _model_pin(
            test_opus_id, "test-opus-pin", max_output_tokens=32_768
        ),
        "evolution_transport": _transport(),
        "resources": {
            "development_probe": {
                "max_requests_per_stage": runner.DRIVER_PROBE_CALLS_PER_STAGE,
                "max_complete_driver_checks": (
                    runner.COMPLETE_DRIVER_CHECKS_PER_STAGE
                ),
                "wall_timeout_s_per_request": 30,
                "max_output_chars_per_request": 12_000,
            },
            "validation": {"record_video": True, "worker_wall_timeout_s": 120},
        },
    }
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
                    "real_mujoco_evidence": "franka-recorder.json",
                },
            },
        },
    )
    manifest["readiness_evidence"] = {
        "package": _evidence(
            tmp_path,
            "package",
            {
                "artifact_type": "experiment3_package_check_evidence",
                "robot_configuration_ids": [runner.ROBOT_CONFIGURATION],
            },
        ),
        "reference_positive_control": _evidence(
            tmp_path,
            "reference",
            {
                "artifact_type": "experiment3_reference_positive_control",
                "robot_configuration_id": runner.ROBOT_CONFIGURATION,
            },
        ),
        "producer_model_canary": _evidence(
            tmp_path,
            "producer-model",
            {
                "artifact_type": "experiment3_exact_sonnet_runtime_pin_canary",
                "requested_model": runner.SONNET_MODEL_ID,
                "returned_model": runner.SONNET_MODEL_ID,
                "returned_model_matches_pin": True,
                "request_settings": {
                    "temperature": 0.0,
                    "context_limit_tokens": 1_000_000,
                    "max_output_tokens": 16_384,
                },
            },
        ),
        "evolution_model_canary": _evidence(
            tmp_path,
            "evolution-model",
            {
                "artifact_type": "experiment2_company_api_model_identity_canaries",
                "canaries": [
                    {
                        "role": "terminal_evolution",
                        "requested_model": runner.OPUS_MODEL_ID,
                        "returned_model": runner.OPUS_MODEL_ID,
                        "returned_model_matches_pin": True,
                        "structured_output_valid": True,
                    }
                ],
            },
        ),
        "evolution_contract": _evidence(
            tmp_path,
            "evolution-contract",
            {
                "artifact_type": "experiment2_opus_evolution_contract_canary",
                "requested_model": runner.OPUS_MODEL_ID,
                "returned_model": runner.OPUS_MODEL_ID,
                "returned_model_matches_pin": True,
                "contract": {
                    "allowed_model_fields": [
                        "observation",
                        "lesson",
                        "recommendation",
                        "scope",
                        "evidence",
                    ],
                    "returned_exact_five_field_object": True,
                    "framework_wrapper_model_authored": False,
                },
            },
        ),
        "candidate_isolation": copy.deepcopy(boundary),
        "canonical_physics": copy.deepcopy(boundary),
        "independent_harness": copy.deepcopy(boundary),
        "recorder": copy.deepcopy(boundary),
        "experience_boundary": _evidence(
            tmp_path,
            "experience-boundary",
            {
                "artifact_type": "experiment2_runtime_experience_boundary_checks",
                "covered_boundaries": [
                    "Evolution disabled creates no Opus client or review queue",
                    "exact Experience ID tracing into later TGCD and GENERATE",
                ],
            },
        ),
    }
    return manifest


def _source_pipeline_result(*, task_demo_executed: bool = True) -> dict[str, Any]:
    return {
        "run_id": runner.SOURCE_RUN_ID,
        "evolution_enabled": False,
        "evolution_model": None,
        "experience_input_ids": {runner.ROBOT_CONFIGURATION: []},
        "cells": [
            {
                "cell_id": runner.SOURCE_CELL_ID,
                "robot_configuration_id": runner.ROBOT_CONFIGURATION,
                "condition": runner.CONDITION,
                "attempt_count": 1,
                "pipeline_completed": True,
                "final_capability_validation_passed": True,
                "capability_validation_executed": True,
                "video_required": True,
                "video_complete": True,
                "capability_validation": {
                    "pipeline_completed": True,
                    "physical_validation_executed": True,
                    "validation_passed": True,
                    "video_required": True,
                    "video_complete": True,
                    "failure": None,
                },
                "task_demo_executed": task_demo_executed,
                "task_demo_passed": task_demo_executed,
                "task_demo_pipeline_completed": task_demo_executed,
                "task_demo_video_complete": task_demo_executed,
                "task_demo_task_counts": {
                    "passed": 5 if task_demo_executed else 0,
                    "total": 5 if task_demo_executed else 0,
                },
                "task_demo": (
                    {
                        "pipeline_completed": True,
                        "physical_validation_executed": True,
                        "validation_passed": True,
                        "video_required": True,
                        "video_complete": True,
                        "skipped": False,
                        "failure": None,
                    }
                    if task_demo_executed
                    else {
                        "pipeline_completed": False,
                        "physical_validation_executed": False,
                        "validation_passed": False,
                        "video_required": True,
                        "video_complete": False,
                        "skipped": True,
                        "skip_reason": "capability validation did not pass",
                        "failure": None,
                    }
                ),
                "outcomes": {
                    "TGCD": {"completed": True},
                    "IVC": {"completed": True},
                    "Evolution": None,
                },
            }
        ],
    }


def _proposal() -> dict[str, Any]:
    return {
        "non_blocking": True,
        "terminal_report_read": True,
        "evolution_attempted": True,
        "evolution_completed": True,
        "model_call_count": 1,
        "proposal_created": True,
        "proposal": {
            "observation": "The public terminal run passed.",
            "lesson": "Retain the public actuator envelope.",
            "recommendation": "Make that envelope explicit in later synthesis.",
            "scope": "robotstudio_so101",
            "evidence": ["public terminal capability and Task Demo verdicts"],
        },
        "current_run_unchanged": True,
        "failure": None,
    }


def _source_revision_evidence() -> dict[str, str]:
    return {
        "authority_revision": runner.AUTHORITY_REVISION,
        "manifest_revision": runner.MANIFEST_REVISION,
        "protocol_revision": runner.PROTOCOL_REVISION,
        "git_commit": TEST_GIT_COMMIT,
    }


def test_checked_in_manifest_fixes_two_roles_and_distinct_model_assignments() -> None:
    manifest = runner.validate_design_manifest(runner.load_manifest())

    assert manifest["run_roles"]["source"]["run_id"] == runner.SOURCE_RUN_ID
    assert manifest["run_roles"]["later"]["run_id"] == runner.LATER_RUN_ID
    assert manifest["models"]["producer"]["exact_model_id"] == runner.SONNET_MODEL_ID
    assert manifest["models"]["terminal_evolution"]["exact_model_id"] == runner.OPUS_MODEL_ID
    assert manifest["denominator"]["declared_closure_runs"] == 2
    assert manifest["denominator"]["improvement_claim"] is False
    assert (
        manifest["runtime"]["resources"]["development_probe"][
            "max_requests_per_stage"
        ]
        == runner.DRIVER_PROBE_CALLS_PER_STAGE
    )
    assert (
        manifest["runtime"]["resources"]["development_probe"][
            "max_complete_driver_checks"
        ]
        == runner.COMPLETE_DRIVER_CHECKS_PER_STAGE
    )

    drifted = copy.deepcopy(manifest)
    drifted["models"]["terminal_evolution"]["max_output_tokens"] = 16_384
    with pytest.raises(runner.Experiment2RunnerError, match="32,768"):
        runner.validate_design_manifest(drifted)


def test_identity_and_task_demo_postchecks_reject_false_success() -> None:
    client = SimpleNamespace(
        calls=[
            {
                "status": "success",
                "http_status": 200,
                "returned_model": runner.SONNET_MODEL_ID,
            },
            {"status": "success", "http_status": 200, "returned_model": None},
        ]
    )
    with pytest.raises(runner.Experiment2RunnerError, match="omitted"):
        runner._assert_returned_identity(
            client,
            role="Producer",
            expected_model_id=runner.SONNET_MODEL_ID,
        )

    with pytest.raises(runner.Experiment2RunnerError, match="requires an executed"):
        runner._task_demo_terminal(
            {
                "final_capability_validation_passed": True,
                "task_demo_executed": False,
                "task_demo": {
                    "skipped": True,
                    "skip_reason": "no final driver was admitted",
                },
            }
        )


def test_checked_in_manifest_has_executable_readiness_and_model_pins() -> None:
    manifest = runner.load_manifest()
    assert manifest["authority_revision"] == runner.AUTHORITY_REVISION
    assert manifest["manifest_revision"] == runner.MANIFEST_REVISION
    assert manifest["protocol_revision"] == runner.PROTOCOL_REVISION
    preflight = runner.validate_executable_preflight(
        manifest,
        mainline_root=Path(__file__).resolve().parents[2] / "autoadapter",
    )

    assert preflight["producer_model"]["model_id"] == runner.SONNET_MODEL_ID
    assert preflight["producer_model"]["temperature"] == 0.0
    assert preflight["producer_model"]["max_output_tokens"] == 16_384
    assert preflight["evolution_model"]["model_id"] == runner.OPUS_MODEL_ID
    assert preflight["evolution_model"]["temperature"] == 0.0
    assert preflight["evolution_model"]["max_output_tokens"] == 32_768
    assert (
        preflight["resources"]["development_probe"]["max_requests_per_stage"]
        == runner.DRIVER_PROBE_CALLS_PER_STAGE
    )
    assert (
        preflight["resources"]["development_probe"][
            "max_complete_driver_checks"
        ]
        == runner.COMPLETE_DRIVER_CHECKS_PER_STAGE
    )

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["resources"]["development_probe"][
        "max_requests_per_stage"
    ] = 14
    with pytest.raises(runner.Experiment2RunnerError, match="reserve 25"):
        runner.validate_executable_preflight(
            drifted,
            mainline_root=Path(__file__).resolve().parents[2] / "autoadapter",
        )

    drifted = copy.deepcopy(manifest)
    drifted["runtime"]["resources"]["development_probe"][
        "max_complete_driver_checks"
    ] = 1
    with pytest.raises(runner.Experiment2RunnerError, match="must be 2"):
        runner.validate_executable_preflight(
            drifted,
            mainline_root=Path(__file__).resolve().parents[2] / "autoadapter",
        )

    readiness = preflight["readiness_evidence"]
    assert set(readiness) == {
        "package",
        "reference_positive_control",
        "candidate_isolation",
        "canonical_physics",
        "independent_harness",
        "recorder",
        "producer_model_canary",
        "evolution_model_canary",
        "evolution_contract",
        "experience_boundary",
    }
    assert all(item["passed"] is True for item in readiness.values())
    assert readiness["reference_positive_control"]["artifact_type"].startswith(
        "experiment3_reference_positive_control"
    )
    assert (
        readiness["producer_model_canary"]["artifact_type"]
        == "experiment3_exact_sonnet_runtime_pin_canary"
    )
    assert (
        readiness["evolution_model_canary"]["artifact_type"]
        == "experiment2_company_api_model_identity_canaries"
    )


def test_source_requires_committed_revision_before_client_construction(
    tmp_path: Path,
) -> None:
    clients: list[str] = []
    output = tmp_path / "uncommitted-source"

    with pytest.raises(runner.Experiment2RunnerError, match="full Git commit"):
        runner.run_source(
            tmp_path,
            manifest=_executable_manifest(tmp_path),
            output_dir=output,
            manual_launch_event="operator-source-launch",
            client_factory=lambda role, *_args: clients.append(role),
            run_experiment_fn=lambda *_args, **_kwargs: {},
            git_commit_fn=lambda _root: "",
        )

    assert clients == []
    assert not output.exists()


def test_readiness_rejects_passed_reference_evidence_for_another_robot(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    manifest["readiness_evidence"]["reference_positive_control"] = _evidence(
        tmp_path,
        "wrong-reference",
        {
            "artifact_type": "experiment3_reference_positive_control",
            "robot_configuration_id": "franka_panda",
        },
    )

    with pytest.raises(runner.Experiment2RunnerError, match="does not prove SO-101"):
        runner.validate_executable_preflight(manifest, mainline_root=tmp_path)


def test_source_runs_sonnet_to_task_demo_before_one_distinct_opus_call_and_no_overwrite(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    events: list[str] = []

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        events.append(f"client:{role}")
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        events.append("run:sonnet")
        config = kwargs["config"]
        assert kwargs["run_id"] == runner.SOURCE_RUN_ID
        assert kwargs["producer_client"].role == "producer"
        assert config["robots"] == [runner.ROBOT_CONFIGURATION]
        assert config["generation_conditions"] == [runner.CONDITION]
        assert config["experience"]["input"] == []
        assert config["evolution"] == {"enabled": False}
        assert kwargs["skip_reference_calibration"] is True
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {
                "returned_model": runner.SONNET_MODEL_ID,
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 3,
                "cache_write_tokens": 4,
                "reasoning_tokens": 5,
                "total_tokens": 120,
                "elapsed_s": 0.5,
            }
        )
        return _source_pipeline_result()

    def fake_evolution(client: Any, cell: dict[str, Any]) -> dict[str, Any]:
        events.append("run:opus")
        assert client.role == "terminal_evolution"
        assert cell["task_demo_executed"] is True
        client.calls.append(
            {
                "returned_model": client.config.model,
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 15,
                "elapsed_s": 0.25,
            }
        )
        return _proposal()

    output = tmp_path / "source"
    result = runner.run_source(
        tmp_path,
        manifest=manifest,
        output_dir=output,
        manual_launch_event="operator-source-launch-1",
        client_factory=client_factory,
        run_experiment_fn=fake_run,
        evolution_runner_fn=fake_evolution,
        check_self_containment=False,
    )

    assert events == [
        "client:producer",
        "run:sonnet",
        "client:terminal_evolution",
        "run:opus",
    ]
    assert result["cells"][0]["outcomes"]["Evolution"]["proposal_created"] is True
    assert result["experiment2_revision_evidence"] == _source_revision_evidence()
    for field, value in _source_revision_evidence().items():
        assert result[field] == value
        assert result["cells"][0][field] == value
    resources = result["resource_summary"]
    assert resources["runner_wall_time_s"] >= 0
    assert resources["producer"]["call_count"] == 1
    assert resources["producer"]["token_categories"]["total_tokens"] == 120
    assert resources["producer"]["model_elapsed_time_s"] == 0.5
    assert resources["producer"]["estimated_cost"]["amount"] == pytest.approx(
        0.00014
    )
    assert resources["terminal_evolution"]["call_count"] == 1
    assert resources["terminal_evolution"]["token_categories"]["total_tokens"] == 15
    assert resources["terminal_evolution"]["model_elapsed_time_s"] == 0.25
    assert resources["terminal_evolution"]["estimated_cost"]["is_estimate"] is True
    queue = json.loads(
        (output / "experience_review_queue.json").read_text(encoding="utf-8")
    )
    assert queue["records"][0]["source_robot"] == runner.ROBOT_CONFIGURATION
    assert queue["records"][0]["source_condition"] == runner.CONDITION
    assert queue["records"][0]["source_outcome"] == "positive"
    assert queue["records"][0]["generation_condition"] == runner.CONDITION
    assert queue["records"][0]["terminal_outcome_label"] == "positive"
    assert queue["records"][0]["disposition"] is None
    for field, value in _source_revision_evidence().items():
        assert queue[field] == value

    with pytest.raises(runner.Experiment2RunnerError, match="never overwritten"):
        runner.run_source(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            manual_launch_event="operator-source-launch-2",
            client_factory=client_factory,
            run_experiment_fn=fake_run,
            evolution_runner_fn=fake_evolution,
        )
    assert events.count("run:sonnet") == 1


def test_source_does_not_construct_or_call_opus_when_task_demo_was_not_reached(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    roles: list[str] = []

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        roles.append(role)
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        return _source_pipeline_result(task_demo_executed=False)

    with pytest.raises(runner.Experiment2RunnerError, match="Opus was not called"):
        runner.run_source(
            tmp_path,
            manifest=manifest,
            output_dir=tmp_path / "no-demo-source",
            manual_launch_event="operator-source-launch",
            client_factory=client_factory,
            run_experiment_fn=fake_run,
            evolution_runner_fn=lambda *_: (_ for _ in ()).throw(
                AssertionError("Opus must not be called")
            ),
        )

    assert roles == ["producer"]
    assert not (tmp_path / "no-demo-source" / "experience_review_queue.json").exists()
    retained = json.loads(
        (tmp_path / "no-demo-source" / "experiment_report.json").read_text()
    )
    assert retained["experiment2_runner_status"] == "failed"
    assert retained["experiment2_runner_failure"]["stage"] == "source-result-postcheck"
    assert retained["resource_summary"]["producer"]["call_count"] == 1


def test_source_public_function_builds_clients_from_only_the_pinned_credential_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _executable_manifest(tmp_path)
    monkeypatch.setenv("TEST_MODEL_KEY", "manifest-pinned-secret")
    monkeypatch.setenv("AUTOADAPTER_MODEL_API_KEY", "must-not-be-read")
    observed_clients: list[Any] = []

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        client = kwargs["producer_client"]
        observed_clients.append(client)
        assert client.config.api_key == "manifest-pinned-secret"
        Path(kwargs["output_dir"]).mkdir(parents=True)
        client.calls.append({"returned_model": runner.SONNET_MODEL_ID})
        return _source_pipeline_result()

    def fake_evolution(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        observed_clients.append(client)
        assert client.config.api_key == "manifest-pinned-secret"
        client.calls.append({"returned_model": client.config.model})
        return _proposal()

    result = runner.run_source(
        tmp_path,
        manifest=manifest,
        output_dir=tmp_path / "default-client-source",
        manual_launch_event="operator-source-launch",
        run_experiment_fn=fake_run,
        evolution_runner_fn=fake_evolution,
    )

    assert len(observed_clients) == 2
    assert observed_clients[0] is not observed_clients[1]
    assert observed_clients[0].config.model == runner.SONNET_MODEL_ID
    assert observed_clients[1].config.model == manifest["models"]["terminal_evolution"]["exact_model_id"]
    producer_resources = result["resource_summary"]["producer"]
    evolution_resources = result["resource_summary"]["terminal_evolution"]
    assert producer_resources["call_count"] == 1
    assert producer_resources["token_categories"]["input_tokens"] is None
    assert producer_resources["token_categories"]["output_tokens"] is None
    assert producer_resources["token_observed_call_counts"]["input_tokens"] == 0
    assert producer_resources["estimated_cost"]["amount"] is None
    assert evolution_resources["call_count"] == 1
    assert evolution_resources["token_categories"]["input_tokens"] is None
    assert evolution_resources["estimated_cost"]["amount"] is None


def test_source_allows_one_logical_opus_turn_with_one_bounded_physical_retry(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        return _source_pipeline_result()

    def retried_opus_turn(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        client.calls.extend(
            [
                {
                    "status": "http_error",
                    "http_status": 503,
                    "returned_model": None,
                },
                {
                    "status": "success",
                    "http_status": 200,
                    "returned_model": client.config.model,
                },
            ]
        )
        return _proposal()

    output = tmp_path / "two-opus-source"
    result = runner.run_source(
        tmp_path,
        manifest=manifest,
        output_dir=output,
        manual_launch_event="operator-source-launch",
        client_factory=client_factory,
        run_experiment_fn=fake_run,
        evolution_runner_fn=retried_opus_turn,
    )

    assert result["experiment2_terminal_evolution"]["logical_call_count"] == 1
    assert result["experiment2_terminal_evolution"]["physical_request_count"] == 2
    assert result["experiment2_terminal_evolution"]["returned_identity_verified"] is True
    assert (output / "experience_review_queue.json").is_file()


def test_source_retains_terminal_opus_identity_failure_before_blocking_review(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"status": "success", "returned_model": runner.SONNET_MODEL_ID}
        )
        return _source_pipeline_result()

    def wrong_identity(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        client.calls.append(
            {"status": "success", "returned_model": "wrong.opus.identity"}
        )
        return _proposal()

    output = tmp_path / "wrong-opus-identity"
    with pytest.raises(
        runner.Experiment2RunnerError,
        match="terminal Evolution failure was retained",
    ):
        runner.run_source(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            manual_launch_event="operator-source-launch",
            client_factory=client_factory,
            run_experiment_fn=fake_run,
            evolution_runner_fn=wrong_identity,
        )

    report = json.loads((output / "experiment_report.json").read_text())
    evolution = report["cells"][0]["outcomes"]["Evolution"]
    assert evolution["proposal"] is None
    assert evolution["proposal_created"] is False
    assert evolution["failure"]["type"] == "Experiment2TerminalEvolutionError"
    assert not (output / "experience_review_queue.json").exists()
    assert report["experiment2_runner_status"] == "failed"
    assert report["experiment2_runner_failure"]["stage"] == "terminal-evolution-postcheck"
    assert report["resource_summary"]["terminal_evolution"]["call_count"] == 1


def test_truthful_task_demo_not_run_is_terminal_and_can_yield_negative_experience(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    roles: list[str] = []

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        roles.append(role)
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        result = _source_pipeline_result(task_demo_executed=False)
        result["cells"][0]["final_capability_validation_passed"] = False
        result["cells"][0]["capability_validation"]["validation_passed"] = False
        result["cells"][0]["task_demo"] = {
            "skipped": True,
            "skip_reason": "no final driver was admitted",
        }
        return result

    def fake_evolution(client: Any, cell: dict[str, Any]) -> dict[str, Any]:
        assert cell["task_demo"]["skip_reason"]
        client.calls.append({"returned_model": client.config.model})
        return _proposal()

    output = tmp_path / "not-run-source"
    runner.run_source(
        tmp_path,
        manifest=manifest,
        output_dir=output,
        manual_launch_event="operator-source-launch",
        client_factory=client_factory,
        run_experiment_fn=fake_run,
        evolution_runner_fn=fake_evolution,
    )

    queue = json.loads(
        (output / "experience_review_queue.json").read_text(encoding="utf-8")
    )
    assert roles == ["producer", "terminal_evolution"]
    assert queue["records"][0]["source_outcome"] == "negative"
    assert queue["records"][0]["terminal_outcome_label"] == "negative"


def test_indeterminate_source_retains_opus_but_writes_no_review_queue(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)
    roles: list[str] = []

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        roles.append(role)
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        result = _source_pipeline_result(task_demo_executed=False)
        cell = result["cells"][0]
        cell["attempt_count"] = 0
        cell["pipeline_completed"] = False
        cell["final_capability_validation_passed"] = False
        cell["capability_validation_executed"] = False
        cell["video_complete"] = False
        cell["capability_validation"] = {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "video_required": True,
            "video_complete": False,
            "failure": {"stage": "generate", "type": "GenerationError"},
        }
        return result

    def fake_evolution(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        client.calls.append({"returned_model": client.config.model})
        return _proposal()

    output = tmp_path / "indeterminate-source"
    with pytest.raises(
        runner.Experiment2RunnerError,
        match="no human-review queue was written",
    ):
        runner.run_source(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            manual_launch_event="operator-source-launch",
            client_factory=client_factory,
            run_experiment_fn=fake_run,
            evolution_runner_fn=fake_evolution,
        )

    assert roles == ["producer", "terminal_evolution"]
    assert not (output / "experience_review_queue.json").exists()
    report = json.loads((output / "experiment_report.json").read_text())
    assert report["experiment2_runner_status"] == "failed"
    assert (
        report["experiment2_runner_failure"]["stage"]
        == "source-review-eligibility"
    )
    proposal = report["cells"][0]["outcomes"]["Evolution"]["proposal"]
    assert set(proposal) == {
        "observation",
        "lesson",
        "recommendation",
        "scope",
        "evidence",
    }


def test_no_reusable_lesson_retains_opus_but_writes_no_review_queue(
    tmp_path: Path,
) -> None:
    manifest = _executable_manifest(tmp_path)

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        return _client(model, transport, role)

    def fake_run(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        return _source_pipeline_result()

    def no_lesson(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        client.calls.append({"returned_model": client.config.model})
        outcome = _proposal()
        outcome["proposal_created"] = False
        outcome["proposal"] = None
        outcome["no_reusable_lesson"] = True
        return outcome

    output = tmp_path / "no-lesson-source"
    with pytest.raises(runner.Experiment2RunnerError, match="no reusable"):
        runner.run_source(
            tmp_path,
            manifest=manifest,
            output_dir=output,
            manual_launch_event="operator-source-launch",
            client_factory=client_factory,
            run_experiment_fn=fake_run,
            evolution_runner_fn=no_lesson,
        )

    assert not (output / "experience_review_queue.json").exists()
    report = json.loads((output / "experiment_report.json").read_text())
    evolution = report["cells"][0]["outcomes"]["Evolution"]
    assert evolution["evolution_completed"] is True
    assert evolution["no_reusable_lesson"] is True
    assert evolution["proposal"] is None
    assert (
        report["experiment2_runner_failure"]["stage"]
        == "source-review-eligibility"
    )


def test_indeterminate_legacy_queue_cannot_be_accepted_or_rejected(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "legacy-indeterminate-source"
    source_dir.mkdir()
    queue = {
        "artifact_type": "experience_review_queue",
        "experiment_id": runner.EXPERIMENT_ID,
        "run_id": runner.SOURCE_RUN_ID,
        **_source_revision_evidence(),
        "records": [
            {
                "source_run_id": runner.SOURCE_RUN_ID,
                "cell_id": runner.SOURCE_CELL_ID,
                "robot_configuration_id": runner.ROBOT_CONFIGURATION,
                "source_robot": runner.ROBOT_CONFIGURATION,
                "generation_condition": runner.CONDITION,
                "terminal_outcome_label": "indeterminate",
                "evolution": {
                    "proposal_created": True,
                    "proposal": _proposal()["proposal"],
                },
                "disposition": None,
                "reason": None,
            }
        ],
    }
    queue_path = source_dir / "experience_review_queue.json"
    original = json.dumps(queue, indent=2) + "\n"
    queue_path.write_text(original, encoding="utf-8")

    for disposition in ("accept", "reject"):
        with pytest.raises(
            runner.Experiment2RunnerError,
            match="indeterminate",
        ):
            runner.review_source(
                source_dir,
                disposition=disposition,
                reason="A nonempty reason must not make this queue reviewable.",
            )

    assert queue_path.read_text(encoding="utf-8") == original
    assert not (source_dir / "experience_reviewed_queue.json").exists()
    assert not (source_dir / "experience_snapshot.json").exists()


def test_retained_pre_fix_source_queue_is_never_reviewable(tmp_path: Path) -> None:
    source_dir = tmp_path / "pre-fix-source"
    source_dir.mkdir()
    queue = {
        "artifact_type": "experience_review_queue",
        "experiment_id": runner.EXPERIMENT_ID,
        "run_id": runner.PRE_FIX_SOURCE_RUN_ID,
        "records": [],
    }
    queue_path = source_dir / "experience_review_queue.json"
    original = json.dumps(queue) + "\n"
    queue_path.write_text(original, encoding="utf-8")

    for disposition in ("accept", "reject"):
        with pytest.raises(runner.Experiment2RunnerError, match="pre-fix"):
            runner.review_source(
                source_dir,
                disposition=disposition,
                reason="Historical evidence must remain immutable.",
            )

    assert queue_path.read_text(encoding="utf-8") == original
    assert not (source_dir / "experience_reviewed_queue.json").exists()
    assert not (source_dir / "experience_snapshot.json").exists()


def test_accept_freezes_exact_id_and_manual_later_consumes_it_without_evolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _executable_manifest(tmp_path)
    source_dir = tmp_path / "source"

    def client_factory(
        role: str, model: dict[str, Any], transport: dict[str, Any]
    ) -> Any:
        return _client(model, transport, role)

    def fake_source(_root: Path, **kwargs: Any) -> dict[str, Any]:
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {"returned_model": runner.SONNET_MODEL_ID}
        )
        return _source_pipeline_result()

    def fake_evolution(client: Any, _cell: dict[str, Any]) -> dict[str, Any]:
        client.calls.append({"returned_model": client.config.model})
        return _proposal()

    runner.run_source(
        tmp_path,
        manifest=manifest,
        output_dir=source_dir,
        manual_launch_event="operator-source-launch",
        client_factory=client_factory,
        run_experiment_fn=fake_source,
        evolution_runner_fn=fake_evolution,
    )
    review = runner.review_source(
        source_dir,
        disposition="accept",
        reason="The proposal is public, bounded, and supported by retained evidence.",
    )
    snapshot_path = Path(str(review["snapshot_path"]))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["records"][0]["experience_id"] == runner.EXPERIENCE_ID
    assert snapshot["records"][0]["review_reason"].startswith("The proposal")
    assert snapshot["source_revision_evidence"] == _source_revision_evidence()
    for field, value in _source_revision_evidence().items():
        assert snapshot[field] == value

    tampered_snapshot = copy.deepcopy(snapshot)
    tampered_snapshot["source_revision_evidence"]["git_commit"] = "b" * 40
    tampered_path = tmp_path / "tampered-experience-snapshot.json"
    tampered_path.write_text(json.dumps(tampered_snapshot), encoding="utf-8")
    tampered_clients: list[str] = []
    with pytest.raises(runner.Experiment2RunnerError, match="lineage is incomplete"):
        runner.run_later(
            tmp_path,
            manifest=manifest,
            snapshot_path=tampered_path,
            output_dir=tmp_path / "later-tampered-lineage",
            manual_launch_event="operator-later-launch-tampered",
            client_factory=lambda role, *_args: tampered_clients.append(role),
            run_experiment_fn=lambda *_args, **_kwargs: {},
        )
    assert tampered_clients == []

    blocked_clients: list[str] = []
    blocked_output = tmp_path / "later-uncommitted"
    with pytest.raises(runner.Experiment2RunnerError, match="full Git commit"):
        runner.run_later(
            tmp_path,
            manifest=manifest,
            snapshot_path=snapshot_path,
            output_dir=blocked_output,
            manual_launch_event="operator-later-launch-uncommitted",
            client_factory=lambda role, *_args: blocked_clients.append(role),
            run_experiment_fn=lambda *_args, **_kwargs: {},
            git_commit_fn=lambda _root: "",
        )
    assert blocked_clients == []
    assert not blocked_output.exists()

    monkeypatch.setenv("TEST_MODEL_KEY", "manifest-pinned-secret")
    later_clients: list[Any] = []

    def fake_later(_root: Path, **kwargs: Any) -> dict[str, Any]:
        config = kwargs["config"]
        later_clients.append(kwargs["producer_client"])
        assert kwargs["producer_client"].config.api_key == "manifest-pinned-secret"
        assert kwargs["run_id"] == runner.LATER_RUN_ID
        assert config["experience"]["input"]["records"][0]["experience_id"] == runner.EXPERIENCE_ID
        assert config["evolution"] == {"enabled": False}
        Path(kwargs["output_dir"]).mkdir(parents=True)
        kwargs["producer_client"].calls.append(
            {
                "returned_model": runner.SONNET_MODEL_ID,
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
                "elapsed_s": 0.1,
            }
        )
        return {
            "run_id": runner.LATER_RUN_ID,
            "evolution_enabled": False,
            "evolution_model": None,
            "experience_input_ids": {
                runner.ROBOT_CONFIGURATION: [runner.EXPERIENCE_ID]
            },
            "cells": [
                {
                    "cell_id": runner.SOURCE_CELL_ID,
                    "robot_configuration_id": runner.ROBOT_CONFIGURATION,
                    "condition": runner.CONDITION,
                    "attempt_count": 1,
                    "final_capability_validation_passed": True,
                    "capability_validation_executed": True,
                    "video_required": True,
                    "video_complete": True,
                    "task_demo_executed": True,
                    "task_demo_passed": True,
                    "task_demo_video_complete": True,
                    "task_demo_task_counts": {"passed": 5, "total": 5},
                    "outcomes": {
                        "TGCD": {
                            "completed": True,
                            "experience_ids": [runner.EXPERIENCE_ID],
                        },
                        "IVC": {"completed": True},
                        "GENERATE": {
                            "eligible_experience_ids": [runner.EXPERIENCE_ID]
                        },
                        "Evolution": None,
                    },
                }
            ],
        }

    result = runner.run_later(
        tmp_path,
        manifest=manifest,
        snapshot_path=snapshot_path,
        output_dir=tmp_path / "later",
        manual_launch_event="operator-later-launch",
        run_experiment_fn=fake_later,
    )

    assert len(later_clients) == 1
    assert result["experiment2_loaded_experience_id"] == runner.EXPERIENCE_ID
    assert result["experiment2_experience_load"]["experience_id"] == runner.EXPERIENCE_ID
    assert result["experiment2_experience_load"]["snapshot_id"] == snapshot["snapshot_id"]
    assert result["experiment2_experience_load"]["source_run_id"] == runner.SOURCE_RUN_ID
    assert (
        result["experiment2_experience_load"]["source_revision_evidence"]
        == _source_revision_evidence()
    )
    assert result["experiment2_experience_load"]["manual_launch_event"] == "operator-later-launch"
    assert result["experiment2_experience_load"]["loaded_at_utc"].endswith("Z")
    assert result["resource_summary"]["producer"]["call_count"] == 1
    assert result["resource_summary"]["producer"]["token_categories"]["total_tokens"] == 60
    assert result["resource_summary"]["runner_wall_time_s"] >= 0
    assert result["experiment2_revision_evidence"] == _source_revision_evidence()
    for field, value in _source_revision_evidence().items():
        assert result["cells"][0][field] == value
    assert not (tmp_path / "later" / "experience_review_queue.json").exists()

    def wrong_identity_later(root: Path, **kwargs: Any) -> dict[str, Any]:
        value = fake_later(root, **kwargs)
        kwargs["producer_client"].calls[-1]["returned_model"] = "wrong.model"
        return value

    with pytest.raises(runner.Experiment2RunnerError, match="was retained"):
        runner.run_later(
            tmp_path,
            manifest=manifest,
            snapshot_path=snapshot_path,
            output_dir=tmp_path / "later-wrong-identity",
            manual_launch_event="operator-later-launch-failed",
            run_experiment_fn=wrong_identity_later,
        )
    failed = json.loads(
        (tmp_path / "later-wrong-identity" / "experiment_report.json").read_text()
    )
    assert failed["experiment2_runner_status"] == "failed"
    assert failed["experiment2_runner_failure"]["stage"] == "later-result-postcheck"
    assert failed["resource_summary"]["producer"]["call_count"] == 1


def test_reject_blocks_later_and_null_proposal_cannot_be_reviewed(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "rejected-source"
    source_dir.mkdir()
    queue = {
        "artifact_type": "experience_review_queue",
        "experiment_id": runner.EXPERIMENT_ID,
        "run_id": runner.SOURCE_RUN_ID,
        **_source_revision_evidence(),
        "records": [
            {
                "source_run_id": runner.SOURCE_RUN_ID,
                "cell_id": runner.SOURCE_CELL_ID,
                "robot_configuration_id": runner.ROBOT_CONFIGURATION,
                "generation_condition": runner.CONDITION,
                "source_robot": runner.ROBOT_CONFIGURATION,
                "source_condition": runner.CONDITION,
                "source_outcome": "negative",
                "outcome_label": "negative",
                "terminal_outcome_label": "negative",
                "evolution": {
                    "proposal_created": True,
                    "proposal": _proposal()["proposal"],
                },
                "disposition": None,
                "reason": None,
            }
        ],
    }
    (source_dir / "experience_review_queue.json").write_text(
        json.dumps(queue), encoding="utf-8"
    )
    review = runner.review_source(
        source_dir,
        disposition="reject",
        reason="The proposed public lesson is too narrow to reuse.",
    )
    assert review["later_run_eligible"] is False
    assert review["snapshot_path"] is None

    calls: list[str] = []
    with pytest.raises(runner.Experiment2RunnerError, match="accepted frozen"):
        runner.run_later(
            tmp_path,
            manifest=_executable_manifest(tmp_path),
            snapshot_path=review["reviewed_queue_path"],
            output_dir=tmp_path / "blocked-later",
            manual_launch_event="operator-later-launch",
            client_factory=lambda role, *_: calls.append(role),
            run_experiment_fn=lambda *_args, **_kwargs: {},
        )
    assert calls == []

    null_dir = tmp_path / "null-source"
    null_dir.mkdir()
    null_queue = copy.deepcopy(queue)
    null_queue["records"][0]["evolution"] = {
        "proposal_created": False,
        "no_reusable_lesson": True,
        "proposal": None,
    }
    (null_dir / "experience_review_queue.json").write_text(
        json.dumps(null_queue), encoding="utf-8"
    )
    for disposition in ("accept", "reject"):
        with pytest.raises(runner.Experiment2RunnerError, match="no reusable"):
            runner.review_source(
                null_dir,
                disposition=disposition,
                reason="A null proposal cannot enter human review.",
            )
    assert not (null_dir / "experience_reviewed_queue.json").exists()
    assert not (null_dir / "experience_snapshot.json").exists()


def test_cli_keeps_source_review_and_later_as_separate_explicit_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = Path(runner.__file__).with_name("manifest.json")
    assert runner.main(["design-check", "--manifest", str(manifest_path)]) == 0
    design = json.loads(capsys.readouterr().out)
    assert design["ok"] is True
    assert design["source_run_id"] == runner.SOURCE_RUN_ID
    assert design["later_run_id"] == runner.LATER_RUN_ID

    events: list[tuple[str, dict[str, Any]]] = []
    first_env = tmp_path / "first.env"
    second_env = tmp_path / "second.env"
    first_env.write_text(
        'EXPERIMENT2_CLI_FILE_KEY="file-secret-one"\n'
        "EXPERIMENT2_CLI_EXISTING=file-must-not-replace\n",
        encoding="utf-8",
    )
    second_env.write_text(
        "EXPERIMENT2_CLI_FILE_KEY=file-secret-two\n", encoding="utf-8"
    )
    monkeypatch.delenv("EXPERIMENT2_CLI_FILE_KEY", raising=False)
    monkeypatch.setenv("EXPERIMENT2_CLI_EXISTING", "environment-wins")

    def fake_source(root: str, **kwargs: Any) -> dict[str, Any]:
        assert os.environ["EXPERIMENT2_CLI_FILE_KEY"] == "file-secret-one"
        assert os.environ["EXPERIMENT2_CLI_EXISTING"] == "environment-wins"
        events.append(("source", {"root": root, **kwargs}))
        return {
            "experiment2_task_demo_stage": {"status": "executed", "passed": True},
            "resource_summary": {
                "producer": {"call_count": 1},
                "terminal_evolution": {"call_count": 1},
            },
        }

    def fake_review(source_output: str, **kwargs: Any) -> dict[str, Any]:
        events.append(("review", {"source_output": source_output, **kwargs}))
        return {
            "disposition": kwargs["disposition"],
            "reason": kwargs["reason"],
            "reviewed_queue_path": str(tmp_path / "reviewed.json"),
            "snapshot_path": str(tmp_path / "snapshot.json"),
            "later_run_eligible": True,
        }

    def fake_later(root: str, **kwargs: Any) -> dict[str, Any]:
        events.append(("later", {"root": root, **kwargs}))
        return {
            "experiment2_loaded_experience_id": runner.EXPERIENCE_ID,
            "experiment2_task_demo_stage": {"status": "executed", "passed": False},
            "resource_summary": {"producer": {"call_count": 1}},
        }

    monkeypatch.setattr(runner, "run_source", fake_source)
    monkeypatch.setattr(runner, "review_source", fake_review)
    monkeypatch.setattr(runner, "run_later", fake_later)
    monkeypatch.setenv("TEST_MODEL_KEY", "must-never-be-printed")

    source_output = tmp_path / "source-cli"
    assert runner.main(
        [
            "source",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(source_output),
            "--manual-event",
            "operator-source-launch",
            "--env-file",
            str(first_env),
            "--env-file",
            str(second_env),
        ]
    ) == 0
    source_capture = capsys.readouterr()
    source_payload = json.loads(source_capture.out)
    assert source_payload["command"] == "source"
    assert events == [
        (
            "source",
            {
                "root": str(tmp_path),
                "manifest_path": str(manifest_path),
                "output_dir": str(source_output),
                "manual_launch_event": "operator-source-launch",
            },
        )
    ]
    printed = source_capture.out + source_capture.err
    assert "must-never-be-printed" not in printed
    assert "file-secret-one" not in printed
    assert "file-secret-two" not in printed

    assert runner.main(
        [
            "review",
            "--source-output",
            str(source_output),
            "--disposition",
            "accept",
            "--reason",
            "bounded public proposal",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["command"] == "review"
    assert [event[0] for event in events] == ["source", "review"]

    assert runner.main(
        [
            "later",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--snapshot",
            str(tmp_path / "snapshot.json"),
            "--output",
            str(tmp_path / "later-cli"),
            "--manual-event",
            "operator-later-launch",
        ]
    ) == 0
    later_payload = json.loads(capsys.readouterr().out)
    assert later_payload["experience_id"] == runner.EXPERIENCE_ID
    assert [event[0] for event in events] == ["source", "review", "later"]


def test_cli_returns_nonzero_secret_free_json_for_runner_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = Path(runner.__file__).with_name("manifest.json")
    monkeypatch.setattr(
        runner,
        "run_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runner.Experiment2RunnerError("formal source pin is incomplete")
        ),
    )
    rc = runner.main(
        [
            "source",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
            "--output",
            str(tmp_path / "blocked"),
            "--manual-event",
            "operator-source-launch",
        ]
    )
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert rc == 2
    assert captured.out == ""
    assert error == {
        "command": "source",
        "error": "formal source pin is incomplete",
        "error_type": "Experiment2RunnerError",
        "ok": False,
    }


def test_resource_summary_retains_failed_call_time_without_inventing_usage() -> None:
    client = SimpleNamespace(
        calls=[{"status": "http_error", "elapsed_s": 0.4, "http_status": 503}]
    )
    resources = runner._resource_summary(
        client, _model_pin(runner.SONNET_MODEL_ID, "failed-call-pin")
    )

    assert resources["call_count"] == 1
    assert resources["model_elapsed_time_s"] == 0.4
    assert resources["token_categories"]["total_tokens"] is None
    assert resources["token_observed_call_counts"]["total_tokens"] == 0
    assert resources["estimated_cost"]["amount"] is None
