"""Deterministic packaging of validated Python functions as Agent tools."""

from __future__ import annotations

import ast
import collections.abc
import inspect
import json
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Union, get_args, get_origin

from .audit import atomic_write_json, sha256_file, sha256_json
from .generated_loader import load_isolated_generated_module
from .react_agent import ToolSpec
from .schema_validation import validate_json_schema


class ToolPackagingError(RuntimeError):
    pass


def package_tree_sha256(package_root: str | Path) -> str:
    root = Path(package_root)
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
            records.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)})
    return sha256_json(records)


def _load_function(package_root: Path, module_name: str, function_name: str) -> Callable[..., Any]:
    path = package_root / "generated_capability_package" / f"{module_name}.py"
    try:
        module = load_isolated_generated_module(
            package_root,
            module_name,
            namespace_label="tool",
        )
    except BaseException as exc:
        raise ToolPackagingError(f"cannot import validated module {path}") from exc
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ToolPackagingError(f"validated binding {module_name}.{function_name} is not callable")
    return function


def _annotation_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        parent = _annotation_name(node.value)
        return f"{parent}.{node.attr.lower()}" if parent else node.attr.lower()
    return None


def _annotation_arguments(node: ast.Subscript) -> list[ast.AST]:
    value = node.slice
    return list(value.elts) if isinstance(value, ast.Tuple) else [value]


def _annotation_ast_to_schema(node: ast.AST) -> dict[str, Any]:
    """Translate the deliberately small public-annotation subset to JSON Schema.

    Generated modules commonly enable ``from __future__ import annotations``;
    in that case ``inspect`` returns strings.  Parsing those strings is required
    to preserve container item types in the downstream Agent catalog.
    """

    if isinstance(node, ast.Constant) and node.value is None:
        return {"type": "null"}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return {
            "anyOf": [
                _annotation_ast_to_schema(node.left),
                _annotation_ast_to_schema(node.right),
            ]
        }
    name = _annotation_name(node)
    if name is not None:
        short = name.rsplit(".", 1)[-1]
        if short in {"str", "string"}:
            return {"type": "string"}
        if short in {"int", "integer"}:
            return {"type": "integer"}
        if short in {"float", "number"}:
            return {"type": "number"}
        if short in {"bool", "boolean"}:
            return {"type": "boolean"}
        if short in {"none", "nonetype"}:
            return {"type": "null"}
        if short in {"object", "any"}:
            return {}
        if short in {"list", "sequence", "tuple", "set", "frozenset"}:
            # An unparameterized array remains visibly incomplete and is
            # rejected by the fail-closed catalog check below.
            return {"type": "array", "items": {}}
        if short in {"dict", "mapping"}:
            return {"type": "object", "additionalProperties": {}}
        return {}
    if not isinstance(node, ast.Subscript):
        return {}
    base = _annotation_name(node.value)
    short = "" if base is None else base.rsplit(".", 1)[-1]
    arguments = _annotation_arguments(node)
    if short in {"list", "sequence", "set", "frozenset"}:
        item = _annotation_ast_to_schema(arguments[0]) if arguments else {}
        return {"type": "array", "items": item}
    if short == "tuple":
        if not arguments:
            return {"type": "array", "items": {}}
        if len(arguments) == 2 and isinstance(arguments[1], ast.Constant) and arguments[1].value is Ellipsis:
            return {"type": "array", "items": _annotation_ast_to_schema(arguments[0])}
        item_schemas = [_annotation_ast_to_schema(argument) for argument in arguments]
        if item_schemas and all(schema == item_schemas[0] for schema in item_schemas):
            return {
                "type": "array",
                "items": item_schemas[0],
                "minItems": len(item_schemas),
                "maxItems": len(item_schemas),
            }
        return {
            "type": "array",
            "prefixItems": item_schemas,
            "items": False,
            "minItems": len(item_schemas),
            "maxItems": len(item_schemas),
        }
    if short in {"dict", "mapping"}:
        value = _annotation_ast_to_schema(arguments[1]) if len(arguments) > 1 else {}
        return {"type": "object", "additionalProperties": value}
    if short == "optional":
        value = _annotation_ast_to_schema(arguments[0]) if arguments else {}
        return {"anyOf": [value, {"type": "null"}]}
    if short == "union":
        return {"anyOf": [_annotation_ast_to_schema(item) for item in arguments]}
    return {}


def _string_annotation_schema(annotation: str) -> dict[str, Any]:
    try:
        expression = ast.parse(annotation.strip(), mode="eval").body
    except (SyntaxError, ValueError):
        return {}
    return _annotation_ast_to_schema(expression)


def annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is inspect.Signature.empty:
        return {}
    if isinstance(annotation, str):
        return _string_annotation_schema(annotation)
    if annotation is Any:
        return {}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is type(None):
        return {"type": "null"}
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {
        list,
        tuple,
        set,
        frozenset,
        collections.abc.Sequence,
    }:
        item = annotation_to_json_schema(arguments[0]) if arguments else {}
        return {"type": "array", "items": item}
    if origin in {dict, Mapping, collections.abc.Mapping}:
        value = annotation_to_json_schema(arguments[1]) if len(arguments) > 1 else {}
        return {"type": "object", "additionalProperties": value}
    if origin in {Union, types.UnionType}:
        return {"anyOf": [annotation_to_json_schema(item) for item in arguments]}
    return {}


def unstructured_array_schema_paths(
    schema: Mapping[str, Any],
    path: str = "$",
) -> list[str]:
    """Return every array JSON Schema whose item contract is absent or opaque."""

    paths: list[str] = []
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping) or not items:
            paths.append(path)
        else:
            paths.extend(unstructured_array_schema_paths(items, f"{path}.items"))
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, item in enumerate(prefix_items):
                if isinstance(item, Mapping):
                    paths.extend(
                        unstructured_array_schema_paths(
                            item,
                            f"{path}.prefixItems[{index}]",
                        )
                    )
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, item in properties.items():
            if isinstance(item, Mapping):
                paths.extend(
                    unstructured_array_schema_paths(
                        item,
                        f"{path}.properties.{name}",
                    )
                )
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping):
        paths.extend(
            unstructured_array_schema_paths(
                additional,
                f"{path}.additionalProperties",
            )
        )
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        for index, item in enumerate(alternatives):
            if isinstance(item, Mapping):
                paths.extend(
                    unstructured_array_schema_paths(
                        item,
                        f"{path}.anyOf[{index}]",
                    )
                )
    return paths


def _sequence_move_item_schema(binding: Mapping[str, Any]) -> dict[str, Any]:
    if binding.get("object_extent_semantics") != "maximum_horizontal_extent_m":
        raise ToolPackagingError(
            "object_move_sequence object_extent_m must use "
            "maximum_horizontal_extent_m semantics"
        )
    fields = {
        "source": binding.get("source_field"),
        "target": binding.get("target_field"),
        "extent": binding.get("object_extent_field"),
    }
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise ToolPackagingError(
            "object_move_sequence binding requires literal source, target, and extent fields"
        )
    if len(set(fields.values())) != 3:
        raise ToolPackagingError("object_move_sequence move-item fields must be distinct")
    coordinate = {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
    }
    source_field = str(fields["source"])
    target_field = str(fields["target"])
    extent_field = str(fields["extent"])
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            source_field: dict(coordinate),
            target_field: dict(coordinate),
            extent_field: {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "description": (
                    "Maximum full horizontal extent in the grasp plane; "
                    "for a box use max(size_x,size_y), and for a vertical "
                    "cylinder use 2*radius."
                ),
            },
        },
        "required": [source_field, target_field, extent_field],
    }


def _apply_validation_binding_schema(
    parameters: dict[str, Any],
    binding: Mapping[str, Any] | None,
) -> None:
    if not isinstance(binding, Mapping) or binding.get("effect") != "object_move_sequence":
        return
    moves_argument = binding.get("moves_argument")
    properties = parameters.get("properties")
    if not isinstance(moves_argument, str) or not isinstance(properties, dict):
        raise ToolPackagingError("object_move_sequence binding has no valid moves argument")
    declared = properties.get(moves_argument)
    if (
        not isinstance(declared, Mapping)
        or declared.get("type") != "array"
        or not isinstance(declared.get("items"), Mapping)
        or declared["items"].get("type") != "object"
    ):
        raise ToolPackagingError(
            f"object_move_sequence parameter {moves_argument!r} must be annotated "
            "as an array of structured move objects"
        )
    properties[moves_argument] = {
        "type": "array",
        "minItems": 1,
        "items": _sequence_move_item_schema(binding),
    }


def function_parameter_schema(
    function: Callable[..., Any],
    *,
    validation_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    if not parameters or parameters[0].name != "runtime":
        raise ToolPackagingError(f"{function.__name__} does not have injected runtime first")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters[1:]:
        if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD, parameter.POSITIONAL_ONLY}:
            raise ToolPackagingError(
                f"{function.__name__}.{parameter.name} uses an unsupported parameter kind"
            )
        schema = annotation_to_json_schema(parameter.annotation)
        if parameter.default is not inspect.Parameter.empty:
            if not _json_serializable(parameter.default):
                raise ToolPackagingError(
                    f"{function.__name__}.{parameter.name} has a non-JSON default"
                )
            schema = dict(schema)
            schema["default"] = parameter.default
        else:
            required.append(parameter.name)
        properties[parameter.name] = schema
    result = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }
    _apply_validation_binding_schema(result, validation_binding)
    missing_items = unstructured_array_schema_paths(result, "$.parameters")
    if missing_items:
        raise ToolPackagingError(
            "public array parameters require a structured items schema: "
            + ", ".join(missing_items)
        )
    return result


def _json_serializable(value: Any) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False


def _stage1_descriptions(stage1: Mapping[str, Any]) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for layer in stage1.get("layers", {}).values():
        for capability in layer.get("capabilities", []):
            descriptions[str(capability["capability_id"])] = str(capability["intended_outcome"])
    return descriptions


@dataclass(frozen=True)
class PackagedTools:
    package_sha256: str
    catalogs: Mapping[str, Mapping[str, Any]]
    dispatcher: "BoundToolDispatcher"


class BoundToolDispatcher:
    """Bind the hidden runtime argument to the validated callable set."""

    def __init__(
        self,
        runtime: Any,
        bindings: Mapping[str, Callable[..., Any]],
        function_preparer: Callable[[Callable[..., Any], Any], None] | None = None,
    ):
        if function_preparer is not None and not callable(function_preparer):
            raise TypeError("function_preparer must be callable or None")
        self.runtime = runtime
        self._bindings = dict(bindings)
        self._function_preparer = function_preparer

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def rebind(
        self,
        runtime: Any,
        *,
        function_preparer: Callable[[Callable[..., Any], Any], None] | None = None,
    ) -> "BoundToolDispatcher":
        """Create a per-world dispatcher without sharing runtime state."""

        selected_preparer = (
            self._function_preparer
            if function_preparer is None
            else function_preparer
        )
        return BoundToolDispatcher(runtime, self._bindings, selected_preparer)

    def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if name not in self._bindings:
            raise ToolPackagingError(f"tool is not in validated allowlist: {name!r}")
        if not isinstance(arguments, Mapping):
            raise ToolPackagingError("tool arguments must be an object")
        function = self._bindings[name]
        signature = inspect.signature(function)
        try:
            signature.bind(self.runtime, **dict(arguments))
        except TypeError as exc:
            raise ToolPackagingError(f"invalid arguments for {name}: {exc}") from exc
        if self._function_preparer is not None:
            self._function_preparer(function, self.runtime)
        value = function(self.runtime, **dict(arguments))
        if not _json_serializable(value):
            raise ToolPackagingError(f"validated tool {name} returned non-JSON data")
        return value

    def react_tools(self, catalog: Mapping[str, Any]) -> dict[str, ToolSpec]:
        result: dict[str, ToolSpec] = {}
        allowed = set(self._bindings)
        for item in catalog.get("tools", []):
            name = str(item["name"])
            if name not in allowed:
                raise ToolPackagingError(f"catalog contains unbound tool {name!r}")
            result[name] = ToolSpec(
                name=name,
                description=str(item["description"]),
                input_schema=dict(item["parameters"]),
                handler=lambda arguments, selected=name: self.call(selected, arguments),
            )
        return result


def package_validated_tools(
    *,
    package_root: str | Path,
    stage1: Mapping[str, Any],
    package_manifest: Mapping[str, Any],
    validation_passed: bool,
    validated_package_sha256: str,
    runtime: Any,
    output_dir: str | Path | None = None,
) -> PackagedTools:
    """Emit layer-specific and combined catalogs from a passing package."""

    if not validation_passed:
        raise ToolPackagingError("only a fully validated package may be exposed as tools")
    root = Path(package_root).resolve()
    package_digest = package_tree_sha256(root)
    if package_digest != validated_package_sha256:
        raise ToolPackagingError(
            "package bytes differ from the direct-validation attestation"
        )
    descriptions = _stage1_descriptions(stage1)
    bindings: dict[str, Callable[..., Any]] = {}
    by_layer: dict[str, list[dict[str, Any]]] = {"G1": [], "G2": [], "G3": []}
    for item in package_manifest.get("capabilities", []):
        capability_id = str(item["capability_id"])
        granularity = str(item["granularity"])
        module_name = str(item["module"])
        function_name = str(item["function_name"])
        if granularity not in by_layer:
            raise ToolPackagingError(f"invalid granularity {granularity!r}")
        if capability_id not in descriptions:
            raise ToolPackagingError(f"manifest capability absent from Stage 1: {capability_id}")
        if function_name in bindings:
            raise ToolPackagingError(f"duplicate tool name {function_name!r}")
        function = _load_function(root, module_name, function_name)
        binding = item.get("validation_binding")
        if not isinstance(binding, Mapping):
            raise ToolPackagingError(
                f"manifest capability {capability_id!r} has no structured validation binding"
            )
        parameters = function_parameter_schema(
            function,
            validation_binding=binding,
        )
        bindings[function_name] = function
        by_layer[granularity].append(
            {
                "name": function_name,
                "description": descriptions[capability_id],
                "parameters": parameters,
                "capability_id": capability_id,
                "binding": f"{module_name}.{function_name}",
            }
        )
    catalogs: dict[str, dict[str, Any]] = {}
    for layer in ("G1", "G2", "G3"):
        catalogs[layer] = {
            "schema_version": "robot_capability.tool_catalog.v1",
            "catalog_id": f"soarm101_{layer.lower()}_validated",
            "package_sha256": package_digest,
            "tools": sorted(by_layer[layer], key=lambda value: value["name"]),
        }
    catalogs["combined"] = {
        "schema_version": "robot_capability.tool_catalog.v1",
        "catalog_id": "soarm101_combined_validated",
        "package_sha256": package_digest,
        "tools": sorted(
            (item for layer in ("G1", "G2", "G3") for item in by_layer[layer]),
            key=lambda value: value["name"],
        ),
    }
    catalog_schema_path = (
        Path(__file__).resolve().parents[2] / "schemas" / "tool_catalog.schema.json"
    )
    try:
        catalog_schema = json.loads(catalog_schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolPackagingError(
            f"cannot load canonical tool catalog schema {catalog_schema_path}"
        ) from exc
    for catalog_name, catalog in catalogs.items():
        schema_issues = validate_json_schema(
            catalog,
            catalog_schema,
            instance_path=f"$.catalogs.{catalog_name}",
        )
        if schema_issues:
            rendered = "; ".join(
                f"{issue.path}: {issue.message}" for issue in schema_issues
            )
            raise ToolPackagingError(
                f"generated tool catalog violates the canonical schema: {rendered}"
            )
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for name, catalog in catalogs.items():
            atomic_write_json(destination / f"{name.lower()}_catalog.json", catalog)
        atomic_write_json(
            destination / "freeze.json",
            {
                "schema_version": "robot_capability.tool_freeze.v1",
                "package_sha256": package_digest,
                "catalog_hashes": {name: sha256_json(value) for name, value in catalogs.items()},
            },
        )
    return PackagedTools(package_digest, catalogs, BoundToolDispatcher(runtime, bindings))
