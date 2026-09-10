"""Focused regressions for the public A4 contact contract."""

from copy import deepcopy

import pytest

from autoadapter_bench import capability_metrics as metrics


PARAMETERS = {
    "contract_id": "A4",
    "site_name": "ee",
    "tool_geom_names": ["tool"],
    "target_geom_names": ["target"],
}
REQUEST = {
    "precontact_position_m": [0.0, 0.0, 0.0],
    "approach_direction_unit": [1.0, 0.0, 0.0],
    "max_travel_m": 0.05,
    "max_approach_speed_m_s": 0.02,
    "max_duration_s": 1.0,
}


def _sample(time_s, position, *, contact=False, surface_speed=0.02,
            closing_speed=0.02):
    return {
        "time": time_s,
        "site_positions": {"ee": list(position)},
        "contacts": ([{"geom1": "tool", "geom2": "target", "distance": 0.0}]
                     if contact else []),
        "a4_surface_relative_speed_m_s": surface_speed,
        "a4_contact_normal_closing_speed_m_s": closing_speed,
    }


def _evidence(samples):
    first = next(
        (sample["time"] for sample in samples if sample["contacts"]),
        None,
    )
    return {
        "samples": samples,
        "contact_pair_first_times_s": (
            [{"geom1": "tool", "geom2": "target", "first_time_s": first}]
            if first is not None else []
        ),
        "contact_pair_step_counts": (
            [{"geom1": "tool", "geom2": "target",
              "step_count": sum(bool(sample["contacts"]) for sample in samples)}]
            if first is not None else []
        ),
    }


def _passing_samples(*, initial_time=10.0):
    return [
        _sample(initial_time + 0.00, [0.0, 0.012, 0.0]),
        _sample(initial_time + 0.05, [0.0, 0.012, 0.0]),
        _sample(initial_time + 0.10, [0.0, 0.012, 0.0]),
        _sample(initial_time + 0.15, [0.01, 0.012, 0.0]),
        _sample(initial_time + 0.20, [0.02, 0.012, 0.0], contact=True),
        _sample(initial_time + 0.25, [0.0205, 0.012, 0.0], contact=True),
        _sample(initial_time + 0.30, [0.021, 0.012, 0.0], contact=True),
        # This motion is outside the scored contact window.
        _sample(initial_time + 0.35, [0.20, 0.012, 0.0]),
    ]


def _score(samples, parameters=None):
    return metrics.evaluate_b1_contract(
        parameters or PARAMETERS,
        evidence=_evidence(samples),
        request=REQUEST,
    )


def _describe(samples, parameters=None):
    return metrics.describe_contract_measurements(
        parameters or PARAMETERS,
        evidence=_evidence(samples),
        request=REQUEST,
    )


def test_a4_uses_measured_dwell_endpoint_as_ray_origin():
    samples = _passing_samples()

    assert _score(samples) == 1.0
    description = _describe(samples)
    assert description["passed"] is True
    assert description["precontact_index"] == 2
    assert description["precontact_error_m"] == 0.012
    assert description["ray_lateral_error_m"] == 0.0


def test_a4_rejects_precontact_dwell_shorter_than_point_one_second():
    samples = [
        _sample(10.00, [0.0, 0.0, 0.0]),
        _sample(10.05, [0.0, 0.0, 0.0]),
        _sample(10.09, [0.0, 0.0, 0.0]),
        _sample(10.14, [0.02, 0.0, 0.0]),
        _sample(10.19, [0.02, 0.0, 0.0], contact=True),
        _sample(10.24, [0.0205, 0.0, 0.0], contact=True),
        _sample(10.29, [0.021, 0.0, 0.0], contact=True),
    ]

    assert _score(samples) == 0.0
    description = _describe(samples)
    assert description["first_failed_gate"] == "precontact_dwell"
    assert description["longest_precontact_hold_s"] == pytest.approx(0.09)


def test_a4_reports_only_the_scored_contact_window_speed():
    description = _describe(_passing_samples(initial_time=10.0))

    assert description["scored_contact_window_s"][0] == pytest.approx(0.2)
    assert description["scored_contact_window_s"][1] == pytest.approx(0.3)
    assert description["scored_contact_window_max_speed_m_s"] <= 0.02
    assert description["maximum_speed_after_contact_m_s"] <= 0.02
    assert description["passed"] is True


def test_a4_reports_contact_duration_when_hold_is_too_short():
    samples = _passing_samples()[:6]

    description = _describe(samples)
    assert _score(samples) == 0.0
    assert description["first_failed_gate"] == "contact_window"
    assert description["longest_target_contact_s"] == pytest.approx(0.05)
    assert description["required"]["contact_hold_s"] == 0.1


def test_a4_surface_speed_uses_required_trusted_measurement():
    samples = _passing_samples()
    # The point finite difference is intentionally too fast; the trusted
    # nearest-surface relative speed remains within the public limit.
    samples[3]["site_positions"]["ee"] = [0.04, 0.012, 0.0]
    samples[4]["site_positions"]["ee"] = [0.045, 0.012, 0.0]
    samples[5]["site_positions"]["ee"] = [0.0455, 0.012, 0.0]
    samples[6]["site_positions"]["ee"] = [0.046, 0.012, 0.0]
    assert _score(samples) == 1.0

    too_fast = deepcopy(samples)
    too_fast[2]["a4_surface_relative_speed_m_s"] = 0.04
    assert _score(too_fast) == 0.0
    description = _describe(too_fast)
    assert description["first_failed_gate"] == "surface_relative_speed"
    assert description["maximum_surface_relative_speed_m_s"] == 0.04

    too_fast_contact = _passing_samples()
    too_fast_contact[4]["a4_contact_normal_closing_speed_m_s"] = 0.04
    assert _score(too_fast_contact) == 0.0
    assert _describe(too_fast_contact)["first_failed_gate"] == "contact_normal_closing_speed"


def test_a4_missing_surface_or_closing_measurements_are_unavailable():
    missing_surface = _passing_samples()
    for sample in missing_surface:
        sample.pop("a4_surface_relative_speed_m_s")
    description = _describe(missing_surface)
    assert _score(missing_surface) == 0.0
    assert description["available"] is False
    assert "a4_surface_relative_speed_m_s" in description["unavailable_measurements"]

    missing_closing = _passing_samples()
    for sample in missing_closing:
        sample.pop("a4_contact_normal_closing_speed_m_s")
    description = _describe(missing_closing)
    assert _score(missing_closing) == 0.0
    assert description["available"] is False
    assert "a4_contact_normal_closing_speed_m_s" in description["unavailable_measurements"]


def test_a4_uses_running_maximum_for_backtrack_gate():
    samples = [
        _sample(10.00, [0.0, 0.0, 0.0]),
        _sample(10.05, [0.0, 0.0, 0.0]),
        _sample(10.10, [0.0, 0.0, 0.0]),
        _sample(10.15, [0.01, 0.0, 0.0]),
        _sample(10.20, [0.009, 0.0, 0.0]),
        _sample(10.25, [0.008, 0.0, 0.0]),
        _sample(10.30, [0.007, 0.0, 0.0]),
        _sample(10.35, [0.02, 0.0, 0.0], contact=True),
        _sample(10.40, [0.0205, 0.0, 0.0], contact=True),
        _sample(10.45, [0.021, 0.0, 0.0], contact=True),
    ]

    assert _score(samples) == 0.0
    description = _describe(samples)
    assert description["first_failed_gate"] == "ray_backtrack"
    assert description["ray_backtrack_m"] > 0.002
