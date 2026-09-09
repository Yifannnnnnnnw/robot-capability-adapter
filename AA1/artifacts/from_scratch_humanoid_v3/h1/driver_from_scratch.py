"""
H1 Humanoid Robot Driver - From Scratch Implementation
=======================================================

This driver implements balance control for the H1 humanoid robot using
joint-space PD control with active COM-based balance stabilization.

The H1 has 19 actuated DOF:
- 10 leg joints (5 per leg: hip yaw/roll/pitch, knee, ankle)
- 1 torso joint
- 8 arm joints (4 per arm: shoulder pitch/roll/yaw, elbow)

The robot has a free-floating base (pelvis) with 6 DOF.

Control Strategy:
-----------------
- Joint-space PD control: τ = Kp(q_des - q) + Kd(qd_des - qd)
- Active COM stabilization: adjust ankle torques to keep COM over support polygon
- High gains on leg joints for stiffness
- Damping to prevent oscillations

Humanoid Balance:
-----------------
Bipedal humanoid balance requires active control to keep the center of mass
over the support polygon (feet). This implementation uses ankle strategy:
adjusting ankle torques based on COM position and velocity to maintain balance.
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import os


class Robot:
    """H1 Humanoid Robot with balance control."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data
        self.viewer = None
        
        # Joint configuration
        self.n_joints = 19  # Actuated joints
        self.n_qpos = 26  # 7 (free joint) + 19 (actuated)
        self.n_qvel = 25  # 6 (free joint) + 19 (actuated)
        
        # Get home pose from keyframe
        if self.model.nkey > 0:
            self.home_qpos = self.model.key_qpos[0].copy()
        else:
            # Fallback home pose if no keyframe
            self.home_qpos = np.zeros(self.n_qpos)
            self.home_qpos[2] = 0.98  # Pelvis height
            self.home_qpos[3] = 1.0   # Quaternion w
            # Legs in slight crouch
            self.home_qpos[9] = -0.4   # left hip pitch
            self.home_qpos[10] = 0.8   # left knee
            self.home_qpos[11] = -0.4  # left ankle
            self.home_qpos[14] = -0.4  # right hip pitch
            self.home_qpos[15] = 0.8   # right knee
            self.home_qpos[16] = -0.4  # right ankle
        
        # PD gains for balance control
        # High gains for leg stiffness, moderate for arms
        self.kp = np.array([
            # Left leg - high gains for stability
            3000, 3000, 5000, 8000, 800,  # hip_yaw, hip_roll, hip_pitch, knee, ankle
            # Right leg
            3000, 3000, 5000, 8000, 800,
            # Torso - keep upright
            3000,
            # Left arm - moderate gains
            400, 400, 200, 150,  # shoulder_pitch, shoulder_roll, shoulder_yaw, elbow
            # Right arm
            400, 400, 200, 150,
        ])
        
        # Damping - critical damping
        self.kd = 2.0 * np.sqrt(self.kp)
        
        # Control ranges for clamping
        self.ctrl_ranges = np.array([
            [-200, 200], [-200, 200], [-200, 200], [-300, 300], [-40, 40],  # left leg
            [-200, 200], [-200, 200], [-200, 200], [-300, 300], [-40, 40],  # right leg
            [-200, 200],  # torso
            [-40, 40], [-40, 40], [-18, 18], [-18, 18],  # left arm
            [-40, 40], [-40, 40], [-18, 18], [-18, 18],  # right arm
        ])
        
        # Joint limits
        self.joint_limits = np.array([
            [-0.43, 0.43], [-0.43, 0.43], [-1.57, 1.57], [-0.26, 2.05], [-0.87, 0.52],  # left leg
            [-0.43, 0.43], [-0.43, 0.43], [-1.57, 1.57], [-0.26, 2.05], [-0.87, 0.52],  # right leg
            [-2.35, 2.35],  # torso
            [-2.87, 2.87], [-0.34, 3.11], [-1.30, 4.45], [-1.25, 2.61],  # left arm
            [-2.87, 2.87], [-3.11, 0.34], [-4.45, 1.30], [-1.25, 2.61],  # right arm
        ])
        
        # Body IDs
        self.pelvis_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.torso_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        
        # Balance control gains
        self.com_kp = 800.0  # Proportional gain for COM stabilization
        self.com_kd = 200.0  # Derivative gain for COM stabilization
        self.tilt_kp = 1000.0  # Gain for tilt correction
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file."""
        # Handle symlinks and relative paths
        mjcf_path = os.path.realpath(mjcf_path)
        
        # Change to the directory containing the MJCF for relative includes
        original_dir = os.getcwd()
        mjcf_dir = os.path.dirname(mjcf_path)
        if mjcf_dir:
            os.chdir(mjcf_dir)
        
        try:
            model = mujoco.MjModel.from_xml_path(os.path.basename(mjcf_path))
            data = mujoco.MjData(model)
            return cls(model, data)
        finally:
            os.chdir(original_dir)
    
    def home(self) -> bool:
        """Reset to home pose."""
        try:
            self.data.qpos[:] = self.home_qpos
            self.data.qvel[:] = 0.0
            self.data.ctrl[:] = 0.0
            mujoco.mj_forward(self.model, self.data)
            return True
        except Exception as e:
            print(f"Error in home(): {e}")
            return False
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions (19 actuated joints, excluding free joint)."""
        return self.data.qpos[7:].copy()
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get pelvis (base) pose in world frame.
        
        Returns:
            xyz: (3,) position
            R: (3, 3) rotation matrix
        """
        xyz = self.data.qpos[0:3].copy()
        quat = self.data.qpos[3:7].copy()  # w, x, y, z
        
        # Convert quaternion to rotation matrix
        R = np.zeros(9)
        mujoco.mju_quat2Mat(R, quat)
        R = R.reshape(3, 3)
        
        return xyz, R
    
    def get_torso_height(self) -> float:
        """Get pelvis height (Z coordinate)."""
        return float(self.data.qpos[2])
    
    def _get_com_state(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get center of mass position and velocity.
        
        Returns:
            com_pos: (3,) COM position in world frame
            com_vel: (3,) COM velocity in world frame
        """
        # Get total mass
        total_mass = np.sum(self.model.body_mass)
        
        # Compute COM position (weighted average of body positions)
        com_pos = np.zeros(3)
        for i in range(self.model.nbody):
            com_pos += self.model.body_mass[i] * self.data.xipos[i]
        com_pos /= total_mass
        
        # Compute COM velocity
        com_vel = np.zeros(3)
        for i in range(self.model.nbody):
            # Get body velocity (linear part of spatial velocity)
            body_vel = self.data.cvel[i, 3:6]  # Linear velocity
            com_vel += self.model.body_mass[i] * body_vel
        com_vel /= total_mass
        
        return com_pos, com_vel
    
    def _apply_pd_control(self, q_target: np.ndarray, qd_target: Optional[np.ndarray] = None,
                          add_balance: bool = True) -> None:
        """
        Apply PD control to track target joint positions with active balance.
        
        Args:
            q_target: Target joint positions (19,)
            qd_target: Target joint velocities (19,), defaults to zero
            add_balance: Whether to add active balance feedback
        """
        if qd_target is None:
            qd_target = np.zeros(self.n_joints)
        
        # Get current state (skip free joint)
        q_current = self.data.qpos[7:].copy()
        qd_current = self.data.qvel[6:].copy()
        
        # Base PD control
        tau = self.kp * (q_target - q_current) - self.kd * (qd_current - qd_target)
        
        # Active balance: adjust control based on COM and tilt
        if add_balance:
            # Get COM state
            com_pos, com_vel = self._get_com_state()
            
            # Get pelvis orientation
            pelvis_quat = self.data.qpos[3:7]
            R = np.zeros(9)
            mujoco.mju_quat2Mat(R, pelvis_quat)
            R = R.reshape(3, 3)
            
            # Compute tilt angles
            # Pitch: rotation around Y axis (forward/backward tilt)
            pitch = np.arctan2(-R[2, 0], R[2, 2])
            # Roll: rotation around X axis (sideways tilt)
            roll = np.arctan2(R[2, 1], R[2, 2])
            
            # Get foot center position (average of both feet)
            left_ankle_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "left_ankle_link")
            right_ankle_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "right_ankle_link")
            foot_center = 0.5 * (self.data.xpos[left_ankle_id] + self.data.xpos[right_ankle_id])
            
            # COM error relative to foot center
            com_error_x = com_pos[0] - foot_center[0]
            com_error_y = com_pos[1] - foot_center[1]
            
            # Ankle strategy: adjust ankle torques to keep COM over feet
            # If COM is forward of feet, push ankles forward (positive ankle torque)
            # This creates a restoring moment
            ankle_correction = self.com_kp * com_error_x + self.com_kd * com_vel[0]
            tau[4] += ankle_correction  # left ankle
            tau[9] += ankle_correction  # right ankle
            
            # Hip strategy: adjust hip pitch/roll for larger disturbances
            # If leaning forward (pitch > 0), push hips back (negative hip pitch)
            hip_pitch_correction = -self.tilt_kp * pitch
            tau[2] += hip_pitch_correction  # left hip pitch
            tau[7] += hip_pitch_correction  # right hip pitch
            
            # Roll correction via hip roll
            hip_roll_correction = -self.tilt_kp * 0.5 * roll
            tau[1] += hip_roll_correction  # left hip roll
            tau[6] += hip_roll_correction  # right hip roll
        
        # Clamp to control ranges
        tau = np.clip(tau, self.ctrl_ranges[:, 0], self.ctrl_ranges[:, 1])
        
        # Apply control
        self.data.ctrl[:] = tau
    
    def stand_balance(self, secs: float = 3.0) -> bool:
        """
        Maintain standing balance for specified duration.
        
        This is the core humanoid behavior. The robot uses PD control with
        active COM-based stabilization to maintain an upright posture.
        
        Args:
            secs: Duration to balance (seconds)
            
        Returns:
            True if successfully maintained balance
        """
        try:
            # Target is the home standing pose
            q_target = self.home_qpos[7:].copy()
            
            dt = self.model.opt.timestep
            n_steps = int(secs / dt)
            
            for step in range(n_steps):
                # Apply PD control with balance feedback
                self._apply_pd_control(q_target, add_balance=True)
                
                # Step simulation
                mujoco.mj_step(self.model, self.data)
            
            # Check final state
            final_height = self.get_torso_height()
            _, R = self.get_base_pose()
            z_up = R[2, 2]  # Z-axis should point up
            
            # Success if robot is still upright and at reasonable height
            success = final_height > 0.6 and z_up > 0.7
            
            if not success:
                print(f"stand_balance failed: h={final_height:.3f}m (need >0.6), z_up={z_up:.3f} (need >0.7)")
            
            return success
            
        except Exception as e:
            print(f"Error in stand_balance(): {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def squat(self, depth: float = 0.15, secs: float = 3.0) -> bool:
        """
        Perform a squat: lower torso, then return to standing.
        
        Args:
            depth: How much to lower the torso (meters)
            secs: Total duration for squat cycle
            
        Returns:
            True if successfully completed squat motion
        """
        try:
            # Get initial height
            initial_height = self.get_torso_height()
            
            # Create squat trajectory
            # Phase 1: Lower (40% of time)
            # Phase 2: Hold (20% of time)
            # Phase 3: Rise (40% of time)
            
            dt = self.model.opt.timestep
            n_steps = int(secs / dt)
            
            phase1_steps = int(n_steps * 0.4)
            phase2_steps = int(n_steps * 0.2)
            phase3_steps = n_steps - phase1_steps - phase2_steps
            
            # Nominal standing pose
            q_stand = self.home_qpos[7:].copy()
            
            # Squat pose: bend knees and hips more
            q_squat = q_stand.copy()
            # Increase knee bend
            q_squat[3] += 0.6  # left knee
            q_squat[8] += 0.6  # right knee
            # Adjust hip pitch
            q_squat[2] -= 0.3  # left hip pitch
            q_squat[7] -= 0.3  # right hip pitch
            # Adjust ankle
            q_squat[4] -= 0.3  # left ankle
            q_squat[9] -= 0.3  # right ankle
            
            # Clamp to joint limits
            q_squat = np.clip(q_squat, self.joint_limits[:, 0], self.joint_limits[:, 1])
            
            min_height = initial_height
            
            # Phase 1: Lower
            for i in range(phase1_steps):
                alpha = i / max(phase1_steps - 1, 1)
                q_target = (1 - alpha) * q_stand + alpha * q_squat
                
                self._apply_pd_control(q_target, add_balance=True)
                mujoco.mj_step(self.model, self.data)
                
                h = self.get_torso_height()
                min_height = min(min_height, h)
            
            # Phase 2: Hold
            for i in range(phase2_steps):
                self._apply_pd_control(q_squat, add_balance=True)
                mujoco.mj_step(self.model, self.data)
                
                h = self.get_torso_height()
                min_height = min(min_height, h)
            
            # Phase 3: Rise
            for i in range(phase3_steps):
                alpha = i / max(phase3_steps - 1, 1)
                q_target = (1 - alpha) * q_squat + alpha * q_stand
                
                self._apply_pd_control(q_target, add_balance=True)
                mujoco.mj_step(self.model, self.data)
            
            # Check success
            final_height = self.get_torso_height()
            _, R = self.get_base_pose()
            z_up = R[2, 2]
            
            # Success criteria:
            # 1. Robot lowered during squat (min_height < initial - 0.05)
            # 2. Robot recovered to standing height (final > 0.6)
            # 3. Robot stayed upright (z_up > 0.7)
            height_drop = initial_height - min_height
            success = (height_drop >= 0.05 and 
                      final_height > 0.6 and 
                      z_up > 0.7)
            
            if not success:
                print(f"squat failed: drop={height_drop:.3f}m (need >=0.05), "
                      f"final_h={final_height:.3f}m (need >0.6), z_up={z_up:.3f} (need >0.7)")
            
            return success
            
        except Exception as e:
            print(f"Error in squat(): {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step(self, n: int = 1) -> None:
        """Step the simulation forward."""
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """Render the current state (requires display)."""
        if self.viewer is None:
            try:
                self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
            except Exception as e:
                print(f"Could not create viewer: {e}")
                return
        
        if self.viewer is not None:
            self.viewer.sync()
    
    def describe(self) -> str:
        """Return a description of the robot."""
        pelvis_height = self.get_torso_height()
        xyz, R = self.get_base_pose()
        
        # Get tilt angle
        z_axis = R[:, 2]
        tilt = np.arccos(np.clip(z_axis[2], -1, 1))
        
        desc = f"""H1 Humanoid Robot
==================
DOF: {self.n_joints} actuated joints + 6 DOF free base = {self.model.nv} total
Actuators: {self.model.nu} (torque control)

Current State:
--------------
Pelvis position: [{xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}] m
Pelvis height: {pelvis_height:.3f} m
Tilt from vertical: {np.degrees(tilt):.1f} degrees

Joint positions (19):
{self.get_joint_positions()}

Capabilities:
-------------
- stand_balance(secs=3.0): Maintain upright standing pose
- squat(depth=0.15, secs=3.0): Lower and raise torso
- get_base_pose(): Get pelvis position and orientation
- get_torso_height(): Get pelvis height

Control Strategy:
-----------------
Joint-space PD control with active COM-based balance stabilization.
- Ankle strategy: adjust ankle torques based on COM position
- Hip strategy: adjust hip pitch/roll based on body tilt
- High gains on leg joints for stiffness
"""
        return desc


# Convenience function for testing
def main():
    """Test the robot driver."""
    import sys
    
    if len(sys.argv) > 1:
        mjcf_path = sys.argv[1]
    else:
        mjcf_path = "mjcf.xml"
    
    print(f"Loading robot from {mjcf_path}...")
    robot = Robot.build_from_mjcf(mjcf_path)
    
    print("\nResetting to home pose...")
    robot.home()
    
    print("\n" + robot.describe())
    
    print("\nTesting stand_balance for 3 seconds...")
    success = robot.stand_balance(secs=3.0)
    print(f"Stand balance: {'✓ SUCCESS' if success else '✗ FAILED'}")
    print(f"Final height: {robot.get_torso_height():.3f}m")
    _, R = robot.get_base_pose()
    print(f"Final z_up: {R[2,2]:.3f}")
    
    print("\nResetting and testing squat...")
    robot.home()
    success = robot.squat(depth=0.12, secs=3.0)
    print(f"Squat: {'✓ SUCCESS' if success else '✗ FAILED'}")
    print(f"Final height: {robot.get_torso_height():.3f}m")
    _, R = robot.get_base_pose()
    print(f"Final z_up: {R[2,2]:.3f}")


if __name__ == "__main__":
    main()
