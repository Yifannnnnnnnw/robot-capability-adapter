"""Fail-closed loader for the shared DIRECT_MUJOCO_EXPERIMENTAL SDK sentinel."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH = Path(
    "general_demo/libraries/sdks/no-sdk-direct-mujoco/1.0.0/record.json"
)
NO_SDK_DIRECT_MUJOCO_RECORD_ID = "no-sdk-direct-mujoco"
NO_SDK_DIRECT_MUJOCO_RECORD_VERSION = "1.0.0"
NO_SDK_DIRECT_MUJOCO_EXECUTION_MODE = "DIRECT_MUJOCO_EXPERIMENTAL"
NO_SDK_DIRECT_MUJOCO_SDK_STATUS = "NOT_APPLICABLE"
NO_SDK_DIRECT_MUJOCO_TRANSPORT = "NONE"

_EMPTY_SURFACE_FIELDS = (
    "public_symbols",
    "operations",
    "action_fields",
    "observation_fields",
    "packages",
    "dependencies",
)
_EXPECTED_KEYS = frozenset(
    {
        "record_type",
        "id",
        "version",
        "execution_mode",
        "sdk_status",
        "transport",
        *_EMPTY_SURFACE_FIELDS,
    }
)


class NoSDKDirectMuJoCoRecordError(ValueError):
    """The shared no-SDK sentinel is missing or violates its fixed contract."""


@dataclass(frozen=True)
class NoSDKDirectMuJoCoRecord:
    """One validated, configuration-neutral SDK Library sentinel."""

    record_path: Path
    record: Mapping[str, Any]


def validate_no_sdk_direct_mujoco_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the exact shared sentinel shape and return a detached mapping."""

    if not isinstance(value, Mapping):
        raise NoSDKDirectMuJoCoRecordError("no-SDK Direct-MuJoCo record must be an object")
    actual_keys = set(value)
    if actual_keys != _EXPECTED_KEYS:
        extra = sorted(actual_keys - _EXPECTED_KEYS)
        missing = sorted(_EXPECTED_KEYS - actual_keys)
        raise NoSDKDirectMuJoCoRecordError(
            "no-SDK Direct-MuJoCo record fields are not exact "
            f"(extra={extra}, missing={missing})"
        )

    expected_scalars = {
        "record_type": "sdk",
        "id": NO_SDK_DIRECT_MUJOCO_RECORD_ID,
        "version": NO_SDK_DIRECT_MUJOCO_RECORD_VERSION,
        "execution_mode": NO_SDK_DIRECT_MUJOCO_EXECUTION_MODE,
        "sdk_status": NO_SDK_DIRECT_MUJOCO_SDK_STATUS,
        "transport": NO_SDK_DIRECT_MUJOCO_TRANSPORT,
    }
    for field, expected in expected_scalars.items():
        if value[field] != expected:
            raise NoSDKDirectMuJoCoRecordError(
                f"no-SDK Direct-MuJoCo record {field} must be {expected!r}"
            )

    for field in _EMPTY_SURFACE_FIELDS:
        if not isinstance(value[field], list) or value[field] != []:
            raise NoSDKDirectMuJoCoRecordError(
                f"no-SDK Direct-MuJoCo record {field} must be an empty array"
            )
    return dict(value)


def load_no_sdk_direct_mujoco_record(
    repo_root: str | Path,
) -> NoSDKDirectMuJoCoRecord:
    """Load exactly the shared sentinel path beneath one repository root."""

    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        raise NoSDKDirectMuJoCoRecordError(f"repository root is not a directory: {root}")
    record_path = root / NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH
    if record_path.is_symlink() or not record_path.is_file():
        raise NoSDKDirectMuJoCoRecordError(
            "shared no-SDK Direct-MuJoCo record is missing at the exact path: "
            f"{record_path}"
        )
    if record_path.resolve() != record_path:
        raise NoSDKDirectMuJoCoRecordError(
            "shared no-SDK Direct-MuJoCo record path must not traverse a symlink: "
            f"{record_path}"
        )
    try:
        value = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NoSDKDirectMuJoCoRecordError(
            f"could not read shared no-SDK Direct-MuJoCo record: {record_path}"
        ) from exc
    return NoSDKDirectMuJoCoRecord(
        record_path=record_path,
        record=validate_no_sdk_direct_mujoco_record(value),
    )


__all__ = [
    "NO_SDK_DIRECT_MUJOCO_EXECUTION_MODE",
    "NO_SDK_DIRECT_MUJOCO_RECORD_ID",
    "NO_SDK_DIRECT_MUJOCO_RECORD_RELATIVE_PATH",
    "NO_SDK_DIRECT_MUJOCO_RECORD_VERSION",
    "NO_SDK_DIRECT_MUJOCO_SDK_STATUS",
    "NO_SDK_DIRECT_MUJOCO_TRANSPORT",
    "NoSDKDirectMuJoCoRecord",
    "NoSDKDirectMuJoCoRecordError",
    "load_no_sdk_direct_mujoco_record",
    "validate_no_sdk_direct_mujoco_record",
]
