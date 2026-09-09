# SPDX-License-Identifier: Apache-2.0
CONFIG = {'family': 'quadruped', 'spec': {'base_body_name': 'base', 'leg_joint_names': {'LF': ['LF_HAA', 'LF_HFE', 'LF_KFE'], 'RF': ['RF_HAA', 'RF_HFE', 'RF_KFE'], 'LH': ['LH_HAA', 'LH_HFE', 'LH_KFE'], 'RH': ['RH_HAA', 'RH_HFE', 'RH_KFE']}, 'leg_actuator_names': {'LF': ['LF_HAA', 'LF_HFE', 'LF_KFE'], 'RF': ['RF_HAA', 'RF_HFE', 'RF_KFE'], 'LH': ['LH_HAA', 'LH_HFE', 'LH_KFE'], 'RH': ['RH_HAA', 'RH_HFE', 'RH_KFE']}, 'home_qpos': [0.0, 0.7, -1.4, 0.0, 0.7, -1.4, 0.0, -0.7, 1.4, 0.0, -0.7, 1.4], 'actuation': 'joint_position', 'body_height_target': 0.3737473812630869}, 'feet': [['geom_52'], ['geom_66'], ['geom_80'], ['geom_94']], 'base': 'base', 'home_height_m': 0.3737473812630869, 'foot_geom_ids': [52, 66, 80, 94], 'fixed_reset_keyframe': 'fixed_settled', 'fixed_scene_entrypoint': 'assets/fixed_scene.xml'}

# Fixed-environment calibration for the original AA1 ANYmal-C asset.  The
# paired front/rear offsets preserve level support while reaching the upper
# calibrated target through native position servos.
_HOME_HEIGHT_M = 0.3513
_HEIGHT_SPAN_M = 0.0523
_REAR_THIGH_OFFSET_RAD = 0.20
_REAR_CALF_OFFSET_RAD = 0.30

"""Private physical reference for fixed quadruped diagnostics; no verdict access."""
import math
import mujoco
import numpy as np
from auto_adapter.skeletons.quadruped_pd_gait import QuadrupedPDGaitSkeleton, QuadrupedSpec


class Driver:
    def __init__(self, model, data):
        self.model, self.data = model, data
        self.pd = QuadrupedPDGaitSkeleton(model=model, data=data, spec=QuadrupedSpec(**CONFIG['spec']))
        self.motion = self.pd
        self.base = model.body(CONFIG['base']).id
        self.home = np.asarray(CONFIG['spec']['home_qpos'])

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
        level=np.clip((target - _HOME_HEIGHT_M) / _HEIGHT_SPAN_M, -1.0, 1.0)
        q_target=self.home.copy()
        for leg_index in (0, 1):
            q_target[3 * leg_index + 1] += _REAR_THIGH_OFFSET_RAD * level
            q_target[3 * leg_index + 2] += _REAR_CALF_OFFSET_RAD * level
        for leg_index in (2, 3):
            q_target[3 * leg_index + 1] -= _REAR_THIGH_OFFSET_RAD * level
            q_target[3 * leg_index + 2] -= _REAR_CALF_OFFSET_RAD * level
        while self.data.time < end:
            self.pd.apply_pd_posture(q_target);self.pd.step(1)

    def hold_stable_stance(self, request):
        end=self.data.time+request['duration_s']
        while self.data.time < end:
            self.pd.apply_pd_posture(self.home);self.pd.step(1)


def build(model,data):
    return Driver(model,data)
