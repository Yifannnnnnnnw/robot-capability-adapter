from __future__ import annotations

import pytest

from autoadapter2.integrations.direct_mujoco.session import (
    DirectMuJoCoEvaluationRobotSession,
    DirectMuJoCoSessionError,
)


def _session(
    trace: list[tuple[float, dict]],
    contacts: list[tuple[float, tuple[dict, ...]]] | None = None,
) -> DirectMuJoCoEvaluationRobotSession:
    session = object.__new__(DirectMuJoCoEvaluationRobotSession)
    session._truth_trace = trace
    session._contact_trace = contacts or []
    session._candidate_invocation_count = 2
    return session


def test_generic_vector_projection_baseline_and_history_operators() -> None:
    trace = [
        (0.0, {"sites": {"tip": {"position": [0.0, 0.0, 0.2]}}}),
        (0.1, {"sites": {"tip": {"position": [0.1, 0.0, 0.2]}}}),
        (0.2, {"sites": {"tip": {"position": [0.1, 0.1, 0.2]}}}),
        (0.3, {"sites": {"tip": {"position": [0.0, 0.1, 0.2]}}}),
        (0.4, {"sites": {"tip": {"position": [0.0, 0.0, 0.2]}}}),
    ]
    session = _session(trace)

    assert session._metric_spec_value(
        trace[2][1],
        {
            "operator": "distance",
            "observation_path": "sites.tip.position",
            "reference": {"value": [0.1, 0.0]},
            "projection": "xy",
        },
        {},
        {},
        "xy-distance",
    ) == pytest.approx(0.1)
    assert session._metric_spec_value(
        trace[2][1],
        {
            "operator": "offset_error",
            "observation_path": "sites.tip.position",
            "offset_path": "parameters.offset_m",
        },
        {"parameters": {"offset_m": [0.1, 0.1, 0.0]}},
        {},
        "offset",
    ) == pytest.approx(0.0)
    assert session._metric_spec_value(
        trace[-1][1],
        {
            "operator": "history_waypoint_max_error",
            "observation_path": "sites.tip.position",
            "projection": "xy",
            "waypoints_path": "parameters.waypoints_m",
        },
        {
            "parameters": {
                "waypoints_m": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]],
            }
        },
        {},
        "waypoint-error",
    ) == pytest.approx(0.0)
    assert session._metric_spec_value(
        trace[-1][1],
        {
            "operator": "history_waypoint_order",
            "observation_path": "sites.tip.position",
            "projection": "xy",
            "waypoints_path": "parameters.waypoints_m",
        },
        {
            "parameters": {
                "waypoints_m": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]],
            }
        },
        {},
        "waypoint-order",
    ) is True


def test_contact_truth_resolves_ids_for_unnamed_geoms_and_boolean_projection() -> None:
    trace = [(0.0, {"status": "EXPERIMENTAL"}), (0.1, {"status": "EXPERIMENTAL"})]
    contacts = [
        (0.0, ()),
        (
            0.1,
            (
                {
                    "geom1_id": 4,
                    "geom2_id": 9,
                    "body1_id": 2,
                    "body2_id": 0,
                    "geom1": "geom:4",
                    "geom2": "geom:9",
                    "body1": "cube",
                    "body2": "body:0",
                },
            ),
        ),
    ]
    session = _session(trace, contacts)
    spec = {
        "operator": "contact_count",
        "scope": "trace",
        "contact_groups": {"subject": ["cube"], "other": ["body:0"]},
    }
    assert session._metric_spec_value(trace[-1][1], spec, {}, {}, "contact") == 1.0
    assert session._metric_spec_value(
        trace[-1][1],
        {**spec, "operator": "contact_boolean"},
        {},
        {},
        "contact-bool",
    ) is True
    with pytest.raises(DirectMuJoCoSessionError, match="requires declared body or geom filters"):
        session._metric_spec_value(
            trace[-1][1],
            {"operator": "contact_count", "scope": "trace"},
            {},
            {},
            "missing-contact-filter",
        )


def test_framework_invocation_count_is_not_candidate_self_report() -> None:
    trace = [(0.0, {"status": "EXPERIMENTAL"})]
    session = _session(trace)
    assert session._metric_spec_value(
        trace[0][1],
        {"operator": "invocation_count"},
        {},
        {},
        "attempt-count",
    ) == 2.0
