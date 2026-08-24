from __future__ import annotations

from experiment.experiment1b_use.corrected_r1.runtime.positive_controls import (
    _plans,
)


def _request(leaf: dict) -> dict:
    return leaf["subtasks"][0]["request"]


def test_new_profile_only_replaces_t17_and_dial_control_plans() -> None:
    legacy = _plans()
    assert _plans("b2_corrected_r23") == legacy

    revised = _plans("b2_corrected_r123_v2")
    assert {
        task_id
        for task_id in legacy
        if revised[task_id] != legacy[task_id]
    } == {"GO2-T17", "mw_dial_turn"}


def test_t17_control_is_a_twenty_second_zero_yaw_closed_loop_weave() -> None:
    plan = _plans("b2_corrected_r123_v2")["GO2-T17"]
    assert len(plan) == 11
    assert {
        leaf["subtasks"][0]["capability_name"] for leaf in plan
    } == {"trace_planar_path"}
    requests = [_request(leaf) for leaf in plan]
    assert sum(request["max_duration_s"] for request in requests) == 20.0
    assert all(
        set(request) == {"waypoints_initial_yaw_m", "max_duration_s"}
        for request in requests
    )


def test_dial_control_uses_the_inward_shifted_public_x_coordinates() -> None:
    plan = _plans("b2_corrected_r123_v2")["mw_dial_turn"]
    move_request = _request(plan[1])
    approach_request = _request(plan[2])
    trace_request = _request(plan[3])
    assert move_request["target_position_m"][0] == 0.347
    assert approach_request["precontact_position_m"][0] == 0.347
    assert {
        waypoint[0] for waypoint in trace_request["waypoints_m"]
    } == {0.347}
