from __future__ import annotations

from pathlib import Path

import pytest

from autoadapter2.evaluation import (
    EvaluationVideoRecorder,
    FrozenVideoProfile,
    RGBFrame,
    VideoInfraCode,
    VideoEncodingError,
)
from autoadapter2.evaluation.ffmpeg import CommandResult, FFmpegVideoEncoder
from autoadapter2.foundation import ContractError, ImmutableError, content_hash


class WritingRunner:
    def __init__(self, *, returncode=0, payload=b"real-container-bytes"):
        self.returncode = returncode
        self.payload = payload
        self.calls = []

    def run(self, argv, stdin):
        self.calls.append((argv, stdin))
        Path(argv[-1]).write_bytes(self.payload)
        return CommandResult(self.returncode, b"", b"tests-only stderr")


class NoOutputRunner:
    def run(self, argv, stdin):
        return CommandResult(0)


def _profile(container="matroska", codec="ffv1"):
    return FrozenVideoProfile(
        profile_id="tests-only",
        profile_version="1.0.0",
        camera="external",
        view="robot-and-scene",
        fps=20,
        width=2,
        height=1,
        container=container,
        codec=codec,
    )


def _frames():
    return (
        RGBFrame(0.0, 2, 1, b"\x01" * 6),
        RGBFrame(0.05, 2, 1, b"\x02" * 6),
    )


def test_ffmpeg_encoder_uses_argv_rgb24_profile_and_reads_real_output(tmp_path):
    runner = WritingRunner()
    encoder = FFmpegVideoEncoder(tmp_path / "attempt-1", command_runner=runner)
    encoded = encoder.encode(_profile(), _frames())

    assert encoded.payload == b"real-container-bytes"
    assert repr(encoded.handle) == "<OpaqueVideoHandle>"
    assert len(runner.calls) == 1
    argv, stdin = runner.calls[0]
    assert isinstance(argv, list)
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-pixel_format") + 1] == "rgb24"
    assert argv[argv.index("-video_size") + 1] == "2x1"
    assert argv[argv.index("-framerate") + 1] == "20"
    assert argv[argv.index("-c:v") + 1] == "ffv1"
    assert argv[argv.index("-f", argv.index("-c:v")) + 1] == "matroska"
    assert "-n" in argv
    assert stdin == b"\x01" * 6 + b"\x02" * 6
    assert Path(argv[-1]).read_bytes() == encoded.payload


def test_existing_attempt_directory_refuses_overwrite_before_process(tmp_path):
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    existing = attempt / "evaluation-video.matroska"
    existing.write_bytes(b"do-not-change")
    runner = WritingRunner()

    with pytest.raises(ImmutableError):
        FFmpegVideoEncoder(attempt, command_runner=runner).encode(_profile(), _frames())
    assert runner.calls == []
    assert existing.read_bytes() == b"do-not-change"


def test_nonzero_exit_preserves_partial_and_recorder_reports_typed_infra(tmp_path):
    runner = WritingRunner(returncode=7, payload=b"partial-media")
    encoder = FFmpegVideoEncoder(tmp_path / "attempt-1", command_runner=runner)
    recorder = EvaluationVideoRecorder(
        recording_id="recording-1",
        profile=_profile(),
        encoder=encoder,
        coverage_start_time_s=0.0,
    )
    for frame in _frames():
        recorder.add_frame(frame)
    result = recorder.close(terminal_time_s=0.05)

    assert result.completion_status == "INCOMPLETE"
    assert result.is_infrastructure_error
    assert result.media_content_hash == content_hash(b"partial-media")
    assert result.handle is not None
    assert VideoInfraCode.ENCODER_FAILED in {failure.code for failure in result.failures}
    assert (tmp_path / "attempt-1" / "evaluation-video.matroska").read_bytes() == b"partial-media"


def test_success_without_output_is_encoding_error(tmp_path):
    encoder = FFmpegVideoEncoder(
        tmp_path / "attempt-1", command_runner=NoOutputRunner()
    )
    with pytest.raises(VideoEncodingError) as caught:
        encoder.encode(_profile(), _frames())
    assert caught.value.partial is None


def test_unsafe_format_token_and_wrong_frame_size_are_rejected(tmp_path):
    with pytest.raises(ContractError):
        FFmpegVideoEncoder(tmp_path / "attempt-1", command_runner=WritingRunner()).encode(
            _profile(container="../escape"), _frames()
        )

    wrong = (RGBFrame(0.0, 1, 1, b"\x00" * 3),)
    with pytest.raises(VideoEncodingError):
        FFmpegVideoEncoder(tmp_path / "attempt-2", command_runner=WritingRunner()).encode(
            _profile(), wrong
        )
