from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from autoadapter2.integrations.direct_mujoco import (
    DirectMuJoCoConfigurationError,
    DirectMuJoCoDevelopmentSandbox,
    DirectMuJoCoEvaluationRobotSession,
    DirectMuJoCoFacade,
    DirectMuJoCoLibraryConfig,
    DirectMuJoCoTaskConfig,
)
from autoadapter2.validation import HarnessInvocation


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_RECORD = ROOT / "libraries/morphology/so-arm101/1.0.0/record.json"


def _write_mini_mjcf(root: Path, *, include_camera: bool = True) -> Path:
    model_path = root / "migrated_1_0" / "robot.xml"
    model_path.parent.mkdir()
    camera_xml = '<camera name="external" pos="0.8 -0.8 0.8" xyaxes="1 1 0 -1 1 0"/>' if include_camera else ""
    model_path.write_text(
        f"""
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
    {camera_xml}
  </worldbody>
  <actuator>
    <position name="hinge_motor" joint="hinge_joint" kp="20" ctrlrange="-1 1" ctrllimited="true"/>
  </actuator>
</mujoco>
""".strip(),
        encoding="utf-8",
    )
    return model_path


def _write_body_actuator_mjcf(root: Path) -> Path:
    model_path = root / "migrated_1_0" / "robot.xml"
    model_path.parent.mkdir()
    model_path.write_text(
        """
<mujoco model="direct-mujoco-body-actuator">
  <option timestep="0.01" gravity="0 0 0"/>
  <worldbody>
    <light name="key" pos="0 0 1"/>
    <body name="base" pos="0 0 0">
      <geom name="link" type="sphere" size="0.05"/>
      <site name="tip" pos="0 0 0.05" size="0.01"/>
    </body>
    <camera name="external" pos="0.8 -0.8 0.8" xyaxes="1 1 0 -1 1 0"/>
  </worldbody>
  <actuator>
    <motor name="body_motor" site="tip" gear="1 0 0 0 0 0"/>
  </actuator>
</mujoco>
""".strip(),
        encoding="utf-8",
    )
    return model_path


def _migrated_record(
    root: Path,
    *,
    reset: dict[str, object] | None = None,
    camera: str = "external",
) -> dict[str, object]:
    record = json.loads(MIGRATION_RECORD.read_text(encoding="utf-8"))
    record = copy.deepcopy(record)
    record["joint_names"] = ["hinge_joint"]
    record["actuator_names"] = ["hinge_motor"]
    record["sensor_names"] = []
    mujoco = copy.deepcopy(record["mujoco"])
    mujoco.update(
        {
            "entrypoint": "migrated_1_0/robot.xml",
            "reset": reset or {"qpos": [0.0], "qvel": [0.0]},
            "frames": {"body_names": ["base"], "site_names": ["tip"]},
            "render": {"camera": camera, "width": 32, "height": 24, "fps": 10},
        }
    )
    record["mujoco"] = mujoco
    return record


def test_empty_actuator_mapping_fails_before_opening_mujoco(tmp_path: Path) -> None:
    record = _migrated_record(tmp_path)
    record["actuator_names"] = []

    with pytest.raises(DirectMuJoCoConfigurationError, match="actuator_names.*empty"):
        DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)


def test_zero_joint_record_resolves_actuator_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _migrated_record(tmp_path, reset={"policy": "model_default"})
    record["joint_names"] = []
    record["actuator_names"] = ["body_motor"]
    config = DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)
    assert config.joint_names == ()
    assert config.actuator_names == ("body_motor",)

    pytest.importorskip("mujoco")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    _write_body_actuator_mjcf(tmp_path)
    session = DirectMuJoCoEvaluationRobotSession(record, asset_root=tmp_path)
    try:
        assert session.state()["joints"] == {}
        assert session.state()["joint_positions"] == {}
        assert session.state()["joint_velocities"] == {}
        with pytest.raises(Exception, match="unknown actuator"):
            session.send_action({"missing_motor": 0.2})
        session.send_action({"body_motor": 0.2})
        session.step(0.1)
        assert session.accepted_action_count == 1
        assert session.physics_step_count > 0
    finally:
        session.close()


def test_missing_library_render_camera_reports_the_specific_field(tmp_path: Path) -> None:
    record = _migrated_record(tmp_path)
    del record["mujoco"]["render"]["camera"]

    with pytest.raises(DirectMuJoCoConfigurationError, match="morphology.mujoco.render.camera"):
        DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)


@pytest.mark.parametrize(
    "reset",
    [
        {"policy": "unknown"},
        {"policy": "model_default", "qpos": [0.0]},
        {"keyframe": "home", "extra": True},
        {"qpos": [0.0], "extra": True},
    ],
)
def test_reset_mapping_rejects_unknown_or_ambiguous_forms(
    tmp_path: Path,
    reset: dict[str, object],
) -> None:
    record = _migrated_record(tmp_path, reset=reset)

    with pytest.raises(DirectMuJoCoConfigurationError, match="morphology.mujoco.reset"):
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
        assert after["sensors"] == {}
        assert session.sensor_observation() == {}
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


def test_task_config_binds_declared_scene_frames_and_measurements(tmp_path: Path) -> None:
    record = _migrated_record(tmp_path)
    _write_mini_mjcf(tmp_path)
    config = DirectMuJoCoLibraryConfig.from_record(record, asset_root=tmp_path)
    task = DirectMuJoCoTaskConfig.from_value(
        {
            "task_id": "mini-reach",
            "scene_entrypoint": "migrated_1_0/robot.xml",
            "frames": {"body_names": ["base"], "site_names": [], "sensor_names": []},
            "parameters": {"target_offset_m": [0.0, 0.0, 0.0]},
            "measurements": [
                {
                    "metric": "joint_position",
                    "path": "joint_positions.hinge_joint",
                }
            ],
        }
    )
    bound = config.bind_task(task)
    assert bound.entrypoint == "migrated_1_0/robot.xml"
    assert bound.body_names == ("base",)
    assert bound.measurement_declarations[-1]["metric"] == "joint_position"
    assert bound.bound_task == task


def test_validation_evidence_records_physical_direct_route_without_no_action_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mujoco")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    record = _migrated_record(tmp_path)
    _write_mini_mjcf(tmp_path)

    invocation = HarnessInvocation(
        capability_id="move",
        case_id="case-1",
        inputs={},
        initial_state={},
        repetition=1,
        measurement={
            "measurement_id": "hinge-position",
            "entity": "hinge_joint",
            "unit": "rad",
            "frame": "joint",
            "path": "joint_positions.hinge_joint",
        },
        metric="joint_position",
        threshold={"comparator": "<=", "value": 1.0},
        dwell_s=0.1,
        timeout_s=0.2,
        aggregation="ALL",
        guard_ids=("direct-route-verified", "finite-required-state"),
        run_snapshot_hash="snapshot",
        candidate_source_hash="candidate",
        suite_hash="suite",
        execution_attempt=1,
        criterion_id="move-criterion",
    )

    class Candidate:
        def _invoke(self, _capability_id, _arguments, sdk):
            sdk.state()
            sdk.send_action({"hinge_motor": 0.8})
            sdk.step(0.1)
            return {"status": "OK"}

    class NoActionCandidate:
        def _invoke(self, _capability_id, _arguments, sdk):
            sdk.state()
            sdk.step(0.1)
            return {"status": "OK"}

    session = DirectMuJoCoEvaluationRobotSession(record, asset_root=tmp_path)
    try:
        session.invoke(Candidate(), "move", {})
        evidence = session.validation_evidence(invocation)
        assert evidence.sdk_route_verified is True
        assert evidence.route_evidence["evidence_scope"] == "DIRECT_MUJOCO_EXPERIMENTAL"
        assert evidence.route_evidence["sdk_grounded"] is False
        assert evidence.criterion_samples["move-criterion"]
        assert all(math.isfinite(sample.value) for sample in evidence.samples)

        session.reset()
        session.invoke(NoActionCandidate(), "move", {})
        no_action = session.validation_evidence(invocation)
        assert no_action.sdk_route_verified is False
        assert no_action.route_evidence["accepted_command_count"] == 0
    finally:
        session.close()


def test_model_default_reset_and_free_camera_use_shared_mujoco_forms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mujoco")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    record = _migrated_record(tmp_path, reset={"policy": "model_default"}, camera="free")
    _write_mini_mjcf(tmp_path, include_camera=False)
    session = DirectMuJoCoEvaluationRobotSession(record, asset_root=tmp_path)
    try:
        assert session.config.reset_policy == "model_default"
        assert session._camera_id == -1
        before = session.state()["joint_positions"]["hinge_joint"]
        session.send_action({"hinge_motor": 0.7})
        session.step(0.2)
        assert session.state()["joint_positions"]["hinge_joint"] != before
        session.reset()
        assert session.state()["joint_positions"]["hinge_joint"] == 0.0
        frame = session.capture_frame()
        assert len(frame.rgb) == 32 * 24 * 3
    finally:
        session.close()


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
