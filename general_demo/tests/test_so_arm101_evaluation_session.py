from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2.demo import (
    EvaluationRobotSession,
    ValidationEvidence,
    evaluate_fixed_demo_criterion,
)
from autoadapter2.evaluation import FrozenVideoProfile, RGBFrame
from autoadapter2.foundation.canonical import canonical_bytes
from autoadapter2.foundation.hashing import content_hash
from autoadapter2.integrations.so_arm101.session import (
    SOArm101SessionError,
    SOArm101EvaluationRobotSession,
    _load_frame_capture_factory,
)
from autoadapter2.integrations.so_arm101.feetech_protocol import MOTOR_NAMES
from autoadapter2.integrations.so_arm101 import session as so_session
from autoadapter2.validation import HarnessInvocation


ROOT = Path(__file__).resolve().parents[1]
SCENE_CONFIG = ROOT / "libraries/morphology/so-arm101/1.0.0/private_demo_scene/scene_config.json"
TASKS = ROOT / "libraries/tasks/so-arm101-follower-stock-gripper/1.0.0/task_instances_private.json"
PRIVATE_CRITERIA = {
    task["task_id"]: task["criterion"]
    for task in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
}
DEMO_DURATIONS = {"T01": 0.5, "T02": 0.3, "T03": 1.0, "T08": 1.0, "T20": 0.25}
DECISIVE_FAILURES = {
    "T01": ("tip_position_error_m", 0.021),
    "T02": ("target_object_displacement_m", 0.011),
    "T03": ("cube_table_supported", False),
    "T08": ("cube_held", False),
    "T20": ("other_button_activation_count", 1),
}


class _Backend:
    timestep = 0.1

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.time = 0.0
        self.qpos = [0.0] * len(MOTOR_NAMES)
        self.qvel = [0.0] * len(MOTOR_NAMES)
        self.ctrl = [0.0] * len(MOTOR_NAMES)
        self.reset_count = 0
        self.closed = False

    def reset(self) -> None:
        self.reset_count += 1
        self.time = 0.0
        self.qpos[:] = [0.0] * len(MOTOR_NAMES)
        self.qvel[:] = [0.0] * len(MOTOR_NAMES)
        self.ctrl[:] = [0.0] * len(MOTOR_NAMES)

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
            "named_qpos": dict(zip(MOTOR_NAMES, self.qpos, strict=True)),
            "named_ctrl": dict(zip(MOTOR_NAMES, self.ctrl, strict=True)),
            "gripper_control_range": [0.0, 1.0],
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
    def __init__(
        self,
        translation: _Translation,
        order: list[str],
        *,
        readback_mismatch: bool = False,
    ) -> None:
        self.translation = translation
        self.order = order
        self.readback_mismatch = readback_mismatch
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
        result: dict[str, float] = {}
        for index, name in enumerate(MOTOR_NAMES):
            value = (
                math.degrees(self.translation.backend.qpos[index])
                if name != "gripper"
                else self.translation.backend.qpos[index] * 100.0
            )
            if self.readback_mismatch and name == "shoulder_pan":
                value += 45.0
            result[f"{name}.pos"] = value
        return result


class _Candidate:
    def __init__(self, *, send_sdk_command: bool) -> None:
        self.send_sdk_command = send_sdk_command

    def _invoke(self, _capability_id: str, _arguments: dict[str, Any], sdk: _Follower) -> dict[str, bool]:
        if self.send_sdk_command:
            sdk.send_action({"shoulder_pan.pos": 1.0})
        return {"accepted": True}


class _Capture:
    def __init__(self, profile: FrozenVideoProfile, render_rgb: object) -> None:
        assert profile.width == 2 and profile.height == 2
        assert callable(render_rgb)
        self.frames: list[str] = []

    def start(self, _simulation_time_s: float) -> None:
        self.frames.append("start")

    def on_step(self, _simulation_time_s: float) -> None:
        self.frames.append("step")

    def stop(self, _simulation_time_s: float) -> tuple[RGBFrame, ...]:
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


def _session(
    order: list[str], *, readback_mismatch: bool = False
) -> SOArm101EvaluationRobotSession:
    backend = _Backend(order)
    holder: dict[str, Any] = {"backend": backend}

    def backend_factory(_path: Path, **_kwargs: Any) -> _Backend:
        return backend

    def translation_factory(value: _Backend) -> _Translation:
        translation = _Translation(value, order)
        holder["translation"] = translation
        return translation

    def follower_factory(_port: str, _calibration: Path) -> _Follower:
        follower = _Follower(
            holder["translation"],
            order,
            readback_mismatch=readback_mismatch,
        )
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


def test_default_capture_factory_uses_shared_time_indexed_slots() -> None:
    profile = FrozenVideoProfile(
        profile_id="shared-capture-test",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-task-scene",
        fps=30,
        width=2,
        height=2,
        container="matroska",
        codec="ffv1",
    )
    factory = _load_frame_capture_factory()
    capture = factory(profile, lambda: b"\x00" * 12)
    capture.start(0.0)
    for index in range(1, 21):
        capture.on_step(index * 0.005)
    frames = capture.stop(0.1)
    timestamps = [round(frame.simulation_time_s, 12) for frame in frames]
    assert timestamps == [0.0, round(1 / 30, 12), round(2 / 30, 12), 0.1]
    assert len(timestamps) == len(set(timestamps))


def test_sample_truth_uses_mujoco_object_velocity_not_site_xvelp() -> None:
    object_types = SimpleNamespace(
        mjOBJ_SITE="site",
        mjOBJ_BODY="body",
        mjOBJ_JOINT="joint",
        mjOBJ_GEOM="geom",
    )

    class _MuJoCo:
        mjtObj = object_types

        @staticmethod
        def mj_name2id(_model: object, object_type: str, name: str) -> int:
            return {
                ("site", "gripperframe"): 0,
                ("body", "demo_cube"): 1,
                ("joint", "demo_button_slide"): 2,
                ("joint", "demo_cube_free"): 3,
            }.get((object_type, name), -1)

        @staticmethod
        def mj_objectVelocity(
            _model: object,
            _data: object,
            object_type: str,
            object_id: int,
            result: list[float],
            flg_local: int,
        ) -> None:
            assert object_type == "site"
            assert object_id == 0
            assert flg_local == 0
            result[:] = [1.0, 2.0, 3.0, 0.1, 0.2, 0.3]

    model = SimpleNamespace(
        jnt_dofadr=[0, 0, 0, 0],
        jnt_qposadr=[0, 0, 0, 0],
        njnt=0,
        jnt_limited=[],
    )
    # Deliberately omit site_xvelp: a regression to the removed non-existent
    # MjData field must fail this test with AttributeError.
    data = SimpleNamespace(
        site_xpos=[[0.30, -0.04, 0.18]],
        xpos=[[0.0, 0.0, 0.0], [0.33, 0.04, 0.037]],
        qpos=[0.0],
        # MuJoCo free-joint qvel is [linear xyz, angular xyz].  Keep the
        # magnitudes asymmetric so swapping the slices is observable.
        qvel=[1.0, 2.0, 2.0, 0.1, 0.2, 0.2],
        ctrl=[0.0],
        ncon=0,
        contact=[],
        time=0.0,
    )
    session = object.__new__(SOArm101EvaluationRobotSession)
    session._truth_provider = None
    session._backend = SimpleNamespace(_mj=_MuJoCo(), model=model, data=data)
    session._simulation_time_s = 0.0
    session._scene_config = {
        "table_surface_z_m": 0.02,
        "cube_half_height_m": 0.017,
        "workspace_bounds_m": {
            "x": [0.0, 1.0],
            "y": [-1.0, 1.0],
            "z": [0.0, 1.0],
        },
    }
    session._current_task = {
        "target": {
            "tip_position_m": [0.30, -0.04, 0.18],
            "cube_goal_center_m": [0.33, 0.04],
        }
    }
    session._event_state = {}
    session._episode_samples = []
    session._last_truth = None

    truth = session._sample_truth()  # type: ignore[attr-defined]
    assert truth["tip_speed_m_s"] == pytest.approx((0.1**2 + 0.2**2 + 0.3**2) ** 0.5)
    assert truth["cube_linear_speed_m_s"] == pytest.approx(3.0)
    assert truth["cube_angular_speed_rad_s"] == pytest.approx(0.3)


def test_validation_evidence_resolves_t02_non_target_contact_metric() -> None:
    session = _session([])
    try:
        session.reset(
            phase="VALIDATION_B",
            execution_id="validation-t02-contact-metric",
            initial_state={"task_id": "T02"},
        )
        session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
        evidence = session.validation_evidence(_invocation("non_target_contact_count"))
        assert evidence.samples
        assert all(sample.value == 0.0 for sample in evidence.samples)
    finally:
        session.close()


def _write_json_artifact(path: Path, value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _factory_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    root = tmp_path / "run-pack"
    manifest_path = root / "general_demo/integrations/so-arm101/integration_manifest.json"
    morphology_path = root / "general_demo/libraries/morphology/so-arm101/1.0.0/record.json"
    translation_path = root / "general_demo/integrations/so-arm101/translation.json"
    model_path = tmp_path / "official/SO-ARM100/Simulation/SO101/so101_new_calib.xml"
    model_bytes = b"<mujoco model='factory-test'/ >"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(model_bytes)

    morphology = json.loads(
        (ROOT / "libraries/morphology/so-arm101/1.0.0/record.json").read_text(encoding="utf-8")
    )
    morphology["mujoco"]["source_sha256"] = hashlib.sha256(model_bytes).hexdigest()
    morphology_hash = _write_json_artifact(morphology_path, morphology)
    translation = json.loads(
        (ROOT / "integrations/so-arm101/translation.json").read_text(encoding="utf-8")
    )
    translation_hash = _write_json_artifact(translation_path, translation)
    manifest = json.loads(
        (ROOT / "integrations/so-arm101/integration_manifest.json").read_text(encoding="utf-8")
    )
    manifest["morphology_ref"]["sha256"] = morphology_hash
    manifest["translation_ref"]["sha256"] = translation_hash
    _write_json_artifact(manifest_path, manifest)
    return manifest_path, model_path, root / "runs/run-1", manifest


def test_factory_verifies_records_and_derives_direction_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path, model_path, run_path, _manifest = _factory_fixture(tmp_path)
    constructed: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _Session:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append((args, kwargs))

    monkeypatch.setattr(so_session, "SOArm101EvaluationRobotSession", _Session)
    monkeypatch.setenv("AUTOADAPTER_SO101_MODEL", str(model_path))
    monkeypatch.delenv("AUTOADAPTER_GRIPPER_DIRECTION", raising=False)

    result = so_session.create_so_arm101_evaluation_session(
        "so-arm101", manifest_path, run_path
    )
    assert result is not None
    assert len(constructed) == 1
    assert constructed[0][0] == (model_path.resolve(),)
    assert constructed[0][1]["gripper_tick_increases_qpos"] is True


@pytest.mark.parametrize(
    "tamper",
    [
        "model-bytes",
        "model-path",
        "morphology-record",
        "morphology-hash",
        "translation-record",
        "translation-hash",
        "direction",
    ],
)
def test_factory_rejects_unverified_model_records_and_direction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    manifest_path, model_path, run_path, manifest = _factory_fixture(tmp_path)
    constructed: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _Session:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append((args, kwargs))

    monkeypatch.setattr(so_session, "SOArm101EvaluationRobotSession", _Session)
    monkeypatch.setenv("AUTOADAPTER_SO101_MODEL", str(model_path))
    monkeypatch.delenv("AUTOADAPTER_GRIPPER_DIRECTION", raising=False)

    if tamper == "model-bytes":
        model_path.write_bytes(b"wrong-model")
    elif tamper == "model-path":
        wrong_model = tmp_path / "wrong-model.xml"
        wrong_model.write_bytes(model_path.read_bytes())
        monkeypatch.setenv("AUTOADAPTER_SO101_MODEL", str(wrong_model))
    elif tamper == "morphology-record":
        morphology_path = manifest_path.parents[3] / manifest["morphology_ref"]["path"]
        morphology = json.loads(morphology_path.read_text(encoding="utf-8"))
        morphology["id"] = "wrong-morphology"
        manifest["morphology_ref"]["sha256"] = _write_json_artifact(morphology_path, morphology)
        _write_json_artifact(manifest_path, manifest)
    elif tamper == "morphology-hash":
        manifest["morphology_ref"]["sha256"] = "0" * 64
        _write_json_artifact(manifest_path, manifest)
    elif tamper == "translation-record":
        translation_path = manifest_path.parents[3] / manifest["translation_ref"]["path"]
        translation = json.loads(translation_path.read_text(encoding="utf-8"))
        translation["id"] = "wrong-translation"
        manifest["translation_ref"]["sha256"] = _write_json_artifact(translation_path, translation)
        _write_json_artifact(manifest_path, manifest)
    elif tamper == "translation-hash":
        manifest["translation_ref"]["sha256"] = "0" * 64
        _write_json_artifact(manifest_path, manifest)
    else:
        monkeypatch.setenv("AUTOADAPTER_GRIPPER_DIRECTION", "tick-decreases-qpos")

    with pytest.raises(so_session.SOArm101SessionError):
        so_session.create_so_arm101_evaluation_session("so-arm101", manifest_path, run_path)
    assert not constructed
    assert not run_path.exists()


@pytest.mark.parametrize("task_id", ["T01", "T02", "T03", "T08", "T20"])
def test_demo_evidence_passes_and_rejects_each_checked_in_fixed_criterion(task_id: str) -> None:
    session = _session([])
    try:
        session.reset(
            phase="DEMO",
            execution_id=f"demo-{task_id}-criteria",
            initial_state={"task_id": task_id},
        )
        session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
        session._advance(DEMO_DURATIONS[task_id])  # type: ignore[attr-defined]
        evidence = dict(session.demo_evidence(task_id))
        assert evidence["duration_s"] >= DEMO_DURATIONS[task_id]
        assert evaluate_fixed_demo_criterion(PRIVATE_CRITERIA[task_id], evidence)

        broken = copy.deepcopy(evidence)
        metric, bad_value = DECISIVE_FAILURES[task_id]
        for sample in broken["samples"]:
            sample["metrics"][metric] = bad_value
        assert not evaluate_fixed_demo_criterion(PRIVATE_CRITERIA[task_id], broken)
    finally:
        session.close()


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
    assert with_traffic.route_evidence["candidate_invocation_count"] == 1
    assert with_traffic.route_evidence["accepted_command_count"] > 0
    assert with_traffic.route_evidence["present_position_read_count"] > 0
    assert with_traffic.route_evidence["sdk_readback_max_tick_error"] <= 1
    assert with_traffic.route_evidence["verified"] is True
    assert content_hash(canonical_bytes(with_traffic.route_evidence)) == with_traffic.route_evidence_hash
    session.close()


def test_demo_route_rejects_pre_satisfied_task_without_candidate_tool_call() -> None:
    session = _session([])
    try:
        session.reset(phase="DEMO", execution_id="demo-T01-no-tool", initial_state={"task_id": "T01"})
        session._advance(DEMO_DURATIONS["T01"])  # type: ignore[attr-defined]
        evidence = dict(session.demo_evidence("T01"))
        route = evidence["route_evidence"]
        assert evidence["measurements"]["tip_position_error_m"] <= 0.02
        assert evaluate_fixed_demo_criterion(PRIVATE_CRITERIA["T01"], evidence) is False
        assert route["candidate_invocation_count"] == 0
        assert route["accepted_command_count"] == 0
        assert route["present_position_read_count"] > 0
        assert route["verified"] is False
        assert evidence["guard_results"]["trusted-external-verdict"] is False
    finally:
        session.close()


def test_demo_route_detail_and_hash_pass_with_real_fixture_traffic() -> None:
    session = _session([])
    try:
        session.reset(phase="DEMO", execution_id="demo-T01-positive", initial_state={"task_id": "T01"})
        session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
        session._advance(DEMO_DURATIONS["T01"])  # type: ignore[attr-defined]
        evidence = dict(session.demo_evidence("T01"))
        route = evidence["route_evidence"]
        assert evaluate_fixed_demo_criterion(PRIVATE_CRITERIA["T01"], evidence) is True
        assert route["phase"] == "DEMO"
        assert route["execution_id"] == "demo-T01-positive"
        assert route["candidate_invocation_count"] == 1
        assert route["accepted_command_count"] > 0
        assert route["physics_steps"] > 0
        assert route["simulation_time_progressed"] is True
        assert route["physics_progress"] is True
        assert route["present_position_read_count"] > 0
        assert route["state_route_observed"] is True
        assert route["sdk_readback_max_tick_error"] <= 1
        assert route["verified"] is True
        assert content_hash(canonical_bytes(route)) == evidence["route_evidence_hash"]
    finally:
        session.close()


def test_demo_route_fails_when_present_position_disagrees_with_named_qpos() -> None:
    session = _session([], readback_mismatch=True)
    try:
        session.reset(phase="DEMO", execution_id="demo-T01-mismatch", initial_state={"task_id": "T01"})
        session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
        evidence = dict(session.demo_evidence("T01"))
        route = evidence["route_evidence"]
        assert route["state_route_observed"] is True
        assert route["sdk_readback_max_tick_error"] > 1
        assert route["verified"] is False
        assert evidence["guard_results"]["trusted-external-verdict"] is False
    finally:
        session.close()


def test_route_evidence_baseline_resets_and_tamper_is_detected() -> None:
    session = _session([])
    try:
        session.reset(phase="DEMO", execution_id="demo-T01-first", initial_state={"task_id": "T01"})
        session.invoke(_Candidate(send_sdk_command=True), "public-effect", {})
        first = dict(session.demo_evidence("T01"))
        assert first["route_evidence"]["candidate_invocation_count"] == 1

        session.reset(phase="DEMO", execution_id="demo-T01-second", initial_state={"task_id": "T01"})
        assert session.route_evidence == {}
        assert session.route_evidence_hash is None
        second = dict(session.demo_evidence("T01"))
        assert second["route_evidence"]["execution_id"] == "demo-T01-second"
        assert second["route_evidence"]["candidate_invocation_count"] == 0
        assert second["route_evidence"]["accepted_command_count"] == 0
        assert second["route_evidence"]["verified"] is False

        session._last_route_evidence["physics_steps"] += 1  # type: ignore[attr-defined]
        with pytest.raises(SOArm101SessionError, match="tampered"):
            _ = session.route_evidence
    finally:
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
