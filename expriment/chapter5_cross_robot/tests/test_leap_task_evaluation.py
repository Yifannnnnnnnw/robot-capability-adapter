"""Named synthetic trace fixtures for LEAP scorer timing and terminal reach."""

from __future__ import annotations

from auto_adapter.demo_evaluation import evaluate_demo_task

from robots.leap_hand.task_evaluation import evaluate_leap_task


_IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
_TIP_LABELS = ("index_tip", "middle_tip", "ring_tip", "thumb_tip")
_TARGET = [0.10, -0.02, 0.20, 0.11, -0.01, 0.20, 0.12, 0.00, 0.20, 0.13, 0.01, 0.20]


def _reach_spec() -> dict:
    return {
        "type": "leap_catalog_task",
        "task_id": "gym_hand_reach_all_fingertips",
        "bindings": {
            "palm": {"kind": "body", "name": "palm"},
            **{label: {"kind": "geom", "name": label} for label in _TIP_LABELS},
        },
    }


def _synthetic_reach_trace(*, terminal_target: bool) -> list[dict]:
    rows = []
    for step in range(1001):
        time = step * 0.002
        at_target = (step == 1000) if terminal_target else step == 1
        positions = _TARGET if at_target else [0.0] * 12
        state = {
            "palm": {"position": [0.0, 0.0, 0.0], "rotation": list(_IDENTITY)},
        }
        for offset, label in enumerate(_TIP_LABELS):
            point = positions[offset * 3:offset * 3 + 3]
            state[label] = {"position": list(point), "rotation": list(_IDENTITY)}
        rows.append({"time": time, "state": state, "contacts": []})
    return rows


def _reach_result(samples: list[dict]) -> dict:
    spec = _reach_spec()
    parameters = {
        "target_fingertip_positions_m": list(_TARGET),
        "max_control_steps": 50,
    }
    return evaluate_demo_task(spec, parameters, samples, evaluator=evaluate_leap_task)


def test_synthetic_time_sampling_fixture_uses_terminal_source_control_step():
    result = _reach_result(_synthetic_reach_trace(terminal_target=True))
    assert result["evaluation_error"] is None, result
    assert result["physical_task_success"] is True, result
    metric = next(item for item in result["task_metrics"]
                   if item["check"] == "concatenated_fingertip_cartesian_l2_error_m")
    assert metric["value"] == 0.0

    early_only = _reach_result(_synthetic_reach_trace(terminal_target=False))
    assert early_only["evaluation_error"] is None, early_only
    assert early_only["physical_task_success"] is False, early_only
    assert any(not item["ok"] for item in early_only["task_metrics"])


def test_synthetic_incomplete_reach_fixture_cannot_pass_from_earlier_samples():
    samples = _synthetic_reach_trace(terminal_target=False)[:-1]
    result = _reach_result(samples)
    assert result["evaluation_error"] is None, result
    assert result["physical_task_success"] is False, result
    coverage = next(item for item in result["task_metrics"]
                    if item["check"] == "control_horizon_covered")
    assert coverage["ok"] is False
