"""
Driver for SO-101 6-DOF tabletop robotic arm with visual gripper.
Implements FK/IK, Cartesian motion, gripper control, and object manipulation.
"""

import mujoco
import numpy as np
import os
from typing import Tuple, List, Optional, Dict, Any


class Robot:
    """SO-101 6-DOF robotic arm driver."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize robot with loaded MuJoCo model and data."""
        self.model = model
        self.data = data
        
        # Cache important IDs
        self._cache_ids()
        
        # Joint limits from study.json
        self.joint_limits = [
            [-1.92, 1.92],    # shoulder_pan
            [-1.75, 1.75],    # shoulder_lift
            [-1.69, 1.69],    # elbow_flex
            [-1.66, 1.66],    # wrist_flex
            [-2.74, 2.84],    # wrist_roll
            [-0.175, 1.75]    # jaw_visual_joint (gripper)
        ]
        
        # IK parameters
        self.ik_damping = 0.1
        self.ik_max_iter = 150
        self.ik_tol = 1e-2  # 1cm tolerance for practical use
        self.ik_step_size = 0.3
        self.nullspace_gain = 0.05
        
        # Motion control parameters
        self.cartesian_kp = 0.5  # Proportional gain for Cartesian motion
        self.joint_kp = 0.8      # Proportional gain for joint motion
        self.joint_kd = 0.1      # Derivative gain for joint motion
        
        # Gripper state
        self._gripper_open_pos = 0.0
        self._gripper_closed_pos = 1.75
        self._gripper_threshold = 0.1  # Position threshold for open/closed
        
        # Object names from study.json
        self.object_names = ["banana", "mug", "bottle", "screwdriver", "duck", "lego"]
        
        # Cache for weld constraints (gripper to objects)
        self._weld_constraints = {}
        self._cache_weld_constraints()
        
        # Modify weld constraints to put objects at end-effector position
        self._fix_weld_constraints()
        
        # Track which object is currently grasped
        self._grasped_object = None
        
        # Initialize to home position
        self.home()
    
    def _cache_ids(self):
        """Cache important MuJoCo object IDs."""
        # Arm joint indices (0-5 based on MJCF structure)
        self.joint_ids = list(range(6))
        
        # End-effector site
        self.ee_site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        if self.ee_site_id < 0:
            raise ValueError("End-effector site 'ee_site' not found in model")
        
        # Gripper body
        self.gripper_body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "gripper_link")
        if self.gripper_body_id < 0:
            raise ValueError("Gripper body 'gripper_link' not found in model")
        
        # Actuator indices (should match joint order)
        self.actuator_names = [
            "act_shoulder_pan", "act_shoulder_lift", "act_elbow_flex",
            "act_wrist_flex", "act_wrist_roll", "act_jaw_visual"
        ]
        self.actuator_ids = []
        for name in self.actuator_names:
            idx = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if idx >= 0:
                self.actuator_ids.append(idx)
            else:
                raise ValueError(f"Actuator '{name}' not found in model")
    
    def _cache_weld_constraints(self):
        """Cache weld constraint indices for object grasping."""
        for i in range(self.model.neq):
            if self.model.eq_type[i] == 1:  # Type 1 = weld constraint
                obj1 = self.model.eq_obj1id[i]
                obj2 = self.model.eq_obj2id[i]
                
                # Get body names
                name1 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, obj1)
                name2 = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, obj2)
                
                # Store if it connects gripper to an object
                if name1 == "gripper_link" and name2 in self.object_names:
                    self._weld_constraints[name2] = i
                elif name2 == "gripper_link" and name1 in self.object_names:
                    self._weld_constraints[name1] = i
    
    def _fix_weld_constraints(self):
        """Fix weld constraints to put objects at end-effector position.
        
        The original MJCF has weld constraints with relative transforms that
        put objects far from the end-effector. We modify them so objects are
        positioned at the end-effector site when grasped.
        """
        # End-effector site is at [0, 0, -0.098] relative to gripper_link
        # We want grasped objects to be at this position
        target_relpos = np.array([0.0, 0.0, -0.098])
        target_quat = np.array([1.0, 0.0, 0.0, 0.0])  # Identity quaternion
        
        for weld_id in self._weld_constraints.values():
            # Check if this is a weld constraint
            if self.model.eq_type[weld_id] == 1:  # weld
                # Modify eq_data to put object at end-effector position
                # eq_data structure for weld constraints:
                # - anchor (3): [0, 0, 0]
                # - relpose position (3): target_relpos
                # - relpose quaternion (4): target_quat
                # - extra (1): 1.0
                self.model.eq_data[weld_id][0:3] = [0.0, 0.0, 0.0]  # anchor
                self.model.eq_data[weld_id][3:6] = target_relpos    # relpose position
                self.model.eq_data[weld_id][6:10] = target_quat     # relpose quaternion
                self.model.eq_data[weld_id][10] = 1.0               # extra
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        if not os.path.exists(mjcf_path):
            raise FileNotFoundError(f"MJCF file not found: {mjcf_path}")
        
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move robot to home position (all joints at zero).
        
        Returns:
            True if successful
        """
        try:
            # Set joint positions to zero
            self.data.qpos[self.joint_ids] = 0.0
            
            # Set actuator controls to zero
            self.data.ctrl[self.actuator_ids] = 0.0
            
            # Step simulation to apply
            mujoco.mj_forward(self.model, self.data)
            
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current joint positions.
        
        Returns:
            Array of joint positions (radians)
        """
        return self.data.qpos[self.joint_ids].copy()
    
    def step(self, n: int = 1) -> None:
        """
        Step simulation n times.
        
        Args:
            n: Number of steps to take
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """Render current scene (placeholder - would need visualization setup)."""
        print("Rendering not implemented in headless mode")
    
    def describe(self) -> str:
        """
        Describe the robot configuration.
        
        Returns:
            Description string
        """
        ee_pos = self.get_ee_pose()[0]
        joint_pos = self.get_joint_positions()
        
        desc = f"""SO-101 6-DOF Robotic Arm:
  - End-effector position: {ee_pos}
  - Joint positions: {joint_pos}
  - Gripper position: {joint_pos[5]:.3f} ({'open' if self.is_gripper_open() else 'closed'})
  - Objects in scene: {self.get_object_names()}
  - Holding object: {self.is_holding()} ({self._grasped_object})
"""
        return desc
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose (position and orientation matrix).
        
        Returns:
            Tuple of (position (3,), orientation matrix (3,3))
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        mat = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return pos, mat
    
    def _compute_jacobian(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute Jacobian for end-effector.
        
        Returns:
            Tuple of (position Jacobian (3, nv), rotation Jacobian (3, nv))
        """
        jac_pos = np.zeros((3, self.model.nv))
        jac_rot = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jac_pos, jac_rot, self.ee_site_id)
        return jac_pos[:, :6], jac_rot[:, :6]  # Only arm joints
    
    def _ik_position(self, target_pos: np.ndarray, max_iter: int = None, 
                     tol: float = None, verbose: bool = False) -> Optional[np.ndarray]:
        """
        Inverse kinematics for position target only.
        
        Args:
            target_pos: Target position (3,)
            max_iter: Maximum iterations (defaults to self.ik_max_iter)
            tol: Tolerance (defaults to self.ik_tol)
            verbose: Print convergence information
            
        Returns:
            Joint positions that achieve target, or None if failed
        """
        if max_iter is None:
            max_iter = self.ik_max_iter
        if tol is None:
            tol = self.ik_tol
        
        q_current = self.get_joint_positions()
        jac_pos = np.zeros((3, self.model.nv))
        
        # Secondary objective: prefer home configuration
        q_preferred = np.zeros(6)
        
        # Adaptive damping based on error
        min_damping = 0.01
        max_damping = 0.5
        
        for i in range(max_iter):
            mujoco.mj_forward(self.model, self.data)
            current_pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Compute error
            error = target_pos - current_pos
            error_norm = np.linalg.norm(error)
            
            if verbose and i % 20 == 0:
                print(f"IK iteration {i}: error = {error_norm:.6f}")
            
            if error_norm < tol:
                if verbose:
                    print(f"IK converged after {i} iterations with error {error_norm:.6f}")
                return q_current
            
            # Adaptive damping: larger damping for larger errors
            damping = min_damping + (max_damping - min_damping) * min(error_norm / 0.5, 1.0)
            
            # Compute Jacobian
            mujoco.mj_jacSite(self.model, self.data, jac_pos, None, self.ee_site_id)
            J = jac_pos[:, :6]
            
            # Damped Least Squares update
            J_JT = J @ J.T
            lambda_sq = damping**2
            update_primary = J.T @ np.linalg.solve(J_JT + lambda_sq * np.eye(3), error)
            
            # Nullspace projection for secondary objective
            J_pseudo = J.T @ np.linalg.solve(J_JT + lambda_sq * np.eye(3), np.eye(3))
            nullspace_proj = np.eye(6) - J_pseudo @ J
            
            # Secondary objective: move toward preferred configuration
            q_error = q_preferred - q_current
            update_secondary = nullspace_proj @ q_error
            
            # Combined update
            update = update_primary + self.nullspace_gain * update_secondary
            
            # Apply update with adaptive step size
            step_size = self.ik_step_size * min(1.0, 0.1 / (error_norm + 1e-6))
            q_current += step_size * update
            
            # Clamp to joint limits
            for j in range(6):
                q_current[j] = np.clip(q_current[j], 
                                      self.joint_limits[j][0], 
                                      self.joint_limits[j][1])
            
            self.data.qpos[self.joint_ids] = q_current
        
        # Check final error even if didn't converge to strict tolerance
        mujoco.mj_forward(self.model, self.data)
        final_pos = self.data.site_xpos[self.ee_site_id].copy()
        final_error = np.linalg.norm(target_pos - final_pos)
        
        if final_error < 0.02:  # 2cm tolerance for practical use
            if verbose:
                print(f"IK reached practical tolerance after {max_iter} iterations: {final_error:.6f}")
            return q_current
        
        if verbose:
            print(f"IK failed to converge after {max_iter} iterations, final error: {final_error:.6f}")
        return None
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target position using IK.
        
        Args:
            target_xyz: Target position (3,)
            duration: Motion duration in seconds (not strictly enforced)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Solve IK for target position
            q_target = self._ik_position(target_xyz, verbose=False)
            if q_target is None:
                print(f"IK failed to find solution for target {target_xyz}")
                return False
            
            # Get current joint positions
            q_current = self.get_joint_positions()
            
            # Interpolate trajectory
            n_steps = max(1, int(duration / self.model.opt.timestep))
            
            for i in range(n_steps):
                # Smooth interpolation (cubic ease-in-out)
                alpha = i / n_steps
                t = alpha * alpha * (3 - 2 * alpha)  # Smooth step
                q_interp = q_current + t * (q_target - q_current)
                
                # Clamp to limits
                for j in range(6):
                    q_interp[j] = np.clip(q_interp[j], 
                                         self.joint_limits[j][0], 
                                         self.joint_limits[j][1])
                
                # Set joint positions
                self.data.qpos[self.joint_ids] = q_interp
                
                # PD control on actuators
                for j, act_id in enumerate(self.actuator_ids):
                    error = q_interp[j] - self.data.qpos[self.joint_ids[j]]
                    qd_error = 0 - self.data.qvel[self.joint_ids[j]]  # Target velocity = 0
                    control = self.joint_kp * error + self.joint_kd * qd_error
                    self.data.ctrl[act_id] = control
                
                # Step simulation - CRITICAL: step multiple times to allow
                # constraint solver to adjust grasped objects
                if self.is_holding():
                    # When holding an object, step more to allow constraint solver to work
                    self.step(10)
                else:
                    self.step()
            
            # Final position
            self.data.qpos[self.joint_ids] = q_target
            # Step more after final position when holding an object
            if self.is_holding():
                self.step(50)
            mujoco.mj_forward(self.model, self.data)
            
            # Verify final position - use relaxed tolerance when holding an object
            final_pos = self.data.site_xpos[self.ee_site_id].copy()
            error = np.linalg.norm(target_xyz - final_pos)
            
            # When holding an object, use more relaxed tolerance (5cm instead of 2cm)
            # because constraints make it harder to reach exact positions
            tolerance = 0.05 if self.is_holding() else 0.02
            
            if error > tolerance:
                print(f"Cartesian motion error too large: {error:.3f}m (tolerance: {tolerance:.3f}m)")
                return False
            
            return True
            
        except Exception as e:
            print(f"Error in move_cartesian: {e}")
            return False
    
    def gripper_open(self) -> bool:
        """
        Open gripper and release any grasped objects.
        
        Returns:
            True if successful
        """
        try:
            # Set gripper joint to open position
            self.data.qpos[5] = self._gripper_open_pos
            
            # Set actuator control
            gripper_act_id = self.actuator_ids[5]
            self.data.ctrl[gripper_act_id] = self._gripper_open_pos
            
            # Release all weld constraints
            for obj_name, weld_id in self._weld_constraints.items():
                self.data.eq_active[weld_id] = 0
            
            # Clear grasped object
            self._grasped_object = None
            
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in gripper_open: {e}")
            return False
    
    def gripper_close(self) -> bool:
        """
        Close gripper and grasp the closest object if within range.
        
        Returns:
            True if successful
        """
        try:
            # Set gripper joint to closed position
            self.data.qpos[5] = self._gripper_closed_pos
            
            # Set actuator control
            gripper_act_id = self.actuator_ids[5]
            self.data.ctrl[gripper_act_id] = self._gripper_closed_pos
            
            mujoco.mj_forward(self.model, self.data)
            
            # Find closest object to end-effector
            ee_pos = self.get_ee_pose()[0]
            closest_obj = None
            closest_dist = float('inf')
            
            for obj_name in self.object_names:
                try:
                    obj_pos = self.get_object_position(obj_name)
                    dist = np.linalg.norm(ee_pos - obj_pos)
                    if dist < closest_dist:
                        closest_dist = dist
                        closest_obj = obj_name
                except KeyError:
                    continue
            
            # If an object is within grasping distance (10cm), grasp it
            if closest_obj is not None and closest_dist < 0.1:
                # Don't call grasp_object() to avoid recursion
                # Instead, enable the weld constraint directly
                if closest_obj in self._weld_constraints:
                    weld_id = self._weld_constraints[closest_obj]
                    self.data.eq_active[weld_id] = 1
                    self._grasped_object = closest_obj
                    # Step simulation to apply constraint
                    self.step(50)  # Step more to allow constraint to settle
            
            return True
        except Exception as e:
            print(f"Error in gripper_close: {e}")
            return False
    
    def is_gripper_open(self) -> bool:
        """
        Check if gripper is open.
        
        Returns:
            True if gripper position is near open position
        """
        gripper_pos = self.data.qpos[5]
        return abs(gripper_pos - self._gripper_open_pos) < self._gripper_threshold
    
    def is_gripper_closed(self) -> bool:
        """
        Check if gripper is closed.
        
        Returns:
            True if gripper position is near closed position
        """
        gripper_pos = self.data.qpos[5]
        return abs(gripper_pos - self._gripper_closed_pos) < self._gripper_threshold
    
    def grasp_object(self, object_name: str) -> bool:
        """
        Attempt to grasp an object by enabling weld constraint.
        
        Args:
            object_name: Name of object to grasp
            
        Returns:
            True if successful, False otherwise
        """
        if object_name not in self._weld_constraints:
            print(f"Object '{object_name}' not found or not graspable")
            return False
        
        # Close gripper first (but don't call the full gripper_close to avoid recursion)
        self.data.qpos[5] = self._gripper_closed_pos
        gripper_act_id = self.actuator_ids[5]
        self.data.ctrl[gripper_act_id] = self._gripper_closed_pos
        mujoco.mj_forward(self.model, self.data)
        
        # Enable weld constraint
        weld_id = self._weld_constraints[object_name]
        self.data.eq_active[weld_id] = 1
        self._grasped_object = object_name
        
        # Step simulation to apply constraint
        self.step(50)
        
        return True
    
    def release_object(self, object_name: str) -> bool:
        """
        Release a grasped object by disabling weld constraint.
        
        Args:
            object_name: Name of object to release
            
        Returns:
            True if successful, False otherwise
        """
        if object_name not in self._weld_constraints:
            print(f"Object '{object_name}' not found or not graspable")
            return False
        
        # Disable weld constraint
        weld_id = self._weld_constraints[object_name]
        self.data.eq_active[weld_id] = 0
        
        # Clear grasped object
        if self._grasped_object == object_name:
            self._grasped_object = None
        
        # Open gripper
        self.gripper_open()
        
        # Step simulation to release
        self.step(10)
        
        return True
    
    def is_holding(self) -> bool:
        """
        Check if robot is holding any object.
        
        Returns:
            True if any weld constraint is active
        """
        for weld_id in self._weld_constraints.values():
            if self.data.eq_active[weld_id]:
                return True
        return False
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get world position of a named scene object.
        
        Args:
            name: Object name (e.g., "banana", "mug")
            
        Returns:
            Object position (3,)
            
        Raises:
            KeyError: If object not found
        """
        # Try body first
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.xpos[body_id].copy()
        
        # Try geom
        geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f"{name}_geom")
        if geom_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.geom_xpos[geom_id].copy()
        
        # Try site
        site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
        if site_id >= 0:
            mujoco.mj_forward(self.model, self.data)
            return self.data.site_xpos[site_id].copy()
        
        raise KeyError(f"Object '{name}' not found in model")
    
    def get_object_names(self) -> List[str]:
        """
        Get names of manipulable scene objects.
        
        Returns:
            List of object names
        """
        return self.object_names.copy()
    
    def get_object_distances(self) -> Dict[str, float]:
        """
        Get distances from end-effector to all objects.
        
        Returns:
            Dictionary mapping object names to distances
        """
        ee_pos = self.get_ee_pose()[0]
        distances = {}
        
        for obj_name in self.object_names:
            try:
                obj_pos = self.get_object_position(obj_name)
                distance = np.linalg.norm(ee_pos - obj_pos)
                distances[obj_name] = distance
            except KeyError:
                distances[obj_name] = float('inf')
        
        return distances
    
    def move_to_object(self, object_name: str, offset: np.ndarray = None) -> bool:
        """
        Move end-effector near an object.
        
        Args:
            object_name: Name of object to approach
            offset: Optional offset from object position (default: [0, 0, 0.05])
            
        Returns:
            True if successful
        """
        if offset is None:
            offset = np.array([0.0, 0.0, 0.05])  # 5cm above object
        
        try:
            obj_pos = self.get_object_position(object_name)
            target_pos = obj_pos + offset
            return self.move_cartesian(target_pos)
        except KeyError:
            print(f"Object '{object_name}' not found")
            return False
        except Exception as e:
            print(f"Error in move_to_object: {e}")
            return False


# Example usage
if __name__ == "__main__":
    # Test the driver
    robot = Robot.build_from_mjcf("mjcf.xml")
    
    print("Robot initialized:")
    print(robot.describe())
    
    # Test home position
    robot.home()
    print("\nAfter home():")
    print(robot.describe())
    
    # Test Cartesian motion
    ee_pos, _ = robot.get_ee_pose()
    target_pos = ee_pos + np.array([0.05, 0.0, 0.0])  # Move 5cm in X
    print(f"\nMoving to target: {target_pos}")
    success = robot.move_cartesian(target_pos)
    print(f"Motion successful: {success}")
    
    # Test gripper
    print("\nTesting gripper...")
    robot.gripper_open()
    print(f"Gripper open: {robot.is_gripper_open()}")
    
    robot.gripper_close()
    print(f"Gripper closed: {robot.is_gripper_closed()}")
    
    # Test object positions
    print("\nObject positions:")
    for obj_name in robot.get_object_names():
        try:
            pos = robot.get_object_position(obj_name)
            print(f"  {obj_name}: {pos}")
        except KeyError:
            print(f"  {obj_name}: not found")
    
    # Test object distances
    distances = robot.get_object_distances()
    print("\nDistances from end-effector:")
    for obj_name, dist in distances.items():
        print(f"  {obj_name}: {dist:.3f}m")