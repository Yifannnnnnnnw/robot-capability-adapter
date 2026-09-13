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
            if (REPO_ROOT / robot["mjcf"]).resolve() == Path(mjcf_path).resolve():
                return robot
    return None


def capability_generation_context(
    robot: dict | None,
    *,
    design: dict,
    from_scratch: bool = False,
) -> str:
    """Render the current public design for generation and repair prompts."""
    if design is None:
        raise ValueError("capability_generation_context requires the current design")
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
        "\n\nREQUIRED PUBLIC CAPABILITY CONTRACT (current DESIGN artifact):\n"
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
