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
    STUDY        — inspect the actual robot and MJCF
    DESIGN       — derive the current task-grounded capability and case design
    GENERATE     — write driver_from_scratch.py with no skeleton library
    VALIDATE     — run the current design cases on real MuJoCo physics
    EXPORT       — wrap the validated driver's methods in an MCP server
    DEMO         — optional configured local ReCAP task and video

Output:
    workspace/driver_from_scratch.py  — single-file driver, no auto_adapter imports
    workspace/validate_report.json
    workspace/recordings/*.mp4
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from .agent import ReactLoop, ReactResult, ToolSpec
from .export_support import (
    collect_design_artifacts,
    collect_validation_artifacts,
    dynamic_export_prompt,
    validate_dynamic_export_source,
    validation_case_metadata,
)
from .agent.tools import (
    make_local_exec_tool,
    make_read_file_tool,
    make_write_file_tool,
)
from .orchestrator import (
    PhaseResult,
    _actual_mjcf_context,
    _capability_design_metadata,
    _merge_numeric_usage,
    _scene_handoff_context,
    validate_failure_feedback,
)


def _vlog(msg: str) -> None:
    """Verbose trace to stderr (gated by AA_VERBOSE) so we can see exactly
    which phase / call the pipeline is in when it stalls."""
    if os.environ.get("AA_VERBOSE"):
        print(f"[{time.strftime('%H:%M:%S')}] [orch] {msg}", file=sys.stderr,
              flush=True)


def _current_design_context(design: Mapping[str, Any]) -> str:
    """Expose only this run's public DESIGN contract to generation."""
    signatures = "\n".join(
        f"def {capability['method_name']}(self, request): ..."
        for capability in design["capabilities"]
    )
    return (
        "\n\nCURRENT PUBLIC CAPABILITY DESIGN:\n"
        + json.dumps(design, ensure_ascii=False)
        + "\nRequired interface:\n"
        + signatures
        + "\nImplement every method using your own MuJoCo/NumPy control code. "
        "Retain Robot.build_from_mjcf(mjcf_path). Implement request handling, "
        "feedback, ordering, holds and bounded failure as specified. Use the "
        "same model/data for all actions and observations. Advance real physics "
        "through actuator commands; never set live qpos/qvel to achieve an "
        "action. Measure request deadlines and continuous holds with data.time. "
        "Do not read validation suites or reference control implementations.\n"
    )


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

    max_iters_study: int = 14
    max_iters_gen_algo: int = 40         # algorithm synthesis needs many iters
    max_iters_gen_repair: int = 20       # repair passes are shorter than first gen
    max_iters_export: int = 8
    max_outer_retries: int = 3           # repairs after the initial generation/validation
    max_tokens_per_turn: int = 8000
    enable_demo: bool = False
    demo_config_path: Path | None = None
    # The dynamic design and real-MuJoCo validation route is local-only.
    mode: str = "local"

    # An explicit design can be supplied for a diagnostic resume.  Otherwise
    # DESIGN derives the current contract and scene cases from this run's STUDY.
    capability_design_path: Path | None = None
    scene_cases_path: Path | None = None
    max_iters_capability_design: int = 30

    def __post_init__(self) -> None:
        if self.mode != "local":
            raise ValueError("the dynamic from-scratch pipeline requires mode='local'")
        if self.scene_cases_path is not None and self.capability_design_path is None:
            raise ValueError(
                "scene_cases_path requires capability_design_path"
            )
        if (isinstance(self.max_iters_capability_design, bool)
                or not isinstance(self.max_iters_capability_design, int)
                or self.max_iters_capability_design < 1):
            raise ValueError("max_iters_capability_design must be a positive integer")


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
    design_ok: bool = False
    phases: list[Any] = field(default_factory=list)
    ok: bool = False
    export_ok: bool = False
    demo_ok: bool | None = None

    def to_json(self) -> dict:
        """Return the stable public result shape used by Stage 1 callers."""
        return {
            "robot_id": self.robot_id,
            "workspace": str(self.workspace),
            "ok": self.ok,
            "study_ok": self.study_ok,
            "design_ok": self.design_ok,
            "gen_ok": self.gen_ok,
            "validate_ok": self.validate_ok,
            "generation_ok": self.gen_ok,
            "framework_ok": self.validate_ok,
            "stage1_ok": bool(self.study_ok and self.design_ok and self.gen_ok and self.validate_ok),
            "export_ok": self.export_ok,
            "demo_ok": self.demo_ok,
            "driver_path": str(self.driver_path) if self.driver_path else None,
            "validate_report": self.validate_report,
            "total_duration_sec": self.total_duration_sec,
            "total_tokens": self.total_tokens,
            "error": self.error,
            "phases": json.loads(json.dumps(self.phases, default=str)),
        }


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
You are Phase 3 GENERATE. Write driver_from_scratch.py as one complete
single-file driver based on the current study, capability design, and MJCF.

Hard rules:
  - The generated driver must use mujoco, numpy, and the Python standard
    library only. It must not import auto_adapter, supplied skeletons,
    retained policies, or reference drivers. A local_exec probe may import
    auto_adapter.scene_runtime only to stage a disposable prepared scene and
    apply its public initial state; that helper is not part of the driver.
  - Expose class Robot with classmethod
    build_from_mjcf(mjcf_path: str) -> Robot, plus home(),
    get_joint_positions(), step(n=1), render(), and describe().
  - Implement the complete method(request) interface in the current public
    capability design. The current design defines the capability surface.
  - Load the supplied MJCF path. Prepared scenes may add bodies and qpos; bind
    robot joints, actuators, bodies, and sites by names established from the
    current study and model rather than whole-array offsets.
  - Keep one real model/data pair for actions and observations. Advance actions
    only through native actuator controls and mujoco.mj_step. Never teleport
    live qpos/qvel or change dynamics to produce an effect. Scratch MjData is
    allowed for kinematic calculations.
  - Use data.time for simulation durations, deadlines, and continuous holds.
    Clamp controls and targets to the actual model limits and fail clearly when
    a request cannot be completed.
  - Write code in complete chunks of at most 150 lines. Use append=true only
    for later chunks of the same file.

Tools:
  - read_file for study.json, mjcf.xml, and allowed MuJoCo source
  - execute_python or local_exec for bounded probes of the actual MJCF
  - local_exec for real import and MuJoCo smoke checks
  - write_file for driver_from_scratch.py

Procedure:
  1. Read study.json and inspect the actual MJCF.
  2. Choose the minimum controller and observation code that satisfies the
     current design, and probe uncertain bindings or dynamics on the real model.
  3. Write driver_from_scratch.py with the complete Robot class.
  4. If DESIGN supplied ``design/probe_scenes.json``, read it and use
     ``build_scene_driver`` plus ``reset_scene_driver`` in a disposable
     local_exec directory for representative current-design methods. Use the
     base ``mjcf.xml`` only for robot structure checks. If no prepared scene
     was supplied, state that explicitly and do not search old artifacts.
  5. Finish only after the driver imports and those focused probes run.
"""


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


class FromScratchOrchestrator:
    """Build and validate a current-design driver without skeleton imports."""

    def __init__(self, cfg: FromScratchConfig) -> None:
        self.cfg = cfg
        self.capability_design = None
        self.scene_cases_path: Path | None = None
        self.scene_paths: dict[str, Path] = {}
        self.probe_scenes_path: Path | None = None
        self._last_capability_preparation: dict | None = None
        self.workspace = (Path(cfg.workspace_root) / cfg.robot_id).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "traces").mkdir(parents=True, exist_ok=True)
        (self.workspace / "recordings").mkdir(parents=True, exist_ok=True)

        src = Path(cfg.mjcf_path).resolve()
        dst = self.workspace / "mjcf.xml"
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src)

    def __enter__(self) -> "FromScratchOrchestrator":
        # Model transport remains configurable, while every code and MuJoCo
        # probe executes in the run's local workspace.
        return self

    @staticmethod
    def _localize_prompt(prompt: str) -> str:
        """Adapt AgentCore wording to fresh-process local_exec semantics."""
        localized = (
            prompt
            .replace("execute_python", "local_exec")
            .replace(
                "The session\nkeeps state across calls.",
                "Each local_exec call starts a fresh process; include imports "
                "and setup in every command or save intermediates in workspace files.",
            )
            .replace(
                "The session keeps state across calls.",
                "Each local_exec call starts a fresh process; include imports "
                "and setup in every command or save intermediates in workspace files.",
            )
            .replace(
                "sandboxed CI",
                "the local workspace",
            )
            .replace("local_exec or local_exec", "local_exec")
        )
        if "fresh process" not in localized:
            localized += (
                "\n\nLocal execution note: each local_exec call starts a fresh process; "
                "include imports and setup in every command or save intermediates "
                "in workspace files."
            )
        return localized

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    # ─── Tool bundles ─────────────────────────────────────────────────────

    def _read_extra_roots(self) -> list[Path]:
        """The agent is allowed to read the MJCF source dir (for <include>
        resolution) and the mujoco Python source (to learn the API). NOT
        auto_adapter.skeletons — that's the whole point of from-scratch.
        """
        import mujoco  # noqa: PLC0415

        mjcf_src_dir = Path(self.cfg.mjcf_path).resolve().parent
        mujoco_src = Path(mujoco.__file__).parent
        scene_roots = {
            Path(path).resolve().parent for path in self.scene_paths.values()
        }
        return [mjcf_src_dir, mujoco_src, *sorted(scene_roots, key=str)]

    def _common_tools(self) -> list[ToolSpec]:
        return [
            make_write_file_tool(self.workspace),
            make_read_file_tool(self.workspace, extra_roots=self._read_extra_roots()),
        ]

    def _local_runtime_tool(self) -> ToolSpec:
        return make_local_exec_tool(
            self.workspace,
            # The generated driver remains no-skeleton/no-auto_adapter code.
            # This path is only for the public scene_runtime probe helper.
            python_path_prepend=[Path(__file__).resolve().parents[1]],
        )

    def _require_capability_design(self, phase: str) -> None:
        if not isinstance(self.capability_design, dict):
            raise RuntimeError(
                f"{phase} requires a successful STUDY and DESIGN"
            )

    def _prepare_capability_design_from_study(self) -> dict:
        """Prepare the current design from this run's completed STUDY."""
        study_path = self.workspace / "study.json"
        if not study_path.is_file():
            raise FileNotFoundError("study.json missing after STUDY")
        study = json.loads(study_path.read_text(encoding="utf-8"))
        if not isinstance(study, dict):
            raise ValueError("study.json must contain one JSON object")
        if study.get("robot_id", self.cfg.robot_id) != self.cfg.robot_id:
            raise ValueError("study.json robot_id does not match the run robot")
        input_dir = self.workspace / "design"
        input_dir.mkdir(parents=True, exist_ok=True)
        design_output = input_dir / "capability_design.json"
        preparation_path = input_dir / "capability_preparation.json"
        started = time.time()
        try:
            from .capability_design import (  # noqa: PLC0415
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
                design_path = supplied_path
                preparation = {
                    "mode": "supplied", "token_usage": {}, "duration_sec": 0.0,
                    "trace_path": None, "error": None,
                }
                import shutil  # noqa: PLC0415
                if supplied_path != design_output.resolve():
                    shutil.copy2(supplied_path, design_output)
            else:
                # Scratch receives no skeleton context by design.
                preparation_path.unlink(missing_ok=True)
                result = generate_capability_design(
                    robot_id=self.cfg.robot_id,
                    study=study,
                    study_path=self.workspace / "study.json",
                    mjcf_path=Path(self.cfg.mjcf_path),
                    task_library_dir=Path(task_library_for_robot(self.cfg.robot_id)),
                    output_dir=input_dir,
                    model=self.cfg.bedrock_model,
                    provider=self.cfg.model_provider,
                    region=self.cfg.aws_region,
                    max_iters=self.cfg.max_iters_capability_design,
                    max_tokens_per_turn=self.cfg.max_tokens_per_turn,
                    skeleton_context=None,
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
            if not isinstance(design, Mapping) or not design.get("capabilities"):
                raise ValueError("capability design has no capabilities")
            preparation = dict(preparation)
            preparation.setdefault("token_usage", {})
            preparation.setdefault("duration_sec", time.time() - started)
            preparation.setdefault("trace_path", None)
            preparation.setdefault("error", None)
            preparation["design_path"] = str(design_path)
            preparation["output_dir"] = str(input_dir)
            preparation_path.write_text(
                json.dumps(preparation, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self._prepare_scene_inputs(dict(design), preparation, input_dir)
            preparation_path.write_text(
                json.dumps(preparation, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.capability_design = dict(design)
            self._last_capability_preparation = dict(preparation)
            return preparation
        except Exception as exc:
            self.capability_design = None
            preparation = _capability_design_metadata(preparation_path)
            preparation.update({
                "design_path": str(design_output),
                "output_dir": str(input_dir),
                "duration_sec": float(preparation.get("duration_sec") or time.time() - started),
                "token_usage": preparation.get("token_usage") or {},
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
        from .scene_runtime import (
            export_probe_scenes,
            load_scene_cases,
            prepare_scenes,
        )

        self.probe_scenes_path = None
        supplied_cases = self.cfg.scene_cases_path
        raw_cases = preparation.get("scene_cases_path")
        if supplied_cases is None and raw_cases:
            supplied_cases = Path(str(raw_cases))
            if not supplied_cases.is_absolute():
                supplied_cases = output_dir / supplied_cases
        if supplied_cases is None:
            if self.cfg.capability_design_path is None:
                raise ValueError("automatic capability preparation did not produce scene cases")
            self.scene_cases_path = None
            self.scene_paths = {}
            preparation.pop("probe_scenes_path", None)
            return
        case_path = Path(supplied_cases).expanduser().resolve()
        suite = load_scene_cases(case_path, design=dict(design))
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
        probe_path = export_probe_scenes(
            suite=suite,
            scene_paths=paths,
            output_path=output_dir / "probe_scenes.json",
        )
        self.probe_scenes_path = probe_path
        preparation["probe_scenes_path"] = str(probe_path)

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
        # A new STUDY invalidates every prior design and prepared scene.
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self.probe_scenes_path = None
        self._last_capability_preparation = None
        tools = self._common_tools() + [self._local_runtime_tool()]
        system = self._localize_prompt(_STUDY_SYSTEM)
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "MJCF (workspace-relative): mjcf.xml\n"
            "Produce study.json per the procedure."
        )
        user += _actual_mjcf_context(self.cfg)
        result = self._run_loop(
            name="01_study", system=system, user_msg=user,
            tools=tools, max_iters=self.cfg.max_iters_study,
        )
        return result

    def phase_design(self) -> ReactResult:
        """Prepare/load the capability design after STUDY."""
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self.probe_scenes_path = None
        self._last_capability_preparation = None
        try:
            preparation = self._prepare_capability_design_from_study()
            result = ReactResult(
                final_text="capability design prepared", trace=[], ok=True,
                total_tokens=_merge_numeric_usage({}, preparation.get("token_usage")),
            )
            result.capability_preparation = preparation
            return result
        except Exception as exc:  # noqa: BLE001 - gate GEN_ALGO
            preparation = getattr(self, "_last_capability_preparation", None)
            result = ReactResult(
                final_text="", trace=[], ok=False,
                error=f"capability design preparation failed: {exc}",
                total_tokens=_merge_numeric_usage(
                    {}, preparation.get("token_usage") if isinstance(preparation, Mapping) else {}
                ),
            )
            if isinstance(preparation, Mapping):
                result.capability_preparation = dict(preparation)
            return result

    def phase_gen_algo(self) -> ReactResult:
        """The from-scratch driver synthesis phase."""
        self._require_capability_design("GEN_ALGO")
        tools = self._common_tools() + [self._local_runtime_tool()]
        system = self._localize_prompt(_GEN_ALGO_SYSTEM)
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            "study.json is in the workspace. MJCF at mjcf.xml.\n"
            "Write driver_from_scratch.py — full Robot class with YOUR own\n"
            "control and observation code, no auto_adapter.skeletons imports."
        )
        user += _actual_mjcf_context(self.cfg)
        user += _current_design_context(self.capability_design)
        user += _scene_handoff_context(
            self.probe_scenes_path,
            driver_name="driver_from_scratch.py",
            from_scratch=True,
        )
        return self._run_loop(
            name="02_gen_algo", system=system, user_msg=user,
            tools=tools, max_iters=self.cfg.max_iters_gen_algo,
        )

    def phase_gen_repair(self, feedback: str, attempt: int) -> ReactResult:
        """Repair the existing driver from candidate-facing feedback.

        The generated driver iterates against candidate-facing measurements
        from the current DESIGN cases while the trusted validator stays outside
        the agent's inner loop.
        """
        self._require_capability_design("GEN_REPAIR")
        tools = self._common_tools() + [self._local_runtime_tool()]
        system = self._localize_prompt(_GEN_ALGO_SYSTEM)
        verification_tool = "local_exec"
        user = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"This is repair attempt {int(attempt)}. Your existing "
            "driver_from_scratch.py is the candidate to fix IN PLACE.\n"
            "The held-out Framework validator supplied this candidate-facing "
            "feedback:\n"
            f"{feedback or '(no detail was reported; inspect the existing driver)'}\n\n"
            "Fix the reported failures while preserving behavior that already "
            "passes.\n"
            "Steps:\n"
            "  1. read_file driver_from_scratch.py — find the cause of each failure.\n"
            "  2. Fix MuJoCo bindings, request handling, control, or observations.\n"
            "     Keep the public API and the no-skeleton-import rule.\n"
            f"  3. {verification_tool} to use the prepared-scene handoff and\n"
            "     re-run the failing behavior in a disposable scene probe;\n"
            "     confirm it works before finishing.\n"
            "  4. write_file the corrected driver_from_scratch.py.\n"
            "Do not read validate_report.json, private validation suites, score "
            "or reference control implementations; the feedback above is the "
            "complete validation signal for this repair."
        )
        user += _actual_mjcf_context(self.cfg)
        user += _current_design_context(self.capability_design)
        user += _scene_handoff_context(
            self.probe_scenes_path,
            driver_name="driver_from_scratch.py",
            from_scratch=True,
        )
        return self._run_loop(
            name=f"03_gen_repair_{int(attempt)}", system=system,
            user_msg=user, tools=tools,
            max_iters=min(22, max(1, int(self.cfg.max_iters_gen_repair))),
        )

    # ─── Framework validation (no LLM) ────────────────────────────────────

    def _validate_from_scratch_driver(self) -> dict:
        """Run the current DESIGN cases against the real scratch driver."""
        self._require_capability_design("VALIDATE")
        if self.scene_cases_path is None or not self.scene_paths:
            return {
                "tests": [],
                "all_ok": False,
                "error": "validation requires DESIGN scene cases and prepared scenes",
                "validation_error": True,
                "repairable": False,
            }
        from .design_validation import validate_design_driver  # noqa: PLC0415

        return validate_design_driver(
            driver_path=self.workspace / "driver_from_scratch.py",
            design=self.capability_design,
            scene_cases_path=self.scene_cases_path,
            scene_paths=self.scene_paths,
            from_scratch=True,
            output_dir=self.workspace / "validation",
        )

    # ─── Top-level ────────────────────────────────────────────────────────

    def phase_export(self) -> PhaseResult:
        """Export the validated current driver through the selected contract."""
        artifact = self.workspace / "mcp_server.py"
        artifact.unlink(missing_ok=True)
        started = time.monotonic()

        self._require_capability_design("EXPORT")
        report_path = self.workspace / "validate_report.json"
        if not report_path.is_file():
            return PhaseResult(
                "04_export", False, 0.0,
                error="dynamic export requires a current validate_report.json",
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return PhaseResult(
                "04_export", False, 0.0,
                error=f"dynamic export cannot read validation report: {exc}",
            )
        if report.get("all_ok") is not True:
            return PhaseResult(
                "04_export", False, 0.0,
                error="dynamic export requires successful design validation",
            )
        try:
            system, user_msg = dynamic_export_prompt(
                robot_id=self.cfg.robot_id,
                driver_name="driver_from_scratch.py",
                design=self.capability_design,
            )
        except (TypeError, ValueError) as exc:
            return PhaseResult(
                "04_export", False, 0.0,
                error=f"dynamic export design surface is invalid: {exc}",
            )
        system = self._localize_prompt(system)
        tools = self._common_tools() + [self._local_runtime_tool()]

        result = self._run_loop(
            name="04_export", system=system, user_msg=user_msg,
            tools=tools, max_iters=max(1, int(self.cfg.max_iters_export)),
        )
        ok = bool(result.ok and artifact.is_file())
        phase = PhaseResult(
            "04_export", ok, time.monotonic() - started,
            trace_path=self.workspace / "traces/04_export.jsonl",
            artifact_paths=[artifact] if ok else [],
            final_text=result.final_text,
            error=None if ok else (
                result.error or "mcp_server.py was not written by the current export"
            ),
            token_usage=result.total_tokens,
        )
        if phase.ok:
            try:
                phase.metadata["dynamic_export_surface"] = validate_dynamic_export_source(
                    artifact, self.capability_design
                )
            except Exception as exc:  # noqa: BLE001 - generated surface is the gate
                phase.ok = False
                phase.artifact_paths = []
                phase.error = f"dynamic export surface check failed: {exc}"
                phase.metadata["dynamic_export_error"] = str(exc)
        return phase

    def phase_demo(self) -> PhaseResult:
        """Execute the configured ReCAP task with this run's scratch driver."""
        from .agent.recap import run_configured_demo

        report = run_configured_demo(
            workspace=self.workspace, robot_id=self.cfg.robot_id,
            export_server_path=self.workspace / "mcp_server.py",
            capability_design=self.capability_design,
            scene_cases_path=self.scene_cases_path, from_scratch=True,
            model=self.cfg.bedrock_model, provider=self.cfg.model_provider,
            region=self.cfg.aws_region, max_tokens=self.cfg.max_tokens_per_turn,
            demo_config_path=self.cfg.demo_config_path,
        )
        return PhaseResult(
            "05_demo", report.get("ok") is True, report.get("duration_sec", 0.0),
            trace_path=Path(report["trace_path"]) if report.get("trace_path") else None,
            artifact_paths=[Path(report[key]) for key in ("report_path", "video_path")
                            if report.get(key) and Path(report[key]).is_file()],
            final_text="ReCAP diagnostic demo; physical task success has not been evaluated.",
            error=report.get("error"), metadata=report,
        )

    def _write_summary(self, result: FromScratchResult) -> None:
        """Persist compact phase, evidence, and token records for this run."""
        root = self.workspace.resolve()

        def relative(value: Any) -> str | None:
            if value is None:
                return None
            path = Path(value)
            if not path.is_absolute():
                return str(path)
            try:
                return str(path.resolve().relative_to(root))
            except ValueError:
                return str(path)

        phases: list[dict[str, Any]] = []
        for raw in result.phases:
            phase = dict(raw) if isinstance(raw, Mapping) else {"name": str(raw)}
            if "trace_path" in phase:
                phase["trace_path"] = relative(phase.get("trace_path"))
            artifacts = phase.get("artifacts", phase.get("artifact_paths", []))
            if isinstance(artifacts, (list, tuple)):
                phase["artifacts"] = [relative(path) for path in artifacts]
            phases.append(phase)

        export_path = self.workspace / "mcp_server.py"
        summary = {
            "robot_id": result.robot_id,
            "workspace": str(root),
            "ok": bool(result.ok),
            "study_ok": bool(result.study_ok),
            "design_ok": bool(result.design_ok),
            "generation_ok": bool(result.gen_ok),
            "framework_ok": bool(result.validate_ok),
            "export_ok": bool(result.export_ok),
            "demo_ok": result.demo_ok,
            "driver_path": relative(result.driver_path),
            "export_path": relative(export_path) if export_path.is_file() else None,
            "total_duration_sec": result.total_duration_sec,
            "total_tokens": result.total_tokens,
            "error": result.error,
            "validate_report": result.validate_report,
            "phases": phases,
        }
        (root / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

        lines = [
            f"# From-scratch narrative — `{result.robot_id}`",
            "",
            f"- workspace: `{root}`",
            f"- overall: **{'OK' if result.ok else 'FAIL'}**",
            f"- tokens: `{result.total_tokens}`",
            "",
        ]
        for phase in phases:
            lines.append(
                f"## {phase.get('name', 'phase')} — "
                f"{'OK' if phase.get('ok') else 'FAIL'}"
            )
            if phase.get("error"):
                lines.append(f"- error: `{phase['error']}`")
            if phase.get("trace_path"):
                lines.append(f"- trace: `{phase['trace_path']}`")
            if phase.get("artifacts"):
                lines.append(
                    "- artifacts: "
                    + ", ".join(f"`{item}`" for item in phase["artifacts"])
                )
            for case in phase.get("validation_cases", []) or []:
                if not isinstance(case, Mapping):
                    continue
                measurements = case.get("measurements") or []
                metric_text = "; ".join(
                    f"{item.get('value')} {item.get('unit', '')} "
                    f"{item.get('comparator', '')} {item.get('threshold')}"
                    for item in measurements if isinstance(item, Mapping)
                ) or "no measurements"
                video = case.get("video") if isinstance(case.get("video"), Mapping) else {}
                lines.append(
                    f"- case `{case.get('case_id')}`: "
                    f"{'OK' if case.get('ok') else 'FAIL'}; {metric_text}; "
                    f"video=`{video.get('path') or 'none'}`"
                )
            lines.append("")
        (root / "narrative.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self, *, stop_after: str | None = None) -> FromScratchResult:
        """Run the current DESIGN through generation, validation and MCP export."""
        stages = ("study", "design", "generate", "validate", "export", "demo")
        if stop_after is not None and stop_after not in stages:
            raise ValueError(f"stop_after={stop_after!r} not in {stages}")
        started = time.monotonic()
        tokens = {"in": 0, "out": 0}
        phases: list[dict[str, Any]] = []
        driver_path = self.workspace / "driver_from_scratch.py"
        supplied_inputs = {
            Path(path).expanduser().resolve()
            for path in (self.cfg.capability_design_path, self.cfg.scene_cases_path)
            if path is not None
        }
        for relative in (
            "study.json", "driver_from_scratch.py", "validate_report.json",
            "mcp_server.py",
            "design/capability_design.json", "design/capability_preparation.json",
            "design/scene_cases.yaml", "design/probe_report.json",
            "design/probe_scenes.json",
        ):
            output = self.workspace / relative
            if output.resolve() not in supplied_inputs:
                output.unlink(missing_ok=True)
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self.probe_scenes_path = None
        study_ok = design_ok = gen_ok = validate_ok = export_ok = False
        demo_ok: bool | None = None
        report: dict[str, Any] = {}
        report_path = self.workspace / "validate_report.json"
        error: str | None = None
        stopped = False
        validation_recorded = False
        outer_attempts = 0
        for name in stages:
            if name == "validate" and validation_recorded:
                pass
            elif stopped:
                phases.append({"name": name, "ok": False,
                               "error": f"not run — stop_after={stop_after}"})
            elif error is not None:
                phases.append({"name": name, "ok": False,
                               "error": f"skipped — upstream phase failed: {error}"})
            elif name == "demo" and not self.cfg.enable_demo:
                phases.append({"name": name, "ok": False, "error": "not run — demo disabled"})
            elif name in {"study", "design"}:
                result = self.phase_study() if name == "study" else self.phase_design()
                for key in tokens:
                    tokens[key] += int(result.total_tokens.get(key, 0))
                if name == "study":
                    study_ok = bool(getattr(result, "ok", True)
                                    and (self.workspace / "study.json").is_file())
                    phase_ok = study_ok
                    missing = "study.json was not written by the current run"
                else:
                    design_ok = bool(result.ok and isinstance(self.capability_design, dict)
                                     and (self.workspace / "design/capability_design.json").is_file())
                    phase_ok = design_ok
                    missing = "capability design was not written by the current run"
                phase_error = None if phase_ok else (result.error or missing)
                phase = {"name": name, "ok": phase_ok, "error": phase_error}
                if name == "study":
                    trace = self.workspace / "traces" / "01_study.jsonl"
                    phase["trace_path"] = trace if trace.is_file() else None
                else:
                    phase["trace_path"] = (
                        self.workspace / "design" / "trace.jsonl"
                        if (self.workspace / "design" / "trace.jsonl").is_file()
                        else None
                    )
                    phase["artifacts"] = collect_design_artifacts(self.workspace)
                phases.append(phase)
                if not phase_ok:
                    error = phase_error
            elif name == "generate":
                for attempt in range(max(0, self.cfg.max_outer_retries) + 1):
                    before = driver_path.stat().st_mtime_ns if driver_path.is_file() else None
                    result = (self.phase_gen_algo() if attempt == 0 else
                              self.phase_gen_repair(validate_failure_feedback(report), attempt))
                    outer_attempts = attempt
                    for key in tokens:
                        tokens[key] += int(result.total_tokens.get(key, 0))
                    gen_ok = bool(
                        getattr(result, "ok", True) and driver_path.is_file()
                        and (before is None or driver_path.stat().st_mtime_ns != before)
                    )
                    generation_error = None if gen_ok else (
                        result.error or "GEN_ALGO did not write a current driver_from_scratch.py"
                    )
                    generation_phase = {
                        "name": "generate" if attempt == 0 else f"repair_{attempt}",
                        "ok": gen_ok, "error": generation_error,
                        "trace_path": self.workspace / "traces" / (
                            "02_gen_algo.jsonl" if attempt == 0
                            else f"03_gen_repair_{attempt}.jsonl"
                        ),
                        "artifacts": [driver_path] if gen_ok else [],
                    }
                    phases.append(generation_phase)
                    if not gen_ok:
                        error = generation_error
                        break
                    if stop_after == "generate":
                        break
                    report = self._validate_from_scratch_driver()
                    validate_ok = report.get("all_ok") is True
                    validation_recorded = True
                    report_path.write_text(
                        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8",
                    )
                    validation_phase = {
                        "name": "validate", "ok": validate_ok,
                        "error": report.get("error"),
                    }
                    validation_phase["artifacts"] = collect_validation_artifacts(
                        self.workspace, report, report_path
                    )
                    validation_phase["validation_cases"] = validation_case_metadata(
                        report, self.workspace
                    )
                    phases.append(validation_phase)
                    if validate_ok:
                        break
                    if not report.get("repairable", True) or attempt == max(0, self.cfg.max_outer_retries):
                        error = report.get("error") or "Framework validation failed"
                        break
                if validation_recorded:
                    report["outer_attempts"] = outer_attempts
                    report_path.write_text(
                        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8",
                    )
            else:
                result = self.phase_export() if name == "export" else self.phase_demo()
                for key in tokens:
                    tokens[key] += int(result.token_usage.get(key, 0))
                phases.append({
                    "name": name, "ok": result.ok, "error": result.error,
                    "duration_sec": result.duration_sec,
                    "trace_path": str(result.trace_path) if result.trace_path else None,
                    "artifact_paths": [str(path) for path in result.artifact_paths],
                    "metadata": result.metadata,
                })
                if name == "export":
                    export_ok = result.ok
                else:
                    demo_ok = result.ok
                if not result.ok:
                    error = result.error or f"{name} failed"
            if stop_after == name:
                stopped = True
        result = FromScratchResult(
            robot_id=self.cfg.robot_id, workspace=self.workspace,
            study_ok=study_ok, design_ok=design_ok, gen_ok=gen_ok, validate_ok=validate_ok,
            driver_path=driver_path if gen_ok else None, validate_report=report,
            total_duration_sec=time.monotonic() - started, total_tokens=tokens,
            error=error, phases=phases, ok=error is None,
            export_ok=export_ok, demo_ok=demo_ok,
        )
        self._write_summary(result)
        return result
