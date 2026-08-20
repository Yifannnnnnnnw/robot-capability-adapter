"""Framework-owned canonical session tracking for one private case."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any


class StepBudgetExceeded(RuntimeError):
    """Raised when candidate execution exceeds its physical step budget."""


def _name_id(mujoco: Any, model: Any, object_type: Any, name: str) -> int:
    identifier = int(mujoco.mj_name2id(model, object_type, name))
    if identifier < 0:
        raise ValueError(f"reset references unknown MuJoCo name {name!r}")
    return identifier


def apply_framework_reset(
    mujoco: Any,
    model: Any,
    data: Any,
    reset: Mapping[str, Any] | None,
) -> None:
    """Apply private reset state before candidate evidence collection starts."""

    definition = dict(reset or {"kind": "default"})
    kind = definition.get("kind", "default")
    if kind == "default":
        mujoco.mj_resetData(model, data)
    elif kind == "keyframe":
        name = definition.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("keyframe reset requires a name")
        key_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_KEY, name)
        mujoco.mj_resetDataKeyframe(model, data, key_id)
    else:
        raise ValueError(f"unsupported Framework reset kind {kind!r}")

    for field, expected in (("qpos", int(model.nq)), ("qvel", int(model.nv)), ("ctrl", int(model.nu))):
        if field not in definition:
            continue
        values = definition[field]
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError(f"reset {field} must contain {expected} values")
        getattr(data, field)[:] = values
    for name, value in definition.get("joint_positions", {}).items():
        joint_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_JOINT, str(name))
        address = int(model.jnt_qposadr[joint_id])
        data.qpos[address] = float(value)
    for name, value in definition.get("actuator_controls", {}).items():
        actuator_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_ACTUATOR, str(name))
        data.ctrl[actuator_id] = float(value)
    for name, values in definition.get("body_quaternions", {}).items():
        body_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, str(name))
        quaternion = [float(value) for value in values]
        if len(quaternion) != 4 or not all(math.isfinite(value) for value in quaternion):
            raise ValueError("reset body quaternion must contain four finite values")
        norm = math.sqrt(sum(value * value for value in quaternion))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("reset body quaternion must be normalized")
        model.body_quat[body_id] = quaternion
    mujoco.mj_forward(model, data)


class TrackedMuJoCoSession(AbstractContextManager["TrackedMuJoCoSession"]):
    """Wrap module-level mj_step and retain trusted state/actuation evidence."""

    def __init__(
        self,
        *,
        mujoco: Any,
        model: Any,
        data: Any,
        max_steps: int,
        max_sim_time_s: float,
        sample_hz: float = 20.0,
        capture_frame: Callable[[], None] | None = None,
    ) -> None:
        if max_steps <= 0 or max_sim_time_s <= 0 or sample_hz <= 0:
            raise ValueError("session budgets and sample_hz must be positive")
        self.mujoco = mujoco
        self.model = model
        self.data = data
        self.max_steps = int(max_steps)
        self.max_sim_time_s = float(max_sim_time_s)
        self.sample_period_s = 1.0 / float(sample_hz)
        self.capture_frame = capture_frame
        self._original_step: Any = None
        self.reset_evidence()

    def reset_evidence(self) -> None:
        import numpy as np

        self.step_count = 0
        self.direct_state_write_detected = False
        self.direct_state_write_fields: set[str] = set()
        self.ctrl_observed = False
        self.ctrl_changed = False
        self.samples: list[dict[str, Any]] = []
        self.contact_pair_step_counts: dict[tuple[str, str], int] = {}
        self.contact_pair_min_distances: dict[tuple[str, str], float] = {}
        self.minimum_contact_distance_m: float | None = None
        self.initial_time = float(self.data.time)
        self.initial_ctrl = np.array(self.data.ctrl, dtype=float, copy=True)
        self._last_qpos = np.array(self.data.qpos, dtype=float, copy=True)
        self._last_qvel = np.array(self.data.qvel, dtype=float, copy=True)
        self._last_time = float(self.data.time)
        self._tracked_joint_qpos_addresses: dict[str, int] = {}
        for index in range(int(self.model.njnt)):
            joint_type = int(self.model.jnt_type[index])
            if joint_type not in {
                int(self.mujoco.mjtJoint.mjJNT_HINGE),
                int(self.mujoco.mjtJoint.mjJNT_SLIDE),
            }:
                continue
            name = self.mujoco.mj_id2name(
                self.model, self.mujoco.mjtObj.mjOBJ_JOINT, index
            ) or f"joint_{index}"
            self._tracked_joint_qpos_addresses[name] = int(
                self.model.jnt_qposadr[index]
            )
        self._initial_joint_positions = {
            name: float(self.data.qpos[address])
            for name, address in self._tracked_joint_qpos_addresses.items()
        }
        self.joint_max_abs_deviation_from_reset = {
            name: 0.0 for name in self._tracked_joint_qpos_addresses
        }
        self.samples.append(self.snapshot())
        self._next_sample_time = self.initial_time + self.sample_period_s

    def __enter__(self) -> TrackedMuJoCoSession:
        if self._original_step is not None:
            raise RuntimeError("tracked session is already installed")
        self._original_step = self.mujoco.mj_step

        def tracked_step(model: Any, data: Any, nstep: int = 1) -> None:
            if model is not self.model or data is not self.data:
                raise RuntimeError("candidate attempted to step a non-canonical MuJoCo session")
            steps = int(nstep)
            if steps <= 0:
                raise ValueError("mj_step nstep must be positive")
            for _ in range(steps):
                self._before_step()
                assert self._original_step is not None
                self._original_step(self.model, self.data)
                self._after_step()

        self.mujoco.mj_step = tracked_step
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._original_step is not None:
            self.mujoco.mj_step = self._original_step
            self._original_step = None

    def _mark_unstepped_state_change(self) -> None:
        import numpy as np

        if not np.allclose(self.data.qpos, self._last_qpos, rtol=0.0, atol=1e-12):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("qpos")
        if not np.allclose(self.data.qvel, self._last_qvel, rtol=0.0, atol=1e-12):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("qvel")
        if not math.isclose(float(self.data.time), self._last_time, rel_tol=0.0, abs_tol=1e-12):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("time")

    def _before_step(self) -> None:
        import numpy as np

        if self.step_count >= self.max_steps:
            raise StepBudgetExceeded(f"candidate exceeded {self.max_steps} MuJoCo steps")
        self._mark_unstepped_state_change()
        self.ctrl_observed = True
        if not np.allclose(self.data.ctrl, self.initial_ctrl, rtol=0.0, atol=1e-12):
            self.ctrl_changed = True

    def _after_step(self) -> None:
        import numpy as np

        self.step_count += 1
        for name, address in self._tracked_joint_qpos_addresses.items():
            deviation = abs(
                float(self.data.qpos[address]) - self._initial_joint_positions[name]
            )
            self.joint_max_abs_deviation_from_reset[name] = max(
                self.joint_max_abs_deviation_from_reset[name], deviation
            )
        pairs: set[tuple[str, str]] = set()
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            names = []
            for geom_id in (int(contact.geom1), int(contact.geom2)):
                names.append(
                    self.mujoco.mj_id2name(
                        self.model, self.mujoco.mjtObj.mjOBJ_GEOM, geom_id
                    )
                    or f"geom_{geom_id}"
                )
            pair = tuple(sorted(names))
            pairs.add(pair)
            distance = float(contact.dist)
            previous = self.contact_pair_min_distances.get(pair)
            if previous is None or distance < previous:
                self.contact_pair_min_distances[pair] = distance
            if (
                self.minimum_contact_distance_m is None
                or distance < self.minimum_contact_distance_m
            ):
                self.minimum_contact_distance_m = distance
        for pair in pairs:
            self.contact_pair_step_counts[pair] = (
                self.contact_pair_step_counts.get(pair, 0) + 1
            )
        elapsed = float(self.data.time) - self.initial_time
        if elapsed > self.max_sim_time_s + 1e-12:
            raise StepBudgetExceeded(
                f"candidate exceeded {self.max_sim_time_s} simulated seconds"
            )
        self._last_qpos = np.array(self.data.qpos, dtype=float, copy=True)
        self._last_qvel = np.array(self.data.qvel, dtype=float, copy=True)
        self._last_time = float(self.data.time)
        if float(self.data.time) + 1e-12 >= self._next_sample_time:
            self.samples.append(self.snapshot())
            if self.capture_frame is not None:
                self.capture_frame()
            self._next_sample_time += self.sample_period_s

    def finish(self) -> None:
        self._mark_unstepped_state_change()
        if not self.samples or self.samples[-1]["time"] != float(self.data.time):
            self.samples.append(self.snapshot())
            if self.capture_frame is not None:
                self.capture_frame()

    def snapshot(self) -> dict[str, Any]:
        import numpy as np

        body_positions = {
            self.mujoco.mj_id2name(self.model, self.mujoco.mjtObj.mjOBJ_BODY, index)
            or f"body_{index}": np.asarray(self.data.xpos[index], dtype=float).tolist()
            for index in range(int(self.model.nbody))
        }
        body_quaternions = {
            self.mujoco.mj_id2name(self.model, self.mujoco.mjtObj.mjOBJ_BODY, index)
            or f"body_{index}": np.asarray(self.data.xquat[index], dtype=float).tolist()
            for index in range(int(self.model.nbody))
        }
        site_positions = {
            self.mujoco.mj_id2name(self.model, self.mujoco.mjtObj.mjOBJ_SITE, index)
            or f"site_{index}": np.asarray(self.data.site_xpos[index], dtype=float).tolist()
            for index in range(int(self.model.nsite))
        }
        joint_positions: dict[str, float] = {}
        joint_velocities: dict[str, float] = {}
        for index in range(int(self.model.njnt)):
            joint_type = int(self.model.jnt_type[index])
            if joint_type not in {
                int(self.mujoco.mjtJoint.mjJNT_HINGE),
                int(self.mujoco.mjtJoint.mjJNT_SLIDE),
            }:
                continue
            name = self.mujoco.mj_id2name(
                self.model, self.mujoco.mjtObj.mjOBJ_JOINT, index
            ) or f"joint_{index}"
            joint_positions[name] = float(self.data.qpos[int(self.model.jnt_qposadr[index])])
            joint_velocities[name] = float(self.data.qvel[int(self.model.jnt_dofadr[index])])
        actuator_controls = {
            self.mujoco.mj_id2name(
                self.model, self.mujoco.mjtObj.mjOBJ_ACTUATOR, index
            )
            or f"actuator_{index}": float(self.data.ctrl[index])
            for index in range(int(self.model.nu))
        }
        contacts = []
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            geom1 = int(contact.geom1)
            geom2 = int(contact.geom2)
            contacts.append(
                {
                    "geom1": self.mujoco.mj_id2name(
                        self.model, self.mujoco.mjtObj.mjOBJ_GEOM, geom1
                    )
                    or f"geom_{geom1}",
                    "geom2": self.mujoco.mj_id2name(
                        self.model, self.mujoco.mjtObj.mjOBJ_GEOM, geom2
                    )
                    or f"geom_{geom2}",
                    "distance": float(contact.dist),
                }
            )
        return {
            "time": float(self.data.time),
            "qpos": np.asarray(self.data.qpos, dtype=float).tolist(),
            "qvel": np.asarray(self.data.qvel, dtype=float).tolist(),
            "ctrl": np.asarray(self.data.ctrl, dtype=float).tolist(),
            "body_positions": body_positions,
            "body_quaternions": body_quaternions,
            "site_positions": site_positions,
            "joint_positions": joint_positions,
            "joint_velocities": joint_velocities,
            "actuator_controls": actuator_controls,
            "contacts": contacts,
        }

    def evidence(self) -> dict[str, Any]:
        return {
            "canonical_model_data": self.step_count > 0,
            "step_count": self.step_count,
            "ctrl_observed_before_step": self.ctrl_observed,
            "ctrl_changed_from_reset": self.ctrl_changed,
            "direct_state_write_detected": self.direct_state_write_detected,
            "direct_state_write_fields": sorted(self.direct_state_write_fields),
            "contact_pair_step_counts": [
                {"geom1": pair[0], "geom2": pair[1], "step_count": count}
                for pair, count in sorted(self.contact_pair_step_counts.items())
            ],
            "contact_monitoring_complete": True,
            "minimum_contact_distance_m": self.minimum_contact_distance_m,
            "contact_pair_min_distances": [
                {
                    "geom1": pair[0],
                    "geom2": pair[1],
                    "minimum_distance_m": distance,
                }
                for pair, distance in sorted(
                    self.contact_pair_min_distances.items()
                )
            ],
            "joint_max_abs_deviation_from_reset": dict(
                sorted(self.joint_max_abs_deviation_from_reset.items())
            ),
            "samples": self.samples,
        }
