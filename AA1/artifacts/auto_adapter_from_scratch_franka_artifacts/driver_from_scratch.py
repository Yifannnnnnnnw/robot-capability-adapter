"""
Franka Panda Robot Driver - From Scratch Implementation
========================================================

This driver implements a complete robot control system for the Franka Panda
7-DOF manipulator with parallel-jaw gripper, using only mujoco, numpy, and
standard library.

Key features:
- Forward Kinematics: Direct MuJoCo xpos/xmat queries
- Inverse Kinematics: Damped Least Squares (DLS) with adaptive damping
- Motion Control: Linear interpolation with smooth velocity profiles
- Gripper Control: Parallel-jaw actuator via tendon
- Grasp Detection: Simple force-based heuristic

Author: Auto-generated driver
"""

import mujoco
import numpy as np
import json
import os
from typing import Optional, Tuple
from pathlib import Path


class Robot:
    """
    Franka Panda 7-DOF manipulator driver.
    
    This class provides a complete interface for controlling the Franka Panda
    robot in MuJoCo simulation, including FK, IK, motion planning, and gripper
    control.
    """
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, study: dict):
        """
        Initialize robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
            study: Study dictionary with robot configuration
        """
        self.model = model
        self.data = data
        self.study = study
        
        # Parse study info
        self.dof = study['dof']
        self.arm_joint_names = study['arm_joint_names']
        self.arm_actuator_names = study['arm_actuator_names']
        self.ee_body_name = study['ee_body']
        self.gripper_actuator_names = study['gripper_actuator_names']
        
        # Resolve indices
        self._resolve_indices()
        
        # Parse joint limits
        self.joint_limits = np.array([
            study['joint_limits'][jname] for jname in self.arm_joint_names
        ])
        
        # Home configuration (from keyframe)
        self.home_q = np.array([0, 0, 0, -1.57079, 0, 1.57079, -0.7853])
        
        # IK parameters
        self.ik_max_iter = 200
        self.ik_tol = 1e-3  # 1mm tolerance
        self.ik_lambda_init = 0.01
        
        # Gripper parameters
        self.gripper_open_pos = 255.0  # From ctrlrange
        self.gripper_close_pos = 0.0
        self.gripper_force_threshold = 5.0  # Force threshold for grasp detection
        
        # Motion parameters
        self.control_timestep = self.model.opt.timestep
        
        print(f"Robot initialized: {self.dof}-DOF arm with gripper")
    
    def _resolve_indices(self):
        """Resolve MuJoCo object indices from names."""
        # Arm joint indices and qpos addresses
        self.arm_joint_ids = []
        self.arm_qpos_addrs = []
        for jname in self.arm_joint_names:
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            self.arm_joint_ids.append(jid)
            self.arm_qpos_addrs.append(self.model.jnt_qposadr[jid])
        
        # Arm actuator indices
        self.arm_actuator_ids = []
        for aname in self.arm_actuator_names:
            aid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, aname)
            self.arm_actuator_ids.append(aid)
        
        # Gripper actuator indices
        self.gripper_actuator_ids = []
        for aname in self.gripper_actuator_names:
            aid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, aname)
            self.gripper_actuator_ids.append(aid)
        
        # EE body index
        self.ee_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, self.ee_body_name
        )
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF scene file
            
        Returns:
            Robot instance
        """
        mjcf_path = Path(mjcf_path).resolve()
        
        # If it's a symlink, resolve it
        if mjcf_path.is_symlink():
            mjcf_path = mjcf_path.resolve()
        
        # Load model (need to change directory for relative includes)
        original_dir = os.getcwd()
        try:
            os.chdir(mjcf_path.parent)
            model = mujoco.MjModel.from_xml_path(str(mjcf_path.name))
        finally:
            os.chdir(original_dir)
        
        data = mujoco.MjData(model)
        
        # Load study.json from original directory (not symlink target)
        study_path = Path(original_dir) / 'study.json'
        with open(study_path, 'r') as f:
            study = json.load(f)
        
        return cls(model, data, study)
    
    # ==========================================================================
    # Joint-level Control
    # ==========================================================================
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current arm joint positions."""
        return np.array([self.data.qpos[addr] for addr in self.arm_qpos_addrs])
    
    def set_joint_positions(self, q: np.ndarray):
        """Set arm joint positions (and update controls to match)."""
        q = np.clip(q, self.joint_limits[:, 0], self.joint_limits[:, 1])
        for i, addr in enumerate(self.arm_qpos_addrs):
            self.data.qpos[addr] = q[i]
        for i, aid in enumerate(self.arm_actuator_ids):
            self.data.ctrl[aid] = q[i]
    
    def set_joint_controls(self, q: np.ndarray):
        """Set arm actuator controls."""
        q = np.clip(q, self.joint_limits[:, 0], self.joint_limits[:, 1])
        for i, aid in enumerate(self.arm_actuator_ids):
            self.data.ctrl[aid] = q[i]
    
    # ==========================================================================
    # Forward Kinematics
    # ==========================================================================
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose.
        
        Returns:
            Tuple of (position, rotation_matrix)
            - position: (3,) array of xyz coordinates
            - rotation_matrix: (3, 3) rotation matrix
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.xpos[self.ee_body_id].copy()
        rot = self.data.xmat[self.ee_body_id].reshape(3, 3).copy()
        return pos, rot
    
    # ==========================================================================
    # Inverse Kinematics
    # ==========================================================================
    
    def compute_jacobian(self) -> np.ndarray:
        """
        Compute end-effector Jacobian.
        
        Returns:
            (6, 7) Jacobian matrix [position; rotation]
        """
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        mujoco.mj_jacBody(self.model, self.data, jacp, jacr, self.ee_body_id)
        # Extract only arm DOFs (first 7)
        jac = np.vstack([jacp[:, :self.dof], jacr[:, :self.dof]])
        return jac
    
    def ik_dls(
        self,
        target_pos: np.ndarray,
        q_init: Optional[np.ndarray] = None,
        max_iter: Optional[int] = None,
        tol: Optional[float] = None,
        verbose: bool = False
    ) -> Tuple[np.ndarray, bool]:
        """
        Inverse kinematics using Damped Least Squares.
        
        This implementation uses an adaptive damping scheme that scales with
        the residual norm, providing good convergence for both near and far
        targets. Joint limits are enforced via clamping.
        
        Args:
            target_pos: Target position (3,) array
            q_init: Initial joint configuration (default: current position)
            max_iter: Maximum iterations (default: self.ik_max_iter)
            tol: Convergence tolerance in meters (default: self.ik_tol)
            verbose: Print iteration details
            
        Returns:
            Tuple of (joint_positions, success)
            
        Raises:
            RuntimeError: If target is unreachable after max iterations
        """
        if q_init is None:
            q_init = self.get_joint_positions()
        if max_iter is None:
            max_iter = self.ik_max_iter
        if tol is None:
            tol = self.ik_tol
        
        q = q_init.copy()
        
        for iteration in range(max_iter):
            # Update FK
            self.set_joint_positions(q)
            current_pos, _ = self.get_ee_pose()
            
            # Position error
            error = target_pos - current_pos
            error_norm = np.linalg.norm(error)
            
            if verbose and iteration % 20 == 0:
                print(f"  IK iter {iteration}: error = {error_norm*1000:.2f} mm")
            
            if error_norm < tol:
                if verbose:
                    print(f"  IK converged in {iteration} iterations")
                return q, True
            
            # Compute Jacobian (only position part for position-only IK)
            jac_full = self.compute_jacobian()
            jac_pos = jac_full[:3, :]  # Only position Jacobian
            
            # Adaptive damping: increases with error
            lambda_dls = self.ik_lambda_init * (1.0 + error_norm)
            
            # Damped least squares solution
            JtJ = jac_pos.T @ jac_pos
            damped = JtJ + lambda_dls**2 * np.eye(self.dof)
            dq = np.linalg.solve(damped, jac_pos.T @ error)
            
            # Step size control to prevent large jumps
            step_size = 1.0
            dq_norm = np.linalg.norm(dq)
            if dq_norm > 0.5:
                step_size = 0.5 / dq_norm
            
            # Update joint positions
            q = q + step_size * dq
            
            # Enforce joint limits
            q = np.clip(q, self.joint_limits[:, 0], self.joint_limits[:, 1])
        
        # Failed to converge
        final_error = error_norm
        if final_error > 0.02:  # 2cm threshold for "unreachable"
            raise RuntimeError(
                f"IK failed to converge after {max_iter} iterations. "
                f"Final error: {final_error*1000:.1f} mm. "
                f"Target may be unreachable."
            )
        
        if verbose:
            print(f"  IK did not fully converge but error is acceptable: "
                  f"{final_error*1000:.2f} mm")
        
        return q, True
    
    # ==========================================================================
    # High-level Motion Control
    # ==========================================================================
    
    def home(self) -> bool:
        """
        Move to home configuration.
        
        Returns:
            True if successful
        """
        return self.move_to_joint_positions(self.home_q, duration=2.0)
    
    def move_to_joint_positions(
        self,
        target_q: np.ndarray,
        duration: float = 2.0
    ) -> bool:
        """
        Move arm to target joint positions with smooth interpolation.
        
        Args:
            target_q: Target joint positions
            duration: Movement duration in seconds
            
        Returns:
            True if successful
        """
        # Clamp target to limits
        target_q = np.clip(target_q, self.joint_limits[:, 0], self.joint_limits[:, 1])
        
        # Get current position
        q_start = self.get_joint_positions()
        
        # Compute number of steps
        n_steps = int(duration / self.control_timestep)
        
        # Linear interpolation
        for i in range(n_steps):
            t = (i + 1) / n_steps
            q_interp = q_start + t * (target_q - q_start)
            
            self.set_joint_controls(q_interp)
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        duration: float = 2.0,
        verbose: bool = False
    ) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Uses IK to compute target joint configuration, then executes smooth
        joint-space trajectory.
        
        Args:
            target_xyz: Target position (3,) array
            duration: Movement duration in seconds
            verbose: Print IK debug info
            
        Returns:
            True if successful
            
        Raises:
            RuntimeError: If IK fails (target unreachable)
        """
        # Solve IK
        q_current = self.get_joint_positions()
        q_target, success = self.ik_dls(target_xyz, q_init=q_current, verbose=verbose)
        
        if not success:
            return False
        
        # Execute joint-space motion
        return self.move_to_joint_positions(q_target, duration=duration)
    
    # ==========================================================================
    # Gripper Control
    # ==========================================================================
    
    def set_gripper_control(self, value: float):
        """Set gripper actuator control."""
        for aid in self.gripper_actuator_ids:
            self.data.ctrl[aid] = value
    
    def gripper_open(self, duration: float = 1.0) -> bool:
        """
        Open gripper.
        
        Args:
            duration: Movement duration in seconds
            
        Returns:
            True if successful
        """
        n_steps = int(duration / self.control_timestep)
        for _ in range(n_steps):
            self.set_gripper_control(self.gripper_open_pos)
            mujoco.mj_step(self.model, self.data)
        return True
    
    def gripper_close(self, duration: float = 1.0) -> bool:
        """
        Close gripper.
        
        Args:
            duration: Movement duration in seconds
            
        Returns:
            True if successful
        """
        n_steps = int(duration / self.control_timestep)
        for _ in range(n_steps):
            self.set_gripper_control(self.gripper_close_pos)
            mujoco.mj_step(self.model, self.data)
        return True
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        Uses a simple heuristic: gripper is closed but actuator force is high,
        indicating contact with an object.
        
        Returns:
            True if holding an object
        """
        # Check gripper actuator force
        for aid in self.gripper_actuator_ids:
            force = np.abs(self.data.actuator_force[aid])
            if force > self.gripper_force_threshold:
                return True
        return False
    
    # ==========================================================================
    # Simulation Control
    # ==========================================================================
    
    def step(self, n: int = 1):
        """
        Step simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(
        self,
        camera: int = -1,
        height: int = 480,
        width: int = 640
    ) -> np.ndarray:
        """
        Render camera view.
        
        Args:
            camera: Camera ID (-1 for free camera)
            height: Image height
            width: Image width
            
        Returns:
            RGB image array (height, width, 3)
        """
        renderer = mujoco.Renderer(self.model, height=height, width=width)
        renderer.update_scene(self.data, camera=camera)
        return renderer.render()
    
    # ==========================================================================
    # Introspection
    # ==========================================================================
    
    def describe(self) -> dict:
        """
        Get robot description and current state.
        
        Returns:
            Dictionary with robot info and state
        """
        q = self.get_joint_positions()
        ee_pos, ee_rot = self.get_ee_pose()
        
        return {
            'robot_id': self.study.get('robot_id', 'unknown'),
            'dof': self.dof,
            'arm_joints': self.arm_joint_names,
            'ee_body': self.ee_body_name,
            'has_gripper': len(self.gripper_actuator_ids) > 0,
            'joint_limits': self.joint_limits.tolist(),
            'current_state': {
                'joint_positions': q.tolist(),
                'ee_position': ee_pos.tolist(),
                'ee_rotation': ee_rot.tolist(),
                'is_holding': self.is_holding(),
            },
            'ik_params': {
                'method': 'Damped Least Squares',
                'max_iter': self.ik_max_iter,
                'tolerance_m': self.ik_tol,
                'lambda_init': self.ik_lambda_init,
            }
        }


# ==============================================================================
# Module-level convenience functions
# ==============================================================================

def test_basic():
    """Basic smoke test."""
    print("=== Basic Test ===")
    robot = Robot.build_from_mjcf('mjcf.xml')
    
    print("\nMoving to home...")
    robot.home()
    
    print("\nRobot state:")
    desc = robot.describe()
    print(f"  DOF: {desc['dof']}")
    print(f"  Joints: {desc['arm_joints']}")
    print(f"  EE position: {np.array(desc['current_state']['ee_position'])}")
    print(f"  Has gripper: {desc['has_gripper']}")
    print(f"  IK method: {desc['ik_params']['method']}")
    
    return robot


if __name__ == '__main__':
    test_basic()
