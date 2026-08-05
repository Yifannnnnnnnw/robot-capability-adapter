from __future__ import annotations

import json
from pathlib import Path

import pytest

from soarm_demo.bridge.lerobot_mujoco import SO101MujocoRobot
from soarm_demo.direct_validation import execute_case
from soarm_demo.oracle import MujocoJointValidationEnvironment


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
KEYS = {
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
}


def test_mujoco_bridge_six_key_receipt_and_progress() -> None:
    pytest.importorskip("mujoco")
    robot = SO101MujocoRobot(MODEL, auto_step=False)
    # The pinned MJCF timestep is 0.005 s, so its real-time clock is 200 Hz.
    assert robot.physics_hz == pytest.approx(200.0)
    assert robot.simulation_hz == pytest.approx(200.0)
    robot.connect(calibrate=False)
    try:
        assert set(robot.observation_features) == KEYS
        assert set(robot.action_features) == KEYS
        accepted = robot.send_action({"shoulder_pan.pos": 5.0, "gripper.pos": 50.0})
        assert accepted == {"shoulder_pan.pos": 5.0, "gripper.pos": 50.0}
        assert robot.last_receipt is not None
        # The receipt is target acceptance, not a claim of instant arrival.
        assert robot.last_receipt.simulation_time == 0.0
        robot.advance(0.25)
        observation = robot.get_observation()
        assert observation["shoulder_pan.pos"] == pytest.approx(5.0, abs=0.1)
    finally:
        robot.disconnect()


def test_mujoco_bridge_rejects_clock_mismatch_and_unknown_position_key() -> None:
    pytest.importorskip("mujoco")
    with pytest.raises(ValueError, match="must match the pinned MJCF timestep"):
        SO101MujocoRobot(MODEL, auto_step=False, simulation_hz=250.0)

    robot = SO101MujocoRobot(MODEL, auto_step=False)
    robot.connect(calibrate=False)
    try:
        with pytest.raises(KeyError, match="unknown SO-101 action fields"):
            robot.send_action({"imaginary_motor.pos": 1.0})
    finally:
        robot.disconnect()


def test_mapping_relative_limit_requires_exact_action_key_set() -> None:
    pytest.importorskip("mujoco")
    robot = SO101MujocoRobot(
        MODEL,
        auto_step=False,
        max_relative_target={"shoulder_pan": 5.0, "gripper": 5.0},
    )
    robot.connect(calibrate=False)
    try:
        with pytest.raises(ValueError, match="exactly match"):
            robot.send_action({"shoulder_pan.pos": 10.0})
    finally:
        robot.disconnect()


def test_generated_g1_is_directly_invoked_on_mujoco_bridge() -> None:
    pytest.importorskip("mujoco")
    manifest = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(encoding="utf-8")
    )
    case = {
        "case_id": "mujoco_g1_move_joints",
        "capability_id": "G1.move_joints",
        "module": "g1",
        "function_name": "move_joints",
        "initial_state": {"qpos_rad": {}},
        "call_arguments": {
            "targets": {"shoulder_pan.pos": 5.0},
            "tolerance": 0.5,
            "max_steps": 250,
        },
        "target_measurements": {"joint_positions": {"shoulder_pan.pos": 5.0}},
        "tolerances": {"joint_positions": {"shoulder_pan.pos": 0.5}},
        "forbidden_conditions": ["action_clipped"],
        "timeout_s": 6.0,
        "test_entrypoint": "soarm_demo.direct_validation:execute_case",
        "reference_provenance": ["morphology/soarm101/v1/model/so101.xml"],
    }
    result = execute_case(
        package_root=ROOT / "fixtures/generated_pass",
        package_manifest=manifest,
        case=case,
        environment_factory=lambda _: MujocoJointValidationEnvironment(MODEL),
    )
    assert result.passed, result.to_dict()
