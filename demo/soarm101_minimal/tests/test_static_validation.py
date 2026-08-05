from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from soarm_demo.static_validation import (
    compute_stage1_sha256,
    validate_generated_package,
)


class StaticValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage1 = _stage1_artifact()
        _write_valid_package(self.root, self.stage1)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_package_passes_without_executing_generated_functions(self) -> None:
        # Every generated function raises if called. Static validation must still
        # pass because the generated package is parsed, never imported/executed.
        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(report.failures, ())
        self.assertEqual(report.stage1_sha256, compute_stage1_sha256(self.stage1))
        self.assertIsNone(report.to_failure_feedback(repair_round=0))

    def test_g3_result_contract_requires_truthful_phase_observability(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        g3 = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G3"
        )
        g3["result_contract"]["required"] = ["status"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_generated_package(self.root, self.stage1)

        failures = [
            failure
            for failure in report.failures
            if failure.code == "INVALID_RESULT_CONTRACT"
            and failure.capability_id == g3["capability_id"]
        ]
        self.assertEqual(len(failures), 1)
        self.assertIn("phase_reached", failures[0].message)

    def test_sequence_result_contract_requires_failure_localization_fields(self) -> None:
        g3_stage1 = self.stage1["layers"]["G3"]["capabilities"][0]
        g3_stage1["validation_effect"] = "object_move_sequence"
        g3_stage1["implementation_family"] = "ordered_pick_place_sequence"
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage1_sha256"] = compute_stage1_sha256(self.stage1)
        g3 = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G3"
        )
        g3["validation_binding"] = {
            "effect": "object_move_sequence",
            "moves_argument": "source",
            "source_field": "source_position_m",
            "target_field": "target_position_m",
            "object_extent_field": "object_extent_m",
            "object_extent_semantics": "maximum_horizontal_extent_m",
            "target_components": "xyz",
        }
        g3["result_contract"]["required"] = [
            "status",
            "phase_reached",
            "completed_moves",
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_generated_package(self.root, self.stage1)

        failure = next(
            failure
            for failure in report.failures
            if failure.code == "INVALID_RESULT_CONTRACT"
            and failure.capability_id == g3["capability_id"]
        )
        self.assertIn("failed_move_index", failure.message)
        self.assertIn("timeout_scope", failure.message)

    def test_private_kinematics_is_required_and_inventory_rejects_extras(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.unlink()
        report = validate_generated_package(self.root, self.stage1)
        self.assertIn("MISSING_REQUIRED_FILE", _failure_codes(report))

        support.write_text(
            "def _forward_kinematics(value: object) -> object:\n    return value\n\n"
            "def _solve_ik(value: object) -> object:\n    return value\n",
            encoding="utf-8",
        )
        (support.parent / "extra.py").write_text("VALUE = 1\n", encoding="utf-8")
        report = validate_generated_package(self.root, self.stage1)
        self.assertIn("EXTRA_GENERATED_FILE", _failure_codes(report))

    def test_generated_inventory_rejects_symlinks_without_following_them(self) -> None:
        package = self.root / "generated_capability_package"
        outside = self.root / "outside.py"
        outside.write_text("raise RuntimeError('must not be parsed')\n", encoding="utf-8")
        support = package / "_kinematics.py"
        support.unlink()
        support.symlink_to(outside)

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("GENERATED_SYMLINK_FORBIDDEN", _failure_codes(report))
        self.assertIn("MISSING_REQUIRED_FILE", _failure_codes(report))

    def test_private_kinematics_rejects_time_runtime_and_object_side_effects(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            '''import time

def _forward_kinematics(handle: object) -> object:
    handle.send_action({})
    return time.monotonic()

def _solve_ik(runtime: object) -> object:
    return runtime
''',
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("INVALID_GENERATED_IMPORT", codes)
        self.assertIn("INVALID_PRIVATE_KINEMATICS", codes)
        self.assertIn("KINEMATICS_SIDE_EFFECT_FORBIDDEN", codes)

    def test_private_kinematics_rejects_mutable_globals_and_object_mutation(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            '''_cache = []

def _forward_kinematics(values: list[float]) -> list[float]:
    values[0] = 0.0
    return values

def _solve_ik(values: list[float]) -> list[float]:
    del values[0]
    return values
''',
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("KINEMATICS_MUTABLE_GLOBAL_FORBIDDEN", codes)
        self.assertIn("KINEMATICS_MUTATION_FORBIDDEN", codes)

    def test_private_kinematics_allows_fresh_local_numeric_work_arrays(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            '''def _forward_kinematics(values: list[float]) -> list[float]:
    work = list(values)
    work[0] = float(work[0]) + 1.0
    return work

def _solve_ik(values: list[float]) -> list[float]:
    work = list(values)
    work[0] = float(work[0]) - 1.0
    return work
''',
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())

    def test_forward_kinematics_rejects_run_n_site_rotation_omission(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            _run_n_kinematics_support(site_rotation_mode="omitted"),
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)
        failures = [
            failure
            for failure in report.failures
            if failure.code == "KINEMATICS_SITE_ROTATION_OMITTED"
        ]

        self.assertEqual(len(failures), 1, report.to_dict())
        failure = failures[0]
        self.assertEqual(
            failure.path,
            "generated_capability_package/_kinematics.py",
        )
        self.assertIsNotNone(failure.line)
        self.assertEqual(
            failure.observed["site_quaternion_reference_lines"],
            [],
        )
        self.assertEqual(
            failure.observed["tool_point_transform_calls"][0][
                "uses_composed_site_rotation"
            ],
            False,
        )
        self.assertTrue(
            failure.target["returned_site_rotated_tool_offset"],
        )

    def test_forward_kinematics_accepts_reference_style_site_rotation(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            _reference_style_kinematics_support(),
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())

    def test_dead_site_rotated_tool_offset_does_not_satisfy_gate(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            _run_n_kinematics_support(site_rotation_mode="dead"),
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)
        failures = [
            failure
            for failure in report.failures
            if failure.code == "KINEMATICS_SITE_ROTATION_OMITTED"
        ]

        self.assertEqual(len(failures), 1, report.to_dict())
        transforms = failures[0].observed["tool_point_transform_calls"]
        self.assertTrue(
            any(item["uses_composed_site_rotation"] for item in transforms)
        )
        self.assertTrue(
            any(not item["uses_composed_site_rotation"] for item in transforms)
        )

    def test_renamed_second_fk_argument_cannot_bypass_site_rotation_gate(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            _run_n_kinematics_support(
                site_rotation_mode="omitted",
                tool_parameter="controlled_coordinate_m",
            ),
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn(
            "KINEMATICS_SITE_ROTATION_OMITTED",
            _failure_codes(report),
            report.to_dict(),
        )

    def test_g2_g3_require_exact_private_support_import_without_duplicate_helpers(self) -> None:
        module = self.root / "generated_capability_package" / "g2.py"
        module.write_text(
            '''from ._kinematics import _solve_ik as _ik
from .g3 import pick_and_place

def _forward_copy(value: object) -> object:
    return value

def move_to_pose(runtime: object, pose: tuple[float, ...]) -> dict[str, object]:
    _solve_ik({"nested": [runtime]})
    return {"status": "failed"}
''',
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("INVALID_GENERATED_IMPORT", codes)
        self.assertIn("CROSS_GRANULARITY_IMPORT", codes)
        self.assertIn("KINEMATICS_REUSE_REQUIRED", codes)
        self.assertIn("DUPLICATE_KINEMATICS_HELPER", codes)
        self.assertIn("RUNTIME_TO_KINEMATICS_FORBIDDEN", codes)

    def test_g2_g3_cannot_expand_the_private_support_import_surface(self) -> None:
        support = self.root / "generated_capability_package" / "_kinematics.py"
        support.write_text(
            support.read_text(encoding="utf-8")
            + "\ndef _extra_helper(value: object) -> object:\n    return value\n",
            encoding="utf-8",
        )
        module = self.root / "generated_capability_package" / "g2.py"
        module.write_text(
            module.read_text(encoding="utf-8").replace(
                "from ._kinematics import _forward_kinematics, _solve_ik",
                "from ._kinematics import _extra_helper, _forward_kinematics, _solve_ik",
            ),
            encoding="utf-8",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("KINEMATICS_IMPORT_SURFACE_INVALID", _failure_codes(report))

    def test_unresolved_private_helper_call_fails_before_direct_execution(self) -> None:
        _write_module(
            self.root,
            "g3",
            '''
def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    source_pose = _forward_kinematics(source)
    target_joints = _solve_ik_with_fk_check(target)
    runtime.send_action({"source_pose": source_pose, "target_joints": target_joints})
    return {"status": "succeeded"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)
        unresolved = [
            failure
            for failure in report.failures
            if failure.code == "UNRESOLVED_PRIVATE_CALL"
        ]

        self.assertEqual(len(unresolved), 1, report.to_dict())
        failure = unresolved[0]
        self.assertEqual(
            failure.message,
            "private call target is unresolved in its lexical scope",
        )
        self.assertEqual(
            failure.path,
            "generated_capability_package/g3.py",
        )
        self.assertIsNotNone(failure.line)
        self.assertIsNotNone(failure.column)
        self.assertEqual(failure.observed, "_solve_ik_with_fk_check")
        self.assertEqual(
            failure.target,
            "a module/local definition, legal import, parameter, or builtin binding",
        )
        self.assertNotIn(
            "KINEMATICS_IMPORT_SURFACE_INVALID",
            _failure_codes(report),
        )

    def test_resolved_private_calls_do_not_widen_kinematics_imports(self) -> None:
        _write_module(
            self.root,
            "g3",
            '''
def _module_helper(value: object) -> object:
    return value

def _invoke(_callback: object, value: object) -> object:
    return _callback(value)

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    def _local_helper(value: object) -> object:
        return value

    source_value = _local_helper(_module_helper(source))
    target_joints = _solve_ik(target)
    target_pose = _forward_kinematics(target_joints)
    source_size = _invoke(len, source_value)
    return {
        "status": "succeeded",
        "source_size": source_size,
        "target_pose": target_pose,
    }
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())

    def test_g3_pre_close_selected_open_command_must_flow_through_helper(self) -> None:
        stale_observation_source = '''
def _approach(
    runtime: object,
    tool_center_in_frame_m: tuple[float, float, float],
) -> None:
    observed = runtime.get_observation()
    action = {"shoulder_pan.pos": float(tool_center_in_frame_m[0])}
    action["gripper.pos"] = float(observed["gripper.pos"])
    runtime.send_action(action)
    for _index in range(2):
        waypoint = {"shoulder_pan.pos": float(tool_center_in_frame_m[1])}
        waypoint["gripper.pos"] = float(observed["gripper.pos"])
        runtime.send_action(waypoint)

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    sample = {
        "command": 20.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.035),
    }
    runtime.send_action({"gripper.pos": float(sample["command"])})
    _approach(runtime, sample["tool_center_in_frame_m"])
    _forward_kinematics(source)
    _solve_ik(target)
    return {"status": "succeeded"}
'''
        _write_module(self.root, "g3", stale_observation_source)

        report = validate_generated_package(self.root, self.stage1)
        failures = [
            failure
            for failure in report.failures
            if failure.code == "G3_PRE_CLOSE_GRIPPER_COMMAND_FLOW_INVALID"
        ]

        self.assertEqual(len(failures), 1, report.to_dict())
        failure = failures[0]
        self.assertEqual(failure.capability_id, "G3.pick_and_place")
        self.assertEqual(failure.observed["selected_sample"], "sample")
        self.assertEqual(failure.observed["motion_helper"], "_approach")
        self.assertIsNone(failure.observed["selected_command_parameter"])
        self.assertEqual(len(failure.observed["send_action_lines"]), 2)
        self.assertEqual(len(failure.observed["unproven_send_actions"]), 2)
        self.assertTrue(
            all(
                "observed" in item["gripper_source"]
                and "gripper.pos" in item["gripper_source"]
                for item in failure.observed["unproven_send_actions"]
            )
        )
        self.assertEqual(
            failure.target["required_dataflow"],
            [
                (
                    "selected aperture sample -> direct field arguments, or one "
                    "explicit wrapper sample parameter"
                ),
                (
                    "the same sample's ['tool_center_in_frame_m'] and "
                    "['command'] -> distinct explicit motion-helper parameters"
                ),
                "command parameter -> every pre-close runtime.send_action gripper.pos",
            ],
        )

        explicit_selected_command_source = '''
def _approach(
    runtime: object,
    tool_center_in_frame_m: tuple[float, float, float],
    selected_open_command: float,
) -> None:
    open_value = float(selected_open_command)
    action = {
        "shoulder_pan.pos": float(tool_center_in_frame_m[0]),
        "gripper.pos": open_value,
    }
    runtime.send_action(action)
    for _index in range(2):
        waypoint = {"shoulder_pan.pos": float(tool_center_in_frame_m[1])}
        waypoint["gripper.pos"] = float(open_value)
        runtime.send_action(waypoint)

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    sample = {
        "command": 20.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.035),
    }
    runtime.send_action({"gripper.pos": float(sample["command"])})
    _approach(
        runtime,
        sample["tool_center_in_frame_m"],
        sample["command"],
    )
    _forward_kinematics(source)
    _solve_ik(target)
    return {"status": "succeeded"}
'''
        _write_module(self.root, "g3", explicit_selected_command_source)

        repaired_report = validate_generated_package(self.root, self.stage1)

        self.assertNotIn(
            "G3_PRE_CLOSE_GRIPPER_COMMAND_FLOW_INVALID",
            _failure_codes(repaired_report),
            repaired_report.to_dict(),
        )
        self.assertTrue(repaired_report.passed, repaired_report.to_dict())

    def test_g3_pre_close_nested_wrapper_requires_same_sample_definite_flow(self) -> None:
        template = '''
def _motion(
    runtime: object,
    target: tuple[float, float, float],
    tool_center: tuple[float, float, float],
    open_command: float,
) -> None:
    action = {"shoulder_pan.pos": float(target[0])}
ACTION_GRIPPER
    runtime.send_action(action)

def _approach_wrapper(
    runtime: object,
    target: tuple[float, float, float],
    selected_sample: dict[str, object],
    other_sample: dict[str, object],
) -> None:
WRAPPER_EXTRACTION
    _motion(runtime, target, tool_center, MOTION_COMMAND)

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    sample = {
        "command": 20.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.035),
    }
    other_sample = {
        "command": 40.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.055),
    }
    _approach_wrapper(runtime, source, sample, other_sample)
    _forward_kinematics(source)
    _solve_ik(target)
    return {"status": "succeeded"}
'''

        def source(
            *,
            extraction: str,
            motion_command: str,
            action_gripper: str,
        ) -> str:
            return (
                template.replace("WRAPPER_EXTRACTION", extraction)
                .replace("MOTION_COMMAND", motion_command)
                .replace("ACTION_GRIPPER", action_gripper)
            )

        safe = source(
            extraction=(
                '    tool_center = selected_sample["tool_center_in_frame_m"]\n'
                '    selected_open_command = float(selected_sample["command"])'
            ),
            motion_command="selected_open_command",
            action_gripper='    action["gripper.pos"] = float(open_command)',
        )
        _write_module(self.root, "g3", safe)

        safe_report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(safe_report.passed, safe_report.to_dict())

        unsafe_sources = {
            "constant": source(
                extraction=(
                    '    tool_center = selected_sample["tool_center_in_frame_m"]\n'
                    '    selected_open_command = float(selected_sample["command"])'
                ),
                motion_command="40.0",
                action_gripper='    action["gripper.pos"] = float(open_command)',
            ),
            "mixed_sample": source(
                extraction=(
                    '    tool_center = selected_sample["tool_center_in_frame_m"]\n'
                    '    selected_open_command = float(other_sample["command"])'
                ),
                motion_command="selected_open_command",
                action_gripper='    action["gripper.pos"] = float(open_command)',
            ),
            "missing_gripper_key": source(
                extraction=(
                    '    tool_center = selected_sample["tool_center_in_frame_m"]\n'
                    '    selected_open_command = float(selected_sample["command"])'
                ),
                motion_command="selected_open_command",
                action_gripper="    action = dict(action)",
            ),
            "branch_unproven": source(
                extraction=(
                    '    tool_center = selected_sample["tool_center_in_frame_m"]\n'
                    '    if target[0] >= 0.0:\n'
                    '        selected_open_command = float(selected_sample["command"])'
                ),
                motion_command="selected_open_command",
                action_gripper='    action["gripper.pos"] = float(open_command)',
            ),
        }
        for label, unsafe in unsafe_sources.items():
            with self.subTest(label=label):
                _write_module(self.root, "g3", unsafe)
                report = validate_generated_package(self.root, self.stage1)
                flow_failures = [
                    failure
                    for failure in report.failures
                    if failure.code
                    == "G3_PRE_CLOSE_GRIPPER_COMMAND_FLOW_INVALID"
                    and failure.observed.get("selected_sample") == "sample"
                ]
                self.assertTrue(flow_failures, report.to_dict())
                self.assertTrue(
                    all(
                        failure.observed["flow_route"] == "nested_wrapper"
                        for failure in flow_failures
                    ),
                    report.to_dict(),
                )

    def test_g3_pre_close_checks_only_globally_earliest_motion_boundary(self) -> None:
        template = '''
def _motion(
    runtime: object,
    target: tuple[float, float, float],
    tool_center: tuple[float, float, float],
    gripper_command: float,
) -> None:
    action = {
        "shoulder_pan.pos": float(target[0]) + float(tool_center[0]),
        "gripper.pos": float(gripper_command),
    }
    runtime.send_action(action)

def _lower_release_retreat(
    runtime: object,
    target: tuple[float, float, float],
    lower_tool_center: tuple[float, float, float],
    release_tool_center: tuple[float, float, float],
    holding_close_command: float,
    release_command: float,
) -> None:
    lower_action = {
        "shoulder_pan.pos": float(target[0]) + float(lower_tool_center[0]),
        "gripper.pos": float(holding_close_command),
    }
    runtime.send_action(lower_action)
    release_action = {
        "shoulder_pan.pos": float(target[0]) + float(release_tool_center[0]),
        "gripper.pos": float(release_command),
    }
    runtime.send_action(release_action)

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    grasp_sample = {
        "command": 20.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.035),
    }
    release_sample = {
        "command": 40.0,
        "tool_center_in_frame_m": (0.0, 0.0, 0.055),
    }
    _motion(
        runtime,
        source,
        grasp_sample["tool_center_in_frame_m"],
        FIRST_COMMAND,
    )
    _lower_release_retreat(
        runtime,
        target,
        grasp_sample["tool_center_in_frame_m"],
        release_sample["tool_center_in_frame_m"],
        1.1,
        release_sample["command"],
    )
    _forward_kinematics(source)
    _solve_ik(target)
    return {"status": "succeeded"}
'''

        safe = template.replace("FIRST_COMMAND", 'grasp_sample["command"]')
        _write_module(self.root, "g3", safe)

        safe_report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(safe_report.passed, safe_report.to_dict())

        unsafe = template.replace("FIRST_COMMAND", 'release_sample["command"]')
        _write_module(self.root, "g3", unsafe)

        unsafe_report = validate_generated_package(self.root, self.stage1)
        flow_failures = [
            failure
            for failure in unsafe_report.failures
            if failure.code == "G3_PRE_CLOSE_GRIPPER_COMMAND_FLOW_INVALID"
        ]

        self.assertEqual(len(flow_failures), 1, unsafe_report.to_dict())
        self.assertEqual(
            flow_failures[0].observed["selected_sample"],
            "grasp_sample",
        )
        self.assertEqual(flow_failures[0].observed["motion_helper"], "_motion")

    def test_bound_and_timeout_parameters_must_be_used_by_public_body(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G2"
        )
        capability["signature"]["parameters"].append(
            {
                "name": "timeout_s",
                "annotation": "float",
                "has_default": True,
                "default": 1.0,
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _write_module(
            self.root,
            "g2",
            '''
def move_to_pose(
    runtime: object,
    pose: tuple[float, ...],
    timeout_s: float = 1.0,
) -> dict[str, object]:
    runtime.send_action({})
    return {"status": "failed"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)
        unused = {
            failure.observed
            for failure in report.failures
            if failure.code == "UNUSED_SEMANTIC_PARAMETER"
        }

        self.assertEqual(unused, {"pose", "timeout_s"}, report.to_dict())

    def test_returned_literal_status_must_be_declared_in_manifest(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    if position > 0.0:
        return {"status": "invalid_target"}
    return {"status": "succeeded" if joint else "failed"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)
        undeclared = [
            failure
            for failure in report.failures
            if failure.code == "UNDECLARED_STATUS_LITERAL"
        ]

        self.assertEqual(len(undeclared), 1, report.to_dict())
        self.assertEqual(undeclared[0].observed, "invalid_target")
        self.assertEqual(undeclared[0].target, ["failed", "succeeded"])

    def test_missing_file_returns_structured_failure_feedback(self) -> None:
        (self.root / "generated_capability_package" / "g3.py").unlink()

        report = validate_generated_package(self.root, self.stage1)
        feedback = report.to_failure_feedback(repair_round=2)

        self.assertFalse(report.passed)
        self.assertIn("MISSING_REQUIRED_FILE", _failure_codes(report))
        self.assertIsNotNone(feedback)
        assert feedback is not None
        self.assertEqual(feedback["schema_version"], "robot_capability.failure_feedback.v1")
        self.assertEqual(feedback["stage"], "static")
        self.assertEqual(feedback["repair_round"], 2)
        self.assertTrue(feedback["failures"])

    def test_manifest_must_match_frozen_stage1_one_to_one(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage1_sha256"] = "0" * 64
        manifest["capabilities"][0]["function_name"] = "renamed_joint_command"
        manifest["capabilities"].append(
            {
                "capability_id": "G3.extra",
                "granularity": "G3",
                "module": "g3",
                "function_name": "extra",
                "signature": {},
                "result_contract": {},
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_generated_package(self.root, self.stage1)

        self.assertFalse(report.passed)
        self.assertTrue(
            {"STAGE1_HASH_MISMATCH", "MANIFEST_FIELD_MISMATCH", "MANIFEST_CAPABILITY_EXTRA"}
            <= _failure_codes(report)
        )

    def test_public_functions_require_runtime_first_and_complete_annotations(self) -> None:
        _write_module(
            self.root,
            "g1",
            """
def command_joint(joint, runtime: object, position: float):
    return {}

def public_helper(runtime: object) -> dict[str, object]:
    return {}
""",
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("RUNTIME_NOT_FIRST_PARAMETER", codes)
        self.assertIn("MISSING_PARAMETER_ANNOTATION", codes)
        self.assertIn("MISSING_RETURN_ANNOTATION", codes)
        self.assertIn("UNEXPECTED_PUBLIC_FUNCTION", codes)

    def test_all_forbidden_imports_are_found_even_inside_functions(self) -> None:
        _write_module(
            self.root,
            "g2",
            """
import httpx
import importlib
import mujoco
import os
import oracle
import pathlib
import private
import requests
import serial
import socket
import subprocess
import sys
import urllib
import validation
from soarm_demo import bridge

def move_to_pose(runtime: object, pose: tuple[float, ...]) -> dict[str, object]:
    import lerobot
    return {}
""",
        )

        report = validate_generated_package(self.root, self.stage1)
        forbidden = [
            failure.observed
            for failure in report.failures
            if failure.code == "FORBIDDEN_IMPORT"
        ]

        self.assertTrue(
            {
                "httpx",
                "importlib",
                "lerobot",
                "mujoco",
                "os",
                "oracle",
                "pathlib",
                "private",
                "requests",
                "serial",
                "soarm_demo.bridge",
                "socket",
                "subprocess",
                "sys",
                "urllib",
                "validation",
            }
            <= set(forbidden)
        )

    def test_forbidden_calls_and_top_level_side_effects_are_rejected(self) -> None:
        _write_module(
            self.root,
            "g3",
            """
def _make_cache() -> dict[str, object]:
    return {}

CACHE = _make_cache()

def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    return eval("{}")
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("FORBIDDEN_CALL", _failure_codes(report))
        self.assertIn("TOP_LEVEL_SIDE_EFFECT", _failure_codes(report))

    def test_every_forbidden_builtin_call_is_reported(self) -> None:
        _write_module(
            self.root,
            "g1",
            """
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    eval("1")
    exec("value = 1")
    compile("value = 1", "generated", "exec")
    __import__("math")
    open("forbidden.txt")
    return {}
""",
        )

        report = validate_generated_package(self.root, self.stage1)
        forbidden = {
            failure.observed
            for failure in report.failures
            if failure.code == "FORBIDDEN_CALL"
        }

        self.assertEqual(forbidden, {"eval", "exec", "compile", "__import__", "open"})

    def test_non_whitelisted_top_level_control_flow_is_rejected(self) -> None:
        _write_module(
            self.root,
            "g1",
            """
if True:
    VALUE = 1

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    return {}
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("TOP_LEVEL_SIDE_EFFECT", _failure_codes(report))

    def test_runtime_lifecycle_and_internals_are_framework_owned(self) -> None:
        _write_module(
            self.root,
            "g1",
            """
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    runtime.connect()
    runtime.disconnect()
    return {"bus": runtime.bus}
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("FORBIDDEN_RUNTIME_LIFECYCLE", _failure_codes(report))
        self.assertIn("FORBIDDEN_RUNTIME_INTERNAL", _failure_codes(report))

    def test_runtime_reset_simulation_state_and_private_members_are_forbidden(self) -> None:
        _write_module(
            self.root,
            "g1",
            """
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    runtime.reset()
    runtime.advance(0.1)
    runtime.world_snapshot()
    runtime.site_position()
    runtime.body_position("red_cube")
    leaked = runtime._control_backend
    return {"model": runtime.model, "data": runtime.data, "leaked": leaked}
""",
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("FORBIDDEN_RUNTIME_PRIVILEGED_CALL", codes)
        self.assertIn("FORBIDDEN_RUNTIME_INTERNAL", codes)
        self.assertIn("FORBIDDEN_ATTRIBUTE", codes)
        observed = {
            failure.observed
            for failure in report.failures
            if failure.code == "FORBIDDEN_ATTRIBUTE"
        }
        self.assertTrue(
            {
                "reset",
                "advance",
                "world_snapshot",
                "site_position",
                "body_position",
                "_control_backend",
                "model",
                "data",
            }
            <= observed
        )

    def test_runtime_kinematics_probes_are_rejected_by_positive_allowlist(self) -> None:
        _write_module(
            self.root,
            "g2",
            '''
def move_to_pose(runtime: object, pose: tuple[float, ...]) -> dict[str, object]:
    if hasattr(runtime, "compute_ik"):
        runtime.compute_ik(*pose)
    runtime.compute_fk(0.0, 0.0, 0.0, 0.0, 0.0)
    return {"status": "failed"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)
        undocumented = {
            failure.observed
            for failure in report.failures
            if failure.code == "UNDOCUMENTED_RUNTIME_ATTRIBUTE"
        }

        self.assertIn("FORBIDDEN_CALL", codes)
        self.assertEqual(undocumented, {"compute_ik", "compute_fk"})

    def test_generated_results_cannot_construct_nan_or_infinity(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
import math

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    return {
        "status": "failed",
        "a": float("inf"),
        "b": math.nan,
        "c": 1e999,
    }
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        assert sum(
            failure.code == "NONFINITE_GENERATED_VALUE"
            for failure in report.failures
        ) == 3

    def test_g3_validation_binding_requires_both_source_and_goal(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        g3_index = next(
            index
            for index, item in enumerate(original["capabilities"])
            if item["granularity"] == "G3"
        )
        for binding in (
            {
                "effect": "object_source_to_target",
                "source_argument": "source",
                "target_components": "xyz",
            },
            {
                "effect": "object_source_to_target",
                "target_argument": "target",
                "target_components": "xyz",
            },
        ):
            manifest = json.loads(json.dumps(original))
            manifest["capabilities"][g3_index]["validation_binding"] = binding
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = validate_generated_package(self.root, self.stage1)

            self.assertIn("INVALID_VALIDATION_BINDING", _failure_codes(report))

    def test_move_sequence_rejects_public_string_field_selectors(self) -> None:
        self.stage1["layers"]["G3"]["capabilities"][0][
            "validation_effect"
        ] = "object_move_sequence"
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage1_sha256"] = compute_stage1_sha256(self.stage1)
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G3"
        )
        capability["signature"]["parameters"] = [
            {"name": "runtime", "annotation": "object", "has_default": False},
            {
                "name": "moves",
                "annotation": "list[dict[str, object]]",
                "has_default": False,
            },
            {"name": "source_field", "annotation": "str", "has_default": False},
            {"name": "target_field", "annotation": "str", "has_default": False},
        ]
        capability["validation_binding"] = {
            "effect": "object_move_sequence",
            "moves_argument": "moves",
            "source_field": "source_field",
            "target_field": "target_field",
            "object_extent_field": "object_extent_m",
            "object_extent_semantics": "maximum_horizontal_extent_m",
            "target_components": "xyz",
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _write_module(
            self.root,
            "g3",
            '''
def pick_and_place(
    runtime: object,
    moves: list[dict[str, object]],
    source_field: str,
    target_field: str,
) -> dict[str, object]:
    for move in moves:
        runtime.send_action({"source": move[source_field], "target": move[target_field]})
    return {"status": "succeeded"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        matching = [
            failure
            for failure in report.failures
            if failure.code == "INVALID_VALIDATION_BINDING"
            and "fixed literal" in failure.message
        ]
        self.assertEqual(len(matching), 1, report.to_dict())
        self.assertEqual(matching[0].observed, ["source_field", "target_field"])

    def test_move_sequence_unhashable_field_is_reported_not_raised(self) -> None:
        self.stage1["layers"]["G3"]["capabilities"][0][
            "validation_effect"
        ] = "object_move_sequence"
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage1_sha256"] = compute_stage1_sha256(self.stage1)
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G3"
        )
        capability["signature"]["parameters"] = [
            {"name": "runtime", "annotation": "object", "has_default": False},
            {
                "name": "moves",
                "annotation": "list[dict[str, object]]",
                "has_default": False,
            },
        ]
        capability["validation_binding"] = {
            "effect": "object_move_sequence",
            "moves_argument": "moves",
            "source_field": "source_position_m",
            "target_field": "target_position_m",
            "object_extent_field": ["unhashable"],
            "object_extent_semantics": "maximum_horizontal_extent_m",
            "target_components": "xyz",
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("INVALID_VALIDATION_BINDING", _failure_codes(report))

    def test_g3_auxiliary_geometry_parameters_require_explicit_bindings(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G3"
        )
        capability["signature"]["parameters"].extend(
            [
                {
                    "name": "object_size_m",
                    "annotation": "float",
                    "has_default": True,
                    "default": 0.03,
                },
                {
                    "name": "contact_height_above_table_m",
                    "annotation": "float",
                    "has_default": True,
                    "default": 0.02,
                },
            ]
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_generated_package(self.root, self.stage1)

        codes = _failure_codes(report)
        self.assertIn("OBJECT_EXTENT_BINDING_REQUIRED", codes)
        self.assertIn("CONTACT_HEIGHT_BINDING_REQUIRED", codes)

    def test_scalar_x_cannot_claim_to_bind_an_xyz_cartesian_target(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G2"
        )
        capability["signature"]["parameters"] = [
            {"name": "runtime", "annotation": "object", "has_default": False},
            {"name": "target_x_m", "annotation": "float", "has_default": False},
            {"name": "target_y_m", "annotation": "float", "has_default": False},
            {"name": "target_z_m", "annotation": "float", "has_default": False},
        ]
        capability["validation_binding"] = {
            "effect": "cartesian_target",
            "target_argument": "target_x_m",
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _write_module(
            self.root,
            "g2",
            """
def move_to_pose(
    runtime: object,
    target_x_m: float,
    target_y_m: float,
    target_z_m: float,
) -> dict[str, object]:
    raise RuntimeError(
        f"must not execute: {(target_x_m, target_y_m, target_z_m)!r}"
    )
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("VECTOR_BINDING_SHAPE_MISMATCH", _failure_codes(report))

    def test_explicit_xyz_component_map_binds_split_scalar_api(self) -> None:
        manifest_path = self.root / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        capability = next(
            item for item in manifest["capabilities"] if item["granularity"] == "G2"
        )
        capability["signature"]["parameters"] = [
            {"name": "runtime", "annotation": "object", "has_default": False},
            {"name": "target_x_m", "annotation": "float", "has_default": False},
            {"name": "target_y_m", "annotation": "float", "has_default": False},
            {"name": "target_z_m", "annotation": "float", "has_default": False},
        ]
        capability["validation_binding"] = {
            "effect": "cartesian_target",
            "target_arguments": {
                "x": "target_x_m",
                "y": "target_y_m",
                "z": "target_z_m",
            },
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _write_module(
            self.root,
            "g2",
            """
def move_to_pose(
    runtime: object,
    target_x_m: float,
    target_y_m: float,
    target_z_m: float,
) -> dict[str, object]:
    raise RuntimeError(
        f"must not execute: {(target_x_m, target_y_m, target_z_m)!r}"
    )
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())

    def test_granularity_modules_cannot_import_each_other(self) -> None:
        _write_module(
            self.root,
            "g2",
            """
from .g1 import command_joint

def move_to_pose(runtime: object, pose: tuple[float, ...]) -> dict[str, object]:
    return command_joint(runtime, "shoulder_pan", 0.0)
""",
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("CROSS_GRANULARITY_IMPORT", _failure_codes(report))

    def test_repair_round_is_bounded_by_failure_feedback_schema(self) -> None:
        (self.root / "package_manifest.json").unlink()
        report = validate_generated_package(self.root, self.stage1)

        with self.assertRaises(ValueError):
            report.to_failure_feedback(repair_round=11)

    def test_reflection_builtin_and_dunder_subscript_escape_are_rejected(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    reader = globals()["__builtins__"]["open"]
    return {"secret": reader("private/oracle.yaml").read()}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(
            {"FORBIDDEN_CALL", "FORBIDDEN_REFLECTION"} & _failure_codes(report)
        )

    def test_runtime_alias_cannot_bypass_lifecycle_and_internal_boundary(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    alias = runtime
    alias.disconnect()
    return {"model": alias.model}
''',
        )

        report = validate_generated_package(self.root, self.stage1)
        codes = _failure_codes(report)

        self.assertIn("RUNTIME_ALIAS_FORBIDDEN", codes)
        self.assertIn("FORBIDDEN_ATTRIBUTE", codes)

    def test_annotation_cannot_execute_code_during_import(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
def command_joint(
    runtime: object,
    joint: open("private/oracle.yaml").read(),
    position: float,
) -> dict[str, object]:
    return {}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("UNSAFE_ANNOTATION", _failure_codes(report))

    def test_unknown_import_is_rejected_by_allowlist(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
import random

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    return {"value": random.random()}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("FORBIDDEN_IMPORT", _failure_codes(report))

    def test_module_time_calls_are_accepted_for_framework_clock_binding(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
import time

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    runtime.send_action({joint: position})
    deadline = time.monotonic() + 0.05
    if time.monotonic() < deadline:
        time.sleep(0.005)
    return {"status": "succeeded"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertTrue(report.passed, report.to_dict())

    def test_time_import_alias_from_import_and_local_import_are_rejected(self) -> None:
        invalid_sources = (
            '''
import time as clock

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    clock.sleep(0.01)
    return {"status": "succeeded"}
''',
            '''
from time import monotonic, sleep

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    sleep(0.01)
    return {"status": "succeeded", "finished_at": monotonic()}
''',
            '''
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    import time
    time.sleep(0.01)
    return {"status": "succeeded"}
''',
        )
        for source in invalid_sources:
            with self.subTest(source=source):
                _write_module(self.root, "g1", source)

                report = validate_generated_package(self.root, self.stage1)

                self.assertIn(
                    "INVALID_TIME_IMPORT_STYLE",
                    _failure_codes(report),
                    report.to_dict(),
                )

    def test_time_module_and_callables_cannot_be_captured_or_shadowed(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
import time

SLEEP = time.sleep

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    clock = time
    clock.sleep(0.01)
    return {"status": "succeeded"}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("TIME_BINDING_ALIAS_FORBIDDEN", _failure_codes(report))

    def test_only_sleep_and_monotonic_are_available_on_generated_time(self) -> None:
        _write_module(
            self.root,
            "g1",
            '''
import time

def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    return {"status": "succeeded", "host_time": time.perf_counter()}
''',
        )

        report = validate_generated_package(self.root, self.stage1)

        self.assertIn("UNDOCUMENTED_TIME_ATTRIBUTE", _failure_codes(report))


def _stage1_artifact() -> dict[str, Any]:
    def layer(
        capability_id: str,
        function_name: str,
        validation_effect: str,
    ) -> dict[str, Any]:
        granularity = capability_id.split(".", 1)[0]
        return {
            "rationale": "A sufficiently detailed rationale.",
            "capabilities": [
                {
                    "capability_id": capability_id,
                    "function_name": function_name,
                    "intended_outcome": "A deterministic robot state transition.",
                    "validation_effect": validation_effect,
                    "implementation_family": {
                        "G1": "joint_motion",
                        "G2": "cartesian_motion",
                        "G3": "pick_place",
                    }[granularity],
                    "implementation_evidence": {
                        "status": "verified",
                        "basis": {
                            "G1": "pinned_runtime_contract",
                            "G2": "compiled_kinematics",
                            "G3": "verified_physical_baseline",
                        }[granularity],
                        "refs": ["fixture://stage1"],
                    },
                }
            ],
        }

    return {
        "schema_version": "robot_capability.stage1.v2",
        "target": {
            "robot_id": "soarm101",
            "runtime_id": "lerobot_soarm101_0_6_0",
        },
        "layers": {
            "G1": layer("G1.command_joint", "command_joint", "joint_targets"),
            "G2": layer("G2.move_to_pose", "move_to_pose", "cartesian_target"),
            "G3": layer(
                "G3.pick_and_place",
                "pick_and_place",
                "object_source_to_target",
            ),
        },
        "assumptions": [],
        "unresolved_evidence_gaps": [],
        "evidence_refs": [],
    }


def _kinematics_test_math_prefix() -> str:
    return '''_BODY_POSITIONS_M = ((0.0388353, 0.0, 0.0624),)
_BODY_QUATERNIONS_WXYZ = ((0.0, 0.0, -1.0, 0.0),)
_GRIPPERFRAME_POSITION_M = (0.012, -0.000218, -0.098127)
_GRIPPERFRAME_QUATERNION_WXYZ = (1.0, 0.0, 1.0, 0.0)

def _matmul3(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(
            sum(left[row][index] * right[index][column] for index in range(3))
            for column in range(3)
        )
        for row in range(3)
    )

def _matvec3(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][index] * vector[index] for index in range(3))
        for row in range(3)
    )

def _quaternion_matrix_wxyz(
    quaternion: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    w, x, y, z = quaternion
    return (
        (w * w + x * x - y * y - z * z, 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), w * w - x * x + y * y - z * z, 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), w * w - x * x - y * y + z * z),
    )

def _rotation_z(angle_rad: float) -> tuple[tuple[float, float, float], ...]:
    cosine = float(angle_rad) * 0.0 + 1.0
    sine = float(angle_rad) * 0.0
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))

'''


def _run_n_kinematics_support(
    *,
    site_rotation_mode: str,
    tool_parameter: str = "tool_point_in_gripperframe_m",
) -> str:
    site_rotation_source = {
        "omitted": "",
        "dead": '''    dead_site_rotation = _matmul3(
        rotation,
        _quaternion_matrix_wxyz(_GRIPPERFRAME_QUATERNION_WXYZ),
    )
    dead_tool_offset = _matvec3(
        dead_site_rotation,
        tuple(float(value) for value in TOOL_PARAMETER),
    )
''',
    }[site_rotation_mode]
    function_source = '''def _forward_kinematics(
    joint_positions_rad: tuple[float, float, float, float, float],
    TOOL_PARAMETER: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    rotation = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    position = (0.0, 0.0, 0.0)
    for body_position, body_quaternion, joint_angle in zip(
        _BODY_POSITIONS_M,
        _BODY_QUATERNIONS_WXYZ,
        joint_positions_rad,
    ):
        offset = _matvec3(rotation, body_position)
        position = tuple(position[index] + offset[index] for index in range(3))
        rotation = _matmul3(rotation, _quaternion_matrix_wxyz(body_quaternion))
        rotation = _matmul3(rotation, _rotation_z(float(joint_angle)))

    site_offset = _matvec3(rotation, _GRIPPERFRAME_POSITION_M)
    site_position = tuple(
        position[index] + site_offset[index] for index in range(3)
    )
SITE_ROTATION_SOURCE
    tool_offset = _matvec3(
        rotation,
        tuple(float(value) for value in TOOL_PARAMETER),
    )
    tool_position = tuple(
        site_position[index] + tool_offset[index] for index in range(3)
    )
    return tool_position

def _solve_ik(
    target_position_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(float(value) for value in target_position_m)
'''
    return _kinematics_test_math_prefix() + function_source.replace(
        "SITE_ROTATION_SOURCE",
        site_rotation_source.rstrip(),
    ).replace("TOOL_PARAMETER", tool_parameter)


def _reference_style_kinematics_support() -> str:
    return _kinematics_test_math_prefix() + '''def _forward_pose_rad(
    joint_positions_rad: tuple[float, float, float, float, float],
) -> tuple[
    tuple[float, float, float],
    tuple[tuple[float, float, float], ...],
]:
    rotation = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    position = (0.0, 0.0, 0.0)
    for body_position, body_quaternion, joint_angle in zip(
        _BODY_POSITIONS_M,
        _BODY_QUATERNIONS_WXYZ,
        joint_positions_rad,
    ):
        offset = _matvec3(rotation, body_position)
        position = tuple(position[index] + offset[index] for index in range(3))
        rotation = _matmul3(rotation, _quaternion_matrix_wxyz(body_quaternion))
        rotation = _matmul3(rotation, _rotation_z(float(joint_angle)))
    site_offset = _matvec3(rotation, _GRIPPERFRAME_POSITION_M)
    site_position = tuple(
        position[index] + site_offset[index] for index in range(3)
    )
    site_rotation = _matmul3(
        rotation,
        _quaternion_matrix_wxyz(_GRIPPERFRAME_QUATERNION_WXYZ),
    )
    return site_position, site_rotation

def _forward_tool_point_rad(
    joint_positions_rad: tuple[float, float, float, float, float],
    point_in_gripperframe_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    position, rotation = _forward_pose_rad(joint_positions_rad)
    offset = _matvec3(
        rotation,
        tuple(float(value) for value in point_in_gripperframe_m),
    )
    return tuple(position[index] + offset[index] for index in range(3))

def _forward_kinematics(
    joint_positions_rad: tuple[float, float, float, float, float],
    tool_point_in_gripperframe_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    return _forward_tool_point_rad(
        joint_positions_rad,
        tool_point_in_gripperframe_m,
    )

def _solve_ik(
    target_position_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(float(value) for value in target_position_m)
'''


def _write_valid_package(root: Path, stage1: dict[str, Any]) -> None:
    package = root / "generated_capability_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '"""Generated package used only for static-validation tests."""\n',
        encoding="utf-8",
    )
    (package / "_kinematics.py").write_text(
        '''"""Private mathematical fixture support."""

def _forward_kinematics(value: object) -> object:
    return value

def _solve_ik(value: object) -> object:
    return value
''',
        encoding="utf-8",
    )
    _write_module(
        root,
        "g1",
        """
def command_joint(runtime: object, joint: str, position: float) -> dict[str, object]:
    raise RuntimeError(
        f"generated code must not execute during static validation: {position!r}"
    )
""",
    )
    _write_module(
        root,
        "g2",
        """
def move_to_pose(runtime: object, pose: tuple[float, ...]) -> dict[str, object]:
    raise RuntimeError(
        f"generated code must not execute during static validation: {pose!r}"
    )
""",
    )
    _write_module(
        root,
        "g3",
        """
def pick_and_place(
    runtime: object,
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> dict[str, object]:
    raise RuntimeError(
        "generated code must not execute during static validation: "
        f"{source!r} -> {target!r}"
    )
""",
    )
    capabilities = []
    signatures = {
        "command_joint": [
            ("runtime", "object"),
            ("joint", "str"),
            ("position", "float"),
        ],
        "move_to_pose": [
            ("runtime", "object"),
            ("pose", "tuple[float, ...]"),
        ],
        "pick_and_place": [
            ("runtime", "object"),
            ("source", "tuple[float, float, float]"),
            ("target", "tuple[float, float, float]"),
        ],
    }
    validation_bindings = {
        "command_joint": {
            "effect": "joint_targets",
            "joint_target_arguments": {"shoulder_pan.pos": "position"},
        },
        "move_to_pose": {
            "effect": "cartesian_target",
            "target_argument": "pose",
        },
        "pick_and_place": {
            "effect": "object_source_to_target",
            "source_argument": "source",
            "target_argument": "target",
            "target_components": "xyz",
        },
    }
    for granularity in ("G1", "G2", "G3"):
        capability = stage1["layers"][granularity]["capabilities"][0]
        function_name = capability["function_name"]
        required_result_fields = ["status"]
        if granularity == "G3":
            required_result_fields.append("phase_reached")
        capabilities.append(
            {
                "capability_id": capability["capability_id"],
                "granularity": granularity,
                "module": granularity.lower(),
                "function_name": function_name,
                "signature": {
                    "parameters": [
                        {"name": name, "annotation": annotation, "has_default": False}
                        for name, annotation in signatures[function_name]
                    ],
                    "return_annotation": "dict[str, object]",
                },
                "validation_binding": validation_bindings[function_name],
                "result_contract": {
                    "type": "object",
                    "required": required_result_fields,
                    "status_values": ["succeeded", "failed"],
                    "success_status_values": ["succeeded"],
                },
            }
        )
    manifest = {
        "schema_version": "robot_capability.package_manifest.v2",
        "stage1_sha256": compute_stage1_sha256(stage1),
        "package": "generated_capability_package",
        "capabilities": capabilities,
    }
    (root / "package_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _write_module(root: Path, module: str, source: str) -> None:
    if module in {"g2", "g3"}:
        source = (
            "from ._kinematics import _forward_kinematics, _solve_ik\n\n"
            + source
        )
    (root / "generated_capability_package" / f"{module}.py").write_text(
        source.strip() + "\n",
        encoding="utf-8",
    )


def _failure_codes(report: Any) -> set[str]:
    return {failure.code for failure in report.failures}


if __name__ == "__main__":
    unittest.main()
