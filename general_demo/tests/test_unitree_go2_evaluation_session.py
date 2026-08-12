from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import sys
import time
import types
from queue import Empty
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoadapter2.integrations.unitree_go2.session as go2_session_module
from autoadapter2.demo import EvaluationRobotSession
from autoadapter2.demo.fixed_criteria import evaluate_fixed_demo_criterion
from autoadapter2.evaluation import FrozenVideoProfile, RGBFrame
from autoadapter2.integrations.session_support import MuJoCoFrameCapture
from autoadapter2.integrations.unitree_go2.bridge import (
    ACTIVE_MOTOR_NAMES,
    DDS_MOTOR_SLOT_COUNT,
    Go2DDSMuJoCoBridge,
    INACTIVE_SAFE_FIELDS,
    LowStateFrame,
    MuJoCoSensorFrame,
    SportModeStateFrame,
    UnitreeSDK2Transport,
)
from autoadapter2.integrations.unitree_go2.session import (
    Go2SessionError,
    UnitreeGo2EvaluationRobotSession,
    _candidate_binding_from_config,
    _UnitreeGo2SDKConnection,
    create_evaluation_robot_session,
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
    motor_cmd: list[Slot] = field(default_factory=lambda: [Slot() for _ in range(DDS_MOTOR_SLOT_COUNT)])
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
        self.control_history: list[tuple[float, ...]] = []
        self.after_step = None

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
        self.control_history.append(tuple(self.controls))

    def step(self) -> None:
        self.time += self.timestep
        self.q[0] += self.controls[0] * 0.001
        self.dq[0] = self.controls[0]
        if self.after_step is not None:
            self.after_step()

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self) -> None:
        self.queue = multiprocessing.Queue()
        self.write_count = multiprocessing.Value("i", 0)
        self.lowstate_queue = multiprocessing.Queue()
        self.sportstate_queue = multiprocessing.Queue()
        self.lowstates: list[LowStateFrame] = []
        self.sportstates: list[SportModeStateFrame] = []
        self.started = False
        self.closed = False

    @staticmethod
    def crc_for(message: LowCmd) -> int:
        total = 0
        for slot in message.motor_cmd:
            total += slot.mode
            total += sum(
                int(getattr(slot, name) * 1000)
                for name in ("q", "dq", "kp", "kd", "tau")
            )
        return total & 0xFFFFFFFF

    def start(self) -> None:
        self.started = True

    def take_lowcmd(self):
        try:
            message = self.queue.get_nowait()
        except Empty:
            return None
        # A real DDS reader deserializes the child process' wire sample into
        # the parent's pinned LowCmd_ type.  Recreate that boundary here so
        # the spawn test does not rely on cross-process Python class identity.
        if isinstance(message, dict) and "motor_cmd" in message:
            decoded = LowCmd()
            decoded.crc = message["crc"]
            for target, source in zip(decoded.motor_cmd, message["motor_cmd"]):
                for name in ("mode", "q", "dq", "kp", "kd", "tau"):
                    setattr(target, name, source[name])
            return decoded
        return message

    def is_lowcmd_type(self, message: object) -> bool:
        return isinstance(message, LowCmd)

    def compute_crc(self, message: object) -> int:
        assert isinstance(message, LowCmd)
        return self.crc_for(message)

    def publish_lowstate(self, state: LowStateFrame) -> None:
        self.lowstates.append(state)
        self.lowstate_queue.put(state)

    def publish_sportmodestate(self, state: SportModeStateFrame) -> None:
        self.sportstates.append(state)
        self.sportstate_queue.put(state)

    def close(self) -> None:
        self.closed = True
        self.queue.close()
        self.lowstate_queue.close()
        self.sportstate_queue.close()


class FakePublisher:
    def __init__(self, transport: FakeTransport) -> None:
        self._transport = transport
        self.writes: list[object] = []

    def Write(self, message: object) -> None:
        self.writes.append(message)
        self._transport.write_count.value += 1
        self._transport.queue.put(message)


class PollingSubscriber:
    """Upstream-shaped polling subscriber used only by the process fixture."""

    def __init__(self, samples=()) -> None:
        self._samples = list(samples)

    def Init(self, *_args) -> None:
        return None

    def Read(self, _timeout=None):
        return self._samples.pop(0) if self._samples else None

    def Close(self) -> None:
        return None


class ProcessPollingSubscriber:
    def __init__(self, queue) -> None:
        self._queue = queue

    def Init(self, *_args) -> None:
        return None

    def Read(self, _timeout=None):
        try:
            latest = self._queue.get(timeout=1.0 if _timeout is None else _timeout)
        except Exception:
            return None
        # SDK polling is a latest-sample observation, not a preloaded list.
        # Drain only samples already received at this read boundary; future
        # samples still require another runner tick.
        while True:
            try:
                latest = self._queue.get_nowait()
            except Exception:
                return latest

    def Close(self) -> None:
        return None


class ProcessPublisher:
    def __init__(self, queue, write_count) -> None:
        self._queue = queue
        self._write_count = write_count

    def Init(self) -> None:
        return None

    def Write(self, message: object) -> None:
        self._write_count.value += 1
        self._queue.put(
            {
                "crc": message.crc,
                "motor_cmd": [
                    {
                        name: getattr(slot, name)
                        for name in ("mode", "q", "dq", "kp", "kd", "tau")
                    }
                    for slot in message.motor_cmd
                ],
            }
        )

    def Close(self) -> None:
        return None


def _make_fake_candidate_binding(config):
    publisher = ProcessPublisher(config["command_queue"], config["write_count"])
    lowstate = ProcessPollingSubscriber(config["lowstate_queue"])
    sportstate = ProcessPollingSubscriber(config["sportstate_queue"])
    binding = SimpleNamespace(
        ChannelPublisher=object,
        ChannelSubscriber=object,
        LowCmd_=LowCmd,
        LowState_=LowStateFrame,
        SportModeState_=SportModeStateFrame,
        CRC=FakeCRC,
        lowcmd_publisher=publisher,
        lowstate_subscriber=lowstate,
        sport_mode_state_subscriber=sportstate,
        crc=FakeCRC(),
    )
    return binding, lambda: None


class FakeCRC:
    def Crc(self, message: object) -> int:
        assert isinstance(message, LowCmd)
        return FakeTransport.crc_for(message)


class FakeSDKConnection:
    """A connected upstream-shaped binding; it exposes no command methods."""

    is_real_sdk = False

    def __init__(self, transport: FakeTransport, *, lowstate_samples=(), sportstate_samples=()) -> None:
        self.transport = transport
        self.started = False
        self.closed = False
        self.lowcmd_type = LowCmd
        self.lowcmd_publisher = FakePublisher(transport)
        self.lowstate_subscriber = PollingSubscriber(lowstate_samples)
        self.sport_mode_state_subscriber = PollingSubscriber(sportstate_samples)
        self.crc = FakeCRC()
        self.binding = SimpleNamespace(
            ChannelPublisher=object,
            ChannelSubscriber=object,
            LowCmd_=LowCmd,
            LowState_=object,
            SportModeState_=object,
            CRC=FakeCRC,
            lowcmd_publisher=self.lowcmd_publisher,
            lowstate_subscriber=self.lowstate_subscriber,
            sport_mode_state_subscriber=self.sport_mode_state_subscriber,
            crc=self.crc,
        )

    @property
    def candidate_worker_config(self):
        return {
            "kind": "fixture",
            "factory": _make_fake_candidate_binding,
            "command_queue": self.transport.queue,
            "write_count": self.transport.write_count,
            "lowstate_queue": self.transport.lowstate_queue,
            "sportstate_queue": self.transport.sportstate_queue,
        }

    @property
    def low_state_publications(self) -> int:
        return len(self.transport.lowstates)

    @property
    def sport_mode_state_publications(self) -> int:
        return len(self.transport.sportstates)

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True
        self.started = False


def _publish_command(
    sdk: object,
    value: float = 0.3,
    *,
    kp: float = 20.0,
    kd: float = 0.5,
    tau: float = 0.0,
) -> None:
    command = sdk.LowCmd_()
    assert len(command.motor_cmd) == DDS_MOTOR_SLOT_COUNT
    for index, slot in enumerate(command.motor_cmd):
        slot.mode = 1
        if index < 12:
            slot.q = value
            slot.dq = 0.0
            slot.kp = kp
            slot.kd = kd
            slot.tau = tau
        else:
            for name, safe_value in INACTIVE_SAFE_FIELDS.items():
                setattr(slot, name, safe_value)
    command.crc = sdk.crc.Crc(command)
    sdk.lowcmd_publisher.Write(command)


class Candidate:
    def _invoke(self, capability_id, _arguments, sdk):
        assert capability_id == "low-level-command"
        _publish_command(sdk)
        return {"status": "issued"}


class SequencedCandidate:
    def __init__(self, first_step, second_step) -> None:
        self.first_step = first_step
        self.second_step = second_step

    def _invoke(self, _capability_id, _arguments, sdk):
        _publish_command(sdk, 1.0, kp=0.0, tau=1.0)
        assert self.first_step.wait(1.0)
        _publish_command(sdk, 2.0, kp=0.0, tau=2.0)
        assert self.second_step.wait(1.0)
        _publish_command(sdk, 3.0, kp=0.0, tau=3.0)
        return {"status": "issued"}


class FeedbackCandidate:
    def __init__(self, accepted_step) -> None:
        self.accepted_step = accepted_step

    def _invoke(self, _capability_id, _arguments, sdk):
        first = None
        for _ in range(64):
            first = sdk.lowstate_subscriber.Read(0.05)
            if first is not None:
                break
        assert first is not None, "candidate did not receive first LowState"
        first_q = first.motor_state[0].q
        _publish_command(sdk, first_q, kp=0.0, kd=0.0, tau=first_q)
        assert self.accepted_step.wait(1.0)
        # The polling endpoint may still contain state samples produced while
        # the first command was in flight.  Read until a later DDS sample
        # reflects the accepted command; never synthesize feedback locally.
        second_q = first_q
        observed = []
        for _ in range(32):
            second = sdk.lowstate_subscriber.Read(0.05)
            if second is None:
                continue
            second_q = second.motor_state[0].q
            observed.append((second_q, second.motor_state[0].dq))
            if second_q > first_q:
                break
        assert second_q > first_q, f"candidate did not receive later feedback: {first_q} -> {second_q}; {observed}"
        _publish_command(sdk, second_q, kp=0.0, kd=0.0, tau=second_q)
        return {"feedback_q": [first_q, second_q]}


class InfiniteCandidate:
    def _invoke(self, _capability_id, _arguments, _sdk):
        while True:
            time.sleep(0.01)


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
        guard_ids=(
            "trusted-external-verdict",
            "finite-required-state",
            "no-body-or-head-floor-contact",
        ),
        run_snapshot_hash="sha256:" + "1" * 64,
        candidate_source_hash="sha256:" + "2" * 64,
        suite_hash="sha256:" + "3" * 64,
        execution_attempt=1,
    )


def _session(*, rollout_steps: int = 3, lowstate_samples=(), sportstate_samples=()):
    backend = FakeBackend()
    transport = FakeTransport()
    sdk = FakeSDKConnection(
        transport,
        lowstate_samples=lowstate_samples,
        sportstate_samples=sportstate_samples,
    )
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
    capture_count = {"created": 0}

    def capture_factory(capture_profile, render):
        capture_count["created"] += 1
        return MuJoCoFrameCapture(capture_profile, render)

    session = UnitreeGo2EvaluationRobotSession(
        backend=backend,
        transport=transport,
        bridge=Go2DDSMuJoCoBridge(backend, transport),
        sdk=sdk,
        truth_provider=_truth,
        render_rgb=lambda: b"\x01\x02\x03" * 2,
        video_profile=profile,
        frame_capture_factory=capture_factory,
        rollout_steps=rollout_steps,
    )
    return session, backend, transport, sdk, capture_count


def test_formula_helpers_use_initial_yaw_and_upright_dot() -> None:
    assert upright_score((0.0, 0.0, 2.0)) == pytest.approx(1.0)
    forward, lateral = initial_body_yaw_frame((0.0, 1.0, 0.0))
    assert forward == pytest.approx((0.0, 1.0))
    assert lateral == pytest.approx((-1.0, 0.0))
    assert start_frame_displacement(
        (0.0, 0.0, 0.3), (0.2, 0.4, 0.3), forward, lateral
    ) == pytest.approx((0.4, -0.2, 0.4472135955))


def test_reset_records_and_verifies_state_without_candidate_or_behavior() -> None:
    session, backend, _transport, _sdk, _capture_count = _session()
    assert isinstance(session, EvaluationRobotSession)
    session.reset(
        phase="VALIDATION_B",
        execution_id="reset-1",
        initial_state={"task_id": "G01"},
    )
    assert session.reset_verification["verified"] is True
    assert session.start_position == pytest.approx((0.0, 0.0, 0.34))
    assert session.start_yaw_frame["forward"] == pytest.approx((1.0, 0.0))
    assert session.standing_height_m == pytest.approx(0.34)
    assert backend.time == 0.0
    session.close()


def test_direct_validation_candidate_then_collect_advances_private_clock() -> None:
    session, backend, transport, sdk, _capture_count = _session()
    session.reset(
        phase="VALIDATION_B",
        execution_id="direct-collect",
        initial_state={"task_id": "G04"},
    )
    # This is the Validation-B ordering: candidate code sees only connected
    # SDK2 objects, then the session-owned collector advances the bridge.
    result = Candidate()._invoke("low-level-command", {}, session.sdk)
    evidence = session.validation_evidence(_invocation())
    assert result == {"status": "issued"}
    assert backend.time > 0.0
    assert evidence.sdk_route_verified is True
    assert session.route_evidence["accepted_command_count"] == 1
    assert transport.write_count.value == 1
    assert not hasattr(session.sdk, "write_low_command")
    session.close()


def test_invoke_rolls_physics_and_captures_one_shared_stream() -> None:
    session, backend, transport, sdk, capture_count = _session()
    session.reset(
        phase="DEMO",
        execution_id="G04-trial",
        initial_state={"task_id": "G04"},
    )
    session.start_external_recording(phase="DEMO", execution_id="G04-trial")
    result = session.invoke(Candidate(), "low-level-command", {"duration_s": 0.3})
    frames = session.stop_external_recording()
    assert result == {"status": "issued"}
    assert backend.time == pytest.approx(0.3)
    assert len(frames) == 4
    assert frames[0].simulation_time_s == 0.0
    assert frames[-1].simulation_time_s == pytest.approx(0.3)
    assert capture_count["created"] == 1
    assert session.route_evidence["accepted_command_count"] == 1
    assert session.route_evidence["accepted_command_type_verified"] is True
    assert session.route_evidence["accepted_command_crc_verified"] is True
    assert session.route_evidence["state_publication_observed"] is True
    assert session.route_evidence["simulation_time_progressed"] is True
    assert len(transport.lowstates) == len(transport.sportstates) == 3
    assert transport.write_count.value == 1

    evidence = session.validation_evidence(_invocation())
    assert evidence.sdk_route_verified is True
    assert evidence.guard_results["finite-required-state"] is True
    assert evidence.guard_results["no-body-or-head-floor-contact"] is True
    assert evidence.samples[-1].value > 0.0

    demo = session.demo_evidence("G04")
    assert demo["motion_samples"]
    assert demo["terminal_samples"]
    assert all(sample["phase"] == "motion" for sample in demo["motion_samples"])
    assert all(sample["phase"] == "terminal" for sample in demo["terminal_samples"])
    assert demo["motion_samples"][-1]["time_s"] == pytest.approx(
        demo["terminal_samples"][0]["time_s"]
    )
    assert demo["sdk_route_guard"] is True
    assert demo["sdk_route_evidence"]["verified"] is True
    assert "terminal_stop_samples" not in demo
    assert set(demo["guard_results"]) == {
        "trusted-external-verdict",
        "finite-required-state",
        "no-body-or-head-floor-contact",
    }
    session.close()


def test_invoke_clock_applies_commands_in_arrival_order_then_stales_without_replay() -> None:
    session, backend, _transport, _sdk, _capture_count = _session(rollout_steps=3)
    spawn_context = multiprocessing.get_context("spawn")
    first_step = spawn_context.Event()
    second_step = spawn_context.Event()
    step_count = {"value": 0}

    def signal_step() -> None:
        step_count["value"] += 1
        if step_count["value"] == 1:
            first_step.set()
        elif step_count["value"] == 2:
            second_step.set()

    backend.after_step = signal_step

    session.reset(phase="DEMO", execution_id="ordered-clock", initial_state={"task_id": "G03"})
    result = session.invoke(
        SequencedCandidate(first_step, second_step),
        "low-level-command",
        {"duration_s": 0.3},
    )
    assert result == {"status": "issued"}
    assert [
        values[0]
        for values in backend.control_history
        if values[0] > 0.0
    ] == pytest.approx([1.0, 1.5, 2.25])
    assert session.route_evidence["accepted_command_count"] == 3

    # No new DDS message is published.  The bridge's frozen 0.100 s
    # simulation-time rule must zero controls; the session must not replay the
    # final queued value to keep the robot alive.
    session._advance(0.2, wait_for_command=False)
    assert backend.controls == [0.0] * 12
    assert session._last_bridge_result["command_health"] == "STALE"
    session.close()


def test_candidate_polls_successive_lowstates_and_publishes_feedback_commands() -> None:
    spawn_context = multiprocessing.get_context("spawn")
    accepted_step = spawn_context.Event()
    session, backend, transport, _sdk, _capture_count = _session(rollout_steps=4)
    step_count = {"value": 0}

    def signal_accepted_step() -> None:
        step_count["value"] += 1
        if any(value > 0.0 for value in backend.controls):
            accepted_step.set()

    backend.after_step = signal_accepted_step

    session.reset(phase="DEMO", execution_id="feedback-polling", initial_state={"task_id": "G03"})
    result = session.invoke(FeedbackCandidate(accepted_step), "low-level-command", {"duration_s": 0.4})
    feedback_q = result["feedback_q"]
    assert len(feedback_q) == 2
    assert feedback_q[1] > feedback_q[0]
    assert [values[0] for values in backend.control_history if values[0] > 0.0] == pytest.approx(
        feedback_q
    )
    assert transport.write_count.value == 2
    assert session.route_evidence["accepted_command_count"] == 2
    session.close()


def test_candidate_timeout_terminates_worker_and_allows_a_clean_next_trial(monkeypatch) -> None:
    session, backend, transport, sdk, _capture_count = _session(rollout_steps=1)
    monkeypatch.setattr(go2_session_module, "DEFAULT_CANDIDATE_TIMEOUT_S", 0.01)

    session.reset(phase="DEMO", execution_id="timeout", initial_state={"task_id": "G01"})
    with pytest.raises(Go2SessionError, match="bounded clock window"):
        session.invoke(InfiniteCandidate(), "low-level-command", {})
    assert session.evidence_scope == "TEST_FIXTURE_ONLY"
    assert backend.closed is False
    assert transport.closed is False
    assert sdk.closed is False

    # Allow the fresh worker to finish its spawn/initialization window in the
    # clean-trial assertion; the timeout above remains the behavior under test.
    monkeypatch.setattr(go2_session_module, "DEFAULT_CANDIDATE_TIMEOUT_S", 8.0)
    session.reset(phase="DEMO", execution_id="timeout-next-trial", initial_state={"task_id": "G01"})
    assert session.invoke(Candidate(), "low-level-command", {}) == {"status": "issued"}
    assert transport.write_count.value == 1
    session.close()


def test_context_manager_closes_sdk_bridge_transport_and_backend() -> None:
    session, backend, transport, sdk, _capture_count = _session()
    with session:
        session.reset(
            phase="VALIDATION_B",
            execution_id="close-1",
            initial_state={"task_id": "G01"},
        )
    assert sdk.closed is True
    assert transport.closed is True
    assert backend.closed is True
    session.close()


@pytest.mark.parametrize("task_id, dwell_s", [("G01", 1.0), ("G02", 1.0), ("G03", 2.0), ("G05", 1.0)])
def test_demo_evidence_uses_post_action_terminal_windows(task_id: str, dwell_s: float) -> None:
    session, _backend, _transport, _sdk, _capture_count = _session(rollout_steps=1)
    session.reset(phase="DEMO", execution_id=f"{task_id}-window", initial_state={"task_id": task_id})
    session.invoke(Candidate(), "low-level-command", {"duration_s": 0.1})
    evidence = session.demo_evidence(task_id)
    terminal = evidence["terminal_samples"]
    assert terminal
    assert all(sample["phase"] == "terminal" for sample in terminal)
    assert all(sample["time_s"] > 0.0 for sample in terminal)
    assert all(isinstance(value, (int, float, bool)) for sample in terminal for value in sample["metrics"].values())
    assert evidence["duration_s"] == pytest.approx(dwell_s)
    assert set(evidence["guard_results"]) == {
        "trusted-external-verdict",
        "finite-required-state",
        "no-body-or-head-floor-contact",
    }
    session.close()


def test_private_task_instances_cover_only_fixed_demo_tasks() -> None:
    path = Path(__file__).parents[1] / (
        "libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["visibility"] == "DEMO_EVALUATION_HARNESS_ONLY"
    assert [item["task_id"] for item in value["instances"]] == [
        "G01",
        "G02",
        "G03",
        "G04",
        "G05",
    ]
    assert value["instances"][3]["parameters"]["final_stop_dwell_s"] == 0.5


def _fixed_criteria() -> dict[str, dict]:
    path = Path(__file__).parents[1] / (
        "libraries/tasks/unitree-go2-stock-12dof/1.0.0/evaluation_private.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        item["task_id"]: item
        for item in value["criteria"]
        if item["task_id"] in {"G01", "G02", "G03", "G04", "G05"}
    }


def test_demo_no_action_cannot_pass_a_pre_satisfied_g03_reset() -> None:
    session, _backend, _transport, _sdk, _capture_count = _session(rollout_steps=1)
    session.reset(
        phase="DEMO",
        execution_id="g03-no-action",
        initial_state={"task_id": "G03"},
    )
    evidence = session.demo_evidence("G03")
    assert evidence["sdk_route_evidence"]["verified"] is False
    assert evidence["sdk_route_evidence"]["accepted_command_count"] == 0
    assert evidence["sdk_route_evidence"]["candidate_invocation_observed"] is False
    assert evidence["sdk_route_guard"] is False
    assert evidence["guard_results"]["trusted-external-verdict"] is False
    assert evaluate_fixed_demo_criterion(_fixed_criteria()["G03"], evidence) is False
    session.close()


def _sample(time_s: float, metrics: dict[str, object], phase: str = "terminal") -> dict:
    return {"time_s": time_s, "phase": phase, "metrics": metrics}


def _positive_demo_evidence(task_id: str) -> dict:
    guards = {
        "trusted-external-verdict": True,
        "finite-required-state": True,
        "no-body-or-head-floor-contact": True,
    }
    if task_id == "G01":
        samples = [
            _sample(time_s, {"body_height_m": 0.34, "upright_score": 1.0, "planar_speed_m_s": 0.0})
            for time_s in (0.0, 0.5, 1.0)
        ]
        return {"task_id": task_id, "samples": samples, "duration_s": 1.0, "guard_results": guards}
    if task_id == "G02":
        samples = [
            _sample(
                time_s,
                {
                    "body_height_to_standing_height_ratio": 0.5,
                    "upright_score": 1.0,
                    "planar_speed_m_s": 0.0,
                },
            )
            for time_s in (0.0, 0.5, 1.0)
        ]
        return {"task_id": task_id, "samples": samples, "duration_s": 1.0, "guard_results": guards}
    if task_id == "G03":
        samples = [
            _sample(time_s, {"body_height_m": 0.34, "upright_score": 1.0, "horizontal_drift_m": 0.0})
            for time_s in (0.0, 1.0, 2.0)
        ]
        return {"task_id": task_id, "samples": samples, "duration_s": 2.0, "guard_results": guards}
    if task_id == "G04":
        motion = [
            _sample(0.0, {"forward_displacement_m": 0.04, "body_height_m": 0.34, "upright_score": 1.0, "absolute_lateral_displacement_m": 0.0}, "motion"),
            _sample(0.5, {"forward_displacement_m": 0.1, "body_height_m": 0.34, "upright_score": 1.0, "absolute_lateral_displacement_m": 0.0}, "motion"),
        ]
        terminal = [
            _sample(0.5, {"planar_speed_m_s": 0.0}, "terminal"),
            _sample(1.0, {"planar_speed_m_s": 0.0}, "terminal"),
        ]
        return {
            "task_id": task_id,
            "samples": motion + terminal,
            "motion_samples": motion,
            "terminal_samples": terminal,
            "motion_duration_s": 0.5,
            "terminal_duration_s": 0.5,
            "duration_s": 0.5,
            "guard_results": guards,
        }
    samples = [
        _sample(time_s, {"absolute_body_height_error_m": 0.0, "upright_score": 1.0, "horizontal_drift_m": 0.0})
        for time_s in (0.0, 0.5, 1.0)
    ]
    return {"task_id": task_id, "samples": samples, "duration_s": 1.0, "guard_results": guards}


@pytest.mark.parametrize("task_id", ["G01", "G02", "G03", "G04", "G05"])
def test_fixed_demo_evaluator_accepts_one_positive_and_rejects_one_decisive_negative(task_id: str) -> None:
    criterion = _fixed_criteria()[task_id]
    positive = _positive_demo_evidence(task_id)
    assert evaluate_fixed_demo_criterion(criterion, positive) is True

    negative = copy.deepcopy(positive)
    if task_id == "G01":
        negative["samples"][1]["metrics"]["planar_speed_m_s"] = 0.2
    elif task_id == "G02":
        negative["samples"][1]["metrics"]["body_height_to_standing_height_ratio"] = 0.9
    elif task_id == "G03":
        negative["samples"][1]["metrics"]["horizontal_drift_m"] = 0.1
    elif task_id == "G04":
        negative["motion_samples"][1]["metrics"]["forward_displacement_m"] = 0.0
        negative["samples"][1]["metrics"]["forward_displacement_m"] = 0.0
    else:
        negative["samples"][1]["metrics"]["absolute_body_height_error_m"] = 0.1
    assert evaluate_fixed_demo_criterion(criterion, negative) is False


def test_factory_is_importable_through_lazy_integration_export() -> None:
    import autoadapter2.integrations.unitree_go2 as go2_integration

    assert callable(go2_integration.create_evaluation_robot_session)


def _external_factory_refs(root: Path) -> dict[str, object]:
    manifest = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    runtime_lock = root / "general_demo/environments/unitree-go2-linux-amd64/1.0.0/runtime-lock.json"
    return {
        "integration_manifest_path": manifest,
        "integration_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "runtime_lock_path": runtime_lock,
        "runtime_lock_sha256": hashlib.sha256(runtime_lock.read_bytes()).hexdigest(),
    }


def test_factory_rejects_wrong_manifest_configuration(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    source = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    value = json.loads(source.read_text(encoding="utf-8"))
    value["robot_configuration_id"] = "wrong-configuration"
    path = tmp_path / "integration_manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    refs = _external_factory_refs(root)
    refs["integration_manifest_path"] = path
    refs["integration_manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(Go2SessionError):
        create_evaluation_robot_session(
            project_root=root,
            model_path=tmp_path / "scene.xml",
            **refs,
        )


def test_factory_rejects_wrong_scene_hash(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>", encoding="utf-8")
    with pytest.raises(Go2SessionError, match="external manifest path and hash"):
        create_evaluation_robot_session(project_root=root, model_path=scene)
    with pytest.raises(Go2SessionError, match="scene hash"):
        create_evaluation_robot_session(
            project_root=root,
            model_path=scene,
            **_external_factory_refs(root),
        )


def test_factory_rejects_unready_manifest(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    source = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    value = json.loads(source.read_text(encoding="utf-8"))
    value["status"] = "DRAFT"
    path = tmp_path / "integration_manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    refs = _external_factory_refs(root)
    refs["integration_manifest_path"] = path
    refs["integration_manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(Go2SessionError):
        create_evaluation_robot_session(
            project_root=root,
            model_path=tmp_path / "scene.xml",
            **refs,
        )


def test_factory_binds_selected_manifest_and_runtime_lock_bytes_dynamically(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    manifest_source = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    lock_source = root / "general_demo/environments/unitree-go2-linux-amd64/1.0.0/runtime-lock.json"
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    runtime_lock = json.loads(lock_source.read_text(encoding="utf-8"))
    runtime_lock["selected_by_test"] = "current-bytes"
    selected_lock = tmp_path / "runtime-lock.json"
    selected_lock.write_text(json.dumps(runtime_lock), encoding="utf-8")
    selected_lock_sha = hashlib.sha256(selected_lock.read_bytes()).hexdigest()
    manifest["runtime"]["lock_sha256"] = selected_lock_sha
    selected_manifest = tmp_path / "integration_manifest.json"
    selected_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    selected_manifest_sha = hashlib.sha256(selected_manifest.read_bytes()).hexdigest()
    bad_scene = tmp_path / "scene.xml"
    bad_scene.write_text("<mujoco/>", encoding="utf-8")

    with pytest.raises(Go2SessionError, match="scene hash"):
        create_evaluation_robot_session(
            project_root=root,
            integration_manifest_path=selected_manifest,
            integration_manifest_sha256=selected_manifest_sha,
            runtime_lock_path=selected_lock,
            runtime_lock_sha256=selected_lock_sha,
            model_path=bad_scene,
        )
    with pytest.raises(Go2SessionError, match="selected runtime lock hash"):
        create_evaluation_robot_session(
            project_root=root,
            integration_manifest_path=selected_manifest,
            integration_manifest_sha256=selected_manifest_sha,
            runtime_lock_path=selected_lock,
            runtime_lock_sha256="0" * 64,
            model_path=bad_scene,
        )

    source = Path(go2_session_module.__file__).read_text(encoding="utf-8")
    assert "GO2_INTEGRATION_MANIFEST_SHA256" not in source
    assert "GO2_RUNTIME_LOCK_SHA256" not in source
    assert "GO2_MORPHOLOGY_SHA256" not in source
    assert "GO2_SDK_SHA256" not in source
    assert "GO2_TRANSLATION_SHA256" not in source


def test_factory_constructs_only_sdk_grounded_scope_after_verified_selection(monkeypatch) -> None:
    root = Path(__file__).parents[2]
    profile = go2_session_module._profile(None)
    selected_model = root / "verified-scene.xml"
    selected_tasks = root / (
        "general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
    )
    calls: dict[str, object] = {}

    def verified_inputs(**kwargs):
        calls.update(kwargs)
        return selected_model, selected_tasks, profile

    class ProductionSession:
        evidence_scope = "SDK_GROUNDED_SIMULATION"

        def __init__(self, model_path, *, task_instances_path, video_profile):
            self.model_path = model_path
            self.task_instances_path = task_instances_path
            self.video_profile = video_profile

        def close(self):
            return None

    monkeypatch.setattr(go2_session_module, "_verify_production_inputs", verified_inputs)
    monkeypatch.setattr(go2_session_module, "UnitreeGo2EvaluationRobotSession", ProductionSession)
    result = create_evaluation_robot_session(
        project_root=root,
        **_external_factory_refs(root),
    )
    assert result.evidence_scope == "SDK_GROUNDED_SIMULATION"
    assert result.model_path == selected_model
    assert calls["root"] == root


def test_factory_accepts_first_g2_runner_positional_contract(monkeypatch, tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    manifest = root / "general_demo/integrations/unitree-go2/integration_manifest.json"
    run_directory = tmp_path / "first-g2-run"
    run_directory.mkdir()
    selected_model = root / "verified-scene.xml"
    selected_tasks = root / (
        "general_demo/libraries/tasks/unitree-go2-stock-12dof/1.0.0/task_instances_private.json"
    )
    profile = go2_session_module._profile(None)
    calls: dict[str, object] = {}

    def verified_inputs(**kwargs):
        calls.update(kwargs)
        return selected_model, selected_tasks, profile

    class ProductionSession:
        evidence_scope = "SDK_GROUNDED_SIMULATION"

        def __init__(self, model_path, *, task_instances_path, video_profile):
            self.model_path = model_path
            self.task_instances_path = task_instances_path
            self.video_profile = video_profile

        def close(self):
            return None

    monkeypatch.setattr(go2_session_module, "_verify_production_inputs", verified_inputs)
    monkeypatch.setattr(go2_session_module, "UnitreeGo2EvaluationRobotSession", ProductionSession)
    result = create_evaluation_robot_session("unitree-go2", manifest, run_directory)
    assert result.evidence_scope == "SDK_GROUNDED_SIMULATION"
    assert calls["root"] == root
    assert calls["integration_manifest_path"] == manifest.resolve()
    assert calls["runtime_lock_path"] == (
        root / "general_demo/environments/unitree-go2-linux-amd64/1.0.0/runtime-lock.json"
    ).resolve()
    assert isinstance(calls["integration_manifest_sha256"], str)
    assert isinstance(calls["runtime_lock_sha256"], str)


def test_factory_uses_selected_translation_runtime_lock_for_first_g2_runner(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    artifact_root = project_root / "general_demo/run_artifacts/go2-ready"
    manifest_path = artifact_root / "integration_manifest.json"
    translation_path = artifact_root / "translation.json"
    runtime_lock_path = artifact_root / "runtime-lock.json"
    fallback_lock_path = (
        project_root
        / "general_demo/environments/unitree-go2-linux-amd64/1.0.0/runtime-lock.json"
    )
    run_directory = artifact_root / "run"
    run_directory.mkdir(parents=True)
    fallback_lock_path.parent.mkdir(parents=True)

    runtime_lock_path.write_text(
        json.dumps(
            {
                "runtime_id": "unitree-go2-linux-amd64",
                "version": "1.0.0",
                "selected_from": "run_artifacts",
            }
        ),
        encoding="utf-8",
    )
    runtime_lock_sha256 = hashlib.sha256(runtime_lock_path.read_bytes()).hexdigest()
    fallback_lock_path.write_text(
        json.dumps({"selected_from": "checked-in-environment"}),
        encoding="utf-8",
    )
    translation_path.write_text(
        json.dumps(
            {
                "record_type": "translation",
                "runtime_lock_ref": {
                    "path": runtime_lock_path.relative_to(project_root).as_posix(),
                    "sha256": runtime_lock_sha256,
                },
            }
        ),
        encoding="utf-8",
    )
    translation_sha256 = hashlib.sha256(translation_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "translation_ref": {
                    "path": translation_path.relative_to(project_root).as_posix(),
                    "sha256": translation_sha256,
                },
                "runtime": {
                    "id": "unitree-go2-linux-amd64",
                    "version": "1.0.0",
                    "lock_sha256": runtime_lock_sha256,
                },
            }
        ),
        encoding="utf-8",
    )

    profile = go2_session_module._profile(None)
    calls: dict[str, object] = {}

    def verified_inputs(**kwargs):
        calls.update(kwargs)
        return project_root / "verified-scene.xml", project_root / "tasks.json", profile

    class ProductionSession:
        evidence_scope = "SDK_GROUNDED_SIMULATION"

        def __init__(self, model_path, *, task_instances_path, video_profile):
            self.model_path = model_path
            self.task_instances_path = task_instances_path
            self.video_profile = video_profile

        def close(self):
            return None

    monkeypatch.setattr(go2_session_module, "_verify_production_inputs", verified_inputs)
    monkeypatch.setattr(go2_session_module, "UnitreeGo2EvaluationRobotSession", ProductionSession)

    result = create_evaluation_robot_session("unitree-go2", manifest_path, run_directory)

    assert result.evidence_scope == "SDK_GROUNDED_SIMULATION"
    assert calls["root"] == project_root.resolve()
    assert calls["runtime_lock_path"] == runtime_lock_path.resolve()
    assert calls["runtime_lock_sha256"] == runtime_lock_sha256


def test_real_shaped_session_binds_channel_factory_to_domain_one_loopback(monkeypatch) -> None:
    factory_calls: list[tuple[int, str]] = []

    class Endpoint:
        def __init__(self, topic, message_type):
            self.topic = topic
            self.message_type = message_type
            self.closed = False
            self.init_args = None

        def Init(self, *_args):
            self.init_args = _args
            return None

        def Write(self, _message):
            return None

        def Read(self, _timeout=None):
            return None

        def Close(self):
            self.closed = True

    class LowCmdType:
        pass

    class LowStateType:
        pass

    class SportModeStateType:
        pass

    class CRC:
        def Crc(self, _message):
            return 0

    def channel_factory_initialize(domain, interface):
        factory_calls.append((domain, interface))

    channel = types.ModuleType("unitree_sdk2py.core.channel")
    channel.ChannelFactoryInitialize = channel_factory_initialize
    channel.ChannelPublisher = Endpoint
    channel.ChannelSubscriber = Endpoint
    dds = types.ModuleType("unitree_sdk2py.idl.unitree_go.msg.dds_")
    dds.LowCmd_ = LowCmdType
    dds.LowState_ = LowStateType
    dds.SportModeState_ = SportModeStateType
    default = types.ModuleType("unitree_sdk2py.idl.default")
    default.unitree_go_msg_dds__LowState_ = LowStateType
    default.unitree_go_msg_dds__SportModeState_ = SportModeStateType
    crc = types.ModuleType("unitree_sdk2py.utils.crc")
    crc.CRC = CRC
    for name, module in {
        "unitree_sdk2py": types.ModuleType("unitree_sdk2py"),
        "unitree_sdk2py.core": types.ModuleType("unitree_sdk2py.core"),
        "unitree_sdk2py.core.channel": channel,
        "unitree_sdk2py.idl": types.ModuleType("unitree_sdk2py.idl"),
        "unitree_sdk2py.idl.default": default,
        "unitree_sdk2py.idl.unitree_go": types.ModuleType("unitree_sdk2py.idl.unitree_go"),
        "unitree_sdk2py.idl.unitree_go.msg": types.ModuleType("unitree_sdk2py.idl.unitree_go.msg"),
        "unitree_sdk2py.idl.unitree_go.msg.dds_": dds,
        "unitree_sdk2py.utils": types.ModuleType("unitree_sdk2py.utils"),
        "unitree_sdk2py.utils.crc": crc,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    backend = FakeBackend()
    transport = UnitreeSDK2Transport()
    bridge = Go2DDSMuJoCoBridge(backend, transport)
    sdk = _UnitreeGo2SDKConnection()
    session = UnitreeGo2EvaluationRobotSession(
        backend=backend,
        transport=transport,
        bridge=bridge,
        sdk=sdk,
        truth_provider=_truth,
        render_rgb=lambda: b"\x00" * 6,
        auto_start=False,
    )
    session.start()
    assert factory_calls == [(1, "lo")]
    assert callable(session.sdk.ChannelFactoryInitialize)
    session.sdk.ChannelFactoryInitialize()
    assert factory_calls == [(1, "lo"), (1, "lo")]
    candidate_binding, close_candidate_binding = _candidate_binding_from_config(
        {"kind": "unitree_sdk2", "domain": 1, "interface": "lo"}
    )
    candidate_binding.ChannelFactoryInitialize()
    assert factory_calls == [(1, "lo"), (1, "lo"), (1, "lo"), (1, "lo")]
    close_candidate_binding()
    with pytest.raises(Go2SessionError, match="domain 1 on lo"):
        _candidate_binding_from_config({"kind": "unitree_sdk2", "domain": 0, "interface": "lo"})
    assert callable(sdk._lowstate_audit_subscriber.init_args[0])
    assert sdk._lowstate_audit_subscriber.init_args[1] == 10
    assert sdk._sport_audit_subscriber.init_args[1] == 10
    assert not hasattr(session.sdk, "lowcmd_publisher")
    assert session._candidate_worker_config == {
        "kind": "unitree_sdk2",
        "domain": 1,
        "interface": "lo",
    }
    session.close()
