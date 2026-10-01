"""Real MuJoCo smoke checks for the newly vendored robot-zoo assets."""

from pathlib import Path

import mujoco
import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

ADDED_ASSETS = (
    ("kinova_gen3_robotiq_2f85", "assets/mjcf/kinova_gen3_robotiq_2f85/scene.xml"),
    ("ufactory_xarm7", "assets/mjcf/ufactory_xarm7/scene.xml"),
    (
        "universal_robots_ur5e_robotiq_2f85",
        "assets/mjcf/universal_robots_ur5e_robotiq_2f85/scene.xml",
    ),
    ("leap_hand", "assets/mjcf/leap_hand/scene_right.xml"),
    ("hello_robot_stretch_2", "assets/mjcf/hello_robot_stretch_2/scene.xml"),
    ("aloha_2", "assets/mjcf/aloha_2/scene.xml"),
)


@pytest.mark.parametrize(("robot_id", "relative_path"), ADDED_ASSETS)
def test_added_zoo_asset_loads_and_steps(robot_id: str, relative_path: str) -> None:
    """Load each real closure, run forward dynamics, and reject non-finite state."""
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"{robot_id}: missing entrypoint {path}"

    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    for _ in range(8):
        mujoco.mj_step(model, data)

    assert np.isfinite(data.qpos).all(), f"{robot_id}: non-finite qpos"
    assert np.isfinite(data.qvel).all(), f"{robot_id}: non-finite qvel"
    assert np.isfinite(data.ctrl).all(), f"{robot_id}: non-finite ctrl"
    assert np.isfinite(data.time), f"{robot_id}: non-finite simulation time"
