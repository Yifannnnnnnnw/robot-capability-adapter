from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..foundation.errors import ContractError


SO_JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
SO_FIELDS = [f"{name}.pos" for name in SO_JOINTS]
SO_MOTOR_MAPPING = [
    (1, "shoulder_pan.pos", "shoulder_pan"),
    (2, "shoulder_lift.pos", "shoulder_lift"),
    (3, "elbow_flex.pos", "elbow_flex"),
    (4, "wrist_flex.pos", "wrist_flex"),
    (5, "wrist_roll.pos", "wrist_roll"),
    (6, "gripper.pos", "gripper"),
]

GO2_ACTUATORS = [
    "FR_hip", "FR_thigh", "FR_calf",
    "FL_hip", "FL_thigh", "FL_calf",
    "RR_hip", "RR_thigh", "RR_calf",
    "RL_hip", "RL_thigh", "RL_calf",
]
GO2_JOINTS = [f"{name}_joint" for name in GO2_ACTUATORS]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _read_ref(project_root: Path, manifest: dict[str, Any], name: str) -> dict[str, Any]:
    ref = manifest[name]
    path = project_root / ref["path"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load {name}: {path}") from exc


def validate_robot_facts(project_root: Path, manifest: dict[str, Any]) -> None:
    """Validate the two frozen Demo routes against Authority 0.15.0 facts.

    Hash and generic manifest validation remain owned by the experiment gate. This function keeps
    robot-specific names, units, transports, and safety rules out of generic orchestration code.
    """

    morphology = _read_ref(project_root, manifest, "morphology_ref")
    sdk = _read_ref(project_root, manifest, "sdk_ref")
    translation = _read_ref(project_root, manifest, "translation_ref")
    robot = manifest.get("robot_model_id")
    if robot == "so-arm101":
        _validate_so(manifest, morphology, sdk, translation)
    elif robot == "unitree-go2":
        _validate_go2(manifest, morphology, sdk, translation)
    else:
        raise ContractError(f"unsupported first-Demo robot_model_id: {robot!r}")


def _validate_so(
    manifest: dict[str, Any],
    morphology: dict[str, Any],
    sdk: dict[str, Any],
    translation: dict[str, Any],
) -> None:
    _expect(manifest["manifest_id"] == "so-arm101-follower-stock-gripper-mujoco", "wrong SO manifest ID")
    _expect(manifest["robot_configuration_id"] == "so-arm101-follower-stock-gripper", "wrong SO configuration")
    _expect(morphology["base_type"] == "fixed" and morphology["actuated_dof"] == 6, "wrong SO morphology")
    _expect(morphology["joint_names"] == SO_JOINTS, "wrong SO joint order")
    _expect(morphology["actuator_names"] == SO_JOINTS, "wrong SO actuator order")
    _expect(morphology["mujoco"]["version"] == "3.3.6", "wrong SO MuJoCo version")
    _expect(
        morphology["mujoco"]["source_sha256"]
        == "d75253eb568e8a7214db9c631ab7bed4217f608a26f7276ebe9a7636cac82580",
        "wrong SO MJCF hash",
    )
    _expect(sdk["id"] == "lerobot-so101-follower", "wrong SO SDK Entry")
    _expect(sdk["action_fields"] == SO_FIELDS and sdk["observation_fields"] == SO_FIELDS, "wrong SO SDK fields")
    _expect(sdk["units"] == {"arm": "degree", "gripper": "normalized_0_100"}, "wrong SO SDK units")
    packages = {item["name"]: item for item in sdk["packages"]}
    _expect(packages["lerobot"]["version"] == "0.6.0", "wrong LeRobot version")
    _expect(
        packages["lerobot"]["sha256"]
        == "b38a564fbc441d98380576863bf68635dde5fc2c42ddc2a39d0486640dc9e9a8",
        "wrong LeRobot wheel hash",
    )
    _expect(
        packages["feetech-servo-sdk"]["sha256"]
        == "d4d3832e4b1b22a8222133a414db9f868224c2fb639426a1b11d96ddfe84e69c",
        "wrong Feetech sdist hash",
    )
    observed = [
        (item["motor_id"], item["sdk_field"], item["joint"])
        for item in translation["motor_mapping"]
    ]
    _expect(observed == SO_MOTOR_MAPPING, "wrong SO motor mapping")
    _expect(translation["registers"]["Goal_Position"] == {"address": 42, "width_bytes": 2, "access": "write"}, "wrong Goal_Position register")
    _expect(translation["registers"]["Present_Position"] == {"address": 56, "width_bytes": 2, "access": "read"}, "wrong Present_Position register")
    _expect(translation["conversion"]["raw_tick_range"] == [0, 4095], "wrong SO raw tick range")
    _expect(translation["normal_motion_writes"] == "actuator_control_only", "SO motion may only write actuator control")
    _expect(translation["direct_qpos_qvel_write"] == "reset_only", "SO direct state write must be reset-only")


def _validate_go2(
    manifest: dict[str, Any],
    morphology: dict[str, Any],
    sdk: dict[str, Any],
    translation: dict[str, Any],
) -> None:
    _expect(manifest["manifest_id"] == "unitree-go2-stock-12dof-mujoco", "wrong Go2 manifest ID")
    _expect(manifest["robot_configuration_id"] == "unitree-go2-stock-12dof", "wrong Go2 configuration")
    _expect(morphology["base_type"] == "free" and morphology["actuated_dof"] == 12, "wrong Go2 morphology")
    _expect(morphology["joint_names"] == GO2_JOINTS, "wrong Go2 joint order")
    _expect(morphology["actuator_names"] == GO2_ACTUATORS, "wrong Go2 actuator order")
    _expect(morphology["mujoco"]["version"] == "3.3.6", "wrong Go2 MuJoCo version")
    _expect(
        morphology["mujoco"]["source_sha256"]
        == "2014a3d76e30f17ab9447d8a67bd015291f74fa4d71ae30d005f1a32bd693d4b",
        "wrong Go2 MJCF hash",
    )
    _expect(sdk["id"] == "unitree-sdk2-go2-lowlevel", "wrong Go2 SDK Entry")
    _expect(sdk["motor_slot_count"] == 20 and sdk["active_motor_count"] == 12, "wrong Go2 slot counts")
    _expect(
        sdk["topics"] == {
            "command": "rt/lowcmd",
            "state": "rt/lowstate",
            "sport_state_read_only": "rt/sportmodestate",
        },
        "wrong Go2 SDK topics",
    )
    mapping = translation["active_motor_mapping"]
    _expect([item["index"] for item in mapping] == list(range(12)), "wrong Go2 active indices")
    _expect([item["actuator"] for item in mapping] == GO2_ACTUATORS, "wrong Go2 actuator mapping")
    _expect([item["joint"] for item in mapping] == GO2_JOINTS, "wrong Go2 joint mapping")
    _expect(translation["dds"]["domain_id"] == 1 and translation["dds"]["interface"] == "lo", "wrong Go2 DDS route")
    _expect(translation["dds"]["motor_slot_count"] == 20, "wrong Go2 DDS width")
    _expect(translation["active_slot_rule"]["mode"] == 1, "wrong Go2 active mode")
    _expect(translation["active_slot_rule"]["finite_fields"] == ["q", "dq", "kp", "kd", "tau"], "wrong Go2 command fields")
    inactive = translation["inactive_slot_rule"]
    _expect(inactive == {"indices": list(range(12, 20)), "mode": 1, "q": 2146000000.0, "dq": 16000.0, "kp": 0.0, "kd": 0.0, "tau": 0.0}, "wrong Go2 inactive-slot rule")
    _expect(translation["stale_rule"] == {"clock": "simulation_time", "limit_s": 0.1, "action": "zero_all_controls_and_mark_STALE"}, "wrong Go2 stale rule")
    _expect(translation["normal_motion_writes"] == "actuator_control_only", "Go2 motion may only write actuator control")
    _expect(translation["direct_qpos_qvel_write"] == "reset_only", "Go2 direct state write must be reset-only")
