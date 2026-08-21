from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from autoadapter2.driver_synthesis.generation import (
    build_public_generation_inputs,
    generate,
    study,
)
from autoadapter2.driver_synthesis.interactive import (
    DevelopmentSessionError,
    PublicDevelopmentSession,
    render_interface_stub,
)
from autoadapter2.driver_synthesis.probe import ProbeBudget, ProbeError
from autoadapter2.driver_synthesis.repair import repair_with_probes
from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolCall, ToolTurn


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


NESTED_DRIVER_SOURCE = """
import mujoco


class Controller:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def apply(self, target):
        self.data.ctrl[0] = float(target)
        mujoco.mj_step(self.model, self.data)


class Driver:
    def __init__(self, model, data):
        self._controller = Controller(model, data)

    def drive(self, request):
        self._controller.apply(request["task_parameters"].get("target", 0.0))
        return True


def build(model, data):
    return Driver(model, data)
"""


NATIVE_REQUEST_DRIVER_SOURCE = """
import mujoco


class Driver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = float(request["target"])
        mujoco.mj_step(self.model, self.data)
        return True


def build(model, data):
    return Driver(model, data)
"""


def _call(call_id: str, name: str, arguments: Mapping[str, Any]) -> ToolCall:
    raw = json.dumps(dict(arguments), sort_keys=True)
    return ToolCall(call_id, name, dict(arguments), raw)


class ScriptedToolClient:
    def __init__(self, turns: Mapping[str, Sequence[ToolTurn]]) -> None:
        self.turns = {stage: list(values) for stage, values in turns.items()}
        self.calls: list[dict[str, Any]] = []
        self.messages: dict[str, list[list[dict[str, Any]]]] = {}
        self.tools: dict[str, list[list[dict[str, Any]]]] = {}

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del system_prompt
        self.calls.append({"stage": stage, "mode": "react"})
        self.messages.setdefault(stage, []).append(
            [dict(message) for message in messages]
        )
        self.tools.setdefault(stage, []).append([dict(tool) for tool in tools])
        return self.turns[stage].pop(0)


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
        self.design = {
            "artifact_type": "capability_design",
            "capabilities": [
                {
                    "capability_id": "cap-1",
                    "method_name": "drive",
                    "covered_task_ids": ["task-1"],
                }
            ],
        }
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
            budget=ProbeBudget(max_requests=2, timeout_s=10),
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

    def test_discretionary_probes_preserve_bundled_check_budget(self) -> None:
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "reserved-smoke-session",
            budget=ProbeBudget(max_requests=3, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            capability_task_ids={"drive": ("task-1",)},
        )
        probe = {
            "probe_id": "one-discretionary-step",
            "script": (
                "import os\nimport mujoco\n"
                "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                "data = mujoco.MjData(model)\n"
                "mujoco.mj_step(model, data)\n"
            ),
        }

        first = session.run_mujoco_probe(probe)
        self.assertEqual(first["development_status"]["probe_calls_remaining"], 2)
        with self.assertRaisesRegex(ProbeError, "reserved"):
            session.run_mujoco_probe({**probe, "probe_id": "blocked-extra-step"})

        checked = session.check_driver(
            {
                "source": DRIVER_SOURCE,
                "checks": [
                    {
                        "method_name": "drive",
                        "request": {
                            "task_id": "task-1",
                            "task_parameters": {"target": 0.1},
                        },
                    }
                ]
            }
        )
        self.assertTrue(checked["successful"])
        self.assertEqual(checked["development_status"]["probe_calls_remaining"], 0)
        self.assertEqual(checked["development_status"]["missing_current_revision_smokes"], [])

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

    def test_check_driver_batches_import_and_every_capability_smoke(self) -> None:
        request = {"task_id": "task-1", "task_parameters": {"target": 0.1}}

        checked = self.session.check_driver(
            {
                "source": DRIVER_SOURCE,
                "checks": [{"method_name": "drive", "request": request}],
            }
        )

        self.assertTrue(checked["successful"])
        self.assertEqual(
            checked["check_scope"], "public_source_import_and_physics_liveness"
        )
        self.assertFalse(checked["capability_behavior_validated"])
        self.assertFalse(checked["private_harness_executed"])
        self.assertTrue(checked["write"]["source_changed"])
        self.assertEqual(checked["revision"], 1)
        self.assertTrue(checked["import"]["successful"])
        self.assertEqual(
            [(item["method_name"], item["successful"]) for item in checked["capability_checks"]],
            [("drive", True)],
        )
        public_observation = checked["capability_checks"][0]["probe"][
            "public_observation"
        ]
        self.assertGreater(public_observation["simulation_time_s"], 0.0)
        self.assertEqual(len(public_observation["ctrl"]), 1)
        self.assertEqual(len(public_observation["qpos"]), 1)
        self.assertEqual(checked["development_status"]["probe_calls_used"], 2)
        self.assertIn("submit_driver", checked["next_action"])
        self.assertEqual(self.session.submit_driver({})["smoked_methods"], ["drive"])
        visible_tools = {tool.name for tool in self.session.driver_tools()}
        self.assertIn("check_driver", visible_tools)
        self.assertNotIn("write_driver", visible_tools)
        self.assertNotIn("read_driver", visible_tools)
        self.assertNotIn("audit_driver", visible_tools)
        self.assertNotIn("import_driver", visible_tools)
        self.assertNotIn("smoke_driver", visible_tools)

    def test_capability_request_check_smokes_complete_native_object(self) -> None:
        native_source = DRIVER_SOURCE.replace(
            'request["task_parameters"].get("target", 0.0)',
            'request.get("joint_target", 0.0)',
        )
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "capability-request-session",
            budget=ProbeBudget(max_requests=2, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            invocation_abi={
                "kind": "capability_request",
                "method_call": "method(request=request)",
            },
        )
        request = {
            "joint_target": 0.2,
            "max_control_steps": 1,
            "options": {"settle": True},
        }

        scripts: list[str] = []

        def successful_probe(**arguments: Any) -> dict[str, Any]:
            scripts.append(str(arguments["script"]))
            return {
                "probe_id": arguments["probe_id"],
                "exit_code": 0,
                "timed_out": False,
                "spawn_error": None,
                "physics_steps": 1,
                "elapsed_wall_s": 0.01,
                "stdout": "",
                "stderr": "",
            }

        with patch.object(session, "_run_probe", side_effect=successful_probe):
            checked = session.check_driver(
                {
                    "source": native_source,
                    "checks": [{"method_name": "drive", "request": request}],
                }
            )

        self.assertTrue(checked["successful"])
        self.assertNotIn("task_id", checked["capability_checks"][0])
        self.assertEqual(len(scripts), 2)
        self.assertIn(json.dumps(request, ensure_ascii=True, sort_keys=True), scripts[1])
        check_tool = next(tool for tool in session.driver_tools() if tool.name == "check_driver")
        request_schema = check_tool.input_schema["properties"]["checks"]["items"][
            "properties"
        ]["request"]
        self.assertEqual(request_schema, {"type": "object"})

    def test_keyword_request_still_rejects_native_only_object(self) -> None:
        self.session.write_driver({"source": DRIVER_SOURCE})

        with self.assertRaisesRegex(DevelopmentSessionError, "requires string task_id"):
            self.session.smoke_driver(
                {"method_name": "drive", "request": {"target": 0.1}}
            )

    def test_check_driver_requires_exactly_one_request_per_capability(self) -> None:
        request = {"task_id": "task-1", "task_parameters": {"target": 0.1}}

        with self.assertRaisesRegex(DevelopmentSessionError, "duplicate"):
            self.session.check_driver(
                {
                    "source": DRIVER_SOURCE,
                    "checks": [
                        {"method_name": "drive", "request": request},
                        {"method_name": "drive", "request": request},
                    ]
                }
            )
        self.assertEqual(self.session.revision, 0)
        self.assertFalse(self.session.candidate_path.exists())
        self.assertEqual(self.session._development_status()["probe_calls_used"], 0)

    def test_check_driver_retains_audit_failure_as_current_revision_diagnostic(self) -> None:
        request = {"task_id": "task-1", "task_parameters": {"target": 0.1}}
        invalid_source = "def build(model, data):\n    this is not valid Python\n"

        checked = self.session.check_driver(
            {
                "source": invalid_source,
                "checks": [{"method_name": "drive", "request": request}],
            }
        )

        self.assertFalse(checked["successful"])
        self.assertFalse(checked["audit"]["successful"])
        self.assertEqual(checked["audit"]["error"]["type"], "DriverSourceError")
        self.assertEqual(self.session.revision, 1)
        self.assertEqual(self.session.read_driver({})["source"], invalid_source)
        self.assertEqual(checked["development_status"]["probe_calls_used"], 0)

    def test_driver_probe_limit_is_capability_count_plus_three_optional_probes(self) -> None:
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "derived-probe-limit-session",
            budget=ProbeBudget(max_requests=32, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            capability_task_ids={"drive": ("task-1",)},
        )

        status = session._development_status()

        self.assertEqual(status["configured_probe_calls_limit"], 32)
        self.assertEqual(status["probe_calls_limit"], 5)
        self.assertEqual(status["probe_calls_remaining"], 5)

    def test_identical_write_preserves_revision_audit_and_smoke_progress(self) -> None:
        first = self.session.write_driver({"source": DRIVER_SOURCE})
        self.assertTrue(first["source_changed"])
        self.session.audit_driver({})
        self.session.smoke_driver(
            {
                "method_name": "drive",
                "request": {
                    "task_id": "task-1",
                    "task_parameters": {"target": 0.1},
                },
            }
        )

        duplicate = self.session.write_driver({"source": DRIVER_SOURCE})

        self.assertFalse(duplicate["source_changed"])
        self.assertEqual(duplicate["revision"], first["revision"])
        self.assertEqual(
            duplicate["development_status"]["missing_current_revision_smokes"], []
        )
        self.assertIn("Do not write it again", duplicate["next_action"])
        submitted = self.session.submit_driver({"note": "unchanged and ready"})
        self.assertEqual(submitted["smoked_methods"], ["drive"])

    def test_nested_controller_passes_import_and_canonical_physics_smoke(self) -> None:
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "nested-controller-session",
            budget=ProbeBudget(max_requests=2, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
            capability_methods=("drive",),
            capability_task_ids={"drive": ("task-1",)},
        )
        session.write_driver({"source": NESTED_DRIVER_SOURCE})

        imported = session.import_driver({})
        smoke = session.smoke_driver(
            {
                "method_name": "drive",
                "request": {
                    "task_id": "task-1",
                    "task_parameters": {"target": 0.1},
                },
            }
        )

        self.assertTrue(imported["successful"])
        self.assertTrue(smoke["successful"])
        self.assertEqual(session.submit_driver({})["smoked_methods"], ["drive"])

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

    def test_study_and_generate_run_as_interactive_tool_conversations(self) -> None:
        study_probe = (
            "import os\nimport mujoco\n"
            "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
            "data = mujoco.MjData(model)\n"
            "mujoco.mj_step(model, data)\n"
        )
        request = {"task_id": "task-1", "task_parameters": {"target": 0.1}}
        client = ScriptedToolClient(
            {
                "study": (
                    ToolTurn(
                        None,
                        (_call("s1", "run_mujoco_probe", {"probe_id": "step", "script": study_probe}),),
                    ),
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s2",
                                "submit_study",
                                {
                                    "findings": ["one actuator is available"],
                                    "implementation_plan": json.dumps(
                                        ["map drive to motor control"]
                                    ),
                                },
                            ),
                        ),
                    ),
                ),
                "generate": (
                    ToolTurn(
                        None,
                        (
                            _call(
                                "g1",
                                "check_driver",
                                {
                                    "source": DRIVER_SOURCE,
                                    "checks": [
                                        {"method_name": "drive", "request": request}
                                    ],
                                },
                            ),
                        ),
                    ),
                    ToolTurn(None, (_call("g2", "submit_driver", {"note": "ready"}),)),
                ),
            }
        )
        root = Path(self.temporary.name) / "react-cell"
        budget = ProbeBudget(max_requests=4, timeout_s=10)
        study_result = study(
            client,  # type: ignore[arg-type]
            self.package,
            self.design,
            condition="from-scratch",
            workspace=root,
            probe_budget=budget,
            source_root=Path(__file__).resolve().parents[1] / "src",
        )
        generated = generate(
            client,  # type: ignore[arg-type]
            self.package,
            self.design,
            study_result,
            condition="from-scratch",
            workspace=root / "attempt-0",
            probe_results=study_result.probe_results,
            probe_budget=budget,
            source_root=Path(__file__).resolve().parents[1] / "src",
        )

        self.assertEqual(study_result.probe_results[0]["physics_steps"], 1)
        self.assertGreater(len(study_result.call_evidence.react_trace), 1)
        self.assertEqual(len(client.messages["study"]), 2)
        first_turn_tools = {
            tool["function"]["name"] for tool in client.tools["study"][0]
        }
        self.assertEqual(first_turn_tools, {"run_mujoco_probe", "submit_study"})
        probe_definition = next(
            tool
            for tool in client.tools["study"][0]
            if tool["function"]["name"] == "run_mujoco_probe"
        )
        self.assertIn(
            "AUTOADAPTER_PROBE_SCENE",
            probe_definition["function"]["description"],
        )
        self.assertIn("Do not import the skeleton", probe_definition["function"]["description"])
        submit_definition = next(
            tool
            for tool in client.tools["study"][0]
            if tool["function"]["name"] == "submit_study"
        )
        self.assertNotIn(
            "skeleton_inspection",
            submit_definition["function"]["parameters"]["required"],
        )
        study_probe_observation = "\n".join(
            str(message.get("content", ""))
            for message in client.messages["study"][1]
        )
        self.assertIn('"study_requirement_satisfied": true', study_probe_observation)
        self.assertIn("Call submit_study now", study_probe_observation)
        self.assertEqual(generated.driver_source, DRIVER_SOURCE)
        self.assertEqual(generated.output["generation_note"], "ready")
        self.assertGreaterEqual(len(generated.probe_results), 2)
        self.assertEqual(len(client.messages["generate"]), 2)
        self.assertEqual(
            {
                tool["function"]["name"]
                for tool in client.tools["generate"][1]
            },
            {"submit_driver"},
        )
        stub_observation = client.messages["generate"][0][0]["content"]
        self.assertIn("def drive(self, request):", stub_observation)
        self.assertIn("NotImplementedError", stub_observation)

    def test_skeleton_study_schema_requires_skeleton_inspection(self) -> None:
        (self.package.skeleton_dir / "family.py").write_text(
            "# public trusted skeleton family\n", encoding="utf-8"
        )
        probe = (
            "import os\nimport mujoco\n"
            "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
            "data = mujoco.MjData(model)\n"
            "mujoco.mj_step(model, data)\n"
        )
        client = ScriptedToolClient(
            {
                "study": (
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s1",
                                "run_mujoco_probe",
                                {"probe_id": "step", "script": probe},
                            ),
                        ),
                    ),
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s2",
                                "submit_study",
                                {
                                    "findings": ["canonical physics advances"],
                                    "implementation_plan": ["use trusted skeleton"],
                                    "skeleton_inspection": {"files": ["family.py"]},
                                },
                            ),
                        ),
                    ),
                )
            }
        )

        result = study(
            client,  # type: ignore[arg-type]
            self.package,
            self.design,
            condition="skeleton-assisted",
            workspace=Path(self.temporary.name) / "skeleton-study-schema",
            probe_budget=ProbeBudget(max_requests=2, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
        )

        submit_definition = next(
            tool
            for tool in client.tools["study"][0]
            if tool["function"]["name"] == "submit_study"
        )
        self.assertIn(
            "skeleton_inspection",
            submit_definition["function"]["parameters"]["required"],
        )
        self.assertEqual(result.output["skeleton_inspection"]["files"], ["family.py"])

    def test_study_allows_one_failed_probe_recovery_before_submission(self) -> None:
        failed_probe = (
            "import mujoco\n"
            "mujoco.MjModel.from_xml_path('missing-scene.xml')\n"
        )
        successful_probe = (
            "import os\nimport mujoco\n"
            "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
            "data = mujoco.MjData(model)\n"
            "mujoco.mj_step(model, data)\n"
        )
        client = ScriptedToolClient(
            {
                "study": (
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s1",
                                "run_mujoco_probe",
                                {"probe_id": "failed", "script": failed_probe},
                            ),
                        ),
                    ),
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s2",
                                "run_mujoco_probe",
                                {"probe_id": "recovered", "script": successful_probe},
                            ),
                        ),
                    ),
                    ToolTurn(
                        None,
                        (
                            _call(
                                "s3",
                                "submit_study",
                                {
                                    "findings": ["canonical scene advances"],
                                    "implementation_plan": ["map drive to motor control"],
                                },
                            ),
                        ),
                    ),
                )
            }
        )

        result = study(
            client,  # type: ignore[arg-type]
            self.package,
            self.design,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "recover-study-probe",
            probe_budget=ProbeBudget(max_requests=4, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
        )

        self.assertEqual(
            [item["probe_id"] for item in result.probe_results],
            ["failed", "recovered"],
        )
        self.assertEqual(result.probe_results[0]["exit_code"], 1)
        self.assertEqual(result.probe_results[1]["physics_steps"], 1)
        failed_observation = "\n".join(
            str(message.get("content", ""))
            for message in client.messages["study"][1]
        )
        self.assertIn('"study_requirement_satisfied": false', failed_observation)
        self.assertIn("single recovery probe", failed_observation)
        final_turn_tools = {
            tool["function"]["name"] for tool in client.tools["study"][2]
        }
        self.assertEqual(final_turn_tools, {"submit_study"})

    def test_repair_keeps_report_and_driver_in_one_interactive_conversation(self) -> None:
        request = {"task_id": "task-1", "task_parameters": {"target": 0.2}}
        client = ScriptedToolClient(
            {
                "repair": (
                    ToolTurn(
                        None,
                        (
                            _call(
                                "r1",
                                "check_driver",
                                {
                                    "source": DRIVER_SOURCE,
                                    "checks": [
                                        {"method_name": "drive", "request": request}
                                    ],
                                },
                            ),
                        ),
                    ),
                    ToolTurn(None, (_call("r2", "submit_driver", {"note": "fixed request mapping"}),)),
                )
            }
        )
        previous = DRIVER_SOURCE.replace(
            'request["task_parameters"].get("target", 0.0)',
            "request.task_parameters.target",
        )
        public_inputs = build_public_generation_inputs(
            self.package,
            self.design,
            condition="from-scratch",
        )
        report = {
            "validation_passed": False,
            "trials": [
                {
                    "case_id": "opaque-case",
                    "candidate_exception": {
                        "type": "AttributeError",
                        "message": "dict has no attribute task_parameters",
                    },
                    "measured": {"physics_steps": 0},
                    "criterion_definition": {"private": True},
                }
            ],
        }
        repaired = repair_with_probes(
            client,  # type: ignore[arg-type]
            package=self.package,
            previous_driver_source=previous,
            candidate_report=report,
            media_manifest=[],
            public_inputs=public_inputs,
            condition="from-scratch",
            previous_attempt=0,
            workspace=Path(self.temporary.name) / "repair-attempt",
            max_total_attempts=3,
            capability_methods=("drive",),
            probe_budget=ProbeBudget(max_requests=3, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
        )

        self.assertEqual(repaired.driver_source, DRIVER_SOURCE)
        self.assertEqual(
            {
                tool["function"]["name"]
                for tool in client.tools["repair"][1]
            },
            {"submit_driver"},
        )
        self.assertEqual(repaired.output["repair_note"], "fixed request mapping")
        self.assertEqual(len(client.messages["repair"]), 2)
        self.assertTrue(
            any(result["physics_steps"] > 0 for result in repaired.probe_results)
        )
        self.assertGreater(len(repaired.call_evidence.react_trace), 1)
        self.assertIn("AttributeError", repr(repaired.repair_inputs))
        self.assertNotIn("criterion_definition", repr(repaired.repair_inputs))
        previous_observation = client.messages["repair"][0][0]["content"]
        self.assertIn("request.task_parameters.target", previous_observation)

    def test_repair_inherits_capability_native_request_abi(self) -> None:
        request = {"target": 0.2}
        client = ScriptedToolClient(
            {
                "repair": (
                    ToolTurn(
                        None,
                        (
                            _call(
                                "r1",
                                "check_driver",
                                {
                                    "source": NATIVE_REQUEST_DRIVER_SOURCE,
                                    "checks": [
                                        {"method_name": "drive", "request": request}
                                    ],
                                },
                            ),
                        ),
                    ),
                    ToolTurn(None, (_call("r2", "submit_driver", {"note": "fixed"}),)),
                )
            }
        )
        native_design = {
            "artifact_type": "b1_fixed_capability_design",
            "invocation_abi": {
                "kind": "capability_request",
                "method_call": "method(request=request)",
            },
            "capabilities": [
                {
                    "capability_id": "A1",
                    "method_name": "drive",
                    "request_schema": {"type": "object"},
                }
            ],
        }
        public_inputs = build_public_generation_inputs(
            self.package,
            native_design,
            condition="from-scratch",
        )

        repaired = repair_with_probes(
            client,  # type: ignore[arg-type]
            package=self.package,
            previous_driver_source=NATIVE_REQUEST_DRIVER_SOURCE,
            candidate_report={"validation_passed": False},
            media_manifest=[],
            public_inputs=public_inputs,
            condition="from-scratch",
            previous_attempt=0,
            workspace=Path(self.temporary.name) / "native-repair-attempt",
            max_total_attempts=3,
            capability_methods=("drive",),
            probe_budget=ProbeBudget(max_requests=3, timeout_s=10),
            source_root=Path(__file__).resolve().parents[1] / "src",
        )

        self.assertEqual(repaired.driver_source, NATIVE_REQUEST_DRIVER_SOURCE)
        check_observation = json.loads(client.messages["repair"][1][-1]["content"])
        self.assertTrue(check_observation["ok"])
        capability_check = check_observation["result"]["capability_checks"][0]
        self.assertNotIn("task_id", capability_check)
        self.assertTrue(capability_check["successful"])
        self.assertTrue(
            any(result["physics_steps"] > 0 for result in repaired.probe_results)
        )


if __name__ == "__main__":
    unittest.main()
