"""Source-coupled development sandbox for the direct MuJoCo adapter."""

from __future__ import annotations

import ast
import copy
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .config import DirectMuJoCoConfigurationError, DirectMuJoCoLibraryConfig, load_morphology_record
from .session import DirectMuJoCoEvaluationRobotSession, DirectMuJoCoFacade


DIRECT_MUJOCO_EXPERIMENTAL_CONTRACT: dict[str, Any] = {
    "contract_id": "direct-mujoco-experimental-development-sandbox",
    "version": "1.0.0",
    "mode": "source_coupled_direct_physics",
    "status": "EXPERIMENTAL",
    "capability_source": {
        "syntax_check": True,
        "execution": True,
        "function_rule": "capability_<capability_id>(..., _sdk=DirectMuJoCoFacade)",
        "max_chars": 200_000,
    },
    "probe_fields": ["probe_id", "capability_id", "arguments", "horizon_s"],
    "facade": {
        "name": "DirectMuJoCoFacade",
        "official_sdk": False,
        "methods": ["actuator_names", "joint_names", "send_action", "state", "get_observation", "step"],
    },
    "observations": ["simulation_time_s", "actuators", "joints", "bodies", "sites", "sensors"],
}

_MAX_SOURCE_CHARS = 200_000
_MAX_HORIZON_S = 10.0
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


class DirectMuJoCoDevelopmentSandboxError(ValueError):
    """Invalid public probe or source input for the experimental sandbox."""


def get_direct_mujoco_experimental_contract() -> dict[str, Any]:
    return copy.deepcopy(DIRECT_MUJOCO_EXPERIMENTAL_CONTRACT)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DirectMuJoCoDevelopmentSandboxError(f"{label}_invalid")
    return value


def _parse_probe(probe: Mapping[str, Any]) -> tuple[str, str, dict[str, Any], float]:
    if not isinstance(probe, Mapping) or set(probe) != {
        "probe_id",
        "capability_id",
        "arguments",
        "horizon_s",
    }:
        raise DirectMuJoCoDevelopmentSandboxError("probe_fields_invalid")
    probe_id = _identifier(probe["probe_id"], "probe_id")
    capability_id = _identifier(probe["capability_id"], "capability_id")
    arguments = probe["arguments"]
    if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
        raise DirectMuJoCoDevelopmentSandboxError("arguments_invalid")
    if "_sdk" in arguments:
        raise DirectMuJoCoDevelopmentSandboxError("arguments_invalid")
    horizon = probe["horizon_s"]
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)):
        raise DirectMuJoCoDevelopmentSandboxError("horizon_s_invalid")
    horizon_s = float(horizon)
    if not math.isfinite(horizon_s) or not 0.0 < horizon_s <= _MAX_HORIZON_S:
        raise DirectMuJoCoDevelopmentSandboxError("horizon_s_invalid")
    return probe_id, capability_id, copy.deepcopy(dict(arguments)), horizon_s


def _parse_source(source: Any) -> tuple[object, str]:
    if not isinstance(source, str) or not source.strip():
        raise DirectMuJoCoDevelopmentSandboxError("source_required")
    if len(source.encode("utf-8")) > _MAX_SOURCE_CHARS:
        raise DirectMuJoCoDevelopmentSandboxError("source_too_large")
    try:
        tree = ast.parse(source, filename="<capability.py>", mode="exec")
        return compile(tree, "<capability.py>", "exec"), source
    except (SyntaxError, TypeError, ValueError, UnicodeError) as exc:
        raise DirectMuJoCoDevelopmentSandboxError("source_syntax_error") from exc


def _feedback(
    status: str,
    summary: str,
    observations: Mapping[str, Any],
    exception: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "observations": copy.deepcopy(dict(observations)),
        "exception": exception,
    }


class DirectMuJoCoDevelopmentSandbox:
    """Execute candidate source against a fresh facade-owned physics session.

    The sandbox never writes actuator controls itself.  The submitted source
    must call ``DirectMuJoCoFacade.send_action`` and ``step``; only the facade
    performs those operations on behalf of the candidate.
    """

    contract = DIRECT_MUJOCO_EXPERIMENTAL_CONTRACT

    def __init__(
        self,
        morphology_record: Mapping[str, Any] | str | Path | DirectMuJoCoLibraryConfig,
        *,
        asset_root: str | Path | None = None,
        session_factory: Callable[..., DirectMuJoCoEvaluationRobotSession] | None = None,
    ) -> None:
        if isinstance(morphology_record, DirectMuJoCoLibraryConfig):
            self._config = morphology_record
        else:
            record, record_path = load_morphology_record(morphology_record)
            resolved_root = asset_root if asset_root is not None else (
                record_path.parent if record_path is not None else None
            )
            if resolved_root is None:
                raise DirectMuJoCoConfigurationError(
                    "asset_root is required when morphology_record is an in-memory record"
                )
            self._config = DirectMuJoCoLibraryConfig.from_record(record, asset_root=resolved_root)
        self._session_factory = session_factory or DirectMuJoCoEvaluationRobotSession

    def __call__(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        return self.run(capability_source, probe)

    def run(self, capability_source: str, probe: Mapping[str, Any]) -> dict[str, Any]:
        session: DirectMuJoCoEvaluationRobotSession | None = None
        try:
            code, _source = _parse_source(capability_source)
            probe_id, capability_id, arguments, horizon_s = _parse_probe(probe)
        except DirectMuJoCoDevelopmentSandboxError as exc:
            return _feedback("ERROR", "Direct physics probe input rejected.", {}, str(exc))
        try:
            session = self._session_factory(self._config)
            namespace: dict[str, Any] = {
                "__builtins__": dict(_SAFE_BUILTINS),
                "__name__": "__direct_mujoco_capability__",
                "math": math,
            }
            exec(code, namespace, namespace)
            function = namespace.get(f"capability_{capability_id}")
            if not callable(function):
                raise DirectMuJoCoDevelopmentSandboxError(
                    f"capability_{capability_id} is not defined"
                )
            before = session.get_observation()
            function(**arguments, _sdk=session.sdk)
            after = session.get_observation()
            status = "OK" if session.accepted_action_count and session.physics_step_count else "INCONCLUSIVE"
            return _feedback(
                status,
                "Direct physics probe completed experimentally.",
                {
                    "probe_id": probe_id,
                    "capability_id": capability_id,
                    "requested_horizon_s": horizon_s,
                    "accepted_action_count": session.accepted_action_count,
                    "physics_step_count": session.physics_step_count,
                    "simulation_time_s": session.simulation_time_s,
                    "state_changed": before != after,
                    "observation": after,
                },
            )
        except Exception as exc:
            return _feedback(
                "ERROR",
                "Direct physics probe execution failed.",
                {"probe_id": probe_id, "capability_id": capability_id},
                "probe_execution_error",
            )
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


DirectMuJoCoDevelopmentProbe = DirectMuJoCoDevelopmentSandbox
DirectMuJoCoSandbox = DirectMuJoCoDevelopmentSandbox


def create_direct_mujoco_development_sandbox(
    morphology_record: Mapping[str, Any] | str | Path | DirectMuJoCoLibraryConfig,
    *,
    asset_root: str | Path | None = None,
    session_factory: Callable[..., DirectMuJoCoEvaluationRobotSession] | None = None,
) -> DirectMuJoCoDevelopmentSandbox:
    return DirectMuJoCoDevelopmentSandbox(
        morphology_record,
        asset_root=asset_root,
        session_factory=session_factory,
    )


create_direct_mujoco_development_probe = create_direct_mujoco_development_sandbox
direct_mujoco_development_probe_callback = create_direct_mujoco_development_sandbox


__all__ = [
    "DIRECT_MUJOCO_EXPERIMENTAL_CONTRACT",
    "DirectMuJoCoDevelopmentProbe",
    "DirectMuJoCoDevelopmentSandbox",
    "DirectMuJoCoDevelopmentSandboxError",
    "DirectMuJoCoSandbox",
    "create_direct_mujoco_development_probe",
    "create_direct_mujoco_development_sandbox",
    "direct_mujoco_development_probe_callback",
    "get_direct_mujoco_experimental_contract",
]
