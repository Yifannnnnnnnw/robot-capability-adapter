from __future__ import annotations

import importlib.util
import json
import tempfile
import textwrap
from pathlib import Path

from autoadapter2.driver_synthesis.generation import build_public_generation_inputs
from autoadapter2.driver_synthesis.probe import prepare_public_probe_workspace
from autoadapter2.driver_synthesis.source_check import audit_driver_source
from autoadapter2.harness.worker import execute_case
from autoadapter2.libraries import RobotPackage
from autoadapter2.trusted_skeletons import (
    BarkourPositionPolicySkeleton,
    QuadrupedPositionPolicySpec,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "google_barkour_vb" / "1.0.0"
SKELETON_PATH = PACKAGE_ROOT / "skeleton" / "quadruped_position_policy.py"
SCENE_PATH = PACKAGE_ROOT / "assets" / "scene.xml"


def _public_package() -> RobotPackage:
    morphology = json.loads(
        (PACKAGE_ROOT / "morphology.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (PACKAGE_ROOT / "tasks" / "sources.json").read_text(encoding="utf-8")
    )
    catalog = json.loads(
        (PACKAGE_ROOT / "tasks" / "catalog.json").read_text(encoding="utf-8")
    )
    return RobotPackage(
        root=PACKAGE_ROOT,
        robot_configuration_id=morphology["robot_configuration_id"],
        package_version=morphology["package_version"],
        snapshot_id=catalog["snapshot_id"],
        morphology=morphology,
        sources=tuple(sources["sources"]),
        tasks=tuple(catalog["tasks"]),
        mjcf_path=SCENE_PATH,
        skeleton_dir=PACKAGE_ROOT / "skeleton",
        reference_driver=PACKAGE_ROOT / "reference" / "driver.py",
        private_dir=PACKAGE_ROOT / "tasks" / "private",
    )


def _load_inventory():
    spec = importlib.util.spec_from_file_location(
        "google_barkour_vb_position_policy_inventory", SKELETON_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_position_policy_inventory_and_probe_staging_are_condition_scoped() -> None:
    inventory = _load_inventory()
    assert inventory.BarkourPositionPolicySkeleton is BarkourPositionPolicySkeleton
    assert inventory.QuadrupedPositionPolicySpec is QuadrupedPositionPolicySpec
    assert inventory.POLICY_ARTIFACT_ID == (
        "barkour-joystick-v005-step-000100270080"
    )

    package = _public_package()
    design = {
        "capabilities": [
            {"capability_id": "planar_motion", "method_name": "drive_local"}
        ]
    }
    inputs = build_public_generation_inputs(
        package,
        design,
        condition="skeleton-assisted",
    )
    sources = {
        item["path"]: item["source"]
        for item in inputs["condition_eligible_artifacts"]["source_files"]
    }
    assert "quadruped_position_policy.py" in sources
    runtime_path = (
        "runtime/autoadapter2/trusted_skeletons/quadruped_position_policy.py"
    )
    assert runtime_path in sources
    assert "class BarkourPositionPolicySkeleton" in sources[runtime_path]
    assert "BARKOUR_POLICY_JOINT_LOW" in sources[runtime_path]
    assert "BARKOUR_POLICY_JOINT_HIGH" in sources[runtime_path]
    assert "task_id" not in sources[runtime_path]

    with tempfile.TemporaryDirectory(prefix="barkour-position-probe-") as temporary:
        staged = prepare_public_probe_workspace(
            package,
            Path(temporary) / "workspace",
            condition="skeleton-assisted",
            framework_source_root=ROOT / "src",
        )
        assert staged.python_root is not None
        staged_actor = (
            staged.python_root
            / "autoadapter2"
            / "trusted_skeletons"
            / "data"
            / "barkour_joystick_actor.npz"
        )
        assert staged_actor.is_file()
        assert staged_actor.read_bytes() == (
            ROOT
            / "src"
            / "autoadapter2"
            / "trusted_skeletons"
            / "data"
            / "barkour_joystick_actor.npz"
        ).read_bytes()
        assert not (staged.root / "reference").exists()
        assert not (staged.root / "tasks" / "private").exists()


def test_position_policy_executes_inside_candidate_worker_boundary() -> None:
    candidate_source = textwrap.dedent(
        """
        from autoadapter2.trusted_skeletons.quadruped_position_policy import (
            BARKOUR_POLICY_JOINT_HIGH,
            BARKOUR_POLICY_JOINT_LOW,
            BarkourPositionPolicySkeleton,
            QuadrupedPositionPolicySpec,
        )

        JOINTS = (
            "abduction_front_left", "hip_front_left", "knee_front_left",
            "abduction_hind_left", "hip_hind_left", "knee_hind_left",
            "abduction_front_right", "hip_front_right", "knee_front_right",
            "abduction_hind_right", "hip_hind_right", "knee_hind_right",
        )

        class Driver:
            def __init__(self, model, data):
                spec = QuadrupedPositionPolicySpec(
                    base_body_name="torso",
                    imu_site_name="imu_frame",
                    gyro_sensor_name="gyro",
                    home_keyframe_name="home",
                    joint_names=JOINTS,
                    actuator_names=JOINTS,
                    policy_joint_low=BARKOUR_POLICY_JOINT_LOW,
                    policy_joint_high=BARKOUR_POLICY_JOINT_HIGH,
                )
                self.policy = BarkourPositionPolicySkeleton.from_session(
                    model=model, data=data, spec=spec
                )

            def drive_local(self, request):
                parameters = request["task_parameters"]
                return self.policy.command_planar_velocity(
                    parameters["vx_m_s"],
                    parameters["vy_m_s"],
                    parameters["yaw_rate_rad_s"],
                    duration=parameters["duration_s"],
                )

        def build(model, data):
            return Driver(model, data)
        """
    )
    audit = audit_driver_source(
        candidate_source,
        condition="skeleton-assisted",
        capability_methods=("drive_local",),
    )
    assert audit.imports_trusted_skeleton

    with tempfile.TemporaryDirectory(prefix="barkour-position-worker-") as temporary:
        driver_path = Path(temporary) / "driver.py"
        driver_path.write_text(candidate_source, encoding="utf-8")
        result = execute_case(
            {
                "driver_path": str(driver_path),
                "scene_path": str(SCENE_PATH),
                "capability_methods": ["drive_local"],
                "method_name": "drive_local",
                "public_arguments": {
                    "request": {
                        "task_id": "public_probe_only",
                        "task_parameters": {
                            "vx_m_s": 0.4,
                            "vy_m_s": 0.0,
                            "yaw_rate_rad_s": 0.0,
                            "duration_s": 0.2,
                        },
                    }
                },
                "reset": {"kind": "keyframe", "name": "home"},
                "max_steps": 300,
                "max_sim_time_s": 1.0,
                "sample_hz": 20.0,
                "render": {"enabled": False},
            }
        )

    assert result["worker_completed"] is True
    assert result["method_invoked"] is True
    assert result["candidate_exception"] is None
    assert result["canonical_model_data"] is True
    assert result["candidate_return_type"] == "dict"
    assert result["physical_evidence"]["step_count"] == 200
