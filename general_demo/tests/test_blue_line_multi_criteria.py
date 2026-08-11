from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autoadapter2.blue_line import BlueLineRunner
from autoadapter2.generation import FixtureJsonGenerator, Stage1Config, Stage1Runner
from autoadapter2.validation.validation_b import _rule_entries


ROOT = Path(__file__).parents[1]
STANDARD_PATH = ROOT / "private_governance" / "blue_line" / "standards" / "go2_g01_stand_v1.json"
ROBOT = {
    "robot_model_id": "unitree-go2",
    "robot_configuration_id": "unitree-go2-stock-12dof",
    "action_affordances": ["body motion command"],
    "observation_affordances": ["body state observation"],
    "effect_allowlist": ["stand", "sit"],
    "unit_allowlist": ["m", "unitless", "m/s"],
    "frame_allowlist": ["world", "body"],
}
TASKS = [{"requirement_id": "req-stand", "description": "Stand and hold the robot upright."}]
PROFILE = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
POLICY = {
    "policy_id": "blue-first-demo",
    "model_id": "fixed-fixture",
    "prompt_id": "blue-first-demo-v1",
    "max_cases_per_capability": 2,
    "repetitions": 1,
}


def _record() -> dict:
    return json.loads(STANDARD_PATH.read_text())


def _body(effect: str = "stand") -> dict:
    return {
        "capabilities": [{
            "capability_id": "stand",
            "kind": "action",
            "requirement_ids": ["req-stand"],
            "inputs": [],
            "outputs": [],
            "effect": effect,
            "preconditions": ["robot is reset"],
            "invocation_semantics": "Invoke once with the frozen task inputs.",
            "temporal_semantics": "Returns after the robot state has been observed.",
            "invariants": ["does not claim unobserved physical success"],
            "required_action_affordances": ["body motion command"],
            "required_observation_affordances": ["body state observation"],
            "errors": [{"code": "MOTION_REJECTED", "message": "Motion was rejected."}],
            "unsupported_scope": ["unapproved motion effects"],
        }],
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }


def _sealed_design(effect: str = "stand") -> tuple[dict, dict]:
    result = Stage1Runner(
        FixtureJsonGenerator([_body(effect)]),
        Stage1Config(max_correction_calls=0),
    ).run("run-blue-multi", ROBOT, TASKS, PROFILE)
    assert result.status == "SEALED" and result.capability_design and result.seal
    return result.capability_design, result.seal


def _snapshot_and_catalog(record: dict) -> tuple[dict, dict]:
    measurements = []
    for criterion in record["criteria"]:
        measurements.append({
            "measurement_id": criterion["measurement_id"],
            "entity": criterion["measurement_id"],
            "unit": "m/s" if criterion["metric"].endswith("_m_s") else "unitless",
            "frame": "world",
            "adapter_id": "go2-truth-state",
            "truth_source": "physical_state",
            "metrics": [criterion["metric"]],
        })
    guards = [
        {"guard_id": guard_id, "adapter_id": "go2-truth-state"}
        for guard_id in record["required_guard_ids"]
    ]
    return {"snapshot_id": "go2-first-demo-stand-v1", "standards": [record]}, {
        "catalog_id": "go2-first-demo-measurements-v1",
        "measurements": measurements,
        "guards": guards,
    }


def _spec(record: dict, *, criteria: list[dict] | None = None) -> dict:
    selected = criteria if criteria is not None else record["criteria"]
    return {
        "capability_specs": [{
            "capability_id": "stand",
            "criteria": copy.deepcopy(selected),
            "cases": [{"case_id": "nominal", "initial_state": {"posture": "supported"}, "inputs": {}}],
            "lineage": {"kind": "COPIED", "standard_id": record["standard_id"], "material": False},
            "false_pass_analysis": [
                {"risk_id": guard_id, "guard_id": guard_id}
                for guard_id in record["required_guard_ids"]
            ],
        }],
    }


def _run(spec: dict, *, effect: str = "stand"):
    design, seal = _sealed_design(effect)
    record = _record()
    standards, catalog = _snapshot_and_catalog(record)
    return BlueLineRunner(FixtureJsonGenerator([spec, copy.deepcopy(spec), copy.deepcopy(spec)])).run(
        design, seal, standards, catalog, POLICY
    )


def test_complete_go2_stand_is_ready_with_all_three_criteria() -> None:
    record = _record()
    result = _run(_spec(record))
    assert result.status == "READY"
    assert result.validation_suite is not None
    entry = result.validation_suite["capability_cases"][0]
    assert [item["criterion_id"] for item in entry["criteria"]] == [
        "g01-body-height", "g01-upright", "g01-planar-speed"
    ]
    assert set(result.validation_suite["capability_cases"][0]["false_pass_analysis"][0]) == {"risk_id", "guard_id"}

    rules, repetitions = _rule_entries(
        result.validation_suite, result.validation_spec, {"stand"}
    )
    assert repetitions == 1
    assert [rule["criterion_id"] for rule in rules] == [
        "g01-body-height", "g01-upright", "g01-planar-speed"
    ]


def test_height_only_stand_cannot_be_ready() -> None:
    record = _record()
    result = _run(_spec(record, criteria=record["criteria"][:1]))
    assert result.status == "NEEDS_REVIEW"
    assert "COPIED_STANDARD_COVERAGE" in {item["code"] for item in result.diagnostics}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("threshold", "COPIED_STANDARD_BINDING"),
        ("guard", "COPIED_STANDARD_GUARD_BINDING"),
        ("risk", "FALSE_PASS_ANALYSIS"),
    ],
)
def test_copied_stand_rejects_changed_threshold_guard_or_risk_mapping(mutation: str, expected: str) -> None:
    record = _record()
    spec = _spec(record)
    if mutation == "threshold":
        spec["capability_specs"][0]["criteria"][0]["threshold_value"] = 0.16
    elif mutation == "guard":
        spec["capability_specs"][0]["criteria"][0]["guard_ids"] = ["sdk_receipt_not_completion"]
    else:
        spec["capability_specs"][0]["false_pass_analysis"][0]["guard_id"] = "entity_unit_frame_match"
    result = _run(spec)
    assert result.status == "NEEDS_REVIEW"
    assert expected in {item["code"] for item in result.diagnostics}


def test_stage1_effect_allowlist_and_blue_line_scope_are_exact() -> None:
    result = Stage1Runner(
        FixtureJsonGenerator([_body("unapproved-effect")]),
        Stage1Config(max_correction_calls=0),
    ).run("run-blue-effect", ROBOT, TASKS, PROFILE)
    assert result.status == "FAILED"
    assert "PUBLIC_EFFECT" in {item["code"] for item in result.diagnostics}

    record = _record()
    standards, catalog = _snapshot_and_catalog(record)
    design, seal = _sealed_design("sit")
    spec = _spec(record)
    result = BlueLineRunner(FixtureJsonGenerator([spec, copy.deepcopy(spec), copy.deepcopy(spec)])).run(
        design, seal, standards, catalog, POLICY
    )
    assert result.status == "NEEDS_REVIEW"
    assert "STANDARD_SCOPE" in {item["code"] for item in result.diagnostics}


def test_all_first_demo_records_are_approved_and_versioned() -> None:
    paths = sorted((ROOT / "private_governance" / "blue_line" / "standards").glob("*.json"))
    records = [json.loads(path.read_text()) for path in paths if path.name != "reference_candidates.json"]
    assert len(records) == 10
    assert {record["effect_id"] for record in records} == {
        "stand", "sit", "hold_stable", "move_forward", "target_body_height",
        "move_end_effector_to_target", "establish_target_contact", "move_object_to_region",
        "grasp_and_lift_object", "actuate_target_button",
    }
    for record in records:
        assert record["record_status"] == "APPROVED"
        assert record["intended_use"] == "capability_validation_b"
        assert record["version"]
        assert record["criteria"]
        assert record["required_guard_ids"]
        assert record["false_pass_requirements"]
        assert record["approval_lineage"]["approval_id"]
