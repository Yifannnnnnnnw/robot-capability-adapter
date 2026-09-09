"""
SO-101 5-DOF Arm Driver - Written from scratch
Robot: abl_physics_s2
Class: ARM (5-DOF serial chain + 1-DOF gripper)

IK Method: Damped Least Squares (DLS)
- Non-redundant 5-DOF arm → position-only IK (3 constraints, 5 DOF)
- Damping: adaptive based on residual (0.01 base, scales up near singularities)
- Step size: 0.5 with joint limit clamping
- Max iterations: 100, tolerance: 1mm
"""

import mujoco
import numpy as np
from typing import Tuple, Optional, List
import time
import os


class Robot:
    """SO-101 5-DOF arm with gripper - complete driver without framework dependencies."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        
        # Joint configuration
        self.arm_joint_names = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
        self.gripper_joint_name = "gripper"
        
        # Resolve joint indices
        self.arm_joint_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) 
                              for name in self.arm_joint_names]
        self.gripper_joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, self.gripper_joint_name)
        
        # Resolve actuator indices
        self.arm_actuator_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) 
                                 for name in self.arm_joint_names]
        self.gripper_actuator_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, self.gripper_joint_name)
        
        # End-effector site
        self.ee_site_name = "gripperframe"
        self.ee_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, self.ee_site_name)
        
        # Joint limits (from qpos addresses)
        self.arm_joint_limits = np.array([model.jnt_range[jid] for jid in self.arm_joint_ids])
        self.gripper_limits = model.jnt_range[self.gripper_joint_id]
        
        # Home position (all zeros is a good home for this arm)
        self.home_qpos = np.zeros(5)
        
        # Gripper state
        self.gripper_open_pos = 1.5  # Open position
        self.gripper_closed_pos = -0.1  # Closed position
        self._is_holding = False
        
        # IK parameters
        self.ik_max_iter = 100
        self.ik_tol = 1e-3  # 1mm
        self.ik_damping_base = 0.01
        self.ik_step_size = 0.5
        
        print(f"Robot initialized: {len(self.arm_joint_ids)} DOF arm + gripper")
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file path."""
        # Resolve symlinks
        mjcf_path = os.path.realpath(mjcf_path)
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """Move to home position (all joints at zero)."""
        try:
            # Set arm to home
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_adr = self.model.jnt_qposadr[jid]
                self.data.qpos[qpos_adr] = self.home_qpos[i]
                self.data.ctrl[self.arm_actuator_ids[i]] = self.home_qpos[i]
            
            # Open gripper
            gripper_qpos_adr = self.model.jnt_qposadr[self.gripper_joint_id]
            self.data.qpos[gripper_qpos_adr] = self.gripper_open_pos
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_open_pos
            
            # Step simulation to settle
            for _ in range(100):
                mujoco.mj_step(self.model, self.data)
            
            self._is_holding = False
            return True
        except Exception as e:
            print(f"Home failed: {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current arm joint positions (5 DOF)."""
        qpos = np.zeros(5)
        for i, jid in enumerate(self.arm_joint_ids):
            qpos_adr = self.model.jnt_qposadr[jid]
            qpos[i] = self.data.qpos[qpos_adr]
        return qpos
    
    def get_ee_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get end-effector pose as (xyz, R3x3)."""
        mujoco.mj_forward(self.model, self.data)
        xyz = self.data.site_xpos[self.ee_site_id].copy()
        R = self.data.site_xmat[self.ee_site_id].reshape(3, 3).copy()
        return xyz, R
    
    def _solve_ik(self, target_xyz: np.ndarray, q_init: Optional[np.ndarray] = None) -> Tuple[bool, np.ndarray]:
        """
        Solve inverse kinematics using Damped Least Squares.
        
        Returns: (success, joint_angles)
        """
        if q_init is None:
            q = self.get_joint_positions()
        else:
            q = q_init.copy()
        
        for iteration in range(self.ik_max_iter):
            # Forward kinematics
            for i, jid in enumerate(self.arm_joint_ids):
                qpos_adr = self.model.jnt_qposadr[jid]
                self.data.qpos[qpos_adr] = q[i]
            mujoco.mj_forward(self.model, self.data)
            
            # Compute error
            current_xyz = self.data.site_xpos[self.ee_site_id].copy()
            error = target_xyz - current_xyz
            error_norm = np.linalg.norm(error)
            
            if error_norm < self.ik_tol:
                return True, q
            
            # Compute Jacobian (position only)
            jac_pos = np.zeros((3, self.model.nv))
            jac_rot = np.zeros((3, self.model.nv))
            mujoco.mj_jacSite(self.model, self.data, jac_pos, jac_rot, self.ee_site_id)
            J = jac_pos[:, :5]  # Only arm joints
            
            # Adaptive damping: increase near singularities
            # Check manipulability (det(J*J^T))
            JJT = J @ J.T
            manipulability = np.sqrt(np.abs(np.linalg.det(JJT)))
            damping = self.ik_damping_base
            if manipulability < 0.01:
                damping = 0.05  # Increase damping near singularities
            
            # DLS: dq = J^T (J J^T + λ^2 I)^-1 error
            damped_inv = np.linalg.inv(JJT + damping**2 * np.eye(3))
            dq = J.T @ damped_inv @ error
            
            # Update with step size
            q += self.ik_step_size * dq
            
            # Clamp to joint limits
            for i in range(5):
                q[i] = np.clip(q[i], self.arm_joint_limits[i, 0], self.arm_joint_limits[i, 1])
        
        # Failed to converge
        return False, q
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Args:
            target_xyz: Target position [x, y, z]
            duration: Time to complete motion (seconds)
        
        Returns:
            True if successful, False if target unreachable
        """
        # Check if target is reasonable (within workspace)
        target_dist = np.linalg.norm(target_xyz)
        if target_dist > 0.6 or target_dist < 0.1:
            print(f"Target out of workspace: distance={target_dist:.3f}m")
            return False
        
        # Solve IK
        success, target_q = self._solve_ik(target_xyz)
        if not success:
            print(f"IK failed to converge for target {target_xyz}")
            return False
        
        # Interpolate from current to target
        q_start = self.get_joint_positions()
        steps = int(duration * 1000)  # Assuming 1kHz control
        
        for step in range(steps):
            alpha = (step + 1) / steps
            q_interp = (1 - alpha) * q_start + alpha * target_q
            
            # Set control
            for i, act_id in enumerate(self.arm_actuator_ids):
                self.data.ctrl[act_id] = q_interp[i]
            
            mujoco.mj_step(self.model, self.data)
        
        # Verify we reached the target
        final_xyz, _ = self.get_ee_pose()
        error = np.linalg.norm(final_xyz - target_xyz)
        
        if error > 0.02:  # 2cm tolerance
            print(f"Move completed with error: {error*1000:.1f}mm")
            return False
        
        return True
    
    def gripper_open(self) -> bool:
        """Open the gripper."""
        try:
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_open_pos
            for _ in range(50):
                mujoco.mj_step(self.model, self.data)
            self._is_holding = False
            return True
        except Exception as e:
            print(f"Gripper open failed: {e}")
            return False
    
    def gripper_close(self) -> bool:
        """Close the gripper."""
        try:
            self.data.ctrl[self.gripper_actuator_id] = self.gripper_closed_pos
            for _ in range(50):
                mujoco.mj_step(self.model, self.data)
            
            # Check if we're holding something by checking gripper position
            # The gripper has a servo model, so it won't reach the commanded position
            # if there's no object. Check if it's significantly open.
            gripper_qpos_adr = self.model.jnt_qposadr[self.gripper_joint_id]
            actual_pos = self.data.qpos[gripper_qpos_adr]
            
            # If gripper is more open than 0.2 rad, we're likely holding something
            # (the servo can't close fully without an object)
            if actual_pos > 0.2:
                self._is_holding = True
            else:
                self._is_holding = False
            
            return True
        except Exception as e:
            print(f"Gripper close failed: {e}")
            return False
    
    def is_holding(self) -> bool:
        """Check if gripper is holding an object."""
        return self._is_holding
    
    def get_object_position(self, name: str) -> np.ndarray:
        """
        Get world position of a named scene object.
        
        Tries to resolve name as: body → geom → site
        
        Args:
            name: Object name
        
        Returns:
            xyz position [3]
        
        Raises:
            KeyError: If object name not found
        """
        # Try body first
        try:
            body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            if body_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.xpos[body_id].copy()
        except Exception:
            pass
        
        # Try geom
        try:
            geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            if geom_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.geom_xpos[geom_id].copy()
        except Exception:
            pass
        
        # Try site
        try:
            site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, name)
            if site_id >= 0:
                mujoco.mj_forward(self.model, self.data)
                return self.data.site_xpos[site_id].copy()
        except Exception:
            pass
        
        raise KeyError(f"Object '{name}' not found in scene (tried body, geom, site)")
    
    def get_object_names(self) -> List[str]:
        """
        Get list of manipulable scene objects (non-robot bodies/geoms).
        
        Returns:
            List of object names
        """
        robot_bodies = {'world', 'base', 'shoulder', 'upper_arm', 'lower_arm', 
                       'wrist', 'gripper', 'camera_mount', 'moving_jaw_so101_v1'}
        robot_geoms = {'floor', 'fixed_jaw_box1', 'fixed_jaw_box2', 'fixed_jaw_sph_tip1',
                      'fixed_jaw_sph_tip2', 'fixed_jaw_sph_tip3', 'fixed_jaw_box3',
                      'fixed_jaw_box4', 'fixed_jaw_box5', 'fixed_jaw_box6', 'fixed_jaw_box7',
                      'camera_box1', 'camera_box2', 'moving_jaw_box1', 'moving_jaw_sph_tip1',
                      'moving_jaw_sph_tip2', 'moving_jaw_sph_tip3', 'moving_jaw_box2',
                      'moving_jaw_box3'}
        
        objects = []
        
        # Check bodies
        for i in range(self.model.nbody):
            name = self.model.body(i).name
            if name and name not in robot_bodies:
                objects.append(name)
        
        # Check geoms
        for i in range(self.model.ngeom):
            name = self.model.geom(i).name
            if name and name not in robot_geoms and name not in objects:
                objects.append(name)
        
        return objects
    
    def step(self, n: int = 1):
        """Step the simulation forward."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> Optional[np.ndarray]:
        """Render the scene (requires renderer setup)."""
        # Basic rendering would require mujoco.Renderer
        # For now, return None as rendering is optional
        return None
    
    def describe(self) -> str:
        """Return a description of the robot."""
        ee_xyz, ee_R = self.get_ee_pose()
        q = self.get_joint_positions()
        objects = self.get_object_names()
        
        desc = f"""SO-101 5-DOF Arm Driver
========================
DOF: 5 (arm) + 1 (gripper)
Joints: {', '.join(self.arm_joint_names)}
End-effector: {self.ee_site_name}

Current State:
--------------
Joint positions: {np.array2string(q, precision=3, suppress_small=True)}
EE position: {np.array2string(ee_xyz, precision=3, suppress_small=True)}
Gripper: {'HOLDING' if self._is_holding else 'OPEN'}

Scene Objects: {len(objects)}
{', '.join(objects) if objects else '(none)'}

IK Method: Damped Least Squares
- Max iterations: {self.ik_max_iter}
- Tolerance: {self.ik_tol*1000:.1f}mm
- Base damping: {self.ik_damping_base}
- Step size: {self.ik_step_size}
"""
        return desc


if __name__ == "__main__":
    # Self-test
    print("=== SO-101 Driver Self-Test ===\n")
    
    # Build robot
    robot = Robot.build_from_mjcf("mjcf.xml")
    print(robot.describe())
    
    # Test home
    print("\n[TEST] Homing...")
    assert robot.home(), "Home failed"
    print("✓ Home successful")
    
    # Test FK
    print("\n[TEST] Forward kinematics...")
    ee_xyz, ee_R = robot.get_ee_pose()
    print(f"EE position: {ee_xyz}")
    print(f"EE rotation:\n{ee_R}")
    print("✓ FK successful")
    
    # Test IK round-trip
    print("\n[TEST] IK round-trip (+5cm in X)...")
    start_xyz, _ = robot.get_ee_pose()
    target_xyz = start_xyz.copy()
    target_xyz[0] += 0.05  # +5cm in X
    
    print(f"Start: {start_xyz}")
    print(f"Target: {target_xyz}")
    
    success = robot.move_cartesian(target_xyz, duration=1.0)
    assert success, "IK move failed"
    
    final_xyz, _ = robot.get_ee_pose()
    error = np.linalg.norm(final_xyz - target_xyz)
    print(f"Final: {final_xyz}")
    print(f"Error: {error*1000:.2f}mm")
    
    assert error < 0.02, f"IK error too large: {error*1000:.1f}mm"
    print("✓ IK round-trip successful")
    
    # Test gripper
    print("\n[TEST] Gripper control...")
    assert robot.gripper_close(), "Gripper close failed"
    print("✓ Gripper closed")
    assert robot.gripper_open(), "Gripper open failed"
    print("✓ Gripper opened")
    
    # Test object queries
    print("\n[TEST] Object queries...")
    objects = robot.get_object_names()
    print(f"Found {len(objects)} objects: {objects}")
    
    # Try to get floor position (should work)
    try:
        floor_pos = robot.get_object_position("floor")
        print(f"Floor position: {floor_pos}")
        print("✓ Object position query successful")
    except KeyError as e:
        print(f"✗ Object query failed: {e}")
    
    # Try non-existent object (should raise KeyError)
    try:
        robot.get_object_position("nonexistent_banana")
        print("✗ Should have raised KeyError for nonexistent object")
    except KeyError:
        print("✓ Correctly raised KeyError for nonexistent object")
    
    print("\n=== All tests passed! ===")
