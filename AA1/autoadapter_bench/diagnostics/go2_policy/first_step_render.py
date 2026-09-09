#!/usr/bin/env python3
"""Verify AA1 inherited step and first render before policy resolution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import mujoco
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENE = REPO_ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml"


def run() -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT))
    from auto_adapter.skeletons.go2_velocity_policy import Go2VelocityPolicySkeleton
    from auto_adapter.skeletons.go2_velocity_policy import Go2VelocityPolicySpec

    names = tuple(
        f"{leg}_{suffix}_joint"
        for leg in ("FL", "FR", "RL", "RR")
        for suffix in ("hip", "thigh", "calf")
    )
    spec = Go2VelocityPolicySpec(
        base_body_name="base_link",
        joint_names=names,
        actuator_names=tuple(name.removesuffix("_joint") for name in names),
        default_joint_angles=(
            0.1,
            0.8,
            -1.5,
            -0.1,
            0.8,
            -1.5,
            0.1,
            1.0,
            -1.5,
            -0.1,
            1.0,
            -1.5,
        ),
    )
    model = mujoco.MjModel.from_xml_path(str(SCENE))
    data = mujoco.MjData(model)
    policy = Go2VelocityPolicySkeleton(model=model, data=data, spec=spec)
    policy.step(1)
    frame = policy.render(height=64, width=64)
    return {
        "model_generated": False,
        "scene": str(SCENE),
        "real_mujoco": True,
        "sim_time_s": float(data.time),
        "physics_steps": 1,
        "frame_shape": list(frame.shape),
        "frame_dtype": str(frame.dtype),
        "frame_finite": bool(np.isfinite(frame).all()),
        "frame_min": int(frame.min()),
        "frame_max": int(frame.max()),
        "ok": bool(
            np.isclose(data.time, model.opt.timestep, atol=1.0e-12)
            and frame.shape == (64, 64, 3)
            and np.isfinite(frame).all()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
