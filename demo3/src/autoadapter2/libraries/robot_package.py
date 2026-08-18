"""Admission checks for one self-contained Direct-MuJoCo robot package."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RobotPackageError(ValueError):
    """Raised when a robot package is incomplete or internally inconsistent."""


_SUPPORTED_BINDING_KINDS = {
    "final_site_position_error",
    "final_site_axis_error",
    "final_weighted_site_position_error",
    "final_body_position_error",
    "final_joint_position_error",
    "joint_range",
    "body_height",
    "minimum_body_height",
    "body_planar_displacement",
    "body_axis_displacement",
    "body_directional_displacement",
    "mean_body_planar_speed",
    "body_yaw_change_deg",
    "mean_body_heading_error_deg",
    "named_bodies_axis_completion",
    "mean_body_yaw_rate",
    "ordered_body_waypoint_completion_ratio",
    "ordered_body_waypoint_completion_time",
    "ordered_body_axis_gate_completion_ratio",
    "minimum_body_point_clearance",
    "named_geom_contact_step_count",
    "contact_sample_count",
    "physics_step_count",
}
_REQUIRED_GUARD_KINDS = {
    "actuator_and_physics_step_required",
    "no_direct_state_write",
    "canonical_model_data",
}
_SUPPORTED_GUARD_KINDS = _REQUIRED_GUARD_KINDS | {"complete_video"}


@dataclass(frozen=True)
class RobotPackage:
    """A package that passed the minimum Demo3 runnable-input checks."""

    root: Path
    robot_configuration_id: str
    package_version: str
    snapshot_id: str
    morphology: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    tasks: tuple[dict[str, Any], ...]
    mjcf_path: Path
    skeleton_dir: Path
    reference_driver: Path
    private_dir: Path


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RobotPackageError(f"required package file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RobotPackageError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise RobotPackageError(f"{path} must contain one JSON object")
    return value


def _required_text(value: dict[str, Any], field: str, *, where: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RobotPackageError(f"{where}.{field} must be a non-empty string")
    return item.strip()


def _required_list(value: dict[str, Any], field: str, *, where: str) -> list[Any]:
    item = value.get(field)
    if not isinstance(item, list) or not item:
        raise RobotPackageError(f"{where}.{field} must be a non-empty list")
    return item


def _validate_identity(
    document: dict[str, Any],
    *,
    path: Path,
    robot_configuration_id: str,
    package_version: str,
) -> None:
    if document.get("robot_configuration_id") != robot_configuration_id:
        raise RobotPackageError(f"robot identity mismatch in {path}")
    if document.get("package_version") != package_version:
        raise RobotPackageError(f"package version mismatch in {path}")


def _validate_sources(
    document: dict[str, Any],
    *,
    path: Path,
) -> tuple[dict[str, Any], ...]:
    values = _required_list(document, "sources", where=path.name)
    sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, value in enumerate(values):
        where = f"{path.name}.sources[{index}]"
        if not isinstance(value, dict):
            raise RobotPackageError(f"{where} must be an object")
        source_id = _required_text(value, "source_id", where=where)
        if source_id in source_ids:
            raise RobotPackageError(f"duplicate source_id {source_id!r}")
        for field in (
            "title",
            "organization",
            "version_or_date",
            "locator",
            "specific_reference",
        ):
            _required_text(value, field, where=where)
        locator = str(value["locator"])
        if not locator.startswith(("https://", "http://")):
            raise RobotPackageError(f"{where}.locator must be an HTTP(S) citation")
        lowered_locator = locator.lower()
        if "github.com" in lowered_locator:
            version = str(value["version_or_date"]).lower()
            floating = any(
                marker in lowered_locator or marker in version
                for marker in (
                    "/blob/main/",
                    "/blob/master/",
                    "/tree/main/",
                    "/tree/master/",
                    "main branch",
                    "master branch",
                )
            )
            pinned = bool(
                re.search(r"(?<![0-9a-f])[0-9a-f]{12,40}(?![0-9a-f])", locator + " " + version)
                or "/releases/tag/" in lowered_locator
            )
            if floating or not pinned:
                raise RobotPackageError(f"{where}.locator must pin a GitHub revision")
        source_ids.add(source_id)
        sources.append(value)
    return tuple(sources)


def _validate_source_refs(
    clause: dict[str, Any],
    *,
    where: str,
    source_ids: set[str],
) -> None:
    refs = _required_list(clause, "source_refs", where=where)
    for index, ref in enumerate(refs):
        ref_where = f"{where}.source_refs[{index}]"
        if not isinstance(ref, dict):
            raise RobotPackageError(f"{ref_where} must be an object")
        source_id = _required_text(ref, "source_id", where=ref_where)
        if source_id not in source_ids:
            raise RobotPackageError(f"{ref_where} references unknown source {source_id!r}")
        _required_text(ref, "specific_reference", where=ref_where)
        support = _required_text(ref, "support", where=ref_where)
        if support not in {"direct", "adapted"}:
            raise RobotPackageError(f"{ref_where}.support must be direct or adapted")
        if support == "adapted":
            _required_text(ref, "adaptation", where=ref_where)


def _validate_scoring_clause(
    clause: dict[str, Any],
    *,
    where: str,
    source_ids: set[str],
) -> None:
    for field in ("clause_id", "metric", "unit", "comparator"):
        _required_text(clause, field, where=where)
    if clause["comparator"] not in {"<", "<=", ">", ">=", "==", "between"}:
        raise RobotPackageError(f"{where}.comparator is unsupported")
    if "threshold" not in clause:
        raise RobotPackageError(f"{where}.threshold is required")
    threshold = clause["threshold"]
    valid_number = isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
    valid_range = (
        isinstance(threshold, list)
        and len(threshold) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in threshold)
    )
    if not (valid_number or valid_range):
        raise RobotPackageError(f"{where}.threshold must be numeric or a two-number range")
    if not isinstance(clause.get("temporal"), dict):
        raise RobotPackageError(f"{where}.temporal must be an object")
    _required_text(clause["temporal"], "kind", where=f"{where}.temporal")
    if not isinstance(clause.get("aggregation"), dict):
        raise RobotPackageError(f"{where}.aggregation must be an object")
    _required_text(clause["aggregation"], "kind", where=f"{where}.aggregation")
    _validate_source_refs(clause, where=where, source_ids=source_ids)


def _validate_tasks(
    document: dict[str, Any],
    *,
    path: Path,
    source_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    values = _required_list(document, "tasks", where=path.name)
    if len(values) < 20:
        raise RobotPackageError("Task Library must contain at least 20 tasks")
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for index, value in enumerate(values):
        where = f"{path.name}.tasks[{index}]"
        if not isinstance(value, dict):
            raise RobotPackageError(f"{where} must be an object")
        task_id = _required_text(value, "task_id", where=where)
        if task_id in task_ids:
            raise RobotPackageError(f"duplicate task_id {task_id!r}")
        for field in (
            "name",
            "description",
            "source_task_or_operation",
            "applicability",
            "adaptation",
        ):
            _required_text(value, field, where=where)
        invocation = value.get("invocation_schema")
        if not isinstance(invocation, dict):
            raise RobotPackageError(f"{where}.invocation_schema must be an object")
        if invocation.get("envelope") != "request" or invocation.get("required") != ["request"]:
            raise RobotPackageError(f"{where}.invocation_schema must use the request envelope")
        request = invocation.get("request")
        if not isinstance(request, dict):
            raise RobotPackageError(f"{where}.invocation_schema.request must be an object")
        if request.get("type") != "object" or request.get("required") != [
            "task_id",
            "task_parameters",
        ]:
            raise RobotPackageError(
                f"{where}.invocation_schema.request must require task_id and task_parameters"
            )
        if request.get("task_id") != task_id:
            raise RobotPackageError(f"{where}.invocation_schema.request.task_id must match task_id")
        parameters = request.get("task_parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise RobotPackageError(
                f"{where}.invocation_schema.request.task_parameters must be an object schema"
            )
        required_parameters = parameters.get("required")
        properties = parameters.get("properties")
        if not isinstance(required_parameters, list) or not required_parameters:
            raise RobotPackageError(
                f"{where}.invocation_schema task_parameters must declare required fields"
            )
        if not isinstance(properties, dict) or any(
            not isinstance(name, str) or name not in properties for name in required_parameters
        ):
            raise RobotPackageError(
                f"{where}.invocation_schema required task parameters need property schemas"
            )
        _required_list(value, "scene_assumptions", where=where)
        _required_list(value, "observation_assumptions", where=where)
        clauses = _required_list(value, "scoring", where=where)
        clause_ids: set[str] = set()
        for clause_index, clause in enumerate(clauses):
            clause_where = f"{where}.scoring[{clause_index}]"
            if not isinstance(clause, dict):
                raise RobotPackageError(f"{clause_where} must be an object")
            _validate_scoring_clause(clause, where=clause_where, source_ids=source_ids)
            clause_id = str(clause["clause_id"])
            if clause_id in clause_ids:
                raise RobotPackageError(f"duplicate clause_id {clause_id!r} in {task_id!r}")
            clause_ids.add(clause_id)
        task_ids.add(task_id)
        tasks.append(value)
    return tuple(tasks)


def _contained_path(root: Path, relative: str, *, field: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise RobotPackageError(f"{field} must be package-relative")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RobotPackageError(f"{field} escapes the robot package") from exc
    return resolved


def _validate_mjcf(path: Path) -> None:
    if not path.is_file():
        raise RobotPackageError(f"MJCF entrypoint is missing: {path}")
    try:
        import mujoco

        mujoco.MjModel.from_xml_path(str(path))
    except Exception as exc:
        raise RobotPackageError(f"MJCF closure does not load: {type(exc).__name__}: {exc}") from exc


def _validate_asset_closure(package_root: Path, entrypoint: Path) -> None:
    assets_root = (package_root / "assets").resolve()
    for path in (assets_root, entrypoint):
        try:
            path.resolve(strict=True).relative_to(assets_root)
        except (FileNotFoundError, ValueError) as exc:
            raise RobotPackageError(f"asset path escapes package assets: {path}") from exc
    for path in assets_root.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(assets_root)
            except (FileNotFoundError, ValueError) as exc:
                raise RobotPackageError(f"asset symlink escapes package: {path}") from exc

    pending = [entrypoint]
    visited: set[Path] = set()
    while pending:
        xml_path = pending.pop().resolve()
        if xml_path in visited:
            continue
        visited.add(xml_path)
        try:
            root = ET.parse(xml_path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise RobotPackageError(f"cannot inspect MJCF asset {xml_path}: {exc}") from exc
        for element in root.iter():
            file_value = element.attrib.get("file")
            if not file_value:
                continue
            relative = Path(file_value)
            if relative.is_absolute() or ".." in relative.parts:
                raise RobotPackageError(
                    f"MJCF file reference must stay package-relative: {file_value}"
                )
            if element.tag == "include":
                included = (xml_path.parent / relative).resolve()
                try:
                    included.relative_to(assets_root)
                except ValueError as exc:
                    raise RobotPackageError(
                        f"MJCF include escapes package assets: {file_value}"
                    ) from exc
                if not included.is_file():
                    raise RobotPackageError(f"MJCF include is missing: {included}")
                pending.append(included)


def _validate_private_inputs(
    *,
    package_root: Path,
    private_dir: Path,
    robot_configuration_id: str,
    package_version: str,
    snapshot_id: str,
    tasks: tuple[dict[str, Any], ...],
) -> None:
    documents = {
        name: _read_object(private_dir / f"{name}.json")
        for name in ("instances", "bindings", "guards")
    }
    for name, document in documents.items():
        _validate_identity(
            document,
            path=private_dir / f"{name}.json",
            robot_configuration_id=robot_configuration_id,
            package_version=package_version,
        )
        if document.get("task_snapshot_id") != snapshot_id:
            raise RobotPackageError(f"task snapshot mismatch in private {name}.json")

    binding_values = _required_list(documents["bindings"], "bindings", where="bindings.json")
    bindings: dict[str, dict[str, Any]] = {}
    for index, binding in enumerate(binding_values):
        where = f"bindings.json.bindings[{index}]"
        if not isinstance(binding, dict):
            raise RobotPackageError(f"{where} must be an object")
        binding_id = _required_text(binding, "binding_id", where=where)
        if binding_id in bindings:
            raise RobotPackageError(f"duplicate binding_id {binding_id!r}")
        for field in ("metric", "unit", "kind"):
            _required_text(binding, field, where=where)
        if binding["kind"] not in _SUPPORTED_BINDING_KINDS:
            raise RobotPackageError(f"{where}.kind is not implemented by the trusted Harness")
        if not isinstance(binding.get("parameters"), dict):
            raise RobotPackageError(f"{where}.parameters must be an object")
        bindings[binding_id] = binding

    guard_values = _required_list(documents["guards"], "guards", where="guards.json")
    guards: dict[str, dict[str, Any]] = {}
    for index, guard in enumerate(guard_values):
        where = f"guards.json.guards[{index}]"
        if not isinstance(guard, dict):
            raise RobotPackageError(f"{where} must be an object")
        guard_id = _required_text(guard, "guard_id", where=where)
        kind = _required_text(guard, "kind", where=where)
        if guard_id in guards:
            raise RobotPackageError(f"duplicate guard_id {guard_id!r}")
        if kind not in _SUPPORTED_GUARD_KINDS:
            raise RobotPackageError(f"{where}.kind is not implemented by the trusted Harness")
        guards[guard_id] = guard

    tasks_by_id = {str(task["task_id"]): task for task in tasks}

    def validate_public_arguments(
        public_arguments: Any,
        *,
        task_id: str,
        where: str,
    ) -> None:
        if not isinstance(public_arguments, dict) or set(public_arguments) != {"request"}:
            raise RobotPackageError(f"{where} must contain only request")
        request = public_arguments.get("request")
        if not isinstance(request, dict) or request.get("task_id") != task_id:
            raise RobotPackageError(f"{where}.request has the wrong task_id")
        parameters = request.get("task_parameters")
        if not isinstance(parameters, dict):
            raise RobotPackageError(f"{where}.request.task_parameters must be an object")
        schema_parameters = tasks_by_id[task_id]["invocation_schema"]["request"][
            "task_parameters"
        ]
        missing_parameters = set(schema_parameters["required"]) - set(parameters)
        if missing_parameters:
            raise RobotPackageError(
                f"{where}.request.task_parameters misses {sorted(missing_parameters)}"
            )

    def validate_reset(reset: Any, *, where: str) -> None:
        if not isinstance(reset, dict) or reset.get("kind") not in {"default", "keyframe"}:
            raise RobotPackageError(f"{where} must select a supported Framework reset")
        quaternions = reset.get("body_quaternions", {})
        if not isinstance(quaternions, dict):
            raise RobotPackageError(f"{where}.body_quaternions must be an object")
        for body_name, quaternion in quaternions.items():
            if not isinstance(body_name, str) or not body_name.strip():
                raise RobotPackageError(f"{where}.body_quaternions has an invalid body name")
            if (
                not isinstance(quaternion, list)
                or len(quaternion) != 4
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in quaternion
                )
            ):
                raise RobotPackageError(
                    f"{where}.body_quaternions.{body_name} must be four finite numbers"
                )
            norm = math.sqrt(sum(float(value) ** 2 for value in quaternion))
            if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
                raise RobotPackageError(
                    f"{where}.body_quaternions.{body_name} must be normalized"
                )

    instance_values = _required_list(
        documents["instances"], "instances", where="instances.json"
    )
    instance_ids: set[str] = set()
    covered_tasks: set[str] = set()
    scenes: set[Path] = set()
    for index, instance in enumerate(instance_values):
        where = f"instances.json.instances[{index}]"
        if not isinstance(instance, dict):
            raise RobotPackageError(f"{where} must be an object")
        instance_id = _required_text(instance, "instance_id", where=where)
        task_id = _required_text(instance, "task_id", where=where)
        if instance_id in instance_ids:
            raise RobotPackageError(f"duplicate instance_id {instance_id!r}")
        if task_id not in tasks_by_id:
            raise RobotPackageError(f"{where} references unknown task {task_id!r}")
        if task_id in covered_tasks:
            raise RobotPackageError(f"Demo3 expects one private instance for task {task_id!r}")
        instance_ids.add(instance_id)
        covered_tasks.add(task_id)

        validate_public_arguments(
            instance.get("public_arguments"),
            task_id=task_id,
            where=f"{where}.public_arguments",
        )

        validate_reset(instance.get("reset"), where=f"{where}.reset")
        clause_bindings = instance.get("clause_bindings")
        if not isinstance(clause_bindings, dict):
            raise RobotPackageError(f"{where}.clause_bindings must be an object")
        clauses = {
            str(clause["clause_id"]): clause for clause in tasks_by_id[task_id]["scoring"]
        }
        if set(clause_bindings) != set(clauses):
            raise RobotPackageError(f"{where} must bind every task scoring clause exactly once")
        for clause_id, binding_id in clause_bindings.items():
            binding = bindings.get(str(binding_id))
            if binding is None:
                raise RobotPackageError(f"{where} references unknown binding {binding_id!r}")
            clause = clauses[clause_id]
            if binding["metric"] != clause["metric"] or binding["unit"] != clause["unit"]:
                raise RobotPackageError(f"{where} uses an incompatible binding for {clause_id!r}")

        guard_ids = instance.get("guard_ids")
        if not isinstance(guard_ids, list) or not guard_ids or any(
            not isinstance(guard_id, str) or guard_id not in guards for guard_id in guard_ids
        ):
            raise RobotPackageError(f"{where}.guard_ids must reference known private guards")
        selected_guard_kinds = {str(guards[guard_id]["kind"]) for guard_id in guard_ids}
        if not _REQUIRED_GUARD_KINDS <= selected_guard_kinds:
            raise RobotPackageError(f"{where} omits a required anti-false-pass guard")
        repetitions = instance.get("repetitions")
        timeout_sim_s = instance.get("timeout_sim_s")
        max_steps = instance.get("max_steps")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
            raise RobotPackageError(f"{where}.repetitions must be a positive integer")
        repetition_variants = instance.get("repetition_variants")
        if repetition_variants is not None:
            if not isinstance(repetition_variants, list) or len(repetition_variants) != repetitions:
                raise RobotPackageError(
                    f"{where}.repetition_variants must contain one entry per repetition"
                )
            for variant_index, variant in enumerate(repetition_variants):
                variant_where = f"{where}.repetition_variants[{variant_index}]"
                if not isinstance(variant, dict) or not variant:
                    raise RobotPackageError(f"{variant_where} must be a non-empty object")
                if not set(variant) <= {"public_arguments", "reset"}:
                    raise RobotPackageError(
                        f"{variant_where} may contain only public_arguments and reset"
                    )
                if "public_arguments" in variant:
                    validate_public_arguments(
                        variant["public_arguments"],
                        task_id=task_id,
                        where=f"{variant_where}.public_arguments",
                    )
                if "reset" in variant:
                    validate_reset(variant["reset"], where=f"{variant_where}.reset")
        if (
            isinstance(timeout_sim_s, bool)
            or not isinstance(timeout_sim_s, (int, float))
            or timeout_sim_s <= 0
        ):
            raise RobotPackageError(f"{where}.timeout_sim_s must be positive")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise RobotPackageError(f"{where}.max_steps must be a positive integer")

        scene_relative = _required_text(instance, "scene_entrypoint", where=where)
        scene = _contained_path(package_root, scene_relative, field=f"{where}.scene_entrypoint")
        try:
            scene.relative_to((package_root / "assets").resolve())
        except ValueError as exc:
            raise RobotPackageError(f"{where}.scene_entrypoint must live under assets/") from exc
        scenes.add(scene)

    if covered_tasks != set(tasks_by_id):
        missing = sorted(set(tasks_by_id) - covered_tasks)
        raise RobotPackageError(f"private instances do not cover every task: {missing}")
    for scene in scenes:
        _validate_asset_closure(package_root, scene)
        _validate_mjcf(scene)


def load_robot_package(root: str | Path) -> RobotPackage:
    """Load one package only after its complete runnable inputs pass checks."""

    package_root = Path(root).resolve()
    morphology_path = package_root / "morphology.json"
    sources_path = package_root / "tasks" / "sources.json"
    catalog_path = package_root / "tasks" / "catalog.json"
    private_dir = package_root / "tasks" / "private"

    morphology = _read_object(morphology_path)
    robot_configuration_id = _required_text(
        morphology, "robot_configuration_id", where=morphology_path.name
    )
    package_version = _required_text(morphology, "package_version", where=morphology_path.name)
    mjcf_relative = _required_text(morphology, "mjcf_entrypoint", where=morphology_path.name)

    sources_document = _read_object(sources_path)
    catalog_document = _read_object(catalog_path)
    for document, path in ((sources_document, sources_path), (catalog_document, catalog_path)):
        _validate_identity(
            document,
            path=path,
            robot_configuration_id=robot_configuration_id,
            package_version=package_version,
        )
    sources = _validate_sources(sources_document, path=sources_path)
    tasks = _validate_tasks(
        catalog_document,
        path=catalog_path,
        source_ids={str(item["source_id"]) for item in sources},
    )
    snapshot_id = _required_text(catalog_document, "snapshot_id", where=catalog_path.name)

    _validate_private_inputs(
        package_root=package_root,
        private_dir=private_dir,
        robot_configuration_id=robot_configuration_id,
        package_version=package_version,
        snapshot_id=snapshot_id,
        tasks=tasks,
    )

    skeleton_dir = package_root / "skeleton"
    if not skeleton_dir.is_dir() or not any(skeleton_dir.glob("*.py")):
        raise RobotPackageError("robot package must contain a Python skeleton family")
    reference_driver = package_root / "reference" / "driver.py"
    if not reference_driver.is_file():
        raise RobotPackageError("robot package must contain reference/driver.py")

    mjcf_path = _contained_path(package_root, mjcf_relative, field="mjcf_entrypoint")
    assets_dir = (package_root / "assets").resolve()
    try:
        mjcf_path.relative_to(assets_dir)
    except ValueError as exc:
        raise RobotPackageError("mjcf_entrypoint must live under assets/") from exc
    _validate_asset_closure(package_root, mjcf_path)
    _validate_mjcf(mjcf_path)

    return RobotPackage(
        root=package_root,
        robot_configuration_id=robot_configuration_id,
        package_version=package_version,
        snapshot_id=snapshot_id,
        morphology=morphology,
        sources=sources,
        tasks=tasks,
        mjcf_path=mjcf_path,
        skeleton_dir=skeleton_dir,
        reference_driver=reference_driver,
        private_dir=private_dir,
    )


def load_indexed_robot_package(
    demo_root: str | Path,
    robot_configuration_id: str,
) -> RobotPackage:
    """Resolve only an explicitly indexed runnable package under Demo3."""

    root = Path(demo_root).resolve()
    index_path = root / "libraries" / "robots" / "index.json"
    index = _read_object(index_path)
    robots = index.get("robots")
    if not isinstance(robots, dict):
        raise RobotPackageError("runnable robot index must contain a robots object")
    relative = robots.get(robot_configuration_id)
    if not isinstance(relative, str) or not relative:
        raise RobotPackageError(
            f"robot configuration is not runnable: {robot_configuration_id!r}"
        )
    package_root = _contained_path(
        index_path.parent,
        relative,
        field=f"robot index entry {robot_configuration_id!r}",
    )
    return load_robot_package(package_root)
