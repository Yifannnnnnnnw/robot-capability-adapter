from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "unitree_g1" / "1.0.0"
SCENE_PATH = PACKAGE_ROOT / "assets" / "g1_mjlab_policy_scene.xml"
PACKAGE_SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "g1_velocity_policy.py"
TRUSTED_SKELETON_PATH = (
    ROOT / "src" / "autoadapter2" / "trusted_skeletons" / "g1_velocity_policy.py"
)
POLICY_RESOURCE = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "data"
    / "g1_velocity_policy.npz"
)
METADATA_PATH = PACKAGE_ROOT / "reference" / "g1_velocity_policy.json"


def _load_package_skeleton():
    module_name = "unitree_g1_velocity_policy_inventory"
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE_SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _session(module):
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, {"kind": "keyframe", "name": "home"})
    skeleton = module.G1VelocityPolicySkeleton(
        model=model,
        data=data,
        spec=module.G1_VELOCITY_POLICY_SPEC,
    )
    return model, data, skeleton


def test_g1_policy_scene_matches_retained_training_contract() -> None:
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nbody, model.ngeom) == (
        36,
        35,
        29,
        31,
        69,
    )
    assert (model.nsite, model.nsensor, model.nkey) == (6, 4, 1)
    assert model.opt.timestep == 0.005
    assert model.opt.iterations == 10
    assert model.opt.ls_iterations == 20
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home") == 0
    assert np.isclose(model.key_qpos[0, 2], 0.8)
    assert np.allclose(model.key_ctrl[0], model.key_qpos[0, 7:])


def test_g1_policy_conversion_matches_upstream_onnx_fixture() -> None:
    module = _load_package_skeleton()
    _, _, skeleton = _session(module)
    skeleton._resolve()
    actual = skeleton._infer(np.zeros(98, dtype=np.float32))
    expected = np.asarray(
        [
            -0.100641295, -0.0834358409, 0.0272081718, -0.131985545,
            0.356105, 0.186826885, -0.0476036891, -0.174490824,
            0.0837012306, -0.254235655, -0.165358126, -0.0663399249,
            -0.00082809478, 0.025956329, 0.197746098, 0.308097929,
            0.852711499, 0.244175926, 0.229756519, -0.190681532,
            -0.130930513, 0.832206845, 0.282841951, -0.524270356,
            -0.451460898, -0.166639239, 0.185848609, 0.130170316,
            -0.242952347,
        ],
        dtype=np.float32,
    )
    assert np.allclose(actual, expected, rtol=0.0, atol=1.0e-5)


def test_g1_policy_is_task_neutral_and_actuator_only() -> None:
    source = TRUSTED_SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "task_id",
        "scoring",
        "tasks/private",
        "humanoidbench",
        "robocup",
        "reference/driver",
    ):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mj_step" in source
    assert "onnxruntime" not in lowered

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


def test_g1_policy_holds_and_walks_in_direct_mujoco() -> None:
    module = _load_package_skeleton()

    _, hold_data, hold_policy = _session(module)
    hold_start = hold_data.qpos[:3].copy()
    hold_result = hold_policy.hold(4.0)
    hold_delta = hold_data.qpos[:3] - hold_start
    assert np.linalg.norm(hold_delta[:2]) < 0.04
    assert hold_data.qpos[2] > 0.75
    assert hold_result["base_rotation"][2, 2] > 0.99
    assert hold_result["policy_inference_count"] == 200

    _, walk_data, walk_policy = _session(module)
    walk_start = walk_data.qpos[:3].copy()
    walk_result = walk_policy.command_velocity(0.3, 0.0, 0.0, 4.0)
    walk_delta = walk_data.qpos[:3] - walk_start
    assert walk_delta[0] > 0.9
    assert abs(walk_delta[1]) < 0.2
    assert walk_data.qpos[2] > 0.75
    assert walk_result["base_rotation"][2, 2] > 0.99
    assert walk_result["policy_inference_count"] == 200


def test_g1_policy_artifact_and_provenance_are_self_contained() -> None:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    assert metadata["artifact_id"] == "unitree-mjlab-g1-velocity-v0"
    assert metadata["source_revision"] == "1425b15f73bd4095f0df53709d7c389c3eb9e790"
    assert metadata["retained_resource"].endswith("g1_velocity_policy.npz")
    assert metadata["onnx_runtime_required"] is False
    assert POLICY_RESOURCE.is_file()
    assert (PACKAGE_ROOT / metadata["license_file"]).is_file()

    with np.load(POLICY_RESOURCE, allow_pickle=False) as retained:
        assert retained["observation_mean"].shape == (98,)
        assert retained["layer_0_weight"].shape == (512, 98)
        assert retained["layer_1_weight"].shape == (256, 512)
        assert retained["layer_2_weight"].shape == (128, 256)
        assert retained["layer_3_weight"].shape == (29, 128)
        assert all(np.isfinite(retained[name]).all() for name in retained.files)
