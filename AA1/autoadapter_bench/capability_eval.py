# SPDX-License-Identifier: Apache-2.0
"""Run one fixed capability-validation case against a real MuJoCo world.

The evaluator is deliberately small.  It owns the case reset, the canonical
``mujoco.mj_step`` observation boundary, trace/video capture, and the call into
the trusted B1 metric function.  A capability method's return value is kept
only as diagnostic metadata; the verdict always comes from the sampled world
and the guards in this module.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]


def _model_data(skel: Any) -> tuple[Any, Any, Any]:
    """Return the one model/data pair owned by a skeleton."""
    model = getattr(skel, "model", None)
    if model is None:
        model = getattr(skel, "_model", None)
    data = getattr(skel, "data", None)
    if data is None:
        data = getattr(skel, "_data", None)
    if model is None or data is None:
        raise ValueError("skeleton must expose model and data")
    import mujoco  # noqa: PLC0415

    return model, data, mujoco


def _case_id(case: Mapping[str, Any]) -> str:
    value = case.get("id", case.get("case_id"))
    if not isinstance(value, str) or not value:
        raise ValueError("capability case requires a non-empty id")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _name(mujoco: Any, model: Any, obj: Any, index: int) -> str:
    value = mujoco.mj_id2name(model, obj, int(index))
    if value:
        return str(value)
    enum_name = str(obj).split("mjOBJ_")[-1].lower()
    return f"{enum_name}_{int(index)}"


def _obj(mujoco: Any, name: str) -> Any:
    return getattr(mujoco.mjtObj, name)


def _joint_info(mujoco: Any, model: Any, joint_name: str) -> tuple[int, int, int]:
    joint_id = int(mujoco.mj_name2id(model, _obj(mujoco, "mjOBJ_JOINT"), joint_name))
    if joint_id < 0:
        raise ValueError(f"unknown reset joint {joint_name!r}")
    joint_type = int(model.jnt_type[joint_id])
    if joint_type == int(mujoco.mjtJoint.mjJNT_FREE):
        width = 7
    elif joint_type == int(mujoco.mjtJoint.mjJNT_BALL):
        width = 4
    else:
        width = 1
    return joint_id, int(model.jnt_qposadr[joint_id]), width


def _assign_joint_qpos(
    mujoco: Any,
    model: Any,
    data: Any,
    values: Mapping[str, Any],
) -> None:
    for joint_name, raw_value in values.items():
        if not isinstance(joint_name, str):
            raise ValueError("qpos_by_joint keys must be joint names")
        _, address, width = _joint_info(mujoco, model, joint_name)
        if width == 1 and not isinstance(raw_value, Sequence):
            data.qpos[address] = _finite_float(raw_value, f"qpos {joint_name}")
            continue
        if isinstance(raw_value, (str, bytes)) or not isinstance(raw_value, Sequence):
            raise ValueError(f"qpos {joint_name} must contain {width} values")
        if len(raw_value) != width:
            raise ValueError(f"qpos {joint_name} must contain {width} values")
        data.qpos[address : address + width] = [
            _finite_float(item, f"qpos {joint_name}") for item in raw_value
        ]


def _assign_controls(
    mujoco: Any,
    model: Any,
    data: Any,
    values: Mapping[str, Any],
) -> None:
    for actuator_name, raw_value in values.items():
        if not isinstance(actuator_name, str):
            raise ValueError("ctrl_by_actuator keys must be actuator names")
        actuator_id = int(
            mujoco.mj_name2id(model, _obj(mujoco, "mjOBJ_ACTUATOR"), actuator_name)
        )
        if actuator_id < 0:
            raise ValueError(f"unknown reset actuator {actuator_name!r}")
        data.ctrl[actuator_id] = _finite_float(raw_value, f"ctrl {actuator_name}")


def _assign_mocap(
    mujoco: Any,
    model: Any,
    data: Any,
    values: Mapping[str, Any],
) -> None:
    for body_name, raw_value in values.items():
        if not isinstance(body_name, str):
            raise ValueError("mocap_by_body keys must be body names")
        body_id = int(mujoco.mj_name2id(model, _obj(mujoco, "mjOBJ_BODY"), body_name))
        if body_id < 0:
            raise ValueError(f"unknown reset body {body_name!r}")
        mocap_id = int(model.body_mocapid[body_id])
        if mocap_id < 0:
            raise ValueError(f"body {body_name!r} has no mocap body")
        if isinstance(raw_value, Mapping):
            position = raw_value.get("position", raw_value.get("pos"))
            quaternion = raw_value.get("quaternion", raw_value.get("quat"))
            if position is None or quaternion is None:
                raise ValueError(
                    f"mocap {body_name} requires position and quaternion"
                )
        elif isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
            if len(raw_value) == 3:
                position = raw_value
                quaternion = np.asarray(data.mocap_quat[mocap_id], dtype=float).tolist()
            elif len(raw_value) == 7:
                position, quaternion = raw_value[:3], raw_value[3:]
            else:
                raise ValueError(f"mocap {body_name} must contain 3 or 7 values")
        else:
            raise ValueError(f"mocap {body_name} must be an object or 7-vector")
        if (
            isinstance(position, (str, bytes))
            or not isinstance(position, Sequence)
            or len(position) != 3
            or isinstance(quaternion, (str, bytes))
            or not isinstance(quaternion, Sequence)
            or len(quaternion) != 4
        ):
            raise ValueError(f"mocap {body_name} has invalid position/quaternion")
        data.mocap_pos[mocap_id] = [
            _finite_float(item, f"mocap position {body_name}") for item in position
        ]
        data.mocap_quat[mocap_id] = [
            _finite_float(item, f"mocap quaternion {body_name}")
            for item in quaternion
        ]


def _euler_quaternion(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Return MuJoCo's [w, x, y, z] quaternion for XYZ Euler tilt."""
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return np.array(
        [
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        ],
        dtype=np.float64,
    )


def _apply_tilt(mujoco: Any, model: Any, data: Any, tilt: Mapping[str, Any]) -> None:
    """Apply an optional framework-only G5 tilt after settling."""
    body_name = tilt.get("body_name", tilt.get("base_body_name"))
    if not isinstance(body_name, str) or not body_name:
        raise ValueError("tilt requires body_name")
    roll = _finite_float(tilt.get("roll_rad", tilt.get("roll", 0.0)), "tilt roll")
    pitch = _finite_float(
        tilt.get("pitch_rad", tilt.get("pitch", 0.0)), "tilt pitch"
    )
    yaw = _finite_float(tilt.get("yaw_rad", tilt.get("yaw", 0.0)), "tilt yaw")
    body_id = int(mujoco.mj_name2id(model, _obj(mujoco, "mjOBJ_BODY"), body_name))
    if body_id < 0:
        raise ValueError(f"unknown tilt body {body_name!r}")
    joint_id = int(model.body_jntadr[body_id])
    if joint_id < 0 or int(model.jnt_type[joint_id]) != int(mujoco.mjtJoint.mjJNT_FREE):
        raise ValueError(f"tilt body {body_name!r} does not own a free joint")
    address = int(model.jnt_qposadr[joint_id])
    data.qpos[address + 3 : address + 7] = _euler_quaternion(roll, pitch, yaw)


def _normalise_reset(reset: Any) -> Mapping[str, Any]:
    if reset is None:
        return {}
    if not isinstance(reset, Mapping):
        raise ValueError("case reset must be an object")
    # Direct callers sometimes pass the complete case to this helper.
    known = {
        "keyframe",
        "keyframe_name",
        "qpos_by_joint",
        "ctrl_by_actuator",
        "mocap_by_body",
        "tilt",
        "post_settle",
        "after_settle",
    }
    case_keys = {
        "id",
        "case_id",
        "capability_id",
        "method_name",
        "request",
        "measurement_binding",
        "timing",
    }
    if "reset" in reset and (
        bool(set(reset).intersection(case_keys))
        or not bool(set(reset).intersection(known))
    ):
        nested = reset.get("reset")
        return _normalise_reset(nested)
    return reset


def _apply_reset_values(
    skel: Any,
    reset: Mapping[str, Any],
    *,
    allow_keyframe: bool = True,
    allow_tilt: bool = False,
) -> None:
    model, data, mujoco = _model_data(skel)
    if allow_keyframe:
        key = reset.get("keyframe", reset.get("keyframe_name"))
        if key is None and reset.get("kind") == "keyframe":
            key = reset.get("name")
        if key is not None:
            if isinstance(key, str):
                key_id = int(mujoco.mj_name2id(model, _obj(mujoco, "mjOBJ_KEY"), key))
            else:
                key_id = int(key)
            if key_id < 0 or key_id >= int(model.nkey):
                raise ValueError(f"unknown reset keyframe {key!r}")
            mujoco.mj_resetDataKeyframe(model, data, key_id)
        else:
            mujoco.mj_resetData(model, data)
    if "qpos_by_joint" in reset:
        _assign_joint_qpos(
            mujoco, model, data, _mapping(reset["qpos_by_joint"], "qpos_by_joint")
        )
    if "ctrl_by_actuator" in reset:
        _assign_controls(
            mujoco,
            model,
            data,
            _mapping(reset["ctrl_by_actuator"], "ctrl_by_actuator"),
        )
    if "mocap_by_body" in reset:
        _assign_mocap(
            mujoco, model, data, _mapping(reset["mocap_by_body"], "mocap_by_body")
        )
    if allow_tilt and "tilt" in reset:
        _apply_tilt(mujoco, model, data, _mapping(reset["tilt"], "tilt"))
    mujoco.mj_forward(model, data)


def reset_capability_case(skel: Any, reset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reset the existing skeleton model/data using only framework bindings.

    The helper intentionally reuses the skeleton's existing ``MjData``.  It
    does not load an MJCF or construct a second world, which keeps all traces
    and videos tied to the same canonical simulation object.
    """
    config = _normalise_reset(reset)
    _apply_reset_values(skel, config)
    model, data, _ = _model_data(skel)
    return {
        "model_id": id(model),
        "data_id": id(data),
        "time": float(data.time),
    }


def _first_number(mapping: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return _finite_float(mapping[name], name)
    return None


def _request_duration(request: Mapping[str, Any]) -> float:
    values: list[float] = []
    for key, value in request.items():
        if "duration" in str(key) or "timeout" in str(key):
            try:
                number = _finite_float(value, str(key))
            except (TypeError, ValueError):
                continue
            if number > 0.0:
                values.append(number)
    return max(values, default=1.0)


def _timing(case: Mapping[str, Any], model: Any) -> dict[str, float | int]:
    raw = case.get("timing", {})
    timing = _mapping(raw, "timing")
    dt = _finite_float(model.opt.timestep, "model timestep")
    if dt <= 0.0:
        raise ValueError("model timestep must be positive")
    settle_s = _first_number(timing, ("settle_s", "settle_time_s", "settle_duration_s"))
    if settle_s is None:
        reset_raw = case.get("reset") or {}
        reset_config = _mapping(reset_raw, "reset")
        settle_s = _first_number(reset_config, ("settle_s", "settle_time_s"))
        if settle_s is None and isinstance(reset_config.get("timing"), Mapping):
            settle_s = _first_number(
                reset_config["timing"], ("settle_s", "settle_time_s")
            )
        settle_s = settle_s or 0.0
    settle_steps_raw = timing.get("settle_steps")
    settle_explicit = settle_steps_raw is not None or any(
        key in timing for key in ("settle_s", "settle_time_s", "settle_duration_s")
    )
    if settle_steps_raw is None:
        settle_steps = max(0, int(math.ceil(settle_s / dt - 1.0e-12)))
        if not settle_explicit and settle_steps == 0:
            # Establish a post-reset physical state before the first trusted
            # sample even when an older suite omitted an explicit settle key.
            settle_steps = 1
    else:
        settle_steps = int(settle_steps_raw)
        if settle_steps < 0:
            raise ValueError("settle_steps must be non-negative")
    max_sim = _first_number(
        timing,
        ("max_sim_time_s", "max_action_time_s", "timeout_sim_s", "max_duration_s"),
    )
    if max_sim is None:
        max_sim = _first_number(case, ("timeout_sim_s", "max_sim_time_s"))
    if max_sim is None:
        max_sim = _request_duration(_mapping(case.get("request", {}), "request")) + 1.0
    if max_sim <= 0.0:
        raise ValueError("capability timing max_sim_time_s must be positive")
    max_steps_raw = timing.get("max_steps")
    max_steps = (
        int(max_steps_raw)
        if max_steps_raw is not None
        else int(math.ceil(max_sim / dt)) + 1
    )
    if max_steps <= 0:
        raise ValueError("capability timing max_steps must be positive")
    sample_hz = _first_number(timing, ("sample_hz",)) or (1.0 / dt)
    if sample_hz <= 0.0:
        raise ValueError("sample_hz must be positive")
    video_fps = _first_number(timing, ("video_fps", "fps")) or min(60.0, sample_hz)
    if video_fps <= 0.0:
        raise ValueError("video_fps must be positive")
    capture_every_raw = timing.get("capture_every")
    capture_every = (
        int(capture_every_raw)
        if capture_every_raw is not None
        # The recorder observes every physical step.  ``sample_hz`` is an
        # evidence metadata field and may be lower than the model's physics
        # rate, so it must not determine the video stride.
        else max(1, int(round(1.0 / (dt * video_fps))))
    )
    if capture_every <= 0:
        raise ValueError("capture_every must be positive")
    return {
        "physics_timestep_s": dt,
        "settle_s": float(settle_s),
        "settle_steps": settle_steps,
        "max_sim_time_s": float(max_sim),
        "max_steps": max_steps,
        "sample_hz": float(sample_hz),
        "video_fps": float(video_fps),
        "capture_every": capture_every,
        "video_width": int(timing.get("video_width", 480)),
        "video_height": int(timing.get("video_height", 360)),
        "camera": timing.get("camera", -1),
    }


def _run_settle(skel: Any, steps: int) -> None:
    if steps <= 0:
        return
    model, data, mujoco = _model_data(skel)
    for _ in range(steps):
        mujoco.mj_step(model, data, 1)


_MODEL_PARAMETER_ARRAY_NAMES = (
    # Dynamics and joint parameters.
    "body_mass",
    "body_inertia",
    "body_pos",
    "body_quat",
    "body_ipos",
    "body_iquat",
    "jnt_type",
    "jnt_qposadr",
    "jnt_dofadr",
    "jnt_bodyid",
    "jnt_limited",
    "jnt_range",
    "jnt_margin",
    "jnt_stiffness",
    "jnt_solref",
    "jnt_solimp",
    "jnt_pos",
    "jnt_axis",
    "dof_bodyid",
    "dof_jntid",
    "dof_parentid",
    "dof_armature",
    "dof_damping",
    "dof_frictionloss",
    "dof_solref",
    "dof_solimp",
    # Geometry/site pose and contact parameters.
    "geom_type",
    "geom_contype",
    "geom_conaffinity",
    "geom_condim",
    "geom_priority",
    "geom_solmix",
    "geom_solref",
    "geom_solimp",
    "geom_margin",
    "geom_gap",
    "geom_size",
    "geom_rbound",
    "geom_pos",
    "geom_quat",
    "geom_friction",
    "geom_bodyid",
    "geom_dataid",
    "site_type",
    "site_pos",
    "site_quat",
    "site_size",
    "site_group",
    "site_matid",
    "site_bodyid",
    "site_sameframe",
    # Actuator transmission, range, and force/dynamics parameters.
    "actuator_trntype",
    "actuator_dyntype",
    "actuator_gaintype",
    "actuator_biastype",
    "actuator_trnid",
    "actuator_ctrlrange",
    "actuator_forcerange",
    "actuator_gear",
    "actuator_dynprm",
    "actuator_gainprm",
    "actuator_biasprm",
    "actuator_ctrllimited",
    "actuator_forcelimited",
    "actuator_actlimited",
    "actuator_actrange",
    "actuator_lengthrange",
    # Equality constraints affect the same live physical world.
    "eq_type",
    "eq_obj1",
    "eq_obj2",
    "eq_data",
    "eq_solref",
    "eq_solimp",
    "eq_active0",
    # Reference and spring states are model parameters, not candidate state.
    "qpos0",
    "qpos_spring",
)


class _ModelSnapshot:
    """Snapshot the model fields that can alter the observed dynamics.

    Mesh vertices, texture buffers, and other rendering assets are intentionally
    outside this per-step guard.  The evaluator only needs the mutable
    dynamics, joint, actuator, contact, and geometry-pose fields that can
    change a physical result; scanning all ``MjModel`` arrays made every
    physics step copy large static assets.
    """

    def __init__(self, arrays: dict[str, np.ndarray], options: dict[str, Any]) -> None:
        self.arrays = arrays
        self.options = options

    @classmethod
    def capture(cls, model: Any) -> "_ModelSnapshot":
        arrays: dict[str, np.ndarray] = {}
        for name in _MODEL_PARAMETER_ARRAY_NAMES:
            try:
                value = getattr(model, name)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, np.ndarray):
                arrays[name] = np.array(value, copy=True)
        options: dict[str, Any] = {}
        opt = getattr(model, "opt", None)
        if opt is not None:
            for name in dir(opt):
                if name.startswith("_"):
                    continue
                try:
                    value = getattr(opt, name)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(value, (bool, int, float, np.integer, np.floating)):
                    options[name] = value.item() if isinstance(value, np.generic) else value
        return cls(arrays, options)

    def changed(self, model: Any) -> list[str]:
        changed: list[str] = []
        for name, expected in self.arrays.items():
            try:
                actual = np.asarray(getattr(model, name))
            except Exception:  # noqa: BLE001
                changed.append(name)
                continue
            if actual.shape != expected.shape or not np.array_equal(
                actual, expected, equal_nan=True
            ):
                changed.append(name)
        opt = getattr(model, "opt", None)
        if opt is None:
            return changed
        for name, expected in self.options.items():
            try:
                actual = getattr(opt, name)
                actual = actual.item() if isinstance(actual, np.generic) else actual
            except Exception:  # noqa: BLE001
                changed.append(f"opt.{name}")
                continue
            if actual != expected:
                changed.append(f"opt.{name}")
        return changed


def _array_changed(left: np.ndarray, right: np.ndarray) -> bool:
    return left.shape != right.shape or not np.allclose(
        left, right, rtol=0.0, atol=1.0e-12, equal_nan=True
    )


class _TraceRecorder:
    """Observe every canonical physical step and retain trusted evidence."""

    def __init__(
        self,
        skel: Any,
        *,
        max_steps: int,
        max_sim_time_s: float,
        video: "_VideoRecorder",
    ) -> None:
        self.skel = skel
        self.model, self.data, self.mujoco = _model_data(skel)
        self.max_steps = int(max_steps)
        self.max_sim_time_s = float(max_sim_time_s)
        self.video = video
        self.original_step: Any = None
        self.step_count = 0
        self.initial_time = float(self.data.time)
        self.initial_ctrl = np.array(self.data.ctrl, dtype=np.float64, copy=True)
        self.last_qpos = np.array(self.data.qpos, dtype=np.float64, copy=True)
        self.last_qvel = np.array(self.data.qvel, dtype=np.float64, copy=True)
        self.last_mocap_pos = np.array(self.data.mocap_pos, dtype=np.float64, copy=True)
        self.last_mocap_quat = np.array(self.data.mocap_quat, dtype=np.float64, copy=True)
        self.last_time = float(self.data.time)
        self.model_snapshot = _ModelSnapshot.capture(self.model)
        self.direct_state_write_detected = False
        self.direct_state_write_fields: set[str] = set()
        self.model_parameter_write_detected = False
        self.model_parameter_write_fields: set[str] = set()
        self.foreign_step_detected = False
        self.ctrl_observed_before_step = False
        self.ctrl_changed_from_reset = False
        self.control_range_monitoring_complete = False
        self.control_range_violation_detected = False
        self.control_range_violation_actuators: set[str] = set()
        self.actuator_force_nonzero_step_count = 0
        self.contact_monitoring_complete = False
        self.contact_pair_step_counts: dict[tuple[str, str], int] = {}
        self.contact_pair_first_times_s: dict[tuple[str, str], float] = {}
        self.contact_pair_min_distances: dict[tuple[str, str], float] = {}
        self.minimum_contact_distance_m: float | None = None
        self.samples: list[dict[str, Any]] = []
        self.initial_joint_positions: dict[str, float] = {}
        self.initial_site_positions: dict[str, np.ndarray] = {}
        self.initial_body_positions: dict[str, np.ndarray] = {}
        self.joint_max_abs_deviation_from_reset: dict[str, float] = {}
        self.site_max_displacement_from_reset: dict[str, float] = {}
        self.body_max_displacement_from_reset: dict[str, float] = {}
        self._setup_reference_state()

    def _setup_reference_state(self) -> None:
        joint_obj = _obj(self.mujoco, "mjOBJ_JOINT")
        site_obj = _obj(self.mujoco, "mjOBJ_SITE")
        body_obj = _obj(self.mujoco, "mjOBJ_BODY")
        hinge = int(self.mujoco.mjtJoint.mjJNT_HINGE)
        slide = int(self.mujoco.mjtJoint.mjJNT_SLIDE)
        for index in range(int(self.model.njnt)):
            if int(self.model.jnt_type[index]) not in {hinge, slide}:
                continue
            name = _name(self.mujoco, self.model, joint_obj, index)
            address = int(self.model.jnt_qposadr[index])
            self.initial_joint_positions[name] = float(self.data.qpos[address])
            self.joint_max_abs_deviation_from_reset[name] = 0.0
        for index in range(int(self.model.nsite)):
            name = _name(self.mujoco, self.model, site_obj, index)
            value = np.asarray(self.data.site_xpos[index], dtype=np.float64).copy()
            self.initial_site_positions[name] = value
            self.site_max_displacement_from_reset[name] = 0.0
        for index in range(int(self.model.nbody)):
            name = _name(self.mujoco, self.model, body_obj, index)
            value = np.asarray(self.data.xpos[index], dtype=np.float64).copy()
            self.initial_body_positions[name] = value
            self.body_max_displacement_from_reset[name] = 0.0
        self.samples.append(self.snapshot())
        self.video.capture(force=True, step_count=0)

    def install(self) -> None:
        if self.original_step is not None:
            raise RuntimeError("capability trace is already installed")
        self.original_step = self.mujoco.mj_step

        def tracked_step(model: Any, data: Any, nstep: int = 1) -> None:
            if model is not self.model or data is not self.data:
                self.foreign_step_detected = True
                raise RuntimeError("candidate attempted to step a non-canonical world")
            steps = int(nstep)
            if steps <= 0:
                raise ValueError("mj_step nstep must be positive")
            for _ in range(steps):
                self._before_step()
                assert self.original_step is not None
                self.original_step(self.model, self.data, 1)
                self._after_step()

        self.mujoco.mj_step = tracked_step

    def uninstall(self) -> None:
        if self.original_step is not None:
            self.mujoco.mj_step = self.original_step
            self.original_step = None

    def _mark_unstepped_state_change(self) -> None:
        checks = (
            ("qpos", np.asarray(self.data.qpos), self.last_qpos),
            ("qvel", np.asarray(self.data.qvel), self.last_qvel),
            ("mocap_pos", np.asarray(self.data.mocap_pos), self.last_mocap_pos),
            ("mocap_quat", np.asarray(self.data.mocap_quat), self.last_mocap_quat),
        )
        for name, actual, expected in checks:
            if _array_changed(actual, expected):
                self.direct_state_write_detected = True
                self.direct_state_write_fields.add(name)
        if not math.isclose(float(self.data.time), self.last_time, rel_tol=0.0, abs_tol=1.0e-12):
            self.direct_state_write_detected = True
            self.direct_state_write_fields.add("time")

    def _check_model(self) -> None:
        changed = self.model_snapshot.changed(self.model)
        if changed:
            self.model_parameter_write_detected = True
            self.model_parameter_write_fields.update(changed)

    def _check_controls(self) -> None:
        self.ctrl_observed_before_step = True
        ctrl = np.asarray(self.data.ctrl, dtype=np.float64)
        if not np.allclose(ctrl, self.initial_ctrl, rtol=0.0, atol=1.0e-12):
            self.ctrl_changed_from_reset = True
        self.control_range_monitoring_complete = True
        if not np.all(np.isfinite(ctrl)):
            self.control_range_violation_detected = True
        for index in range(int(self.model.nu)):
            value = float(ctrl[index])
            invalid = not math.isfinite(value)
            if not invalid and bool(self.model.actuator_ctrllimited[index]):
                lo, hi = map(float, self.model.actuator_ctrlrange[index])
                invalid = value < lo - 1.0e-12 or value > hi + 1.0e-12
            if invalid:
                self.control_range_violation_detected = True
                self.control_range_violation_actuators.add(
                    _name(self.mujoco, self.model, _obj(self.mujoco, "mjOBJ_ACTUATOR"), index)
                )

    def _before_step(self) -> None:
        if self.step_count >= self.max_steps:
            raise RuntimeError("capability case exceeded max_steps")
        if float(self.data.time) - self.initial_time >= self.max_sim_time_s - 1.0e-12:
            raise RuntimeError("capability case exceeded max_sim_time_s")
        self._mark_unstepped_state_change()
        self._check_model()
        self._check_controls()

    def _contacts_after_step(self) -> None:
        geom_obj = _obj(self.mujoco, "mjOBJ_GEOM")
        pairs: set[tuple[str, str]] = set()
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            first = _name(self.mujoco, self.model, geom_obj, int(contact.geom1))
            second = _name(self.mujoco, self.model, geom_obj, int(contact.geom2))
            pair = tuple(sorted((first, second)))
            pairs.add(pair)
            now = float(self.data.time)
            self.contact_pair_first_times_s.setdefault(pair, now)
            distance = float(contact.dist)
            previous = self.contact_pair_min_distances.get(pair)
            if previous is None or distance < previous:
                self.contact_pair_min_distances[pair] = distance
            if self.minimum_contact_distance_m is None or distance < self.minimum_contact_distance_m:
                self.minimum_contact_distance_m = distance
        for pair in pairs:
            self.contact_pair_step_counts[pair] = self.contact_pair_step_counts.get(pair, 0) + 1
        self.contact_monitoring_complete = True

    def _update_extrema(self) -> None:
        for name, address in (
            (
                name,
                int(self.model.jnt_qposadr[index]),
            )
            for index in range(int(self.model.njnt))
            for name in [_name(self.mujoco, self.model, _obj(self.mujoco, "mjOBJ_JOINT"), index)]
            if int(self.model.jnt_type[index]) in {
                int(self.mujoco.mjtJoint.mjJNT_HINGE),
                int(self.mujoco.mjtJoint.mjJNT_SLIDE),
            }
        ):
            if name in self.initial_joint_positions:
                deviation = abs(float(self.data.qpos[address]) - self.initial_joint_positions[name])
                self.joint_max_abs_deviation_from_reset[name] = max(
                    self.joint_max_abs_deviation_from_reset[name], deviation
                )
        for index in range(int(self.model.nsite)):
            name = _name(self.mujoco, self.model, _obj(self.mujoco, "mjOBJ_SITE"), index)
            if name in self.initial_site_positions:
                displacement = float(
                    np.linalg.norm(np.asarray(self.data.site_xpos[index]) - self.initial_site_positions[name])
                )
                self.site_max_displacement_from_reset[name] = max(
                    self.site_max_displacement_from_reset[name], displacement
                )
        for index in range(int(self.model.nbody)):
            name = _name(self.mujoco, self.model, _obj(self.mujoco, "mjOBJ_BODY"), index)
            if name in self.initial_body_positions:
                displacement = float(
                    np.linalg.norm(np.asarray(self.data.xpos[index]) - self.initial_body_positions[name])
                )
                self.body_max_displacement_from_reset[name] = max(
                    self.body_max_displacement_from_reset[name], displacement
                )

    def _after_step(self) -> None:
        self.step_count += 1
        forces = np.asarray(self.data.actuator_force, dtype=np.float64)
        if np.all(np.isfinite(forces)) and np.any(np.abs(forces) > 1.0e-12):
            self.actuator_force_nonzero_step_count += 1
        # mj_step advances qpos/time after computing derived poses and contacts.
        # Refresh those fields so sampling and video describe the recorded time.
        self.mujoco.mj_forward(self.model, self.data)
        self._update_extrema()
        self._contacts_after_step()
        self.samples.append(self.snapshot())
        self.video.capture(force=False, step_count=self.step_count)
        self.last_qpos = np.array(self.data.qpos, dtype=np.float64, copy=True)
        self.last_qvel = np.array(self.data.qvel, dtype=np.float64, copy=True)
        self.last_mocap_pos = np.array(self.data.mocap_pos, dtype=np.float64, copy=True)
        self.last_mocap_quat = np.array(self.data.mocap_quat, dtype=np.float64, copy=True)
        self.last_time = float(self.data.time)
        self._check_model()
        if float(self.data.time) - self.initial_time > self.max_sim_time_s + 1.0e-12:
            raise RuntimeError("capability case exceeded max_sim_time_s")

    def finish(self) -> None:
        self._mark_unstepped_state_change()
        self._check_model()
        self._check_controls()
        if not self.samples or self.samples[-1]["time"] != float(self.data.time):
            self.samples.append(self.snapshot())
        self.video.capture(force=True, step_count=self.step_count)

    def snapshot(self) -> dict[str, Any]:
        body_obj = _obj(self.mujoco, "mjOBJ_BODY")
        site_obj = _obj(self.mujoco, "mjOBJ_SITE")
        joint_obj = _obj(self.mujoco, "mjOBJ_JOINT")
        geom_obj = _obj(self.mujoco, "mjOBJ_GEOM")
        body_positions = {
            _name(self.mujoco, self.model, body_obj, index): np.asarray(
                self.data.xpos[index], dtype=np.float64
            ).tolist()
            for index in range(int(self.model.nbody))
        }
        body_quaternions = {
            _name(self.mujoco, self.model, body_obj, index): np.asarray(
                self.data.xquat[index], dtype=np.float64
            ).tolist()
            for index in range(int(self.model.nbody))
        }
        site_positions = {
            _name(self.mujoco, self.model, site_obj, index): np.asarray(
                self.data.site_xpos[index], dtype=np.float64
            ).tolist()
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
            name = _name(self.mujoco, self.model, joint_obj, index)
            joint_positions[name] = float(self.data.qpos[int(self.model.jnt_qposadr[index])])
            joint_velocities[name] = float(self.data.qvel[int(self.model.jnt_dofadr[index])])
        contacts: list[dict[str, Any]] = []
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            contacts.append(
                {
                    "geom1": _name(self.mujoco, self.model, geom_obj, int(contact.geom1)),
                    "geom2": _name(self.mujoco, self.model, geom_obj, int(contact.geom2)),
                    "distance": float(contact.dist),
                }
            )
        return {
            "time": float(self.data.time),
            "qpos": np.asarray(self.data.qpos, dtype=np.float64).tolist(),
            "qvel": np.asarray(self.data.qvel, dtype=np.float64).tolist(),
            "ctrl": np.asarray(self.data.ctrl, dtype=np.float64).tolist(),
            "body_positions": body_positions,
            "body_quaternions": body_quaternions,
            "site_positions": site_positions,
            "joint_positions": joint_positions,
            "joint_velocities": joint_velocities,
            "contacts": contacts,
        }

    def evidence(self) -> dict[str, Any]:
        return {
            "canonical_model_data": self.step_count > 0 and not self.foreign_step_detected,
            "step_count": self.step_count,
            "physics_timestep_s": float(self.model.opt.timestep),
            "ctrl_observed_before_step": self.ctrl_observed_before_step,
            "ctrl_changed_from_reset": self.ctrl_changed_from_reset,
            "actuator_force_nonzero_step_count": self.actuator_force_nonzero_step_count,
            "control_range_monitoring_complete": self.control_range_monitoring_complete,
            "control_range_violation_detected": self.control_range_violation_detected,
            "control_range_violation_actuators": sorted(self.control_range_violation_actuators),
            "direct_state_write_detected": self.direct_state_write_detected,
            "direct_state_write_fields": sorted(self.direct_state_write_fields),
            "model_parameter_write_detected": self.model_parameter_write_detected,
            "model_parameter_write_fields": sorted(self.model_parameter_write_fields),
            "contact_monitoring_complete": self.contact_monitoring_complete,
            "contact_pair_step_counts": [
                {"geom1": pair[0], "geom2": pair[1], "step_count": count}
                for pair, count in sorted(self.contact_pair_step_counts.items())
            ],
            "contact_pair_first_times_s": [
                {"geom1": pair[0], "geom2": pair[1], "first_time_s": value}
                for pair, value in sorted(self.contact_pair_first_times_s.items())
            ],
            "contact_pair_min_distances": [
                {
                    "geom1": pair[0],
                    "geom2": pair[1],
                    "minimum_distance_m": value,
                }
                for pair, value in sorted(self.contact_pair_min_distances.items())
            ],
            "minimum_contact_distance_m": self.minimum_contact_distance_m,
            "joint_max_abs_deviation_from_reset": dict(
                sorted(self.joint_max_abs_deviation_from_reset.items())
            ),
            "site_max_displacement_from_reset": dict(
                sorted(self.site_max_displacement_from_reset.items())
            ),
            "body_max_displacement_from_reset": dict(
                sorted(self.body_max_displacement_from_reset.items())
            ),
            "samples": self.samples,
        }


class _VideoRecorder:
    """Render frames immediately from the same model/data sampled by a trace."""

    def __init__(
        self,
        skel: Any,
        output_path: Path,
        *,
        capture_every: int,
        fps: float,
        width: int = 480,
        height: int = 360,
        camera: Any = -1,
    ) -> None:
        self.skel = skel
        self.model, self.data, self.mujoco = _model_data(skel)
        self.output_path = output_path
        self.capture_every = max(1, int(capture_every))
        self.fps = float(fps)
        self.camera_name = camera
        self.frames: list[np.ndarray] = []
        self.last_capture_step: int | None = None
        self.errors: list[str] = []
        self.renderer: Any = None
        self.camera: Any = None
        try:
            self.renderer = self.mujoco.Renderer(
                self.model, height=int(height), width=int(width)
            )
            self.camera = self.mujoco.MjvCamera()
            try:
                self.mujoco.mjv_defaultFreeCamera(self.model, self.camera)
            except Exception:  # noqa: BLE001
                self.camera.type = self.mujoco.mjtCamera.mjCAMERA_FREE
            self.camera.type = self.mujoco.mjtCamera.mjCAMERA_FREE
            try:
                self.camera.lookat[:] = np.asarray(self.model.stat.center, dtype=float)
                self.camera.distance = float(max(self.model.stat.extent, 0.3)) * 1.8
            except Exception:  # noqa: BLE001
                self.camera.distance = 3.0
        except Exception as exc:  # noqa: BLE001
            self.renderer = None
            self.errors.append(f"renderer initialization failed: {exc}")

    def capture(self, *, force: bool, step_count: int) -> None:
        if self.renderer is None:
            # A candidate's render() cannot replace the trusted world's video.
            # The initialization error is retained once and makes finish fail.
            return
        if not force and step_count % self.capture_every:
            return
        # A final forced capture must not duplicate the frame already captured
        # at the same physical step.  Keeping one frame per sampled step keeps
        # the encoded video duration aligned with the simulation clock.
        if force and self.last_capture_step == step_count:
            return
        try:
            if self.renderer is not None:
                selected_camera: Any = self.camera
                if isinstance(self.camera_name, str) and self.camera_name:
                    camera_id = int(
                        self.mujoco.mj_name2id(
                            self.model,
                            _obj(self.mujoco, "mjOBJ_CAMERA"),
                            self.camera_name,
                        )
                    )
                    if camera_id < 0:
                        raise RuntimeError(f"unknown evidence camera {self.camera_name!r}")
                    selected_camera = self.camera_name
                self.renderer.update_scene(self.data, camera=selected_camera)
                frame = np.asarray(self.renderer.render(), dtype=np.uint8).copy()
            if frame.ndim != 3 or frame.shape[2] not in {3, 4}:
                raise RuntimeError(f"rendered frame has invalid shape {frame.shape}")
            self.frames.append(frame[:, :, :3])
            self.last_capture_step = step_count
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"frame capture failed: {exc}")

    def finish(self) -> dict[str, Any]:
        if self.renderer is not None:
            try:
                self.renderer.close()
            except Exception:  # noqa: BLE001
                pass
        info: dict[str, Any] = {
            "path": str(self.output_path),
            "frame_count": len(self.frames),
            "ok": False,
            "errors": list(self.errors),
        }
        if not self.frames:
            info["errors"].append("no video frames were captured")
            return info
        try:
            import imageio.v2 as imageio  # noqa: PLC0415

            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            imageio.mimsave(
                str(self.output_path),
                self.frames,
                format="FFMPEG",
                fps=self.fps,
                codec="libx264",
                pixelformat="yuv420p",
                ffmpeg_params=["-movflags", "+faststart"],
            )
            reader = imageio.get_reader(str(self.output_path))
            try:
                decoded = reader.get_data(0)
                if decoded.ndim != 3 or decoded.size == 0:
                    raise ValueError("video first frame is unusable")
            finally:
                reader.close()
            info["ok"] = (
                self.output_path.is_file()
                and self.output_path.stat().st_size > 0
                and not info["errors"]
            )
            if not info["ok"]:
                info["errors"].append("video writer produced no file")
        except Exception as exc:  # noqa: BLE001
            info["errors"].append(f"video write failed: {exc}")
        return info


def _binding_parameters(case: Mapping[str, Any], model: Any, mujoco: Any) -> dict[str, Any]:
    raw = _mapping(case.get("measurement_binding"), "measurement_binding")
    nested = raw.get("parameters")
    if isinstance(nested, Mapping):
        parameters = dict(nested)
        for key, value in raw.items():
            if key not in {"parameters", "metric", "unit", "kind"}:
                parameters.setdefault(key, value)
    else:
        parameters = dict(raw)
    capability_id = case.get("capability_id")
    if "contract_id" not in parameters and isinstance(capability_id, str):
        parameters["contract_id"] = capability_id

    # A4 bindings may name tool bodies instead of enumerating every geometry.
    # Resolve the complete body subtree from the real model so a contact with a
    # child finger cannot disappear because of an incomplete hand-written list.
    if parameters.get("contract_id") == "A3":
        # The public capability profile uses the explicit gripper symbol;
        # the trusted metric function uses the generic aperture symbol.
        if "joint_name" not in parameters and "gripper_joint_name" in parameters:
            parameters["joint_name"] = parameters["gripper_joint_name"]
    if parameters.get("contract_id") == "A4" and "tool_body_names" in parameters:
        body_names = parameters["tool_body_names"]
        if not isinstance(body_names, Sequence) or isinstance(body_names, (str, bytes)):
            raise ValueError("A4 tool_body_names must be an array")
        body_obj = _obj(mujoco, "mjOBJ_BODY")
        body_ids = {
            int(mujoco.mj_name2id(model, body_obj, str(name))) for name in body_names
        }
        if any(value < 0 for value in body_ids):
            raise ValueError("A4 tool_body_names contains an unknown body")
        descendants = set(body_ids)
        changed = True
        while changed:
            changed = False
            for body_id in range(int(model.nbody)):
                parent = int(model.body_parentid[body_id])
                if parent in descendants and body_id not in descendants:
                    descendants.add(body_id)
                    changed = True
        geom_obj = _obj(mujoco, "mjOBJ_GEOM")
        derived = {
            _name(mujoco, model, geom_obj, geom_id)
            for geom_id in range(int(model.ngeom))
            if int(model.geom_bodyid[geom_id]) in descendants
        }
        existing = parameters.get("tool_geom_names", [])
        if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes)):
            derived.update(str(value) for value in existing)
        parameters["tool_geom_names"] = sorted(derived)
    return parameters


def _is_kuka(case: Mapping[str, Any], robot_definition: Mapping[str, Any] | None) -> bool:
    values = [case.get("robot_configuration_id"), case.get("robot_id")]
    if robot_definition is not None:
        values.extend([robot_definition.get("id"), robot_definition.get("name")])
    return any(isinstance(value, str) and "kuka" in value.lower() for value in values)


def _guard_errors(
    case: Mapping[str, Any],
    evidence: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    robot_definition: Mapping[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    if int(evidence.get("step_count", 0)) <= 0:
        errors.append("no real mujoco.mj_step was observed")
    if evidence.get("canonical_model_data") is not True:
        errors.append("canonical model/data step guard failed")
    if evidence.get("ctrl_observed_before_step") is not True:
        errors.append("control was not observed before a physical step")
    if evidence.get("control_range_monitoring_complete") is not True:
        errors.append("control-range monitoring was incomplete")
    if evidence.get("control_range_violation_detected") is True:
        errors.append("actuator control left its finite model range")
    if evidence.get("direct_state_write_detected") is True:
        fields = ", ".join(evidence.get("direct_state_write_fields", []))
        errors.append(f"candidate wrote live state before/after a step: {fields}")
    if evidence.get("model_parameter_write_detected") is True:
        fields = ", ".join(evidence.get("model_parameter_write_fields", []))
        errors.append(f"candidate modified model parameters: {fields}")
    if evidence.get("ctrl_changed_from_reset") is not True and int(
        evidence.get("actuator_force_nonzero_step_count", 0)
    ) <= 0:
        errors.append("no observed control effect (changed ctrl or non-zero actuator force)")
    capability_id = str(case.get("capability_id", parameters.get("contract_id", "")))
    if capability_id == "G5" and int(evidence.get("actuator_force_nonzero_step_count", 0)) <= 0:
        errors.append("G5 requires non-zero actuator force on a real step")
    if parameters.get("side_effect_guard_profile") == "fixed_arm":
        names = parameters.get("guarded_joint_names")
        if (
            (not isinstance(names, Sequence) or isinstance(names, (str, bytes)) or not names)
            and not _is_kuka(case, robot_definition)
        ):
            errors.append("fixed_arm requires non-empty guarded_joint_names")
    if capability_id in {"A4", "G5"} and evidence.get("contact_monitoring_complete") is not True:
        errors.append(f"{capability_id} contact monitoring was incomplete")
    if capability_id == "A4":
        minimum = evidence.get("minimum_contact_distance_m")
        if minimum is not None and float(minimum) < -0.005 - 1.0e-12:
            errors.append("A4 contact penetration exceeded -0.005 m")
    if capability_id == "A1":
        request = case.get("request")
        site_name = parameters.get("site_name")
        if isinstance(request, Mapping) and isinstance(site_name, str):
            target = request.get("target_position_m")
            samples = evidence.get("samples")
            if (
                isinstance(target, Sequence)
                and not isinstance(target, (str, bytes))
                and len(target) == 3
                and isinstance(samples, list)
                and samples
                and isinstance(samples[0], Mapping)
            ):
                positions = samples[0].get("site_positions", {})
                if isinstance(positions, Mapping) and site_name in positions:
                    initial = np.asarray(positions[site_name], dtype=float)
                    target_array = np.asarray(target, dtype=float)
                    requested = float(np.linalg.norm(target_array - initial))
                    maximum = float(
                        max(
                            np.linalg.norm(
                                np.asarray(sample.get("site_positions", {}).get(site_name), dtype=float)
                                - initial
                            )
                            for sample in samples
                            if isinstance(sample.get("site_positions"), Mapping)
                            and site_name in sample.get("site_positions", {})
                        )
                    )
                    if requested > 1.0e-3 and maximum < min(1.0e-4, requested * 0.1):
                        errors.append("A1 target request produced no measurable real site motion")
    return errors


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _return_metadata(value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return {"type": type(value).__name__, "value": value}
    return {"type": type(value).__name__}


def _model_generated_value(
    driver_origin: str | None,
    *,
    reference: bool,
) -> bool | None:
    """Label only an explicitly identified real model-generation path."""
    if reference:
        return False
    if driver_origin == "real_model_generation":
        return True
    return None


def run_capability_case(
    skel: Any,
    case: Mapping[str, Any],
    output_dir: str | Path,
    *,
    robot_definition: Mapping[str, Any] | None = None,
    from_scratch: bool = False,
    execute: Callable[[], Any] | None = None,
    driver_origin: str | None = None,
    reference: bool = False,
) -> dict[str, Any]:
    """Reset, execute, trace, render, and score one capability case."""
    case_id = _case_id(case)
    output_root = Path(output_dir)
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    trace_path = case_dir / "trace.json"
    video_path = case_dir / "video.mp4"
    resolved_origin = driver_origin or (
        "reference" if reference else ("from_scratch" if from_scratch else "provided")
    )
    model_generated = _model_generated_value(driver_origin, reference=reference)
    result: dict[str, Any] = {
        "id": case_id,
        "case_id": case_id,
        "test": case_id,
        "capability_id": case.get("capability_id"),
        "method_name": case.get("method_name"),
        "ok": False,
        "score": 0.0,
        "metric": 0.0,
        "errors": [],
        "trace_path": str(trace_path),
        "video_path": str(video_path),
        "sim_elapsed_s": 0.0,
        "physics_steps": 0,
        "from_scratch": bool(from_scratch),
        "driver_origin": resolved_origin,
        "reference": bool(reference),
        "model_generated": model_generated,
        "n_frames": 0,
        "error": None,
        "detail": "",
    }
    trace: _TraceRecorder | None = None
    video: _VideoRecorder | None = None
    action_return: dict[str, Any] | None = None
    try:
        model, data, mujoco = _model_data(skel)
        request = _mapping(case.get("request", {}), "request")
        timing = _timing(case, model)
        reset = _normalise_reset(case.get("reset"))
        reset_capability_case(skel, reset)
        _run_settle(skel, int(timing["settle_steps"]))
        # G5 perturbations are framework operations after the stable settle.
        post_settle = reset.get("post_settle", reset.get("after_settle"))
        if post_settle is None and str(case.get("capability_id")) == "G5" and "tilt" in reset:
            post_settle = {"tilt": reset["tilt"]}
        if post_settle is not None:
            _apply_reset_values(
                skel,
                _normalise_reset(post_settle),
                allow_keyframe=False,
                allow_tilt=True,
            )
        # The baseline is deliberately taken after settle and any framework
        # perturbation; candidate state writes are measured from this point.
        mujoco.mj_forward(model, data)
        binding = _binding_parameters(case, model, mujoco)
        video = _VideoRecorder(
            skel,
            video_path,
            capture_every=int(timing["capture_every"]),
            fps=float(timing["video_fps"]),
            width=int(timing["video_width"]),
            height=int(timing["video_height"]),
            camera=timing["camera"],
        )
        trace = _TraceRecorder(
            skel,
            max_steps=int(timing["max_steps"]),
            max_sim_time_s=float(timing["max_sim_time_s"]),
            video=video,
        )
        trace.install()
        try:
            if execute is not None:
                returned = execute()
            else:
                method_name = case.get("method_name")
                if not isinstance(method_name, str) or not method_name:
                    raise ValueError("capability case requires method_name")
                method = getattr(skel, method_name, None)
                if not callable(method):
                    raise AttributeError(f"skeleton has no callable {method_name!r}")
                returned = method(request=request)
            action_return = _return_metadata(returned)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"capability execution failed: {exc}")
        finally:
            try:
                trace.finish()
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"trace finalization failed: {exc}")
            trace.uninstall()
        evidence = trace.evidence()
        result["physics_steps"] = int(evidence["step_count"])
        result["sim_elapsed_s"] = float(data.time) - float(trace.initial_time)
        result["evidence_summary"] = {
            key: value
            for key, value in evidence.items()
            if key != "samples"
        }
        result["action_return"] = action_return
        video_info = video.finish()
        result["video"] = video_info
        result["n_frames"] = int(video_info.get("frame_count", 0))
        if not video_info.get("ok"):
            result["errors"].extend(str(item) for item in video_info.get("errors", []))
        trace_payload = {
            "artifact_type": "capability_physics_trace",
            "schema_version": "1.0",
            "case_id": case_id,
            "capability_id": case.get("capability_id"),
            "driver_origin": result["driver_origin"],
            "from_scratch": bool(from_scratch),
            "reference": bool(reference),
            "model_generated": model_generated,
            "evidence": evidence,
        }
        _write_json(trace_path, trace_payload)
        parameters = binding
        try:
            from autoadapter_bench import capability_metrics  # noqa: PLC0415

            score = capability_metrics.evaluate_b1_contract(
                parameters=parameters,
                evidence=evidence,
                request=request,
            )
            result["score"] = float(score)
            if result["score"] != 1.0:
                result["errors"].append(
                    f"trusted metric contract did not pass (score={result['score']:.3g})"
                )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"trusted metric evaluation failed: {exc}")
        guard_errors = _guard_errors(
            case,
            evidence,
            parameters,
            robot_definition=robot_definition,
        )
        result["guard_errors"] = guard_errors
        result["errors"].extend(guard_errors)
        result["detail"] = (
            "; ".join(result["errors"])
            if result["errors"]
            else f"{parameters.get('contract_id')} trusted score={result['score']:.1f}"
        )
        result["metrics"] = {
            "contract_id": parameters.get("contract_id"),
            "score": result["score"],
            "parameters": parameters,
        }
        try:
            measurements = capability_metrics.describe_contract_measurements(
                parameters, evidence=evidence, request=request)
            result["metrics"]["measurements"] = measurements
        except Exception as exc:
            result["metrics"]["measurement_summary_error"] = str(exc)
        result["metric"] = result["score"]
        _write_json(case_dir / "metrics.json", result["metrics"])
        result["ok"] = (
            not result["errors"]
            and result["score"] == 1.0
            and bool(video_info.get("ok"))
            and trace_path.is_file()
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
        if trace is not None:
            try:
                trace.uninstall()
            except Exception:  # noqa: BLE001
                pass
        if video is not None:
            try:
                video_info = video.finish()
                result["video"] = video_info
                result["n_frames"] = int(video_info.get("frame_count", 0))
            except Exception as video_exc:  # noqa: BLE001
                result["errors"].append(f"video finalization failed: {video_exc}")
    finally:
        result["error"] = result["errors"][0] if result["errors"] else None
        if result["errors"]:
            result["detail"] = "; ".join(result["errors"])
            measurements = result.get("metrics", {}).get("measurements")
            if measurements:
                result["detail"] += "; observed=" + json.dumps(measurements, sort_keys=True)
        try:
            _write_json(case_dir / "result.json", result)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"result write failed: {exc}")
    return result


def _load_suite(robot_definition: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    profile = robot_definition.get("capability_profile")
    if not isinstance(profile, str) or not profile:
        raise ValueError("robot definition has no capability_profile")
    suite_path = (REPO_ROOT / profile / "capability_validation_suite.json").resolve()
    if not suite_path.is_file():
        raise FileNotFoundError(f"capability validation suite not found: {suite_path}")
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("capability validation suite must be an object")
    suite_robot = payload.get("robot_configuration_id")
    if (
        isinstance(suite_robot, str)
        and isinstance(robot_definition.get("id"), str)
        and suite_robot != robot_definition["id"]
    ):
        raise ValueError("capability validation suite does not match robot definition")
    cases = payload.get("cases", payload.get("tests"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("capability validation suite requires non-empty cases")
    return suite_path, payload


def run_capability_suite(
    skel: Any,
    robot_definition: Mapping[str, Any],
    output_dir: str | Path,
    case_ids: Sequence[str] | None = None,
    from_scratch: bool = False,
    *,
    driver_origin: str | None = None,
    reference: bool = False,
) -> dict[str, Any]:
    """Run selected cases in order, resetting the same model/data each time."""
    robot = _mapping(robot_definition, "robot_definition")
    suite_path, suite = _load_suite(robot)
    raw_cases = suite.get("cases", suite.get("tests"))
    assert isinstance(raw_cases, list)
    selected = list(case_ids) if case_ids is not None else None
    selected_set = set(selected) if selected is not None else None
    if selected is not None and len(selected_set) != len(selected):
        raise ValueError("case_ids must be unique")
    cases: list[Mapping[str, Any]] = []
    all_ids: set[str] = set()
    for raw_case in raw_cases:
        case = _mapping(raw_case, "case")
        identifier = _case_id(case)
        if identifier in all_ids:
            raise ValueError(f"duplicate capability case id {identifier!r}")
        all_ids.add(identifier)
        if selected_set is None or identifier in selected_set:
            cases.append(case)
    if selected_set is not None:
        missing = selected_set - all_ids
        if missing:
            raise ValueError(f"unknown capability case ids: {sorted(missing)}")
    if not cases:
        raise ValueError("no capability cases selected")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    tests = [
        run_capability_case(
            skel,
            case,
            root,
            robot_definition=robot,
            from_scratch=from_scratch,
            driver_origin=driver_origin,
            reference=reference,
        )
        for case in cases
    ]
    result: dict[str, Any] = {
        "artifact_type": "capability_validation_result",
        "schema_version": "1.0",
        "suite_path": str(suite_path),
        "robot_id": robot.get("id"),
        "from_scratch": bool(from_scratch),
        "driver_origin": driver_origin
        or ("reference" if reference else ("from_scratch" if from_scratch else "provided")),
        "reference": bool(reference),
        "model_generated": _model_generated_value(driver_origin, reference=reference),
        "output_dir": str(root),
        "tests": tests,
        "all_ok": all(bool(item.get("ok")) for item in tests),
    }
    _write_json(root / "suite_result.json", result)
    return result
