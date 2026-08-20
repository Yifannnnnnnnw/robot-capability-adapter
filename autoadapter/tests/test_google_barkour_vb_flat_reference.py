from __future__ import annotations

import ast
import importlib.util
import json
import math
from pathlib import Path

import mujoco
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "google_barkour_vb" / "1.0.0"
REFERENCE_ROOT = PACKAGE_ROOT / "reference"
DRIVER_PATH = REFERENCE_ROOT / "flat_joystick.py"
ACTOR_PATH = REFERENCE_ROOT / "barkour_joystick_actor.npz"
METADATA_PATH = REFERENCE_ROOT / "barkour_joystick_actor.json"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"
TRUSTED_SOURCE_PATH = (
    ROOT / "src" / "autoadapter2" / "trusted_skeletons" / "quadruped_position_policy.py"
)
TRUSTED_ACTOR_PATH = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "data"
    / "barkour_joystick_actor.npz"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("barkour_flat_joystick", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_session() -> tuple[mujoco.MjModel, mujoco.MjData]:
    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    assert home_id >= 0
    mujoco.mj_resetDataKeyframe(model, data, home_id)
    mujoco.mj_forward(model, data)
    return model, data


def _assigned_simulator_attributes(source: str) -> set[str]:
    assigned: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            value = target
            while isinstance(value, ast.Subscript):
                value = value.value
            if isinstance(value, ast.Attribute) and value.attr in {"qpos", "qvel"}:
                assigned.add(value.attr)
    return assigned


def test_flat_reference_artifact_and_source_boundary() -> None:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    assert metadata["checkpoint_step"] == "000100270080"
    assert metadata["artifact_id"] == "barkour-joystick-v005-step-000100270080"
    assert metadata["playground_version"] == "0.0.5"
    assert metadata["playground_commit"] == (
        "81dfe512c9f2f03107fda1e31de585d04bb30bc4"
    )
    assert metadata["observation_size"] == 465
    assert metadata["action_size"] == 12
    assert metadata["control_period_s"] == 0.02
    assert metadata["training_menagerie_commit"] == (
        "14ceccf557cc47240202f2354d684eca58ff8de4"
    )
    assert metadata["canonical_package_menagerie_commit"] == (
        "da76818e269b82289eba39808e2fb91d679d6994"
    )
    assert metadata["source_license"]["spdx"] == "Apache-2.0"
    assert metadata["training"]["checkpoint_environment_steps"] == 100270080
    assert metadata["training"]["num_envs"] == 8192
    assert metadata["training"]["actor_hidden_layer_sizes"] == [128] * 4
    assert metadata["training"]["critic_hidden_layer_sizes"] == [256] * 5
    assert metadata["verification"]["retained_numpy_max_abs_error"] < (
        metadata["verification"]["tolerance"]
    )

    with np.load(ACTOR_PATH, allow_pickle=False) as retained:
        assert retained["normalizer_mean"].shape == (465,)
        assert retained["normalizer_std"].shape == (465,)
        assert retained["actor_0_kernel"].shape == (465, 128)
        assert retained["actor_4_kernel"].shape == (128, 24)
        assert all(np.all(np.isfinite(retained[name])) for name in retained.files)
    assert ACTOR_PATH.read_bytes() == TRUSTED_ACTOR_PATH.read_bytes()

    wrapper_source = DRIVER_PATH.read_text(encoding="utf-8")
    trusted_source = TRUSTED_SOURCE_PATH.read_text(encoding="utf-8")
    assert _assigned_simulator_attributes(trusted_source) == set()
    for source in (wrapper_source, trusted_source):
        assert "task_id" not in source
        assert "tasks/private" not in source
        assert "instances.json" not in source
        assert "bindings.json" not in source
        assert "guards.json" not in source
        assert "MjModel.from_xml" not in source
        assert "mj_reset" not in source
    assert "self.data.ctrl" in trusted_source
    assert "mj_step" in trusted_source

    assert not (REFERENCE_ROOT / "driver.py").exists()
    runnable_index = json.loads(
        (ROOT / "libraries" / "robots" / "index.json").read_text(encoding="utf-8")
    )
    assert "google_barkour_vb" not in runnable_index["robots"]


def test_flat_reference_passes_canonical_stand_and_eight_direction_gate() -> None:
    module = _load_module()

    model, data = _canonical_session()
    initial_ctrl = data.ctrl.copy()
    stand = module.build(model=model, data=data).hold(duration=5.0)
    assert stand["fall_reason"] is None
    assert stand["physics_steps"] == 5000
    assert stand["steps_per_policy_action"] == 20
    stand_velocity = np.asarray(stand["mean_local_velocity_after_1s_m_s"])
    assert np.linalg.norm(stand_velocity[:2]) <= 0.1
    assert float(np.asarray(stand["final_upvector"])[2]) > 0.96
    assert not np.allclose(data.ctrl, initial_ctrl)
    assert np.all(np.isfinite(data.qpos))
    assert np.all(np.isfinite(data.qvel))

    observed_speeds: list[float] = []
    observed_heading_errors: list[float] = []
    for direction_deg in range(0, 360, 45):
        direction = math.radians(direction_deg)
        command = np.asarray(
            [0.4 * math.cos(direction), 0.4 * math.sin(direction), 0.0]
        )
        model, data = _canonical_session()
        result = module.build(model=model, data=data).command_planar_velocity(
            float(command[0]),
            float(command[1]),
            0.0,
            duration=5.0,
        )
        assert result["fall_reason"] is None, direction_deg
        assert result["physics_steps"] == 5000
        local_velocity = np.asarray(result["mean_local_velocity_after_1s_m_s"])
        speed = float(np.linalg.norm(local_velocity[:2]))
        cosine = float(
            np.dot(command[:2], local_velocity[:2])
            / (np.linalg.norm(command[:2]) * speed)
        )
        heading_error = math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))
        observed_speeds.append(speed)
        observed_heading_errors.append(heading_error)
        assert speed >= 0.4, direction_deg
        assert heading_error <= 10.0, direction_deg
        assert np.all(np.isfinite(data.qpos)), direction_deg
        assert np.all(np.isfinite(data.qvel)), direction_deg

    assert min(observed_speeds) >= 0.5
    assert max(observed_heading_errors) <= 7.6
