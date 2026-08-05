from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import jsonschema
import pytest
import yaml

from soarm_demo.schema_validation import ArtifactSchemaError, validate_stage1


ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _file_evidence(path: str, *, nonempty: bool = False) -> dict[str, Any]:
    return {"path": path, "sha256": HASH, "bytes": 1 if nonempty else 0}


def _budget(name: str, limit: int, used: int) -> dict[str, Any]:
    return {
        "name": name,
        "limit": limit,
        "used": used,
        "remaining": limit - used,
    }


def _phase_result(budget: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "completed",
        "agent_turns": budget["used"],
        "provider_http_attempts": 0,
        "provider_retries": 0,
        "agent_turn_budget": copy.deepcopy(budget),
        "provider_attempt_budget_policy": {},
        "usage": {},
        "session_id": "generation-session",
        "episode_id": "generation-episode",
    }


def _repair_result(round_number: int, *, used: int = 2) -> dict[str, Any]:
    return {
        "round": round_number,
        "status": "completed",
        "agent_turns": used,
        "provider_http_attempts": 0,
        "provider_retries": 0,
        "client_kind": "scripted_fixture",
        "scripted": True,
        "agent_turn_budget": _budget(
            f"generation_repair_round_{round_number:02d}", 6, used
        ),
        "provider_attempt_budget_policy": {},
        "usage": {},
        "session_id": "generation-session",
        "episode_id": "generation-episode",
    }


def _repair_budget(results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = [] if results is None else copy.deepcopy(results)
    return {
        "round_limit": 10,
        "rounds_used": len(values),
        "rounds_remaining": 10 - len(values),
        "model_call_limit_per_round": 6,
        "results": values,
    }


def _video_record(phase: str, identifier: str, *, round_number: int | None = None):
    value: dict[str, Any] = {
        "phase": phase,
        "id": identifier,
        "video": _file_evidence(f"videos/{identifier}.mp4", nonempty=True),
        "metadata": _file_evidence(
            f"videos/{identifier}.metadata.json", nonempty=True
        ),
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": 20.0,
        "frames": 4,
        "width": 640,
        "height": 480,
        "simulation_start_s": 0.0,
        "simulation_end_s": 0.2,
        "encoded_duration_s": 0.2,
    }
    if round_number is not None:
        value["round"] = round_number
    if phase == "validation":
        value.update(
            {
                "case_id": identifier,
                "suite_sha256": HASH,
                "package_sha256": HASH,
                "direct_report": _file_evidence(
                    f"validation/direct_{identifier}.json", nonempty=True
                ),
            }
        )
    elif phase == "demo":
        value.update(
            {
                "task_id": identifier,
                "package_sha256": HASH,
                "demo_freeze": _file_evidence(
                    "demo/freeze.json", nonempty=True
                ),
                "demo_report": _file_evidence(
                    "demo/demo_report.json", nonempty=True
                ),
            }
        )
    return value


def _video_index(*, required: bool, status: str = "complete") -> dict[str, Any]:
    return {
        "schema_version": "robot_capability.mujoco_video_index.v1",
        "required": required,
        "status": status,
        "renderer_kind": "mujoco.Renderer",
        "codec": "mp4v",
        "fps": 20.0,
        "width": 640,
        "height": 480,
        "validation": (
            [_video_record("validation", "case-1", round_number=0)]
            if required
            else []
        ),
        "demo": (
            [_video_record("demo", f"task-{index}") for index in range(1, 7)]
            if required and status == "complete"
            else []
        ),
        "offline_reason": None if required else "offline fixture has no video",
    }


def _sdk_activation(*, mode: str) -> dict[str, Any]:
    source = {"path": "manifest.yaml", "sha256": HASH}
    keys = [
        "shoulder_pan.pos",
        "shoulder_lift.pos",
        "elbow_flex.pos",
        "wrist_flex.pos",
        "wrist_roll.pos",
        "gripper.pos",
    ]
    decision = {
        "schema_version": "robot_capability.sdk_activation.v2",
        "mode": mode,
        "activation_scope": (
            "live_aws_provider_gate" if mode == "aws" else "offline_reference_only"
        ),
        "live_gate_satisfied": mode == "aws",
        "probe_status": "pass",
        "target": {"package": "lerobot", "version": "0.6.0", "commit": "a" * 40},
        "runtime_id": "lerobot_soarm101_0_6_0",
        "verified": {
            "canonical_import": {
                "module": "lerobot.robots.so_follower",
                "symbols": ["SO101Follower", "SO101FollowerConfig"],
            },
            "aliases": {
                "SO101Follower": "SOFollower",
                "SO101FollowerConfig": "SOFollowerRobotConfig",
            },
            "required_methods": [
                "connect",
                "disconnect",
                "send_action",
                "get_observation",
            ],
            "action_keys": keys,
            "observation_keys": keys,
            "units": {
                **{key: "deg" for key in keys[:-1]},
                "gripper.pos": "normalized_0_100",
            },
            "send_action_nonblocking": True,
            "hardware_or_serial_opened": False,
        },
        "bindings": {
            "manifest": source,
            "api_surface": {**source, "path": "api_surface.yaml"},
            "runtime_contract": {**source, "path": "runtime_contract.yaml"},
            "api_probe": {**source, "path": "api_probe.json"},
        },
    }
    return {
        "decision": decision,
        "evidence": _file_evidence("framework/sdk_activation.json", nonempty=True),
    }


def _valid_sealed_report(*, mode: str = "offline") -> dict[str, Any]:
    stage1_budget = _budget("generation_stage1", 3, 3)
    stage2_budget = _budget("generation_stage2_initial", 30, 7)
    suite_budget = _budget("validation_suite", 3, 3)
    repair = _repair_budget()
    input_hashes = {
        "library_manifest:morphology": HASH,
        "library_manifest:sdk_runtime": HASH,
        "library_manifest:tasks": HASH,
        "library_manifest:experience": HASH,
        "source_tree": HASH,
        "sdk_activation:manifest": HASH,
        "sdk_activation:api_surface": HASH,
        "sdk_activation:runtime_contract": HASH,
        "sdk_activation:api_probe": HASH,
        "task_demo_batch": HASH,
        "task_private_oracles": HASH,
        "task_visible_catalog": HASH,
        "task_pilot_heldout_catalog": HASH,
    }
    input_hashes.update({f"extra:{index}": HASH for index in range(6)})
    artifact_hashes = {
        name: HASH
        for name in (
            "stage1",
            "validation_suite",
            "generated_package",
            "combined_tool_catalog",
            "public_api",
            "package_manifest",
            "demo_freeze",
            "demo_report",
            "sdk_activation",
            "video_index",
        )
    }
    evidence_file = _file_evidence("artifact.json")
    video_index = _video_index(required=mode == "aws")
    video_evidence = [_file_evidence("video_index.json", nonempty=True)]
    records = [*video_index["validation"], *video_index["demo"]]
    for record in records:
        video_evidence.extend([record["video"], record["metadata"]])
        if "direct_report" in record:
            video_evidence.append(record["direct_report"])
    return {
        "schema_version": "robot_capability.sealed_run_report.v2",
        "run_id": "schema-test",
        "terminal_state": "SEALED",
        "terminal_reason": "completed",
        "mode": mode,
        "sealed_at": "2026-08-05T00:00:00+00:00",
        "run_configuration": {},
        "input_hashes": input_hashes,
        "artifact_hashes": artifact_hashes,
        "evidence": {
            "configuration": {},
            "run_manifest_preseal": evidence_file,
            "input_library_report": evidence_file,
            "private_execution_preflight": evidence_file,
            "stage1": [evidence_file],
            "generated_package": {},
            "public_api": evidence_file,
            "validation_suite": [evidence_file],
            "static_validation": [evidence_file],
            "direct_validation": [evidence_file],
            "tool_catalogs": [evidence_file],
            "demo": [evidence_file, evidence_file],
            "videos": video_evidence,
            "traces": [evidence_file for _ in range(8)],
        },
        "budgets": {
            "generation_stage1": stage1_budget,
            "generation_stage2_initial": stage2_budget,
            "validation_suite": suite_budget,
            "demo": {
                "limit_per_task": 30,
                "used_per_task": [2] * 6,
                "per_task": [
                    _budget(f"demo:task-{index}", 30, 2)
                    for index in range(1, 7)
                ],
            },
            "repair": repair,
        },
        "model_accounting": {
            "public_identities": {},
            "roles": {},
            "aggregate": {},
            "generation_phases": {
                "stage1": _phase_result(stage1_budget),
                "stage2_initial": _phase_result(stage2_budget),
                "repair_rounds": [],
            },
        },
        "generation_agent": {},
        "repairs": [],
        "repair": {**repair, "history": []},
        "validation": {
            "static_passed": True,
            "direct_function_passed": True,
            "suite_sha256": HASH,
            "public_api_sha256": HASH,
            "runtime": "fixture" if mode == "offline" else "actual_mujoco",
        },
        "static_validation_passed": True,
        "direct_function_validation_passed": True,
        "validation_runtime": "fixture" if mode == "offline" else "actual_mujoco",
        "demo": {
            "summary": {
                "visible": {"passed": 3, "total": 3, "success_rate": 1.0},
                "pilot-held-out": {
                    "passed": 3,
                    "total": 3,
                    "success_rate": 1.0,
                },
                "overall": {"passed": 6, "total": 6, "success_rate": 1.0},
            },
            "tasks": [
                {
                    "partition": (
                        "visible" if index <= 3 else "pilot-held-out"
                    ),
                    "task_id": f"task-{index}",
                    "passed": True,
                    "oracle": {"passed": True},
                }
                for index in range(1, 7)
            ],
        },
        "video_recording": video_index,
        "evidence_boundary": {},
        "sdk_activation": _sdk_activation(mode=mode),
    }


def _valid_terminal_report(*, mode: str = "offline") -> dict[str, Any]:
    repair = _repair_budget()
    return {
        "schema_version": "robot_capability.terminal_run_report.v2",
        "run_id": "terminal-schema-test",
        "terminal_state": "GENERATION_FAILED",
        "terminal_reason": "RuntimeError",
        "mode": mode,
        "ended_at": "2026-08-05T00:00:00+00:00",
        "error": {"type": "RuntimeError"},
        "input_hashes": {},
        "artifact_hashes": {},
        "budgets": {"repair": repair},
        "model_accounting": {
            "public_identities": {},
            "roles": {},
            "aggregate": {},
            "generation_phases": {
                "stage1": None,
                "stage2_initial": None,
                "repair_rounds": [],
            },
        },
        "repair": {**repair, "history": []},
        "video_recording": {
            "schema_version": "robot_capability.mujoco_video_status.v1",
            "required": mode == "aws",
            "status": "error" if mode == "aws" else "unavailable",
            "reason_code": "pipeline_failed_before_video_index",
        },
        "evidence_files": [],
    }


def test_four_library_manifests_validate() -> None:
    schema = _json(ROOT / "schemas/library_manifest.schema.json")
    manifests = [
        ROOT / "libraries/morphology/soarm101/v1/manifest.yaml",
        ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0/manifest.yaml",
        ROOT / "libraries/tasks/soarm101_tabletop/v1/manifest.yaml",
        ROOT / "libraries/experience/v1/manifest.yaml",
    ]
    for path in manifests:
        jsonschema.validate(_yaml(path), schema)


def test_morphology_and_sdk_payloads_validate() -> None:
    morphology = _yaml(ROOT / "libraries/morphology/soarm101/v1/kinematics.yaml")
    runtime_contract = _yaml(
        ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0/runtime_contract.yaml"
    )
    jsonschema.validate(morphology, _json(ROOT / "schemas/morphology.schema.json"))
    jsonschema.validate(
        _yaml(ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0/api_surface.yaml"),
        _json(ROOT / "schemas/sdk_runtime.schema.json"),
    )
    jsonschema.validate(
        runtime_contract,
        _json(ROOT / "schemas/runtime_contract.schema.json"),
    )
    jsonschema.validate(
        _json(ROOT / "libraries/sdk_runtime/lerobot_soarm101/0.6.0/api_probe.json"),
        _json(ROOT / "schemas/sdk_api_probe_result.schema.json"),
    )

    generated_time = runtime_contract["generated_time_semantics"]
    assert generated_time["import_statement"] == "import time"
    assert generated_time["allowed_calls"] == ["time.sleep", "time.monotonic"]
    assert generated_time["mujoco"] == {
        "host_auto_step": False,
        "monotonic_source": "MjData.time",
        "sleep_advances": "active_real_MjModel_MjData_pair",
        "sleep_step_count": "ceil(seconds / model.opt.timestep)",
        "per_tick_framework_callbacks": [
            "evidence_sampling",
            "video_capture_scheduling",
        ],
    }
    assert generated_time["hardware"] == {
        "module_binding": "python_standard_library_time",
        "monotonic_source": "python_standard_library_time.monotonic",
        "sleep_effect": "python_standard_library_time.sleep",
    }

    contact = morphology["gripper_contact_model"]
    limit_policy = morphology["command_limit_policy"]
    assert limit_policy["conversion_roundoff_margin_rad"] > 0
    assert limit_policy["validated_dynamic_margin_rad"] >= 0.02
    assert (
        limit_policy["validated_dynamic_margin_rad"]
        > limit_policy["conversion_roundoff_margin_rad"]
    )
    fixed = contact["fixed_tip_centroid_in_frame_m"]
    commands = [sample["command"] for sample in contact["aperture_samples"]]
    apertures = [sample["aperture_m"] for sample in contact["aperture_samples"]]
    assert commands == sorted(commands)
    assert apertures == sorted(apertures)
    for sample in contact["aperture_samples"]:
        midpoint = [
            (fixed[axis] + sample["moving_tip_centroid_in_frame_m"][axis]) / 2.0
            for axis in range(3)
        ]
        assert sample["tool_center_in_frame_m"] == pytest.approx(midpoint, abs=1e-8)
    minimum_cube_aperture = 0.034 + contact["verified_tabletop_pinch_baseline"][
        "minimum_total_aperture_clearance_m"
    ]
    selected = next(
        sample
        for sample in contact["aperture_samples"]
        if sample["aperture_m"] >= minimum_cube_aperture
    )
    assert selected["command"] == 24.0
    baseline = contact["verified_tabletop_pinch_baseline"]
    release = contact["tabletop_release_and_sequence_requirements"]
    controlled_point_ik = baseline["verified_controlled_point_ik"]
    assert controlled_point_ik == {
        "status": "verified",
        "phases": ["approach", "descent", "lower"],
        "point_source": "selected_open_aperture_sample.tool_center_in_frame_m",
        "tolerance_m": 0.0006,
        "post_clamp_fk_check_required": True,
        "post_clamp_fk_point_source": (
            "selected_open_aperture_sample.tool_center_in_frame_m"
        ),
        "post_clamp_fk_tolerance_m": 0.0006,
        "evidence_source": controlled_point_ik["evidence_source"],
        "directly_replayed_real_mujoco_phases": [
            "approach",
            "descent",
            "close",
            "lift",
            "transport",
            "lower",
            "release_dwell",
            "vertical_retreat",
            "settle",
        ],
        "directly_measured_physical_outcomes": [
            "opposing_jaw_grasp",
            "lift",
            "supported_placement",
            "release",
            "vertical_retreat",
            "settled_object",
        ],
        "directly_checked_numeric_chain": [
            "effective_interior_clamp_rad",
            "sdk_degree_conversion",
            "runtime_accepted_action_round_trip",
            "exact_fk_selected_open_tool_point",
        ],
        "derived_safety_contract_requirement": controlled_point_ik[
            "derived_safety_contract_requirement"
        ],
    }
    assert controlled_point_ik["evidence_source"] == (
        "tests/test_gripper_contact_profile.py::"
        "test_command_20_profile_places_releases_and_retreats_with_"
        "post_conversion_fk_bound"
    )
    assert "not an upstream manufacturer claim" in controlled_point_ik[
        "derived_safety_contract_requirement"
    ]
    close = baseline["verified_close_command"]
    assert close["status"] == "verified"
    assert close["command"] == 1.1
    assert close["command_unit"] == "normalized_0_100"
    assert close["minimum_normalized_interior_command"] == pytest.approx(
        close["validated_dynamic_margin_rad"]
        * 100.0
        / close["actuator_ctrl_span_rad"]
    )
    assert close["command"] > close["minimum_normalized_interior_command"]

    holding_interpolation = baseline["verified_holding_motion_interpolation"]
    assert holding_interpolation["status"] == "verified"
    assert holding_interpolation["phases"] == ["lift", "transport", "lower"]
    assert holding_interpolation["lift_waypoints"] == 18
    assert holding_interpolation["lower_waypoints"] == 20
    assert holding_interpolation["maximum_transport_waypoint_step_m"] == 0.02
    assert holding_interpolation["omit_current_pose_waypoint"] is True
    assert "real-MuJoCo" in holding_interpolation["evidence_source"]
    assert baseline["lift_waypoints"] == holding_interpolation["lift_waypoints"]
    assert baseline["descent_waypoints"] == holding_interpolation["lower_waypoints"]

    completion = baseline["verified_arm_waypoint_completion_policy"]
    assert completion["status"] == "verified"
    assert completion["global_deadline_is_outer_bound"] is True
    assert completion["waypoint_settle_timeout_s"] == 3.0
    assert completion["poll_period_s"] == 0.02
    assert completion["required_consecutive_polls"] == 2
    assert completion["omit_current_pose_waypoint"] is True
    assert completion["open_or_pre_contact_motion"] == {
        "phases": [
            "approach",
            "descent",
            "vertical_retreat",
            "inter_object_transit",
        ],
        "criterion": "all_five_arm_joint_errors_within_deg",
        "maximum_joint_error_deg": 1.0,
    }
    assert completion["contact_holding_motion"] == {
        "phases": ["lift", "transport", "lower"],
        "criterion": "observed_vs_commanded_arm_fk_translation_error",
        "controlled_point_in_gripperframe_m": [0.0, 0.0, 0.0],
        "maximum_fk_translation_error_m": 0.01,
    }
    assert completion["settle_expiry_result"] == "truthful_phase_timeout"

    phase_commands = release["gripper_phase_command_contract"]
    release_selection = release["release_aperture_selection"]
    assert release_selection["status"] == "verified"
    assert release_selection["object_extent_semantics"] == (
        "maximum_horizontal_extent_m"
    )
    assert release_selection["minimum_total_aperture_clearance_m"] == 0.008
    assert release_selection["independent_from_grasp_aperture_selection"] is True
    assert release_selection["selected_sample_name"] == (
        "selected_release_aperture_sample"
    )
    assert release_selection["command_source"] == (
        "selected_release_aperture_sample.command"
    )
    assert release_selection["tool_center_source"] == (
        "selected_release_aperture_sample.tool_center_in_frame_m"
    )
    assert release_selection["no_qualifying_sample_result"] == "invalid_extent"
    assert phase_commands["holding_motion"] == {
        "phases": ["lift", "transport", "lower"],
        "gripper_command_source": (
            "verified_tabletop_pinch_baseline.verified_close_command.command"
        ),
        "explicit_command_required_each_action": True,
    }
    assert phase_commands["post_release_retreat"] == {
        "phases": ["release_dwell", "vertical_retreat"],
        "gripper_command_source": "selected_release_aperture_sample.command",
        "tool_center_source": (
            "selected_release_aperture_sample.tool_center_in_frame_m"
        ),
        "explicit_command_required_each_action": True,
        "same_selected_release_command_required_every_retreat_tick": True,
        "same_selected_release_tool_center_required_for_retreat_ik": True,
    }
    assert phase_commands["commands_must_be_distinct"] is True

    transit = release["multi_object_safe_transit"]
    assert transit["status"] == "verified"
    assert transit["path_space"] == "cartesian"
    assert transit["maximum_cartesian_waypoint_step_m"] == 0.02
    assert transit["completed_vertical_retreat_required"] is True
    assert transit["single_joint_space_jump_prohibited"] is True

    move_item = release["sequence_move_item_contract"]
    assert move_item == {
        "source_position_field": "source_position_m",
        "target_position_field": "target_position_m",
        "object_extent_field": "object_extent_m",
        "object_extent_semantics": "maximum_horizontal_extent_m",
        "position_components": "xyz",
        "fields_are_literal_keys": True,
        "object_extent_required_per_move": True,
        "hardcoded_default_object_extent_prohibited": True,
    }

    long_motion = limit_policy["long_distance_joint_motion"]
    assert long_motion["single_step_prohibited"] is True
    assert long_motion["bounded_waypoint_interpolation_required"] is True
    assert long_motion["poll_every_waypoint"] is True
    if long_motion["status"] == "required_but_unverified":
        assert "unresolved_gap" in long_motion
        assert "verified_maximum_arm_step_deg" not in long_motion
        assert "verified_maximum_gripper_step_normalized" not in long_motion
    assert release["minimum_vertical_retreat_m"] >= baseline["lift_height_m"]
    assert release["retreat_waypoints"] >= 2


def test_morphology_generation_contract_is_fail_closed() -> None:
    morphology = _yaml(ROOT / "libraries/morphology/soarm101/v1/kinematics.yaml")
    schema = _json(ROOT / "schemas/morphology.schema.json")

    invalid_close = copy.deepcopy(morphology)
    invalid_close["gripper_contact_model"]["verified_tabletop_pinch_baseline"][
        "verified_close_command"
    ]["command"] = 1.0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_close, schema)

    invalid_completion = copy.deepcopy(morphology)
    invalid_completion["gripper_contact_model"]["verified_tabletop_pinch_baseline"][
        "verified_arm_waypoint_completion_policy"
    ]["contact_holding_motion"]["maximum_fk_translation_error_m"] = 0.02
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_completion, schema)

    for field, invalid_value in (
        ("lift_waypoints", 17),
        ("lower_waypoints", 19),
        ("maximum_transport_waypoint_step_m", 0.021),
        ("omit_current_pose_waypoint", False),
    ):
        invalid_holding_interpolation = copy.deepcopy(morphology)
        invalid_holding_interpolation["gripper_contact_model"][
            "verified_tabletop_pinch_baseline"
        ]["verified_holding_motion_interpolation"][field] = invalid_value
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_holding_interpolation, schema)

    invalid_ik_tolerance = copy.deepcopy(morphology)
    invalid_ik_tolerance["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]["tolerance_m"] = 0.0025
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_ik_tolerance, schema)

    missing_post_clamp_fk = copy.deepcopy(morphology)
    del missing_post_clamp_fk["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]["post_clamp_fk_check_required"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_post_clamp_fk, schema)

    looser_post_clamp_fk = copy.deepcopy(morphology)
    looser_post_clamp_fk["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]["post_clamp_fk_tolerance_m"] = 0.001
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(looser_post_clamp_fk, schema)

    incomplete_evidence_scope = copy.deepcopy(morphology)
    incomplete_evidence_scope["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]["directly_replayed_real_mujoco_phases"] = [
        "approach",
        "descent",
        "lift",
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incomplete_evidence_scope, schema)

    wrong_controlled_point = copy.deepcopy(morphology)
    wrong_controlled_point["gripper_contact_model"][
        "verified_tabletop_pinch_baseline"
    ]["verified_controlled_point_ik"]["point_source"] = (
        "closed_tool_center_in_frame_m"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wrong_controlled_point, schema)

    invalid_retreat = copy.deepcopy(morphology)
    invalid_retreat["gripper_contact_model"][
        "tabletop_release_and_sequence_requirements"
    ]["gripper_phase_command_contract"]["post_release_retreat"][
        "same_selected_release_command_required_every_retreat_tick"
    ] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_retreat, schema)

    invalid_transit = copy.deepcopy(morphology)
    invalid_transit["gripper_contact_model"][
        "tabletop_release_and_sequence_requirements"
    ]["multi_object_safe_transit"]["maximum_cartesian_waypoint_step_m"] = 0.021
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_transit, schema)

    missing_extent = copy.deepcopy(morphology)
    del missing_extent["gripper_contact_model"][
        "tabletop_release_and_sequence_requirements"
    ]["sequence_move_item_contract"]["object_extent_field"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_extent, schema)

    fabricated_joint_step = copy.deepcopy(morphology)
    joint_policy = fabricated_joint_step["command_limit_policy"][
        "long_distance_joint_motion"
    ]
    if joint_policy["status"] == "required_but_unverified":
        joint_policy["verified_maximum_arm_step_deg"] = 5.0
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(fabricated_joint_step, schema)


def test_stage2_and_repair_prompts_carry_verified_motion_contract() -> None:
    stage2 = (ROOT / "prompts/stage2.md").read_text(encoding="utf-8")
    repair = (ROOT / "prompts/repair.md").read_text(encoding="utf-8")

    for prompt in (stage2, repair):
        compact = " ".join(prompt.split())
        assert "absolute deadline" in prompt
        assert "1.041742627066557" in prompt
        assert "close command is 1.1" in prompt
        assert "object_extent_m" in prompt
        assert "0.02 m" in prompt or "0.02-m" in prompt
        assert "joint-space" in prompt
        assert "required_but_unverified" in prompt
        assert "verified_arm_waypoint_completion_policy" in prompt
        assert "3.0 seconds" in prompt
        assert "two consecutive" in prompt
        assert "0.01 m" in prompt
        assert "verified_controlled_point_ik.tolerance_m" in compact
        assert "0.0006 m" in compact
        assert "0.0025 m" in compact
        assert "must not be transferred from G2 to G3" in compact
        assert "exact commanded arm values" in compact
        assert "one bounded control-period sleep" in compact
        assert "contact-blocked measured gripper" in compact
        assert "Never impose the 3.0-second arm-waypoint settle limit" in compact
        assert "only the bounded close dwell" in compact
        assert "only one" in compact and "placement coordinate" in compact
        assert "preserve the literal" in compact
        assert "`(target_x, target_y, contact_z)`" in compact
        assert "`contact_z + tool_center_z`" in compact
        assert "`contact_z - tool_center_z`" in compact
        assert "0.008 m total" in compact
        assert "release sample" in compact
        assert "grasp-open sample" in compact
        assert "verified_holding_motion_interpolation" in compact
        assert "exactly 18" in compact
        assert "exactly 20" in compact
        assert "0.02 m" in compact
        assert "N=ceil(cartesian_distance/0.02)" in compact
        assert "current/t=0 pose is never sent" in compact
        assert "longer than 0.02 m must never be sent as one direct target" in compact
        assert "single distant joint target" in compact.lower()

    assert "run FK again on the exact commanded arm values" in stage2
    assert "every `object_move_sequence`" in stage2
    assert "3-number xyz JSON array" in stage2
    assert "append_generated_file_chunks" in stage2
    assert "two or three consecutive source" in stage2
    assert "at or below 9000 characters" in stage2
    assert "JSON escaping and the ReAct envelope" in " ".join(stage2.split())
    assert "provider-truncated response is never executed" in stage2
    assert "recompute FK on the exact commanded values" in repair
    assert "append_generated_file_chunks" in repair
    assert "at most 9000 characters per batch" in repair
    assert "read_generated_python_symbol" in stage2
    assert "only three model calls remain" in stage2
    assert "Never open a whole-file transaction this late" in stage2
    assert "normalized gripperframe site quaternion" in stage2
    assert "every nonzero tool point" in stage2
    assert "passing gripperframe-origin G2 case" in stage2
    assert "exactly one of `symbol`" in stage2
    assert "integer `context_lines` from 0 through 6" in stage2
    assert "before changing polling or timeout code" in stage2
    assert "failure.capability_id" in repair
    assert "read_generated_python_symbol" in repair
    assert "do not spend another call guessing" in repair
    assert "no_package_change" in repair
    assert "most recent real" in repair
    assert "object_source_plus_height_delta" in repair
    assert "source.z + height_delta" in repair
    assert "Do not add a pick/place baseline lift" in repair
    assert "normalized gripperframe site quaternion" in repair
    for prompt in (stage2, repair):
        assert "_quaternion_matrix_wxyz(_GRIPPERFRAME_QUATERNION_WXYZ)" in prompt
        assert "_matmul3(arm_rotation, site_quaternion_rotation)" in prompt
        assert "_matvec3(composed_site_rotation, tool_point_in_gripperframe_m)" in prompt
        assert "_quaternion_to_matrix" in prompt
    assert "all G3 cases using nonzero `tool_center_in_frame_m`" in repair
    assert "exact symbol\n`_forward_kinematics`" in repair
    assert "before inspecting or changing polling" in repair
    assert "Prioritize the\nedit/finish sequence" in repair
    assert "`finish_package` already runs the\nauthoritative syntax" in repair
    assert "between 0 and 6\ninclusive" in repair
    assert "exactly one locator" in repair
    assert "do not invent helper\nnames" in repair
    assert "exactly one JSON ReAct action per response" in repair


def test_generation_and_demo_prompts_freeze_nested_sequence_and_container_contracts() -> None:
    stage1 = (ROOT / "prompts/stage1.md").read_text(encoding="utf-8")
    stage2 = (ROOT / "prompts/stage2.md").read_text(encoding="utf-8")
    demo = (ROOT / "prompts/demo_react.md").read_text(encoding="utf-8")

    assert "conceptual item contract" in stage1
    assert "must not invent the\nconcrete public parameter or item-key names" in stage1
    assert "Every public array annotation must include its item type" in stage2
    assert "parameters.properties.<moves>.items" in stage2

    assert "catalog's `parameters` value as the only call contract" in demo
    assert "properties.<moves>.items.properties" in demo
    assert "Never flatten nested move fields" in demo
    assert '"source_position_m"' in demo
    assert '"target_position_m"' in demo
    assert '"object_extent_m"' in demo
    assert "illustrative schema values, not task facts" in demo
    assert "maximum *full* horizontal width in the grasp plane" in demo
    assert "max(size_x, size_y)" in demo
    assert "2 * radius" in demo
    assert "It is\nnever a radius" in demo

    assert "safe release\nobject-center height from which the object will settle" in demo
    assert "not the final\nsettled support-center height" in demo
    assert "current tabletop tray/bowl representation" in demo
    assert (
        "receptacle center/support z + rim_height + object half-height" in demo
    )
    assert "Do not pass\n`center_z` directly" in demo
    assert "do not use `rim_height` itself as an object-center z" in demo
    assert '`z_reference: "support_surface"`' in demo
    assert "region center z + object vertical half-height" in demo
    assert "Never discard the region z" in demo
    assert "bottomless" not in demo
    assert "desired object-center support height" not in demo

    assert "complete object\nfootprint, plus a safety margin" in demo
    assert "full footprints do not overlap" in demo
    assert "gripper approach/release clearance" in demo
    assert "one axis through the receptacle center" in demo
    assert "smallest symmetric center-line offsets" in demo
    assert "blind retry" in demo
    assert "new observable evidence" in demo


def test_validation_prompts_require_complementary_g3_shape_coverage() -> None:
    prompts = [
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "prompts/validation_generate.md",
            "prompts/validation_review.md",
            "prompts/validation_final_review.md",
        )
    ]
    for prompt in prompts:
        compact = " ".join(prompt.split())
        assert "pick_place" in compact
        assert "vertical cylinder" in compact
        assert "lift" in compact and "cube" in compact
        assert "ordered" in compact
        assert "one cube" in compact and "one vertical cylinder" in compact


def test_validation_prompts_require_deterministic_error_repair_and_g1_key_equality() -> None:
    prompts = [
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "prompts/validation_generate.md",
            "prompts/validation_review.md",
            "prompts/validation_final_review.md",
        )
    ]
    for prompt in prompts:
        compact = " ".join(prompt.split())
        assert "deterministic_check.errors" in compact
        assert "previous_candidate" in compact
        assert "unchanged" in compact and "forbidden" in compact
        assert "character-for-character" in compact
        assert 'call_arguments["joint_targets"]' in compact
        assert 'target_measurements["joint_positions"]' in compact
        assert ".pos" in compact
        assert 'shoulder_pan.pos":15.0' in compact
        assert 'shoulder_pan":15.0' in compact
        assert "values" in compact and "equal" in compact


def test_validation_prompts_keep_public_and_harness_timeout_clocks_separate() -> None:
    prompts = [
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "prompts/validation_generate.md",
            "prompts/validation_review.md",
            "prompts/validation_final_review.md",
        )
    ]
    for prompt in prompts:
        compact = " ".join(prompt.split())
        compact_lower = compact.lower()
        assert "host-monotonic" in compact or "host monotonic" in compact
        assert "MuJoCo simulation time" in compact
        assert "independent" in compact
        assert "optional defaulted public" in compact or "declares a default" in compact
        assert "omit" in compact_lower and "call_arguments" in compact


def test_no_change_feedback_schema_preserves_causal_validation_evidence() -> None:
    schema = _json(ROOT / "schemas/failure_feedback.schema.json")
    carried = {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": "direct_function",
        "repair_round": 3,
        "failures": [
            {
                "code": "DIRECT_ORACLE_MISMATCH",
                "message": "object center is above the bound target",
                "capability_id": "G3.lift_object",
                "case_id": "G3.lift_object.direct",
                "observed": {"object_z_m": 0.208},
                "target": {"object_z_m": 0.12},
                "gap": {"absolute_error_m": 0.088},
                "execution": {
                    "returned_result": {
                        "status": "success",
                        "phase_reached": "complete",
                    },
                    "returned_status": "success",
                    "reported_phase": "complete",
                },
                "traceback": None,
            }
        ],
        "no_package_change": {
            "code": "NO_PACKAGE_CHANGE",
            "completed_repair_round": 2,
            "consecutive_rounds": 1,
            "causal_feedback_repair_round": 2,
            "package_sha256": HASH,
            "retry_available": True,
        },
    }

    jsonschema.validate(carried, schema)

    generic_stage = copy.deepcopy(carried)
    generic_stage["stage"] = "generation_repair"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(generic_stage, schema)

    replaced_cause = copy.deepcopy(carried)
    replaced_cause["failures"] = [
        {
            "code": "NO_PACKAGE_CHANGE",
            "message": "generic gate result",
            "observed": HASH,
            "target": "different hash",
        }
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(replaced_cause, schema)

    missing_observation = copy.deepcopy(carried)
    del missing_observation["failures"][0]["observed"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_observation, schema)


def test_all_twelve_task_templates_validate() -> None:
    schema = _json(ROOT / "schemas/task.schema.json")
    paths = [
        ROOT / "libraries/tasks/soarm101_tabletop/v1/visible_tasks.jsonl",
        ROOT / "private/task_library/soarm101_tabletop/v1/heldout_tasks.jsonl",
    ]
    count = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                jsonschema.validate(json.loads(line), schema)
                count += 1
    assert count == 12


def test_fixture_generation_and_validation_artifacts_validate() -> None:
    stage1 = _json(ROOT / "fixtures/stage1_capabilities.json")
    jsonschema.validate(stage1, _json(ROOT / "schemas/stage1.schema.json"))
    manifest = _json(ROOT / "fixtures/generated_pass/package_manifest.json")
    jsonschema.validate(manifest, _json(ROOT / "schemas/capability_manifest.schema.json"))
    case_schema = _json(ROOT / "schemas/validation_case.schema.json")
    for case in _json(ROOT / "fixtures/validation_suite.json")["cases"]:
        jsonschema.validate(case, case_schema)


def test_tool_catalog_schema_requires_structured_items_for_every_array() -> None:
    schema = _json(ROOT / "schemas/tool_catalog.schema.json")
    catalog = {
        "schema_version": "robot_capability.tool_catalog.v1",
        "catalog_id": "soarm101_combined_validated",
        "package_sha256": HASH,
        "tools": [
            {
                "name": "place_objects_sequence",
                "description": "Execute exact ordered move records.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "moves": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "source_position_m": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "target_position_m": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "object_extent_m": {
                                        "type": "number",
                                        "exclusiveMinimum": 0.0,
                                    },
                                },
                                "required": [
                                    "source_position_m",
                                    "target_position_m",
                                    "object_extent_m",
                                ],
                            },
                        }
                    },
                    "required": ["moves"],
                },
                "capability_id": "G3.place_objects_sequence",
                "binding": "g3.place_objects_sequence",
            }
        ],
    }

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(catalog, schema)

    missing_top_level_items = copy.deepcopy(catalog)
    del missing_top_level_items["tools"][0]["parameters"]["properties"]["moves"][
        "items"
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_top_level_items, schema)

    missing_coordinate_items = copy.deepcopy(catalog)
    del missing_coordinate_items["tools"][0]["parameters"]["properties"]["moves"][
        "items"
    ]["properties"]["source_position_m"]["items"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_coordinate_items, schema)


def test_move_sequence_manifest_requires_extent_field_and_semantics() -> None:
    schema = _json(ROOT / "schemas/capability_manifest.schema.json")
    manifest = _json(ROOT / "fixtures/generated_pass/package_manifest.json")
    sequence_api = next(
        item
        for item in manifest["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )

    for missing in ("object_extent_field", "object_extent_semantics"):
        invalid = copy.deepcopy(manifest)
        invalid_sequence_api = next(
            item
            for item in invalid["capabilities"]
            if item["validation_binding"]["effect"] == "object_move_sequence"
        )
        del invalid_sequence_api["validation_binding"][missing]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)

    assert sequence_api["validation_binding"]["object_extent_field"] == (
        "object_extent_m"
    )
    assert sequence_api["validation_binding"]["object_extent_semantics"] == (
        "maximum_horizontal_extent_m"
    )

    wrong_extent_semantics = copy.deepcopy(manifest)
    wrong_sequence_api = next(
        item
        for item in wrong_extent_semantics["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )
    wrong_sequence_api["validation_binding"]["object_extent_semantics"] = (
        "cylinder_diameter_m"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wrong_extent_semantics, schema)


def test_manifest_never_relabels_approach_offset_as_object_lift_goal() -> None:
    manifest = _json(ROOT / "fixtures/generated_pass/package_manifest.json")
    capability = next(
        item for item in manifest["capabilities"] if item["granularity"] == "G3"
    )
    capability["signature"]["parameters"].append(
        {
            "name": "approach_height_offset_m",
            "annotation": "float",
            "has_default": False,
        }
    )
    capability["validation_binding"] = {
        "effect": "object_source_plus_height_delta",
        "source_argument": "object_position_m",
        "height_delta_argument": "approach_height_offset_m",
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            manifest,
            _json(ROOT / "schemas/capability_manifest.schema.json"),
        )


def test_experience_schema_mirror_matches_canonical_contract_byte_for_byte() -> None:
    canonical = ROOT / "schemas" / "experience.schema.json"
    mirror = ROOT / "libraries" / "experience" / "v1" / "experience.schema.json"

    assert canonical.read_bytes() == mirror.read_bytes()


def test_stage1_runtime_validator_matches_structural_schema(
    stage1_artifact: dict[str, Any],
) -> None:
    schema = _json(ROOT / "schemas/stage1.schema.json")
    jsonschema.validate(stage1_artifact, schema)
    assert validate_stage1(stage1_artifact) == stage1_artifact

    def extra_root(value: dict[str, Any]) -> None:
        value["implementation_hint"] = "must not enter Stage 1"

    def extra_layer(value: dict[str, Any]) -> None:
        value["layers"]["G1"]["parameters"] = []

    def extra_capability(value: dict[str, Any]) -> None:
        value["layers"]["G2"]["capabilities"][0]["return_type"] = "dict"

    def wrong_layer(value: dict[str, Any]) -> None:
        value["layers"]["G1"]["capabilities"][0]["capability_id"] = "G2.command_joint"

    def keyword_function(value: dict[str, Any]) -> None:
        value["layers"]["G3"]["capabilities"][0]["function_name"] = "return"

    def observation_helper(value: dict[str, Any]) -> None:
        value["layers"]["G2"]["capabilities"][0]["function_name"] = "get_pose"

    def too_many_g1_capabilities(value: dict[str, Any]) -> None:
        original = value["layers"]["G1"]["capabilities"][0]
        for index in (2, 3):
            extra = copy.deepcopy(original)
            extra["capability_id"] = f"G1.extra_{index}"
            extra["function_name"] = f"extra_{index}"
            value["layers"]["G1"]["capabilities"].append(extra)

    mutations: list[Callable[[dict[str, Any]], None]] = [
        extra_root,
        extra_layer,
        extra_capability,
        wrong_layer,
        keyword_function,
        observation_helper,
        too_many_g1_capabilities,
    ]
    for mutate in mutations:
        invalid = copy.deepcopy(stage1_artifact)
        mutate(invalid)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)
        with pytest.raises(ArtifactSchemaError):
            validate_stage1(invalid)


def test_report_v2_schemas_accept_complete_offline_and_aws_contracts() -> None:
    sealed_schema = _json(ROOT / "schemas/sealed_run_report.schema.json")
    terminal_schema = _json(ROOT / "schemas/terminal_run_report.schema.json")

    jsonschema.Draft202012Validator.check_schema(sealed_schema)
    jsonschema.Draft202012Validator.check_schema(terminal_schema)
    jsonschema.validate(_valid_sealed_report(mode="offline"), sealed_schema)
    jsonschema.validate(_valid_sealed_report(mode="aws"), sealed_schema)
    jsonschema.validate(_valid_terminal_report(mode="offline"), terminal_schema)
    jsonschema.validate(_valid_terminal_report(mode="aws"), terminal_schema)


def test_terminal_v2_accepts_explicit_demo_failure_state() -> None:
    report = _valid_terminal_report(mode="aws")
    report["terminal_state"] = "DEMO_FAILED"
    report["terminal_reason"] = "demo_oracle_success_gate_failed"

    jsonschema.validate(
        report,
        _json(ROOT / "schemas/terminal_run_report.schema.json"),
    )


def test_video_index_schema_requires_direct_demo_package_and_file_bindings() -> None:
    schema = _json(ROOT / "schemas/video_index.schema.json")
    value = _video_index(required=True)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(value, schema)

    for field in ("package_sha256", "demo_freeze", "demo_report"):
        invalid = copy.deepcopy(value)
        invalid["demo"][0].pop(field)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def test_video_index_schema_accepts_and_validates_resolver_scene_bindings() -> None:
    schema = _json(ROOT / "schemas/video_index.schema.json")
    value = _video_index(required=True)
    binding = {
        "binding_sha256": HASH,
        "source_scene_request_sha256": HASH,
        "scene_catalog": {
            "catalog_id": "soarm101_tabletop_primitives",
            "version": "1.0.0",
            "source_sha256": HASH,
            "content_sha256": HASH,
        },
    }
    for record in [*value["validation"], *value["demo"]]:
        record.update(
            {
                "resolved_scene_binding": copy.deepcopy(binding),
                "generation_environment_freeze_sha256": HASH,
                "generation_environment_manifest_sha256": HASH,
            }
        )
    jsonschema.validate(value, schema)

    malformed = copy.deepcopy(value)
    malformed["validation"][0]["resolved_scene_binding"][
        "source_scene_request_sha256"
    ] = "not-a-hash"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed, schema)

    incomplete = copy.deepcopy(value)
    incomplete["demo"][0].pop("generation_environment_manifest_sha256")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incomplete, schema)


def test_sealed_report_schema_accepts_bound_scene_video_records() -> None:
    schema = _json(ROOT / "schemas/sealed_run_report.schema.json")
    report = _valid_sealed_report(mode="aws")
    binding = {
        "binding_sha256": HASH,
        "source_scene_request_sha256": HASH,
        "scene_catalog": {
            "catalog_id": "soarm101_tabletop_primitives",
            "version": "1.0.0",
            "source_sha256": HASH,
            "content_sha256": HASH,
        },
    }
    for record in [
        *report["video_recording"]["validation"],
        *report["video_recording"]["demo"],
    ]:
        record.update(
            {
                "resolved_scene_binding": copy.deepcopy(binding),
                "generation_environment_freeze_sha256": HASH,
                "generation_environment_manifest_sha256": HASH,
            }
        )

    jsonschema.validate(report, schema)

    incomplete = copy.deepcopy(report)
    incomplete["video_recording"]["demo"][0].pop(
        "generation_environment_manifest_sha256"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incomplete, schema)


def test_terminal_report_schema_accepts_bound_scene_video_records() -> None:
    schema = _json(ROOT / "schemas/terminal_run_report.schema.json")
    report = _valid_terminal_report(mode="aws")
    report["video_recording"] = _video_index(required=True)
    report["artifact_hashes"]["video_index"] = HASH
    report["evidence_files"] = [
        _file_evidence("video_index.json", nonempty=True)
    ]
    binding = {
        "binding_sha256": HASH,
        "source_scene_request_sha256": HASH,
        "scene_catalog": {
            "catalog_id": "soarm101_tabletop_primitives",
            "version": "1.0.0",
            "source_sha256": HASH,
            "content_sha256": HASH,
        },
    }
    for record in [
        *report["video_recording"]["validation"],
        *report["video_recording"]["demo"],
    ]:
        record.update(
            {
                "resolved_scene_binding": copy.deepcopy(binding),
                "generation_environment_freeze_sha256": HASH,
                "generation_environment_manifest_sha256": HASH,
            }
        )

    jsonschema.validate(report, schema)


def test_report_v2_rejects_legacy_v1_discriminator() -> None:
    sealed = _valid_sealed_report()
    sealed["schema_version"] = "robot_capability.sealed_run_report.v1"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            sealed, _json(ROOT / "schemas/sealed_run_report.schema.json")
        )

    terminal = _valid_terminal_report()
    terminal["schema_version"] = "robot_capability.terminal_run_report.v1"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            terminal, _json(ROOT / "schemas/terminal_run_report.schema.json")
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["budgets"]["generation_stage1"].update(limit=4),
        lambda value: value["budgets"]["generation_stage2_initial"].update(
            limit=31
        ),
        lambda value: value["budgets"]["validation_suite"].update(limit=4),
        lambda value: value["budgets"]["demo"].update(limit_per_task=31),
        lambda value: value["budgets"]["demo"]["per_task"][0].update(limit=31),
        lambda value: value["budgets"]["repair"].update(round_limit=11),
        lambda value: value["budgets"]["repair"].update(
            model_call_limit_per_round=7
        ),
    ],
)
def test_sealed_v2_rejects_wrong_hard_budget_contract(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    report = _valid_sealed_report()
    mutation(report)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            report, _json(ROOT / "schemas/sealed_run_report.schema.json")
        )


def test_sealed_v2_repair_results_have_independent_six_call_budgets() -> None:
    report = _valid_sealed_report()
    result = _repair_result(1, used=2)
    report["budgets"]["repair"] = _repair_budget([result])
    report["repair"] = {
        **_repair_budget([result]),
        "history": [{"repair_round": 1}],
    }
    report["repairs"] = [{"repair_round": 1}]
    report["model_accounting"]["generation_phases"]["repair_rounds"] = [
        copy.deepcopy(result)
    ]
    schema = _json(ROOT / "schemas/sealed_run_report.schema.json")
    jsonschema.validate(report, schema)

    report["repair"]["results"][0]["agent_turn_budget"]["limit"] = 7
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["video_recording"].update(
            renderer_kind="fake.Renderer"
        ),
        lambda value: value["video_recording"].update(fps=-1),
        lambda value: value["video_recording"].update(width=320),
        lambda value: value["video_recording"]["validation"][0]["video"].update(
            sha256="not-a-hash"
        ),
        lambda value: value["video_recording"]["validation"][0].update(
            phase="demo"
        ),
        lambda value: value["video_recording"]["demo"].pop(),
        lambda value: value["video_recording"]["demo"][0].pop(
            "package_sha256"
        ),
        lambda value: value["video_recording"]["demo"][0].pop("demo_freeze"),
        lambda value: value["demo"]["tasks"][0].update(passed="false"),
        lambda value: value["demo"]["summary"]["overall"].update(
            success_rate=1.5
        ),
    ],
)
def test_sealed_v2_rejects_malformed_embedded_video_index(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    report = _valid_sealed_report(mode="aws")
    mutation(report)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            report, _json(ROOT / "schemas/sealed_run_report.schema.json")
        )


def test_sealed_v2_accepts_consistent_demo_performance_miss() -> None:
    report = _valid_sealed_report()
    report["demo"]["tasks"][0]["passed"] = False
    report["demo"]["tasks"][0]["oracle"]["passed"] = False
    report["demo"]["summary"]["visible"].update(
        passed=2,
        success_rate=2 / 3,
    )
    report["demo"]["summary"]["overall"].update(
        passed=5,
        success_rate=5 / 6,
    )

    jsonschema.validate(
        report,
        _json(ROOT / "schemas/sealed_run_report.schema.json"),
    )


def test_sealed_v2_requires_index_and_mp4_entries_in_video_evidence() -> None:
    schema = _json(ROOT / "schemas/sealed_run_report.schema.json")
    report = _valid_sealed_report(mode="aws")
    report["evidence"]["videos"] = [
        item
        for item in report["evidence"]["videos"]
        if item["path"] != "video_index.json"
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)

    report = _valid_sealed_report(mode="aws")
    first_mp4 = next(
        item
        for item in report["evidence"]["videos"]
        if item["path"].endswith(".mp4")
    )
    report["evidence"]["videos"].remove(first_mp4)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_terminal_v2_requires_structured_video_status() -> None:
    schema = _json(ROOT / "schemas/terminal_run_report.schema.json")
    report = _valid_terminal_report(mode="aws")
    del report["video_recording"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)

    report = _valid_terminal_report(mode="aws")
    report["video_recording"]["reason_code"] = "raw provider body is forbidden"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_terminal_v2_accepts_partial_video_index_and_binds_index_artifact_key() -> None:
    schema = _json(ROOT / "schemas/terminal_run_report.schema.json")
    report = _valid_terminal_report(mode="aws")
    report["video_recording"] = _video_index(required=True, status="partial")
    report["video_recording"]["demo"] = [
        _video_record("demo", "partially-recorded-task")
    ]
    report["artifact_hashes"]["video_index"] = HASH
    report["evidence_files"].append(
        _file_evidence("video_index.json", nonempty=True)
    )
    jsonschema.validate(report, schema)

    invalid_binding = copy.deepcopy(report)
    invalid_binding["video_recording"]["demo"][0].pop("demo_report")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid_binding, schema)

    del report["artifact_hashes"]["video_index"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)

    report = _valid_terminal_report(mode="aws")
    report["video_recording"] = _video_index(required=True, status="partial")
    report["artifact_hashes"]["video_index"] = HASH
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_terminal_v2_rejects_wrong_present_budget_contract() -> None:
    schema = _json(ROOT / "schemas/terminal_run_report.schema.json")
    report = _valid_terminal_report()
    report["budgets"]["generation_stage2_initial"] = _budget(
        "generation_stage2_initial", 31, 1
    )

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)
