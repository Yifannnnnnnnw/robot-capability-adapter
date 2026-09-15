"""Render recorded LEAP states into compact paper figures.

This reads a completed ``run_tasks.py`` output directory. It loads each
original scene, applies the recorded qpos state, calls forward kinematics, and
renders that state. No task is rerun and no new physical trajectory is made.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "AA1"))
from auto_adapter.scene_runtime import apply_initial_state  # noqa: E402


SOURCE_HORIZON_S = {
    "gym_hand_reach_all_fingertips": 2.0,
    "robel_dclaw_pose_fixed": 4.0,
    "myosuite_object_hold_fixed": 1.5,
    "gym_hand_manipulate_block_full_pose": 4.0,
    "robel_dclaw_turn_fixed": 4.0,
}
LABELS = {
    "gym_hand_reach_all_fingertips": "Fingertip reach",
    "robel_dclaw_pose_fixed": "Joint pose",
    "myosuite_object_hold_fixed": "Object hold",
    "gym_hand_manipulate_block_full_pose": "Block manipulation",
    "robel_dclaw_turn_fixed": "Valve turning",
}
WIDTH, HEIGHT, CAPTION, FONT_SIZE = 640, 480, 48, 46
REPLAY_WIDTH, REPLAY_HEIGHT, REPLAY_FPS = 960, 720, 20


def _font() -> ImageFont.FreeTypeFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, FONT_SIZE)
    raise RuntimeError("no explicit Arial/DejaVu font is installed for captions")


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _trace(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("state"), dict):
                raise ValueError(f"invalid trace row {path}:{line_number}")
            row["time"] = float(row["time"])
            if not math.isfinite(row["time"]):
                raise ValueError(f"nonfinite trace time {path}:{line_number}")
            if rows and row["time"] < rows[-1]["time"] - 1e-9:
                raise ValueError(f"trace time goes backwards: {path}:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError(f"trace has no recorded states: {path}")
    return rows


def _id(model: mujoco.MjModel, kind: str, name: str) -> int:
    obj = getattr(mujoco.mjtObj, f"mjOBJ_{kind.upper()}")
    value = int(mujoco.mj_name2id(model, obj, name))
    if value < 0:
        raise ValueError(f"scene has no {kind} named {name!r}")
    return value


def _free_joint(model: mujoco.MjModel, body_id: int) -> int | None:
    free = int(mujoco.mjtJoint.mjJNT_FREE)
    for joint_id in range(model.njnt):
        if (int(model.jnt_bodyid[joint_id]) == body_id
                and int(model.jnt_type[joint_id]) == free):
            return joint_id
    return None


def _quat_from_rotation(rotation, label: str) -> np.ndarray:
    matrix = np.asarray(rotation, dtype=np.float64).reshape(-1)
    if matrix.shape != (9,) or not np.isfinite(matrix).all():
        raise ValueError(f"{label}.rotation must be nine finite values")
    quaternion = np.empty(4, dtype=np.float64)
    # DemoTrace writes world xmat; mju_mat2Quat writes the freejoint wxyz qpos.
    mujoco.mju_mat2Quat(quaternion, matrix)
    norm = float(np.linalg.norm(quaternion))
    if norm <= 1e-12:
        raise ValueError(f"{label}.rotation produces a zero quaternion")
    return quaternion / norm


def _restore(model: mujoco.MjModel, data: mujoco.MjData, case: dict, row: dict) -> None:
    state = row["state"]
    task_id = str(case["task_id"])
    for label, binding in case["success_spec"]["bindings"].items():
        if label not in state:
            raise ValueError(f"trace sample is missing bound state {label!r}")
        kind, value = binding["kind"], state[label]
        if kind == "joint":
            name = ("valve_OBJRx" if task_id == "robel_dclaw_turn_fixed"
                    and label == "fixture_joint" else binding["name"])
            joint_id = _id(model, "joint", str(name))
            if int(model.jnt_type[joint_id]) == int(mujoco.mjtJoint.mjJNT_FREE):
                raise ValueError(f"trace joint is not scalar: {name!r}")
            data.qpos[int(model.jnt_qposadr[joint_id])] = float(value["value"])
        elif kind == "body":
            body_id = _id(model, "body", str(binding["name"]))
            joint_id = _free_joint(model, body_id)
            if joint_id is None:
                continue
            position = np.asarray(value["position"], dtype=np.float64).reshape(-1)
            if position.shape != (3,) or not np.isfinite(position).all():
                raise ValueError(f"{label}.position must be three finite values")
            address = int(model.jnt_qposadr[joint_id])
            data.qpos[address:address + 3] = position
            data.qpos[address + 3:address + 7] = _quat_from_rotation(value["rotation"], label)
    # A recorded state is refreshed with forward kinematics only.
    mujoco.mj_forward(model, data)


def _select(rows: list[dict], task_id: str) -> tuple[list[int], list[float], dict]:
    times = np.asarray([row["time"] for row in rows], dtype=np.float64)
    recorded = max(0.0, float(times[-1] - times[0]))
    source = SOURCE_HORIZON_S[task_id]
    effective = min(source, recorded)
    requested = [float(times[0] + effective * fraction)
                 for fraction in (0.0, 1 / 3, 2 / 3, 1.0)]
    indices = [int(np.argmin(np.abs(times - target))) for target in requested]
    actual = [float(times[index]) for index in indices]
    return indices, actual, {
        "source_horizon_s": source,
        "recorded_duration_s": recorded,
        "effective_horizon_s": effective,
        "horizon_covered": recorded >= source - 1e-9,
        "trace_start_time_s": float(times[0]),
        "trace_end_time_s": float(times[-1]),
    }


def _camera(points: list[np.ndarray]) -> mujoco.MjvCamera:
    cloud = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    cloud = cloud[np.isfinite(cloud).all(axis=1)] if len(cloud) else np.zeros((1, 3))
    if not len(cloud):
        cloud = np.zeros((1, 3))
    lower, upper = cloud.min(axis=0), cloud.max(axis=0)
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = 0.5 * (lower + upper)
    camera.distance = max(0.38, 1.7 * float(np.max(upper - lower)) + 0.03)
    camera.azimuth, camera.elevation = 135.0, -45.0
    return camera


def _caption(image: np.ndarray, text: str) -> Image.Image:
    frame = Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB")
    result = Image.new("RGB", (frame.width, frame.height + CAPTION), "white")
    result.paste(frame, (0, CAPTION))
    draw = ImageDraw.Draw(result)
    font = _font()
    box = draw.textbbox((0, 0), text, font=font)
    y = (CAPTION - (box[3] - box[1])) // 2 - box[1]
    draw.text((6, y), text, font=font, fill=(15, 15, 15))
    return result


def _render_frames(model, data, case, rows, indices, camera, width, height, *, replay=False):
    images = []
    label = LABELS.get(str(case["task_id"]), str(case["task_id"]))
    with mujoco.Renderer(model, height=height, width=width) as renderer:
        for frame_number, index in enumerate(indices):
            _restore(model, data, case, rows[index])
            renderer.update_scene(data, camera=camera)
            prefix = "Recorded trace | " if replay else ""
            text = (f"{prefix}{label} | {rows[index]['time']:.3f} s"
                    if frame_number == 0 else f"{rows[index]['time']:.3f} s")
            images.append(_caption(renderer.render(), text))
    return images


def _row(model, data, case, rows, indices, camera) -> Image.Image:
    images = _render_frames(model, data, case, rows, indices, camera, WIDTH, HEIGHT)
    result = Image.new("RGB", (WIDTH * 4, HEIGHT + CAPTION), "white")
    for index, image in enumerate(images):
        result.paste(image, (index * WIDTH, 0))
    return result


def _verdict(report: dict) -> dict:
    keys = ("ok", "execution_ok", "physical_task_success", "task_success", "error", "evaluation_error")
    verdict = {key: report[key] for key in keys if key in report}
    if verdict.get("physical_task_success") is not True:
        verdict["task_success"] = False
    return verdict


def _case(tasks_root: Path, output_dir: Path, case: dict) -> dict:
    case_id, task_id = str(case["id"]), str(case["task_id"])
    case_dir = tasks_root / case_id
    trace_path, report_path = case_dir / "physics_samples.jsonl", case_dir / "task_report.json"
    report = _json(report_path) if report_path.is_file() else {}
    scene = Path(case["scene_path"])
    if not scene.is_absolute():
        scene = tasks_root / scene
    record = {
        "case_id": case_id, "task_id": task_id,
        "source": {"scene_path": str(scene.resolve()), "trace_path": str(trace_path.resolve()),
                    "task_report_path": str(report_path.resolve())},
        "task_verdict": _verdict(report), "render_ok": False,
    }
    rows = _trace(trace_path)
    indices, actual, horizon = _select(rows, task_id)
    model = mujoco.MjModel.from_xml_path(str(scene.resolve()))
    # Enlarge only this renderer's framebuffer; source XML and physics stay intact.
    model.vis.global_.offwidth = max(WIDTH, REPLAY_WIDTH)
    model.vis.global_.offheight = max(HEIGHT, REPLAY_HEIGHT)
    data = mujoco.MjData(model)
    initial = case.get("initial_state", {})
    if abs(float(initial.get("settle_s", 0.0))) > 1e-12:
        raise ValueError("re-rendering requires initial_state.settle_s == 0")
    apply_initial_state(model, data, {"initial_state": initial})
    points = []
    for index in indices:
        _restore(model, data, case, rows[index])
        points.extend(
            np.asarray(data.geom_xpos[geom_id], dtype=np.float64).copy()
            for geom_id in range(model.ngeom)
            if int(model.geom_bodyid[geom_id]) != 0
        )
    camera = _camera(points)
    row = _row(model, data, case, rows, indices, camera)
    row_path = output_dir / "rows" / f"{case_id}.png"
    row_path.parent.mkdir(parents=True, exist_ok=True)
    row.save(row_path)
    record.update({
        "render_ok": True, "horizon": horizon, "row_png": str(row_path.resolve()),
        "frames": [{"index": index, "requested_fraction": fraction, "recorded_time_s": time_s}
                   for index, fraction, time_s in zip(indices, (0.0, 1 / 3, 2 / 3, 1.0), actual, strict=True)],
    })
    replay_start = float(horizon["trace_start_time_s"])
    replay_end = replay_start + float(horizon["effective_horizon_s"])
    replay_targets = list(np.arange(replay_start, replay_end + 1e-9, 1 / REPLAY_FPS))
    if not replay_targets or replay_targets[-1] < replay_end - 1e-9:
        replay_targets.append(replay_end)
    times = np.asarray([row["time"] for row in rows], dtype=np.float64)
    replay_indices = [int(np.argmin(np.abs(times - target))) for target in replay_targets]
    replay_dir = output_dir / "replays"
    replay_dir.mkdir(parents=True, exist_ok=True)
    video_path = replay_dir / f"{case_id}.mp4"
    replay_frames = _render_frames(
        model, data, case, rows, replay_indices, camera,
        REPLAY_WIDTH, REPLAY_HEIGHT, replay=True,
    )
    import imageio.v2 as imageio
    with imageio.get_writer(
        video_path, fps=REPLAY_FPS, codec="libx264", macro_block_size=None,
        ffmpeg_log_level="error",
    ) as writer:
        for image in replay_frames:
            writer.append_data(np.asarray(image, dtype=np.uint8))
    metadata_path = replay_dir / f"{case_id}.json"
    metadata_path.write_text(json.dumps({
        "kind": "re-rendered_recorded_states",
        "video_path": str(video_path.resolve()),
        "source_trace": str(trace_path.resolve()), "fps": REPLAY_FPS,
        "frames": [{"frame": frame, "requested_time_s": target,
                    "recorded_time_s": float(rows[index]["time"]),
                    "source_index": index}
                   for frame, (target, index) in enumerate(zip(replay_targets, replay_indices, strict=True))],
    }, indent=2) + "\n", encoding="utf-8")
    record["replay"] = {"video_path": str(video_path.resolve()),
                        "metadata_path": str(metadata_path.resolve())}
    return record


def render_results(tasks_root: Path, output_dir: Path) -> dict:
    plan_path = tasks_root / "task_plan.json"
    plan = _json(plan_path)
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"task plan contains no cases: {plan_path}")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    records, errors = [], []
    for case in cases:
        try:
            record = _case(tasks_root, output_dir, case)
        except Exception as exc:
            case_id = str(case.get("id", "unknown"))
            report_path = tasks_root / case_id / "task_report.json"
            report = _json(report_path) if report_path.is_file() else {}
            scene = Path(case.get("scene_path", ""))
            if not scene.is_absolute():
                scene = tasks_root / scene
            record = {"case_id": case_id, "task_id": case.get("task_id"),
                      "source": {"scene_path": str(scene.resolve()),
                                  "trace_path": str((tasks_root / case_id / "physics_samples.jsonl").resolve()),
                                  "task_report_path": str(report_path.resolve())},
                      "task_verdict": _verdict(report), "render_ok": False,
                      "error": f"{type(exc).__name__}: {exc}"}
            errors.append(record)
        records.append(record)
    images = []
    for record in records:
        if record.get("render_ok") is True:
            with Image.open(record["row_png"]) as image:
                images.append(image.convert("RGB"))
    compact_path = None
    if images:
        compact = Image.new("RGB", (WIDTH * 4, sum(image.height for image in images)), "white")
        y = 0
        for image in images:
            compact.paste(image, (0, y))
            y += image.height
        compact_path = output_dir / "leap_compact.png"
        compact.save(compact_path)
    manifest = {"kind": "re-rendered_recorded_states", "source_tasks": str(tasks_root.resolve()),
                "source_plan": str(plan_path.resolve()),
                "compact_png": str(compact_path.resolve()) if compact_path else None,
                "cases": records, "errors": errors}
    (output_dir / "render_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def compose_keyframes(source_manifest: Path, output_dir: Path) -> dict:
    """Select one existing endpoint panel per task without rerendering states."""
    source = _json(source_manifest)
    records = [row for row in source["cases"] if row.get("render_ok") is True]
    if len(records) != 5:
        raise ValueError("keyframe layout requires all five rendered LEAP tasks")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    strip = Image.new("RGB", (WIDTH * len(records), HEIGHT + CAPTION), "white")
    selected = []
    crop = (3 * WIDTH, CAPTION, 4 * WIDTH, HEIGHT + CAPTION)
    for index, record in enumerate(records):
        with Image.open(record["row_png"]) as row:
            if row.size != (WIDTH * 4, HEIGHT + CAPTION):
                raise ValueError(f"unexpected panel dimensions: {record['row_png']}")
            frame = row.convert("RGB").crop(crop)
        label = f"({chr(97 + index)}) {LABELS[record['task_id']]}"
        strip.paste(_caption(np.asarray(frame), label), (index * WIDTH, 0))
        selected.append({
            "case_id": record["case_id"], "task_id": record["task_id"],
            "row_png": record["row_png"], "crop_xyxy": list(crop),
            "frame": record["frames"][-1], "source": record["source"],
            "render_ok": True,
        })
    output_dir.mkdir(parents=True)
    image_path = output_dir / "leap_keyframes.png"
    strip.save(image_path)
    manifest = {
        "kind": "selected_recorded_state_panels",
        "source_manifest": str(source_manifest),
        "selection": "Required evaluation endpoint or earlier trace termination; first attempts only.",
        "compact_png": str(image_path), "cases": selected, "errors": [],
    }
    (output_dir / "selection_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tasks", type=Path)
    source.add_argument("--keyframes-from", type=Path, help="Existing render_manifest.json; select one panel per task")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.keyframes_from:
            manifest = compose_keyframes(args.keyframes_from.resolve(), args.output_dir.resolve())
        else:
            manifest = render_results(args.tasks.resolve(), args.output_dir.resolve())
    except Exception as exc:
        print(json.dumps({"kind": "re-rendered_recorded_states", "ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1
    print(json.dumps({"kind": manifest["kind"], "cases": len(manifest["cases"]),
                      "rendered": sum(row.get("render_ok") is True for row in manifest["cases"]),
                      "compact_png": manifest["compact_png"]}))
    return 0 if not manifest["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
