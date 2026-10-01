# SPDX-License-Identifier: Apache-2.0
"""Small, implementation-blind measurements for prepared AA1 cases.

The design supplies the criterion numbers.  This module only evaluates the
explicit operator and binding written in a case, using samples captured from
the same MuJoCo model/data instance that executed the capability call.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

import mujoco
import numpy as np


_COMPARATORS = {"<", "<=", ">", ">=", "==", "between"}
_TEMPORAL_KINDS = {"terminal", "window"}
_AGGREGATION_KINDS = {"last", "max", "min", "mean", "integral"}
_OPERATORS = {
    "site_position_error",
    "body_position_error",
    "joint_position_error",
    "joint_drift",
    "joint_relation_error",
    "ordered_site_targets_error",
    "ordered_body_targets_error",
    "contact_normal_force",
}
_SITE_OPERATORS = {"site_position_error", "ordered_site_targets_error"}
_BODY_OPERATORS = {"body_position_error", "ordered_body_targets_error"}
_JOINT_OPERATORS = {
    "joint_position_error",
    "joint_drift",
    "joint_relation_error",
}


class MeasurementError(ValueError):
    """Raised when a case measurement cannot be evaluated honestly."""


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    )


def _number(value: Any, *, where: str) -> float:
    if not _finite_number(value):
        raise MeasurementError(f"{where} must be a finite number")
    return float(value)


def _vector(value: Any, *, length: int, where: str) -> list[float]:
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != length:
        raise MeasurementError(f"{where} must be a numeric array of length {length}")
    result = [_number(item, where=f"{where}[{index}]") for index, item in enumerate(value)]
    return result


def _mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MeasurementError(f"{where} must be an object")
    return value


def _request_field(request: Any, field: Any, *, where: str) -> Any:
    """Resolve a simple dotted request path without guessing aliases."""

    if not isinstance(field, str) or not field.strip():
        raise MeasurementError(f"{where} must be a non-empty request field")
    value: Any = request
    for component in field.split("."):
        if not component or not isinstance(value, Mapping) or component not in value:
            raise MeasurementError(f"{where} {field!r} is missing from request")
        value = value[component]
    return value


def _model_name(model: Any, kind: str, name: str, *, where: str) -> int:
    if not isinstance(name, str) or not name.strip():
        raise MeasurementError(f"{where} must be a non-empty name")
    try:
        return int(getattr(model, kind)(name).id)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise MeasurementError(f"{where} {name!r} is not present in the model") from exc


def _model_geom_name(model: Any, name: str, *, where: str) -> str:
    """Resolve a generated scene geom name while keeping authored names clear."""

    try:
        _model_name(model, "geom", name, where=where)
        return name
    except MeasurementError as original:
        alias = name if name.endswith("_geom") else f"{name}_geom"
        if alias != name:
            try:
                _model_name(model, "geom", alias, where=where)
                return alias
            except MeasurementError:
                pass
        raise original


def _joint_info(model: Any, name: str, *, where: str) -> tuple[int, str]:
    joint_id = _model_name(model, "joint", name, where=where)
    joint_type = int(model.jnt_type[joint_id])
    if joint_type == int(mujoco.mjtJoint.mjJNT_HINGE):
        return joint_id, "rad"
    if joint_type == int(mujoco.mjtJoint.mjJNT_SLIDE):
        return joint_id, "m"
    raise MeasurementError(
        f"{where} {name!r} must be a scalar hinge or slide joint; "
        "free and ball joints are not scalar measurements"
    )


def _criterion_parts(criterion: Any, *, where: str) -> tuple[str, str, str, Any, Mapping[str, Any], Mapping[str, Any]]:
    criterion_map = _mapping(criterion, where=where)
    for field in ("metric", "unit", "comparator", "threshold", "temporal", "aggregation"):
        if field not in criterion_map:
            raise MeasurementError(f"{where}.{field} is required")
    metric = criterion_map["metric"]
    unit = criterion_map["unit"]
    comparator = criterion_map["comparator"]
    if not isinstance(metric, str) or not metric.strip():
        raise MeasurementError(f"{where}.metric must be non-empty text")
    if not isinstance(unit, str) or not unit.strip():
        raise MeasurementError(f"{where}.unit must be non-empty text")
    if comparator not in _COMPARATORS:
        raise MeasurementError(f"{where}.comparator {comparator!r} is unsupported")
    threshold = criterion_map["threshold"]
    if comparator == "between":
        if (
            not isinstance(threshold, (list, tuple))
            or len(threshold) != 2
            or not all(_finite_number(item) for item in threshold)
            or float(threshold[0]) > float(threshold[1])
        ):
            raise MeasurementError(
                f"{where}.threshold must be an ordered finite two-number range for between"
            )
    elif not _finite_number(threshold):
        raise MeasurementError(f"{where}.threshold must be a finite number")
    temporal = _mapping(criterion_map["temporal"], where=f"{where}.temporal")
    aggregation = _mapping(criterion_map["aggregation"], where=f"{where}.aggregation")
    temporal_kind = temporal.get("kind")
    if temporal_kind not in _TEMPORAL_KINDS:
        raise MeasurementError(
            f"{where}.temporal.kind must be terminal or window"
        )
    if temporal_kind == "terminal":
        if set(temporal) != {"kind"}:
            raise MeasurementError(f"{where}.temporal.terminal has no extra fields")
    else:
        if set(temporal) != {"kind", "start_s", "end_s"}:
            raise MeasurementError(
                f"{where}.temporal.window requires only start_s and end_s"
            )
        start = _number(temporal.get("start_s"), where=f"{where}.temporal.start_s")
        end = _number(temporal.get("end_s"), where=f"{where}.temporal.end_s")
        if start < 0.0 or end <= start:
            raise MeasurementError(
                f"{where}.temporal.window must satisfy 0 <= start_s < end_s"
            )
    aggregation_kind = aggregation.get("kind")
    if aggregation_kind not in _AGGREGATION_KINDS or set(aggregation) != {"kind"}:
        raise MeasurementError(
            f"{where}.aggregation.kind must be one of "
            "last, max, min, mean, integral and has no extra fields"
        )
    return metric, unit, str(comparator), threshold, temporal, aggregation


def _expected_unit(operator: str, *, joint_unit: str | None, aggregation: str) -> str:
    if operator in _SITE_OPERATORS | _BODY_OPERATORS:
        return "m"
    if operator in _JOINT_OPERATORS:
        if joint_unit is None:
            raise MeasurementError("a scalar joint is required to derive the unit")
        return joint_unit
    if operator == "contact_normal_force":
        return "N*s" if aggregation == "integral" else "N"
    raise MeasurementError(f"unsupported measurement operator {operator!r}")


def validate_measurement(
    model: Any,
    measurement: Any,
    criterion: Any,
    request: Any,
) -> None:
    """Validate one explicit case binding against a real MuJoCo model.

    No operator is selected from the metric text.  The binding's explicit
    ``operator`` field is required and is the sole source of measurement
    semantics.
    """

    measurement_map = _mapping(measurement, where="measurement")
    if "operator" not in measurement_map or "bindings" not in measurement_map:
        raise MeasurementError("measurement requires operator and bindings")
    operator = measurement_map["operator"]
    if operator not in _OPERATORS:
        raise MeasurementError(f"measurement.operator {operator!r} is unsupported")
    bindings = _mapping(measurement_map["bindings"], where="measurement.bindings")
    metric, unit, _comparator, _threshold, _temporal, aggregation = _criterion_parts(
        criterion, where="criterion"
    )
    del metric
    aggregation_kind = str(aggregation["kind"])
    if aggregation_kind == "integral" and operator != "contact_normal_force":
        raise MeasurementError(
            "integral aggregation is supported only for contact_normal_force"
        )

    if operator in {"site_position_error", "body_position_error"}:
        kind = "body" if operator in _BODY_OPERATORS else "site"
        if set(bindings) != {kind, "target_request_field"}:
            raise MeasurementError(
                f"{operator} bindings require {kind} and target_request_field"
            )
        _model_name(model, kind, bindings[kind], where=f"bindings.{kind}")
        target = _request_field(
            request,
            bindings["target_request_field"],
            where="bindings.target_request_field",
        )
        _vector(target, length=3, where="request target")
        expected = "m"
    elif operator in {"ordered_site_targets_error", "ordered_body_targets_error"}:
        kind = "body" if operator in _BODY_OPERATORS else "site"
        if set(bindings) != {kind, "target_request_fields"}:
            raise MeasurementError(
                f"{operator} bindings require {kind} and "
                "target_request_fields"
            )
        _model_name(model, kind, bindings[kind], where=f"bindings.{kind}")
        fields = bindings["target_request_fields"]
        if not isinstance(fields, (list, tuple)) or len(fields) < 2:
            raise MeasurementError(
                f"{operator} requires at least two target request fields"
            )
        for index, field in enumerate(fields):
            target = _request_field(
                request,
                field,
                where=f"bindings.target_request_fields[{index}]",
            )
            _vector(target, length=3, where=f"request target {field!r}")
        expected = "m"
    elif operator == "joint_position_error":
        if set(bindings) != {"joint", "target_request_field"}:
            raise MeasurementError(
                "joint_position_error bindings require joint and target_request_field"
            )
        _joint_id, joint_unit = _joint_info(
            model, bindings["joint"], where="bindings.joint"
        )
        target = _request_field(
            request,
            bindings["target_request_field"],
            where="bindings.target_request_field",
        )
        _number(target, where="request target")
        expected = joint_unit
    elif operator == "joint_drift":
        if set(bindings) != {"joint"}:
            raise MeasurementError("joint_drift bindings require joint only")
        _joint_id, joint_unit = _joint_info(
            model, bindings["joint"], where="bindings.joint"
        )
        expected = joint_unit
    elif operator == "joint_relation_error":
        if set(bindings) != {"joint", "other_joint", "multiplier", "offset"}:
            raise MeasurementError(
                "joint_relation_error bindings require joint, other_joint, "
                "multiplier, and offset"
            )
        _joint_id, joint_unit = _joint_info(
            model, bindings["joint"], where="bindings.joint"
        )
        _other_id, other_unit = _joint_info(
            model, bindings["other_joint"], where="bindings.other_joint"
        )
        if joint_unit != other_unit:
            raise MeasurementError(
                "joint_relation_error requires joints with the same derived unit"
            )
        _number(bindings["multiplier"], where="bindings.multiplier")
        _number(bindings["offset"], where="bindings.offset")
        expected = joint_unit
    else:
        if set(bindings) != {"geom1", "geom2"}:
            raise MeasurementError(
                "contact_normal_force bindings require geom1 and geom2"
            )
        _model_geom_name(model, bindings["geom1"], where="bindings.geom1")
        _model_geom_name(model, bindings["geom2"], where="bindings.geom2")
        if bindings["geom1"] == bindings["geom2"]:
            raise MeasurementError("contact_normal_force requires two distinct geoms")
        expected = _expected_unit(
            operator, joint_unit=None, aggregation=aggregation_kind
        )

    if unit != expected:
        raise MeasurementError(
            f"criterion.unit {unit!r} does not match {operator} ({expected!r})"
        )


def _sample_time(sample: Mapping[str, Any], *, where: str) -> float:
    value = sample.get("time")
    return _number(value, where=f"{where}.time")


def _sample_vector(sample: Mapping[str, Any], collection: str, name: str, *, where: str) -> np.ndarray:
    values = sample.get(collection)
    if not isinstance(values, Mapping) or name not in values:
        raise MeasurementError(f"{where} does not contain {collection}.{name}")
    return np.asarray(_vector(values[name], length=3, where=f"{where}.{collection}.{name}"), dtype=float)


def _sample_scalar(sample: Mapping[str, Any], name: str, *, where: str) -> float:
    values = sample.get("joint_positions")
    if not isinstance(values, Mapping) or name not in values:
        raise MeasurementError(f"{where} does not contain joint_positions.{name}")
    return _number(values[name], where=f"{where}.joint_positions.{name}")


def _sample_contacts(sample: Mapping[str, Any], *, where: str) -> Sequence[Any]:
    if "contacts" not in sample:
        raise MeasurementError(f"{where}.contacts is missing")
    contacts = sample.get("contacts")
    if not isinstance(contacts, list):
        raise MeasurementError(f"{where}.contacts must be an array")
    return contacts


def _contact_force(contact: Mapping[str, Any], *, where: str) -> float:
    key = "normal_force_N"
    if key not in contact:
        raise MeasurementError(
            f"{where} has no true normal force; capture with capture_sample first"
        )
    return _number(contact[key], where=f"{where}.{key}")


def _target_vector(request: Any, field: Any, *, where: str) -> np.ndarray:
    return np.asarray(
        _vector(
            _request_field(request, field, where=where),
            length=3,
            where=f"request target {field!r}",
        ),
        dtype=float,
    )


def _target_scalar(request: Any, field: Any, *, where: str) -> float:
    return _number(
        _request_field(request, field, where=where),
        where=f"request target {field!r}",
    )


def _ordered_trajectory_error(
    points: Sequence[np.ndarray], targets: Sequence[np.ndarray]
) -> float:
    """Return the smallest max target error using strictly ordered samples."""

    if len(points) < len(targets):
        raise MeasurementError(
            "ordered target measurement has fewer trajectory samples than targets"
        )
    # Dynamic programming over strictly increasing sample indices.  Keeping
    # only the prefix minimum of the previous target reduces the work to
    # O(number_of_targets * number_of_samples), which matters for real traces.
    previous = [
        float(np.linalg.norm(point - targets[0]))
        for point in points
    ]
    for target in targets[1:]:
        current: list[float] = []
        prefix_min = math.inf
        for sample_index in range(len(points)):
            distance = float(np.linalg.norm(points[sample_index] - target))
            if sample_index:
                prefix_min = min(prefix_min, previous[sample_index - 1])
            current.append(max(prefix_min, distance) if math.isfinite(prefix_min) else math.inf)
        previous = current
    if not previous or not any(math.isfinite(value) for value in previous):
        raise MeasurementError("ordered target trajectory is not strictly ordered")
    return float(min(previous))


def _relative_samples(
    samples: list[Mapping[str, Any]],
    temporal: Mapping[str, Any],
    *,
    operator: str,
    aggregation: str,
) -> tuple[list[Mapping[str, Any]], list[float], list[Mapping[str, Any]]]:
    if not samples:
        raise MeasurementError("no samples were captured")
    times = [_sample_time(sample, where=f"samples[{index}]") for index, sample in enumerate(samples)]
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise MeasurementError("samples are not ordered by time")
    base = times[0]
    relative = [time - base for time in times]
    if any(not math.isfinite(time) or time < -1e-12 for time in relative):
        raise MeasurementError("sample-relative times are invalid")
    if temporal.get("kind") == "terminal":
        if len(samples) < 2:
            raise MeasurementError("terminal criterion needs pre-call and post-call samples")
        # Ordered trajectories deliberately retain all action samples.  A
        # terminal endpoint operator still receives only the last sample.
        use_action_trace = (
            operator in {"ordered_site_targets_error", "ordered_body_targets_error"}
            or aggregation == "integral"
        )
        eligible = list(samples) if use_action_trace else [samples[-1]]
        eligible_times = list(relative) if use_action_trace else [relative[-1]]
        return eligible, eligible_times, list(samples)
    start = _number(temporal.get("start_s"), where="criterion.temporal.start_s")
    end = _number(temporal.get("end_s"), where="criterion.temporal.end_s")
    if relative[0] > start + 1e-9 or relative[-1] < end - 1e-9:
        raise MeasurementError(
            "insufficient samples to cover the complete requested time window"
        )
    selected = [
        (sample, time)
        for sample, time in zip(samples, relative)
        if start - 1e-9 <= time <= end + 1e-9
    ]
    if len(selected) < 2 or selected[0][1] > start + 1e-9 or selected[-1][1] < end - 1e-9:
        raise MeasurementError(
            "insufficient samples inside the requested time window"
        )
    return [item[0] for item in selected], [item[1] for item in selected], list(samples)


def _aggregate(values: Sequence[float], times: Sequence[float], kind: str) -> float:
    if not values:
        raise MeasurementError("no measurement values are available")
    numbers = np.asarray(values, dtype=float)
    if kind == "last":
        return float(numbers[-1])
    if kind == "max":
        return float(np.max(numbers))
    if kind == "min":
        return float(np.min(numbers))
    if kind == "mean":
        return float(np.mean(numbers))
    if len(values) < 2 or times[-1] <= times[0]:
        raise MeasurementError("integral aggregation needs at least two distinct sample times")
    return float(np.trapezoid(numbers, np.asarray(times, dtype=float)))


def _compare(value: float, comparator: str, threshold: Any) -> bool:
    if comparator == "<":
        return value < float(threshold)
    if comparator == "<=":
        return value <= float(threshold)
    if comparator == ">":
        return value > float(threshold)
    if comparator == ">=":
        return value >= float(threshold)
    if comparator == "==":
        return value == float(threshold)
    low, high = float(threshold[0]), float(threshold[1])
    return low <= value <= high


def _design_criterion(design: Any, capability_id: Any, criterion_index: Any) -> Mapping[str, Any]:
    if not isinstance(design, Mapping):
        raise MeasurementError("design must be an object")
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list):
        raise MeasurementError("design.capabilities must be an array")
    capability = next(
        (
            item
            for item in capabilities
            if isinstance(item, Mapping) and item.get("capability_id") == capability_id
        ),
        None,
    )
    if capability is None:
        raise MeasurementError(f"unknown capability_id {capability_id!r}")
    criteria = capability.get("criteria")
    if (
        not isinstance(criteria, list)
        or isinstance(criterion_index, bool)
        or not isinstance(criterion_index, int)
        or not 0 <= criterion_index < len(criteria)
        or not isinstance(criteria[criterion_index], Mapping)
    ):
        raise MeasurementError(
            f"criterion_index {criterion_index!r} is invalid for capability {capability_id!r}"
        )
    return criteria[criterion_index]


def _operator_values(
    operator: str,
    bindings: Mapping[str, Any],
    request: Any,
    selected: Sequence[Mapping[str, Any]],
    all_samples: Sequence[Mapping[str, Any]],
    *,
    where: str,
) -> list[float]:
    if operator in {"site_position_error", "body_position_error"}:
        kind = "body" if operator in _BODY_OPERATORS else "site"
        target = _target_vector(request, bindings["target_request_field"], where=where)
        return [
            float(
                np.linalg.norm(
                    _sample_vector(sample, f"{kind}_positions", bindings[kind], where=where)
                    - target
                )
            )
            for sample in selected
        ]
    if operator in {"ordered_site_targets_error", "ordered_body_targets_error"}:
        kind = "body" if operator in _BODY_OPERATORS else "site"
        targets = [
            _target_vector(request, field, where=where)
            for field in bindings["target_request_fields"]
        ]
        trajectory = [
            _sample_vector(sample, f"{kind}_positions", bindings[kind], where=where)
            for sample in selected
        ]
        return [_ordered_trajectory_error(trajectory, targets)]
    if operator == "joint_position_error":
        target = _target_scalar(request, bindings["target_request_field"], where=where)
        return [
            abs(_sample_scalar(sample, bindings["joint"], where=where) - target)
            for sample in selected
        ]
    if operator == "joint_drift":
        if not all_samples:
            raise MeasurementError("joint_drift has no action samples")
        initial = _sample_scalar(all_samples[0], bindings["joint"], where=where)
        return [
            abs(_sample_scalar(sample, bindings["joint"], where=where) - initial)
            for sample in selected
        ]
    if operator == "joint_relation_error":
        multiplier = _number(bindings["multiplier"], where=f"{where}.multiplier")
        offset = _number(bindings["offset"], where=f"{where}.offset")
        return [
            abs(
                _sample_scalar(sample, bindings["joint"], where=where)
                - multiplier * _sample_scalar(sample, bindings["other_joint"], where=where)
                - offset
            )
            for sample in selected
        ]
    forces: list[float] = []
    geom1, geom2 = bindings["geom1"], bindings["geom2"]

    def pair_matches(pair: tuple[Any, Any]) -> bool:
        geom1_names = {geom1}
        geom2_names = {geom2}
        if isinstance(geom1, str) and not geom1.endswith("_geom"):
            geom1_names.add(f"{geom1}_geom")
        if isinstance(geom2, str) and not geom2.endswith("_geom"):
            geom2_names.add(f"{geom2}_geom")
        return (pair[0] in geom1_names and pair[1] in geom2_names) or (
            pair[0] in geom2_names and pair[1] in geom1_names
        )

    for sample in selected:
        total = 0.0
        for index, raw_contact in enumerate(_sample_contacts(sample, where=where)):
            contact = _mapping(raw_contact, where=f"{where}.contacts[{index}]")
            pair = (contact.get("geom1"), contact.get("geom2"))
            if not pair_matches(pair):
                continue
            total += _contact_force(contact, where=f"{where}.contacts[{index}]")
        forces.append(total)
    # An absent pair is a physical zero force.  The explicit criterion
    # comparator/threshold decides whether that is acceptable; it must never
    # be turned into an automatic pass or an automatic error here.
    return forces


def evaluate_measurements(
    design: Any,
    case: Any,
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate each explicit case criterion and return JSON-safe results."""

    case_map = _mapping(case, where="case")
    measurements = case_map.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise MeasurementError("case.measurements must be a non-empty array")
    if not isinstance(samples, list):
        raise MeasurementError("samples must be an array")
    request = case_map.get("request")
    results: list[dict[str, Any]] = []
    for index, raw_measurement in enumerate(measurements):
        result: dict[str, Any] = {
            "criterion_index": (
                raw_measurement.get("criterion_index")
                if isinstance(raw_measurement, Mapping)
                else None
            ),
            "metric": None,
            "unit": None,
            "value": None,
            "threshold": None,
            "comparator": None,
            "ok": False,
        }
        try:
            measurement = _mapping(raw_measurement, where=f"measurements[{index}]")
            criterion_index = measurement.get("criterion_index")
            criterion = _design_criterion(
                design, case_map.get("capability_id"), criterion_index
            )
            metric, unit, comparator, threshold, temporal, aggregation = _criterion_parts(
                criterion, where=f"criterion[{criterion_index}]"
            )
            result.update(
                {
                    "metric": metric,
                    "unit": unit,
                    "threshold": copy.deepcopy(threshold),
                    "comparator": comparator,
                }
            )
            operator = measurement.get("operator")
            if operator not in _OPERATORS:
                raise MeasurementError(f"measurement.operator {operator!r} is unsupported")
            bindings = _mapping(
                measurement.get("bindings"),
                where=f"measurements[{index}].bindings",
            )
            selected, selected_times, all_samples = _relative_samples(
                [
                    _mapping(sample, where=f"samples[{sample_index}]")
                    for sample_index, sample in enumerate(samples)
                ],
                temporal,
                operator=operator,
                aggregation=str(aggregation["kind"]),
            )
            if aggregation["kind"] == "integral" and operator != "contact_normal_force":
                raise MeasurementError(
                    "integral aggregation is supported only for contact_normal_force"
                )
            values = _operator_values(
                operator,
                bindings,
                request,
                selected,
                all_samples,
                where=f"measurements[{index}]",
            )
            value = _aggregate(values, selected_times[-len(values) :] if len(values) > 1 else selected_times[: len(values)], str(aggregation["kind"]))
            result["value"] = value
            result["ok"] = _compare(value, comparator, threshold)
        except (MeasurementError, KeyError, TypeError, ValueError, IndexError) as exc:
            result["error"] = str(exc)
            result["ok"] = False
        results.append(result)
    return results


def capture_sample(model: Any, data: Any) -> dict[str, Any]:
    """Capture world body origins/sites, scalar joints, and normal contact forces.

    The caller must refresh derived quantities after stepping the simulation.
    """

    body_positions: dict[str, list[float]] = {}
    for body_id in range(int(model.nbody)):
        name = str(model.body(body_id).name)
        if name:
            body_positions[name] = [float(value) for value in np.asarray(data.xpos[body_id]).reshape(3)]

    site_positions: dict[str, list[float]] = {}
    for site_id in range(int(model.nsite)):
        name = str(model.site(site_id).name)
        if not name:
            continue
        site_positions[name] = [float(value) for value in np.asarray(data.site_xpos[site_id]).reshape(3)]

    joint_positions: dict[str, float] = {}
    for joint_id in range(int(model.njnt)):
        name = str(model.joint(joint_id).name)
        joint_type = int(model.jnt_type[joint_id])
        if not name or joint_type not in {
            int(mujoco.mjtJoint.mjJNT_HINGE),
            int(mujoco.mjtJoint.mjJNT_SLIDE),
        }:
            continue
        qpos_address = int(model.jnt_qposadr[joint_id])
        joint_positions[name] = float(data.qpos[qpos_address])

    contacts: list[dict[str, Any]] = []
    force = np.zeros(6, dtype=float)
    for contact_id in range(int(data.ncon)):
        contact = data.contact[contact_id]
        geom1_id, geom2_id = int(contact.geom1), int(contact.geom2)
        geom1_name = str(model.geom(geom1_id).name)
        geom2_name = str(model.geom(geom2_id).name)
        if not geom1_name or not geom2_name:
            continue
        force.fill(0.0)
        mujoco.mj_contactForce(model, data, contact_id, force)
        contacts.append(
            {
                "geom1": geom1_name,
                "geom2": geom2_name,
                "distance": float(contact.dist),
                "normal_force_N": float(force[0]),
            }
        )

    return {
        "time": float(data.time),
        "body_positions": body_positions,
        "site_positions": site_positions,
        "joint_positions": joint_positions,
        "contacts": contacts,
    }


def measurement_catalog() -> str:
    """Return the exact compact authoring contract for dynamic measurements."""

    return (
        "Supported case measurement bindings (operator is mandatory; metric text "
        "never selects an operator):\n"
        "- site_position_error: bindings={site, target_request_field}; value is "
        "Euclidean world site error, unit m.\n"
        "- body_position_error: bindings={body, target_request_field}; value is "
        "Euclidean world body-origin error, unit m. Use an existing body name "
        "when the robot has no suitable site; body origins are not centres of mass "
        "or fingertip offsets.\n"
        "- joint_position_error: bindings={joint, target_request_field}; scalar "
        "absolute error, unit rad for hinge or m for slide.\n"
        "- joint_drift: bindings={joint}; absolute change from the first sample, "
        "unit rad for hinge or m for slide.\n"
        "- joint_relation_error: bindings={joint, other_joint, multiplier, offset}; "
        "abs(joint - multiplier*other_joint - offset), same derived unit.\n"
        "- ordered_site_targets_error: bindings={site, target_request_fields:[field1, "
        "field2, ...]}; ordered trajectory matching all targets, unit m; it uses "
        "the recorded path even for temporal kind terminal.\n"
        "- ordered_body_targets_error: bindings={body, target_request_fields:[field1, "
        "field2, ...]}; ordered body-origin trajectory matching all targets, unit m; "
        "it uses the recorded path even for temporal kind terminal.\n"
        "- contact_normal_force: bindings={geom1, geom2}; sum of true "
        "mj_contactForce normal components per sample, unit N (or N*s with "
        "aggregation integral).\n"
        "Criteria must use numeric threshold (or [low, high] with comparator between), "
        "comparator one of < <= > >= == between, temporal exactly "
        "{kind:terminal} or {kind:window,start_s:...,end_s:...}, and aggregation "
        "exactly {kind:last|max|min|mean|integral}. Window samples must cover both "
        "endpoints; terminal requires pre-call and post-call samples. Integral uses "
        "the full captured action trace (including terminal temporal criteria) and "
        "trapezoids over sample-relative seconds. The contacts field itself is "
        "required; an absent named pair contributes physical zero force. Missing "
        "names, targets, or samples are errors and cannot pass.")


__all__ = [
    "MeasurementError",
    "capture_sample",
    "evaluate_measurements",
    "measurement_catalog",
    "validate_measurement",
]
