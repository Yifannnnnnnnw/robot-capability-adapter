from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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


def _call(call_id: str, name: str, arguments: Mapping[str, Any]) -> ToolCall:
    raw = json.dumps(dict(arguments), sort_keys=True)
    return ToolCall(call_id, name, dict(arguments), raw)


class ScriptedToolClient:
    def __init__(self, turns: Mapping[str, Sequence[ToolTurn]]) -> None:
        self.turns = {stage: list(values) for stage, values in turns.items()}
        self.calls: list[dict[str, Any]] = []
        self.messages: dict[str, list[list[dict[str, Any]]]] = {}

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del system_prompt, tools
        self.calls.append({"stage": stage, "mode": "react"})
        self.messages.setdefault(stage, []).append(
            [dict(message) for message in messages]
        )
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

    def test_discretionary_probes_preserve_one_smoke_per_missing_capability(self) -> None:
        session = PublicDevelopmentSession(
            package=self.package,
            condition="from-scratch",
            workspace=Path(self.temporary.name) / "reserved-smoke-session",
            budget=ProbeBudget(max_requests=2, timeout_s=10),
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
        self.assertEqual(first["development_status"]["probe_calls_remaining"], 1)
        with self.assertRaisesRegex(ProbeError, "reserved"):
            session.run_mujoco_probe({**probe, "probe_id": "blocked-extra-step"})

        session.write_driver({"source": DRIVER_SOURCE})
        smoke = session.smoke_driver(
            {
                "method_name": "drive",
                "request": {
                    "task_id": "task-1",
                    "task_parameters": {"target": 0.1},
                },
            }
        )
        self.assertTrue(smoke["successful"])
        self.assertEqual(smoke["development_status"]["probe_calls_remaining"], 0)
        self.assertEqual(smoke["development_status"]["missing_current_revision_smokes"], [])

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
                                    "implementation_plan": ["map drive to motor control"],
                                },
                            ),
                        ),
                    ),
                ),
                "generate": (
                    ToolTurn(None, (_call("g1", "read_driver", {}),)),
                    ToolTurn(None, (_call("g2", "write_driver", {"source": DRIVER_SOURCE}),)),
                    ToolTurn(None, (_call("g3", "audit_driver", {}),)),
                    ToolTurn(
                        None,
                        (_call("g4", "smoke_driver", {"method_name": "drive", "request": request}),),
                    ),
                    ToolTurn(None, (_call("g5", "submit_driver", {"note": "ready"}),)),
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
        self.assertEqual(generated.driver_source, DRIVER_SOURCE)
        self.assertEqual(generated.output["generation_note"], "ready")
        self.assertGreaterEqual(len(generated.probe_results), 2)
        stub_observation = client.messages["generate"][1][-1]["content"]
        self.assertIn("def drive(self, request):", stub_observation)
        self.assertIn("NotImplementedError", stub_observation)

    def test_repair_keeps_report_and_driver_in_one_interactive_conversation(self) -> None:
        request = {"task_id": "task-1", "task_parameters": {"target": 0.2}}
        client = ScriptedToolClient(
            {
                "repair": (
                    ToolTurn(None, (_call("r1", "read_driver", {}),)),
                    ToolTurn(None, (_call("r2", "write_driver", {"source": DRIVER_SOURCE}),)),
                    ToolTurn(None, (_call("r3", "audit_driver", {}),)),
                    ToolTurn(
                        None,
                        (_call("r4", "smoke_driver", {"method_name": "drive", "request": request}),),
                    ),
                    ToolTurn(None, (_call("r5", "submit_driver", {"note": "fixed request mapping"}),)),
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
        self.assertEqual(repaired.output["repair_note"], "fixed request mapping")
        self.assertEqual(repaired.probe_results[0]["physics_steps"], 1)
        self.assertGreater(len(repaired.call_evidence.react_trace), 1)
        self.assertIn("AttributeError", repr(repaired.repair_inputs))
        self.assertNotIn("criterion_definition", repr(repaired.repair_inputs))
        previous_observation = client.messages["repair"][1][-1]["content"]
        self.assertIn("request.task_parameters.target", previous_observation)


if __name__ == "__main__":
    unittest.main()
