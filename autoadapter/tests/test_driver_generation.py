from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoadapter2.driver_synthesis.generation import (
    GENERATE_PROMPT,
    GENERATE_REACT_SYSTEM,
    IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT,
    STUDY_PROMPT,
    STUDY_REACT_SYSTEM,
    GenerationError,
    _build_study_inputs,
    _prepare_fixed_generation_files,
    _react_user_prompt,
    build_public_generation_inputs,
    generate,
    study,
)
from autoadapter2.driver_synthesis.probe import (
    ProbeSourceError,
    ProbeBudget,
    _copy_file,
    audit_public_source,
    prepare_public_probe_workspace,
    public_asset_closure_manifest,
    run_probes,
)
from autoadapter2.driver_synthesis.repair import (
    REPAIR_PROMPT,
    REPAIR_REACT_SYSTEM,
    RepairError,
    RepairLimitError,
    build_repair_inputs,
    redact_candidate_report,
    repair_with_probes,
)
from autoadapter2.driver_synthesis.source_check import DriverSourceError, audit_driver_source
from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ToolCall, ToolTurn, run_artifact_react
from autoadapter2.driver_synthesis.interactive import (
    DevelopmentSessionError, DriverDevelopmentConversation, PublicDevelopmentSession,
)
from autoadapter2.agent_context import AgentContextManager


FROM_SCRATCH_DRIVER = """
import mujoco


class GeneratedDriver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = float(request.get("target", 0.0))
        mujoco.mj_step(self.model, self.data)


def build(model, data):
    return GeneratedDriver(model, data)
"""


SKELETON_DRIVER = """
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton


class GeneratedDriver(ArmSerialDLSSkeleton):
    def drive(self, request):
        return self.data


def build(model, data):
    return GeneratedDriver.from_session(model=model, data=data, spec=None)
"""


class FakeJsonGenerator:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = copy.deepcopy(responses)
        self.calls: list[dict] = []
        self.inputs: list[dict] = []

    def generate_json(self, *, stage, prompt, inputs):
        self.calls.append({"stage": stage, "prompt": prompt})
        self.inputs.append(copy.deepcopy(dict(inputs)))
        return copy.deepcopy(self.responses[stage])


class DriverGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name) / "admitted-package"
        assets = root / "assets"
        (assets / "models").mkdir(parents=True)
        (root / "tasks" / "private").mkdir(parents=True)
        (root / "reference").mkdir()
        (root / "skeleton").mkdir()

        (assets / "scene.xml").write_text(
            "<mujoco model='tiny'><include file='included.xml'/></mujoco>\n",
            encoding="utf-8",
        )
        (assets / "included.xml").write_text(
            """<mujoco model='included'>
  <compiler meshdir='models'/>
  <asset><mesh name='tri' file='tri.obj'/></asset>
  <worldbody>
    <body name='base'>
      <joint name='hinge' type='hinge' axis='0 0 1'/>
      <geom type='mesh' mesh='tri'/>
    </body>
  </worldbody>
  <actuator><motor name='motor' joint='hinge' ctrlrange='-1 1' ctrllimited='true'/></actuator>
</mujoco>
""",
            encoding="utf-8",
        )
        (assets / "models" / "tri.obj").write_text(
            "v 0 0 0\nv 0.1 0 0\nv 0 0.1 0\nv 0 0 0.1\n"
            "f 1 2 3\nf 1 2 4\nf 1 3 4\nf 2 3 4\n",
            encoding="utf-8",
        )
        (assets / "private_scene.xml").write_text(
            "PRIVATE_SCENE_SENTINEL\n",
            encoding="utf-8",
        )
        (root / "morphology.json").write_text(
            json.dumps(
                {
                    "robot_configuration_id": "test-arm",
                    "package_version": "1.0.0",
                    "mjcf_entrypoint": "assets/scene.xml",
                }
            ),
            encoding="utf-8",
        )
        (root / "tasks" / "catalog.json").write_text(
            json.dumps({"robot_configuration_id": "test-arm", "package_version": "1.0.0"}),
            encoding="utf-8",
        )
        (root / "tasks" / "sources.json").write_text(
            json.dumps({"robot_configuration_id": "test-arm", "package_version": "1.0.0"}),
            encoding="utf-8",
        )
        (root / "tasks" / "private" / "instances.json").write_text(
            "PRIVATE_INSTANCE_SENTINEL\n",
            encoding="utf-8",
        )
        (root / "reference" / "driver.py").write_text(
            "REFERENCE_DRIVER_SENTINEL\n",
            encoding="utf-8",
        )
        (root / "skeleton" / "primitive.py").write_text(
            "TRUSTED_PACKAGE_SKELETON_SOURCE\n",
            encoding="utf-8",
        )
        self.root = root
        self.package = RobotPackage(
            root=root,
            robot_configuration_id="test-arm",
            package_version="1.0.0",
            snapshot_id="tasks-1",
            morphology={
                "robot_configuration_id": "test-arm",
                "package_version": "1.0.0",
                "mjcf_entrypoint": "assets/scene.xml",
            },
            sources=(
                {
                    "source_id": "source-1",
                    "title": "Test source",
                    "specific_reference": "Section 1",
                },
            ),
            tasks=({"task_id": "task-1"},),
            mjcf_path=assets / "scene.xml",
            skeleton_dir=root / "skeleton",
            reference_driver=root / "reference" / "driver.py",
            private_dir=root / "tasks" / "private",
        )
        self.design = {
            "artifact_type": "capability_design",
            "invocation_abi": {
                "kind": "capability_request",
                "method_call": "method(request=request)",
                "request_required": ["request"],
            },
            "capabilities": [
                {
                    "capability_id": "cap-1",
                    "method_name": "drive",
                    "request_schema": {
                        "type": "object",
                        "properties": {"target": {"type": "number"}},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "public_smoke_request": {"target": 0.0},
                }
            ],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_generate_and_repair_prompts_restore_feedback_loop_contract(self) -> None:
        for prompt in (
            GENERATE_PROMPT,
            GENERATE_REACT_SYSTEM,
            REPAIR_PROMPT,
            REPAIR_REACT_SYSTEM,
        ):
            self.assertIn(IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT, prompt)

    def test_interactive_driver_prompts_expose_mapping_request_abi(self) -> None:
        for prompt in (GENERATE_REACT_SYSTEM, REPAIR_REACT_SYSTEM):
            normalized = " ".join(prompt.split())
            self.assertIn("request_schema", normalized)
            self.assertIn("do not add task, scene, reset, private-criteria, or whole-task fields", normalized)
            self.assertNotIn("request.task_parameters", normalized)

    def test_react_fixed_tasks_are_in_system_and_user_is_only_sorted_public_input(self) -> None:
        user_prompt = _react_user_prompt(
            {"z_dynamic_sentinel": 2, "a_dynamic_sentinel": 1}
        )

        self.assertEqual(
            user_prompt,
            'PUBLIC_INPUT_JSON:\n{"a_dynamic_sentinel": 1, "z_dynamic_sentinel": 2}',
        )
        for system_prompt, fixed_sentinel in (
            (STUDY_REACT_SYSTEM, "Do not write driver.py"),
            (GENERATE_REACT_SYSTEM, "Do not merely print source in a JSON answer"),
            (REPAIR_REACT_SYSTEM, "Continue editing the current driver using those failures"),
        ):
            self.assertIn(fixed_sentinel, system_prompt)
            self.assertNotIn(fixed_sentinel, user_prompt)
            self.assertNotIn("dynamic_sentinel", system_prompt)
            self.assertNotIn("AutoAdapter 1.0", system_prompt)

    @staticmethod
    def _study(condition: str) -> dict:
        output = {
            "condition": condition,
            "findings": ["public morphology inspected"],
            "implementation_plan": ["use actuator control"],
            "probe_requests": [
                {
                    "probe_id": "canonical-step",
                    "script": "import mujoco\nprint(mujoco.__version__)",
                }
            ],
        }
        if condition == "skeleton-assisted":
            output["skeleton_inspection"] = {"symbols": ["ArmSerialDLSSkeleton"]}
        return output

    def test_reference_source_and_path_are_absent_from_study_and_generate_inputs(self) -> None:
        for condition, driver in (
            ("skeleton-assisted", SKELETON_DRIVER),
            ("from-scratch", FROM_SCRATCH_DRIVER),
        ):
            with self.subTest(condition=condition):
                client = FakeJsonGenerator(
                    {
                        "study": self._study(condition),
                        "generate": {"driver_filename": "driver.py", "driver_source": driver},
                    }
                )
                study_result = study(
                    client,
                    self.package,
                    self.design,
                    condition=condition,
                    runtime_contract={"primitives": ["mujoco.mj_step", "data.ctrl"]},
                )
                generate(
                    client,
                    self.package,
                    self.design,
                    study_result,
                    condition=condition,
                    workspace=Path(self.temporary.name) / condition,
                    runtime_contract={"primitives": ["mujoco.mj_step", "data.ctrl"]},
                )
                self.assertEqual([item["stage"] for item in client.calls], ["study", "generate"])
                # STUDY is pre-TGCD and package-only; the sealed ABI begins at GENERATE.
                abi = client.inputs[1]["allowed_runtime_facts"]["public_invocation_abi"]
                self.assertEqual(abi["kind"], "capability_request")
                self.assertEqual(
                    abi["request_schema_source"],
                    "sealed_capability_design.capabilities[].request_schema",
                )
                self.assertFalse(abi["effect_catalog_or_task_effect_allowlist"])
                runtime = client.inputs[1]["allowed_runtime_facts"]
                self.assertIn("os", runtime["candidate_forbidden_imports"])
                self.assertIn(
                    "os", runtime["probe_environment"]["allowed_utility_imports"]
                )
                self.assertIn(
                    "AUTOADAPTER_PROBE_SCENE",
                    runtime["probe_environment"]["canonical_scene_loader"],
                )
                interface_stub = client.inputs[1]["driver_interface_stub"]
                self.assertIn("def drive(self, request):", interface_stub)
                self.assertIn("NotImplementedError", interface_stub)
                self.assertNotIn("mujoco", interface_stub)
                for inputs in client.inputs:
                    packed = repr(inputs)
                    self.assertNotIn("REFERENCE_DRIVER_SENTINEL", packed)
                    self.assertNotIn(str(self.package.reference_driver), packed)
                    self.assertNotIn("calibration_reference_source", packed)
                    self.assertNotIn("reference_driver", packed)

    def test_capability_request_abi_is_projected_without_task_envelope(self) -> None:
        design = copy.deepcopy(self.design)
        design["invocation_abi"] = {
            "kind": "capability_request",
            "method_call": "method(request=request)",
        }
        design["capabilities"][0]["request_schema"] = {
            "type": "object",
            "properties": {"joint_target": {"type": "number"}},
            "required": ["joint_target"],
        }
        design["capabilities"][0]["public_smoke_request"] = {"joint_target": 0.1}

        inputs = build_public_generation_inputs(
            self.package,
            design,
            condition="from-scratch",
            runtime_contract={
                "primitives": ["mujoco.mj_step", "data.ctrl"],
                "public_invocation_abi": {"kind": "keyword_request"},
            },
        )

        abi = inputs["allowed_runtime_facts"]["public_invocation_abi"]
        self.assertEqual(abi["kind"], "capability_request")
        self.assertEqual(abi["method_call"], "method(request=request)")
        self.assertNotIn("request_schema", abi)
        self.assertEqual(
            abi["request_schema_source"],
            "sealed_capability_design.capabilities[].request_schema",
        )
        self.assertIn("pre-TGCD study", STUDY_PROMPT)
        self.assertIn("pre-TGCD", STUDY_REACT_SYSTEM)
        self.assertNotIn("sealed_capability_design", STUDY_PROMPT)

    def test_study_condition_is_framework_canonicalized(self) -> None:
        output = self._study("from-scratch")
        output["condition"] = "from_scratch"
        client = FakeJsonGenerator({"study": output})

        result = study(
            client,
            self.package,
            self.design,
            condition="from-scratch",
        )

        self.assertEqual(result.condition, "from-scratch")
        self.assertEqual(result.output["condition"], "from-scratch")
        self.assertEqual(result.output["model_declared_condition"], "from_scratch")

    def test_study_prompt_matches_projected_fixed_design(self) -> None:
        """Prompt wiring only: the scripted model does not establish physics evidence."""
        design = copy.deepcopy(self.design)
        design["capabilities"][0]["criteria"] = [{"threshold": 0.015}]
        output = self._study("from-scratch")

        class ScriptedStudyClient:
            def __init__(self):
                self.sent = []

            def generate_tool_turn(self, **kwargs):
                self.sent.append(copy.deepcopy(kwargs))
                if len(self.sent) == 1:
                    arguments = {"path": "study.json", "content": json.dumps(output)}
                    return ToolTurn(content=None, tool_calls=(
                        ToolCall("write-study", "write_file", arguments, json.dumps(arguments)),
                    ), finish_reason="tool_calls")
                return ToolTurn(content="Study complete.", finish_reason="stop")

        for interactive in (False, True):
            for label, supplied, fixed in (
                ("fixed", design, True),
                ("dynamic", None, False),
                ("unsealed", {"capabilities": design["capabilities"]}, False),
            ):
                with self.subTest(interactive=interactive, design=label):
                    client = ScriptedStudyClient() if interactive else FakeJsonGenerator({"study": output})
                    with patch(
                        "autoadapter2.driver_synthesis.generation.PublicDevelopmentSession.has_successful_physics_probe",
                        return_value=True,
                    ):
                        result = study(
                            client, self.package, supplied, condition="from-scratch",
                            workspace=Path(self.temporary.name) / f"prompt-{interactive}-{label}",
                        )
                    if interactive:
                        sent = client.sent[0]
                        prompt = sent["system_prompt"]
                        inputs = json.loads(sent["messages"][0]["content"].split("\n", 1)[1])
                    else:
                        prompt = client.calls[0]["prompt"]
                        inputs = client.inputs[0]
                    self.assertEqual(result.call_evidence.prompt, prompt)
                    self.assertEqual("sealed_capability_design" in inputs, fixed)
                    self.assertNotIn("capability_validation_suite", inputs)
                    normalized = " ".join(prompt.split())
                    if fixed:
                        self.assertIn("sealed_capability_design", normalized)
                        self.assertIn("public criteria", normalized)
                        self.assertNotIn("no capability design", normalized)
                        self.assertEqual(inputs["sealed_capability_design"], design)
                    else:
                        self.assertIn("no capability design", normalized)
                    self.assertFalse(inputs["condition_eligible_artifacts"]["skeleton_available"])

    def test_asset_closure_excludes_sibling_scene_but_keeps_include_and_mesh(self) -> None:
        manifest = public_asset_closure_manifest(self.package)
        paths = {str(item["path"]) for item in manifest["files"]}
        self.assertEqual(paths, {"scene.xml", "included.xml", "models/tri.obj"})
        self.assertNotIn("PRIVATE_SCENE_SENTINEL", repr(manifest))

        public = prepare_public_probe_workspace(
            self.package,
            Path(self.temporary.name) / "staged",
            condition="from-scratch",
            framework_source_root=Path(__file__).resolve().parents[1] / "src",
        )
        self.assertTrue(public.scene_path.is_file())
        self.assertTrue((public.root / "assets" / "models" / "tri.obj").is_file())
        self.assertFalse((public.root / "assets" / "private_scene.xml").exists())

    def test_copy_file_prefers_darwin_clone_and_falls_back(self) -> None:
        source = Path(self.temporary.name) / "clone-source.bin"
        source.write_bytes(b"source")
        cloned = Path(self.temporary.name) / "clone" / "destination.bin"

        def clone_file(command, **_kwargs):
            Path(command[-1]).write_bytes(Path(command[-2]).read_bytes())
            return type("CloneResult", (), {"returncode": 0})()

        with patch("autoadapter2.driver_synthesis.probe.sys.platform", "darwin"), patch(
            "autoadapter2.driver_synthesis.probe.subprocess.run",
            side_effect=clone_file,
        ) as run_clone:
            _copy_file(source, cloned)

        self.assertEqual(cloned.read_bytes(), b"source")
        cloned.write_bytes(b"changed")
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(run_clone.call_args.args[0][:3], ["cp", "-c", "-p"])

        fallback = Path(self.temporary.name) / "fallback" / "destination.bin"
        with patch("autoadapter2.driver_synthesis.probe.sys.platform", "darwin"), patch(
            "autoadapter2.driver_synthesis.probe.subprocess.run",
            return_value=type("CloneResult", (), {"returncode": 1})(),
        ):
            _copy_file(source, fallback)
        self.assertEqual(fallback.read_bytes(), b"source")

    def test_probe_isolation_and_nstep_fact_use_staged_public_scene(self) -> None:
        outside = Path(self.temporary.name) / "outside-probe-secret.txt"
        outside.write_text("OUTSIDE_PROBE_SECRET", encoding="utf-8")
        script = """
import os
from pathlib import Path
import mujoco

public = Path(os.environ["AUTOADAPTER_PROBE_PUBLIC_PACKAGE"])
files = sorted(path.relative_to(public).as_posix() for path in public.rglob("*") if path.is_file())
print("FILES=" + ",".join(files))
print("PYROOT=" + str(os.environ.get("AUTOADAPTER_PROBE_PYTHON_ROOT")))
print("CWD=" + Path.cwd().as_posix())
model = mujoco.MjModel.from_xml_path("assets/scene.xml")
data = mujoco.MjData(model)
data.ctrl[0] = 0.2
mujoco.mj_step(model, data, 2)
print("TIME=" + str(data.time))
""" + f"""
try:
    print("LEAK=" + open({str(outside)!r}, encoding="utf-8").read())
except PermissionError:
    print("OUTSIDE_READ=BLOCKED")
try:
    getattr(os, "sy" + "stem")("/usr/bin/true")
except PermissionError:
    print("DYNAMIC_PROCESS=BLOCKED")
"""
        result = run_probes(
            [{"probe_id": "public-scene", "script": script}],
            package=self.package,
            workspace=Path(self.temporary.name) / "probe",
            condition="from-scratch",
            budget=ProbeBudget(max_steps=3, max_sim_time_s=1.0),
        )[0]
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["physics_steps"], 2)
        self.assertIn("included.xml", result["stdout"])
        self.assertIn("models/tri.obj", result["stdout"])
        self.assertNotIn("private_scene.xml", result["stdout"])
        self.assertNotIn("PRIVATE_INSTANCE_SENTINEL", result["stdout"])
        self.assertNotIn("REFERENCE_DRIVER_SENTINEL", result["stdout"])
        self.assertIn("PYROOT=None", result["stdout"])
        self.assertIn("CWD=", result["stdout"])
        self.assertIn("/probe/public_package", result["stdout"])
        self.assertIn("OUTSIDE_READ=BLOCKED", result["stdout"])
        self.assertIn("DYNAMIC_PROCESS=BLOCKED", result["stdout"])
        self.assertNotIn("OUTSIDE_PROBE_SECRET", result["stdout"])
        self.assertFalse((Path(self.temporary.name) / "probe" / "public_python").exists())

    def test_source_boundary_rejects_framework_network_and_wrong_condition_imports(self) -> None:
        audit_public_source(
            "from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton",
            condition="skeleton-assisted",
        )
        with self.assertRaises(ProbeSourceError):
            audit_public_source("import autoadapter2.harness", condition="skeleton-assisted")
        with self.assertRaises(ProbeSourceError):
            audit_public_source("from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton", condition="from-scratch")
        with self.assertRaises(ProbeSourceError):
            audit_public_source("import socket", condition="from-scratch")
        with self.assertRaises(ProbeSourceError):
            audit_public_source("import pathlib", condition="from-scratch")
        for source in (
            "import os\nos.system('true')",
            "import os\nos.popen('true')",
            "import os\nos.spawnv(0, '/usr/bin/true', ['true'])",
            "import os\nos.execl('/usr/bin/true', 'true')",
            "import os\nos.kill(os.getpid(), 0)",
            "import os\nos.killpg(os.getpgrp(), 0)",
        ):
            with self.subTest(source=source), self.assertRaises(ProbeSourceError):
                audit_public_source(
                    source,
                    condition="from-scratch",
                    allow_probe_utilities=True,
                )

    def test_generation_rejects_capability_method_without_fixed_request_abi(self) -> None:
        client = FakeJsonGenerator(
            {
                "study": self._study("from-scratch"),
                "generate": {
                    "driver_filename": "driver.py",
                    "driver_source": FROM_SCRATCH_DRIVER.replace(
                        "def drive(self, request):",
                        "def drive(self, target):",
                    ),
                },
            }
        )
        study_result = study(
            client,
            self.package,
            self.design,
            condition="from-scratch",
        )
        with self.assertRaises(GenerationError):
            generate(
                client,
                self.package,
                self.design,
                study_result,
                condition="from-scratch",
                workspace=Path(self.temporary.name) / "bad-abi",
            )

    def test_source_audit_covers_mujoco_aliases_mutable_state_and_request_abi(self) -> None:
        alias_driver = """
from mujoco import mj_step as advance


class GeneratedDriver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = 0.0
        advance(self.model, self.data)


def build(model, data):
    return GeneratedDriver(model, data)
"""
        audit_driver_source(
            alias_driver,
            condition="from-scratch",
            capability_methods=["drive"],
        )

        for statement in (
            "from mujoco import mj_resetData as reset",
            "from mujoco import mj_resetDataKeyframe as reset",
            "from mujoco import MjData as Data",
            "from mujoco import MjModel as Model",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(DriverSourceError):
                    audit_driver_source(
                        statement + "\n" + FROM_SCRATCH_DRIVER,
                        condition="from-scratch",
                        capability_methods=["drive"],
                    )

        for field in (
            "qacc",
            "qacc_warmstart",
            "act",
            "mocap_pos",
            "mocap_quat",
            "qfrc_applied",
            "xfrc_applied",
            "userdata",
        ):
            with self.subTest(field=field):
                source = FROM_SCRATCH_DRIVER.replace(
                    "        mujoco.mj_step(self.model, self.data)",
                    f"        self.data.{field}[0] = 0.0\n"
                    "        mujoco.mj_step(self.model, self.data)",
                )
                with self.assertRaises(DriverSourceError):
                    audit_driver_source(
                        source,
                        condition="from-scratch",
                        capability_methods=["drive"],
                    )

        bad_signature = FROM_SCRATCH_DRIVER.replace(
            "def drive(self, request):",
            "def drive(self, target):",
        )
        with self.assertRaisesRegex(DriverSourceError, "request"):
            audit_driver_source(
                bad_signature,
                condition="from-scratch",
                capability_methods=["drive"],
            )

    def test_repair_preserves_candidate_facing_private_case_facts_and_uses_probe_round(self) -> None:
        public_inputs = build_public_generation_inputs(
            self.package,
            self.design,
            condition="from-scratch",
            runtime_contract={"primitives": ["mujoco.mj_step", "data.ctrl"]},
            study_output={
                "findings": [
                    {"criterion": "model-authored public success wording"}
                ],
                "implementation_plan": [
                    {
                        "step": "implement public behavior",
                        "details": {
                            "criterion": "model-authored public implementation check"
                        },
                    }
                ],
            },
        )
        report = {
            "private_case_id": "opaque-case-7",
            "passed_private_case_count": 0,
            "private_case_count": 1,
            "measurement_value": 0.5,
            "actual_invocation_arguments": {
                "request": {"task_id": "task-1", "task_parameters": {"target": 0.2}}
            },
            "criterion_passed": False,
            "guard_outcomes": {"control": True},
            "private_suite": {"threshold": 0.01},
            "private_path": str(self.package.private_dir / "instances.json"),
        }
        media = {"video_path": str(Path(self.temporary.name) / "media" / "trial.mp4"), "frame_count": 4}
        client = FakeJsonGenerator(
            {
                "repair_prepare": {
                    "repair_plan": ["inspect actuator response"],
                    "probe_requests": [
                        {
                            "probe_id": "repair-check",
                            "script": """
import os
from pathlib import Path
import mujoco

public = Path(os.environ["AUTOADAPTER_PROBE_PUBLIC_PACKAGE"])
assert (public / "morphology.json").is_file()
model = mujoco.MjModel.from_xml_path(os.environ["AUTOADAPTER_PROBE_SCENE"])
data = mujoco.MjData(model)
mujoco.mj_step(model, data)
print("probe-time=" + str(data.time))
""",
                        }
                    ],
                },
                "repair": {"driver_filename": "driver.py", "driver_source": FROM_SCRATCH_DRIVER},
            }
        )
        result = repair_with_probes(
            client,
            package=self.package,
            previous_driver_source=FROM_SCRATCH_DRIVER,
            candidate_report=report,
            media_manifest=media,
            public_inputs=public_inputs,
            condition="from-scratch",
            previous_attempt=0,
            workspace=Path(self.temporary.name) / "repair",
            probe_budget=ProbeBudget(max_steps=2, max_sim_time_s=1.0),
        )
        self.assertEqual(result.attempt, 1)
        self.assertEqual([item["stage"] for item in client.calls], ["repair_prepare", "repair"])
        final_inputs = client.inputs[1]
        candidate_report = final_inputs["candidate_report"]
        self.assertEqual(candidate_report["private_case_id"], "opaque-case-7")
        self.assertEqual(candidate_report["passed_private_case_count"], 0)
        self.assertEqual(candidate_report["private_case_count"], 1)
        self.assertEqual(candidate_report["measurement_value"], 0.5)
        self.assertNotIn("private_suite", candidate_report)
        self.assertNotIn("private_path", candidate_report)
        self.assertEqual(final_inputs["media_manifest"]["frame_count"], 4)
        self.assertEqual(
            final_inputs["public_context"]["study"]["findings"][0]["criterion"],
            "model-authored public success wording",
        )
        self.assertEqual(
            final_inputs["public_context"]["study"]["implementation_plan"][0][
                "details"
            ]["criterion"],
            "model-authored public implementation check",
        )
        self.assertEqual(result.probe_results[0]["exit_code"], 0)
        self.assertEqual(result.probe_results[0]["physics_steps"], 1)
        self.assertIn("probe_results", final_inputs)
        self.assertIn("prepare_call_evidence", result.__dict__)

        with self.assertRaises(RepairLimitError):
            repair_with_probes(
                client,
                package=self.package,
                previous_driver_source=FROM_SCRATCH_DRIVER,
                candidate_report=report,
                media_manifest=media,
                public_inputs=public_inputs,
                condition="from-scratch",
                previous_attempt=2,
                workspace=Path(self.temporary.name) / "repair-limit",
            )
        self.assertEqual(len(client.calls), 2)

    def test_interactive_repair_rejects_unchanged_previous_driver_after_length_finish(
        self,
    ) -> None:
        changed_driver = FROM_SCRATCH_DRIVER.replace(
            'request.get("target", 0.0)',
            'request["target"]',
        )

        class ScriptedClient:
            def __init__(self) -> None:
                self.messages: list[list[dict]] = []
                self.turns = [
                    ToolTurn(content="unfinished", finish_reason="length"),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            ToolCall(
                                id="write-repair",
                                name="write_file",
                                arguments={
                                    "path": "driver.py",
                                    "content": changed_driver,
                                },
                                raw_arguments=json.dumps(
                                    {
                                        "path": "driver.py",
                                        "content": changed_driver,
                                    }
                                ),
                            ),
                        ),
                    ),
                    ToolTurn(content="done", finish_reason="stop"),
                ]

            def generate_tool_turn(self, **kwargs):
                self.messages.append(
                    [dict(message) for message in kwargs["messages"]]
                )
                return self.turns.pop(0)

        client = ScriptedClient()
        public_inputs = build_public_generation_inputs(
            self.package,
            self.design,
            condition="from-scratch",
        )
        with patch(
            "autoadapter2.driver_synthesis.interactive."
            "PublicDevelopmentSession.validate_driver_artifact",
            return_value={"import": {"ok": True}},
        ):
            result = repair_with_probes(
                client,
                package=self.package,
                previous_driver_source=FROM_SCRATCH_DRIVER,
                candidate_report={"validation_passed": False},
                media_manifest=[],
                public_inputs=public_inputs,
                condition="from-scratch",
                previous_attempt=0,
                workspace=Path(self.temporary.name) / "interactive-repair",
            )

        self.assertEqual(result.driver_source, changed_driver)
        self.assertEqual(len(client.messages), 3)
        feedback = "\n".join(
            str(message.get("content", "")) for message in client.messages[1]
        )
        self.assertIn("unchanged from the previous frozen driver", feedback)
        first_validation = next(
            event
            for event in result.call_evidence.react_trace
            if event.get("event") == "end_turn"
        )
        self.assertFalse(first_validation["artifact_valid"])

    def test_fixed_generate_files_keep_study_scene_and_real_skeleton_readable(self) -> None:
        """Fixed inputs remain complete on disk without being repeated in the prompt."""
        self.package.morphology["invocation_abi"] = {
            "request_required": ["task_id", "task_parameters"]
        }
        (self.package.skeleton_dir / "primitive.py").write_text(
            "from autoadapter2.trusted_skeletons.arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec\n"
        )
        source_root = Path(__file__).resolve().parents[1] / "src"
        original_morphology = (self.package.root / "morphology.json").read_text()
        for condition in ("skeleton-assisted", "from-scratch"):
            with self.subTest(condition=condition):
                study_inputs = _build_study_inputs(
                    self.package, self.design, condition=condition,
                    experience=(), runtime_contract=None,
                )
                full = build_public_generation_inputs(
                    self.package, self.design, condition=condition,
                    study_output=self._study(condition), probe_results=[{"stdout": "PROBE_EVIDENCE"}],
                )
                before = copy.deepcopy(full)
                workspace = Path(self.temporary.name) / condition / "files"
                session = PublicDevelopmentSession(
                    package=self.package, condition=condition, workspace=workspace,
                    budget=ProbeBudget(), source_root=source_root,
                )
                try:
                    compact = _prepare_fixed_generation_files(full, session)
                    self.assertEqual(full, before)
                    self.assertEqual(compact["sealed_capability_design"], self.design)
                    self.assertNotIn("task_library", compact["public_robot_package"])
                    self.assertNotIn("study", compact)
                    self.assertNotIn("probe_results", compact)
                    self.assertEqual(
                        compact["public_robot_package"]["morphology"]["invocation_abi"],
                        self.design["invocation_abi"],
                    )
                    for path in compact["public_files"].values():
                        content = session.read_file({"path": path})["content"]
                        self.assertTrue(content)
                        with self.assertRaises(DevelopmentSessionError):
                            session.write_file({"path": path, "content": "overwrite"})
                    self.assertEqual(
                        json.loads(session.read_file({"path": compact["public_files"]["study"]})["content"]),
                        self._study(condition),
                    )
                    self.assertEqual(
                        json.loads(session.read_file({"path": "morphology.json"})["content"])["invocation_abi"],
                        self.design["invocation_abi"],
                    )
                    self.assertIn("PROBE_EVIDENCE", session.read_file({
                        "path": compact["public_files"]["study_probe_results"]
                    })["content"])
                    for path in compact["public_robot_package"]["selected_mjcf_closure"]["text_files"]:
                        self.assertTrue(session.read_file({"path": path})["content"])
                    if condition == "skeleton-assisted":
                        sources = compact["condition_eligible_artifacts"]["source_files"]
                        self.assertTrue(all("source" not in item for item in sources))
                        runtime = next(item["path"] for item in sources if "/runtime/" in item["path"])
                        expected = next(item["source"] for item in full["condition_eligible_artifacts"]["source_files"] if item["path"].startswith("runtime/"))
                        self.assertEqual(session.read_file({"path": runtime})["content"], expected)
                        self.assertIn("class ArmSpec", expected)
                        self.assertIn("class ArmSerialDLSSkeleton", expected)
                        inspection = session.inspect_skeleton({"name": runtime.removeprefix("skeleton/")})
                        self.assertEqual(inspection["source"], expected)
                        self.assertNotIn("next_offset", inspection)
                        # The next model turn must receive complete real source,
                        # including through native message projection, not just the handler.
                        observed = []
                        class FileReaderClient:
                            def generate_tool_turn(self, **kwargs):
                                observed.append(kwargs["messages"].copy())
                                if len(observed) == 1:
                                    return ToolTurn(content=None, tool_calls=tuple(
                                        ToolCall(name, name, args, json.dumps(args))
                                        for name, args in (
                                            ("read_file", {"path": runtime}),
                                            ("inspect_skeleton", {"name": runtime.removeprefix("skeleton/")}),
                                        )
                                    ))
                                return ToolTurn(content="done")
                        fixture = workspace / "delivery_fixture.txt"
                        fixture.write_text("file delivery test artifact")
                        file_tools = session.artifact_tools(include_skeleton=True)
                        self.assertNotIn("offset", file_tools[0].input_schema["properties"])
                        run_artifact_react(
                            client=FileReaderClient(), stage="file-delivery-test",
                            system_prompt="Read the source.", user_prompt="Read both tools.",
                            tools=file_tools, artifact_name=fixture.name,
                            artifact_path=fixture, max_turns=4,
                        )
                        delivered = AgentContextManager().project_native(observed[1]).messages
                        for message, field in zip(
                            (m for m in delivered if m["role"] == "tool"),
                            ("content", "source"), strict=True,
                        ):
                            self.assertGreater(len(message["content"]), 24000)
                            self.assertEqual(json.loads(message["content"])["result"][field], expected)
                        self.assertNotIn("class ArmSerialDLSSkeleton", json.dumps(compact))
                    else:
                        with self.assertRaises(DevelopmentSessionError):
                            session.read_file({"path": "skeleton/primitive.py"})
                    for private_path in ("tasks/private/instances.json", "reference/driver.py", "assets/private_scene.xml"):
                        with self.assertRaises(DevelopmentSessionError):
                            session.read_file({"path": private_path})
                finally:
                    session.close()
                evidence = workspace.with_name("files-inputs")
                self.assertEqual(json.loads((evidence / "initial_inputs.json").read_text()), compact)
                self.assertTrue((evidence / compact["public_files"]["study"]).is_file())
                self.assertEqual(study_inputs, _build_study_inputs(
                    self.package, self.design, condition=condition,
                    experience=(), runtime_contract=None,
                ))
        self.assertEqual((self.package.root / "morphology.json").read_text(), original_morphology)

    def test_generate_repair_share_history_python_and_budget_with_small_feedback(self) -> None:
        """Scripted model, real public MuJoCo worker and real import/build checks."""
        changed = FROM_SCRATCH_DRIVER.replace('request.get("target", 0.0)', 'request["target"]')

        def tool(name, arguments):
            return ToolTurn(content=None, finish_reason="tool_calls", tool_calls=(
                ToolCall(name + str(len(json.dumps(arguments))), name, arguments, json.dumps(arguments)),
            ))

        class ScriptedClient:
            def __init__(self):
                self.messages = []
                self.systems = []
                self.turns = [
                    tool("execute_python", {"code": (
                        "import os\nimport mujoco\n"
                        "model = mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])\n"
                        "data = mujoco.MjData(model)\n"
                        "mujoco.mj_step(model, data)\ncontinuity_marker = 41\n"
                    )}),
                    tool("write_file", {"path": "driver.py", "content": FROM_SCRATCH_DRIVER}),
                    ToolTurn(content="submitted", finish_reason="stop"),
                    ToolTurn(content="unchanged submission", finish_reason="stop"),
                    tool("execute_python", {"code": (
                        "assert continuity_marker == 41\n"
                        "import json\n"
                        "with open(os.path.join(os.environ['AUTOADAPTER_PROBE_PUBLIC_PACKAGE'], 'generation_inputs/study.json')) as f:\n"
                        "    assert json.load(f)['condition'] == 'from-scratch'\n"
                        "mujoco.mj_step(model, data)\nprint('CONTINUED', data.time)\n"
                    )}),
                    tool("read_file", {"path": "validation_feedback/attempt-0/trial-0.json"}),
                    tool("write_file", {"path": "driver.py", "content": changed}),
                    ToolTurn(content="repaired", finish_reason="stop"),
                ]

            def generate_tool_turn(self, **kwargs):
                self.messages.append(copy.deepcopy(kwargs["messages"]))
                self.systems.append(kwargs["system_prompt"])
                return self.turns.pop(0)

        client = ScriptedClient()
        workspace = Path(self.temporary.name) / "continuous"
        with DriverDevelopmentConversation() as development:
            generated = generate(
                client, self.package, self.design, self._study("from-scratch"),
                condition="from-scratch", workspace=workspace, development=development,
                probe_budget=ProbeBudget(max_steps=3), max_turns=6,
                fixed_file_inputs=True,
            )
            session = development.session
            worker = session._python_session
            stage_dir = session.public_workspace.root
            public_inputs = generated.call_evidence.inputs
            self.assertIn("public_files", public_inputs)
            self.assertNotIn("task_library", public_inputs["public_robot_package"])
            repaired = repair_with_probes(
                client, package=self.package, previous_driver_source=generated.driver_source,
                candidate_report={"validation_passed": False, "trials": [{
                    "case_id": "case-1", "capability_id": "cap-1", "trial_passed": False,
                    "measurement_value": 0.2, "public_arguments": {"request": {"target": 0.4}},
                    "measurement_binding": {"secret": "PRIVATE_BINDING_SENTINEL"},
                    "candidate_log": "useful diagnostic",
                }]}, media_manifest=[], public_inputs=public_inputs,
                condition="from-scratch", previous_attempt=0, workspace=workspace,
                development=development, max_turns=7,
            )
            self.assertIs(development.session, session)
            self.assertIs(session._python_session, worker)
            self.assertEqual(repaired.driver_source, changed)
            self.assertEqual(len(repaired.probe_results), 2)  # explicit probe plus import/build
            probe = repaired.probe_results[0]
            self.assertTrue(probe["successful"], probe)
            self.assertEqual(probe["physics_steps_total"], 2)
            self.assertIn("CONTINUED", probe["stdout"])
            self.assertEqual(client.systems[0], client.systems[3])
            self.assertEqual(client.messages[0][0], client.messages[3][0])
            self.assertGreater(len(client.messages[3]), 1)
            latest = client.messages[3][-1]["content"]
            self.assertTrue(latest.startswith("DRIVER_VALIDATION_FEEDBACK_JSON:\n"))
            self.assertNotIn("public_context", latest)
            self.assertNotIn("previous_driver_source", latest)
            for path in (workspace / "validation_feedback").rglob("*.json"):
                self.assertNotIn("PRIVATE_BINDING_SENTINEL", path.read_text())
            self.assertIn("unchanged from the previous frozen driver", json.dumps(client.messages[4]))
            read_event = next(event for event in repaired.call_evidence.react_trace
                              if event.get("tool") == "read_file")
            self.assertTrue(read_event["ok"], read_event)
            self.assertIn("useful diagnostic", read_event["observation"])
            # The failure remains available after it leaves the recent history window.
            for project in (AgentContextManager(recent_groups=1).project_native,
                            AgentContextManager(recent_groups=1).project_text_observation):
                projected = project(development.messages)
                self.assertIn("DRIVER_VALIDATION_FEEDBACK_JSON:", json.dumps(projected.messages))
                self.assertIn('"target": 0.4', "\n".join(str(m.get("content")) for m in projected.messages))
        self.assertIsNone(session._python_session)
        self.assertFalse(stage_dir.exists())

    def test_repair_rejects_private_definitions_despite_public_study_criterion(self) -> None:
        unsafe_public_inputs = (
            {
                "study": {
                    "findings": [
                        {"criterion_definition": {"threshold": 0.01}}
                    ]
                }
            },
            {
                "study": {
                    "implementation_plan": [
                        {"private_suite": {"case": "hidden"}}
                    ]
                }
            },
            {"study": {"probe_requests": [{"criterion": "not a finding"}]}},
        )

        for public_inputs in unsafe_public_inputs:
            with self.subTest(public_inputs=public_inputs):
                with self.assertRaisesRegex(RepairError, "contains private field"):
                    build_repair_inputs(
                        previous_driver_source=FROM_SCRATCH_DRIVER,
                        candidate_report={"validation_passed": False},
                        media_manifest=[],
                        public_inputs=public_inputs,
                        condition="from-scratch",
                        previous_attempt=0,
                    )

    def test_repair_rejects_unsafe_probe_but_still_requests_repaired_source(self) -> None:
        public_inputs = build_public_generation_inputs(
            self.package,
            self.design,
            condition="from-scratch",
        )
        client = FakeJsonGenerator(
            {
                "repair_prepare": {
                    "repair_plan": ["inspect runtime"],
                    "probe_requests": [
                        {"probe_id": "unsafe", "script": "import sys\nprint(sys.path)"}
                    ],
                },
                "repair": {
                    "driver_filename": "driver.py",
                    "driver_source": FROM_SCRATCH_DRIVER,
                },
            }
        )

        result = repair_with_probes(
            client,
            package=self.package,
            previous_driver_source=FROM_SCRATCH_DRIVER,
            candidate_report={"validation_passed": False},
            media_manifest=[],
            public_inputs=public_inputs,
            condition="from-scratch",
            previous_attempt=0,
            workspace=Path(self.temporary.name) / "unsafe-repair-probe",
        )

        self.assertEqual(result.probe_results, ())
        preparation = client.inputs[1]["repair_prepare"]
        self.assertEqual(preparation["probe_requests"], [])
        self.assertEqual(preparation["rejected_probe_requests"][0]["probe_id"], "unsafe")
        self.assertIn("forbidden", preparation["rejected_probe_requests"][0]["message"])

    def test_redaction_removes_definition_not_candidate_facing_private_case_keys(self) -> None:
        report = {
            "private_case_id": "opaque",
            "private_case_count": 3,
            "passed_private_case_count": 1,
            "private_guard": {"kind": "hidden"},
            "criterion_passed": False,
            "measurement_value": 0.7,
        }
        redacted = redact_candidate_report(report)
        self.assertEqual(redacted["private_case_id"], "opaque")
        self.assertEqual(redacted["private_case_count"], 3)
        self.assertEqual(redacted["passed_private_case_count"], 1)
        self.assertNotIn("private_guard", redacted)
        self.assertEqual(redacted["measurement_value"], 0.7)

    def test_repair_compacts_dense_samples_but_keeps_complete_trial_results(self) -> None:
        samples = [
            {
                "time": float(index),
                "qpos": [index, index + 1],
                "qvel": [0.1, 0.2],
                "ctrl": [0.3, 0.4],
                "joint_positions": {"joint": float(index)},
                "body_positions": {"target": [float(index), 0.0, 0.2]},
                "site_positions": {"tip": [0.1, 0.2, 0.3]},
                "body_quaternions": {"noise": list(range(1000))},
                "contacts": [{"geom1": "finger", "geom2": "object"}],
            }
            for index in range(20)
        ]
        report = {
            "validation_passed": False,
            "trials": [
                {
                    "case_id": "case-1",
                    "measurement_value": 0.5,
                    "criterion_passed": False,
                    "guard_outcomes": {"guard_actuator_and_physics_step": True},
                    "physical_evidence": {
                        "step_count": 400,
                        "samples": samples,
                    },
                }
            ],
        }

        inputs = build_repair_inputs(
            previous_driver_source=FROM_SCRATCH_DRIVER,
            candidate_report=report,
            media_manifest=[],
            public_inputs={},
            condition="from-scratch",
            previous_attempt=0,
        )

        compact_report = inputs["candidate_report"]
        trial = compact_report["trials"][0]
        physical = trial["physical_evidence"]
        self.assertEqual(trial["measurement_value"], 0.5)
        self.assertFalse(trial["criterion_passed"])
        self.assertEqual(physical["step_count"], 400)
        self.assertEqual(physical["sample_count"], 20)
        self.assertTrue(physical["dense_samples_compacted"])
        self.assertEqual(physical["initial_sample"]["qpos"], [0, 1])
        self.assertEqual(physical["final_sample"]["qpos"], [19, 20])
        self.assertEqual(physical["initial_sample"]["contact_count"], 1)
        self.assertNotIn("samples", physical)
        self.assertNotIn("body_quaternions", physical["initial_sample"])
        self.assertIn("samples", report["trials"][0]["physical_evidence"])
        self.assertLess(len(json.dumps(inputs)), 10_000)
        focus = inputs["repair_focus_summary"]
        self.assertEqual(focus["failed_trials"][0]["case_id"], "case-1")
        self.assertEqual(focus["failed_trials"][0]["measurement_value"], 0.5)
        self.assertEqual(
            focus["failed_trials"][0]["physical_evidence"]["endpoint_state"][
                "joint_positions"
            ]["final"]["joint"],
            19.0,
        )
        self.assertEqual(focus["passed_trials"], [])
        self.assertIn("complete authoritative", focus["usage"])


if __name__ == "__main__":
    unittest.main()
