#!/usr/bin/env python3
"""
Complete robot driver for piper_onboard (6-DOF arm with gripper).
Written from scratch without auto_adapter.skeletons imports.

IK Method: Damped Least Squares (DLS) with adaptive damping
- 6-DOF non-redundant arm
- Position-only IK (3x6 Jacobian) for better convergence in constrained workspace
- Adaptive damping: λ = λ_base + λ_scale * ||error||
- Joint limit clamping and step size limiting for robustness
"""

import mujoco
import numpy as np
import os
from typing import Tuple, Optional


class Robot:
    """6-DOF arm robot with parallel gripper."""
    
    # Constants
    ARM_JOINT_NAMES = ['joint1', 'joint2', 'joint3', 'joint4', 'joint5', 'joint6']
    HOME_QPOS = [0.0, 1.57, -1.3485, 0.0, 0.0, 0.0]  # Arm joints only
    GRIPPER_JOINT_NAME = 'joint7'
    EE_SITE_NAME = 'ee_site'
    
    # IK parameters
    IK_MAX_ITER = 150
    IK_TOL = 1e-3  # 1mm tolerance
    IK_LAMBDA_BASE = 0.05
    IK_LAMBDA_SCALE = 5.0
    IK_STEP_LIMIT = 0.3  # Max joint angle change per iteration
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize robot with MuJoCo model and data."""
        self.model = model
        self.data = data
        
        # Find joint indices
        self.arm_joint_ids = [model.joint(name).id for name in self.ARM_JOINT_NAMES]
        self.arm_qpos_addrs = [model.jnt_qposadr[jid] for jid in self.arm_joint_ids]
        self.arm_dof_addrs = [model.jnt_dofadr[jid] for jid in self.arm_joint_ids]
        
        # Find gripper joint
        self.gripper_joint_id = model.joint(self.GRIPPER_JOINT_NAME).id
        self.gripper_qpos_addr = model.jnt_qposadr[self.gripper_joint_id]
        
        # Find EE site
        self.ee_site_id = model.site(self.EE_SITE_NAME).id
        
        # Get joint limits
        self.joint_limits = []
        for jid in self.arm_joint_ids:
            limits = model.jnt_range[jid]
            self.joint_limits.append((limits[0], limits[1]))
        
        # Get gripper limits
        gripper_limits = model.jnt_range[self.gripper_joint_id]
        self.gripper_range = (gripper_limits[0], gripper_limits[1])
        
        # Find actuator indices
        self.arm_actuator_ids = []
        for name in self.ARM_JOINT_NAMES:
            try:
                act_id = model.actuator(name).id
                self.arm_actuator_ids.append(act_id)
            except KeyError:
                raise ValueError(f"Actuator {name} not found")
        
        try:
            self.gripper_actuator_id = model.actuator('gripper').id
        except KeyError:
            raise ValueError("Gripper actuator not found")
        
        # Rendering support
        self.renderer = None
        self.render_enabled = False
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file path."""
        # Handle symlinks and includes
        mjcf_path = os.path.realpath(mjcf_path)
        mjcf_dir = os.path.dirname(mjcf_path)
        
        # Change to model directory so includes work
        orig_dir = os.getcwd()
        try:
            os.chdir(mjcf_dir)
            model = mujoco.MjModel.from_xml_path(os.path.basename(mjcf_path))
        finally:
            os.chdir(orig_dir)
        
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """Move robot to home position."""
        try:
            # Set arm joints to home position
            for i, addr in enumerate(self.arm_qpos_addrs):
                self.data.qpos[addr] = self.HOME_QPOS[i]
            
            # Open gripper
            self.data.qpos[self.gripper_qpos_addr] = self.gripper_range[1]
            
            # Set control signals to match
            for i, act_id in enumerate(self.arm_actuator_ids):
                self.data.ctrl[act_id] = self.HOME_QPOS[i]
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_range[1]
            
            # Forward kinematics
            mujoco.mj_forward(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current arm joint positions (6-element array)."""
        return np.array([self.data.qpos[addr] for addr in self.arm_qpos_addrs])
    
    def step(self, n: int = 1):
        """Step simulation forward by n steps."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """Render current scene and return RGB image."""
        if not self.render_enabled:
            # Initialize renderer on first call
            self.renderer = mujoco.Renderer(self.model, height=480, width=640)
            self.render_enabled = True
        
        self.renderer.update_scene(self.data)
        return self.renderer.render()
    
    def describe(self) -> str:
        """Return description of robot state."""
        qpos = self.get_joint_positions()
        ee_pos, ee_rot = self.get_ee_pose()
        gripper_pos = self.data.qpos[self.gripper_qpos_addr]
        
        desc = f"Piper 6-DOF Arm\n"
        desc += f"  Joint positions: [{', '.join(f'{q:.3f}' for q in qpos)}]\n"
        desc += f"  EE position: [{ee_pos[0]:.4f}, {ee_pos[1]:.4f}, {ee_pos[2]:.4f}]\n"
        desc += f"  Gripper opening: {gripper_pos:.4f} (range: [{self.gripper_range[0]:.3f}, {self.gripper_range[1]:.3f}])\n"
        return desc
    
    # ========== ARM-SPECIFIC API ==========
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose.
        
        Returns:
            (xyz, R3x3): Position and rotation matrix
        """
        mujoco.mj_forward(self.model, self.data)
        ee_pos = self.data.site_xpos[self.ee_site_id].copy()
        ee_rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return ee_pos, ee_rot
    
    def _compute_ik_dls(self, target_pos: np.ndarray, target_rot: Optional[np.ndarray] = None) -> Tuple[bool, int]:
        """
        Damped Least Squares IK solver.
        
        Args:
            target_pos: Target position (3,)
            target_rot: Target rotation matrix (3, 3), or None for position-only IK
        
        Returns:
            (success, num_iterations)
        """
        position_only = (target_rot is None)
        
        for iteration in range(self.IK_MAX_ITER):
            # Forward kinematics
            mujoco.mj_forward(self.model, self.data)
            
            # Current EE pose
            ee_pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Position error
            pos_error = target_pos - ee_pos
            pos_error_norm = np.linalg.norm(pos_error)
            
            # Check position convergence
            if pos_error_norm < self.IK_TOL:
                return True, iteration
            
            # Compute Jacobian
            jacp = np.zeros((3, self.model.nv))
            jacr = np.zeros((3, self.model.nv))
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site_id)
            
            # Extract arm Jacobian (first 6 DOF)
            J_pos = jacp[:, :6]
            
            if position_only:
                # Position-only IK (3x6 system)
                J = J_pos
                error = pos_error
            else:
                # Full 6D IK
                ee_mat = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
                
                # Orientation error (axis-angle from R_error = R_target * R_current^T)
                R_error = target_rot @ ee_mat.T
                angle = np.arccos(np.clip((np.trace(R_error) - 1) / 2, -1, 1))
                if angle < 1e-6:
                    rot_error = np.zeros(3)
                else:
                    axis = np.array([
                        R_error[2, 1] - R_error[1, 2],
                        R_error[0, 2] - R_error[2, 0],
                        R_error[1, 0] - R_error[0, 1]
                    ]) / (2 * np.sin(angle))
                    rot_error = angle * axis
                
                J_rot = jacr[:, :6]
                J = np.vstack([J_pos, J_rot])
                error = np.hstack([pos_error, rot_error])
            
            # Adaptive damping: larger error -> more damping (more stable)
            lambda_damp = self.IK_LAMBDA_BASE + self.IK_LAMBDA_SCALE * pos_error_norm
            
            # Damped least squares solution
            JtJ = J.T @ J + lambda_damp**2 * np.eye(6)
            
            try:
                dq = np.linalg.solve(JtJ, J.T @ error)
            except np.linalg.LinAlgError:
                # Singular matrix - can't solve
                return False, iteration
            
            # Clamp step size to prevent instability
            step_norm = np.linalg.norm(dq)
            if step_norm > self.IK_STEP_LIMIT:
                dq = dq * self.IK_STEP_LIMIT / step_norm
            
            # Update joint positions with limit clamping
            for i, addr in enumerate(self.arm_qpos_addrs):
                self.data.qpos[addr] += dq[i]
                # Clamp to joint limits
                lower, upper = self.joint_limits[i]
                self.data.qpos[addr] = np.clip(self.data.qpos[addr], lower, upper)
        
        # Failed to converge
        return False, self.IK_MAX_ITER
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target position (position-only IK, orientation is free).
        
        Args:
            target_xyz: Target position (3,)
            duration: Time to reach target (used for validation, not actual control)
        
        Returns:
            True if IK succeeded and target is reachable
        """
        # Use position-only IK for better convergence
        # This allows the orientation to adjust freely to reach the position
        success, iterations = self._compute_ik_dls(target_xyz, target_rot=None)
        
        if not success:
            print(f"IK failed to converge after {iterations} iterations")
            return False
        
        # Update control signals to match new joint positions
        for i, act_id in enumerate(self.arm_actuator_ids):
            self.data.ctrl[act_id] = self.data.qpos[self.arm_qpos_addrs[i]]
        
        # Verify we reached the target
        mujoco.mj_forward(self.model, self.data)
        final_pos, _ = self.get_ee_pose()
        error = np.linalg.norm(target_xyz - final_pos)
        
        if error > 0.02:  # 2cm threshold
            print(f"Warning: Large position error after IK: {error*1000:.1f}mm")
            return False
        
        return True
    
    def gripper_open(self) -> bool:
        """Open the gripper."""
        try:
            # Set gripper to maximum opening
            target = self.gripper_range[1]
            self.data.qpos[self.gripper_qpos_addr] = target
            self.data.ctrl[self.gripper_actuator_id] = target
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_open(): {e}")
            return False
    
    def gripper_close(self) -> bool:
        """Close the gripper."""
        try:
            # Set gripper to minimum opening (closed)
            target = self.gripper_range[0]
            self.data.qpos[self.gripper_qpos_addr] = target
            self.data.ctrl[self.gripper_actuator_id] = target
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_close(): {e}")
            return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        Returns:
            True if gripper is partially closed (indicating contact with object)
        """
        gripper_pos = self.data.qpos[self.gripper_qpos_addr]
        # If gripper is not fully open, assume it's holding something
        # Fully open is at upper limit, so if significantly below that, it's holding
        threshold = self.gripper_range[1] * 0.7  # 70% of max opening
        return gripper_pos < threshold


# ========== STANDALONE TEST ==========

if __name__ == "__main__":
    print("=" * 60)
    print("Testing driver_from_scratch.py")
    print("=" * 60)
    
    # Build robot
    print("\n1. Building robot from MJCF...")
    robot = Robot.build_from_mjcf('mjcf.xml')
    print("   ✓ Robot built successfully")
    
    # Home
    print("\n2. Moving to home position...")
    success = robot.home()
    print(f"   {'✓' if success else '✗'} Home: {success}")
    
    # Describe
    print("\n3. Robot state:")
    print(robot.describe())
    
    # Test FK
    print("\n4. Testing forward kinematics...")
    ee_pos, ee_rot = robot.get_ee_pose()
    print(f"   EE position: [{ee_pos[0]:.4f}, {ee_pos[1]:.4f}, {ee_pos[2]:.4f}]")
    print(f"   EE orientation (z-axis): [{ee_rot[0,2]:.3f}, {ee_rot[1,2]:.3f}, {ee_rot[2,2]:.3f}]")
    
    # Test IK - move +5cm in X
    print("\n5. Testing IK: move +5cm in X direction...")
    target = ee_pos + np.array([0.05, 0, 0])
    success = robot.move_cartesian(target, duration=2.0)
    print(f"   {'✓' if success else '✗'} IK succeeded: {success}")
    
    if success:
        new_pos, _ = robot.get_ee_pose()
        error = np.linalg.norm(target - new_pos)
        print(f"   Target:  [{target[0]:.4f}, {target[1]:.4f}, {target[2]:.4f}]")
        print(f"   Reached: [{new_pos[0]:.4f}, {new_pos[1]:.4f}, {new_pos[2]:.4f}]")
        print(f"   Error: {error*1000:.2f}mm")
    
    # Test gripper
    print("\n6. Testing gripper...")
    robot.gripper_close()
    print(f"   Closed - is_holding: {robot.is_holding()}")
    robot.gripper_open()
    print(f"   Opened - is_holding: {robot.is_holding()}")
    
    print("\n" + "=" * 60)
    print("All tests complete!")
    print("=" * 60)
