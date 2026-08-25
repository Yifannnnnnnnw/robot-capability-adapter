from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autoadapter2.capability_design.protocol import validate_schema_value
from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.pipeline import render_reference_driver
from autoadapter2.libraries import load_robot_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "robotstudio_so101" / "1.0.4"
PRIVATE_ROOT = PACKAGE_ROOT / "capability_validation" / "private"
PUBLIC_REFERENCE = ROOT / "references" / "capability_v2" / "so101.json"
FIXED_SUITE = (
    ROOT.parent
    / "experiment"
    / "experiment1a_generation"
    / "validation"
    / "fixed_validation_bundles"
    / "robotstudio_so101"
    / "capability_validation_suite.json"
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(map(_keys, value.values()))) if value else set()
    if isinstance(value, list):
        return set().union(*(map(_keys, value))) if value else set()
    return set()


def _native_design() -> dict[str, Any]:
    design = copy.deepcopy(_read(PUBLIC_REFERENCE))
    for index, capability in enumerate(design["capabilities"], start=1):
        capability["capability_id"] = f"model-capability-{index}"
        capability["method_name"] = f"model_native_method_{index}"
    return design


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("so101_native_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_context_keeps_h1_h3_as_evidence_anchors_not_cases() -> None:
    instances_document = _read(PRIVATE_ROOT / "instances.json")
    bindings_document = _read(PRIVATE_ROOT / "bindings.json")
    guards_document = _read(PRIVATE_ROOT / "guards.json")
    public = _read(PUBLIC_REFERENCE)
    suite = _read(FIXED_SUITE)

    instances = instances_document["instances"]
    bindings = bindings_document["bindings"]
    guards = guards_document["guards"]
    assert len(instances) == 12
    assert len(bindings) == 6
    assert len(guards) == 4
    assert {binding["parameters"]["contract_id"] for binding in bindings} == {
        "A1", "A2", "A3", "A4", "A5", "A6"
    }

    source_cases = {
        case["case_id"]: case
        for case in suite["cases"]
        if case["case_variant"] in {"H1", "H3"}
    }
    by_profile: dict[str, dict[str, dict[str, Any]]] = {}
    binding_by_id = {binding["binding_id"]: binding for binding in bindings}
    public_by_profile = {
        capability["capability_id"]: capability
        for capability in public["capabilities"]
    }
    expected_guards = {guard["guard_id"] for guard in guards}
    for instance in instances:
        profile = instance["calibration_profile"]
        role = instance["case_role"]
        by_profile.setdefault(profile, {})[role] = instance
        source = source_cases[instance["source_case_id"]]
        assert instance["scene_entrypoint"] == source["scene_entrypoint"]
        assert instance["reset"] == source["reset"]
        assert "public_arguments" not in instance
        assert len(instance["request_anchors"]) == 1
        anchor = instance["request_anchors"][0]
        assert anchor["anchor_id"] == instance["source_case_id"]
        assert anchor["request"] == source["request"]
        assert anchor["source_ref"].endswith(f"::{instance['source_case_id']}")
        assert instance["repetitions"] == source["repetitions"]
        assert instance["max_steps"] == source["max_steps"]
        assert instance["sample_hz"] == source["sample_hz"]
        assert instance["timeout_sim_s"] == source["timeout_sim_s"]
        assert instance["video_fps"] == 10.0
        assert instance["video_width"] == 800
        assert instance["video_height"] == 600
        assert instance["camera"] == -1
        assert set(instance["guard_ids"]) == expected_guards
        assert set(instance["clause_bindings"]) == {"criterion-0"}
        binding = binding_by_id[instance["clause_bindings"]["criterion-0"]]
        criterion = public_by_profile[profile]["criteria"][0]
        assert (binding["metric"], binding["unit"]) == (
            criterion["metric"], criterion["unit"]
        )
        assert binding["kind"] == "b1_contract"
        assert binding["parameters"]["contract_id"] == profile
        validate_schema_value(
            anchor["request"],
            public_by_profile[profile]["request_schema"],
        )

    assert set(by_profile) == {"A1", "A2", "A3", "A4", "A5", "A6"}
    for profile, roles in by_profile.items():
        assert set(roles) == {"nominal", "calibrated_boundary"}
        assert roles["nominal"]["instance_id"] != roles["calibrated_boundary"]["instance_id"]
        assert (
            roles["nominal"]["request_anchors"][0]["request"]
            != roles["calibrated_boundary"]["request_anchors"][0]["request"]
        ), profile

    request_keys = set().union(
        *(_keys(instance["request_anchors"][0]["request"]) for instance in instances)
    )
    assert request_keys.isdisjoint(
        {"task", "task_id", "task_parameters", "scene", "reset", "criteria"}
    )
    assert "task_id" not in _keys(guards_document)


def test_native_renderer_uses_arbitrary_names_and_native_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _native_design()
    methods = [capability["method_name"] for capability in design["capabilities"]]
    rendered = render_reference_driver(package, design, tmp_path / "native")
    source = rendered.read_text(encoding="utf-8")
    assert "task_id" not in source
    assert "task_parameters" not in source
    assert "_rendered_task_id" not in source
    assert "fixed_capability_driver" not in source
    audit_driver_source(
        source,
        condition="skeleton-assisted",
        capability_methods=tuple(methods),
    )

    module = _load(rendered)
    rendered_class = module.RenderedSO101CapabilityDriver
    calls: list[tuple[str, Any]] = []
    fixed_methods = [
        "move_end_effector_to_position",
        "trace_cartesian_path",
        "set_gripper_opening",
        "approach_until_contact",
        "move_cartesian_offset_and_return",
        "set_wrist_roll",
    ]
    for fixed_method in fixed_methods:
        def record(
            self: Any,
            request: Any,
            *,
            _fixed_method: str = fixed_method,
        ) -> None:
            del self
            calls.append((_fixed_method, request))

        monkeypatch.setattr(module.Driver, fixed_method, record)

    instance_by_profile = {
        instance["calibration_profile"]: instance
        for instance in _read(PRIVATE_ROOT / "instances.json")["instances"]
        if instance["case_role"] == "nominal"
    }
    driver = rendered_class.__new__(rendered_class)
    for index, method in enumerate(methods, start=1):
        request = instance_by_profile[f"A{index}"]["request_anchors"][0]["request"]
        getattr(driver, method)(request=request)
    assert [name for name, _ in calls] == fixed_methods
    assert calls[0][1] == {"target_position_m": [0.4, 0.1, 0.2], "max_duration_s": 4.0}


def test_renderer_keeps_explicit_task_metric_fallback_and_fails_closed(
    tmp_path: Path,
) -> None:
    package = load_robot_package(PACKAGE_ROOT)
    task_design = {"capabilities": [{"method_name": "legacy_task_metric"}]}
    task_source = render_reference_driver(
        package, task_design, tmp_path / "task-metric"
    ).read_text(encoding="utf-8")
    assert "_rendered_task_id" in task_source

    mixed = _native_design()
    mixed["capabilities"][0].pop("criteria")
    with pytest.raises(ValueError, match="mixed or incomplete"):
        render_reference_driver(package, mixed, tmp_path / "mixed")

    duplicate = _native_design()
    duplicate["capabilities"][-1]["criteria"] = copy.deepcopy(
        duplicate["capabilities"][0]["criteria"]
    )
    duplicate["capabilities"][-1]["request_schema"] = copy.deepcopy(
        duplicate["capabilities"][0]["request_schema"]
    )
    with pytest.raises(ValueError, match="ambiguous duplicate"):
        render_reference_driver(package, duplicate, tmp_path / "duplicate")

    unknown = _native_design()
    unknown["capabilities"][0]["criteria"][0]["metric"] = "unknown_metric"
    with pytest.raises(ValueError, match="does not match"):
        render_reference_driver(package, unknown, tmp_path / "unknown")

    too_small = _native_design()
    too_small["capabilities"] = too_small["capabilities"][:2]
    with pytest.raises(ValueError, match="three to six"):
        render_reference_driver(package, too_small, tmp_path / "too-small")


@pytest.mark.parametrize("profile_count", [3, 4, 5, 6])
def test_native_renderer_accepts_unique_known_profile_subsets(
    tmp_path: Path, profile_count: int
) -> None:
    package = load_robot_package(PACKAGE_ROOT)
    design = _native_design()
    design["capabilities"] = design["capabilities"][:profile_count]
    rendered = render_reference_driver(
        package, design, tmp_path / f"native-{profile_count}"
    )
    source = rendered.read_text(encoding="utf-8")
    assert "task_id" not in source
    for capability in design["capabilities"]:
        assert f"def {capability['method_name']}(" in source
