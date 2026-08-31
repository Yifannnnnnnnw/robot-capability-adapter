#!/usr/bin/env python3
"""Build the fixed capability-v2 inputs used by the bounded Exp3 route.

The generator is deliberately zero-model.  SO-101 and Go2 are wrapped from
the tracked complete worked references.  The remaining configurations use a
small morphology-specific set of numeric capabilities calibrated from the
package's real reach-scene reset, observable end entity, and one actuated
scalar joint.  The normal capability-v2 validators audit every generated
artifact before it is written.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import mujoco

from autoadapter2.capability_design.protocol import CAPABILITY_INVOCATION_ABI
from autoadapter2.capability_design.tgcd import validate_capability_design
from autoadapter2.harness.operators import inspect_scene_entities
from autoadapter2.harness.runner import run_private_suite
from autoadapter2.harness.session import apply_framework_reset
from autoadapter2.libraries import load_robot_package
from autoadapter2.validation_compiler.ivc import (
    validate_capability_validation_suite,
)


ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(__file__).resolve().parent
ROBOT_ROOT = ROOT / "autoadapter" / "libraries" / "robots"
WORKED_REFERENCE_PATH = (
    ROOT / "autoadapter" / "references" / "capability_v2"
    / "ivc_worked_references.json"
)

VERSIONS = {
    "robotstudio_so101": "1.0.4",
    "unitree-go2-stock-12dof": "1.0.0",
    "franka_panda": "1.0.0",
    "kinova_gen3_robotiq_2f85": "1.0.0",
    "ufactory_xarm7": "1.0.0",
    "universal_robots_ur5e_robotiq_2f85": "1.0.0",
    "piper": "1.0.0",
    "kuka_iiwa_14": "1.0.0",
    "leap_hand": "1.0.0",
    "hello_robot_stretch_2": "1.0.0",
    "aloha_2": "1.0.0",
}


# Every configured entity is declared in morphology.public_observations and
# rechecked against the selected real MuJoCo scene before generation.
GENERIC_CONFIG = {
    "franka_panda": {
        "endpoints": [("body", "hand", "arm tool", 0)],
        "joints": [
            ("finger_joint1", "gripper"),
            ("joint7", "wrist"),
            ("joint4", "elbow"),
        ],
    },
    "kinova_gen3_robotiq_2f85": {
        "endpoints": [("site", "pinch_site", "arm tool", 0)],
        "joints": [
            ("left_driver_joint", "gripper"),
            ("joint_6", "wrist"),
            ("joint_4", "elbow"),
        ],
    },
    "ufactory_xarm7": {
        "endpoints": [("site", "link_tcp", "arm tool", 0)],
        "joints": [
            ("left_driver_joint", "gripper"),
            ("joint7", "wrist"),
            ("joint4", "elbow"),
        ],
    },
    "universal_robots_ur5e_robotiq_2f85": {
        "endpoints": [("site", "pinch_site", "arm tool", 0)],
        "joints": [
            ("left_driver_joint", "gripper"),
            ("wrist_3_joint", "wrist"),
            ("elbow_joint", "elbow"),
        ],
    },
    "piper": {
        "endpoints": [("site", "ee_site", "arm tool", 0)],
        "joints": [
            ("joint7", "gripper slide"),
            ("joint6", "wrist"),
            ("joint3", "elbow"),
        ],
    },
    "kuka_iiwa_14": {
        "endpoints": [("site", "attachment_site", "arm tool", 0)],
        "joints": [
            ("joint7", "wrist"),
            ("joint6", "forearm"),
            ("joint4", "elbow"),
        ],
    },
    "leap_hand": {
        "endpoints": [
            ("site", "if_tip", "index fingertip", 0),
            ("site", "th_tip", "thumb fingertip", 9),
        ],
        "joints": [
            ("if_mcp", "index MCP"),
            ("if_pip", "index PIP"),
            ("th_mcp", "thumb MCP"),
        ],
        "include_displacement": False,
    },
    "hello_robot_stretch_2": {
        "endpoints": [("body", "link_gripper_slider", "mobile tool", 0)],
        "joints": [
            ("joint_gripper_slide", "gripper slide"),
            ("joint_wrist_yaw", "wrist yaw"),
            ("joint_lift", "vertical lift"),
        ],
        "base_displacement_body": "base_link",
    },
    "aloha_2": {
        "endpoints": [
            ("site", "right/gripper", "right-arm tool", 0),
            # Left target is supplied by the tracked ALOHA B1 reference.
            ("site", "left/gripper", "left-arm tool", "aloha_left"),
        ],
        "joints": [
            ("right/left_finger", "right gripper"),
            ("left/left_finger", "left gripper"),
            ("right/wrist_rotate", "right wrist"),
            ("left/wrist_rotate", "left wrist"),
        ],
        "include_displacement": False,
    },
}


JOINT_ACTUATOR_OVERRIDES = {
    ("franka_panda", "finger_joint1"): "actuator8",
    ("kinova_gen3_robotiq_2f85", "left_driver_joint"): "fingers_actuator",
    ("ufactory_xarm7", "left_driver_joint"): "gripper",
    ("universal_robots_ur5e_robotiq_2f85", "left_driver_joint"): (
        "fingers_actuator"
    ),
}

NUMERIC_TOLERANCE_RULE = (
    "64 * IEEE-754 binary64 epsilon * max(1, abs(lower), abs(upper), span)"
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _reference_entry(robot_id: str) -> dict[str, Any]:
    document = _read(WORKED_REFERENCE_PATH)
    for entry in document["worked_references"]:
        if entry["capability_design"]["robot_configuration_id"] == robot_id:
            return copy.deepcopy(entry)
    raise ValueError(f"no worked reference for {robot_id}")


def _support(
    package: Any,
    capabilities: list[dict[str, Any]],
    *,
    reference_robot: bool,
) -> list[dict[str, str]]:
    """Create support-only relations; never encode order or a call plan."""

    result: list[dict[str, str]] = []
    for task in package.tasks:
        task_id = str(task["task_id"])
        description = str(task.get("description", "")).lower()
        if reference_robot and package.robot_configuration_id == "robotstudio_so101":
            ids = ["A1"]
            if any(token in task_id for token in ("push", "press", "sweep")):
                ids.extend(["A4", "A5"])
            if any(token in task_id for token in ("pick", "bin", "hole", "peg")):
                ids.extend(["A3", "A2"])
            if any(token in task_id for token in ("dial", "lever", "faucet", "door")):
                ids.extend(["A6", "A4"])
            if any(token in task_id for token in ("drawer", "window", "handle")):
                ids.extend(["A5", "A4"])
        elif reference_robot:
            ids = ["G1", "G5"]
            if any(token in description for token in ("path", "lane", "order", "course")):
                ids.append("G3")
            if any(token in description for token in ("height", "payload", "table", "apex")):
                ids.append("G4")
            if any(token in description for token in ("turn", "direction", "waypoint")):
                ids.append("G2")
        else:
            ids = [item["capability_id"] for item in capabilities]
        for capability_id in dict.fromkeys(ids):
            result.append(
                {
                    "task_id": task_id,
                    "capability_id": capability_id,
                    "rationale": (
                        "This reusable low-level physical effect can contribute to "
                        "the source task without prescribing call order, waypoints, "
                        "or a task macro."
                    ),
                }
            )

    covered = {item["capability_id"] for item in result}
    first_task = str(package.tasks[0]["task_id"])
    for capability in capabilities:
        capability_id = capability["capability_id"]
        if capability_id not in covered:
            result.append(
                {
                    "task_id": first_task,
                    "capability_id": capability_id,
                    "rationale": (
                        "The capability supplies a reusable physical primitive for "
                        "this source task without defining an execution sequence."
                    ),
                }
            )
    return result


def _reference_pair(package: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = _reference_entry(package.robot_configuration_id)
    reference = entry["capability_design"]
    capabilities = copy.deepcopy(reference["capabilities"])
    raw_design = {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "task_snapshot_id": package.snapshot_id,
        "invocation_abi": copy.deepcopy(CAPABILITY_INVOCATION_ABI),
        "capabilities": capabilities,
        "task_support": _support(
            package,
            capabilities,
            reference_robot=True,
        ),
    }
    design = validate_capability_design(raw_design, package)
    suite = validate_capability_validation_suite(
        entry["validation_suite"],
        package=package,
        design=design,
    )
    return design, suite


def _private_instances(package: Any) -> list[dict[str, Any]]:
    document = _read(package.private_dir / "instances.json")
    values = document.get("instances")
    if not isinstance(values, list) or not values:
        raise ValueError(f"{package.robot_configuration_id} has no private instances")
    return values


def _reach_instance(package: Any) -> dict[str, Any]:
    instances = _private_instances(package)
    for instance in instances:
        task_id = str(instance.get("task_id", ""))
        if "reach" in task_id:
            return copy.deepcopy(instance)
    return copy.deepcopy(instances[0])


def _entity_position(
    model: Any,
    data: Any,
    *,
    entity_kind: str,
    entity_name: str,
) -> list[float]:
    if entity_kind == "site":
        object_type = mujoco.mjtObj.mjOBJ_SITE
        positions = data.site_xpos
    else:
        object_type = mujoco.mjtObj.mjOBJ_BODY
        positions = data.xpos
    entity_id = int(mujoco.mj_name2id(model, object_type, entity_name))
    if entity_id < 0:
        raise ValueError(f"unknown {entity_kind} {entity_name!r}")
    return [float(value) for value in positions[entity_id]]


def _joint_calibration(
    model: Any,
    data: Any,
    *,
    joint_name: str,
    entities: dict[str, Any],
) -> tuple[str, float, float, float]:
    joint_id = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
    )
    if joint_id < 0:
        raise ValueError(f"unknown joint {joint_name!r}")
    unit = entities["joint_units"].get(joint_name)
    limits = entities["joint_ranges"].get(joint_name)
    if unit not in {"rad", "m"} or not isinstance(limits, list):
        raise ValueError(f"joint {joint_name!r} lacks a finite scalar range")
    lower, upper = (float(limits[0]), float(limits[1]))
    address = int(model.jnt_qposadr[joint_id])
    initial = float(data.qpos[address])
    if not lower <= initial <= upper:
        raise ValueError(f"joint {joint_name!r} reset is outside its range")
    return unit, lower, upper, initial


def _joint_actuator(
    model: Any,
    *,
    robot_id: str,
    joint_name: str,
) -> tuple[int, str, bool]:
    joint_id = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
    )
    override = JOINT_ACTUATOR_OVERRIDES.get((robot_id, joint_name))
    if override is not None:
        actuator_id = int(
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, override)
        )
        if actuator_id < 0:
            raise ValueError(f"unknown calibration actuator {override!r}")
        return actuator_id, override, False
    joint_transmission = int(mujoco.mjtTrn.mjTRN_JOINT)
    for actuator_id in range(int(model.nu)):
        if (
            int(model.actuator_trntype[actuator_id]) == joint_transmission
            and int(model.actuator_trnid[actuator_id, 0]) == joint_id
        ):
            name = mujoco.mj_id2name(
                model,
                mujoco.mjtObj.mjOBJ_ACTUATOR,
                actuator_id,
            )
            if not name:
                raise ValueError(f"joint {joint_name!r} actuator has no name")
            return actuator_id, str(name), True
    raise ValueError(f"no trusted actuator resolves to joint {joint_name!r}")


def _run_constant_control(
    *,
    model: Any,
    instance: dict[str, Any],
    actuator_id: int,
    joint_name: str,
    control_value: float,
    duration_s: float,
) -> tuple[float, int]:
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance.get("reset"))
    data.ctrl[actuator_id] = float(control_value)
    steps = round(float(duration_s) / float(model.opt.timestep))
    if steps <= 0 or not math.isclose(
        steps * float(model.opt.timestep),
        float(duration_s),
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise ValueError(f"duration {duration_s:g} is not a real physics-step count")
    max_steps = int(instance.get("max_steps", steps))
    if steps > max_steps:
        raise ValueError(
            f"calibration duration needs {steps} steps but instance permits {max_steps}"
        )
    mujoco.mj_step(model, data, nstep=steps)
    joint_id = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
    )
    address = int(model.jnt_qposadr[joint_id])
    terminal = float(data.qpos[address])
    if not math.isfinite(terminal):
        raise ValueError(f"calibration for {joint_name!r} produced non-finite state")
    return terminal, steps


def _best_constant_control(
    *,
    model: Any,
    instance: dict[str, Any],
    actuator_id: int,
    joint_name: str,
    target: float,
    duration_s: float,
    direct_mapping: bool,
) -> tuple[float, float, float, int]:
    lower = float(model.actuator_ctrlrange[actuator_id, 0])
    upper = float(model.actuator_ctrlrange[actuator_id, 1])
    if not lower < upper:
        raise ValueError(f"calibration actuator for {joint_name!r} is unbounded")
    if direct_mapping:
        controls = [min(upper, max(lower, target))]
    else:
        search_lower, search_upper = lower, upper
        controls = []
        for _ in range(4):
            grid = [
                search_lower + (search_upper - search_lower) * index / 8.0
                for index in range(9)
            ]
            scored: list[tuple[float, float, float, int]] = []
            search_duration = min(2.0, duration_s)
            for control in grid:
                terminal, steps = _run_constant_control(
                    model=model,
                    instance=instance,
                    actuator_id=actuator_id,
                    joint_name=joint_name,
                    control_value=control,
                    duration_s=search_duration,
                )
                scored.append((abs(terminal - target), control, terminal, steps))
            scored.sort(key=lambda value: value[0])
            best_control = scored[0][1]
            spacing = (search_upper - search_lower) / 8.0
            search_lower = max(lower, best_control - spacing)
            search_upper = min(upper, best_control + spacing)
        controls = [best_control]
    control = controls[0]
    terminal, steps = _run_constant_control(
        model=model,
        instance=instance,
        actuator_id=actuator_id,
        joint_name=joint_name,
        control_value=control,
        duration_s=duration_s,
    )
    return control, terminal, abs(terminal - target), steps


def _measured_joint_contract(
    *,
    robot_id: str,
    model: Any,
    instance: dict[str, Any],
    capability_id: str,
    method_name: str,
    metric: str,
    unit: str,
    joint_name: str,
    lower: float,
    upper: float,
    reset_position: float,
    nominal_duration_s: float,
    boundary_duration_s: float,
) -> tuple[float, float, float, dict[str, Any], str]:
    actuator_id, actuator_name, direct_mapping = _joint_actuator(
        model,
        robot_id=robot_id,
        joint_name=joint_name,
    )
    candidates: list[dict[str, Any]] = []
    for target in (lower, upper):
        control, terminal, error, steps = _best_constant_control(
            model=model,
            instance=instance,
            actuator_id=actuator_id,
            joint_name=joint_name,
            target=target,
            duration_s=boundary_duration_s,
            direct_mapping=direct_mapping,
        )
        requested_displacement = abs(target - reset_position)
        actual_displacement = abs(terminal - reset_position)
        candidates.append(
            {
                "target": target,
                "control": control,
                "terminal": terminal,
                "error": error,
                "steps": steps,
                "requested_displacement": requested_displacement,
                "actual_displacement": actual_displacement,
                "executable": (
                    requested_displacement > 0.0
                    and actual_displacement > 0.0
                    and error < 0.5 * requested_displacement
                ),
            }
        )
    executable = [item for item in candidates if item["executable"]]
    if not executable:
        raise ValueError(
            f"{robot_id} {joint_name} has no discriminating executable limit"
        )
    chosen = max(executable, key=lambda item: item["requested_displacement"])
    boundary = float(chosen["target"])
    nominal = reset_position + 0.5 * (boundary - reset_position)

    case_values: dict[str, dict[str, Any]] = {}
    controls: dict[str, float] = {}
    for role, target, duration in (
        ("nominal", nominal, nominal_duration_s),
        ("calibrated_boundary", boundary, boundary_duration_s),
    ):
        control, _terminal, _error, _steps = _best_constant_control(
            model=model,
            instance=instance,
            actuator_id=actuator_id,
            joint_name=joint_name,
            target=target,
            duration_s=duration,
            direct_mapping=direct_mapping,
        )
        controls[role] = control
        terminal_errors: list[float] = []
        stepped = False
        for _ in range(3):
            terminal, steps = _run_constant_control(
                model=model,
                instance=instance,
                actuator_id=actuator_id,
                joint_name=joint_name,
                control_value=control,
                duration_s=duration,
            )
            terminal_errors.append(abs(terminal - target))
            stepped = stepped or steps > 0
        case_values[role] = {
            "case_id": f"{robot_id}-{capability_id.lower()}-{role}",
            "target": target,
            "max_duration_s": duration,
            "terminal_errors": terminal_errors,
            "max_terminal_error": max(terminal_errors),
            "trusted_actuator_executed": True,
            "physics_stepped": stepped,
        }
    span = upper - lower
    numeric_tolerance = (
        64.0
        * sys.float_info.epsilon
        * max(1.0, abs(lower), abs(upper), span)
    )
    sealed_threshold = max(
        case_values["nominal"]["max_terminal_error"],
        case_values["calibrated_boundary"]["max_terminal_error"],
    ) + numeric_tolerance
    nominal_displacement = abs(nominal - reset_position)
    if not sealed_threshold < nominal_displacement:
        raise ValueError(
            f"{robot_id} {joint_name} calibration threshold {sealed_threshold:g} "
            f"would allow reset to pass nominal displacement {nominal_displacement:g}"
        )
    source_id = f"exp3-calibration-{robot_id}-{capability_id.lower()}"
    record = {
        "method_name": method_name,
        "criterion_source_id": source_id,
        "metric": metric,
        "unit": unit,
        "instance_id": str(instance["instance_id"]),
        "scene_entrypoint": str(instance["scene_entrypoint"]),
        "joint_name": joint_name,
        "actuator_name": actuator_name,
        "controller_kind": "trusted_constant_position_control",
        "timestep_s": float(model.opt.timestep),
        "repetitions": 3,
        "reset_position": reset_position,
        "joint_lower": lower,
        "joint_upper": upper,
        "boundary_candidates": [
            {
                "target": float(item["target"]),
                "control": float(item["control"]),
                "terminal": float(item["terminal"]),
                "error": float(item["error"]),
                "requested_displacement": float(item["requested_displacement"]),
                "actual_displacement": float(item["actual_displacement"]),
                "executable": bool(item["executable"]),
            }
            for item in candidates
        ],
        "selected_boundary": boundary,
        "numeric_tolerance_rule": NUMERIC_TOLERANCE_RULE,
        "cases": case_values,
        "numeric_tolerance": numeric_tolerance,
        "sealed_threshold": sealed_threshold,
    }
    candidate_summary = "; ".join(
        (
            f"target {item['target']:.12g}: terminal {item['terminal']:.12g}, "
            f"error {item['error']:.12g}, actual displacement "
            f"{item['actual_displacement']:.12g}, executable={item['executable']}"
        )
        for item in candidates
    )
    detail = (
        f"Real trusted actuator {actuator_name} was evaluated from declared reset "
        f"{reset_position:.12g} toward both exact MJCF limits: {candidate_summary}. "
        f"The farther executable limit {boundary:.12g} is boundary; nominal is its "
        f"reset midpoint {nominal:.12g}. Three real reset/controller/physics repeats "
        f"per case produced errors {case_values['nominal']['terminal_errors']} and "
        f"{case_values['calibrated_boundary']['terminal_errors']}. "
        "experiment/experiment3/fixed_inputs/calibration_report.json#robots/"
        f"{robot_id}/capabilities/{capability_id} seals max error plus "
        f"{NUMERIC_TOLERANCE_RULE} = {sealed_threshold:.12g} {unit}; this remains "
        f"below nominal reset displacement {nominal_displacement:.12g}. Selected "
        f"constant controls were nominal {controls['nominal']:.12g} and boundary "
        f"{controls['calibrated_boundary']:.12g}."
    )
    return nominal, boundary, sealed_threshold, record, detail


def _evidence_ref(
    robot_id: str,
    *,
    source_id: str,
    specific_reference: str,
) -> dict[str, str]:
    return {
        "source_id": f"{robot_id}_{source_id}",
        "specific_reference": specific_reference,
    }


def _display_vector(values: list[float]) -> str:
    return "[" + ", ".join(f"{value:.12g}" for value in values) + "]"


def _reach_target_and_criterion(
    package: Any,
    instance: dict[str, Any],
) -> tuple[list[float], dict[str, Any]]:
    task_id = str(instance["task_id"])
    task = next(
        (item for item in package.tasks if str(item["task_id"]) == task_id),
        None,
    )
    if not isinstance(task, dict):
        raise ValueError(f"{package.robot_configuration_id} lacks task {task_id!r}")
    scoring = task.get("scoring")
    if not isinstance(scoring, list) or not scoring:
        raise ValueError(f"{task_id!r} lacks a public scoring clause")
    source_criterion = scoring[0]
    if (
        not isinstance(source_criterion, dict)
        or source_criterion.get("unit") != "m"
        or source_criterion.get("comparator") not in {"<", "<="}
        or not isinstance(source_criterion.get("threshold"), (int, float))
    ):
        raise ValueError(f"{task_id!r} lacks a usable metric-distance criterion")

    public_arguments = instance.get("public_arguments")
    if not isinstance(public_arguments, dict):
        raise ValueError(f"{instance['instance_id']} lacks public_arguments")
    request = public_arguments.get("request")
    parameters = request.get("task_parameters") if isinstance(request, dict) else None
    if not isinstance(parameters, dict):
        raise ValueError(f"{instance['instance_id']} lacks task parameters")
    raw_target = parameters.get("target_position")
    if raw_target is None:
        fingertips = parameters.get("target_fingertip_positions_m")
        raw_target = fingertips[:3] if isinstance(fingertips, list) else None
    if (
        not isinstance(raw_target, list)
        or len(raw_target) != 3
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in raw_target
        )
    ):
        raise ValueError(f"{instance['instance_id']} lacks a finite XYZ target")
    return [float(value) for value in raw_target], copy.deepcopy(source_criterion)


def _number_schema(
    *,
    unit: str,
    frame: str,
    minimum: float,
    maximum: float,
    evidence_ref: dict[str, str],
) -> dict[str, Any]:
    return {
        "type": "number",
        "unit": unit,
        "frame": frame,
        "minimum": float(minimum),
        "maximum": float(maximum),
        "evidence_refs": [copy.deepcopy(evidence_ref)],
    }


def _criterion(
    *,
    metric: str,
    unit: str,
    comparator: str,
    threshold: float,
    evidence_ref: dict[str, str],
) -> dict[str, Any]:
    return {
        "metric": metric,
        "unit": unit,
        "comparator": comparator,
        "threshold": float(threshold),
        "temporal": {"kind": "terminal_state"},
        "aggregation": {"kind": "single_trial"},
        "source_refs": [copy.deepcopy(evidence_ref)],
    }


def _capability(
    *,
    capability_id: str,
    method_name: str,
    description: str,
    effect: str,
    request_schema: dict[str, Any],
    criterion: dict[str, Any],
    evidence_ref: dict[str, str],
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "method_name": method_name,
        "description": description,
        "effect": effect,
        "request_schema": request_schema,
        "preconditions": [
            "The selected observable robot state is finite at call start.",
            "Every request value satisfies the closed calibrated schema.",
        ],
        "temporal_semantics": {
            "kind": "bounded_closed_loop",
            "completion": "Stop on observed convergence or the request duration bound.",
        },
        "invariants": [
            "Commands remain within public joint and actuator limits.",
            "The implementation advances only the supplied canonical MuJoCo session.",
        ],
        "failure_behavior": (
            "Stop active commands and return a bounded error if convergence is not "
            "observed within the request duration or public limits."
        ),
        "criteria": [criterion],
        "evidence_refs": [copy.deepcopy(evidence_ref)],
    }


def _identifier(value: str) -> str:
    result = "".join(character if character.isalnum() else "_" for character in value)
    return "_".join(part for part in result.lower().split("_") if part)


def _entity_body_name(
    model: Any,
    *,
    entity_kind: str,
    entity_name: str,
) -> str:
    if entity_kind == "body":
        return entity_name
    site_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, entity_name))
    if site_id < 0:
        raise ValueError(f"unknown site {entity_name!r}")
    body_id = int(model.site_bodyid[site_id])
    body_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
    if not body_name:
        raise ValueError(f"site {entity_name!r} has no named parent body")
    return str(body_name)


def _site_position_in_body_frame(
    model: Any,
    data: Any,
    *,
    site_name: str,
    body_name: str,
) -> list[float]:
    site_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name))
    body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name))
    if site_id < 0 or body_id < 0:
        raise ValueError(f"unknown site/body frame {site_name!r}/{body_name!r}")
    delta = [
        float(data.site_xpos[site_id][index] - data.xpos[body_id][index])
        for index in range(3)
    ]
    rotation = data.xmat[body_id]
    return [
        sum(float(rotation[row * 3 + column]) * delta[row] for row in range(3))
        for column in range(3)
    ]


def _validated_pair(
    package: Any,
    capabilities: list[dict[str, Any]],
    case_specs: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    robot_id = package.robot_configuration_id
    design = validate_capability_design(
        {
            "artifact_type": "capability_design",
            "schema_version": "2.0",
            "capability_protocol_version": "capability-v2",
            "robot_configuration_id": robot_id,
            "package_version": package.package_version,
            "task_snapshot_id": package.snapshot_id,
            "invocation_abi": copy.deepcopy(CAPABILITY_INVOCATION_ABI),
            "capabilities": capabilities,
            "task_support": _support(
                package,
                capabilities,
                reference_robot=False,
            ),
        },
        package,
    )
    validated_capabilities = {
        item["capability_id"]: item for item in design["capabilities"]
    }
    cases: list[dict[str, Any]] = []
    for spec in case_specs:
        capability_id = str(spec["capability_id"])
        capability = validated_capabilities[capability_id]
        for role_index, role in enumerate(("nominal", "calibrated_boundary")):
            instance = spec["instance"]
            cases.append(
                {
                    "case_id": f"{robot_id}-{capability_id.lower()}-{role}",
                    "case_role": role,
                    "capability_id": capability_id,
                    "method_name": capability["method_name"],
                    "request": copy.deepcopy(spec["requests"][role_index]),
                    "request_grounding_refs": [copy.deepcopy(spec["evidence_ref"])],
                    "instance_id": instance["instance_id"],
                    "measurement_binding": copy.deepcopy(spec["binding"]),
                    "guard_ids": copy.deepcopy(instance["guard_ids"]),
                    "repetitions": instance["repetitions"],
                    "timeout_sim_s": instance["timeout_sim_s"],
                    "criteria": copy.deepcopy(capability["criteria"]),
                }
            )
    suite = validate_capability_validation_suite(
        {
            "artifact_type": "capability_validation_suite",
            "schema_version": "2.0",
            "capability_protocol_version": "capability-v2",
            "robot_configuration_id": robot_id,
            "package_version": package.package_version,
            "task_snapshot_id": package.snapshot_id,
            "whole_suite_aggregation": {"kind": "all_cases"},
            "cases": cases,
        },
        package=package,
        design=design,
    )
    return design, suite


def _generic_pair(
    package: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the reach-grounded serial-arm and Stretch fixed contracts."""

    robot_id = package.robot_configuration_id
    config = GENERIC_CONFIG[robot_id]
    instance = _reach_instance(package)
    scene_path = package.root / str(instance["scene_entrypoint"])
    entities = inspect_scene_entities(scene_path)
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, instance.get("reset"))
    target_position, reach_criterion = _reach_target_and_criterion(package, instance)
    reach_threshold = float(reach_criterion["threshold"])
    reach_comparator = str(reach_criterion["comparator"])
    timestep_s = float(model.opt.timestep)
    timeout_sim_s = float(instance["timeout_sim_s"])
    duration_values = [timeout_sim_s, timeout_sim_s]
    task_id = str(instance["task_id"])
    clause_id = str(reach_criterion["clause_id"])
    capabilities: list[dict[str, Any]] = []
    case_specs: list[dict[str, Any]] = []
    calibration_capabilities: dict[str, Any] = {}

    entity_kind, entity_name, entity_label, _target_selector = config["endpoints"][0]
    entity_kind = str(entity_kind)
    entity_name = str(entity_name)
    entity_label = str(entity_label)
    entity_key = "sites" if entity_kind == "site" else "bodies"
    if entity_name not in entities[entity_key]:
        raise ValueError(f"{robot_id} lacks {entity_kind} {entity_name!r}")
    initial_position = _entity_position(
        model,
        data,
        entity_kind=entity_kind,
        entity_name=entity_name,
    )
    target_delta = [
        target - initial
        for target, initial in zip(target_position, initial_position, strict=True)
    ]
    reach_distance = math.sqrt(sum(value * value for value in target_delta))
    if reach_distance <= 2.0 * reach_threshold:
        raise ValueError(
            f"{robot_id} reset-to-target distance {reach_distance:g} is too small"
        )
    midpoint = [
        initial + 0.5 * delta
        for initial, delta in zip(initial_position, target_delta, strict=True)
    ]
    direction = [value / reach_distance for value in target_delta]
    position_ref = _evidence_ref(
        robot_id,
        source_id="public_reach_and_real_reset",
        specific_reference=(
            f"tasks/catalog.json {task_id}/{clause_id} supplies the exact public "
            f"criterion {reach_comparator} {reach_threshold:.12g} m. Real instance "
            f"{instance['instance_id']} in {instance['scene_entrypoint']} supplies "
            f"target {_display_vector(target_position)} and its declared reset "
            f"places {entity_kind} {entity_name} at "
            f"{_display_vector(initial_position)} m. Request bounds are the exact "
            "coordinate-wise reset/target envelope; nominal is the deterministic "
            "midpoint and calibrated-boundary is the package target. Duration "
            f"bounds are timestep {timestep_s:.12g} s through instance timeout "
            f"{timeout_sim_s:.12g} s; both cases receive that same timeout so "
            "difficulty differs only by target. The midpoint is a fixed protocol "
            "formula, not empirical calibration."
        ),
    )
    coordinate_properties = {
        axis: _number_schema(
            unit="m",
            frame="world",
            minimum=min(initial, target),
            maximum=max(initial, target),
            evidence_ref=position_ref,
        )
        for axis, initial, target in zip(
            ("x", "y", "z"), initial_position, target_position, strict=True
        )
    }
    position_metric = f"terminal {entity_label} world-position error"
    capabilities.append(
        _capability(
            capability_id="C1",
            method_name="move_observed_tool_to_position",
            description=f"Move the observable {entity_label} to a bounded world position.",
            effect=f"Closed-loop world-position regulation of {entity_name}.",
            request_schema={
                "type": "object",
                "properties": {
                    "target_position": {
                        "type": "object",
                        "properties": coordinate_properties,
                        "required": ["x", "y", "z"],
                        "additionalProperties": False,
                    },
                    "max_duration_s": _number_schema(
                        unit="s",
                        frame="none",
                        minimum=timestep_s,
                        maximum=timeout_sim_s,
                        evidence_ref=position_ref,
                    ),
                },
                "required": ["target_position", "max_duration_s"],
                "additionalProperties": False,
            },
            criterion=_criterion(
                metric=position_metric,
                unit="m",
                comparator=reach_comparator,
                threshold=reach_threshold,
                evidence_ref=position_ref,
            ),
            evidence_ref=position_ref,
        )
    )
    if entity_kind == "site":
        position_binding = {
            "metric": position_metric,
            "unit": "m",
            "kind": "final_site_frame_xyz_position_error",
            "parameters": {
                "site_name": entity_name,
                "reference_body_name": "world",
                "target_x_argument": "request.target_position.x",
                "target_y_argument": "request.target_position.y",
                "target_z_argument": "request.target_position.z",
            },
        }
    else:
        position_binding = {
            "metric": position_metric,
            "unit": "m",
            "kind": "final_body_xyz_position_error",
            "parameters": {
                "body_name": entity_name,
                "target_x_argument": "request.target_position.x",
                "target_y_argument": "request.target_position.y",
                "target_z_argument": "request.target_position.z",
            },
        }
    case_specs.append(
        {
            "capability_id": "C1",
            "instance": instance,
            "evidence_ref": position_ref,
            "binding": position_binding,
            "requests": [
                {
                    "target_position": dict(zip(("x", "y", "z"), midpoint)),
                    "max_duration_s": duration_values[0],
                },
                {
                    "target_position": dict(zip(("x", "y", "z"), target_position)),
                    "max_duration_s": duration_values[1],
                },
            ],
        }
    )

    if config.get("include_displacement", True):
        displacement_body = _entity_body_name(
            model,
            entity_kind=entity_kind,
            entity_name=entity_name,
        )
        displacement_ref = _evidence_ref(
            robot_id,
            source_id="real_reset_to_target_displacement",
            specific_reference=(
                f"The same public {task_id}/{clause_id} tolerance is "
                f"{reach_comparator} {reach_threshold:.12g} m. Real reset-to-target "
                f"vector {_display_vector(target_delta)} has length "
                f"{reach_distance:.12g} m and normalized world direction "
                f"{_display_vector(direction)}. Cases request exactly 50% and 100% "
                "of that measured vector length; the trusted operator measures "
                "requested displacement error, not mere positive progress. The "
                "50% point is a declared protocol choice, not empirical calibration."
            ),
        )
        displacement_metric = (
            f"terminal {entity_label} requested world-direction displacement error"
        )
        displacement_capability = _capability(
                capability_id="C2",
                method_name="move_observed_tool_along_direction",
                description=(
                    f"Move {entity_label} a requested distance along a world direction."
                ),
                effect=(
                    f"Requested displacement of body {displacement_body} within the "
                    "public reach tolerance."
                ),
                request_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "object",
                            "properties": {
                                axis: _number_schema(
                                    unit="dimensionless",
                                    frame="world",
                                    minimum=-1.0,
                                    maximum=1.0,
                                    evidence_ref=displacement_ref,
                                )
                                for axis in ("x", "y", "z")
                            },
                            "required": ["x", "y", "z"],
                            "additionalProperties": False,
                        },
                        "distance_m": _number_schema(
                            unit="m",
                            frame="world",
                            minimum=0.5 * reach_distance,
                            maximum=reach_distance,
                            evidence_ref=displacement_ref,
                        ),
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=timestep_s,
                            maximum=timeout_sim_s,
                            evidence_ref=displacement_ref,
                        ),
                    },
                    "required": ["direction", "distance_m", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=displacement_metric,
                    unit="m",
                    comparator=reach_comparator,
                    threshold=reach_threshold,
                    evidence_ref=displacement_ref,
                ),
                evidence_ref=displacement_ref,
            )
        displacement_capability["preconditions"].append(
            "The requested world-frame unit direction vector has Euclidean norm 1 "
            "within absolute tolerance 1e-6."
        )
        displacement_capability["invariants"].append(
            "The trusted measurement normalizes no malformed direction; the "
            "unit-vector precondition remains true for the sealed request."
        )
        capabilities.append(displacement_capability)
        direction_request = dict(zip(("x", "y", "z"), direction))
        if entity_kind == "site":
            displacement_kind = "final_site_directional_displacement_error"
            displacement_entity_parameters = {"site_name": entity_name}
        else:
            displacement_kind = "final_body_directional_displacement_error"
            displacement_entity_parameters = {"body_name": displacement_body}
        case_specs.append(
            {
                "capability_id": "C2",
                "instance": instance,
                "evidence_ref": displacement_ref,
                "binding": {
                    "metric": displacement_metric,
                    "unit": "m",
                    "kind": displacement_kind,
                    "parameters": {
                        **displacement_entity_parameters,
                        "direction_x_argument": "request.direction.x",
                        "direction_y_argument": "request.direction.y",
                        "direction_z_argument": "request.direction.z",
                        "target_distance_argument": "request.distance_m",
                    },
                },
                "requests": [
                    {
                        "direction": direction_request,
                        "distance_m": 0.5 * reach_distance,
                        "max_duration_s": duration_values[0],
                    },
                    {
                        "direction": direction_request,
                        "distance_m": reach_distance,
                        "max_duration_s": duration_values[1],
                    },
                ],
            }
        )

    for joint_name, joint_label in config["joints"]:
        joint_unit, lower, upper, initial = _joint_calibration(
            model,
            data,
            joint_name=str(joint_name),
            entities=entities,
        )
        span = upper - lower
        if span <= 0.0:
            raise ValueError(f"{robot_id} joint {joint_name!r} has empty range")
        capability_id = f"C{len(capabilities) + 1}"
        method_name = f"set_{_identifier(str(joint_label))}_position"
        metric = f"terminal {joint_label} position error"
        nominal, boundary, tolerance, calibration_record, calibration_detail = (
            _measured_joint_contract(
                robot_id=robot_id,
                model=model,
                instance=instance,
                capability_id=capability_id,
                method_name=method_name,
                metric=metric,
                unit=joint_unit,
                joint_name=str(joint_name),
                lower=lower,
                upper=upper,
                reset_position=initial,
                nominal_duration_s=timeout_sim_s,
                boundary_duration_s=timeout_sim_s,
            )
        )
        calibration_capabilities[capability_id] = calibration_record
        joint_ref = _evidence_ref(
            robot_id,
            source_id=str(calibration_record["criterion_source_id"]),
            specific_reference=(
                f"Real scene {instance['scene_entrypoint']} declares joint "
                f"{joint_name} with exact {joint_unit} range "
                f"[{lower:.12g}, {upper:.12g}], and the declared reset resolves to "
                f"{initial:.12g}. Request bounds are narrowed to the measured "
                f"nominal/boundary envelope [{min(nominal, boundary):.12g}, "
                f"{max(nominal, boundary):.12g}]. {calibration_detail}"
            ),
        )
        joint_ref["source_id"] = str(calibration_record["criterion_source_id"])
        capabilities.append(
            _capability(
                capability_id=capability_id,
                method_name=method_name,
                description=f"Set the observable {joint_label} joint position.",
                effect=f"Closed-loop scalar regulation of {joint_name}.",
                request_schema={
                    "type": "object",
                    "properties": {
                        "target_position": _number_schema(
                            unit=joint_unit,
                            frame="joint",
                            minimum=min(nominal, boundary),
                            maximum=max(nominal, boundary),
                            evidence_ref=joint_ref,
                        ),
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=timestep_s,
                            maximum=timeout_sim_s,
                            evidence_ref=joint_ref,
                        ),
                    },
                    "required": ["target_position", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=metric,
                    unit=joint_unit,
                    comparator="<=",
                    threshold=tolerance,
                    evidence_ref=joint_ref,
                ),
                evidence_ref=joint_ref,
            )
        )
        case_specs.append(
            {
                "capability_id": capability_id,
                "instance": instance,
                "evidence_ref": joint_ref,
                "binding": {
                    "metric": metric,
                    "unit": joint_unit,
                    "kind": "final_joint_position_error",
                    "parameters": {
                        "joint_name": joint_name,
                        "target_argument": "request.target_position",
                    },
                },
                "requests": [
                    {
                        "target_position": nominal,
                        "max_duration_s": duration_values[0],
                    },
                    {
                        "target_position": boundary,
                        "max_duration_s": duration_values[1],
                    },
                ],
            }
        )

    base_body = config.get("base_displacement_body")
    if isinstance(base_body, str):
        b1_path = (
            "experiment/experiment1a_generation/validation/"
            "B1_DRIVER_VALIDATION_CRITERIA.md#st1"
        )
        base_ref = _evidence_ref(
            robot_id,
            source_id="tracked_st1_base_translation",
            specific_reference=(
                f"{b1_path} specifies the tracked Stretch ST1 translation-position "
                "error bound 0.020 m and request travel envelope +/-0.20 m. The "
                f"selected real scene contains body {base_body}; cases use exact "
                "0.10 m and boundary 0.20 m world-x translations, both with the "
                "same tracked 8 s bound. This is a fixed public protocol, not "
                "an empirical capability claim."
            ),
        )
        capability_id = f"C{len(capabilities) + 1}"
        metric = "terminal mobile-base requested world-direction displacement error"
        capabilities.append(
            _capability(
                capability_id=capability_id,
                method_name="move_mobile_base_along_world_direction",
                description="Translate the Stretch mobile base along a world direction.",
                effect="Requested planar base displacement within 0.020 m.",
                request_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "object",
                            "properties": {
                                axis: _number_schema(
                                    unit="dimensionless",
                                    frame="world",
                                    minimum=-1.0,
                                    maximum=1.0,
                                    evidence_ref=base_ref,
                                )
                                for axis in ("x", "y", "z")
                            },
                            "required": ["x", "y", "z"],
                            "additionalProperties": False,
                        },
                        "distance_m": _number_schema(
                            unit="m",
                            frame="world",
                            minimum=0.10,
                            maximum=0.20,
                            evidence_ref=base_ref,
                        ),
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=8.0,
                            maximum=8.0,
                            evidence_ref=base_ref,
                        ),
                    },
                    "required": ["direction", "distance_m", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=metric,
                    unit="m",
                    comparator="<=",
                    threshold=0.020,
                    evidence_ref=base_ref,
                ),
                evidence_ref=base_ref,
            )
        )
        case_specs.append(
            {
                "capability_id": capability_id,
                "instance": instance,
                "evidence_ref": base_ref,
                "binding": {
                    "metric": metric,
                    "unit": "m",
                    "kind": "final_body_directional_displacement_error",
                    "parameters": {
                        "body_name": base_body,
                        "direction_x_argument": "request.direction.x",
                        "direction_y_argument": "request.direction.y",
                        "direction_z_argument": "request.direction.z",
                        "target_distance_argument": "request.distance_m",
                    },
                },
                "requests": [
                    {
                        "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
                        "distance_m": 0.10,
                        "max_duration_s": 8.0,
                    },
                    {
                        "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
                        "distance_m": 0.20,
                        "max_duration_s": 8.0,
                    },
                ],
            }
        )
    design, suite = _validated_pair(package, capabilities, case_specs)
    return design, suite, {
        "instance_id": str(instance["instance_id"]),
        "scene_entrypoint": str(instance["scene_entrypoint"]),
        "capabilities": calibration_capabilities,
    }


def _instance_by_task(package: Any, task_id: str) -> dict[str, Any]:
    for instance in _private_instances(package):
        if instance.get("task_id") == task_id:
            return copy.deepcopy(instance)
    raise ValueError(f"{package.robot_configuration_id} lacks instance for {task_id}")


def _leap_pair(package: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build four fingertip contracts plus the public 16-joint pose contract."""

    robot_id = package.robot_configuration_id
    reach_instance = _instance_by_task(package, "gym_hand_reach_all_fingertips")
    reach_scene = package.root / str(reach_instance["scene_entrypoint"])
    model = mujoco.MjModel.from_xml_path(str(reach_scene))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, reach_instance.get("reset"))
    public_request = reach_instance["public_arguments"]["request"]["task_parameters"]
    raw_targets = public_request["target_fingertip_positions_m"]
    if not isinstance(raw_targets, list) or len(raw_targets) != 12:
        raise ValueError("LEAP reach instance needs twelve target coordinates")
    task = next(
        item
        for item in package.tasks
        if item["task_id"] == "gym_hand_reach_all_fingertips"
    )
    source = task["scoring"][0]
    threshold = float(source["threshold"])
    comparator = str(source["comparator"])
    timestep_s = float(model.opt.timestep)
    timeout_s = float(reach_instance["timeout_sim_s"])
    capabilities: list[dict[str, Any]] = []
    case_specs: list[dict[str, Any]] = []
    fingertips = (
        ("if_tip", "index fingertip", 0),
        ("mf_tip", "middle fingertip", 3),
        ("rf_tip", "ring fingertip", 6),
        ("th_tip", "thumb fingertip", 9),
    )
    for site_name, label, offset in fingertips:
        initial = _site_position_in_body_frame(
            model,
            data,
            site_name=site_name,
            body_name="palm",
        )
        target = [float(value) for value in raw_targets[offset : offset + 3]]
        midpoint = [
            start + 0.5 * (goal - start)
            for start, goal in zip(initial, target, strict=True)
        ]
        distance = math.sqrt(
            sum((goal - start) ** 2 for start, goal in zip(initial, target, strict=True))
        )
        if distance <= threshold:
            raise ValueError(f"LEAP {site_name} target is not discriminating")
        evidence_ref = _evidence_ref(
            robot_id,
            source_id=f"{site_name}_public_reach",
            specific_reference=(
                "tasks/catalog.json gym_hand_reach_all_fingertips/"
                f"{source['clause_id']} gives the exact concatenated four-tip "
                f"criterion {comparator} {threshold:.12g} m. Applying that same "
                f"strict global bound to the individual {label} is a conservative "
                "primitive adaptation. The real home reset places the site at "
                f"{_display_vector(initial)} m in palm frame; the private instance "
                f"supplies target {_display_vector(target)}. Bounds are their exact "
                "coordinate envelope; nominal is the midpoint and boundary is the "
                "full target. No grasp state is assumed."
            ),
        )
        capability_id = f"C{len(capabilities) + 1}"
        metric = f"terminal {label} palm-frame position error"
        capabilities.append(
            _capability(
                capability_id=capability_id,
                method_name=f"move_{_identifier(label)}_to_position",
                description=f"Move the {label} to a bounded palm-frame position.",
                effect=f"Closed-loop palm-frame regulation of site {site_name}.",
                request_schema={
                    "type": "object",
                    "properties": {
                        "target_position": {
                            "type": "object",
                            "properties": {
                                axis: _number_schema(
                                    unit="m",
                                    frame="palm",
                                    minimum=min(start, goal),
                                    maximum=max(start, goal),
                                    evidence_ref=evidence_ref,
                                )
                                for axis, start, goal in zip(
                                    ("x", "y", "z"), initial, target, strict=True
                                )
                            },
                            "required": ["x", "y", "z"],
                            "additionalProperties": False,
                        },
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=timestep_s,
                            maximum=timeout_s,
                            evidence_ref=evidence_ref,
                        ),
                    },
                    "required": ["target_position", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=metric,
                    unit="m",
                    comparator=comparator,
                    threshold=threshold,
                    evidence_ref=evidence_ref,
                ),
                evidence_ref=evidence_ref,
            )
        )
        case_specs.append(
            {
                "capability_id": capability_id,
                "instance": reach_instance,
                "evidence_ref": evidence_ref,
                "binding": {
                    "metric": metric,
                    "unit": "m",
                    "kind": "final_site_frame_xyz_position_error",
                    "parameters": {
                        "site_name": site_name,
                        "reference_body_name": "palm",
                        "target_x_argument": "request.target_position.x",
                        "target_y_argument": "request.target_position.y",
                        "target_z_argument": "request.target_position.z",
                    },
                },
                "requests": [
                    {
                        "target_position": dict(zip(("x", "y", "z"), midpoint)),
                        "max_duration_s": timeout_s,
                    },
                    {
                        "target_position": dict(zip(("x", "y", "z"), target)),
                        "max_duration_s": timeout_s,
                    },
                ],
            }
        )

    pose_instance = _instance_by_task(package, "robel_dclaw_pose_fixed")
    pose_scene = package.root / str(pose_instance["scene_entrypoint"])
    pose_model = mujoco.MjModel.from_xml_path(str(pose_scene))
    pose_data = mujoco.MjData(pose_model)
    apply_framework_reset(mujoco, pose_model, pose_data, pose_instance.get("reset"))
    joint_names = [
        "if_mcp", "if_rot", "if_pip", "if_dip",
        "mf_mcp", "mf_rot", "mf_pip", "mf_dip",
        "rf_mcp", "rf_rot", "rf_pip", "rf_dip",
        "th_cmc", "th_axl", "th_mcp", "th_ipl",
    ]
    pose_targets = [
        float(value)
        for value in pose_instance["public_arguments"]["request"]["task_parameters"][
            "target_joint_positions_rad"
        ]
    ]
    pose_entities = inspect_scene_entities(pose_scene)
    reset_positions: list[float] = []
    all_lowers: list[float] = []
    all_uppers: list[float] = []
    ranges: list[str] = []
    for joint_name in joint_names:
        unit, lower, upper, initial = _joint_calibration(
            pose_model,
            pose_data,
            joint_name=joint_name,
            entities=pose_entities,
        )
        if unit != "rad":
            raise ValueError(f"LEAP joint {joint_name} is not angular")
        reset_positions.append(initial)
        all_lowers.append(lower)
        all_uppers.append(upper)
        ranges.append(f"{joint_name}=[{lower:.12g},{upper:.12g}]")
    midpoint_targets = [
        start + 0.5 * (goal - start)
        for start, goal in zip(reset_positions, pose_targets, strict=True)
    ]
    pose_task = next(
        item for item in package.tasks if item["task_id"] == "robel_dclaw_pose_fixed"
    )
    pose_source = pose_task["scoring"][0]
    pose_threshold = float(pose_source["threshold"])
    control_period_s = 4.0 / 80.0
    physics_steps = round(control_period_s / float(pose_model.opt.timestep))
    pose_ref = _evidence_ref(
        robot_id,
        source_id="public_sixteen_joint_pose",
        specific_reference=(
            "tasks/catalog.json robel_dclaw_pose_fixed/"
            f"{pose_source['clause_id']} supplies the exact maximum-over-16-joints "
            f"criterion {pose_source['comparator']} {pose_threshold:.12g} rad. "
            "The private public request supplies target "
            f"{_display_vector(pose_targets)}; home reset is "
            f"{_display_vector(reset_positions)}. Exact MJCF ranges are "
            f"{'; '.join(ranges)}. The shared array schema uses their enclosing "
            f"range [{min(all_lowers):.12g}, {max(all_uppers):.12g}] while the two "
            "cases use midpoint/full target vectors. Source timing is 80 control "
            f"steps in 4 s, resolving to {physics_steps} physics steps/control step."
        ),
    )
    capability_id = "C5"
    metric = "terminal maximum absolute error over all 16 hand joints"
    capabilities.append(
        _capability(
            capability_id=capability_id,
            method_name="set_all_hand_joint_positions",
            description="Move all sixteen LEAP joints to one bounded pose.",
            effect="Simultaneous terminal regulation of all sixteen hand joints.",
            request_schema={
                "type": "object",
                "properties": {
                    "target_joint_positions_rad": {
                        "type": "array",
                        "items": _number_schema(
                            unit="rad",
                            frame="joint",
                            minimum=min(all_lowers),
                            maximum=max(all_uppers),
                            evidence_ref=pose_ref,
                        ),
                        "minItems": 16,
                        "maxItems": 16,
                        "unit": "rad",
                        "frame": "joint",
                    },
                    "control_steps": {
                        "type": "integer",
                        "unit": "count",
                        "frame": "none",
                        "minimum": 80,
                        "maximum": 80,
                        "evidence_refs": [copy.deepcopy(pose_ref)],
                    },
                },
                "required": ["target_joint_positions_rad", "control_steps"],
                "additionalProperties": False,
            },
            criterion=_criterion(
                metric=metric,
                unit="rad",
                comparator=str(pose_source["comparator"]),
                threshold=pose_threshold,
                evidence_ref=pose_ref,
            ),
            evidence_ref=pose_ref,
        )
    )
    case_specs.append(
        {
            "capability_id": capability_id,
            "instance": pose_instance,
            "evidence_ref": pose_ref,
            "binding": {
                "metric": metric,
                "unit": "rad",
                "kind": "final_maximum_joint_position_error",
                "parameters": {
                    "joint_names": joint_names,
                    "target_argument": "request.target_joint_positions_rad",
                    "physics_steps_per_control_step": physics_steps,
                    "control_steps_argument": "request.control_steps",
                },
            },
            "requests": [
                {
                    "target_joint_positions_rad": midpoint_targets,
                    "control_steps": 80,
                },
                {
                    "target_joint_positions_rad": pose_targets,
                    "control_steps": 80,
                },
            ],
        }
    )
    return _validated_pair(package, capabilities, case_specs)


def _aloha_pair(
    package: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build symmetric left/right endpoint, gripper, and wrist contracts."""

    robot_id = package.robot_configuration_id
    capability_dir = package.root / "capability_validation" / "private"
    instance_document = _read(capability_dir / "instances.json")
    instances = {
        str(item["instance_id"]): copy.deepcopy(item)
        for item in instance_document["instances"]
    }
    side_instances = {
        "left": instances["aloha-2-capability-left-side"],
        "right": instances["aloha-2-capability-right-side"],
    }
    b1_suite_path = (
        ROOT
        / "experiment"
        / "experiment1a_generation"
        / "validation"
        / "fixed_validation_bundles"
        / "aloha_2"
        / "capability_validation_suite.json"
    )
    b1_suite = _read(b1_suite_path)
    endpoint_targets: dict[str, list[float]] = {}
    for case in b1_suite["cases"]:
        if case.get("capability_id") != "AL1":
            continue
        request = case.get("request", {})
        side = request.get("arm")
        target = request.get("target_position_world_m")
        if side in {"left", "right"} and side not in endpoint_targets:
            endpoint_targets[str(side)] = [float(value) for value in target]
    if set(endpoint_targets) != {"left", "right"}:
        raise ValueError("tracked ALOHA AL1 reference lacks symmetric targets")

    scene_path = package.root / str(side_instances["left"]["scene_entrypoint"])
    entities = inspect_scene_entities(scene_path)
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    apply_framework_reset(mujoco, model, data, side_instances["left"].get("reset"))
    timestep_s = float(model.opt.timestep)
    capabilities: list[dict[str, Any]] = []
    case_specs: list[dict[str, Any]] = []
    calibration_capabilities: dict[str, Any] = {}

    for side in ("left", "right"):
        site_name = f"{side}/gripper"
        instance = side_instances[side]
        initial = _entity_position(
            model,
            data,
            entity_kind="site",
            entity_name=site_name,
        )
        target = endpoint_targets[side]
        midpoint = [
            start + 0.5 * (goal - start)
            for start, goal in zip(initial, target, strict=True)
        ]
        distance = math.sqrt(
            sum((goal - start) ** 2 for start, goal in zip(initial, target, strict=True))
        )
        if distance <= 0.015:
            raise ValueError(f"ALOHA {side} endpoint target is not discriminating")
        evidence_ref = _evidence_ref(
            robot_id,
            source_id=f"tracked_al1_{side}_endpoint",
            specific_reference=(
                "experiment/experiment1a_generation/validation/"
                "B1_DRIVER_VALIDATION_CRITERIA.md#al1 fixes endpoint error "
                "<= 0.015 m; its tracked fixed ALOHA suite supplies the symmetric "
                f"{side} target {_display_vector(target)}. Capability-only instance "
                f"{instance['instance_id']} loads {instance['scene_entrypoint']} at "
                f"neutral_pose, yielding reset site position "
                f"{_display_vector(initial)}. The array bounds are the exact shared "
                "numeric envelope of reset and target; nominal is their midpoint, "
                "boundary is the full tracked target; both cases receive the same "
                "8 s capability-only instance timeout."
            ),
        )
        capability_id = f"C{len(capabilities) + 1}"
        metric = f"terminal {side}-arm tool world-position error"
        shared_minimum = min([*initial, *target])
        shared_maximum = max([*initial, *target])
        capabilities.append(
            _capability(
                capability_id=capability_id,
                method_name=f"move_{side}_tool_to_position",
                description=f"Move the ALOHA {side} tool to a world position.",
                effect=f"Closed-loop world-position regulation of {site_name}.",
                request_schema={
                    "type": "object",
                    "properties": {
                        "target_position_m": {
                            "type": "array",
                            "items": _number_schema(
                                unit="m",
                                frame="world",
                                minimum=shared_minimum,
                                maximum=shared_maximum,
                                evidence_ref=evidence_ref,
                            ),
                            "minItems": 3,
                            "maxItems": 3,
                            "unit": "m",
                            "frame": "world",
                        },
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=timestep_s,
                            maximum=8.0,
                            evidence_ref=evidence_ref,
                        ),
                    },
                    "required": ["target_position_m", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=metric,
                    unit="m",
                    comparator="<=",
                    threshold=0.015,
                    evidence_ref=evidence_ref,
                ),
                evidence_ref=evidence_ref,
            )
        )
        case_specs.append(
            {
                "capability_id": capability_id,
                "instance": instance,
                "evidence_ref": evidence_ref,
                "binding": {
                    "metric": metric,
                    "unit": "m",
                    "kind": "final_site_position_error",
                    "parameters": {
                        "site_name": site_name,
                        "target_argument": "request.target_position_m",
                    },
                },
                "requests": [
                    {"target_position_m": midpoint, "max_duration_s": 8.0},
                    {"target_position_m": target, "max_duration_s": 8.0},
                ],
            }
        )

    joint_specs = (
        ("left", "left/left_finger", "left gripper"),
        ("right", "right/left_finger", "right gripper"),
        ("left", "left/wrist_rotate", "left wrist"),
        ("right", "right/wrist_rotate", "right wrist"),
    )
    for side, joint_name, label in joint_specs:
        instance = side_instances[side]
        unit, lower, upper, initial = _joint_calibration(
            model,
            data,
            joint_name=joint_name,
            entities=entities,
        )
        span = upper - lower
        capability_id = f"C{len(capabilities) + 1}"
        method_name = f"set_{_identifier(label)}_position"
        metric = f"terminal {label} position error"
        nominal, boundary, tolerance, calibration_record, calibration_detail = (
            _measured_joint_contract(
                robot_id=robot_id,
                model=model,
                instance=instance,
                capability_id=capability_id,
                method_name=method_name,
                metric=metric,
                unit=unit,
                joint_name=joint_name,
                lower=lower,
                upper=upper,
                reset_position=initial,
                nominal_duration_s=8.0,
                boundary_duration_s=8.0,
            )
        )
        calibration_capabilities[capability_id] = calibration_record
        evidence_ref = _evidence_ref(
            robot_id,
            source_id=str(calibration_record["criterion_source_id"]),
            specific_reference=(
                f"Capability-only instance {instance['instance_id']} permits the "
                f"{side} side and locks the opposite side. Real "
                f"{instance['scene_entrypoint']} declares {joint_name} with exact "
                f"{unit} range [{lower:.12g}, {upper:.12g}] and neutral reset "
                f"{initial:.12g}. Request bounds are the measured nominal/boundary "
                f"envelope [{min(nominal, boundary):.12g}, "
                f"{max(nominal, boundary):.12g}]. {calibration_detail}"
            ),
        )
        evidence_ref["source_id"] = str(calibration_record["criterion_source_id"])
        capabilities.append(
            _capability(
                capability_id=capability_id,
                method_name=method_name,
                description=f"Set the ALOHA {label} scalar joint position.",
                effect=f"Closed-loop scalar regulation of {joint_name}.",
                request_schema={
                    "type": "object",
                    "properties": {
                        "target_position": _number_schema(
                            unit=unit,
                            frame="joint",
                            minimum=min(nominal, boundary),
                            maximum=max(nominal, boundary),
                            evidence_ref=evidence_ref,
                        ),
                        "max_duration_s": _number_schema(
                            unit="s",
                            frame="none",
                            minimum=timestep_s,
                            maximum=8.0,
                            evidence_ref=evidence_ref,
                        ),
                    },
                    "required": ["target_position", "max_duration_s"],
                    "additionalProperties": False,
                },
                criterion=_criterion(
                    metric=metric,
                    unit=unit,
                    comparator="<=",
                    threshold=tolerance,
                    evidence_ref=evidence_ref,
                ),
                evidence_ref=evidence_ref,
            )
        )
        case_specs.append(
            {
                "capability_id": capability_id,
                "instance": instance,
                "evidence_ref": evidence_ref,
                "binding": {
                    "metric": metric,
                    "unit": unit,
                    "kind": "final_joint_position_error",
                    "parameters": {
                        "joint_name": joint_name,
                        "target_argument": "request.target_position",
                    },
                },
                "requests": [
                    {"target_position": nominal, "max_duration_s": 8.0},
                    {"target_position": boundary, "max_duration_s": 8.0},
                ],
            }
        )
    design, suite = _validated_pair(package, capabilities, case_specs)
    return design, suite, {
        "instance_id": "aloha-2-capability-left-side",
        "scene_entrypoint": str(side_instances["left"]["scene_entrypoint"]),
        "capabilities": calibration_capabilities,
    }


def _suite_instance(package: Any, suite: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(suite["cases"][0]["instance_id"])
    directories = [
        package.root / "capability_validation" / "private",
        package.private_dir,
    ]
    for directory in directories:
        path = directory / "instances.json"
        if not path.is_file():
            continue
        for instance in _read(path).get("instances", []):
            if instance.get("instance_id") == instance_id:
                return copy.deepcopy(instance)
    raise ValueError(f"cannot resolve suite instance {instance_id!r}")


def _smoke_record(
    *,
    package: Any,
    design: dict[str, Any],
    suite: dict[str, Any],
) -> dict[str, Any]:
    """Execute one real scene/reset/operator path without claiming capability PASS."""

    selected_case = copy.deepcopy(suite["cases"][0])
    single_case_suite = copy.deepcopy(suite)
    single_case_suite["cases"] = [selected_case]
    methods = [str(item["method_name"]) for item in design["capabilities"]]
    method_source = "\n".join(
        (
            f"    def {method}(self, request):\n"
            "        self._pulse()\n"
        )
        for method in methods
    )
    candidate_source = (
        "import mujoco\n\n"
        "class Driver:\n"
        "    def __init__(self, model, data):\n"
        "        self.model = model\n"
        "        self.data = data\n\n"
        "    def _pulse(self):\n"
        "        if self.model.nu <= 0:\n"
        "            raise RuntimeError('smoke scene has no actuator')\n"
        "        current = float(self.data.ctrl[0])\n"
        "        if bool(self.model.actuator_ctrllimited[0]):\n"
        "            lower = float(self.model.actuator_ctrlrange[0, 0])\n"
        "            upper = float(self.model.actuator_ctrlrange[0, 1])\n"
        "            delta = max((upper - lower) * 1.0e-6, 1.0e-8)\n"
        "            target = current + delta\n"
        "            if target > upper:\n"
        "                target = current - delta\n"
        "        else:\n"
        "            target = current + 1.0e-6\n"
        "        self.data.ctrl[0] = target\n"
        "        for _ in range(64):\n"
        "            mujoco.mj_step(self.model, self.data)\n\n"
        f"{method_source}\n"
        "def build(*, model, data):\n"
        "    return Driver(model, data)\n"
    )
    with tempfile.TemporaryDirectory(prefix="exp3-fixed-smoke-") as temporary:
        temporary_root = Path(temporary)
        candidate = temporary_root / "driver.py"
        candidate.write_text(candidate_source, encoding="utf-8")
        report = run_private_suite(
            package=package,
            design=design,
            suite=single_case_suite,
            driver_path=candidate,
            condition="from-scratch",
            output_dir=temporary_root / "harness",
            record_video=False,
            wall_timeout_s=60.0,
            run_id="experiment3-fixed-input-smoke",
            attempt=0,
        )
    trials = report.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError(f"{package.robot_configuration_id} smoke produced no trial")
    trial = trials[0]
    measurement = trial.get("measurement_value")
    if (
        isinstance(measurement, bool)
        or not isinstance(measurement, (int, float))
        or not math.isfinite(float(measurement))
        or trial.get("measurement_error") is not None
        or trial.get("candidate_exception") is not None
        or not trial.get("worker_completed")
        or not trial.get("method_invoked")
    ):
        raise ValueError(
            f"{package.robot_configuration_id} smoke did not execute a finite "
            f"measurement: {trial.get('measurement_error') or trial.get('candidate_exception')}"
        )
    instance = _suite_instance(package, suite)
    return {
        "instance_id": str(instance["instance_id"]),
        "scene_entrypoint": str(instance["scene_entrypoint"]),
        "case_id": str(selected_case["case_id"]),
        "operator_kind": str(selected_case["measurement_binding"]["kind"]),
        "scene_loaded": True,
        "reset_applied": True,
        "trusted_operator_executed": True,
        "measurement_finite": True,
        "measurement_value": float(measurement),
        "passed": True,
    }


def main() -> int:
    index: dict[str, Any] = {
        "artifact_type": "experiment3_fixed_capability_input_set",
        "schema_version": "1.0",
        "capability_protocol_version": "capability-v2",
        "input_set_id": "experiment3-eleven-robot-fixed-inputs-v1",
        "calibration_report": "calibration_report.json",
        "smoke_report": "smoke_report.json",
        "robots": {},
    }
    generated: dict[str, tuple[Any, dict[str, Any], dict[str, Any]]] = {}
    calibration_report: dict[str, Any] = {
        "artifact_type": "experiment3_fixed_threshold_calibration",
        "schema_version": "1.0",
        "input_set_id": index["input_set_id"],
        "passed": True,
        "robots": {},
    }
    for robot_id, version in VERSIONS.items():
        package = load_robot_package(ROBOT_ROOT / robot_id / version)
        if robot_id in {"robotstudio_so101", "unitree-go2-stock-12dof"}:
            design, suite = _reference_pair(package)
            instance = _suite_instance(package, suite)
            calibration = {
                "instance_id": str(instance["instance_id"]),
                "scene_entrypoint": str(instance["scene_entrypoint"]),
                "capabilities": {},
            }
            source_kind = "tracked_complete_worked_reference"
        elif robot_id == "leap_hand":
            design, suite = _leap_pair(package)
            instance = _suite_instance(package, suite)
            calibration = {
                "instance_id": str(instance["instance_id"]),
                "scene_entrypoint": str(instance["scene_entrypoint"]),
                "capabilities": {},
            }
            source_kind = "public_task_criteria_and_real_scene_calibration"
        elif robot_id == "aloha_2":
            design, suite, calibration = _aloha_pair(package)
            source_kind = "tracked_symmetric_reference_and_real_scene_calibration"
        else:
            design, suite, calibration = _generic_pair(package)
            source_kind = "package_reset_and_scene_calibration"
        generated[robot_id] = (package, design, suite)
        calibration_report["robots"][robot_id] = calibration
        index["robots"][robot_id] = {
            "package_version": package.package_version,
            "task_snapshot_id": package.snapshot_id,
            "capability_design": f"{robot_id}/capability_design.json",
            "capability_validation_suite": (
                f"{robot_id}/capability_validation_suite.json"
            ),
            "capability_count": len(design["capabilities"]),
            "case_count": len(suite["cases"]),
            "source_kind": source_kind,
        }
    smoke_report: dict[str, Any] = {
        "artifact_type": "experiment3_fixed_input_mujoco_smoke",
        "schema_version": "1.0",
        "input_set_id": index["input_set_id"],
        "passed": True,
        "robots": {},
    }
    for robot_id in VERSIONS:
        package, design, suite = generated[robot_id]
        smoke_report["robots"][robot_id] = _smoke_record(
            package=package,
            design=design,
            suite=suite,
        )

    # The index is written last and therefore acts as the completion marker for
    # one fully generated, validator-audited, calibrated, and smoke-executed set.
    for robot_id in VERSIONS:
        _package, design, suite = generated[robot_id]
        destination = OUTPUT_ROOT / robot_id
        _write(destination / "capability_design.json", design)
        _write(destination / "capability_validation_suite.json", suite)
    _write(OUTPUT_ROOT / "calibration_report.json", calibration_report)
    _write(OUTPUT_ROOT / "smoke_report.json", smoke_report)
    _write(OUTPUT_ROOT / "index.json", index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
