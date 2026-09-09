# SPDX-License-Identifier: Apache-2.0
"""MuJoCo truth sampling for diagnostic movement, ordering and hold checks."""
import mujoco
import numpy as np


def find_mujoco(skel):
    model = data = None
    for value in vars(skel).values():
        if isinstance(value, mujoco.MjModel):
            model = value
        elif isinstance(value, mujoco.MjData):
            data = value
    if model is None or data is None:
        raise ValueError("driver must expose its real MuJoCo model and data")
    return model, data


def sample_state(skel, state_refs: dict) -> dict:
    model, data = find_mujoco(skel)
    # Refresh world positions after integration without changing live qpos.
    mujoco.mj_kinematics(model, data)

    def index(kind, name):
        value = mujoco.mj_name2id(model, kind, name)
        if value < 0:
            raise ValueError(f"trusted observation binding {name!r} is absent")
        return value

    state = {"time": float(data.time),
             "finite": bool(np.isfinite(data.qpos).all() and
                            np.isfinite(data.qvel).all() and
                            np.isfinite(data.ctrl).all())}
    if "base_body" in state_refs:
        bid = index(mujoco.mjtObj.mjOBJ_BODY, state_refs["base_body"])
        rotation = data.xmat[bid].reshape(3, 3)
        state.update(base_xyz=data.xpos[bid].tolist(),
                     base_upright=float(rotation[2, 2]),
                     base_yaw=float(np.arctan2(rotation[1, 0], rotation[0, 0])))
    if "ee_body" in state_refs:
        bid = index(mujoco.mjtObj.mjOBJ_BODY, state_refs["ee_body"])
        state["ee"] = data.xpos[bid].tolist()
    if "ee_site" in state_refs:
        sid = index(mujoco.mjtObj.mjOBJ_SITE, state_refs["ee_site"])
        state["ee"] = data.site_xpos[sid].tolist()
    if "ee_sites" in state_refs:
        state["ees"] = {
            side: data.site_xpos[index(mujoco.mjtObj.mjOBJ_SITE, name)].tolist()
            for side, name in state_refs["ee_sites"].items()}
    if "fingertip_geoms" in state_refs:
        state["fingertips"] = {
            finger: data.geom_xpos[index(mujoco.mjtObj.mjOBJ_GEOM, name)].tolist()
            for finger, name in state_refs["fingertip_geoms"].items()}
    if "arm_joints" in state_refs:
        state["arm_qpos"] = [
            float(data.qpos[model.jnt_qposadr[index(mujoco.mjtObj.mjOBJ_JOINT, name)]])
            for name in state_refs["arm_joints"]]
    return state


class PhysicsTrace:
    """Observe every real step of one world, including batched mj_step calls.

    Used sequentially by the existing evaluator and framework checks. Tool
    labels locate phases; elapsed simulation time and state determine success.
    """

    def __init__(self, skel, state_refs: dict):
        self.skel, self.state_refs = skel, state_refs
        self.model, self.data = find_mujoco(skel)
        self.samples = []
        self.tool, self.idx = "initial", -1

    def snapshot(self):
        state = sample_state(self.skel, self.state_refs)
        state.update(tool=self.tool, idx=self.idx)
        self.samples.append(state)
        return state

    def __enter__(self):
        self.snapshot()
        self._original_step = mujoco.mj_step

        def step(model, data, nstep=1):
            if model is not self.model or data is not self.data:
                return self._original_step(model, data, nstep)
            for _ in range(int(nstep)):
                self._original_step(model, data)
                self.snapshot()

        mujoco.mj_step = step
        return self

    def __exit__(self, exc_type, exc, tb):
        mujoco.mj_step = self._original_step
