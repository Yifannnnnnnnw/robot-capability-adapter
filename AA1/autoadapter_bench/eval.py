#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""AutoAdapter-Bench evaluator.

Usage:
    python autoadapter_bench/eval.py \
        --robot so101 \
        --suites simple,hard,contact_rich \
        --model us.anthropic.claude-sonnet-4-6 \
        --n-trials 5 \
        --output autoadapter_bench/results/run_2026-05-30_sonnet46_so101.json

Produces a single result.json with this schema:
    {
      "meta": {
        "robot": "so101",
        "robot_class": "arm",
        "mjcf": "...",
        "model": "us.anthropic.claude-sonnet-4-5...",
        "git_sha": "...",
        "started_at": "2026-05-30T02:30:00",
        "completed_at": "...",
        "wall_clock_sec": 1234.0
      },
      "suites": {
        "simple": {
          "tasks": [
            {
              "id": "move_x_plus_5cm",
              "trials": [
                {"ok": true, "physics_ok": true, "llm_ok": true,
                 "n_tool_calls": 3, "tokens": {"in": 7000, "out": 400},
                 "duration_sec": 14.2, "summary": "...",
                 "physics_detail": "EE returned to home within 1.5 cm",
                 "physics_metrics": {...}, "mp4_path": "..."},
              ...
              ],
              "pass_rate": 1.0, "mean_calls": 3.4, ...
            },
            ...
          ]
        },
        ...
      },
      "aggregate": {
        "n_tasks": 5,
        "n_trials_total": 25,
        "physics_pass_rate": 0.92,
        "llm_pass_rate": 1.00,
        "total_tokens": {...},
        "total_cost_usd": 1.50
      }
    }

This is the canonical entry point — reviewers should be able to reproduce
ANY scoreboard number by running this command.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Bench loading
# ──────────────────────────────────────────────────────────────────────────


def load_robot_zoo() -> dict:
    p = REPO_ROOT / "autoadapter_bench" / "spec" / "robot_zoo.yaml"
    return yaml.safe_load(p.read_text())


def load_task_suite(robot_class: str) -> dict:
    fname = {"arm": "tasks_arm.yaml", "quadruped": "tasks_quadruped.yaml",
             "wheeled": "tasks_wheeled.yaml", "aerial": "tasks_aerial.yaml",
             "humanoid": "tasks_humanoid.yaml",
             "dexterous_hand": "tasks_dexterous_hand.yaml",
             "mobile_manipulator": "tasks_mobile_manipulator.yaml",
             "bimanual": "tasks_bimanual.yaml"}[robot_class]
    return yaml.safe_load((REPO_ROOT / "autoadapter_bench" / "spec" / fname).read_text())


def find_robot(zoo: dict, robot_id: str) -> dict:
    for r in zoo["robots"]:
        if r["id"] == robot_id:
            return r
    raise ValueError(f"unknown robot id: {robot_id!r}; available: "
                      f"{[r['id'] for r in zoo['robots']]}")


def find_driver_workspace(robot_id: str) -> Path:
    """Map robot_id → path of the workspace containing driver.py.

    The benchmark expects drivers to already exist in
    `auto_adapter_<robot_id>_artifacts/`. If they don't, eval prints a
    clear error and exits.
    """
    candidates = [
        REPO_ROOT / "artifacts" / f"auto_adapter_{robot_id}_v2_artifacts",
        REPO_ROOT / "artifacts" / f"auto_adapter_{robot_id}_artifacts",
        REPO_ROOT / "artifacts" / f"auto_adapter_from_scratch_{robot_id}_artifacts",
        REPO_ROOT / "artifacts" / f"auto_adapter_{robot_id}_from_scratch_artifacts",
        REPO_ROOT / f"auto_adapter_{robot_id}_artifacts",
        REPO_ROOT / f"auto_adapter_{robot_id}_v2_artifacts",
    ]
    for c in candidates:
        if (c / "driver.py").exists() or (c / "driver_from_scratch.py").exists():
            return c
    raise SystemExit(
        f"No driver workspace found for {robot_id}. Looked in:\n  "
        + "\n  ".join(str(c) for c in candidates)
        + f"\nRun the driver-generation phase first."
    )


# ──────────────────────────────────────────────────────────────────────────
# Token-cost helper (model-aware)
# ──────────────────────────────────────────────────────────────────────────

# Per-model pricing (USD per 1M tokens) from MODEL_IDS.md.
# Keys are substrings of the full Bedrock model ID (lower-cased).
_MODEL_PRICING: list[tuple[str, float, float]] = [
    # (needle,               $/M in,   $/M out)
    ("claude-haiku-4-5",      1.00,    5.00),
    ("claude-sonnet-4-6",     3.00,   15.00),
    ("claude-sonnet-4-5",     3.00,   15.00),   # fallback for older Sonnet 4.5 ID
    ("claude-opus-4-8",      15.00,   75.00),
    ("claude-opus-4-9",      15.00,   75.00),   # anticipated successor; same price tier
    ("ministral",             0.10,    0.10),
    ("qwen3",                 0.15,    0.60),
    ("deepseek.v3",           0.27,    1.10),
    ("nova-pro",              0.80,    3.20),
]
_SONNET_FALLBACK_IN  = 3.00
_SONNET_FALLBACK_OUT = 15.00


def _cost_usd(model: str, tin: int, tout: int) -> float:
    """Return the USD cost for *tin* input tokens and *tout* output tokens
    for the given Bedrock *model* ID.  Matches against MODEL_IDS.md pricing;
    falls back to Sonnet 4.6 prices when no entry matches (no error raised)."""
    m = model.lower()
    for needle, p_in, p_out in _MODEL_PRICING:
        if needle in m:
            return tin * p_in / 1_000_000 + tout * p_out / 1_000_000
    # Unknown model — use Sonnet price as a conservative default
    return tin * _SONNET_FALLBACK_IN / 1_000_000 + tout * _SONNET_FALLBACK_OUT / 1_000_000


# ──────────────────────────────────────────────────────────────────────────
# Physics-validated success criteria
# ──────────────────────────────────────────────────────────────────────────


def _build_aggregate(result: dict, wall: float | None = None) -> dict:
    """Recompute the aggregate block from the CURRENT per-trial data. Called on
    every checkpoint so the stored aggregate is always consistent with the
    trials present — even if the process is killed mid-run or resumed (the old
    bug: aggregate was computed only at the very end, so a kill after the last
    task's checkpoint left a stale aggregate next to complete trials)."""
    all_tr = [t for s in result.get("suites", {}).values()
              for tk in s.get("tasks", []) for t in tk.get("trials", [])]
    n = len(all_tr)
    tin = sum(t.get("tokens", {}).get("in", 0) for t in all_tr)
    tout = sum(t.get("tokens", {}).get("out", 0) for t in all_tr)

    def _true(v):
        # Robust to legacy data where a numpy bool was serialized via
        # json.dumps(default=str) as the STRING "True"/"False": the bare
        # string "False" is truthy in Python, so a plain `if v` miscounts
        # failures as passes. Only genuine True (bool True or "True") passes.
        return v is True or v == "True"

    agg = {
        "n_tasks": sum(len(s.get("tasks", [])) for s in result.get("suites", {}).values()),
        "n_trials_total": n,
        "llm_pass_rate": sum(1 for t in all_tr if _true(t.get("llm_ok"))) / max(n, 1),
        "physics_pass_rate": sum(1 for t in all_tr if _true(t.get("physics_ok"))) / max(n, 1),
        "agreement_rate": sum(1 for t in all_tr if _true(t.get("agreement"))) / max(n, 1),
        "total_tokens": {"in": tin, "out": tout},
        "total_cost_usd": _cost_usd(result.get("meta", {}).get("model", ""), tin, tout),
    }
    prev = result.get("aggregate", {})
    if wall is not None:
        agg["wall_clock_sec"] = wall
    elif "wall_clock_sec" in prev:
        agg["wall_clock_sec"] = prev["wall_clock_sec"]
    result["aggregate"] = agg
    return agg


def _find_mj(skel):
    """Locate (MjModel, MjData) on a driver by scanning its attributes by TYPE.
    Driver-agnostic: works whether the driver stores them as self.model/self.data,
    self._model/self._data, etc."""
    import mujoco  # noqa: PLC0415
    model = data = None
    for v in vars(skel).values():
        if isinstance(v, mujoco.MjModel):
            model = v
        elif isinstance(v, mujoco.MjData):
            data = v
    return model, data


def _obj_pos(skel, name):
    """Object position, driver-agnostic. Prefer the driver's own
    `get_object_position` (the skeleton path, keeps Leaderboard-A behavior
    unchanged); otherwise read GROUND TRUTH straight from the underlying MuJoCo
    model/data. This makes the physics grader independent of whether a
    (model-synthesized) driver happens to expose an object-lookup method."""
    import numpy as np  # noqa: PLC0415
    import mujoco  # noqa: PLC0415
    gop = getattr(skel, "get_object_position", None)
    if callable(gop):
        # Method PRESENT → use it and let exceptions propagate, exactly as the
        # pre-change grader did. This keeps Leaderboard-A (skeleton driver)
        # numerically identical and grades A and B through the same path
        # (each driver's own perception API). The ground-truth branch below is
        # ONLY a bridge for legacy drivers that never exposed the method.
        return np.asarray(gop(name), dtype=float)
    model, data = _find_mj(skel)
    if model is not None and data is not None:
        for objtype, arr in ((mujoco.mjtObj.mjOBJ_BODY, data.xpos),
                             (mujoco.mjtObj.mjOBJ_GEOM, data.geom_xpos),
                             (mujoco.mjtObj.mjOBJ_SITE, data.site_xpos)):
            oid = mujoco.mj_name2id(model, objtype, name)
            if oid >= 0:
                return np.asarray(arr[oid], dtype=float)
    raise KeyError(name)


def _replay_tool_calls(skel, tool_call_log: list, snapshots: list | None = None,
                       state_refs: dict | None = None,
                       trace_samples: list | None = None,
                       robot_definition: dict | None = None,
                       from_scratch: bool = False) -> bool:
    """Re-execute a tool log on a fresh skeleton.

    The first three arguments retain the original evaluator API.  When
    ``state_refs`` is supplied, :class:`PhysicsTrace` observes the real model
    and data at the initial state and after every real ``mj_step``.  The
    optional ``trace_samples`` list is filled in place so callers can retain
    the trace without changing the historical boolean return value.
    """
    from contextlib import nullcontext  # noqa: PLC0415
    from auto_adapter.agent.task_planner import tool_registry_for, capability_tool_registry

    import numpy as np  # noqa: PLC0415

    def _snap(idx, tool):
        if snapshots is None:
            return
        rec = {"idx": idx, "tool": tool}
        try:
            rec["height"] = float(skel.get_body_height())
        except Exception:  # noqa: BLE001
            rec["height"] = None
        try:
            rec["xyz"] = np.asarray(skel.get_base_pose()[0], dtype=float).tolist()
        except Exception:  # noqa: BLE001
            rec["xyz"] = None
        try:  # arm EE position, for waypoint-trace tasks (e.g. draw an L)
            rec["ee"] = np.asarray(skel.get_ee_pose()[0], dtype=float).tolist()
        except Exception:  # noqa: BLE001
            rec["ee"] = None
        snapshots.append(rec)

    def _call_result_ok(tool, result):
        # Gripper operations legitimately return False when no object is
        # grasped; that is an observation for the task grader, not replay
        # divergence.  Motion/hold primitives use False to report a failed
        # bounded control attempt.
        motion_tools = {
            "move_cartesian", "move_fingertips", "move_joints", "drive_forward",
            "turn", "takeoff", "move_to", "walk_forward", "stand_up", "sit",
            "home", "stand_balance", "squat", "hold",
        }
        if tool not in motion_tools:
            return True
        if isinstance(result, (bool, np.bool_)):
            return bool(result)
        if isinstance(result, dict):
            for key in ("ok", "success", "reached"):
                if key in result and isinstance(result[key], (bool, np.bool_)):
                    return bool(result[key])
        return True

    # Share the exact argument conversion and defaults used by live tools.
    registry = (capability_tool_registry(skel, robot_definition, from_scratch=from_scratch)
                if robot_definition and robot_definition.get("capability_profile")
                else tool_registry_for(skel)) or {}

    def _invoke(method, tool, inp):
        if tool in registry:
            info = registry[tool]
            return method(*info["args"](inp), **info["kwargs"](inp))
        return method(**inp)

    trace = None
    trace_init_error = None
    if state_refs:
        try:
            from autoadapter_bench.physics import PhysicsTrace  # noqa: PLC0415
            trace = PhysicsTrace(skel, state_refs)
        except Exception as exc:  # noqa: BLE001
            trace_init_error = exc

    all_clean = trace_init_error is None
    context = trace if trace is not None else nullcontext()
    with context:
        for idx, call in enumerate(tool_call_log):
            tool = call.get("tool")
            inp = call.get("input", {}) or {}
            if not isinstance(inp, dict):
                inp = {}
            method = getattr(skel, tool, None)
            if method is None or not callable(method):
                print(f"   replay missing tool: {tool}", file=sys.stderr)
                all_clean = False
                _snap(idx, tool)
                continue
            if trace is not None:
                trace.tool, trace.idx = tool, idx
            try:
                result = _invoke(method, tool, inp)
                if not _call_result_ok(tool, result):
                    all_clean = False
                    print(f"   replay tool reported failure: {tool}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"   replay caught {tool}: {type(e).__name__}: {e} — continuing",
                      file=sys.stderr)
                all_clean = False
            _snap(idx, tool)

    if trace_samples is not None and trace is not None:
        trace_samples.extend(trace.samples)
    if trace_init_error is not None:
        print(f"   replay physics trace unavailable: {type(trace_init_error).__name__}: "
              f"{trace_init_error}", file=sys.stderr)
    return all_clean


def evaluate_success(skel, before_state: dict, success_spec: dict,
                     llm_result, task_id: str) -> tuple[bool, str, dict]:
    """Compute physics-validated success based on success_spec.type.
    Returns (ok, detail, metrics)."""
    import numpy as np  # noqa: PLC0415

    typ = success_spec["type"]

    if typ == "ee_pose_returned_home":
        home = np.array(before_state["ee_pose"])
        samples = before_state.get("_physics_samples", []) or []
        if samples and any(not s.get("finite") for s in samples):
            return False, "EE trace contains non-finite MuJoCo state", {}
        traj = [np.asarray(s["ee"], dtype=float) for s in samples
                if s.get("ee") is not None and np.isfinite(s["ee"]).all()]
        if not traj:
            snaps = before_state.get("_phase_snapshots", []) or []
            traj = [np.asarray(s["ee"], dtype=float) for s in snaps
                    if s.get("ee") is not None and np.isfinite(s["ee"]).all()]
        # Prefer the last trusted world-frame sample whenever one exists;
        # driver self-reports are only a legacy fallback for robots without a
        # zoo EE binding.
        if traj:
            ee = traj[-1]
        else:
            try:
                ee, _ = skel.get_ee_pose()
                ee = np.asarray(ee, dtype=float)
            except Exception:  # noqa: BLE001
                ee = np.full(3, np.nan)
        err = float(np.linalg.norm(ee - home))
        peak_excursion = max(
            (float(np.linalg.norm(point - home)) for point in traj), default=0.0
        )
        min_excursion = float(success_spec.get("min_excursion_m", 0.01))
        waypoints = [home + np.asarray(offset, dtype=float)
                     for offset in success_spec.get("required_offsets", [])]
        waypoint_tol = float(success_spec.get("target_tolerance_m",
                                               success_spec["tolerance_m"]))
        reached, cursor = [], 0
        for waypoint in waypoints:
            found = next((i for i in range(cursor, len(traj))
                          if float(np.linalg.norm(traj[i] - waypoint)) <= waypoint_tol),
                         None)
            reached.append(found is not None)
            if found is not None:
                cursor = found + 1
        targets_ok = all(reached) if waypoints else True
        ok = bool(err < float(success_spec["tolerance_m"])
                  and peak_excursion >= min_excursion and targets_ok)
        return (ok,
                f"EE err to home: {err*100:.2f} cm; peak excursion "
                f"{peak_excursion*100:.2f} cm (need >={min_excursion*100:.1f}); "
                f"targets={sum(reached)}/{len(waypoints)}",
                {"err_m": err, "peak_excursion_m": peak_excursion,
                 "min_excursion_m": min_excursion,
                 "required_offsets_reached": reached})

    if typ == "tool_executed_without_crash":
        called = {c["tool"] for c in llm_result.tool_call_log}
        required = set(success_spec.get("required_tools", []))
        missing = required - called
        replay_clean = bool(before_state.get("_replay_clean", True))
        ok = bool(not missing and replay_clean)
        detail = f"missing tools: {missing or 'none'}"
        if not replay_clean:
            detail += "; replay diverged"
        return ok, detail, {"called": list(called),
                            "replay_clean": replay_clean}

    if typ == "fingertip_targets_reached":
        target_map = (success_spec.get("target_fingertips") or
                      success_spec.get("fingertip_targets") or
                      success_spec.get("targets") or {})
        required_fingers = ("index", "middle", "ring", "thumb")
        if set(target_map) != set(required_fingers):
            return False, "success spec must provide all four fingertip targets", {}
        samples = before_state.get("_physics_samples", []) or []
        if not samples or any(not s.get("finite") for s in samples):
            return False, "fingertip trace contains no complete finite physics run", {}
        if any(not isinstance(s.get("fingertips"), dict) or
               set(s["fingertips"]) != set(required_fingers) for s in samples):
            return False, "no complete MuJoCo fingertip trace", {}
        final = next((s.get("fingertips") for s in reversed(samples)
                      if isinstance(s.get("fingertips"), dict)), None)
        initial = before_state.get("fingertips")
        if not isinstance(final, dict) or not isinstance(initial, dict):
            return False, "no complete MuJoCo fingertip trace", {}
        tolerance = float(success_spec.get("per_finger_tolerance_m",
                                           success_spec.get("tolerance_m", 0.00894427191)))
        aggregate_tolerance = float(success_spec.get("aggregate_tolerance_m",
                                                      0.00894427191))
        min_motion = float(success_spec.get("min_finger_motion_m", 0.01))
        errors, displacements = {}, {}
        finite = True
        for finger in required_fingers:
            goal = np.asarray(target_map[finger], dtype=float)
            point = np.asarray(final.get(finger), dtype=float)
            start = np.asarray(initial.get(finger), dtype=float)
            finite = finite and goal.shape == (3,) and point.shape == (3,) \
                and start.shape == (3,) and np.isfinite(goal).all() \
                and np.isfinite(point).all() and np.isfinite(start).all()
            if not finite:
                continue
            errors[finger] = float(np.linalg.norm(point - goal))
            displacements[finger] = float(np.linalg.norm(point - start))
        aggregate = float(np.linalg.norm(
            np.concatenate([np.asarray(final[f], dtype=float) -
                            np.asarray(target_map[f], dtype=float)
                            for f in required_fingers])
        )) if finite else float("inf")
        reached = finite and all(err <= tolerance for err in errors.values())
        moved = finite and all(disp >= min_motion for disp in displacements.values())
        ok = bool(reached and moved and aggregate <= aggregate_tolerance)
        return (ok,
                f"fingertip errors={errors}; aggregate={aggregate:.6f} m; "
                f"displacements={displacements}",
                {"per_finger_error_m": errors,
                 "aggregate_error_m": aggregate,
                 "per_finger_displacement_m": displacements,
                 "all_fingers_moved": bool(moved),
                 "trace_finite": bool(finite)})

    if typ == "mobile_manipulation_ordered":
        samples = before_state.get("_physics_samples", []) or []
        if len(samples) < 2 or any(not s.get("finite") for s in samples):
            return False, "no complete MuJoCo mobile-manipulator trace", {}
        valid = list(samples)
        base_xyz = [np.asarray(s["base_xyz"], dtype=float) for s in valid
                    if s.get("base_xyz") is not None]
        ee_xyz = [np.asarray(s["ee"], dtype=float) for s in valid
                  if s.get("ee") is not None]
        arm_q = [np.asarray(s["arm_qpos"], dtype=float) for s in valid
                 if s.get("arm_qpos") is not None]
        if len(base_xyz) != len(valid) or len(ee_xyz) != len(valid) \
                or len(arm_q) != len(valid):
            return False, "mobile trace lacks base, EE, or arm-joint truth", {}
        base_target = np.asarray(success_spec["base_target_xy"], dtype=float)
        ee_target = np.asarray(success_spec["ee_target_xyz"], dtype=float)
        base_tol = float(success_spec.get("base_tolerance_m", 0.025))
        ee_tol = float(success_spec.get("ee_tolerance_m", 0.025))
        min_base = float(success_spec.get("min_base_motion_m", 0.05))
        arm_threshold = float(success_spec.get("arm_motion_threshold", 0.01))
        initial_base = base_xyz[0]
        initial_arm = arm_q[0]
        base_displacements = [float(np.linalg.norm(p[:2] - initial_base[:2]))
                              for p in base_xyz]
        peak_base = max(base_displacements, default=0.0)
        base_target_dist = [float(np.linalg.norm(p[:2] - base_target))
                            for p in base_xyz]
        arm_motion = [float(np.linalg.norm(q - initial_arm)) for q in arm_q]
        first_arm = next((i for i, value in enumerate(arm_motion)
                          if value >= arm_threshold), None)
        base_at_first = (first_arm is not None and
                         base_displacements[first_arm] >= min_base and
                         base_target_dist[first_arm] <= base_tol)
        drift_tol = float(success_spec.get("arm_base_drift_tolerance_m", 0.01))
        arm_base_drift = (max(float(np.linalg.norm(p[:2] - base_xyz[first_arm][:2]))
                              for p in base_xyz[first_arm:])
                          if first_arm is not None else float("inf"))
        base_stays_in_region = (first_arm is not None and
                                all(value <= base_tol
                                    for value in base_target_dist[first_arm:]))
        order_ok = bool(base_at_first and base_stays_in_region
                        and arm_base_drift <= drift_tol)
        final_base_ok = base_target_dist[-1] <= base_tol
        ee_err = float(np.linalg.norm(ee_xyz[-1] - ee_target))
        arm_moved = max(arm_motion, default=0.0) >= arm_threshold
        ok = bool(order_ok and final_base_ok and arm_moved and ee_err <= ee_tol)
        return (ok,
                f"base peak={peak_base:.4f}m target_err={base_target_dist[-1]:.4f}m; "
                f"first arm={first_arm}; EE err={ee_err:.4f}m",
                {"base_peak_motion_m": peak_base,
                 "base_target_error_m": base_target_dist[-1],
                 "first_arm_motion_sample": first_arm,
                 "arm_peak_motion": max(arm_motion, default=0.0),
                 "arm_base_drift_m": arm_base_drift,
                 "ee_error_m": ee_err,
                 "ordered": bool(order_ok),
                 "base_at_first_arm": bool(base_at_first),
                 "base_stays_in_region": bool(base_stays_in_region)})

    if typ == "bimanual_reach_hold":
        samples = before_state.get("_physics_samples", []) or []
        if (not samples or any(not s.get("finite") for s in samples)
                or any(not isinstance(s.get("ees"), dict) or
                       set(s["ees"]) < {"left", "right"} for s in samples)):
            return False, "no complete finite MuJoCo bimanual trace", {}
        valid = list(samples)
        targets = (success_spec.get("targets") or {
            "left": success_spec.get("left_target_xyz"),
            "right": success_spec.get("right_target_xyz"),
        })
        if (set(targets) != {"left", "right"} or
                any(targets[side] is None for side in ("left", "right"))):
            return False, "success spec must provide left and right targets", {}
        target = {side: np.asarray(targets[side], dtype=float)
                  for side in ("left", "right")}
        tol = float(success_spec.get("tolerance_m", 0.025))
        min_motion = float(success_spec.get("min_ee_motion_m", 0.01))
        start = {side: np.asarray(valid[0]["ees"][side], dtype=float)
                 for side in ("left", "right")}
        final = {side: np.asarray(valid[-1]["ees"][side], dtype=float)
                 for side in ("left", "right")}
        errors = {side: float(np.linalg.norm(final[side] - target[side]))
                  for side in ("left", "right")}
        displacement = {side: max(float(np.linalg.norm(np.asarray(s["ees"][side], dtype=float)
                                                      - start[side]))
                                  for s in valid)
                        for side in ("left", "right")}
        both_in = [all(float(np.linalg.norm(np.asarray(s["ees"][side], dtype=float)
                                             - target[side])) <= tol
                       for side in ("left", "right")) for s in valid]
        hold_s = float(success_spec.get("hold_s", success_spec.get("hold_duration_s", 0.5)))
        longest = 0.0
        start_time = None
        for state, in_target in zip(valid, both_in, strict=True):
            current_time = float(state["time"])
            if in_target:
                if start_time is None:
                    start_time = current_time
                longest = max(longest, current_time - start_time)
            else:
                start_time = None
        ok = bool(all(err <= tol for err in errors.values())
                  and all(value >= min_motion for value in displacement.values())
                  and longest + 1e-9 >= hold_s)
        return (ok,
                f"left/right errors={errors}; motion={displacement}; "
                f"simultaneous hold={longest:.3f}s (need {hold_s:.3f}s)",
                {"per_arm_error_m": errors,
                 "per_arm_displacement_m": displacement,
                 "simultaneous_hold_s": longest,
                 "hold_s": hold_s})

    if typ == "llm_reports_failure":
        summary = (llm_result.summary or "").lower()
        any_word = success_spec.get("must_contain_any", [])
        hit = next((w for w in any_word if w in summary), None)
        ok = hit is not None
        return ok, f"matched word: {hit}", {"hit": hit}

    if typ == "object_lifted_by":
        # Optional: require that the lifted object is the y-argmax of accept_objects
        require_y_argmax = success_spec.get("require_correct_choice_y_argmax", False)
        # The lifted object must still be HELD at the gripper. A real grasp keeps
        # the object within held_tol of the end-effector; this rejects a flung /
        # launched object that merely rose in Z (e.g. a weld grasp activated
        # without capturing the relative pose, which throws the object metres
        # away while it still passes a height-only check).
        held_tol = float(success_spec.get("held_tolerance_m", 0.12))
        try:
            ee_now = np.asarray(skel.get_ee_pose()[0], dtype=float)
        except Exception:  # noqa: BLE001
            ee_now = None
        correct_obj = None
        if require_y_argmax:
            ys = {}
            for obj in success_spec.get("accept_objects", []):
                ys[obj] = float(np.array(before_state["objects"][obj])[1])
            correct_obj = max(ys, key=ys.get)
        lifted_held = []   # objects that BOTH rose AND stayed at the gripper
        lifted_flung = []  # rose but flung away (for diagnostics)
        for obj in success_spec.get("accept_objects", []):
            try:
                pos = _obj_pos(skel, obj)
                before = np.array(before_state["objects"][obj])
                dz = float(pos[2] - before[2])
                if dz <= float(success_spec["tolerance_m"]):
                    continue
                gap = float(np.linalg.norm(pos - ee_now)) if ee_now is not None else 0.0
                if ee_now is not None and gap > held_tol:
                    lifted_flung.append((obj, dz, gap))
                else:
                    lifted_held.append((obj, dz, gap))
            except Exception:  # noqa: BLE001
                continue
        if not lifted_held:
            if lifted_flung:
                o, dz, gap = lifted_flung[0]
                return False, (f"{o} rose {dz*100:.1f}cm but ended {gap*100:.1f}cm "
                               f"from the gripper (>{held_tol*100:.0f}cm: flung, "
                               f"not held)"), {"object": o, "dz_m": dz, "gap_m": gap}
            return False, "no accepted object was lifted and held", {}
        held_names = [o for o, _, _ in lifted_held]
        if require_y_argmax and correct_obj not in held_names:
            return False, (f"correct y-argmax object {correct_obj} was not "
                           f"lifted-and-held (held: {held_names})"), \
                {"expected": correct_obj, "held": held_names}
        obj, dz, gap = (next(x for x in lifted_held if x[0] == correct_obj)
                        if require_y_argmax else lifted_held[0])
        return True, f"{obj} lifted {dz*100:.1f} cm (held {gap*100:.1f}cm)", \
            {"object": obj, "dz_m": dz, "gap_m": gap}

    if typ == "ee_at_midpoint":
        objs = success_spec["objects"]
        positions = [np.array(before_state["objects"][o]) for o in objs]
        midpoint = np.mean(positions, axis=0)
        midpoint[2] += 0.05
        ee, _ = skel.get_ee_pose()
        err = float(np.linalg.norm(ee - midpoint))
        ok = err < float(success_spec["tolerance_m"])
        return ok, f"EE to midpoint err {err*100:.1f} cm", {"err_m": err}

    if typ == "ee_waypoint_trace":
        # Verify the EE physically VISITED an ordered set of waypoints
        # (offsets from the home pose) and then RETURNED home. Used by the
        # "draw a shape" trajectory task: unlike ee_pose_returned_home this
        # confirms the strokes were actually drawn, not just the return.
        # Reads the per-call EE trajectory captured during replay.
        home = np.array(before_state["ee_pose"], dtype=float)
        wps = [home + np.array(w, dtype=float) for w in success_spec["waypoints"]]
        tol = float(success_spec.get("waypoint_tol_m", 0.025))
        home_tol = float(success_spec.get("home_tol_m", 0.04))
        snaps = before_state.get("_phase_snapshots", []) or []
        traj = [np.array(s["ee"], dtype=float) for s in snaps if s.get("ee") is not None]
        reached, cursor = [], 0
        for wp in wps:  # match each waypoint in order along the trajectory
            found = -1
            for j in range(cursor, len(traj)):
                if float(np.linalg.norm(traj[j] - wp)) < tol:
                    found = j
                    break
            reached.append(found >= 0)
            if found >= 0:
                cursor = found + 1
        ee_final = np.array(skel.get_ee_pose()[0], dtype=float)
        home_err = float(np.linalg.norm(ee_final - home))
        returned = home_err < home_tol
        ok = bool(all(reached) and returned)
        return (ok,
                f"waypoints {sum(reached)}/{len(wps)} reached in order; "
                f"home_err={home_err*100:.1f}cm ({'home' if returned else 'NOT home'})",
                {"waypoints_reached": int(sum(reached)), "n_waypoints": len(wps),
                 "home_err_m": home_err})

    if typ == "object_above_object":
        top = _obj_pos(skel,success_spec["top"])
        bottom = _obj_pos(skel,success_spec["bottom"])
        # The top object must sit a real-but-BOUNDED gap above the bottom: high
        # enough to be stacked (min_dz), but not flung/floating metres up
        # (max_dz). A height-only "top.z > bottom.z" check is satisfied by an
        # object launched into the air, so bound the gap.
        dz = float(top[2] - bottom[2])
        min_dz = float(success_spec.get("min_dz_m", 0.005))
        max_dz = float(success_spec.get("max_dz_m", 0.15))
        z_above = bool(min_dz < dz < max_dz)
        xy_dist = float(np.linalg.norm(top[:2] - bottom[:2]))
        ok = bool(z_above and xy_dist < float(success_spec["xy_tolerance_m"]))
        return (ok,
                f"{success_spec['top']} above {success_spec['bottom']}: "
                f"dz={dz*100:.1f}cm (need {min_dz*100:.1f}<dz<{max_dz*100:.0f}); "
                f"xy={xy_dist*100:.1f}cm",
                {"dz_m": dz, "z_above": z_above, "xy_dist_m": xy_dist})

    if typ == "object_close_to_object_xy":
        a = _obj_pos(skel,success_spec["obj_a"])
        b = _obj_pos(skel,success_spec["obj_b"])
        d = float(np.linalg.norm(a[:2] - b[:2]))
        # xy proximity alone is satisfied by obj_a floating in the air directly
        # above obj_b; require their heights to also be close (a placed/nested
        # object), so a flung-up object is rejected.
        dz = float(abs(a[2] - b[2]))
        z_tol = float(success_spec.get("z_tolerance_m", 0.10))
        ok = bool(d < float(success_spec["xy_tolerance_m"]) and dz < z_tol)
        return ok, f"xy dist {d*100:.1f} cm, |dz| {dz*100:.1f} cm (need <{z_tol*100:.0f})", \
            {"xy_m": d, "dz_m": dz}

    if typ == "object_did_not_move":
        before = np.array(before_state["objects"][success_spec["object"]])
        now = _obj_pos(skel,success_spec["object"])
        moved = float(np.linalg.norm(now - before))
        bottle_ok = moved < float(success_spec["max_displacement_m"])
        ee, _ = skel.get_ee_pose()
        target = np.array(success_spec["ee_target"])
        ee_err = float(np.linalg.norm(ee - target))
        ee_ok = ee_err < float(success_spec["ee_tolerance_m"])
        ok = bottle_ok and ee_ok
        return (ok,
                f"obj moved {moved*100:.1f}cm; EE err {ee_err*100:.1f}cm",
                {"obj_moved_m": moved, "ee_err_m": ee_err})

    if typ == "objects_swapped":
        a_now = _obj_pos(skel,success_spec["obj_a"])
        b_now = _obj_pos(skel,success_spec["obj_b"])
        a_target = np.array(before_state["objects"][success_spec["obj_b"]])
        b_target = np.array(before_state["objects"][success_spec["obj_a"]])
        a_err = float(np.linalg.norm(a_now[:2] - a_target[:2]))
        b_err = float(np.linalg.norm(b_now[:2] - b_target[:2]))
        ok = a_err < float(success_spec["xy_tolerance_m"]) and \
             b_err < float(success_spec["xy_tolerance_m"])
        return ok, f"a_err {a_err*100:.1f}cm b_err {b_err*100:.1f}cm", {
            "a_err_m": a_err, "b_err_m": b_err
        }

    if typ == "object_placed_near_target":
        # Optional settle
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        obj_pos = _obj_pos(skel,success_spec["object"])
        target = np.array(success_spec["target_xy"])
        err = float(np.linalg.norm(obj_pos[:2] - target))
        # "Placed" means resting near the target, not held aloft or flung up:
        # reject an object that ended well above its start height.
        before_z = float(np.array(before_state["objects"][success_spec["object"]])[2])
        rise = float(obj_pos[2] - before_z)
        max_rise = float(success_spec.get("max_rise_m", 0.08))
        ok = bool(err < float(success_spec["xy_tolerance_m"]) and rise < max_rise)
        return ok, f"xy err {err*100:.1f} cm, rise {rise*100:+.1f} cm (need <{max_rise*100:.0f})", \
            {"err_m": err, "rise_m": rise}

    if typ == "objects_in_tower":
        # Verify each object_i is meaningfully above object_{i-1} (z gap > min_dz)
        # and xy-aligned. min_dz prevents trivial pass when upper barely floats
        # at same height as lower.
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        stack = success_spec["stack"]
        tol = float(success_spec["xy_tolerance_m"])
        min_dz = float(success_spec.get("min_dz_m", 0.01))  # require ≥ 1 cm vertical gap
        # Upper bound too: a stacked object sits ~one object-height above the one
        # below, not flung metres up. Without this, a launched object that is
        # xy-aligned passes the height check.
        max_dz = float(success_spec.get("max_dz_m", 0.15))
        positions = {name: _obj_pos(skel,name) for name in stack}
        ok = True
        details = []
        for lower, upper in zip(stack[:-1], stack[1:]):
            xy = float(np.linalg.norm(positions[upper][:2] - positions[lower][:2]))
            z = float(positions[upper][2] - positions[lower][2])
            details.append(f"{upper}>{lower}: dxy={xy*100:.1f}cm dz={z*100:.1f}cm")
            if not (xy < tol and min_dz < z < max_dz):
                ok = False
        return ok, "; ".join(details), {"stack": stack, "positions": {k: v.tolist() for k, v in positions.items()}}

    if typ == "two_swaps_completed":
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        pair_a = success_spec["pair_a"]
        pair_b = success_spec["pair_b"]
        tol = float(success_spec["xy_tolerance_m"])
        before_obj = before_state["objects"]
        # pair_a: a should be at b's original XY, b at a's original XY
        a, b = pair_a
        ax_now = _obj_pos(skel,a)[:2]
        bx_now = _obj_pos(skel,b)[:2]
        ax_target = np.array(before_obj[b][:2])
        bx_target = np.array(before_obj[a][:2])
        ea1 = float(np.linalg.norm(ax_now - ax_target))
        eb1 = float(np.linalg.norm(bx_now - bx_target))
        # pair_b: c at d's original, d at c's original
        c, d = pair_b
        cx_now = _obj_pos(skel,c)[:2]
        dx_now = _obj_pos(skel,d)[:2]
        cx_target = np.array(before_obj[d][:2])
        dx_target = np.array(before_obj[c][:2])
        ea2 = float(np.linalg.norm(cx_now - cx_target))
        eb2 = float(np.linalg.norm(dx_now - dx_target))
        ok = all(e < tol for e in [ea1, eb1, ea2, eb2])
        return ok, (f"{a}↔{b}: {ea1*100:.1f},{eb1*100:.1f}cm; "
                    f"{c}↔{d}: {ea2*100:.1f},{eb2*100:.1f}cm"), {
            "errs_cm": [ea1*100, eb1*100, ea2*100, eb2*100]
        }

    if typ == "cleared_and_visited":
        # 1) stay objects didn't move
        # 2) EE final pose near cleared object's ORIGINAL XY (+ 5 cm lift in Z),
        #    matching the prompt's "(its starting XY, Z + 5 cm)" target.
        cleared = success_spec["clear_object"]
        stay = success_spec.get("stay_objects", [])
        max_disp = float(success_spec["max_displacement_m"])
        ee_tol = float(success_spec["ee_tolerance_m"])
        ee_z_offset = float(success_spec.get("ee_z_offset_m", 0.05))
        before_obj = before_state["objects"]
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        max_stay_err = 0.0
        for name in stay:
            disp = float(np.linalg.norm(
                np.array(_obj_pos(skel,name)) - np.array(before_obj[name])))
            max_stay_err = max(max_stay_err, disp)
        stay_ok = max_stay_err < max_disp
        target = np.array(before_obj[cleared]).copy()
        target[2] += ee_z_offset  # match prompt's +5 cm lift
        ee, _ = skel.get_ee_pose()
        ee_err = float(np.linalg.norm(ee - target))
        ee_ok = ee_err < ee_tol
        ok = stay_ok and ee_ok
        return ok, (f"max stay-disp {max_stay_err*100:.1f}cm "
                    f"(<{max_disp*100:.0f}); EE-err {ee_err*100:.1f}cm "
                    f"(<{ee_tol*100:.0f}, target z+{ee_z_offset*100:.0f}cm)"), {
            "max_stay_disp_m": max_stay_err, "ee_err_m": ee_err
        }

    if typ == "object_pose_match":
        # Push task: object should reach target XY without lifting.
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        obj = success_spec["object"]
        target_xy = np.array(success_spec["target_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.02))
        before_z = float(np.array(before_state["objects"][obj])[2])
        now = _obj_pos(skel,obj)
        xy_err = float(np.linalg.norm(now[:2] - target_xy))
        z_rise = float(now[2] - before_z)
        pos_ok = xy_err < xy_tol
        not_lifted = z_rise < max_z
        ok = pos_ok and not_lifted
        return ok, (f"xy_err={xy_err*100:.1f}cm (<{xy_tol*100:.0f}); "
                    f"z_rise={z_rise*100:.1f}cm (<{max_z*100:.0f})"), {
            "xy_err_m": xy_err, "z_rise_m": z_rise
        }

    if typ == "object_pose_match_relative":
        # Arm-agnostic: target is start_xy + delta_xy
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        obj = success_spec["object"]
        delta = np.array(success_spec["delta_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.02))
        before = np.array(before_state["objects"][obj])
        target_xy = before[:2] + delta
        now = _obj_pos(skel,obj)
        xy_err = float(np.linalg.norm(now[:2] - target_xy))
        z_rise = float(now[2] - before[2])
        ok = xy_err < xy_tol and z_rise < max_z and z_rise > -0.05  # didn't fall off
        return ok, (f"xy_err={xy_err*100:.1f}cm (<{xy_tol*100:.0f}); "
                    f"z_rise={z_rise*100:.1f}cm (<{max_z*100:.0f}); "
                    f"target rel start={delta.tolist()}"), {
            "xy_err_m": xy_err, "z_rise_m": z_rise
        }

    if typ == "push_relative_obstacle_safe":
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        push_obj = success_spec["push_object"]
        obstacle = success_spec["obstacle"]
        delta = np.array(success_spec["delta_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_obs_disp = float(success_spec["max_obstacle_displacement_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.02))
        before = np.array(before_state["objects"][push_obj])
        target_xy = before[:2] + delta
        now = _obj_pos(skel,push_obj)
        xy_err = float(np.linalg.norm(now[:2] - target_xy))
        z_rise = float(now[2] - before[2])
        obs_disp = float(np.linalg.norm(
            np.array(_obj_pos(skel,obstacle))
            - np.array(before_state["objects"][obstacle])))
        ok = (xy_err < xy_tol and z_rise < max_z and z_rise > -0.05
              and obs_disp < max_obs_disp)
        return ok, (f"push xy_err={xy_err*100:.1f}cm (<{xy_tol*100:.0f}); "
                    f"z_rise={z_rise*100:.1f}cm; "
                    f"obs_disp={obs_disp*100:.1f}cm (<{max_obs_disp*100:.0f})"), {
            "xy_err_m": xy_err, "z_rise_m": z_rise, "obs_disp_m": obs_disp
        }

    if typ == "all_objects_translated":
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        objects = success_spec["objects"]
        delta = np.array(success_spec["delta_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.03))
        details = []
        ok = True
        for obj in objects:
            before = np.array(before_state["objects"][obj])
            target_xy = before[:2] + delta
            now = _obj_pos(skel,obj)
            xy_err = float(np.linalg.norm(now[:2] - target_xy))
            z_rise = float(now[2] - before[2])
            details.append(f"{obj}: xy_err={xy_err*100:.1f}cm z_rise={z_rise*100:.1f}cm")
            if not (xy_err < xy_tol and z_rise < max_z and z_rise > -0.05):
                ok = False
        return ok, "; ".join(details), {"objects": objects, "delta": delta.tolist()}

    if typ == "ee_returned_no_object_disturbance":
        max_obj_disp = float(success_spec["max_object_displacement_m"])
        ee_tol = float(success_spec["ee_tolerance_m"])
        tracked = success_spec["tracked_objects"]
        ee, _ = skel.get_ee_pose()
        ee_err = float(np.linalg.norm(ee - np.array(before_state["ee_pose"])))
        max_disp = 0.0
        details = []
        for obj in tracked:
            try:
                disp = float(np.linalg.norm(
                    np.array(_obj_pos(skel,obj))
                    - np.array(before_state["objects"][obj])))
                max_disp = max(max_disp, disp)
                if disp > 0.005:
                    details.append(f"{obj}:{disp*100:.1f}cm")
            except Exception:
                pass
        ok = ee_err < ee_tol and max_disp < max_obj_disp
        return ok, (f"ee_err={ee_err*100:.1f}cm (<{ee_tol*100:.0f}); "
                    f"max_obj_disp={max_disp*100:.1f}cm (<{max_obj_disp*100:.0f}); "
                    f"moved=[{','.join(details)}]"), {
            "ee_err_m": ee_err, "max_obj_disp_m": max_disp
        }

    if typ == "object_slid_not_lifted":
        # Same as object_pose_match (kept as alias for prompt clarity).
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        obj = success_spec["object"]
        target_xy = np.array(success_spec["target_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.02))
        before_z = float(np.array(before_state["objects"][obj])[2])
        now = _obj_pos(skel,obj)
        xy_err = float(np.linalg.norm(now[:2] - target_xy))
        z_rise = float(now[2] - before_z)
        ok = xy_err < xy_tol and z_rise < max_z
        return ok, (f"xy_err={xy_err*100:.1f}cm; z_rise={z_rise*100:.1f}cm"), {
            "xy_err_m": xy_err, "z_rise_m": z_rise
        }

    if typ == "push_to_target_obstacle_safe":
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        push_obj = success_spec["push_object"]
        obstacle = success_spec["obstacle"]
        target_xy = np.array(success_spec["target_xy"])
        xy_tol = float(success_spec["xy_tolerance_m"])
        max_obs_disp = float(success_spec["max_obstacle_displacement_m"])
        max_z = float(success_spec.get("max_z_rise_m", 0.02))
        push_now = _obj_pos(skel,push_obj)
        before_push_z = float(np.array(before_state["objects"][push_obj])[2])
        xy_err = float(np.linalg.norm(push_now[:2] - target_xy))
        z_rise = float(push_now[2] - before_push_z)
        obs_disp = float(np.linalg.norm(
            np.array(_obj_pos(skel,obstacle))
            - np.array(before_state["objects"][obstacle])))
        ok = (xy_err < xy_tol and z_rise < max_z and obs_disp < max_obs_disp)
        return ok, (f"push xy_err={xy_err*100:.1f}cm (<{xy_tol*100:.0f}); "
                    f"z_rise={z_rise*100:.1f}cm; obs_disp={obs_disp*100:.1f}cm "
                    f"(<{max_obs_disp*100:.0f})"), {
            "xy_err_m": xy_err, "z_rise_m": z_rise, "obs_disp_m": obs_disp
        }

    if typ == "all_objects_in_halfspace":
        for _ in range(int(success_spec.get("settle_steps", 0))):
            skel.step(1)
        objects = success_spec["objects"]
        axis_name = success_spec["axis"]
        threshold = float(success_spec["threshold"])
        comparison = success_spec["comparison"]  # 'greater' or 'less'
        max_z = float(success_spec.get("max_z_rise_m", 0.03))
        ax_idx = {"x": 0, "y": 1, "z": 2}[axis_name]
        details = []
        ok = True
        for obj in objects:
            now = _obj_pos(skel,obj)
            val = float(now[ax_idx])
            before_z = float(np.array(before_state["objects"][obj])[2])
            z_rise = float(now[2] - before_z)
            cond_ok = (val > threshold) if comparison == "greater" else (val < threshold)
            details.append(f"{obj}:{axis_name}={val*100:.1f}cm (z_rise={z_rise*100:.1f})")
            if not (cond_ok and z_rise < max_z):
                ok = False
        return ok, "; ".join(details), {"objects": objects}

    if typ == "ee_returned_obstacle_safe":
        # EE returned to home + obstacle didn't move.
        max_obs_disp = float(success_spec["max_obstacle_displacement_m"])
        ee_tol = float(success_spec["ee_tolerance_m"])
        obstacle = success_spec["obstacle"]
        ee, _ = skel.get_ee_pose()
        ee_err = float(np.linalg.norm(ee - np.array(before_state["ee_pose"])))
        obs_disp = float(np.linalg.norm(
            np.array(_obj_pos(skel,obstacle))
            - np.array(before_state["objects"][obstacle])))
        ok = ee_err < ee_tol and obs_disp < max_obs_disp
        return ok, (f"ee_err={ee_err*100:.1f}cm (<{ee_tol*100:.0f}); "
                    f"obs_disp={obs_disp*100:.1f}cm (<{max_obs_disp*100:.0f})"), {
            "ee_err_m": ee_err, "obs_disp_m": obs_disp
        }

    if typ == "body_height_above":
        h = float(skel.get_body_height())
        ok = h > float(success_spec["threshold_m"])
        return ok, f"body height {h:.3f} m", {"height_m": h}

    if typ == "body_height_below":
        h = float(skel.get_body_height())
        ok = h < float(success_spec["threshold_m"])
        return ok, f"body height {h:.3f} m", {"height_m": h}

    if typ == "forward_displacement_at_least":
        # Need before pose; we recorded `base_pose` in before_state
        before = np.array(before_state["base_pose_xyz"])
        now, _ = skel.get_base_pose()
        dx = float(now[0] - before[0])
        z_ok = (not success_spec.get("must_not_fall", False)) or (now[2] > 0.10)
        ok = dx >= float(success_spec["min_m"]) and z_ok
        return ok, f"dx={dx:+.3f} m, body z={now[2]:.3f}", {"dx_m": dx, "z_m": float(now[2])}

    if typ == "ee_visited_n_positions":
        # Physics-grounded coverage: the END-EFFECTOR must have physically
        # come within tolerance_m (horizontal) of >= n distinct scene objects
        # during the replay, verified against the per-call EE trajectory and
        # the objects' true world positions — NOT merely the agent's commanded
        # move_cartesian targets.
        #
        # NOTE (paper v1): the v1 reported number for this single task used a
        # tool-call coverage criterion (n unique commanded targets). This task
        # is EXCLUDED from the 12-task physics-only headline (App. robustness),
        # so the reported model ranking does not depend on this grader. The
        # released benchmark uses the stricter physics check below; if the
        # per-call EE trajectory is unavailable it falls back to the coverage
        # criterion (and says so in the detail string).
        n = int(success_spec["n_required"])
        tol = float(success_spec.get("tolerance_m", 0.06))
        snaps = before_state.get("_phase_snapshots", []) or []
        traj = [np.array(s["ee"], dtype=float) for s in snaps if s.get("ee") is not None]
        # Scene object world positions come from the harness-captured
        # before_state["objects"] (driver-agnostic — the same source every
        # other object grader uses; the SO-101 skeleton does not expose
        # get_object_names()).
        obj_xy = []
        for name, p in (before_state.get("objects") or {}).items():
            obj_xy.append((name, np.asarray(p, dtype=float)[:2]))
        if traj and obj_xy:
            visited = []
            for name, xy in obj_xy:
                hit = any(float(np.linalg.norm(p[:2] - xy)) < tol for p in traj)
                if hit:
                    visited.append(name)
            ok = bool(len(visited) >= n)
            return ok, (f"EE physically visited {len(visited)}/{len(obj_xy)} objects "
                        f"within {tol*100:.0f} cm (need >={n}): {visited}"), \
                {"physically_visited": visited, "n_objects": len(obj_xy)}
        # fallback: commanded-target coverage (no EE trajectory available)
        targets = set()
        for c in llm_result.tool_call_log:
            if c["tool"] == "move_cartesian":
                inp = c["input"]
                targets.add((round(float(inp["x"]), 2), round(float(inp["y"]), 2)))
        ok = len(targets) >= n
        return ok, f"[fallback: commanded coverage] unique targets={len(targets)}", \
            {"unique_targets": list(targets)}

    if typ == "multi_phase_height_trajectory":
        # We can't directly measure phases without timestamped state.
        # Use a heuristic: agent must call stand_up + sit at least the right
        # number of times.
        calls = [c["tool"] for c in llm_result.tool_call_log]
        n_stand = sum(1 for t in calls if t == "stand_up")
        n_sit = sum(1 for t in calls if t == "sit")
        expected_stand = sum(1 for p in success_spec["phases"] if p == "stand")
        expected_sit = sum(1 for p in success_spec["phases"] if p == "sit")
        ok = n_stand >= expected_stand and n_sit >= expected_sit
        return ok, f"stand_up calls={n_stand} (need {expected_stand}), sit calls={n_sit} (need {expected_sit})", {
            "n_stand": n_stand, "n_sit": n_sit
        }

    if typ == "llm_reports_pose":
        summary = (llm_result.summary or "").lower()
        hit = next((w for w in success_spec["must_contain_any"] if w in summary), None)
        return hit is not None, f"matched: {hit}", {"hit": hit}

    # ── Quadruped v2: physics-grounded locomotion (height-ratio,
    #    forward-progress band, and snapshot-based phase trajectories) ──────
    if typ == "body_height_ratio":
        h0 = float(before_state.get("body_height") or 0.0)
        h1 = float(skel.get_body_height())
        r = h1 / h0 if h0 > 1e-6 else 0.0
        ok = True
        if "max_ratio" in success_spec:
            ok = ok and r <= float(success_spec["max_ratio"])
        if "min_ratio" in success_spec:
            ok = ok and r >= float(success_spec["min_ratio"])
        return ok, f"height ratio {r:.2f} ({h0:.3f}->{h1:.3f} m)", {"height_ratio": r}

    if typ == "forward_progress":
        x0 = float(np.array(before_state["base_pose_xyz"])[0])
        y0 = float(np.array(before_state["base_pose_xyz"])[1])
        pos, _ = skel.get_base_pose()
        dx = float(pos[0] - x0); dy = float(pos[1] - y0)
        h0 = float(before_state.get("body_height") or 0.0)
        hr = (float(skel.get_body_height()) / h0) if h0 > 1e-6 else 1.0
        ok = dx >= float(success_spec["x_min_m"])
        if "x_max_m" in success_spec:
            ok = ok and dx <= float(success_spec["x_max_m"])
        if "abs_y_max_m" in success_spec:
            ok = ok and abs(dy) <= float(success_spec["abs_y_max_m"])
        if "height_ratio_min" in success_spec:
            ok = ok and hr >= float(success_spec["height_ratio_min"])
        return ok, f"dx={dx:+.3f} m, |dy|={abs(dy):.3f}, height_ratio={hr:.2f}", {
            "dx_m": dx, "dy_m": dy, "height_ratio": hr}

    if typ == "turn_to_heading":
        # Wheeled/mobile base: did the base rotate to the requested heading
        # change (signed yaw delta from start)?
        def _yaw():
            if hasattr(skel, "get_base_yaw"):
                return float(skel.get_base_yaw())
            R = np.asarray(skel.get_base_pose()[1], dtype=float)
            return float(np.arctan2(R[1, 0], R[0, 0]))
        yaw0 = float(before_state.get("base_yaw", 0.0))
        dyaw = ((_yaw() - yaw0 + np.pi) % (2 * np.pi)) - np.pi
        target = float(success_spec["delta_rad"])
        err = abs(((dyaw - target + np.pi) % (2 * np.pi)) - np.pi)
        tol = float(success_spec.get("tol_rad", 0.26))  # ~15 deg
        ok = bool(err < tol)
        return ok, (f"turned {np.degrees(dyaw):+.0f}deg "
                    f"(target {np.degrees(target):+.0f}, err {np.degrees(err):.0f})"), {
            "dyaw_rad": dyaw, "err_rad": err}

    if typ == "drive_and_turn":
        # Compose: the base must BOTH advance (peak displacement during the
        # run) AND turn (final heading change). Both legs are verified, so a
        # robot that only drives, or only turns, fails. Wheeled-safe.
        snaps = before_state.get("_phase_snapshots", []) or []
        x0 = float(np.array(before_state["base_pose_xyz"])[0])
        y0 = float(np.array(before_state["base_pose_xyz"])[1])
        max_disp = 0.0
        for s in snaps:
            if s.get("xyz") is not None:
                dx = s["xyz"][0] - x0; dy = s["xyz"][1] - y0
                max_disp = max(max_disp, (dx * dx + dy * dy) ** 0.5)
        def _yaw():
            if hasattr(skel, "get_base_yaw"):
                return float(skel.get_base_yaw())
            R = np.asarray(skel.get_base_pose()[1], dtype=float)
            return float(np.arctan2(R[1, 0], R[0, 0]))
        dyaw = abs(((_yaw() - float(before_state.get("base_yaw", 0.0)) + np.pi)
                    % (2 * np.pi)) - np.pi)
        drive_ok = max_disp >= float(success_spec["min_drive_m"])
        turn_ok = dyaw >= float(success_spec["min_turn_rad"])
        ok = bool(drive_ok and turn_ok)
        return ok, (f"drive {max_disp*100:.0f}cm({'ok' if drive_ok else 'X'}), "
                    f"turn {np.degrees(dyaw):.0f}deg({'ok' if turn_ok else 'X'})"), {
            "drive_m": max_disp, "turn_rad": dyaw}

    if typ == "phase_trajectory":
        snaps = before_state.get("_phase_snapshots", []) or []
        x0 = float(np.array(before_state["base_pose_xyz"])[0])
        h0 = float(before_state.get("body_height") or 0.0)

        def _state_at(sel: str):
            """Return (height_ratio, dx) at a snapshot selector:
            'final' | 'after:<tool>:<k>' | 'before:<tool>:<k>'."""
            if sel == "final":
                h1 = float(skel.get_body_height())
                x1 = float(skel.get_base_pose()[0][0])
            else:
                kind, tool, k = sel.split(":"); k = int(k)
                hits = [i for i, s in enumerate(snaps) if s["tool"] == tool]
                if len(hits) < k:
                    return None  # selector unsatisfiable (tool not called enough)
                si = hits[k - 1]
                if kind == "before":
                    si = si - 1
                    if si < 0:
                        h1, x1 = h0, x0  # before the first call = start state
                        return (h1 / h0 if h0 > 1e-6 else 1.0, x1 - x0)
                rec = snaps[si]
                h1 = rec["height"] if rec["height"] is not None else h0
                x1 = rec["xyz"][0] if rec["xyz"] is not None else x0
            return (h1 / h0 if h0 > 1e-6 else 1.0, x1 - x0)

        details, ok_all, dx_by_phase = [], True, {}
        for ph in success_spec["phases"]:
            st = _state_at(ph["at"])
            if st is None:
                ok_all = False; details.append(f"{ph['at']}:UNSAT"); continue
            hr, dx = st
            dx_by_phase[ph["at"]] = dx
            p_ok = True
            if "min_height_ratio" in ph: p_ok = p_ok and hr >= ph["min_height_ratio"]
            if "max_height_ratio" in ph: p_ok = p_ok and hr <= ph["max_height_ratio"]
            if "min_dx_m" in ph: p_ok = p_ok and dx >= ph["min_dx_m"]
            if "max_dx_m" in ph: p_ok = p_ok and dx <= ph["max_dx_m"]
            if "min_progress_retention" in ph:
                ref = dx_by_phase.get(ph["retention_ref"], 0.0)
                ret = (dx / ref) if ref > 1e-6 else 0.0
                p_ok = p_ok and ret >= ph["min_progress_retention"]
                details.append(f"{ph['at']}:ret={ret:.2f}")
            ok_all = ok_all and p_ok
            details.append(f"{ph['at']}:hr={hr:.2f},dx={dx:+.3f}{'' if p_ok else '✗'}")
        return ok_all, "; ".join(details), {"phases": dx_by_phase}

    # ── Aerial / multirotor graders (read post-step floating-base pose) ──────
    if typ in ("reach_3d_target", "takeoff_to_height", "hover_stability",
               "waypoint_3d_trace", "returned_to_start_xy"):
        def _base_pos_up():
            p, R = skel.get_base_pose()
            p = np.asarray(p, dtype=float)
            R = np.asarray(R, dtype=float)
            return p, float(R[2, 2])   # upright = body z-axis .z

        _start = np.array(before_state.get("base_pose_xyz", [0, 0, 0]), dtype=float)

        def _resolve(spec_key_abs, spec_key_off):
            # target is either absolute (target_xyz) or an offset from the
            # drone's own start pose (target_offset_xyz) — the latter is
            # robust to the model's rest altitude.
            if spec_key_off in success_spec:
                return _start + np.array(success_spec[spec_key_off], dtype=float)
            return np.array(success_spec[spec_key_abs], dtype=float)

        if typ == "takeoff_to_height":
            if "target_height_m" in success_spec:
                from autoadapter_bench.physics import grade_takeoff_trace
                return grade_takeoff_trace(
                    before_state.get("_physics_samples", []),
                    target_height=float(success_spec["target_height_m"]),
                    tolerance=float(success_spec["tolerance_m"]),
                    hold_s=float(success_spec["hold_s"]),
                    min_climb=float(success_spec.get("min_climb_m", .15)),
                    min_upright=float(success_spec.get("min_upright", .7)))
            # Grade ABSOLUTE final altitude (matches the prompt "climb to ~X m");
            # the agent flies in world coordinates. (min_climb_m kept as a
            # legacy relative fallback.)
            p, up = _base_pos_up()
            up_ok = up >= float(success_spec.get("min_upright", 0.7))
            if "min_altitude_m" in success_spec:
                ok = bool(float(p[2]) >= float(success_spec["min_altitude_m"]) and up_ok)
                return ok, f"altitude z={p[2]:.3f} m (need >={success_spec['min_altitude_m']}), up={up:.2f}", \
                    {"altitude_m": float(p[2]), "upright": up}
            z0 = float(np.array(before_state.get("base_pose_xyz", [0, 0, 0]))[2])
            dz = float(p[2]) - z0
            ok = bool(dz >= float(success_spec["min_climb_m"]) and up_ok)
            return ok, f"climb dz={dz:+.3f} m (need >={success_spec['min_climb_m']}), up={up:.2f}", \
                {"dz_m": dz, "upright": up}

        if typ == "reach_3d_target":
            p, up = _base_pos_up()
            tgt = _resolve("target_xyz", "target_offset_xyz")
            err = float(np.linalg.norm(p - tgt))
            ok = bool(err < float(success_spec["tol_m"])
                      and up >= float(success_spec.get("min_upright", 0.7)))
            return ok, f"pos={p.round(2).tolist()} target={tgt.tolist()} err={err:.3f}m up={up:.2f}", \
                {"err_m": err, "upright": up}

        if typ == "returned_to_start_xy":
            # Round-trip: must (a) actually fly OUT at least min_excursion_m, then
            # (b) return to within tol_m of start, upright. The excursion check
            # closes the inaction loophole (a drone that just hovers in place
            # would otherwise trivially "return to start").
            p, up = _base_pos_up()
            start = np.array(before_state.get("base_pose_xyz", [0, 0, 0]), dtype=float)
            errxy = float(np.linalg.norm(p[:2] - start[:2]))
            snaps = before_state.get("_phase_snapshots", []) or []
            xyz = [np.array(s["xyz"], dtype=float) for s in snaps if s.get("xyz") is not None]
            peak_excursion = max((float(np.linalg.norm(q[:2] - start[:2])) for q in xyz), default=0.0)
            min_exc = float(success_spec.get("min_excursion_m", 0.25))
            ok = bool(errxy < float(success_spec["tol_m"])
                      and peak_excursion >= min_exc
                      and up >= float(success_spec.get("min_upright", 0.7)))
            return ok, (f"peak excursion {peak_excursion*100:.1f}cm (need >={min_exc*100:.0f}), "
                        f"final drift {errxy*100:.1f}cm (need <{success_spec['tol_m']*100:.0f}), up={up:.2f}"), \
                {"xy_err_m": errxy, "peak_excursion_m": peak_excursion, "upright": up}

        if typ == "hover_stability":
            # final pose near the hover target AND the replayed trajectory never
            # drifted beyond max_drift_m of it (reads base-pose snapshots).
            p, up = _base_pos_up()
            tgt = _resolve("target_xyz", "target_offset_xyz")
            err = float(np.linalg.norm(p - tgt))
            snaps = before_state.get("_phase_snapshots", []) or []
            xyz = [np.array(s["xyz"], dtype=float) for s in snaps if s.get("xyz") is not None]
            max_drift = max((float(np.linalg.norm(q - tgt)) for q in xyz), default=err)
            ok = bool(err < float(success_spec["tol_m"])
                      and max_drift < float(success_spec["max_drift_m"])
                      and up >= float(success_spec.get("min_upright", 0.7)))
            return ok, f"final err={err:.3f}m max_drift={max_drift:.3f}m up={up:.2f}", \
                {"err_m": err, "max_drift_m": max_drift, "upright": up}

        if typ == "waypoint_3d_trace":
            # ordered 3D waypoints visited along the replayed base trajectory,
            # then upright at the end (analogue of ee_waypoint_trace for arms).
            p, up = _base_pos_up()
            _off = _start if success_spec.get("waypoints_relative", True) else np.zeros(3)
            wps = [np.array(w, dtype=float) + _off for w in success_spec["waypoints"]]
            tol = float(success_spec.get("waypoint_tol_m", 0.2))
            snaps = before_state.get("_phase_snapshots", []) or []
            traj = [np.array(s["xyz"], dtype=float) for s in snaps if s.get("xyz") is not None]
            reached, cursor = [], 0
            for wp in wps:
                found = -1
                for j in range(cursor, len(traj)):
                    if float(np.linalg.norm(traj[j] - wp)) < tol:
                        found = j
                        break
                reached.append(found >= 0)
                if found >= 0:
                    cursor = found + 1
            ok = bool(all(reached) and up >= float(success_spec.get("min_upright", 0.7)))
            return ok, f"waypoints reached {sum(reached)}/{len(wps)} in order, up={up:.2f}", \
                {"reached": reached, "upright": up}

    # ── Humanoid / biped graders (read post-step floating-torso pose) ───────
    if typ in ("torso_upright_height", "squat_and_recover", "stayed_in_place"):
        def _torso_up():
            p, R = skel.get_base_pose()
            p = np.asarray(p, dtype=float)
            R = np.asarray(R, dtype=float)
            return p, float(R[2, 2])
        p, up = _torso_up()
        # Prefer the driver's own get_torso_height() for the height check — the
        # generator prompt allows get_base_pose() to be pelvis-or-torso, so a
        # valid pelvis-based driver would otherwise be misgraded on height.
        torso_h = float(p[2])
        if hasattr(skel, "get_torso_height"):
            try:
                rep = float(skel.get_torso_height())
                # Trust the driver's self-reported torso height ONLY if it is
                # physically consistent with the sim-derived base/pelvis z (a
                # torso sits at most ~0.5 m above the pelvis). This stops a
                # driver that returns a constant/fabricated height from masking a
                # collapsed or sunk torso — the height must track real sim state.
                if p[2] - 0.05 <= rep <= p[2] + 0.5:
                    torso_h = rep
            except Exception:  # noqa: BLE001
                pass
        min_up = float(success_spec.get("min_upright", 0.7))

        if typ == "torso_upright_height":
            duration_s = float(success_spec.get("duration_s", 0.0))
            if duration_s > 0.0:
                samples = before_state.get("_physics_samples", []) or []
                if (len(samples) < 2 or any(not s.get("finite") for s in samples)
                        or any(s.get("base_xyz") is None or
                               s.get("base_upright") is None for s in samples)):
                    return False, "no complete torso trajectory captured for timed balance", \
                        {"duration_s": 0.0, "required_duration_s": duration_s}
                elapsed = float(samples[-1]["time"] - samples[0]["time"])
                heights = [float(np.asarray(s["base_xyz"], dtype=float)[2])
                           for s in samples]
                upright_values = [float(s["base_upright"]) for s in samples]
                duration_ok = elapsed + 1e-9 >= duration_s
                trajectory_ok = (min(heights) >= float(success_spec["min_torso_h_m"])
                                  and min(upright_values) >= min_up)
                if not (duration_ok and trajectory_ok):
                    return (False,
                            f"balance elapsed={elapsed:.3f}s (need {duration_s:.3f}), "
                            f"min h={min(heights):.3f}m, min upright={min(upright_values):.3f}",
                            {"duration_s": elapsed,
                             "required_duration_s": duration_s,
                             "min_height_m": min(heights),
                             "min_upright": min(upright_values)})
                # The truth trace is the final pose used for this timed check;
                # the driver-reported height is not allowed to mask a collapse.
                torso_h = heights[-1]
                up = upright_values[-1]
            ok = bool(torso_h >= float(success_spec["min_torso_h_m"]) and up >= min_up)
            return ok, f"torso h={torso_h:.2f}m (need >={success_spec['min_torso_h_m']}), up={up:.2f}", \
                {"torso_h_m": torso_h, "upright": up,
                 "duration_s": elapsed if duration_s > 0 else 0.0,
                 "required_duration_s": duration_s}

        if typ == "stayed_in_place":
            start = np.array(before_state.get("base_pose_xyz", [0, 0, 0]), dtype=float)
            drift = float(np.linalg.norm(p[:2] - start[:2]))
            ok = bool(drift < float(success_spec["max_drift_m"])
                      and torso_h >= float(success_spec["min_torso_h_m"])
                      and up >= min_up)
            return ok, f"xy drift {drift*100:.1f}cm h={torso_h:.2f} up={up:.2f}", \
                {"drift_m": drift, "torso_h_m": torso_h, "upright": up}

        if typ == "squat_and_recover":
            # the torso must dip by >= min_drop_m at some point and recover to
            # near its peak height, staying upright (reads the base-pose
            # trajectory captured during replay).
            snaps = before_state.get("_phase_snapshots", []) or []
            hs = [float(np.array(s["xyz"], dtype=float)[2]) for s in snaps if s.get("xyz") is not None]
            if len(hs) < 2:
                return False, "no torso-height trajectory captured", {"n_snaps": len(hs)}
            h_max, h_min, h_final = max(hs), min(hs), float(p[2])
            lowered = (h_max - h_min) >= float(success_spec["min_drop_m"])
            recovered = h_final >= h_max - float(success_spec.get("recover_tol_m", 0.10))
            ok = bool(lowered and recovered and up >= min_up)
            return ok, (f"dip={h_max-h_min:.2f}m (need >={success_spec['min_drop_m']}), "
                        f"recover h={h_final:.2f}/{h_max:.2f}, up={up:.2f}"), \
                {"dip_m": h_max - h_min, "h_final": h_final, "h_max": h_max, "upright": up}

    raise ValueError(f"unknown success type: {typ!r}")


# ──────────────────────────────────────────────────────────────────────────
# Per-task runner
# ──────────────────────────────────────────────────────────────────────────


def _state_refs_for(robot_dict: dict) -> dict:
    """Return the zoo's trusted MuJoCo truth bindings."""
    return dict(robot_dict.get("state_refs") or {})


def _truth_state(skel, state_refs: dict) -> dict:
    """Sample configured MuJoCo truth and expose legacy evaluator aliases."""
    if not state_refs:
        return {}
    from autoadapter_bench.physics import sample_state  # noqa: PLC0415

    state = sample_state(skel, state_refs)
    if "ee" in state:
        state["ee_pose"] = list(state["ee"])
    if "base_xyz" in state:
        state["base_pose_xyz"] = list(state["base_xyz"])
        state["base_height"] = float(state["base_xyz"][2])
    if "fingertips" in state:
        state["fingertips"] = {
            finger: list(point) for finger, point in state["fingertips"].items()
        }
    if "ees" in state:
        state["ees"] = {
            side: list(point) for side, point in state["ees"].items()
        }
    return state


def _path_str(path) -> str | None:
    return str(path) if path else None


def run_capability_task(planner, robot_dict: dict, task: dict, n_trials: int) -> list[dict]:
    """Use real planner tools and the Framework scorer in the same MuJoCo world."""
    from auto_adapter.robot_catalog import load_capability_suite
    from autoadapter_bench.capability_eval import run_capability_case
    suite = load_capability_suite(robot_dict)
    case = next(item for item in suite["cases"] if item["case_id"] == task["capability_case_id"])
    report_path = planner.workspace / "validate_report.json"
    framework = json.loads(report_path.read_text()) if report_path.exists() else {}
    trials = []
    for trial_idx in range(n_trials):
        task_id = f"{task['id']}_t{trial_idx}"
        task_result = None
        driver = planner._load_driver()

        def execute():
            nonlocal task_result
            task_result = planner.execute_task(
                task["prompt"], task_id=task_id, driver=driver, initialize=False,
                capture_video=False)
            return task_result.summary

        output = planner.trace_dir / f"{task_id}_physics"
        outcome = run_capability_case(driver, case, output, execute=execute,
                                      robot_definition=robot_dict, driver_origin="provided_driver")
        llm_ok = bool(task_result and task_result.ok)
        physical_ok = bool(outcome["ok"])
        trials.append({
            "trial": trial_idx, "llm_ok": llm_ok, "physics_ok": physical_ok,
            "agreement": llm_ok == physical_ok,
            "framework_ok": framework.get("all_ok") is True,
            "validated_driver_task_ok": physical_ok and framework.get("all_ok") is True,
            "n_tool_calls": task_result.n_tool_calls if task_result else 0,
            "frames": outcome.get("n_frames", 0),
            "duration_sec": task_result.duration_sec if task_result else 0.,
            "tokens": task_result.token_usage if task_result else {},
            "summary": task_result.summary if task_result else "",
            "error": outcome.get("error") or (task_result.error if task_result else None),
            "physics_detail": outcome.get("detail", ""),
            "physics_metrics": outcome.get("metrics", {}),
            "mp4_path": outcome.get("video_path"),
            "trace_path": str(task_result.trace_path) if task_result else None,
            "physics_trace_path": outcome.get("trace_path"),
        })
    return trials


def run_task(planner, robot_dict: dict, task: dict,
             n_trials: int) -> list[dict]:
    """Execute one task n_trials times, return list of trial results."""
    if task.get("capability_case_id"):
        return run_capability_task(planner, robot_dict, task, n_trials)
    import numpy as np  # noqa: PLC0415

    trials: list[dict] = []
    for trial_idx in range(n_trials):
        print(f"    trial {trial_idx + 1}/{n_trials}: ", end="", flush=True)
        task_id = f"{task['id']}_t{trial_idx}"
        rec_dir = getattr(planner, "rec_dir", None)
        trace_dir = getattr(planner, "trace_dir", None)
        expected_mp4 = (Path(rec_dir) / f"{task_id}.mp4"
                        if rec_dir is not None else None)
        expected_trace = (Path(trace_dir) / f"{task_id}.jsonl"
                          if trace_dir is not None else None)

        # Fresh skeleton for before-state measurement
        skel = planner._load_driver()
        state_refs = _state_refs_for(robot_dict)
        # Only call home() if the robot class exposes it. Quadrupeds use
        # stand_up()/sit() instead; the fresh skel's initial qpos is fine
        # for before-state capture.
        if hasattr(skel, "home") and callable(getattr(skel, "home")):
            try:
                skel.home()
            except Exception:  # noqa: BLE001 — pose reset is best-effort
                pass
        # Record class-appropriate before state
        before_state: dict = {}
        if state_refs:
            try:
                before_state.update(_truth_state(skel, state_refs))
            except Exception as e:  # noqa: BLE001
                before_state["_physics_error"] = f"{type(e).__name__}: {e}"
        if hasattr(skel, "get_ee_pose"):
            try:
                ee, _ = skel.get_ee_pose()
                before_state.setdefault("ee_pose", np.asarray(ee).tolist())
            except Exception:  # noqa: BLE001
                pass
        if hasattr(skel, "get_base_pose"):
            try:
                p, R = skel.get_base_pose()
                before_state.setdefault("base_pose_xyz", np.asarray(p).tolist())
                R = np.asarray(R, dtype=float)
                before_state.setdefault("base_yaw", float(np.arctan2(R[1, 0], R[0, 0])))
            except Exception:  # noqa: BLE001
                pass
        if hasattr(skel, "get_base_yaw"):
            try:
                before_state["base_yaw"] = float(skel.get_base_yaw())
            except Exception:  # noqa: BLE001
                pass
        if hasattr(skel, "get_body_height"):
            try:
                before_state["body_height"] = float(skel.get_body_height())
            except Exception:  # noqa: BLE001
                pass
        # Record positions of all named bodies the task or scene mentions
        objects = {}
        # Known bodies: SO-101 v2 scene (banana...lego) + pushbench scene (tee, obs, cube_*)
        candidate_bodies = [
            "banana", "mug", "bottle", "screwdriver", "duck", "lego",
            "tee", "obs", "cube_red", "cube_green", "cube_blue",
        ]
        for b in candidate_bodies:
            try:
                objects[b] = _obj_pos(skel,b).tolist()
            except Exception:
                pass
        before_state["objects"] = objects

        # Execute, sample and film the very same world whose initial state
        # was measured above. Replay remains a diagnostic utility only.
        from autoadapter_bench.physics import PhysicsTrace
        try:
            physical_trace = PhysicsTrace(skel, state_refs)
            planner._physics_trace = physical_trace
            with physical_trace:
                r = planner.execute_task(task["prompt"], task_id=task_id,
                                         driver=skel, initialize=False)
            crash = None
        except Exception as e:  # noqa: BLE001
            r = None
            crash = f"{type(e).__name__}: {e}"
        finally:
            planner._physics_trace = None

        if r is None:
            trials.append({
                "trial": trial_idx, "llm_ok": False, "physics_ok": False,
                "agreement": True, "n_tool_calls": 0, "frames": 0,
                "duration_sec": 0.0, "tokens": {}, "summary": "",
                "error": crash, "physics_detail": "crash",
                "physics_metrics": {},
                "mp4_path": _path_str(expected_mp4),
                "trace_path": _path_str(expected_trace),
                "physics_trace_path": None,
            })
            print(f"CRASH ({crash[:60]})")
            continue

        replay_skel = skel  # Legacy evaluator argument; this is the executed world.
        physics_samples = physical_trace.samples
        replay_clean = bool(physics_samples) and all(call.get("ok") for call in r.tool_call_log)
        phase_by_index = {}
        for state in physics_samples:
            phase_by_index[state["idx"]] = {
                "idx": state["idx"], "tool": state["tool"],
                "ee": state.get("ee"), "xyz": state.get("base_xyz"),
                "height": (state["base_xyz"][2] if "base_xyz" in state else None),
            }
        phase_snaps = list(phase_by_index.values())
        before_state["_phase_snapshots"] = phase_snaps
        before_state["_physics_samples"] = physics_samples
        before_state["_replay_clean"] = replay_clean
        physics_trace_path = None
        if physics_samples and trace_dir is not None:
            physics_trace_path = Path(trace_dir) / f"{task_id}_physics.json"
            physics_trace_path.parent.mkdir(parents=True, exist_ok=True)
            physics_trace_path.write_text(json.dumps(physics_samples, indent=2,
                                                       default=str))
        try:
            physics_ok, detail, metrics = evaluate_success(
                replay_skel, before_state, task["success"], r, task["id"]
            )
        except Exception as e:  # noqa: BLE001
            physics_ok = False
            detail = f"physics eval exception: {type(e).__name__}: {e}"
            metrics = {}
        if not replay_clean:
            detail = f"LIVE EXECUTION OR TRACE FAILED. {detail}"
            metrics["live_execution_failed"] = True
            # A failed or missing replay method is a physics failure even if a
            # post-hoc observation happens to satisfy the geometric predicate.
            physics_ok = False

        mp4_path = getattr(r, "mp4_path", None)
        trace_path = getattr(r, "trace_path", None)
        metrics["behavior_ok_before_evidence_checks"] = bool(physics_ok)
        evidence_error = None
        try:
            for label, path in (("video", mp4_path), ("generation trace", trace_path),
                                ("physical trace", physics_trace_path)):
                if path is None or not Path(path).is_file() or Path(path).stat().st_size == 0:
                    raise ValueError(f"required {label} is unavailable")
            import imageio.v2 as imageio
            reader = imageio.get_reader(str(mp4_path), format="FFMPEG")
            try:
                reader.get_data(0)
            finally:
                reader.close()
        except Exception as exc:
            evidence_error = f"{type(exc).__name__}: {exc}"
            physics_ok = False
            detail = f"INCOMPLETE EVIDENCE: {evidence_error}. {detail}"
        metrics["evidence_complete"] = evidence_error is None

        trials.append({
            "trial": trial_idx,
            "llm_ok": bool(r.ok),
            "physics_ok": bool(physics_ok),
            "agreement": bool(r.ok == physics_ok),
            "n_tool_calls": r.n_tool_calls,
            "frames": r.n_frames,
            "duration_sec": r.duration_sec,
            "tokens": r.token_usage,
            "summary": (r.summary or "")[:300],
            "error": r.error or evidence_error,
            "physics_detail": detail,
            "physics_metrics": metrics,
            "mp4_path": _path_str(mp4_path),
            "trace_path": _path_str(trace_path),
            "physics_trace_path": _path_str(physics_trace_path),
        })
        agree = "✓" if r.ok == physics_ok else "✗"
        print(f"llm={'OK' if r.ok else 'FAIL'} phys={'OK' if physics_ok else 'FAIL'} {agree}")

    return trials


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description="AutoAdapter-Bench evaluator")
    p.add_argument("--robot", required=True, help="Robot ID (see robot_zoo.yaml)")
    p.add_argument("--suites", default="simple",
                   help="Comma-separated suite names (simple,hard,contact_rich)")
    p.add_argument("--model", default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--provider", choices=["holistic", "bedrock"], default="holistic")
    p.add_argument("--n-trials", type=int, default=None,
                   help="Override task-level n_trials (default = use yaml value)")
    p.add_argument("--tasks", default=None,
                   help="Comma-separated task IDs (default = all in suite)")
    p.add_argument("--output", required=True, help="Path for results.json")
    p.add_argument("--driver-workspace", default=None,
                   help="Override driver workspace (else auto-locate by robot_id). "
                        "Used for Leaderboard-B: eval a model on the driver IT "
                        "synthesized, not the default one.")
    p.add_argument("--resume", action="store_true",
                   help="If output exists, skip already-completed tasks "
                        "(incremental checkpoint after each task)")
    args = p.parse_args()

    # Late import — speeds up --help
    from auto_adapter.agent.task_planner import TaskPlanner  # noqa: PLC0415

    zoo = load_robot_zoo()
    robot = find_robot(zoo, args.robot)
    if args.driver_workspace:
        workspace = Path(args.driver_workspace).resolve()
        if not ((workspace / "driver.py").exists()
                or (workspace / "driver_from_scratch.py").exists()):
            raise SystemExit(f"--driver-workspace {workspace} has no driver.py / "
                             "driver_from_scratch.py")
    else:
        workspace = find_driver_workspace(args.robot)
    print(f"Robot: {robot['id']}  class: {robot['class']}  workspace: {workspace}")

    # Ensure mjcf.xml is a symlink to the real MJCF
    real_mjcf = (REPO_ROOT / robot.get("capability_mjcf", robot["mjcf"])).resolve()
    mjcf_link = workspace / "mjcf.xml"
    if not (mjcf_link.exists() and mjcf_link.is_symlink() and
            Path(os.readlink(mjcf_link)).resolve() == real_mjcf):
        if mjcf_link.exists() or mjcf_link.is_symlink():
            mjcf_link.unlink()
        mjcf_link.symlink_to(real_mjcf)
        print(f"  re-symlinked mjcf.xml → {real_mjcf}")

    suite_yaml = load_task_suite(robot["class"])
    if robot.get("capability_profile"):
        from auto_adapter.robot_catalog import load_capability_suite
        conditions = load_capability_suite(robot)
        selected = "A1" if robot["class"] == "arm" else ("G1" if robot["id"] == "go2" else "G4")
        case = next(item for item in conditions["cases"] if item["capability_id"] == selected)
        suite_yaml["suites"]["capability"] = {"tasks": [{
            "id": f"{selected}_diagnostic", "capability_case_id": case["case_id"],
            "prompt": "Execute " + case["method_name"] + " with request " + json.dumps(case["request"])
                      + ". Meet the capability's physical hold and timing requirements.",
            "success": {"capability_id": selected, "n_trials": 1},
        }]}
    requested = [s.strip() for s in args.suites.split(",") if s.strip()]
    task_filter = set(t.strip() for t in args.tasks.split(",")) if args.tasks else None

    # Get git sha for reproducibility
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                          cwd=str(REPO_ROOT),
                                          stderr=subprocess.DEVNULL).decode().strip()[:12]
    except Exception:
        git_sha = "unknown"

    # ─── Resume / checkpoint support ─────────────────────────────
    # If output exists, load it and skip tasks already completed.
    output_path = Path(args.output)
    resume_completed: set[tuple[str, str]] = set()  # (suite, task_id) pairs
    result: dict
    if output_path.exists() and getattr(args, "resume", False):
        try:
            result = json.loads(output_path.read_text())
            for s_name, s_data in result.get("suites", {}).items():
                for t in s_data.get("tasks", []):
                    if len(t.get("trials", [])) >= t.get("n_trials", 0):
                        resume_completed.add((s_name, t["id"]))
            print(f"[resume] loaded {output_path.name} — skipping "
                  f"{len(resume_completed)} already-completed tasks")
        except Exception as e:  # noqa: BLE001
            print(f"[resume] failed to load {output_path}: {e}; starting fresh")
            result = None  # type: ignore
    else:
        result = None  # type: ignore

    if result is None:
        result = {
            "meta": {
                "benchmark_version": "v1.0",
                "robot": robot["id"],
                "robot_class": robot["class"],
                "mjcf": robot["mjcf"],
                "vendor": robot["vendor"],
                "dof": robot["dof"],
                "model": args.model,
                "provider": args.provider,
                "region": args.region,
                "git_sha": git_sha,
                "started_at": datetime.utcnow().isoformat(),
                "suites_requested": requested,
            },
            "suites": {},
        }

    def _checkpoint() -> None:
        _build_aggregate(result)  # keep aggregate consistent on every write
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))

    t_start = time.time()
    grand_in = grand_out = 0
    # Re-tally already-loaded tokens so cost continues from resume point
    for s_data in result.get("suites", {}).values():
        for t in s_data.get("tasks", []):
            grand_in += t.get("tokens", {}).get("in", 0)
            grand_out += t.get("tokens", {}).get("out", 0)

    # Namespace recordings/traces by output filename stem (e.g. "ours_so101_opus48")
    # so model runs don't overwrite each other's videos.
    _run_tag = Path(args.output).stem
    with TaskPlanner(
        workspace=workspace,
        bedrock_model=args.model,
        model_provider=args.provider,
        robot_id=robot["id"],
        region=args.region,
        run_tag=_run_tag,
        max_iters=int(os.environ.get("AUTOADAPTER_MAX_ITERS", "50")),
        max_tokens_per_turn=6000,
    ) as planner:
        for suite_name in requested:
            if suite_name not in suite_yaml["suites"]:
                print(f"[skip] unknown suite {suite_name!r}")
                continue
            suite = suite_yaml["suites"][suite_name]
            print(f"\n=== suite: {suite_name} — {len(suite['tasks'])} tasks ===")
            # Initialise / inherit per-suite results from prior partial run
            existing_suite = result["suites"].get(suite_name, {"tasks": []})
            suite_results = list(existing_suite.get("tasks", []))
            done_task_ids = {t["id"] for t in suite_results}
            for task in suite["tasks"]:
                if task_filter and task["id"] not in task_filter:
                    continue
                if (suite_name, task["id"]) in resume_completed:
                    print(f"  [skip-resume] {task['id']} already complete")
                    continue
                # Remove any partial (interrupted) entry for this task
                suite_results = [t for t in suite_results if t["id"] != task["id"]]
                n_trials = args.n_trials or task["success"].get("n_trials", 1)
                print(f"  task: {task['id']}  ({n_trials} trials)")
                trials = run_task(planner, robot, task, n_trials)
                n_llm = sum(1 for t in trials if t["llm_ok"])
                n_phys = sum(1 for t in trials if t["physics_ok"])
                n_agree = sum(1 for t in trials if t["agreement"])
                in_t = sum(t["tokens"].get("in", 0) for t in trials)
                out_t = sum(t["tokens"].get("out", 0) for t in trials)
                grand_in += in_t; grand_out += out_t
                suite_results.append({
                    "id": task["id"],
                    "prompt": task["prompt"],
                    "success_spec": task["success"],
                    "n_trials": n_trials,
                    "trials": trials,
                    "llm_pass_rate": n_llm / max(n_trials, 1),
                    "physics_pass_rate": n_phys / max(n_trials, 1),
                    "agreement_rate": n_agree / max(n_trials, 1),
                    "mean_tool_calls": sum(t["n_tool_calls"] for t in trials) / max(n_trials, 1),
                    "tokens": {"in": in_t, "out": out_t},
                    "cost_usd": _cost_usd(args.model, in_t, out_t),
                })
                # CHECKPOINT: persist after each task so we can resume mid-suite
                result["suites"][suite_name] = {"tasks": suite_results}
                _checkpoint()
            result["suites"][suite_name] = {"tasks": suite_results}
            _checkpoint()

    wall = time.time() - t_start
    result["meta"]["completed_at"] = datetime.utcnow().isoformat()
    result["meta"]["wall_clock_sec"] = wall

    # Aggregate (recomputed from all trials — same helper used on every checkpoint)
    agg = _build_aggregate(result, wall=wall)
    n_total = agg["n_trials_total"]

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved: {args.output}")
    print(f"  total trials:         {n_total}")
    print(f"  llm_pass_rate:        {result['aggregate']['llm_pass_rate']*100:.0f}%")
    print(f"  physics_pass_rate:    {result['aggregate']['physics_pass_rate']*100:.0f}%")
    print(f"  agreement_rate:       {result['aggregate']['agreement_rate']*100:.0f}%")
    print(f"  cost: ${result['aggregate']['total_cost_usd']:.2f}")
    print(f"  wall: {wall:.1f}s")


if __name__ == "__main__":
    main()
