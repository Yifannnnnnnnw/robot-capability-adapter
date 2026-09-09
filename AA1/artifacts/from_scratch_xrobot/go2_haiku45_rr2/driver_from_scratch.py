"""
Unitree Go2 Quadruped Robot Driver
Implements stand_up(), sit(), walk_forward(), and get_body_height() / get_base_pose()
using joint-space PD control with a diagonal-pair gait.
"""

import mujoco
import numpy as np
from typing import Tuple


class Robot:
    """
    Unitree Go2 quadruped robot driver.
    
    This robot has 12 DOF (3 per leg: hip abduction, thigh, calf) and uses
    position-controlled motors. The base is a free-floating body.
    """
    
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        """
        Initialize the robot driver.
        
        Args:
            model: MuJoCo model
            data: MuJoCo data
        """
        self.model = model
        self.data = data
        
        # Build actuator to (qpos, qvel) mapping
        self.act_to_qpos = []
        for i in range(self.model.nu):
            jnt_id = self.model.actuator_trnid[i, 0]
            qpos_adr = self.model.jnt_qposadr[jnt_id]
            qvel_adr = self.model.jnt_dofadr[jnt_id]
            self.act_to_qpos.append((qpos_adr, qvel_adr))
        
        # Get body IDs
        self.base_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        self.foot_ids = {}
        for leg in ['FL', 'FR', 'RL', 'RR']:
            foot_name = f"{leg}_foot"
            self.foot_ids[leg] = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, foot_name)
        
        # Home pose from keyframe
        self.home_qpos = np.array(self.model.key_qpos[0])
        
        # PD control gains
        self.kp = 50.0
        self.kd = 2.0
        
        # Gait parameters
        self.gait_period = 0.8  # seconds
        self.swing_amp = 0.4  # radians
        
    @classmethod
    def build_from_mjcf(cls, mjcf_path: str) -> "Robot":
        """
        Build a robot from an MJCF file.
        
        Args:
            mjcf_path: Path to the MJCF file
            
        Returns:
            Robot instance
        """
        model = mujoco.MjModel.from_xml_path(mjcf_path)
        data = mujoco.MjData(model)
        return cls(model, data)
    
    def home(self) -> bool:
        """
        Move the robot to the home pose.
        
        Returns:
            True if successful
        """
        self.data.qpos[:] = self.home_qpos
        self.data.qvel[:] = 0
        self.data.ctrl[:] = 0
        mujoco.mj_forward(self.model, self.data)
        return True
    
    def get_joint_positions(self) -> np.ndarray:
        """
        Get the current joint positions (leg joints only, excluding base).
        
        Returns:
            Array of 12 joint positions
        """
        return self.data.qpos[7:19].copy()
    
    def step(self, n: int = 1) -> None:
        """
        Step the simulation forward.
        
        Args:
            n: Number of steps
        """
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
    
    def render(self) -> None:
        """
        Render the current state (placeholder).
        """
        pass
    
    def describe(self) -> str:
        """
        Describe the robot state.
        
        Returns:
            String description
        """
        base_pos = self.data.xpos[self.base_id]
        base_height = base_pos[2]
        joint_pos = self.get_joint_positions()
        
        return (
            f"Unitree Go2 Quadruped\n"
            f"  Base position: {base_pos}\n"
            f"  Base height: {base_height:.4f} m\n"
            f"  Joint positions: {joint_pos}\n"
        )
    
    def get_body_height(self) -> float:
        """
        Get the height of the base body.
        
        Returns:
            Height in meters
        """
        return self.data.xpos[self.base_id, 2]
    
    def get_base_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get the base pose (position and orientation).
        
        Returns:
            Tuple of (xyz position, 3x3 rotation matrix)
        """
        pos = self.data.xpos[self.base_id].copy()
        
        # Get quaternion from qpos (wxyz order)
        quat = self.data.qpos[3:7]
        
        # Convert quaternion to rotation matrix
        # MuJoCo uses wxyz order
        w, x, y, z = quat
        rot = np.array([
            [1 - 2*(y**2 + z**2), 2*(x*y - w*z), 2*(x*z + w*y)],
            [2*(x*y + w*z), 1 - 2*(x**2 + z**2), 2*(y*z - w*x)],
            [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x**2 + y**2)]
        ])
        
        return pos, rot
    
    def _apply_pd_control(self, q_target: np.ndarray) -> None:
        """
        Apply PD control to track target joint angles.
        
        Args:
            q_target: Target joint positions (19 DOF)
        """
        for i in range(12):
            qpos_idx, qvel_idx = self.act_to_qpos[i]
            q_des = q_target[qpos_idx]
            q_curr = self.data.qpos[qpos_idx]
            qd_curr = self.data.qvel[qvel_idx]
            
            tau = self.kp * (q_des - q_curr) + self.kd * (0 - qd_curr)
            self.data.ctrl[i] = np.clip(
                tau,
                self.model.actuator_ctrlrange[i, 0],
                self.model.actuator_ctrlrange[i, 1]
            )
    
    def stand_up(self, duration: float = 2.0) -> bool:
        """
        Stand up from a sitting position.
        
        Uses PD control to track the home pose. The robot should reach a stable
        standing position with body height > 0.15 m.
        
        Args:
            duration: Time to stand up (seconds)
            
        Returns:
            True if successful (body height > 0.15 m at end)
        """
        dt = self.model.opt.timestep
        steps = int(duration / dt)
        
        for step in range(steps):
            # Smoothly interpolate to home pose
            alpha = min(1.0, step / (steps * 0.8))
            q_target = self.data.qpos + alpha * (self.home_qpos - self.data.qpos)
            
            self._apply_pd_control(q_target)
            mujoco.mj_step(self.model, self.data)
        
        # Check if standing
        height = self.get_body_height()
        return height > 0.15
    
    def sit(self, duration: float = 1.5) -> bool:
        """
        Sit down by folding the legs.
        
        Uses PD control to increase hip and knee angles, lowering the body.
        The final body height should be < 0.8x the standing height.
        
        Args:
            duration: Time to sit (seconds)
            
        Returns:
            True if successful (body height drops by > 20%)
        """
        dt = self.model.opt.timestep
        steps = int(duration / dt)
        
        # Record initial height
        initial_height = self.get_body_height()
        
        # Create sitting pose: fold legs
        q_sit = self.home_qpos.copy()
        # Increase thigh angles (forward)
        q_sit[8] += 0.6   # FL_thigh
        q_sit[11] += 0.6  # FR_thigh
        q_sit[14] += 0.6  # RL_thigh
        q_sit[17] += 0.6  # RR_thigh
        # Decrease calf angles (more negative = more folded)
        q_sit[9] -= 0.6   # FL_calf
        q_sit[12] -= 0.6  # FR_calf
        q_sit[15] -= 0.6  # RL_calf
        q_sit[18] -= 0.6  # RR_calf
        
        # Clamp to joint limits
        for i in range(1, 13):
            if self.model.jnt_limited[i]:
                q_sit[self.model.jnt_qposadr[i]] = np.clip(
                    q_sit[self.model.jnt_qposadr[i]],
                    self.model.jnt_range[i, 0],
                    self.model.jnt_range[i, 1]
                )
        
        for step in range(steps):
            # Smoothly interpolate to sitting pose
            alpha = min(1.0, step / (steps * 0.7))
            q_target = self.home_qpos + alpha * (q_sit - self.home_qpos)
            
            self._apply_pd_control(q_target)
            mujoco.mj_step(self.model, self.data)
        
        # Check if sat down
        final_height = self.get_body_height()
        height_drop = initial_height - final_height
        return height_drop > initial_height * 0.2
    
    def walk_forward(self, secs: float = 2.0, speed: float = 0.2) -> bool:
        """
        Walk forward using a trotting gait.
        
        Uses a periodic gait where diagonal leg pairs (FL+RR, FR+RL)
        swing alternately in opposite phases. The gait modulates thigh angles
        to create forward motion.
        
        For forward motion:
        - Front legs (FL, FR) swing backward (decrease thigh angle)
        - Back legs (RL, RR) swing forward (increase thigh angle)
        
        Args:
            secs: Duration of walk (seconds)
            speed: Forward speed (m/s) - used to scale gait amplitude
            
        Returns:
            True if forward progress > 3 cm over the duration
        """
        dt = self.model.opt.timestep
        steps = int(secs / dt)
        
        # Record initial position
        initial_pos = self.data.xpos[self.base_id, 0:2].copy()
        
        # Scale gait amplitude with speed
        swing_amp = self.swing_amp * (speed / 0.2)
        
        for step in range(steps):
            t = step * dt
            phase = (t % self.gait_period) / self.gait_period
            
            q_target = self.home_qpos.copy()
            
            # Trotting gait: diagonal pairs swing in opposite phases
            if phase < 0.5:
                # Phase 1: FL+RR swing, FR+RL stance
                swing_phase = phase * 2  # 0 to 1
                swing_val = np.sin(swing_phase * np.pi)
                
                # FL: swing backward (decrease thigh angle)
                q_target[8] = self.home_qpos[8] - swing_amp * swing_val
                
                # RR: swing forward (increase thigh angle)
                q_target[17] = self.home_qpos[17] + swing_amp * swing_val
                
                # FR and RL stay at home position (stance)
                q_target[11] = self.home_qpos[11]
                q_target[14] = self.home_qpos[14]
            else:
                # Phase 2: FR+RL swing, FL+RR stance
                swing_phase = (phase - 0.5) * 2  # 0 to 1
                swing_val = np.sin(swing_phase * np.pi)
                
                # FR: swing backward (decrease thigh angle)
                q_target[11] = self.home_qpos[11] - swing_amp * swing_val
                
                # RL: swing forward (increase thigh angle)
                q_target[14] = self.home_qpos[14] + swing_amp * swing_val
                
                # FL and RR stay at home position (stance)
                q_target[8] = self.home_qpos[8]
                q_target[17] = self.home_qpos[17]
            
            # Clamp to joint limits
            for i in range(1, 13):
                if self.model.jnt_limited[i]:
                    qpos_idx = self.model.jnt_qposadr[i]
                    q_target[qpos_idx] = np.clip(
                        q_target[qpos_idx],
                        self.model.jnt_range[i, 0],
                        self.model.jnt_range[i, 1]
                    )
            
            self._apply_pd_control(q_target)
            mujoco.mj_step(self.model, self.data)
        
        # Check forward progress
        final_pos = self.data.xpos[self.base_id, 0:2].copy()
        forward_disp = final_pos[0] - initial_pos[0]
        
        # Need > 3 cm forward progress
        return forward_disp > 0.03
