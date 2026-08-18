from __future__ import annotations

import math

import pytest

from autoadapter2.harness.measurements import MeasurementError, measure


def _evidence(position: list[float]) -> dict:
    return {
        "samples": [
            {
                "time": 0.0,
                "site_positions": {"fixture_site": position},
            }
        ]
    }


def _arguments(target: list[float]) -> dict:
    return {"request": {"task_parameters": {"target_position": target}}}


def test_site_axis_error_uses_only_the_source_axis() -> None:
    value = measure(
        {
            "kind": "final_site_axis_error",
            "parameters": {
                "site_name": "fixture_site",
                "target_argument": "request.task_parameters.target_position",
                "axis": 1,
            },
        },
        evidence=_evidence([10.0, 0.12, -8.0]),
        public_arguments=_arguments([0.0, 0.10, 0.0]),
    )
    assert value == pytest.approx(0.02)


def test_weighted_site_error_matches_metaworld_peg_formula() -> None:
    value = measure(
        {
            "kind": "final_weighted_site_position_error",
            "parameters": {
                "site_name": "fixture_site",
                "target_argument": "request.task_parameters.target_position",
                "weights": [1.0, 2.0, 2.0],
            },
        },
        evidence=_evidence([0.03, 0.04, 0.05]),
        public_arguments=_arguments([0.0, 0.0, 0.0]),
    )
    assert value == pytest.approx(math.sqrt(0.03**2 + 0.08**2 + 0.10**2))


def test_site_axis_error_rejects_an_invalid_axis() -> None:
    with pytest.raises(MeasurementError, match="axis 0, 1, or 2"):
        measure(
            {
                "kind": "final_site_axis_error",
                "parameters": {
                    "site_name": "fixture_site",
                    "target_argument": "request.task_parameters.target_position",
                    "axis": 3,
                },
            },
            evidence=_evidence([0.0, 0.0, 0.0]),
            public_arguments=_arguments([0.0, 0.0, 0.0]),
        )
