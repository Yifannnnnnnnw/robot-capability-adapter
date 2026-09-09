"""
Driver for Skydio X2 quadcopter drone.
Implements a cascaded PID controller for stable flight.
"""

import mujoco
import numpy as np
import math
import os
from typing import Tuple, List, Optional


class Robot:
    """Robot driver for Skydio X2 quadcopter."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self._setup_controller()
        
        # Flight state
        self.is_flying = False
        self.current_target = None
        self.landing = False
        
        # Object tracking (for manipulation tasks)
        self.scene_objects = {}  # name -> (type, id)
        self._discover_objects()
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF file
            
        Returns:
            Robot instance
        """
        # Resolve include paths
        scene_dir = os.path.dirname(os.path.abspath(mjcf_path))
        original_cwd = os.getcwd()
        os.chdir(scene_dir)
        
        try:
            model = mujoco.MjModel.from_xml_path(os.path.basename(mjcf_path))
            data = mujoco.MjData(model)
            return cls(model, data)
        finally:
            os.chdir(original_cwd)
    
    def _setup_controller(self):
        """Initialize the cascaded flight controller."""
        # Physical parameters
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        self.mass = self.model.body_mass[body_id]
        self.g = 9.81
        
        # Rotor positions from sites
        self.rotor_positions = []
        for i in range(1, 5):
            site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, f'thrust{i}')
            self.rotor_positions.append(self.model.site_pos[site_id])
        self.rotor_positions = np.array(self.rotor_positions)
        
        # Control gains - tuned for stability
        # Position control
        self.kp_pos = np.array([1.2, 1.2, 2.5])  # x, y, z
        self.kd_pos = np.array([0.8, 0.8, 1.5])
        
        # Attitude control
        self.kp_att = np.array([6.0, 6.0, 1.5])  # roll, pitch, yaw
        self.kd_att = np.array([2.0, 2.0, 0.5])
        
        # Mixer matrix (thrusts -> [F_z, tau_x, tau_y, tau_z])
        self.mixer = np.array([
            [1, 1, 1, 1],           # Total thrust
            [0, 0, 0, 0],           # tau_x (roll)
            [0, 0, 0, 0],           # tau_y (pitch)
            [-0.0201, 0.0201, -0.0201, 0.0201]  # tau_z (yaw) from gear ratios
        ])
        
        # Fill mixer based on rotor positions
        for i, pos in enumerate(self.rotor_positions):
            self.mixer[1, i] = pos[1]  # y coordinate for roll
            self.mixer[2, i] = -pos[0]  # -x coordinate for pitch
        
        # Control allocation matrix
        self.alloc = np.linalg.pinv(self.mixer)
        
        # Integral term for altitude
        self.z_integral = 0.0
        self.z_integral_limit = 0.5
        
        # Hover thrust from keyframe
        self.hover_thrust = 3.2495625
    
    def _discover_objects(self):
        """Discover manipulable objects in the scene."""
        # Look for bodies
        for i in range(self.model.nbody):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, i)
            if name and name not in ['x2', 'floor']:
                self.scene_objects[name] = (mujoco.mjtObj.mjOBJ_BODY, i)
        
        # Look for geoms
        for i in range(self.model.ngeom):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, i)
            if name and name not in ['floor']:
                self.scene_objects[name] = (mujoco.mjtObj.mjOBJ_GEOM, i)
        
        # Look for sites (excluding thrust sites)
        for i in range(self.model.nsite):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_SITE, i)
            if name and not name.startswith('thrust'):
                self.scene_objects[name] = (mujoco.mjtObj.mjOBJ_SITE, i)
    
    def _quat_to_euler(self, quat: np.ndarray) -> np.ndarray:
        """Convert quaternion (w, x, y, z) to Euler angles (roll, pitch, yaw)."""
        w, x, y, z = quat
        
        # Roll (x-axis rotation)
        sinr_cosp = 2 * (w * x + y * z)
        cosr_cosp = 1 - 2 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        
        # Pitch (y-axis rotation)
        sinp = 2 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)
        else:
            pitch = math.asin(sinp)
        
        # Yaw (z-axis rotation)
        siny_cosp = 2 * (w * z + x * y)
        cosy_cosp = 1 - 2 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        
        return np.array([roll, pitch, yaw])
    
    def _quat_to_rotmat(self, quat: np.ndarray) -> np.ndarray:
        """Convert quaternion to rotation matrix."""
        w, x, y, z = quat
        return np.array([
            [1 - 2*y*y - 2*z*z, 2*x*y - 2*w*z, 2*x*z + 2*w*y],
            [2*x*y + 2*w*z, 1 - 2*x*x - 2*z*z, 2*y*z - 2*w*x],
            [2*x*z - 2*w*y, 2*y*z + 2*w*x, 1 - 2*x*x - 2*y*y]
        ])
    
    def _compute_control(self, target_pos: np.ndarray, target_yaw: float = 0.0) -> np.ndarray:
        """
        Compute rotor thrusts using cascaded PID controller.
        
        Args:
            target_pos: Target position (x, y, z) in world coordinates
            target_yaw: Target yaw angle (radians)
            
        Returns:
            Array of 4 rotor thrusts
        """
        # Current state
        pos = self.data.qpos[:3]
        vel = self.data.qvel[:3]
        quat = self.data.qpos[3:7]
        ang_vel = self.data.qvel[3:6]
        
        # Position PD control
        pos_error = target_pos - pos
        vel_error = -vel  # Target velocity is 0
        
        acc_des = self.kp_pos * pos_error + self.kd_pos * vel_error
        
        # Integral term for altitude (z-axis)
        if abs(pos_error[2]) < 0.1:
            self.z_integral += pos_error[2] * 0.01  # dt ~ 0.01
            self.z_integral = np.clip(self.z_integral, -self.z_integral_limit, self.z_integral_limit)
            acc_des[2] += 0.3 * self.z_integral
        else:
            self.z_integral = 0.0
        
        # Gravity compensation
        acc_des[2] += self.g
        
        # Convert to body frame
        R = self._quat_to_rotmat(quat)
        F_des_body = self.mass * np.dot(R.T, acc_des)
        
        # Extract thrust magnitude
        thrust_mag = max(F_des_body[2], 1.0)  # Minimum thrust
        
        # Desired tilt angles (limited for stability)
        max_tilt = math.radians(20)
        roll_des = math.atan2(-F_des_body[1], thrust_mag)
        pitch_des = math.atan2(F_des_body[0], thrust_mag)
        roll_des = np.clip(roll_des, -max_tilt, max_tilt)
        pitch_des = np.clip(pitch_des, -max_tilt, max_tilt)
        
        # Attitude control
        current_rpy = self._quat_to_euler(quat)
        target_rpy = np.array([roll_des, pitch_des, target_yaw])
        
        # Error with yaw wrapping
        rpy_error = target_rpy - current_rpy
        rpy_error[2] = math.atan2(math.sin(rpy_error[2]), math.cos(rpy_error[2]))
        
        # PD control
        tau_des = self.kp_att * rpy_error + self.kd_att * (-ang_vel)
        
        # Limit torques
        max_tau = 0.5
        tau_des = np.clip(tau_des, -max_tau, max_tau)
        
        # Wrench vector [F_z, tau_x, tau_y, tau_z]
        wrench = np.array([thrust_mag, tau_des[0], tau_des[1], tau_des[2]])
        
        # Allocate to rotor thrusts
        thrusts = np.dot(self.alloc, wrench)
        
        # Clamp to actuator limits [0, 13]
        thrusts = np.clip(thrusts, 0.0, 13.0)
        
        return thrusts
    
    def home(self) -> bool:
        """
        Reset to home position (ground).
        
        Returns:
            True if successful
        """
        # Set to ground position
        self.data.qpos[:] = [0, 0, 0.1, 1, 0, 0, 0]  # Slightly above ground
        self.data.qvel[:] = 0
        self.data.ctrl[:] = [0, 0, 0, 0]
        mujoco.mj_forward(self.model, self.data)
        
        self.is_flying = False
        self.landing = False
        self.z_integral = 0.0
        
        return True
    
    def get_joint_positions(self):
        """
        Get joint positions.
        For aerial robot, returns base position and orientation.
        """
        return {
            'position': self.data.qpos[:3].copy(),
            'orientation': self.data.qpos[3:7].copy(),
            'velocity': self.data.qvel[:3].copy(),
            'angular_velocity': self.data.qvel[3:6].copy()
        }
    
    def step(self, n: int = 1) -> None:
        """
        Step the simulation.
        
        Args:
            n: Number of steps to take
        """
        for _ in range(n):
            if self.is_flying and self.current_target is not None:
                # Apply flight control
                target_pos, target_yaw = self.current_target
                thrusts = self._compute_control(target_pos, target_yaw)
                self.data.ctrl[:] = thrusts
            
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """Render the current state (placeholder)."""
        print(f"Position: {self.data.qpos[:3]}")
        print(f"Orientation: {self.data.qpos[3:7]}")
        print(f"Controls: {self.data.ctrl}")
        if self.current_target:
            print(f"Target: {self.current_target[0]}, Yaw: {self.current_target[1]:.3f} rad")
    
    def describe(self) -> str:
        """Return description of the robot."""
        return f"Skydio X2 Quadcopter (mass: {self.mass:.3f} kg, 4 rotors)"
    
    # Aerial robot specific methods
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get base pose (position and orientation matrix).
        
        Returns:
            Tuple of (position (3,), rotation matrix (3,3))
        """
        pos = self.data.qpos[:3].copy()
        quat = self.data.qpos[3:7].copy()
        R = self._quat_to_rotmat(quat)
        return pos, R
    
    def takeoff(self, height: float = 0.5) -> bool:
        """
        Take off to specified height.
        
        Args:
            height: Target altitude in meters
            
        Returns:
            True if takeoff successful
        """
        if self.is_flying:
            print("Already flying")
            return False
        
        current_pos = self.data.qpos[:3].copy()
        target_pos = np.array([current_pos[0], current_pos[1], height])
        
        self.current_target = (target_pos, 0.0)
        self.is_flying = True
        self.landing = False
        
        # Takeoff sequence
        print(f"Taking off to {height}m...")
        
        # Fly for 3 seconds to reach altitude
        for _ in range(300):
            self.step()
        
        # Check if we reached target height
        final_height = self.data.qpos[2]
        success = abs(final_height - height) < 0.1
        
        if success:
            print(f"Takeoff successful! Height: {final_height:.3f}m")
        else:
            print(f"Takeoff partially successful. Height: {final_height:.3f}m (target: {height}m)")
        
        return success
    
    def move_to(self, x: float, y: float, z: float, tol: float = 0.1) -> bool:
        """
        Fly to a world target position.
        
        Args:
            x, y, z: Target coordinates
            tol: Tolerance for arrival
            
        Returns:
            True if target reached within tolerance
        """
        if not self.is_flying:
            print("Not flying. Call takeoff() first.")
            return False
        
        target_pos = np.array([x, y, z])
        self.current_target = (target_pos, 0.0)  # Maintain current yaw
        
        print(f"Moving to ({x:.2f}, {y:.2f}, {z:.2f})...")
        
        # Fly for up to 5 seconds
        for i in range(500):
            self.step()
            
            # Check if we're close enough
            current_pos = self.data.qpos[:3]
            error = np.linalg.norm(current_pos - target_pos)
            
            if error < tol:
                print(f"Target reached! Error: {error:.3f}m")
                return True
            
            if i % 100 == 0:
                print(f"  Step {i}: pos={current_pos}, error={error:.3f}m")
        
        # Final check
        current_pos = self.data.qpos[:3]
        error = np.linalg.norm(current_pos - target_pos)
        
        if error < tol:
            print(f"Target reached! Error: {error:.3f}m")
            return True
        else:
            print(f"Target not reached. Final error: {error:.3f}m")
            return False
    
    def hover(self, secs: float = 2.0) -> bool:
        """
        Station-keep at current position.
        
        Args:
            secs: Duration to hover in seconds
            
        Returns:
            True if hover successful (didn't crash)
        """
        if not self.is_flying:
            print("Not flying. Call takeoff() first.")
            return False
        
        target_pos = self.data.qpos[:3].copy()
        self.current_target = (target_pos, 0.0)
        
        print(f"Hovering for {secs} seconds...")
        
        steps = int(secs / 0.01)  # 0.01s timestep
        for i in range(steps):
            self.step()
            
            # Check if we're still upright
            quat = self.data.qpos[3:7]
            R = self._quat_to_rotmat(quat)
            body_z = R[:, 2]
            
            # Z-axis should point mostly up
            if body_z[2] < 0.7:  # More than ~45 degrees tilt
                print("Lost stability during hover!")
                return False
        
        print("Hover successful!")
        return True
    
    def land(self) -> bool:
        """
        Land the drone smoothly.
        
        Returns:
            True if landing successful
        """
        if not self.is_flying:
            print("Not flying")
            return False
        
        self.landing = True
        
        # Get current position
        current_pos = self.data.qpos[:3].copy()
        
        # Descend in steps
        print("Beginning landing sequence...")
        
        target_height = 0.15  # Landing height
        descent_rate = 0.1  # m/s
        
        while current_pos[2] > target_height:
            # Update target
            target_pos = np.array([current_pos[0], current_pos[1], max(target_height, current_pos[2] - descent_rate * 0.01)])
            self.current_target = (target_pos, 0.0)
            
            # Step simulation
            self.step()
            
            # Update current position
            current_pos = self.data.qpos[:3].copy()
        
        # Cut thrust
        self.data.ctrl[:] = [0, 0, 0, 0]
        self.is_flying = False
        self.landing = False
        
        print(f"Landing complete! Final height: {current_pos[2]:.3f}m")
        return True
    
    # Object manipulation methods (required by framework)
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get world position of a named scene object.
        
        Args:
            name: Name of the object
            
        Returns:
            World position (x, y, z)
            
        Raises:
            KeyError: If object not found
        """
        if name not in self.scene_objects:
            raise KeyError(f"Object '{name}' not found in scene")
        
        obj_type, obj_id = self.scene_objects[name]
        
        if obj_type == mujoco.mjtObj.mjOBJ_BODY:
            return self.data.xpos[obj_id].copy()
        elif obj_type == mujoco.mjtObj.mjOBJ_GEOM:
            return self.data.geom_xpos[obj_id].copy()
        elif obj_type == mujoco.mjtObj.mjOBJ_SITE:
            return self.data.site_xpos[obj_id].copy()
        else:
            raise KeyError(f"Unknown object type for '{name}'")
    
    def get_object_names(self) -> List[str]:
        """
        Get names of manipulable scene objects.
        
        Returns:
            List of object names
        """
        return list(self.scene_objects.keys())


# Example usage
if __name__ == "__main__":
    # Test the driver
    robot = Robot.build_from_mjcf("mjcf.xml")
    
    print(robot.describe())
    robot.home()
    robot.render()
    
    # Test flight
    if robot.takeoff(0.5):
        robot.hover(1.0)
        robot.move_to(0.3, 0.2, 0.6)
        robot.hover(1.0)
        robot.land()
    
    print("\nObject discovery:")
    print(f"Found objects: {robot.get_object_names()}")