from __future__ import annotations

import ast
import importlib.util
import json
import math
import sys
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
SCENE_PATH = PACKAGE_ROOT / "assets" / "leap_cube_policy_scene.xml"
PACKAGE_SKELETON_PATH = (
    PACKAGE_ROOT / "skeleton" / "leap_cube_reorientation.py"
)
TRUSTED_SKELETON_PATH = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "leap_cube_reorientation.py"
)
POLICY_RESOURCE = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "data"
    / "leap_cube_reorientation_policy.npz"
)
METADATA_PATH = (
    PACKAGE_ROOT / "reference" / "leap_cube_reorientation_policy.json"
)


def _load_package_skeleton():
    module_name = "leap_cube_reorientation_policy_inventory"
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE_SKELETON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _session(goal_quaternion: np.ndarray | None = None):
    module = _load_package_skeleton()
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, {"kind": "keyframe", "name": "home"})
    if goal_quaternion is not None:
        data.mocap_quat[0] = goal_quaternion
        mujoco.mj_forward(model, data)
    skeleton = module.LeapCubeReorientationSkeleton(
        model=model,
        data=data,
        spec=module.LEAP_CUBE_REORIENTATION_SPEC,
    )
    return model, data, skeleton


def test_leap_policy_scene_matches_upstream_deployment_contract() -> None:
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    assert (model.nq, model.nv, model.nu, model.nbody, model.ngeom) == (
        23,
        22,
        16,
        21,
        61,
    )
    assert (model.nsite, model.nsensor, model.nkey) == (6, 13, 1)
    assert model.opt.timestep == 0.002
    assert model.opt.iterations == 5
    assert model.opt.ls_iterations == 8
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home") == 0
    assert np.allclose(model.key_ctrl[0], model.key_qpos[0, :16])


def test_leap_policy_conversion_matches_upstream_onnx_fixture() -> None:
    _, _, skeleton = _session()
    skeleton._resolve()
    actual = skeleton._infer(np.zeros(57, dtype=np.float32))
    expected = np.asarray(
        [
            0.356846213,
            -0.190122068,
            0.622865677,
            0.302636206,
            -0.241226181,
            -0.206620589,
            0.23049973,
            0.323973775,
            0.768528759,
            0.682882667,
            0.83566314,
            0.729100585,
            0.695410013,
            0.726188004,
            0.714010417,
            0.21225597,
        ],
        dtype=np.float32,
    )
    assert np.allclose(actual, expected, rtol=0.0, atol=1.0e-5)


def test_leap_policy_is_goal_conditioned_and_actuator_only() -> None:
    source = TRUSTED_SKELETON_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "task_id",
        "scoring",
        "tasks/private",
        "reference/driver",
        "from_xml_path",
        "mj_resetdata",
        "onnxruntime",
    ):
        assert forbidden not in lowered
    assert "data.ctrl" in source
    assert "mj_step" in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            assert not any(
                isinstance(child, ast.Attribute)
                and child.attr in {"qpos", "qvel", "mocap_pos", "mocap_quat"}
                for child in ast.walk(target)
            )


def test_leap_policy_reorients_cube_in_direct_mujoco() -> None:
    half_turn = math.pi / 4.0
    goal = np.asarray(
        [math.cos(half_turn), 0.0, 0.0, math.sin(half_turn)], dtype=float
    )
    _, data, skeleton = _session(goal)
    qpos_before = data.qpos.copy()
    initial_error = skeleton.get_cube_state()["orientation_error_rad"]
    result = skeleton.track_orientation(10.0, stop_below_rad=0.1)

    assert initial_error > 1.0
    assert result["minimum_orientation_error_rad"] < 0.1
    assert result["final_orientation_error_rad"] < 0.1
    assert result["palm_cube_distance_m"] < 0.08
    assert 0 < result["physics_steps"] < 5000
    assert result["policy_inference_count"] > 0
    assert not np.allclose(data.qpos, qpos_before)
    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()


def test_leap_policy_artifact_scope_and_provenance_are_explicit() -> None:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    assert metadata["artifact_id"] == "mujoco-playground-leap-cube-reorient"
    assert metadata["source_revision"] == (
        "e74217bb89c77a74ba02e4789263991864375799"
    )
    assert metadata["retained_resource"].endswith(
        "leap_cube_reorientation_policy.npz"
    )
    assert metadata["onnx_runtime_required"] is False
    assert metadata["validated_scope"].startswith("bounded single-cube orientation")
    assert "package-wide reference success" in metadata["not_validated_for"]
    assert POLICY_RESOURCE.is_file()
    assert (PACKAGE_ROOT / metadata["license_file"]).is_file()

    with np.load(POLICY_RESOURCE, allow_pickle=False) as retained:
        assert retained["observation_mean"].shape == (57,)
        assert retained["layer_0_weight"].shape == (57, 512)
        assert retained["layer_1_weight"].shape == (512, 256)
        assert retained["layer_2_weight"].shape == (256, 128)
        assert retained["layer_3_weight"].shape == (128, 32)
        assert all(np.isfinite(retained[name]).all() for name in retained.files)
