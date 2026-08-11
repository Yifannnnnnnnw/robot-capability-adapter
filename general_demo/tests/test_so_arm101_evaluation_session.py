from __future__ import annotations

from pathlib import Path
from typing import Any

from autoadapter2.demo import EvaluationRobotSession, ValidationEvidence
from autoadapter2.evaluation import FrozenVideoProfile, RGBFrame
from autoadapter2.integrations.so_arm101.session import (
    SOArm101EvaluationRobotSession,
)
from autoadapter2.validation import HarnessInvocation


ROOT = Path(__file__).resolve().parents[1]
SCENE_CONFIG = ROOT / "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene_config.json"
TASKS = ROOT / "libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/task_instances_private.json"


class _Backend:
    timestep = 0.1

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.time = 0.0
        self.qpos = [0.0, 0.0]
        self.qvel = [0.0, 0.0]
        self.ctrl = [0.0, 0.0]
        self.reset_count = 0
        self.closed = False

    def reset(self) -> None:
        self.reset_count += 1
        self.time = 0.0
        self.qpos[:] = [0.0, 0.0]
        self.qvel[:] = [0.0, 0.0]
        self.ctrl[:] = [0.0, 0.0]

    def set_goal_ticks(self, _values: dict[int, int]) -> None:
        raise AssertionError("unit backend must not receive direct tick goals")

    def present_ticks(self, _motor_ids: object) -> dict[int, int]:
        return {}

    def step(self, seconds: float) -> None:
        self.time += seconds
        self.qpos[0] += self.ctrl[0] * seconds
        self.qvel[0] = self.ctrl[0]

    def state(self) -> dict[str, Any]:
        return {
            "qpos": list(self.qpos),
            "qvel": list(self.qvel),
            "ctrl": list(self.ctrl),
            "time": self.time,
        }

    def close(self) -> None:
        self.order.append("backend.close")
        self.closed = True


class _Translation:
    def __init__(self, backend: _Backend, order: list[str]) -> None:
        self.backend = backend
        self.order = order
        self.accepted_goal_writes = 0

    def install(self) -> str:
        self.order.append("translation.install")
        return "/dev/pts/test-so101"

    def reset(self) -> None:
        self.order.append("translation.reset")
        self.backend.reset()

    def step(self, seconds: float) -> None:
        self.backend.step(seconds)

    def wait_for_goal_writes(self, minimum_count: int, _timeout_s: float) -> bool:
        return self.accepted_goal_writes >= minimum_count

    def health(self) -> dict[str, int]:
        return {"accepted_goal_writes": self.accepted_goal_writes}

    def close(self) -> None:
        self.order.append("translation.close")


class _Follower:
    def __init__(self, translation: _Translation, order: list[str]) -> None:
        self.translation = translation
        self.order = order
        self.connected = False

    def connect(self, *, calibrate: bool = False) -> None:
        assert calibrate is False
        self.order.append("follower.connect")
        self.connected = True

    def disconnect(self) -> None:
        self.order.append("follower.disconnect")
        self.connected = False

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.translation.backend.ctrl[0] = float(next(iter(action.values())))
        self.translation.accepted_goal_writes += 1
        return dict(action)

    def get_observation(self) -> dict[str, float]:
        return {"shoulder_pan.pos": self.translation.backend.qpos[0]}


class _Candidate:
    def __init__(self, *, send_sdk_command: bool) -> None:
        self.send_sdk_command = send_sdk_command

    def _invoke(self, _capability_id: str, _arguments: dict[str, Any], sdk: _Follower) -> dict[str, bool]:
        if self.send_sdk_command:
            sdk.send_action({"shoulder_pan.pos": 1.0})
        return {"accepted": True}


class _Capture:
    def __init__(self, profile: FrozenVideoProfile, render_rgb: object, simulation_time: object) -> None:
        assert profile.width == 2 and profile.height == 2
        assert callable(render_rgb)
        assert callable(simulation_time)
        self.frames: list[str] = []

    def start(self) -> None:
        self.frames.append("start")

    def on_step(self) -> None:
        self.frames.append("step")

    def stop(self) -> tuple[RGBFrame, ...]:
        self.frames.append("stop")
        return (
            RGBFrame(0.0, 2, 2, b"\x00" * 12),
            RGBFrame(0.1, 2, 2, b"\x01" * 12),
        )


def _profile() -> FrozenVideoProfile:
    return FrozenVideoProfile(
        profile_id="test-profile",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-task-scene",
        fps=10,
        width=2,
        height=2,
        container="matroska",
        codec="ffv1",
    )


def _truth(session: SOArm101EvaluationRobotSession) -> dict[str, Any]:
    time_s = session.simulation_time_s
    return {
        "time_s": time_s,
        "tip_position_m": [0.30, -0.04, 0.18],
        "tip_position_error_m": 0.01,
        "tip_speed_m_s": 0.0,
        "cube_center_m": [0.33, 0.04, 0.037],
        "cube_center_planar_goal_error_m": 0.01,
        "cube_linear_speed_m_s": 0.0,
        "cube_angular_speed_rad_s": 0.0,
        "cube_height_increase_m": 0.025,
        "gripper_relative_cube_slip_m": 0.001,
        "cube_held": True,
        "cube_table_supported": True,
        "intended_tip_face_contact": True,
        "specified_button_displacement_m": 0.004,
        "specified_button_active": True,
        "other_button_activation_count": 0,
        "other_object_contact_count": 0,
        "finite_state": True,
        "safety_violation": False,
    }


def _invocation(metric: str = "tip_position_error_m") -> HarnessInvocation:
    return HarnessInvocation(
        capability_id="public-effect",
        case_id="case-1",
        inputs={},
        initial_state={"task_id": "T01"},
        repetition=1,
        measurement={"measurement_id": "m-1", "entity": "gripper", "unit": "m", "frame": "robot_base"},
        metric=metric,
        threshold={"comparator": "<=", "value": 0.02},
        dwell_s=0.1,
        timeout_s=0.2,
        aggregation="ALL",
        guard_ids=("finite-physical-state",),
        run_snapshot_hash="sha256:" + "0" * 64,
        candidate_source_hash="sha256:" + "1" * 64,
        suite_hash="sha256:" + "2" * 64,
        execution_attempt=1,
    )


def _session(order: list[str]) -> SOArm101EvaluationRobotSession:
    backend = _Backend(order)
    holder: dict[str, Any] = {"backend": backend}

    def backend_factory(_path: Path, **_kwargs: Any) -> _Backend:
        return backend

    def translation_factory(value: _Backend) -> _Translation:
        translation = _Translation(value, order)
        holder["translation"] = translation
        return translation

    def follower_factory(_port: str, _calibration: Path) -> _Follower:
        follower = _Follower(holder["translation"], order)
        holder["follower"] = follower
        return follower

    session = SOArm101EvaluationRobotSession(
        "not-used-by-injected-backend.xml",
        scene_config_path=SCENE_CONFIG,
        task_instances_path=TASKS,
        video_profile=_profile(),
        backend_factory=backend_factory,
        translation_factory=translation_factory,
        follower_factory=follower_factory,
        frame_capture_factory=_Capture,
        truth_provider=_truth,
    )
    session._test_holder = holder  # type: ignore[attr-defined]
    return session


def test_protocol_reset_truth_and_route_require_real_session_traffic() -> None:
    order: list[str] = []
    session = _session(order)
    assert isinstance(session, EvaluationRobotSession)
    assert session.evidence_scope == "TEST_FIXTURE_ONLY"
    assert not hasattr(session, "backend")
    assert not hasattr(session, "translation")

    session.reset(phase="VALIDATION_B", execution_id="validation-1", initial_state={"task_id": "T01"})
    without_traffic = session.validation_evidence(_invocation())
    assert isinstance(without_traffic, ValidationEvidence)
    assert without_traffic.sdk_route_verified is False
    assert without_traffic.guard_results["sdk-route-verified"] is False

    session.reset(phase="VALIDATION_B", execution_id="validation-2", initial_state={"task_id": "T01"})
    before = session._test_holder["backend"].qpos[:]  # type: ignore[attr-defined]
    result = session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
    after = session._test_holder["backend"].qpos[:]  # type: ignore[attr-defined]
    assert result == {"accepted": True}
    assert after != before, "physics progress must follow the SDK/PTY command"
    with_traffic = session.validation_evidence(_invocation())
    assert with_traffic.sdk_route_verified is True
    assert with_traffic.guard_results["physics-progress-observed"] is True
    session.close()


def test_demo_evidence_is_private_and_cleanup_is_follower_then_pty_then_backend() -> None:
    order: list[str] = []
    session = _session(order)
    session.reset(phase="DEMO", execution_id="demo-T20-1", initial_state={"task_id": "T20"})
    session.start_external_recording(phase="DEMO", execution_id="demo-T20-1")
    session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
    frames = session.stop_external_recording()
    evidence = session.demo_evidence("T20")
    assert len(frames) == 2
    assert set(("samples", "terminal_metrics", "event_metrics", "guard_results")).issubset(evidence)
    assert evidence["measurements"]["specified_button_displacement_m"] == 0.004
    assert evidence["guard_results"] == {
        "trusted-external-verdict": True,
        "so-safety-gate": True,
        "finite-physical-state": True,
    }
    session.close()
    assert order.index("follower.disconnect") < order.index("translation.close") < order.index("backend.close")
    assert session.evidence_scope == "UNAVAILABLE"
