from __future__ import annotations

import mujoco

from autoadapter2.harness.measurements import evaluate_guards
from autoadapter2.harness.session import TrackedMuJoCoSession


MODEL_XML = """
<mujoco model="neutral_peak_probe">
  <option timestep="0.002"/>
  <worldbody>
    <body>
      <joint name="other/waist" type="hinge" axis="0 0 1"
             range="-1 1" damping="4"/>
      <geom type="capsule" fromto="0 0 0 0.2 0 0" size="0.02" mass="1"/>
    </body>
  </worldbody>
  <actuator>
    <position name="other/waist" joint="other/waist" kp="100"/>
  </actuator>
</mujoco>
"""


def test_neutral_guard_rejects_an_excursion_between_evidence_samples() -> None:
    model = mujoco.MjModel.from_xml_string(MODEL_XML)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    tracker = TrackedMuJoCoSession(
        mujoco=mujoco,
        model=model,
        data=data,
        max_steps=1000,
        max_sim_time_s=2.0,
        sample_hz=1.0,
    )

    with tracker:
        data.ctrl[0] = 0.8
        mujoco.mj_step(model, data, nstep=150)
        data.ctrl[0] = 0.0
        mujoco.mj_step(model, data, nstep=340)
        tracker.finish()

    evidence = tracker.evidence()
    sampled_positions = [
        abs(sample["joint_positions"]["other/waist"])
        for sample in evidence["samples"]
    ]
    peak = evidence["joint_max_abs_deviation_from_reset"]["other/waist"]
    assert len(sampled_positions) == 2
    assert max(sampled_positions) < 1e-6
    assert peak > 0.15

    guard = {
        "guard_id": "other-arm-neutral",
        "kind": "named_joints_remain_near_reset",
        "joint_tolerances": {"other/waist": 0.05},
    }
    worker_result = {"physical_evidence": evidence}
    assert evaluate_guards([guard], worker_result=worker_result) == {
        "other-arm-neutral": False
    }

    guard["joint_tolerances"]["other/waist"] = 0.2
    assert evaluate_guards([guard], worker_result=worker_result) == {
        "other-arm-neutral": True
    }
