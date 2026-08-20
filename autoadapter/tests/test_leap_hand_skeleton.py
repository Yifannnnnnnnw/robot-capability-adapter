from __future__ import annotations

import ast
import json
import inspect
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter2.driver_synthesis import SessionBoundSkeleton, discover_primitives
from autoadapter2.trusted_skeletons import (
    HandJointPositionSkeleton,
    HandJointPositionSpec,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene_right.xml"
MORPHOLOGY_PATH = PACKAGE_ROOT / "morphology.json"


def _canonical_spec() -> HandJointPositionSpec:
    morphology = json.loads(MORPHOLOGY_PATH.read_text(encoding="utf-8"))
    control = morphology["public_control"]
    observations = morphology["public_observations"]
    joint_names = tuple(control["joint_names"])
    finger_joints = {
        "index": joint_names[0:4],
        "middle": joint_names[4:8],
        "ring": joint_names[8:12],
        "thumb": joint_names[12:16],
    }
    return HandJointPositionSpec(
        palm_body_name=observations["base_body"],
        joint_names=joint_names,
        actuator_names=tuple(control["actuator_names"]),
        finger_joint_names=finger_joints,
        fingertip_geom_names=observations["fingertip_geoms"],
        home_qpos=tuple(0.0 for _ in joint_names),
        max_joint_command_delta=0.08,
        joint_tolerance_rad=0.03,
    )


def _session() -> tuple[mujoco.MjModel, mujoco.MjData, HandJointPositionSkeleton]:
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = HandJointPositionSkeleton.from_session(
        model=model, data=data, spec=_canonical_spec()
    )
    return model, data, skeleton


def test_spec_normalizes_public_morphology_and_rejects_malformed_inputs() -> None:
    spec = _canonical_spec()
    assert isinstance(spec, HandJointPositionSpec)
    assert spec.joint_names[0] == "if_mcp"
    assert spec.finger_joint_names["thumb"] == (
        "th_cmc",
        "th_axl",
        "th_mcp",
        "th_ipl",
    )
    with pytest.raises(ValueError, match="equal length"):
        HandJointPositionSpec(
            palm_body_name="palm",
            joint_names=("j0",),
            actuator_names=("a0", "a1"),
            finger_joint_names={"finger": ("j0",)},
            fingertip_geom_names={"finger": "tip"},
        )
    with pytest.raises(ValueError, match="matching keys"):
        HandJointPositionSpec(
            palm_body_name="palm",
            joint_names=("j0",),
            actuator_names=("a0",),
            finger_joint_names={"finger": ("j0",)},
            fingertip_geom_names={"other": "tip"},
        )
    with pytest.raises(ValueError, match="partition"):
        HandJointPositionSpec(
            palm_body_name="palm",
            joint_names=("j0", "j1"),
            actuator_names=("a0", "a1"),
            finger_joint_names={"finger": ("j0",)},
            fingertip_geom_names={"finger": "tip"},
        )
    with pytest.raises(ValueError, match="positive"):
        HandJointPositionSpec(
            palm_body_name="palm",
            joint_names=("j0",),
            actuator_names=("a0",),
            finger_joint_names={"finger": ("j0",)},
            fingertip_geom_names={"finger": "tip"},
            max_joint_command_delta=0.0,
        )
    with pytest.raises(ValueError, match="finite"):
        HandJointPositionSpec(
            palm_body_name="palm",
            joint_names=("j0",),
            actuator_names=("a0",),
            finger_joint_names={"finger": ("j0",)},
            fingertip_geom_names={"finger": "tip"},
            home_qpos=(float("nan"),),
        )


def test_resolves_exact_model_mapping_and_public_primitive_surface() -> None:
    model, data, skeleton = _session()
    assert isinstance(skeleton, SessionBoundSkeleton)
    assert skeleton.model is model
    assert skeleton.data is data
    assert not skeleton._resolved

    positions = skeleton.get_joint_positions()
    assert positions.shape == (16,)
    assert skeleton._resolved
    for joint_name, actuator_name in zip(
        skeleton.spec.joint_names, skeleton.spec.actuator_names, strict=True
    ):
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
        assert int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_HINGE)
        assert int(model.actuator_trntype[actuator_id]) == int(mujoco.mjtTrn.mjTRN_JOINT)
        assert int(model.actuator_trnid[actuator_id, 0]) == joint_id
        assert np.all(skeleton._command_low <= skeleton._command_high)

    assert {
        item.name for item in discover_primitives(HandJointPositionSkeleton)
    } == {
        "get_fingertip_positions",
        "get_joint_positions",
        "hold",
        "home",
        "move_joints",
        "move_named_joints",
    }


def test_fingertip_positions_transform_between_world_and_palm_frames() -> None:
    _model, _data, skeleton = _session()
    world = skeleton.get_fingertip_positions(frame="world")
    palm = skeleton.get_fingertip_positions()
    palm_id = mujoco.mj_name2id(
        skeleton.model, mujoco.mjtObj.mjOBJ_BODY, skeleton.spec.palm_body_name
    )
    rotation = np.asarray(skeleton.data.xmat[palm_id], dtype=float).reshape(3, 3)
    origin = np.asarray(skeleton.data.xpos[palm_id], dtype=float)
    for finger in world:
        np.testing.assert_allclose(
            palm[finger], rotation.T @ (world[finger] - origin), rtol=0.0, atol=1e-12
        )
    with pytest.raises(ValueError, match="frame"):
        skeleton.get_fingertip_positions(frame="camera")


def test_move_is_bounded_ctrl_only_and_named_motion_holds_unspecified_joints() -> None:
    model, data, skeleton = _session()
    initial_qpos = data.qpos.copy()
    initial_qvel = data.qvel.copy()
    skeleton.get_joint_positions()
    actuator_ids = np.asarray(skeleton._actuator_ids, dtype=int)
    target = skeleton.get_joint_positions()
    target[2] = 0.65
    target[3] = 0.75
    initial_ctrl = data.ctrl.copy()
    observed_before_step: list[np.ndarray] = []
    commands: list[np.ndarray] = []
    original_step = mujoco.mj_step

    def traced_step(step_model: mujoco.MjModel, step_data: mujoco.MjData) -> None:
        observed_before_step.append(
            np.asarray(step_data.qpos[list(skeleton._joint_qpos_adr)], dtype=float).copy()
        )
        commands.append(np.asarray(step_data.ctrl[actuator_ids], dtype=float).copy())
        original_step(step_model, step_data)

    mujoco.mj_step = traced_step
    try:
        assert skeleton.move_named_joints(
            {"if_pip": float(target[2]), "if_dip": float(target[3])},
            duration_s=0.5,
            tolerance_rad=0.06,
        )
    finally:
        mujoco.mj_step = original_step

    assert commands
    for observed, command in zip(observed_before_step, commands, strict=True):
        np.testing.assert_array_less(
            np.abs(command - observed),
            skeleton.spec.max_joint_command_delta + 1e-12,
        )
    assert np.all(np.isfinite(data.qvel))
    np.testing.assert_array_equal(initial_ctrl, np.zeros(model.nu))
    assert np.all(np.abs(data.ctrl[actuator_ids]) <= skeleton._command_high + 1e-12)
    current = skeleton.get_joint_positions()
    assert abs(float(current[2] - target[2])) <= 0.06
    assert abs(float(current[3] - target[3])) <= 0.06
    np.testing.assert_allclose(current[[0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]], initial_qpos[[0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]], atol=0.06)
    assert not np.array_equal(data.qpos, initial_qpos)
    assert not np.array_equal(data.qvel, initial_qvel)
    assert np.isfinite(data.ctrl).all()
    assert np.all(data.ctrl[actuator_ids] >= model.actuator_ctrlrange[actuator_ids, 0])
    assert np.all(data.ctrl[actuator_ids] <= model.actuator_ctrlrange[actuator_ids, 1])


def test_home_and_hold_advance_the_same_session_without_state_writes() -> None:
    model, data, skeleton = _session()
    target = skeleton.get_joint_positions()
    target[0] = 0.5
    target[4] = 0.45
    assert skeleton.move_joints(target, duration_s=0.5, tolerance_rad=0.06)
    before_hold_qpos = data.qpos.copy()
    before_time = float(data.time)
    assert skeleton.hold(steps=4)
    assert float(data.time) == pytest.approx(before_time + 4 * model.opt.timestep)
    assert np.all(np.isfinite(data.qpos))
    assert np.all(np.isfinite(data.qvel))
    assert np.max(np.abs(data.qpos - before_hold_qpos)) < 0.04
    assert skeleton.home(duration_s=0.5)
    np.testing.assert_allclose(skeleton.get_joint_positions(), 0.0, atol=0.06)


def test_skeleton_source_has_no_direct_qpos_or_qvel_writes() -> None:
    source = inspect.getsource(HandJointPositionSkeleton)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"qpos", "qvel"}
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(tree)
    )
