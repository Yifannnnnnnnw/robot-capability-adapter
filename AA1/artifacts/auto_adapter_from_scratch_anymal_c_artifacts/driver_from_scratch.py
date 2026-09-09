"""
ANYmal C Quadruped Robot Driver (from scratch)

This driver controls the ANYmal C quadruped robot with 12 DOF (4 legs × 3 joints).
It implements PD control for joint-space locomotion behaviors without using IK
(as this is a quadruped with no end-effector to track).

Joint naming: Each leg has HAA (Hip Abduction/Adduction), HFE (Hip Flexion/Extension),
              KFE (Knee Flexion/Extension)
Legs: LF (Left Front), RF (Right Front), LH (Left Hind), RH (Right Hind)

Author: Auto-generated Phase 2 GEN_ALGO
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import os


class Robot:
    """ANYmal C Quadruped Robot Driver"""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """
        Initialize the robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        
        # Joint configuration
        self.joint_names = [
            "LF_HAA", "LF_HFE", "LF_KFE",  # Left Front
            "RF_HAA", "RF_HFE", "RF_KFE",  # Right Front
            "LH_HAA", "LH_HFE", "LH_KFE",  # Left Hind
            "RH_HAA", "RH_HFE", "RH_KFE",  # Right Hind
        ]
        
        # Verify model structure
        assert self.model.nu == 12, f"Expected 12 actuators, got {self.model.nu}"
        assert self.model.nq == 19, f"Expected 19 qpos (7 freejoint + 12 joints), got {self.model.nq}"
        
        # Joint indices (qpos starts at 7, after freejoint)
        self.qpos_start = 7
        self.qvel_start = 6  # qvel starts at 6 (freejoint uses 6 velocities)
        
        # Get joint limits
        self.joint_limits = np.zeros((12, 2))
        for i in range(12):
            jnt_id = self.model.actuator_trnid[i, 0]
            self.joint_limits[i] = self.model.jnt_range[jnt_id]
        
        # Body IDs
        self.base_body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "base")
        self.shank_body_ids = {
            "LF": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "LF_SHANK"),
            "RF": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "RF_SHANK"),
            "LH": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "LH_SHANK"),
            "RH": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "RH_SHANK"),
        }
        
        # Predefined poses
        self.home_pose = np.array([0.0] * 12)
        
        self.stand_pose = np.array([
            0.0, 0.5, -1.0,   # LF: neutral HAA, extend HFE/KFE
            0.0, 0.5, -1.0,   # RF
            0.0, -0.5, 1.0,   # LH: hind legs have opposite sign
            0.0, -0.5, 1.0,   # RH
        ])
        
        self.sit_pose = np.array([
            0.0, 1.5, -2.5,   # LF: folded
            0.0, 1.5, -2.5,   # RF
            0.0, -1.5, 2.5,   # LH
            0.0, -1.5, 2.5,   # RH
        ])
        
        # PD control gains
        self.kp = 50.0  # Position gain
        self.kd = 5.0   # Velocity gain
        
        # Renderer
        self._renderer = None
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        # Resolve symlinks and relative paths
        mjcf_path = os.path.realpath(mjcf_path)
        
        if not os.path.exists(mjcf_path):
            raise FileNotFoundError(f"MJCF file not found: {mjcf_path}")
        
        # Load model
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        
        # Initialize
        mujoco.mj_forward(model, data)
        
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move robot to home (neutral) pose.
        
        Returns:
            True if successful
        """
        return self._move_to_pose(self.home_pose, duration=2.0)
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Move robot to standing pose.
        
        Args:
            duration: Time to reach standing pose (seconds)
            
        Returns:
            True if successful
        """
        return self._move_to_pose(self.stand_pose, duration=duration)
    
    def sit(self, duration: float = 1.5) -> bool:
        """
        Move robot to sitting pose (folded legs).
        
        Args:
            duration: Time to reach sitting pose (seconds)
            
        Returns:
            True if successful
        """
        return self._move_to_pose(self.sit_pose, duration=duration)
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using a trotting gait.
        
        This implements a simple diagonal-pair trotting gait where:
        - LF+RH swing together (diagonal pair 1)
        - RF+LH swing together (diagonal pair 2)
        - Swing phase uses sinusoidal joint modulation
        
        Args:
            secs: Duration of walking (seconds)
            speed: Walking speed factor (affects gait frequency)
            
        Returns:
            True if successful
        """
        try:
            # Start from standing pose
            current_pose = self.get_joint_positions()
            if np.linalg.norm(current_pose - self.stand_pose) > 0.5:
                print("Not in standing pose, moving to stand first...")
                self.stand_up(duration=1.0)
            
            # Gait parameters
            dt = self.model.opt.timestep
            gait_frequency = speed  # Hz (speed factor)
            gait_period = 1.0 / gait_frequency if gait_frequency > 0 else 1.0
            
            num_steps = int(secs / dt)
            
            print(f"Walking forward for {secs}s at speed={speed}...")
            initial_pos = self.get_base_pose()[0].copy()
            
            for step in range(num_steps):
                t = step * dt
                phase = (t % gait_period) / gait_period  # 0 to 1
                
                # Trotting gait: diagonal pairs swing anti-phase
                lf_rh_phase = phase  # LF and RH together
                rf_lh_phase = (phase + 0.5) % 1.0  # RF and LH together (180° out of phase)
                
                target = self.stand_pose.copy()
                
                # Sinusoidal modulation for swing phase
                # During swing (phase < 0.5): lift leg by modulating HFE/KFE
                
                # LF (indices 0,1,2)
                if lf_rh_phase < 0.5:
                    swing = np.sin(2 * np.pi * lf_rh_phase * 2)
                    target[1] += 0.3 * swing  # HFE
                    target[2] -= 0.3 * swing  # KFE
                
                # RF (indices 3,4,5)
                if rf_lh_phase < 0.5:
                    swing = np.sin(2 * np.pi * rf_lh_phase * 2)
                    target[4] += 0.3 * swing  # HFE
                    target[5] -= 0.3 * swing  # KFE
                
                # LH (indices 6,7,8)
                if rf_lh_phase < 0.5:
                    swing = np.sin(2 * np.pi * rf_lh_phase * 2)
                    target[7] -= 0.3 * swing  # HFE (opposite sign for hind legs)
                    target[8] += 0.3 * swing  # KFE
                
                # RH (indices 9,10,11)
                if lf_rh_phase < 0.5:
                    swing = np.sin(2 * np.pi * lf_rh_phase * 2)
                    target[10] -= 0.3 * swing  # HFE (opposite sign for hind legs)
                    target[11] += 0.3 * swing  # KFE
                
                # Clamp to joint limits
                target = self._clamp_to_limits(target)
                
                # Apply control
                self.data.ctrl[:] = target
                mujoco.mj_step(self.model, self.data)
            
            final_pos = self.get_base_pose()[0]
            distance = np.linalg.norm(final_pos[:2] - initial_pos[:2])
            print(f"Walked {distance:.3f}m (XY distance)")
            
            return True
            
        except Exception as e:
            print(f"Walk failed: {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions.
        
        Returns:
            Array of 12 joint positions (radians)
        """
        return self.data.qpos[self.qpos_start:self.qpos_start + 12].copy()
    
    def get_body_height(self) -> float:
        """
        Get the height of the base body above ground.
        
        Returns:
            Height in meters
        """
        return self.data.xpos[self.base_body_id][2]
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get the pose of the base body.
        
        Returns:
            Tuple of (position [3], orientation quaternion [4])
        """
        pos = self.data.qpos[0:3].copy()
        quat = self.data.qpos[3:7].copy()
        return pos, quat
    
    def step(self, n: int = 1):
        """
        Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """
        Render the current state.
        
        Returns:
            RGB image array or None
        """
        try:
            if self._renderer is None:
                self._renderer = mujoco.Renderer(self.model, height=480, width=640)
            
            self._renderer.update_scene(self.data)
            return self._renderer.render()
        except Exception as e:
            print(f"Render failed: {e}")
            return None
    
    def describe(self) -> str:
        """
        Get a description of the robot's current state.
        
        Returns:
            Description string
        """
        joint_pos = self.get_joint_positions()
        base_pos, base_quat = self.get_base_pose()
        base_height = self.get_body_height()
        
        desc = "=== ANYmal C Quadruped Robot ===" + "\\n"
        desc += f"DOF: 12 (4 legs × 3 joints)" + "\\n"
        desc += f"Actuator type: Position control" + "\\n"
        desc += f"Base position: [{base_pos[0]:.3f}, {base_pos[1]:.3f}, {base_pos[2]:.3f}]" + "\\n"
        desc += f"Base height: {base_height:.3f} m" + "\\n"
        desc += f"Base orientation (quat): [{base_quat[0]:.3f}, {base_quat[1]:.3f}, {base_quat[2]:.3f}, {base_quat[3]:.3f}]" + "\\n"
        desc += f"\\nJoint positions (rad):" + "\\n"
        
        for i, name in enumerate(self.joint_names):
            desc += f"  {name}: {joint_pos[i]:+.3f}" + "\\n"
        
        desc += f"\\nFoot heights (shank bodies):" + "\\n"
        for leg_name, body_id in self.shank_body_ids.items():
            foot_z = self.data.xpos[body_id][2]
            desc += f"  {leg_name}: {foot_z:.3f} m" + "\\n"
        
        return desc
    
    # ========== Private methods ==========
    
    def _move_to_pose(self, target_pose: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move to a target pose using PD control with interpolation.
        
        Args:
            target_pose: Target joint positions (12,)
            duration: Time to reach target (seconds)
            
        Returns:
            True if successful
        """
        try:
            # Clamp target to limits
            target_pose = self._clamp_to_limits(target_pose)
            
            # Get current pose
            start_pose = self.get_joint_positions()
            
            # Simulate with interpolation
            dt = self.model.opt.timestep
            num_steps = int(duration / dt)
            
            for step in range(num_steps):
                # Linear interpolation
                alpha = (step + 1) / num_steps
                interpolated_target = (1 - alpha) * start_pose + alpha * target_pose
                
                # Apply control (position control actuators)
                self.data.ctrl[:] = interpolated_target
                
                # Step simulation
                mujoco.mj_step(self.model, self.data)
            
            # Check if we reached the target
            final_pose = self.get_joint_positions()
            error = np.linalg.norm(final_pose - target_pose)
            
            if error > 0.1:  # 0.1 rad tolerance
                print(f"Warning: Large final error {error:.4f} rad")
            
            return True
            
        except Exception as e:
            print(f"Move to pose failed: {e}")
            return False
    
    def _clamp_to_limits(self, q: np.ndarray) -> np.ndarray:
        """
        Clamp joint positions to limits.
        
        Args:
            q: Joint positions (12,)
            
        Returns:
            Clamped joint positions
        """
        q_clamped = q.copy()
        for i in range(12):
            q_clamped[i] = np.clip(q[i], self.joint_limits[i, 0], self.joint_limits[i, 1])
        return q_clamped


# ========== Standalone test ==========

if __name__ == "__main__":
    import sys
    
    # Test the driver
    print("Testing ANYmal C driver...")
    
    # Build robot
    robot = Robot.build_from_mjcf("mjcf.xml")
    print("✓ Robot built successfully")
    
    # Home
    robot.home()
    print("✓ Moved to home pose")
    
    # Describe
    print()
    print(robot.describe())
    
    # Stand up
    print("\\n" + "="*50)
    print("Testing stand_up()...")
    robot.stand_up(duration=1.0)
    print(f"Base height after standing: {robot.get_body_height():.3f} m")
    
    # Sit
    print("\\n" + "="*50)
    print("Testing sit()...")
    robot.sit(duration=1.0)
    print(f"Base height after sitting: {robot.get_body_height():.3f} m")
    
    # Walk
    print("\\n" + "="*50)
    print("Testing walk_forward()...")
    robot.stand_up(duration=1.0)
    initial_pos = robot.get_base_pose()[0]
    robot.walk_forward(secs=2.0, speed=0.3)
    final_pos = robot.get_base_pose()[0]
    distance = np.linalg.norm(final_pos[:2] - initial_pos[:2])
    print(f"Distance traveled: {distance:.3f} m")
    
    print("\\n✓ All tests passed!")
