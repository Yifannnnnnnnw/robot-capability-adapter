# SPDX-License-Identifier: Apache-2.0
"""Layer 1 — natural-language task → skeleton method calls.

DESIGN.md three-layer architecture:
    L1 (this file) — TaskPlanner: takes a task description in English, plans
                     a sequence of robot actions, calls MCP-style tools on
                     the driver, observes intermediate state, loops until
                     the task is complete or fails.
    L2             — agent-generated mcp_server.py (driver's MCP wrapper)
    L3             — agent-generated driver.py (skeleton + Spec)

For local testing this module talks DIRECTLY to the skeleton (bypasses the
MCP transport). The tool surface is identical to what mcp_server.py exposes,
so swapping to a real MCP client later is a one-handler change.

Per-task lifecycle:
    1. Load the workspace's driver.py + build a fresh skeleton instance.
    2. Wrap every public skeleton method as a ToolSpec.
    3. Monkey-patch skel.step() to capture frames every N sim steps.
    4. Run a ReactLoop where the user_msg is the natural-language task.
    5. Save captured frames as an mp4; return {ok, summary, mp4_path}.

Frame capture is automatic — the agent never needs to call render(). It just
calls move_cartesian / gripper_close / etc. and frames accumulate.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .react_loop import ReactLoop, ReactResult, ToolSpec


# ──────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    """One task's outcome."""

    task_id: str
    task_description: str
    ok: bool
    summary: str
    mp4_path: Optional[Path]
    n_frames: int
    duration_sec: float
    n_tool_calls: int
    trace_path: Optional[Path]
    token_usage: dict
    error: Optional[str] = None
    tool_call_log: list[dict] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Tool descriptions: hand-curated per skeleton method
# Keys map to the EXACT public method names; if a method isn't in this
# dict, the planner skips it (avoids the agent calling internal helpers).
# ──────────────────────────────────────────────────────────────────────────


_ARM_TOOL_SPECS: dict[str, dict] = {
    "home": {
        "description": "Move the arm to its home configuration. Use to reset before / between tasks.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"duration": float(inp.get("duration", 2.0))},
    },
    "move_cartesian": {
        "description": (
            "Move the end-effector to a target XYZ position (in world coordinates, meters). "
            "Use this for any positioning. Returns True iff the motion succeeded. "
            "The arm uses Damped-Least-Squares IK internally. Tip: small relative moves "
            "from the current EE pose are most likely to succeed; check get_ee_pose() first."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "duration": {"type": "number", "default": 2.0},
            },
            "required": ["x", "y", "z"],
        },
        "args": lambda inp: [np.array([inp["x"], inp["y"], inp["z"]], dtype=np.float64)],
        "kwargs": lambda inp: {"duration": float(inp.get("duration", 2.0))},
    },
    "gripper_open": {
        "description": "Open the gripper. Releases any held object.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "gripper_close": {
        "description": (
            "Close the gripper. If a graspable body is within grasp_radius of the EE, "
            "the grasp backend engages and the body is held. Returns True iff something "
            "was grasped (False = gripper closed but no object in reach)."
        ),
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_ee_pose": {
        "description": (
            "Return the current end-effector pose as {position: [x,y,z], rotation: 3x3 matrix flattened}. "
            "Use this to learn where the arm currently is before planning a relative move."
        ),
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_object_position": {
        "description": (
            "Return the world-frame XYZ position of a body in the scene by name. Use to "
            "find graspable objects (banana, mug, etc. — names come from the task / earlier observation)."
        ),
        "schema": {
            "type": "object",
            "properties": {"body_name": {"type": "string"}},
            "required": ["body_name"],
        },
        "args": lambda inp: [str(inp["body_name"])],
        "kwargs": lambda inp: {},
    },
    "is_holding": {
        "description": "Return True iff the gripper currently has an object grasped.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_joint_positions": {
        "description": "Return the current arm joint positions as a list (in joint order).",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
}


_QUADRUPED_TOOL_SPECS: dict[str, dict] = {
    "stand_up": {
        "description": "Move from current pose to the standing home pose via PD. Returns True iff body reached the target height.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"duration": float(inp.get("duration", 2.0))},
    },
    "walk_forward": {
        "description": "Hand-tuned trot. Fragile in v1. Use small `secs` (1-3) and low `speed` (0.1-0.3).",
        "schema": {
            "type": "object",
            "properties": {
                "secs": {"type": "number", "default": 2.0},
                "speed": {"type": "number", "default": 0.2},
            },
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {
            "secs": float(inp.get("secs", 2.0)),
            "speed": float(inp.get("speed", 0.2)),
        },
    },
    "sit": {
        "description": "Lower the body to a folded sit pose.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"duration": float(inp.get("duration", 2.0))},
    },
    "get_body_height": {
        "description": "Return the torso's world-frame Z height in meters.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_base_pose": {
        "description": "Return torso pose {position: [x,y,z], rotation: 9-elt flat matrix}.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
}


_WHEELED_TOOL_SPECS: dict[str, dict] = {
    "drive_forward": {
        "description": "Drive the base straight forward by distance_m meters (both wheels, closed-loop on base pose). Returns True on success.",
        "schema": {
            "type": "object",
            "properties": {
                "distance_m": {"type": "number", "default": 0.3},
                "speed": {"type": "number", "default": 1.0},
            },
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"distance_m": float(inp.get("distance_m", 0.3)),
                               "speed": float(inp.get("speed", 1.0))},
    },
    "turn": {
        "description": "Turn the base in place by angle_rad radians (+ = left/CCW), differential wheels. Verify with get_base_yaw.",
        "schema": {
            "type": "object",
            "properties": {
                "angle_rad": {"type": "number"},
                "speed": {"type": "number", "default": 1.0},
            },
            "required": ["angle_rad"],
        },
        "args": lambda inp: [float(inp.get("angle_rad", inp.get("angle", 0.0)))],
        "kwargs": lambda inp: {"speed": float(inp.get("speed", 1.0))},
    },
    "get_base_pose": {
        "description": "Return base pose {position:[x,y,z], rotation: 9-elt flat matrix}.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
    "get_base_yaw": {
        "description": "Return the base heading (yaw) in radians.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
}


_AERIAL_TOOL_SPECS: dict[str, dict] = {
    "takeoff": {
        "description": "Spin up the rotors and climb to ~height m altitude, then hold a stable hover. Returns True iff upright and at altitude.",
        "schema": {
            "type": "object",
            "properties": {"height": {"type": "number", "default": 0.5}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"height": float(inp.get("height", 0.5))},
    },
    "move_to": {
        "description": "Fly to world target (x,y,z) with a closed loop on get_base_pose; arrive within tol and stay upright.",
        "schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "z": {"type": "number"}, "tol": {"type": "number", "default": 0.1},
            },
            "required": ["x", "y", "z"],
        },
        "args": lambda inp: [float(inp["x"]), float(inp["y"]), float(inp["z"])],
        "kwargs": lambda inp: {"tol": float(inp.get("tol", 0.1))},
    },
    "hover": {
        "description": "Station-keep at the current pose for secs seconds.",
        "schema": {
            "type": "object",
            "properties": {"secs": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"secs": float(inp.get("secs", 2.0))},
    },
    "land": {
        "description": "Descend to the ground and idle the rotors.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
    "get_base_pose": {
        "description": "Return drone pose {position:[x,y,z], rotation: 9-elt flat matrix}.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
}


_HUMANOID_TOOL_SPECS: dict[str, dict] = {
    "stand_balance": {
        "description": "Hold a stable standing pose (joint-space PD) for secs seconds, keeping the torso upright and at standing height.",
        "schema": {
            "type": "object",
            "properties": {"secs": {"type": "number", "default": 3.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"secs": float(inp.get("secs", 3.0))},
    },
    "squat": {
        "description": "Lower the torso by depth m (bend hips+knees) then return to standing, staying balanced.",
        "schema": {
            "type": "object",
            "properties": {
                "depth": {"type": "number", "default": 0.15},
                "secs": {"type": "number", "default": 3.0},
            },
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {"depth": float(inp.get("depth", 0.15)),
                               "secs": float(inp.get("secs", 3.0))},
    },
    "get_torso_height": {
        "description": "Return the torso's world-frame Z height in meters.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
    "get_base_pose": {
        "description": "Return torso pose {position:[x,y,z], rotation: 9-elt flat matrix}.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [], "kwargs": lambda inp: {},
    },
}


def _require_arm(inp: dict) -> str:
    """Read the required bimanual arm label without choosing a default."""
    arm = inp.get("arm")
    if arm not in {"left", "right"}:
        raise ValueError("arm must be explicitly 'left' or 'right'")
    return arm


def _xyz_args(inp: dict) -> list[np.ndarray]:
    return [np.asarray([inp["x"], inp["y"], inp["z"]], dtype=np.float64)]


def _duration_kwargs(inp: dict, default: float) -> dict[str, float]:
    return {"duration": float(inp.get("duration", default))}


def _arm_kwargs(inp: dict) -> dict[str, str]:
    return {"arm": _require_arm(inp)}


def _arm_duration_kwargs(inp: dict, default: float = 2.0) -> dict[str, Any]:
    return {"arm": _require_arm(inp), "duration": float(inp.get("duration", default))}


_HAND_TOOL_SPECS: dict[str, dict] = {
    "get_fingertip_positions": {
        "description": "Return world-frame XYZ positions for the index, middle, ring, and thumb fingertips.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_joint_positions": {
        "description": "Return the hand's current joint positions in the configured joint order.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "move_joints": {
        "description": "Move the dexterous hand to a joint-position target over the requested duration.",
        "schema": {
            "type": "object",
            "properties": {
                "q_target": {"type": "array", "items": {"type": "number"}},
                "duration": {"type": "number", "default": 2.0},
            },
            "required": ["q_target"],
        },
        "args": lambda inp: [np.asarray(inp["q_target"], dtype=np.float64)],
        "kwargs": lambda inp: _duration_kwargs(inp, 2.0),
    },
    "move_fingertips": {
        "description": "Move all four fingertips to world-frame XYZ targets.",
        "schema": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "object",
                    "properties": {
                        finger: {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                        }
                        for finger in ("index", "middle", "ring", "thumb")
                    },
                    "required": ["index", "middle", "ring", "thumb"],
                },
                "duration": {"type": "number", "default": 2.0},
            },
            "required": ["targets"],
        },
        "args": lambda inp: [{
            finger: np.asarray(inp["targets"][finger], dtype=np.float64)
            for finger in ("index", "middle", "ring", "thumb")
        }],
        "kwargs": lambda inp: _duration_kwargs(inp, 2.0),
    },
    "home": {
        "description": "Move all hand joints to their configured home position.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: _duration_kwargs(inp, 2.0),
    },
}


_STRETCH_TOOL_SPECS: dict[str, dict] = {
    "drive_forward": {
        "description": "Drive the Stretch base straight by distance_m meters.",
        "schema": {
            "type": "object",
            "properties": {
                "distance_m": {"type": "number", "default": 0.3},
                "speed": {"type": "number", "default": 0.1},
            },
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: {
            "distance_m": float(inp.get("distance_m", 0.3)),
            "speed": float(inp.get("speed", 0.1)),
        },
    },
    "turn": {
        "description": "Turn the Stretch base in place by angle_rad radians.",
        "schema": {
            "type": "object",
            "properties": {
                "angle_rad": {"type": "number"},
                "speed": {"type": "number", "default": 0.3},
            },
            "required": ["angle_rad"],
        },
        "args": lambda inp: [float(inp["angle_rad"])],
        "kwargs": lambda inp: {"speed": float(inp.get("speed", 0.3))},
    },
    "get_base_pose": {
        "description": "Return the Stretch base world pose.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_base_yaw": {
        "description": "Return the Stretch base heading in radians.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "get_ee_pose": {
        "description": "Return the Stretch end-effector world pose.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "move_cartesian": {
        "description": "Move the Stretch end-effector to a world-frame XYZ target.",
        "schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "duration": {"type": "number", "default": 2.0},
            },
            "required": ["x", "y", "z"],
        },
        "args": _xyz_args,
        "kwargs": lambda inp: _duration_kwargs(inp, 2.0),
    },
    "gripper_open": {
        "description": "Open the Stretch gripper.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "gripper_close": {
        "description": "Close the Stretch gripper.",
        "schema": {"type": "object", "properties": {}},
        "args": lambda inp: [],
        "kwargs": lambda inp: {},
    },
    "home": {
        "description": "Return the Stretch lift, coupled extension, wrist, and base controls to home.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 1.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: _duration_kwargs(inp, 1.0),
    },
}


_BIMANUAL_ARM_PROPERTY = {"type": "string", "enum": ["left", "right"]}
_BIMANUAL_ARM_SCHEMA = {
    "type": "object",
    "properties": {"arm": _BIMANUAL_ARM_PROPERTY},
    "required": ["arm"],
}


def _bimanual_xyz_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
            "duration": {"type": "number", "default": 2.0},
            "arm": _BIMANUAL_ARM_PROPERTY,
        },
        "required": ["x", "y", "z", "arm"],
    }


def _bimanual_q_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "q_target": {"type": "array", "items": {"type": "number"}},
            "duration": {"type": "number", "default": 2.0},
            "arm": _BIMANUAL_ARM_PROPERTY,
        },
        "required": ["q_target", "arm"],
    }


_BIMANUAL_TOOL_SPECS: dict[str, dict] = {
    "get_ee_pose": {
        "description": "Return one ALOHA arm's end-effector pose; arm must be left or right.",
        "schema": _BIMANUAL_ARM_SCHEMA,
        "args": lambda inp: [],
        "kwargs": _arm_kwargs,
    },
    "move_cartesian": {
        "description": "Move one ALOHA arm to a world-frame XYZ target; arm must be left or right.",
        "schema": _bimanual_xyz_schema(),
        "args": _xyz_args,
        "kwargs": lambda inp: _arm_duration_kwargs(inp, 2.0),
    },
    "gripper_open": {
        "description": "Open one ALOHA gripper; arm must be left or right.",
        "schema": _BIMANUAL_ARM_SCHEMA,
        "args": lambda inp: [],
        "kwargs": _arm_kwargs,
    },
    "gripper_close": {
        "description": "Close one ALOHA gripper; arm must be left or right.",
        "schema": _BIMANUAL_ARM_SCHEMA,
        "args": lambda inp: [],
        "kwargs": _arm_kwargs,
    },
    "get_joint_positions": {
        "description": "Return one ALOHA arm's joint positions; arm must be left or right.",
        "schema": _BIMANUAL_ARM_SCHEMA,
        "args": lambda inp: [],
        "kwargs": _arm_kwargs,
    },
    "move_joints": {
        "description": "Move one ALOHA arm to a joint-position target; arm must be left or right.",
        "schema": _bimanual_q_schema(),
        "args": lambda inp: [np.asarray(inp["q_target"], dtype=np.float64)],
        "kwargs": lambda inp: _arm_duration_kwargs(inp, 2.0),
    },
    "home": {
        "description": "Move both ALOHA arms to their configured home positions together.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 2.0}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: _duration_kwargs(inp, 2.0),
    },
    "hold": {
        "description": "Hold both ALOHA arms and grippers under their current controls.",
        "schema": {
            "type": "object",
            "properties": {"duration": {"type": "number", "default": 0.5}},
        },
        "args": lambda inp: [],
        "kwargs": lambda inp: _duration_kwargs(inp, 0.5),
    },
}


_TOOL_REGISTRY: dict[str, dict[str, dict]] = {
    "ArmSerialDLSSkeleton": _ARM_TOOL_SPECS,
    "QuadrupedPDGaitSkeleton": _QUADRUPED_TOOL_SPECS,
    "HandFingertipDLSSkeleton": _HAND_TOOL_SPECS,
    "StretchMobileManipulationSkeleton": _STRETCH_TOOL_SPECS,
    "BimanualSerialDLSSkeleton": _BIMANUAL_TOOL_SPECS,
}


_EXPECTED_SKELETONS = {
    "arm": "ArmSerialDLSSkeleton",
    "quadruped": "QuadrupedPDGaitSkeleton",
    "dexterous_hand": "HandFingertipDLSSkeleton",
    "mobile_manipulator": "StretchMobileManipulationSkeleton",
    "bimanual": "BimanualSerialDLSSkeleton",
}
_EXPECTED_REGISTRIES = {
    "arm": _ARM_TOOL_SPECS,
    "quadruped": _QUADRUPED_TOOL_SPECS,
    "dexterous_hand": _HAND_TOOL_SPECS,
    "mobile_manipulator": _STRETCH_TOOL_SPECS,
    "bimanual": _BIMANUAL_TOOL_SPECS,
    # These are the two existing from-scratch shapes.  They intentionally
    # retain their old registry and do not get new-shape strict validation.
    "wheeled": _WHEELED_TOOL_SPECS,
    "aerial": _AERIAL_TOOL_SPECS,
    "humanoid": _HUMANOID_TOOL_SPECS,
}
_NEW_SHAPE_REQUIREMENTS = {
    "dexterous_hand": (
        "get_fingertip_positions", "get_joint_positions", "move_joints",
        "move_fingertips", "home",
    ),
    "mobile_manipulator": (
        "get_base_pose", "get_base_yaw", "drive_forward", "turn",
        "get_ee_pose", "move_cartesian", "gripper_open", "gripper_close", "home",
    ),
    "bimanual": (
        "get_ee_pose", "move_cartesian", "gripper_open", "gripper_close",
        "get_joint_positions", "move_joints", "home", "hold",
    ),
}


def _missing_methods(skel: Any, method_names: tuple[str, ...]) -> list[str]:
    return [
        name for name in method_names
        if not callable(getattr(skel, name, None))
    ]


def tool_registry_for(
    skel: Any,
    expected_robot_class: Optional[str] = None,
) -> Optional[dict[str, dict]]:
    """Select the curated tool registry for a skeleton instance.

    Trusted new morphology labels are strict: the generated driver must use
    the matching skeleton class and expose its complete public contract.  The
    older from-scratch classes keep the historical method-based dispatch.
    """
    cls_name = type(skel).__name__
    expected = expected_robot_class

    if expected is not None:
        registry = _EXPECTED_REGISTRIES.get(expected)
        if registry is None:
            raise ValueError(f"unsupported expected_robot_class {expected_robot_class!r}")
        expected_skeleton = _EXPECTED_SKELETONS.get(expected)
        if expected in _NEW_SHAPE_REQUIREMENTS:
            if cls_name != expected_skeleton:
                raise ValueError(
                    f"expected {expected_skeleton} for {expected}, got {cls_name}"
                )
            missing = _missing_methods(skel, _NEW_SHAPE_REQUIREMENTS[expected])
            if missing:
                raise ValueError(
                    f"{cls_name} is missing required {expected} methods: {', '.join(missing)}"
                )
        return registry

    registry = _TOOL_REGISTRY.get(cls_name)
    if registry is not None:
        for shape, skeleton in _EXPECTED_SKELETONS.items():
            if skeleton == cls_name and shape in _NEW_SHAPE_REQUIREMENTS:
                missing = _missing_methods(skel, _NEW_SHAPE_REQUIREMENTS[shape])
                if missing:
                    raise ValueError(
                        f"{cls_name} is missing required {shape} methods: {', '.join(missing)}"
                    )
                break
        return registry

    # A from-scratch driver can still return a class named Robot.  Detect a
    # complete new-shape surface before the legacy arm-first fallback.
    for shape in ("bimanual", "mobile_manipulator", "dexterous_hand"):
        if not _missing_methods(skel, _NEW_SHAPE_REQUIREMENTS[shape]):
            return _EXPECTED_REGISTRIES[shape]

    # Preserve the established from-scratch dispatch for H1, Skydio, and
    # other old robot classes.
    if hasattr(skel, "stand_up") and hasattr(skel, "sit"):
        return _QUADRUPED_TOOL_SPECS
    if hasattr(skel, "drive_forward") and hasattr(skel, "turn"):
        return _WHEELED_TOOL_SPECS
    if hasattr(skel, "takeoff") and hasattr(skel, "move_to"):
        return _AERIAL_TOOL_SPECS
    if hasattr(skel, "stand_balance") and hasattr(skel, "squat"):
        return _HUMANOID_TOOL_SPECS
    if hasattr(skel, "move_cartesian") and hasattr(skel, "get_ee_pose"):
        return _ARM_TOOL_SPECS
    return None


def _trusted_robot_class_for_mjcf(mjcf_path: Path) -> Optional[str]:
    """Resolve a workspace MJCF only through the checked-in robot zoo."""
    if not mjcf_path.exists() and not mjcf_path.is_symlink():
        return None
    try:
        from auto_adapter.robot_catalog import find_robot_definition  # noqa: PLC0415
        definition = find_robot_definition(mjcf_path=mjcf_path)
    except (OSError, ValueError, ImportError):
        return None
    if not definition:
        return None
    robot_class = definition.get("class")
    return str(robot_class) if robot_class else None


# ──────────────────────────────────────────────────────────────────────────
# Frame-capturing wrapper
# ──────────────────────────────────────────────────────────────────────────


class _FrameCapture:
    """Capture render frames every N sim steps for an mp4 of the task.

    Preferred path (works for BOTH skeleton drivers and agent-synthesized
    from-scratch drivers): monkeypatch ``mujoco.mj_step`` so EVERY physics step
    is captured regardless of whether the driver steps via ``skel.step()`` or
    calls ``mujoco.mj_step()`` directly (from-scratch drivers do the latter),
    and render with a private offscreen ``mujoco.Renderer`` built from the
    skeleton's model/data (the from-scratch drivers' own ``render()`` uses the
    interactive ``mujoco.viewer`` and returns nothing headless). Falls back to
    the old ``skel.step`` + ``skel.render()`` path if model/data are not
    reachable. Restore with ``.uninstall()``.
    """

    def __init__(self, skel, capture_every: int = 4, max_frames: int = 3000) -> None:
        self._skel = skel
        self._capture_every = int(capture_every)
        self._max_frames = int(max_frames)
        self.frames: list[np.ndarray] = []
        self._step_count = 0
        self._mode = "fallback"
        self._mj = None
        self._orig_mj_step = None
        self._renderer = None
        self._cam = None
        self._track_free = False
        self._track_offset = np.zeros(3, dtype=np.float64)
        self._orig_step = None

        model = getattr(skel, "_model", None) or getattr(skel, "model", None)
        data = getattr(skel, "_data", None) or getattr(skel, "data", None)
        try:
            import mujoco  # noqa: PLC0415
            if model is not None and data is not None:
                self._mj = mujoco
                self._data = data
                self._renderer = mujoco.Renderer(model, height=360, width=480)
                cam = mujoco.MjvCamera()
                try:
                    mujoco.mjv_defaultFreeCamera(model, cam)
                except Exception:  # noqa: BLE001
                    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
                cam.type = mujoco.mjtCamera.mjCAMERA_FREE
                try:
                    cam.azimuth = float(model.vis.global_.azimuth)
                    cam.elevation = float(model.vis.global_.elevation)
                except Exception:  # noqa: BLE001
                    pass
                # AUTO-FRAME the scene from the model's own statistics so any
                # robot (fixed-base arm at ~[0.3,0,0.4], or floating base) is in
                # view. A fixed lookat=[0,0,0] renders arms nearly black.
                try:
                    cam.lookat[:] = np.array(model.stat.center, dtype=float)
                    cam.distance = float(max(model.stat.extent, 0.3)) * 1.8
                except Exception:  # noqa: BLE001
                    cam.distance = 3.0
                self._cam = cam
                # track the base ONLY for genuinely floating-base robots, i.e.
                # the ROBOT'S ROOT joint (joint 0) is free. Arms have free joints
                # too (manipulable scene objects), but joint 0 is a hinge — for
                # those we keep the static auto-framed camera (else lookat would
                # follow an object/garbage qpos and render black).
                self._track_free = (model.njnt > 0 and
                                     model.jnt_type[0] == mujoco.mjtJoint.mjJNT_FREE)
                if self._track_free:
                    # Keep the scene center's height and lateral offset while
                    # following a moving free base; tracking qpos[:3] alone
                    # points the camera at the chassis and hides the arm/table.
                    self._track_offset = (
                        np.asarray(cam.lookat, dtype=np.float64).copy()
                        - np.asarray(data.qpos[0:3], dtype=np.float64)
                    )
                self._orig_mj_step = mujoco.mj_step
                target_model = model
                target_data = data

                def _patched_mj_step(m, d, nstep=1):
                    # MuJoCo exposes one global step function.  Only split
                    # nstep calls for this capture's own model/data; other
                    # worlds must retain their original stepping semantics.
                    if m is not target_model or d is not target_data:
                        return self._orig_mj_step(m, d, nstep)
                    for _ in range(int(nstep)):
                        self._orig_mj_step(target_model, target_data, 1)
                        self._step_count += 1
                        if (self._step_count % self._capture_every) == 0 \
                                and len(self.frames) < self._max_frames:
                            self._grab()

                mujoco.mj_step = _patched_mj_step
                self._mode = "mj_step"
        except Exception:  # noqa: BLE001
            self._renderer = None

        if self._mode == "fallback":
            self._orig_step = skel.step

            def _patched_step(n: int = 1) -> None:
                for _ in range(int(n)):
                    self._orig_step(1)
                    self._step_count += 1
                    if (self._step_count % self._capture_every) == 0 \
                            and len(self.frames) < self._max_frames:
                        try:
                            self.frames.append(skel.render())
                        except Exception:  # noqa: BLE001
                            pass

            skel.step = _patched_step  # type: ignore[method-assign]

    def _grab(self) -> None:
        try:
            if self._renderer is not None:
                if self._track_free:
                    self._cam.lookat[:] = (
                        np.asarray(self._data.qpos[0:3], dtype=np.float64)
                        + self._track_offset
                    )
                self._renderer.update_scene(self._data, camera=self._cam)
                self.frames.append(self._renderer.render())
            else:
                self.frames.append(self._skel.render())
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> None:
        """Force-capture one frame regardless of step counter."""
        if len(self.frames) < self._max_frames:
            self._grab()

    def uninstall(self) -> None:
        if self._mode == "mj_step" and self._orig_mj_step is not None:
            self._mj.mj_step = self._orig_mj_step
            if self._renderer is not None:
                try: self._renderer.close()
                except Exception: pass
        elif self._orig_step is not None:
            self._skel.step = self._orig_step  # type: ignore[method-assign]


# ──────────────────────────────────────────────────────────────────────────
# TaskPlanner
# ──────────────────────────────────────────────────────────────────────────


class TaskPlanner:
    """Layer 1: Natural-language task → skeleton method calls.

    Workflow:
        with TaskPlanner(workspace=...) as planner:
            r1 = planner.execute_task("task A description")
            r2 = planner.execute_task("task B description")

    Each `execute_task` is one ReactLoop on a FRESH skeleton instance —
    the world state resets between tasks (otherwise task B would start
    from wherever task A left the gripper / objects).
    """

    def __init__(
        self,
        *,
        workspace: Path,
        expected_robot_class: Optional[str] = None,
        bedrock_model: str = "us.anthropic.claude-sonnet-4-6",
        model_provider: str = "holistic",
        region: str = "us-east-1",
        run_tag: Optional[str] = None,
        max_iters: int = 25,
        max_tokens_per_turn: int = 6000,
        # capture_every=4 sim steps × sim_dt=0.002s = capture every 8ms (~125
        # captures per second of REAL sim time). 10 was too sparse for SHORT
        # tasks (a grasp produced only ~80 frames → visibly choppy / "frame by
        # frame"); 4 gives smooth motion. Grading is unaffected (video only).
        capture_every: int = 4,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not (self.workspace / "driver.py").exists():
            raise FileNotFoundError(
                f"workspace {self.workspace} has no driver.py; build one via SelfAssemble first"
            )

        # The generated study is agent-authored and cannot select the tool
        # surface.  Prefer an explicit expectation; otherwise use only the
        # checked-in robot zoo matched through this workspace MJCF.
        self.expected_robot_class = (
            expected_robot_class
            if expected_robot_class is not None
            else _trusted_robot_class_for_mjcf(self.workspace / "mjcf.xml")
        )
        self.bedrock_model = bedrock_model
        self.model_provider = model_provider
        self.region = region
        self.max_iters = int(max_iters)
        self.max_tokens_per_turn = int(max_tokens_per_turn)
        self.capture_every = int(capture_every)

        # Make workspace + repo root importable for driver.py
        self._added_paths: list[str] = []
        for p in (str(self.workspace), str(Path(__file__).resolve().parents[2])):
            if p not in sys.path:
                sys.path.insert(0, p)
                self._added_paths.append(p)

        # Namespace recordings + traces by run_tag (e.g. "ours_opus48") so
        # parallel/sequential model runs don't overwrite each other's videos.
        self.run_tag = run_tag
        sub = f"/{run_tag}" if run_tag else ""
        self.rec_dir = self.workspace / f"recordings{sub}"
        self.trace_dir = self.workspace / f"task_traces{sub}"
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "TaskPlanner":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for p in self._added_paths:
            if p in sys.path:
                sys.path.remove(p)

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _load_driver(self) -> Any:
        """Side-load workspace's driver and return the constructed robot.

        Supports both styles:
          1. Framework style:  `mod.build()` returns the skeleton (SO-101
             template, agent fills ArmSpec)
          2. From-scratch style: `mod.Robot.build_from_mjcf("mjcf.xml")`
             returns a Robot instance (agent wrote the whole class)
        """
        sys.modules.pop("driver", None)
        spec = importlib.util.spec_from_file_location(
            "driver", str(self.workspace / "driver.py")
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        orig_cwd = os.getcwd()
        os.chdir(self.workspace)
        try:
            spec.loader.exec_module(mod)
            # Prefer module-level build() if it exists
            if hasattr(mod, "build") and callable(getattr(mod, "build")):
                skel = mod.build()
            elif hasattr(mod, "Robot") and hasattr(mod.Robot, "build_from_mjcf"):
                skel = mod.Robot.build_from_mjcf("mjcf.xml")
            else:
                raise AttributeError(
                    "driver.py must define either `build()` at module level "
                    "OR a `Robot` class with a `build_from_mjcf(mjcf_path)` "
                    "classmethod. Found neither."
                )
        finally:
            os.chdir(orig_cwd)
        return skel

    def _build_tools(
        self,
        skel: Any,
        capture: _FrameCapture,
        call_log: list[dict],
    ) -> list[ToolSpec]:
        """Wrap every public method in the relevant registry as a ToolSpec.

        Trusted new-shape registries require the expected skeleton class and
        complete method surface. Legacy from-scratch classes retain their
        historical method-based dispatch.
        """
        registry = tool_registry_for(skel, self.expected_robot_class)

        if registry is None:
            # Last-resort generic fallback: every public callable becomes a
            # no-arg tool (won't have schemas for parameterised methods)
            tools: list[ToolSpec] = []
            for name in sorted(dir(skel)):
                if name.startswith("_"):
                    continue
                attr = getattr(skel, name)
                if not callable(attr) or name in {"close", "step", "render", "settle"}:
                    continue
                tools.append(self._make_introspected_tool(skel, name, capture, call_log))
            return tools

        tools = []
        for method_name, info in registry.items():
            method = getattr(skel, method_name, None)
            if method is None or not callable(method):
                continue
            tools.append(self._make_registered_tool(
                skel, method_name, info, capture, call_log
            ))
        return tools

    def _make_registered_tool(
        self,
        skel: Any,
        method_name: str,
        info: dict,
        capture: _FrameCapture,
        call_log: list[dict],
    ) -> ToolSpec:
        method = getattr(skel, method_name)
        args_fn: Callable[[dict], list] = info["args"]
        kwargs_fn: Callable[[dict], dict] = info["kwargs"]

        def handler(inp: dict) -> Any:
            t0 = time.time()
            trace = getattr(self, "_physics_trace", None)
            if trace is not None:
                trace.tool, trace.idx = method_name, len(call_log)
            try:
                args = args_fn(inp)
                kwargs = kwargs_fn(inp)
                result = method(*args, **kwargs)
                # Snapshot a frame at the END of each tool call (in addition
                # to mid-call captures from the patched step()) so even no-op
                # tools like get_ee_pose contribute to the video timeline.
                capture.snapshot()
                call_log.append({
                    "tool": method_name,
                    "input": inp,
                    "dur_ms": (time.time() - t0) * 1000.0,
                    "ok": True,
                })
                return _to_jsonable(result)
            except Exception as e:  # noqa: BLE001
                call_log.append({
                    "tool": method_name,
                    "input": inp,
                    "dur_ms": (time.time() - t0) * 1000.0,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                })
                raise

        return ToolSpec(
            name=method_name,
            description=info["description"],
            input_schema=info["schema"],
            handler=handler,
        )

    def _make_introspected_tool(
        self,
        skel: Any,
        method_name: str,
        capture: _FrameCapture,
        call_log: list[dict],
    ) -> ToolSpec:
        method = getattr(skel, method_name)
        try:
            sig = inspect.signature(method)
            sig_str = str(sig)
        except (TypeError, ValueError):
            sig_str = "(*args, **kwargs)"

        def handler(inp: dict) -> Any:
            t0 = time.time()
            trace = getattr(self, "_physics_trace", None)
            if trace is not None:
                trace.tool, trace.idx = method_name, len(call_log)
            try:
                result = method()
                capture.snapshot()
                call_log.append({"tool": method_name, "input": inp, "ok": True,
                                 "dur_ms": (time.time() - t0) * 1000.0})
                return _to_jsonable(result)
            except Exception as e:  # noqa: BLE001
                call_log.append({"tool": method_name, "input": inp, "ok": False,
                                 "error": f"{type(e).__name__}: {e}",
                                 "dur_ms": (time.time() - t0) * 1000.0})
                raise

        return ToolSpec(
            name=method_name,
            description=f"Call {method_name}{sig_str}. (introspected — no curated description)",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )

    # ─── Public API ───────────────────────────────────────────────────────

    def execute_task(
        self,
        task_description: str,
        *,
        task_id: Optional[str] = None,
        capture_video: bool = True,
        reset_world: bool = True,
        driver: Any = None,
        initialize: bool = True,
    ) -> TaskResult:
        """Run one NL task. Always builds a fresh skeleton (clean world).

        The agent gets:
          * the task description as the user message
          * the curated tool surface for this skeleton class
          * a system prompt telling it the task lifecycle
        """
        if task_id is None:
            task_id = f"task_{int(time.time())}"

        # ─── Build a fresh skeleton + frame capture ───────────────────────
        if driver is not None:
            skel = driver
        elif reset_world:
            skel = self._load_driver()
        else:
            raise NotImplementedError("reset_world=False not yet implemented")

        # CRITICAL: settle to a known initial state by calling home() if
        # available. eval.py records before_state AFTER home(); replay also
        # starts from home(). If we skip home() here the agent sees the
        # raw unsettled MJCF pose (objects mid-air by a few mm), creating
        # an asymmetry between what the agent observes and what the
        # evaluator measures the trial against.
        if initialize and hasattr(skel, "home") and callable(getattr(skel, "home")):
            try:
                skel.home()
            except Exception:  # noqa: BLE001 — best-effort homing
                pass

        capture = _FrameCapture(skel, capture_every=self.capture_every)
        # Snapshot the initial state so videos start with the home pose visible
        capture.snapshot()

        call_log: list[dict] = []
        try:
            tools = self._build_tools(skel, capture, call_log)
        except Exception:
            capture.uninstall()
            raise

        # Compute robot-class-specific hints to help the planner
        cls_name = type(skel).__name__
        hint = _CLASS_HINTS.get(cls_name, "")

        system = _PLANNER_SYSTEM.format(
            cls_name=cls_name,
            class_hint=hint,
            tool_names=", ".join(t.name for t in tools),
        )

        trace_path = self.trace_dir / f"{task_id}.jsonl"
        loop = ReactLoop(
            tools=tools,
            system=system,
            model=self.bedrock_model,
            provider=self.model_provider,
            region=self.region,
            max_iters=self.max_iters,
            max_tokens_per_turn=self.max_tokens_per_turn,
            trace_path=trace_path,
        )

        t0 = time.time()
        try:
            result: ReactResult = loop.run(task_description)
        finally:
            capture.uninstall()
        dur = time.time() - t0

        # ─── Save mp4 ─────────────────────────────────────────────────────
        mp4_path: Optional[Path] = None
        if capture_video and capture.frames:
            import imageio  # noqa: PLC0415

            mp4_path = self.rec_dir / f"{task_id}.mp4"
            try:
                # Force FFMPEG plugin — without imageio-ffmpeg, .mp4 silently
                # routes to TiffWriter which can't accept fps/codec. faststart
                # moves moov atom to file start so QuickTime/Safari can play it.
                imageio.mimsave(str(mp4_path), capture.frames,
                                format="FFMPEG", fps=30, codec="libx264",
                                pixelformat="yuv420p",
                                ffmpeg_params=["-movflags", "+faststart"])
            except Exception as e:  # noqa: BLE001
                print(f"!! failed to save {mp4_path}: {e} "
                      f"(install imageio-ffmpeg)", file=sys.stderr)
                mp4_path = None

        # Structural ok: task is OK iff the agent made progress (≥ 1 tool
        # call, all of which succeeded) — even if it didn't reach end_turn
        # before max_iters. This catches long-horizon plans where the agent
        # genuinely completed the work but ran out of iters before sending
        # the closing summary (same logic as orchestrator's expected_artifacts
        # rule).
        any_tool_fail = any(not c["ok"] for c in call_log)
        structural_ok = (
            result.ok
            or (
                not any_tool_fail
                and len(call_log) >= 1
                and result.error
                and "max_iters" in result.error
            )
        )

        return TaskResult(
            task_id=task_id,
            task_description=task_description,
            ok=structural_ok,
            summary=result.final_text,
            mp4_path=mp4_path,
            n_frames=len(capture.frames),
            duration_sec=dur,
            n_tool_calls=len(call_log),
            trace_path=trace_path,
            token_usage=result.total_tokens,
            error=(None if structural_ok else result.error),
            tool_call_log=call_log,
        )


# ──────────────────────────────────────────────────────────────────────────
# Prompts + class hints
# ──────────────────────────────────────────────────────────────────────────


_PLANNER_SYSTEM = """\
You are the OPERATOR of a robot. The user gives you a task in English and you
accomplish it by calling tools that act on a {cls_name}. The world is
deterministic and reset before each task.

Available tools: {tool_names}.

{class_hint}

Procedure for any task:
  1. Use observation tools (get_ee_pose / get_object_position / etc.) to
     understand the current state — don't guess positions.
  2. Plan a sequence of motions / gripper ops that accomplishes the task.
  3. Execute them, OBSERVING after each action that needs feedback
     (e.g. after gripper_close, check is_holding before lifting).
  4. When done, reply with one sentence summarizing what you accomplished
     and any relevant final measurement.

Tips:
  - All coordinates are in WORLD frame, meters.
  - Plan in small, observable steps — don't fire 10 tools without checking
     intermediate state.
  - If a motion fails (returns False or IK unreachable), don't repeat it;
     instead query the EE pose and try a smaller / different target.
  - The video record is automatic; you don't need to render or save anything.
"""


_CLASS_HINTS: dict[str, str] = {
    "ArmSerialDLSSkeleton": (
        "ROBOT CLASS HINT: This is a serial-chain arm with DLS IK. The arm has "
        "an end-effector reference frame; move_cartesian takes (x, y, z) target "
        "coordinates and runs IK internally. For grasping: APPROACH above the "
        "body (~5 cm), DESCEND to within ~2 cm, gripper_close, then move up.\n"
        "Note: the gripper close ctrl direction is calibrated in the driver; "
        "just call gripper_open / gripper_close — they map to the right ctrl."
    ),
    "QuadrupedPDGaitSkeleton": (
        "ROBOT CLASS HINT: This is a 4-legged quadruped with joint-space PD "
        "torque control. Behaviors are stand_up, walk_forward(secs, speed), "
        "and sit. The trot gait is hand-tuned and can be unstable — use small "
        "secs (1-3) and low speed (0.1-0.3); large amplitudes topple the robot."
    ),
    "HandFingertipDLSSkeleton": (
        "ROBOT CLASS HINT: This is a four-finger dexterous hand. Use "
        "get_fingertip_positions or get_joint_positions before a motion; "
        "move_fingertips requires index, middle, ring, and thumb XYZ targets."
    ),
    "StretchMobileManipulationSkeleton": (
        "ROBOT CLASS HINT: This is a mobile Stretch manipulator. Base motion "
        "uses drive_forward and turn; arm motion uses get_ee_pose and "
        "move_cartesian. Keep base alignment separate from arm reaching."
    ),
    "BimanualSerialDLSSkeleton": (
        "ROBOT CLASS HINT: This is a fixed-base bimanual ALOHA. Every arm "
        "tool requires arm='left' or arm='right'; home and hold operate on "
        "both arms together."
    ),
}


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _to_jsonable(x: Any) -> Any:
    """Convert numpy / tuples / etc. to JSON-friendly types for the LLM."""
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (tuple, list)):
        return [_to_jsonable(v) for v in x]
    if isinstance(x, dict):
        return {k: _to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x
