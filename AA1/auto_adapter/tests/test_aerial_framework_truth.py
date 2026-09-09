"""Focused regressions for takeoff overshoot, teleporting and morphology routing."""
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from auto_adapter.orchestrator_from_scratch import _trusted_from_scratch_class
from autoadapter_bench.eval import evaluate_success
from autoadapter_bench.physics import PhysicsTrace, grade_takeoff_trace


def _samples(terminal):
    return [{"time": i * .1, "base_xyz": [0, 0, .2 if i == 0 else terminal],
             "base_upright": 1., "finite": True} for i in range(10)]


def test_takeoff_overshoot_and_short_hold_fail_both_shared_scores():
    spec = {"type": "takeoff_to_height", "target_height_m": .5,
            "tolerance_m": .05, "hold_s": .5}
    driver = SimpleNamespace(get_base_pose=lambda: (np.array([0, 0, 1.]), np.eye(3)))
    for samples in (_samples(1.0), _samples(.5)[:4]):
        # The old endpoint-only condition accepted an upright drone at 1 m.
        assert driver.get_base_pose()[0][2] >= .45
        assert not grade_takeoff_trace(samples)[0]
        assert not evaluate_success(driver, {"_physics_samples": samples}, spec, None, "fixture")[0]
    assert grade_takeoff_trace(_samples(.5))[0]


def test_catalog_humanoid_cannot_select_arm_validator():
    driver = SimpleNamespace(get_ee_pose=lambda: None, move_cartesian=lambda: True)
    with pytest.raises(ValueError, match="humanoid driver missing"):
        _trusted_from_scratch_class(driver, {"class": "humanoid"})
    driver.stand_balance = lambda: True
    driver.squat = lambda: True
    assert _trusted_from_scratch_class(driver, {"class": "humanoid"}) == "humanoid"


def test_physics_trace_rejects_teleport_before_step():
    model = mujoco.MjModel.from_xml_string('<mujoco><worldbody><body name="base"><freejoint/><geom type="sphere" size=".1"/></body></worldbody></mujoco>')
    data = mujoco.MjData(model)
    robot = SimpleNamespace(model=model, data=data)
    with pytest.raises(ValueError, match="changed live qpos"):
        with PhysicsTrace(robot, {"base_body": "base"}):
            data.qpos[2] = .5
            mujoco.mj_step(model, data)
