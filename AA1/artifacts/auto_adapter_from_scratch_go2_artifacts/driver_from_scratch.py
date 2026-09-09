#!/usr/bin/env python3
"""
Unitree Go2 Quadruped Driver - Built from Scratch
==================================================

A complete, single-file driver for the Unitree Go2 quadruped robot.
Implements joint-space PD control for standing, sitting, and walking gaits.

NO imports from auto_adapter.skeletons - pure mujoco + numpy implementation.

Author: Auto-generated Phase 2 GEN_ALGO
Robot: Unitree Go2 (12-DOF quadruped, 4 legs × 3 joints)
"""

import mujoco
import numpy as np
from typing import Optional, Tuple
import os


class Robot:
    """
    Unitree Go2 quadruped robot driver.
    
    Provides high-level locomotion primitives:
    - stand_up(): Move to standing pose with PD interpolation
    - sit(): Fold legs into sitting pose
    - walk_forward(): Simple diagonal-pair gait
    - get_body_height(), get_base_pose(): State queries
    
    Control uses joint-space PD: τ = kp*(q_des - q) + kd*(qd_des - qd)
    """
    
    # Joint ordering (matches actuator order in study.json)
    JOINT_NAMES = [
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
        "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"
    ]
    
    # Foot body names (for contact sensing)
    FOOT_NAMES = ["FR_foot", "FL_foot", "RR_foot", "RL_foot"]
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, mjcf_path: str):
        """
        Initialize the Go2 robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
            mjcf_path: Path to MJCF file (for description)
        """
        self.model = model
        self.data = data
        self.mjcf_path = mjcf_path
        self.renderer = None
        
        # Build index maps
        self._build_index_maps()
        
        # PD gains (tuned for stable standing and walking)
        self.kp = 100.0  # Position gain
        self.kd = 10.0   # Velocity gain
        
        # Standing pose (home configuration from keyframe)
        # [hip, thigh, calf] × 4 legs
        self.standing_pose = np.array([0.0, 0.9, -1.8] * 4)
        
        # Sitting pose (legs folded)
        # Front legs fold moderately, back legs fold more
        self.sitting_pose = np.array([
            0.0, 1.5, -2.5,  # FR
            0.0, 1.5, -2.5,  # FL
            0.0, 2.0, -2.5,  # RR
            0.0, 2.0, -2.5,  # RL
        ])
        
        # Initialize to standing pose
        self._reset_to_standing()
        
        print(f"✓ Go2 driver initialized: {len(self.JOINT_NAMES)} DOF")
    
    def _reset_to_standing(self):
        """Reset robot to standing pose with proper base height."""
        # Set base position (from home keyframe)
        self.data.qpos[0:3] = [0, 0, 0.27]  # xyz
        self.data.qpos[3:7] = [1, 0, 0, 0]  # quaternion (w,x,y,z)
        
        # Set joint positions to standing pose
        self.data.qpos[self.qpos_indices] = self.standing_pose
        
        # Zero velocities
        self.data.qvel[:] = 0
        
        # Forward kinematics
        mujoco.mj_forward(self.model, self.data)
    
    def _build_index_maps(self):
        """Build mappings from joint/actuator names to MuJoCo indices."""
        # Joint qpos indices (in actuator order)
        self.qpos_indices = []
        for name in self.JOINT_NAMES:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            qpos_adr = self.model.jnt_qposadr[jid]
            self.qpos_indices.append(qpos_adr)
        self.qpos_indices = np.array(self.qpos_indices, dtype=np.int32)
        
        # Actuator indices (should be 0-11 in order)
        self.actuator_indices = np.arange(12, dtype=np.int32)
        
        # Foot body indices
        self.foot_body_ids = []
        for name in self.FOOT_NAMES:
            bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            self.foot_body_ids.append(bid)
        
        # Base body
        self.base_body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        
        # Joint limits
        self.joint_limits = np.zeros((12, 2))
        for i, name in enumerate(self.JOINT_NAMES):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            self.joint_limits[i] = self.model.jnt_range[jid]
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Factory method: construct Robot from MJCF file path.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
            
        Raises:
            FileNotFoundError: If MJCF file doesn't exist
            ValueError: If model loading fails
        """
        # Resolve symlinks
        if os.path.islink(mjcf_path):
            mjcf_path = os.readlink(mjcf_path)
        
        if not os.path.exists(mjcf_path):
            raise FileNotFoundError(f"MJCF not found: {mjcf_path}")
        
        try:
            model = mujoco.MjModel.from_xml_path(mjcf_path)
            data = mujoco.MjData(model)
        except Exception as e:
            raise ValueError(f"Failed to load MJCF: {e}")
        
        return cls(model, data, mjcf_path)
    
    def home(self) -> bool:
        """
        Move robot to home (standing) pose using PD interpolation.
        
        Returns:
            True if successful, False otherwise
        """
        return self.stand_up(duration=2.0)
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions (12-DOF).
        
        Returns:
            Joint positions in actuator order [rad]
        """
        return self.data.qpos[self.qpos_indices].copy()
    
    def get_joint_velocities(self) -> np.ndarray:
        """
        Get current joint velocities (12-DOF).
        
        Returns:
            Joint velocities in actuator order [rad/s]
        """
        # Velocities start at index 6 (after 6-DOF freejoint)
        return self.data.qvel[6:18].copy()
    
    def step(self, n: int = 1):
        """
        Step the simulation forward by n steps.
        
        Args:
            n: Number of simulation steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """
        Render the current scene.
        
        Returns:
            RGB image array (H, W, 3) or None if rendering fails
        """
        try:
            if self.renderer is None:
                self.renderer = mujoco.Renderer(self.model, height=480, width=640)
            
            self.renderer.update_scene(self.data)
            return self.renderer.render()
        except Exception as e:
            print(f"Render failed: {e}")
            return None
    
    def describe(self) -> str:
        """
        Return a human-readable description of the robot.
        
        Returns:
            Multi-line description string
        """
        q = self.get_joint_positions()
        base_xyz, base_quat = self.get_base_pose()
        height = self.get_body_height()
        
        desc = f"""
╔══════════════════════════════════════════════════════════════╗
║                    Unitree Go2 Quadruped                     ║
╚══════════════════════════════════════════════════════════════╝

Robot Type:      Quadruped (4 legs, 12-DOF)
Control Mode:    Joint-space PD (torque)
MJCF Source:     {os.path.basename(self.mjcf_path)}

┌─ State ──────────────────────────────────────────────────────┐
│ Base Position:   [{base_xyz[0]:7.4f}, {base_xyz[1]:7.4f}, {base_xyz[2]:7.4f}] m
│ Base Height:     {height:7.4f} m
│ Base Orientation: [{base_quat[0]:6.3f}, {base_quat[1]:6.3f}, {base_quat[2]:6.3f}, {base_quat[3]:6.3f}]
└──────────────────────────────────────────────────────────────┘

┌─ Joint Positions (rad) ──────────────────────────────────────┐
│ FR: [{q[0]:6.3f}, {q[1]:6.3f}, {q[2]:6.3f}]  (hip, thigh, calf)
│ FL: [{q[3]:6.3f}, {q[4]:6.3f}, {q[5]:6.3f}]
│ RR: [{q[6]:6.3f}, {q[7]:6.3f}, {q[8]:6.3f}]
│ RL: [{q[9]:6.3f}, {q[10]:6.3f}, {q[11]:6.3f}]
└──────────────────────────────────────────────────────────────┘

┌─ Capabilities ───────────────────────────────────────────────┐
│ • stand_up(duration=2.0)      : Rise to standing pose
│ • sit(duration=1.5)           : Fold legs into sitting pose
│ • walk_forward(secs, speed)   : Diagonal-pair gait
│ • get_body_height()           : Query base height
│ • get_base_pose()             : Query base position/orientation
└──────────────────────────────────────────────────────────────┘

PD Gains: kp={self.kp}, kd={self.kd}
        """
        return desc
    
    # ==================== Locomotion Primitives ====================
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Move to standing pose using PD interpolation.
        
        Args:
            duration: Time to complete motion [seconds]
            
        Returns:
            True if motion completes successfully
        """
        return self._pd_interpolate(
            target_pose=self.standing_pose,
            duration=duration,
            kp=self.kp,
            kd=self.kd,
            description="Standing up"
        )
    
    def sit(self, duration: float = 1.5) -> bool:
        """
        Move to sitting pose (legs folded) using PD interpolation.
        
        Args:
            duration: Time to complete motion [seconds]
            
        Returns:
            True if motion completes successfully
        """
        return self._pd_interpolate(
            target_pose=self.sitting_pose,
            duration=duration,
            kp=self.kp,
            kd=self.kd,
            description="Sitting down"
        )
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using a simple diagonal-pair gait.
        
        The gait oscillates thigh joints sinusoidally:
        - FR + RL swing together (diagonal pair 1)
        - FL + RR swing together (diagonal pair 2, π phase shift)
        
        Args:
            secs: Duration to walk [seconds]
            speed: Gait frequency [Hz], higher = faster
            
        Returns:
            True if walk completes successfully
        """
        print(f"🚶 Walking forward for {secs:.1f}s at speed={speed:.2f} Hz...")
        
        # Compute number of steps
        dt = self.model.opt.timestep
        n_steps = int(secs / dt)
        
        # Swing amplitude (thigh joint oscillation)
        swing_amp = 0.4
        
        # Lower PD gains for smoother walking
        kp_walk = 80.0
        kd_walk = 8.0
        
        for step in range(n_steps):
            t = step * dt
            phase = 2 * np.pi * speed * t
            
            # Start from standing pose
            q_des = self.standing_pose.copy()
            qd_des = np.zeros(12)
            
            # Diagonal pair 1: FR (idx 1) + RL (idx 10)
            fr_swing = 0.5 * swing_amp * np.sin(phase)
            rl_swing = 0.5 * swing_amp * np.sin(phase)
            
            # Diagonal pair 2: FL (idx 4) + RR (idx 7)
            fl_swing = 0.5 * swing_amp * np.sin(phase + np.pi)
            rr_swing = 0.5 * swing_amp * np.sin(phase + np.pi)
            
            # Apply to thigh joints
            q_des[1] += fr_swing   # FR_thigh
            q_des[4] += fl_swing   # FL_thigh
            q_des[7] += rr_swing   # RR_thigh
            q_des[10] += rl_swing  # RL_thigh
            
            # Clamp to joint limits
            q_des = self._clamp_to_limits(q_des)
            
            # Compute and apply PD torque
            tau = self._pd_control(q_des, qd_des, kp_walk, kd_walk)
            self.data.ctrl[:] = tau
            
            mujoco.mj_step(self.model, self.data)
        
        print(f"✓ Walk complete. Final base position: {self.data.xpos[self.base_body_id]}")
        return True
    
    def get_body_height(self) -> float:
        """
        Get current height of the robot base (z-coordinate).
        
        Returns:
            Base height [meters]
        """
        return float(self.data.xpos[self.base_body_id][2])
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get current base link pose (position and orientation).
        
        Returns:
            Tuple of (xyz position [m], quaternion [w,x,y,z])
        """
        xyz = self.data.xpos[self.base_body_id].copy()
        quat = self.data.xquat[self.base_body_id].copy()
        return xyz, quat
    
    # ==================== Internal Helpers ====================
    
    def _pd_interpolate(
        self,
        target_pose: np.ndarray,
        duration: float,
        kp: float,
        kd: float,
        description: str = "Moving"
    ) -> bool:
        """
        Smoothly interpolate to target joint configuration using PD control.
        
        Args:
            target_pose: Target joint positions [12]
            duration: Time to complete motion [seconds]
            kp: Proportional gain
            kd: Derivative gain
            description: Motion description for logging
            
        Returns:
            True if successful
        """
        # Clamp target to joint limits
        target_pose = self._clamp_to_limits(target_pose)
        
        dt = self.model.opt.timestep
        n_steps = int(duration / dt)
        
        print(f"🤖 {description} (duration={duration:.1f}s, {n_steps} steps)...")
        
        # Track convergence
        last_error = float('inf')
        
        for step in range(n_steps):
            # Desired velocity is zero (pure position tracking)
            qd_des = np.zeros(12)
            
            # Compute and apply PD torque
            tau = self._pd_control(target_pose, qd_des, kp, kd)
            self.data.ctrl[:] = tau
            
            mujoco.mj_step(self.model, self.data)
            
            # Check convergence every 50 steps
            if step % 50 == 0:
                q_current = self.get_joint_positions()
                error = np.linalg.norm(q_current - target_pose)
                height = self.get_body_height()
                
                if step % 200 == 0:
                    print(f"  Step {step:4d}/{n_steps}: error={error:.4f} rad, height={height:.4f} m")
                
                last_error = error
        
        # Final check
        q_final = self.get_joint_positions()
        final_error = np.linalg.norm(q_final - target_pose)
        print(f"✓ {description} complete. Final error: {final_error:.4f} rad")
        
        return final_error < 0.2  # Success if within 0.2 rad (relaxed for dynamic system)
    
    def _pd_control(
        self,
        q_desired: np.ndarray,
        qd_desired: np.ndarray,
        kp: float,
        kd: float
    ) -> np.ndarray:
        """
        Compute PD control torques.
        
        Args:
            q_desired: Desired joint positions [12]
            qd_desired: Desired joint velocities [12]
            kp: Proportional gain
            kd: Derivative gain
            
        Returns:
            Control torques [12], clamped to actuator limits
        """
        q_current = self.get_joint_positions()
        qd_current = self.get_joint_velocities()
        
        # PD law: τ = kp*(q_des - q) + kd*(qd_des - qd)
        tau = kp * (q_desired - q_current) + kd * (qd_desired - qd_current)
        
        # Clamp to actuator torque limits
        for i in range(12):
            tau_min = self.model.actuator_ctrlrange[i][0]
            tau_max = self.model.actuator_ctrlrange[i][1]
            tau[i] = np.clip(tau[i], tau_min, tau_max)
        
        return tau
    
    def _clamp_to_limits(self, q: np.ndarray) -> np.ndarray:
        """
        Clamp joint positions to joint limits.
        
        Args:
            q: Joint positions [12]
            
        Returns:
            Clamped joint positions [12]
        """
        return np.clip(q, self.joint_limits[:, 0], self.joint_limits[:, 1])


# ==================== Demo / Smoke Test ====================

if __name__ == "__main__":
    print("=" * 70)
    print("Unitree Go2 Driver - Smoke Test")
    print("=" * 70)
    
    # Build robot - try to find the actual MJCF file
    mjcf_path = "mjcf.xml"
    if os.path.islink(mjcf_path):
        mjcf_path = os.readlink(mjcf_path)
    
    if not os.path.exists(mjcf_path):
        # Try absolute path
        mjcf_path = "assets/mjcf/go2/go2_scene.xml"
    
    robot = Robot.build_from_mjcf(mjcf_path)
    
    # Print description
    print(robot.describe())
    
    # Test home
    print("\n" + "=" * 70)
    print("TEST 1: Home (Stand Up)")
    print("=" * 70)
    success = robot.home()
    print(f"Home success: {success}")
    print(f"Joint positions: {robot.get_joint_positions()}")
    
    # Test sit
    print("\n" + "=" * 70)
    print("TEST 2: Sit")
    print("=" * 70)
    success = robot.sit(duration=1.5)
    print(f"Sit success: {success}")
    print(f"Body height: {robot.get_body_height():.4f} m")
    
    # Test stand again
    print("\n" + "=" * 70)
    print("TEST 3: Stand Up Again")
    print("=" * 70)
    success = robot.stand_up(duration=2.0)
    print(f"Stand success: {success}")
    print(f"Body height: {robot.get_body_height():.4f} m")
    
    # Test walk
    print("\n" + "=" * 70)
    print("TEST 4: Walk Forward")
    print("=" * 70)
    base_start = robot.get_base_pose()[0]
    print(f"Starting position: {base_start}")
    
    success = robot.walk_forward(secs=2.0, speed=0.3)
    print(f"Walk success: {success}")
    
    base_end = robot.get_base_pose()[0]
    print(f"Ending position: {base_end}")
    print(f"Distance traveled: {np.linalg.norm(base_end[:2] - base_start[:2]):.4f} m")
    
    print("\n" + "=" * 70)
    print("✓ All smoke tests complete!")
    print("=" * 70)
