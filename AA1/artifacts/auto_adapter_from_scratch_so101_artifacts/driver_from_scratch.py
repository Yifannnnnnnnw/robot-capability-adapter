"""
SO-101 5-DOF Robot Driver - Built from Scratch
================================================

Custom implementation of FK, IK, motion planning, and gripper control
for the SO-101 tabletop manipulation arm.

IK Method: Damped Least Squares (DLS) with adaptive damping
- 5-DOF arm → position-only IK (3 DOF position control)
- Adaptive damping: λ = λ_base * (1 + ||error||)
- Step size control to prevent oscillations
- Joint limit clamping after each iteration

Gripper: Weld-based grasping
- Proximity detection (distance < threshold)
- Enable/disable weld constraints dynamically
- Visual jaw animation via jaw_visual_joint

Author: Phase 2 GEN_ALGO
"""

import mujoco
import numpy as np
from typing import Optional, Tuple
import os


class Robot:
    """
    SO-101 5-DOF robotic arm with weld-based gripper.
    
    Features:
    - Cartesian motion planning with position-only IK
    - Smooth trajectory interpolation
    - Weld-based object grasping
    - Configurable damping and convergence tolerances
    """
    
    def __init__(
        self,
        model: mujoco.MjModel,
        data: mujoco.MjData,
        arm_joint_names: list[str],
        ee_site: str,
        gripper_actuator: str,
        graspable_bodies: list[str],
        home_config: Optional[np.ndarray] = None
    ):
        """
        Initialize robot controller.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
            arm_joint_names: List of arm joint names in order
            ee_site: Name of end-effector site
            gripper_actuator: Name of gripper actuator (jaw_visual)
            graspable_bodies: List of graspable body names
            home_config: Home joint configuration (default: all zeros)
        """
        self.model = model
        self.data = data
        self.arm_joint_names = arm_joint_names
        self.n_joints = len(arm_joint_names)
        
        # Resolve indices
        self.joint_ids = [
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            for name in arm_joint_names
        ]
        self.site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, ee_site)
        
        # Joint limits
        self.joint_limits = np.array([
            model.jnt_range[jid] for jid in self.joint_ids
        ])
        
        # Gripper configuration
        self.gripper_actuator_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, gripper_actuator
        )
        self.gripper_body_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, 'gripper_link'
        )
        
        # Find weld constraints for grasping
        self.weld_constraints = []
        for body_name in graspable_bodies:
            for eq_id in range(model.neq):
                if model.eq_type[eq_id] == mujoco.mjtEq.mjEQ_WELD:
                    body1 = model.eq_obj1id[eq_id]
                    body2 = model.eq_obj2id[eq_id]
                    b1_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body1)
                    b2_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body2)
                    
                    if (b1_name == 'gripper_link' and b2_name == body_name) or \
                       (b2_name == 'gripper_link' and b1_name == body_name):
                        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
                        self.weld_constraints.append({
                            'eq_id': eq_id,
                            'body_name': body_name,
                            'body_id': body_id
                        })
                        break
        
        # Home configuration
        self.home_config = home_config if home_config is not None else np.zeros(self.n_joints)
        
        # IK parameters
        self.ik_max_iter = 100
        self.ik_tol = 0.01  # 1cm tolerance
        self.ik_lambda_base = 0.1
        
        # Grasping parameters
        self.grasp_distance_threshold = 0.08  # 8cm proximity threshold
        self.held_object = None
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        # Resolve symlinks and get absolute path
        if os.path.islink(mjcf_path):
            model_path = os.readlink(mjcf_path)
            if not os.path.isabs(model_path):
                # Relative symlink - resolve relative to symlink location
                symlink_dir = os.path.dirname(os.path.abspath(mjcf_path))
                model_path = os.path.join(symlink_dir, model_path)
        elif os.path.exists(mjcf_path):
            model_path = mjcf_path
        else:
            # Fallback: try to find in expected location
            model_path = 'assets/mjcf/so101_mujoco.xml'
        
        # Change to model directory for mesh loading
        model_dir = os.path.dirname(os.path.abspath(model_path))
        original_dir = os.getcwd()
        
        try:
            os.chdir(model_dir)
            model = mujoco.MjModel.from_xml_path(os.path.basename(model_path))
            data = mujoco.MjData(model)
        finally:
            os.chdir(original_dir)
        
        # Robot configuration (from study.json)
        arm_joint_names = [
            'shoulder_pan',
            'shoulder_lift',
            'elbow_flex',
            'wrist_flex',
            'wrist_roll'
        ]
        ee_site = 'ee_site'
        gripper_actuator = 'act_jaw_visual'
        graspable_bodies = ['banana', 'mug', 'bottle', 'screwdriver', 'duck', 'lego']
        
        return cls(
            model=model,
            data=data,
            arm_joint_names=arm_joint_names,
            ee_site=ee_site,
            gripper_actuator=gripper_actuator,
            graspable_bodies=graspable_bodies
        )
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions."""
        q = np.zeros(self.n_joints)
        for i, jid in enumerate(self.joint_ids):
            q[i] = self.data.qpos[self.model.jnt_qposadr[jid]]
        return q
    
    def set_joint_positions(self, q: np.ndarray) -> None:
        """Set joint positions (clamped to limits)."""
        q_clamped = np.zeros(self.n_joints)
        for i in range(self.n_joints):
            q_clamped[i] = np.clip(q[i], self.joint_limits[i, 0], self.joint_limits[i, 1])
        
        for i, jid in enumerate(self.joint_ids):
            self.data.qpos[self.model.jnt_qposadr[jid]] = q_clamped[i]
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose.
        
        Returns:
            (xyz, R) where xyz is 3D position, R is 3x3 rotation matrix
        """
        mujoco.mj_forward(self.model, self.data)
        xyz = self.data.site_xpos[self.site_id].copy()
        
        # Get rotation matrix from site
        R = self.data.site_xmat[self.site_id].reshape(3, 3).copy()
        
        return xyz, R
    
    def compute_jacobian(self) -> np.ndarray:
        """
        Compute 3x5 position Jacobian for the end-effector.
        
        Returns:
            3x5 Jacobian matrix (position only)
        """
        nv = self.model.nv
        jacp = np.zeros((3, nv))
        jacr = np.zeros((3, nv))
        
        mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.site_id)
        
        # Extract columns for arm joints
        J = np.zeros((3, self.n_joints))
        for i, jid in enumerate(self.joint_ids):
            dof_idx = self.model.jnt_dofadr[jid]
            J[:, i] = jacp[:, dof_idx]
        
        return J
    
    def inverse_kinematics(
        self,
        target_xyz: np.ndarray,
        q_init: Optional[np.ndarray] = None,
        max_iter: Optional[int] = None,
        tol: Optional[float] = None
    ) -> Tuple[np.ndarray, bool, float]:
        """
        Damped Least Squares IK for position-only control.
        
        Algorithm:
        1. Compute position error: e = target - current
        2. Compute Jacobian: J = ∂pos/∂q
        3. Solve damped system: (J^T J + λ²I)Δq = J^T e
        4. Update: q ← q + α·Δq (with step size control)
        5. Clamp q to joint limits
        
        Args:
            target_xyz: Target 3D position
            q_init: Initial joint configuration (default: current)
            max_iter: Max iterations (default: self.ik_max_iter)
            tol: Convergence tolerance in meters (default: self.ik_tol)
            
        Returns:
            (q, success, final_error) tuple
        """
        max_iter = max_iter or self.ik_max_iter
        tol = tol or self.ik_tol
        
        q = q_init.copy() if q_init is not None else self.get_joint_positions()
        
        for iteration in range(max_iter):
            # Forward kinematics
            self.set_joint_positions(q)
            mujoco.mj_forward(self.model, self.data)
            
            current_xyz = self.data.site_xpos[self.site_id].copy()
            error = target_xyz - current_xyz
            error_norm = np.linalg.norm(error)
            
            # Check convergence
            if error_norm < tol:
                return q, True, error_norm
            
            # Compute Jacobian
            J = self.compute_jacobian()
            
            # Adaptive damping: increases with error magnitude
            lambda_damping = self.ik_lambda_base * (1.0 + error_norm)
            
            # Damped least squares solution
            JtJ = J.T @ J + lambda_damping**2 * np.eye(self.n_joints)
            dq = np.linalg.solve(JtJ, J.T @ error)
            
            # Step size control to prevent overshooting
            dq_norm = np.linalg.norm(dq)
            step_size = min(1.0, 0.5 / (dq_norm + 1e-6))
            
            q += step_size * dq
            
            # Clamp to joint limits
            for i in range(self.n_joints):
                q[i] = np.clip(q[i], self.joint_limits[i, 0], self.joint_limits[i, 1])
        
        # Failed to converge - compute final error
        self.set_joint_positions(q)
        mujoco.mj_forward(self.model, self.data)
        current_xyz = self.data.site_xpos[self.site_id].copy()
        final_error = np.linalg.norm(target_xyz - current_xyz)
        
        return q, False, final_error
    
    def home(self) -> bool:
        """
        Move to home configuration.
        
        Returns:
            True on success
        """
        self.set_joint_positions(self.home_config)
        
        # Set actuator targets to home configuration
        for i, jid in enumerate(self.joint_ids):
            actuator_name = f"act_{self.arm_joint_names[i]}"
            act_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
            self.data.ctrl[act_id] = self.home_config[i]
        
        # Settle dynamics
        for _ in range(100):
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def move_cartesian(
        self,
        target_xyz: np.ndarray,
        duration: float = 2.0
    ) -> bool:
        """
        Move end-effector to target position with smooth trajectory.
        
        Uses linear interpolation in joint space between current and target configurations.
        
        Args:
            target_xyz: Target 3D position
            duration: Motion duration in seconds
            
        Returns:
            True if target reached within tolerance
            
        Raises:
            RuntimeError: If IK fails to find a solution
        """
        # Compute IK
        q_current = self.get_joint_positions()
        q_target, success, error = self.inverse_kinematics(target_xyz, q_init=q_current)
        
        if not success:
            raise RuntimeError(
                f"IK failed to converge: final error = {error:.4f}m (tolerance = {self.ik_tol}m)"
            )
        
        # Generate smooth trajectory (linear interpolation in joint space)
        timestep = self.model.opt.timestep
        n_steps = int(duration / timestep)
        
        for step in range(n_steps):
            alpha = (step + 1) / n_steps  # 0 → 1
            q_interp = (1 - alpha) * q_current + alpha * q_target
            
            # Set actuator targets
            for i, jid in enumerate(self.joint_ids):
                actuator_name = f"act_{self.arm_joint_names[i]}"
                act_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
                self.data.ctrl[act_id] = q_interp[i]
            
            mujoco.mj_step(self.model, self.data)
        
        # Verify final position
        xyz_final, _ = self.get_ee_pose()
        final_error = np.linalg.norm(target_xyz - xyz_final)
        
        return final_error < 2 * self.ik_tol  # Allow 2cm tolerance
    
    def gripper_open(self) -> bool:
        """
        Open gripper (visual animation + release any held object).
        
        Returns:
            True on success
        """
        # Animate jaw
        self.data.ctrl[self.gripper_actuator_id] = -0.175  # Open position
        
        # Release held object
        if self.held_object is not None:
            weld_info = next(
                (w for w in self.weld_constraints if w['body_name'] == self.held_object),
                None
            )
            if weld_info:
                self.data.eq_active[weld_info['eq_id']] = 0
            self.held_object = None
        
        # Settle
        for _ in range(50):
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def gripper_close(self) -> bool:
        """
        Close gripper (visual animation + weld nearby object).
        
        Checks proximity to all graspable objects and welds the closest
        one if within threshold distance. Uses end-effector site position
        for proximity detection.
        
        Returns:
            True on success
        """
        # Animate jaw
        self.data.ctrl[self.gripper_actuator_id] = 1.75  # Closed position
        
        # Update physics to get current positions
        mujoco.mj_forward(self.model, self.data)
        ee_pos = self.data.site_xpos[self.site_id].copy()
        
        # Find closest graspable object
        closest_dist = float('inf')
        closest_weld = None
        
        for weld_info in self.weld_constraints:
            body_pos = self.data.xpos[weld_info['body_id']].copy()
            dist = np.linalg.norm(ee_pos - body_pos)
            
            if dist < closest_dist:
                closest_dist = dist
                closest_weld = weld_info
        
        # Weld if within threshold
        if closest_weld and closest_dist < self.grasp_distance_threshold:
            self.data.eq_active[closest_weld['eq_id']] = 1
            self.held_object = closest_weld['body_name']
        
        # Settle
        for _ in range(50):
            mujoco.mj_step(self.model, self.data)
        
        return True
    
    def is_holding(self) -> bool:
        """Check if robot is holding an object."""
        return self.held_object is not None
    
    def step(self, n: int = 1) -> None:
        """Step simulation forward."""
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
        
        # Update scene
        renderer.update_scene(self.data, camera=camera)
        
        # Render
        rgb = renderer.render()
        
        return rgb
    
    def describe(self) -> dict:
        """
        Get robot description.
        
        Returns:
            Dictionary with robot info
        """
        xyz, R = self.get_ee_pose()
        q = self.get_joint_positions()
        
        return {
            'robot_id': 'so101_from_scratch',
            'dof': self.n_joints,
            'joints': self.arm_joint_names,
            'ee_position': xyz.tolist(),
            'joint_positions': q.tolist(),
            'holding_object': self.held_object,
            'num_weld_constraints': len(self.weld_constraints),
            'ik_method': 'Damped Least Squares (position-only)',
            'ik_tolerance': self.ik_tol,
            'grasp_method': 'weld-based'
        }


# Test harness
if __name__ == '__main__':
    print("=" * 60)
    print("SO-101 Robot Driver - From Scratch Test")
    print("=" * 60)
    
    # Build robot
    robot = Robot.build_from_mjcf('mjcf.xml')
    print("\n✓ Robot built successfully")
    
    # Home
    robot.home()
    print("✓ Homed to zero configuration")
    
    # Describe
    desc = robot.describe()
    print(f"\n✓ Robot Description:")
    for key, value in desc.items():
        print(f"  {key}: {value}")
    
    # Test Cartesian motion
    print("\n" + "=" * 60)
    print("Testing Cartesian Motion")
    print("=" * 60)
    
    xyz_start, _ = robot.get_ee_pose()
    print(f"Start position: {xyz_start}")
    
    target_xyz = xyz_start + np.array([0.05, 0.0, 0.0])
    print(f"Target position: {target_xyz} (+5cm in X)")
    
    try:
        success = robot.move_cartesian(target_xyz, duration=1.0)
        xyz_final, _ = robot.get_ee_pose()
        error = np.linalg.norm(target_xyz - xyz_final)
        
        print(f"✓ Motion completed: success={success}")
        print(f"  Final position: {xyz_final}")
        print(f"  Position error: {error*100:.2f}cm")
        
        if error < 0.02:
            print("  ✓ Within 2cm tolerance")
        else:
            print("  ✗ Exceeds 2cm tolerance")
    except RuntimeError as e:
        print(f"✗ Motion failed: {e}")
    
    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)
