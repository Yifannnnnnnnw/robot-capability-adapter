from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import autoadapter2.pipeline as pipeline
from autoadapter2.evolution import (
    EvolutionError,
    apply_experience_review,
    build_experience_review_queue,
    build_experience_snapshot,
    load_experience_snapshot,
    run_evolution,
)
from autoadapter2.pipeline import (
    ExperimentConfig,
    PipelineError,
    _clone_client_for_manifest,
    _experience_source_run_ids,
    _public_experience,
    run_experiment,
)

from test_pipeline import _fake_hooks


class _RecordingEvolutionClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self, *, stage: str, prompt: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"stage": stage, "prompt": prompt, "inputs": inputs})
        return json.loads(json.dumps(self.response))


def _model_manifest(*, model_id: str = "claude-sonnet", thinking: Any = "disabled") -> dict[str, Any]:
    return {
        "vendor": "anthropic",
        "api_protocol": "openai-compatible",
        "model_id": model_id,
        "revision": "test",
        "base_url": "https://api.example.test/v1",
        "context_window_tokens": 200000,
        "max_output_tokens": 1024,
        "temperature": 0.0,
        "thinking": thinking,
        "tool_history_mode": "native",
        "price_snapshot": {
            "date": "2026-08-23",
            "currency": "USD",
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 1.0,
        },
    }


def _queue() -> dict[str, Any]:
    return build_experience_review_queue(
        run_id="source-run",
        experiment_id="experience-test",
        expected_robots=("r-arm",),
        expected_conditions=("from-scratch",),
        cells=[
            {
                "cell_id": "r-arm::from-scratch",
                "pipeline_completed": True,
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": {
                    "pipeline_completed": True,
                    "physical_validation_executed": True,
                    "validation_passed": False,
                    "video_required": True,
                    "video_complete": True,
                },
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": {
                    "pipeline_completed": False,
                    "physical_validation_executed": False,
                    "validation_passed": False,
                    "skipped": True,
                    "skip_reason": "final capability validation did not pass",
                },
                "outcomes": {
                    "Evolution": {
                        "proposal": {
                            "observation": "Public failure observation",
                            "lesson": "Keep the public actuator bound visible.",
                            "recommendation": "Use the bound in a later run.",
                            "scope": "r-arm",
                            "evidence": ["failed public trial"],
                        }
                    }
                },
            }
        ],
    )


def test_legacy_shared_client_and_distinct_evolution_client_are_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "check_environment", lambda: {"test": True})
    hooks, state = _fake_hooks(tmp_path, [], validation_pass_at=1)
    seen: list[Any] = []
    hooks = dataclasses.replace(
        hooks,
        evolution_runner=lambda model, report: (
            seen.append(model) or {"non_blocking": True, "evolution_completed": True}
        ),
    )
    run_experiment(
        tmp_path,
        config={
            "experiment_id": "shared-client",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        },
        output_dir=tmp_path / "shared",
        run_id="shared-run",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )
    assert seen == [state["client"]]

    seen.clear()
    evolution_client = object()
    run_experiment(
        tmp_path,
        config={
            "experiment_id": "separate-client",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        },
        output_dir=tmp_path / "separate",
        run_id="separate-run",
        producer_client=state["client"],
        evolution_client=evolution_client,
        hooks=hooks,
        check_self_containment=False,
    )
    assert seen == [evolution_client]


def test_nested_evolution_model_routes_to_an_auto_clone_and_top_level_alias_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "check_environment", lambda: {"test": True})
    evolution_manifest = _model_manifest(model_id="claude-opus", thinking=None)
    config = {
        "experiment_id": "nested-evolution-model",
        "robots": ["r-arm"],
        "generation_conditions": ["skeleton-assisted"],
        "evolution": {
            "after_each_terminal_cell": True,
            "outcome_field": "cells[].outcomes.Evolution",
            "model": evolution_manifest,
        },
    }
    parsed = ExperimentConfig.from_mapping(config)
    assert parsed.evolution_model_manifest == evolution_manifest

    producer = SimpleNamespace(
        calls=[],
        config=SimpleNamespace(
            provider="anthropic",
            model="claude-sonnet",
            base_url="https://api.example.test/v1",
            api_key="test-key",
            api_protocol="openai-compatible",
            auth_header="Authorization",
            auth_prefix="Bearer ",
            thinking=None,
            timeout_s=10.0,
            max_tokens=1024,
            tool_history_mode="native",
            history_char_budget=80000,
        ),
    )
    clone_null = _clone_client_for_manifest(producer, evolution_manifest)
    assert clone_null is not None
    assert clone_null.config.thinking is None
    clone_disabled = _clone_client_for_manifest(
        producer, _model_manifest(model_id="claude-opus", thinking="disabled")
    )
    assert clone_disabled is not None
    assert clone_disabled.config.thinking is None

    hooks, _ = _fake_hooks(tmp_path, [], validation_pass_at=1)
    seen: list[Any] = []
    hooks = dataclasses.replace(
        hooks,
        evolution_runner=lambda model, report: (
            seen.append(model)
            or {"non_blocking": True, "evolution_completed": True, "proposal": None}
        ),
    )
    run_experiment(
        tmp_path,
        config=config,
        output_dir=tmp_path / "nested",
        run_id="nested-run",
        producer_client=producer,
        hooks=hooks,
        check_self_containment=False,
    )
    assert len(seen) == 1
    assert seen[0] is not producer
    assert seen[0].config.model == "claude-opus"

    uncloneable_hooks, _ = _fake_hooks(tmp_path, [], validation_pass_at=1)
    with pytest.raises(PipelineError, match="requires a cloneable Producer"):
        run_experiment(
            tmp_path,
            config=config,
            output_dir=tmp_path / "uncloneable",
            run_id="uncloneable-run",
            producer_client=SimpleNamespace(calls=[]),
            hooks=uncloneable_hooks,
            check_self_containment=False,
        )

    with pytest.raises(PipelineError, match="unsupported top-level evolution_model"):
        ExperimentConfig.from_mapping(
            {
                "experiment_id": "ambiguous-evolution-model",
                "robots": ["r-arm"],
                "generation_conditions": ["skeleton-assisted"],
                "evolution": {
                    "after_each_terminal_cell": True,
                    "outcome_field": "cells[].outcomes.Evolution",
                },
                "evolution_model": evolution_manifest,
            }
        )


def test_disabled_evolution_stops_after_task_demo_without_sidecar_or_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "check_environment", lambda: {"test": True})
    events: list[tuple[Any, ...]] = []
    hooks, state = _fake_hooks(tmp_path, events, validation_pass_at=1)
    hooks = dataclasses.replace(
        hooks,
        evolution_runner=lambda *_: (_ for _ in ()).throw(
            AssertionError("disabled Evolution was called")
        ),
    )
    output = tmp_path / "disabled"
    result = run_experiment(
        tmp_path,
        config={
            "experiment_id": "disabled-evolution",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "evolution": {"enabled": False},
        },
        output_dir=output,
        run_id="disabled-run",
        client=state["client"],
        hooks=hooks,
        check_self_containment=False,
    )
    assert result["evolution_enabled"] is False
    assert result["task_demo_executed"] is True
    assert result["cells"][0]["outcomes"]["Evolution"] is None
    assert not (output / "experience_review_queue.json").exists()


def test_evolution_compacts_pipeline_capability_report_and_prompt_bounds_root_cause() -> None:
    report = {
        "pipeline_completed": True,
        "capability_validation_executed": True,
        "final_capability_validation_passed": False,
        "task_demo_executed": False,
        "task_demo_passed": False,
        "renamed_private_payload": {
            "hidden_threshold_m": 0.07,
            "private_path_alias": "/repo/tasks/private/guards.json",
        },
        "failure": {
            "stage": "capability_validation",
            "type": "HarnessError",
            "message": "threshold 0.07 from /repo/tasks/private/guards.json",
        },
        "capability_validation": {
            "pipeline_completed": False,
            "physical_validation_executed": True,
            "validation_passed": False,
            "condition": "/repo/tasks/private/condition.json",
            "passed_task_count": {
                "private_path": "/repo/tasks/private/count.json"
            },
            "raw_marker": "must not cross as raw validation payload",
            "trials": [
                {
                    "trial_passed": False,
                    "trial_id": "/repo/tasks/private/trial.json",
                    "guard_outcomes": {
                        "public_guard": False,
                        "renamed_payload": {"threshold": 0.07},
                    },
                    "renamed_threshold": 0.07,
                    "candidate_log": "/repo/tasks/private/bindings.json",
                    "contact_integrity": {
                        "passed": False,
                        "maximum_allowed_penetration_m": 0.005,
                        "minimum_contact_distance_m": -0.006,
                    },
                    "physical_evidence": {
                        "samples": [{"qpos": list(range(100))}],
                        "step_count": 0,
                    },
                    "video": {
                        "complete": True,
                        "frame_count": {
                            "private_path": "/repo/tasks/private/video.json"
                        },
                        "duration_s": "threshold 0.07",
                    },
                }
            ],
        },
        "attempts": [
            {
                "attempt": {"private_path": "/repo/tasks/private/attempt.json"},
                "driver_generated": "yes",
            }
        ],
        "task_demo": {
            "pipeline_completed": False,
            "physical_validation_executed": False,
            "validation_passed": False,
            "skipped": True,
            "skip_reason": "capability validation did not pass",
        },
    }
    client = _RecordingEvolutionClient({})
    outcome = run_evolution(client, report)
    assert outcome["evolution_completed"] is True
    model_report = client.calls[0]["inputs"]["terminal_report"]
    assert "capability_validation" not in model_report
    assert "raw_marker" not in json.dumps(model_report)
    assert "samples" not in json.dumps(model_report)
    assert "hidden_threshold" not in json.dumps(model_report)
    assert "renamed_threshold" not in json.dumps(model_report)
    assert "maximum_allowed_penetration_m" not in json.dumps(model_report)
    assert "/repo/tasks/private" not in json.dumps(model_report)
    assert "guard_outcomes" not in json.dumps(model_report)
    assert model_report["attempts"] == [{}]
    assert "message" not in model_report["failure"]
    assert model_report["failure"] == {
        "stage": "capability_validation",
        "type": "HarnessError",
    }
    assert model_report["terminal_capability_validation"]["failed_trials"][0][
        "physical_evidence"
    ]["sample_count"] == 1
    assert model_report["task_demo"]["skipped"] is True
    assert model_report["task_demo"]["skip_reason_present"] is True
    assert "skip_reason" not in model_report["task_demo"]
    assert outcome["no_reusable_lesson"] is True
    assert "hypotheses" in client.calls[0]["prompt"]
    assert "root-cause" in client.calls[0]["prompt"]


@pytest.mark.parametrize(
    "proposal",
    (
        {
            "observation": "fact",
            "lesson": "lesson",
            "recommendation": "recommendation",
            "scope": "r-arm",
        },
        {
            "observation": "fact",
            "lesson": "lesson",
            "recommendation": "recommendation",
            "scope": "r-arm",
            "evidence": [],
        },
        {
            "observation": "fact",
            "lesson": "lesson",
            "recommendation": "recommendation",
            "scope": "r-arm",
            "evidence": ["public"],
            "extra_model_field": "not allowed",
        },
    ),
)
def test_evolution_proposal_schema_is_exact_and_public_only(
    proposal: dict[str, Any],
) -> None:
    report = {
        "pipeline_completed": True,
        "capability_validation_executed": True,
        "final_capability_validation_passed": False,
    }
    client = _RecordingEvolutionClient(proposal)
    outcome = run_evolution(client, report)
    assert outcome["proposal_created"] is False
    assert outcome["evolution_completed"] is False
    assert "proposal" in outcome["failure"]["message"]


def test_model_cannot_write_framework_proposal_wrapper() -> None:
    report = {
        "pipeline_completed": True,
        "capability_validation_executed": True,
        "final_capability_validation_passed": False,
    }
    client = _RecordingEvolutionClient(
        {
            "proposal": {
                "observation": "fact",
                "lesson": "lesson",
                "recommendation": "recommendation",
                "scope": "r-arm",
                "evidence": ["public"],
            }
        }
    )
    outcome = run_evolution(client, report)
    assert outcome["proposal_created"] is False
    assert outcome["evolution_completed"] is False
    assert "unexpected proposal" in outcome["failure"]["message"]


def test_queue_assigns_canonical_framework_labels() -> None:
    queue = _queue()
    record = queue["records"][0]
    assert record["source_robot"] == "r-arm"
    assert record["generation_condition"] == "from-scratch"
    assert record["terminal_outcome_label"] == "negative"
    assert record["source_condition"] == "from-scratch"
    assert record["source_outcome"] == "negative"
    assert record["evolution"]["proposal"] == {
        "observation": "Public failure observation",
        "lesson": "Keep the public actuator bound visible.",
        "recommendation": "Use the bound in a later run.",
        "scope": "r-arm",
        "evidence": ["failed public trial"],
    }

    indeterminate = build_experience_review_queue(
        run_id="source-run",
        experiment_id="experience-test",
        expected_robots=("r-arm",),
        expected_conditions=("from-scratch",),
        cells=[
            {
                "cell_id": "r-arm::from-scratch",
                "pipeline_completed": True,
                "outcomes": {
                    "Evolution": {
                        "proposal": {
                            "observation": "public",
                            "lesson": "not enough evidence",
                            "recommendation": "retain uncertainty",
                            "scope": "r-arm",
                            "evidence": ["terminal evidence was incomplete"],
                        }
                    }
                },
            }
        ],
    )
    reviewed = apply_experience_review(
        indeterminate,
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "check"}},
    )
    with pytest.raises(EvolutionError, match="determinate terminal outcome"):
        build_experience_snapshot(reviewed)


def test_malformed_raw_proposal_is_retained_as_failure_not_sanitized() -> None:
    queue = _queue()
    raw = queue["records"][0]["evolution"]
    assert isinstance(raw, dict)

    malformed_queue = build_experience_review_queue(
        run_id="source-run",
        experiment_id="experience-test",
        expected_robots=("r-arm",),
        expected_conditions=("from-scratch",),
        cells=[
            {
                "cell_id": "r-arm::from-scratch",
                "pipeline_completed": True,
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": {
                    "pipeline_completed": True,
                    "physical_validation_executed": True,
                    "validation_passed": False,
                    "video_required": True,
                    "video_complete": True,
                },
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": {
                    "pipeline_completed": False,
                    "physical_validation_executed": False,
                    "validation_passed": False,
                    "skipped": True,
                    "skip_reason": "final capability validation did not pass",
                },
                "outcomes": {
                    "Evolution": {
                        **raw,
                        "proposal": {
                            **raw["proposal"],
                            "private_suite": {"threshold": 0.07},
                        },
                    }
                },
            }
        ],
    )
    evolution = malformed_queue["records"][0]["evolution"]
    assert evolution["proposal"] is None
    assert evolution["proposal_created"] is False
    assert evolution["evolution_completed"] is False
    assert evolution["failure"]["type"] == "EvolutionError"
    reviewed = apply_experience_review(
        malformed_queue,
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "review"}},
    )
    with pytest.raises(EvolutionError, match="must contain an Evolution proposal"):
        build_experience_snapshot(reviewed)


def test_framework_source_outcome_uses_terminal_capability_and_task_demo_facts() -> None:
    proposal = {
        "observation": "Public terminal observation",
        "lesson": "Retain the bounded lesson",
        "recommendation": "Use it only in a later run",
        "scope": "r-arm",
        "evidence": ["public terminal evidence"],
    }
    capability_failure = {
        "pipeline_completed": True,
        "physical_validation_executed": True,
        "validation_passed": False,
        "video_required": True,
        "video_complete": True,
    }
    capability_pass = {**capability_failure, "validation_passed": True}
    task_demo_not_run = {
        "pipeline_completed": False,
        "physical_validation_executed": False,
        "validation_passed": False,
        "skipped": True,
        "skip_reason": "final capability validation did not pass",
    }
    task_demo_pass = {
        "pipeline_completed": True,
        "physical_validation_executed": True,
        "validation_passed": True,
        "video_required": True,
        "video_complete": True,
    }
    task_demo_failure = {**task_demo_pass, "validation_passed": False}
    cases = (
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_failure,
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "negative",
            True,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": True,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_pass,
                "task_demo_executed": True,
                "task_demo_passed": True,
                "task_demo_pipeline_completed": True,
                "task_demo": task_demo_pass,
            },
            "positive",
            True,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": True,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_pass,
                "task_demo_executed": True,
                "task_demo_passed": False,
                "task_demo_pipeline_completed": True,
                "task_demo": task_demo_failure,
            },
            "negative",
            True,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": True,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_pass,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "pipeline_completed": False,
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_failure,
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "infrastructure_failure": True,
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_failure,
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": False,
                "capability_validation": {
                    **capability_failure,
                    "video_complete": False,
                },
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_failure,
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": {
                    **task_demo_not_run,
                    "skip_reason": "",
                },
            },
            "indeterminate",
            False,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_pass,
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "capability_validation_executed": False,
                "final_capability_validation_passed": False,
                "video_required": True,
                "video_complete": True,
                "capability_validation": {
                    **capability_failure,
                    "physical_validation_executed": False,
                },
                "task_demo_executed": False,
                "task_demo_passed": False,
                "task_demo": task_demo_not_run,
            },
            "indeterminate",
            False,
        ),
        (
            {
                "capability_validation_executed": True,
                "final_capability_validation_passed": True,
                "video_required": True,
                "video_complete": True,
                "capability_validation": capability_pass,
                "task_demo_executed": True,
                "task_demo_passed": True,
                "task_demo_pipeline_completed": True,
                "task_demo_video_complete": False,
                "task_demo": {
                    key: value
                    for key, value in task_demo_pass.items()
                    if key != "video_complete"
                },
            },
            "indeterminate",
            False,
        ),
    )
    for index, (facts, expected_outcome, snapshot_allowed) in enumerate(cases):
        cell = {
            "cell_id": f"r-arm::condition-{index}",
            "pipeline_completed": True,
            "outcomes": {"Evolution": {"proposal": proposal}},
            **facts,
        }
        queue = build_experience_review_queue(
            run_id=f"source-{index}",
            experiment_id="outcome-facts",
            expected_robots=("r-arm",),
            expected_conditions=(f"condition-{index}",),
            cells=[cell],
        )
        assert queue["records"][0]["source_outcome"] == expected_outcome
        reviewed = apply_experience_review(
            queue,
            {cell["cell_id"]: {"disposition": "accept", "reason": "reviewed"}},
        )
        if snapshot_allowed:
            assert build_experience_snapshot(reviewed)["records"][0]["source_outcome"] == (
                expected_outcome
            )
        else:
            with pytest.raises(EvolutionError, match="determinate terminal outcome"):
                build_experience_snapshot(reviewed)


def test_review_accept_reject_reason_cannot_edit_proposal_content() -> None:
    queue = _queue()
    with pytest.raises(EvolutionError, match="requires a reason"):
        apply_experience_review(
            queue,
            {"r-arm::from-scratch": {"disposition": "accept"}},
        )
    with pytest.raises(EvolutionError, match="only disposition/decision and reason"):
        apply_experience_review(
            queue,
            {
                "r-arm::from-scratch": {
                    "disposition": "accept",
                    "reason": "ok",
                    "proposal": {"lesson": "edited"},
                }
            },
        )

    reviewed = apply_experience_review(
        queue,
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "public evidence"}},
    )
    with pytest.raises(EvolutionError, match="already frozen"):
        apply_experience_review(
            reviewed,
            {"r-arm::from-scratch": {"disposition": "reject", "reason": "changed"}},
        )
    with pytest.raises(EvolutionError, match="already frozen"):
        apply_experience_review(
            reviewed,
            {"r-arm::from-scratch": {"disposition": "accept", "reason": "changed"}},
        )
    snapshot = build_experience_snapshot(reviewed)
    assert snapshot["usage_scope"] == "next_independent_run_only"
    assert snapshot["records"][0]["lesson"] == "Keep the public actuator bound visible."
    assert snapshot["records"][0]["experience_id"] == "source-run:r-arm::from-scratch"
    assert snapshot["records"][0]["generation_condition"] == "from-scratch"
    assert snapshot["records"][0]["terminal_outcome_label"] == "negative"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("source_robot", "other-robot", "conflicting source_robot"),
        ("source_condition", "other-condition", "conflicting source_condition"),
        ("source_outcome", "positive", "conflicting source_outcome"),
        ("outcome_label", "positive", "conflicting outcome_label"),
        (
            "source_robot_configuration_id",
            "other-robot",
            "conflicting source_robot_configuration_id",
        ),
        (
            "source_generation_condition",
            "other-condition",
            "conflicting source_generation_condition",
        ),
        ("cell_id", "r-arm::other-condition", "inconsistent cell_id"),
        ("source_run_id", "other-run", "mismatched source_run_id"),
    ),
)
def test_review_rejects_tampered_framework_labels(
    field: str, value: str, message: str
) -> None:
    queue = _queue()
    queue["records"][0][field] = value
    with pytest.raises(EvolutionError, match=message):
        apply_experience_review(
            queue,
            {"r-arm::from-scratch": {"disposition": "accept", "reason": "review"}},
        )


def test_snapshot_requires_a_canonical_review_queue_artifact() -> None:
    queue = _queue()
    reviewed = apply_experience_review(
        queue,
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "review"}},
    )
    reviewed.pop("artifact_type")
    with pytest.raises(EvolutionError, match="experience_review_queue"):
        build_experience_snapshot(reviewed)


def test_dispositioned_queue_requires_immutable_reason_and_snapshot_lineage(
    tmp_path: Path,
) -> None:
    queue = _queue()
    queue["records"][0]["disposition"] = "accept"
    with pytest.raises(EvolutionError, match="requires a reason"):
        build_experience_snapshot(queue)

    snapshot = build_experience_snapshot(
        _queue(),
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "retain"}},
    )
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert load_experience_snapshot(path)["usage_scope"] == "next_independent_run_only"
    parsed = ExperimentConfig.from_mapping(
        {
            "experiment_id": "snapshot-input",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "experience": {"input": snapshot},
        }
    )
    assert parsed.experience_input[0]["experience_id"] == (
        "source-run:r-arm::from-scratch"
    )
    assert parsed.experience_snapshot_id == snapshot["snapshot_id"]
    assert parsed.experience_source_run_id == "source-run"
    legacy = dict(snapshot)
    legacy["usage_scope"] = "later_matched_run_only"
    legacy_path = tmp_path / "legacy-snapshot.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert load_experience_snapshot(legacy_path)["usage_scope"] == "later_matched_run_only"
    historical = {
        "artifact_type": "autoadapter_experience_snapshot",
        "snapshot_id": "historical",
        "version": "1.0.0",
        "review_status": "project_owner_reviewed",
        "usage_scope": "later_matched_run_only",
        "b1_input": False,
        "benefit_claim": False,
        "r-arm": [
            {
                "experience_id": "historical-1",
                "reviewed": True,
                "observation": "public",
                "lesson": "public",
                "recommendation": "public",
                "scope": "r-arm",
                "evidence": ["public"],
                "source_run_id": "historical-run",
            }
        ],
    }
    historical_path = tmp_path / "historical-snapshot.json"
    historical_path.write_text(json.dumps(historical), encoding="utf-8")
    assert load_experience_snapshot(historical_path)["r-arm"][0]["experience_id"] == (
        "historical-1"
    )
    historical_config = ExperimentConfig.from_mapping(
        {
            "experiment_id": "historical-input",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
            "experience": {"input": historical},
        }
    )
    assert [item["experience_id"] for item in historical_config.experience_input] == [
        "historical-1"
    ]

    invalid = json.loads(json.dumps(snapshot))
    invalid["records"][0]["terminal_outcome_label"] = "indeterminate"
    invalid_path = tmp_path / "invalid-snapshot.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(EvolutionError, match="invalid terminal_outcome_label"):
        load_experience_snapshot(invalid_path)
    with pytest.raises(PipelineError, match="terminal_outcome_label"):
        _public_experience(invalid, "r-other")


def test_historical_per_robot_snapshot_routing_is_preserved_in_config() -> None:
    def record(experience_id: str, source_run_id: str) -> dict[str, Any]:
        return {
            "experience_id": experience_id,
            "reviewed": True,
            "observation": "public",
            "lesson": "public",
            "recommendation": "public",
            "scope": "legacy",
            "evidence": ["public"],
            "source_run_id": source_run_id,
        }

    historical = {
        "artifact_type": "autoadapter_experience_snapshot",
        "snapshot_id": "historical-two-robot",
        "version": "1.0.0",
        "review_status": "project_owner_reviewed",
        "usage_scope": "later_matched_run_only",
        "b1_input": False,
        "benefit_claim": False,
        "r-arm": [record("arm-1", "arm-source")],
        "r-quad": [record("quad-1", "quad-source")],
    }
    parsed = ExperimentConfig.from_mapping(
        {
            "experiment_id": "historical-routing",
            "robots": ["r-arm", "r-quad"],
            "generation_conditions": ["skeleton-assisted"],
            "experience": {"input": historical},
        }
    )
    assert parsed.experience_snapshot_input == historical
    assert [
        item["experience_id"]
        for item in _public_experience(parsed.experience_snapshot_input, "r-arm")
    ] == ["arm-1"]
    assert [
        item["experience_id"]
        for item in _public_experience(parsed.experience_snapshot_input, "r-quad")
    ] == ["quad-1"]
    assert parsed.as_dict()["experience"]["input"] == historical


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing_observation", "proposal.observation"),
        ("empty_evidence", "public string list"),
        ("extra_model_field", "unsupported fields"),
        ("lineage_only", "proposal.observation"),
    ),
)
def test_new_snapshot_requires_exact_public_proposal_on_load_and_projection(
    tmp_path: Path, mutation: str, message: str
) -> None:
    snapshot = build_experience_snapshot(
        _queue(),
        {"r-arm::from-scratch": {"disposition": "accept", "reason": "retain"}},
    )
    malformed = json.loads(json.dumps(snapshot))
    record = malformed["records"][0]
    if mutation == "missing_observation":
        record.pop("observation")
    elif mutation == "empty_evidence":
        record["evidence"] = []
    elif mutation == "lineage_only":
        for field in ("observation", "lesson", "recommendation", "scope", "evidence"):
            record.pop(field)
    else:
        record["extra_model_field"] = "not public schema"
    path = tmp_path / f"{mutation}.json"
    path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(EvolutionError, match=message):
        load_experience_snapshot(path)
    with pytest.raises(PipelineError, match=message):
        _public_experience(malformed, "r-other")


def test_global_snapshot_is_robot_filtered_and_same_round_input_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "check_environment", lambda: {"test": True})
    queue = _queue()
    snapshot = build_experience_snapshot(
        queue,
        {"r-arm::from-scratch": {"decision": "accept", "reason": "retain"}},
    )
    public_records = _public_experience(snapshot, "r-arm")
    assert [item["experience_id"] for item in public_records] == [
        "source-run:r-arm::from-scratch"
    ]
    assert public_records[0]["source_robot"] == "r-arm"
    assert public_records[0]["generation_condition"] == "from-scratch"
    assert public_records[0]["terminal_outcome_label"] == "negative"
    assert [item["experience_id"] for item in _public_experience(snapshot, "r-quad")] == [
        "source-run:r-arm::from-scratch"
    ]

    hooks, state = _fake_hooks(tmp_path, [], validation_pass_at=1)
    with pytest.raises(PipelineError, match="same-round"):
        run_experiment(
            tmp_path,
            config={
                "experiment_id": "same-round",
                "robots": ["r-arm"],
                "generation_conditions": ["skeleton-assisted"],
            },
            output_dir=tmp_path / "same-round",
            run_id="source-run",
            client=state["client"],
            experience=snapshot,
            hooks=hooks,
            check_self_containment=False,
        )

    empty_snapshot = build_experience_snapshot(
        queue,
        {"r-arm::from-scratch": {"decision": "reject", "reason": "not retained"}},
    )
    with pytest.raises(PipelineError, match="same-round"):
        run_experiment(
            tmp_path,
            config={
                "experiment_id": "same-round-empty-snapshot",
                "robots": ["r-arm"],
                "generation_conditions": ["skeleton-assisted"],
                "experience": {"input": empty_snapshot},
            },
            output_dir=tmp_path / "same-round-empty-snapshot",
            run_id="source-run",
            client=state["client"],
            hooks=hooks,
            check_self_containment=False,
        )

    result = run_experiment(
        tmp_path,
        config={
            "experiment_id": "later-run",
            "robots": ["r-arm"],
            "generation_conditions": ["skeleton-assisted"],
        },
        output_dir=tmp_path / "later-run",
        run_id="later-run",
        client=state["client"],
        experience=snapshot,
        hooks=hooks,
        check_self_containment=False,
    )
    expected_id = "source-run:r-arm::from-scratch"
    assert result["experience_input_ids"]["r-arm"] == [expected_id]
    assert result["experience_snapshot_id"] == snapshot["snapshot_id"]
    assert result["experience_source_run_id"] == "source-run"
    assert result["cells"][0]["experience_input_ids"] == [expected_id]
    assert result["cells"][0]["outcomes"]["STUDY"]["experience_ids"] == [expected_id]
    packed = json.dumps(result["cells"][0]["outcomes"], sort_keys=True)
    assert expected_id in packed
    assert "public evidence" not in packed
    assert "review_decision" not in packed
    retained_input = json.loads(
        (tmp_path / "later-run" / "cells" / "r-arm" / "skeleton-assisted" / "experience_input.json").read_text()
    )
    assert retained_input["records"][0]["generation_condition"] == "from-scratch"
    assert retained_input["records"][0]["terminal_outcome_label"] == "negative"
    assert "public evidence" not in json.dumps(retained_input)
    assert "review_reason" not in json.dumps(retained_input)


def test_same_round_guard_flattens_legacy_per_robot_experience_mapping() -> None:
    legacy = {
        "r-arm": [
            {"experience_id": "legacy-1", "source_run_id": "source-run"},
        ],
        "r-quad": [
            {"experience_id": "legacy-2", "source_run_id": "other-run"},
        ],
    }
    assert _experience_source_run_ids(legacy) == {"source-run", "other-run"}
    assert _experience_source_run_ids({"source_run_id": "empty-snapshot", "records": []}) == {
        "empty-snapshot"
    }
