"""Package-local Go2 reference rendering and request-ABI adapter."""

from __future__ import annotations

import keyword
from pathlib import Path
from typing import Any, Mapping

DEFAULT_RENDER = {
    "width": 320,
    "height": 240,
    "fps": 20.0,
    "camera": -1,
}


def render_settings(*, camera: int | str = -1, fps: float = 20.0) -> dict[str, float | int | str]:
    """Return stable worker render settings without loading or mutating a model."""

    settings = dict(DEFAULT_RENDER)
    settings["camera"] = camera
    settings["fps"] = float(fps)
    return settings


def render_reference_driver(design: Mapping[str, Any], destination: str | Path) -> Path:
    """Render explicit arbitrary TGCD methods over the one public request ABI.

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
        "            ReferenceGo2Driver.stand(self, request)",
        "        elif task_id == \"GO2-T02\":",
        "            ReferenceGo2Driver.crouch(self, request)",
        "        elif task_id == \"GO2-T03\":",
        "            ReferenceGo2Driver.recover_stand(self, request)",
        "        elif task_id == \"GO2-T04\":",
        "            ReferenceGo2Driver.balance_hold(self, request)",
        "        elif task_id == \"GO2-T05\":",
        "            ReferenceGo2Driver.gait_cycle(self, request)",
        "        elif task_id == \"GO2-T06\":",
        "            ReferenceGo2Driver.walk_forward(self, request)",
        "        elif task_id == \"GO2-T07\":",
        "            ReferenceGo2Driver.walk_lateral(self, request)",
        "        elif task_id == \"GO2-T08\":",
        "            ReferenceGo2Driver.walk_diagonal(self, request)",
        "        elif task_id == \"GO2-T09\":",
        "            ReferenceGo2Driver.stop(self, request)",
        "        elif task_id == \"GO2-T10\":",
        "            ReferenceGo2Driver.turn_in_place(self, request)",
        "        elif task_id == \"GO2-T11\":",
        "            ReferenceGo2Driver.reach_waypoint(self, request)",
        "        elif task_id == \"GO2-T12\":",
        "            ReferenceGo2Driver.follow_path(self, request)",
        "        elif task_id == \"GO2-T13\":",
        "            ReferenceGo2Driver.traverse_ramp(self, request)",
        "        elif task_id in {\"GO2-T14\", \"GO2-T15\", \"GO2-T16\"}:",
        "            ReferenceGo2Driver.traverse_step(self, request)",
        "        elif task_id == \"GO2-T17\":",
        "            ReferenceGo2Driver.traverse_stairs(self, request)",
        "        elif task_id == \"GO2-T18\":",
        "            ReferenceGo2Driver.traverse_blocks(self, request)",
        "        elif task_id == \"GO2-T19\":",
        "            ReferenceGo2Driver.traverse_stepping_stones(self, request)",
        "        elif task_id == \"GO2-T20\":",
        "            ReferenceGo2Driver.traverse_poles(self, request)",
        "        elif task_id == \"GO2-T21\":",
        "            ReferenceGo2Driver.traverse_rough_rigid(self, request)",
        "        elif task_id == \"GO2-T22\":",
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
