"""Trusted mapping from persistent-worker messages to ReCAP observations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class B2WorkerProtocolError(RuntimeError):
    """Raised when a worker message violates the fixed B2 public protocol."""


def initial_public_state(response: Mapping[str, Any]) -> dict[str, Any]:
    if response.get("type") != "ready":
        raise B2WorkerProtocolError("worker did not send ready")
    return _public_state(response)


def public_observation(response: Mapping[str, Any]) -> dict[str, Any]:
    """Select only the fixed public envelope from one worker response."""

    if response.get("type") != "observation":
        raise B2WorkerProtocolError("worker did not send an observation")
    state = _public_state(response)
    if response.get("ok") is True:
        operation = {
            "status": "EXECUTED",
            "steps_added": _nonnegative_integer(response.get("steps_added")),
            "total_sim_steps": _nonnegative_integer(
                response.get("sim_step_count")
            ),
        }
    else:
        raw_error = response.get("error")
        code = raw_error.get("code") if isinstance(raw_error, Mapping) else None
        error_code = code if isinstance(code, str) and code else "WORKER_ERROR"
        operation = {
            "status": (
                "ABORT" if error_code == "SESSION_BUDGET_EXCEEDED" else "ERROR"
            ),
            "error_code": error_code,
            "total_sim_steps": _nonnegative_integer(
                response.get("sim_step_count", 0)
            ),
        }
    return {"operation": operation, "public_state": state}


def abort_observation(*, error_code: str) -> dict[str, Any]:
    if not isinstance(error_code, str) or not error_code:
        raise B2WorkerProtocolError("abort error_code must be non-empty")
    return {"operation": {"status": "ABORT", "error_code": error_code}}


def _public_state(response: Mapping[str, Any]) -> dict[str, Any]:
    value = response.get("public_state")
    if not isinstance(value, Mapping):
        raise B2WorkerProtocolError("worker response has no public_state object")
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise B2WorkerProtocolError("worker public_state is not finite JSON") from exc
    if not isinstance(copied, dict):
        raise B2WorkerProtocolError("worker public_state must be an object")
    return copied


def _nonnegative_integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise B2WorkerProtocolError("worker step count must be nonnegative")
    return value


__all__ = [
    "B2WorkerProtocolError",
    "abort_observation",
    "initial_public_state",
    "public_observation",
]
