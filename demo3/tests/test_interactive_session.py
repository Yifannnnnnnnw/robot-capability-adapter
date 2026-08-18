from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autoadapter2.driver_synthesis.interactive import (
    DevelopmentSessionError,
    PublicDevelopmentSession,
    render_interface_stub,
)
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.libraries import RobotPackage


DRIVER_SOURCE = """
import mujoco


class Driver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = float(request["task_parameters"].get("target", 0.0))
        mujoco.mj_step(self.model, self.data)
        return True


def build(model, data):
    return Driver(model, data)
"""


class InteractiveSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name) / "package"
        (root / "assets").mkdir(parents=True)
        (root / "tasks" / "private").mkdir(parents=True)
        (root / "reference").mkdir()
        (root / "skeleton").mkdir()
        (root / "assets" / "scene.xml").write_text(
            """<mujoco model='tiny'>
  <worldbody><body><joint name='joint' type='hinge'/><geom type='capsule' size='0.02 0.1'/></body></worldbody>
  <actuator><motor name='motor' joint='joint' ctrlrange='-1 1'/></actuator>
</mujoco>
""",
            encoding="utf-8",
        )
        morphology = {
            "robot_configuration_id": "tiny",
            "package_version": "1.0.0",
            "mjcf_entrypoint": "assets/scene.xml",
        }
        (root / "morphology.json").write_text(json.dumps(morphology), encoding="utf-8")
        (root / "tasks" / "catalog.json").write_text(
            json.dumps({"tasks": [{"task_id": "task-1"}]}),
            encoding="utf-8",
        )
        (root / "tasks" / "sources.json").write_text(
            json.dumps({"sources": []}),
            encoding="utf-8",
        )
        (root / "tasks" / "private" / "instances.json").write_text(
            "PRIVATE_SENTINEL",
            encoding="utf-8",
        )
        (root / "reference" / "driver.py").write_text(
            "REFERENCE_SENTINEL",
            encoding="utf-8",
        )
        self.package = RobotPackage(
            root=root,
            robot_configuration_id="tiny",
            package_version="1.0.0",
            snapshot_id="tiny-tasks",
            morphology=morphology,
            sources=(),
            tasks=({"task_id": "task-1"},),
            mjcf_path=root / "assets" / "scene.xml",
            skeleton_dir=root / "skeleton",
            reference_driver=root / "reference" / "driver.py",
            private_dir=root / "tasks" / "private",
        )
        self.session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "session",
            budget=ProbeBudget(max_requests=4, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            capability_task_ids={"drive": ("task-1",)},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_tools_exclude_private_and_reference_files(self) -> None:
        listing = self.session.list_public_files({})
        packed = repr(listing)
        self.assertNotIn("private", packed.lower())
        self.assertNotIn("reference", packed.lower())
        self.assertIn("assets/scene.xml", packed)

    def test_interface_stub_contains_only_sealed_signatures_and_placeholders(self) -> None:
        source = render_interface_stub(("drive", "hold_position"))
        self.assertIn("def drive(self, request):", source)
        self.assertIn("def hold_position(self, request):", source)
        self.assertIn("def build(model, data):", source)
        self.assertEqual(source.count("NotImplementedError"), 3)
        self.assertNotIn("mujoco", source)
        self.assertNotIn("ctrl", source)
        self.assertNotIn("mj_step", source)

    def test_generation_session_can_start_from_interface_stub_revision_zero(self) -> None:
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "stub-session",
            budget=ProbeBudget(max_requests=1, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            seed_interface_stub=True,
        )

        initial = session.read_driver({})
        self.assertEqual(initial["revision"], 0)
        self.assertIn("def drive(self, request):", initial["source"])
        self.assertIn("NotImplementedError", initial["source"])
        written = session.write_driver({"source": DRIVER_SOURCE})
        self.assertEqual(written["revision"], 1)

    def test_study_probe_executes_real_public_mujoco_physics(self) -> None:
        result = self.session.run_mujoco_probe(
            {
                "probe_id": "canonical-step",
                "script": (
                    "import os\nimport mujoco\n"
                    "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                    "data = mujoco.MjData(model)\n"
                    "mujoco.mj_step(model, data)\n"
                ),
            }
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["physics_steps"], 1)
        self.assertTrue(self.session.has_successful_physics_probe())

    def test_submit_requires_current_revision_smoke_for_every_capability(self) -> None:
        self.session.write_driver({"source": DRIVER_SOURCE})
        audit = self.session.audit_driver({})
        self.assertEqual(audit["revision"], 1)
        with self.assertRaisesRegex(DevelopmentSessionError, "lacks successful"):
            self.session.submit_driver({"note": "too early"})

        smoke = self.session.smoke_driver(
            {
                "method_name": "drive",
                "request": {
                    "task_id": "task-1",
                    "task_parameters": {"target": 0.1},
                },
            }
        )
        self.assertTrue(smoke["successful"])
        self.assertGreater(smoke["result"]["physics_steps"], 0)
        submitted = self.session.submit_driver({"note": "ready"})
        self.assertEqual(submitted["driver_source"], DRIVER_SOURCE)
        self.assertEqual(submitted["smoked_methods"], ["drive"])

        self.session.write_driver({"source": DRIVER_SOURCE + "\n# revision two\n"})
        with self.assertRaisesRegex(DevelopmentSessionError, "lacks successful"):
            self.session.submit_driver({"note": "stale smoke"})

    def test_smoke_rejects_task_outside_capability_coverage(self) -> None:
        self.session.write_driver({"source": DRIVER_SOURCE})
        with self.assertRaisesRegex(DevelopmentSessionError, "not covered"):
            self.session.smoke_driver(
                {
                    "method_name": "drive",
                    "request": {
                        "task_id": "other-task",
                        "task_parameters": {},
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
