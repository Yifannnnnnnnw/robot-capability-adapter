# SPDX-License-Identifier: Apache-2.0
"""Focused real-physics check for the bounded Stretch 2 skeleton."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from auto_adapter.skeletons.stretch_mobile_manipulation import (
    StretchMobileManipulationSkeleton,
    StretchMobileManipulationSpec,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENE_PATH = REPO_ROOT / "assets" / "mjcf" / "hello_robot_stretch_2" / "scene.xml"
BASE_TARGET_XY = np.asarray((-0.09, 0.0))
EE_TARGET_XYZ = np.asarray((-0.02, -0.24, 0.67))


def test_stretch_base_then_fixed_world_reach() -> None:
    """Drive into a target region, then reach a fixed world point with the arm."""

    assert SCENE_PATH.is_file(), f"Stretch MJCF missing at {SCENE_PATH}"
    skeleton = StretchMobileManipulationSkeleton.from_mjcf(
        str(SCENE_PATH), StretchMobileManipulationSpec()
    )

    skeleton.home(duration=1.0)
    base_before, base_rotation = skeleton.get_base_pose()
    _, ee_rotation = skeleton.get_ee_pose()
    assert base_rotation.shape == (3, 3)
    assert ee_rotation.shape == (3, 3)

    # These are fixed world coordinates calibrated against the canonical
    # scene: the base reaches the target region, then the arm reaches the
    # target point without translating the base.
    target = EE_TARGET_XYZ.copy()
    drive_result = skeleton.drive_forward(0.10, speed=0.10)
    base_after_drive, _ = skeleton.get_base_pose()
    horizontal_displacement = float(
        np.linalg.norm((base_after_drive - base_before)[:2])
    )
    assert drive_result["travelled_distance_m"] > 0.05
    assert horizontal_displacement > 0.05
    assert np.linalg.norm(base_after_drive[:2] - BASE_TARGET_XY) < 0.04

    reach_result = skeleton.move_cartesian(target, duration=2.0)
    base_after_reach, _ = skeleton.get_base_pose()
    ee_after, _ = skeleton.get_ee_pose()
    reach_error = float(np.linalg.norm(ee_after - target))

    # Arm motion may turn the base to expose the side arm, but it must not
    # translate the base while lift/extension motion is running.
    base_translation_during_reach = float(
        np.linalg.norm((base_after_reach - base_after_drive)[:2])
    )
    assert base_translation_during_reach < 0.01
    assert reach_result["final_error_m"] < 0.03
    assert reach_error < 0.03
    assert np.isfinite(skeleton.data.qpos).all()
    assert np.isfinite(skeleton.data.qvel).all()
    assert float(skeleton.get_base_pose()[1][2, 2]) > 0.95
