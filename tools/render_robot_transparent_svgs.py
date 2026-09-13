#!/usr/bin/env python3
"""Render self-contained transparent robot SVGs from AA1's simulation scenes."""

from __future__ import annotations

import argparse
import base64
import html
import io
import math
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from aa1_rendering import add_robot_arguments, robot_scenes

DEFAULT_SIZE = 800
DEFAULT_SUPERSAMPLE = 3
FRAME_MARGIN = 0.08


def _reset_for_view(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    for key_name in ("home", "stand", "neutral_pose", "task_start"):
        key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, key_name)
        if key_id >= 0:
            mujoco.mj_resetDataKeyframe(model, data, key_id)
            mujoco.mj_forward(model, data)
            return
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)


def _robot_root_body_ids(model: mujoco.MjModel) -> set[int]:
    """Locate AA1 robot subtrees through joint motors and drone thrust sites."""
    roots: set[int] = set()
    for actuator_id in range(model.nu):
        transmission = int(model.actuator_trntype[actuator_id])
        target_id = int(model.actuator_trnid[actuator_id, 0])
        if transmission in (mujoco.mjtTrn.mjTRN_JOINT, mujoco.mjtTrn.mjTRN_JOINTINPARENT):
            body_id = int(model.jnt_bodyid[target_id])
        elif transmission == mujoco.mjtTrn.mjTRN_SITE:
            body_id = int(model.site_bodyid[target_id])
        else:
            # AA1's tendon-driven parts share roots with joint-driven parts.
            continue
        while int(model.body_parentid[body_id]) != 0:
            body_id = int(model.body_parentid[body_id])
        if body_id:
            roots.add(body_id)

    if not roots:
        raise ValueError("AA1 scene has no robot bodies driven by joint or site actuators")
    return roots


def _is_descendant(
    model: mujoco.MjModel, body_id: int, root_body_ids: set[int]
) -> bool:
    current = body_id
    while current != 0:
        if current in root_body_ids:
            return True
        current = int(model.body_parentid[current])
    return False


def _robot_geom_ids(model: mujoco.MjModel) -> set[int]:
    root_body_ids = _robot_root_body_ids(model)
    robot_body_ids = {
        body_id
        for body_id in range(1, model.nbody)
        if _is_descendant(model, body_id, root_body_ids)
    }
    geom_ids = {
        geom_id
        for geom_id in range(model.ngeom)
        if int(model.geom_bodyid[geom_id]) in robot_body_ids
    }
    if not geom_ids:
        raise ValueError("robot body subtrees contain no geoms")
    return geom_ids


def _camera_for_robot(
    model: mujoco.MjModel, data: mujoco.MjData, geom_ids: set[int]
) -> mujoco.MjvCamera:
    points: list[np.ndarray] = []
    for geom_id in geom_ids:
        radius = max(float(model.geom_rbound[geom_id]), 0.01)
        position = np.asarray(data.geom_xpos[geom_id], dtype=float)
        points.extend((position - radius, position + radius))

    bounds = np.vstack(points)
    lower = bounds.min(axis=0)
    upper = bounds.max(axis=0)
    center = (lower + upper) / 2.0
    radius = max(float(np.linalg.norm(upper - lower) / 2.0), 0.25)

    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = center
    camera.distance = radius * 3.0
    camera.azimuth = 135
    camera.elevation = -20
    return camera


def _render_robot_rgba(
    scene_path: Path, size: int, supersample: int
) -> Image.Image:
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    _reset_for_view(model, data)

    geom_ids = _robot_geom_ids(model)
    camera = _camera_for_robot(model, data, geom_ids)

    environment_geom_ids = sorted(set(range(model.ngeom)) - geom_ids)
    if environment_geom_ids:
        model.geom_rgba[environment_geom_ids, 3] = 0.0

    render_size = size * supersample
    model.vis.global_.offwidth = max(int(model.vis.global_.offwidth), render_size)
    model.vis.global_.offheight = max(int(model.vis.global_.offheight), render_size)
    model.vis.quality.offsamples = 1
    renderer = mujoco.Renderer(model, render_size, render_size)
    try:
        renderer.update_scene(data, camera=camera)
        rgb = renderer.render().copy()
        renderer.enable_segmentation_rendering()
        renderer.update_scene(data, camera=camera)
        segmentation = renderer.render().copy()
    finally:
        renderer.close()

    mask = (
        segmentation[..., 1] == int(mujoco.mjtObj.mjOBJ_GEOM)
    ) & np.isin(segmentation[..., 0], np.fromiter(geom_ids, dtype=np.int32))
    if not np.any(mask):
        raise ValueError("segmentation render contains no robot pixels")

    ys, xs = np.where(mask)
    left, right = int(xs.min()), int(xs.max()) + 1
    top, bottom = int(ys.min()), int(ys.max()) + 1
    cropped_rgb = rgb[top:bottom, left:right]
    cropped_alpha = mask[top:bottom, left:right].astype(np.uint8) * 255

    crop_height, crop_width = cropped_alpha.shape
    margin = math.ceil(max(crop_width, crop_height) * FRAME_MARGIN)
    canvas_side = max(crop_width, crop_height) + 2 * margin
    rgb_canvas = np.zeros((canvas_side, canvas_side, 3), dtype=np.uint8)
    alpha_canvas = np.zeros((canvas_side, canvas_side), dtype=np.uint8)
    x_offset = (canvas_side - crop_width) // 2
    y_offset = (canvas_side - crop_height) // 2
    rgb_canvas[
        y_offset : y_offset + crop_height, x_offset : x_offset + crop_width
    ] = cropped_rgb
    alpha_canvas[
        y_offset : y_offset + crop_height, x_offset : x_offset + crop_width
    ] = cropped_alpha

    alpha = Image.fromarray(alpha_canvas).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    alpha_array = np.asarray(alpha, dtype=np.float32)
    premultiplied = (
        rgb_canvas.astype(np.float32) * (alpha_canvas[..., None] / 255.0)
    )
    resized_premultiplied = np.empty((size, size, 3), dtype=np.float32)
    for channel in range(3):
        channel_image = Image.fromarray(
            np.rint(premultiplied[..., channel]).astype(np.uint8)
        ).resize((size, size), Image.Resampling.LANCZOS)
        resized_premultiplied[..., channel] = np.asarray(
            channel_image, dtype=np.float32
        )

    resized_rgb = np.zeros((size, size, 3), dtype=np.uint8)
    visible = alpha_array > 0
    resized_rgb[visible] = np.clip(
        resized_premultiplied[visible] * 255.0 / alpha_array[visible, None],
        0,
        255,
    ).astype(np.uint8)
    rgba = np.dstack((resized_rgb, alpha_array.astype(np.uint8)))
    image = Image.fromarray(rgba)
    if image.getchannel("A").getextrema() != (0, 255):
        raise ValueError("rendered PNG is not a bounded transparent cutout")
    return image


def _svg_document(image: Image.Image, robot_id: str, size: int) -> str:
    png = io.BytesIO()
    image.save(png, format="PNG", optimize=True, compress_level=9)
    payload = base64.b64encode(png.getvalue()).decode("ascii")
    title = html.escape(robot_id.replace("_", " ").replace("-", " ").title())
    description = html.escape(
        f"Transparent MuJoCo render of AA1's {robot_id} robot."
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" aria-labelledby="title desc">\n'
        f"  <title id=\"title\">{title}</title>\n"
        f"  <desc id=\"desc\">{description}</desc>\n"
        f'  <image href="data:image/png;base64,{payload}" x="0" y="0" '
        f'width="{size}" height="{size}" preserveAspectRatio="xMidYMid meet"/>\n'
        "</svg>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    add_robot_arguments(parser, "robot_svgs")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument(
        "--supersample", type=int, default=DEFAULT_SUPERSAMPLE
    )
    args = parser.parse_args()
    if args.size <= 0 or args.supersample <= 0:
        raise SystemExit("--size and --supersample must be positive")

    scenes = robot_scenes(args.aa1_root, args.robot)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for robot_id, scene_path in scenes.items():
        image = _render_robot_rgba(scene_path, args.size, args.supersample)
        output_path = args.output_dir / f"{robot_id}.svg"
        output_path.write_text(
            _svg_document(image, robot_id, args.size), encoding="utf-8"
        )
        print(f"{output_path} <- {scene_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
