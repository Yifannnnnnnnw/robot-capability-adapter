"""Frozen experiment policy for manipulation-position Blue Line standards.

The migrated task packages previously carried 40--60 mm positional tolerances
without a derivation. Demo2 rejects those values instead of silently copying
them into an evaluation suite.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping


POSITION_PRECISION_POLICY = {
    "policy_id": "demo2-manipulation-position-v1",
    "basis": "user-approved experiment protocol",
    "ik_reference_tolerance_m": 0.001,
    "maximum_physical_position_error_m": 0.01,
    "minimum_terminal_dwell_s": 0.5,
    "candidate_performance_may_relax_standard": False,
}

# Operators that return a Euclidean position error in metres. Success-side
# lower bounds such as lift height are deliberately not included.
POSITION_ERROR_OPERATORS = frozenset({
    "distance",
    "history_waypoint_max_error",
    "history_final_return_error",
    "offset_error",
})

# Terminal-state errors must hold over a window rather than at one sample.
TERMINAL_DWELL_OPERATORS = frozenset({
    "distance",
    "history_final_return_error",
})


def policy_record() -> dict[str, Any]:
    return copy.deepcopy(POSITION_PRECISION_POLICY)


def _validate_position_standard(
    *,
    operator: Any,
    comparator: Any,
    threshold_value: Any,
    dwell_value: Any,
) -> None:
    """Validate one normalized physical-position criterion."""

    if operator not in POSITION_ERROR_OPERATORS or comparator not in {"<", "<="}:
        return
    try:
        threshold = float(threshold_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("position-error threshold must be numeric metres") from exc
    cap = float(POSITION_PRECISION_POLICY["maximum_physical_position_error_m"])
    if not math.isfinite(threshold) or threshold <= 0.0 or threshold > cap:
        raise ValueError(
            f"position-error threshold {threshold!r} exceeds "
            f"{POSITION_PRECISION_POLICY['policy_id']} cap {cap} m"
        )
    if operator in TERMINAL_DWELL_OPERATORS:
        try:
            dwell = float(dwell_value or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("terminal position dwell must be numeric seconds") from exc
        minimum = float(POSITION_PRECISION_POLICY["minimum_terminal_dwell_s"])
        if not math.isfinite(dwell) or dwell < minimum:
            raise ValueError(
                f"terminal position standard requires continuous_dwell_s >= {minimum}"
            )


def validate_position_standard(
    check: Mapping[str, Any],
    measurement: Mapping[str, Any],
    *,
    dwell_s: Any,
) -> None:
    """Reject broad or non-dwelling source Tasks Library standards."""

    _validate_position_standard(
        operator=measurement.get("operator"),
        comparator=check.get("comparator"),
        threshold_value=check.get("value"),
        dwell_value=dwell_s,
    )


def validate_compiled_position_standard(criterion: Mapping[str, Any]) -> None:
    """Apply the same policy again to the frozen suite consumed by validation."""

    measurement = criterion.get("measurement")
    if not isinstance(measurement, Mapping):
        return
    _validate_position_standard(
        operator=measurement.get("operator"),
        comparator=criterion.get("comparator"),
        threshold_value=criterion.get("threshold"),
        dwell_value=criterion.get("dwell_s"),
    )
