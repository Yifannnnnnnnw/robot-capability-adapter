"""Public SO-ARM101 development probes backed by one fresh physics instance.

Each call executes the submitted capability through a narrow public facade.
The facade maps the real LeRobot six-field container shape to the same bounded
0..4095 control ticks used by the admitted robot interface, then projects
public joint readback after a bounded physics segment.
"""

from __future__ import annotations

import ast
import builtins
import copy
import inspect
import keyword
import math
import re
import sys
import types
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any

from .feetech_protocol import MOTOR_IDS, MOTOR_NAMES
from .translation import MuJoCoSO101Backend


SO_ARM101_MOTOR_ORDER = tuple(MOTOR_NAMES)
SO_ARM101_JOINT_ORDER = tuple(MOTOR_NAMES)
SO_ARM101_MOTOR_IDS = tuple(MOTOR_IDS[name] for name in SO_ARM101_MOTOR_ORDER)
SO_ARM101_SDK_FIELDS = tuple(f"{name}.pos" for name in SO_ARM101_MOTOR_ORDER)
SO_ARM101_PUBLIC_OBSERVATION_FIELDS = SO_ARM101_SDK_FIELDS + ("public_task_state",)
SO_ARM101_PROBE_FIELDS = (
    "probe_id",
    "capability_id",
    "arguments",
    "target_position",
    "duration_s",
)

SO_ARM101_MAX_DURATION_S = 5.0
SO_ARM101_MAX_ID_LENGTH = 128
SO_ARM101_MAX_EXECUTION_EVENTS = 20_000
SO_ARM101_SOURCE_MAX_CHARS = 200_000
SO_ARM101_ARM_MIN_DEG = -180.0
SO_ARM101_ARM_MAX_DEG = 180.0
SO_ARM101_GRIPPER_MIN = 0.0
SO_ARM101_GRIPPER_MAX = 100.0
SO_ARM101_RAW_TICK_MIN = 0
SO_ARM101_RAW_TICK_MAX = 4095

SO_ARM101_OBSERVATION_NAMES = (
    "probe_id",
    "capability_id",
    "accepted_command_count",
    "physics_step_count",
    "simulation_time_s",
    "joint_positions_rad",
    "motion_delta_rad",
    "finite_observation_available",
)

SO_ARM101_DEVELOPMENT_PROBE_CONTRACT: dict[str, Any] = {
    "contract_id": "so-arm101-development-probe",
    "version": "2.0.0",
    "robot": "so-arm101",
    "mode": "source_coupled_sdk_probe",
    "backend": "fresh_pinned_model",
    "capability_source": {
        "syntax_check": True,
        "execution": True,
        "imports": ["math", "time", "numpy"],
        "max_chars": SO_ARM101_SOURCE_MAX_CHARS,
    },
    "probe_fields": list(SO_ARM101_PROBE_FIELDS),
    "facade": {
        "members": ["send_action", "get_observation"],
        "get_observation": {
            "type": "object",
            "fields": list(SO_ARM101_PUBLIC_OBSERVATION_FIELDS),
            "additional_properties": False,
            "public_task_state": {"type": "object"},
        },
        "send_action": {
            "type": "object",
            "fields": list(SO_ARM101_SDK_FIELDS),
            "additional_properties": False,
        },
    },
    "fields": {
        "probe_id": {"type": "string", "max_length": SO_ARM101_MAX_ID_LENGTH},
        "capability_id": {"type": "string", "max_length": SO_ARM101_MAX_ID_LENGTH},
        "arguments": {
            "type": "object",
            "key_policy": "bound_capability_public_input_names",
        },
        "target_position": {
            "type": "array",
            "length": 6,
            "order": list(SO_ARM101_JOINT_ORDER),
            "units": {
                "arm": "degree",
                "gripper": "normalized_0_100",
            },
            "arm_min": SO_ARM101_ARM_MIN_DEG,
            "arm_max": SO_ARM101_ARM_MAX_DEG,
            "gripper_min": SO_ARM101_GRIPPER_MIN,
            "gripper_max": SO_ARM101_GRIPPER_MAX,
        },
        "duration_s": {
            "type": "number",
            "unit": "s",
            "min": 0.0,
            "max": SO_ARM101_MAX_DURATION_S,
        },
    },
    "motor_order": list(SO_ARM101_MOTOR_ORDER),
    "joint_order": list(SO_ARM101_JOINT_ORDER),
    "motor_ids": list(SO_ARM101_MOTOR_IDS),
    "conversion": {
        "arm": "tick = int(((degree + 180) / 360) * 4095)",
        "gripper": "tick = int((normalized_0_100 / 100) * 4095)",
        "raw_tick_min": SO_ARM101_RAW_TICK_MIN,
        "raw_tick_max": SO_ARM101_RAW_TICK_MAX,
        "direction": "constructor_supplied",
    },
    "observations": {
        "names": list(SO_ARM101_OBSERVATION_NAMES),
        "units": {
            "probe_id": "identifier",
            "capability_id": "identifier",
            "accepted_command_count": "commands",
            "physics_step_count": "steps",
            "simulation_time_s": "s",
            "joint_positions_rad": "rad",
            "motion_delta_rad": "rad",
            "finite_observation_available": "boolean",
        },
    },
    "limits": {
        "max_duration_s": SO_ARM101_MAX_DURATION_S,
        "max_id_length": SO_ARM101_MAX_ID_LENGTH,
        "max_execution_events": SO_ARM101_MAX_EXECUTION_EVENTS,
        "arm_degree_range": [SO_ARM101_ARM_MIN_DEG, SO_ARM101_ARM_MAX_DEG],
        "gripper_range": [SO_ARM101_GRIPPER_MIN, SO_ARM101_GRIPPER_MAX],
        "raw_tick_range": [SO_ARM101_RAW_TICK_MIN, SO_ARM101_RAW_TICK_MAX],
    },
}

SO101_DEVELOPMENT_PROBE_CONTRACT = SO_ARM101_DEVELOPMENT_PROBE_CONTRACT
SO101_PROBE_CONTRACT = SO_ARM101_DEVELOPMENT_PROBE_CONTRACT
PUBLIC_SO_ARM101_DEVELOPMENT_PROBE_CONTRACT = SO_ARM101_DEVELOPMENT_PROBE_CONTRACT

_FORBIDDEN_PUBLIC_TERMS = (
    "private",
    "criterion",
    "validation",
    "blue line",
    "suite",
    "threshold",
    "mujoco",
    "translation",
    "simulator",
    "raw_state",
    "score",
    "target_error",
)


class SOArm101DevelopmentProbeError(ValueError):
    """Internal input or execution error converted to public feedback."""


def get_so_arm101_development_probe_contract() -> dict[str, Any]:
    """Return an independent JSON-compatible copy of the public contract."""

    return copy.deepcopy(SO_ARM101_DEVELOPMENT_PROBE_CONTRACT)


def _error(
    summary: str,
    exception: str,
    *,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "summary": summary,
        "observations": dict(observations or {}),
        "exception": exception,
    }


def _ok(observations: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "OK",
        "summary": "SO-ARM101 probe completed.",
        "observations": dict(observations),
        "exception": None,
    }


def _feedback(
    status: str,
    summary: str,
    observations: Mapping[str, Any],
    exception: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "observations": dict(observations),
        "exception": exception,
    }


def _parse_source(capability_source: object) -> Any:
    if not isinstance(capability_source, str) or not capability_source.strip():
        raise SOArm101DevelopmentProbeError("source_required")
    if len(capability_source.encode("utf-8")) > int(
        SO_ARM101_DEVELOPMENT_PROBE_CONTRACT["capability_source"]["max_chars"]
    ):
        raise SOArm101DevelopmentProbeError("source_too_large")
    try:
        tree = ast.parse(capability_source, filename="<capability.py>", mode="exec")
        return compile(tree, "<capability.py>", "exec")
    except (SyntaxError, ValueError, TypeError, UnicodeError) as exc:
        raise SOArm101DevelopmentProbeError("source_syntax_error") from exc


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > SO_ARM101_MAX_ID_LENGTH:
        raise SOArm101DevelopmentProbeError(f"{field}_invalid")
    lowered = value.lower()
    if any(term in lowered for term in _FORBIDDEN_PUBLIC_TERMS):
        raise SOArm101DevelopmentProbeError(f"{field}_invalid")
    return value


def _number(value: object, field: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SOArm101DevelopmentProbeError(f"{field}_invalid")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise SOArm101DevelopmentProbeError(f"{field}_invalid")
    return number


def _target_position(value: object) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise SOArm101DevelopmentProbeError("target_position_invalid")
    if len(value) != len(SO_ARM101_JOINT_ORDER):
        raise SOArm101DevelopmentProbeError("target_position_width")
    values = [
        _number(
            item,
            f"target_position_{index}",
            low=SO_ARM101_ARM_MIN_DEG if index < 5 else SO_ARM101_GRIPPER_MIN,
            high=SO_ARM101_ARM_MAX_DEG if index < 5 else SO_ARM101_GRIPPER_MAX,
        )
        for index, item in enumerate(value)
    ]
    return tuple(values)


def _probe_values(
    probe: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any], tuple[float, ...], float]:
    if not isinstance(probe, Mapping):
        raise SOArm101DevelopmentProbeError("probe_object_required")
    if set(probe) != set(SO_ARM101_PROBE_FIELDS):
        raise SOArm101DevelopmentProbeError("probe_fields_invalid")
    probe_id = _identifier(probe["probe_id"], "probe_id")
    capability_id = _identifier(probe["capability_id"], "capability_id")
    arguments = probe["arguments"]
    if not isinstance(arguments, Mapping):
        raise SOArm101DevelopmentProbeError("arguments_invalid")
    try:
        arguments_copy = copy.deepcopy(dict(arguments))
    except Exception as exc:
        raise SOArm101DevelopmentProbeError("arguments_invalid") from exc
    if any(not isinstance(name, str) or name == "_sdk" for name in arguments_copy):
        raise SOArm101DevelopmentProbeError("arguments_invalid")
    target_position = _target_position(probe["target_position"])
    duration_s = _number(
        probe["duration_s"],
        "duration_s",
        low=0.0,
        high=SO_ARM101_MAX_DURATION_S,
    )
    return probe_id, capability_id, arguments_copy, target_position, duration_s


def public_positions_to_ticks(target_position: Sequence[Real]) -> dict[int, int]:
    """Convert the six public position values to deterministic motor ticks."""

    values = _target_position(target_position)
    ticks: dict[int, int] = {}
    for index, name in enumerate(SO_ARM101_MOTOR_ORDER):
        value = values[index]
        if name == "gripper":
            fraction = value / 100.0
        else:
            fraction = (value + 180.0) / 360.0
        tick = int(fraction * SO_ARM101_RAW_TICK_MAX)
        ticks[SO_ARM101_MOTOR_IDS[index]] = min(
            SO_ARM101_RAW_TICK_MAX,
            max(SO_ARM101_RAW_TICK_MIN, tick),
        )
    return ticks


def _state_mapping(state: object, field: str) -> Mapping[str, Any]:
    if not isinstance(state, Mapping) or not isinstance(state.get(field), Mapping):
        raise SOArm101DevelopmentProbeError("state_shape_invalid")
    values = state[field]
    if set(values) != set(SO_ARM101_JOINT_ORDER):
        raise SOArm101DevelopmentProbeError("state_names_invalid")
    return values


def _named_values(state: object, field: str) -> dict[str, float]:
    values = _state_mapping(state, field)
    result: dict[str, float] = {}
    for name in SO_ARM101_JOINT_ORDER:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise SOArm101DevelopmentProbeError("state_value_invalid")
        number = float(value)
        if not math.isfinite(number):
            raise SOArm101DevelopmentProbeError("state_value_invalid")
        result[name] = number
    return result


def _range_values(state: object) -> list[float]:
    if not isinstance(state, Mapping):
        raise SOArm101DevelopmentProbeError("state_shape_invalid")
    value = state.get("gripper_control_range")
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != 2:
        raise SOArm101DevelopmentProbeError("state_range_invalid")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real):
            raise SOArm101DevelopmentProbeError("state_range_invalid")
        number = float(item)
        if not math.isfinite(number):
            raise SOArm101DevelopmentProbeError("state_range_invalid")
        result.append(number)
    if result[1] < result[0]:
        raise SOArm101DevelopmentProbeError("state_range_invalid")
    return result


def _capability_function_name(capability_id: str) -> str:
    """Mirror the deterministic Python Binding name derivation."""

    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", capability_id.strip()).strip("_") or "value"
    if normalized[0].isdigit() or keyword.iskeyword(normalized):
        normalized = f"value_{normalized}"
    return f"capability_{normalized}"


def _public_parameter_name(public_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", public_name.strip()).strip("_") or "value"
    if normalized[0].isdigit() or keyword.iskeyword(normalized):
        normalized = f"value_{normalized}"
    return f"arg_{normalized}"


def _sdk_action_to_ticks(action: Mapping[str, Any]) -> dict[int, int]:
    if tuple(action) != SO_ARM101_SDK_FIELDS:
        raise SOArm101DevelopmentProbeError("candidate_action_fields_invalid")
    values: list[float] = []
    for index, field in enumerate(SO_ARM101_SDK_FIELDS):
        values.append(
            _number(
                action[field],
                f"candidate_action_{index}",
                low=SO_ARM101_ARM_MIN_DEG if index < 5 else SO_ARM101_GRIPPER_MIN,
                high=SO_ARM101_ARM_MAX_DEG if index < 5 else SO_ARM101_GRIPPER_MAX,
            )
        )
    return public_positions_to_ticks(values)


def _tick_to_public_position(name: str, tick: Any) -> float:
    if isinstance(tick, bool) or not isinstance(tick, Real):
        raise SOArm101DevelopmentProbeError("observation_value_invalid")
    number = float(tick)
    if not math.isfinite(number) or not SO_ARM101_RAW_TICK_MIN <= number <= SO_ARM101_RAW_TICK_MAX:
        raise SOArm101DevelopmentProbeError("observation_value_invalid")
    if name == "gripper":
        return number * SO_ARM101_GRIPPER_MAX / SO_ARM101_RAW_TICK_MAX
    return number * (SO_ARM101_ARM_MAX_DEG - SO_ARM101_ARM_MIN_DEG) / SO_ARM101_RAW_TICK_MAX + SO_ARM101_ARM_MIN_DEG


def _public_sdk_observation(
    backend: object,
    *,
    gripper_tick_increases_qpos: bool,
) -> dict[str, float]:
    """Project backend readback into the six public LeRobot position fields."""

    present_ticks = getattr(backend, "present_ticks", None)
    if callable(present_ticks):
        raw_ticks = present_ticks(SO_ARM101_MOTOR_IDS)
        if not isinstance(raw_ticks, Mapping) or set(raw_ticks) != set(SO_ARM101_MOTOR_IDS):
            raise SOArm101DevelopmentProbeError("observation_shape_invalid")
        return {
            field: _tick_to_public_position(name, raw_ticks[motor_id])
            for field, name, motor_id in zip(
                SO_ARM101_SDK_FIELDS,
                SO_ARM101_MOTOR_ORDER,
                SO_ARM101_MOTOR_IDS,
                strict=True,
            )
        }

    state = backend.state()
    named_joint_positions = _named_values(state, "named_qpos")
    gripper_range = _range_values(state)
    if gripper_range[1] <= gripper_range[0]:
        raise SOArm101DevelopmentProbeError("state_range_invalid")
    result: dict[str, float] = {}
    for field, name in zip(SO_ARM101_SDK_FIELDS, SO_ARM101_MOTOR_ORDER, strict=True):
        value = named_joint_positions[name]
        if name == "gripper":
            fraction = (value - gripper_range[0]) / (gripper_range[1] - gripper_range[0])
            if not gripper_tick_increases_qpos:
                fraction = 1.0 - fraction
            result[field] = fraction * SO_ARM101_GRIPPER_MAX
        else:
            result[field] = math.degrees(value)
        if not math.isfinite(result[field]):
            raise SOArm101DevelopmentProbeError("observation_value_invalid")
    return result


def _joint_positions(backend: object) -> list[float]:
    state = backend.state()
    named_joint_positions = _named_values(state, "named_qpos")
    return [named_joint_positions[name] for name in SO_ARM101_JOINT_ORDER]


def _step_count(backend: object, duration_s: float) -> tuple[int, float]:
    if duration_s <= 0.0:
        return 0, 0.0
    timestep = getattr(backend, "timestep", None)
    if isinstance(timestep, Real) and not isinstance(timestep, bool) and math.isfinite(float(timestep)) and float(timestep) > 0.0:
        step_s = float(timestep)
        return max(1, math.ceil(duration_s / step_s - 1e-12)), step_s
    return 1, duration_s


class _SOArm101CandidateError(TypeError):
    """A bounded public shape error raised by the candidate-facing facade."""


class _SOArm101DevelopmentFacade:
    """The two-operation SO surface used by the source-coupled probe."""

    __slots__ = ("_backend", "_gripper_tick_increases_qpos", "_accepted_commands")

    def __init__(self, backend: object, gripper_tick_increases_qpos: bool) -> None:
        object.__setattr__(self, "_backend", backend)
        object.__setattr__(self, "_gripper_tick_increases_qpos", gripper_tick_increases_qpos)
        object.__setattr__(self, "_accepted_commands", 0)

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(f"SO-ARM101 facade has no public attribute {name!r}")
        return object.__getattribute__(self, name)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("SO-ARM101 facade is read-only")

    def send_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        owner = object.__getattribute__(self, "_backend")
        if not isinstance(action, Mapping):
            raise _SOArm101CandidateError("candidate_action_mapping_required")
        try:
            action_copy = dict(action)
        except Exception as exc:
            raise _SOArm101CandidateError("candidate_action_mapping_required") from exc
        try:
            ticks = _sdk_action_to_ticks(action_copy)
            owner.set_goal_ticks(ticks)
        except SOArm101DevelopmentProbeError as exc:
            raise _SOArm101CandidateError(str(exc)) from exc
        object.__setattr__(
            self,
            "_accepted_commands",
            int(object.__getattribute__(self, "_accepted_commands")) + 1,
        )
        return action_copy

    def get_observation(self) -> dict[str, Any]:
        owner = object.__getattribute__(self, "_backend")
        direction = object.__getattribute__(self, "_gripper_tick_increases_qpos")
        try:
            observation = _public_sdk_observation(owner, gripper_tick_increases_qpos=direction)
            observation["public_task_state"] = {}
            return observation
        except SOArm101DevelopmentProbeError as exc:
            raise _SOArm101CandidateError(str(exc)) from exc

    def _accepted_command_count(self) -> int:
        return int(object.__getattribute__(self, "_accepted_commands"))


class _SOArm101ProbeClock:
    """Virtual time for allowed candidate time calls; it never sleeps the host."""

    def __init__(self) -> None:
        self._time_s = 0.0

    def time(self) -> float:
        return float(self._time_s)

    monotonic = time
    perf_counter = time
    process_time = time

    def sleep(self, seconds: object) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, Real):
            raise TypeError("sleep duration must be numeric")
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0.0:
            raise ValueError("sleep duration must be finite and non-negative")
        self._time_s += duration
        if self._time_s > SO_ARM101_MAX_DURATION_S:
            raise SOArm101DevelopmentProbeError("execution_time_limit")


def _safe_execution_globals(clock: _SOArm101ProbeClock) -> dict[str, Any]:
    time_module = types.ModuleType("time")
    time_module.sleep = clock.sleep
    time_module.time = clock.time
    time_module.monotonic = clock.monotonic
    time_module.perf_counter = clock.perf_counter
    time_module.process_time = clock.process_time

    def safe_import(
        name: str,
        globals_: object = None,
        locals_: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        del globals_, locals_
        if level or fromlist:
            raise ImportError("candidate import is not allowed")
        if name == "math":
            return math
        if name == "time":
            return time_module
        if name == "numpy":
            try:
                import numpy as np
            except Exception as exc:  # pragma: no cover - dependency-specific
                raise ImportError("approved numerical dependency is unavailable") from exc
            return np
        raise ImportError("candidate import is not allowed")

    builtin_names = (
        "abs",
        "all",
        "any",
        "ArithmeticError",
        "AttributeError",
        "bool",
        "dict",
        "enumerate",
        "Exception",
        "float",
        "int",
        "IndexError",
        "isinstance",
        "KeyError",
        "len",
        "list",
        "max",
        "min",
        "OverflowError",
        "print",
        "range",
        "RuntimeError",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "TypeError",
        "ValueError",
        "zip",
    )
    safe_builtins = {name: getattr(builtins, name) for name in builtin_names}
    safe_builtins["__import__"] = safe_import
    return {
        "__name__": "capability_probe",
        "__builtins__": safe_builtins,
    }


def _bounded_candidate_exception(exc: BaseException) -> str:
    if isinstance(exc, _SOArm101CandidateError):
        return str(exc)
    if isinstance(exc, AttributeError):
        name = getattr(exc, "name", None)
        if (
            isinstance(name, str)
            and name.isidentifier()
            and not name.startswith("_")
            and len(name) <= SO_ARM101_MAX_ID_LENGTH
            and not any(term in name.lower() for term in _FORBIDDEN_PUBLIC_TERMS)
        ):
            return f"attribute_error:{name}"
        return "attribute_error"
    if isinstance(exc, TypeError):
        message = str(exc).lower()
        if "slice" in message:
            return "candidate_type_error:observation_mapping_required"
        if "mapping" in message or "action" in message:
            return "candidate_type_error:action_mapping_required"
        return "candidate_type_error"
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], slice):
        return "candidate_type_error:observation_mapping_required"
    if isinstance(exc, ImportError):
        return "candidate_import_error"
    if isinstance(exc, NameError):
        name = getattr(exc, "name", None)
        if (
            isinstance(name, str)
            and name.isidentifier()
            and not name.startswith("_")
            and not any(term in name.lower() for term in _FORBIDDEN_PUBLIC_TERMS)
        ):
            return f"name_error:{name}"
        return "name_error"
    return "candidate_execution_error"


def _public_observations(
    backend: object,
    facade: _SOArm101DevelopmentFacade,
    *,
    probe_id: str,
    capability_id: str,
    initial_positions: Sequence[float],
    physics_step_count: int,
    timestep_s: float,
) -> dict[str, Any]:
    positions = _joint_positions(backend)
    if len(initial_positions) != len(positions):
        raise SOArm101DevelopmentProbeError("observation_shape_invalid")
    motion_delta = max(
        abs(current - initial)
        for current, initial in zip(positions, initial_positions, strict=True)
    )
    if not math.isfinite(motion_delta):
        raise SOArm101DevelopmentProbeError("observation_value_invalid")
    sdk_observation = object.__getattribute__(facade, "get_observation")()
    if set(sdk_observation) != set(SO_ARM101_PUBLIC_OBSERVATION_FIELDS):
        raise SOArm101DevelopmentProbeError("observation_shape_invalid")
    if any(
        key != "public_task_state" and not math.isfinite(float(value))
        for key, value in sdk_observation.items()
    ):
        raise SOArm101DevelopmentProbeError("observation_value_invalid")
    return {
        "probe_id": probe_id,
        "capability_id": capability_id,
        "accepted_command_count": object.__getattribute__(facade, "_accepted_command_count")(),
        "physics_step_count": int(physics_step_count),
        "simulation_time_s": float(physics_step_count * timestep_s),
        "joint_positions_rad": positions,
        "motion_delta_rad": float(motion_delta),
        "finite_observation_available": True,
    }


class SOArm101DevelopmentProbe:
    """Callable public development probe for one pinned SO-ARM101 model."""

    contract = SO_ARM101_DEVELOPMENT_PROBE_CONTRACT

    def __init__(
        self,
        model_path: str | Path,
        gripper_tick_increases_qpos: bool,
        *,
        backend_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(gripper_tick_increases_qpos, bool):
            raise TypeError("gripper_tick_increases_qpos must be bool")
        self.model_path = model_path
        self.gripper_tick_increases_qpos = gripper_tick_increases_qpos
        self._backend_factory = backend_factory or MuJoCoSO101Backend

    def __call__(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        return self.run(capability_source, probe)

    def run(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        backend: Any | None = None
        facade: _SOArm101DevelopmentFacade | None = None
        probe_id = ""
        capability_id = ""
        candidate_exception: Exception | None = None
        feedback: dict[str, Any] = _error(
            "SO-ARM101 probe execution failed.",
            "probe_execution_error",
        )

        def minimal_observations() -> dict[str, Any]:
            result: dict[str, Any] = {}
            if probe_id:
                result["probe_id"] = probe_id
            if capability_id:
                result["capability_id"] = capability_id
            if facade is not None:
                result["accepted_command_count"] = object.__getattribute__(
                    facade,
                    "_accepted_command_count",
                )()
            return result

        try:
            code = _parse_source(capability_source)
            probe_id, capability_id, arguments, _target_position_values, duration_s = _probe_values(probe)
        except SOArm101DevelopmentProbeError as exc:
            return _error("SO-ARM101 probe input rejected.", str(exc))
        except Exception:
            return _error("SO-ARM101 probe input rejected.", "probe_input_error")

        try:
            backend = self._backend_factory(
                self.model_path,
                gripper_tick_increases_qpos=self.gripper_tick_increases_qpos,
            )
            backend.reset()
            initial_positions = _joint_positions(backend)
            facade = _SOArm101DevelopmentFacade(
                backend,
                self.gripper_tick_increases_qpos,
            )
            clock = _SOArm101ProbeClock()
            namespace = _safe_execution_globals(clock)
            namespace["_sdk"] = facade
            previous_trace = sys.gettrace()
            events = 0
            candidate_error: Exception | None = None
            try:
                def execution_trace(frame: types.FrameType, event: str, arg: object) -> Any:
                    nonlocal events
                    del arg
                    if frame.f_code.co_filename == "<capability.py>" and event in {"line", "call"}:
                        events += 1
                        if events > SO_ARM101_MAX_EXECUTION_EVENTS:
                            raise SOArm101DevelopmentProbeError("execution_limit")
                    return execution_trace

                sys.settrace(execution_trace)
                try:
                    exec(code, namespace, namespace)
                    function = namespace.get(_capability_function_name(capability_id))
                    if not callable(function):
                        raise SOArm101DevelopmentProbeError("capability_function_missing")
                    parameters = inspect.signature(function).parameters
                    call_arguments: dict[str, Any] = {}
                    for public_name, value in arguments.items():
                        generated_name = _public_parameter_name(public_name)
                        if generated_name in parameters and public_name not in parameters:
                            call_arguments[generated_name] = value
                        else:
                            call_arguments[public_name] = value
                    function(**call_arguments, _sdk=facade)
                except Exception as exc:
                    candidate_error = exc
            finally:
                sys.settrace(previous_trace)
            if candidate_error is not None:
                candidate_exception = candidate_error
                raise candidate_error

            physics_step_count, timestep_s = _step_count(backend, duration_s)
            if duration_s > 0.0:
                backend.step(duration_s)
            final_observations = _public_observations(
                backend,
                facade,
                probe_id=probe_id,
                capability_id=capability_id,
                initial_positions=initial_positions,
                physics_step_count=physics_step_count,
                timestep_s=timestep_s,
            )
            accepted_command_count = int(final_observations["accepted_command_count"])
            if (
                accepted_command_count >= 1
                and physics_step_count > 0
                and final_observations["finite_observation_available"] is True
            ):
                feedback = _ok(final_observations)
            else:
                feedback = _feedback(
                    "INCONCLUSIVE",
                    "SO-ARM101 probe returned without an accepted physical effect.",
                    final_observations,
                )
        except SOArm101DevelopmentProbeError as exc:
            feedback = _error(
                "SO-ARM101 probe execution failed.",
                str(exc),
                observations=minimal_observations(),
            )
        except Exception as exc:
            feedback = _error(
                "SO-ARM101 probe execution failed.",
                _bounded_candidate_exception(candidate_exception)
                if candidate_exception is not None
                else "probe_execution_error",
                observations=minimal_observations(),
            )
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    feedback = _error(
                        "SO-ARM101 probe execution failed.",
                        "probe_close_error",
                        observations=minimal_observations(),
                    )
        return feedback


SO101DevelopmentProbe = SOArm101DevelopmentProbe
SOArm101DevelopmentSandbox = SOArm101DevelopmentProbe


def create_so_arm101_development_probe(
    model_path: str | Path,
    gripper_tick_increases_qpos: bool,
    *,
    backend_factory: Callable[..., Any] | None = None,
) -> SOArm101DevelopmentProbe:
    """Create a callback that owns a fresh backend for each call."""

    return SOArm101DevelopmentProbe(
        model_path,
        gripper_tick_increases_qpos,
        backend_factory=backend_factory,
    )


def create_so_arm101_development_sandbox(
    model_path: str | Path,
    gripper_tick_increases_qpos: bool,
    *,
    backend_factory: Callable[..., Any] | None = None,
) -> SOArm101DevelopmentProbe:
    return create_so_arm101_development_probe(
        model_path,
        gripper_tick_increases_qpos,
        backend_factory=backend_factory,
    )


so_arm101_development_probe_callback = create_so_arm101_development_probe


__all__ = [
    "PUBLIC_SO_ARM101_DEVELOPMENT_PROBE_CONTRACT",
    "SO101DevelopmentProbe",
    "SO101_DEVELOPMENT_PROBE_CONTRACT",
    "SO101_PROBE_CONTRACT",
    "SO_ARM101_DEVELOPMENT_PROBE_CONTRACT",
    "SO_ARM101_JOINT_ORDER",
    "SO_ARM101_MAX_DURATION_S",
    "SO_ARM101_MOTOR_IDS",
    "SO_ARM101_MOTOR_ORDER",
    "SO_ARM101_PUBLIC_OBSERVATION_FIELDS",
    "SO_ARM101_SDK_FIELDS",
    "SO_ARM101_OBSERVATION_NAMES",
    "SO_ARM101_PROBE_FIELDS",
    "SOArm101DevelopmentProbe",
    "SOArm101DevelopmentProbeError",
    "SOArm101DevelopmentSandbox",
    "create_so_arm101_development_probe",
    "create_so_arm101_development_sandbox",
    "get_so_arm101_development_probe_contract",
    "public_positions_to_ticks",
    "so_arm101_development_probe_callback",
]
