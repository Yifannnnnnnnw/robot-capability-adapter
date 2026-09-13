"""Named model/driver fixtures exercising the real MuJoCo task lifecycle."""
import json
from pathlib import Path

import pytest

from auto_adapter.agent.recap import _task_inputs, passed_design


@pytest.fixture
def task_inputs(tmp_path):
    source = tmp_path / "generation"
    source.mkdir()
    scene = source / "scene.xml"
    scene.write_text('''<mujoco><option timestep="0.01"/>
      <worldbody><light pos="0 0 3"/><geom type="plane" size="2 2 .1"/>
      <body pos="0 0 .3"><joint name="joint" type="hinge" axis="0 1 0"/>
      <geom type="capsule" size=".05 .2"/></body></worldbody>
      <actuator><motor joint="joint"/></actuator></mujoco>''')
    driver = source / "driver.py"
    driver.write_text('''import mujoco
class Robot:
    def __init__(self, path):
        self.model = mujoco.MjModel.from_xml_path(path)
        self.data = mujoco.MjData(self.model)
        self.calls = 0
    @classmethod
    def build_from_mjcf(cls, path): return cls(path)
    def advance(self, request):
        self.calls += 1
        mujoco.mj_step(self.model, self.data, request["steps"])
        return {"calls": self.calls}
    def get_joint_positions(self): return self.data.qpos.copy()
def build(): return Robot.build_from_mjcf("mjcf.xml")
''')
    (source / "mjcf.xml").symlink_to(scene)
    design = {"robot_configuration_id": "fixture", "capabilities": [{
        "capability_id": "C1", "method_name": "advance", "description": "Advance the joint.",
        "request_schema": {"type": "object", "properties": {
            "steps": {"type": "integer", "minimum": 1, "maximum": 40}},
            "required": ["steps"], "additionalProperties": False},
    }]}
    suite = {"cases": [{"case_id": "nominal", "capability_id": "C1",
                        "criteria": "PRIVATE_SENTINEL_DO_NOT_PLAN_WITH"},
                       {"case_id": "boundary", "capability_id": "C1"}]}
    report = {"tests": [{"case_id": c["case_id"], "ok": True} for c in suite["cases"]]}
    return dict(driver_path=driver, robot_id="fixture", capability_design=design,
                validation_suite=suite, validation_report=report,
                task_description="Perform the two actions.", scene_path=scene,
                initial_state={}, parameters={}, required_capabilities=["advance"],
                model="fixture", output_dir=tmp_path / "task")


class RecursiveFixtureModel:
    def __init__(self):
        self.turn = 0

    def generate_json(self, *, messages):
        assert "PRIVATE_SENTINEL_DO_NOT_PLAN_WITH" not in json.dumps(messages)
        action = json.dumps({"capability_name": "advance", "request": {"steps": 20}})
        plans = [
            ["First action", "Second action"],
            [action],
            [action],
            [],
        ]
        args = {"think": "Fixture action summary.", "subtasks": plans[self.turn]}
        self.turn += 1
        return json.dumps(args)


@pytest.mark.parametrize("scratch", [False, True])
def test_fresh_persistent_world_and_separate_generation_artifacts(task_inputs, scratch):
    from auto_adapter.agent.task_execution import run_task

    original = task_inputs["driver_path"].read_bytes()
    original_scene = (task_inputs["driver_path"].parent / "mjcf.xml").readlink()
    task_inputs["from_scratch"] = scratch
    report = run_task(**task_inputs, model_client=RecursiveFixtureModel())
    assert report["ok"], report
    assert report["controller"] == "auto_adapter.agent.vendor.recap.chatbot.chatbot"
    assert report["controller_source_commit"] == "2fb112ffad685c7c6f7de86d5487ecca6f566fcc"
    assert report["physical_task_success"] is None
    assert report["controller_result"]["capability_calls"] == 2
    assert report["sim_time_end"] == pytest.approx(0.4)
    assert report["n_frames"] > 1
    assert Path(report["video_path"]).stat().st_size > 100
    assert Path(report["trace_path"]).is_file()
    tree = report["controller_result"]["context_tree"]
    assert len(tree["children"]) == 2
    assert tree["children"][0]["task_name"] == "First action"
    assert json.loads(tree["children"][1]["task_name"])["capability_name"] == "advance"
    assert tree["info_list"][-1]["subtasks"] == []
    turns = [json.loads(line) for line in Path(report["model_trace_path"]).read_text().splitlines()]
    assert len(turns) == 4
    assert all("messages" in turn and "response" in turn for turn in turns)
    assert task_inputs["driver_path"].read_bytes() == original
    assert (task_inputs["driver_path"].parent / "mjcf.xml").readlink() == original_scene
    # A second task starts from its own initial state, never the last task's time.
    task_inputs["output_dir"] = task_inputs["output_dir"].with_name("second-task")
    second = run_task(**task_inputs, model_client=RecursiveFixtureModel())
    assert second["ok"]
    assert second["sim_time_start"] == pytest.approx(0.0)
    assert second["sim_time_end"] == pytest.approx(0.4)


def test_missing_required_validation_blocks_before_any_model_call(task_inputs):
    from auto_adapter.agent.task_execution import run_task

    task_inputs["validation_report"]["tests"][-1]["ok"] = False
    model = RecursiveFixtureModel()
    report = run_task(**task_inputs, model_client=model)
    assert not report["ok"]
    assert model.turn == 0
    assert report["physical_task_success"] is None
    assert Path(report["report_path"]).is_file()


def test_explicit_dynamic_design_never_falls_back_to_catalog(tmp_path):
    design = {"robot_configuration_id": "piper", "capabilities": [{"method_name": "new_action"}]}
    config = tmp_path / "demo.yaml"
    config.write_text("robots:\n  piper:\n    task: fixture\n    scene: fixture.xml\n")
    with pytest.raises(ValueError, match="corresponding scene_cases_path"):
        _task_inputs(workspace=tmp_path, robot_id="piper", capability_design=design,
                     demo_config_path=config)


def test_dynamic_case_filter_requires_all_case_results(task_inputs):
    suite = {"scene_cases": task_inputs["validation_suite"]["cases"]}
    assert passed_design(task_inputs["capability_design"], suite,
                         task_inputs["validation_report"])["capabilities"]
    task_inputs["validation_report"]["tests"].pop()
    with pytest.raises(ValueError, match="no fully Framework-passed"):
        passed_design(task_inputs["capability_design"], suite, task_inputs["validation_report"])
