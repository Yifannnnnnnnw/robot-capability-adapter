"""
driver_from_scratch.py — Complete Robot driver for SO-101 5-DOF arm.

This module implements a Robot class with:
  - FK via mujoco.mj_forward
  - IK via Damped Least Squares (DLS) numerical method
  - Cartesian motion planning
  - Gripper control with weld constraints
  - Object manipulation support

No imports from auto_adapter.skeletons.
"""

import mujoco as mj
import numpy as np
from typing import Tuple, List, Optional


class Robot:
    """
    SO-101 5-DOF tabletop arm with parallel gripper.
    
    Attributes:
        model: MuJoCo model
        data: MuJoCo data
        arm_joint_ids: [0, 1, 2, 3, 4] for shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll
        gripper_joint_id: 5 for jaw_visual_joint
        ee_site_id: 0 for ee_site
        gripper_body_id: 7 for gripper_link
        object_body_ids: dict mapping object name -> body_id
        joint_limits: 5x2 array of [min, max] for each arm joint
        gripper_open_pos: jaw position when open (default -0.175)
        gripper_close_pos: jaw position when closed (default 1.75)
        held_object: name of currently held object, or None
    """
    
    def __init__(self, model: mj.MjModel, data: mj.MjData):
        self.model = model
        self.data = data
        
        # Joint indices
        self.arm_joint_ids = [0, 1, 2, 3, 4]
        self.gripper_joint_id = 5
        self.ee_site_id = 0
        self.gripper_body_id = 7
        
        # Object body IDs
        self.object_body_ids = {
            "banana": 9,
            "mug": 10,
            "bottle": 11,
            "screwdriver": 12,
            "duck": 13,
            "lego": 14,
        }
        
        # Joint limits [min, max] for each arm joint
        self.joint_limits = np.array([
            [-1.92, 1.92],   # shoulder_pan
            [-1.75, 1.75],   # shoulder_lift
            [-1.69, 1.69],   # elbow_flex
            [-1.66, 1.66],   # wrist_flex
            [-2.74, 2.84],   # wrist_roll
        ])
        
        # Gripper state
        self.gripper_open_pos = -0.175
        self.gripper_close_pos = 1.75
        self.held_object = None
        
        # IK parameters
        self.ik_max_iter = 200
        self.ik_tol = 1e-4
        self.ik_damping = 0.01
        self.ik_step_size = 0.5
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Load robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        model = mj.MjModel.from_xml_path(mjcf_path)
        data = mj.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move to home pose (all arm joints at 0, gripper open).
        
        Returns:
            True if successful
        """
        try:
            # Set arm to zero
            self.data.qpos[:5] = 0.0
            # Open gripper
            self.data.qpos[5] = self.gripper_open_pos
            # Disable any held object
            self.held_object = None
            # Disable all weld constraints
            self.data.eq_active[:] = False
            mj.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current arm joint positions (5 DOF).
        
        Returns:
            Array of 5 joint angles in radians
        """
        return self.data.qpos[:5].copy()
    
    def step(self, n: int = 1) -> None:
        """
        Advance simulation by n steps.
        
        Args:
            n: Number of simulation steps
        """
        for _ in range(n):
            mj.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """
        Placeholder for rendering. In a real system, would use mujoco.Renderer.
        """
        pass
    
    def describe(self) -> str:
        """
        Return a description of the robot state.
        
        Returns:
            String describing current configuration
        """
        q = self.get_joint_positions()
        ee_xyz, ee_rot = self.get_ee_pose()
        gripper_pos = self.data.qpos[5]
        
        desc = f"""
SO-101 5-DOF Arm Status:
  Joint positions: {q}
  EE position: {ee_xyz}
  EE rotation (3x3):
{ee_rot}
  Gripper position: {gripper_pos:.4f}
  Held object: {self.held_object}
"""
        return desc
    
    # ========== FK / EE Pose ==========
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector position and orientation.
        
        Returns:
            (xyz, R3x3) where xyz is world position and R3x3 is rotation matrix
        """
        mj.mj_forward(self.model, self.data)
        ee_xyz = self.data.site_xpos[self.ee_site_id].copy()
        ee_rot = self.data.xmat[self.gripper_body_id].reshape(3, 3).copy()
        return ee_xyz, ee_rot
    
    # ========== Utility: Rotation Matrix to Quaternion ==========
    
    def _mat2quat(self, mat: np.ndarray) -> np.ndarray:
        """
        Convert 3x3 rotation matrix to wxyz quaternion.
        
        Args:
            mat: 3x3 rotation matrix
            
        Returns:
            wxyz quaternion (4,)
        """
        trace = mat[0, 0] + mat[1, 1] + mat[2, 2]
        if trace > 0:
            s = 0.5 / np.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (mat[2, 1] - mat[1, 2]) * s
            y = (mat[0, 2] - mat[2, 0]) * s
            z = (mat[1, 0] - mat[0, 1]) * s
        elif mat[0, 0] > mat[1, 1] and mat[0, 0] > mat[2, 2]:
            s = 2.0 * np.sqrt(1.0 + mat[0, 0] - mat[1, 1] - mat[2, 2])
            w = (mat[2, 1] - mat[1, 2]) / s
            x = 0.25 * s
            y = (mat[0, 1] + mat[1, 0]) / s
            z = (mat[0, 2] + mat[2, 0]) / s
        elif mat[1, 1] > mat[2, 2]:
            s = 2.0 * np.sqrt(1.0 + mat[1, 1] - mat[0, 0] - mat[2, 2])
            w = (mat[0, 2] - mat[2, 0]) / s
            x = (mat[0, 1] + mat[1, 0]) / s
            y = 0.25 * s
            z = (mat[1, 2] + mat[2, 1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + mat[2, 2] - mat[0, 0] - mat[1, 1])
            w = (mat[1, 0] - mat[0, 1]) / s
            x = (mat[0, 2] + mat[2, 0]) / s
            y = (mat[1, 2] + mat[2, 1]) / s
            z = 0.25 * s
        return np.array([w, x, y, z])
    
    # ========== IK ==========
    
    def _compute_jacobian(self, q: np.ndarray, eps: float = 1e-6) -> np.ndarray:
        """
        Compute 3x5 Jacobian (EE position w.r.t. arm joints) numerically.
        
        Args:
            q: Current arm joint configuration (5,)
            eps: Finite difference step
            
        Returns:
            3x5 Jacobian matrix
        """
        J = np.zeros((3, 5))
        
        for j in range(5):
            # Forward difference
            q_plus = q.copy()
            q_plus[j] += eps
            self.data.qpos[:5] = q_plus
            mj.mj_forward(self.model, self.data)
            ee_pos_plus = self.data.site_xpos[self.ee_site_id].copy()
            
            # Backward difference
            q_minus = q.copy()
            q_minus[j] -= eps
            self.data.qpos[:5] = q_minus
            mj.mj_forward(self.model, self.data)
            ee_pos_minus = self.data.site_xpos[self.ee_site_id].copy()
            
            # Central difference
            J[:, j] = (ee_pos_plus - ee_pos_minus) / (2 * eps)
        
        return J
    
    def _ik_dls(self, target_xyz: np.ndarray, q_init: np.ndarray) -> Tuple[np.ndarray, float, int]:
        """
        Damped Least Squares (DLS) inverse kinematics.
        
        Solves: min ||J(q) * dq - error||^2 + λ ||dq||^2
        
        Algorithm:
          - Iterative numerical IK using central-difference Jacobian
          - DLS damping: λ = 0.01 (adaptive if singular)
          - Step clamp: ±0.5 rad per iteration
          - Joint limit enforcement: hard clamp to [min, max]
          - Convergence: residual < 1e-4 m or max 200 iterations
        
        Args:
            target_xyz: Target EE position (3,)
            q_init: Initial joint configuration (5,)
            
        Returns:
            (q_solution, residual, iterations)
        """
        q = q_init.copy()
        
        for it in range(self.ik_max_iter):
            # Forward kinematics
            self.data.qpos[:5] = q
            mj.mj_forward(self.model, self.data)
            ee_pos_curr = self.data.site_xpos[self.ee_site_id].copy()
            
            # Error
            error = target_xyz - ee_pos_curr
            residual = np.linalg.norm(error)
            
            if residual < self.ik_tol:
                return q, residual, it
            
            # Jacobian
            J = self._compute_jacobian(q)
            
            # DLS: (J^T J + λI)^{-1} J^T error
            JtJ = J.T @ J
            lambda_damp = self.ik_damping
            
            try:
                dq = np.linalg.solve(JtJ + lambda_damp * np.eye(5), J.T @ error)
            except np.linalg.LinAlgError:
                # Singular matrix, increase damping
                lambda_damp *= 10
                dq = np.linalg.solve(JtJ + lambda_damp * np.eye(5), J.T @ error)
            
            # Step clamp
            dq = np.clip(dq, -self.ik_step_size, self.ik_step_size)
            q = q + dq
            
            # Joint limit clamp
            for i in range(5):
                q[i] = np.clip(q[i], self.joint_limits[i, 0], self.joint_limits[i, 1])
        
        # Final residual
        self.data.qpos[:5] = q
        mj.mj_forward(self.model, self.data)
        ee_pos_final = self.data.site_xpos[self.ee_site_id].copy()
        residual = np.linalg.norm(target_xyz - ee_pos_final)
        
        return q, residual, self.ik_max_iter
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target position via IK and smooth trajectory.
        
        Uses position control (ctrl) to track a joint-space trajectory
        interpolated from current pose to IK solution.
        
        Args:
            target_xyz: Target EE position (3,)
            duration: Time to reach target (seconds)
            
        Returns:
            True if successful (residual < 1cm), False otherwise
        """
        try:
            # Get current pose
            q_curr = self.get_joint_positions()
            
            # Solve IK
            q_target, residual, iters = self._ik_dls(target_xyz, q_curr)
            
            if residual > 0.01:  # 1cm threshold
                print(f"IK failed: residual {residual:.6f} m > 0.01 m")
                return False
            
            # Smooth trajectory: linear interpolation in joint space
            num_steps = int(duration / self.model.opt.timestep)
            q_traj = np.linspace(q_curr, q_target, num_steps)
            
            for q_des in q_traj:
                # Set desired position via position control
                self.data.ctrl[:5] = q_des
                self.step(1)
            
            return True
        
        except Exception as e:
            print(f"Error in move_cartesian(): {e}")
            return False
    
    # ========== Gripper ==========
    
    def gripper_open(self) -> bool:
        """
        Open the gripper.
        
        Releases any held object and moves jaw to open position.
        
        Returns:
            True if successful
        """
        try:
            # Release any held object
            if self.held_object is not None:
                # Find the weld constraint index
                for i, obj_name in enumerate(["banana", "mug", "bottle", "screwdriver", "duck", "lego"]):
                    if obj_name == self.held_object:
                        self.data.eq_active[i] = False
                        break
                self.held_object = None
            
            # Move gripper to open position
            num_steps = int(0.5 / self.model.opt.timestep)
            gripper_traj = np.linspace(self.data.qpos[5], self.gripper_open_pos, num_steps)
            
            for gripper_pos in gripper_traj:
                self.data.ctrl[5] = gripper_pos
                self.step(1)
            
            return True
        
        except Exception as e:
            print(f"Error in gripper_open(): {e}")
            return False
    
    def gripper_close(self) -> bool:
        """
        Close the gripper.
        
        Moves jaw to closed position and checks for nearby objects to grasp.
        If an object is within 10cm of the EE, activates the weld constraint
        with the current relative pose.
        
        Returns:
            True if successful
        """
        try:
            # Move gripper to closed position
            num_steps = int(0.5 / self.model.opt.timestep)
            gripper_traj = np.linspace(self.data.qpos[5], self.gripper_close_pos, num_steps)
            
            for gripper_pos in gripper_traj:
                self.data.ctrl[5] = gripper_pos
                self.step(1)
            
            # Check if any object is near the gripper and activate weld
            ee_xyz, ee_rot = self.get_ee_pose()
            for obj_name, obj_body_id in self.object_body_ids.items():
                obj_xyz = self.data.xpos[obj_body_id].copy()
                obj_rot = self.data.xmat[obj_body_id].reshape(3, 3).copy()
                dist = np.linalg.norm(ee_xyz - obj_xyz)
                
                if dist < 0.10:  # 10cm threshold
                    # Compute current relative pose
                    rel_pos = obj_xyz - ee_xyz
                    rel_rot = ee_rot.T @ obj_rot
                    rel_quat = self._mat2quat(rel_rot)
                    
                    # Update weld constraint data with current relative pose
                    weld_idx = list(self.object_body_ids.keys()).index(obj_name)
                    self.model.eq_data[weld_idx, 3:6] = rel_pos
                    self.model.eq_data[weld_idx, 6:10] = rel_quat
                    
                    # Activate weld constraint
                    self.data.eq_active[weld_idx] = True
                    self.held_object = obj_name
                    break
            
            return True
        
        except Exception as e:
            print(f"Error in gripper_close(): {e}")
            return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        Returns:
            True if holding an object
        """
        return self.held_object is not None
    
    # ========== Object Manipulation ==========
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get world position of a named object.
        
        Resolves object name to body ID via mujoco.mj_name2id,
        then returns world position from data.xpos.
        
        Args:
            name: Object name (e.g., "banana", "mug")
            
        Returns:
            World position (3,)
            
        Raises:
            KeyError: If object not found
        """
        if name not in self.object_body_ids:
            raise KeyError(f"Object '{name}' not found. Available: {list(self.object_body_ids.keys())}")
        
        body_id = self.object_body_ids[name]
        mj.mj_forward(self.model, self.data)
        return self.data.xpos[body_id].copy()
    
    def get_object_names(self) -> List[str]:
        """
        Get list of manipulable object names.
        
        Returns:
            List of object names
        """
        return list(self.object_body_ids.keys())


# ============================================================================
# Test / Demo
# ============================================================================

if __name__ == "__main__":
    # Build robot
    robot = Robot.build_from_mjcf("mjcf.xml")
    
    # Home
    print("Moving to home...")
    robot.home()
    print(robot.describe())
    
    # Get EE pose
    ee_xyz, ee_rot = robot.get_ee_pose()
    print(f"\nEE position: {ee_xyz}")
    print(f"EE rotation:\n{ee_rot}")
    
    # Test IK: move +5cm in X
    print("\n=== IK Test: +5cm in X ===")
    target = ee_xyz + np.array([0.05, 0, 0])
    print(f"Target: {target}")
    success = robot.move_cartesian(target, duration=1.0)
    print(f"Success: {success}")
    
    ee_xyz_new, _ = robot.get_ee_pose()
    print(f"Final EE position: {ee_xyz_new}")
    print(f"Error: {np.linalg.norm(target - ee_xyz_new):.6f} m")
    
    # Test gripper
    print("\n=== Gripper Test ===")
    print(f"Holding: {robot.is_holding()}")
    robot.gripper_close()
    print(f"After close: {robot.is_holding()}")
    robot.gripper_open()
    print(f"After open: {robot.is_holding()}")
    
    # Test object detection
    print("\n=== Object Detection ===")
    print(f"Objects: {robot.get_object_names()}")
    for obj_name in robot.get_object_names():
        obj_pos = robot.get_object_position(obj_name)
        print(f"  {obj_name}: {obj_pos}")
