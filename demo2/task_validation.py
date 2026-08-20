"""Trusted direct-MuJoCo validator for Demo2 Tasks Library suites.

The generated driver is only allowed to execute motion.  Every verdict in this
module is computed from MuJoCo ``model``/``data`` state captured by this harness.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .precision_policy import policy_record, validate_compiled_position_standard


DEMO2_ROOT = Path(__file__).resolve().parent
LEGACY_ASSETS = DEMO2_ROOT / "legacy_assets"
LEGACY_CORE = DEMO2_ROOT / "legacy_core"
LEGACY_RUNTIME = DEMO2_ROOT / "legacy_runtime"

_SUPPORTED_INVOCATIONS = {
    "reach_above_object": "reach_above_object",
    "trace_square_and_return": "trace_cartesian_path",
    "gripper_open_close": "cycle_gripper",
    "controlled_contact": "establish_controlled_contact",
    "offset_and_return": "move_cartesian_offset_and_return",
    "push_object_to_goal": "push_object_to_goal",
    "stand_and_hold": "stand_up",
    "sit_and_hold": "sit",
    "hold_current_upright": "hold_stable",
    "walk_forward_and_stop": "walk_forward",
    "set_height_and_hold": "set_body_height",
    "reach_target": "reach_target",
    "visit_waypoints": "visit_cartesian_waypoints",
    "reject_unreachable_and_return_home": "reject_unreachable_and_return_home",
    "grasp_and_lift": "grasp_and_lift",
    "move_to_waypoint_and_return": "move_to_waypoint_and_return",
    # Menagerie task packages use the public effect name as the operator.
    "trace_cartesian_path": "trace_cartesian_path",
    "move_cartesian_offset_and_return": "move_cartesian_offset_and_return",
    "visit_cartesian_waypoints": "visit_cartesian_waypoints",
    "set_joint_posture_and_hold": "set_joint_posture_and_hold",
    "flex_index_finger_and_hold": "flex_index_finger_and_hold",
    "set_symmetric_finger_posture": "set_symmetric_finger_posture",
    "set_thumb_opposition_and_hold": "set_thumb_opposition_and_hold",
    "cycle_to_pregrasp_posture": "cycle_to_pregrasp_posture",
    "move_bimanual_targets": "move_bimanual_targets",
    "cycle_bimanual_grippers": "cycle_bimanual_grippers",
    "move_bimanual_offset_and_return": "move_bimanual_offset_and_return",
    "drive_base_forward": "drive_base_forward",
    "set_lift_height": "set_lift_height",
    "extend_arm": "extend_arm",
    "rotate_wrist": "rotate_wrist",
    "cycle_gripper": "cycle_gripper",
    "set_body_height_and_hold": "set_body_height_and_hold",
    "walk_bounded_direction_and_stop": "walk_bounded_direction_and_stop",
    "set_arm_joint_posture_and_hold": "set_arm_joint_posture_and_hold",
    "set_upper_body_posture_and_hold": "set_upper_body_posture_and_hold",
    "set_leg_posture_and_hold": "set_leg_posture_and_hold",
}

_SUPPORTED_MEASUREMENTS = {
    "distance",
    "history_waypoint_max_error",
    "history_final_return_error",
    "history_directional_delta",
    "contact_count",
    "contact_boolean",
    "offset_error",
    "body_height",
    "upright_score",
    "planar_speed",
    "height_ratio",
    "horizontal_drift",
    "initial_yaw_forward_displacement",
    "initial_yaw_lateral_displacement_abs",
    "body_height_error",
    "candidate_outcome",
    "invocation_count",
    "history_waypoint_order",
    "trace_baseline_delta",
    "joint_vector_error",
    "joint_symmetry_error",
    "initial_yaw_lateral_drift",
    "initial_yaw_lateral_displacement",
    "initial_yaw_forward_drift",
    "final_planar_speed",
}

_SUPPORTED_GUARDS = {
    "trusted-external-verdict",
    "trusted-mujoco-state",
    "finite-required-state",
    "scene-entrypoint-bound",
    "no-body-or-head-floor-contact",
}

_BASE_BODY_MEASUREMENTS = {
    "body_height",
    "upright_score",
    "planar_speed",
    "final_planar_speed",
    "height_ratio",
    "horizontal_drift",
    "initial_yaw_forward_displacement",
    "initial_yaw_lateral_displacement_abs",
    "initial_yaw_lateral_drift",
    "initial_yaw_lateral_displacement",
    "initial_yaw_forward_drift",
    "body_height_error",
}

_INVOCATION_EFFECT_ALIASES = {
    ("stand_and_hold", "stand_and_hold"),
}


def _supports_invocation_effect(operator: Any, effect: Any) -> bool:
    name = str(operator)
    return _SUPPORTED_INVOCATIONS.get(name) == effect or (name, effect) in _INVOCATION_EFFECT_ALIASES

_REQUIRED_TEST_FIELDS = {
    "requirement_id",
    "task_id",
    "capability_id",
    "effect",
    "method",
    "case_id",
    "scene_entrypoint",
    "reset",
    "parameters",
    "invocation",
    "criteria",
    "guards",
}


class TaskValidationError(RuntimeError):
    """A suite, driver, invocation, or physical observation is invalid."""


def _load_driver(path: Path) -> Any:
    if not path.is_file():
        raise TaskValidationError(f"missing generated driver: {path}")
    name = f"demo2_task_driver_{path.parent.name.replace('-', '_')}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TaskValidationError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "build", None)):
        raise TaskValidationError("driver.py must define callable build(mjcf_path=...)")
    return module


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float, int)):
        return float(value)
    return value


def _compare(actual: Any, comparator: str, threshold: Any) -> bool:
    actual = _json_scalar(actual)
    threshold = _json_scalar(threshold)
    if comparator == "==":
        return actual == threshold
    if isinstance(actual, bool) or isinstance(threshold, bool):
        raise TaskValidationError(f"boolean values only support ==, got {comparator!r}")
    a = float(actual)
    b = float(threshold)
    if not math.isfinite(a) or not math.isfinite(b):
        raise TaskValidationError("comparison contains NaN or infinity")
    if comparator == "<=":
        return a <= b
    if comparator == "<":
        return a < b
    if comparator == ">=":
        return a >= b
    if comparator == ">":
        return a > b
    raise TaskValidationError(f"unsupported comparator: {comparator!r}")


def _resolve_scene(asset_root: Path, entrypoint: Any) -> Path:
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise TaskValidationError("scene_entrypoint must be a non-empty string")
    relative = Path(entrypoint)
    if relative.is_absolute() or ".." in relative.parts:
        raise TaskValidationError("scene_entrypoint must be a safe relative path")
    normalized = relative
    parts = normalized.parts
    if parts[:2] == ("assets", "mjcf"):
        normalized = Path(*parts[2:])
    root = Path(asset_root).resolve()
    candidate = root / normalized
    if candidate.is_file():
        resolved = candidate.resolve()
        if resolved.is_relative_to(root):
            return resolved
    raise TaskValidationError(f"declared scene is unavailable: {entrypoint}")


def _mj_name(mj: Any, model: Any, object_type: Any, identifier: int) -> str:
    value = mj.mj_id2name(model, object_type, int(identifier))
    return str(value) if value is not None else f"id:{int(identifier)}"


def _name_id(mj: Any, model: Any, object_type: Any, name: str, label: str) -> int:
    identifier = int(mj.mj_name2id(model, object_type, name))
    if identifier < 0:
        raise TaskValidationError(f"required {label} {name!r} is absent from MJCF")
    return identifier


def _required_names(test: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    bodies: set[str] = set()
    sites: set[str] = set()
    joints: set[str] = set()

    def add_path(value: Any) -> None:
        if not isinstance(value, str):
            return
        fields = value.split(".")
        if len(fields) < 2:
            return
        if fields[0] == "bodies":
            bodies.add(fields[1])
        elif fields[0] == "sites":
            sites.add(fields[1])
        elif fields[0] == "joint_positions":
            joints.add(fields[1])

    for criterion in test.get("criteria", []):
        measurement = criterion.get("measurement", {}) if isinstance(criterion, Mapping) else {}
        add_path(measurement.get("observation_path"))
        reference = measurement.get("reference", {})
        if isinstance(reference, Mapping):
            add_path(reference.get("observation_path"))
        parameters = test.get("parameters", {})
        if isinstance(parameters, Mapping):
            names_path = measurement.get("joint_names_path")
            if isinstance(names_path, str) and names_path.startswith("parameters."):
                values = parameters.get(names_path.removeprefix("parameters."), [])
                if isinstance(values, list):
                    joints.update(str(value) for value in values if isinstance(value, str))
            pairs_path = measurement.get("joint_pairs_path")
            if isinstance(pairs_path, str) and pairs_path.startswith("parameters."):
                pairs = parameters.get(pairs_path.removeprefix("parameters."), [])
                if isinstance(pairs, list):
                    for pair in pairs:
                        if isinstance(pair, list):
                            joints.update(str(value) for value in pair if isinstance(value, str))
    parameters = test.get("parameters", {})
    if isinstance(parameters, Mapping):
        target = parameters.get("target_object_id")
        if isinstance(target, str):
            bodies.add(target)
    bodies.add("base_link")
    return bodies, sites, joints


@dataclass
class _Capture:
    robot: Any
    test: Mapping[str, Any]
    frames: list[Any]
    frame_stride: int = 20
    frame_cap: int = 450

    def __post_init__(self) -> None:
        self.model = getattr(self.robot, "model", None)
        self.data = getattr(self.robot, "data", None)
        self.mj = getattr(self.robot, "_mj", None)
        if self.model is None or self.data is None:
            raise TaskValidationError("driver.build() result must expose MuJoCo model and data")
        if self.mj is None:
            import mujoco

            self.mj = mujoco
        self.body_names, self.site_names, self.joint_names = _required_names(self.test)
        self._body_ids: dict[str, int] = {}
        self._site_ids: dict[str, int] = {}
        self._joint_adrs: dict[str, int] = {}
        for name in sorted(self.body_names):
            identifier = int(self.mj.mj_name2id(self.model, self.mj.mjtObj.mjOBJ_BODY, name))
            if identifier >= 0:
                self._body_ids[name] = identifier
        for name in sorted(self.site_names):
            self._site_ids[name] = _name_id(
                self.mj, self.model, self.mj.mjtObj.mjOBJ_SITE, name, "site"
            )
        for name in sorted(self.joint_names):
            identifier = _name_id(
                self.mj, self.model, self.mj.mjtObj.mjOBJ_JOINT, name, "joint"
            )
            self._joint_adrs[name] = int(self.model.jnt_qposadr[identifier])
        self.base_body = "base_link" if "base_link" in self._body_ids else None
        if self.base_body is None:
            spec = getattr(self.robot, "spec", None)
            candidate = getattr(spec, "base_body_name", None)
            if isinstance(candidate, str):
                identifier = int(
                    self.mj.mj_name2id(self.model, self.mj.mjtObj.mjOBJ_BODY, candidate)
                )
                if identifier >= 0:
                    self.base_body = candidate
                    self._body_ids[candidate] = identifier
        if self.base_body is None:
            declared: list[str] = []
            for criterion in self.test.get("criteria", []):
                measurement = (
                    criterion.get("measurement")
                    if isinstance(criterion, Mapping)
                    else None
                )
                if not isinstance(measurement, Mapping):
                    continue
                if measurement.get("operator") not in _BASE_BODY_MEASUREMENTS:
                    continue
                path = measurement.get("observation_path")
                fields = path.split(".") if isinstance(path, str) else []
                if len(fields) >= 2 and fields[0] == "bodies" and fields[1] in self._body_ids:
                    declared.append(fields[1])
            if declared:
                if len(set(declared)) != 1:
                    raise TaskValidationError(
                        "base-body measurements declare more than one physical body"
                    )
                self.base_body = declared[0]
        self.trace: list[dict[str, Any]] = []
        self.phase = "initial"
        self.ticks = 0
        self.original_step = self.robot.step

    def _contacts(self) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            sides: list[dict[str, Any]] = []
            for geom_id in (int(contact.geom1), int(contact.geom2)):
                body_id = int(self.model.geom_bodyid[geom_id])
                sides.append({
                    "geom": _mj_name(
                        self.mj, self.model, self.mj.mjtObj.mjOBJ_GEOM, geom_id
                    ),
                    "body": _mj_name(
                        self.mj, self.model, self.mj.mjtObj.mjOBJ_BODY, body_id
                    ),
                    "body_id": body_id,
                })
            contacts.append({"a": sides[0], "b": sides[1]})
        return contacts

    def snapshot(self) -> None:
        self.mj.mj_forward(self.model, self.data)
        bodies = {
            name: {
                "position": np.asarray(self.data.xpos[identifier], dtype=float).copy(),
                "rotation": np.asarray(self.data.xmat[identifier], dtype=float).reshape(3, 3).copy(),
            }
            for name, identifier in self._body_ids.items()
        }
        sites = {
            name: {
                "position": np.asarray(self.data.site_xpos[identifier], dtype=float).copy(),
                "rotation": np.asarray(self.data.site_xmat[identifier], dtype=float).reshape(3, 3).copy(),
            }
            for name, identifier in self._site_ids.items()
        }
        joints = {
            name: float(self.data.qpos[address])
            for name, address in self._joint_adrs.items()
        }
        planar_velocity = np.zeros(2, dtype=float)
        if self.base_body is not None:
            velocity = np.zeros(6, dtype=float)
            self.mj.mj_objectVelocity(
                self.model,
                self.data,
                self.mj.mjtObj.mjOBJ_BODY,
                self._body_ids[self.base_body],
                velocity,
                0,
            )
            planar_velocity = velocity[3:5].copy()
        finite = bool(
            np.isfinite(np.asarray(self.data.qpos)).all()
            and np.isfinite(np.asarray(self.data.qvel)).all()
            and all(
                np.isfinite(value["position"]).all()
                and np.isfinite(value["rotation"]).all()
                for value in (*bodies.values(), *sites.values())
            )
            and np.isfinite(planar_velocity).all()
            and all(math.isfinite(value) for value in joints.values())
        )
        self.trace.append({
            "time": float(self.data.time),
            "phase": self.phase,
            "bodies": bodies,
            "sites": sites,
            "joints": joints,
            "planar_velocity": planar_velocity,
            "contacts": self._contacts(),
            "finite": finite,
        })

    def _render(self) -> None:
        if len(self.frames) >= self.frame_cap or not callable(getattr(self.robot, "render", None)):
            return
        try:
            self.frames.append(self.robot.render())
        except Exception:
            pass

    def install(self) -> None:
        def captured_step(n: int = 1) -> None:
            for _ in range(int(n)):
                self.original_step(1)
                self.ticks += 1
                self.snapshot()
                if self.ticks % self.frame_stride == 0:
                    self._render()

        self.robot.step = captured_step
        self.snapshot()
        self._render()

    def restore(self) -> None:
        self.robot.step = self.original_step


def _reset_robot(robot: Any, reset: Mapping[str, Any]) -> None:
    model = getattr(robot, "model", None)
    data = getattr(robot, "data", None)
    mj = getattr(robot, "_mj", None)
    if model is None or data is None:
        raise TaskValidationError("driver.build() result must expose MuJoCo model and data")
    if mj is None:
        import mujoco

        mj = mujoco
    mode = reset.get("mode")
    if mode == "source_scene_default":
        mj.mj_resetData(model, data)
    elif mode in {"keyframe", "source_keyframe"}:
        keyframe = reset.get("keyframe")
        if not isinstance(keyframe, str) or not keyframe:
            raise TaskValidationError("keyframe reset requires a keyframe name")
        key = _name_id(mj, model, mj.mjtObj.mjOBJ_KEY, keyframe, "keyframe")
        mj.mj_resetDataKeyframe(model, data, key)
    else:
        raise TaskValidationError(f"unsupported reset mode: {mode!r}")
    mj.mj_forward(model, data)


def _observation(sample: Mapping[str, Any], path: Any) -> np.ndarray:
    if not isinstance(path, str):
        raise TaskValidationError("measurement observation_path is missing")
    fields = path.split(".")
    if len(fields) != 3 and not (len(fields) == 2 and fields[0] == "joint_positions"):
        raise TaskValidationError(f"unsupported observation_path: {path!r}")
    if fields[0] == "bodies" and fields[2] == "position":
        value = sample["bodies"].get(fields[1])
        if value is None:
            raise TaskValidationError(f"body observation unavailable: {fields[1]!r}")
        return np.asarray(value["position"], dtype=float)
    if fields[0] == "sites" and fields[2] == "position":
        value = sample["sites"].get(fields[1])
        if value is None:
            raise TaskValidationError(f"site observation unavailable: {fields[1]!r}")
        return np.asarray(value["position"], dtype=float)
    if fields[0] == "joint_positions":
        if fields[1] not in sample["joints"]:
            raise TaskValidationError(f"joint observation unavailable: {fields[1]!r}")
        return np.asarray(float(sample["joints"][fields[1]]))
    raise TaskValidationError(f"unsupported observation_path: {path!r}")


def _parameter(parameters: Mapping[str, Any], path: Any) -> Any:
    if not isinstance(path, str) or not path.startswith("parameters."):
        raise TaskValidationError(f"unsupported parameter path: {path!r}")
    name = path.removeprefix("parameters.")
    if name not in parameters:
        raise TaskValidationError(f"missing parameter: {name}")
    return parameters[name]


def _context_path(root: Any, path: Any, label: str) -> Any:
    if not isinstance(path, str) or not path:
        raise TaskValidationError(f"{label} must be a non-empty dotted path")
    current = root
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            raise TaskValidationError(f"{label} {path!r} is unavailable")
        current = current[component]
    return current


def _declared_waypoints(
    measurement: Mapping[str, Any],
    parameters: Mapping[str, Any],
    baseline: np.ndarray,
) -> list[np.ndarray]:
    path = measurement.get("waypoints_path")
    if path is not None:
        raw = _parameter(parameters, path)
        try:
            values = np.asarray(raw, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TaskValidationError("declared waypoints are not numeric") from exc
        if values.ndim != 2 or values.shape[0] < 1 or values.shape[1] != 3:
            raise TaskValidationError("declared waypoints must have shape N x 3")
        if not np.isfinite(values).all():
            raise TaskValidationError("declared waypoints contain non-finite values")
        return [value.copy() for value in values]
    if measurement.get("waypoint_pattern") == "square_xy":
        side = float(_parameter(parameters, measurement.get("side_path")))
        if not math.isfinite(side) or side <= 0:
            raise TaskValidationError("square side must be finite and positive")
        return [
            baseline.copy(),
            baseline + [side, 0.0, 0.0],
            baseline + [side, side, 0.0],
            baseline + [0.0, side, 0.0],
        ]
    raise TaskValidationError("waypoint measurement has no declared waypoint sequence")


def _project(vector: np.ndarray, projection: Any) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    if projection in (None, "xyz"):
        return value
    if projection == "xy":
        return value[..., :2]
    raise TaskValidationError(f"unsupported projection: {projection!r}")


def _window(trace: Sequence[Mapping[str, Any]], criterion: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    phase = criterion.get("phase")
    dwell = float(criterion.get("dwell_s", 0.0) or 0.0)
    if phase == "motion":
        values = [sample for sample in trace if sample["phase"] == "motion"]
    elif phase in {"full", "terminal"} and dwell > 0:
        values = [sample for sample in trace if sample["phase"] == "terminal"]
        if values:
            cutoff = float(values[-1]["time"]) - dwell - 1e-9
            values = [sample for sample in values if float(sample["time"]) >= cutoff]
    elif phase in {"full", "terminal"}:
        values = [sample for sample in trace if sample["phase"] == "terminal"]
    else:
        raise TaskValidationError(f"unsupported criterion phase: {phase!r}")
    if not values:
        raise TaskValidationError(f"no physical samples for phase {phase!r}")
    return values


def _aliases(side: Mapping[str, Any]) -> set[str]:
    geom = str(side["geom"]).lower()
    body = str(side["body"]).lower()
    values = {geom, body}
    if int(side["body_id"]) == 0 or "floor" in geom:
        values.update({"world", "body:0", "floor"})
    if any(token in value for token in ("jaw", "finger", "gripper") for value in (geom, body)):
        values.add("gripper")
    return values


def _matches(side: Mapping[str, Any], token: str) -> bool:
    wanted = token.lower()
    return wanted in _aliases(side)


def _contact_count(
    trace: Sequence[Mapping[str, Any]], measurement: Mapping[str, Any]
) -> int:
    groups = measurement.get("contact_groups")
    if not isinstance(groups, Mapping):
        raise TaskValidationError("contact measurement lacks contact_groups")
    subjects = groups.get("subject")
    if not isinstance(subjects, list) or not all(isinstance(value, str) for value in subjects):
        raise TaskValidationError("contact subject group must be a string array")
    others = groups.get("other")
    allowed = groups.get("allowed")
    relation = groups.get("relation")

    def count_sample(sample: Mapping[str, Any]) -> int:
        count = 0
        for contact in sample["contacts"]:
            a, b = contact["a"], contact["b"]
            oriented: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
            if any(_matches(a, token) for token in subjects):
                oriented.append((a, b))
            elif any(_matches(b, token) for token in subjects):
                oriented.append((b, a))
            for _, other in oriented:
                if isinstance(others, list):
                    if any(_matches(other, str(token)) for token in others):
                        count += 1
                elif relation == "outside_allowed" and isinstance(allowed, list):
                    if not any(_matches(other, str(token)) for token in allowed):
                        count += 1
                elif others is None and allowed is None and relation is None:
                    count += 1
                else:
                    raise TaskValidationError("unsupported contact group relation")
        return count

    values = [count_sample(sample) for sample in trace]
    return max(values, default=0)


def _base_samples(
    capture: _Capture, samples: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    if capture.base_body is None:
        raise TaskValidationError("base-body measurement requested but base body is unavailable")
    return [sample["bodies"][capture.base_body] for sample in samples]


def _measure(
    capture: _Capture,
    criterion: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[Any, str]:
    measurement = criterion.get("measurement")
    if not isinstance(measurement, Mapping):
        raise TaskValidationError("criterion measurement must be an object")
    if measurement.get("status") != "RESOLVED" and measurement.get("operator") != "candidate_outcome":
        raise TaskValidationError("criterion measurement is not RESOLVED")
    if measurement.get("metric") != criterion.get("metric"):
        raise TaskValidationError("criterion metric and measurement metric differ")
    operator = measurement.get("operator")
    if operator not in _SUPPORTED_MEASUREMENTS:
        raise TaskValidationError(f"unsupported measurement operator: {operator!r}")
    trace = capture.trace
    if not trace or not all(bool(sample["finite"]) for sample in trace):
        raise TaskValidationError("physical trace contains missing or non-finite state")
    samples = _window(trace, criterion)
    parameters = context["parameters"]
    projection = measurement.get("projection")

    if operator == "distance":
        observed = [_project(_observation(sample, measurement.get("observation_path")), projection) for sample in samples]
        reference_path = measurement.get("reference_path")
        reference = measurement.get("reference")
        if reference_path is not None:
            target = np.asarray(_parameter(parameters, reference_path), dtype=float)
        elif isinstance(reference, Mapping) and "observation_path" in reference:
            target = _observation(trace[0], reference["observation_path"])
        elif isinstance(reference, Mapping) and "value" in reference:
            target = np.asarray(reference["value"], dtype=float)
        else:
            raise TaskValidationError("distance measurement lacks a resolved reference")
        offset_path = measurement.get("reference_offset_path")
        if offset_path is not None:
            target = target + np.asarray(_parameter(parameters, offset_path), dtype=float)
        target = _project(target, projection)
        errors = [float(np.linalg.norm(value - target)) for value in observed]
        actual = max(errors) if float(criterion.get("dwell_s", 0.0) or 0.0) > 0 else errors[-1]
        return actual, f"trusted-state distance error over {len(errors)} sample(s)"

    if operator in {"history_waypoint_max_error", "history_waypoint_order"}:
        baseline = np.asarray(_observation(trace[0], measurement.get("observation_path")), dtype=float)
        declared = _declared_waypoints(measurement, parameters, baseline)
        invoked = context.get("waypoints")
        if isinstance(invoked, list) and invoked:
            declared = [np.asarray(value, dtype=float) for value in invoked]
        observed = [
            _project(_observation(sample, measurement.get("observation_path")), projection)
            for sample in trace
            if sample["phase"] in {"motion", "terminal"}
        ]
        if not observed:
            raise TaskValidationError("waypoint measurement has no physical motion trace")
        intended = [_project(np.asarray(value, dtype=float), projection) for value in declared]
        errors = [min(float(np.linalg.norm(value - target)) for value in observed) for target in intended]
        if operator == "history_waypoint_max_error":
            return max(errors), f"worst nearest-trace error across {len(intended)} invoked waypoints"
        next_index = 0
        for position in observed:
            nearest = min(
                range(len(intended)),
                key=lambda index: float(np.linalg.norm(position - intended[index])),
            )
            if nearest == next_index:
                next_index += 1
                if next_index == len(intended):
                    break
        return next_index == len(intended), f"visited {next_index}/{len(intended)} nearest waypoint regions in frozen order"

    if operator == "history_final_return_error":
        start = _project(_observation(trace[0], measurement.get("observation_path")), projection)
        errors = [
            float(
                np.linalg.norm(
                    _project(
                        _observation(sample, measurement.get("observation_path")),
                        projection,
                    )
                    - start
                )
            )
            for sample in samples
        ]
        actual = (
            max(errors)
            if float(criterion.get("dwell_s", 0.0) or 0.0) > 0
            else errors[-1]
        )
        return actual, f"trusted terminal-window return error across {len(errors)} sample(s)"

    if operator == "history_directional_delta":
        values = [float(_observation(sample, measurement.get("observation_path"))) for sample in trace]
        if measurement.get("direction") == "increase":
            actual = max(values) - values[0]
        elif measurement.get("direction") == "decrease":
            peak_index = int(np.argmax(values))
            actual = values[peak_index] - min(values[peak_index:])
        else:
            raise TaskValidationError("directional delta requires increase or decrease")
        return float(actual), f"trusted joint trace directional excursion across {len(values)} samples"

    if operator == "contact_count":
        actual = _contact_count(trace, measurement)
        return actual, "maximum trusted MuJoCo contact count matching frozen groups"

    if operator == "contact_boolean":
        if float(criterion.get("dwell_s", 0.0) or 0.0) > 0:
            actual = bool(samples) and all(
                _contact_count([sample], measurement) > 0 for sample in samples
            )
            return actual, "frozen contact relation holds throughout the terminal dwell"
        actual = _contact_count(trace, measurement) > 0
        return actual, "trusted MuJoCo trace contains the frozen contact relation"

    if operator == "offset_error":
        start = _observation(trace[0], measurement.get("observation_path"))
        target = start + np.asarray(
            _parameter(parameters, measurement.get("offset_path")), dtype=float
        )
        errors = [
            float(np.linalg.norm(_observation(sample, measurement.get("observation_path")) - target))
            for sample in trace
            if sample["phase"] == "motion"
        ]
        if not errors:
            raise TaskValidationError("offset measurement has no motion trace")
        return min(errors), "nearest trusted motion state to requested Cartesian offset"

    if operator == "trace_baseline_delta":
        values = [_observation(sample, measurement.get("observation_path")) for sample in trace]
        component = measurement.get("component")
        if not isinstance(component, int):
            raise TaskValidationError("trace_baseline_delta requires integer component")
        scalars: list[float] = []
        for value in values:
            vector = np.asarray(value, dtype=float).reshape(-1)
            if component < 0 or component >= vector.size:
                raise TaskValidationError("trace_baseline_delta component is out of range")
            scalars.append(float(vector[component]))
        baseline = scalars[0]
        deltas = [value - baseline for value in scalars]
        aggregation = measurement.get("aggregation", "max")
        if aggregation == "max":
            actual = max(deltas)
        elif aggregation == "min":
            actual = min(deltas)
        elif aggregation in {"last", "terminal"}:
            actual = deltas[-1]
        else:
            raise TaskValidationError(f"unsupported trace_baseline_delta aggregation: {aggregation!r}")
        return actual, f"trusted component-{component} delta from pre-invocation baseline"

    if operator == "invocation_count":
        actual = context.get("invocation_count")
        if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0:
            raise TaskValidationError("harness invocation count is unavailable")
        return actual, "method call count maintained by trusted harness"

    if operator == "candidate_outcome":
        returned = context.get("candidate_return")
        if not isinstance(returned, Mapping):
            raise TaskValidationError("candidate outcome requires a mapping return value")
        result_path = measurement.get("result_path")
        reason_path = measurement.get("reason_path")
        result = _context_path(returned, result_path, "candidate outcome result_path")
        expected = measurement.get("expected")
        matches = result == expected
        reason: Any = None
        if reason_path is not None:
            reason = _context_path(returned, reason_path, "candidate outcome reason_path")
            expected_reason = measurement.get("expected_reason")
            matches = matches and reason == expected_reason
        detail = (
            f"candidate mapping result={result!r} (expected {expected!r}), "
            f"reason={reason!r} (expected {measurement.get('expected_reason')!r})"
        )
        return bool(matches), detail

    if operator == "joint_vector_error":
        names = _parameter(parameters, measurement.get("joint_names_path"))
        reference = measurement.get("reference")
        if not isinstance(reference, Mapping):
            raise TaskValidationError("joint vector error requires a reference object")
        targets = _parameter(parameters, reference.get("value_path"))
        if not isinstance(names, list) or not all(isinstance(value, str) for value in names):
            raise TaskValidationError("joint vector error requires joint_names")
        try:
            target_values = np.asarray(targets, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TaskValidationError("joint vector target must be numeric") from exc
        if target_values.shape != (len(names),) or not np.isfinite(target_values).all():
            raise TaskValidationError("joint vector target length must match joint_names")
        errors = [
            max(abs(float(sample["joints"][name]) - float(target)) for name, target in zip(names, target_values, strict=True))
            for sample in samples
        ]
        return max(errors), f"maximum per-joint absolute error across {len(samples)} trusted sample(s)"

    if operator == "joint_symmetry_error":
        pairs = _parameter(parameters, measurement.get("joint_pairs_path"))
        if not isinstance(pairs, list) or not pairs:
            raise TaskValidationError("joint symmetry error requires joint_pairs")
        normalized: list[tuple[str, str]] = []
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(value, str) for value in pair):
                raise TaskValidationError("each joint symmetry pair must contain two joint names")
            normalized.append((pair[0], pair[1]))
        errors = [
            max(abs(float(sample["joints"][left]) - float(sample["joints"][right])) for left, right in normalized)
            for sample in samples
        ]
        return max(errors), f"maximum paired-joint difference across {len(samples)} trusted sample(s)"

    base = _base_samples(capture, samples)
    positions = [np.asarray(value["position"], dtype=float) for value in base]
    rotations = [np.asarray(value["rotation"], dtype=float) for value in base]
    initial_base = _base_samples(capture, [trace[0]])[0]
    initial_pos = np.asarray(initial_base["position"], dtype=float)
    initial_rot = np.asarray(initial_base["rotation"], dtype=float)

    if operator == "body_height":
        values = [float(value[2]) for value in positions]
        actual = min(values) if criterion.get("comparator") in {">", ">="} else max(values)
        return actual, f"conservative body-height aggregate across {len(values)} sample(s)"
    if operator == "upright_score":
        values = [float(value[2, 2]) for value in rotations]
        return min(values), f"minimum trusted torso upright score across {len(values)} sample(s)"
    if operator in {"planar_speed", "final_planar_speed"}:
        values = [float(np.linalg.norm(sample["planar_velocity"])) for sample in samples]
        return max(values), f"maximum trusted planar speed across {len(values)} sample(s)"
    if operator == "height_ratio":
        reference = float(_parameter(parameters, measurement.get("reference_path")))
        if not math.isfinite(reference) or reference == 0:
            raise TaskValidationError("height-ratio reference must be finite and nonzero")
        values = [float(value[2]) / reference for value in positions]
        actual = min(values) if criterion.get("comparator") in {">", ">="} else max(values)
        return actual, f"conservative height ratio across {len(values)} sample(s)"
    if operator == "horizontal_drift":
        values = [float(np.linalg.norm(value[:2] - initial_pos[:2])) for value in positions]
        return max(values), f"maximum horizontal drift across {len(values)} sample(s)"
    if operator in {
        "initial_yaw_forward_displacement",
        "initial_yaw_lateral_displacement_abs",
        "initial_yaw_lateral_drift",
        "initial_yaw_lateral_displacement",
        "initial_yaw_forward_drift",
    }:
        forward = np.asarray(initial_rot[:2, 0], dtype=float)
        norm = float(np.linalg.norm(forward))
        if not math.isfinite(norm) or norm < 1e-9:
            raise TaskValidationError("initial body yaw frame is degenerate")
        forward /= norm
        lateral = np.asarray([-forward[1], forward[0]], dtype=float)
        displacements = [value[:2] - initial_pos[:2] for value in positions]
        if operator == "initial_yaw_forward_displacement":
            return float(np.dot(displacements[-1], forward)), "final displacement in initial-yaw forward axis"
        if operator == "initial_yaw_lateral_displacement":
            return float(np.dot(displacements[-1], lateral)), "final displacement in initial-yaw lateral axis"
        axis = forward if operator == "initial_yaw_forward_drift" else lateral
        values = [abs(float(np.dot(value, axis))) for value in displacements]
        return max(values), f"maximum absolute drift in initial-yaw {'forward' if axis is forward else 'lateral'} axis"
    if operator == "body_height_error":
        reference_path = measurement.get("reference_path")
        if reference_path is None and isinstance(measurement.get("reference"), Mapping):
            reference_path = measurement["reference"].get("value_path")
        reference = float(_parameter(parameters, reference_path))
        values = [abs(float(value[2]) - reference) for value in positions]
        return max(values), f"maximum absolute body-height error across {len(values)} sample(s)"
    raise TaskValidationError(f"unsupported measurement operator: {operator!r}")


def _number(source: Mapping[str, Any], name: str, default: float | None = None) -> float:
    value = source.get(name, default)
    if value is None:
        raise TaskValidationError(f"missing invocation value: {name}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise TaskValidationError(f"invocation value {name} must be finite and non-negative")
    return result


def _max_dwell(test: Mapping[str, Any]) -> float:
    values = [float(value.get("dwell_s", 0.0) or 0.0) for value in test["criteria"]]
    invocation = test["invocation"]
    parameters = test["parameters"]
    values.extend([
        float(invocation.get("hold_s", 0.0) or 0.0),
        float(parameters.get("dwell_s", 0.0) or 0.0),
        float(parameters.get("final_stop_dwell_s", 0.0) or 0.0),
    ])
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise TaskValidationError("dwell values must be finite and non-negative")
    return max(values, default=0.0)


def _step_seconds(robot: Any, seconds: float) -> None:
    if seconds <= 0:
        return
    timestep = float(robot.model.opt.timestep)
    if not math.isfinite(timestep) or timestep <= 0:
        raise TaskValidationError("MuJoCo timestep must be finite and positive")
    robot.step(max(1, int(math.ceil(seconds / timestep))))


def _invoke(robot: Any, test: Mapping[str, Any], capture: _Capture) -> dict[str, Any]:
    effect = test.get("effect")
    method_name = test.get("method")
    invocation = test.get("invocation")
    parameters = test.get("parameters")
    if not isinstance(invocation, Mapping) or not isinstance(parameters, Mapping):
        raise TaskValidationError("invocation and parameters must be objects")
    operator = invocation.get("operator")
    if str(operator) not in _SUPPORTED_INVOCATIONS:
        raise TaskValidationError(f"unsupported invocation operator: {operator!r}")
    if (
        not _supports_invocation_effect(operator, effect)
        or method_name != effect
        or invocation.get("effect") != effect
    ):
        raise TaskValidationError("method/effect/invocation binding is inconsistent")
    method = getattr(robot, str(method_name), None)
    if not callable(method):
        raise TaskValidationError(f"driver lacks sealed public method {method_name!r}")
    context: dict[str, Any] = {
        "parameters": dict(parameters),
        "invocation_count": 0,
        "candidate_return": None,
    }

    def call(**kwargs: Any) -> Any:
        context["invocation_count"] += 1
        returned = method(**kwargs)
        context["candidate_return"] = returned
        return returned

    def vector3(value: Any, label: str) -> np.ndarray:
        try:
            result = np.asarray(value, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TaskValidationError(f"{label} must be a numeric 3-vector") from exc
        if result.shape != (3,) or not np.isfinite(result).all():
            raise TaskValidationError(f"{label} must be a finite 3-vector")
        return result

    def distance_target(metric: str) -> np.ndarray:
        measurement: Mapping[str, Any] | None = None
        for criterion in test.get("criteria", []):
            candidate = criterion.get("measurement") if isinstance(criterion, Mapping) else None
            if isinstance(candidate, Mapping) and candidate.get("metric") == metric:
                measurement = candidate
                break
        if measurement is None or measurement.get("operator") != "distance":
            raise TaskValidationError(f"resolved distance measurement unavailable for {metric!r}")
        reference_path = measurement.get("reference_path")
        reference = measurement.get("reference")
        if reference_path is not None:
            target = vector3(_parameter(parameters, reference_path), f"{metric} reference")
        elif isinstance(reference, Mapping) and "observation_path" in reference:
            target = vector3(
                _observation(capture.trace[0], reference["observation_path"]),
                f"{metric} reference",
            )
        elif isinstance(reference, Mapping) and "value" in reference:
            target = vector3(reference["value"], f"{metric} reference")
        else:
            raise TaskValidationError(f"distance measurement for {metric!r} lacks a reference")
        offset_path = measurement.get("reference_offset_path")
        if offset_path is not None:
            target = target + vector3(
                _parameter(parameters, offset_path), f"{metric} reference offset"
            )
        return target

    capture.phase = "motion"

    if operator == "reach_above_object":
        target_name = parameters.get("target_object_id")
        if not isinstance(target_name, str):
            raise TaskValidationError("reach requires target_object_id")
        initial = capture.trace[0]["bodies"].get(target_name)
        if initial is None:
            raise TaskValidationError(f"reach target body unavailable: {target_name!r}")
        offset = vector3(parameters.get("target_offset_m"), "target_offset_m")
        target = np.asarray(initial["position"], dtype=float) + offset
        call(target_position=target.tolist(), duration=_number(invocation, "duration_s", 1.0))
        context["target_position"] = target.tolist()
    elif operator == "reach_target":
        arm = invocation.get("arm")
        if arm is None:
            target = vector3(parameters.get("target_position_m"), "target_position_m")
            call(target_position=target.tolist(), duration=_number(invocation, "duration_s", 1.0))
        else:
            if arm not in {"left", "right"}:
                raise TaskValidationError("reach arm must be 'left' or 'right'")
            target = distance_target(f"{arm}_target_error_m")
            call(
                target_position=target.tolist(),
                arm=arm,
                duration=_number(invocation, "duration_s", 1.0),
            )
        context["target_position"] = target.tolist()
    elif operator == "move_bimanual_targets":
        targets = {
            arm: distance_target(f"{arm}_bimanual_target_error_m")
            for arm in ("left", "right")
        }
        call(
            left_target_position=targets["left"].tolist(),
            right_target_position=targets["right"].tolist(),
            duration=_number(invocation, "duration_s", 1.0),
        )
        context.update({
            "left_target_position": targets["left"].tolist(),
            "right_target_position": targets["right"].tolist(),
        })
    elif operator in {"trace_square_and_return", "trace_cartesian_path"}:
        path = test["criteria"][0]["measurement"].get("observation_path")
        start = np.asarray(_observation(capture.trace[0], path), dtype=float)
        side = float(parameters.get("square_side_m"))
        if not math.isfinite(side) or side <= 0:
            raise TaskValidationError("square_side_m must be finite and positive")
        waypoints = [
            start + [side, 0.0, 0.0],
            start + [side, side, 0.0],
            start + [0.0, side, 0.0],
            start.copy(),
        ]
        call(
            waypoints=[value.tolist() for value in waypoints],
            duration_per_segment=_number(invocation, "duration_per_segment_s", 0.5),
        )
        context["waypoints"] = [value.tolist() for value in waypoints]
    elif operator == "gripper_open_close":
        call(duration=_number(invocation, "duration_s", 1.0))
    elif operator == "controlled_contact":
        target_name = parameters.get("target_object_id")
        if not isinstance(target_name, str):
            raise TaskValidationError("contact task requires target_object_id")
        initial = capture.trace[0]["bodies"].get(target_name)
        if initial is None:
            raise TaskValidationError(f"contact target body unavailable: {target_name!r}")
        offset = vector3(invocation.get("contact_offset_m"), "contact_offset_m")
        target = np.asarray(initial["position"], dtype=float) + offset
        call(contact_position=target.tolist(), duration=_number(invocation, "duration_s", 1.0))
        context["contact_position"] = target.tolist()
    elif operator in {"offset_and_return", "move_cartesian_offset_and_return"}:
        offset = vector3(parameters.get("offset_m"), "offset_m")
        call(offset=offset.tolist(), duration=_number(invocation, "duration_s", 1.0))
        context["offset"] = offset.tolist()
    elif operator in {"visit_waypoints", "visit_cartesian_waypoints"}:
        raw_waypoints = parameters.get("waypoints_m")
        try:
            waypoint_values = np.asarray(raw_waypoints, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TaskValidationError("waypoints_m must be a numeric N x 3 array") from exc
        if (
            waypoint_values.ndim != 2
            or waypoint_values.shape[0] < 1
            or waypoint_values.shape[1] != 3
            or not np.isfinite(waypoint_values).all()
        ):
            raise TaskValidationError("waypoints_m must be a finite N x 3 array")
        waypoints = [value.tolist() for value in waypoint_values]
        call(
            waypoints=waypoints,
            duration_per_segment=_number(invocation, "duration_per_segment_s", 0.5),
        )
        context["waypoints"] = waypoints
    elif operator == "reject_unreachable_and_return_home":
        target = vector3(parameters.get("target_position_m"), "target_position_m")
        call(target_position=target.tolist(), duration=_number(invocation, "duration_s", 1.0))
        context["target_position"] = target.tolist()
    elif operator == "push_object_to_goal":
        target_name = parameters.get("target_object_id")
        if not isinstance(target_name, str):
            raise TaskValidationError("push task requires target_object_id")
        initial = capture.trace[0]["bodies"].get(target_name)
        if initial is None:
            raise TaskValidationError(f"push target body unavailable: {target_name!r}")
        object_position = np.asarray(initial["position"], dtype=float)
        goal_xy = np.asarray(parameters.get("goal_center_m"), dtype=float)
        if goal_xy.shape != (2,):
            raise TaskValidationError("push goal_center_m must be a planar vector")
        goal = np.asarray([goal_xy[0], goal_xy[1], object_position[2]], dtype=float)
        call(
            object_position=object_position.tolist(),
            goal_position=goal.tolist(),
            duration=_number(invocation, "duration_s", 1.0),
        )
        context.update({"object_position": object_position.tolist(), "goal_position": goal.tolist()})
    elif operator == "grasp_and_lift":
        target_name = parameters.get("target_object_id")
        if not isinstance(target_name, str):
            raise TaskValidationError("grasp task requires target_object_id")
        initial = capture.trace[0]["bodies"].get(target_name)
        if initial is None:
            raise TaskValidationError(f"grasp target body unavailable: {target_name!r}")
        object_position = np.asarray(initial["position"], dtype=float)
        lift = float(parameters.get("lift_m"))
        if not math.isfinite(lift) or lift <= 0:
            raise TaskValidationError("lift_m must be finite and positive")
        call(
            object_position=object_position.tolist(),
            lift=lift,
            duration=_number(invocation, "duration_s", 1.0),
        )
        context.update({"object_position": object_position.tolist(), "lift": lift})
    elif operator == "move_to_waypoint_and_return":
        waypoint = vector3(parameters.get("waypoint_m"), "waypoint_m")
        call(waypoint=waypoint.tolist(), duration=_number(invocation, "duration_s", 1.0))
        context["waypoint"] = waypoint.tolist()
    elif operator == "set_joint_posture_and_hold":
        names = parameters.get("joint_names")
        targets = parameters.get("target_joint_positions_rad")
        call(
            joint_names=names,
            target_joint_positions=targets,
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "flex_index_finger_and_hold":
        call(
            target_joint_positions=parameters.get("target_joint_positions_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "set_symmetric_finger_posture":
        call(
            joint_pairs=parameters.get("joint_pairs"),
            target_joint_positions=parameters.get("target_joint_positions_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "set_thumb_opposition_and_hold":
        call(
            target_joint_positions=parameters.get("target_joint_positions_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "cycle_to_pregrasp_posture":
        call(
            joint_names=parameters.get("joint_names"),
            target_joint_positions=parameters.get("target_joint_positions_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "cycle_bimanual_grippers":
        call(duration=_number(invocation, "duration_s", 1.0))
    elif operator == "move_bimanual_offset_and_return":
        left_offset = vector3(parameters.get("left_offset_m"), "left_offset_m")
        right_offset = vector3(parameters.get("right_offset_m"), "right_offset_m")
        call(
            left_offset=left_offset.tolist(),
            right_offset=right_offset.tolist(),
            duration=_number(invocation, "duration_s", 1.0),
        )
        context.update({
            "left_offset": left_offset.tolist(),
            "right_offset": right_offset.tolist(),
        })
    elif operator == "drive_base_forward":
        call(
            distance=_number(invocation, "distance_m"),
            duration=_number(invocation, "duration_s", 2.0),
        )
    elif operator == "set_lift_height":
        call(
            delta_height=_number(invocation, "delta_height_m"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "extend_arm":
        call(
            extension=_number(invocation, "extension_m"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "rotate_wrist":
        call(
            delta_yaw=_number(invocation, "delta_yaw_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "cycle_gripper":
        call(duration=_number(invocation, "duration_s", 1.0))
    elif operator == "stand_and_hold":
        default_duration = 2.0 if effect == "stand_up" else 1.0
        call(duration=_number(invocation, "duration_s", default_duration))
    elif operator == "set_body_height_and_hold":
        call(
            target_body_height=_number(invocation, "target_body_height_m"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "walk_bounded_direction_and_stop":
        direction = invocation.get("direction")
        if direction not in {
            "forward_initial_body_yaw",
            "lateral_positive_initial_body_yaw",
        }:
            raise TaskValidationError("unsupported bounded-walk direction")
        call(
            direction=direction,
            distance=_number(invocation, "distance_m"),
            duration=_number(invocation, "duration_s", 2.0),
        )
    elif operator in {
        "set_arm_joint_posture_and_hold",
        "set_upper_body_posture_and_hold",
        "set_leg_posture_and_hold",
    }:
        call(
            joint_names=parameters.get("joint_names"),
            target_joint_positions=parameters.get("target_joint_positions_rad"),
            duration=_number(invocation, "duration_s", 1.0),
        )
    elif operator == "sit_and_hold":
        call(duration=_number(invocation, "duration_s", 1.5))
    elif operator == "hold_current_upright":
        call(duration=_number(invocation, "duration_s", 2.0))
    elif operator == "walk_forward_and_stop":
        call(
            duration=_number(invocation, "duration_s", 3.0),
            speed=_number(invocation, "speed_m_s", 0.3),
        )
    elif operator == "set_height_and_hold":
        target_height = parameters.get("target_body_height_m")
        if target_height is None:
            raise TaskValidationError("height task requires target_body_height_m")
        call(
            target_height=float(target_height),
            duration=_number(invocation, "duration_s", 2.0),
        )
    else:  # guarded above; keeps static checkers honest
        raise TaskValidationError(f"unsupported invocation operator: {operator!r}")

    capture.snapshot()
    capture.phase = "terminal"
    capture.snapshot()
    _step_seconds(robot, _max_dwell(test))
    if not capture.trace or capture.trace[-1]["phase"] != "terminal":
        capture.snapshot()
    return context


def _guard_results(capture: _Capture, test: Mapping[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for guard in test.get("guards", []):
        guard_id = guard.get("guard_id") if isinstance(guard, Mapping) else None
        ok = False
        detail = ""
        if guard_id not in _SUPPORTED_GUARDS:
            detail = f"unsupported guard {guard_id!r}"
        elif guard_id in {"trusted-external-verdict", "trusted-mujoco-state"}:
            ok = True
            detail = "verdict computed only from harness-read MuJoCo model/data"
        elif guard_id == "finite-required-state":
            ok = bool(capture.trace) and all(bool(sample["finite"]) for sample in capture.trace)
            detail = "all required harness observations are finite" if ok else "non-finite physical state observed"
        elif guard_id == "scene-entrypoint-bound":
            ok = True
            detail = "driver was freshly built against the declared resolved scene"
        elif guard_id == "no-body-or-head-floor-contact":
            offending: list[str] = []
            patterns = ("base", "body", "trunk", "chest", "head", "neck")
            for sample in capture.trace:
                for contact in sample["contacts"]:
                    a, b = contact["a"], contact["b"]
                    for candidate, other in ((a, b), (b, a)):
                        names = _aliases(candidate)
                        body_like = any(pattern in value for pattern in patterns for value in names)
                        floor_like = any(value in {"world", "body:0", "floor"} or "floor" in value for value in _aliases(other))
                        if body_like and floor_like:
                            offending.append(f"{candidate['body']}/{candidate['geom']} -> {other['geom']}")
            ok = not offending
            detail = "no body/head-floor contact" if ok else f"forbidden contacts: {sorted(set(offending))}"
        results.append({"guard_id": guard_id, "ok": bool(ok), "detail": detail})
    return results


def _suite_diagnostics(
    suite: Mapping[str, Any],
    expected_requirement_ids: Sequence[str],
    expected_capability_ids: set[str] | None,
) -> list[str]:
    issues: list[str] = []
    if suite.get("schema_version") != "2.0.0":
        issues.append("suite schema_version must be 2.0.0")
    if suite.get("precision_policy") != policy_record():
        issues.append(
            "suite precision_policy is missing or does not match the frozen experiment policy"
        )
    raw_tests = suite.get("requirement_tests")
    if not isinstance(raw_tests, list):
        return issues + ["suite requirement_tests must be an array"]
    if len(set(expected_requirement_ids)) != len(expected_requirement_ids):
        issues.append("expected_requirement_ids contains duplicates")
    observed: list[str] = []
    for index, test in enumerate(raw_tests):
        if not isinstance(test, Mapping):
            issues.append(f"requirement_tests[{index}] must be an object")
            continue
        missing = _REQUIRED_TEST_FIELDS - set(test)
        if missing:
            issues.append(f"requirement_tests[{index}] missing fields: {sorted(missing)}")
        requirement_id = test.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            issues.append(f"requirement_tests[{index}] has invalid requirement_id")
        else:
            observed.append(requirement_id)
        capability_id = test.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id:
            issues.append(f"requirement_tests[{index}] has invalid capability_id")
        elif expected_capability_ids is not None and capability_id not in expected_capability_ids:
            issues.append(f"requirement_tests[{index}] references unknown capability {capability_id!r}")
        if not isinstance(test.get("criteria"), list) or not test.get("criteria"):
            issues.append(f"requirement_tests[{index}] has no criteria")
        else:
            for criterion_index, criterion in enumerate(test["criteria"]):
                if not isinstance(criterion, Mapping):
                    issues.append(
                        f"requirement_tests[{index}].criteria[{criterion_index}] must be an object"
                    )
                    continue
                try:
                    validate_compiled_position_standard(criterion)
                except ValueError as exc:
                    issues.append(
                        "requirement_tests[{index}].criteria[{criterion_index}] violates "
                        "precision_policy: {error}".format(
                            index=index,
                            criterion_index=criterion_index,
                            error=exc,
                        )
                    )
        if not isinstance(test.get("guards"), list) or not test.get("guards"):
            issues.append(f"requirement_tests[{index}] has no guards")
    if len(set(observed)) != len(observed):
        issues.append("suite contains duplicate requirement_id values")
    expected = set(expected_requirement_ids)
    observed_set = set(observed)
    if expected - observed_set:
        issues.append(f"suite is missing expected requirements: {sorted(expected - observed_set)}")
    if observed_set - expected:
        issues.append(f"suite contains unknown requirements: {sorted(observed_set - expected)}")
    if len(raw_tests) != len(expected_requirement_ids):
        issues.append("suite requirement count differs from expected exact coverage")
    return issues


def _unevaluated_criteria(
    test: Mapping[str, Any], execution_error: str
) -> list[dict[str, Any]]:
    """Preserve every frozen criterion when execution cannot reach measurement."""

    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(test.get("criteria", [])):
        criterion = raw if isinstance(raw, Mapping) else {}
        detail = f"not evaluated because requirement execution failed: {execution_error}"
        rows.append({
            "criterion_id": criterion.get("criterion_id", f"invalid-criterion-{index}"),
            "metric": criterion.get("metric"),
            "phase": criterion.get("phase"),
            "dwell_s": criterion.get("dwell_s"),
            "actual": None,
            "comparator": criterion.get("comparator"),
            "threshold": criterion.get("threshold"),
            "ok": False,
            "error": execution_error,
            "detail": detail,
        })
    return rows


def _unevaluated_guards(
    test: Mapping[str, Any], execution_error: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in test.get("guards", []):
        guard = raw if isinstance(raw, Mapping) else {}
        rows.append({
            "guard_id": guard.get("guard_id"),
            "ok": False,
            "detail": f"not evaluated because requirement execution failed: {execution_error}",
        })
    return rows


def validate_task_driver(
    workspace: Path,
    suite: Mapping[str, Any],
    *,
    asset_root: Path,
    expected_requirement_ids: Iterable[str],
    expected_capability_ids: Iterable[str] | None = None,
    record_video: bool = True,
) -> dict[str, Any]:
    """Run one fresh direct-MuJoCo trial per covered Stage1 requirement.

    ``expected_requirement_ids`` must come from the sealed Stage1 decision
    scope, not from the Blue suite itself.  That independent input is what
    makes missing and unknown Blue requirements detectable.
    """

    workspace = Path(workspace).resolve()
    asset_root = Path(asset_root).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    report_path = workspace / "validate_report.json"
    expected = tuple(expected_requirement_ids)
    expected_caps = set(expected_capability_ids) if expected_capability_ids is not None else None
    diagnostics = _suite_diagnostics(suite, expected, expected_caps)
    started = time.time()
    frames: list[Any] = []
    requirements: list[dict[str, Any]] = []
    module: Any = None
    load_error: str | None = None
    old_cwd = Path.cwd()
    added_paths: list[str] = []
    try:
        for source in (workspace, LEGACY_RUNTIME, LEGACY_CORE):
            value = str(source)
            if value not in sys.path:
                sys.path.insert(0, value)
                added_paths.append(value)
        os.chdir(workspace)
        if not diagnostics:
            try:
                module = _load_driver(workspace / "driver.py")
            except Exception as exc:
                load_error = f"{type(exc).__name__}: {exc}"

        if not diagnostics:
            for blue_test in suite["requirement_tests"]:
                robot = None
                capture = None
                criteria: list[dict[str, Any]] = []
                guards: list[dict[str, Any]] = []
                error: str | None = None
                if module is None:
                    error = f"driver load failed: {load_error or 'unknown driver load error'}"
                else:
                    try:
                        scene_path = _resolve_scene(asset_root, blue_test["scene_entrypoint"])
                        robot = module.build(mjcf_path=str(scene_path))
                        _reset_robot(robot, blue_test["reset"])
                        if not callable(getattr(robot, "step", None)):
                            raise TaskValidationError("driver.build() result must expose step(n)")
                        capture = _Capture(
                            robot,
                            blue_test,
                            frames,
                            frame_cap=450 if record_video else 0,
                        )
                        capture.install()
                        context = _invoke(robot, blue_test, capture)
                        for criterion in blue_test["criteria"]:
                            try:
                                actual, detail = _measure(capture, criterion, context)
                                actual = _json_scalar(actual)
                                ok = _compare(actual, str(criterion.get("comparator")), criterion.get("threshold"))
                                criteria.append({
                                    "criterion_id": criterion.get("criterion_id"),
                                    "metric": criterion.get("metric"),
                                    "phase": criterion.get("phase"),
                                    "dwell_s": criterion.get("dwell_s"),
                                    "actual": actual,
                                    "comparator": criterion.get("comparator"),
                                    "threshold": criterion.get("threshold"),
                                    "ok": bool(ok),
                                    "error": None,
                                    "detail": detail,
                                })
                            except Exception as exc:
                                criterion_error = f"{type(exc).__name__}: {exc}"
                                criteria.append({
                                    "criterion_id": criterion.get("criterion_id"),
                                    "metric": criterion.get("metric"),
                                    "phase": criterion.get("phase"),
                                    "dwell_s": criterion.get("dwell_s"),
                                    "actual": None,
                                    "comparator": criterion.get("comparator"),
                                    "threshold": criterion.get("threshold"),
                                    "ok": False,
                                    "error": criterion_error,
                                    "detail": criterion_error,
                                })
                        guards = _guard_results(capture, blue_test)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                if capture is not None:
                    capture.restore()
                if robot is not None and callable(getattr(robot, "close", None)):
                    try:
                        robot.close()
                    except Exception:
                        pass
                if error is not None:
                    criteria = _unevaluated_criteria(blue_test, error)
                    guards = _unevaluated_guards(blue_test, error)
                ok = bool(
                    error is None
                    and criteria
                    and all(item["ok"] for item in criteria)
                    and guards
                    and all(item["ok"] for item in guards)
                )
                requirement_structural_ok = bool(
                    error is None
                    and criteria
                    and all(item.get("error") is None for item in criteria)
                    and guards
                    and all(item.get("guard_id") in _SUPPORTED_GUARDS for item in guards)
                )
                requirements.append({
                    "requirement_id": blue_test.get("requirement_id"),
                    "task_id": blue_test.get("task_id"),
                    "capability_id": blue_test.get("capability_id"),
                    "effect": blue_test.get("effect"),
                    "method": blue_test.get("method"),
                    "case_id": blue_test.get("case_id"),
                    "ok": ok,
                    "structural_ok": requirement_structural_ok,
                    "error": error,
                    "criteria": criteria,
                    "guards": guards,
                    "trace_samples": len(capture.trace) if capture is not None else 0,
                })
    finally:
        os.chdir(old_cwd)
        for value in added_paths:
            if value in sys.path:
                sys.path.remove(value)

    recording: str | None = None
    video_error: str | None = None
    if record_video and frames:
        try:
            import imageio.v2 as imageio

            recordings = workspace / "recordings"
            recordings.mkdir(exist_ok=True)
            path = recordings / f"validate_tasks_{int(time.time())}.mp4"
            imageio.mimsave(str(path), frames, fps=30, codec="libx264")
            recording = str(path.relative_to(workspace))
            for requirement in requirements:
                requirement["recording"] = recording
        except Exception as exc:
            video_error = f"{type(exc).__name__}: {exc}"
    elif record_video:
        video_error = "MuJoCo renderer produced no frames in this process"

    observed_ids = [str(value.get("requirement_id")) for value in requirements]
    exact_coverage = bool(
        not diagnostics
        and len(observed_ids) == len(expected)
        and len(set(observed_ids)) == len(observed_ids)
        and set(observed_ids) == set(expected)
    )
    structural_ok = bool(
        not diagnostics
        and load_error is None
        and len(requirements) == len(expected)
        and all(value["structural_ok"] for value in requirements)
    )
    all_ok = bool(
        exact_coverage
        and structural_ok
        and len(requirements) == len(expected)
        and all(value["ok"] for value in requirements)
    )
    report = {
        "artifact_type": "demo2_direct_task_validation_report",
        "schema_version": "1.0.0",
        "precision_policy": policy_record(),
        "all_ok": all_ok,
        "structural_ok": structural_ok,
        "exact_requirement_coverage": exact_coverage,
        "expected_requirement_ids": list(expected),
        "observed_requirement_ids": observed_ids,
        "passed_requirement_ids": [value["requirement_id"] for value in requirements if value["ok"]],
        "failed_requirement_ids": [value["requirement_id"] for value in requirements if not value["ok"]],
        "n_passed": sum(1 for value in requirements if value["ok"]),
        "n_total": len(requirements),
        "suite_diagnostics": diagnostics,
        "driver_load_error": load_error,
        "requirements": requirements,
        "tests": requirements,
        "duration_sec": time.time() - started,
        "recording": recording,
        "video_error": video_error,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def task_repair_feedback(report: Mapping[str, Any]) -> str:
    """Return criterion-level physical evidence suitable for a generation repair."""

    failures: list[str] = []
    for diagnostic in report.get("suite_diagnostics", []):
        failures.append(f"- validation-suite: {diagnostic}")
    if report.get("driver_load_error"):
        failures.append(f"- driver-load: {report['driver_load_error']}")
    for requirement in report.get("requirements", []):
        prefix = (
            f"requirement={requirement.get('requirement_id')}; "
            f"task={requirement.get('task_id')}; "
            f"capability={requirement.get('capability_id')}; "
            f"method={requirement.get('method')}"
        )
        if requirement.get("error"):
            failures.append(f"- {prefix}; execution={requirement['error']}")
        for criterion in requirement.get("criteria", []):
            if criterion.get("ok"):
                continue
            failures.append(
                "- {prefix}; criterion={criterion}; actual={actual}; comparator={comparator}; "
                "threshold={threshold}; phase={phase}; dwell_s={dwell}; detail={detail}".format(
                    prefix=prefix,
                    criterion=criterion.get("criterion_id", criterion.get("metric")),
                    actual=criterion.get("actual"),
                    comparator=criterion.get("comparator"),
                    threshold=criterion.get("threshold"),
                    phase=criterion.get("phase"),
                    dwell=criterion.get("dwell_s"),
                    detail=criterion.get("detail"),
                )
            )
        for guard in requirement.get("guards", []):
            if not guard.get("ok"):
                failures.append(
                    f"- {prefix}; guard={guard.get('guard_id')}; detail={guard.get('detail')}"
                )
    if report.get("recording"):
        failures.append(f"- validation video: {report['recording']}")
    return "\n".join(failures) or "No failing task criteria or guards were reported."


# Compatibility for callers that select the feedback function by validator module.
repair_feedback = task_repair_feedback


__all__ = [
    "TaskValidationError",
    "repair_feedback",
    "task_repair_feedback",
    "validate_task_driver",
]
