from __future__ import annotations

import json
from typing import Any

from autoadapter2.evolution import build_experience_review_queue


def _cell(cell_id: str, evolution: Any, *, completed: bool = True) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "pipeline_completed": completed,
        "outcomes": {"Evolution": evolution},
    }


def test_queue_has_one_deterministic_record_per_expected_cell_and_stays_pending() -> None:
    cells = [
        _cell(
            "r-arm::from-scratch",
            {
                "non_blocking": True,
                "evolution_completed": True,
                "proposal_created": False,
                "proposal": None,
            },
        ),
        _cell(
            "r-quad::skeleton-assisted",
            {
                "non_blocking": True,
                "evolution_completed": True,
                "proposal_created": True,
                "proposal": {
                    "observation": "A public outcome was retained.",
                    "lesson": "Review the bounded proposal before a later run.",
                    "recommendation": "Consider it only after human review.",
                    "scope": "r-quad",
                    "evidence": ["public outcome"],
                },
            },
        ),
    ]

    queue = build_experience_review_queue(
        run_id="run-1",
        experiment_id="experiment-1",
        expected_robots=("r-quad", "r-arm"),
        expected_conditions=("skeleton-assisted", "from-scratch"),
        cells=cells,
    )

    assert [record["cell_id"] for record in queue["records"]] == [
        "r-quad::skeleton-assisted",
        "r-quad::from-scratch",
        "r-arm::skeleton-assisted",
        "r-arm::from-scratch",
    ]
    assert len(queue["records"]) == 4
    assert queue["expected_evolution_outcome_count"] == 4
    assert queue["retained_evolution_outcome_count"] == 2
    assert queue["all_evolution_outcomes_retained"] is False
    assert queue["cell_pipeline_completed"] is False
    assert queue["reviewed_disposition_count"] == 0
    assert queue["dispositions_complete"] is False
    assert all(
        record["disposition"] is None and record["reason"] is None
        for record in queue["records"]
    )
    assert queue["records"][0]["source_run_id"] == "run-1"
    assert queue["records"][0]["robot_configuration_id"] == "r-quad"
    assert queue["records"][0]["generation_condition"] == "skeleton-assisted"


def test_queue_retains_evolution_failure_but_drops_private_definition_fields() -> None:
    failed = {
        "non_blocking": True,
        "terminal_report_read": True,
        "evolution_attempted": True,
        "evolution_completed": False,
        "proposal_created": False,
        "proposal": {
            "observation": "The public model call failed.",
            "private_suite": "do-not-copy",
            "private_guard_definition": "do-not-copy",
            "reference_driver_source": "do-not-copy",
        },
        "failure": {
            "type": "RuntimeError",
            "message": "temporary model outage",
            "private_binding": "do-not-copy",
        },
        "private_validation_suite": {"sentinel": "do-not-copy"},
    }

    queue = build_experience_review_queue(
        run_id="run-failed-evolution",
        experiment_id="experiment-1",
        expected_robots=("r-arm",),
        expected_conditions=("from-scratch",),
        cells=[_cell("r-arm::from-scratch", failed)],
    )

    outcome = queue["records"][0]["evolution"]
    assert outcome["evolution_completed"] is False
    assert outcome["failure"]["type"] == "RuntimeError"
    assert outcome["proposal"] == {
        "observation": "The public model call failed.",
    }
    serialized = json.dumps(queue, sort_keys=True)
    for forbidden in (
        "private_suite",
        "private_guard_definition",
        "reference_driver_source",
        "private_binding",
        "private_validation_suite",
        "do-not-copy",
    ):
        assert forbidden not in serialized
    assert queue["retained_evolution_outcome_count"] == 1
    assert queue["all_evolution_outcomes_retained"] is True
    assert queue["dispositions_complete"] is False
