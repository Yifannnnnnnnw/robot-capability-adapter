# SPDX-License-Identifier: Apache-2.0
"""Read trusted robot morphology and observation bindings from AA1's zoo."""
from pathlib import Path

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
        if mjcf_path is not None and (REPO_ROOT / robot["mjcf"]).resolve() == Path(mjcf_path).resolve():
            return robot
    return None
