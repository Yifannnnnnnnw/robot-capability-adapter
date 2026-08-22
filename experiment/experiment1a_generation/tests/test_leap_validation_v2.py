from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import mujoco
import numpy as np


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from autoadapter2.harness import run_private_suite  # noqa: E402
from autoadapter2.harness.session import apply_framework_reset  # noqa: E402
from autoadapter2.libraries import load_indexed_robot_package  # noqa: E402
from experiment.experiment1a_generation.runtime.b1 import load_fixed_bundle  # noqa: E402


FINGERTIP_SITES = ("if_tip", "mf_tip", "rf_tip", "th_tip")
L3_TARGET_JOINT_CONFIGURATIONS = {
    "L3-H1": [
        0.154, 0.012, 0.066, 0.048,
        0.072, -0.012, 0.060, 0.042,
        0.078, 0.009, 0.072, 0.045,
        0.072, 0.048, 0.066, 0.006,
    ],
    "L3-H2": [
        0.175, -0.0125, 0.070, 0.050,
        0.075, 0.015, 0.065, 0.045,
        0.080, -0.010, 0.075, 0.040,
        0.075, 0.050, 0.070, -0.010,
    ],
    "L3-H3": [
        0.129, 0.028, 0.063, 0.042,
        0.091, -0.028, 0.077, 0.056,
        0.084, 0.0245, 0.070, 0.063,
        0.077, 0.042, 0.084, 0.021,
    ],
}
L6_TARGET_JOINT_CONFIGURATIONS = {
    "L6-H1": {"index": [0.188481, 0.017696, 0.106177, 0.070784]},
    "L6-H2": {
        "middle": [0.095621, -0.028686, 0.095621, 0.066934],
        "thumb": [0.115888, 0.115888, 0.139066, 0.046355],
    },
    "L6-H3": {
        "index": [0.158079, -0.025323, 0.126614, 0.063307],
        "ring": [0.095434, 0.020822, 0.095434, 0.069406],
    },
}


STATIC_DRIVER = textwrap.dedent(
    """
    import mujoco

    class Driver:
        def __init__(self, model, data):
            self.model = model
            self.data = data

        def _step(self, count=1):
            self.data.ctrl[0] = float(self.data.ctrl[0])
            for _ in range(count):
                mujoco.mj_step(self.model, self.data)

        def move_hand_to_joint_pose(self, request):
            self._step()

        def trace_hand_joint_path(self, request):
            self._step()

        def reach_fingertips_to_targets(self, request):
            self._step()

        def establish_fingertip_contact_pattern(self, request):
            self._step()

        def hold_fingertip_contacts(self, request):
            steps = int(request["duration_s"] / self.model.opt.timestep) + 50
            self._step(steps)

        def move_fingertip_offset_and_return(self, request):
            self._step(500)

    def build(*, model, data):
        return Driver(model, data)
    """
)


ACTUATOR_DRIVER_TEMPLATE = textwrap.dedent(
    """
    import mujoco
    import numpy as np

    L6_SCALE = __L6_SCALE__
    FINGERS = {
        "index": ("if_tip", ("if_mcp", "if_rot", "if_pip", "if_dip")),
        "middle": ("mf_tip", ("mf_mcp", "mf_rot", "mf_pip", "mf_dip")),
        "ring": ("rf_tip", ("rf_mcp", "rf_rot", "rf_pip", "rf_dip")),
        "thumb": ("th_tip", ("th_cmc", "th_axl", "th_mcp", "th_ipl")),
    }

    class Driver:
        def __init__(self, model, data):
            self.model = model
            self.data = data
            self.palm_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, "palm"
            )

        def _command_fingertip(self, finger, target_world):
            site_name, joint_names = FINGERS[finger]
            site_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_SITE, site_name
            )
            joint_ids = [
                mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
                for name in joint_names
            ]
            dofs = [int(self.model.jnt_dofadr[joint_id]) for joint_id in joint_ids]
            qpos_addresses = [
                int(self.model.jnt_qposadr[joint_id]) for joint_id in joint_ids
            ]
            actuator_ids = [
                mujoco.mj_name2id(
                    self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_act"
                )
                for name in joint_names
            ]
            jacp = np.zeros((3, self.model.nv), dtype=float)
            jacr = np.zeros((3, self.model.nv), dtype=float)
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, site_id)
            jacobian = jacp[:, dofs]
            error = np.asarray(target_world, dtype=float) - np.asarray(
                self.data.site_xpos[site_id], dtype=float
            )
            delta = jacobian.T @ np.linalg.solve(
                jacobian @ jacobian.T + 1.0e-5 * np.eye(3), error
            )
            current = np.asarray(self.data.qpos[qpos_addresses], dtype=float)
            command = current + np.clip(4.0 * delta, -0.25, 0.25)
            for actuator_id, value in zip(actuator_ids, command):
                lower, upper = self.model.actuator_ctrlrange[actuator_id]
                self.data.ctrl[actuator_id] = np.clip(value, lower, upper)

        def _step_to(self, targets_world, steps, *, physics_substeps=1):
            for _ in range(steps):
                for finger, target_world in targets_world.items():
                    self._command_fingertip(finger, target_world)
                mujoco.mj_step(
                    self.model, self.data, nstep=physics_substeps
                )

        def _palm_transform(self):
            position = np.asarray(
                self.data.xpos[self.palm_id], dtype=float
            ).copy()
            rotation = np.asarray(
                self.data.xmat[self.palm_id], dtype=float
            ).reshape(3, 3).copy()
            return position, rotation

        def move_hand_to_joint_pose(self, request):
            mujoco.mj_step(self.model, self.data)

        def trace_hand_joint_path(self, request):
            mujoco.mj_step(self.model, self.data)

        def reach_fingertips_to_targets(self, request):
            palm_position, palm_rotation = self._palm_transform()
            local_targets = np.asarray(
                request["target_fingertip_positions_palm_m"], dtype=float
            ).reshape(4, 3)
            targets_world = {
                finger: palm_position + palm_rotation @ target
                for finger, target in zip(FINGERS, local_targets)
            }
            self._step_to(
                targets_world,
                int(request["max_control_steps"]),
                physics_substeps=10,
            )

        def establish_fingertip_contact_pattern(self, request):
            mujoco.mj_step(self.model, self.data)

        def hold_fingertip_contacts(self, request):
            mujoco.mj_step(self.model, self.data)

        def move_fingertip_offset_and_return(self, request):
            palm_position, palm_rotation = self._palm_transform()
            del palm_position
            fingers = list(request["finger_names"])
            starts_world = {}
            targets_world = {}
            for finger, offset in zip(fingers, request["offsets_palm_m"]):
                site_id = mujoco.mj_name2id(
                    self.model, mujoco.mjtObj.mjOBJ_SITE, FINGERS[finger][0]
                )
                start = np.asarray(self.data.site_xpos[site_id], dtype=float).copy()
                starts_world[finger] = start
                targets_world[finger] = (
                    start
                    + L6_SCALE
                    * palm_rotation
                    @ np.asarray(offset, dtype=float)
                )
            if L6_SCALE >= 1.0:
                self._step_to(targets_world, 800)
                self._step_to(starts_world, 800)
            else:
                self._step_to(targets_world, 500)

    def build(*, model, data):
        return Driver(model, data)
    """
)
ACTUATOR_REFERENCE_DRIVER = ACTUATOR_DRIVER_TEMPLATE.replace("__L6_SCALE__", "1.0")
ONE_MILLIMETRE_DRIVER = ACTUATOR_DRIVER_TEMPLATE.replace("__L6_SCALE__", "0.04")


class LeapValidationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        robot_ids = json.loads(
            (EXPERIMENT_ROOT / "config" / "components" / "robot-set.json").read_text(
                encoding="utf-8"
            )
        )["robot_configuration_ids"]
        cls.package = load_indexed_robot_package(
            REPOSITORY_ROOT / "autoadapter", "leap_hand"
        )
        cls.bundle = load_fixed_bundle(
            EXPERIMENT_ROOT / "manifest.json",
            robot_ids,
            "leap_hand",
            cls.package,
        )

    def _run_capability(
        self, capability_id: str, driver_source: str, *, run_id: str
    ) -> dict:
        suite = dict(self.bundle.suite)
        suite["cases"] = [
            case
            for case in suite["cases"]
            if case["capability_id"] == capability_id
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = root / "driver.py"
            driver.write_text(driver_source, encoding="utf-8")
            return run_private_suite(
                package=self.package,
                design=self.bundle.design,
                suite=suite,
                driver_path=driver,
                condition="from-scratch",
                output_dir=root / "report",
                record_video=False,
                wall_timeout_s=30.0,
                run_id=run_id,
            )

    def test_scene_has_four_independent_aligned_mocap_contact_targets(self) -> None:
        scene = (
            self.package.root / "assets" / "leap_hand_kinematic_scene.xml"
        )
        model = mujoco.MjModel.from_xml_path(str(scene))
        expected = {
            ("if_tip", "b1_index_target_geom"),
            ("mf_tip", "b1_middle_target_geom"),
            ("rf_tip", "b1_ring_target_geom"),
            ("th_tip", "b1_thumb_target_geom"),
        }
        actual = {
            tuple(
                sorted(
                    (
                        mujoco.mj_id2name(
                            model, mujoco.mjtObj.mjOBJ_GEOM, int(model.pair_geom1[i])
                        ),
                        mujoco.mj_id2name(
                            model, mujoco.mjtObj.mjOBJ_GEOM, int(model.pair_geom2[i])
                        ),
                    )
                )
            )
            for i in range(model.npair)
        }
        self.assertEqual(actual, {tuple(sorted(pair)) for pair in expected})
        for finger in ("index", "middle", "ring", "thumb"):
            body_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, f"b1_{finger}_target_body"
            )
            self.assertGreaterEqual(int(model.body_mocapid[body_id]), 0)
            geom_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_GEOM, f"b1_{finger}_target_geom"
            )
            self.assertEqual(int(model.geom_contype[geom_id]), 2)
            self.assertEqual(int(model.geom_conaffinity[geom_id]), 1)
            self.assertEqual(
                int(model.geom_contype[geom_id])
                & int(model.geom_conaffinity[geom_id]),
                0,
            )

    def test_noncorresponding_fingertip_target_contact_is_not_masked(self) -> None:
        scene = self.package.root / "assets" / "leap_hand_kinematic_scene.xml"
        model = mujoco.MjModel.from_xml_path(str(scene))
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)

        fingertip_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, "mf_tip"
        )
        target_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, "b1_index_target_geom"
        )
        target_body_id = int(model.geom_bodyid[target_id])
        mocap_id = int(model.body_mocapid[target_body_id])
        target_center_from_mocap = (
            np.asarray(data.geom_xpos[target_id], dtype=float)
            - np.asarray(data.mocap_pos[mocap_id], dtype=float)
        )
        data.mocap_pos[mocap_id] = (
            np.asarray(data.geom_xpos[fingertip_id], dtype=float)
            - target_center_from_mocap
            + np.array([0.010, 0.0, 0.0])
        )
        mujoco.mj_forward(model, data)

        contacts = {
            frozenset((int(contact.geom1), int(contact.geom2)))
            for contact in data.contact
        }
        self.assertIn(frozenset((fingertip_id, target_id)), contacts)

    def test_l3_targets_are_fk_of_legal_joint_configurations(self) -> None:
        scene = self.package.root / "assets" / "leap_hand_kinematic_scene.xml"
        cases = {
            case["case_id"]: case
            for case in self.bundle.suite["cases"]
            if case["capability_id"] == "L3"
        }
        self.assertEqual(set(cases), set(L3_TARGET_JOINT_CONFIGURATIONS))
        for case_id, target_qpos in L3_TARGET_JOINT_CONFIGURATIONS.items():
            case = cases[case_id]
            model = mujoco.MjModel.from_xml_path(str(scene))
            data = mujoco.MjData(model)
            apply_framework_reset(mujoco, model, data, case["reset"])
            palm_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, "palm"
            )
            palm_position = np.asarray(data.xpos[palm_id], dtype=float).copy()
            palm_rotation = np.asarray(
                data.xmat[palm_id], dtype=float
            ).reshape(3, 3).copy()
            initial = np.concatenate(
                [
                    palm_rotation.T
                    @ (
                        np.asarray(
                            data.site_xpos[
                                mujoco.mj_name2id(
                                    model, mujoco.mjtObj.mjOBJ_SITE, site_name
                                )
                            ],
                            dtype=float,
                        )
                        - palm_position
                    )
                    for site_name in FINGERTIP_SITES
                ]
            )
            for joint_id, value in enumerate(target_qpos):
                self.assertGreaterEqual(value, float(model.jnt_range[joint_id, 0]))
                self.assertLessEqual(value, float(model.jnt_range[joint_id, 1]))
            data.qpos[:] = target_qpos
            mujoco.mj_forward(model, data)
            actual = np.concatenate(
                [
                    palm_rotation.T
                    @ (
                        np.asarray(
                            data.site_xpos[
                                mujoco.mj_name2id(
                                    model, mujoco.mjtObj.mjOBJ_SITE, site_name
                                )
                            ],
                            dtype=float,
                        )
                        - palm_position
                    )
                    for site_name in FINGERTIP_SITES
                ]
            )
            requested = np.asarray(
                case["request"]["target_fingertip_positions_palm_m"],
                dtype=float,
            )
            np.testing.assert_allclose(actual, requested, rtol=0.0, atol=1.0e-8)
            self.assertGreater(float(np.linalg.norm(initial - requested)), 0.025)

    def test_l3_actuator_only_reference_reaches_all_targets(self) -> None:
        report = self._run_capability(
            "L3",
            ACTUATOR_REFERENCE_DRIVER,
            run_id="leap-l3-actuator-reference",
        )
        self.assertEqual(len(report["trials"]), 3)
        for trial in report["trials"]:
            evidence = trial["physical_evidence"]
            self.assertEqual(evidence["step_count"], 500)
            self.assertFalse(evidence["direct_state_write_detected"])
            self.assertTrue(all(trial["guard_outcomes"].values()))
            self.assertEqual(trial["measurement_value"], 1.0)
            self.assertTrue(trial["trial_passed"])

    def test_l6_offsets_have_fk_reachability_and_disjoint_tolerances(self) -> None:
        scene = self.package.root / "assets" / "leap_hand_kinematic_scene.xml"
        cases = {
            case["case_id"]: case
            for case in self.bundle.suite["cases"]
            if case["capability_id"] == "L6"
        }
        finger_sites = {
            "index": "if_tip",
            "middle": "mf_tip",
            "ring": "rf_tip",
            "thumb": "th_tip",
        }
        finger_joints = {
            finger: names
            for finger, names in next(iter(cases.values()))["binding"][
                "parameters"
            ]["finger_joint_names"].items()
        }
        for case_id, configurations in L6_TARGET_JOINT_CONFIGURATIONS.items():
            case = cases[case_id]
            model = mujoco.MjModel.from_xml_path(str(scene))
            data = mujoco.MjData(model)
            apply_framework_reset(mujoco, model, data, case["reset"])
            palm_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_BODY, "palm"
            )
            palm_rotation = np.asarray(
                data.xmat[palm_id], dtype=float
            ).reshape(3, 3).copy()
            starts = {
                finger: np.asarray(
                    data.site_xpos[
                        mujoco.mj_name2id(
                            model,
                            mujoco.mjtObj.mjOBJ_SITE,
                            finger_sites[finger],
                        )
                    ],
                    dtype=float,
                ).copy()
                for finger in case["request"]["finger_names"]
            }
            for finger, expected_offset in zip(
                case["request"]["finger_names"],
                case["request"]["offsets_palm_m"],
            ):
                offset = np.asarray(expected_offset, dtype=float)
                self.assertGreater(float(np.linalg.norm(offset)), 0.020)
                for joint_name, value in zip(
                    finger_joints[finger], configurations[finger]
                ):
                    joint_id = mujoco.mj_name2id(
                        model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
                    )
                    self.assertGreaterEqual(
                        value, float(model.jnt_range[joint_id, 0])
                    )
                    self.assertLessEqual(
                        value, float(model.jnt_range[joint_id, 1])
                    )
                    data.qpos[int(model.jnt_qposadr[joint_id])] = value
                mujoco.mj_forward(model, data)
                site_id = mujoco.mj_name2id(
                    model,
                    mujoco.mjtObj.mjOBJ_SITE,
                    finger_sites[finger],
                )
                actual_offset = palm_rotation.T @ (
                    np.asarray(data.site_xpos[site_id], dtype=float)
                    - starts[finger]
                )
                np.testing.assert_allclose(
                    actual_offset, offset, rtol=0.0, atol=1.0e-7
                )
                apply_framework_reset(mujoco, model, data, case["reset"])

    def test_l6_actuator_only_reference_passes_all_cases(self) -> None:
        report = self._run_capability(
            "L6",
            ACTUATOR_REFERENCE_DRIVER,
            run_id="leap-l6-actuator-reference",
        )
        self.assertEqual(len(report["trials"]), 3)
        for trial in report["trials"]:
            evidence = trial["physical_evidence"]
            self.assertEqual(evidence["step_count"], 1600)
            self.assertFalse(evidence["direct_state_write_detected"])
            self.assertTrue(all(trial["guard_outcomes"].values()))
            self.assertTrue(trial["contact_integrity"]["passed"])
            self.assertEqual(trial["measurement_value"], 1.0)
            self.assertTrue(trial["trial_passed"])

    def test_l6_static_and_one_millimetre_controls_fail(self) -> None:
        static_report = self._run_capability(
            "L6", STATIC_DRIVER, run_id="leap-l6-static-negative"
        )
        self.assertEqual(
            [trial["measurement_value"] for trial in static_report["trials"]],
            [0.0, 0.0, 0.0],
        )

        one_mm_report = self._run_capability(
            "L6",
            ONE_MILLIMETRE_DRIVER,
            run_id="leap-l6-one-millimetre-negative",
        )
        for trial in one_mm_report["trials"]:
            evidence = trial["physical_evidence"]
            self.assertEqual(evidence["step_count"], 500)
            self.assertFalse(evidence["direct_state_write_detected"])
            self.assertTrue(all(trial["guard_outcomes"].values()))
            self.assertEqual(trial["measurement_value"], 0.0)
            self.assertFalse(trial["trial_passed"])

    def test_fixed_joint_l5_negative_control_fails_after_registered_event(self) -> None:
        suite = dict(self.bundle.suite)
        suite["cases"] = [
            case for case in suite["cases"] if case["case_id"] == "L5-H1"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = root / "driver.py"
            driver.write_text(STATIC_DRIVER, encoding="utf-8")
            report = run_private_suite(
                package=self.package,
                design=self.bundle.design,
                suite=suite,
                driver_path=driver,
                condition="from-scratch",
                output_dir=root / "report",
                record_video=False,
                wall_timeout_s=30.0,
                run_id="leap-l5-negative-control",
            )

        trial = report["trials"][0]
        evidence = trial["physical_evidence"]
        self.assertTrue(trial["method_invoked"])
        self.assertTrue(evidence["framework_preinvoke"]["all_required_contacts_held"])
        self.assertEqual(evidence["framework_preinvoke"]["step_count"], 50)
        self.assertEqual(len(evidence["framework_events"]), 1)
        self.assertTrue(evidence["framework_events"][0]["complete"])
        self.assertFalse(trial["trial_passed"])
        self.assertEqual(trial["measurement_value"], 0.0)


if __name__ == "__main__":
    unittest.main()
