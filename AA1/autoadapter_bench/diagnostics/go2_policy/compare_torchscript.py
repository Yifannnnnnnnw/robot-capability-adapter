#!/usr/bin/env python3
"""Execute the fixed upstream TorchScript export and compare AA1 outputs.

Run this script with a temporary environment containing Torch and NumPy.  The
AA1 implementation is exercised through its own ``_infer`` method, while the
source side is loaded by ``torch.jit.load``; no checkpoint conversion or
training is performed here.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import torch


REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_RESOURCE = REPO_ROOT / "auto_adapter" / "skeletons" / "data" / "go2_velocity_policy.npz"


def _aa1_inference(observations: list[np.ndarray]) -> list[np.ndarray]:
    # Load the two AA1 modules without importing ``auto_adapter.__init__``;
    # this temporary Torch environment intentionally contains no AA1 extras
    # such as PyYAML.
    package_name = "_aa1_go2_compare"
    package = types.ModuleType(package_name)
    package.__path__ = [str(REPO_ROOT / "auto_adapter" / "skeletons")]
    sys.modules[package_name] = package
    base_name = f"{package_name}.base"
    base_spec = importlib.util.spec_from_file_location(
        base_name, REPO_ROOT / "auto_adapter" / "skeletons" / "base.py"
    )
    if base_spec is None or base_spec.loader is None:
        raise RuntimeError("could not load AA1 SkeletonBase")
    base_module = importlib.util.module_from_spec(base_spec)
    sys.modules[base_name] = base_module
    base_spec.loader.exec_module(base_module)
    policy_name = f"{package_name}.go2_velocity_policy"
    policy_spec = importlib.util.spec_from_file_location(
        policy_name, REPO_ROOT / "auto_adapter" / "skeletons" / "go2_velocity_policy.py"
    )
    if policy_spec is None or policy_spec.loader is None:
        raise RuntimeError("could not load AA1 Go2 policy")
    policy_module = importlib.util.module_from_spec(policy_spec)
    sys.modules[policy_name] = policy_module
    policy_spec.loader.exec_module(policy_module)
    Go2VelocityPolicySkeleton = policy_module.Go2VelocityPolicySkeleton

    # _infer only needs the retained weights and recurrent history.  Keeping
    # this comparison independent of MuJoCo makes the source runtime check
    # small while the real MuJoCo path is covered by run_real_control.py.
    policy = object.__new__(Go2VelocityPolicySkeleton)
    policy._np = np
    policy._history = np.zeros((5, 45), dtype=np.float32)
    with np.load(POLICY_RESOURCE, allow_pickle=False) as retained:
        policy._weights = {
            name: retained[name].astype(np.float32)
            for name in retained.files
        }
    return [policy._infer(observation) for observation in observations]


def compare(checkpoint: Path) -> dict[str, Any]:
    module = torch.jit.load(str(checkpoint), map_location="cpu")
    module.eval()
    observations = [
        np.zeros(45, dtype=np.float32),
        np.linspace(-0.4, 0.4, 45, dtype=np.float32),
    ]
    torch_actions: list[np.ndarray] = []
    with torch.no_grad():
        for observation in observations:
            output = module(torch.from_numpy(observation).reshape(1, 45))
            torch_actions.append(output.detach().cpu().numpy().reshape(12))
    aa1_actions = _aa1_inference(observations)
    if len(torch_actions) != len(aa1_actions):
        raise RuntimeError("TorchScript and AA1 returned different action counts")
    checks = []
    for index, (torch_action, aa1_action) in enumerate(
        zip(torch_actions, aa1_actions)
    ):
        checks.append(
            {
                "input": "zero" if index == 0 else "ramp",
                "torch_action": torch_action.tolist(),
                "aa1_action": aa1_action.tolist(),
                "max_abs_error": float(np.max(np.abs(torch_action - aa1_action))),
            }
        )
    return {
        "artifact_id": "go2-cts-150k",
        "source_revision": "30e74dc507bec7a642a8c98be26081f2c6f0822d",
        "checkpoint": str(checkpoint),
        "torch_version": torch.__version__,
        "model_generated": False,
        "torch_runtime_used": True,
        "loader": "torch.jit.load",
        "inference_checks": checks,
        "ok": all(item["max_abs_error"] <= 2.0e-6 for item in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(args.checkpoint.resolve())
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
