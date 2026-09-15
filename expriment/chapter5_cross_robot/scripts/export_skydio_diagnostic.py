#!/usr/bin/env python3
"""Package the generated Skydio X2 driver for assisted downstream diagnosis.

This is an experimenter-assisted export.  It preserves the original driver,
design, scene cases, validation report, and MJCF byte-for-byte, and supplies
only the five MCP forwarding wrappers required by the declared capability
design.  ``automatic_export_success`` is deliberately always false because
generation exhausted its 22-turn budget before automatic EXPORT; the wrappers
are supplied separately for downstream diagnosis.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AA1_ROOT = PROJECT_ROOT / "AA1"
sys.path.insert(0, str(AA1_ROOT))

from auto_adapter.export_support import validate_dynamic_export_source  # noqa: E402


MCP_SERVER = r'''# SPDX-License-Identifier: Apache-2.0
"""MCP forwarding surface for the assisted Skydio X2 diagnostic export.

The live robot is built once by the framework ``ExportRuntime``.  Tools only
forward validated request dictionaries to the generated driver; the state
resource is read-only.  Generation exhausted its turn budget before automatic
EXPORT, so this wrapper is supplied separately for diagnosis.
"""
from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

import driver_from_scratch
from auto_adapter.export_runtime import ExportRuntime


runtime = ExportRuntime(
    lambda: driver_from_scratch.Robot.build_from_mjcf("mjcf.xml")
)
mcp = FastMCP("skydio_x2", lifespan=runtime.lifespan)


@mcp.resource("robot://state")
def robot_state() -> dict:
    """Read-only observation snapshot of the live simulated aircraft."""

    return runtime.observe()


Position3D = Annotated[
    list[Annotated[float, Field(ge=-10.0, le=10.0)]],
    Field(
        min_length=3,
        max_length=3,
        json_schema_extra={"unit": "m", "frame": "world"},
    ),
]


def _payload(request: Any) -> Any:
    """Forward the validated request as a plain dictionary payload."""

    return dict(request) if isinstance(request, dict) else request


@with_config(ConfigDict(strict=True, extra="forbid"))
class HoldStationAtPointRequest(TypedDict):
    target_position_m: Position3D
    hold_duration_s: Annotated[
        float,
        Field(ge=0.5, le=60.0, json_schema_extra={"unit": "s"}),
    ]


@mcp.tool()
def hold_station_at_point(request: HoldStationAtPointRequest) -> Any:
    """Hold the aircraft body origin at a requested world point."""

    robot = runtime.robot
    return robot.hold_station_at_point(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class FlyBodyToPositionRequest(TypedDict):
    target_position_m: Position3D
    max_speed_mps: NotRequired[
        Annotated[
            float,
            Field(ge=0.05, le=5.0, json_schema_extra={"unit": "m/s"}),
        ]
    ]
    settle_time_s: NotRequired[
        Annotated[
            float,
            Field(ge=0.0, le=30.0, json_schema_extra={"unit": "s"}),
        ]
    ]


@mcp.tool()
def fly_body_to_position(request: FlyBodyToPositionRequest) -> Any:
    """Translate the aircraft body origin to a requested world position."""

    robot = runtime.robot
    return robot.fly_body_to_position(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class TraverseOrderedPositionsRequest(TypedDict):
    via_position_1_m: Position3D
    via_position_2_m: Position3D
    final_position_m: Position3D
    arrival_radius_m: NotRequired[
        Annotated[
            float,
            Field(ge=0.05, le=1.0, json_schema_extra={"unit": "m"}),
        ]
    ]


@mcp.tool()
def traverse_ordered_positions(request: TraverseOrderedPositionsRequest) -> Any:
    """Fly through three requested body-origin positions in order."""

    robot = runtime.robot
    return robot.traverse_ordered_positions(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class AlignBodyHeadingAtPointRequest(TypedDict):
    target_position_m: Position3D
    target_forward_reference_position_m: Position3D
    hold_duration_s: NotRequired[
        Annotated[
            float,
            Field(ge=0.0, le=60.0, json_schema_extra={"unit": "s"}),
        ]
    ]


@mcp.tool()
def align_body_heading_at_point(request: AlignBodyHeadingAtPointRequest) -> Any:
    """Place the body and its forward-reference site at requested points."""

    robot = runtime.robot
    return robot.align_body_heading_at_point(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class DescendAndSettleOnSupportRequest(TypedDict):
    target_touchdown_position_m: Position3D
    max_descent_speed_mps: NotRequired[
        Annotated[
            float,
            Field(ge=0.01, le=2.0, json_schema_extra={"unit": "m/s"}),
        ]
    ]
    settle_time_s: NotRequired[
        Annotated[
            float,
            Field(ge=0.0, le=30.0, json_schema_extra={"unit": "s"}),
        ]
    ]


@mcp.tool()
def descend_and_settle_on_support(
    request: DescendAndSettleOnSupportRequest,
) -> Any:
    """Descend to a requested world touchdown point and settle on support."""

    robot = runtime.robot
    return robot.descend_and_settle_on_support(request=_payload(request))


if __name__ == "__main__":
    mcp.run()
'''


COPY_FILES = (
    "driver_from_scratch.py",
    "validate_report.json",
    "mjcf.xml",
    "design/capability_design.json",
    "design/scene_cases.yaml",
)


def _copy_inputs(source: Path, output: Path) -> dict[str, bool]:
    """Copy the diagnostic inputs and report whether each copy is identical."""

    for relative in COPY_FILES:
        path = source / relative
        if not path.is_file():
            raise FileNotFoundError(path)

    (output / "design").mkdir()
    for relative in COPY_FILES:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_path = source / relative
        if relative == "mjcf.xml" and source_path.is_symlink():
            # The canonical X2 scene is a symlink whose XML includes x2.xml
            # relative to its real directory.  Preserve that link so the
            # scratch builder resolves the same native model after packaging.
            destination.symlink_to(source_path.readlink())
        else:
            shutil.copy2(source_path, destination, follow_symlinks=False)

    return {
        relative: (source / relative).read_bytes() == (output / relative).read_bytes()
        for relative in COPY_FILES
    }


def _validation_summary(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("validate_report.json must contain an object")
    return {
        key: value.get(key)
        for key in ("all_ok", "n_passed", "n_total", "error_stage")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.workspace.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=False)
    identical = _copy_inputs(source, output)
    design = json.loads(
        (output / "design/capability_design.json").read_text(encoding="utf-8")
    )
    (output / "mcp_server.py").write_text(MCP_SERVER, encoding="utf-8")

    wrapper_check: dict[str, Any]
    try:
        wrapper_check = validate_dynamic_export_source(
            output / "mcp_server.py", design
        )
        wrapper_check = {"ok": True, **wrapper_check}
    except Exception as exc:
        wrapper_check = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    record = {
        "source_workspace": str(source),
        "output_workspace": str(output),
        "assistance": (
            "Experimenter supplied the five MCP forwarding wrappers and the "
            "stdio entry point after generation exhausted its 22-turn budget; "
            "automatic EXPORT did not run."
        ),
        "automatic_export_success": False,
        "driver_design_validation_mjcf_bytes_unchanged": identical,
        "all_copied_inputs_unchanged": all(identical.values()),
        "mjcf_symlink_preserved": (source / "mjcf.xml").is_symlink()
        == (output / "mjcf.xml").is_symlink(),
        "validation_report_summary": _validation_summary(
            output / "validate_report.json"
        ),
        "wrapper_check": wrapper_check,
    }
    (output / "assisted_export.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if not wrapper_check.get("ok"):
        raise RuntimeError("local MCP export surface check failed")
    if not record["all_copied_inputs_unchanged"]:
        raise RuntimeError("copied diagnostic inputs differ from their source bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
