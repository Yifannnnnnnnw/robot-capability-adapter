"""
driver_from_scratch.py

Complete robot driver for SO-101 5-DOF arm with gripper.
Written from scratch using only mujoco, numpy, and stdlib.

IK Method: Damped Least Squares (DLS) for position-only control.
- 5-DOF arm is non-redundant for 3D position targets
- Adaptive damping: λ = λ_init * (1 + ||error||)
- Step clamping to prevent large jumps
- Joint limit enforcement
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import os


class Robot:
    """SO-101 robotic arm with 5-DOF arm and 1-DOF gripper."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self.viewer = None
        
        # Arm joints (first 5)
        self.arm_joint_names = [
            "shoulder_pan", "shoulder_lift", "elbow_flex", 
            "wrist_flex", "wrist_roll"
        ]
        self.arm_dof = 5
        
        # Gripper joint (6th)
        self.gripper_joint_name = "gripper"
        self.gripper_joint_idx = 5
        
        # Resolve joint indices
        self.arm_joint_indices = []
        for name in self.arm_joint_names:
            jnt_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jnt_id < 0:
                raise ValueError(f"Joint {name} not found")
            qpos_adr = model.jnt_qposadr[jnt_id]
            self.arm_joint_indices.append(qpos_adr)
        
        # Resolve actuator indices
        self.arm_actuator_indices = []
        for name in self.arm_joint_names:
            act_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if act_id < 0:
                raise ValueError(f"Actuator {name} not found")
            self.arm_actuator_indices.append(act_id)
        
        self.gripper_actuator_idx = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, self.gripper_joint_name
        )
        
        # End-effector site
        self.ee_site_name = "gripperframe"
        self.ee_site_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_SITE, self.ee_site_name
        )
        if self.ee_site_id < 0:
            raise ValueError(f"EE site {self.ee_site_name} not found")
        
        # Joint limits
        self.arm_joint_limits = []
        for i in range(self.arm_dof):
            self.arm_joint_limits.append(
                (model.jnt_range[i, 0], model.jnt_range[i, 1])
            )
        
        self.gripper_limits = (
            model.jnt_range[self.gripper_joint_idx, 0],
            model.jnt_range[self.gripper_joint_idx, 1]
        )
        
        # Home configuration (all zeros is a good home for this robot)
        self.home_config = np.zeros(self.arm_dof)
        
        # Gripper state
        self.gripper_open_pos = 1.5  # Open position
        self.gripper_closed_pos = -0.1  # Closed position
        
        # IK parameters
        self.ik_max_iter = 100
        self.ik_tol = 1e-3
        self.ik_lambda_init = 0.01
        self.ik_max_step = 0.3
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """Build robot from MJCF file."""
        # Handle symlinks and relative paths
        mjcf_path = os.path.realpath(mjcf_path)
        
        # Change to the directory containing the MJCF to resolve includes
        original_dir = os.getcwd()
        mjcf_dir = os.path.dirname(mjcf_path)
        mjcf_file = os.path.basename(mjcf_path)
        
        try:
            os.chdir(mjcf_dir)
            model = mujoco.MjModel.from_xml_path(mjcf_file)
            data = mujoco.MjData(model)
        finally:
            os.chdir(original_dir)
        
        return cls(model, data)
    
    def home(self) -> bool:
        """Move to home configuration."""
        try:
            # Set arm to home
            for i, idx in enumerate(self.arm_joint_indices):
                self.data.qpos[idx] = self.home_config[i]
            
            # Open gripper
            self.data.qpos[self.gripper_joint_idx] = self.gripper_open_pos
            
            # Set control targets
            for i, act_idx in enumerate(self.arm_actuator_indices):
                self.data.ctrl[act_idx] = self.home_config[i]
            self.data.ctrl[self.gripper_actuator_idx] = self.gripper_open_pos
            
            # Step simulation to settle
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current arm joint positions."""
        positions = np.zeros(self.arm_dof)
        for i, idx in enumerate(self.arm_joint_indices):
            positions[i] = self.data.qpos[idx]
        return positions
    
    def step(self, n: int = 1):
        """Step the simulation."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self):
        """Render the scene (passive viewer)."""
        if self.viewer is None:
            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        self.viewer.sync()
    
    def describe(self) -> str:
        """Return a description of the robot."""
        ee_pos, ee_rot = self.get_ee_pose()
        joint_pos = self.get_joint_positions()
        gripper_pos = self.data.qpos[self.gripper_joint_idx]
        
        desc = "SO-101 Robotic Arm\n"
        desc += "=" * 50 + "\n"
        desc += f"DOF: {self.arm_dof} (arm) + 1 (gripper)\n"
        desc += f"Arm joints: {', '.join(self.arm_joint_names)}\n"
        desc += f"EE site: {self.ee_site_name}\n"
        desc += f"\nCurrent state:\n"
        desc += f"  Joint positions: {joint_pos}\n"
        desc += f"  Gripper position: {gripper_pos:.3f}\n"
        desc += f"  EE position: {ee_pos}\n"
        desc += f"  EE orientation:\n{ee_rot}\n"
        return desc
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get end-effector pose (position, rotation matrix)."""
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return pos, rot
    
    def _solve_ik(
        self, 
        target_pos: np.ndarray, 
        q_init: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, bool]:
        """
        Solve inverse kinematics using Damped Least Squares.
        
        Args:
            target_pos: Target 3D position for end-effector
            q_init: Initial joint configuration (uses current if None)
        
        Returns:
            (q_solution, success)
        """
        if q_init is None:
            q_init = self.get_joint_positions()
        
        q = q_init.copy()
        
        for iteration in range(self.ik_max_iter):
            # Forward kinematics
            for i, idx in enumerate(self.arm_joint_indices):
                self.data.qpos[idx] = q[i]
            mujoco.mj_forward(self.model, self.data)
            
            # Current EE position
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Position error
            error = target_pos - current_pos
            error_norm = np.linalg.norm(error)
            
            if error_norm < self.ik_tol:
                return q, True
            
            # Compute Jacobian
            jacp = np.zeros((3, self.model.nv))
            jacr = np.zeros((3, self.model.nv))
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site_id)
            
            # Use only arm joints
            J = jacp[:, :self.arm_dof]
            
            # Adaptive damping based on error magnitude
            lambda_damp = self.ik_lambda_init * (1.0 + error_norm)
            
            # Damped Least Squares: dq = J^T (J J^T + λ^2 I)^-1 error
            JJT = J @ J.T
            damped = JJT + lambda_damp**2 * np.eye(3)
            dq = J.T @ np.linalg.solve(damped, error)
            
            # Limit step size to prevent large jumps
            dq_norm = np.linalg.norm(dq)
            if dq_norm > self.ik_max_step:
                dq = dq * (self.ik_max_step / dq_norm)
            
            # Update joint angles
            q += dq
            
            # Clamp to joint limits
            for i in range(self.arm_dof):
                q[i] = np.clip(q[i], self.arm_joint_limits[i][0], 
                              self.arm_joint_limits[i][1])
        
        # Did not converge
        return q, False
    
    def move_cartesian(
        self, 
        target_xyz: np.ndarray, 
        duration: float = 2.0
    ) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Args:
            target_xyz: Target 3D position [x, y, z]
            duration: Time to complete motion (seconds)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Solve IK
            q_target, success = self._solve_ik(target_xyz)
            
            if not success:
                print(f"IK failed to converge for target {target_xyz}")
                return False
            
            # Get current configuration
            q_start = self.get_joint_positions()
            
            # Interpolate and execute
            steps = int(duration * 500)  # Assuming 500 Hz control
            for i in range(steps):
                alpha = (i + 1) / steps
                q_interp = (1 - alpha) * q_start + alpha * q_target
                
                # Set control targets
                for j, act_idx in enumerate(self.arm_actuator_indices):
                    self.data.ctrl[act_idx] = q_interp[j]
                
                mujoco.mj_step(self.model, self.data)
            
            # Verify final position
            final_pos, _ = self.get_ee_pose()
            error = np.linalg.norm(final_pos - target_xyz)
            
            if error > 0.02:  # 2cm tolerance
                print(f"Warning: Final position error {error:.4f}m")
                return False
            
            return True
            
        except Exception as e:
            print(f"Error in move_cartesian(): {e}")
            return False
    
    def gripper_open(self) -> bool:
        """Open the gripper."""
        try:
            self.data.ctrl[self.gripper_actuator_idx] = self.gripper_open_pos
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_open(): {e}")
            return False
    
    def gripper_close(self) -> bool:
        """Close the gripper."""
        try:
            self.data.ctrl[self.gripper_actuator_idx] = self.gripper_closed_pos
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_close(): {e}")
            return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        Simple heuristic: if gripper is partially closed (not fully open),
        assume it's holding something.
        """
        gripper_pos = self.data.qpos[self.gripper_joint_idx]
        # If gripper is more than halfway closed, consider it holding
        threshold = (self.gripper_open_pos + self.gripper_closed_pos) / 2
        return gripper_pos < threshold
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get world position of a named scene object.
        
        Args:
            name: Object name (body, geom, or site)
        
        Returns:
            3D position [x, y, z]
        
        Raises:
            KeyError: If object name is not found
        """
        # Try body first
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.xpos[body_id].copy()
        
        # Try geom
        geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if geom_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.geom_xpos[geom_id].copy()
        
        # Try site
        site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
        if site_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.site_xpos[site_id].copy()
        
        raise KeyError(f"Object '{name}' not found in model")
    
    def get_object_names(self) -> list:
        """
        Get names of manipulable scene objects.
        
        Returns bodies that are not part of the robot itself.
        """
        robot_bodies = {
            "world", "base", "shoulder", "upper_arm", "lower_arm", 
            "wrist", "gripper", "camera_mount", "moving_jaw_so101_v1"
        }
        
        object_names = []
        for i in range(self.model.nbody):
            body_name = self.model.body(i).name
            if body_name and body_name not in robot_bodies:
                object_names.append(body_name)
        
        return object_names


def main():
    """Test the robot driver."""
    print("Building robot from MJCF...")
    robot = Robot.build_from_mjcf("mjcf.xml")
    
    print("\nMoving to home position...")
    robot.home()
    
    print("\n" + robot.describe())
    
    print("\n=== Testing IK ===")
    ee_pos, _ = robot.get_ee_pose()
    print(f"Current EE position: {ee_pos}")
    
    # Test 1: Move +5cm in X
    print("\nTest 1: Move +5cm in X")
    target = ee_pos + np.array([0.05, 0.0, 0.0])
    success = robot.move_cartesian(target, duration=1.0)
    print(f"Success: {success}")
    
    final_pos, _ = robot.get_ee_pose()
    error = np.linalg.norm(final_pos - target)
    print(f"Final position: {final_pos}")
    print(f"Target position: {target}")
    print(f"Error: {error:.6f}m")
    
    # Test 2: Return to home
    print("\nTest 2: Return to home")
    robot.home()
    final_pos, _ = robot.get_ee_pose()
    error = np.linalg.norm(final_pos - ee_pos)
    print(f"Final position: {final_pos}")
    print(f"Error from original: {error:.6f}m")
    
    # Test gripper
    print("\n=== Testing Gripper ===")
    print("Closing gripper...")
    robot.gripper_close()
    print(f"Is holding: {robot.is_holding()}")
    
    print("Opening gripper...")
    robot.gripper_open()
    print(f"Is holding: {robot.is_holding()}")
    
    # Test object queries
    print("\n=== Testing Object Queries ===")
    object_names = robot.get_object_names()
    print(f"Scene objects: {object_names}")
    
    print("\nDriver test complete!")


if __name__ == "__main__":
    main()
