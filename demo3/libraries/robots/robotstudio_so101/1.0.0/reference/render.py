"""Small render configuration helper for Framework-owned video capture."""

from __future__ import annotations


def default_render_config() -> dict[str, object]:
    """Return stable dimensions accepted by the shared Harness renderer."""

    return {
        "enabled": True,
        "width": 160,
        "height": 120,
        "fps": 10.0,
        "camera": -1,
    }
