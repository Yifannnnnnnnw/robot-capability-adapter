"""Focused checks for fixed-family admission and observed measurement defects."""
import copy
import json
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
from autoadapter2.capability_design import validate_capability_design
from autoadapter2.trusted_skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton, QuadrupedSpec

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


def test_eleven_fixed_packages_have_108_audited_cases():
    robots = json.loads((ROOT / "configs/diagnostics/fixed-family-v1.json").read_text())["robots"]
    packages = {robot: load_indexed_robot_package(ROOT, robot, require_task_library=False) for robot in robots}
    fixed, _ = _load_fixed_inputs(ROOT / "references/fixed_family_v1", packages=packages,
                                  hooks=PipelineHooks(), require_task_support=False)
    assert len(fixed) == 11
    assert sum(len(item["suite"]["cases"]) for item in fixed.values()) == 108
    assert {c["capability_id"] for c in fixed["kuka_iiwa_14"]["design"]["capabilities"]} == {"A1", "A2", "A4", "A5"}
    design = copy.deepcopy(fixed["franka_panda"]["design"])
    design["capabilities"][0]["criteria"][0]["threshold"] = .5
    with pytest.raises(ValueError, match="non-executable transfer|rich semantic"):
        validate_capability_design(design, packages["franka_panda"], require_task_support=False)


@pytest.mark.parametrize("robot", ["unitree_a1", "anybotics_anymal_c"])
def test_new_quadruped_native_position_control_and_task_boundary(robot):
    package = load_indexed_robot_package(ROOT, robot, require_task_library=False)
    with pytest.raises(ValueError, match="non-empty|20 tasks"):
        load_indexed_robot_package(ROOT, robot)
    model = mujoco.MjModel.from_xml_path(str(package.mjcf_path))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    config = package.morphology["public_control"]["skeleton_spec"]
    assert config["actuation"] == "joint_position"
    skeleton = QuadrupedPDGaitSkeleton(model=model, data=data, spec=QuadrupedSpec(**config))
    before = data.qpos.copy()
    skeleton.apply_pd_posture(config["home_qpos"])
    assert list(data.qpos) == list(before)
    assert list(data.ctrl[skeleton._actuator_ids]) == pytest.approx(config["home_qpos"])
    skeleton.step(5)
    assert data.time > 0
