from __future__ import annotations

from pathlib import Path
import sys

import pytest


CORRECTED_ROOT = Path(__file__).resolve().parents[1]
if str(CORRECTED_ROOT) not in sys.path:
    sys.path.insert(0, str(CORRECTED_ROOT))

from runtime.contact_policy import evaluate_contact_integrity


def _metadata(*records: tuple[str, str, float]) -> dict[str, dict[str, object]]:
    return {
        geom: {
            "body_name": body,
            "minimum_positive_half_size_m": half_size,
        }
        for geom, body, half_size in records
    }


def _evidence(geom1: str, geom2: str, distance: float) -> dict[str, object]:
    return {
        "contact_monitoring_complete": True,
        "minimum_contact_distance_m": distance,
        "contact_pair_min_distances": [
            {
                "geom1": geom1,
                "geom2": geom2,
                "minimum_distance_m": distance,
            }
        ],
    }


def test_expected_go2_foot_support_26mm_penetration_passes_with_28mm_proxy() -> None:
    result = evaluate_contact_integrity(
        _evidence("FL_foot_geom", "floor", -0.026),
        robot_family="unitree-go2-stock-12dof",
        task_id="GO2-T02",
        geom_metadata=_metadata(
            ("FL_foot_geom", "FL_foot", 0.022),
            ("floor", "world", 0.1),
        ),
        semantic_roles={"FL_foot": "foot", "floor": "support"},
        expected_pairs={("FL_foot_geom", "floor")},
    )

    assert result["passed"] is True
    pair = result["deepest_contact_pair"]
    assert pair["body1"] == "FL_foot"
    assert pair["body2"] == "world"
    assert pair["minimum_distance_m"] == -0.026
    assert pair["applied_threshold_m"] == pytest.approx(0.028)
    assert pair["policy_applied"] == "expected_contact_geometry_proxy"


def test_unrelated_go2_body_fixture_over_5mm_fails_even_if_pair_is_declared() -> None:
    result = evaluate_contact_integrity(
        _evidence("base_geom", "fixture", -0.0051),
        robot_family="go2",
        task_id="GO2-T06",
        geom_metadata=_metadata(
            ("base_geom", "base_link", 0.05),
            ("fixture", "course_fixture", 0.05),
        ),
        semantic_roles={"base_link": "base", "fixture": "obstacle"},
        expected_pairs={("base_geom", "fixture")},
    )

    assert result["passed"] is False
    assert result["deepest_contact_pair"]["applied_threshold_m"] == 0.005
    assert result["deepest_contact_pair"]["proxy_moving_geom"] is None


def test_t17_pole_base_never_receives_foot_proxy() -> None:
    result = evaluate_contact_integrity(
        _evidence("RR_foot_geom", "pole_base_geom", -0.006),
        robot_family="go2",
        task_id="GO2-T17",
        geom_metadata=_metadata(
            ("RR_foot_geom", "RR_foot", 0.022),
            ("pole_base_geom", "pole_base", 0.025),
        ),
        semantic_roles={"RR_foot": "foot", "pole_base_geom": "pole_base"},
        expected_pairs={("RR_foot_geom", "pole_base_geom")},
    )

    assert result["passed"] is False
    assert result["deepest_contact_pair"]["policy_applied"] == "default_5mm"


def test_so101_expected_workpiece_support_uses_workpiece_half_size() -> None:
    result = evaluate_contact_integrity(
        _evidence("workpiece_geom", "work_surface", -0.011),
        robot_family="robotstudio_so101",
        task_id="mw_push_to_goal",
        geom_metadata=_metadata(
            ("workpiece_geom", "workpiece", 0.006),
            ("work_surface", "work_surface", 0.02),
        ),
        semantic_roles={"workpiece": "workpiece", "work_surface": "support"},
        expected_pairs={("workpiece_geom", "work_surface")},
    )

    assert result["passed"] is True
    assert result["deepest_contact_pair"]["applied_threshold_m"] == pytest.approx(
        0.012
    )


def test_so101_wall_contact_stays_at_5mm() -> None:
    result = evaluate_contact_integrity(
        _evidence("workpiece_geom", "wall_geom", -0.006),
        robot_family="so101",
        task_id="mw_pick_place_wall",
        geom_metadata=_metadata(
            ("workpiece_geom", "workpiece", 0.02),
            ("wall_geom", "wall", 0.01),
        ),
        semantic_roles={"workpiece": "workpiece", "wall": "wall"},
        expected_pairs={("workpiece_geom", "wall_geom")},
    )

    assert result["passed"] is False
    assert result["deepest_contact_pair"]["applied_threshold_m"] == 0.005


def test_no_contacts_passes_when_monitoring_is_complete() -> None:
    result = evaluate_contact_integrity(
        {
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": None,
            "contact_pair_min_distances": [],
        },
        robot_family="go2",
        task_id="GO2-T03",
        geom_metadata={},
        semantic_roles={},
        expected_pairs=set(),
    )

    assert result["passed"] is True
    assert result["deepest_contact_pair"] is None
