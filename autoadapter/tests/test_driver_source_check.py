from __future__ import annotations

import unittest

from autoadapter2.driver_synthesis import DriverSourceError, audit_driver_source


SKELETON_DRIVER = """
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton

class GeneratedDriver(ArmSerialDLSSkeleton):
    def reach_target(self, request):
        return self.move_cartesian(request["target"])

def build(model, data, spec):
    return GeneratedDriver.from_session(model=model, data=data, spec=spec)
"""

FROM_SCRATCH_DRIVER = """
import mujoco

class GeneratedDriver:
    def __init__(self, model, data):
        self.model = model
        self.data = data

    def hold_posture(self, request):
        target = request["target"]
        self.data.ctrl[:] = target
        mujoco.mj_step(self.model, self.data)

def build(model, data):
    return GeneratedDriver(model, data)
"""


class DriverSourceCheckTests(unittest.TestCase):
    def test_condition_specific_sources_pass(self) -> None:
        skeleton = audit_driver_source(
            SKELETON_DRIVER,
            condition="skeleton-assisted",
            capability_methods=["reach_target"],
        )
        scratch = audit_driver_source(
            FROM_SCRATCH_DRIVER,
            condition="from-scratch",
            capability_methods=["hold_posture"],
        )

        self.assertTrue(skeleton.imports_trusted_skeleton)
        self.assertFalse(scratch.imports_trusted_skeleton)

    def test_direct_qpos_write_is_rejected(self) -> None:
        source = FROM_SCRATCH_DRIVER.replace(
            "self.data.ctrl[:] = target",
            "self.data.qpos[:] = target\n        self.data.ctrl[:] = target",
        )

        with self.assertRaisesRegex(DriverSourceError, "direct state write"):
            audit_driver_source(
                source,
                condition="from-scratch",
                capability_methods=["hold_posture"],
            )

    def test_model_loading_and_reset_are_rejected(self) -> None:
        for forbidden in (
            "mujoco.MjModel.from_xml_path('easy.xml')",
            "mujoco.MjModel.from_xml_string('<mujoco/>')",
            "mujoco.mj_resetData(self.model, self.data)",
        ):
            with self.subTest(forbidden=forbidden):
                source = FROM_SCRATCH_DRIVER.replace(
                    "self.data.ctrl[:] = target",
                    forbidden + "\n        self.data.ctrl[:] = target",
                )
                with self.assertRaises(DriverSourceError):
                    audit_driver_source(
                        source,
                        condition="from-scratch",
                        capability_methods=["hold_posture"],
                    )

    def test_indirect_state_mutation_is_rejected(self) -> None:
        for mutation in (
            "self.data.qpos.fill(0.0)",
            "__import__('numpy').copyto(self.data.qvel, target)",
            "self.model.geom_friction[:] = 100.0",
        ):
            with self.subTest(mutation=mutation):
                source = FROM_SCRATCH_DRIVER.replace(
                    "self.data.ctrl[:] = target",
                    mutation + "\n        self.data.ctrl[:] = target",
                )
                with self.assertRaises(DriverSourceError):
                    audit_driver_source(
                        source,
                        condition="from-scratch",
                        capability_methods=["hold_posture"],
                    )

    def test_model_site_position_write_is_rejected(self) -> None:
        source = FROM_SCRATCH_DRIVER.replace(
            "self.data.ctrl[:] = target",
            "self.model.site_pos[0, 0] = 100.0\n        self.data.ctrl[:] = target",
        )

        with self.assertRaisesRegex(DriverSourceError, "direct model write"):
            audit_driver_source(
                source,
                condition="from-scratch",
                capability_methods=["hold_posture"],
            )

    def test_internal_mujoco_step_path_is_rejected(self) -> None:
        for source in (
            FROM_SCRATCH_DRIVER.replace(
                "import mujoco", "from mujoco._functions import mj_step"
            ).replace("mujoco.mj_step", "mj_step"),
            FROM_SCRATCH_DRIVER.replace(
                "mujoco.mj_step(self.model, self.data)",
                "mujoco._functions.mj_step(self.model, self.data)",
            ),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    DriverSourceError, "internal MuJoCo"
                ):
                    audit_driver_source(
                        source,
                        condition="from-scratch",
                        capability_methods=["hold_posture"],
                    )

    def test_private_framework_and_filesystem_imports_are_rejected(self) -> None:
        for statement in (
            "import autoadapter2.harness",
            "from autoadapter2.validation_compiler import run_ivc",
            "from pathlib import Path",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(DriverSourceError):
                    audit_driver_source(
                        statement + "\n" + FROM_SCRATCH_DRIVER,
                        condition="from-scratch",
                        capability_methods=["hold_posture"],
                    )

    def test_from_scratch_cannot_import_skeleton(self) -> None:
        with self.assertRaisesRegex(DriverSourceError, "must not import"):
            audit_driver_source(
                SKELETON_DRIVER,
                condition="from-scratch",
                capability_methods=["reach_target"],
            )

    def test_capability_must_be_explicitly_defined(self) -> None:
        with self.assertRaisesRegex(DriverSourceError, "does not explicitly define"):
            audit_driver_source(
                SKELETON_DRIVER,
                condition="skeleton-assisted",
                capability_methods=["different_capability"],
            )

    def test_candidate_cannot_dispatch_on_task_or_private_fields(self) -> None:
        for field in ("task_id", "task_parameters", "scene", "reset", "criteria"):
            with self.subTest(field=field):
                source = FROM_SCRATCH_DRIVER.replace(
                    'target = request["target"]',
                    f'target = request[{field!r}]',
                )
                with self.assertRaisesRegex(
                    DriverSourceError, "candidate capability request field"
                ):
                    audit_driver_source(
                        source,
                        condition="from-scratch",
                        capability_methods=["hold_posture"],
                        candidate_request_boundary=True,
                    )

    def test_private_reference_may_keep_internal_task_dispatch(self) -> None:
        source = FROM_SCRATCH_DRIVER.replace(
            'target = request["target"]',
            'target = request["task_parameters"]["target"]',
        )
        audit = audit_driver_source(
            source,
            condition="from-scratch",
            capability_methods=["hold_posture"],
        )
        self.assertGreater(audit.ctrl_references, 0)

    def test_candidate_cannot_recover_tracked_mujoco_step(self) -> None:
        exploits = (
            FROM_SCRATCH_DRIVER.replace(
                "mujoco.mj_step(self.model, self.data)",
                "mujoco.mj_step.__globals__['_original_step'](self.model, self.data)",
            ),
            FROM_SCRATCH_DRIVER.replace(
                "mujoco.mj_step(self.model, self.data)",
                "mujoco.mj_step.__closure__[0].cell_contents(self.model, self.data)",
            ),
            "import operator\n" + FROM_SCRATCH_DRIVER,
            FROM_SCRATCH_DRIVER.replace(
                'target = request["target"]',
                "target = __builtins__['float'](request['target'])",
            ),
        )

        for source in exploits:
            with self.subTest(source=source), self.assertRaises(DriverSourceError):
                audit_driver_source(
                    source,
                    condition="from-scratch",
                    capability_methods=["hold_posture"],
                    candidate_request_boundary=True,
                )

    def test_candidate_capability_cannot_remain_a_placeholder(self) -> None:
        source = FROM_SCRATCH_DRIVER.replace(
            '        target = request["target"]\n'
            "        self.data.ctrl[:] = target\n"
            "        mujoco.mj_step(self.model, self.data)",
            '        raise NotImplementedError("implement hold_posture")',
        )
        source += (
            "\ndef development_only(model, data):\n"
            "    data.ctrl[0] = 0.0\n"
            "    mujoco.mj_step(model, data)\n"
        )

        with self.assertRaisesRegex(DriverSourceError, "still a placeholder"):
            audit_driver_source(
                source,
                condition="from-scratch",
                capability_methods=["hold_posture"],
                candidate_request_boundary=True,
            )

    def test_candidate_may_build_through_an_explicit_factory(self) -> None:
        source = FROM_SCRATCH_DRIVER.replace(
            "def build(model, data):\n    return GeneratedDriver(model, data)",
            "def make_driver(model, data):\n"
            "    return GeneratedDriver(model, data)\n\n"
            "def build(model, data):\n"
            "    return make_driver(model, data)",
        )

        audit = audit_driver_source(
            source,
            condition="from-scratch",
            capability_methods=["hold_posture"],
            candidate_request_boundary=True,
        )
        self.assertGreater(audit.physics_step_references, 0)


if __name__ == "__main__":
    unittest.main()
