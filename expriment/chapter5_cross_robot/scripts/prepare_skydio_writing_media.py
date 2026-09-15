#!/usr/bin/env python3
"""Build compact writing previews from the retained Skydio task recordings.

The script reads only existing MP4 files, task reports, and recorded physics
trace metadata.  It does not import MuJoCo, rerun a task, or alter any source
recording.  Main-sheet labels use exact source-video frame indices and
video-playback times; the renderer's sparse capture cadence means those labels
are not presented as inferred simulation timestamps.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import imageio.v2 as imageio
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_ROOT = PROJECT_ROOT / (
    "expriment/chapter5_cross_robot/data/runs/skydio_single_20260915_01"
)
ORIGINAL_ROOT = RUN_ROOT / "tasks_astra_diagnostic"
RETRY_ROOT = RUN_ROOT / "tasks_astra_orbit_clarified_20260915"
DEFAULT_OUTPUT = PROJECT_ROOT / (
    "expriment/chapter5_cross_robot/data/previews/"
    "skydio_writing_20260915"
)
COMPACT_OUTPUT = DEFAULT_OUTPUT.parent / "skydio_compact_20260915"

# Keep the aircraft in the tracked view and remove unused outer margins.
# The exact crop is recorded alongside the original source-video paths.
CROP_LTRB = (35, 40, 445, 305)
PANEL_SIZE = (410, 265)
MARGIN_X = 4
PANEL_GAP = 4
ROW_HEIGHT = 320
HEADER_HEIGHT = 0
CANVAS_WIDTH = MARGIN_X * 2 + 4 * PANEL_SIZE[0] + 3 * PANEL_GAP
FOOTER_HEIGHT = 0

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)
BOLD_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = BOLD_FONT_CANDIDATES if bold else FONT_CANDIDATES
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _source_record(task_dir: Path) -> dict[str, Any]:
    report_path = task_dir / "task_report.json"
    trace_path = task_dir / "physics_samples.jsonl"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    video = Path(report["video_path"]).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if not trace_path.is_file():
        raise FileNotFoundError(trace_path)

    with trace_path.open(encoding="utf-8") as stream:
        sample_count = sum(1 for line in stream if line.strip())
    reader = imageio.get_reader(str(video))
    try:
        meta = dict(reader.get_meta_data())
        frame_count = int(reader.count_frames())
    finally:
        reader.close()
    fps = float(meta.get("fps") or 30.0)
    sim_end = report.get("sim_time_end")
    if not isinstance(sim_end, (int, float)):
        raise ValueError(f"task report lacks numeric sim_time_end: {report_path}")
    return {
        "task_dir": task_dir,
        "report_path": report_path,
        "trace_path": trace_path,
        "video_path": video,
        "report": report,
        "frame_count": frame_count,
        "fps": fps,
        "video_duration_s": frame_count / fps if frame_count else 0.0,
        "video_last_frame_time_s": (frame_count - 1) / fps if frame_count else 0.0,
        "simulation_duration_s": float(sim_end),
        "trace_sample_count": sample_count,
        "video_size": [int(meta.get("size", (480, 368))[0]),
                       int(meta.get("size", (480, 368))[1])],
    }


def _frame_selection(record: dict[str, Any], positions: Iterable[float]) -> list[dict[str, Any]]:
    frame_count = int(record["frame_count"])
    fps = float(record["fps"])
    if frame_count < 1:
        raise ValueError(f"video has no frames: {record['video_path']}")
    selected = []
    for panel_number, fraction in enumerate(positions):
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"selection fraction outside [0,1]: {fraction}")
        index = int(round(fraction * (frame_count - 1)))
        selected.append({
            "panel_label": f"({chr(ord('a') + panel_number)})",
            "selection_fraction": fraction,
            "video_frame_index_zero_based": index,
            "video_time_s": index / fps,
        })
    return selected


def _read_selected_frames(record: dict[str, Any], selections: list[dict[str, Any]]) -> list[Image.Image]:
    reader = imageio.get_reader(str(record["video_path"]))
    frames = []
    try:
        for selection in selections:
            frame = Image.fromarray(reader.get_data(selection["video_frame_index_zero_based"])).convert("RGB")
            left, top, right, bottom = CROP_LTRB
            frame = frame.crop((left, top, right, bottom)).resize(
                PANEL_SIZE, Image.Resampling.LANCZOS
            )
            frames.append(frame)
    finally:
        reader.close()
    return frames


def _draw_frame_panel(canvas: Image.Image, frame: Image.Image, selection: dict[str, Any], x: int, y: int) -> None:
    draw = ImageDraw.Draw(canvas)
    canvas.paste(frame, (x, y))
    draw.rectangle((x, y, x + PANEL_SIZE[0], y + PANEL_SIZE[1]), outline="#9aa8b6", width=1)
    label = f"{selection['panel_label']}  video {selection['video_time_s']:.2f}s"
    draw.text(
        (x, y + PANEL_SIZE[1] + 3), label, font=_font(20),
        fill="#263c50",
    )


def _draw_row(
    canvas: Image.Image,
    record: dict[str, Any],
    row_index: int,
    title: str,
    task_id: str,
    positions: Iterable[float],
    status_override: str | None = None,
) -> dict[str, Any]:
    row_top = HEADER_HEIGHT + row_index * ROW_HEIGHT
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, row_top, CANVAS_WIDTH, row_top + ROW_HEIGHT - 1),
                   fill="#f2f5f8" if row_index % 2 == 0 else "#e8eef3")
    record = dict(record)
    selections = _frame_selection(record, positions)
    frames = _read_selected_frames(record, selections)
    for column, (frame, selection) in enumerate(zip(frames, selections, strict=True)):
        x = MARGIN_X + column * (PANEL_SIZE[0] + PANEL_GAP)
        _draw_frame_panel(canvas, frame, selection, x, row_top + 29)

    report = record["report"]
    verdict = "PASS" if report.get("physical_task_success") is True else "FAIL"
    accent = "#16794b" if verdict == "PASS" else "#a55b10"
    draw.text((MARGIN_X, row_top + 6), f"{task_id}  {title}",
              font=_font(22, bold=True), fill="#142334")
    status = status_override or verdict
    status_box = draw.textbbox((0, 0), status, font=_font(20, bold=True))
    draw.text((CANVAS_WIDTH - MARGIN_X - (status_box[2] - status_box[0]), row_top + 8),
              status, font=_font(20, bold=True), fill=accent)
    record["task_id"] = task_id
    record["title"] = title
    record["physical_task_success"] = report.get("physical_task_success")
    record["execution_ok"] = report.get("execution_ok")
    record["task_metrics"] = report.get("task_metrics", [])
    record["frames"] = selections
    record["selection_note"] = (
        "Representative normalized positions choose four frames from the original MP4. "
        "The image labels show source-video playback time only; no simulation timestamp "
        "is inferred from the sparse renderer capture."
    )
    return record


def _build_main_sheet(output: Path) -> tuple[Path, list[dict[str, Any]]]:
    specs = (
        ("X2-T01_central", "Ground takeoff → hover", "X2-T01",
         (0.00, 0.12, 0.30, 1.00), None),
        ("X2-T04_central", "Climb → transit → return", "X2-T04",
         (0.00, 0.12, 0.25, 1.00), None),
        ("X2-T05_central", "Ordered waypoints", "X2-T05",
         (0.00, 0.30, 0.55, 1.00), None),
        ("X2-T07_central", "Original orbit (first attempt)", "X2-T07",
         (0.00, 0.22, 0.55, 1.00), None),
    )
    height = HEADER_HEIGHT + ROW_HEIGHT * len(specs) + FOOTER_HEIGHT
    canvas = Image.new("RGB", (CANVAS_WIDTH, height), "#e8eef3")
    records = []
    for row, (directory, title, task_id, positions, outcome) in enumerate(specs):
        record = _source_record(ORIGINAL_ROOT / directory)
        records.append(_draw_row(canvas, record, row, title, task_id, positions, outcome))
    output.mkdir(parents=True, exist_ok=True)
    path = output / "skydio_x2_original_tasks_overview.png"
    canvas.save(path, format="PNG", optimize=True)
    return path, records


def _build_retry_strip(output: Path) -> tuple[Path, dict[str, Any]]:
    record = _source_record(RETRY_ROOT / "X2-T07_central")
    positions = (0.00, 0.23, 0.68, 1.00)
    width = CANVAS_WIDTH
    height = HEADER_HEIGHT + ROW_HEIGHT + FOOTER_HEIGHT
    canvas = Image.new("RGB", (width, height), "#e8eef3")
    retry_record = _draw_row(canvas, record, 0, "Clarified orbit retry", "X2-T07 retry", positions,
                              "FAIL")
    output.mkdir(parents=True, exist_ok=True)
    path = output / "skydio_x2_clarified_orbit_retry.png"
    canvas.save(path, format="PNG", optimize=True)
    return path, retry_record


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(record)
    for key in ("task_dir", "report", "video_path", "trace_path", "report_path"):
        value = cleaned.get(key)
        if isinstance(value, Path):
            cleaned[key] = str(value)
    cleaned.pop("report", None)
    return cleaned


def _build_compact_sheet(output: Path) -> tuple[Path, Path]:
    """Keep one recorded frame per task, without displaying task verdicts."""
    specs = (
        ("X2-T01", "Takeoff and hover", 0.30),
        ("X2-T04", "Climb, transit and return", 0.25),
        ("X2-T05", "Ordered waypoints", 0.55),
        ("X2-T07", "Orbit (first attempt)", 0.55),
    )
    canvas = Image.new("RGB", (CANVAS_WIDTH, ROW_HEIGHT), "white")
    draw = ImageDraw.Draw(canvas)
    records = []
    for column, (task_id, title, fraction) in enumerate(specs):
        record = _source_record(ORIGINAL_ROOT / f"{task_id}_central")
        selection = _frame_selection(record, (fraction,))[0]
        selection["panel_label"] = f"({chr(ord('a') + column)})"
        frame = _read_selected_frames(record, [selection])[0]
        x = MARGIN_X + column * (PANEL_SIZE[0] + PANEL_GAP)
        draw.text((x, 4), f"{selection['panel_label']} {title}",
                  font=_font(22, bold=True), fill="#142334")
        canvas.paste(frame, (x, 30))
        draw.text((x, 298), f"video {selection['video_time_s']:.2f} s",
                  font=_font(20), fill="#263c50")
        record.update(task_id=task_id, title=title, frames=[selection],
                      original_report_success=record["report"].get("physical_task_success"))
        records.append(_clean_record(record))

    output.mkdir(parents=True, exist_ok=True)
    image_path = output / "skydio_tasks_compact.png"
    canvas.save(image_path, format="PNG", optimize=True)
    source_path = output / "skydio_tasks_compact_source.json"
    source = {
        "figure": image_path.name,
        "generated_by": str(Path(__file__).resolve()),
        "source_run": "skydio_single_20260915_01",
        "crop_ltrb_pixels": list(CROP_LTRB),
        "panel_size_pixels": list(PANEL_SIZE),
        "frames": records,
        "caption": (
            "One frame from each original task video: takeoff and hover; climb, "
            "transit and return; ordered waypoints; and orbit. Labels are video "
            "playback times (frame index / fps), not simulation times. The camera "
            "tracks the aircraft; individual frames do not show the full trajectory "
            "or establish task completion. Original reports and recordings are unchanged."
        ),
    }
    source_path.write_text(json.dumps(source, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return image_path, source_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--compact", action="store_true",
                        help="Render one frame per original task in a single row, without verdict labels.")
    parser.add_argument("--skip-retry", action="store_true",
                        help="Do not render the separately labelled clarified orbit retry strip.")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.compact:
        if output == DEFAULT_OUTPUT.resolve():
            output = COMPACT_OUTPUT
        overview, source = _build_compact_sheet(output)
        print(json.dumps({"overview": str(overview), "source": str(source)}, indent=2))
        return 0
    overview, records = _build_main_sheet(output)
    retry_path = None
    retry_record = None
    if not args.skip_retry:
        retry_path, retry_record = _build_retry_strip(output)

    manifest = {
        "kind": "skydio_writing_media_from_retained_recordings",
        "source_run": "skydio_single_20260915_01",
        "source_original_root": str(ORIGINAL_ROOT),
        "source_retry_root": str(RETRY_ROOT),
        "generated_by": str(Path(__file__).resolve()),
        "model_calls": 0,
        "new_physics_runs": 0,
        "source_recordings_modified": False,
        "overview_png": str(overview),
        "crop_ltrb_pixels": list(CROP_LTRB),
        "panel_size_pixels": list(PANEL_SIZE),
        "selection_method": (
            "Read original MP4 frames with imageio/FFmpeg, crop unused margins, "
            "resize with Lanczos, and compose with PIL. Each selection stores "
            "exact frame index and frame_index/fps video time; panel letters are "
            "identifiers only and do not assert task phases."
        ),
        "original_rows": [_clean_record(record) for record in records],
        "clarified_retry": (_clean_record(retry_record) if retry_record is not None else None),
        "clarified_retry_png": str(retry_path) if retry_path is not None else None,
        "audit_note": (
            "The four-row overview uses only the first retained task attempt for "
            "T01, T04, T05 and T07. The retry is separate and explicitly labelled; "
            "neither frame time is presented as an inferred exact simulation time."
        ),
    }
    manifest_path = output / "selection_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    readme = output / "README.md"
    readme.write_text(
        "# Skydio X2 writing media\n\n"
        "`skydio_x2_original_tasks_overview.png` is a compact four-row sheet "
        "made from the retained original Astra task MP4 files: X2-T01, X2-T04, "
        "X2-T05 and the original X2-T07 orbit attempt. The separate "
        "`skydio_x2_clarified_orbit_retry.png` is a labelled retry and must not "
        "be read as the original orbit trajectory.\n\n"
        "Frames were decoded with imageio/FFmpeg and composed with PIL after a "
        "fixed crop of the 480x368 source frame. Labels show exact zero-based "
        "video playback time (`frame / 30 fps`); frame indices remain in the "
        "manifest. Because "
        "the runtime captures video sparsely from the native trace, the labels "
        "do not claim exact simulation timestamps. Source simulation durations, "
        "reports, physics-trace paths, and frame selections are in "
        "`selection_manifest.json`. No model call or new physics run was made.\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "overview": str(overview),
        "retry": str(retry_path) if retry_path else None,
        "manifest": str(manifest_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
