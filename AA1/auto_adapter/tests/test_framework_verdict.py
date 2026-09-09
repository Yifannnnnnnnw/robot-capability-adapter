"""Focused regressions for framework validation false successes."""
from pathlib import Path

import pytest

from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig


def _orchestrator(tmp_path: Path, class_name: str) -> SelfAssemble:
    scene = tmp_path / "scene.xml"
    scene.write_text("<mujoco/>")
    runner = SelfAssemble(SelfAssembleConfig("fixture", scene, tmp_path / "runs"))
    (runner.workspace / "driver.py").write_text(
        f"class {class_name}:\n    pass\n\ndef build():\n    return {class_name}()\n"
    )
    return runner


def test_unknown_skeleton_is_not_validated(tmp_path):
    result = _orchestrator(tmp_path, "UnknownSkeleton")._phase_validate_framework()
    assert not result.ok


@pytest.mark.parametrize("expected_class", ["mobile_manipulator", "bimanual"])
def test_composite_robot_cannot_pass_as_an_arm(tmp_path, monkeypatch, expected_class):
    runner = _orchestrator(tmp_path, "ArmSerialDLSSkeleton")
    runner.expected_robot_class = expected_class
    monkeypatch.setattr(runner, "_validate_arm", lambda *args: None)
    assert not runner._phase_validate_framework().ok


@pytest.mark.parametrize("crashes", [False, True])
def test_behavior_failure_must_reenter_generation(tmp_path, monkeypatch, crashes):
    runner = _orchestrator(tmp_path, "ArmSerialDLSSkeleton")

    def failing_behavior(skel, tests, rec_dir):
        if crashes:
            raise RuntimeError("observed behavior failure")
        tests.append({"test": "ik_roundtrip", "ok": False, "metric": 0.2,
                      "detail": "no movement: missed the target"})

    monkeypatch.setattr(runner, "_validate_arm", failing_behavior)
    assert not runner._phase_validate_framework().ok
