# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the explicit DESIGN measurement operators."""

from __future__ import annotations

import copy
from pathlib import Path

import mujoco
import numpy as np
import pytest

from auto_adapter.design_measurements import (
    MeasurementError,
    capture_sample,
    evaluate_measurements,
    measurement_catalog,
    validate_measurement,
)


XML = """
<mujoco>
  <option timestep="0.01"/>
  <worldbody>
    <body name="arm">
      <joint name="shoulder" type="hinge"/>
      <joint name="wrist" type="hinge"/>
      <geom name="tool_geom" type="sphere" size="0.04" mass="1"/>
      <site name="tool_site" pos="0 0 0"/>
    </body>
    <body name="target" pos="0.15 0 0">
      <geom name="target_geom" type="sphere" size="0.04" mass="1"/>
    </body>
  </worldbody>
</mujoco>
"""


def _model() -> tuple[mujoco.MjModel, mujoco.MjData]:
    model = mujoco.MjModel.from_xml_string(XML)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data


def _criterion(metric: str, unit: str, threshold: float, *, aggregation: str = "last") -> dict:
    return {
        "metric": metric,
        "unit": unit,
        "comparator": "<=",
        "threshold": threshold,
        "temporal": {"kind": "terminal"},
        "aggregation": {"kind": aggregation},
    }


def _design(criteria: list[dict]) -> dict:
    return {
        "capabilities": [
            {
                "capability_id": "free_named_cap",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "target_position_m": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "number"},
                        },
                        "target_wrist_rad": {"type": "number"},
                        "waypoint_a": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "number"},
                        },
                        "waypoint_b": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {"type": "number"},
                        },
                    },
                    "required": [
                        "target_position_m",
                        "target_wrist_rad",
                        "waypoint_a",
                        "waypoint_b",
                    ],
                    "additionalProperties": False,
                },
                "criteria": criteria,
            }
        ]
    }


def _sample(time: float, *, site: list[float], wrist: float, contacts: list[dict] | None = None) -> dict:
    return {
        "time": time,
        "site_positions": {"tool_site": site},
        "joint_positions": {"shoulder": 0.0, "wrist": wrist},
        "contacts": [] if contacts is None else contacts,
    }


def test_catalog_states_only_explicit_supported_semantics() -> None:
    text = measurement_catalog()
    for operator in (
        "site_position_error",
        "body_position_error",
        "joint_position_error",
        "joint_drift",
        "joint_relation_error",
        "ordered_site_targets_error",
        "ordered_body_targets_error",
        "contact_normal_force",
    ):
        assert operator in text
    assert "{kind:terminal}" in text
    assert "{kind:window,start_s:...,end_s:...}" in text


def test_endpoint_can_be_right_while_explicit_wrist_criterion_fails() -> None:
    model, _data = _model()
    criterion = _criterion("wrist_pose_error", "rad", 0.05)
    design = _design([criterion])
    case = {
        "capability_id": "free_named_cap",
        "request": {
            "target_position_m": [0.0, 0.0, 0.0],
            "target_wrist_rad": 0.0,
            "waypoint_a": [0.0, 0.0, 0.0],
            "waypoint_b": [1.0, 0.0, 0.0],
        },
        "measurements": [
            {
                "criterion_index": 0,
                "operator": "joint_position_error",
                "bindings": {"joint": "wrist", "target_request_field": "target_wrist_rad"},
            }
        ],
    }
    validate_measurement(model, case["measurements"][0], criterion, case["request"])
    results = evaluate_measurements(
        design,
        case,
        [
            _sample(0.0, site=[0.0, 0.0, 0.0], wrist=0.0),
            # The endpoint is exactly at the target site, while the wrist is wrong.
            _sample(0.1, site=[0.0, 0.0, 0.0], wrist=0.2),
        ],
    )
    assert results[0]["ok"] is False
    assert results[0]["value"] == 0.2


def test_ordered_operator_rejects_endpoint_only_success_when_waypoint_is_missing() -> None:
    model, _data = _model()
    criterion = _criterion("ordered_path_error", "m", 0.05)
    design = _design([criterion])
    case = {
        "capability_id": "free_named_cap",
        "request": {
            "target_position_m": [1.0, 0.0, 0.0],
            "target_wrist_rad": 0.0,
            "waypoint_a": [0.5, 0.0, 0.0],
            "waypoint_b": [1.0, 0.0, 0.0],
        },
        "measurements": [
            {
                "criterion_index": 0,
                "operator": "ordered_site_targets_error",
                "bindings": {
                    "site": "tool_site",
                    "target_request_fields": ["waypoint_a", "waypoint_b"],
                },
            }
        ],
    }
    validate_measurement(model, case["measurements"][0], criterion, case["request"])
    results = evaluate_measurements(
        design,
        case,
        [
            _sample(0.0, site=[0.0, 0.0, 0.0], wrist=0.0),
            # The final target is right, but the intermediate waypoint was never visited.
            _sample(0.1, site=[1.0, 0.0, 0.0], wrist=0.0),
        ],
    )
    assert results[0]["ok"] is False
    assert results[0]["value"] >= 0.5


def test_missing_named_contact_is_zero_and_positive_force_criterion_fails() -> None:
    model, data = _model()
    criterion = {
        "metric": "required_contact_force",
        "unit": "N",
        "comparator": ">",
        "threshold": 0.1,
        "temporal": {"kind": "terminal"},
        "aggregation": {"kind": "max"},
    }
    design = _design([criterion])
    case = {
        "capability_id": "free_named_cap",
        "request": {
            "target_position_m": [0.0, 0.0, 0.0],
            "target_wrist_rad": 0.0,
            "waypoint_a": [0.0, 0.0, 0.0],
            "waypoint_b": [1.0, 0.0, 0.0],
        },
        "measurements": [
            {
                "criterion_index": 0,
                "operator": "contact_normal_force",
                "bindings": {"geom1": "tool_geom", "geom2": "target_geom"},
            }
        ],
    }
    validate_measurement(model, case["measurements"][0], criterion, case["request"])
    sample = capture_sample(model, data)
    assert sample["contacts"] == []
    results = evaluate_measurements(design, case, [sample, {**sample, "time": 0.1}])
    assert results[0]["value"] == 0.0
    assert results[0]["ok"] is False


def test_contact_capture_uses_mujoco_normal_force() -> None:
    contact_xml = """<mujoco><worldbody>
      <geom name="floor" type="plane" size="1 1 .1"/>
      <body pos="0 0 .03"><freejoint/>
        <geom name="ball" type="sphere" size=".04" mass="1"/>
      </body></worldbody></mujoco>"""
    model = mujoco.MjModel.from_xml_string(contact_xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    sample = capture_sample(model, data)
    assert sample["contacts"]
    assert any(item["normal_force_N"] > 0 for item in sample["contacts"])


def test_body_origin_measurements_follow_real_motion_and_reject_wrong_order() -> None:
    """A named native slider fixture has a body origin distinct from its COM."""
    model = mujoco.MjModel.from_xml_string("""<mujoco>
      <option timestep="0.01" gravity="0 0 0" integrator="Euler"/>
      <worldbody><body name="tool" pos="0.2 0 0">
        <joint name="slide" type="slide" axis="1 0 0"/>
        <geom type="sphere" pos="0.1 0 0" size="0.02" mass="1"/>
      </body></worldbody></mujoco>""")
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert model.nsite == 0
    criterion = _criterion("endpoint_error", "m", 1e-6)
    ordered_criterion = _criterion("ordered_error", "m", 1e-6)
    request = {"target_position_m": [0.22, 0, 0], "waypoint_a": [0.21, 0, 0],
               "waypoint_b": [0.22, 0, 0]}
    measurements = [
        {"criterion_index": 0, "operator": "body_position_error",
         "bindings": {"body": "tool", "target_request_field": "target_position_m"}},
        {"criterion_index": 1, "operator": "ordered_body_targets_error",
         "bindings": {"body": "tool", "target_request_fields": ["waypoint_a", "waypoint_b"]}},
    ]
    design = _design([criterion, ordered_criterion])
    case = {"capability_id": "free_named_cap", "request": request, "measurements": measurements}
    for measurement, rule in zip(measurements, (criterion, ordered_criterion)):
        validate_measurement(model, measurement, rule, request)
    samples = [capture_sample(model, data)]
    assert samples[0]["body_positions"]["tool"] == pytest.approx([0.2, 0, 0])
    assert data.xipos[model.body("tool").id, 0] == pytest.approx(0.3)
    data.qvel[0] = 1.0
    for _ in range(2):
        mujoco.mj_step(model, data)
        mujoco.mj_forward(model, data)
        samples.append(capture_sample(model, data))
    assert samples[-1]["body_positions"]["tool"] == pytest.approx([0.22, 0, 0])
    assert all(result["ok"] for result in evaluate_measurements(design, case, samples))

    # Reversing the requested order keeps the same endpoint but must fail the path.
    reversed_case = copy.deepcopy(case)
    reversed_case["measurements"][1]["bindings"]["target_request_fields"].reverse()
    results = evaluate_measurements(design, reversed_case, samples)
    assert results[0]["ok"] is True and results[1]["ok"] is False
    assert evaluate_measurements(design, case, [samples[0], samples[-1]])[1]["ok"] is False
    assert not evaluate_measurements(design, case, samples[:2])[0]["ok"]
    missing = copy.deepcopy(samples)
    del missing[-1]["body_positions"]["tool"]
    assert not evaluate_measurements(design, case, missing)[0]["ok"]
    bad = copy.deepcopy(measurements[0])
    bad["bindings"]["body"] = "missing_body"
    with pytest.raises(MeasurementError, match="not present"):
        validate_measurement(model, bad, criterion, request)


def test_canonical_panda_without_sites_supports_body_scene_probe(tmp_path: Path) -> None:
    from auto_adapter.scene_runtime import probe_case

    mjcf = Path(__file__).resolve().parents[2] / "assets/mjcf/franka_panda/scene.xml"
    source = mjcf.read_bytes()
    model = mujoco.MjModel.from_xml_path(str(mjcf))
    assert model.nsite == 0
    model.body("hand")
    criteria = [_criterion("hand_error", "m", 0.05), _criterion("hand_path_error", "m", 0.05)]
    design = _design(criteria)
    case = {
        "case_id": "panda_hand", "scene": "empty", "capability_id": "free_named_cap",
        "initial_state": {"robot": {"keyframe": "home"}},
        "request": {"target_position_m": [0.46, 0.10, 0.47], "target_wrist_rad": 0.0,
                    "waypoint_a": [0.5, 0.0, 0.5], "waypoint_b": [0.46, 0.10, 0.47]},
        "execution": {"max_sim_time_s": 0.1, "wall_timeout_s": 2},
        "measurements": [
            {"criterion_index": 0, "operator": "body_position_error",
             "bindings": {"body": "hand", "target_request_field": "target_position_m"}},
            {"criterion_index": 1, "operator": "ordered_body_targets_error",
             "bindings": {"body": "hand", "target_request_fields": ["waypoint_a", "waypoint_b"]}},
        ],
    }
    report = probe_case(mjcf_path=mjcf, suite={"scenes": {"empty": {"objects": []}}, "cases": [case]},
                        design=design, output_dir=tmp_path, case_id=case["case_id"])
    assert report["ok"] is True, report
    assert report["bindings_resolved"] is True and report["steps"] > 0
    samples = [report["initial_sample"], report["post_step_sample"]]
    assert all(np.isfinite(sample["body_positions"]["hand"]).all() for sample in samples)
    # Body coordinates must describe the recorded joint state at the same instant.
    snapshot = mujoco.MjData(model)
    for name, position in samples[-1]["joint_positions"].items():
        snapshot.qpos[model.joint(name).qposadr[0]] = position
    mujoco.mj_forward(model, snapshot)
    np.testing.assert_allclose(samples[-1]["body_positions"]["hand"],
                               snapshot.xpos[model.body("hand").id], rtol=0, atol=1e-12)
    # Successful scene preparation must not imply the unexecuted target was reached.
    assert not any(result["ok"] for result in evaluate_measurements(design, case, samples))
    assert mjcf.read_bytes() == source
