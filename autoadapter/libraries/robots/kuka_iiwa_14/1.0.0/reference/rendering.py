"""Package-local KUKA iiwa reference rendering for arbitrary TGCD methods."""

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
        "    return _task_id(request)",
        "",
        "\n\nclass RenderedKukaIiwa14Driver(ReferenceKukaIiwa14Driver):",
        "    def _rendered_request(self, request: Mapping[str, Any]) -> None:",
        "        task_id = _rendered_task_id(request)",
        "        if task_id == \"mw_reach_target\":",
        "            ReferenceKukaIiwa14Driver.reach_task(self, request=request)",
        "        elif task_id in {\"mw_push_to_goal\", \"mw_push_wall\", \"mw_sweep_into_goal\", \"mw_soccer\"}:",
        "            ReferenceKukaIiwa14Driver.contact_task(self, request=request)",
        "        elif task_id in {\"mw_drawer_open\", \"mw_drawer_close\", \"mw_button_press\", \"mw_button_press_topdown\", \"mw_handle_press\", \"mw_door_open\", \"mw_door_close\", \"mw_door_lock\", \"mw_door_unlock\", \"mw_window_open\", \"mw_window_close\"}:",
        "            ReferenceKukaIiwa14Driver.fixture_task(self, request=request)",
        "        elif task_id in {\"mw_faucet_open\", \"mw_dial_turn\", \"mw_lever_pull\", \"mw_faucet_close\"}:",
        "            ReferenceKukaIiwa14Driver.rotation_task(self, request=request)",
        "        else:",
        "            raise ValueError(f\"unsupported KUKA iiwa calibration task {task_id!r}\")",
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
            "def build(*, model: Any, data: Any) -> RenderedKukaIiwa14Driver:",
            "    return RenderedKukaIiwa14Driver(model=model, data=data)",
        ]
    )
    output = Path(destination).resolve() / "driver.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(base_source + "\n".join(wrappers) + "\n", encoding="utf-8")
    return output
