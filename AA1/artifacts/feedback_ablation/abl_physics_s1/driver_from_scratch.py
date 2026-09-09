"""
SO-101 5-DOF Robotic Arm Driver
Written from scratch without auto_adapter.skeletons imports.

This driver implements:
- Forward kinematics via MuJoCo
- Damped Least Squares (DLS) inverse kinematics for Cartesian control
- Position-controlled gripper
- Scene object tracking for manipulation tasks
"""

import mujoco
import numpy as np
from typing import Tuple, Optional, List
import time


class Robot:
    """SO-101 5-DOF robotic arm with gripper."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self.viewer = None
        
        # Arm configuration
        self.arm_joint_names = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 
                                'wrist_flex', 'wrist_roll']
        self.arm_joint_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) 
                              for name in self.arm_joint_names]
        self.arm_actuator_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                                 for name in self.arm_joint_names]
        
        # Gripper configuration
        self.gripper_joint_name = 'gripper'
        self.gripper_joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, 
                                                   self.gripper_joint_name)
        self.gripper_actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR,
                                                      self.gripper_joint_name)
        
        # End-effector site
        self.ee_site_name = 'gripperframe'
        self.ee_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, 
                                            self.ee_site_name)
        
        # Joint limits
        self.joint_limits = []
        for jid in self.arm_joint_ids:
            self.joint_limits.append(model.jnt_range[jid].copy())
        
        # Gripper limits
        self.gripper_limits = model.jnt_range[self.gripper_joint_id].copy()
        
        # Home position (all zeros is a reasonable home for this arm)
        self.home_qpos = np.zeros(len(self.arm_joint_ids))
        
        # IK parameters
        self.ik_damping = 0.1
        self.ik_max_iter = 100
        self.ik_tol = 1e-3
        self.ik_max_step = 0.1
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file."""
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """Move to home position."""
        try:
            # Set arm joints to home position
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_addr = self.model.jnt_qposadr[jid]
                self.data.qpos[qpos_addr] = self.home_qpos[i]
                self.data.ctrl[self.arm_actuator_ids[i]] = self.home_qpos[i]
            
            # Open gripper
            gripper_qpos_addr = self.model.jnt_qposadr[self.gripper_joint_id]
            self.data.qpos[gripper_qpos_addr] = self.gripper_limits[1]  # Open
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_limits[1]
            
            # Step simulation to settle
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current arm joint positions."""
        qpos = np.zeros(len(self.arm_joint_ids))
        for i, jid in enumerate(self.arm_joint_ids):
            qpos_addr = self.model.jnt_qposadr[jid]
            qpos[i] = self.data.qpos[qpos_addr]
        return qpos
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get end-effector pose (position, rotation matrix)."""
        mujoco.mj_forward(self.model, self.data)
        
        # Position
        pos = self.data.site_xpos[self.ee_site_id].copy()
        
        # Rotation matrix
        rot_mat = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        
        return pos, rot_mat
    
    def _compute_ik_dls(self, target_pos: np.ndarray, 
                        initial_qpos: Optional[np.ndarray] = None) -> Tuple[bool, np.ndarray]:
        """
        Compute inverse kinematics using Damped Least Squares.
        
        Args:
            target_pos: Target 3D position for end-effector
            initial_qpos: Initial joint configuration (uses current if None)
            
        Returns:
            (success, final_qpos)
        """
        # Set initial configuration
        if initial_qpos is not None:
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_addr = self.model.jnt_qposadr[jid]
                self.data.qpos[qpos_addr] = initial_qpos[i]
        
        n_joints = len(self.arm_joint_ids)
        
        for iteration in range(self.ik_max_iter):
            # Forward kinematics
            mujoco.mj_forward(self.model, self.data)
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Position error
            error = target_pos - current_pos
            error_norm = np.linalg.norm(error)
            
            if error_norm < self.ik_tol:
                # Success
                qpos = self.get_joint_positions()
                return True, qpos
            
            # Compute Jacobian (position only, 3xN)
            jac = np.zeros((3, self.model.nv))
            mujoco.mj_jacSite(self.model, self.data, jac, None, self.ee_site_id)
            
            # Extract columns for arm joints
            jac_arm = jac[:, self.arm_joint_ids]
            
            # Adaptive damping: increase damping for large errors
            damping = self.ik_damping * (1.0 + error_norm)
            damping_sq = damping ** 2
            
            # DLS: dq = J^T (JJ^T + λ²I)^-1 * error
            JJT = jac_arm @ jac_arm.T + damping_sq * np.eye(3)
            dq = jac_arm.T @ np.linalg.solve(JJT, error)
            
            # Step size limiting
            dq_norm = np.linalg.norm(dq)
            if dq_norm > self.ik_max_step:
                dq = dq * (self.ik_max_step / dq_norm)
            
            # Update joint positions
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_addr = self.model.jnt_qposadr[jid]
                self.data.qpos[qpos_addr] += dq[i]
                
                # Clamp to joint limits
                jnt_range = self.joint_limits[i]
                self.data.qpos[qpos_addr] = np.clip(self.data.qpos[qpos_addr], 
                                                     jnt_range[0], jnt_range[1])
        
        # Did not converge
        qpos = self.get_joint_positions()
        return False, qpos
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Args:
            target_xyz: Target 3D position [x, y, z]
            duration: Time to complete motion (seconds)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Compute IK
            success, target_qpos = self._compute_ik_dls(target_xyz)
            
            if not success:
                print(f"IK failed to converge for target {target_xyz}")
                # Check if we're close enough anyway
                current_pos, _ = self.get_ee_pose()
                error = np.linalg.norm(target_xyz - current_pos)
                if error > 0.02:  # 2cm tolerance
                    return False
                print(f"Proceeding with error {error:.4f} m")
            
            # Interpolate from current to target position
            start_qpos = self.get_joint_positions()
            n_steps = int(duration / self.model.opt.timestep)
            
            for step in range(n_steps):
                alpha = (step + 1) / n_steps
                interp_qpos = (1 - alpha) * start_qpos + alpha * target_qpos
                
                # Set control targets
                for i, act_id in enumerate(self.arm_actuator_ids):
                    self.data.ctrl[act_id] = interp_qpos[i]
                
                mujoco.mj_step(self.model, self.data)
            
            # Verify final position
            final_pos, _ = self.get_ee_pose()
            error = np.linalg.norm(target_xyz - final_pos)
            
            if error > 0.02:  # 2cm tolerance
                print(f"Warning: Final position error {error:.4f} m")
                return False
            
            return True
            
        except Exception as e:
            print(f"Error in move_cartesian(): {e}")
            return False
    
    def gripper_open(self) -> bool:
        """Open the gripper."""
        try:
            # Set gripper to open position (max value)
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_limits[1]
            
            # Step simulation
            for _ in range(50):
                mujoco.mj_step(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in gripper_open(): {e}")
            return False
    
    def gripper_close(self) -> bool:
        """Close the gripper."""
        try:
            # Set gripper to closed position (min value)
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_limits[0]
            
            # Step simulation
            for _ in range(50):
                mujoco.mj_step(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in gripper_close(): {e}")
            return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        This is a simple heuristic: if the gripper joint is not at its
        closed limit, we assume something is preventing it from closing fully.
        """
        gripper_qpos_addr = self.model.jnt_qposadr[self.gripper_joint_id]
        current_gripper_pos = self.data.qpos[gripper_qpos_addr]
        
        # If gripper is commanded closed but not at the limit, likely holding something
        commanded_pos = self.data.ctrl[self.gripper_actuator_id]
        
        # Check if commanded to close and not fully closed
        if commanded_pos < 0.0 and current_gripper_pos > (self.gripper_limits[0] + 0.1):
            return True
        
        return False
    
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
        mujoco.mj_forward(self.model, self.data)
        
        # Try body first
        try:
            body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            if body_id >= 0:
                return self.data.xpos[body_id].copy()
        except:
            pass
        
        # Try geom
        try:
            geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            if geom_id >= 0:
                return self.data.geom_xpos[geom_id].copy()
        except:
            pass
        
        # Try site
        try:
            site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
            if site_id >= 0:
                return self.data.site_xpos[site_id].copy()
        except:
            pass
        
        raise KeyError(f"Object '{name}' not found in scene (tried body, geom, site)")
    
    def get_object_names(self) -> List[str]:
        """
        Get names of manipulable scene objects.
        
        Returns objects that are not part of the robot itself.
        """
        object_names = []
        
        # Robot body names to exclude
        robot_bodies = {'world', 'base', 'shoulder', 'upper_arm', 'lower_arm', 
                       'wrist', 'gripper', 'camera_mount', 'moving_jaw_so101_v1'}
        
        # Add non-robot bodies
        for i in range(self.model.nbody):
            body_name = self.model.body(i).name
            if body_name and body_name not in robot_bodies:
                object_names.append(body_name)
        
        # Add named geoms (excluding robot parts and floor)
        for i in range(self.model.ngeom):
            geom_name = self.model.geom(i).name
            if geom_name and geom_name not in ['floor', ''] and not any(
                robot_part in geom_name for robot_part in 
                ['jaw', 'camera', 'fixed', 'moving']):
                object_names.append(geom_name)
        
        return object_names
    
    def step(self, n: int = 1):
        """Step the simulation forward."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self):
        """Render the scene (creates viewer if needed)."""
        if self.viewer is None:
            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        
        if self.viewer is not None:
            self.viewer.sync()
    
    def describe(self) -> str:
        """Return a description of the robot."""
        ee_pos, ee_rot = self.get_ee_pose()
        joint_pos = self.get_joint_positions()
        
        desc = "SO-101 5-DOF Robotic Arm\n"
        desc += "=" * 40 + "\n"
        desc += f"DOF: {len(self.arm_joint_ids)}\n"
        desc += f"Arm joints: {', '.join(self.arm_joint_names)}\n"
        desc += f"End-effector site: {self.ee_site_name}\n"
        desc += f"\nCurrent state:\n"
        desc += f"  Joint positions: {joint_pos}\n"
        desc += f"  EE position: {ee_pos}\n"
        desc += f"  Gripper: {'holding' if self.is_holding() else 'empty'}\n"
        desc += f"\nIK configuration:\n"
        desc += f"  Method: Damped Least Squares (DLS)\n"
        desc += f"  Damping: {self.ik_damping} (adaptive)\n"
        desc += f"  Max iterations: {self.ik_max_iter}\n"
        desc += f"  Tolerance: {self.ik_tol} m\n"
        desc += f"  Max step: {self.ik_max_step} rad\n"
        
        return desc


if __name__ == "__main__":
    # Simple test
    robot = Robot.build_from_mjcf("scene.xml")
    robot.home()
    print(robot.describe())
