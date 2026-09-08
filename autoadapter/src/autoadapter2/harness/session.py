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


def _finite_vector(value: Any, *, size: int, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{label} must contain {size} values")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} must contain finite values")
    return result


def _mocap_id(mujoco: Any, model: Any, body_name: str) -> int:
    body_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    mocap_id = int(model.body_mocapid[body_id])
    if mocap_id < 0:
        raise ValueError(f"Framework target body {body_name!r} is not a mocap body")
    return mocap_id


def _point_in_body_frame(
    mujoco: Any,
    model: Any,
    data: Any,
    *,
    body_name: str,
    position_m: Any,
) -> Any:
    import numpy as np

    body_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    local = np.asarray(
        _finite_vector(position_m, size=3, label="body-frame position"),
        dtype=float,
    )
    rotation = np.asarray(data.xmat[body_id], dtype=float).reshape(3, 3)
    return np.asarray(data.xpos[body_id], dtype=float) + rotation @ local


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
    placements = definition.get("mocap_body_positions", {})
    if not isinstance(placements, Mapping):
        raise ValueError("reset mocap_body_positions must be an object")
    for raw_body_name, raw_placement in placements.items():
        body_name = str(raw_body_name)
        if not isinstance(raw_placement, Mapping):
            raise ValueError("reset mocap placement must be an object")
        mocap_id = _mocap_id(mujoco, model, body_name)
        site_name = raw_placement.get("site_name")
        frame_body_name = raw_placement.get("frame_body_name")
        if isinstance(site_name, str) and site_name:
            site_id = _name_id(mujoco, model, mujoco.mjtObj.mjOBJ_SITE, site_name)
            data.mocap_pos[mocap_id] = data.site_xpos[site_id]
            if "offset_m" in raw_placement:
                if not isinstance(frame_body_name, str) or not frame_body_name:
                    raise ValueError(
                        "site mocap placement offset_m requires frame_body_name"
                    )
                frame_body_id = _name_id(
                    mujoco,
                    model,
                    mujoco.mjtObj.mjOBJ_BODY,
                    frame_body_name,
                )
                import numpy as np

                rotation = np.asarray(
                    data.xmat[frame_body_id], dtype=float
                ).reshape(3, 3)
                offset = np.asarray(
                    _finite_vector(
                        raw_placement.get("offset_m"),
                        size=3,
                        label="site mocap placement offset_m",
                    ),
                    dtype=float,
                )
                data.mocap_pos[mocap_id] += rotation @ offset
            elif frame_body_name is not None:
                raise ValueError(
                    "site mocap placement frame_body_name requires offset_m"
                )
            if "position_m" in raw_placement:
                raise ValueError("site mocap placement cannot declare position_m")
        elif isinstance(frame_body_name, str) and frame_body_name:
            data.mocap_pos[mocap_id] = _point_in_body_frame(
                mujoco,
                model,
                data,
                body_name=frame_body_name,
                position_m=raw_placement.get("position_m"),
            )
        else:
            raise ValueError(
                "reset mocap placement requires site_name or frame_body_name/position_m"
            )
    if placements:
        mujoco.mj_forward(model, data)


def build_framework_step_callback(
    mujoco: Any,
    model: Any,
    data: Any,
    events: Any,
) -> Callable[[], None] | None:
    """Compile private, Framework-owned linear mocap disturbances."""

    import numpy as np

    if events is None:
        return None
    if not isinstance(events, list):
        raise ValueError("framework_events must be a list")
    if not events:
        return None
    initial_time = float(data.time)
    compiled: list[dict[str, Any]] = []
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            raise ValueError("Framework event must be an object")
        if raw_event.get("kind") != "move_mocap_body":
            raise ValueError(f"unsupported Framework event kind {raw_event.get('kind')!r}")
        body_name = raw_event.get("body_name")
        frame_body_name = raw_event.get("frame_body_name")
        if not isinstance(body_name, str) or not body_name:
            raise ValueError("Framework mocap event requires body_name")
        if not isinstance(frame_body_name, str) or not frame_body_name:
            raise ValueError("Framework mocap event requires frame_body_name")
        mocap_id = _mocap_id(mujoco, model, body_name)
        frame_body_id = _name_id(
            mujoco, model, mujoco.mjtObj.mjOBJ_BODY, frame_body_name
        )
        start_time_s = float(raw_event.get("start_time_s"))
        duration_s = float(raw_event.get("duration_s"))
        if (
            not math.isfinite(start_time_s)
            or start_time_s < 0.0
            or not math.isfinite(duration_s)
            or duration_s <= 0.0
        ):
            raise ValueError("Framework mocap event times must be finite and valid")
        local_displacement = np.asarray(
            _finite_vector(
                raw_event.get("displacement_m"),
                size=3,
                label="Framework mocap displacement_m",
            ),
            dtype=float,
        )
        frame_rotation = np.asarray(data.xmat[frame_body_id], dtype=float).reshape(3, 3)
        world_displacement = frame_rotation @ local_displacement
        start_position = np.asarray(data.mocap_pos[mocap_id], dtype=float).copy()
        compiled.append(
            {
                "body_name": body_name,
                "mocap_id": mocap_id,
                "start_time_s": start_time_s,
                "duration_s": duration_s,
                "start_position": start_position,
                "displacement_m": local_displacement,
                "world_displacement": world_displacement,
                "progress": 0.0,
            }
        )

    def apply_events() -> None:
        elapsed = float(data.time) - initial_time + float(model.opt.timestep)
        for event in compiled:
            linear = min(
                max(
                    (elapsed - float(event["start_time_s"]))
                    / float(event["duration_s"]),
                    0.0,
                ),
                1.0,
            )
            progress = linear * linear * (3.0 - 2.0 * linear)
            data.mocap_pos[int(event["mocap_id"])] = (
                event["start_position"] + progress * event["world_displacement"]
            )
            event["progress"] = progress

    def event_evidence() -> list[dict[str, Any]]:
        return [
            {
                "kind": "move_mocap_body",
                "body_name": str(event["body_name"]),
                "start_time_s": float(event["start_time_s"]),
                "duration_s": float(event["duration_s"]),
                "displacement_m": np.asarray(
                    event["displacement_m"], dtype=float
                ).tolist(),
                "progress": float(event["progress"]),
                "complete": bool(float(event["progress"]) >= 1.0 - 1.0e-12),
                "actual_world_displacement_m": (
                    np.asarray(
                        data.mocap_pos[int(event["mocap_id"])], dtype=float
                    )
                    - np.asarray(event["start_position"], dtype=float)
                ).tolist(),
            }
            for event in compiled
        ]

    setattr(apply_events, "evidence", event_evidence)

    return apply_events


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
        before_physics_step: Callable[[], None] | None = None,
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
        self.before_physics_step = before_physics_step
        self._original_step: Any = None
        self.reset_evidence()

    def reset_evidence(self) -> None:
        import numpy as np

        self.step_count = 0
        self.direct_state_write_detected = False
        self.direct_state_write_fields: set[str] = set()
        self.ctrl_observed = False
        self.ctrl_changed = False
        self.actuator_force_nonzero_step_count = 0
        self.control_range_violation_detected = False
        self.control_range_violation_actuators: set[str] = set()
        self.samples: list[dict[str, Any]] = []
        self.contact_pair_step_counts: dict[tuple[str, str], int] = {}
        self.contact_pair_first_times_s: dict[tuple[str, str], float] = {}
        self.contact_pair_min_distances: dict[tuple[str, str], float] = {}
        self.minimum_contact_distance_m: float | None = None
        self.initial_time = float(self.data.time)
        self.initial_ctrl = np.array(self.data.ctrl, dtype=float, copy=True)
        self._last_qpos = np.array(self.data.qpos, dtype=float, copy=True)
        self._last_qvel = np.array(self.data.qvel, dtype=float, copy=True)
        self._last_mocap_pos = np.array(self.data.mocap_pos, dtype=float, copy=True)
        self._last_mocap_quat = np.array(self.data.mocap_quat, dtype=float, copy=True)
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
        self._tracked_site_ids = {
            self.mujoco.mj_id2name(
                self.model, self.mujoco.mjtObj.mjOBJ_SITE, index
            )
            or f"site_{index}": index
            for index in range(int(self.model.nsite))
        }
        self._tracked_body_ids = {
            self.mujoco.mj_id2name(
                self.model, self.mujoco.mjtObj.mjOBJ_BODY, index
            )
            or f"body_{index}": index
            for index in range(int(self.model.nbody))
        }
        self._initial_site_positions = {
            name: np.asarray(self.data.site_xpos[index], dtype=float).copy()
            for name, index in self._tracked_site_ids.items()
        }
        self._initial_body_positions = {
            name: np.asarray(self.data.xpos[index], dtype=float).copy()
            for name, index in self._tracked_body_ids.items()
        }
        self.site_max_displacement_from_reset = {
            name: 0.0 for name in self._tracked_site_ids
        }
        self.body_max_displacement_from_reset = {
            name: 0.0 for name in self._tracked_body_ids
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
                if self.before_physics_step is not None:
                    self.before_physics_step()
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
        if not np.allclose(
            self.data.mocap_pos, self._last_mocap_pos, rtol=0.0, atol=1e-12
        ):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("mocap_pos")
        if not np.allclose(
            self.data.mocap_quat, self._last_mocap_quat, rtol=0.0, atol=1e-12
        ):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("mocap_quat")
        if not math.isclose(float(self.data.time), self._last_time, rel_tol=0.0, abs_tol=1e-12):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("time")

    def _before_step(self) -> None:
        if self.step_count >= self.max_steps:
            raise StepBudgetExceeded(f"candidate exceeded {self.max_steps} MuJoCo steps")
        self._mark_unstepped_state_change()
        self._check_control_ranges()

    def _check_control_ranges(self) -> None:
        import numpy as np

        self.ctrl_observed = True
        if not np.allclose(self.data.ctrl, self.initial_ctrl, rtol=0.0, atol=1e-12):
            self.ctrl_changed = True
        for index in range(int(self.model.nu)):
            value = float(self.data.ctrl[index])
            violates = not math.isfinite(value)
            if not violates and bool(self.model.actuator_ctrllimited[index]):
                lower = float(self.model.actuator_ctrlrange[index, 0])
                upper = float(self.model.actuator_ctrlrange[index, 1])
                violates = value < lower - 1.0e-12 or value > upper + 1.0e-12
            if violates:
                self.control_range_violation_detected = True
                name = self.mujoco.mj_id2name(
                    self.model,
                    self.mujoco.mjtObj.mjOBJ_ACTUATOR,
                    index,
                ) or f"actuator_{index}"
                self.control_range_violation_actuators.add(name)

    def _after_step(self) -> None:
        import numpy as np

        self.step_count += 1
        # Sample only after the original mj_step has computed the applied force;
        # forward/reset observations alone do not count as physical actuation.
        forces = np.asarray(self.data.actuator_force, dtype=float)
        if np.all(np.isfinite(forces)) and np.any(np.abs(forces) > 1.0e-12):
            self.actuator_force_nonzero_step_count += 1
        for name, address in self._tracked_joint_qpos_addresses.items():
            deviation = abs(
                float(self.data.qpos[address]) - self._initial_joint_positions[name]
            )
            self.joint_max_abs_deviation_from_reset[name] = max(
                self.joint_max_abs_deviation_from_reset[name], deviation
            )
        for name, index in self._tracked_site_ids.items():
            displacement = float(
                np.linalg.norm(
                    np.asarray(self.data.site_xpos[index], dtype=float)
                    - self._initial_site_positions[name]
                )
            )
            self.site_max_displacement_from_reset[name] = max(
                self.site_max_displacement_from_reset[name], displacement
            )
        for name, index in self._tracked_body_ids.items():
            displacement = float(
                np.linalg.norm(
                    np.asarray(self.data.xpos[index], dtype=float)
                    - self._initial_body_positions[name]
                )
            )
            self.body_max_displacement_from_reset[name] = max(
                self.body_max_displacement_from_reset[name], displacement
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
            self.contact_pair_first_times_s.setdefault(pair, float(self.data.time))
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
        self._last_qpos = np.array(self.data.qpos, dtype=float, copy=True)
        self._last_qvel = np.array(self.data.qvel, dtype=float, copy=True)
        self._last_mocap_pos = np.array(self.data.mocap_pos, dtype=float, copy=True)
        self._last_mocap_quat = np.array(self.data.mocap_quat, dtype=float, copy=True)
        self._last_time = float(self.data.time)
        if elapsed > self.max_sim_time_s + 1e-12:
            raise StepBudgetExceeded(
                f"candidate exceeded {self.max_sim_time_s} simulated seconds"
            )
        if float(self.data.time) + 1e-12 >= self._next_sample_time:
            self.samples.append(self.snapshot())
            if self.capture_frame is not None:
                self.capture_frame()
            self._next_sample_time += self.sample_period_s

    def finish(self) -> None:
        self._mark_unstepped_state_change()
        self._check_control_ranges()
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
        event_evidence = getattr(self.before_physics_step, "evidence", None)
        return {
            "canonical_model_data": self.step_count > 0,
            "step_count": self.step_count,
            "physics_timestep_s": float(self.model.opt.timestep),
            "ctrl_observed_before_step": self.ctrl_observed,
            "ctrl_changed_from_reset": self.ctrl_changed,
            "actuator_force_nonzero_step_count": self.actuator_force_nonzero_step_count,
            "control_range_monitoring_complete": True,
            "control_range_violation_detected": self.control_range_violation_detected,
            "control_range_violation_actuators": sorted(
                self.control_range_violation_actuators
            ),
            "direct_state_write_detected": self.direct_state_write_detected,
            "direct_state_write_fields": sorted(self.direct_state_write_fields),
            "contact_pair_step_counts": [
                {"geom1": pair[0], "geom2": pair[1], "step_count": count}
                for pair, count in sorted(self.contact_pair_step_counts.items())
            ],
            "contact_pair_first_times_s": [
                {"geom1": pair[0], "geom2": pair[1], "first_time_s": time_s}
                for pair, time_s in sorted(self.contact_pair_first_times_s.items())
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
            "site_max_displacement_from_reset": dict(
                sorted(self.site_max_displacement_from_reset.items())
            ),
            "body_max_displacement_from_reset": dict(
                sorted(self.body_max_displacement_from_reset.items())
            ),
            "framework_events": (
                event_evidence() if callable(event_evidence) else []
            ),
            "samples": self.samples,
        }
