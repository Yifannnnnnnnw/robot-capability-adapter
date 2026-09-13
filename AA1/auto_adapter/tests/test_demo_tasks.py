"""Check diagnostic demo inputs against their real MuJoCo scenes."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np
import pytest
import yaml

from auto_adapter.scene_runtime import apply_initial_state


AA1_ROOT = Path(__file__).resolve().parents[2]
DEMOS = yaml.safe_load(
    (AA1_ROOT / "auto_adapter/demo_tasks.yaml").read_text(encoding="utf-8")
)["robots"]
ROBOT_IDS = {
    "so101", "menagerie_so101", "so101_push", "piper_push", "franka_push",
    "piper", "franka", "ur5e", "kuka_iiwa14", "go2", "unitree_a1", "anymal_c",
    "kinova_gen3_robotiq_2f85", "ufactory_xarm7",
    "universal_robots_ur5e_robotiq_2f85", "leap_hand", "hello_robot_stretch_2",
    "aloha_2", "skydio_x2", "h1", "unitree_g1", "google_barkour_vb",
}
EXPECTED_SCENE_PATHS = {
    "so101": "assets/mjcf/demo_scenes/so101/fixed_scene.xml",
    "menagerie_so101": "assets/mjcf/robotstudio_so101/scene.xml",
    "so101_push": "assets/mjcf/pushbench/so101_pushbench.xml",
    "piper_push": "assets/mjcf/piper/pushbench.xml",
    "franka_push": "assets/mjcf/franka_panda/pushbench.xml",
    "piper": "assets/mjcf/demo_scenes/piper/fixed_scene.xml",
    "franka": "assets/mjcf/demo_scenes/franka/fixed_scene.xml",
    "ur5e": "assets/mjcf/universal_robots_ur5e/scene.xml",
    "kuka_iiwa14": "assets/mjcf/demo_scenes/kuka_iiwa14/fixed_scene.xml",
    "go2": "assets/mjcf/go2/go2_scene.xml",
    "unitree_a1": "assets/mjcf/demo_scenes/unitree_a1/fixed_scene.xml",
    "anymal_c": "assets/mjcf/demo_scenes/anymal_c/fixed_scene.xml",
    "kinova_gen3_robotiq_2f85": "assets/mjcf/demo_scenes/kinova_gen3_robotiq_2f85/fixed_scene.xml",
    "ufactory_xarm7": "assets/mjcf/demo_scenes/ufactory_xarm7/fixed_scene.xml",
    "universal_robots_ur5e_robotiq_2f85": "assets/mjcf/demo_scenes/universal_robots_ur5e_robotiq_2f85/fixed_scene.xml",
    "leap_hand": "assets/mjcf/leap_hand/scene_right.xml",
    "hello_robot_stretch_2": "assets/mjcf/hello_robot_stretch_2/scene.xml",
    "aloha_2": "assets/mjcf/aloha_2/scene.xml",
    "skydio_x2": "assets/mjcf/skydio_x2/scene.xml",
    "h1": "assets/mjcf/h1/scene.xml",
    "unitree_g1": "assets/mjcf/unitree_g1/scene.xml",
    "google_barkour_vb": "assets/mjcf/google_barkour_vb/scene.xml",
}
RETIRED_METHOD_NAMES = {
    "trace_cartesian_path", "set_gripper_opening", "move_cartesian_offset_and_return",
    "trace_planar_path", "set_body_height", "hold_stable_stance",
}


def test_demo_inputs_cover_the_twenty_two_robots() -> None:
    assert set(DEMOS) == ROBOT_IDS
    for robot_id, demo in DEMOS.items():
        assert set(demo) == {"task", "scene", "initial_state", "parameters"}
        assert demo["scene"] == EXPECTED_SCENE_PATHS[robot_id]
        assert demo["task"].strip()
        assert isinstance(demo["parameters"], dict)
        # These parameters are sent to the model as public JSON.
        json.dumps(demo["parameters"], allow_nan=False)


def test_demo_inputs_have_no_retired_interfaces_or_paths() -> None:
    serialized = json.dumps(DEMOS)
    assert "required_capabilities" not in serialized
    assert "assets/mjcf/capabilities/" not in serialized
    for method_name in RETIRED_METHOD_NAMES:
        assert method_name not in serialized

def test_arm_demos_do_not_request_an_extra_hold_but_aloha_keeps_its_hold() -> None:
    for robot_id in (
        "so101", "piper", "franka", "kuka_iiwa14", "kinova_gen3_robotiq_2f85",
        "ufactory_xarm7", "universal_robots_ur5e_robotiq_2f85",
    ):
        demo = DEMOS[robot_id]
        assert "terminal_hold_s" not in demo["parameters"], robot_id
        assert "hold" not in demo["task"].lower(), robot_id
    assert DEMOS["aloha_2"]["parameters"]["hold_duration_s"] == 0.5
    assert "hold both target positions together for 0.5 seconds" in DEMOS["aloha_2"]["task"]


@pytest.mark.parametrize("robot_id", sorted(ROBOT_IDS))
def test_demo_initial_state_loads_in_the_selected_scene(robot_id: str) -> None:
    demo = DEMOS[robot_id]
    scene = AA1_ROOT / demo["scene"]
    assert scene.is_file()
    model = mujoco.MjModel.from_xml_path(str(scene))
    data = mujoco.MjData(model)
    apply_initial_state(model, data, {"initial_state": demo["initial_state"]})

    assert np.isfinite(data.qpos).all()
    assert np.isfinite(data.qvel).all()
    assert np.isfinite(data.ctrl).all()
    for name, state in demo["initial_state"].get("free_bodies", {}).items():
        body = model.body(name)
        joint_id = int(model.body_jntadr[body.id])
        address = int(model.jnt_qposadr[joint_id])
        np.testing.assert_allclose(data.qpos[address:address + 3], state["position_m"])
        quaternion = np.asarray(state["quaternion_wxyz"])
        np.testing.assert_allclose(
            data.qpos[address + 3:address + 7], quaternion / np.linalg.norm(quaternion)
        )


@pytest.mark.parametrize(
    "robot_id,crouch_height,standing_height",
    [("go2", 0.26, 0.32), ("unitree_a1", 0.23, 0.26), ("anymal_c", 0.34, 0.374)],
)
def test_quadruped_demo_keeps_the_requested_motion_order_and_heights(
    robot_id: str, crouch_height: float, standing_height: float,
) -> None:
    demo = DEMOS[robot_id]
    parameters = demo["parameters"]
    assert parameters["translation_initial_yaw_m"] == [0.1, 0.0]
    assert parameters["yaw_delta_rad"] == 0.0
    assert parameters["crouch_height_m"] == crouch_height
    assert parameters["standing_height_m"] == standing_height
    for phase in ("initial_stand_s", "post_walk_stand_s", "final_stand_s"):
        assert parameters[phase] == 1.2
    task = demo["task"]
    positions = [
        task.index("Hold a stable stance for 1.2 seconds"),
        task.index("Move forward 10 centimetres"),
        task.index("Hold a stable stance for another 1.2 seconds"),
        task.index("Crouch to a body height"),
        task.index("then rise to"),
        task.index("and hold a stable stance for 1.2 seconds"),
    ]
    assert positions == sorted(positions)
