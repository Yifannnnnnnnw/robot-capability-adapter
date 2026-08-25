from __future__ import annotations

import json
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from autoadapter2.harness import HarnessError, run_private_suite
from autoadapter2.harness import runner as harness_runner
from autoadapter2.libraries import RobotPackage
from autoadapter2.validation_compiler.ivc import (
    IVCError,
    _private_inputs_from_package,
)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value)), encoding="utf-8")


def _worker_result() -> dict[str, Any]:
    return {
        "worker_completed": True,
        "method_invoked": True,
        "canonical_model_data": True,
        "candidate_exception": None,
        "candidate_log": "",
        "physical_evidence": {
            "step_count": 4,
            "ctrl_observed_before_step": True,
            "ctrl_changed_from_reset": True,
            "direct_state_write_detected": False,
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": None,
            "contact_pair_min_distances": [],
            "samples": [{"time": 0.0}, {"time": 0.1}],
        },
        "video": {"requested": False, "complete": False, "frame_count": 0},
    }


def _fixture(tmp_path: Path) -> tuple[RobotPackage, Path, dict[str, Any], dict[str, Any]]:
    package_root = tmp_path / "package"
    (package_root / "assets").mkdir(parents=True)
    (package_root / "assets" / "scene.xml").write_text("<mujoco/>", encoding="utf-8")
    legacy = package_root / "tasks" / "private"
    capability = package_root / "capability_validation" / "private"
    guards = [
        {"guard_id": "control", "kind": "actuator_and_physics_step_required"},
        {"guard_id": "state", "kind": "no_direct_state_write"},
        {"guard_id": "canonical", "kind": "canonical_model_data"},
    ]
    _write(
        legacy / "instances.json",
        {
            "instances": [
                {
                    "instance_id": "legacy-task-instance",
                    "task_id": "private-task-identifier",
                    "clause_bindings": {"task": "legacy-task-binding"},
                    "guard_ids": ["control", "state", "canonical"],
                    "repetitions": 1,
                    "timeout_sim_s": 1.0,
                }
            ]
        },
    )
    _write(
        legacy / "bindings.json",
        {"bindings": [{"binding_id": "legacy-task-binding", "kind": "unused"}]},
    )
    _write(legacy / "guards.json", {"guards": guards})
    _write(
        capability / "instances.json",
        {
            "instances": [
                {
                    "instance_id": "cap-a-nominal",
                    "capability_id": "cap-a",
                    "case_role": "nominal",
                    "scene_entrypoint": "assets/scene.xml",
                    "public_arguments": {
                        "request": {
                            "task_id": "SECRET-private-task",
                            "task_parameters": {"target": [0.4, 0.0, 0.2]},
                        }
                    },
                    "clause_bindings": {"criterion": "cap-a-binding"},
                    "guard_ids": ["control", "state", "canonical"],
                    "repetitions": 1,
                    "timeout_sim_s": 1.0,
                    "max_steps": 20,
                }
            ]
        },
    )
    _write(
        capability / "bindings.json",
        {
            "bindings": [
                {
                    "binding_id": "cap-a-binding",
                    "capability_id": "cap-a",
                    "case_role": "nominal",
                    "metric": "end_effector_position_error",
                    "unit": "m",
                    "kind": "b1_contract",
                    "parameters": {"contract_id": "A1"},
                }
            ]
        },
    )
    _write(capability / "guards.json", {"guards": []})

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
                    self.data.ctrl[0] = float(request["target_position_m"][0])
                    mujoco.mj_step(self.model, self.data)

            def build(*, model, data):
                return Driver(model, data)
            """
        ),
        encoding="utf-8",
    )
    reference_driver = tmp_path / "reference" / "driver.py"
    reference_driver.parent.mkdir()
    reference_driver.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
    package = RobotPackage(
        root=package_root,
        robot_configuration_id="test-arm",
        package_version="1.0.0",
        snapshot_id="snapshot-1",
        morphology={"mjcf_entrypoint": "assets/scene.xml"},
        sources=(),
        tasks=(),
        mjcf_path=package_root / "assets" / "scene.xml",
        skeleton_dir=package_root / "skeleton",
        reference_driver=reference_driver,
        private_dir=legacy,
    )
    criterion = {
        "metric": "end_effector_position_error",
        "unit": "m",
        "comparator": "<=",
        "threshold": 0.015,
        "temporal": {"kind": "continuous", "duration_s": 0.5},
        "aggregation": {"kind": "all_samples"},
        "source_refs": [
            {"source_id": "calibration", "specific_reference": "real threshold"}
        ],
    }
    design = {
        "capabilities": [
            {
                "capability_id": "cap-a",
                "method_name": "move_target",
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
                "case_id": "cap-a-nominal-case",
                "case_role": "nominal",
                "capability_id": "cap-a",
                "method_name": "move_target",
                "instance_id": "cap-a-nominal",
                "binding_id": "cap-a-binding",
                "guard_ids": ["control", "state", "canonical"],
                "repetitions": 1,
                "timeout_sim_s": 1.0,
                "request": {
                    "target_position_m": [0.45, 0.0, 0.2],
                    "max_duration_s": 2.0,
                },
                "criteria": [criterion],
            }
        ],
    }
    return package, candidate, design, suite


def test_capability_candidate_receives_only_native_request_and_plural_criterion_executes(
    tmp_path: Path,
) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    payloads: list[dict[str, Any]] = []

    def worker(payload: Mapping[str, Any], **_kwargs: Any) -> dict[str, Any]:
        payloads.append(dict(payload))
        return _worker_result()

    with (
        mock.patch.object(harness_runner, "_run_worker", side_effect=worker),
        mock.patch.object(harness_runner, "measure", return_value=1.0),
        mock.patch.object(
            harness_runner,
            "_resolve_b1_body_geom_symbols",
            wraps=harness_runner._resolve_b1_body_geom_symbols,
        ) as resolver,
    ):
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=candidate,
            condition="from-scratch",
            output_dir=tmp_path / "candidate-report",
            record_video=False,
        )

    assert payloads[0]["public_arguments"] == {"request": suite["cases"][0]["request"]}
    assert "task_id" not in json.dumps(report)
    assert resolver.call_count == 1
    assert report["validation_passed"] is True
    assert report["trials"][0]["temporal_evidence"]["kind"] == "trusted_b1_contract"
    assert report["trials"][0]["aggregation_value"] == 1.0


def test_trusted_reference_keeps_private_task_dispatch_but_measures_native_request(
    tmp_path: Path,
) -> None:
    package, candidate, design, suite = _fixture(tmp_path)
    payloads: list[dict[str, Any]] = []
    measurement_arguments: list[Mapping[str, Any]] = []

    def worker(payload: Mapping[str, Any], **_kwargs: Any) -> dict[str, Any]:
        payloads.append(dict(payload))
        return _worker_result()

    def measured(
        _binding: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any],
        public_arguments: Mapping[str, Any],
    ) -> float:
        del evidence
        measurement_arguments.append(public_arguments)
        return 1.0

    with (
        mock.patch.object(harness_runner, "_run_worker", side_effect=worker),
        mock.patch.object(harness_runner, "measure", side_effect=measured),
    ):
        report = run_private_suite(
            package=package,
            design=design,
            suite=suite,
            driver_path=candidate,
            condition="from-scratch",
            output_dir=tmp_path / "reference-report",
            record_video=False,
            trusted_reference_driver=True,
        )

    assert payloads[0]["public_arguments"]["request"]["task_id"] == "SECRET-private-task"
    assert measurement_arguments == [{"request": suite["cases"][0]["request"]}]
    assert report["validation_passed"] is True


def test_private_capability_bank_is_closed_except_for_shared_guards(tmp_path: Path) -> None:
    package, _candidate, _design, _suite = _fixture(tmp_path)
    merged = _private_inputs_from_package(package)
    assert {item["instance_id"] for item in merged["instances"]["instances"]} == {
        "cap-a-nominal",
    }
    assert {item["binding_id"] for item in merged["bindings"]["bindings"]} == {
        "cap-a-binding",
    }
    assert {item["guard_id"] for item in merged["guards"]["guards"]} == {
        "control",
        "state",
        "canonical",
    }

    assert set(
        harness_runner._merged_private_index(
            package,
            "instances",
            "instance_id",
            capability_v2=True,
        )
    ) == {"cap-a-nominal"}
    assert set(
        harness_runner._merged_private_index(
            package,
            "instances",
            "instance_id",
            capability_v2=False,
        )
    ) == {"legacy-task-instance"}

    capability_guards = package.root / "capability_validation" / "private" / "guards.json"
    _write(
        capability_guards,
        {"guards": [{"guard_id": "control", "kind": "duplicate"}]},
    )
    with pytest.raises(IVCError, match="conflicting ID"):
        _private_inputs_from_package(package)
    with pytest.raises(HarnessError, match="conflicting IDs"):
        harness_runner._merged_private_index(
            package,
            "guards",
            "guard_id",
            capability_v2=True,
        )
