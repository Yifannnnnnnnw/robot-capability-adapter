from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from autoadapter2.driver_synthesis.generation import (
    GenerationError,
    build_public_generation_inputs,
    generate,
    study,
)
from autoadapter2.driver_synthesis.probe import (
    ProbeSourceError,
    ProbeBudget,
    audit_public_source,
    prepare_public_probe_workspace,
    public_asset_closure_manifest,
    run_probes,
)
from autoadapter2.driver_synthesis.repair import (
    RepairLimitError,
    build_repair_inputs,
    redact_candidate_report,
    repair_with_probes,
)
from autoadapter2.driver_synthesis.source_check import DriverSourceError, audit_driver_source
from autoadapter2.libraries import RobotPackage


FROM_SCRATCH_DRIVER = """
import mujoco


class GeneratedDriver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def drive(self, request):
        self.data.ctrl[0] = float(request["task_parameters"].get("target", 0.0))
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
            "capabilities": [{"capability_id": "cap-1", "method_name": "drive"}],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

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
                abi = client.inputs[0]["allowed_runtime_facts"]["public_invocation_abi"]
                self.assertEqual(abi["request_schema"]["task_id"], "string")
                self.assertEqual(abi["request_schema"]["task_parameters"], "object")
                self.assertFalse(abi["effect_catalog_or_task_effect_allowlist"])
                for inputs in client.inputs:
                    packed = repr(inputs)
                    self.assertNotIn("REFERENCE_DRIVER_SENTINEL", packed)
                    self.assertNotIn(str(self.package.reference_driver), packed)
                    self.assertNotIn("calibration_reference_source", packed)
                    self.assertNotIn("reference_driver", packed)

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

    def test_probe_isolation_and_nstep_fact_use_staged_public_scene(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
