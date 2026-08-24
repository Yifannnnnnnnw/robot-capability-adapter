"""Trusted Harness adapter for the isolated corrected-R1 audit."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mujoco

from autoadapter2.b2.task_harness import evaluate_b2_task_harness
from autoadapter2.harness.runner import HarnessError, _read_object
from autoadapter2.libraries import RobotPackage

from .contact_policy import build_geom_metadata, evaluate_contact_integrity


AUDIT_ID = "AA2-B2-CORRECTED-R1"
AUDIT_REVISION = "1.0.0"
SUITE_ARTIFACT_TYPE = "b2_corrected_r1_task_suite"
R23_AUDIT_ID = "AA2-B2-CORRECTED-R23"
R23_AUDIT_REVISION = "1.0.0"
R23_SUITE_ARTIFACT_TYPE = "b2_corrected_r23_task_suite"

_SUPPORTED_SUITES = {
    SUITE_ARTIFACT_TYPE: {
        "document_id": AUDIT_ID,
        "revision": AUDIT_REVISION,
    },
    R23_SUITE_ARTIFACT_TYPE: {
        "document_id": R23_AUDIT_ID,
        "revision": R23_AUDIT_REVISION,
    },
}

_GO2_FIXTURES: dict[str, tuple[set[str], set[str]]] = {
    "GO2-T02": ({"floor", "lee_step"}, set()),
    "GO2-T03": ({"floor", "lee_upper_platform"}, set()),
    "GO2-T06": (
        {"floor"},
        {
            "inclined_platform",
            "raised_platform",
            "stair_1",
            "stair_2",
            "stair_3",
            "block_1",
            "block_2",
            "block_3",
        },
    ),
    "GO2-T16": ({"floor", "start_table", "end_table"}, set()),
    "GO2-T17": ({"floor"}, set()),
}

_SO101_FIXTURES: dict[str, tuple[set[str], set[str]]] = {
    "mw_push_to_goal": ({"work_surface"}, {"push_goal_marker"}),
    "mw_sweep_into_goal": (
        {"table_left", "table_right", "table_front", "table_back"},
        {"goal_catch"},
    ),
    "mw_pick_place": ({"work_surface"}, {"pick_place_goal_marker"}),
    "mw_pick_place_wall": ({"work_surface"}, {"wall_pick_goal_marker"}),
    "mw_dial_turn": (set(), set()),
}


def evaluate_corrected_task_harness(
    *,
    package: RobotPackage,
    task_suite_path: str | Path,
    instance_id: str,
    replicate_id: str,
    session_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the existing trusted task measurements with corrected contact policy."""

    suite_path = Path(task_suite_path).resolve()
    suite = _read_object(suite_path)
    suite_identity = corrected_suite_identity(suite)
    if suite.get("formal_episode") is not False:
        raise HarnessError(
            "corrected R1 task suite must explicitly declare formal_episode=false"
        )
    report = evaluate_b2_task_harness(
        package=package,
        task_suite_path=suite_path,
        instance_id=instance_id,
        replicate_id=replicate_id,
        session_result=session_result,
        suite_artifact_type=str(suite["artifact_type"]),
        suite_identity_field="audit_identity",
        suite_document_id=suite_identity["document_id"],
        suite_revision=suite_identity["revision"],
    )
    worker = session_result.get("worker")
    if not isinstance(worker, Mapping):
        raise HarnessError("corrected audit session has no worker evidence")
    evidence = worker.get("physical_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    scene_path = (package.root / str(report["scene_entrypoint"])).resolve()
    contact_integrity = evaluate_scene_contact_integrity(
        physical_evidence=evidence,
        robot_configuration_id=package.robot_configuration_id,
        task_id=str(report["task_id"]),
        scene_path=scene_path,
    )
    guards = report.get("guard_outcomes")
    guard_outcomes = guards if isinstance(guards, Mapping) else {}
    physical_integrity_passed = bool(
        report.get("physical_execution_passed") is True
        and contact_integrity.get("passed") is True
        and report.get("guard_error") is None
        and guard_outcomes
        and all(value is True for value in guard_outcomes.values())
    )
    passed = bool(
        report.get("task_metric_passed") is True
        and physical_integrity_passed
        and report.get("video_complete") is True
    )
    report.update(
        {
            "audit_identity": {
                **suite_identity,
            },
            "formal_episode": False,
            "contact_integrity": contact_integrity,
            "physical_integrity_passed": physical_integrity_passed,
            "physical_harness_verdict": "PASS" if passed else "FAIL",
        }
    )
    return report


def corrected_suite_identity(suite: Mapping[str, Any]) -> dict[str, str]:
    artifact_type = suite.get("artifact_type")
    expected = _SUPPORTED_SUITES.get(artifact_type)
    if expected is None or suite.get("audit_identity") != expected:
        raise HarnessError("corrected task suite has an incompatible identity")
    return dict(expected)


def evaluate_scene_contact_integrity(
    *,
    physical_evidence: Mapping[str, Any],
    robot_configuration_id: str,
    task_id: str,
    scene_path: str | Path,
) -> dict[str, Any]:
    """Resolve scene names/sizes and apply the audit's explicit pair declaration."""

    model = mujoco.MjModel.from_xml_path(str(Path(scene_path).resolve()))
    geom_metadata = build_geom_metadata(model=model, mujoco_module=mujoco)
    semantic_roles, expected_pairs = _contact_declaration(
        model=model,
        robot_configuration_id=robot_configuration_id,
        task_id=task_id,
    )
    return evaluate_contact_integrity(
        physical_evidence,
        robot_family=robot_configuration_id,
        task_id=task_id,
        geom_metadata=geom_metadata,
        semantic_roles=semantic_roles,
        expected_pairs=expected_pairs,
    )


def _contact_declaration(
    *,
    model: Any,
    robot_configuration_id: str,
    task_id: str,
) -> tuple[dict[str, str], set[tuple[str, str]]]:
    geom_records = [_geom_record(model, geom_id) for geom_id in range(int(model.ngeom))]
    roles: dict[str, str] = {}
    expected: set[tuple[str, str]] = set()

    if robot_configuration_id == "unitree-go2-stock-12dof":
        if task_id not in _GO2_FIXTURES:
            raise HarnessError(f"no corrected Go2 contact declaration for {task_id!r}")
        support_names, obstacle_names = _GO2_FIXTURES[task_id]
        moving: list[str] = []
        fixtures: list[str] = []
        for geom_name, body_name, _body_id in geom_records:
            if geom_name in {"FL", "FR", "RL", "RR"}:
                roles[geom_name] = "foot"
                moving.append(geom_name)
            elif body_name.endswith("_calf"):
                roles[geom_name] = "calf"
                moving.append(geom_name)
            if geom_name in support_names:
                roles[geom_name] = "support"
                fixtures.append(geom_name)
            elif geom_name in obstacle_names:
                roles[geom_name] = "obstacle"
                fixtures.append(geom_name)
        expected.update((moving_geom, fixture) for moving_geom in moving for fixture in fixtures)
        return roles, expected

    if robot_configuration_id != "robotstudio_so101":
        raise HarnessError(
            f"unsupported corrected contact robot {robot_configuration_id!r}"
        )
    if task_id not in _SO101_FIXTURES:
        raise HarnessError(f"no corrected SO-101 contact declaration for {task_id!r}")
    support_names, goal_names = _SO101_FIXTURES[task_id]
    workpiece: list[str] = []
    dial: list[str] = []
    gripper: list[str] = []
    fixtures: list[str] = []
    for geom_name, _body_name, body_id in geom_records:
        if _body_has_ancestor(model, body_id, "workpiece"):
            roles[geom_name] = "workpiece"
            workpiece.append(geom_name)
        elif _body_has_ancestor(model, body_id, "dial"):
            roles[geom_name] = "dial"
            dial.append(geom_name)
        elif _body_has_ancestor(model, body_id, "gripper"):
            roles[geom_name] = "gripper"
            gripper.append(geom_name)
        if geom_name in support_names:
            roles[geom_name] = "support"
            fixtures.append(geom_name)
        elif geom_name in goal_names:
            roles[geom_name] = "goal"
            fixtures.append(geom_name)
    expected.update((object_geom, fixture) for object_geom in workpiece for fixture in fixtures)
    expected.update((object_geom, tool_geom) for object_geom in workpiece for tool_geom in gripper)
    expected.update((dial_geom, tool_geom) for dial_geom in dial for tool_geom in gripper)
    return roles, expected


def _geom_record(model: Any, geom_id: int) -> tuple[str, str, int]:
    geom_name = (
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
        or f"geom_{geom_id}"
    )
    body_id = int(model.geom_bodyid[geom_id])
    body_name = (
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        or f"body_{body_id}"
    )
    return geom_name, body_name, body_id


def _body_has_ancestor(model: Any, body_id: int, ancestor_name: str) -> bool:
    current = body_id
    while current >= 0:
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, current)
        if name == ancestor_name:
            return True
        if current == 0:
            break
        current = int(model.body_parentid[current])
    return False


__all__ = [
    "AUDIT_ID",
    "AUDIT_REVISION",
    "R23_AUDIT_ID",
    "R23_AUDIT_REVISION",
    "corrected_suite_identity",
    "evaluate_corrected_task_harness",
    "evaluate_scene_contact_integrity",
]
