"""Shared Harness support for SDK-grounded MuJoCo evaluation sessions.

Robot-specific sessions own the MuJoCo model, SDK, and renderer.  This module
only turns renderer output into deterministic, simulation-time-indexed RGB
frames for the Framework Evaluation Video recorder.
"""

from __future__ import annotations

import math
import inspect
from collections.abc import Callable
from typing import Any

from ..evaluation import FrozenVideoProfile, RGBFrame
from ..foundation.errors import ContractError


class FrameCaptureError(ContractError):
    """A frame capture lifecycle or renderer contract was violated."""


MuJoCoFrameCaptureError = FrameCaptureError


# A renderer normally has a zero-argument ``render`` method.  Keeping the
# callback type broad lets a real MuJoCo adapter supply either a callable or a
# renderer object without making this shared module depend on MuJoCo.
RenderFrame = Callable[[], Any]


def _simulation_time(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameCaptureError(f"{label} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise FrameCaptureError(f"{label} must be a finite non-negative number")
    return result


class MuJoCoFrameCapture:
    """Capture one deterministic external-view frame stream from a renderer.

    ``simulation_time_s`` is supplied by the Session Runner.  Wall-clock time
    is never consulted.  A renderer may return an :class:`RGBFrame` or raw RGB
    bytes; the capture object owns the output timestamp and profile dimensions.
    """

    def __init__(
        self,
        profile: FrozenVideoProfile,
        renderer: object | None = None,
        *,
        render: object | None = None,
    ):
        if not isinstance(profile, FrozenVideoProfile):
            raise FrameCaptureError("frame capture requires one FrozenVideoProfile")
        if renderer is not None and render is not None:
            raise FrameCaptureError("frame capture accepts only one renderer")
        renderer = renderer if renderer is not None else render
        if callable(renderer):
            callback = renderer
        else:
            callback = getattr(renderer, "render", None)
        if not callable(callback):
            raise FrameCaptureError("frame capture renderer must be callable")
        self._profile = profile
        self._renderer: RenderFrame = callback
        self._period_s = 1.0 / profile.fps
        self._start_time_s: float | None = None
        self._last_time_s: float | None = None
        self._next_slot = 1
        self._frames_by_slot: dict[int, RGBFrame] = {}
        self._closed_frames: tuple[RGBFrame, ...] | None = None

    @property
    def profile(self) -> FrozenVideoProfile:
        return self._profile

    @property
    def started(self) -> bool:
        return self._start_time_s is not None and self._closed_frames is None

    def start(self, simulation_time_s: float) -> None:
        """Start at reset and immediately capture the reset frame."""

        if self._start_time_s is not None:
            raise FrameCaptureError("frame capture has already started")
        start = _simulation_time(simulation_time_s, "reset simulation time")
        self._start_time_s = start
        self._last_time_s = start
        self._frames_by_slot[0] = self._render(start)

    def on_step(self, simulation_time_s: float) -> None:
        """Fill every FPS slot reached by the current simulation time."""

        now = self._require_running(simulation_time_s)
        assert self._start_time_s is not None
        if self._last_time_s is not None and now < self._last_time_s:
            raise FrameCaptureError("simulation time moved backwards during capture")
        self._last_time_s = now
        while self._start_time_s + self._next_slot * self._period_s <= now + 1e-12:
            slot = self._next_slot
            due_time = round(self._start_time_s + slot * self._period_s, 12)
            self._frames_by_slot[slot] = self._render(due_time)
            self._next_slot += 1

    def stop(self, simulation_time_s: float) -> tuple[RGBFrame, ...]:
        """Capture the terminal observation and return ordered RGB frames."""

        if self._closed_frames is not None:
            return self._closed_frames
        terminal = self._require_running(simulation_time_s)
        self.on_step(terminal)
        assert self._start_time_s is not None
        terminal_frame = self._render(terminal)
        terminal_slot = self._slot(terminal)
        # The terminal timestamp can fall in the same frozen-FPS slot as the
        # last scheduled frame.  Replacing that frame preserves one frame per
        # slot while ensuring the returned stream reaches the terminal state.
        self._frames_by_slot[terminal_slot] = terminal_frame
        frames = tuple(self._frames_by_slot[index] for index in sorted(self._frames_by_slot))
        self._closed_frames = frames
        return frames

    def _require_running(self, simulation_time_s: float) -> float:
        if self._start_time_s is None or self._closed_frames is not None:
            raise FrameCaptureError("frame capture is not running")
        return _simulation_time(simulation_time_s, "simulation time")

    def _slot(self, simulation_time_s: float) -> int:
        assert self._start_time_s is not None
        return max(0, math.floor((simulation_time_s - self._start_time_s) * self._profile.fps + 0.5))

    def _render(self, timestamp_s: float) -> RGBFrame:
        try:
            try:
                parameters = inspect.signature(self._renderer).parameters.values()
                accepts_time = any(
                    parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                accepts_time = False
            raw = self._renderer(timestamp_s) if accepts_time else self._renderer()
        except Exception as exc:
            raise FrameCaptureError("MuJoCo renderer failed to return a frame") from exc
        if raw is None:
            raise FrameCaptureError("MuJoCo renderer returned no frame")
        if isinstance(raw, RGBFrame):
            width, height, rgb = raw.width, raw.height, raw.rgb
        else:
            width, height, rgb = self._profile.width, self._profile.height, self._rgb_bytes(raw)
        if width != self._profile.width or height != self._profile.height:
            raise FrameCaptureError("rendered frame resolution differs from FrozenVideoProfile")
        try:
            return RGBFrame(timestamp_s, width, height, rgb)
        except Exception as exc:
            raise FrameCaptureError("MuJoCo renderer returned an invalid RGB frame") from exc

    def _rgb_bytes(self, raw: object) -> bytes:
        if isinstance(raw, bytes):
            payload = raw
        elif isinstance(raw, (bytearray, memoryview)):
            payload = bytes(raw)
        else:
            # MuJoCo renderers commonly return an array-like object.  Avoid a
            # NumPy dependency here while accepting its immutable byte view.
            tobytes = getattr(raw, "tobytes", None)
            if not callable(tobytes):
                raise FrameCaptureError("renderer output must be RGB bytes or RGBFrame")
            try:
                payload = tobytes()
            except Exception as exc:
                raise FrameCaptureError("renderer output could not be converted to RGB bytes") from exc
            if not isinstance(payload, bytes):
                raise FrameCaptureError("renderer byte conversion did not return bytes")
        expected = self._profile.width * self._profile.height * 3
        if len(payload) != expected:
            raise FrameCaptureError(f"renderer returned {len(payload)} RGB bytes; expected {expected}")
        return payload


__all__ = [
    "FrameCaptureError",
    "MuJoCoFrameCapture",
    "MuJoCoFrameCaptureError",
    "RenderFrame",
]
