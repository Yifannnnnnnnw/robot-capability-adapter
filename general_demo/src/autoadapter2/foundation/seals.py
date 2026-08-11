from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .canonical import CANONICALIZER_VERSION, canonical_bytes
from .errors import IntegrityError
from .hashing import content_hash, is_content_hash


def create_seal(
    artifact_type: str,
    artifact_hash: str,
    parents: Iterable[str] = (),
    canonicalizer: str = CANONICALIZER_VERSION,
) -> dict[str, Any]:
    if not artifact_type or not is_content_hash(artifact_hash):
        raise IntegrityError("invalid artifact seal inputs")
    parent_list = sorted(set(parents))
    if any(not is_content_hash(parent) for parent in parent_list):
        raise IntegrityError("seal parents must be content hashes")
    body = {
        "seal_version": "1",
        "artifact_type": artifact_type,
        "artifact_hash": artifact_hash,
        "parents": parent_list,
        "canonicalizer": canonicalizer,
    }
    return {**body, "seal_hash": content_hash(canonical_bytes(body))}


def verify_seal(seal: dict[str, Any]) -> bool:
    if not isinstance(seal, dict) or not is_content_hash(seal.get("seal_hash")):
        raise IntegrityError("malformed seal")
    body = {key: value for key, value in seal.items() if key != "seal_hash"}
    if body.get("seal_version") != "1" or not is_content_hash(body.get("artifact_hash")):
        raise IntegrityError("malformed seal body")
    if body.get("parents") != sorted(set(body.get("parents", []))):
        raise IntegrityError("seal parents must be sorted and unique")
    if any(not is_content_hash(parent) for parent in body["parents"]):
        raise IntegrityError("malformed seal parent")
    if content_hash(canonical_bytes(body)) != seal["seal_hash"]:
        raise IntegrityError("seal hash mismatch")
    return True
