"""Focused regression checks for the native X2 task verdicts."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from robots.skydio_x2.task_evaluation import evaluate_skydio_task  # noqa: E402


def _rotation(yaw: float) -> list[float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return [c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0]


def _orbit_trace() -> tuple[list[dict], list[float], float]:
    samples: list[dict] = []
    times = [index / 10.0 for index in range(601)]
    legacy_bearing_errors: list[float] = []
    for time in times:
        circuit_time = min(time, 10.0)
        angle = 2.0 * math.pi * circuit_time / 10.0
        position = [1.5 * math.cos(angle), 1.5 * math.sin(angle), 1.5]
        desired_yaw = math.atan2(-position[1], -position[0])
        # The first circuit is consistently 20 degrees off.  A legacy
        # whole-trace RMSE would be diluted below 12 degrees by the 50 s
        # correctly-oriented stationary tail.
        yaw = desired_yaw + math.radians(20.0) if time <= 10.0 else math.pi
        error = (yaw - desired_yaw + math.pi) % (2.0 * math.pi) - math.pi
        legacy_bearing_errors.append(error)
        samples.append({
            "state": {
                "base": {
                    "position": position,
                    "rotation": _rotation(yaw),
                    "linear_velocity": [0.0, 0.0, 0.0],
                }
            },
            "contacts": [],
        })
    legacy_rmse = math.degrees(math.sqrt(
        sum(error * error for error in legacy_bearing_errors)
        / len(legacy_bearing_errors)
    ))
    return samples, times, legacy_rmse


class SkydioTaskEvaluationTest(unittest.TestCase):
    def test_bad_circuit_orientation_cannot_be_diluted_by_stationary_tail(self) -> None:
        samples, times, legacy_rmse = _orbit_trace()
        self.assertLess(legacy_rmse, 12.0)
        metrics = evaluate_skydio_task(
            {
                "type": "skydio_x2_catalog_task",
                "task_id": "X2-T07",
                "bindings": {"base": {"kind": "body", "name": "x2"}},
                "orbit_radius_m": 1.5,
                "orbit_height_m": 1.5,
                "positive_direction": 1,
                "orbit_progress_threshold_rad": 2.0 * math.pi,
                "radius_threshold_m": 0.2,
                "height_threshold_m": 0.15,
                "bearing_rmse_threshold_deg": 12.0,
                "terminal_window_s": 2.0,
                "body_tilt_threshold_deg": 20.0,
            },
            {"center_xy_m": [0.0, 0.0]},
            samples,
            times,
        )
        bearing = next(metric for metric in metrics
                       if metric["check"] == "orbit_bearing_rmse_deg")
        self.assertGreater(bearing["value"], 12.0)
        self.assertFalse(bearing["ok"])


if __name__ == "__main__":
    unittest.main()
