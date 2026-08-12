from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autoadapter2.integrations.direct_mujoco import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoDevelopmentSandbox,
    DirectMuJoCoEvaluationRobotSession,
    DirectMuJoCoFacade,
    DirectMuJoCoLibraryConfig,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_RECORD = ROOT / "libraries/morphology/so-arm101/1.0.0/record.json"


def _write_mini_mjcf(root: Path) -> Path:
    model_path = root / "migrated_1_0" / "robot.xml"
    model_path.parent.mkdir()
    model_path.write_text(
        """
<mujoco model="direct-mujoco-mini">
  <compiler angle="radian"/>
  <option timestep="0.01" gravity="0 0 0"/>
  <worldbody>
    <light name="key" pos="0 0 1"/>
    <body name="base" pos="0 0 0">
      <joint name="hinge_joint" type="hinge" axis="0 0 1" damping="0.1"/>
      <geom name="link" type="capsule" fromto="0 0 0 0.2 0 0" size="0.02"/>
      <site name="tip" pos="0.2 0 0" size="0.01"/>
    </body>
    <camera name="external" pos="0.8 -0.8 0.8" xyaxes="1 1 0 -1 1 0"/>
  </worldbody>
  <actuator>
    <position name="hinge_motor" joint="hinge_joint" kp="20" ctrlrange="-1 1" ctrllimited="true"/>
  </actuator>
  <sensor>
    <jointpos name="joint_position_sensor" joint="hinge_joint"/>
  </sensor>
</mujoco>
""".strip(),
        encoding="utf-8",
    )
    return model_path


def _migrated_record(root: Path) -> dict[str, object]:
    record = json.loads(MIGRATION_RECORD.read_text(encoding="utf-8"))
    record = copy.deepcopy(record)
    record["joint_names"] = ["hinge_joint"]
    record["actuator_names"] = ["hinge_motor"]
    record["sensor_names"] = ["joint_position_sensor"]
    mujoco = copy.deepcopy(record["mujoco"])
    mujoco.update(
        {
            "entrypoint": "migrated_1_0/robot.xml",
            "reset": {"qpos": [0.0], "qvel": [0.0]},
            "frames": {"body_names": ["base"], "site_names": ["tip"]},
            "render": {"camera": "external", "width": 32, "height": 24, "fps": 10},
        }
    )
    record["mujoco"] = mujoco
    return record


def test_invalid_library_mapping_fails_before_opening_mujoco(tmp_path: Path) -> None:
    record = _migrated_record(tmp_path)
    record["actuator_names"] = ["hinge_motor", "extra_motor"]

    with pytest.raises(DirectMuJoCoConfigurationError, match="actuator_names"):
        DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)


def test_missing_library_render_camera_reports_the_specific_field(tmp_path: Path) -> None:
    record = _migrated_record(tmp_path)
    del record["mujoco"]["render"]["camera"]

    with pytest.raises(DirectMuJoCoConfigurationError, match="morphology.mujoco.render.camera"):
        DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)


def test_direct_mujoco_1_0_migration_asset_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mujoco")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    record = _migrated_record(tmp_path)
    _write_mini_mjcf(tmp_path)
    session = DirectMuJoCoEvaluationRobotSession(record, asset_root=tmp_path)
    try:
        assert isinstance(session.sdk, DirectMuJoCoFacade)
        assert session.sdk.actuator_names == ("hinge_motor",)
        before = session.sdk.get_observation()
        session.sdk.send_action({"hinge_motor": 0.8})
        session.sdk.step(0.2)
        after = session.sdk.state()
        assert after["joint_positions"]["hinge_joint"] != before["joint_positions"]["hinge_joint"]
        assert after["sensors"]["joint_position_sensor"] == after["joint_positions"]["hinge_joint"]
        assert "model" not in dir(session.sdk)

        frame = session.capture_frame()
        assert (frame.width, frame.height) == (32, 24)
        assert len(frame.rgb) == 32 * 24 * 3

        session.reset()
        session.start_external_recording(phase="EXPERIMENTAL", execution_id="smoke")
        session.sdk.send_action({"hinge_motor": -0.4})
        session.sdk.step(0.1)
        frames = session.stop_external_recording()
        assert frames
        assert all(len(item.rgb) == 32 * 24 * 3 for item in frames)
    finally:
        session.close()
    with pytest.raises(Exception, match="closed"):
        session.sdk.state()


def test_development_sandbox_executes_source_through_facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mujoco")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    record = _migrated_record(tmp_path)
    _write_mini_mjcf(tmp_path)
    source = """
def capability_move(target, *, _sdk):
    _sdk.send_action({"hinge_motor": target})
    _sdk.step(0.2)
"""
    feedback = DirectMuJoCoDevelopmentSandbox(record, asset_root=tmp_path).run(
        source,
        {
            "probe_id": "smoke",
            "capability_id": "move",
            "arguments": {"target": 0.7},
            "horizon_s": 0.2,
        },
    )
    assert feedback["status"] == "OK"
    assert feedback["observations"]["accepted_action_count"] == 1
    assert feedback["observations"]["physics_step_count"] > 0
    assert feedback["observations"]["state_changed"] is True
    assert feedback["observations"]["observation"]["status"] == "EXPERIMENTAL"
