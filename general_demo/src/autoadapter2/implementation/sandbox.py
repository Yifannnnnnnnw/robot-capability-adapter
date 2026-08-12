"""Callback-only, public-feedback development sandbox boundary.

The demo does not implement simulation here.  A caller may supply a callback
that performs its own admitted work; this boundary returns only a small,
sanitized public feedback projection to Stage 2.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from ..foundation.errors import ContractError


SandboxCallback = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

_FORBIDDEN_TERMS = (
    "private", "criterion", "validation", "blue line", "suite", "threshold",
    "mujoco", "translation", "simulator", "raw_state", "score", "target_error",
    "truth", "video", "verdict",
)
_ALLOWED_FEEDBACK_FIELDS = {"status", "summary", "observations", "exception"}

_SANDBOX_CONTRACT = {
    "artifact_type": "stage2_public_sandbox_contract",
    "schema_version": "1.0.0",
    "execution": {
        "mode": "callback_only",
        "direct_handle_access": False,
        "candidate_input": "complete capability.py source",
        "probe_input": "public JSON object",
    },
    "probe": {
        "coverage_identity_fields": ["probe_id", "capability_id"],
        "coverage_identity_rule": "both fields must be exact non-empty strings",
        "use_varied_public_probes": True,
    },
    "feedback": {
        "fields": ["status", "summary", "observations", "exception"],
        "status_values": ["OK", "ERROR", "INCONCLUSIVE"],
        "projection": "bounded public execution feedback",
    },
}


def get_sandbox_contract() -> dict[str, Any]:
    """Return the closed public contract without exposing callback state."""

    return copy.deepcopy(_SANDBOX_CONTRACT)


def _public_value(value: Any, location: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"Sandbox feedback {location} contains a non-string key")
            if any(term in str(key).lower() for term in _FORBIDDEN_TERMS):
                raise ContractError(f"Sandbox feedback {location}.{key} is not public")
            _public_value(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _public_value(item, f"{location}[{index}]")
    elif isinstance(value, str) and any(term in value.lower() for term in _FORBIDDEN_TERMS):
        raise ContractError(f"Sandbox feedback {location} is not public")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ContractError(f"Sandbox feedback {location} is not JSON-compatible")


def _error_feedback(summary: str, exception: str) -> dict[str, Any]:
    return {"status": "ERROR", "summary": summary, "observations": {}, "exception": exception}


class CallbackSandbox:
    """An injected callback, not a simulator, SDK, or private evaluation harness."""

    def __init__(
        self,
        callback: SandboxCallback,
        contract: Mapping[str, Any] | None = None,
    ):
        if not callable(callback):
            raise ContractError("Sandbox requires a callback")
        self._callback = callback
        if contract is None:
            self._contract = get_sandbox_contract()
        else:
            if not isinstance(contract, Mapping) or not contract:
                raise ContractError("Sandbox contract must be a non-empty public object")
            try:
                _public_value(contract, "contract")
            except ContractError as exc:
                raise ContractError("Sandbox contract is not public JSON") from exc
            self._contract = get_sandbox_contract()
            self._contract["robot_contract"] = copy.deepcopy(dict(contract))

    @property
    def contract(self) -> dict[str, Any]:
        """Return the closed public contract as an isolated value."""

        return copy.deepcopy(self._contract)

    def run(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(capability_source, str) or not capability_source.strip():
            raise ContractError("Sandbox requires a non-empty working capability.py")
        if not isinstance(probe, Mapping):
            raise ContractError("Sandbox probe must be a public object")
        try:
            _public_value(probe, "probe")
            raw = self._callback(capability_source, copy.deepcopy(dict(probe)))
        except Exception:
            return _error_feedback("Sandbox callback failed.", "sandbox_callback_error")
        if not isinstance(raw, Mapping):
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        feedback = dict(raw)
        if set(feedback) - _ALLOWED_FEEDBACK_FIELDS:
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        if feedback.get("status") not in {"OK", "ERROR", "INCONCLUSIVE"} or not isinstance(feedback.get("summary"), str) or not feedback["summary"].strip():
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        if "observations" in feedback and not isinstance(feedback["observations"], Mapping):
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        if "exception" in feedback and feedback["exception"] is not None and not isinstance(feedback["exception"], str):
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        try:
            _public_value(feedback)
        except ContractError:
            return _error_feedback("Sandbox callback returned invalid public feedback.", "sandbox_feedback_contract_error")
        return {
            "status": feedback["status"],
            "summary": feedback["summary"],
            "observations": copy.deepcopy(dict(feedback.get("observations", {}))),
            "exception": feedback.get("exception"),
        }
