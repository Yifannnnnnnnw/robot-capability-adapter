from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ImmutableError, ReferenceResolutionError
from ..foundation.hashing import content_hash
from ..foundation.identifiers import ExactReference, _id, _version
from ..foundation.seals import create_seal, verify_seal
from .fixture_schema_subset import validate_fixture_schema


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
        _id(kind, "registry kind")
        self.root = Path(root)
        self.root_resolved = self.root.resolve()
        self.kind = kind

    def _safe_directory(self, id: str, version: str) -> Path:
        directory = self.root / id / version
        resolved = directory.resolve()
        try:
            resolved.relative_to(self.root_resolved)
        except ValueError as exc:
            raise ImmutableError("registry path escapes its root") from exc
        return directory

    def publish(
        self,
        id: str,
        version: str,
        payload: dict[str, Any],
        status: str = "FROZEN_FIXTURE",
    ) -> RegistryEntry:
        _id(id)
        _version(version)
        if not isinstance(payload, dict):
            raise TypeError("registry payload must be a JSON object")
        digest = content_hash(canonical_bytes(payload))
        directory = self._safe_directory(id, version)
        payload_path = directory / "payload.json"
        manifest_path = directory / "manifest.json"
        freeze_record = {
            "status": status,
            "authority_status": payload.get("authority_status", "OPEN"),
        }
        manifest_core = {
            "kind": self.kind,
            "id": id,
            "version": version,
            "status": status,
            "payload_hash": digest,
            "freeze_record": freeze_record,
        }
        manifest = {
            **manifest_core,
            "manifest_hash": content_hash(canonical_bytes(manifest_core)),
            "seal": create_seal(
                "registry.manifest",
                content_hash(canonical_bytes(manifest_core)),
                parents=[digest],
            ),
        }
        if directory.exists():
            if not manifest_path.exists() or not payload_path.exists():
                raise ImmutableError("registry version is partially published")
            old_manifest = json.loads(manifest_path.read_text())
            old_payload = json.loads(payload_path.read_text())
            if old_manifest != manifest or content_hash(canonical_bytes(old_payload)) != digest:
                raise ImmutableError("released version cannot be overwritten")
            return RegistryEntry(self.kind, id, version, digest, status, old_payload)
        if directory.is_symlink():
            raise ImmutableError("registry version path cannot be a symlink")
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
            directory = self._safe_directory(exact.id, exact.version)
            manifest_path = directory / "manifest.json"
            payload_path = directory / "payload.json"
            manifest = json.loads(manifest_path.read_text())
            payload = json.loads(payload_path.read_text())
        except Exception as exc:
            raise ReferenceResolutionError("exact reference cannot be resolved") from exc
        actual = content_hash(canonical_bytes(payload))
        expected_core = {
            "kind": exact.kind,
            "id": exact.id,
            "version": exact.version,
            "status": manifest.get("status"),
            "payload_hash": actual,
            "freeze_record": manifest.get("freeze_record"),
        }
        expected_manifest_hash = content_hash(canonical_bytes(expected_core))
        if (
            actual != exact.content_hash
            or manifest.get("payload_hash") != actual
            or manifest.get("manifest_hash") != expected_manifest_hash
        ):
            raise ReferenceResolutionError("reference hash does not match payload")
        if (
            manifest.get("kind") != self.kind
            or manifest.get("id") != exact.id
            or manifest.get("version") != exact.version
            or manifest.get("freeze_record", {}).get("status") != manifest.get("status")
        ):
            raise ReferenceResolutionError("reference identity mismatch")
        try:
            verify_seal(manifest["seal"])
        except Exception as exc:
            raise ReferenceResolutionError("registry manifest seal is invalid") from exc
        if manifest["seal"].get("artifact_hash") != manifest["manifest_hash"]:
            raise ReferenceResolutionError("registry manifest seal does not bind manifest")
        if require_frozen and manifest.get("status") not in {"FROZEN", "FROZEN_FIXTURE"}:
            raise ReferenceResolutionError("reference is not frozen")
        return RegistryEntry(self.kind, exact.id, exact.version, actual, manifest["status"], payload)

    def reference(self, id: str, version: str) -> ExactReference:
        _id(id)
        _version(version)
        manifest_path = self._safe_directory(id, version) / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as exc:
            raise ReferenceResolutionError("version is not published") from exc
        return ExactReference(self.kind, id, version, manifest["payload_hash"])


class SchemaRegistry(VersionedRegistry):
    def __init__(self, root: str | Path):
        super().__init__(root, "schema")

    def validate(self, ref: ExactReference | str | dict[str, Any], instance: Any) -> Any:
        return validate_fixture_schema(instance, self.resolve(ref, require_frozen=True).payload)


class ProfileRegistry(VersionedRegistry):
    def __init__(self, root: str | Path):
        super().__init__(root, "profile")


class RecordRegistry(VersionedRegistry):
    pass
