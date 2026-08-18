from __future__ import annotations

import unittest

from autoadapter2.driver_synthesis import DriverSourceError, audit_driver_source


SKELETON_DRIVER = """
from autoadapter2.trusted_skeletons import ArmSerialDLSSkeleton

class GeneratedDriver(ArmSerialDLSSkeleton):
    def reach_target(self, request):
        return self.move_cartesian(request["task_parameters"]["target"])

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
        target = request["task_parameters"]["target"]
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


if __name__ == "__main__":
    unittest.main()
