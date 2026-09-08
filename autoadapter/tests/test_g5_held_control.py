"""Real MuJoCo regressions for constant native-servo control in G5."""

from pathlib import Path

import pytest

from autoadapter2.fixed_family import reference
from autoadapter2.harness.measurements import evaluate_guards
from autoadapter2.harness.runner import run_private_suite
from autoadapter2.harness.worker import execute_case
from autoadapter2.libraries import load_indexed_robot_package


ROOT = Path(__file__).resolve().parents[1]
CONTROL_GUARD = [{"guard_id": "control", "kind": "actuator_and_physics_step_required"}]


def _servo_case(tmp_path, *, passive=False, steps=20):
    scene = tmp_path / "scene.xml"
    actuator = '<motor joint="joint"/>' if passive else '<position joint="joint" kp="40"/>'
    scene.write_text(
        '<mujoco><option gravity="0 0 0"/>'
        '<worldbody><body><joint name="joint" damping="2"/>'
        '<geom type="sphere" size=".1" mass="1"/></body></worldbody>'
        f'<actuator>{actuator}</actuator></mujoco>'
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        'import mujoco\n'
        'class Driver:\n'
        '    def __init__(self, model, data):\n'
        '        self.model, self.data = model, data\n'
        '    def command_joint(self, request):\n'
        f'        for _ in range({steps}):\n'
        '            self.data.ctrl[:] = 0.0\n'
        '            mujoco.mj_step(self.model, self.data)\n'
        'def build(model, data):\n'
        '    return Driver(model, data)\n'
    )
    return execute_case({
        "driver_path": str(driver), "scene_path": str(scene),
        "capability_methods": ["command_joint"], "method_name": "command_joint",
        "public_arguments": {"request": {}},
        "reset": {"kind": "default", "qpos": [.4], "ctrl": [0.]},
        "max_steps": 30, "max_sim_time_s": 1., "render": {"enabled": False},
    })


def test_constant_servo_is_real_control_without_a_changed_target(tmp_path):
    result = _servo_case(tmp_path)
    evidence = result["physical_evidence"]
    assert result["candidate_exception"] is None
    assert evidence["step_count"] == 20
    assert not evidence["ctrl_changed_from_reset"]
    assert evidence["actuator_force_nonzero_step_count"] == 20
    assert evidence["samples"][-1]["qpos"][0] < .4
    # The old control rule rejects this real, physically effective servo.
    assert not evaluate_guards(CONTROL_GUARD, worker_result=result)["control"]
    assert evaluate_guards(
        CONTROL_GUARD, worker_result=result, allow_held_actuator_control=True,
    )["control"]


@pytest.mark.parametrize("passive,steps", [(True, 20), (False, 0)])
def test_passive_or_unstepped_servo_does_not_gain_control_evidence(tmp_path, passive, steps):
    result = _servo_case(tmp_path, passive=passive, steps=steps)
    assert result["candidate_exception"] is None
    assert result["physical_evidence"]["actuator_force_nonzero_step_count"] == 0
    assert not evaluate_guards(
        CONTROL_GUARD, worker_result=result, allow_held_actuator_control=True,
    )["control"]


@pytest.mark.parametrize("capability,operator,expected_control", [
    ("G5", "quadruped_stable_stance_recovery", True),
    ("G5", "go2_stable_stance_recovery", True),
    ("G4", "quadruped_body_height_regulation", False),
])
def test_g5_guard_exception_does_not_replace_physical_criteria(
    tmp_path, capability, operator, expected_control,
):
    package = load_indexed_robot_package(ROOT, "unitree_a1", require_task_library=False)
    fixed = reference("unitree_a1")
    design, suite = fixed["capability_design"], fixed["validation_suite"]
    design["capabilities"] = [c for c in design["capabilities"] if c["capability_id"] == capability]
    case = next(c for c in suite["cases"] if c["capability_id"] == capability)
    case["measurement_binding"]["kind"] = operator
    suite["cases"] = [case]
    driver = tmp_path / "driver.py"
    driver.write_text(
        'import mujoco\n'
        'class Driver:\n'
        '    def __init__(self, model, data):\n'
        '        self.model, self.data = model, data\n'
        f'    def {case["method_name"]}(self, request):\n'
        '        for _ in range(20):\n'
        '            self.data.ctrl[:] = self.data.ctrl.copy()\n'
        '            mujoco.mj_step(self.model, self.data)\n'
        'def build(model, data):\n'
        '    return Driver(model, data)\n'
    )
    report = run_private_suite(
        package=package, design=design, suite=suite, driver_path=driver,
        condition="from-scratch", output_dir=tmp_path / "harness", record_video=False,
    )
    trial = report["trials"][0]
    assert trial["worker_completed"]
    assert trial["candidate_exception"] is None
    assert trial["measurement_error"] is None
    assert not trial["physical_evidence"]["ctrl_changed_from_reset"]
    assert trial["physical_evidence"]["actuator_force_nonzero_step_count"] > 0
    assert trial["guard_outcomes"]["unitree_a1-fixed-control"] is expected_control
    # Twenty 2-ms steps cannot satisfy the required stance/height hold duration.
    assert not trial["criterion_passed"]
    assert not trial["trial_passed"]
    assert not report["validation_passed"]
