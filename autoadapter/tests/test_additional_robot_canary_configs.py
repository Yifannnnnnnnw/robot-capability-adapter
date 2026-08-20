from __future__ import annotations

from pathlib import Path

import pytest

from autoadapter2.pipeline import ExperimentConfig


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
CANARIES = (
    ("franka_panda", "franka_panda-canary.json"),
    ("kinova_gen3_robotiq_2f85", "kinova_gen3_robotiq_2f85-canary.json"),
    ("ufactory_xarm7", "ufactory_xarm7-canary.json"),
    (
        "universal_robots_ur5e_robotiq_2f85",
        "universal_robots_ur5e_robotiq_2f85-canary.json",
    ),
    ("piper", "piper-canary.json"),
    ("kuka_iiwa_14", "kuka_iiwa_14-canary.json"),
    ("aloha_2", "aloha_2-canary.json"),
)


@pytest.mark.parametrize(("robot", "filename"), CANARIES)
def test_additional_robot_canaries_parse_with_shared_limits(
    robot: str, filename: str
) -> None:
    config = ExperimentConfig.from_path(CONFIG_DIR / filename)

    assert config.robots == (robot,)
    assert config.generation_conditions == ("skeleton-assisted",)
    assert config.max_driver_attempts_per_condition == 3
    assert config.probe_budget.max_requests == 14
    assert config.probe_budget.timeout_s == 30.0
    assert config.probe_budget.max_output_chars == 12000
    assert config.record_video is True
    assert config.worker_wall_timeout_s == 120.0
