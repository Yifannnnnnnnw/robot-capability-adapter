"""Package-local ALOHA 2 reference rendering for arbitrary TGCD methods."""

from __future__ import annotations

import keyword
from pathlib import Path
from typing import Any, Mapping


def render_reference_driver(design: Mapping[str, Any], destination: str | Path) -> Path:
    """Render model-authored method names over the fixed request envelope.

    The task-id dispatch is calibration-only. It keeps the positive-control
    controller independent of arbitrary TGCD method names and is not a public
    task-to-effect catalog.
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
        "    task_id, _ = _request(request)",
        "    return task_id",
        "",
        "\n\nclass RenderedAloha2Driver(ReferenceAloha2Driver):",
        "    def _rendered_request(self, request: Mapping[str, Any]) -> None:",
        "        task_id = _rendered_task_id(request)",
        "        if task_id == \"mw_reach_target\":",
        "            ReferenceAloha2Driver.reach_task(self, request=request)",
        "        elif task_id in {\"mw_push_to_goal\", \"mw_push_wall\", \"mw_sweep_into_goal\", \"mw_soccer\"}:",
        "            ReferenceAloha2Driver.contact_task(self, request=request)",
        "        elif task_id in {\"mw_pick_place\", \"mw_pick_place_wall\"}:",
        "            ReferenceAloha2Driver.object_task(self, request=request)",
        "        elif task_id in {\"mw_drawer_open\", \"mw_drawer_close\", \"mw_button_press\", \"mw_button_press_topdown\", \"mw_handle_press\", \"mw_handle_pull\", \"mw_door_open\", \"mw_door_close\", \"mw_window_open\", \"mw_window_close\"}:",
        "            ReferenceAloha2Driver.fixture_task(self, request=request)",
        "        elif task_id in {\"mw_faucet_open\", \"mw_dial_turn\", \"mw_lever_pull\"}:",
        "            ReferenceAloha2Driver.rotation_task(self, request=request)",
        "        else:",
        "            raise ValueError(f\"unsupported ALOHA 2 calibration task {task_id!r}\")",
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
            "def build(*, model: Any, data: Any) -> RenderedAloha2Driver:",
            "    return RenderedAloha2Driver(model=model, data=data)",
        ]
    )
    output = Path(destination).resolve() / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output
