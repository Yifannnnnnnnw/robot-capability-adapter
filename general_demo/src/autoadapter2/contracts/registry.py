from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ImmutableError, ReferenceResolutionError
from ..foundation.hashing import content_hash
from ..foundation.identifiers import ExactReference
from .schema_validator import validate_json


@dataclass(frozen=True)
class RegistryEntry:
    kind: str
    id: str
    version: str
    content_hash: str
    status: str
    payload: dict[str, Any]

    @property
    def ref(self) -> ExactReference:
        return ExactReference(self.kind, self.id, self.version, self.content_hash)


class VersionedRegistry:
    def __init__(self, root: str | Path, kind: str):
        self.root = Path(root)
        self.kind = kind

    def publish(
        self,
        id: str,
        version: str,
        payload: dict[str, Any],
        status: str = "FROZEN_FIXTURE",
    ) -> RegistryEntry:
        digest = content_hash(canonical_bytes(payload))
        directory = self.root / id / version
        payload_path = directory / "payload.json"
        manifest_path = directory / "manifest.json"
        manifest = {
            "kind": self.kind,
            "id": id,
            "version": version,
            "content_hash": digest,
            "status": status,
        }
        if directory.exists():
            if not manifest_path.exists() or not payload_path.exists():
                raise ImmutableError("registry version is partially published")
            old_manifest = json.loads(manifest_path.read_text())
            old_payload = json.loads(payload_path.read_text())
            if old_manifest != manifest or content_hash(canonical_bytes(old_payload)) != digest:
                raise ImmutableError("released version cannot be overwritten")
            return RegistryEntry(self.kind, id, version, digest, status, old_payload)
        directory.mkdir(parents=True)
        payload_path.write_bytes(canonical_bytes(payload))
        manifest_path.write_bytes(canonical_bytes(manifest))
        return RegistryEntry(self.kind, id, version, digest, status, payload)

    def resolve(
        self,
        ref: ExactReference | str | dict[str, Any],
        require_frozen: bool = False,
    ) -> RegistryEntry:
        try:
            exact = ExactReference.from_value(ref, expected_kind=self.kind)
            manifest_path = self.root / exact.id / exact.version / "manifest.json"
            payload_path = self.root / exact.id / exact.version / "payload.json"
            manifest = json.loads(manifest_path.read_text())
            payload = json.loads(payload_path.read_text())
        except Exception as exc:
            raise ReferenceResolutionError("exact reference cannot be resolved") from exc
        actual = content_hash(canonical_bytes(payload))
        if actual != exact.content_hash or manifest.get("content_hash") != actual:
            raise ReferenceResolutionError("reference hash does not match payload")
        if manifest.get("kind") != self.kind or manifest.get("id") != exact.id:
            raise ReferenceResolutionError("reference identity mismatch")
        if require_frozen and manifest.get("status") not in {"FROZEN", "FROZEN_FIXTURE"}:
            raise ReferenceResolutionError("reference is not frozen")
        return RegistryEntry(self.kind, exact.id, exact.version, actual, manifest["status"], payload)

    def reference(self, id: str, version: str) -> ExactReference:
        manifest_path = self.root / id / version / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as exc:
            raise ReferenceResolutionError("version is not published") from exc
        return ExactReference(self.kind, id, version, manifest["content_hash"])


class SchemaRegistry(VersionedRegistry):
    def __init__(self, root: str | Path):
        super().__init__(root, "schema")

    def validate(self, ref: ExactReference | str | dict[str, Any], instance: Any) -> Any:
        return validate_json(instance, self.resolve(ref, require_frozen=True).payload)


class ProfileRegistry(VersionedRegistry):
    def __init__(self, root: str | Path):
        super().__init__(root, "profile")


class RecordRegistry(VersionedRegistry):
    pass
