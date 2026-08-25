"""Package-local SO-101 reference rendering for sealed capability designs.

Capability-v2 designs containing three to six capabilities are matched to a
unique subset of the six calibrated SO-101 profiles by their closed native
request schema and their top-level criterion metric/unit.
The generated wrappers call the fixed capability reference directly; they do
not inspect task identifiers or choose behaviour from a request payload.

The older task-metric reference path remains available only for legacy designs
whose capability records contain neither a request schema nor criteria.  A
partly migrated or otherwise ambiguous design is rejected instead of silently
falling back to task dispatch.
"""

from __future__ import annotations

import json
import keyword
from pathlib import Path
from typing import Any, Mapping


_RESERVED_METHODS = {"build", "model", "data", "step", "spec"}


def _capabilities(design: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("reference design must contain capabilities")
    if not all(isinstance(item, Mapping) for item in capabilities):
        raise ValueError("reference design capability must be an object")
    return list(capabilities)


def _method_names(capabilities: list[Mapping[str, Any]]) -> list[str]:
    method_names: list[str] = []
    for capability in capabilities:
        method_name = capability.get("method_name")
        if (
            not isinstance(method_name, str)
            or not method_name.isidentifier()
            or keyword.iskeyword(method_name)
            or method_name.startswith("_")
            or method_name in _RESERVED_METHODS
        ):
            raise ValueError(f"invalid TGCD method name {method_name!r}")
        if method_name in method_names:
            raise ValueError(f"duplicate TGCD method name {method_name!r}")
        method_names.append(method_name)
    return method_names


def _public_reference_path() -> Path:
    # .../autoadapter/libraries/robots/<robot>/<version>/reference/rendering.py
    autoadapter_root = Path(__file__).resolve().parents[5]
    return autoadapter_root / "references" / "capability_v2" / "so101.json"


def _schema_signature(value: Any) -> Any:
    """Return the physical/schema part of a public request contract.

    Evidence prose may be reworded by TGCD. Closed fields, types, frames,
    units, cardinalities, and calibrated numeric bounds must remain identical.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _schema_signature(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if key not in {"description", "evidence_refs"}
        }
    if isinstance(value, list):
        return [_schema_signature(child) for child in value]
    return value


def _native_profiles() -> dict[tuple[str, str, str], tuple[str, str]]:
    document = json.loads(_public_reference_path().read_text(encoding="utf-8"))
    profiles: dict[tuple[str, str, str], tuple[str, str]] = {}
    for capability in document.get("capabilities", []):
        criteria = capability.get("criteria")
        if not isinstance(criteria, list) or len(criteria) != 1:
            raise ValueError("SO-101 public reference profile must have one criterion")
        criterion = criteria[0]
        schema = capability.get("request_schema")
        if not isinstance(criterion, Mapping) or not isinstance(schema, Mapping):
            raise ValueError("SO-101 public reference profile is incomplete")
        key = (
            str(criterion.get("metric")),
            str(criterion.get("unit")),
            json.dumps(_schema_signature(schema), sort_keys=True, separators=(",", ":")),
        )
        if key in profiles:
            raise ValueError("SO-101 public reference profiles are ambiguous")
        profiles[key] = (
            str(capability["capability_id"]),
            str(capability["method_name"]),
        )
    if set(profile for profile, _ in profiles.values()) != {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
        "A6",
    }:
        raise ValueError("SO-101 public reference must define profiles A1-A6")
    return profiles


def _native_mapping(
    capabilities: list[Mapping[str, Any]],
) -> list[tuple[str, str, str]]:
    profiles = _native_profiles()
    if not 3 <= len(capabilities) <= len(profiles):
        raise ValueError(
            "native SO-101 reference requires three to six calibrated profiles"
        )
    matches: list[tuple[str, str, str]] = []
    seen_profiles: set[str] = set()
    for capability in capabilities:
        criteria = capability.get("criteria")
        schema = capability.get("request_schema")
        if not isinstance(criteria, list) or len(criteria) != 1:
            raise ValueError("native SO-101 capability must have one top-level criterion")
        criterion = criteria[0]
        if not isinstance(criterion, Mapping) or not isinstance(schema, Mapping):
            raise ValueError("native SO-101 capability is missing criterion or request schema")
        key = (
            str(criterion.get("metric")),
            str(criterion.get("unit")),
            json.dumps(_schema_signature(schema), sort_keys=True, separators=(",", ":")),
        )
        profile = profiles.get(key)
        if profile is None:
            raise ValueError(
                "native SO-101 capability does not match a calibrated metric/unit/schema profile"
            )
        profile_id, fixed_method = profile
        if profile_id in seen_profiles:
            raise ValueError(f"ambiguous duplicate SO-101 calibration profile {profile_id}")
        seen_profiles.add(profile_id)
        matches.append((str(capability["method_name"]), profile_id, fixed_method))
    return matches


def _render_native(
    capabilities: list[Mapping[str, Any]], destination: Path
) -> Path:
    mapping = _native_mapping(capabilities)
    base_source = Path(__file__).with_name("fixed_capability_driver.py").read_text(
        encoding="utf-8"
    )
    wrappers = ["", "", "class RenderedSO101CapabilityDriver(Driver):"]
    for method_name, profile_id, fixed_method in mapping:
        wrappers.extend(
            [
                "",
                f"    def {method_name}(self, request: Any) -> None:",
                f'        """Native wrapper for calibrated profile {profile_id}."""',
                f"        Driver.{fixed_method}(self, request=request)",
            ]
        )
    wrappers.extend(
        [
            "",
            "",
            "def build(*, model: Any, data: Any) -> RenderedSO101CapabilityDriver:",
            "    return RenderedSO101CapabilityDriver(model=model, data=data)",
        ]
    )
    output = destination / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output


def _render_task_metric(method_names: list[str], destination: Path) -> Path:
    """Render the retained legacy task-envelope positive control."""

    base_source = Path(__file__).with_name("driver.py").read_text(encoding="utf-8")
    wrappers = [
        "",
        "\n\ndef _rendered_task_id(request: Mapping[str, Any]) -> str:",
        "    task_id, _ = _request(request)",
        "    return task_id",
        "",
        "\n\nclass RenderedSO101Driver(ReferenceSO101Driver):",
        "    def _rendered_request(self, request: Mapping[str, Any]) -> None:",
        "        task_id = _rendered_task_id(request)",
        '        if task_id == "mw_reach_target":',
        "            ReferenceSO101Driver.reach_task(self, request=request)",
        '        elif task_id in {"mw_push_to_goal", "mw_push_wall", "mw_sweep_into_goal"}:',
        "            ReferenceSO101Driver.contact_task(self, request=request)",
        '        elif task_id in {"mw_pick_place", "mw_pick_place_wall", "mw_peg_insertion_side", "mw_bin_picking", "mw_pick_out_of_hole"}:',
        "            ReferenceSO101Driver.object_task(self, request=request)",
        '        elif task_id in {"mw_drawer_open", "mw_drawer_close", "mw_button_press", "mw_button_press_topdown", "mw_handle_press", "mw_handle_pull", "mw_door_open", "mw_door_close"}:',
        "            ReferenceSO101Driver.fixture_task(self, request=request)",
        '        elif task_id in {"mw_faucet_open", "mw_dial_turn", "mw_lever_pull"}:',
        "            ReferenceSO101Driver.rotation_task(self, request=request)",
        "        else:",
        '            raise ValueError(f"unsupported SO-101 calibration task {task_id!r}")',
    ]
    for method_name in method_names:
        wrappers.extend(
            [
                "",
                f"    def {method_name}(self, request: Mapping[str, Any]) -> None:",
                '        """Legacy task-metric method using the package task envelope."""',
                "        self._rendered_request(request)",
            ]
        )
    wrappers.extend(
        [
            "",
            "",
            "def build(*, model: Any, data: Any) -> RenderedSO101Driver:",
            "    return RenderedSO101Driver(model=model, data=data)",
        ]
    )
    output = destination / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output


def render_reference_driver(design: Mapping[str, Any], destination: str | Path) -> Path:
    """Render one unambiguous native capability-v2 or legacy task design."""

    capabilities = _capabilities(design)
    method_names = _method_names(capabilities)
    protocol_fields = [
        ("request_schema" in capability, "criteria" in capability)
        for capability in capabilities
    ]
    if all(has_schema and has_criteria for has_schema, has_criteria in protocol_fields):
        return _render_native(capabilities, Path(destination).resolve())
    if all(not has_schema and not has_criteria for has_schema, has_criteria in protocol_fields):
        return _render_task_metric(method_names, Path(destination).resolve())
    raise ValueError(
        "mixed or incomplete SO-101 reference design cannot select a safe renderer"
    )
