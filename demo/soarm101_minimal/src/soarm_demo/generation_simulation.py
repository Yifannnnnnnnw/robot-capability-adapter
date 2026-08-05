"""Public, bounded simulation feedback for the continuous Generation Agent.

This module is deliberately *not* a validation harness.  It lets Generation
inspect one resolver-frozen, synthetic public scene and, after the ordinary
authoritative static gate passes, execute a generated capability in a fresh
worker process.  The worker returns only an allowlisted projection of public
control measurements.  Validation suites, Demo instances, contacts, raw
``MjData`` and oracle predicates never cross this boundary.
"""

from __future__ import annotations

import copy
import io
import json
import math
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .audit import atomic_write_json, canonical_json, sha256_file, sha256_json
from .schema_validation import validate_json_schema
from .static_validation import validate_generated_package
from .tool_packager import package_tree_sha256


class GenerationSimulationError(RuntimeError):
    """Fail-closed error at the public Generation simulation boundary."""


class _ProbeLimitExceeded(BaseException):
    """A worker resource limit that generated ``except Exception`` cannot hide."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_ID = re.compile(r"^G[123]\.[a-z][a-z0-9_]*$")
_SAFE_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_FORBIDDEN_PUBLIC_KEY_PARTS = (
    "contact",
    "heldout",
    "oracle",
    "private",
    "threshold",
    "validation_case",
    "validation_suite",
)
_JOINT_KEYS = (
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
)
_PROFILE_BY_EFFECT = {
    "joint_targets": "public_joint_hold",
    "cartesian_target": "public_cartesian_hold",
    "object_source_to_target": "public_object_relocation",
    "object_source_plus_height_delta": "public_object_lift",
    "object_move_sequence": "public_object_sequence",
}

def _strict_json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value!r}")

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise GenerationSimulationError("generation environment freeze is not an object")
    return value


def _safe_deepcopy(value: Any) -> Any:
    """Round-trip finite JSON so no resolver-owned object remains mutable."""

    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise GenerationSimulationError(
            "generation environment contains non-finite or non-JSON data"
        ) from exc


def _walk_public_keys(value: Any, *, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_PUBLIC_KEY_PARTS):
                issues.append(f"{path}.{key}")
            issues.extend(_walk_public_keys(item, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_walk_public_keys(item, path=f"{path}[{index}]"))
    return issues


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationSimulationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GenerationSimulationError(f"{label} must be finite")
    return result


def _vector3(value: Any, *, label: str) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise GenerationSimulationError(f"{label} must contain three numbers")
    return [_finite_number(item, label=f"{label}[{index}]") for index, item in enumerate(value)]


def _source_binding_path(
    binding: Any,
    *,
    label: str,
    libraries_root: Path,
) -> Path:
    if not isinstance(binding, Mapping):
        raise GenerationSimulationError(f"environment freeze lacks {label} binding")
    raw_path = binding.get("path")
    digest = binding.get("sha256")
    if not isinstance(raw_path, str) or not raw_path or not isinstance(digest, str):
        raise GenerationSimulationError(f"environment freeze has malformed {label} binding")
    if _SHA256.fullmatch(digest) is None:
        raise GenerationSimulationError(f"environment freeze has malformed {label} hash")
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or "\\" in raw_path:
        raise GenerationSimulationError(
            f"environment freeze {label} path is not a safe library-relative path"
        )
    candidate = libraries_root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(libraries_root)
    except ValueError as exc:
        raise GenerationSimulationError(
            f"environment freeze {label} path escapes the libraries root"
        ) from exc
    if candidate.is_symlink() or not resolved.is_file() or sha256_file(resolved) != digest:
        raise GenerationSimulationError(f"environment freeze {label} binding drifted")
    return resolved


def _verify_environment_freeze(
    path: Path,
    *,
    expected_sha256: str | None,
    libraries_root: Path,
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    """Use the resolver verifier and retain an in-memory exact hash pin."""

    try:
        from .environment_resolver import verify_generation_environment_freeze
    except ImportError as exc:  # pragma: no cover - integration ordering guard
        raise GenerationSimulationError(
            "generation environment resolver is unavailable"
        ) from exc

    try:
        verify_generation_environment_freeze(
            path,
            expected_sha256=expected_sha256,
            libraries_root=libraries_root,
            rehash_sources=True,
        )
    except BaseException as exc:
        raise GenerationSimulationError(
            "generation environment freeze verification failed"
        ) from exc
    freeze = _strict_json(path)
    manifest = freeze.get("manifest")
    detached_digest = freeze.get("manifest_sha256")
    if not isinstance(manifest, Mapping) or not isinstance(detached_digest, str):
        raise GenerationSimulationError(
            "generation environment freeze lacks manifest binding"
        )
    document = _safe_deepcopy(manifest)
    if (
        _SHA256.fullmatch(detached_digest) is None
        or sha256_json(document) != detached_digest
    ):
        raise GenerationSimulationError(
            "generation environment detached manifest hash is invalid"
        )
    file_digest = sha256_file(path)
    if expected_sha256 is not None and file_digest != expected_sha256:
        raise GenerationSimulationError("generation environment freeze file hash drifted")
    return document, file_digest, detached_digest, _safe_deepcopy(freeze)


def _public_simulation(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = manifest.get("public_simulation")
    if not isinstance(value, Mapping):
        raise GenerationSimulationError("environment freeze lacks public_simulation")
    result = _safe_deepcopy(value)
    forbidden = _walk_public_keys(result)
    if forbidden:
        # Do not echo sensitive field names or paths into the ReAct trace.
        raise GenerationSimulationError(
            "public simulation projection contains forbidden evaluator data"
        )
    required = {"common_reset", "smoke_instance", "probe_inputs"}
    if set(result) < required:
        raise GenerationSimulationError("public simulation projection is incomplete")
    instance = result["smoke_instance"]
    if not isinstance(instance, Mapping):
        raise GenerationSimulationError("public smoke instance must be an object")
    if any(key in instance for key in ("task_id", "seed", "language", "goal")):
        raise GenerationSimulationError(
            "public smoke instance must not be a task or benchmark instance"
        )
    if (
        result.get("privacy_class") != "public_generation_sandbox"
        or result.get("not_task_instance") is not True
    ):
        raise GenerationSimulationError(
            "public smoke instance lacks synthetic non-task provenance"
        )
    return result


def _morphology_binding(
    manifest: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    bindings = manifest.get("bindings")
    morphology = bindings.get("morphology") if isinstance(bindings, Mapping) else None
    if not isinstance(morphology, Mapping):
        raise GenerationSimulationError("environment freeze lacks morphology bindings")
    binding = morphology.get(name)
    if not isinstance(binding, Mapping):
        raise GenerationSimulationError(f"environment freeze lacks {name} binding")
    return binding


@dataclass(frozen=True)
class ProbeLimits:
    max_calls: int = 12
    worker_startup_timeout_s: float = 10.0
    wall_timeout_s: float = 12.0
    max_total_simulation_s: float = 90.0
    max_simulation_s_per_call: float = 15.0
    max_runtime_calls_per_probe: int = 2_000
    max_worker_memory_mb: int = 1_536

    def __post_init__(self) -> None:
        integral = {
            "max_calls": self.max_calls,
            "max_runtime_calls_per_probe": self.max_runtime_calls_per_probe,
            "max_worker_memory_mb": self.max_worker_memory_mb,
        }
        for name, value in integral.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (
            ("worker_startup_timeout_s", self.worker_startup_timeout_s),
            ("wall_timeout_s", self.wall_timeout_s),
            ("max_total_simulation_s", self.max_total_simulation_s),
            ("max_simulation_s_per_call", self.max_simulation_s_per_call),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.worker_startup_timeout_s + self.wall_timeout_s > 22.0:
            raise ValueError(
                "worker startup plus generated execution must leave cleanup "
                "room inside the 25-second tool deadline"
            )
        if self.max_simulation_s_per_call > self.max_total_simulation_s:
            raise ValueError("per-call simulation allowance exceeds total allowance")


class GenerationSimulationSandbox:
    """One immutable public environment epoch shared by Stage 1/2/repair."""

    def __init__(
        self,
        *,
        mode: str,
        environment_freeze_path: str | Path,
        libraries_root: str | Path,
        expected_environment_freeze_sha256: str | None,
        run_root: str | Path,
        limits: ProbeLimits | None = None,
    ) -> None:
        if mode not in {"aws", "offline"}:
            raise ValueError("Generation simulation mode must be aws or offline")
        self.mode = mode
        self.environment_freeze_path = Path(environment_freeze_path).resolve()
        self.libraries_root = Path(libraries_root).resolve()
        if not self.libraries_root.is_dir():
            raise GenerationSimulationError("libraries_root is not a directory")
        self.run_root = Path(run_root).resolve()
        self.audit_root = self.run_root / "generation" / "simulation"
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or ProbeLimits()
        (
            manifest,
            freeze_file_sha256,
            manifest_sha256,
            freeze_envelope,
        ) = _verify_environment_freeze(
            self.environment_freeze_path,
            expected_sha256=expected_environment_freeze_sha256,
            libraries_root=self.libraries_root,
        )
        self._manifest = manifest
        self._freeze_file_sha256 = freeze_file_sha256
        self._manifest_sha256 = manifest_sha256
        self._freeze_envelope = freeze_envelope
        self._public = _public_simulation(manifest)
        self._robot_model_path = _source_binding_path(
            _morphology_binding(manifest, "robot_model"),
            label="robot_model",
            libraries_root=self.libraries_root,
        )
        self._scene_definition_path = _source_binding_path(
            _morphology_binding(manifest, "scene_definition"),
            label="scene_definition",
            libraries_root=self.libraries_root,
        )
        self._scene_catalog_path = _source_binding_path(
            _morphology_binding(manifest, "scene_asset_catalog"),
            label="scene_asset_catalog",
            libraries_root=self.libraries_root,
        )
        self._probe_calls = 0
        self._simulation_seconds = 0.0
        self._records: list[dict[str, Any]] = []
        self._prepared_inspection = self._inspect_catalog_without_world()
        self._prepared = True
        self._persist_accounting()

    @classmethod
    def prepare(
        cls,
        *,
        mode: str,
        environment_freeze_path: str | Path,
        libraries_root: str | Path,
        run_root: str | Path,
        expected_environment_freeze_sha256: str | None = None,
        limits: ProbeLimits | None = None,
    ) -> "GenerationSimulationSandbox":
        """Resolve and inspect catalog references without creating a world."""

        return cls(
            mode=mode,
            environment_freeze_path=environment_freeze_path,
            libraries_root=libraries_root,
            expected_environment_freeze_sha256=expected_environment_freeze_sha256,
            run_root=run_root,
            limits=limits,
        )

    @property
    def prepared(self) -> bool:
        return self._prepared

    @property
    def environment_freeze_sha256(self) -> str:
        return self._freeze_file_sha256

    def _verify_integrity(self) -> None:
        if sha256_file(self.environment_freeze_path) != self._freeze_file_sha256:
            raise GenerationSimulationError("generation environment freeze file drifted")
        manifest, digest, manifest_digest, freeze_envelope = _verify_environment_freeze(
            self.environment_freeze_path,
            expected_sha256=self._freeze_file_sha256,
            libraries_root=self.libraries_root,
        )
        if (
            digest != self._freeze_file_sha256
            or manifest_digest != self._manifest_sha256
            or canonical_json(manifest) != canonical_json(self._manifest)
            or canonical_json(freeze_envelope) != canonical_json(self._freeze_envelope)
        ):
            raise GenerationSimulationError("generation environment freeze content drifted")
        for name in ("robot_model", "scene_definition", "scene_asset_catalog"):
            _source_binding_path(
                _morphology_binding(manifest, name),
                label=name,
                libraries_root=self.libraries_root,
            )

    def assert_ready(self) -> None:
        if not self._prepared:
            raise GenerationSimulationError("generation simulation sandbox is not prepared")
        self._verify_integrity()

    def _inspect_catalog_without_world(self) -> dict[str, Any]:
        """Check the public asset request without importing or constructing MuJoCo."""

        try:
            catalog = yaml.safe_load(self._scene_catalog_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise GenerationSimulationError("public scene catalog cannot be read") from exc
        if not isinstance(catalog, Mapping) or not isinstance(catalog.get("assets"), list):
            raise GenerationSimulationError("public scene catalog is malformed")
        catalog_version = catalog.get("version")
        roles_by_ref: dict[str, str] = {}
        for raw_asset in catalog["assets"]:
            if not isinstance(raw_asset, Mapping):
                raise GenerationSimulationError("public scene catalog asset is malformed")
            asset_id = raw_asset.get("asset_id")
            role = raw_asset.get("role")
            variants = raw_asset.get("variants")
            if (
                not isinstance(asset_id, str)
                or not isinstance(role, str)
                or not isinstance(catalog_version, str)
                or not isinstance(variants, list)
            ):
                raise GenerationSimulationError("public scene catalog asset is malformed")
            for variant in variants:
                variant_id = variant.get("variant_id") if isinstance(variant, Mapping) else None
                if not isinstance(variant_id, str):
                    raise GenerationSimulationError("public scene catalog variant is malformed")
                roles_by_ref[
                    f"morphology.scene_asset/{asset_id}@{catalog_version}#{variant_id}"
                ] = role

        selected: list[str] = []

        def select(spec: Any, *, allowed: frozenset[str], expected_roles: set[str]) -> str:
            if not isinstance(spec, Mapping) or not set(spec) <= allowed:
                raise GenerationSimulationError(
                    "public scene instance must contain only asset_ref, id, and pose"
                )
            ref = spec.get("asset_ref")
            if not isinstance(ref, str) or roles_by_ref.get(ref) not in expected_roles:
                raise GenerationSimulationError("public scene selects a missing or invalid asset")
            selected.append(ref)
            return str(roles_by_ref[ref])

        common = self._public["common_reset"]
        table = common.get("table") if isinstance(common, Mapping) else None
        select(
            table,
            allowed=frozenset({"asset_ref", "center_m"}),
            expected_roles={"support_surface"},
        )
        smoke = self._public["smoke_instance"]
        role_counts = {"dynamic_object": 0, "receptacle": 0, "marker": 0}
        for spec in smoke.get("bodies", []):
            role = select(
                spec,
                allowed=frozenset(
                    {"asset_ref", "id", "position_m", "center_m", "quaternion_wxyz"}
                ),
                expected_roles={"dynamic_object", "receptacle"},
            )
            role_counts[role] += 1
        for spec in smoke.get("markers", []):
            role = select(
                spec,
                allowed=frozenset(
                    {"asset_ref", "id", "position_m", "center_m", "approach_vector"}
                ),
                expected_roles={"marker"},
            )
            role_counts[role] += 1

        inspection = {
            "schema_version": "robot_capability.generation_public_simulation_inspection.v1",
            "evidence_class": "static_catalog_inspection_no_world",
            "physical_runtime": False,
            "configured_probe_runtime": (
                "actual_mujoco_public_smoke_scene"
                if self.mode == "aws"
                else "deterministic_non_physical_fixture"
            ),
            "worlds_created": 0,
            "environment_freeze_sha256": self._freeze_file_sha256,
            "robot_model_sha256": sha256_file(self._robot_model_path),
            "scene_revision_sha256": self._manifest_sha256,
            "scene_summary": {
                "dynamic_object_count": role_counts["dynamic_object"],
                "receptacle_count": role_counts["receptacle"],
                "marker_count": role_counts["marker"],
                "selected_asset_refs": sorted(selected),
                "selected_asset_instance_count": len(selected),
                "catalog_asset_variant_count": len(roles_by_ref),
                "unreferenced_asset_variants_materialized": 0,
            },
            "runtime_surface": ["get_observation", "send_action"],
            "feedback_scope": {
                "public_synthetic_scene_only": True,
                "not_validation": True,
                "cannot_certify_validation_a_or_b": True,
                "contains_oracle_or_contact_data": False,
            },
            "probe_budget": self._budget_projection(),
        }
        atomic_write_json(self.audit_root / "catalog_inspection.json", inspection)
        return inspection

    def inspect_environment(self, _: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del _
        self.assert_ready()
        result = copy.deepcopy(self._prepared_inspection)
        result["probe_budget"] = self._budget_projection()
        return result

    def framework_preflight_inputs(self) -> dict[str, Any]:
        """Return the trusted public scene inputs for framework consumers.

        This method is never registered as a ReAct tool.  It exposes the
        already-verified freeze/catalog pair so Validation B and Demo can build
        only their actual case/task worlds from the same selected inputs.
        """

        self.assert_ready()
        settle = self._public["common_reset"].get("settle_before_probe_s", 0.5)
        settle_s = _finite_number(settle, label="settle_before_probe_s")
        if not 0.0 <= settle_s <= 2.0:
            raise GenerationSimulationError(
                "public preflight settle time is outside the bounded range"
            )
        return {
            "model_path": self._robot_model_path,
            "common_reset": copy.deepcopy(self._public["common_reset"]),
            "initial_state": copy.deepcopy(self._public["smoke_instance"]),
            "scene_catalog": self._scene_catalog_path,
            "scene_freeze": copy.deepcopy(self._freeze_envelope),
            "environment_freeze_sha256": self._freeze_file_sha256,
            "scene_revision_sha256": self._manifest_sha256,
            "scene_catalog_sha256": sha256_file(self._scene_catalog_path),
            "reset_settle_s": settle_s,
        }

    def continuity_checkpoint(self) -> dict[str, Any]:
        """Bounded safe state for a repair context epoch."""

        self.assert_ready()
        return {
            "schema_version": "robot_capability.generation_simulation_continuity.v1",
            "environment_freeze_sha256": self._freeze_file_sha256,
            "same_frozen_environment_epoch": True,
            "evidence_class": self._prepared_inspection["evidence_class"],
            "probe_budget": self._budget_projection(),
            "recent_probe_signatures": [
                {
                    "sequence": record["sequence"],
                    "capability_id": record["capability_id"],
                    "package_sha256": record["package_sha256"],
                    "outcome": record["outcome"],
                    "record_sha256": record["record_sha256"],
                }
                for record in self._records[-6:]
            ],
        }

    def _budget_projection(self) -> dict[str, Any]:
        return {
            "calls": {
                "limit": self.limits.max_calls,
                "used": self._probe_calls,
                "remaining": max(0, self.limits.max_calls - self._probe_calls),
            },
            "simulation_seconds": {
                "limit": self.limits.max_total_simulation_s,
                "used": round(self._simulation_seconds, 9),
                "remaining": round(
                    max(0.0, self.limits.max_total_simulation_s - self._simulation_seconds),
                    9,
                ),
            },
            "accounting_scope": "local_generation_probe_not_llm_budget",
            "worker_wall_time_seconds": {
                "trusted_startup_limit": self.limits.worker_startup_timeout_s,
                "generated_execution_limit": self.limits.wall_timeout_s,
                "combined_limit": (
                    self.limits.worker_startup_timeout_s
                    + self.limits.wall_timeout_s
                ),
            },
        }

    def _persist_accounting(self) -> None:
        document = {
            "schema_version": "robot_capability.generation_simulation_accounting.v1",
            "environment_freeze_sha256": self._freeze_file_sha256,
            "mode": self.mode,
            "evidence_class": "static_catalog_inspection_no_world",
            "budget": self._budget_projection(),
            "probe_records": copy.deepcopy(self._records),
            "validation_claim": "none",
        }
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "schemas/generation_simulation_accounting.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        issues = validate_json_schema(
            document,
            schema,
            instance_path="$generation_simulation_accounting",
        )
        if issues:
            raise GenerationSimulationError(
                "generation simulation accounting schema validation failed: "
                + "; ".join(
                    f"{issue.path}: {issue.message}" for issue in issues
                )
            )
        atomic_write_json(self.audit_root / "accounting.json", document)

    def probe_generated_capability(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one fixed public profile after an authoritative static pass."""

        capability_id = arguments.get("capability_id")
        profile_id = arguments.get("profile_id")
        if not isinstance(capability_id, str) or _CAPABILITY_ID.fullmatch(capability_id) is None:
            return self._blocked_result("INVALID_PUBLIC_CAPABILITY_ID", capability_id=None)
        if self._probe_calls >= self.limits.max_calls:
            return self._blocked_result("PUBLIC_PROBE_CALL_BUDGET_EXHAUSTED", capability_id)
        remaining_sim = self.limits.max_total_simulation_s - self._simulation_seconds
        if remaining_sim <= 0.0:
            return self._blocked_result("PUBLIC_PROBE_SIMULATION_BUDGET_EXHAUSTED", capability_id)

        self._probe_calls += 1
        sequence = self._probe_calls
        package_root = Path(arguments.get("package_root", self.run_root / "generated_package"))
        # The ReAct tool schema never exposes package_root.  Reject ambient or
        # manually supplied paths even if this method is called directly.
        expected_root = (self.run_root / "generated_package").resolve()
        if package_root.resolve() != expected_root:
            result = self._blocked_result("PACKAGE_ROOT_OUTSIDE_GENERATION_WORKSPACE", capability_id)
            self._persist_accounting()
            return result
        try:
            self.assert_ready()
            package_before = package_tree_sha256(expected_root)
            static_report = validate_generated_package(
                expected_root,
                arguments["frozen_stage1"],
            )
        except BaseException:
            result = self._blocked_result("PUBLIC_PROBE_PRECONDITION_FAILED", capability_id)
            self._persist_accounting()
            return result
        if not static_report.passed:
            result = self._blocked_result(
                "AUTHORITATIVE_STATIC_PASS_REQUIRED",
                capability_id,
                package_sha256=package_before,
            )
            self._record_probe(sequence, capability_id, package_before, result)
            return result

        try:
            manifest = _strict_json(expected_root / "package_manifest.json")
            capability = next(
                item
                for item in manifest["capabilities"]
                if isinstance(item, Mapping) and item.get("capability_id") == capability_id
            )
            binding = capability.get("validation_binding")
            effect = binding.get("effect") if isinstance(binding, Mapping) else None
            expected_profile = _PROFILE_BY_EFFECT.get(effect)
            if profile_id != expected_profile:
                raise GenerationSimulationError("public smoke profile does not match capability")
            call_arguments = _public_call_arguments(
                capability,
                self._public["probe_inputs"],
                max_timeout_s=min(
                    self.limits.max_simulation_s_per_call,
                    remaining_sim,
                ),
            )
        except (StopIteration, KeyError, TypeError, GenerationSimulationError):
            result = self._blocked_result(
                "NO_FIXED_PUBLIC_SMOKE_PROFILE_FOR_CAPABILITY",
                capability_id,
                package_sha256=package_before,
            )
            self._record_probe(sequence, capability_id, package_before, result)
            return result

        # Execute a byte-for-byte private snapshot, never the model-writable
        # workspace tree. Re-hash and re-run the authoritative static gate on
        # the copy to close the validate/copy/import TOCTOU window.
        snapshot_parent = Path(
            tempfile.mkdtemp(prefix="package-snapshot-", dir=self.audit_root)
        )
        execution_root = snapshot_parent / "generated_package"
        try:
            shutil.copytree(expected_root, execution_root, symlinks=True)
            if package_tree_sha256(execution_root) != package_before:
                result = self._blocked_result(
                    "PACKAGE_DRIFTED_DURING_PROBE_SNAPSHOT",
                    capability_id,
                    package_sha256=package_before,
                )
                self._record_probe(sequence, capability_id, package_before, result)
                return result
            copied_static = validate_generated_package(
                execution_root,
                arguments["frozen_stage1"],
            )
            if not copied_static.passed:
                result = self._blocked_result(
                    "PROBE_SNAPSHOT_STATIC_RECHECK_FAILED",
                    capability_id,
                    package_sha256=package_before,
                )
                self._record_probe(sequence, capability_id, package_before, result)
                return result
            payload = {
                "mode": self.mode,
                "robot_model_path": str(self._robot_model_path),
                "robot_model_sha256": sha256_file(self._robot_model_path),
                "scene_catalog_path": str(self._scene_catalog_path),
                "scene_catalog_sha256": sha256_file(self._scene_catalog_path),
                "scene_freeze": copy.deepcopy(self._freeze_envelope),
                "common_reset": copy.deepcopy(self._public["common_reset"]),
                "smoke_instance": copy.deepcopy(self._public["smoke_instance"]),
                "probe_inputs": copy.deepcopy(self._public["probe_inputs"]),
                "package_root": str(execution_root),
                "package_sha256": package_before,
                "module": capability["module"],
                "function_name": capability["function_name"],
                "call_arguments": call_arguments,
                "declared_status_values": list(
                    capability.get("result_contract", {}).get("status_values", [])
                ),
                "max_simulation_s": min(
                    self.limits.max_simulation_s_per_call,
                    remaining_sim,
                ),
                "max_runtime_calls": self.limits.max_runtime_calls_per_probe,
                "max_worker_memory_mb": self.limits.max_worker_memory_mb,
                "worker_startup_timeout_s": self.limits.worker_startup_timeout_s,
                "wall_timeout_s": self.limits.wall_timeout_s,
            }
            worker = _run_probe_worker(
                payload,
                startup_timeout_s=self.limits.worker_startup_timeout_s,
                execution_timeout_s=self.limits.wall_timeout_s,
            )
        finally:
            shutil.rmtree(snapshot_parent, ignore_errors=True)
        try:
            package_after = package_tree_sha256(expected_root)
            self.assert_ready()
        except BaseException:
            worker = {"outcome": "integrity_failure", "simulation_seconds": 0.0}
            package_after = ""
        if package_after != package_before:
            worker = {"outcome": "package_mutated", "simulation_seconds": 0.0}

        infrastructure_timing = copy.deepcopy(
            worker.pop("_infrastructure_timing", {})
        )
        simulation_seconds = worker.get("simulation_seconds", 0.0)
        if (
            isinstance(simulation_seconds, (int, float))
            and not isinstance(simulation_seconds, bool)
            and math.isfinite(float(simulation_seconds))
            and 0.0 <= float(simulation_seconds) <= self.limits.max_simulation_s_per_call + 1e-9
        ):
            self._simulation_seconds += float(simulation_seconds)
        result = {
            "schema_version": "robot_capability.generation_public_probe_result.v1",
            "classification": "public_generation_smoke_observation_not_validation",
            "capability_id": capability_id,
            "profile_id": profile_id,
            "outcome": str(worker.get("outcome", "worker_failed")),
            "environment_freeze_sha256": self._freeze_file_sha256,
            "scene_revision_sha256": self._prepared_inspection["scene_revision_sha256"],
            "package_sha256": package_before,
            "runtime_evidence": self._prepared_inspection["configured_probe_runtime"],
            "measurements": copy.deepcopy(worker.get("measurements", {})),
            "generated_status": worker.get("generated_status"),
            "simulation_seconds": round(float(simulation_seconds or 0.0), 9),
            "probe_budget": self._budget_projection(),
            "certification": {
                "validation_a": False,
                "validation_b": False,
                "demo": False,
                "seal": False,
            },
        }
        self._record_probe(
            sequence,
            capability_id,
            package_before,
            result,
            infrastructure_timing=infrastructure_timing,
        )
        return result

    def _blocked_result(
        self,
        code: str,
        capability_id: str | None,
        *,
        package_sha256: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "robot_capability.generation_public_probe_result.v1",
            "classification": "public_generation_smoke_observation_not_validation",
            "capability_id": capability_id,
            "outcome": "blocked",
            "code": code,
            "environment_freeze_sha256": self._freeze_file_sha256,
            "package_sha256": package_sha256,
            "measurements": {},
            "generated_status": None,
            "simulation_seconds": 0.0,
            "probe_budget": self._budget_projection(),
            "certification": {
                "validation_a": False,
                "validation_b": False,
                "demo": False,
                "seal": False,
            },
        }

    def _record_probe(
        self,
        sequence: int,
        capability_id: str,
        package_sha256: str,
        result: Mapping[str, Any],
        *,
        infrastructure_timing: Mapping[str, Any] | None = None,
    ) -> None:
        record = {
            "sequence": sequence,
            "capability_id": capability_id,
            "package_sha256": package_sha256,
            "environment_freeze_sha256": self._freeze_file_sha256,
            "outcome": result.get("outcome"),
            "result_sha256": sha256_json(result),
            "infrastructure_timing": copy.deepcopy(
                dict(infrastructure_timing or {})
            ),
        }
        record["record_sha256"] = sha256_json(record)
        self._records.append(record)
        self._persist_accounting()

def _public_call_arguments(
    capability: Mapping[str, Any],
    probe_inputs: Any,
    *,
    max_timeout_s: float,
) -> dict[str, Any]:
    if not isinstance(probe_inputs, Mapping):
        raise GenerationSimulationError("public probe_inputs must be an object")
    binding = capability.get("validation_binding")
    signature = capability.get("signature")
    if not isinstance(binding, Mapping) or not isinstance(signature, Mapping):
        raise GenerationSimulationError("capability lacks public binding/signature")
    effect = binding.get("effect")
    output: dict[str, Any] = {}

    def assign_vector(binding_name: str, profile_name: str) -> None:
        name = binding.get(binding_name)
        if not isinstance(name, str):
            raise GenerationSimulationError("public vector binding is absent")
        output[name] = _vector3(probe_inputs.get(profile_name), label=profile_name)

    def assign_components(binding_name: str, profile_name: str) -> None:
        names = binding.get(binding_name)
        vector = _vector3(probe_inputs.get(profile_name), label=profile_name)
        if not isinstance(names, Mapping):
            raise GenerationSimulationError("public component binding is absent")
        expected = ("x", "y", "z") if set(names) == {"x", "y", "z"} else ("x", "y")
        for index, component in enumerate(expected):
            name = names.get(component)
            if not isinstance(name, str):
                raise GenerationSimulationError("public component binding is malformed")
            output[name] = vector[index]

    if effect == "joint_targets":
        targets = probe_inputs.get("joint_targets")
        if not isinstance(targets, Mapping):
            raise GenerationSimulationError("joint smoke profile is absent")
        clean_targets = {
            str(key): _finite_number(value, label=f"joint_targets.{key}")
            for key, value in targets.items()
        }
        if set(clean_targets) - set(_JOINT_KEYS):
            raise GenerationSimulationError("joint smoke profile has an unknown key")
        if "joint_targets_argument" in binding:
            output[str(binding["joint_targets_argument"])] = clean_targets
        else:
            names = binding.get("joint_target_arguments")
            if not isinstance(names, Mapping):
                raise GenerationSimulationError("joint target binding is absent")
            for key, name in names.items():
                normalized = str(key) if str(key).endswith(".pos") else f"{key}.pos"
                if normalized not in clean_targets or not isinstance(name, str):
                    raise GenerationSimulationError("joint scalar binding is unsupported")
                output[name] = clean_targets[normalized]
    elif effect == "cartesian_target":
        if "target_argument" in binding:
            assign_vector("target_argument", "cartesian_target_m")
        else:
            assign_components("target_arguments", "cartesian_target_m")
    elif effect == "object_source_to_target":
        if "source_argument" in binding:
            assign_vector("source_argument", "object_source_m")
        else:
            assign_components("source_arguments", "object_source_m")
        if "target_argument" in binding:
            assign_vector("target_argument", "object_target_m")
        else:
            assign_components("target_arguments", "object_target_m")
        extent_name = binding.get("object_extent_argument")
        if isinstance(extent_name, str):
            output[extent_name] = _finite_number(
                probe_inputs.get("object_extent_m"), label="object_extent_m"
            )
    elif effect == "object_source_plus_height_delta":
        if "source_argument" in binding:
            assign_vector("source_argument", "object_source_m")
        else:
            assign_components("source_arguments", "object_source_m")
        height_name = binding.get("height_delta_argument")
        if not isinstance(height_name, str):
            raise GenerationSimulationError("height-delta binding is absent")
        output[height_name] = _finite_number(
            probe_inputs.get("height_delta_m"), label="height_delta_m"
        )
        extent_name = binding.get("object_extent_argument")
        if isinstance(extent_name, str):
            output[extent_name] = _finite_number(
                probe_inputs.get("object_extent_m"), label="object_extent_m"
            )
    elif effect == "object_move_sequence":
        moves_name = binding.get("moves_argument")
        moves = probe_inputs.get("sequence_moves")
        if not isinstance(moves_name, str) or not isinstance(moves, list) or not moves:
            raise GenerationSimulationError("sequence smoke profile is absent")
        source_field = binding.get("source_field")
        target_field = binding.get("target_field")
        extent_field = binding.get("object_extent_field")
        if not all(isinstance(item, str) for item in (source_field, target_field, extent_field)):
            raise GenerationSimulationError("sequence binding is incomplete")
        output[moves_name] = [
            {
                str(source_field): _vector3(
                    move.get("object_source_m"), label="sequence.object_source_m"
                ),
                str(target_field): _vector3(
                    move.get("object_target_m"), label="sequence.object_target_m"
                ),
                str(extent_field): _finite_number(
                    move.get("object_extent_m"), label="sequence.object_extent_m"
                ),
            }
            for move in moves
            if isinstance(move, Mapping)
        ]
        if len(output[moves_name]) != len(moves):
            raise GenerationSimulationError("sequence smoke profile contains a malformed move")
    else:
        raise GenerationSimulationError("unsupported capability effect")

    parameters = signature.get("parameters")
    if not isinstance(parameters, list):
        raise GenerationSimulationError("public signature parameters are absent")
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            raise GenerationSimulationError("public signature parameter is malformed")
        name = parameter.get("name")
        if name == "runtime" or name in output or parameter.get("has_default") is True:
            continue
        if isinstance(name, str) and "timeout" in name.lower():
            output[name] = float(max_timeout_s)
            continue
        raise GenerationSimulationError("required parameter has no fixed public smoke input")
    return output


class _PublicRuntimeFacade:
    """Dynamic second boundary: generated code sees only two control methods."""

    __slots__ = ("_backend", "_runtime_calls", "_max_runtime_calls", "_action_count", "_observation_count")
    _PUBLIC = frozenset({"send_action", "get_observation"})

    def __init__(self, backend: Any, *, max_runtime_calls: int) -> None:
        object.__setattr__(self, "_backend", backend)
        object.__setattr__(self, "_runtime_calls", 0)
        object.__setattr__(self, "_max_runtime_calls", int(max_runtime_calls))
        object.__setattr__(self, "_action_count", 0)
        object.__setattr__(self, "_observation_count", 0)

    def __getattribute__(self, name: str) -> Any:
        if name not in _PublicRuntimeFacade._PUBLIC:
            raise AttributeError("public probe runtime exposes only send_action/get_observation")
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        del name, value
        raise AttributeError("public probe runtime is immutable")

    def _consume(self) -> None:
        calls = int(object.__getattribute__(self, "_runtime_calls")) + 1
        if calls > int(object.__getattribute__(self, "_max_runtime_calls")):
            raise _ProbeLimitExceeded("runtime call budget exceeded")
        object.__setattr__(self, "_runtime_calls", calls)

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        object.__getattribute__(self, "_consume")()
        if not isinstance(action, Mapping) or len(action) > len(_JOINT_KEYS):
            raise _ProbeLimitExceeded("action shape exceeds public runtime contract")
        clean = {
            str(key): float(value)
            for key, value in action.items()
            if str(key) in _JOINT_KEYS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        }
        if len(clean) != len(action):
            raise _ProbeLimitExceeded("action contains a non-public or non-finite value")
        object.__setattr__(
            self,
            "_action_count",
            int(object.__getattribute__(self, "_action_count")) + 1,
        )
        backend = object.__getattribute__(self, "_backend")
        return dict(backend.send_action(clean))

    def get_observation(self) -> dict[str, float]:
        object.__getattribute__(self, "_consume")()
        object.__setattr__(
            self,
            "_observation_count",
            int(object.__getattribute__(self, "_observation_count")) + 1,
        )
        backend = object.__getattribute__(self, "_backend")
        observation = backend.get_observation()
        return {key: float(observation[key]) for key in _JOINT_KEYS}


class _PublicSimulationClock:
    __slots__ = ("_backend", "_actual_mujoco", "_elapsed", "_maximum")

    def __init__(self, backend: Any, *, actual_mujoco: bool, maximum: float) -> None:
        self._backend = backend
        self._actual_mujoco = bool(actual_mujoco)
        self._elapsed = 0.0
        self._maximum = float(maximum)

    def monotonic(self) -> float:
        return float(self._elapsed)

    def sleep(self, seconds: float) -> None:
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0.0 or duration > 60.0:
            raise _ProbeLimitExceeded("invalid generated sleep duration")
        if self._elapsed + duration > self._maximum + 1e-12:
            raise _ProbeLimitExceeded("simulation-time budget exceeded")
        if self._actual_mujoco and duration:
            self._backend.advance(duration)
        self._elapsed += duration


def _set_worker_limits(payload: Mapping[str, Any]) -> None:
    """Best-effort OS limits supplement, never replace, AST/runtime isolation."""

    try:
        import resource

        memory_bytes = int(payload.get("max_worker_memory_mb", 1_536)) * 1024 * 1024
        # macOS native MuJoCo/Accelerate reserves a large sparse virtual range,
        # so RLIMIT_AS/DATA can kill a healthy worker before its resident use
        # approaches the cap.  RLIMIT_RSS is the appropriate resident-memory
        # guard there; Linux uses the enforceable address-space guard.
        memory_limit_names = (
            ("RLIMIT_RSS",) if sys.platform == "darwin" else ("RLIMIT_AS",)
        )
        for name in memory_limit_names:
            limit = getattr(resource, name, None)
            if limit is not None:
                try:
                    resource.setrlimit(limit, (memory_bytes, memory_bytes))
                except (OSError, ValueError):
                    pass
        cpu_limit = max(
            1,
            int(
                math.ceil(
                    float(payload.get("worker_startup_timeout_s", 10.0))
                    + float(payload.get("wall_timeout_s", 12.0))
                )
            ),
        )
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        except (OSError, ValueError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
        except (OSError, ValueError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except (OSError, ValueError):
            pass
    except ImportError:  # pragma: no cover - non-POSIX portability
        pass


def _backend_snapshot(backend: Any, *, actual_mujoco: bool) -> dict[str, Any]:
    if actual_mujoco:
        snapshot = backend.world_snapshot()
        return {
            "joint_positions": dict(snapshot["joint_positions"]),
            "end_effector_position_m": list(snapshot["end_effector_position_m"]),
            "object_positions_m": {
                str(name): list(value["position_m"])
                for name, value in snapshot["objects"].items()
            },
            "finite_state": bool(snapshot["finite_state"]),
            "model_counts": {
                "nq": int(backend.model.nq),
                "nv": int(backend.model.nv),
                "nbody": int(backend.model.nbody),
                "ngeom": int(backend.model.ngeom),
            },
        }
    snapshot = backend.snapshot()
    return {
        "joint_positions": dict(snapshot["joint_positions"]),
        "end_effector_position_m": list(snapshot["end_effector_position_m"]),
        "object_positions_m": {
            str(name): list(value)
            for name, value in snapshot["object_positions_m"].items()
        },
        "finite_state": True,
        "model_counts": None,
    }


def _sanitized_measurements(
    initial: Mapping[str, Any],
    final: Mapping[str, Any],
    *,
    facade: _PublicRuntimeFacade,
) -> dict[str, Any]:
    def rounded_vector(value: Sequence[Any]) -> list[float]:
        return [round(float(item), 6) for item in value]

    initial_objects = initial.get("object_positions_m", {})
    final_objects = final.get("object_positions_m", {})
    displacements = []
    if isinstance(initial_objects, Mapping) and isinstance(final_objects, Mapping):
        for key in set(initial_objects) & set(final_objects):
            start = initial_objects[key]
            end = final_objects[key]
            if isinstance(start, Sequence) and isinstance(end, Sequence) and len(start) == len(end) == 3:
                displacements.append(
                    math.sqrt(sum((float(end[index]) - float(start[index])) ** 2 for index in range(3)))
                )
    joints = final.get("joint_positions", {})
    return {
        "finite_state": bool(final.get("finite_state")),
        "final_joint_positions": {
            key: round(float(joints[key]), 5)
            for key in _JOINT_KEYS
            if isinstance(joints, Mapping) and key in joints
        },
        "final_end_effector_position_m": rounded_vector(final["end_effector_position_m"]),
        "dynamic_object_count": len(final_objects) if isinstance(final_objects, Mapping) else 0,
        "maximum_public_object_displacement_m": round(max(displacements, default=0.0), 6),
        "action_count": int(object.__getattribute__(facade, "_action_count")),
        "observation_count": int(object.__getattribute__(facade, "_observation_count")),
    }


def _probe_worker(connection: Any, payload: dict[str, Any], work_directory: str) -> None:
    """Child entrypoint.  Never send raw generated values or exception text."""

    response: dict[str, Any] = {"outcome": "worker_failed", "simulation_seconds": 0.0}
    backend: Any = None
    worker_phase = "bootstrap"
    try:
        worker_phase = "resource_limits"
        _set_worker_limits(payload)
        worker_phase = "process_isolation"
        os.chdir(work_directory)
        sys.dont_write_bytecode = True
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
            mode = payload["mode"]
            actual_mujoco = mode == "aws"
            worker_phase = "runtime_create"
            catalog_path = Path(payload["scene_catalog_path"])
            if sha256_file(catalog_path) != payload["scene_catalog_sha256"]:
                raise _ProbeLimitExceeded("scene catalog hash drift")
            if actual_mujoco:
                worker_phase = "mujoco_import"
                from .bridge.mujoco_tabletop import SO101MujocoTabletopRuntime

                model_path = Path(payload["robot_model_path"])
                if sha256_file(model_path) != payload["robot_model_sha256"]:
                    raise _ProbeLimitExceeded("robot model hash drift")
                worker_phase = "mujoco_model_create"
                backend = SO101MujocoTabletopRuntime(
                    model_path,
                    common_reset=payload["common_reset"],
                    initial_state=payload["smoke_instance"],
                    auto_step=False,
                    scene_catalog=catalog_path,
                    scene_freeze=payload["scene_freeze"],
                    require_scene_catalog=True,
                    require_explicit_asset_refs=True,
                )
                worker_phase = "mujoco_connect"
                backend.connect(calibrate=False)
            else:
                worker_phase = "fixture_import"
                from .bridge.deterministic_tabletop import DeterministicTabletopRuntime
                from .bridge.scene_catalog import SceneAssetCatalog

                # Offline mode remains explicitly nonphysical, but it still
                # consumes the exact formal catalog/freeze pair and rejects
                # inline or missing asset identities before creating the
                # deterministic state fixture.
                worker_phase = "fixture_catalog_verify"
                catalog = SceneAssetCatalog(catalog_path)
                catalog.verify_environment_freeze(payload["scene_freeze"])
                common_reset = payload["common_reset"]
                table = common_reset.get("table")
                if not isinstance(table, Mapping):
                    raise _ProbeLimitExceeded("public table asset is missing")
                catalog.resolve(table, expected_role="support_surface", legacy_kind=None)
                smoke_instance = payload["smoke_instance"]
                fixture_instance = copy.deepcopy(smoke_instance)
                fixture_bodies: list[dict[str, Any]] = []
                for spec in smoke_instance.get("bodies", []):
                    if not isinstance(spec, Mapping) or "asset_ref" not in spec:
                        raise _ProbeLimitExceeded("public body asset ref is missing")
                    resolved = catalog.resolve(
                        spec,
                        expected_role=None,
                        legacy_kind=None,
                    )
                    if resolved.role not in {"dynamic_object", "receptacle"}:
                        raise _ProbeLimitExceeded("public body asset role is invalid")
                    # The deterministic fixture predates catalog refs and uses
                    # ``kind`` only for its coarse object/receptacle split.
                    # Inject that catalog-derived identity into a private copy;
                    # never change the frozen public projection.
                    fixture_spec = copy.deepcopy(dict(spec))
                    fixture_spec["kind"] = resolved.runtime_kind
                    fixture_bodies.append(fixture_spec)
                fixture_instance["bodies"] = fixture_bodies
                fixture_markers: list[dict[str, Any]] = []
                for spec in smoke_instance.get("markers", []):
                    if not isinstance(spec, Mapping) or "asset_ref" not in spec:
                        raise _ProbeLimitExceeded("public marker asset ref is missing")
                    resolved = catalog.resolve(
                        spec,
                        expected_role="marker",
                        legacy_kind=None,
                    )
                    fixture_spec = copy.deepcopy(dict(spec))
                    fixture_spec["kind"] = resolved.runtime_kind
                    fixture_markers.append(fixture_spec)
                fixture_instance["markers"] = fixture_markers

                worker_phase = "fixture_create"
                backend = DeterministicTabletopRuntime()
                backend.reset(fixture_instance)

            # MuJoCo's trusted macOS import performs one `sysctl` platform
            # check.  Complete all trusted runtime imports/construction first,
            # then remove credentials and command lookup before any generated
            # module is imported or invoked.
            os.environ.clear()
            os.environ.update(
                {"HOME": work_directory, "TMPDIR": work_directory, "PATH": ""}
            )
            worker_phase = "scene_snapshot"
            initial = _backend_snapshot(backend, actual_mujoco=actual_mujoco)
            # Separate trusted process/import/catalog/MuJoCo startup from the
            # generated-package execution wall clock.  This is the first point
            # at which a public Generation world is created.
            connection.send(
                {
                    "event": "runtime_ready",
                    "ready_monotonic_s": time.monotonic(),
                }
            )
            worker_phase = "package_integrity"
            package_root = Path(payload["package_root"])
            if package_tree_sha256(package_root) != payload["package_sha256"]:
                raise _ProbeLimitExceeded("package hash drift")
            from .generated_loader import load_isolated_generated_module

            worker_phase = "generated_module_load"
            module = load_isolated_generated_module(
                package_root,
                str(payload["module"]),
                namespace_label="generation_public_probe",
            )
            function = getattr(module, str(payload["function_name"]), None)
            if not callable(function):
                raise _ProbeLimitExceeded("generated function missing")
            facade = _PublicRuntimeFacade(
                backend,
                max_runtime_calls=int(payload["max_runtime_calls"]),
            )
            clock = _PublicSimulationClock(
                backend,
                actual_mujoco=actual_mujoco,
                maximum=float(payload["max_simulation_s"]),
            )
            globals_view = getattr(function, "__globals__", None)
            if not isinstance(globals_view, dict):
                raise _ProbeLimitExceeded("generated function globals unavailable")
            imported_time = globals_view.get("time")
            if imported_time is not None:
                if imported_time is not time:
                    raise _ProbeLimitExceeded("generated time binding is not standard")
                globals_view["time"] = clock
            generated_status = None
            outcome = "generated_returned"
            worker_phase = "generated_call"
            try:
                generated_result = function(facade, **dict(payload["call_arguments"]))
                status = generated_result.get("status") if isinstance(generated_result, Mapping) else None
                declared = payload.get("declared_status_values", [])
                if isinstance(status, str) and status in declared and _SAFE_PUBLIC_ID.fullmatch(status):
                    generated_status = status
                elif status is not None:
                    generated_status = "unreported_or_undeclared"
            except _ProbeLimitExceeded:
                outcome = "probe_resource_limit"
            except BaseException:
                outcome = "generated_exception"
            worker_phase = "final_snapshot"
            final = _backend_snapshot(backend, actual_mujoco=actual_mujoco)
            response = {
                "outcome": outcome,
                "generated_status": generated_status,
                "simulation_seconds": float(clock.monotonic()),
                "measurements": _sanitized_measurements(
                    initial, final, facade=facade
                ),
            }
    except _ProbeLimitExceeded:
        response = {"outcome": "integrity_or_resource_failure", "simulation_seconds": 0.0}
    except BaseException:
        response = {
            "outcome": "worker_failed",
            "code": f"WORKER_{worker_phase.upper()}_FAILED",
            "simulation_seconds": 0.0,
        }
    finally:
        if backend is not None:
            try:
                backend.disconnect()
            except BaseException:
                response = {"outcome": "worker_cleanup_failed", "simulation_seconds": 0.0}
        try:
            connection.send(response)
        except BaseException:
            pass
        try:
            connection.close()
        except BaseException:
            pass


def _run_probe_worker(
    payload: Mapping[str, Any],
    *,
    startup_timeout_s: float,
    execution_timeout_s: float,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    work_directory = tempfile.mkdtemp(prefix="soarm-generation-probe-")
    process = context.Process(
        target=_probe_worker,
        args=(child, _safe_deepcopy(payload), work_directory),
        name="soarm-generation-public-probe",
        daemon=True,
    )
    started = time.monotonic()
    startup_elapsed_s = 0.0

    def timed(
        value: Mapping[str, Any],
        *,
        execution_elapsed_s: float = 0.0,
    ) -> dict[str, Any]:
        result = _safe_deepcopy(value)
        result["_infrastructure_timing"] = {
            "trusted_startup_s": round(
                max(0.0, startup_elapsed_s),
                6,
            ),
            "generated_execution_s": round(
                max(0.0, execution_elapsed_s),
                6,
            ),
        }
        return result

    try:
        process.start()
        child.close()
        if not parent.poll(float(startup_timeout_s)):
            startup_elapsed_s = time.monotonic() - started
            process.terminate()
            process.join(timeout=2.0)
            return timed(
                {
                    "outcome": "worker_startup_timeout",
                    "simulation_seconds": 0.0,
                }
            )
        try:
            value = parent.recv()
        except EOFError:
            startup_elapsed_s = time.monotonic() - started
            return timed(
                {"outcome": "worker_failed", "simulation_seconds": 0.0}
            )
        startup_elapsed_s = time.monotonic() - started
        execution_elapsed_s = 0.0
        if isinstance(value, Mapping) and value.get("event") == "runtime_ready":
            ready_value = value.get("ready_monotonic_s")
            if (
                not isinstance(ready_value, (int, float))
                or isinstance(ready_value, bool)
                or not math.isfinite(float(ready_value))
            ):
                process.terminate()
                process.join(timeout=2.0)
                return timed(
                    {"outcome": "worker_failed", "simulation_seconds": 0.0}
                )
            execution_started = float(ready_value)
            elapsed_before_poll = max(0.0, time.monotonic() - execution_started)
            execution_remaining = float(execution_timeout_s) - elapsed_before_poll
            if execution_remaining <= 0.0 or not parent.poll(execution_remaining):
                process.terminate()
                process.join(timeout=2.0)
                return timed(
                    {
                        "outcome": "generated_execution_wall_timeout",
                        "simulation_seconds": 0.0,
                    },
                    execution_elapsed_s=time.monotonic() - execution_started,
                )
            try:
                value = parent.recv()
            except EOFError:
                return timed(
                    {"outcome": "worker_failed", "simulation_seconds": 0.0},
                    execution_elapsed_s=time.monotonic() - execution_started,
                )
            execution_elapsed_s = time.monotonic() - execution_started
        process.join(timeout=2.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
            return timed(
                {"outcome": "worker_cleanup_failed", "simulation_seconds": 0.0},
                execution_elapsed_s=execution_elapsed_s,
            )
        if not isinstance(value, Mapping):
            return timed(
                {"outcome": "worker_failed", "simulation_seconds": 0.0},
                execution_elapsed_s=execution_elapsed_s,
            )
        return timed(value, execution_elapsed_s=execution_elapsed_s)
    finally:
        try:
            parent.close()
        except BaseException:
            pass
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
        shutil.rmtree(work_directory, ignore_errors=True)


__all__ = [
    "GenerationSimulationError",
    "GenerationSimulationSandbox",
    "ProbeLimits",
]
