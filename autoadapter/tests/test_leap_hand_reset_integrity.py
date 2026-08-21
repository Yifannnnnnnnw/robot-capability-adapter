from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "leap_hand" / "1.0.0"
MAXIMUM_PENETRATION_M = 0.005
PASSIVE_STEPS = 100


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _deepest_contact_m(data: mujoco.MjData) -> float:
    if data.ncon == 0:
        return 0.0
    return min(float(contact.dist) for contact in data.contact[: data.ncon])


def _passive_depths(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    *,
    steps: int,
) -> tuple[np.ndarray, list[float]]:
    reset_controls = np.asarray(data.ctrl, dtype=float).copy()
    depths = []
    for _ in range(steps):
        np.testing.assert_array_equal(data.ctrl, reset_controls)
        mujoco.mj_step(model, data)
        np.testing.assert_array_equal(data.ctrl, reset_controls)
        depths.append(_deepest_contact_m(data))
    return reset_controls, depths


def test_legacy_home_reset_reproduces_passive_settling_penetration() -> None:
    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets" / "leap_reference_cube_scene.xml")
    )
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, {"kind": "keyframe", "name": "home"})
    assert _deepest_contact_m(data) >= -MAXIMUM_PENETRATION_M
    _, depths = _passive_depths(model, data, steps=PASSIVE_STEPS)
    assert min(depths) < -MAXIMUM_PENETRATION_M


def test_all_48_private_executions_reset_and_settle_within_five_mm() -> None:
    package = load_robot_package(PACKAGE_ROOT)
    instances = _read(package.private_dir / "instances.json")["instances"]
    execution_count = 0

    for instance in instances:
        variants = instance.get("repetition_variants")
        for clause_id in instance["clause_bindings"]:
            for repetition in range(instance["repetitions"]):
                reset = instance["reset"]
                if variants is not None:
                    reset = variants[repetition]["reset"]
                label = f"{instance['task_id']}:{clause_id}:r{repetition + 1}"

                model = mujoco.MjModel.from_xml_path(
                    str(package.root / instance["scene_entrypoint"])
                )
                data = mujoco.MjData(model)
                apply_framework_reset(mujoco, model, data, reset)
                assert model.nu == 16, label
                assert np.all(
                    model.actuator_biastype == int(mujoco.mjtBias.mjBIAS_AFFINE)
                ), label
                initial_depth = _deepest_contact_m(data)
                assert initial_depth >= -MAXIMUM_PENETRATION_M, (
                    f"{label} initial contact depth {initial_depth:.9f} m"
                )

                reset_controls, passive_depths = _passive_depths(
                    model, data, steps=PASSIVE_STEPS
                )
                assert len(passive_depths) == PASSIVE_STEPS
                assert min(passive_depths) >= -MAXIMUM_PENETRATION_M, (
                    f"{label} passive contact depth {min(passive_depths):.9f} m"
                )
                assert np.isfinite(data.qpos).all(), label
                assert np.isfinite(data.qvel).all(), label
                assert np.isfinite(reset_controls).all(), label
                execution_count += 1

    assert execution_count == 48
