"""Versioned input-library loading and generation-view isolation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import atomic_write_json, sha256_file
from .schema_validation import non_finite_json_issues, validate_json_schema

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced with a clear runtime error
    yaml = None


class LibraryError(RuntimeError):
    pass


class ExperienceNotFoundError(LibraryError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is not allowed")


def _strict_json_loads(text: str, *, source: str | Path) -> Any:
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LibraryError(f"invalid JSON in {source}: {exc}") from exc
    issues = non_finite_json_issues(value)
    if issues:
        raise LibraryError(
            f"invalid JSON in {source}: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    return value


def load_structured(path: str | Path) -> Any:
    source = Path(path)
    if source.suffix == ".json":
        return _strict_json_loads(source.read_text(encoding="utf-8"), source=source)
    if source.suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise LibraryError("PyYAML is required to read YAML library manifests")
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
        issues = non_finite_json_issues(value)
        if issues:
            raise LibraryError(
                f"invalid non-finite value in {source}: "
                + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
            )
        return value
    raise LibraryError(f"unsupported structured file: {source}")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = _strict_json_loads(line, source=f"{path}:{number}")
            if not isinstance(value, dict):
                raise LibraryError(f"{path}:{number}: JSONL record must be an object")
            records.append(value)
    return records


@dataclass(frozen=True)
class LibraryPayload:
    path: str
    visibility: str
    required: bool = True
    media_type: str | None = None


@dataclass
class LibraryEntry:
    root: Path
    manifest: dict[str, Any]

    @classmethod
    def open(cls, root: str | Path) -> "LibraryEntry":
        entry_root = Path(root).resolve()
        manifest_path = entry_root / "manifest.yaml"
        if not manifest_path.is_file():
            raise LibraryError(f"missing manifest: {manifest_path}")
        manifest = load_structured(manifest_path)
        if not isinstance(manifest, dict):
            raise LibraryError(f"manifest must be an object: {manifest_path}")
        return cls(root=entry_root, manifest=manifest)

    @property
    def payloads(self) -> list[LibraryPayload]:
        output: list[LibraryPayload] = []
        for value in self.manifest.get("payloads", []):
            output.append(
                LibraryPayload(
                    path=str(value["path"]),
                    visibility=str(value["visibility"]),
                    required=bool(value.get("required", True)),
                    media_type=value.get("media_type"),
                )
            )
        return output

    def resolve_payload(self, payload: LibraryPayload) -> Path:
        candidate = (self.root / payload.path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise LibraryError(f"payload escapes entry root: {payload.path}")
        return candidate

    def verify(self) -> dict[str, Any]:
        errors: list[str] = []
        computed: dict[str, str] = {}
        for payload in self.payloads:
            path = self.resolve_payload(payload)
            if payload.required and not path.exists():
                errors.append(f"missing required payload: {payload.path}")
                continue
            if path.is_file():
                computed[payload.path] = sha256_file(path)
                expected = self.manifest.get("content_hashes", {}).get(payload.path)
                if expected and expected != computed[payload.path]:
                    errors.append(f"hash mismatch: {payload.path}")
        return {
            "ok": not errors,
            "entry_id": self.manifest.get("entry_id"),
            "version": self.manifest.get("version"),
            "errors": errors,
            "computed_hashes": computed,
        }

    def validate_runtime_schemas(self, schemas_root: str | Path) -> dict[str, Any]:
        """Validate the manifest and type-specific machine-readable payloads.

        This check is deliberately independent from :meth:`verify`: hashes
        prove byte identity, while JSON Schema proves that the bytes still
        satisfy the versioned contracts.  The orchestrator runs both checks
        before any generation-visible file is copied.
        """

        schema_dir = Path(schemas_root).resolve()
        manifest_schema_path = schema_dir / "library_manifest.schema.json"
        manifest_schema = load_structured(manifest_schema_path)
        errors: list[str] = []
        validated: list[dict[str, Any]] = []

        def check(value: Any, schema_path: Path, label: str) -> None:
            schema = load_structured(schema_path)
            if not isinstance(schema, dict):
                raise LibraryError(f"runtime schema must be an object: {schema_path}")
            try:
                issues = validate_json_schema(value, schema, instance_path=label)
            except ValueError as exc:
                raise LibraryError(f"invalid runtime schema {schema_path}: {exc}") from exc
            errors.extend(f"{issue.path}: {issue.message}" for issue in issues)
            validated.append(
                {
                    "instance": label,
                    "schema": schema_path.name,
                    "schema_sha256": sha256_file(schema_path),
                    "ok": not issues,
                }
            )

        check(self.manifest, manifest_schema_path, "manifest.yaml")
        library_type = self.manifest.get("library_type")
        morphology_entry_kind = self.manifest.get("entry_kind", "robot")
        payload_specs = {
            "morphology": (
                ("scene.yaml", "morphology_scene.schema.json", "structured")
                if morphology_entry_kind == "scene"
                else ("kinematics.yaml", "morphology.schema.json", "structured")
            ),
            "sdk_runtime": ("api_surface.yaml", "sdk_runtime.schema.json", "structured"),
            "tasks": ("visible_tasks.jsonl", "task.schema.json", "jsonl"),
            "experience": ("records.jsonl", "experience.schema.json", "jsonl"),
        }
        spec = payload_specs.get(library_type)
        if spec is None:
            errors.append(f"manifest.yaml.library_type: unsupported library type {library_type!r}")
        else:
            relative, schema_name, encoding = spec
            payload_path = self.root / relative
            if not payload_path.is_file():
                errors.append(f"{relative}: required schema-validated payload is missing")
            elif encoding == "structured":
                check(load_structured(payload_path), schema_dir / schema_name, relative)
            else:
                records = load_jsonl(payload_path)
                schema_path = schema_dir / schema_name
                for index, record in enumerate(records, start=1):
                    check(record, schema_path, f"{relative}:{index}")
                if not records:
                    # An empty JSONL library is still validated against a known
                    # schema contract; record the schema hash for auditability.
                    schema = load_structured(schema_path)
                    if not isinstance(schema, dict):
                        raise LibraryError(f"runtime schema must be an object: {schema_path}")
                    try:
                        validate_json_schema({}, schema, instance_path=f"{relative}:<empty-contract-probe>")
                    except ValueError as exc:
                        raise LibraryError(f"invalid runtime schema {schema_path}: {exc}") from exc
                    validated.append(
                        {
                            "instance": relative,
                            "records": 0,
                            "schema": schema_path.name,
                            "schema_sha256": sha256_file(schema_path),
                            "ok": True,
                        }
                    )

        additional_structured_payloads: list[tuple[str, str]] = []
        if library_type == "morphology" and morphology_entry_kind == "robot":
            additional_structured_payloads.append(
                ("attachments.yaml", "morphology_attachments.schema.json")
            )
        elif library_type == "morphology" and morphology_entry_kind == "scene":
            additional_structured_payloads.append(
                (
                    "assets/primitive_catalog.yaml",
                    "morphology_asset_catalog.schema.json",
                )
            )
        elif library_type == "tasks":
            additional_structured_payloads.append(
                (
                    "environment_requirements.yaml",
                    "task_environment_requirements.schema.json",
                )
            )
            additional_structured_payloads.append(
                (
                    "splits/split_manifest.json",
                    "task_split_manifest.schema.json",
                )
            )
        for relative, schema_name in additional_structured_payloads:
            payload_path = self.root / relative
            if not payload_path.is_file():
                errors.append(f"{relative}: required schema-validated payload is missing")
            else:
                check(load_structured(payload_path), schema_dir / schema_name, relative)

        if library_type == "tasks":
            split_path = self.root / "splits/split_manifest.json"
            if split_path.is_file():
                split = load_structured(split_path)
                split_hashes = (
                    split.get("content_hashes")
                    if isinstance(split, dict)
                    else None
                )
                for relative in (
                    "sources.yaml",
                    "taxonomy.yaml",
                    "visible_tasks.jsonl",
                ):
                    expected = (
                        split_hashes.get(relative)
                        if isinstance(split_hashes, dict)
                        else None
                    )
                    actual_path = self.root / relative
                    if not isinstance(expected, str):
                        errors.append(
                            f"splits/split_manifest.json.content_hashes.{relative}: "
                            "missing public payload binding"
                        )
                    elif not actual_path.is_file() or sha256_file(actual_path) != expected:
                        errors.append(
                            f"splits/split_manifest.json.content_hashes.{relative}: "
                            "public task payload hash mismatch"
                        )

        if library_type == "sdk_runtime":
            runtime_contract = self.root / "runtime_contract.yaml"
            if not runtime_contract.is_file():
                errors.append(
                    "runtime_contract.yaml: required schema-validated payload is missing"
                )
            else:
                check(
                    load_structured(runtime_contract),
                    schema_dir / "runtime_contract.schema.json",
                    "runtime_contract.yaml",
                )
            api_probe = self.root / "api_probe.json"
            if not api_probe.is_file():
                errors.append(
                    "api_probe.json: required schema-validated payload is missing"
                )
            else:
                check(
                    load_structured(api_probe),
                    schema_dir / "sdk_api_probe_result.schema.json",
                    "api_probe.json",
                )

        if library_type == "experience":
            mirror = self.root / "experience.schema.json"
            canonical = schema_dir / "experience.schema.json"
            if not mirror.is_file():
                errors.append("experience.schema.json: required local schema mirror is missing")
            elif mirror.read_bytes() != canonical.read_bytes():
                errors.append(
                    "experience.schema.json: local mirror differs from canonical schemas/experience.schema.json"
                )

        return {
            "ok": not errors,
            "entry_id": self.manifest.get("entry_id"),
            "library_type": library_type,
            "errors": errors,
            "validated": validated,
        }

    def materialize_generation_view(self, destination: str | Path) -> dict[str, Any]:
        target = Path(destination)
        if target.exists() and any(target.iterdir()):
            raise LibraryError(f"generation destination must be empty: {target}")
        target.mkdir(parents=True, exist_ok=True)
        copied: dict[str, str] = {}
        for payload in self.payloads:
            if payload.visibility != "generation":
                continue
            source = self.resolve_payload(payload)
            if not source.exists():
                if payload.required:
                    raise LibraryError(f"required generation payload missing: {payload.path}")
                continue
            output = target / payload.path
            output.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, output)
            else:
                shutil.copy2(source, output)
                copied[payload.path] = sha256_file(output)
        snapshot = {
            "schema_version": "robot_capability.generation_snapshot.v1",
            "library_type": self.manifest.get("library_type"),
            "entry_id": self.manifest.get("entry_id"),
            "version": self.manifest.get("version"),
            "files": copied,
        }
        atomic_write_json(target / "snapshot.json", snapshot)
        return snapshot


class ExperienceLibrary:
    """Operational empty-library interface for P0."""

    def __init__(self, records_path: str | Path):
        self.records_path = Path(records_path)

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            load_jsonl(self.records_path),
            key=lambda item: (
                str(item.get("experience_id", "")),
                _semver_key(str(item.get("version", "0.0.0"))),
            ),
        )

    def select(self, ids: list[str]) -> list[dict[str, Any]]:
        if len(ids) != len(set(ids)):
            raise LibraryError("duplicate experience IDs are not allowed")
        index = {str(item.get("experience_id")): item for item in self.list()}
        missing = [identifier for identifier in ids if identifier not in index]
        if missing:
            raise ExperienceNotFoundError(f"experience IDs not found: {missing}")
        return [index[identifier] for identifier in ids]

    def generation_view(self, ids: list[str]) -> list[dict[str, Any]]:
        return [_experience_generation_view(item) for item in self.select(ids)]


def _semver_key(value: str) -> tuple[int, int, int, str]:
    parts = value.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]), int(parts[1]), int(parts[2]), ""
    return 0, 0, 0, value


def _experience_generation_view(record: dict[str, Any]) -> dict[str, Any]:
    """Build the manifest-declared allowlist without copying private evidence."""

    output: dict[str, Any] = {}
    for scalar in ("experience_id", "version", "scope", "generation_summary"):
        if scalar in record:
            output[scalar] = record[scalar]
    nested_allowlist = {
        "failure_summary": ("category", "symptom"),
        "diagnosis": ("hypothesis", "confidence"),
        "recommended_change": ("change_type", "guidance", "must_preserve"),
        "outcome": ("repair_succeeded", "regressions", "reuse_risk"),
    }
    for parent, fields in nested_allowlist.items():
        source = record.get(parent)
        if not isinstance(source, dict):
            continue
        selected = {field: source[field] for field in fields if field in source}
        if selected:
            output[parent] = selected
    return output
