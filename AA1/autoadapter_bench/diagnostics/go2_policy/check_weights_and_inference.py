#!/usr/bin/env python3
"""Check the retained Go2 NumPy policy against its fixed TorchScript export.

The upstream file is a TorchScript zip archive.  Reading its float storages
directly keeps this provenance check independent of a Torch runtime and does
not add Torch to AA1.  The storage order is pinned to the requested upstream
revision and is checked against the expected tensor shapes before comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_RESOURCE = REPO_ROOT / "auto_adapter" / "skeletons" / "data" / "go2_velocity_policy.npz"
POLICY_SOURCE_REVISION = "30e74dc507bec7a642a8c98be26081f2c6f0822d"

REQUIRED_SHAPES: dict[str, tuple[int, ...]] = {
    "student_encoder_0_weight": (512, 225),
    "student_encoder_0_bias": (512,),
    "student_encoder_2_weight": (256, 512),
    "student_encoder_2_bias": (256,),
    "student_encoder_4_weight": (32, 256),
    "student_encoder_4_bias": (32,),
    "actor_0_weight": (512, 77),
    "actor_0_bias": (512,),
    "actor_2_weight": (256, 512),
    "actor_2_bias": (256,),
    "actor_4_weight": (128, 256),
    "actor_4_bias": (128,),
    "actor_6_weight": (12, 128),
    "actor_6_bias": (12,),
}

# These are the FloatStorage records in the fixed upstream TorchScript export.
CHECKPOINT_STORAGE_ID: dict[str, str] = {
    "student_encoder_0_weight": "1",
    "student_encoder_0_bias": "2",
    "student_encoder_2_weight": "3",
    "student_encoder_2_bias": "4",
    "student_encoder_4_weight": "5",
    "student_encoder_4_bias": "6",
    "actor_0_weight": "7",
    "actor_0_bias": "8",
    "actor_2_weight": "9",
    "actor_2_bias": "10",
    "actor_4_weight": "11",
    "actor_4_bias": "12",
    "actor_6_weight": "13",
    "actor_6_bias": "14",
}


def _checkpoint_tensors(checkpoint: Path) -> dict[str, np.ndarray]:
    """Read fixed-revision TorchScript FloatStorage records as float32 arrays."""

    with zipfile.ZipFile(checkpoint) as archive:
        names = set(archive.namelist())
        prefix = "policy/"
        if "policy/data.pkl" not in names or "policy/version" not in names:
            raise ValueError("checkpoint is not the expected TorchScript archive")
        result: dict[str, np.ndarray] = {}
        for tensor_name, storage_id in CHECKPOINT_STORAGE_ID.items():
            member = f"{prefix}data/{storage_id}"
            if member not in names:
                raise ValueError(f"checkpoint is missing storage {storage_id}")
            value = np.frombuffer(archive.read(member), dtype="<f4").copy()
            expected_size = int(np.prod(REQUIRED_SHAPES[tensor_name]))
            if value.size != expected_size:
                raise ValueError(
                    f"{tensor_name} storage has {value.size} values; "
                    f"expected {expected_size}"
                )
            result[tensor_name] = value.reshape(REQUIRED_SHAPES[tensor_name])
        return result


def _retained_tensors(resource: Path) -> dict[str, np.ndarray]:
    with np.load(resource, allow_pickle=False) as retained:
        if set(retained.files) != set(REQUIRED_SHAPES):
            raise ValueError("retained NPZ has an unexpected tensor inventory")
        result = {name: retained[name].copy() for name in REQUIRED_SHAPES}
    for name, shape in REQUIRED_SHAPES.items():
        if result[name].shape != shape or result[name].dtype != np.float32:
            raise ValueError(f"retained tensor {name!r} has the wrong shape or dtype")
        if not np.isfinite(result[name]).all():
            raise ValueError(f"retained tensor {name!r} contains non-finite values")
    return result


def _elu(value: np.ndarray) -> np.ndarray:
    result = value.copy()
    negative = result <= 0.0
    result[negative] = np.expm1(result[negative])
    return result


def _numpy_forward(
    weights: dict[str, np.ndarray], observations: list[np.ndarray]
) -> list[np.ndarray]:
    """Evaluate the exported history encoder and actor without AA1 imports."""

    history = np.zeros((5, 45), dtype=np.float32)
    actions: list[np.ndarray] = []
    for observation in observations:
        value = np.asarray(observation, dtype=np.float32)
        history = np.concatenate((history[1:], value[None, :]), axis=0)
        latent = _elu(
            history.reshape(-1) @ weights["student_encoder_0_weight"].T
            + weights["student_encoder_0_bias"]
        )
        latent = _elu(
            latent @ weights["student_encoder_2_weight"].T
            + weights["student_encoder_2_bias"]
        )
        latent = (
            latent @ weights["student_encoder_4_weight"].T
            + weights["student_encoder_4_bias"]
        )
        latent = latent / max(float(np.linalg.norm(latent)), 1.0e-12)
        actor = np.concatenate((latent, value)).astype(np.float32)
        actor = _elu(
            actor @ weights["actor_0_weight"].T + weights["actor_0_bias"]
        )
        actor = _elu(
            actor @ weights["actor_2_weight"].T + weights["actor_2_bias"]
        )
        actor = _elu(
            actor @ weights["actor_4_weight"].T + weights["actor_4_bias"]
        )
        action = (
            actor @ weights["actor_6_weight"].T + weights["actor_6_bias"]
        ).astype(np.float32)
        actions.append(action)
    return actions


def _local_inference(observations: list[np.ndarray]) -> list[np.ndarray]:
    sys.path.insert(0, str(REPO_ROOT))
    import mujoco

    from auto_adapter.skeletons.go2_velocity_policy import (
        Go2VelocityPolicySkeleton,
        Go2VelocityPolicySpec,
    )

    model = mujoco.MjModel.from_xml_path(
        str(REPO_ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml")
    )
    data = mujoco.MjData(model)
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
    policy = Go2VelocityPolicySkeleton(model=model, data=data, spec=spec)
    policy._resolve()
    return [policy._infer(value) for value in observations]


def check(checkpoint: Path) -> dict[str, Any]:
    upstream = _checkpoint_tensors(checkpoint)
    retained = _retained_tensors(POLICY_RESOURCE)
    tensor_checks: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_SHAPES:
        difference = np.abs(upstream[name] - retained[name])
        tensor_checks[name] = {
            "shape": list(REQUIRED_SHAPES[name]),
            "exact_float32_match": bool(np.array_equal(upstream[name], retained[name])),
            "max_abs_error": float(np.max(difference)),
        }

    observations = [
        np.zeros(45, dtype=np.float32),
        np.linspace(-0.4, 0.4, 45, dtype=np.float32),
    ]
    exported_actions = _numpy_forward(upstream, observations)
    retained_actions = _numpy_forward(retained, observations)
    local_actions = _local_inference(observations)
    inference_checks = []
    for index, (exported, retained_action, local) in enumerate(
        zip(exported_actions, retained_actions, local_actions, strict=True)
    ):
        inference_checks.append(
            {
                "input": "zero" if index == 0 else "ramp",
                "exported_vs_retained_max_abs_error": float(
                    np.max(np.abs(exported - retained_action))
                ),
                "retained_vs_aa1_max_abs_error": float(
                    np.max(np.abs(retained_action - local))
                ),
                "aa1_action": local.tolist(),
            }
        )

    return {
        "artifact_id": "go2-cts-150k",
        "source_revision": POLICY_SOURCE_REVISION,
        "checkpoint": str(checkpoint),
        "retained_resource": str(POLICY_RESOURCE),
        "model_generated": False,
        "torch_runtime_used": False,
        "tensor_checks": tensor_checks,
        "inference_checks": inference_checks,
        "ok": all(
            item["exact_float32_match"] for item in tensor_checks.values()
        )
        and all(
            item["exported_vs_retained_max_abs_error"] == 0.0
            and item["retained_vs_aa1_max_abs_error"] <= 2.0e-6
            for item in inference_checks
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check(args.checkpoint.resolve())
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
