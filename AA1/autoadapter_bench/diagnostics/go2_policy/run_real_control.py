#!/usr/bin/env python3
"""Run a short Go2 velocity-policy control canary on the real AA1 MJCF."""

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


def _spec() -> Any:
    sys.path.insert(0, str(REPO_ROOT))
    from auto_adapter.skeletons.go2_velocity_policy import Go2VelocityPolicySpec

    names = tuple(
        f"{leg}_{suffix}_joint"
        for leg in ("FL", "FR", "RL", "RR")
        for suffix in ("hip", "thigh", "calf")
    )
    return Go2VelocityPolicySpec(
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


def run(duration: float = 1.0) -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT))
    from auto_adapter.skeletons.go2_velocity_policy import Go2VelocityPolicySkeleton

    policy = Go2VelocityPolicySkeleton.from_mjcf(str(SCENE), _spec())
    position_before, rotation_before = policy.get_base_pose()
    ctrl_before = np.asarray(policy.data.ctrl, dtype=float).copy()
    result = policy.command_planar_velocity(
        0.25,
        0.0,
        0.0,
        duration=duration,
    )
    position_after, rotation_after = policy.get_base_pose()
    twist = policy.get_base_twist()
    state_finite = bool(
        np.isfinite(policy.data.qpos).all()
        and np.isfinite(policy.data.qvel).all()
        and np.isfinite(policy.data.ctrl).all()
    )
    return {
        "model_generated": False,
        "scene": str(SCENE),
        "real_mujoco": True,
        "requested_command": {"vx_m_s": 0.25, "vy_m_s": 0.0, "yaw_rate_rad_s": 0.0},
        "requested_duration_s": float(duration),
        "sim_time_s": float(policy.data.time),
        "physics_steps": int(result["physics_steps"]),
        "policy_inference_count": int(result["policy_inference_count"]),
        "base_position_before_m": position_before.tolist(),
        "base_position_after_m": position_after.tolist(),
        "base_displacement_m": (position_after - position_before).tolist(),
        "base_rotation_before": rotation_before.tolist(),
        "base_rotation_after": rotation_after.tolist(),
        "base_twist_after": {
            "linear_world_m_s": twist["linear_world_m_s"].tolist(),
            "linear_body_yaw_m_s": twist["linear_body_yaw_m_s"].tolist(),
            "angular_world_rad_s": twist["angular_world_rad_s"].tolist(),
            "yaw_rate_rad_s": float(twist["yaw_rate_rad_s"]),
        },
        "ctrl_changed_from_reset": bool(
            not np.array_equal(ctrl_before, np.asarray(policy.data.ctrl, dtype=float))
        ),
        "state_finite": state_finite,
        "ok": state_finite
        and int(result["physics_steps"]) > 0
        and bool(
            not np.array_equal(ctrl_before, np.asarray(policy.data.ctrl, dtype=float))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.duration)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
