"""Small allowlisted public-state projectors for the two prepared B2 robots."""

from __future__ import annotations

import math
from typing import Any


PUBLIC_STATE_PROFILE_REVISION = "b2-public-state-v2"


class PublicObservationError(RuntimeError):
    """Raised when a fixed public state cannot be projected safely."""


def project_public_state(
    *,
    robot_configuration_id: str,
    mujoco: Any,
    model: Any,
    data: Any,
) -> dict[str, Any]:
    """Project canonical MuJoCo state through one robot-specific allowlist."""

    if robot_configuration_id == "robotstudio_so101":
        return _project_so101(mujoco=mujoco, model=model, data=data)
    if robot_configuration_id == "unitree-go2-stock-12dof":
        return _project_go2(mujoco=mujoco, model=model, data=data)
    raise PublicObservationError(
        f"no B2 public-state profile for {robot_configuration_id!r}"
    )


def _project_so101(*, mujoco: Any, model: Any, data: Any) -> dict[str, Any]:
    site_id = _name_id(
        mujoco,
        model,
        mujoco.mjtObj.mjOBJ_SITE,
        "gripperframe",
    )
    gripper_joint_id = _name_id(
        mujoco,
        model,
        mujoco.mjtObj.mjOBJ_JOINT,
        "gripper",
    )
    wrist_roll_joint_id = _name_id(
        mujoco,
        model,
        mujoco.mjtObj.mjOBJ_JOINT,
        "wrist_roll",
    )
    gripper_body_id = _name_id(
        mujoco,
        model,
        mujoco.mjtObj.mjOBJ_BODY,
        "gripper",
    )
    qpos_address = int(model.jnt_qposadr[gripper_joint_id])
    wrist_roll_qpos_address = int(model.jnt_qposadr[wrist_roll_joint_id])
    lower = float(model.jnt_range[gripper_joint_id, 0])
    upper = float(model.jnt_range[gripper_joint_id, 1])
    if not upper > lower:
        raise PublicObservationError("SO-101 gripper range is invalid")
    opening = (float(data.qpos[qpos_address]) - lower) / (upper - lower)
    opening = min(max(opening, 0.0), 1.0)

    gripper_contact = False
    for index in range(int(data.ncon)):
        contact = data.contact[index]
        body_1 = int(model.geom_bodyid[int(contact.geom1)])
        body_2 = int(model.geom_bodyid[int(contact.geom2)])
        in_gripper_1 = _is_descendant(model, body_1, gripper_body_id)
        in_gripper_2 = _is_descendant(model, body_2, gripper_body_id)
        if in_gripper_1 != in_gripper_2:
            gripper_contact = True
            break

    return _finite_state(
        {
            "simulation_time_s": float(data.time),
            "end_effector_position_world_m": [
                float(value) for value in data.site_xpos[site_id]
            ],
            "wrist_roll_rad": float(data.qpos[wrist_roll_qpos_address]),
            "gripper_opening_fraction": opening,
            "end_effector_contact_detected": gripper_contact,
        }
    )


def _project_go2(*, mujoco: Any, model: Any, data: Any) -> dict[str, Any]:
    base_id = _name_id(
        mujoco,
        model,
        mujoco.mjtObj.mjOBJ_BODY,
        "base_link",
    )
    quaternion = [float(value) for value in data.xquat[base_id]]
    roll, pitch, yaw = _quaternion_to_rpy(quaternion)

    base_joint_id = int(model.body_jntadr[base_id])
    if (
        int(model.body_jntnum[base_id]) != 1
        or base_joint_id < 0
        or int(model.jnt_type[base_joint_id])
        != int(mujoco.mjtJoint.mjJNT_FREE)
    ):
        raise PublicObservationError("Go2 base_link must own the free joint")
    dof_address = int(model.jnt_dofadr[base_joint_id])
    linear_velocity = [
        float(value) for value in data.qvel[dof_address : dof_address + 3]
    ]

    foot_geom_ids = {
        _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_GEOM, name)
        for name in ("FL", "FR", "RL", "RR")
    }
    feet_in_contact = _contacted_geom_ids(data, foot_geom_ids)

    return _finite_state(
        {
            "simulation_time_s": float(data.time),
            "base_position_world_m": [float(value) for value in data.xpos[base_id]],
            "base_roll_pitch_yaw_rad": [roll, pitch, yaw],
            "base_linear_velocity_world_m_s": linear_velocity,
            "foot_contact_count": len(feet_in_contact),
        }
    )


def _name_id(mujoco: Any, model: Any, object_type: Any, name: str) -> int:
    identifier = int(mujoco.mj_name2id(model, object_type, name))
    if identifier < 0:
        raise PublicObservationError(f"public profile symbol {name!r} is absent")
    return identifier


def _is_descendant(model: Any, body_id: int, ancestor_id: int) -> bool:
    current = body_id
    while current > 0 and current != ancestor_id:
        current = int(model.body_parentid[current])
    return current == ancestor_id


def _contacted_geom_ids(data: Any, selected_geom_ids: set[int]) -> set[int]:
    contacted: set[int] = set()
    for index in range(int(data.ncon)):
        contact = data.contact[index]
        for geom_id in (int(contact.geom1), int(contact.geom2)):
            if geom_id in selected_geom_ids:
                contacted.add(geom_id)
    return contacted


def _quaternion_to_rpy(quaternion: list[float]) -> tuple[float, float, float]:
    if len(quaternion) != 4:
        raise PublicObservationError("body quaternion must have four components")
    w, x, y, z = quaternion
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch_sine = 2.0 * (w * y - z * x)
    pitch = math.asin(min(max(pitch_sine, -1.0), 1.0))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def _finite_state(value: dict[str, Any]) -> dict[str, Any]:
    def check(item: Any) -> None:
        if isinstance(item, bool) or isinstance(item, int):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise PublicObservationError("public state contains a non-finite value")
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        raise PublicObservationError("public state contains an unsupported value")

    for state_value in value.values():
        check(state_value)
    return value


__all__ = [
    "PUBLIC_STATE_PROFILE_REVISION",
    "PublicObservationError",
    "project_public_state",
]
