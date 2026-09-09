# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the shared native quadruped position primitives."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import mujoco
import numpy as np
import pytest

from autoadapter_bench.capability_eval import run_capability_case
from auto_adapter.skeletons import QuadrupedPDGaitSkeleton, QuadrupedSpec


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("robot", "scene"),
    (
        ("unitree_a1", "assets/mjcf/capabilities/unitree_a1/fixed_scene.xml"),
        ("anymal_c", "assets/mjcf/capabilities/anymal_c/fixed_scene.xml"),
    ),
)
def test_native_planar_primitives_use_one_real_session(robot: str, scene: str) -> None:
    binding = json.loads(
        (ROOT / "autoadapter_bench/spec/capabilities" / robot / "robot_bindings.json")
        .read_text()
    )
    model = mujoco.MjModel.from_xml_path(os.path.realpath(ROOT / scene))
    data = mujoco.MjData(model)
    key = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_KEY, binding["home_keyframe"]
    )
    assert key >= 0
    mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    legs = {
        leg: binding["joint_names"][3 * index : 3 * index + 3]
        for index, leg in enumerate(binding["leg_order"])
    }
    actuators = {
        leg: binding["actuator_names"][3 * index : 3 * index + 3]
        for index, leg in enumerate(binding["leg_order"])
    }
    skeleton = QuadrupedPDGaitSkeleton(
        model=model,
        data=data,
        spec=QuadrupedSpec(
            base_body_name=binding["base_body_name"],
            leg_joint_names=legs,
            leg_actuator_names=actuators,
            home_qpos=binding["nominal_home_qpos_rad"],
            actuation="joint_position",
            body_height_target=float(binding.get("body_height_target", 0.3)),
        ),
    )

    assert set(("angular", "linear")) == set(skeleton.get_base_velocity())
    before = float(data.time)
    skeleton.command_planar_velocity(0.03, 0.01, 0.02, duration=model.opt.timestep)
    skeleton.walk_forward(speed=0.03, duration=model.opt.timestep)
    skeleton.walk_lateral(speed=0.02, duration=model.opt.timestep)
    skeleton.turn_in_place(yaw_rate=0.03, duration=model.opt.timestep)

    assert float(data.time) > before
    assert np.all(np.isfinite(data.qpos))
    assert np.all(np.isfinite(data.qvel))
    for actuator_id in skeleton._actuator_ids:
        assert float(model.actuator_ctrlrange[actuator_id, 0]) - 1e-12 <= float(
            data.ctrl[actuator_id]
        ) <= float(model.actuator_ctrlrange[actuator_id, 1]) + 1e-12

    # Capability methods remain private reference-driver concerns; the public
    # skeleton exposes only the short native primitives and observations.
    for high_level_name in (
        "track_planar_twist",
        "move_body_relative_pose",
        "trace_planar_path",
        "set_body_height",
        "hold_stable_stance",
    ):
        assert not hasattr(skeleton, high_level_name)


@pytest.mark.parametrize(
    ("robot", "scene", "driver_path"),
    (
        (
            "unitree_a1",
            "assets/mjcf/capabilities/unitree_a1/fixed_scene.xml",
            "autoadapter_bench/reference_controls/unitree_a1/fixed_capability_driver.py",
        ),
        (
            "anymal_c",
            "assets/mjcf/capabilities/anymal_c/fixed_scene.xml",
            "autoadapter_bench/reference_controls/anymal_c/fixed_capability_driver.py",
        ),
    ),
)
def test_g4_nominal_rejects_constant_hold_after_stable_settle(
    robot: str,
    scene: str,
    driver_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an unchanged native hold from passing the calibrated G4 target."""

    class FixtureRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def update_scene(self, *args, **kwargs):
            pass

        def render(self):
            return np.zeros((16, 16, 3), dtype=np.uint8)

        def close(self):
            pass

    monkeypatch.setattr(mujoco, "Renderer", FixtureRenderer)
    conditions = json.loads(
        (
            ROOT
            / "autoadapter_bench/reference_controls"
            / robot
            / "reference_conditions.json"
        )
        .read_text(encoding="utf-8")
    )
    case = next(
        item
        for item in conditions["cases"]
        if item["case_id"] == f"{robot}-g4-nominal"
    )
    model = mujoco.MjModel.from_xml_path(os.path.realpath(ROOT / scene))
    data = mujoco.MjData(model)

    class NativeWorld:
        pass

    world = NativeWorld()
    world.model, world.data = model, data

    def constant_native_hold() -> None:
        duration = float(case["request"]["max_duration_s"])
        steps = int(np.ceil(duration / float(model.opt.timestep)))
        for _ in range(steps):
            mujoco.mj_step(model, data)

    result = run_capability_case(
        world,
        case,
        tmp_path / robot / "constant_native_hold",
        execute=constant_native_hold,
        robot_definition={
            "id": robot,
            "capability_profile": f"autoadapter_bench/spec/capabilities/{robot}",
        },
    )
    assert result["score"] == 0.0
    assert result["evidence_summary"]["step_count"] > 0
    assert result["evidence_summary"]["ctrl_changed_from_reset"] is False

    module_spec = importlib.util.spec_from_file_location(
        f"fixed_{robot}", ROOT / driver_path
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    reference_world = module.build(model, data)
    reference_result = run_capability_case(
        reference_world,
        case,
        tmp_path / robot / "reference",
        execute=lambda: reference_world.set_body_height(case["request"]),
        robot_definition={
            "id": robot,
            "capability_profile": f"autoadapter_bench/spec/capabilities/{robot}",
        },
        driver_origin="reference",
        reference=True,
    )
    assert reference_result["score"] == 1.0
