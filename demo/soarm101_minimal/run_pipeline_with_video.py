#!/usr/bin/env python3
"""Launch the AWS MuJoCo pipeline from the dedicated venv.

On macOS the process needs a usable CoreGraphics session for offscreen
``mujoco.Renderer`` output, but it does not need ``mjpython`` (that trampoline
is for MuJoCo's passive viewer).  This launcher marks that explicit runtime
boundary and keeps every platform on the same venv Python.  It never reads the
ignored API credential; the child pipeline owns that step.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def graphics_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment["SOARM_MUJOCO_VIDEO_LAUNCHER"] = "1"
    if sys.platform != "darwin":
        environment.setdefault("MUJOCO_GL", "egl")
    return environment


def launch(argv: Sequence[str]) -> None:
    arguments = list(argv)
    try:
        mode_index = arguments.index("--mode")
        selected_mode = arguments[mode_index + 1]
    except (ValueError, IndexError):
        selected_mode = None
    if selected_mode != "aws":
        raise SystemExit("video launcher requires the explicit arguments: --mode aws")
    interpreter = VENV / "bin/python"
    if not interpreter.is_file():
        raise SystemExit("create the dedicated .venv before launching the video run")
    command = [str(interpreter), str(ROOT / "run_pipeline.py"), *arguments]
    os.execve(str(interpreter), command, graphics_environment())


if __name__ == "__main__":
    launch(sys.argv[1:])
