from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import IdentifierError
from .hashing import is_content_hash

ID_PATTERN = re.compile(r"^[a-z][a-z0-9._/-]*$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REF_PATTERN = re.compile(
    r"^(?P<kind>[a-z][a-z0-9._/-]*):(?P<id>[a-z][a-z0-9._/-]*)@"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)#(?P<hash>sha256:[0-9a-f]{64})$"
)


def _id(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise IdentifierError(f"invalid {label}: {value!r}")
    return value


def _version(value: str) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise IdentifierError(f"invalid version: {value!r}")
    return value


@dataclass(frozen=True)
class ExactReference:
    kind: str
    id: str
    version: str
    content_hash: str

    def __post_init__(self) -> None:
        _id(self.kind, "reference kind")
        _id(self.id)
        _version(self.version)
        if not is_content_hash(self.content_hash):
            raise IdentifierError("reference content_hash must be sha256:<hex>")

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}@{self.version}#{self.content_hash}"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "id": self.id,
            "version": self.version,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_value(cls, value: Any, expected_kind: str | None = None) -> "ExactReference":
        if isinstance(value, cls):
            ref = value
        elif isinstance(value, dict):
            digest = value.get("content_hash", value.get("hash"))
            if value.get("kind") is None or value.get("id") is None:
                raise IdentifierError("exact reference requires kind and id")
            if value.get("version") is None or digest is None:
                raise IdentifierError("exact reference requires version and content_hash")
            ref = cls(value["kind"], value["id"], value["version"], digest)
        elif isinstance(value, str):
            match = REF_PATTERN.fullmatch(value)
            if not match:
                raise IdentifierError("malformed exact reference")
            ref = cls(match["kind"], match["id"], match["version"], match["hash"])
        else:
            raise IdentifierError("reference must be an object, string, or ExactReference")
        if expected_kind is not None and ref.kind != expected_kind:
            raise IdentifierError(f"expected {expected_kind}, got {ref.kind}")
        return ref
