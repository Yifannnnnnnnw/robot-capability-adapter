from __future__ import annotations

from typing import Any

from autoadapter2.consumer import ConsumerResult
from autoadapter2.demo import DemoEvaluationHarness, DemoTask, EvaluationRoute
from autoadapter2.evaluation import EncodedVideo, FrozenVideoProfile, OpaqueVideoHandle, RGBFrame
from autoadapter2.foundation.hashing import content_hash


class _Encoder:
    def encode(self, _profile, frames):
        return EncodedVideo(OpaqueVideoHandle("private-ordering"), bytes([len(frames)]))


class _Consumer:
    def __init__(self) -> None:
        self.views: list[dict[str, Any]] = []

    def execute(self, task, *, seed: int) -> ConsumerResult:
        del seed
        view = dict(task)
        assert "private_criterion" not in view
        assert "truth" not in view
        assert "video" not in view
        self.views.append(view)
        return ConsumerResult("COMPLETED", {"success": True}, 1, ())


class _Session:
    evidence_scope = "TEST_FIXTURE_ONLY"
    robot_model_id = "fixture-robot"
    robot_configuration_id = "fixture-configuration"
    sdk = object()

    def __init__(self, *, evidence_fails: bool = False) -> None:
        self.time_s = 0.1
        self.recording = False
        self.evidence_fails = evidence_fails
        self.events: list[str] = []

    @property
    def simulation_time_s(self) -> float:
        return self.time_s

    def reset(self, *, phase: str, execution_id: str, initial_state) -> None:
        del phase, execution_id, initial_state
        self.events.append("reset")
        self.time_s = 0.0

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        del phase, execution_id
        self.events.append("start")
        self.recording = True

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        assert self.recording
        self.events.append("stop")
        self.recording = False
        rgb = b"\x01" * 6
        return (RGBFrame(0.0, 2, 1, rgb), RGBFrame(0.1, 2, 1, rgb))

    def validation_evidence(self, _invocation):
        raise AssertionError("not used by this test")

    def demo_evidence(self, _task_id: str) -> dict[str, Any]:
        assert self.recording, "trusted dwell/terminal evidence must be collected before stop"
        self.events.append("evidence")
        if self.evidence_fails:
            raise RuntimeError("fixture evidence acquisition failed")
        return {"task_id": "fixture", "samples": [{"time_s": 0.1, "metrics": {"physical": 1.0}}]}

    def invoke(self, candidate, capability_id: str, arguments):
        del candidate, capability_id, arguments
        return {"status": "OK"}


def _profile() -> FrozenVideoProfile:
    return FrozenVideoProfile(
        profile_id="ordering",
        profile_version="1.0.0",
        camera="external",
        view="robot-and-resource",
        fps=10,
        width=2,
        height=1,
        container="fake",
        codec="rgb",
    )


def _route() -> EvaluationRoute:
    digest = content_hash(b"fixture")
    return EvaluationRoute("run-ordering", digest, digest, digest, digest)


def _tasks() -> tuple[DemoTask, ...]:
    return tuple(
        DemoTask(
            requirement_id=f"req-{index}",
            task_id=f"task-{index}",
            description="public task",
            public_state={"target": index},
            private_criterion={"task_id": f"task-{index}", "secret": "not-for-consumer"},
        )
        for index in range(5)
    )


def _harness(session: _Session) -> DemoEvaluationHarness:
    return DemoEvaluationHarness(
        session,
        _profile(),
        lambda _phase, _execution_id: _Encoder(),
        _route(),
        lambda _criterion, _evidence: True,
        repetitions=1,
    )


def test_demo_evidence_is_collected_before_recording_stops() -> None:
    session = _Session()
    consumer = _Consumer()
    results = _harness(session).run(
        consumer,
        _tasks(),
        seed=0,
        consumer_id="consumer",
        layer_hash=content_hash(b"layer"),
        visible_capability_ids=("capability",),
    )

    assert all(result.status == "PASS" for result in results)
    assert session.events[:4] == ["reset", "start", "evidence", "stop"]
    assert all("private_criterion" not in view for view in consumer.views)


def test_evidence_failure_still_attempts_stop_and_is_infrastructure() -> None:
    session = _Session(evidence_fails=True)
    results = _harness(session).run(
        _Consumer(),
        _tasks(),
        seed=0,
        consumer_id="consumer",
        layer_hash=content_hash(b"layer"),
        visible_capability_ids=("capability",),
    )

    assert all(result.status == "INFRASTRUCTURE_ERROR" for result in results)
    assert session.events[:4] == ["reset", "start", "evidence", "stop"]
    assert all(result.video_handle is not None for result in results)
