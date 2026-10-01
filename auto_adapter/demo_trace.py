"""Record named physical state from the MCP server's one live MuJoCo world."""
from __future__ import annotations

import json
from pathlib import Path

import mujoco
import numpy as np


class DemoTrace:
    """Sample native steps independently of video frames and driver getters.

    Installed before the video recorder and removed after it. Missing steps,
    backwards time and invalid samples make the trace unscorable.
    """

    def __init__(self, model, data, bindings: dict, path: Path):
        if not bindings:
            raise ValueError("demo success requires physical bindings")
        self.model, self.data = model, data
        self.path = Path(path)
        self.errors: list[str] = []
        self.count = 0
        self.last_time: float | None = None
        self.refs = {}
        for label, spec in bindings.items():
            kind, name = spec["kind"], spec["name"]
            if kind not in ("body", "site", "geom", "joint"):
                raise ValueError(f"unknown demo binding kind: {kind}")
            ref = getattr(model, kind)(name)
            if kind == "joint" and int(model.jnt_type[ref.id]) not in (
                int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)
            ):
                raise ValueError(f"demo joint must be scalar: {name}")
            offset = np.asarray(spec.get("local_position_m", [0, 0, 0]), dtype=float)
            if offset.shape != (3,) or not np.isfinite(offset).all():
                raise ValueError(f"invalid local measurement point: {label}")
            self.refs[label] = (kind, int(ref.id), offset)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8")
        self._step = mujoco.mj_step
        self._step2 = mujoco.mj_step2
        self._closed = False
        self.snapshot()

        def step(current_model, current_data, nstep=1):
            if current_model is not model or current_data is not data:
                return self._step(current_model, current_data, nstep)
            for _ in range(int(nstep)):
                self._step(model, data)
                self.snapshot()

        def step2(current_model, current_data):
            self._step2(current_model, current_data)
            if current_model is model and current_data is data:
                self.snapshot()

        mujoco.mj_step, mujoco.mj_step2 = step, step2

    def snapshot(self):
        if self.errors:
            return
        try:
            model, data = self.model, self.data
            now = float(data.time)
            if not np.isfinite(now):
                raise ValueError("nonfinite simulation time")
            if self.last_time is not None:
                delta = now - self.last_time
                if delta < -1e-9:
                    raise ValueError("simulation time moved backwards")
                if delta > float(model.opt.timestep) * 1.01:
                    raise ValueError("unobserved native simulation steps")
                if abs(delta) < 1e-9:
                    return
            if not all(np.isfinite(value).all() for value in (data.qpos, data.qvel)):
                raise ValueError("nonfinite MuJoCo state")
            # mj_step integrates qpos after computing derived positions. Refresh
            # only kinematics/velocities so positions match this sample's time;
            # do not advance another world or rerun dynamics/contact solving.
            mujoco.mj_kinematics(model, data)
            mujoco.mj_comPos(model, data)
            mujoco.mj_comVel(model, data)
            state = {}
            for label, (kind, idx, local_offset) in self.refs.items():
                if kind == "joint":
                    state[label] = {"value": float(data.qpos[model.jnt_qposadr[idx]])}
                    continue
                if kind == "body":
                    pos, rot = data.xpos[idx], data.xmat[idx]
                else:
                    pos = getattr(data, kind + "_xpos")[idx]
                    rot = getattr(data, kind + "_xmat")[idx]
                velocity = np.zeros(6)
                mujoco.mj_objectVelocity(
                    model, data, getattr(mujoco.mjtObj, "mjOBJ_" + kind.upper()),
                    idx, velocity, 0,
                )
                world_offset = np.asarray(rot).reshape(3, 3) @ local_offset
                state[label] = {
                    "position": (pos + world_offset).tolist(), "rotation": rot.tolist(),
                    "linear_velocity": (velocity[3:] + np.cross(velocity[:3], world_offset)).tolist(),
                    "angular_velocity": velocity[:3].tolist(),
                }
            contacts = set()
            for contact in data.contact[:data.ncon]:
                if contact.dist > 0:
                    continue
                body_ids = [int(model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2)]
                names = [model.body(idx).name or f"body#{idx}" for idx in body_ids]
                contacts.add(tuple(sorted(names)))
            # These are the solver's contacts from the completed integration
            # interval, not a new collision query at the endpoint. Re-running
            # mj_collision here would invalidate the driver's efc/contact-force
            # correspondence. Keep the interval explicit beside endpoint state.
            row = {"time": now, "state": state, "contacts": sorted(contacts),
                   "contact_interval_s": [self.last_time if self.last_time is not None else now, now]}
            self._stream.write(json.dumps(row, allow_nan=False) + "\n")
            self.last_time = now
            self.count += 1
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")

    def close(self) -> dict:
        if not self._closed:
            self.snapshot()
            mujoco.mj_step, mujoco.mj_step2 = self._step, self._step2
            self._stream.close()
            self._closed = True
        return {
            "path": str(self.path), "sample_count": self.count,
            "timestep_s": float(self.model.opt.timestep),
            "error": "; ".join(self.errors) if self.errors else None,
        }
