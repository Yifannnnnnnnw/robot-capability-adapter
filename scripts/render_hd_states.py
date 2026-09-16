"""Render the selected LEAP/Skydio clips at actual simulation speed.

Run with AA1/.venv/bin/python website/scripts/render_hd_states.py.
Only forward kinematics are evaluated; this never advances a simulation or
loads a driver. Uniform 30 fps frames use the nearest saved physical states,
with the exact initial and terminal poses included. The camera is retained.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AA1"))
from auto_adapter.scene_runtime import apply_initial_state  # noqa: E402


RUNS = ROOT / "expriment/chapter5_cross_robot/data/runs"
DESTINATION = ROOT / "website/assets/robots"
WIDTH, HEIGHT, FPS = 1440, 1080, 30
SOURCES = {
    "leap-reach": "leap_single_20260915_01/tasks_astra_direct_retry_20260915/gym_hand_reach_all_fingertips_central",
    "leap-block": "leap_single_20260915_01/tasks_astra/gym_hand_manipulate_block_full_pose_central",
    "leap-pose": "leap_single_20260915_01/tasks_astra/robel_dclaw_pose_fixed_central",
    "skydio-transit": "skydio_single_20260915_01/tasks_astra_diagnostic/X2-T04_central",
    "skydio-waypoints": "skydio_single_20260915_01/tasks_astra_diagnostic/X2-T05_central",
    "skydio-orbit": "skydio_single_20260915_01/tasks_astra_orbit_clarified_20260915/X2-T07_central",
    "skydio-orbit-full": "skydio_single_20260915_01/tasks_astra_diagnostic/X2-T07_central",
}


def read_json(path: Path):
    return json.loads(path.read_text())


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def realtime_indices(source: Path, rows: list[dict]) -> tuple[list[int], dict]:
    """Sample the trace at 30 fps, preserving its actual elapsed time."""
    times = np.asarray([row["time"] for row in rows])
    runtime = read_json(source / "runtime_report.json")
    trace = runtime["physics_trace"]
    if trace["error"] or len(rows) != trace["sample_count"]:
        raise ValueError(f"incomplete physical trace: {source}")
    if not np.allclose(np.diff(times), trace["timestep_s"], atol=1e-10, rtol=0):
        raise ValueError(f"nonuniform physical trace: {source}")
    if not np.allclose([times[0], times[-1]],
                       [runtime["sim_time_start"], runtime["sim_time_end"]],
                       atol=1e-9, rtol=0):
        raise ValueError(f"physical trace does not cover the complete task: {source}")
    duration = float(times[-1] - times[0])
    # Remove floating-point drift at an exact frame boundary before rounding up.
    frame_count = math.ceil(round(duration * FPS, 9)) + 1
    targets = np.minimum(times[0] + np.arange(frame_count) / FPS, times[-1])
    indices = np.rint((targets - times[0]) / trace["timestep_s"]).astype(int)
    indices = np.clip(indices, 0, len(rows) - 1)
    indices[0], indices[-1] = 0, len(rows) - 1
    sample_error = float(np.max(np.abs(times[indices] - targets)))
    if sample_error > trace["timestep_s"] / 2 + 1e-9:
        raise ValueError(f"real-time sampling exceeds half a physical step: {source}")
    timing = {
        "playback": "actual simulation speed",
        "recorded_duration_s": duration,
        "encoded_duration_s": frame_count / FPS,
        "maximum_sample_time_error_s": sample_error,
        "terminal_presentation_hold_s": frame_count / FPS - duration,
        "initial_and_terminal_poses_included": True,
    }
    if not 0 <= timing["terminal_presentation_hold_s"] <= 2 / FPS + 1e-9:
        raise ValueError(f"unexpected endpoint presentation hold: {source}")
    return indices.tolist(), timing


def binding_refs(model, bindings):
    refs = {}
    covered = set()
    for label, binding in bindings.items():
        kind, name = binding["kind"], binding["name"]
        ref = getattr(model, kind)(name)
        free_joint = None
        if kind == "joint":
            if int(model.jnt_type[ref.id]) not in (
                int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)
            ):
                raise ValueError(f"unsupported scalar joint: {name}")
            covered.add(ref.id)
        elif kind == "body":
            for joint in range(model.njnt):
                if (model.jnt_bodyid[joint] == ref.id
                        and model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_FREE):
                    free_joint = joint
                    covered.add(joint)
        refs[label] = (kind, ref.id, free_joint,
                       np.asarray(binding.get("local_position_m", [0, 0, 0])))
    if covered != set(range(model.njnt)) or model.nmocap:
        raise ValueError("recorded bindings do not cover every moving joint")
    return refs


def restore_and_check(model, data, refs, row):
    for label, (kind, index, free_joint, offset) in refs.items():
        value = row["state"][label]
        if kind == "joint":
            data.qpos[model.jnt_qposadr[index]] = value["value"]
        elif free_joint is not None:
            rotation = np.asarray(value["rotation"], dtype=float)
            quaternion = np.empty(4)
            mujoco.mju_mat2Quat(quaternion, rotation)
            address = model.jnt_qposadr[free_joint]
            data.qpos[address:address + 3] = value["position"] - rotation.reshape(3, 3) @ offset
            data.qpos[address + 3:address + 7] = quaternion / np.linalg.norm(quaternion)
    data.time = row["time"]
    mujoco.mj_forward(model, data)
    error = 0.0
    for label, (kind, index, _, offset) in refs.items():
        value = row["state"][label]
        if kind == "joint":
            difference = abs(data.qpos[model.jnt_qposadr[index]] - value["value"])
        else:
            position = data.xpos[index] if kind == "body" else getattr(data, kind + "_xpos")[index]
            rotation = data.xmat[index] if kind == "body" else getattr(data, kind + "_xmat")[index]
            difference = max(
                np.max(np.abs(position + rotation.reshape(3, 3) @ offset - value["position"])),
                np.max(np.abs(rotation - value["rotation"])),
            )
        error = max(error, float(difference))
    if not np.isfinite(data.qpos).all() or error > 1e-9:
        raise ValueError(f"recorded pose restoration failed at {row['time']}: {error}")
    return error


def original_camera(model, data):
    """Match _TaskRecorder plus export_runtime._frame_robot without recording."""
    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model, camera)
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.azimuth = model.vis.global_.azimuth
    camera.elevation = model.vis.global_.elevation
    camera.lookat[:] = model.stat.center
    camera.distance = max(float(model.stat.extent), 0.3) * 1.8
    joints = np.flatnonzero(model.jnt_type != mujoco.mjtJoint.mjJNT_FREE)
    if not len(joints):
        joints = range(model.njnt)
    bodies = set()
    for joint in joints:
        body = int(model.jnt_bodyid[joint])
        while body:
            bodies.add(body)
            body = int(model.body_parentid[body])
    if bodies:
        positions = data.xpos[sorted(bodies)]
        lower, upper = positions.min(axis=0), positions.max(axis=0)
        camera.lookat[:] = (lower + upper) / 2
        camera.distance = max(camera.distance, np.linalg.norm(upper - lower) * 2.5)
    tracking = bool(model.njnt and model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE)
    offset = camera.lookat.copy() - data.qpos[:3] if tracking else None
    return camera, offset


def render(name: str) -> dict:
    source = RUNS / SOURCES[name]
    config = read_json(source / "task_runtime_config.json")
    if config["initial_state"].get("settle_s", 0) != 0:
        raise ValueError("state replay cannot perform initial settling")
    rows = read_jsonl(source / "physics_samples.jsonl")
    indices, timing = realtime_indices(source, rows)
    model = mujoco.MjModel.from_xml_path(config["scene_path"])
    model.vis.global_.offwidth, model.vis.global_.offheight = WIDTH, HEIGHT
    data = mujoco.MjData(model)
    apply_initial_state(model, data, config)
    refs = binding_refs(model, config["success"]["bindings"])
    camera, offset = original_camera(model, data)
    destination = DESTINATION / f"{name}.mp4"
    temporary = destination.with_suffix(".rendering.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(temporary),
    ]
    maximum_error = 0.0
    try:
        with mujoco.Renderer(model, height=HEIGHT, width=WIDTH) as renderer:
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            try:
                for index in indices:
                    maximum_error = max(maximum_error, restore_and_check(model, data, refs, rows[index]))
                    if offset is not None:
                        camera.lookat[:] = data.qpos[:3] + offset
                    renderer.update_scene(data, camera=camera)
                    process.stdin.write(renderer.render().tobytes())
            finally:
                process.stdin.close()
                exit_code = process.wait()
            if exit_code:
                raise RuntimeError(f"ffmpeg encoding failed for {name}: {exit_code}")
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(temporary),
                        "-f", "null", "-"], check=True)
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames,duration,pix_fmt,codec_name",
            "-of", "json", str(temporary),
        ]))["streams"][0]
        if (probe["width"], probe["height"], probe["r_frame_rate"], int(probe["nb_read_frames"])) != (WIDTH, HEIGHT, "30/1", len(indices)):
            raise ValueError(f"encoded video differs from requested dimensions/timing: {probe}")
        if abs(float(probe["duration"]) - timing["encoded_duration_s"]) > 1e-6:
            raise ValueError(f"encoded video duration differs from the real-time schedule: {probe}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    poster = destination.with_suffix(".jpg")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
                    str(len(indices) / FPS * 0.4), "-i", str(destination),
                    "-frames:v", "1", "-q:v", "2", str(poster)], check=True)
    result = {
        "clip": name, "source": str(source.relative_to(ROOT)), "video": str(destination.relative_to(ROOT)),
        "video_bytes": destination.stat().st_size, "poster_bytes": poster.stat().st_size,
        "maximum_bound_pose_error": maximum_error, "checked_rendered_frames": len(indices),
        **timing, "ffprobe": probe,
    }
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clips", nargs="*", metavar="CLIP", help="selected clip names; default: all seven")
    args = parser.parse_args()
    unknown = set(args.clips) - SOURCES.keys()
    if unknown:
        parser.error(f"unknown clips: {', '.join(sorted(unknown))}")
    for clip in args.clips or SOURCES:
        render(clip)
