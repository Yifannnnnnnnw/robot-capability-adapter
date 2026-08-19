"""Capability-neutral contract for skeleton-assisted Driver Synthesis."""

from __future__ import annotations

import inspect
import keyword
from dataclasses import dataclass
from typing import Any, Iterable


INFRASTRUCTURE_NAMES = frozenset(
    {
        "build",
        "close",
        "data",
        "describe",
        "from_session",
        "model",
        "render",
        "settle",
        "spec",
        "step",
    }
)


class SkeletonContractError(ValueError):
    """Raised when a generated driver violates the skeleton boundary."""


@dataclass(frozen=True)
class PrimitiveDescription:
    """One trusted skeleton operation available to generated driver code."""

    name: str
    signature: str
    summary: str


class SessionBoundSkeleton:
    """Base contract for a skeleton attached to a Framework-owned session."""

    def __init__(self, *, model: Any, data: Any, spec: Any) -> None:
        if model is None or data is None:
            raise SkeletonContractError("canonical model and data are required")
        self.model = model
        self.data = data
        self.spec = spec

    @classmethod
    def from_session(cls, *, model: Any, data: Any, spec: Any) -> SessionBoundSkeleton:
        """Construct without loading or replacing the canonical MuJoCo model."""

        return cls(model=model, data=data, spec=spec)


def discover_primitives(skeleton_type: type[Any]) -> tuple[PrimitiveDescription, ...]:
    """Reflect reusable operations without declaring final capability names."""

    primitives: list[PrimitiveDescription] = []
    for name, member in inspect.getmembers(skeleton_type):
        if name.startswith("_") or name in INFRASTRUCTURE_NAMES or not callable(member):
            continue
        try:
            signature = str(inspect.signature(member))
        except (TypeError, ValueError):
            continue
        doc = inspect.getdoc(member) or ""
        summary = doc.splitlines()[0] if doc else ""
        primitives.append(
            PrimitiveDescription(name=name, signature=signature, summary=summary)
        )
    return tuple(primitives)


def validate_capability_names(capability_names: Iterable[str]) -> tuple[str, ...]:
    """Validate TGCD-authored names before Driver Synthesis starts."""

    names = tuple(capability_names)
    if not names:
        raise SkeletonContractError("at least one capability method is required")
    if len(names) != len(set(names)):
        raise SkeletonContractError("capability method names must be unique")
    for name in names:
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
            raise SkeletonContractError(f"invalid Python capability method name: {name!r}")
        if name.startswith("_") or name in INFRASTRUCTURE_NAMES:
            raise SkeletonContractError(f"reserved capability method name: {name!r}")
    return names


def validate_explicit_capability_methods(
    driver_type: type[Any],
    capability_names: Iterable[str],
) -> tuple[PrimitiveDescription, ...]:
    """Require every sealed-design method directly on the generated driver."""

    methods: list[PrimitiveDescription] = []
    for name in validate_capability_names(capability_names):
        member = driver_type.__dict__.get(name)
        if member is None:
            raise SkeletonContractError(
                f"generated driver must explicitly define capability method {name!r}"
            )
        if not inspect.isfunction(member):
            raise SkeletonContractError(
                f"generated capability {name!r} must be an instance method"
            )
        signature = inspect.signature(member)
        parameters = tuple(signature.parameters.values())
        if not parameters or parameters[0].name != "self":
            raise SkeletonContractError(
                f"generated capability {name!r} must start with self"
            )
        doc = inspect.getdoc(member) or ""
        methods.append(
            PrimitiveDescription(
                name=name,
                signature=str(signature),
                summary=doc.splitlines()[0] if doc else "",
            )
        )
    return tuple(methods)
