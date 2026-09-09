"""
KUKA iiwa14 7-DOF Collaborative Robot Arm Driver
Written from scratch - no auto_adapter.skeletons imports.

This driver implements:
- Forward kinematics via MuJoCo
- Inverse kinematics using Damped Least Squares (DLS) with adaptive damping
- Cartesian motion control with smooth interpolation
- Joint-space motion control
- No physical gripper (attachment site only)
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import time


class Robot:
    """
    KUKA iiwa14 7-DOF collaborative robot arm driver.
    
    The iiwa14 is a redundant (7-DOF) arm with position-controlled joints.
    IK uses Damped Least Squares (DLS) with adaptive damping that scales
    with tracking error for robust convergence.
    """
    
    # Home configuration: [0, 45°, 0, -90°, 0, 0, 0]
    HOME_QPOS = np.array([0.0, 0.785398, 0.0, -1.5708, 0.0, 0.0, 0.0])
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, mjcf_path: str):
        """
        Initialize robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
            mjcf_path: Path to MJCF file
        """
        self.model = model
        self.data = data
        self.mjcf_path = mjcf_path
        
        # Validate model structure
        if self.model.nq != 7 or self.model.nu != 7:
            raise ValueError(f"Expected 7 DOF arm, got nq={self.model.nq}, nu={self.model.nu}")
        
        # Find EE site
        self.ee_site_name = "attachment_site"
        try:
            self.ee_site_id = self.model.site(self.ee_site_name).id
        except KeyError:
            raise ValueError(f"End-effector site '{self.ee_site_name}' not found in model")
        
        # Store joint limits for clamping
        self.joint_limits = []
        for i in range(self.model.njnt):
            self.joint_limits.append(self.model.jnt_range[i].copy())
        
        # Renderer (created lazily)
        self._renderer = None
        
        # Control timestep
        self.dt = self.model.opt.timestep
        
        print(f"[Robot] KUKA iiwa14 initialized from {mjcf_path}")
        print(f"[Robot] 7-DOF arm, dt={self.dt:.4f}s")
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Factory method: load robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data, mjcf_path)
    
    def describe(self) -> str:
        """Return human-readable description of the robot."""
        desc = []
        desc.append("=" * 60)
        desc.append("KUKA iiwa14 7-DOF Collaborative Robot Arm")
        desc.append("=" * 60)
        desc.append(f"DOF: {self.model.nv}")
        desc.append(f"Actuators: {self.model.nu}")
        desc.append(f"End-effector site: {self.ee_site_name}")
        desc.append(f"Timestep: {self.dt:.4f}s")
        desc.append("")
        
        desc.append("Joints:")
        for i in range(self.model.njnt):
            joint_name = self.model.joint(i).name
            limits = self.joint_limits[i]
            qpos_val = self.data.qpos[i]
            desc.append(f"  {i}: {joint_name:10s} = {qpos_val:+.4f} rad  (limits: [{limits[0]:+.3f}, {limits[1]:+.3f}])")
        
        desc.append("")
        ee_pos, ee_mat = self.get_ee_pose()
        desc.append(f"End-effector position: [{ee_pos[0]:.4f}, {ee_pos[1]:.4f}, {ee_pos[2]:.4f}]")
        desc.append("")
        desc.append("Capabilities:")
        desc.append("  - get_ee_pose() -> (xyz, R3x3)")
        desc.append("  - move_cartesian(target_xyz, duration) -> bool")
        desc.append("  - home() -> bool")
        desc.append("  - No physical gripper (attachment point only)")
        desc.append("=" * 60)
        
        return "\n".join(desc)
    
    def home(self) -> bool:
        """
        Move to home configuration using smooth interpolation.
        
        Returns:
            True on success
        """
        print("[Robot] Moving to home position...")
        duration = 2.0
        return self._interpolate_joint_space(self.HOME_QPOS, duration)
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions.
        
        Returns:
            Array of 7 joint angles in radians
        """
        return self.data.qpos[:].copy()
    
    def set_joint_positions(self, qpos: np.ndarray) -> None:
        """
        Set joint positions directly (no interpolation).
        
        Args:
            qpos: Array of 7 joint angles in radians
        """
        if len(qpos) != 7:
            raise ValueError(f"Expected 7 joint positions, got {len(qpos)}")
        
        # Clamp to limits
        for i in range(7):
            qpos[i] = np.clip(qpos[i], self.joint_limits[i][0], self.joint_limits[i][1])
        
        self.data.qpos[:] = qpos
        self.data.ctrl[:] = qpos  # Position control
        mujoco.mj_forward(self.model, self.data)
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose (forward kinematics).
        
        Returns:
            Tuple of (position, rotation_matrix)
            - position: 3D array [x, y, z] in meters
            - rotation_matrix: 3x3 rotation matrix
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        mat = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return pos, mat
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target Cartesian position (position-only IK).
        
        Uses Damped Least Squares IK with adaptive damping. The arm will
        find a joint configuration that places the EE at the target position,
        maintaining the current orientation as much as possible.
        
        Args:
            target_xyz: Target position [x, y, z] in meters
            duration: Time to reach target (seconds)
            
        Returns:
            True if target reached within tolerance, False otherwise
        """
        target_xyz = np.asarray(target_xyz, dtype=float)
        if target_xyz.shape != (3,):
            raise ValueError(f"target_xyz must be shape (3,), got {target_xyz.shape}")
        
        print(f"[Robot] move_cartesian: target={target_xyz}, duration={duration}s")
        
        # Solve IK
        success, iterations, final_error = self._solve_ik(target_xyz, target_mat=None)
        
        if not success:
            print(f"[Robot] IK failed: error={final_error:.6f}m after {iterations} iterations")
            return False
        
        print(f"[Robot] IK converged in {iterations} iterations, error={final_error:.6f}m")
        
        # Get IK solution
        target_qpos = self.data.qpos[:].copy()
        
        # Interpolate to target
        return self._interpolate_joint_space(target_qpos, duration)
    
    def _solve_ik(self, target_pos: np.ndarray, target_mat: Optional[np.ndarray] = None,
                  max_iter: int = 100, tol: float = 1e-3,
                  lambda_init: float = 0.1, lambda_scale: float = 10.0) -> Tuple[bool, int, float]:
        """
        Solve inverse kinematics using Damped Least Squares (DLS).
        
        This implementation uses adaptive damping that increases with tracking
        error for robust convergence. The redundant 7-DOF configuration allows
        the solver to find natural joint configurations.
        
        Args:
            target_pos: Target position [x, y, z]
            target_mat: Target rotation matrix (3x3), or None for position-only IK
            max_iter: Maximum iterations
            tol: Convergence tolerance (meters)
            lambda_init: Initial damping factor
            lambda_scale: Damping scale factor (increases with error)
            
        Returns:
            Tuple of (success, iterations, final_error)
        """
        nv = self.model.nv
        
        for iteration in range(max_iter):
            # Forward kinematics
            mujoco.mj_forward(self.model, self.data)
            
            # Current EE pose
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            current_mat = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
            
            # Position error
            pos_error = target_pos - current_pos
            
            # Orientation error (if target orientation provided)
            if target_mat is not None:
                # Compute rotation error using rotation matrix difference
                R_error = target_mat @ current_mat.T
                # Convert to axis-angle (approximate for small angles)
                ori_error = np.array([
                    R_error[2, 1] - R_error[1, 2],
                    R_error[0, 2] - R_error[2, 0],
                    R_error[1, 0] - R_error[0, 1]
                ]) * 0.5
                error = np.concatenate([pos_error, ori_error])
            else:
                error = pos_error
            
            error_norm = np.linalg.norm(error)
            
            # Check convergence
            if error_norm < tol:
                return True, iteration, error_norm
            
            # Compute Jacobian
            jacp = np.zeros((3, nv))
            jacr = np.zeros((3, nv))
            mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site_id)
            
            if target_mat is not None:
                J = np.vstack([jacp, jacr])
            else:
                J = jacp
            
            # Adaptive damping: increases with error for stability
            lambda_damp = lambda_init * (1.0 + lambda_scale * error_norm)
            
            # Damped least squares: (J^T J + λ^2 I)^-1 J^T e
            JTJ = J.T @ J
            damping_matrix = (lambda_damp ** 2) * np.eye(nv)
            dq = np.linalg.solve(JTJ + damping_matrix, J.T @ error)
            
            # Clamp step size for stability
            max_step = 0.1  # radians
            dq_norm = np.linalg.norm(dq)
            if dq_norm > max_step:
                dq = dq * (max_step / dq_norm)
            
            # Update joint positions
            self.data.qpos[:] += dq
            
            # Clamp to joint limits
            for i in range(nv):
                self.data.qpos[i] = np.clip(
                    self.data.qpos[i],
                    self.joint_limits[i][0],
                    self.joint_limits[i][1]
                )
        
        # Max iterations reached
        return False, max_iter, error_norm
    
    def _interpolate_joint_space(self, target_qpos: np.ndarray, duration: float) -> bool:
        """
        Smoothly interpolate from current to target joint positions.
        
        Uses cosine interpolation for smooth acceleration/deceleration.
        
        Args:
            target_qpos: Target joint positions
            duration: Time to reach target (seconds)
            
        Returns:
            True on success
        """
        start_qpos = self.data.qpos[:].copy()
        
        num_steps = max(1, int(duration / self.dt))
        
        for step in range(num_steps + 1):
            # Cosine interpolation for smooth motion
            alpha = step / num_steps
            alpha_smooth = 0.5 * (1.0 - np.cos(np.pi * alpha))
            
            qpos = start_qpos + alpha_smooth * (target_qpos - start_qpos)
            
            # Set position
            self.set_joint_positions(qpos)
            
            # Step simulation
            self.step(1)
        
        return True
    
    def step(self, n: int = 1) -> None:
        """
        Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> np.ndarray:
        """
        Render current scene to RGB image.
        
        Returns:
            RGB image array (height, width, 3)
        """
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self.model, height=480, width=640)
        
        self._renderer.update_scene(self.data)
        return self._renderer.render()
    
    # ===== Gripper methods (no physical gripper on iiwa14) =====
    
    def gripper_open(self) -> bool:
        """
        Open gripper (NO-OP: iiwa14 has no physical gripper).
        
        Returns:
            True (always succeeds as no-op)
        """
        print("[Robot] gripper_open: No physical gripper on iiwa14 (attachment site only)")
        return True
    
    def gripper_close(self) -> bool:
        """
        Close gripper (NO-OP: iiwa14 has no physical gripper).
        
        Returns:
            True (always succeeds as no-op)
        """
        print("[Robot] gripper_close: No physical gripper on iiwa14 (attachment site only)")
        return True
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object (NO-OP: iiwa14 has no physical gripper).
        
        Returns:
            False (always, no gripper)
        """
        return False
    
    # ===== Additional utility methods =====
    
    def get_joint_velocities(self) -> np.ndarray:
        """
        Get current joint velocities.
        
        Returns:
            Array of 7 joint velocities in rad/s
        """
        return self.data.qvel[:].copy()
    
    def move_joint(self, joint_positions: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move to specific joint configuration.
        
        Args:
            joint_positions: Target joint positions (7 values in radians)
            duration: Time to reach target (seconds)
            
        Returns:
            True on success
        """
        joint_positions = np.asarray(joint_positions, dtype=float)
        if joint_positions.shape != (7,):
            raise ValueError(f"Expected 7 joint positions, got {joint_positions.shape}")
        
        print(f"[Robot] move_joint: target={joint_positions}, duration={duration}s")
        return self._interpolate_joint_space(joint_positions, duration)
    
    def get_jacobian(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector Jacobian matrices.
        
        Returns:
            Tuple of (position_jacobian, rotation_jacobian)
            Each is a (3, 7) matrix relating joint velocities to EE velocities
        """
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site_id)
        return jacp, jacr
    
    def check_self_collision(self) -> bool:
        """
        Check for self-collisions.
        
        Returns:
            True if any contacts detected
        """
        return self.data.ncon > 0
    
    def reset(self) -> None:
        """Reset simulation to initial state."""
        mujoco.mj_resetData(self.model, self.data)
        self.data.qpos[:] = self.HOME_QPOS
        self.data.ctrl[:] = self.HOME_QPOS
        mujoco.mj_forward(self.model, self.data)
        print("[Robot] Reset to initial state")


# ===== Standalone test functions =====

def test_basic_construction():
    """Test that robot can be constructed and basic methods work."""
    print("\n" + "=" * 60)
    print("TEST: Basic Construction")
    print("=" * 60)
    
    robot = Robot.build_from_mjcf("mjcf.xml")
    print(robot.describe())
    
    # Test home
    robot.home()
    
    # Test get_joint_positions
    qpos = robot.get_joint_positions()
    print(f"\nJoint positions: {qpos}")
    
    # Test get_ee_pose
    ee_pos, ee_mat = robot.get_ee_pose()
    print(f"EE position: {ee_pos}")
    print(f"EE rotation:\n{ee_mat}")
    
    print("\n[TEST PASSED]")
    return robot


def test_ik_roundtrip():
    """Test IK by moving EE +5cm in X, reading back position."""
    print("\n" + "=" * 60)
    print("TEST: IK Round-trip")
    print("=" * 60)
    
    robot = Robot.build_from_mjcf("mjcf.xml")
    robot.home()
    
    # Get home EE position
    ee_pos_home, _ = robot.get_ee_pose()
    print(f"Home EE position: {ee_pos_home}")
    
    # Move +5cm in X
    target_pos = ee_pos_home + np.array([0.05, 0.0, 0.0])
    print(f"Target position: {target_pos}")
    
    success = robot.move_cartesian(target_pos, duration=1.0)
    
    if not success:
        print("[TEST FAILED] move_cartesian returned False")
        return False
    
    # Read back position
    ee_pos_final, _ = robot.get_ee_pose()
    print(f"Final EE position: {ee_pos_final}")
    
    error = np.linalg.norm(ee_pos_final - target_pos)
    print(f"Position error: {error:.6f} m ({error*100:.3f} cm)")
    
    if error < 0.02:  # 2cm tolerance
        print("[TEST PASSED]")
        return True
    else:
        print("[TEST FAILED] Error exceeds 2cm tolerance")
        return False


if __name__ == "__main__":
    # Run tests
    robot = test_basic_construction()
    test_ik_roundtrip()
