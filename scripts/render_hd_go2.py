#!/usr/bin/env python3
"""Replay two recorded Go2 capability checks for the project website.

Run with AA1/.venv/bin/python website/scripts/render_hd_go2.py.
The exact saved candidates, scenes, initial states and requests run through
the existing trusted Harness recorder. All saved per-step measurements and
contacts must reproduce before native HD rendering starts. These are
capability-validation presentation replays, not ReCAP or new experiment runs.
No model is called; original research artifacts remain read-only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AA1"))
SOURCE = ROOT / "expriment/chapter4_synthesis/data/runs/exp2a_8000_01/r01_opus5_go2/generation/go2"
CLIPS = {
    "go2-forward": "attempt-002-fafd59eb11/goto_forward_pose",
    "go2-waypoints": "attempt-003-83e940636e/path_three_waypoints",
}
WIDTH, HEIGHT, FPS = 1440, 1080, 30
TOLERANCE = 1e-8


def read_json(path):
    return json.loads(path.read_text())


def compare_saved(expected, actual, path="sample"):
    """Compare every saved field; newer measurement fields may be additional."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or not expected.keys() <= actual.keys():
            raise ValueError(f"Missing recorded fields at {path}")
        return max((compare_saved(value, actual[key], f"{path}.{key}")
                    for key, value in expected.items()), default=0.0)
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise ValueError(f"Recorded array length differs at {path}")
        return max((compare_saved(a, b, f"{path}[{i}]")
                    for i, (a, b) in enumerate(zip(expected, actual))), default=0.0)
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        difference = abs(float(expected) - float(actual))
        if not math.isfinite(difference) or difference > TOLERANCE:
            raise ValueError(f"Recorded numeric value differs at {path}: {difference}")
        return difference
    if expected != actual:
        raise ValueError(f"Recorded value differs at {path}: {expected!r} != {actual!r}")
    return 0.0


def replay(name, work):
    import numpy as np
    from auto_adapter.design_validation import _Recorder, _execution, _json
    from auto_adapter.design_measurements import evaluate_measurements
    from auto_adapter.scene_runtime import build_scene_driver, load_scene_cases, reset_scene_driver

    original_dir = SOURCE / "validation" / CLIPS[name]
    original = read_json(original_dir / "result.json")
    saved = read_json(original_dir / "samples.json")
    design = read_json(SOURCE / "design/capability_design.json")
    suite = load_scene_cases(SOURCE / "design/scene_cases.yaml", design=design)
    case = next(case for case in suite["cases"] if case["case_id"] == original["case_id"])
    scene = SOURCE / "design/scenes" / case["scene"] / "scene.xml"
    robot = build_scene_driver(original_dir / "driver.py", scene, work_dir=work / "driver")
    reset_scene_driver(robot, case["initial_state"])
    model, data = robot.model, robot.data
    if model.nmocap:
        raise ValueError("Presentation replay does not support mocap bodies")
    recorder = _Recorder(model, data, _execution(case), work / "harness-video.mp4")
    if recorder.renderer is None:
        raise RuntimeError(f"The original Harness recorder needs graphics: {recorder.errors}")

    def full_state():
        return (float(data.time), data.qpos.copy(), data.qvel.copy(), data.ctrl.copy())

    states = [full_state()]
    original_capture = recorder._capture_frame

    def capture_with_state(step):
        # The original Harness calls this after mj_forward and capture_sample.
        original_capture(step)
        states.append(full_state())

    recorder._capture_frame = capture_with_state
    camera = {key: float(getattr(recorder.camera, key))
              for key in ("azimuth", "elevation", "distance")}
    camera["lookat"] = recorder.camera.lookat.tolist()
    recorder.install()
    try:
        getattr(robot, original["method_name"])(request=case["request"])
    finally:
        recorder.uninstall()
        video = recorder.finish()
    if not video["ok"]:
        raise RuntimeError(f"Harness capture failed: {video['errors']}")
    if recorder.step_count != original["physics_steps"]:
        raise ValueError("Replay physics step count differs")
    maximum_error = compare_saved(saved["samples"], recorder.samples, "physics")
    maximum_error = max(maximum_error,
                        compare_saved(original["initial_sample"], recorder.samples[0], "initial"),
                        compare_saved(original["final_sample"], recorder.samples[-1], "final"))
    measurements = [_json(item) for item in evaluate_measurements(design, case, recorder.samples)]
    metric_error = compare_saved(original["metrics"]["measurements"], measurements, "measurements")
    physical_passed = bool(measurements) and all(item["ok"] for item in measurements)
    if physical_passed != original["ok"]:
        raise ValueError("Capability verdict differs from the saved validation")

    times = np.asarray([state[0] for state in states])
    duration = float(times[-1] - times[0])
    compare_saved(original["sim_elapsed_s"], duration, "elapsed_time")
    frame_count = math.ceil(round(duration * FPS, 9)) + 1
    targets = np.minimum(times[0] + np.arange(frame_count) / FPS, times[-1])
    indices = np.rint((targets - times[0]) / model.opt.timestep).astype(int)
    indices = np.clip(indices, 0, len(states) - 1)
    indices[0], indices[-1] = 0, len(states) - 1
    sample_error = float(np.max(np.abs(times[indices] - targets)))
    if sample_error > model.opt.timestep / 2 + TOLERANCE:
        raise ValueError("Frame sampling differs from the nearest real physics state")
    selected = [states[index] for index in indices]
    np.savez_compressed(work / "frame_states.npz",
                        time=np.asarray([state[0] for state in selected]),
                        qpos=np.stack([state[1] for state in selected]),
                        qvel=np.stack([state[2] for state in selected]),
                        ctrl=np.stack([state[3] for state in selected]))
    report = {
        "kind": "presentation-replay-of-recorded-capability-validation",
        "source": str(original_dir.relative_to(ROOT)), "method": original["method_name"],
        "request": case["request"], "model_calls": 0, "new_experiment_evidence": False,
        "saved_samples_compared": len(recorder.samples), "maximum_saved_sample_error": maximum_error,
        "contacts_compared": True, "initial_and_final_samples_compared": True,
        "measurement_error": metric_error, "capability_passed": physical_passed,
        "measurements": measurements, "absolute_comparison_tolerance": TOLERANCE,
        "scene_path": str(scene), "camera": camera,
        "frame_count": frame_count, "fps": FPS, "simulation_duration_s": duration,
        "encoded_duration_s": frame_count / FPS, "maximum_sample_time_error_s": sample_error,
        "terminal_presentation_hold_s": frame_count / FPS - duration,
    }
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{name}: {len(recorder.samples)} saved samples/contacts match; "
          f"max error {maximum_error}; {frame_count} frames; capability passed={physical_passed}", flush=True)


def render(name, work, assets):
    import mujoco
    import numpy as np

    report = read_json(work / "replay_report.json")
    states = np.load(work / "frame_states.npz")
    model = mujoco.MjModel.from_xml_path(report["scene_path"])
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, WIDTH)
    model.vis.global_.offheight = max(model.vis.global_.offheight, HEIGHT)
    data = mujoco.MjData(model)
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    for key, value in report["camera"].items():
        if key == "lookat":
            camera.lookat[:] = value
        else:
            setattr(camera, key, value)
    # The validation view includes a distant lane marker. Frame the full robot
    # trajectory for presentation, without changing its poses or any physics.
    root_body = int(model.body_rootid[model.body("base_link").id])
    robot_geoms = np.asarray([i for i in range(model.ngeom)
                             if model.body_rootid[model.geom_bodyid[i]] == root_body])
    radii = model.geom_rbound[robot_geoms, None]
    lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
    for positions in states["qpos"]:
        data.qpos[:] = positions
        mujoco.mj_forward(model, data)
        centers = data.geom_xpos[robot_geoms]
        lower = np.minimum(lower, np.min(centers - radii, axis=0))
        upper = np.maximum(upper, np.max(centers + radii, axis=0))
    camera.lookat[:] = (lower + upper) / 2
    camera.distance = max(1.6, 1.6 * float(np.max(upper - lower)))
    report["presentation_camera"] = {
        "framing": "fixed view around the complete recorded robot trajectory",
        "lookat": camera.lookat.tolist(), "distance": camera.distance,
        "azimuth": camera.azimuth, "elevation": camera.elevation,
    }
    temporary = work / "presentation-hd.mp4"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "pipe:0",
               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        with mujoco.Renderer(model, width=WIDTH, height=HEIGHT) as renderer:
            for index, time in enumerate(states["time"]):
                data.time = time
                data.qpos[:] = states["qpos"][index]
                data.qvel[:] = states["qvel"][index]
                data.ctrl[:] = states["ctrl"][index]
                mujoco.mj_forward(model, data)
                renderer.update_scene(data, camera=camera)
                process.stdin.write(renderer.render().tobytes())
    finally:
        process.stdin.close()
        code = process.wait()
    if code:
        raise RuntimeError(f"FFmpeg encoding failed: {code}")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(temporary), "-f", "null", "-"], check=True)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames,duration,codec_name,pix_fmt",
        "-of", "json", str(temporary)]))["streams"][0]
    if (probe["width"], probe["height"], probe["r_frame_rate"], int(probe["nb_read_frames"])) != (
            WIDTH, HEIGHT, "30/1", report["frame_count"]):
        raise ValueError(f"Encoded media differs from requested dimensions/timing: {probe}")
    assets.mkdir(parents=True, exist_ok=True)
    destination = assets / f"{name}.mp4"
    import shutil
    shutil.copyfile(temporary, destination)
    poster = assets / f"{name}.jpg"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss",
                    str(report["encoded_duration_s"] * 0.4), "-i", str(destination),
                    "-frames:v", "1", "-q:v", "2", str(poster)], check=True)
    report["render"] = {"ffprobe": probe, "crf": 19, "video": str(destination),
                        "poster": str(poster), "bytes": destination.stat().st_size}
    (work / "replay_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", choices=["all", *CLIPS], default="all")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--assets-dir", type=Path, default=ROOT / "website/assets/robots")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--replay-only", action="store_true")
    modes.add_argument("--render-only", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    work = (args.work_dir or Path(tempfile.mkdtemp(prefix="go2-presentation-"))).resolve()
    assets = args.assets_dir.resolve()
    if not args.worker:
        print(f"Presentation work directory: {work}", flush=True)
        for name in CLIPS if args.clip == "all" else [args.clip]:
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--clip", name,
                       "--work-dir", str(work), "--assets-dir", str(assets)]
            if args.replay_only:
                command.append("--replay-only")
            if args.render_only:
                command.append("--render-only")
            subprocess.run(command, check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return
    work = work / args.clip
    work.mkdir(parents=True, exist_ok=True)
    if not args.render_only:
        replay(args.clip, work)
    if not args.replay_only:
        render(args.clip, work, assets)


if __name__ == "__main__":
    main()
