from __future__ import annotations

import importlib.util
import inspect
import math
from pathlib import Path

import numpy as np
import pytest

from autoadapter2.harness.session import apply_framework_reset


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter/libraries/robots/robotstudio_so101/1.0.0"
)
DRIVER_PATH = PACKAGE_ROOT / "reference/fixed_capability_driver.py"


def _load_driver_module():
    spec = importlib.util.spec_from_file_location(
        "b2_so101_wrist_roll_reference", DRIVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_set_wrist_roll_is_closed_bounded_and_steps_real_physics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mujoco = pytest.importorskip("mujoco")
    module = _load_driver_module()
    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets/reach_scene.xml")
    )
    data = mujoco.MjData(model)
    call_time_targets = {
        "shoulder_pan": 0.2,
        "shoulder_lift": -0.3,
        "elbow_flex": 0.4,
        "wrist_flex": -0.2,
        "wrist_roll": 0.3,
        "gripper": 0.8,
    }
    apply_framework_reset(
        mujoco,
        model,
        data,
        {
            "kind": "default",
            "joint_positions": call_time_targets,
            "actuator_controls": call_time_targets,
        },
    )
    driver = module.build(model=model, data=data)

    for invalid_request in (
        {"target_roll_rad": 0.0},
        {"target_roll_rad": 0.0, "max_duration_s": 1.0, "extra": 1},
        {"target_roll_rad": math.nan, "max_duration_s": 1.0},
        {"target_roll_rad": -2.7438474, "max_duration_s": 1.0},
        {"target_roll_rad": 2.7438474, "max_duration_s": 1.0},
        {"target_roll_rad": 0.0, "max_duration_s": 0.249},
    ):
        with pytest.raises((TypeError, ValueError)):
            driver.set_wrist_roll(invalid_request)

    before = driver._arm.get_joint_positions().copy()
    gripper_before = driver._gripper_position()
    wrist_joint = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_JOINT, "wrist_roll"
    )
    wrist_qpos = int(model.jnt_qposadr[wrist_joint])
    real_step = mujoco.mj_step
    wrist_samples: list[float] = []

    def recording_step(step_model, step_data) -> None:
        real_step(step_model, step_data)
        wrist_samples.append(float(step_data.qpos[wrist_qpos]))

    monkeypatch.setattr(module.mujoco, "mj_step", recording_step)
    start_time = float(data.time)
    target = -math.pi / 2.0
    driver.set_wrist_roll(
        {"target_roll_rad": target, "max_duration_s": 2.0}
    )
    elapsed = float(data.time) - start_time
    after = driver._arm.get_joint_positions()

    assert 0.0 < elapsed <= 2.0 + 1.0e-9
    assert abs(float(after[-1]) - target) <= 0.02
    required_hold_steps = int(math.floor(0.25 / model.opt.timestep + 1.0e-12))
    assert len(wrist_samples) >= required_hold_steps
    assert all(
        abs(position - target) <= 0.03
        for position in wrist_samples[-required_hold_steps:]
    )
    np.testing.assert_allclose(after[:-1], before[:-1], atol=0.005, rtol=0.0)
    assert abs(driver._gripper_position() - gripper_before) <= 0.005

    actuator_targets = {
        name: float(
            data.ctrl[
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            ]
        )
        for name in call_time_targets
    }
    for name in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"):
        assert actuator_targets[name] == pytest.approx(call_time_targets[name])
    assert actuator_targets["wrist_roll"] == pytest.approx(target)
    assert actuator_targets["gripper"] == pytest.approx(gripper_before)
    assert "qpos[" not in inspect.getsource(module.Driver.set_wrist_roll)
