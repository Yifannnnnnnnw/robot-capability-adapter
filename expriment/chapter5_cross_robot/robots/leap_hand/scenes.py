"""Prepare the five central LEAP Hand downstream task scenes.

The robot is always the maintained right LEAP Hand from
``AA1/assets/mjcf/leap_hand/scene_right.xml``.  This module only creates
experiment-local XML scenes and returns the explicit task contracts consumed
by the Chapter 5 task runner.  It does not generate or invoke a driver.

The source LEAP scene includes ``right_hand.xml`` with a relative mesh
directory.  A one-file copy of that include is written beside the prepared
scenes with only the mesh directory made absolute; robot bodies, joints,
actuators, sensors, and their numeric values are otherwise preserved.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
LEAP_ASSETS = ROOT / "AA1/assets/mjcf/leap_hand"
LEAP_SCENE = LEAP_ASSETS / "scene_right.xml"
LEAP_HAND = LEAP_ASSETS / "right_hand.xml"
ROBEL_ASSETS = HERE / "scenes/leap_robel_assets"
ROBEL_VALVE = ROBEL_ASSETS / "valve3.xml"
ROBEL_DEPENDENCIES = ROBEL_ASSETS / "dependencies.xml"
ROBEL_PROBE = ROBEL_ASSETS / "fixture_probe.xml"
ROBEL_MESHES = ROBEL_ASSETS / "meshes"

TASKS = (
    "gym_hand_reach_all_fingertips",
    "robel_dclaw_pose_fixed",
    "myosuite_object_hold_fixed",
    "gym_hand_manipulate_block_full_pose",
    "robel_dclaw_turn_fixed",
)

JOINTS = (
    "if_mcp", "if_rot", "if_pip", "if_dip",
    "mf_mcp", "mf_rot", "mf_pip", "mf_dip",
    "rf_mcp", "rf_rot", "rf_pip", "rf_dip",
    "th_cmc", "th_axl", "th_mcp", "th_ipl",
)
TIP_GEOMS = {
    "index": "if_tip",
    "middle": "mf_tip",
    "ring": "rf_tip",
    "thumb": "th_tip",
}

# The source task registrations and model files establish these control
# periods.  Reach/Block use Gymnasium's 20*0.002 s; Hold uses MyoSuite's
# frame_skip=10*0.002 s; Pose and Turn are fixed by ROBEL's registrations.
CONTROL_DT_S = {
    "gym_hand_reach_all_fingertips": 0.04,
    "robel_dclaw_pose_fixed": 0.05,
    "myosuite_object_hold_fixed": 0.02,
    "gym_hand_manipulate_block_full_pose": 0.04,
    "robel_dclaw_turn_fixed": 0.1,
}
HORIZON = {
    "gym_hand_reach_all_fingertips": 50,
    "robel_dclaw_pose_fixed": 80,
    "myosuite_object_hold_fixed": 75,
    "gym_hand_manipulate_block_full_pose": 100,
    "robel_dclaw_turn_fixed": 40,
}

REACH_ERROR_M = 0.00894427190999916
POSE_ERROR_RAD = 10.0 * math.pi / 180.0
MAX_ALLOWED_INITIAL_OBJECT_PENETRATION_M = 0.0002

# scene_right.xml fixes the palm at this pose.  The palm has no free or
# floating joint, so this frame remains fixed throughout every case.
PALM_POSITION_WORLD_M = (0.0, 0.0, 0.1)
PALM_QUATERNION_WXYZ = (0.0, 1.0, 0.0, 0.0)
PALM_ROTATION_WORLD_FROM_LOCAL = (
    (1.0, 0.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, -1.0),
)

# A modest, reachable target generated from one nearby joint configuration.
# The values are in the public [index, middle, ring, thumb] order, xyz
# contiguous for each fingertip, as required by HandReach.
REACH_TARGET_PALM_M = (
    0.11476879, 0.00386655, -0.03204712,
    0.11466879, -0.04153345, -0.03204712,
    0.11466885, -0.08693295, -0.03204612,
    -0.06283928, 0.11981724, -0.02840804,
)

# A non-home fixed pose.  Values stay inside the maintained canonical LEAP
# joint limits and deliberately put the no-action initial state outside the
# 10-degree terminal bound.
POSE_TARGET_RAD = (
    0.25, 0.20, 0.50, 0.40,
    0.25, 0.20, 0.50, 0.40,
    0.25, 0.20, 0.50, 0.40,
    0.20, 0.15, 0.40, 0.30,
)

# The source MyoSuite object/goal are translated by one explicit rigid-frame
# offset into the canonical world frame.  This preserves the source vector
# [-0.005, -0.010, +0.020] m and places the object over the palm.  The shared
# reset lift removes the observed 4.6 mm palm penetration while retaining
# physical palm support.
OBJECT_RESET_LIFT_M = 0.0045
HOLD_OBJECT_PALM_M = (-0.035, 0.0, -0.060 - OBJECT_RESET_LIFT_M)
HOLD_TARGET_PALM_M = (-0.040, 0.010, -0.080 - OBJECT_RESET_LIFT_M)
HOLD_OBJECT_SOURCE_M = (-0.235, -0.510, 1.450)
HOLD_GOAL_SOURCE_M = (-0.240, -0.520, 1.470)
HOLD_OBJECT_SIZE_M = (0.025, 0.036, 0.030)
HOLD_SOURCE_TO_SCENE_TRANSLATION_M = (
    0.200, 0.510, -1.290 + OBJECT_RESET_LIFT_M,
)

# The block task uses a deterministic central source-range offset.  The cube
# starts palm-supported; its initial world orientation is identity, so the
# source offset is expressed directly in its initial object frame.
BLOCK_OBJECT_WORLD_M = (-0.035, 0.0, 0.155 + OBJECT_RESET_LIFT_M)
BLOCK_TARGET_OFFSET_OBJECT_M = (0.020, -0.020, 0.030)
BLOCK_TARGET_WORLD_M = tuple(
    BLOCK_OBJECT_WORLD_M[i] + BLOCK_TARGET_OFFSET_OBJECT_M[i]
    for i in range(3)
)
# Actual object orientation is identity in world coordinates.  In the palm
# frame that is a 180-degree x rotation.  The requested target is 20 degrees
# away from that pose, which makes the full-pose criterion non-trivial while
# remaining reachable.
BLOCK_TARGET_ORIENTATION_PALM_WXYZ = (-0.17364817766693033, 0.984807753012208, 0.0, 0.0)

# Fixed-axis three-lobed fixture.  The fixture body and its three physical
# capsule lobes come from the pinned ROBEL ``valve3.xml`` source asset.  Only
# this wrapper placement is experiment-local; the source body tree, joint,
# dimensions, and friction values are retained.
# At the all-zero hand reset this rigid placement gives the source valve's
# physical lobes three fingertip contacts (index/middle/ring) with sub-mm
# solver penetration; the source body itself is otherwise unchanged.
TURN_FIXTURE_MOUNT_WORLD_M = (0.150, 0.050, 0.0495)


def _fmt(values: tuple[float, ...] | list[float]) -> str:
    return " ".join(f"{float(value):.12g}" for value in values)


def _parse_robel_include(path: Path) -> ET.Element:
    """Parse a source ROBEL include whose license precedes its XML header."""

    text = path.read_text(encoding="utf-8")
    start = text.find("<mujocoinclude")
    if start < 0:
        raise ValueError(f"source ROBEL include has no mujocoinclude root: {path}")
    return ET.fromstring(text[start:])


def _local_to_world(values: tuple[float, float, float]) -> tuple[float, float, float]:
    """Map a fixed palm-frame point into the canonical world frame."""

    return tuple(
        PALM_POSITION_WORLD_M[row]
        + sum(PALM_ROTATION_WORLD_FROM_LOCAL[row][column] * values[column]
              for column in range(3))
        for row in range(3)
    )


def _quat_mul(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _robot_initial() -> dict:
    """Return an explicit all-zero 16-joint initial state."""

    return {
        "robot": {"qpos_by_joint": {name: 0.0 for name in JOINTS}},
        "settle_s": 0.0,
    }


def _source_control(task_id: str) -> tuple[float, int, float]:
    dt = CONTROL_DT_S[task_id]
    horizon = HORIZON[task_id]
    return dt, horizon, dt * horizon


def _add_material(asset: ET.Element, name: str, rgba: str) -> None:
    ET.SubElement(asset, "material", {"name": name, "rgba": rgba})


def _add_marker(
    worldbody: ET.Element,
    *,
    body_name: str,
    geom_name: str,
    position: tuple[float, float, float],
    size: tuple[float, ...],
    geom_type: str = "sphere",
    quaternion: tuple[float, float, float, float] = PALM_QUATERNION_WXYZ,
    material: str = "leap_target",
) -> None:
    body = ET.SubElement(worldbody, "body", {
        "name": body_name,
        "pos": _fmt(position),
        "quat": _fmt(quaternion),
    })
    ET.SubElement(body, "geom", {
        "name": geom_name,
        "type": geom_type,
        "size": _fmt(size),
        "material": material,
        "contype": "0",
        "conaffinity": "0",
        "density": "0",
    })


def _add_free_object(
    worldbody: ET.Element,
    *,
    body_name: str,
    geom_name: str,
    geom_type: str,
    size: tuple[float, ...],
    density: float,
    quaternion: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
) -> None:
    body = ET.SubElement(worldbody, "body", {
        "name": body_name,
        "pos": "0 0 0",
        "quat": _fmt(quaternion),
    })
    ET.SubElement(body, "freejoint", {"name": f"{body_name}_free"})
    ET.SubElement(body, "geom", {
        "name": geom_name,
        "type": geom_type,
        "size": _fmt(size),
        "density": f"{density:.12g}",
        "friction": "1.0 0.05 0.01",
        "condim": "3",
        "material": "leap_object",
    })


def _add_source_fixture_support(root: ET.Element) -> None:
    """Add only the pinned ROBEL fixture assets/defaults used by valve3."""

    required = [ROBEL_VALVE, ROBEL_DEPENDENCIES, ROBEL_PROBE]
    required.extend(ROBEL_MESHES / name for name in (
        "valve_3_high_poly.stl", "xh_base_high_poly.stl", "xh_base_hull.stl"))
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "pinned ROBEL valve3 assets are unavailable: "
            + ", ".join(str(path) for path in missing))

    asset = root.find("asset")
    dependencies = _parse_robel_include(ROBEL_DEPENDENCIES)
    mesh_files = {
        "motor": ROBEL_MESHES / "xh_base_high_poly.stl",
        "motor_hull": ROBEL_MESHES / "xh_base_hull.stl",
        "valve_3": ROBEL_MESHES / "valve_3_high_poly.stl",
    }
    for child in dependencies.find("asset"):
        if child.tag == "mesh" and child.get("name") in mesh_files:
            mesh = copy.deepcopy(child)
            mesh.set("file", str(mesh_files[child.get("name")].resolve()))
            asset.append(mesh)
        elif child.tag == "material":
            asset.append(copy.deepcopy(child))

    # ``valve3.xml`` uses the source ``station`` childclass.  Keep its
    # dependency defaults intact and place them before worldbody so MuJoCo
    # resolves the original class names without inheriting LEAP defaults.
    defaults = copy.deepcopy(dependencies.find("default"))
    if defaults is None:
        raise ValueError("pinned ROBEL fixture has no default hierarchy")
    # ``scene_right.xml`` has a tighter LEAP contact default than ROBEL's
    # station dependency.  State the source station values explicitly so the
    # copied fixture cannot inherit those robot defaults silently.  The
    # nested plastic class below still overrides friction with the source
    # plastic value.
    station = next((node for node in defaults.iter("default")
                    if node.get("class") == "station"), None)
    if station is None:
        raise ValueError("pinned ROBEL fixture has no station default")
    station_geom = station.find("geom")
    if station_geom is None:
        raise ValueError("pinned ROBEL fixture has no station geom default")
    station_geom.set("solref", "0.02 1")
    station_geom.set("solimp", "0.9 0.95 0.001 0.5 2")
    station_geom.set("friction", "1 0.005 0.0001")
    worldbody_index = list(root).index(root.find("worldbody"))
    root.insert(worldbody_index, defaults)


def _set_source_inertial(
    body: ET.Element,
    *,
    pos: tuple[float, float, float],
    quat: tuple[float, float, float, float],
    mass: float,
    diagonal_inertia: tuple[float, float, float],
) -> None:
    """Pin the source-compiled inertia when the source compiler is omitted."""

    inertial = body.find("inertial")
    if inertial is None:
        inertial = ET.Element("inertial")
        body.insert(0, inertial)
    inertial.attrib.clear()
    inertial.attrib.update({
        "pos": _fmt(pos),
        "quat": _fmt(quat),
        "mass": f"{mass:.16g}",
        "diaginertia": _fmt(diagonal_inertia),
    })


def _add_source_turn_fixture(root: ET.Element) -> None:
    """Place the original ROBEL valve3 body under one fixed scene wrapper."""

    worldbody = root.find("worldbody")
    source_root = _parse_robel_include(ROBEL_VALVE)
    source_body = source_root.find("body")
    if source_body is None:
        raise ValueError("pinned ROBEL valve3.xml has no valve_base body")
    mount = ET.SubElement(worldbody, "body", {
        "name": "turn_fixture_mount",
        "pos": _fmt(TURN_FIXTURE_MOUNT_WORLD_M),
    })
    fixture = copy.deepcopy(source_body)
    # The source XML relies on ``inertiagrouprange='3 5'`` while the LEAP
    # include cannot use that global compiler setting: it would exclude the
    # robot's group-0 collision geoms.  These values are the source-compiled
    # inertials from fixture_probe.xml and preserve the source body dynamics
    # without changing the robot compiler.
    _set_source_inertial(
        fixture,
        pos=(0.01193720026811237, -0.00012361248268293095,
             0.018043591148653074),
        quat=(0.500199042059942, 0.500199042059942,
              0.4998008786730142, 0.4998008786730142),
        mass=0.05385993948123726,
        diagonal_inertia=(1.6567442844388007e-05,
                          1.4890553762476005e-05,
                          9.97818877475281e-06),
    )
    valve = fixture.find("body[@name='valve']")
    if valve is None:
        raise ValueError("pinned ROBEL valve3 body has no valve child")
    _set_source_inertial(
        valve,
        pos=(-6.128293619122214e-20, 2.364145860960807e-19,
             0.050083189365265665),
        quat=(0.00018114269274317833, 0.7071067579844836,
              3.222313305230874e-05, 0.7071067804523388),
        mass=0.4764350922875066,
        diagonal_inertia=(0.0009999380117320824,
                          0.0006127118395286085,
                          0.0005929049835245614),
    )
    # The source site is a visual marker and is not needed for the independent
    # scorer.  Removing it keeps all task bindings on measured geom/body/joint
    # values while leaving the source physical fixture unchanged.
    for site in fixture.iter("site"):
        parent = next((candidate for candidate in fixture.iter()
                       if site in list(candidate)), None)
        if parent is not None:
            parent.remove(site)
    mount.append(fixture)


def _scene_root(robot_include: Path) -> ET.Element:
    root = copy.deepcopy(ET.parse(LEAP_SCENE).getroot())
    root.find("include").set("file", str(robot_include.resolve()))
    asset = root.find("asset")
    _add_material(asset, "leap_target", "0.1 0.8 0.2 0.65")
    _add_material(asset, "leap_object", "0.8 0.35 0.1 1")
    _add_material(asset, "leap_fixture", "0.15 0.25 0.85 1")
    return root


def _write_robot_include(destination: Path) -> Path:
    include = destination / "leap_right_hand.xml"
    if include.exists():
        raise FileExistsError(f"refusing to overwrite prepared robot include: {include}")
    root = copy.deepcopy(ET.parse(LEAP_HAND).getroot())
    compiler = root.find("compiler")
    compiler.set("meshdir", str((LEAP_ASSETS / "assets").resolve()))
    ET.indent(root)
    ET.ElementTree(root).write(include, encoding="unicode")
    return include


def _common_parameters(task_id: str) -> dict:
    dt, horizon, duration = _source_control(task_id)
    return {
        "control_dt_s": dt,
        "horizon": horizon,
        "max_sim_time_s": duration,
        "palm_body_name": "palm",
        "joint_order": list(JOINTS),
        "fingertip_order": list(TIP_GEOMS),
    }


def _build_reach_scene(robot_include: Path) -> tuple[ET.Element, dict]:
    task_id = "gym_hand_reach_all_fingertips"
    dt, horizon, duration = _source_control(task_id)
    root = _scene_root(robot_include)
    worldbody = root.find("worldbody")
    for label, start in zip(TIP_GEOMS, range(0, 12, 3)):
        local = tuple(REACH_TARGET_PALM_M[start:start + 3])
        _add_marker(
            worldbody,
            body_name=f"reach_target_{label}",
            geom_name=f"reach_target_{label}_geom",
            position=_local_to_world(local),
            size=(0.004,),
        )
    bindings = {"palm": {"kind": "body", "name": "palm"}}
    for label, geom in TIP_GEOMS.items():
        bindings[f"{label}_tip"] = {"kind": "geom", "name": geom}
        bindings[f"target_{label}"] = {
            "kind": "geom", "name": f"reach_target_{label}_geom",
        }
    spec = {
        "type": "leap_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": duration,
        "horizon": horizon,
        "control_dt_s": dt,
        "terminal_step": horizon,
        "success_threshold_m": REACH_ERROR_M,
    }
    parameters = {
        **_common_parameters(task_id),
        # Source invocation fields.
        "target_fingertip_positions_m": list(REACH_TARGET_PALM_M),
        "max_control_steps": horizon,
        # Scorer/scene fields.
        "target_fingertip_positions_palm_m": list(REACH_TARGET_PALM_M),
        "target_geom_names": [f"reach_target_{label}_geom" for label in TIP_GEOMS],
        "fingertip_measurement_reference": (
            "MuJoCo geom center for the bound tip geoms if_tip, mf_tip, rf_tip, "
            "and th_tip; convert any capability reference to these centers."
        ),
        "success_threshold_m": REACH_ERROR_M,
        "terminal_step": horizon,
    }
    return root, {
        "id": f"{task_id}_central",
        "task_id": task_id,
        "task_description": (
            "Move all four canonical LEAP fingertips in [index, middle, ring, "
            "thumb] order to the requested 12-coordinate palm-frame goal. "
            "The measured fingertip reference is the center of the bound tip "
            "geoms if_tip/mf_tip/rf_tip/th_tip. "
            "At the terminal step of the 50-step/2-second attempt, the "
            "concatenated Cartesian error must be strictly below "
            f"{REACH_ERROR_M:.11g} m."
        ),
        "initial_state": _robot_initial(),
        "parameters": parameters,
        "success_spec": spec,
    }


def _build_pose_scene(robot_include: Path) -> tuple[ET.Element, dict]:
    task_id = "robel_dclaw_pose_fixed"
    dt, horizon, duration = _source_control(task_id)
    root = _scene_root(robot_include)
    bindings = {
        name: {"kind": "joint", "name": name}
        for name in JOINTS
    }
    spec = {
        "type": "leap_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": duration,
        "horizon": horizon,
        "control_dt_s": dt,
        "terminal_step": horizon,
        "success_threshold_rad": POSE_ERROR_RAD,
    }
    parameters = {
        **_common_parameters(task_id),
        # Source invocation fields.
        "target_joint_positions_rad": list(POSE_TARGET_RAD),
        "max_control_steps": horizon,
        "duration_s": duration,
        # Scorer fields.
        "target_joint_positions_rad_by_joint": dict(zip(JOINTS, POSE_TARGET_RAD)),
        "success_threshold_rad": POSE_ERROR_RAD,
        "terminal_step": horizon,
    }
    return root, {
        "id": f"{task_id}_central",
        "task_id": task_id,
        "task_description": (
            "Move all 16 canonical LEAP joints to the requested fixed pose in "
            "public joint order. At the terminal step of the 80-step/4-second "
            "attempt, every absolute joint-position error must be strictly "
            f"below {POSE_ERROR_RAD:.12g} rad (10 degrees)."
        ),
        "initial_state": _robot_initial(),
        "parameters": parameters,
        "success_spec": spec,
    }


def _build_hold_scene(robot_include: Path) -> tuple[ET.Element, dict]:
    task_id = "myosuite_object_hold_fixed"
    dt, horizon, duration = _source_control(task_id)
    root = _scene_root(robot_include)
    worldbody = root.find("worldbody")
    object_world = _local_to_world(HOLD_OBJECT_PALM_M)
    target_world = _local_to_world(HOLD_TARGET_PALM_M)
    _add_free_object(
        worldbody,
        body_name="hold_object",
        geom_name="hold_object_geom",
        geom_type="ellipsoid",
        size=HOLD_OBJECT_SIZE_M,
        density=500.0,
        quaternion=(1.0, 0.0, 0.0, 0.0),
    )
    _add_marker(
        worldbody,
        body_name="hold_target",
        geom_name="hold_target_geom",
        position=target_world,
        size=(0.005,),
    )
    bindings = {
        "palm": {"kind": "body", "name": "palm"},
        "object": {"kind": "body", "name": "hold_object"},
        "target": {"kind": "geom", "name": "hold_target_geom"},
    }
    spec = {
        "type": "leap_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": duration,
        "horizon": horizon,
        "control_dt_s": dt,
        "success_threshold_m": 0.010,
        "drop_threshold_m": 0.300,
        "required_solved_steps": 5,
        "terminal_step": horizon,
    }
    parameters = {
        **_common_parameters(task_id),
        # Source invocation fields and source-equivalent object contract.
        "target_object_position_m": list(HOLD_TARGET_PALM_M),
        "max_control_steps": horizon,
        "object_shape": "ellipsoid",
        "object_size_m": list(HOLD_OBJECT_SIZE_M),
        "source_object_position_m": list(HOLD_OBJECT_SOURCE_M),
        "source_goal_position_m": list(HOLD_GOAL_SOURCE_M),
        "source_to_scene_translation_m": list(HOLD_SOURCE_TO_SCENE_TRANSLATION_M),
        # Scorer/scene fields.
        "object_body_name": "hold_object",
        "target_geom_name": "hold_target_geom",
        "object_initial_position_palm_m": list(HOLD_OBJECT_PALM_M),
        "object_initial_position_world_m": list(object_world),
        "target_object_position_palm_m": list(HOLD_TARGET_PALM_M),
        "target_object_position_world_m": list(target_world),
        "success_threshold_m": 0.010,
        "drop_threshold_m": 0.300,
        "required_solved_steps": 5,
        "terminal_step": horizon,
    }
    initial = _robot_initial()
    initial["free_bodies"] = {
        "hold_object": {
            "position_m": list(object_world),
            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            "linear_velocity_m_s": [0.0, 0.0, 0.0],
            "angular_velocity_rad_s": [0.0, 0.0, 0.0],
        }
    }
    return root, {
        "id": f"{task_id}_central",
        "task_id": task_id,
        "task_description": (
            "Keep the source-equivalent free ellipsoid (semiaxes "
            f"{list(HOLD_OBJECT_SIZE_M)}) within 0.010 m of its fixed palm-frame "
            "target for more than five control samples in the 75-step/1.5-second "
            "attempt, with no object-goal distance above 0.300 m."
        ),
        "initial_state": initial,
        "parameters": parameters,
        "success_spec": spec,
    }


def _build_block_scene(robot_include: Path) -> tuple[ET.Element, dict]:
    task_id = "gym_hand_manipulate_block_full_pose"
    dt, horizon, duration = _source_control(task_id)
    root = _scene_root(robot_include)
    worldbody = root.find("worldbody")
    target_orientation_world = _quat_mul(
        PALM_QUATERNION_WXYZ,
        BLOCK_TARGET_ORIENTATION_PALM_WXYZ,
    )
    _add_free_object(
        worldbody,
        body_name="block_object",
        geom_name="block_object_geom",
        geom_type="box",
        size=(0.025, 0.025, 0.025),
        density=400.0,
        quaternion=(1.0, 0.0, 0.0, 0.0),
    )
    _add_marker(
        worldbody,
        body_name="block_target",
        geom_name="block_target_geom",
        position=BLOCK_TARGET_WORLD_M,
        size=(0.026, 0.026, 0.026),
        geom_type="box",
        quaternion=target_orientation_world,
    )
    target_position_palm = tuple(
        sum(PALM_ROTATION_WORLD_FROM_LOCAL[row][column]
            * (BLOCK_TARGET_WORLD_M[column] - PALM_POSITION_WORLD_M[column])
            for column in range(3))
        for row in range(3)
    )
    bindings = {
        "palm": {"kind": "body", "name": "palm"},
        "object": {"kind": "body", "name": "block_object"},
        "target": {"kind": "geom", "name": "block_target_geom"},
    }
    spec = {
        "type": "leap_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": duration,
        "horizon": horizon,
        "control_dt_s": dt,
        "position_threshold_m": 0.010,
        "orientation_threshold_rad": 0.1,
        "terminal_step": horizon,
    }
    parameters = {
        **_common_parameters(task_id),
        # Source invocation fields.
        "target_position_offset_m": list(BLOCK_TARGET_OFFSET_OBJECT_M),
        "target_orientation_wxyz": list(BLOCK_TARGET_ORIENTATION_PALM_WXYZ),
        "max_control_steps": horizon,
        # Scorer/scene fields.
        "object_body_name": "block_object",
        "target_geom_name": "block_target_geom",
        "object_half_extents_m": [0.025, 0.025, 0.025],
        "object_initial_position_world_m": list(BLOCK_OBJECT_WORLD_M),
        "object_initial_orientation_world_wxyz": [1.0, 0.0, 0.0, 0.0],
        "target_position_world_m": list(BLOCK_TARGET_WORLD_M),
        "target_position_palm_m": list(target_position_palm),
        "target_orientation_world_wxyz": list(target_orientation_world),
        "position_threshold_m": 0.010,
        "orientation_threshold_rad": 0.1,
        "terminal_step": horizon,
    }
    initial = _robot_initial()
    initial["free_bodies"] = {
        "block_object": {
            "position_m": list(BLOCK_OBJECT_WORLD_M),
            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            "linear_velocity_m_s": [0.0, 0.0, 0.0],
            "angular_velocity_rad_s": [0.0, 0.0, 0.0],
        }
    }
    return root, {
        "id": f"{task_id}_central",
        "task_id": task_id,
        "task_description": (
            "Manipulate one free 0.05 m cube from its palm-supported central "
            "start to the requested Cartesian position offset and orientation. "
            "At the terminal step of the 100-step/4-second attempt, position "
            "error must be strictly below 0.010 m and shortest quaternion-angle "
            "error strictly below 0.1 rad in the same state."
        ),
        "initial_state": initial,
        "parameters": parameters,
        "success_spec": spec,
    }


def _build_turn_scene(robot_include: Path) -> tuple[ET.Element, dict]:
    task_id = "robel_dclaw_turn_fixed"
    dt, horizon, duration = _source_control(task_id)
    root = _scene_root(robot_include)
    _add_source_fixture_support(root)
    _add_source_turn_fixture(root)
    bindings = {
        "palm": {"kind": "body", "name": "palm"},
        "fixture": {"kind": "body", "name": "valve"},
        "fixture_joint": {"kind": "joint", "name": "valve_OBJRx"},
    }
    spec = {
        "type": "leap_task",
        "task_id": task_id,
        "bindings": bindings,
        "max_sim_time_s": duration,
        "horizon": horizon,
        "control_dt_s": dt,
        "target_angle_rad": math.pi,
        "initial_angle_rad": 0.0,
        "angle_threshold_rad": 0.1,
        "terminal_step": horizon,
    }
    parameters = {
        **_common_parameters(task_id),
        # Source invocation fields.
        "initial_fixture_angle_rad": 0.0,
        "target_fixture_angle_rad": math.pi,
        "max_control_steps": horizon,
        "duration_s": duration,
        # Scorer/scene fields.
        "fixture_body_name": "valve",
        "fixture_joint_name": "valve_OBJRx",
        "fixture_geometry_source_note": (
            "The pinned ROBEL valve3.xml body and valve_3/xh_base meshes are "
            "retained; only the rigid mount placement is experiment-local."
        ),
        "initial_angle_rad": 0.0,
        "target_angle_rad": math.pi,
        "angle_threshold_rad": 0.1,
        "terminal_step": horizon,
    }
    initial = _robot_initial()
    initial["robot"]["qpos_by_joint"]["valve_OBJRx"] = 0.0
    return root, {
        "id": f"{task_id}_central",
        "task_id": task_id,
        "task_description": (
            "Rotate the real three-lobed, fixed-axis LEAP-hand fixture from "
            "angle 0 toward pi. At the terminal step of the 40-step/4-second "
            "attempt, the absolute wrapped fixture angle error must be strictly "
            "below 0.1 rad."
        ),
        "initial_state": initial,
        "parameters": parameters,
        "success_spec": spec,
    }


def _write_scene(root: ET.Element, path: Path) -> None:
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="unicode")


def _add_joint_trace_bindings(case: dict) -> None:
    """Expose the complete robot joint state in every native trace."""

    bindings = case["success_spec"]["bindings"]
    for name in JOINTS:
        bindings.setdefault(name, {"kind": "joint", "name": name})


def prepare_cases(output: str | Path) -> list[dict]:
    """Write the five central XML scenes and return their task contracts."""

    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    robot_include = _write_robot_include(destination)
    builders = (
        ("gym_hand_reach_all_fingertips", _build_reach_scene),
        ("robel_dclaw_pose_fixed", _build_pose_scene),
        ("myosuite_object_hold_fixed", _build_hold_scene),
        ("gym_hand_manipulate_block_full_pose", _build_block_scene),
        ("robel_dclaw_turn_fixed", _build_turn_scene),
    )
    cases = []
    for task_id, builder in builders:
        scene = destination / f"leap_{task_id}_central.xml"
        if scene.exists():
            raise FileExistsError(f"refusing to overwrite prepared scene: {scene}")
        root, case = builder(robot_include)
        _add_joint_trace_bindings(case)
        _write_scene(root, scene)
        case["scene_path"] = str(scene.resolve())
        cases.append(case)
    return cases


def _joint_value(model, data, name: str) -> float:
    joint_id = model.joint(name).id
    return float(data.qpos[model.jnt_qposadr[joint_id]])


def _body_position(model, data, name: str):
    return data.xpos[model.body(name).id].copy()


def _geom_position(model, data, name: str):
    return data.geom_xpos[model.geom(name).id].copy()


def _support_contacts(model, data, object_body: str) -> list[str]:
    """Return robot bodies physically contacting a free object at reset."""

    object_id = int(model.body(object_body).id)
    names = set()
    for contact in data.contact[:data.ncon]:
        body_ids = (int(model.geom_bodyid[contact.geom1]),
                    int(model.geom_bodyid[contact.geom2]))
        if object_id not in body_ids or contact.dist > 0:
            continue
        support_id = body_ids[1] if body_ids[0] == object_id else body_ids[0]
        if support_id != 0:
            names.add(str(model.body(support_id).name))
    return sorted(names)


def _max_contact_penetration(model, data, body_name: str) -> float:
    """Return the deepest current contact involving one body, in metres."""

    body_id = int(model.body(body_name).id)
    return max(
        (max(0.0, -float(contact.dist))
         for contact in data.contact[:data.ncon]
         if body_id in {
             int(model.geom_bodyid[contact.geom1]),
             int(model.geom_bodyid[contact.geom2]),
         }),
        default=0.0,
    )


def _body_linear_velocity(model, data, body_name: str):
    """Return one body's world-frame linear velocity without changing state."""

    import mujoco
    import numpy as np

    velocity = np.zeros(6)
    mujoco.mj_objectVelocity(
        model, data, mujoco.mjtObj.mjOBJ_BODY,
        model.body(body_name).id, velocity, 0,
    )
    return velocity[3:].copy()


def _metric_snapshot(model, data, case: dict) -> dict:
    """Return diagnostic initial/no-action metrics for one loaded case."""

    import numpy as np

    task_id = case["task_id"]
    params = case["parameters"]
    if task_id == "gym_hand_reach_all_fingertips":
        tips = np.concatenate([_geom_position(model, data, TIP_GEOMS[label])
                               for label in TIP_GEOMS])
        targets = np.concatenate([_geom_position(
            model, data, f"reach_target_{label}_geom") for label in TIP_GEOMS])
        return {"terminal_error_m": float(np.linalg.norm(tips - targets))}
    if task_id == "robel_dclaw_pose_fixed":
        target = np.asarray(params["target_joint_positions_rad"], dtype=float)
        actual = np.asarray([_joint_value(model, data, name) for name in JOINTS])
        return {"max_joint_error_rad": float(np.max(np.abs(actual - target)))}
    if task_id == "myosuite_object_hold_fixed":
        object_position = _body_position(model, data, "hold_object")
        target_position = _geom_position(model, data, "hold_target_geom")
        return {"object_goal_distance_m": float(np.linalg.norm(object_position - target_position))}
    if task_id == "gym_hand_manipulate_block_full_pose":
        object_position = _body_position(model, data, "block_object")
        target_position = _geom_position(model, data, "block_target_geom")
        return {"block_position_error_m": float(np.linalg.norm(object_position - target_position))}
    if task_id == "robel_dclaw_turn_fixed":
        angle = _joint_value(model, data, "valve_OBJRx")
        return {"wrapped_fixture_angle_error_rad": abs(((angle - math.pi + math.pi) % (2 * math.pi)) - math.pi)}
    raise ValueError(f"unknown LEAP task {task_id!r}")


def check_cases(cases: list[dict]) -> list[dict]:
    """Load each real scene, apply its initial state, and push no action."""

    import mujoco
    import numpy as np

    sys.path.insert(0, str(ROOT / "AA1"))
    from auto_adapter.scene_runtime import apply_initial_state

    if [case["task_id"] for case in cases] != list(TASKS):
        raise ValueError("LEAP cases must retain the configured five-task order")
    checked = []
    for case in cases:
        model = mujoco.MjModel.from_xml_path(case["scene_path"])
        data = mujoco.MjData(model)
        apply_initial_state(model, data, case)
        initial = _metric_snapshot(model, data, case)
        spec = case["success_spec"]
        task_id = case["task_id"]
        support_contacts = []
        tracked_object = None
        initial_max_contact_penetration_m = None
        if task_id == "myosuite_object_hold_fixed":
            tracked_object = "hold_object"
            support_contacts = _support_contacts(model, data, "hold_object")
            if not support_contacts:
                raise ValueError(f"hold case has no physical reset support: {case['id']}")
        elif task_id == "gym_hand_manipulate_block_full_pose":
            tracked_object = "block_object"
            support_contacts = _support_contacts(model, data, "block_object")
            if not support_contacts:
                raise ValueError(f"block case has no physical reset support: {case['id']}")
        elif task_id == "robel_dclaw_turn_fixed":
            # The source turn contract does not require a reset grasp, but
            # retain the observed contact set as a placement diagnostic.
            support_contacts = _support_contacts(model, data, "valve")
        if tracked_object is not None:
            initial_max_contact_penetration_m = _max_contact_penetration(
                model, data, tracked_object,
            )
            if initial_max_contact_penetration_m > MAX_ALLOWED_INITIAL_OBJECT_PENETRATION_M:
                raise ValueError(
                    f"{task_id} reset penetration exceeds "
                    f"{MAX_ALLOWED_INITIAL_OBJECT_PENETRATION_M:g} m: "
                    f"{initial_max_contact_penetration_m:g} m"
                )
        if task_id == "gym_hand_reach_all_fingertips" and initial["terminal_error_m"] <= spec["success_threshold_m"]:
            raise ValueError(f"reach case starts inside goal: {case['id']}")
        if task_id == "robel_dclaw_pose_fixed" and initial["max_joint_error_rad"] < spec["success_threshold_rad"]:
            raise ValueError(f"pose case starts inside goal: {case['id']}")
        if task_id == "myosuite_object_hold_fixed" and initial["object_goal_distance_m"] <= spec["success_threshold_m"]:
            raise ValueError(f"hold case starts solved: {case['id']}")
        if task_id == "gym_hand_manipulate_block_full_pose" and initial["block_position_error_m"] <= spec["position_threshold_m"]:
            raise ValueError(f"block case starts inside position goal: {case['id']}")
        if task_id == "robel_dclaw_turn_fixed" and initial["wrapped_fixture_angle_error_rad"] <= spec["angle_threshold_rad"]:
            raise ValueError(f"turn case starts inside angle goal: {case['id']}")
        for _ in range(10):
            mujoco.mj_step(model, data)
        after = _metric_snapshot(model, data, case)
        after_20ms_linear_velocity_m_s = None
        if tracked_object is not None:
            after_20ms_linear_velocity_m_s = _body_linear_velocity(
                model, data, tracked_object,
            ).tolist()
        if not all(np.isfinite(array).all() for array in (data.qpos, data.qvel, data.ctrl)):
            raise ValueError(f"non-finite no-action scene state: {case['id']}")
        checked.append({
            "case": case["id"],
            "task_id": task_id,
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "initial": initial,
            "initial_support_contacts": support_contacts,
            "initial_max_contact_penetration_m": initial_max_contact_penetration_m,
            "after_10_native_steps": after,
            "after_0.02s_object_linear_velocity_m_s": after_20ms_linear_velocity_m_s,
            "initial_sim_time_s": 0.0,
            "after_10_native_steps_sim_time_s": float(data.time),
            "finite": True,
            "no_action": True,
        })
    return checked


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    prepared = prepare_cases(args.output_dir)
    print(json.dumps(check_cases(prepared) if args.check else prepared,
                       indent=2, ensure_ascii=False))
