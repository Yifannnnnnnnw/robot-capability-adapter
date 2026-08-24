from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoadapter2.driver_synthesis.interactive import (
    DevelopmentSessionError,
    IsolatedArtifactSession,
    PublicDevelopmentSession,
    render_interface_stub,
)
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.driver_synthesis.source_check import DriverSourceError
from autoadapter2.libraries import RobotPackage


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


DRIVER_SOURCE = '''
import mujoco


class CapabilityDriver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = float(request["target"])
        mujoco.mj_step(self.model, self.data)
        return {"status": "EXECUTED"}


def build(model, data):
    return CapabilityDriver(model, data)
'''


class InteractiveFileSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name) / "package"
        for directory in (
            root / "assets",
            root / "tasks" / "private",
            root / "reference",
            root / "skeleton",
        ):
            directory.mkdir(parents=True, exist_ok=True)
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
        (root / "morphology.json").write_text(
            json.dumps(morphology), encoding="utf-8"
        )
        (root / "tasks" / "catalog.json").write_text(
            json.dumps({"tasks": [{"task_id": "task-1"}]}), encoding="utf-8"
        )
        (root / "tasks" / "sources.json").write_text(
            json.dumps({"sources": []}), encoding="utf-8"
        )
        (root / "tasks" / "private" / "instances.json").write_text(
            "PRIVATE_SENTINEL", encoding="utf-8"
        )
        (root / "reference" / "driver.py").write_text(
            "REFERENCE_SENTINEL", encoding="utf-8"
        )
        (root / "skeleton" / "motion.py").write_text(
            "class PublicMotion: pass\n", encoding="utf-8"
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
            workspace=Path(self.temporary.name) / "scratch-session",
            budget=ProbeBudget(max_requests=None, timeout_s=10),
            source_root=SOURCE_ROOT,
            capability_methods=("drive",),
        )
        self.extra_sessions: list[object] = []

    def tearDown(self) -> None:
        self.session.close()
        for session in self.extra_sessions:
            close = getattr(session, "close", None)
            if callable(close):
                close()
        self.temporary.cleanup()

    def test_exact_file_tools_and_public_only_projection(self) -> None:
        self.assertEqual(
            {tool.name for tool in self.session.artifact_tools()},
            {"read_file", "write_file", "execute_python"},
        )
        morphology = self.session.read_file({"path": "morphology.json"})
        self.assertEqual(morphology["root"], "public_package")
        self.assertNotIn("PRIVATE_SENTINEL", morphology["content"])
        for forbidden in (
            "tasks/private/instances.json",
            "reference/driver.py",
        ):
            with self.subTest(path=forbidden), self.assertRaises(
                DevelopmentSessionError
            ):
                self.session.read_file({"path": forbidden})
        staged_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in self.session.public_workspace.root.rglob("*")
            if path.is_file()
        )
        self.assertNotIn("PRIVATE_SENTINEL", staged_text)
        self.assertNotIn("REFERENCE_SENTINEL", staged_text)

    def test_skeleton_tools_exist_only_in_assisted_generate_or_repair(self) -> None:
        with self.assertRaises(DevelopmentSessionError):
            self.session.artifact_tools(include_skeleton=True)
        assisted = PublicDevelopmentSession(
            package=self.package,
            condition="skeleton-assisted",
            workspace=Path(self.temporary.name) / "assisted-session",
            budget=ProbeBudget(max_requests=None, timeout_s=10),
            source_root=SOURCE_ROOT,
            capability_methods=("drive",),
        )
        self.extra_sessions.append(assisted)
        self.assertEqual(
            {tool.name for tool in assisted.artifact_tools(include_skeleton=True)},
            {
                "read_file",
                "write_file",
                "execute_python",
                "list_skeletons",
                "inspect_skeleton",
            },
        )
        listing = assisted.list_skeletons({})
        self.assertEqual(listing["skeletons"][0]["name"], "motion.py")
        inspected = assisted.inspect_skeleton({"name": "motion.py"})
        self.assertIn("PublicMotion", inspected["source"])

    def test_persistent_python_session_executes_real_mujoco_without_credentials(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AWS_SECRET_ACCESS_KEY": "DO_NOT_INHERIT"},
            clear=False,
        ):
            first = self.session.execute_python(
                {
                    "code": (
                        "import os\n"
                        "import mujoco\n"
                        "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                        "data = mujoco.MjData(model)\n"
                        "mujoco.mj_step(model, data)\n"
                        "counter = 41\n"
                        "print('credential=' + str(os.environ.get('AWS_SECRET_ACCESS_KEY')))\n"
                    )
                }
            )
        second = self.session.execute_python(
            {"code": "counter += 1\nprint('counter=' + str(counter))\n"}
        )
        self.assertTrue(first["successful"])
        self.assertGreater(first["physics_steps"], 0)
        self.assertIn("credential=None", first["stdout"])
        self.assertTrue(second["successful"])
        self.assertIn("counter=42", second["stdout"])
        self.assertIsNone(second["development_status"]["probe_calls_limit"])
        self.assertIsNone(second["development_status"]["probe_calls_remaining"])

    def test_driver_file_crosses_source_and_real_import_boundary(self) -> None:
        written = self.session.write_file(
            {"path": "driver.py", "content": DRIVER_SOURCE}
        )
        self.assertTrue(written["source_changed"])
        result = self.session.validate_driver_artifact()
        self.assertTrue(result["valid"])
        self.assertTrue(result["import"]["successful"])

    def test_candidate_task_dispatch_is_rejected_before_freeze(self) -> None:
        invalid = DRIVER_SOURCE.replace(
            'request["target"]', 'request["task_id"]'
        )
        self.session.write_file({"path": "driver.py", "content": invalid})
        with self.assertRaisesRegex(DriverSourceError, "capability request field"):
            self.session.validate_driver_artifact()

    def test_interface_stub_has_only_sealed_signatures(self) -> None:
        source = render_interface_stub(("drive", "hold"))
        self.assertIn("def drive(self, request):", source)
        self.assertIn("def hold(self, request):", source)
        self.assertIn("def build(model, data):", source)
        self.assertNotIn("task_id", source)
        self.assertNotIn("mujoco", source)

    def test_ivc_session_has_workspace_only_tools_and_no_robot_package(self) -> None:
        isolated = IsolatedArtifactSession(
            workspace=Path(self.temporary.name) / "ivc-session",
            budget=ProbeBudget(max_requests=None, timeout_s=10),
        )
        self.extra_sessions.append(isolated)
        isolated.write_file({"path": "ivc_inputs.json", "content": "{}\n"})
        self.assertEqual(
            {tool.name for tool in isolated.artifact_tools()},
            {"read_file", "write_file", "execute_python"},
        )
        self.assertEqual(
            isolated.read_file({"path": "ivc_inputs.json"})["root"],
            "workspace",
        )
        with self.assertRaises(DevelopmentSessionError):
            isolated.read_file({"path": "morphology.json"})
        first = isolated.execute_python({"code": "value = 6\nprint(value)"})
        second = isolated.execute_python({"code": "value += 1\nprint(value)"})
        self.assertTrue(first["successful"])
        self.assertIn("7", second["stdout"])
        self.assertFalse((isolated.workspace / "staged").exists())


if __name__ == "__main__":
    unittest.main()
