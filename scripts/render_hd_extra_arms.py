#!/usr/bin/env python3
"""Render saved Piper and SO101 motions at native HD and simulation speed.

Run with AA1/.venv/bin/python website/scripts/render_hd_extra_arms.py.
Piper replays the recorded ReCAP requests through its original generated driver,
checking the full physical trace and call endpoints before rendering. SO101
restores all six recorded joint poses and checks the recorded site positions;
it does not advance dynamics. These are presentation replays, not new trials.
Original research artifacts are read-only and no model calls are made.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import render_hd_panda as panda


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AA1"))
PIPER = ROOT / "expriment/chapter3_process_cases/data/runs/piper_sonnet46"
SO101 = ROOT / "expriment/chapter4_synthesis/data/runs/exp2a_8000_01/r03_sonnet46_so101/generation/menagerie_so101/validation"
SOURCES = {
    "piper-recap": PIPER / "demo",
    "so101-waypoints": SO101 / "attempt-003-152fd27cfa/waypoint_two_point",
    "so101-reach": SO101 / "attempt-001-ce93c3f113/reach_forward",
}
FPS = 30


def read_json(path):
    return json.loads(path.read_text())


def frame_robot_motion(work):
    """Fit the presentation camera to the robot across the saved motion."""
    import mujoco
    import numpy as np

    report = read_json(work / "replay_report.json")
    states = np.load(work / "frame_states.npz")
    model = mujoco.MjModel.from_xml_path(report["scene_path"])
    data = mujoco.MjData(model)
    bodies = set()
    for joint in range(model.njnt):
        if model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_FREE:
            continue
        body = int(model.jnt_bodyid[joint])
        while body:
            bodies.add(body)
            body = int(model.body_parentid[body])
    geoms = [index for index in range(model.ngeom) if int(model.geom_bodyid[index]) in bodies]
    lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
    radii = model.geom_rbound[geoms, None]
    for pose in states["qpos"]:
        data.qpos[:] = pose
        mujoco.mj_forward(model, data)
        lower = np.minimum(lower, np.min(data.geom_xpos[geoms] - radii, axis=0))
        upper = np.maximum(upper, np.max(data.geom_xpos[geoms] + radii, axis=0))
    report.setdefault("source_camera", report["camera"].copy())
    report["camera"].update(
        lookat=((lower + upper) / 2).tolist(),
        distance=max(0.65, float(np.linalg.norm(upper - lower)) * 1.15),
    )
    report["camera_framing"] = "Fitted to the recorded robot motion for presentation"
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")


def prepare_so101(clip, work):
    import mujoco
    import numpy as np

    source = SOURCES[clip]
    result = read_json(source / "result.json")
    rows = read_json(source / "samples.json")["samples"]
    model = mujoco.MjModel.from_xml_path(str(source / "mjcf.xml"))
    data = mujoco.MjData(model)
    joint_names = {model.joint(index).name for index in range(model.njnt)}
    if model.nq != 6 or model.njnt != 6 or model.nmocap:
        raise ValueError("SO101 source is not the recorded six-joint scene")
    addresses = {name: int(model.joint(name).qposadr[0]) for name in joint_names}
    times = np.asarray([row["time"] for row in rows])
    if len(rows) != result["physics_steps"] + 1:
        raise ValueError("SO101 saved samples do not cover the full motion")
    if not np.allclose(np.diff(times), model.opt.timestep, atol=1e-10, rtol=0):
        raise ValueError("SO101 saved sample timing is not uniform")
    duration = float(times[-1] - times[0])
    if abs(duration - result["sim_elapsed_s"]) > 1e-9:
        raise ValueError("SO101 saved duration differs from its result")
    poses = []
    maximum_error = 0.0
    camera = None
    for row in rows:
        if set(row["joint_positions"]) != joint_names:
            raise ValueError("SO101 source omits a moving joint")
        for name, value in row["joint_positions"].items():
            data.qpos[addresses[name]] = value
        data.time = row["time"]
        mujoco.mj_forward(model, data)
        for name, position in row["site_positions"].items():
            error = float(np.max(np.abs(data.site_xpos[model.site(name).id] - position)))
            if not np.isfinite(error) or error > 1e-9:
                raise ValueError(f"SO101 recorded site differs at {row['time']}: {name}: {error}")
            maximum_error = max(maximum_error, error)
        if camera is None:
            camera = panda.camera_for(model, data)
        poses.append(data.qpos.copy())
    count = math.ceil(round(duration * FPS, 9)) + 1
    video_times = np.minimum(np.arange(count) / FPS, duration)
    targets = times[0] + video_times
    indices = np.rint((targets - times[0]) / model.opt.timestep).astype(int)
    indices = np.clip(indices, 0, len(rows) - 1)
    indices[0], indices[-1] = 0, len(rows) - 1
    sample_error = float(np.max(np.abs(times[indices] - targets)))
    if sample_error > model.opt.timestep / 2 + 1e-9:
        raise ValueError("SO101 frame is not the nearest recorded physical state")
    np.savez_compressed(
        work / "frame_states.npz", time=times[indices], video_time=video_times,
        qpos=np.asarray(poses)[indices], qvel=np.zeros((count, model.nv)),
        ctrl=np.zeros((count, model.nu)),
    )
    report = {
        "kind": "presentation-replay-of-recorded-harness-capability",
        "source": str(source.relative_to(ROOT)), "new_experiment_evidence": False,
        "model_calls": 0, "physics_steps_executed": 0,
        "source_frame_count": result["video"]["frame_count"],
        "source_samples_checked": len(rows), "maximum_site_pose_error": maximum_error,
        "frame_count": count, "fps": FPS, "simulation_duration_s": duration,
        "max_frame_sampling_error_s": sample_error,
        "initial_and_terminal_poses_included": True,
        "sampling": "30 fps at actual simulation speed, nearest saved physical state",
        "scene_path": str((source / "mjcf.xml").resolve()), "camera": camera,
        "original_capability_passed": result["ok"],
        "original_measurements": result["metrics"]["measurements"],
    }
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{clip}: checked {len(rows)} saved poses; {count} real-time frames prepared", flush=True)


def verify_video(clip, work, assets):
    video = assets / f"robots/{clip}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(video), "-f", "null", "-"], check=True)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames,duration,pix_fmt,codec_name",
        "-of", "json", str(video),
    ]))["streams"][0]
    report = read_json(work / "replay_report.json")
    if (probe["width"], probe["height"], probe["r_frame_rate"], int(probe["nb_read_frames"])) != (1440, 1080, "30/1", report["frame_count"]):
        raise ValueError(f"Encoded video dimensions or cadence differ: {probe}")
    if abs(float(probe["duration"]) - report["frame_count"] / FPS) > 1e-6:
        raise ValueError(f"Encoded duration differs: {probe}")
    report["ffprobe"] = probe
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{clip}: native HD video decoded and verified: {probe['duration']} s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", choices=["all", *SOURCES], default="all")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--assets-dir", type=Path, default=ROOT / "website/assets")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--replay-only", action="store_true")
    modes.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    work_root = (args.work_dir or Path(tempfile.mkdtemp(prefix="extra-arms-hd-"))).resolve()
    assets = args.assets_dir.resolve()
    panda.SOURCE = PIPER
    panda.CLIPS = {clip: ("demo", f"robots/{clip}.mp4", f"robots/{clip}.jpg") for clip in SOURCES}
    print(f"Presentation output directory: {work_root}", flush=True)
    for clip in SOURCES if args.clip == "all" else [args.clip]:
        work = work_root / clip
        work.mkdir(parents=True, exist_ok=True)
        if not args.render_only:
            if clip == "piper-recap":
                panda.replay(clip, work)
            else:
                prepare_so101(clip, work)
        if not args.replay_only:
            frame_robot_motion(work)
            panda.render(clip, work, assets, 1440, 1080)
            verify_video(clip, work, assets)


if __name__ == "__main__":
    main()
