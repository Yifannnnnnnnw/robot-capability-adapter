"""Static validation for generated capability packages.

The validator deliberately treats the Stage 2 output as untrusted text.  It
only reads JSON and Python source files and parses Python with :mod:`ast`; it
never imports or executes the generated package.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACKAGE_NAME = "generated_capability_package"
REQUIRED_PACKAGE_FILES = (
    "__init__.py",
    "_kinematics.py",
    "g1.py",
    "g2.py",
    "g3.py",
)
REQUIRED_MANIFEST_VERSION = "robot_capability.package_manifest.v2"
_KINEMATICS_REUSE_SYMBOLS = {"_forward_kinematics", "_solve_ik"}
_HEAVY_KINEMATICS_PREFIXES = ("_forward", "_solve")
_GRIPPERFRAME_SITE_QUATERNION = "_GRIPPERFRAME_QUATERNION_WXYZ"
_TOOL_POINT_FLOW_TAG = "tool_point_in_gripperframe"
_SITE_QUATERNION_FLOW_TAG = "gripperframe_site_quaternion"
_SITE_QUATERNION_MATRIX_FLOW_TAG = "gripperframe_site_quaternion_matrix"
_SITE_ROTATION_FLOW_TAG = "gripperframe_site_rotation"
_SITE_TOOL_OFFSET_FLOW_TAG = "gripperframe_site_tool_offset"
_UNROTATED_TOOL_OFFSET_FLOW_TAG = "unrotated_gripperframe_tool_offset"
_PURE_MATH_BUILTIN_CALLS = {
    "ValueError",
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "len",
    "list",
    "max",
    "min",
    "range",
    "round",
    "set",
    "sorted",
    "sum",
    "tuple",
    "zip",
}
_PYTHON_BUILTIN_NAMES = frozenset(vars(builtins))
_UNRESOLVED_PRIVATE_CALL_MESSAGE = (
    "private call target is unresolved in its lexical scope"
)
_UNRESOLVED_PRIVATE_CALL_TARGET = (
    "a module/local definition, legal import, parameter, or builtin binding"
)
_G3_PRE_CLOSE_GRIPPER_FLOW_CODE = (
    "G3_PRE_CLOSE_GRIPPER_COMMAND_FLOW_INVALID"
)
_G3_PRE_CLOSE_GRIPPER_FLOW_MESSAGE = (
    "G3 pre-close approach/descent actions must preserve the selected "
    "grasp-open aperture command"
)
_G3_PRE_CLOSE_GRIPPER_FLOW_TARGET = {
    "required_dataflow": [
        (
            "selected aperture sample -> direct field arguments, or one explicit "
            "wrapper sample parameter"
        ),
        (
            "the same sample's ['tool_center_in_frame_m'] and ['command'] -> "
            "distinct explicit motion-helper parameters"
        ),
        "command parameter -> every pre-close runtime.send_action gripper.pos",
    ],
    "forbidden_sources": [
        "runtime.get_observation()['gripper.pos']",
        "a constant, initial, stale, or otherwise unproven gripper value",
        "tool center and command selected from different aperture samples",
    ],
}

# Generated capabilities receive a framework-owned runtime object.  They must
# not reach around that boundary to transports, the simulator, or validators.
_FORBIDDEN_IMPORT_ROOTS = {
    "builtins",
    "httpx",
    "importlib",
    "lerobot",
    "mujoco",
    "os",
    "pathlib",
    "requests",
    "serial",
    "socket",
    "subprocess",
    "sys",
    "urllib",
}
_ALLOWED_IMPORT_ROOTS = {"__future__", "math", "time"}
_ALLOWED_TIME_CALLS = {"monotonic", "sleep"}
_FORBIDDEN_IMPORT_PREFIXES = ("soarm_demo.bridge",)
_FORBIDDEN_IMPORT_COMPONENTS = {"oracle", "private", "validation"}
_FORBIDDEN_CALLS = {
    "__import__",
    "breakpoint",
    "classmethod",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "exit",
    "getattr",
    "globals",
    "hasattr",
    "help",
    "input",
    "locals",
    "memoryview",
    "open",
    "property",
    "quit",
    "setattr",
    "staticmethod",
    "super",
    "type",
    "vars",
}
_FORBIDDEN_RUNTIME_LIFECYCLE_CALLS = {
    "calibrate",
    "configure",
    "connect",
    "disconnect",
    "setup_motors",
}
_FORBIDDEN_RUNTIME_PRIVILEGED_CALLS = {
    "advance",
    "body_position",
    "reset",
    "site_position",
    "world_snapshot",
}
_FORBIDDEN_RUNTIME_INTERNAL_ATTRIBUTES = {
    "bus",
    "cameras",
    "data",
    "last_receipt",
    "model",
    "serial",
}
_ALLOWED_RUNTIME_ATTRIBUTES = {"send_action", "get_observation"}
_VALIDATION_EFFECTS_BY_GRANULARITY = {
    "G1": {"joint_targets"},
    "G2": {"cartesian_target"},
    "G3": {
        "object_source_to_target",
        "object_source_plus_height_delta",
        "object_move_sequence",
    },
}


def _coordinate_vector_annotation(annotation: Any) -> bool:
    normalized = str(annotation).replace(" ", "").lower()
    return normalized.startswith(
        (
            "list[float]",
            "tuple[float",
            "sequence[float]",
            "typing.list[float]",
            "typing.tuple[float",
            "typing.sequence[float]",
        )
    )


def _coordinate_scalar_annotation(annotation: Any) -> bool:
    return str(annotation).replace(" ", "").lower() in {
        "float",
        "builtins.float",
    }


@dataclass(frozen=True)
class StaticValidationFailure:
    """One deterministic, repair-oriented static validation failure."""

    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    capability_id: str | None = None
    observed: Any = None
    target: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "capability_id": self.capability_id,
            "observed": self.observed,
            "target": self.target,
        }

    def feedback_message(self) -> str:
        location = self.path or "generated artifact"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        return f"{location}: {self.message}"


@dataclass(frozen=True)
class StaticValidationReport:
    """Structured result returned without loading generated Python code."""

    artifact_root: str
    stage1_sha256: str | None
    checked_files: tuple[str, ...]
    failures: tuple[StaticValidationFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "robot_capability.static_validation_report.v1",
            "passed": self.passed,
            "artifact_root": self.artifact_root,
            "stage1_sha256": self.stage1_sha256,
            "checked_files": list(self.checked_files),
            "failures": [failure.to_dict() for failure in self.failures],
        }

    def to_failure_feedback(self, repair_round: int) -> dict[str, Any] | None:
        """Return a failure-feedback artifact, or ``None`` for a passing run."""

        if not 0 <= repair_round <= 10:
            raise ValueError("repair_round must be between 0 and 10")
        if self.passed:
            return None
        return {
            "schema_version": "robot_capability.failure_feedback.v1",
            "stage": "static",
            "repair_round": repair_round,
            "failures": [
                {
                    "code": failure.code,
                    "message": failure.feedback_message(),
                    "capability_id": failure.capability_id,
                    "case_id": None,
                    "observed": failure.observed,
                    "target": failure.target,
                    "gap": None,
                    "traceback": None,
                }
                for failure in self.failures
            ],
        }


@dataclass(frozen=True)
class _CapabilitySpec:
    capability_id: str
    granularity: str
    module: str
    function_name: str
    validation_effect: str


def compute_stage1_sha256(stage1: Mapping[str, Any]) -> str:
    """Hash a Stage 1 artifact using the demo's canonical JSON convention."""

    canonical = json.dumps(
        stage1,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_generated_package(
    artifact_root: str | Path,
    frozen_stage1: Mapping[str, Any] | str | Path,
) -> StaticValidationReport:
    """Validate one Stage 2 output directory without executing generated code.

    ``artifact_root`` must contain ``package_manifest.json`` and the
    ``generated_capability_package`` directory. ``frozen_stage1`` may be an
    already-loaded mapping or the path to its JSON snapshot.
    """

    root = Path(artifact_root)
    failures: list[StaticValidationFailure] = []
    checked_files: list[str] = []

    _validate_package_inventory(root, failures)

    stage1 = _load_stage1(frozen_stage1, failures)
    expected: dict[str, _CapabilitySpec] = {}
    stage1_sha256: str | None = None
    if stage1 is not None:
        stage1_sha256 = compute_stage1_sha256(stage1)
        expected = _extract_stage1_capabilities(stage1, failures)

    required_paths = [root / "package_manifest.json"] + [
        root / PACKAGE_NAME / filename for filename in REQUIRED_PACKAGE_FILES
    ]
    for path in required_paths:
        if path.is_symlink() or not path.is_file():
            failures.append(
                StaticValidationFailure(
                    code="MISSING_REQUIRED_FILE",
                    message="required Stage 2 artifact is missing",
                    path=_display_path(path, root),
                    observed=False,
                    target=True,
                )
            )

    manifest_path = root / "package_manifest.json"
    manifest: dict[str, Any] | None = None
    manifest_by_id: dict[str, Mapping[str, Any]] = {}
    if manifest_path.is_file() and not manifest_path.is_symlink():
        manifest = _read_json_object(manifest_path, root, failures)
        checked_files.append(_display_path(manifest_path, root))
    if manifest is not None:
        _validate_manifest(manifest, expected, stage1_sha256, failures)
        capabilities = manifest.get("capabilities")
        if isinstance(capabilities, list):
            manifest_by_id = {
                str(item["capability_id"]): item
                for item in capabilities
                if isinstance(item, Mapping) and isinstance(item.get("capability_id"), str)
            }

    parsed_modules: dict[str, ast.Module] = {}
    for filename in REQUIRED_PACKAGE_FILES:
        path = root / PACKAGE_NAME / filename
        if not path.is_file() or path.is_symlink():
            continue
        display_path = _display_path(path, root)
        tree = _parse_python(path, display_path, failures)
        checked_files.append(display_path)
        if tree is None:
            continue
        parsed_modules[filename] = tree
        _validate_ast_safety(tree, display_path, failures)

    support_tree = parsed_modules.get("_kinematics.py")
    init_tree = parsed_modules.get("__init__.py")
    if init_tree is not None:
        _validate_package_init(init_tree, failures)
    if support_tree is not None:
        _validate_private_kinematics_module(support_tree, failures)

    for module_name in ("g1", "g2", "g3"):
        tree = parsed_modules.get(f"{module_name}.py")
        if tree is not None:
            _validate_layer_independence(tree, module_name, failures)
            if module_name in {"g2", "g3"}:
                _validate_kinematics_reuse(
                    tree,
                    module_name,
                    support_tree,
                    failures,
                )
            _validate_capability_functions(
                tree,
                module_name,
                expected,
                manifest_by_id,
                f"{PACKAGE_NAME}/{module_name}.py",
                failures,
            )
            if module_name == "g3":
                _validate_g3_pre_close_gripper_command_flow(
                    tree,
                    {
                        spec.function_name: spec.capability_id
                        for spec in expected.values()
                        if spec.module == "g3"
                    },
                    f"{PACKAGE_NAME}/g3.py",
                    failures,
                )

    return StaticValidationReport(
        artifact_root=str(root),
        stage1_sha256=stage1_sha256,
        checked_files=tuple(checked_files),
        failures=tuple(failures),
    )


def _validate_package_inventory(
    root: Path,
    failures: list[StaticValidationFailure],
) -> None:
    """Require the fixed five-module inventory without following symlinks."""

    manifest = root / "package_manifest.json"
    if manifest.is_symlink():
        failures.append(
            StaticValidationFailure(
                code="GENERATED_SYMLINK_FORBIDDEN",
                message="generated artifacts must be regular files, not symlinks",
                path="package_manifest.json",
            )
        )
    package_dir = root / PACKAGE_NAME
    if package_dir.is_symlink():
        failures.append(
            StaticValidationFailure(
                code="GENERATED_SYMLINK_FORBIDDEN",
                message="generated package directory must not be a symlink",
                path=PACKAGE_NAME,
            )
        )
        return
    if not package_dir.is_dir():
        return
    expected = set(REQUIRED_PACKAGE_FILES)
    try:
        entries = tuple(package_dir.iterdir())
    except OSError as error:
        failures.append(
            StaticValidationFailure(
                code="ARTIFACT_READ_ERROR",
                message=f"could not inspect generated package inventory: {error}",
                path=PACKAGE_NAME,
            )
        )
        return
    for entry in entries:
        relative = f"{PACKAGE_NAME}/{entry.name}"
        if entry.is_symlink():
            failures.append(
                StaticValidationFailure(
                    code="GENERATED_SYMLINK_FORBIDDEN",
                    message="generated artifacts must be regular files, not symlinks",
                    path=relative,
                )
            )
            continue
        if entry.name in expected and entry.is_file():
            continue
        if entry.name == "__pycache__" and entry.is_dir():
            # CPython may create this trusted cache after isolated validation
            # loading; it is excluded from package hashes and never source.
            continue
        failures.append(
            StaticValidationFailure(
                code="EXTRA_GENERATED_FILE",
                message="generated package has a fixed five-Python-file inventory",
                path=relative,
                observed=entry.name,
                target=sorted(expected),
            )
        )


def _load_stage1(
    value: Mapping[str, Any] | str | Path,
    failures: list[StaticValidationFailure],
) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        failures.append(
            StaticValidationFailure(
                code="STAGE1_READ_ERROR",
                message=f"could not read frozen Stage 1 JSON: {error}",
                path=str(path),
            )
        )
        return None
    except json.JSONDecodeError as error:
        failures.append(
            StaticValidationFailure(
                code="STAGE1_INVALID_JSON",
                message=error.msg,
                path=str(path),
                line=error.lineno,
                column=error.colno,
            )
        )
        return None
    if not isinstance(loaded, dict):
        failures.append(
            StaticValidationFailure(
                code="STAGE1_INVALID_STRUCTURE",
                message="frozen Stage 1 artifact must be a JSON object",
                path=str(path),
                observed=type(loaded).__name__,
                target="object",
            )
        )
        return None
    return loaded


def _extract_stage1_capabilities(
    stage1: Mapping[str, Any],
    failures: list[StaticValidationFailure],
) -> dict[str, _CapabilitySpec]:
    expected: dict[str, _CapabilitySpec] = {}
    layers = stage1.get("layers")
    if not isinstance(layers, Mapping):
        failures.append(
            StaticValidationFailure(
                code="STAGE1_INVALID_STRUCTURE",
                message="frozen Stage 1 artifact has no layers object",
                path="stage1.layers",
            )
        )
        return expected
    for granularity in ("G1", "G2", "G3"):
        layer = layers.get(granularity)
        capabilities = layer.get("capabilities") if isinstance(layer, Mapping) else None
        if not isinstance(capabilities, list) or not capabilities:
            failures.append(
                StaticValidationFailure(
                    code="STAGE1_INVALID_STRUCTURE",
                    message=f"{granularity} must contain at least one capability",
                    path=f"stage1.layers.{granularity}.capabilities",
                )
            )
            continue
        for index, capability in enumerate(capabilities):
            path = f"stage1.layers.{granularity}.capabilities[{index}]"
            if not isinstance(capability, Mapping):
                failures.append(
                    StaticValidationFailure(
                        code="STAGE1_INVALID_STRUCTURE",
                        message="capability must be an object",
                        path=path,
                    )
                )
                continue
            capability_id = capability.get("capability_id")
            function_name = capability.get("function_name")
            validation_effect = capability.get("validation_effect")
            if (
                not isinstance(capability_id, str)
                or not isinstance(function_name, str)
                or validation_effect not in _VALIDATION_EFFECTS_BY_GRANULARITY[granularity]
            ):
                failures.append(
                    StaticValidationFailure(
                        code="STAGE1_INVALID_STRUCTURE",
                        message=(
                            "capability_id/function_name must be strings and "
                            "validation_effect must match the granularity"
                        ),
                        path=path,
                    )
                )
                continue
            if capability_id in expected:
                failures.append(
                    StaticValidationFailure(
                        code="STAGE1_DUPLICATE_CAPABILITY",
                        message="capability_id is duplicated in frozen Stage 1",
                        path=path,
                        capability_id=capability_id,
                    )
                )
                continue
            expected[capability_id] = _CapabilitySpec(
                capability_id=capability_id,
                granularity=granularity,
                module=granularity.lower(),
                function_name=function_name,
                validation_effect=str(validation_effect),
            )
    return expected


def _read_json_object(
    path: Path,
    root: Path,
    failures: list[StaticValidationFailure],
) -> dict[str, Any] | None:
    display_path = _display_path(path, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        failures.append(
            StaticValidationFailure(
                code="ARTIFACT_READ_ERROR",
                message=f"could not read file: {error}",
                path=display_path,
            )
        )
        return None
    except json.JSONDecodeError as error:
        failures.append(
            StaticValidationFailure(
                code="INVALID_JSON",
                message=error.msg,
                path=display_path,
                line=error.lineno,
                column=error.colno,
            )
        )
        return None
    if not isinstance(value, dict):
        failures.append(
            StaticValidationFailure(
                code="INVALID_MANIFEST",
                message="package manifest must be a JSON object",
                path=display_path,
                observed=type(value).__name__,
                target="object",
            )
        )
        return None
    return value


def _validate_manifest(
    manifest: Mapping[str, Any],
    expected: Mapping[str, _CapabilitySpec],
    stage1_sha256: str | None,
    failures: list[StaticValidationFailure],
) -> None:
    path = "package_manifest.json"
    if manifest.get("schema_version") != REQUIRED_MANIFEST_VERSION:
        failures.append(
            StaticValidationFailure(
                code="INVALID_MANIFEST",
                message="unexpected package manifest schema_version",
                path=path,
                observed=manifest.get("schema_version"),
                target=REQUIRED_MANIFEST_VERSION,
            )
        )
    if manifest.get("package") != PACKAGE_NAME:
        failures.append(
            StaticValidationFailure(
                code="INVALID_MANIFEST",
                message="manifest package must name the generated package",
                path=path,
                observed=manifest.get("package"),
                target=PACKAGE_NAME,
            )
        )
    if stage1_sha256 is not None and manifest.get("stage1_sha256") != stage1_sha256:
        failures.append(
            StaticValidationFailure(
                code="STAGE1_HASH_MISMATCH",
                message="manifest is not bound to the frozen Stage 1 artifact",
                path=path,
                observed=manifest.get("stage1_sha256"),
                target=stage1_sha256,
            )
        )

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list):
        failures.append(
            StaticValidationFailure(
                code="INVALID_MANIFEST",
                message="capabilities must be a list",
                path=path,
                observed=type(capabilities).__name__,
                target="list",
            )
        )
        return

    manifest_by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(capabilities):
        item_path = f"{path}.capabilities[{index}]"
        if not isinstance(item, Mapping):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_MANIFEST",
                    message="capability entry must be an object",
                    path=item_path,
                )
            )
            continue
        capability_id = item.get("capability_id")
        if not isinstance(capability_id, str):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_MANIFEST",
                    message="capability_id must be a string",
                    path=item_path,
                )
            )
            continue
        if capability_id in manifest_by_id:
            failures.append(
                StaticValidationFailure(
                    code="MANIFEST_DUPLICATE_CAPABILITY",
                    message="capability appears more than once in manifest",
                    path=item_path,
                    capability_id=capability_id,
                )
            )
            continue
        manifest_by_id[capability_id] = item
        if not isinstance(item.get("signature"), Mapping):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_MANIFEST",
                    message="signature must be an object",
                    path=item_path,
                    capability_id=capability_id,
                )
            )
        if not isinstance(item.get("result_contract"), Mapping):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_MANIFEST",
                    message="result_contract must be an object",
                    path=item_path,
                    capability_id=capability_id,
                )
            )

    for capability_id, spec in expected.items():
        item = manifest_by_id.get(capability_id)
        if item is None:
            failures.append(
                StaticValidationFailure(
                    code="MANIFEST_CAPABILITY_MISSING",
                    message="frozen Stage 1 capability is absent from manifest",
                    path=path,
                    capability_id=capability_id,
                    target={
                        "granularity": spec.granularity,
                        "module": spec.module,
                        "function_name": spec.function_name,
                    },
                )
            )
            continue
        for field in ("granularity", "module", "function_name"):
            target = getattr(spec, field)
            if item.get(field) != target:
                failures.append(
                    StaticValidationFailure(
                        code="MANIFEST_FIELD_MISMATCH",
                        message=f"manifest {field} differs from frozen Stage 1",
                        path=path,
                        capability_id=capability_id,
                        observed=item.get(field),
                        target=target,
                    )
                )
        _validate_manifest_binding(item, spec, failures)

    for capability_id in manifest_by_id.keys() - expected.keys():
        failures.append(
            StaticValidationFailure(
                code="MANIFEST_CAPABILITY_EXTRA",
                message="manifest contains a capability not present in frozen Stage 1",
                path=path,
                capability_id=capability_id,
            )
        )


def _validate_manifest_binding(
    item: Mapping[str, Any],
    spec: _CapabilitySpec,
    failures: list[StaticValidationFailure],
) -> None:
    """Bind validation semantics to required public parameters, not name guesses."""

    binding = item.get("validation_binding")
    if not isinstance(binding, Mapping):
        failures.append(
            StaticValidationFailure(
                code="INVALID_VALIDATION_BINDING",
                message="manifest capability requires a structured validation_binding",
                path="package_manifest.json",
                capability_id=spec.capability_id,
                observed=binding,
            )
        )
        return
    effect = binding.get("effect")
    if effect != spec.validation_effect:
        failures.append(
            StaticValidationFailure(
                code="VALIDATION_EFFECT_MISMATCH",
                message="validation binding effect differs from frozen Stage 1",
                path="package_manifest.json",
                capability_id=spec.capability_id,
                observed=effect,
                target=spec.validation_effect,
            )
        )

    signature = item.get("signature")
    raw_parameters = signature.get("parameters", []) if isinstance(signature, Mapping) else []
    parameters = {
        str(parameter.get("name")): parameter
        for parameter in raw_parameters
        if isinstance(parameter, Mapping) and isinstance(parameter.get("name"), str)
    } if isinstance(raw_parameters, list) else {}

    argument_names: list[Any] = []
    auxiliary_argument_names: list[Any] = []
    vector_argument_names: list[Any] = []
    component_argument_names: list[Any] = []
    required_fields: set[str]
    permitted_fields: set[str]
    if effect == "joint_targets":
        has_mapping = "joint_targets_argument" in binding
        has_scalars = "joint_target_arguments" in binding
        if has_mapping == has_scalars:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "joint_targets must bind exactly one mapping argument or "
                        "one measurement-key-to-argument map"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        if has_mapping:
            argument_names.append(binding.get("joint_targets_argument"))
            required_fields = {"effect", "joint_targets_argument"}
            permitted_fields = set(required_fields)
        else:
            joint_arguments = binding.get("joint_target_arguments")
            if not isinstance(joint_arguments, Mapping) or not joint_arguments:
                failures.append(
                    StaticValidationFailure(
                        code="INVALID_VALIDATION_BINDING",
                        message="joint_target_arguments must be a non-empty object",
                        path="package_manifest.json",
                        capability_id=spec.capability_id,
                    )
                )
            else:
                argument_names.extend(joint_arguments.values())
            required_fields = {"effect", "joint_target_arguments"}
            permitted_fields = set(required_fields)
    elif effect == "cartesian_target":
        has_vector = "target_argument" in binding
        has_components = "target_arguments" in binding
        if has_vector == has_components:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "cartesian_target must bind exactly one xyz vector argument "
                        "or one explicit x/y/z component map"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        if has_vector:
            name = binding.get("target_argument")
            argument_names.append(name)
            vector_argument_names.append(name)
            required_fields = {"effect", "target_argument"}
        else:
            target_arguments = binding.get("target_arguments")
            if not isinstance(target_arguments, Mapping) or set(target_arguments) != {
                "x",
                "y",
                "z",
            }:
                failures.append(
                    StaticValidationFailure(
                        code="INVALID_VALIDATION_BINDING",
                        message="cartesian target_arguments must map exactly x, y, and z",
                        path="package_manifest.json",
                        capability_id=spec.capability_id,
                    )
                )
            elif isinstance(target_arguments, Mapping):
                argument_names.extend(target_arguments.values())
                component_argument_names.extend(target_arguments.values())
            required_fields = {"effect", "target_arguments"}
        permitted_fields = set(required_fields)
    elif effect == "object_source_to_target":
        source_vector = binding.get("source_argument")
        target_vector = binding.get("target_argument")
        source_arguments = binding.get("source_arguments")
        target_arguments = binding.get("target_arguments")
        has_source_vector = "source_argument" in binding
        has_source_components = "source_arguments" in binding
        has_target_vector = "target_argument" in binding
        has_target_components = "target_arguments" in binding
        if has_source_vector == has_source_components or has_target_vector == has_target_components:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "G3 source and target must each bind exactly one coordinate "
                        "vector or one explicit component map"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        source_names: set[Any] = set()
        target_names: set[Any] = set()
        required_fields = {"effect", "target_components"}
        if has_source_vector:
            argument_names.append(source_vector)
            vector_argument_names.append(source_vector)
            source_names.add(source_vector)
            required_fields.add("source_argument")
        else:
            if not isinstance(source_arguments, Mapping) or set(source_arguments) != {
                "x",
                "y",
                "z",
            }:
                failures.append(
                    StaticValidationFailure(
                        code="INVALID_VALIDATION_BINDING",
                        message="G3 source_arguments must map exactly x, y, and z",
                        path="package_manifest.json",
                        capability_id=spec.capability_id,
                    )
                )
            elif isinstance(source_arguments, Mapping):
                argument_names.extend(source_arguments.values())
                component_argument_names.extend(source_arguments.values())
                source_names.update(source_arguments.values())
            required_fields.add("source_arguments")
        if has_target_vector:
            argument_names.append(target_vector)
            vector_argument_names.append(target_vector)
            target_names.add(target_vector)
            required_fields.add("target_argument")
        else:
            expected_components = (
                {"x", "y", "z"}
                if binding.get("target_components") == "xyz"
                else {"x", "y"}
            )
            if not isinstance(target_arguments, Mapping) or set(target_arguments) != expected_components:
                failures.append(
                    StaticValidationFailure(
                        code="INVALID_VALIDATION_BINDING",
                        message=(
                            "G3 target_arguments keys must exactly match "
                            f"target_components={binding.get('target_components')!r}"
                        ),
                        path="package_manifest.json",
                        capability_id=spec.capability_id,
                    )
                )
            elif isinstance(target_arguments, Mapping):
                argument_names.extend(target_arguments.values())
                component_argument_names.extend(target_arguments.values())
                target_names.update(target_arguments.values())
            required_fields.add("target_arguments")
        permitted_fields = set(required_fields) | {
            "object_extent_argument",
            "object_extent_semantics",
            "contact_height_argument",
            "contact_height_reference",
        }
        if source_names & target_names:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message="G3 source and target coordinate bindings must not overlap",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        if binding.get("target_components") not in {"xy", "xyz"}:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message="target_components must be 'xy' or 'xyz'",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
    elif effect == "object_source_plus_height_delta":
        has_vector = "source_argument" in binding
        has_components = "source_arguments" in binding
        if has_vector == has_components:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "G3 lift source must bind exactly one xyz vector or one "
                        "explicit x/y/z component map"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        source_names: set[Any] = set()
        if has_vector:
            source = binding.get("source_argument")
            argument_names.append(source)
            vector_argument_names.append(source)
            source_names.add(source)
            required_fields = {"effect", "source_argument", "height_delta_argument"}
        else:
            source_arguments = binding.get("source_arguments")
            if not isinstance(source_arguments, Mapping) or set(source_arguments) != {
                "x",
                "y",
                "z",
            }:
                failures.append(
                    StaticValidationFailure(
                        code="INVALID_VALIDATION_BINDING",
                        message="G3 lift source_arguments must map exactly x, y, and z",
                        path="package_manifest.json",
                        capability_id=spec.capability_id,
                    )
                )
            elif isinstance(source_arguments, Mapping):
                argument_names.extend(source_arguments.values())
                component_argument_names.extend(source_arguments.values())
                source_names.update(source_arguments.values())
            required_fields = {"effect", "source_arguments", "height_delta_argument"}
        argument_names.append(binding.get("height_delta_argument"))
        permitted_fields = set(required_fields) | {
            "object_extent_argument",
            "object_extent_semantics",
        }
        if binding.get("height_delta_argument") in source_names:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message="G3 source and height delta must be different public arguments",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        height_name = binding.get("height_delta_argument")
        if not isinstance(height_name, str) or not (
            height_name.endswith("lift_height_m")
            or height_name.endswith("height_delta_m")
        ):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "height_delta_argument must explicitly name a lift-height "
                        "or height-delta quantity; approach offsets are not goals"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=height_name,
                )
            )
    elif effect == "object_move_sequence":
        argument_names.append(binding.get("moves_argument"))
        required_fields = {
            "effect",
            "moves_argument",
            "source_field",
            "target_field",
            "object_extent_field",
            "object_extent_semantics",
            "target_components",
        }
        permitted_fields = set(required_fields)
        move_item_fields = (
            binding.get("source_field"),
            binding.get("target_field"),
            binding.get("object_extent_field"),
        )
        if (
            not all(isinstance(name, str) for name in move_item_fields)
            or len(set(move_item_fields)) != len(move_item_fields)
        ):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "move source_field, target_field, and object_extent_field "
                        "must be pairwise different literal keys"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        field_parameter_collisions = sorted(
            {
                name
                for name in move_item_fields
                if isinstance(name, str) and name in parameters
            }
        )
        if field_parameter_collisions:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message=(
                        "move source_field, target_field, and object_extent_field "
                        "are fixed literal move-item keys, not public selector "
                        "parameters"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=field_parameter_collisions,
                )
            )
        if binding.get("target_components") not in {"xy", "xyz"}:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_VALIDATION_BINDING",
                    message="target_components must be 'xy' or 'xyz'",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
    else:
        required_fields = {"effect"}
        permitted_fields = {"effect"}
        failures.append(
            StaticValidationFailure(
                code="INVALID_VALIDATION_BINDING",
                message="validation binding uses an unsupported effect",
                path="package_manifest.json",
                capability_id=spec.capability_id,
                observed=effect,
            )
        )

    if effect in {"object_source_to_target", "object_source_plus_height_delta"}:
        extent_argument = binding.get("object_extent_argument")
        extent_semantics = binding.get("object_extent_semantics")
        if (extent_argument is None) != (extent_semantics is None):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_OBJECT_EXTENT_BINDING",
                    message="object extent argument and semantics must be declared together",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        if extent_argument is not None:
            auxiliary_argument_names.append(extent_argument)
        if extent_semantics is not None and extent_semantics not in {
            "cube_edge_m",
            "cylinder_diameter_m",
            "maximum_horizontal_extent_m",
        }:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_OBJECT_EXTENT_BINDING",
                    message="object extent semantics is unsupported",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=extent_semantics,
                )
            )
        extent_candidates = sorted(
            name
            for name in parameters
            if name != "runtime"
            and any(token in name for token in ("object_size", "object_extent", "object_diameter"))
        )
        if extent_candidates and extent_argument not in extent_candidates:
            failures.append(
                StaticValidationFailure(
                    code="OBJECT_EXTENT_BINDING_REQUIRED",
                    message="G3 object geometry parameters require explicit validation binding",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=extent_argument,
                    target=extent_candidates,
                )
            )

    if effect == "object_source_to_target":
        contact_argument = binding.get("contact_height_argument")
        contact_reference = binding.get("contact_height_reference")
        if (contact_argument is None) != (contact_reference is None):
            failures.append(
                StaticValidationFailure(
                    code="INVALID_CONTACT_HEIGHT_BINDING",
                    message="contact height argument and reference must be declared together",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                )
            )
        if contact_argument is not None:
            auxiliary_argument_names.append(contact_argument)
        if contact_reference is not None and contact_reference not in {
            "absolute_base_z_m",
            "height_above_table_m",
            "offset_from_object_center_m",
        }:
            failures.append(
                StaticValidationFailure(
                    code="INVALID_CONTACT_HEIGHT_BINDING",
                    message="contact height reference is unsupported",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=contact_reference,
                )
            )
        contact_candidates = sorted(
            name
            for name in parameters
            if name != "runtime"
            and ("push_height" in name or "contact_height" in name)
        )
        if contact_candidates and contact_argument not in contact_candidates:
            failures.append(
                StaticValidationFailure(
                    code="CONTACT_HEIGHT_BINDING_REQUIRED",
                    message="G3 contact-height parameters require an explicit reference frame",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=contact_argument,
                    target=contact_candidates,
                )
            )

    missing = required_fields - set(binding)
    extra = set(binding) - permitted_fields
    if missing or extra:
        failures.append(
            StaticValidationFailure(
                code="INVALID_VALIDATION_BINDING",
                message="validation binding fields do not match its effect",
                path="package_manifest.json",
                capability_id=spec.capability_id,
                observed={"missing": sorted(missing), "extra": sorted(extra)},
            )
        )
    for argument_name in argument_names:
        parameter = parameters.get(str(argument_name)) if isinstance(argument_name, str) else None
        if argument_name == "runtime" or parameter is None:
            failures.append(
                StaticValidationFailure(
                    code="VALIDATION_ARGUMENT_MISSING",
                    message="validation binding must name an existing non-runtime parameter",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=argument_name,
                    target=sorted(name for name in parameters if name != "runtime"),
                )
            )
        elif bool(parameter.get("has_default", False)):
            failures.append(
                StaticValidationFailure(
                    code="VALIDATION_ARGUMENT_OPTIONAL",
                    message="source/goal validation arguments must be required",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=argument_name,
                )
            )
    for argument_name in vector_argument_names:
        parameter = (
            parameters.get(str(argument_name))
            if isinstance(argument_name, str)
            else None
        )
        if parameter is not None and not _coordinate_vector_annotation(
            parameter.get("annotation")
        ):
            failures.append(
                StaticValidationFailure(
                    code="VECTOR_BINDING_SHAPE_MISMATCH",
                    message=(
                        "bound coordinate vector has a non-vector annotation; "
                        "use a required coordinate sequence or explicit component map "
                        "(suite_repairable=false)"
                    ),
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed={
                        "argument": argument_name,
                        "annotation": parameter.get("annotation"),
                    },
                    target="xyz coordinate sequence",
                )
            )
    for argument_name in component_argument_names:
        parameter = (
            parameters.get(str(argument_name))
            if isinstance(argument_name, str)
            else None
        )
        if parameter is not None and not _coordinate_scalar_annotation(
            parameter.get("annotation")
        ):
            failures.append(
                StaticValidationFailure(
                    code="COMPONENT_BINDING_TYPE_MISMATCH",
                    message="bound coordinate component must be a required float parameter",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed={
                        "argument": argument_name,
                        "annotation": parameter.get("annotation"),
                    },
                    target="float",
                )
            )
    for argument_name in auxiliary_argument_names:
        parameter = parameters.get(str(argument_name)) if isinstance(argument_name, str) else None
        if argument_name == "runtime" or parameter is None:
            failures.append(
                StaticValidationFailure(
                    code="VALIDATION_AUXILIARY_ARGUMENT_MISSING",
                    message="auxiliary validation binding must name an existing non-runtime parameter",
                    path="package_manifest.json",
                    capability_id=spec.capability_id,
                    observed=argument_name,
                    target=sorted(name for name in parameters if name != "runtime"),
                )
            )


def _parse_python(
    path: Path,
    display_path: str,
    failures: list[StaticValidationFailure],
) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            StaticValidationFailure(
                code="ARTIFACT_READ_ERROR",
                message=f"could not read file: {error}",
                path=display_path,
            )
        )
        return None
    try:
        return ast.parse(source, filename=display_path)
    except SyntaxError as error:
        failures.append(
            StaticValidationFailure(
                code="PYTHON_SYNTAX_ERROR",
                message=error.msg,
                path=display_path,
                line=error.lineno,
                column=error.offset,
            )
        )
        return None


def _validate_ast_safety(
    tree: ast.Module,
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    _validate_import_contract(tree, path, failures)
    _validate_deterministic_time_usage(tree, path, failures)
    _validate_unresolved_private_calls(tree, path, failures)
    for node in ast.walk(tree):
        nonfinite_literal: Any = None
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, float)
            and not math.isfinite(node.value)
        ):
            nonfinite_literal = repr(node.value)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "math"
            and node.attr in {"inf", "nan"}
        ):
            nonfinite_literal = f"math.{node.attr}"
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.strip().lower()
            in {"inf", "+inf", "-inf", "infinity", "+infinity", "-infinity", "nan"}
        ):
            nonfinite_literal = f"float({node.args[0].value!r})"
        if nonfinite_literal is not None:
            _append_node_failure(
                failures,
                node,
                "NONFINITE_GENERATED_VALUE",
                "generated code may not construct NaN or infinity; all result data must be finite JSON",
                path,
                observed=nonfinite_literal,
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name):
                    _append_node_failure(
                        failures,
                        node,
                        "FORBIDDEN_IMPORT",
                        f"import of {alias.name!r} crosses the generated-package boundary",
                        path,
                        observed=alias.name,
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            relative_kinematics_import = (
                node.level == 1
                and path.endswith(("/g2.py", "/g3.py"))
                and module == "_kinematics"
                and all(
                    alias.asname is None
                    and alias.name.startswith("_")
                    and not alias.name.startswith("__")
                    for alias in node.names
                )
            )
            candidates = (
                [f"{module}.{alias.name}" for alias in node.names] + [module]
                if module
                else [alias.name for alias in node.names]
            )
            forbidden = None if relative_kinematics_import else next(
                (name for name in candidates if _is_forbidden_module(name)),
                None,
            )
            if forbidden is not None:
                _append_node_failure(
                    failures,
                    node,
                    "FORBIDDEN_IMPORT",
                    f"import of {forbidden!r} crosses the generated-package boundary",
                    path,
                    observed=forbidden,
                )
        if isinstance(node, ast.Call):
            called_name = _called_name(node.func)
            if called_name in _FORBIDDEN_CALLS:
                _append_node_failure(
                    failures,
                    node,
                    "FORBIDDEN_CALL",
                    f"call to {called_name!r} is forbidden in generated code",
                    path,
                    observed=called_name,
                )
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "runtime"
                and node.func.attr
                in _FORBIDDEN_RUNTIME_LIFECYCLE_CALLS
                | _FORBIDDEN_RUNTIME_PRIVILEGED_CALLS
            ):
                lifecycle = node.func.attr in _FORBIDDEN_RUNTIME_LIFECYCLE_CALLS
                _append_node_failure(
                    failures,
                    node,
                    (
                        "FORBIDDEN_RUNTIME_LIFECYCLE"
                        if lifecycle
                        else "FORBIDDEN_RUNTIME_PRIVILEGED_CALL"
                    ),
                    (
                        "generated code must not call framework-owned "
                        f"runtime.{node.func.attr}()"
                    ),
                    path,
                    observed=node.func.attr,
                )
        if isinstance(node, ast.Name) and (
            node.id.startswith("__") or node.id in {"globals", "locals", "vars"}
        ):
            _append_node_failure(
                failures,
                node,
                "FORBIDDEN_REFLECTION",
                f"name {node.id!r} can escape the generated-code boundary",
                path,
                observed=node.id,
            )
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("_")
            or node.attr in _FORBIDDEN_RUNTIME_LIFECYCLE_CALLS
            or node.attr in _FORBIDDEN_RUNTIME_PRIVILEGED_CALLS
            or node.attr in _FORBIDDEN_RUNTIME_INTERNAL_ATTRIBUTES
        ):
            _append_node_failure(
                failures,
                node,
                "FORBIDDEN_ATTRIBUTE",
                f"attribute {node.attr!r} is unavailable to generated code",
                path,
                observed=node.attr,
            )
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "runtime"
                and node.attr in _FORBIDDEN_RUNTIME_INTERNAL_ATTRIBUTES
            ):
                _append_node_failure(
                    failures,
                    node,
                    "FORBIDDEN_RUNTIME_INTERNAL",
                    f"generated code must not access runtime.{node.attr}",
                    path,
                    observed=node.attr,
                )

        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "runtime"
            and node.attr not in _ALLOWED_RUNTIME_ATTRIBUTES
        ):
            _append_node_failure(
                failures,
                node,
                "UNDOCUMENTED_RUNTIME_ATTRIBUTE",
                (
                    f"runtime.{node.attr} is not in the pinned generated-code "
                    "surface; only send_action/get_observation are available"
                ),
                path,
                observed=node.attr,
                target=sorted(_ALLOWED_RUNTIME_ATTRIBUTES),
            )

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "runtime":
                _append_node_failure(
                    failures,
                    node,
                    "RUNTIME_ALIAS_FORBIDDEN",
                    "the injected runtime may be passed to helpers but not stored under an alias",
                    path,
                )

    for index, statement in enumerate(tree.body):
        if _is_module_docstring(statement, index):
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _validate_definition_eagerness(statement, path, failures)
            continue
        if isinstance(statement, ast.ClassDef):
            _append_node_failure(
                failures,
                statement,
                "UNSUPPORTED_GENERATED_CONSTRUCT",
                "generated capability modules may not define classes",
                path,
            )
            continue
        if isinstance(statement, ast.Assign):
            if all(_safe_assignment_target(target) for target in statement.targets) and _safe_eager_expr(statement.value):
                continue
        elif isinstance(statement, ast.AnnAssign):
            if (
                _safe_assignment_target(statement.target)
                and statement.value is not None
                and _safe_eager_expr(statement.value)
                and _safe_eager_expr(statement.annotation)
            ):
                continue
        elif isinstance(statement, ast.TypeAlias):
            if _safe_eager_expr(statement.value):
                continue
        _append_node_failure(
            failures,
            statement,
            "TOP_LEVEL_SIDE_EFFECT",
            "module top level may contain only imports, definitions, literal constants, and a docstring",
            path,
            observed=type(statement).__name__,
                )


class _ScopeBindingCollector(ast.NodeVisitor):
    """Collect names bound by one lexical scope without entering child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda creates a child scope but does not bind a name by itself.
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if isinstance(node.name, str):
            self.names.add(node.name)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
        self.generic_visit(node)


def _argument_names(arguments: ast.arguments) -> set[str]:
    positional = [*arguments.posonlyargs, *arguments.args]
    names = {argument.arg for argument in [*positional, *arguments.kwonlyargs]}
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


def _bound_names(statements: list[ast.stmt]) -> frozenset[str]:
    collector = _ScopeBindingCollector()
    for statement in statements:
        collector.visit(statement)
    return frozenset(collector.names)


def _function_bound_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    return _bound_names(node.body) | _argument_names(node.args)


class _UnresolvedPrivateCallVisitor(ast.NodeVisitor):
    """Find direct private-name calls that Python cannot resolve lexically.

    This is deliberately a narrow undefined-name check.  Public names remain
    covered by the existing generated-code allowlists, while private helper
    calls are the dangerous case: a plausible misspelling otherwise survives
    static validation and raises ``NameError`` only in direct MuJoCo execution.
    """

    def __init__(
        self,
        tree: ast.Module,
        path: str,
        failures: list[StaticValidationFailure],
    ) -> None:
        self.path = path
        self.failures = failures
        self.scopes: list[tuple[str, frozenset[str]]] = [
            ("module", _bound_names(tree.body))
        ]

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id.startswith("_")
            and not node.func.id.startswith("__")
            and node.func.id not in _PYTHON_BUILTIN_NAMES
            and not any(node.func.id in names for _, names in self.scopes)
        ):
            _append_node_failure(
                self.failures,
                node,
                "UNRESOLVED_PRIVATE_CALL",
                _UNRESOLVED_PRIVATE_CALL_MESSAGE,
                self.path,
                observed=node.func.id,
                target=_UNRESOLVED_PRIVATE_CALL_TARGET,
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        # Decorators, defaults, and annotations execute in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_argument_expressions(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)

        outer_scopes = self.scopes
        # A method closes over enclosing function scopes, but not class locals.
        lexical_parents = [scope for scope in outer_scopes if scope[0] != "class"]
        self.scopes = [
            *lexical_parents,
            ("function", _function_bound_names(node)),
        ]
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scopes = outer_scopes

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)
        self.scopes.append(("class", _bound_names(node.body)))
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_argument_expressions(node.args)
        collector = _ScopeBindingCollector()
        collector.visit(node.body)
        bindings = frozenset(collector.names | _argument_names(node.args))
        self.scopes.append(("function", bindings))
        try:
            self.visit(node.body)
        finally:
            self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.expr, ...],
    ) -> None:
        if not generators:
            for result in results:
                self.visit(result)
            return
        # Python evaluates the first iterable outside the comprehension scope.
        self.visit(generators[0].iter)
        collector = _ScopeBindingCollector()
        for generator in generators:
            collector.visit(generator.target)
        self.scopes.append(("comprehension", frozenset(collector.names)))
        try:
            self.visit(generators[0].target)
            for condition in generators[0].ifs:
                self.visit(condition)
            for generator in generators[1:]:
                self.visit(generator.iter)
                self.visit(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            for result in results:
                self.visit(result)
        finally:
            self.scopes.pop()

    def _visit_argument_expressions(self, arguments: ast.arguments) -> None:
        all_arguments = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        if arguments.vararg is not None:
            all_arguments.append(arguments.vararg)
        if arguments.kwarg is not None:
            all_arguments.append(arguments.kwarg)
        for argument in all_arguments:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)


def _validate_unresolved_private_calls(
    tree: ast.Module,
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Reject unresolved direct private helper calls before direct execution."""

    visitor = _UnresolvedPrivateCallVisitor(tree, path, failures)
    for statement in tree.body:
        visitor.visit(statement)


def _flat_function_nodes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    """Return one function body's nodes without entering nested definitions."""

    nodes: list[ast.AST] = []

    class _Visitor(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            nodes.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nodes.append(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nodes.append(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nodes.append(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            nodes.append(node)

    visitor = _Visitor()
    for statement in function.body:
        visitor.visit(statement)
    return nodes


def _literal_subscript_key(node: ast.Subscript) -> str | None:
    value = node.slice
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _sample_field_reference(
    node: ast.AST,
    *,
    sample_name: str,
    field_name: str,
) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == sample_name
        and _literal_subscript_key(node) == field_name
    )


def _transparent_sample_field_expression(
    expression: ast.AST,
    *,
    sample_name: str,
    field_name: str,
    aliases: set[str],
) -> bool:
    if _sample_field_reference(
        expression,
        sample_name=sample_name,
        field_name=field_name,
    ):
        return True
    if isinstance(expression, ast.Name):
        return expression.id in aliases
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "float"
        and len(expression.args) == 1
        and not expression.keywords
        and _transparent_sample_field_expression(
            expression.args[0],
            sample_name=sample_name,
            field_name=field_name,
            aliases=aliases,
        )
    )


def _function_has_send_action(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_action"
        for node in _flat_function_nodes(function)
    )


def _call_parameter_for_matching_argument(
    call: ast.Call,
    helper: ast.FunctionDef | ast.AsyncFunctionDef,
    predicate: Any,
) -> str | None:
    positional_parameters = [*helper.args.posonlyargs, *helper.args.args]
    matched: list[str] = []
    for index, argument in enumerate(call.args):
        if predicate(argument) and index < len(positional_parameters):
            matched.append(positional_parameters[index].arg)
    keyword_parameters = {
        argument.arg
        for argument in [*positional_parameters, *helper.args.kwonlyargs]
    }
    for keyword in call.keywords:
        if (
            keyword.arg is not None
            and keyword.arg in keyword_parameters
            and predicate(keyword.value)
        ):
            matched.append(keyword.arg)
    unique = list(dict.fromkeys(matched))
    return unique[0] if len(unique) == 1 else None


def _gripper_value_from_dict(
    expression: ast.AST,
    scalar_open: set[str],
) -> tuple[bool, str]:
    if not isinstance(expression, ast.Dict):
        return False, "action expression does not expose a literal gripper.pos"
    for key, value in zip(expression.keys, expression.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and key.value == "gripper.pos"
        ):
            return (
                _transparent_open_parameter_expression(value, scalar_open),
                ast.unparse(value),
            )
    return False, "dict action has no explicit gripper.pos"


def _transparent_open_parameter_expression(
    expression: ast.AST,
    scalar_open: set[str],
) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in scalar_open
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "float"
        and len(expression.args) == 1
        and not expression.keywords
        and _transparent_open_parameter_expression(
            expression.args[0], scalar_open
        )
    )


def _direct_calls_in_statement(statement: ast.stmt) -> list[ast.Call]:
    """Collect calls in one statement without entering its child blocks."""

    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            for _field, value in ast.iter_fields(node):
                if isinstance(value, ast.stmt):
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, ast.AST) and not isinstance(
                            item, ast.stmt
                        ):
                            self.visit(item)
                elif isinstance(value, ast.AST):
                    self.visit(value)

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            # A deferred lambda is not executed as part of this statement.
            return

    _Visitor().visit(statement)
    return calls


def _sample_field_flow_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    sample_name: str,
) -> list[tuple[ast.Call, frozenset[str], frozenset[str]]]:
    """Return calls with definite local aliases for one selected sample.

    Branch-local assignments are available inside that branch, but are not
    propagated beyond the compound statement.  That deliberately conservative
    merge keeps a command assigned on only one path from satisfying the gate.
    """

    events: list[tuple[ast.Call, frozenset[str], frozenset[str]]] = []

    def stored_names(target: ast.AST | None) -> set[str]:
        if target is None:
            return set()
        return {
            node.id
            for node in ast.walk(target)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }

    def update_assignment(
        statement: ast.Assign | ast.AnnAssign,
        command_aliases: set[str],
        tool_aliases: set[str],
    ) -> None:
        value = statement.value
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        for target in targets:
            if not isinstance(target, ast.Name):
                for name in stored_names(target):
                    command_aliases.discard(name)
                    tool_aliases.discard(name)
                continue
            command_value = value is not None and _transparent_sample_field_expression(
                value,
                sample_name=sample_name,
                field_name="command",
                aliases=command_aliases,
            )
            tool_value = value is not None and _transparent_sample_field_expression(
                value,
                sample_name=sample_name,
                field_name="tool_center_in_frame_m",
                aliases=tool_aliases,
            )
            command_aliases.discard(target.id)
            tool_aliases.discard(target.id)
            if command_value and not tool_value:
                command_aliases.add(target.id)
            elif tool_value and not command_value:
                tool_aliases.add(target.id)

    def analyze_block(
        statements: list[ast.stmt],
        command_aliases: set[str],
        tool_aliases: set[str],
    ) -> None:
        for statement in statements:
            for call in _direct_calls_in_statement(statement):
                events.append(
                    (
                        call,
                        frozenset(command_aliases),
                        frozenset(tool_aliases),
                    )
                )

            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                update_assignment(statement, command_aliases, tool_aliases)
            elif isinstance(statement, (ast.AugAssign, ast.Delete)):
                for name in stored_names(statement):
                    command_aliases.discard(name)
                    tool_aliases.discard(name)

            child_blocks: list[
                tuple[list[ast.stmt], set[str], set[str]]
            ] = []
            if isinstance(statement, (ast.If, ast.While)):
                child_blocks.extend(
                    [
                        (statement.body, set(command_aliases), set(tool_aliases)),
                        (statement.orelse, set(command_aliases), set(tool_aliases)),
                    ]
                )
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                body_command = set(command_aliases)
                body_tool = set(tool_aliases)
                for name in stored_names(statement.target):
                    body_command.discard(name)
                    body_tool.discard(name)
                child_blocks.extend(
                    [
                        (statement.body, body_command, body_tool),
                        (statement.orelse, set(command_aliases), set(tool_aliases)),
                    ]
                )
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                body_command = set(command_aliases)
                body_tool = set(tool_aliases)
                for item in statement.items:
                    for name in stored_names(item.optional_vars):
                        body_command.discard(name)
                        body_tool.discard(name)
                child_blocks.append((statement.body, body_command, body_tool))
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                child_blocks.extend(
                    [
                        (statement.body, set(command_aliases), set(tool_aliases)),
                        (statement.orelse, set(command_aliases), set(tool_aliases)),
                        (statement.finalbody, set(command_aliases), set(tool_aliases)),
                    ]
                )
                for handler in statement.handlers:
                    handler_command = set(command_aliases)
                    handler_tool = set(tool_aliases)
                    if handler.name is not None:
                        handler_command.discard(handler.name)
                        handler_tool.discard(handler.name)
                    child_blocks.append(
                        (handler.body, handler_command, handler_tool)
                    )
            elif isinstance(statement, ast.Match):
                # Pattern bindings are deliberately not modeled.  Keeping only
                # incoming aliases is safe unless a case shadows one; clear all
                # aliases in that uncommon construct rather than risk a proof.
                child_blocks.extend(
                    (case.body, set(), set()) for case in statement.cases
                )
            for child, child_command, child_tool in child_blocks:
                analyze_block(child, child_command, child_tool)

    analyze_block(function.body, set(), set())
    return events


def _pre_close_send_action_evidence(
    helper: ast.FunctionDef | ast.AsyncFunctionDef,
    open_parameter: str,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Conservatively prove a helper's gripper field at each action send."""

    send_lines: list[int] = []
    unproven: list[dict[str, Any]] = []

    def analyze_block(
        statements: list[ast.stmt],
        scalar_open: set[str],
        action_gripper: dict[str, tuple[bool, str]],
    ) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        if _transparent_open_parameter_expression(
                            value, scalar_open
                        ):
                            scalar_open.add(target.id)
                        else:
                            scalar_open.discard(target.id)
                        if isinstance(value, ast.Dict):
                            action_gripper[target.id] = _gripper_value_from_dict(
                                value, scalar_open
                            )
                        else:
                            action_gripper.pop(target.id, None)
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and _literal_subscript_key(target) == "gripper.pos"
                    ):
                        action_gripper[target.value.id] = (
                            _transparent_open_parameter_expression(
                                value, scalar_open
                            ),
                            ast.unparse(value),
                        )
            elif isinstance(statement, (ast.AugAssign, ast.Delete)):
                for node in ast.walk(statement):
                    if isinstance(node, ast.Name) and isinstance(
                        node.ctx, (ast.Store, ast.Del)
                    ):
                        scalar_open.discard(node.id)
                        action_gripper.pop(node.id, None)

            for node in _direct_calls_in_statement(statement):
                if not (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "send_action"
                ):
                    continue
                line = getattr(node, "lineno", getattr(statement, "lineno", 0))
                send_lines.append(line)
                argument = node.args[0] if len(node.args) == 1 else None
                if isinstance(argument, ast.Name):
                    proven, source = action_gripper.get(
                        argument.id,
                        (False, "action gripper.pos has no dominating proof"),
                    )
                elif argument is not None:
                    proven, source = _gripper_value_from_dict(
                        argument, scalar_open
                    )
                else:
                    proven, source = False, "send_action must receive one action"
                if not proven:
                    unproven.append(
                        {
                            "line": line,
                            "action": (
                                None if argument is None else ast.unparse(argument)
                            ),
                            "gripper_source": source,
                        }
                    )

            child_blocks: list[list[ast.stmt]] = []
            if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                child_blocks.extend([statement.body, statement.orelse])
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                child_blocks.append(statement.body)
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                child_blocks.extend(
                    [statement.body, statement.orelse, statement.finalbody]
                )
                child_blocks.extend(handler.body for handler in statement.handlers)
            elif isinstance(statement, ast.Match):
                child_blocks.extend(case.body for case in statement.cases)
            for child in child_blocks:
                analyze_block(
                    child,
                    set(scalar_open),
                    dict(action_gripper),
                )

    analyze_block(helper.body, {open_parameter}, {})
    return sorted(set(send_lines)), unproven


def _validate_g3_pre_close_gripper_command_flow(
    tree: ast.Module,
    public_capabilities: Mapping[str, str],
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Keep selected grasp-open aperture through the first motion boundary.

    The rule intentionally does not depend on generated helper names.  It
    accepts either a public caller passing the selected fields directly to a
    motion helper, or one wrapper boundary that receives the selected sample
    as a whole, extracts both fields from that same parameter, and passes them
    as distinct arguments to its motion helpers.  Only the globally earliest
    selected-aperture tool-centered motion boundary in each public capability
    is pre-close; later tool-centered lower/release/retreat boundaries are not
    reclassified as additional pre-close boundaries merely because they use a
    different aperture sample.
    """

    definitions = {
        statement.name: statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    motion_helpers = {
        name: function
        for name, function in definitions.items()
        if _function_has_send_action(function)
    }
    checked_helpers: dict[tuple[str, str], tuple[list[int], list[dict[str, Any]]]] = {}

    @dataclass(frozen=True)
    class _MotionBoundary:
        order: tuple[int, int, int, int, int]
        call: ast.Call
        helper_name: str
        helper: ast.FunctionDef | ast.AsyncFunctionDef | None
        command_aliases: frozenset[str]
        tool_aliases: frozenset[str]
        sample_parameter: str
        sample_name: str
        wrapper_name: str | None = None
        wrapper_call_line: int | None = None
        failure_reason: str | None = None

    def sample_fields_used(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        sample_name: str,
    ) -> set[str]:
        return {
            key
            for node in _flat_function_nodes(function)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == sample_name
            and (key := _literal_subscript_key(node))
            in {"command", "tool_center_in_frame_m"}
        }

    def call_expressions(call: ast.Call) -> list[ast.expr]:
        return [
            *call.args,
            *(keyword.value for keyword in call.keywords),
        ]

    def boundary_order(
        outer_call: ast.Call,
        inner_call: ast.Call,
        *,
        nested: bool,
    ) -> tuple[int, int, int, int, int]:
        return (
            getattr(outer_call, "lineno", 0),
            getattr(outer_call, "col_offset", 0),
            int(nested),
            getattr(inner_call, "lineno", 0),
            getattr(inner_call, "col_offset", 0),
        )

    def motion_uses_selected_tool(
        *,
        call: ast.Call,
        sample_parameter: str,
        tool_aliases: frozenset[str],
    ) -> bool:
        return any(
            _transparent_sample_field_expression(
                expression,
                sample_name=sample_parameter,
                field_name="tool_center_in_frame_m",
                aliases=set(tool_aliases),
            )
            for expression in call_expressions(call)
        )

    def append_motion_failure(
        *,
        call: ast.Call,
        capability_id: str,
        public_name: str,
        sample_name: str,
        helper_name: str,
        tool_parameter: str | None,
        command_parameter: str | None,
        send_lines: list[int],
        unproven: list[dict[str, Any]],
        wrapper_name: str | None = None,
        wrapper_call_line: int | None = None,
        wrapper_sample_parameter: str | None = None,
        reason: str | None = None,
    ) -> None:
        observed: dict[str, Any] = {
            "caller": public_name,
            "selected_sample": sample_name,
            "flow_route": (
                "direct_fields" if wrapper_name is None else "nested_wrapper"
            ),
            "wrapper": wrapper_name,
            "wrapper_call_line": wrapper_call_line,
            "wrapper_sample_parameter": wrapper_sample_parameter,
            "motion_helper": helper_name,
            "helper_call_line": getattr(call, "lineno", 0),
            "selected_tool_parameter": tool_parameter,
            "selected_command_parameter": command_parameter,
            "send_action_lines": send_lines,
            "unproven_send_actions": unproven,
        }
        if reason is not None:
            observed["reason"] = reason
        _append_node_failure(
            failures,
            call,
            _G3_PRE_CLOSE_GRIPPER_FLOW_CODE,
            _G3_PRE_CLOSE_GRIPPER_FLOW_MESSAGE,
            path,
            capability_id=capability_id,
            observed=observed,
            target=_G3_PRE_CLOSE_GRIPPER_FLOW_TARGET,
        )

    def validate_motion_call(
        *,
        call: ast.Call,
        helper_name: str,
        helper: ast.FunctionDef | ast.AsyncFunctionDef,
        command_aliases: frozenset[str],
        tool_aliases: frozenset[str],
        sample_parameter: str,
        capability_id: str,
        public_name: str,
        sample_name: str,
        wrapper_name: str | None = None,
        wrapper_call_line: int | None = None,
    ) -> bool:
        expressions = call_expressions(call)

        def derives(field_name: str, aliases: frozenset[str], expression: ast.AST) -> bool:
            return _transparent_sample_field_expression(
                expression,
                sample_name=sample_parameter,
                field_name=field_name,
                aliases=set(aliases),
            )

        has_selected_tool = motion_uses_selected_tool(
            call=call,
            sample_parameter=sample_parameter,
            tool_aliases=tool_aliases,
        )
        if not has_selected_tool:
            return False

        tool_parameter = _call_parameter_for_matching_argument(
            call,
            helper,
            lambda expression: derives(
                "tool_center_in_frame_m", tool_aliases, expression
            ),
        )
        command_parameter = _call_parameter_for_matching_argument(
            call,
            helper,
            lambda expression: derives("command", command_aliases, expression),
        )
        if command_parameter is None:
            send_lines, unproven = _pre_close_send_action_evidence(
                helper,
                "__missing_selected_open_parameter__",
            )
        else:
            cache_key = (helper_name, command_parameter)
            if cache_key not in checked_helpers:
                checked_helpers[cache_key] = _pre_close_send_action_evidence(
                    helper, command_parameter
                )
            send_lines, unproven = checked_helpers[cache_key]

        valid = (
            tool_parameter is not None
            and command_parameter is not None
            and tool_parameter != command_parameter
            and bool(send_lines)
            and not unproven
        )
        if not valid:
            append_motion_failure(
                call=call,
                capability_id=capability_id,
                public_name=public_name,
                sample_name=sample_name,
                helper_name=helper_name,
                tool_parameter=tool_parameter,
                command_parameter=command_parameter,
                send_lines=send_lines,
                unproven=unproven,
                wrapper_name=wrapper_name,
                wrapper_call_line=wrapper_call_line,
                wrapper_sample_parameter=(
                    sample_parameter if wrapper_name is not None else None
                ),
                reason=(
                    "selected tool center and command must bind to distinct "
                    "explicit helper parameters"
                ),
            )
        return True

    for public_name, capability_id in public_capabilities.items():
        function = definitions.get(public_name)
        if function is None:
            continue
        nodes = _flat_function_nodes(function)
        sample_names = {
            node.value.id
            for node in nodes
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and _literal_subscript_key(node)
            in {"command", "tool_center_in_frame_m"}
        }
        public_calls = sorted(
            (node for node in nodes if isinstance(node, ast.Call)),
            key=lambda node: (
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
            ),
        )

        # A sample passed whole to a wrapper is also selected evidence when the
        # corresponding wrapper parameter reads either aperture field.
        for call in public_calls:
            wrapper_name = call.func.id if isinstance(call.func, ast.Name) else None
            wrapper = definitions.get(wrapper_name or "")
            if wrapper is None:
                continue
            actual_names = {
                expression.id
                for expression in call_expressions(call)
                if isinstance(expression, ast.Name)
            }
            for actual_name in actual_names:
                parameter = _call_parameter_for_matching_argument(
                    call,
                    wrapper,
                    lambda expression, name=actual_name: (
                        isinstance(expression, ast.Name)
                        and expression.id == name
                    ),
                )
                if parameter is not None and sample_fields_used(wrapper, parameter):
                    sample_names.add(actual_name)

        boundaries: list[_MotionBoundary] = []
        for sample_name in sorted(sample_names):
            # Direct form: retain only this sample's earliest relevant call.
            # Global ordering across all samples is resolved after collection.
            for call, command_aliases, tool_aliases in _sample_field_flow_calls(
                function, sample_name
            ):
                helper_name = call.func.id if isinstance(call.func, ast.Name) else None
                helper = motion_helpers.get(helper_name or "")
                if helper is None or not motion_uses_selected_tool(
                    call=call,
                    sample_parameter=sample_name,
                    tool_aliases=tool_aliases,
                ):
                    continue
                boundaries.append(
                    _MotionBoundary(
                        order=boundary_order(call, call, nested=False),
                        call=call,
                        helper_name=helper_name or "",
                        helper=helper,
                        command_aliases=command_aliases,
                        tool_aliases=tool_aliases,
                        sample_parameter=sample_name,
                        sample_name=sample_name,
                    )
                )
                break

            # Nested form: the public call fixes ordering relative to other
            # public-level boundaries; the inner call orders motion within that
            # one wrapper invocation.
            for wrapper_call in public_calls:
                wrapper_name = (
                    wrapper_call.func.id
                    if isinstance(wrapper_call.func, ast.Name)
                    else None
                )
                wrapper = definitions.get(wrapper_name or "")
                if wrapper is None:
                    continue
                sample_parameter = _call_parameter_for_matching_argument(
                    wrapper_call,
                    wrapper,
                    lambda expression: (
                        isinstance(expression, ast.Name)
                        and expression.id == sample_name
                    ),
                )
                if sample_parameter is None:
                    continue

                inner_events = [
                    (call, command_aliases, tool_aliases, helper_name, helper)
                    for call, command_aliases, tool_aliases in _sample_field_flow_calls(
                        wrapper, sample_parameter
                    )
                    if isinstance(call.func, ast.Name)
                    and (helper_name := call.func.id) in motion_helpers
                    and (helper := motion_helpers[helper_name]) is not None
                ]
                if not inner_events:
                    # A sample-consuming post-close helper may own its actions
                    # directly.  This gate only admits/inspects the explicit
                    # wrapper-to-inner-motion shape requested for pre-close.
                    continue

                fields = sample_fields_used(wrapper, sample_parameter)
                nested_boundary: _MotionBoundary | None = None
                for (
                    inner_call,
                    command_aliases,
                    tool_aliases,
                    helper_name,
                    helper,
                ) in inner_events:
                    order = boundary_order(
                        wrapper_call,
                        inner_call,
                        nested=True,
                    )
                    if motion_uses_selected_tool(
                        call=inner_call,
                        sample_parameter=sample_parameter,
                        tool_aliases=tool_aliases,
                    ):
                        nested_boundary = _MotionBoundary(
                            order=order,
                            call=inner_call,
                            helper_name=helper_name,
                            helper=helper,
                            command_aliases=command_aliases,
                            tool_aliases=tool_aliases,
                            sample_parameter=sample_parameter,
                            sample_name=sample_name,
                            wrapper_name=wrapper_name,
                            wrapper_call_line=getattr(wrapper_call, "lineno", 0),
                        )
                        break

                    passed_whole = any(
                        isinstance(expression, ast.Name)
                        and expression.id == sample_parameter
                        for expression in call_expressions(inner_call)
                    )
                    if passed_whole:
                        nested_boundary = _MotionBoundary(
                            order=order,
                            call=inner_call,
                            helper_name=helper_name,
                            helper=None,
                            command_aliases=frozenset(),
                            tool_aliases=frozenset(),
                            sample_parameter=sample_parameter,
                            sample_name=sample_name,
                            wrapper_name=wrapper_name,
                            wrapper_call_line=getattr(wrapper_call, "lineno", 0),
                            failure_reason=(
                                "wrapper passed the selected sample as a whole "
                                "instead of independent field parameters"
                            ),
                        )
                        break

                if (
                    nested_boundary is None
                    and "tool_center_in_frame_m" in fields
                ):
                    first_inner_call = inner_events[0][0]
                    nested_boundary = _MotionBoundary(
                        order=boundary_order(
                            wrapper_call,
                            first_inner_call,
                            nested=True,
                        ),
                        call=wrapper_call,
                        helper_name="<unproven-inner-motion-helper>",
                        helper=None,
                        command_aliases=frozenset(),
                        tool_aliases=frozenset(),
                        sample_parameter=sample_parameter,
                        sample_name=sample_name,
                        wrapper_name=wrapper_name,
                        wrapper_call_line=getattr(wrapper_call, "lineno", 0),
                        failure_reason=(
                            "wrapper did not pass the selected tool center and "
                            "command together to an inner motion helper"
                        ),
                    )
                if nested_boundary is not None:
                    boundaries.append(nested_boundary)
                    break

        if not boundaries:
            continue

        earliest_order = min(boundary.order for boundary in boundaries)
        for boundary in (
            candidate
            for candidate in boundaries
            if candidate.order == earliest_order
        ):
            if boundary.failure_reason is not None:
                append_motion_failure(
                    call=boundary.call,
                    capability_id=capability_id,
                    public_name=public_name,
                    sample_name=boundary.sample_name,
                    helper_name=boundary.helper_name,
                    tool_parameter=None,
                    command_parameter=None,
                    send_lines=[],
                    unproven=[],
                    wrapper_name=boundary.wrapper_name,
                    wrapper_call_line=boundary.wrapper_call_line,
                    wrapper_sample_parameter=boundary.sample_parameter,
                    reason=boundary.failure_reason,
                )
                continue

            if boundary.helper is None:
                continue
            validate_motion_call(
                call=boundary.call,
                helper_name=boundary.helper_name,
                helper=boundary.helper,
                command_aliases=boundary.command_aliases,
                tool_aliases=boundary.tool_aliases,
                sample_parameter=boundary.sample_parameter,
                capability_id=capability_id,
                public_name=public_name,
                sample_name=boundary.sample_name,
                wrapper_name=boundary.wrapper_name,
                wrapper_call_line=boundary.wrapper_call_line,
            )


def _validate_deterministic_time_usage(
    tree: ast.Module,
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Keep generated timing bindable to the framework-owned MuJoCo clock.

    The harness replaces the generated module's global ``time`` binding before
    a MuJoCo call.  A local import, import alias, or captured time callable
    would retain the host clock and bypass deterministic stepping.  Hardware
    execution still receives the ordinary Python module through the same
    source-level ``import time`` contract.
    """

    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    top_level_imports = {
        id(statement)
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports_time = alias.name == "time" or alias.name.startswith("time.")
                shadows_time = alias.asname == "time" and alias.name != "time"
                if imports_time and (
                    alias.name != "time"
                    or alias.asname is not None
                    or id(node) not in top_level_imports
                ):
                    _append_node_failure(
                        failures,
                        node,
                        "INVALID_TIME_IMPORT_STYLE",
                        "generated timing must use one module-level `import time` without an alias",
                        path,
                        observed=(
                            f"import {alias.name} as {alias.asname}"
                            if alias.asname is not None
                            else f"import {alias.name}"
                        ),
                        target="import time",
                    )
                elif shadows_time:
                    _append_node_failure(
                        failures,
                        node,
                        "TIME_BINDING_ALIAS_FORBIDDEN",
                        "the reserved generated-code name `time` may bind only the standard time module",
                        path,
                        observed=f"import {alias.name} as time",
                        target="import time",
                    )
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lstrip(".")
            imports_from_time = module == "time" or module.startswith("time.")
            shadows_time = any(
                alias.asname == "time" or (alias.asname is None and alias.name == "time")
                for alias in node.names
            )
            if imports_from_time:
                _append_node_failure(
                    failures,
                    node,
                    "INVALID_TIME_IMPORT_STYLE",
                    "generated timing must use `import time`; `from time import ...` cannot be rebound to the MuJoCo clock",
                    path,
                    observed=ast.unparse(node),
                    target="import time",
                )
            elif shadows_time:
                _append_node_failure(
                    failures,
                    node,
                    "TIME_BINDING_ALIAS_FORBIDDEN",
                    "the reserved generated-code name `time` may bind only the standard time module",
                    path,
                    observed=ast.unparse(node),
                    target="import time",
                )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "time":
                _append_node_failure(
                    failures,
                    node,
                    "TIME_BINDING_ALIAS_FORBIDDEN",
                    "generated code must not replace the framework-bindable `time` module name",
                    path,
                    observed=node.name,
                    target="import time",
                )
        if isinstance(node, ast.arg) and node.arg == "time":
            _append_node_failure(
                failures,
                node,
                "TIME_BINDING_ALIAS_FORBIDDEN",
                "generated helpers must not shadow the framework-bindable `time` module name",
                path,
                observed=node.arg,
                target="import time",
            )
        if isinstance(node, ast.ExceptHandler) and node.name == "time":
            _append_node_failure(
                failures,
                node,
                "TIME_BINDING_ALIAS_FORBIDDEN",
                "generated code must not shadow the framework-bindable `time` module name",
                path,
                observed=node.name,
                target="import time",
            )
        if isinstance(node, ast.Name) and node.id == "time":
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                _append_node_failure(
                    failures,
                    node,
                    "TIME_BINDING_ALIAS_FORBIDDEN",
                    "generated code must not replace the framework-bindable `time` module name",
                    path,
                    observed=type(node.ctx).__name__,
                    target="import time",
                )
                continue
            parent = parents.get(id(node))
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in _ALLOWED_TIME_CALLS
            ):
                _append_node_failure(
                    failures,
                    node,
                    "TIME_BINDING_ALIAS_FORBIDDEN",
                    "use `time.sleep(...)` or `time.monotonic()` directly; do not capture or pass the time module",
                    path,
                    observed="time",
                    target=sorted(f"time.{name}" for name in _ALLOWED_TIME_CALLS),
                )
                continue
            grandparent = parents.get(id(parent))
            if not isinstance(grandparent, ast.Call) or grandparent.func is not parent:
                _append_node_failure(
                    failures,
                    parent,
                    "TIME_BINDING_ALIAS_FORBIDDEN",
                    "time.sleep and time.monotonic must be called directly, not stored or passed as aliases",
                    path,
                    observed=f"time.{parent.attr}",
                    target=f"time.{parent.attr}(...)",
                )
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "time"
            and node.attr not in _ALLOWED_TIME_CALLS
        ):
            _append_node_failure(
                failures,
                node,
                "UNDOCUMENTED_TIME_ATTRIBUTE",
                "generated code may call only time.sleep and time.monotonic",
                path,
                observed=f"time.{node.attr}",
                target=sorted(f"time.{name}" for name in _ALLOWED_TIME_CALLS),
            )


def _validate_import_contract(
    tree: ast.Module,
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Enforce the complete, module-specific generated import allowlist."""

    filename = path.rsplit("/", 1)[-1]
    top_level_imports = {
        id(statement)
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if id(node) not in top_level_imports:
            _append_node_failure(
                failures,
                node,
                "INVALID_GENERATED_IMPORT",
                "generated imports must appear only at module scope",
                path,
                observed=ast.unparse(node),
            )
            continue

        valid = False
        if isinstance(node, ast.Import):
            allowed = (
                {"math"}
                if filename == "_kinematics.py"
                else {"math", "time"}
                if filename in {"g1.py", "g2.py", "g3.py"}
                else set()
            )
            valid = bool(node.names) and all(
                alias.name in allowed and alias.asname is None
                for alias in node.names
            )
        else:
            if (
                node.level == 0
                and node.module == "__future__"
                and len(node.names) == 1
                and node.names[0].name == "annotations"
                and node.names[0].asname is None
                and filename != "__init__.py"
            ):
                valid = True
            elif (
                filename in {"g2.py", "g3.py"}
                and node.level == 1
                and node.module == "_kinematics"
                and bool(node.names)
                and all(
                    alias.asname is None
                    and alias.name.startswith("_")
                    and not alias.name.startswith("__")
                    for alias in node.names
                )
            ):
                valid = True

        if not valid:
            _append_node_failure(
                failures,
                node,
                "INVALID_GENERATED_IMPORT",
                (
                    "imports are module-specific: _kinematics may import only "
                    "math/future; g1-g3 may import only math/time/future; and "
                    "g2/g3 alone may import private symbols from exactly "
                    "`._kinematics` without aliases"
                ),
                path,
                observed=ast.unparse(node),
            )


def _validate_package_init(
    tree: ast.Module,
    failures: list[StaticValidationFailure],
) -> None:
    """Keep package initialization inert; loaders bind declared modules directly."""

    path = f"{PACKAGE_NAME}/__init__.py"
    for index, statement in enumerate(tree.body):
        if _is_module_docstring(statement, index):
            continue
        _append_node_failure(
            failures,
            statement,
            "NONEMPTY_PACKAGE_INIT",
            "generated package __init__.py may contain only one package docstring",
            path,
            observed=type(statement).__name__,
        )


def _validate_private_kinematics_module(
    tree: ast.Module,
    failures: list[StaticValidationFailure],
) -> None:
    """Keep the required shared support module private and purely mathematical."""

    path = f"{PACKAGE_NAME}/_kinematics.py"
    bound_names: set[str] = set()
    private_functions: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef):
            bound_names.add(statement.name)
            private_functions.add(statement.name)
            if (
                not statement.name.startswith("_")
                or statement.name.startswith("__")
            ):
                _append_node_failure(
                    failures,
                    statement,
                    "INVALID_PRIVATE_KINEMATICS",
                    "all _kinematics functions must be single-underscore private",
                    path,
                    observed=statement.name,
                )
        elif isinstance(statement, ast.AsyncFunctionDef):
            bound_names.add(statement.name)
            _append_node_failure(
                failures,
                statement,
                "INVALID_PRIVATE_KINEMATICS",
                "the pure mathematical _kinematics module may not define async functions",
                path,
                observed=statement.name,
            )
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            if value is None or not _immutable_kinematics_constant(value):
                _append_node_failure(
                    failures,
                    statement,
                    "KINEMATICS_MUTABLE_GLOBAL_FORBIDDEN",
                    "_kinematics module constants must be recursively immutable",
                    path,
                    observed=ast.unparse(value) if value is not None else None,
                )
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                for name_node in ast.walk(target):
                    if isinstance(name_node, ast.Name):
                        bound_names.add(name_node.id)
                        if (
                            not name_node.id.startswith("_")
                            or name_node.id.startswith("__")
                        ):
                            _append_node_failure(
                                failures,
                                name_node,
                                "INVALID_PRIVATE_KINEMATICS",
                                "all _kinematics constants must be single-underscore private",
                                path,
                                observed=name_node.id,
                            )

    missing = sorted(_KINEMATICS_REUSE_SYMBOLS - bound_names)
    if missing:
        failures.append(
            StaticValidationFailure(
                code="KINEMATICS_SUPPORT_INCOMPLETE",
                message="_kinematics must define the shared FK and IK entrypoints",
                path=path,
                observed=sorted(bound_names),
                target=sorted(_KINEMATICS_REUSE_SYMBOLS),
            )
        )
    _validate_forward_kinematics_site_rotation(tree, failures)
    for definition in (
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ):
        _validate_kinematics_function_mutations(
            definition,
            bound_names,
            path,
            failures,
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "runtime":
            _append_node_failure(
                failures,
                node,
                "INVALID_PRIVATE_KINEMATICS",
                "_kinematics is pure math and may not receive or reference runtime",
                path,
                observed="runtime",
            )
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            _append_node_failure(
                failures,
                node,
                "KINEMATICS_SIDE_EFFECT_FORBIDDEN",
                "_kinematics functions may not mutate global or nonlocal state",
                path,
                observed=ast.unparse(node),
            )
        if isinstance(node, ast.Attribute):
            valid_math_attribute = (
                isinstance(node.value, ast.Name)
                and node.value.id == "math"
                and not node.attr.startswith("_")
            )
            if not valid_math_attribute:
                _append_node_failure(
                    failures,
                    node,
                    "KINEMATICS_SIDE_EFFECT_FORBIDDEN",
                    "_kinematics may access attributes only on the math module",
                    path,
                    observed=ast.unparse(node),
                )
        if isinstance(node, ast.Call):
            valid_call = False
            if isinstance(node.func, ast.Name):
                valid_call = (
                    node.func.id in _PURE_MATH_BUILTIN_CALLS
                    or node.func.id in private_functions
                )
            elif isinstance(node.func, ast.Attribute):
                valid_call = (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "math"
                    and not node.func.attr.startswith("_")
                )
            if not valid_call:
                _append_node_failure(
                    failures,
                    node,
                    "KINEMATICS_SIDE_EFFECT_FORBIDDEN",
                    "_kinematics may call only pure builtins, private helpers, and math",
                    path,
                    observed=ast.unparse(node.func),
                )


def _validate_forward_kinematics_site_rotation(
    tree: ast.Module,
    failures: list[StaticValidationFailure],
) -> None:
    """Require the MJCF site rotation on returned gripper-frame tool offsets.

    This is deliberately a conservative AST data-flow check.  Generated source
    remains untrusted text: the validator neither imports it nor calls any of
    its helpers.  The analysis follows returned values through private helper
    calls so the reference-style ``pose -> tool point -> FK`` factoring remains
    valid, while an unrelated/dead quaternion reference cannot satisfy the
    gate.
    """

    definitions = {
        statement.name: statement
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef)
    }
    forward = definitions.get("_forward_kinematics")
    if forward is None:
        return

    positional_argument_names = [
        argument.arg
        for argument in (*forward.args.posonlyargs, *forward.args.args)
    ]
    argument_names = {
        argument.arg
        for argument in (
            *forward.args.posonlyargs,
            *forward.args.args,
            *forward.args.kwonlyargs,
        )
    }
    non_joint_argument_names = set(argument_names)
    if positional_argument_names:
        non_joint_argument_names.discard(positional_argument_names[0])
    tool_point_names = {
        name
        for name in non_joint_argument_names
        if any(marker in name.lower() for marker in ("tool", "point", "offset"))
    }
    if len(positional_argument_names) >= 2:
        # The generated FK contract uses its first positional input for arm
        # joints and its second for the controlled point in gripperframe.  Do
        # not let a cosmetic parameter rename bypass the morphology gate.
        tool_point_names.add(positional_argument_names[1])
    if not tool_point_names:
        # Deterministic offline fixtures expose only the frame origin.  The
        # site-orientation contract becomes relevant as soon as FK accepts a
        # point expressed in gripperframe coordinates.
        return

    module_site_quaternion_defined = any(
        _assignment_binds_name(statement, _GRIPPERFRAME_SITE_QUATERNION)
        for statement in tree.body
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
    )
    analysis = _KinematicsSiteRotationFlow(
        definitions,
        module_site_quaternion_defined=module_site_quaternion_defined,
    )
    initial_bindings = {
        name: frozenset({_TOOL_POINT_FLOW_TAG})
        if name in tool_point_names
        else frozenset()
        for name in argument_names
    }
    returned_tags = analysis.analyze_function(
        forward,
        initial_bindings,
        active_functions=frozenset(),
    )
    if (
        _SITE_TOOL_OFFSET_FLOW_TAG in returned_tags
        and _UNROTATED_TOOL_OFFSET_FLOW_TAG not in returned_tags
    ):
        return

    bad_transform = next(
        (
            item
            for item in analysis.tool_point_transforms
            if not item["uses_composed_site_rotation"]
        ),
        None,
    )
    failure_node: ast.AST = (
        bad_transform["node"] if bad_transform is not None else forward
    )
    observed_transforms = [
        {
            "line": item["line"],
            "expression": item["expression"],
            "uses_composed_site_rotation": item[
                "uses_composed_site_rotation"
            ],
        }
        for item in analysis.tool_point_transforms[:4]
    ]
    _append_node_failure(
        failures,
        failure_node,
        "KINEMATICS_SITE_ROTATION_OMITTED",
        (
            "_forward_kinematics must compose "
            "_GRIPPERFRAME_QUATERNION_WXYZ into the rotation used to "
            "transform its gripper-frame tool point, and return that result"
        ),
        f"{PACKAGE_NAME}/_kinematics.py",
        observed={
            "function": forward.name,
            "tool_point_parameters": sorted(tool_point_names),
            "module_site_quaternion_defined": module_site_quaternion_defined,
            "site_quaternion_reference_lines": sorted(
                analysis.site_quaternion_reference_lines
            ),
            "site_rotation_composition_lines": sorted(
                analysis.site_rotation_composition_lines
            ),
            "tool_point_transform_calls": observed_transforms,
            "returned_site_rotated_tool_offset": (
                _SITE_TOOL_OFFSET_FLOW_TAG in returned_tags
            ),
            "returned_unrotated_tool_offset": (
                _UNROTATED_TOOL_OFFSET_FLOW_TAG in returned_tags
            ),
        },
        target={
            "required_dataflow": [
                "_GRIPPERFRAME_QUATERNION_WXYZ",
                "_quaternion_matrix_wxyz(site_quaternion)",
                "_matmul3(arm_rotation, site_quaternion_rotation)",
                "_matvec3(composed_site_rotation, tool_point_in_gripperframe_m)",
                "returned tool position",
            ],
            "returned_site_rotated_tool_offset": True,
            "returned_unrotated_tool_offset": False,
        },
    )


class _KinematicsSiteRotationFlow:
    """Small interprocedural taint analysis for the FK site transform."""

    def __init__(
        self,
        definitions: Mapping[str, ast.FunctionDef],
        *,
        module_site_quaternion_defined: bool,
    ) -> None:
        self._definitions = definitions
        self._module_site_quaternion_defined = module_site_quaternion_defined
        self.site_quaternion_reference_lines: set[int] = set()
        self.site_rotation_composition_lines: set[int] = set()
        self.tool_point_transforms: list[dict[str, Any]] = []
        self._observed_transform_nodes: set[int] = set()

    def analyze_function(
        self,
        definition: ast.FunctionDef,
        initial_bindings: Mapping[str, frozenset[str]],
        *,
        active_functions: frozenset[str],
    ) -> frozenset[str]:
        if definition.name in active_functions:
            return frozenset()
        active = active_functions | {definition.name}
        bindings = dict(initial_bindings)
        return_tags: list[frozenset[str]] = []
        self._analyze_statements(definition.body, bindings, active, return_tags)
        return frozenset().union(*return_tags) if return_tags else frozenset()

    def _analyze_statements(
        self,
        statements: list[ast.stmt],
        bindings: dict[str, frozenset[str]],
        active_functions: frozenset[str],
        return_tags: list[frozenset[str]],
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.Assign):
                tags = self._expression_tags(
                    statement.value, bindings, active_functions
                )
                for target in statement.targets:
                    _bind_flow_tags(target, tags, bindings)
            elif isinstance(statement, ast.AnnAssign):
                tags = (
                    self._expression_tags(
                        statement.value, bindings, active_functions
                    )
                    if statement.value is not None
                    else frozenset()
                )
                _bind_flow_tags(statement.target, tags, bindings)
            elif isinstance(statement, ast.AugAssign):
                tags = self._expression_tags(
                    statement.value, bindings, active_functions
                ) | self._expression_tags(
                    statement.target, bindings, active_functions
                )
                _bind_flow_tags(statement.target, tags, bindings)
            elif isinstance(statement, ast.Return):
                return_tags.append(
                    self._expression_tags(
                        statement.value, bindings, active_functions
                    )
                    if statement.value is not None
                    else frozenset()
                )
                # Statements after an unconditional return in the same block
                # are dead and must not provide a fake satisfying data-flow.
                break
            elif isinstance(statement, ast.Raise):
                if statement.exc is not None:
                    self._expression_tags(
                        statement.exc, bindings, active_functions
                    )
                break
            elif isinstance(statement, (ast.Break, ast.Continue)):
                break
            elif isinstance(statement, ast.If):
                self._expression_tags(statement.test, bindings, active_functions)
                branch_bindings = dict(bindings)
                else_bindings = dict(bindings)
                self._analyze_statements(
                    statement.body,
                    branch_bindings,
                    active_functions,
                    return_tags,
                )
                self._analyze_statements(
                    statement.orelse,
                    else_bindings,
                    active_functions,
                    return_tags,
                )
                _merge_flow_bindings(bindings, branch_bindings, else_bindings)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                iterator_tags = self._expression_tags(
                    statement.iter, bindings, active_functions
                )
                body_bindings = dict(bindings)
                _bind_flow_tags(statement.target, iterator_tags, body_bindings)
                self._analyze_statements(
                    statement.body,
                    body_bindings,
                    active_functions,
                    return_tags,
                )
                else_bindings = dict(bindings)
                self._analyze_statements(
                    statement.orelse,
                    else_bindings,
                    active_functions,
                    return_tags,
                )
                _merge_flow_bindings(bindings, body_bindings, else_bindings)
            elif isinstance(statement, ast.While):
                self._expression_tags(statement.test, bindings, active_functions)
                body_bindings = dict(bindings)
                self._analyze_statements(
                    statement.body,
                    body_bindings,
                    active_functions,
                    return_tags,
                )
                else_bindings = dict(bindings)
                self._analyze_statements(
                    statement.orelse,
                    else_bindings,
                    active_functions,
                    return_tags,
                )
                _merge_flow_bindings(bindings, body_bindings, else_bindings)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                body_bindings = dict(bindings)
                for item in statement.items:
                    tags = self._expression_tags(
                        item.context_expr, bindings, active_functions
                    )
                    if item.optional_vars is not None:
                        _bind_flow_tags(item.optional_vars, tags, body_bindings)
                self._analyze_statements(
                    statement.body,
                    body_bindings,
                    active_functions,
                    return_tags,
                )
                _merge_flow_bindings(bindings, bindings, body_bindings)
            elif isinstance(statement, ast.Try):
                alternatives: list[dict[str, frozenset[str]]] = []
                body_bindings = dict(bindings)
                self._analyze_statements(
                    statement.body,
                    body_bindings,
                    active_functions,
                    return_tags,
                )
                alternatives.append(body_bindings)
                for handler in statement.handlers:
                    handler_bindings = dict(bindings)
                    self._analyze_statements(
                        handler.body,
                        handler_bindings,
                        active_functions,
                        return_tags,
                    )
                    alternatives.append(handler_bindings)
                for alternative in alternatives:
                    self._analyze_statements(
                        statement.orelse,
                        alternative,
                        active_functions,
                        return_tags,
                    )
                    self._analyze_statements(
                        statement.finalbody,
                        alternative,
                        active_functions,
                        return_tags,
                    )
                _merge_flow_bindings(bindings, *alternatives)
            elif isinstance(statement, ast.Expr):
                self._expression_tags(
                    statement.value, bindings, active_functions
                )

    def _expression_tags(
        self,
        expression: ast.expr,
        bindings: Mapping[str, frozenset[str]],
        active_functions: frozenset[str],
    ) -> frozenset[str]:
        if isinstance(expression, ast.Name):
            tags = bindings.get(expression.id, frozenset())
            if (
                expression.id == _GRIPPERFRAME_SITE_QUATERNION
                and self._module_site_quaternion_defined
            ):
                self.site_quaternion_reference_lines.add(expression.lineno)
                tags |= {_SITE_QUATERNION_FLOW_TAG}
            return frozenset(tags)

        if isinstance(expression, ast.Call):
            positional = [
                self._expression_tags(item, bindings, active_functions)
                for item in expression.args
            ]
            keywords = {
                keyword.arg: self._expression_tags(
                    keyword.value, bindings, active_functions
                )
                for keyword in expression.keywords
                if keyword.arg is not None
            }
            called = _called_name(expression.func)
            argument_tags = frozenset().union(
                *positional,
                *keywords.values(),
            ) if positional or keywords else frozenset()

            if called == "_quaternion_matrix_wxyz":
                result = set(argument_tags)
                if _SITE_QUATERNION_FLOW_TAG in argument_tags:
                    result.add(_SITE_QUATERNION_MATRIX_FLOW_TAG)
                return frozenset(result)

            if called == "_matmul3":
                result = set(argument_tags)
                if _SITE_QUATERNION_MATRIX_FLOW_TAG in argument_tags:
                    result.add(_SITE_ROTATION_FLOW_TAG)
                    self.site_rotation_composition_lines.add(expression.lineno)
                return frozenset(result)

            if called == "_matvec3":
                matrix_tags = _flow_call_argument(
                    positional,
                    keywords,
                    0,
                    ("matrix", "rotation", "left"),
                )
                vector_tags = _flow_call_argument(
                    positional,
                    keywords,
                    1,
                    ("vector", "point", "right"),
                )
                result = set(argument_tags)
                if _TOOL_POINT_FLOW_TAG in vector_tags:
                    uses_site_rotation = (
                        _SITE_ROTATION_FLOW_TAG in matrix_tags
                    )
                    if id(expression) not in self._observed_transform_nodes:
                        self._observed_transform_nodes.add(id(expression))
                        self.tool_point_transforms.append(
                            {
                                "node": expression,
                                "line": expression.lineno,
                                "expression": ast.unparse(expression),
                                "uses_composed_site_rotation": uses_site_rotation,
                            }
                        )
                    if uses_site_rotation:
                        result.add(_SITE_TOOL_OFFSET_FLOW_TAG)
                    else:
                        result.add(_UNROTATED_TOOL_OFFSET_FLOW_TAG)
                return frozenset(result)

            callee = self._definitions.get(called or "")
            if callee is not None and callee.name not in active_functions:
                call_bindings = _bind_flow_call_arguments(
                    callee,
                    positional,
                    keywords,
                )
                return self.analyze_function(
                    callee,
                    call_bindings,
                    active_functions=active_functions,
                )
            return argument_tags

        tags: set[str] = set()
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                tags.update(
                    self._expression_tags(child, bindings, active_functions)
                )
            elif isinstance(child, ast.comprehension):
                tags.update(
                    self._expression_tags(child.iter, bindings, active_functions)
                )
                for condition in child.ifs:
                    tags.update(
                        self._expression_tags(
                            condition, bindings, active_functions
                        )
                    )
        return frozenset(tags)


def _assignment_binds_name(
    statement: ast.Assign | ast.AnnAssign,
    name: str,
) -> bool:
    targets = (
        statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    )
    return any(
        isinstance(node, ast.Name) and node.id == name
        for target in targets
        for node in ast.walk(target)
    )


def _bind_flow_tags(
    target: ast.expr,
    tags: frozenset[str],
    bindings: dict[str, frozenset[str]],
) -> None:
    if isinstance(target, ast.Name):
        bindings[target.id] = tags
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _bind_flow_tags(item, tags, bindings)


def _merge_flow_bindings(
    destination: dict[str, frozenset[str]],
    *sources: Mapping[str, frozenset[str]],
) -> None:
    names = set(destination)
    for source in sources:
        names.update(source)
    for name in names:
        destination[name] = frozenset().union(
            *(source.get(name, frozenset()) for source in sources)
        )


def _flow_call_argument(
    positional: list[frozenset[str]],
    keywords: Mapping[str, frozenset[str]],
    index: int,
    keyword_names: tuple[str, ...],
) -> frozenset[str]:
    if index < len(positional):
        return positional[index]
    return frozenset().union(
        *(keywords.get(name, frozenset()) for name in keyword_names)
    )


def _bind_flow_call_arguments(
    definition: ast.FunctionDef,
    positional: list[frozenset[str]],
    keywords: Mapping[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    parameters = [*definition.args.posonlyargs, *definition.args.args]
    bindings = {
        parameter.arg: positional[index]
        if index < len(positional)
        else keywords.get(parameter.arg, frozenset())
        for index, parameter in enumerate(parameters)
    }
    for parameter in definition.args.kwonlyargs:
        bindings[parameter.arg] = keywords.get(parameter.arg, frozenset())
    if definition.args.vararg is not None:
        bindings[definition.args.vararg.arg] = frozenset().union(
            *positional[len(parameters) :]
        )
    if definition.args.kwarg is not None:
        declared = {parameter.arg for parameter in parameters}
        declared.update(parameter.arg for parameter in definition.args.kwonlyargs)
        bindings[definition.args.kwarg.arg] = frozenset().union(
            *(
                tags
                for name, tags in keywords.items()
                if name not in declared
            )
        )
    return bindings


def _validate_kinematics_function_mutations(
    definition: ast.FunctionDef,
    module_bindings: set[str],
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Allow local numerical work arrays, but never mutate inputs or globals."""

    argument_names = {
        argument.arg
        for argument in (
            *definition.args.posonlyargs,
            *definition.args.args,
            *definition.args.kwonlyargs,
        )
    }
    if definition.args.vararg is not None:
        argument_names.add(definition.args.vararg.arg)
    if definition.args.kwarg is not None:
        argument_names.add(definition.args.kwarg.arg)
    protected_names = set(argument_names) | set(module_bindings)

    # A direct alias retains the same mutable object.  Iterate because aliases
    # may be chained; constructors such as list(seed) intentionally create a
    # fresh local work array and are not tainted.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(definition):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in protected_names:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in protected_names:
                    protected_names.add(target.id)
                    changed = True

    for node in ast.walk(definition):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)
        else:
            continue
        forbidden = False
        for target in targets:
            root = target
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if (
                isinstance(root, ast.Name)
                and root.id in protected_names
                and (
                    isinstance(target, (ast.Attribute, ast.Subscript))
                    or isinstance(node, (ast.AugAssign, ast.Delete))
                )
            ):
                forbidden = True
                break
        if forbidden:
            _append_node_failure(
                failures,
                node,
                "KINEMATICS_MUTATION_FORBIDDEN",
                "_kinematics may not mutate arguments or module-shared state",
                path,
                observed=ast.unparse(node),
            )


def _validate_kinematics_reuse(
    tree: ast.Module,
    module_name: str,
    support_tree: ast.Module | None,
    failures: list[StaticValidationFailure],
) -> None:
    """Require G2/G3 to reuse shared FK/IK rather than duplicate heavy helpers."""

    path = f"{PACKAGE_NAME}/{module_name}.py"
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    imported: set[str] = set()
    import_statements: list[ast.ImportFrom] = []
    import_nodes: dict[str, ast.ImportFrom] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 1
            and statement.module == "_kinematics"
        ):
            import_statements.append(statement)
            for alias in statement.names:
                imported.add(alias.name)
                import_nodes[alias.name] = statement

    missing_imports = sorted(_KINEMATICS_REUSE_SYMBOLS - imported)
    if missing_imports:
        failures.append(
            StaticValidationFailure(
                code="KINEMATICS_REUSE_REQUIRED",
                message=(
                    f"{module_name} must import the shared FK and IK from "
                    "exactly `._kinematics`"
                ),
                path=path,
                observed=sorted(imported),
                target=sorted(_KINEMATICS_REUSE_SYMBOLS),
            )
        )
    extra_imports = sorted(imported - _KINEMATICS_REUSE_SYMBOLS)
    if extra_imports or len(import_statements) != 1:
        failures.append(
            StaticValidationFailure(
                code="KINEMATICS_IMPORT_SURFACE_INVALID",
                message=(
                    f"{module_name} must use one relative import containing only "
                    "the shared FK and IK entrypoints"
                ),
                path=path,
                observed={
                    "imports": sorted(imported),
                    "statement_count": len(import_statements),
                },
                target=sorted(_KINEMATICS_REUSE_SYMBOLS),
            )
        )

    support_names: set[str] = set()
    if support_tree is not None:
        for statement in support_tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                support_names.add(statement.name)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    support_names.update(
                        node.id for node in ast.walk(target) if isinstance(node, ast.Name)
                    )
            elif isinstance(statement, ast.AnnAssign):
                support_names.update(
                    node.id
                    for node in ast.walk(statement.target)
                    if isinstance(node, ast.Name)
                )
    undefined = sorted(imported - support_names) if support_tree is not None else []
    for name in undefined:
        _append_node_failure(
            failures,
            import_nodes[name],
            "KINEMATICS_IMPORT_UNDEFINED",
            "relative kinematics import names a symbol absent from _kinematics.py",
            path,
            observed=name,
            target=sorted(support_names),
        )

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in imported
        ):
            parent = parents.get(id(node))
            if not isinstance(parent, ast.Call) or parent.func is not node:
                _append_node_failure(
                    failures,
                    node,
                    "KINEMATICS_ALIAS_FORBIDDEN",
                    (
                        "imported kinematics symbols may only be called directly; "
                        "they may not be stored, returned, or passed as aliases"
                    ),
                    path,
                    observed=node.id,
                )
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in imported:
            continue
        argument_nodes: list[ast.AST] = [*node.args]
        argument_nodes.extend(keyword.value for keyword in node.keywords)
        if any(
            isinstance(descendant, ast.Name) and descendant.id == "runtime"
            for argument in argument_nodes
            for descendant in ast.walk(argument)
        ):
            _append_node_failure(
                failures,
                node,
                "RUNTIME_TO_KINEMATICS_FORBIDDEN",
                (
                    "runtime may not be passed directly or inside a nested "
                    "expression to a private kinematics helper"
                ),
                path,
                observed=ast.unparse(node),
            )

    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            statement.name.startswith(prefix)
            for prefix in _HEAVY_KINEMATICS_PREFIXES
        ):
            _append_node_failure(
                failures,
                statement,
                "DUPLICATE_KINEMATICS_HELPER",
                (
                    f"{module_name} must reuse _kinematics instead of redefining "
                    f"heavy helper {statement.name!r}"
                ),
                path,
                observed=statement.name,
            )


def _validate_definition_eagerness(
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    if definition.decorator_list:
        _append_node_failure(
            failures,
            definition,
            "TOP_LEVEL_SIDE_EFFECT",
            "decorators execute while the module is imported and are not allowed",
            path,
        )
    if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
        eager_values: list[ast.expr] = list(definition.args.defaults)
        eager_values.extend(value for value in definition.args.kw_defaults if value is not None)
        for value in eager_values:
            if not _safe_eager_expr(value):
                _append_node_failure(
                    failures,
                    value,
                    "TOP_LEVEL_SIDE_EFFECT",
                    "function defaults must be side-effect-free constant expressions",
                    path,
                )
        annotations = [
            argument.annotation
            for argument in (
                *definition.args.posonlyargs,
                *definition.args.args,
                *definition.args.kwonlyargs,
            )
            if argument.annotation is not None
        ]
        if definition.args.vararg is not None and definition.args.vararg.annotation is not None:
            annotations.append(definition.args.vararg.annotation)
        if definition.args.kwarg is not None and definition.args.kwarg.annotation is not None:
            annotations.append(definition.args.kwarg.annotation)
        if definition.returns is not None:
            annotations.append(definition.returns)
        for annotation in annotations:
            if not _safe_annotation_expr(annotation):
                _append_node_failure(
                    failures,
                    annotation,
                    "UNSAFE_ANNOTATION",
                    "annotations must be side-effect-free built-in type expressions",
                    path,
                )
        return

    for base in definition.bases:
        if not _safe_eager_expr(base):
            _append_node_failure(
                failures,
                base,
                "TOP_LEVEL_SIDE_EFFECT",
                "class bases must be side-effect-free expressions",
                path,
            )
    if definition.keywords:
        _append_node_failure(
            failures,
            definition,
            "TOP_LEVEL_SIDE_EFFECT",
            "class metaclass keywords are not allowed",
            path,
        )
    for index, statement in enumerate(definition.body):
        if _is_module_docstring(statement, index):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _validate_definition_eagerness(statement, path, failures)
            continue
        if isinstance(statement, ast.Assign):
            if all(_safe_assignment_target(target) for target in statement.targets) and _safe_eager_expr(statement.value):
                continue
        if isinstance(statement, ast.AnnAssign):
            if (
                _safe_assignment_target(statement.target)
                and statement.value is not None
                and _safe_eager_expr(statement.value)
                and _safe_eager_expr(statement.annotation)
            ):
                continue
        if isinstance(statement, ast.Pass):
            continue
        _append_node_failure(
            failures,
            statement,
            "TOP_LEVEL_SIDE_EFFECT",
            "class body contains code that executes during module import",
            path,
            observed=type(statement).__name__,
        )


def _validate_capability_functions(
    tree: ast.Module,
    module_name: str,
    expected: Mapping[str, _CapabilitySpec],
    manifest_by_id: Mapping[str, Mapping[str, Any]],
    path: str,
    failures: list[StaticValidationFailure],
) -> None:
    module_specs = {
        spec.function_name: spec
        for spec in expected.values()
        if spec.module == module_name
    }
    definitions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(statement.name, []).append(statement)

    for name, nodes in definitions.items():
        if len(nodes) > 1:
            _append_node_failure(
                failures,
                nodes[1],
                "DUPLICATE_FUNCTION_DEFINITION",
                f"function {name!r} is defined more than once",
                path,
                observed=len(nodes),
                target=1,
            )
        if not name.startswith("_") and name not in module_specs:
            _append_node_failure(
                failures,
                nodes[0],
                "UNEXPECTED_PUBLIC_FUNCTION",
                f"public function {name!r} is not frozen in Stage 1 for {module_name}",
                path,
                observed=name,
                target=sorted(module_specs),
            )

    for function_name, spec in module_specs.items():
        nodes = definitions.get(function_name)
        if not nodes:
            failures.append(
                StaticValidationFailure(
                    code="MISSING_CAPABILITY_FUNCTION",
                    message=f"expected public function {function_name!r} is not defined",
                    path=path,
                    capability_id=spec.capability_id,
                    target=function_name,
                )
            )
            continue
        function = nodes[0]
        positional = [*function.args.posonlyargs, *function.args.args]
        if not positional or positional[0].arg != "runtime":
            _append_node_failure(
                failures,
                function,
                "RUNTIME_NOT_FIRST_PARAMETER",
                "the first positional parameter must be named 'runtime'",
                path,
                capability_id=spec.capability_id,
                observed=positional[0].arg if positional else None,
                target="runtime",
            )

        parameters = [*positional, *function.args.kwonlyargs]
        if function.args.vararg is not None:
            parameters.append(function.args.vararg)
        if function.args.kwarg is not None:
            parameters.append(function.args.kwarg)
        for parameter in parameters:
            if parameter.annotation is None:
                _append_node_failure(
                    failures,
                    parameter,
                    "MISSING_PARAMETER_ANNOTATION",
                    f"parameter {parameter.arg!r} has no type annotation",
                    path,
                    capability_id=spec.capability_id,
                    observed=None,
                    target="type annotation",
                )
        if function.returns is None:
            _append_node_failure(
                failures,
                function,
                "MISSING_RETURN_ANNOTATION",
                "public capability function has no return annotation",
                path,
                capability_id=spec.capability_id,
                observed=None,
                target="return annotation",
            )
        manifest_item = manifest_by_id.get(spec.capability_id)
        if manifest_item is not None:
            _validate_manifest_signature(
                function,
                manifest_item.get("signature"),
                path,
                spec.capability_id,
                failures,
            )
            _validate_result_contract(
                manifest_item.get("result_contract"),
                path,
                spec.capability_id,
                spec.validation_effect,
                failures,
            )
            _validate_public_function_semantics(
                function,
                manifest_item,
                path,
                spec.capability_id,
                failures,
            )


def _validate_layer_independence(
    tree: ast.Module,
    module_name: str,
    failures: list[StaticValidationFailure],
) -> None:
    other_layers = {"g1", "g2", "g3"} - {module_name}
    path = f"{PACKAGE_NAME}/{module_name}.py"
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
            imported.extend(alias.name for alias in node.names)
        for imported_name in imported:
            components = set(imported_name.lstrip(".").split("."))
            crossed = sorted(components & other_layers)
            if crossed:
                _append_node_failure(
                    failures,
                    node,
                    "CROSS_GRANULARITY_IMPORT",
                    f"{module_name} must be self-contained and cannot import {crossed[0]}",
                    path,
                    observed=imported_name,
                )


def _validate_manifest_signature(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    declared: Any,
    path: str,
    capability_id: str,
    failures: list[StaticValidationFailure],
) -> None:
    if not isinstance(declared, Mapping):
        return
    declared_parameters = declared.get("parameters")
    if not isinstance(declared_parameters, list):
        failures.append(
            StaticValidationFailure(
                code="MANIFEST_SIGNATURE_INCOMPLETE",
                message="signature.parameters must be an ordered array",
                path="package_manifest.json",
                capability_id=capability_id,
            )
        )
        return
    positional = [*function.args.posonlyargs, *function.args.args]
    positional_defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(function.args.defaults)
    ) + list(function.args.defaults)
    actual: list[dict[str, Any]] = []
    for parameter, default in zip(positional, positional_defaults, strict=True):
        actual.append(
            {
                "name": parameter.arg,
                "annotation": None if parameter.annotation is None else ast.unparse(parameter.annotation),
                "has_default": default is not None,
                "default": _literal_default(default),
            }
        )
    for parameter, default in zip(
        function.args.kwonlyargs,
        function.args.kw_defaults,
        strict=True,
    ):
        actual.append(
            {
                "name": parameter.arg,
                "annotation": None if parameter.annotation is None else ast.unparse(parameter.annotation),
                "has_default": default is not None,
                "default": _literal_default(default),
            }
        )
    if function.args.vararg is not None or function.args.kwarg is not None:
        _append_node_failure(
            failures,
            function,
            "UNSUPPORTED_PUBLIC_SIGNATURE",
            "public capabilities may not use *args or **kwargs",
            path,
            capability_id=capability_id,
        )
    comparable_declared: list[dict[str, Any]] = []
    for item in declared_parameters:
        if not isinstance(item, Mapping):
            comparable_declared.append({"invalid": item})
            continue
        comparable_declared.append(
            {
                "name": item.get("name"),
                "annotation": item.get("annotation"),
                "has_default": item.get("has_default"),
                "default": item.get("default") if item.get("has_default") else None,
            }
        )
    if comparable_declared != actual:
        failures.append(
            StaticValidationFailure(
                code="MANIFEST_SIGNATURE_MISMATCH",
                message="manifest signature does not match the parsed public function",
                path="package_manifest.json",
                capability_id=capability_id,
                observed=comparable_declared,
                target=actual,
            )
        )
    actual_return = None if function.returns is None else ast.unparse(function.returns)
    if declared.get("return_annotation") != actual_return:
        failures.append(
            StaticValidationFailure(
                code="MANIFEST_RETURN_MISMATCH",
                message="manifest return_annotation does not match the parsed public function",
                path="package_manifest.json",
                capability_id=capability_id,
                observed=declared.get("return_annotation"),
                target=actual_return,
            )
        )


def _literal_default(node: ast.expr | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def _validate_result_contract(
    contract: Any,
    path: str,
    capability_id: str,
    validation_effect: str,
    failures: list[StaticValidationFailure],
) -> None:
    if not isinstance(contract, Mapping):
        return
    required = contract.get("required")
    status_values = contract.get("status_values")
    success_status_values = contract.get("success_status_values")
    observability_required = {"status"}
    if capability_id.startswith("G3."):
        observability_required.add("phase_reached")
    if validation_effect == "object_move_sequence":
        observability_required.update(
            {"completed_moves", "failed_move_index", "timeout_scope"}
        )
    valid_required = (
        isinstance(required, list)
        and all(isinstance(name, str) for name in required)
        and observability_required <= set(required)
    )
    valid_statuses = (
        isinstance(status_values, list)
        and bool(status_values)
        and all(isinstance(value, str) for value in status_values)
    )
    valid_successes = (
        isinstance(success_status_values, list)
        and bool(success_status_values)
        and all(isinstance(value, str) for value in success_status_values)
        and valid_statuses
        and set(success_status_values) <= set(status_values)
    )
    if (
        contract.get("type") != "object"
        or not valid_required
        or not valid_statuses
        or not valid_successes
    ):
        failures.append(
            StaticValidationFailure(
                code="INVALID_RESULT_CONTRACT",
                message=(
                    "result_contract must require its observable status/phase "
                    f"fields {sorted(observability_required)!r} and declare "
                    "non-empty status_values plus a non-empty successful subset"
                ),
                path="package_manifest.json",
                capability_id=capability_id,
                observed=dict(contract),
            )
        )


def _validate_public_function_semantics(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    manifest_item: Mapping[str, Any],
    path: str,
    capability_id: str,
    failures: list[StaticValidationFailure],
) -> None:
    """Reject manifest semantics that the public implementation plainly ignores.

    This deliberately remains a narrow, low-false-positive check. It proves
    only that validation-bound arguments and explicit timeout arguments are
    loaded somewhere in the public function body; it does not infer whether a
    generated motion algorithm uses them correctly. Returned statuses are
    checked only when the function directly returns a dict literal whose
    ``status`` value is literal (or has literal conditional branches).
    """

    parameters = {
        parameter.arg: parameter
        for parameter in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    semantic_parameters = _validation_bound_argument_names(
        manifest_item.get("validation_binding")
    )
    semantic_parameters.update(
        name
        for name in parameters
        if name == "timeout_s" or name.endswith("_timeout_s")
    )
    loaded_names = {
        node.id
        for statement in function.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    for parameter_name in sorted(semantic_parameters & parameters.keys()):
        if parameter_name in loaded_names:
            continue
        _append_node_failure(
            failures,
            parameters[parameter_name],
            "UNUSED_SEMANTIC_PARAMETER",
            (
                f"validation-bound or timeout parameter {parameter_name!r} is "
                "never loaded by the public capability body"
            ),
            path,
            capability_id=capability_id,
            observed=parameter_name,
            target="use the parameter in the implemented capability semantics",
        )

    contract = manifest_item.get("result_contract")
    status_values = (
        contract.get("status_values") if isinstance(contract, Mapping) else None
    )
    if not (
        isinstance(status_values, list)
        and status_values
        and all(isinstance(value, str) for value in status_values)
    ):
        return
    declared_statuses = set(status_values)
    for return_node, literal_status in _direct_returned_literal_statuses(function):
        if literal_status in declared_statuses:
            continue
        _append_node_failure(
            failures,
            return_node,
            "UNDECLARED_STATUS_LITERAL",
            (
                f"returned literal status {literal_status!r} is not declared in "
                "manifest result_contract.status_values"
            ),
            path,
            capability_id=capability_id,
            observed=literal_status,
            target=sorted(declared_statuses),
        )


def _validation_bound_argument_names(binding: Any) -> set[str]:
    """Extract public argument names from the closed validation-binding schema."""

    if not isinstance(binding, Mapping):
        return set()
    names: set[str] = set()
    for field in (
        "joint_targets_argument",
        "target_argument",
        "source_argument",
        "height_delta_argument",
        "moves_argument",
        "object_extent_argument",
        "contact_height_argument",
    ):
        value = binding.get(field)
        if isinstance(value, str):
            names.add(value)
    for field in (
        "joint_target_arguments",
        "target_arguments",
        "source_arguments",
    ):
        value = binding.get(field)
        if isinstance(value, Mapping):
            names.update(name for name in value.values() if isinstance(name, str))
    return names


def _direct_returned_literal_statuses(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.Return, str]]:
    """Find literal statuses in direct dict returns, excluding nested scopes."""

    returns: list[ast.Return] = []

    class _ReturnVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Return(self, node: ast.Return) -> None:
            returns.append(node)

    visitor = _ReturnVisitor()
    for statement in function.body:
        visitor.visit(statement)

    found: list[tuple[ast.Return, str]] = []
    for return_node in returns:
        if not isinstance(return_node.value, ast.Dict):
            continue
        for key, value in zip(
            return_node.value.keys,
            return_node.value.values,
            strict=True,
        ):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value == "status"
            ):
                continue
            found.extend(
                (return_node, status)
                for status in _literal_status_branches(value)
            )
    return found


def _literal_status_branches(value: ast.expr) -> set[str]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, ast.IfExp):
        return _literal_status_branches(value.body) | _literal_status_branches(
            value.orelse
        )
    return set()


def _is_forbidden_module(module: str) -> bool:
    normalized = module.lstrip(".")
    if not normalized:
        return False
    root = normalized.split(".", 1)[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        return True
    if root in _FORBIDDEN_IMPORT_ROOTS:
        return True
    if any(
        normalized == prefix or normalized.startswith(prefix + ".")
        for prefix in _FORBIDDEN_IMPORT_PREFIXES
    ):
        return True
    for component in normalized.split("."):
        if component in _FORBIDDEN_IMPORT_COMPONENTS:
            return True
        if any(
            component.startswith(forbidden + "_")
            or component.endswith("_" + forbidden)
            for forbidden in _FORBIDDEN_IMPORT_COMPONENTS
        ):
            return True
    return False


def _called_name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _safe_assignment_target(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_safe_assignment_target(item) for item in node.elts)
    return False


def _immutable_kinematics_constant(node: ast.expr) -> bool:
    """Accept only recursively immutable support-module constant expressions."""

    if isinstance(node, ast.Constant):
        return isinstance(node.value, (bool, int, float, str, bytes, type(None)))
    if isinstance(node, ast.Tuple):
        return all(_immutable_kinematics_constant(item) for item in node.elts)
    if isinstance(node, ast.UnaryOp):
        return _immutable_kinematics_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _immutable_kinematics_constant(
            node.left
        ) and _immutable_kinematics_constant(node.right)
    if isinstance(node, ast.Name):
        return node.id.startswith("_") and not node.id.startswith("__")
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "math"
            and not node.attr.startswith("_")
        )
    return False


def _safe_eager_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return not node.id.startswith("__") and node.id not in _FORBIDDEN_CALLS
    if isinstance(node, ast.Attribute):
        return _safe_eager_expr(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_safe_eager_expr(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _safe_eager_expr(key)) and _safe_eager_expr(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.UnaryOp):
        return _safe_eager_expr(node.operand)
    if isinstance(node, ast.BinOp):
        return _safe_eager_expr(node.left) and _safe_eager_expr(node.right)
    if isinstance(node, ast.BoolOp):
        return all(_safe_eager_expr(value) for value in node.values)
    if isinstance(node, ast.Compare):
        return _safe_eager_expr(node.left) and all(
            _safe_eager_expr(comparator) for comparator in node.comparators
        )
    if isinstance(node, ast.Subscript):
        return _safe_eager_expr(node.value) and _safe_eager_expr(node.slice)
    if isinstance(node, ast.Slice):
        return all(
            value is None or _safe_eager_expr(value)
            for value in (node.lower, node.upper, node.step)
        )
    return False


def _safe_annotation_expr(node: ast.expr) -> bool:
    """Allow type syntax without permitting import-time execution."""

    if isinstance(node, ast.Name):
        return not node.id.startswith("__") and node.id not in _FORBIDDEN_CALLS
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, type(None))) or node.value is Ellipsis
    if isinstance(node, ast.Subscript):
        return _safe_annotation_expr(node.value) and _safe_annotation_expr(node.slice)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_safe_annotation_expr(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _safe_annotation_expr(node.left) and _safe_annotation_expr(node.right)
    return False


def _is_module_docstring(statement: ast.stmt, index: int) -> bool:
    return (
        index == 0
        and isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _append_node_failure(
    failures: list[StaticValidationFailure],
    node: ast.AST,
    code: str,
    message: str,
    path: str,
    *,
    capability_id: str | None = None,
    observed: Any = None,
    target: Any = None,
) -> None:
    failures.append(
        StaticValidationFailure(
            code=code,
            message=message,
            path=path,
            line=getattr(node, "lineno", None),
            column=(getattr(node, "col_offset", None) + 1)
            if getattr(node, "col_offset", None) is not None
            else None,
            capability_id=capability_id,
            observed=observed,
            target=target,
        )
    )


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
