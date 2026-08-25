from __future__ import annotations

from pathlib import Path

from autoadapter.scripts.check_ivc_inline_mainline import (
    ROBOT_CONFIGURATIONS,
    run_zero_model_mainline_preflight,
)
from autoadapter2.validation_compiler.ivc import _copy_private_inputs


ROOT = Path(__file__).resolve().parents[2]
AUTOADAPTER_ROOT = ROOT / "autoadapter"


def test_private_projection_accepts_real_mujoco_driver_joint_symbol() -> None:
    copied = _copy_private_inputs(
        {
            "instances": {
                "instances": [
                    {
                        "instance_id": "scene-a",
                        "reset": {
                            "joint_positions": {"right_driver_joint": 0.0}
                        },
                    }
                ]
            }
        }
    )

    assert copied["instances"]["instances"][0]["reset"]["joint_positions"] == {
        "right_driver_joint": 0.0
    }


def test_zero_model_tgcd_ivc_harness_whitelist_recap_orchestration() -> None:
    result = run_zero_model_mainline_preflight(
        AUTOADAPTER_ROOT,
        include_all_packages=False,
    )

    assert result["passed"] is True
    assert result["external_model_requests"] == 0
    assert result["experiment_cells_created"] == 0
    assert result["orchestration"]["whitelisted_capabilities"] == ["A1"]
    assert result["orchestration"]["recap_capability_calls"] >= 1
    assert {
        item["robot_configuration_id"] for item in result["inline_mujoco_smokes"]
    } == {"robotstudio_so101", "unitree-go2-stock-12dof"}


def test_exp3_preflight_robot_set_is_exactly_eleven() -> None:
    assert len(ROBOT_CONFIGURATIONS) == 11
    assert len(set(ROBOT_CONFIGURATIONS)) == 11
