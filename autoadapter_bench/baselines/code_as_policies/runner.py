# SPDX-License-Identifier: Apache-2.0
"""Code-as-Policies baseline (Liang et al., ICRA 2023).

Key architectural differences from our pipeline:

  Ours (AutoAdapter)          CaP baseline
  ─────────────────────────   ──────────────────────────────────────
  Agent reads MJCF             We HAND-WRITE a perception+control API
                               and document it to the LLM
  Agent writes IK from scratch The IK lives in the API (our skeleton)
  5-phase pipeline             ONE LLM call per task (single-shot CaP)
  ReactLoop tool_use protocol  LLM emits one Python program
  Physics-validated success    (same — we use the same eval validators)

The CaP baseline is "what you'd build if you had to do this in 2023" —
a human implements the robot integration manually (using our skeleton as
the stand-in for "human-implemented stack") and the LLM only composes
high-level API calls into task programs.

This makes CaP a STRONG baseline because it's effectively given our
framework for free — the only thing it's missing vs us is the per-phase
orchestration and the algorithm-synthesis layer. Fair comparison.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ──────────────────────────────────────────────────────────────────────────
# Result type — matches TaskResult interface for drop-in eval reuse
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class CaPResult:
    """Mirrors TaskResult so the existing evaluator can ingest CaP runs."""

    task_id: str
    task_description: str
    ok: bool
    summary: str
    mp4_path: Optional[Path]
    n_frames: int
    duration_sec: float
    n_tool_calls: int             # Number of API CALLS the program made (introspected)
    trace_path: Optional[Path]
    token_usage: dict
    error: Optional[str] = None
    tool_call_log: list[dict] = field(default_factory=list)
    program_source: str = ""      # The actual Python code the LLM emitted


# ──────────────────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────────────────


_CAP_SYSTEM = """\
You are a robot programmer. The user gives you a task in English; you reply
with ONE Python program that uses ONLY the following primitives to accomplish
the task. The program runs in a sandbox where these primitives are already
bound as module-level functions. Imports of `numpy` (as `np`), `math`, and
`time` are pre-loaded.

OUTPUT FORMAT: a single fenced Python code block (```python ... ```), no
prose before or after. No `print` calls — return final state via the
primitives' side effects on the robot.

AVAILABLE PRIMITIVES (these are the only functions you may call):

  ARM (5-7 DOF serial manipulator):
    home() -> bool
        Move arm to home configuration.
    get_ee_pose() -> (np.ndarray xyz, np.ndarray R3x3)
        Current end-effector world-frame pose.
    move_to(xyz, duration=2.0) -> bool
        Move end-effector to target xyz in world frame.
        Raises RuntimeError on unreachable target.
    gripper_open() -> bool
        Open the gripper. Releases any held object.
    gripper_close() -> bool
        Close the gripper. Returns True iff an object was grasped.
    is_holding() -> bool
        Whether the gripper has an object.
    get_object_position(name: str) -> np.ndarray
        World-frame xyz of a body. Common names depend on the scene
        (banana, mug, bottle, screwdriver, duck, lego for SO-101).
    get_joint_positions() -> np.ndarray
        Current arm joint angles.

  QUADRUPED (4-leg locomotion robot):
    stand_up(duration=2.0) -> bool
    sit(duration=1.5) -> bool
    walk_forward(secs=2.0, speed=0.2) -> bool
    get_body_height() -> float
    get_base_pose() -> (np.ndarray xyz, np.ndarray R3x3)
    get_joint_positions() -> np.ndarray

  WHEELED (differential-drive mobile base):
    drive_forward(distance_m=0.3, speed=1.0) -> bool
        Drive straight forward by distance_m meters.
    turn(angle_rad, speed=1.0) -> bool
        Turn in place by angle_rad radians (+ = left/CCW).
    get_base_pose() -> (np.ndarray xyz, np.ndarray R3x3)
    get_base_yaw() -> float

  AERIAL (free-flying multirotor / quadcopter):
    takeoff(height=0.5) -> bool
        Climb to ~height m altitude and hold a stable upright hover.
    move_to(x, y, z, tol=0.1) -> bool
        Fly to absolute world point (x, y, z) m, staying upright.
        NOTE: three SCALAR args (not a vector).
    hover(secs=2.0) -> bool
        Station-keep at the current pose for secs seconds.
    land() -> bool
    get_base_pose() -> (np.ndarray xyz, np.ndarray R3x3)
        World position + orientation of the drone body.

  HUMANOID (bipedal robot):
    stand_balance(secs=3.0) -> bool
        Hold a stable upright standing pose for secs seconds.
    squat(depth=0.15, secs=3.0) -> bool
        Lower the torso by depth m then return to standing, balanced.
    get_torso_height() -> float
    get_base_pose() -> (np.ndarray xyz, np.ndarray R3x3)

CONVENTIONS:
  - All positions are world-frame meters.
  - For grasping: APPROACH 5cm above the body, DESCEND to within 2cm,
    gripper_close, then LIFT. The robot's grasp_radius is ~8cm; closer
    is more reliable.
  - On exception, your program exits without retry — write defensively
    (try/except around motions you expect to fail).

You will be told which primitives are actually available for this robot.
Use only those.
"""


# Few-shot exemplars (CaP-faithful "prompt with example programs", Liang et
# al. 2023 §3), delivered in the CANONICAL chat few-shot format: interleaved
# user/assistant turns demonstrating (task -> program) pairs, then the real
# task. Exemplar tasks are DELIBERATELY DIFFERENT from any benchmark task to
# avoid leakage — they only teach the API-composition style (read state ->
# compute target -> act -> verify). Class-matched: arm tasks see arm
# exemplars, quadrupeds see locomotion exemplars. Used when few_shot=True;
# default runs stay zero-shot.
_CAP_FEWSHOT_EXEMPLARS: dict[str, list[tuple[str, str]]] = {
    "arm": [
        (
            "Raise the end-effector 4 cm in +Z, hold briefly, then return home.",
            "xyz, R = get_ee_pose()\n"
            "target = xyz.copy()\n"
            "target[2] += 0.04\n"
            "try:\n"
            "    move_to(target, duration=2.0)\n"
            "    time.sleep(0.2)\n"
            "except RuntimeError:\n"
            "    pass            # target unreachable; skip the motion\n"
            "home()",
        ),
        (
            "Open the gripper, then close it and report whether it grasped.",
            "gripper_open()\n"
            "time.sleep(0.1)\n"
            "grasped = gripper_close()\n"
            "# `grasped` is True iff an object was captured",
        ),
    ],
    "quadruped": [
        (
            "Stand up, take one brief walk, and confirm you stayed upright.",
            "stand_up(duration=2.0)\n"
            "h0 = get_body_height()\n"
            "walk_forward(secs=0.5, speed=0.15)\n"
            "h1 = get_body_height()\n"
            "# h1 near h0 confirms the robot stayed upright after the walk",
        ),
        (
            "Sit down to a low pose, then stand back up.",
            "stand_up(duration=2.0)\n"
            "sit(duration=1.5)\n"
            "time.sleep(0.1)\n"
            "stand_up(duration=2.0)",
        ),
    ],
}


def _fewshot_messages(robot_class: str, available_primitives: list[str]
                      ) -> list[dict]:
    """Build interleaved user/assistant exemplar turns for the robot class.
    Returns [] when no exemplars match (keeps the call site simple)."""
    key = "quadruped" if "quad" in robot_class.lower() else "arm"
    msgs: list[dict] = []
    for task, program in _CAP_FEWSHOT_EXEMPLARS.get(key, []):
        msgs.append({"role": "user",
                     "content": _build_user_message(task, robot_class,
                                                    available_primitives)})
        msgs.append({"role": "assistant",
                     "content": f"```python\n{program}\n```"})
    return msgs


def _build_user_message(task_prompt: str, robot_class: str,
                         available_primitives: list[str]) -> str:
    return (
        f"Robot class: {robot_class}\n"
        f"Available primitives: {', '.join(available_primitives)}\n\n"
        f"Task: {task_prompt}\n\n"
        f"Respond with a single Python code block."
    )


# ──────────────────────────────────────────────────────────────────────────
# Sandbox — bind skeleton methods + capture call log + frame capture
# ──────────────────────────────────────────────────────────────────────────


class _FrameCapture:
    """Same frame-capture as TaskPlanner — monkey-patches skel.step()."""

    def __init__(self, skel, capture_every: int = 10, max_frames: int = 3000) -> None:
        self._skel = skel
        self._capture_every = int(capture_every)
        self._max_frames = int(max_frames)
        self.frames: list[np.ndarray] = []
        self._step_count = 0
        self._orig_step = skel.step

        def _patched(n: int = 1) -> None:
            for _ in range(int(n)):
                self._orig_step(1)
                self._step_count += 1
                if (self._step_count % self._capture_every) == 0 \
                        and len(self.frames) < self._max_frames:
                    try:
                        self.frames.append(skel.render())
                    except Exception:
                        pass

        skel.step = _patched  # type: ignore[method-assign]

    def uninstall(self) -> None:
        self._skel.step = self._orig_step  # type: ignore[method-assign]


def _build_sandbox(skel, call_log: list[dict]) -> tuple[dict, list[str]]:
    """Bind skeleton methods as module-level primitives. Each call appends
    to `call_log` so we can compare to TaskPlanner's tool-use breakdown.
    Returns (sandbox_globals, available_primitive_names).
    """
    sandbox: dict[str, Any] = {
        "__builtins__": __builtins__,
        "np": np, "math": __import__("math"), "time": __import__("time"),
    }
    available: list[str] = []

    def _serialize_input(name: str, args: tuple, kwargs: dict) -> dict:
        """Convert (args, kwargs) → `input` dict matching TaskPlanner format
        so eval._replay_tool_calls can re-execute identically.
        """
        inp: dict = {}
        if name == "move_cartesian":
            # Arm: single 3-vector position arg → x, y, z; duration kwarg
            if args:
                xyz = np.asarray(args[0], dtype=np.float64).ravel()
                if xyz.shape == (3,):
                    inp["x"], inp["y"], inp["z"] = [float(v) for v in xyz]
            if "duration" in kwargs:
                inp["duration"] = float(kwargs["duration"])
            elif len(args) >= 2:
                inp["duration"] = float(args[1])
        elif name == "move_to":
            # Aerial: three SCALAR args move_to(x, y, z, tol=0.1)
            vals = list(args)
            if len(vals) >= 3:
                inp["x"], inp["y"], inp["z"] = float(vals[0]), float(vals[1]), float(vals[2])
            else:
                for k in ("x", "y", "z"):
                    if k in kwargs:
                        inp[k] = float(kwargs[k])
            tol = kwargs.get("tol", vals[3] if len(vals) > 3 else None)
            if tol is not None:
                inp["tol"] = float(tol)
        elif name == "takeoff":
            inp["height"] = float(kwargs.get("height", args[0] if args else 0.5))
        elif name == "hover":
            inp["secs"] = float(kwargs.get("secs", args[0] if args else 2.0))
        elif name == "drive_forward":
            inp["distance_m"] = float(kwargs.get("distance_m", args[0] if args else 0.3))
            inp["speed"] = float(kwargs.get("speed", args[1] if len(args) > 1 else 1.0))
        elif name == "turn":
            inp["angle_rad"] = float(kwargs.get("angle_rad", args[0] if args else 0.0))
            inp["speed"] = float(kwargs.get("speed", args[1] if len(args) > 1 else 1.0))
        elif name == "stand_balance":
            inp["secs"] = float(kwargs.get("secs", args[0] if args else 3.0))
        elif name == "squat":
            inp["depth"] = float(kwargs.get("depth", args[0] if args else 0.15))
            inp["secs"] = float(kwargs.get("secs", args[1] if len(args) > 1 else 3.0))
        elif name == "get_object_position":
            if args:
                inp["body_name"] = str(args[0])
            elif "name" in kwargs:
                inp["body_name"] = str(kwargs["name"])
        elif name in ("home", "stand_up", "sit"):
            if "duration" in kwargs:
                inp["duration"] = float(kwargs["duration"])
            elif args:
                inp["duration"] = float(args[0])
        elif name == "walk_forward":
            inp["secs"] = float(kwargs.get("secs",
                                            args[0] if len(args) > 0 else 2.0))
            inp["speed"] = float(kwargs.get("speed",
                                             args[1] if len(args) > 1 else 0.2))
        # else: zero-arg methods (get_ee_pose, gripper_open/close, is_holding,
        # get_joint_positions, get_body_height, get_base_pose) → empty input
        return inp

    def _wrap(name: str, fn):
        def _wrapped(*args, **kwargs):
            t0 = time.time()
            inp = _serialize_input(name, args, kwargs)
            try:
                result = fn(*args, **kwargs)
                call_log.append({
                    "tool": name,
                    "input": inp,        # TaskPlanner-compatible
                    "ok": True,
                    "dur_ms": (time.time() - t0) * 1000.0,
                })
                return result
            except Exception as e:
                call_log.append({
                    "tool": name,
                    "input": inp,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "dur_ms": (time.time() - t0) * 1000.0,
                })
                raise
        return _wrapped

    # Inspect what methods the skel actually has + bind them
    arm_methods = ["home", "get_ee_pose", "get_object_position",
                   "gripper_open", "gripper_close", "is_holding",
                   "get_joint_positions"]
    quad_methods = ["stand_up", "sit", "walk_forward",
                    "get_body_height", "get_base_pose",
                    "get_joint_positions", "home"]
    wheeled_methods = ["drive_forward", "turn", "get_base_pose", "get_base_yaw"]
    # aerial "move_to" is the driver's NATIVE 3-scalar move_to (bound below in
    # the loop, logged as "move_to"); distinct from the arm move_cartesian alias.
    aerial_methods = ["takeoff", "move_to", "hover", "land", "get_base_pose"]
    humanoid_methods = ["stand_balance", "squat", "get_torso_height",
                        "get_base_pose"]

    has_move_cartesian = hasattr(skel, "move_cartesian")
    if has_move_cartesian:
        def move_to(xyz, duration=2.0):
            xyz_arr = np.asarray(xyz, dtype=np.float64)
            if xyz_arr.shape != (3,):
                raise ValueError(f"move_to expects 3-vector, got {xyz_arr.shape}")
            return skel.move_cartesian(xyz_arr, duration=float(duration))
        # NOTE: sandbox exposes the function as "move_to" to the LLM (CaP-style
        # naming convention), but the call_log records the SKELETON method name
        # `move_cartesian` so the eval.py replay machinery (which dispatches
        # by skeleton method) reproduces movement correctly. The LLM doesn't
        # know about move_cartesian — sandbox keeps the move_to alias.
        sandbox["move_to"] = _wrap("move_cartesian", move_to)
        available.append("move_to")

    for m in (arm_methods + quad_methods + wheeled_methods
              + aerial_methods + humanoid_methods):
        if hasattr(skel, m) and m not in sandbox:
            sandbox[m] = _wrap(m, getattr(skel, m))
            available.append(m)
    return sandbox, sorted(set(available))


# ──────────────────────────────────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────────────────────────────────


def _extract_code(text: str) -> str:
    """Pull the python block out of the model's reply."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text.strip()


class CaPPlanner:
    """Drop-in CaP equivalent of TaskPlanner.

    Usage:
        with CaPPlanner(workspace=path, bedrock_model=...) as p:
            r = p.execute_task("pick the banana and lift 10cm",
                               task_id="my_task")
    """

    def __init__(
        self,
        *,
        workspace: Path,
        bedrock_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region: str = "us-east-1",
        max_tokens: int = 4000,
        capture_every: int = 10,
        run_tag: Optional[str] = None,
        few_shot: bool = False,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.run_tag = run_tag
        self.few_shot = bool(few_shot)
        if not (self.workspace / "driver.py").exists() and \
           not (self.workspace / "driver_from_scratch.py").exists():
            raise FileNotFoundError(
                f"workspace {self.workspace} has no driver — CaP needs the "
                "framework driver to expose its primitives"
            )
        self.bedrock_model = bedrock_model
        self.region = region
        self.max_tokens = int(max_tokens)
        self.capture_every = int(capture_every)
        self._added_paths: list[str] = []
        for p in (str(self.workspace), str(Path(__file__).resolve().parents[3])):
            if p not in sys.path:
                sys.path.insert(0, p)
                self._added_paths.append(p)
        self._sub = f"/{run_tag}" if run_tag else ""
        self.rec_dir = self.workspace / f"recordings{self._sub}"
        self.trace_dir = self.workspace / f"task_traces{self._sub}"
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "CaPPlanner":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for p in self._added_paths:
            if p in sys.path:
                sys.path.remove(p)

    def _load_driver(self):
        """Identical to TaskPlanner._load_driver — supports both styles."""
        sys.modules.pop("driver", None)
        target = self.workspace / "driver.py"
        if not target.exists():
            target = self.workspace / "driver_from_scratch.py"
        spec = importlib.util.spec_from_file_location("driver", str(target))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        orig_cwd = os.getcwd()
        os.chdir(self.workspace)
        try:
            spec.loader.exec_module(mod)
            if hasattr(mod, "build") and callable(getattr(mod, "build")):
                skel = mod.build()
            elif hasattr(mod, "Robot") and hasattr(mod.Robot, "build_from_mjcf"):
                skel = mod.Robot.build_from_mjcf("mjcf.xml")
            else:
                raise AttributeError(f"driver at {target} has no build()/Robot.build_from_mjcf")
        finally:
            os.chdir(orig_cwd)
        return skel

    def _detect_robot_class(self, skel) -> str:
        if hasattr(skel, "move_cartesian") and hasattr(skel, "get_ee_pose"):
            return "arm"
        if hasattr(skel, "stand_up") and hasattr(skel, "sit"):
            return "quadruped"
        if hasattr(skel, "takeoff") and hasattr(skel, "move_to"):
            return "aerial"
        if hasattr(skel, "stand_balance") and hasattr(skel, "squat"):
            return "humanoid"
        if hasattr(skel, "drive_forward") and hasattr(skel, "turn"):
            return "wheeled"
        return "other"

    def execute_task(
        self,
        task_description: str,
        *,
        task_id: Optional[str] = None,
        capture_video: bool = True,
    ) -> CaPResult:
        from auto_adapter.agent.converse_client import (  # noqa: PLC0415
            ConverseClient, is_anthropic_model,
        )

        if task_id is None:
            task_id = f"cap_{int(time.time())}"

        skel = self._load_driver()
        # Settle to home pose for parity with TaskPlanner.execute_task and
        # eval.py's before_state recording. Without this, CaP's generated
        # code sees an unsettled MJCF pose (~3 mm shift on the push scene),
        # creating an asymmetric observation gap vs ReAct.
        if hasattr(skel, "home") and callable(getattr(skel, "home")):
            try:
                skel.home()
            except Exception:  # noqa: BLE001
                pass
        capture = _FrameCapture(skel, capture_every=self.capture_every)
        call_log: list[dict] = []
        sandbox, primitives = _build_sandbox(skel, call_log)
        robot_class = self._detect_robot_class(skel)

        if is_anthropic_model(self.bedrock_model):
            from anthropic import AnthropicBedrock  # noqa: PLC0415
            client = AnthropicBedrock(aws_region=self.region, timeout=300.0, max_retries=0)
        else:
            client = ConverseClient(region=self.region)
        user_msg = _build_user_message(task_description, robot_class, primitives)

        # Persist the trace
        trace_path = self.trace_dir / f"cap_{task_id}.txt"

        t0 = time.time()
        # Newer reasoning models (e.g. claude-opus-4-8) deprecate temperature.
        _drop_temp = "opus-4-8" in self.bedrock_model or "opus-4-9" in self.bedrock_model
        messages: list[dict] = []
        if self.few_shot:
            messages.extend(_fewshot_messages(robot_class, primitives))
        messages.append({"role": "user", "content": user_msg})
        _kwargs = dict(
            model=self.bedrock_model,
            max_tokens=self.max_tokens,
            system=_CAP_SYSTEM,
            messages=messages,
        )
        if not _drop_temp:
            _kwargs["temperature"] = 0.0
        try:
            resp = client.messages.create(**_kwargs)
            tokens_in = int(getattr(resp.usage, "input_tokens", 0))
            tokens_out = int(getattr(resp.usage, "output_tokens", 0))
            llm_text = "".join(b.text for b in resp.content if b.type == "text")
        except Exception as e:
            capture.uninstall()
            return CaPResult(
                task_id=task_id, task_description=task_description,
                ok=False, summary="",
                mp4_path=None, n_frames=0,
                duration_sec=time.time() - t0,
                n_tool_calls=0, trace_path=None,
                token_usage={"in": 0, "out": 0},
                error=f"bedrock invoke failed: {type(e).__name__}: {e}",
            )

        program = _extract_code(llm_text)

        # Persist the program for reproducibility
        trace_path.write_text(
            f"# TASK: {task_description}\n"
            f"# ROBOT_CLASS: {robot_class}\n"
            f"# PRIMITIVES: {primitives}\n"
            f"# tokens in={tokens_in} out={tokens_out}\n"
            f"# === RAW LLM REPLY ===\n{llm_text}\n"
            f"# === EXTRACTED CODE ===\n{program}\n"
        )

        # Execute the program in the sandbox
        error: Optional[str] = None
        stderr_buf = io.StringIO()
        try:
            sys.stderr = stderr_buf
            exec(compile(program, f"<cap_{task_id}>", "exec"), sandbox)
        except Exception:
            error = traceback.format_exc(limit=3)
        finally:
            sys.stderr = sys.__stderr__
            err_text = stderr_buf.getvalue()
            if err_text:
                error = (error or "") + "\nSTDERR:\n" + err_text
            capture.uninstall()

        dur = time.time() - t0

        # Save mp4
        mp4_path: Optional[Path] = None
        if capture_video and capture.frames:
            import imageio  # noqa: PLC0415

            mp4_path = self.rec_dir / f"cap_{task_id}.mp4"
            try:
                imageio.mimsave(str(mp4_path), capture.frames,
                                format="FFMPEG", fps=30, codec="libx264",
                                pixelformat="yuv420p",
                                ffmpeg_params=["-movflags", "+faststart"])
            except Exception as e:  # noqa: BLE001
                print(f"!! failed to save {mp4_path}: {e} "
                      f"(install imageio-ffmpeg)", file=sys.stderr)
                mp4_path = None

        # Self-reported success: did the program complete without exception
        # AND did all tool calls succeed?
        any_call_fail = any(not c["ok"] for c in call_log)
        ok = (error is None) and (not any_call_fail) and len(call_log) > 0
        summary = (
            "Program executed successfully."
            if ok
            else f"Program failed: {error[:200] if error else 'no tool calls made'}"
        )

        return CaPResult(
            task_id=task_id,
            task_description=task_description,
            ok=ok,
            summary=summary,
            mp4_path=mp4_path,
            n_frames=len(capture.frames),
            duration_sec=dur,
            n_tool_calls=len(call_log),
            trace_path=trace_path,
            token_usage={"in": tokens_in, "out": tokens_out},
            error=error,
            tool_call_log=call_log,
            program_source=program,
        )
