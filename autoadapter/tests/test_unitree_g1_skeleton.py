from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree_g1" / "1.0.0"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "joint_position.py"


def _load_skeleton():
    module_name = "unitree_g1_package_skeleton"
    spec = importlib.util.spec_from_file_location(module_name, SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_g1_skeleton_is_task_neutral_and_steps_real_mujoco() -> None:
    source = SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("task_id", "scoring", "tasks/private", "humanoidbench", "robocup"):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mujoco.mj_step" in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            assert not any(
                isinstance(child, ast.Attribute) and child.attr in {"qpos", "qvel"}
                for child in ast.walk(target)
            )

    module = _load_skeleton()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, {"kind": "keyframe", "name": "stand"})
    skeleton = module.G1JointPositionSkeleton.from_session(
        model=model,
        data=data,
        spec=module.G1JointPositionSpec.default(),
    )
    description = skeleton.describe()
    assert description["joint_names"] == list(module.G1_JOINT_NAMES)
    assert description["actuator_names"] == list(module.G1_ACTUATOR_NAMES)
    assert len(description["joint_limits_rad"]) == 29

    target = skeleton.get_joint_positions()
    target[0] += 0.12
    observed = skeleton.command_joint_positions(target, steps=12)
    assert observed["simulation_time"] > 0.0
    assert np.isfinite(observed["joint_positions"]).all()
    assert np.isfinite(observed["joint_velocities"]).all()
    assert np.isfinite(observed["base_position"]).all()
    assert np.isfinite(observed["base_quaternion"]).all()
    assert np.isfinite(observed["actuator_controls"]).all()
