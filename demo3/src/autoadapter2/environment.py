"""Direct runtime checks for the declared mainline environment."""

from __future__ import annotations

import shutil
import sys
from typing import Any


class EnvironmentError(RuntimeError):
    """Raised when the declared experiment runtime is unavailable."""


def check_environment() -> dict[str, Any]:
    """Fail early on the exact Python, MuJoCo, NumPy, and video runtime."""

    if sys.version_info[:2] != (3, 11):
        raise EnvironmentError(
            f"the mainline requires Python 3.11; found {sys.version_info.major}.{sys.version_info.minor}"
        )
    try:
        import mujoco
        import numpy
    except Exception as exc:
        raise EnvironmentError(f"cannot import declared runtime: {type(exc).__name__}: {exc}") from exc
    expected = {"mujoco": "3.3.6", "numpy": "2.4.6"}
    actual = {"mujoco": mujoco.__version__, "numpy": numpy.__version__}
    for name, version in expected.items():
        if actual[name] != version:
            raise EnvironmentError(
                f"the mainline requires {name}=={version}; found {actual[name]}"
            )
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise EnvironmentError("missing required video executable: " + ", ".join(missing))
    try:
        model = mujoco.MjModel.from_xml_string('<mujoco model="environment-check"/>')
        data = mujoco.MjData(model)
        mujoco.mj_step(model, data)
    except Exception as exc:
        raise EnvironmentError(f"MuJoCo physics smoke failed: {type(exc).__name__}: {exc}") from exc
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "mujoco": actual["mujoco"],
        "numpy": actual["numpy"],
        "ffmpeg": tools["ffmpeg"],
        "ffprobe": tools["ffprobe"],
        "mujoco_physics_smoke": True,
    }


__all__ = ["EnvironmentError", "check_environment"]
