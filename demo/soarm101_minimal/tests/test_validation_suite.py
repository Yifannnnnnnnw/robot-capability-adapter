from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from soarm_demo.model_client import ScriptedModelClient
from soarm_demo.audit import sha256_json
from soarm_demo.validation_suite import (
    _public_api_sha256,
    SUITE_SCHEMA_VERSION,
    SUITE_FREEZE_SCHEMA_VERSION,
    TIMEOUT_CONTRACT,
    ValidationSuiteError,
    ValidationSuiteGenerator,
    parse_json_document,
    validate_suite,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _physical_reference() -> dict[str, Any]:
    import yaml

    value = yaml.safe_load(
        (ROOT / "fixtures/validation_reference/tabletop_primitives.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


def _strict_physical_suite() -> dict[str, Any]:
    suite = _load_json("fixtures/validation_suite.json")
    required_safety = {
        "g1": {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "simulation_nan_or_instability",
        },
        "g2": {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "non_gripper_robot_table_collision",
            "simulation_nan_or_instability",
        },
        "g3": {
            "action_clipped",
            "actuator_or_joint_limit_exceeded",
            "non_gripper_robot_table_collision",
            "object_outside_table_support_polygon",
            "simulation_nan_or_instability",
        },
    }
    for case in suite["cases"]:
        initial_state = case["initial_state"]
        if "qpos" in initial_state:
            initial_state["qpos_rad"] = initial_state.pop("qpos")
        case["target_measurements"]["finite"] = True
        case["forbidden_conditions"] = sorted(required_safety[case["module"]])
    return suite


def _catalog_physical_reference() -> dict[str, Any]:
    import yaml

    reference = _physical_reference()
    catalog = yaml.safe_load(
        (
            ROOT
            / "libraries/morphology/scenes/soarm101_tabletop/v1/assets/primitive_catalog.yaml"
        ).read_text(encoding="utf-8")
    )
    assert isinstance(catalog, dict)
    reference.update(
        {
            "require_scene_asset_refs": True,
            "scene_asset_catalog": catalog,
        }
    )
    return reference


def _compact_catalog_physical_suite() -> dict[str, Any]:
    suite = _strict_physical_suite()
    refs = {
        "test_cube": "morphology.scene_asset/cube_34mm_25g@1.0.0#red",
        "test_cylinder": (
            "morphology.scene_asset/cylinder_r15_h40_22g@1.0.0#orange"
        ),
        "cube_a": "morphology.scene_asset/cube_30mm_20g@1.0.0#red",
        "cylinder_b": (
            "morphology.scene_asset/cylinder_r15_h40_22g@1.0.0#blue"
        ),
    }
    for case in suite["cases"]:
        for body in case["initial_state"].get("bodies", []):
            identifier = body["id"]
            compact = {
                "asset_ref": refs[identifier],
                "id": identifier,
                "position_m": copy.deepcopy(body["position_m"]),
                "quaternion_wxyz": copy.deepcopy(
                    body.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0])
                ),
            }
            body.clear()
            body.update(compact)
    return suite


def _physical_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_json("fixtures/stage1_capabilities.json"),
        _load_json("fixtures/generated_pass/package_manifest.json"),
        _load_json("schemas/validation_case.schema.json"),
    )


def _case(capability: dict[str, Any], value: float, *, suffix: str = "base") -> dict[str, Any]:
    public_parameters = [
        parameter["name"]
        for parameter in capability["signature"]["parameters"]
        if parameter["name"] != "runtime"
    ]
    return {
        "case_id": f"{capability['capability_id']}.{suffix}",
        "capability_id": capability["capability_id"],
        "module": capability["module"],
        "function_name": capability["function_name"],
        "initial_state": {"seed": 1},
        "call_arguments": {name: value for name in public_parameters},
        "target_measurements": {"position": value},
        "tolerances": {"position": 0.01},
        "forbidden_conditions": ["collision"],
        "timeout_s": 1.0,
        "test_entrypoint": "soarm_demo.direct_validation:execute_case",
        "reference_provenance": ["fixture://validation-reference"],
    }


def _suite(manifest: dict[str, Any], *, omit_last: bool = False) -> dict[str, Any]:
    capabilities = manifest["capabilities"][:-1] if omit_last else manifest["capabilities"]
    return {
        "schema_version": SUITE_SCHEMA_VERSION,
        "cases": [_case(item, index / 10) for index, item in enumerate(capabilities, start=1)],
    }


def test_generator_always_runs_generate_review_and_final_review_calls(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    first = _suite(public_api_manifest, omit_last=True)
    second = _suite(public_api_manifest)
    final = _suite(public_api_manifest)
    final["cases"][0]["tolerances"]["position"] = 0.005
    client = ScriptedModelClient([json.dumps(first), json.dumps(second), json.dumps(final)])
    generator = ValidationSuiteGenerator(
        client=client,
        prompts=("generate", "review and rewrite", "final review and rewrite"),
        output_dir=tmp_path / "suite",
        trace_path=tmp_path / "suite_trace.jsonl",
        call_limit=3,
    )

    frozen = generator.generate(
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        reference_library={"allowed": "private reference facts"},
        case_schema={"type": "object"},
    )

    assert frozen.model_calls == 3
    assert len(client.requests) == 3
    assert frozen.suite == final
    assert frozen.output_path.is_file()
    assert frozen.generated_test_path.is_file()
    compile(frozen.generated_test_path.read_text(encoding="utf-8"), str(frozen.generated_test_path), "exec")
    assert sorted(path.name for path in (tmp_path / "suite").glob("call_*_candidate.json")) == [
        "call_01_candidate.json",
        "call_02_candidate.json",
        "call_03_candidate.json",
    ]
    freeze = json.loads((tmp_path / "suite/freeze.json").read_text(encoding="utf-8"))
    assert freeze["stage1_sha256"] == sha256_json(stage1_artifact)
    assert freeze["public_api_sha256"] == _public_api_sha256(public_api_manifest)
    assert freeze["physical_policy_enforced"] is False
    assert len(freeze["model_configuration_sha256"]) == 64
    assert freeze["schema_version"] == SUITE_FREEZE_SCHEMA_VERSION
    assert freeze["timeout_contract"] == TIMEOUT_CONTRACT

    second_request = json.loads(client.requests[1][-1].content)
    third_request = json.loads(client.requests[2][-1].content)
    assert second_request["purpose"] == "review_and_rewrite"
    assert second_request["previous_candidate"] == first
    assert second_request["deterministic_check"]["ok"] is False
    assert third_request["purpose"] == "final_review_and_rewrite"
    assert third_request["previous_candidate"] == second
    assert third_request["deterministic_check"]["ok"] is True


def test_generator_feeds_executable_preflight_errors_into_next_review(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    candidate = _suite(public_api_manifest)
    client = ScriptedModelClient([json.dumps(candidate)] * 3)
    calls = 0

    def preflight(_: Mapping[str, Any]) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("baseline target is already satisfied",) if calls < 3 else ()

    generator = ValidationSuiteGenerator(
        client=client,
        prompts=("generate", "review", "final"),
        output_dir=tmp_path / "suite",
        trace_path=tmp_path / "trace.jsonl",
    )

    generator.generate(
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        reference_library={},
        case_schema={},
        candidate_preflight=preflight,
    )

    assert calls == 3
    second_request = json.loads(client.requests[1][-1].content)
    assert second_request["deterministic_check"]["errors"] == [
        "baseline target is already satisfied"
    ]
    freeze = json.loads((tmp_path / "suite/freeze.json").read_text(encoding="utf-8"))
    assert freeze["executable_preflight_enforced"] is True


def test_suite_check_rejects_missing_coverage_and_runtime_argument(
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    suite = _suite(public_api_manifest, omit_last=True)
    suite["cases"][0]["call_arguments"]["runtime"] = "must-never-be-model-supplied"

    check = validate_suite(suite, stage1_artifact, public_api_manifest)

    assert check.ok is False
    error_text = "\n".join(check.errors)
    assert "must not expose injected runtime" in error_text
    assert "unknown call arguments: ['runtime']" in error_text
    assert "suite does not cover Stage 1 capabilities" in error_text
    assert "G3.pick_and_place" in error_text


def test_suite_check_accepts_one_direct_case_per_stage1_capability(
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    check = validate_suite(
        _suite(public_api_manifest),
        stage1_artifact,
        public_api_manifest,
    )

    assert check.ok is True
    assert check.errors == ()
    assert check.covered_capabilities == (
        "G1.command_joint",
        "G2.move_to_pose",
        "G3.pick_and_place",
    )


def test_physical_policy_accepts_typed_nontrivial_soarm_suite() -> None:
    stage1, manifest, case_schema = _physical_inputs()

    check = validate_suite(
        _strict_physical_suite(),
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, "\n".join(check.errors)


def test_formal_physical_policy_resolves_compact_catalog_refs_without_mutation() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _compact_catalog_physical_suite()
    before = copy.deepcopy(suite)

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_catalog_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, "\n".join(check.errors)
    assert suite == before
    assert all(
        set(body) <= {"asset_ref", "id", "position_m", "quaternion_wxyz"}
        for case in suite["cases"]
        for body in case["initial_state"].get("bodies", [])
    )


def test_formal_physical_policy_rejects_inline_physics_and_unknown_asset_ref() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    reference = _catalog_physical_reference()
    inline = _compact_catalog_physical_suite()
    inline["cases"][2]["initial_state"]["bodies"][0]["mass_kg"] = 99.0

    inline_check = validate_suite(
        inline,
        stage1,
        manifest,
        reference_library=reference,
        case_schema=case_schema,
    )
    assert inline_check.ok is False
    assert "unsafe instance override fields" in "\n".join(inline_check.errors)

    unknown = _compact_catalog_physical_suite()
    unknown["cases"][2]["initial_state"]["bodies"][0]["asset_ref"] = (
        "morphology.scene_asset/not_in_catalog@1.0.0#red"
    )
    unknown_check = validate_suite(
        unknown,
        stage1,
        manifest,
        reference_library=reference,
        case_schema=case_schema,
    )
    assert unknown_check.ok is False
    assert "unknown asset" in "\n".join(unknown_check.errors)


def test_physical_policy_rejects_duplicate_cube_only_pick_place_coverage() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    case = next(
        item
        for item in suite["cases"]
        if item["capability_id"] == "G3.pick_and_place"
    )
    body = case["initial_state"]["bodies"][0]
    body["kind"] = "cube"
    body.pop("radius_m", None)
    body.pop("height_m", None)
    body["size_m"] = [0.03, 0.03, 0.04]

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "pick_place validation must use exactly one vertical cylinder" in "\n".join(
        check.errors
    )


def test_move_sequence_checks_each_authored_object_extent() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    case = next(
        item
        for item in suite["cases"]
        if item["capability_id"] == "G3.place_objects"
    )
    case["initial_state"]["bodies"][0]["size_m"] = [0.02, 0.04, 0.03]
    case["call_arguments"]["moves"][0]["object_extent_m"] = 0.04

    accepted = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )
    assert accepted.ok is True, "\n".join(accepted.errors)

    case["call_arguments"]["moves"][1]["object_extent_m"] = 0.04
    rejected = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )
    assert rejected.ok is False
    assert (
        "bound move extent for 'cylinder_b' does not match the authored "
        "initial-state geometry"
    ) in "\n".join(rejected.errors)


def test_physical_policy_rejects_dynamic_move_field_selector_contract() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    sequence_api = next(
        item
        for item in manifest["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )
    sequence_api["signature"]["parameters"][2:2] = [
        {"name": "source_field", "annotation": "str", "has_default": False},
        {"name": "target_field", "annotation": "str", "has_default": False},
    ]
    sequence_api["validation_binding"]["source_field"] = "source_field"
    sequence_api["validation_binding"]["target_field"] = "target_field"
    sequence_case = next(
        item
        for item in suite["cases"]
        if item["capability_id"] == sequence_api["capability_id"]
    )
    for move in sequence_case["call_arguments"]["moves"]:
        move["src"] = move.pop("object_position_m")
        move["tgt"] = move.pop("target_position_m")
    sequence_case["call_arguments"].update(
        {"source_field": "src", "target_field": "tgt"}
    )

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "fixed literal move-item keys" in "\n".join(check.errors)


def test_physical_policy_resolves_split_cartesian_component_arguments() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    g2_api = next(item for item in manifest["capabilities"] if item["module"] == "g2")
    existing_parameters = g2_api["signature"]["parameters"]
    g2_api["signature"]["parameters"] = [
        existing_parameters[0],
        {"name": "target_x_m", "annotation": "float", "has_default": False},
        {"name": "target_y_m", "annotation": "float", "has_default": False},
        {"name": "target_z_m", "annotation": "float", "has_default": False},
        *existing_parameters[2:],
    ]
    g2_api["validation_binding"] = {
        "effect": "cartesian_target",
        "target_arguments": {
            "x": "target_x_m",
            "y": "target_y_m",
            "z": "target_z_m",
        },
    }
    g2_case = next(item for item in suite["cases"] if item["module"] == "g2")
    target_x, target_y, target_z = g2_case["call_arguments"].pop("position_m")
    g2_case["call_arguments"].update(
        {
            "target_x_m": target_x,
            "target_y_m": target_y,
            "target_z_m": target_z,
        }
    )

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, "\n".join(check.errors)


def test_physical_policy_uses_explicit_joint_binding_for_gripper_pos() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    g1_api = next(item for item in manifest["capabilities"] if item["module"] == "g1")
    g1_api["signature"]["parameters"] = [
        {"name": "runtime", "annotation": "object", "has_default": False},
        {"name": "gripper_pos", "annotation": "float", "has_default": False},
    ]
    g1_api["validation_binding"] = {
        "effect": "joint_targets",
        "joint_target_arguments": {"gripper.pos": "gripper_pos"},
    }
    g1_case = next(item for item in suite["cases"] if item["module"] == "g1")
    g1_case["call_arguments"] = {"gripper_pos": 50.0}
    g1_case["target_measurements"] = {
        "joint_positions": {"gripper.pos": 50.0},
        "finite": True,
    }
    g1_case["tolerances"] = {
        "joint_positions": {"gripper.pos": 0.5},
    }

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, "\n".join(check.errors)


def test_physical_policy_reports_actionable_g1_mapping_key_contract() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    g1_api = next(item for item in manifest["capabilities"] if item["module"] == "g1")
    old_argument = g1_api["validation_binding"]["joint_targets_argument"]
    parameter = next(
        item
        for item in g1_api["signature"]["parameters"]
        if item["name"] == old_argument
    )
    parameter["name"] = "joint_targets"
    g1_api["validation_binding"]["joint_targets_argument"] = "joint_targets"
    g1_case = next(item for item in suite["cases"] if item["module"] == "g1")
    valid_targets = g1_case["call_arguments"].pop(old_argument)
    g1_case["call_arguments"]["joint_targets"] = {
        key.removesuffix(".pos"): value for key, value in valid_targets.items()
    }

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    contract_errors = [
        error for error in check.errors if "G1 mapping binding requires" in error
    ]
    assert check.ok is False
    assert len(contract_errors) == 1
    error = contract_errors[0]
    assert "call_arguments['joint_targets'] keys to exactly equal" in error
    assert "target_measurements['joint_positions'] keys" in error
    assert "values at identical keys must equal" in error
    assert "missing call keys=['shoulder_lift.pos', 'shoulder_pan.pos']" in error
    assert "extra call keys=['shoulder_lift', 'shoulder_pan']" in error
    assert "not call_arguments['joint_targets']={'shoulder_pan': 15.0}" in error


def test_physical_policy_reports_actionable_g1_mapping_value_contract() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    g1_api = next(item for item in manifest["capabilities"] if item["module"] == "g1")
    old_argument = g1_api["validation_binding"]["joint_targets_argument"]
    parameter = next(
        item
        for item in g1_api["signature"]["parameters"]
        if item["name"] == old_argument
    )
    parameter["name"] = "joint_targets"
    g1_api["validation_binding"]["joint_targets_argument"] = "joint_targets"
    g1_case = next(item for item in suite["cases"] if item["module"] == "g1")
    valid_targets = g1_case["call_arguments"].pop(old_argument)
    g1_case["call_arguments"]["joint_targets"] = valid_targets
    g1_case["call_arguments"]["joint_targets"]["shoulder_pan.pos"] += 1.0

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    error_text = "\n".join(check.errors)
    assert check.ok is False
    assert "values in call_arguments['joint_targets'] to exactly equal" in error_text
    assert "target_measurements['joint_positions']" in error_text
    assert "mismatched keys=['shoulder_pan.pos']" in error_text


def test_physical_policy_rejects_g3_source_that_does_not_match_clean_reset() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    case = next(
        item
        for item in suite["cases"]
        if item["capability_id"] == "G3.push_object"
    )
    case["call_arguments"]["object_position_m"][0] += 0.01

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "object source 'test_cube' is not bound" in "\n".join(check.errors)


def test_physical_policy_rejects_mismatched_extent_and_outside_contact_height() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    capability = next(
        item for item in manifest["capabilities"] if item["capability_id"] == "G3.push_object"
    )
    capability["signature"]["parameters"].extend(
        [
            {
                "name": "object_size_m",
                "annotation": "float",
                "has_default": True,
                "default": 0.025,
            },
            {
                "name": "contact_height_above_table_m",
                "annotation": "float",
                "has_default": True,
                "default": 0.05,
            },
        ]
    )
    capability["validation_binding"].update(
        {
            "object_extent_argument": "object_size_m",
            "object_extent_semantics": "cube_edge_m",
            "contact_height_argument": "contact_height_above_table_m",
            "contact_height_reference": "height_above_table_m",
        }
    )
    case = next(
        item for item in suite["cases"] if item["capability_id"] == "G3.push_object"
    )
    case["initial_state"]["bodies"][0]["size_m"] = [0.03, 0.03, 0.03]
    case["call_arguments"].update(
        {
            "object_size_m": 0.025,
            "contact_height_above_table_m": 0.05,
        }
    )

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    text = "\n".join(check.errors)
    assert "bound object extent" in text
    assert "outside the object's vertical contact span" in text


def test_physical_policy_rejects_brittle_tolerances_extra_measurements_and_unsafe_id() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["cases"][0]["case_id"] = "unsafe/path"
    suite["cases"][0]["tolerances"]["joint_positions"]["shoulder_pan.pos"] = 0.01
    suite["cases"][1]["tolerances"]["end_effector_position_m"] = 0.001
    suite["cases"][1]["target_measurements"]["contact"] = False
    suite["cases"][2]["tolerances"]["object_positions_m"]["test_cube"] = 0.001

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "filename-safe ASCII" in text
    assert "trusted g1 range" in text
    assert "trusted g2 range" in text
    assert "trusted g3 range" in text
    assert "unsupported physical extras: ['contact']" in text


def test_physical_policy_rejects_out_of_envelope_target_and_optional_timeout_override() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    manifest = json.loads(json.dumps(manifest))
    g2_api = next(item for item in manifest["capabilities"] if item["module"] == "g2")
    g2_api["signature"]["parameters"].append(
        {
            "name": "timeout_s",
            "annotation": "float",
            "has_default": True,
            "default": 8.0,
        }
    )
    g2_case = next(item for item in suite["cases"] if item["module"] == "g2")
    g2_case["call_arguments"]["position_m"] = [0.90, 0.0, 0.10]
    g2_case["call_arguments"]["timeout_s"] = 8.0
    g2_case["target_measurements"]["end_effector_position_m"] = [0.90, 0.0, 0.10]
    g2_case["timeout_s"] = 8.5

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "outside the trusted P0 workspace" in text
    assert "call_arguments.timeout_s must be omitted" in text
    assert "independent host-monotonic harness deadline" in text
    assert "must exceed call_arguments.timeout_s" not in text


def test_physical_policy_accepts_optional_public_timeout_default_with_no_cross_clock_margin() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    manifest = json.loads(json.dumps(manifest))
    g2_api = next(item for item in manifest["capabilities"] if item["module"] == "g2")
    g2_api["signature"]["parameters"].append(
        {
            "name": "timeout_s",
            "annotation": "float",
            "has_default": True,
            "default": 90.0,
        }
    )

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, check.errors


def test_physical_policy_does_not_compare_required_public_timeout_to_harness_deadline() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    manifest = json.loads(json.dumps(manifest))
    g2_api = next(item for item in manifest["capabilities"] if item["module"] == "g2")
    g2_api["signature"]["parameters"].append(
        {
            "name": "motion_timeout_s",
            "annotation": "float",
            "has_default": False,
        }
    )
    g2_case = next(item for item in suite["cases"] if item["module"] == "g2")
    g2_case["call_arguments"]["motion_timeout_s"] = 10.0
    g2_case["timeout_s"] = 8.0

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is True, check.errors


def test_physical_policy_rejects_finite_only_noop_bypass() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    for case in suite["cases"]:
        case["target_measurements"] = {"finite": True}
        case["tolerances"] = {"finite": "ignored"}
        case["forbidden_conditions"] = []

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "must include 'joint_positions' for g1" in text
    assert "must include 'end_effector_position_m' for g2" in text
    assert "must include 'object_positions_m' for g3" in text
    assert "boolean/string targets must not have a tolerance" in text
    assert "forbidden_conditions must be non-empty" in text


def test_physical_policy_rejects_boolean_tolerance_and_missing_safety() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["cases"][0]["tolerances"]["finite"] = 1.0
    suite["cases"][1]["forbidden_conditions"] = ["simulation_nan_or_instability"]

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "boolean/string targets must not have a tolerance" in text
    assert "lacks required g2 safety checks" in text


@pytest.mark.parametrize(
    ("bad_state", "message"),
    [
        ({"qpos_rad": "degrees-not-an-object"}, "qpos_rad must be an object"),
        ({"bodies": "not-an-array"}, "bodies must be an array"),
        (
            {
                "bodies": [
                    {"id": "test_cube", "kind": "sphere", "position_m": [0.3, 0.0]}
                ]
            },
            "kind must be 'cube' or 'cylinder'",
        ),
    ],
)
def test_physical_policy_rejects_invalid_initial_state(
    bad_state: dict[str, Any], message: str
) -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["cases"][2]["initial_state"] = bad_state

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert message in "\n".join(check.errors)


def test_physical_policy_rejects_missing_target_object() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["cases"][2]["target_measurements"]["object_positions_m"] = {
        "ghost_cube": [0.39, -0.05, 0.037]
    }
    suite["cases"][2]["tolerances"]["object_positions_m"] = {
        "ghost_cube": 0.003
    }

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "target object 'ghost_cube' is absent" in "\n".join(check.errors)


def test_physical_policy_rejects_argument_type_and_target_mismatch() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["cases"][0]["call_arguments"]["targets"] = "wrong type"
    suite["cases"][1]["call_arguments"]["position_m"] = [0.2, 0.0, 0.1]

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "does not match public annotation 'dict[str, float]'" in text
    assert "end-effector target is not bound" in text


def test_physical_policy_rejects_target_within_initial_tolerance() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    case = suite["cases"][2]
    initial = case["initial_state"]["bodies"][0]["position_m"]
    case["call_arguments"]["target_position_m"] = list(initial)
    case["target_measurements"]["object_positions_m"]["test_cube"] = list(initial)

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "within tolerance of its initial state" in "\n".join(check.errors)


def test_suite_rejects_undeclared_top_level_fields() -> None:
    stage1, manifest, case_schema = _physical_inputs()
    suite = _strict_physical_suite()
    suite["model_notes"] = "must not be frozen"

    check = validate_suite(
        suite,
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
    )

    assert check.ok is False
    assert "undeclared top-level fields" in "\n".join(check.errors)


def test_scripted_fixture_can_explicitly_skip_physical_evidence_policy() -> None:
    stage1, manifest, case_schema = _physical_inputs()

    check = validate_suite(
        _load_json("fixtures/validation_suite.json"),
        stage1,
        manifest,
        reference_library=_physical_reference(),
        case_schema=case_schema,
        enforce_physical_policy=False,
    )

    assert check.ok is True, "\n".join(check.errors)


def test_minimal_suite_rejects_duplicate_cases_and_flattened_tolerances(
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    suite = _suite(public_api_manifest)
    duplicate = json.loads(json.dumps(suite["cases"][0]))
    duplicate["case_id"] += ".duplicate"
    suite["cases"].append(duplicate)
    suite["cases"][1]["target_measurements"] = {
        "object_positions_m": {"cube": [0.30, 0.0, 0.035]}
    }
    suite["cases"][1]["tolerances"] = {
        "object_positions_m.cube": 0.02
    }

    check = validate_suite(suite, stage1_artifact, public_api_manifest)

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "exactly one case per capability" in text
    assert "tolerance key is not present" in text
    assert "numeric target lacks a tolerance" in text


def test_suite_rejects_non_lerobot_joint_measurement_keys(
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    suite = _suite(public_api_manifest)
    suite["cases"][0]["target_measurements"] = {
        "joint_positions": {"shoulder_pan_deg": 10.0}
    }
    suite["cases"][0]["tolerances"] = {
        "joint_positions": {"shoulder_pan_deg": 1.0}
    }

    check = validate_suite(
        suite,
        stage1_artifact,
        public_api_manifest,
        reference_library={
            "permitted_measurements": ["position", "joint_positions"],
            "forbidden_conditions": ["collision"],
        },
    )

    assert check.ok is False
    assert "invalid LeRobot keys: ['shoulder_pan_deg']" in "\n".join(check.errors)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_parser_rejects_non_finite_constants(constant: str) -> None:
    with pytest.raises(ValidationSuiteError, match="non-finite"):
        parse_json_document('{"value": ' + constant + "}")


def test_suite_rejects_non_finite_tolerance_and_unbounded_timeout(
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    suite = _suite(public_api_manifest)
    suite["cases"][0]["tolerances"]["position"] = float("nan")
    suite["cases"][1]["timeout_s"] = float("inf")
    suite["cases"][2]["timeout_s"] = 31.0

    check = validate_suite(suite, stage1_artifact, public_api_manifest)

    assert check.ok is False
    text = "\n".join(check.errors)
    assert "finite numbers" in text
    assert text.count("finite and in (0, 30]") == 2


@pytest.mark.parametrize(
    ("prompt_count", "call_limit"),
    [(2, 3), (4, 3), (3, 2), (3, 4)],
)
def test_validation_suite_budget_is_fixed_at_exactly_three(
    tmp_path: Path,
    prompt_count: int,
    call_limit: int,
) -> None:
    with pytest.raises(ValueError):
        ValidationSuiteGenerator(
            client=ScriptedModelClient([]),
            prompts=tuple("prompt" for _ in range(prompt_count)),
            output_dir=tmp_path / "suite",
            trace_path=tmp_path / "trace.jsonl",
            call_limit=call_limit,
        )


def test_malformed_first_response_is_repaired_across_all_three_calls(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    final = _suite(public_api_manifest)
    client = ScriptedModelClient(["not json", json.dumps(final), json.dumps(final)])
    generator = ValidationSuiteGenerator(
        client=client,
        prompts=("generate", "review", "final review"),
        output_dir=tmp_path / "suite",
        trace_path=tmp_path / "trace.jsonl",
    )

    frozen = generator.generate(
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        reference_library={},
        case_schema={},
    )

    assert frozen.model_calls == 3
    assert len(client.requests) == 3
    assert (tmp_path / "suite/call_01_parse_error.json").is_file()
    second_request = json.loads(client.requests[1][-1].content)
    assert second_request["previous_candidate"] is None
    assert "not a JSON document" in second_request["deterministic_check"]["errors"][0]
