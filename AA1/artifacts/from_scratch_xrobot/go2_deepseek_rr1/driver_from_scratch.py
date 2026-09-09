"""
Driver for Unitree Go2 quadruped robot.
Implements standing, sitting, and walking behaviors using PD control.
"""

import mujoco
import numpy as np
import math
import time
import os
from typing import Tuple, List, Optional


class Robot:
    """Robot driver for Unitree Go2 quadruped."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize with MuJoCo model and data."""
        self.model = model
        self.data = data
        self.viewer = None
        
        # PD control gains
        self.kp = 200.0
        self.kd = 20.0
        
        # Joint and actuator mappings
        self._setup_mappings()
        
        # Predefined poses
        self._define_poses()
        
    def _setup_mappings(self):
        """Setup joint and actuator name mappings."""
        # Joint names in qpos[7:] order
        self.joint_names = [
            'FL_hip_joint', 'FL_thigh_joint', 'FL_calf_joint',
            'FR_hip_joint', 'FR_thigh_joint', 'FR_calf_joint',
            'RL_hip_joint', 'RL_thigh_joint', 'RL_calf_joint',
            'RR_hip_joint', 'RR_thigh_joint', 'RR_calf_joint'
        ]
        
        # Actuator names in ctrl order
        self.actuator_names = [
            'FR_hip', 'FR_thigh', 'FR_calf',
            'FL_hip', 'FL_thigh', 'FL_calf',
            'RR_hip', 'RR_thigh', 'RR_calf',
            'RL_hip', 'RL_thigh', 'RL_calf'
        ]
        
        # Map joint name to index in qpos[7:]
        self.joint_idx = {name: i for i, name in enumerate(self.joint_names)}
        
        # Map actuator name to index in ctrl
        self.actuator_idx = {name: i for i, name in enumerate(self.actuator_names)}
        
        # Map joint to actuator (for PD control)
        self.joint_to_actuator = {}
        for i, jname in enumerate(self.joint_names):
            leg = jname[:2]  # FL, FR, RL, RR
            joint_type = jname.split('_')[1]  # hip, thigh, calf
            
            if leg == 'FL':
                act_idx = 3 + ['hip', 'thigh', 'calf'].index(joint_type)
            elif leg == 'FR':
                act_idx = 0 + ['hip', 'thigh', 'calf'].index(joint_type)
            elif leg == 'RL':
                act_idx = 9 + ['hip', 'thigh', 'calf'].index(joint_type)
            elif leg == 'RR':
                act_idx = 6 + ['hip', 'thigh', 'calf'].index(joint_type)
            self.joint_to_actuator[i] = act_idx
    
    def _define_poses(self):
        """Define standing, sitting, and home poses."""
        # Get home pose from keyframe
        home_idx = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, 'home')
        if home_idx >= 0:
            self.home_qpos = self.model.key_qpos[home_idx].copy()
            self.home_ctrl = self.model.key_ctrl[home_idx].copy()
        else:
            # Default home pose if no keyframe
            self.home_qpos = np.zeros(self.model.nq)
            self.home_qpos[2] = 0.27  # Base height
            self.home_qpos[3] = 1.0   # Quaternion w
            self.home_qpos[7:] = [0.0, 0.9, -1.8] * 4  # Joints
            self.home_ctrl = np.zeros(self.model.nu)
        
        # Standing pose (taller)
        self.stand_qpos = self.home_qpos.copy()
        self.stand_qpos[7:] = [
            0.0, 1.2, -1.5,  # FL: hip, thigh, calf
            0.0, 1.2, -1.5,  # FR
            0.0, 1.2, -1.5,  # RL  
            0.0, 1.2, -1.5   # RR
        ]
        
        # Sitting pose (lower)
        self.sit_qpos = self.home_qpos.copy()
        self.sit_qpos[7:] = [
            0.0, 0.6, -2.2,  # FL: hip, thigh, calf
            0.0, 0.6, -2.2,  # FR
            0.0, 0.6, -2.2,  # RL  
            0.0, 0.6, -2.2   # RR
        ]
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file path."""
        # Resolve symlinks to get real path
        real_path = os.path.realpath(mjcf_path)
        
        # Load model from real path
        model = mujoco.MjModel.from_xml_path(real_path)
        data = mujoco.MjData(model)
        
        return cls(model, data)
    
    def home(self) -> bool:
        """Reset to home pose."""
        try:
            self.data.qpos[:] = self.home_qpos
            self.data.qvel[:] = 0
            self.data.ctrl[:] = self.home_ctrl
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions (excluding base)."""
        return self.data.qpos[7:].copy()
    
    def step(self, n: int = 1) -> None:
        """Step simulation n times."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """Render current state."""
        if self.viewer is None:
            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        else:
            mujoco.viewer.sync(self.viewer, self.model, self.data)
    
    def describe(self) -> str:
        """Return description of robot state."""
        height = self.get_body_height()
        joints = self.get_joint_positions()
        return (f"Unitree Go2 quadruped - Height: {height:.3f}m, "
                f"Joints: {len(joints)}, Base position: {self.data.xpos[1]}")
    
    def get_body_height(self) -> float:
        """Get body height (base link z-position)."""
        base_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, 'base_link')
        if base_id >= 0:
            return self.data.xpos[base_id][2]
        return 0.0
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get base position and orientation matrix.
        
        Returns:
            Tuple of (position (3,), orientation matrix (3,3))
        """
        base_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, 'base_link')
        if base_id >= 0:
            pos = self.data.xpos[base_id].copy()
            # Get orientation matrix from quaternion in qpos
            # Base orientation is stored as quaternion in qpos[3:7]
            quat = self.data.qpos[3:7]
            # Convert quaternion to rotation matrix
            R = np.zeros((3, 3))
            mujoco.mju_quat2Mat(R.ravel(), quat)
            return pos, R
        return np.zeros(3), np.eye(3)
    
    def _pd_control_to_target(self, target_qpos: np.ndarray) -> None:
        """Apply PD control to reach target joint positions."""
        # Get current joint positions and velocities
        current_qpos = self.data.qpos[7:]
        current_qvel = self.data.qvel[6:]  # Skip base velocity
        
        target_joints = target_qpos[7:]
        
        # Apply PD control for each joint
        for i in range(12):
            error = target_joints[i] - current_qpos[i]
            error_vel = 0 - current_qvel[i]
            torque = self.kp * error + self.kd * error_vel
            
            # Map to actuator
            act_idx = self.joint_to_actuator[i]
            self.data.ctrl[act_idx] = torque
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """Stand up to stable pose.
        
        Args:
            duration: Time to reach standing pose in seconds.
            
        Returns:
            True if successful, False otherwise.
        """
        try:
            dt = self.model.opt.timestep
            steps = int(duration / dt)
            
            # Record initial height for validation
            initial_height = self.get_body_height()
            
            for step in range(steps):
                # Interpolate from current to standing pose
                alpha = min(1.0, step / max(steps - 1, 1))
                target_qpos = self.data.qpos.copy()
                target_qpos[7:] = (1 - alpha) * self.data.qpos[7:] + alpha * self.stand_qpos[7:]
                
                self._pd_control_to_target(target_qpos)
                self.step()
            
            # Validate final height > 0.15m
            final_height = self.get_body_height()
            if final_height > 0.15:
                print(f"Stand up successful: height = {final_height:.3f}m")
                return True
            else:
                print(f"Stand up failed: height = {final_height:.3f}m < 0.15m")
                return False
                
        except Exception as e:
            print(f"Error in stand_up(): {e}")
            return False
    
    def sit(self, duration: float = 1.5) -> bool:
        """Sit down to folded pose.
        
        Args:
            duration: Time to reach sitting pose in seconds.
            
        Returns:
            True if successful (final height < 0.8 * standing height), False otherwise.
        """
        try:
            dt = self.model.opt.timestep
            steps = int(duration / dt)
            
            # First stand up if not already standing
            standing_height = self.get_body_height()
            if standing_height < 0.2:
                self.stand_up(1.0)
                standing_height = self.get_body_height()
            
            for step in range(steps):
                # Interpolate from current to sitting pose
                alpha = min(1.0, step / max(steps - 1, 1))
                target_qpos = self.data.qpos.copy()
                target_qpos[7:] = (1 - alpha) * self.data.qpos[7:] + alpha * self.sit_qpos[7:]
                
                self._pd_control_to_target(target_qpos)
                self.step()
            
            # Validate final height < 0.8 * standing height
            final_height = self.get_body_height()
            if final_height < 0.8 * standing_height:
                print(f"Sit successful: height = {final_height:.3f}m < {0.8 * standing_height:.3f}m")
                return True
            else:
                print(f"Sit failed: height = {final_height:.3f}m >= {0.8 * standing_height:.3f}m")
                return False
                
        except Exception as e:
            print(f"Error in sit(): {e}")
            return False
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """Walk forward using trot gait.
        
        Args:
            secs: Duration to walk in seconds.
            speed: Target forward speed (m/s).
            
        Returns:
            True if successful (forward displacement > 3cm), False otherwise.
        """
        try:
            dt = self.model.opt.timestep
            total_steps = int(secs / dt)
            
            # Only stand up if not already standing (height > 0.2m)
            if self.get_body_height() < 0.2:
                self.stand_up(1.0)
            
            # Record initial position AFTER standing
            initial_pos = self.data.xpos[1].copy()
            
            # Gait parameters
            step_period = 0.5  # seconds
            steps_per_cycle = int(step_period / dt)
            
            # Base standing pose
            base_qpos = self.stand_qpos.copy()
            
            for step in range(total_steps):
                # Time in gait cycle (0 to 1)
                t = (step % steps_per_cycle) / steps_per_cycle
                
                # Create target for trot gait
                target_qpos = base_qpos.copy()
                
                # Oscillate hip joints
                hip_swing = 0.3 * math.sin(2 * math.pi * t)
                
                if t < 0.5:
                    # FL and RR swing forward
                    target_qpos[7] = hip_swing   # FL hip
                    target_qpos[16] = hip_swing  # RR hip
                    target_qpos[10] = -hip_swing * 0.5  # FR hip  
                    target_qpos[13] = -hip_swing * 0.5  # RL hip
                else:
                    # FR and RL swing forward
                    target_qpos[10] = hip_swing   # FR hip
                    target_qpos[13] = hip_swing   # RL hip
                    target_qpos[7] = -hip_swing * 0.5   # FL hip  
                    target_qpos[16] = -hip_swing * 0.5  # RR hip
                
                self._pd_control_to_target(target_qpos)
                self.step()
            
            # Check forward displacement
            final_pos = self.data.xpos[1].copy()
            displacement = final_pos[0] - initial_pos[0]
            
            if displacement > 0.03:  # > 3cm
                print(f"Walk successful: displacement = {displacement:.3f}m")
                return True
            else:
                print(f"Walk failed: displacement = {displacement:.3f}m <= 0.03m")
                return False
                
        except Exception as e:
            print(f"Error in walk_forward(): {e}")
            return False


# Example usage
if __name__ == "__main__":
    # Test the driver
    robot = Robot.build_from_mjcf("mjcf.xml")
    robot.home()
    print(robot.describe())
    
    # Test behaviors
    success = robot.stand_up(2.0)
    print(f"Stand up: {success}")
    
    success = robot.sit(1.5)
    print(f"Sit: {success}")
    
    success = robot.walk_forward(2.0, 0.2)
    print(f"Walk forward: {success}")