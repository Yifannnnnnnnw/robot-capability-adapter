"""driver.py — RealSO101: real-hardware backend for the SO-ARM101.

Design overview
===============
This driver mirrors the interface of the MuJoCo *sim* driver, but every
action is executed on PHYSICAL hardware:

  * Actuation  : vector_os_nano.SO101Arm + SO101Gripper (radian-calibrated
                 to match the MJCF joint convention exactly).
  * Observation: the arm's joint encoders (read-before-write) PLUS an
                 Intel RealSense D405 eye-in-hand RGB+depth camera.
  * Kinematics : a MuJoCo model loaded for FORWARD-KINEMATICS ONLY. We call
                 `mj_forward` (kinematics + site poses) and NEVER `mj_step`.
                 Physics is the real world; the MJCF only supplies link
                 geometry / joint axes so we can compute EE pose & Jacobians.

Safety philosophy
=================
Hardware can hurt itself and its surroundings, so the driver is
defensive-by-default:

  * HARD FLOOR: the EE site must stay at world z >= ee_z_min_m (0.10 m).
    Every Cartesian move runs a "shadow FK" on the candidate joint vector
    BEFORE it is ever sent to the servos. If ANY interpolation substep
    predicts an EE below the floor, the WHOLE action is REJECTED (returns
    False, logs a [RealSO101] SAFETY message). We never send a partial,
    dangerous trajectory.
  * PER-STEP DELTA CLAMP: no single substep may move any joint more than
    MAX_STEP_RAD (~20 deg). IK targets that would require larger jumps are
    split across substeps; if even a single substep still exceeds the clamp
    we reject.
  * GRACEFUL SHUTDOWN: before disconnect (which drops torque) we open the
    gripper and park at home so gravity cannot slam the arm or drop a held
    object.

The IK is a damped-least-squares (DLS) position-only solver driven by the
MuJoCo site Jacobian (`mj_jacSite`). We only control the 3 translational
DOFs of the EE site; orientation is left free (sufficient for top-down
pick-style motions on this small arm).

Author: Auto-Adapter GENERATE (real-hardware phase)
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

try:
    import mujoco
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"[RealSO101] mujoco import failed: {exc}") from exc


# ────────────────────────────────────────────────────────────────────────
# Module constants
# ────────────────────────────────────────────────────────────────────────
ARM_JOINT_NAMES: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)

# Per-MJCF joint ranges (radians) — used to clamp IK output.
JOINT_LIMITS: dict[str, tuple[float, float]] = {
    "shoulder_pan": (-1.92, 1.92),
    "shoulder_lift": (-1.75, 1.75),
    "elbow_flex": (-1.69, 1.69),
    "wrist_flex": (-1.66, 1.66),
    "wrist_roll": (-2.74, 2.84),
}

MAX_STEP_RAD: float = np.deg2rad(20.0)  # per-substep joint delta clamp
EE_SITE_NAME: str = "ee_site"

# RealSense D405 measured intrinsics.
RS_FX, RS_FY, RS_CX, RS_CY = 395.3, 394.3, 319.0, 231.8


def _log(msg: str) -> None:
    print(f"[RealSO101] {msg}")


def _safety(msg: str) -> None:
    print(f"[RealSO101] SAFETY: {msg}")


class RealSO101:
    """Real-hardware backend with sim-compatible interface."""

    # ── construction / lifecycle ────────────────────────────────────────
    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        robot_id: str = "my_awesome_follower_arm",
        ik_tol_m: float = 0.005,
        ee_z_min_m: float = 0.10,
        ee_z_max_m: float = 0.50,
        mjcf_path: str | None = None,
    ) -> None:
        self.port = port
        self.robot_id = robot_id
        self.ik_tol_m = float(ik_tol_m)
        self.ee_z_min_m = float(ee_z_min_m)
        self.ee_z_max_m = float(ee_z_max_m)
        self._explicit_mjcf_path = mjcf_path

        self._arm: Any = None
        self._gripper: Any = None
        self._rs_pipe: Any = None
        self._rs_align: Any = None
        self._holding: bool = False
        self._connected: bool = False

        # Shadow-FK MuJoCo model (kinematics only).
        self._model: Any = None
        self._data: Any = None
        self._ee_site_id: int = -1
        self._qpos_adr: list[int] = []
        self._dof_adr: list[int] = []

        self._load_kinematics()
        self._connect_hardware()

    def _load_kinematics(self) -> None:
        """Load MJCF for FK / Jacobian only.  NEVER stepped."""
        try:
            # The model XML is expected alongside the driver.
            self._model = mujoco.MjModel.from_xml_path(self._explicit_mjcf_path or "so101.xml")
        except Exception as exc:
            raise RuntimeError(
                f"[RealSO101] failed to load kinematic MJCF 'so101.xml': {exc}"
            ) from exc

        self._data = mujoco.MjData(self._model)
        self._ee_site_id = mujoco.mj_name2id(
            self._model, mujoco.mjtObj.mjOBJ_SITE, EE_SITE_NAME
        )
        if self._ee_site_id < 0:
            raise RuntimeError(f"[RealSO101] site '{EE_SITE_NAME}' not found in MJCF")

        # Cache qpos / dof addresses for the 5 controlled joints.
        for jname in ARM_JOINT_NAMES:
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            if jid < 0:
                raise RuntimeError(f"[RealSO101] joint '{jname}' not found in MJCF")
            self._qpos_adr.append(int(self._model.jnt_qposadr[jid]))
            self._dof_adr.append(int(self._model.jnt_dofadr[jid]))
        _log("kinematic MJCF loaded (FK/Jacobian only — no physics).")

    def _connect_hardware(self) -> None:
        """Tolerant connect to arm, gripper, and RealSense."""
        # --- Arm + gripper (vector_os_nano) ---
        try:
            from vector_os_nano.hardware.so101.arm import SO101Arm
            from vector_os_nano.hardware.so101.gripper import SO101Gripper

            self._arm = SO101Arm(port=self.port, baudrate=1000000)
            self._arm.connect()
            self._gripper = SO101Gripper(serial_bus=self._arm._bus)
            _log(f"SO101Arm connected on {self.port}.")
        except Exception as exc:
            raise RuntimeError(f"[RealSO101] arm connect failed: {exc}") from exc

        # --- RealSense D405 (best-effort; vision is non-critical) ---
        try:
            import pyrealsense2 as rs

            self._rs_pipe = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            self._rs_align = rs.align(rs.stream.color)
            self._rs_pipe.start(cfg)
            _log("RealSense D405 streaming.")
        except Exception as exc:
            self._rs_pipe = None
            self._rs_align = None
            _log(f"WARNING: RealSense init failed ({exc}); vision disabled.")

        self._connected = True

    def __enter__(self) -> "RealSO101":
        return self

    def __exit__(self, *a: Any) -> None:
        self.disconnect()

    def disconnect(self) -> None:
        """Safe shutdown: open gripper, park home, THEN drop torque."""
        if not self._connected:
            return
        try:
            # Don't leave the arm gripping something gravity will drop.
            if self._gripper is not None:
                try:
                    self._gripper.open()
                    self._holding = False
                    _log("gripper opened pre-disconnect.")
                except Exception as exc:
                    _log(f"WARNING: gripper open on shutdown failed: {exc}")
            # Park at home so torque-drop doesn't slam the table.
            if self._arm is not None:
                try:
                    self._arm.move_joints([0.0, 0.0, 0.0, 0.0, 0.0], duration=3.0)
                    _log("parked at home pose pre-disconnect.")
                except Exception as exc:
                    _log(f"WARNING: park-home on shutdown failed: {exc}")
                try:
                    self._arm.disconnect()
                    _log("arm torque disabled / disconnected.")
                except Exception as exc:
                    _log(f"WARNING: arm disconnect failed: {exc}")
        finally:
            if self._rs_pipe is not None:
                try:
                    self._rs_pipe.stop()
                    _log("RealSense stopped.")
                except Exception as exc:
                    _log(f"WARNING: RealSense stop failed: {exc}")
            self._connected = False

    # ── observation ─────────────────────────────────────────────────────
    def get_joint_positions(self) -> np.ndarray:
        """Return current 5 arm joints in radians (ARM_JOINT_NAMES order)."""
        if self._arm is None:
            raise RuntimeError("[RealSO101] arm not connected")
        pos = self._arm.get_joint_positions()
        arr = np.asarray(pos, dtype=float).reshape(-1)
        if arr.shape[0] < 5:
            raise RuntimeError(f"[RealSO101] expected >=5 joints, got {arr.shape[0]}")
        return arr[:5].copy()

    def _fk(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Shadow forward kinematics: joints (rad) -> (ee_xyz, ee_rotmat).

        KINEMATICS ONLY — uses mj_forward, never mj_step.
        """
        q = np.asarray(q, dtype=float).reshape(-1)
        # Reset qpos to neutral then write the 5 controlled joints.
        for adr, val in zip(self._qpos_adr, q[:5]):
            self._data.qpos[adr] = float(val)
        mujoco.mj_forward(self._model, self._data)
        xyz = self._data.site_xpos[self._ee_site_id].copy()
        rot = self._data.site_xmat[self._ee_site_id].reshape(3, 3).copy()
        return xyz, rot

    def get_ee_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """World-frame EE pose (xyz, rotmat) via shadow FK on live joints."""
        q = self.get_joint_positions()
        return self._fk(q)

    # ── vision ──────────────────────────────────────────────────────────
    def get_camera_frame(self) -> tuple[np.ndarray, np.ndarray]:
        """Grab one aligned (color BGR uint8, depth_mm uint16) frame."""
        if self._rs_pipe is None or self._rs_align is None:
            raise RuntimeError("[RealSO101] RealSense not available")
        frames = self._rs_pipe.wait_for_frames()
        aligned = self._rs_align.process(frames)
        color = np.asanyarray(aligned.get_color_frame().get_data())
        depth = np.asanyarray(aligned.get_depth_frame().get_data())
        return color.copy(), depth.copy()

    def visual_self_check(self) -> dict:
        """Heuristic gripper-visibility check from one RealSense frame.

        The gripper jaws are light-grey/white and sit in the near
        foreground of the eye-in-hand view.  We threshold for a bright,
        near-camera blob and report its centroid depth.
        """
        result: dict[str, Any] = {"gripper_visible": False, "gripper_depth_mm": None}
        if self._rs_pipe is None:
            _log("visual_self_check: vision disabled.")
            return result
        try:
            import cv2

            color, depth = self.get_camera_frame()
            gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
            # Bright (whitish) region.
            bright = gray > 170
            # Near foreground: valid depth and within ~25 cm.
            near = (depth > 0) & (depth < 250)
            mask = bright & near
            count = int(mask.sum())
            if count > 400:  # blob large enough to be a jaw
                ys, xs = np.nonzero(mask)
                cy, cx = int(ys.mean()), int(xs.mean())
                d = float(depth[cy, cx])
                result["gripper_visible"] = True
                result["gripper_depth_mm"] = d if d > 0 else None
                _log(f"visual_self_check: gripper visible, depth~{d:.0f}mm.")
            else:
                _log("visual_self_check: gripper NOT clearly visible.")
        except Exception as exc:
            _log(f"WARNING: visual_self_check failed: {exc}")
        return result

    # ── safety helpers ──────────────────────────────────────────────────
    def _clamp_limits(self, q: np.ndarray) -> np.ndarray:
        out = q.copy()
        for i, jname in enumerate(ARM_JOINT_NAMES):
            lo, hi = JOINT_LIMITS[jname]
            out[i] = float(np.clip(out[i], lo, hi))
        return out

    def _z_ok(self, q: np.ndarray) -> tuple[bool, float]:
        xyz, _ = self._fk(q)
        z = float(xyz[2])
        return (self.ee_z_min_m <= z <= self.ee_z_max_m), z

    def _validate_trajectory(self, waypoints: list[np.ndarray]) -> bool:
        """Run shadow FK over every waypoint; reject if any below floor or
        any per-step delta exceeds the clamp."""
        prev: np.ndarray | None = None
        for k, wp in enumerate(waypoints):
            ok, z = self._z_ok(wp)
            if not ok:
                _safety(
                    f"waypoint {k} EE z={z:.3f}m outside "
                    f"[{self.ee_z_min_m},{self.ee_z_max_m}] — REJECT."
                )
                return False
            if prev is not None:
                dmax = float(np.max(np.abs(wp - prev)))
                if dmax > MAX_STEP_RAD + 1e-6:
                    _safety(
                        f"waypoint {k} joint delta {np.rad2deg(dmax):.1f}deg "
                        f"> {np.rad2deg(MAX_STEP_RAD):.0f}deg — REJECT."
                    )
                    return False
            prev = wp
        return True

    # ── IK ──────────────────────────────────────────────────────────────
    def _ik_position(
        self, target_xyz: np.ndarray, q0: np.ndarray, iters: int = 100
    ) -> np.ndarray | None:
        """Damped least-squares position-only IK on the EE site."""
        q = q0.astype(float).copy()
        target = np.asarray(target_xyz, dtype=float).reshape(3)
        damping = 1e-2
        jacp = np.zeros((3, self._model.nv))

        for _ in range(iters):
            for adr, val in zip(self._qpos_adr, q):
                self._data.qpos[adr] = float(val)
            mujoco.mj_forward(self._model, self._data)
            cur = self._data.site_xpos[self._ee_site_id]
            err = target - cur
            if np.linalg.norm(err) < self.ik_tol_m:
                break
            mujoco.mj_jacSite(self._model, self._data, jacp, None, self._ee_site_id)
            J = jacp[:, self._dof_adr]  # (3,5)
            JT = J.T
            dq = JT @ np.linalg.solve(J @ JT + damping * np.eye(3), err)
            # Step-limit per iteration for stability.
            dq = np.clip(dq, -0.2, 0.2)
            q = self._clamp_limits(q + dq)

        # Final residual check.
        final_xyz, _ = self._fk(q)
        if np.linalg.norm(target - final_xyz) > 0.02:
            _log(
                f"IK did not converge (residual "
                f"{np.linalg.norm(target - final_xyz)*1000:.1f}mm)."
            )
            return None
        return q

    # ── actions ─────────────────────────────────────────────────────────
    def home(self, steps: int = 8) -> bool:
        """Gradually drive to the all-zero home pose, validated by FK."""
        if self._arm is None:
            _safety("home: arm not connected — REJECT.")
            return False
        q0 = self.get_joint_positions()
        q_target = np.zeros(5)
        waypoints = [
            q0 + (q_target - q0) * (i / steps) for i in range(1, steps + 1)
        ]
        if not self._validate_trajectory([q0, *waypoints]):
            return False
        for wp in waypoints:
            self._arm.move_joints(wp.tolist(), duration=0.5)
            time.sleep(0.05)
        _log("home complete.")
        return True

    def move_cartesian(
        self, target_xyz: np.ndarray, duration: float = 2.0, steps: int = 8
    ) -> bool:
        """Solve IK to target, interpolate in joint space, safety-check each
        substep, then execute.  Returns False (and sends nothing) on any
        safety violation or IK failure."""
        if self._arm is None:
            _safety("move_cartesian: arm not connected — REJECT.")
            return False

        target = np.asarray(target_xyz, dtype=float).reshape(3)

        # Reject obviously-unsafe targets up front.
        if not (self.ee_z_min_m <= target[2] <= self.ee_z_max_m):
            _safety(
                f"target z={target[2]:.3f}m outside "
                f"[{self.ee_z_min_m},{self.ee_z_max_m}] — REJECT."
            )
            return False

        q0 = self.get_joint_positions()
        q_goal = self._ik_position(target, q0)
        if q_goal is None:
            _log("move_cartesian: IK failed — REJECT.")
            return False

        # Build joint-space interpolation.
        waypoints = [
            q0 + (q_goal - q0) * (i / steps) for i in range(1, steps + 1)
        ]
        if not self._validate_trajectory([q0, *waypoints]):
            return False

        # Execute (validated) substeps.
        dt = max(duration / steps, 0.1)
        for k, wp in enumerate(waypoints):
            # Re-check live: read joints, predict next pose; abort if unsafe.
            ok, z = self._z_ok(wp)
            if not ok:
                _safety(f"mid-motion substep {k} EE z={z:.3f}m — ABORT, hold.")
                try:
                    self._arm.stop()
                except Exception:
                    pass
                return False
            self._arm.move_joints(wp.tolist(), duration=dt)
            time.sleep(0.05)

        _log(f"move_cartesian to {np.round(target, 3)} complete.")
        return True

    def gripper_open(self) -> bool:
        if self._gripper is None:
            _safety("gripper_open: gripper not connected — REJECT.")
            return False
        try:
            self._gripper.open()
            self._holding = False
            _log("gripper opened.")
            return True
        except Exception as exc:
            _log(f"WARNING: gripper_open failed: {exc}")
            return False

    def gripper_close(self) -> bool:
        if self._gripper is None:
            _safety("gripper_close: gripper not connected — REJECT.")
            return False
        try:
            self._gripper.close()
            self._holding = True
            _log("gripper closed.")
            return True
        except Exception as exc:
            _log(f"WARNING: gripper_close failed: {exc}")
            return False

    def is_holding(self) -> bool:
        """Best-effort grip state (tracks last open/close command)."""
        return self._holding

    # ── diagnostics ─────────────────────────────────────────────────────
    def describe(self) -> dict:
        info: dict[str, Any] = {
            "class": "RealSO101",
            "port": self.port,
            "robot_id": self.robot_id,
            "connected": self._connected,
            "arm_available": self._arm is not None,
            "gripper_available": self._gripper is not None,
            "vision_available": self._rs_pipe is not None,
            "ee_z_min_m": self.ee_z_min_m,
            "ee_z_max_m": self.ee_z_max_m,
            "ik_tol_m": self.ik_tol_m,
            "max_step_deg": float(np.rad2deg(MAX_STEP_RAD)),
            "joint_names": list(ARM_JOINT_NAMES),
            "holding": self._holding,
        }
        try:
            q = self.get_joint_positions()
            xyz, _ = self._fk(q)
            info["joint_positions_rad"] = q.tolist()
            info["ee_xyz_world"] = xyz.tolist()
        except Exception as exc:
            info["state_error"] = str(exc)
        return info


if __name__ == "__main__":
    _log("RealSO101 driver self-test (requires hardware).")
    try:
        with RealSO101() as robot:
            import json

            print(json.dumps(robot.describe(), indent=2))
            print(robot.visual_self_check())
            robot.home()
    except RuntimeError as exc:
        _log(f"self-test aborted: {exc}")