"""End-to-end SO-ARM101 minimal Demo orchestration."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audit import atomic_write_json, sha256_file, sha256_json, utc_now
from .authored_scene_preflight import (
    AuthoredScenePreflightError,
    run_authored_scene_preflight,
)
from .bridge.deterministic_tabletop import DeterministicTabletopRuntime
from .bridge.scene_catalog import SceneAssetCatalog, SceneAssetCatalogError
from .demo_runner import FrozenDemoRunner, load_json, public_model_identity
from .direct_validation import (
    InvalidValidationCaseError,
    ValidationHarnessInfrastructureError,
    preflight_validation_suite,
    run_direct_validation,
)
from .evolution import write_candidate_bundle
from .environment_resolver import (
    ResolvedGenerationEnvironment,
    resolve_generation_environment,
)
from .generation import (
    ContinuousGeneration,
    GenerationWorkspace,
    repair_ledger_semantic_issues,
)
from .generation_simulation import GenerationSimulationSandbox, ProbeLimits
from .libraries import ExperienceLibrary, LibraryEntry, load_structured
from .model_client import AWSModelAPIClient, AWSModelAPIConfig, ModelClient, ScriptedModelClient
from .oracle import (
    FixtureDemoEnvironment,
    FixtureValidationEnvironment,
    MujocoTabletopDemoEnvironment,
    MujocoTabletopValidationEnvironment,
    prepare_generated_callable_for_mujoco,
)
from .private_execution_bundle import (
    PrivateExecutionBundle,
    PrivateExecutionBundleError,
    materialize_private_execution_bundle,
)
from .report_audit import (
    demo_report_structure_semantic_errors,
    sealed_report_semantic_errors,
    terminal_report_semantic_errors,
)
from .run_manifest import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    RunManifest,
    RunState,
)
from .schema_validation import validate_json_schema
from .sdk_activation import (
    SDKActivationError,
    evaluate_sdk_activation,
    freeze_sdk_activation,
)
from .static_validation import validate_generated_package
from .tool_packager import package_tree_sha256, package_validated_tools
from .validation_suite import ValidationSuiteGenerator, ValidationSuiteError


@dataclass(frozen=True)
class DemoPaths:
    root: Path

    @property
    def libraries(self) -> dict[str, Path]:
        return {
            "morphology": self.root / "libraries/morphology/soarm101/v1",
            "sdk_runtime": self.root / "libraries/sdk_runtime/lerobot_soarm101/0.6.0",
            "tasks": self.root / "libraries/tasks/soarm101_tabletop/v1",
            "experience": self.root / "libraries/experience/v1",
        }

    @property
    def fixtures(self) -> Path:
        return self.root / "fixtures"

    @property
    def private_tasks(self) -> Path:
        return self.root / "private/task_library/soarm101_tabletop/v1"

    @property
    def morphology_scene(self) -> Path:
        return self.root / "libraries/morphology/scenes/soarm101_tabletop/v1"


class PipelineError(RuntimeError):
    pass


def _safe_trace_name(value: object) -> str:
    """Map an audited identifier to one non-secret filesystem component."""

    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    if not name:
        raise PipelineError("trace identifier has no safe filesystem characters")
    return name[:160]


def _safe_identifier_index(
    values: Sequence[object], *, label: str
) -> dict[str, str]:
    """Bind safe path stems one-to-one to their audited identifiers."""

    index: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or not value:
            raise PipelineError(f"{label} must be a non-empty string")
        stem = _safe_trace_name(value)
        previous = index.get(stem)
        if previous is not None:
            raise PipelineError(
                f"{label} filename collision after safe-name mapping: "
                f"{previous!r} and {value!r} both map to {stem!r}"
            )
        index[stem] = value
    return index


def _json_action(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def offline_generation_responses(paths: DemoPaths) -> list[str]:
    stage1 = load_json(paths.fixtures / "stage1_capabilities.json")
    fixture_root = paths.fixtures / "generated_pass"
    responses = [
        _json_action(
            {"type": "tool", "tool": "read_generation_snapshot", "arguments": {}}
        ),
        _json_action(
            {
                "type": "tool",
                "tool": "review_stage1",
                "arguments": {"artifact": stage1},
            }
        ),
        _json_action(
            {
                "type": "tool",
                "tool": "submit_stage1",
                "arguments": {"artifact": stage1},
            }
        ),
    ]
    responses.append(
        _json_action(
            {
                "type": "tool",
                "tool": "write_package_manifest",
                "arguments": {
                    "manifest": load_json(fixture_root / "package_manifest.json")
                },
            }
        )
    )
    for relative in (
        "generated_capability_package/__init__.py",
        "generated_capability_package/_kinematics.py",
        "generated_capability_package/g1.py",
        "generated_capability_package/g2.py",
        "generated_capability_package/g3.py",
    ):
        responses.append(
            _json_action(
                {
                    "type": "tool",
                    "tool": "write_generated_file",
                    "arguments": {
                        "path": relative,
                        "content": (fixture_root / relative).read_text(encoding="utf-8"),
                    },
                }
            )
        )
    responses.extend(
        [
            _json_action(
                {"type": "tool", "tool": "check_generated_syntax", "arguments": {}}
            ),
            _json_action(
                {
                    "type": "final",
                    "content": "Stage 2 package is complete and syntax-checked.",
                }
            ),
        ]
    )
    return responses


def _offline_object_extent_m(object_facts: Mapping[str, Any]) -> float:
    """Derive the public per-move horizontal extent contract for the fixture."""

    size = object_facts.get("size_m")
    if (
        isinstance(size, Sequence)
        and not isinstance(size, (str, bytes))
        and len(size) >= 2
    ):
        extent = max(float(size[0]), float(size[1]))
    elif isinstance(object_facts.get("radius_m"), (int, float)) and not isinstance(
        object_facts.get("radius_m"), bool
    ):
        extent = 2.0 * float(object_facts["radius_m"])
    else:
        raise PipelineError("offline fixture object has no horizontal extent facts")
    if not math.isfinite(extent) or extent <= 0.0:
        raise PipelineError("offline fixture object extent must be positive and finite")
    return extent


def offline_demo_responses(paths: DemoPaths) -> list[str]:
    batch = load_json(paths.private_tasks / "demo_batch.json")
    responses: list[str] = []
    for entry in batch["ordered_instances"]:
        instance_path = paths.private_tasks / "initial_states" / Path(entry["instance_ref"]).name
        instance = load_json(instance_path)
        facts = instance["agent_input"]
        task_id = instance["task_id"]
        if task_id in {"soarm101_p0_push_cube_to_region", "soarm101_p0_push_cylinder_lateral"}:
            object_value = next(iter(facts["objects"].values()))
            target = facts["targets"]["push_region"]["center_m"]
            tool_name = "push_object"
            arguments = {
                "object_position_m": object_value["position_m"],
                "target_position_m": target,
            }
        elif task_id in {"soarm101_p0_place_cube_in_tray", "soarm101_p0_place_cube_in_bowl_new_region"}:
            object_value = next(iter(facts["objects"].values()))
            receptacle = next(iter(facts["receptacles"].values()))
            tool_name = "pick_and_place"
            arguments = {
                "object_position_m": object_value["position_m"],
                "target_position_m": receptacle["center_m"],
            }
        elif task_id == "soarm101_p0_place_two_objects_in_tray":
            center = facts["receptacles"]["tray"]["center_m"]
            ordered = facts["goals"]["ordered_object_ids"]
            offsets = (-0.025, 0.025)
            tool_name = "place_objects"
            arguments = {
                "moves": [
                    {
                        "object_position_m": facts["objects"][object_id]["position_m"],
                        "target_position_m": [center[0], center[1] + offsets[index], center[2]],
                        "object_extent_m": _offline_object_extent_m(
                            facts["objects"][object_id]
                        ),
                    }
                    for index, object_id in enumerate(ordered)
                ]
            }
        elif task_id == "soarm101_p0_sort_two_cubes_matching_trays":
            expected = facts["goals"]["object_to_receptacle"]
            tool_name = "place_objects"
            arguments = {
                "moves": [
                    {
                        "object_position_m": facts["objects"][object_id]["position_m"],
                        "target_position_m": facts["receptacles"][receptacle_id]["center_m"],
                        "object_extent_m": _offline_object_extent_m(
                            facts["objects"][object_id]
                        ),
                    }
                    for object_id, receptacle_id in expected.items()
                ]
            }
        else:
            raise PipelineError(f"offline fixture has no Demo script for {task_id}")
        responses.append(
            _json_action(
                {
                    "type": "tool",
                    "tool": tool_name,
                    "arguments": arguments,
                }
            )
        )
        responses.append(
            _json_action(
                {
                    "type": "final",
                    "content": f"Executed validated capability for {task_id}.",
                }
            )
        )
    return responses


def _prompts(paths: DemoPaths, names: Sequence[str]) -> list[str]:
    return [
        (paths.root / "prompts" / name).read_text(encoding="utf-8")
        for name in names
    ]


def materialize_inputs(paths: DemoPaths, run: RunManifest) -> tuple[Path, dict[str, Any]]:
    destination = run.root / "input_snapshot"
    destination.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    entries: dict[str, LibraryEntry] = {}

    # Validate all four libraries before copying any generation-visible bytes.
    # This avoids both a partially materialized view and a hash-valid but
    # structurally invalid input reaching the Generation Agent.
    for name, source in paths.libraries.items():
        entry = LibraryEntry.open(source)
        schema_validation = entry.validate_runtime_schemas(paths.root / "schemas")
        if not schema_validation["ok"]:
            raise PipelineError(
                f"{name} library schema validation failed: {schema_validation['errors']}"
            )
        verification = entry.verify()
        if not verification["ok"]:
            raise PipelineError(f"{name} library verification failed: {verification['errors']}")
        entries[name] = entry
        reports[name] = {
            "schema_validation": schema_validation,
            "verification": verification,
        }
        run.input_hashes[f"library_manifest:{name}"] = sha256_file(source / "manifest.yaml")

    for name, entry in entries.items():
        snapshot = entry.materialize_generation_view(destination / name)
        reports[name]["snapshot"] = snapshot
    experience = ExperienceLibrary(paths.libraries["experience"] / "records.jsonl")
    if experience.list() != [] or experience.select([]) != [] or experience.generation_view([]) != []:
        raise PipelineError("P0 Experience Library must be operational and empty")
    # File-level isolation: only allowlisted materialized payloads are present.
    forbidden_names = {"heldout_tasks.jsonl", "task_oracles.yaml", "demo_batch.json", "similarity_matrix.json"}
    leaked = [path.name for path in destination.rglob("*") if path.name in forbidden_names]
    if leaked:
        raise PipelineError(f"private task artifacts leaked into generation snapshot: {leaked}")
    # The report includes framework-only schema and manifest evidence.  Keep it
    # beside, never inside, the Agent-readable snapshot.
    atomic_write_json(run.root / "input_library_report.json", reports)
    return destination, reports


def _prepare_generation_environment(
    *,
    paths: DemoPaths,
    run: RunManifest,
    run_config: Mapping[str, Any],
    mode: str,
    input_snapshot: Path,
) -> tuple[ResolvedGenerationEnvironment, GenerationSimulationSandbox]:
    """Resolve public assets without creating a world before provider request 1."""

    freeze_path = run.root / "generation/environment_freeze.json"
    resolved = resolve_generation_environment(
        paths.libraries,
        morphology_scene=paths.morphology_scene,
        libraries_root=paths.root / "libraries",
        output_path=freeze_path,
        input_snapshot_root=input_snapshot,
        schemas_root=paths.root / "schemas",
    )
    run.input_hashes["generation_environment_freeze"] = resolved.freeze_sha256
    run.input_hashes["generation_environment_manifest"] = resolved.manifest_sha256
    bindings = resolved.manifest.get("bindings")
    if not isinstance(bindings, Mapping):
        raise PipelineError("resolved Generation environment has no source bindings")
    for library_name, group in sorted(bindings.items()):
        if not isinstance(group, Mapping):
            raise PipelineError("resolved Generation environment binding group is malformed")
        for binding_name, binding in sorted(group.items()):
            digest = binding.get("sha256") if isinstance(binding, Mapping) else None
            if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise PipelineError("resolved Generation environment source hash is malformed")
            run.input_hashes[
                f"generation_environment_source:{library_name}:{binding_name}"
            ] = digest

    public_limits = run_config["generation"]["public_simulation"]
    sandbox = GenerationSimulationSandbox.prepare(
        mode=mode,
        environment_freeze_path=resolved.freeze_path,
        expected_environment_freeze_sha256=resolved.freeze_sha256,
        libraries_root=paths.root / "libraries",
        run_root=run.root,
        limits=ProbeLimits(
            max_calls=int(public_limits["max_probe_calls"]),
            worker_startup_timeout_s=float(
                public_limits["worker_startup_timeout_s"]
            ),
            wall_timeout_s=float(public_limits["wall_timeout_s"]),
            max_total_simulation_s=float(
                public_limits["max_total_simulation_s"]
            ),
            max_simulation_s_per_call=float(
                public_limits["max_simulation_s_per_call"]
            ),
            max_runtime_calls_per_probe=int(
                public_limits["max_runtime_calls_per_probe"]
            ),
            max_worker_memory_mb=int(public_limits["max_worker_memory_mb"]),
        ),
    )
    inspection_path = run.root / "generation/simulation/catalog_inspection.json"
    run.artifact_hashes["generation_simulation_inspection"] = sha256_file(
        inspection_path
    )
    return resolved, sandbox


def _frozen_scene_execution_context(
    simulation_sandbox: GenerationSimulationSandbox,
) -> dict[str, Any]:
    """Re-establish the exact resolver-selected scene for trusted consumers.

    Generation, Validation B, and Demo use different agents and private
    contexts, but they must not silently select different physical assets.  A
    fresh Oracle environment is still constructed for every case/task; this
    context only binds all of those isolated environments to the same robot
    model, catalog, and resolver freeze.
    """

    preflight = simulation_sandbox.framework_preflight_inputs()
    catalog_path = Path(preflight["scene_catalog"]).resolve()
    if sha256_file(catalog_path) != preflight["scene_catalog_sha256"]:
        raise PipelineError("resolver-frozen scene catalog drifted")
    catalog_document = load_structured(catalog_path)
    if not isinstance(catalog_document, Mapping):
        raise PipelineError("resolver-frozen scene catalog must contain an object")
    catalog = SceneAssetCatalog(catalog_path)
    catalog.verify_environment_freeze(preflight["scene_freeze"])
    return {
        **preflight,
        "scene_catalog_path": catalog_path,
        "scene_catalog_document": copy.deepcopy(dict(catalog_document)),
        "catalog": catalog,
    }


def _catalog_body_extent_resolver(
    catalog: SceneAssetCatalog,
):
    """Resolve trusted G3 horizontal extents without expanding frozen cases."""

    def resolve(body: Mapping[str, Any], semantics: object) -> float | None:
        try:
            asset = catalog.resolve(
                body,
                expected_role="dynamic_object",
                legacy_kind=None,
            )
        except SceneAssetCatalogError:
            return None
        parameters = asset.parameters
        if asset.runtime_kind == "cube":
            raw_size = parameters.get("size_m")
            if isinstance(raw_size, (int, float)) and not isinstance(raw_size, bool):
                size = [float(raw_size)] * 3
            elif (
                isinstance(raw_size, Sequence)
                and not isinstance(raw_size, (str, bytes))
                and len(raw_size) == 3
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and float(value) > 0.0
                    for value in raw_size
                )
            ):
                size = [float(value) for value in raw_size]
            else:
                return None
            if semantics == "cube_edge_m":
                return size[0] if max(size) - min(size) <= 1e-9 else None
            if semantics == "maximum_horizontal_extent_m":
                return max(size[0], size[1])
            return None
        if asset.runtime_kind == "cylinder":
            radius = parameters.get("radius_m")
            if (
                semantics
                in {"cylinder_diameter_m", "maximum_horizontal_extent_m"}
                and isinstance(radius, (int, float))
                and not isinstance(radius, bool)
                and math.isfinite(float(radius))
                and float(radius) > 0.0
            ):
                return 2.0 * float(radius)
        return None

    return resolve


def _private_execution_preflight(
    *,
    paths: DemoPaths,
    run: RunManifest,
    mode: str,
    bundle: PrivateExecutionBundle,
    scene_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Statically gate all 12 authored task records before provider request 1.

    The written evidence contains only counts, booleans, and SHA-256 bindings.
    Held-out IDs, language, coordinates, oracle thresholds, and source paths
    remain inside the hash-locked private bundle.  This gate never creates a
    MuJoCo world in either mode.
    """

    catalog = scene_execution.get("catalog")
    scene_freeze = scene_execution.get("scene_freeze")
    if not isinstance(catalog, SceneAssetCatalog) or not isinstance(
        scene_freeze, Mapping
    ):
        raise PipelineError("private execution preflight lacks the frozen scene context")
    try:
        evidence = run_authored_scene_preflight(
            mode=mode,
            bundle=bundle,
            catalog=catalog,
            scene_freeze=scene_freeze,
        )
    except AuthoredScenePreflightError:
        raise PipelineError("all-12 authored task integrity preflight failed") from None

    reference_path = paths.fixtures / "validation_reference/tabletop_primitives.yaml"
    reference = load_structured(reference_path)
    scene_contract = (
        reference.get("scene_asset_contract")
        if isinstance(reference, Mapping)
        else None
    )
    if not isinstance(scene_contract, Mapping) or scene_contract.get("source") != (
        "frozen_morphology_scene_catalog"
    ):
        raise PipelineError("Validation reference is not bound to the frozen scene catalog")
    evidence["validation_reference"] = {
        "sha256": sha256_file(reference_path),
        "frozen_catalog_contract_verified": True,
    }
    schema = load_json(paths.root / "schemas/private_execution_preflight.schema.json")
    schema_issues = validate_json_schema(
        evidence,
        schema,
        instance_path="$private_execution_preflight",
    )
    if schema_issues:
        raise PipelineError(
            "private execution preflight evidence failed schema validation: "
            + "; ".join(
                f"{issue.path}: {issue.message}" for issue in schema_issues
            )
        )
    evidence_path = run.root / "framework/private_execution_preflight.json"
    atomic_write_json(evidence_path, evidence)
    return evidence


def _public_api_hash(package_manifest: Mapping[str, Any]) -> str:
    return sha256_json(
        [
            {
                "capability_id": item["capability_id"],
                "granularity": item["granularity"],
                "module": item["module"],
                "function_name": item["function_name"],
                "signature": item["signature"],
                "result_contract": item["result_contract"],
            }
            for item in package_manifest.get("capabilities", [])
        ]
    )


def _file_evidence(base: Path, path: Path) -> dict[str, Any]:
    """Describe one file by a stable relative path and bytes hash."""

    logical_base = base.absolute()
    logical_path = path.absolute()
    try:
        relative = logical_path.relative_to(logical_base)
    except ValueError as exc:
        raise PipelineError(f"evidence path escaped its declared root: {path}") from exc
    resolved_base = base.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise PipelineError(
            f"evidence symlink escaped its declared root: {path}"
        ) from exc
    if not resolved_path.is_file():
        raise PipelineError(f"evidence file is missing: {path}")
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(resolved_path),
        "bytes": resolved_path.stat().st_size,
    }


def _evidence_for_paths(base: Path, paths: Sequence[Path]) -> list[dict[str, Any]]:
    unique = {path.resolve(): path for path in paths}
    return [_file_evidence(base, unique[path]) for path in sorted(unique, key=str)]


_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_DIRECT_REPORT_NAME = re.compile(r"^direct_round_(\d{2})\.json$")
_VIDEO_ROUND_NAME = re.compile(r"^round_(\d{2})$")
_SCENE_CATALOG_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SCENE_CATALOG_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _video_scene_binding_fields(
    run: RunManifest,
    metadata: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    """Validate and project one recorder-owned resolved-scene binding.

    Legacy synthetic video fixtures predate the resolver boundary and may omit
    this field.  A real run is unambiguously identified by the three resolver
    hashes recorded in ``run.input_hashes``; for such a run the sidecar must
    select the exact catalog bound by the immutable environment freeze.
    """

    input_hashes = getattr(run, "input_hashes", {})
    if not isinstance(input_hashes, Mapping):
        input_hashes = {}
    freeze_key = "generation_environment_freeze"
    manifest_key = "generation_environment_manifest"
    catalog_key = "generation_environment_source:morphology:scene_asset_catalog"
    formal_values = {
        freeze_key: input_hashes.get(freeze_key),
        manifest_key: input_hashes.get(manifest_key),
        catalog_key: input_hashes.get(catalog_key),
    }
    formal_present = [name for name, value in formal_values.items() if value is not None]
    if formal_present and len(formal_present) != len(formal_values):
        raise PipelineError(
            "MuJoCo video resolver binding is incomplete in run.input_hashes"
        )
    formal = bool(formal_present)
    for name, value in formal_values.items():
        if formal and (
            not isinstance(value, str)
            or _SHA256_PATTERN.fullmatch(value) is None
        ):
            raise PipelineError(f"MuJoCo video resolver hash {name!r} is invalid")

    raw_binding = metadata.get("resolved_scene_binding")
    if raw_binding is None:
        if formal:
            raise PipelineError(
                f"MuJoCo {phase} video metadata lacks its resolved scene binding"
            )
        return {}
    if not isinstance(raw_binding, Mapping) or set(raw_binding) != {
        "binding_sha256",
        "source_scene_request_sha256",
        "scene_catalog",
    }:
        raise PipelineError(
            f"MuJoCo {phase} video resolved scene binding is malformed"
        )
    for name in ("binding_sha256", "source_scene_request_sha256"):
        value = raw_binding.get(name)
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            raise PipelineError(
                f"MuJoCo {phase} video resolved scene {name} is invalid"
            )
    raw_catalog = raw_binding.get("scene_catalog")
    if not isinstance(raw_catalog, Mapping) or set(raw_catalog) != {
        "catalog_id",
        "version",
        "source_sha256",
        "content_sha256",
    }:
        raise PipelineError(
            f"MuJoCo {phase} video resolved scene catalog binding is malformed"
        )
    catalog_id = raw_catalog.get("catalog_id")
    version = raw_catalog.get("version")
    if (
        not isinstance(catalog_id, str)
        or _SCENE_CATALOG_ID_PATTERN.fullmatch(catalog_id) is None
        or not isinstance(version, str)
        or _SCENE_CATALOG_VERSION_PATTERN.fullmatch(version) is None
    ):
        raise PipelineError(
            f"MuJoCo {phase} video resolved scene catalog identity is invalid"
        )
    for name in ("source_sha256", "content_sha256"):
        value = raw_catalog.get(name)
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            raise PipelineError(
                f"MuJoCo {phase} video resolved scene catalog {name} is invalid"
            )

    result: dict[str, Any] = {
        "resolved_scene_binding": copy.deepcopy(dict(raw_binding))
    }
    if not formal:
        return result

    freeze_path = run.root / "generation/environment_freeze.json"
    if not freeze_path.is_file() or sha256_file(freeze_path) != formal_values[freeze_key]:
        raise PipelineError(
            f"MuJoCo {phase} video is not bound to the exact environment freeze file"
        )
    try:
        envelope = load_json(freeze_path)
    except (OSError, ValueError, TypeError) as exc:
        raise PipelineError("MuJoCo video environment freeze is unreadable") from exc
    manifest = envelope.get("manifest") if isinstance(envelope, Mapping) else None
    detached_manifest_sha256 = (
        envelope.get("manifest_sha256") if isinstance(envelope, Mapping) else None
    )
    if (
        not isinstance(manifest, Mapping)
        or detached_manifest_sha256 != formal_values[manifest_key]
        or sha256_json(manifest) != formal_values[manifest_key]
    ):
        raise PipelineError(
            f"MuJoCo {phase} video environment manifest binding is invalid"
        )
    bindings = manifest.get("bindings")
    morphology = bindings.get("morphology") if isinstance(bindings, Mapping) else None
    frozen_catalog = (
        morphology.get("scene_asset_catalog")
        if isinstance(morphology, Mapping)
        else None
    )
    if not isinstance(frozen_catalog, Mapping):
        raise PipelineError("MuJoCo video environment freeze lacks its scene catalog")
    if (
        frozen_catalog.get("sha256") != formal_values[catalog_key]
        or raw_catalog.get("source_sha256") != formal_values[catalog_key]
        or raw_catalog.get("catalog_id") != frozen_catalog.get("catalog_id")
        or raw_catalog.get("version") != frozen_catalog.get("version")
    ):
        raise PipelineError(
            f"MuJoCo {phase} video scene catalog does not match the environment freeze"
        )

    result.update(
        {
            "generation_environment_freeze_sha256": formal_values[freeze_key],
            "generation_environment_manifest_sha256": formal_values[manifest_key],
        }
    )
    return result


def _video_record(run: RunManifest, path: Path, *, phase: str) -> dict[str, Any]:
    """Fully decode one actual MuJoCo MP4 and bind its trusted sidecar."""

    if phase not in {"validation", "demo"}:
        raise PipelineError(f"unsupported MuJoCo video phase: {phase}")
    metadata_path = path.with_suffix(".metadata.json")
    if not path.is_file() or path.stat().st_size <= 0:
        raise PipelineError(f"MuJoCo {phase} video is missing or empty: {path.name}")
    if not metadata_path.is_file():
        raise PipelineError(f"MuJoCo {phase} video metadata is missing: {path.name}")
    try:
        metadata = load_json(metadata_path)
    except (OSError, ValueError, TypeError) as exc:
        raise PipelineError(f"MuJoCo {phase} video metadata is unreadable") from exc
    if not isinstance(metadata, Mapping):
        raise PipelineError(f"MuJoCo {phase} video metadata must be an object")
    if metadata.get("schema_version") != "robot_capability.mujoco_video_evidence.v1":
        raise PipelineError(f"MuJoCo {phase} video metadata has an invalid schema")
    if metadata.get("video_path") != path.name:
        raise PipelineError(f"MuJoCo {phase} video metadata path is not relative/bound")
    if metadata.get("renderer_kind") != "mujoco.Renderer":
        raise PipelineError(f"MuJoCo {phase} video did not use mujoco.Renderer")
    if metadata.get("codec") != "mp4v":
        raise PipelineError(f"MuJoCo {phase} video did not use the frozen mp4v codec")
    frames = metadata.get("frames")
    if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
        raise PipelineError(f"MuJoCo {phase} video contains no recorded frames")
    width = metadata.get("width")
    height = metadata.get("height")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width < 160
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height < 120
    ):
        raise PipelineError(f"MuJoCo {phase} video dimensions are invalid")
    for name in ("fps", "simulation_start_s", "simulation_end_s"):
        value = metadata.get(name)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise PipelineError(f"MuJoCo {phase} video metadata {name} is invalid")
    fps = float(metadata["fps"])
    simulation_start_s = float(metadata["simulation_start_s"])
    simulation_end_s = float(metadata["simulation_end_s"])
    if fps <= 0:
        raise PipelineError(f"MuJoCo {phase} video fps must be positive")
    if simulation_end_s < simulation_start_s:
        raise PipelineError(f"MuJoCo {phase} video time interval is invalid")
    scene_binding_fields = _video_scene_binding_fields(run, metadata, phase=phase)
    simulation_duration_s = simulation_end_s - simulation_start_s
    # Recorder semantics allow one forced start frame and one forced final
    # frame in addition to regularly sampled simulation-time frames.
    maximum_reasonable_frames = math.floor(simulation_duration_s * fps + 1e-6) + 2
    if frames > maximum_reasonable_frames:
        raise PipelineError(
            f"MuJoCo {phase} video frame count is impossible for its simulation interval"
        )
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - pinned runtime dependency
        raise PipelineError("OpenCV is required to verify MuJoCo MP4 evidence") from exc
    capture = cv2.VideoCapture(str(path))
    decoded_frames = 0
    decoder_fps = float("nan")
    reported_frames = float("nan")
    try:
        if not capture.isOpened():
            raise PipelineError(f"MuJoCo {phase} MP4 cannot be opened by the decoder")
        decoder_fps = float(capture.get(cv2.CAP_PROP_FPS))
        reported_frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        while True:
            decoded, frame = capture.read()
            if not decoded:
                break
            if frame is None or len(frame.shape) < 2:
                raise PipelineError(f"MuJoCo {phase} MP4 contains an invalid frame")
            if int(frame.shape[1]) != width or int(frame.shape[0]) != height:
                raise PipelineError(
                    f"MuJoCo {phase} video dimensions disagree with metadata"
                )
            decoded_frames += 1
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"MuJoCo {phase} MP4 decoding failed") from exc
    finally:
        capture.release()
    if decoded_frames != frames:
        raise PipelineError(
            f"MuJoCo {phase} decoded frame count disagrees with metadata"
        )
    if (
        not math.isfinite(decoder_fps)
        or decoder_fps <= 0
        or not math.isclose(decoder_fps, fps, rel_tol=0.01, abs_tol=0.01)
    ):
        raise PipelineError(f"MuJoCo {phase} decoder fps disagrees with metadata")
    if (
        math.isfinite(reported_frames)
        and reported_frames > 0
        and not math.isclose(reported_frames, decoded_frames, rel_tol=0.0, abs_tol=0.5)
    ):
        raise PipelineError(f"MuJoCo {phase} container frame count is inconsistent")
    encoded_duration_s = decoded_frames / decoder_fps
    if not math.isfinite(encoded_duration_s) or encoded_duration_s <= 0:
        raise PipelineError(f"MuJoCo {phase} encoded duration is invalid")
    return {
        "phase": phase,
        "id": path.stem,
        "video": _file_evidence(run.root, path),
        "metadata": _file_evidence(run.root, metadata_path),
        "renderer_kind": metadata["renderer_kind"],
        "codec": metadata["codec"],
        "fps": fps,
        "frames": frames,
        "width": width,
        "height": height,
        "simulation_start_s": simulation_start_s,
        "simulation_end_s": simulation_end_s,
        "encoded_duration_s": encoded_duration_s,
        **scene_binding_fields,
    }


def _round_from_report_path(path: Path) -> int:
    match = _DIRECT_REPORT_NAME.fullmatch(path.name)
    if match is None:
        raise PipelineError(f"invalid direct validation report filename: {path.name}")
    return int(match.group(1))


def _round_from_video_path(path: Path) -> int:
    match = _VIDEO_ROUND_NAME.fullmatch(path.parent.name)
    if match is None:
        raise PipelineError(f"invalid validation video round directory: {path.parent.name}")
    return int(match.group(1))


def _validation_report_bindings(
    run: RunManifest, *, strict: bool
) -> dict[int, dict[str, Any]]:
    """Load exact per-round case bindings from durable direct reports."""

    bindings: dict[int, dict[str, Any]] = {}
    formal_scene_binding = bool(
        getattr(run, "input_hashes", {}).get("generation_environment_freeze")
    )
    for report_path in sorted((run.root / "validation").glob("direct_round_*.json")):
        try:
            round_index = _round_from_report_path(report_path)
            if round_index in bindings:
                raise PipelineError(
                    f"multiple direct validation reports map to round {round_index:02d}"
                )
            report = load_json(report_path)
            if not isinstance(report, Mapping):
                raise PipelineError("direct validation report must be an object")
            cases = report.get("cases")
            if not isinstance(cases, list) or not cases:
                raise PipelineError("direct validation report must contain executed cases")
            case_ids = [
                case.get("case_id") if isinstance(case, Mapping) else None
                for case in cases
            ]
            case_by_stem = _safe_identifier_index(
                case_ids, label=f"direct round {round_index:02d} case_id"
            )
            scene_binding_by_case: dict[str, dict[str, Any]] = {}
            for case in cases:
                assert isinstance(case, Mapping)
                case_id = case.get("case_id")
                diagnostics = case.get("framework_diagnostics")
                resolved_scene = (
                    diagnostics.get("resolved_scene_binding")
                    if isinstance(diagnostics, Mapping)
                    else None
                )
                if not isinstance(resolved_scene, Mapping):
                    if formal_scene_binding:
                        raise PipelineError(
                            f"direct round {round_index:02d} case {case_id!r} lacks "
                            "resolved scene diagnostics"
                        )
                    continue
                normalized = _video_scene_binding_fields(
                    run,
                    {"resolved_scene_binding": resolved_scene},
                    phase="validation",
                )["resolved_scene_binding"]
                scene_binding_by_case[str(case_id)] = normalized
            summary = report.get("summary")
            if (
                not isinstance(summary, Mapping)
                or not isinstance(summary.get("total"), int)
                or isinstance(summary.get("total"), bool)
                or int(summary["total"]) != len(cases)
            ):
                raise PipelineError(
                    f"direct round {round_index:02d} summary total does not match cases"
                )
            suite_sha256 = report.get("suite_sha256")
            package_sha256 = report.get("package_sha256")
            if not isinstance(suite_sha256, str) or not _SHA256_PATTERN.fullmatch(
                suite_sha256
            ):
                raise PipelineError(
                    f"direct round {round_index:02d} has an invalid suite hash"
                )
            if not isinstance(package_sha256, str) or not _SHA256_PATTERN.fullmatch(
                package_sha256
            ):
                raise PipelineError(
                    f"direct round {round_index:02d} has an invalid package hash"
                )
            frozen_suite_sha256 = run.artifact_hashes.get("validation_suite")
            if (
                frozen_suite_sha256 is not None
                and suite_sha256 != frozen_suite_sha256
            ):
                raise PipelineError(
                    f"direct round {round_index:02d} is not bound to the frozen suite"
                )
            bindings[round_index] = {
                "case_by_stem": case_by_stem,
                "suite_sha256": suite_sha256,
                "package_sha256": package_sha256,
                "direct_report": _file_evidence(run.root, report_path),
                "scene_binding_by_case": scene_binding_by_case,
            }
        except Exception as exc:
            if strict or formal_scene_binding:
                if isinstance(exc, PipelineError):
                    raise
                raise PipelineError(
                    f"could not bind validation report {report_path.name}"
                ) from exc
            continue
    return bindings


def _validation_video_records(
    run: RunManifest, paths: Sequence[Path], *, strict: bool
) -> list[dict[str, Any]]:
    bindings = _validation_report_bindings(run, strict=strict)
    expected = {
        round_index: set(binding["case_by_stem"])
        for round_index, binding in bindings.items()
    }
    actual: dict[int, set[str]] = {}
    parsed_paths: list[tuple[Path, int]] = []
    for path in paths:
        try:
            round_index = _round_from_video_path(path)
        except PipelineError:
            if strict:
                raise
            continue
        actual.setdefault(round_index, set()).add(path.stem)
        parsed_paths.append((path, round_index))
    if strict:
        if not expected:
            raise PipelineError("MuJoCo validation video has no direct report binding")
        if set(actual) != set(expected):
            raise PipelineError(
                "MuJoCo validation video rounds do not exactly match direct reports"
            )
        for round_index, expected_stems in expected.items():
            if actual[round_index] != expected_stems:
                raise PipelineError(
                    f"MuJoCo validation videos for round {round_index:02d} have "
                    "missing or orphan case IDs"
                )
    records: list[dict[str, Any]] = []
    for path, round_index in parsed_paths:
        binding = bindings.get(round_index)
        case_id = (
            None
            if binding is None
            else binding["case_by_stem"].get(path.stem)
        )
        if binding is None or case_id is None:
            if strict:
                raise PipelineError(
                    f"MuJoCo validation video is orphaned from round {round_index:02d}"
                )
            continue
        try:
            record = _video_record(run, path, phase="validation")
        except PipelineError:
            if strict:
                raise
            continue
        report_scene_binding = binding["scene_binding_by_case"].get(str(case_id))
        video_scene_binding = record.get("resolved_scene_binding")
        if report_scene_binding is not None or video_scene_binding is not None:
            if report_scene_binding != video_scene_binding:
                raise PipelineError(
                    f"MuJoCo validation video {case_id!r} resolved scene does not "
                    "match its direct report"
                )
        records.append(
            {
                **record,
                "case_id": case_id,
                "round": round_index,
                "suite_sha256": binding["suite_sha256"],
                "package_sha256": binding["package_sha256"],
                "direct_report": binding["direct_report"],
            }
        )
    return records


def _demo_task_bindings(
    run: RunManifest, *, strict: bool
) -> tuple[dict[str, str], dict[str, Any], dict[str, dict[str, Any]]]:
    formal_scene_binding = bool(
        getattr(run, "input_hashes", {}).get("generation_environment_freeze")
    )
    freeze_path = run.root / "demo/freeze.json"
    if not freeze_path.is_file():
        if strict:
            raise PipelineError("MuJoCo Demo video has no frozen task binding")
        return {}, {}, {}
    try:
        freeze = load_json(freeze_path)
        tasks = freeze.get("tasks") if isinstance(freeze, Mapping) else None
        if not isinstance(tasks, list) or not tasks:
            raise PipelineError("Demo freeze must contain tasks")
        task_ids = [
            task.get("task_id") if isinstance(task, Mapping) else None for task in tasks
        ]
        task_by_stem = _safe_identifier_index(task_ids, label="Demo task_id")
        if strict and len(task_by_stem) != 6:
            raise PipelineError("MuJoCo Demo freeze must bind exactly six task IDs")
        report_path = run.root / "demo/demo_report.json"
        if not report_path.is_file():
            if strict:
                raise PipelineError("MuJoCo Demo video has no final Demo report")
            return {}, {}, {}
        report = load_json(report_path)
        report_tasks = report.get("tasks") if isinstance(report, Mapping) else None
        if not isinstance(report_tasks, list):
            raise PipelineError("Demo report tasks are invalid")
        report_ids = [
            task.get("task_id") if isinstance(task, Mapping) else None
            for task in report_tasks
        ]
        if report_ids != task_ids:
            raise PipelineError("Demo report task IDs do not match the freeze")
        scene_binding_by_task: dict[str, dict[str, Any]] = {}
        for task in report_tasks:
            assert isinstance(task, Mapping)
            task_id = task.get("task_id")
            oracle = task.get("oracle")
            resolved_scene = (
                oracle.get("resolved_scene_binding")
                if isinstance(oracle, Mapping)
                else None
            )
            if not isinstance(resolved_scene, Mapping):
                if formal_scene_binding:
                    raise PipelineError(
                        f"Demo task {task_id!r} lacks resolved scene oracle evidence"
                    )
                continue
            normalized = _video_scene_binding_fields(
                run,
                {"resolved_scene_binding": resolved_scene},
                phase="demo",
            )["resolved_scene_binding"]
            scene_binding_by_task[str(task_id)] = normalized

        freeze_evidence = _file_evidence(run.root, freeze_path)
        report_evidence = _file_evidence(run.root, report_path)
        freeze_package = freeze.get("package_sha256")
        report_package = report.get("package_sha256")
        if (
            not isinstance(freeze_package, str)
            or _SHA256_PATTERN.fullmatch(freeze_package) is None
            or report_package != freeze_package
        ):
            raise PipelineError("Demo freeze/report package hashes do not match")
        if report.get("demo_freeze_sha256") != freeze_evidence["sha256"]:
            raise PipelineError("Demo report is not bound to the exact freeze file")
        generated_package = run.artifact_hashes.get("generated_package")
        if generated_package != freeze_package:
            raise PipelineError(
                "Demo freeze/report package hash does not match the validated package"
            )
        return (
            task_by_stem,
            {
                "package_sha256": freeze_package,
                "demo_freeze": freeze_evidence,
                "demo_report": report_evidence,
            },
            scene_binding_by_task,
        )
    except Exception as exc:
        if strict or formal_scene_binding:
            if isinstance(exc, PipelineError):
                raise
            raise PipelineError("could not bind MuJoCo Demo videos to frozen tasks") from exc
        return {}, {}, {}


def _demo_video_records(
    run: RunManifest, paths: Sequence[Path], *, strict: bool
) -> list[dict[str, Any]]:
    task_by_stem, shared_binding, scene_binding_by_task = _demo_task_bindings(
        run, strict=strict
    )
    actual_stems = {path.stem for path in paths}
    if strict and actual_stems != set(task_by_stem):
        raise PipelineError("MuJoCo Demo videos have missing or orphan task IDs")
    records: list[dict[str, Any]] = []
    for path in paths:
        task_id = task_by_stem.get(path.stem)
        if task_id is None:
            if strict:
                raise PipelineError("MuJoCo Demo video is orphaned from the freeze")
            continue
        try:
            record = _video_record(run, path, phase="demo")
        except PipelineError:
            if strict:
                raise
            continue
        report_scene_binding = scene_binding_by_task.get(str(task_id))
        video_scene_binding = record.get("resolved_scene_binding")
        if report_scene_binding is not None or video_scene_binding is not None:
            if report_scene_binding != video_scene_binding:
                raise PipelineError(
                    f"MuJoCo Demo video {task_id!r} resolved scene does not match "
                    "its oracle report"
                )
        records.append({**record, "task_id": task_id, **shared_binding})
    return records


def _write_video_index(
    *,
    paths: DemoPaths,
    run: RunManifest,
    mode: str,
    run_config: Mapping[str, Any],
    strict: bool,
) -> dict[str, Any]:
    """Freeze run-relative video evidence; offline runs explicitly record none."""

    validation_paths = sorted((run.root / "validation/videos").glob("round_*/*.mp4"))
    demo_paths = sorted((run.root / "demo/videos").glob("*.mp4"))
    if mode == "offline":
        if validation_paths or demo_paths:
            raise PipelineError("offline fixture must not create simulated MuJoCo videos")
        validation_records: list[dict[str, Any]] = []
        demo_records: list[dict[str, Any]] = []
    else:
        if run_config.get("runtime", {}).get("record_video_in_aws") is not True:
            raise PipelineError("AWS MuJoCo run must require validation and Demo video")
        validation_records = _validation_video_records(
            run, validation_paths, strict=strict
        )
        demo_records = _demo_video_records(run, demo_paths, strict=strict)
    runtime = run_config["runtime"]
    all_records = [*validation_records, *demo_records]
    for record in all_records:
        if (
            record["renderer_kind"] != runtime["video_renderer"]
            or record["codec"] != runtime["video_codec"]
            or not math.isclose(
                float(record["fps"]),
                float(runtime["video_fps"]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or int(record["width"]) != int(runtime["video_width"])
            or int(record["height"]) != int(runtime["video_height"])
        ):
            raise PipelineError("MuJoCo video metadata disagrees with frozen run configuration")
    index = {
        "schema_version": "robot_capability.mujoco_video_index.v1",
        "required": mode == "aws",
        "status": "complete" if strict else "partial",
        "renderer_kind": str(runtime["video_renderer"]),
        "codec": str(runtime["video_codec"]),
        "fps": float(runtime["video_fps"]),
        "width": int(runtime["video_width"]),
        "height": int(runtime["video_height"]),
        "validation": validation_records,
        "demo": demo_records,
        "offline_reason": (
            "deterministic orchestration fixture; no fake physical video"
            if mode == "offline"
            else None
        ),
    }
    issues = validate_json_schema(
        index,
        load_json(paths.root / "schemas/video_index.schema.json"),
        instance_path="$video_index",
    )
    if issues:
        raise PipelineError(
            "video index failed runtime schema validation: "
            + "; ".join(f"{item.path}: {item.message}" for item in issues)
        )
    atomic_write_json(run.root / "video_index.json", index)
    return index


def _aggregate_model_audits(audits: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate independent role clients without treating quota gauges as sums."""

    usage_values = [
        audit.get("usage", {})
        for audit in audits.values()
        if isinstance(audit.get("usage"), Mapping)
    ]

    def total_or_none(total_name: str, reported_name: str) -> int | float | None:
        reported = sum(int(value.get(reported_name, 0)) for value in usage_values)
        if reported == 0:
            return None
        return sum(
            item
            for value in usage_values
            if isinstance((item := value.get(total_name)), (int, float))
        )

    remaining_latest = [
        value.get("remaining_budget_latest")
        for value in usage_values
        if isinstance(value.get("remaining_budget_latest"), (int, float))
    ]
    remaining_minimum = [
        value.get("remaining_budget_minimum")
        for value in usage_values
        if isinstance(value.get("remaining_budget_minimum"), (int, float))
    ]
    return {
        "logical_requests": sum(int(value.get("logical_requests", 0)) for value in audits.values()),
        "successful_responses": sum(
            int(value.get("successful_responses", 0)) for value in audits.values()
        ),
        "failed_requests": sum(int(value.get("failed_requests", 0)) for value in audits.values()),
        "provider_http_attempts": sum(
            int(value.get("provider_http_attempts", 0)) for value in audits.values()
        ),
        "provider_retries": sum(
            int(value.get("provider_retries", 0)) for value in audits.values()
        ),
        "usage": {
            "response_count": sum(int(value.get("response_count", 0)) for value in usage_values),
            "input_tokens_total": total_or_none(
                "input_tokens_total", "input_tokens_reported"
            ),
            "output_tokens_total": total_or_none(
                "output_tokens_total", "output_tokens_reported"
            ),
            "cost_total": total_or_none("cost_total", "cost_reported"),
            "remaining_budget_latest": remaining_latest[-1] if remaining_latest else None,
            "remaining_budget_minimum": min(remaining_minimum) if remaining_minimum else None,
            "input_tokens_reported": sum(
                int(value.get("input_tokens_reported", 0)) for value in usage_values
            ),
            "output_tokens_reported": sum(
                int(value.get("output_tokens_reported", 0)) for value in usage_values
            ),
            "cost_reported": sum(int(value.get("cost_reported", 0)) for value in usage_values),
            "remaining_budget_reported": sum(
                int(value.get("remaining_budget_reported", 0)) for value in usage_values
            ),
        },
    }


def _model_audit_report(
    generation_client: ModelClient,
    suite_client: ModelClient,
    demo_client: ModelClient,
) -> dict[str, Any]:
    roles = {
        "generation": generation_client.audit_snapshot(),
        "validation_suite": suite_client.audit_snapshot(),
        "demo_consumer": demo_client.audit_snapshot(),
    }
    return {
        "public_identities": {
            name: public_model_identity(client)
            for name, client in {
                "generation": generation_client,
                "validation_suite": suite_client,
                "demo_consumer": demo_client,
            }.items()
        },
        "roles": roles,
        "aggregate": _aggregate_model_audits(roles),
    }


def _generation_phase_accounting(
    generation: ContinuousGeneration | None,
) -> dict[str, Any]:
    """Report independent Stage 1, initial Stage 2, and repair windows."""

    if generation is None:
        return {"stage1": None, "stage2_initial": None, "repair_rounds": []}

    def result_value(result: Any) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "status": result.status,
            "agent_turns": result.agent_turns,
            "provider_http_attempts": result.provider_http_attempts,
            "provider_retries": result.provider_retries,
            "agent_turn_budget": dict(result.agent_turn_budget),
            "provider_attempt_budget_policy": dict(
                result.provider_attempt_budget_policy
            ),
            "usage": dict(result.usage),
            "session_id": result.session_id,
            "episode_id": result.episode_id,
        }

    return {
        "stage1": result_value(generation.stage1_result),
        "stage2_initial": result_value(generation.stage2_result),
        "repair_rounds": generation.repair_result_audits(),
    }


def _repair_budget_state(
    generation: ContinuousGeneration | None,
    run_config: Mapping[str, Any],
) -> dict[str, Any]:
    round_limit = int(run_config["generation"]["max_repair_rounds"])
    calls_per_round = int(
        run_config["generation"]["repair_max_model_calls_per_round"]
    )
    rounds_used = 0 if generation is None else generation.repair_rounds
    results = [] if generation is None else generation.repair_result_audits()
    return {
        "round_limit": round_limit,
        "rounds_used": rounds_used,
        "rounds_remaining": max(0, round_limit - rounds_used),
        "model_call_limit_per_round": calls_per_round,
        "results": results,
    }


def _persist_repair_ledger(
    *,
    generation: ContinuousGeneration,
    run: RunManifest,
) -> Path:
    """Validate and atomically persist the privacy-safe repair ledger.

    This is a strict persistence boundary on ordinary execution: an invalid
    export must never be advertised through ``artifact_hashes``. Callers that
    are already handling a primary provider/runtime exception may invoke it
    best-effort so persistence cannot replace that primary cause.
    """

    schema_path = (
        run.root.parent.parent / "schemas" / "repair_ledger.schema.json"
    )
    if schema_path.is_symlink() or not schema_path.is_file():
        raise PipelineError(
            f"repair ledger schema is missing or unsafe: {schema_path}"
        )
    try:
        schema = load_json(schema_path)
    except BaseException as exc:
        raise PipelineError("repair ledger schema is unreadable") from exc

    ledger = generation.repair_ledger_audit()
    issues = validate_json_schema(
        ledger,
        schema,
        instance_path="$repair_ledger",
    )
    if issues:
        raise PipelineError(
            "repair ledger failed runtime schema validation: "
            + "; ".join(f"{item.path}: {item.message}" for item in issues)
        )
    semantic_issues = repair_ledger_semantic_issues(ledger)
    if semantic_issues:
        raise PipelineError(
            "repair ledger failed semantic validation: "
            + "; ".join(semantic_issues)
        )

    validation_root = run.root / "validation"
    if validation_root.exists() and validation_root.is_symlink():
        raise PipelineError("repair ledger validation directory must not be a symlink")
    ledger_path = validation_root / "repair_ledger.json"
    if ledger_path.exists() and ledger_path.is_symlink():
        raise PipelineError("repair ledger target must not be a symlink")
    resolved_run_root = run.root.resolve()
    resolved_ledger_path = ledger_path.resolve(strict=False)
    if (
        resolved_ledger_path != resolved_run_root
        and resolved_run_root not in resolved_ledger_path.parents
    ):
        raise PipelineError("repair ledger target escapes the run root")

    atomic_write_json(ledger_path, ledger)
    persisted = load_json(ledger_path)
    if persisted != ledger:
        raise PipelineError("persisted repair ledger changed JSON semantics")
    persisted_issues = validate_json_schema(
        persisted,
        schema,
        instance_path="$repair_ledger",
    )
    if persisted_issues:
        raise PipelineError("persisted repair ledger failed schema revalidation")
    persisted_semantic_issues = repair_ledger_semantic_issues(persisted)
    if persisted_semantic_issues:
        raise PipelineError(
            "persisted repair ledger failed semantic revalidation: "
            + "; ".join(persisted_semantic_issues)
        )
    run.artifact_hashes["repair_ledger"] = sha256_file(ledger_path)
    run.save()
    return ledger_path


def _no_package_change_feedback(
    *,
    causal_feedback: Mapping[str, Any],
    completed_round: int,
    feedback_round: int,
    consecutive_no_change_rounds: int,
    package_sha256: str,
    retry_available: bool,
) -> dict[str, Any]:
    """Carry real validation evidence into the next no-change repair round.

    A package-change gate is not another validation stage.  In particular, it
    must not replace the most recent static/direct failure with a generic
    generation failure: that would discard the only causal evidence the Agent
    can use.  ``failures`` is therefore copied byte-for-byte at the JSON value
    level, while gate facts live in separate ``no_package_change`` metadata.
    """

    schema_version = causal_feedback.get("schema_version")
    if schema_version != "robot_capability.failure_feedback.v1":
        raise PipelineError("repair carry-over requires failure_feedback.v1")
    stage = causal_feedback.get("stage")
    if stage not in {"static", "direct_function"}:
        raise PipelineError(
            "repair carry-over requires the latest real static/direct_function "
            "validation feedback"
        )
    causal_repair_round = causal_feedback.get("repair_round")
    if (
        isinstance(causal_repair_round, bool)
        or not isinstance(causal_repair_round, int)
        or not 0 <= causal_repair_round <= 10
    ):
        raise PipelineError("causal validation feedback has an invalid repair_round")
    failures = causal_feedback.get("failures")
    if (
        not isinstance(failures, Sequence)
        or isinstance(failures, (str, bytes, bytearray))
        or not failures
        or not all(isinstance(item, Mapping) for item in failures)
    ):
        raise PipelineError("causal validation feedback must contain real failures")
    if any(item.get("code") == "NO_PACKAGE_CHANGE" for item in failures):
        raise PipelineError(
            "NO_PACKAGE_CHANGE metadata cannot replace causal validation failures"
        )
    if not 1 <= consecutive_no_change_rounds <= 10:
        raise PipelineError("consecutive no-package-change rounds must be 1..10")

    return {
        "schema_version": schema_version,
        "stage": stage,
        "repair_round": feedback_round,
        "failures": copy.deepcopy(list(failures)),
        "no_package_change": {
            "code": "NO_PACKAGE_CHANGE",
            "completed_repair_round": completed_round,
            "consecutive_rounds": consecutive_no_change_rounds,
            "causal_feedback_repair_round": causal_repair_round,
            "package_sha256": package_sha256,
            "retry_available": retry_available,
        },
    }


def _repair_until_package_changes(
    *,
    generation: ContinuousGeneration,
    package_root: Path,
    initial_feedback: Mapping[str, Any],
    repair_history: list[dict[str, Any]],
    run: RunManifest,
    run_config: Mapping[str, Any],
) -> bool:
    """Run independent repair rounds until package bytes actually change.

    Static/direct validation and their MuJoCo video environments live outside
    this function.  Returning ``True`` is therefore the only path back to
    those expensive stages.  Every no-change round still consumes and reports
    its independent Agent accounting window.
    """

    round_limit = int(run_config["generation"]["max_repair_rounds"])
    calls_per_round = int(
        run_config["generation"]["repair_max_model_calls_per_round"]
    )
    # Anchor carry-over to the latest *real* validation result.  If this helper
    # returns after a changed package, the outer loop validates again and a new
    # invocation receives the newer static/direct feedback.
    causal_feedback = copy.deepcopy(dict(initial_feedback))
    feedback = copy.deepcopy(causal_feedback)
    consecutive_no_change_rounds = 0
    while generation.repair_rounds < round_limit:
        expected_round = generation.repair_rounds + 1
        if feedback.get("repair_round") != expected_round:
            raise PipelineError(
                "repair feedback round does not match the next independent round: "
                f"feedback={feedback.get('repair_round')!r}, expected={expected_round}"
            )
        repair_history.append(feedback)
        atomic_write_json(
            run.root / "validation" / f"feedback_{expected_round:02d}.json",
            feedback,
        )
        if run.state != RunState.STAGE2_RUNNING:
            run.transition(RunState.STAGE2_RUNNING)

        package_before = package_tree_sha256(package_root)
        run.record(
            "repair_package_change_gate_started",
            repair_round=expected_round,
            package_before_sha256=package_before,
        )
        repair_failed = False
        try:
            generation.repair(
                feedback,
                max_rounds=round_limit,
                max_model_calls_per_round=calls_per_round,
            )
        except BaseException:
            repair_failed = True
            raise
        finally:
            try:
                _persist_repair_ledger(generation=generation, run=run)
            except BaseException:
                # On an ordinary repair return the ledger is a strict required
                # artifact. During exception unwinding, preserve the primary
                # provider/runtime failure; the top-level handler retries this
                # persistence boundary best-effort before writing terminal
                # evidence.
                if not repair_failed:
                    raise
        package_after = package_tree_sha256(package_root)
        completed_round = generation.repair_rounds
        package_changed = package_after != package_before
        next_round = completed_round + 1 if completed_round < round_limit else None
        no_change_feedback = None
        if not package_changed:
            consecutive_no_change_rounds += 1
            no_change_feedback = _no_package_change_feedback(
                causal_feedback=causal_feedback,
                completed_round=completed_round,
                feedback_round=(next_round or completed_round),
                consecutive_no_change_rounds=consecutive_no_change_rounds,
                package_sha256=package_after,
                retry_available=next_round is not None,
            )

        accounting = generation.repair_result_audits()[-1]
        repair_audit = generation.repair_ledger_audit()
        latest_repair_round = repair_audit["rounds"][-1]
        repair_process = latest_repair_round["process"]
        gate_result = {
            "schema_version": "robot_capability.repair_package_gate_result.v2",
            "repair_round": completed_round,
            "status": "package_changed" if package_changed else "no_package_change",
            "code": "PACKAGE_CHANGED" if package_changed else "NO_PACKAGE_CHANGE",
            "package_before_sha256": package_before,
            "package_after_sha256": package_after,
            "package_changed": package_changed,
            "validation_skipped": {
                "static": not package_changed,
                "direct": not package_changed,
                "video": not package_changed,
            },
            "next_repair_round": next_round if not package_changed else None,
            "feedback": no_change_feedback,
            "agent_accounting": accounting,
            # Safe observable actions only: no model prose, source bodies,
            # validation measurements, or private task identifiers.
            "repair_process": repair_process,
        }
        result_path = (
            run.root
            / "validation"
            / f"repair_result_{completed_round:02d}.json"
        )
        atomic_write_json(result_path, gate_result)
        result_sha256 = sha256_file(result_path)
        run.record(
            "repair_package_change_gate",
            repair_round=completed_round,
            code=gate_result["code"],
            package_before_sha256=package_before,
            package_after_sha256=package_after,
            static_skipped=not package_changed,
            direct_skipped=not package_changed,
            video_skipped=not package_changed,
            result_path=result_path.relative_to(run.root).as_posix(),
            result_sha256=result_sha256,
        )
        if package_changed:
            return True
        if next_round is None:
            run.transition(
                RunState.GENERATION_FAILED,
                reason="repair package-change gate exhausted after 10 rounds",
            )
            return False
        assert no_change_feedback is not None
        feedback = no_change_feedback
    raise PipelineError("repair package-change gate entered with exhausted budget")


def _write_run_inputs(
    *,
    paths: DemoPaths,
    run: RunManifest,
    mode: str,
) -> Path:
    """Write one compact, non-secret reproducibility record before Generation.

    This is deliberately a research manifest rather than a repeated TOCTOU
    gate.  Runtime privacy is enforced separately by the Generation snapshot
    allowlist and the private Demo execution bundle.
    """

    fixed_paths: set[Path] = set()
    for directory, pattern in (
        (paths.root / "configs", "*.yaml"),
        (paths.root / "prompts", "*.md"),
        (paths.root / "schemas", "*.json"),
    ):
        fixed_paths.update(path for path in directory.glob(pattern) if path.is_file())
    fixed_paths.update(
        path
        for path in paths.fixtures.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not path.name.endswith((".pyc", ".pyo"))
    )

    records: list[dict[str, Any]] = []
    for path in sorted(fixed_paths, key=lambda item: item.relative_to(paths.root).as_posix()):
        relative = path.relative_to(paths.root).as_posix()
        digest = sha256_file(path)
        run.input_hashes[f"fixed_file:{relative}"] = digest
        if relative.startswith("schemas/"):
            run.input_hashes[f"schema:{path.name}"] = digest
        elif relative.startswith("prompts/"):
            run.input_hashes[f"prompt:{path.name}"] = digest
        elif relative == "configs/run.yaml":
            run.input_hashes["config:run"] = digest
        elif relative == "configs/models.yaml":
            run.input_hashes["config:models"] = digest
        elif relative == "fixtures/validation_reference/tabletop_primitives.yaml":
            run.input_hashes["validation_reference"] = digest
        records.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    # Hash the evaluator code that is actually imported.  Tests may execute
    # against a temporary copy of the data libraries, so this root is resolved
    # from the module rather than assumed to be ``paths.root``.
    source_root = Path(__file__).resolve().parents[2]
    source_paths = {
        path
        for path in (source_root / "src/soarm_demo").rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    }
    source_paths.update(
        path
        for path in (
            source_root / "run_pipeline.py",
            source_root / "run_pipeline_with_video.py",
        )
        if path.is_file()
    )
    source_records: list[dict[str, Any]] = []
    for path in sorted(
        source_paths, key=lambda item: item.relative_to(source_root).as_posix()
    ):
        relative = path.relative_to(source_root).as_posix()
        digest = sha256_file(path)
        record = {
            "path": f"source/{relative}",
            "bytes": path.stat().st_size,
            "sha256": digest,
        }
        records.append(record)
        source_records.append(record)
        run.input_hashes[f"fixed_file:source/{relative}"] = digest
    run.input_hashes["source_tree"] = sha256_json(source_records)

    document = {
        "schema_version": "robot_capability.run_inputs.v1",
        "run_id": run.run_id,
        "mode": mode,
        "recorded_at": utc_now(),
        "fixed_files": records,
        "input_hashes": dict(sorted(run.input_hashes.items())),
        "research_boundaries": {
            "generation_uses_public_snapshot_only": True,
            "heldout_tasks_and_oracles_remain_private": True,
            "objective_validation_uses_programmatic_state": True,
            "mujoco_worlds_are_created_only_for_executed_probes_cases_or_tasks": True,
        },
    }
    destination = run.root / "run_inputs.json"
    atomic_write_json(destination, document)
    run.artifact_hashes["run_inputs"] = sha256_file(destination)
    run.record(
        "run_inputs_recorded",
        fixed_file_count=len(records),
        sha256=run.artifact_hashes["run_inputs"],
    )
    return destination


def _run_evidence(paths: DemoPaths, run: RunManifest) -> dict[str, Any]:
    """Hash every reproducibility-critical success artifact without embedding it."""

    package_root = run.root / "generated_package"
    package_files = [
        path
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc")
    ]
    validation_root = run.root / "validation"
    demo_root = run.root / "demo"
    trace_paths = [run.root / "generation_trace.jsonl"]
    if validation_root.is_dir():
        trace_paths.extend(validation_root.rglob("*.jsonl"))
    if demo_root.is_dir():
        trace_paths.extend(demo_root.rglob("*.jsonl"))
        trace_paths.extend(
            path for path in demo_root.rglob("*") if path.is_file() and "trace" in path.name
        )
    video_paths = [run.root / "video_index.json"]
    video_paths.extend(sorted((run.root / "validation/videos").rglob("*.mp4")))
    video_paths.extend(sorted((run.root / "validation/videos").rglob("*.metadata.json")))
    video_paths.extend(sorted((run.root / "demo/videos").rglob("*.mp4")))
    video_paths.extend(sorted((run.root / "demo/videos").rglob("*.metadata.json")))
    return {
        "configuration": {
            "run": _file_evidence(paths.root, paths.root / "configs/run.yaml"),
            "models": _file_evidence(paths.root, paths.root / "configs/models.yaml"),
            "sdk_activation": _file_evidence(
                run.root, run.root / "framework/sdk_activation.json"
            ),
            "run_inputs": _file_evidence(run.root, run.root / "run_inputs.json"),
            "generation_input_snapshot": _file_evidence(
                run.root,
                run.root / "generation_input_snapshot_manifest.json",
            ),
            "prompts": _evidence_for_paths(
                paths.root, sorted((paths.root / "prompts").glob("*.md"))
            ),
        },
        "run_manifest_preseal": _file_evidence(
            run.root, run.root / "run_manifest_preseal.json"
        ),
        "input_library_report": _file_evidence(
            run.root, run.root / "input_library_report.json"
        ),
        "private_execution_preflight": _file_evidence(
            run.root, run.root / "framework/private_execution_preflight.json"
        ),
        "stage1": _evidence_for_paths(run.root, sorted((run.root / "stage1").glob("*.json"))),
        "generated_package": {
            "tree_sha256": run.artifact_hashes.get("generated_package"),
            "files": _evidence_for_paths(run.root, package_files),
        },
        "public_api": _file_evidence(
            run.root, validation_root / "public_api_freeze.json"
        ),
        "validation_suite": _evidence_for_paths(
            run.root,
            [
                validation_root / "suite/suite.json",
                validation_root / "suite/freeze.json",
                validation_root / "suite/test_generated_suite.py",
            ],
        ),
        "static_validation": _evidence_for_paths(
            run.root, sorted(validation_root.glob("static_round_*.json"))
        ),
        "direct_validation": _evidence_for_paths(
            run.root, sorted(validation_root.glob("direct_round_*.json"))
        ),
        "tool_catalogs": _evidence_for_paths(
            run.root, sorted((run.root / "tools").glob("*.json"))
        ),
        "demo": _evidence_for_paths(
            run.root, [demo_root / "freeze.json", demo_root / "demo_report.json"]
        ),
        "videos": _evidence_for_paths(run.root, video_paths),
        "traces": _evidence_for_paths(run.root, trace_paths),
    }


def _existing_run_files(run: RunManifest) -> list[dict[str, Any]]:
    """Best-effort failure evidence; exclude reports that would be self-referential."""

    paths = [
        path
        for path in run.root.rglob("*")
        if path.is_file()
        and "private_execution_bundle" not in path.relative_to(run.root).parts
        and path.name not in {
            "run_manifest.json",
            "terminal_report.json",
            "sealed_report.json",
        }
        and "__pycache__" not in path.parts
        and not path.name.endswith(".pyc")
    ]
    return _evidence_for_paths(run.root, paths)


def _load_run_config(paths: DemoPaths) -> dict[str, Any]:
    value = load_structured(paths.root / "configs/run.yaml")
    if not isinstance(value, dict):
        raise PipelineError("configs/run.yaml must contain an object")
    if value.get("schema_version") != "robot_capability.demo_run_config.v1":
        raise PipelineError("configs/run.yaml has an unsupported schema_version")
    expected = {
        "stage1": value.get("generation", {}).get("stage1_max_model_calls"),
        "stage2": value.get("generation", {}).get("stage2_initial_max_model_calls"),
        "repairs": value.get("generation", {}).get("max_repair_rounds"),
        "repair_calls": value.get("generation", {}).get(
            "repair_max_model_calls_per_round"
        ),
        "suite": value.get("validation", {}).get("suite_max_model_calls"),
        "demo": value.get("demo", {}).get("max_model_calls_per_task"),
    }
    if expected != {
        "stage1": 3,
        "stage2": 30,
        "repairs": 10,
        "repair_calls": 6,
        "suite": 3,
        "demo": 30,
    }:
        raise PipelineError(
            "P0 run budgets must be Stage1=3, initial Stage2=30, "
            "repair=10 rounds with 6 calls/round, validation suite=3, "
            "Demo=30/task"
        )
    target = value.get("target", {})
    if target.get("morphology_scene_entry") != "scenes/soarm101_tabletop/v1":
        raise PipelineError(
            "P0 target.morphology_scene_entry must select the versioned "
            "SOARM101 tabletop scene inside the Morphology Library"
        )
    public_simulation = value.get("generation", {}).get("public_simulation", {})
    if public_simulation != {
        "enabled": True,
        "profile": "soarm101_tabletop_public_smoke_v1",
        "max_probe_calls": 12,
        "max_total_simulation_s": 90,
        "max_simulation_s_per_call": 15,
        "max_runtime_calls_per_probe": 2000,
        "worker_startup_timeout_s": 10,
        "wall_timeout_s": 12,
        "max_worker_memory_mb": 1536,
    }:
        raise PipelineError(
            "P0 Generation public simulation limits/profile must remain frozen"
        )
    runtime = value.get("runtime", {})
    if runtime.get("kind") != "lerobot_compatible_mujoco":
        raise PipelineError("P0 runtime.kind must be lerobot_compatible_mujoco")
    if float(runtime.get("simulation_hz", 0)) != 200.0:
        raise PipelineError("P0 runtime.simulation_hz must match the pinned MJCF at 200 Hz")
    if runtime.get("host_auto_step") is not False:
        raise PipelineError(
            "P0 AWS Validation/Demo runtime.host_auto_step must be false for "
            "deterministic generated-code time"
        )
    video_contract = {
        "record_video_in_aws": runtime.get("record_video_in_aws"),
        "video_renderer": runtime.get("video_renderer"),
        "video_codec": runtime.get("video_codec"),
        "video_fps": runtime.get("video_fps"),
        "video_width": runtime.get("video_width"),
        "video_height": runtime.get("video_height"),
    }
    if video_contract != {
        "record_video_in_aws": True,
        "video_renderer": "mujoco.Renderer",
        "video_codec": "mp4v",
        "video_fps": 20,
        "video_width": 640,
        "video_height": 480,
    }:
        raise PipelineError("P0 AWS video contract must be MuJoCo MP4 at 640x480, 20 fps")
    return value


def _model_clients(
    mode: str,
    paths: DemoPaths,
    run_config: Mapping[str, Any],
) -> tuple[ModelClient, ModelClient, ModelClient]:
    if mode == "offline":
        suite = (paths.fixtures / "validation_suite.json").read_text(
            encoding="utf-8"
        )
        return (
            ScriptedModelClient(offline_generation_responses(paths), model="scripted-generation"),
            ScriptedModelClient([suite, suite, suite], model="scripted-validation-suite"),
            ScriptedModelClient(offline_demo_responses(paths), model="scripted-demo-consumer"),
        )
    if mode != "aws":
        raise ValueError("mode must be 'offline' or 'aws'")
    model_document = load_structured(paths.root / "configs/models.yaml")
    if not isinstance(model_document, Mapping):
        raise PipelineError("configs/models.yaml must contain an object")
    profile_names = {
        str(run_config.get("generation", {}).get("model_profile")),
        str(run_config.get("validation", {}).get("suite_model_profile")),
        str(run_config.get("demo", {}).get("model_profile")),
    }
    if len(profile_names) != 1:
        raise PipelineError("P0 requires one frozen model profile for all three Agent roles")
    profile_name = profile_names.pop()
    profiles = model_document.get("profiles", {})
    profile = profiles.get(profile_name) if isinstance(profiles, Mapping) else None
    if not isinstance(profile, Mapping) or profile.get("provider") != "aws_model_api":
        raise PipelineError(f"unknown AWS model profile {profile_name!r}")
    config = AWSModelAPIConfig(
        model=str(profile["model"]),
        api_key_env=str(profile["api_key_env"]),
        route=str(profile["route"]),
        short_endpoint=str(profile["short_endpoint"]),
        long_base_url=str(profile["long_base_url"]),
        long_path=str(profile["long_path"]),
        max_tokens=int(profile["max_tokens"]),
        temperature=float(profile["temperature"]),
        short_timeout_s=float(profile["short_timeout_s"]),
        long_timeout_s=float(profile["long_timeout_s"]),
        max_retries=int(profile["max_retries"]),
    )
    # The plan defines one LLM call as one provider API request.  Transparent
    # retries would turn a 30-call Agent ceiling into more than 30 HTTP calls.
    if config.max_retries != 0:
        raise PipelineError("P0 model profile must set max_retries=0 for hard request budgets")
    api_key = os.environ.pop(config.api_key_env, None)
    if not api_key:
        raise PipelineError(
            f"AWS mode requires the complete key in {config.api_key_env}; a truncated key is not usable"
        )
    # Generated Python executes later in this process.  Keep the credential out
    # of os.environ and inject it only into the three fixed infrastructure
    # clients; traces and generated code never receive it.
    # Separate client objects make the identity/context boundary explicit even
    # though all three use the same stateless endpoint configuration.
    return (
        AWSModelAPIClient(config, api_key=api_key),
        AWSModelAPIClient(config, api_key=api_key),
        AWSModelAPIClient(config, api_key=api_key),
    )


def _load_local_env(path: Path) -> None:
    """Load the ignored Demo .env without logging or overwriting real env vars."""

    if not path.is_file():
        return
    allowed = {
        "AWS_MODEL_API_KEY",
        "AWS_MODEL_API_SHORT_ENDPOINT",
        "AWS_MODEL_API_LONG_BASE_URL",
        "AWS_MODEL_API_LONG_PATH",
    }
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in allowed:
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(name, value)


def run_pipeline(
    *,
    demo_root: str | Path,
    mode: str = "offline",
    run_id: str | None = None,
) -> Path:
    paths = DemoPaths(Path(demo_root).resolve())
    # Offline orchestration has no provider requests and must not load a live
    # credential into its process at all. AWS mode pops the value from the
    # environment immediately after constructing the three fixed clients.
    if mode == "aws":
        if os.environ.get("SOARM_MUJOCO_VIDEO_LAUNCHER") != "1":
            raise PipelineError(
                "AWS mode requires run_pipeline_with_video.py so MuJoCo video "
                "recording is available before any model call"
            )
        _load_local_env(paths.root / ".env")
    run_config = _load_run_config(paths)
    # This decision is deliberately evaluated before model-client construction.
    # A manifest status alone has no authority to activate live AWS generation.
    try:
        sdk_activation_decision = evaluate_sdk_activation(
            sdk_root=paths.libraries["sdk_runtime"],
            schemas_root=paths.root / "schemas",
            mode=mode,
        )
    except SDKActivationError as exc:
        raise PipelineError(str(exc)) from exc
    generation_client, suite_client, demo_client = _model_clients(
        mode,
        paths,
        run_config,
    )
    run = RunManifest.create(paths.root / "runs", run_id=run_id)
    repair_history: list[dict[str, Any]] = []
    generation: ContinuousGeneration | None = None
    generator: ValidationSuiteGenerator | None = None
    simulation_sandbox: GenerationSimulationSandbox | None = None
    private_bundle: PrivateExecutionBundle | None = None
    demo_report: Mapping[str, Any] | None = None
    video_index: Mapping[str, Any] | None = None
    video_index_failure: str | None = None
    sdk_activation: Mapping[str, Any] | None = None
    try:
        sdk_activation = freeze_sdk_activation(
            sdk_root=paths.libraries["sdk_runtime"],
            run_root=run.root,
            decision=sdk_activation_decision,
            schema_path=paths.root / "schemas/sdk_activation.schema.json",
        )
        sdk_decision = sdk_activation["decision"]
        for binding_name, binding in sdk_decision["bindings"].items():
            run.input_hashes[f"sdk_activation:{binding_name}"] = str(
                binding["sha256"]
            )
        run.artifact_hashes["sdk_activation"] = str(
            sdk_activation["evidence"]["sha256"]
        )
        if any(
            int(client.audit_snapshot().get("logical_requests", 0)) != 0
            for client in (generation_client, suite_client, demo_client)
        ):
            raise PipelineError("a provider request occurred before SDK activation freeze")
        input_snapshot, input_reports = materialize_inputs(paths, run)
        _resolved_environment, simulation_sandbox = _prepare_generation_environment(
            paths=paths,
            run=run,
            run_config=run_config,
            mode=mode,
            input_snapshot=input_snapshot,
        )
        scene_execution = _frozen_scene_execution_context(simulation_sandbox)
        body_extent_resolver = _catalog_body_extent_resolver(
            scene_execution["catalog"]
        )
        if any(
            int(client.audit_snapshot().get("logical_requests", 0)) != 0
            for client in (generation_client, suite_client, demo_client)
        ):
            raise PipelineError("private inputs must freeze before provider request 1")
        task_library_manifest = load_structured(
            paths.libraries["tasks"] / "manifest.yaml"
        )
        expected_private_partition_hash = (
            task_library_manifest.get("content_hashes", {}).get(
                "private_partition_bundle"
            )
            if isinstance(task_library_manifest, Mapping)
            else None
        )
        if not isinstance(expected_private_partition_hash, str):
            raise PipelineError(
                "task library manifest lacks its private-partition bundle hash"
            )
        private_bundle = materialize_private_execution_bundle(
            private_task_root=paths.private_tasks,
            visible_tasks_path=paths.libraries["tasks"] / "visible_tasks.jsonl",
            destination=run.root / "private_execution_bundle",
            schema_path=paths.root / "schemas/private_execution_bundle.schema.json",
            expected_private_partition_sha256=expected_private_partition_hash,
        )
        run.input_hashes["private_execution_bundle_freeze"] = (
            private_bundle.freeze_sha256
        )
        run.input_hashes["private_execution_bundle_manifest"] = (
            private_bundle.manifest_sha256
        )
        run.input_hashes["task_private_partition_bundle"] = (
            private_bundle.private_partition_source_sha256
        )
        run.input_hashes["task_demo_batch"] = private_bundle.sha256_for_role(
            "demo_batch"
        )
        run.input_hashes["task_private_oracles"] = private_bundle.sha256_for_role(
            "task_oracles"
        )
        run.input_hashes["task_visible_catalog"] = private_bundle.sha256_for_role(
            "visible_tasks"
        )
        run.input_hashes["task_pilot_heldout_catalog"] = (
            private_bundle.sha256_for_role("heldout_tasks")
        )
        run.input_hashes["demo_common_reset"] = private_bundle.sha256_for_role(
            "common_reset"
        )
        for ordinal in range(1, 7):
            run.input_hashes[f"demo_initial_state:{ordinal:02d}"] = (
                private_bundle.sha256_for_role(f"instance_{ordinal:02d}")
            )
        for ordinal in range(1, 13):
            run.input_hashes[f"authored_initial_state:{ordinal:02d}"] = (
                private_bundle.sha256_for_role(
                    f"authored_instance_{ordinal:02d}"
                )
            )
        private_preflight = _private_execution_preflight(
            paths=paths,
            run=run,
            mode=mode,
            bundle=private_bundle,
            scene_execution=scene_execution,
        )
        preflight_path = run.root / "framework/private_execution_preflight.json"
        run.artifact_hashes["private_execution_preflight"] = sha256_file(
            preflight_path
        )
        partition_counts = private_preflight["partition_counts"]
        if (
            partition_counts.get("catalog_visible") != 9
            or partition_counts.get("catalog_pilot_heldout") != 3
            or partition_counts.get("authored_instances") != 12
            or partition_counts.get("selected_visible") != 3
            or partition_counts.get("selected_pilot_heldout") != 3
            or partition_counts.get("selected_overlap") != 0
        ):
            raise PipelineError(
                "private execution preflight did not prove the disjoint 9/3 "
                "catalog and selected 3/3 Demo split"
            )
        if any(
            int(client.audit_snapshot().get("logical_requests", 0)) != 0
            for client in (generation_client, suite_client, demo_client)
        ):
            raise PipelineError("a provider request occurred before private preflight")
        run.transition(RunState.INPUT_LIBRARIES_READY)
        batch = private_bundle.read_json("demo_batch")
        ordered_instances = batch.get("ordered_instances")
        if not isinstance(ordered_instances, list) or len(ordered_instances) != 6:
            raise PipelineError("P0 Demo batch must precommit exactly six instances")
        seen_instance_refs: set[str] = set()
        for entry in ordered_instances:
            if not isinstance(entry, Mapping):
                raise PipelineError("Demo batch entry must be an object")
            instance_ref = Path(str(entry.get("instance_ref", "")))
            if (
                instance_ref.is_absolute()
                or len(instance_ref.parts) != 2
                or instance_ref.parts[0] != "initial_states"
                or instance_ref.as_posix() in seen_instance_refs
            ):
                raise PipelineError("Demo batch contains an invalid or duplicate instance_ref")
            seen_instance_refs.add(instance_ref.as_posix())
            ordinal = entry.get("ordinal")
            if not isinstance(ordinal, int) or not 1 <= ordinal <= 6:
                raise PipelineError("Demo batch ordinal is invalid")
            instance = private_bundle.read_json(f"instance_{ordinal:02d}")
            if instance.get("task_id") != entry.get("task_id"):
                raise PipelineError("Demo bundle instance/task binding is invalid")
        _safe_identifier_index(
            [
                entry.get("task_id") if isinstance(entry, Mapping) else None
                for entry in ordered_instances
            ],
            label="Demo task_id",
        )
        run.transition(RunState.TASK_SPLIT_FROZEN)

        workspace = GenerationWorkspace(
            input_snapshot=input_snapshot,
            run_root=run.root,
            stage1_schema=load_json(paths.root / "schemas/stage1.schema.json"),
            package_manifest_schema=load_json(
                paths.root / "schemas/capability_manifest.schema.json"
            ),
            allow_orchestration_fixture_evidence=mode == "offline",
            simulation_sandbox=simulation_sandbox,
        )
        snapshot_audit = workspace.input_snapshot_manifest_audit
        run.input_hashes["generation_input_snapshot_manifest"] = str(
            snapshot_audit["manifest_sha256"]
        )
        run.input_hashes["generation_input_snapshot_tree"] = str(
            snapshot_audit["tree_sha256"]
        )
        if any(
            int(client.audit_snapshot().get("logical_requests", 0)) != 0
            for client in (generation_client, suite_client, demo_client)
        ):
            raise PipelineError("a provider request occurred before run inputs were recorded")
        _write_run_inputs(paths=paths, run=run, mode=mode)
        generation = ContinuousGeneration.create(
            client=generation_client,
            workspace=workspace,
            system_prompt=(paths.root / "prompts/generation_system.md").read_text(
                encoding="utf-8"
            ),
            trace_path=run.root / "generation_trace.jsonl",
            stage1_call_limit=int(run_config["generation"]["stage1_max_model_calls"]),
            repair_instruction=(paths.root / "prompts/repair.md").read_text(
                encoding="utf-8"
            ),
            require_repair_artifact_change=True,
        )
        run.transition(RunState.GENERATION_AGENT_READY)
        run.transition(RunState.STAGE1_RUNNING)
        stage1 = generation.run_stage1(
            (paths.root / "prompts/stage1.md").read_text(encoding="utf-8")
        )
        run.artifact_hashes["stage1"] = stage1.sha256
        run.transition(RunState.STAGE1_FROZEN)
        run.transition(RunState.STAGE2_RUNNING)
        package_root = generation.run_stage2(
            (paths.root / "prompts/stage2.md").read_text(encoding="utf-8"),
            initial_call_limit=int(
                run_config["generation"]["stage2_initial_max_model_calls"]
            ),
        )

        frozen_suite: Mapping[str, Any] | None = None
        frozen_api_hash: str | None = None
        direct_report = None
        while True:
            run.transition(RunState.STATIC_VALIDATION)
            static_report = validate_generated_package(package_root, stage1.artifact)
            static_path = run.root / "validation" / f"static_round_{generation.repair_rounds:02d}.json"
            atomic_write_json(static_path, static_report.to_dict())
            if generation.repair_rounds > 0:
                static_observation = static_report.to_failure_feedback(
                    generation.repair_rounds
                )
                if static_observation is None:
                    static_observation = {
                        "schema_version": (
                            "robot_capability.validation_observation.v1"
                        ),
                        "stage": "static",
                        "repair_round": generation.repair_rounds,
                        "passed": True,
                        "failures": [],
                    }
                else:
                    static_observation = dict(static_observation)
                    static_observation["passed"] = False
                generation.observe_validation_feedback(
                    static_observation,
                    after_repair_round=generation.repair_rounds,
                )
                _persist_repair_ledger(generation=generation, run=run)
            if not static_report.passed:
                if (
                    generation.repair_rounds
                    >= int(run_config["generation"]["max_repair_rounds"])
                ):
                    run.transition(RunState.VALIDATION_FAILED, reason="static validation repair budget exhausted")
                    break
                feedback = static_report.to_failure_feedback(generation.repair_rounds + 1)
                assert feedback is not None
                package_changed = _repair_until_package_changes(
                    generation=generation,
                    package_root=package_root,
                    initial_feedback=feedback,
                    repair_history=repair_history,
                    run=run,
                    run_config=run_config,
                )
                if not package_changed:
                    break
                continue

            package_manifest = load_json(package_root / "package_manifest.json")
            current_api_hash = _public_api_hash(package_manifest)
            if frozen_api_hash is None:
                frozen_api_hash = current_api_hash
                atomic_write_json(
                    run.root / "validation/public_api_freeze.json",
                    {
                        "schema_version": "robot_capability.public_api_freeze.v1",
                        "public_api_sha256": frozen_api_hash,
                    },
                )
            elif current_api_hash != frozen_api_hash:
                run.transition(RunState.VALIDATION_FAILED, reason="repair changed frozen public API")
                break

            if frozen_suite is None:
                run.transition(RunState.VALIDATION_SUITE_GENERATION)
                reference = load_structured(
                    paths.fixtures / "validation_reference/tabletop_primitives.yaml"
                )
                if not isinstance(reference, dict):
                    raise PipelineError("validation reference must contain an object")
                # The private suite designer receives the exact immutable asset
                # vocabulary, not a second hand-authored set of cube defaults.
                # The compact suite it returns still contains only
                # asset_ref/id/pose; trusted checks use this detached catalog
                # projection to recover geometry and physics.
                reference = copy.deepcopy(reference)
                reference.update(
                    {
                        "require_scene_asset_refs": True,
                        "scene_asset_catalog": copy.deepcopy(
                            scene_execution["scene_catalog_document"]
                        ),
                        "scene_asset_catalog_sha256": scene_execution[
                            "scene_catalog_sha256"
                        ],
                        "generation_environment_freeze_sha256": scene_execution[
                            "environment_freeze_sha256"
                        ],
                        "scene_revision_sha256": scene_execution[
                            "scene_revision_sha256"
                        ],
                    }
                )
                case_schema = load_json(
                    paths.root / "schemas/validation_case.schema.json"
                )
                generator = ValidationSuiteGenerator(
                    client=suite_client,
                    prompts=_prompts(
                        paths,
                        [
                            "validation_generate.md",
                            "validation_review.md",
                            "validation_final_review.md",
                        ],
                    ),
                    output_dir=run.root / "validation/suite",
                    trace_path=run.root / "validation/suite_trace.jsonl",
                    call_limit=int(run_config["validation"]["suite_max_model_calls"]),
                )
                if mode == "aws":
                    def candidate_preflight(
                        candidate: Mapping[str, Any],
                    ) -> tuple[str, ...]:
                        return preflight_validation_suite(
                            candidate,
                            environment_factory=lambda case: (
                                MujocoTabletopValidationEnvironment(
                                    case,
                                    model_path=scene_execution["model_path"],
                                    common_reset=private_bundle.read_json(
                                        "common_reset"
                                    ),
                                    auto_step=False,
                                    scene_catalog=scene_execution[
                                        "scene_catalog_path"
                                    ],
                                    scene_freeze=scene_execution["scene_freeze"],
                                    require_scene_catalog=True,
                                    require_explicit_asset_refs=True,
                                )
                            ),
                            package_manifest=package_manifest,
                            body_extent_resolver=body_extent_resolver,
                        )
                else:
                    candidate_preflight = None
                try:
                    suite_result = generator.generate(
                        stage1=stage1.artifact,
                        package_manifest=package_manifest,
                        reference_library=reference,
                        case_schema=case_schema,
                        candidate_preflight=candidate_preflight,
                        model_configuration_sha256=sha256_file(
                            paths.root / "configs/models.yaml"
                        ),
                    )
                except ValidationSuiteError as exc:
                    run.transition(
                        RunState.VALIDATION_SUITE_GENERATION_FAILED,
                        reason=str(exc),
                    )
                    break
                frozen_suite = suite_result.suite
                _safe_identifier_index(
                    [
                        case.get("case_id") if isinstance(case, Mapping) else None
                        for case in frozen_suite.get("cases", [])
                    ],
                    label="validation case_id",
                )
                run.artifact_hashes["validation_suite"] = suite_result.sha256
                run.budgets["validation_suite"] = generator.budget.to_dict()
                run.transition(RunState.DIRECT_FUNCTION_VALIDATION)
            else:
                run.transition(RunState.DIRECT_FUNCTION_VALIDATION)

            if mode == "aws":
                simulation_trace_root = run.root / "validation/simulation_traces"

                def validation_environment_factory(
                    case: Mapping[str, Any],
                ) -> MujocoTabletopValidationEnvironment:
                    video_path = (
                        run.root
                        / "validation/videos"
                        / f"round_{generation.repair_rounds:02d}"
                        / f"{_safe_trace_name(case.get('case_id'))}.mp4"
                    )
                    return MujocoTabletopValidationEnvironment(
                        case,
                        model_path=scene_execution["model_path"],
                        common_reset=private_bundle.read_json("common_reset"),
                        trace_path=simulation_trace_root
                        / f"round_{generation.repair_rounds:02d}"
                        / f"{_safe_trace_name(case.get('case_id'))}.jsonl",
                        video_path=video_path,
                        video_fps=float(run_config["runtime"]["video_fps"]),
                        video_width=int(run_config["runtime"]["video_width"]),
                        video_height=int(run_config["runtime"]["video_height"]),
                        auto_step=False,
                        scene_catalog=scene_execution["scene_catalog_path"],
                        scene_freeze=scene_execution["scene_freeze"],
                        require_scene_catalog=True,
                        require_explicit_asset_refs=True,
                    )

                direct_environment_factory = validation_environment_factory
            else:
                direct_environment_factory = FixtureValidationEnvironment

            try:
                direct_report = run_direct_validation(
                    package_root=package_root,
                    package_manifest=package_manifest,
                    suite=frozen_suite,
                    environment_factory=direct_environment_factory,
                    output_path=run.root
                    / "validation"
                    / f"direct_round_{generation.repair_rounds:02d}.json",
                )
            except InvalidValidationCaseError:
                run.transition(
                    RunState.VALIDATION_SUITE_GENERATION_FAILED,
                    reason="frozen validation case failed executable preflight",
                )
                break
            except ValidationHarnessInfrastructureError:
                run.transition(
                    RunState.INFRASTRUCTURE_FAILED,
                    reason="direct validation harness infrastructure failed",
                )
                break
            if generation.repair_rounds > 0:
                direct_observation = direct_report.failure_feedback(
                    repair_round=generation.repair_rounds
                )
                direct_observation["passed"] = bool(direct_report.passed)
                generation.observe_validation_feedback(
                    direct_observation,
                    after_repair_round=generation.repair_rounds,
                )
                _persist_repair_ledger(generation=generation, run=run)
            video_index = _write_video_index(
                paths=paths,
                run=run,
                mode=mode,
                run_config=run_config,
                strict=False,
            )
            run.artifact_hashes["video_index"] = sha256_file(
                run.root / "video_index.json"
            )
            if direct_report.passed:
                break
            if (
                generation.repair_rounds
                >= int(run_config["generation"]["max_repair_rounds"])
            ):
                run.transition(RunState.VALIDATION_FAILED, reason="direct validation repair budget exhausted")
                break
            feedback = direct_report.failure_feedback(repair_round=generation.repair_rounds + 1)
            package_changed = _repair_until_package_changes(
                generation=generation,
                package_root=package_root,
                initial_feedback=feedback,
                repair_history=repair_history,
                run=run,
                run_config=run_config,
            )
            if not package_changed:
                break

        if direct_report is None or not direct_report.passed:
            raise PipelineError(f"pipeline stopped in {run.state}: {run.terminal_reason}")

        run.transition(RunState.TOOL_PACKAGING)
        packaging_runtime = DeterministicTabletopRuntime()
        packaged = package_validated_tools(
            package_root=package_root,
            stage1=stage1.artifact,
            package_manifest=package_manifest,
            validation_passed=True,
            validated_package_sha256=direct_report.package_sha256,
            runtime=packaging_runtime,
            output_dir=run.root / "tools",
        )
        run.artifact_hashes["generated_package"] = packaged.package_sha256
        combined = packaged.catalogs["combined"]
        for catalog_name, catalog in packaged.catalogs.items():
            run.artifact_hashes[f"{catalog_name.lower()}_tool_catalog"] = sha256_json(
                catalog
            )
        run.transition(RunState.DEMO_FROZEN)

        if mode == "aws":
            demo_simulation_trace_root = run.root / "demo/simulation_traces"

            def demo_environment_factory(
                instance: Mapping[str, Any],
            ) -> MujocoTabletopDemoEnvironment:
                task_name = _safe_trace_name(instance.get("task_id"))
                return MujocoTabletopDemoEnvironment(
                    instance,
                    model_path=scene_execution["model_path"],
                    common_reset=private_bundle.read_json("common_reset"),
                    trace_path=demo_simulation_trace_root
                    / f"{task_name}.jsonl",
                    video_path=run.root / "demo/videos" / f"{task_name}.mp4",
                    video_fps=float(run_config["runtime"]["video_fps"]),
                    video_width=int(run_config["runtime"]["video_width"]),
                    video_height=int(run_config["runtime"]["video_height"]),
                    auto_step=False,
                    scene_catalog=scene_execution["scene_catalog_path"],
                    scene_freeze=scene_execution["scene_freeze"],
                    require_scene_catalog=True,
                    require_explicit_asset_refs=True,
                )

            demo_environment = demo_environment_factory
            oracle_evaluator_id = (
                "soarm_demo.oracle:MujocoTabletopDemoEnvironment.score"
            )
        else:
            demo_environment = FixtureDemoEnvironment
            oracle_evaluator_id = "soarm_demo.oracle:FixtureDemoEnvironment.score"

        runner = FrozenDemoRunner(
            client=demo_client,
            system_prompt=(paths.root / "prompts/demo_react.md").read_text(
                encoding="utf-8"
            ),
            combined_catalog=combined,
            tool_factory=lambda runtime: packaged.dispatcher.rebind(
                runtime,
                function_preparer=(
                    prepare_generated_callable_for_mujoco
                    if mode == "aws"
                    else None
                ),
            ).react_tools(combined),
            environment_factory=demo_environment,
            output_dir=run.root / "demo",
            max_model_calls_per_task=int(run_config["demo"]["max_model_calls_per_task"]),
            generation_session_id=generation.agent.session_id,
            oracle_evaluator_id=oracle_evaluator_id,
            oracle_evaluator_path=Path(__file__).resolve().parent / "oracle.py",
            scene_facts_factory=scene_execution["catalog"].derive_agent_input,
        )
        run.transition(RunState.DEMO_RUNNING)
        demo_report = runner.run(
            run_id=run.run_id,
            private_execution_bundle=private_bundle,
        )
        demo_freeze = load_json(run.root / "demo/freeze.json")
        for artifact_name, artifact, schema_name in (
            ("Demo freeze", demo_freeze, "demo_freeze.schema.json"),
            ("Demo report", demo_report, "demo_report.schema.json"),
        ):
            issues = validate_json_schema(
                artifact,
                load_json(paths.root / "schemas" / schema_name),
                instance_path=f"${artifact_name.replace(' ', '_').lower()}",
            )
            if issues:
                raise PipelineError(
                    f"{artifact_name} failed runtime schema validation: "
                    + "; ".join(f"{item.path}: {item.message}" for item in issues)
                )
        demo_structure_issues = demo_report_structure_semantic_errors(
            demo_report,
            freeze=demo_freeze,
            freeze_sha256=sha256_file(run.root / "demo/freeze.json"),
        )
        if demo_structure_issues:
            raise PipelineError(
                "Demo report failed structural semantic validation: "
                + "; ".join(demo_structure_issues)
            )
        demo_infrastructure_failures = [
            f"demo.tasks[{index}] reported an infrastructure failure"
            for index, task in enumerate(demo_report.get("tasks", []))
            if isinstance(task, Mapping)
            and (
                task.get("termination_reason") == "infrastructure_failure"
                or task.get("agent_status") == "infrastructure_failed"
                or "infrastructure_error" in (
                    task.get("oracle")
                    if isinstance(task.get("oracle"), Mapping)
                    else {}
                )
            )
        ]
        if demo_infrastructure_failures:
            # FrozenDemoRunner deliberately records a task-local provider,
            # environment, or oracle exception and continues so the other
            # precommitted tasks still leave evidence.  Do not confuse that
            # report-level containment with an ordinary oracle miss: leaving
            # DEMO_RUNNING here lets the terminal handler classify the run as
            # INFRASTRUCTURE_FAILED.
            raise PipelineError("; ".join(demo_infrastructure_failures))
        video_index = _write_video_index(
            paths=paths,
            run=run,
            mode=mode,
            run_config=run_config,
            strict=True,
        )
        run.artifact_hashes["video_index"] = sha256_file(
            run.root / "video_index.json"
        )
        if generation.stage1_result is None:
            raise PipelineError("Stage 1 accounting is unavailable at seal time")
        run.budgets["generation_stage1"] = dict(
            generation.stage1_result.agent_turn_budget
        )
        if generation.stage2_result is None:
            raise PipelineError("Stage 2 accounting is unavailable at seal time")
        run.budgets["generation_stage2_initial"] = dict(
            generation.stage2_result.agent_turn_budget
        )
        run.budgets["demo"] = {
            "limit_per_task": int(run_config["demo"]["max_model_calls_per_task"]),
            "used_per_task": [task["model_calls"] for task in demo_report["tasks"]],
            "per_task": [task["agent_turn_budget"] for task in demo_report["tasks"]],
        }
        run.budgets["repair"] = _repair_budget_state(generation, run_config)
        run.record("budgets_finalized", budgets=run.budgets)
        run.artifact_hashes["public_api"] = str(frozen_api_hash)
        run.artifact_hashes["package_manifest"] = sha256_file(
            package_root / "package_manifest.json"
        )
        run.artifact_hashes["demo_freeze"] = sha256_file(run.root / "demo/freeze.json")
        run.artifact_hashes["demo_report"] = sha256_file(run.root / "demo/demo_report.json")
        # One last detached-manifest verification closes the Generation input
        # continuity chain at seal time, after every Stage 1/2/repair tool
        # boundary has already checked the same baseline.
        workspace.assert_input_snapshot_unchanged()
        run.save()
        # The live manifest changes once SEALED is recorded. Preserve the exact
        # pre-seal state as immutable evidence so its reported hash remains
        # verifiable after the final transition.
        atomic_write_json(
            run.root / "run_manifest_preseal.json",
            load_json(run.path),
        )
        evidence = _run_evidence(paths, run)
        model_accounting = _model_audit_report(
            generation_client, suite_client, demo_client
        )
        model_accounting["generation_phases"] = _generation_phase_accounting(
            generation
        )
        if mode == "aws":
            validation_runtime = (
                "actual MuJoCo MjModel/MjData tabletop runtime with SO-ARM101 "
                "LeRobot-compatible send_action/get_observation bridge"
            )
            evidence_boundary = {
                "validation": (
                    "direct generated-function calls use actual MuJoCo state, "
                    "contacts, forward kinematics, and forbidden-condition evidence"
                ),
                "demo": (
                    "six frozen task instances use actual MuJoCo terminal state and "
                    "private simulation traces; no teleport/attach shortcut"
                ),
                "reachability": (
                    "only capabilities/cases that pass the frozen direct suite are "
                    "attested; scene compilation alone is not task-success evidence"
                ),
                "pilot_heldout": "pipeline pilot, not formal generalization evidence",
                "real_hardware": "not evaluated",
            }
        else:
            validation_runtime = (
                "deterministic orchestration fixture; not physical evidence"
            )
            evidence_boundary = {
                "mujoco_bridge": "separate compile/API-semantic probe",
                "offline_fixture": "workflow reproducibility only",
                "pilot_heldout": "pipeline pilot, not formal generalization evidence",
            }

        sealed = {
            "schema_version": "robot_capability.sealed_run_report.v2",
            "run_id": run.run_id,
            "terminal_state": RunState.SEALED.value,
            "terminal_reason": "completed",
            "mode": mode,
            "sealed_at": utc_now(),
            "run_configuration": run_config,
            "input_hashes": run.input_hashes,
            "artifact_hashes": run.artifact_hashes,
            "evidence": evidence,
            "budgets": run.budgets,
            "model_accounting": model_accounting,
            "generation_agent": generation.audit_state(),
            "repairs": repair_history,
            "repair": {
                **_repair_budget_state(generation, run_config),
                "history": repair_history,
            },
            "validation": {
                "static_passed": True,
                "direct_function_passed": direct_report.passed,
                "suite_sha256": run.artifact_hashes["validation_suite"],
                "public_api_sha256": frozen_api_hash,
                "runtime": validation_runtime,
            },
            "static_validation_passed": True,
            "direct_function_validation_passed": direct_report.passed,
            "validation_runtime": validation_runtime,
            "demo": {
                **demo_report["summary"],
                "summary": demo_report["summary"],
                "tasks": demo_report["tasks"],
            },
            "video_recording": video_index,
            "evidence_boundary": evidence_boundary,
            "sdk_activation": {
                **sdk_activation,
            },
        }
        sealed_issues = validate_json_schema(
            sealed,
            load_json(paths.root / "schemas/sealed_run_report.schema.json"),
            instance_path="$sealed",
        )
        if sealed_issues:
            raise PipelineError(
                "sealed report failed runtime schema validation: "
                + "; ".join(f"{item.path}: {item.message}" for item in sealed_issues)
            )
        semantic_issues = sealed_report_semantic_errors(sealed, run_root=run.root)
        if semantic_issues:
            raise PipelineError(
                "sealed report failed cross-artifact semantic audit: "
                + "; ".join(semantic_issues)
            )
        # The report is durably replaced before the state machine advertises
        # SEALED. The final manifest then binds the exact report bytes.
        atomic_write_json(run.root / "sealed_report.json", sealed)
        run.artifact_hashes["sealed_report"] = sha256_file(run.root / "sealed_report.json")
        run.transition(RunState.SEALED)
        run.save()
        # Evolution is deliberately a post-run extension. Its bytes cannot be
        # part of the sealed hash set without creating a report/hash cycle, and
        # it has no authority to mutate the Experience Library.
        write_candidate_bundle(run.root, schema_root=paths.root / "schemas")
        return run.root
    except BaseException as exc:
        # A deterministic Evolution failure happens only after the durable
        # SEALED transition. Preserve that classification and its sealed
        # artifacts; do not manufacture a contradictory failure report.
        if run.state == RunState.SEALED:
            raise

        # Repair normally persists its ledger in a per-round finally block.
        # Retry at the outer failure boundary in case a provider exception or
        # another failure interrupted the inner persistence path. This must
        # never replace the original exception.
        if generation is not None:
            try:
                _persist_repair_ledger(generation=generation, run=run)
            except BaseException:
                pass

        # Preserve an auditable index for every fully written video even when a
        # later validation, repair, Demo, or sealing step fails.  This is
        # deliberately best-effort: evidence collection must never replace the
        # original terminal cause (for example, a renderer failure that left an
        # incomplete file).
        if mode == "aws":
            try:
                video_index = _write_video_index(
                    paths=paths,
                    run=run,
                    mode=mode,
                    run_config=run_config,
                    strict=False,
                )
                run.artifact_hashes["video_index"] = sha256_file(
                    run.root / "video_index.json"
                )
            except BaseException as video_report_exc:
                video_index = None
                video_index_failure = type(video_report_exc).__name__
                run.artifact_hashes.pop("video_index", None)
                run.record(
                    "terminal_video_index_failed",
                    error_type=type(video_report_exc).__name__,
                )
        if generation is not None:
            if generation.stage1_result is not None:
                run.budgets["generation_stage1"] = dict(
                    generation.stage1_result.agent_turn_budget
                )
                if generation.stage2_result is not None:
                    run.budgets["generation_stage2_initial"] = dict(
                        generation.stage2_result.agent_turn_budget
                    )
            else:
                run.budgets["generation_stage1"] = generation.agent.budget.to_dict()
            run.budgets["repair"] = _repair_budget_state(generation, run_config)
        if generator is not None:
            run.budgets["validation_suite"] = generator.budget.to_dict()
        if demo_report is not None:
            run.budgets["demo"] = {
                "limit_per_task": int(run_config["demo"]["max_model_calls_per_task"]),
                "used_per_task": [task["model_calls"] for task in demo_report["tasks"]],
                "per_task": [task["agent_turn_budget"] for task in demo_report["tasks"]],
            }
        if run.state not in TERMINAL_STATES:
            try:
                preferred = (
                    RunState.INPUT_INVALID
                    if run.state in {RunState.INIT, RunState.INPUT_LIBRARIES_READY}
                    else RunState.GENERATION_FAILED
                    if run.state in {RunState.STAGE1_RUNNING, RunState.STAGE2_RUNNING}
                    else RunState.TOOL_PACKAGING_FAILED
                    if run.state == RunState.TOOL_PACKAGING
                    else RunState.INFRASTRUCTURE_FAILED
                )
                if preferred not in ALLOWED_TRANSITIONS.get(run.state, set()):
                    preferred = RunState.INFRASTRUCTURE_FAILED
                run.transition(preferred, reason=type(exc).__name__)
            except (ValueError, RuntimeError):
                run.record("unhandled_error", error_type=type(exc).__name__)
        try:
            terminal_model_accounting = _model_audit_report(
                generation_client, suite_client, demo_client
            )
            terminal_model_accounting["generation_phases"] = (
                _generation_phase_accounting(generation)
            )
            aggregate_accounting = terminal_model_accounting.get("aggregate", {})
            run.record(
                "terminal_accounting_finalized",
                budget_names=sorted(run.budgets),
                logical_requests=aggregate_accounting.get("logical_requests"),
                provider_http_attempts=aggregate_accounting.get(
                    "provider_http_attempts"
                ),
                provider_retries=aggregate_accounting.get("provider_retries"),
            )
            # ``record`` saves the final budget/accounting cut first.  The
            # snapshot is therefore identical to the current manifest until
            # terminal_report_written adds the deliberate report hash/event.
            atomic_write_json(
                run.root / "run_manifest_terminal_snapshot.json",
                load_json(run.path),
            )
            terminal_video_recording: Mapping[str, Any]
            if video_index is not None:
                terminal_video_recording = video_index
            else:
                terminal_video_recording = {
                    "schema_version": "robot_capability.mujoco_video_status.v1",
                    "required": mode == "aws",
                    "status": "error" if video_index_failure else "unavailable",
                    "reason_code": (
                        f"video_index_{video_index_failure}"
                        if video_index_failure
                        else "video_index_not_created"
                    ),
                }
            terminal = {
                "schema_version": "robot_capability.terminal_run_report.v2",
                "run_id": run.run_id,
                "terminal_state": run.state.value,
                "terminal_reason": run.terminal_reason or type(exc).__name__,
                "mode": mode,
                "ended_at": utc_now(),
                "error": {"type": type(exc).__name__},
                "input_hashes": run.input_hashes,
                "artifact_hashes": run.artifact_hashes,
                "budgets": run.budgets,
                "model_accounting": terminal_model_accounting,
                "repair": {
                    **_repair_budget_state(generation, run_config),
                    "history": repair_history,
                },
                "video_recording": terminal_video_recording,
                "evidence_files": _existing_run_files(run),
            }
            terminal_issues = validate_json_schema(
                terminal,
                load_json(paths.root / "schemas/terminal_run_report.schema.json"),
                instance_path="$terminal",
            )
            if terminal_issues:
                raise PipelineError("terminal report failed runtime schema validation")
            terminal_semantic_issues = terminal_report_semantic_errors(
                terminal, run_root=run.root
            )
            if terminal_semantic_issues:
                raise PipelineError(
                    "terminal report failed semantic audit: "
                    + "; ".join(terminal_semantic_issues)
                )
            atomic_write_json(run.root / "terminal_report.json", terminal)
            run.artifact_hashes["terminal_report"] = sha256_file(
                run.root / "terminal_report.json"
            )
            run.record(
                "terminal_report_written",
                report_sha256=run.artifact_hashes["terminal_report"],
            )
        except BaseException as report_exc:
            run.record(
                "terminal_report_failed",
                error_type=type(report_exc).__name__,
            )
        # Failed-run Evolution is best-effort and strictly post-terminal. A
        # malformed candidate or unwritable evolution directory must not mask
        # the primary provider, validation, renderer, or Demo failure.
        try:
            write_candidate_bundle(run.root, schema_root=paths.root / "schemas")
        except BaseException:
            pass
        raise
