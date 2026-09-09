"""
ANYmal-C Quadruped Driver - From Scratch Implementation
========================================================

This driver implements a complete control system for the ANYmal-C quadruped robot
without using any auto_adapter.skeletons imports. It provides:

- Position-controlled joint actuation
- Standing, sitting, and walking behaviors
- PD control for smooth motion transitions
- Trot gait for forward locomotion

The robot has 12 DOF (4 legs × 3 joints):
- HAA: Hip Abduction/Adduction
- HFE: Hip Flexion/Extension  
- KFE: Knee Flexion/Extension

Joint naming: {LF,RF,LH,RH}_{HAA,HFE,KFE}
- LF: Left Front, RF: Right Front
- LH: Left Hind, RH: Right Hind
"""

import mujoco
import numpy as np
from typing import Tuple, Optional
import os


class Robot:
    """ANYmal-C quadruped robot driver with position control."""
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """Initialize robot with MuJoCo model and data.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        self.viewer = None
        
        # Joint configuration
        self.joint_names = [
            'LF_HAA', 'LF_HFE', 'LF_KFE',  # Left Front
            'RF_HAA', 'RF_HFE', 'RF_KFE',  # Right Front
            'LH_HAA', 'LH_HFE', 'LH_KFE',  # Left Hind
            'RH_HAA', 'RH_HFE', 'RH_KFE'   # Right Hind
        ]
        
        # Get joint indices
        self.joint_ids = []
        self.joint_qpos_addrs = []
        self.joint_qvel_addrs = []
        
        for name in self.joint_names:
            jnt_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            self.joint_ids.append(jnt_id)
            self.joint_qpos_addrs.append(self.model.jnt_qposadr[jnt_id])
            self.joint_qvel_addrs.append(self.model.jnt_dofadr[jnt_id])
        
        # Get actuator indices (should match joint order)
        self.actuator_ids = []
        for name in self.joint_names:
            act_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            self.actuator_ids.append(act_id)
        
        # Get base body ID
        self.base_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, 'base')
        
        # Joint limits (from study.json, but we'll read from model)
        self.joint_limits = np.zeros((len(self.joint_names), 2))
        for i, jnt_id in enumerate(self.joint_ids):
            qpos_adr = self.joint_qpos_addrs[i]
            self.joint_limits[i, 0] = self.model.jnt_range[jnt_id, 0]
            self.joint_limits[i, 1] = self.model.jnt_range[jnt_id, 1]
        
        # Predefined poses
        self.home_pose = np.array([
            0.0, 0.4, -0.8,   # LF
            0.0, 0.4, -0.8,   # RF
            0.0, -0.4, 0.8,   # LH
            0.0, -0.4, 0.8    # RH
        ])
        
        self.stand_pose = self.home_pose.copy()
        
        self.sit_pose = np.array([
            0.0, 1.2, -2.0,   # LF - fold legs
            0.0, 1.2, -2.0,   # RF
            0.0, -1.2, 2.0,   # LH
            0.0, -1.2, 2.0    # RH
        ])
        
        # PD gains for position control
        self.kp = 100.0  # Position gain
        self.kd = 10.0   # Velocity gain
    
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> 'Robot':
        """Build robot from MJCF file.
        
        Args:
            mjcf_path: Path to MJCF XML file
            
        Returns:
            Robot instance
        """
        # Handle symlinks by loading from the actual file location
        if os.path.islink(mjcf_path):
            mjcf_path = os.readlink(mjcf_path)
        
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def get_joint_positions(self) -> np.ndarray:
        """Get current joint positions.
        
        Returns:
            Array of 12 joint positions in order: LF, RF, LH, RH (HAA, HFE, KFE each)
        """
        positions = np.zeros(len(self.joint_names))
        for i, addr in enumerate(self.joint_qpos_addrs):
            positions[i] = self.data.qpos[addr]
        return positions
    
    def get_joint_velocities(self) -> np.ndarray:
        """Get current joint velocities.
        
        Returns:
            Array of 12 joint velocities
        """
        velocities = np.zeros(len(self.joint_names))
        for i, addr in enumerate(self.joint_qvel_addrs):
            velocities[i] = self.data.qvel[addr]
        return velocities
    
    def set_joint_targets(self, targets: np.ndarray):
        """Set target joint positions for position-controlled actuators.
        
        Args:
            targets: Array of 12 target joint positions
        """
        # Clamp to joint limits
        targets = np.clip(targets, self.joint_limits[:, 0], self.joint_limits[:, 1])
        
        # Set control signals (position targets for position actuators)
        for i, act_id in enumerate(self.actuator_ids):
            self.data.ctrl[act_id] = targets[i]
    
    def step(self, n: int = 1):
        """Step the simulation forward.
        
        Args:
            n: Number of steps to take
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def home(self) -> bool:
        """Move robot to home/standing pose.
        
        Returns:
            True if successful
        """
        return self._move_to_pose(self.home_pose, duration=2.0)
    
    def _move_to_pose(self, target_pose: np.ndarray, duration: float = 2.0) -> bool:
        """Move robot to target pose using smooth interpolation.
        
        Args:
            target_pose: Target joint positions (12,)
            duration: Time to reach target in seconds
            
        Returns:
            True if successful
        """
        start_pose = self.get_joint_positions()
        dt = self.model.opt.timestep
        steps = int(duration / dt)
        
        for i in range(steps):
            # Linear interpolation
            alpha = (i + 1) / steps
            current_target = start_pose + alpha * (target_pose - start_pose)
            
            self.set_joint_targets(current_target)
            self.step()
        
        # Check if we reached the target (within tolerance)
        final_pose = self.get_joint_positions()
        error = np.linalg.norm(final_pose - target_pose)
        
        # More lenient tolerance for position-controlled actuators
        return error < 0.5  # 0.5 rad tolerance
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """Stand up to a stable standing pose.
        
        The robot moves to a standing configuration with legs extended to support
        the body at a height > 0.15 m.
        
        Args:
            duration: Time to reach standing pose in seconds
            
        Returns:
            True if successful (body height > 0.15 m)
        """
        success = self._move_to_pose(self.stand_pose, duration=duration)
        
        # Verify standing height
        height = self.get_body_height()
        
        return success and height > 0.15
    
    def sit(self, duration: float = 1.5) -> bool:
        """Sit down by folding legs.
        
        The robot folds its legs to lower the body. The final height must be
        less than 0.8x the standing height to pass validation.
        
        Args:
            duration: Time to reach sitting pose in seconds
            
        Returns:
            True if successful (body lowered significantly)
        """
        # Get standing height for comparison
        stand_height = 0.45  # Approximate standing height
        
        success = self._move_to_pose(self.sit_pose, duration=duration)
        
        # Verify sitting (body should be lowered)
        final_height = self.get_body_height()
        
        return success and final_height < 0.8 * stand_height
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """Walk forward using a trot gait.
        
        The robot uses a trot gait where diagonal leg pairs (LF+RH, RF+LH) move
        together. The gait produces forward displacement in the +X direction.
        
        Gait mechanics:
        - Front legs: Small HFE = forward position, Large HFE = backward position
        - Rear legs: Less negative HFE = forward, More negative HFE = backward
        - For forward motion: Front legs swing backward, rear legs swing forward
        
        Args:
            secs: Duration to walk in seconds
            speed: Walking speed parameter (affects gait frequency)
            
        Returns:
            True if forward progress > 3 cm with small sideways drift
        """
        # Record starting position
        start_pos = self.get_base_pose()[0]
        
        # Gait parameters
        gait_freq = 2.0 * speed  # Hz
        dt = self.model.opt.timestep
        steps_per_cycle = int(1.0 / (gait_freq * dt))
        total_steps = int(secs / dt)
        
        # Swing amplitude
        swing_amp = 0.3
        
        for step in range(total_steps):
            phase = (step % steps_per_cycle) / steps_per_cycle
            
            if phase < 0.5:
                # LF+RH swing phase
                p = phase * 2.0
                # LF swings from forward (0.2) to backward (0.5)
                lf_hfe = 0.2 + swing_amp * np.sin(p * np.pi)
                # RH swings from backward (-0.7) to forward (-0.4)
                rh_hfe = -0.7 + swing_amp * np.sin(p * np.pi)
                
                # Stance legs (pushing)
                rf_hfe = 0.5   # RF in backward position
                lh_hfe = -0.7  # LH in backward position
            else:
                # RF+LH swing phase
                p = (phase - 0.5) * 2.0
                rf_hfe = 0.2 + swing_amp * np.sin(p * np.pi)
                lh_hfe = -0.7 + swing_amp * np.sin(p * np.pi)
                
                # Stance legs
                lf_hfe = 0.5
                rh_hfe = -0.7
            
            # Construct full gait pose
            gait_pose = np.array([
                0.0, lf_hfe, -0.8,   # LF
                0.0, rf_hfe, -0.8,   # RF
                0.0, lh_hfe, 0.8,    # LH
                0.0, rh_hfe, 0.8     # RH
            ])
            
            self.set_joint_targets(gait_pose)
            self.step()
        
        # Check forward progress
        end_pos = self.get_base_pose()[0]
        displacement = end_pos - start_pos
        
        forward_dist = displacement[0]  # X direction
        sideways_dist = abs(displacement[1])  # Y direction
        
        # Success criteria: >3 cm forward, <5 cm sideways drift
        return forward_dist > 0.03 and sideways_dist < 0.05
    
    def get_body_height(self) -> float:
        """Get the height of the robot base above ground.
        
        Returns:
            Height in meters (Z coordinate of base body)
        """
        return float(self.data.xpos[self.base_id][2])
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get the pose of the robot base.
        
        Returns:
            Tuple of (position, rotation_matrix)
            - position: (3,) array of XYZ coordinates
            - rotation_matrix: (3, 3) rotation matrix
        """
        position = self.data.xpos[self.base_id].copy()
        
        # Get rotation matrix from quaternion
        # The base has a freejoint, quat is at qpos[3:7]
        quat = self.data.qpos[3:7].copy()  # [w, x, y, z] in MuJoCo
        
        # Convert quaternion to rotation matrix
        rotation = np.zeros((3, 3))
        mujoco.mju_quat2Mat(rotation.ravel(), quat)
        
        return position, rotation
    
    def get_base_yaw(self) -> float:
        """Get the yaw angle of the robot base.
        
        Returns:
            Yaw angle in radians
        """
        _, R = self.get_base_pose()
        
        # Extract yaw from rotation matrix
        # yaw = atan2(R[1,0], R[0,0])
        yaw = np.arctan2(R[1, 0], R[0, 0])
        
        return float(yaw)
    
    def render(self):
        """Render the robot in a viewer window."""
        if self.viewer is None:
            self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        
        if self.viewer is not None:
            self.viewer.sync()
    
    def describe(self) -> str:
        """Get a description of the robot state.
        
        Returns:
            String description of robot configuration and state
        """
        joint_pos = self.get_joint_positions()
        base_pos, _ = self.get_base_pose()
        height = self.get_body_height()
        yaw = self.get_base_yaw()
        
        desc = "ANYmal-C Quadruped Robot\n"
        desc += "=" * 50 + "\n"
        desc += f"DOF: 12 (4 legs × 3 joints)\n"
        desc += f"Base position: [{base_pos[0]:.3f}, {base_pos[1]:.3f}, {base_pos[2]:.3f}]\n"
        desc += f"Base height: {height:.3f} m\n"
        desc += f"Base yaw: {np.degrees(yaw):.1f}°\n"
        desc += f"\nJoint positions (rad):\n"
        
        for i, name in enumerate(self.joint_names):
            desc += f"  {name:10s}: {joint_pos[i]:7.3f}\n"
        
        desc += f"\nCapabilities:\n"
        desc += f"  - stand_up(duration=2.0) -> bool\n"
        desc += f"  - sit(duration=1.5) -> bool\n"
        desc += f"  - walk_forward(secs=2.0, speed=0.2) -> bool\n"
        desc += f"  - get_body_height() -> float\n"
        desc += f"  - get_base_pose() -> (xyz, R3x3)\n"
        desc += f"  - get_base_yaw() -> float\n"
        
        return desc


# Convenience function for quick testing
def main():
    """Test the robot driver."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python driver_from_scratch.py <mjcf_path>")
        sys.exit(1)
    
    mjcf_path = sys.argv[1]
    
    print("Loading robot...")
    robot = Robot.build_from_mjcf(mjcf_path)
    
    print("\nMoving to home pose...")
    robot.home()
    
    print("\n" + robot.describe())
    
    print("\nTesting stand_up...")
    success = robot.stand_up()
    print(f"Stand up: {'SUCCESS' if success else 'FAILED'}")
    print(f"Height: {robot.get_body_height():.3f} m")
    
    print("\nTesting sit...")
    success = robot.sit()
    print(f"Sit: {'SUCCESS' if success else 'FAILED'}")
    print(f"Height: {robot.get_body_height():.3f} m")
    
    print("\nTesting walk_forward...")
    robot.stand_up()
    start_pos = robot.get_base_pose()[0]
    success = robot.walk_forward(secs=1.5)
    end_pos = robot.get_base_pose()[0]
    displacement = end_pos - start_pos
    print(f"Walk forward: {'SUCCESS' if success else 'FAILED'}")
    print(f"Displacement: X={displacement[0]:.3f} m, Y={displacement[1]:.3f} m")


if __name__ == "__main__":
    main()
