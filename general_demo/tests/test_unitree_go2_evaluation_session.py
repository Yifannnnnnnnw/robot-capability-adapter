from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoadapter2.demo import EvaluationRobotSession
from autoadapter2.evaluation import FrozenVideoProfile, RGBFrame
from autoadapter2.integrations.unitree_go2.bridge import (
    ACTIVE_MOTOR_NAMES,
    DDS_MOTOR_SLOT_COUNT,
    Go2DDSMuJoCoBridge,
    INACTIVE_SAFE_FIELDS,
    LowStateFrame,
    MuJoCoSensorFrame,
    SportModeStateFrame,
)
from autoadapter2.integrations.unitree_go2.session import (
    UnitreeGo2EvaluationRobotSession,
    initial_body_yaw_frame,
    start_frame_displacement,
    upright_score,
)
from autoadapter2.validation import HarnessInvocation


@dataclass
class Slot:
    mode: int = 1
    q: float = 0.0
    dq: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    tau: float = 0.0


@dataclass
class LowCmd:
    motor_cmd: list[Slot]
    crc: int = 0


class FakeBackend:
    actuator_names = ACTIVE_MOTOR_NAMES
    timestep = 0.1

    def __init__(self) -> None:
        self.time = 0.0
        self.controls = [0.0] * 12
        self.q = [0.2] * 12
        self.dq = [0.0] * 12
        self.closed = False
        self.set_controls_calls = 0

    @property
    def simulation_time(self) -> float:
        return self.time

    def reset(self) -> None:
        self.time = 0.0
        self.q[:] = [0.2] * 12
        self.dq[:] = [0.0] * 12

    def full_state(self):
        return tuple([0.0, 0.0, 0.34] + [0.0] * 16), tuple([0.0] * 18)

    def sensors(self) -> MuJoCoSensorFrame:
        return MuJoCoSensorFrame(
            q=self.q,
            dq=self.dq,
            actuator_force=self.controls,
            imu_quaternion=(1.0, 0.0, 0.0, 0.0),
            imu_gyroscope=(0.0, 0.0, 0.0),
            imu_accelerometer=(0.0, 0.0, 9.81),
            frame_position=(self.time * 0.5, 0.0, 0.34),
            frame_linear_velocity=(0.5, 0.0, 0.0),
        )

    def set_controls(self, controls) -> None:
        self.controls[:] = list(controls)
        self.set_controls_calls += 1

    def step(self) -> None:
        self.time += self.timestep
        self.q[0] += self.controls[0] * 0.001
        self.dq[0] = self.controls[0]

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self) -> None:
        self.queue: list[object] = []
        self.lowstates: list[LowStateFrame] = []
        self.sportstates: list[SportModeStateFrame] = []
        self.started = False
        self.closed = False

    @staticmethod
    def crc_for(message: LowCmd) -> int:
        total = 0
        for slot in message.motor_cmd:
            total += slot.mode
            total += sum(int(getattr(slot, field) * 1000) for field in ("q", "dq", "kp", "kd", "tau"))
        return total & 0xFFFFFFFF

    def start(self) -> None:
        self.started = True

    def take_lowcmd(self):
        return self.queue.pop(0) if self.queue else None

    def is_lowcmd_type(self, message: object) -> bool:
        return isinstance(message, LowCmd)

    def compute_crc(self, message: object) -> int:
        assert isinstance(message, LowCmd)
        return self.crc_for(message)

    def publish_lowstate(self, state: LowStateFrame) -> None:
        self.lowstates.append(state)

    def publish_sportmodestate(self, state: SportModeStateFrame) -> None:
        self.sportstates.append(state)

    def close(self) -> None:
        self.closed = True


class FakeSDK:
    is_real_sdk = False

    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport
        self.started = False
        self.closed = False
        self.command_count = 0
        self.low_state_publications = 0
        self.sport_mode_state_publications = 0
        self._last_command_evidence = {}

    @property
    def last_command_evidence(self):
        return self._last_command_evidence

    def start(self) -> None:
        self.started = True

    def write_low_command(self, q, dq, kp, kd, tau) -> None:
        active = [Slot(q=q[i], dq=dq[i], kp=kp[i], kd=kd[i], tau=tau[i]) for i in range(12)]
        message = LowCmd(active + [Slot(**INACTIVE_SAFE_FIELDS) for _ in range(8)])
        message.crc = FakeTransport.crc_for(message)
        self.transport.queue.append(message)
        self.command_count += 1
        self._last_command_evidence = {
            "command_count": self.command_count,
            "type_verified": True,
            "crc_verified": True,
            "message_type": "LowCmd_",
        }

    def get_low_state(self):
        if self.transport.lowstates:
            self.low_state_publications = len(self.transport.lowstates)
            return self.transport.lowstates[-1]
        return None

    def get_sport_mode_state(self):
        if self.transport.sportstates:
            self.sport_mode_state_publications = len(self.transport.sportstates)
            return self.transport.sportstates[-1]
        return None

    def close(self) -> None:
        self.closed = True
        self.started = False


class Candidate:
    def _invoke(self, capability_id, arguments, sdk):
        assert capability_id == "low-level-command"
        sdk.write_low_command(
            [0.3] * 12,
            [0.0] * 12,
            [20.0] * 12,
            [0.5] * 12,
            [0.0] * 12,
        )
        return {"status": "issued"}


def _truth(backend: FakeBackend):
    sensors = backend.sensors()
    return {
        "body_position": sensors.frame_position,
        "body_linear_velocity": sensors.frame_linear_velocity,
        "body_x_axis": (1.0, 0.0, 0.0),
        "body_z_axis": (0.0, 0.0, 1.0),
        "body_height_m": 0.34,
        "body_floor_contact": False,
        "head_floor_contact": False,
        "contact_observation_available": True,
    }


def _invocation(metric: str = "forward_displacement_m") -> HarnessInvocation:
    return HarnessInvocation(
        capability_id="low-level-command",
        case_id="case-1",
        inputs={},
        initial_state={"task_id": "G04"},
        repetition=1,
        measurement={
            "measurement_id": "go2-private-truth",
            "entity": "go2.body",
            "unit": "m",
            "frame": "initial_body_yaw",
        },
        metric=metric,
        threshold={"comparator": ">", "value": 0.0},
        dwell_s=0.0,
        timeout_s=2.0,
        aggregation="ALL",
        guard_ids=("trusted-external-verdict", "finite-required-state", "no-body-or-head-floor-contact"),
        run_snapshot_hash="sha256:" + "1" * 64,
        candidate_source_hash="sha256:" + "2" * 64,
        suite_hash="sha256:" + "3" * 64,
        execution_attempt=1,
    )


def _session(*, rollout_steps: int = 3):
    backend = FakeBackend()
    transport = FakeTransport()
    sdk = FakeSDK(transport)
    profile = FrozenVideoProfile(
        profile_id="test",
        profile_version="1.0.0",
        camera="test",
        view="test",
        fps=10,
        width=2,
        height=1,
        container="raw",
        codec="raw",
    )
    session = UnitreeGo2EvaluationRobotSession(
        backend=backend,
        transport=transport,
        bridge=Go2DDSMuJoCoBridge(backend, transport),
        sdk=sdk,
        truth_provider=_truth,
        render_rgb=lambda: b"\x01\x02\x03" * 2,
        video_profile=profile,
        frame_capture_factory=lambda _profile, render, simulation_time: RGBFrame(
            simulation_time, 2, 1, render()
        ),
        rollout_steps=rollout_steps,
    )
    return session, backend, transport, sdk


def test_formula_helpers_use_initial_yaw_and_upright_dot() -> None:
    assert upright_score((0.0, 0.0, 2.0)) == pytest.approx(1.0)
    forward, lateral = initial_body_yaw_frame((0.0, 1.0, 0.0))
    assert forward == pytest.approx((0.0, 1.0))
    assert lateral == pytest.approx((-1.0, 0.0))
    assert start_frame_displacement((0.0, 0.0, 0.3), (0.2, 0.4, 0.3), forward, lateral) == pytest.approx(
        (0.4, -0.2, 0.4472135955)
    )


def test_reset_records_and_verifies_state_without_candidate_or_behavior() -> None:
    session, backend, _transport, sdk = _session()
    assert isinstance(session, EvaluationRobotSession)
    session.reset(phase="VALIDATION_B", execution_id="reset-1", initial_state={"task_id": "G01"})
    assert session.reset_verification["verified"] is True
    assert session.start_position == pytest.approx((0.0, 0.0, 0.34))
    assert session.start_yaw_frame["forward"] == pytest.approx((1.0, 0.0))
    assert session.standing_height_m == pytest.approx(0.34)
    assert sdk.command_count == 0
    assert backend.time == 0.0
    session.close()


def test_invoke_accepts_real_bridge_command_rolls_physics_and_collects_private_truth() -> None:
    session, backend, transport, sdk = _session()
    session.reset(phase="DEMO", execution_id="G04-trial", initial_state={"task_id": "G04"})
    session.start_external_recording(phase="DEMO", execution_id="G04-trial")
    result = session.invoke(Candidate(), "low-level-command", {"duration_s": 0.3})
    frames = session.stop_external_recording()
    assert result == {"status": "issued"}
    assert backend.time == pytest.approx(0.3)
    assert len(frames) == 4
    assert frames[0].simulation_time_s == 0.0
    assert frames[-1].simulation_time_s == pytest.approx(0.3)
    evidence = session.validation_evidence(_invocation())
    assert evidence.sdk_route_verified is True
    assert evidence.guard_results["finite-required-state"] is True
    assert evidence.guard_results["no-body-or-head-floor-contact"] is True
    assert evidence.samples[-1].value == pytest.approx(0.15)
    route = session.route_evidence
    assert route["accepted_command_count"] == 1
    assert route["accepted_command_type_verified"] is True
    assert route["accepted_command_crc_verified"] is True
    assert route["state_publication_observed"] is True
    assert route["simulation_time_progressed"] is True
    assert len(transport.lowstates) == len(transport.sportstates) == 3
    assert sdk.command_count == 1
    demo = session.demo_evidence("G04")
    assert demo["motion_samples"]
    assert demo["terminal_stop_samples"]
    assert "criterion" not in json.dumps(demo).lower()
    session.close()


def test_context_manager_closes_sdk_bridge_transport_and_backend() -> None:
    session, backend, transport, sdk = _session()
    with session:
        session.reset(phase="VALIDATION_B", execution_id="close-1", initial_state={"task_id": "G01"})
    assert sdk.closed is True
    assert transport.closed is True
    assert backend.closed is True
    session.close()


def test_private_task_instances_cover_only_fixed_demo_tasks() -> None:
    path = Path(__file__).parents[1] / "libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["visibility"] == "DEMO_EVALUATION_HARNESS_ONLY"
    assert [item["task_id"] for item in value["instances"]] == ["G01", "G02", "G03", "G04", "G05"]
    assert value["instances"][3]["parameters"]["final_stop_dwell_s"] == 0.5
