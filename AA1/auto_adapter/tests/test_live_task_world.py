"""Regression: task evaluation must observe the live world, not a rebuild."""
import json
from types import SimpleNamespace

import mujoco

from autoadapter_bench.eval import run_task


def test_task_uses_one_world_for_execution_and_truth(tmp_path):
    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><body name="body">
        <joint name="j" type="slide"/><geom type="sphere" size=".03" mass="1"/>
        <site name="ee"/></body></worldbody><actuator><position joint="j" kp="100"/>
        </actuator></mujoco>''')
    driver = SimpleNamespace(model=model, data=mujoco.MjData(model))
    builds = []

    def build():
        builds.append(driver)
        return driver

    def execute(prompt, **kwargs):
        assert kwargs["driver"] is driver
        assert kwargs["initialize"] is False
        driver.data.ctrl[0] = .1
        mujoco.mj_step(model, driver.data, 100)
        return SimpleNamespace(ok=True, tool_call_log=[{"tool": "fixture_move", "ok": True}],
                               n_tool_calls=1, n_frames=0, duration_sec=.2, token_usage={},
                               summary="fixture", error=None, mp4_path=None, trace_path=None)

    planner = SimpleNamespace(_load_driver=build, execute_task=execute,
                              rec_dir=tmp_path / "recordings", trace_dir=tmp_path / "traces")
    result = run_task(planner, {"class": "arm", "state_refs": {"ee_site": "ee"}}, {
        "id": "fixture", "prompt": "fixture",
        "success": {"type": "tool_executed_without_crash", "required_tools": ["fixture_move"]},
    }, 1)[0]
    assert len(builds) == 1
    samples = json.loads(open(result["physics_trace_path"]).read())
    assert len(samples) == 101
    assert samples[-1]["time"] == driver.data.time
    assert samples[-1]["ee"] != samples[0]["ee"]
    # This named test provides no video; it must not claim a complete trial.
    assert not result["physics_ok"]
