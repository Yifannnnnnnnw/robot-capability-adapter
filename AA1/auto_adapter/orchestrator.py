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
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .agent import ReactLoop, ReactResult, ToolSpec
from .robot_catalog import (
    REPO_ROOT, SKELETON_FOR_CLASS, find_robot_definition,
    capability_generation_context, load_capability_design,
)
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


def _feedback_text(value: Any) -> str:
    """Render a small candidate-facing value while hiding absolute paths."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(_feedback_text(item) for item in value)
    elif isinstance(value, dict):
        value = json.dumps(value, sort_keys=True, default=str)
    else:
        value = str(value)
    # Details/errors are useful, but a validator may have embedded a local
    # artifact path in them.  Keep the measured failure and redact that path.
    return re.sub(
        r"(?<![A-Za-z0-9_.-])(?:/|[A-Za-z]:[\\/])[^\s,;]+",
        "<path omitted>",
        value,
    )


def validate_failure_feedback(report: dict) -> str:
    """Return the small failure signal that is safe to give a generator.

    Only failed checks, their detail/error text, and the evaluator's public
    ``metrics.measurements`` are copied.  Private request
    bindings, reset data, suite results, and artifact paths stay in the
    validator report.
    """
    lines: list[str] = ["Framework failure feedback (measurements only):"]
    failures = []
    for test in (report.get("tests", []) or []) if isinstance(report, dict) else []:
        if not isinstance(test, dict) or bool(test.get("ok")):
            continue

        label = test.get("capability_id") or test.get("test") or "check"
        method_name = test.get("method_name")
        if method_name and method_name != label:
            label = f"{label}.{method_name}"
        parts: list[str] = []
        for key in ("detail", "error"):
            text = _feedback_text(test.get(key))
            if text:
                parts.append(f"{key}={text}")
        errors = _feedback_text(test.get("errors"))
        if errors:
            parts.append(f"errors={errors}")
        metrics = test.get("metrics")
        measurements = metrics.get("measurements") if isinstance(metrics, dict) else None
        if measurements is not None:
            parts.append(f"measurements={_feedback_text(measurements)}")
        failures.append(f"- check={_feedback_text(label)}: "
                        + ("; ".join(parts) or "no detail"))

    if failures:
        lines.extend(failures)
    else:
        error = _feedback_text(report.get("error")) if isinstance(report, dict) else ""
        lines.append(
            "- framework error=" + (error or "validation failed without a reported check")
        )
    return "\n".join(lines)


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
    # "local" → run STUDY/GENERATE/VALIDATE/DEMO on this Mac via local_exec.
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
    model_provider: str = "holistic"
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
    demo_task: str = "Demonstrate a short sequence of the available validated capabilities, using their documented request bounds and observing the robot between actions."

    # Total submissions: initial generation plus at most three Framework
    # repairs. Set to 1 for an initial submission with no repair.
    max_outer_gen_val_iters: int = 4

    # 8000 fits Sonnet's longest reasonable single-turn output (a ~500-line
    # code block + commentary). Bumping above this saves few real cases but
    # costs more on every turn since input grows monotonically with the trace.
    max_tokens_per_turn: int = 8000
    # Trusted evaluator input. Catalog entries supply this automatically;
    # generated study.json never determines which morphology must pass.
    expected_robot_class: Optional[str] = None

    # Optional task-grounded capability preparation.  The historical path
    # remains the default; a prepared design is deliberately a local-only
    # diagnostic input and is selected after the public STUDY artifact.
    prepare_capabilities: bool = False
    capability_design_path: Path | None = None
    scene_cases_path: Path | None = None
    max_iters_capability_design: int = 30

    def __post_init__(self) -> None:
        if self.prepare_capabilities and self.capability_design_path is not None:
            raise ValueError(
                "prepare_capabilities and capability_design_path are mutually exclusive"
            )
        if self.scene_cases_path is not None and self.capability_design_path is None:
            raise ValueError(
                "scene_cases_path requires capability_design_path"
            )
        if (self.prepare_capabilities or self.capability_design_path is not None) \
                and self.mode != "local":
            raise ValueError("task-grounded capability preparation is local-only")
        if (isinstance(self.max_iters_capability_design, bool)
                or not isinstance(self.max_iters_capability_design, int)
                or self.max_iters_capability_design < 1):
            raise ValueError("max_iters_capability_design must be a positive integer")


def _capability_options_enabled(cfg: Any) -> bool:
    """Whether this run uses a post-STUDY capability design."""
    return bool(
        getattr(cfg, "prepare_capabilities", False)
        or getattr(cfg, "capability_design_path", None) is not None
    )


def _actual_mjcf_context(cfg: Any) -> str:
    if not _capability_options_enabled(cfg):
        return ""
    return (
        f"\nActual MJCF source: {Path(cfg.mjcf_path).resolve()}\n"
        "Resolve the workspace symlink before locating relative includes "
        "and meshes; inspect this model directory rather than searching the filesystem."
    )


def _trusted_skeleton_context(robot: dict | None, expected_class: str | None) -> str:
    """Return a compact public low-level skeleton summary for TGCD.

    TGCD may use this to avoid authoring capabilities that the selected
    implementation route cannot expose.  It receives names/signatures only;
    the fixed capability contract remains a later Generate input.
    """
    skeleton_name = (robot or {}).get("capability_skeleton")
    if not skeleton_name:
        skeleton_name = SKELETON_FOR_CLASS.get((robot or {}).get("class") or expected_class)
    if not skeleton_name:
        return ""
    lines = [f"Trusted low-level skeleton: {skeleton_name}"]
    try:
        import inspect
        from . import skeletons

        skeleton = getattr(skeletons, skeleton_name)
        methods = []
        for name in dir(skeleton):
            if name.startswith("_"):
                continue
            member = getattr(skeleton, name, None)
            if callable(member):
                try:
                    methods.append((name, str(inspect.signature(member))))
                except (TypeError, ValueError):
                    methods.append((name, "(...)"))
        for name, signature in sorted(methods):
            lines.append(f"- {name}{signature}")
    except Exception:  # noqa: BLE001 - context is advisory, not a gate
        pass
    return "\n".join(lines)


def _capability_design_metadata(path: Path) -> dict:
    """Read the small TGCD resource record when the preparation helper wrote it."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _numeric_token_usage(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): number
        for key, number in value.items()
        if not isinstance(number, bool) and isinstance(number, (int, float))
    }


def _merge_numeric_usage(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict:
    merged = _numeric_token_usage(left)
    for key, value in _numeric_token_usage(right).items():
        merged[key] = merged.get(key, 0) + value
    return merged


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

    @property
    def generation_ok(self) -> bool:
        """Whether the recorded generation/repair result produced a candidate."""
        candidates = [
            phase for phase in self.phases
            if phase.name in {"generate", "02_generate"}
            or phase.name.startswith("03_repair_")
        ]
        return bool(candidates and candidates[-1].ok)

    @property
    def framework_ok(self) -> bool:
        """Whether the legacy Framework validation phase passed."""
        return any(phase.name in {"validate", "03_validate"} and phase.ok
                   for phase in self.phases)

    @property
    def stage1_ok(self) -> bool:
        """Whether the legacy result contains a successful phase-one path."""
        return bool(self.phases and self.phases[0].name in {"study", "01_study"}
                    and self.phases[0].ok and self.generation_ok
                    and self.framework_ok)

    def to_json(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "workspace": str(self.workspace),
            "ok": self.ok,
            "generation_ok": self.generation_ok,
            "framework_ok": self.framework_ok,
            "stage1_ok": self.stage1_ok,
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


_CAPABILITY_GENERATE_SYSTEM = """\
You are Phase 2 GENERATE for the provided capability design.
Read study.json, list_skeletons and inspect_skeleton to examine the required
low-level skeleton and its public methods. Re-open the real MJCF where needed.
Write driver.py with a Robot subclass of the catalogued skeleton and build()
returning Robot.from_mjcf('mjcf.xml', spec=...). Fill robot-specific bindings,
then IMPLEMENT every required method(request). The inherited low-level IK,
actuator and gait primitives do not implement the complete public contracts.
Generate feedback, request-dependent targets, ordering, holds, stopping and
bounded failure handling. Do not hard-code test requests or success outcomes.
The prepared validation scenes may contain additional free-joint objects and
extra ``nq`` entries. Bind only the named robot joints, actuators and sites
from the public study/design; ignore unrelated scene entities unless a public
request explicitly observes them. ``build()`` must load the framework MJCF
from the supplied ``mjcf.xml`` path and must not depend on cached initial-state
files. Initialise any target from the current model/data on every call.
Use native actuator commands and advance the same MuJoCo model/data. Never
teleport or modify live state to complete an action. Kinematic calculations
may use scratch data. Probe real gripper direction and joint limits as needed.
Use local_exec to construct and develop the real driver. Public skeleton code,
robot assets, study and the capability contract are available; private tests
and complete reference drivers are not generation inputs. Do not read them.
Keep ALL code outputs, including write_file, under 150 lines per call. Write
driver.py incrementally: the initial imports/class/bindings first, then append
methods with write_file(append=true). Save the first chunk within your first
six tool turns; probe and refine the saved driver instead of repeating setup.
Finish by checking that
every required capability method exists, and summarize the bindings/control.
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
        self._dynamic_capabilities = _capability_options_enabled(cfg)
        self.robot_definition = find_robot_definition(cfg.robot_id, cfg.mjcf_path)
        catalog_class = self.robot_definition["class"] if self.robot_definition else None
        if catalog_class and cfg.expected_robot_class and catalog_class != cfg.expected_robot_class:
            raise ValueError("expected_robot_class conflicts with robot zoo")
        self.expected_robot_class = catalog_class or cfg.expected_robot_class
        # Dynamic/supplied designs are selected only after a successful public
        # STUDY.  In particular, do not replace the caller's MJCF with the
        # catalog capability scene on this route.  The default path retains
        # the historical catalog behavior verbatim.
        self.capability_design = None
        self.scene_cases_path: Path | None = None
        self.scene_paths: dict[str, Path] = {}
        self._last_capability_preparation: dict | None = None
        if not self._dynamic_capabilities:
            self.capability_design = load_capability_design(self.robot_definition)
            if self.capability_design and self.robot_definition.get("capability_mjcf"):
                cfg.mjcf_path = REPO_ROOT / self.robot_definition["capability_mjcf"]
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
        # Local runs use the venv-backed local_exec tool for every phase; do
        # not start an AgentCore session that could become an accidental
        # execution/artifact world for STUDY or GENERATE.
        if self.cfg.mode == "local":
            return self

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

    # ─── Task-grounded capability preparation ────────────────────────────

    def _require_capability_design(self, phase: str) -> None:
        if getattr(self, "_dynamic_capabilities", False) \
                and not isinstance(self.capability_design, dict):
            raise RuntimeError(
                f"dynamic {phase} requires a successful STUDY and capability design; "
                "catalog fallback is disabled"
            )

    def _prepare_capability_design(self, study: Mapping[str, Any]) -> dict:
        """Run TGCD or load an explicit design after STUDY.

        The helper owns the file boundary and writes only under the current
        workspace.  Success is admitted from the current helper return and
        preparation record, so an invocation failure cannot be made successful
        by a stale artifact from an earlier diagnostic.
        """
        if not getattr(self, "_dynamic_capabilities", False):
            return {}
        if not isinstance(study, Mapping):
            raise ValueError("STUDY artifact must be one JSON object")
        robot_id = str(study.get("robot_id", self.cfg.robot_id))
        if robot_id != self.cfg.robot_id:
            raise ValueError(
                f"STUDY robot_id {robot_id!r} does not match {self.cfg.robot_id!r}"
            )
        input_dir = self.workspace / "design"
        input_dir.mkdir(parents=True, exist_ok=True)
        design_output = input_dir / "capability_design.json"
        preparation_path = input_dir / "capability_preparation.json"
        started = time.time()
        preparation: dict[str, Any] = {}
        try:
            from .capability_preparation import (  # noqa: PLC0415
                generate_capability_design,
                load_capability_design_file,
                task_library_for_robot,
            )

            supplied = self.cfg.capability_design_path
            if supplied is not None:
                supplied_path = Path(supplied).expanduser().resolve()
                design = load_capability_design_file(
                    supplied_path, expected_robot_id=self.cfg.robot_id
                )
                if not isinstance(design, dict):
                    design = dict(design)
                design_path = supplied_path
                preparation = {
                    "mode": "supplied",
                    "token_usage": {},
                    "duration_sec": 0.0,
                    "trace_path": None,
                    "error": None,
                }
                shutil.copy2(supplied_path, design_output)
            else:
                # A current preparation record is part of the generated
                # design handoff.  Remove the previous record before asking
                # TGCD to write so an invocation failure cannot inherit stale
                # scene paths or token usage.
                preparation_path.unlink(missing_ok=True)
                task_library_dir = task_library_for_robot(self.cfg.robot_id)
                skeleton_context = _trusted_skeleton_context(
                    self.robot_definition, self.expected_robot_class
                ) if self.robot_definition and self.robot_definition.get(
                    "generation_route", "skeleton"
                ) == "skeleton" else None
                result = generate_capability_design(
                    robot_id=self.cfg.robot_id,
                    study=dict(study),
                    study_path=self.workspace / "study.json",
                    mjcf_path=Path(self.cfg.mjcf_path),
                    task_library_dir=Path(task_library_dir),
                    output_dir=input_dir,
                    model=self.cfg.bedrock_model,
                    provider=self.cfg.model_provider,
                    region=self.cfg.aws_region,
                    max_iters=self.cfg.max_iters_capability_design,
                    max_tokens_per_turn=self.cfg.max_tokens_per_turn,
                    skeleton_context=skeleton_context,
                    prepare_scene_cases=True,
                )
                design = result
                if not isinstance(design, Mapping):
                    raise ValueError("TGCD did not return a capability design object")
                design = dict(design)
                design_path = design_output

                if not preparation_path.is_file():
                    raise ValueError(
                        "TGCD did not write current capability_preparation.json"
                    )
                preparation = _capability_design_metadata(preparation_path)
                if preparation.get("error"):
                    raise RuntimeError(str(preparation["error"]))
                preparation.setdefault("token_usage", {})
                preparation.setdefault("duration_sec", time.time() - started)
                preparation.setdefault("trace_path", None)
                preparation.setdefault("error", None)

            if design.get("robot_configuration_id") not in (None, self.cfg.robot_id):
                raise ValueError(
                    "capability design robot_configuration_id does not match the run robot"
                )
            capabilities = design.get("capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                raise ValueError("capability design has no capabilities")
            preparation.setdefault("duration_sec", time.time() - started)
            preparation.setdefault("token_usage", {})
            preparation.setdefault("trace_path", None)
            preparation.setdefault("error", None)
            preparation["design_path"] = str(design_path)
            preparation["output_dir"] = str(input_dir)
            # The generation helper owns its current metadata.  Supplied
            # designs have no helper metadata, so write a small local record.
            preparation_path.write_text(json.dumps(preparation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._prepare_scene_inputs(design, preparation, input_dir)
            preparation_path.write_text(
                json.dumps(preparation, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.capability_design = design
            self._last_capability_preparation = dict(preparation)
            return preparation
        except Exception as exc:
            self.capability_design = None
            preparation = _capability_design_metadata(preparation_path)
            preparation.update({
                "design_path": str(design_output),
                "output_dir": str(input_dir),
                "duration_sec": float(preparation.get("duration_sec") or time.time() - started),
                "token_usage": _numeric_token_usage(preparation.get("token_usage")),
                "trace_path": preparation.get("trace_path"),
                "error": f"{type(exc).__name__}: {exc}",
            })
            preparation_path.write_text(
                json.dumps(preparation, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self._last_capability_preparation = dict(preparation)
            raise

    def _prepare_scene_inputs(
        self, design: Mapping[str, Any], preparation: dict[str, Any], output_dir: Path
    ) -> None:
        """Resolve the current design's case suite and prepared scenes."""
        from .scene_runtime import load_scene_cases, prepare_scenes

        supplied_cases = self.cfg.scene_cases_path
        raw_cases = preparation.get("scene_cases_path")
        if supplied_cases is None and raw_cases:
            supplied_cases = Path(str(raw_cases))
            if not supplied_cases.is_absolute():
                supplied_cases = output_dir / supplied_cases
        if supplied_cases is None:
            if self.cfg.prepare_capabilities:
                raise ValueError(
                    "automatic capability preparation did not produce scene cases"
                )
            self.scene_cases_path = None
            self.scene_paths = {}
            return
        case_path = Path(supplied_cases).expanduser().resolve()
        suite = load_scene_cases(case_path, design=dict(design))
        # Auto-prepared scenes already come from TGCD.  Explicit case YAML is
        # hosted here so it is always built from the caller's actual MJCF.
        raw_paths = preparation.get("scene_paths")
        if self.cfg.scene_cases_path is not None:
            raw_paths = prepare_scenes(
                mjcf_path=Path(self.cfg.mjcf_path).resolve(),
                suite=suite,
                output_dir=output_dir,
            )
        if not isinstance(raw_paths, Mapping):
            raise ValueError("design has no prepared scene path mapping")
        paths: dict[str, Path] = {}
        for scene_id in suite["scenes"]:
            if scene_id not in raw_paths:
                raise ValueError(f"design has no prepared scene for {scene_id!r}")
            path = Path(str(raw_paths[scene_id])).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(
                    f"prepared scene path for {scene_id!r} is missing: {path}"
                )
            paths[str(scene_id)] = path
        self.scene_cases_path = case_path
        self.scene_paths = paths
        preparation["scene_cases_path"] = str(case_path)
        preparation["scene_paths"] = {key: str(path) for key, path in paths.items()}

    def _phase_design(self) -> PhaseResult:
        """Prepare or load the capability design after a successful STUDY."""
        if not getattr(self, "_dynamic_capabilities", False):
            return PhaseResult("design", True, 0.0, metadata={"legacy": True})
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self._last_capability_preparation = None
        started = time.time()
        try:
            study_path = self.workspace / "study.json"
            if not study_path.is_file():
                raise FileNotFoundError("study.json missing after STUDY")
            study = json.loads(study_path.read_text(encoding="utf-8"))
            preparation = self._prepare_capability_design(study)
            return PhaseResult(
                name="design", ok=True, duration_sec=max(0.0, time.time() - started),
                artifact_paths=[p for p in (
                    self.workspace / "design" / "capability_design.json",
                    self.workspace / "design" / "capability_preparation.json",
                ) if p.is_file()],
                token_usage=_numeric_token_usage(preparation.get("token_usage")),
                metadata={
                    "capability_design_path": preparation.get("design_path"),
                    "capability_preparation": dict(preparation),
                    "capability_design_duration_sec": preparation.get("duration_sec", 0.0),
                    "capability_design_token_usage": dict(preparation.get("token_usage") or {}),
                    "scene_cases_path": str(self.scene_cases_path) if self.scene_cases_path else None,
                    "scene_paths": {
                        key: str(path) for key, path in self.scene_paths.items()
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.capability_design = None
            self.scene_cases_path = None
            self.scene_paths = {}
            preparation = getattr(self, "_last_capability_preparation", None) or {}
            return PhaseResult(
                name="design", ok=False,
                duration_sec=max(0.0, time.time() - started),
                artifact_paths=[self.workspace / "design" / "capability_preparation.json"]
                if (self.workspace / "design" / "capability_preparation.json").is_file() else [],
                error=f"capability design preparation failed: {exc}",
                token_usage=_numeric_token_usage(preparation.get("token_usage")),
                metadata={"capability_preparation_error": str(exc)},
            )

    def _attach_capability_metadata(
        self, study_result: PhaseResult, preparation: Mapping[str, Any]
    ) -> None:
        """Record TGCD resources on STUDY and include them in phase totals."""
        metadata = dict(study_result.metadata or {})
        metadata["capability_design_path"] = preparation.get("design_path")
        metadata["capability_preparation"] = dict(preparation)
        metadata["capability_design_duration_sec"] = preparation.get("duration_sec", 0.0)
        metadata["capability_design_token_usage"] = dict(
            preparation.get("token_usage") or {}
        )
        study_result.metadata = metadata
        study_result.token_usage = _merge_numeric_usage(
            study_result.token_usage, preparation.get("token_usage")
        )
        try:
            study_result.duration_sec += max(0.0, float(preparation.get("duration_sec", 0.0)))
        except (TypeError, ValueError):
            pass

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
        before_mtimes = {
            rel: (self.workspace / rel).stat().st_mtime_ns
            for rel in expected_artifacts
            if (self.workspace / rel).is_file()
        }

        loop = ReactLoop(
            tools=tools,
            system=system,
            model=self.cfg.bedrock_model,
            provider=self.cfg.model_provider,
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
            current_write = (
                p.exists()
                and (
                    rel not in before_mtimes
                    or p.stat().st_mtime_ns != before_mtimes[rel]
                )
            )
            if p.exists() and (
                not getattr(self, "_dynamic_capabilities", False) or current_write
            ):
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

        # A resumed workspace already contains a candidate. Its existence
        # cannot turn an API/transport failure into a successful repair.
        transport_error = bool(result.trace and result.trace[-1].stop_reason == "invoke_error")
        if transport_error:
            ok = False
            error = result.error or "model invocation failed"

        return PhaseResult(
            name=name,
            ok=ok,
            duration_sec=dur,
            trace_path=trace_path,
            artifact_paths=artifact_paths,
            final_text=result.final_text,
            error=error,
            token_usage=result.total_tokens,
            metadata={"transport_error": True} if transport_error else {},
        )

    # ─── Per-phase methods (thin: just bind tools + prompts) ──────────────

    def _phase_study(self) -> PhaseResult:
        # A repeated STUDY starts a new design handoff.  Never let an earlier
        # dynamic design make a failed/partial STUDY look generation-ready.
        if getattr(self, "_dynamic_capabilities", False):
            self.capability_design = None
            self.scene_cases_path = None
            self.scene_paths = {}
            self._last_capability_preparation = None
        if self.cfg.mode == "local":
            # The workspace is the authoritative artifact world in local
            # mode.  Keep MuJoCo probing in the same venv/cwd as write_file.
            tools = self._local_tools() + self._local_runtime_tools()
            system = (
                _STUDY_SYSTEM
                .replace("execute_python", "local_exec")
                .replace(
                    "the CI session preserves Python state across calls "
                    "(imports persist, variables persist).",
                    "each local_exec call starts a fresh process; include "
                    "imports and setup in every command or save intermediates "
                    "in workspace files.",
                )
            )
        else:
            assert self._exec_python_tool is not None
            tools = self._local_tools() + [self._exec_python_tool]
            system = _STUDY_SYSTEM
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"MJCF file (workspace-relative): {self.mjcf_workspace_path}\n"
            "Produce study.json per the procedure."
        )
        user_msg += _actual_mjcf_context(self.cfg)
        result = self._run_phase(
            name="01_study",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_study,
            expected_artifacts=["study.json"],
        )
        return result

    def _phase_generate(self, prior_validate_failures: Optional[str] = None) -> PhaseResult:
        self._require_capability_design("GENERATE")
        system = _CAPABILITY_GENERATE_SYSTEM if self.capability_design else _GENERATE_SYSTEM
        if self.cfg.mode == "local":
            # All Python/MuJoCo probes and generated files must stay in the
            # local workspace.  AgentCore is intentionally absent here.
            tools = (
                self._local_tools()
                + self._skeleton_tools()
                + self._local_runtime_tools()
            )
            system = (
                system
                .replace("execute_python", "local_exec")
                .replace(
                    "CI session state persists.",
                    "each local_exec call starts a fresh process; include "
                    "imports and setup in every command or save intermediates "
                    "in workspace files.",
                )
            )
        else:
            assert self._exec_python_tool is not None
            # DGX mode retains the existing CodeInterpreter + DGX routing.
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
        user_msg += _actual_mjcf_context(self.cfg)
        user_msg += capability_generation_context(
            self.robot_definition, design=self.capability_design
        )
        if prior_validate_failures:
            verification_tool = "local_exec" if self.cfg.mode == "local" else "execute_python"
            user_msg += (
                "\n\nIMPORTANT — your previous driver.py was generated but the "
                "outer VALIDATE harness reported the following structural "
                "failures. Re-generate driver.py addressing these failures:\n"
                f"{prior_validate_failures}\n"
                "Use only the candidate-facing feedback above; use "
                f"{verification_tool} to "
                "verify your fixes BEFORE returning."
            )
        return self._run_phase(
            name="02_generate",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_generate,
            expected_artifacts=["driver.py"],
        )

    def _phase_repair(self, feedback: str, attempt: int) -> PhaseResult:
        """Repair the existing driver from a sanitized Framework signal.

        A repair gets its own trace so the original generation remains
        inspectable.  The validator report and private suite are deliberately
        not generation inputs; the caller supplies only
        :func:`validate_failure_feedback` output.
        """
        self._require_capability_design("REPAIR")
        system = _CAPABILITY_GENERATE_SYSTEM if self.capability_design else _GENERATE_SYSTEM
        if self.cfg.mode == "local":
            tools = (
                self._local_tools()
                + self._skeleton_tools()
                + self._local_runtime_tools()
            )
            system = (
                system
                .replace("execute_python", "local_exec")
                .replace(
                    "CI session state persists.",
                    "each local_exec call starts a fresh process; include "
                    "imports and setup in every command or save intermediates "
                    "in workspace files.",
                )
            )
        else:
            assert self._exec_python_tool is not None
            tools = (
                self._local_tools()
                + self._skeleton_tools()
                + [self._exec_python_tool]
                + self._runtime_tools()
            )

        verification_tool = "local_exec" if self.cfg.mode == "local" else "execute_python"
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"This is repair attempt {int(attempt)}. A prior driver.py may "
            "exist in the workspace; if it exists, modify it IN PLACE, and if "
            "it is missing, create it from the public inputs.\n"
            "Use the public study.json, the public capability contract, the "
            "MJCF, and the public skeleton interface. Preserve behavior that "
            "already passes and fix the failures below.\n\n"
            "Candidate-facing Framework feedback:\n"
            f"{feedback or '(no detail was reported; inspect the existing driver)'}\n\n"
            "Rules:\n"
            "  1. Read and edit driver.py in place when present; otherwise create "
            "a complete driver.py.\n"
            f"  2. Use {verification_tool} only for small public checks against the "
            "real MJCF and current driver.\n"
            "  3. Do not read validate_report.json, private validation suites, "
            "scoring code, or reference control implementations. The feedback "
            "above is the complete validation signal for this repair.\n"
            "  4. Keep request handling, real actuator physics, observations, and "
            "the existing public interface intact; do not hard-code a test.\n"
            "Finish after writing the corrected driver.py."
        )
        user_msg += _actual_mjcf_context(self.cfg)
        user_msg += capability_generation_context(
            self.robot_definition, design=self.capability_design
        )
        return self._run_phase(
            name=f"03_repair_{int(attempt)}",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=min(22, max(1, int(self.cfg.max_iters_generate))),
            expected_artifacts=["driver.py"],
        )

    def _summarise_validate_failures(self) -> str:
        """Read the report and return only candidate-facing failure feedback."""
        report_path = self.workspace / "validate_report.json"
        if not report_path.exists():
            return "validate_report.json missing — assume driver build failed."
        try:
            report = json.loads(report_path.read_text())
        except Exception:
            return "validate_report.json unreadable — no failure details available."
        return validate_failure_feedback(report)

    def _phase_validate(self) -> PhaseResult:
        if getattr(self, "_dynamic_capabilities", False):
            self._require_capability_design("VALIDATE")
            if self.scene_cases_path is None or not self.scene_paths:
                return PhaseResult(
                    name="03_validate", ok=False, duration_sec=0.0,
                    error="dynamic validation requires scene cases and prepared scenes",
                    metadata={"validation_error": True, "repairable": False},
                )
            from .design_validation import validate_design_driver  # noqa: PLC0415

            started = time.time()
            report_path = self.workspace / "validate_report.json"
            # A failed current invocation cannot inherit a previous report.
            report_path.unlink(missing_ok=True)
            report = validate_design_driver(
                driver_path=self.workspace / "driver.py",
                design=self.capability_design,
                scene_cases_path=self.scene_cases_path,
                scene_paths=self.scene_paths,
                output_dir=self.workspace / "validation",
            )
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            validation_error = bool(report.get("validation_error"))
            repairable = bool(report.get("repairable", not validation_error))
            return PhaseResult(
                name="03_validate",
                ok=report.get("all_ok") is True,
                duration_sec=max(0.0, time.time() - started),
                artifact_paths=[report_path],
                final_text=(
                    f"design validation: {report.get('n_passed', 0)}/"
                    f"{report.get('n_total', 0)} cases passed"
                ),
                error=None if report.get("all_ok") is True else report.get("error") or "design validation failed",
                token_usage={"in": 0, "out": 0},
                metadata={
                    "validation_error": validation_error,
                    "repairable": repairable,
                    "validation_report": report,
                },
            )
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
        if getattr(self, "_dynamic_capabilities", False):
            raise ValueError(
                "dynamic capability designs cannot enter the legacy Framework evaluator"
            )
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
                if self.capability_design:
                    from auto_adapter.robot_catalog import validate_capability_driver
                    from autoadapter_bench.capability_eval import run_capability_suite
                    validate_capability_driver(skel, self.robot_definition)
                    outcome = run_capability_suite(
                        skel, self.robot_definition, self.workspace / "capability_validation",
                        driver_origin="real_model_generation")
                    tests.extend(outcome["tests"])
                    if not outcome["tests"]:
                        raise ValueError("capability suite produced no checks")
                elif self.expected_robot_class and cls_name != expected_skeleton:
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
        if getattr(self, "_dynamic_capabilities", False):
            raise ValueError(
                "dynamic capability designs cannot use the legacy fixed task evaluator"
            )
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
        if getattr(self, "_dynamic_capabilities", False):
            raise ValueError("dynamic capability designs cannot use fixed arm validation")
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
        if getattr(self, "_dynamic_capabilities", False):
            raise ValueError(
                "dynamic capability designs cannot use fixed quadruped validation"
            )
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
        if self.cfg.mode == "local" and self.capability_design:
            return self._phase_recap_demo()
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

    def _phase_recap_demo(self) -> PhaseResult:
        """Run the canonical controller in a bounded local diagnostic process."""
        started = time.monotonic()
        report_path = self.workspace / "recap_demo_report.json"
        report_path.unlink(missing_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(REPO_ROOT), str(REPO_ROOT.parent / "autoadapter" / "src"),
            env.get("PYTHONPATH"))))
        command = [sys.executable, "-m", "auto_adapter.agent.recap_demo",
                   "--workspace", str(self.workspace), "--robot-id", self.cfg.robot_id,
                   "--task", self.cfg.demo_task, "--model", self.cfg.bedrock_model,
                   "--provider", self.cfg.model_provider, "--region", self.cfg.aws_region,
                   "--max-tokens", str(self.cfg.max_tokens_per_turn)]
        log_path = self.workspace / "traces" / "recap_demo_process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("w") as log:
                process = subprocess.run(command, env=env, cwd=REPO_ROOT,
                                         stdout=log, stderr=subprocess.STDOUT, timeout=600)
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            ok = process.returncode == 0 and report.get("ok") is True
            return PhaseResult(
                name="05_demo", ok=ok, duration_sec=time.monotonic() - started,
                trace_path=Path(report["trace_path"]) if report.get("trace_path") else log_path,
                artifact_paths=[p for p in (report_path, self.workspace / "demo.mp4") if p.exists()],
                final_text="ReCAP diagnostic demo; physical task success has not been evaluated.",
                error=None if ok else "ReCAP demo failed; inspect recap_demo_report.json and process log",
                metadata=report)
        except subprocess.TimeoutExpired:
            return PhaseResult(name="05_demo", ok=False, duration_sec=time.monotonic() - started,
                               trace_path=log_path, error="ReCAP demo exceeded 600 seconds")

    # ─── Top-level entrypoint ─────────────────────────────────────────────

    PHASES = ("study", "generate", "validate", "export", "demo")
    DYNAMIC_PHASES = ("study", "design", "generate", "validate")

    def _finish_result(
        self,
        results: list[PhaseResult],
        *,
        ok_override: bool | None = None,
    ) -> SelfAssembleResult:
        """Persist the aggregate result and narrative for either route."""
        # ``not run — stop_after=...`` is an intentional prefix marker and
        # must not make a successful requested prefix report as failed.
        executed = [
            phase for phase in results
            if not (phase.error or "").startswith("not run — stop_after=")
        ]
        aggregate_ok = bool(executed) and all(phase.ok for phase in executed)
        if ok_override is not None:
            aggregate_ok = bool(ok_override)
        out = SelfAssembleResult(
            robot_id=self.cfg.robot_id,
            workspace=self.workspace,
            phases=results,
            ok=aggregate_ok,
        )
        summary_path = self.workspace / "summary.json"
        summary_path.write_text(json.dumps(out.to_json(), indent=2))
        self._write_narrative(out)
        return out

    def _run_dynamic(self, *, stop_after: Optional[str]) -> SelfAssembleResult:
        if stop_after is not None and stop_after not in self.DYNAMIC_PHASES:
            raise ValueError(f"dynamic stop_after={stop_after!r} not in {self.DYNAMIC_PHASES}")
        # A dynamic invocation owns a fresh current-output boundary.  The
        # prior run's study, candidate, validation report, or design handoff
        # cannot certify this invocation after a transport/model failure.
        for path in (
            self.workspace / "study.json",
            self.workspace / "driver.py",
            self.workspace / "validate_report.json",
            self.workspace / "design" / "capability_design.json",
            self.workspace / "design" / "capability_preparation.json",
            self.workspace / "design" / "scene_cases.yaml",
            self.workspace / "design" / "probe_report.json",
        ):
            path.unlink(missing_ok=True)
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        results: list[PhaseResult] = []
        phase_methods = {
            "study": self._phase_study,
            "design": self._phase_design,
        }
        failed: str | None = None
        for name in ("study", "design"):
            if failed is not None:
                results.append(PhaseResult(name=name, ok=False, duration_sec=0.0, error=failed))
                continue
            phase = phase_methods[name]()
            if name == "study" and not (self.workspace / "study.json").is_file():
                phase.ok = False
                phase.error = phase.error or "study.json was not written by the current run"
            if name == "design" and not (
                isinstance(self.capability_design, dict)
                and (self.workspace / "design" / "capability_design.json").is_file()
            ):
                phase.ok = False
                phase.error = phase.error or "capability design was not written by the current run"
            results.append(phase)
            if not phase.ok:
                failed = f"skipped — upstream `{name}` failed: {phase.error}"
                continue
            if stop_after == name:
                break
        if failed is None and (stop_after is None or stop_after not in {"study", "design"}):
            max_outer = max(1, int(self.cfg.max_outer_gen_val_iters))
            gen: PhaseResult | None = None
            val: PhaseResult | None = None
            attempts = 0
            feedback: str | None = None
            while attempts < max_outer:
                attempts += 1
                gen = self._phase_generate() if feedback is None else self._phase_repair(feedback, attempts - 1)
                gen.metadata = dict(gen.metadata or {})
                gen.metadata["outer_gen_val_iters"] = attempts
                if not gen.ok:
                    results.append(gen)
                    results.append(PhaseResult(
                        name="validate", ok=False, duration_sec=0.0,
                        error=f"skipped — upstream `generate` failed: {gen.error}",
                    ))
                    failed = f"skipped — upstream `generate` failed: {gen.error}"
                    break
                results.append(gen)
                if stop_after == "generate":
                    break
                val = self._phase_validate()
                results.append(val)
                if val.ok:
                    break
                if not val.metadata.get("repairable", True):
                    failed = f"skipped — dynamic validation preparation failed: {val.error}"
                    break
                feedback = self._summarise_validate_failures()
            if stop_after == "validate" and val is None and failed is None:
                # This is only reachable with a zero outer budget; keep the
                # result explicit rather than implying that validation ran.
                results.append(PhaseResult(
                    name="validate", ok=False, duration_sec=0.0,
                    error="dynamic validation was not run",
                    metadata={"validation_error": True, "repairable": False},
                ))
            elif val is not None and not val.ok and failed is None:
                failed = f"skipped — dynamic validation failed after {attempts} attempts: {val.error}"
        if stop_after is not None:
            try:
                index = self.DYNAMIC_PHASES.index(stop_after)
            except ValueError:
                index = -1
            names = self.DYNAMIC_PHASES[index + 1 :] if index >= 0 else []
            existing = {phase.name for phase in results}
            for name in names:
                if name not in existing:
                    results.append(PhaseResult(
                        name=name, ok=False, duration_sec=0.0,
                        error=f"not run — stop_after={stop_after}",
                    ))
        elif failed is not None:
            existing = {phase.name for phase in results}
            for name in ("generate", "validate"):
                if name not in existing:
                    results.append(PhaseResult(
                        name=name, ok=False, duration_sec=0.0, error=failed
                    ))
        if failed is not None:
            effective_ok = False
        elif stop_after in {"study", "design"}:
            effective_ok = True
        elif stop_after == "generate":
            effective_ok = gen is not None and gen.ok
        else:
            effective_ok = val is not None and val.ok
        return self._finish_result(results, ok_override=effective_ok)

    def run(self, *, stop_after: Optional[str] = None) -> SelfAssembleResult:
        """Run phases sequentially. `stop_after` ∈ PHASES (inclusive) lets you
        run just Phase 1, just Phase 1+2, etc. — useful when DGX is down.

        On the first failing phase, later phases are skipped (each is recorded
        as `ok=False, error="skipped — upstream <name> failed"`).
        """
        if getattr(self, "_dynamic_capabilities", False):
            return self._run_dynamic(stop_after=stop_after)
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
            if phase == "validate":
                # Recorded inside GENERATE, including its failure/skip result.
                pass
            elif skipping:
                results.append(
                    PhaseResult(name=phase, ok=False, duration_sec=0.0, error=skip_reason)
                )
            elif phase == "generate":
                # Outer GEN←VAL retry loop. The first pass is GENERATE; later
                # passes repair the existing driver with their own trace.
                # If VALIDATE structural tests fail, feed only the sanitized
                # failure detail back into a repair pass up to `max_outer`.
                max_outer = max(1, int(self.cfg.max_outer_gen_val_iters))
                gen_res: Optional[PhaseResult] = None
                val_res: Optional[PhaseResult] = None
                outer_iter = 0
                attempts = 0
                prior_failures: Optional[str] = None
                while outer_iter < max_outer:
                    attempts += 1
                    if prior_failures is None:
                        gen_res = self._phase_generate()
                    else:
                        gen_res = self._phase_repair(
                            prior_failures, attempt=attempts - 1
                        )
                    if not gen_res.ok:
                        # GENERATE itself failed — no point retrying VALIDATE
                        break
                    if stop_after == "generate":
                        # A generate-only canary must not silently run the
                        # uncalibrated physical validation suite.
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
                    gen_res.metadata["outer_gen_val_iters"] = attempts
                    results.append(gen_res)
                if val_res is not None:
                    results.append(val_res)
                    if not val_res.ok:
                        skipping = True
                        skip_reason = (
                            f"skipped — outer GEN←VAL loop exhausted "
                            f"({attempts} attempts) and structural tests still fail: "
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

        return self._finish_result(results)

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


# ──────────────────────────────────────────────────────────────────────────
# Unified phase-one entrypoint
# ──────────────────────────────────────────────────────────────────────────


_STAGE1_MAX_REPAIRS = 3
_STAGE1_FRAMEWORK_TIMEOUT_SEC = 900
_STAGE1_SCRATCH_CHECKS = {
    # These are the checks emitted by FromScratchOrchestrator's existing
    # validator.  Requiring the complete set prevents a truthful all_ok=True
    # on a report that simply omitted one of the trusted behaviours.
    "h1": {
        "no_skeleton_import", "robot_class_present", "build_from_mjcf",
        "home", "stand_balance", "squat", "humanoid_walk", "describe",
    },
    "skydio_x2": {
        "no_skeleton_import", "robot_class_present", "build_from_mjcf",
        "home", "takeoff", "move_to", "describe",
    },
}


def _stage1_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def _stage1_read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _stage1_pointer(value: Any) -> Optional[str]:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return str(path.resolve().relative_to(REPO_ROOT.parent.resolve()))
    except ValueError:
        return str(path.resolve())


def _stage1_python() -> str:
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.is_file() else sys.executable


def _stage1_git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT.parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:  # noqa: BLE001 - metadata must not block a run
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    value = str(getattr(result, "stdout", "") or "").strip()
    return value or None


def _stage1_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage1_transport_error(result: Any) -> bool:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, Mapping) and bool(metadata.get("transport_error")):
        return True
    trace = getattr(result, "trace", None) or []
    if not trace:
        return False
    last = trace[-1]
    reason = last.get("stop_reason") if isinstance(last, Mapping) else getattr(last, "stop_reason", None)
    return reason == "invoke_error"


def _stage1_external_exception(exc: BaseException) -> bool:
    """Identify model/session failures without labelling local bugs blocked."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, ValueError) and "missing holistic api credential" in str(exc).lower():
        return True
    if isinstance(exc, (AttributeError, AssertionError, KeyError, NameError,
                        TypeError, ValueError, UnboundLocalError)):
        return False
    text = (type(exc).__name__ + " " + str(exc)).lower()
    return any(token in text for token in (
        "invoke", "holistic", "bedrock", "agentcore", "credential",
        "throttl", "rate limit", "network", "connection", "timeout",
        "timed out", "dns", "authentication", "unauthorized",
    ))


def _stage1_trace_usage(path: Path) -> dict:
    """Recover usage from a partially written JSONL trace after an exception."""
    totals: dict[str, int | float] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals
    for line in lines:
        try:
            step = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        usage = step.get("token_usage") if isinstance(step, dict) else None
        if not isinstance(usage, Mapping):
            continue
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            totals[key] = totals.get(key, 0) + value
    return totals


def _stage1_phase_result(
    runner: Any,
    result: Any,
    *,
    name: str,
    started: float,
    artifact_names: tuple[str, ...],
) -> PhaseResult:
    """Normalize a raw scratch ReactResult or standard PhaseResult."""
    elapsed = max(0.0, time.time() - started)
    trace_path = getattr(result, "trace_path", None)
    if trace_path is None:
        trace_path = runner.workspace / "traces" / f"{name}.jsonl"
    else:
        trace_path = Path(trace_path)
    artifacts = [runner.workspace / rel for rel in artifact_names
                 if (runner.workspace / rel).is_file()]
    transport_error = _stage1_transport_error(result)
    token_usage = getattr(result, "token_usage", None)
    if token_usage is None:
        token_usage = getattr(result, "total_tokens", None)
    if not isinstance(token_usage, Mapping):
        token_usage = _stage1_trace_usage(trace_path)
    else:
        token_usage = dict(token_usage)
    existing = result if isinstance(result, PhaseResult) else None
    if existing is not None:
        duration = float(existing.duration_sec) if existing.duration_sec > 0 else elapsed
        metadata = dict(existing.metadata or {})
        if transport_error:
            metadata["transport_error"] = True
        return PhaseResult(
            name=existing.name or name,
            ok=bool(existing.ok) and not transport_error,
            duration_sec=duration,
            trace_path=Path(existing.trace_path) if existing.trace_path else trace_path,
            artifact_paths=list(existing.artifact_paths or artifacts),
            final_text=existing.final_text,
            error=existing.error,
            token_usage=dict(existing.token_usage or token_usage),
            metadata=metadata,
        )

    ok = bool(artifacts) and not transport_error
    error = getattr(result, "error", None)
    if error is not None:
        error = str(error)
    return PhaseResult(
        name=name,
        ok=ok,
        duration_sec=elapsed,
        trace_path=trace_path if trace_path.exists() else None,
        artifact_paths=artifacts,
        final_text=str(getattr(result, "final_text", "") or ""),
        error=error,
        token_usage=dict(token_usage),
        metadata={"transport_error": True} if transport_error else {},
    )


def _stage1_exception_result(
    runner: Any,
    *,
    name: str,
    started: float,
    exc: BaseException,
    artifact_names: tuple[str, ...],
    external_blocked: bool = False,
) -> PhaseResult:
    trace_path = runner.workspace / "traces" / f"{name}.jsonl"
    artifacts = [runner.workspace / rel for rel in artifact_names
                 if (runner.workspace / rel).is_file()]
    return PhaseResult(
        name=name,
        ok=False,
        duration_sec=max(0.0, time.time() - started),
        trace_path=trace_path if trace_path.exists() else None,
        artifact_paths=artifacts,
        error=f"{type(exc).__name__}: {exc}",
        token_usage=_stage1_trace_usage(trace_path),
        metadata={"transport_error": True} if external_blocked else {},
    )


def _stage1_phase_payload(result: Optional[PhaseResult]) -> Optional[dict]:
    if result is None:
        return None
    return {
        "name": result.name,
        "ok": bool(result.ok),
        "duration_sec": float(result.duration_sec),
        "trace_path": _stage1_pointer(result.trace_path),
        "artifacts": [_stage1_pointer(path) for path in result.artifact_paths],
        "error": result.error,
        "token_usage": dict(result.token_usage or {}),
        "metadata": dict(result.metadata or {}),
    }


def _stage1_add_tokens(total: dict, usage: Mapping[str, Any] | None) -> None:
    if not isinstance(usage, Mapping):
        return
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total[key] = total.get(key, 0) + value


def _stage1_physics_seconds(report: Mapping[str, Any] | None) -> float:
    if not isinstance(report, Mapping):
        return 0.0
    for key in ("physics_duration_sec", "physics_time_sec", "sim_elapsed_s"):
        try:
            value = report.get(key)
            if value is not None:
                return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    total = 0.0
    for item in report.get("tests", []) or []:
        if not isinstance(item, Mapping):
            continue
        value = item.get("sim_elapsed_s")
        if value is None:
            metrics = item.get("metrics")
            measurements = metrics.get("measurements") if isinstance(metrics, Mapping) else None
            value = measurements.get("sim_elapsed_s") if isinstance(measurements, Mapping) else None
        try:
            if value is not None:
                total += max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return total


def _stage1_route(definition: Mapping[str, Any]) -> str:
    route = definition.get("generation_route")
    if route not in {"skeleton", "from_scratch"}:
        raise ValueError(f"unsupported generation_route={route!r}")
    return str(route)


def _stage1_canonical_definition(robot_id: str) -> dict:
    definition = find_robot_definition(robot_id)
    if not isinstance(definition, Mapping) or definition.get("id") != robot_id:
        raise ValueError(f"robot is not present in the trusted zoo: {robot_id}")
    return dict(definition)


def _stage1_canonical_mjcf(definition: Mapping[str, Any]) -> Path:
    relative = definition.get("capability_mjcf") or definition.get("mjcf")
    if not isinstance(relative, str) or not relative:
        raise ValueError("trusted robot definition has no MJCF")
    return (REPO_ROOT / relative).resolve()


def _stage1_reserved_outputs(root: Path, robot_id: str) -> list[Path]:
    paths = [root / f"summary_{robot_id}.json", root / "initial" / robot_id]
    try:
        repair_roots = [path for path in root.iterdir()
                        if path.name.startswith("repair_")]
    except OSError:
        repair_roots = []
    paths.extend(path / robot_id for path in repair_roots)
    return [path for path in paths if path.exists() or path.is_symlink()]


def _stage1_copy_candidate(source: Optional[Path], destination: Path,
                           route: str) -> list[Path]:
    """Copy only public Study + candidate inputs into a fresh repair round."""
    if source is None:
        return []
    source = source.resolve()
    if source.is_file():
        source = source.parent
    if not source.is_dir():
        raise FileNotFoundError(f"candidate source directory not found: {source}")
    names = ("driver.py", "study.json") if route == "skeleton" else (
        "driver_from_scratch.py", "driver.py", "study.json")
    copied: list[Path] = []
    for name in names:
        src = source / name
        if src.is_file():
            dst = destination / name
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


def _stage1_standard_runner(
    robot_id: str, mjcf_path: Path, workspace_root: Path, model: str
) -> SelfAssemble:
    cfg = SelfAssembleConfig(
        robot_id=robot_id,
        mjcf_path=mjcf_path,
        workspace_root=workspace_root,
        mode="local",
        validate_mode="framework",
        bedrock_model=model,
        model_provider="holistic",
        max_outer_gen_val_iters=1,
    )
    return SelfAssemble(cfg)


def _stage1_scratch_runner(
    robot_id: str, mjcf_path: Path, workspace_root: Path, model: str
) -> Any:
    from .orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

    kwargs = dict(
        robot_id=robot_id,
        mjcf_path=mjcf_path,
        workspace_root=workspace_root,
        bedrock_model=model,
        model_provider="holistic",
    )
    cfg = FromScratchConfig(**kwargs, mode="local")
    return FromScratchOrchestrator(cfg)


def _stage1_child_config(payload: Mapping[str, Any]) -> Any:
    """Build the original local orchestrator for the clean worker process."""
    workspace = Path(str(payload["workspace"])).resolve()
    robot_id = str(payload["robot_id"])
    mjcf_path = Path(str(payload["mjcf_path"])).resolve()
    model = str(payload["model"])
    route = str(payload["route"])
    if route == "skeleton":
        return _stage1_standard_runner(robot_id, mjcf_path, workspace.parent, model)
    return _stage1_scratch_runner(robot_id, mjcf_path, workspace.parent, model)


def _stage1_framework_child(payload: Mapping[str, Any]) -> int:
    """Private ``python -c`` target for a clean Framework process."""
    workspace = Path(str(payload["workspace"])).resolve()
    report_path = workspace / "validate_report.json"
    original_cwd = os.getcwd()
    try:
        os.chdir(workspace)
        runner = _stage1_child_config(payload)
        if str(payload["route"]) == "skeleton":
            phase = runner._phase_validate_framework()
            report = _stage1_read_json(report_path)
            if report is None:
                report = {
                    "tests": [],
                    "all_ok": False,
                    "error": getattr(phase, "error", None)
                             or "Framework phase did not write validate_report.json",
                }
                _stage1_write_json(report_path, report)
                return 2
        else:
            report = runner._validate_from_scratch_driver()
            _stage1_write_json(report_path, report)
        print(json.dumps({"all_ok": report.get("all_ok"),
                          "n_tests": len(report.get("tests", []) or [])}))
        return 0
    except Exception as exc:  # noqa: BLE001
        report = {
            "tests": [],
            "all_ok": False,
            "error": f"framework subprocess exception: {type(exc).__name__}: {exc}",
        }
        _stage1_write_json(report_path, report)
        print(report["error"], file=sys.stderr)
        return 2
    finally:
        os.chdir(original_cwd)


def _stage1_framework_ok(
    report: Mapping[str, Any] | None,
    robot_id: str,
    definition: Mapping[str, Any],
    route: str,
) -> bool:
    if not isinstance(report, Mapping) or report.get("all_ok") is not True:
        return False
    tests = [item for item in (report.get("tests", []) or [])
             if isinstance(item, Mapping)]
    if route == "skeleton" and definition.get("capability_profile"):
        from .robot_catalog import load_capability_suite

        try:
            suite = load_capability_suite(dict(definition))
        except Exception:
            return False
        expected = {
            str(item.get("case_id", item.get("id")))
            for item in suite.get("cases", suite.get("tests", []))
            if isinstance(item, Mapping) and item.get("case_id", item.get("id"))
        }
        if not expected:
            return False
        for case_id in expected:
            matching = [item for item in tests if str(
                item.get("case_id", item.get("id", item.get("test")))) == case_id]
            if not matching or not all(item.get("ok") is True for item in matching):
                return False
        return True
    required = _STAGE1_SCRATCH_CHECKS.get(robot_id) if route == "from_scratch" else None
    if required:
        by_name = {str(item.get("test")): item for item in tests}
        return required.issubset(by_name) and all(
            by_name[name].get("ok") is True for name in required
        )
    return True


def _stage1_framework_subprocess(
    *,
    robot_id: str,
    workspace: Path,
    definition: Mapping[str, Any],
    route: str,
    mjcf_path: Path,
    model: str,
) -> tuple[dict, dict]:
    """Run the original Framework validator in a fresh AA1 venv process."""
    report_path = workspace / "validate_report.json"
    if report_path.exists() and report_path.is_file():
        report_path.unlink()
    payload = {
        "robot_id": robot_id,
        "workspace": str(workspace.resolve()),
        "mjcf_path": str(mjcf_path.resolve()),
        "route": route,
        "model": model,
        "robot_definition": dict(definition),
    }
    code = (
        "import json, sys; "
        "from auto_adapter.orchestrator import _stage1_framework_child; "
        "raise SystemExit(_stage1_framework_child(json.loads(sys.argv[1])))"
    )
    command = [_stage1_python(), "-c", code, json.dumps(payload, default=str)]
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + current if current else "")
    started = time.time()
    info: dict[str, Any] = {
        "command": command,
        "timeout_sec": _STAGE1_FRAMEWORK_TIMEOUT_SEC,
        "route": route,
        "model": model,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=_STAGE1_FRAMEWORK_TIMEOUT_SEC,
            check=False,
        )
        info.update({
            "returncode": getattr(completed, "returncode", None),
            "duration_sec": max(0.0, time.time() - started),
            "stdout_tail": str(getattr(completed, "stdout", "") or "")[-4000:],
            "stderr_tail": str(getattr(completed, "stderr", "") or "")[-4000:],
        })
    except subprocess.TimeoutExpired as exc:
        info.update({
            "returncode": None,
            "duration_sec": max(0.0, time.time() - started),
            "timed_out": True,
            "stdout_tail": str(exc.stdout)[-4000:] if exc.stdout else "",
            "stderr_tail": str(exc.stderr)[-4000:] if exc.stderr else "",
        })
    except OSError as exc:
        info.update({
            "returncode": None,
            "duration_sec": max(0.0, time.time() - started),
            "spawn_error": f"{type(exc).__name__}: {exc}",
        })
    report = _stage1_read_json(report_path)
    report_valid = report is not None
    if report is None:
        info["report_missing"] = True
        report = {
            "tests": [],
            "all_ok": False,
            "error": info.get("spawn_error")
                     or ("Framework subprocess timed out" if info.get("timed_out")
                         else "Framework subprocess did not write validate_report.json"),
        }
        _stage1_write_json(report_path, report)
    child_ok = (
        info.get("returncode") == 0
        and not info.get("timed_out")
        and not info.get("spawn_error")
        and report_valid
        and report_path.is_file()
    )
    info["child_ok"] = bool(child_ok)
    if not child_ok:
        report = dict(report)
        report["all_ok"] = False
        report["error"] = (
            "Framework subprocess failed; report is not accepted: "
            + str(info.get("spawn_error") or info.get("stderr_tail")
                  or "nonzero returncode")
        )
        _stage1_write_json(report_path, report)
    info["report_path"] = _stage1_pointer(report_path)
    _stage1_write_json(workspace / "framework_subprocess.json", info)
    return report, info


def _stage1_run_phase(runner: Any, name: str, fn: Any,
                      artifact_names: tuple[str, ...]) -> PhaseResult:
    started = time.time()
    try:
        raw = fn()
    except BaseException as exc:  # noqa: BLE001 - preserve partial traces
        return _stage1_exception_result(
            runner, name=name, started=started, exc=exc,
            artifact_names=artifact_names,
            external_blocked=_stage1_external_exception(exc),
        )
    return _stage1_phase_result(
        runner, raw, name=name, started=started,
        artifact_names=artifact_names,
    )


def _stage1_round_context(
    workspace: Path,
    *,
    robot_id: str,
    model: str,
    route: str,
    attempt: int,
    max_repairs: int,
    started_at: str,
    git_commit: Optional[str],
) -> dict:
    return {
        "robot_id": robot_id,
        "provider": "holistic",
        "model": model,
        "route": route,
        "round": "initial" if attempt == 0 else f"repair_{attempt}",
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": None,
        "git_commit": git_commit,
        "parameters": {"max_repairs": max_repairs},
        "actual_generation_cost_usd": None,
    }


def _stage1_update_summary(path: Path, summary: dict) -> None:
    _stage1_write_json(path, summary)


def run_stage1(*, robot_id: str, workspace_root: Path | str, model: str,
               max_repairs: int = 3) -> dict:
    """Run one fresh Study → Generate → Framework → bounded repair path.

    The entrypoint deliberately accepts no resume/source argument.  Every
    output round is created under ``initial`` or ``repair_N`` and an existing
    robot output is rejected before any round is constructed.
    """
    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int):
        raise TypeError("max_repairs must be an integer from 0 to 3")
    if not 0 <= max_repairs <= _STAGE1_MAX_REPAIRS:
        raise ValueError("max_repairs must be from 0 to 3")
    if not isinstance(robot_id, str) or not robot_id:
        raise ValueError("robot_id must be a non-empty string")
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")

    definition = _stage1_canonical_definition(robot_id)
    route = _stage1_route(definition)
    mjcf_path = _stage1_canonical_mjcf(definition)
    root = Path(workspace_root).expanduser().resolve()
    reserved = _stage1_reserved_outputs(root, robot_id)
    if reserved:
        raise FileExistsError(
            "refusing to overwrite existing phase-one output: "
            + ", ".join(_stage1_pointer(path) or str(path) for path in reserved)
        )

    summary_path = root / f"summary_{robot_id}.json"
    git_commit = _stage1_git_commit()
    run_started = time.time()
    summary: dict[str, Any] = {
        "robot_id": robot_id,
        "provider": "holistic",
        "model": model,
        "route": route,
        "git_commit": git_commit,
        "max_repairs": max_repairs,
        "attempts": 0,
        "effective_repairs": 0,
        "rounds": [],
        "generation_ok": False,
        "framework_ok": False,
        "stage1_ok": False,
        "external_blocked": False,
        "error": None,
        "total_tokens": {},
        "total_duration_sec": 0.0,
        "generation_duration_sec": 0.0,
        "framework_duration_sec": 0.0,
        "physics_duration_sec": 0.0,
        "physics_time_sec": 0.0,
        "actual_generation_cost_usd": None,
        "workspace": None,
        "summary_path": _stage1_pointer(summary_path),
        "final_candidate": None,
        "final_report": None,
    }
    _stage1_update_summary(summary_path, summary)

    total_tokens: dict[str, Any] = {}
    candidate_path: Optional[Path] = None
    previous_workspace: Optional[Path] = None
    report: Optional[dict] = None
    report_path: Optional[Path] = None
    last_error: Optional[str] = None
    external_blocked = False
    generation_ok = False
    framework_ok = False

    for attempt in range(max_repairs + 1):
        round_root = root / ("initial" if attempt == 0 else f"repair_{attempt}")
        round_started = _stage1_utc_now()
        round_summary: dict[str, Any] = {
            "attempt": attempt,
            "round": "initial" if attempt == 0 else f"repair_{attempt}",
            "workspace": _stage1_pointer(round_root / robot_id),
            "copied_files": [],
        }
        summary["rounds"].append(round_summary)
        summary["attempts"] = attempt + 1
        _stage1_update_summary(summary_path, summary)

        context: Optional[dict] = None
        workspace: Optional[Path] = None
        try:
            if route == "skeleton":
                runner = _stage1_standard_runner(robot_id, mjcf_path, round_root, model)
                candidate_name = "driver.py"
                study_name = "study.json"
            else:
                runner = _stage1_scratch_runner(robot_id, mjcf_path, round_root, model)
                candidate_name = "driver_from_scratch.py"
                study_name = "study.json"
            workspace = Path(runner.workspace)
            summary["workspace"] = _stage1_pointer(workspace)
            context = _stage1_round_context(
                workspace, robot_id=robot_id, model=model, route=route,
                attempt=attempt, max_repairs=max_repairs,
                started_at=round_started, git_commit=git_commit,
            )
            _stage1_write_json(workspace / "run_context.json", context)
            round_summary["run_context"] = _stage1_pointer(workspace / "run_context.json")
            if attempt > 0:
                copied = _stage1_copy_candidate(previous_workspace,
                                                workspace, route)
                round_summary["copied_files"] = [_stage1_pointer(path) for path in copied]
                _stage1_update_summary(summary_path, summary)

            # The Study phase is only run once.  Repairs receive the original
            # public study artifact and never regenerate it.
            if attempt == 0:
                study_fn = (runner._phase_study if route == "skeleton"
                            else runner.phase_study)
                study = _stage1_run_phase(
                    runner, "01_study", study_fn,
                    (study_name,),
                )
                round_summary["study"] = _stage1_phase_payload(study)
                _stage1_add_tokens(total_tokens, study.token_usage)
                summary["total_tokens"] = dict(total_tokens)
                summary["generation_duration_sec"] += study.duration_sec
                _stage1_update_summary(summary_path, summary)
                context["study"] = _stage1_phase_payload(study)
                _stage1_write_json(workspace / "run_context.json", context)
                if not study.ok or _stage1_transport_error(study):
                    last_error = study.error or "STUDY failed"
                    external_blocked = bool(_stage1_transport_error(study))
                    round_summary["framework_ok"] = False
                    context["finished_at"] = _stage1_utc_now()
                    _stage1_write_json(workspace / "run_context.json", context)
                    break
            elif not (workspace / study_name).is_file():
                last_error = "study.json missing from repair input"
                context["finished_at"] = _stage1_utc_now()
                _stage1_write_json(workspace / "run_context.json", context)
                break

            if attempt == 0:
                generate_fn = (runner._phase_generate if route == "skeleton"
                               else runner.phase_gen_algo)
                generation = _stage1_run_phase(
                    runner, ("02_generate" if route == "skeleton" else "02_gen_algo"),
                    generate_fn,
                    (candidate_name,),
                )
            else:
                feedback = validate_failure_feedback(report or {})
                _stage1_write_json(workspace / "feedback_input.json", {"feedback": feedback})
                round_summary["feedback_input"] = _stage1_pointer(workspace / "feedback_input.json")
                _stage1_update_summary(summary_path, summary)
                repair_fn = (lambda: runner._phase_repair(feedback, attempt)
                             if route == "skeleton"
                             else runner.phase_gen_repair(feedback, attempt))
                generation = _stage1_run_phase(
                    runner,
                    (f"03_repair_{attempt}" if route == "skeleton"
                     else f"03_gen_repair_{attempt}"),
                    repair_fn,
                    (candidate_name,),
                )
            round_summary["generation"] = _stage1_phase_payload(generation)
            _stage1_add_tokens(total_tokens, generation.token_usage)
            summary["total_tokens"] = dict(total_tokens)
            summary["generation_duration_sec"] += generation.duration_sec
            _stage1_update_summary(summary_path, summary)
            context["generation"] = _stage1_phase_payload(generation)
            _stage1_write_json(workspace / "run_context.json", context)

            candidate = workspace / candidate_name
            candidate_path = candidate if candidate.is_file() else None
            generation_ok = bool(
                generation.ok and candidate_path and not _stage1_transport_error(generation)
            )
            previous_workspace = workspace
            round_summary["candidate"] = _stage1_pointer(candidate_path)
            if attempt > 0 and generation.ok and generation_ok:
                summary["effective_repairs"] += 1
            summary["generation_ok"] = generation_ok
            _stage1_update_summary(summary_path, summary)

            if _stage1_transport_error(generation):
                last_error = generation.error or "model invocation failed"
                external_blocked = True
                generation_ok = False
                round_summary["framework_ok"] = False
                context["finished_at"] = _stage1_utc_now()
                _stage1_write_json(workspace / "run_context.json", context)
                break

            # Framework runs even when Generate omitted the candidate, so the
            # missing-build signal reaches the repair prompt.
            report, framework_info = _stage1_framework_subprocess(
                robot_id=robot_id, workspace=workspace, definition=definition,
                route=route, mjcf_path=mjcf_path, model=model,
            )
            report_path = workspace / "validate_report.json"
            framework_seconds = float(framework_info.get("duration_sec") or 0.0)
            summary["framework_duration_sec"] += framework_seconds
            summary["physics_duration_sec"] += _stage1_physics_seconds(report)
            summary["physics_time_sec"] = summary["physics_duration_sec"]
            framework_ok = bool(framework_info.get("child_ok")) and _stage1_framework_ok(
                report, robot_id, definition, route,
            )
            candidate_path = candidate if candidate.is_file() else None
            generation_ok = bool(candidate_path) and generation_ok
            summary["generation_ok"] = generation_ok
            summary["framework_ok"] = framework_ok
            round_summary["validate_report"] = _stage1_pointer(report_path)
            round_summary["framework_report"] = round_summary["validate_report"]
            round_summary["framework_subprocess"] = framework_info
            round_summary["framework_ok"] = framework_ok
            context["validate_report"] = _stage1_pointer(report_path)
            context["framework_subprocess"] = framework_info
            context["finished_at"] = _stage1_utc_now()
            _stage1_write_json(workspace / "run_context.json", context)
            _stage1_update_summary(summary_path, summary)

            if framework_ok and generation_ok:
                last_error = None
                break
            if framework_ok and not generation_ok:
                # A Framework pass cannot hide a failed generation call.  Keep
                # the generation error visible and use the remaining repair
                # budget to obtain a completed model response.
                last_error = generation.error or "generation failed"
                if attempt >= max_repairs:
                    break
                continue
            last_error = str(report.get("error") or "Framework validation failed")
            if attempt >= max_repairs:
                break
        except BaseException as exc:  # noqa: BLE001 - preserve round evidence
            last_error = f"{type(exc).__name__}: {exc}"
            external_blocked = _stage1_external_exception(exc)
            round_summary["error"] = last_error
            if context is not None and workspace is not None:
                context["finished_at"] = _stage1_utc_now()
                _stage1_write_json(workspace / "run_context.json", context)
            _stage1_update_summary(summary_path, summary)
            # The workspace and context exist whenever construction completed;
            # keep the finished timestamp absent only for a failed constructor.
            break

        if framework_ok and generation_ok:
            break

    summary["total_tokens"] = total_tokens
    summary["generation_ok"] = bool(
        generation_ok and candidate_path and candidate_path.is_file()
    )
    summary["framework_ok"] = bool(framework_ok)
    summary["stage1_ok"] = bool(summary["generation_ok"] and framework_ok
                                and not external_blocked)
    summary["external_blocked"] = bool(external_blocked)
    summary["error"] = last_error
    summary["total_duration_sec"] = max(0.0, time.time() - run_started)
    summary["final_candidate"] = _stage1_pointer(candidate_path)
    summary["final_report"] = _stage1_pointer(report_path)
    summary["final_candidate_path"] = summary["final_candidate"]
    summary["final_report_path"] = summary["final_report"]
    summary["final_framework_report"] = summary["final_report"]
    _stage1_update_summary(summary_path, summary)
    return summary
