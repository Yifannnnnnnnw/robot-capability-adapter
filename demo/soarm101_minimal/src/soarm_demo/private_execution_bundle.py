"""Run-local, hash-locked private inputs for trusted Validation and Demo.

The source task library is consulted once, before the first model call.  All
later trusted consumers re-open this run-local bundle and verify every file
against one detached manifest hash.  The bundle is deliberately outside the
Generation snapshot and its payloads must never be copied into audit logs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .audit import atomic_write_json, sha256_bytes, sha256_file, sha256_json
from .schema_validation import validate_json_schema


FREEZE_SCHEMA_VERSION = "robot_capability.private_execution_bundle_freeze.v1"
MANIFEST_SCHEMA_VERSION = "robot_capability.private_execution_bundle_manifest.v1"
FREEZE_FILENAME = "freeze.json"
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


class PrivateExecutionBundleError(RuntimeError):
    """A private source, frozen manifest, or bundle payload is invalid."""


def _load_json_object_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateExecutionBundleError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PrivateExecutionBundleError(f"{label} must contain one JSON object")
    return value


def _load_jsonl_bytes(payload: bytes, *, label: str) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise PrivateExecutionBundleError(f"{label} is not valid UTF-8 JSONL") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PrivateExecutionBundleError(
                f"{label}:{line_number} is not valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PrivateExecutionBundleError(
                f"{label}:{line_number} must contain one object"
            )
        records.append(value)
    return records


def _task_index(records: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise PrivateExecutionBundleError(f"{label} contains an invalid task_id")
        if task_id in result:
            raise PrivateExecutionBundleError(f"{label} contains duplicate task_id")
        result[task_id] = record
    return result


def _safe_bundle_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not relative
        or "." in pure.parts
        or ".." in pure.parts
        or pure.as_posix() != relative
    ):
        raise PrivateExecutionBundleError("private bundle manifest contains an unsafe path")
    unresolved = root / relative
    if unresolved.is_symlink():
        raise PrivateExecutionBundleError("private bundle payload must not be a symlink")
    target = unresolved.resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise PrivateExecutionBundleError("private bundle payload escaped its root") from exc
    return target


def _read_stable_source(path: Path, *, source_root: Path) -> bytes:
    if path.is_symlink():
        raise PrivateExecutionBundleError("private execution source must not be a symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(source_root.resolve())
    except ValueError as exc:
        raise PrivateExecutionBundleError("private execution source escaped its root") from exc
    if not resolved.is_file():
        raise PrivateExecutionBundleError("required private execution source is missing")
    before = sha256_file(resolved)
    payload = resolved.read_bytes()
    after = sha256_file(resolved)
    if before != after or sha256_bytes(payload) != before:
        raise PrivateExecutionBundleError(
            "private execution source changed while the bundle was materialized"
        )
    return payload


def private_partition_bundle_sha256(source_root: str | Path) -> str:
    """Hash every private source file using the task-manifest convention."""

    root = Path(source_root).resolve()
    if not root.is_dir():
        raise PrivateExecutionBundleError("private task source root is missing")
    records: list[bytes] = []
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise PrivateExecutionBundleError("private task source root is empty")
    for path in files:
        payload = _read_stable_source(path, source_root=root)
        relative = path.resolve().relative_to(root).as_posix()
        records.append(
            relative.encode("utf-8")
            + b"\0"
            + sha256_bytes(payload).encode("ascii")
            + b"\n"
        )
    return sha256_bytes(b"".join(records))


@dataclass(frozen=True)
class PrivateExecutionBundle:
    root: Path
    freeze_path: Path
    freeze_sha256: str
    manifest_sha256: str
    private_partition_source_sha256: str
    files_by_role: Mapping[str, Mapping[str, Any]]
    schema_path: Path

    def verify(self) -> None:
        """Re-hash the freeze, detached manifest, and every private payload."""

        if not _HASH_RE.fullmatch(self.freeze_sha256):
            raise PrivateExecutionBundleError("expected private freeze hash is malformed")
        if self.freeze_path.is_symlink() or not self.freeze_path.is_file():
            raise PrivateExecutionBundleError("private execution freeze is missing or unsafe")
        if sha256_file(self.freeze_path) != self.freeze_sha256:
            raise PrivateExecutionBundleError("private execution freeze hash mismatch")
        freeze = _load_json_object_bytes(
            self.freeze_path.read_bytes(), label="private execution freeze"
        )
        try:
            schema = _load_json_object_bytes(
                self.schema_path.read_bytes(), label="private execution bundle schema"
            )
        except OSError as exc:
            raise PrivateExecutionBundleError(
                "private execution bundle schema is unavailable"
            ) from exc
        issues = validate_json_schema(freeze, schema, instance_path="$private_bundle")
        if issues:
            raise PrivateExecutionBundleError("private execution freeze failed schema validation")
        manifest = freeze["manifest"]
        if sha256_json(manifest) != freeze["manifest_sha256"]:
            raise PrivateExecutionBundleError("private execution detached manifest hash mismatch")
        if freeze["manifest_sha256"] != self.manifest_sha256:
            raise PrivateExecutionBundleError("private execution manifest binding changed")
        source_partition_hash = manifest.get("private_partition_source_sha256")
        if (
            not isinstance(source_partition_hash, str)
            or _HASH_RE.fullmatch(source_partition_hash) is None
            or source_partition_hash != self.private_partition_source_sha256
        ):
            raise PrivateExecutionBundleError(
                "private execution source-partition binding changed"
            )

        observed_roles: dict[str, Mapping[str, Any]] = {}
        observed_paths: set[str] = set()
        for record in manifest["files"]:
            role = str(record["role"])
            relative = str(record["path"])
            if role in observed_roles or relative in observed_paths:
                raise PrivateExecutionBundleError(
                    "private execution manifest contains duplicate roles or paths"
                )
            observed_roles[role] = record
            observed_paths.add(relative)
            target = _safe_bundle_path(self.root, relative)
            if not target.is_file():
                raise PrivateExecutionBundleError("private execution payload is missing")
            payload = target.read_bytes()
            if len(payload) != record["bytes"] or sha256_bytes(payload) != record["sha256"]:
                raise PrivateExecutionBundleError("private execution payload hash mismatch")

        expected_roles = set(self.files_by_role)
        if set(observed_roles) != expected_roles:
            raise PrivateExecutionBundleError("private execution role set changed")
        expected_files = {FREEZE_FILENAME, *observed_paths}
        actual_files = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        if actual_files != expected_files:
            raise PrivateExecutionBundleError("private execution bundle contains uncommitted files")

    def _record(self, role: str) -> Mapping[str, Any]:
        record = self.files_by_role.get(role)
        if not isinstance(record, Mapping):
            raise PrivateExecutionBundleError("private execution role is not frozen")
        return record

    def read_bytes(self, role: str) -> bytes:
        """Verify the whole bundle, then bind the exact bytes returned to a role."""

        self.verify()
        record = self._record(role)
        target = _safe_bundle_path(self.root, str(record["path"]))
        payload = target.read_bytes()
        if len(payload) != record["bytes"] or sha256_bytes(payload) != record["sha256"]:
            raise PrivateExecutionBundleError("private execution payload changed during read")
        return payload

    def read_json(self, role: str) -> dict[str, Any]:
        return _load_json_object_bytes(self.read_bytes(role), label="private execution payload")

    def read_jsonl(self, role: str) -> list[dict[str, Any]]:
        return _load_jsonl_bytes(self.read_bytes(role), label="private execution payload")

    def path_for_role(self, role: str) -> Path:
        """Return a verified path for consumers that require a filesystem path."""

        self.verify()
        return _safe_bundle_path(self.root, str(self._record(role)["path"]))

    def sha256_for_role(self, role: str) -> str:
        digest = self._record(role).get("sha256")
        if not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
            raise PrivateExecutionBundleError("private execution role hash is malformed")
        return digest


def _bundle_from_freeze(
    freeze_path: Path,
    *,
    expected_freeze_sha256: str,
    schema_path: Path,
) -> PrivateExecutionBundle:
    freeze = _load_json_object_bytes(
        freeze_path.read_bytes(), label="private execution freeze"
    )
    manifest = freeze.get("manifest")
    raw_files = manifest.get("files") if isinstance(manifest, Mapping) else None
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
        raise PrivateExecutionBundleError("private execution freeze has no file manifest")
    roles: dict[str, Mapping[str, Any]] = {}
    for raw in raw_files:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("role"), str):
            raise PrivateExecutionBundleError("private execution file record is malformed")
        roles[str(raw["role"])] = dict(raw)
    manifest_hash = freeze.get("manifest_sha256")
    if not isinstance(manifest_hash, str):
        raise PrivateExecutionBundleError("private execution manifest hash is missing")
    bundle = PrivateExecutionBundle(
        root=freeze_path.parent.resolve(),
        freeze_path=freeze_path.resolve(),
        freeze_sha256=expected_freeze_sha256,
        manifest_sha256=manifest_hash,
        private_partition_source_sha256=str(
            manifest.get("private_partition_source_sha256", "")
        ),
        files_by_role=roles,
        schema_path=schema_path.resolve(),
    )
    bundle.verify()
    return bundle


def verify_private_execution_bundle(
    freeze_path: str | Path,
    *,
    expected_freeze_sha256: str,
    schema_path: str | Path,
) -> PrivateExecutionBundle:
    """Open an existing bundle and fail closed on any manifest or file drift."""

    path = Path(freeze_path)
    if path.is_symlink() or not path.is_file():
        raise PrivateExecutionBundleError("private execution freeze is missing or unsafe")
    if sha256_file(path) != expected_freeze_sha256:
        raise PrivateExecutionBundleError("private execution freeze hash mismatch")
    return _bundle_from_freeze(
        path,
        expected_freeze_sha256=expected_freeze_sha256,
        schema_path=Path(schema_path),
    )


def materialize_private_execution_bundle(
    *,
    private_task_root: str | Path,
    visible_tasks_path: str | Path,
    destination: str | Path,
    schema_path: str | Path,
    expected_private_partition_sha256: str | None = None,
) -> PrivateExecutionBundle:
    """Freeze the complete 9+3 task set and the selected 3+3 Demo batch.

    The six selected ``instance_XX`` roles are retained for the Demo runner,
    while the twelve ``authored_instance_XX`` roles make the complete authored
    scene set available to the framework-only preflight.  Role and destination
    names deliberately contain no task identifier.
    """

    source_root = Path(private_task_root).resolve()
    visible_source = Path(visible_tasks_path).resolve()
    source_partition_sha256 = private_partition_bundle_sha256(source_root)
    if expected_private_partition_sha256 is not None:
        if (
            _HASH_RE.fullmatch(expected_private_partition_sha256) is None
            or expected_private_partition_sha256 != source_partition_sha256
        ):
            raise PrivateExecutionBundleError(
                "private task partition does not match its library-manifest hash"
            )
    target_root = Path(destination)
    if target_root.exists():
        raise PrivateExecutionBundleError("private execution bundle destination already exists")
    target_root.mkdir(parents=True, exist_ok=False)

    source_payloads: dict[str, bytes] = {
        "demo_batch": _read_stable_source(
            source_root / "demo_batch.json", source_root=source_root
        ),
        "visible_tasks": _read_stable_source(
            visible_source, source_root=visible_source.parent
        ),
        "heldout_tasks": _read_stable_source(
            source_root / "heldout_tasks.jsonl", source_root=source_root
        ),
        "task_oracles": _read_stable_source(
            source_root / "task_oracles.yaml", source_root=source_root
        ),
        "common_reset": _read_stable_source(
            source_root / "initial_states/_common_reset.json", source_root=source_root
        ),
    }
    batch = _load_json_object_bytes(source_payloads["demo_batch"], label="Demo batch")
    ordered = batch.get("ordered_instances")
    if batch.get("status") != "fixed_before_generation" or not isinstance(ordered, list):
        raise PrivateExecutionBundleError("Demo batch is not fixed before Generation")
    if len(ordered) != 6:
        raise PrivateExecutionBundleError("Demo batch must contain exactly six instances")
    partitions = [entry.get("partition") if isinstance(entry, Mapping) else None for entry in ordered]
    if partitions.count("visible") != 3 or partitions.count("pilot-held-out") != 3:
        raise PrivateExecutionBundleError("Demo batch must select exactly 3 visible + 3 held-out")
    if [entry.get("ordinal") for entry in ordered if isinstance(entry, Mapping)] != list(range(1, 7)):
        raise PrivateExecutionBundleError("Demo batch ordinals must be exactly 1..6")

    visible_records = _load_jsonl_bytes(
        source_payloads["visible_tasks"], label="visible task catalog"
    )
    heldout_records = _load_jsonl_bytes(
        source_payloads["heldout_tasks"], label="held-out task catalog"
    )
    visible_index = _task_index(visible_records, label="visible task catalog")
    heldout_index = _task_index(heldout_records, label="held-out task catalog")
    if len(visible_index) != 9 or len(heldout_index) != 3:
        raise PrivateExecutionBundleError(
            "private execution task catalogs must contain exactly 9 visible + 3 held-out tasks"
        )
    if set(visible_index) & set(heldout_index):
        raise PrivateExecutionBundleError("visible and held-out task IDs must be disjoint")

    all_tasks = {**visible_index, **heldout_index}
    initial_state_root = source_root / "initial_states"
    authored_paths = sorted(
        path
        for path in initial_state_root.glob("*.json")
        if path.name != "_common_reset.json"
    )
    if len(authored_paths) != 12:
        raise PrivateExecutionBundleError(
            "private execution source must contain exactly twelve authored initial states"
        )
    authored_payload_by_task: dict[str, bytes] = {}
    authored_records: list[tuple[str, bytes]] = []
    for ordinal, source_instance in enumerate(authored_paths, start=1):
        payload = _read_stable_source(source_instance, source_root=source_root)
        instance = _load_json_object_bytes(payload, label="authored private instance")
        task_id = instance.get("task_id")
        if (
            not isinstance(task_id, str)
            or task_id not in all_tasks
            or task_id in authored_payload_by_task
            or source_instance.name != f"{task_id}.json"
        ):
            raise PrivateExecutionBundleError(
                "authored initial-state files are not a one-to-one task catalog projection"
            )
        authored_payload_by_task[task_id] = payload
        authored_records.append((f"authored_instance_{ordinal:02d}", payload))
    if set(authored_payload_by_task) != set(all_tasks):
        raise PrivateExecutionBundleError(
            "authored initial states do not cover the complete 9+3 task catalog"
        )

    selected_ids: set[str] = set()
    instance_records: list[tuple[str, bytes]] = []
    for ordinal, entry in enumerate(ordered, start=1):
        if not isinstance(entry, Mapping):
            raise PrivateExecutionBundleError("Demo batch entry must be an object")
        task_id = entry.get("task_id")
        partition = entry.get("partition")
        if not isinstance(task_id, str) or task_id in selected_ids:
            raise PrivateExecutionBundleError("Demo batch task IDs must be valid and unique")
        selected_ids.add(task_id)
        expected_index = visible_index if partition == "visible" else heldout_index
        if task_id not in expected_index:
            raise PrivateExecutionBundleError("Demo batch task is in the wrong frozen partition")
        expected_visibility = (
            "generation_visible" if partition == "visible" else "demo_heldout"
        )
        task = expected_index[task_id]
        split = task.get("split")
        if not isinstance(split, Mapping) or split.get("visibility") != expected_visibility:
            raise PrivateExecutionBundleError("Demo task visibility does not match its partition")
        raw_ref = entry.get("instance_ref")
        ref = PurePosixPath(str(raw_ref))
        if (
            ref.is_absolute()
            or len(ref.parts) != 2
            or ref.parts[0] != "initial_states"
            or ref.parts[1] in {"", ".", "..", "_common_reset.json"}
        ):
            raise PrivateExecutionBundleError("Demo batch contains an unsafe instance_ref")
        if ref.name != f"{task_id}.json":
            raise PrivateExecutionBundleError("Demo instance_ref does not match its task")
        payload = authored_payload_by_task[task_id]
        instance = _load_json_object_bytes(payload, label="private Demo instance")
        if instance.get("task_id") != task_id:
            raise PrivateExecutionBundleError("Demo instance does not match its task")
        instance_records.append((f"instance_{ordinal:02d}", payload))

    destinations = {
        "demo_batch": "data/demo_batch.json",
        "visible_tasks": "data/visible_tasks.jsonl",
        "heldout_tasks": "data/heldout_tasks.jsonl",
        "task_oracles": "data/task_oracles.yaml",
        "common_reset": "data/initial_states/_common_reset.json",
    }
    for role, payload in instance_records:
        source_payloads[role] = payload
        destinations[role] = f"data/initial_states/{role}.json"
    for role, payload in authored_records:
        source_payloads[role] = payload
        destinations[role] = f"data/initial_states/{role}.json"

    records: list[dict[str, Any]] = []
    for role in sorted(source_payloads):
        payload = source_payloads[role]
        relative = destinations[role]
        output = target_root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        os.chmod(output, 0o444)
        records.append(
            {
                "role": role,
                "path": relative,
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": "sha256_raw_bytes",
        "private_partition_source_sha256": source_partition_sha256,
        "privacy": {
            "generation_visible": False,
            "agent_snapshot_member": False,
            "log_payloads_allowed": False,
        },
        "partition_counts": {
            "generation_visible_templates": 9,
            "pilot_heldout_templates": 3,
            "authored_instances": 12,
            "selected_visible_instances": 3,
            "selected_pilot_heldout_instances": 3,
            "selected_instances": 6,
        },
        "files": records,
    }
    if private_partition_bundle_sha256(source_root) != source_partition_sha256:
        raise PrivateExecutionBundleError(
            "private task partition changed while its run-local bundle was materialized"
        )
    freeze = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "manifest": manifest,
        "manifest_sha256": sha256_json(manifest),
    }
    freeze_path = target_root / FREEZE_FILENAME
    atomic_write_json(freeze_path, freeze)
    os.chmod(freeze_path, 0o444)
    freeze_hash = sha256_file(freeze_path)
    return _bundle_from_freeze(
        freeze_path,
        expected_freeze_sha256=freeze_hash,
        schema_path=Path(schema_path),
    )
