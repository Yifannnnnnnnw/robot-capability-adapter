from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "soarm_video_launcher", ROOT / "run_pipeline_with_video.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_graphics_launcher_marks_video_required_without_mjpython_injection() -> None:
    environment = MODULE.graphics_environment({})

    assert environment["SOARM_MUJOCO_VIDEO_LAUNCHER"] == "1"
    if sys.platform == "darwin":
        assert "MJPYTHON_BIN" not in environment
        assert "MJPYTHON_LIBPYTHON" not in environment
        assert "DYLD_LIBRARY_PATH" not in environment
    else:
        assert environment["MUJOCO_GL"] == "egl"


def test_graphics_launcher_preserves_explicit_non_darwin_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE.sys, "platform", "linux")
    environment = MODULE.graphics_environment({"MUJOCO_GL": "osmesa"})

    assert environment["MUJOCO_GL"] == "osmesa"
    assert environment["SOARM_MUJOCO_VIDEO_LAUNCHER"] == "1"


def test_launch_execs_venv_python_and_preserves_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_execve(
        executable: str, arguments: list[str], environment: dict[str, str]
    ) -> None:
        captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        )

    monkeypatch.setattr(MODULE.os, "execve", fake_execve)
    MODULE.launch(["--mode", "aws", "--run-id", "launcher-contract-test"])

    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[0] == str(MODULE.VENV / "bin/python")
    assert arguments[1] == str(MODULE.ROOT / "run_pipeline.py")
    assert arguments[2:] == ["--mode", "aws", "--run-id", "launcher-contract-test"]
    assert str(captured["executable"]) == str(MODULE.VENV / "bin/python")
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["SOARM_MUJOCO_VIDEO_LAUNCHER"] == "1"


@pytest.mark.parametrize(
    "arguments",
    [[], ["--mode"], ["--mode", "offline"], ["--run-id", "missing-mode"]],
)
def test_launch_rejects_missing_or_non_aws_mode(arguments: list[str]) -> None:
    with pytest.raises(SystemExit, match="requires the explicit arguments"):
        MODULE.launch(arguments)
