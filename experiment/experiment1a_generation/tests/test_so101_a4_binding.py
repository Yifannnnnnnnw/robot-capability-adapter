from __future__ import annotations

import copy
import json
from pathlib import Path

import mujoco

from autoadapter2.harness.b1_contracts import evaluate_b1_contract
from autoadapter2.harness.runner import _resolve_b1_body_geom_symbols


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SUITE_PATH = (
    REPOSITORY_ROOT
    / "experiment"
    / "experiment1a_generation"
    / "validation" / "fixed_validation_bundles"
    / "robotstudio_so101"
    / "capability_validation_suite.json"
)
PACKAGE_ROOT = (
    REPOSITORY_ROOT
    / "autoadapter"
    / "libraries"
    / "robots"
    / "robotstudio_so101"
    / "1.0.0"
)


def _suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _case(case_id: str) -> dict:
    return next(case for case in _suite()["cases"] if case["case_id"] == case_id)


def _geom_name(model: mujoco.MjModel, geom_id: int) -> str:
    return (
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
        or f"geom_{geom_id}"
    )


def _contact_evidence() -> dict:
    samples = []
    for index in range(27):
        # Hold precontact for 0.10 s, then advance at 0.020 m/s for 22 mm.
        axial = 0.0 if index <= 2 else min(0.022, (index - 2) * 0.001)
        contacts = []
        if index >= 24:
            contacts.append(
                {
                    "geom1": "geom_37",
                    "geom2": "front_button_geom",
                    "distance": -0.0005,
                }
            )
        samples.append(
            {
                "time": index * 0.05,
                "site_positions": {
                    "gripperframe": [0.38, 0.07 + axial, 0.22]
                },
                "joint_positions": {"gripper": 0.0},
                "contacts": contacts,
            }
        )
    return {"samples": samples}


def test_a4_binding_expands_the_real_gripper_contact_subtree() -> None:
    a4_cases = [case for case in _suite()["cases"] if case["capability_id"] == "A4"]
    assert len(a4_cases) == 3
    assert all(
        case["binding"]["parameters"].get("tool_body_names") == ["gripper"]
        and "tool_geom_names" not in case["binding"]["parameters"]
        for case in a4_cases
    )

    case = _case("A4-H1")
    scene_path = PACKAGE_ROOT / case["scene_entrypoint"]
    resolved = _resolve_b1_body_geom_symbols(case["binding"], scene_path)
    resolved_names = set(resolved["parameters"]["tool_geom_names"])
    assert {
        "fixed_jaw_box1",
        "fixed_jaw_box4",
        "moving_jaw_box1",
        "moving_jaw_box2",
        "geom_37",
        "geom_47",
    } <= resolved_names

    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    # Collision-free precontact IK branch advanced 22 mm along the sealed +Y ray.
    contact_q = (-0.296, 0.039, -0.149, 0.310, -1.168)
    for joint_name, value in zip(
        ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"),
        contact_q,
    ):
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        data.qpos[int(model.jnt_qposadr[joint_id])] = value
    mujoco.mj_forward(model, data)

    actual_target_pairs = {
        tuple(
            sorted(
                (
                    _geom_name(model, int(data.contact[index].geom1)),
                    _geom_name(model, int(data.contact[index].geom2)),
                )
            )
        )
        for index in range(int(data.ncon))
        if "front_button_geom"
        in {
            _geom_name(model, int(data.contact[index].geom1)),
            _geom_name(model, int(data.contact[index].geom2)),
        }
    }
    assert ("front_button_geom", "geom_37") in actual_target_pairs
    actual_contact_names = {name for pair in actual_target_pairs for name in pair}
    assert not ({"fixed_jaw_box1", "moving_jaw_box1"} & actual_contact_names)
    assert resolved_names & actual_contact_names


def test_a4_real_contact_is_accepted_but_unrelated_contact_is_rejected() -> None:
    case = _case("A4-H1")
    scene_path = PACKAGE_ROOT / case["scene_entrypoint"]
    resolved = _resolve_b1_body_geom_symbols(case["binding"], scene_path)
    old_parameters = {
        "contract_id": "A4",
        "site_name": "gripperframe",
        "tool_geom_names": ["fixed_jaw_box1", "moving_jaw_box1"],
        "target_geom_names": ["front_button_geom"],
    }

    evidence = _contact_evidence()
    assert (
        evaluate_b1_contract(
            old_parameters,
            evidence=evidence,
            request=case["request"],
        )
        == 0.0
    )
    assert (
        evaluate_b1_contract(
            resolved["parameters"],
            evidence=evidence,
            request=case["request"],
        )
        == 1.0
    )

    unrelated = copy.deepcopy(evidence)
    for sample in unrelated["samples"]:
        if sample["contacts"]:
            sample["contacts"].append(
                {
                    "geom1": "geom_37",
                    "geom2": "button_housing",
                    "distance": -0.0002,
                }
            )
    assert (
        evaluate_b1_contract(
            resolved["parameters"],
            evidence=unrelated,
            request=case["request"],
        )
        == 0.0
    )
