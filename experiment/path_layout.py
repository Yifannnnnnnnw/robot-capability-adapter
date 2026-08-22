"""Resolve run paths across the experiment-directory migration.

Historical run records are intentionally immutable.  This module only maps a
missing path under one of the former active run roots to its read-only archive
location; it never edits a record or creates a compatibility directory.
"""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

_ARCHIVE_PREFIXES = (
    (Path("experiment/experiment1/runs"), Path("experiment/archive/runs/experiment1")),
    (Path("experiment/b2_recap/runs"), Path("experiment/archive/runs/b2_recap")),
)


def resolve_run_path(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    """Return ``path`` or its archive equivalent when an old run path is missing.

    Relative paths are resolved for existence checks from ``repository_root``
    (the current repository by default) but are returned in their original
    relative form.  Absolute paths are returned as absolute paths.  Paths
    outside the repository and paths that already exist are left unchanged.
    """

    original = Path(path)
    root = Path(repository_root) if repository_root is not None else REPOSITORY_ROOT
    root = root.resolve()
    absolute = original if original.is_absolute() else root / original

    if absolute.exists():
        return original

    try:
        relative = absolute.resolve(strict=False).relative_to(root)
    except ValueError:
        return original

    for old_prefix, archive_prefix in _ARCHIVE_PREFIXES:
        try:
            suffix = relative.relative_to(old_prefix)
        except ValueError:
            continue
        mapped = root / archive_prefix / suffix
        return mapped if original.is_absolute() else archive_prefix / suffix

    return original


__all__ = ["REPOSITORY_ROOT", "resolve_run_path"]
