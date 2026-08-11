from __future__ import annotations

import pytest

from autoadapter2.evaluation import (
    ClosedEvaluationVideo,
    EncodedVideo,
    EvaluationVideoRecorder,
    FrozenVideoProfile,
    OpaqueVideoHandle,
    RGBFrame,
    VideoInfraCode,
    verify_closed_evaluation_video,
)
from autoadapter2.foundation import ContractError, content_hash


class DeterministicFakeEncoder:
    def encode(self, profile, frames):
        payload = b"FAKE\0" + b"|".join(
            f"{frame.simulation_time_s:.6f}".encode() + b":" + frame.rgb
            for frame in frames
        )
        return EncodedVideo(OpaqueVideoHandle("tests-only-video"), payload)


class FailingFakeEncoder:
    def encode(self, profile, frames):
        raise RuntimeError("tests-only encoding failure")


def _profile() -> FrozenVideoProfile:
    return FrozenVideoProfile(
        profile_id="tests-only",
        profile_version="1.0.0",
        camera="external-evaluation",
        view="robot-and-resource",
        fps=10,
        width=2,
        height=1,
        container="fake",
        codec="fake-rgb",
    )


def _frame(timestamp: float, value: int = 1, width: int = 2) -> RGBFrame:
    return RGBFrame(timestamp, width, 1, bytes([value]) * width * 3)


def _recorder(encoder=None, start=0.0):
    return EvaluationVideoRecorder(
        recording_id="attempt-1",
        profile=_profile(),
        encoder=encoder or DeterministicFakeEncoder(),
        coverage_start_time_s=start,
        bindings={"run_id": "run-1", "execution": "case-1-repetition-0"},
    )


def test_complete_video_hashes_media_and_closes_immutable_manifest():
    recorder = _recorder()
    recorder.add_frame(_frame(0.0, 1))
    recorder.add_frame(_frame(0.1, 2))
    recorder.add_frame(_frame(0.2, 3))
    result = recorder.close(terminal_time_s=0.2)

    assert result.completion_status == "COMPLETE"
    assert result.execution_disposition == "EVIDENCE_COMPLETE"
    assert result.media_content_hash is not None
    assert result.manifest_content_hash == content_hash(result.manifest_bytes)
    assert result.manifest["closed"] is True
    assert result.manifest["profile_content_hash"] == _profile().content_hash
    assert result.manifest["media"]["content_hash"] == result.media_content_hash
    assert result.manifest["coverage"]["frame_simulation_timestamps_s"] == [0.0, 0.1, 0.2]
    assert result.manifest["coverage"]["frame_slots"] == [0, 1, 2]
    assert result.manifest["failures"] == []
    assert repr(result.handle) == "<OpaqueVideoHandle>"
    assert "FAKE" not in result.manifest_bytes.decode()
    assert verify_closed_evaluation_video(result)
    assert recorder.close(terminal_time_s=99.0) is result
    with pytest.raises(ContractError):
        recorder.add_frame(_frame(0.3))


def test_missing_slot_is_typed_infrastructure_and_partial_media_is_retained():
    recorder = _recorder()
    recorder.add_frame(_frame(0.0))
    recorder.add_frame(_frame(0.2))
    result = recorder.close(terminal_time_s=0.2)

    assert result.completion_status == "INCOMPLETE"
    assert result.is_infrastructure_error
    assert result.media_content_hash is not None
    assert result.handle is not None
    assert VideoInfraCode.FRAME_SLOT_MISSING in {failure.code for failure in result.failures}


def test_duplicate_and_non_monotonic_timestamps_are_typed_infrastructure():
    duplicate = _recorder()
    duplicate.add_frame(_frame(0.0))
    duplicate.add_frame(_frame(0.0))
    duplicate_result = duplicate.close(terminal_time_s=0.0)
    duplicate_codes = {failure.code for failure in duplicate_result.failures}
    assert VideoInfraCode.FRAME_TIMESTAMP_DUPLICATE in duplicate_codes
    assert VideoInfraCode.FRAME_SLOT_DUPLICATE in duplicate_codes

    reversed_recorder = _recorder()
    reversed_recorder.add_frame(_frame(0.1))
    reversed_recorder.add_frame(_frame(0.0))
    reversed_result = reversed_recorder.close(terminal_time_s=0.1)
    assert VideoInfraCode.FRAME_TIMESTAMP_NON_MONOTONIC in {
        failure.code for failure in reversed_result.failures
    }


def test_start_end_and_resolution_coverage_failures_are_infrastructure():
    recorder = _recorder()
    recorder.add_frame(_frame(0.1, width=1))
    result = recorder.close(terminal_time_s=0.3)
    codes = {failure.code for failure in result.failures}
    assert VideoInfraCode.FRAME_DIMENSION_MISMATCH in codes
    assert VideoInfraCode.COVERAGE_START_MISSING in codes
    assert VideoInfraCode.COVERAGE_END_MISSING in codes
    assert result.execution_disposition == "INFRASTRUCTURE_ERROR"


def test_encoder_failure_is_capture_failed_without_fake_media_claim():
    recorder = _recorder(FailingFakeEncoder())
    recorder.add_frame(_frame(0.0))
    result = recorder.close(terminal_time_s=0.0)

    assert result.completion_status == "CAPTURE_FAILED"
    assert result.media_content_hash is None
    assert result.handle is None
    assert result.manifest["media"]["content_hash"] is None
    assert [failure.code for failure in result.failures] == [VideoInfraCode.ENCODER_FAILED]


def test_no_frames_is_typed_infrastructure_and_invalid_rgb_is_contract_error():
    result = _recorder().close(terminal_time_s=0.2)
    assert result.completion_status == "CAPTURE_FAILED"
    assert result.is_infrastructure_error
    codes = {failure.code for failure in result.failures}
    assert VideoInfraCode.COVERAGE_START_MISSING in codes
    assert VideoInfraCode.COVERAGE_END_MISSING in codes
    assert VideoInfraCode.FRAME_SLOT_MISSING in codes

    with pytest.raises(ContractError):
        RGBFrame(0.0, 2, 1, b"too-short")


def test_terminal_before_start_is_contract_error():
    recorder = _recorder(start=1.0)
    with pytest.raises(ContractError):
        recorder.close(terminal_time_s=0.9)


def test_handmade_or_mutated_video_closure_is_not_framework_evidence():
    recorder = _recorder()
    recorder.add_frame(_frame(0.0))
    genuine = recorder.close(terminal_time_s=0.0)
    forged = ClosedEvaluationVideo(
        completion_status=genuine.completion_status,
        execution_disposition=genuine.execution_disposition,
        media_content_hash=genuine.media_content_hash,
        manifest_content_hash=genuine.manifest_content_hash,
        manifest_bytes=genuine.manifest_bytes,
        handle=genuine.handle,
        failures=genuine.failures,
    )
    assert not verify_closed_evaluation_video(forged)
    object.__setattr__(genuine, "completion_status", "INCOMPLETE")
    assert not verify_closed_evaluation_video(genuine)
