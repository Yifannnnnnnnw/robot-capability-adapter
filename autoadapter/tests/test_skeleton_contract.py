from __future__ import annotations

import unittest

from autoadapter2.driver_synthesis import (
    SessionBoundSkeleton,
    SkeletonContractError,
    discover_primitives,
    validate_capability_names,
    validate_explicit_capability_methods,
)


class ExampleArmSkeleton(SessionBoundSkeleton):
    def move_cartesian(self, target_xyz, duration=2.0):
        """Move the end effector through actuator-driven control."""

        self.last_motion = (target_xyz, duration)
        return True

    def get_ee_pose(self):
        """Read the current end-effector pose."""

        return (0.0, 0.0, 0.0)

    def step(self, n=1):
        self.steps = getattr(self, "steps", 0) + n

    def _internal_solver(self):
        raise AssertionError("private helpers are not generation primitives")


class GeneratedInspectionDriver(ExampleArmSkeleton):
    def approach_target_safely(self, target_xyz, duration=2.0):
        """Approach a target using the trusted Cartesian primitive."""

        return super().move_cartesian(target_xyz, duration=duration)

    def observe_tool_pose(self):
        """Return the tool pose through the trusted observation primitive."""

        return super().get_ee_pose()


class SkeletonContractTests(unittest.TestCase):
    def test_session_constructor_preserves_framework_objects(self) -> None:
        model = object()
        data = object()
        spec = {"robot": "example"}

        driver = GeneratedInspectionDriver.from_session(
            model=model,
            data=data,
            spec=spec,
        )

        self.assertIs(driver.model, model)
        self.assertIs(driver.data, data)
        self.assertIs(driver.spec, spec)

    def test_primitive_discovery_is_capability_neutral(self) -> None:
        primitives = discover_primitives(ExampleArmSkeleton)

        self.assertEqual(
            {item.name for item in primitives},
            {"get_ee_pose", "move_cartesian"},
        )

    def test_sealed_design_names_can_compose_primitives(self) -> None:
        methods = validate_explicit_capability_methods(
            GeneratedInspectionDriver,
            ("approach_target_safely", "observe_tool_pose"),
        )
        driver = GeneratedInspectionDriver.from_session(
            model=object(),
            data=object(),
            spec=object(),
        )

        self.assertEqual(
            [item.name for item in methods],
            ["approach_target_safely", "observe_tool_pose"],
        )
        self.assertTrue(driver.approach_target_safely([0.1, 0.2, 0.3], duration=1.5))
        self.assertEqual(driver.last_motion, ([0.1, 0.2, 0.3], 1.5))
        self.assertEqual(driver.observe_tool_pose(), (0.0, 0.0, 0.0))

    def test_inherited_primitive_does_not_count_as_generated_capability(self) -> None:
        with self.assertRaisesRegex(
            SkeletonContractError,
            "must explicitly define capability method 'move_cartesian'",
        ):
            validate_explicit_capability_methods(
                GeneratedInspectionDriver,
                ("move_cartesian",),
            )

    def test_reserved_and_invalid_names_fail_before_generation(self) -> None:
        for name in ("step", "build", "not-a-method"):
            with self.subTest(name=name):
                with self.assertRaises(SkeletonContractError):
                    validate_capability_names((name,))


if __name__ == "__main__":
    unittest.main()
