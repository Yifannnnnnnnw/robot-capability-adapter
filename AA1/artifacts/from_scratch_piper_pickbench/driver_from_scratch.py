#!/usr/bin/env python3
"""
Driver for Piper 6-DOF arm with parallel-jaw gripper and weld-based grasping.

This driver implements:
- Position-only Damped Least Squares IK for Cartesian motion
- Weld-based grasping with proximity detection
- Position control via MuJoCo actuators
- Robust error handling and joint limit enforcement

Author: Auto-generated driver (from_scratch)
Robot: Piper 6-DOF arm (from_scratch_piper_pickbench)
"""

import os
import numpy as np
import mujoco


class Robot:
    """Piper 6-DOF arm driver with IK, FK, and weld-based grasping."""
    
    # Joint limits (from study.json)
    JOINT_LIMITS = np.array([
        [-2.618, 2.618],   # joint1
        [0.0, 3.14],       # joint2
        [-2.697, 0.0],     # joint3
        [-1.832, 1.832],   # joint4
        [-1.22, 1.22],     # joint5
        [-3.14, 3.14]      # joint6
    ])
    
    # Home configuration
    HOME_QPOS = np.array([0, 1.57, -1.3485, 0, 0, 0])
    
    # Gripper control
    GRIPPER_OPEN = 0.035
    GRIPPER_CLOSED = 0.0
    GRASP_THRESHOLD = 0.02  # Distance threshold for grasping (2cm)
    
    # IK parameters
    IK_MAX_ITER = 150
    IK_TOL = 0.001  # 1mm tolerance
    IK_DAMPING_MIN = 0.001
    IK_DAMPING_MAX = 0.1
    IK_MAX_STEP = 0.2  # radians
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize robot with MuJoCo model and data."""
        self.model = model
        self.data = data
        
        # Resolve indices
        self.ee_site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        
        # Arm actuator indices
        self.arm_actuator_ids = [
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"joint{i}") 
            for i in range(1, 7)
        ]
        
        # Gripper actuator
        self.gripper_actuator_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, "gripper"
        )
        
        # Body IDs for grasping
        self.link6_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "link6")
        self.graspable_bodies = {
            "cube_red": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "cube_red"),
            "cube_green": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "cube_green"),
            "cube_blue": mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "cube_blue"),
        }
        
        # Find weld constraint indices
        self.weld_constraints = {}
        for i in range(model.neq):
            if model.eq_type[i] == mujoco.mjtEq.mjEQ_WELD:
                body1_id = model.eq_obj1id[i]
                body2_id = model.eq_obj2id[i]
                for name, body_id in self.graspable_bodies.items():
                    if body2_id == body_id and body1_id == self.link6_body_id:
                        self.weld_constraints[name] = i
                        break
        
        # State tracking
        self.grasped_object = None
        self.gripper_state = "open"
        
        print(f"Robot initialized: {len(self.arm_actuator_ids)} arm DOF, "
              f"{len(self.weld_constraints)} graspable objects")
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        # Handle symlinks and relative includes
        mjcf_path = os.path.realpath(mjcf_path)
        mjcf_dir = os.path.dirname(mjcf_path)
        orig_dir = os.getcwd()
        
        try:
            os.chdir(mjcf_dir)
            model = mujoco.MjModel.from_xml_path(os.path.basename(mjcf_path))
            data = mujoco.MjData(model)
        finally:
            os.chdir(orig_dir)
        
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move robot to home configuration.
        
        Returns:
            True on success
        """
        # Set arm joints to home
        self.data.qpos[:6] = self.HOME_QPOS.copy()
        
        # Set actuator targets
        for i, act_id in enumerate(self.arm_actuator_ids):
            self.data.ctrl[act_id] = self.HOME_QPOS[i]
        
        # Open gripper
        self.data.ctrl[self.gripper_actuator_id] = self.GRIPPER_OPEN
        
        # Step physics to settle
        for _ in range(100):
            mujoco.mj_step(self.model, self.data)
        
        self.gripper_state = "open"
        return True
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get current arm joint positions.
        
        Returns:
            Array of 6 joint angles in radians
        """
        return self.data.qpos[:6].copy()
    
    def get_ee_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Get end-effector pose.
        
        Returns:
            (position, rotation_matrix) where position is (3,) and rotation is (3, 3)
        """
        mujoco.mj_forward(self.model, self.data)
        pos = self.data.site_xpos[self.ee_site_id].copy()
        rot = self.data.site_xmat[self.ee_site_id].copy().reshape(3, 3)
        return pos, rot
    
    def _clamp_to_limits(self, q: np.ndarray) -> np.ndarray:
        """Clamp joint configuration to limits."""
        return np.clip(q, self.JOINT_LIMITS[:, 0], self.JOINT_LIMITS[:, 1])
    
    def _compute_position_jacobian(self) -> np.ndarray:
        """
        Compute 3x6 position Jacobian for EE site.
        
        Returns:
            3x6 Jacobian matrix
        """
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jacp, jacr, self.ee_site_id)
        return jacp[:, :6]
    
    def _position_ik(self, target_pos: np.ndarray, q_init: np.ndarray = None,
                     verbose: bool = False) -> tuple[np.ndarray, bool]:
        """
        Position-only inverse kinematics using Damped Least Squares.
        
        This IK solver optimizes only for position, leaving orientation free.
        This is appropriate for pick-and-place tasks where precise orientation
        is less critical than reaching the target position.
        
        Algorithm: Iterative DLS with adaptive damping
        - Damping increases with error magnitude (0.001 to 0.1)
        - Step size adapts to distance (up to 0.2 rad)
        - Joint limits enforced via clamping
        
        Args:
            target_pos: Desired EE position (3,)
            q_init: Initial joint configuration (6,), defaults to current
            verbose: Print iteration details
            
        Returns:
            (q_solution, success) where success is True if converged within tolerance
        """
        if q_init is None:
            q_init = self.get_joint_positions()
        
        q = q_init.copy()
        
        for iteration in range(self.IK_MAX_ITER):
            # Forward kinematics
            self.data.qpos[:6] = q
            mujoco.mj_forward(self.model, self.data)
            pos = self.data.site_xpos[self.ee_site_id].copy()
            
            # Position error
            pos_error = target_pos - pos
            error_norm = np.linalg.norm(pos_error)
            
            if verbose and iteration % 20 == 0:
                print(f"  IK iter {iteration}: error={error_norm*1000:.2f} mm")
            
            # Check convergence
            if error_norm < self.IK_TOL:
                if verbose:
                    print(f"  IK converged in {iteration} iterations")
                return q, True
            
            # Compute Jacobian
            Jp = self._compute_position_jacobian()
            
            # Adaptive damping based on error magnitude
            lambda_factor = min(1.0, error_norm / 0.05)
            damping = (self.IK_DAMPING_MIN + 
                      (self.IK_DAMPING_MAX - self.IK_DAMPING_MIN) * lambda_factor)
            
            # Damped least squares: dq = J^T (J J^T + λI)^-1 error
            JJt = Jp @ Jp.T
            dq = Jp.T @ np.linalg.solve(JJt + damping * np.eye(3), pos_error)
            
            # Adaptive step limiting
            max_step = min(self.IK_MAX_STEP, error_norm * 2.0)
            dq_norm = np.linalg.norm(dq)
            if dq_norm > max_step:
                dq = dq * max_step / dq_norm
            
            # Update and clamp
            q = q + dq
            q = self._clamp_to_limits(q)
        
        # Failed to converge
        if verbose:
            print(f"  IK failed to converge. Final error: {error_norm*1000:.2f} mm")
        return q, False
    
    def move_cartesian(self, target_xyz: np.ndarray, duration: float = 2.0) -> bool:
        """
        Move end-effector to target Cartesian position.
        
        Uses position-only IK and interpolates in joint space over the duration.
        
        Args:
            target_xyz: Target position (3,)
            duration: Motion duration in seconds
            
        Returns:
            True if target was reached successfully
        """
        # Solve IK
        q_start = self.get_joint_positions()
        q_target, success = self._position_ik(target_xyz, q_start, verbose=False)
        
        if not success:
            print(f"Warning: IK did not fully converge, moving to best solution")
        
        # Interpolate in joint space
        steps = int(duration / self.model.opt.timestep)
        for i in range(steps):
            alpha = (i + 1) / steps
            q_interp = q_start + alpha * (q_target - q_start)
            
            # Set joint targets
            self.data.qpos[:6] = q_interp
            for j, act_id in enumerate(self.arm_actuator_ids):
                self.data.ctrl[act_id] = q_interp[j]
            
            mujoco.mj_step(self.model, self.data)
        
        # Verify final position
        final_pos, _ = self.get_ee_pose()
        error = np.linalg.norm(final_pos - target_xyz)
        
        if error > 0.02:  # 2cm threshold
            print(f"Warning: Final position error {error*100:.2f} cm")
            return False
        
        return True
    
    def gripper_open(self) -> bool:
        """
        Open the gripper and release any grasped object.
        
        Returns:
            True on success
        """
        # Release weld if holding something
        if self.grasped_object is not None:
            weld_id = self.weld_constraints[self.grasped_object]
            self.data.eq_active[weld_id] = 0
            self.grasped_object = None
        
        # Command gripper open
        self.data.ctrl[self.gripper_actuator_id] = self.GRIPPER_OPEN
        
        # Step to execute
        for _ in range(50):
            mujoco.mj_step(self.model, self.data)
        
        self.gripper_state = "open"
        return True
    
    def gripper_close(self) -> bool:
        """
        Close the gripper and attempt to grasp nearby objects.
        
        Uses weld constraints to rigidly attach objects within threshold distance.
        
        Returns:
            True if an object was grasped, False otherwise
        """
        # Command gripper close
        self.data.ctrl[self.gripper_actuator_id] = self.GRIPPER_CLOSED
        
        # Step to execute
        for _ in range(50):
            mujoco.mj_step(self.model, self.data)
        
        self.gripper_state = "closed"
        
        # Check for graspable objects in range
        ee_pos = self.data.site_xpos[self.ee_site_id].copy()
        
        for obj_name, body_id in self.graspable_bodies.items():
            obj_pos = self.data.xpos[body_id].copy()
            distance = np.linalg.norm(ee_pos - obj_pos)
            
            if distance < self.GRASP_THRESHOLD:
                # Activate weld constraint. (Patched post-synthesis: also
                # update eq_data to the CURRENT relative pose so the weld
                # holds the object at where it is now, not at where it was
                # at MJCF load time.)
                weld_id = self.weld_constraints[obj_name]
                link6_id = self.model.eq_obj1id[weld_id]
                link6_pos = self.data.xpos[link6_id].copy()
                link6_quat = self.data.xquat[link6_id].copy()
                inv_q = np.array([link6_quat[0], -link6_quat[1],
                                  -link6_quat[2], -link6_quat[3]])
                delta_w = obj_pos - link6_pos
                relpose = np.zeros(3)
                mujoco.mju_rotVecQuat(relpose, delta_w, inv_q)
                obj_quat = self.data.xquat[body_id].copy()
                relquat = np.zeros(4)
                mujoco.mju_mulQuat(relquat, inv_q, obj_quat)
                self.model.eq_data[weld_id, 0:3] = 0.0
                self.model.eq_data[weld_id, 3:6] = relpose
                self.model.eq_data[weld_id, 6:10] = relquat
                self.model.eq_data[weld_id, 10] = 1.0
                self.data.eq_active[weld_id] = 1
                self.grasped_object = obj_name
                print(f"Grasped {obj_name} (distance: {distance*100:.2f} cm)")
                return True
        
        print("No object in grasp range")
        return False
    
    def is_holding(self) -> bool:
        """
        Check if gripper is holding an object.
        
        Returns:
            True if holding an object
        """
        return self.grasped_object is not None

    def get_object_position(self, body_name: str) -> np.ndarray:
        """Return world-frame XYZ position of any named body in the scene.

        Added post-synthesis (the synthesis pipeline currently lacks an
        explicit observation-tool requirement; the agent inferred grasp
        logic but did not write get_object_position itself).
        """
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if bid < 0:
            raise ValueError(f"unknown body {body_name!r}")
        mujoco.mj_forward(self.model, self.data)
        return self.data.xpos[bid].copy()

    def step(self, n: int = 1):
        """
        Step the physics simulation.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> np.ndarray:
        """
        Render camera view.
        
        Returns:
            RGB image array
        """
        # Use default camera
        renderer = mujoco.Renderer(self.model, height=480, width=640)
        renderer.update_scene(self.data)
        return renderer.render()
    
    def describe(self) -> str:
        """
        Return a human-readable description of the robot state.
        
        Returns:
            Multi-line string describing robot state
        """
        joint_pos = self.get_joint_positions()
        ee_pos, ee_rot = self.get_ee_pose()
        
        desc = []
        desc.append("=== Piper 6-DOF Arm ===")
        desc.append(f"Joint positions (rad): {np.array2string(joint_pos, precision=3)}")
        desc.append(f"EE position (m): {np.array2string(ee_pos, precision=3)}")
        desc.append(f"Gripper: {self.gripper_state}")
        desc.append(f"Holding: {self.grasped_object if self.grasped_object else 'nothing'}")
        desc.append(f"Time: {self.data.time:.2f} s")
        
        return "\n".join(desc)


# Self-test when run as main
if __name__ == "__main__":
    print("Testing driver_from_scratch.py...")
    
    robot = Robot.build_from_mjcf("mjcf.xml")
    print("\n1. Home position")
    robot.home()
    print(robot.describe())
    
    print("\n2. Get EE pose")
    pos, rot = robot.get_ee_pose()
    print(f"Position: {pos}")
    print(f"Rotation:\n{rot}")
    
    print("\n3. Move +5cm in X")
    target = pos + np.array([0.05, 0, 0])
    success = robot.move_cartesian(target, duration=1.0)
    print(f"Success: {success}")
    new_pos, _ = robot.get_ee_pose()
    print(f"New position: {new_pos}")
    print(f"Error: {np.linalg.norm(new_pos - target)*100:.2f} cm")
    
    print("\n4. Test gripper")
    robot.gripper_close()
    print(f"Holding: {robot.is_holding()}")
    robot.gripper_open()
    print(f"Holding after open: {robot.is_holding()}")
    
    print("\n5. Final state")
    print(robot.describe())
    
    print("\nDriver test complete!")
