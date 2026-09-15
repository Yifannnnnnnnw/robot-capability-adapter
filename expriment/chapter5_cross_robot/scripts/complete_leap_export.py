"""Complete this LEAP run's truncated MCP wrapper for downstream diagnosis.

This is experimenter-assisted packaging, not an automatic export success.
The original generation workspace is retained; driver and validation bytes
are copied unchanged. The three missing wrappers only forward driver calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run import AA1  # Makes the maintained package importable.
from auto_adapter.export_support import validate_dynamic_export_source


MISSING_WRAPPERS = '''

@with_config(ConfigDict(strict=True, extra="forbid"))
class TraceFingertipWaypointsRequest(TypedDict):
    finger: FingerName
    waypoint_1_position_m: Position3D
    waypoint_2_position_m: Position3D
    waypoint_3_position_m: Position3D
    segment_duration_s: NotRequired[Annotated[float, Field(
        ge=0.1, le=5.0, json_schema_extra={"unit": "s", "frame": "none"})]]


@mcp.tool()
def trace_fingertip_waypoints(request: TraceFingertipWaypointsRequest) -> Any:
    """Move one fingertip link through three world-frame waypoints in order."""
    robot = runtime.robot
    return robot.trace_fingertip_waypoints(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class ApplyFingertipContactForceRequest(TypedDict):
    finger: FingerName
    target_normal_force_N: Annotated[float, Field(
        ge=0.5, le=15.0, json_schema_extra={"unit": "N", "frame": "contact_normal"})]
    hold_duration_s: Annotated[float, Field(
        ge=0.2, le=10.0, json_schema_extra={"unit": "s", "frame": "none"})]
    max_flexion_rad: NotRequired[Annotated[float, Field(
        ge=0.0, le=2.0, json_schema_extra={"unit": "rad", "frame": "joint"})]]


@mcp.tool()
def apply_fingertip_contact_force(request: ApplyFingertipContactForceRequest) -> Any:
    """Flex one finger against contact until a bounded force is produced, then hold."""
    robot = runtime.robot
    return robot.apply_fingertip_contact_force(request=_payload(request))


@with_config(ConfigDict(strict=True, extra="forbid"))
class HoldSynchronizedFingerFlexionRequest(TypedDict):
    target_flexion_rad: Annotated[float, Field(
        ge=-0.3, le=1.8, json_schema_extra={"unit": "rad", "frame": "joint"})]
    hold_duration_s: Annotated[float, Field(
        ge=0.2, le=10.0, json_schema_extra={"unit": "s", "frame": "none"})]
    approach_duration_s: NotRequired[Annotated[float, Field(
        ge=0.1, le=5.0, json_schema_extra={"unit": "s", "frame": "none"})]]


@mcp.tool()
def hold_synchronized_finger_flexion(request: HoldSynchronizedFingerFlexionRequest) -> Any:
    """Flex index, middle and ring joints to a common angle and hold that posture."""
    robot = runtime.robot
    return robot.hold_synchronized_finger_flexion(request=_payload(request))


if __name__ == "__main__":
    mcp.run()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.workspace.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ("driver.py", "validate_report.json", "mjcf.xml"):
        shutil.copy2(source / name, output / name)
    (output / "design").mkdir()
    for name in ("capability_design.json", "scene_cases.yaml"):
        shutil.copy2(source / "design" / name, output / "design" / name)
    shutil.copytree(source / "design/scenes", output / "design/scenes")
    incomplete = (source / "mcp_server.py").read_text()
    (output / "mcp_server.py").write_text(incomplete + MISSING_WRAPPERS)
    design = json.loads((output / "design/capability_design.json").read_text())
    check = validate_dynamic_export_source(output / "mcp_server.py", design)
    unchanged = all((source / name).read_bytes() == (output / name).read_bytes()
                    for name in ("driver.py", "validate_report.json",
                                 "design/capability_design.json", "design/scene_cases.yaml"))
    if not unchanged:
        raise RuntimeError("copied driver or validation inputs differ")
    record = {"source_workspace": str(source), "assistance":
              "Experimenter completed three missing MCP forwarding wrappers and the stdio entry point.",
              "driver_and_validation_unchanged": unchanged,
              "automatic_export_success": False, "wrapper_check": check}
    (output / "assisted_export.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
