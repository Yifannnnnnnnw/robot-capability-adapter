from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from autoadapter2.harness import run_private_suite
from autoadapter2.harness import runner as harness_runner
from autoadapter2.harness.b1_contracts import SUPPORTED_CONTRACT_IDS
from autoadapter2.harness.measurements import measure
from autoadapter2.libraries import RobotPackage


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.0"


def _samples(target: list[float]) -> list[dict]:
    return [
        {
            "time": time,
            "site_positions": {"gripperframe": list(target)},
            "body_positions": {},
            "body_quaternions": {},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": [],
        }
        for time in (0.0, 0.25, 0.5)
    ]


def _worker(target: list[float]) -> dict:
    return {
        "worker_completed": True,
        "method_invoked": True,
        "canonical_model_data": True,
        "candidate_exception": None,
        "candidate_return_type": "dict",
        "candidate_return_value": {"success": True},
        "candidate_log": "",
        "physical_evidence": {
            "step_count": 10,
            "ctrl_observed_before_step": True,
            "ctrl_changed_from_reset": True,
            "direct_state_write_detected": False,
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": None,
            "contact_pair_min_distances": [],
            "contact_pair_step_counts": [],
            "samples": _samples(target),
        },
        "video": {"requested": False, "complete": False, "frame_count": 0},
    }


def _package(private_dir: Path) -> RobotPackage:
    return RobotPackage(
        root=PACKAGE_ROOT,
        robot_configuration_id="robotstudio_so101",
        package_version="1.0.0",
        snapshot_id="snapshot",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(),
        mjcf_path=PACKAGE_ROOT / "assets" / "scene.xml",
        skeleton_dir=PACKAGE_ROOT / "skeleton",
        reference_driver=PACKAGE_ROOT / "reference",
        private_dir=private_dir,
    )


def _design() -> dict:
    return {
        "capabilities": [
            {
                "capability_id": "A1",
                "method_name": "move_end_effector_to_position",
                "validation_contract": [],
            }
        ]
    }


def _suite() -> dict:
    return {
        "artifact_type": "b1_fixed_validation_suite",
        "cases": [
            {
                "case_id": "A1-hidden-1",
                "capability_id": "A1",
                "method_name": "move_end_effector_to_position",
                "scene_entrypoint": "assets/scene.xml",
                "request": {
                    "target_position_m": [0.1, 0.2, 0.3],
                    "max_duration_s": 2.0,
                },
                "reset": {"kind": "default"},
                "max_steps": 100,
                "sample_hz": 37.0,
                "timeout_sim_s": 2.0,
                "binding": {
                    "kind": "b1_contract",
                    "parameters": {"contract_id": "A1", "site_name": "gripperframe"},
                },
                "guards": [
                    {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
                    {"guard_id": "no-write", "kind": "no_direct_state_write"},
                    {"guard_id": "canonical", "kind": "canonical_model_data"},
                ],
                "criterion": {
                    "comparator": ">=",
                    "threshold": 1,
                    "temporal": {"kind": "fixed_trials"},
                    "aggregation": {"kind": "single_trial"},
                },
            }
        ],
    }


def test_b1_contract_register_covers_the_whole_fixed_table() -> None:
    expected = {
        *(f"A{index}" for index in range(1, 6)),
        *(f"G{index}" for index in range(1, 6)),
        *(f"L{index}" for index in range(1, 7)),
        *(f"ST{index}" for index in range(1, 9)),
        *(f"AL{index}" for index in range(1, 7)),
    }
    assert SUPPORTED_CONTRACT_IDS == expected


def test_b1_measurement_uses_trusted_samples_not_candidate_self_report() -> None:
    binding = {
        "kind": "b1_contract",
        "parameters": {"contract_id": "A1", "site_name": "gripperframe"},
    }
    public_arguments = {
        "request": {
            "target_position_m": [0.1, 0.2, 0.3],
            "max_duration_s": 2.0,
            "success": True,
        }
    }
    assert measure(
        binding,
        evidence=_worker([0.1, 0.2, 0.3])["physical_evidence"],
        public_arguments=public_arguments,
    ) == 1.0
    assert measure(
        binding,
        evidence=_worker([1.1, 1.2, 1.3])["physical_evidence"],
        public_arguments=public_arguments,
    ) == 0.0


def test_inline_b1_case_runs_without_package_private_ids() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidate = root / "driver.py"
        candidate.write_text(
            textwrap.dedent(
                """
                import mujoco

                class Driver:
                    def __init__(self, model, data):
                        self.model = model
                        self.data = data

                    def move_end_effector_to_position(self, request):
                        self.data.ctrl[0] = 0.0
                        mujoco.mj_step(self.model, self.data)
                        return {"success": True}

                def build(*, model, data):
                    return Driver(model, data)
                """
            ),
            encoding="utf-8",
        )
        nonexistent_private = root / "does-not-exist"
        with mock.patch.object(
            harness_runner,
            "_run_worker",
            return_value=_worker([0.1, 0.2, 0.3]),
        ) as run_worker:
            report = run_private_suite(
                package=_package(nonexistent_private),
                design=_design(),
                suite=_suite(),
                driver_path=candidate,
                condition="from-scratch",
                output_dir=root / "evidence",
                record_video=False,
            )

    payload = run_worker.call_args.args[0]
    assert payload["public_arguments"] == {"request": _suite()["cases"][0]["request"]}
    assert payload["sample_hz"] == 37.0
    assert report["pipeline_completed"] is True
    assert report["validation_passed"] is True
    assert report["passed_private_case_count"] == 1
    assert report["private_case_count"] == 1


def test_inline_b1_case_records_a_metric_failure_as_a_completed_trial() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidate = root / "driver.py"
        candidate.write_text(
            "import mujoco\n"
            "class Driver:\n"
            "    def __init__(self, model, data): self.model, self.data = model, data\n"
            "    def move_end_effector_to_position(self, request):\n"
            "        self.data.ctrl[0] = 0.0\n"
            "        mujoco.mj_step(self.model, self.data)\n"
            "        return {'success': True}\n"
            "def build(*, model, data): return Driver(model, data)\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            harness_runner,
            "_run_worker",
            return_value=_worker([1.1, 1.2, 1.3]),
        ):
            report = run_private_suite(
                package=_package(root / "absent-private"),
                design=_design(),
                suite=_suite(),
                driver_path=candidate,
                condition="from-scratch",
                output_dir=root / "evidence",
                record_video=False,
            )

    trial = report["trials"][0]
    assert report["pipeline_completed"] is True
    assert report["physical_validation_executed"] is True
    assert report["validation_passed"] is False
    assert trial["measurement_value"] == 0.0
    assert trial["measurement_error"] is None
    assert trial["trial_passed"] is False


def test_b1_body_symbol_resolves_descendant_and_unnamed_geoms() -> None:
    binding = {
        "kind": "b1_contract",
        "parameters": {
            "contract_id": "ST7",
            "tool_body_names": ["gripper"],
            "tool_geom_names": [],
        },
    }
    scene = PACKAGE_ROOT / "assets" / "scene.xml"
    try:
        resolved = harness_runner._resolve_b1_body_geom_symbols(binding, scene)
    except harness_runner.HarnessError as exc:
        pytest.skip(f"SO-101 fixture has no body named gripper: {exc}")
    names = resolved["parameters"]["tool_geom_names"]
    assert names
    assert all(isinstance(name, str) and name for name in names)
