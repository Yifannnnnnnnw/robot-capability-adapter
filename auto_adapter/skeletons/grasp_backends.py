# SPDX-License-Identifier: Apache-2.0
"""Pluggable grasp backends for ArmSerialDLSSkeleton.

DESIGN.md §0.7 + codex F2: factor grasp out of the skeleton so the same
arm skeleton can drive robots with different grasping models:

  * WeldGraspBackend    — MuJoCo `<weld>` equality constraints, used by SO-101
                          and most demo MJCFs where contact dynamics are noisy
                          and a "kinematic glue" is fine. The skeleton activates
                          the weld between gripper body and the closest graspable
                          body, capturing their CURRENT relative pose as the
                          weld anchor (otherwise MuJoCo snaps to the MJCF default).

  * ContactGraspBackend — No weld; relies on actual physical contact + friction
                          to hold the object. The agent closes the gripper, lets
                          physics settle, then checks whether the closest body is
                          still near the EE. Works for Franka / UR5 / etc. when
                          the gripper actually closes physically.

  * NoOpGraspBackend    — For robots without grippers (or where grasping isn't
                          part of the skill set). gripper_close/open are no-ops;
                          is_holding always False.

The skeleton selects a backend via `ArmSpec.grasp_backend` (or auto-picks based
on the spec's contents). All backends share the same interface so the rest of
the skeleton doesn't change.
"""
from __future__ import annotations

from typing import Optional, Protocol

import numpy as np


# ──────────────────────────────────────────────────────────────────────────
# Backend protocol
# ──────────────────────────────────────────────────────────────────────────


class GraspBackend(Protocol):
    """Interface every grasp backend implements.

    Implementations are passed the *skeleton* (not the spec) so they can read
    live MuJoCo state via skeleton.model / skeleton.data / skeleton._mj / etc.
    """

    name: str

    def setup(self, skeleton) -> None:
        """Called once at skeleton construction. Resolve MJCF indices, etc."""
        ...

    def engage(self, skeleton) -> Optional[str]:
        """Close gripper + attempt to grasp. Returns held body name, or None."""
        ...

    def disengage(self, skeleton) -> None:
        """Open gripper + release any held object."""
        ...

    def is_holding(self) -> bool:
        ...

    def held_body(self) -> Optional[str]:
        ...

    def describe(self) -> dict:
        ...


# ──────────────────────────────────────────────────────────────────────────
# 1. Weld backend — SO-101 style, kinematic glue
# ──────────────────────────────────────────────────────────────────────────


class WeldGraspBackend:
    """Activate a MuJoCo weld equality between gripper body and closest graspable
    body. Captures current relative pose into eq_data so the object doesn't snap.

    MJCF welds frequently have no explicit name (e.g. SO-101); we resolve them
    at setup() by scanning model.eq_type for `mjEQ_WELD` and matching
    eq.obj2id (or obj1id) names to the spec's graspable_bodies.
    """

    name = "weld"

    def __init__(self) -> None:
        # Filled by setup()
        self._weld_eq_ids: dict[str, int] = {}  # body_name → equality index
        self._held: Optional[str] = None

    def setup(self, skeleton) -> None:
        """Resolve weld equalities between gripper_link and each graspable body."""
        mj = skeleton._mj
        model = skeleton.model
        spec = skeleton.spec

        graspable = list(spec.weld_graspable_bodies or [])
        if not graspable:
            self._weld_eq_ids = {}
            return

        body_id = {
            mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, i): i
            for i in range(model.nbody)
        }
        # mjEQ_WELD = 2; using the enum for clarity
        WELD = int(mj.mjtEq.mjEQ_WELD)
        eq_map: dict[str, int] = {}
        for ei in range(model.neq):
            if int(model.eq_type[ei]) != WELD:
                continue
            b1 = int(model.eq_obj1id[ei])
            b2 = int(model.eq_obj2id[ei])
            # Match by the second body name (typically the graspable object)
            for g in graspable:
                gid = body_id.get(g)
                if gid is None:
                    continue
                if b2 == gid or b1 == gid:
                    eq_map[g] = ei
                    break
        missing = [g for g in graspable if g not in eq_map]
        if missing:
            # Not fatal — the agent may have listed extra bodies. Just record.
            pass
        self._weld_eq_ids = eq_map

    def engage(self, skeleton) -> Optional[str]:
        """Set the close ctrl, settle, then activate the nearest body's weld."""
        mj = skeleton._mj
        spec = skeleton.spec

        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(spec.gripper_close_ctrl)
        if skeleton.spec.gripper_settle_steps > 0:
            skeleton.step(skeleton.spec.gripper_settle_steps)

        if not self._weld_eq_ids:
            return None

        mj.mj_forward(skeleton.model, skeleton.data)
        ee = skeleton._ee_pos_now()
        best_body: Optional[str] = None
        best_dist = float(spec.grasp_radius)
        for body_name in self._weld_eq_ids:
            obj = skeleton.get_object_position(body_name)
            d = float(np.linalg.norm(obj - ee))
            if d < best_dist:
                best_dist = d
                best_body = body_name

        if best_body is None:
            self._held = None
            return None

        # Capture current relative pose into eq_data so the weld locks the
        # bodies *here*, not at the MJCF default anchor (which would snap).
        eid = self._weld_eq_ids[best_body]
        b1 = int(skeleton.model.eq_obj1id[eid])
        b2 = int(skeleton.model.eq_obj2id[eid])
        p1 = np.array(skeleton.data.xpos[b1], dtype=np.float64)
        R1 = np.array(skeleton.data.xmat[b1], dtype=np.float64).reshape(3, 3)
        p2 = np.array(skeleton.data.xpos[b2], dtype=np.float64)
        R2 = np.array(skeleton.data.xmat[b2], dtype=np.float64).reshape(3, 3)
        rel_pos = R1.T @ (p2 - p1)
        rel_R = R1.T @ R2
        rel_quat = np.zeros(4, dtype=np.float64)
        mj.mju_mat2Quat(rel_quat, rel_R.flatten())
        skeleton.model.eq_data[eid, :3] = 0.0
        skeleton.model.eq_data[eid, 3:6] = rel_pos
        skeleton.model.eq_data[eid, 6:10] = rel_quat
        # torquescale at idx 10 → leave default 1.0

        for body_name, ei in self._weld_eq_ids.items():
            skeleton.data.eq_active[ei] = (body_name == best_body)

        skeleton.step(50)
        self._held = best_body
        return best_body

    def disengage(self, skeleton) -> None:
        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(skeleton.spec.gripper_open_ctrl)
        for eid in self._weld_eq_ids.values():
            skeleton.data.eq_active[eid] = False
        if skeleton.spec.gripper_settle_steps > 0:
            skeleton.step(skeleton.spec.gripper_settle_steps)
        self._held = None

    def is_holding(self) -> bool:
        return self._held is not None

    def held_body(self) -> Optional[str]:
        return self._held

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "weld_eqs": dict(self._weld_eq_ids),
            "held_body": self._held,
        }


# ──────────────────────────────────────────────────────────────────────────
# 2. Contact backend — physics-based, for Franka / UR5 / etc.
# ──────────────────────────────────────────────────────────────────────────


class ContactGraspBackend:
    """Pure physics: close the gripper, let contacts hold the object via friction.

    No MJCF equality constraints required. After settle, we check whether the
    closest graspable body is still within `grasp_radius` of the EE — if so,
    we consider the grasp successful. This works when the gripper geometry
    actually closes on the object and friction holds it.

    `graspable_body_names` lets the user list candidate bodies; if omitted,
    the backend considers ALL free-joint bodies in the scene.
    """

    name = "contact"

    def __init__(self) -> None:
        self._candidate_bodies: list[str] = []
        self._held: Optional[str] = None

    def setup(self, skeleton) -> None:
        mj = skeleton._mj
        model = skeleton.model
        spec = skeleton.spec

        explicit = list(spec.weld_graspable_bodies or [])
        if explicit:
            self._candidate_bodies = explicit
            return

        # Auto-discover: any body with a free joint is a candidate.
        free_bodies: list[str] = []
        FREE = int(mj.mjtJoint.mjJNT_FREE)
        for bi in range(model.nbody):
            jnum = int(model.body_jntnum[bi])
            jadr = int(model.body_jntadr[bi])
            if jnum >= 1 and int(model.jnt_type[jadr]) == FREE:
                name = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, bi)
                if name:
                    free_bodies.append(name)
        self._candidate_bodies = free_bodies

    def engage(self, skeleton) -> Optional[str]:
        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(skeleton.spec.gripper_close_ctrl)
        if skeleton.spec.gripper_settle_steps > 0:
            skeleton.step(skeleton.spec.gripper_settle_steps)
        skeleton._mj.mj_forward(skeleton.model, skeleton.data)

        ee = skeleton._ee_pos_now()
        best_body: Optional[str] = None
        best_dist = float(skeleton.spec.grasp_radius)
        for name in self._candidate_bodies:
            try:
                obj = skeleton.get_object_position(name)
            except (KeyError, ValueError):
                continue
            d = float(np.linalg.norm(obj - ee))
            if d < best_dist:
                best_dist = d
                best_body = name

        self._held = best_body  # may be None
        return best_body

    def disengage(self, skeleton) -> None:
        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(skeleton.spec.gripper_open_ctrl)
        if skeleton.spec.gripper_settle_steps > 0:
            skeleton.step(skeleton.spec.gripper_settle_steps)
        self._held = None

    def is_holding(self) -> bool:
        return self._held is not None

    def held_body(self) -> Optional[str]:
        return self._held

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "candidate_bodies": self._candidate_bodies,
            "held_body": self._held,
        }


# ──────────────────────────────────────────────────────────────────────────
# 3. NoOp backend — gripperless arms / quadrupeds / mobile bases
# ──────────────────────────────────────────────────────────────────────────


class NoOpGraspBackend:
    """For robots without grippers. All grasp ops are no-ops; is_holding=False.

    Selected automatically when ArmSpec.gripper_actuator_names is None or empty.
    """

    name = "noop"

    def setup(self, skeleton) -> None:  # pragma: no cover - trivial
        pass

    def engage(self, skeleton) -> Optional[str]:
        return None

    def disengage(self, skeleton) -> None:
        pass

    def is_holding(self) -> bool:
        return False

    def held_body(self) -> Optional[str]:
        return None

    def describe(self) -> dict:
        return {"backend": self.name}


# ──────────────────────────────────────────────────────────────────────────
# Factory: pick a backend from an ArmSpec
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────
# 4. RealContact backend — verify grasp via sustained contact forces
# ──────────────────────────────────────────────────────────────────────────


class RealContactGraspBackend:
    """Physics-validated grasp: gripper closes, sim runs, we read contact
    sensors. A grasp is REAL only when contact forces between the gripper's
    geoms and an object's geom stay above a threshold across N sim steps
    AFTER the gripper has closed.

    Unlike WeldGraspBackend (kinematic glue) or the simple ContactGraspBackend
    (which just checks proximity), this backend exercises actual MuJoCo
    physics: friction, normal force, slip. It's the only backend whose
    is_holding() means the same thing as "a real robot is holding the
    object".

    Caveats:
      - Requires the gripper geometry to actually close on something. SO-101's
        single-jaw / Franka's parallel jaw / Piper's slide-finger all qualify.
      - Requires a SETTLE period after close (default: 30 steps). During this
        period the body may slip out if grip is unstable.
      - is_holding() is recomputed each call from the LATEST data.contact —
        so move_cartesian + this backend will see "not held" if the object
        slips during transit.

    Set up by listing candidate body names (or auto-discover free-joint
    bodies the same way ContactGraspBackend does).
    """

    name = "real_contact"

    def __init__(self, force_threshold: float = 0.5,
                 check_steps: int = 30) -> None:
        self._force_threshold = float(force_threshold)
        self._check_steps = int(check_steps)
        self._candidate_bodies: list[str] = []
        self._candidate_body_ids: dict[str, int] = {}
        self._gripper_body_ids: set[int] = set()
        self._held: Optional[str] = None

    def setup(self, skeleton) -> None:
        mj = skeleton._mj
        model = skeleton.model
        spec = skeleton.spec

        # Candidate body names: explicit list, else auto-discover free bodies
        explicit = list(spec.weld_graspable_bodies or [])
        if not explicit:
            FREE = int(mj.mjtJoint.mjJNT_FREE)
            for bi in range(model.nbody):
                jnum = int(model.body_jntnum[bi])
                jadr = int(model.body_jntadr[bi])
                if jnum >= 1 and int(model.jnt_type[jadr]) == FREE:
                    nm = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, bi)
                    if nm:
                        explicit.append(nm)
        self._candidate_bodies = explicit

        for n in self._candidate_bodies:
            bid = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, n)
            if bid >= 0:
                self._candidate_body_ids[n] = int(bid)

        # Identify gripper bodies by gripper actuator → joint → body chain.
        # Anything in the SUBTREE of the body containing the gripper joint
        # is treated as a gripper body. Heuristic but works for SO-101
        # (jaw_visual is a child of gripper_link), Franka (left/right_finger
        # both children of hand), etc.
        for aname in spec.gripper_actuator_names or []:
            aid = mj.mj_name2id(model, mj.mjtObj.mjOBJ_ACTUATOR, aname)
            if aid < 0:
                continue
            jid = int(model.actuator_trnid[aid, 0])
            if jid < 0:
                continue
            jbody = int(model.jnt_bodyid[jid])
            # Walk up to find an ancestor "gripper-ish" body
            cur = jbody
            for _ in range(8):
                if cur <= 0:
                    break
                # Always include this body
                self._gripper_body_ids.add(cur)
                cur = int(model.body_parentid[cur])
            # Also include all descendants of jbody
            for bi in range(model.nbody):
                anc = bi
                for _ in range(16):
                    if anc <= 0:
                        break
                    if anc == jbody:
                        self._gripper_body_ids.add(bi)
                        break
                    anc = int(model.body_parentid[anc])

    def _check_held_via_contact(self, skeleton) -> Optional[str]:
        """Return the candidate body name currently in sustained contact
        with the gripper, or None."""
        mj = skeleton._mj
        model = skeleton.model
        data = skeleton.data

        # Force a forward pass to refresh contact data
        mj.mj_forward(model, data)

        candidate_scores: dict[str, float] = {n: 0.0 for n in self._candidate_bodies}

        for ci in range(int(data.ncon)):
            con = data.contact[ci]
            g1 = int(con.geom1)
            g2 = int(con.geom2)
            b1 = int(model.geom_bodyid[g1])
            b2 = int(model.geom_bodyid[g2])
            gripper_side: Optional[int] = None
            other_body: Optional[int] = None
            if b1 in self._gripper_body_ids and b2 not in self._gripper_body_ids:
                gripper_side, other_body = b1, b2
            elif b2 in self._gripper_body_ids and b1 not in self._gripper_body_ids:
                gripper_side, other_body = b2, b1
            else:
                continue
            # Find which candidate this body belongs to (could be the body
            # itself or a descendant geom of it).
            anc = other_body
            for _ in range(16):
                if anc <= 0:
                    break
                nm = mj.mj_id2name(model, mj.mjtObj.mjOBJ_BODY, anc)
                if nm in candidate_scores:
                    # Use contact normal force magnitude as a score proxy
                    # (data.efc_force[con.efc_address] is signed).
                    f = float(abs(con.dist)) + 1.0  # any contact contributes
                    candidate_scores[nm] += f
                    break
                anc = int(model.body_parentid[anc])

        # Pick whichever candidate accumulated the most contact "evidence"
        # above the threshold. If none, return None.
        best = None
        best_score = self._force_threshold
        for nm, sc in candidate_scores.items():
            if sc > best_score:
                best_score = sc
                best = nm
        return best

    def engage(self, skeleton) -> Optional[str]:
        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(skeleton.spec.gripper_close_ctrl)
        # Run the gripper closing motion + settle
        skeleton.step(self._check_steps)
        # Sample contact AFTER settle
        held = self._check_held_via_contact(skeleton)
        self._held = held
        return held

    def disengage(self, skeleton) -> None:
        for aid in skeleton._gripper_actuator_ids:
            skeleton.data.ctrl[aid] = float(skeleton.spec.gripper_open_ctrl)
        skeleton.step(self._check_steps)
        self._held = None

    def is_holding(self) -> bool:
        return self._held is not None

    def held_body(self) -> Optional[str]:
        return self._held

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "force_threshold": self._force_threshold,
            "check_steps": self._check_steps,
            "candidates": self._candidate_bodies,
            "n_gripper_bodies": len(self._gripper_body_ids),
            "held_body": self._held,
        }


_REGISTRY: dict[str, type] = {
    "weld": WeldGraspBackend,
    "contact": ContactGraspBackend,
    "real_contact": RealContactGraspBackend,
    "noop": NoOpGraspBackend,
}


def make_grasp_backend(spec) -> GraspBackend:
    """Build the appropriate backend for a spec.

    Resolution:
      1. If `spec.grasp_backend` is set explicitly, use it.
      2. Else if there are no gripper actuators → NoOp.
      3. Else if any `<weld>` equality is implied (graspable_bodies set) → Weld.
      4. Else → Contact.
    """
    explicit = getattr(spec, "grasp_backend", None)
    if explicit:
        cls = _REGISTRY.get(explicit)
        if cls is None:
            raise ValueError(
                f"unknown grasp_backend {explicit!r}; choose from {list(_REGISTRY)}"
            )
        return cls()

    has_gripper = bool(getattr(spec, "gripper_actuator_names", None))
    if not has_gripper:
        return NoOpGraspBackend()

    has_weld_targets = bool(getattr(spec, "weld_graspable_bodies", None))
    return WeldGraspBackend() if has_weld_targets else ContactGraspBackend()
