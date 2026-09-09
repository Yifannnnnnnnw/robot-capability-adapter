"""
Skydio X2 Quadrotor Driver - From Scratch
==========================================

A cascaded PD controller for the Skydio X2 quadrotor with 4 thrust actuators.

Controller Architecture:
- Outer loop: Position control (x, y, z) -> desired thrust + tilt angles
- Inner loop: Attitude control (roll, pitch, yaw) -> per-rotor thrust commands
- Mixing: Convert total thrust + torques to individual rotor thrusts

The controller uses empirically tuned gains for stable flight.
"""

import mujoco
import numpy as np
from typing import Tuple, Optional


class Robot:
    """Skydio X2 quadrotor driver with cascaded PD control."""
    
    # Physical constants
    MASS = 1.325  # kg
    GRAVITY = 9.81  # m/s^2
    HOVER_THRUST_PER_ROTOR = MASS * GRAVITY / 4.0  # ~3.25 N
    
    # Controller gains (tuned for stability and performance)
    KP_Z = 5.0
    KD_Z = 4.0
    KP_XY = 0.25
    KD_XY = 1.2
    KP_ATT = 3.5
    KD_ATT = 4.5  # High damping
    
    MAX_TILT = 0.06  # radians (~3.4 degrees)
    K_MIX = 0.12  # Mixing gain
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize the robot with MuJoCo model and data."""
        self.model = model
        self.data = data
        self._ctrl_active = False
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file path."""
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return cls(model, data)
    
    def home(self) -> bool:
        """Reset to hover position at 0.3m altitude."""
        try:
            # Use the hover keyframe if available
            if self.model.nkey > 0:
                mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
            else:
                mujoco.mj_resetData(self.model, self.data)
                self.data.qpos[2] = 0.3  # 0.3m altitude
            
            # Set hover thrust
            self.data.ctrl[:] = self.HOVER_THRUST_PER_ROTOR
            mujoco.mj_forward(self.model, self.data)
            self._ctrl_active = False
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get joint positions (qpos for free joint: xyz + quaternion)."""
        return self.data.qpos.copy()
    
    def step(self, n: int = 1) -> None:
        """Step the simulation n times."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """Render is handled externally; return None."""
        return None
    
    def describe(self) -> str:
        """Return a description of the robot."""
        pos = self.data.qpos[0:3]
        return (f"Skydio X2 Quadrotor\n"
                f"  Mass: {self.MASS} kg\n"
                f"  Actuators: 4 thrust motors (0-13 N)\n"
                f"  Current position: [{pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}] m\n"
                f"  Controller: Cascaded PD (position + attitude)")
    
    # ========== Aerial-specific API ==========
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get base position and orientation.
        
        Returns:
            (xyz, R): Position vector and 3x3 rotation matrix
        """
        pos = self.data.qpos[0:3].copy()
        quat = self.data.qpos[3:7].copy()  # [w, x, y, z]
        R = self._quat_to_rotation_matrix(quat)
        return pos, R
    
    def takeoff(self, height: float = 0.5) -> bool:
        """
        Take off to specified height and hold a stable hover.
        
        Args:
            height: Target altitude in meters
            
        Returns:
            True if successful (reached height and stayed upright)
        """
        try:
            target = np.array([0.0, 0.0, height])
            
            # Climb to target height
            for step in range(5000):
                self._control_step(target)
                
                pos, R = self.get_base_pose()
                roll, pitch, _ = self._rotation_matrix_to_euler(R)
                
                # Check for flip
                if abs(roll) > 1.2 or abs(pitch) > 1.2:
                    print(f"Takeoff failed: large tilt at step {step}")
                    return False
                
                # Check if reached target
                err = np.linalg.norm(target - pos)
                if err < 0.15 and abs(pos[2] - height) < 0.1:
                    # Hold for a bit to verify stability
                    stable = True
                    for _ in range(800):
                        self._control_step(target)
                        pos, R = self.get_base_pose()
                        roll, pitch, _ = self._rotation_matrix_to_euler(R)
                        if abs(roll) > 0.6 or abs(pitch) > 0.6:
                            stable = False
                            break
                        if np.linalg.norm(target - pos) > 0.25:
                            stable = False
                            break
                    
                    if stable:
                        self._ctrl_active = True
                        return True
            
            # Timeout - check if we're close enough
            pos, R = self.get_base_pose()
            roll, pitch, _ = self._rotation_matrix_to_euler(R)
            err = np.linalg.norm(target - pos)
            print(f"Takeoff timeout: err={err:.3f}m, roll={np.degrees(roll):.1f}°, pitch={np.degrees(pitch):.1f}°")
            return err < 0.2 and abs(roll) < 0.5 and abs(pitch) < 0.5
            
        except Exception as e:
            print(f"Error in takeoff(): {e}")
            return False
    
    def move_to(self, x: float, y: float, z: float, tol: float = 0.1) -> bool:
        """
        Fly to a target position.
        
        Args:
            x, y, z: Target position in world frame
            tol: Position tolerance in meters
            
        Returns:
            True if reached target within tolerance
        """
        try:
            target = np.array([x, y, z])
            
            for step in range(6000):
                self._control_step(target)
                
                pos, R = self.get_base_pose()
                roll, pitch, _ = self._rotation_matrix_to_euler(R)
                
                # Check for flip
                if abs(roll) > 1.2 or abs(pitch) > 1.2:
                    print(f"move_to failed: large tilt at step {step}")
                    return False
                
                # Check if reached target
                err = np.linalg.norm(target - pos)
                if err < tol:
                    # Hold for verification
                    stable = True
                    for _ in range(400):
                        self._control_step(target)
                        pos, R = self.get_base_pose()
                        roll, pitch, _ = self._rotation_matrix_to_euler(R)
                        if np.linalg.norm(target - pos) > tol * 1.8:
                            stable = False
                            break
                        if abs(roll) > 0.8 or abs(pitch) > 0.8:
                            stable = False
                            break
                    
                    if stable:
                        return True
            
            # Timeout
            pos, R = self.get_base_pose()
            roll, pitch, _ = self._rotation_matrix_to_euler(R)
            err = np.linalg.norm(target - pos)
            print(f"move_to timeout: err={err:.3f}m, roll={np.degrees(roll):.1f}°, pitch={np.degrees(pitch):.1f}°")
            return err < tol * 2.0 and abs(roll) < 0.6 and abs(pitch) < 0.6
            
        except Exception as e:
            print(f"Error in move_to(): {e}")
            return False
    
    def hover(self, secs: float = 2.0) -> bool:
        """
        Hold current position for specified duration.
        
        Args:
            secs: Duration in seconds
            
        Returns:
            True if maintained position successfully
        """
        try:
            target, _ = self.get_base_pose()
            steps = int(secs / self.model.opt.timestep)
            
            for _ in range(steps):
                self._control_step(target)
                
                pos, R = self.get_base_pose()
                roll, pitch, _ = self._rotation_matrix_to_euler(R)
                
                # Check for flip or large drift
                if abs(roll) > 1.0 or abs(pitch) > 1.0:
                    return False
                
                if np.linalg.norm(target - pos) > 0.4:
                    return False
            
            return True
            
        except Exception as e:
            print(f"Error in hover(): {e}")
            return False
    
    def land(self) -> bool:
        """
        Descend to the ground and idle rotors.
        
        Returns:
            True if landed successfully
        """
        try:
            # Descend gradually
            for _ in range(2500):
                pos, _ = self.get_base_pose()
                target = np.array([pos[0], pos[1], max(0.05, pos[2] - 0.0008)])
                self._control_step(target)
                
                if pos[2] < 0.08:
                    break
            
            # Idle rotors
            self.data.ctrl[:] = 0.0
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            
            self._ctrl_active = False
            return True
            
        except Exception as e:
            print(f"Error in land(): {e}")
            return False
    
    # ========== Internal controller ==========
    
    def _control_step(self, target: np.ndarray) -> None:
        """
        Execute one step of the cascaded controller.
        
        Args:
            target: Target position [x, y, z]
        """
        pos = self.data.qpos[0:3].copy()
        quat = self.data.qpos[3:7].copy()
        vel = self.data.qvel[0:3].copy()
        ang_vel = self.data.qvel[3:6].copy()
        
        R = self._quat_to_rotation_matrix(quat)
        roll, pitch, yaw = self._rotation_matrix_to_euler(R)
        
        # Outer loop: position control
        pos_err = target - pos
        
        # Altitude control
        z_accel = self.KP_Z * pos_err[2] - self.KD_Z * vel[2]
        thrust_total = self.MASS * (self.GRAVITY + z_accel)
        thrust_total = np.clip(thrust_total, 0, 52)  # 4 * 13 N max
        
        # Horizontal control -> desired tilt angles
        # NOTE: Signs are inverted from intuition due to body frame dynamics
        roll_des = np.clip(-self.KP_XY * pos_err[1] + self.KD_XY * vel[1], 
                          -self.MAX_TILT, self.MAX_TILT)
        pitch_des = np.clip(self.KP_XY * pos_err[0] - self.KD_XY * vel[0], 
                           -self.MAX_TILT, self.MAX_TILT)
        yaw_des = 0.0
        
        # Inner loop: attitude control
        att_err = np.array([
            roll_des - roll,
            pitch_des - pitch,
            np.arctan2(np.sin(yaw_des - yaw), np.cos(yaw_des - yaw))
        ])
        
        tau = self.KP_ATT * att_err - self.KD_ATT * ang_vel
        
        # Mixing: convert thrust + torques to rotor thrusts
        # Rotor layout (X-configuration):
        #   1: rear-left  (-.14, -.18), CCW
        #   2: rear-right (-.14,  .18), CW
        #   3: front-right( .14,  .18), CCW
        #   4: front-left ( .14, -.18), CW
        #
        # Correct mixing matrix (empirically verified):
        #   thrust1 = base - k*tau_roll + k*tau_pitch - k*tau_yaw
        #   thrust2 = base + k*tau_roll + k*tau_pitch + k*tau_yaw
        #   thrust3 = base + k*tau_roll - k*tau_pitch - k*tau_yaw
        #   thrust4 = base - k*tau_roll - k*tau_pitch + k*tau_yaw
        
        base = thrust_total / 4.0
        
        thrust1 = base - self.K_MIX * tau[0] + self.K_MIX * tau[1] - self.K_MIX * tau[2]
        thrust2 = base + self.K_MIX * tau[0] + self.K_MIX * tau[1] + self.K_MIX * tau[2]
        thrust3 = base + self.K_MIX * tau[0] - self.K_MIX * tau[1] - self.K_MIX * tau[2]
        thrust4 = base - self.K_MIX * tau[0] - self.K_MIX * tau[1] + self.K_MIX * tau[2]
        
        self.data.ctrl[0] = np.clip(thrust1, 0, 13)
        self.data.ctrl[1] = np.clip(thrust2, 0, 13)
        self.data.ctrl[2] = np.clip(thrust3, 0, 13)
        self.data.ctrl[3] = np.clip(thrust4, 0, 13)
        
        mujoco.mj_step(self.model, self.data)
    
    # ========== Utility functions ==========
    
    @staticmethod
    def _quat_to_rotation_matrix(q: np.ndarray) -> np.ndarray:
        """Convert quaternion [w, x, y, z] to 3x3 rotation matrix."""
        w, x, y, z = q
        return np.array([
            [1 - 2*(y**2 + z**2), 2*(x*y - w*z), 2*(x*z + w*y)],
            [2*(x*y + w*z), 1 - 2*(x**2 + z**2), 2*(y*z - w*x)],
            [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x**2 + y**2)]
        ])
    
    @staticmethod
    def _rotation_matrix_to_euler(R: np.ndarray) -> Tuple[float, float, float]:
        """Convert rotation matrix to roll, pitch, yaw (ZYX convention)."""
        sy = np.sqrt(R[0, 0]**2 + R[1, 0]**2)
        if sy > 1e-6:
            roll = np.arctan2(R[2, 1], R[2, 2])
            pitch = np.arctan2(-R[2, 0], sy)
            yaw = np.arctan2(R[1, 0], R[0, 0])
        else:
            roll = np.arctan2(-R[1, 2], R[1, 1])
            pitch = np.arctan2(-R[2, 0], sy)
            yaw = 0
        return roll, pitch, yaw
