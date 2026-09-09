# SPDX-License-Identifier: Apache-2.0
"""Focused real-physics check for the AA1 LEAP fingertip skeleton."""
from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
import pytest

from auto_adapter.skeletons.hand_fingertip_dls import (
    FINGER_ORDER,
    HandFingertipDLSSpec,
    HandFingertipDLSSkeleton,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = AA1_ROOT / "assets" / "mjcf" / "leap_hand" / "scene_right.xml"

JOINT_NAMES = (
    "if_mcp",
    "if_rot",
    "if_pip",
    "if_dip",
    "mf_mcp",
    "mf_rot",
    "mf_pip",
    "mf_dip",
    "rf_mcp",
    "rf_rot",
    "rf_pip",
    "rf_dip",
    "th_cmc",
    "th_axl",
    "th_mcp",
    "th_ipl",
)
ACTUATOR_NAMES = tuple(f"{name}_act" for name in JOINT_NAMES)
FINGERTIP_GEOMS = {
    "index": "if_tip",
    "middle": "mf_tip",
    "ring": "rf_tip",
    "thumb": "th_tip",
}
FINGER_JOINTS = {
    "index": JOINT_NAMES[0:4],
    "middle": JOINT_NAMES[4:8],
    "ring": JOINT_NAMES[8:12],
    "thumb": JOINT_NAMES[12:16],
}

# A fixed, modestly bent posture inside the published LEAP joint ranges.  The
# target fingertips are generated from a separate MjData object below, so the
# controlled skeleton never uses qpos assignment as a shortcut.
TARGET_Q = np.asarray(
    (
        0.35,
        0.12,
        0.45,
        0.55,
        0.30,
        -0.10,
        0.40,
        0.50,
        0.25,
        -0.10,
        0.35,
        0.45,
        0.35,
        0.45,
        0.30,
        0.45,
    ),
    dtype=np.float64,
)


def _spec() -> HandFingertipDLSSpec:
    return HandFingertipDLSSpec(
        palm_body_name="palm",
        joint_names=JOINT_NAMES,
        actuator_names=ACTUATOR_NAMES,
        fingertip_geom_names=FINGERTIP_GEOMS,
        finger_joint_names=FINGER_JOINTS,
        home_qpos=(0.0,) * 16,
        max_joint_command_delta=0.1,
        joint_tolerance_rad=0.03,
        fingertip_tolerance_m=0.003,
        ik_damping=0.02,
        ik_step_clamp=0.08,
        ik_position_gain=1.0,
    )


def _target_positions(model: mujoco.MjModel) -> dict[str, np.ndarray]:
    """Compute world-frame goals with a separate FK data object."""

    fk_data = mujoco.MjData(model)
    fk_data.qpos[:] = model.qpos0
    for joint_name, value in zip(JOINT_NAMES, TARGET_Q, strict=True):
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        fk_data.qpos[model.jnt_qposadr[joint_id]] = value
    mujoco.mj_forward(model, fk_data)
    return {
        finger: np.asarray(
            fk_data.geom_xpos[
                mujoco.mj_name2id(
                    model, mujoco.mjtObj.mjOBJ_GEOM, FINGERTIP_GEOMS[finger]
                )
            ],
            dtype=np.float64,
        ).copy()
        for finger in FINGER_ORDER
    }


def test_leap_fingertip_dls_runs_real_physics_from_separate_fk_target() -> None:
    assert SCENE_PATH.is_file(), f"LEAP scene missing: {SCENE_PATH}"
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = HandFingertipDLSSkeleton(model=model, data=data, spec=_spec())

    skeleton.home()
    initial_q = skeleton.get_joint_positions()
    initial_tips = skeleton.get_fingertip_positions()
    targets = _target_positions(model)
    assert all(np.isfinite(point).all() for point in targets.values())

    assert skeleton.move_fingertips(targets, duration=2.0)
    final_q = skeleton.get_joint_positions()
    final_tips = skeleton.get_fingertip_positions()
    error = np.concatenate(
        [final_tips[finger] - targets[finger] for finger in FINGER_ORDER]
    )
    aggregate_error = float(np.linalg.norm(error))
    print(
        f"scene={SCENE_PATH} moved_q={np.linalg.norm(final_q - initial_q):.4f} "
        f"moved_tip={np.linalg.norm(final_tips['index'] - initial_tips['index']):.4f} "
        f"aggregate_error={aggregate_error:.6f}"
    )
    assert np.linalg.norm(final_q - initial_q) > 1e-3
    assert all(np.linalg.norm(final_tips[finger] - initial_tips[finger]) > 0.01
               for finger in FINGER_ORDER)
    assert aggregate_error < 0.00894427191

    assert skeleton.home(duration=1.0)
    np.testing.assert_allclose(skeleton.get_joint_positions(), 0.0, atol=0.03)

    description = skeleton.describe()
    assert description["dof"] == 16
    assert tuple(description["fingers"]) == FINGER_ORDER
    assert description["palm_body"] == "palm"


def test_leap_fingertip_dls_requires_all_canonical_targets() -> None:
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    skeleton = HandFingertipDLSSkeleton(model=model, data=data, spec=_spec())
    with pytest.raises(ValueError, match="exactly"):
        skeleton.move_fingertips(
            {finger: np.zeros(3) for finger in FINGER_ORDER[:-1]}, duration=0.01
        )


def test_unreachable_fingertips_fail_after_bounded_physics() -> None:
    skeleton = HandFingertipDLSSkeleton.from_mjcf(str(SCENE_PATH), _spec())
    t0 = skeleton.data.time
    assert not skeleton.move_fingertips({f: [3, 3, 3] for f in FINGER_ORDER}, duration=0.02)
    assert skeleton.data.time - t0 == pytest.approx(0.02)
