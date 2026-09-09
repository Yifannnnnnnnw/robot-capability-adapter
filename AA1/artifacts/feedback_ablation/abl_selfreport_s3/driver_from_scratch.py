"""
SO-101 5-DOF Robotic Arm Driver
Written from scratch without auto_adapter.skeletons imports.

This driver implements:
- Forward kinematics via MuJoCo
- Damped Least Squares (DLS) IK for position control (3-DOF task, 5-DOF robot)
- Cartesian motion planning with linear interpolation
- Gripper control (parallel jaw)
- Object detection and manipulation support
"""

import mujoco
import numpy as np
from typing import Tuple, Optional, List
import time
import os


class Robot:
    """SO-101 5-DOF robotic arm with parallel jaw gripper."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize robot with MuJoCo model and data.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        
        # Arm joint configuration
        self.arm_joint_names = ["shoulder_pan", "shoulder_lift", "elbow_flex", 
                                 "wrist_flex", "wrist_roll"]
        self.arm_joint_ids = [model.joint(name).id for name in self.arm_joint_names]
        self.arm_actuator_ids = [model.actuator(name).id for name in self.arm_joint_names]
        
        # End-effector site
        self.ee_site_name = "gripperframe"
        self.ee_site_id = model.site(self.ee_site_name).id
        
        # Gripper configuration
        self.gripper_joint_name = "gripper"
        self.gripper_joint_id = model.joint(self.gripper_joint_name).id
        self.gripper_actuator_id = model.actuator(self.gripper_joint_name).id
        self.gripper_qpos_adr = model.jnt_qposadr[self.gripper_joint_id]
        
        # Joint limits
        self.joint_limits = []
        for jid in self.arm_joint_ids:
            jnt_range = model.jnt_range[jid]
            self.joint_limits.append((jnt_range[0], jnt_range[1]))
        
        # Gripper limits
        gripper_range = model.jnt_range[self.gripper_joint_id]
        self.gripper_open_pos = gripper_range[1]  # Max value = open
        self.gripper_closed_pos = gripper_range[0]  # Min value = closed
        
        # IK parameters
        self.ik_damping = 0.05
        self.ik_max_iter = 100
        self.ik_tol = 1e-3
        
        # Home position (all zeros)
        self.home_qpos = np.zeros(len(self.arm_joint_ids))
        
        # Timestep
        self.dt = model.opt.timestep
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file path.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        # Resolve symlinks
        if os.path.islink(mjcf_path):
            mjcf_path = os.readlink(mjcf_path)
        
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """Move robot to home position (all joints at zero).
        
        Returns:
            True if successful
        """
        try:
            # Set target positions to zero
            for i, act_id in enumerate(self.arm_actuator_ids):
                self.data.ctrl[act_id] = 0.0
            
            # Open gripper
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_open_pos
            
            # Simulate for 2 seconds to reach home
            steps = int(2.0 / self.dt)
            for _ in range(steps):
                mujoco.mj_step(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions of the arm.
        
        Returns:
            Array of joint positions (5,)
        """
        qpos = np.zeros(len(self.arm_joint_ids))
        for i, jid in enumerate(self.arm_joint_ids):
            qpos_adr = self.model.jnt_qposadr[jid]
            qpos[i] = self.data.qpos[qpos_adr]
        return qpos
    
    def set_joint_positions(self, qpos: np.ndarray) -> None:
        """Set joint positions directly in qpos (for IK).
        
        Args:
            qpos: Joint positions (5,)
        """
        for i, jid in enumerate(self.arm_joint_ids):
            qpos_adr = self.model.jnt_qposadr[jid]
            self.data.qpos[qpos_adr] = qpos[i]
    
    def step(self, n: int = 1) -> None:
        """Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """Render the current scene.
        
        Returns:
            RGB image array or None if no renderer available
        """
        # Basic rendering support - would need a renderer instance
        # For now, return None as we don't have a viewer set up
        return None
    
    def describe(self) -> str:
        """Return a description of the robot.
        
        Returns:
            Description string
        """
        ee_pos, ee_rot = self.get_ee_pose()
        joint_pos = self.get_joint_positions()
        
        desc = "SO-101 5-DOF Robotic Arm\n"
        desc += "=" * 40 + "\n"
        desc += f"DOF: {len(self.arm_joint_ids)}\n"
        desc += f"Joints: {', '.join(self.arm_joint_names)}\n"
        desc += f"End-effector site: {self.ee_site_name}\n"
        desc += f"\nCurrent State:\n"
        desc += f"  Joint positions: {joint_pos}\n"
        desc += f"  EE position: {ee_pos}\n"
        desc += f"  Gripper position: {self.data.qpos[self.gripper_qpos_adr]:.4f}\n"
        return desc
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get end-effector pose (position and orientation).
        
        Returns:
            Tuple of (position (3,), rotation matrix (3, 3))
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return pos, rot
    
    def _compute_jacobian(self, site_id: int, joint_ids: List[int]) -> np.ndarray:
        """Compute Jacobian for a site with respect to specified joints.
        
        Args:
            site_id: Site ID
            joint_ids: List of joint IDs
            
        Returns:
            Jacobian matrix (6, n_joints)
        """
        nv = self.model.nv
        n_joints = len(joint_ids)
        
        # Full Jacobian (6 x nv)
        jac_pos = np.zeros((3, nv))
        jac_rot = np.zeros((3, nv))
        mujoco.mj_jacSite(self.model, self.data, jac_pos, jac_rot, site_id)
        
        # Extract columns for our joints
        J = np.zeros((6, n_joints))
        for i, jid in enumerate(joint_ids):
            dof_adr = self.model.jnt_dofadr[jid]
            J[0:3, i] = jac_pos[:, dof_adr]
            J[3:6, i] = jac_rot[:, dof_adr]
        
        return J
    
    def _solve_ik(self, target_pos: np.ndarray, 
                  max_iter: Optional[int] = None,
                  tol: Optional[float] = None,
                  damping: Optional[float] = None) -> Tuple[bool, float]:
        """Solve inverse kinematics using Damped Least Squares.
        
        This is a position-only IK for a 5-DOF arm (3-DOF task, 2 DOF redundancy).
        Uses DLS with adaptive damping based on error magnitude.
        
        Args:
            target_pos: Target position (3,)
            max_iter: Maximum iterations (default: self.ik_max_iter)
            tol: Convergence tolerance (default: self.ik_tol)
            damping: Damping factor (default: self.ik_damping)
            
        Returns:
            Tuple of (success, final_error)
        """
        if max_iter is None:
            max_iter = self.ik_max_iter
        if tol is None:
            tol = self.ik_tol
        if damping is None:
            damping = self.ik_damping
        
        for iteration in range(max_iter):
            # Forward kinematics
            mujoco.mj_forward(self.model, self.data)
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Position error
            error = target_pos - current_pos
            error_norm = np.linalg.norm(error)
            
            if error_norm < tol:
                return True, error_norm
            
            # Compute Jacobian (position part only for 5-DOF)
            J_full = self._compute_jacobian(self.ee_site_id, self.arm_joint_ids)
            J = J_full[0:3, :]  # Position only (3 x 5)
            
            # Adaptive damping: increase damping for larger errors
            adaptive_damping = damping * (1.0 + error_norm)
            damping_sq = adaptive_damping ** 2
            
            # Damped least squares: dq = J^T (J J^T + λ^2 I)^-1 error
            JJt = J @ J.T  # (3 x 3)
            try:
                dq = J.T @ np.linalg.solve(JJt + damping_sq * np.eye(3), error)
            except np.linalg.LinAlgError:
                return False, error_norm
            
            # Limit step size to prevent large jumps
            max_step = 0.5  # radians
            dq_norm = np.linalg.norm(dq)
            if dq_norm > max_step:
                dq = dq * (max_step / dq_norm)
            
            # Update joint positions with clamping
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_adr = self.model.jnt_qposadr[jid]
                new_q = self.data.qpos[qpos_adr] + dq[i]
                # Clamp to limits
                new_q = np.clip(new_q, self.joint_limits[i][0], self.joint_limits[i][1])
                self.data.qpos[qpos_adr] = new_q
        
        # Did not converge
        mujoco.mj_forward(self.model, self.data)
        current_pos = self.data.site_xpos[self.ee_site_id].copy()
        final_error = np.linalg.norm(target_pos - current_pos)
        return False, final_error
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """Move end-effector to target Cartesian position.
        
        Uses linear interpolation in Cartesian space with IK at each waypoint.
        
        Args:
            target_xyz: Target position (3,)
            duration: Duration of motion in seconds
            
        Returns:
            True if successful
        """
        try:
            # Get current position
            current_pos, _ = self.get_ee_pose()
            
            # Number of waypoints
            n_waypoints = max(10, int(duration / (self.dt * 10)))
            
            # Linear interpolation
            for i in range(n_waypoints + 1):
                alpha = i / n_waypoints
                waypoint = current_pos + alpha * (target_xyz - current_pos)
                
                # Solve IK for this waypoint
                success, error = self._solve_ik(waypoint, max_iter=50, tol=0.005)
                
                if not success and error > 0.02:
                    print(f"Warning: IK failed at waypoint {i}/{n_waypoints}, error: {error:.4f}")
                    return False
                
                # Set actuator targets to current joint positions
                for j, act_id in enumerate(self.arm_actuator_ids):
                    qpos_adr = self.model.jnt_qposadr[self.arm_joint_ids[j]]
                    self.data.ctrl[act_id] = self.data.qpos[qpos_adr]
                
                # Simulate a few steps
                steps_per_waypoint = max(1, int(duration / (n_waypoints * self.dt)))
                for _ in range(steps_per_waypoint):
                    mujoco.mj_step(self.model, self.data)
            
            # Final check
            final_pos, _ = self.get_ee_pose()
            final_error = np.linalg.norm(final_pos - target_xyz)
            
            if final_error > 0.02:
                print(f"Warning: Final position error: {final_error:.4f} m")
                return False
            
            return True
            
        except Exception as e:
            print(f"Error in move_cartesian(): {e}")
            return False
    
    def gripper_open(self) -> bool:
        """Open the gripper.
        
        Returns:
            True if successful
        """
        try:
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_open_pos
            # Simulate for 0.5 seconds
            steps = int(0.5 / self.dt)
            for _ in range(steps):
                mujoco.mj_step(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_open(): {e}")
            return False
    
    def gripper_close(self) -> bool:
        """Close the gripper.
        
        Returns:
            True if successful
        """
        try:
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_closed_pos
            # Simulate for 0.5 seconds
            steps = int(0.5 / self.dt)
            for _ in range(steps):
                mujoco.mj_step(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_close(): {e}")
            return False
    
    def is_holding(self) -> bool:
        """Check if gripper is holding an object.
        
        This is a simple heuristic: if the gripper is not fully closed
        but the actuator is commanding closed position, it's likely holding something.
        
        Returns:
            True if likely holding an object
        """
        gripper_pos = self.data.qpos[self.gripper_qpos_adr]
        gripper_cmd = self.data.ctrl[self.gripper_actuator_id]
        
        # If commanding closed but not fully closed, likely holding
        threshold = 0.3  # radians from fully closed
        if gripper_cmd < (self.gripper_closed_pos + 0.1):
            if gripper_pos > (self.gripper_closed_pos + threshold):
                return True
        
        return False
    
    def get_object_position(self, name: str) -> np.ndarray:
        """Get world position of a named scene object.
        
        Searches for the name in bodies, then geoms, then sites.
        
        Args:
            name: Object name
            
        Returns:
            World position (3,)
            
        Raises:
            KeyError: If object name not found
        """
        # Try to find as body
        try:
            body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            if body_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.xpos[body_id].copy()
        except:
            pass
        
        # Try to find as geom
        try:
            geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            if geom_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.geom_xpos[geom_id].copy()
        except:
            pass
        
        # Try to find as site
        try:
            site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
            if site_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.site_xpos[site_id].copy()
        except:
            pass
        
        raise KeyError(f"Object '{name}' not found in scene (searched bodies, geoms, sites)")
    
    def get_object_names(self) -> List[str]:
        """Get names of manipulable scene objects.
        
        Returns bodies that are not part of the robot itself.
        
        Returns:
            List of object names
        """
        robot_body_names = {"world", "base", "shoulder", "upper_arm", "lower_arm", 
                           "wrist", "gripper", "camera_mount", "moving_jaw_so101_v1"}
        
        object_names = []
        for i in range(self.model.nbody):
            body_name = self.model.body(i).name
            if body_name and body_name not in robot_body_names:
                object_names.append(body_name)
        
        return object_names


if __name__ == "__main__":
    # Simple test
    print("Testing SO-101 Robot Driver")
    print("=" * 50)
    
    # Build robot
    robot = Robot.build_from_mjcf("mjcf.xml")
    print("Robot built successfully")
    
    # Home
    print("\nHoming robot...")
    robot.home()
    print("Home complete")
    
    # Describe
    print("\n" + robot.describe())
    
    # Test EE pose
    ee_pos, ee_rot = robot.get_ee_pose()
    print(f"\nEE Position: {ee_pos}")
    print(f"EE Orientation:\n{ee_rot}")
    
    # Test gripper
    print("\nTesting gripper...")
    robot.gripper_close()
    print(f"Gripper closed: {robot.data.qpos[robot.gripper_qpos_adr]:.4f}")
    robot.gripper_open()
    print(f"Gripper opened: {robot.data.qpos[robot.gripper_qpos_adr]:.4f}")
    
    # Test object names
    print(f"\nObjects in scene: {robot.get_object_names()}")
    
    print("\n" + "=" * 50)
    print("Driver test complete!")
