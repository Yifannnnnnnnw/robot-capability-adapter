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
