from __future__ import annotations

import copy
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1.runtime import b1 as b1_runtime  # noqa: E402
from experiment.experiment1.runtime.b1 import load_fixed_bundle  # noqa: E402
from experiment.experiment1.runtime.fixed_bundles import (  # noqa: E402
    B1FixedBundleError,
    CAPABILITY_IDS_BY_ROBOT,
    METHOD_BY_CAPABILITY,
    validate_b1_fixed_validation_suite,
)


class Experiment1FixedBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = EXPERIMENT_ROOT / "manifest.json"
        cls.robot_ids = json.loads(
            (EXPERIMENT_ROOT / "components" / "robot-set.json").read_text(
                encoding="utf-8"
            )
        )["robot_configuration_ids"]
        cls.packages = {}
        for robot_id in cls.robot_ids:
            root = (
                REPOSITORY_ROOT
                / "autoadapter"
                / "libraries"
                / "robots"
                / robot_id
                / "1.0.0"
            )
            morphology = json.loads(
                (root / "morphology.json").read_text(encoding="utf-8")
            )
            catalog = json.loads(
                (root / "tasks" / "catalog.json").read_text(encoding="utf-8")
            )
            cls.packages[robot_id] = SimpleNamespace(
                root=root.resolve(),
                robot_configuration_id=robot_id,
                package_version=morphology["package_version"],
                snapshot_id=catalog["snapshot_id"],
            )
        cls.bundles = {
            robot_id: load_fixed_bundle(
                cls.manifest_path,
                cls.robot_ids,
                robot_id,
                cls.packages[robot_id],
            )
            for robot_id in cls.robot_ids
        }

    def test_all_four_bundles_load_with_twenty_three_capabilities_and_sixty_nine_cases(self) -> None:
        self.assertEqual(set(self.bundles), set(CAPABILITY_IDS_BY_ROBOT))
        self.assertEqual(
            sum(len(bundle.design["capabilities"]) for bundle in self.bundles.values()),
            23,
        )
        self.assertEqual(
            sum(len(bundle.suite["cases"]) for bundle in self.bundles.values()),
            69,
        )
        self.assertTrue(
            all(
                bundle.design["artifact_type"] == "b1_fixed_capability_design"
                and bundle.suite["artifact_type"] == "b1_fixed_validation_suite"
                for bundle in self.bundles.values()
            )
        )

    def test_each_capability_has_exactly_h1_h2_h3_and_matching_method(self) -> None:
        for robot_id, bundle in self.bundles.items():
            methods = {
                capability["capability_id"]: capability["method_name"]
                for capability in bundle.design["capabilities"]
            }
            coverage = Counter(
                (case["capability_id"], case["case_variant"])
                for case in bundle.suite["cases"]
            )
            self.assertEqual(tuple(methods), CAPABILITY_IDS_BY_ROBOT[robot_id])
            for capability_id in CAPABILITY_IDS_BY_ROBOT[robot_id]:
                self.assertEqual(methods[capability_id], METHOD_BY_CAPABILITY[capability_id])
                self.assertEqual(
                    {variant for (found_id, variant) in coverage if found_id == capability_id},
                    {"H1", "H2", "H3"},
                )
                self.assertTrue(
                    all(
                        count == 1
                        for (found_id, _variant), count in coverage.items()
                        if found_id == capability_id
                    )
                )
                cases = [
                    case
                    for case in bundle.suite["cases"]
                    if case["capability_id"] == capability_id
                ]
                self.assertEqual(len({json.dumps(case["request"], sort_keys=True) for case in cases}), 3)
                self.assertTrue(
                    all(case["method_name"] == methods[capability_id] for case in cases)
                )

    def test_corrected_artifacts_use_new_matching_identities(self) -> None:
        for robot_id, bundle in self.bundles.items():
            design_version = "v3" if robot_id == "robotstudio_so101" else "v2"
            suite_version = "v4" if robot_id == "robotstudio_so101" else "v3"
            pass_standard_version = (
                "v3" if robot_id == "robotstudio_so101" else "v2"
            )
            self.assertEqual(
                bundle.design["capability_design_id"],
                f"experiment1-b1-fixed-interface::{robot_id}::{design_version}",
            )
            self.assertEqual(
                bundle.suite["suite_id"],
                f"experiment1-b1-fixed-suite::{robot_id}::{suite_version}",
            )
            self.assertEqual(
                bundle.suite["pass_standard_id"],
                f"experiment1-b1-driver-validation-criteria-{pass_standard_version}",
            )
            self.assertEqual(
                bundle.fixed_capability_interface_id,
                bundle.design["capability_design_id"],
            )
            self.assertEqual(
                bundle.fixed_capability_pass_standard_id,
                bundle.suite["pass_standard_id"],
            )
            self.assertEqual(
                bundle.validation_suite_id,
                bundle.suite["suite_id"],
            )

    def test_so101_a6_wrist_roll_contract_is_closed_and_crosses_both_signs(self) -> None:
        bundle = self.bundles["robotstudio_so101"]
        capability = next(
            value
            for value in bundle.design["capabilities"]
            if value["capability_id"] == "A6"
        )
        schema = capability["request_schema"]
        self.assertEqual(
            schema,
            {
                "type": "object",
                "properties": {
                    "target_roll_rad": {
                        "type": "number",
                        "unit": "rad",
                        "frame": "joint",
                        "description": (
                            "Absolute non-wrapped SO-101 wrist-roll joint target."
                        ),
                        "minimum": -2.7438473,
                        "maximum": 2.7438473,
                    },
                    "max_duration_s": {
                        "type": "number",
                        "unit": "s",
                        "frame": "none",
                        "description": "Maximum execution duration.",
                        "minimum": 0.25,
                        "maximum": 8.0,
                    },
                },
                "required": ["target_roll_rad", "max_duration_s"],
                "additionalProperties": False,
            },
        )

        cases = [
            value
            for value in bundle.suite["cases"]
            if value["capability_id"] == "A6"
        ]
        self.assertEqual([value["case_id"] for value in cases], ["A6-H1", "A6-H2", "A6-H3"])
        targets = [value["request"]["target_roll_rad"] for value in cases]
        resets = [value["reset"]["joint_positions"]["wrist_roll"] for value in cases]
        self.assertTrue(any(value < 0.0 for value in targets))
        self.assertTrue(any(value > 0.0 for value in targets))
        self.assertTrue(all(target * reset < 0.0 for target, reset in zip(targets, resets)))
        self.assertTrue(
            all(
                value["reset"]["actuator_controls"]["wrist_roll"] == reset
                for value, reset in zip(cases, resets)
            )
        )
        expected_binding = {
            "contract_id": "A6",
            "side_effect_guard_profile": "so101",
            "joint_name": "wrist_roll",
            "target_request_key": "target_roll_rad",
            "target_tolerance_rad": 0.03,
            "continuous_hold_s": 0.25,
            "guarded_joint_names": [
                "shoulder_pan",
                "shoulder_lift",
                "elbow_flex",
                "wrist_flex",
                "gripper",
            ],
            "guarded_joint_tolerance_rad": 0.03,
        }
        self.assertTrue(
            all(value["binding"]["parameters"] == expected_binding for value in cases)
        )

    def test_every_inline_scene_exists_inside_its_package(self) -> None:
        for robot_id, bundle in self.bundles.items():
            package = self.packages[robot_id]
            assets = (package.root / "assets").resolve()
            for case in bundle.suite["cases"]:
                scene = (package.root / case["scene_entrypoint"]).resolve()
                scene.relative_to(assets)
                self.assertTrue(scene.is_file(), scene)
                self.assertEqual(scene.suffix, ".xml")

    def test_named_contact_targets_exist_in_each_scene(self) -> None:
        def named_geoms(scene: Path, visited: set[Path] | None = None) -> set[str]:
            visited = set() if visited is None else visited
            scene = scene.resolve()
            if scene in visited:
                return set()
            visited.add(scene)
            root = ET.parse(scene).getroot()
            names = {
                str(geom.attrib["name"])
                for geom in root.iter("geom")
                if geom.attrib.get("name")
            }
            for include in root.iter("include"):
                filename = include.attrib.get("file")
                if filename:
                    names.update(named_geoms(scene.parent / filename, visited))
            return names

        for robot_id, bundle in self.bundles.items():
            package = self.packages[robot_id]
            for case in bundle.suite["cases"]:
                targets = case["binding"]["parameters"].get("target_geom_names")
                if isinstance(targets, dict):
                    expected = {
                        name for values in targets.values() for name in values
                    }
                elif isinstance(targets, list):
                    expected = set(targets)
                else:
                    continue
                scene = package.root / case["scene_entrypoint"]
                self.assertLessEqual(
                    expected,
                    named_geoms(scene),
                    f"{robot_id} {case['case_id']} contact target",
                )

    def test_validator_rejects_missing_hidden_case(self) -> None:
        robot_id = "robotstudio_so101"
        package = self.packages[robot_id]
        bundle = self.bundles[robot_id]
        invalid = copy.deepcopy(bundle.suite)
        invalid["cases"].pop()
        with self.assertRaises(B1FixedBundleError):
            validate_b1_fixed_validation_suite(
                invalid,
                package=package,
                design=bundle.design,
            )

    def test_loader_rejects_index_identity_that_disagrees_with_artifact(self) -> None:
        robot_id = "robotstudio_so101"
        bundle = self.bundles[robot_id]
        metadata = {
            robot_id: {
                "fixed_capability_interface_id": "wrong-interface-id",
                "fixed_capability_pass_standard_id": bundle.suite[
                    "pass_standard_id"
                ],
                "validation_suite_id": bundle.suite["suite_id"],
            }
        }
        with mock.patch.object(
            b1_runtime,
            "_bundle_paths",
            return_value=(
                bundle.container_path,
                {robot_id: (bundle.design_path, bundle.suite_path)},
                metadata,
            ),
        ):
            with self.assertRaisesRegex(
                b1_runtime.B1RunError, "disagrees with its artifact"
            ):
                load_fixed_bundle(
                    self.manifest_path,
                    [robot_id],
                    robot_id,
                    self.packages[robot_id],
                )


if __name__ == "__main__":
    unittest.main()
