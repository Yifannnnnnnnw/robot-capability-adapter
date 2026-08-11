"""Fail-closed reader for the mainline, robot-scoped Tasks Library.

The Tasks Library contains both public descriptions and Harness-private evaluation
criteria.  This module makes that split explicit: formal Stage 1 can obtain only
the two-field projection, while evaluation code asks separately for its criteria.
Research candidates are available only through the explicitly named review API.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..foundation.errors import ContractError


_SCHEMA_VERSION = "1.0.0"
_INDEX_TYPE = "tasks_library_index"
_CATALOG_TYPE = "task_catalog"
_COLLECTION_TYPE = "demo_task_collection"
_PROJECTION_TYPE = "task_description_projection_template"
_PRIVATE_TYPE = "task_evaluation_private"
_QUEUE_TYPE = "task_candidate_review_queue"


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"Tasks Library {label} must be non-empty text")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"Tasks Library {label} must be an object")
    return dict(value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"Tasks Library {label} must be an array")
    return value


def _unique(values: Iterable[str], label: str) -> list[str]:
    result = list(values)
    if any(not isinstance(value, str) or not value for value in result) or len(set(result)) != len(result):
        raise ContractError(f"Tasks Library {label} must contain unique non-empty identifiers")
    return result


def _artifact(value: Any, artifact_type: str, label: str) -> dict[str, Any]:
    artifact = _object(value, label)
    if artifact.get("artifact_type") != artifact_type:
        raise ContractError(f"Tasks Library {label} has wrong artifact_type")
    if artifact.get("schema_version") != _SCHEMA_VERSION:
        raise ContractError(f"Tasks Library {label} has unsupported schema_version")
    return artifact


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            return _object(json.load(handle), label)
    except FileNotFoundError as exc:
        raise ContractError(f"Tasks Library {label} is missing") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"Tasks Library {label} is invalid JSON") from exc


def _safe_artifact_path(root: Path, relative_path: Any, label: str) -> Path:
    raw = _text(relative_path, label)
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts or any(part in {"", "."} for part in candidate.parts):
        raise ContractError(f"Tasks Library {label} must be a safe relative path")
    if candidate.suffix != ".json":
        raise ContractError(f"Tasks Library {label} must name a JSON artifact")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*candidate.parts)).resolve()
    if resolved_root not in resolved.parents:
        raise ContractError(f"Tasks Library {label} escapes the library root")
    return resolved


@dataclass(frozen=True)
class TaskLibraryPackage:
    """A validated exact catalog/configuration/version selection."""

    robot_configuration_id: str
    catalog_id: str
    catalog_version: str
    _demo_task_templates: tuple[dict[str, str], ...]
    _demo_private_criteria: tuple[dict[str, Any], ...]
    _review_candidates: tuple[dict[str, Any], ...]

    def _opaque_requirement_id(self, run_id: str, task_id: str) -> str:
        """Make a deterministic, run-local reference with no Library identity in it."""
        run = _text(run_id, "run_id")
        material = "\0".join((
            "autoadapter2-task-requirement-ref-v1",
            self.catalog_id,
            self.catalog_version,
            run,
            task_id,
        )).encode("utf-8")
        return f"req-{hashlib.sha256(material).hexdigest()[:32]}"

    def stage1_projection(self, run_id: str) -> list[dict[str, str]]:
        """Return the sole formal Stage 1 task view for one run.

        Static Library task keys are internal curation/assembly keys.  The model
        sees only a run-local opaque requirement reference and a description.
        """
        return [
            {"requirement_id": self._opaque_requirement_id(run_id, item["task_id"]), "description": item["description"]}
            for item in self._demo_task_templates
        ]

    def demo_public_tasks(self, run_id: str) -> list[dict[str, str]]:
        """Return public Demo descriptions using the same run-local mapping as Stage 1."""
        return [
            {
                "task_id": item["task_id"],
                "requirement_id": self._opaque_requirement_id(run_id, item["task_id"]),
                "description": item["description"],
            }
            for item in self._demo_task_templates
        ]

    def demo_private_criteria(self) -> list[dict[str, Any]]:
        """Return Harness-only criteria in the frozen fixed-Demo task order."""
        return copy.deepcopy(list(self._demo_private_criteria))

    def review_candidates(self, task_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Return unadmitted research records only for human review.

        The caller must use this explicit API; candidates are never merged into any
        formal or Demo selection.  Named IDs fail closed when they are unknown.
        """
        candidates = {item["task_id"]: item for item in self._review_candidates}
        if task_ids is None:
            return copy.deepcopy(list(self._review_candidates))
        requested = _unique(task_ids, "review candidate task_ids")
        unknown = set(requested) - set(candidates)
        if unknown:
            raise ContractError(f"Tasks Library unknown review candidate IDs: {sorted(unknown)}")
        return copy.deepcopy([candidates[task_id] for task_id in requested])


class TasksLibrary:
    """Resolve exact configuration-scoped Tasks Library packages from ``index.json``."""

    def __init__(self, root: str | Path):
        self._root = Path(root)
        if not self._root.is_dir():
            raise ContractError("Tasks Library root must be an existing directory")

    def load(self, robot_configuration_id: str, catalog_version: str) -> TaskLibraryPackage:
        configuration = _text(robot_configuration_id, "robot_configuration_id")
        version = _text(catalog_version, "catalog_version")
        index = _artifact(_read_json(self._root / "index.json", "index.json"), _INDEX_TYPE, "index.json")
        records = _list(index.get("catalogs"), "index catalogs")
        matching = [
            _object(record, "index catalog record")
            for record in records
            if isinstance(record, Mapping)
            and record.get("robot_configuration_id") == configuration
            and record.get("catalog_version") == version
        ]
        if len(matching) != 1:
            raise ContractError("Tasks Library requires exactly one matching configuration/version catalog")
        record = matching[0]
        self._validate_index_record(record, configuration, version)

        expected_directory = PurePosixPath(configuration) / version
        artifacts: dict[str, dict[str, Any]] = {}
        for field, artifact_type in (
            ("catalog_path", _CATALOG_TYPE),
            ("demo_collection_path", _COLLECTION_TYPE),
            ("stage1_projection_path", _PROJECTION_TYPE),
            ("evaluation_private_path", _PRIVATE_TYPE),
        ):
            path = _safe_artifact_path(self._root, record.get(field), f"index {field}")
            try:
                rel = path.relative_to(self._root.resolve())
            except ValueError as exc:  # Defensive; _safe_artifact_path already checks this.
                raise ContractError(f"Tasks Library index {field} escapes the library root") from exc
            if PurePosixPath(rel.as_posix()).parent != expected_directory:
                raise ContractError(f"Tasks Library index {field} must stay in its exact configuration/version directory")
            artifacts[field] = _artifact(_read_json(path, field), artifact_type, field)

        queue: dict[str, Any] | None = None
        queue_path = record.get("candidate_review_queue_path")
        if queue_path is not None:
            path = _safe_artifact_path(self._root, queue_path, "index candidate_review_queue_path")
            if PurePosixPath(path.relative_to(self._root.resolve()).as_posix()).parent != expected_directory:
                raise ContractError("Tasks Library candidate_review_queue_path must stay in its exact configuration/version directory")
            queue = _artifact(_read_json(path, "candidate_review_queue_path"), _QUEUE_TYPE, "candidate_review_queue_path")
        elif int(record["candidate_task_count"]) != 0:
            raise ContractError("Tasks Library nonzero candidate count requires a review queue")

        return self._validate_package(record, artifacts, queue)

    @staticmethod
    def _validate_index_record(record: dict[str, Any], configuration: str, version: str) -> None:
        required_text = ("catalog_id", "catalog_path", "demo_collection_path", "stage1_projection_path", "evaluation_private_path")
        for field in required_text:
            _text(record.get(field), f"index {field}")
        if record.get("robot_configuration_id") != configuration or record.get("catalog_version") != version:
            raise ContractError("Tasks Library index selection changed during resolution")
        for field in ("task_count", "human_approved_task_count", "candidate_task_count", "fixed_demo_task_count"):
            value = record.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(f"Tasks Library index {field} must be a non-negative integer")
        if record["task_count"] != record["human_approved_task_count"]:
            raise ContractError("Tasks Library admitted task_count must equal human_approved_task_count")
        if record["fixed_demo_task_count"] != 5:
            raise ContractError("Tasks Library first Demo requires exactly five fixed tasks")
        if record.get("total_researched_record_count") is not None:
            if record["total_researched_record_count"] != record["task_count"] + record["candidate_task_count"]:
                raise ContractError("Tasks Library total researched count does not match admitted plus candidate counts")

    def _validate_package(
        self,
        record: dict[str, Any],
        artifacts: dict[str, dict[str, Any]],
        queue: dict[str, Any] | None,
    ) -> TaskLibraryPackage:
        catalog = artifacts["catalog_path"]
        collection = artifacts["demo_collection_path"]
        projection = artifacts["stage1_projection_path"]
        private = artifacts["evaluation_private_path"]
        configuration = record["robot_configuration_id"]
        catalog_id = record["catalog_id"]
        version = record["catalog_version"]

        for label, artifact in (("catalog", catalog), ("collection", collection), ("projection", projection), ("private evaluation", private)):
            if artifact.get("robot_configuration_id") != configuration:
                raise ContractError(f"Tasks Library {label} belongs to another robot configuration")
        if catalog.get("catalog_id") != catalog_id or catalog.get("catalog_version") != version:
            raise ContractError("Tasks Library catalog identity does not match index")
        if catalog.get("catalog_review_status") != "HUMAN_APPROVED":
            raise ContractError("Tasks Library selected catalog is not human approved")
        if collection.get("catalog_id") != catalog_id or collection.get("catalog_version") != version:
            raise ContractError("Tasks Library collection does not bind the selected catalog")
        if collection.get("review_status") != "HUMAN_APPROVED":
            raise ContractError("Tasks Library Demo collection is not human approved")

        catalog_tasks = _list(catalog.get("tasks"), "catalog tasks")
        if len(catalog_tasks) != record["task_count"]:
            raise ContractError("Tasks Library catalog task count does not match index")
        task_by_id: dict[str, dict[str, Any]] = {}
        for item in catalog_tasks:
            task = _object(item, "catalog task")
            task_id = _text(task.get("task_id"), "catalog task_id")
            _text(task.get("description"), "catalog description")
            if task.get("task_review_status") != "HUMAN_APPROVED":
                raise ContractError(
                    f"Tasks Library catalog task {task_id} is not human approved"
                )
            if task_id in task_by_id:
                raise ContractError("Tasks Library catalog task_id values must be unique")
            task_by_id[task_id] = task

        demo_ids = _unique(_list(collection.get("task_ids"), "demo collection task_ids"), "demo collection task_ids")
        if len(demo_ids) != record["fixed_demo_task_count"]:
            raise ContractError("Tasks Library demo task count does not match index")
        unknown_demo = set(demo_ids) - set(task_by_id)
        if unknown_demo:
            raise ContractError(f"Tasks Library Demo references unknown catalog task IDs: {sorted(unknown_demo)}")

        if projection.get("source_collection_id") != collection.get("collection_id") or projection.get("source_collection_version") != collection.get("collection_version"):
            raise ContractError("Tasks Library Stage 1 projection does not bind its Demo collection")
        projection_tasks = _list(projection.get("tasks"), "Stage 1 projection tasks")
        if len(projection_tasks) != len(demo_ids):
            raise ContractError("Tasks Library Stage 1 projection count does not match Demo collection")
        demo_task_templates: list[dict[str, str]] = []
        for task_id, raw_task in zip(demo_ids, projection_tasks, strict=True):
            task = _object(raw_task, "Stage 1 projection task")
            if set(task) != {"task_id", "description"}:
                raise ContractError("Tasks Library Stage 1 template may contain only task_id and description")
            if _text(task["task_id"], "Stage 1 template task_id") != task_id:
                raise ContractError("Tasks Library Stage 1 template task_id does not match Demo collection order")
            description = _text(task.get("description"), "Stage 1 projection description")
            source = task_by_id[task_id]
            if description != source["description"]:
                raise ContractError("Tasks Library Stage 1 template must exactly match its admitted catalog task")
            demo_task_templates.append({"task_id": task_id, "description": description})

        if private.get("catalog_id") != catalog_id or private.get("catalog_version") != version:
            raise ContractError("Tasks Library private evaluation does not bind the selected catalog")
        criteria = _list(private.get("criteria"), "private evaluation criteria")
        if len(criteria) != len(task_by_id):
            raise ContractError("Tasks Library private criteria count must match the admitted catalog")
        criterion_by_task: dict[str, dict[str, Any]] = {}
        for raw_criterion in criteria:
            criterion = _object(raw_criterion, "private criterion")
            task_id = _text(criterion.get("task_id"), "private criterion task_id")
            if task_id not in task_by_id:
                raise ContractError(f"Tasks Library private criterion references unknown catalog task {task_id}")
            if task_id in criterion_by_task:
                raise ContractError("Tasks Library private criterion task_ids must be unique")
            if not _text(criterion.get("criterion_status"), "private criterion status").startswith("HUMAN_APPROVED"):
                raise ContractError("Tasks Library private criterion is not human approved")
            criterion_by_task[task_id] = criterion
        if set(criterion_by_task) != set(task_by_id):
            raise ContractError("Tasks Library private criteria must cover every admitted catalog task exactly once")

        review_candidates: list[dict[str, Any]] = []
        if queue is not None:
            if queue.get("robot_configuration_id") != configuration:
                raise ContractError("Tasks Library review queue belongs to another robot configuration")
            if queue.get("queue_status") != "NOT_ADMITTED_REVIEW_REQUIRED" or queue.get("visibility") != "RESEARCH_REVIEW_ONLY":
                raise ContractError("Tasks Library review queue is not restricted to unadmitted review")
            candidates = _list(queue.get("candidates"), "review queue candidates")
            if len(candidates) != record["candidate_task_count"]:
                raise ContractError("Tasks Library review candidate count does not match index")
            candidate_ids: set[str] = set()
            for raw_candidate in candidates:
                candidate = _object(raw_candidate, "review candidate")
                task_id = _text(candidate.get("task_id"), "review candidate task_id")
                _text(candidate.get("description"), "review candidate description")
                if task_id in candidate_ids:
                    raise ContractError("Tasks Library review candidate IDs must be unique")
                if task_id in task_by_id:
                    raise ContractError("Tasks Library review candidate duplicates an admitted task")
                if (
                    candidate.get("difficulty_status") != "PROPOSED_REVIEW_REQUIRED"
                    or candidate.get("private_criterion_status")
                    != "PROPOSED_REVIEW_REQUIRED"
                ):
                    raise ContractError(
                        "Tasks Library review candidate is not explicitly unapproved"
                    )
                candidate_ids.add(task_id)
                review_candidates.append(candidate)
            if set(demo_ids) & candidate_ids:
                raise ContractError("Tasks Library Demo cannot contain review candidates")
        elif record["candidate_task_count"]:
            raise ContractError("Tasks Library candidate count has no review queue")

        return TaskLibraryPackage(
            robot_configuration_id=configuration,
            catalog_id=catalog_id,
            catalog_version=version,
            _demo_task_templates=tuple(demo_task_templates),
            _demo_private_criteria=tuple(criterion_by_task[task_id] for task_id in demo_ids),
            _review_candidates=tuple(review_candidates),
        )
