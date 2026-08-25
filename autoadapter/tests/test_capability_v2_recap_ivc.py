from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoadapter2.b2.capability_adapter import CapabilityAdapter
from autoadapter2.b2.public_observation import project_public_state
from autoadapter2.capability_design import (
    CAPABILITY_INVOCATION_ABI,
    CAPABILITY_PROTOCOL_VERSION,
    CapabilityDesignError,
    CapabilitySchemaError,
    TGCD_ARTIFACT_TURNS,
    TGCDPhase,
    build_public_tgcd_inputs,
    load_public_reference_catalog,
    validate_capability_design,
)
from autoadapter2.capability_design.tgcd import run_tgcd
from autoadapter2.task_demo.recap import RecapBudgets, run_recap
from autoadapter2.libraries import load_robot_package
from autoadapter2.react import ToolCall, ToolTurn
from autoadapter2.validation_compiler import (
    IVC_ARTIFACT_TURNS,
    IVC_CASE_ROLES,
    IVCPhase,
    build_ivc_inputs,
    load_sanitized_ivc_examples,
    run_ivc,
    validate_capability_validation_suite,
)
from autoadapter2.validation_compiler.ivc import IVCError


ROOT = Path(__file__).resolve().parents[2]
SO101_PACKAGE_ROOT = ROOT / "autoadapter" / "libraries" / "robots" / "robotstudio_so101" / "1.0.4"


def _package() -> dict[str, Any]:
    return {
        "robot_configuration_id": "test_robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "test-tasks-v1",
        "morphology": {
            "public_observations": {"frames": ["world"]},
            "public_affordances": {"actions": ["position"], "observations": ["joint_positions"]},
        },
        "tasks": [
            {"task_id": "task-a", "description": "First public task"},
            {"task_id": "task-b", "description": "Second public task"},
        ],
    }


def _evidence(field: str) -> list[dict[str, str]]:
    return [{"source_id": "test-calibration", "specific_reference": field}]


def _scalar_schema(field: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "number",
                "unit": "m",
                "frame": "world",
                "minimum": -1.0,
                "maximum": 1.0,
                "evidence_refs": _evidence(f"request bound: {field}"),
            }
        },
        "required": [field],
        "additionalProperties": False,
    }


def _design() -> dict[str, Any]:
    capabilities = []
    for index in range(3):
        capability_id = f"C{index + 1}"
        method_name = f"apply_effect_{index + 1}"
        capabilities.append(
            {
                "capability_id": capability_id,
                "method_name": method_name,
                "description": f"Apply reusable effect {index + 1}.",
                "effect": f"bounded_effect_{index + 1}",
                "request_schema": _scalar_schema(f"amount_{index + 1}"),
                "preconditions": ["The public robot state is finite."],
                "temporal_semantics": {"kind": "bounded_single_call", "hold_s": 0.2},
                "invariants": ["No unrelated public state is intentionally changed."],
                "failure_behavior": "Return a bounded operation error without private details.",
                "criteria": [
                    {
                        "metric": f"effect_{index + 1}_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.05 + index * 0.01,
                        "temporal": {"kind": "terminal_hold", "duration_s": 0.2},
                        "aggregation": {"kind": "all_samples"},
                        "source_refs": _evidence(f"criterion: C{index + 1}"),
                    }
                ],
                "evidence_refs": _evidence(f"capability: C{index + 1}"),
            }
        )
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        **{
            "robot_configuration_id": "test_robot",
            "package_version": "1.0.0",
            "task_snapshot_id": "test-tasks-v1",
        },
        "invocation_abi": CAPABILITY_INVOCATION_ABI,
        "capabilities": capabilities,
        "task_support": [
            {"task_id": task_id, "capability_id": cap_id, "rationale": "same reusable effect"}
            for task_id in ("task-a", "task-b")
            for cap_id in ("C1", "C2", "C3")
        ],
    }


def _private_inputs(design: dict[str, Any]) -> dict[str, Any]:
    instances = []
    bindings = []
    guards = []
    for capability in design["capabilities"]:
        capability_id = capability["capability_id"]
        criterion = capability["criteria"][0]
        for role in IVC_CASE_ROLES:
            suffix = f"{capability_id.lower()}-{role}"
            field = f"amount_{capability_id[1:]}"
            anchor_amount = -0.25 if role == "nominal" else 0.75
            domain = json.loads(json.dumps(capability["request_schema"]))
            domain_leaf = domain["properties"][field]
            if role == "nominal":
                domain_leaf["minimum"] = -0.5
                domain_leaf["maximum"] = 0.25
            else:
                domain_leaf["minimum"] = 0.5
                domain_leaf["maximum"] = 0.9
            profile = f"{capability_id}-{role}"
            instances.append(
                {
                    "instance_id": f"instance-{suffix}",
                    "capability_id": capability_id,
                    "case_role": role,
                    "calibration_profile": profile,
                    "scene_entrypoint": f"assets/{suffix}.xml",
                    "reset": {"kind": "default"},
                    "request_domain": domain,
                    "request_anchors": [
                        {
                            "anchor_id": f"anchor-{suffix}",
                            "request": {field: anchor_amount},
                            "source_ref": f"private-calibration::{suffix}",
                        }
                    ],
                    "clause_bindings": {
                        "capability-criterion": f"binding-{suffix}"
                    },
                    "guard_ids": [],
                    "repetitions": 2,
                    "timeout_sim_s": 5.0,
                }
            )
            bindings.append(
                {
                    "binding_id": f"binding-{suffix}",
                    "capability_id": capability_id,
                    "case_role": role,
                    "calibration_profile": profile,
                    "metric": criterion["metric"],
                    "unit": criterion["unit"],
                }
            )
    return {"instances": instances, "bindings": bindings, "guards": guards}


def _suite(design: dict[str, Any], private: dict[str, Any]) -> dict[str, Any]:
    cases = []
    instances = {item["instance_id"]: item for item in private["instances"]}
    bindings = {item["binding_id"]: item for item in private["bindings"]}
    for capability in design["capabilities"]:
        cap_id = capability["capability_id"]
        method_name = capability["method_name"]
        for role in IVC_CASE_ROLES:
            suffix = f"{cap_id.lower()}-{role}"
            instance = instances[f"instance-{suffix}"]
            authored_amount = 0.0 if role == "nominal" else 0.6
            cases.append(
                {
                    "case_id": f"case-{suffix}",
                    "case_role": role,
                    "capability_id": cap_id,
                    "method_name": method_name,
                    "instance_id": instance["instance_id"],
                    "binding_id": bindings[f"binding-{suffix}"]["binding_id"],
                    "guard_ids": [],
                    "repetitions": 2,
                    "timeout_sim_s": 5.0,
                    "request": {f"amount_{cap_id[1:]}": authored_amount},
                    "criteria": json.loads(json.dumps(capability["criteria"])),
                }
            )
    return {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "robot_configuration_id": "test_robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "test-tasks-v1",
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": cases,
    }


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for child in value.values():
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def test_capability_schema_is_closed_units_framed_and_source_backed() -> None:
    package = _package()
    design = validate_capability_design(_design(), package)
    for capability in design["capabilities"]:
        schema = capability["request_schema"]
        assert schema["additionalProperties"] is False
        field = next(iter(schema["properties"].values()))
        assert field["unit"] == "m"
        assert field["frame"] == "world"
        assert field["evidence_refs"]
    forbidden = json.loads(json.dumps(_design()))
    forbidden["capabilities"][0]["request_schema"]["properties"]["task_id"] = {
        "type": "string",
        "unit": "none",
        "frame": "none",
    }
    forbidden["capabilities"][0]["request_schema"]["required"].append("task_id")
    with pytest.raises(CapabilityDesignError):
        validate_capability_design(forbidden, package)


def test_numeric_schema_bound_without_evidence_is_rejected() -> None:
    schema = _scalar_schema("amount")
    del schema["properties"]["amount"]["evidence_refs"]
    with pytest.raises(CapabilitySchemaError, match="evidence_refs"):
        from autoadapter2.capability_design import validate_schema_definition

        validate_schema_definition(schema)


def test_recap_finish_name_is_reserved_at_capability_design_boundary() -> None:
    design = _design()
    design["capabilities"][0]["method_name"] = "finish"
    with pytest.raises(CapabilityDesignError, match="non-reserved"):
        validate_capability_design(design, _package())


@pytest.mark.parametrize(
    "method_name",
    ["class", "_private_method", "model", "close", "render", "step"],
)
def test_tgcd_rejects_every_generation_reserved_method_name(
    method_name: str,
) -> None:
    design = _design()
    design["capabilities"][0]["method_name"] = method_name
    with pytest.raises(CapabilityDesignError, match="non-reserved"):
        validate_capability_design(design, _package())


def test_tgcd_rejects_multiple_top_level_criteria() -> None:
    design = _design()
    design["capabilities"][0]["criteria"].append(
        json.loads(json.dumps(design["capabilities"][0]["criteria"][0]))
    )
    with pytest.raises(CapabilityDesignError, match="exactly one"):
        validate_capability_design(design, _package())


def test_public_reference_projection_has_only_so101_and_go2_designs() -> None:
    references = load_public_reference_catalog()
    assert [len(reference["capabilities"]) for reference in references] == [6, 5]
    keys = _walk_keys(references)
    assert not keys.intersection(
        {
            "task_support",
            "task_id",
            "task_ids",
            "task_mapping",
            "task_mappings",
            "oracle_plan",
            "exact_task_calls",
        }
    )
    for reference in references:
        for capability in reference["capabilities"]:
            assert capability["criteria"][0]["source_refs"]
            assert capability["preconditions"]
            assert capability["temporal_semantics"]
            assert capability["invariants"]
            assert capability["failure_behavior"]


def test_tgcd_inputs_expose_exact_abi_and_compact_authoring_indices() -> None:
    package = _package()
    inputs = build_public_tgcd_inputs(package)

    assert inputs["invocation_abi"] == {
        "kind": "capability_request",
        "method_call": "method(request=request)",
        "request_required": ["request"],
    }
    assert inputs["artifact_header"] == {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": "test_robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "test-tasks-v1",
        "invocation_abi": inputs["invocation_abi"],
    }
    assert inputs["validator_contract"]["criteria_count_per_capability"] == 1
    assert inputs["validator_contract"][
        "bounded_schema_fields_require_evidence_refs"
    ] is True
    assert "bounds" not in inputs["validator_contract"][
        "request_schema_supported_keywords"
    ]
    assert inputs["validator_contract"]["criterion_required_fields"] == [
        "metric",
        "unit",
        "comparator",
        "threshold",
        "temporal",
        "aggregation",
        "source_refs",
    ]
    assert inputs["matching_capability_references"] == []
    assert inputs["task_index"] == [
        {
            key: task[key]
            for key in ("task_id", "name", "description")
            if key in task
        }
        for task in package["tasks"]
    ]
    assert inputs["matching_reference_indices"] == []

    matching_package = json.loads(json.dumps(package))
    matching_package["robot_configuration_id"] = "robotstudio_so101"
    matching_inputs = build_public_tgcd_inputs(matching_package)
    assert matching_inputs["matching_reference_indices"] == [0]
    assert [
        reference["robot_configuration_id"]
        for reference in matching_inputs["matching_capability_references"]
    ] == ["robotstudio_so101"]


class _TGCDModel:
    def __init__(self, design: dict[str, Any]) -> None:
        self.design = design
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            invalid = json.loads(json.dumps(self.design))
            invalid["task_support"] = []
            return invalid
        return self.design


class _ArtifactModel:
    def __init__(self, artifacts: list[dict[str, Any]]) -> None:
        self.artifacts = list(artifacts)
        self.messages: list[list[dict[str, Any]]] = []
        self.tool_names: list[set[str]] = []

    def generate_tool_turn(self, **kwargs: Any) -> ToolTurn:
        self.messages.append([dict(item) for item in kwargs["messages"]])
        self.tool_names.append(
            {item["function"]["name"] for item in kwargs["tools"]}
        )
        artifact = self.artifacts.pop(0)
        path = artifact.pop("__path__")
        content = json.dumps(artifact, ensure_ascii=True)
        call = ToolCall(
            id=f"write-{len(self.messages)}",
            name="write_file",
            arguments={"path": path, "content": content},
            raw_arguments=json.dumps({"path": "canonical", "content": content}),
        )
        return ToolTurn(content=None, tool_calls=(call,), finish_reason="end_turn")


def _real_package_design() -> tuple[Any, dict[str, Any]]:
    package = load_robot_package(SO101_PACKAGE_ROOT)
    design = _design()
    design.update(
        {
            "robot_configuration_id": package.robot_configuration_id,
            "package_version": package.package_version,
            "task_snapshot_id": package.snapshot_id,
        }
    )
    design["task_support"] = [
        {
            "task_id": task["task_id"],
            "capability_id": capability["capability_id"],
            "rationale": "bounded reusable physical effect",
        }
        for task in package.tasks
        for capability in design["capabilities"]
    ]
    return package, design


def test_tgcd_six_turn_phase_accepts_generic_experience_and_seals_artifact(tmp_path: Path) -> None:
    model = _TGCDModel(_design())
    events: list[dict[str, Any]] = []
    artifact_path = tmp_path / "capability_design.json"
    result = TGCDPhase(
        model,
        _package(),
        study={
            "condition": "skeleton-assisted",
            "findings": ["canonical joints are finite"],
            "implementation_plan": ["use bounded feedback"],
            "probe_requests": [{"probe_id": "public-liveness", "script": "pass"}],
        },
        experience=[
            {
                "observation": "public outcome",
                "lesson": "keep the motion bounded",
                "recommendation": "hold the target",
                "scope": "design",
                "public_evidence": ["source-x"],
                "provenance": {"run": "r1"},
                "outcome": "useful",
            }
        ],
        callback=events.append,
        artifact_path=artifact_path,
    ).run()
    assert result["artifact_type"] == "capability_design"
    assert len(model.calls) == 2
    assert TGCD_ARTIFACT_TURNS == 6
    assert artifact_path.exists()
    assert any(event["stage"] == "tgcd-sealed" for event in events)
    assert "candidate_driver" not in json.dumps(model.calls[0]["inputs"])
    assert model.calls[0]["inputs"]["completed_public_study"]["findings"] == [
        "canonical joints are finite"
    ]


def test_tgcd_real_client_uses_exact_file_tools_and_accepts_final_turn_write(
    tmp_path: Path,
) -> None:
    package, design = _real_package_design()
    artifact = {**json.loads(json.dumps(design)), "__path__": "capability_design.json"}
    model = _ArtifactModel([artifact])

    result = run_tgcd(
        model,
        package,
        max_turns=1,
        artifact_path=tmp_path / "sealed" / "capability_design.json",
        reference_catalog=[],
    )

    assert result["artifact_type"] == "capability_design"
    assert model.tool_names == [{"write_file"}]
    assert '"artifact_header"' in model.messages[0][0]["content"]
    assert '"validator_contract"' in model.messages[0][0]["content"]
    assert '"matching_capability_references":[]' in model.messages[0][0]["content"]
    assert (tmp_path / "sealed" / "capability_design.json").is_file()


def test_tgcd_rejects_candidate_material_in_study_projection() -> None:
    with pytest.raises(CapabilityDesignError, match="candidate/private"):
        build_public_tgcd_inputs(
            _package(),
            study={"candidate_driver": "not public study material"},
        )


class _IVCModel:
    def __init__(self, suite: dict[str, Any]) -> None:
        self.suite = suite
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            invalid = json.loads(json.dumps(self.suite))
            invalid["cases"].pop()
            return invalid
        return self.suite


def test_ivc_is_blind_and_compiles_exactly_two_cases_per_capability(tmp_path: Path) -> None:
    package = _package()
    design = _design()
    private = _private_inputs(design)
    suite = _suite(design, private)
    inputs = build_ivc_inputs(
        package=package,
        design=design,
        private_inputs=private,
        examples=[{"observation": "public only", "outcome": "bounded"}],
    )
    serialized = json.dumps(inputs).lower()
    assert "driver" not in serialized
    assert "repair" not in serialized
    assert "candidate" not in serialized
    assert load_sanitized_ivc_examples()
    assert inputs["sanitized_capability_validation_examples"]
    assert inputs["artifact_header"] == {
        "artifact_type": "capability_validation_suite",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": "test_robot",
        "package_version": "1.0.0",
        "task_snapshot_id": "test-tasks-v1",
        "whole_suite_aggregation": {"kind": "all_cases"},
    }
    assert inputs["validator_contract"]["case_count"] == 6
    assert inputs["validator_contract"][
        "ivc_authors_complete_request_for_each_case"
    ] is True
    assert inputs["validator_contract"][
        "validate_authored_request_against_selected_instance_request_domain_when_supplied"
    ] is True
    assert inputs["validator_contract"][
        "request_anchors_are_calibration_evidence_not_prescribed_cases"
    ] is True
    model = _IVCModel(suite)
    events: list[dict[str, Any]] = []
    artifact_path = tmp_path / "capability_validation_suite.json"
    result = IVCPhase(
        model,
        package,
        design,
        private,
        callback=events.append,
        artifact_path=artifact_path,
        reference_positive_control_hook=lambda **kwargs: {"reference_passed": True},
    ).run()
    assert len(result["cases"]) == 2 * len(design["capabilities"])
    assert len(model.calls) == 2
    assert any(event["stage"] == "ivc-reference-positive-control" for event in events)
    assert artifact_path.exists()
    roles = {(case["capability_id"], case["case_role"]) for case in result["cases"]}
    assert len(roles) == 2 * len(design["capabilities"])
    assert IVC_ARTIFACT_TURNS == 6

    tampered = json.loads(json.dumps(suite))
    tampered["cases"][0]["criteria"][0]["threshold"] = 999.0
    with pytest.raises(IVCError, match="criterion"):
        validate_capability_validation_suite(
            tampered, package=package, design=design, private_inputs=private
        )


def test_ivc_authors_non_anchor_requests_inside_optional_calibrated_domains() -> None:
    package = _package()
    design = _design()
    private = _private_inputs(design)
    suite = _suite(design, private)

    calibrated = validate_capability_validation_suite(
        suite, package=package, design=design, private_inputs=private
    )
    assert calibrated["cases"][0]["request"] != calibrated["cases"][1]["request"]
    instances = {item["instance_id"]: item for item in private["instances"]}
    for case in calibrated["cases"]:
        anchors = instances[case["instance_id"]]["request_anchors"]
        assert all(case["request"] != anchor["request"] for anchor in anchors)


def test_ivc_rejects_authored_request_outside_optional_calibrated_domain() -> None:
    package = _package()
    design = _design()
    private = _private_inputs(design)
    suite = _suite(design, private)

    outside_domain = json.loads(json.dumps(suite))
    request_field = next(iter(outside_domain["cases"][0]["request"]))
    outside_domain["cases"][0]["request"][request_field] = 0.5
    with pytest.raises(IVCError, match="private request_domain"):
        validate_capability_validation_suite(
            outside_domain,
            package=package,
            design=design,
            private_inputs=private,
        )


def test_ivc_authors_from_sealed_schema_when_private_domain_is_absent() -> None:
    package = _package()
    design = _design()
    private = _private_inputs(design)
    for instance in private["instances"]:
        instance.pop("request_domain")
        instance.pop("request_anchors")
    suite = _suite(design, private)

    canonical = validate_capability_validation_suite(
        suite,
        package=package,
        design=design,
        private_inputs=private,
    )

    assert canonical["cases"] == suite["cases"]


def test_ivc_rejects_request_role_and_instance_binding_mismatches() -> None:
    package = _package()
    design = _design()
    private = _private_inputs(design)
    suite = _suite(design, private)

    invalid_request = json.loads(json.dumps(suite))
    invalid_request["cases"][0]["request"] = {"task_id": "task-a"}
    with pytest.raises(IVCError, match="request"):
        validate_capability_validation_suite(
            invalid_request, package=package, design=design, private_inputs=private
        )

    duplicate_requests = json.loads(json.dumps(suite))
    cap_id = duplicate_requests["cases"][0]["capability_id"]
    pair = [
        case for case in duplicate_requests["cases"] if case["capability_id"] == cap_id
    ]
    pair[1]["request"] = json.loads(json.dumps(pair[0]["request"]))
    duplicate_private = json.loads(json.dumps(private))
    for instance in duplicate_private["instances"]:
        instance.pop("request_domain")
        instance.pop("request_anchors")
    with pytest.raises(IVCError, match="requests must differ"):
        validate_capability_validation_suite(
            duplicate_requests,
            package=package,
            design=design,
            private_inputs=duplicate_private,
        )

    wrong_binding = json.loads(json.dumps(suite))
    wrong_binding["cases"][0]["binding_id"] = wrong_binding["cases"][2]["binding_id"]
    with pytest.raises(IVCError, match="does not belong"):
        validate_capability_validation_suite(
            wrong_binding, package=package, design=design, private_inputs=private
        )

    wrong_role = json.loads(json.dumps(suite))
    wrong_role["cases"][0]["case_role"] = "calibrated_boundary"
    with pytest.raises(IVCError, match="private instance role"):
        validate_capability_validation_suite(
            wrong_role, package=package, design=design, private_inputs=private
        )

    wrong_profile = json.loads(json.dumps(private))
    wrong_profile["bindings"][0]["calibration_profile"] = "another-profile"
    with pytest.raises(IVCError, match="calibration_profile differ"):
        validate_capability_validation_suite(
            suite,
            package=package,
            design=design,
            private_inputs=wrong_profile,
        )


def test_ivc_reference_failure_returns_sanitized_same_conversation_correction(
    tmp_path: Path,
) -> None:
    package, design = _real_package_design()
    private = _private_inputs(design)
    suite = _suite(design, private)
    for field, value in {
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
    }.items():
        suite[field] = value
    first = {**json.loads(json.dumps(suite)), "__path__": "capability_validation_suite.json"}
    second = {**json.loads(json.dumps(suite)), "__path__": "capability_validation_suite.json"}
    model = _ArtifactModel([first, second])
    controls = iter(
        [
            RuntimeError("SECRET-private-case-id-and-driver-path"),
            {"passed": True},
        ]
    )

    def reference_control(**_kwargs: Any) -> dict[str, Any]:
        value = next(controls)
        if isinstance(value, Exception):
            raise value
        return value

    result = run_ivc(
        model,
        package=package,
        design=design,
        private_inputs=private,
        max_turns=2,
        reference_positive_control_hook=reference_control,
        artifact_path=tmp_path / "sealed" / "capability_validation_suite.json",
    )

    assert len(result["cases"]) == 2 * len(design["capabilities"])
    assert model.tool_names == [
        {"write_file"},
        {"write_file"},
    ]
    assert '"artifact_header"' in model.messages[0][0]["content"]
    assert '"validator_contract"' in model.messages[0][0]["content"]
    correction_messages = json.dumps(model.messages[1])
    assert "reference calibration failed" in correction_messages
    assert "SECRET-private-case-id-and-driver-path" not in correction_messages
    workspace = tmp_path / "sealed" / "workspace"
    assert (workspace / "ivc_inputs.json").is_file()
    assert not (workspace / "staged").exists()
    assert not (workspace / "morphology.json").exists()
    assert not (workspace / "driver.py").exists()


def test_recap_uses_dynamic_catalogue_and_fixed_16_12_budgets() -> None:
    design = _design()
    calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((method_name, arguments))
        return {"operation": {"status": "EXECUTED"}, "public_state": {"sim_time_s": 1.0}}

    adapter = CapabilityAdapter(design, invoke)
    catalog = adapter.public_catalog()
    assert {item["method_name"] for item in catalog} == {
        capability["method_name"] for capability in design["capabilities"]
    }
    assert all("criteria" not in item and "task_support" not in item for item in catalog)

    class Model:
        def __init__(self) -> None:
            self.turn = 0

        def generate_recap_json(self, **kwargs: Any) -> dict[str, Any]:
            self.turn += 1
            if self.turn == 1:
                method = design["capabilities"][0]["method_name"]
                field = next(iter(design["capabilities"][0]["request_schema"]["properties"]))
                return {
                    "reasoning_summary": "Apply one public capability.",
                    "subtasks": [{"kind": "capability", "capability_name": method, "request": {field: 0.1}}],
                }
            return {"reasoning_summary": "Done.", "subtasks": []}

    result = run_recap(
        public_task={"objective": "Use the public interface."},
        adapter=adapter,
        model=Model(),
    )
    assert result.status == "CONTROLLER_FINISHED"
    assert result.planning_turns == 2
    assert result.capability_calls == 1
    assert RecapBudgets().max_planning_turns == 16
    assert RecapBudgets().max_capability_calls == 12
    assert calls[0][1].keys() == {"request"}


class _MujocoTypes:
    mjOBJ_SITE = "site"
    mjOBJ_JOINT = "joint"
    mjOBJ_BODY = "body"
    mjOBJ_GEOM = "geom"
    mjOBJ_SENSOR = "sensor"


class _MujocoJoint:
    mjJNT_FREE = 0


class _FakeMujoco:
    mjtObj = _MujocoTypes
    mjtJoint = _MujocoJoint

    @staticmethod
    def mj_name2id(model: Any, object_type: Any, name: str) -> int:
        return 0


def _fake_model_data() -> tuple[Any, Any]:
    model = SimpleNamespace(
        jnt_qposadr=[0],
        jnt_range=[[0.0, 1.0]],
        body_parentid=[0],
        body_jntadr=[0],
        body_jntnum=[1],
        jnt_type=[0],
        jnt_dofadr=[0],
        geom_bodyid=[0],
        sensor_adr=[0],
        sensor_dim=[1],
    )
    data = SimpleNamespace(
        time=1.0,
        qpos=[0.5],
        qvel=[0.0, 0.0, 0.0],
        site_xpos=[[0.1, 0.2, 0.3]],
        site_xmat=[[1.0] * 9],
        xpos=[[0.0, 0.0, 0.3]],
        xquat=[[1.0, 0.0, 0.0, 0.0]],
        cvel=[[0.0] * 6],
        sensordata=[0.0],
        ncon=0,
        contact=[],
    )
    return model, data


def test_public_observation_projection_covers_all_eleven_current_packages() -> None:
    index = json.loads((ROOT / "autoadapter" / "libraries" / "robots" / "index.json").read_text())
    mujoco = _FakeMujoco()
    for robot_id in index["robots"]:
        model, data = _fake_model_data()
        state = project_public_state(
            robot_configuration_id=robot_id,
            mujoco=mujoco,
            model=model,
            data=data,
        )
        assert state["simulation_time_s"] == 1.0
        assert "criterion" not in json.dumps(state).lower()
        assert "private" not in json.dumps(state).lower()
