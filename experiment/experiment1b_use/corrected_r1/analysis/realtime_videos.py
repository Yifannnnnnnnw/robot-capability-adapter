#!/usr/bin/env python3
"""Create 20-fps realtime-speed copies of the 19 retained 10-fps videos."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CORRECTED_ROOT = HERE.parent
REPOSITORY_ROOT = HERE.parents[3]
DEFAULT_MANIFEST = CORRECTED_ROOT / "config/manifest.json"
DEFAULT_OUTPUT_DIR = HERE / "realtime_videos"
DEFAULT_INDEX = HERE / "realtime_video_index.json"
AUDIT_IDENTITY = {
    "document_id": "AA2-B2-CORRECTED-R1",
    "revision": "1.0.0",
}


class RealtimeVideoError(RuntimeError):
    """Raised when a retained video cannot be safely converted or verified."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealtimeVideoError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RealtimeVideoError(f"{label} must contain one JSON object: {path}")
    return value


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.resolve())


def _repo_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RealtimeVideoError("evidence path is absent")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _rate(raw: str) -> float:
    numerator, separator, denominator = raw.partition("/")
    try:
        value = float(numerator) / float(denominator) if separator else float(raw)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise RealtimeVideoError(f"invalid ffprobe frame rate: {raw!r}") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise RealtimeVideoError(f"invalid ffprobe frame rate: {raw!r}")
    return value


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,width,height,nb_frames:format=duration",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RealtimeVideoError(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    try:
        value = json.loads(completed.stdout)
        stream = value["streams"][0]
        duration = float(value["format"]["duration"])
        fps = _rate(str(stream["avg_frame_rate"]))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RealtimeVideoError(f"ffprobe output is incomplete for {path}") from exc
    return {
        "duration_s": duration,
        "fps": fps,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "frame_count": (
            int(stream["nb_frames"])
            if str(stream.get("nb_frames", "")).isdigit()
            else None
        ),
    }


def _episode_video(unit: Mapping[str, Any]) -> tuple[Path, float, dict[str, Any]]:
    terminal_path = _repo_path(unit.get("original_terminal_path"))
    terminal = _read_object(terminal_path, label="retained terminal")
    episode_path = _repo_path(terminal.get("episode_record_path"))
    episode_record = _read_object(episode_path, label="retained episode record")
    episode = episode_record.get("episode")
    if not isinstance(episode, Mapping):
        raise RealtimeVideoError(f"episode result is absent: {episode_path}")
    episode_meta = episode.get("episode")
    worker = episode.get("worker")
    if not isinstance(episode_meta, Mapping) or not isinstance(worker, Mapping):
        raise RealtimeVideoError(f"episode/video evidence is absent: {episode_path}")
    physical = worker.get("physical_evidence")
    if not isinstance(physical, Mapping):
        raise RealtimeVideoError(f"physical evidence is absent: {episode_path}")
    steps = physical.get("step_count")
    timestep = physical.get("physics_timestep_s")
    if (
        isinstance(steps, bool)
        or not isinstance(steps, int)
        or steps < 0
        or isinstance(timestep, bool)
        or not isinstance(timestep, (int, float))
        or float(timestep) <= 0.0
    ):
        raise RealtimeVideoError(f"simulation duration evidence is invalid: {episode_path}")
    video_path = _repo_path(episode_meta.get("video_path"))
    if not video_path.is_file():
        raise RealtimeVideoError(f"retained source video is absent: {video_path}")
    return video_path, steps * float(timestep), {
        "terminal_path": _relative(terminal_path),
        "episode_record_path": _relative(episode_path),
    }


def _convert(
    *, source: Path, output: Path, ffmpeg: str, ffprobe: str, force: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_before = source.stat()
    source_probe = _probe(source, ffprobe)
    if abs(source_probe["fps"] - 10.0) > 0.01:
        raise RealtimeVideoError(
            f"retained source video is not the expected 10 fps: {source}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if force or not output.exists():
        temporary = output.with_name(f".{output.stem}.tmp.mp4")
        if temporary.exists():
            temporary.unlink()
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            "setpts=0.5*PTS,fps=20",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise RealtimeVideoError(
                f"ffmpeg failed for {source}: {completed.stderr.strip()}"
            )
        temporary.replace(output)
    output_probe = _probe(output, ffprobe)
    source_after = source.stat()
    if (
        source_before.st_size != source_after.st_size
        or source_before.st_mtime_ns != source_after.st_mtime_ns
    ):
        raise RealtimeVideoError(f"source video changed during conversion: {source}")
    if abs(output_probe["fps"] - 20.0) > 0.01:
        raise RealtimeVideoError(f"realtime copy is not 20 fps: {output}")
    return source_probe, output_probe


def create_realtime_videos(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    index_path: Path = DEFAULT_INDEX,
    force: bool = False,
) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise RealtimeVideoError("ffmpeg and ffprobe are required")
    manifest = _read_object(manifest_path.resolve(), label="corrected manifest")
    retained = manifest.get("retained_units")
    if (
        manifest.get("audit_identity") != AUDIT_IDENTITY
        or manifest.get("formal_episode") is not False
        or not isinstance(retained, list)
        or len(retained) != 19
    ):
        raise RealtimeVideoError("corrected manifest does not declare 19 retained units")
    records: list[dict[str, Any]] = []
    for unit in retained:
        if not isinstance(unit, Mapping):
            raise RealtimeVideoError("retained unit is invalid")
        source, sim_duration, source_evidence = _episode_video(unit)
        output = output_dir.resolve() / f"{str(unit['unit_id']).replace('::', '__')}.mp4"
        source_probe, output_probe = _convert(
            source=source,
            output=output,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            force=force,
        )
        tolerance = max(0.10, 1.5 / 20.0)
        duration_matches = abs(output_probe["duration_s"] - sim_duration) <= tolerance
        if not duration_matches:
            raise RealtimeVideoError(
                f"realtime duration does not match simulation for {unit['unit_id']}: "
                f"{output_probe['duration_s']:.3f}s vs {sim_duration:.3f}s"
            )
        records.append(
            {
                "unit_id": unit["unit_id"],
                "robot_configuration_id": unit["robot_configuration_id"],
                "task_id": unit["task_id"],
                "model_id": unit["model_id"],
                "replicate_id": unit["replicate_id"],
                "execution_origin": "retained_rejudged",
                "source_video_path": _relative(source),
                "source_video_preserved": True,
                "source_probe": source_probe,
                "simulation_duration_s": sim_duration,
                "realtime_copy_path": _relative(output),
                "realtime_probe": output_probe,
                "duration_matches_simulation": duration_matches,
                "source_evidence": source_evidence,
            }
        )
    index = {
        "artifact_type": "b2_corrected_r1_realtime_video_index",
        "schema_version": "1.0",
        "audit_identity": AUDIT_IDENTITY,
        "formal_episode": False,
        "source_videos_modified": False,
        "conversion": {
            "source_fps": 10.0,
            "output_fps": 20.0,
            "timestamp_scale": 0.5,
        },
        "complete": len(records) == 19
        and all(item["duration_matches_simulation"] is True for item in records),
        "video_count": len(records),
        "videos": records,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        index = create_realtime_videos(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            index_path=args.index,
            force=args.force,
        )
    except RealtimeVideoError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "complete": index["complete"],
                "video_count": index["video_count"],
                "index": str(args.index.resolve()),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
