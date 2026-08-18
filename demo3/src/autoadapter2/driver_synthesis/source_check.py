"""Focused source checks for one submitted model-generated driver.py."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from .skeleton_contract import SkeletonContractError, validate_capability_names


GenerationCondition = Literal["skeleton-assisted", "from-scratch"]


class DriverSourceError(ValueError):
    """Raised when submitted source violates a formal generation condition."""


@dataclass(frozen=True)
class DriverSourceAudit:
    condition: GenerationCondition
    capability_methods: tuple[str, ...]
    imports_trusted_skeleton: bool
    ctrl_references: int
    physics_step_references: int


_FORBIDDEN_IMPORT_ROOTS = {
    "ctypes",
    "glob",
    "httpx",
    "importlib",
    "inspect",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "urllib",
}
_FORBIDDEN_CALL_NAMES = {
    "getattr",
    "globals",
    "locals",
    "compile",
    "eval",
    "exec",
    "setattr",
    "vars",
    "__import__",
    "open",
}
_FORBIDDEN_CALL_ATTRIBUTES = {
    "from_xml_path",
    "mj_resetData",
    "mj_resetDataKeyframe",
}
_FORBIDDEN_CONSTRUCTORS = {"MjData", "MjModel"}
_FORBIDDEN_STATE_ATTRIBUTES = {
    "act",
    "act_dot",
    "qacc",
    "qacc_warmstart",
    "qpos",
    "qvel",
    "time",
    "mocap_pos",
    "mocap_quat",
    "qfrc_applied",
    "xfrc_applied",
    "userdata",
    "plugin_state",
    "xpos",
    "xquat",
    "site_xpos",
    "site_xmat",
    "site_pos",
    "site_quat",
    "site_size",
    "sensordata",
    "contact",
    "body_pos",
    "body_quat",
    "geom_pos",
    "geom_quat",
    "geom_friction",
    "actuator_gear",
    "actuator_gainprm",
    "actuator_biasprm",
    "dof_damping",
    "jnt_range",
    "eq_active",
}
_MUTATING_METHODS = {"fill", "itemset", "place", "put", "resize", "sort"}


def _contains_forbidden_state_attribute(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Attribute) and child.attr in _FORBIDDEN_STATE_ATTRIBUTES
        for child in ast.walk(node)
    )


def _attribute_path(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _target_attributes(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript):
        return _target_attributes(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [item for child in target.elts for item in _target_attributes(child)]
    return []


def _target_path(target: ast.AST) -> str:
    if isinstance(target, ast.Subscript):
        return _target_path(target.value)
    if isinstance(target, ast.Attribute):
        prefix = _target_path(target.value)
        return f"{prefix}.{target.attr}" if prefix else target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def _writes_model_state(target: ast.AST) -> bool:
    parts = _target_path(target).split(".")
    try:
        index = parts.index("model")
    except ValueError:
        return False
    # ``self.model = model`` retains the Framework object and is required.  Any
    # deeper target mutates Framework-owned model configuration or observations.
    return index < len(parts) - 1


def _has_request_abi(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return (
        len(parameters) == 2
        and parameters[0].arg == "self"
        and parameters[1].arg == "request"
        and len(node.args.posonlyargs) <= 1
        and node.args.vararg is None
        and node.args.kwarg is None
    )


class _AuditVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.function_names: list[str] = []
        self.function_nodes: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        self.top_level_functions: set[str] = set()
        self.imports_trusted_skeleton = False
        self.ctrl_references = 0
        self.physics_step_references = 0
        self._scope_depth = 0
        self._physics_step_aliases: set[str] = set()
        self._mujoco_module_aliases: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                self.errors.append(f"candidate runtime import is forbidden: {alias.name}")
            if alias.name.startswith("autoadapter2.trusted_skeletons"):
                self.imports_trusted_skeleton = True
            elif root == "autoadapter2":
                self.errors.append(f"private Framework import is forbidden: {alias.name}")
            if root == "mujoco":
                if alias.name != "mujoco":
                    self.errors.append(
                        f"internal MuJoCo module import is forbidden: {alias.name}"
                    )
                else:
                    self._mujoco_module_aliases.add(alias.asname or "mujoco")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".", 1)[0]
        if node.level:
            self.errors.append("relative imports are forbidden in submitted driver.py")
        if root in _FORBIDDEN_IMPORT_ROOTS:
            self.errors.append(f"candidate runtime import is forbidden: {module}")
        if module.startswith("autoadapter2.trusted_skeletons"):
            self.imports_trusted_skeleton = True
        elif root == "autoadapter2":
            self.errors.append(f"private Framework import is forbidden: {module}")
        if module == "mujoco":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name in _FORBIDDEN_CALL_ATTRIBUTES:
                    self.errors.append(
                        f"candidate-owned model/reset import is forbidden: mujoco.{alias.name}"
                    )
                if alias.name in _FORBIDDEN_CONSTRUCTORS:
                    self.errors.append(
                        f"candidate-owned MuJoCo construction import is forbidden: mujoco.{alias.name}"
                    )
                if alias.name == "mj_step":
                    self._physics_step_aliases.add(local_name)
                if alias.name == "*":
                    self.errors.append("wildcard import from mujoco is forbidden")
        elif root == "mujoco":
            self.errors.append(f"internal MuJoCo module import is forbidden: {module}")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_names.append(node.name)
        self.function_nodes.setdefault(node.name, []).append(node)
        if self._scope_depth == 0:
            self.top_level_functions.add(node.name)
        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _FORBIDDEN_CALL_NAMES:
                self.errors.append(f"dynamic execution/binding is forbidden: {name}")
            if name in _FORBIDDEN_CONSTRUCTORS:
                self.errors.append(f"candidate-owned MuJoCo construction is forbidden: {name}")
            if name in self._physics_step_aliases:
                self.physics_step_references += 1
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
            path = _attribute_path(node.func)
            if name in _FORBIDDEN_CALL_ATTRIBUTES:
                self.errors.append(f"candidate-owned model/reset call is forbidden: {path}")
            if name in _FORBIDDEN_CONSTRUCTORS:
                self.errors.append(f"candidate-owned MuJoCo construction is forbidden: {path}")
            if path.endswith("types.MethodType") or name == "MethodType":
                self.errors.append("dynamic MethodType binding is forbidden")
            if name in _MUTATING_METHODS and _contains_forbidden_state_attribute(node.func.value):
                self.errors.append(f"direct state mutation is forbidden: {path}")
            root = path.split(".", 1)[0]
            if name == "mj_step" and root in self._mujoco_module_aliases:
                self.physics_step_references += 1
            if "._functions." in f".{path}." or path.startswith("mujoco._functions"):
                self.errors.append("internal MuJoCo function access is forbidden")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"copyto", "place", "put"}
            and any(_contains_forbidden_state_attribute(argument) for argument in node.args)
        ):
            self.errors.append("array helper may not mutate trusted state")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        path = _attribute_path(node)
        if "._functions." in f".{path}." or path.startswith("mujoco._functions"):
            self.errors.append("internal MuJoCo function access is forbidden")
        if node.attr == "ctrl":
            self.ctrl_references += 1
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if _writes_model_state(target):
                self.errors.append(
                    f"direct model write is forbidden: {_target_path(target)}"
                )
            for attribute in _target_attributes(target):
                if attribute in _FORBIDDEN_STATE_ATTRIBUTES:
                    self.errors.append(f"direct state write is forbidden: {attribute}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _writes_model_state(node.target):
            self.errors.append(
                f"direct model write is forbidden: {_target_path(node.target)}"
            )
        for attribute in _target_attributes(node.target):
            if attribute in _FORBIDDEN_STATE_ATTRIBUTES:
                self.errors.append(f"direct state write is forbidden: {attribute}")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _writes_model_state(node.target):
            self.errors.append(
                f"direct model write is forbidden: {_target_path(node.target)}"
            )
        for attribute in _target_attributes(node.target):
            if attribute in _FORBIDDEN_STATE_ATTRIBUTES:
                self.errors.append(f"direct state write is forbidden: {attribute}")
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if _writes_model_state(target):
                self.errors.append(
                    f"direct model write is forbidden: {_target_path(target)}"
                )
            for attribute in _target_attributes(target):
                if attribute in _FORBIDDEN_STATE_ATTRIBUTES:
                    self.errors.append(f"direct state write is forbidden: {attribute}")
        self.generic_visit(node)


def audit_driver_source(
    source: str,
    *,
    condition: GenerationCondition,
    capability_methods: tuple[str, ...] | list[str],
) -> DriverSourceAudit:
    """Audit the submitted source, not its trusted dependencies or the repository."""

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise DriverSourceError(f"unknown generation condition {condition!r}")
    try:
        required_methods = validate_capability_names(capability_methods)
    except SkeletonContractError as exc:
        raise DriverSourceError(str(exc)) from exc
    try:
        tree = ast.parse(source, filename="driver.py")
    except SyntaxError as exc:
        raise DriverSourceError(f"driver.py is not valid Python: {exc.msg}") from exc

    visitor = _AuditVisitor()
    visitor.visit(tree)
    if "build" not in visitor.top_level_functions:
        visitor.errors.append("driver.py must define top-level build()")
    missing = sorted(set(required_methods) - set(visitor.function_names))
    if missing:
        visitor.errors.append(f"driver.py does not explicitly define capability methods {missing}")
    for method_name in required_methods:
        for function in visitor.function_nodes.get(method_name, ()):
            if not _has_request_abi(function):
                visitor.errors.append(
                    f"capability method {method_name!r} must explicitly accept keyword request"
                )
    if condition == "skeleton-assisted" and not visitor.imports_trusted_skeleton:
        visitor.errors.append("skeleton-assisted driver must import the trusted skeleton family")
    if condition == "from-scratch" and visitor.imports_trusted_skeleton:
        visitor.errors.append("from-scratch driver must not import the trusted skeleton family")
    if condition == "from-scratch" and visitor.ctrl_references == 0:
        visitor.errors.append("from-scratch driver.py contains no actuator ctrl path")
    if condition == "from-scratch" and visitor.physics_step_references == 0:
        visitor.errors.append("from-scratch driver.py contains no physics-step path")
    if visitor.errors:
        raise DriverSourceError("; ".join(visitor.errors))
    return DriverSourceAudit(
        condition=condition,
        capability_methods=required_methods,
        imports_trusted_skeleton=visitor.imports_trusted_skeleton,
        ctrl_references=visitor.ctrl_references,
        physics_step_references=visitor.physics_step_references,
    )
