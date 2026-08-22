from __future__ import annotations

import copy
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from autoadapter2.harness import run_private_suite
from autoadapter2.harness import runner as harness_runner
from autoadapter2.harness.b1_contracts import (
    SUPPORTED_CONTRACT_IDS,
    evaluate_b1_contract,
)
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
        *(f"A{index}" for index in range(1, 7)),
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
    assert report["passed_capability_count"] == 1


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


def test_b1_rejects_late_target_entry_and_over_budget_offset_legs() -> None:
    late_samples = []
    for time, position in (
        (0.0, [0.0, 0.0, 0.0]),
        (0.5, [0.0, 0.0, 0.0]),
        (1.0, [0.1, 0.2, 0.3]),
        (1.25, [0.1, 0.2, 0.3]),
        (1.5, [0.1, 0.2, 0.3]),
    ):
        late_samples.append(
            {
                "time": time,
                "site_positions": {"gripperframe": position},
                "body_positions": {},
                "body_quaternions": {},
                "joint_positions": {},
                "joint_velocities": {},
                "contacts": [],
            }
        )
    assert evaluate_b1_contract(
        {"contract_id": "A1", "site_name": "gripperframe"},
        evidence={"samples": late_samples},
        request={"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 0.75},
    ) == 0.0

    offset_samples = []
    for time, position in (
        (0.0, [0.0, 0.0, 0.0]),
        (0.3, [0.01, 0.0, 0.0]),
        (0.6, [0.02, 0.0, 0.0]),
        (0.85, [0.02, 0.0, 0.0]),
        (1.1, [0.01, 0.0, 0.0]),
        (1.5, [0.0, 0.0, 0.0]),
        (2.0, [0.0, 0.0, 0.0]),
    ):
        offset_samples.append(
            {
                "time": time,
                "site_positions": {"gripperframe": position},
                "body_positions": {},
                "body_quaternions": {},
                "joint_positions": {},
                "joint_velocities": {},
                "contacts": [],
            }
        )
    parameters = {"contract_id": "A5", "site_name": "gripperframe"}
    request = {
        "offset_robot_base_m": [0.02, 0.0, 0.0],
        "max_duration_per_leg_s": 1.0,
    }
    assert evaluate_b1_contract(
        parameters, evidence={"samples": offset_samples}, request=request
    ) == 1.0
    request["max_duration_per_leg_s"] = 0.7
    assert evaluate_b1_contract(
        parameters, evidence={"samples": offset_samples}, request=request
    ) == 0.0


def test_so101_wrist_roll_requires_a_timed_target_hold_without_other_motion() -> None:
    def sample(time: float, wrist_roll: float) -> dict:
        return {
            "time": time,
            "site_positions": {"gripperframe": [0.2, 0.0, 0.3]},
            "body_positions": {},
            "body_quaternions": {},
            "joint_positions": {
                "shoulder_pan": 0.0,
                "shoulder_lift": 0.0,
                "elbow_flex": 0.0,
                "wrist_flex": 0.0,
                "wrist_roll": wrist_roll,
                "gripper": 0.4,
            },
            "joint_velocities": {},
            "contacts": [],
        }

    parameters = {
        "contract_id": "A6",
        "joint_name": "wrist_roll",
        "side_effect_guard_profile": "so101",
    }
    request = {"target_roll_rad": 1.2, "max_duration_s": 0.3}
    samples = [
        sample(0.0, -0.6),
        sample(0.2, 1.2),
        sample(0.35, 1.2),
        sample(0.45, 1.2),
    ]
    assert evaluate_b1_contract(
        parameters, evidence={"samples": samples}, request=request
    ) == 1.0

    late_samples = [
        sample(0.0, -0.6),
        sample(0.35, 1.2),
        sample(0.50, 1.2),
        sample(0.60, 1.2),
    ]
    assert evaluate_b1_contract(
        parameters, evidence={"samples": late_samples}, request=request
    ) == 0.0

    drifting_samples = copy.deepcopy(samples)
    drifting_samples[-1]["joint_positions"]["shoulder_pan"] = 0.04
    assert evaluate_b1_contract(
        parameters, evidence={"samples": drifting_samples}, request=request
    ) == 0.0


def test_b1_fixed_duration_cannot_be_rescued_after_the_request_horizon() -> None:
    def sample(time: float, x: float) -> dict:
        return {
            "time": time,
            "site_positions": {},
            "body_positions": {"base_link": [x, 0.0, 0.3]},
            "body_quaternions": {"base_link": [1.0, 0.0, 0.0, 0.0]},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": [],
        }

    parameters = {"contract_id": "G1", "body_name": "base_link"}
    request = {
        "linear_velocity_body_m_s": [0.2, 0.0],
        "yaw_rate_rad_s": 0.0,
        "duration_s": 2.0,
    }
    on_time = [sample(0.0, 0.0), sample(1.0, 0.2), sample(2.0, 0.4)]
    assert evaluate_b1_contract(
        parameters, evidence={"samples": on_time}, request=request
    ) == 1.0

    late_only = [
        sample(0.0, 0.0),
        sample(1.0, 0.0),
        sample(2.0, 0.0),
        sample(3.0, 0.2),
        sample(4.0, 0.4),
    ]
    assert evaluate_b1_contract(
        parameters, evidence={"samples": late_only}, request=request
    ) == 0.0


def test_b1_bimanual_budget_uses_the_final_synchronized_dwell() -> None:
    left_target = [0.1, 0.0, 0.0]
    right_target = [-0.1, 0.0, 0.0]
    far = [0.0, 0.0, 0.0]

    def sample(time: float, left: list[float], right: list[float]) -> dict:
        return {
            "time": time,
            "site_positions": {
                "left/gripper": left,
                "right/gripper": right,
            },
            "body_positions": {},
            "body_quaternions": {},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": [],
        }

    samples = [
        sample(0.00, far, far),
        sample(1.00, left_target, far),
        sample(1.05, far, right_target),
        sample(1.10, far, far),
        sample(5.00, left_target, right_target),
        sample(5.25, left_target, right_target),
        sample(5.50, left_target, right_target),
    ]
    assert evaluate_b1_contract(
        {
            "contract_id": "AL4",
            "arm_site_names": {
                "left": "left/gripper",
                "right": "right/gripper",
            },
        },
        evidence={"samples": samples},
        request={
            "left_target_position_world_m": left_target,
            "right_target_position_world_m": right_target,
            "max_duration_s": 2.0,
        },
    ) == 0.0


def test_b1_contact_rejects_unrelated_contact_before_precontact() -> None:
    def sample(
        time: float,
        y: float,
        contacts: list[dict] | None = None,
    ) -> dict:
        return {
            "time": time,
            "site_positions": {"ee": [0.0, y, 0.0]},
            "body_positions": {},
            "body_quaternions": {},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": contacts or [],
        }

    target_contact = [{"geom1": "tip", "geom2": "target", "distance": 0.0}]
    samples = [
        sample(
            0.00,
            0.070,
            [{"geom1": "tip", "geom2": "housing", "distance": 0.0}],
        ),
        sample(0.05, 0.070),
        sample(0.10, 0.070),
        sample(0.15, 0.070),
        sample(0.20, 0.071),
        sample(0.25, 0.072),
        sample(0.30, 0.073),
        sample(0.35, 0.074, target_contact),
        sample(0.40, 0.074, target_contact),
        sample(0.45, 0.074, target_contact),
    ]
    parameters = {
        "contract_id": "A4",
        "site_name": "ee",
        "tool_geom_names": ["tip"],
        "target_geom_names": ["target"],
        "precontact_gate": "held_window_then_ray",
    }
    request = {
        "precontact_position_m": [0.0, 0.070, 0.0],
        "approach_direction_unit": [0.0, 1.0, 0.0],
        "max_travel_m": 0.01,
        "max_approach_speed_m_s": 0.02,
        "max_duration_s": 1.0,
    }
    assert evaluate_b1_contract(
        parameters, evidence={"samples": samples}, request=request
    ) == 0.0

    unsampled = copy.deepcopy(samples)
    unsampled[0]["contacts"] = []
    assert evaluate_b1_contract(
        parameters,
        evidence={
            "samples": unsampled,
            "contact_pair_step_counts": [
                {"geom1": "tip", "geom2": "housing", "step_count": 1}
            ],
        },
        request=request,
    ) == 0.0

    assert evaluate_b1_contract(
        parameters,
        evidence={
            "samples": unsampled,
            "contact_pair_first_times_s": [
                {"geom1": "tip", "geom2": "target", "first_time_s": 0.02}
            ],
        },
        request=request,
    ) == 0.0


def test_b1_held_precontact_allows_a_bounded_transition_into_the_ray() -> None:
    target_contact = [{"geom1": "tip", "geom2": "target", "distance": 0.0}]

    def sample(time: float, position: list[float], contact: bool = False) -> dict:
        return {
            "time": time,
            "site_positions": {"ee": position},
            "body_positions": {},
            "body_quaternions": {},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": target_contact if contact else [],
        }

    samples = [
        sample(0.0, [0.0, 0.012, 0.0]),
        sample(0.1, [0.0, 0.012, 0.0]),
        sample(0.2, [0.0, 0.009, 0.0]),
        sample(0.4, [0.010, 0.009, 0.0], True),
        sample(0.45, [0.010, 0.009, 0.0], True),
        sample(0.50, [0.010, 0.009, 0.0], True),
    ]
    assert evaluate_b1_contract(
        {
            "contract_id": "A4",
            "site_name": "ee",
            "tool_geom_names": ["tip"],
            "target_geom_names": ["target"],
            "precontact_gate": "held_window_then_ray",
        },
        evidence={"samples": samples},
        request={
            "precontact_position_m": [0.0, 0.0, 0.0],
            "approach_direction_unit": [1.0, 0.0, 0.0],
            "max_travel_m": 0.02,
            "max_approach_speed_m_s": 0.05,
            "max_duration_s": 1.0,
        },
    ) == 1.0


def test_b1_leap_reach_requires_the_fixed_fiftieth_control_step() -> None:
    sites = {
        "if_tip": [0.1, 0.0, 0.0],
        "mf_tip": [0.0, 0.1, 0.0],
        "rf_tip": [0.0, 0.0, 0.1],
        "th_tip": [0.1, 0.1, 0.0],
    }
    sample = {
        "time": 0.0,
        "site_positions": sites,
        "body_positions": {"palm": [0.0, 0.0, 0.0]},
        "body_quaternions": {"palm": [1.0, 0.0, 0.0, 0.0]},
        "joint_positions": {},
        "joint_velocities": {},
        "contacts": [],
    }
    parameters = {
        "contract_id": "L3",
        "palm_body_name": "palm",
        "fingertip_site_names": {
            "index": "if_tip",
            "middle": "mf_tip",
            "ring": "rf_tip",
            "thumb": "th_tip",
        },
        "physics_steps_per_control_step": 10,
    }
    request = {
        "target_fingertip_positions_palm_m": [
            coordinate for position in sites.values() for coordinate in position
        ],
        "max_control_steps": 50,
    }
    samples = [sample, {**copy.deepcopy(sample), "time": 1.0}]
    assert evaluate_b1_contract(
        parameters,
        evidence={"samples": samples, "step_count": 1},
        request=request,
    ) == 0.0
    assert evaluate_b1_contract(
        parameters,
        evidence={"samples": samples, "step_count": 500},
        request=request,
    ) == 1.0


def test_b1_leap_rejects_unsampled_wrong_target_contact_and_finger_drift() -> None:
    def l4_sample(time: float, position: list[float], contact: bool) -> dict:
        return {
            "time": time,
            "site_positions": {"if_tip": position},
            "body_positions": {"palm": [0.0, 0.0, 0.0]},
            "body_quaternions": {"palm": [1.0, 0.0, 0.0, 0.0]},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": (
                [{"geom1": "if_geom", "geom2": "index_target", "distance": 0.0}]
                if contact
                else []
            ),
        }

    l4_parameters = {
        "contract_id": "L4",
        "palm_body_name": "palm",
        "fingertip_site_names": {"index": "if_tip"},
        "finger_joint_names": {"index": ["j1", "j2", "j3", "j4"]},
        "fingertip_geom_names": {"index": ["if_geom"]},
        "target_geom_names": {
            "index": ["index_target"],
            "middle": ["middle_target"],
        },
    }
    l4_request = {
        "required_fingers": ["index"],
        "contact_target_positions_palm_m": [[0.02, 0.0, 0.0]],
        "approach_directions_palm_unit": [[1.0, 0.0, 0.0]],
        "max_travel_m": 0.04,
        "max_approach_speed_m_s": 0.05,
        "max_duration_s": 2.0,
    }
    l4_samples = [
        l4_sample(0.0, [0.0, 0.0, 0.0], False),
        l4_sample(0.1, [0.005, 0.0, 0.0], True),
        l4_sample(0.4, [0.02, 0.0, 0.0], True),
        l4_sample(0.45, [0.02, 0.0, 0.0], True),
        l4_sample(0.50, [0.02, 0.0, 0.0], True),
    ]
    common_evidence = {
        "samples": l4_samples,
        "joint_max_abs_deviation_from_reset": {},
        "site_max_displacement_from_reset": {},
    }
    assert evaluate_b1_contract(
        l4_parameters, evidence=common_evidence, request=l4_request
    ) == 1.0
    assert evaluate_b1_contract(
        l4_parameters,
        evidence={
            **common_evidence,
            "contact_pair_step_counts": [
                {"geom1": "if_geom", "geom2": "middle_target", "step_count": 1}
            ],
        },
        request=l4_request,
    ) == 0.0

    finger_sites = {"index": "if_tip", "middle": "mf_tip"}
    finger_joints = {
        "index": ["i1", "i2", "i3", "i4"],
        "middle": ["m1", "m2", "m3", "m4"],
    }

    def l6_sample(time: float, index_x: float) -> dict:
        return {
            "time": time,
            "site_positions": {
                "if_tip": [index_x, 0.0, 0.0],
                "mf_tip": [0.0, 0.1, 0.0],
            },
            "body_positions": {"palm": [0.0, 0.0, 0.0]},
            "body_quaternions": {"palm": [1.0, 0.0, 0.0, 0.0]},
            "joint_positions": {name: 0.0 for names in finger_joints.values() for name in names},
            "joint_velocities": {},
            "contacts": [],
        }

    l6_parameters = {
        "contract_id": "L6",
        "palm_body_name": "palm",
        "fingertip_site_names": finger_sites,
        "finger_joint_names": finger_joints,
    }
    l6_request = {
        "finger_names": ["index"],
        "offsets_palm_m": [[0.02, 0.0, 0.0]],
        "max_duration_per_leg_s": 0.8,
    }
    l6_samples = [
        l6_sample(0.0, 0.0),
        l6_sample(0.25, 0.02),
        l6_sample(0.50, 0.02),
        l6_sample(0.75, 0.0),
        l6_sample(1.00, 0.0),
    ]
    assert evaluate_b1_contract(
        l6_parameters,
        evidence={
            "samples": l6_samples,
            "joint_max_abs_deviation_from_reset": {"m1": 0.04},
            "site_max_displacement_from_reset": {},
        },
        request=l6_request,
    ) == 1.0
    assert evaluate_b1_contract(
        l6_parameters,
        evidence={
            "samples": l6_samples,
            "joint_max_abs_deviation_from_reset": {"m1": 0.06},
            "site_max_displacement_from_reset": {},
        },
        request=l6_request,
    ) == 0.0


def test_b1_leap_contact_path_stays_on_ray_until_held_contact() -> None:
    def sample(time: float, position: list[float], contact: bool) -> dict:
        return {
            "time": time,
            "site_positions": {"if_tip": position},
            "body_positions": {"palm": [0.0, 0.0, 0.0]},
            "body_quaternions": {"palm": [1.0, 0.0, 0.0, 0.0]},
            "joint_positions": {},
            "joint_velocities": {},
            "contacts": (
                [{"geom1": "if_geom", "geom2": "index_target", "distance": 0.0}]
                if contact
                else []
            ),
        }

    samples = [
        sample(0.0, [0.0, 0.0, 0.0], False),
        sample(0.1, [0.005, 0.0, 0.0], True),
        sample(0.5, [0.005, 0.02, 0.0], False),
        sample(1.0, [0.02, 0.0, 0.0], True),
        sample(1.05, [0.02, 0.0, 0.0], True),
        sample(1.10, [0.02, 0.0, 0.0], True),
    ]
    assert evaluate_b1_contract(
        {
            "contract_id": "L4",
            "palm_body_name": "palm",
            "fingertip_site_names": {"index": "if_tip"},
            "finger_joint_names": {"index": ["j1", "j2", "j3", "j4"]},
            "fingertip_geom_names": {"index": ["if_geom"]},
            "target_geom_names": {"index": ["index_target"]},
        },
        evidence={
            "samples": samples,
            "contact_pair_step_counts": [
                {"geom1": "if_geom", "geom2": "index_target", "step_count": 4}
            ],
            "joint_max_abs_deviation_from_reset": {},
            "site_max_displacement_from_reset": {},
        },
        request={
            "required_fingers": ["index"],
            "contact_target_positions_palm_m": [[0.02, 0.0, 0.0]],
            "approach_directions_palm_unit": [[1.0, 0.0, 0.0]],
            "max_travel_m": 0.04,
            "max_approach_speed_m_s": 0.05,
            "max_duration_s": 2.0,
        },
    ) == 0.0


def test_b1_so101_side_effect_guard_rejects_gripper_drift() -> None:
    samples = _samples([0.1, 0.2, 0.3])
    for index, sample in enumerate(samples):
        sample["joint_positions"] = {"gripper": 0.0 if index == 0 else 0.2}
    request = {"target_position_m": [0.1, 0.2, 0.3], "max_duration_s": 2.0}
    unguarded = {"contract_id": "A1", "site_name": "gripperframe"}
    guarded = {**unguarded, "side_effect_guard_profile": "so101"}
    assert evaluate_b1_contract(
        unguarded, evidence={"samples": samples}, request=request
    ) == 1.0
    assert evaluate_b1_contract(
        guarded, evidence={"samples": samples}, request=request
    ) == 0.0


def test_b1_whole_suite_uses_two_of_three_cases_per_capability() -> None:
    suite = copy.deepcopy(_suite())
    template = suite["cases"][0]
    suite["whole_suite_aggregation"] = {
        "kind": "all_capabilities_two_of_three_cases"
    }
    suite["cases"] = []
    for variant in ("H1", "H2", "H3"):
        case = copy.deepcopy(template)
        case["case_id"] = f"A1-{variant}"
        case["case_variant"] = variant
        suite["cases"].append(case)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidate = root / "driver.py"
        candidate.write_text(
            "import mujoco\n"
            "class Driver:\n"
            "    def __init__(self, model, data): self.model, self.data = model, data\n"
            "    def move_end_effector_to_position(self, request):\n"
            "        self.data.ctrl[0] = float(self.data.ctrl[0])\n"
            "        mujoco.mj_step(self.model, self.data)\n"
            "def build(*, model, data): return Driver(model, data)\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            harness_runner,
            "_run_worker",
            side_effect=[
                _worker([0.1, 0.2, 0.3]),
                _worker([0.1, 0.2, 0.3]),
                _worker([1.1, 1.2, 1.3]),
            ],
        ):
            report = run_private_suite(
                package=_package(root / "absent-private"),
                design=_design(),
                suite=suite,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=root / "evidence",
                record_video=False,
            )

    assert report["validation_passed"] is True
    assert report["passed_private_case_count"] == 2
    assert report["capability_results"] == [
        {
            "capability_id": "A1",
            "passed_case_count": 2,
            "task_metric_case_count": 2,
            "case_count": 3,
            "passed": True,
            "task_metric_passed": True,
        }
    ]


def test_b1_two_of_three_rejects_a_missing_designed_capability() -> None:
    suite = copy.deepcopy(_suite())
    template = suite["cases"][0]
    suite["whole_suite_aggregation"] = {
        "kind": "all_capabilities_two_of_three_cases"
    }
    suite["cases"] = []
    for variant in ("H1", "H2", "H3"):
        case = copy.deepcopy(template)
        case["case_id"] = f"A1-{variant}"
        case["case_variant"] = variant
        suite["cases"].append(case)
    design = copy.deepcopy(_design())
    design["capabilities"].append(
        {
            "capability_id": "A2",
            "method_name": "trace_cartesian_path",
            "validation_contract": [],
        }
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidate = root / "driver.py"
        candidate.write_text(
            "import mujoco\n"
            "class Driver:\n"
            "    def __init__(self, model, data): self.model, self.data = model, data\n"
            "    def move_end_effector_to_position(self, request):\n"
            "        self.data.ctrl[0] = float(self.data.ctrl[0])\n"
            "        mujoco.mj_step(self.model, self.data)\n"
            "    def trace_cartesian_path(self, request):\n"
            "        self.data.ctrl[0] = float(self.data.ctrl[0])\n"
            "        mujoco.mj_step(self.model, self.data)\n"
            "def build(*, model, data): return Driver(model, data)\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            harness_runner,
            "_run_worker",
            return_value=_worker([0.1, 0.2, 0.3]),
        ):
            report = run_private_suite(
                package=_package(root / "absent-private"),
                design=design,
                suite=suite,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=root / "evidence",
                record_video=False,
            )

    assert report["passed_private_case_count"] == 3
    assert report["capability_results"][0]["passed"] is True
    assert report["validation_passed"] is False
