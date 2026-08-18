"""Small Framework-owned video encoding and completeness checks."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


_MAX_DIAGNOSTIC_CHARS = 2_000


class VideoError(RuntimeError):
    """Raised when a required video cannot be encoded or verified."""


@dataclass(frozen=True, slots=True)
class VideoInfo:
    """Facts reported for one successfully decoded video file."""

    frame_count: int
    width: int
    height: int
    duration: float
    decodable: bool
    complete: bool


def _diagnostic(value: bytes | str | None) -> str:
    if value is None:
        return "no diagnostic output"
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    if not text:
        return "no diagnostic output"
    if len(text) > _MAX_DIAGNOSTIC_CHARS:
        return text[:_MAX_DIAGNOSTIC_CHARS] + " ...[truncated]"
    return text


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise VideoError(f"required executable is unavailable: {name}")
    return executable


def _path(value: str | Path, *, field: str) -> Path:
    try:
        result = Path(value)
    except TypeError as exc:
        raise VideoError(f"{field} must be a filesystem path") from exc
    if result.exists() and result.is_dir():
        raise VideoError(f"{field} must name a file, not a directory: {result}")
    return result


def _fps_value(fps: float) -> float:
    if isinstance(fps, bool):
        raise VideoError("fps must be a positive finite number")
    try:
        value = float(fps)
    except (TypeError, ValueError) as exc:
        raise VideoError("fps must be a positive finite number") from exc
    if not math.isfinite(value) or value <= 0:
        raise VideoError("fps must be a positive finite number")
    return value


def _validated_frames(frames: Iterable[np.ndarray]) -> tuple[np.ndarray, ...]:
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise VideoError("frames must be a finite iterable of RGB arrays") from exc

    normalized: list[np.ndarray] = []
    expected_shape: tuple[int, int, int] | None = None
    index = 0
    while True:
        try:
            frame = next(iterator)
        except StopIteration:
            break
        except Exception as exc:
            raise VideoError(f"failed while reading frame {index}: {exc}") from exc

        try:
            array = np.asarray(frame)
        except (TypeError, ValueError) as exc:
            raise VideoError(f"frame {index} is not an array") from exc
        if array.dtype != np.dtype(np.uint8):
            raise VideoError(f"frame {index} must have dtype uint8, got {array.dtype}")
        if array.ndim != 3 or array.shape[2] != 3:
            raise VideoError(
                f"frame {index} must have shape HxWx3, got {tuple(array.shape)}"
            )
        if array.shape[0] <= 0 or array.shape[1] <= 0:
            raise VideoError(f"frame {index} must have positive height and width")
        shape = (int(array.shape[0]), int(array.shape[1]), 3)
        if expected_shape is None:
            expected_shape = shape
        elif shape != expected_shape:
            raise VideoError(
                f"frame {index} shape {shape} differs from first frame {expected_shape}"
            )
        normalized.append(np.ascontiguousarray(array))
        index += 1

    if not normalized:
        raise VideoError("frames must contain at least one frame; received zero frames")
    return tuple(normalized)


def encode_rgb_video(
    frames: Iterable[np.ndarray],
    output_path: str | Path,
    fps: float,
) -> Path:
    """Encode finite uint8 RGB frames to one MP4 through ffmpeg stdin."""

    output = _path(output_path, field="output_path")
    fps_value = _fps_value(fps)
    arrays = _validated_frames(frames)
    height, width, _ = arrays[0].shape
    raw_rgb = b"".join(frame.tobytes(order="C") for frame in arrays)
    command = [
        _tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps_value:.12g}",
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv444p",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-y",
        str(output),
    ]
    try:
        result = subprocess.run(
            command,
            input=raw_rgb,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise VideoError(f"ffmpeg could not start: {exc}") from exc
    if result.returncode != 0:
        raise VideoError(
            f"ffmpeg failed with exit code {result.returncode}: "
            f"{_diagnostic(result.stderr)}"
        )
    try:
        size = output.stat().st_size
    except OSError as exc:
        raise VideoError(f"ffmpeg produced no readable output at {output}") from exc
    if size <= 0:
        raise VideoError(f"ffmpeg produced an empty output file at {output}")
    return output


def _positive_int(value: object, *, field: str) -> int:
    try:
        result = int(value)  # ffprobe emits numeric fields as JSON strings.
    except (TypeError, ValueError) as exc:
        raise VideoError(f"ffprobe returned an invalid {field}: {value!r}") from exc
    if result <= 0:
        raise VideoError(f"ffprobe returned a non-positive {field}: {result}")
    return result


def _duration(stream: dict[str, object], format_info: dict[str, object]) -> float:
    for source in (stream, format_info):
        value = source.get("duration")
        if value is None or value == "N/A":
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(result) and result > 0:
            return result
    raise VideoError("ffprobe returned no positive finite video duration")


def _frame_count(stream: dict[str, object]) -> int:
    for field in ("nb_read_frames", "nb_frames"):
        value = stream.get(field)
        if value is None or value == "N/A":
            continue
        try:
            result = int(value)
        except (TypeError, ValueError):
            continue
        if result > 0:
            return result
    raise VideoError("ffprobe returned no positive decoded frame count")


def _run_decode(path: Path) -> None:
    command = [
        _tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-xerror",
        "-nostdin",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise VideoError(f"ffmpeg decoder could not start: {exc}") from exc
    if result.returncode != 0:
        raise VideoError(
            f"video is undecodable or incomplete (ffmpeg exit code "
            f"{result.returncode}): {_diagnostic(result.stderr)}"
        )


def inspect_video(path: str | Path) -> VideoInfo:
    """Probe and fully decode one video, failing unless it is complete."""

    video = _path(path, field="path")
    try:
        size = video.stat().st_size
    except OSError as exc:
        raise VideoError(f"video file is missing or unreadable: {video}") from exc
    if size <= 0:
        raise VideoError(f"video file is empty: {video}")

    command = [
        _tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=codec_type,width,height,nb_read_frames,nb_frames,duration:format=duration",
        "-of",
        "json",
        str(video),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise VideoError(f"ffprobe could not start: {exc}") from exc
    if result.returncode != 0:
        raise VideoError(
            f"ffprobe failed with exit code {result.returncode}: "
            f"{_diagnostic(result.stderr)}"
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise VideoError("ffprobe found no video stream")
    stream = streams[0]
    format_info = payload.get("format", {})
    if not isinstance(format_info, dict):
        format_info = {}
    if stream.get("codec_type") not in (None, "video"):
        raise VideoError("ffprobe selected a non-video stream")

    frame_count = _frame_count(stream)
    declared_count = stream.get("nb_frames")
    if declared_count not in (None, "N/A"):
        try:
            if int(declared_count) != frame_count:
                raise VideoError(
                    "ffprobe frame counts disagree: "
                    f"decoded={frame_count}, declared={declared_count}"
                )
        except (TypeError, ValueError) as exc:
            raise VideoError(f"ffprobe returned an invalid declared frame count") from exc
    width = _positive_int(stream.get("width"), field="width")
    height = _positive_int(stream.get("height"), field="height")
    duration = _duration(stream, format_info)
    _run_decode(video)
    return VideoInfo(
        frame_count=frame_count,
        width=width,
        height=height,
        duration=duration,
        decodable=True,
        complete=True,
    )


__all__ = ["VideoError", "VideoInfo", "encode_rgb_video", "inspect_video"]
