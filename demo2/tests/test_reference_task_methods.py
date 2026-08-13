"""Focused checks for Demo2's 1.0-style official-task reference drivers.

These checks establish only construction, public contracts, and a minimal
physical call through the skeleton. They intentionally do not claim that any
task-level Blue Line criterion passes.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO2 = REPO_ROOT / "demo2"
sys.path.insert(0, str(DEMO2 / "legacy_core"))


def _load_driver(robot_id: str):
    path = DEMO2 / "reference_drivers" / robot_id / "driver.py"
    spec = importlib.util.spec_from_file_location(f"demo2_reference_{robot_id}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parameter_names(method) -> tuple[str, ...]:
    return tuple(inspect.signature(method).parameters)


def test_so101_reference_exposes_and_executes_official_effects() -> None:
    driver = _load_driver("robotstudio_so101")
    robot = driver.build(
        mjcf_path=DEMO2 / "legacy_assets" / "robotstudio_so101" / "scene_box.xml"
    )
    try:
        expected = {
            "reach_above_object": ("target_position", "duration"),
            "trace_cartesian_path": ("waypoints", "duration_per_segment"),
            "cycle_gripper": ("duration",),
            "establish_controlled_contact": ("contact_position", "duration"),
            "move_cartesian_offset_and_return": ("offset", "duration"),
        }
        assert {name: _parameter_names(getattr(robot, name)) for name in expected} == expected

        timestep = float(robot.model.opt.timestep)
        position, _ = robot.get_ee_pose()
        robot.reach_above_object(position, duration=timestep)
        robot.trace_cartesian_path([position], duration_per_segment=timestep)
        robot.establish_controlled_contact(position, duration=timestep)
        robot.move_cartesian_offset_and_return(np.zeros(3), duration=2.0 * timestep)
        robot.cycle_gripper(duration=2.0 * timestep)
        assert np.isfinite(robot.data.qpos).all()
        assert np.isfinite(robot.data.qvel).all()
    finally:
        robot.close()


def test_franka_reference_exposes_and_executes_official_effects() -> None:
    driver = _load_driver("franka_panda")
    robot = driver.build(
        mjcf_path=DEMO2 / "legacy_assets" / "franka_panda" / "pushbench.xml"
    )
    try:
        expected = {
            "reach_above_object": ("target_position", "duration"),
            "push_object_to_goal": (
                "object_position",
                "goal_position",
                "duration",
            ),
            "trace_cartesian_path": ("waypoints", "duration_per_segment"),
        }
        assert {name: _parameter_names(getattr(robot, name)) for name in expected} == expected

        timestep = float(robot.model.opt.timestep)
        position, _ = robot.get_ee_pose()
        robot.reach_above_object(position, duration=timestep)
        robot.trace_cartesian_path([position], duration_per_segment=timestep)
        robot.push_object_to_goal(position, position, duration=2.0 * timestep)
        assert np.isfinite(robot.data.qpos).all()
        assert np.isfinite(robot.data.qvel).all()
    finally:
        robot.close()


def test_go2_reference_exposes_and_executes_official_effects() -> None:
    driver = _load_driver("unitree-go2")
    expected = {
        "stand_up": ("duration",),
        "sit": ("duration",),
        "hold_stable": ("duration",),
        "walk_forward": ("duration", "speed"),
        "set_body_height": ("target_height", "duration"),
    }
    scene = DEMO2 / "legacy_assets" / "go2" / "go2_scene.xml"

    for method_name, parameter_names in expected.items():
        robot = driver.build(mjcf_path=scene)
        try:
            assert _parameter_names(getattr(robot, method_name)) == parameter_names
            timestep = float(robot.model.opt.timestep)
            if method_name == "walk_forward":
                getattr(robot, method_name)(timestep, speed=0.0)
            elif method_name == "set_body_height":
                getattr(robot, method_name)(robot.spec.body_height_target, timestep)
            else:
                getattr(robot, method_name)(timestep)
            assert np.isfinite(robot.data.qpos).all()
            assert np.isfinite(robot.data.qvel).all()
        finally:
            robot.close()
