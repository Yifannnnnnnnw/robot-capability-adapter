# SPDX-License-Identifier: Apache-2.0
"""From-scratch driver synthesis — no skeleton library at all.

The current orchestrator.py has the agent FILL specs that instantiate
framework-provided skeletons (ArmSerialDLSSkeleton, QuadrupedPDGaitSkeleton).
The math (DLS IK, Jacobian, weld activation, PD trot) is hand-written
framework code that limits generalization to whichever algorithms we baked in.

This module is the answer to the deeper "no-hardcoded" rule: the agent
writes the entire driver, INCLUDING the IK algorithm and motion control
logic, based only on:
  - the MJCF
  - the mujoco Python API
  - numpy

The agent is FREE to choose: DLS, Newton's method, pseudoinverse, gradient
descent, analytical IK if it can derive one — whatever fits the robot.
The framework only provides the PRIMITIVES (mj_jacSite, mj_step, etc.),
not the algorithm.

Pipeline:
    STUDY        — same as before (capability graph)
    GEN_ALGO     — NEW: agent writes complete driver.py with IK / motion logic
    VALIDATE_FW  — framework-level: import the driver, run smoke tests
    TASK_SUITE   — same standardized tasks via TaskPlanner-equivalent

Output:
    workspace/driver_from_scratch.py  — single-file driver, no auto_adapter imports
    workspace/validate_report.json
    workspace/recordings/*.mp4
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .agent import ReactLoop, ReactResult, ToolSpec
from .agent.tools import (
    make_execute_python_tool,
    make_local_exec_tool,
    make_read_file_tool,
    make_write_file_tool,
)


def _vlog(msg: str) -> None:
    """Verbose trace to stderr (gated by AA_VERBOSE) so we can see exactly
    which phase / call the pipeline is in when it stalls."""
    if os.environ.get("AA_VERBOSE"):
        print(f"[{time.strftime('%H:%M:%S')}] [orch] {msg}", file=sys.stderr,
              flush=True)


def _scene_movable_bodies(r) -> list[str]:
    """GROUND-TRUTH list of free-joint (movable / graspable) body names read
    straight from the sim — independent of whatever the driver's
    get_object_names() reports. Used so a driver that returns an EMPTY object
    list cannot skip the perception / grasp validation when the scene actually
    contains manipulable objects."""
    import mujoco  # noqa: PLC0415
    m = getattr(r, "model", None) or getattr(r, "_model", None)
    if m is None:
        return []
    out = []
    for j in range(int(m.njnt)):
        if int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
            bid = int(m.jnt_bodyid[j])
            nm = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, bid)
            if nm:
                out.append(nm)
    return out


def _validate_humanoid_stand_balance(
    robot,
    robot_id: str,
    mjcf_path: Path,
    *,
    secs: float = 2.0,
) -> dict:
    """Validate a humanoid stand over the real simulated interval.

    The robot definition supplies the trusted torso body name.  PhysicsTrace
    samples after every real ``mj_step`` so a driver that only returns from
    ``stand_balance`` cannot pass on its initial upright pose.
    """
    try:
        from autoadapter_bench.physics import PhysicsTrace  # noqa: PLC0415
        from .robot_catalog import find_robot_definition  # noqa: PLC0415

        definition = find_robot_definition(robot_id, mjcf_path)
        state_refs = (definition or {}).get("state_refs") or {}
        base_body = state_refs.get("base_body")
        if not base_body:
            raise ValueError(
                f"trusted robot definition has no humanoid base_body for {robot_id!r}"
            )

        trace = PhysicsTrace(robot, {"base_body": base_body})
        with trace:
            trace.tool, trace.idx = "stand_balance", -1
            robot.stand_balance(secs=float(secs))

        samples = trace.samples
        if not samples:
            raise ValueError("stand_balance produced no physics samples")
        elapsed = float(samples[-1]["time"] - samples[0]["time"])
        heights = [float(sample["base_xyz"][2]) for sample in samples]
        upright = [float(sample["base_upright"]) for sample in samples]
        finite = all(bool(sample["finite"]) for sample in samples)
        min_height = min(heights)
        min_upright = min(upright)
        elapsed_ok = elapsed >= float(secs) - 1e-9
        ok = bool(
            elapsed_ok
            and finite
            and min_height > 0.6
            and min_upright > 0.7
        )
        return {
            "test": "stand_balance",
            "ok": ok,
            "detail": (
                f"elapsed={elapsed:.3f}s (need >={float(secs):.3f}), "
                f"steps={max(0, len(samples) - 1)}, "
                f"min_h={min_height:.3f}m (need >0.6), "
                f"min_up={min_upright:.3f} (need >0.7), "
                f"finite={finite}"
            ),
            "metric": elapsed,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "test": "stand_balance",
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "metric": 0.0,
        }


# ──────────────────────────────────────────────────────────────────────────
# Config + dataclasses
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class FromScratchConfig:
    robot_id: str
    mjcf_path: Path
    workspace_root: Path
    bedrock_model: str = "us.anthropic.claude-sonnet-4-6"
    model_provider: str = "holistic"
    aws_region: str = "us-east-1"
    ci_id: str = "aws.codeinterpreter.v1"
    ci_session_timeout_sec: int = 1800   # 30 min — generation is long

    max_iters_study: int = 14
    max_iters_gen_algo: int = 40         # algorithm synthesis needs many iters
    max_iters_gen_repair: int = 20       # repair passes are shorter than first gen
    max_outer_retries: int = 2           # outer VAL→GEN repair attempts after first validate
    max_tokens_per_turn: int = 8000


@dataclass
class FromScratchResult:
    robot_id: str
    workspace: Path
    study_ok: bool
    gen_ok: bool
    validate_ok: bool
    driver_path: Optional[Path]
    validate_report: dict
    total_duration_sec: float
    total_tokens: dict
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Prompts
# ──────────────────────────────────────────────────────────────────────────


_STUDY_SYSTEM = """\
You are Phase 1 STUDY. Read the robot's MJCF and produce study.json with the
kinematic + actuator + grasp information the next phase needs to write a
driver from scratch.

IMPORTANT: keep each execute_python call short (≤150 lines). The session
keeps state across calls.

Procedure:
  1. read_file the MJCF.
  2. execute_python: parse with mujoco.MjModel.from_xml_path; for each
     ARM joint (hinge/slide that the IK should move) record name, axis,
     ctrlrange, qpos limits, parent body. Identify the end-effector
     site OR body. Identify gripper actuator(s) if any. Identify any
     weld equality constraints between gripper body and free-joint bodies.
  3. execute_python: classify the robot (arm | quadruped | wheeled | aerial | humanoid | …).
  4. write_file study.json with this exact schema:
       {
         "robot_id": "<id>",
         "estimated_class": "arm|quadruped|…",
         "dof": <int>,
         "arm_joint_names": [...],
         "arm_actuator_names": [...],
         "joint_limits": {name: [lo, hi]},
         "ee_site": "<name or null>",
         "ee_body": "<name or null>",
         "gripper_actuator_names": [...] or null,
         "gripper_ctrlrange": [lo, hi] or null,
         "weld_graspable_bodies": [...] or null,
         "actuator_type_hint": "position|velocity|motor|tendon",
         "notes": "<one-line summary>"
       }
  5. Reply with one sentence confirming the file.
"""


_GEN_ALGO_SYSTEM = """\
You are Phase 2 GEN_ALGO. You will write driver_from_scratch.py — a complete,
single-file Python driver for this robot — WITHOUT importing anything from
auto_adapter.skeletons. The framework provides building blocks; you choose
the algorithms.

Hard rules:
  - Use `mujoco`, `numpy`, and Python stdlib only. NO `from auto_adapter.skeletons
    import *`. Don't copy framework classes wholesale.
  - The driver must expose a class `Robot` with classmethod
    `build_from_mjcf(mjcf_path: str) -> Robot` plus `home() -> bool`,
    `get_joint_positions()`, `step(n: int = 1)`, `render()`, `describe()`.
  - Beyond those, the API should fit the robot's CLASS (from study.json):

      ARM (serial chain with end-effector):
        - get_ee_pose() -> (xyz, R3x3)
        - move_cartesian(target_xyz, duration=2.0) -> bool
        - gripper_open() / gripper_close() -> bool   (if gripper exists)
        - is_holding() -> bool
        - get_object_position(name: str) -> xyz   (REQUIRED for manipulation:
          world position of a named scene object. The task planner will name
          objects, e.g. "banana", "mug", "cube_red". Resolve the name to a
          body — fall back to geom, then site — via mujoco.mj_name2id and
          return its world xyz from mjData (data.xpos / geom_xpos / site_xpos).
          Raise KeyError if the name is unknown.)
        - get_object_names() -> list[str]   (names of the manipulable scene
          objects, so a planner can discover what's in the world.)
        Pick an IK method that fits DOF + redundancy:
          * 5-DOF non-redundant → DLS or Newton-Raphson
          * 6-7 DOF redundant → DLS with nullspace, or pseudoinverse + secondary
          * Spherical wrist → analytical IK if you can derive it
        Robust IK: clamp to joint limits, raise on unreachable, scale damping
        with residual.

      QUADRUPED (4 legs, locomotion, torque actuators):
        DO NOT WRITE IK — there's no end-effector to reach. Write:
        - stand_up(duration=2.0) -> bool   (PD to a STABLE standing pose;
          body height must end > 0.15 m and stay there)
        - sit(duration=1.5) -> bool        (PD to a folded pose that ACTUALLY
          LOWERS the body: final height must drop below 0.8x the standing
          height — fold hips+knees, do not just hold the stand pose)
        - walk_forward(secs=2.0, speed=0.2) -> bool   (gait that produces REAL
          forward base displacement: >3 cm over ~1.5 s while staying upright,
          with small sideways drift — tune the gait, a degenerate in-place
          shuffle will fail validation)
        - get_body_height() / get_base_pose()
        Use joint-space PD (τ = kp(q_des - q) + kd(qd_des - qd)). For walking,
        pick a simple periodic gait — diagonal pairs swing anti-phase — and
        verify forward progress in your local_exec self-test before finishing.
        These three behaviors are VALIDATED on actual physics (height drop for
        sit, forward displacement for walk), not just "did not crash".

      WHEELED / MOBILE BASE (differential drive, wheel-velocity actuators):
        DO NOT WRITE ARM IK. The base moves via wheel-velocity actuators
        (e.g. left_wheel_vel / right_wheel_vel). Write:
        - drive_forward(distance_m=0.3, speed=1.0) -> bool   (BOTH wheels same
          velocity; produce REAL forward base displacement, low sideways drift;
          close the loop on get_base_pose to stop near distance_m)
        - turn(angle_rad, speed=1.0) -> bool   (DIFFERENTIAL wheel velocities;
          change base YAW by the requested angle, verify via get_base_pose)
        - get_base_pose() -> (xyz, R3x3)   (world base position + orientation)
        - get_base_yaw() -> float
        drive_forward and turn are VALIDATED on physics (real forward
        displacement; real yaw change), not "did not crash". Tune wheel speed
        and step count and verify in your local_exec self-test before finishing.

      AERIAL / MULTIROTOR (free-flying base, N thrust actuators):
        DO NOT WRITE ARM IK and DO NOT WRITE A WALKING GAIT. A quadrotor is
        UNDERACTUATED and open-loop UNSTABLE — it WILL flip and crash unless a
        feedback controller runs every sim step. Write:
        - takeoff(height=0.5) -> bool   (spin rotors up and climb to ~height m
          altitude, then HOLD a stable hover; final z must be near height and
          the body must stay upright — a flip/crash fails validation)
        - move_to(x, y, z, tol=0.1) -> bool   (fly to a world target with a
          closed loop on get_base_pose; arrive within tol and stay upright)
        - hover(secs=2.0) -> bool   (station-keep at the current pose)
        - get_base_pose() -> (xyz, R3x3)   (world base position + orientation;
          use this exact name — downstream tooling reads base pose through it)
        - land() -> bool   (descend to the ground and idle the rotors)
        Use a CASCADED controller stepped each mj_step: an outer position loop
        (PD on x,y,z error -> desired total thrust + desired roll/pitch angles)
        and an inner attitude loop (PD on roll/pitch/yaw -> per-rotor thrust
        offsets), then mix to the n thrust actuators. Per-rotor hover thrust is
        about total_mass * 9.81 / n_rotors (clamp to actuator ctrlrange). Read
        body orientation from the free-joint quaternion (data.qpos[3:7]) and
        body rates from data.qvel[3:6]. VERIFY in your local_exec self-test that
        the drone holds altitude for >=1 s and reaches a moved target WITHOUT
        flipping — a controller that drifts or tumbles fails validation.

      HUMANOID / BIPED (free-floating torso, many joint actuators, legs+arms):
        DO NOT WRITE ARM IK. A standing humanoid is a tall inverted pendulum
        — open-loop it falls. Bipedal balance is the hard part; prioritise
        STATIC balance over dynamic walking. Write:
        - stand_balance(secs=3.0) -> bool   (hold a STABLE standing pose for
          `secs`: PD-track a sensible nominal joint configuration — slightly
          bent knees/hips, torso vertical — and keep the torso upright and at
          roughly its standing height the whole time; falling fails validation)
        - squat(depth=0.15, secs=3.0) -> bool   (smoothly lower the torso by
          ~depth m by bending hips+knees, then return to standing; stay
          balanced and recover the standing height — do not topple)
        - get_base_pose() -> (xyz, R3x3)   (torso/pelvis world position +
          orientation; use this exact name)
        - get_torso_height() -> float   (torso world Z)
        - walk_forward(distance_m=0.10, secs=2.0) -> bool   (ATTEMPT a small
          forward walk; ONLY try this once stand_balance is robust. Dynamic
          bipedal walking is HARD — a 5-10 cm shuffle is a success, do not
          aim far.)
        Use joint-space PD (τ = kp(q_des − q) + kd(qd_des − qd)) with per-joint
        gains; read the torso orientation from the free-joint quaternion
        (data.qpos[3:7], wxyz order). Define the nominal standing pose from the
        MJCF keyframe if present, else a mild crouch. VERIFY in your local_exec
        self-test that the torso stays up for the full duration and that squat
        lowers then recovers WITHOUT falling.
        WALK (only if you attempt it) — make it a CLOSED-LOOP quasi-static
        micro-step state machine, NOT open-loop sinusoids:
          * 4 phases: shift weight onto stance foot -> lift+place swing foot a
            small step (2-4 cm, 1-2 cm clearance) -> settle into double support
            -> swap stance/swing. Step period ~0.5-0.8 s.
          * Keep the torso over the stance foot before unloading the swing foot;
            feet parallel, NO cross-over, NO large yaw change, slight crouch.
          * Update q_des ONLINE every control substep from the balance state
            (base pose/vel, torso height, foot poses, contact if available);
            smooth/interpolate targets; clamp to joint+actuator limits.
          * ABORT immediately (return without falling further) if torso height
            or uprightness drops below a guard. Forbidden: open-loop joint
            oscillations, a large first step, writing the base pose directly,
            judging progress by world-x (use the start-heading frame).
        It is FINE if walk_forward fails — stand+squat already validate; an
        honest "walk not achieved" is acceptable. Do NOT fake forward progress.

      OTHER (biped, …):
        Whatever makes sense. Write the behaviors a useful task planner
        would call. Don't force-fit arm semantics on a non-arm robot.

  - Code should be ROBUST: graceful failure, clamp to limits, helpful error
    messages.

You have these tools:
  - read_file (study.json, mjcf.xml, mujoco source under .venv if useful)
  - execute_python (sandboxed CI — for prototyping math, parsing MJCF, etc.)
  - local_exec (LOCAL — `python` resolves to the venv with mujoco; use this
    to actually test the driver you wrote)
  - write_file (workspace-relative)

Procedure:
  1. read_file study.json.
  2. execute_python or local_exec: prototype FK using mujoco — set qpos,
     call mj_forward, read site_xpos / body xpos. Verify your understanding
     of the index resolution works.
  3. execute_python or local_exec: prototype your IK on the home pose +
     a small offset. Iterate until residual < 1cm. PRINT the algorithm
     details (damping schedule, iteration count, step clamp).
  4. write_file driver_from_scratch.py — the FULL Robot class, well-commented,
     with the algorithms you just prototyped baked in.
  5. local_exec: `python -c "import driver_from_scratch; r =
     driver_from_scratch.Robot.build_from_mjcf('mjcf.xml'); r.home();
     print(r.describe())"` to prove it constructs + runs.
  6. local_exec: test ik_roundtrip — move EE +5cm in X, read back, error < 2cm.
  7. Reply with one sentence summarizing: which IK method you chose,
     final residual on the round-trip test, and a brief description of
     gripper/grasp behavior.
"""


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


class FromScratchOrchestrator:
    """Spawns Phase 1 (STUDY) and Phase 2 (GEN_ALGO) — agent writes a full driver
    from scratch, no skeleton library imports allowed.

    Validates the result by:
      - import the agent's driver
      - run home() + a small ik_roundtrip
      - capture frames during behavior
      - write validate_report.json with metrics
    """

    def __init__(self, cfg: FromScratchConfig) -> None:
        self.cfg = cfg
        self.workspace = (Path(cfg.workspace_root) / cfg.robot_id).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "traces").mkdir(parents=True, exist_ok=True)
        (self.workspace / "recordings").mkdir(parents=True, exist_ok=True)

        src = Path(cfg.mjcf_path).resolve()
        dst = self.workspace / "mjcf.xml"
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src)

        self._ci_client: Any = None
        self._ci_session_id: Optional[str] = None
        self._exec_python_tool: Optional[ToolSpec] = None

    def __enter__(self) -> "FromScratchOrchestrator":
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        # read/connect timeouts: a hung CodeInterpreter socket must FAIL FAST
        # (surface as a tool error the agent can react to) instead of blocking
        # the whole pipeline forever. max_attempts=1 → no silent double-execute
        # of non-idempotent code on a timeout.
        _ci_cfg = Config(read_timeout=180, connect_timeout=20,
                         retries={"max_attempts": 1})
        _vlog(f"creating bedrock-agentcore client (region={self.cfg.aws_region})")
        self._ci_client = boto3.client("bedrock-agentcore",
                                       region_name=self.cfg.aws_region, config=_ci_cfg)
        _vlog(f"starting CodeInterpreter session (ci_id={self.cfg.ci_id}, "
              f"timeout={self.cfg.ci_session_timeout_sec}s)…")
        sess = self._ci_client.start_code_interpreter_session(
            codeInterpreterIdentifier=self.cfg.ci_id,
            name=f"from-scratch-{self.cfg.robot_id}",
            sessionTimeoutSeconds=self.cfg.ci_session_timeout_sec,
        )
        self._ci_session_id = sess["sessionId"]
        self._exec_python_tool = make_execute_python_tool(
            self._ci_client, self._ci_session_id, ci_id=self.cfg.ci_id,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._ci_session_id is not None:
            try:
                self._ci_client.stop_code_interpreter_session(
                    codeInterpreterIdentifier=self.cfg.ci_id,
                    sessionId=self._ci_session_id,
                )
            except Exception:  # noqa: BLE001
                pass

    # ─── Tool bundles ─────────────────────────────────────────────────────

    def _read_extra_roots(self) -> list[Path]:
        """The agent is allowed to read the MJCF source dir (for <include>
        resolution) and the mujoco Python source (to learn the API). NOT
        auto_adapter.skeletons — that's the whole point of from-scratch.
        """
        import mujoco  # noqa: PLC0415

        mjcf_src_dir = Path(self.cfg.mjcf_path).resolve().parent
        mujoco_src = Path(mujoco.__file__).parent
        return [mjcf_src_dir, mujoco_src]

    def _common_tools(self) -> list[ToolSpec]:
        return [
            make_write_file_tool(self.workspace),
            make_read_file_tool(self.workspace, extra_roots=self._read_extra_roots()),
        ]

    def _local_runtime_tool(self) -> ToolSpec:
        return make_local_exec_tool(
            self.workspace,
            python_path_prepend=[],  # critical: DO NOT add auto_adapter on path
        )

    # ─── Phases ───────────────────────────────────────────────────────────

    def _run_loop(self, *, name: str, system: str, user_msg: str,
                  tools: list[ToolSpec], max_iters: int) -> ReactResult:
        trace_path = self.workspace / "traces" / f"{name}.jsonl"
        _vlog(f"PHASE {name} START (max_iters={max_iters}, "
              f"tools={[t.name for t in tools]})")
        _t0 = time.time()
        loop = ReactLoop(
            tools=tools, system=system,
            model=self.cfg.bedrock_model, region=self.cfg.aws_region,
            provider=self.cfg.model_provider,
            max_iters=max_iters,
            max_tokens_per_turn=self.cfg.max_tokens_per_turn,
            trace_path=trace_path,
        )
        res = loop.run(user_msg)
        _vlog(f"PHASE {name} END in {time.time()-_t0:.0f}s "
              f"(ok={res.ok}, err={res.error}, "
              f"tok_out={res.total_tokens.get('out')})")
        return res

    def phase_study(self) -> ReactResult:
        assert self._exec_python_tool is not None
        tools = self._common_tools() + [self._exec_python_tool]
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "MJCF (workspace-relative): mjcf.xml\n"
            "Produce study.json per the procedure."
        )
        return self._run_loop(
            name="01_study", system=_STUDY_SYSTEM, user_msg=user,
            tools=tools, max_iters=self.cfg.max_iters_study,
        )

    def phase_gen_algo(self) -> ReactResult:
        """The from-scratch driver synthesis phase."""
        assert self._exec_python_tool is not None
        tools = self._common_tools() + [
            self._exec_python_tool, self._local_runtime_tool(),
        ]
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "study.json is in the workspace. MJCF at mjcf.xml.\n"
            "Write driver_from_scratch.py — full Robot class with YOUR own\n"
            "FK / IK / motion / gripper code, no auto_adapter.skeletons imports."
        )
        return self._run_loop(
            name="02_gen_algo", system=_GEN_ALGO_SYSTEM, user_msg=user,
            tools=tools, max_iters=self.cfg.max_iters_gen_algo,
        )

    def phase_gen_repair(self, report: dict, attempt: int) -> ReactResult:
        """Outer VAL→GEN retry: feed the held-out validator's failing tests
        back to the agent so it can fix driver_from_scratch.py in place.

        This is what makes the from-scratch pipeline actually 'iterate against
        post-step physical state until the structural-test suite passes' — the
        framework validator is otherwise out of the agent's inner loop.
        """
        assert self._exec_python_tool is not None
        tools = self._common_tools() + [
            self._exec_python_tool, self._local_runtime_tool(),
        ]
        failing = [t for t in report.get("tests", []) if not t.get("ok")]
        fail_lines = "\n".join(
            f"  - {t.get('test')}: {t.get('detail', '')}" for t in failing
        ) or "  (driver missing or unparseable)"
        if report.get("error"):
            fail_lines = f"  - {report['error']}\n" + fail_lines
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "Your driver_from_scratch.py was written but the held-out structural\n"
            "validator FAILED the following tests:\n"
            f"{fail_lines}\n\n"
            "Fix driver_from_scratch.py IN PLACE so every failing test passes.\n"
            "Steps:\n"
            "  1. read_file driver_from_scratch.py — find the cause of each failure.\n"
            "  2. Fix the code (e.g. wrong MuJoCo attribute names, IK math, gripper\n"
            "     logic). Keep the public API and the no-skeleton-import rule.\n"
            "  3. local_exec to re-run build_from_mjcf('mjcf.xml') + the failing\n"
            "     behavior and confirm it works before finishing.\n"
            "  4. write_file the corrected driver_from_scratch.py."
        )
        return self._run_loop(
            name=f"03_gen_repair_{attempt}", system=_GEN_ALGO_SYSTEM,
            user_msg=user, tools=tools, max_iters=self.cfg.max_iters_gen_repair,
        )

    # ─── Framework validation (no LLM) ────────────────────────────────────

    def _validate_from_scratch_driver(self) -> dict:
        """Import the agent's driver, run smoke tests, return report."""
        driver_path = self.workspace / "driver_from_scratch.py"
        report: dict = {"tests": [], "all_ok": False}

        if not driver_path.exists():
            report["error"] = "driver_from_scratch.py missing"
            return report

        # Check zero auto_adapter.skeletons imports — AST-level, so docstring
        # / comment mentions like "no imports from auto_adapter.skeletons"
        # don't cause false positives.
        import ast  # noqa: PLC0415

        src = driver_path.read_text()
        has_skeleton_import = False
        try:
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("auto_adapter.skeletons"):
                        has_skeleton_import = True
                        break
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("auto_adapter.skeletons"):
                            has_skeleton_import = True
                            break
                if has_skeleton_import:
                    break
        except SyntaxError:
            # Couldn't parse — fall back to conservative substring check on
            # the IMPORT lines only (skips docstrings/comments)
            for line in src.splitlines():
                stripped = line.strip()
                if not (stripped.startswith("import ") or stripped.startswith("from ")):
                    continue
                if "auto_adapter.skeletons" in stripped:
                    has_skeleton_import = True
                    break
        report["tests"].append({
            "test": "no_skeleton_import",
            "ok": not has_skeleton_import,
            "detail": ("OK — zero skeleton imports" if not has_skeleton_import
                       else "FAIL — driver imports from auto_adapter.skeletons"),
            "metric": 0.0 if has_skeleton_import else 1.0,
        })
        if has_skeleton_import:
            return report

        # Side-load + run
        import numpy as np  # noqa: PLC0415

        orig_cwd = os.getcwd()
        sys_path_added = False
        if str(self.workspace) not in sys.path:
            sys.path.insert(0, str(self.workspace))
            sys_path_added = True
        sys.modules.pop("driver_from_scratch", None)
        try:
            os.chdir(self.workspace)
            try:
                spec = importlib.util.spec_from_file_location(
                    "driver_from_scratch", str(driver_path))
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                except Exception as e:  # noqa: BLE001 — import-time crash
                    # A repaired driver with an undefined name / bad import must
                    # NOT take down run(); record it as a failed test so the
                    # outer VAL→GEN loop can feed the traceback back for repair.
                    report["tests"].append({
                        "test": "driver_import", "ok": False,
                        "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                    })
                    return report
                robot_cls = getattr(mod, "Robot", None)
                if robot_cls is None:
                    report["tests"].append({
                        "test": "robot_class_present", "ok": False,
                        "detail": "no `Robot` class found in driver_from_scratch.py",
                        "metric": 0.0,
                    })
                    return report
                report["tests"].append({"test": "robot_class_present", "ok": True,
                                         "detail": "OK", "metric": 1.0})
                # Build. We pass the realpath (not the local symlink) so that
                # mujoco resolves any <include file="X.xml"/> relative to the
                # SOURCE directory. Agents' drivers commonly don't realpath
                # internally — that's a reasonable thing to test, but not
                # what this structural validator is checking.
                try:
                    mjcf_str = "mjcf.xml"
                    mjcf_p = self.workspace / "mjcf.xml"
                    if mjcf_p.is_symlink() or mjcf_p.exists():
                        mjcf_str = os.path.realpath(mjcf_p)
                    r = robot_cls.build_from_mjcf(mjcf_str)
                except Exception as e:  # noqa: BLE001
                    report["tests"].append({
                        "test": "build_from_mjcf", "ok": False,
                        "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                    })
                    return report
                report["tests"].append({"test": "build_from_mjcf", "ok": True,
                                         "detail": "OK", "metric": 1.0})

                # Detect robot class from available methods. Arm: has
                # get_ee_pose + move_cartesian. Quadruped: has stand_up + sit.
                # Both have home, describe, step.
                has_arm = (hasattr(r, "get_ee_pose") and hasattr(r, "move_cartesian"))
                has_quad = (hasattr(r, "stand_up") and hasattr(r, "sit"))
                has_wheeled = (hasattr(r, "drive_forward") and hasattr(r, "turn"))
                has_aerial = (hasattr(r, "takeoff") and hasattr(r, "move_to"))
                has_humanoid = (hasattr(r, "stand_balance") and hasattr(r, "squat"))

                # home (always test if present)
                if hasattr(r, "home"):
                    try:
                        r.home()
                        report["tests"].append({"test": "home", "ok": True,
                                                 "detail": "OK", "metric": 1.0})
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "home", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })

                # ── Arm-specific tests ──────────────────────────────────────
                if has_arm:
                    try:
                        ee0, _ = r.get_ee_pose()
                        target = ee0 + np.array([0.03, 0.0, 0.03])
                        r.move_cartesian(target, duration=1.5)
                        ee1, _ = r.get_ee_pose()
                        err = float(np.linalg.norm(ee1 - target))
                        report["tests"].append({
                            "test": "ik_roundtrip",
                            "ok": bool(err < 0.03),
                            "detail": f"target={target.tolist()}, reached={ee1.tolist()}, err={err:.4f}m",
                            "metric": err,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "ik_roundtrip", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": -1.0,
                        })
                    if hasattr(r, "gripper_open") and hasattr(r, "gripper_close"):
                        try:
                            r.gripper_open()
                            r.gripper_close()
                            r.gripper_open()
                            report["tests"].append({"test": "gripper_cycle", "ok": True,
                                                     "detail": "OK", "metric": 1.0})
                        except Exception as e:  # noqa: BLE001
                            report["tests"].append({
                                "test": "gripper_cycle", "ok": False,
                                "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                            })
                    # object perception — REQUIRED for the manipulation suite.
                    # Discover a name then query its world position; the result
                    # must be a 3-vector.
                    # The AGENT's driver is responsible for object perception:
                    # get_object_names() must discover the scene's objects and
                    # get_object_position(name) must return their world xyz. The
                    # validator only CALLS these — it does NOT hardcode object
                    # names or scan the model itself. If the agent didn't write a
                    # working pair, the test fails (that's the honest signal).
                    # Tests that the driver IMPLEMENTED a working perception API,
                    # not that a specific object exists. Both methods must be
                    # callable; if get_object_names() lists objects, the driver
                    # must locate the first (3-vector). An empty list is accepted
                    # (API present, scene has no manipulable objects) so this is
                    # fair to object-free arm scenes too. No hardcoded names, no
                    # model scanning — object discovery is the agent's job.
                    try:
                        gon = getattr(r, "get_object_names", None)
                        gop = getattr(r, "get_object_position", None)
                        if not callable(gon) or not callable(gop):
                            raise AttributeError(
                                "driver must expose callable get_object_names() "
                                "and get_object_position()")
                        names = list(gon())
                        gt = _scene_movable_bodies(r)
                        if names:
                            pos = np.asarray(gop(names[0]), dtype=float)
                            ok = pos.shape == (3,)
                            detail = f"names={names[:6]} {names[0]}->{pos.round(3).tolist() if ok else pos}"
                        elif gt:
                            # BYPASS GUARD: the scene HAS movable bodies (ground
                            # truth) but the driver reported none — it failed to
                            # perceive them. This must FAIL, not pass-by-skip.
                            ok = False
                            detail = (f"FAIL — get_object_names() returned [] but the "
                                      f"scene contains {len(gt)} movable bodies "
                                      f"({gt[:6]}); driver failed to perceive them.")
                        else:
                            ok = True  # genuinely object-free scene
                            detail = "perception API present; scene has no movable objects"
                        report["tests"].append({
                            "test": "object_perception", "ok": bool(ok),
                            "detail": detail, "metric": 1.0 if ok else 0.0,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "object_perception", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })
                    # grasp_lift — REAL grasp: close on an object, lift, and
                    # verify it is actually HELD (stays at the end-effector)
                    # rather than flung. gripper_cycle above only actuates the
                    # empty jaw; this is the behavior the manipulation suite
                    # actually depends on. The detail reports the observed
                    # symptom (how far the object ended from the hand, how much
                    # the rest of the scene moved) so the agent can diagnose and
                    # repair; it does not prescribe a fix.
                    if (hasattr(r, "gripper_close") and hasattr(r, "gripper_open")
                            and callable(getattr(r, "get_object_names", None))
                            and callable(getattr(r, "get_object_position", None))):
                        try:
                            gnames = list(r.get_object_names())
                            # GROUND TRUTH decides whether grasp is testable — NOT
                            # the driver's self-report. A driver that returns an
                            # empty object list cannot skip the grasp test when the
                            # scene actually has movable objects. Fall back to the
                            # ground-truth body names if the driver lists none.
                            gt = _scene_movable_bodies(r)
                            targets = gnames or gt
                            if not gt:
                                report["tests"].append({
                                    "test": "grasp_lift", "ok": True,
                                    "detail": "grasp API present; scene has no "
                                              "movable objects; skipped",
                                    "metric": 1.0})
                            else:
                                starts = {n: np.asarray(r.get_object_position(n),
                                                        dtype=float) for n in targets}
                                obj = targets[0]
                                p0 = starts[obj]
                                for tgt in (p0 + np.array([0.0, 0.0, 0.08]),
                                            p0 + np.array([0.0, 0.0, 0.005])):
                                    try: r.move_cartesian(tgt, duration=1.5)
                                    except Exception: pass  # noqa: BLE001,E701
                                try: r.gripper_close()
                                except Exception: pass  # noqa: BLE001,E701
                                try: r.move_cartesian(p0 + np.array([0.0, 0.0, 0.20]),
                                                      duration=1.5)
                                except Exception: pass  # noqa: BLE001,E701
                                ee, _ = r.get_ee_pose()
                                pf = np.asarray(r.get_object_position(obj),
                                                dtype=float)
                                dz = float(pf[2] - p0[2])
                                gap = float(np.linalg.norm(pf - ee))
                                others = sum(
                                    float(np.linalg.norm(
                                        np.asarray(r.get_object_position(n),
                                                   dtype=float) - starts[n]))
                                    for n in targets if n != obj)
                                held = gap < 0.10
                                lifted = dz > 0.03
                                ok = bool(held and lifted)
                                report["tests"].append({
                                    "test": "grasp_lift", "ok": ok,
                                    "detail": (
                                        f"closed on '{obj}' and lifted: rose "
                                        f"{dz*100:.1f}cm, object ended {gap*100:.1f}cm "
                                        f"from the end-effector (a held object stays "
                                        f"<10cm), {len(targets)-1} other objects moved "
                                        f"{others*100:.1f}cm total. " + (
                                            "OK — object held at the gripper."
                                            if ok else
                                            "FAIL — the object is NOT held at the "
                                            "end-effector after closing and lifting; "
                                            "it was displaced away from the hand "
                                            "instead of moving with it.")),
                                    "metric": gap,
                                })
                        except Exception as e:  # noqa: BLE001
                            report["tests"].append({
                                "test": "grasp_lift", "ok": False,
                                "detail": f"{type(e).__name__}: {e}",
                                "metric": -1.0,
                            })

                # ── Quadruped-specific tests ────────────────────────────────
                elif has_quad:
                    def _bh():
                        if hasattr(r, "get_body_height"):
                            return float(r.get_body_height())
                        if hasattr(r, "get_base_pose"):
                            return float(r.get_base_pose()[0][2])
                        return 0.0
                    def _bx():
                        return float(r.get_base_pose()[0][0]) if hasattr(r, "get_base_pose") else 0.0

                    stand_h = 0.0
                    # stand_up: must reach a real standing height (and raise from start)
                    try:
                        h0 = _bh(); ret = r.stand_up(duration=2.0); h1 = _bh()
                        stand_h = h1
                        report["tests"].append({
                            "test": "stand_up",
                            "ok": bool(h1 > 0.15 and h1 >= h0 - 0.02),
                            "detail": f"body height {h0:.3f} -> {h1:.3f} (need >0.15, not collapse)",
                            "metric": h1,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "stand_up", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": -1.0,
                        })
                    # sit: must ACTUALLY lower the body well below the standing
                    # height (not merely run without crashing).
                    try:
                        ret = r.sit(duration=1.5); h2 = _bh()
                        need = stand_h * 0.8
                        ok = bool(stand_h > 1e-6 and h2 < need)
                        report["tests"].append({
                            "test": "sit", "ok": ok,
                            "detail": f"stand {stand_h:.3f} -> sit {h2:.3f} (need < {need:.3f})",
                            "metric": h2,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "sit", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": -1.0,
                        })
                    # walk_forward: must produce REAL forward displacement.
                    if hasattr(r, "walk_forward"):
                        try:
                            r.stand_up(duration=1.0)
                            x0 = _bx(); r.walk_forward(secs=1.5, speed=0.2); x1 = _bx()
                            dx = x1 - x0
                            ok = bool(dx > 0.03 and _bh() > 0.12)
                            report["tests"].append({
                                "test": "walk_forward", "ok": ok,
                                "detail": f"dx={dx:+.3f} m (need >0.03, upright)",
                                "metric": dx,
                            })
                        except Exception as e:  # noqa: BLE001
                            report["tests"].append({
                                "test": "walk_forward", "ok": False,
                                "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                            })

                # ── Wheeled / mobile-base tests ─────────────────────────────
                elif has_wheeled:
                    import numpy as _np
                    def _bxy():
                        p = r.get_base_pose()[0]
                        return float(p[0]), float(p[1])
                    def _byaw():
                        if hasattr(r, "get_base_yaw"):
                            return float(r.get_base_yaw())
                        R = _np.asarray(r.get_base_pose()[1], dtype=float)
                        return float(_np.arctan2(R[1, 0], R[0, 0]))
                    # drive_forward: must produce REAL horizontal displacement
                    try:
                        x0, y0 = _bxy(); r.drive_forward(distance_m=0.3, speed=1.0)
                        x1, y1 = _bxy()
                        disp = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                        report["tests"].append({
                            "test": "drive_forward", "ok": bool(disp > 0.05),
                            "detail": f"base displacement {disp*100:.1f} cm (need >5)",
                            "metric": disp,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "drive_forward", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })
                    # turn: must change base yaw
                    try:
                        yaw0 = _byaw(); r.turn(0.785, speed=1.0)  # ~45 deg
                        dyaw = abs(((_byaw() - yaw0 + _np.pi) % (2 * _np.pi)) - _np.pi)
                        report["tests"].append({
                            "test": "turn", "ok": bool(dyaw > 0.2),
                            "detail": f"yaw change {_np.degrees(dyaw):.1f} deg (need >11)",
                            "metric": dyaw,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "turn", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })
                # ── Aerial / multirotor tests ──────────────────────────────
                elif has_aerial:
                    import numpy as _np
                    def _pos():
                        p = r.get_base_pose()[0]
                        return _np.asarray(p, dtype=float)
                    def _upright():
                        # body z-axis should still point mostly up (no flip)
                        R = _np.asarray(r.get_base_pose()[1], dtype=float)
                        return float(R[2, 2])
                    # takeoff: must gain altitude and stay upright (not flip)
                    try:
                        z0 = float(_pos()[2]); r.takeoff(height=0.5)
                        z1 = float(_pos()[2]); up = _upright()
                        ok = bool((z1 - z0) > 0.15 and up > 0.7)
                        report["tests"].append({
                            "test": "takeoff", "ok": ok,
                            "detail": f"dz={z1 - z0:+.3f} m (need >0.15), up={up:.2f} (need >0.7)",
                            "metric": z1 - z0,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "takeoff", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })
                    # move_to: must fly to a horizontal target without flipping
                    try:
                        p0 = _pos()
                        tgt = p0 + _np.array([0.3, 0.0, 0.0])
                        r.move_to(float(tgt[0]), float(tgt[1]), float(tgt[2]), tol=0.15)
                        p1 = _pos(); up = _upright()
                        err = float(_np.linalg.norm(p1 - tgt))
                        ok = bool(err < 0.25 and up > 0.7)
                        report["tests"].append({
                            "test": "move_to", "ok": ok,
                            "detail": f"target={tgt.round(2).tolist()}, reached={p1.round(2).tolist()}, err={err:.3f}m, up={up:.2f}",
                            "metric": err,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "move_to", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": -1.0,
                        })
                # ── Humanoid / biped tests ─────────────────────────────────
                elif has_humanoid:
                    import numpy as _np
                    def _th():
                        if hasattr(r, "get_torso_height"):
                            return float(r.get_torso_height())
                        return float(_np.asarray(r.get_base_pose()[0], dtype=float)[2])
                    def _up():
                        R = _np.asarray(r.get_base_pose()[1], dtype=float)
                        return float(R[2, 2])
                    # stand_balance: verify the complete real-physics interval,
                    # not only the initial/final pose after a no-op return.
                    report["tests"].append(
                        _validate_humanoid_stand_balance(
                            r, self.cfg.robot_id, self.cfg.mjcf_path, secs=2.0
                        )
                    )
                    # squat: must ACTUALLY DESCEND by ~depth then recover
                    # WITHOUT toppling. squat() is a blocking down-then-up call,
                    # so we sample torso height in a background thread to verify
                    # the descent really happened (a no-op squat must fail).
                    try:
                        import threading as _threading
                        import time as _time
                        r.stand_balance(secs=0.5)
                        h_stand = _th()
                        _samples = []
                        _stop = [False]
                        def _sampler():
                            while not _stop[0]:
                                try:
                                    _samples.append(_th())
                                except Exception:  # noqa: BLE001
                                    pass
                                _time.sleep(0.01)
                        _t = _threading.Thread(target=_sampler); _t.start()
                        r.squat(depth=0.15, secs=2.0)
                        _stop[0] = True; _t.join()
                        h_end = _th(); up = _up()
                        dip = h_stand - (min(_samples) if _samples else h_stand)
                        ok = bool(dip >= 0.05 and h_end > 0.6 and up > 0.7)
                        report["tests"].append({
                            "test": "squat", "ok": ok,
                            "detail": (f"stand h={h_stand:.2f}, dip={dip:.3f}m "
                                       f"(need >=0.05), post-squat h={h_end:.2f}, up={up:.2f}"),
                            "metric": dip,
                        })
                    except Exception as e:  # noqa: BLE001
                        report["tests"].append({
                            "test": "squat", "ok": False,
                            "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                        })
                    # humanoid_walk: NON-CRITICAL frontier probe. In-call thread
                    # sampling; progress measured in the START-HEADING frame so
                    # fall-forward / inaction / rotation cannot game it. Failing
                    # is fine (stand+squat already validate); passing lets us
                    # claim short-shuffle walking.
                    if hasattr(r, "walk_forward"):
                        try:
                            import threading as _th2
                            import time as _tm2
                            r.stand_balance(secs=0.5)
                            _bp0 = _np.asarray(r.get_base_pose()[0], dtype=float)
                            _R0 = _np.asarray(r.get_base_pose()[1], dtype=float)
                            _x0 = _R0[:, 0].copy(); _y0 = _R0[:, 1].copy()
                            _yaw0 = float(_np.arctan2(_R0[1, 0], _R0[0, 0]))
                            _min_h = [_th()]; _min_up = [_up()]; _stop2 = [False]
                            def _wsamp():
                                while not _stop2[0]:
                                    try:
                                        _min_h.append(_th()); _min_up.append(_up())
                                    except Exception:  # noqa: BLE001
                                        pass
                                    _tm2.sleep(0.01)
                            _wt = _th2.Thread(target=_wsamp); _wt.start()
                            r.walk_forward(distance_m=0.10, secs=2.0)
                            _stop2[0] = True; _wt.join()
                            _bp1 = _np.asarray(r.get_base_pose()[0], dtype=float)
                            _R1 = _np.asarray(r.get_base_pose()[1], dtype=float)
                            _disp = _bp1 - _bp0
                            _fwd = float(_disp @ _x0); _lat = float(_disp @ _y0)
                            _yaw1 = float(_np.arctan2(_R1[1, 0], _R1[0, 0]))
                            _dyaw = abs(((_yaw1 - _yaw0 + _np.pi) % (2 * _np.pi)) - _np.pi)
                            ok = bool(min(_min_h) > 0.80 and min(_min_up) > 0.65
                                      and _fwd > 0.05 and abs(_lat) < 0.08
                                      and _dyaw < 0.35)
                            report["tests"].append({
                                "test": "humanoid_walk", "ok": ok,
                                "detail": (f"fwd={_fwd:+.3f}m (need >0.05), lat={_lat:+.3f} "
                                           f"(|.|<0.08), dyaw={_np.degrees(_dyaw):.0f}deg "
                                           f"(<20), min_h={min(_min_h):.2f}, min_up={min(_min_up):.2f}"),
                                "metric": _fwd,
                            })
                        except Exception as e:  # noqa: BLE001
                            report["tests"].append({
                                "test": "humanoid_walk", "ok": False,
                                "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                            })
                else:
                    report["tests"].append({
                        "test": "class_detection",
                        "ok": False,
                        "detail": (f"unknown Robot class: not arm "
                                   f"(get_ee_pose+move_cartesian), quadruped "
                                   f"(stand_up+sit), wheeled (drive_forward+turn), "
                                   f"aerial (takeoff+move_to), nor humanoid "
                                   f"(stand_balance+squat). "
                                   f"dir() top public: "
                                   f"{[m for m in dir(r) if not m.startswith('_')][:10]}"),
                        "metric": 0.0,
                    })

                # describe sanity — accept dict (preferred) OR string (some
                # agents return a formatted summary; that's a different design
                # choice but functionally fine)
                try:
                    d = r.describe()
                    if isinstance(d, dict):
                        report["tests"].append({"test": "describe", "ok": True,
                                                 "detail": f"dict keys={list(d.keys())[:8]}",
                                                 "metric": 1.0})
                    elif isinstance(d, str):
                        report["tests"].append({"test": "describe", "ok": True,
                                                 "detail": f"str (len={len(d)})",
                                                 "metric": 1.0})
                    else:
                        report["tests"].append({"test": "describe", "ok": False,
                                                 "detail": f"unexpected type: {type(d).__name__}",
                                                 "metric": 0.0})
                except Exception as e:  # noqa: BLE001
                    report["tests"].append({
                        "test": "describe", "ok": False,
                        "detail": f"{type(e).__name__}: {e}", "metric": 0.0,
                    })

                # ── Source-code analysis: count what the agent wrote ─────
                report["source_metrics"] = {
                    "n_lines": len(src.splitlines()),
                    "n_chars": len(src),
                    "has_mujoco": ("import mujoco" in src or "from mujoco" in src),
                    "has_numpy": "numpy" in src,
                    "ik_method_hint": _guess_ik_method(src),
                }

            finally:
                os.chdir(orig_cwd)
        finally:
            if sys_path_added:
                sys.path.remove(str(self.workspace))

        n_ok = sum(1 for t in report["tests"] if t["ok"])
        report["all_ok"] = all(t["ok"] for t in report["tests"])
        # structural_ok: critical tests (driver builds + key behaviors run)
        # pass — even if non-critical metric thresholds (like describe-returns-dict)
        # don't. The point is "did the agent produce a functional driver".
        critical_tests = {"no_skeleton_import", "robot_class_present",
                          "build_from_mjcf", "home", "ik_roundtrip",
                          "object_perception", "class_detection", "grasp_lift",
                          "stand_up", "sit", "walk_forward",
                          "drive_forward", "turn",
                          "takeoff", "move_to",
                          "stand_balance", "squat"}
        critical_results = [t for t in report["tests"] if t["test"] in critical_tests]
        report["structural_ok"] = (
            bool(critical_results) and all(t["ok"] for t in critical_results)
        )
        report["n_passed"] = n_ok
        report["n_total"] = len(report["tests"])
        return report

    # ─── Top-level ────────────────────────────────────────────────────────

    def run(self) -> FromScratchResult:
        t0 = time.time()
        tok_in = tok_out = 0
        err: Optional[str] = None

        # STUDY
        r_study = self.phase_study()
        tok_in += int(r_study.total_tokens.get("in", 0))
        tok_out += int(r_study.total_tokens.get("out", 0))
        # Phase OK if the expected artifact exists, regardless of whether the
        # agent reached end_turn before max_iters. (Common pattern: agent
        # writes study.json on the final iter then hits the iter cap.)
        study_ok = (self.workspace / "study.json").exists()
        if not study_ok:
            err = f"STUDY failed: {r_study.error}"
            report = self._validate_from_scratch_driver()  # may report driver missing
            (self.workspace / "validate_report.json").write_text(json.dumps(report, indent=2))
            return FromScratchResult(
                robot_id=self.cfg.robot_id, workspace=self.workspace,
                study_ok=False, gen_ok=False, validate_ok=False,
                driver_path=None, validate_report=report,
                total_duration_sec=time.time() - t0,
                total_tokens={"in": tok_in, "out": tok_out},
                error=err,
            )

        # GEN_ALGO
        r_gen = self.phase_gen_algo()
        tok_in += int(r_gen.total_tokens.get("in", 0))
        tok_out += int(r_gen.total_tokens.get("out", 0))
        gen_ok = (self.workspace / "driver_from_scratch.py").exists()
        if not gen_ok:
            err = f"GEN_ALGO failed: {r_gen.error}; no driver_from_scratch.py"

        # VALIDATE (framework, deterministic)
        report = self._validate_from_scratch_driver()
        # validate_ok = "did the agent produce a structurally-valid driver"
        # (critical structural tests pass, even if e.g. describe() returned
        # a str instead of dict — cosmetic).
        validate_ok = report.get("structural_ok", report.get("all_ok", False))

        # OUTER VAL→GEN RETRY: if the held-out validator fails, feed the failing
        # tests back to the agent and let it repair the driver in place. This is
        # what makes the pipeline 'iterate until the structural-test suite
        # passes' rather than giving up after a single shallow bug.
        outer_attempts = 0
        while (not validate_ok and gen_ok
               and outer_attempts < self.cfg.max_outer_retries):
            outer_attempts += 1
            r_rep = self.phase_gen_repair(report, outer_attempts)
            tok_in += int(r_rep.total_tokens.get("in", 0))
            tok_out += int(r_rep.total_tokens.get("out", 0))
            gen_ok = (self.workspace / "driver_from_scratch.py").exists()
            report = self._validate_from_scratch_driver()
            validate_ok = report.get("structural_ok", report.get("all_ok", False))

        report["outer_attempts"] = outer_attempts
        (self.workspace / "validate_report.json").write_text(json.dumps(report, indent=2))

        return FromScratchResult(
            robot_id=self.cfg.robot_id, workspace=self.workspace,
            study_ok=True, gen_ok=gen_ok, validate_ok=validate_ok,
            driver_path=(self.workspace / "driver_from_scratch.py" if gen_ok else None),
            validate_report=report,
            total_duration_sec=time.time() - t0,
            total_tokens={"in": tok_in, "out": tok_out},
            error=err,
        )


# ──────────────────────────────────────────────────────────────────────────
# Helper: regex-light IK method detection from source
# ──────────────────────────────────────────────────────────────────────────


def _guess_ik_method(src: str) -> str:
    """Heuristic — look for tell-tale IK method keywords. Multi-match → list."""
    src_l = src.lower()
    hints: list[str] = []
    if "damped" in src_l or "dls" in src_l or "lambda" in src_l and "jjt" in src_l:
        hints.append("damped_least_squares")
    if "pinv" in src_l or "pseudoinverse" in src_l or "pseudo-inverse" in src_l:
        hints.append("pseudoinverse")
    if "newton" in src_l:
        hints.append("newton")
    if "gradient" in src_l:
        hints.append("gradient_descent")
    if "nullspace" in src_l:
        hints.append("nullspace_projection")
    if "transpose" in src_l and "jacobian" in src_l:
        hints.append("jacobian_transpose")
    return ", ".join(hints) if hints else "unknown"
