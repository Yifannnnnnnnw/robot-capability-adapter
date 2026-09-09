"""Replay saved task_traces back through the synthesized driver to regenerate
the mp4 videos that the original run lost (imageio-ffmpeg backend missing).

Usage:
    python scripts/replay_traces_to_video.py [--artifacts DIR] [--overwrite] [--max N]

For each ``task_traces/*.jsonl``:
  1. Load the driver via ``driver.build()`` to get a fresh skel.
  2. Wrap skel with FrameCapture so renders accumulate on every sim step.
  3. Iterate JSONL: dispatch each ``actions[i] = {name, input}`` through
     ``_TOOL_REGISTRY`` (same dispatch the live agent used).
  4. Save the captured frames to ``recordings/<task_id>.mp4`` via ffmpeg.

Skips: trace files that map to an mp4 already > 1 KB (real video, not the
broken 8-byte TIFF stubs from the old runs).
"""
from __future__ import annotations
import argparse, importlib.util, json, sys, time, traceback
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.agent.task_planner import (  # noqa: E402
    _FrameCapture, _TOOL_REGISTRY,
)


def _load_driver_build(driver_py: Path):
    """Import driver.py from an artifact and return its build() callable."""
    spec = importlib.util.spec_from_file_location(
        f"driver_{driver_py.parent.name}", str(driver_py)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.build


def _task_id_from_trace(p: Path) -> str:
    """`conditional_pick_t0.jsonl` → `conditional_pick_t0` (preserve trial id)."""
    return p.stem


def _replay_one(trace_path: Path, artifact_dir: Path, overwrite: bool) -> dict:
    import os
    saved_cwd = Path.cwd()
    os.chdir(artifact_dir)  # driver.build() uses relative mjcf.xml path
    try:
        return _replay_one_impl(trace_path, artifact_dir, overwrite)
    finally:
        os.chdir(saved_cwd)


def _replay_one_impl(trace_path: Path, artifact_dir: Path, overwrite: bool) -> dict:
    task_id = _task_id_from_trace(trace_path)
    out_dir = artifact_dir / "recordings"
    out_dir.mkdir(exist_ok=True)
    mp4_path = out_dir / f"{task_id}.mp4"

    if not overwrite and mp4_path.exists() and mp4_path.stat().st_size > 1024:
        return {"task": task_id, "skipped": True, "mp4": str(mp4_path)}

    # Read iters
    iters = []
    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                iters.append(json.loads(line))
            except Exception as e:
                return {"task": task_id, "ok": False, "error": f"trace parse {e}"}
    if not iters:
        return {"task": task_id, "ok": False, "error": "empty trace"}

    # Build skel — try driver.py first, then driver_from_scratch.py
    driver_py = None
    for cand in ("driver.py", "driver_from_scratch.py"):
        p = artifact_dir / cand
        if p.exists():
            driver_py = p
            break
    if driver_py is None:
        return {"task": task_id, "ok": False, "error": "no driver.py"}
    try:
        build_fn = _load_driver_build(driver_py)
    except AttributeError:
        # from-scratch drivers may export different entry points
        import importlib.util as _u
        spec = _u.spec_from_file_location(
            f"driver_{artifact_dir.name}", str(driver_py))
        mod = _u.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        build_fn = None
        for name in ("build", "build_robot", "make", "main"):
            if hasattr(mod, name):
                build_fn = getattr(mod, name)
                break
        if build_fn is None and hasattr(mod, "Robot"):
            build_fn = mod.Robot  # from-scratch drivers expose `class Robot`
        if build_fn is None:
            return {"task": task_id, "ok": False,
                    "error": f"no build()/Robot in {driver_py.name}"}
    try:
        skel = build_fn()
    except TypeError as e:
        # `Robot(model, data[, study])` style — load MJCF and pass
        if "missing" in str(e) and ("model" in str(e) or "data" in str(e)):
            try:
                import mujoco
                mj_model = mujoco.MjModel.from_xml_path("mjcf.xml")
                mj_data = mujoco.MjData(mj_model)
                if "study" in str(e):
                    import json as _json
                    study = _json.load(open("study.json"))
                    skel = build_fn(mj_model, mj_data, study)
                else:
                    skel = build_fn(mj_model, mj_data)
                # Inject render() if Robot doesn't have one (needed by FrameCapture)
                if not hasattr(skel, "render"):
                    _r = mujoco.Renderer(mj_model, height=480, width=640)
                    skel.render = lambda: (_r.update_scene(mj_data), _r.render())[1]
                # Inject step() if missing (FrameCapture monkey-patches it)
                if not hasattr(skel, "step"):
                    skel.step = lambda n=1: [mujoco.mj_step(mj_model, mj_data) for _ in range(int(n))]
            except Exception as e2:
                return {"task": task_id, "ok": False, "error": f"Robot(model,data,...): {e2}"}
        else:
            return {"task": task_id, "ok": False, "error": f"build(): {e}"}
    except Exception as e:
        return {"task": task_id, "ok": False, "error": f"build(): {e}"}

    skel_cls = type(skel).__name__
    tool_specs = _TOOL_REGISTRY.get(skel_cls)
    if tool_specs is None:
        # Try parent classes
        for k in _TOOL_REGISTRY:
            if any(b.__name__ == k for b in type(skel).__mro__):
                tool_specs = _TOOL_REGISTRY[k]
                break
    if tool_specs is None:
        return {"task": task_id, "ok": False, "error": f"no tool registry for {skel_cls}"}

    capture = _FrameCapture(skel, capture_every=10, max_frames=3000)
    try:
        capture.snapshot()  # initial frame
    except Exception:
        pass

    n_actions = 0
    n_skipped = 0
    t0 = time.time()
    try:
        for it in iters:
            actions = it.get("actions", []) or []
            for a in actions:
                name = a.get("name")
                inp = a.get("input", {}) or {}
                spec = tool_specs.get(name)
                if spec is None:
                    n_skipped += 1
                    continue
                method = getattr(skel, name, None)
                if method is None:
                    n_skipped += 1
                    continue
                try:
                    args = spec["args"](inp)
                    kwargs = spec["kwargs"](inp)
                    method(*args, **kwargs)
                    n_actions += 1
                except Exception:
                    # Replay-time errors are usually graspable mismatches; keep going
                    n_skipped += 1
    finally:
        capture.uninstall()

    if not capture.frames:
        return {"task": task_id, "ok": False,
                "error": f"no frames captured (n_actions={n_actions})"}

    import imageio  # noqa: PLC0415
    try:
        imageio.mimsave(str(mp4_path), capture.frames,
                        format="FFMPEG", fps=30, codec="libx264",
                        pixelformat="yuv420p",
                        ffmpeg_params=["-movflags", "+faststart"])
    except Exception as e:
        return {"task": task_id, "ok": False, "error": f"mp4 save: {e}"}

    return {
        "task": task_id, "ok": True,
        "mp4": str(mp4_path),
        "size_kb": mp4_path.stat().st_size // 1024,
        "n_frames": len(capture.frames),
        "n_actions": n_actions,
        "n_skipped": n_skipped,
        "wall_sec": round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default=str(REPO_ROOT / "artifacts"),
                    help="Root artifacts dir (will scan for task_traces/)")
    ap.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing >1KB mp4s")
    ap.add_argument("--max", type=int, default=None,
                    help="Cap on total trials to replay (default: all)")
    ap.add_argument("--filter", default=None,
                    help="Substring filter on artifact dir name")
    args = ap.parse_args()

    artifacts_root = Path(args.artifacts)
    trace_dirs = sorted(artifacts_root.glob("*/task_traces"))
    if args.filter:
        trace_dirs = [d for d in trace_dirs if args.filter in d.parent.name]

    print(f"[replay] found {len(trace_dirs)} artifact dirs with task_traces/")
    total = ok = skip = fail = 0
    for tdir in trace_dirs:
        artifact_dir = tdir.parent
        trace_files = sorted(tdir.glob("*.jsonl"))
        print(f"\n=== {artifact_dir.name}  ({len(trace_files)} traces) ===")
        for tf in trace_files:
            if args.max is not None and total >= args.max:
                print(f"[stop] reached --max {args.max}")
                break
            total += 1
            try:
                r = _replay_one(tf, artifact_dir, overwrite=args.overwrite)
            except Exception as e:
                r = {"task": tf.stem, "ok": False,
                     "error": f"{type(e).__name__}: {e}"}
                traceback.print_exc(limit=2)
            if r.get("skipped"):
                skip += 1
                print(f"  - {r['task']:40s} SKIP (already saved)")
            elif r.get("ok"):
                ok += 1
                print(f"  ✓ {r['task']:40s} {r['n_frames']:4d} frames  "
                      f"{r['size_kb']:5d} KB  {r['wall_sec']}s  "
                      f"({r['n_actions']} actions, {r['n_skipped']} skipped)")
            else:
                fail += 1
                print(f"  ✗ {r['task']:40s} FAIL: {r.get('error')}")
        if args.max is not None and total >= args.max:
            break

    print(f"\n=== TOTAL: {total} traces, {ok} OK, {skip} skipped, {fail} fail ===")


if __name__ == "__main__":
    main()
