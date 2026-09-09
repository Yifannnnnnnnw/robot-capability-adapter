"""
UR5e Robot Driver - Written from scratch
6-DOF serial manipulator with DLS IK solver
No gripper attached
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import time
import os


class Robot:
    """
    UR5e 6-DOF robotic arm driver.
    
    Implements Damped Least Squares (DLS) IK for Cartesian control.
    DLS is chosen for the 6-DOF non-redundant arm because:
    - Numerically stable near singularities via damping
    - Handles both position and orientation constraints
    - Adaptive damping scales with residual for robust convergence
    """
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """
        Initialize robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        self.renderer = None
        
        # Joint configuration from study.json
        self.joint_names = [
            "shoulder_pan_joint",
            "shoulder_lift_joint", 
            "elbow_joint",
            "wrist_1_joint",
            "wrist_2_joint",
            "wrist_3_joint"
        ]
        
        self.actuator_names = [
            "shoulder_pan",
            "shoulder_lift",
            "elbow",
            "wrist_1",
            "wrist_2",
            "wrist_3"
        ]
        
        # Resolve indices
        self.joint_ids = [
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            for name in self.joint_names
        ]
        
        self.actuator_ids = [
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            for name in self.actuator_names
        ]
        
        # End-effector site
        self.ee_site_name = "attachment_site"
        self.ee_site_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_SITE, self.ee_site_name
        )
        
        # Joint limits (rad)
        self.joint_limits = np.array([
            [-6.28319, 6.28319],  # shoulder_pan
            [-6.28319, 6.28319],  # shoulder_lift
            [-3.1415, 3.1415],     # elbow
            [-6.28319, 6.28319],  # wrist_1
            [-6.28319, 6.28319],  # wrist_2
            [-6.28319, 6.28319],  # wrist_3
        ])
        
        # Home configuration (from keyframe in MJCF)
        self.home_qpos = np.array([-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0])
        
        # No gripper on this robot
        self.has_gripper = False
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Factory method to construct Robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF scene file
            
        Returns:
            Robot instance
        """
        # Resolve symlinks to get actual path
        if os.path.islink(mjcf_path):
            mjcf_path = os.readlink(mjcf_path)
        
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move robot to home configuration.
        
        Returns:
            True on success
        """
        try:
            self.data.qpos[:] = self.home_qpos
            self.data.ctrl[:] = self.home_qpos
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"home() failed: {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions.
        
        Returns:
            6-element array of joint angles (rad)
        """
        return self.data.qpos[:6].copy()
    
    def step(self, n: int = 1) -> None:
        """
        Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """
        Render current scene.
        
        Returns:
            RGB image array (H, W, 3) or None if no renderer
        """
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=480, width=640)
        
        self.renderer.update_scene(self.data)
        return self.renderer.render()
    
    def describe(self) -> str:
        """
        Return human-readable description of robot state.
        
        Returns:
            Multiline string description
        """
        q = self.get_joint_positions()
        ee_pos, ee_rot = self.get_ee_pose()
        
        desc = "UR5e 6-DOF Robotic Arm\n"
        desc += "=" * 40 + "\n"
        desc += f"DOF: 6\n"
        desc += f"Has gripper: {self.has_gripper}\n"
        desc += f"\nJoint positions (rad):\n"
        for i, name in enumerate(self.joint_names):
            desc += f"  {name:20s}: {q[i]:7.4f}\n"
        desc += f"\nEnd-effector pose:\n"
        desc += f"  Position (m): [{ee_pos[0]:7.4f}, {ee_pos[1]:7.4f}, {ee_pos[2]:7.4f}]\n"
        desc += f"  Rotation:\n"
        for row in ee_rot:
            desc += f"    [{row[0]:7.4f}, {row[1]:7.4f}, {row[2]:7.4f}]\n"
        
        return desc
    
    # ===== ARM-SPECIFIC API =====
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose.
        
        Returns:
            (position, rotation) where:
            - position: 3-element XYZ (m)
            - rotation: 3x3 rotation matrix
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return pos, rot
    
    def _compute_jacobian(self) -> np.ndarray:
        """
        Compute the 6x6 geometric Jacobian for the end-effector site.
        
        Returns:
            6x6 Jacobian [position; rotation]
        """
        jac_pos = np.zeros((3, self.model.nv))
        jac_rot = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jac_pos, jac_rot, self.ee_site_id)
        return np.vstack([jac_pos, jac_rot])
    
    def _ik_dls(
        self,
        target_pos: np.ndarray,
        target_rot: np.ndarray,
        q_init: Optional[np.ndarray] = None,
        max_iters: int = 100,
        pos_tol: float = 1e-3,  # 1mm
        damping_init: float = 0.05,
        damping_max: float = 0.5,
    ) -> Tuple[np.ndarray, bool, int, float]:
        """
        Damped Least Squares IK solver.
        
        Algorithm:
        - Iteratively minimize position and orientation error
        - Use DLS: dq = J^T (JJ^T + λ²I)^{-1} error
        - Adaptive damping: λ grows with residual to avoid instability
        - Step clamping prevents large jumps
        - Joint limits enforced after each step
        
        Args:
            target_pos: Target XYZ position (m)
            target_rot: Target 3x3 rotation matrix
            q_init: Initial joint configuration (None = current)
            max_iters: Maximum iterations
            pos_tol: Position convergence tolerance (m)
            damping_init: Base damping factor
            damping_max: Maximum damping factor
            
        Returns:
            (q_solution, success, iterations, final_error)
        """
        if q_init is None:
            q = self.get_joint_positions()
        else:
            q = q_init.copy()
        
        for iteration in range(max_iters):
            # Forward kinematics
            self.data.qpos[:] = q
            mujoco.mj_forward(self.model, self.data)
            
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            current_rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
            
            # Position error
            pos_error = target_pos - current_pos
            
            # Rotation error (axis-angle approximation from SO(3) difference)
            rot_diff = target_rot @ current_rot.T
            # Extract axis-angle: θ*axis ≈ [R23-R32, R31-R13, R12-R21] / 2
            rot_error = np.array([
                rot_diff[2, 1] - rot_diff[1, 2],
                rot_diff[0, 2] - rot_diff[2, 0],
                rot_diff[1, 0] - rot_diff[0, 1]
            ]) * 0.5
            
            # Combined 6D error
            error = np.hstack([pos_error, rot_error])
            error_norm = np.linalg.norm(error)
            
            # Check convergence (position criterion)
            if np.linalg.norm(pos_error) < pos_tol:
                return q, True, iteration + 1, error_norm
            
            # Compute Jacobian
            J = self._compute_jacobian()
            
            # Adaptive damping: increase with error to stabilize
            damping = damping_init + (damping_max - damping_init) * min(1.0, error_norm / 0.1)
            
            # DLS step: dq = J^T (JJ^T + λ²I)^{-1} error
            JJT = J @ J.T
            I6 = np.eye(6)
            dq = J.T @ np.linalg.solve(JJT + damping**2 * I6, error)
            
            # Step size limiting (prevent large jumps)
            max_step = 0.3  # rad
            step_norm = np.linalg.norm(dq)
            if step_norm > max_step:
                dq = dq * (max_step / step_norm)
            
            # Update joint configuration
            q = q + dq
            
            # Clamp to joint limits
            q = np.clip(q, self.joint_limits[:, 0], self.joint_limits[:, 1])
        
        # Failed to converge
        return q, False, max_iters, error_norm
    
    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        duration: float = 2.0,
        maintain_orientation: bool = True
    ) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Uses DLS IK to compute joint configuration, then interpolates
        smoothly to the target over the specified duration.
        
        Args:
            target_xyz: Target XYZ position (m)
            duration: Motion duration (s)
            maintain_orientation: If True, keep current EE orientation
            
        Returns:
            True if target reached within tolerance, False otherwise
        """
        # Get current state
        current_pos, current_rot = self.get_ee_pose()
        q_start = self.get_joint_positions()
        
        # Determine target orientation
        if maintain_orientation:
            target_rot = current_rot
        else:
            # Default: keep home orientation
            self.data.qpos[:] = self.home_qpos
            mujoco.mj_forward(self.model, self.data)
            target_rot = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
            self.data.qpos[:] = q_start  # restore
        
        # Solve IK
        q_target, success, iters, error = self._ik_dls(
            target_xyz, target_rot, q_init=q_start
        )
        
        if not success:
            print(f"IK failed to converge after {iters} iterations (error={error:.4f})")
            print(f"Target may be unreachable or near singularity.")
            return False
        
        # Interpolate to target
        n_steps = int(duration / self.model.opt.timestep)
        for i in range(n_steps):
            alpha = (i + 1) / n_steps
            q_interp = q_start + alpha * (q_target - q_start)
            self.data.qpos[:] = q_interp
            self.data.ctrl[:] = q_interp
            self.step()
        
        # Verify final position
        final_pos, _ = self.get_ee_pose()
        pos_error = np.linalg.norm(final_pos - target_xyz)
        
        if pos_error > 0.02:  # 2cm tolerance
            print(f"Warning: Final position error {pos_error*1000:.1f}mm exceeds 2cm")
            return False
        
        return True
    
    # ===== GRIPPER API (not applicable but included for interface consistency) =====
    
    def gripper_open(self) -> bool:
        """
        Open gripper (not applicable - no gripper).
        
        Returns:
            False (no gripper)
        """
        print("Warning: UR5e has no gripper attached")
        return False
    
    def gripper_close(self) -> bool:
        """
        Close gripper (not applicable - no gripper).
        
        Returns:
            False (no gripper)
        """
        print("Warning: UR5e has no gripper attached")
        return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object (not applicable).
        
        Returns:
            False (no gripper)
        """
        return False


# ===== Convenience API for testing =====

def demo_basic():
    """Basic demo: home, describe, and simple motion."""
    print("=== UR5e Basic Demo ===\n")
    
    robot = Robot.build_from_mjcf("mjcf.xml")
    robot.home()
    
    print(robot.describe())
    print("\n" + "="*40 + "\n")
    
    # Get current EE pose
    ee_pos, ee_rot = robot.get_ee_pose()
    print(f"Current EE position: {ee_pos}")
    
    # Move +5cm in X
    target = ee_pos + np.array([0.05, 0, 0])
    print(f"\nMoving to {target}...")
    success = robot.move_cartesian(target, duration=1.0)
    print(f"Success: {success}")
    
    final_pos, _ = robot.get_ee_pose()
    print(f"Final EE position: {final_pos}")
    print(f"Error: {np.linalg.norm(final_pos - target)*1000:.2f} mm")


if __name__ == "__main__":
    demo_basic()
