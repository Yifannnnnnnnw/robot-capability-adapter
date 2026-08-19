"""Focused checks that the mainline does not resolve project code outside itself."""

from __future__ import annotations

import ast
from pathlib import Path


class SelfContainmentError(ValueError):
    """Raised when a mainline-authored runtime dependency escapes its root."""


_FORBIDDEN_PROJECT_ROOTS = {"demo2", "demo3", "general_demo", "extensions"}
_FORBIDDEN_PATH_MARKERS = tuple(
    prefix + name + suffix
    for prefix, suffix in (("../", ""), ("/", "/"))
    for name in ("demo2", "demo3", "general_demo", "extensions")
)


def check_self_contained(root: str | Path) -> dict[str, int]:
    """Inspect authored Python and symlinks without inventing provenance machinery."""

    mainline_root = Path(root).resolve()
    if not (mainline_root / "pyproject.toml").is_file():
        raise SelfContainmentError(f"not a mainline root: {mainline_root}")

    python_files = 0
    symlinks = 0
    for path in mainline_root.rglob("*"):
        if any(part in {"__pycache__", ".pytest_cache", "runs"} for part in path.parts):
            continue
        if path.is_symlink():
            symlinks += 1
            try:
                path.resolve(strict=True).relative_to(mainline_root)
            except (FileNotFoundError, ValueError) as exc:
                raise SelfContainmentError(f"symlink escapes mainline: {path}") from exc
        if path.suffix != ".py" or not path.is_file():
            continue
        python_files += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise SelfContainmentError(f"cannot inspect {path}: {exc}") from exc
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                modules.append(node.module or "")
            for module in modules:
                if module.split(".", 1)[0] in _FORBIDDEN_PROJECT_ROOTS:
                    raise SelfContainmentError(
                        f"external project import {module!r} in {path.relative_to(mainline_root)}"
                    )
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_PATH_MARKERS:
            if forbidden in source:
                raise SelfContainmentError(
                    f"external project path {forbidden!r} in {path.relative_to(mainline_root)}"
                )

    return {"python_files_checked": python_files, "symlinks_checked": symlinks}


__all__ = ["SelfContainmentError", "check_self_contained"]
