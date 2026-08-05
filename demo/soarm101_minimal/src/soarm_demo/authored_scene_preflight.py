"""Framework-only static integrity gate for all twelve authored task records.

The task library is larger than the six-task Demo batch.  Before provider
request 1 this module proves that the complete 9-visible + 3-held-out authored
set is a consistent projection of the frozen scene catalog.  It deliberately
does *not* materialize a MuJoCo world: a world is created only when a
Generation probe, Validation B case, or selected Demo task is actually
executed.  Its public evidence contains only counts, booleans, policies, and
cryptographic bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Sequence

from .audit import sha256_json
from .bridge.scene_catalog import (
    ResolvedSceneAsset,
    SceneAssetCatalog,
    SceneAssetCatalogError,
)
from .private_execution_bundle import (
    PrivateExecutionBundle,
    PrivateExecutionBundleError,
)


SCHEMA_VERSION = "robot_capability.private_execution_preflight.v2"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_FACT_PATH = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_GEOMETRY_FACTS: Mapping[str, tuple[str, ...]] = {
    "cube": ("position_m", "size_m"),
    "cylinder": ("position_m", "radius_m", "height_m"),
    "tray": ("center_m", "inner_size_m", "rim_height_m"),
    "bowl": ("center_m", "inner_radius_m", "rim_height_m"),
    "planar_circle": ("center_m", "radius_m"),
    "pose_target": ("position_m",),
}
_ROLE_BUCKET = {
    "dynamic_object": "objects",
    "receptacle": "receptacles",
    "marker": "targets",
}


class AuthoredScenePreflightError(RuntimeError):
    """The frozen 9+3 task records are not consistent with the scene catalog."""


@dataclass(frozen=True)
class _ValidatedAuthoredSet:
    task_hashes: tuple[str, ...]
    instance_hashes: tuple[str, ...]
    pair_hashes: tuple[str, ...]
    descriptor_hashes: tuple[str, ...]
    resolved_asset_references: int


def _sequence(value: object, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AuthoredScenePreflightError(f"{label} must be an array")
    return value


def _task_index(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_visibility: str,
) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for record in records:
        task_id = record.get("task_id")
        split = record.get("split")
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id in output
            or not isinstance(split, Mapping)
            or split.get("visibility") != expected_visibility
        ):
            raise AuthoredScenePreflightError(
                "frozen task catalog has an invalid ID, duplicate, or partition label"
            )
        output[task_id] = record
    return output


def _lookup_fact(root: Mapping[str, Any], path: str) -> Any:
    if _FACT_PATH.fullmatch(path) is None:
        raise AuthoredScenePreflightError(
            "task required_scene_fact_paths contains a malformed path"
        )
    value: Any = root
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise AuthoredScenePreflightError(
                "task required_scene_fact_paths is absent from its authored agent_input"
            )
        value = value[part]
    return value


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _equivalent(left[key], right[key]) for key in left
        )
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes))
    ):
        return len(left) == len(right) and all(
            _equivalent(a, b) for a, b in zip(left, right, strict=True)
        )
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isfinite(float(left)) and math.isfinite(float(right)) and math.isclose(
            float(left), float(right), rel_tol=0.0, abs_tol=1e-12
        )
    return left == right


def _all_numbers_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_all_numbers_finite(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return all(_all_numbers_finite(item) for item in value)
    return False


def _validate_asset_fact_projection(
    *,
    catalog: SceneAssetCatalog,
    instance: Mapping[str, Any],
    agent_input: Mapping[str, Any],
) -> list[ResolvedSceneAsset]:
    expected_ids = {bucket: set() for bucket in _ROLE_BUCKET.values()}
    resolved: list[ResolvedSceneAsset] = []
    seen_ids: set[str] = set()
    for collection_name, expected_role in (("bodies", None), ("markers", "marker")):
        collection = _sequence(
            instance.get(collection_name, []),
            label="authored scene asset collection",
        )
        for raw in collection:
            if not isinstance(raw, Mapping):
                raise AuthoredScenePreflightError(
                    "authored scene asset collection contains a non-object"
                )
            identifier = raw.get("id")
            if (
                not isinstance(identifier, str)
                or _IDENTIFIER.fullmatch(identifier) is None
                or identifier in seen_ids
                or "asset_ref" not in raw
            ):
                raise AuthoredScenePreflightError(
                    "authored scene asset ID/ref is malformed or duplicated"
                )
            seen_ids.add(identifier)
            try:
                asset = catalog.resolve(raw, expected_role=expected_role)
            except SceneAssetCatalogError:
                raise AuthoredScenePreflightError(
                    "authored scene asset does not resolve against the frozen catalog"
                ) from None
            if collection_name == "bodies" and asset.role not in {
                "dynamic_object",
                "receptacle",
            }:
                raise AuthoredScenePreflightError(
                    "authored body resolves to a role unsupported by the tabletop runtime"
                )
            bucket_name = _ROLE_BUCKET.get(asset.role)
            if bucket_name is None:
                raise AuthoredScenePreflightError(
                    "authored scene asset role has no agent_input projection"
                )
            bucket = agent_input.get(bucket_name, {})
            if not isinstance(bucket, Mapping) or identifier not in bucket:
                raise AuthoredScenePreflightError(
                    "authored scene asset is missing from its agent_input projection"
                )
            facts = bucket[identifier]
            if not isinstance(facts, Mapping):
                raise AuthoredScenePreflightError(
                    "authored scene asset agent_input projection must be an object"
                )
            expected_ids[bucket_name].add(identifier)
            for fact_name in _GEOMETRY_FACTS.get(asset.runtime_kind, ()):
                expected_value = asset.parameters.get(fact_name)
                if fact_name not in facts or not _equivalent(
                    expected_value, facts[fact_name]
                ):
                    raise AuthoredScenePreflightError(
                        "agent_input scene geometry disagrees with the frozen asset catalog"
                    )
            resolved.append(asset)

    for bucket_name, identifiers in expected_ids.items():
        raw_bucket = agent_input.get(bucket_name, {})
        if not isinstance(raw_bucket, Mapping) or set(raw_bucket) != identifiers:
            raise AuthoredScenePreflightError(
                "agent_input contains a missing or unbound scene entity"
            )
    return resolved


def _validate_task_instance(
    *,
    task: Mapping[str, Any],
    instance: Mapping[str, Any],
    common_reset: Mapping[str, Any],
    catalog: SceneAssetCatalog,
) -> list[ResolvedSceneAsset]:
    if not all(
        _all_numbers_finite(value) for value in (task, instance, common_reset)
    ):
        raise AuthoredScenePreflightError(
            "authored task, initial state, or common reset contains a non-finite value"
        )
    task_id = task.get("task_id")
    instance_id = instance.get("instance_id")
    if (
        not isinstance(task_id, str)
        or instance.get("task_id") != task_id
        or not isinstance(instance_id, str)
        or not instance_id.startswith(f"{task_id}__")
    ):
        raise AuthoredScenePreflightError("authored task/instance identity binding is invalid")
    if (
        task.get("scene_id") != common_reset.get("scene_id")
        or instance.get("inherits_reset") != "_common_reset.json"
    ):
        raise AuthoredScenePreflightError("authored task/instance reset scene binding is invalid")
    robot_scope = task.get("robot_scope")
    if not isinstance(robot_scope, list) or "soarm101" not in robot_scope:
        raise AuthoredScenePreflightError("authored task robot scope is invalid")
    goal = task.get("goal")
    if not isinstance(goal, Mapping) or goal.get("private_predicate_ref") != instance.get(
        "oracle_ref"
    ):
        raise AuthoredScenePreflightError("authored task/instance oracle binding is invalid")

    contract = task.get("agent_input_contract")
    authored_agent_input = instance.get("agent_input")
    try:
        agent_input = catalog.derive_agent_input(instance)
    except SceneAssetCatalogError:
        raise AuthoredScenePreflightError(
            "authored scene facts cannot be derived from the frozen catalog"
        ) from None
    units = common_reset.get("units")
    if (
        not isinstance(contract, Mapping)
        or not isinstance(authored_agent_input, Mapping)
        or not isinstance(units, Mapping)
        or contract.get("observation_mode") != "structured_state_no_vision"
        or contract.get("coordinate_frame") != common_reset.get("coordinate_frame")
        or agent_input.get("coordinate_frame") != common_reset.get("coordinate_frame")
        or contract.get("position_unit") != units.get("position")
        or agent_input.get("position_unit") != units.get("position")
    ):
        raise AuthoredScenePreflightError(
            "authored task/instance coordinate or observation contract is inconsistent"
        )
    required_paths = _sequence(
        contract.get("required_scene_fact_paths"),
        label="required_scene_fact_paths",
    )
    if not required_paths or len(required_paths) != len(set(required_paths)):
        raise AuthoredScenePreflightError(
            "required_scene_fact_paths must be non-empty and unique"
        )
    for path in required_paths:
        if not isinstance(path, str):
            raise AuthoredScenePreflightError(
                "required_scene_fact_paths must contain strings"
            )
        _lookup_fact(agent_input, path)
    forbidden = _sequence(
        contract.get("forbidden_scene_fact_prefixes"),
        label="forbidden_scene_fact_prefixes",
    )
    for prefix in forbidden:
        if not isinstance(prefix, str) or not prefix:
            raise AuthoredScenePreflightError(
                "forbidden_scene_fact_prefixes contains an invalid prefix"
            )
        if prefix.split(".", 1)[0] in agent_input:
            raise AuthoredScenePreflightError(
                "agent_input contains a forbidden scene-fact prefix"
            )
    return _validate_asset_fact_projection(
        catalog=catalog,
        instance=instance,
        agent_input=agent_input,
    )


def _validate_authored_set(
    bundle: PrivateExecutionBundle,
    catalog: SceneAssetCatalog,
    scene_freeze: Mapping[str, Any],
) -> _ValidatedAuthoredSet:
    bundle.verify()
    catalog.verify_unchanged()
    catalog.verify_environment_freeze(scene_freeze)
    common_reset = bundle.read_json("common_reset")
    try:
        table = common_reset.get("table")
        if not isinstance(table, Mapping):
            raise AuthoredScenePreflightError("common reset has no table asset")
        table_asset = catalog.resolve(table, expected_role="support_surface")
    except SceneAssetCatalogError:
        raise AuthoredScenePreflightError(
            "common reset table does not resolve against the frozen catalog"
        ) from None

    visible = bundle.read_jsonl("visible_tasks")
    heldout = bundle.read_jsonl("heldout_tasks")
    if len(visible) != 9 or len(heldout) != 3:
        raise AuthoredScenePreflightError(
            "frozen task set must contain exactly 9 visible + 3 held-out tasks"
        )
    visible_index = _task_index(visible, expected_visibility="generation_visible")
    heldout_index = _task_index(heldout, expected_visibility="demo_heldout")
    if set(visible_index) & set(heldout_index):
        raise AuthoredScenePreflightError("frozen visible and held-out task sets overlap")
    task_index = {**visible_index, **heldout_index}

    instance_role_by_task: dict[str, str] = {}
    for ordinal in range(1, 13):
        role = f"authored_instance_{ordinal:02d}"
        instance = bundle.read_json(role)
        task_id = instance.get("task_id")
        if (
            not isinstance(task_id, str)
            or task_id not in task_index
            or task_id in instance_role_by_task
        ):
            raise AuthoredScenePreflightError(
                "authored initial states are not a one-to-one task projection"
            )
        instance_role_by_task[task_id] = role
    if set(instance_role_by_task) != set(task_index):
        raise AuthoredScenePreflightError(
            "authored initial states do not cover the complete task set"
        )

    batch = bundle.read_json("demo_batch")
    ordered = _sequence(batch.get("ordered_instances"), label="Demo ordered instances")
    if len(ordered) != 6:
        raise AuthoredScenePreflightError("frozen Demo selection must contain six instances")
    for ordinal, entry in enumerate(ordered, start=1):
        if not isinstance(entry, Mapping):
            raise AuthoredScenePreflightError("frozen Demo selection entry is malformed")
        task_id = entry.get("task_id")
        authored_role = instance_role_by_task.get(str(task_id))
        if (
            authored_role is None
            or bundle.sha256_for_role(f"instance_{ordinal:02d}")
            != bundle.sha256_for_role(authored_role)
        ):
            raise AuthoredScenePreflightError(
                "selected Demo state is not byte-identical to its authored state"
            )

    descriptor_index = catalog.asset_descriptor_sha256
    descriptors = [descriptor_index[table_asset.asset_ref]]
    task_hashes: list[str] = []
    instance_hashes: list[str] = []
    pair_hashes: list[str] = []
    resolved_count = 1
    for task_id in sorted(task_index):
        task = task_index[task_id]
        role = instance_role_by_task[task_id]
        instance = bundle.read_json(role)
        assets = _validate_task_instance(
            task=task,
            instance=instance,
            common_reset=common_reset,
            catalog=catalog,
        )
        task_hash = sha256_json(task)
        instance_hash = bundle.sha256_for_role(role)
        task_hashes.append(task_hash)
        instance_hashes.append(instance_hash)
        pair_hashes.append(
            sha256_json(
                {
                    "task_sha256": task_hash,
                    "instance_sha256": instance_hash,
                }
            )
        )
        resolved_count += len(assets)
        for asset in assets:
            descriptor = descriptor_index.get(asset.asset_ref)
            if not isinstance(descriptor, str):
                raise AuthoredScenePreflightError(
                    "resolved scene asset lacks a frozen descriptor hash"
                )
            descriptors.append(descriptor)
    catalog.verify_unchanged()
    return _ValidatedAuthoredSet(
        task_hashes=tuple(sorted(task_hashes)),
        instance_hashes=tuple(sorted(instance_hashes)),
        pair_hashes=tuple(sorted(pair_hashes)),
        descriptor_hashes=tuple(sorted(descriptors)),
        resolved_asset_references=resolved_count,
    )


def run_authored_scene_preflight(
    *,
    mode: str,
    bundle: PrivateExecutionBundle,
    catalog: SceneAssetCatalog,
    scene_freeze: Mapping[str, Any],
) -> dict[str, Any]:
    """Return redacted static evidence for the complete authored task set."""

    if mode not in {"offline", "aws"}:
        raise AuthoredScenePreflightError("authored scene preflight mode is invalid")
    try:
        validated = _validate_authored_set(bundle, catalog, scene_freeze)
    except (PrivateExecutionBundleError, SceneAssetCatalogError):
        raise AuthoredScenePreflightError(
            "authored scene preflight input freeze verification failed"
        ) from None

    authored_instance_binding = sha256_json(list(validated.instance_hashes))
    task_template_binding = sha256_json(list(validated.task_hashes))
    task_instance_pairing = sha256_json(list(validated.pair_hashes))
    descriptor_binding = sha256_json(list(validated.descriptor_hashes))
    source_binding = sha256_json(
        {
            "private_execution_bundle_manifest_sha256": bundle.manifest_sha256,
            "private_partition_source_sha256": (
                bundle.private_partition_source_sha256
            ),
            "scene_freeze_manifest_sha256": scene_freeze.get("manifest_sha256"),
            "task_template_binding_sha256": task_template_binding,
            "authored_instance_binding_sha256": authored_instance_binding,
            "task_instance_pairing_sha256": task_instance_pairing,
            "asset_descriptor_binding_sha256": descriptor_binding,
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "private_execution_bundle_freeze_sha256": bundle.freeze_sha256,
        "private_execution_bundle_manifest_sha256": bundle.manifest_sha256,
        "source_bindings": {
            "private_partition_source_sha256": (
                bundle.private_partition_source_sha256
            ),
            "visible_task_catalog_sha256": bundle.sha256_for_role("visible_tasks"),
            "pilot_heldout_task_catalog_sha256": bundle.sha256_for_role(
                "heldout_tasks"
            ),
            "common_reset_sha256": bundle.sha256_for_role("common_reset"),
            "task_template_binding_sha256": task_template_binding,
            "authored_instance_binding_sha256": authored_instance_binding,
            "task_instance_pairing_sha256": task_instance_pairing,
            "scene_catalog_sha256": catalog.source_sha256,
            "scene_freeze_manifest_sha256": scene_freeze.get("manifest_sha256"),
            "complete_source_binding_sha256": source_binding,
        },
        "partition_counts": {
            "catalog_visible": 9,
            "catalog_pilot_heldout": 3,
            "authored_instances": 12,
            "selected_visible": 3,
            "selected_pilot_heldout": 3,
            "selected_overlap": 0,
        },
        "authored_scene_checks": {
            "task_partition_disjoint": True,
            "task_instance_bijection": True,
            "selected_instances_match_authored_sources": True,
            "scene_and_reset_binding_consistent": True,
            "required_scene_fact_paths_present": True,
            "catalog_derived_asset_ids_complete": True,
            "catalog_is_only_physical_fact_authority": True,
            "all_numbers_finite": True,
        },
        "asset_resolution": {
            "authored_instances": 12,
            "resolved_asset_references": validated.resolved_asset_references,
            "unique_asset_descriptors": len(set(validated.descriptor_hashes)),
            "descriptor_binding_sha256": descriptor_binding,
            "scene_catalog_sha256": catalog.source_sha256,
        },
        "mujoco_worlds_created": 0,
        "privacy": {
            "contains_task_ids": False,
            "contains_language": False,
            "contains_poses": False,
            "contains_oracle_thresholds": False,
            "contains_source_paths": False,
            "generation_visible": False,
        },
    }


__all__ = [
    "AuthoredScenePreflightError",
    "SCHEMA_VERSION",
    "run_authored_scene_preflight",
]
