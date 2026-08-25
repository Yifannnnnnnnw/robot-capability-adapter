#!/usr/bin/env python3
"""Executable zero-model preflight for the inline-measurement IVC mainline.

The checker deliberately imports no model provider and reads no credential.  It
builds the IVC authoring projection for every indexed Experiment 3 package,
then uses deterministic artifacts and a scripted ReCAP turn source to exercise
TGCD validation, IVC validation, real capability Harness execution, whitelist
construction, and the persistent-worker Task Demo Harness.  SO-101 and Go2
each execute one real nominal/boundary inline-measurement pair in MuJoCo.

These runs are local diagnostics.  They are neither experiment cells nor
evidence that any model can author the same artifacts.
"""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoadapter2.capability_design import validate_capability_design
from autoadapter2.capability_design.protocol import CAPABILITY_INVOCATION_ABI
from autoadapter2.harness import run_private_suite
from autoadapter2.libraries import RobotPackage, load_indexed_robot_package
from autoadapter2.pipeline import (
    _default_task_demo_run,
    _passed_capability_ids,
    _reference_condition,
    _whitelisted_design,
    render_reference_driver,
)
from autoadapter2.react import ToolCall, ToolTurn
from autoadapter2.task_demo.recap import RecapBudgets, run_recap
from autoadapter2.validation_compiler import (
    load_ivc_worked_references,
    validate_capability_validation_suite,
)
from autoadapter2.validation_compiler.ivc import (
    _private_inputs_from_package,
    build_ivc_inputs,
)


ROBOT_CONFIGURATIONS = (
    "robotstudio_so101",
    "unitree-go2-stock-12dof",
    "franka_panda",
    "kinova_gen3_robotiq_2f85",
    "ufactory_xarm7",
    "universal_robots_ur5e_robotiq_2f85",
    "piper",
    "kuka_iiwa_14",
    "leap_hand",
    "hello_robot_stretch_2",
    "aloha_2",
)
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


class InlineIVCPreflightError(RuntimeError):
    """Raised when a zero-model executable boundary does not close."""


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise InlineIVCPreflightError(message)


def _projection_probe_design() -> dict[str, Any]:
    """Return the minimum shape needed to build model-visible IVC inputs."""

    return {"capabilities": [{"capability_id": "zero-model-input-probe"}]}


def check_all_package_inputs(root: str | Path) -> dict[str, Any]:
    """Build real scene/entity/operator inputs for all eleven indexed packages."""

    mainline_root = Path(root).resolve()
    robots: dict[str, Any] = {}
    operator_kinds: tuple[str, ...] | None = None
    for robot in ROBOT_CONFIGURATIONS:
        package = load_indexed_robot_package(mainline_root, robot)
        private = _private_inputs_from_package(package)
        inputs = build_ivc_inputs(
            package=package,
            design=_projection_probe_design(),
            private_inputs=private,
        )
        instances = inputs["private_instances"].get("instances")
        scenes = inputs["scene_entity_catalog"].get("scenes")
        operators = inputs["measurement_operator_catalog"].get("operators")
        _require(isinstance(instances, list) and instances, f"{robot}: no IVC instances")
        _require(isinstance(scenes, list) and scenes, f"{robot}: no parsed scenes")
        _require(isinstance(operators, list) and operators, f"{robot}: no operators")
        current_kinds = tuple(sorted(str(item["kind"]) for item in operators))
        if operator_kinds is None:
            operator_kinds = current_kinds
        _require(
            current_kinds == operator_kinds,
            f"{robot}: operator catalog differs from the Framework catalog",
        )
        entity_counts = {
            kind: sum(
                len(scene["entities"].get(kind, []))
                for scene in scenes
                if isinstance(scene, Mapping)
                and isinstance(scene.get("entities"), Mapping)
            )
            for kind in ("bodies", "sites", "joints", "geoms", "actuators", "keyframes")
        }
        robots[robot] = {
            "package_version": package.package_version,
            "instance_count": len(instances),
            "scene_count": len(scenes),
            "entity_counts": entity_counts,
        }
    return {
        "passed": True,
        "robot_count": len(robots),
        "operator_count": len(operator_kinds or ()),
        "robots": robots,
    }


def _worked_reference(robot: str) -> dict[str, Any]:
    matches = [
        reference
        for reference in load_ivc_worked_references()
        if reference.get("validation_suite", {}).get("robot_configuration_id") == robot
    ]
    _require(len(matches) == 1, f"{robot}: expected exactly one complete worked reference")
    return copy.deepcopy(matches[0])


def _deterministic_tgcd_artifact(
    package: RobotPackage,
    reference_design: Mapping[str, Any],
) -> dict[str, Any]:
    """Make the SO worked design a complete package-bound test artifact."""

    capabilities = copy.deepcopy(reference_design.get("capabilities"))
    _require(isinstance(capabilities, list) and capabilities, "worked design has no capabilities")
    capability_ids = [str(item["capability_id"]) for item in capabilities]
    _require(
        len(package.tasks) >= len(capability_ids),
        "deterministic task-support fixture cannot cover every capability",
    )
    support = []
    for index, task in enumerate(package.tasks):
        capability_index = 0 if index == 0 else 1 + ((index - 1) % (len(capability_ids) - 1))
        support.append(
            {
                "task_id": str(task["task_id"]),
                "capability_id": capability_ids[capability_index],
                "rationale": "Zero-model orchestration fixture; support relation only.",
            }
        )
    artifact = {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "invocation_abi": copy.deepcopy(CAPABILITY_INVOCATION_ABI),
        "capabilities": capabilities,
        "task_support": support,
    }
    return validate_capability_design(artifact, package)


class _DeterministicRecapTurns:
    """Two scripted tool turns: one real capability call, then ``finish``."""

    def __init__(self, *, method_name: str, request: Mapping[str, Any]) -> None:
        self.method_name = method_name
        self.request = copy.deepcopy(dict(request))
        self.turn_count = 0

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del stage, system_prompt, messages
        exposed = {
            str(item.get("function", {}).get("name"))
            for item in tools
            if isinstance(item, Mapping)
        }
        self.turn_count += 1
        if self.turn_count % 2 == 1:
            _require(self.method_name in exposed, "whitelisted capability tool was not exposed")
            arguments = copy.deepcopy(self.request)
            return ToolTurn(
                content="Execute one deterministic capability smoke.",
                tool_calls=(
                    ToolCall(
                        id=f"preflight-capability-{self.turn_count}",
                        name=self.method_name,
                        arguments=arguments,
                        raw_arguments=json.dumps(arguments, sort_keys=True),
                    ),
                ),
                finish_reason="tool_calls",
            )
        _require("finish" in exposed, "ReCAP finish tool was not exposed")
        return ToolTurn(
            content="End the deterministic controller trial.",
            tool_calls=(
                ToolCall(
                    id=f"preflight-finish-{self.turn_count}",
                    name="finish",
                    arguments={},
                    raw_arguments="{}",
                ),
            ),
            finish_reason="tool_calls",
        )


def _inline_smoke(
    *,
    root: Path,
    robot: str,
    workspace: Path,
    design_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reference = _worked_reference(robot)
    package = load_indexed_robot_package(root, robot)
    design = (
        copy.deepcopy(dict(design_override))
        if design_override is not None
        else copy.deepcopy(reference["capability_design"])
    )
    full_suite = validate_capability_validation_suite(
        reference["validation_suite"],
        package=package,
        design=design,
    )
    driver = render_reference_driver(package, design, workspace / "driver")
    methods = [str(item["method_name"]) for item in design["capabilities"]]
    condition = _reference_condition(driver, methods)
    capability_id = str(design["capabilities"][0]["capability_id"])
    smoke_suite = copy.deepcopy(full_suite)
    smoke_suite["cases"] = [
        case for case in full_suite["cases"] if case["capability_id"] == capability_id
    ]
    _require(len(smoke_suite["cases"]) == 2, f"{robot}: smoke pair is incomplete")
    report = run_private_suite(
        package=package,
        design=design,
        suite=smoke_suite,
        driver_path=driver,
        condition=condition,
        trusted_reference_driver=True,
        output_dir=workspace / "capability-harness",
        record_video=False,
        wall_timeout_s=120.0,
        run_id=f"zero-model-inline-{robot}",
    )
    trials = report.get("trials")
    _require(report.get("pipeline_completed") is True, f"{robot}: Harness did not complete")
    _require(
        report.get("physical_validation_executed") is True,
        f"{robot}: MuJoCo physics did not execute",
    )
    _require(isinstance(trials, list) and len(trials) == 2, f"{robot}: wrong trial count")
    _require(
        all(item.get("measurement_error") is None for item in trials),
        f"{robot}: inline measurement failed",
    )
    return {
        "package": package,
        "design": design,
        "suite": smoke_suite,
        "driver": driver,
        "report": report,
        "summary": {
            "robot_configuration_id": robot,
            "capability_id": capability_id,
            "case_count": len(trials),
            "physical_validation_executed": True,
            "inline_measurement_completed": True,
            "validation_passed": bool(report.get("validation_passed")),
            "trial_passes": [bool(item.get("trial_passed")) for item in trials],
        },
    }


def run_zero_model_mainline_preflight(
    root: str | Path,
    *,
    include_all_packages: bool = True,
) -> dict[str, Any]:
    """Execute the approved local preflight without constructing an LLM client."""

    mainline_root = Path(root).resolve()
    package_inputs = (
        check_all_package_inputs(mainline_root)
        if include_all_packages
        else {"passed": True, "skipped_in_focused_call": True}
    )
    with tempfile.TemporaryDirectory(prefix="autoadapter2-inline-ivc-preflight-") as raw:
        workspace = Path(raw)
        so_reference = _worked_reference("robotstudio_so101")
        so_package = load_indexed_robot_package(mainline_root, "robotstudio_so101")
        tgcd = _deterministic_tgcd_artifact(
            so_package,
            so_reference["capability_design"],
        )
        so = _inline_smoke(
            root=mainline_root,
            robot="robotstudio_so101",
            workspace=workspace / "so101",
            design_override=tgcd,
        )
        passed = _passed_capability_ids(suite=so["suite"], report=so["report"])
        _require(passed, "SO-101 smoke yielded no nominal+boundary whitelist")
        whitelisted = _whitelisted_design(tgcd, passed)
        _require(
            len(whitelisted["capabilities"]) == 1,
            "SO-101 preflight expected one whitelisted capability",
        )
        nominal = next(
            case for case in so["suite"]["cases"] if case["case_role"] == "nominal"
        )
        scripted_recap = _DeterministicRecapTurns(
            method_name=str(whitelisted["capabilities"][0]["method_name"]),
            request=nominal["request"],
        )
        task_demo = _default_task_demo_run(
            package=so["package"],
            design=whitelisted,
            driver_path=so["driver"],
            client=scripted_recap,
            recap_runner=run_recap,
            budgets=RecapBudgets(),
            selection_seed="zero-model-inline-ivc-preflight",
            output_dir=workspace / "so101" / "task-demo",
            record_video=False,
            wall_timeout_s=120.0,
        )
        _require(task_demo.get("pipeline_completed") is True, "ReCAP Task Demo did not complete")
        _require(
            task_demo.get("physical_validation_executed") is True,
            "ReCAP Task Demo did not execute MuJoCo physics",
        )
        controller = task_demo.get("high_level_controller")
        _require(
            isinstance(controller, Mapping)
            and controller.get("completed") is True
            and int(controller.get("capability_call_count", 0)) >= 1,
            "ReCAP did not make a real whitelisted capability call",
        )
        go = _inline_smoke(
            root=mainline_root,
            robot="unitree-go2-stock-12dof",
            workspace=workspace / "go2",
        )

    return {
        "check": "inline_ivc_zero_model_mainline_preflight",
        "passed": True,
        "external_model_client_constructed": False,
        "external_model_requests": 0,
        "experiment_cells_created": 0,
        "package_inputs": package_inputs,
        "orchestration": {
            "stages": [
                "deterministic_tgcd_audit",
                "deterministic_ivc_audit",
                "driver_capability_harness",
                "nominal_boundary_whitelist",
                "recap_task_demo_harness",
            ],
            "whitelisted_capabilities": list(passed),
            "recap_capability_calls": int(controller["capability_call_count"]),
            "recap_physical_validation_executed": True,
            "recap_task_verdict": (
                "PASS" if task_demo.get("validation_passed") is True else "FAIL"
            ),
        },
        "inline_mujoco_smokes": [so["summary"], go["summary"]],
        "evidence_class": "local zero-model diagnostic only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--skip-all-package-inputs",
        action="store_true",
        help="run only the focused SO/Go orchestration smoke",
    )
    args = parser.parse_args()
    result = run_zero_model_mainline_preflight(
        args.root,
        include_all_packages=not args.skip_all_package_inputs,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
