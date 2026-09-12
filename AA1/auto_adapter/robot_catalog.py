# SPDX-License-Identifier: Apache-2.0
"""Read trusted robot morphology and observation bindings from AA1's zoo."""
from pathlib import Path
import json

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKELETON_FOR_CLASS = {
    "arm": "ArmSerialDLSSkeleton",
    "quadruped": "QuadrupedPDGaitSkeleton",
    "dexterous_hand": "HandFingertipDLSSkeleton",
    "mobile_manipulator": "StretchMobileManipulationSkeleton",
    "bimanual": "BimanualSerialDLSSkeleton",
}


def find_robot_definition(robot_id: str | None = None,
                          mjcf_path: Path | None = None) -> dict | None:
    zoo = yaml.safe_load((REPO_ROOT / "autoadapter_bench/spec/robot_zoo.yaml").read_text())
    for robot in zoo["robots"]:
        if robot["id"] == robot_id:
            return robot
        if mjcf_path is not None:
            paths = [robot["mjcf"]]
            if robot.get("capability_mjcf"):
                paths.append(robot["capability_mjcf"])
            if any((REPO_ROOT / path).resolve() == Path(mjcf_path).resolve() for path in paths):
                return robot
    return None


def load_capability_design(robot: dict | None) -> dict | None:
    """Read the public contract selected by the trusted robot catalog."""
    if not robot or not robot.get("capability_profile"):
        return None
    path = REPO_ROOT / robot["capability_profile"] / "capability_design.json"
    design = json.loads(path.read_text())
    if design.get("robot_configuration_id") != robot["id"]:
        raise ValueError("capability design does not match the catalog robot")
    capabilities = design.get("capabilities", [])
    names = [item["method_name"] for item in capabilities]
    if not names or len(names) != len(set(names)):
        raise ValueError("capability design needs distinct required methods")
    return design


def load_capability_suite(robot: dict) -> dict:
    """Private physical conditions; only Framework and task evaluation use this."""
    path = REPO_ROOT / robot["capability_profile"] / "capability_validation_suite.json"
    suite = json.loads(path.read_text())
    if suite.get("robot_configuration_id") != robot["id"] or not suite.get("cases"):
        raise ValueError("missing or mismatched trusted capability conditions")
    return suite


def capability_generation_context(
    robot: dict | None,
    *,
    from_scratch: bool = False,
    design: dict | None = None,
) -> str:
    """Public generation input; never include the validation suite or references.

    ``design`` is supplied by the orchestrator after TGCD (or an explicit
    design-file load).  Keeping that object in memory avoids accidentally
    falling back to a trusted legacy contract after a dynamic design has been
    selected.  Calls that do not pass it retain the historical catalog path.
    """
    design = design if design is not None else load_capability_design(robot)
    if design is None:
        return ""
    signatures = "\n".join(
        f"def {cap['method_name']}(self, request): ..."
        for cap in design["capabilities"]
    )
    skeleton_name = None
    if robot:
        skeleton_name = robot.get("capability_skeleton")
        if not skeleton_name:
            skeleton_name = SKELETON_FOR_CLASS.get(robot.get("class"))
    implementation = (
        "Implement the required methods using your own MuJoCo/NumPy control code. "
        "Do not import supplied skeletons, retained policies, or reference drivers. "
        "Retain Robot.build_from_mjcf(mjcf_path)."
        if from_scratch else
        f"Define a generated Robot subclass of {skeleton_name or 'the trusted low-level skeleton'}. "
        "Fill its robot bindings AND implement every capability method below. "
        "A Spec-only driver is incomplete. The skeleton supplies low-level control, "
        "not these complete capabilities. Retain module-level build() returning "
        "Robot.from_mjcf(..., spec=...)."
    )
    return (
        "\n\nREQUIRED PUBLIC CAPABILITY CONTRACT (selected by the robot catalog):\n"
        + json.dumps(design, ensure_ascii=False)
        + "\nRequired interface:\n" + signatures + "\n" + implementation
        + "\nImplement request handling, feedback, ordering, holds and bounded failure "
        "as specified. Use the same model/data for all actions and observations. "
        "Advance real physics through actuator commands; never set live qpos/qvel "
        "to achieve an action. Kinematic calculations may use separate scratch data. "
        "Measure request deadlines and continuous holds with data.time (simulation "
        "seconds), never time.time(), monotonic(), or wall-clock sleeps. "
        "Do not read validation suites or reference control implementations.\n"
    )


def validate_capability_driver(driver, robot: dict, *, from_scratch: bool = False) -> dict:
    """Validate the required interface without letting candidates choose a profile."""
    from auto_adapter import skeletons

    design = load_capability_design(robot)
    if design is None:
        raise ValueError("catalog robot has no capability profile")
    if from_scratch:
        if isinstance(driver, skeletons.SkeletonBase):
            raise ValueError("from-scratch driver must not use a supplied skeleton")
    else:
        expected = getattr(skeletons, robot["capability_skeleton"], None)
        if expected is None or not isinstance(driver, expected):
            raise ValueError(f"catalog requires {robot['capability_skeleton']}")
        if type(driver) is expected:
            raise ValueError("Spec-only driver: build() must return the generated capability subclass")
    missing = [cap["method_name"] for cap in design["capabilities"]
               if not callable(getattr(driver, cap["method_name"], None))]
    if missing:
        raise ValueError("missing required capabilities: " + ", ".join(missing))
    return design
