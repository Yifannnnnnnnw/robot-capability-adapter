# SPDX-License-Identifier: Apache-2.0
"""Read-only A4 surface and contact velocity observations.

The A4 Harness records two velocity measurements which are not available from
the candidate's return value.  The pair list is resolved once from the trusted
binding and the canonical model.  Each snapshot then reads the supplied
``MjData`` only; it does not step, forward, or otherwise mutate the world.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


_REQUIRED_KEYS = frozenset(
    {
        "a4_surface_relative_speed_m_s",
        "a4_contact_normal_closing_speed_m_s",
    }
)


def _names(binding: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = binding.get(key)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"A4 binding {key} must be an array of geometry names")
    names = tuple(str(item) for item in value)
    if not names or any(not item for item in names):
        raise ValueError(f"A4 binding {key} must contain geometry names")
    return names


class A4Observations:
    """Compute the two public A4 velocity scalars from a canonical world.

    ``tool_geom_names`` and ``target_geom_names`` are copied from the trusted
    measurement binding.  A pair is frozen when this object is constructed if
    the two geoms are distinct, are not on the same body (MuJoCo does not
    collide geoms on one body), are not disabled visual geoms
    (``contype == conaffinity == 0``), and satisfy MuJoCo's symmetric
    contype/conaffinity collision filter:
    ``(contype1 & conaffinity2) or (contype2 & conaffinity1)``.

    The first scalar is the maximum norm of the relative velocities of the
    closest surface points returned by ``mj_geomDistance``.  The second is
    the maximum positive closing speed among matching live contacts.  MuJoCo's
    contact normal points from ``geom1`` to ``geom2``; therefore closing speed
    is ``max(0, -dot(v_geom2 - v_geom1, normal))``.
    """

    def __init__(
        self,
        model: Any,
        binding: Mapping[str, Any],
        *,
        mujoco_module: Any | None = None,
    ) -> None:
        if not isinstance(binding, Mapping):
            raise ValueError("A4 binding must be an object")
        self.model = model
        if mujoco_module is None:
            import mujoco as mujoco_module  # noqa: PLC0415

        self.mujoco = mujoco_module
        self.tool_geom_names = _names(binding, "tool_geom_names")
        self.target_geom_names = _names(binding, "target_geom_names")
        self._pairs = self._freeze_pairs()
        if not self._pairs:
            raise ValueError("A4 binding has no collision-eligible tool-target geometry pair")
        self._pair_set = frozenset(self._pairs)

    def _resolve_geom(self, name: str) -> int:
        geom_id = int(
            self.mujoco.mj_name2id(
                self.model, self.mujoco.mjtObj.mjOBJ_GEOM, name
            )
        )
        if geom_id < 0 and name.startswith("geom_"):
            # capability_eval's canonical name helper uses this exact form for
            # unnamed geoms.  Accept it only when MuJoCo confirms that the
            # referenced index is in range and has no real name.
            suffix = name[len("geom_") :]
            if suffix.isdigit():
                candidate = int(suffix)
                if 0 <= candidate < int(self.model.ngeom):
                    actual_name = self.mujoco.mj_id2name(
                        self.model, self.mujoco.mjtObj.mjOBJ_GEOM, candidate
                    )
                    if not actual_name:
                        geom_id = candidate
        if geom_id < 0:
            raise ValueError(f"unknown A4 geometry {name!r}")
        return geom_id

    def _collision_eligible(self, first: int, second: int) -> bool:
        contype_first = int(self.model.geom_contype[first])
        affinity_first = int(self.model.geom_conaffinity[first])
        contype_second = int(self.model.geom_contype[second])
        affinity_second = int(self.model.geom_conaffinity[second])
        if (contype_first == 0 and affinity_first == 0) or (
            contype_second == 0 and affinity_second == 0
        ):
            return False
        return bool(
            (contype_first & affinity_second)
            or (contype_second & affinity_first)
        )

    def _freeze_pairs(self) -> tuple[tuple[int, int], ...]:
        tool_ids = tuple(self._resolve_geom(name) for name in self.tool_geom_names)
        target_ids = tuple(self._resolve_geom(name) for name in self.target_geom_names)
        pairs: list[tuple[int, int]] = []
        for tool_id in tool_ids:
            for target_id in target_ids:
                if tool_id == target_id:
                    continue
                if int(self.model.geom_bodyid[tool_id]) == int(
                    self.model.geom_bodyid[target_id]
                ):
                    continue
                if not self._collision_eligible(tool_id, target_id):
                    continue
                pair = (tool_id, target_id)
                if pair not in pairs:
                    pairs.append(pair)
        return tuple(pairs)

    @property
    def collision_pairs(self) -> tuple[tuple[int, int], ...]:
        """Return the frozen ``(tool_geom_id, target_geom_id)`` pairs."""
        return self._pairs

    def _point_velocity(self, data: Any, point: np.ndarray, body_id: int) -> np.ndarray:
        qvel = np.asarray(data.qvel, dtype=np.float64)
        if not np.all(np.isfinite(qvel)) or not np.all(np.isfinite(point)):
            raise ValueError("A4 point velocity inputs were non-finite")
        jacobian = np.zeros((3, int(self.model.nv)), dtype=np.float64)
        self.mujoco.mj_jac(self.model, data, jacobian, None, point, body_id)
        velocity = jacobian @ qvel
        if not np.all(np.isfinite(velocity)):
            raise ValueError("A4 point velocity was non-finite")
        return velocity

    def _surface_relative_speed(self, data: Any) -> float:
        maximum = 0.0
        fromto = np.empty(6, dtype=np.float64)
        for first, second in self._pairs:
            distance = float(
                self.mujoco.mj_geomDistance(
                    self.model, data, first, second, 1.0e6, fromto
                )
            )
            if not np.isfinite(distance) or not np.all(np.isfinite(fromto)):
                raise ValueError("A4 closest surface observation was non-finite")
            first_point, second_point = fromto[:3], fromto[3:]
            first_velocity = self._point_velocity(
                data,
                first_point,
                int(self.model.geom_bodyid[first]),
            )
            second_velocity = self._point_velocity(
                data,
                second_point,
                int(self.model.geom_bodyid[second]),
            )
            relative_velocity = second_velocity - first_velocity
            speed = float(np.linalg.norm(relative_velocity))
            if not np.isfinite(speed):
                raise ValueError("A4 surface relative speed was non-finite")
            maximum = max(maximum, speed)
        return maximum

    def _contact_normal_closing_speed(self, data: Any) -> float:
        maximum = 0.0
        for index in range(int(data.ncon)):
            contact = data.contact[index]
            first = int(contact.geom1)
            second = int(contact.geom2)
            if (first, second) not in self._pair_set and (
                (second, first) not in self._pair_set
            ):
                continue
            frame = np.asarray(contact.frame, dtype=np.float64)
            if frame.size < 3:
                raise ValueError("A4 contact frame was malformed")
            normal = frame[:3]
            norm = float(np.linalg.norm(normal))
            if not np.isfinite(norm) or norm <= 1.0e-12:
                raise ValueError("A4 contact normal was malformed")
            normal = normal / norm
            point = np.asarray(contact.pos, dtype=np.float64)
            if point.shape != (3,) or not np.all(np.isfinite(point)):
                raise ValueError("A4 contact point was malformed")
            first_velocity = self._point_velocity(
                data, point, int(self.model.geom_bodyid[first])
            )
            second_velocity = self._point_velocity(
                data, point, int(self.model.geom_bodyid[second])
            )
            signed_gap_rate = float(np.dot(second_velocity - first_velocity, normal))
            if not np.isfinite(signed_gap_rate):
                raise ValueError("A4 contact normal closing speed was non-finite")
            closing = max(0.0, -signed_gap_rate)
            maximum = max(maximum, closing)
        return maximum

    def snapshot(self, data: Any) -> dict[str, float]:
        """Return both required A4 scalars without stepping or forwarding."""
        result = {
            "a4_surface_relative_speed_m_s": self._surface_relative_speed(data),
            "a4_contact_normal_closing_speed_m_s": self._contact_normal_closing_speed(data),
        }
        if frozenset(result) != _REQUIRED_KEYS:
            raise RuntimeError("A4 observation keys are incomplete")
        return result


__all__ = ["A4Observations"]
