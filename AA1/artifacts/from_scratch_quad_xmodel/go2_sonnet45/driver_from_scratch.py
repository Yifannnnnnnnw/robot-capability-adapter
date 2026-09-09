"""
Unitree Go2 Quadruped Driver - From Scratch Implementation
===========================================================

This driver implements PD control for a 12-DOF quadruped robot with torque actuators.
The robot has 4 legs (FR, FL, RR, RL), each with 3 joints: hip abduction, thigh, calf.

Key behaviors:
- stand_up(): PD control to stable standing pose (height > 0.15m)
- sit(): PD control to folded sitting pose (height drops significantly)
- walk_forward(): Coordinated gait with all legs moving in phase

No IK is needed for quadrupeds - we use joint-space PD control with carefully
tuned target poses and gaits.

IMPORTANT: The joint order in the MJCF does not match the logical order.
We use qpos/qvel address mapping to correctly access joint values.
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import os


class Robot:
    """Unitree Go2 quadruped robot driver with PD control."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self.viewer = None
        
        # Joint configuration (logical order: FR, FL, RR, RL)
        self.joint_names = [
            "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
            "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
            "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
            "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"
        ]
        
        # Joint limits
        self.joint_limits = {
            "FR_hip_joint": (-1.0472, 1.0472),
            "FR_thigh_joint": (-1.5708, 3.4907),
            "FR_calf_joint": (-2.7227, -0.83776),
            "FL_hip_joint": (-1.0472, 1.0472),
            "FL_thigh_joint": (-1.5708, 3.4907),
            "FL_calf_joint": (-2.7227, -0.83776),
            "RR_hip_joint": (-1.0472, 1.0472),
            "RR_thigh_joint": (-0.5236, 4.5379),
            "RR_calf_joint": (-2.7227, -0.83776),
            "RL_hip_joint": (-1.0472, 1.0472),
            "RL_thigh_joint": (-0.5236, 4.5379),
            "RL_calf_joint": (-2.7227, -0.83776),
        }
        
        # Resolve joint indices
        self.joint_ids = []
        for name in self.joint_names:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                raise ValueError(f"Joint '{name}' not found in model")
            self.joint_ids.append(jid)
        
        # Get qpos/qvel addresses for each joint (handles non-sequential ordering)
        self.qpos_addrs = [model.jnt_qposadr[jid] for jid in self.joint_ids]
        self.qvel_addrs = [model.jnt_dofadr[jid] for jid in self.joint_ids]
        
        # Find base body
        self.base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        if self.base_id < 0:
            raise ValueError("Base body 'base_link' not found")
        
        # PD gains - tuned for stability
        self.kp = 280.0  # Position gain
        self.kd = 28.0   # Velocity gain
        
        # Key poses (in logical joint order: FR, FL, RR, RL)
        # Home pose: from keyframe, stable at ~0.28m height
        self.home_pose = np.array([0, 0.9, -1.8, 0, 0.9, -1.8, 
                                   0, 0.9, -1.8, 0, 0.9, -1.8])
        
        # Standing pose: slightly more extended, ~0.37m height
        self.stand_pose = np.array([0, 0.6, -1.2, 0, 0.6, -1.2,
                                    0, 0.6, -1.2, 0, 0.6, -1.2])
        
        # Sitting pose: folded legs, ~0.14m height (< 0.8 * stand height)
        self.sit_pose = np.array([0, 1.5, -2.5, 0, 1.5, -2.5,
                                  0, 1.5, -2.5, 0, 1.5, -2.5])
        
        # Initialize to home pose
        self._initialize()
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file."""
        # Handle relative paths and includes
        if os.path.islink(mjcf_path):
            mjcf_path = os.readlink(mjcf_path)
        
        # Change to the directory containing the MJCF for includes to work
        original_dir = os.getcwd()
        mjcf_dir = os.path.dirname(os.path.abspath(mjcf_path))
        mjcf_file = os.path.basename(mjcf_path)
        
        try:
            os.chdir(mjcf_dir)
            model = mujoco.MjModel.from_xml_path(mjcf_file)
        finally:
            os.chdir(original_dir)
        
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def _initialize(self):
        """Initialize robot to home pose and stabilize."""
        # Reset simulation
        mujoco.mj_resetData(self.model, self.data)
        
        # Set joint positions to home pose using correct addresses
        for i, addr in enumerate(self.qpos_addrs):
            self.data.qpos[addr] = self.home_pose[i]
        
        # Forward kinematics
        mujoco.mj_forward(self.model, self.data)
        
        # Stabilize with PD control
        for _ in range(1000):
            q = self._get_joint_positions_internal()
            qd = self._get_joint_velocities_internal()
            tau = self.kp * (self.home_pose - q) - self.kd * qd
            self.data.ctrl[:12] = tau
            mujoco.mj_step(self.model, self.data)
    
    def home(self) -> bool:
        """Move to home pose and stabilize."""
        return self._move_to_pose(self.home_pose, duration=2.0)
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Stand up to a stable standing pose.
        
        Target: Body height > 0.15m after stabilization.
        Uses PD control to reach and maintain standing configuration.
        """
        return self._move_to_pose(self.stand_pose, duration=duration)
    
    def sit(self, duration: float = 1.5) -> bool:
        """
        Sit down by folding legs.
        
        Target: Body height drops below 0.8x standing height.
        The sitting pose folds hips and knees to lower the body significantly.
        With thigh=1.5 and calf=-2.5, the body lowers to ~0.14m.
        """
        return self._move_to_pose(self.sit_pose, duration=duration)
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using a coordinated gait.
        
        This implements a rowing-style gait where all legs move in phase:
        - Thigh and calf joints oscillate with a phase relationship
        - The coordinated motion produces forward displacement
        - Frequency and amplitude are tuned for stability and forward progress
        - Phase offset is NEGATIVE to produce forward motion
        
        Args:
            secs: Duration to walk (seconds)
            speed: Speed parameter (affects frequency, 0.1-0.5 recommended)
        
        Returns:
            True if walk completed successfully
        """
        # First ensure we're in standing pose
        self._move_to_pose(self.stand_pose, duration=0.5)
        
        # Gait parameters - tuned for forward motion
        base_freq = 2.0
        freq = base_freq * (speed / 0.2)  # Scale with speed
        
        # Amplitudes - larger values produce more forward displacement
        # Tuned to achieve >3cm forward motion in ~1.5s
        thigh_amp = 0.35
        calf_amp = 0.5
        
        # Phase relationship: -90 deg between thigh and calf
        # NEGATIVE phase offset produces FORWARD motion
        phase_offset = -np.pi / 2
        
        steps = int(secs / self.model.opt.timestep)
        dt = self.model.opt.timestep
        
        for i in range(steps):
            t = i * dt
            phase = 2 * np.pi * freq * t
            
            # Oscillating offsets
            thigh_offset = thigh_amp * np.sin(phase)
            calf_offset = calf_amp * np.sin(phase + phase_offset)
            
            # Apply to all legs (all in phase for stability)
            q_target = self.stand_pose.copy()
            for leg in range(4):
                q_target[leg * 3 + 1] = self.stand_pose[leg * 3 + 1] + thigh_offset
                q_target[leg * 3 + 2] = self.stand_pose[leg * 3 + 2] + calf_offset
            
            # Clamp to limits
            q_target = self._clamp_to_limits(q_target)
            
            # PD control
            q = self._get_joint_positions_internal()
            qd = self._get_joint_velocities_internal()
            tau = self.kp * (q_target - q) - self.kd * qd
            
            self.data.ctrl[:12] = tau
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def get_body_height(self) -> float:
        """Get current height of the base body above ground."""
        return self.data.xpos[self.base_id][2]
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get base position and orientation.
        
        Returns:
            (xyz, R): Position (3,) and rotation matrix (3, 3)
        """
        xyz = self.data.xpos[self.base_id].copy()
        
        # Get quaternion and convert to rotation matrix
        quat = self.data.xquat[self.base_id].copy()  # [w, x, y, z]
        R = np.zeros((3, 3))
        mujoco.mju_quat2Mat(R.ravel(), quat)
        
        return xyz, R
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions (in logical order)."""
        return self._get_joint_positions_internal()
    
    def step(self, n: int = 1):
        """Step the simulation forward."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self):
        """Render the current state (creates viewer if needed)."""
        if self.viewer is None:
            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        self.viewer.sync()
    
    def describe(self) -> str:
        """Return a description of the robot."""
        q = self.get_joint_positions()
        height = self.get_body_height()
        xyz, R = self.get_base_pose()
        
        desc = [
            "Unitree Go2 Quadruped Robot",
            "=" * 40,
            f"DOF: 12 (4 legs × 3 joints)",
            f"Actuators: Torque-controlled motors",
            f"",
            f"Current State:",
            f"  Base height: {height:.4f} m",
            f"  Base position: [{xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}]",
            f"  Joint positions: {q}",
            f"",
            f"PD Gains: kp={self.kp}, kd={self.kd}",
            f"",
            f"Available behaviors:",
            f"  - stand_up(): Stand to stable pose (height > 0.15m)",
            f"  - sit(): Fold legs to sitting pose (height drops to ~0.14m)",
            f"  - walk_forward(secs, speed): Coordinated walking gait",
        ]
        
        return "\n".join(desc)
    
    # ========== Internal helpers ==========
    
    def _get_joint_positions_internal(self) -> np.ndarray:
        """Get joint positions using correct qpos addresses."""
        return np.array([self.data.qpos[addr] for addr in self.qpos_addrs])
    
    def _get_joint_velocities_internal(self) -> np.ndarray:
        """Get joint velocities using correct qvel addresses."""
        return np.array([self.data.qvel[addr] for addr in self.qvel_addrs])
    
    def _move_to_pose(self, target_pose: np.ndarray, duration: float) -> bool:
        """
        Move to target pose using PD control.
        
        Args:
            target_pose: Target joint positions (12,) in logical order
            duration: Time to reach pose (seconds)
        
        Returns:
            True if successful
        """
        target_pose = self._clamp_to_limits(target_pose)
        
        steps = int(duration / self.model.opt.timestep)
        
        for _ in range(steps):
            q = self._get_joint_positions_internal()
            qd = self._get_joint_velocities_internal()
            
            # PD control
            tau = self.kp * (target_pose - q) - self.kd * qd
            
            self.data.ctrl[:12] = tau
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def _clamp_to_limits(self, q: np.ndarray) -> np.ndarray:
        """Clamp joint positions to limits."""
        q_clamped = q.copy()
        for i, name in enumerate(self.joint_names):
            lo, hi = self.joint_limits[name]
            q_clamped[i] = np.clip(q[i], lo, hi)
        return q_clamped
