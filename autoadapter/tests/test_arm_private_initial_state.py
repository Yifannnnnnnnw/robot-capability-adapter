from __future__ import annotations

import json
from pathlib import Path

import mujoco
import pytest

from autoadapter2.harness.measurements import compare, measure
from autoadapter2.harness.session import apply_framework_reset


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("robot_id", ("franka_panda", "ufactory_xarm7"))
def test_handle_pull_private_case_requires_physical_change(robot_id: str) -> None:
    package_root = ROOT / "libraries" / "robots" / robot_id / "1.0.0"
    instance = next(
        item
        for item in _read(package_root / "tasks/private/instances.json")["instances"]
        if item["task_id"] == "mw_handle_pull"
    )
    binding_id = instance["clause_bindings"]["pulled_handle_distance"]
    binding = next(
        item
        for item in _read(package_root / "tasks/private/bindings.json")["bindings"]
        if item["binding_id"] == binding_id
    )
    task = next(
        item
        for item in _read(package_root / "tasks/catalog.json")["tasks"]
        if item["task_id"] == "mw_handle_pull"
    )
    criterion = next(
        item
        for item in task["scoring"]
        if item["clause_id"] == "pulled_handle_distance"
    )

    model = mujoco.MjModel.from_xml_path(
        str(package_root / instance["scene_entrypoint"])
    )
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance["reset"])
    site_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_SITE, binding["parameters"]["site_name"]
    )
    evidence = {
        "samples": [
            {
                "time": float(data.time),
                "site_positions": {
                    binding["parameters"]["site_name"]: data.site_xpos[
                        site_id
                    ].tolist()
                },
            }
        ]
    }
    value = measure(
        binding,
        evidence=evidence,
        public_arguments=instance["public_arguments"],
    )

    assert instance["reset"]["joint_positions"]["vertical_handle_slide"] == -0.055
    assert value > criterion["threshold"]
    assert not compare(
        value,
        comparator=criterion["comparator"],
        threshold=criterion["threshold"],
    )


def test_reference_evidence_supersedes_the_false_success_runs() -> None:
    evidence = (ROOT / "evidence" / "README.md").read_text(encoding="utf-8")

    assert "franka-reference-positive-control-20260820T034710Z" in evidence
    assert "xarm7-reference-positive-control-20260820T034432Z" in evidence
    assert "franka-reference-positive-control-20260820T013634Z" not in evidence
    assert "xarm7-reference-positive-control-20260820T031649Z" not in evidence
    assert evidence.count("superseded") >= 2
