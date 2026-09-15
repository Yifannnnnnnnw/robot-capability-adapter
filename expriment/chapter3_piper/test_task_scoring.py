"""Focused fixture checks for the experiment-only Chapter 3 scorers.

The short records are shaped like trusted ``DemoTrace`` rows.  They exercise
predicate regressions only and never substitute for an experiment run.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "AA1"))

from auto_adapter.demo_evaluation import evaluate_demo_task  # noqa: E402
from task_scoring import install_experiment_scoring  # noqa: E402


def _body(position):
    return {"position": list(position)}


def _joint(value):
    return {"value": float(value)}


def _sample(time, state, contacts=()):
    return {
        "time": float(time),
        "state": state,
        "contacts": [list(pair) for pair in contacts],
    }


def _reach_spec():
    return {
        "type": "ee_at_target",
        "bindings": {"ee": {"kind": "site", "name": "ee_site"}},
        "target_parameter": "target_position",
        "metric": "euclidean_distance_3d",
        "comparator": "<=",
        "tolerance_m": 0.02,
        "temporal": "terminal_state",
    }


def _push_spec():
    return {
        "type": "object_pushed_to_target",
        "bindings": {
            "object": {"kind": "body", "name": "task_cube"},
            "ee": {"kind": "site", "name": "ee_site"},
        },
        "target_parameter": "target_position",
        "metric": "euclidean_distance_3d",
        "comparator": "<=",
        "tolerance_m": 0.02,
        "contact_bodies": ["link7", "link8"],
        "require_contact_driven_motion": True,
        "temporal": "terminal_state_after_push",
    }


def _pick_spec():
    return {
        "type": "object_picked_and_placed",
        "bindings": {
            "object": {"kind": "body", "name": "task_cube"},
            "left_finger": {"kind": "body", "name": "link7"},
            "right_finger": {"kind": "body", "name": "link8"},
            "gripper": {"kind": "joint", "name": "joint7"},
            "initial_support": {
                "kind": "body",
                "name": "chapter3_table_support",
            },
            "receiving_support": {
                "kind": "body",
                "name": "receiving_platform_support",
            },
        },
        "target_parameter": "target_position",
        "metric": "euclidean_distance_3d",
        "comparator": "<=",
        "tolerance_m": 0.07,
        "gripper_bodies": ["link7", "link8"],
        "required_phases": ["grasp", "lift", "release"],
        "temporal": "terminal_state_after_release",
    }


def _pick_state(object_position, gripper_value=0.0):
    return {
        "object": _body(object_position),
        "left_finger": _body([0.0, -0.02, 0.3]),
        "right_finger": _body([0.0, 0.02, 0.3]),
        "gripper": _joint(gripper_value),
        "initial_support": _body([0.45, 0.0, 0.18]),
        "receiving_support": _body([0.50, 0.0, 0.23]),
    }


def _genuine_pick_samples():
    return [
        _sample(
            0.0,
            _pick_state([0.40, 0.0, 0.205], 0.028),
            [("chapter3_table_support", "task_cube")],
        ),
        _sample(
            0.1,
            _pick_state([0.40, 0.0, 0.205], 0.007),
            [
                ("chapter3_table_support", "task_cube"),
                ("link7", "task_cube"),
                ("link8", "task_cube"),
            ],
        ),
        _sample(
            0.2,
            _pick_state([0.42, 0.0, 0.235], 0.007),
            [("link7", "task_cube"), ("link8", "task_cube")],
        ),
        _sample(
            0.3,
            _pick_state([0.50, 0.0, 0.285], 0.028),
            [("receiving_platform_support", "task_cube")],
        ),
    ]


class ExperimentScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_experiment_scoring()

    def test_reach_uses_inclusive_terminal_3d_distance(self):
        spec = _reach_spec()
        parameters = {"target_position": [0.40, 0.0, 0.30]}
        at_boundary = [
            _sample(0.0, {"ee": _body([0.20, 0.0, 0.20])}),
            _sample(0.1, {"ee": _body([0.40, 0.0, 0.32])}),
        ]
        result = evaluate_demo_task(spec, parameters, at_boundary)
        self.assertIs(result["physical_task_success"], True)

        outside_in_z = copy.deepcopy(at_boundary)
        outside_in_z[-1]["state"]["ee"] = _body([0.40, 0.0, 0.32001])
        self.assertIs(
            evaluate_demo_task(spec, parameters, outside_in_z)[
                "physical_task_success"
            ],
            False,
        )

    def test_push_genuine_contact_motion_reaches_terminal_goal(self):
        spec = _push_spec()
        parameters = {"target_position": [0.50, 0.0, 0.205]}
        genuine_contact_push = [
            _sample(
                0.0,
                {
                    "object": _body([0.40, 0.0, 0.205]),
                    "ee": _body([0.36, 0.0, 0.205]),
                },
            ),
            _sample(
                0.1,
                {
                    "object": _body([0.45, 0.0, 0.205]),
                    "ee": _body([0.44, 0.0, 0.205]),
                },
                [("link7", "task_cube")],
            ),
            _sample(
                0.2,
                {
                    "object": _body([0.50, 0.0, 0.205]),
                    "ee": _body([0.49, 0.0, 0.205]),
                },
                [("link7", "task_cube")],
            ),
        ]
        result = evaluate_demo_task(spec, parameters, genuine_contact_push)
        self.assertIs(result["physical_task_success"], True)

    def test_push_rejects_goal_without_contact_driven_progress(self):
        spec = _push_spec()
        parameters = {"target_position": [0.50, 0.0, 0.205]}
        object_at_goal_without_contact = [
            _sample(
                0.0,
                {
                    "object": _body([0.40, 0.0, 0.205]),
                    "ee": _body([0.36, 0.0, 0.205]),
                },
            ),
            _sample(
                0.1,
                {
                    "object": _body([0.50, 0.0, 0.205]),
                    "ee": _body([0.49, 0.0, 0.205]),
                },
            ),
        ]
        self.assertIs(
            evaluate_demo_task(spec, parameters, object_at_goal_without_contact)[
                "physical_task_success"
            ],
            False,
        )

        contact_only_after_artificial_motion = copy.deepcopy(
            object_at_goal_without_contact
        )
        contact_only_after_artificial_motion.append(
            _sample(
                0.2,
                {
                    "object": _body([0.50, 0.0, 0.205]),
                    "ee": _body([0.49, 0.0, 0.205]),
                },
                [("link7", "task_cube")],
            )
        )
        self.assertIs(
            evaluate_demo_task(
                spec, parameters, contact_only_after_artificial_motion
            )["physical_task_success"],
            False,
        )

    def test_pick_genuine_grasp_lift_release_reaches_terminal_goal(self):
        result = evaluate_demo_task(
            _pick_spec(),
            {"target_position": [0.50, 0.0, 0.285]},
            _genuine_pick_samples(),
        )
        self.assertIs(result["physical_task_success"], True)

    def test_pick_rejects_held_at_goal_no_contact_and_no_lift(self):
        spec = _pick_spec()
        parameters = {"target_position": [0.50, 0.0, 0.285]}

        held_at_goal = _genuine_pick_samples()
        held_at_goal[-1]["contacts"] = [
            ["link7", "task_cube"],
            ["link8", "task_cube"],
        ]
        self.assertIs(
            evaluate_demo_task(spec, parameters, held_at_goal)[
                "physical_task_success"
            ],
            False,
        )

        never_separated_from_table = _genuine_pick_samples()
        never_separated_from_table[2]["contacts"].append(
            ["chapter3_table_support", "task_cube"]
        )
        self.assertIs(
            evaluate_demo_task(spec, parameters, never_separated_from_table)[
                "physical_task_success"
            ],
            False,
        )

        artificial_no_contact_transport = [
            _sample(
                0.0,
                _pick_state([0.40, 0.0, 0.205], 0.028),
                [("chapter3_table_support", "task_cube")],
            ),
            _sample(0.1, _pick_state([0.42, 0.0, 0.235], 0.007)),
            _sample(
                0.2,
                _pick_state([0.50, 0.0, 0.285], 0.028),
                [("receiving_platform_support", "task_cube")],
            ),
        ]
        self.assertIs(
            evaluate_demo_task(spec, parameters, artificial_no_contact_transport)[
                "physical_task_success"
            ],
            False,
        )

        no_lift = [
            _sample(
                0.0,
                _pick_state([0.40, 0.0, 0.220], 0.028),
                [("chapter3_table_support", "task_cube")],
            ),
            _sample(
                0.1,
                _pick_state([0.40, 0.0, 0.220], 0.007),
                [
                    ("chapter3_table_support", "task_cube"),
                    ("link7", "task_cube"),
                    ("link8", "task_cube"),
                ],
            ),
            _sample(
                0.2,
                _pick_state([0.50, 0.0, 0.220], 0.007),
                [("link7", "task_cube"), ("link8", "task_cube")],
            ),
            _sample(0.3, _pick_state([0.50, 0.0, 0.220], 0.028)),
        ]
        terminal_error = evaluate_demo_task(spec, parameters, no_lift)
        self.assertLessEqual(
            terminal_error["task_metrics"][-1]["value"], spec["tolerance_m"]
        )
        self.assertIs(terminal_error["physical_task_success"], False)

    def test_contract_fields_are_validated_instead_of_ignored(self):
        parameters = {"target_position": [0.40, 0.0, 0.30]}
        samples = [
            _sample(0.0, {"ee": _body([0.30, 0.0, 0.30])}),
            _sample(0.1, {"ee": _body([0.40, 0.0, 0.30])}),
        ]
        changes = {
            "type": "unknown_reach_type",
            "metric": "euclidean_distance_2d",
            "comparator": "<",
            "temporal": "any_state",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                invalid = _reach_spec()
                invalid[field] = value
                result = evaluate_demo_task(invalid, parameters, samples)
                self.assertIsNone(result["physical_task_success"])
                self.assertTrue(result["evaluation_error"])

        invalid_binding = _reach_spec()
        invalid_binding["bindings"]["ee"]["kind"] = "body"
        result = evaluate_demo_task(invalid_binding, parameters, samples)
        self.assertIsNone(result["physical_task_success"])
        self.assertIn("binding 'ee'", result["evaluation_error"])

        invalid_push = _push_spec()
        invalid_push["require_contact_driven_motion"] = False
        push_samples = [
            _sample(
                0.0,
                {
                    "object": _body([0.40, 0.0, 0.205]),
                    "ee": _body([0.40, 0.0, 0.205]),
                },
            ),
            _sample(
                0.1,
                {
                    "object": _body([0.50, 0.0, 0.205]),
                    "ee": _body([0.50, 0.0, 0.205]),
                },
                [("link7", "task_cube")],
            ),
        ]
        self.assertIsNone(
            evaluate_demo_task(
                invalid_push,
                {"target_position": [0.50, 0.0, 0.205]},
                push_samples,
            )["physical_task_success"]
        )

        invalid_pick = _pick_spec()
        invalid_pick["required_phases"] = ["grasp", "release", "lift"]
        self.assertIsNone(
            evaluate_demo_task(
                invalid_pick,
                {"target_position": [0.50, 0.0, 0.285]},
                _genuine_pick_samples(),
            )["physical_task_success"]
        )


if __name__ == "__main__":
    unittest.main()
