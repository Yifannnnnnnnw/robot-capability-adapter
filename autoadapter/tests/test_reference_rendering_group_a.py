from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from autoadapter2.driver_synthesis import audit_driver_source
from autoadapter2.pipeline import render_reference_driver


ROOT = Path(__file__).resolve().parents[1]
METHOD_NAMES = (
    "tgcd_reach_alpha",
    "tgcd_contact_beta",
    "tgcd_object_gamma",
    "tgcd_fixture_delta",
    "tgcd_rotation_epsilon",
)
TASK_TO_REFERENCE = {
    "mw_reach_target": "reach_task",
    "mw_push_to_goal": "contact_task",
    "mw_pick_place": "object_task",
    "mw_pick_place_wall": "object_task",
    "mw_push_wall": "contact_task",
    "mw_sweep_into_goal": "contact_task",
    "mw_drawer_open": "fixture_task",
    "mw_drawer_close": "fixture_task",
    "mw_button_press": "fixture_task",
    "mw_button_press_topdown": "fixture_task",
    "mw_handle_press": "fixture_task",
    "mw_handle_pull": "fixture_task",
    "mw_door_open": "fixture_task",
    "mw_door_close": "fixture_task",
    "mw_faucet_open": "rotation_task",
    "mw_dial_turn": "rotation_task",
    "mw_lever_pull": "rotation_task",
    "mw_peg_insertion_side": "object_task",
    "mw_bin_picking": "object_task",
    "mw_pick_out_of_hole": "object_task",
}
PACKAGE_CASES = (
    pytest.param(
        "franka_panda",
        "ReferenceFrankaPandaDriver",
        "RenderedFrankaPandaDriver",
        id="franka-panda",
    ),
    pytest.param(
        "ufactory_xarm7",
        "ReferenceUfactoryXarm7Driver",
        "RenderedUfactoryXarm7Driver",
        id="ufactory-xarm7",
    ),
    pytest.param("piper", "ReferencePiperDriver", "RenderedPiperDriver", id="piper"),
)


def _design() -> dict[str, Any]:
    return {
        "capabilities": [
            {"capability_id": f"cap-{index}", "method_name": method_name}
            for index, method_name in enumerate(METHOD_NAMES)
        ]
    }


def _load_driver(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "robot_id,reference_class_name,rendered_class_name", PACKAGE_CASES
)
def test_reference_rendering_audits_and_routes_every_catalog_task(
    robot_id: str,
    reference_class_name: str,
    rendered_class_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = ROOT / "libraries" / "robots" / robot_id / "1.0.0"
    package = SimpleNamespace(
        root=package_root,
        reference_driver=package_root / "reference" / "driver.py",
        robot_configuration_id=robot_id,
    )
    design = _design()
    base_source = package.reference_driver.read_text(encoding="utf-8")
    catalog_path = package_root / "tasks" / "catalog.json"
    catalog_before = json.loads(catalog_path.read_text(encoding="utf-8"))

    driver_path = render_reference_driver(package, design, tmp_path / robot_id)
    rendered_source = driver_path.read_text(encoding="utf-8")
    assert rendered_source[: len(base_source)] == base_source
    assert package.reference_driver.read_text(encoding="utf-8") == base_source
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == catalog_before

    audit = audit_driver_source(
        rendered_source,
        condition="from-scratch",
        capability_methods=METHOD_NAMES,
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert not audit.imports_trusted_skeleton

    monkeypatch.setitem(sys.modules, "mujoco", ModuleType("mujoco"))
    numpy_stub = ModuleType("numpy")
    numpy_stub.asarray = lambda value, *args, **kwargs: value  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "numpy", numpy_stub)
    module = _load_driver(driver_path, f"rendered_reference_{robot_id}")
    reference_class = getattr(module, reference_class_name)
    rendered_class = getattr(module, rendered_class_name)
    assert all(callable(rendered_class.__dict__.get(name)) for name in METHOD_NAMES)

    calls: list[str] = []
    for reference_method in set(TASK_TO_REFERENCE.values()):
        def record(
            self: Any,
            request: Any,
            *,
            _reference_method: str = reference_method,
        ) -> None:
            del self, request
            calls.append(_reference_method)

        monkeypatch.setattr(reference_class, reference_method, record)

    driver = rendered_class.__new__(rendered_class)
    for method_name in METHOD_NAMES:
        getattr(driver, method_name)(
            request={"task_id": "mw_reach_target", "task_parameters": {}}
        )
    assert calls == ["reach_task"] * len(METHOD_NAMES)

    calls.clear()
    task_ids = [task["task_id"] for task in catalog_before["tasks"]]
    assert len(task_ids) == 20
    assert set(task_ids) == set(TASK_TO_REFERENCE)
    for task_id in task_ids:
        getattr(driver, METHOD_NAMES[0])(
            request={"task_id": task_id, "task_parameters": {}}
        )
    assert calls == [TASK_TO_REFERENCE[task_id] for task_id in task_ids]


@pytest.mark.parametrize(
    "robot_id,_,__", PACKAGE_CASES
)
def test_reference_rendering_rejects_invalid_and_duplicate_method_names(
    robot_id: str,
    _: str,
    __: str,
    tmp_path: Path,
) -> None:
    package_root = ROOT / "libraries" / "robots" / robot_id / "1.0.0"
    package = SimpleNamespace(
        root=package_root,
        reference_driver=package_root / "reference" / "driver.py",
        robot_configuration_id=robot_id,
    )

    with pytest.raises(ValueError, match="invalid TGCD method name"):
        render_reference_driver(
            package,
            {"capabilities": [{"method_name": "build"}]},
            tmp_path / f"{robot_id}-invalid",
        )

    with pytest.raises(ValueError, match="duplicate TGCD method name"):
        render_reference_driver(
            package,
            {
                "capabilities": [
                    {"method_name": "duplicate_name"},
                    {"method_name": "duplicate_name"},
                ]
            },
            tmp_path / f"{robot_id}-duplicate",
        )
