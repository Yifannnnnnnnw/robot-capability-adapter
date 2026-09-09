#!/usr/bin/env python3
"""Auto-Adapter Phase 2 GENERATE — invoked on real hardware (SO-ARM101).

Feeds the LLM agent:
  • MJCF (kinematic reference only, NOT physics)
  • LeRobot SO101Follower API (actuation channel)
  • RealSense D405 API (observation channel — eye-in-hand)
  • Existing calibration JSON (with documented sign-flip homing offsets)
  • Safety constraints (z floor, gradual motion, vision-validated EE)
  • Required driver surface (home, move_cartesian, gripper, get_ee_pose_visual)

The agent synthesises a Python file that uses LeRobot for actuation and
RealSense for closed-loop EE pose estimation. Output saved to
`artifacts/auto_adapter_real_so101_artifacts/driver.py`.

This is paper §X "Sim-to-real via Auto-Adapter" — the same pipeline
that generates sim drivers (with MuJoCo as feedback) generates real
drivers (with vision + servo readings as feedback).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import boto3
from anthropic import AnthropicBedrock

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "artifacts" / "auto_adapter_real_so101_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_ID = "us.anthropic.claude-opus-4-8"  # Top reasoning tier (May 2026)
# Fallback: us.anthropic.claude-sonnet-4-6


def main() -> None:
    print("=== Auto-Adapter Phase 2 GENERATE: real SO-ARM101 ===\n")

    # ── Load context: MJCF, calibration, API surfaces ────────────────
    mjcf_path = REPO_ROOT / "artifacts" / "auto_adapter_so101_v2_artifacts" / "mjcf.xml"
    mjcf_text = mjcf_path.read_text()
    # Trim — we don't need the full mesh asset list for the agent
    # Keep <worldbody> through end (joints + actuators + objects)
    m = re.search(r"<worldbody>.*?</mujoco>", mjcf_text, re.DOTALL)
    mjcf_kinematics = m.group(0) if m else mjcf_text

    calibration = json.loads(
        (REPO_ROOT / "real_robot" / "context" / "calibration.json").read_text()
    )

    lerobot_api = """
USE THE VECTOR-OS-NANO STACK (battle-tested SO-ARM101 hardware abstraction
with EXPLICIT encoder-to-radian calibration that matches the MJCF joint
convention exactly):

from vector_os_nano.hardware.so101.arm import SO101Arm
from vector_os_nano.hardware.so101.joint_config import ARM_JOINT_NAMES, JOINT_CONFIG

arm = SO101Arm(port="/dev/ttyACM0", baudrate=1000000)
arm.connect()  # read-before-write torque-enable pattern; safe startup

# Joint positions: list of 5 radians, ordered as ARM_JOINT_NAMES.
positions = arm.get_joint_positions()   # 5-element list, radians

# Move arm to target joint positions (50-waypoint smooth interp under the hood)
arm.move_joints([0, 0, 0, 0, 0], duration=3.0)   # all-zero home, 3 second motion

# Gripper is controlled via the raw bus (separate from arm joints):
from vector_os_nano.hardware.so101.gripper import SO101Gripper
gripper = SO101Gripper(bus=arm._bus)   # share the same serial bus
gripper.open()   # blocking
gripper.close()  # blocking

# Stop holds current position:
arm.stop()

# Disconnect DISABLES TORQUE on all servos — gravity will collapse the arm
# unless you first move to a safe parked pose:
arm.disconnect()

# IMPORTANT: BEFORE arm.disconnect() you MUST move to a safe park pose
# (e.g. arm.move_joints([0,0,0,0,0]) home) so when torque drops the arm
# doesn't slam onto the table.

NOTES on convention:
 - vector_os_nano's joint_config.py has explicit (enc_min, enc_max) ↔
   (rad_min, rad_max) per joint. Its "all-zero" joint config maps to the
   physical pose that ALSO corresponds to the MJCF home_qpos=[0,0,0,0,0]
   (i.e. arm horizontal forward, EE roughly at (0.39, 0, 0.23) world).
 - This is DIFFERENT from LeRobot's `SO101Follower` calibration convention.
   Stick to vector_os_nano for both reads and writes — DO NOT mix.
"""

    realsense_api = """
import pyrealsense2 as rs
import numpy as np

# Intrinsics (measured for our D405): fx=395.3, fy=394.3, cx=319.0, cy=231.8
# (Read live with `pipe.get_active_profile().get_stream(rs.stream.color)`)

pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
align = rs.align(rs.stream.color)  # align depth pixels to color frame

pipe.start(cfg)
frames = pipe.wait_for_frames()
aligned = align.process(frames)
color_np = np.asanyarray(aligned.get_color_frame().get_data())   # (480,640,3) BGR
depth_mm = np.asanyarray(aligned.get_depth_frame().get_data())   # (480,640) uint16 mm
pipe.stop()

# Pixel (u,v) + depth(m) → 3D camera-frame point:
def pixel_to_xyz_cam(u, v, depth_m, fx, fy, cx, cy):
    z = depth_m
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy
    return np.array([x, y, z])
"""

    realsense_mount = """
The RealSense D405 is EYE-IN-HAND: mounted on the gripper, looking
forward in the gripper's local frame. The transform from the EE site
(ee_site in MJCF, at gripper jaw tip) to the camera optical frame is
UNKNOWN at this time — the agent should treat camera frame as
ego-centric to the gripper. For visual *relative* error correction
(see gripper drift across two readings), no transform is needed: pixel
displacement in camera frame maps directly to required EE motion (with
a small sign+scale calibration the agent can compute online).

For absolute EE pose in the WORLD frame: use forward kinematics from
joint readings (MJCF kinematic chain). Vision is for verifying that
joint→EE mapping AND for detecting drift, not for primary EE pose
estimation."""

    safety_spec = """
HARD SAFETY (driver MUST enforce):
  • EE z >= 0.10 m in the world frame (table is at z=0.02; 8 cm buffer).
  • If shadow FK predicts EE z below floor, REFUSE the action and return False.
  • Per-step joint delta <= 20° (gradual interpolation, 8 sub-steps per
    move_cartesian, ~0.5s each).
  • Read joint pos BEFORE every action; ABORT mid-motion if shadow FK
    predicts the next interpolated step would put EE below floor.
  • Open gripper before disconnect (don't leave it gripping something
    that gravity will then drop when torque cuts).
"""

    required_surface = """
Required class: `RealSO101`. Methods (all signatures match sim driver):

    def __init__(self, port="/dev/ttyACM0", robot_id="my_awesome_follower_arm",
                 ik_tol_m=0.005, ee_z_min_m=0.10, ee_z_max_m=0.50): ...
    def __enter__(self) -> "RealSO101": ...
    def __exit__(self, *a) -> None: ...
    def disconnect(self) -> None: ...

    # --- Observation (uses LeRobot + shadow MJCF FK) ---
    def get_joint_positions(self) -> np.ndarray:       # 5-vec radians
    def get_ee_pose(self) -> tuple[np.ndarray, np.ndarray]:   # (xyz, rotmat) world frame, via shadow FK

    # --- Vision (uses RealSense) ---
    def get_camera_frame(self) -> tuple[np.ndarray, np.ndarray]:  # (color BGR, depth_mm)
    def visual_self_check(self) -> dict:               # returns {"gripper_visible": bool, "gripper_depth_mm": float|None}

    # --- Action ---
    def home(self, steps=8) -> bool:                   # gradual to all-zero
    def move_cartesian(self, target_xyz: np.ndarray, duration=2.0, steps=8) -> bool:
    def gripper_open(self) -> bool:
    def gripper_close(self) -> bool:
    def is_holding(self) -> bool:

    # --- Diagnostics ---
    def describe(self) -> dict:
"""

    user_msg = f"""You are Auto-Adapter Phase 2 GENERATE, but invoked on REAL hardware:
  • The actuation channel is LeRobot's SO101Follower (NOT MuJoCo physics).
  • The observation channel is the SO101Follower's joint readings PLUS an
    Intel RealSense D405 mounted on the gripper (eye-in-hand RGB+depth).
  • The MJCF model is provided for KINEMATIC REFERENCE ONLY — its
    forward kinematics is a good geometric reference for the SO-ARM101's
    link lengths and joint axes. NEVER call mj_step or any physics
    integration; only mj_forward (kinematics-only) to compute EE pose
    from joint angles.

This synthesis is the analogue of our sim Generate phase: produce one
self-contained Python file `driver.py` that exposes the same robot
interface as the sim driver, but backed by real hardware.

Below is everything you need.

# ── 1. MJCF (kinematics reference — read joints, FK only) ──
```xml
{mjcf_kinematics}
```

# ── 2. LeRobot SO101Follower API (actuation channel) ──
```python
{lerobot_api}
```

# ── 3. RealSense D405 API (observation channel) ──
```python
{realsense_api}
```

# ── 4. Camera mount ──
{realsense_mount}

# ── 5. Existing calibration JSON ──
The user has already calibrated the servos using LeRobot's calibration
flow. Connecting with `calibrate=False` and matching `id` loads this:

```json
{json.dumps(calibration, indent=2)}
```

Note that homing_offset can be NEGATIVE for some joints (elbow_flex,
wrist_flex) — LeRobot's get_observation already handles this. You read
positions in DEGREES that match the convention the user calibrated
against. The user calibrated such that the visual home pose
(arm pointing forward, gripper at workspace center, slight downward tilt)
corresponds to all-joints-at-0°. Forward kinematics in MJCF with
qpos=[0,0,0,0,0] rad should match the real EE pose to within
~2-3 cm (sim-to-real residual we accept).

# ── 6. Hard safety ──
{safety_spec}

# ── 7. Required driver class surface ──
```python
{required_surface}
```

# ── 8. Implementation notes ──

(a) ALL hardware connections must be tolerant — wrap connect() in
try/except, log errors, raise descriptive RuntimeError.

(b) For `get_ee_pose`: read live joints (`get_joint_positions`), write
into MJCF qpos, call `mj_forward`, read site_xpos for `ee_site`. NEVER
call `mj_step` — this is FK only.

(c) For `move_cartesian`: numerical DLS IK from current joints to
target xyz using MJCF Jacobian. THEN interpolate in joint space across
8 substeps with safety check per step. Send each substep via
SO101Follower.send_action and wait briefly.

(d) For `visual_self_check`: grab one RealSense frame; estimate whether
the gripper is visible (heuristic: a roughly white blob in the
foreground of the depth image). If yes, return depth at the centroid.

(e) DEPENDENCIES: numpy, mujoco, lerobot, pyrealsense2, opencv-python.

Output the COMPLETE `driver.py` file. Start with a top-of-file
docstring explaining design choices. No code-fence around the whole
file. Just the .py contents.
"""

    system_msg = """You are Auto-Adapter's GENERATE phase for real hardware.

Output ONLY the Python file contents. No explanatory text before or
after. Begin with the module docstring; end with `if __name__ == "__main__":`
or just the last function/class definition.

The driver MUST be safe: every action that could put the EE below the
floor must be REJECTED (return False, log a SAFETY message) rather than
silently sending a dangerous joint command.

The MJCF is for kinematics ONLY. NEVER call mj_step or step any physics
in the driver — physics happens on real hardware.

Use Python 3.10+ features. Type hints required. Logging via print()
prefixed with [RealSO101]."""

    client = AnthropicBedrock(aws_region="us-east-1")
    print(f"Model: {MODEL_ID}")
    print(f"Prompt size: {len(user_msg)} chars\n")
    print("Synthesising driver... (1–3 min on Opus)\n")
    t0 = time.time()

    # Opus 4.8 deprecates `temperature` (always uses optimal sampling
    # internally); other models still accept it.
    common = dict(model=MODEL_ID, max_tokens=12000,
                  system=system_msg,
                  messages=[{"role": "user", "content": user_msg}])
    try:
        resp = client.messages.create(**common, temperature=0.0)
    except Exception as e:
        if "temperature" in str(e).lower() and "deprecated" in str(e).lower():
            resp = client.messages.create(**common)
        else:
            raise
    dt = time.time() - t0
    tokens_in = int(resp.usage.input_tokens)
    tokens_out = int(resp.usage.output_tokens)
    cost = tokens_in * 15e-6 + tokens_out * 75e-6  # Opus 4.7 estimated pricing
    print(f"  done {dt:.1f}s, in={tokens_in} out={tokens_out} cost~${cost:.3f}\n")

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Strip code fence if any
    if text.startswith("```"):
        text = re.sub(r"^```(?:python)?\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)

    driver_path = OUT_DIR / "driver.py"
    driver_path.write_text(text)
    print(f"Saved: {driver_path}  ({len(text)} bytes)\n")

    # Persist synthesis metadata
    meta = {
        "model": MODEL_ID,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd_est": cost,
        "wall_sec": dt,
        "prompt_chars": len(user_msg),
        "driver_size_bytes": len(text),
    }
    (OUT_DIR / "synthesis_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"meta: {OUT_DIR / 'synthesis_meta.json'}")


if __name__ == "__main__":
    main()
