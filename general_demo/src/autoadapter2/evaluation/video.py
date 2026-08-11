from __future__ import annotations

import json
import math
import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..foundation.canonical import CANONICALIZER_VERSION, canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash


def _nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    return value


def _simulation_time(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field_name} must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ContractError(f"{field_name} must be a finite non-negative number")
    return result


@dataclass(frozen=True)
class FrozenVideoProfile:
    profile_id: str
    profile_version: str
    camera: str
    view: str
    fps: int
    width: int
    height: int
    container: str
    codec: str

    def __post_init__(self) -> None:
        for name in ("profile_id", "profile_version", "camera", "view", "container", "codec"):
            _nonempty(getattr(self, name), name)
        if isinstance(self.fps, bool) or not isinstance(self.fps, int) or self.fps <= 0:
            raise ContractError("fps must be a positive integer")
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ContractError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "camera": self.camera,
            "view": self.view,
            "fps": self.fps,
            "resolution": {"width": self.width, "height": self.height},
            "container": self.container,
            "codec": self.codec,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(canonical_bytes(self.to_dict()))


@dataclass(frozen=True)
class RGBFrame:
    simulation_time_s: float
    width: int
    height: int
    rgb: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "simulation_time_s",
            _simulation_time(self.simulation_time_s, "simulation_time_s"),
        )
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ContractError(f"frame {name} must be a positive integer")
        if not isinstance(self.rgb, bytes):
            raise ContractError("frame rgb must be immutable bytes")
        expected = self.width * self.height * 3
        if len(self.rgb) != expected:
            raise ContractError(f"RGB frame requires exactly {expected} bytes")


class OpaqueVideoHandle:
    """Framework-private media locator whose value is deliberately not exposed."""

    __slots__ = ("__token",)

    def __init__(self, token: str):
        self.__token = _nonempty(token, "opaque video handle token")

    def __repr__(self) -> str:
        return "<OpaqueVideoHandle>"

    def __str__(self) -> str:
        return "<opaque-video-handle>"


@dataclass(frozen=True)
class EncodedVideo:
    """Transient encoder result. Payload bytes never enter the public closure."""

    handle: OpaqueVideoHandle
    payload: bytes = field(repr=False)


class VideoEncodingError(Exception):
    """Encoder infrastructure failure with optional retained partial media."""

    def __init__(self, message: str, *, partial: EncodedVideo | None = None):
        super().__init__(message)
        self.partial = partial


@runtime_checkable
class VideoEncoder(Protocol):
    def encode(
        self, profile: FrozenVideoProfile, frames: Sequence[RGBFrame]
    ) -> EncodedVideo: ...


class VideoInfraCode(str, Enum):
    FRAME_DIMENSION_MISMATCH = "FRAME_DIMENSION_MISMATCH"
    FRAME_TIMESTAMP_DUPLICATE = "FRAME_TIMESTAMP_DUPLICATE"
    FRAME_TIMESTAMP_NON_MONOTONIC = "FRAME_TIMESTAMP_NON_MONOTONIC"
    FRAME_SLOT_DUPLICATE = "FRAME_SLOT_DUPLICATE"
    FRAME_SLOT_MISSING = "FRAME_SLOT_MISSING"
    COVERAGE_START_MISSING = "COVERAGE_START_MISSING"
    COVERAGE_END_MISSING = "COVERAGE_END_MISSING"
    ENCODER_FAILED = "ENCODER_FAILED"
    ENCODED_OUTPUT_INVALID = "ENCODED_OUTPUT_INVALID"


@dataclass(frozen=True)
class VideoInfrastructureFailure:
    code: VideoInfraCode
    message: str
    frame_index: int | None = None
    simulation_time_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "frame_index": self.frame_index,
            "simulation_time_s": self.simulation_time_s,
        }


@dataclass(frozen=True)
class ClosedEvaluationVideo:
    completion_status: str
    execution_disposition: str
    media_content_hash: str | None
    manifest_content_hash: str
    manifest_bytes: bytes = field(repr=False)
    handle: OpaqueVideoHandle | None = field(repr=False)
    failures: tuple[VideoInfrastructureFailure, ...]

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_bytes)

    @property
    def is_infrastructure_error(self) -> bool:
        return self.execution_disposition == "INFRASTRUCTURE_ERROR"


@dataclass(frozen=True)
class _VideoOrigin:
    closure_hash: str
    media_payload: bytes | None
    handle: OpaqueVideoHandle | None


_VIDEO_ORIGINS: dict[
    int, tuple[weakref.ReferenceType[ClosedEvaluationVideo], _VideoOrigin]
] = {}


def _closure_hash(video: ClosedEvaluationVideo) -> str:
    return content_hash(canonical_bytes({
        "completion_status": video.completion_status,
        "execution_disposition": video.execution_disposition,
        "media_content_hash": video.media_content_hash,
        "manifest_content_hash": video.manifest_content_hash,
        "manifest_bytes_hash": content_hash(video.manifest_bytes),
        "handle_present": video.handle is not None,
        "failures": [failure.to_dict() for failure in video.failures],
    }))


def _register_closed_video(
    video: ClosedEvaluationVideo,
    media_payload: bytes | None,
) -> None:
    key = id(video)

    def remove(reference: weakref.ReferenceType[ClosedEvaluationVideo]) -> None:
        current = _VIDEO_ORIGINS.get(key)
        if current is not None and current[0] is reference:
            _VIDEO_ORIGINS.pop(key, None)

    reference = weakref.ref(video, remove)
    _VIDEO_ORIGINS[key] = (
        reference,
        _VideoOrigin(_closure_hash(video), media_payload, video.handle),
    )


def verify_closed_evaluation_video(video: Any) -> bool:
    """Verify that an unmodified closure was issued by this Recorder process."""

    if not isinstance(video, ClosedEvaluationVideo):
        return False
    registered = _VIDEO_ORIGINS.get(id(video))
    if registered is None or registered[0]() is not video:
        return False
    origin = registered[1]
    try:
        if _closure_hash(video) != origin.closure_hash or video.handle is not origin.handle:
            return False
        manifest = video.manifest
    except Exception:
        return False
    top_level = {
        "manifest_type", "manifest_version", "canonicalizer", "closed",
        "recording_id", "bindings", "profile", "profile_content_hash",
        "coverage", "frame_index_content_hash", "media", "completion_status",
        "execution_disposition", "failures",
    }
    profile_fields = {
        "profile_id", "profile_version", "camera", "view", "fps",
        "resolution", "container", "codec",
    }
    coverage_fields = {
        "start_simulation_time_s", "terminal_simulation_time_s",
        "first_frame_simulation_time_s", "last_frame_simulation_time_s",
        "frame_count", "frame_simulation_timestamps_s", "frame_slots",
    }
    media_fields = {"content_hash", "size_bytes", "container", "codec"}
    profile = manifest.get("profile") if isinstance(manifest, dict) else None
    coverage = manifest.get("coverage") if isinstance(manifest, dict) else None
    media = manifest.get("media") if isinstance(manifest, dict) else None
    bindings = manifest.get("bindings") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or set(manifest) != top_level
        or manifest.get("manifest_type") != "framework_evaluation_video"
        or manifest.get("manifest_version") != "0.1.0"
        or manifest.get("canonicalizer") != CANONICALIZER_VERSION
        or manifest.get("closed") is not True
        or not isinstance(manifest.get("recording_id"), str)
        or not manifest["recording_id"].strip()
        or not isinstance(bindings, dict)
        or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value for key, value in bindings.items())
        or not isinstance(profile, dict)
        or set(profile) != profile_fields
        or not isinstance(coverage, dict)
        or set(coverage) != coverage_fields
        or not isinstance(media, dict)
        or set(media) != media_fields
        or manifest.get("profile_content_hash") != content_hash(canonical_bytes(profile))
        or manifest.get("completion_status") != video.completion_status
        or manifest.get("execution_disposition") != video.execution_disposition
        or manifest.get("failures") != [failure.to_dict() for failure in video.failures]
        or media.get("content_hash") != video.media_content_hash
        or content_hash(video.manifest_bytes) != video.manifest_content_hash
    ):
        return False
    payload = origin.media_payload
    if video.media_content_hash is None:
        return payload is None and video.handle is None and media.get("size_bytes") is None
    return (
        isinstance(payload, bytes)
        and bool(payload)
        and content_hash(payload) == video.media_content_hash
        and media.get("size_bytes") == len(payload)
        and video.handle is not None
    )


class EvaluationVideoRecorder:
    """Small Harness-owned frame-integrity and encoding boundary.

    The caller owns rendering and execution identity. This class neither selects a
    camera nor invokes Validation or Demo logic.
    """

    def __init__(
        self,
        *,
        recording_id: str,
        profile: FrozenVideoProfile,
        encoder: VideoEncoder,
        coverage_start_time_s: float,
        bindings: Mapping[str, str] | None = None,
    ):
        self._recording_id = _nonempty(recording_id, "recording_id")
        if not isinstance(profile, FrozenVideoProfile):
            raise ContractError("profile must be a FrozenVideoProfile")
        if not isinstance(encoder, VideoEncoder):
            raise ContractError("encoder does not implement VideoEncoder")
        self._profile = profile
        self._encoder = encoder
        self._start = _simulation_time(coverage_start_time_s, "coverage_start_time_s")
        raw_bindings = dict(bindings or {})
        for key, value in raw_bindings.items():
            _nonempty(key, "binding key")
            _nonempty(value, f"binding {key}")
        self._bindings = dict(sorted(raw_bindings.items()))
        self._frames: list[RGBFrame] = []
        self._failures: list[VideoInfrastructureFailure] = []
        self._closed: ClosedEvaluationVideo | None = None

    def _failure(
        self,
        code: VideoInfraCode,
        message: str,
        frame_index: int | None = None,
        simulation_time_s: float | None = None,
    ) -> None:
        candidate = VideoInfrastructureFailure(code, message, frame_index, simulation_time_s)
        if candidate not in self._failures:
            self._failures.append(candidate)

    def _slot(self, timestamp: float) -> int:
        return math.floor((timestamp - self._start) * self._profile.fps + 0.5)

    def add_frame(self, frame: RGBFrame) -> None:
        if self._closed is not None:
            raise ContractError("a closed evaluation video cannot accept frames")
        if not isinstance(frame, RGBFrame):
            raise ContractError("frame must be an RGBFrame")
        index = len(self._frames)
        if frame.width != self._profile.width or frame.height != self._profile.height:
            self._failure(
                VideoInfraCode.FRAME_DIMENSION_MISMATCH,
                "frame resolution differs from the frozen profile",
                index,
                frame.simulation_time_s,
            )
        if self._frames:
            previous = self._frames[-1]
            if frame.simulation_time_s == previous.simulation_time_s:
                self._failure(
                    VideoInfraCode.FRAME_TIMESTAMP_DUPLICATE,
                    "consecutive frames have the same simulation timestamp",
                    index,
                    frame.simulation_time_s,
                )
            elif frame.simulation_time_s < previous.simulation_time_s:
                self._failure(
                    VideoInfraCode.FRAME_TIMESTAMP_NON_MONOTONIC,
                    "frame simulation timestamps are not monotonic",
                    index,
                    frame.simulation_time_s,
                )
        slot = self._slot(frame.simulation_time_s)
        previous_slots = {self._slot(item.simulation_time_s) for item in self._frames}
        if slot in previous_slots:
            self._failure(
                VideoInfraCode.FRAME_SLOT_DUPLICATE,
                f"more than one frame occupies frozen-fps slot {slot}",
                index,
                frame.simulation_time_s,
            )
        self._frames.append(frame)

    def _validate_coverage(self, terminal_time: float) -> None:
        terminal_slot = max(0, self._slot(terminal_time))
        slots = sorted({self._slot(frame.simulation_time_s) for frame in self._frames})
        half_period = 0.5 / self._profile.fps
        if not self._frames or 0 not in slots:
            self._failure(
                VideoInfraCode.COVERAGE_START_MISSING,
                "no frame covers the post-reset pre-invocation/task boundary",
            )
        if (
            not self._frames
            or max(frame.simulation_time_s for frame in self._frames) + half_period + 1e-12
            < terminal_time
        ):
            self._failure(
                VideoInfraCode.COVERAGE_END_MISSING,
                "no frame covers the declared terminal observation boundary",
            )
        relevant = [slot for slot in slots if 0 <= slot <= terminal_slot]
        previous = -1
        for slot in relevant + [terminal_slot + 1]:
            if slot > previous + 1:
                first_missing = previous + 1
                last_missing = slot - 1
                self._failure(
                    VideoInfraCode.FRAME_SLOT_MISSING,
                    f"missing frozen-fps slots {first_missing}..{last_missing}",
                )
            previous = slot

    def close(self, *, terminal_time_s: float) -> ClosedEvaluationVideo:
        if self._closed is not None:
            return self._closed
        terminal = _simulation_time(terminal_time_s, "terminal_time_s")
        if terminal < self._start:
            raise ContractError("terminal_time_s precedes coverage_start_time_s")
        self._validate_coverage(terminal)

        media_hash: str | None = None
        media_size: int | None = None
        media_payload: bytes | None = None
        handle: OpaqueVideoHandle | None = None
        if self._frames:
            try:
                encoded = self._encoder.encode(self._profile, tuple(self._frames))
            except VideoEncodingError as exc:
                partial = exc.partial
                if (
                    isinstance(partial, EncodedVideo)
                    and isinstance(partial.handle, OpaqueVideoHandle)
                    and isinstance(partial.payload, bytes)
                    and partial.payload
                ):
                    handle = partial.handle
                    media_payload = partial.payload
                    media_size = len(partial.payload)
                    media_hash = content_hash(partial.payload)
                self._failure(
                    VideoInfraCode.ENCODER_FAILED,
                    "encoder reported an infrastructure failure",
                )
            except Exception as exc:
                self._failure(
                    VideoInfraCode.ENCODER_FAILED,
                    f"encoder raised {type(exc).__name__}",
                )
            else:
                if (
                    not isinstance(encoded, EncodedVideo)
                    or not isinstance(encoded.handle, OpaqueVideoHandle)
                    or not isinstance(encoded.payload, bytes)
                    or not encoded.payload
                ):
                    self._failure(
                        VideoInfraCode.ENCODED_OUTPUT_INVALID,
                        "encoder returned no valid opaque handle and non-empty byte payload",
                    )
                else:
                    handle = encoded.handle
                    media_payload = encoded.payload
                    media_size = len(encoded.payload)
                    media_hash = content_hash(encoded.payload)

        frame_index = [
            {
                "simulation_time_s": frame.simulation_time_s,
                "width": frame.width,
                "height": frame.height,
                "rgb_content_hash": content_hash(frame.rgb),
            }
            for frame in self._frames
        ]
        if media_hash is None:
            completion_status = "CAPTURE_FAILED"
        elif self._failures:
            completion_status = "INCOMPLETE"
        else:
            completion_status = "COMPLETE"
        disposition = (
            "EVIDENCE_COMPLETE"
            if completion_status == "COMPLETE"
            else "INFRASTRUCTURE_ERROR"
        )
        manifest = {
            "manifest_type": "framework_evaluation_video",
            "manifest_version": "0.1.0",
            "canonicalizer": CANONICALIZER_VERSION,
            "closed": True,
            "recording_id": self._recording_id,
            "bindings": self._bindings,
            "profile": self._profile.to_dict(),
            "profile_content_hash": self._profile.content_hash,
            "coverage": {
                "start_simulation_time_s": self._start,
                "terminal_simulation_time_s": terminal,
                "first_frame_simulation_time_s": (
                    min(frame.simulation_time_s for frame in self._frames)
                    if self._frames
                    else None
                ),
                "last_frame_simulation_time_s": (
                    max(frame.simulation_time_s for frame in self._frames)
                    if self._frames
                    else None
                ),
                "frame_count": len(self._frames),
                "frame_simulation_timestamps_s": [
                    frame.simulation_time_s for frame in self._frames
                ],
                "frame_slots": [
                    self._slot(frame.simulation_time_s) for frame in self._frames
                ],
            },
            "frame_index_content_hash": content_hash(canonical_bytes(frame_index)),
            "media": {
                "content_hash": media_hash,
                "size_bytes": media_size,
                "container": self._profile.container,
                "codec": self._profile.codec,
            },
            "completion_status": completion_status,
            "execution_disposition": disposition,
            "failures": [failure.to_dict() for failure in self._failures],
        }
        manifest_bytes = canonical_bytes(manifest)
        self._closed = ClosedEvaluationVideo(
            completion_status=completion_status,
            execution_disposition=disposition,
            media_content_hash=media_hash,
            manifest_content_hash=content_hash(manifest_bytes),
            manifest_bytes=manifest_bytes,
            handle=handle,
            failures=tuple(self._failures),
        )
        _register_closed_video(self._closed, media_payload)
        return self._closed
