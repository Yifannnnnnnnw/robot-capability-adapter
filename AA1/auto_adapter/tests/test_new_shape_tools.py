# SPDX-License-Identifier: Apache-2.0
"""Focused tool-dispatch checks for the AA1 new robot shapes."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from auto_adapter.agent.task_planner import (
    TaskPlanner,
    _STRETCH_TOOL_SPECS,
    tool_registry_for,
)


class _Capture:
    def snapshot(self) -> None:
        pass


class BimanualSerialDLSSkeleton:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_ee_pose(self, *, arm: str):
        self.calls.append(("get_ee_pose", arm))
        return np.zeros(3), np.eye(3)

    def move_cartesian(self, target_xyz, duration=2.0, *, arm: str):
        self.calls.append(("move_cartesian", arm, np.asarray(target_xyz), duration))
        return True

    def gripper_open(self, *, arm: str):
        self.calls.append(("gripper_open", arm))
        return True

    def gripper_close(self, *, arm: str):
        self.calls.append(("gripper_close", arm))
        return True

    def get_joint_positions(self, *, arm: str):
        self.calls.append(("get_joint_positions", arm))
        return np.zeros(2)

    def move_joints(self, q_target, duration=2.0, *, arm: str):
        self.calls.append(("move_joints", arm, np.asarray(q_target), duration))
        return True

    def home(self, duration=2.0):
        self.calls.append(("home", duration))
        return True

    def hold(self, duration=0.5):
        self.calls.append(("hold", duration))
        return True


class StretchMobileManipulationSkeleton:
    def get_base_pose(self):
        return np.zeros(3), np.eye(3)

    def get_base_yaw(self):
        return 0.0

    def drive_forward(self, distance_m, speed=0.1):
        return distance_m, speed

    def turn(self, angle_rad, speed=0.3):
        return angle_rad, speed

    def get_ee_pose(self):
        return np.zeros(3), np.eye(3)

    def move_cartesian(self, target_xyz, duration=2.0):
        return target_xyz, duration

    def gripper_open(self):
        return True

    def gripper_close(self):
        return True

    def home(self, duration=1.0):
        return duration


class ArmSerialDLSSkeleton:
    def get_ee_pose(self):
        return np.zeros(3), np.eye(3)

    def move_cartesian(self, target_xyz, duration=2.0):
        return True


def _tool(planner: TaskPlanner, skel, registry: dict, name: str, log: list[dict]):
    return planner._make_registered_tool(skel, name, registry[name], _Capture(), log)


def test_bimanual_arm_labels_are_required_and_forwarded() -> None:
    skel = BimanualSerialDLSSkeleton()
    registry = tool_registry_for(skel, expected_robot_class="bimanual")
    planner = TaskPlanner.__new__(TaskPlanner)
    log: list[dict] = []

    arm_tools = (
        ("get_ee_pose", {"arm": "left"}),
        ("move_cartesian", {"arm": "right", "x": 0.1, "y": 0.2, "z": 0.3}),
        ("gripper_open", {"arm": "left"}),
        ("gripper_close", {"arm": "right"}),
        ("get_joint_positions", {"arm": "left"}),
        ("move_joints", {"arm": "right", "q_target": [0.1, 0.2]}),
    )
    for name, inp in arm_tools:
        spec = registry[name]["schema"]
        assert spec["properties"]["arm"]["enum"] == ["left", "right"]
        assert "arm" in spec["required"]
        _tool(planner, skel, registry, name, log).handler(inp)

    assert [call[1] for call in skel.calls] == ["left", "right", "left", "right", "left", "right"]
    with pytest.raises(ValueError, match="explicitly"):
        _tool(planner, skel, registry, "get_ee_pose", log).handler({})


def test_stretch_registry_combines_base_arm_and_gripper_defaults() -> None:
    registry = tool_registry_for(
        StretchMobileManipulationSkeleton(),
        expected_robot_class="mobile_manipulator",
    )
    assert registry is _STRETCH_TOOL_SPECS
    assert {
        "drive_forward", "turn", "get_base_pose", "get_base_yaw",
        "get_ee_pose", "move_cartesian", "gripper_open", "gripper_close", "home",
    } <= set(registry)
    assert registry["drive_forward"]["kwargs"]({})["speed"] == 0.1
    assert registry["turn"]["kwargs"]({})["speed"] == 0.3


def test_trusted_bimanual_shape_rejects_single_arm() -> None:
    with pytest.raises(ValueError, match="expected BimanualSerialDLSSkeleton"):
        tool_registry_for(ArmSerialDLSSkeleton(), expected_robot_class="bimanual")


def test_task_planner_reads_expected_class_from_trusted_mjcf(tmp_path: Path) -> None:
    (tmp_path / "driver.py").write_text("def build(): return None\n")
    source = Path(__file__).resolve().parents[2] / "assets/mjcf/aloha_2/scene.xml"
    (tmp_path / "mjcf.xml").symlink_to(source)
    planner = TaskPlanner(workspace=tmp_path)
    assert planner.expected_robot_class == "bimanual"
