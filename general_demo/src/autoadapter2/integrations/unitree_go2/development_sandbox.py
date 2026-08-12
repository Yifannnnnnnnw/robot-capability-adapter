"""Public Go2 development probes backed by one fresh physics instance.

The provider is deliberately narrower than the production robot session.  It
accepts one closed joint-PD segment, applies the resulting actuator controls,
and returns only the public sensor projection.  The candidate source is parsed
for syntax only; it is never imported or executed here.
"""

from __future__ import annotations

import ast
import copy
import math
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any

from .bridge import ACTIVE_JOINT_NAMES, ACTIVE_MOTOR_NAMES, MuJoCoGo2Backend


GO2_MOTOR_ORDER = tuple(ACTIVE_MOTOR_NAMES)
GO2_JOINT_ORDER = tuple(ACTIVE_JOINT_NAMES)
GO2_PROBE_FIELDS = (
    "probe_id",
    "capability_id",
    "target_q",
    "target_dq",
    "kp",
    "kd",
    "tau",
    "steps",
)

# The cap is intentionally a control-step cap rather than a wall-clock
# promise.  It keeps a development call bounded while allowing a useful short
# physical segment at the pinned scene timestep.
GO2_MAX_STEPS = 2_000
GO2_MAX_ID_LENGTH = 128
GO2_TARGET_Q_ABS_MAX_RAD = 10.0
GO2_TARGET_DQ_ABS_MAX_RAD_S = 100.0
GO2_KP_MAX = 1_000.0
GO2_KD_MAX = 100.0
GO2_TAU_ABS_MAX = 1_000.0

GO2_OBSERVATION_NAMES = (
    "probe_id",
    "capability_id",
    "simulation_time_s",
    "joint_positions_rad",
    "joint_velocities_rad_s",
    "frame_position_m",
    "frame_linear_velocity_m_s",
    "imu_quaternion_wxyz",
)

# This is a plain JSON-compatible mapping on purpose: Stage 2 can copy it
# into its own public bundle without importing a backend or a session.
GO2_DEVELOPMENT_PROBE_CONTRACT: dict[str, Any] = {
    "contract_id": "unitree-go2-development-probe",
    "version": "1.0.0",
    "robot": "unitree-go2",
    "mode": "closed_joint_pd_segment",
    "backend": "fresh_pinned_scene",
    "capability_source": {
        "syntax_check": True,
        "execution": False,
        "max_chars": 200_000,
    },
    "probe_fields": list(GO2_PROBE_FIELDS),
    "fields": {
        "probe_id": {"type": "string", "max_length": GO2_MAX_ID_LENGTH},
        "capability_id": {"type": "string", "max_length": GO2_MAX_ID_LENGTH},
        "target_q": {
            "type": "array",
            "length": 12,
            "unit": "rad",
            "min": -GO2_TARGET_Q_ABS_MAX_RAD,
            "max": GO2_TARGET_Q_ABS_MAX_RAD,
        },
        "target_dq": {
            "type": "array",
            "length": 12,
            "unit": "rad/s",
            "min": -GO2_TARGET_DQ_ABS_MAX_RAD_S,
            "max": GO2_TARGET_DQ_ABS_MAX_RAD_S,
        },
        "kp": {
            "type": "array",
            "length": 12,
            "unit": "position_gain",
            "min": 0.0,
            "max": GO2_KP_MAX,
        },
        "kd": {
            "type": "array",
            "length": 12,
            "unit": "velocity_gain",
            "min": 0.0,
            "max": GO2_KD_MAX,
        },
        "tau": {
            "type": "array",
            "length": 12,
            "unit": "torque",
            "min": -GO2_TAU_ABS_MAX,
            "max": GO2_TAU_ABS_MAX,
        },
        "steps": {
            "type": "integer",
            "unit": "physics_steps",
            "min": 1,
            "max": GO2_MAX_STEPS,
        },
    },
    "motor_order": list(GO2_MOTOR_ORDER),
    "joint_order": list(GO2_JOINT_ORDER),
    "control_equation": "tau + kp * (target_q - current_q) + kd * (target_dq - current_dq)",
    "observations": {
        "names": list(GO2_OBSERVATION_NAMES),
        "units": {
            "probe_id": "identifier",
            "capability_id": "identifier",
            "simulation_time_s": "s",
            "joint_positions_rad": "rad",
            "joint_velocities_rad_s": "rad/s",
            "frame_position_m": "m",
            "frame_linear_velocity_m_s": "m/s",
            "imu_quaternion_wxyz": "unitless_wxyz",
        },
    },
    "limits": {
        "max_steps": GO2_MAX_STEPS,
        "max_id_length": GO2_MAX_ID_LENGTH,
        "max_target_q_abs_rad": GO2_TARGET_Q_ABS_MAX_RAD,
        "max_target_dq_abs_rad_s": GO2_TARGET_DQ_ABS_MAX_RAD_S,
        "max_kp": GO2_KP_MAX,
        "max_kd": GO2_KD_MAX,
        "max_tau_abs": GO2_TAU_ABS_MAX,
    },
}

# Short aliases make the provider discoverable without creating a second
# contract object that could drift from the canonical mapping.
GO2_PROBE_CONTRACT = GO2_DEVELOPMENT_PROBE_CONTRACT
PUBLIC_GO2_DEVELOPMENT_PROBE_CONTRACT = GO2_DEVELOPMENT_PROBE_CONTRACT

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


class Go2DevelopmentProbeError(ValueError):
    """Internal input or execution error converted to public feedback."""


def get_go2_development_probe_contract() -> dict[str, Any]:
    """Return an independent JSON-compatible copy of the public contract."""

    return copy.deepcopy(GO2_DEVELOPMENT_PROBE_CONTRACT)


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
        "summary": "Go2 probe completed.",
        "observations": dict(observations),
        "exception": None,
    }


def _parse_source(capability_source: object) -> None:
    if not isinstance(capability_source, str) or not capability_source.strip():
        raise Go2DevelopmentProbeError("source_required")
    if len(capability_source.encode("utf-8")) > int(GO2_DEVELOPMENT_PROBE_CONTRACT["capability_source"]["max_chars"]):
        raise Go2DevelopmentProbeError("source_too_large")
    try:
        ast.parse(capability_source, filename="<capability.py>", mode="exec")
    except (SyntaxError, ValueError, TypeError, UnicodeError) as exc:
        raise Go2DevelopmentProbeError("source_syntax_error") from exc


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > GO2_MAX_ID_LENGTH:
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    lowered = value.lower()
    if any(term in lowered for term in _FORBIDDEN_PUBLIC_TERMS):
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    return value


def _number(value: object, field: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    return number


def _vector(
    value: object,
    field: str,
    *,
    length: int,
    low: float,
    high: float,
) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise Go2DevelopmentProbeError(f"{field}_invalid")
    if len(value) != length:
        raise Go2DevelopmentProbeError(f"{field}_width")
    return tuple(
        _number(item, f"{field}_{index}", low=low, high=high)
        for index, item in enumerate(value)
    )


def _probe_values(probe: Mapping[str, Any]) -> tuple[str, str, tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...], int]:
    if not isinstance(probe, Mapping):
        raise Go2DevelopmentProbeError("probe_object_required")
    if set(probe) != set(GO2_PROBE_FIELDS):
        raise Go2DevelopmentProbeError("probe_fields_invalid")
    probe_id = _identifier(probe["probe_id"], "probe_id")
    capability_id = _identifier(probe["capability_id"], "capability_id")
    target_q = _vector(
        probe["target_q"],
        "target_q",
        length=12,
        low=-GO2_TARGET_Q_ABS_MAX_RAD,
        high=GO2_TARGET_Q_ABS_MAX_RAD,
    )
    target_dq = _vector(
        probe["target_dq"],
        "target_dq",
        length=12,
        low=-GO2_TARGET_DQ_ABS_MAX_RAD_S,
        high=GO2_TARGET_DQ_ABS_MAX_RAD_S,
    )
    kp = _vector(probe["kp"], "kp", length=12, low=0.0, high=GO2_KP_MAX)
    kd = _vector(probe["kd"], "kd", length=12, low=0.0, high=GO2_KD_MAX)
    tau = _vector(
        probe["tau"],
        "tau",
        length=12,
        low=-GO2_TAU_ABS_MAX,
        high=GO2_TAU_ABS_MAX,
    )
    steps = probe["steps"]
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= GO2_MAX_STEPS:
        raise Go2DevelopmentProbeError("steps_invalid")
    return probe_id, capability_id, target_q, target_dq, kp, kd, tau, steps


def _state_field(state: object, name: str) -> object:
    if isinstance(state, Mapping):
        try:
            return state[name]
        except KeyError as exc:
            raise Go2DevelopmentProbeError("state_field_missing") from exc
    try:
        return getattr(state, name)
    except AttributeError as exc:
        raise Go2DevelopmentProbeError("state_field_missing") from exc


def _state_vector(state: object, name: str, length: int) -> list[float]:
    value = _state_field(state, name)
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) != length:
        raise Go2DevelopmentProbeError("state_shape_invalid")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real):
            raise Go2DevelopmentProbeError("state_value_invalid")
        number = float(item)
        if not math.isfinite(number):
            raise Go2DevelopmentProbeError("state_value_invalid")
        result.append(number)
    return result


def _observations(
    backend: object,
    *,
    probe_id: str,
    capability_id: str,
) -> dict[str, Any]:
    simulation_time = getattr(backend, "simulation_time")
    if isinstance(simulation_time, bool) or not isinstance(simulation_time, Real):
        raise Go2DevelopmentProbeError("time_invalid")
    simulation_time_s = float(simulation_time)
    if not math.isfinite(simulation_time_s) or simulation_time_s < 0:
        raise Go2DevelopmentProbeError("time_invalid")
    state = backend.sensors()
    return {
        "probe_id": probe_id,
        "capability_id": capability_id,
        "simulation_time_s": simulation_time_s,
        "joint_positions_rad": _state_vector(state, "q", 12),
        "joint_velocities_rad_s": _state_vector(state, "dq", 12),
        "frame_position_m": _state_vector(state, "frame_position", 3),
        "frame_linear_velocity_m_s": _state_vector(state, "frame_linear_velocity", 3),
        "imu_quaternion_wxyz": _state_vector(state, "imu_quaternion", 4),
    }


class Go2DevelopmentProbe:
    """Callable public development probe for one pinned Go2 scene path."""

    contract = GO2_DEVELOPMENT_PROBE_CONTRACT

    def __init__(
        self,
        model_path: str | Path,
        *,
        backend_factory: Callable[[str | Path], Any] | None = None,
    ) -> None:
        self.model_path = model_path
        self._backend_factory = backend_factory or MuJoCoGo2Backend

    def __call__(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        return self.run(capability_source, probe)

    def run(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        backend: Any | None = None
        feedback: dict[str, Any]
        try:
            _parse_source(capability_source)
            (
                probe_id,
                capability_id,
                target_q,
                target_dq,
                kp,
                kd,
                tau,
                steps,
            ) = _probe_values(probe)
        except Go2DevelopmentProbeError as exc:
            return _error("Go2 probe input rejected.", str(exc))
        except Exception:
            return _error("Go2 probe input rejected.", "probe_input_error")

        try:
            backend = self._backend_factory(self.model_path)
            backend.reset()
            for _ in range(steps):
                state = backend.sensors()
                current_q = _state_vector(state, "q", 12)
                current_dq = _state_vector(state, "dq", 12)
                controls = tuple(
                    tau[index]
                    + kp[index] * (target_q[index] - current_q[index])
                    + kd[index] * (target_dq[index] - current_dq[index])
                    for index in range(12)
                )
                if not all(math.isfinite(value) for value in controls):
                    raise Go2DevelopmentProbeError("control_value_invalid")
                backend.set_controls(controls)
                backend.step()
            feedback = _ok(
                _observations(
                    backend,
                    probe_id=probe_id,
                    capability_id=capability_id,
                )
            )
        except Exception:
            feedback = _error(
                "Go2 probe execution failed.",
                "probe_execution_error",
                observations={"probe_id": probe_id, "capability_id": capability_id},
            )
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    feedback = _error(
                        "Go2 probe execution failed.",
                        "probe_close_error",
                        observations={"probe_id": probe_id, "capability_id": capability_id},
                    )
        return feedback


Go2DevelopmentSandbox = Go2DevelopmentProbe


def create_go2_development_probe(
    model_path: str | Path,
    *,
    backend_factory: Callable[[str | Path], Any] | None = None,
) -> Go2DevelopmentProbe:
    """Create a callback that owns a fresh backend for each call."""

    return Go2DevelopmentProbe(model_path, backend_factory=backend_factory)


def create_go2_development_sandbox(
    model_path: str | Path,
    *,
    backend_factory: Callable[[str | Path], Any] | None = None,
) -> Go2DevelopmentProbe:
    return create_go2_development_probe(model_path, backend_factory=backend_factory)


go2_development_probe_callback = create_go2_development_probe


__all__ = [
    "GO2_DEVELOPMENT_PROBE_CONTRACT",
    "GO2_JOINT_ORDER",
    "GO2_MAX_STEPS",
    "GO2_MOTOR_ORDER",
    "GO2_OBSERVATION_NAMES",
    "GO2_PROBE_CONTRACT",
    "GO2_PROBE_FIELDS",
    "Go2DevelopmentProbe",
    "Go2DevelopmentProbeError",
    "Go2DevelopmentSandbox",
    "PUBLIC_GO2_DEVELOPMENT_PROBE_CONTRACT",
    "create_go2_development_probe",
    "create_go2_development_sandbox",
    "get_go2_development_probe_contract",
    "go2_development_probe_callback",
]
