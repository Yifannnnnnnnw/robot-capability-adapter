from __future__ import annotations

import copy
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from autoadapter2.demo import DemoTask, ValidationEvidence
from autoadapter2.evaluation import (
    EncodedVideo,
    FrozenVideoProfile,
    OpaqueVideoHandle,
    RGBFrame,
)
from autoadapter2.foundation.errors import ContractError
from autoadapter2.foundation.hashing import sha256_bytes
from autoadapter2.generation import FixtureJsonGenerator
from autoadapter2.integration import (
    READINESS_CHECK_IDS,
    stable_json_sha256,
    write_stable_json,
)
from autoadapter2.libraries import TasksLibrary
from autoadapter2.orchestration import DemoModelAdapters, DemoRunPlan, GeneralDemoRunner
import autoadapter2.orchestration.demo_runner as demo_runner_module
from autoadapter2.orchestration.demo_runner import _field_schema
from autoadapter2.validation import MeasurementSample, RepairConfig, ValidationAProfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}


def test_field_schema_maps_float_and_preserves_fixed_shapes() -> None:
    assert _field_schema({"type": "float", "shape": "scalar"}) == {"type": "number"}
    assert _field_schema({"type": "number", "shape": "scalar"}) == {"type": "number"}
    assert _field_schema({"type": "number", "shape": "vector:4"}) == {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 4,
        "maxItems": 4,
    }
    for shape, length in (("[2]", 2), ("[3]", 3)):
        assert _field_schema({"type": "array", "shape": shape}) == {
            "type": "array",
            "items": {"type": "number"},
            "minItems": length,
            "maxItems": length,
        }

    for field in (
        {"type": "array", "shape": "[]"},
        {"type": "array", "shape": "[0]"},
        {"type": "array", "shape": "[-1]"},
        {"type": "array", "shape": "[3"},
        {"type": "array", "shape": "3]"},
        {"type": "array", "shape": "[3.0]"},
        {"type": "array", "shape": "[abc]"},
        {"type": "number", "shape": "vector:0"},
        {"type": "number", "shape": "vector:invalid"},
    ):
        with pytest.raises(ContractError):
            _field_schema(field)


def _copy(root: Path, relative: str) -> None:
    source = PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _json_ref(root: Path, relative: str, value: object) -> dict[str, str]:
    return {"path": relative, "sha256": write_stable_json(root / relative, value)}


def _bytes_ref(root: Path, relative: str, payload: bytes) -> dict[str, str]:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {"path": relative, "sha256": sha256_bytes(payload)}


def _ready_robot_run(root: Path, robot: str) -> tuple[str, str, str, dict[str, Any]]:
    manifest_rel = f"general_demo/integrations/{robot}/integration_manifest.json"
    manifest = json.loads((PROJECT_ROOT / manifest_rel).read_text(encoding="utf-8"))
    records: dict[str, dict[str, Any]] = {}
    for name in ("morphology_ref", "sdk_ref", "translation_ref"):
        relative = manifest[name]["path"]
        records[name] = json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8"))

    morphology = records["morphology_ref"]
    morphology["mujoco"]["asset_closure_status"] = "VERIFIED"
    manifest["morphology_ref"] = _json_ref(root, manifest["morphology_ref"]["path"], morphology)

    sdk = records["sdk_ref"]
    sdk["runtime"]["container_digest_status"] = "VERIFIED"
    manifest["sdk_ref"] = _json_ref(root, manifest["sdk_ref"]["path"], sdk)

    translation = records["translation_ref"]
    if robot == "so-arm101":
        implementation_refs = list(translation["implementation"]["source_files"])
        implementation_refs.append(translation["implementation"]["readiness_runner"])
    else:
        implementation_refs = []
        for relative in (
            "general_demo/src/autoadapter2/integrations/unitree_go2/bridge.py",
            "general_demo/src/autoadapter2/integrations/unitree_go2/readiness.py",
        ):
            _copy(root, relative)
            implementation_refs.append(
                {"path": relative, "sha256": sha256_bytes((root / relative).read_bytes())}
            )
        translation["implementation"] = {
            "source_files": implementation_refs,
            "readiness_runner": None,
        }
    for reference in implementation_refs:
        if not (root / reference["path"]).exists():
            _copy(root, reference["path"])
    translation.update(status="READY", conformance_status="PASS", unresolved=[])
    manifest["translation_ref"] = _json_ref(
        root, manifest["translation_ref"]["path"], translation
    )

    profile_rel = (
        "general_demo/contracts/profiles/readiness/"
        "general-demo-integration-readiness/1.0.0/profile.json"
    )
    profile = json.loads((PROJECT_ROOT / profile_rel).read_text(encoding="utf-8"))
    profile_ref = _json_ref(root, profile_rel, profile)
    manifest.update(status="READY", readiness_profile_ref=profile_ref, unresolved_gaps=[])
    manifest["runtime"]["lock_sha256"] = "a" * 64
    for check in manifest["compatibility_checks"]:
        check["verdict"] = "PASS"
    manifest_ref = _json_ref(root, manifest_rel, manifest)

    run_id = f"run-{robot}"
    input_ref = _json_ref(root, f"general_demo/runs/{run_id}/input.json", {"frozen": True})
    checks = [
        {
            "check_id": check_id,
            "verdict": "PASS",
            "evidence_refs": [
                _bytes_ref(
                    root,
                    f"general_demo/runs/{run_id}/evidence/{check_id}.txt",
                    check_id.encode(),
                )
            ],
        }
        for check_id in READINESS_CHECK_IDS
    ]
    report = {
        "schema_version": "1.0.0",
        "attempt_id": f"attempt-{robot}",
        "run_id": run_id,
        "integration_manifest_ref": manifest_ref,
        "runtime_sha256": stable_json_sha256(manifest["runtime"]),
        "readiness_profile_ref": profile_ref,
        "environment_fingerprint_sha256": "b" * 64,
        "dependency_sha256": {
            "morphology": manifest["morphology_ref"]["sha256"],
            "sdk": manifest["sdk_ref"]["sha256"],
            "translation": manifest["translation_ref"]["sha256"],
            "runtime_lock": manifest["runtime"]["lock_sha256"],
            "readiness_profile": profile_ref["sha256"],
        },
        "time_limits": copy.deepcopy(profile["time_limits"]),
        "numerical_tolerances": copy.deepcopy(profile["numerical_tolerances"]),
        "checks": checks,
        "cleanup": {
            "verdict": "PASS",
            "evidence_refs": [
                _bytes_ref(
                    root,
                    f"general_demo/runs/{run_id}/evidence/cleanup.txt",
                    b"cleanup",
                )
            ],
        },
        "started_at": "2026-08-11T00:00:00Z",
        "ended_at": "2026-08-11T00:00:01Z",
        "verdict": "PASS",
    }
    report_rel = f"general_demo/runs/{run_id}/readiness_report.json"
    report_ref = _json_ref(root, report_rel, report)
    snapshot = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "integration_manifest_ref": manifest_ref,
        "readiness_report_ref": report_ref,
        "runtime_sha256": stable_json_sha256(manifest["runtime"]),
        "readiness_profile_ref": profile_ref,
        "library_view_refs": [input_ref],
        "task_set_ref": input_ref,
        "g2_profile_ref": input_ref,
        "observation_profile_ref": input_ref,
        "model_prompt_config_ref": input_ref,
        "budget_ref": input_ref,
        "blue_line_input_refs": [input_ref],
        "sealed_artifact_refs": [],
    }
    snapshot_rel = f"general_demo/runs/{run_id}/run_snapshot.json"
    _json_ref(root, snapshot_rel, snapshot)
    projection = {
        "robot_model_id": manifest["robot_model_id"],
        "robot_configuration_id": manifest["robot_configuration_id"],
        "action_affordances": ["joint target command"],
        "observation_affordances": ["joint position observation"],
        "unit_allowlist": ["rad", "none"],
        "frame_allowlist": ["joint", "none"],
    }
    return manifest_rel, snapshot_rel, report_rel, projection


class _Encoder:
    def encode(self, _profile, frames):
        return EncodedVideo(OpaqueVideoHandle("fixture-video"), b"encoded:" + bytes([len(frames)]))


class _Sdk:
    def __init__(self, owner: "_RobotSession") -> None:
        self._owner = owner

    def command(self, target: list[float]) -> None:
        self._owner.target = list(target)
        self._owner.time_s = 0.2


class _RobotSession:
    evidence_scope = "TEST_FIXTURE_ONLY"

    def __init__(self, width: int, robot: str) -> None:
        self.width = width
        self.robot_model_id = robot
        self.robot_configuration_id = (
            "so-arm101-follower-stock-gripper"
            if robot == "so-arm101" else "unitree-go2-stock-12dof"
        )
        self.time_s = 0.0
        self.target = [0.0] * width
        self.sdk = _Sdk(self)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    @property
    def simulation_time_s(self) -> float:
        return self.time_s

    def reset(self, *, phase: str, execution_id: str, initial_state) -> None:
        del phase, execution_id, initial_state
        self.time_s = 0.0

    def start_external_recording(self, *, phase: str, execution_id: str) -> None:
        del phase, execution_id

    def stop_external_recording(self) -> tuple[RGBFrame, ...]:
        rgb = b"\x00" * (2 * 2 * 3)
        return (RGBFrame(0.0, 2, 2, rgb), RGBFrame(0.2, 2, 2, rgb))

    def validation_evidence(self, invocation) -> ValidationEvidence:
        samples = (MeasurementSample(0.0, 0.01), MeasurementSample(0.2, 0.01))
        criteria = tuple(getattr(invocation, "criteria", ()))
        criterion_samples = {
            criterion["criterion_id"]: samples
            for criterion in criteria
            if isinstance(criterion, dict)
            and isinstance(criterion.get("criterion_id"), str)
        } or None
        route_detail = {
            "robot_model_id": self.robot_model_id,
            "candidate_invocation_observed": True,
            "accepted_command_count": 1,
            "simulation_time_progressed": True,
            "state_route_observed": True,
            "verified": True,
        }
        if self.robot_model_id == "so-arm101":
            route_detail.update({
                "present_position_qpos_consistent": True,
                "present_position_qpos_max_error_ticks": 0,
            })
        return ValidationEvidence(
            samples=samples,
            elapsed_s=0.2,
            guard_results={"physical-state-not-command-receipt": True},
            sdk_route_verified=True,
            route_evidence=route_detail,
            criterion_samples=criterion_samples,
        )

    def demo_evidence(self, _task_id: str) -> dict[str, bool]:
        return {"passed": True}

    def invoke(self, candidate, capability_id: str, arguments):
        return candidate._invoke(capability_id, arguments, self.sdk)


def _design(width: int, requirement_ids: list[str]) -> dict[str, Any]:
    return {
        "capabilities": [{
            "capability_id": "set-joint-configuration",
            "kind": "action",
            "requirement_ids": requirement_ids,
            "inputs": [{
                "name": "target", "type": "number", "shape": f"vector:{width}",
                "unit": "rad", "frame": "joint", "required": True,
            }],
            "outputs": [{
                "name": "accepted", "type": "boolean", "shape": "scalar",
                "unit": "none", "frame": "none", "required": True,
            }],
            "effect": "The declared robot joints reach the requested configuration.",
            "preconditions": ["robot is connected"],
            "invocation_semantics": "Invoke once with the public target vector.",
            "temporal_semantics": "Returns after a bounded observation window.",
            "invariants": ["physical completion is judged outside candidate code"],
            "required_action_affordances": ["joint target command"],
            "required_observation_affordances": ["joint position observation"],
            "errors": [{"code": "TARGET_REJECTED", "message": "Target was rejected."}],
            "unsupported_scope": ["task-specific planning"],
        }],
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }


def _blue_spec(width: int) -> dict[str, Any]:
    return {
        "capability_specs": [{
            "capability_id": "set-joint-configuration",
            "measurement": {
                "measurement_id": "joint-error", "entity": "joint-set",
                "unit": "rad", "frame": "joint",
            },
            "metric": "max_joint_error",
            "threshold": {"comparator": "<=", "value": 0.05},
            "dwell_s": 0.2,
            "timeout_s": 2.0,
            "aggregation": "ALL",
            "guard_ids": ["physical-state-not-command-receipt"],
            "cases": [{
                "case_id": "nominal",
                "initial_state": {"joint": [0.0] * width},
                "inputs": {"target": [0.1] * width},
            }],
            "lineage": {"kind": "COPIED", "standard_id": "joint-arrival", "material": False},
        }]
    }


def _experience_snapshot(recipient_class: str) -> dict[str, Any]:
    sdk_entry_id = None if recipient_class == "design" else "fixture-sdk@1.0.0"
    applicability = {
        "robot_model_id": "so-arm101",
        "robot_configuration_id": "so-arm101-follower-stock-gripper",
        "sdk_entry_id": sdk_entry_id,
        "granularity_condition": "G2",
        "capability_effect_scope": ["joint-target"],
        "observation_condition": "public-observation",
    }
    digest = "sha256:" + "1" * 64
    return {
        "artifact_type": "experience_snapshot",
        "format_version": "experimental-1",
        "snapshot_id": f"{recipient_class}-snapshot",
        "recipient_class": recipient_class,
        "applicability": applicability,
        "records": [{
            "record_id": f"{recipient_class}-record",
            "version": "1.0.0",
            "record_ref": {"path": f"{recipient_class}-record-ref", "content_hash": digest},
            "projection": {
                "experience_id": f"{recipient_class}-guidance",
                "guidance": "use-bounded-effect",
                "applicability": copy.deepcopy(applicability),
                "provenance": {
                    "closure_hash": digest,
                    "summary_ref": "summary-ref",
                    "stage_artifacts_ref": "stage-artifacts-ref",
                    "evidence_digest_hash": digest,
                },
            },
        }],
    }


def _plan(root: Path, robot: str, width: int) -> tuple[DemoRunPlan, DemoModelAdapters]:
    manifest, snapshot, report, projection = _ready_robot_run(root, robot)
    task_library_root = root / "general_demo/libraries/tasks"
    shutil.copytree(
        PROJECT_ROOT / "general_demo/libraries/tasks",
        task_library_root,
        dirs_exist_ok=True,
    )
    task_package = TasksLibrary(task_library_root).load(
        projection["robot_configuration_id"], "1.0.0"
    )
    public_tasks = task_package.demo_public_tasks(f"run-{robot}")
    private_criteria = task_package.demo_private_criteria()
    tasks = tuple(
        DemoTask(
            requirement_id=public["requirement_id"],
            task_id=public["task_id"],
            description=public["description"],
            public_state={"target": [0.1] * width},
            private_criterion=criterion,
        )
        for public, criterion in zip(public_tasks, private_criteria, strict=True)
    )
    descriptor = {
        "value": [0.1] * width, "type": "number", "shape": f"vector:{width}",
        "unit": "rad", "frame": "joint",
    }
    standards = {
        "snapshot_id": "standards-demo",
        "standards": [{
            "standard_id": "joint-arrival", "measurement_id": "joint-error",
            "metric": "max_joint_error", "comparator": "<=", "threshold_value": 0.05,
            "dwell_s": 0.2, "timeout_s": 2.0, "aggregation": "ALL",
        }],
    }
    measurements = {
        "catalog_id": "measurements-demo",
        "measurements": [{
            "measurement_id": "joint-error", "entity": "joint-set", "unit": "rad",
            "frame": "joint", "adapter_id": "truth-joint-state",
            "truth_source": "physical_state", "metrics": ["max_joint_error"],
        }],
        "guards": [{
            "guard_id": "physical-state-not-command-receipt",
            "adapter_id": "truth-joint-state",
        }],
    }
    policy = {
        "policy_id": "blue-demo", "model_id": "fixed-fixture",
        "prompt_id": "blue-prompt", "max_cases_per_capability": 1, "repetitions": 1,
    }
    profile = ValidationAProfile(
        sdk_facade_members={"set-joint-configuration": ("command",)},
        fixture_probes={"set-joint-configuration": {"inputs": {"target": descriptor}}},
    )
    implementation_bundle = {
        "artifact_type": "stage2_implementation_bundle",
        "schema_version": "1.0.0",
        "sdk_implementation_projection": {
            "approved_member": "command",
            "robot": robot,
        },
        "robot_implementation_facts": {
            "joint_count": width,
            "unit": "rad",
            "frame": "joint",
        },
        "implementation_experience": [],
    }
    snapshot_path = root / snapshot
    frozen_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    run_dir = f"general_demo/runs/run-{robot}"
    frozen_snapshot["task_set_ref"] = _json_ref(
        root, f"{run_dir}/task_set.json", {"tasks": [task.frozen_record() for task in tasks]}
    )
    frozen_snapshot["g2_profile_ref"] = _json_ref(
        root, f"{run_dir}/g2_profile.json", G2
    )
    frozen_snapshot["observation_profile_ref"] = _json_ref(
        root, f"{run_dir}/robot_projection.json", projection
    )
    frozen_snapshot["blue_line_input_refs"] = [
        _json_ref(root, f"{run_dir}/standards.json", standards),
        _json_ref(root, f"{run_dir}/measurements.json", measurements),
        _json_ref(root, f"{run_dir}/blue_line_policy.json", policy),
    ]
    frozen_snapshot["library_view_refs"] = [
        _json_ref(root, f"{run_dir}/implementation_bundle.json", implementation_bundle)
    ]
    frozen_snapshot["budget_ref"] = _json_ref(
        root,
        f"{run_dir}/budget.json",
        {
            "artifact_type": "run_budget",
            "schema_version": "1.0.0",
            "stage1": {"max_correction_calls": 2},
            "blue_line": {"max_inference_calls": 3},
            "stage2": {"max_inference_calls": 30},
            "repair": {
                "max_invocations": 10,
                "max_infrastructure_retries": 1,
            },
            "consumer": {"max_inference_calls": 4},
            "demo": {"repetitions": 1},
        },
    )
    write_stable_json(snapshot_path, frozen_snapshot)
    plan = DemoRunPlan(
        run_id=f"run-{robot}",
        integration_manifest_path=manifest,
        run_snapshot_path=snapshot,
        readiness_report_path=report,
        robot_public_projection=projection,
        g2_profile=G2,
        tasks=tasks,
        standards_snapshot=standards,
        measurement_catalog=measurements,
        blue_line_policy=policy,
        implementation_bundle=implementation_bundle,
        validation_a_profile=profile,
        public_state_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "array", "items": {"type": "number"},
                    "minItems": width, "maxItems": width,
                }
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        validation_harness_config={"profile": "fixture"},
        repair_config=RepairConfig(max_repairs=10, max_infrastructure_retries=1),
    )

    consumer_calls: dict[str, int] = {}

    def consumer_model(inputs: dict[str, Any]) -> dict[str, Any]:
        task_id = inputs["task"]["task_id"]
        consumer_calls[task_id] = consumer_calls.get(task_id, 0) + 1
        if consumer_calls[task_id] == 1:
            return {
                "thought": "Use the promoted G2 capability.",
                "action": {
                    "capability_id": "set-joint-configuration",
                    "arguments": {"target": inputs["task"]["public_state"]["target"]},
                },
            }
        return {"thought": "The action completed.", "final": {"done": True}}

    source = (
        "def capability_set_joint_configuration(arg_target, *, _sdk):\n"
        "    _sdk.command(arg_target)\n"
        "    return {\"accepted\": True}\n"
    )
    models = DemoModelAdapters(
        stage1=FixtureJsonGenerator(
            [_design(width, [task.requirement_id for task in tasks])]
        ),
        blue_line=FixtureJsonGenerator([_blue_spec(width)]),
        stage2=FixtureJsonGenerator([{"action": "submit", "capability.py": source}]),
        repair=lambda _request: pytest.fail("Repair must not run on the passing path"),
        consumer=consumer_model,
    )
    return plan, models


@pytest.mark.parametrize(
    ("robot", "width"),
    [("so-arm101", 6), ("unitree-go2", 12)],
)
def test_same_g2_runner_completes_two_robot_shaped_runs(
    tmp_path: Path, robot: str, width: int
) -> None:
    plan, models = _plan(tmp_path, robot, width)
    result = GeneralDemoRunner(
        tmp_path,
        models,
        _RobotSession(width, robot),
        FrozenVideoProfile("demo-video", "1.0.0", "external", "fixed", 5, 2, 2, "mp4", "h264"),
        lambda _phase, _execution_id: _Encoder(),
        lambda criterion, evidence: bool(criterion) and evidence == {"passed": True},
    ).run(plan)

    assert result.status == "COMPLETE"
    assert [stage["status"] for stage in result.summary["stages"]] == [
        "READY", "SEALED", "READY", "SUBMITTED", "PASS", "PROMOTED", "PASS",
    ]
    assert len(result.validation_video_handles) == 1
    assert len(result.demo_trials) == 5
    assert all(trial.status == "PASS" for trial in result.demo_trials)
    assert result.summary["robot"]["robot_model_id"] == robot
    assert result.summary["granularity_profile"]["granularity"] == "G2"


def test_frozen_experience_reaches_only_its_recipient_stage_and_shares_bundle(
    tmp_path: Path,
) -> None:
    plan, models = _plan(tmp_path, "so-arm101", 6)
    design_snapshot = _experience_snapshot("design")
    implementation_snapshot = _experience_snapshot("implementation")
    run_dir = Path(plan.run_snapshot_path).parent.as_posix()
    snapshot_path = tmp_path / plan.run_snapshot_path
    frozen_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    frozen_snapshot["library_view_refs"].extend([
        _json_ref(tmp_path, f"{run_dir}/design-experience.json", design_snapshot),
        _json_ref(tmp_path, f"{run_dir}/implementation-experience.json", implementation_snapshot),
    ])
    write_stable_json(snapshot_path, frozen_snapshot)
    plan = replace(
        plan,
        design_experience_snapshot=design_snapshot,
        implementation_experience_snapshot=implementation_snapshot,
    )

    captured: dict[str, Any] = {}
    original_repair_runner = demo_runner_module.RepairRunner

    class _CapturingRepairRunner(original_repair_runner):
        def run(self, *args, **kwargs):
            captured["bundle"] = kwargs.get("implementation_bundle", args[-1])
            return super().run(*args, **kwargs)

    with patch.object(demo_runner_module, "RepairRunner", _CapturingRepairRunner):
        result = GeneralDemoRunner(
            tmp_path,
            models,
            _RobotSession(6, "so-arm101"),
            FrozenVideoProfile("demo-video", "1.0.0", "external", "fixed", 5, 2, 2, "mp4", "h264"),
            lambda _phase, _execution_id: _Encoder(),
            lambda criterion, evidence: bool(criterion) and evidence == {"passed": True},
        ).run(plan)

    stage1_inputs = models.stage1.calls[0]["inputs"]
    assert stage1_inputs["design_experience_snapshot"] == design_snapshot
    assert "implementation_experience_snapshot" not in stage1_inputs
    derived_bundle = models.stage2.calls[0]["inputs"]["implementation_bundle"]
    assert derived_bundle["implementation_experience"] == [
        implementation_snapshot["records"][0]["projection"]
    ]
    assert plan.implementation_bundle["implementation_experience"] == []
    assert derived_bundle == captured["bundle"].artifact
    assert result.artifacts["stages"]["stage2"]["implementation_bundle_hash"] == captured["bundle"].bundle_hash
