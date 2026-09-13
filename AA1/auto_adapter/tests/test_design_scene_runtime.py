# SPDX-License-Identifier: Apache-2.0
"""Focused real-MuJoCo checks for the DESIGN scene/case runtime."""

from __future__ import annotations

from pathlib import Path

import mujoco
import pytest
import yaml

from auto_adapter.scene_runtime import (
    SceneCaseError,
    apply_initial_state,
    load_scene_cases,
    prepare_scenes,
    probe_case,
)


AA1_ROOT = Path(__file__).resolve().parents[2]
PIPER_MJCF = AA1_ROOT / "assets" / "mjcf" / "piper" / "scene.xml"


def _design() -> dict:
    return {
        "capabilities": [
            {
                "capability_id": "move_named_cap",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target_position_m": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "number"},
                        }
                    },
                    "required": ["target_position_m"],
                    "additionalProperties": False,
                },
                "criteria": [
                    {
                        "metric": "tool_target_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.1,
                        "temporal": {"kind": "terminal"},
                        "aggregation": {"kind": "last"},
                    }
                ],
            }
        ]
    }


def _suite() -> dict:
    return {
        "scenes": {
            "far_cube": {
                "objects": [
                    {
                        "name": "far_cube",
                        "shape": "box",
                        "motion": "free",
                        "size_m": [0.02, 0.02, 0.02],
                        "position_m": [3.0, 3.0, 3.0],
                        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                        "mass_kg": 1.0,
                        "friction": [0.8, 0.01, 0.001],
                    }
                ]
            }
        },
        "cases": [
            {
                "case_id": "piper_far_cube",
                "scene": "far_cube",
                "capability_id": "move_named_cap",
                "initial_state": {
                    "robot": {"keyframe": "home", "qpos_by_joint": {}},
                    "free_bodies": {
                        "far_cube": {
                            "position_m": [3.0, 3.0, 3.0],
                            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                            "linear_velocity_m_s": [0.0, 0.0, 0.0],
                            "angular_velocity_rad_s": [0.0, 0.0, 0.0],
                        }
                    },
                    "ctrl_by_actuator": {},
                    "settle_s": 0.0,
                },
                "request": {"target_position_m": [0.4, 0.0, 0.3]},
                "execution": {"max_sim_time_s": 0.5, "wall_timeout_s": 2.0},
                "measurements": [
                    {
                        "criterion_index": 0,
                        "operator": "site_position_error",
                        "bindings": {
                            "site": "ee_site",
                            "target_request_field": "target_position_m",
                        },
                    }
                ],
            }
        ],
    }


def test_load_validates_request_and_every_criterion(tmp_path: Path) -> None:
    path = tmp_path / "scene_cases.yaml"
    path.write_text(yaml.safe_dump(_suite()), encoding="utf-8")
    loaded = load_scene_cases(path, design=_design())
    assert loaded["scenes"]["far_cube"]["objects"][0]["shape"] == "box"
    assert loaded["cases"][0]["request"]["target_position_m"] == [0.4, 0.0, 0.3]

    invalid = _suite()
    invalid["cases"][0]["request"] = {"target_position_m": [0.4, 0.0]}
    path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
    with pytest.raises(SceneCaseError, match="length 3|minItems"):
        load_scene_cases(path, design=_design())


@pytest.mark.parametrize("shape,size", [("box", [0.02, 0.02, 0.02]), ("sphere", [0.02]), ("cylinder", [0.02, 0.04])])
def test_piper_scene_assembly_reload_reset_restore_and_independence(tmp_path: Path, shape, size) -> None:
    suite = _suite()
    suite["scenes"]["far_cube"]["objects"][0].update(shape=shape, size_m=size)
    paths = prepare_scenes(mjcf_path=PIPER_MJCF, suite=suite, output_dir=tmp_path)
    scene_path = paths["far_cube"]
    assert scene_path == tmp_path.resolve() / "scenes" / "far_cube" / "scene.xml"

    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    apply_initial_state(model, data, _suite()["cases"][0])
    cube_id = model.body("far_cube").id
    assert model.nq == 15  # Piper qpos plus the newly assembled free body.
    assert data.time == 0.0
    assert data.qpos[-7:-4].tolist() == [3.0, 3.0, 3.0]
    assert data.xpos[cube_id].tolist() == [3.0, 3.0, 3.0]

    # Separate reloads receive separate model/data objects.  Stepping one does
    # not advance the other.
    model_2 = mujoco.MjModel.from_xml_path(str(scene_path))
    data_2 = mujoco.MjData(model_2)
    apply_initial_state(model_2, data_2, _suite()["cases"][0])
    mujoco.mj_step(model, data, 10)
    assert data.time > 0.0
    assert data_2.time == 0.0


def test_keyframe_reset_requires_explicit_free_body_state(tmp_path: Path) -> None:
    paths = prepare_scenes(mjcf_path=PIPER_MJCF, suite=_suite(), output_dir=tmp_path)
    model = mujoco.MjModel.from_xml_path(str(paths["far_cube"]))
    data = mujoco.MjData(model)
    case = _suite()["cases"][0]
    del case["initial_state"]["free_bodies"]["far_cube"]
    with pytest.raises(SceneCaseError, match="explicit initial state"):
        apply_initial_state(model, data, case)


def test_probe_reports_compile_reload_and_binding_only(tmp_path: Path) -> None:
    report = probe_case(
        mjcf_path=PIPER_MJCF,
        suite=_suite(),
        design=_design(),
        output_dir=tmp_path,
        case_id="piper_far_cube",
    )
    assert report["ok"] is True
    assert report["compiled"] is True
    assert report["reloaded"] is True
    assert report["bindings_resolved"] is True
    assert report["probe_only"] is True
    assert "criteria_passed" not in report
    assert report["initial_sample"]["time"] == 0.0
    assert report["post_step_sample"]["time"] > report["initial_sample"]["time"]
