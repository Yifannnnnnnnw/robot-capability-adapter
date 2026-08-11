from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..foundation.errors import VisibilityError


class VisibilityGuard:
    def __init__(self, allowlists: Mapping[str, Iterable[str]]):
        self.allowlists = {
            recipient: frozenset(paths) for recipient, paths in allowlists.items()
        }

    @staticmethod
    def _matches(allowed: str, requested: str) -> bool:
        return (
            requested == allowed
            or (allowed.endswith(".*") and requested.startswith(allowed[:-1]))
        )

    def check(self, recipient: str, paths: Iterable[str]) -> None:
        allowed = self.allowlists.get(recipient, frozenset())
        denied = [
            path for path in paths
            if not any(self._matches(item, path) for item in allowed)
        ]
        if denied:
            raise VisibilityError(f"visibility denied for {recipient}: {denied}")

    def assert_allowed(self, recipient: str, path: str) -> None:
        self.check(recipient, [path])
