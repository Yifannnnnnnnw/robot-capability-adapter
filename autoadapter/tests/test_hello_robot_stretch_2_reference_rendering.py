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
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "hello_robot_stretch_2" / "1.0.0"
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
    "mw_push_wall": "contact_task",
    "mw_sweep_into_goal": "contact_task",
    "mw_pick_place": "object_task",
    "mw_pick_place_wall": "object_task",
    "mw_peg_insertion_side": "object_task",
    "mw_bin_picking": "object_task",
    "mw_pick_out_of_hole": "object_task",
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
}


def _design() -> dict[str, Any]:
    return {
        "capabilities": [
            {"capability_id": f"cap-{index}", "method_name": method_name}
            for index, method_name in enumerate(METHOD_NAMES)
        ]
    }


def _package() -> SimpleNamespace:
    return SimpleNamespace(
        root=PACKAGE_ROOT,
        reference_driver=PACKAGE_ROOT / "reference" / "driver.py",
        robot_configuration_id="hello_robot_stretch_2",
    )


def _load_driver(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_rendering_audits_and_routes_every_catalog_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package()
    base_source = package.reference_driver.read_text(encoding="utf-8")
    catalog = json.loads(
        (PACKAGE_ROOT / "tasks" / "catalog.json").read_text(encoding="utf-8")
    )

    driver_path = render_reference_driver(package, _design(), tmp_path / "stretch")
    rendered_source = driver_path.read_text(encoding="utf-8")
    assert rendered_source[: len(base_source)] == base_source
    assert package.reference_driver.read_text(encoding="utf-8") == base_source

    audit = audit_driver_source(
        rendered_source,
        condition="skeleton-assisted",
        capability_methods=METHOD_NAMES,
    )
    assert audit.ctrl_references > 0
    assert audit.physics_step_references > 0
    assert audit.imports_trusted_skeleton

    monkeypatch.setitem(sys.modules, "mujoco", ModuleType("mujoco"))
    module = _load_driver(driver_path, "rendered_reference_stretch_2")
    reference_class = module.ReferenceStretch2Driver
    rendered_class = module.RenderedStretch2Driver
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
    task_ids = [task["task_id"] for task in catalog["tasks"]]
    assert len(task_ids) == 20
    assert len(set(task_ids)) == 20
    assert set(task_ids) == set(TASK_TO_REFERENCE)
    for task_id in task_ids:
        getattr(driver, METHOD_NAMES[0])(
            request={"task_id": task_id, "task_parameters": {}}
        )
    assert calls == [TASK_TO_REFERENCE[task_id] for task_id in task_ids]

    with pytest.raises(ValueError, match="unsupported Stretch calibration task"):
        getattr(driver, METHOD_NAMES[0])(
            request={"task_id": "unknown_task", "task_parameters": {}}
        )


def test_reference_rendering_rejects_invalid_and_duplicate_method_names(
    tmp_path: Path,
) -> None:
    package = _package()

    with pytest.raises(ValueError, match="reference design must contain capabilities"):
        render_reference_driver(package, {"capabilities": []}, tmp_path / "empty")

    with pytest.raises(ValueError, match="invalid TGCD method name"):
        render_reference_driver(
            package,
            {"capabilities": [{"method_name": "build"}]},
            tmp_path / "reserved",
        )

    with pytest.raises(ValueError, match="invalid TGCD method name"):
        render_reference_driver(
            package,
            {"capabilities": [{"method_name": "_private"}]},
            tmp_path / "private",
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
            tmp_path / "duplicate",
        )
