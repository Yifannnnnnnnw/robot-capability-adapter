from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autoadapter2.foundation.errors import ContractError
from autoadapter2.integration.robot_facts import validate_robot_facts


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _manifest(robot: str) -> dict:
    path = PROJECT_ROOT / "general_demo" / "integrations" / robot / "integration_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _mutated_project(tmp_path: Path, manifest: dict, ref_name: str, mutate) -> dict:
    result = copy.deepcopy(manifest)
    for name in ("morphology_ref", "sdk_ref", "translation_ref"):
        source = PROJECT_ROOT / result[name]["path"]
        target = tmp_path / result[name]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        value = json.loads(source.read_text(encoding="utf-8"))
        if name == ref_name:
            mutate(value)
        target.write_text(json.dumps(value), encoding="utf-8")
    return result


def test_checked_in_robot_records_match_frozen_facts() -> None:
    validate_robot_facts(PROJECT_ROOT, _manifest("so-arm101"))
    validate_robot_facts(PROJECT_ROOT, _manifest("unitree-go2"))


@pytest.mark.parametrize("robot", ["so-arm101"])
def test_top_level_ready_cannot_hide_pending_robot_dependencies(robot: str) -> None:
    manifest = _manifest(robot)
    manifest["status"] = "READY"
    with pytest.raises(ContractError):
        validate_robot_facts(PROJECT_ROOT, manifest)


def test_go2_ready_dependencies_are_verified() -> None:
    validate_robot_facts(PROJECT_ROOT, _manifest("unitree-go2"))


@pytest.mark.parametrize(
    ("ref_name", "mutate"),
    [
        ("morphology_ref", lambda value: value["joint_names"].reverse()),
        ("sdk_ref", lambda value: value["units"].update(arm="rad")),
        ("sdk_ref", lambda value: value["packages"][0].update(sha256="0" * 64)),
        ("translation_ref", lambda value: value["motor_mapping"][0].update(motor_id=2)),
        ("translation_ref", lambda value: value["registers"]["Goal_Position"].update(address=43)),
    ],
)
def test_so_frozen_fact_mutants_fail(tmp_path: Path, ref_name: str, mutate) -> None:
    manifest = _mutated_project(tmp_path, _manifest("so-arm101"), ref_name, mutate)
    with pytest.raises(ContractError):
        validate_robot_facts(tmp_path, manifest)


@pytest.mark.parametrize(
    ("ref_name", "mutate"),
    [
        ("morphology_ref", lambda value: value["actuator_names"].reverse()),
        ("sdk_ref", lambda value: value.update(motor_slot_count=13)),
        ("sdk_ref", lambda value: value["topics"].update(command="rt/wrong")),
        ("translation_ref", lambda value: value["active_motor_mapping"][0].update(actuator="FL_hip")),
        ("translation_ref", lambda value: value["inactive_slot_rule"].update(q=0.0)),
        ("translation_ref", lambda value: value["stale_rule"].update(limit_s=999.0)),
    ],
)
def test_go2_frozen_fact_mutants_fail(tmp_path: Path, ref_name: str, mutate) -> None:
    manifest = _mutated_project(tmp_path, _manifest("unitree-go2"), ref_name, mutate)
    with pytest.raises(ContractError):
        validate_robot_facts(tmp_path, manifest)
