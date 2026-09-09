# SPDX-License-Identifier: Apache-2.0
"""SelfAssemble: 5-phase orchestrator for the auto_adapter agent.

Implements DESIGN.md §0.7 (3-layer architecture) + §0.9 (ReAct loop spec).

Pipeline:
    STUDY     — parse MJCF, build a capability map (joints / dof / class)
    GENERATE  — pick a skeleton, fill its Spec, write driver.py
    VALIDATE  — scp driver.py to DGX, run a behavior test, fetch report
    EXPORT    — generate an MCP server stub exposing validated skills
    DEMO      — run the end-to-end demo on DGX, record a video

The orchestrator owns:
    * one AgentCore CodeInterpreter session (shared across phases)
    * one DGX ssh/scp binding (set up once, used by VALIDATE + DEMO)
    * the workspace layout under `<workspace_root>/<robot_id>/`

Each phase is one `ReactLoop.run()` call with a phase-specific (system_prompt,
tools, user_message) tuple. Phase output is parsed into a structured artifact
saved under `<workspace>/<artifact_name>`. A failing phase short-circuits the
rest of the pipeline; partial results are still returned.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .agent import ReactLoop, ReactResult, ToolSpec
from .robot_catalog import SKELETON_FOR_CLASS, find_robot_definition
from .agent.tools import (
    make_execute_python_tool,
    make_inspect_skeleton_tool,
    make_list_skeletons_tool,
    make_local_exec_tool,
    make_read_file_tool,
    make_scp_tools,
    make_ssh_dgx_exec_tool,
    make_write_file_tool,
)


# ──────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class SelfAssembleConfig:
    """Configuration for one SelfAssemble run.

    The workspace layout is `<workspace_root>/<robot_id>/` and is fully
    self-contained: every artifact the agent produces (study.json,
    driver.py, traces/, etc.) lives there.
    """

    robot_id: str
    mjcf_path: Path  # local path to the input MJCF
    workspace_root: Path  # `<workspace_root>/<robot_id>/` will be created
    # "local" → run VALIDATE/DEMO on this Mac via local_exec.
    # "dgx"   → run VALIDATE/DEMO on `dgx_host` via ssh_dgx_exec + scp_*.
    mode: str = "local"
    # "framework" → orchestrator deterministically exercises the driver
    #               (driver.build() + per-class smoke calls). No LLM. Fast,
    #               reliable, doesn't burn iters trying to write a test
    #               script.  Default — matches the benchmark choke fix.
    # "agent"     → LLM writes validate.py + behavior tests + records mp4
    #               (the old behavior). Use when you want deeper validation
    #               including agent-judged behavior correctness.
    validate_mode: str = "framework"
    aws_region: str = "us-east-1"
    bedrock_model: str = "us.anthropic.claude-sonnet-4-6"
    dgx_host: str = "YOUR_DGX_HOST"
    dgx_remote_workspace: str = "/home/USER/auto_adapter_workspace"
    ci_id: str = "aws.codeinterpreter.v1"
    ci_session_timeout_sec: int = 900

    # Per-phase iteration caps. STUDY+GENERATE are LLM-heavy; VALIDATE+DEMO
    # mostly shell out, so they need fewer turns.
    max_iters_study: int = 16
    max_iters_generate: int = 22
    max_iters_validate: int = 25
    max_iters_export: int = 8
    max_iters_demo: int = 18

    # Outer GEN←VAL retry loop. If VALIDATE structural tests fail, the
    # orchestrator can re-enter GENERATE with the previous failure-detail
    # injected into the prompt context. This implements the "until perfect"
    # semantics over the physical-environment feedback signal at the
    # outer-loop level. In practice GENERATE's inner exec_python loop
    # usually catches its own mistakes, so this outer loop fires rarely.
    # Set to 1 to disable (single GEN+VAL pass).
    max_outer_gen_val_iters: int = 3

    # 8000 fits Sonnet's longest reasonable single-turn output (a ~500-line
    # code block + commentary). Bumping above this saves few real cases but
    # costs more on every turn since input grows monotonically with the trace.
    max_tokens_per_turn: int = 8000
    # Trusted evaluator input. Catalog entries supply this automatically;
    # generated study.json never determines which morphology must pass.
    expected_robot_class: Optional[str] = None


@dataclass
class PhaseResult:
    """One phase's outcome."""

    name: str
    ok: bool
    duration_sec: float
    trace_path: Optional[Path] = None
    artifact_paths: list[Path] = field(default_factory=list)
    final_text: str = ""
    error: Optional[str] = None
    token_usage: dict = field(default_factory=dict)
    # Arbitrary phase-level metadata. Used e.g. by the outer GEN←VAL loop
    # to record how many outer iterations were needed.
    metadata: dict = field(default_factory=dict)


@dataclass
class SelfAssembleResult:
    """Aggregate result across the 5 phases."""

    robot_id: str
    workspace: Path
    phases: list[PhaseResult]
    ok: bool  # True iff all phases reached `ok=True`

    def to_json(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "workspace": str(self.workspace),
            "ok": self.ok,
            "phases": [
                {
                    "name": p.name,
                    "ok": p.ok,
                    "duration_sec": p.duration_sec,
                    "trace_path": str(p.trace_path) if p.trace_path else None,
                    "artifacts": [str(a) for a in p.artifact_paths],
                    "error": p.error,
                    "token_usage": p.token_usage,
                    "metadata": p.metadata or {},
                }
                for p in self.phases
            ],
        }


# ──────────────────────────────────────────────────────────────────────────
# Phase prompts (one per phase, easy to iterate on)
# ──────────────────────────────────────────────────────────────────────────


_STUDY_SYSTEM = """\
You are Phase 1 STUDY of an auto-adapter pipeline. Your job is to look at \
the robot's MJCF file and produce a capability map JSON.

IMPORTANT: keep each execute_python call short (≤150 lines of code). If your \
analysis needs more code, split it across multiple calls — the CI session \
preserves Python state across calls (imports persist, variables persist).

Procedure:
  1. read_file the MJCF (path is given in the user message).
  2. execute_python: parse the MJCF (xml.etree.ElementTree works; mujoco is \
also available if needed). Extract:
       - body tree (root → leaves) and identify the end-effector body/site
       - joint list with name, type, axis, ctrlrange, qpos limits
       - actuator list with name, joint binding, ctrlrange
       - any sites (ee_site is critical for arms)
       - any equality constraints already in the XML
  3. execute_python: classify the robot as one of: "arm" | "quadruped" | \
"dexterous_hand" | "mobile_manipulator" | "bimanual" | "mobile_base" | \
"humanoid" | "aerial" | "unknown". Inspect the whole robot: four fingers \
on a palm → dexterous_hand; a wheeled base AND an arm → mobile_manipulator; \
two arm chains in the same world → bimanual; legs ≥ 4 with hip+knee \
joints → quadruped; a single fixed serial chain → arm. Do not classify \
Stretch or ALOHA as an ordinary arm by inspecting only one chain.
  4. write_file the result as `study.json` (workspace-relative) with this \
exact schema:
       {
         "robot_id": "<id>",
         "estimated_class": "arm|quadruped|dexterous_hand|mobile_manipulator|bimanual|mobile_base|humanoid|aerial|unknown",
         "dof": <int>,
         "joints": [{"name":..., "type":..., "axis": [x,y,z], "ctrlrange":[lo,hi]}, ...],
         "actuators": [{"name":..., "joint":..., "ctrlrange":[lo,hi]}, ...],
         "ee_site": "<site name or null>",
         "ee_body": "<leaf body name>",
         "graspable_bodies": ["<name>", ...],     // free-joint bodies you found
         "notes": "<one-line summary>"
       }
  5. Reply with one sentence confirming the file was written and listing \
the chosen class + dof.
"""


_GENERATE_SYSTEM = """\
You are Phase 2 GENERATE. Phase 1 STUDY produced study.json. Your job is to \
pick a skeleton from the framework and produce a `driver.py` that instantiates \
it with a correctly filled Spec.

IMPORTANT: keep each execute_python call short (≤150 lines). Split across \
calls if needed — CI session state persists.

Procedure:
  1. read_file study.json.
  2. list_skeletons to see what's available.
  3. inspect_skeleton on the most appropriate one (arm → ArmSerialDLSSkeleton; \
quadruped → QuadrupedPDGaitSkeleton; dexterous_hand → HandFingertipDLSSkeleton; \
mobile_manipulator → StretchMobileManipulationSkeleton; bimanual → \
BimanualSerialDLSSkeleton) to get the Spec field schema. For bimanual, \
inspect ArmSerialDLSSkeleton too: the bimanual spec contains left/right \
ArmSpec values, both operating on one shared model/data. Read the model \
keyframes: for ALOHA use the collision-free `neutral_pose` as \
`initial_keyframe`, and derive both arm home_qpos vectors from that keyframe \
rather than the all-zero qpos0. The hand must \
map all four fingertips (index, middle, ring, thumb); Stretch must expose \
both base and arm control. Never substitute an ordinary arm for these \
composite morphologies. For arm specs, NOTE the `grasp_backend` field — pick:
       - `"weld"` if the MJCF has explicit `<equality><weld .../>` constraints \
between the gripper body and any graspable bodies (typical for demo MJCFs \
like SO-101)
       - `"contact"` if the gripper is a physical 2-finger / parallel-jaw \
that should grasp via actual contact + friction (typical for Franka, UR5)
       - `"noop"` if the robot has no gripper at all
       - leave as `None` to let the framework auto-pick based on spec content
  4. execute_python: re-open the MJCF to derive any spec values the study \
missed (joint_limits dict, home_qpos guess, ee_site_name or ee_body_name, \
etc.). For the EE reference: prefer ee_site_name when the MJCF defines a \
`<site>` near the end-effector. If NO suitable site exists (e.g. Franka \
panda.xml has none), set ee_body_name to the EE BODY instead (e.g. 'hand', \
'gripper_link', or the leaf body of the arm chain). Don't set both. PRINT \
a draft spec dict literally so you can paste it into driver.py.
  5. **Gripper-direction probe (arms only).** Determining which ctrlrange \
end CLOSES the gripper is non-obvious from the MJCF alone — actively probe \
it. Use local_exec to run a short python script:
       - load the MJCF via `mujoco.MjModel.from_xml_path(...)` + `mujoco.MjData(model)`
       - find the gripper actuator id, find the gripper joint id (qpos slot)
       - try ctrl=ctrlrange.min(): `data.ctrl[a]=min; for _ in range(200): mj_step(...)`; \
record `data.qpos[j]` as `qpos_at_min`
       - reset, repeat with ctrl=ctrlrange.max(); record `qpos_at_max`
       - print both. The end whose qpos is CLOSER TO ZERO (smaller |qpos|) is \
the CLOSED position for a typical revolute jaw. For a prismatic finger, the \
end with smaller stroke is closed.
       - Use the probe result to set gripper_close_ctrl / gripper_open_ctrl \
in the spec — DO NOT just guess based on ctrlrange.min() vs max().
  6. write_file `driver.py` (workspace-relative). It must:
       - import the chosen Skeleton + Spec from auto_adapter.skeletons
       - define a `build()` function that returns the constructed skeleton \
         (loading the MJCF via SkeletonBase.from_mjcf classmethod)
       - keep the Spec values verbatim from your derived draft — no \
         placeholders, no "TODO".
  7. local_exec: `python -c "import driver; skel = driver.build(); print(skel.describe())"` \
to prove the driver constructs without errors.
  8. Reply with one sentence summarizing what you wrote + which ctrl is close \
vs open per your probe.
"""


_VALIDATE_SYSTEM_DGX = """\
You are Phase 3 VALIDATE. Your job is to copy driver.py to the DGX, run a \
behavior smoke test there, and pull back a report.

Procedure:
  1. scp_to_dgx driver.py and the MJCF (workspace-relative names; remote \
relpaths under the same basename).
  2. write_file a `validate.py` test script (workspace-relative) that:
       - imports driver, builds the skeleton, runs a minimal behavior \
(arm: home() then move_cartesian to a small offset; quadruped: walk \
forward 1s), prints a JSON line `{"behavior":"...","ok":true/false,...}`
  3. scp_to_dgx validate.py.
  4. ssh_dgx_exec to run validate.py with `python validate.py` (assume \
mujoco is on the DGX PYTHONPATH inside the conda env you used before).
  5. Parse the stdout JSON line. write_file the parsed result as \
`validate_report.json`.
  6. Reply with one sentence: behavior + ok/fail + key metric.
"""


_VALIDATE_SYSTEM_LOCAL = """\
You are Phase 3 VALIDATE. driver.py is in the workspace and you run everything \
locally — no scp, no ssh. The workspace cwd already has driver.py + mjcf.xml \
+ study.json, and PYTHONPATH includes both the workspace and the auto_adapter \
repo, so scripts can `import driver` and `import auto_adapter.skeletons` \
directly.

RECORDING: every behavior you test MUST also record video frames so a human \
reviewer can see what the robot actually did (jitter, falls, near-miss \
grasps, etc.). A `recordings/` directory exists in the workspace — save \
attempt mp4s there with names like `recordings/validate_<test>_<int(time.time())>.mp4`. \
Use imageio: `imageio.mimsave(path, frames, fps=30, codec="libx264")`.

Procedure:
  1. read_file study.json. Note `estimated_class` — the test set depends on it.
  2. write_file a `validate.py` test script (workspace-relative). For each \
test, capture frames (skel.render() every ~5 sim steps; aim for 30-60 frames) \
and print one JSON line per test:
       {"test":"<name>","ok":<bool>,"detail":"<what happened>","metric":<number>,"recording":"recordings/validate_<name>_<ts>.mp4"}
     End the script with `print("___END___")`.

  ── If estimated_class == "arm" ──
       test_a "ik_roundtrip": move_cartesian(home_pose + [0.05, 0, 0.05]), \
read back ee pose. metric = position error in meters (ok if < 0.01).
       test_b "grasp_lift": (a) APPROACH 5cm above the first graspable body, \
(b) DESCEND to 2cm above — THIS DESCENT IS REQUIRED, otherwise gripper_close \
fires while EE is still outside grasp_radius, (c) gripper_close, (d) LIFT \
+0.10 m in Z, (e) read body Z height. metric = lift_height_m (ok if > 0.05).

  ── If estimated_class == "quadruped" ──
       test_a "stand_up": call skel.stand_up(duration=2.0). \
metric = final body height in meters (ok if > 0.20). Capture frames \
throughout the stand_up motion.
       test_b "walk_forward": call skel.walk_forward(secs=3.0, speed=0.3). \
metric = forward displacement (X) in meters (ok if > 0.05). Capture frames \
throughout the walk. NOTE: the hand-tuned trot can be fragile — record what \
actually happened even if displacement is small.
       test_c "sit": call skel.sit(duration=1.5). \
metric = final body height in meters (ok if < spec body_height_target × 0.7).

  ── For any other class ──
       Run whatever behavior tests the skeleton exposes (introspect via \
`dir(skel)`), capture frames, report metrics.

  3. local_exec to run `python validate.py 2>&1`. timeout_sec=240.
  4. Parse stdout JSON lines. write_file `validate_report.json` with \
`{"tests": [...], "all_ok": <bool>}`.
  5. Reply with one sentence: test names + each ok/fail + key metrics + \
recording paths.
"""


_EXPORT_SYSTEM = """\
You are Phase 4 EXPORT. Phase 2 produced driver.py and Phase 3 confirmed \
it works. Generate an MCP server stub that exposes the skeleton's behaviors \
as MCP tools.

Use the official Python MCP SDK (already installed): \
`from mcp.server.fastmcp import FastMCP`.

CRITICAL: Only register MCP tools that wrap methods that ACTUALLY exist on the \
skeleton. DO NOT INVENT methods like `grasp(body_name)` or `release()` — these \
are not on ArmSerialDLSSkeleton. ALWAYS introspect first (step 2 below) and \
use the EXACT signatures you discover, including default values.

Procedure:
  1. read_file driver.py and validate_report.json.
  2. local_exec to introspect the actual skeleton API: \
`python -c "import driver, inspect; skel=driver.build(); methods=[(m, str(inspect.signature(getattr(skel,m)))) for m in dir(skel) if not m.startswith('_') and callable(getattr(skel,m))]; [print(f'{n}{s}') for n,s in methods]"`. \
This shows every callable + its exact signature. Use ONLY these.
  3. write_file `mcp_server.py` (workspace-relative). It must:
       - `from mcp.server.fastmcp import FastMCP` and create one `mcp = FastMCP("<robot_id>")`
       - Lazy-build the skeleton on first tool call (don't build at import time)
       - Register one `@mcp.tool()` per skeleton method from step 2 that an LLM \
planner would plausibly call (typical arm: home, move_cartesian, gripper_open, \
gripper_close, get_ee_pose, is_holding, get_joint_positions). Each tool \
forwards its args to the underlying method with the EXACT signature, has a \
clear docstring, and returns a JSON-serializable dict.
       - Be runnable as `python mcp_server.py` (stdio transport). End with \
`if __name__ == "__main__": mcp.run()` so it serves on stdio.
  4. local_exec to verify it imports cleanly: \
`python -c "import importlib; importlib.import_module('mcp_server'); print('import ok')"`. \
DO NOT run the server itself — it would block on stdio.
  5. Reply with one sentence listing the registered tool names.
"""


_DEMO_SYSTEM_DGX = """\
You are Phase 5 DEMO. Run an end-to-end demo on the DGX and capture a \
video / log.

Procedure:
  1. scp_to_dgx mcp_server.py if not already there.
  2. write_file a `demo.py` workspace-relative that runs a representative \
behavior end-to-end (arm: home → grasp closest graspable body → lift 20cm; \
quadruped: stand → walk forward 3s) and records frames via the skeleton's \
render() method into an mp4.
  3. scp_to_dgx demo.py.
  4. ssh_dgx_exec to run demo.py.
  5. scp_from_dgx the resulting mp4 back into the local workspace.
  6. Reply with one sentence: behavior + path to local mp4.
"""


_DEMO_SYSTEM_LOCAL = """\
You are Phase 5 DEMO. Run an end-to-end demo locally and capture an mp4. \
No scp, no ssh — everything runs in the workspace.

IMPORTANT: before writing demo.py, ALWAYS read the skeleton source first so \
you know the exact method signatures — read_file is allowed against the \
auto_adapter/skeletons/ directory (e.g. `read_file("arm_serial_dls.py")` or \
`read_file("quadruped_pd_gait.py")` resolve under the skeletons root). \
Do not guess signatures. `render()` returns (H, W, 3) uint8 RGB — call it \
WITHOUT camera args; the skeleton picks a default camera. Use imageio: \
`imageio.mimsave(path, frames, fps=30, codec="libx264")`.

Procedure:
  1. read_file validate_report.json AND the skeleton source for the methods \
you'll call.
  2. write_file `demo.py` (workspace-relative). The behavior depends on the \
robot class (from study.json):
       - ARM: home → grasp the first graspable body → lift 20 cm (REMEMBER \
to DESCEND to within 2 cm of the body before gripper_close — see VALIDATE)
       - QUADRUPED: stand_up → (optional) walk_forward → sit. CHECK \
validate_report.json: if `walk_forward` test was OK, include a short \
walk_forward(secs=2.0, speed=0.2) between stand and sit; if it FAILED \
(robot toppled, displacement negative or near zero), SKIP walk_forward \
and demo just stand_up → settle (~1s) → sit. The point is to show a \
WORKING demo — don't include behaviors that fall over. Render throughout.
       - Other: any sensible end-to-end behavior the skeleton exposes.
  3. The script must:
       - render() every few sim steps; target ~60-150 total frames (2-5 s of \
video at 30 fps)
       - save both `demo.mp4` (workspace root) AND `recordings/demo_<int(time.time())>.mp4`
       - print final JSON line `{"behavior":"...","frames":<int>,"mp4_path":"demo.mp4","ok":<bool>}`
  4. local_exec to run `python demo.py 2>&1`. timeout_sec=240.
  5. Reply with one sentence: behavior + frame count + path to demo.mp4.
"""


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


class SelfAssemble:
    """5-phase orchestrator. Use as a context manager so the CI session
    is always stopped, even on phase failure:

        with SelfAssemble(cfg) as sa:
            result = sa.run()
    """

    def __init__(self, cfg: SelfAssembleConfig) -> None:
        self.cfg = cfg
        self.robot_definition = find_robot_definition(cfg.robot_id, cfg.mjcf_path)
        catalog_class = self.robot_definition["class"] if self.robot_definition else None
        if catalog_class and cfg.expected_robot_class and catalog_class != cfg.expected_robot_class:
            raise ValueError("expected_robot_class conflicts with robot zoo")
        self.expected_robot_class = catalog_class or cfg.expected_robot_class
        self.workspace = (Path(cfg.workspace_root) / cfg.robot_id).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "traces").mkdir(parents=True, exist_ok=True)
        # Per-attempt sim videos go here. VALIDATE/DEMO prompts tell the
        # agent to save attempt mp4s with timestamped names so each iteration
        # (including failed attempts where the arm jittered or missed a
        # grasp) is preserved.
        (self.workspace / "recordings").mkdir(parents=True, exist_ok=True)

        # Stage MJCF into the workspace via SYMLINK (not copy) so MuJoCo's
        # relative mesh path resolution (e.g. <mesh file="../urdf/meshes/X.stl"/>)
        # still resolves against the MJCF's ORIGINAL directory. A copy would
        # break those refs and require staging the entire mesh tree alongside.
        src = Path(cfg.mjcf_path).resolve()
        dst = self.workspace / "mjcf.xml"
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src)
        self.mjcf_workspace_path = "mjcf.xml"

        # Filled in __enter__ to be lifecycle-safe
        self._ci_client: Any = None
        self._ci_session_id: Optional[str] = None
        self._exec_python_tool: Optional[ToolSpec] = None

    # ─── Lifecycle (CI session) ───────────────────────────────────────────

    def __enter__(self) -> "SelfAssemble":
        import boto3  # noqa: PLC0415

        self._ci_client = boto3.client("bedrock-agentcore", region_name=self.cfg.aws_region)
        sess = self._ci_client.start_code_interpreter_session(
            codeInterpreterIdentifier=self.cfg.ci_id,
            name=f"auto-adapter-{self.cfg.robot_id}",
            sessionTimeoutSeconds=self.cfg.ci_session_timeout_sec,
        )
        self._ci_session_id = sess["sessionId"]
        self._exec_python_tool = make_execute_python_tool(
            self._ci_client, self._ci_session_id, ci_id=self.cfg.ci_id
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

    # ─── Tool bundles per phase ───────────────────────────────────────────

    def _read_extra_roots(self) -> list[Path]:
        """Read-only roots the agent may inspect:
        - the auto_adapter.skeletons source dir (so agent can see method sigs)
        - the SOURCE dir of the MJCF (so MJCFs that <include other.xml> can
          actually be read by the agent for analysis; mujoco itself follows
          the symlink to the real location for mesh resolution, but read_file
          needs explicit permission to step outside the workspace).
        """
        from . import skeletons as sk_pkg  # noqa: PLC0415

        skel_root = Path(sk_pkg.__file__).parent.resolve()
        mjcf_src_dir = Path(self.cfg.mjcf_path).resolve().parent
        return [skel_root, mjcf_src_dir]

    def _local_tools(self) -> list[ToolSpec]:
        return [
            make_write_file_tool(self.workspace),
            make_read_file_tool(self.workspace, extra_roots=self._read_extra_roots()),
        ]

    def _skeleton_tools(self) -> list[ToolSpec]:
        return [make_list_skeletons_tool(), make_inspect_skeleton_tool()]

    def _dgx_tools(self) -> list[ToolSpec]:
        ssh_tool = make_ssh_dgx_exec_tool(
            self.cfg.dgx_host, remote_workspace=self.cfg.dgx_remote_workspace
        )
        scp_to, scp_from = make_scp_tools(
            self.cfg.dgx_host,
            local_workspace=self.workspace,
            remote_workspace=self.cfg.dgx_remote_workspace,
        )
        return [ssh_tool, scp_to, scp_from]

    def _local_runtime_tools(self) -> list[ToolSpec]:
        """Mac-local counterpart to _dgx_tools: just `local_exec`. The agent
        writes scripts via write_file (already operating on this workspace)
        and runs them in-place; no scp needed.
        """
        from . import skeletons as _sk  # noqa: PLC0415

        repo_root = Path(_sk.__file__).resolve().parents[2]  # vector-os-nano/
        return [
            make_local_exec_tool(
                self.workspace, python_path_prepend=[repo_root]
            )
        ]

    def _runtime_tools(self) -> list[ToolSpec]:
        return self._local_runtime_tools() if self.cfg.mode == "local" else self._dgx_tools()

    # ─── Phase runner ─────────────────────────────────────────────────────

    def _run_phase(
        self,
        *,
        name: str,
        system: str,
        user_msg: str,
        tools: list[ToolSpec],
        max_iters: int,
        expected_artifacts: list[str],
    ) -> PhaseResult:
        trace_path = self.workspace / "traces" / f"{name}.jsonl"

        loop = ReactLoop(
            tools=tools,
            system=system,
            model=self.cfg.bedrock_model,
            region=self.cfg.aws_region,
            max_iters=max_iters,
            max_tokens_per_turn=self.cfg.max_tokens_per_turn,
            trace_path=trace_path,
        )

        t0 = time.time()
        result: ReactResult = loop.run(user_msg)
        dur = time.time() - t0

        artifact_paths: list[Path] = []
        missing: list[str] = []
        for rel in expected_artifacts:
            p = self.workspace / rel
            if p.exists():
                artifact_paths.append(p)
            else:
                missing.append(rel)

        # Phase OK iff the agent produced everything we asked for. If no
        # artifacts were requested, fall back to the loop's own end_turn
        # signal. (Otherwise: an agent that wrote the artifact on its last
        # iter but hit max_iters before sending a closing message would be
        # marked FAIL even though the deliverable is there.)
        if expected_artifacts:
            ok = not missing
            error = (
                None
                if ok
                else (result.error or f"missing expected artifact(s): {missing}")
            )
        else:
            ok = result.ok
            error = result.error

        return PhaseResult(
            name=name,
            ok=ok,
            duration_sec=dur,
            trace_path=trace_path,
            artifact_paths=artifact_paths,
            final_text=result.final_text,
            error=error,
            token_usage=result.total_tokens,
        )

    # ─── Per-phase methods (thin: just bind tools + prompts) ──────────────

    def _phase_study(self) -> PhaseResult:
        assert self._exec_python_tool is not None
        tools = self._local_tools() + [self._exec_python_tool]
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"MJCF file (workspace-relative): {self.mjcf_workspace_path}\n"
            "Produce study.json per the procedure."
        )
        return self._run_phase(
            name="01_study",
            system=_STUDY_SYSTEM,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_study,
            expected_artifacts=["study.json"],
        )

    def _phase_generate(self, prior_validate_failures: Optional[str] = None) -> PhaseResult:
        assert self._exec_python_tool is not None
        # local_exec is needed in local mode so the agent can probe gripper
        # direction on the actual MuJoCo model (AgentCore CI may not have
        # mujoco installed). DGX mode still gets it via ssh_dgx_exec.
        tools = (
            self._local_tools()
            + self._skeleton_tools()
            + [self._exec_python_tool]
            + self._runtime_tools()
        )
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"study.json is in the workspace. MJCF is at {self.mjcf_workspace_path}.\n"
            "Produce driver.py per the procedure."
        )
        if prior_validate_failures:
            user_msg += (
                "\n\nIMPORTANT — your previous driver.py was generated but the "
                "outer VALIDATE harness reported the following structural "
                "failures. Re-generate driver.py addressing these failures:\n"
                f"{prior_validate_failures}\n"
                "Read validate_report.json for the full report; use exec_python to "
                "verify your fixes BEFORE returning."
            )
        return self._run_phase(
            name="02_generate",
            system=_GENERATE_SYSTEM,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_generate,
            expected_artifacts=["driver.py"],
        )

    def _summarise_validate_failures(self) -> str:
        """Read validate_report.json and produce a short bullet-list of which
        structural tests failed and their detail strings. Used to inject
        failure context into a retried GENERATE pass (outer GEN←VAL loop)."""
        report_path = self.workspace / "validate_report.json"
        if not report_path.exists():
            return "validate_report.json missing — assume driver build failed."
        try:
            report = json.loads(report_path.read_text())
        except Exception as e:
            return f"validate_report.json unreadable: {type(e).__name__}: {e}"
        failures = []
        for t in report.get("tests", []) or []:
            if not t.get("ok", False):
                failures.append(
                    f"  - test={t.get('test', '?')}: {t.get('detail', '(no detail)')}"
                )
        if not failures:
            err = report.get("error") or "no specific failures listed"
            return f"VALIDATE returned not-ok but no per-test failures listed: {err}"
        return "Failed structural tests:\n" + "\n".join(failures)

    def _phase_validate(self) -> PhaseResult:
        if self.cfg.validate_mode == "framework":
            return self._phase_validate_framework()
        tools = self._local_tools() + self._runtime_tools()
        if self.cfg.mode == "local":
            system = _VALIDATE_SYSTEM_LOCAL
            user_msg = (
                f"Robot ID: {self.cfg.robot_id}\n"
                "driver.py, mjcf.xml, study.json are in the workspace. "
                "Run locally per the procedure and write validate_report.json."
            )
        else:
            system = _VALIDATE_SYSTEM_DGX
            user_msg = (
                f"Robot ID: {self.cfg.robot_id}\n"
                f"driver.py is in the workspace; mjcf.xml is too. "
                f"DGX host: {self.cfg.dgx_host}, remote workspace: {self.cfg.dgx_remote_workspace}\n"
                "Validate per the procedure and write validate_report.json."
            )
        return self._run_phase(
            name="03_validate",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_validate,
            expected_artifacts=["validate_report.json"],
        )

    def _phase_validate_framework(self) -> PhaseResult:
        """Deterministic framework-level validation. No LLM, no test-script
        generation. Loads the agent-built driver, exercises each public method
        the skeleton class advertises, records frames per behavior, writes a
        structural validate_report.json.

        Replaces the agent-written validate.py that often burned max_iters
        debugging numpy / signature mistakes (benchmark choke).
        """
        import importlib.util  # noqa: PLC0415
        import sys as _sys  # noqa: PLC0415

        t0 = time.time()
        rec_dir = self.workspace / "recordings"
        rec_dir.mkdir(exist_ok=True)
        report_path = self.workspace / "validate_report.json"

        def _record_phase(name: str, ok: bool, detail: str, metric: float,
                          tests: list, mp4_rel: Optional[str] = None) -> None:
            entry = {"test": name, "ok": ok, "detail": detail, "metric": metric}
            if mp4_rel:
                entry["recording"] = mp4_rel
            tests.append(entry)

        tests: list[dict] = []
        load_error: Optional[str] = None

        # Side-load the driver
        driver_path = self.workspace / "driver.py"
        if not driver_path.exists():
            report_path.write_text(json.dumps(
                {"tests": [], "all_ok": False,
                 "error": "driver.py missing"}, indent=2))
            return PhaseResult(
                name="03_validate", ok=False,
                duration_sec=time.time() - t0,
                trace_path=None,
                artifact_paths=[report_path],
                final_text="",
                error="driver.py missing",
                token_usage={},
            )

        sys_path_added = False
        if str(self.workspace) not in _sys.path:
            _sys.path.insert(0, str(self.workspace))
            sys_path_added = True
        _sys.modules.pop("driver", None)
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.workspace)
            try:
                spec = importlib.util.spec_from_file_location("driver", str(driver_path))
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                skel = mod.build()
            except Exception as e:  # noqa: BLE001
                load_error = f"{type(e).__name__}: {e}"
                _record_phase("driver_build", False, load_error, 0.0, tests)
                report_path.write_text(json.dumps(
                    {"tests": tests, "all_ok": False, "error": load_error}, indent=2))
                return PhaseResult(
                    name="03_validate", ok=False,
                    duration_sec=time.time() - t0,
                    trace_path=None,
                    artifact_paths=[report_path],
                    final_text=f"driver.build() failed: {load_error}",
                    error=load_error,
                    token_usage={},
                )

            # driver_build smoke
            _record_phase("driver_build", True,
                          f"driver.build() returned {type(skel).__name__}",
                          1.0, tests)

            cls_name = type(skel).__name__
            try:
                expected_skeleton = SKELETON_FOR_CLASS.get(self.expected_robot_class)
                if self.expected_robot_class and cls_name != expected_skeleton:
                    _record_phase("expected_morphology", False,
                                  f"catalog expects {self.expected_robot_class} "
                                  f"({expected_skeleton}), got {cls_name}", 0.0, tests)
                elif cls_name == "ArmSerialDLSSkeleton":
                    self._validate_arm(skel, tests, rec_dir)
                elif cls_name == "QuadrupedPDGaitSkeleton":
                    self._validate_quadruped(skel, tests, rec_dir)
                elif cls_name in {"HandFingertipDLSSkeleton",
                                  "StretchMobileManipulationSkeleton",
                                  "BimanualSerialDLSSkeleton"}:
                    self._validate_new_morphology(skel, tests, rec_dir)
                else:
                    _record_phase("supported_skeleton", False,
                                  f"unknown skeleton class {cls_name}; "
                                  f"no framework behavior validator available",
                                  0.0, tests)
            except Exception as e:  # noqa: BLE001
                _record_phase("behavior_smoke", False,
                              f"{type(e).__name__}: {e}", 0.0, tests)
        finally:
            os.chdir(orig_cwd)
            if sys_path_added:
                _sys.path.remove(str(self.workspace))

        # A non-negative metric can still describe a failed movement. Keep
        # build status separate; every required behavior must pass validation.
        structural_ok = all(t["ok"] for t in tests
                            if t["test"] in {"driver_build", "expected_morphology", "supported_skeleton",
                                             "behavior_smoke"})
        all_metrics_ok = all(t["ok"] for t in tests)
        n_ok = sum(1 for t in tests if t["ok"])
        report_path.write_text(json.dumps({
            "tests": tests,
            "all_ok": all_metrics_ok,
            "structural_ok": structural_ok,
            "n_passed": n_ok,
            "n_total": len(tests),
        }, indent=2))

        dur = time.time() - t0
        return PhaseResult(
            name="03_validate", ok=all_metrics_ok,
            duration_sec=dur,
            trace_path=None,
            artifact_paths=[report_path],
            final_text=(
                f"framework validate: {n_ok}/{len(tests)} behavior thresholds met"
            ),
            error=None if all_metrics_ok else "required framework validation failed",
            token_usage={"in": 0, "out": 0},
        )

    # ─── Framework validators per skeleton class ──────────────────────────

    def _validate_new_morphology(self, skel, tests: list, rec_dir: Path) -> None:
        """Exercise the calibrated task on a generated driver using MuJoCo truth."""
        from types import SimpleNamespace
        import imageio.v2 as imageio
        from .agent.task_planner import _FrameCapture
        from autoadapter_bench.eval import (
            load_task_suite, _replay_tool_calls, _truth_state, evaluate_success,
        )

        if not self.robot_definition or not self.robot_definition.get("state_refs"):
            raise ValueError("new morphology validation requires trusted robot zoo bindings")
        task = load_task_suite(self.expected_robot_class)["suites"]["simple"]["tasks"][0]
        refs = self.robot_definition["state_refs"]
        skel.home()
        before = _truth_state(skel, refs)
        actions = task["reference_actions"]
        tag = f"framework_{task['id']}_{time.time_ns()}"
        trace_path = rec_dir / f"{tag}.json"
        video_path = rec_dir / f"{tag}.mp4"
        capture = _FrameCapture(skel, capture_every=max(1, round(1 / (30 * skel.model.opt.timestep))))
        samples: list = []
        try:
            clean = _replay_tool_calls(skel, actions, state_refs=refs, trace_samples=samples)
            before["_physics_samples"] = samples
            before["_replay_clean"] = clean
            ok, detail, metrics = evaluate_success(
                skel, before, task["success"],
                SimpleNamespace(tool_call_log=actions, ok=clean), task["id"],
            )
            capture.snapshot()
        finally:
            capture.uninstall()
            trace_path.write_text(json.dumps({"task": task["id"], "actions": actions,
                                             "samples": samples}, indent=2))
        if not capture.frames:
            raise RuntimeError("framework validation produced no video frames")
        imageio.mimsave(video_path, capture.frames, fps=30, codec="libx264")
        tests.append({"test": task["id"], "ok": bool(clean and ok),
                      "detail": detail, "metric": 1.0 if clean and ok else 0.0,
                      "metrics": metrics,
                      "recording": str(video_path.relative_to(self.workspace)),
                      "trace": str(trace_path.relative_to(self.workspace))})

    def _validate_arm(self, skel, tests: list, rec_dir: Path) -> None:
        """Run a fixed sequence on an ArmSerialDLSSkeleton + capture frames."""
        import imageio  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        # Hook step() to capture frames
        frames: list = []
        orig_step = skel.step

        def _capture_step(n: int = 1) -> None:
            for _ in range(int(n)):
                orig_step(1)
                if len(frames) < 600 and (len(frames) * 10) % 10 == 0:
                    try:
                        frames.append(skel.render())
                    except Exception:  # noqa: BLE001
                        pass

        # Wrap once; restore at end via try/finally
        skel.step = _capture_step  # type: ignore[method-assign]
        capture_every = 10  # frames every 10 sim steps
        try:
            # ── home_settle ───────────────────────────────────────────────
            t0 = time.time()
            try:
                skel.home()
                ee_pos, _ = skel.get_ee_pose()
                tests.append({
                    "test": "home_settle",
                    "ok": True,
                    "detail": f"home() converged; EE at {ee_pos.tolist()}",
                    "metric": float(time.time() - t0),
                })
            except Exception as e:  # noqa: BLE001
                tests.append({"test": "home_settle", "ok": False,
                              "detail": f"{type(e).__name__}: {e}",
                              "metric": -1.0})
                return  # subsequent tests assume home() worked

            # ── ik_roundtrip ──────────────────────────────────────────────
            try:
                ee0, _ = skel.get_ee_pose()
                target = ee0 + np.array([0.03, 0.0, 0.03])
                skel.move_cartesian(target, duration=1.5)
                ee1, _ = skel.get_ee_pose()
                err = float(np.linalg.norm(ee1 - target))
                tests.append({
                    "test": "ik_roundtrip",
                    "ok": bool(err < 0.02),
                    "detail": f"target {target.tolist()}, "
                              f"reached {ee1.tolist()}, err {err:.4f} m",
                    "metric": err,
                })
            except Exception as e:  # noqa: BLE001
                tests.append({"test": "ik_roundtrip", "ok": False,
                              "detail": f"{type(e).__name__}: {e}",
                              "metric": -1.0})

            # ── gripper_cycle ─────────────────────────────────────────────
            if skel.spec.gripper_actuator_names:
                try:
                    skel.gripper_open()
                    skel.gripper_close()
                    skel.gripper_open()
                    tests.append({
                        "test": "gripper_cycle",
                        "ok": True,
                        "detail": "open/close/open completed without exception",
                        "metric": 1.0,
                    })
                except Exception as e:  # noqa: BLE001
                    tests.append({"test": "gripper_cycle", "ok": False,
                                  "detail": f"{type(e).__name__}: {e}",
                                  "metric": -1.0})
            else:
                tests.append({"test": "gripper_cycle", "ok": True,
                              "detail": "no gripper actuator (skipped)",
                              "metric": 0.0})

            # ── grasp_lift (if there are weld-graspable bodies in the scene) ─
            graspables = skel.spec.weld_graspable_bodies or []
            if graspables:
                try:
                    body_name = graspables[0]
                    body_pos = skel.get_object_position(body_name)
                    pre_z = float(body_pos[2])
                    # APPROACH 5cm above, DESCEND to 2cm, close, LIFT 10cm
                    skel.move_cartesian(body_pos + np.array([0, 0, 0.05]), duration=1.5)
                    skel.move_cartesian(body_pos + np.array([0, 0, 0.02]), duration=1.0)
                    skel.gripper_close()
                    skel.move_cartesian(body_pos + np.array([0, 0, 0.12]), duration=1.5)
                    post = np.asarray(skel.get_object_position(body_name), dtype=float)
                    post_z = float(post[2])
                    lift = post_z - pre_z
                    # The object must also still be HELD at the gripper, not
                    # merely higher: a weld grasp activated without capturing the
                    # current relative pose flings the object away while it still
                    # "rose" in Z (height-only checks miss this).
                    try:
                        ee = np.asarray(skel.get_ee_pose()[0], dtype=float)
                        gap = float(np.linalg.norm(post - ee))
                    except Exception:  # noqa: BLE001
                        gap = 0.0
                    tests.append({
                        "test": "grasp_lift",
                        "ok": bool(lift > 0.05 and gap < 0.12),
                        "detail": f"{body_name}: lift={lift:.3f} m, held {gap*100:.1f}cm "
                                  f"from EE (need lift>0.05, gap<0.12)",
                        "metric": lift,
                    })
                except Exception as e:  # noqa: BLE001
                    tests.append({"test": "grasp_lift", "ok": False,
                                  "detail": f"{type(e).__name__}: {e}",
                                  "metric": -1.0})

        finally:
            skel.step = orig_step  # type: ignore[method-assign]
            if frames:
                try:
                    mp4 = rec_dir / f"validate_framework_{int(time.time())}.mp4"
                    imageio.mimsave(str(mp4), frames, fps=30, codec="libx264")
                    if tests:
                        tests[-1].setdefault("recording", str(mp4.relative_to(self.workspace)))
                except Exception:  # noqa: BLE001
                    pass

    def _validate_quadruped(self, skel, tests: list, rec_dir: Path) -> None:
        """Run stand / sit / walk smoke on a QuadrupedPDGaitSkeleton."""
        import imageio  # noqa: PLC0415

        frames: list = []
        orig_step = skel.step

        def _capture_step(n: int = 1) -> None:
            for _ in range(int(n)):
                orig_step(1)
                if len(frames) < 600:
                    try:
                        frames.append(skel.render())
                    except Exception:  # noqa: BLE001
                        pass

        skel.step = _capture_step  # type: ignore[method-assign]
        try:
            try:
                h0 = float(skel.get_body_height())
                ret = skel.stand_up(duration=2.0)
                h1 = float(skel.get_body_height())
                tests.append({
                    "test": "stand_up",
                    "ok": bool(h1 > 0.15),  # ≥ 15cm means it lifted off
                    "detail": f"body height {h0:.3f} → {h1:.3f}, returned {ret}",
                    "metric": h1,
                })
            except Exception as e:  # noqa: BLE001
                tests.append({"test": "stand_up", "ok": False,
                              "detail": f"{type(e).__name__}: {e}",
                              "metric": -1.0})

            try:
                ret = skel.sit(duration=1.5)
                h2 = float(skel.get_body_height())
                tests.append({
                    "test": "sit",
                    "ok": bool(h2 < skel.spec.body_height_target * 0.7),
                    "detail": f"body height after sit: {h2:.3f}, returned {ret}",
                    "metric": h2,
                })
            except Exception as e:  # noqa: BLE001
                tests.append({"test": "sit", "ok": False,
                              "detail": f"{type(e).__name__}: {e}",
                              "metric": -1.0})

            try:
                # walk is fragile — succeed if it runs without exception,
                # report the displacement either way
                skel.stand_up(duration=1.5)
                p0, _ = skel.get_base_pose()
                skel.walk_forward(secs=1.0, speed=0.15)
                p1, _ = skel.get_base_pose()
                dx = float(p1[0] - p0[0])
                tests.append({
                    "test": "walk_forward",
                    "ok": True,  # framework validator: pass if no crash
                    "detail": f"forward displacement {dx:+.3f} m "
                              f"(positive = forward; v1 trot is fragile)",
                    "metric": dx,
                })
            except Exception as e:  # noqa: BLE001
                tests.append({"test": "walk_forward", "ok": False,
                              "detail": f"{type(e).__name__}: {e}",
                              "metric": -1.0})
        finally:
            skel.step = orig_step  # type: ignore[method-assign]
            if frames:
                try:
                    mp4 = rec_dir / f"validate_framework_{int(time.time())}.mp4"
                    imageio.mimsave(str(mp4), frames, fps=30, codec="libx264")
                    if tests:
                        tests[-1].setdefault("recording", str(mp4.relative_to(self.workspace)))
                except Exception:  # noqa: BLE001
                    pass

    def _phase_export(self) -> PhaseResult:
        # EXPORT also needs local_exec so the agent can syntax-check the
        # generated mcp_server.py via a one-shot `python -c "import ..."`.
        tools = self._local_tools() + self._runtime_tools()
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "driver.py and validate_report.json are in the workspace. "
            "Write mcp_server.py per the procedure."
        )
        return self._run_phase(
            name="04_export",
            system=_EXPORT_SYSTEM,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_export,
            expected_artifacts=["mcp_server.py"],
        )

    def _phase_demo(self) -> PhaseResult:
        tools = self._local_tools() + self._runtime_tools()
        if self.cfg.mode == "local":
            system = _DEMO_SYSTEM_LOCAL
            user_msg = (
                f"Robot ID: {self.cfg.robot_id}\n"
                "mcp_server.py, driver.py, mjcf.xml, validate_report.json are "
                "in the workspace. Run the demo locally per the procedure."
            )
        else:
            system = _DEMO_SYSTEM_DGX
            user_msg = (
                f"Robot ID: {self.cfg.robot_id}\n"
                "mcp_server.py and driver.py and mjcf.xml are in the workspace. "
                "Run the demo per the procedure."
            )
        return self._run_phase(
            name="05_demo",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_demo,
            expected_artifacts=["demo.mp4"],
        )

    # ─── Top-level entrypoint ─────────────────────────────────────────────

    PHASES = ("study", "generate", "validate", "export", "demo")

    def run(self, *, stop_after: Optional[str] = None) -> SelfAssembleResult:
        """Run phases sequentially. `stop_after` ∈ PHASES (inclusive) lets you
        run just Phase 1, just Phase 1+2, etc. — useful when DGX is down.

        On the first failing phase, later phases are skipped (each is recorded
        as `ok=False, error="skipped — upstream <name> failed"`).
        """
        if stop_after is not None and stop_after not in self.PHASES:
            raise ValueError(f"stop_after={stop_after!r} not in {self.PHASES}")

        method_for = {
            "study":   self._phase_study,
            "export":  self._phase_export,
            "demo":    self._phase_demo,
        }

        results: list[PhaseResult] = []
        skipping = False
        skip_reason = ""
        for phase in self.PHASES:
            if skipping:
                results.append(
                    PhaseResult(name=phase, ok=False, duration_sec=0.0, error=skip_reason)
                )
            elif phase == "generate":
                # Outer GEN←VAL retry loop. We run GENERATE, then VALIDATE.
                # If VALIDATE structural tests fail, feed the failure detail
                # back into GENERATE's prompt and retry up to `max_outer`.
                max_outer = max(1, int(self.cfg.max_outer_gen_val_iters))
                gen_res: Optional[PhaseResult] = None
                val_res: Optional[PhaseResult] = None
                outer_iter = 0
                prior_failures: Optional[str] = None
                while outer_iter < max_outer:
                    gen_res = self._phase_generate(prior_validate_failures=prior_failures)
                    if not gen_res.ok:
                        # GENERATE itself failed — no point retrying VALIDATE
                        break
                    val_res = self._phase_validate()
                    if val_res.ok:
                        break
                    # Read structural failures from the validate_report.json
                    prior_failures = self._summarise_validate_failures()
                    outer_iter += 1
                # Record both phases with the outer-loop count noted on GENERATE
                if gen_res is not None:
                    gen_res.metadata = dict(gen_res.metadata or {})
                    gen_res.metadata["outer_gen_val_iters"] = outer_iter + 1
                    results.append(gen_res)
                if val_res is not None:
                    results.append(val_res)
                    if not val_res.ok:
                        skipping = True
                        skip_reason = (
                            f"skipped — outer GEN←VAL loop exhausted "
                            f"({outer_iter+1} attempts) and structural tests still fail: "
                            f"{val_res.error}"
                        )
                elif gen_res is not None and not gen_res.ok:
                    # GENERATE failed and we never got to VALIDATE; pad it
                    results.append(PhaseResult(
                        name="validate", ok=False, duration_sec=0.0,
                        error=f"skipped — upstream `generate` failed: {gen_res.error}"
                    ))
                    skipping = True
                    skip_reason = f"skipped — upstream `generate` failed: {gen_res.error}"
            elif phase == "validate":
                # Already handled inside the `generate` branch above (outer
                # GEN←VAL loop). Still honor stop_after="validate" below.
                pass
            else:
                res = method_for[phase]()
                results.append(res)
                if not res.ok:
                    skipping = True
                    skip_reason = f"skipped — upstream `{phase}` failed: {res.error}"
            if stop_after is not None and phase == stop_after:
                # Pad remaining phases with explicit "not run" markers
                for remaining in self.PHASES[self.PHASES.index(phase) + 1 :]:
                    results.append(
                        PhaseResult(
                            name=remaining,
                            ok=False,
                            duration_sec=0.0,
                            error=f"not run — stop_after={stop_after}",
                        )
                    )
                break

        all_ok = all(r.ok for r in results)
        out = SelfAssembleResult(
            robot_id=self.cfg.robot_id,
            workspace=self.workspace,
            phases=results,
            ok=all_ok,
        )

        # Persist the aggregate result alongside the per-phase traces
        summary_path = self.workspace / "summary.json"
        summary_path.write_text(json.dumps(out.to_json(), indent=2))

        # Human-readable narrative: stitches together what the agent did
        # per phase, including every sim recording it captured. This is
        # what a researcher reads to see the agent's exploration.
        self._write_narrative(out)

        return out

    def _write_narrative(self, result: "SelfAssembleResult") -> None:
        """Walk traces + artifacts + recordings → narrative.md timeline."""
        lines: list[str] = []
        lines.append(f"# SelfAssemble narrative — `{self.cfg.robot_id}`")
        lines.append("")
        lines.append(f"- workspace: `{self.workspace}`")
        lines.append(f"- mode: `{self.cfg.mode}`  model: `{self.cfg.bedrock_model}`")
        lines.append(f"- overall: **{'OK' if result.ok else 'FAIL'}**  "
                     f"(total: {sum(p.duration_sec for p in result.phases):.1f}s)")
        lines.append("")

        for p in result.phases:
            lines.append(f"## {p.name} — {'OK' if p.ok else 'FAIL'} ({p.duration_sec:.1f}s)")
            if p.token_usage:
                lines.append(f"  - tokens: {p.token_usage}")
            if p.error:
                lines.append(f"  - error: `{p.error}`")
            if p.trace_path and p.trace_path.exists():
                try:
                    n_steps = sum(1 for _ in p.trace_path.open())
                except OSError:
                    n_steps = -1
                lines.append(f"  - trace: `{p.trace_path.name}`  ({n_steps} LLM turns)")
            if p.artifact_paths:
                lines.append(f"  - artifacts: " + ", ".join(f"`{a.name}`" for a in p.artifact_paths))
            if p.final_text:
                snippet = p.final_text.replace("\n", " ").strip()
                if len(snippet) > 240:
                    snippet = snippet[:240] + "…"
                lines.append(f"  - agent's closing line: > {snippet}")
            lines.append("")

        # List every recording the agent captured, oldest-first
        rec_dir = self.workspace / "recordings"
        recs = sorted(rec_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if recs:
            lines.append("## Recordings (every sim attempt the agent ran)")
            lines.append("")
            for r in recs:
                size_kb = r.stat().st_size / 1024
                lines.append(f"- `{r.name}` ({size_kb:.1f} KB)")
            lines.append("")
            lines.append(f"_Open any with_: `open <workspace>/recordings/<name>`")
            lines.append("")

        (self.workspace / "narrative.md").write_text("\n".join(lines))
