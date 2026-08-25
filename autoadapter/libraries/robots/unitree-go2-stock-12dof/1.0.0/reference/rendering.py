"""Package-local Go2 reference rendering and request-ABI adapter."""

from __future__ import annotations

import json
import keyword
from pathlib import Path
from typing import Any, Mapping

DEFAULT_RENDER = {
    "width": 800,
    "height": 600,
    "fps": 20.0,
    "camera": -1,
}


def render_settings(*, camera: int | str = -1, fps: float = 20.0) -> dict[str, float | int | str]:
    """Return stable worker render settings without loading or mutating a model."""

    settings = dict(DEFAULT_RENDER)
    settings["camera"] = camera
    settings["fps"] = float(fps)
    return settings


def _schema_signature(value: Any) -> Any:
    """Retain the physical schema while ignoring source-description prose."""

    if isinstance(value, Mapping):
        return {
            str(key): _schema_signature(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if key not in {"description", "evidence_refs"}
        }
    if isinstance(value, list):
        return [_schema_signature(child) for child in value]
    return value


def _public_reference_path() -> Path:
    return Path(__file__).resolve().parents[5] / "references" / "capability_v2" / "go2.json"


def _native_profiles() -> dict[tuple[str, str, str], tuple[str, str]]:
    document = json.loads(_public_reference_path().read_text(encoding="utf-8"))
    profiles: dict[tuple[str, str, str], tuple[str, str]] = {}
    for capability in document.get("capabilities", []):
        criteria = capability.get("criteria")
        schema = capability.get("request_schema")
        if (
            not isinstance(criteria, list)
            or len(criteria) != 1
            or not isinstance(criteria[0], Mapping)
            or not isinstance(schema, Mapping)
        ):
            raise ValueError("Go2 public reference profile is incomplete")
        criterion = criteria[0]
        key = (
            str(criterion.get("metric")),
            str(criterion.get("unit")),
            json.dumps(_schema_signature(schema), sort_keys=True, separators=(",", ":")),
        )
        if key in profiles:
            raise ValueError("Go2 public reference profiles are ambiguous")
        profiles[key] = (
            str(capability["capability_id"]),
            str(capability["method_name"]),
        )
    if set(profile for profile, _method in profiles.values()) != {
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
    }:
        raise ValueError("Go2 public reference must define profiles G1-G5")
    return profiles


def _render_native(
    capabilities: list[Mapping[str, Any]], destination: Path
) -> Path:
    profiles = _native_profiles()
    if not 3 <= len(capabilities) <= len(profiles):
        raise ValueError("native Go2 reference requires three to five calibrated profiles")
    matches: list[tuple[str, str, str]] = []
    seen_profiles: set[str] = set()
    for capability in capabilities:
        criteria = capability.get("criteria")
        schema = capability.get("request_schema")
        if (
            not isinstance(criteria, list)
            or len(criteria) != 1
            or not isinstance(criteria[0], Mapping)
            or not isinstance(schema, Mapping)
        ):
            raise ValueError("native Go2 capability is missing criterion or request schema")
        criterion = criteria[0]
        key = (
            str(criterion.get("metric")),
            str(criterion.get("unit")),
            json.dumps(_schema_signature(schema), sort_keys=True, separators=(",", ":")),
        )
        profile = profiles.get(key)
        if profile is None:
            raise ValueError(
                "native Go2 capability does not match a calibrated metric/unit/schema profile"
            )
        profile_id, fixed_method = profile
        if profile_id in seen_profiles:
            raise ValueError(f"ambiguous duplicate Go2 calibration profile {profile_id}")
        seen_profiles.add(profile_id)
        matches.append((str(capability["method_name"]), profile_id, fixed_method))

    base_source = Path(__file__).with_name("fixed_capability_driver.py").read_text(
        encoding="utf-8"
    )
    wrappers = ["", "", "class RenderedGo2CapabilityDriver(Driver):"]
    for method_name, profile_id, fixed_method in matches:
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
            "def build(*, model: Any, data: Any) -> RenderedGo2CapabilityDriver:",
            "    return RenderedGo2CapabilityDriver(model=model, data=data)",
        ]
    )
    output = destination / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output


def render_reference_driver(design: Mapping[str, Any], destination: str | Path) -> Path:
    """Render one unambiguous native capability-v2 or legacy task adapter.

    The calibration implementation remains package-owned, while the method
    names come only from the sealed design.  Every generated wrapper accepts
    ``request`` and dispatches its task id to a fixed calibration primitive.
    This task dispatch is reference-only material and is never exposed to
    TGCD or included in the public Task Library.
    """

    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("reference design must contain capabilities")
    method_names: list[str] = []
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            raise ValueError("reference design capability must be an object")
        method_name = capability.get("method_name")
        if (
            not isinstance(method_name, str)
            or not method_name.isidentifier()
            or keyword.iskeyword(method_name)
            or method_name.startswith("_")
            or method_name in {"build", "model", "data", "step", "spec"}
        ):
            raise ValueError(f"invalid TGCD method name {method_name!r}")
        if method_name in method_names:
            raise ValueError(f"duplicate TGCD method name {method_name!r}")
        method_names.append(method_name)

    protocol_fields = [
        ("request_schema" in capability, "criteria" in capability)
        for capability in capabilities
    ]
    if all(has_schema and has_criteria for has_schema, has_criteria in protocol_fields):
        return _render_native(capabilities, Path(destination).resolve())
    if not all(
        not has_schema and not has_criteria
        for has_schema, has_criteria in protocol_fields
    ):
        raise ValueError(
            "mixed or incomplete Go2 reference design cannot select a safe renderer"
        )

    base_source = Path(__file__).with_name("driver.py").read_text(encoding="utf-8")
    wrappers = [
        "",
        "\n\ndef _rendered_task_id(request: Mapping[str, Any]) -> str:",
        "    if not isinstance(request, Mapping):",
        "        raise TypeError(\"request must be an object\")",
        "    task_id = request.get(\"task_id\")",
        "    if not isinstance(task_id, str) or not task_id.strip():",
        "        raise ValueError(\"request.task_id must be a non-empty string\")",
        "    return task_id",
        "",
        "\n\nclass RenderedGo2Driver(ReferenceGo2Driver):",
        "    def _rendered_request(self, request: Mapping[str, Any]) -> None:",
        "        task_id = _rendered_task_id(request)",
        "        if task_id == \"GO2-T01\":",
        "            ReferenceGo2Driver.walk_forward(self, request)",
        "        elif task_id in {\"GO2-T02\", \"GO2-T03\", \"GO2-T04\"}:",
        "            ReferenceGo2Driver.traverse_step(self, request)",
        "        elif task_id == \"GO2-T05\":",
        "            ReferenceGo2Driver.turn_in_place(self, request)",
        "        elif task_id == \"GO2-T06\":",
        "            ReferenceGo2Driver.follow_path(self, request)",
        "        elif task_id in {\"GO2-T07\", \"GO2-T08\", \"GO2-T09\", \"GO2-T10\", \"GO2-T11\"}:",
        "            ReferenceGo2Driver.traverse_rough_rigid(self, request)",
        "        elif task_id == \"GO2-T12\":",
        "            ReferenceGo2Driver.traverse_blocks(self, request)",
        "        elif task_id == \"GO2-T13\":",
        "            ReferenceGo2Driver.traverse_stairs(self, request)",
        "        elif task_id == \"GO2-T14\":",
        "            ReferenceGo2Driver.traverse_stepping_stones(self, request)",
        "        elif task_id == \"GO2-T15\":",
        "            ReferenceGo2Driver.traverse_poles(self, request)",
        "        elif task_id == \"GO2-T16\":",
        "            ReferenceGo2Driver.traverse_step(self, request)",
        "        elif task_id == \"GO2-T17\":",
        "            ReferenceGo2Driver.follow_path(self, request)",
        "        elif task_id == \"GO2-T18\":",
        "            ReferenceGo2Driver.traverse_ramp(self, request)",
        "        elif task_id == \"GO2-T19\":",
        "            ReferenceGo2Driver.traverse_step(self, request)",
        "        elif task_id == \"GO2-T20\":",
        "            ReferenceGo2Driver.follow_path(self, request)",
        "        else:",
        "            raise ValueError(f\"unsupported Go2 calibration task {task_id!r}\")",
    ]
    for method_name in method_names:
        wrappers.extend(
            [
                "",
                f"    def {method_name}(self, request: Mapping[str, Any]) -> None:",
                "        \"\"\"TGCD-authored method using the package request envelope.\"\"\"",
                "        self._rendered_request(request)",
            ]
        )
    wrappers.extend(
        [
            "",
            "",
            "def build(*, model: Any, data: Any) -> RenderedGo2Driver:",
            "    return RenderedGo2Driver(model=model, data=data)",
        ]
    )
    output = Path(destination).resolve() / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output
