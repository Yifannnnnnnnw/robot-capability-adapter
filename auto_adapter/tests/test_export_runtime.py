"""Focused real-MuJoCo checks for the exported server runtime."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from auto_adapter.export_runtime import ExportRuntime


@pytest.fixture
def tiny_scene(tmp_path: Path) -> Path:
    scene = tmp_path / "tiny.xml"
    scene.write_text(
        """<mujoco model="tiny">
          <option timestep="0.01"/>
          <worldbody>
            <geom name="floor" type="plane" size="2 2 .1"/>
            <body name="arm" pos="0 0 .3">
              <joint name="hinge" type="hinge" axis="0 1 0"/>
              <geom name="link" type="capsule" size=".05 .2"/>
            </body>
          </worldbody>
          <actuator><motor name="motor" joint="hinge"/></actuator>
        </mujoco>""",
        encoding="utf-8",
    )
    return scene


class TinyRobot:
    def __init__(self, scene_path: Path) -> None:
        self.model = mujoco.MjModel.from_xml_path(str(scene_path))
        self.data = mujoco.MjData(self.model)
        self.closed = False

    def advance(self, request: dict) -> dict:
        for _ in range(int(request["steps"])):
            mujoco.mj_step(self.model, self.data)
        return {"sim_time_s": float(self.data.time)}

    def get_joint_positions(self) -> np.ndarray:
        return self.data.qpos.copy()

    def close(self) -> None:
        self.closed = True


def test_lazy_singleton_observes_real_world_and_detaches_values(
    tiny_scene: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AA1_TASK_RUNTIME_CONFIG", raising=False)
    builds: list[TinyRobot] = []

    def builder() -> TinyRobot:
        robot = TinyRobot(tiny_scene)
        builds.append(robot)
        return robot

    runtime = ExportRuntime(builder)
    robot = runtime.robot
    assert runtime.robot is robot
    assert len(builds) == 1

    first = runtime.observe()
    robot.advance({"steps": 2})
    second = runtime.observe()
    assert first["sim_time_s"] == pytest.approx(0.0)
    assert second["sim_time_s"] > first["sim_time_s"]
    assert second["sim_time_s"] == pytest.approx(0.02)
    assert second["qpos"] == robot.data.qpos.tolist()
    assert second["get_joint_positions"] == robot.data.qpos.tolist()

    # Public observations are detached snapshots, so a caller cannot mutate
    # the live MuJoCo state through a previous response.
    second["qpos"][0] = 123.0
    second["get_joint_positions"][0] = 123.0
    assert runtime.observe()["qpos"] == robot.data.qpos.tolist()

    report = runtime.close()
    assert robot.closed
    assert runtime.close() == report
    with pytest.raises(RuntimeError, match="closed"):
        _ = runtime.robot


@pytest.fixture
def keyed_scene(tmp_path: Path) -> Path:
    scene = tmp_path / "keyed.xml"
    scene.write_text(
        """<mujoco model="keyed">
          <option timestep="0.01"/>
          <worldbody>
            <geom name="floor" type="plane" size="2 2 .1"/>
            <body name="arm" pos="0 0 .3">
              <joint name="hinge" type="hinge" axis="0 1 0"/>
              <geom name="link" type="capsule" size=".05 .2"/>
            </body>
            <body name="object" pos="0 0 .2">
              <freejoint name="object_free"/>
              <geom name="object_geom" type="box" size=".04 .04 .04"/>
            </body>
          </worldbody>
          <keyframe>
            <key name="robot_start" qpos=".4 0 0 .2 1 0 0 0"/>
          </keyframe>
        </mujoco>""",
        encoding="utf-8",
    )
    return scene


def test_config_applies_robot_then_free_body_initial_state(
    keyed_scene: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "recording"
    config_path = tmp_path / "runtime.json"
    initial_state = {
        "robot": {
            "keyframe": "robot_start",
            "qpos_by_joint": {"hinge": -0.25},
        },
        "free_bodies": {
            "object": {
                "position_m": [0.45, 0.1, 0.3],
                "quaternion_wxyz": [1, 0, 0, 0],
                "linear_velocity_m_s": [0, 0, 0],
                "angular_velocity_rad_s": [0, 0, 0],
            }
        },
    }
    config_path.write_text(
        json.dumps(
            {
                "scene_path": str(keyed_scene),
                "initial_state": initial_state,
                "output_dir": str(output_dir),
                "record_video": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AA1_TASK_RUNTIME_CONFIG", str(config_path))

    runtime = ExportRuntime(lambda: TinyRobot(keyed_scene))
    robot = runtime.robot
    hinge_id = robot.model.joint("hinge").id
    hinge_qpos = int(robot.model.jnt_qposadr[hinge_id])
    object_id = robot.model.body("object").id
    object_joint = int(robot.model.body_jntadr[object_id])
    object_qpos = int(robot.model.jnt_qposadr[object_joint])

    # The keyframe selects the robot pose, then the explicit free body state
    # replaces the object's keyframe/default pose.
    assert robot.data.qpos[hinge_qpos] == pytest.approx(-0.25)
    assert robot.data.qpos[object_qpos : object_qpos + 3].tolist() == pytest.approx(
        [0.45, 0.1, 0.3]
    )
    assert runtime.observe()["sim_time_s"] == pytest.approx(0.0)

    report = runtime.close()
    report_path = output_dir / "runtime_report.json"
    assert report_path.is_file()
    assert report["sim_time_start"] == pytest.approx(0.0)
    assert report["sim_time_end"] == pytest.approx(0.0)
    assert report["n_frames"] == report["frame_count"] == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_scene_mismatch_is_rejected_once(tiny_scene: Path, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    other_scene = tmp_path / "other.xml"
    other_scene.write_text(tiny_scene.read_text(encoding="utf-8").replace(
        'size=".05 .2"', 'size=".08 .2"'
    ), encoding="utf-8")
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps({
        "scene_path": str(tiny_scene),
        "initial_state": {},
        "output_dir": str(tmp_path / "out"),
        "record_video": False,
    }), encoding="utf-8")
    monkeypatch.setenv("AA1_TASK_RUNTIME_CONFIG", str(config_path))
    builds = 0

    def builder() -> TinyRobot:
        nonlocal builds
        builds += 1
        return TinyRobot(other_scene)

    runtime = ExportRuntime(builder)
    with pytest.raises(ValueError, match="different scene"):
        _ = runtime.robot
    with pytest.raises(RuntimeError, match="build failed"):
        _ = runtime.robot
    assert builds == 1
    runtime.close()


def test_physical_trace_samples_every_native_step_without_rendering(
    keyed_scene, tmp_path, monkeypatch,
):
    config = {
        "scene_path": str(keyed_scene), "initial_state": {},
        "output_dir": str(tmp_path / "physics"), "record_video": False,
        "success": {"bindings": {
            "arm": {"kind": "body", "name": "arm"},
            "hinge": {"kind": "joint", "name": "hinge"},
        }},
    }
    path = tmp_path / "physics_config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("AA1_TASK_RUNTIME_CONFIG", str(path))
    original = mujoco.mj_step
    runtime = ExportRuntime(lambda: TinyRobot(keyed_scene))
    try:
        robot = runtime.robot
        mujoco.mj_step(robot.model, robot.data, nstep=3)
        mujoco.mj_step1(robot.model, robot.data)
        mujoco.mj_step2(robot.model, robot.data)
        # Other-world advances never contaminate this task's samples.
        other = TinyRobot(keyed_scene)
        mujoco.mj_step(other.model, other.data, nstep=2)
        runtime.observe()
    finally:
        report = runtime.close()
    rows = [json.loads(line) for line in Path(report["physics_trace"]["path"]).read_text().splitlines()]
    assert report["physics_trace"]["error"] is None
    assert report["physics_trace"]["sample_count"] == len(rows) == 5
    assert [row["time"] for row in rows] == pytest.approx([0, .01, .02, .03, .04])
    assert rows[-1]["state"]["hinge"]["value"] == pytest.approx(robot.data.qpos[0])
    assert rows[-1]["state"]["arm"]["position"] == pytest.approx(robot.data.xpos[1])
    assert report["video_path"] is None
    assert mujoco.mj_step is original


def test_missing_native_steps_are_not_accepted_as_complete_trace(
    tiny_scene, tmp_path, monkeypatch,
):
    config = {"scene_path": str(tiny_scene), "initial_state": {},
              "output_dir": str(tmp_path / "gap"), "record_video": False,
              "success": {"bindings": {"q": {"kind": "joint", "name": "hinge"}}}}
    path = tmp_path / "gap_config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("AA1_TASK_RUNTIME_CONFIG", str(path))
    original_step = mujoco.mj_step
    runtime = ExportRuntime(lambda: TinyRobot(tiny_scene))
    try:
        robot = runtime.robot
        original_step(robot.model, robot.data, 3)
    finally:
        report = runtime.close()
    assert "unobserved native" in report["physics_trace"]["error"]


def test_contacts_keep_native_solver_interval_without_recomputing_collisions(tmp_path):
    from auto_adapter.demo_trace import DemoTrace

    model = mujoco.MjModel.from_xml_string('''<mujoco><option timestep="0.01"/>
      <worldbody><geom type="plane" size="1 1 .1"/>
      <body name="ball" pos="0 0 .06"><freejoint/>
      <geom type="sphere" size=".05" mass="1"/></body></worldbody></mujoco>''')
    data = mujoco.MjData(model)
    trace = DemoTrace(model, data, {"ball": {"kind": "body", "name": "ball"}},
                      tmp_path / "contact.jsonl")
    try:
        mujoco.mj_step(model, data, 5)
        assert data.xpos[1, 2] < .05  # endpoint has just crossed the floor
        assert data.ncon == 0  # no new collision query was inserted into the solve
        mujoco.mj_step(model, data)
        assert data.ncon > 0
        force_before = np.zeros(6)
        mujoco.mj_contactForce(model, data, 0, force_before)
    finally:
        report = trace.close()
    force_after = np.zeros(6)
    mujoco.mj_contactForce(model, data, 0, force_after)
    np.testing.assert_array_equal(force_before, force_after)
    rows = [json.loads(line) for line in Path(report["path"]).read_text().splitlines()]
    assert rows[5]["contacts"] == []
    assert rows[5]["contact_interval_s"] == pytest.approx([.04, .05])
    assert rows[6]["contact_interval_s"] == pytest.approx([.05, .06])
    assert rows[6]["contacts"]  # contact first solved during this interval


def test_recording_writes_report_and_video_when_offscreen_rendering_is_available(
    tiny_scene: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "runtime.json"
    output_dir = tmp_path / "video"
    config_path.write_text(json.dumps({
        "scene_path": str(tiny_scene),
        "initial_state": {},
        "output_dir": str(output_dir),
        "record_video": True,
    }), encoding="utf-8")
    monkeypatch.setenv("AA1_TASK_RUNTIME_CONFIG", str(config_path))
    runtime = ExportRuntime(lambda: TinyRobot(tiny_scene))
    robot = runtime.robot
    if runtime._capture is None:
        runtime.close()
        pytest.skip("offscreen MuJoCo rendering is unavailable")
    robot.advance({"steps": 3})
    runtime.observe()
    report = runtime.close()
    # Even a call shorter than the regular capture interval records its endpoint.
    assert report["n_frames"] >= 2
    assert report["video_path"] == str(output_dir / "video.mp4")
    assert Path(report["video_path"]).stat().st_size > 0
    assert (output_dir / "runtime_report.json").is_file()
