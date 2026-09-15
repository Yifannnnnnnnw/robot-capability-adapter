# SPDX-License-Identifier: Apache-2.0
"""SelfAssemble: driver generation, validation, export, and optional ReCAP demo.

Implements DESIGN.md §0.7 (3-layer architecture) + §0.9 (ReAct loop spec).

Pipeline:
    STUDY     — parse MJCF, build a capability map (joints / dof / class)
    DESIGN    — prepare or load this robot's task-grounded capabilities
    GENERATE  — pick a skeleton, fill its Spec, write driver.py
    VALIDATE  — execute the design's cases in fresh MuJoCo workers
    EXPORT    — generate an MCP server stub exposing validated skills
    DEMO      — optional configured local ReCAP task and video

The orchestrator owns the local workspace under `<workspace_root>/<robot_id>/`,
phase ordering, model budgets, repair and result records.

Agent phases use `ReactLoop.run()` with phase-specific prompts and tools;
the configured demo uses the ReCAP controller. Phase output is recorded as an artifact
saved under `<workspace>/<artifact_name>`. A failing phase short-circuits the
rest of the pipeline; partial results are still returned.
"""
from __future__ import annotations

import json
import os
import re
import shutil
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
from .robot_catalog import (
    REPO_ROOT, SKELETON_FOR_CLASS, find_robot_definition,
    capability_generation_context,
)
from .agent.tools import (
    make_inspect_skeleton_tool,
    make_list_skeletons_tool,
    make_local_exec_tool,
    make_read_file_tool,
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
    # All generated code, validation and optional ReCAP execution are local.
    mode: str = "local"
    aws_region: str = "us-east-1"
    bedrock_model: str = "us.anthropic.claude-sonnet-4-6"
    model_provider: str = "holistic"

    # Iteration caps for the model-driven stages; VALIDATE is deterministic.
    max_iters_study: int = 16
    max_iters_generate: int = 22
    max_iters_export: int = 8
    enable_demo: bool = False
    demo_config_path: Path | None = None

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

    # Every run prepares a task-grounded design after STUDY, or loads one.
    capability_design_path: Path | None = None
    scene_cases_path: Path | None = None
    max_iters_capability_design: int = 30

    def __post_init__(self) -> None:
        if self.scene_cases_path is not None and self.capability_design_path is None:
            raise ValueError(
                "scene_cases_path requires capability_design_path"
            )
        if self.mode != "local":
            raise ValueError("task-grounded capability preparation is local-only")
        if (isinstance(self.max_iters_capability_design, bool)
                or not isinstance(self.max_iters_capability_design, int)
                or self.max_iters_capability_design < 1):
            raise ValueError("max_iters_capability_design must be a positive integer")


def _actual_mjcf_context(cfg: Any) -> str:
    return (
        f"\nActual MJCF source: {Path(cfg.mjcf_path).resolve()}\n"
        "Resolve the workspace symlink before locating relative includes "
        "and meshes; inspect this model directory rather than searching the filesystem."
    )


def _scene_handoff_context(
    probe_scenes_path: Path | None,
    *,
    driver_name: str,
    from_scratch: bool = False,
) -> str:
    """Describe the public prepared-scene handoff and its disposable probe."""

    if probe_scenes_path is None:
        return (
            "\n\nNo prepared DESIGN scene handoff is available for this run. "
            "Do not search old artifacts or old scene files. Use the base "
            "mjcf.xml only for robot structure checks and state explicitly that "
            "no prepared scene is available for an environment probe.\n"
        )
    driver_literal = driver_name.replace("'", "\\'")
    scratch_literal = ", from_scratch=True" if from_scratch else ""
    return f"""

PUBLIC PREPARED DESIGN SCENE HANDOFF:
  Read this file with read_file before doing environment probes:
  {probe_scenes_path}
  (workspace-relative path: design/probe_scenes.json)
  It contains only an environments list. Each entry has scene_id,
  capability_id, scene_path (the actual prepared scene XML), and initial_state
  (robot keyframe/qpos, free_bodies, ctrl, settle). It intentionally has no
  case IDs, requests, measurements, execution limits, scoring, or reference
  controls. Choose the request from the current public capability contract.

For each probe, choose both chosen_scene_id and chosen_capability_id from the
handoff/design, then select the matching entry. Do not default to
environments[0]. For contact or push behavior, inspect/select the prepared
scene XML whose contents contain the corresponding physical object; the base
mjcf.xml is for robot structure checks only.

Use a disposable directory so the probe never changes the candidate in place:
```python
from pathlib import Path
import json
import tempfile
from auto_adapter.scene_runtime import build_scene_driver, reset_scene_driver

handoff = json.loads(Path("design/probe_scenes.json").read_text())
chosen_scene_id = "<scene id selected from the handoff>"
chosen_capability_id = "<capability id from the public design>"
env = next(item for item in handoff["environments"]
           if item["scene_id"] == chosen_scene_id
           and item["capability_id"] == chosen_capability_id)
probe_dir = Path(tempfile.mkdtemp(dir=Path.cwd()))
driver_path = Path("{driver_literal}").resolve()
robot = build_scene_driver(driver_path, env["scene_path"],
                           work_dir=probe_dir{scratch_literal})
reset_scene_driver(robot, env["initial_state"])
model = getattr(robot, "model", getattr(robot, "_model", None))
data = getattr(robot, "data", getattr(robot, "_data", None))
print(model.nbody, data.time)
# Select a request from the public capability contract and call the method.
```
The helper builds a copy in the disposable directory; edit only the original
{driver_name}. Its use is limited to local_exec or a fresh validator worker.
Never read a private suite or reference driver.
"""


def _trusted_skeleton_context(robot: dict | None, expected_class: str | None) -> str:
    """Return a compact public low-level skeleton summary for TGCD.

    DESIGN may use this to avoid authoring capabilities that the selected
    implementation route cannot expose. It receives names/signatures only;
    generation then implements the resulting current capability design.
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
    """Aggregate result across the requested stages."""

    robot_id: str
    workspace: Path
    phases: list[PhaseResult]
    ok: bool  # Requested stages succeeded, including a final successful repair.

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
        """Whether the final recorded design validation passed."""
        validations = [p for p in self.phases if p.name in {"validate", "03_validate"}]
        return bool(validations and validations[-1].ok)

    @property
    def stage1_ok(self) -> bool:
        """Whether STUDY, DESIGN, generation and validation all succeeded."""
        return bool(self.phases and self.phases[0].name in {"study", "01_study"}
                    and self.phases[0].ok
                    and any(p.name == "design" and p.ok for p in self.phases)
                    and self.generation_ok
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
  from the public study/design; ignore unrelated scene entities in the robot
  binding layer, while using the supplied scene object for a public contact or
  push probe when the capability requires it. ``build()`` must load the
  supplied ``mjcf.xml`` path and must not depend on cached initial-state files.
  For a local_exec scene probe, use the public ``design/probe_scenes.json``
  handoff and ``build_scene_driver`` rather than building against base
  ``mjcf.xml``. Initialise any target from the current model/data on every call.
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


# ──────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────


class SelfAssemble:
    """Local six-stage orchestrator. The public entry supports a context manager:

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
        # Select this run's design after STUDY and retain the caller's MJCF.
        self.capability_design = None
        self.scene_cases_path: Path | None = None
        self.scene_paths: dict[str, Path] = {}
        self.probe_scenes_path: Path | None = None
        self._last_capability_preparation: dict | None = None
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

    # ─── Context manager ─────────────────────────────────────────────────

    def __enter__(self) -> "SelfAssemble":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
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
        scene_roots = {
            Path(path).resolve().parent for path in self.scene_paths.values()
        }
        return [skel_root, mjcf_src_dir, *sorted(scene_roots, key=str)]

    def _local_tools(self) -> list[ToolSpec]:
        return [
            make_write_file_tool(self.workspace),
            make_read_file_tool(self.workspace, extra_roots=self._read_extra_roots()),
        ]

    def _skeleton_tools(self) -> list[ToolSpec]:
        return [make_list_skeletons_tool(), make_inspect_skeleton_tool()]


    def _local_runtime_tools(self) -> list[ToolSpec]:
        """Run generated code and MuJoCo probes in the current workspace."""
        from . import skeletons as _sk  # noqa: PLC0415

        repo_root = Path(_sk.__file__).resolve().parents[2]  # vector-os-nano/
        return [
            make_local_exec_tool(
                self.workspace, python_path_prepend=[repo_root]
            )
        ]

    def _runtime_tools(self) -> list[ToolSpec]:
        return self._local_runtime_tools()

    # ─── Task-grounded capability preparation ────────────────────────────

    def _require_capability_design(self, phase: str) -> None:
        if not isinstance(self.capability_design, dict):
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
                if supplied_path != design_output.resolve():
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
                raise ValueError(
                    "automatic capability preparation did not produce scene cases"
                )
            self.scene_cases_path = None
            self.scene_paths = {}
            preparation.pop("probe_scenes_path", None)
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
        probe_path = export_probe_scenes(
            suite=suite,
            scene_paths=paths,
            output_path=output_dir / "probe_scenes.json",
        )
        self.probe_scenes_path = probe_path
        preparation["probe_scenes_path"] = str(probe_path)

    def _phase_design(self) -> PhaseResult:
        """Prepare or load the capability design after a successful STUDY."""
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self.probe_scenes_path = None
        self._last_capability_preparation = None
        started = time.time()
        try:
            study_path = self.workspace / "study.json"
            if not study_path.is_file():
                raise FileNotFoundError("study.json missing after STUDY")
            study = json.loads(study_path.read_text(encoding="utf-8"))
            preparation = self._prepare_capability_design(study)
            design_trace = self.workspace / "design" / "trace.jsonl"
            design_artifacts = collect_design_artifacts(self.workspace)
            return PhaseResult(
                name="design", ok=True, duration_sec=max(0.0, time.time() - started),
                trace_path=design_trace if design_trace.is_file() else None,
                artifact_paths=design_artifacts,
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
                    "probe_scenes_path": (
                        str(self.probe_scenes_path)
                        if self.probe_scenes_path else None
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.capability_design = None
            self.scene_cases_path = None
            self.scene_paths = {}
            self.probe_scenes_path = None
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
            if current_write:
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
        self.capability_design = None
        self.scene_cases_path = None
        self.scene_paths = {}
        self.probe_scenes_path = None
        self._last_capability_preparation = None
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
        system = _CAPABILITY_GENERATE_SYSTEM
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
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"study.json is in the workspace. MJCF is at {self.mjcf_workspace_path}.\n"
            "Produce driver.py per the procedure."
        )
        user_msg += _actual_mjcf_context(self.cfg)
        user_msg += capability_generation_context(
            self.robot_definition, design=self.capability_design
        )
        user_msg += _scene_handoff_context(
            self.probe_scenes_path,
            driver_name="driver.py",
        )
        if prior_validate_failures:
            verification_tool = "local_exec"
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
        system = _CAPABILITY_GENERATE_SYSTEM
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

        verification_tool = "local_exec"
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
        user_msg += _scene_handoff_context(
            self.probe_scenes_path,
            driver_name="driver.py",
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
            artifact_paths=collect_validation_artifacts(self.workspace, report, report_path),
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
                "validation_cases": validation_case_metadata(report, self.workspace),
            },
        )


    def _phase_export(self, *, allow_failed_validation: bool = False) -> PhaseResult:
        # EXPORT also needs local_exec so the agent can syntax-check the
        # generated mcp_server.py via a one-shot `python -c "import ..."`.
        (self.workspace / "mcp_server.py").unlink(missing_ok=True)
        tools = self._local_tools() + self._runtime_tools()
        self._require_capability_design("EXPORT")
        report_path = self.workspace / "validate_report.json"
        if not report_path.is_file():
            return PhaseResult(
                name="04_export", ok=False, duration_sec=0.0,
                error="dynamic export requires a current validate_report.json",
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return PhaseResult(
                name="04_export", ok=False, duration_sec=0.0,
                error=f"dynamic export cannot read validation report: {exc}",
            )
        if report.get("all_ok") is not True and not allow_failed_validation:
            return PhaseResult(
                name="04_export", ok=False, duration_sec=0.0,
                error="dynamic export requires successful design validation",
            )
        try:
            system, user_msg = dynamic_export_prompt(
                robot_id=self.cfg.robot_id,
                driver_name="driver.py",
                design=self.capability_design,
            )
        except (TypeError, ValueError) as exc:
            return PhaseResult(
                name="04_export", ok=False, duration_sec=0.0,
                error=f"dynamic export design surface is invalid: {exc}",
            )
        phase = self._run_phase(
            name="04_export",
            system=system,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_export,
            expected_artifacts=["mcp_server.py"],
        )
        phase.metadata["allow_failed_validation"] = allow_failed_validation
        phase.metadata["source_validation_all_ok"] = report.get("all_ok") is True
        if phase.ok:
            try:
                phase.metadata["dynamic_export_surface"] = validate_dynamic_export_source(
                    self.workspace / "mcp_server.py", self.capability_design
                )
            except Exception as exc:  # noqa: BLE001 - generated surface is the gate
                phase.ok = False
                phase.error = f"dynamic export surface check failed: {exc}"
                phase.metadata["dynamic_export_error"] = str(exc)
        return phase

    def _phase_demo(self) -> PhaseResult:
        if self.cfg.mode != "local":
            return PhaseResult("05_demo", False, 0.0,
                               error="configured ReCAP demo requires local mode")
        return self._phase_recap_demo()

    def _phase_recap_demo(self) -> PhaseResult:
        """Use this run's design and validation for the configured demo."""
        from .agent.recap import run_configured_demo

        report = run_configured_demo(
            workspace=self.workspace, robot_id=self.cfg.robot_id,
            export_server_path=self.workspace / "mcp_server.py",
            capability_design=self.capability_design,
            scene_cases_path=self.scene_cases_path,
            model=self.cfg.bedrock_model, provider=self.cfg.model_provider,
            region=self.cfg.aws_region, max_tokens=self.cfg.max_tokens_per_turn,
            demo_config_path=self.cfg.demo_config_path,
        )
        return PhaseResult(
            name="05_demo", ok=report.get("ok") is True,
            duration_sec=report.get("duration_sec", 0.0),
            trace_path=Path(report["trace_path"]) if report.get("trace_path") else None,
            artifact_paths=[Path(report[key]) for key in (
                "report_path", "video_path", "evaluation_path", "physics_samples_path")
                            if report.get(key) and Path(report[key]).is_file()],
            final_text=(f"ReCAP diagnostic demo: execution_ok={report.get('execution_ok')}; "
                        f"physical_task_success={report.get('physical_task_success')}."),
            error=(report.get("error") or report.get("evaluation_error") or
                   ("fixed DEMO physical task criteria failed"
                    if report.get("physical_task_success") is False else None)), metadata=report,
        )

    # ─── Top-level entrypoint ─────────────────────────────────────────────

    PHASES = ("study", "design", "generate", "validate", "export", "demo")

    def _finish_result(
        self,
        results: list[PhaseResult],
        *,
        ok_override: bool | None = None,
    ) -> SelfAssembleResult:
        """Persist the aggregate result and narrative for the requested stages."""
        # Intentional stops and a disabled demo do not fail the requested run.
        executed = [
            phase for phase in results
            if not (phase.error or "").startswith("not run — ")
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

    def run(self, *, stop_after: Optional[str] = None) -> SelfAssembleResult:
        """Run DESIGN, generation, validation, export and optional ReCAP demo."""
        phases = self.PHASES
        if stop_after is not None and stop_after not in phases:
            raise ValueError(f"stop_after={stop_after!r} not in {phases}")
        supplied_inputs = {
            Path(path).expanduser().resolve()
            for path in (self.cfg.capability_design_path, self.cfg.scene_cases_path)
            if path is not None
        }
        # Keep the existing current-output boundary for generated designs.
        for relative in (
            "study.json", "driver.py", "validate_report.json", "mcp_server.py",
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
        methods = {
            "study": self._phase_study, "design": self._phase_design,
            "export": self._phase_export, "demo": self._phase_demo,
        }
        results: list[PhaseResult] = []
        failed: str | None = None
        stopped = False
        validation_recorded = False
        for name in phases:
            if name == "validate" and validation_recorded:
                # Validation is recorded with each generation/repair attempt.
                pass
            elif stopped:
                results.append(PhaseResult(name, False, 0.0,
                    error=f"not run — stop_after={stop_after}"))
            elif failed is not None:
                results.append(PhaseResult(name, False, 0.0, error=failed))
            elif name == "demo" and not self.cfg.enable_demo:
                results.append(PhaseResult(name, False, 0.0,
                    error="not run — demo disabled"))
            elif name == "generate":
                feedback: str | None = None
                max_attempts = max(1, int(self.cfg.max_outer_gen_val_iters))
                for attempt in range(max_attempts):
                    generated = (self._phase_generate() if feedback is None
                                 else self._phase_repair(feedback, attempt))
                    generated.metadata = dict(generated.metadata or {})
                    generated.metadata["outer_gen_val_iters"] = attempt + 1
                    results.append(generated)
                    if not generated.ok:
                        failed = f"skipped — upstream generate failed: {generated.error}"
                        break
                    if stop_after == "generate":
                        break
                    validated = self._phase_validate()
                    results.append(validated)
                    validation_recorded = True
                    if validated.ok:
                        break
                    if (not validated.metadata.get("repairable", True)
                            or attempt + 1 == max_attempts):
                        failed = (f"skipped — validation failed after {attempt + 1} "
                                  f"attempts: {validated.error}")
                        break
                    feedback = self._summarise_validate_failures()
            else:
                result = methods[name]()
                if name == "study" and not (self.workspace / "study.json").is_file():
                    result.ok = False
                    result.error = result.error or "study.json was not written by the current run"
                if name == "design" and not (
                    isinstance(self.capability_design, dict)
                    and (self.workspace / "design/capability_design.json").is_file()
                ):
                    result.ok = False
                    result.error = result.error or "capability design was not written by the current run"
                results.append(result)
                if not result.ok:
                    failed = f"skipped — upstream {name} failed: {result.error}"
            if stop_after == name:
                stopped = True
        # Failed attempts remain visible, but a successful final repair owns
        # generation/validation outcome. Demo never feeds back into repair.
        return self._finish_result(results, ok_override=failed is None)

    def _write_narrative(self, result: "SelfAssembleResult") -> None:
        """Walk traces + artifacts + recordings → narrative.md timeline."""
        def relative(path: Path) -> str:
            try:
                return str(path.resolve().relative_to(self.workspace.resolve()))
            except ValueError:
                return str(path)

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
                lines.append(f"  - trace: `{relative(p.trace_path)}`  ({n_steps} trace entries)")
            if p.artifact_paths:
                lines.append(f"  - artifacts: " + ", ".join(f"`{relative(a)}`" for a in p.artifact_paths))
            for case in p.metadata.get("validation_cases", []):
                values = "; ".join(
                    f"{item.get('value')} {item.get('unit', '')} "
                    f"{item.get('comparator', '')} {item.get('threshold')}"
                    for item in case.get("measurements", [])
                )
                lines.append(
                    f"  - case `{case.get('case_id')}`: "
                    f"{'OK' if case.get('ok') else 'FAIL'}; {values}; "
                    f"video=`{case.get('video', {}).get('path') or 'none'}`"
                )
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


def run_stage1(*, robot_id: str, workspace_root: Path | str, model: str,
               max_repairs: int = 3, stop_after: str | None = None,
               enable_demo: bool = False, demo_config_path: Path | None = None) -> dict:
    """Run one zoo robot through the public DESIGN pipeline and requested stages."""
    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int) or not 0 <= max_repairs <= 3:
        raise ValueError("max_repairs must be an integer from 0 to 3")
    definition = find_robot_definition(robot_id)
    if not definition or definition.get("id") != robot_id:
        raise ValueError(f"robot is not present in the trusted zoo: {robot_id}")
    route = definition.get("generation_route", "skeleton")
    options = dict(robot_id=robot_id, mjcf_path=REPO_ROOT / definition["mjcf"],
                   workspace_root=Path(workspace_root).expanduser().resolve(),
                   bedrock_model=model, mode="local", enable_demo=enable_demo,
                   demo_config_path=demo_config_path)
    if route == "skeleton":
        runner = SelfAssemble(SelfAssembleConfig(
            **options, max_outer_gen_val_iters=max_repairs + 1))
    elif route == "from_scratch":
        from .orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator
        runner = FromScratchOrchestrator(FromScratchConfig(
            **options, max_outer_retries=max_repairs))
    else:
        raise ValueError(f"unsupported generation_route={route!r}")
    with runner:
        result = runner.run(stop_after=stop_after)
    payload = result.to_json()
    generation_ok = result.generation_ok if route == "skeleton" else result.gen_ok
    framework_ok = result.framework_ok if route == "skeleton" else result.validate_ok
    if route == "skeleton":
        stage1_ok = result.stage1_ok
        exported = [phase for phase in result.phases if phase.name in {"export", "04_export"}]
        demos = [phase for phase in result.phases if phase.name in {"demo", "05_demo"}
                 and not (phase.error or "").startswith("not run — ")]
        export_ok = bool(exported and exported[-1].ok)
        demo_ok = demos[-1].ok if demos else None
        errors = [phase.error for phase in result.phases if not phase.ok and phase.error
                  and not phase.error.startswith(("not run — ", "skipped — "))]
        error = errors[-1] if not result.ok and errors else None
    else:
        stage1_ok = bool(result.study_ok and result.design_ok and generation_ok and framework_ok)
        export_ok, demo_ok, error = result.export_ok, result.demo_ok, result.error
    payload.update(route=route, ok=result.ok, stop_after=stop_after,
                   workspace=str(result.workspace), generation_ok=generation_ok,
                   framework_ok=framework_ok, stage1_ok=stage1_ok,
                   export_ok=export_ok, demo_ok=demo_ok, error=error,
                   summary_path=str(runner.workspace / "summary.json"))
    return payload
