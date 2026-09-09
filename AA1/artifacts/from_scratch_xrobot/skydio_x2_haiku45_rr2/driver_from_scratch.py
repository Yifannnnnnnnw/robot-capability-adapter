"""
Skydio X2 Quadrotor Drone Driver
================================
A complete, self-contained driver for the Skydio X2 aerial robot.
Uses a cascaded PD controller with proper thrust mixing based on rotor geometry.

No imports from auto_adapter.skeletons — only mujoco, numpy, and stdlib.

CONTROLLER DESIGN:
- Outer loop: PD control on altitude (z) to compute total thrust
- Outer loop: PD control on XY position to compute desired roll/pitch angles
- Inner loop: PD control on roll/pitch/yaw to stabilize attitude
- Thrust mixing: Proper mixing matrix accounting for rotor moment arms
- Gains tuned for responsive movement while maintaining stability

ROTOR LAYOUT (X-configuration):
  thrust1: rear-left   (-0.14, -0.18, 0.05) - CCW
  thrust2: front-left  (-0.14,  0.18, 0.05) - CW
  thrust3: front-right ( 0.14,  0.18, 0.08) - CCW
  thrust4: rear-right  ( 0.14, -0.18, 0.08) - CW

BEHAVIORS:
- takeoff(height): Climb to target altitude and hover
- hover(secs): Maintain current position for duration
- land(): Descend to ground and idle rotors
- move_to(x, y, z): Fly to target position
- get_base_pose(): Return (xyz, R3x3) of drone body
- get_base_yaw(): Return yaw angle in radians
"""

import mujoco
import numpy as np
import os
from typing import Tuple, Optional


class Robot:
    """Skydio X2 quadrotor drone controller."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize the robot with a loaded MuJoCo model and data."""
        self.model = model
        self.data = data
        
        # Get body and mass info
        self.body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "x2")
        self.total_mass = sum(self.model.body_mass[i] for i in range(self.model.nbody))
        self.g = 9.81
        self.hover_thrust_per_motor = self.total_mass * self.g / 4.0
        
        # Precompute thrust mixing matrix (inverse)
        # Based on rotor positions and spin directions
        # Rotor positions: T1=(-0.14,-0.18), T2=(-0.14,0.18), T3=(0.14,0.18), T4=(0.14,-0.18)
        # Spin directions: T1=CCW, T2=CW, T3=CCW, T4=CW
        # Mixing matrix M maps [F_total, tau_x, tau_y, tau_z] to [T1, T2, T3, T4]
        # M_inv maps [T1, T2, T3, T4] to [F_total, tau_x, tau_y, tau_z]
        self.M_inv = np.array([
            [ 0.25,       -1.38888889,  1.78571429,  0.25      ],
            [ 0.25,        1.38888889,  1.78571429, -0.25      ],
            [ 0.25,        1.38888889, -1.78571429,  0.25      ],
            [ 0.25,       -1.38888889, -1.78571429, -0.25      ],
        ])
        
        # Controller gains - tuned for responsive movement with good damping
        # Altitude control - reduced to avoid overshoot when tilting
        self.kp_z = 0.6
        self.kd_z = 0.3
        
        # XY position control - low gains for smooth convergence
        self.kp_xy = 0.2
        self.kd_xy = 0.4
        
        # Attitude control
        self.kp_att = 1.0
        self.kd_att = 0.3
        
        # Limits
        self.max_tilt_angle = 0.3  # ~17 degrees max
        
        # State tracking
        self._is_hovering = False
        self._target_z = 0.3
        self._target_xy = np.array([0.0, 0.0])
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Build a Robot from an MJCF file path."""
        # Resolve symlinks
        mjcf_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """Reset to the hover keyframe (home position)."""
        try:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
            mujoco.mj_forward(self.model, self.data)
            self._is_hovering = False
            return True
        except Exception as e:
            print(f"Error homing: {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Return the current joint positions (qpos)."""
        return self.data.qpos.copy()
    
    def step(self, n: int = 1) -> None:
        """Step the simulation n times."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """Placeholder for rendering (not implemented in headless mode)."""
        pass
    
    def describe(self) -> str:
        """Return a description of the robot state."""
        xyz, R = self.get_base_pose()
        quat = self.data.qpos[3:7]
        roll, pitch, yaw = self._quat_to_euler(quat)
        
        return (
            f"Skydio X2 Quadrotor\n"
            f"  Position: {xyz}\n"
            f"  Attitude (roll, pitch, yaw): {np.degrees([roll, pitch, yaw])} deg\n"
            f"  Velocity: {self.data.qvel[:3]}\n"
            f"  Angular velocity: {self.data.qvel[3:6]}\n"
            f"  Control: {self.data.ctrl}\n"
            f"  Hovering: {self._is_hovering}"
        )
    
    # ========== FK / Pose Methods ==========
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return the base (body) pose as (xyz, R3x3).
        xyz: world position of the drone body
        R3x3: 3x3 rotation matrix (world -> body)
        """
        xyz = self.data.xpos[self.body_id].copy()
        R = self.data.xmat[self.body_id].reshape(3, 3).copy()
        return xyz, R
    
    def get_base_yaw(self) -> float:
        """Return the yaw angle (rotation around z-axis) in radians."""
        quat = self.data.qpos[3:7]
        _, _, yaw = self._quat_to_euler(quat)
        return yaw
    
    # ========== Utility Methods ==========
    
    @staticmethod
    def _quat_to_euler(quat_wxyz: np.ndarray) -> Tuple[float, float, float]:
        """
        Convert wxyz quaternion to roll, pitch, yaw (radians).
        Returns: (roll, pitch, yaw)
        """
        w, x, y, z = quat_wxyz
        
        # Roll (x-axis rotation)
        sinr_cosp = 2 * (w * x + y * z)
        cosr_cosp = 1 - 2 * (x * x + y * y)
        roll = np.arctan2(sinr_cosp, cosr_cosp)
        
        # Pitch (y-axis rotation)
        sinp = 2 * (w * y - z * x)
        sinp = np.clip(sinp, -1, 1)
        pitch = np.arcsin(sinp)
        
        # Yaw (z-axis rotation)
        siny_cosp = 2 * (w * z + x * y)
        cosy_cosp = 1 - 2 * (y * y + z * z)
        yaw = np.arctan2(siny_cosp, cosy_cosp)
        
        return roll, pitch, yaw
    
    @staticmethod
    def _euler_to_quat(roll: float, pitch: float, yaw: float) -> np.ndarray:
        """
        Convert roll, pitch, yaw (radians) to wxyz quaternion.
        Returns: [w, x, y, z]
        """
        cy = np.cos(yaw * 0.5)
        sy = np.sin(yaw * 0.5)
        cp = np.cos(pitch * 0.5)
        sp = np.sin(pitch * 0.5)
        cr = np.cos(roll * 0.5)
        sr = np.sin(roll * 0.5)
        
        w = cr * cp * cy + sr * sp * sy
        x = sr * cp * cy - cr * sp * sy
        y = cr * sp * cy + sr * cp * sy
        z = cr * cp * sy - sr * sp * cy
        
        return np.array([w, x, y, z])
    
    def _compute_thrust_mix(
        self,
        total_thrust: float,
        tau_roll: float,
        tau_pitch: float,
        tau_yaw: float = 0.0
    ) -> np.ndarray:
        """
        Mix desired total thrust and torques to 4 motor thrusts using the proper mixing matrix.
        
        The mixing matrix accounts for rotor positions and spin directions:
          thrust1: rear-left   (-0.14, -0.18) - CCW
          thrust2: front-left  (-0.14,  0.18) - CW
          thrust3: front-right ( 0.14,  0.18) - CCW
          thrust4: rear-right  ( 0.14, -0.18) - CW
        
        Torque conventions:
          tau_roll > 0: right side down (left side up)
          tau_pitch > 0: nose down (tail up)
          tau_yaw > 0: CCW rotation (viewed from above)
        
        Args:
            total_thrust: total thrust from all 4 motors (sum of individual thrusts)
            tau_roll: desired roll torque
            tau_pitch: desired pitch torque
            tau_yaw: desired yaw torque
        
        Returns: [thrust1, thrust2, thrust3, thrust4]
        """
        # Desired outputs: [F_total, tau_x, tau_y, tau_z]
        desired = np.array([total_thrust, tau_roll, tau_pitch, tau_yaw])
        
        # Use precomputed inverse mixing matrix to get individual thrusts
        thrusts = self.M_inv @ desired
        
        # Clamp to actuator range [0, 13]
        thrusts = np.clip(thrusts, 0, 13)
        
        return thrusts
    
    def _control_step(
        self,
        target_xyz: Optional[np.ndarray] = None,
        target_yaw: Optional[float] = None,
        use_attitude_control: bool = True
    ) -> None:
        """
        Execute one control step with cascaded PD controller.
        
        Args:
            target_xyz: desired position [x, y, z]. If None, use current target.
            target_yaw: desired yaw angle. If None, maintain current yaw.
            use_attitude_control: if True, stabilize attitude; if False, open-loop.
        """
        if target_xyz is not None:
            self._target_xy = target_xyz[:2].copy()
            self._target_z = target_xyz[2]
        
        xyz, R = self.get_base_pose()
        quat = self.data.qpos[3:7]
        roll, pitch, yaw = self._quat_to_euler(quat)
        
        # Outer loop: altitude PD
        z_error = self._target_z - xyz[2]
        z_vel = self.data.qvel[2]
        z_accel_des = self.kp_z * z_error - self.kd_z * z_vel
        
        # total_thrust is the SUM of all 4 motor thrusts
        total_thrust = self.hover_thrust_per_motor * 4 + self.total_mass * z_accel_des
        total_thrust = np.clip(total_thrust, 0, 13 * 4)
        
        # Outer loop: XY position PD
        xy_error = self._target_xy - xyz[:2]
        xy_vel = self.data.qvel[:2]
        xy_accel_des = self.kp_xy * xy_error - self.kd_xy * xy_vel
        
        # Convert desired acceleration to desired roll/pitch (small angle approx)
        # a_x = g * sin(pitch), a_y = -g * sin(roll)
        pitch_des = np.clip(xy_accel_des[0] / self.g, -self.max_tilt_angle, self.max_tilt_angle)
        roll_des = np.clip(-xy_accel_des[1] / self.g, -self.max_tilt_angle, self.max_tilt_angle)
        
        # Inner loop: attitude PD
        if use_attitude_control:
            roll_error = roll_des - roll
            pitch_error = pitch_des - pitch
            
            roll_rate = self.data.qvel[3]
            pitch_rate = self.data.qvel[4]
            yaw_rate = self.data.qvel[5]
            
            tau_roll = self.kp_att * roll_error - self.kd_att * roll_rate
            tau_pitch = self.kp_att * pitch_error - self.kd_att * pitch_rate
            
            # Yaw control (if target_yaw provided)
            tau_yaw = 0.0
            if target_yaw is not None:
                yaw_error = target_yaw - yaw
                # Wrap yaw error to [-pi, pi]
                yaw_error = np.arctan2(np.sin(yaw_error), np.cos(yaw_error))
                tau_yaw = self.kp_att * yaw_error - self.kd_att * yaw_rate
        else:
            tau_roll = 0.0
            tau_pitch = 0.0
            tau_yaw = 0.0
        
        # Mix to motor thrusts
        thrusts = self._compute_thrust_mix(total_thrust, tau_roll, tau_pitch, tau_yaw)
        self.data.ctrl[:] = thrusts
    
    # ========== High-Level Behaviors ==========
    
    def takeoff(self, height: float = 0.5) -> bool:
        """
        Takeoff to a target altitude and hover.
        
        Args:
            height: target altitude in meters
        
        Returns:
            True if successful, False otherwise
        """
        if height < 0.1:
            print("Error: target height too low")
            return False
        
        self._target_z = height
        self._target_xy = self.get_base_pose()[0][:2].copy()
        
        # Takeoff phase: ramp up to target altitude
        max_steps = 1000
        for step in range(max_steps):
            self._control_step()
            self.step(1)
            
            xyz, _ = self.get_base_pose()
            z = xyz[2]
            
            # Check if we've reached target
            if abs(z - height) < 0.05 and abs(self.data.qvel[2]) < 0.1:
                self._is_hovering = True
                return True
        
        # Timeout
        xyz, _ = self.get_base_pose()
        print(f"Takeoff timeout: reached z={xyz[2]:.3f}, target={height}")
        return False
    
    def move_to(self, x: float, y: float, z: float, tol: float = 0.1) -> bool:
        """
        Fly to a target position and hover.
        
        Args:
            x, y, z: target position in world frame
            tol: position tolerance in meters
        
        Returns:
            True if reached target, False otherwise
        """
        target = np.array([x, y, z])
        self._target_xy = target[:2].copy()
        self._target_z = target[2]
        
        max_steps = 1000
        for step in range(max_steps):
            self._control_step()
            self.step(1)
            
            xyz, _ = self.get_base_pose()
            dist = np.linalg.norm(target - xyz)
            
            # Check if we've reached target
            if dist < tol and np.linalg.norm(self.data.qvel[:3]) < 0.1:
                self._is_hovering = True
                return True
        
        # Timeout
        xyz, _ = self.get_base_pose()
        print(f"Move_to timeout: reached {xyz}, target={target}, dist={np.linalg.norm(target - xyz):.3f}")
        return False
    
    def hover(self, secs: float = 2.0) -> bool:
        """
        Hover at the current position for a duration.
        
        Args:
            secs: duration in seconds
        
        Returns:
            True if successful, False otherwise
        """
        xyz, _ = self.get_base_pose()
        self._target_xy = xyz[:2].copy()
        self._target_z = xyz[2]
        
        steps = int(secs / self.model.opt.timestep)
        for step in range(steps):
            self._control_step()
            self.step(1)
        
        self._is_hovering = True
        return True
    
    def land(self) -> bool:
        """
        Descend to the ground and idle the rotors.
        
        Returns:
            True if successful, False otherwise
        """
        xyz, _ = self.get_base_pose()
        target_z = 0.05  # Just above ground
        
        max_steps = 1000
        for step in range(max_steps):
            self._target_z = target_z
            self._control_step()
            self.step(1)
            
            xyz, _ = self.get_base_pose()
            z = xyz[2]
            
            # Check if we've landed
            if z < 0.06 and abs(self.data.qvel[2]) < 0.05:
                # Idle rotors
                self.data.ctrl[:] = 0
                self._is_hovering = False
                return True
        
        # Timeout
        xyz, _ = self.get_base_pose()
        print(f"Land timeout: reached z={xyz[2]:.3f}")
        return False
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get the world position of a named scene object.
        
        Resolves the name to a body, geom, or site via mujoco.mj_name2id.
        
        Args:
            name: name of the object (body, geom, or site)
        
        Returns:
            xyz position in world frame
        
        Raises:
            KeyError if the name is not found
        """
        # Try body first
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body_id >= 0:
            return self.data.xpos[body_id].copy()
        
        # Try geom
        geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if geom_id >= 0:
            return self.data.geom_xpos[geom_id].copy()
        
        # Try site
        site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
        if site_id >= 0:
            return self.data.site_xpos[site_id].copy()
        
        raise KeyError(f"Object '{name}' not found in scene")
    
    def get_object_names(self) -> list:
        """
        Return a list of manipulable scene object names.
        
        For a drone, this is typically empty (no manipulable objects).
        """
        # Collect all body, geom, and site names (excluding the drone itself)
        names = []
        
        for i in range(self.model.nbody):
            body_name = self.model.body(i).name
            if body_name not in ["world", "x2"]:
                names.append(body_name)
        
        for i in range(self.model.ngeom):
            geom_name = self.model.geom(i).name
            if geom_name and geom_name not in ["floor"]:
                names.append(geom_name)
        
        for i in range(self.model.nsite):
            site_name = self.model.site(i).name
            if site_name and site_name not in ["imu", "thrust1", "thrust2", "thrust3", "thrust4"]:
                names.append(site_name)
        
        return list(set(names))  # Remove duplicates


if __name__ == "__main__":
    # Example usage
    robot = Robot.build_from_mjcf("mjcf.xml")
    robot.home()
    print(robot.describe())
    
    # Test takeoff
    print("\n--- Testing takeoff ---")
    if robot.takeoff(height=0.5):
        print("Takeoff successful!")
    else:
        print("Takeoff failed!")
    
    print(robot.describe())
    
    # Test hover
    print("\n--- Testing hover ---")
    robot.hover(secs=1.0)
    print(robot.describe())
    
    # Test land
    print("\n--- Testing land ---")
    if robot.land():
        print("Land successful!")
    else:
        print("Land failed!")
    
    print(robot.describe())
