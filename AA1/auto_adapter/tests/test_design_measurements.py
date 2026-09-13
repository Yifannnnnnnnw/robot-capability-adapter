# SPDX-License-Identifier: Apache-2.0
"""Focused checks for the explicit DESIGN measurement operators."""

from __future__ import annotations

import mujoco

from auto_adapter.design_measurements import (
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
        "joint_position_error",
        "joint_drift",
        "joint_relation_error",
        "ordered_site_targets_error",
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
