from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
from threading import RLock
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from soarm_demo.direct_validation import FrameworkEvidenceError, execute_case
from soarm_demo.oracle import (
    MujocoTabletopDemoEnvironment,
    MujocoTabletopValidationEnvironment,
    P0_VALIDATION_BODY_DEFAULTS,
    _MujocoEpisodeVideoRecorder,
    _advance_with_sampling,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "libraries/morphology/soarm101/v1/model/so101.xml"
STATES = ROOT / "private/task_library/soarm101_tabletop/v1/initial_states"
COMMON = STATES / "_common_reset.json"
PACKAGE = ROOT / "fixtures/generated_pass"


def _state(name: str) -> dict[str, Any]:
    return json.loads((STATES / name).read_text(encoding="utf-8"))


def _legacy_common_reset() -> dict[str, Any]:
    common = json.loads(COMMON.read_text(encoding="utf-8"))
    common["table"] = {
        "center_m": [0.35, 0.0, 0.01],
        "half_size_m": [0.35, 0.25, 0.01],
        "surface_z_m": 0.02,
    }
    return common


def _set_object_xy(runtime: Any, object_id: str, x: float, y: float) -> None:
    """Trusted test probe: set free-body XY without adding an oracle control API."""

    with runtime._lock:
        joint_id = runtime._name_id(runtime._mj.mjtObj.mjOBJ_JOINT, f"{object_id}_freejoint")
        qpos_address = int(runtime.model.jnt_qposadr[joint_id])
        dof_address = int(runtime.model.jnt_dofadr[joint_id])
        runtime.data.qpos[qpos_address] = x
        runtime.data.qpos[qpos_address + 1] = y
        runtime.data.qvel[dof_address : dof_address + 6] = 0.0
        runtime._mj.mj_forward(runtime.model, runtime.data)


def _set_object_position(
    runtime: Any, object_id: str, x: float, y: float, z: float
) -> None:
    """Trusted test probe for a transient free-body trajectory sample."""

    with runtime._lock:
        joint_id = runtime._name_id(
            runtime._mj.mjtObj.mjOBJ_JOINT, f"{object_id}_freejoint"
        )
        qpos_address = int(runtime.model.jnt_qposadr[joint_id])
        dof_address = int(runtime.model.jnt_dofadr[joint_id])
        runtime.data.qpos[qpos_address : qpos_address + 3] = [x, y, z]
        runtime.data.qvel[dof_address : dof_address + 6] = 0.0
        runtime._mj.mj_forward(runtime.model, runtime.data)


def test_actual_validation_environment_applies_explicit_fixture_body_defaults_and_traces(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    trace = tmp_path / "private_validation_simulation.jsonl"
    case_context = {"framework_metadata": {"trace_path": str(trace)}}
    environment = MujocoTabletopValidationEnvironment(
        case_context,
        model_path=MODEL,
        common_reset=_legacy_common_reset(),
        scene_catalog=None,
        auto_step=False,
        reset_settle_s=0.05,
        measurement_settle_s=0.0,
    )
    try:
        assert environment._mujoco_runtime.scene_catalog_binding is None
        # Size, mass, and identity quaternion are intentionally omitted from
        # this validation-only body.  Position is never defaulted.
        environment.reset(
            {
                "bodies": [
                    {
                        "id": "validation_cube",
                        "kind": "cube",
                        "position_m": [0.30, -0.05, 0.035],
                    },
                    {
                        "id": "validation_cylinder",
                        "kind": "cylinder",
                        "position_m": [0.30, 0.05, 0.040],
                    },
                ]
            }
        )
        measured = environment.measure(
            {
                "joint_positions": {"shoulder_pan.pos": 0.0},
                "end_effector_position_m": [0.0, 0.0, 0.0],
                "object_positions_m": {
                    "validation_cube": [0.0, 0.0, 0.0],
                    "validation_cylinder": [0.0, 0.0, 0.0],
                },
                "contact": True,
                "finite": True,
            }
        )
        forbidden = environment.forbidden(
            ["action_clipped", "object_outside_workspace", "simulation_nan_or_instability"]
        )
        diagnostics = environment.diagnostics()
    finally:
        environment.close()

    assert P0_VALIDATION_BODY_DEFAULTS["cube"] == {
        "size_m": [0.03, 0.03, 0.03],
        "mass_kg": 0.020,
    }
    assert P0_VALIDATION_BODY_DEFAULTS["cylinder"] == {
        "radius_m": 0.015,
        "height_m": 0.040,
        "mass_kg": 0.020,
    }
    assert set(measured) == {
        "joint_positions",
        "end_effector_position_m",
        "object_positions_m",
        "contact",
        "finite",
    }
    assert measured["finite"] is True
    assert measured["contact"] is True
    assert measured["object_positions_m"]["validation_cube"][0] == pytest.approx(
        0.30, abs=1e-5
    )
    assert measured["object_positions_m"]["validation_cylinder"][0] == pytest.approx(
        0.30, abs=1e-5
    )
    assert forbidden == {
        "action_clipped": False,
        "object_outside_workspace": False,
        "simulation_nan_or_instability": False,
    }
    assert set(diagnostics["final_object_linear_velocities_m_s"]) == {
        "validation_cube",
        "validation_cylinder",
    }
    assert diagnostics["trajectory_summary_scope"] == {
        "target_aware": False,
        "time_origin": "post_reset_settle",
        "significant_motion_threshold_m": 0.002,
        "maximum_keyframes_per_object": 5,
    }
    assert math.isfinite(diagnostics["terminal_simulation_time_s"])
    assert diagnostics["terminal_simulation_time_s"] >= 0.0
    assert diagnostics["simulation_time_since_last_action_s"] is None
    cube_motion = diagnostics["object_motion_summaries"]["validation_cube"]
    assert cube_motion["initial_position_m"] == pytest.approx(
        cube_motion["final_position_m"], abs=1e-8
    )
    assert cube_motion["trajectory_sample_count"] >= 2
    assert len(cube_motion["keyframes"]) <= 5
    assert isinstance(cube_motion["gripper_contact_ever"], bool)
    assert isinstance(cube_motion["ever_grasped_by_opposing_jaws"], bool)
    assert diagnostics["action_clipping_details"] == []
    assert diagnostics["joint_or_actuator_limit_violations"] == []
    assert isinstance(diagnostics["terminal_contact_pairs"], list)
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "world_state" for event in events)


def test_generated_runtime_facade_exposes_only_lerobot_control_members() -> None:
    pytest.importorskip("mujoco")
    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    try:
        assert environment._mujoco_runtime.scene_catalog_binding is not None
        environment.reset({"bodies": []})
        runtime = environment.runtime
        assert set(runtime.action_features) == set(runtime.observation_features)
        assert runtime.is_connected is True
        assert runtime.is_calibrated is True
        assert isinstance(runtime.name, str)
        assert runtime.send_action({"shoulder_pan.pos": 1.0}) == {
            "shoulder_pan.pos": 1.0
        }
        assert "shoulder_pan.pos" in runtime.get_observation()

        for private_name in (
            "reset",
            "world_snapshot",
            "site_position",
            "body_position",
            "advance",
            "model",
            "data",
            "connect",
            "disconnect",
            "last_receipt",
            "video_path",
            "simulation_video_path",
            "renderer",
            "_control_backend",
            "__dict__",
            "__class__",
        ):
            with pytest.raises(AttributeError, match="LeRobot control contract"):
                getattr(runtime, private_name)
        for absent_storage in ("_control_backend", "_after_public_call"):
            with pytest.raises(AttributeError):
                object.__getattribute__(runtime, absent_storage)

        def untrusted_generated_function(injected_runtime: object) -> None:
            injected_runtime.reset()  # type: ignore[attr-defined]

        with pytest.raises(AttributeError, match="LeRobot control contract"):
            untrusted_generated_function(runtime)
        with pytest.raises(AttributeError, match="immutable"):
            runtime.model = object()
    finally:
        environment.close()


def test_diagnostics_separate_terminal_simulation_time_from_last_action_time() -> None:
    pytest.importorskip("mujoco")
    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    try:
        environment.reset({"bodies": []})
        environment.runtime.send_action({"shoulder_pan.pos": 1.0})
        receipt = environment._mujoco_runtime.last_receipt
        assert receipt is not None
        environment._mujoco_runtime.advance(0.05)
        diagnostics = environment.diagnostics()
    finally:
        environment.close()

    terminal_time_s = diagnostics["terminal_simulation_time_s"]
    since_last_action_s = diagnostics["simulation_time_since_last_action_s"]
    assert math.isfinite(terminal_time_s)
    assert math.isfinite(since_last_action_s)
    assert 0.0 < since_last_action_s <= terminal_time_s
    assert since_last_action_s == pytest.approx(
        terminal_time_s - float(receipt.simulation_time), abs=1e-12
    )


def test_forbidden_evidence_is_ever_seen_across_transient_clipping_and_workspace_escape() -> None:
    pytest.importorskip("mujoco")
    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    try:
        environment.reset(
            {
                "bodies": [
                    {
                        "id": "validation_cube",
                        "kind": "cube",
                        "position_m": [0.30, 0.0, 0.035],
                    }
                ]
            }
        )
        assert environment.forbidden(["non_gripper_robot_table_collision"]) == {
            "non_gripper_robot_table_collision": False
        }
        environment.runtime.send_action({"shoulder_pan.pos": 999.0})
        environment.runtime.send_action({"shoulder_pan.pos": 0.0})
        assert environment._mujoco_runtime.last_receipt is not None
        assert not any(environment._mujoco_runtime.last_receipt.clipped.values())

        _set_object_xy(environment._mujoco_runtime, "validation_cube", 0.90, 0.0)
        environment.runtime.get_observation()
        _set_object_xy(environment._mujoco_runtime, "validation_cube", 0.30, 0.0)
        environment.runtime.get_observation()
        final_snapshot = environment._mujoco_runtime.world_snapshot()
        assert final_snapshot["objects"]["validation_cube"]["position_m"][0] == pytest.approx(
            0.30
        )

        backend = environment._mujoco_runtime
        with backend._lock:
            joint_id = backend._joint_ids["shoulder_pan"]
            qpos_address = int(backend.model.jnt_qposadr[joint_id])
            original_qpos = float(backend.data.qpos[qpos_address])
            backend.data.qpos[qpos_address] = float(backend.model.jnt_range[joint_id][1]) + 0.05
            backend._mj.mj_forward(backend.model, backend.data)
        environment._sample_episode_evidence()
        with backend._lock:
            backend.data.qpos[qpos_address] = original_qpos
            original_velocity = float(backend.data.qvel[0])
            backend.data.qvel[0] = float("nan")
            backend._mj.mj_forward(backend.model, backend.data)
        environment._sample_episode_evidence()
        with backend._lock:
            backend.data.qvel[0] = original_velocity
            backend._mj.mj_forward(backend.model, backend.data)
            original_joint_qpos = {
                name: float(backend.data.qpos[int(backend.model.jnt_qposadr[joint_id])])
                for name, joint_id in backend._joint_ids.items()
            }
            collision_qpos = {
                "shoulder_pan": -1.101039402444556,
                "shoulder_lift": 1.4116882389873449,
                "elbow_flex": -0.8323245444252746,
                "wrist_flex": 1.5107048152129763,
                "wrist_roll": 2.6806255114534503,
                "gripper": 1.092453787784816,
            }
            for name, value in collision_qpos.items():
                joint_id = backend._joint_ids[name]
                backend.data.qpos[int(backend.model.jnt_qposadr[joint_id])] = value
            backend._mj.mj_forward(backend.model, backend.data)
        environment._sample_episode_evidence()
        with backend._lock:
            for name, value in original_joint_qpos.items():
                joint_id = backend._joint_ids[name]
                backend.data.qpos[int(backend.model.jnt_qposadr[joint_id])] = value
            backend._mj.mj_forward(backend.model, backend.data)
        environment._sample_episode_evidence()

        evidence = environment.forbidden(
            [
                "action_clipped",
                "object_outside_workspace",
                "actuator_or_joint_limit_exceeded",
                "simulation_nan_or_instability",
                "non_gripper_robot_table_collision",
            ]
        )
        diagnostics = environment.diagnostics()
    finally:
        environment.close()

    assert evidence == {
        "action_clipped": True,
        "object_outside_workspace": True,
        "actuator_or_joint_limit_exceeded": True,
        "simulation_nan_or_instability": True,
        "non_gripper_robot_table_collision": True,
    }
    clipping = next(
        item
        for item in diagnostics["action_clipping_details"]
        if item["joint_key"] == "shoulder_pan.pos"
    )
    assert clipping["event_count"] == 1
    assert clipping["first_event"]["requested_value"] == 999.0
    assert clipping["first_event"]["accepted_value"] == pytest.approx(
        clipping["allowed_actuator_target_range"]["high"]
    )
    assert clipping["maximum_clipping_event"][
        "requested_outside_actuator_range_by"
    ] > 800.0

    limit = next(
        item
        for item in diagnostics["joint_or_actuator_limit_violations"]
        if item["joint_key"] == "shoulder_pan.pos"
        and item["kind"] == "joint_position"
        and item["side"] == "high"
    )
    assert limit["limit"]["units"] == "degrees"
    assert limit["first_event"]["requested_value"] == 0.0
    assert limit["maximum_excess_event"]["observed_value"] > limit["limit"]["high"]
    assert limit["maximum_excess_event"]["excess"] == pytest.approx(
        math.degrees(0.05), rel=1e-6
    )

    cube_motion = diagnostics["object_motion_summaries"]["validation_cube"]
    assert cube_motion["initial_position_m"][0] == pytest.approx(0.30)
    assert cube_motion["final_position_m"][0] == pytest.approx(0.30)
    assert cube_motion["maximum_displacement_from_initial_m"] == pytest.approx(0.60)
    assert cube_motion["position_bounds_m"]["max"][0] == pytest.approx(0.90)
    assert cube_motion["significant_motion_count"] >= 2
    assert cube_motion["last_significant_motion_time_s"] is not None
    assert len(cube_motion["keyframes"]) <= 5


def test_diagnostics_preserve_transient_lift_even_when_object_returns_to_start() -> None:
    pytest.importorskip("mujoco")
    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    try:
        environment.reset(
            {
                "bodies": [
                    {
                        "id": "validation_cube",
                        "kind": "cube",
                        "position_m": [0.30, 0.0, 0.035],
                    }
                ]
            }
        )
        _set_object_position(
            environment._mujoco_runtime, "validation_cube", 0.30, 0.0, 0.12
        )
        environment.runtime.get_observation()
        _set_object_position(
            environment._mujoco_runtime, "validation_cube", 0.30, 0.0, 0.035
        )
        environment.runtime.get_observation()
        diagnostics = environment.diagnostics()
    finally:
        environment.close()

    assert diagnostics["trajectory_summary_scope"]["target_aware"] is False
    motion = diagnostics["object_motion_summaries"]["validation_cube"]
    assert motion["initial_position_m"] == pytest.approx([0.30, 0.0, 0.035])
    assert motion["final_position_m"] == pytest.approx([0.30, 0.0, 0.035])
    assert motion["maximum_height_m"] == pytest.approx(0.12)
    assert motion["maximum_lift_above_initial_m"] == pytest.approx(0.085)
    assert motion["maximum_displacement_from_initial_m"] == pytest.approx(0.085)
    assert motion["significant_motion_count"] >= 2
    assert {frame["kind"] for frame in motion["keyframes"]} >= {
        "initial",
        "maximum_height",
        "last_significant_motion",
        "final",
    }
    assert len(motion["keyframes"]) <= 5


def test_close_releases_video_and_facade_even_when_forced_final_frame_fails() -> None:
    pytest.importorskip("mujoco")

    class FailingVideo:
        closed = False

        def capture(self, *_: object, **__: object) -> None:
            raise RuntimeError("intentional final-frame failure")

        def close(self) -> None:
            self.closed = True

    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    environment.reset({"bodies": []})
    video = FailingVideo()
    environment._video = video

    with pytest.raises(FrameworkEvidenceError) as caught:
        environment.close()

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "intentional final-frame failure" in str(caught.value.__cause__)
    assert video.closed is True
    with pytest.raises(RuntimeError, match="facade is closed"):
        environment.runtime.get_observation()


def test_runtime_callback_capture_failure_is_framework_evidence_error() -> None:
    pytest.importorskip("mujoco")

    class FailingCallbackVideo:
        closed = False

        def capture(self, *_: object, force: bool = False, **__: object) -> None:
            if not force:
                raise RuntimeError("intentional callback capture failure")

        def close(self) -> None:
            self.closed = True

    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    environment.reset({"bodies": []})
    video = FailingCallbackVideo()
    environment._video = video

    with pytest.raises(FrameworkEvidenceError) as caught:
        environment.runtime.get_observation()

    assert "validation evidence sampling failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
    with pytest.raises(FrameworkEvidenceError):
        environment.close()
    assert video.closed is True
    with pytest.raises(RuntimeError, match="facade is closed"):
        environment.runtime.get_observation()


def test_renderer_initialization_failure_is_wrapped_and_releases_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeWriter:
        released = False

        def isOpened(self) -> bool:
            return True

        def release(self) -> None:
            self.released = True

    writer = FakeWriter()
    fake_cv2 = SimpleNamespace(
        VideoWriter=lambda *_args, **_kwargs: writer,
        VideoWriter_fourcc=lambda *_args: 0,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    class FailingMujoco:
        @staticmethod
        def Renderer(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("intentional renderer initialization failure")

    runtime = SimpleNamespace(
        _mj=FailingMujoco(),
        model=object(),
        data=SimpleNamespace(time=0.0),
        _lock=RLock(),
    )
    recorder = _MujocoEpisodeVideoRecorder(
        tmp_path / "renderer_failure.mp4",
        common_reset={"table": {"center_m": [0.35, 0.0, 0.01]}},
    )

    with pytest.raises(FrameworkEvidenceError, match="renderer initialization failed"):
        recorder.start(runtime)

    assert writer.released is True
    assert not recorder.metadata_path.exists()


def test_background_observer_failure_surfaces_as_framework_evidence() -> None:
    pytest.importorskip("mujoco")
    environment = MujocoTabletopValidationEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        reset_settle_s=0.0,
        measurement_settle_s=0.0,
    )
    environment.reset({"bodies": []})
    environment._mujoco_runtime._observer_error = RuntimeError(
        "intentional background observer failure"
    )

    with pytest.raises(FrameworkEvidenceError) as caught:
        environment.close()

    assert isinstance(caught.value.__cause__, RuntimeError)
    with pytest.raises(RuntimeError, match="facade is closed"):
        environment.runtime.get_observation()


@pytest.mark.skipif(
    os.environ.get("SOARM101_RUN_MUJOCO_VIDEO_TESTS") != "1",
    reason="native mujoco.Renderer requires the framework graphics-enabled launcher",
)
def test_framework_only_native_mujoco_mp4_is_separate_per_case_round_and_demo_task(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    cv2 = pytest.importorskip("cv2")
    validation_video = tmp_path / "validation/case_g1/repair_02.mp4"
    validation = MujocoTabletopValidationEnvironment(
        {"framework_metadata": {"simulation_video_path": str(validation_video)}},
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=True,
        reset_settle_s=0.10,
        measurement_settle_s=0.10,
        video_fps=20.0,
        video_width=320,
        video_height=240,
    )
    try:
        validation.reset(
            {
                "bodies": [
                    {
                        "id": "validation_cube",
                        "kind": "cube",
                        "position_m": [0.30, 0.0, 0.035],
                    }
                ]
            }
        )
        validation.runtime.send_action({"shoulder_pan.pos": 3.0})
        validation.measure({"finite": True})
    finally:
        validation.close()

    instance = _state("soarm101_p0_push_cube_to_region.json")
    demo_video = tmp_path / f"demo/{instance['task_id']}.mp4"
    demo = MujocoTabletopDemoEnvironment(
        {"framework_metadata": {"video_path": str(demo_video)}},
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
        video_fps=20.0,
        video_width=320,
        video_height=240,
    )
    try:
        demo.reset(instance)
        demo.score({"task_id": instance["task_id"]}, instance)
    finally:
        demo.close()

    for path in (validation_video, demo_video):
        assert path.is_file() and path.stat().st_size > 0
        metadata = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        assert metadata["renderer_kind"] == "mujoco.Renderer"
        assert metadata["codec"] == "mp4v"
        assert metadata["video_path"] == path.name
        assert not Path(metadata["video_path"]).is_absolute()
        assert metadata["fps"] == 20.0
        assert metadata["frames"] >= 2
        capture = cv2.VideoCapture(str(path))
        try:
            assert capture.isOpened()
            assert capture.get(cv2.CAP_PROP_FRAME_COUNT) == pytest.approx(
                metadata["frames"]
            )
            ok, frame = capture.read()
            assert ok is True
            assert frame.shape == (240, 320, 3)
        finally:
            capture.release()


def test_actual_demo_initial_state_fails_then_manual_target_and_boundary_are_scored(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    instance = _state("soarm101_p0_push_cube_to_region.json")
    task: Mapping[str, Any] = {"task_id": instance["task_id"]}
    environment = MujocoTabletopDemoEnvironment(
        {"framework_metadata": {"simulation_trace_path": str(tmp_path / "demo_sim.jsonl")}},
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        initial = environment.score(task, instance)
        assert initial["runtime_kind"] == "actual_mujoco"
        assert initial["reachability_claim"] == "not_evaluated"
        assert initial["passed"] is False

        target = instance["agent_input"]["targets"]["push_region"]["center_m"]
        environment.reset(instance)
        _set_object_xy(environment._mujoco_runtime, "red_cube", target[0], target[1])
        at_target = environment.score(task, instance)
        assert at_target["passed"] is True

        environment.reset(instance)
        _set_object_xy(
            environment._mujoco_runtime, "red_cube", target[0] + 0.025, target[1]
        )
        at_boundary = environment.score(task, instance)
        assert at_boundary["passed"] is True

        environment.reset(instance)
        _set_object_xy(
            environment._mujoco_runtime, "red_cube", target[0] + 0.0252, target[1]
        )
        outside_boundary = environment.score(task, instance)
        assert outside_boundary["passed"] is False
        assert outside_boundary["measurements"]["object_xy_distance_to_target_center_m"] > 0.025
    finally:
        environment.close()


def test_actual_push_oracle_rejects_transient_pick_and_place_lift() -> None:
    pytest.importorskip("mujoco")
    instance = _state("soarm101_p0_push_cube_to_region.json")
    task: Mapping[str, Any] = {"task_id": instance["task_id"]}
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        initial_z = environment._initial_snapshot["objects"]["red_cube"][
            "position_m"
        ][2]
        target = instance["agent_input"]["targets"]["push_region"]["center_m"]
        _set_object_position(
            environment._mujoco_runtime,
            "red_cube",
            target[0],
            target[1],
            initial_z + 0.05,
        )
        environment._sample_episode_evidence()
        _set_object_position(
            environment._mujoco_runtime,
            "red_cube",
            target[0],
            target[1],
            initial_z,
        )
        environment._sample_episode_evidence()
        result = environment.score(task, instance)
    finally:
        environment.close()

    assert result["passed"] is False
    assert result["measurements"][
        "maximum_object_lift_above_initial_m"
    ] == pytest.approx(0.05, abs=1e-6)
    assert result["measurements"]["maximum_lift_tolerance_m"] == pytest.approx(
        0.010
    )


def test_actual_push_cylinder_oracle_rejects_transient_pick_and_place_lift() -> None:
    pytest.importorskip("mujoco")
    instance = _state("soarm101_p0_push_cylinder_lateral.json")
    task: Mapping[str, Any] = {"task_id": instance["task_id"]}
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        initial_z = environment._initial_snapshot["objects"]["orange_cylinder"][
            "position_m"
        ][2]
        target = instance["agent_input"]["targets"]["push_region"]["center_m"]
        _set_object_position(
            environment._mujoco_runtime,
            "orange_cylinder",
            target[0],
            target[1],
            initial_z + 0.05,
        )
        environment._sample_episode_evidence()
        _set_object_position(
            environment._mujoco_runtime,
            "orange_cylinder",
            target[0],
            target[1],
            initial_z,
        )
        environment._sample_episode_evidence()
        result = environment.score(task, instance)
    finally:
        environment.close()

    assert result["passed"] is False
    assert result["measurements"][
        "maximum_object_lift_above_initial_m"
    ] == pytest.approx(0.05, abs=1e-6)
    assert result["measurements"]["maximum_lift_tolerance_m"] == pytest.approx(
        0.010
    )


@pytest.mark.parametrize(
    ("state_name", "object_id"),
    (
        ("soarm101_p0_push_cube_to_region.json", "red_cube"),
        ("soarm101_p0_push_cylinder_lateral.json", "orange_cylinder"),
    ),
)
def test_actual_push_lift_threshold_passes_at_one_centimeter_and_fails_above_it(
    state_name: str, object_id: str
) -> None:
    pytest.importorskip("mujoco")
    instance = _state(state_name)
    task: Mapping[str, Any] = {"task_id": instance["task_id"]}
    target = instance["agent_input"]["targets"]["push_region"]["center_m"]
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        initial_z = environment._initial_snapshot["objects"][object_id][
            "position_m"
        ][2]
        _set_object_position(
            environment._mujoco_runtime,
            object_id,
            target[0],
            target[1],
            initial_z + 0.010,
        )
        environment._sample_episode_evidence()
        _set_object_position(
            environment._mujoco_runtime,
            object_id,
            target[0],
            target[1],
            initial_z,
        )
        environment._sample_episode_evidence()
        at_boundary = environment.score(task, instance)

        environment.reset(instance)
        initial_z = environment._initial_snapshot["objects"][object_id][
            "position_m"
        ][2]
        _set_object_position(
            environment._mujoco_runtime,
            object_id,
            target[0],
            target[1],
            initial_z + 0.01001,
        )
        environment._sample_episode_evidence()
        _set_object_position(
            environment._mujoco_runtime,
            object_id,
            target[0],
            target[1],
            initial_z,
        )
        environment._sample_episode_evidence()
        above_boundary = environment.score(task, instance)
    finally:
        environment.close()

    assert at_boundary["passed"] is True
    assert at_boundary["measurements"][
        "maximum_object_lift_above_initial_m"
    ] == pytest.approx(0.010, abs=1e-9)
    assert above_boundary["passed"] is False
    assert above_boundary["measurements"][
        "maximum_object_lift_above_initial_m"
    ] == pytest.approx(0.01001, abs=1e-9)


@pytest.mark.parametrize(
    ("state_name", "object_id"),
    (
        ("soarm101_p0_push_cube_to_region.json", "red_cube"),
        ("soarm101_p0_push_cylinder_lateral.json", "orange_cylinder"),
    ),
)
def test_actual_push_manual_horizontal_slide_without_lift_passes(
    state_name: str, object_id: str
) -> None:
    pytest.importorskip("mujoco")
    instance = _state(state_name)
    task: Mapping[str, Any] = {"task_id": instance["task_id"]}
    target = instance["agent_input"]["targets"]["push_region"]["center_m"]
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        initial_position = environment._initial_snapshot["objects"][object_id][
            "position_m"
        ]
        # This trusted protocol probe is not an agent control path.  It samples
        # a horizontal trajectory from actual MjData while keeping the object's
        # center height fixed, proving that the anti-lift rule still accepts a
        # genuine push-shaped path.
        for fraction in (0.2, 0.4, 0.6, 0.8, 1.0):
            _set_object_position(
                environment._mujoco_runtime,
                object_id,
                initial_position[0]
                + fraction * (target[0] - initial_position[0]),
                initial_position[1]
                + fraction * (target[1] - initial_position[1]),
                initial_position[2],
            )
            environment._sample_episode_evidence()
        result = environment.score(task, instance)
    finally:
        environment.close()

    assert result["runtime_kind"] == "actual_mujoco"
    assert result["passed"] is True
    assert result["measurements"][
        "maximum_object_lift_above_initial_m"
    ] == pytest.approx(0.0, abs=1e-9)


def test_actual_demo_does_not_infer_unobserved_multi_object_event_order() -> None:
    pytest.importorskip("mujoco")
    instance = _state("soarm101_p0_place_two_objects_in_tray.json")
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        center = instance["agent_input"]["receptacles"]["tray"]["center_m"]
        # Both terminal poses are valid, but both changes occur between the
        # same two private trace samples, so order cannot be claimed.
        _set_object_xy(
            environment._mujoco_runtime, "red_cube", center[0] - 0.020, center[1]
        )
        _set_object_xy(
            environment._mujoco_runtime,
            "blue_cylinder",
            center[0] + 0.020,
            center[1],
        )
        result = environment.score({"task_id": instance["task_id"]}, instance)
    finally:
        environment.close()

    assert result["passed"] is False
    assert result["measurements"]["contained"] == {
        "red_cube": True,
        "blue_cylinder": True,
    }
    assert result["measurements"]["event_order_proven"] is False
    assert "multiple stable placement events" in result["measurements"][
        "event_order_failure_reason"
    ]


def test_actual_demo_orders_stable_placements_and_ignores_airborne_crossing_and_jitter() -> None:
    pytest.importorskip("mujoco")
    instance = _state("soarm101_p0_place_two_objects_in_tray.json")
    environment = MujocoTabletopDemoEnvironment(
        model_path=MODEL,
        common_reset=COMMON,
        auto_step=False,
    )
    try:
        environment.reset(instance)
        center = instance["agent_input"]["receptacles"]["tray"]["center_m"]
        initial = environment._initial_snapshot["objects"]
        red_support_z = float(initial["red_cube"]["position_m"][2])
        blue_support_z = float(initial["blue_cylinder"]["position_m"][2])

        # Passing through the tray's XY footprint while airborne and released
        # is not a placement-success event because the object is unsupported.
        _set_object_position(
            environment._mujoco_runtime,
            "blue_cylinder",
            center[0],
            center[1] + 0.018,
            0.12,
        )
        environment.runtime.get_observation()
        _set_object_position(
            environment._mujoco_runtime,
            "blue_cylinder",
            0.32,
            0.08,
            blue_support_z,
        )
        environment.runtime.get_observation()

        # The red placement is stable for longer than the 0.10-second event
        # window and is therefore latched as the first event.
        _set_object_position(
            environment._mujoco_runtime,
            "red_cube",
            center[0],
            center[1] - 0.018,
            red_support_z,
        )
        environment.runtime.get_observation()
        _advance_with_sampling(
            environment._mujoco_runtime,
            0.12,
            environment._observe_simulation_state,
        )

        # A post-latch one-sample containment jitter does not erase the stable
        # event; terminal containment is still checked independently by score.
        _set_object_position(
            environment._mujoco_runtime,
            "red_cube",
            center[0] + 0.040,
            center[1] - 0.018,
            red_support_z,
        )
        environment.runtime.get_observation()
        _set_object_position(
            environment._mujoco_runtime,
            "red_cube",
            center[0],
            center[1] - 0.018,
            red_support_z,
        )
        environment.runtime.get_observation()

        _set_object_position(
            environment._mujoco_runtime,
            "blue_cylinder",
            center[0],
            center[1] + 0.018,
            blue_support_z,
        )
        environment.runtime.get_observation()
        _advance_with_sampling(
            environment._mujoco_runtime,
            0.12,
            environment._observe_simulation_state,
        )
        result = environment.score({"task_id": instance["task_id"]}, instance)
    finally:
        environment.close()

    assert result["passed"] is True
    assert result["measurements"]["observed_event_order"] == [
        "red_cube",
        "blue_cylinder",
    ]
    assert result["measurements"]["event_order_proven"] is True
    assert result["measurements"]["event_order_failure_reason"] is None


def test_generated_g1_runs_as_a_direct_function_against_actual_mujoco(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mujoco")
    manifest = json.loads((PACKAGE / "package_manifest.json").read_text(encoding="utf-8"))
    case = {
        "case_id": "actual_mujoco_g1_move_joints",
        "capability_id": "G1.move_joints",
        "module": "g1",
        "function_name": "move_joints",
        "initial_state": {"qpos": {}, "bodies": []},
        "call_arguments": {
            "targets": {"shoulder_pan.pos": 5.0, "shoulder_lift.pos": -40.0},
            "tolerance": 1.0,
            "max_steps": 100,
        },
        "target_measurements": {
            "joint_positions": {"shoulder_pan.pos": 5.0, "shoulder_lift.pos": -40.0}
        },
        "tolerances": {
            "joint_positions": {"shoulder_pan.pos": 1.0, "shoulder_lift.pos": 1.0}
        },
        "forbidden_conditions": ["action_clipped", "simulation_nan_or_instability"],
        "timeout_s": 3.0,
        "test_entrypoint": "soarm_demo.direct_validation:execute_case",
        "reference_provenance": ["pinned-so101-mjcf"],
        "framework_metadata": {"trace_path": str(tmp_path / "g1_actual_mujoco.jsonl")},
    }

    result = execute_case(
        package_root=PACKAGE,
        package_manifest=manifest,
        case=case,
        environment_factory=lambda received: MujocoTabletopValidationEnvironment(
            received,
            model_path=MODEL,
            common_reset=COMMON,
            auto_step=False,
            reset_settle_s=0.05,
            measurement_settle_s=0.10,
        ),
    )

    assert result.passed is True
    assert result.timed_out is False
    assert result.exception_type is None
    assert result.result["status"] == "succeeded"
    assert result.forbidden_evidence == {
        "action_clipped": False,
        "simulation_nan_or_instability": False,
    }
    assert (tmp_path / "g1_actual_mujoco.jsonl").is_file()
