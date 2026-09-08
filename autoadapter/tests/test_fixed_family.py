"""Focused checks for fixed-family admission and observed measurement defects."""
import copy
from pathlib import Path

import mujoco
import pytest

from autoadapter2.fixed_family import reference
from autoadapter2.harness.b1_contracts import evaluate_b1_contract
from autoadapter2.harness.operators import inspect_scene_entities
from autoadapter2.harness.runner import _trusted_measurement_evidence
from autoadapter2.libraries import load_indexed_robot_package
from autoadapter2.pipeline import PipelineHooks, _load_fixed_inputs
from autoadapter2.validation_compiler import IVCError, validate_capability_validation_suite

ROOT = Path(__file__).resolve().parents[1]


def test_unnamed_geom_catalog_alias_is_measurable(tmp_path):
    scene = tmp_path / "scene.xml"
    scene.write_text('<mujoco><worldbody><geom type="sphere" size=".1"/>'
                     '<geom type="sphere" size=".1" pos="1 0 0"/></worldbody></mujoco>')
    assert inspect_scene_entities(scene)["geoms"] == ["geom_0", "geom_1"]
    binding = {"kind": "final_geom_pair_distance", "parameters": {
        "geom_a_name": "geom_0", "geom_b_name": "geom_1"}}
    enriched = _trusted_measurement_evidence(
        scene_path=scene, binding=binding, evidence={"samples": [{"time": 0, "qpos": []}]},
    )
    assert enriched["samples"][0]["trusted_geom_pair_distances"][0]["distance_m"] == pytest.approx(.8)


def test_fixed_arm_side_effect_guard_rejects_gripper_drift():
    parameters = {"contract_id": "A1", "site_name": "tcp", "side_effect_guard_profile": "fixed_arm",
                  "guarded_joint_names": ["finger"], "guarded_joint_tolerances": [.002]}
    evidence = {"samples": [{"time": t, "site_positions": {"tcp": [0, 0, 0]},
                             "joint_positions": {"finger": 0.0}} for t in [0, .25, .5]]}
    request = {"target_position_m": [0, 0, 0], "max_duration_s": 1}
    assert evaluate_b1_contract(parameters, evidence=evidence, request=request) == 1
    evidence["samples"][-1]["joint_positions"]["finger"] = .003
    assert evaluate_b1_contract(parameters, evidence=evidence, request=request) == 0


def test_fixed_go2_requires_exact_private_standard():
    robot = "unitree-go2-stock-12dof"
    package = load_indexed_robot_package(ROOT, robot)
    fixed, _ = _load_fixed_inputs(ROOT / "references/fixed_family_v1", packages={robot: package},
                                  hooks=PipelineHooks(), require_task_support=False)
    suite = copy.deepcopy(fixed[robot]["suite"])
    suite["cases"][0]["measurement_binding"]["parameters"]["body_name"] = "world"
    with pytest.raises(IVCError, match="repository-owned"):
        validate_capability_validation_suite(suite, package=package,
                                             design=fixed[robot]["design"], fixed_family=True)
