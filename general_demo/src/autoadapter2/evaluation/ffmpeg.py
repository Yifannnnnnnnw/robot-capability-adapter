from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from ..foundation.errors import ContractError, ImmutableError
from .video import (
    EncodedVideo,
    FrozenVideoProfile,
    OpaqueVideoHandle,
    RGBFrame,
    VideoEncodingError,
)


_FORMAT_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class CommandRunner(Protocol):
    def run(self, argv: list[str], stdin: bytes) -> CommandResult: ...


class SubprocessCommandRunner:
    """Production process seam. It never invokes a shell."""

    def run(self, argv: list[str], stdin: bytes) -> CommandResult:
        completed = subprocess.run(
            argv,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class FFmpegVideoEncoder:
    """Encode one RGB24 frame sequence into a new attempt directory."""

    def __init__(
        self,
        attempt_directory: str | Path,
        *,
        executable: str = "ffmpeg",
        command_runner: CommandRunner | None = None,
    ):
        self._attempt_directory = Path(attempt_directory)
        if not isinstance(executable, str) or not executable.strip():
            raise ContractError("ffmpeg executable must be a non-empty string")
        self._executable = executable
        self._runner = command_runner or SubprocessCommandRunner()

    @staticmethod
    def _token(value: str, name: str) -> str:
        if not isinstance(value, str) or not _FORMAT_TOKEN.fullmatch(value):
            raise ContractError(f"video {name} is not a safe ffmpeg token")
        return value

    @staticmethod
    def _partial(output_path: Path) -> EncodedVideo | None:
        if output_path.is_symlink() or not output_path.is_file():
            return None
        payload = output_path.read_bytes()
        if not payload:
            return None
        return EncodedVideo(OpaqueVideoHandle(f"file:{output_path}"), payload)

    def encode(
        self, profile: FrozenVideoProfile, frames: Sequence[RGBFrame]
    ) -> EncodedVideo:
        if not isinstance(profile, FrozenVideoProfile):
            raise ContractError("profile must be a FrozenVideoProfile")
        frame_tuple = tuple(frames)
        if not frame_tuple:
            raise VideoEncodingError("ffmpeg cannot encode an empty frame sequence")
        if any(
            not isinstance(frame, RGBFrame)
            or frame.width != profile.width
            or frame.height != profile.height
            for frame in frame_tuple
        ):
            raise VideoEncodingError("RGB frame dimensions do not match the frozen profile")

        container = self._token(profile.container, "container")
        codec = self._token(profile.codec, "codec")
        try:
            self._attempt_directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ImmutableError("video attempt directory already exists; overwrite refused") from exc
        output_path = self._attempt_directory / f"evaluation-video.{container}"
        argv = [
            self._executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{profile.width}x{profile.height}",
            "-framerate",
            str(profile.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            codec,
            "-f",
            container,
            str(output_path),
        ]
        stdin = b"".join(frame.rgb for frame in frame_tuple)
        try:
            result = self._runner.run(argv, stdin)
        except Exception as exc:
            raise VideoEncodingError(
                f"ffmpeg runner raised {type(exc).__name__}",
                partial=self._partial(output_path),
            ) from exc
        if not isinstance(result, CommandResult):
            raise VideoEncodingError(
                "ffmpeg runner returned an invalid result",
                partial=self._partial(output_path),
            )
        partial = self._partial(output_path)
        if result.returncode != 0:
            raise VideoEncodingError(
                f"ffmpeg exited with code {result.returncode}", partial=partial
            )
        if partial is None:
            raise VideoEncodingError("ffmpeg produced no non-empty regular media file")
        return partial


__all__ = [
    "CommandResult",
    "CommandRunner",
    "FFmpegVideoEncoder",
    "SubprocessCommandRunner",
]
