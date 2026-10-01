#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render robot thumbnails + pickbench scene PNGs for the paper.

Outputs to `paper/latex/figures/`:
  zoo_so101.png, zoo_piper.png, zoo_ur5e.png, zoo_franka.png,
  zoo_kuka.png, zoo_go2.png, zoo_a1.png, zoo_anymal.png
  pickbench_render.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "paper" / "latex" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# (label, mjcf path relative to assets/mjcf, camera config)
ROBOTS = [
    ("zoo_so101",  "so101_mujoco.xml",                         {"distance": 0.58, "elevation": -20, "azimuth": 145, "lookat": (0.20, 0.0, 0.17)}),
    ("zoo_piper",  "piper/scene.xml",                          {"distance": 0.88, "elevation": -22, "azimuth": 135, "lookat": (0.3, 0.0, 0.23)}),
    ("zoo_ur5e",   "universal_robots_ur5e/scene.xml",          {"distance": 1.12, "elevation": -20, "azimuth": 130, "lookat": (0.0, 0.0, 0.38)}),
    ("zoo_franka", "franka_panda/scene.xml",                   {"distance": 1.12, "elevation": -22, "azimuth": 135, "lookat": (0.15, 0.0, 0.42)}),
    ("zoo_kuka",   "kuka_iiwa_14/scene.xml",                   {"distance": 1.2,  "elevation": -22, "azimuth": 130, "lookat": (0.0, 0.0, 0.47)}),
    ("zoo_go2",    "go2/go2_scene.xml",                        {"distance": 1.05, "elevation": -18, "azimuth": 120, "lookat": (0.0, 0.0, 0.18)}),
    ("zoo_a1",     "unitree_a1/scene.xml",                     {"distance": 1.0,  "elevation": -18, "azimuth": 125, "lookat": (0.0, 0.0, 0.18)}),
    ("zoo_anymal", "anybotics_anymal_c/scene.xml",             {"distance": 1.28, "elevation": -18, "azimuth": 130, "lookat": (0.0, 0.0, 0.32)}),
]

WIDTH = 630
HEIGHT = 420


def render_scene(mjcf_path: Path, cam_cfg: dict, out_path: Path) -> None:
    model = mujoco.MjModel.from_xml_path(str(mjcf_path))
    data = mujoco.MjData(model)
    # Move to its "settle" pose if a keyframe named "home" exists; otherwise just step a few times
    try:
        kf_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        if kf_id >= 0:
            mujoco.mj_resetDataKeyframe(model, data, kf_id)
    except Exception:
        pass
    mujoco.mj_forward(model, data)
    # Step physics 200 ticks so quadrupeds settle on the floor
    for _ in range(200):
        mujoco.mj_step(model, data)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.distance = float(cam_cfg["distance"])
    cam.elevation = float(cam_cfg["elevation"])
    cam.azimuth = float(cam_cfg["azimuth"])
    cam.lookat[:] = np.array(cam_cfg["lookat"], dtype=float)
    with mujoco.Renderer(model, height=HEIGHT, width=WIDTH) as r:
        r.update_scene(data, camera=cam)
        img = r.render()
    Image.fromarray(img).save(out_path)
    print(f"  wrote {out_path}  ({img.shape[1]}x{img.shape[0]})")


def main() -> int:
    mjcf_dir = REPO_ROOT / "assets" / "mjcf"
    # Robot zoo lineup
    print("Rendering 8 robot thumbnails...")
    for label, rel_path, cam in ROBOTS:
        mjcf = mjcf_dir / rel_path
        if not mjcf.exists():
            print(f"  MISSING {mjcf} — skipping {label}")
            continue
        try:
            render_scene(mjcf, cam, OUT_DIR / f"{label}.png")
        except Exception as e:
            print(f"  FAIL {label}: {type(e).__name__}: {e}")

    # Pickbench scene render — top-down + slight angle
    print("\nRendering pickbench scene...")
    pickbench = mjcf_dir / "piper" / "pickbench.xml"
    cam = {"distance": 1.2, "elevation": -55, "azimuth": 90,
           "lookat": (0.40, 0.0, 0.20)}
    if pickbench.exists():
        try:
            render_scene(pickbench, cam, OUT_DIR / "pickbench_render.png")
        except Exception as e:
            print(f"  FAIL pickbench: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
