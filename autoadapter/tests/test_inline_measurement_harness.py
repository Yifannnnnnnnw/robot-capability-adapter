from __future__ import annotations

import copy
import json
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
    measurement_operator_catalog,
)
from autoadapter2.libraries import RobotPackage


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
