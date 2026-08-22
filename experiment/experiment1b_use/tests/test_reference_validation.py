"""Focused false-success checks for the B2 reference-calibration gate."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
REFERENCE_ROOT = HERE.parent / "validation" / "reference"
if str(REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(REFERENCE_ROOT))

import build_suites  # noqa: E402
import run_reference_calibration as calibration  # noqa: E402


def test_fixed_selection_is_the_exact_authority_cohort_and_paths() -> None:
    selections = build_suites.load_selection()
    observed = {
        value["robot_configuration_id"]: value for value in selections
    }
    assert observed == build_suites.EXPECTED_SELECTIONS


def test_duplicate_easy_case_cannot_replace_a_complete_suite() -> None:
    suite = json.loads(
        (
            REFERENCE_ROOT
            / "resolved/robotstudio_so101/capability_validation_suite.json"
        ).read_text(encoding="utf-8")
    )
    cases = copy.deepcopy(suite["cases"])
    cases[-1] = copy.deepcopy(cases[0])
    methods = {
        "A1": "move_end_effector_to_position",
        "A2": "trace_cartesian_path",
        "A3": "set_gripper_opening",
        "A4": "approach_until_contact",
        "A5": "move_cartesian_offset_and_return",
        "A6": "set_wrist_roll",
    }
    with pytest.raises(ValueError, match="duplicate case IDs"):
        build_suites._validate_case_set("robotstudio_so101", cases, methods)


@pytest.mark.parametrize(
    ("record_video", "complete_cohort", "inputs_clean"),
    [
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_incomplete_evidence_cannot_clear_the_prerequisite(
    record_video: bool,
    complete_cohort: bool,
    inputs_clean: bool,
) -> None:
    summaries = [
        {"validation_passed": True},
        {"validation_passed": True},
    ]
    assert not calibration._prerequisite_passed(
        record_video=record_video,
        complete_robot_cohort=complete_cohort,
        calibration_inputs_clean=inputs_clean,
        summaries=summaries,
    )


def test_video_evidence_requires_both_request_and_complete() -> None:
    assert not calibration._video_evidence_complete(
        {"requested": False, "complete": True}
    )
    assert not calibration._video_evidence_complete(
        {"requested": True, "complete": False}
    )
    assert calibration._video_evidence_complete(
        {"requested": True, "complete": True}
    )


def test_resolved_so101_inherits_the_b1_a6_contract() -> None:
    design = json.loads(
        (
            REFERENCE_ROOT / "resolved/robotstudio_so101/capability_design.json"
        ).read_text(encoding="utf-8")
    )
    suite = json.loads(
        (
            REFERENCE_ROOT / "resolved/robotstudio_so101/capability_validation_suite.json"
        ).read_text(encoding="utf-8")
    )
    assert design["capability_design_id"] == build_suites.SOURCE_DESIGN_IDS[
        "robotstudio_so101"
    ]
    assert [value["capability_id"] for value in design["capabilities"]] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
    ]
    a6_cases = [
        value for value in suite["cases"] if value["capability_id"] == "A6"
    ]
    assert len(suite["cases"]) == 18
    assert [value["case_id"] for value in a6_cases] == [
        "A6-H1",
        "A6-H2",
        "A6-H3",
    ]
    assert {
        value["request"]["target_roll_rad"] < 0.0 for value in a6_cases
    } == {False, True}
