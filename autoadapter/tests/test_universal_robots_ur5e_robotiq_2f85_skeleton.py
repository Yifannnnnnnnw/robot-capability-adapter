from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.driver_synthesis.generation import build_public_generation_inputs
from autoadapter2.libraries import RobotPackage
from autoadapter2.trusted_skeletons import (
    ArmSerialDLSSkeleton,
    ArmSpec,
    IKUnreachableError,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT
    / "libraries"
    / "robots"
    / "universal_robots_ur5e_robotiq_2f85"
    / "1.0.0"
)
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "arm_serial_dls.py"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"


def _load_inventory():
    module_spec = importlib.util.spec_from_file_location(
        "universal_robots_ur5e_robotiq_2f85_skeleton_inventory",
        SKELETON_PATH,
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _public_package() -> RobotPackage:
    morphology = json.loads(
        (PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8")
    )
    sources_document = json.loads(
        (PACKAGE_ROOT / "tasks" / "sources.json").read_text(encoding="utf-8")
    )
    catalog = json.loads(
        (PACKAGE_ROOT / "tasks" / "catalog.json").read_text(encoding="utf-8")
    )
    return RobotPackage(
        root=PACKAGE_ROOT,
        robot_configuration_id=morphology["robot_configuration_id"],
        package_version=morphology["package_version"],
        snapshot_id=catalog["snapshot_id"],
        morphology=morphology,
        sources=tuple(sources_document["sources"]),
        tasks=tuple(catalog["tasks"]),
        mjcf_path=SCENE_PATH,
        skeleton_dir=PACKAGE_ROOT / "skeleton",
        reference_driver=PACKAGE_ROOT / "reference" / "driver.py",
        private_dir=PACKAGE_ROOT / "tasks" / "private",
    )


def test_ur5e_robotiq_inventory_and_canonical_session_are_live() -> None:
    module = _load_inventory()
    assert module.ArmSerialDLSSkeleton is ArmSerialDLSSkeleton
    assert module.ArmSpec is ArmSpec
    assert module.IKUnreachableError is IKUnreachableError
    assert module.__all__ == ["ArmSerialDLSSkeleton", "ArmSpec", "IKUnreachableError"]

    package = _public_package()
    design = {
        "capabilities": [
            {"capability_id": "arm_motion", "method_name": "move_arm"},
        ]
    }
    inputs = build_public_generation_inputs(
        package,
        design,
        condition="skeleton-assisted",
    )
    source_files = {
        item["path"]: item["source"]
        for item in inputs["condition_eligible_artifacts"]["source_files"]
    }
    assert "arm_serial_dls.py" in source_files
    runtime_path = "runtime/autoadapter2/trusted_skeletons/arm_serial_dls.py"
    assert runtime_path in source_files
    assert "class ArmSerialDLSSkeleton" in source_files[runtime_path]

    public_asset_paths = {
        item["path"]
        for item in inputs["public_robot_package"]["selected_mjcf_closure"]["files"]
    }
    private_scene_names = {
        Path(instance["scene_entrypoint"]).name
        for instance in json.loads(
            (PACKAGE_ROOT / "tasks" / "private" / "instances.json").read_text(
                encoding="utf-8"
            )
        )["instances"]
    }
    assert "scene.xml" in public_asset_paths
    assert public_asset_paths.isdisjoint(private_scene_names)

    morphology = package.morphology
    control = morphology["public_control"]
    observations = morphology["public_observations"]
    assert tuple(control["arm_joint_names"]) == (
        "shoulder_pan_joint",
        "shoulder_lift_joint",
        "elbow_joint",
        "wrist_1_joint",
        "wrist_2_joint",
        "wrist_3_joint",
    )
    assert tuple(control["arm_actuator_names"]) == (
        "shoulder_pan",
        "shoulder_lift",
        "elbow",
        "wrist_1",
        "wrist_2",
        "wrist_3",
    )
    assert control["arm_joint_limits_rad"] == {
        "shoulder_pan_joint": [-6.28319, 6.28319],
        "shoulder_lift_joint": [-6.28319, 6.28319],
        "elbow_joint": [-3.1415, 3.1415],
        "wrist_1_joint": [-6.28319, 6.28319],
        "wrist_2_joint": [-6.28319, 6.28319],
        "wrist_3_joint": [-6.28319, 6.28319],
    }
    assert control.get("continuous_joint_names", []) == []
    home_arm = tuple(morphology["reset_fact"]["home_qpos"][:6])
    assert home_arm == (-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0)

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    assert home_id >= 0
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)

    skeleton = module.ArmSerialDLSSkeleton.from_session(
        model=model,
        data=data,
        spec=module.ArmSpec(
            ee_site_name=observations["end_effector_site"],
            arm_joint_names=control["arm_joint_names"],
            arm_actuator_names=control["arm_actuator_names"],
            joint_limits=control["arm_joint_limits_rad"],
            continuous_joint_names=(),
            home_qpos=home_arm,
            gripper_actuator_names=("fingers_actuator",),
            gripper_close_ctrl=control["gripper_direction"]["closed"],
            gripper_open_ctrl=control["gripper_direction"]["open"],
        ),
    )

    ee_position, ee_rotation = skeleton.get_ee_pose()
    joint_positions = skeleton.get_joint_positions()
    joint_velocities = skeleton.get_joint_velocities()
    assert ee_position.shape == (3,)
    assert ee_rotation.shape == (3, 3)
    np.testing.assert_allclose(joint_positions, home_arm, atol=1e-8)
    assert np.isfinite(ee_position).all()
    assert np.isfinite(ee_rotation).all()
    assert np.isfinite(joint_velocities).all()

    target = np.asarray(home_arm, dtype=float)
    target[0] += 0.05
    start_time = float(data.time)
    assert skeleton.move_joints(target, duration=0.02) is True
    assert float(data.time) > start_time
    arm_ids = [
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        for name in control["arm_actuator_names"]
    ]
    np.testing.assert_allclose(data.ctrl[arm_ids], target, atol=1e-12)

    gripper_id = mujoco.mj_name2id(
        model,
        mujoco.mjtObj.mjOBJ_ACTUATOR,
        "fingers_actuator",
    )
    after_arm_time = float(data.time)
    assert skeleton.gripper_close(settle_steps=2) is True
    assert float(data.time) > after_arm_time
    assert data.ctrl[gripper_id] == 255.0
    after_close_time = float(data.time)
    assert skeleton.gripper_open(settle_steps=2) is True
    assert float(data.time) > after_close_time
    assert data.ctrl[gripper_id] == 0.0
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
