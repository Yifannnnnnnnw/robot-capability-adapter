"""Closed, content-addressed Stage 2 Implementation Bundle."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash


_ARTIFACT_TYPE = "stage2_implementation_bundle"
_SCHEMA_VERSION = "1.0.0"
_FIELDS = {
    "artifact_type",
    "schema_version",
    "sdk_implementation_projection",
    "robot_implementation_facts",
    "implementation_experience",
}
_BUNDLE_TOKEN = object()


def _json_value(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{path} contains a non-string JSON object key")
            _json_value(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"{path} contains a non-finite JSON number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ContractError(f"{path} contains a non-JSON value")


class ImplementationBundle:
    """Framework-validated immutable view of one public Stage 2 Bundle."""

    __slots__ = ("_artifact", "_bundle_hash")

    def __init__(self, token: object, artifact: dict[str, Any], bundle_hash: str):
        if token is not _BUNDLE_TOKEN:
            raise ContractError("ImplementationBundle is Framework-created only")
        self._artifact = artifact
        self._bundle_hash = bundle_hash

    @property
    def artifact(self) -> dict[str, Any]:
        return copy.deepcopy(self._artifact)

    @property
    def bundle_hash(self) -> str:
        return self._bundle_hash


def validate_implementation_bundle(value: Any) -> ImplementationBundle:
    """Validate, deep-copy, and hash one closed JSON Implementation Bundle."""

    if isinstance(value, ImplementationBundle):
        artifact = value.artifact
        expected_hash = value.bundle_hash
    elif isinstance(value, Mapping):
        artifact = copy.deepcopy(dict(value))
        expected_hash = None
    else:
        raise ContractError("Stage 2 requires an Implementation Bundle object")
    if set(artifact) != _FIELDS:
        raise ContractError("Implementation Bundle fields are not closed")
    if artifact.get("artifact_type") != _ARTIFACT_TYPE:
        raise ContractError("Implementation Bundle artifact_type is invalid")
    if artifact.get("schema_version") != _SCHEMA_VERSION:
        raise ContractError("Implementation Bundle schema_version is unsupported")
    if not isinstance(artifact.get("sdk_implementation_projection"), dict):
        raise ContractError("Implementation Bundle sdk_implementation_projection must be an object")
    if not isinstance(artifact.get("robot_implementation_facts"), dict):
        raise ContractError("Implementation Bundle robot_implementation_facts must be an object")
    if not isinstance(artifact.get("implementation_experience"), list):
        raise ContractError("Implementation Bundle implementation_experience must be an array")
    _json_value(artifact, "implementation_bundle")
    bundle_hash = content_hash(canonical_bytes(artifact))
    if expected_hash is not None and expected_hash != bundle_hash:
        raise ContractError("Implementation Bundle content no longer matches its hash")
    return ImplementationBundle(_BUNDLE_TOKEN, artifact, bundle_hash)
