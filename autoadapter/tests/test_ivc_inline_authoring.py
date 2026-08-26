from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from autoadapter2.driver_synthesis.interactive import IsolatedArtifactSession
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.libraries import RobotPackage, load_robot_package
from autoadapter2.react import ToolCall, ToolTurn
from autoadapter2.validation_compiler.ivc import (
    IVCError,
    IVC_SYSTEM_PROMPT,
    _build_ivc_authoring_brief,
    _build_ivc_authoring_index,
    _private_inputs_from_package,
    build_ivc_inputs,
    load_sanitized_ivc_examples,
    run_ivc,
    validate_capability_validation_suite,
)


ROOT = Path(__file__).resolve().parents[2]
ROBOT_ROOT = ROOT / "autoadapter" / "libraries" / "robots"


def _without_repeated_schema_annotations(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    projected = {}
    for name, value in schema.items():
        if name in {"description", "evidence_refs"}:
            continue
        if name == "properties" and isinstance(value, dict):
            projected[name] = {
                property_name: _without_repeated_schema_annotations(
                    property_schema
                )
                for property_name, property_schema in value.items()
            }
        elif name == "items" and isinstance(value, dict):
            projected[name] = _without_repeated_schema_annotations(value)
        else:
            projected[name] = copy.deepcopy(value)
    return projected


def _synthetic(
    tmp_path: Path,
) -> tuple[RobotPackage, dict[str, Any], dict[str, Any], dict[str, Any]]:
    package_root = tmp_path / "package"
    scene = package_root / "assets" / "scene.xml"
    scene.parent.mkdir(parents=True)
    scene.write_text(
        "<mujoco><worldbody><body name='tool'><site name='tool_site'/></body>"
        "</worldbody></mujoco>",
        encoding="utf-8",
    )
    package = RobotPackage(
        root=package_root,
        robot_configuration_id="novel-arm",
        package_version="1.0.0",
        snapshot_id="novel-tasks-v1",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(),
        mjcf_path=scene,
        skeleton_dir=package_root / "skeleton",
        reference_driver=package_root / "reference" / "driver.py",
        private_dir=package_root / "tasks" / "private",
    )
    evidence_ref = {
        "source_id": "real-calibration",
        "specific_reference": "target_m bounds from admitted scene calibration",
    }
    criterion = {
        "metric": "novel_tip_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.02,
        "temporal": {"kind": "terminal_state"},
        "aggregation": {"kind": "single_trial"},
        "source_refs": [copy.deepcopy(evidence_ref)],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "N1",
                "method_name": "move_novel_tip",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target_m": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {
                                "type": "number",
                                "minimum": -1.0,
                                "maximum": 1.0,
                                "evidence_refs": [copy.deepcopy(evidence_ref)],
                            },
                        }
                    },
                    "required": ["target_m"],
                    "additionalProperties": False,
                },
                "criteria": [criterion],
            }
        ],
        "task_support": [],
    }
    guards = [
        {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
        {"guard_id": "state", "kind": "no_direct_state_write"},
        {"guard_id": "canonical", "kind": "canonical_model_data"},
    ]
    instance = {
        "instance_id": "novel-scene",
        "context_namespace": "capability",
        "scene_entrypoint": "assets/scene.xml",
        "reset": {"kind": "default"},
        "guard_ids": [guard["guard_id"] for guard in guards],
        "repetitions": 1,
        "timeout_sim_s": 2.0,
    }
    private = {
        "instances": {
            "calibration_namespace": "capability",
            "instances": [instance],
        },
        "bindings": {
            "calibration_namespace": "capability",
            "bindings": [
                {
                    "binding_id": "example-only",
                    "kind": "final_site_position_error",
                    "metric": "example_metric",
                    "unit": "m",
                    "parameters": {
                        "site_name": "tool_site",
                        "target_argument": "request.target_m",
                    },
                }
            ],
        },
        "guards": {
            "calibration_namespace": "capability",
            "guards": guards,
        },
    }
    cases = []
    for role, target in (
        ("nominal", [0.1, 0.0, 0.1]),
        ("calibrated_boundary", [1.0, 0.0, 0.1]),
    ):
        cases.append(
            {
                "case_id": f"novel-{role}",
                "case_role": role,
                "capability_id": "N1",
                "method_name": "move_novel_tip",
                "request": {"target_m": target},
                "request_grounding_refs": [copy.deepcopy(evidence_ref)],
                "instance_id": "novel-scene",
                "measurement_binding": {
                    "metric": "novel_tip_error",
                    "unit": "m",
                    "kind": "final_site_position_error",
                    "parameters": {
                        "site_name": "tool_site",
                        "target_argument": "request.target_m",
                    },
                },
                "guard_ids": list(instance["guard_ids"]),
                "repetitions": 1,
                "timeout_sim_s": 2.0,
                "criteria": [copy.deepcopy(criterion)],
            }
        )
    suite = {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }
    return package, design, private, suite


def test_novel_capability_seals_ivc_authored_inline_measurement(tmp_path: Path) -> None:
    package, design, private, suite = _synthetic(tmp_path)

    canonical = validate_capability_validation_suite(
        suite,
        package=package,
        design=design,
        private_inputs=private,
    )

    assert [case["request"] for case in canonical["cases"]] == [
        {"target_m": [0.1, 0.0, 0.1]},
        {"target_m": [1.0, 0.0, 0.1]},
    ]
    assert all("binding_id" not in case for case in canonical["cases"])
    assert {
        case["measurement_binding"]["kind"] for case in canonical["cases"]
    } == {"final_site_position_error"}


def test_ivc_audit_reports_all_independent_case_errors(tmp_path: Path) -> None:
    package, design, private, suite = _synthetic(tmp_path)
    (package.root / "assets" / "scene.xml").write_text(
        "<mujoco><worldbody><body name='tool'>"
        "<joint name='tool_joint' type='hinge' range='-1 1'/>"
        "<geom type='sphere' size='.01' mass='.1'/>"
        "<site name='tool_site'/></body></worldbody></mujoco>",
        encoding="utf-8",
    )
    suite["cases"][0]["measurement_binding"]["parameters"][
        "target_argument"
    ] = "target_m"
    suite["cases"][1]["measurement_binding"] = {
        "metric": "novel_tip_error",
        "unit": "m",
        "kind": "final_joint_position_error",
        "parameters": {
            "joint_name": "tool_joint",
            "target_argument": "request.target_m",
        },
    }

    with pytest.raises(IVCError) as captured:
        validate_capability_validation_suite(
            suite,
            package=package,
            design=design,
            private_inputs=private,
        )

    message = str(captured.value)
    assert "cases[0] (case_id='novel-nominal')" in message
    assert "request path 'target_m' must be rooted at request.<field>" in message
    assert "cases[1] (case_id='novel-calibrated_boundary')" in message
    assert (
        "final_joint_position_error for joint 'tool_joint' must use unit 'rad', "
        "not 'm'"
    ) in message


def test_dynamic_suite_rejects_historical_binding_id(tmp_path: Path) -> None:
    package, design, private, suite = _synthetic(tmp_path)
    suite["cases"][0]["binding_id"] = "example-only"

    with pytest.raises(IVCError, match="forbidden field 'binding_id'"):
        validate_capability_validation_suite(
            suite,
            package=package,
            design=design,
            private_inputs=private,
        )


def test_build_inputs_exposes_catalog_scenes_and_examples_not_binding_selection(
    tmp_path: Path,
) -> None:
    package, design, private, _suite = _synthetic(tmp_path)
    design["capabilities"][0]["evidence_refs"] = [
        {
            "source_id": "capability-provenance-only",
            "specific_reference": "not a request bound",
        }
    ]
    design["task_support"] = [
        {"task_id": "task-z", "capability_id": "N1", "rationale": "verbose-z"},
        {"task_id": "task-a", "capability_id": "N1", "rationale": "verbose-a"},
    ]

    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    assert "private_bindings" not in inputs
    assert inputs["validator_contract"]["binding_id_is_forbidden"] is True
    assert inputs["trusted_measurement_examples"]["bindings"][0]["example_id"] == (
        "example-only"
    )
    assert "binding_id" not in json.dumps(inputs["trusted_measurement_examples"])
    scene = inputs["scene_entity_catalog"]["scenes"][0]
    assert scene["entities"]["sites"] == ["tool_site"]
    operator_kinds = {
        item["kind"]
        for item in inputs["measurement_operator_catalog"]["operators"]
    }
    assert "final_site_position_error" in operator_kinds
    assert "go2_stable_stance_recovery" not in operator_kinds
    assert "so101_end_effector_regulation" not in operator_kinds
    assert len(inputs["complete_so101_go2_worked_references"]) == 2
    authoring_index = _build_ivc_authoring_index(inputs)
    capability_entry = authoring_index["sealed_authoring_contract"][
        "capabilities"
    ][0]
    assert set(authoring_index) == {
        "raw_paths",
        "private_instance_records",
        "private_instance_shared_execution_fields",
        "sealed_authoring_contract",
        "measurement_binding_contract",
        "operator_parameter_signature_legend",
        "measurement_binding_kind_catalog",
        "scene_declared_frame_aliases",
    }
    assert set(capability_entry) == {
        "capability_id",
        "method_name",
        "criteria",
        "request_schema",
        "rooted_request_paths",
        "allowed_request_grounding_refs_from_sealed_schema",
    }
    assert authoring_index["raw_paths"] == {
        "design": "ivc_inputs.json::sealed_capability_design",
        "task_support": (
            "ivc_inputs.json::sealed_capability_design.task_support"
        ),
        "instances": "ivc_inputs.json::private_instances.instances",
        "operators": "ivc_inputs.json::measurement_operator_catalog.operators",
        "scenes": "ivc_inputs.json::scene_entity_catalog.scenes",
    }
    assert capability_entry["rooted_request_paths"] == ["request.target_m"]
    assert capability_entry["request_schema"] == {
        "type": "object",
        "properties": {
            "target_m": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "number",
                    "minimum": -1.0,
                    "maximum": 1.0,
                },
            }
        },
        "required": ["target_m"],
        "additionalProperties": False,
    }
    assert capability_entry["request_schema"] == (
        _without_repeated_schema_annotations(
            design["capabilities"][0]["request_schema"]
        )
    )
    assert capability_entry["criteria"] == design["capabilities"][0]["criteria"]
    assert authoring_index["sealed_authoring_contract"][
        "task_support_by_capability"
    ] == {"N1": ["task-a", "task-z"]}
    assert "verbose-a" not in json.dumps(authoring_index)
    operator_signatures = authoring_index["measurement_binding_kind_catalog"]
    assert operator_signatures["final_site_position_error"][
        "parameter_types_not_values"
    ] == {
        "site_name": "entity:site",
        "target_argument": "request_path:number_array_3",
    }
    assert "final_joint_position_error" in operator_signatures
    expected_operator_kinds = {
        operator["kind"]
        for operator in inputs["measurement_operator_catalog"]["operators"]
        if "m" in operator["output_units"]
    }
    assert set(operator_signatures) == expected_operator_kinds
    assert authoring_index["measurement_binding_contract"]["exact_fields"] == [
        "metric",
        "unit",
        "kind",
        "parameters",
    ]
    assert authoring_index["measurement_binding_contract"]["forbidden_fields"] == [
        "operator",
        "mode",
        "evaluation_mode",
        "binding_id",
    ]
    assert "replace with exact" in authoring_index[
        "operator_parameter_signature_legend"
    ]["entity:<type>"]
    for operator in inputs["measurement_operator_catalog"]["operators"]:
        if operator["kind"] not in expected_operator_kinds:
            continue
        raw_schema = operator["parameter_schema"]
        required = set(raw_schema.get("required", []))
        expected_parameters = {}
        for name, parameter_schema in raw_schema["properties"].items():
            signature = parameter_schema["type"]
            if name in operator.get("entity_parameters", {}):
                signature = f"entity:{operator['entity_parameters'][name]}"
            elif name in operator.get("request_value_types", {}):
                signature = f"request_path:{operator['request_value_types'][name]}"
            if name not in required:
                signature = f"optional:{signature}"
            expected_parameters[name] = signature
        projected = operator_signatures[operator["kind"]]
        assert projected["allowed_binding_units"] == operator["output_units"]
        assert projected["parameter_types_not_values"] == expected_parameters
        assert "purpose" not in projected
        if operator.get("evaluation_mode") in (None, "numeric_measurement"):
            assert "framework_evaluation_mode_not_a_binding_field" not in projected
        else:
            assert projected[
                "framework_evaluation_mode_not_a_binding_field"
            ] == operator["evaluation_mode"]
    assert capability_entry[
        "allowed_request_grounding_refs_from_sealed_schema"
    ] == [
        {
            "source_id": "real-calibration",
            "specific_reference": "target_m bounds from admitted scene calibration",
        }
    ]
    assert "capability-provenance-only" not in json.dumps(capability_entry)
    assert "operator_shortlist_filtered_by_sealed_criterion_units" not in authoring_index
    assert "relevant_scene_entity_catalogs" not in authoring_index
    candidate_text = json.dumps(operator_signatures)
    for forbidden in (
        "selected_kind",
        "recommended_kind",
        "selected_instance_id",
        "selected_entity",
        "binding_id",
        "measurement_binding",
    ):
        assert forbidden not in candidate_text

    def nested_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(
                *(nested_keys(item) for item in value.values())
            )
        if isinstance(value, list):
            return set().union(*(nested_keys(item) for item in value))
        return set()

    assert nested_keys(authoring_index).isdisjoint(
        {
            "selected_kind",
            "recommended_kind",
            "selected_instance_id",
            "selected_entity",
            "binding_id",
            "measurement_binding",
        }
    )

    shared_fields = authoring_index["private_instance_shared_execution_fields"]
    projected_instances = []
    for projected_record in authoring_index["private_instance_records"]:
        record = dict(shared_fields)
        record.update(projected_record)
        projected_instances.append(record)
    expected_instances = []
    for raw_record in inputs["private_instances"]["instances"]:
        expected = {
            "instance_id": raw_record["instance_id"],
            "context_namespace": raw_record.get("context_namespace"),
            "scene_entrypoint": raw_record["scene_entrypoint"],
            "mandatory_guard_ids": raw_record.get("guard_ids", []),
            "repetitions": raw_record.get("repetitions"),
            "timeout_sim_s": raw_record.get("timeout_sim_s"),
        }
        for optional_field in ("capability_id", "case_role", "request_domain"):
            if raw_record.get(optional_field) is not None:
                expected[optional_field] = raw_record[optional_field]
        expected_instances.append(expected)
    assert projected_instances == expected_instances


def test_real_kinova_authoring_index_is_compact_and_projects_robot_base() -> None:
    package = load_robot_package(
        ROBOT_ROOT / "kinova_gen3_robotiq_2f85" / "1.0.0"
    )
    evidence_ref = {
        "source_id": "schema-calibration",
        "specific_reference": "bounded robot-base Cartesian calibration",
    }
    coordinate = {
        "type": "number",
        "unit": "m",
        "frame": "robot_base",
        "minimum": -1.0,
        "maximum": 1.0,
        "evidence_refs": [evidence_ref],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "K1",
                "method_name": "move_tip",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target_position": {
                            "type": "object",
                            "properties": {
                                axis: copy.deepcopy(coordinate)
                                for axis in ("x", "y", "z")
                            },
                            "required": ["x", "y", "z"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["target_position"],
                    "additionalProperties": False,
                },
                "criteria": [
                    {
                        "metric": "tip_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.05,
                        "temporal": {"kind": "terminal_state"},
                        "aggregation": {"kind": "single_trial"},
                        "source_refs": [evidence_ref],
                    }
                ],
            }
        ],
        "task_support": [],
    }
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=_private_inputs_from_package(package),
    )
    authoring_index = _build_ivc_authoring_index(inputs)
    compact_index = json.dumps(
        authoring_index, ensure_ascii=True, separators=(",", ":")
    )
    assert len(compact_index.encode("utf-8")) < 30_000
    capability_entry = authoring_index["sealed_authoring_contract"][
        "capabilities"
    ][0]
    assert capability_entry["rooted_request_paths"] == [
        "request.target_position.x",
        "request.target_position.y",
        "request.target_position.z",
    ]
    assert capability_entry["criteria"] == design["capabilities"][0]["criteria"]
    assert capability_entry["request_schema"] == (
        _without_repeated_schema_annotations(
            design["capabilities"][0]["request_schema"]
        )
    )
    operator_signatures = authoring_index["measurement_binding_kind_catalog"]
    assert "final_site_frame_xyz_position_error" in operator_signatures
    assert "final_joint_position_error" in operator_signatures
    alias_group = next(
        item
        for item in authoring_index["scene_declared_frame_aliases"]
        if "assets/reach_scene.xml" in item["scene_entrypoints"]
    )
    assert alias_group["declared_frame_aliases"] == {"robot_base": "base_link"}
    assert alias_group["unresolved_declared_frames"] == []
    authoring_brief = json.dumps(
        _build_ivc_authoring_brief(inputs),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    assert '"sealed_capability_design"' not in authoring_brief
    assert len((IVC_SYSTEM_PROMPT + authoring_brief).encode("utf-8")) < 40_000


def test_franka_ivc_inputs_fit_read_limit_when_serialized_compactly(
    tmp_path: Path,
) -> None:
    package = load_robot_package(ROBOT_ROOT / "franka_panda" / "1.0.0")
    _unused_package, design, _unused_private, _unused_suite = _synthetic(tmp_path)
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=_private_inputs_from_package(package),
    )

    pretty = json.dumps(inputs, ensure_ascii=True, indent=2) + "\n"
    compact = json.dumps(inputs, ensure_ascii=True, separators=(",", ":")) + "\n"

    # This is the observed real-package regression: indentation pushes the
    # phase input past the session's 200k read_file boundary.
    assert len(pretty.encode("utf-8")) > 200_000
    assert len(compact.encode("utf-8")) <= 200_000
    assert json.loads(compact) == inputs
    session = IsolatedArtifactSession(
        workspace=tmp_path / "franka-ivc-workspace",
        budget=ProbeBudget(max_requests=None),
        package=package,
    )
    try:
        (session.workspace / "ivc_inputs.json").write_text(
            compact,
            encoding="utf-8",
        )
        observed = session.read_file({"path": "ivc_inputs.json"})
    finally:
        session.close()
    assert json.loads(observed["content"]) == inputs


class _InspectThenCorrectIVCModel:
    def __init__(self, suite: dict[str, Any]) -> None:
        self.suite = copy.deepcopy(suite)
        self.messages: list[list[dict[str, Any]]] = []
        self.tool_names: list[set[str]] = []

    def generate_tool_turn(self, **kwargs: Any) -> ToolTurn:
        self.messages.append([dict(item) for item in kwargs["messages"]])
        self.tool_names.append(
            {item["function"]["name"] for item in kwargs["tools"]}
        )
        turn = len(self.messages)
        if turn <= 2:
            call = ToolCall(
                id=f"inspect-{turn}",
                name="read_file",
                arguments={"path": "ivc_inputs.json"},
                raw_arguments=json.dumps({"path": "ivc_inputs.json"}),
            )
            return ToolTurn(content=None, tool_calls=(call,), finish_reason="tool_calls")

        artifact = copy.deepcopy(self.suite)
        if turn == 3:
            # Reproduce the observed wrapper/list/guessed-ID mistake.  The
            # authoring index supplies the exact scalar ID instead.
            artifact["cases"][0]["instance_id"] = ["guessed-private-instance"]
        content = json.dumps(artifact, ensure_ascii=True, separators=(",", ":"))
        call = ToolCall(
            id=f"write-{turn}",
            name="write_file",
            arguments={
                "path": "capability_validation_suite.json",
                "content": content,
            },
            raw_arguments=json.dumps(
                {
                    "path": "capability_validation_suite.json",
                    "content": content,
                }
            ),
        )
        return ToolTurn(content=None, tool_calls=(call,), finish_reason="end_turn")


def test_ivc_reserves_turns_three_through_six_for_delivery_and_correction(
    tmp_path: Path,
) -> None:
    package, design, private, suite = _synthetic(tmp_path)
    model = _InspectThenCorrectIVCModel(suite)
    destination = tmp_path / "sealed" / "capability_validation_suite.json"

    canonical = run_ivc(
        model,
        package=package,
        design=design,
        private_inputs=private,
        max_turns=6,
        artifact_path=destination,
    )

    assert canonical["cases"] == suite["cases"]
    assert len(model.messages) == 4
    assert model.tool_names == [
        {"read_file", "write_file", "execute_python"},
        {"read_file", "write_file", "execute_python"},
        {"write_file"},
        {"write_file"},
    ]
    assert "instance_id is not a supplied private instance" in json.dumps(
        model.messages[3]
    )
    first_prompt = str(model.messages[0][0]["content"])
    assert '"instances":"ivc_inputs.json::private_instances.instances"' in first_prompt
    assert '"instance_id":"novel-scene"' in first_prompt
    assert '"mandatory_guard_ids":["control","state","canonical"]' in first_prompt
    assert '"allowed_request_grounding_refs_from_sealed_schema"' in first_prompt
    assert '"measurement_binding_contract"' in first_prompt
    assert (
        '"forbidden_fields":["operator","mode","evaluation_mode","binding_id"]'
        in first_prompt
    )
    normalized_system_prompt = " ".join(IVC_SYSTEM_PROMPT.split()).lower()
    assert (
        "never copy a signature marker as a parameter value"
        in normalized_system_prompt
    )
    assert "operator, mode, and" in IVC_SYSTEM_PROMPT
    assert '"measurement_binding_kind_catalog"' in first_prompt
    assert '"unit_compatible_operator_signatures_by_kind"' not in first_prompt
    assert '"final_site_position_error"' in first_prompt
    assert '"parameter_types_not_values":{"site_name":"entity:site","target_argument":"request_path:number_array_3"}' in first_prompt
    assert '"scenes":"ivc_inputs.json::scene_entity_catalog.scenes"' in first_prompt
    assert '"rooted_request_paths":["request.target_m"]' in first_prompt
    assert "measurement_operator_catalog.operators" in first_prompt
    assert "at most two inspection turns" in IVC_SYSTEM_PROMPT
    assert "turns three through six are write/correction only" in IVC_SYSTEM_PROMPT
    assert "The Framework has not selected an operator" in IVC_SYSTEM_PROMPT
    assert "Ground requests only with the supplied schema/domain evidence pairs" in (
        IVC_SYSTEM_PROMPT
    )
    assert len(first_prompt.encode("utf-8")) < 40_000
    serialized_inputs = (
        destination.parent / "workspace" / "ivc_inputs.json"
    ).read_text(encoding="utf-8")
    assert serialized_inputs == json.dumps(
        json.loads(serialized_inputs),
        ensure_ascii=True,
        separators=(",", ":"),
    ) + "\n"
    assert len(
        json.loads(serialized_inputs)["complete_so101_go2_worked_references"]
    ) == 2


def test_complete_so101_go2_references_pass_real_operator_audit() -> None:
    references = load_sanitized_ivc_examples()
    index = json.loads((ROBOT_ROOT / "index.json").read_text(encoding="utf-8"))[
        "robots"
    ]
    counts: list[int] = []
    for reference in references:
        suite = reference["validation_suite"]
        package = load_robot_package(ROBOT_ROOT / index[suite["robot_configuration_id"]])
        canonical = validate_capability_validation_suite(
            suite,
            package=package,
            design=reference["capability_design"],
        )
        counts.append(len(canonical["cases"]))
        assert "binding_id" not in json.dumps(canonical)
        assert "task_support" not in reference["capability_design"]
    assert counts == [12, 10]


def test_semantic_operator_catalog_is_scoped_to_exact_reference_contract() -> None:
    reference = next(
        item
        for item in load_sanitized_ivc_examples()
        if item["validation_suite"]["robot_configuration_id"]
        == "robotstudio_so101"
    )
    package = load_robot_package(ROBOT_ROOT / "robotstudio_so101" / "1.0.4")
    inputs = build_ivc_inputs(
        package=package,
        design=reference["capability_design"],
        private_inputs=_private_inputs_from_package(package),
    )

    operators = {
        item["kind"]: item
        for item in inputs["measurement_operator_catalog"]["operators"]
    }
    assert "so101_end_effector_regulation" in operators
    assert "go2_planar_twist_tracking" not in operators
    assert operators["so101_end_effector_regulation"]["execution_scope"] == {
        "robot_configuration_id": "robotstudio_so101",
        "capability_id": "A1",
        "requires_exact_worked_reference_contract": True,
    }


def test_semantic_operator_rejects_changed_reference_identity_or_criterion() -> None:
    reference = next(
        item
        for item in load_sanitized_ivc_examples()
        if item["validation_suite"]["robot_configuration_id"]
        == "robotstudio_so101"
    )
    package = load_robot_package(ROBOT_ROOT / "robotstudio_so101" / "1.0.4")

    changed_criterion_design = copy.deepcopy(reference["capability_design"])
    changed_criterion_suite = copy.deepcopy(reference["validation_suite"])
    changed = changed_criterion_design["capabilities"][0]["criteria"][0]
    changed["threshold"] = float(changed["threshold"]) + 0.001
    for case in changed_criterion_suite["cases"]:
        if case["capability_id"] == "A1":
            case["criteria"] = [copy.deepcopy(changed)]
    with pytest.raises(IVCError, match="outside its exact SO-101/Go2 worked-reference"):
        validate_capability_validation_suite(
            changed_criterion_suite,
            package=package,
            design=changed_criterion_design,
        )

    renamed_design = copy.deepcopy(reference["capability_design"])
    renamed_suite = copy.deepcopy(reference["validation_suite"])
    renamed_design["capabilities"][0]["capability_id"] = "renamed-A1"
    for case in renamed_suite["cases"]:
        if case["capability_id"] == "A1":
            case["capability_id"] = "renamed-A1"
    with pytest.raises(IVCError, match="outside its exact SO-101/Go2 worked-reference"):
        validate_capability_validation_suite(
            renamed_suite,
            package=package,
            design=renamed_design,
        )


def test_go2_ivc_context_includes_all_task_scenes_beside_five_references() -> None:
    package = load_robot_package(
        ROBOT_ROOT / "unitree-go2-stock-12dof" / "1.0.0"
    )
    private = _private_inputs_from_package(package)
    design = json.loads(
        (ROOT / "autoadapter/references/capability_v2/go2.json").read_text(
            encoding="utf-8"
        )
    )
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
    )

    instances = inputs["private_instances"]["instances"]
    assert len(instances) == 30
    assert sum(item["context_namespace"] == "task" for item in instances) == 20
    assert sum(item["context_namespace"] == "capability" for item in instances) == 10
    assert len(inputs["trusted_measurement_examples"]["bindings"]) == 27
    serialized = json.dumps(inputs["private_instances"])
    for forbidden in (
        "task_id",
        "public_arguments",
        "reference_arguments",
        "preinvoke",
        "framework_events",
        "task_plan",
        "clause_bindings",
    ):
        assert forbidden not in serialized
