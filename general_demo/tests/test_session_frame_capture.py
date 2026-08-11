from __future__ import annotations

import math

import pytest

from autoadapter2.evaluation import FrozenVideoProfile
from autoadapter2.integrations.session_support import FrameCaptureError, MuJoCoFrameCapture


def _profile() -> FrozenVideoProfile:
    return FrozenVideoProfile(
        profile_id="capture",
        profile_version="1.0.0",
        camera="external",
        view="robot-and-resource",
        fps=10,
        width=2,
        height=1,
        container="fake",
        codec="rgb",
    )


class _Renderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self) -> bytes:
        self.calls += 1
        return bytes([self.calls]) * 6


def test_capture_is_simulation_time_driven_and_fills_deterministic_slots() -> None:
    renderer = _Renderer()
    capture = MuJoCoFrameCapture(_profile(), renderer)

    capture.start(0.0)
    capture.on_step(0.05)
    capture.on_step(0.11)
    capture.on_step(0.31)
    frames = capture.stop(0.35)

    assert [frame.simulation_time_s for frame in frames] == [0.0, 0.1, 0.2, 0.3, 0.35]
    assert [math.floor(frame.simulation_time_s * 10 + 0.5) for frame in frames] == [0, 1, 2, 3, 4]
    assert [frame.rgb[0] for frame in frames] == [1, 2, 3, 4, 5]
    assert renderer.calls == 5


def test_capture_rejects_non_monotonic_or_invalid_simulation_time() -> None:
    capture = MuJoCoFrameCapture(_profile(), lambda: b"\x00" * 6)
    capture.start(0.0)
    capture.on_step(0.2)
    with pytest.raises(FrameCaptureError):
        capture.on_step(0.1)

    other = MuJoCoFrameCapture(_profile(), lambda: b"\x00" * 6)
    with pytest.raises(FrameCaptureError):
        other.start(math.nan)


@pytest.mark.parametrize("rendered", [None, b"short"])
def test_capture_rejects_missing_or_invalid_render_frames(rendered) -> None:
    with pytest.raises(FrameCaptureError):
        MuJoCoFrameCapture(_profile(), lambda: rendered).start(0.0)


def test_capture_requires_lifecycle_and_returns_terminal_frame() -> None:
    capture = MuJoCoFrameCapture(_profile(), lambda: b"\x01" * 6)
    with pytest.raises(FrameCaptureError):
        capture.on_step(0.1)
    with pytest.raises(FrameCaptureError):
        capture.stop(0.1)

    capture.start(0.0)
    frames = capture.stop(0.02)
    assert frames[-1].simulation_time_s == 0.02
    assert capture.stop(0.5) == frames
