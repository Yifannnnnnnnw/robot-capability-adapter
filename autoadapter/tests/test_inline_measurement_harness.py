from __future__ import annotations

import copy
import json
import math
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from autoadapter2.harness import HarnessError, run_private_suite
from autoadapter2.harness import runner as harness_runner
from autoadapter2.harness.measurements import MeasurementError, measure
from autoadapter2.harness.operators import (
    MeasurementOperatorError,
    audit_inline_measurement_binding,
    inspect_scene_entities,
    measurement_operator_catalog,
)
from autoadapter2.libraries import RobotPackage, load_robot_package


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value)), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[RobotPackage, Path, dict[str, Any], dict[str, Any]]:
    package_root = tmp_path / "package"
    scene = package_root / "assets" / "scene.xml"
    scene.parent.mkdir(parents=True)
    scene.write_text(
        "<mujoco><worldbody><body name='tool' pos='0.4 0 0.2'>"
        "<site name='tool_site'/></body></worldbody></mujoco>",
        encoding="utf-8",
    )
    private = package_root / "capability_validation" / "private"
    _write_json(
        private / "instances.json",
        {
            "instances": [
                {
                    "instance_id": "scene-a",
                    "scene_entrypoint": "assets/scene.xml",
                    "reset": {"kind": "default"},
                    "guard_ids": ["control", "state", "canonical"],
                    "repetitions": 1,
                    "timeout_sim_s": 1.0,
                }
            ]
        },
    )
    _write_json(private / "bindings.json", {"bindings": []})
    _write_json(
        private / "guards.json",
        {
            "guards": [
                {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
                {"guard_id": "state", "kind": "no_direct_state_write"},
                {"guard_id": "canonical", "kind": "canonical_model_data"},
            ]
        },
    )
    candidate = tmp_path / "candidate" / "driver.py"
    candidate.parent.mkdir()
    candidate.write_text(
        textwrap.dedent(
            """
            import mujoco

            class Driver:
                def __init__(self, model, data):
                    self.model = model
                    self.data = data

                def move_target(self, request):
                    self.data.ctrl[0] = 0.0
                    mujoco.mj_step(self.model, self.data)

            def build(*, model, data):
                return Driver(model, data)
            """
        ),
        encoding="utf-8",
    )
    package = RobotPackage(
        root=package_root,
        robot_configuration_id="test-arm",
        package_version="1.0.0",
        snapshot_id="snapshot-1",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(),
        mjcf_path=scene,
        skeleton_dir=package_root / "skeleton",
        reference_driver=candidate,
        private_dir=package_root / "tasks" / "private",
    )
    criterion = {
        "metric": "end_effector_position_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.015,
        "temporal": {"kind": "continuous", "duration_s": 0.5},
        "aggregation": {"kind": "single_trial"},
        "source_refs": [
            {"source_id": "calibration", "specific_reference": "real threshold"}
        ],
    }
    request_schema = {
        "type": "object",
        "properties": {
            "target_position_m": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            }
        },
        "required": ["target_position_m"],
        "additionalProperties": False,
    }
    design = {
        "capabilities": [
            {
                "capability_id": "cap-a",
                "method_name": "move_target",
                "request_schema": request_schema,
                "criteria": [criterion],
            }
        ]
    }
    suite = {
        "artifact_type": "capability_validation_suite",
        "capability_protocol_version": "capability-v2",
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": [
            {
                "case_id": "cap-a-nominal",
                "case_role": "nominal",
                "capability_id": "cap-a",
                "method_name": "move_target",
                "request": {"target_position_m": [0.4, 0.0, 0.2]},
                "request_grounding_refs": [
                    {
                        "source_id": "calibration",
                        "specific_reference": "real target",
                    }
                ],
                "instance_id": "scene-a",
                "measurement_binding": {
                    "metric": "end_effector_position_error",
                    "unit": "m",
                    "kind": "final_site_position_error",
                    "parameters": {
                        "site_name": "tool_site",
                        "target_argument": "request.target_position_m",
                    },
                },
                "guard_ids": ["control", "state", "canonical"],
                "repetitions": 1,
                "timeout_sim_s": 1.0,
                "criteria": [criterion],
            }
        ],
    }
    return package, candidate, design, suite


def _worker_result() -> dict[str, Any]:
    sample = {
        "body_positions": {"tool": [0.4, 0.0, 0.2]},
        "site_positions": {"tool_site": [0.4, 0.0, 0.2]},
        "body_quaternions": {"tool": [1.0, 0.0, 0.0, 0.0]},
        "joint_positions": {},
        "contacts": [],
    }
    return {
        "worker_completed": True,
        "method_invoked": True,
        "canonical_model_data": True,
        "candidate_exception": None,
        "candidate_log": "",
        "physical_evidence": {
            "step_count": 2,
            "ctrl_observed_before_step": True,
            "ctrl_changed_from_reset": True,
            "direct_state_write_detected": False,
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": None,
            "contact_pair_min_distances": [],
            "samples": [
                {"time": 0.0, **sample},
                {"time": 0.5, **sample},
            ],
        },
        "video": {"requested": False, "complete": False, "frame_count": 0},
    }


def test_inline_binding_measures_real_scene_and_stays_parent_side(tmp_path: Path) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    payloads: list[dict[str, Any]] = []

    def worker(payload: Mapping[str, Any], **_kwargs: Any) -> dict[str, Any]:
        payloads.append(dict(payload))
        return _worker_result()

    with mock.patch.object(harness_runner, "_run_worker", side_effect=worker):
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=candidate,
            condition="from-scratch",
            output_dir=tmp_path / "report",
            record_video=False,
        )

    assert report["validation_passed"] is True
    assert report["trials"][0]["measurement_value"] == 0.0
    assert payloads[0]["public_arguments"] == {
        "request": suite["cases"][0]["request"]
    }
    serialized_payload = json.dumps(payloads[0])
    for forbidden in ("measurement_binding", "criteria", "guard_ids", "task_id"):
        assert forbidden not in serialized_payload


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda binding: binding.update(kind="not_trusted"),
            "trusted operator catalog",
        ),
        (
            lambda binding: binding.update(unit="rad"),
            "sealed criterion unit",
        ),
        (
            lambda binding: binding["parameters"].update(site_name="missing-site"),
            "unknown site",
        ),
        (
            lambda binding: binding["parameters"].update(
                target_argument="request.private_target"
            ),
            "absent from the sealed request schema",
        ),
    ],
)
def test_invalid_inline_binding_is_rejected_before_worker(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    invalid = copy.deepcopy(suite)
    mutation(invalid["cases"][0]["measurement_binding"])

    with mock.patch.object(harness_runner, "_run_worker") as worker:
        with pytest.raises(HarnessError, match=message):
            run_private_suite(
                package=package,
                design=design,
                suite=invalid,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=tmp_path / "invalid-report",
                record_video=False,
            )
    worker.assert_not_called()


def test_capability_v2_binding_id_is_rejected_before_worker(tmp_path: Path) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    suite["cases"][0]["binding_id"] = "historical-binding"

    with mock.patch.object(harness_runner, "_run_worker") as worker:
        with pytest.raises(HarnessError, match="not binding_id"):
            run_private_suite(
                package=package,
                design=design,
                suite=suite,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=tmp_path / "binding-id-report",
                record_video=False,
            )
    worker.assert_not_called()


def test_operator_catalog_is_closed_and_excludes_b1_dispatch() -> None:
    catalog = measurement_operator_catalog()
    operators = {operator["kind"]: operator for operator in catalog["operators"]}
    kinds = set(operators)
    assert "final_site_position_error" in kinds
    assert "mean_body_yaw_rate" in kinds
    assert "final_body_xyz_position_error" in kinds
    assert "accumulated_body_arc_angle_error" in kinds
    assert "final_body_directional_displacement_error" in kinds
    assert "b1_contract" not in kinds
    assert catalog["binding_fields"] == ["metric", "unit", "kind", "parameters"]
    joint_error = operators["final_joint_position_error"]
    assert joint_error["output_units"] == ["rad", "m"]
    properties = joint_error["parameter_schema"]["properties"]
    assert properties["target_scale"]["default"] == 1.0
    assert properties["target_offset"]["default"] == 0.0


def _joint_scene(tmp_path: Path, *, joint_type: str) -> Path:
    scene = tmp_path / f"{joint_type}.xml"
    joint_range = "0 0.04" if joint_type == "slide" else "-1 1"
    scene.write_text(
        "<mujoco><worldbody><body name='moving'>"
        f"<joint name='measured_joint' type='{joint_type}' axis='1 0 0' "
        f"range='{joint_range}'/>"
        "<geom type='sphere' size='0.01' mass='1'/></body></worldbody></mujoco>",
        encoding="utf-8",
    )
    return scene


def _joint_request_schema(
    field: str,
    *,
    unit: str = "rad",
    minimum: float = -2.0,
    maximum: float = 2.0,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "number",
                "unit": unit,
                "minimum": minimum,
                "maximum": maximum,
                "evidence_refs": [
                    {
                        "source_id": "test-calibration",
                        "specific_reference": "trusted request bounds",
                    }
                ],
            }
        },
        "required": [field],
        "additionalProperties": False,
    }


def _joint_binding(*, metric: str, unit: str, field: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "unit": unit,
        "kind": "final_joint_position_error",
        "parameters": {
            "joint_name": "measured_joint",
            "target_argument": f"request.{field}",
        },
    }


def _joint_evidence(actual: float) -> dict[str, Any]:
    return {
        "samples": [
            {"joint_positions": {"measured_joint": 0.0}},
            {"joint_positions": {"measured_joint": actual}},
        ]
    }


def test_inline_binding_reports_operator_field_as_kind_authoring_error() -> None:
    with pytest.raises(
        MeasurementOperatorError,
        match="rename it to 'kind'.*trusted catalog key",
    ):
        audit_inline_measurement_binding(
            {
                "metric": "position_error",
                "unit": "m",
                "operator": "final_site_position_error",
                "parameters": {},
            },
            criterion={"metric": "position_error", "unit": "m"},
            request_schema={},
        )


def test_inline_binding_reports_evaluation_mode_as_non_kind() -> None:
    with pytest.raises(
        MeasurementOperatorError,
        match="Framework evaluation metadata, not a trusted catalog kind",
    ):
        audit_inline_measurement_binding(
            {
                "metric": "position_error",
                "unit": "m",
                "kind": "numeric_measurement",
                "parameters": {},
            },
            criterion={"metric": "position_error", "unit": "m"},
            request_schema={},
        )


def test_inline_binding_rejects_catalog_signature_as_parameter_value() -> None:
    with pytest.raises(
        MeasurementOperatorError,
        match="catalog type/source signature",
    ):
        audit_inline_measurement_binding(
            {
                "metric": "position_error",
                "unit": "m",
                "kind": "final_site_position_error",
                "parameters": {
                    "site_name": "entity:site",
                    "target_argument": "request_path:number_array_3",
                },
            },
            criterion={"metric": "position_error", "unit": "m"},
            request_schema={},
        )


def test_scaled_slide_joint_target_is_audited_and_measured(tmp_path: Path) -> None:
    binding = _joint_binding(
        metric="aperture_position_error",
        unit="m",
        field="aperture_fraction",
    )
    binding["parameters"].update(target_scale=0.04, target_offset=0.0)
    canonical = audit_inline_measurement_binding(
        binding,
        criterion={"metric": "aperture_position_error", "unit": "m"},
        request_schema=_joint_request_schema(
            "aperture_fraction", unit="fraction", minimum=0.0, maximum=1.0
        ),
        scene_path=_joint_scene(tmp_path, joint_type="slide"),
    )

    assert measure(
        canonical,
        evidence=_joint_evidence(0.04),
        public_arguments={"request": {"aperture_fraction": 1.0}},
    ) == 0.0


def test_scaled_joint_target_rejects_zero_scale_current_position_cheat(
    tmp_path: Path,
) -> None:
    binding = _joint_binding(
        metric="aperture_position_error",
        unit="m",
        field="aperture_fraction",
    )
    binding["parameters"].update(target_scale=0.0, target_offset=0.02)

    with pytest.raises(MeasurementOperatorError, match="target_scale must be positive"):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "aperture_position_error", "unit": "m"},
            request_schema=_joint_request_schema(
                "aperture_fraction", unit="fraction", minimum=0.0, maximum=1.0
            ),
            scene_path=_joint_scene(tmp_path, joint_type="slide"),
        )


def test_scaled_joint_target_rejects_arbitrary_offset(tmp_path: Path) -> None:
    binding = _joint_binding(
        metric="aperture_position_error",
        unit="m",
        field="aperture_fraction",
    )
    binding["parameters"].update(target_scale=0.04, target_offset=0.01)

    with pytest.raises(
        MeasurementOperatorError,
        match="target_offset does not match the evidence-backed",
    ):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "aperture_position_error", "unit": "m"},
            request_schema=_joint_request_schema(
                "aperture_fraction", unit="fraction", minimum=0.0, maximum=1.0
            ),
            scene_path=_joint_scene(tmp_path, joint_type="slide"),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda parameters: parameters.update(target_gain=0.04),
        lambda parameters: parameters.update(target_scale="0.04"),
        lambda parameters: parameters.update(target_offset=float("inf")),
    ],
)
def test_scaled_joint_target_rejects_unknown_wrong_and_nonfinite_parameters(
    tmp_path: Path,
    mutation: Any,
) -> None:
    binding = _joint_binding(
        metric="aperture_position_error",
        unit="m",
        field="aperture_fraction",
    )
    mutation(binding["parameters"])

    with pytest.raises(MeasurementOperatorError):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "aperture_position_error", "unit": "m"},
            request_schema=_joint_request_schema(
                "aperture_fraction", unit="fraction", minimum=0.0, maximum=1.0
            ),
            scene_path=_joint_scene(tmp_path, joint_type="slide"),
        )


def test_joint_position_error_keeps_legacy_radian_defaults(tmp_path: Path) -> None:
    binding = _joint_binding(metric="joint_error", unit="rad", field="target_rad")
    canonical = audit_inline_measurement_binding(
        binding,
        criterion={"metric": "joint_error", "unit": "rad"},
        request_schema=_joint_request_schema("target_rad"),
        scene_path=_joint_scene(tmp_path, joint_type="hinge"),
    )

    assert measure(
        canonical,
        evidence=_joint_evidence(0.25),
        public_arguments={"request": {"target_rad": 0.2}},
    ) == pytest.approx(0.05)


def test_joint_position_error_rejects_unit_that_disagrees_with_joint_type(
    tmp_path: Path,
) -> None:
    binding = _joint_binding(metric="joint_error", unit="rad", field="target_rad")
    with pytest.raises(MeasurementOperatorError, match="must use unit 'm'"):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "joint_error", "unit": "rad"},
            request_schema=_joint_request_schema("target_rad"),
            scene_path=_joint_scene(tmp_path, joint_type="slide"),
        )


def test_scaled_joint_measurement_rejects_nonfinite_request_value() -> None:
    binding = _joint_binding(
        metric="aperture_position_error",
        unit="m",
        field="aperture_fraction",
    )
    binding["parameters"]["target_scale"] = 0.04
    with pytest.raises(MeasurementError, match="request value must be finite"):
        measure(
            binding,
            evidence=_joint_evidence(0.04),
            public_arguments={"request": {"aperture_fraction": float("nan")}},
        )


def _numeric_leaf(unit: str) -> dict[str, Any]:
    return {
        "type": "number",
        "unit": unit,
        "frame": "world",
        "minimum": -2.0,
        "maximum": 2.0,
    }


def _xyz_object_schema(field: str, *, unit: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "object",
                "properties": {
                    axis: _numeric_leaf(unit) for axis in ("x", "y", "z")
                },
                "required": ["x", "y", "z"],
                "additionalProperties": False,
            }
        },
        "required": [field],
        "additionalProperties": False,
    }


def _body_evidence(*positions: tuple[float, float, float]) -> dict[str, Any]:
    return {
        "samples": [
            {
                "time": float(index),
                "body_positions": {"tool": list(position)},
            }
            for index, position in enumerate(positions)
        ]
    }


def test_final_body_xyz_position_error_audits_scalar_paths_and_measures(
    tmp_path: Path,
) -> None:
    package, _candidate, _design, _suite = _fixture(tmp_path)
    schema = _xyz_object_schema("target_position", unit="m")
    binding = {
        "metric": "body_position_error",
        "unit": "m",
        "kind": "final_body_xyz_position_error",
        "parameters": {
            "body_name": "tool",
            "target_x_argument": "request.target_position.x",
            "target_y_argument": "request.target_position.y",
            "target_z_argument": "request.target_position.z",
        },
    }
    canonical = audit_inline_measurement_binding(
        binding,
        criterion={"metric": "body_position_error", "unit": "m"},
        request_schema=schema,
        scene_path=package.mjcf_path,
    )

    assert measure(
        canonical,
        evidence=_body_evidence((0.0, 0.0, 0.0), (0.4, -0.1, 0.2)),
        public_arguments={
            "request": {"target_position": {"x": 0.4, "y": -0.1, "z": 0.2}}
        },
    ) == 0.0


def test_accumulated_body_arc_angle_error_uses_signed_trusted_samples(
    tmp_path: Path,
) -> None:
    package, _candidate, _design, _suite = _fixture(tmp_path)
    schema = _xyz_object_schema("arc_center", unit="m")
    schema["properties"].update(
        {
            "arc_axis": {
                "type": "string",
                "frame": "world",
                "enum": ["x", "y", "z"],
            },
            "angle": _numeric_leaf("rad"),
        }
    )
    schema["required"].extend(["arc_axis", "angle"])
    binding = {
        "metric": "arc_angle_error",
        "unit": "rad",
        "kind": "accumulated_body_arc_angle_error",
        "parameters": {
            "body_name": "tool",
            "center_x_argument": "request.arc_center.x",
            "center_y_argument": "request.arc_center.y",
            "center_z_argument": "request.arc_center.z",
            "axis_argument": "request.arc_axis",
            "target_angle_argument": "request.angle",
        },
    }
    canonical = audit_inline_measurement_binding(
        binding,
        criterion={"metric": "arc_angle_error", "unit": "rad"},
        request_schema=schema,
        scene_path=package.mjcf_path,
    )

    assert measure(
        canonical,
        evidence=_body_evidence(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (-1.0, 0.0, 0.0),
        ),
        public_arguments={
            "request": {
                "arc_center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "arc_axis": "z",
                "angle": math.pi,
            }
        },
    ) == pytest.approx(0.0)


def test_final_body_directional_displacement_error_projects_xyz_direction(
    tmp_path: Path,
) -> None:
    package, _candidate, _design, _suite = _fixture(tmp_path)
    schema = _xyz_object_schema("direction", unit="fraction")
    schema["properties"]["distance"] = _numeric_leaf("m")
    schema["required"].append("distance")
    binding = {
        "metric": "directional_displacement_error",
        "unit": "m",
        "kind": "final_body_directional_displacement_error",
        "parameters": {
            "body_name": "tool",
            "direction_x_argument": "request.direction.x",
            "direction_y_argument": "request.direction.y",
            "direction_z_argument": "request.direction.z",
            "target_distance_argument": "request.distance",
        },
    }
    canonical = audit_inline_measurement_binding(
        binding,
        criterion={"metric": "directional_displacement_error", "unit": "m"},
        request_schema=schema,
        scene_path=package.mjcf_path,
    )

    assert measure(
        canonical,
        evidence=_body_evidence((0.0, 0.0, 0.0), (0.0, -0.16, 0.0)),
        public_arguments={
            "request": {
                "direction": {"x": 0.0, "y": -1.0, "z": 0.0},
                "distance": 0.16,
            }
        },
    ) == pytest.approx(0.0)


def test_body_xyz_binding_rejects_aliased_paths_and_wrong_unit(tmp_path: Path) -> None:
    package, _candidate, _design, _suite = _fixture(tmp_path)
    schema = _xyz_object_schema("target_position", unit="m")
    binding = {
        "metric": "body_position_error",
        "unit": "m",
        "kind": "final_body_xyz_position_error",
        "parameters": {
            "body_name": "tool",
            "target_x_argument": "request.target_position.x",
            "target_y_argument": "request.target_position.x",
            "target_z_argument": "request.target_position.z",
        },
    }
    with pytest.raises(MeasurementOperatorError, match="distinct sibling x/y/z"):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "body_position_error", "unit": "m"},
            request_schema=schema,
            scene_path=package.mjcf_path,
        )

    binding["parameters"]["target_y_argument"] = "request.target_position.y"
    schema["properties"]["target_position"]["properties"]["x"]["unit"] = "rad"
    with pytest.raises(MeasurementOperatorError, match="unit='m'.*frame='world'"):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": "body_position_error", "unit": "m"},
            request_schema=schema,
            scene_path=package.mjcf_path,
        )


def test_directional_displacement_rejects_materially_non_unit_direction() -> None:
    binding = {
        "kind": "final_body_directional_displacement_error",
        "parameters": {
            "body_name": "tool",
            "direction_x_argument": "request.direction.x",
            "direction_y_argument": "request.direction.y",
            "direction_z_argument": "request.direction.z",
            "target_distance_argument": "request.distance",
        },
    }
    with pytest.raises(MeasurementError, match="must have unit length"):
        measure(
            binding,
            evidence=_body_evidence((0.0, 0.0, 0.0), (0.0, -0.16, 0.0)),
            public_arguments={
                "request": {
                    "direction": {"x": 0.0, "y": -2.0, "z": 0.0},
                    "distance": 0.16,
                }
            },
        )


def test_arc_angle_rejects_near_pi_sample_alias() -> None:
    binding = {
        "kind": "accumulated_body_arc_angle_error",
        "parameters": {
            "body_name": "tool",
            "center_x_argument": "request.arc_center.x",
            "center_y_argument": "request.arc_center.y",
            "center_z_argument": "request.arc_center.z",
            "axis_argument": "request.arc_axis",
            "target_angle_argument": "request.angle",
        },
    }
    with pytest.raises(MeasurementError, match="too far apart"):
        measure(
            binding,
            evidence=_body_evidence((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
            public_arguments={
                "request": {
                    "arc_center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "arc_axis": "z",
                    "angle": math.pi,
                }
            },
        )


def test_final_body_position_error_rejects_object_target_before_worker(
    tmp_path: Path,
) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    design["capabilities"][0]["request_schema"] = _xyz_object_schema(
        "target_position_m", unit="m"
    )
    suite["cases"][0]["request"] = {
        "target_position_m": {"x": 0.4, "y": 0.0, "z": 0.2}
    }
    suite["cases"][0]["measurement_binding"] = {
        "metric": "end_effector_position_error",
        "unit": "m",
        "kind": "final_body_position_error",
        "parameters": {
            "body_name": "tool",
            "target_argument": "request.target_position_m",
        },
    }

    with mock.patch.object(harness_runner, "_run_worker") as worker:
        with pytest.raises(HarnessError, match="numeric array schema"):
            run_private_suite(
                package=package,
                design=design,
                suite=suite,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=tmp_path / "object-target-report",
                record_video=False,
            )
    worker.assert_not_called()


_KINOVA_ASSETS = (
    Path(__file__).resolve().parents[1]
    / "libraries"
    / "robots"
    / "kinova_gen3_robotiq_2f85"
    / "1.0.0"
    / "assets"
)


def _kinova_geom_pair_binding(kind: str, *, metric: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "geom_a_name": "left_pad1",
        "geom_b_name": "right_pad1",
    }
    if kind == "final_geom_pair_distance_error":
        parameters["target_argument"] = "request.target_aperture"
    return {
        "metric": metric,
        "unit": "m",
        "kind": kind,
        "parameters": parameters,
    }


def _kinova_aperture_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target_aperture": {
                "type": "number",
                "unit": "m",
                "frame": "gripper_base",
                "minimum": 0.0,
                "maximum": 0.085,
            }
        },
        "required": ["target_aperture"],
        "additionalProperties": False,
    }


def _settled_kinova_gripper_evidence(control: float) -> dict[str, Any]:
    import mujoco

    scene_path = _KINOVA_ASSETS / "pick_place_scene.xml"
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    actuator_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_ACTUATOR, "fingers_actuator"
    )
    data.ctrl[actuator_id] = control
    for _ in range(400):
        mujoco.mj_step(model, data)
    sample = {
        "qpos": data.qpos.tolist(),
        # Parent enrichment must overwrite, never trust, this worker-side field.
        "trusted_geom_pair_distances": [
            {
                "geom_a_name": "left_pad1",
                "geom_b_name": "right_pad1",
                "distance_m": 99.0,
            }
        ],
    }
    return {"samples": [sample, dict(sample)]}


def test_real_kinova_geom_distance_measures_aperture_without_ivc_mapping() -> None:
    scene_path = _KINOVA_ASSETS / "pick_place_scene.xml"
    set_binding = audit_inline_measurement_binding(
        _kinova_geom_pair_binding(
            "final_geom_pair_distance_error", metric="gripper_aperture_error"
        ),
        criterion={
            "metric": "gripper_aperture_error",
            "unit": "m",
            "comparator": "<=",
            "threshold": 0.003,
        },
        request_schema=_kinova_aperture_schema(),
        scene_path=scene_path,
    )
    actual_binding = audit_inline_measurement_binding(
        _kinova_geom_pair_binding(
            "final_geom_pair_distance", metric="gripper_aperture_at_grasp"
        ),
        criterion={
            "metric": "gripper_aperture_at_grasp",
            "unit": "m",
            "comparator": "between",
            "threshold": [-0.001, 0.086],
        },
        request_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        scene_path=scene_path,
    )
    open_evidence = harness_runner._trusted_measurement_evidence(
        actual_binding,
        _settled_kinova_gripper_evidence(0.0),
        scene_path=scene_path,
    )
    closed_evidence = harness_runner._trusted_measurement_evidence(
        actual_binding,
        _settled_kinova_gripper_evidence(255.0),
        scene_path=scene_path,
    )
    open_distance = measure(
        actual_binding, evidence=open_evidence, public_arguments={"request": {}}
    )
    closed_distance = measure(
        actual_binding, evidence=closed_evidence, public_arguments={"request": {}}
    )
    assert open_distance == pytest.approx(0.08518, abs=5.0e-4)
    assert closed_distance == pytest.approx(0.0, abs=5.0e-4)
    assert open_distance > closed_distance + 0.08
    assert measure(
        set_binding,
        evidence=open_evidence,
        public_arguments={"request": {"target_aperture": 0.085}},
    ) < 5.0e-4
    assert measure(
        set_binding,
        evidence=closed_evidence,
        public_arguments={"request": {"target_aperture": 0.0}},
    ) < 5.0e-4


def test_real_kinova_geom_distance_reaches_private_harness_verdict(
    tmp_path: Path,
) -> None:
    package = load_robot_package(_KINOVA_ASSETS.parent)
    candidate = tmp_path / "driver.py"
    candidate.write_text(
        textwrap.dedent(
            """
            import mujoco

            class Driver:
                def __init__(self, model, data):
                    self.model = model
                    self.data = data

                def set_gripper_aperture(self, request):
                    self.data.ctrl[0] = 0.0
                    mujoco.mj_step(self.model, self.data)

            def build(*, model, data):
                return Driver(model, data)
            """
        ),
        encoding="utf-8",
    )
    criterion = {
        "metric": "gripper_aperture_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.003,
        "temporal": {"kind": "terminal_state"},
        "aggregation": {"kind": "single_trial"},
    }
    design = {
        "capabilities": [
            {
                "capability_id": "set-gripper",
                "method_name": "set_gripper_aperture",
                "request_schema": _kinova_aperture_schema(),
                "criteria": [criterion],
            }
        ]
    }
    case = {
        "case_id": "set-gripper-open",
        "case_role": "nominal",
        "capability_id": "set-gripper",
        "method_name": "set_gripper_aperture",
        "request": {"target_aperture": 0.085},
        "request_grounding_refs": [],
        "instance_id": "kinova-gen3-robotiq-2f85-mw_pick_place",
        "measurement_binding": _kinova_geom_pair_binding(
            "final_geom_pair_distance_error", metric="gripper_aperture_error"
        ),
        "guard_ids": [
            "guard_actuator_and_physics_step",
            "guard_no_direct_state_write",
            "guard_canonical_model_data",
        ],
        "repetitions": 1,
        "timeout_sim_s": 20.0,
        "criteria": [criterion],
    }
    suite = {
        "artifact_type": "capability_validation_suite",
        "capability_protocol_version": "capability-v2",
        "whole_suite_aggregation": {"kind": "all_cases"},
        "cases": [case],
    }
    worker_result = _worker_result()
    samples = _settled_kinova_gripper_evidence(0.0)["samples"]
    worker_result["physical_evidence"]["samples"] = [
        {"time": float(index), **sample} for index, sample in enumerate(samples)
    ]

    with mock.patch.object(
        harness_runner, "_run_worker", return_value=worker_result
    ) as worker:
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=candidate,
            condition="from-scratch",
            output_dir=tmp_path / "kinova-geom-report",
            record_video=False,
        )

    assert report["validation_passed"] is True
    assert report["trials"][0]["measurement_value"] < 5.0e-4
    assert "measurement_binding" not in json.dumps(worker.call_args.args[0])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda binding, schema, criterion: binding["parameters"].update(
                geom_a_name="not_a_geom"
            ),
            "unknown geom",
        ),
        (
            lambda binding, schema, criterion: binding["parameters"].update(
                geom_b_name="left_pad1"
            ),
            "two distinct geoms",
        ),
        (
            lambda binding, schema, criterion: schema["properties"][
                "target_aperture"
            ].update(maximum=0.0),
            "finite increasing bounds",
        ),
        (
            lambda binding, schema, criterion: schema["properties"][
                "target_aperture"
            ].update(unit="rad"),
            "must declare unit='m'",
        ),
    ],
)
def test_kinova_geom_pair_binding_fails_closed_before_measurement(
    mutation: Any, message: str
) -> None:
    binding = _kinova_geom_pair_binding(
        "final_geom_pair_distance_error", metric="gripper_aperture_error"
    )
    schema = _kinova_aperture_schema()
    criterion = {
        "metric": "gripper_aperture_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.003,
    }
    mutation(binding, schema, criterion)
    with pytest.raises(MeasurementOperatorError, match=message):
        audit_inline_measurement_binding(
            binding,
            criterion=criterion,
            request_schema=schema,
            scene_path=_KINOVA_ASSETS / "pick_place_scene.xml",
        )


def _kinova_frame_schema() -> dict[str, Any]:
    def scalar(unit: str) -> dict[str, Any]:
        return {
            "type": "number",
            "unit": unit,
            "frame": "robot_base",
            "minimum": -2.0,
            "maximum": 2.0,
        }

    return {
        "type": "object",
        "properties": {
            "target_position": {
                "type": "object",
                "properties": {axis: scalar("m") for axis in ("x", "y", "z")},
                "required": ["x", "y", "z"],
                "additionalProperties": False,
            },
            "direction_unit_vector": {
                "type": "object",
                "properties": {
                    axis: scalar("dimensionless") for axis in ("dx", "dy", "dz")
                },
                "required": ["dx", "dy", "dz"],
                "additionalProperties": False,
            },
            "pivot_point": {
                "type": "object",
                "properties": {axis: scalar("m") for axis in ("x", "y", "z")},
                "required": ["x", "y", "z"],
                "additionalProperties": False,
            },
            "rotation_axis": {
                "type": "object",
                "properties": {
                    axis: scalar("dimensionless") for axis in ("ax", "ay", "az")
                },
                "required": ["ax", "ay", "az"],
                "additionalProperties": False,
            },
            "angular_displacement": scalar("rad"),
        },
        "required": [
            "target_position",
            "direction_unit_vector",
            "pivot_point",
            "rotation_axis",
            "angular_displacement",
        ],
        "additionalProperties": False,
    }


def _frame_binding(kind: str, *, metric: str, entity: str) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        ("site_name" if entity == "pinch_site" else "body_name"): entity,
        "reference_body_name": "base_link",
    }
    if kind.endswith("position_error"):
        parameters.update(
            target_x_argument="request.target_position.x",
            target_y_argument="request.target_position.y",
            target_z_argument="request.target_position.z",
        )
    elif kind == "site_frame_xyz_directional_displacement":
        parameters.update(
            direction_x_argument="request.direction_unit_vector.dx",
            direction_y_argument="request.direction_unit_vector.dy",
            direction_z_argument="request.direction_unit_vector.dz",
        )
    else:
        parameters.update(
            center_x_argument="request.pivot_point.x",
            center_y_argument="request.pivot_point.y",
            center_z_argument="request.pivot_point.z",
            axis_x_argument="request.rotation_axis.ax",
            axis_y_argument="request.rotation_axis.ay",
            axis_z_argument="request.rotation_axis.az",
            target_angle_argument="request.angular_displacement",
        )
    return {
        "metric": metric,
        "unit": "rad" if "arc_angle" in kind else "m",
        "kind": kind,
        "parameters": parameters,
    }


def test_real_kinova_frame_contracts_audit_exact_site_body_and_reference() -> None:
    schema = _kinova_frame_schema()
    cases = [
        (
            _frame_binding(
                "final_site_frame_xyz_position_error",
                metric="euclidean_distance_tcp_to_target",
                entity="pinch_site",
            ),
            "reach_scene.xml",
        ),
        (
            _frame_binding(
                "site_frame_xyz_directional_displacement",
                metric="tcp_displacement_along_direction",
                entity="pinch_site",
            ),
            "push_to_goal_scene.xml",
        ),
        (
            _frame_binding(
                "accumulated_site_frame_axis_arc_angle_error",
                metric="angular_displacement_error",
                entity="pinch_site",
            ),
            "dial_scene.xml",
        ),
        (
            _frame_binding(
                "final_body_frame_xyz_position_error",
                metric="euclidean_distance_object_to_goal",
                entity="workpiece",
            ),
            "pick_place_scene.xml",
        ),
    ]
    for binding, scene_name in cases:
        assert audit_inline_measurement_binding(
            binding,
            criterion={"metric": binding["metric"], "unit": binding["unit"]},
            request_schema=schema,
            scene_path=_KINOVA_ASSETS / scene_name,
        ) == binding


def test_robot_base_alias_is_kinematic_and_aloha_tie_fails_closed() -> None:
    kinova = inspect_scene_entities(_KINOVA_ASSETS / "reach_scene.xml")
    assert kinova["frame_aliases"]["robot_base"] == "base_link"
    assert kinova["body_parent_names"]["base_link"] == "world"
    assert kinova["body_joint_counts"]["base_link"] == 0
    assert kinova["body_descendant_joint_counts"]["base_link"] > 0

    aloha_scene = (
        Path(__file__).resolve().parents[1]
        / "libraries"
        / "robots"
        / "aloha_2"
        / "1.0.0"
        / "assets"
        / "reach_scene.xml"
    )
    aloha = inspect_scene_entities(aloha_scene)
    assert "robot_base" not in aloha["frame_aliases"]
    assert aloha["body_descendant_joint_counts"]["left/base_link"] == 8
    assert aloha["body_descendant_joint_counts"]["right/base_link"] == 8


def test_real_kinova_pinch_moves_in_base_link_but_is_rigid_in_gripper_base() -> None:
    import mujoco
    import numpy as np

    model = mujoco.MjModel.from_xml_path(str(_KINOVA_ASSETS / "reach_scene.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "pinch_site")

    def site_in_frame(body_name: str) -> Any:
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        rotation = data.xmat[body_id].reshape(3, 3)
        return rotation.T @ (data.site_xpos[site_id] - data.xpos[body_id])

    initial_robot_base = site_in_frame("base_link").copy()
    initial_gripper_base = site_in_frame("base").copy()
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "joint_2")
    actuator_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_ACTUATOR, "joint_2"
    )
    data.ctrl[actuator_id] = float(data.qpos[model.jnt_qposadr[joint_id]]) + 0.4
    for _ in range(500):
        mujoco.mj_step(model, data)

    assert np.linalg.norm(site_in_frame("base_link") - initial_robot_base) > 0.2
    assert np.linalg.norm(site_in_frame("base") - initial_gripper_base) < 1.0e-9


def test_kinova_rigid_gripper_base_is_rejected_as_request_frame() -> None:
    schema = _kinova_frame_schema()
    for leaf in schema["properties"]["target_position"]["properties"].values():
        leaf["frame"] = "base"
    binding = _frame_binding(
        "final_site_frame_xyz_position_error",
        metric="euclidean_distance_tcp_to_target",
        entity="pinch_site",
    )
    binding["parameters"]["reference_body_name"] = "base"
    with pytest.raises(MeasurementOperatorError, match="rigid relative"):
        audit_inline_measurement_binding(
            binding,
            criterion={"metric": binding["metric"], "unit": "m"},
            request_schema=schema,
            scene_path=_KINOVA_ASSETS / "reach_scene.xml",
        )


def _kinova_frame_evidence(
    *,
    site_positions: list[tuple[float, float, float]],
    body_positions: list[tuple[float, float, float]] | None = None,
) -> dict[str, Any]:
    if body_positions is None:
        body_positions = [(0.0, 0.0, 0.0)] * len(site_positions)
    return {
        "samples": [
            {
                "time": float(index),
                "site_positions": {"pinch_site": list(site_position)},
                "body_positions": {
                    "base_link": [0.0, 0.0, 0.0],
                    "workpiece": list(body_position),
                },
                "body_quaternions": {"base_link": [1.0, 0.0, 0.0, 0.0]},
            }
            for index, (site_position, body_position) in enumerate(
                zip(site_positions, body_positions)
            )
        ]
    }


def test_kinova_frame_measurements_use_site_and_body_evidence() -> None:
    target_request = {
        "request": {"target_position": {"x": 0.4, "y": -0.1, "z": 0.2}}
    }
    evidence = _kinova_frame_evidence(
        site_positions=[(0.0, 0.0, 0.0), (0.4, -0.1, 0.2)],
        body_positions=[(0.0, 0.0, 0.0), (0.4, -0.1, 0.2)],
    )
    site_binding = _frame_binding(
        "final_site_frame_xyz_position_error",
        metric="euclidean_distance_tcp_to_target",
        entity="pinch_site",
    )
    body_binding = _frame_binding(
        "final_body_frame_xyz_position_error",
        metric="euclidean_distance_object_to_goal",
        entity="workpiece",
    )
    assert measure(
        site_binding, evidence=evidence, public_arguments=target_request
    ) == pytest.approx(0.0)
    assert measure(
        body_binding, evidence=evidence, public_arguments=target_request
    ) == pytest.approx(0.0)

    direction_binding = _frame_binding(
        "site_frame_xyz_directional_displacement",
        metric="tcp_displacement_along_direction",
        entity="pinch_site",
    )
    assert measure(
        direction_binding,
        evidence=_kinova_frame_evidence(
            site_positions=[(0.0, 0.0, 0.0), (0.2, 0.0, 0.0)]
        ),
        public_arguments={
            "request": {
                "direction_unit_vector": {"dx": 1.0, "dy": 0.0, "dz": 0.0}
            }
        },
    ) == pytest.approx(0.2)

    arc_binding = _frame_binding(
        "accumulated_site_frame_axis_arc_angle_error",
        metric="angular_displacement_error",
        entity="pinch_site",
    )
    assert measure(
        arc_binding,
        evidence=_kinova_frame_evidence(
            site_positions=[
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (-1.0, 0.0, 0.0),
            ]
        ),
        public_arguments={
            "request": {
                "pivot_point": {"x": 0.0, "y": 0.0, "z": 0.0},
                "rotation_axis": {"ax": 0.0, "ay": 0.0, "az": 1.0},
                "angular_displacement": math.pi,
            }
        },
    ) == pytest.approx(0.0)


def test_kinova_frame_contracts_fail_closed_on_frame_unit_entity_and_vector() -> None:
    schema = _kinova_frame_schema()
    position = _frame_binding(
        "final_site_frame_xyz_position_error",
        metric="euclidean_distance_tcp_to_target",
        entity="pinch_site",
    )
    position["parameters"]["site_name"] = "bracelet_link"
    with pytest.raises(MeasurementOperatorError, match="unknown site"):
        audit_inline_measurement_binding(
            position,
            criterion={"metric": position["metric"], "unit": "m"},
            request_schema=schema,
            scene_path=_KINOVA_ASSETS / "reach_scene.xml",
        )

    position["parameters"]["site_name"] = "pinch_site"
    schema["properties"]["target_position"]["properties"]["z"]["frame"] = "world"
    with pytest.raises(MeasurementOperatorError, match="one common non-empty frame"):
        audit_inline_measurement_binding(
            position,
            criterion={"metric": position["metric"], "unit": "m"},
            request_schema=schema,
            scene_path=_KINOVA_ASSETS / "reach_scene.xml",
        )

    direction = _frame_binding(
        "site_frame_xyz_directional_displacement",
        metric="tcp_displacement_along_direction",
        entity="pinch_site",
    )
    with pytest.raises(MeasurementError, match="must have unit length"):
        measure(
            direction,
            evidence=_kinova_frame_evidence(
                site_positions=[(0.0, 0.0, 0.0), (0.2, 0.0, 0.0)]
            ),
            public_arguments={
                "request": {
                    "direction_unit_vector": {"dx": 2.0, "dy": 0.0, "dz": 0.0}
                }
            },
        )


def test_final_site_position_error_rejects_object_target_before_worker(
    tmp_path: Path,
) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    design["capabilities"][0]["request_schema"] = _xyz_object_schema(
        "target_position_m", unit="m"
    )
    suite["cases"][0]["request"] = {
        "target_position_m": {"x": 0.4, "y": 0.0, "z": 0.2}
    }
    suite["cases"][0]["measurement_binding"] = {
        "metric": "end_effector_position_error",
        "unit": "m",
        "kind": "final_site_position_error",
        "parameters": {
            "site_name": "tool_site",
            "target_argument": "request.target_position_m",
        },
    }
    with mock.patch.object(harness_runner, "_run_worker") as worker:
        with pytest.raises(HarnessError, match="numeric array schema"):
            run_private_suite(
                package=package,
                design=design,
                suite=suite,
                driver_path=candidate,
                condition="from-scratch",
                output_dir=tmp_path / "site-object-target-report",
                record_video=False,
            )
    worker.assert_not_called()
