"""Pair-aware physical-integrity policy for the corrected-R1 audit.

The original B2 Harness remains untouched.  This module consumes its recorded
``physical_evidence`` plus trusted geometry/role declarations and returns a
JSON-ready rejudication.  A pair receives the geometry proxy only when it is
both explicitly declared for the task and allowed by the narrow robot policy;
everything else retains the original 5 mm limit.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import math
from typing import Any


DEFAULT_PENETRATION_LIMIT_M = 0.005
EXPECTED_CONTACT_BUFFER_M = 0.006
EXPECTED_CONTACT_CAP_M = 0.030


def build_geom_metadata(*, model: Any, mujoco_module: Any) -> dict[str, dict[str, Any]]:
    """Return the trusted geom metadata needed by the pure policy evaluator.

    ``model.geom_size`` already stores MuJoCo collision half-sizes/radii.  Zero
    padding is ignored, so this also works for sphere, capsule, cylinder, box,
    ellipsoid, and plane records without reinterpreting geom types.
    """

    metadata: dict[str, dict[str, Any]] = {}
    for geom_id in range(int(model.ngeom)):
        geom_name = (
            mujoco_module.mj_id2name(
                model, mujoco_module.mjtObj.mjOBJ_GEOM, geom_id
            )
            or f"geom_{geom_id}"
        )
        body_id = int(model.geom_bodyid[geom_id])
        body_name = (
            mujoco_module.mj_id2name(
                model, mujoco_module.mjtObj.mjOBJ_BODY, body_id
            )
            or f"body_{body_id}"
        )
        positive_sizes = [
            float(value)
            for value in model.geom_size[geom_id]
            if math.isfinite(float(value)) and float(value) > 0.0
        ]
        metadata[geom_name] = {
            "body_name": body_name,
            "minimum_positive_half_size_m": (
                min(positive_sizes) if positive_sizes else None
            ),
        }
    return metadata


def evaluate_contact_integrity(
    physical_evidence: Mapping[str, Any],
    *,
    robot_family: str,
    task_id: str,
    geom_metadata: Mapping[str, Mapping[str, Any]],
    semantic_roles: Mapping[str, str],
    expected_pairs: Collection[tuple[str, str]],
) -> dict[str, Any]:
    """Evaluate every recorded contact pair under the corrected audit policy.

    ``semantic_roles`` may key either a geom name or its body name.  Trusted
    task configuration supplies ``expected_pairs``; merely assigning compatible
    roles is insufficient to obtain the proxy.  This prevents an unrelated
    fixture from being silently admitted.
    """

    family = _normalise_family(robot_family)
    declared_pairs = {_canonical_pair(pair) for pair in expected_pairs}
    result: dict[str, Any] = {
        "policy": "corrected_r1_pair_aware_v1",
        "task_id": task_id,
        "contact_monitoring_complete": (
            physical_evidence.get("contact_monitoring_complete") is True
        ),
        "passed": False,
        "default_maximum_allowed_penetration_m": DEFAULT_PENETRATION_LIMIT_M,
        "expected_contact_buffer_m": EXPECTED_CONTACT_BUFFER_M,
        "expected_contact_cap_m": EXPECTED_CONTACT_CAP_M,
        "minimum_contact_distance_m": None,
        "deepest_contact_pair": None,
        "pair_results": [],
        "evidence_error": None,
    }
    if not result["contact_monitoring_complete"]:
        result["evidence_error"] = "contact monitoring is incomplete"
        return result

    raw_records = physical_evidence.get("contact_pair_min_distances")
    if not isinstance(raw_records, list):
        result["evidence_error"] = "contact pair minimum-distance records are absent"
        return result

    pair_results: list[dict[str, Any]] = []
    invalid_record = False
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            invalid_record = True
            continue
        geom1 = raw_record.get("geom1")
        geom2 = raw_record.get("geom2")
        distance = _finite_number(raw_record.get("minimum_distance_m"))
        if not isinstance(geom1, str) or not geom1 or not isinstance(geom2, str) or not geom2 or distance is None:
            invalid_record = True
            continue
        pair_results.append(
            _evaluate_pair(
                geom1=geom1,
                geom2=geom2,
                minimum_distance_m=distance,
                family=family,
                task_id=task_id,
                geom_metadata=geom_metadata,
                semantic_roles=semantic_roles,
                pair_declared=_canonical_pair((geom1, geom2)) in declared_pairs,
            )
        )

    recorded_global = _finite_number(
        physical_evidence.get("minimum_contact_distance_m")
    )
    pair_minimum = min(
        (item["minimum_distance_m"] for item in pair_results),
        default=None,
    )
    if recorded_global is not None and (
        pair_minimum is None or recorded_global < pair_minimum - 1.0e-12
    ):
        # Preserve a conservative result when an imported legacy record has a
        # global minimum that cannot be resolved to a pair.
        pair_results.append(
            {
                "geom1": None,
                "geom2": None,
                "body1": None,
                "body2": None,
                "minimum_distance_m": recorded_global,
                "penetration_m": max(0.0, -recorded_global),
                "expected_pair_declared": False,
                "role1": None,
                "role2": None,
                "proxy_moving_geom": None,
                "policy_applied": "default_5mm_unresolved_global_minimum",
                "applied_threshold_m": DEFAULT_PENETRATION_LIMIT_M,
                "passed": recorded_global >= -DEFAULT_PENETRATION_LIMIT_M - 1.0e-12,
            }
        )

    result["pair_results"] = pair_results
    if pair_results:
        deepest = min(pair_results, key=lambda item: item["minimum_distance_m"])
        result["minimum_contact_distance_m"] = deepest["minimum_distance_m"]
        result["deepest_contact_pair"] = dict(deepest)
    elif recorded_global is not None:
        # The branch above normally creates the unresolved pair.  Keep this
        # fallback explicit in case the representation changes.
        result["minimum_contact_distance_m"] = recorded_global

    if invalid_record:
        result["evidence_error"] = "one or more contact pair records are invalid"
    result["passed"] = not invalid_record and all(
        item["passed"] for item in pair_results
    )
    return result


def _evaluate_pair(
    *,
    geom1: str,
    geom2: str,
    minimum_distance_m: float,
    family: str,
    task_id: str,
    geom_metadata: Mapping[str, Mapping[str, Any]],
    semantic_roles: Mapping[str, str],
    pair_declared: bool,
) -> dict[str, Any]:
    body1 = _body_name(geom1, geom_metadata)
    body2 = _body_name(geom2, geom_metadata)
    role1 = _semantic_role(geom1, body1, semantic_roles)
    role2 = _semantic_role(geom2, body2, semantic_roles)
    moving_geom = None
    if pair_declared:
        moving_geom = _allowed_proxy_moving_geom(
            family=family,
            task_id=task_id,
            geom1=geom1,
            geom2=geom2,
            role1=role1,
            role2=role2,
        )

    threshold = DEFAULT_PENETRATION_LIMIT_M
    policy_applied = "default_5mm"
    if moving_geom is not None:
        half_size = _moving_half_size(moving_geom, geom_metadata)
        if half_size is not None:
            threshold = min(
                half_size + EXPECTED_CONTACT_BUFFER_M,
                EXPECTED_CONTACT_CAP_M,
            )
            policy_applied = "expected_contact_geometry_proxy"
        else:
            policy_applied = "default_5mm_missing_moving_geometry"

    return {
        "geom1": geom1,
        "geom2": geom2,
        "body1": body1,
        "body2": body2,
        "minimum_distance_m": minimum_distance_m,
        "penetration_m": max(0.0, -minimum_distance_m),
        "expected_pair_declared": pair_declared,
        "role1": role1,
        "role2": role2,
        "proxy_moving_geom": moving_geom,
        "policy_applied": policy_applied,
        "applied_threshold_m": threshold,
        "passed": minimum_distance_m >= -threshold - 1.0e-12,
    }


def _allowed_proxy_moving_geom(
    *,
    family: str,
    task_id: str,
    geom1: str,
    geom2: str,
    role1: str | None,
    role2: str | None,
) -> str | None:
    sides = ((geom1, role1, role2), (geom2, role2, role1))
    if family == "go2":
        allowed_other_roles = {"support"} if task_id.upper() == "GO2-T17" else {
            "support",
            "obstacle",
        }
        for moving_geom, moving_role, other_role in sides:
            if moving_role in {"foot", "calf"} and other_role in allowed_other_roles:
                return moving_geom
        return None

    for moving_geom, moving_role, other_role in sides:
        if moving_role == "workpiece" and other_role in {
            "support",
            "goal",
            "gripper",
        }:
            return moving_geom
        if moving_role == "dial" and other_role == "gripper":
            return moving_geom
    return None


def _normalise_family(value: str) -> str:
    lowered = str(value).strip().lower()
    if "go2" in lowered:
        return "go2"
    if "so101" in lowered or "so-101" in lowered:
        return "so101"
    raise ValueError(f"unsupported corrected-audit robot family {value!r}")


def _canonical_pair(pair: tuple[str, str]) -> tuple[str, str]:
    if (
        not isinstance(pair, tuple)
        or len(pair) != 2
        or not all(isinstance(value, str) and value for value in pair)
    ):
        raise ValueError("expected contact pairs must be two non-empty geom names")
    return tuple(sorted(pair))


def _body_name(
    geom_name: str, geom_metadata: Mapping[str, Mapping[str, Any]]
) -> str | None:
    record = geom_metadata.get(geom_name)
    if not isinstance(record, Mapping):
        return None
    value = record.get("body_name")
    return value if isinstance(value, str) and value else None


def _semantic_role(
    geom_name: str,
    body_name: str | None,
    semantic_roles: Mapping[str, str],
) -> str | None:
    value = semantic_roles.get(geom_name)
    if value is None and body_name is not None:
        value = semantic_roles.get(body_name)
    return value if isinstance(value, str) and value else None


def _moving_half_size(
    geom_name: str, geom_metadata: Mapping[str, Mapping[str, Any]]
) -> float | None:
    record = geom_metadata.get(geom_name)
    if not isinstance(record, Mapping):
        return None
    value = _finite_number(record.get("minimum_positive_half_size_m"))
    if value is None or value <= 0.0:
        return None
    return value


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None
