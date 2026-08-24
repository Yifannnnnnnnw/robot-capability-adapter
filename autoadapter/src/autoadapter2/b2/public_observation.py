"""Small allowlisted public-state projectors for the two prepared B2 robots."""

from __future__ import annotations

import math
import json
from collections.abc import Mapping
from pathlib import Path
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
    morphology: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project canonical MuJoCo state through the package public allowlist.

    SO-101 and Go2 retain their compact historical envelopes for compatibility;
    every other current package is projected from its declared
    ``public_observations``/``public_affordances`` without exposing package
    private task records, resets, guards, or criteria.
    """

    if robot_configuration_id == "robotstudio_so101":
        return _project_so101(mujoco=mujoco, model=model, data=data)
    if robot_configuration_id == "unitree-go2-stock-12dof":
        return _project_go2(mujoco=mujoco, model=model, data=data)
    public_morphology = morphology if morphology is not None else _load_public_morphology(robot_configuration_id)
    return _project_generic(
        robot_configuration_id=robot_configuration_id,
        morphology=public_morphology,
        mujoco=mujoco,
        model=model,
        data=data,
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
    lower = float(_matrix_value(model.jnt_range, gripper_joint_id, 0))
    upper = float(_matrix_value(model.jnt_range, gripper_joint_id, 1))
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


def _load_public_morphology(robot_configuration_id: str) -> Mapping[str, Any]:
    """Load only morphology metadata for a package's public projection."""

    root = Path(__file__).resolve().parents[3] / "libraries" / "robots" / robot_configuration_id
    candidates = sorted(root.glob("*/morphology.json"))
    if not candidates:
        raise PublicObservationError(
            f"no public morphology profile for {robot_configuration_id!r}"
        )
    try:
        value = json.loads(candidates[0].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PublicObservationError("public morphology metadata is invalid") from exc
    if not isinstance(value, Mapping):
        raise PublicObservationError("public morphology metadata must be an object")
    return value


def _project_generic(
    *,
    robot_configuration_id: str,
    morphology: Mapping[str, Any],
    mujoco: Any,
    model: Any,
    data: Any,
) -> dict[str, Any]:
    """Project all current morphology shapes from declared public symbols."""

    observations = morphology.get("public_observations")
    affordances = morphology.get("public_affordances")
    if not isinstance(observations, Mapping) or not isinstance(affordances, Mapping):
        raise PublicObservationError(
            f"{robot_configuration_id!r} lacks public observation metadata"
        )
    observation_kinds = set(affordances.get("observations", ()))
    state: dict[str, Any] = {
        "observation_revision": PUBLIC_STATE_PROFILE_REVISION,
        "robot_configuration_id": robot_configuration_id,
        "simulation_time_s": _finite_float(getattr(data, "time", 0.0)),
    }
    if "joint_positions" in observation_kinds:
        state["joint_positions"] = _finite_vector(getattr(data, "qpos", ()))
    if "joint_velocities" in observation_kinds:
        state["joint_velocities"] = _finite_vector(getattr(data, "qvel", ()))

    public_names = _public_symbol_names(observations)
    site_positions: dict[str, list[float]] = {}
    site_poses: dict[str, dict[str, Any]] = {}
    for name in public_names["sites"]:
        identifier = _safe_name_id(mujoco, model, mujoco.mjtObj.mjOBJ_SITE, name)
        if identifier is None:
            continue
        position = _finite_vector(model_vector(data.site_xpos[identifier]))
        site_positions[name] = position
        pose: dict[str, Any] = {"position_world_m": position}
        if hasattr(data, "site_xmat"):
            pose["rotation_matrix"] = _finite_vector(model_vector(data.site_xmat[identifier]))
        site_poses[name] = pose
    if site_positions and ("named_site_positions" in observation_kinds or "named_site_pose" in observation_kinds or "named_site_poses" in observation_kinds):
        state["named_site_positions"] = site_positions
    if site_poses and ("named_site_pose" in observation_kinds or "named_site_poses" in observation_kinds):
        state["named_site_poses"] = site_poses

    body_positions: dict[str, list[float]] = {}
    body_poses: dict[str, dict[str, Any]] = {}
    for name in public_names["bodies"]:
        identifier = _safe_name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, name)
        if identifier is None:
            continue
        position = _finite_vector(model_vector(data.xpos[identifier]))
        body_positions[name] = position
        pose: dict[str, Any] = {"position_world_m": position}
        if hasattr(data, "xquat"):
            pose["quaternion_wxyz"] = _finite_vector(model_vector(data.xquat[identifier]))
        body_poses[name] = pose
    if body_positions and "named_body_pose" in observation_kinds:
        state["named_body_poses"] = body_poses

    base_name = observations.get("base_body")
    if isinstance(base_name, str) and base_name in body_poses:
        state["base_pose"] = body_poses[base_name]
        if "base_velocity" in observation_kinds:
            body_id = _safe_name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, base_name)
            if body_id is not None and hasattr(data, "cvel"):
                state["base_velocity"] = _finite_vector(model_vector(data.cvel[body_id]))

    sensor_values: dict[str, list[float] | float] = {}
    if "imu_observations" in observation_kinds or observations.get("sensor_names"):
        sensor_names = observations.get("sensor_names", ())
        if isinstance(sensor_names, list) and hasattr(data, "sensordata"):
            for name in sensor_names:
                if not isinstance(name, str):
                    continue
                sensor_type = getattr(mujoco.mjtObj, "mjOBJ_SENSOR", None)
                if sensor_type is None:
                    continue
                sensor_id = _safe_name_id(mujoco, model, sensor_type, name)
                if sensor_id is None:
                    continue
                adr = int(model.sensor_adr[sensor_id])
                dim = int(model.sensor_dim[sensor_id])
                values = _finite_vector(data.sensordata[adr : adr + dim])
                sensor_values[name] = values[0] if dim == 1 else values
    if sensor_values:
        state["sensor_values"] = sensor_values

    contact_names = public_names["geoms"]
    if contact_names and (
        "contact_state" in observation_kinds or "foot_contacts" in observation_kinds
    ):
        contact_ids = {
            name: _safe_name_id(mujoco, model, mujoco.mjtObj.mjOBJ_GEOM, name)
            for name in contact_names
        }
        active: set[str] = set()
        for index in range(int(getattr(data, "ncon", 0))):
            contact = data.contact[index]
            for geom_id in (int(contact.geom1), int(contact.geom2)):
                for name, selected in contact_ids.items():
                    if selected is not None and geom_id == selected:
                        active.add(name)
        state["contact_state"] = {
            "active_geoms": sorted(active),
            "active_count": len(active),
            "configured_geoms": sorted(contact_ids),
        }
    return _finite_state(state)


def _public_symbol_names(observations: Mapping[str, Any]) -> dict[str, set[str]]:
    sites: set[str] = set()
    bodies: set[str] = set()
    geoms: set[str] = set()
    for key in ("end_effector_site", "native_pinch_site", "arm_attachment_site", "base_site", "imu_site"):
        value = observations.get(key)
        if isinstance(value, str):
            sites.add(value)
    for key in ("site_names", "foot_sites", "imu_sites"):
        value = observations.get(key)
        if isinstance(value, list):
            sites.update(item for item in value if isinstance(item, str))
    for key in ("end_effector_body", "arm_attachment_body", "gripper_base_body", "gripper_mount_body", "base_body", "lift_body", "wrist_body", "torso_body"):
        value = observations.get(key)
        if isinstance(value, str):
            bodies.add(value)
    for key in ("head_bodies", "foot_bodies", "object_bodies"):
        value = observations.get(key)
        if isinstance(value, list):
            bodies.update(item for item in value if isinstance(item, str))
    for key in ("gripper_contact_geoms", "contact_geoms", "fingertip_geoms"):
        value = observations.get(key)
        if isinstance(value, list):
            geoms.update(item for item in value if isinstance(item, str))
        elif isinstance(value, Mapping):
            geoms.update(item for item in value if isinstance(item, str))
    return {"sites": sites, "bodies": bodies, "geoms": geoms}


def _safe_name_id(mujoco: Any, model: Any, object_type: Any, name: str) -> int | None:
    try:
        identifier = int(mujoco.mj_name2id(model, object_type, name))
    except Exception:
        return None
    return identifier if identifier >= 0 else None


def _finite_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PublicObservationError("public state contains a non-numeric value") from exc
    if not math.isfinite(result):
        raise PublicObservationError("public state contains a non-finite value")
    return result


def _finite_vector(value: Any) -> list[float]:
    try:
        return [_finite_float(item) for item in value]
    except TypeError as exc:
        raise PublicObservationError("public state vector is not iterable") from exc


def model_vector(value: Any) -> Any:
    """Keep array conversion in one tiny helper for fake MuJoCo test objects."""

    return value


def _matrix_value(value: Any, row: int, column: int) -> Any:
    """Read a MuJoCo matrix from either ndarray-like or nested fake storage."""

    try:
        return value[row, column]
    except (IndexError, KeyError, TypeError):
        return value[row][column]


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
        if isinstance(item, str):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise PublicObservationError("public state contains a non-finite value")
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, Mapping):
            for child in item.values():
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
