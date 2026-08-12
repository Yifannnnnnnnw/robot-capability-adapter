"""Public SO-ARM101 development probes backed by one fresh physics instance.

The provider uses the robot-only backend directly.  It does not construct a
serial transport or an upstream SDK object.  Public degree/percentage goals
are converted to the same bounded 0..4095 control ticks used by the admitted
robot interface, then the final public joint state is projected out.
"""

from __future__ import annotations

import ast
import copy
import math
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any

from .feetech_protocol import MOTOR_IDS, MOTOR_NAMES
from .translation import MuJoCoSO101Backend


SO_ARM101_MOTOR_ORDER = tuple(MOTOR_NAMES)
SO_ARM101_JOINT_ORDER = tuple(MOTOR_NAMES)
SO_ARM101_MOTOR_IDS = tuple(MOTOR_IDS[name] for name in SO_ARM101_MOTOR_ORDER)
SO_ARM101_PROBE_FIELDS = (
    "probe_id",
    "capability_id",
    "target_position",
    "duration_s",
)

SO_ARM101_MAX_DURATION_S = 5.0
SO_ARM101_MAX_ID_LENGTH = 128
SO_ARM101_ARM_MIN_DEG = -180.0
SO_ARM101_ARM_MAX_DEG = 180.0
SO_ARM101_GRIPPER_MIN = 0.0
SO_ARM101_GRIPPER_MAX = 100.0
SO_ARM101_RAW_TICK_MIN = 0
SO_ARM101_RAW_TICK_MAX = 4095

SO_ARM101_OBSERVATION_NAMES = (
    "probe_id",
    "capability_id",
    "joint_positions_rad",
    "named_joint_positions_rad",
    "named_control_positions_rad",
    "gripper_control_range_rad",
)

SO_ARM101_DEVELOPMENT_PROBE_CONTRACT: dict[str, Any] = {
    "contract_id": "so-arm101-development-probe",
    "version": "1.0.0",
    "robot": "so-arm101",
    "mode": "closed_position_segment",
    "backend": "fresh_pinned_model",
    "capability_source": {
        "syntax_check": True,
        "execution": False,
        "max_chars": 200_000,
    },
    "probe_fields": list(SO_ARM101_PROBE_FIELDS),
    "fields": {
        "probe_id": {"type": "string", "max_length": SO_ARM101_MAX_ID_LENGTH},
        "capability_id": {"type": "string", "max_length": SO_ARM101_MAX_ID_LENGTH},
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
            "joint_positions_rad": "rad",
            "named_joint_positions_rad": "rad",
            "named_control_positions_rad": "rad",
            "gripper_control_range_rad": "rad",
        },
    },
    "limits": {
        "max_duration_s": SO_ARM101_MAX_DURATION_S,
        "max_id_length": SO_ARM101_MAX_ID_LENGTH,
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


def _parse_source(capability_source: object) -> None:
    if not isinstance(capability_source, str) or not capability_source.strip():
        raise SOArm101DevelopmentProbeError("source_required")
    if len(capability_source.encode("utf-8")) > int(
        SO_ARM101_DEVELOPMENT_PROBE_CONTRACT["capability_source"]["max_chars"]
    ):
        raise SOArm101DevelopmentProbeError("source_too_large")
    try:
        ast.parse(capability_source, filename="<capability.py>", mode="exec")
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


def _probe_values(probe: Mapping[str, Any]) -> tuple[str, str, tuple[float, ...], float]:
    if not isinstance(probe, Mapping):
        raise SOArm101DevelopmentProbeError("probe_object_required")
    if set(probe) != set(SO_ARM101_PROBE_FIELDS):
        raise SOArm101DevelopmentProbeError("probe_fields_invalid")
    probe_id = _identifier(probe["probe_id"], "probe_id")
    capability_id = _identifier(probe["capability_id"], "capability_id")
    target_position = _target_position(probe["target_position"])
    duration_s = _number(
        probe["duration_s"],
        "duration_s",
        low=0.0,
        high=SO_ARM101_MAX_DURATION_S,
    )
    return probe_id, capability_id, target_position, duration_s


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


def _observations(
    backend: object,
    *,
    probe_id: str,
    capability_id: str,
) -> dict[str, Any]:
    state = backend.state()
    named_joint_positions = _named_values(state, "named_qpos")
    named_control_positions = _named_values(state, "named_ctrl")
    return {
        "probe_id": probe_id,
        "capability_id": capability_id,
        "joint_positions_rad": [named_joint_positions[name] for name in SO_ARM101_JOINT_ORDER],
        "named_joint_positions_rad": named_joint_positions,
        "named_control_positions_rad": named_control_positions,
        "gripper_control_range_rad": _range_values(state),
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
        feedback: dict[str, Any]
        try:
            _parse_source(capability_source)
            probe_id, capability_id, target_position, duration_s = _probe_values(probe)
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
            backend.set_goal_ticks(public_positions_to_ticks(target_position))
            backend.step(duration_s)
            feedback = _ok(
                _observations(
                    backend,
                    probe_id=probe_id,
                    capability_id=capability_id,
                )
            )
        except Exception:
            feedback = _error(
                "SO-ARM101 probe execution failed.",
                "probe_execution_error",
                observations={"probe_id": probe_id, "capability_id": capability_id},
            )
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    feedback = _error(
                        "SO-ARM101 probe execution failed.",
                        "probe_close_error",
                        observations={"probe_id": probe_id, "capability_id": capability_id},
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
