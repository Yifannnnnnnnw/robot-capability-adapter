#!/usr/bin/env python3
"""Render real MuJoCo scene PNGs for robot package MJCF entrypoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ROBOTS_ROOT = ROOT / "autoadapter" / "libraries" / "robots"
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 600
CONTACT_SHEET_COLUMNS = 5
CONTACT_SHEET_TILE = (320, 240)
CONTACT_SHEET_MARGIN = 24
CONTACT_SHEET_LABEL_HEIGHT = 50
PRESENTATION_ENTRYPOINTS = {
    "piper": "assets/scene.xml",
    "robotstudio_so101": "assets/scene.xml",
}


def _object_name(model: mujoco.MjModel, obj: mujoco.mjtObj, index: int) -> str:
    return mujoco.mj_id2name(model, obj, index) or ""


def _reset_for_view(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    for key_name in ("home", "stand", "neutral_pose", "task_start"):
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, key_name)
        if key_id >= 0:
            mujoco.mj_resetDataKeyframe(model, data, key_id)
            mujoco.mj_forward(model, data)
            return
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)


def _camera_for_model(model: mujoco.MjModel, data: mujoco.MjData) -> mujoco.MjvCamera:
    points: list[np.ndarray] = []
    for geom_id in range(model.ngeom):
        geom_type = int(model.geom_type[geom_id])
        geom_name = _object_name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
        if geom_type == int(mujoco.mjtGeom.mjGEOM_PLANE):
            continue
        if geom_name.lower() in {"floor", "ground", "groundplane"}:
            continue
        radius = max(float(model.geom_rbound[geom_id]), 0.03)
        pos = np.array(data.geom_xpos[geom_id], dtype=float)
        points.append(pos - radius)
        points.append(pos + radius)

    if points:
        stacked = np.vstack(points)
        lower = stacked.min(axis=0)
        upper = stacked.max(axis=0)
        center = (lower + upper) / 2.0
        radius = max(float(np.linalg.norm(upper - lower) / 2.0), 0.25)
    else:
        center = np.array(model.stat.center, dtype=float)
        radius = max(float(model.stat.extent), 0.25)

    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = center
    camera.distance = max(radius * 2.8, 0.8)
    camera.azimuth = 135
    camera.elevation = -24
    return camera


def _render_package(package_root: Path, width: int, height: int) -> Path:
    morphology = json.loads((package_root / "morphology.json").read_text(encoding="utf-8"))
    scene_entrypoint = PRESENTATION_ENTRYPOINTS.get(
        package_root.parent.name, morphology["mjcf_entrypoint"]
    )
    scene_path = package_root / scene_entrypoint
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    model.vis.global_.offwidth = max(int(model.vis.global_.offwidth), width)
    model.vis.global_.offheight = max(int(model.vis.global_.offheight), height)
    data = mujoco.MjData(model)
    _reset_for_view(model, data)
    camera = _camera_for_model(model, data)

    renderer = mujoco.Renderer(model, height, width)
    try:
        renderer.update_scene(data, camera=camera)
        image = renderer.render()
    finally:
        renderer.close()

    output_path = package_root / "mujoco_scene.png"
    Image.fromarray(image).save(output_path)
    return output_path


def _write_contact_sheet(robots_root: Path) -> Path:
    scene_paths = sorted(robots_root.glob("*/1.0.0/mujoco_scene.png"))
    if not scene_paths:
        raise SystemExit(f"no mujoco_scene.png files found under {robots_root}")

    tile_width, tile_height = CONTACT_SHEET_TILE
    row_height = tile_height + CONTACT_SHEET_LABEL_HEIGHT
    rows = (len(scene_paths) + CONTACT_SHEET_COLUMNS - 1) // CONTACT_SHEET_COLUMNS
    sheet_width = (
        CONTACT_SHEET_MARGIN * (CONTACT_SHEET_COLUMNS + 1)
        + tile_width * CONTACT_SHEET_COLUMNS
    )
    sheet_height = CONTACT_SHEET_MARGIN + rows * row_height
    sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
    draw = ImageDraw.Draw(sheet)

    for index, scene_path in enumerate(scene_paths):
        row, column = divmod(index, CONTACT_SHEET_COLUMNS)
        x = CONTACT_SHEET_MARGIN + column * (tile_width + CONTACT_SHEET_MARGIN)
        y = CONTACT_SHEET_MARGIN + row * row_height
        with Image.open(scene_path) as scene:
            thumbnail = scene.convert("RGB").resize(
                CONTACT_SHEET_TILE, Image.Resampling.LANCZOS
            )
            sheet.paste(thumbnail, (x, y))
        draw.text((x, y + tile_height + 6), scene_path.parent.parent.name, fill="black")

    output_path = robots_root / "mujoco_scene_contact_sheet.png"
    sheet.save(output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robots-root", type=Path, default=ROBOTS_ROOT)
    parser.add_argument("--robot", action="append", default=[])
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    morphology_paths = sorted(args.robots_root.glob("*/1.0.0/morphology.json"))
    if args.robot:
        selected = set(args.robot)
        morphology_paths = [
            path for path in morphology_paths if path.parent.parent.name in selected
        ]
        missing = selected - {path.parent.parent.name for path in morphology_paths}
        if missing:
            raise SystemExit(f"unknown robot package(s): {', '.join(sorted(missing))}")
    if not morphology_paths:
        raise SystemExit(f"no morphology.json files found under {args.robots_root}")

    for morphology_path in morphology_paths:
        output_path = _render_package(morphology_path.parent, args.width, args.height)
        print(output_path.relative_to(ROOT))
    contact_sheet_path = _write_contact_sheet(args.robots_root)
    print(contact_sheet_path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
