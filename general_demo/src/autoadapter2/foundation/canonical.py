from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from typing import Any

from .errors import ContractError

CANONICALIZER_VERSION = "json-canonical-v1"


def _ready(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _ready(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("canonical JSON object keys must be strings")
        return {key: _ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("non-finite numbers are not canonical JSON")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ContractError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")
