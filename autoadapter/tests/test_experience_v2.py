from __future__ import annotations

import copy

import pytest

from autoadapter2.evolution import (
    EvolutionError,
    apply_experience_review,
    build_experience_review_queue,
    build_experience_snapshot,
    run_evolution,
)
from autoadapter2.pipeline import _experience_ids, _public_experience


class _EvolutionClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate_json(self, **request: object) -> dict[str, object]:
        self.calls.append(copy.deepcopy(request))
        return copy.deepcopy(self.response)


def _terminal_cell(evolution: dict[str, object]) -> dict[str, object]:
    capability = {
        "pipeline_completed": True,
        "physical_validation_executed": True,
        "validation_passed": True,
        "video_required": False,
        "video_complete": True,
        "trials": [],
    }
    task_demo = copy.deepcopy(capability)
    return {
        "cell_id": "robotstudio_so101::skeleton-assisted",
        "robot_configuration_id": "robotstudio_so101",
        "condition": "skeleton-assisted",
        "pipeline_completed": True,
        "capability_validation_executed": True,
        "final_capability_validation_passed": True,
        "task_demo_executed": True,
        "task_demo_passed": True,
        "task_demo_pipeline_completed": True,
        "capability_validation": capability,
        "task_demo": task_demo,
        "evolution": evolution,
    }


def test_evolution_authors_only_the_five_public_fields() -> None:
    proposal = {
        "observation": "One bounded move reached the public target.",
        "lesson": "Keep the request frame explicit.",
        "recommendation": "Check the frame before constructing the controller.",
        "scope": "SO-101 Cartesian moves",
        "public_evidence": ["capability move nominal passed"],
    }
    client = _EvolutionClient(proposal)

    outcome = run_evolution(client, _terminal_cell({}))

    assert outcome["evolution_completed"] is True
    assert outcome["proposal"] == proposal
    assert set(outcome["proposal"]) == {
        "observation",
        "lesson",
        "recommendation",
        "scope",
        "public_evidence",
    }


def test_new_snapshot_adds_only_framework_provenance_and_outcome() -> None:
    proposal = {
        "observation": "One bounded move reached the public target.",
        "lesson": "Keep the request frame explicit.",
        "recommendation": "Check the frame before constructing the controller.",
        "scope": "SO-101 Cartesian moves",
        "public_evidence": ["capability move nominal passed"],
    }
    evolution = {
        "evolution_completed": True,
        "proposal_created": True,
        "proposal": proposal,
    }
    queue = build_experience_review_queue(
        run_id="source-run",
        experiment_id="diagnostic-canary",
        expected_robots=["robotstudio_so101"],
        expected_conditions=["skeleton-assisted"],
        cells=[_terminal_cell(evolution)],
    )
    reviewed = apply_experience_review(
        queue,
        {
            "robotstudio_so101::skeleton-assisted": {
                "decision": "accept",
                "reason": "The proposal is public and tied to retained evidence.",
            }
        },
    )

    snapshot = build_experience_snapshot(reviewed)
    record = snapshot["records"][0]

    assert snapshot["version"] == "2.0.0"
    assert set(record) == set(proposal) | {"provenance", "outcome"}
    assert record["provenance"]["review"] == {
        "decision": "accept",
        "reason": "The proposal is public and tied to retained evidence.",
    }
    assert record["outcome"] == {"terminal_label": "positive"}
    visible = _public_experience(snapshot, "robotstudio_so101")
    assert visible == (record,)
    assert _experience_ids(visible) == [
        "source-run:robotstudio_so101::skeleton-assisted"
    ]


@pytest.mark.parametrize(
    ("task_demo_passed", "terminal_label"),
    ((True, "positive"), (False, "negative")),
)
def test_partial_capability_closure_uses_trusted_task_demo_outcome(
    task_demo_passed: bool,
    terminal_label: str,
) -> None:
    proposal = {
        "observation": "One whitelisted capability supported a physical Task Demo.",
        "lesson": "Keep partial capability closure distinct from whole-suite success.",
        "recommendation": "Retain the trusted Task Demo verdict for the later run.",
        "scope": "SO-101 bounded reach",
        "public_evidence": ["bounded_reach nominal and boundary passed"],
    }
    evolution = {
        "evolution_completed": True,
        "proposal_created": True,
        "proposal": proposal,
    }
    cell = _terminal_cell(evolution)
    cell.update(
        {
            "capability_validation_executed": False,
            "final_capability_validation_passed": False,
            "video_required": True,
            "video_complete": False,
            "capability_validation": {
                "pipeline_completed": True,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_required": True,
                "video_complete": False,
                "trials": [],
            },
            "passed_capability_whitelist": ["bounded_reach"],
            "task_demo_executed": True,
            "task_demo_passed": task_demo_passed,
            "task_demo_pipeline_completed": True,
            "task_demo_video_complete": True,
            "task_demo": {
                "pipeline_completed": True,
                "physical_validation_executed": True,
                "validation_passed": task_demo_passed,
                "video_required": True,
                "video_complete": True,
                "trials": [],
            },
        }
    )

    queue = build_experience_review_queue(
        run_id="partial-source",
        experiment_id="diagnostic-canary",
        expected_robots=["robotstudio_so101"],
        expected_conditions=["skeleton-assisted"],
        cells=[cell],
    )
    assert queue["records"][0]["terminal_outcome_label"] == terminal_label

    reviewed = apply_experience_review(
        queue,
        {
            "robotstudio_so101::skeleton-assisted": {
                "decision": "accept",
                "reason": "The Task Demo evidence is complete and physically executed.",
            }
        },
    )
    snapshot = build_experience_snapshot(reviewed)
    assert snapshot["records"][0]["outcome"] == {
        "terminal_label": terminal_label
    }


def test_partial_capability_closure_requires_trusted_task_demo_video() -> None:
    evolution = {
        "evolution_completed": True,
        "proposal_created": True,
        "proposal": {
            "observation": "A partial run was observed.",
            "lesson": "Missing video is not trusted terminal evidence.",
            "recommendation": "Keep the outcome indeterminate.",
            "scope": "SO-101 bounded reach",
            "public_evidence": ["Task Demo video incomplete"],
        },
    }
    cell = _terminal_cell(evolution)
    cell.update(
        {
            "capability_validation_executed": False,
            "final_capability_validation_passed": False,
            "capability_validation": {
                "pipeline_completed": True,
                "physical_validation_executed": False,
                "validation_passed": False,
                "video_required": True,
                "video_complete": False,
            },
            "passed_capability_whitelist": ["bounded_reach"],
            "task_demo_video_complete": False,
            "task_demo": {
                "pipeline_completed": True,
                "physical_validation_executed": True,
                "validation_passed": True,
                "video_required": True,
                "video_complete": False,
            },
        }
    )

    queue = build_experience_review_queue(
        run_id="partial-source-missing-video",
        experiment_id="diagnostic-canary",
        expected_robots=["robotstudio_so101"],
        expected_conditions=["skeleton-assisted"],
        cells=[cell],
    )
    assert queue["records"][0]["terminal_outcome_label"] == "indeterminate"

    reviewed = apply_experience_review(
        queue,
        {
            "robotstudio_so101::skeleton-assisted": {
                "decision": "accept",
                "reason": "Retained only to verify the fail-closed snapshot boundary.",
            }
        },
    )
    with pytest.raises(EvolutionError, match="determinate terminal outcome"):
        build_experience_snapshot(reviewed)


def test_old_model_field_name_is_rejected_for_new_evolution() -> None:
    client = _EvolutionClient(
        {
            "observation": "Observed.",
            "lesson": "Lesson.",
            "recommendation": "Recommendation.",
            "scope": "Scope.",
            "evidence": ["legacy name"],
        }
    )

    outcome = run_evolution(client, _terminal_cell({}))

    assert outcome["evolution_completed"] is False
    assert "invalid fields" in outcome["failure"]["message"]


def test_human_cannot_edit_a_proposal_during_review() -> None:
    queue = build_experience_review_queue(
        run_id="source-run",
        experiment_id="diagnostic-canary",
        expected_robots=["robotstudio_so101"],
        expected_conditions=["skeleton-assisted"],
        cells=[_terminal_cell({"evolution_completed": True, "proposal": None})],
    )

    with pytest.raises(EvolutionError, match="accepts only"):
        apply_experience_review(
            queue,
            {
                "robotstudio_so101::skeleton-assisted": {
                    "decision": "reject",
                    "reason": "No reusable proposal.",
                    "lesson": "edited",
                }
            },
        )
