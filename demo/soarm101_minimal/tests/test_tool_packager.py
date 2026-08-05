from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from soarm_demo.tool_packager import (
    ToolPackagingError,
    annotation_to_json_schema,
    package_tree_sha256,
    package_validated_tools,
    unstructured_array_schema_paths,
)


def _write_package(root: Path) -> None:
    package = root / "generated_capability_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('generated __init__ must not execute')\n",
        encoding="utf-8",
    )
    (package / "_kinematics.py").write_text(
        "def _identity(value: float) -> float:\n    return value\n",
        encoding="utf-8",
    )
    functions = {
        "g1": ("command_joint", "position"),
        "g2": ("move_to_pose", "target"),
        "g3": ("pick_and_place", "target"),
    }
    for module, (function, parameter) in functions.items():
        support_import = (
            "from ._kinematics import _identity\n\n"
            if module in {"g2", "g3"}
            else ""
        )
        value_expression = (
            f"_identity({parameter})"
            if module in {"g2", "g3"}
            else parameter
        )
        (package / f"{module}.py").write_text(
            (
                support_import
                + f"def {function}(runtime: object, {parameter}: float) -> dict[str, object]:\n"
                f"    runtime['calls'].append(('{function}', {parameter}))\n"
                f"    return {{'status': 'ok', 'value': {value_expression}}}\n"
            ),
            encoding="utf-8",
        )


def test_packager_rejects_package_that_has_not_fully_passed_validation(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)

    with pytest.raises(ToolPackagingError, match="fully validated"):
        package_validated_tools(
            package_root=tmp_path,
            stage1=stage1_artifact,
            package_manifest=public_api_manifest,
            validation_passed=False,
            validated_package_sha256=package_tree_sha256(tmp_path),
            runtime={"calls": []},
        )


def test_packager_hides_runtime_emits_four_catalogs_and_binds_dispatcher(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)
    runtime: dict[str, Any] = {"calls": []}
    output = tmp_path / "tool_output"

    packaged = package_validated_tools(
        package_root=tmp_path,
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        validation_passed=True,
        validated_package_sha256=package_tree_sha256(tmp_path),
        runtime=runtime,
        output_dir=output,
    )

    assert set(packaged.catalogs) == {"G1", "G2", "G3", "combined"}
    assert [len(packaged.catalogs[layer]["tools"]) for layer in ("G1", "G2", "G3")] == [1, 1, 1]
    assert len(packaged.catalogs["combined"]["tools"]) == 3
    for catalog in packaged.catalogs.values():
        for tool in catalog["tools"]:
            parameters = tool["parameters"]
            assert "runtime" not in parameters["properties"]
            assert "runtime" not in parameters["required"]

    assert packaged.dispatcher.names == ("command_joint", "move_to_pose", "pick_and_place")
    result = packaged.dispatcher.call("command_joint", {"position": 0.25})
    assert result == {"status": "ok", "value": 0.25}
    assert runtime["calls"] == [("command_joint", 0.25)]

    with pytest.raises(ToolPackagingError, match="invalid arguments"):
        packaged.dispatcher.call(
            "command_joint",
            {"runtime": "model-must-not-inject-this", "position": 0.5},
        )

    react_tools = packaged.dispatcher.react_tools(packaged.catalogs["combined"])
    react_result = react_tools["move_to_pose"].handler({"target": 0.4})
    assert react_result == {"status": "ok", "value": 0.4}
    assert runtime["calls"][-1] == ("move_to_pose", 0.4)

    assert sorted(path.name for path in output.glob("*_catalog.json")) == [
        "combined_catalog.json",
        "g1_catalog.json",
        "g2_catalog.json",
        "g3_catalog.json",
    ]
    assert (output / "freeze.json").is_file()


def test_packager_relative_support_import_uses_clean_synthetic_namespace(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_package(tmp_path)
    user_package = types.ModuleType("generated_capability_package")
    user_support = types.ModuleType("generated_capability_package._kinematics")
    user_support._identity = lambda value: -999.0
    monkeypatch.setitem(sys.modules, "generated_capability_package", user_package)
    monkeypatch.setitem(
        sys.modules,
        "generated_capability_package._kinematics",
        user_support,
    )
    before = {name for name in sys.modules if name.startswith("_soarm_tool_")}

    packaged = package_validated_tools(
        package_root=tmp_path,
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        validation_passed=True,
        validated_package_sha256=package_tree_sha256(tmp_path),
        runtime={"calls": []},
    )

    assert packaged.dispatcher.call("move_to_pose", {"target": 0.4})["value"] == 0.4
    assert sys.modules["generated_capability_package"] is user_package
    assert sys.modules["generated_capability_package._kinematics"] is user_support
    assert {
        name for name in sys.modules if name.startswith("_soarm_tool_")
    } == before


def test_rebound_dispatcher_prepares_each_function_for_its_runtime(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)
    packaged = package_validated_tools(
        package_root=tmp_path,
        stage1=stage1_artifact,
        package_manifest=public_api_manifest,
        validation_passed=True,
        validated_package_sha256=package_tree_sha256(tmp_path),
        runtime={"calls": []},
    )
    prepared: list[tuple[str, dict[str, Any]]] = []

    def prepare(function: Any, runtime: dict[str, Any]) -> None:
        assert runtime["calls"] == []
        prepared.append((function.__name__, runtime))

    first_runtime: dict[str, Any] = {"calls": []}
    first = packaged.dispatcher.rebind(
        first_runtime,
        function_preparer=prepare,
    )
    assert first.call("command_joint", {"position": 0.25}) == {
        "status": "ok",
        "value": 0.25,
    }
    assert prepared == [("command_joint", first_runtime)]
    assert first_runtime["calls"] == [("command_joint", 0.25)]

    second_runtime: dict[str, Any] = {"calls": []}
    second = first.rebind(second_runtime)
    assert second.call("move_to_pose", {"target": 0.4}) == {
        "status": "ok",
        "value": 0.4,
    }
    assert prepared[-1] == ("move_to_pose", second_runtime)
    assert second_runtime["calls"] == [("move_to_pose", 0.4)]


def test_packager_rejects_bytes_not_bound_to_validation_attestation(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)

    with pytest.raises(ToolPackagingError, match="attestation"):
        package_validated_tools(
            package_root=tmp_path,
            stage1=stage1_artifact,
            package_manifest=public_api_manifest,
            validation_passed=True,
            validated_package_sha256="0" * 64,
            runtime={"calls": []},
        )


def _add_sequence_capability(
    root: Path,
    stage1: dict[str, Any],
    manifest: dict[str, Any],
    *,
    moves_annotation: str = "list[dict[str, object]]",
) -> None:
    package = root / "generated_capability_package"
    (package / "g3.py").write_text(
        (
            "from __future__ import annotations\n\n"
            "from ._kinematics import _identity\n\n"
            "def pick_and_place(runtime: object, target: float) -> dict[str, object]:\n"
            "    runtime['calls'].append(('pick_and_place', target))\n"
            "    return {'status': 'ok', 'value': _identity(target)}\n\n"
            f"def place_objects_sequence(runtime: object, moves: {moves_annotation}, "
            "timeout_s: float) -> dict[str, object]:\n"
            "    return {'status': 'ok', 'completed_moves': len(moves)}\n"
        ),
        encoding="utf-8",
    )
    stage1["layers"]["G3"]["capabilities"].append(
        {
            "capability_id": "G3.place_objects_sequence",
            "function_name": "place_objects_sequence",
            "intended_outcome": (
                "Execute ordered source-to-target object relocation records."
            ),
            "validation_effect": "object_move_sequence",
            "implementation_family": "ordered_pick_place_sequence",
            "implementation_evidence": {
                "status": "verified",
                "basis": "verified_physical_baseline",
                "refs": ["fixture://stage1"],
            },
        }
    )
    manifest["capabilities"].append(
        {
            "capability_id": "G3.place_objects_sequence",
            "granularity": "G3",
            "module": "g3",
            "function_name": "place_objects_sequence",
            "signature": {
                "parameters": [
                    {
                        "name": "runtime",
                        "annotation": "object",
                        "has_default": False,
                    },
                    {
                        "name": "moves",
                        "annotation": moves_annotation,
                        "has_default": False,
                    },
                    {
                        "name": "timeout_s",
                        "annotation": "float",
                        "has_default": False,
                    },
                ],
                "return_annotation": "dict[str, object]",
            },
            "validation_binding": {
                "effect": "object_move_sequence",
                "moves_argument": "moves",
                "source_field": "source_position_m",
                "target_field": "target_position_m",
                "object_extent_field": "object_extent_m",
                "object_extent_semantics": "maximum_horizontal_extent_m",
                "target_components": "xyz",
            },
            "result_contract": {
                "type": "object",
                "required": ["status"],
                "status_values": ["ok", "error"],
                "success_status_values": ["ok"],
            },
        }
    )


def test_stringized_nested_array_annotation_preserves_structured_items() -> None:
    schema = annotation_to_json_schema("list[dict[str, object]]")

    assert schema == {
        "type": "array",
        "items": {"type": "object", "additionalProperties": {}},
    }
    assert unstructured_array_schema_paths(schema) == []
    assert unstructured_array_schema_paths(
        annotation_to_json_schema("list")
    ) == ["$"]


def test_sequence_catalog_has_exact_nested_move_schema_and_rejects_flattening(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)
    stage1 = copy.deepcopy(stage1_artifact)
    manifest = copy.deepcopy(public_api_manifest)
    _add_sequence_capability(tmp_path, stage1, manifest)

    packaged = package_validated_tools(
        package_root=tmp_path,
        stage1=stage1,
        package_manifest=manifest,
        validation_passed=True,
        validated_package_sha256=package_tree_sha256(tmp_path),
        runtime={"calls": []},
    )
    tool = next(
        item
        for item in packaged.catalogs["combined"]["tools"]
        if item["name"] == "place_objects_sequence"
    )
    moves = tool["parameters"]["properties"]["moves"]
    item = moves["items"]

    assert moves["minItems"] == 1
    assert item["additionalProperties"] is False
    assert item["required"] == [
        "source_position_m",
        "target_position_m",
        "object_extent_m",
    ]
    assert item["properties"]["source_position_m"] == {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
    }
    assert item["properties"]["object_extent_m"]["exclusiveMinimum"] == 0.0
    assert "2*radius" in item["properties"]["object_extent_m"]["description"]

    valid_arguments = {
        "moves": [
            {
                "source_position_m": [0.3, -0.1, 0.04],
                "target_position_m": [0.4, -0.05, 0.06],
                "object_extent_m": 0.03,
            }
        ],
        "timeout_s": 28.0,
    }
    jsonschema.validate(valid_arguments, tool["parameters"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "moves": [
                    {
                        "source_x_m": 0.3,
                        "source_y_m": -0.1,
                        "source_z_m": 0.04,
                        "target_x_m": 0.4,
                        "target_y_m": -0.05,
                        "target_z_m": 0.06,
                        "object_extent_m": 0.03,
                    }
                ],
                "timeout_s": 28.0,
            },
            tool["parameters"],
        )

    catalog_schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas/tool_catalog.schema.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.validate(packaged.catalogs["combined"], catalog_schema)


def test_packager_rejects_untyped_public_array_before_catalog_exposure(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    public_api_manifest: dict[str, Any],
) -> None:
    _write_package(tmp_path)
    stage1 = copy.deepcopy(stage1_artifact)
    manifest = copy.deepcopy(public_api_manifest)
    _add_sequence_capability(
        tmp_path,
        stage1,
        manifest,
        moves_annotation="list",
    )

    with pytest.raises(ToolPackagingError, match="structured move objects"):
        package_validated_tools(
            package_root=tmp_path,
            stage1=stage1,
            package_manifest=manifest,
            validation_passed=True,
            validated_package_sha256=package_tree_sha256(tmp_path),
            runtime={"calls": []},
        )
