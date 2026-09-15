"""Focused H1 scorer regression checks."""

from __future__ import annotations

import unittest

from robots.h1.task_evaluation import evaluate_h1_task


def _state() -> dict:
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return {
        "pelvis": {
            "position": [0.0, 0.0, 1.0], "rotation": identity,
            "linear_velocity": [0.0, 0.0, 0.0], "angular_velocity": [0.0, 0.0, 0.0],
        },
        "torso": {
            "position": [0.0, 0.0, 1.0], "rotation": identity,
            "linear_velocity": [0.0, 0.0, 0.0], "angular_velocity": [0.0, 0.0, 0.0],
        },
    }


class H1TaskEvaluationTest(unittest.TestCase):
    def test_wrong_right_forearm_contact_is_rejected(self) -> None:
        """A right-arm distractor contact cannot be hidden by a left-arm hit."""

        states = [_state(), _state(), _state()]
        samples = [
            {"time": 0.0, "state": states[0], "contacts": []},
            {
                "time": 1.0, "state": states[1],
                "contacts": [
                    ("left_elbow_link", "button_target_1"),
                    ("right_elbow_link", "button_target_0"),
                ],
            },
            {
                "time": 2.0, "state": states[2],
                "contacts": [("left_elbow_link", "button_target_1")],
            },
        ]
        spec = {
            "type": "h1_catalog_task",
            "task_id": "H1-T14",
            "target_index": 1,
            "bindings": {
                "pelvis": {"kind": "body", "name": "pelvis"},
                "torso": {"kind": "body", "name": "torso_link"},
            },
        }
        metrics = evaluate_h1_task(spec, {"target_index": 1}, samples, [0.0, 1.0, 2.0])
        by_name = {metric["check"]: metric for metric in metrics}
        self.assertTrue(by_name["completed_operation"]["ok"])
        self.assertFalse(by_name["forbidden_contact_samples"]["ok"])


if __name__ == "__main__":
    unittest.main()
