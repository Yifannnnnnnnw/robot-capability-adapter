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
from autoadapter2.harness.operators import measurement_operator_catalog
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
    kinds = {operator["kind"] for operator in catalog["operators"]}
    assert "final_site_position_error" in kinds
    assert "mean_body_yaw_rate" in kinds
    assert "b1_contract" not in kinds
    assert catalog["binding_fields"] == ["metric", "unit", "kind", "parameters"]
