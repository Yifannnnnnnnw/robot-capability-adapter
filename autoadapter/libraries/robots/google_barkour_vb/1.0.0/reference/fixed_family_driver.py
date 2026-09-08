CONFIG = {'family': 'quadruped', 'base': 'torso', 'feet': [['geom_9'], ['geom_15'], ['geom_19'], ['geom_25']], 'spec': {'base_body_name': 'torso', 'leg_joint_names': {'front_left': ['abduction_front_left', 'hip_front_left', 'knee_front_left'], 'hind_left': ['abduction_hind_left', 'hip_hind_left', 'knee_hind_left'], 'front_right': ['abduction_front_right', 'hip_front_right', 'knee_front_right'], 'hind_right': ['abduction_hind_right', 'hip_hind_right', 'knee_hind_right']}, 'leg_actuator_names': {'front_left': ['abduction_front_left', 'hip_front_left', 'knee_front_left'], 'hind_left': ['abduction_hind_left', 'hip_hind_left', 'knee_hind_left'], 'front_right': ['abduction_front_right', 'hip_front_right', 'knee_front_right'], 'hind_right': ['abduction_hind_right', 'hip_hind_right', 'knee_hind_right']}, 'home_qpos': [0.0, 0.5, 1.0, 0.0, 0.5, 1.0, 0.0, 0.5, 1.0, 0.0, 0.5, 1.0], 'actuation': 'joint_position', 'body_height_target': 0.25636956301084757}, 'home_height_m': 0.25636956301084757, 'barkour': True, 'foot_geom_ids': [9, 15, 19, 25], 'fixed_reset_keyframe': 'fixed_settled', 'fixed_scene_entrypoint': 'assets/fixed_scene.xml'}

"""Private physical reference for fixed quadruped diagnostics; no verdict access."""
import math
import mujoco
import numpy as np
from autoadapter2.trusted_skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton, QuadrupedSpec


from typing import Any

from autoadapter2.trusted_skeletons.quadruped_position_policy import (
    BARKOUR_POLICY_JOINT_HIGH,
    BARKOUR_POLICY_JOINT_LOW,
    BarkourPositionPolicySkeleton,
    QuadrupedPositionPolicySpec,
)


_JOINT_NAMES = (
    "abduction_front_left",
    "hip_front_left",
    "knee_front_left",
    "abduction_hind_left",
    "hip_hind_left",
    "knee_hind_left",
    "abduction_front_right",
    "hip_front_right",
    "knee_front_right",
    "abduction_hind_right",
    "hip_hind_right",
    "knee_hind_right",
)


def barkour_spec() -> QuadrupedPositionPolicySpec:
    """Return the exact public Barkour vB position-policy mapping."""

    return QuadrupedPositionPolicySpec(
        base_body_name="torso",
        imu_site_name="imu_frame",
        gyro_sensor_name="gyro",
        home_keyframe_name="home",
        joint_names=_JOINT_NAMES,
        actuator_names=_JOINT_NAMES,
        policy_joint_low=BARKOUR_POLICY_JOINT_LOW,
        policy_joint_high=BARKOUR_POLICY_JOINT_HIGH,
        action_scale=0.3,
        policy_period_s=0.02,
        expected_physics_timestep_s=0.001,
        minimum_base_height_m=0.18,
        maximum_command_duration_s=20.0,
    )



class Driver:
    def __init__(self, model, data):
        self.model, self.data = model, data
        self.pd = QuadrupedPDGaitSkeleton(model=model, data=data, spec=QuadrupedSpec(**CONFIG['spec']))
        self.motion = self.pd
        self.base = model.body(CONFIG['base']).id
        self.home = np.asarray(CONFIG['spec']['home_qpos'])
        if CONFIG.get('barkour'):
            self.motion = BarkourPositionPolicySkeleton(model=model,data=data,spec=barkour_spec())

    def _yaw(self):
        r=self.data.xmat[self.base].reshape(3,3)
        return math.atan2(r[1,0],r[0,0])

    def _command(self, vx, vy, yaw, duration):
        self.motion.command_planar_velocity(vx=float(vx),vy=float(vy),yaw_rate=float(yaw),duration=float(duration))

    def track_planar_twist(self, request):
        self._command(*request['linear_velocity_body_m_s'],request['yaw_rate_rad_s'],request['duration_s'])

    def _pose(self, target, yaw, duration):
        end=self.data.time+duration
        while self.data.time < end:
            angle=self._yaw();rot=np.asarray([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
            error=rot.T@(np.asarray(target)-self.data.xpos[self.base,:2])
            turn=math.atan2(math.sin(yaw-angle),math.cos(yaw-angle))
            self._command(np.clip(1.5*error[0],-.3,.3),np.clip(1.5*error[1],-.15,.15),np.clip(2*turn,-.6,.6),.05)

    def move_body_relative_pose(self, request):
        yaw=self._yaw();rot=np.asarray([[math.cos(yaw),-math.sin(yaw)],[math.sin(yaw),math.cos(yaw)]])
        target=self.data.xpos[self.base,:2].copy()+rot@np.asarray(request['translation_initial_yaw_m'])
        self._pose(target,yaw+request['yaw_delta_rad'],request['max_duration_s'])

    def trace_planar_path(self, request):
        yaw=self._yaw();origin=self.data.xpos[self.base,:2].copy();rot=np.asarray([[math.cos(yaw),-math.sin(yaw)],[math.sin(yaw),math.cos(yaw)]])
        for point in request['waypoints_initial_yaw_m']:
            self._pose(origin+rot@np.asarray(point),yaw,request['max_duration_s']/len(request['waypoints_initial_yaw_m']))

    def set_body_height(self, request):
        target=float(request['target_height_m']);end=self.data.time+request['max_duration_s']
        joints=sum(CONFIG['spec']['leg_joint_names'].values(),[])
        dofs=[int(self.model.jnt_dofadr[self.model.joint(n).id]) for n in joints]
        while self.data.time < end:
            q=self.pd.get_joint_positions();error=target-self.data.xpos[self.base,2]
            for k,g in enumerate(CONFIG['foot_geom_ids']):
                jac=np.zeros((3,self.model.nv));mujoco.mj_jacGeom(self.model,self.data,jac,None,g)
                j=jac[:,dofs[k*3:k*3+3]]
                delta=j.T@np.linalg.solve(j@j.T+.001*np.eye(3),np.asarray([0,0,-error]))
                q[k*3:k*3+3]+=np.clip(.3*delta,-.01,.01)
            self.pd.apply_pd_posture(q);self.pd.step(1)

    def hold_stable_stance(self, request):
        end=self.data.time+request['duration_s']
        while self.data.time < end:
            self.pd.apply_pd_posture(self.home);self.pd.step(1)


def build(model,data):
    return Driver(model,data)
