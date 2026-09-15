"""Experiment-local physical scorers for the three Chapter 3 PiPER tasks.

The worker installs these handlers into the existing AA1 DEMO evaluator for its
own process.  Trace validation, aggregation, and report semantics remain owned
by that evaluator; this module only supplies the three approved task predicates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from auto_adapter import demo_evaluation as _demo


_POSITION_EPSILON_M = 1e-9
_COMMON_FIELDS = {
    "type",
    "bindings",
    "target_parameter",
    "metric",
    "comparator",
    "tolerance_m",
    "temporal",
}


def _contract(
    spec: Mapping[str, Any],
    *,
    family: str,
    temporal: str,
    binding_kinds: Mapping[str, str],
    extra_fields: Sequence[str] = (),
) -> Mapping[str, Mapping[str, Any]]:
    expected = {
        "type": family,
        "metric": "euclidean_distance_3d",
        "comparator": "<=",
        "temporal": temporal,
    }
    for field, value in expected.items():
        if spec.get(field) != value:
            raise _demo._Unscorable(
                f"{family} requires success_spec.{field}={value!r}"
            )

    unknown = set(spec) - (_COMMON_FIELDS | set(extra_fields))
    if unknown:
        raise _demo._Unscorable(
            f"{family} has unsupported success fields: {sorted(unknown)!r}"
        )

    bindings = _demo._mapping(spec.get("bindings"), "success_spec.bindings")
    if set(bindings) != set(binding_kinds):
        raise _demo._Unscorable(
            f"{family} requires bindings {sorted(binding_kinds)!r}"
        )
    checked: dict[str, Mapping[str, Any]] = {}
    for label, expected_kind in binding_kinds.items():
        binding = _demo._mapping(bindings[label], f"binding {label!r}")
        if binding.get("kind") != expected_kind:
            raise _demo._Unscorable(
                f"binding {label!r} must have kind {expected_kind!r}"
            )
        unknown_binding_fields = set(binding) - {"kind", "name", "local_position_m"}
        if unknown_binding_fields:
            raise _demo._Unscorable(
                f"binding {label!r} has unsupported fields: "
                f"{sorted(unknown_binding_fields)!r}"
            )
        checked[label] = binding
    return checked


def _target_and_tolerance(
    spec: Mapping[str, Any], parameters: Mapping[str, Any]
) -> tuple[list[float], float]:
    target_parameter = spec.get("target_parameter")
    if not isinstance(target_parameter, str) or not target_parameter:
        raise _demo._Unscorable("success_spec.target_parameter must name a parameter")
    target = _demo._vector(
        parameters.get(target_parameter),
        3,
        f"parameters.{target_parameter}",
    )
    tolerance = _demo._nonnegative(spec.get("tolerance_m"), "tolerance_m")
    return target, tolerance


def _body_names(value: Any, name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise _demo._Unscorable(f"{name} must be a nonempty list of body names")
    result = list(value)
    if not all(isinstance(item, str) and item for item in result):
        raise _demo._Unscorable(f"{name} must contain nonempty body names")
    if len(set(result)) != len(result):
        raise _demo._Unscorable(f"{name} must not contain duplicate body names")
    return result


def _contacts(sample: Mapping[str, Any], object_name: str, body_names: Sequence[str]) -> set[str]:
    contacted: set[str] = set()
    for pair_arg in sample.get("contacts", ()):
        pair = set(pair_arg)
        if object_name in pair:
            contacted.update(pair.intersection(body_names))
    return contacted


def _at_most(value: float, threshold: float) -> bool:
    """Apply ``<=`` with only a picometre-scale float round-off allowance."""

    return value <= threshold + 1e-12


def _ee_at_target(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    _times: list[float],
) -> list[dict[str, Any]]:
    _contract(
        spec,
        family="ee_at_target",
        temporal="terminal_state",
        binding_kinds={"ee": "site"},
    )
    target, tolerance = _target_and_tolerance(spec, parameters)
    error = _demo._distance(_demo._position(samples, "ee", -1), target)
    return [
        _demo._metric(
            "terminal_ee_goal_distance_m",
            error,
            tolerance,
            _at_most(error, tolerance),
            target_m=target,
        )
    ]


def _object_pushed_to_target(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    _times: list[float],
) -> list[dict[str, Any]]:
    bindings = _contract(
        spec,
        family="object_pushed_to_target",
        temporal="terminal_state_after_push",
        binding_kinds={"object": "body", "ee": "site"},
        extra_fields=("contact_bodies", "require_contact_driven_motion"),
    )
    if spec.get("require_contact_driven_motion") is not True:
        raise _demo._Unscorable(
            "object_pushed_to_target requires contact-driven motion"
        )
    target, tolerance = _target_and_tolerance(spec, parameters)
    object_name = str(bindings["object"]["name"])
    contact_bodies = _body_names(spec.get("contact_bodies"), "contact_bodies")
    if object_name in contact_bodies:
        raise _demo._Unscorable("contact_bodies cannot contain the pushed object")

    evidence: tuple[int, float, float, float] | None = None
    for index in range(1, len(samples)):
        contacted = _contacts(samples[index], object_name, contact_bodies)
        if not contacted:
            continue
        object_before = _demo._position(samples, "object", index - 1)
        object_after = _demo._position(samples, "object", index)
        object_motion = _demo._distance(object_before, object_after)
        ee_motion = _demo._distance(
            _demo._position(samples, "ee", index - 1),
            _demo._position(samples, "ee", index),
        )
        goal_progress = (
            _demo._distance(object_before, target)
            - _demo._distance(object_after, target)
        )
        if (
            object_motion > _POSITION_EPSILON_M
            and ee_motion > _POSITION_EPSILON_M
        ):
            evidence = (index, object_motion, ee_motion, goal_progress)
            break

    terminal_error = _demo._distance(
        _demo._position(samples, "object", -1), target
    )
    return [
        _demo._metric(
            "contact_driven_object_motion_m",
            None if evidence is None else evidence[1],
            _POSITION_EPSILON_M,
            evidence is not None,
            comparator=">",
            sample_index=None if evidence is None else evidence[0],
            end_effector_motion_m=None if evidence is None else evidence[2],
            goal_error_reduction_m=None if evidence is None else evidence[3],
            contact_bodies=contact_bodies,
        ),
        _demo._metric(
            "terminal_object_goal_distance_m",
            terminal_error,
            tolerance,
            _at_most(terminal_error, tolerance),
            target_m=target,
        ),
    ]


def _object_picked_and_placed(
    spec: Mapping[str, Any],
    parameters: Mapping[str, Any],
    samples: list[Mapping[str, Any]],
    _times: list[float],
) -> list[dict[str, Any]]:
    bindings = _contract(
        spec,
        family="object_picked_and_placed",
        temporal="terminal_state_after_release",
        binding_kinds={
            "object": "body",
            "left_finger": "body",
            "right_finger": "body",
            "gripper": "joint",
            "initial_support": "body",
            "receiving_support": "body",
        },
        extra_fields=("gripper_bodies", "required_phases"),
    )
    phases = spec.get("required_phases")
    if (
        isinstance(phases, (str, bytes))
        or not isinstance(phases, Sequence)
        or list(phases) != ["grasp", "lift", "release"]
    ):
        raise _demo._Unscorable(
            "object_picked_and_placed requires phases ['grasp', 'lift', 'release']"
        )

    object_name = str(bindings["object"]["name"])
    finger_names = _body_names(spec.get("gripper_bodies"), "gripper_bodies")
    bound_finger_names = {
        str(bindings["left_finger"]["name"]),
        str(bindings["right_finger"]["name"]),
    }
    if len(bound_finger_names) != 2 or set(finger_names) != bound_finger_names:
        raise _demo._Unscorable(
            "gripper_bodies must name the two bound finger bodies"
        )
    initial_support_name = str(bindings["initial_support"]["name"])
    receiving_support_name = str(bindings["receiving_support"]["name"])
    if len({object_name, initial_support_name, receiving_support_name}) != 3:
        raise _demo._Unscorable(
            "object, initial support, and receiving support must be distinct bodies"
        )

    target, tolerance = _target_and_tolerance(spec, parameters)
    initial_position = _demo._position(samples, "object", 0)
    initial_support_contact = initial_support_name in _contacts(
        samples[0], object_name, [initial_support_name]
    )

    grasp_index: int | None = None
    if initial_support_contact:
        required_fingers = set(finger_names)
        grasp_index = next(
            (
                index
                for index in range(1, len(samples))
                if _contacts(samples[index], object_name, finger_names)
                == required_fingers
            ),
            None,
        )

    lift_index: int | None = None
    if grasp_index is not None:
        for index in range(grasp_index + 1, len(samples)):
            object_position = _demo._position(samples, "object", index)
            previous_position = _demo._position(samples, "object", index - 1)
            lifted_during_interval = (
                object_position[2] - previous_position[2]
                > _POSITION_EPSILON_M
            )
            above_initial_support = (
                object_position[2] - initial_position[2]
                > _POSITION_EPSILON_M
            )
            separated = initial_support_name not in _contacts(
                samples[index], object_name, [initial_support_name]
            )
            held = bool(_contacts(samples[index], object_name, finger_names))
            if lifted_during_interval and above_initial_support and separated and held:
                lift_index = index
                break

    release_index: int | None = None
    if lift_index is not None:
        release_index = next(
            (
                index
                for index in range(lift_index + 1, len(samples))
                if not _contacts(samples[index], object_name, finger_names)
            ),
            None,
        )

    terminal_contacts = _contacts(samples[-1], object_name, finger_names)
    terminal_released = release_index is not None and not terminal_contacts
    terminal_error = _demo._distance(
        _demo._position(samples, "object", -1), target
    )
    receiving_support_contact = receiving_support_name in _contacts(
        samples[-1], object_name, [receiving_support_name]
    )
    lift_rise = (
        None
        if lift_index is None
        else _demo._position(samples, "object", lift_index)[2] - initial_position[2]
    )
    return [
        _demo._metric(
            "object_initial_support_contact",
            initial_support_contact,
            True,
            initial_support_contact,
        ),
        _demo._metric(
            "ordered_grasp_contact",
            grasp_index,
            "after_initial_support",
            grasp_index is not None,
            gripper_value=None
            if grasp_index is None
            else _demo._value(samples, "gripper", grasp_index),
        ),
        _demo._metric(
            "ordered_contact_lift_from_initial_support_m",
            lift_rise,
            _POSITION_EPSILON_M,
            lift_index is not None,
            comparator=">",
            sample_index=lift_index,
        ),
        _demo._metric(
            "ordered_release_contact_off",
            release_index,
            "after_lift",
            terminal_released,
            terminal_gripper_contacts=sorted(terminal_contacts),
            gripper_value=None
            if release_index is None
            else _demo._value(samples, "gripper", release_index),
        ),
        _demo._metric(
            "terminal_object_goal_distance_m",
            terminal_error,
            tolerance,
            _at_most(terminal_error, tolerance),
            target_m=target,
            receiving_support_contact=receiving_support_contact,
        ),
    ]


def install_experiment_scoring() -> None:
    """Install the three experiment predicates in the current worker process."""

    _demo._FAMILIES.update(
        {
            "ee_at_target": _ee_at_target,
            "object_pushed_to_target": _object_pushed_to_target,
            "object_picked_and_placed": _object_picked_and_placed,
        }
    )


__all__ = ["install_experiment_scoring"]
