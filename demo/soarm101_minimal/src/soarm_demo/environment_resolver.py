"""Resolve and freeze the public Generation simulation environment.

``scene`` is deliberately not a fifth top-level input library.  A scene is a
versioned Morphology entry selected by the Tasks entry.  This module verifies
that exact four-library topology, selects the nested Morphology scene, and
writes one detached-hash envelope before the first Generation model request.

Only a locally-authored synthetic smoke scene is projected into the envelope.
Concrete task instances, held-out tasks and validation oracles never cross this
boundary.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from .audit import atomic_write_json, sha256_bytes, sha256_file, sha256_json
from .libraries import LibraryEntry, load_jsonl, load_structured
from .schema_validation import validate_json_schema


class EnvironmentResolutionError(RuntimeError):
    """The selected input libraries cannot form one immutable environment."""


_FOUR_LIBRARY_TYPES = frozenset({"morphology", "sdk_runtime", "tasks", "experience"})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ASSET_REF = re.compile(
    r"^morphology\.scene_asset/"
    r"(?P<asset_id>[a-z][a-z0-9_]*)@"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)#"
    r"(?P<variant_id>[a-z][a-z0-9_]*)$"
)
_PRIVATE_KEY_TOKENS = frozenset(
    {
        "oracle",
        "oracle_ref",
        "seed",
        "heldout",
        "held_out",
        "initial_state",
        "initial_states",
        "demo_batch",
        "similarity_matrix",
        "task_oracles",
    }
)


@dataclass(frozen=True)
class ResolvedGenerationEnvironment:
    """Auditable result returned to the pipeline before Generation starts."""

    freeze_path: Path
    freeze_sha256: str
    manifest_sha256: str
    manifest: dict[str, Any]
    scene_snapshot_root: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "freeze_path": str(self.freeze_path),
            "freeze_sha256": self.freeze_sha256,
            "manifest_sha256": self.manifest_sha256,
            "scene_snapshot_root": (
                str(self.scene_snapshot_root)
                if self.scene_snapshot_root is not None
                else None
            ),
        }


@dataclass(frozen=True)
class MjcfReferencedFile:
    """One external file selected by an MJCF ``file`` attribute.

    ``reference`` is the POSIX resource name MuJoCo expects when compiling an
    XML string.  ``path`` is the corresponding live file selected relative to
    the top-level MJCF.  The resolver later binds both values and the file hash
    into the detached environment freeze.
    """

    kind: str
    reference: str
    path: Path


def _demo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _as_entry(value: LibraryEntry | str | Path) -> LibraryEntry:
    return value if isinstance(value, LibraryEntry) else LibraryEntry.open(value)


def _safe_relative_file(path: Path, *, libraries_root: Path, label: str) -> str:
    lexical_root = Path(os.path.abspath(libraries_root))
    lexical_path = Path(os.path.abspath(path))
    if lexical_root.is_symlink():
        raise EnvironmentResolutionError(f"{label} root may not be a symlink")
    try:
        lexical_relative = lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise EnvironmentResolutionError(
            f"{label} is not contained by libraries_root"
        ) from exc
    if any(
        lexical_root.joinpath(*lexical_relative.parts[:index]).is_symlink()
        for index in range(1, len(lexical_relative.parts) + 1)
    ):
        raise EnvironmentResolutionError(f"{label} may not use symlinks")

    root = lexical_root.resolve()
    resolved = lexical_path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise EnvironmentResolutionError(
            f"{label} is not contained by libraries_root"
        ) from exc
    if not resolved.is_file():
        raise EnvironmentResolutionError(f"{label} is not a regular file")
    return PurePosixPath(*relative.parts).as_posix()


def _safe_posix_reference(value: str, *, label: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise EnvironmentResolutionError(f"{label} is not a safe POSIX path")
    reference = PurePosixPath(value)
    if (
        reference.is_absolute()
        or reference in {PurePosixPath("."), PurePosixPath("")}
        or any(part in {"", ".", ".."} for part in reference.parts)
    ):
        raise EnvironmentResolutionError(f"{label} is not a safe relative path")
    return reference


def discover_mjcf_referenced_files(
    model_path: str | Path,
    *,
    model_bytes: bytes | None = None,
) -> tuple[MjcfReferencedFile, ...]:
    """Discover the exact external-file closure of one self-contained MJCF.

    The current bridge composes the top-level MJCF with ``ElementTree`` before
    passing an in-memory resource map to MuJoCo.  XML includes would introduce
    another model graph with different path-base semantics, so they are rejected
    fail-closed until that graph is explicitly represented in the freeze.
    """

    selected = Path(model_path)
    try:
        payload = selected.read_bytes() if model_bytes is None else model_bytes
        root = ET.fromstring(payload)
    except (OSError, ET.ParseError) as exc:
        raise EnvironmentResolutionError(f"robot MJCF cannot be parsed: {exc}") from exc
    if root.tag != "mujoco":
        raise EnvironmentResolutionError("robot model is not a MuJoCo MJCF document")
    if root.findall(".//include"):
        raise EnvironmentResolutionError(
            "MJCF include files are not supported by the frozen robot-model closure"
        )

    compiler = root.find("compiler")
    compiler_attributes = {} if compiler is None else dict(compiler.attrib)
    assetdir = compiler_attributes.get("assetdir")
    directories = {
        "mesh": assetdir or compiler_attributes.get("meshdir", ""),
        "texture": assetdir or compiler_attributes.get("texturedir", ""),
        "hfield": assetdir or "",
        "skin": assetdir or "",
    }
    references: dict[str, MjcfReferencedFile] = {}
    for kind, directory in directories.items():
        directory_path = (
            PurePosixPath()
            if not directory
            else _safe_posix_reference(directory, label=f"MJCF compiler {kind} directory")
        )
        for index, element in enumerate(root.findall(f".//asset/{kind}")):
            raw_file = element.get("file")
            if raw_file is None:
                continue
            file_ref = _safe_posix_reference(
                raw_file,
                label=f"MJCF asset.{kind}[{index}].file",
            )
            reference_path = directory_path / file_ref
            reference = reference_path.as_posix()
            candidate = selected.parent.joinpath(*reference_path.parts)
            prior = references.get(reference)
            discovered = MjcfReferencedFile(
                kind=kind,
                reference=reference,
                path=candidate,
            )
            if prior is not None and (prior.kind != kind or prior.path != candidate):
                raise EnvironmentResolutionError(
                    f"MJCF resource reference {reference!r} is ambiguous"
                )
            references[reference] = discovered
    return tuple(references[key] for key in sorted(references))


def _robot_model_path(
    robot: LibraryEntry,
    *,
    declared_attachment_models: set[str],
) -> Path:
    """Select a generic morphology MJCF without a robot-specific filename."""

    explicit_paths = set(declared_attachment_models)
    for key in ("runtime_model", "model_binding"):
        value = robot.manifest.get(key)
        if isinstance(value, Mapping) and isinstance(value.get("path"), str):
            explicit_paths.add(str(value["path"]))
    mjcf_payloads = {
        payload.path
        for payload in robot.payloads
        if payload.required
        and (
            payload.media_type in {"application/xml", "model/mjcf"}
            or PurePosixPath(payload.path).suffix.lower() in {".xml", ".mjcf"}
        )
    }
    if explicit_paths:
        if len(explicit_paths) != 1:
            raise EnvironmentResolutionError(
                "selected robot attachments disagree on the robot model path"
            )
        selected_relative = next(iter(explicit_paths))
        if selected_relative not in mjcf_payloads:
            raise EnvironmentResolutionError(
                "selected robot model is not a required morphology MJCF payload"
            )
    elif len(mjcf_payloads) == 1:
        selected_relative = next(iter(mjcf_payloads))
    else:
        raise EnvironmentResolutionError(
            "morphology must select exactly one required MJCF robot-model payload"
        )
    relative = _safe_posix_reference(selected_relative, label="robot model payload")
    selected = robot.root.joinpath(*relative.parts)
    _safe_relative_file(selected, libraries_root=robot.root, label="robot model")
    return selected


def _robot_model_binding(
    robot: LibraryEntry,
    *,
    model_path: Path,
    libraries_root: Path,
) -> dict[str, Any]:
    declared_payloads = {payload.path for payload in robot.payloads if payload.required}
    referenced_files: list[dict[str, str]] = []
    for referenced in discover_mjcf_referenced_files(model_path):
        robot_relative = _safe_relative_file(
            referenced.path,
            libraries_root=robot.root,
            label=f"robot MJCF {referenced.kind} resource {referenced.reference!r}",
        )
        if robot_relative not in declared_payloads:
            raise EnvironmentResolutionError(
                f"robot MJCF resource is not a required morphology payload: {robot_relative}"
            )
        referenced_files.append(
            {
                "kind": referenced.kind,
                "reference": referenced.reference,
                "path": _safe_relative_file(
                    referenced.path,
                    libraries_root=libraries_root,
                    label=f"robot MJCF {referenced.kind} resource {referenced.reference!r}",
                ),
                "sha256": sha256_file(referenced.path),
            }
        )
    return _binding(
        model_path,
        libraries_root=libraries_root,
        label="robot model",
        robot_id=robot.manifest["entry_id"],
        version=robot.manifest["version"],
        model_format="mujoco_mjcf",
        referenced_files=referenced_files,
    )


def _binding(
    path: Path,
    *,
    libraries_root: Path,
    label: str,
    **identity: Any,
) -> dict[str, Any]:
    return {
        **identity,
        "path": _safe_relative_file(path, libraries_root=libraries_root, label=label),
        "sha256": sha256_file(path),
    }


def _strict_verify_entry(
    entry: LibraryEntry,
    *,
    schemas_root: Path,
    expected_type: str,
    expected_kind: str | None = None,
) -> None:
    manifest = entry.manifest
    if manifest.get("library_type") != expected_type:
        raise EnvironmentResolutionError(
            f"expected {expected_type!r} entry, got {manifest.get('library_type')!r}"
        )
    if expected_kind is not None and manifest.get("entry_kind", "robot") != expected_kind:
        raise EnvironmentResolutionError(
            f"expected Morphology {expected_kind!r} entry"
        )
    if manifest.get("status") != "ready":
        raise EnvironmentResolutionError(
            f"library entry {manifest.get('entry_id')!r} is not ready"
        )
    payloads = entry.payloads
    paths = [payload.path for payload in payloads]
    if len(paths) != len(set(paths)):
        raise EnvironmentResolutionError(
            f"library entry {manifest.get('entry_id')!r} has duplicate payload paths"
        )
    declared_hashes = manifest.get("content_hashes")
    if not isinstance(declared_hashes, Mapping):
        raise EnvironmentResolutionError("library manifest content_hashes is missing")
    for payload in payloads:
        resolved = entry.resolve_payload(payload)
        if resolved.is_symlink():
            raise EnvironmentResolutionError(
                f"library payload may not be a symlink: {payload.path}"
            )
        if payload.required and not resolved.is_file():
            raise EnvironmentResolutionError(
                f"required library payload is not a regular file: {payload.path}"
            )
        if not resolved.exists():
            continue
        expected = declared_hashes.get(payload.path)
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise EnvironmentResolutionError(
                f"payload lacks a mandatory SHA-256 binding: {payload.path}"
            )
        if sha256_file(resolved) != expected:
            raise EnvironmentResolutionError(f"payload hash mismatch: {payload.path}")
    verification = entry.verify()
    schema_verification = entry.validate_runtime_schemas(schemas_root)
    errors = list(verification["errors"]) + list(schema_verification["errors"])
    if errors:
        raise EnvironmentResolutionError(
            f"library entry {manifest.get('entry_id')!r} failed verification: "
            + "; ".join(errors)
        )


def _require_compatibility_member(
    compatibility: Mapping[str, Any],
    field: str,
    expected: str,
    *,
    label: str,
) -> None:
    values = compatibility.get(field)
    if (
        not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        or expected not in values
    ):
        raise EnvironmentResolutionError(
            f"{label} compatibility does not include {field}={expected!r}"
        )


def _asset_index(catalog: Mapping[str, Any]) -> dict[str, set[str]]:
    version = catalog.get("version")
    output: dict[str, set[str]] = {}
    assets = catalog.get("assets")
    if not isinstance(assets, list):
        raise EnvironmentResolutionError("scene asset catalog assets must be an array")
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise EnvironmentResolutionError("scene asset catalog entry must be an object")
        asset_id = asset.get("asset_id")
        variants = asset.get("variants")
        if not isinstance(asset_id, str) or not isinstance(variants, list):
            raise EnvironmentResolutionError("scene asset catalog entry is malformed")
        if asset_id in output:
            raise EnvironmentResolutionError(f"duplicate scene asset {asset_id!r}")
        output[asset_id] = {
            str(item.get("variant_id"))
            for item in variants
            if isinstance(item, Mapping)
        }
        if not output[asset_id]:
            raise EnvironmentResolutionError(f"scene asset {asset_id!r} has no variants")
    if not isinstance(version, str):
        raise EnvironmentResolutionError("scene asset catalog version is missing")
    return output


def catalog_asset_descriptor_hashes(catalog: Mapping[str, Any]) -> dict[str, str]:
    """Hash every complete reusable asset *variant* by stable asset reference.

    Each payload binds the asset's shared geometry/physics/contract plus one
    material variant.  This lets the runtime verify a red and blue cube
    independently even though they share one geometry profile.
    """

    assets = catalog.get("assets")
    if not isinstance(assets, list):
        raise EnvironmentResolutionError("scene asset catalog assets must be an array")
    version = catalog.get("version")
    if not isinstance(version, str):
        raise EnvironmentResolutionError("scene asset catalog version is missing")
    output: dict[str, str] = {}
    for index, descriptor in enumerate(assets):
        if not isinstance(descriptor, Mapping):
            raise EnvironmentResolutionError(
                f"scene asset catalog descriptor {index} must be an object"
            )
        asset_id = descriptor.get("asset_id")
        if not isinstance(asset_id, str):
            raise EnvironmentResolutionError(
                "scene asset descriptors require string asset_id values"
            )
        variants = descriptor.get("variants")
        if not isinstance(variants, list) or not variants:
            raise EnvironmentResolutionError(
                f"scene asset {asset_id!r} requires at least one variant"
            )
        shared = {key: copy.deepcopy(value) for key, value in descriptor.items() if key != "variants"}
        for variant in variants:
            if not isinstance(variant, Mapping) or not isinstance(variant.get("variant_id"), str):
                raise EnvironmentResolutionError(
                    f"scene asset {asset_id!r} has a malformed variant"
                )
            asset_ref = (
                f"morphology.scene_asset/{asset_id}@{version}#"
                f"{variant['variant_id']}"
            )
            if asset_ref in output:
                raise EnvironmentResolutionError(
                    f"duplicate scene asset reference {asset_ref!r}"
                )
            output[asset_ref] = sha256_json(
                {
                    "asset": shared,
                    "variant": copy.deepcopy(dict(variant)),
                    "asset_ref": asset_ref,
                }
            )
    return dict(sorted(output.items()))


def _require_asset_ref(
    value: Any,
    *,
    catalog_version: str,
    assets: Mapping[str, set[str]],
    label: str,
) -> None:
    if not isinstance(value, str):
        raise EnvironmentResolutionError(f"{label} must be an asset reference")
    match = _ASSET_REF.fullmatch(value)
    if match is None or match.group("version") != catalog_version:
        raise EnvironmentResolutionError(f"{label} has an invalid catalog version/reference")
    if match.group("asset_id") not in assets:
        raise EnvironmentResolutionError(f"{label} selects an unknown scene asset")
    if match.group("variant_id") not in assets[match.group("asset_id")]:
        raise EnvironmentResolutionError(f"{label} selects an unknown asset variant")


def _assert_no_private_projection(value: Any, *, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _PRIVATE_KEY_TOKENS:
                raise EnvironmentResolutionError(
                    f"private evaluator field crossed Generation boundary at {path}"
                )
            _assert_no_private_projection(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_private_projection(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower().replace("\\", "/")
        if "/private/" in f"/{lowered.strip('/')}/" or lowered.startswith("private/"):
            raise EnvironmentResolutionError(
                f"private filesystem reference crossed Generation boundary at {path}"
            )


def _materialize_scene_view(
    scene_entry: LibraryEntry,
    *,
    input_snapshot_root: Path | None,
) -> Path | None:
    if input_snapshot_root is None:
        return None
    target = (
        input_snapshot_root.resolve()
        / "morphology"
        / "scenes"
        / str(scene_entry.manifest["entry_id"])
        / scene_entry.root.name
    )
    return target if scene_entry.materialize_generation_view(target) else target


def resolve_generation_environment(
    libraries: Mapping[str, LibraryEntry | str | Path],
    *,
    morphology_scene: LibraryEntry | str | Path,
    libraries_root: str | Path,
    output_path: str | Path,
    input_snapshot_root: str | Path | None = None,
    schemas_root: str | Path | None = None,
) -> ResolvedGenerationEnvironment:
    """Verify four libraries and freeze their selected public environment.

    This function must be called before provider request 1.  It performs no
    model call and never reads the private task-library directory.
    """

    if set(libraries) != _FOUR_LIBRARY_TYPES:
        raise EnvironmentResolutionError(
            "environment resolution requires exactly morphology, sdk_runtime, "
            "tasks, and experience top-level libraries"
        )
    root = Path(libraries_root).resolve()
    if not root.is_dir():
        raise EnvironmentResolutionError("libraries_root is not a directory")
    schema_dir = Path(schemas_root).resolve() if schemas_root else _demo_root() / "schemas"
    entries = {name: _as_entry(value) for name, value in libraries.items()}
    scene_entry = _as_entry(morphology_scene)

    for name, entry in entries.items():
        _strict_verify_entry(
            entry,
            schemas_root=schema_dir,
            expected_type=name,
            expected_kind="robot" if name == "morphology" else None,
        )
    _strict_verify_entry(
        scene_entry,
        schemas_root=schema_dir,
        expected_type="morphology",
        expected_kind="scene",
    )

    robot = entries["morphology"]
    runtime = entries["sdk_runtime"]
    tasks = entries["tasks"]
    experience = entries["experience"]
    attachments_path = robot.root / "attachments.yaml"
    requirements_path = tasks.root / "environment_requirements.yaml"
    runtime_contract_path = runtime.root / "runtime_contract.yaml"
    scene_path = scene_entry.root / "scene.yaml"
    attachments = load_structured(attachments_path)
    requirements = load_structured(requirements_path)
    runtime_contract = load_structured(runtime_contract_path)
    scene = load_structured(scene_path)
    if not all(
        isinstance(value, Mapping)
        for value in (attachments, requirements, runtime_contract, scene)
    ):
        raise EnvironmentResolutionError("environment metadata must contain objects")

    robot_compatibility = robot.manifest.get("compatibility")
    runtime_compatibility = runtime.manifest.get("compatibility")
    task_compatibility = tasks.manifest.get("compatibility")
    experience_compatibility = experience.manifest.get("compatibility")
    scene_compatibility = scene_entry.manifest.get("compatibility")
    if not all(
        isinstance(value, Mapping)
        for value in (
            robot_compatibility,
            runtime_compatibility,
            task_compatibility,
            experience_compatibility,
            scene_compatibility,
        )
    ):
        raise EnvironmentResolutionError(
            "all selected library manifests require compatibility metadata"
        )

    catalog_ref = scene.get("asset_catalog_ref")
    if not isinstance(catalog_ref, Mapping):
        raise EnvironmentResolutionError("scene asset_catalog_ref is missing")
    catalog_rel = catalog_ref.get("path")
    if not isinstance(catalog_rel, str):
        raise EnvironmentResolutionError("scene catalog path is missing")
    catalog_path = (scene_entry.root / catalog_rel).resolve()
    try:
        catalog_path.relative_to(scene_entry.root)
    except ValueError as exc:
        raise EnvironmentResolutionError("scene catalog path escapes its entry") from exc
    catalog = load_structured(catalog_path)
    if not isinstance(catalog, Mapping):
        raise EnvironmentResolutionError("scene catalog must contain an object")

    robot_req = requirements.get("robot_requirement")
    runtime_req = requirements.get("runtime_requirement")
    scene_req = requirements.get("scene_requirement")
    if not all(isinstance(value, Mapping) for value in (robot_req, runtime_req, scene_req)):
        raise EnvironmentResolutionError("task environment requirements are incomplete")
    if (robot_req.get("robot_id"), robot_req.get("robot_version")) != (
        robot.manifest.get("entry_id"),
        robot.manifest.get("version"),
    ):
        raise EnvironmentResolutionError("Tasks robot requirement is incompatible")
    runtime_id = runtime_contract.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id:
        raise EnvironmentResolutionError("selected runtime_id is missing")
    robot_id = str(robot.manifest.get("entry_id", ""))
    scene_id = str(scene.get("scene_id", ""))
    if robot_compatibility.get("robot_id") != robot_id:
        raise EnvironmentResolutionError("Morphology robot identity is incompatible")
    _require_compatibility_member(
        robot_compatibility,
        "supported_runtimes",
        runtime_id,
        label="Morphology",
    )
    if (
        runtime_compatibility.get("robot_id") != robot_id
        or runtime_compatibility.get("package") != runtime_req.get("package")
        or str(runtime_compatibility.get("package_version"))
        != str(runtime.manifest.get("version"))
    ):
        raise EnvironmentResolutionError("SDK/runtime manifest compatibility drifted")
    for compatibility, label in (
        (scene_compatibility, "Scene"),
        (task_compatibility, "Tasks"),
        (experience_compatibility, "Experience"),
    ):
        _require_compatibility_member(
            compatibility,
            "robots",
            robot_id,
            label=label,
        )
        _require_compatibility_member(
            compatibility,
            "runtime_ids",
            runtime_id,
            label=label,
        )
    _require_compatibility_member(
        scene_compatibility,
        "scene_ids",
        scene_id,
        label="Scene",
    )
    _require_compatibility_member(
        task_compatibility,
        "scenes",
        scene_id,
        label="Tasks",
    )
    if scene_compatibility.get("immutable_revision") is not True:
        raise EnvironmentResolutionError("selected Scene revision is not immutable")
    if (
        runtime_req.get("runtime_id") != runtime_id
        or runtime_req.get("package") != runtime.manifest.get("compatibility", {}).get("package")
        or runtime_req.get("package_version") != runtime.manifest.get("version")
    ):
        raise EnvironmentResolutionError("Tasks SDK/runtime requirement is incompatible")
    if (
        scene_req.get("entry_id") != scene_entry.manifest.get("entry_id")
        or scene_req.get("entry_version") != scene_entry.manifest.get("version")
        or scene_req.get("scene_id") != scene.get("scene_id")
    ):
        raise EnvironmentResolutionError("Tasks scene requirement is incompatible")
    if robot.manifest.get("entry_id") not in scene.get("compatible_robot_ids", []):
        raise EnvironmentResolutionError("scene does not support the selected robot")
    if scene.get("required_runtime_id") != runtime_id:
        raise EnvironmentResolutionError("scene does not support the selected runtime")
    if (
        catalog_ref.get("catalog_id") != catalog.get("catalog_id")
        or catalog_ref.get("version") != catalog.get("version")
    ):
        raise EnvironmentResolutionError("scene catalog identity/version mismatch")

    attachment_index = {
        item.get("attachment_id"): item
        for item in attachments.get("attachments", [])
        if isinstance(item, Mapping)
    }
    declared_attachment_models: set[str] = set()
    for attachment_id in robot_req.get("required_attachment_ids", []):
        attachment = attachment_index.get(attachment_id)
        if (
            not isinstance(attachment, Mapping)
            or attachment.get("status") != "active"
            or attachment.get("required_runtime_id") != runtime_id
        ):
            raise EnvironmentResolutionError(
                f"required attachment {attachment_id!r} is unavailable/incompatible"
            )
        model_binding = attachment.get("model_binding")
        if not isinstance(model_binding, Mapping):
            raise EnvironmentResolutionError("attachment model binding is missing")
        declared_model_path = model_binding.get("robot_model_path")
        if not isinstance(declared_model_path, str):
            raise EnvironmentResolutionError("attachment robot-model path is missing")
        declared_attachment_models.add(declared_model_path)
        model_relative = _safe_posix_reference(
            declared_model_path,
            label="attachment robot-model path",
        )
        model_path = robot.root.joinpath(*model_relative.parts)
        _safe_relative_file(
            model_path,
            libraries_root=robot.root,
            label="attachment robot model",
        )
        if (
            not model_path.is_file()
            or model_binding.get("robot_model_sha256") != sha256_file(model_path)
        ):
            raise EnvironmentResolutionError("attachment robot-model binding drifted")
        for geometry in model_binding.get("geometry_payloads", []):
            if not isinstance(geometry, Mapping):
                raise EnvironmentResolutionError("attachment geometry binding is malformed")
            geometry_relative = _safe_posix_reference(
                str(geometry.get("path", "")),
                label="attachment geometry path",
            )
            geometry_path = robot.root.joinpath(*geometry_relative.parts)
            _safe_relative_file(
                geometry_path,
                libraries_root=robot.root,
                label="attachment geometry",
            )
            if (
                not geometry_path.is_file()
                or geometry.get("sha256") != sha256_file(geometry_path)
            ):
                raise EnvironmentResolutionError("attachment geometry binding drifted")

    assets = _asset_index(catalog)
    visible_tasks = load_jsonl(tasks.root / "visible_tasks.jsonl")
    visible_ids = [str(item.get("task_id")) for item in visible_tasks]
    requirement_rows = requirements.get("visible_task_requirements")
    if not isinstance(requirement_rows, list):
        raise EnvironmentResolutionError("visible_task_requirements must be an array")
    requirement_ids = [str(item.get("task_id")) for item in requirement_rows if isinstance(item, Mapping)]
    if len(requirement_ids) != len(requirement_rows) or set(requirement_ids) != set(visible_ids):
        raise EnvironmentResolutionError(
            "visible task asset requirements must cover exactly the generation-visible tasks"
        )
    for row in requirement_rows:
        assert isinstance(row, Mapping)
        for index, asset_ref in enumerate(row.get("required_asset_refs", [])):
            _require_asset_ref(
                asset_ref,
                catalog_version=str(catalog["version"]),
                assets=assets,
                label=f"visible_task_requirements.{row.get('task_id')}[{index}]",
            )
    public_simulation = copy.deepcopy(scene.get("public_simulation"))
    if not isinstance(public_simulation, Mapping):
        raise EnvironmentResolutionError("scene public simulation projection is absent")
    for collection in ("bodies", "markers"):
        for index, item in enumerate(public_simulation["smoke_instance"][collection]):
            _require_asset_ref(
                item.get("asset_ref") if isinstance(item, Mapping) else None,
                catalog_version=str(catalog["version"]),
                assets=assets,
                label=f"public_simulation.smoke_instance.{collection}[{index}]",
            )
    _require_asset_ref(
        public_simulation["common_reset"]["table"].get("asset_ref"),
        catalog_version=str(catalog["version"]),
        assets=assets,
        label="public_simulation.common_reset.table",
    )

    robot_model_path = _robot_model_path(
        robot,
        declared_attachment_models=declared_attachment_models,
    )
    manifest: dict[str, Any] = {
        "schema_version": "robot_capability.generation_environment_manifest.v1",
        "resolver_version": "1.0.0",
        "selection": {
            "top_level_library_types": sorted(_FOUR_LIBRARY_TYPES),
            "morphology": {
                "robot_entry_id": robot.manifest["entry_id"],
                "robot_entry_version": robot.manifest["version"],
                "scene_entry_id": scene_entry.manifest["entry_id"],
                "scene_entry_version": scene_entry.manifest["version"],
                "scene_id": scene["scene_id"],
            },
            "sdk_runtime": {
                "entry_id": runtime.manifest["entry_id"],
                "version": runtime.manifest["version"],
                "runtime_id": runtime_id,
            },
            "tasks": {
                "entry_id": tasks.manifest["entry_id"],
                "version": tasks.manifest["version"],
            },
            "experience": {
                "entry_id": experience.manifest["entry_id"],
                "version": experience.manifest["version"],
            },
        },
        "bindings": {
            "morphology": {
                "robot_manifest": _binding(
                    robot.root / "manifest.yaml",
                    libraries_root=root,
                    label="robot manifest",
                    entry_id=robot.manifest["entry_id"],
                    version=robot.manifest["version"],
                ),
                "robot_model": _robot_model_binding(
                    robot,
                    model_path=robot_model_path,
                    libraries_root=root,
                ),
                "attachments": _binding(
                    attachments_path,
                    libraries_root=root,
                    label="attachments",
                    robot_id=robot.manifest["entry_id"],
                    version=attachments["version"],
                ),
                "scene_manifest": _binding(
                    scene_entry.root / "manifest.yaml",
                    libraries_root=root,
                    label="scene manifest",
                    entry_id=scene_entry.manifest["entry_id"],
                    version=scene_entry.manifest["version"],
                ),
                "scene_definition": _binding(
                    scene_path,
                    libraries_root=root,
                    label="scene definition",
                    scene_id=scene["scene_id"],
                    version=scene["version"],
                ),
                "scene_asset_catalog": _binding(
                    catalog_path,
                    libraries_root=root,
                    label="scene asset catalog",
                    catalog_id=catalog["catalog_id"],
                    version=catalog["version"],
                    asset_descriptor_sha256=catalog_asset_descriptor_hashes(catalog),
                ),
            },
            "sdk_runtime": {
                "manifest": _binding(
                    runtime.root / "manifest.yaml",
                    libraries_root=root,
                    label="SDK manifest",
                    entry_id=runtime.manifest["entry_id"],
                    version=runtime.manifest["version"],
                ),
                "runtime_contract": _binding(
                    runtime_contract_path,
                    libraries_root=root,
                    label="runtime contract",
                    runtime_id=runtime_id,
                ),
            },
            "tasks": {
                "manifest": _binding(
                    tasks.root / "manifest.yaml",
                    libraries_root=root,
                    label="Tasks manifest",
                    entry_id=tasks.manifest["entry_id"],
                    version=tasks.manifest["version"],
                ),
                "environment_requirements": _binding(
                    requirements_path,
                    libraries_root=root,
                    label="task environment requirements",
                ),
                "visible_tasks": _binding(
                    tasks.root / "visible_tasks.jsonl",
                    libraries_root=root,
                    label="visible tasks",
                ),
            },
            "experience": {
                "manifest": _binding(
                    experience.root / "manifest.yaml",
                    libraries_root=root,
                    label="Experience manifest",
                    entry_id=experience.manifest["entry_id"],
                    version=experience.manifest["version"],
                ),
                "records": _binding(
                    experience.root / "records.jsonl",
                    libraries_root=root,
                    label="Experience records",
                ),
            },
        },
        "public_task_selection": {
            "projection": "generation_visible_templates_only",
            "task_count": len(visible_ids),
            "task_ids": sorted(visible_ids),
            "requirements_sha256": sha256_file(requirements_path),
            "concrete_instances_included": False,
            "validation_oracles_included": False,
        },
        "public_simulation": public_simulation,
        "asset_resolution": {
            "source": "morphology_scene_catalog",
            "materialization": "on_demand",
            "unreferenced_assets_materialized": False,
            "missing_asset_result": "MISSING_ASSET",
        },
    }
    _assert_no_private_projection(manifest)
    schema = load_structured(schema_dir / "generation_environment_freeze.schema.json")
    envelope = {
        "schema_version": "robot_capability.generation_environment_freeze.v1",
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }
    issues = validate_json_schema(envelope, schema, instance_path="freeze")
    if issues:
        raise EnvironmentResolutionError(
            "generation environment freeze violates its schema: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )

    output = Path(output_path).resolve()
    if output.exists():
        raise EnvironmentResolutionError("environment freeze output already exists")
    snapshot_root = _materialize_scene_view(
        scene_entry,
        input_snapshot_root=(
            Path(input_snapshot_root) if input_snapshot_root is not None else None
        ),
    )
    atomic_write_json(output, envelope)
    return ResolvedGenerationEnvironment(
        freeze_path=output,
        freeze_sha256=sha256_file(output),
        manifest_sha256=envelope["manifest_sha256"],
        manifest=copy.deepcopy(manifest),
        scene_snapshot_root=snapshot_root,
    )


def verify_robot_model_freeze(
    freeze: Mapping[str, Any],
    robot_model_path: str | Path,
) -> tuple[bytes, dict[str, bytes]]:
    """Load exactly the robot bytes authorized by an environment freeze.

    The returned bytes are the same bytes whose hashes were checked, so the
    bridge can compile them without reopening a live file after verification.
    The selected libraries root is derived from the frozen relative model path;
    every referenced resource must resolve under that same root, match the
    MJCF-discovered closure exactly, and be free of symlink components.
    """

    if freeze.get("schema_version") != "robot_capability.generation_environment_freeze.v1":
        raise EnvironmentResolutionError("unsupported generation environment freeze schema")
    manifest = freeze.get("manifest")
    if not isinstance(manifest, Mapping):
        raise EnvironmentResolutionError(
            "generation environment freeze.manifest must be an object"
        )
    if freeze.get("manifest_sha256") != sha256_json(manifest):
        raise EnvironmentResolutionError(
            "generation environment freeze manifest_sha256 mismatch"
        )
    bindings = manifest.get("bindings")
    morphology = bindings.get("morphology") if isinstance(bindings, Mapping) else None
    model_binding = (
        morphology.get("robot_model") if isinstance(morphology, Mapping) else None
    )
    if not isinstance(model_binding, Mapping):
        raise EnvironmentResolutionError(
            "generation environment freeze robot_model binding is missing"
        )
    if model_binding.get("model_format") != "mujoco_mjcf":
        raise EnvironmentResolutionError(
            "generation environment freeze robot model format is unsupported"
        )
    raw_model_path = model_binding.get("path")
    expected_model_sha256 = model_binding.get("sha256")
    if (
        not isinstance(raw_model_path, str)
        or not isinstance(expected_model_sha256, str)
        or _SHA256.fullmatch(expected_model_sha256) is None
    ):
        raise EnvironmentResolutionError(
            "generation environment freeze robot_model binding is malformed"
        )
    model_relative = _safe_posix_reference(
        raw_model_path,
        label="frozen robot model path",
    )
    selected_model = Path(os.path.abspath(robot_model_path))
    suffix = tuple(model_relative.parts)
    if len(selected_model.parts) < len(suffix) or tuple(
        selected_model.parts[-len(suffix) :]
    ) != suffix:
        raise EnvironmentResolutionError(
            "selected robot model path does not match the environment freeze"
        )
    root_parts = selected_model.parts[: -len(suffix)]
    libraries_root = Path(*root_parts) if root_parts else Path(".")
    canonical_model = libraries_root.joinpath(*model_relative.parts)
    _safe_relative_file(
        canonical_model,
        libraries_root=libraries_root,
        label="freeze-bound robot model",
    )
    try:
        model_bytes = canonical_model.read_bytes()
    except OSError as exc:  # Defensive: _safe_relative_file already checked it.
        raise EnvironmentResolutionError(f"cannot read frozen robot model: {exc}") from exc
    if sha256_bytes(model_bytes) != expected_model_sha256:
        raise EnvironmentResolutionError("freeze-bound robot model hash mismatch")

    raw_references = model_binding.get("referenced_files")
    if not isinstance(raw_references, list):
        raise EnvironmentResolutionError(
            "generation environment freeze robot referenced_files binding is missing"
        )
    expected_by_reference: dict[str, tuple[str, str, str]] = {}
    for index, item in enumerate(raw_references):
        if not isinstance(item, Mapping):
            raise EnvironmentResolutionError(
                f"frozen robot referenced_files[{index}] is malformed"
            )
        kind = item.get("kind")
        reference = item.get("reference")
        raw_path = item.get("path")
        expected_sha256 = item.get("sha256")
        if (
            kind not in {"mesh", "texture", "hfield", "skin"}
            or not isinstance(reference, str)
            or not isinstance(raw_path, str)
            or not isinstance(expected_sha256, str)
            or _SHA256.fullmatch(expected_sha256) is None
        ):
            raise EnvironmentResolutionError(
                f"frozen robot referenced_files[{index}] is malformed"
            )
        normalized_reference = _safe_posix_reference(
            reference,
            label=f"frozen robot referenced_files[{index}].reference",
        ).as_posix()
        normalized_path = _safe_posix_reference(
            raw_path,
            label=f"frozen robot referenced_files[{index}].path",
        ).as_posix()
        if normalized_reference in expected_by_reference:
            raise EnvironmentResolutionError(
                f"duplicate frozen robot resource reference {normalized_reference!r}"
            )
        expected_by_reference[normalized_reference] = (
            str(kind),
            normalized_path,
            expected_sha256,
        )

    discovered = discover_mjcf_referenced_files(
        canonical_model,
        model_bytes=model_bytes,
    )
    actual_identity: dict[str, tuple[str, str]] = {}
    for item in discovered:
        actual_identity[item.reference] = (
            item.kind,
            _safe_relative_file(
                item.path,
                libraries_root=libraries_root,
                label=f"freeze-bound robot resource {item.reference!r}",
            ),
        )
    expected_identity = {
        reference: (kind, path)
        for reference, (kind, path, _sha256) in expected_by_reference.items()
    }
    if expected_identity != actual_identity:
        raise EnvironmentResolutionError(
            "generation environment frozen robot resource closure mismatch"
        )

    resources: dict[str, bytes] = {}
    for item in discovered:
        _kind, _path, expected_sha256 = expected_by_reference[item.reference]
        try:
            resource_bytes = item.path.read_bytes()
        except OSError as exc:  # Defensive: path validation already checked it.
            raise EnvironmentResolutionError(
                f"cannot read frozen robot resource {item.reference!r}: {exc}"
            ) from exc
        if sha256_bytes(resource_bytes) != expected_sha256:
            raise EnvironmentResolutionError(
                f"freeze-bound robot resource hash mismatch: {item.reference}"
            )
        resources[item.reference] = resource_bytes
    return model_bytes, resources


def verify_generation_environment_freeze(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    libraries_root: str | Path,
    rehash_sources: bool = True,
    schemas_root: str | Path | None = None,
) -> dict[str, Any]:
    """Revalidate a freeze and optionally every relative source binding."""

    freeze_path = Path(path).resolve()
    if not freeze_path.is_file() or freeze_path.is_symlink():
        raise EnvironmentResolutionError("generation environment freeze is not a regular file")
    actual_file_hash = sha256_file(freeze_path)
    if expected_sha256 is not None and actual_file_hash != expected_sha256:
        raise EnvironmentResolutionError("generation environment freeze file hash mismatch")
    try:
        envelope = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentResolutionError("generation environment freeze is invalid JSON") from exc
    if not isinstance(envelope, Mapping):
        raise EnvironmentResolutionError("generation environment freeze must be an object")
    schema_dir = Path(schemas_root).resolve() if schemas_root else _demo_root() / "schemas"
    schema = load_structured(schema_dir / "generation_environment_freeze.schema.json")
    issues = validate_json_schema(envelope, schema, instance_path="freeze")
    if issues:
        raise EnvironmentResolutionError(
            "generation environment freeze schema validation failed: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    manifest = envelope["manifest"]
    if envelope["manifest_sha256"] != sha256_json(manifest):
        raise EnvironmentResolutionError("generation environment manifest detached hash mismatch")
    _assert_no_private_projection(manifest)

    selection = manifest.get("selection")
    bindings = manifest.get("bindings")
    asset_resolution = manifest.get("asset_resolution")
    public = manifest.get("public_simulation")
    if (
        not isinstance(selection, Mapping)
        or selection.get("top_level_library_types") != sorted(_FOUR_LIBRARY_TYPES)
        or not isinstance(bindings, Mapping)
        or set(bindings) != _FOUR_LIBRARY_TYPES
    ):
        raise EnvironmentResolutionError(
            "freeze does not preserve the exact four-library topology"
        )
    if (
        not isinstance(asset_resolution, Mapping)
        or asset_resolution.get("source") != "morphology_scene_catalog"
        or asset_resolution.get("materialization") != "on_demand"
        or asset_resolution.get("unreferenced_assets_materialized") is not False
        or asset_resolution.get("missing_asset_result") != "MISSING_ASSET"
    ):
        raise EnvironmentResolutionError("freeze asset-resolution policy is invalid")
    if (
        not isinstance(public, Mapping)
        or public.get("privacy_class") != "public_generation_sandbox"
        or public.get("not_task_instance") is not True
    ):
        raise EnvironmentResolutionError("freeze public simulation projection is unsafe")

    root = Path(libraries_root).resolve()
    if rehash_sources:
        for library_name, group in bindings.items():
            if not isinstance(group, Mapping):
                raise EnvironmentResolutionError(f"{library_name} bindings must be an object")
            for binding_name, binding in group.items():
                if not isinstance(binding, Mapping):
                    raise EnvironmentResolutionError(
                        f"{library_name}.{binding_name} binding must be an object"
                    )
                raw_path = binding.get("path")
                expected = binding.get("sha256")
                if (
                    not isinstance(raw_path, str)
                    or not isinstance(expected, str)
                    or _SHA256.fullmatch(expected) is None
                ):
                    raise EnvironmentResolutionError("freeze source binding is malformed")
                relative = PurePosixPath(raw_path)
                if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                    raise EnvironmentResolutionError("freeze source path is unsafe")
                source = root.joinpath(*relative.parts)
                _safe_relative_file(source, libraries_root=root, label="freeze source")
                if sha256_file(source) != expected:
                    raise EnvironmentResolutionError(
                        f"generation environment source drifted: {library_name}.{binding_name}"
                    )
        morphology = bindings.get("morphology")
        catalog_binding = (
            morphology.get("scene_asset_catalog")
            if isinstance(morphology, Mapping)
            else None
        )
        if not isinstance(catalog_binding, Mapping):
            raise EnvironmentResolutionError("freeze scene catalog binding is missing")
        catalog_relative = PurePosixPath(str(catalog_binding.get("path", "")))
        catalog_source = root.joinpath(*catalog_relative.parts)
        catalog = load_structured(catalog_source)
        if not isinstance(catalog, Mapping):
            raise EnvironmentResolutionError("freeze scene catalog is malformed")
        if catalog_binding.get("asset_descriptor_sha256") != (
            catalog_asset_descriptor_hashes(catalog)
        ):
            raise EnvironmentResolutionError(
                "generation environment asset descriptor bindings drifted"
            )
        model_binding = (
            morphology.get("robot_model")
            if isinstance(morphology, Mapping)
            else None
        )
        if not isinstance(model_binding, Mapping):
            raise EnvironmentResolutionError("freeze robot model binding is missing")
        model_relative = _safe_posix_reference(
            str(model_binding.get("path", "")),
            label="freeze robot model path",
        )
        verify_robot_model_freeze(
            envelope,
            root.joinpath(*model_relative.parts),
        )
    return {
        "ok": True,
        "freeze_sha256": actual_file_hash,
        "manifest_sha256": envelope["manifest_sha256"],
        "source_rehash_performed": rehash_sources,
    }


__all__ = [
    "EnvironmentResolutionError",
    "MjcfReferencedFile",
    "ResolvedGenerationEnvironment",
    "catalog_asset_descriptor_hashes",
    "discover_mjcf_referenced_files",
    "resolve_generation_environment",
    "verify_generation_environment_freeze",
    "verify_robot_model_freeze",
]
