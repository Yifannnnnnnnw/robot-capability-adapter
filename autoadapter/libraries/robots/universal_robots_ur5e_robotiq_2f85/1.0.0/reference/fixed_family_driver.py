CONFIG = {'family': 'arm', 'site': 'pinch_site', 'base': 'base', 'spec': {'ee_site_name': 'pinch_site', 'arm_joint_names': ['shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint', 'wrist_1_joint', 'wrist_2_joint', 'wrist_3_joint'], 'arm_actuator_names': ['shoulder_pan', 'shoulder_lift', 'elbow', 'wrist_1', 'wrist_2', 'wrist_3'], 'joint_limits': {'shoulder_pan_joint': [-6.28319, 6.28319], 'shoulder_lift_joint': [-6.28319, 6.28319], 'elbow_joint': [-3.1415, 3.1415], 'wrist_1_joint': [-6.28319, 6.28319], 'wrist_2_joint': [-6.28319, 6.28319], 'wrist_3_joint': [-6.28319, 6.28319]}, 'continuous_joint_names': [], 'ik_max_iter': 100, 'ik_damping': 0.02}, 'gripper_actuator': 'fingers_actuator', 'gripper_joint': 'right_driver_joint', 'gripper_closed_ctrl': 255.0, 'gripper_open_ctrl': 0.0, 'closed_position': 0.8, 'open_position': 0.0, 'tool_geom_ids': [33, 35, 37, 39, 41, 42, 43, 47, 49, 51, 53, 54, 55], 'tool_geom_names': ['geom_33', 'geom_35', 'geom_37', 'geom_39', 'geom_41', 'right_pad1', 'right_pad2', 'geom_47', 'geom_49', 'geom_51', 'geom_53', 'left_pad1', 'left_pad2'], 'tool_body_names': ['left_coupler', 'left_driver', 'left_follower', 'left_pad', 'left_spring_link', 'right_coupler', 'right_driver', 'right_follower', 'right_pad', 'right_spring_link', 'robotiq_base'], 'fixture_origin_m': [-0.13399725317860262, 0.4919998706957351, 0.33220036732109454], 'fk_targets_m': [[-0.13399740197579427, 0.451491054171059, 0.26604776837560373], [-0.13399750178676234, 0.424318335696851, 0.2309966113234071]]}

"""Private actuator-only reference for fixed A1-A5 diagnostics."""
import math
import mujoco
import numpy as np
from autoadapter2.trusted_skeletons.arm_serial_dls import ArmSerialDLSSkeleton, ArmSpec


class Driver:
    def __init__(self, model, data):
        self.model, self.data = model, data
        self.arm = ArmSerialDLSSkeleton(model=model, data=data, spec=ArmSpec(**CONFIG['spec']))
        self.site = model.site(CONFIG['site']).id
        self.base = model.body(CONFIG['base']).id
        self.joints = [model.joint(n).id for n in CONFIG['spec']['arm_joint_names']]
        self.dofs = [int(model.jnt_dofadr[j]) for j in self.joints]
        self.grip = model.actuator(CONFIG['gripper_actuator']).id if CONFIG['gripper_actuator'] else None
        self.tools = set(CONFIG['tool_geom_ids'])

    def _step(self, target, gripper=None):
        jac = np.zeros((3, self.model.nv))
        mujoco.mj_jacSite(self.model, self.data, jac, None, self.site)
        j = jac[:, self.dofs]
        err = np.asarray(target) - self.data.site_xpos[self.site]
        dq = j.T @ np.linalg.solve(j @ j.T + .0004 * np.eye(3), err)
        q = self.arm.get_joint_positions() + np.clip(1.4 * dq, -.06, .06)
        limits = CONFIG['spec']['joint_limits']
        q = np.asarray([np.clip(v, *limits[n]) if n in limits else v for n,v in zip(CONFIG['spec']['arm_joint_names'],q)])
        self.arm.set_arm_actuators(q)
        if self.grip is not None and gripper is not None: self.data.ctrl[self.grip] = gripper
        mujoco.mj_step(self.model, self.data)

    def _move(self, target, duration):
        grip = float(self.data.ctrl[self.grip]) if self.grip is not None else None
        start = self.data.site_xpos[self.site].copy()
        end = float(self.data.time) + duration
        began = float(self.data.time)
        travel = max(.1, min(duration - .6, float(np.linalg.norm(np.asarray(target)-start))/.06))
        while self.data.time < end:
            alpha = min(1., (self.data.time-began)/travel)
            self._step(start + alpha*(np.asarray(target)-start), grip)

    def move_end_effector_to_position(self, request):
        self._move(request['target_position_m'], request['max_duration_s'])

    def trace_cartesian_path(self, request):
        for point in request['waypoints_m']:
            self._move(point, request['max_duration_per_segment_s'])

    def set_gripper_opening(self, request):
        if self.grip is None: raise ValueError('this robot has no gripper')
        fraction = float(request['opening_fraction'])
        target = CONFIG['gripper_closed_ctrl'] + fraction*(CONFIG['gripper_open_ctrl']-CONFIG['gripper_closed_ctrl'])
        q = self.arm.get_joint_positions()
        end = self.data.time + request['max_duration_s']
        while self.data.time < end:
            self.arm.set_arm_actuators(q)
            self.data.ctrl[self.grip] = target
            mujoco.mj_step(self.model,self.data)

    def approach_until_contact(self, request):
        began = float(self.data.time)
        self._move(request['precontact_position_m'], 1.)
        pre = np.asarray(request['precontact_position_m'])
        direction = np.asarray(request['approach_direction_unit'])
        start = float(self.data.time)
        stopped = None
        grip = float(self.data.ctrl[self.grip]) if self.grip is not None else None
        while self.data.time < began + request['max_duration_s']:
            touching = any((int(c.geom1) in self.tools) != (int(c.geom2) in self.tools) for c in self.data.contact)
            if touching and stopped is None: stopped = self.data.site_xpos[self.site].copy()
            progress = min(request['max_travel_m'], (self.data.time-start)*request['max_approach_speed_m_s'])
            self._step(stopped if stopped is not None else pre + direction*progress, grip)

    def move_cartesian_offset_and_return(self, request):
        start = self.data.site_xpos[self.site].copy()
        target = start + self.data.xmat[self.base].reshape(3,3) @ np.asarray(request['offset_robot_base_m'])
        self._move(target,request['max_duration_per_leg_s'])
        self._move(start,request['max_duration_per_leg_s'])


def build(model, data):
    return Driver(model, data)
