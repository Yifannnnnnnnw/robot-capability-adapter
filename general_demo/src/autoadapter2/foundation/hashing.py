from __future__ import annotations

import hashlib

from .errors import IntegrityError

PREFIX = "sha256:"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(data: bytes) -> str:
    return PREFIX + sha256_bytes(data)


def verify_content_hash(data: bytes, expected: str) -> None:
    actual = content_hash(data)
    if actual != expected:
        raise IntegrityError(f"hash mismatch: expected {expected}, got {actual}")


def is_content_hash(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    digest = value[len(PREFIX):]
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
