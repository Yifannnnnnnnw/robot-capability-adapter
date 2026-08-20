from __future__ import annotations

import json
import math
import tempfile
import textwrap
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import mujoco

from autoadapter2.harness import run_private_suite
from autoadapter2.harness import runner as harness_runner
from autoadapter2.harness.measurements import evaluate_guards
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolCall, ToolTurn


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = (
    ROOT
    / "libraries"
    / "robots"
    / "robotstudio_so101"
    / "1.0.0"
    / "assets"
)
PACKAGE_ROOT = ASSET_ROOT.parent


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class _ScriptedControllerClient:
    def __init__(self, turns: Sequence[ToolTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        self.calls.append(
            {
                "stage": stage,
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tools": [dict(item) for item in tools],
            }
        )
        return self.turns.pop(0)


class HarnessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.candidate = root / "candidate" / "driver.py"
        self.candidate.parent.mkdir()
        self.candidate.write_text(
            textwrap.dedent(
                """
                import mujoco

                class Driver:
                    def __init__(self, model, data):
                        self.model = model
                        self.data = data

                    def command_joint(self, request):
                        target = request["task_parameters"]["target"]
                        self.data.ctrl[0] = float(target)
                        for _ in range(80):
                            mujoco.mj_step(self.model, self.data)

                def build(*, model, data):
                    return Driver(model, data)
                """
            ),
            encoding="utf-8",
        )
        private = root / "package" / "tasks" / "private"
        _write(
            private / "instances.json",
            {
                "instances": [
                    {
                        "instance_id": "instance-1",
                        "task_id": "task-1",
                        "scene_entrypoint": "assets/scene.xml",
                        "public_arguments": {
                            "request": {
                                "task_id": "task-1",
                                "task_parameters": {"target": 0.2},
                            }
                        },
                        "reset": {"kind": "default"},
                        "guard_ids": ["control", "teleport", "canonical"],
                        "repetitions": 1,
                        "timeout_sim_s": 1.0,
                        "max_steps": 100,
                    }
                ]
            },
        )
        _write(
            private / "bindings.json",
            {
                "bindings": [
                    {
                        "binding_id": "binding-1",
                        "metric": "joint_error",
                        "unit": "rad",
                        "kind": "final_joint_position_error",
                        "parameters": {
                            "joint_name": "shoulder_pan",
                            "target_argument": "request.task_parameters.target",
                        },
                    }
                ]
            },
        )
        _write(
            private / "guards.json",
            {
                "guards": [
                    {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
                    {"guard_id": "teleport", "kind": "no_direct_state_write"},
                    {"guard_id": "canonical", "kind": "canonical_model_data"},
                ]
            },
        )
        self.package = RobotPackage(
            root=PACKAGE_ROOT,
            robot_configuration_id="example",
            package_version="1.0.0",
            snapshot_id="snapshot",
            morphology={"mjcf_entrypoint": "assets/scene.xml"},
            sources=(),
            tasks=(
                {
                    "task_id": "task-1",
                    "name": "Command one joint",
                    "description": "Move the public shoulder joint to the supplied target.",
                    "scoring": [
                        {
                            "clause_id": "joint-error",
                            "metric": "joint_error",
                            "unit": "rad",
                        }
                    ],
                    "invocation_schema": {
                        "request": {
                            "task_parameters": {
                                "required": ["target"],
                                "properties": {
                                    "target": {
                                        "type": "number",
                                        "unit": "rad",
                                        "frame": "shoulder_pan_joint",
                                        "description": "Public joint target.",
                                    }
                                },
                            }
                        }
                    },
                },
            ),
            mjcf_path=ASSET_ROOT / "scene.xml",
            skeleton_dir=root,
            reference_driver=root,
            private_dir=private,
        )
        self.design = {
            "capabilities": [
                {
                    "capability_id": "capability-1",
                    "method_name": "command_joint",
                    "description": "Command a public joint target through its actuator.",
                    "covered_task_ids": ["task-1"],
                    "validation_contract": [
                        {
                            "source_task_id": "task-1",
                            "source_clause_id": "joint-error",
                        }
                    ],
                }
            ]
        }
        self.suite = {
            "cases": [
                {
                    "case_id": "case-1",
                    "capability_id": "capability-1",
                    "method_name": "command_joint",
                    "task_id": "task-1",
                    "source_clause_id": "joint-error",
                    "instance_id": "instance-1",
                    "binding_id": "binding-1",
                    "guard_ids": ["control", "teleport", "canonical"],
                    "repetitions": 1,
                    "timeout_sim_s": 1.0,
                    "criterion": {
                        "comparator": "<=",
                        "threshold": 0.25,
                        "temporal": {"kind": "terminal_state"},
                        "aggregation": {"kind": "single_trial"},
                    },
                }
            ]
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _worker_result(
        self,
        samples: list[dict],
        *,
        step_count: int = 1,
        candidate_exception: dict | None = None,
        canonical_model_data: bool = True,
        worker_completed: bool = True,
    ) -> dict:
        return {
            "worker_completed": worker_completed,
            "method_invoked": True,
            "canonical_model_data": canonical_model_data,
            "candidate_exception": candidate_exception,
            "candidate_log": "",
            "physical_evidence": {
                "step_count": step_count,
                "ctrl_observed_before_step": True,
                "ctrl_changed_from_reset": True,
                "direct_state_write_detected": False,
                "samples": samples,
            },
            "video": {"requested": False, "complete": False, "frame_count": 0},
        }

    def _suite_with_criterion(self, criterion: dict) -> dict:
        suite = json.loads(json.dumps(self.suite))
        suite["cases"][0]["criterion"] = criterion
        return suite

    def _run_worker_result(self, worker: dict, *, criterion: dict) -> dict:
        with mock.patch.object(harness_runner, "_run_worker", return_value=worker):
            return run_private_suite(
                package=self.package,
                design=self.design,
                suite=self._suite_with_criterion(criterion),
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-focused",
                record_video=False,
            )

    def test_task_demo_react_invokes_the_admitted_driver_in_one_live_worker(self) -> None:
        public_arguments = {
            "request": {
                "task_id": "task-1",
                "task_parameters": {"target": 0.2},
            }
        }
        controller = _ScriptedControllerClient(
            (
                ToolTurn(
                    None,
                    (
                        ToolCall(
                            "invoke-1",
                            "command_joint",
                            public_arguments,
                            json.dumps(public_arguments),
                        ),
                    ),
                    "tool_calls",
                ),
                ToolTurn(
                    None,
                    (ToolCall("finish-1", "finish_task_demo", {}, "{}"),),
                    "tool_calls",
                ),
            )
        )
        suite = json.loads(json.dumps(self.suite))
        suite["artifact_type"] = "task_demo_suite"

        report = run_private_suite(
            package=self.package,
            design=self.design,
            suite=suite,
            driver_path=self.candidate,
            condition="from-scratch",
            output_dir=Path(self.temporary.name) / "react-task-demo",
            record_video=False,
            wall_timeout_s=10.0,
            controller_client=controller,
        )

        self.assertTrue(report["pipeline_completed"])
        self.assertTrue(report["physical_validation_executed"])
        self.assertTrue(report["validation_passed"])
        self.assertEqual(report["high_level_controller"]["kind"], "fixed_react")
        self.assertTrue(report["high_level_controller"]["completed"])
        self.assertEqual(report["high_level_controller"]["model_turn_count"], 2)
        self.assertEqual(report["high_level_controller"]["capability_call_count"], 1)
        trial = report["trials"][0]
        self.assertTrue(trial["controller_completed"])
        self.assertTrue(trial["method_invoked"])
        self.assertGreater(trial["physical_evidence"]["step_count"], 0)
        self.assertTrue(trial["trial_passed"])
        self.assertEqual(
            [call["stage"] for call in controller.calls],
            ["task_demo_controller", "task_demo_controller"],
        )

    def test_task_demo_cannot_pass_when_react_never_finishes(self) -> None:
        public_arguments = {
            "request": {
                "task_id": "task-1",
                "task_parameters": {"target": 0.2},
            }
        }
        turns = [
            ToolTurn(
                None,
                (
                    ToolCall(
                        "invoke-1",
                        "command_joint",
                        public_arguments,
                        json.dumps(public_arguments),
                    ),
                ),
                "tool_calls",
            )
        ]
        turns.extend(ToolTurn("done", (), "stop") for _ in range(7))
        controller = _ScriptedControllerClient(tuple(turns))
        suite = json.loads(json.dumps(self.suite))
        suite["artifact_type"] = "task_demo_suite"

        report = run_private_suite(
            package=self.package,
            design=self.design,
            suite=suite,
            driver_path=self.candidate,
            condition="from-scratch",
            output_dir=Path(self.temporary.name) / "unfinished-react-task-demo",
            record_video=False,
            wall_timeout_s=10.0,
            controller_client=controller,
        )

        trial = report["trials"][0]
        self.assertTrue(report["physical_validation_executed"])
        self.assertTrue(trial["temporal_passed"])
        self.assertFalse(trial["controller_completed"])
        self.assertFalse(trial["trial_passed"])
        self.assertFalse(report["validation_passed"])
        self.assertFalse(report["high_level_controller"]["completed"])

    def test_dwell_rejects_short_valid_state_duration(self) -> None:
        worker = self._worker_result(
            [
                {"time": 0.0, "joint_positions": {"shoulder_pan": 0.2}},
                {"time": 0.2, "joint_positions": {"shoulder_pan": 0.2}},
            ]
        )
        report = self._run_worker_result(
            worker,
            criterion={
                "comparator": "<=",
                "threshold": 0.05,
                "temporal": {"kind": "dwell", "duration_s": 0.8},
                "aggregation": {"kind": "single_trial"},
            },
        )

        trial = report["trials"][0]
        self.assertTrue(report["physical_validation_executed"])
        self.assertFalse(trial["temporal_passed"])
        self.assertAlmostEqual(trial["temporal_evidence"]["valid_duration_s"], 0.2)
        self.assertFalse(trial["trial_passed"])
        self.assertFalse(report["validation_passed"])

    def test_eventual_uses_a_passing_trusted_sample(self) -> None:
        worker = self._worker_result(
            [
                {"time": 0.0, "joint_positions": {"shoulder_pan": 0.0}},
                {"time": 0.2, "joint_positions": {"shoulder_pan": 0.2}},
                {"time": 0.4, "joint_positions": {"shoulder_pan": 0.0}},
            ]
        )
        report = self._run_worker_result(
            worker,
            criterion={
                "comparator": "<=",
                "threshold": 0.05,
                "temporal": {"kind": "eventual", "event": "target_reached"},
                "aggregation": {"kind": "single_trial"},
            },
        )

        trial = report["trials"][0]
        self.assertTrue(trial["temporal_passed"])
        self.assertEqual(trial["temporal_evidence"]["sample_index"], 1)
        self.assertTrue(trial["trial_passed"])

    def test_unknown_temporal_or_aggregation_fails_closed(self) -> None:
        worker = self._worker_result(
            [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.2}}]
        )
        unknown_temporal = self._run_worker_result(
            worker,
            criterion={
                "comparator": "<=",
                "threshold": 0.25,
                "temporal": {"kind": "invented_temporal"},
                "aggregation": {"kind": "single_trial"},
            },
        )
        self.assertFalse(unknown_temporal["validation_passed"])
        self.assertFalse(unknown_temporal["trials"][0]["trial_passed"])

        unknown_aggregation = self._run_worker_result(
            worker,
            criterion={
                "comparator": "<=",
                "threshold": 0.25,
                "temporal": {"kind": "terminal_state"},
                "aggregation": {"kind": "invented_aggregation"},
            },
        )
        self.assertFalse(unknown_aggregation["validation_passed"])
        self.assertFalse(unknown_aggregation["trials"][0]["trial_passed"])
        self.assertIn("unsupported aggregation", unknown_aggregation["trials"][0]["aggregation_error"])

    def test_continuous_rejects_short_evidence_window(self) -> None:
        worker = self._worker_result(
            [
                {"time": 0.0, "joint_positions": {"shoulder_pan": 0.0}},
                {"time": 0.2, "joint_positions": {"shoulder_pan": 0.2}},
            ]
        )
        report = self._run_worker_result(
            worker,
            criterion={
                "comparator": ">=",
                "threshold": 0.1,
                "temporal": {"kind": "continuous", "duration_s": 0.8},
                "aggregation": {"kind": "single_trial"},
            },
        )

        self.assertFalse(report["trials"][0]["temporal_passed"])
        self.assertFalse(report["validation_passed"])

    def test_per_trial_mean_aggregates_trusted_trial_values(self) -> None:
        suite = self._suite_with_criterion(
            {
                "comparator": "<=",
                "threshold": 0.1,
                "temporal": {"kind": "terminal_state"},
                "aggregation": {"kind": "per_trial_mean"},
            }
        )
        suite["cases"][0]["repetitions"] = 2
        workers = [
            self._worker_result(
                [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.2}}]
            ),
            self._worker_result(
                [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.0}}]
            ),
        ]
        with mock.patch.object(harness_runner, "_run_worker", side_effect=workers):
            report = run_private_suite(
                package=self.package,
                design=self.design,
                suite=suite,
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-mean",
                record_video=False,
            )

        self.assertTrue(report["validation_passed"])
        self.assertAlmostEqual(report["trials"][0]["aggregation_value"], 0.1)
        self.assertTrue(all(trial["trial_passed"] for trial in report["trials"]))

    def test_repetition_variants_drive_each_worker_and_measurement(self) -> None:
        instances_path = self.package.private_dir / "instances.json"
        instances = json.loads(instances_path.read_text(encoding="utf-8"))
        instance = instances["instances"][0]
        instance["repetitions"] = 2
        instance["repetition_variants"] = [
            {
                "public_arguments": {
                    "request": {
                        "task_id": "task-1",
                        "task_parameters": {"target": 0.1},
                    }
                }
            },
            {
                "public_arguments": {
                    "request": {
                        "task_id": "task-1",
                        "task_parameters": {"target": 0.2},
                    }
                }
            },
        ]
        _write(instances_path, instances)
        suite = self._suite_with_criterion(
            {
                "comparator": "<=",
                "threshold": 0.0,
                "temporal": {"kind": "terminal_state"},
                "aggregation": {"kind": "per_trial_mean"},
            }
        )
        suite["cases"][0]["repetitions"] = 2
        workers = [
            self._worker_result(
                [{"time": 0.0, "joint_positions": {"shoulder_pan": target}}]
            )
            for target in (0.1, 0.2)
        ]

        with mock.patch.object(
            harness_runner, "_run_worker", side_effect=workers
        ) as run_worker:
            report = run_private_suite(
                package=self.package,
                design=self.design,
                suite=suite,
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-variants",
                record_video=False,
            )

        targets = [
            call.args[0]["public_arguments"]["request"]["task_parameters"]["target"]
            for call in run_worker.call_args_list
        ]
        self.assertEqual(targets, [0.1, 0.2])
        self.assertEqual(
            [
                trial["public_arguments"]["request"]["task_parameters"]["target"]
                for trial in report["trials"]
            ],
            [0.1, 0.2],
        )
        self.assertTrue(report["validation_passed"])

    def test_framework_reset_rotates_a_private_static_terrain(self) -> None:
        model = mujoco.MjModel.from_xml_string(
            """<mujoco><worldbody>
  <body name="terrain"><geom name="marker" type="sphere" pos="1 0 0" size="0.01"/></body>
</worldbody></mujoco>"""
        )
        data = mujoco.MjData(model)
        half_angle = math.pi / 4.0

        apply_framework_reset(
            mujoco,
            model,
            data,
            {
                "kind": "default",
                "body_quaternions": {
                    "terrain": [math.cos(half_angle), 0.0, 0.0, math.sin(half_angle)]
                },
            },
        )

        marker_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "marker")
        self.assertAlmostEqual(float(data.geom_xpos[marker_id, 0]), 0.0, places=7)
        self.assertAlmostEqual(float(data.geom_xpos[marker_id, 1]), 1.0, places=7)

    def test_terminal_body_stability_rejects_a_fallen_sliding_pose(self) -> None:
        guard = {
            "guard_id": "stable",
            "kind": "terminal_body_stability",
            "body_name": "base_link",
            "minimum_height_m": 0.18,
            "minimum_upright_cosine": 0.7,
        }
        upright = self._worker_result(
            [
                {
                    "time": 1.0,
                    "body_positions": {"base_link": [1.0, 0.0, 0.27]},
                    "body_quaternions": {"base_link": [1.0, 0.0, 0.0, 0.0]},
                }
            ]
        )
        fallen = self._worker_result(
            [
                {
                    "time": 1.0,
                    "body_positions": {"base_link": [1.0, 0.0, 0.12]},
                    "body_quaternions": {
                        "base_link": [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]
                    },
                }
            ]
        )

        self.assertEqual(evaluate_guards([guard], worker_result=upright), {"stable": True})
        self.assertEqual(evaluate_guards([guard], worker_result=fallen), {"stable": False})

    def test_named_contact_pair_guard_rejects_disjoint_contacts(self) -> None:
        guard = {
            "guard_id": "robot-task-contact",
            "kind": "named_geom_contact_pair_required",
            "robot_geom_name": "link7_contact_geom",
            "task_geom_names": ["button_geom", "button_cap_geom"],
            "minimum_steps": 2,
        }
        disjoint = self._worker_result([])
        disjoint["physical_evidence"]["contact_pair_step_counts"] = [
            {"geom1": "floor", "geom2": "link7_contact_geom", "step_count": 5},
            {"geom1": "button_geom", "geom2": "button_housing", "step_count": 5},
        ]
        exact_pair = self._worker_result([])
        exact_pair["physical_evidence"]["contact_pair_step_counts"] = [
            {"geom1": "button_cap_geom", "geom2": "link7_contact_geom", "step_count": 2},
        ]

        self.assertEqual(
            evaluate_guards([guard], worker_result=disjoint),
            {"robot-task-contact": False},
        )
        self.assertEqual(
            evaluate_guards([guard], worker_result=exact_pair),
            {"robot-task-contact": True},
        )

        finger_bank_guard = {
            "guard_id": "selected-finger-contact",
            "kind": "named_geom_contact_pair_required",
            "robot_geom_names": ["left/left_g0", "left/left_g1", "left/left_g2"],
            "task_geom_names": ["workpiece_geom"],
            "minimum_steps": 1,
        }
        wrong_arm = self._worker_result([])
        wrong_arm["physical_evidence"]["contact_pair_step_counts"] = [
            {"geom1": "right/left_g0", "geom2": "workpiece_geom", "step_count": 4},
        ]
        selected_arm = self._worker_result([])
        selected_arm["physical_evidence"]["contact_pair_step_counts"] = [
            {"geom1": "workpiece_geom", "geom2": "left/left_g2", "step_count": 1},
        ]
        self.assertEqual(
            evaluate_guards([finger_bank_guard], worker_result=wrong_arm),
            {"selected-finger-contact": False},
        )
        self.assertEqual(
            evaluate_guards([finger_bank_guard], worker_result=selected_arm),
            {"selected-finger-contact": True},
        )

    def test_named_joint_neutral_guard_uses_per_joint_peak_deviation(self) -> None:
        guard = {
            "guard_id": "other-arm-neutral",
            "kind": "named_joints_remain_near_reset",
            "joint_tolerances": {
                "left/waist": 0.01,
                "left/left_finger": 0.0005,
            },
        }
        neutral = self._worker_result([])
        neutral["physical_evidence"]["joint_max_abs_deviation_from_reset"] = {
            "left/waist": 0.009,
            "left/left_finger": 0.0004,
        }
        moved = self._worker_result([])
        moved["physical_evidence"]["joint_max_abs_deviation_from_reset"] = {
            "left/waist": 0.011,
            "left/left_finger": 0.0,
        }
        missing = self._worker_result([])
        missing["physical_evidence"]["joint_max_abs_deviation_from_reset"] = {
            "left/waist": 0.0,
        }

        self.assertEqual(
            evaluate_guards([guard], worker_result=neutral),
            {"other-arm-neutral": True},
        )
        self.assertEqual(
            evaluate_guards([guard], worker_result=moved),
            {"other-arm-neutral": False},
        )
        self.assertEqual(
            evaluate_guards([guard], worker_result=missing),
            {"other-arm-neutral": False},
        )

    def test_physical_execution_requires_clean_canonical_stepped_trials(self) -> None:
        base_samples = [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.2}}]
        variants = (
            {"step_count": 0},
            {"candidate_exception": {"type": "RuntimeError"}},
            {"canonical_model_data": False},
            {"worker_completed": False},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                worker = self._worker_result(base_samples, **variant)
                report = self._run_worker_result(
                    worker,
                    criterion={
                        "comparator": "<=",
                        "threshold": 0.25,
                        "temporal": {"kind": "terminal_state"},
                        "aggregation": {"kind": "single_trial"},
                    },
                )
                self.assertFalse(report["physical_validation_executed"])
                self.assertFalse(report["trials"][0]["trial_passed"])

    def test_parent_issues_separate_pipeline_and_physical_verdicts(self) -> None:
        report = run_private_suite(
            package=self.package,
            design=self.design,
            suite=self.suite,
            driver_path=self.candidate,
            condition="from-scratch",
            output_dir=Path(self.temporary.name) / "evidence",
            record_video=False,
        )

        self.assertTrue(report["pipeline_completed"])
        self.assertTrue(report["physical_validation_executed"])
        self.assertTrue(report["validation_passed"])
        self.assertEqual(report["passed_task_count"], 1)
        self.assertEqual(report["passed_source_clause_count"], 1)
        self.assertEqual(report["passed_private_case_count"], 1)
        self.assertTrue(report["trials"][0]["guard_outcomes"]["control"])
        self.assertEqual(
            report["trials"][0]["public_arguments"],
            {
                "request": {
                    "task_id": "task-1",
                    "task_parameters": {"target": 0.2},
                }
            },
        )

    def test_partial_clause_selection_does_not_count_a_whole_task_as_passed(self) -> None:
        design = json.loads(json.dumps(self.design))
        design["capabilities"][0]["validation_contract"].append(
            {
                "source_task_id": "task-1",
                "source_clause_id": "stability",
            }
        )
        worker = self._worker_result(
            [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.2}}]
        )
        with mock.patch.object(harness_runner, "_run_worker", return_value=worker):
            report = run_private_suite(
                package=self.package,
                design=design,
                suite=self.suite,
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-partial-task",
                record_video=False,
            )

        self.assertTrue(report["validation_passed"])
        self.assertEqual(report["selected_task_count"], 1)
        self.assertEqual(report["passed_selected_task_count"], 1)
        self.assertEqual(report["fully_evaluated_task_count"], 0)
        self.assertEqual(report["passed_task_count"], 0)

    def test_candidate_executes_outside_generation_workspace(self) -> None:
        source = self.candidate.read_text(encoding="utf-8")
        source = source.replace(
            "self.data.ctrl[0] = float(target)",
            "print(__file__)\n        self.data.ctrl[0] = float(target)",
        )
        self.candidate.write_text(source, encoding="utf-8")

        report = run_private_suite(
            package=self.package,
            design=self.design,
            suite=self.suite,
            driver_path=self.candidate,
            condition="from-scratch",
            output_dir=Path(self.temporary.name) / "evidence-isolation",
            record_video=False,
        )

        self.assertIn("driver.py", report["trials"][0]["candidate_log"])
        self.assertNotIn(str(self.candidate), report["trials"][0]["candidate_log"])

    def test_required_video_failure_invalidates_trial(self) -> None:
        fake_worker = {
            "worker_completed": True,
            "method_invoked": True,
            "canonical_model_data": True,
            "candidate_exception": None,
            "candidate_log": "",
            "physical_evidence": {
                "step_count": 1,
                "ctrl_observed_before_step": True,
                "ctrl_changed_from_reset": True,
                "direct_state_write_detected": False,
                "samples": [
                    {
                        "time": 0.0,
                        "joint_positions": {"shoulder_pan": 0.0},
                    },
                    {
                        "time": 0.1,
                        "joint_positions": {"shoulder_pan": 0.2},
                    },
                ],
            },
            "video": {"requested": True, "complete": False, "frame_count": 0},
        }
        with mock.patch.object(harness_runner, "_run_worker", return_value=fake_worker):
            report = run_private_suite(
                package=self.package,
                design=self.design,
                suite=self.suite,
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-video",
                record_video=True,
            )

        self.assertFalse(report["validation_passed"])
        self.assertFalse(report["video_complete"])
        self.assertFalse(report["trials"][0]["trial_passed"])

    def test_candidate_exception_marks_decodable_video_incomplete(self) -> None:
        fake_worker = self._worker_result(
            [{"time": 0.0, "joint_positions": {"shoulder_pan": 0.0}}],
            step_count=0,
            candidate_exception={"type": "AttributeError", "message": "bad request"},
        )
        fake_worker["video"] = {
            "requested": True,
            "complete": True,
            "decodable": True,
            "frame_count": 1,
            "duration": 0.05,
        }
        with mock.patch.object(harness_runner, "_run_worker", return_value=fake_worker):
            report = run_private_suite(
                package=self.package,
                design=self.design,
                suite=self.suite,
                driver_path=self.candidate,
                condition="from-scratch",
                output_dir=Path(self.temporary.name) / "evidence-truncated-video",
                record_video=True,
            )

        video = report["trials"][0]["video"]
        self.assertTrue(video["decodable"])
        self.assertFalse(video["complete"])
        self.assertIn("ended before clean", video["error"])
        self.assertFalse(report["video_complete"])
        self.assertFalse(report["validation_passed"])


if __name__ == "__main__":
    unittest.main()
