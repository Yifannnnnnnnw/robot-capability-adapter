"""Frozen low-level Unitree Go2 integration exports.

The bridge remains importable for readiness-only processes without importing
the session's MuJoCo renderer, SDK2 endpoint owner, or candidate binding.  The
session module is loaded only when one of its public names is requested.
"""

from importlib import import_module

from .bridge import Go2DDSMuJoCoBridge, Go2Transport, MuJoCoGo2Backend, UnitreeSDK2Transport
from .development_sandbox import (
    GO2_DEVELOPMENT_PROBE_CONTRACT,
    Go2DevelopmentProbe,
    create_go2_development_probe,
)


_SESSION_EXPORTS = frozenset(
    {
        "Go2EvaluationRobotSession",
        "Go2SessionError",
        "Go2SDKError",
        "Go2TruthSample",
        "Go2ValidationEvidence",
        "create_evaluation_robot_session",
        "UnitreeGo2EvaluationRobotSession",
        "UnitreeGo2EvaluationSession",
        "initial_body_yaw_frame",
        "start_frame_displacement",
        "upright_score",
    }
)


def __getattr__(name: str):
    if name not in _SESSION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(".session", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _SESSION_EXPORTS)


__all__ = [
    "Go2DDSMuJoCoBridge",
    "GO2_DEVELOPMENT_PROBE_CONTRACT",
    "Go2DevelopmentProbe",
    "Go2EvaluationRobotSession",
    "Go2SessionError",
    "Go2SDKError",
    "Go2TruthSample",
    "Go2ValidationEvidence",
    "Go2Transport",
    "create_evaluation_robot_session",
    "create_go2_development_probe",
    "MuJoCoGo2Backend",
    "UnitreeGo2EvaluationRobotSession",
    "UnitreeGo2EvaluationSession",
    "UnitreeSDK2Transport",
    "initial_body_yaw_frame",
    "start_frame_displacement",
    "upright_score",
]
