"""Deterministic LeRobot-shaped tabletop fixture runtime.

This tiny state model exists to test orchestration without an LLM, hardware,
wall-clock physics, or flaky contacts.  It is explicitly *not* reported as
MuJoCo/real-robot performance evidence; the actual bridge lives in
``lerobot_mujoco.py`` and is tested separately.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping


MOTORS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


class DeterministicTabletopRuntime:
    """Immediate six-key motor API plus private fixture state measurements."""

    name = "so101_deterministic_tabletop_fixture"

    def __init__(self) -> None:
        self._connected = False
        self._joints: dict[str, float] = {}
        self._objects: dict[str, dict[str, Any]] = {}
        self._receptacles: dict[str, dict[str, Any]] = {}
        self._attached: str | None = None
        self._clipped = False
        self._event_order: list[str] = []
        self._initial_object_z: dict[str, float] = {}
        self._maximum_object_z: dict[str, float] = {}
        self.reset({})

    @property
    def observation_features(self) -> dict[str, type]:
        return {f"{name}.pos": float for name in MOTORS}

    @property
    def action_features(self) -> dict[str, type]:
        return dict(self.observation_features)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_calibrated(self) -> bool:
        return True

    def connect(self, calibrate: bool = True) -> None:
        del calibrate
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def setup_motors(self) -> None:
        return None

    def calibrate(self) -> None:
        return None

    def configure(self) -> None:
        return None

    def reset(self, initial_state: Mapping[str, Any]) -> None:
        self._joints = {
            "shoulder_pan": 0.0,
            "shoulder_lift": 0.0,
            "elbow_flex": 50.0,
            "wrist_flex": 0.0,
            "wrist_roll": 0.0,
            "gripper": 80.0,
        }
        qpos = initial_state.get("qpos", {})
        if isinstance(qpos, Mapping):
            for name, value in qpos.items():
                clean = str(name).removesuffix(".pos")
                if clean in self._joints:
                    self._joints[clean] = float(value)
        self._objects = {}
        self._receptacles = {}
        for body in initial_state.get("bodies", []):
            if not isinstance(body, Mapping) or "id" not in body:
                continue
            item = deepcopy(dict(body))
            identifier = str(item["id"])
            if item.get("kind") in {"tray", "bowl"}:
                center = item.get("center_m", item.get("position_m", [0.0, 0.0, 0.0]))
                item["position_m"] = [float(value) for value in center]
                self._receptacles[identifier] = item
            else:
                item["position_m"] = [float(value) for value in item.get("position_m", [0, 0, 0])]
                self._objects[identifier] = item
        self._attached = None
        self._clipped = False
        self._event_order = []
        self._initial_object_z = {
            name: float(item["position_m"][2]) for name, item in self._objects.items()
        }
        self._maximum_object_z = dict(self._initial_object_z)
        self._connected = True

    def get_observation(self) -> dict[str, float]:
        self._require_connected()
        return {f"{name}.pos": float(value) for name, value in self._joints.items()}

    def send_action(self, action: Mapping[str, float]) -> dict[str, float]:
        self._require_connected()
        previous_ee = self.end_effector_position()
        previous_gripper = self._joints["gripper"]
        ranges = {
            "shoulder_pan": (-90.0, 90.0),
            "shoulder_lift": (-20.0, 80.0),
            "elbow_flex": (-30.0, 120.0),
            "wrist_flex": (-110.0, 110.0),
            "wrist_roll": (-180.0, 180.0),
            "gripper": (0.0, 100.0),
        }
        accepted: dict[str, float] = {}
        for key, raw in action.items():
            name = str(key).removesuffix(".pos")
            if name not in self._joints:
                continue
            requested = float(raw)
            low, high = ranges[name]
            value = min(high, max(low, requested))
            self._clipped = self._clipped or not math.isclose(requested, value, abs_tol=1e-12)
            self._joints[name] = value
            accepted[f"{name}.pos"] = value
        current_ee = self.end_effector_position()
        current_gripper = self._joints["gripper"]
        if self._attached is not None:
            self._objects[self._attached]["position_m"] = list(current_ee)
            self._maximum_object_z[self._attached] = max(
                self._maximum_object_z[self._attached], float(current_ee[2])
            )
            if current_gripper >= 55.0:
                released = self._attached
                self._attached = None
                if any(self._inside_receptacle(released, receptacle) for receptacle in self._receptacles):
                    self._event_order.append(released)
        else:
            if current_gripper <= 30.0 and previous_gripper > 30.0:
                nearest = self._nearest_object(current_ee, maximum_distance=0.045)
                if nearest is not None:
                    self._attached = nearest
                    self._objects[nearest]["position_m"] = list(current_ee)
            elif current_gripper >= 55.0:
                self._apply_planar_push(previous_ee, current_ee)
        return accepted

    def end_effector_position(self) -> tuple[float, float, float]:
        # A deliberately transparent kinematic map for the orchestration fixture.
        return (
            0.30 + self._joints["shoulder_lift"] * 0.002,
            self._joints["shoulder_pan"] * 0.002,
            0.05 + self._joints["elbow_flex"] * 0.001,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "joint_positions": self.get_observation(),
            "end_effector_position_m": list(self.end_effector_position()),
            "object_positions_m": {
                name: list(item["position_m"]) for name, item in self._objects.items()
            },
            "receptacles": deepcopy(self._receptacles),
            "attached_object": self._attached,
            "containment_event_order": list(self._event_order),
            "action_clipped": self._clipped,
            "maximum_object_lift_above_initial_m": {
                name: max(0.0, self._maximum_object_z[name] - initial_z)
                for name, initial_z in self._initial_object_z.items()
            },
        }

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("deterministic tabletop runtime is not connected")

    def _nearest_object(
        self,
        position: tuple[float, float, float],
        *,
        maximum_distance: float,
    ) -> str | None:
        best: tuple[float, str] | None = None
        for name, item in self._objects.items():
            point = item["position_m"]
            distance = math.sqrt(sum((float(point[i]) - position[i]) ** 2 for i in range(3)))
            if distance <= maximum_distance and (best is None or distance < best[0]):
                best = distance, name
        return None if best is None else best[1]

    def _apply_planar_push(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
    ) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            return
        for item in self._objects.values():
            px, py, pz = map(float, item["position_m"])
            fraction = ((px - start[0]) * dx + (py - start[1]) * dy) / length_sq
            fraction = min(1.0, max(0.0, fraction))
            closest_x = start[0] + fraction * dx
            closest_y = start[1] + fraction * dy
            planar_distance = math.hypot(px - closest_x, py - closest_y)
            if planar_distance <= 0.04 and min(start[2], end[2]) <= pz + 0.03:
                item["position_m"] = [px + dx, py + dy, pz]

    def _inside_receptacle(self, object_id: str, receptacle_id: str) -> bool:
        if object_id not in self._objects or receptacle_id not in self._receptacles:
            return False
        position = self._objects[object_id]["position_m"]
        receptacle = self._receptacles[receptacle_id]
        center = receptacle["position_m"]
        if receptacle.get("kind") == "bowl":
            return math.hypot(position[0] - center[0], position[1] - center[1]) <= float(
                receptacle.get("inner_radius_m", 0.04)
            )
        size = receptacle.get("inner_size_m", [0.08, 0.08])
        return (
            abs(position[0] - center[0]) <= float(size[0]) / 2
            and abs(position[1] - center[1]) <= float(size[1]) / 2
        )

    def object_inside(self, object_id: str, receptacle_id: str) -> bool:
        return self._inside_receptacle(object_id, receptacle_id)
