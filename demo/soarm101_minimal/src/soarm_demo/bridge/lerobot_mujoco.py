"""LeRobot SO-101 API-compatible MuJoCo runtime.

This is deliberately an API-level shim.  It does not emulate the Feetech
serial transport and is fixed framework infrastructure, not generated code.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..audit import JsonlTrace


ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)
GRIPPER = "gripper"
ALL_MOTORS = (*ARM_JOINTS, GRIPPER)


class RobotNotConnectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionReceipt:
    requested: dict[str, float]
    accepted: dict[str, float]
    clipped: dict[str, bool]
    simulation_time: float


class SO101MujocoRobot:
    """Match LeRobot v0.6.0 SO101Follower's public control semantics.

    Arm positions use degrees when ``use_degrees`` is true.  The gripper uses
    LeRobot's normalized 0..100 range.  ``send_action`` only writes targets and
    returns the accepted action; it does not wait until the robot reaches them.
    """

    name = "so101_mujoco_robot"

    def __init__(
        self,
        model_path: str | Path,
        *,
        use_degrees: bool = True,
        max_relative_target: float | Mapping[str, float] | None = None,
        simulation_hz: float | None = None,
        auto_step: bool = True,
        trace_path: str | Path | None = None,
        _precompiled_model: Any | None = None,
    ) -> None:
        try:
            import mujoco
            import numpy as np
        except ImportError as exc:  # pragma: no cover - depends on optional runtime
            raise RuntimeError("mujoco and numpy are required for SO101MujocoRobot") from exc

        self._mj = mujoco
        self._np = np
        self.model_path = Path(model_path).resolve()
        if _precompiled_model is None:
            self.model = mujoco.MjModel.from_xml_path(str(self.model_path))
        else:
            if not isinstance(_precompiled_model, mujoco.MjModel):
                raise TypeError("_precompiled_model must be a mujoco.MjModel")
            self.model = _precompiled_model
        self.data = mujoco.MjData(self.model)
        self.use_degrees = bool(use_degrees)
        self.max_relative_target = max_relative_target
        self.physics_hz = 1.0 / float(self.model.opt.timestep)
        self.simulation_hz = self.physics_hz if simulation_hz is None else float(simulation_hz)
        if self.simulation_hz <= 0:
            raise ValueError("simulation_hz must be positive")
        if not math.isclose(self.simulation_hz, self.physics_hz, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                "simulation_hz must match the pinned MJCF timestep: "
                f"requested={self.simulation_hz:g}, physics_hz={self.physics_hz:g}"
            )
        self.auto_step = bool(auto_step)
        self._connected = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._trace = JsonlTrace(trace_path) if trace_path else None
        self._joint_ids = {name: self._name_id(mujoco.mjtObj.mjOBJ_JOINT, name) for name in ALL_MOTORS}
        self._actuator_ids = {
            name: self._name_id(mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ALL_MOTORS
        }
        self._initial_qpos = self.data.qpos.copy()
        self._initial_qvel = self.data.qvel.copy()
        self._last_receipt: ActionReceipt | None = None

    def _name_id(self, object_type: Any, name: str) -> int:
        identifier = int(self._mj.mj_name2id(self.model, object_type, name))
        if identifier < 0:
            raise ValueError(f"MuJoCo model is missing required {name!r}")
        return identifier

    @property
    def observation_features(self) -> dict[str, type]:
        return {f"{name}.pos": float for name in ALL_MOTORS}

    @property
    def action_features(self) -> dict[str, type]:
        return dict(self.observation_features)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_calibrated(self) -> bool:
        return True

    @property
    def last_receipt(self) -> ActionReceipt | None:
        return self._last_receipt

    def connect(self, calibrate: bool = True) -> None:
        del calibrate
        if self._connected:
            raise RuntimeError("SO101MujocoRobot is already connected")
        self._connected = True
        self._stop_event.clear()
        if self.auto_step:
            self._thread = threading.Thread(target=self._clock_loop, name="so101-mujoco-clock", daemon=True)
            self._thread.start()
        self._emit("connect")

    def setup_motors(self) -> None:
        """Compatibility no-op: the simulated motors are already declared."""

    def calibrate(self) -> None:
        """Compatibility no-op: normalization is fixed by the MJCF mapping."""

    def configure(self) -> None:
        """Compatibility no-op for the fixed simulation actuators."""

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._connected = False
        self._emit("disconnect")

    def close(self) -> None:
        self.disconnect()

    def __enter__(self) -> "SO101MujocoRobot":
        self.connect(calibrate=False)
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    def _require_connected(self) -> None:
        if not self._connected:
            raise RobotNotConnectedError("SO101MujocoRobot is not connected")

    def _clock_loop(self) -> None:
        period = 1.0 / self.simulation_hz
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            next_tick += period
            with self._lock:
                self._mj.mj_step(self.model, self.data)
            wait = next_tick - time.monotonic()
            if wait > 0:
                self._stop_event.wait(wait)
            else:
                next_tick = time.monotonic()

    def advance(self, seconds: float) -> None:
        """Harness-only deterministic stepping for tests and validation."""
        self._require_connected()
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        steps = max(0, int(math.ceil(seconds / self.model.opt.timestep)))
        with self._lock:
            for _ in range(steps):
                self._mj.mj_step(self.model, self.data)

    def reset(self, *, qpos: Mapping[str, float] | None = None) -> None:
        """Harness-only world reset; qpos values are radians except gripper."""
        with self._lock:
            self.data.qpos[:] = self._initial_qpos
            self.data.qvel[:] = self._initial_qvel
            self.data.ctrl[:] = 0
            if qpos:
                for name, value in qpos.items():
                    if name not in self._joint_ids:
                        raise KeyError(f"unknown SO-101 joint {name!r}")
                    address = int(self.model.jnt_qposadr[self._joint_ids[name]])
                    self.data.qpos[address] = float(value)
            self._mj.mj_forward(self.model, self.data)
            for name in ALL_MOTORS:
                actuator = self._actuator_ids[name]
                joint = self._joint_ids[name]
                address = int(self.model.jnt_qposadr[joint])
                self.data.ctrl[actuator] = self.data.qpos[address]
        self._emit("reset")

    def _joint_rad(self, name: str) -> float:
        address = int(self.model.jnt_qposadr[self._joint_ids[name]])
        return float(self.data.qpos[address])

    def _from_model(self, name: str, radians: float) -> float:
        if name == GRIPPER:
            actuator = self._actuator_ids[name]
            low, high = map(float, self.model.actuator_ctrlrange[actuator])
            if high <= low:
                raise ValueError("invalid gripper actuator range")
            return min(100.0, max(0.0, (radians - low) * 100.0 / (high - low)))
        if self.use_degrees:
            return math.degrees(radians)
        # LeRobot SO follower uses normalized -100..100, not radians, when
        # use_degrees is false.
        actuator = self._actuator_ids[name]
        low, high = map(float, self.model.actuator_ctrlrange[actuator])
        return -100.0 + (radians - low) * 200.0 / (high - low)

    def _to_model(self, name: str, value: float) -> float:
        actuator = self._actuator_ids[name]
        low, high = map(float, self.model.actuator_ctrlrange[actuator])
        if name == GRIPPER:
            return low + min(100.0, max(0.0, value)) * (high - low) / 100.0
        if self.use_degrees:
            return math.radians(value)
        normalized = min(100.0, max(-100.0, value))
        return low + (normalized + 100.0) * (high - low) / 200.0

    def get_observation(self) -> dict[str, float]:
        self._require_connected()
        with self._lock:
            result = {
                f"{name}.pos": self._from_model(name, self._joint_rad(name))
                for name in ALL_MOTORS
            }
        self._emit("observation", observation=result)
        return result

    def _max_delta(self, name: str) -> float | None:
        if self.max_relative_target is None:
            return None
        if isinstance(self.max_relative_target, Mapping):
            value = self.max_relative_target.get(name)
            return None if value is None else float(value)
        return float(self.max_relative_target)

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        self._require_connected()
        requested = {str(key): float(value) for key, value in action.items() if str(key).endswith(".pos")}
        if not requested:
            raise ValueError("action must contain at least one '*.pos' field")
        unknown = sorted(
            key for key in requested if key.removesuffix(".pos") not in self._actuator_ids
        )
        if unknown:
            raise KeyError(f"unknown SO-101 action fields: {unknown}")
        requested_motors = {
            key.removesuffix(".pos")
            for key in requested
        }
        if isinstance(self.max_relative_target, Mapping):
            configured_motors = {str(name).removesuffix(".pos") for name in self.max_relative_target}
            if configured_motors != requested_motors:
                raise ValueError(
                    "max_relative_target keys must exactly match the motors in this action: "
                    f"configured={sorted(configured_motors)}, requested={sorted(requested_motors)}"
                )
        accepted: dict[str, float] = {}
        clipped: dict[str, bool] = {}
        with self._lock:
            current = {
                name: self._from_model(name, self._joint_rad(name))
                for name in ALL_MOTORS
            }
            for key, requested_value in requested.items():
                name = key.removesuffix(".pos")
                value = requested_value
                delta_limit = self._max_delta(name)
                if delta_limit is not None:
                    value = min(current[name] + delta_limit, max(current[name] - delta_limit, value))
                model_value = self._to_model(name, value)
                actuator = self._actuator_ids[name]
                low, high = map(float, self.model.actuator_ctrlrange[actuator])
                model_value = min(high, max(low, model_value))
                self.data.ctrl[actuator] = model_value
                accepted_value = self._from_model(name, model_value)
                accepted[key] = accepted_value
                clipped[key] = not math.isclose(accepted_value, requested_value, rel_tol=0, abs_tol=1e-9)
            simulation_time = float(self.data.time)
        self._last_receipt = ActionReceipt(requested, accepted, clipped, simulation_time)
        self._emit(
            "action",
            requested=requested,
            accepted=accepted,
            clipped=clipped,
            simulation_time=simulation_time,
        )
        return accepted

    def site_position(self, name: str = "gripperframe") -> tuple[float, float, float]:
        """Privileged harness/oracle measurement, never exposed as a tool."""
        with self._lock:
            site_id = self._name_id(self._mj.mjtObj.mjOBJ_SITE, name)
            return tuple(float(value) for value in self.data.site_xpos[site_id])

    def body_position(self, name: str) -> tuple[float, float, float]:
        """Privileged harness/oracle measurement, never exposed as a tool."""
        with self._lock:
            body_id = self._name_id(self._mj.mjtObj.mjOBJ_BODY, name)
            return tuple(float(value) for value in self.data.xpos[body_id])

    def _emit(self, event: str, **payload: Any) -> None:
        if self._trace:
            self._trace.append({"component": "lerobot_mujoco_bridge", "event": event, **payload})
