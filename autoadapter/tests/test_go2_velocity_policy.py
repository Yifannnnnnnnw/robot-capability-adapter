from __future__ import annotations

import importlib.util
import json
import math
from collections import Counter
from pathlib import Path

import mujoco
import numpy as np

from autoadapter2.harness.measurements import measure
from autoadapter2.harness.session import TrackedMuJoCoSession, apply_framework_reset
from autoadapter2.trusted_skeletons.go2_velocity_policy import (
    Go2VelocityPolicySkeleton,
    Go2VelocityPolicySpec,
    POLICY_ARTIFACT_ID,
    POLICY_SOURCE_REVISION,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    ROOT / "libraries" / "robots" / "unitree-go2-stock-12dof" / "1.0.0"
)
PRIVATE_ROOT = PACKAGE_ROOT / "tasks" / "private"
POLICY_SOURCE = ROOT / "src" / "autoadapter2" / "trusted_skeletons" / "go2_velocity_policy.py"
POLICY_RESOURCE = (
    ROOT
    / "src"
    / "autoadapter2"
    / "trusted_skeletons"
    / "data"
    / "go2_velocity_policy.npz"
)


def _policy_spec() -> Go2VelocityPolicySpec:
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


def _reference_module():
    path = PACKAGE_ROOT / "reference" / "driver.py"
    spec = importlib.util.spec_from_file_location("go2_policy_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy_and_session():
    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets" / "lee_flat.xml")
    )
    data = mujoco.MjData(model)
    key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "task_start")
    assert key >= 0
    mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    return (
        Go2VelocityPolicySkeleton(model=model, data=data, spec=_policy_spec()),
        model,
        data,
    )


def test_go2_policy_is_local_licensed_numpy_and_matches_export_vectors() -> None:
    metadata = json.loads(
        (PACKAGE_ROOT / "reference" / "go2_velocity_policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["artifact_id"] == POLICY_ARTIFACT_ID == "go2-cts-150k"
    assert metadata["source_revision"] == POLICY_SOURCE_REVISION
    assert metadata["source_path"] == "deploy/pre_train/go2/go2_cts_150k.pt"
    assert metadata["torch_runtime_required"] is False
    assert POLICY_RESOURCE.is_file()
    assert POLICY_RESOURCE.stat().st_size > 1_000_000
    source = POLICY_SOURCE.read_text(encoding="utf-8")
    assert "import torch" not in source
    license_text = (
        PACKAGE_ROOT / "reference" / "go2_velocity_policy_LICENSE.txt"
    ).read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "BSD-3-Clause" in license_text

    model = mujoco.MjModel.from_xml_path(
        str(PACKAGE_ROOT / "assets" / "lee_flat.xml")
    )
    policy = Go2VelocityPolicySkeleton(
        model=model,
        data=mujoco.MjData(model),
        spec=_policy_spec(),
    )
    policy._resolve()
    zero_action = policy._infer(np.zeros(45, dtype=np.float32))
    ramp_action = policy._infer(np.linspace(-0.4, 0.4, 45, dtype=np.float32))
    np.testing.assert_allclose(
        zero_action,
        (
            -0.603626132,
            0.591176212,
            -0.389862686,
            0.688495457,
            -0.199814022,
            -0.587446034,
            -0.082616895,
            -0.352849066,
            -0.466575384,
            -0.093064792,
            -0.074538678,
            0.010981951,
        ),
        rtol=0.0,
        atol=2.0e-6,
    )
    np.testing.assert_allclose(
        ramp_action,
        (
            -0.416912884,
            0.287532449,
            -1.090850353,
            -0.432435423,
            0.370840341,
            -0.073182568,
            -0.638892651,
            -0.481605887,
            1.041430831,
            0.065453477,
            -0.318036497,
            0.160008147,
        ),
        rtol=0.0,
        atol=2.0e-6,
    )


def test_go2_base_twist_labels_world_and_body_yaw_frames() -> None:
    policy, model, data = _policy_and_session()
    policy._resolve()
    root_qpos = policy._root_qpos_address
    root_qvel = policy._root_qvel_address
    half_sqrt = math.sqrt(0.5)
    data.qpos[root_qpos + 3 : root_qpos + 7] = (
        half_sqrt,
        0.0,
        0.0,
        half_sqrt,
    )
    data.qvel[root_qvel : root_qvel + 3] = (1.0, 0.0, 0.0)
    data.qvel[root_qvel + 3 : root_qvel + 6] = (0.0, 0.0, 0.0)
    mujoco.mj_forward(model, data)

    twist = policy.get_base_twist()

    np.testing.assert_allclose(
        twist["linear_world_m_s"][:2], (1.0, 0.0), atol=1.0e-7
    )
    np.testing.assert_allclose(
        twist["linear_body_yaw_m_s"], (0.0, -1.0), atol=1.0e-7
    )

    data.qvel[root_qvel : root_qvel + 3] = (0.0, 0.0, 0.0)
    data.qvel[root_qvel + 3 : root_qvel + 6] = (0.0, 0.0, 0.3)
    mujoco.mj_forward(model, data)
    twist = policy.get_base_twist()
    np.testing.assert_allclose(twist["linear_world_m_s"], 0.0, atol=1.0e-12)
    np.testing.assert_allclose(
        twist["linear_body_yaw_m_s"], 0.0, atol=1.0e-12
    )
    assert math.isclose(twist["yaw_rate_rad_s"], 0.3, abs_tol=1.0e-7)


def test_go2_velocity_feedback_uses_fresh_measurements_in_bounded_chunks() -> None:
    class RecordingPolicy(Go2VelocityPolicySkeleton):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.measurements = iter(
                ((0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.15, 0.0, 0.0))
            )
            self.commands: list[tuple[float, float, float, float]] = []

        def get_base_twist(self):
            measured = next(self.measurements)
            return {
                "linear_world_m_s": np.zeros(3, dtype=float),
                "linear_body_yaw_m_s": np.asarray(measured[:2], dtype=float),
                "angular_world_rad_s": np.zeros(3, dtype=float),
                "yaw_rate_rad_s": measured[2],
            }

        def command_planar_velocity(self, vx, vy, yaw_rate, duration=1.0):
            self.commands.append(
                (float(vx), float(vy), float(yaw_rate), float(duration))
            )
            return {
                "physics_steps": int(
                    round(duration / float(self.model.opt.timestep))
                )
            }

    base, model, data = _policy_and_session()
    policy = RecordingPolicy(model=model, data=data, spec=base.spec)

    result = policy.track_planar_velocity(0.2, 0.0, 0.0, duration=0.08)

    assert result["feedback_cycles"] == 2
    assert [item[3] for item in policy.commands] == [0.04, 0.04]
    assert policy.commands[0][0] > policy.commands[1][0] > 0.2
    assert all(abs(item[1]) <= 1.0e-12 for item in policy.commands)
    assert all(abs(item[2]) <= 1.0e-12 for item in policy.commands)

    policy.measurements = iter(((-100.0, -100.0, -100.0), (0.0, 0.0, 0.0)))
    policy.commands.clear()
    policy.track_planar_velocity(0.0, 0.0, 0.0, duration=0.04)
    correction = np.asarray(policy.commands[0][:3])
    assert math.isclose(
        float(np.linalg.norm(correction[:2])),
        policy.spec.maximum_planar_correction_m_s,
        abs_tol=1.0e-12,
    )
    assert math.isclose(
        correction[2],
        policy.spec.maximum_yaw_correction_rad_s,
        abs_tol=1.0e-12,
    )

    policy.measurements = iter(((-100.0, -100.0, -100.0), (0.0, 0.0, 0.0)))
    policy.commands.clear()
    policy.track_planar_velocity(1.2, 0.0, 3.0, duration=0.04)
    saturated = np.asarray(policy.commands[0][:3])
    assert (
        float(np.linalg.norm(saturated[:2]))
        < policy.spec.maximum_planar_speed_m_s
    )
    assert abs(float(saturated[2])) < policy.spec.maximum_yaw_rate_rad_s


def test_go2_stepping_stone_reference_executes_all_18_source_repetitions() -> None:
    catalog = json.loads(
        (PACKAGE_ROOT / "tasks" / "catalog.json").read_text(encoding="utf-8")
    )
    task = next(task for task in catalog["tasks"] if task["task_id"] == "GO2-T14")
    instances = json.loads(
        (PRIVATE_ROOT / "instances.json").read_text(encoding="utf-8")
    )["instances"]
    instance = next(item for item in instances if item["task_id"] == "GO2-T14")
    bindings = json.loads(
        (PRIVATE_ROOT / "bindings.json").read_text(encoding="utf-8")
    )["bindings"]
    binding_by_id = {item["binding_id"]: item for item in bindings}
    clause_id = task["scoring"][0]["clause_id"]
    binding = binding_by_id[instance["clause_bindings"][clause_id]]
    variants = instance["repetition_variants"]
    assert len(variants) == 18

    scene = PACKAGE_ROOT / instance["scene_entrypoint"]
    model = mujoco.MjModel.from_xml_path(str(scene))
    module = _reference_module()
    evaluated_directions: list[float] = []
    for variant in variants:
        data = mujoco.MjData(model)
        apply_framework_reset(mujoco, model, data, variant["reset"])
        request = variant["public_arguments"]["request"]
        tracker = TrackedMuJoCoSession(
            mujoco=mujoco,
            model=model,
            data=data,
            max_steps=6500,
            max_sim_time_s=13.0,
            sample_hz=20.0,
        )
        with tracker:
            module.build(model=model, data=data).traverse_stepping_stones(request)
            tracker.finish()
        evidence = tracker.evidence()
        value = measure(
            binding,
            evidence=evidence,
            public_arguments=variant["public_arguments"],
        )
        assert math.isfinite(value)
        assert evidence["ctrl_observed_before_step"]
        assert evidence["ctrl_changed_from_reset"]
        assert not evidence["direct_state_write_detected"]
        assert evidence["contact_monitoring_complete"]
        assert evidence["minimum_contact_distance_m"] is not None
        evaluated_directions.append(request["task_parameters"]["direction_rad"])

    direction_counts = Counter(round(value, 12) for value in evaluated_directions)
    assert len(direction_counts) == 6
    assert set(direction_counts.values()) == {3}
