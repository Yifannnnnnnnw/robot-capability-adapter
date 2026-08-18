from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from autoadapter2.harness.worker import execute_case


ROOT = Path(__file__).resolve().parents[1]
SCENE = (
    ROOT
    / "research_candidates"
    / "robotstudio_so101"
    / "1.0.0"
    / "assets"
    / "scene.xml"
)


def _source(*, teleport: bool = False) -> str:
    state_write = "self.data.qpos[0] = 0.8" if teleport else ""
    return textwrap.dedent(
        f"""
        import mujoco

        class Driver:
            def __init__(self, model, data):
                self.model = model
                self.data = data

            def command_joint(self, request):
                target = request["task_parameters"]["target"]
                {state_write}
                self.data.ctrl[0] = float(target)
                mujoco.mj_step(self.model, self.data)
                return True

        def build(*, model, data):
            return Driver(model, data)
        """
    )


class HarnessWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.driver_path = Path(self.temporary.name) / "driver.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self) -> dict:
        return execute_case(
            {
                "driver_path": str(self.driver_path),
                "scene_path": str(SCENE),
                "capability_methods": ["command_joint"],
                "method_name": "command_joint",
                "public_arguments": {
                    "request": {
                        "task_id": "task-1",
                        "task_parameters": {"target": 0.2},
                    }
                },
                "reset": {"kind": "default"},
                "max_steps": 10,
                "max_sim_time_s": 1.0,
                "sample_hz": 20.0,
                "render": {"enabled": False},
            }
        )

    def test_actuator_step_evidence_uses_canonical_session(self) -> None:
        self.driver_path.write_text(_source(), encoding="utf-8")

        result = self._run()

        self.assertTrue(result["worker_completed"])
        self.assertIsNone(result["candidate_exception"])
        self.assertTrue(result["canonical_model_data"])
        self.assertEqual(result["physical_evidence"]["step_count"], 1)
        self.assertTrue(result["physical_evidence"]["ctrl_changed_from_reset"])
        self.assertFalse(result["physical_evidence"]["direct_state_write_detected"])

    def test_runtime_tracker_detects_teleport_before_step(self) -> None:
        self.driver_path.write_text(_source(teleport=True), encoding="utf-8")

        result = self._run()

        self.assertTrue(result["physical_evidence"]["direct_state_write_detected"])
        self.assertIn("qpos", result["physical_evidence"]["direct_state_write_fields"])

    def test_candidate_cannot_import_private_framework_module(self) -> None:
        source = _source().replace(
            "import mujoco", "import mujoco\nimport autoadapter2.harness"
        )
        self.driver_path.write_text(source, encoding="utf-8")

        result = self._run()

        self.assertIsNotNone(result["candidate_exception"])
        self.assertIn(
            result["candidate_exception"]["type"],
            {"ModuleNotFoundError", "ImportError"},
        )


if __name__ == "__main__":
    unittest.main()
