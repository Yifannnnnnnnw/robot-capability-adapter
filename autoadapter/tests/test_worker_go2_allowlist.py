from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import autoadapter2.trusted_skeletons as trusted_skeletons

from autoadapter2.harness.worker import execute_case


ROOT = Path(__file__).resolve().parents[1]
SCENE = (
    ROOT
    / "libraries"
    / "robots"
    / "robotstudio_so101"
    / "1.0.0"
    / "assets"
    / "scene.xml"
)


def test_worker_explicitly_preserves_go2_policy_without_package_reimport() -> None:
    source = textwrap.dedent(
        """
        import mujoco
        from autoadapter2.trusted_skeletons.go2_velocity_policy import (
            Go2VelocityPolicySkeleton,
        )

        class Driver:
            def __init__(self, model, data):
                self.model = model
                self.data = data

            def command_joint(self, request):
                del request
                assert Go2VelocityPolicySkeleton is not None
                self.data.ctrl[0] = 0.2
                mujoco.mj_step(self.model, self.data)

        def build(*, model, data):
            return Driver(model, data)
        """
    )
    original_path = list(trusted_skeletons.__path__)
    with tempfile.TemporaryDirectory() as temporary:
        driver_path = Path(temporary) / "driver.py"
        driver_path.write_text(source, encoding="utf-8")
        trusted_skeletons.__path__ = []
        try:
            result = execute_case(
                {
                    "driver_path": str(driver_path),
                    "scene_path": str(SCENE),
                    "capability_methods": ["command_joint"],
                    "method_name": "command_joint",
                    "public_arguments": {"request": {}},
                    "reset": {"kind": "default"},
                    "max_steps": 10,
                    "max_sim_time_s": 1.0,
                    "sample_hz": 20.0,
                    "render": {"enabled": False},
                }
            )
        finally:
            trusted_skeletons.__path__ = original_path

    assert result["candidate_exception"] is None
    assert result["canonical_model_data"]
    assert result["physical_evidence"]["step_count"] == 1
