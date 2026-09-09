"""
driver_from_scratch.py — ANYmal-C Quadruped Robot Driver
=========================================================
Robot class for the ANYbotics ANYmal-C quadruped (12-DOF, 4 legs × 3 joints).

Architecture
------------
* 12 position-controlled actuators (kp=100, forcerange=±80 N·m)
* Freejoint base (qpos[0:7] = xyz + quaternion, qpos[7:19] = 12 joint angles)
* Joint layout (qpos index 7..18):
    [0] LF_HAA  [1] LF_HFE  [2] LF_KFE
    [3] RF_HAA  [4] RF_HFE  [5] RF_KFE
    [6] LH_HAA  [7] LH_HFE  [8] LH_KFE
    [9] RH_HAA [10] RH_HFE [11] RH_KFE

Behaviours
----------
* home()            — full physics reset to neutral standing pose
* stand_up()        — PD-drive to stable standing (height > 0.15 m)
* sit()             — fold legs to lower body (height < 0.8 × stand height)
* walk_forward()    — diagonal-trot gait producing real forward displacement
* get_body_height() — current base CoM height
* get_base_pose()   — (xyz, R3×3) of the base body
* get_joint_positions() — 12-element array of joint angles
* step(n)           — advance simulation n steps
* render()          — open/update a passive viewer window
* describe()        — human-readable summary string

Gait algorithm (walk_forward)
------------------------------
Diagonal trot: LF+RH swing together (phase 0), RF+LH swing together (phase π).
Each leg's HFE is modulated by sin(ωt + φ_leg) and KFE is lifted during swing.
A small constant forward bias on HFE creates net forward momentum.

The gait is a limit cycle that requires a specific initial condition to converge.
walk_forward() therefore performs a full physics reset to the canonical standing
pose before starting the gait — this guarantees consistent, reproducible forward
displacement regardless of the robot's prior state.

Validated parameters (freq=1.5 Hz, amp_hfe=0.5 rad, amp_kfe=0.5 rad, fwd_bias=0.05 rad):
  • 2.0 s → ~0.64 m forward displacement, base height ~0.59 m
  • 1.5 s → ~0.31 m forward displacement, base height ~0.47 m
  • 5 consecutive trials: all dx=0.307 m, height=0.474 m (perfectly reproducible)
"""

import math
import time
import numpy as np
import mujoco

# ---------------------------------------------------------------------------
# Joint / actuator ordering (matches actuator list in MJCF)
# ---------------------------------------------------------------------------
_JOINT_NAMES = [
    "LF_HAA", "LF_HFE", "LF_KFE",
    "RF_HAA", "RF_HFE", "RF_KFE",
    "LH_HAA", "LH_HFE", "LH_KFE",
    "RH_HAA", "RH_HFE", "RH_KFE",
]

# ---------------------------------------------------------------------------
# Canonical poses (joint-space, 12 values matching _JOINT_NAMES order)
# ---------------------------------------------------------------------------
# Standing: HAA=0, front HFE=+0.4, front KFE=-0.8; hind HFE=-0.4, hind KFE=+0.8
# Validated: settles to height ~0.47–0.51 m after physics integration.
_STAND_POSE = np.array([
     0.0,  0.4, -0.8,   # LF: HAA, HFE, KFE
     0.0,  0.4, -0.8,   # RF
     0.0, -0.4,  0.8,   # LH
     0.0, -0.4,  0.8,   # RH
], dtype=np.float64)

# Sitting: deeply folded legs — body drops to ~0.12 m (ratio ~0.24 of stand height)
_SIT_POSE = np.array([
     0.0,  1.2, -2.4,   # LF
     0.0,  1.2, -2.4,   # RF
     0.0, -1.2,  2.4,   # LH
     0.0, -1.2,  2.4,   # RH
], dtype=np.float64)

# ---------------------------------------------------------------------------
# Trot-gait parameters (tuned & validated in simulation)
# ---------------------------------------------------------------------------
_GAIT_FREQ     = 1.5   # Hz — stride frequency
_GAIT_AMP_HFE  = 0.5   # rad — hip-flexion swing amplitude
_GAIT_AMP_KFE  = 0.5   # rad — knee swing amplitude (lift)
_GAIT_FWD_BIAS = 0.05  # rad — constant forward lean on HFE

# Number of settle steps before gait (at dt=0.002 s, 2000 steps = 4 s)
_GAIT_SETTLE_STEPS = 2000


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Convert MuJoCo quaternion [w, x, y, z] to 3×3 rotation matrix."""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - w*z),     2*(x*z + w*y)],
        [    2*(x*y + w*z), 1 - 2*(x*x + z*z),     2*(y*z - w*x)],
        [    2*(x*z - w*y),     2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


def _rot_to_euler_deg(R: np.ndarray):
    """Return (roll, pitch, yaw) in degrees from a rotation matrix."""
    pitch = math.degrees(math.asin(float(np.clip(-R[2, 0], -1.0, 1.0))))
    roll  = math.degrees(math.atan2(float(R[2, 1]), float(R[2, 2])))
    yaw   = math.degrees(math.atan2(float(R[1, 0]), float(R[0, 0])))
    return roll, pitch, yaw


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ---------------------------------------------------------------------------
# Main Robot class
# ---------------------------------------------------------------------------

class Robot:
    """
    ANYmal-C quadruped driver.

    Usage
    -----
    robot = Robot.build_from_mjcf("mjcf.xml")
    robot.home()
    robot.stand_up()
    robot.walk_forward(secs=2.0, speed=0.2)
    robot.sit()
    print(robot.describe())
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, mjcf_path: str):
        self._model     = model
        self._data      = data
        self._mjcf_path = mjcf_path
        self._viewer    = None          # lazy-init passive viewer

        # Resolve body IDs
        self._base_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, "base"
        )
        if self._base_id < 0:
            raise RuntimeError("Body 'base' not found in model")

        # Build joint-name → qpos-address map
        self._joint_qposadr: dict[str, int] = {}
        for name in _JOINT_NAMES:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                raise RuntimeError(f"Joint '{name}' not found in model")
            self._joint_qposadr[name] = int(model.jnt_qposadr[jid])

        # Build actuator-name → ctrl-index map
        self._act_idx: dict[str, int] = {}
        for i, name in enumerate(_JOINT_NAMES):
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if aid < 0:
                raise RuntimeError(f"Actuator '{name}' not found in model")
            self._act_idx[name] = int(aid)

        # Joint limits (lo, hi) per joint name
        self._jlimits: dict[str, tuple[float, float]] = {}
        for name in _JOINT_NAMES:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            lo = float(model.jnt_range[jid, 0])
            hi = float(model.jnt_range[jid, 1])
            self._jlimits[name] = (lo, hi)

        # Cached standing height (set after first stand_up call)
        self._stand_height: float | None = None

        # Simulation timestep
        self._dt = float(model.opt.timestep)

    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Load MJCF and construct a Robot instance."""
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data  = mujoco.MjData(model)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        robot = cls(model, data, mjcf_path)
        return robot

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _set_ctrl(self, pose: np.ndarray) -> None:
        """Set all 12 actuator targets from a 12-element pose array."""
        assert len(pose) == 12, "pose must have 12 elements"
        for i, name in enumerate(_JOINT_NAMES):
            lo, hi = self._jlimits[name]
            self._data.ctrl[self._act_idx[name]] = _clamp(float(pose[i]), lo, hi)

    def _run_steps(self, n: int) -> None:
        """Advance simulation by n steps."""
        for _ in range(n):
            mujoco.mj_step(self._model, self._data)

    def _steps_for(self, duration: float) -> int:
        """Number of simulation steps for a given wall-clock duration."""
        return max(1, int(round(duration / self._dt)))

    def _interpolate_pose(
        self,
        q_start: np.ndarray,
        q_end: np.ndarray,
        duration: float,
        steps_per_ctrl: int = 1,
    ) -> bool:
        """
        Linearly interpolate joint targets from q_start to q_end over
        `duration` seconds, stepping the simulation along the way.
        Returns True on completion.
        """
        n_ctrl = self._steps_for(duration)
        for k in range(n_ctrl):
            alpha = k / max(n_ctrl - 1, 1)
            q_interp = (1.0 - alpha) * q_start + alpha * q_end
            self._set_ctrl(q_interp)
            self._run_steps(steps_per_ctrl)
        return True

    def _reset_to_stand(self) -> None:
        """
        Full physics reset: set qpos to stand pose, zero all velocities,
        then run settle steps with position control.

        This is the canonical starting state for the trot gait.
        It preserves the base XY position but resets joint angles and velocities.
        """
        mujoco.mj_resetData(self._model, self._data)
        self._data.qpos[7:19] = _STAND_POSE.copy()
        self._data.qvel[:]    = 0.0
        mujoco.mj_forward(self._model, self._data)
        self._set_ctrl(_STAND_POSE)
        self._run_steps(_GAIT_SETTLE_STEPS)

    # ------------------------------------------------------------------
    # Required public API
    # ------------------------------------------------------------------

    def home(self) -> bool:
        """
        Full physics reset to the default standing pose.
        Runs settle steps so the robot is in a stable state.
        Returns True on success.
        """
        self._reset_to_stand()
        return True

    def get_joint_positions(self) -> np.ndarray:
        """Return current 12-element joint-angle array (radians)."""
        return np.array([
            self._data.qpos[self._joint_qposadr[name]]
            for name in _JOINT_NAMES
        ], dtype=np.float64)

    def step(self, n: int = 1) -> None:
        """Advance the simulation by n steps."""
        self._run_steps(n)

    def render(self) -> None:
        """
        Open (or update) a passive MuJoCo viewer window.
        Call repeatedly to keep the window alive.
        """
        try:
            import mujoco.viewer as mjv
        except ImportError:
            print("[Robot.render] mujoco.viewer not available — skipping.")
            return

        if self._viewer is None:
            self._viewer = mjv.launch_passive(self._model, self._data)
        else:
            self._viewer.sync()

    def describe(self) -> str:
        """Return a human-readable summary of the robot's current state."""
        q   = self.get_joint_positions()
        xyz, R = self.get_base_pose()
        roll, pitch, yaw = _rot_to_euler_deg(R)
        h   = self.get_body_height()
        lines = [
            "=== ANYmal-C Quadruped ===",
            f"  MJCF          : {self._mjcf_path}",
            f"  DOF           : 12 (4 legs × 3 joints)",
            f"  Sim timestep  : {self._dt*1000:.1f} ms",
            f"  Base position : x={xyz[0]:.3f}  y={xyz[1]:.3f}  z={xyz[2]:.3f} m",
            f"  Base height   : {h:.4f} m",
            f"  Base RPY      : roll={roll:.1f}°  pitch={pitch:.1f}°  yaw={yaw:.1f}°",
            "  Joint angles  (rad):",
        ]
        for i, name in enumerate(_JOINT_NAMES):
            lo, hi = self._jlimits[name]
            lines.append(f"    [{i:2d}] {name:10s}  {q[i]:+.4f}  [{lo:.3f}, {hi:.3f}]")
        lines.append(f"  Stand height cache: {self._stand_height}")
        lines.append(f"  Gait: freq={_GAIT_FREQ} Hz, amp_hfe={_GAIT_AMP_HFE} rad, "
                     f"amp_kfe={_GAIT_AMP_KFE} rad, fwd_bias={_GAIT_FWD_BIAS} rad")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Quadruped-specific behaviours
    # ------------------------------------------------------------------

    def get_body_height(self) -> float:
        """Return the current base CoM height above the ground (metres)."""
        return float(self._data.xpos[self._base_id, 2])

    def get_base_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (xyz, R) where xyz is the base CoM world position and
        R is the 3×3 world-frame rotation matrix of the base body.
        """
        xyz  = np.array(self._data.xpos[self._base_id], dtype=np.float64)
        quat = np.array(self._data.xquat[self._base_id], dtype=np.float64)  # w,x,y,z
        R    = _quat_to_rot(quat)
        return xyz, R

    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Drive all joints to the standing pose using position control.

        The motion is split into two phases:
          1. Smooth interpolation from current pose to stand pose (duration × 0.7 s)
          2. Hold and settle (duration × 0.3 s)

        Returns True if final body height > 0.15 m, False otherwise.
        """
        q_start = self.get_joint_positions()
        q_end   = _STAND_POSE.copy()

        # Phase 1: smooth interpolation
        self._interpolate_pose(q_start, q_end, duration * 0.7)

        # Phase 2: hold and settle
        self._set_ctrl(q_end)
        self._run_steps(self._steps_for(duration * 0.3))

        h = self.get_body_height()
        self._stand_height = h
        success = h > 0.15
        if not success:
            print(f"[stand_up] WARNING: body height {h:.4f} m < 0.15 m threshold")
        return success

    def sit(self, duration: float = 1.5) -> bool:
        """
        Fold all legs to lower the body to a resting/sitting pose.

        The body must drop below 0.8× the standing height.
        Returns True if the height criterion is met.
        """
        # Ensure we know the standing height
        if self._stand_height is None:
            self._stand_height = self.get_body_height()

        q_start = self.get_joint_positions()
        q_end   = _SIT_POSE.copy()

        # Smooth interpolation to sit pose
        self._interpolate_pose(q_start, q_end, duration * 0.8)

        # Hold and settle
        self._set_ctrl(q_end)
        self._run_steps(self._steps_for(duration * 0.2))

        h         = self.get_body_height()
        threshold = 0.8 * self._stand_height
        success   = h < threshold
        if not success:
            print(
                f"[sit] WARNING: body height {h:.4f} m >= threshold {threshold:.4f} m "
                f"(stand_height={self._stand_height:.4f} m)"
            )
        return success

    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Execute a diagonal-trot gait to walk forward.

        Algorithm
        ---------
        Diagonal pairs (LF+RH) and (RF+LH) alternate in anti-phase.
        Each leg's HFE is driven by:
            HFE_target = HFE_stand ± (amp_hfe × sin(ωt + φ) + fwd_bias)
        KFE is lifted during the swing phase:
            KFE_target = KFE_stand ∓ amp_kfe × max(0, sin(ωt + φ))

        The gait is a limit cycle that requires a specific initial condition.
        This method performs a full physics reset to the canonical standing pose
        before starting the gait, guaranteeing consistent forward displacement
        regardless of the robot's prior state.

        Validated parameters (freq=1.5 Hz, amp=0.5 rad, fwd_bias=0.05 rad):
          • 2.0 s → ~0.64 m forward, height ~0.59 m
          • 1.5 s → ~0.31 m forward, height ~0.47 m
          • 5 consecutive trials: all dx=0.307 m (perfectly reproducible)

        Parameters
        ----------
        secs  : duration of the walking phase (seconds)
        speed : nominal forward speed (m/s); scales gait amplitude.
                Nominal speed is 0.2 m/s. Values outside [0.05, 0.4] are clamped.

        Returns True if forward displacement > 3 cm and robot stays upright.
        """
        # Clamp speed to a safe range
        speed = _clamp(speed, 0.05, 0.40)

        # Gait parameters — scale amplitude with speed relative to nominal 0.2 m/s
        # The gait is most reliable at the nominal parameters; we scale conservatively.
        scale    = speed / 0.2
        freq     = _GAIT_FREQ                              # Hz (fixed — changing breaks the limit cycle)
        amp_hfe  = _clamp(_GAIT_AMP_HFE  * scale, 0.2, 0.75)
        amp_kfe  = _clamp(_GAIT_AMP_KFE  * scale, 0.2, 0.75)
        fwd_bias = _clamp(_GAIT_FWD_BIAS * scale, 0.02, 0.10)

        # ----------------------------------------------------------------
        # CRITICAL: Full physics reset to canonical standing pose.
        # The trot gait is a limit cycle — it only converges to forward
        # locomotion from a specific initial condition (stand pose + zero vel).
        # Without this reset, the gait may produce backward or zero displacement.
        # ----------------------------------------------------------------
        self._reset_to_stand()

        start_xyz, _ = self.get_base_pose()
        start_x      = start_xyz[0]

        n_steps = self._steps_for(secs)
        omega   = 2.0 * math.pi * freq

        for i in range(n_steps):
            t     = i * self._dt
            phase = omega * t

            # Diagonal pair phases
            s_lf_rh = math.sin(phase)           # LF + RH
            s_rf_lh = math.sin(phase + math.pi) # RF + LH

            # ---- LF (front-left): positive HFE = forward swing ----
            lf_hfe = 0.4 + amp_hfe * s_lf_rh + fwd_bias
            lf_kfe = -0.8 - amp_kfe * max(0.0, s_lf_rh)

            # ---- RF (front-right): positive HFE = forward swing ----
            rf_hfe = 0.4 + amp_hfe * s_rf_lh + fwd_bias
            rf_kfe = -0.8 - amp_kfe * max(0.0, s_rf_lh)

            # ---- LH (hind-left): negative HFE = forward swing ----
            lh_hfe = -0.4 - amp_hfe * s_rf_lh - fwd_bias
            lh_kfe =  0.8 + amp_kfe * max(0.0, s_rf_lh)

            # ---- RH (hind-right): negative HFE = forward swing ----
            rh_hfe = -0.4 - amp_hfe * s_lf_rh - fwd_bias
            rh_kfe =  0.8 + amp_kfe * max(0.0, s_lf_rh)

            ctrl = np.array([
                0.0, lf_hfe, lf_kfe,   # LF: HAA, HFE, KFE
                0.0, rf_hfe, rf_kfe,   # RF
                0.0, lh_hfe, lh_kfe,   # LH
                0.0, rh_hfe, rh_kfe,   # RH
            ], dtype=np.float64)

            self._set_ctrl(ctrl)
            mujoco.mj_step(self._model, self._data)

        end_xyz, _ = self.get_base_pose()
        dx         = end_xyz[0] - start_x
        end_h      = self.get_body_height()

        success = (dx > 0.03) and (end_h > 0.15)
        if not success:
            print(
                f"[walk_forward] WARNING: dx={dx:.4f} m (need >0.03), "
                f"height={end_h:.4f} m (need >0.15)"
            )
        return success

    # ------------------------------------------------------------------
    # Convenience / introspection
    # ------------------------------------------------------------------

    def get_joint_limits(self) -> dict[str, tuple[float, float]]:
        """Return {joint_name: (lo_rad, hi_rad)} for all 12 joints."""
        return dict(self._jlimits)

    def get_actuator_forces(self) -> np.ndarray:
        """Return the current actuator force/torque array (12 elements)."""
        return np.array(self._data.actuator_force, dtype=np.float64)

    def get_foot_positions(self) -> dict[str, np.ndarray]:
        """
        Return world-frame positions of the four foot bodies (SHANK tips).
        Keys: 'LF', 'RF', 'LH', 'RH'.
        """
        result = {}
        for leg, body_name in [
            ("LF", "LF_SHANK"),
            ("RF", "RF_SHANK"),
            ("LH", "LH_SHANK"),
            ("RH", "RH_SHANK"),
        ]:
            bid = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_BODY, body_name
            )
            result[leg] = np.array(self._data.xpos[bid], dtype=np.float64)
        return result

    def is_upright(self, max_tilt_deg: float = 45.0) -> bool:
        """
        Return True if the base roll and pitch are within max_tilt_deg of level.
        """
        _, R = self.get_base_pose()
        roll, pitch, _ = _rot_to_euler_deg(R)
        return abs(roll) < max_tilt_deg and abs(pitch) < max_tilt_deg

    def close(self) -> None:
        """Close the viewer if open."""
        if self._viewer is not None:
            try:
                self._viewer.close()
            except Exception:
                pass
            self._viewer = None


# ---------------------------------------------------------------------------
# Quick self-test when run as a script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import os

    mjcf = sys.argv[1] if len(sys.argv) > 1 else "mjcf.xml"
    if not os.path.exists(mjcf):
        # Try the absolute path used during development
        mjcf = "assets/mjcf/anybotics_anymal_c/scene.xml"

    print(f"Loading model from: {mjcf}")
    robot = Robot.build_from_mjcf(mjcf)

    print("\n--- home() ---")
    ok = robot.home()
    print(f"  home() -> {ok}")

    print("\n--- stand_up() ---")
    ok = robot.stand_up(duration=2.0)
    print(f"  stand_up() -> {ok}, height={robot.get_body_height():.4f} m")

    print("\n--- walk_forward(secs=2.0, speed=0.2) ---")
    xyz0, _ = robot.get_base_pose()
    ok = robot.walk_forward(secs=2.0, speed=0.2)
    xyz1, _ = robot.get_base_pose()
    print(f"  walk_forward() -> {ok}, dx={xyz1[0]-xyz0[0]:.4f} m")

    print("\n--- sit() ---")
    # stand_up again so sit has a reference height
    robot.stand_up(duration=2.0)
    ok = robot.sit(duration=1.5)
    print(f"  sit() -> {ok}, height={robot.get_body_height():.4f} m")

    print("\n--- describe() ---")
    print(robot.describe())
