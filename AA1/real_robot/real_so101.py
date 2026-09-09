"""SO-ARM101 real-robot driver — same interface as the sim driver, so the
LLM-agent ReAct loop (TaskPlanner) can run unchanged.

Strategy: we treat MuJoCo's `data.qpos` as the source-of-truth STATE in
the sim driver. The IK code (DLS, pure numpy) and the
linear-joint-space trajectory generator are arm-kinematics-only and stay
the same. The only swap is the physics stepper:

    sim:   set ctrl  -> mj_step (1)
    real:  send_action({joint: deg})   -> servo bus moves over duration

Because MuJoCo's forward kinematics (`mj_forward`) is just math, we keep
a *shadow* MuJoCo model that mirrors the real joint angles each time
we read from the servos. IK / Jacobian are computed against the shadow
model. The shadow never steps physics — we just write qpos and call
mj_forward to get EE pose.

Run on Jetson Orin Nano with LeRobot installed:
    conda activate lerobot
    python real_robot/real_so101.py --port /dev/ttyACM0 --test home_only
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

try:
    import mujoco
except ImportError as e:
    raise ImportError(
        "mujoco required for IK / forward kinematics. "
        "pip install mujoco"
    ) from e


# ─── Sim-side imports: we reuse the synthesized driver's IK directly ────
# The sim driver is a self-contained Robot class with build_from_mjcf,
# inverse_kinematics, compute_jacobian. We do NOT call its move_cartesian
# (which steps physics) — we only borrow IK + Jacobian.


# SO-ARM101 calibration / convention:
#   - servos return degrees (use_degrees=True)
#   - 5 arm joints + 1 gripper
#   - arm_joint_names match our MJCF
ARM_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
GRIPPER_JOINT = "gripper"
# Home config: all-zero for the arm joints
HOME_ARM_DEG = [0.0, 0.0, 0.0, 0.0, 0.0]
GRIPPER_OPEN_NORM = 100.0  # use_degrees=False would be 0-1; with degrees we have 0..100 range_0_100
GRIPPER_CLOSED_NORM = 0.0


class RealSO101:
    """Real-robot driver wrapping LeRobot's SO101Follower.

    Exposes the SAME public methods as the sim driver:
        home(), get_joint_positions(), get_ee_pose(), move_cartesian(xyz, duration),
        gripper_open(), gripper_close(), get_object_position(name).

    The agent (TaskPlanner / ReAct loop) interacts only through this surface,
    so an agent trace recorded in sim can be REPLAYED on the real robot by
    iterating its tool_call_log against this class.
    """

    def __init__(
        self,
        mjcf_path: str,
        port: str = "/dev/ttyACM0",
        robot_id: str = "my_awesome_follower_arm",
        ik_tol: float = 0.005,
        ik_max_iter: int = 100,
        ik_lambda_base: float = 0.1,
        max_step_deg: float = 20.0,    # tighter cap; was 30°
        ee_z_min_m: float = 0.10,      # SAFETY: refuse any move with target z below this
        ee_z_max_m: float = 0.50,      # SAFETY: refuse target above this (workspace ceiling)
    ):
        self.ee_z_min = ee_z_min_m
        self.ee_z_max = ee_z_max_m
        # ─── Shadow MuJoCo model for IK / forward kinematics ──────────
        # Used ONLY for math: qpos write + mj_forward + Jacobian compute.
        # NEVER mj_step.
        self.model = mujoco.MjModel.from_xml_path(mjcf_path)
        self.data = mujoco.MjData(self.model)

        # Resolve joint qpos addresses
        self.joint_qpos_addrs = []
        for jn in ARM_JOINT_NAMES:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, jn)
            if jid < 0:
                raise ValueError(f"joint {jn!r} not found in MJCF")
            self.joint_qpos_addrs.append(self.model.jnt_qposadr[jid])
        self.n_joints = len(ARM_JOINT_NAMES)

        # EE site
        self.site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        if self.site_id < 0:
            raise ValueError("ee_site not found in MJCF")

        # Joint limits from MJCF (radians)
        self.joint_limits = []
        for jn in ARM_JOINT_NAMES:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, jn)
            rng = self.model.jnt_range[jid]
            self.joint_limits.append((float(rng[0]), float(rng[1])))

        # IK params
        self.ik_tol = ik_tol
        self.ik_max_iter = ik_max_iter
        self.ik_lambda_base = ik_lambda_base
        self.max_step_deg = max_step_deg

        # ─── Connect real robot (LeRobot SO101Follower) ──────────────
        from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        cfg = SO101FollowerConfig(
            port=port,
            id=robot_id,
            use_degrees=True,
            disable_torque_on_disconnect=True,
        )
        self.robot = SO101Follower(cfg)
        self.robot.connect(calibrate=False)
        # Sync shadow to current real pose
        self._refresh_shadow_from_real()
        print(f"[RealSO101] Connected to {port}, robot_id={robot_id!r}")
        ee, _ = self.get_ee_pose()
        print(f"[RealSO101] EE at startup: {ee}")

    def disconnect(self):
        if hasattr(self, "robot") and self.robot is not None:
            try:
                self.robot.disconnect()
                print("[RealSO101] Disconnected (torque disabled)")
            except Exception as e:
                print(f"[RealSO101] disconnect warning: {e}")

    def __enter__(self): return self
    def __exit__(self, *a): self.disconnect()

    # ─── State sync ──────────────────────────────────────────────────

    def _read_real_joint_angles_rad(self) -> np.ndarray:
        """Read current servo positions, return as radians (5 arm joints)."""
        obs = self.robot.get_observation()
        deg = np.array([obs[f"{jn}.pos"] for jn in ARM_JOINT_NAMES], dtype=np.float64)
        return np.deg2rad(deg)

    def _refresh_shadow_from_real(self) -> None:
        q_rad = self._read_real_joint_angles_rad()
        for i, addr in enumerate(self.joint_qpos_addrs):
            self.data.qpos[addr] = q_rad[i]
        mujoco.mj_forward(self.model, self.data)

    # ─── Public agent-facing interface (matches sim driver) ──────────

    def get_joint_positions(self) -> np.ndarray:
        """Return current 5 arm joint angles in radians."""
        return self._read_real_joint_angles_rad()

    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return (EE xyz, EE rotmat) in world frame."""
        self._refresh_shadow_from_real()
        xyz = self.data.site_xpos[self.site_id].copy()
        rotmat = self.data.site_xmat[self.site_id].reshape(3, 3).copy()
        return xyz, rotmat

    def home(self, steps: int = 8, step_dt: float = 0.5) -> bool:
        """Send all arm joints to 0.0° gradually to avoid swinging through the table.

        Reads current pose, computes a linear interpolation in joint space
        across N steps to zero. Each step refreshes the shadow model and
        checks predicted EE z stays above the safety floor; if not, abort.
        """
        print("[RealSO101] home() — gradual")
        obs = self.robot.get_observation()
        current_deg = np.array([obs[f"{jn}.pos"] for jn in ARM_JOINT_NAMES])
        target_deg = np.zeros_like(current_deg)
        for s in range(1, steps + 1):
            alpha = s / steps
            interp_deg = (1 - alpha) * current_deg + alpha * target_deg
            # SAFETY: simulate this joint config in shadow and check EE z
            for i, addr in enumerate(self.joint_qpos_addrs):
                self.data.qpos[addr] = float(np.deg2rad(interp_deg[i]))
            mujoco.mj_forward(self.model, self.data)
            ee_z = float(self.data.site_xpos[self.site_id][2])
            if ee_z < self.ee_z_min - 0.03:  # 3cm buffer below floor
                print(f"[RealSO101] SAFETY ABORT home step {s}/{steps}: predicted EE z={ee_z:.3f} below floor {self.ee_z_min}")
                return False
            action = {jn: float(interp_deg[i]) for i, jn in
                      [(0, "shoulder_pan.pos"), (1, "shoulder_lift.pos"),
                       (2, "elbow_flex.pos"), (3, "wrist_flex.pos"),
                       (4, "wrist_roll.pos")]}
            action["gripper.pos"] = GRIPPER_OPEN_NORM
            self.robot.send_action(action)
            time.sleep(step_dt)
        self._refresh_shadow_from_real()
        return True

    def compute_jacobian(self) -> np.ndarray:
        """3x5 position Jacobian at current shadow pose."""
        jacp = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jacp, None, self.site_id)
        return jacp[:, :self.n_joints]

    def inverse_kinematics(
        self,
        target_xyz: np.ndarray,
        q_init: Optional[np.ndarray] = None,
        max_iter: Optional[int] = None,
        tol: Optional[float] = None,
    ) -> Tuple[np.ndarray, bool, float]:
        """DLS IK with adaptive damping — identical math to sim driver."""
        max_iter = max_iter or self.ik_max_iter
        tol = tol or self.ik_tol
        q = q_init.copy() if q_init is not None else self.get_joint_positions()
        for _ in range(max_iter):
            for i, addr in enumerate(self.joint_qpos_addrs):
                self.data.qpos[addr] = q[i]
            mujoco.mj_forward(self.model, self.data)
            cur = self.data.site_xpos[self.site_id].copy()
            err = target_xyz - cur
            if np.linalg.norm(err) < tol:
                return q, True, float(np.linalg.norm(err))
            J = self.compute_jacobian()
            lam = self.ik_lambda_base * (1.0 + np.linalg.norm(err))
            JtJ = J.T @ J + lam ** 2 * np.eye(self.n_joints)
            dq = np.linalg.solve(JtJ, J.T @ err)
            step = min(1.0, 0.5 / (np.linalg.norm(dq) + 1e-6))
            q = q + step * dq
            # Clamp to joint limits
            for i in range(self.n_joints):
                lo, hi = self.joint_limits[i]
                q[i] = max(lo, min(hi, q[i]))
        return q, False, float(np.linalg.norm(err))

    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        duration: float = 2.0,
        steps: int = 8,
    ) -> bool:
        """Move EE to target xyz. SAFE stepwise execution in joint space."""
        # ─── SAFETY 1: workspace Z bounds for the target ───────────
        if target_xyz[2] < self.ee_z_min:
            print(f"[RealSO101] SAFETY REJECT: target z={target_xyz[2]:.3f} < min {self.ee_z_min} (table risk)")
            return False
        if target_xyz[2] > self.ee_z_max:
            print(f"[RealSO101] SAFETY REJECT: target z={target_xyz[2]:.3f} > max {self.ee_z_max}")
            return False
        q_now = self.get_joint_positions()
        q_target, ok, err_m = self.inverse_kinematics(target_xyz, q_init=q_now)
        if not ok:
            print(f"[RealSO101] IK miss: |err|={err_m*100:.2f}cm — sending best effort")
        # ─── SAFETY 2: verify IK-predicted EE z also above floor ───
        for i, addr in enumerate(self.joint_qpos_addrs):
            self.data.qpos[addr] = q_target[i]
        mujoco.mj_forward(self.model, self.data)
        predicted_ee_z = float(self.data.site_xpos[self.site_id][2])
        if predicted_ee_z < self.ee_z_min - 0.02:  # 2cm buffer
            print(f"[RealSO101] SAFETY REJECT: IK-predicted EE z={predicted_ee_z:.3f} too low")
            return False
        # ─── SAFETY 3: stepwise joint-space interpolation with
        # per-step EE z check — bails out if path swings through table.
        step_dt = max(duration / steps, 0.2)
        for s in range(1, steps + 1):
            alpha = s / steps
            q_interp = (1 - alpha) * q_now + alpha * q_target
            for i, addr in enumerate(self.joint_qpos_addrs):
                self.data.qpos[addr] = q_interp[i]
            mujoco.mj_forward(self.model, self.data)
            interp_ee_z = float(self.data.site_xpos[self.site_id][2])
            if interp_ee_z < self.ee_z_min - 0.03:
                print(f"[RealSO101] SAFETY ABORT mid-motion step {s}/{steps}: predicted EE z={interp_ee_z:.3f}")
                return False
            action = {f"{jn}.pos": float(np.rad2deg(q_interp[i])) for i, jn in enumerate(ARM_JOINT_NAMES)}
            self.robot.send_action(action)
            time.sleep(step_dt)
        self._refresh_shadow_from_real()
        final_xyz, _ = self.get_ee_pose()
        final_err = float(np.linalg.norm(target_xyz - final_xyz))
        print(f"[RealSO101] move_cartesian done: target={target_xyz}, reached={final_xyz}, err={final_err*100:.2f}cm")
        return final_err < 2 * self.ik_tol

    def gripper_open(self) -> bool:
        self.robot.send_action({f"{GRIPPER_JOINT}.pos": GRIPPER_OPEN_NORM})
        time.sleep(0.8)
        return True

    def gripper_close(self) -> bool:
        self.robot.send_action({f"{GRIPPER_JOINT}.pos": GRIPPER_CLOSED_NORM})
        time.sleep(0.8)
        return True

    def is_holding(self) -> bool:
        """No tactile / weld sensors on real robot — best-effort heuristic."""
        # Without a tactile sensor we report based on gripper current/load.
        # For now, return based on commanded state vs measured.
        obs = self.robot.get_observation()
        # gripper.pos in range 0..100 (closed..open or vice versa post-cal)
        return obs.get(f"{GRIPPER_JOINT}.pos", 100.0) < 50.0

    def step(self, n: int = 1) -> None:
        """No-op on real robot (compat with sim's step)."""
        pass

    def get_object_position(self, body_name: str) -> np.ndarray:
        """Not available on real robot without external tracking.
        Raises NotImplementedError so caller can fall back to vision."""
        raise NotImplementedError(
            "Real robot has no privileged scene state. "
            "Use a vision tool (RealSense camera) to localise objects."
        )

    def describe(self) -> dict:
        return {
            "robot": "SO-ARM101 (real, LeRobot)",
            "n_joints": self.n_joints,
            "joint_names": ARM_JOINT_NAMES,
            "gripper": GRIPPER_JOINT,
        }


# ─── Smoke tests ────────────────────────────────────────────────────────

def _test_home_only(real: RealSO101):
    print("\n=== TEST: home_only ===")
    real.home()
    ee, _ = real.get_ee_pose()
    q = real.get_joint_positions()
    print(f"  EE: {ee}")
    print(f"  joints (deg): {np.rad2deg(q)}")


def _test_reach_x_plus_5(real: RealSO101):
    print("\n=== TEST: reach +5 cm in X ===")
    real.home()
    ee0, _ = real.get_ee_pose()
    target = ee0 + np.array([0.05, 0.0, 0.0])
    print(f"  start EE: {ee0}")
    print(f"  target  : {target}")
    real.move_cartesian(target, duration=2.5)
    time.sleep(0.5)
    ee1, _ = real.get_ee_pose()
    err = float(np.linalg.norm(target - ee1))
    print(f"  reached : {ee1}")
    print(f"  err     : {err*100:.2f} cm")
    real.home()


def _test_reach_z_plus_5(real: RealSO101):
    print("\n=== TEST: reach +5 cm in Z ===")
    real.home()
    ee0, _ = real.get_ee_pose()
    target = ee0 + np.array([0.0, 0.0, 0.05])
    real.move_cartesian(target, duration=2.5)
    time.sleep(0.5)
    ee1, _ = real.get_ee_pose()
    print(f"  err: {float(np.linalg.norm(target-ee1))*100:.2f} cm")
    real.home()


def _test_gripper_cycle(real: RealSO101):
    print("\n=== TEST: gripper open/close cycle ===")
    real.gripper_open()
    time.sleep(0.5)
    real.gripper_close()
    time.sleep(0.5)
    real.gripper_open()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mjcf", default=None,
                   help="Path to SO-101 MJCF (defaults to artifacts/auto_adapter_so101_v2_artifacts/mjcf.xml)")
    p.add_argument("--port", default="/dev/ttyACM0")
    p.add_argument("--robot-id", default="my_awesome_follower_arm")
    p.add_argument("--test", default="home_only",
                   choices=["home_only", "reach_x", "reach_z", "gripper_cycle", "all"])
    args = p.parse_args()

    if args.mjcf is None:
        repo_root = Path(__file__).resolve().parent.parent
        args.mjcf = str(repo_root / "artifacts/auto_adapter_so101_v2_artifacts/mjcf.xml")

    print(f"MJCF: {args.mjcf}")
    print(f"Port: {args.port}")
    print(f"Robot ID: {args.robot_id}")

    with RealSO101(mjcf_path=args.mjcf, port=args.port, robot_id=args.robot_id) as real:
        if args.test in ("home_only", "all"):
            _test_home_only(real)
        if args.test in ("reach_x", "all"):
            _test_reach_x_plus_5(real)
        if args.test in ("reach_z", "all"):
            _test_reach_z_plus_5(real)
        if args.test in ("gripper_cycle", "all"):
            _test_gripper_cycle(real)


if __name__ == "__main__":
    main()
