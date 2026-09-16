#!/usr/bin/env python3
"""Reproduce the three recorded Panda clips for native-resolution presentation.

Run from the repository root with the existing AA1 environment:
    AA1/.venv/bin/python website/scripts/render_hd_panda.py --work-dir /tmp/panda-hd

Use --replay-only to validate and save video-frame states without graphics;
then --render-only with the same work directory to render those checked states.
Each clip runs in a separate process. Original task artifacts are read-only.
Recorded requests call the original exported server functions and real driver;
there are no model calls. Outputs are presentation replays, not new experiment
evidence. Rendering starts only after every bound physics sample and complete
call-endpoint qpos/qvel reproduce the recording within 1e-8 absolute error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "expriment/chapter5_cross_robot/data/runs/franka_single_20260915_01/tasks_astra_budget48"
CLIPS = {
    "pick-place": ("mw_pick_place_central", "panda-pick-place.mp4", "robots/panda-pick-place.jpg"),
    "drawer": ("mw_drawer_open_central", "robots/panda-drawer.mp4", "robots/panda-drawer.jpg"),
    "dial": ("mw_dial_turn_central", "robots/panda-dial.mp4", "robots/panda-dial.jpg"),
}
TOLERANCE = 1e-8


def camera_for(model, data):
    import mujoco
    import numpy as np

    from auto_adapter.export_runtime import _frame_robot

    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model, camera)
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.azimuth = float(model.vis.global_.azimuth)
    camera.elevation = float(model.vis.global_.elevation)
    camera.lookat[:] = model.stat.center
    camera.distance = float(max(model.stat.extent, 0.3)) * 1.8
    # Reuse the original framing calculation without installing its recorder.
    from types import SimpleNamespace
    capture = SimpleNamespace(_cam=camera, _track_free=False)
    _frame_robot(capture, model, data)
    return {
        "lookat": np.asarray(camera.lookat).tolist(),
        "distance": camera.distance,
        "azimuth": camera.azimuth,
        "elevation": camera.elevation,
    }


def compare_trace(original, reproduced):
    import numpy as np

    count = 0
    max_error = 0.0
    with original.open() as left, reproduced.open() as right:
        from itertools import zip_longest
        for count, (a, b) in enumerate(zip_longest(left, right), 1):
            if a is None or b is None:
                raise RuntimeError("Physics sample count differs from the recording")
            a, b = json.loads(a), json.loads(b)
            if a["state"].keys() != b["state"].keys() or a["contacts"] != b["contacts"]:
                raise RuntimeError(f"Physics bindings or contacts differ at sample {count - 1}")
            errors = [abs(a["time"] - b["time"])]
            for label, fields in a["state"].items():
                if fields.keys() != b["state"][label].keys():
                    raise RuntimeError(f"Physics fields differ at sample {count - 1}")
                for field, value in fields.items():
                    errors.append(float(np.max(np.abs(
                        np.asarray(value) - np.asarray(b["state"][label][field])
                    ))))
            sample_error = max(errors)
            if not np.isfinite(sample_error) or sample_error > TOLERANCE:
                raise RuntimeError(f"Physics state differs at sample {count - 1}: {sample_error}")
            max_error = max(max_error, sample_error)
    return {"samples_compared": count, "max_absolute_error": max_error, "contacts_equal": True}


def replay(clip, work):
    import mujoco
    import numpy as np

    task_dir = SOURCE / CLIPS[clip][0]
    original = json.loads((task_dir / "task_report.json").read_text())
    config = json.loads((task_dir / "task_runtime_config.json").read_text())
    config.update(output_dir=str(work), record_video=False)
    config_path = work / "task_runtime_config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    os.environ["AA1_TASK_RUNTIME_CONFIG"] = str(config_path)
    os.chdir(task_dir)
    sys.path.insert(0, str(ROOT / "AA1"))
    sys.path.insert(0, str(task_dir))
    spec = importlib.util.spec_from_file_location("panda_presentation_server", task_dir / "mcp_server.py")
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    runtime = server.runtime
    robot = runtime.robot
    model, data = robot.model, robot.data
    cam = camera_for(model, data)
    frames = []
    steps = 0
    endpoint_checks = []

    def snapshot():
        frames.append((float(data.time), data.qpos.copy(), data.qvel.copy(), data.ctrl.copy()))

    def compare_endpoint(observation, expected, label):
        error = max(abs(observation["sim_time_s"] - expected["sim_time_s"]), *[
            float(np.max(np.abs(np.asarray(observation[key]) - np.asarray(expected[key]))))
            for key in ("qpos", "qvel")
        ])
        if not np.isfinite(error) or error > TOLERANCE:
            raise RuntimeError(f"{label} full state differs: {error}")
        return error

    # The original recorder is installed after the DemoTrace step hook.
    inner_step = mujoco.mj_step

    def capture_step(current_model, current_data, nstep=1):
        nonlocal steps
        if current_model is not model or current_data is not data:
            return inner_step(current_model, current_data, nstep)
        for _ in range(int(nstep)):
            inner_step(model, data, 1)
            steps += 1
            if steps % 16 == 0:
                snapshot()

    mujoco.mj_step = capture_step
    try:
        snapshot()
        initial = runtime.observe()
        snapshot()
        initial_error = compare_endpoint(initial, original["initial_observation"], "initial")
        for index, call in enumerate(original["tool_call_log"], 1):
            result = getattr(server, call["tool"])(request=call["request"])
            observed = runtime.observe()
            snapshot()
            error = compare_endpoint(observed, call["observation"], f"call {index}")
            expected_result = call["return_value"]
            if result.get("success") != expected_result.get("success"):
                raise RuntimeError(f"Call {index} driver success flag differs")
            endpoint_checks.append({
                "call": index, "tool": call["tool"], "time": float(data.time),
                "frame": len(frames) - 1, "max_absolute_error": error,
                "driver_success": result.get("success"),
            })
            print(f"{clip}: reproduced call {index}/{len(original['tool_call_log'])}", flush=True)
    finally:
        mujoco.mj_step = inner_step
        runtime_report = runtime.close()
    if runtime_report["error"] or runtime_report["physics_trace"]["error"]:
        raise RuntimeError(f"Replay runtime error: {runtime_report}")
    physics = compare_trace(task_dir / "physics_samples.jsonl", work / "physics_samples.jsonl")
    expected_frames = original["n_frames"]
    if len(frames) != expected_frames:
        raise RuntimeError(f"Frame count differs: {len(frames)} != {expected_frames}")
    np.savez_compressed(
        work / "frame_states.npz", time=np.array([f[0] for f in frames]),
        qpos=np.stack([f[1] for f in frames]), qvel=np.stack([f[2] for f in frames]),
        ctrl=np.stack([f[3] for f in frames]),
    )
    report = {
        "kind": "presentation-replay-of-recorded-driver-calls",
        "source": str(task_dir.relative_to(ROOT)), "new_experiment_evidence": False,
        "model_calls": 0, "absolute_comparison_tolerance": TOLERANCE,
        "physics_comparison": physics, "initial_state_max_absolute_error": initial_error,
        "call_endpoints": endpoint_checks, "frame_count": len(frames), "fps": 30,
        "camera": cam, "scene_path": config["scene_path"],
    }
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{clip}: all {physics['samples_compared']} physics samples match; {len(frames)} frame states saved", flush=True)


def render(clip, work, assets, width, height):
    import imageio.v2 as imageio
    import imageio_ffmpeg
    import mujoco
    import numpy as np

    sys.path.insert(0, str(ROOT / "AA1"))
    report = json.loads((work / "replay_report.json").read_text())
    states = np.load(work / "frame_states.npz")
    model = mujoco.MjModel.from_xml_path(report["scene_path"])
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, width)
    model.vis.global_.offheight = max(model.vis.global_.offheight, height)
    data = mujoco.MjData(model)
    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model, camera)
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    for key, value in report["camera"].items():
        if key == "lookat":
            camera.lookat[:] = value
        else:
            setattr(camera, key, value)
    video = assets / CLIPS[clip][1]
    poster = assets / CLIPS[clip][2]
    video.parent.mkdir(parents=True, exist_ok=True)
    poster.parent.mkdir(parents=True, exist_ok=True)
    temporary_video = work / "presentation-hd.mp4"
    ffmpeg = subprocess.Popen([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", "30", "-i", "-", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary_video),
    ], stdin=subprocess.PIPE)
    count = len(states["time"])
    poster_index = min(count - 1, round(count * 0.4))
    try:
        with mujoco.Renderer(model, height=height, width=width) as renderer:
            for index in range(count):
                data.time = states["time"][index]
                data.qpos[:] = states["qpos"][index]
                data.qvel[:] = states["qvel"][index]
                data.ctrl[:] = states["ctrl"][index]
                mujoco.mj_forward(model, data)
                renderer.update_scene(data, camera=camera)
                rgb = renderer.render()
                ffmpeg.stdin.write(rgb.tobytes())
                if index == poster_index:
                    imageio.imwrite(work / "presentation-hd.jpg", rgb, quality=93)
                if index % 100 == 0:
                    print(f"{clip}: rendered {index + 1}/{count}", flush=True)
    finally:
        ffmpeg.stdin.close()
        returncode = ffmpeg.wait()
    if returncode:
        raise RuntimeError(f"ffmpeg failed with code {returncode}")
    import shutil
    shutil.copyfile(temporary_video, video)
    shutil.copyfile(work / "presentation-hd.jpg", poster)
    report["render"] = {
        "width": width, "height": height, "fps": 30, "frame_count": count,
        "duration_s": count / 30, "crf": 19, "poster_frame": poster_index,
        "video": str(video), "poster": str(poster), "bytes": video.stat().st_size,
    }
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{clip}: wrote {width}x{height} presentation replay, {count} frames", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", choices=["all", *CLIPS], default="all")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--assets-dir", type=Path, default=ROOT / "website/assets")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1080)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--replay-only", action="store_true")
    modes.add_argument("--render-only", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.work_dir is None:
        args.work_dir = Path(tempfile.mkdtemp(prefix="panda-hd-"))
    args.work_dir = args.work_dir.resolve()
    args.assets_dir = args.assets_dir.resolve()
    if not args.worker:
        print(f"Presentation replay work directory: {args.work_dir}", flush=True)
        for clip in CLIPS if args.clip == "all" else [args.clip]:
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--clip", clip,
                       "--work-dir", str(args.work_dir), "--assets-dir", str(args.assets_dir),
                       "--width", str(args.width), "--height", str(args.height)]
            if args.replay_only:
                command.append("--replay-only")
            if args.render_only:
                command.append("--render-only")
            subprocess.run(command, check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return
    work = args.work_dir / args.clip
    work.mkdir(parents=True, exist_ok=True)
    if not args.render_only:
        replay(args.clip, work)
    if not args.replay_only:
        render(args.clip, work, args.assets_dir, args.width, args.height)


if __name__ == "__main__":
    main()
