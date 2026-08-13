"""AutoAdapter 1.0-style direct MuJoCo validation driven by a Blue suite."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np


class LegacyValidationError(RuntimeError):
    pass


def _load_driver(path: Path):
    name = f"demo2_driver_{path.parent.name.replace('-', '_')}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LegacyValidationError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "build", None)):
        raise LegacyValidationError("driver.py must define callable build()")
    return module


def _compare(actual: float, comparator: str, threshold: float) -> bool:
    if comparator == "<=":
        return actual <= threshold
    if comparator == "<":
        return actual < threshold
    if comparator == ">=":
        return actual >= threshold
    if comparator == ">":
        return actual > threshold
    raise LegacyValidationError(f"unsupported comparator: {comparator}")


def _call_and_measure(robot: Any, test: Mapping[str, Any]) -> tuple[Any, float, str]:
    method_name = str(test["method"])
    method = getattr(robot, method_name, None)
    if not callable(method):
        raise LegacyValidationError(
            f"driver.build() result does not expose sealed method {method_name}"
        )
    inputs = dict(test["inputs"])
    duration = float(inputs.get("duration", 2.0))
    measurement = test["measurement"]
    if method_name == "move_joints":
        target = np.asarray(inputs["target"], dtype=float)
        returned = method(target, duration=duration)
        reached = np.asarray(robot.get_joint_positions(), dtype=float)
        actual = float(np.linalg.norm(reached - target))
        detail = (
            f"target={target.tolist()}, reached={reached.tolist()}, "
            f"l2_error={actual:.6f} rad, returned={returned}"
        )
    elif method_name == "move_to_cartesian":
        target = np.asarray(inputs["position"], dtype=float)
        returned = method(target, duration=duration)
        reached = np.asarray(robot.get_ee_pose()[0], dtype=float)
        actual = float(np.linalg.norm(reached - target))
        detail = (
            f"target={target.tolist()}, reached={reached.tolist()}, "
            f"l2_error={actual:.6f} m, returned={returned}"
        )
    elif method_name in {"stand_up", "sit"}:
        returned = method(duration=duration)
        actual = float(robot.get_body_height())
        detail = f"body_height={actual:.6f} m, returned={returned}"
    else:
        raise LegacyValidationError(f"no 1.0 direct probe for method {method_name}")
    if not math.isfinite(actual):
        raise LegacyValidationError(f"{measurement} is not finite")
    return returned, actual, detail


def _install_frame_capture(robot: Any, frames: list[Any], *, stride: int = 10, cap: int = 300):
    original_step = robot.step
    tick = 0

    def captured_step(n: int = 1) -> None:
        nonlocal tick
        for _ in range(int(n)):
            original_step(1)
            tick += 1
            if tick % stride == 0 and len(frames) < cap:
                try:
                    frames.append(robot.render())
                except Exception:
                    pass

    robot.step = captured_step
    try:
        frames.append(robot.render())
    except Exception:
        pass
    return original_step


def validate_driver(
    workspace: Path,
    suite: Mapping[str, Any],
    *,
    record_video: bool = True,
) -> dict[str, Any]:
    """Side-load ``driver.py` and run Blue inputs using 1.0's direct-state pattern."""

    workspace = Path(workspace).resolve()
    driver_path = workspace / "driver.py"
    report_path = workspace / "validate_report.json"
    tests: list[dict[str, Any]] = []
    frames: list[Any] = []
    video_error: str | None = None
    started = time.time()
    old_cwd = Path.cwd()
    added_path = False
    try:
        if str(workspace) not in sys.path:
            sys.path.insert(0, str(workspace))
            added_path = True
        os.chdir(workspace)
        try:
            module = _load_driver(driver_path)
            built = module.build()
            tests.append({
                "test": "driver_build",
                "ok": True,
                "detail": f"driver.build() returned {type(built).__name__}",
                "metric": 1.0,
            })
            if callable(getattr(built, "close", None)):
                built.close()
        except Exception as exc:
            tests.append({
                "test": "driver_build",
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "metric": -1.0,
            })
            report = {
                "tests": tests,
                "all_ok": False,
                "structural_ok": False,
                "n_passed": 0,
                "n_total": 1,
                "duration_sec": time.time() - started,
            }
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return report

        for blue_test in suite["capability_tests"]:
            robot = None
            original_step = None
            try:
                robot = module.build()
                if record_video:
                    original_step = _install_frame_capture(robot, frames)
                _, actual, detail = _call_and_measure(robot, blue_test)
                threshold = float(blue_test["threshold"])
                comparator = str(blue_test["comparator"])
                ok = _compare(actual, comparator, threshold)
                tests.append({
                    "test": blue_test["capability_id"],
                    "capability_id": blue_test["capability_id"],
                    "method": blue_test["method"],
                    "case_id": blue_test["case_id"],
                    "standard_id": blue_test["standard_id"],
                    "ok": bool(ok),
                    "detail": f"{detail}; require {comparator} {threshold}",
                    "measurement": blue_test["measurement"],
                    "metric": actual,
                    "comparator": comparator,
                    "threshold": threshold,
                })
            except Exception as exc:
                tests.append({
                    "test": blue_test.get("capability_id", "unknown"),
                    "capability_id": blue_test.get("capability_id"),
                    "method": blue_test.get("method"),
                    "case_id": blue_test.get("case_id"),
                    "standard_id": blue_test.get("standard_id"),
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "measurement": blue_test.get("measurement"),
                    "metric": -1.0,
                    "comparator": blue_test.get("comparator"),
                    "threshold": blue_test.get("threshold"),
                })
            finally:
                if robot is not None:
                    if original_step is not None:
                        robot.step = original_step
                    if callable(getattr(robot, "close", None)):
                        robot.close()
    finally:
        os.chdir(old_cwd)
        if added_path:
            sys.path.remove(str(workspace))

    recording: str | None = None
    if record_video and frames:
        try:
            import imageio.v2 as imageio

            recordings = workspace / "recordings"
            recordings.mkdir(exist_ok=True)
            path = recordings / f"validate_framework_{int(time.time())}.mp4"
            imageio.mimsave(str(path), frames, fps=30, codec="libx264")
            recording = str(path.relative_to(workspace))
            for test in tests[1:]:
                test["recording"] = recording
        except Exception as exc:
            video_error = f"{type(exc).__name__}: {exc}"
    elif record_video:
        video_error = "MuJoCo renderer produced no frames in this process"

    behavior_tests = tests[1:]
    structural_ok = tests[0]["ok"] and all(test["metric"] >= 0 for test in behavior_tests)
    all_ok = bool(tests and all(test["ok"] for test in tests))
    report = {
        "tests": tests,
        "all_ok": all_ok,
        "structural_ok": bool(structural_ok),
        "n_passed": sum(1 for test in tests if test["ok"]),
        "n_total": len(tests),
        "duration_sec": time.time() - started,
        "recording": recording,
        "video_error": video_error,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def repair_feedback(report: Mapping[str, Any]) -> str:
    """Rich 1.0 GEN<-VALIDATE feedback, including actual values and standards."""

    failures: list[str] = []
    for test in report.get("tests", []):
        if test.get("ok"):
            continue
        failures.append(
            "- capability={capability}; method={method}; case={case}; "
            "actual={actual}; comparator={comparator}; threshold={threshold}; detail={detail}".format(
                capability=test.get("capability_id", test.get("test", "?")),
                method=test.get("method", "build"),
                case=test.get("case_id", "?"),
                actual=test.get("metric"),
                comparator=test.get("comparator", "?"),
                threshold=test.get("threshold", "?"),
                detail=test.get("detail", "?"),
            )
        )
    if report.get("recording"):
        failures.append(f"- validation video: {report['recording']}")
    return "\n".join(failures) or "No failing tests were reported."
