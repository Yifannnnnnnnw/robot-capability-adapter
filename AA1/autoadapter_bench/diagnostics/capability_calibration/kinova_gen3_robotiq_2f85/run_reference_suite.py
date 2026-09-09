"""Run the Kinova capability suite with the independent reference driver.

The reference driver is constructed inside each evaluator callback.  The
callback therefore runs only after the Framework has reset and settled the
canonical model/data pair for that case.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


AA1_ROOT = Path(__file__).resolve().parents[4]
if str(AA1_ROOT) not in sys.path:
    sys.path.insert(0, str(AA1_ROOT))

import mujoco  # noqa: E402

from autoadapter_bench.capability_eval import run_capability_case  # noqa: E402
from autoadapter_bench.reference_controls.kinova_gen3_robotiq_2f85.reference_arm import (  # noqa: E402
    ReferenceKinovaGen3RobotiqDriver,
)


ROBOT_ID = "kinova_gen3_robotiq_2f85"
SCENE_PATH = AA1_ROOT / "assets/mjcf/capabilities/kinova_gen3_robotiq_2f85/fixed_scene.xml"
SUITE_PATH = AA1_ROOT / "autoadapter_bench/spec/capabilities/kinova_gen3_robotiq_2f85/capability_validation_suite.json"
DEFAULT_OUTPUT = AA1_ROOT / "autoadapter_bench/diagnostics/capability_calibration/kinova_gen3_robotiq_2f85/framework_reference"


def _load_suite() -> dict[str, Any]:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    if payload.get("robot_configuration_id") != ROBOT_ID:
        raise ValueError("Kinova capability suite has the wrong robot id")
    if not isinstance(payload.get("cases"), list) or len(payload["cases"]) != 10:
        raise ValueError("Kinova capability suite must contain ten cases")
    return payload


def _compact(outcome: dict[str, Any]) -> dict[str, Any]:
    measurements = outcome.get("metrics", {}).get("measurements", {})
    return {
        "case_id": outcome.get("case_id"),
        "capability_id": outcome.get("capability_id"),
        "ok": bool(outcome.get("ok")),
        "score": float(outcome.get("score", 0.0)),
        "physics_steps": int(outcome.get("physics_steps", 0)),
        "sim_elapsed_s": float(outcome.get("sim_elapsed_s", 0.0)),
        "trace_path": outcome.get("trace_path"),
        "video_path": outcome.get("video_path"),
        "video_frames": int(outcome.get("n_frames", 0)),
        "driver_origin": outcome.get("driver_origin"),
        "reference": bool(outcome.get("reference")),
        "model_generated": outcome.get("model_generated"),
        "errors": list(outcome.get("errors", [])),
        "measurements": measurements,
    }


def run(case_ids: list[str] | None, output_dir: Path) -> dict[str, Any]:
    from autoadapter_bench.capability_eval import _write_json  # noqa: PLC0415

    suite = _load_suite()
    selected = set(case_ids) if case_ids else None
    cases = [
        case for case in suite["cases"] if selected is None or case["case_id"] in selected
    ]
    if selected is not None and {case["case_id"] for case in cases} != selected:
        missing = sorted(selected - {case["case_id"] for case in cases})
        raise ValueError(f"unknown Kinova capability case(s): {missing}")

    model = mujoco.MjModel.from_xml_path(str(SCENE_PATH))
    data = mujoco.MjData(model)
    canonical = SimpleNamespace(model=model, data=data)
    robot_definition = {
        "id": ROBOT_ID,
        "capability_profile": "autoadapter_bench/spec/capabilities/kinova_gen3_robotiq_2f85",
    }
    tests: list[dict[str, Any]] = []
    for case in cases:
        # Keep this construction inside execute: run_capability_case performs
        # reset and settling before invoking the callback.
        def execute(case: dict[str, Any] = case) -> Any:
            driver = ReferenceKinovaGen3RobotiqDriver(model=model, data=data)
            method = getattr(driver, case["method_name"])
            return method(request=case["request"])

        tests.append(
            run_capability_case(
                canonical,
                case,
                output_dir,
                robot_definition=robot_definition,
                execute=execute,
                driver_origin="reference",
                reference=True,
            )
        )

    result = {
        "artifact_type": "capability_validation_result",
        "schema_version": "1.0",
        "suite_path": str(SUITE_PATH),
        "robot_id": ROBOT_ID,
        "driver_origin": "reference",
        "reference": True,
        "model_generated": False,
        "output_dir": str(output_dir),
        "tests": tests,
        "all_ok": all(bool(item.get("ok")) for item in tests),
    }
    _write_json(output_dir / "suite_result.json", result)
    _write_json(
        output_dir.parent / "reference_suite_results.json",
        {
            "artifact_type": "kinova_reference_capability_summary",
            "schema_version": "1.0",
            "robot_id": ROBOT_ID,
            "driver_origin": "reference",
            "reference": True,
            "model_generated": False,
            "suite_result_path": str(output_dir / "suite_result.json"),
            "all_ok": bool(result["all_ok"]),
            "cases": [_compact(item) for item in tests],
        },
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.case_ids, args.output_dir)
    print(json.dumps({"all_ok": result["all_ok"], "cases": len(result["tests"])}, indent=2))
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
