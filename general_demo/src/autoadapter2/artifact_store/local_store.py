from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ArtifactNotFoundError, ImmutableError
from ..foundation.hashing import content_hash, is_content_hash, verify_content_hash


@dataclass(frozen=True)
class StoredArtifact:
    content_hash: str
    size: int
    media_type: str


class LocalArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.objects = self.root / "sha256"

    def _path(self, digest: str) -> Path:
        if not is_content_hash(digest):
            raise ValueError("artifact reference must be sha256:<hex>")
        value = digest.removeprefix("sha256:")
        path = self.objects / value[:2] / value
        try:
            path.resolve().relative_to(self.objects.resolve())
        except ValueError as exc:
            raise ValueError("artifact path escapes its root") from exc
        return path

    def put_bytes(self, data: bytes, media_type: str = "application/octet-stream") -> StoredArtifact:
        digest = content_hash(data)
        path = self._path(digest)
        if path.is_symlink():
            raise ImmutableError("content-addressed object cannot be a symlink")
        if path.exists():
            if path.read_bytes() != data:
                raise ImmutableError("content-addressed object was changed")
            return StoredArtifact(digest, len(data), media_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return StoredArtifact(digest, len(data), media_type)

    def put_json(self, value: Any) -> StoredArtifact:
        return self.put_bytes(canonical_bytes(value), "application/json")

    def get_bytes(self, digest: str) -> bytes:
        path = self._path(digest)
        if not path.exists():
            raise ArtifactNotFoundError(digest)
        if path.is_symlink():
            raise ImmutableError("content-addressed object cannot be a symlink")
        data = path.read_bytes()
        verify_content_hash(data, digest)
        return data

    def get_json(self, digest: str) -> Any:
        import json

        return json.loads(self.get_bytes(digest))
