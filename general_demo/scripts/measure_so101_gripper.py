#!/usr/bin/env python3
"""Measure a deterministic SO-ARM101 gripper endpoint aperture proxy.

The proxy is the minimum distance from the moving-jaw collision mesh vertices
to the fixed ``gripperframe`` site.  It is used only once while preparing the
Translation record to decide which MuJoCo joint endpoint is the open endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aperture_proxy(model: mujoco.MjModel, data: mujoco.MjData) -> float:
    moving_body = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_BODY, "moving_jaw_so101_v1"
    )
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "gripperframe")
    if moving_body < 0 or site_id < 0:
        raise RuntimeError("model is missing moving jaw or gripperframe")
    candidates: list[float] = []
    for geom_id in range(model.ngeom):
        if int(model.geom_bodyid[geom_id]) != moving_body:
            continue
        if int(model.geom_group[geom_id]) != 3:
            continue
        mesh_id = int(model.geom_dataid[geom_id])
        if mesh_id < 0:
            continue
        first = int(model.mesh_vertadr[mesh_id])
        count = int(model.mesh_vertnum[mesh_id])
        vertices = np.asarray(model.mesh_vert[first : first + count], dtype=float)
        rotation = np.asarray(data.geom_xmat[geom_id], dtype=float).reshape(3, 3)
        world_vertices = vertices @ rotation.T + np.asarray(data.geom_xpos[geom_id])
        distances = np.linalg.norm(world_vertices - np.asarray(data.site_xpos[site_id]), axis=1)
        candidates.append(float(np.min(distances)))
    if not candidates:
        raise RuntimeError("model has no moving-jaw collision mesh")
    return min(candidates)


def measure(model_path: Path) -> dict[str, object]:
    model = mujoco.MjModel.from_xml_path(str(model_path))
    data = mujoco.MjData(model)
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "gripper")
    if joint_id < 0:
        raise RuntimeError("model is missing gripper joint")
    qpos_address = int(model.jnt_qposadr[joint_id])
    low, high = map(float, model.jnt_range[joint_id])

    values: list[dict[str, float]] = []
    for label, qpos in (("low", low), ("high", high)):
        mujoco.mj_resetData(model, data)
        data.qpos[qpos_address] = qpos
        mujoco.mj_forward(model, data)
        values.append(
            {
                "endpoint": label,
                "qpos_rad": qpos,
                "aperture_proxy_m": _aperture_proxy(model, data),
            }
        )
    larger = max(values, key=lambda value: value["aperture_proxy_m"])
    return {
        "schema_version": "1.0.0",
        "model_path": model_path.name,
        "model_sha256": _sha256(model_path),
        "measurement": "minimum moving-jaw collision-mesh vertex distance to gripperframe site",
        "endpoints": values,
        "open_endpoint": larger["endpoint"],
        "gripper_tick_increases_qpos": larger["endpoint"] == "high",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = measure(args.model.resolve())
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
