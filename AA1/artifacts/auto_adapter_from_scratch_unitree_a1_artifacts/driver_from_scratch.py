"""
Unitree A1 Quadruped Robot Driver - Written from scratch.

This driver implements a complete control interface for the Unitree A1 quadruped
without using auto_adapter.skeletons. The robot has 12 DOF (4 legs × 3 joints).

Architecture:
  - Free-floating base (7 DOF: 3 pos + 4 quat)
  - 12 leg joints: FR, FL, RR, RL × (hip abduction, thigh, calf)
  - Position-controlled actuators with built-in PD control (kp=100, kd=100)

Control strategy:
  - Joint-space position control for standing/sitting behaviors
  - Diagonal-pair trot gait for walking (FR+RL vs FL+RR)
  - Direct position commands to actuators (model has built-in PD)
  
Quadruped-specific notes:
  - NO IK needed - this is a locomotion robot, not a manipulation arm
  - Gravity causes settling, so convergence tolerance is relaxed
  - Walking gait uses sinusoidal leg swing patterns
"""

import mujoco
import numpy as np
from typing import Optional, Tuple
import time


class Robot:
    """Unitree A1 quadruped robot driver with standing, sitting, and walking gaits."""
    
    # Joint ordering: FR, FL, RR, RL (each with hip, thigh, calf)
    JOINT_NAMES = [
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
        "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
    ]
    
    # Standing pose: natural standing configuration
    # Hip neutral, thigh ~0.9rad forward, calf -1.8rad back
    STANDING_POSE = np.array([
        0.0, 0.9, -1.8,   # FR
        0.0, 0.9, -1.8,   # FL
        0.0, 0.9, -1.8,   # RR
        0.0, 0.9, -1.8,   # RL
    ])
    
    # Sitting pose: legs folded, body lower
    SITTING_POSE = np.array([
        0.0, 1.3, -2.6,   # FR
        0.0, 1.3, -2.6,   # FL
        0.0, 1.3, -2.6,   # RR
        0.0, 1.3, -2.6,   # RL
    ])
    
    # Home pose: same as standing
    HOME_POSE = STANDING_POSE.copy()
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """
        Initialize robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        self.renderer: Optional[mujoco.Renderer] = None
        
        # Resolve joint indices
        self.joint_indices = []
        self.qpos_indices = []
        self.qvel_indices = []
        
        for jnt_name in self.JOINT_NAMES:
            jnt_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, jnt_name)
            if jnt_id == -1:
                raise ValueError(f"Joint '{jnt_name}' not found in model")
            
            self.joint_indices.append(jnt_id)
            self.qpos_indices.append(model.jnt_qposadr[jnt_id])
            self.qvel_indices.append(model.jnt_dofadr[jnt_id])
        
        # Resolve actuator indices (should match joint order)
        self.actuator_indices = []
        for jnt_name in self.JOINT_NAMES:
            act_name = jnt_name.replace("_joint", "")
            act_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, act_name)
            if act_id == -1:
                raise ValueError(f"Actuator '{act_name}' not found in model")
            self.actuator_indices.append(act_id)
        
        # Get joint limits
        self.joint_limits_lower = []
        self.joint_limits_upper = []
        for jnt_id in self.joint_indices:
            limits = model.jnt_range[jnt_id]
            self.joint_limits_lower.append(limits[0])
            self.joint_limits_upper.append(limits[1])
        
        self.joint_limits_lower = np.array(self.joint_limits_lower)
        self.joint_limits_upper = np.array(self.joint_limits_upper)
        
        # Get trunk body ID for height tracking
        self.trunk_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "trunk")
        if self.trunk_id == -1:
            raise ValueError("Trunk body not found")
        
        # Timestep
        self.dt = model.opt.timestep
        
        print(f"[A1 Driver] Initialized with {len(self.joint_indices)} joints, dt={self.dt:.4f}s")
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        
        # Initialize with standing pose
        robot = cls(model, data)
        
        # Set initial base height and standing pose
        robot.data.qpos[0:3] = [0, 0, 0.4]  # Base elevated
        robot.data.qpos[3:7] = [1, 0, 0, 0]  # Identity quaternion
        robot._set_joint_positions(cls.STANDING_POSE)
        robot._set_control(cls.STANDING_POSE)
        mujoco.mj_forward(model, data)
        
        return robot
    
    def home(self) -> bool:
        """
        Move to home (standing) position.
        
        Returns:
            True on success
        """
        print("[A1 Driver] Homing to standing position...")
        return self._interpolate_to_pose(self.HOME_POSE, duration=2.0)
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions.
        
        Returns:
            12-element array of joint positions (radians)
        """
        return np.array([self.data.qpos[idx] for idx in self.qpos_indices])
    
    def get_joint_velocities(self) -> np.ndarray:
        """
        Get current joint velocities.
        
        Returns:
            12-element array of joint velocities (rad/s)
        """
        return np.array([self.data.qvel[idx] for idx in self.qvel_indices])
    
    def _set_joint_positions(self, positions: np.ndarray) -> None:
        """Set joint positions in qpos (clamped to limits)."""
        positions = np.clip(positions, self.joint_limits_lower, self.joint_limits_upper)
        for idx, pos in zip(self.qpos_indices, positions):
            self.data.qpos[idx] = pos
    
    def _set_control(self, positions: np.ndarray) -> None:
        """Set control targets (clamped to actuator ranges)."""
        positions = np.clip(positions, self.joint_limits_lower, self.joint_limits_upper)
        for act_id, pos in zip(self.actuator_indices, positions):
            self.data.ctrl[act_id] = pos
    
    def step(self, n: int = 1) -> None:
        """
        Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> np.ndarray:
        """
        Render current scene.
        
        Returns:
            RGB image array (H, W, 3)
        """
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=480, width=640)
        
        self.renderer.update_scene(self.data)
        return self.renderer.render()
    
    def describe(self) -> str:
        """
        Get robot description.
        
        Returns:
            Multi-line description string
        """
        joint_pos = self.get_joint_positions()
        trunk_height = self.get_body_height()
        base_pos, base_quat = self.get_base_pose()
        
        lines = [
            "=" * 60,
            "Unitree A1 Quadruped Robot",
            "=" * 60,
            f"DOF: {len(self.joint_indices)}",
            f"Timestep: {self.dt:.4f} s",
            "",
            "Joint Positions (rad):",
        ]
        
        for i, (name, pos) in enumerate(zip(self.JOINT_NAMES, joint_pos)):
            lines.append(f"  {name:20s}: {pos:7.4f}")
        
        lines.extend([
            "",
            f"Base Position: [{base_pos[0]:.4f}, {base_pos[1]:.4f}, {base_pos[2]:.4f}]",
            f"Base Quaternion: [{base_quat[0]:.4f}, {base_quat[1]:.4f}, {base_quat[2]:.4f}, {base_quat[3]:.4f}]",
            f"Trunk Height: {trunk_height:.4f} m",
            "=" * 60,
        ])
        
        return "\n".join(lines)
    
    def get_body_height(self) -> float:
        """
        Get trunk body height above ground.
        
        Returns:
            Height in meters
        """
        return self.data.xpos[self.trunk_id][2]
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get base pose (position and orientation).
        
        Returns:
            (position [x,y,z], quaternion [w,x,y,z])
        """
        pos = self.data.qpos[0:3].copy()
        quat = self.data.qpos[3:7].copy()
        return pos, quat
    
    def _interpolate_to_pose(self, target_pose: np.ndarray, duration: float = 2.0) -> bool:
        """
        Smoothly interpolate to target joint positions.
        
        Uses cosine interpolation for smooth motion and settles at target.
        For quadrupeds under gravity, some settling error is normal and acceptable.
        
        Args:
            target_pose: Target joint positions (12-element array)
            duration: Interpolation duration in seconds
            
        Returns:
            True on success (error < 0.2 rad)
        """
        start_pose = self.get_joint_positions()
        steps = int(duration / self.dt)
        
        for i in range(steps):
            alpha = (i + 1) / steps  # Linear interpolation parameter
            # Smooth using cosine interpolation for better motion quality
            alpha_smooth = 0.5 * (1 - np.cos(alpha * np.pi))
            
            intermediate_pose = start_pose + alpha_smooth * (target_pose - start_pose)
            self._set_control(intermediate_pose)
            self.step()
        
        # Final settle - hold target and let dynamics settle
        self._set_control(target_pose)
        self.step(100)  # Settle for 100 steps (~0.2s)
        
        # Check convergence
        final_pose = self.get_joint_positions()
        error = np.linalg.norm(final_pose - target_pose)
        
        # Quadrupeds under gravity will settle slightly - 0.2 rad tolerance is reasonable
        if error > 0.2:
            print(f"[A1 Driver] Warning: Large final error {error:.4f} rad (tolerance: 0.2)")
            return False
        
        return True
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Stand up from current pose to standing position.
        
        Args:
            duration: Motion duration in seconds
            
        Returns:
            True on success
        """
        print("[A1 Driver] Standing up...")
        return self._interpolate_to_pose(self.STANDING_POSE, duration)
    
    def sit(self, duration: float = 1.5) -> bool:
        """
        Sit down by folding legs.
        
        Args:
            duration: Motion duration in seconds
            
        Returns:
            True on success
        """
        print("[A1 Driver] Sitting down...")
        return self._interpolate_to_pose(self.SITTING_POSE, duration)
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using diagonal-pair trot gait.
        
        This implements a simple sinusoidal gait where:
          - FR and RL move together (diagonal pair 1)
          - FL and RR move together (diagonal pair 2)
          - When one pair lifts, the other supports
        
        The gait modulates joint angles around the standing pose to create
        a walking motion. Forward velocity comes from body momentum and
        leg swing coordination.
        
        Args:
            secs: Walking duration in seconds
            speed: Gait frequency (cycles per second, 0.2 = slow, 0.5 = fast)
            
        Returns:
            True on success
        """
        print(f"[A1 Driver] Walking forward for {secs:.1f}s at speed {speed:.2f}...")
        
        steps = int(secs / self.dt)
        period = 1.0 / speed  # Gait period in seconds
        
        for i in range(steps):
            t = i * self.dt
            phase = 2 * np.pi * t / period
            
            # Compute gait offsets
            # Diagonal pairs: FR+RL (phase 0), FL+RR (phase pi)
            fr_rl_swing = max(0, np.sin(phase))
            fl_rr_swing = max(0, np.sin(phase + np.pi))
            
            # Joint angle modulation
            # When swinging: thigh forward, calf back (creates lift and forward reach)
            thigh_amp = 0.35   # Thigh swing amplitude (rad)
            calf_amp = -0.35   # Calf compensates (rad)
            
            # Build offset array
            offsets = np.zeros(12)
            
            # FR (indices 0,1,2): hip, thigh, calf
            offsets[1] = fr_rl_swing * thigh_amp
            offsets[2] = fr_rl_swing * calf_amp
            
            # FL (indices 3,4,5)
            offsets[4] = fl_rr_swing * thigh_amp
            offsets[5] = fl_rr_swing * calf_amp
            
            # RR (indices 6,7,8)
            offsets[7] = fl_rr_swing * thigh_amp
            offsets[8] = fl_rr_swing * calf_amp
            
            # RL (indices 9,10,11)
            offsets[10] = fr_rl_swing * thigh_amp
            offsets[11] = fr_rl_swing * calf_amp
            
            # Apply to standing pose
            target = self.STANDING_POSE + offsets
            self._set_control(target)
            self.step()
        
        # Return to standing after walk
        print("[A1 Driver] Walk complete, returning to stand...")
        self.stand_up(duration=1.0)
        
        return True
    
    def get_foot_positions(self) -> dict:
        """
        Get positions of all four feet (calf bodies).
        
        Returns:
            Dictionary mapping leg name to position [x, y, z]
        """
        positions = {}
        for leg in ['FR', 'FL', 'RR', 'RL']:
            body_name = f"{leg}_calf"
            body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            if body_id != -1:
                positions[leg] = self.data.xpos[body_id].copy()
            else:
                positions[leg] = np.zeros(3)
        return positions


# Convenience functions for testing
def demo_basic():
    """Basic demo: load, home, describe."""
    print("\n" + "="*60)
    print("DEMO: Basic operations")
    print("="*60 + "\n")
    
    robot = Robot.build_from_mjcf('mjcf.xml')
    robot.home()
    print(robot.describe())
    return robot


def demo_motions():
    """Demo: standing, sitting, walking."""
    print("\n" + "="*60)
    print("DEMO: Motion primitives")
    print("="*60 + "\n")
    
    robot = Robot.build_from_mjcf('mjcf.xml')
    
    # Stand
    robot.stand_up(duration=1.5)
    print(f"Standing - trunk height: {robot.get_body_height():.4f} m\n")
    
    # Sit
    robot.sit(duration=1.5)
    print(f"Sitting - trunk height: {robot.get_body_height():.4f} m\n")
    
    # Stand again
    robot.stand_up(duration=1.5)
    
    # Walk
    robot.walk_forward(secs=3.0, speed=0.3)
    base_pos, _ = robot.get_base_pose()
    print(f"After walking - base position: {base_pos}\n")
    
    return robot


if __name__ == "__main__":
    # Run demos
    robot = demo_basic()
    print("\n" + "="*60)
    print("Basic demo complete!")
    print("="*60 + "\n")
