"""Focused physical-call checks for the four additional arm references.

Passing these checks means the copied MJCF builds and every task effect routes
through a 1.0 skeleton action.  It is not a claim that private task criteria
pass; that verdict belongs to the trusted task validator.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO2 = REPO_ROOT / "demo2"
REFERENCE_ROOT = DEMO2 / "reference_drivers"
sys.path.insert(0, str(DEMO2 / "legacy_core"))
sys.path.insert(0, str(REPO_ROOT))


def _load_driver(robot_id: str):
    path = REFERENCE_ROOT / robot_id / "driver.py"
    spec = importlib.util.spec_from_file_location(
        f"demo2_additional_reference_{robot_id}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parameters(method) -> tuple[str, ...]:
    return tuple(inspect.signature(method).parameters)


def _assert_physics_call(robot, call) -> object:
    time_before = float(robot.data.time)
    result = call()
    assert float(robot.data.time) > time_before
    assert np.isfinite(robot.data.qpos).all()
    assert np.isfinite(robot.data.qvel).all()
    return result


def test_kuka_and_ur5e_build_and_route_all_public_task_effects() -> None:
    cases = {
        "kuka_iiwa_14": {
            "scene": DEMO2 / "legacy_assets" / "kuka_iiwa_14" / "scene.xml",
            "far": [2.0, 2.0, 2.0],
        },
        "universal_robots_ur5e": {
            "scene": DEMO2
            / "legacy_assets"
            / "universal_robots_ur5e"
            / "scene.xml",
            "far": [3.0, 3.0, 3.0],
        },
    }
    expected = {
        "reach_target": ("target_position", "duration"),
        "trace_cartesian_path": ("waypoints", "duration_per_segment"),
        "move_cartesian_offset_and_return": ("offset", "duration"),
        "visit_cartesian_waypoints": ("waypoints", "duration_per_segment"),
        "reject_unreachable_and_return_home": ("target_position", "duration"),
    }

    for robot_id, case in cases.items():
        driver = _load_driver(robot_id)
        robot = driver.build(mjcf_path=case["scene"])
        try:
            assert {
                name: _parameters(getattr(robot, name)) for name in expected
            } == expected
            start, _ = robot.get_ee_pose()
            timestep = float(robot.model.opt.timestep)
            _assert_physics_call(
                robot,
                lambda: robot.reach_target(start, duration=timestep),
            )
            _assert_physics_call(
                robot,
                lambda: robot.trace_cartesian_path(
                    [start], duration_per_segment=timestep
                ),
            )
            _assert_physics_call(
                robot,
                lambda: robot.move_cartesian_offset_and_return(
                    [0.0, 0.0, 0.0], duration=2.0 * timestep
                ),
            )
            _assert_physics_call(
                robot,
                lambda: robot.visit_cartesian_waypoints(
                    [start], duration_per_segment=timestep
                ),
            )
            rejection = _assert_physics_call(
                robot,
                lambda: robot.reject_unreachable_and_return_home(
                    case["far"], duration=timestep
                ),
            )
            assert rejection == {
                "outcome": "rejected",
                "reason": "unreachable_target",
            }
            assert "attempt_count" not in rejection
        finally:
            robot.close()


def test_piper_builds_both_scenes_and_routes_all_public_task_effects() -> None:
    driver = _load_driver("piper")
    pick_scene = DEMO2 / "legacy_assets" / "piper" / "pickbench.xml"
    push_scene = DEMO2 / "legacy_assets" / "piper" / "pushbench.xml"
    expected = {
        "reach_above_object": ("target_position", "duration"),
        "grasp_and_lift": ("object_position", "lift", "duration"),
        "push_object_to_goal": (
            "object_position",
            "goal_position",
            "duration",
        ),
    }

    robot = driver.build(mjcf_path=pick_scene)
    try:
        assert {name: _parameters(getattr(robot, name)) for name in expected} == expected
        timestep = float(robot.model.opt.timestep)
        start, _ = robot.get_ee_pose()
        _assert_physics_call(
            robot,
            lambda: robot.reach_above_object(start, duration=timestep),
        )
        _assert_physics_call(
            robot,
            lambda: robot.grasp_and_lift(start, lift=0.001, duration=3.0 * timestep),
        )
    finally:
        robot.close()

    robot = driver.build(mjcf_path=push_scene)
    try:
        timestep = float(robot.model.opt.timestep)
        start, _ = robot.get_ee_pose()
        _assert_physics_call(
            robot,
            lambda: robot.push_object_to_goal(
                start, start, duration=3.0 * timestep
            ),
        )
    finally:
        robot.close()


def test_pushbench_builds_and_routes_both_public_task_effects() -> None:
    driver = _load_driver("pushbench")
    scene = (
        DEMO2
        / "legacy_assets"
        / "mjcf"
        / "pushbench"
        / "so101_pushbench.xml"
    )
    robot = driver.build(mjcf_path=scene)
    try:
        expected = {
            "push_object_to_goal": (
                "object_position",
                "goal_position",
                "duration",
            ),
            "move_to_waypoint_and_return": ("waypoint", "duration"),
        }
        assert {name: _parameters(getattr(robot, name)) for name in expected} == expected
        timestep = float(robot.model.opt.timestep)
        start, _ = robot.get_ee_pose()
        _assert_physics_call(
            robot,
            lambda: robot.push_object_to_goal(
                start, start, duration=3.0 * timestep
            ),
        )
        _assert_physics_call(
            robot,
            lambda: robot.move_to_waypoint_and_return(
                start, duration=2.0 * timestep
            ),
        )
    finally:
        robot.close()


def test_reference_task_sources_do_not_assign_mujoco_qpos() -> None:
    """Task wrappers may actuate and step, but cannot teleport MuJoCo state."""
    paths = [
        REFERENCE_ROOT / "arm_task_common.py",
        *(REFERENCE_ROOT / name / "driver.py" for name in (
            "kuka_iiwa_14",
            "piper",
            "universal_robots_ur5e",
            "pushbench",
        )),
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            rendered = [ast.unparse(target) for target in targets]
            assert not any(".qpos" in target for target in rendered), (
                path,
                rendered,
            )
