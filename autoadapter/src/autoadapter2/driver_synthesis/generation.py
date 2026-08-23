"""Model-authored STUDY and driver generation for the mainline.

This module owns the public information boundary for the two preserved AutoAdapter
1.0 generation conditions. It deliberately does not know the private validation
suite or issue a physical verdict. The real client uses a multi-turn tool loop;
the one-shot JSON path remains only for focused compatibility tests.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from autoadapter2.libraries import RobotPackage
from autoadapter2.react import ReactLoopError, ToolSpec, run_react

from .interactive import (
    PublicDevelopmentSession,
    capability_task_ids,
    render_interface_stub,
)
from .probe import (
    ProbeBudget,
    ProbeError,
    ProbeSourceError,
    audit_public_source,
    public_asset_closure_manifest,
)
from .source_check import DriverSourceAudit, DriverSourceError, audit_driver_source


GenerationCondition = Literal["skeleton-assisted", "from-scratch"]

STUDY_REACT_MAX_TURNS = 3
STUDY_REACT_MAX_TOOL_CALLS = 3
DRIVER_REACT_MAX_TURNS = 12
DRIVER_REACT_MAX_TOOL_CALLS = 24


class JsonGenerator(Protocol):
    """The small protocol implemented by the real model client and unit fakes."""

    def generate_json(
        self,
        *,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class GenerationError(RuntimeError):
    """Raised when a model-authored generation artifact is unusable."""

    def __init__(
        self,
        message: str,
        *,
        react_trace: Sequence[Mapping[str, Any]] = (),
        probe_results: Sequence[Mapping[str, Any]] = (),
        candidate_path: str | Path | None = None,
        model_turns: int = 0,
        tool_calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.react_trace = tuple(_copy(dict(item)) for item in react_trace)
        self.probe_results = tuple(_copy(dict(item)) for item in probe_results)
        self.candidate_path = (
            Path(candidate_path) if candidate_path is not None else None
        )
        self.model_turns = int(model_turns)
        self.tool_calls = int(tool_calls)


class DriverSourceAuditError(GenerationError):
    """A model returned driver source, but source audit rejected it pre-Harness."""

    def __init__(
        self,
        message: str,
        *,
        driver_source: str,
        model_output: Mapping[str, Any],
    ) -> None:
        super().__init__(message)
        self.driver_source = driver_source
        self.model_output = copy.deepcopy(dict(model_output))


IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT = """
RUNTIME FEEDBACK-LOOP CONTRACT (public and robot-agnostic):
Every capability that produces a dynamic robot effect must execute a bounded, state-dependent
control loop, either directly or through an allowed trusted-skeleton primitive. Each control cycle
must read fresh canonical model/data state, compute the next actuator command from that newest state
and the public request, write through data.ctrl, advance the same canonical MuJoCo session, then read
fresh state and correct again. A fixed waypoint sequence may be supervisory, but every waypoint must
be tracked from fresh observations; do not use one-read-many-write, sleep-only, pure polling, or
self-reported completion. Stop only on observed public convergence or a bounded timeout. Do not infer
hidden acceptance rules, private resets, expected trajectories, or privileged simulator state.
""".strip()


STUDY_PROMPT = """You are the AutoAdapter 1.0 STUDY stage for a Direct-MuJoCo driver.
Study only the supplied public robot package, sealed public capability design, runtime contract,
and eligible experience. Produce a concise JSON study record for this generation condition.
For skeleton-assisted, record which supplied trusted skeleton source symbols and primitives you
inspected. For from-scratch, plan a controller using only the supplied public MuJoCo, NumPy, and
Python primitives; no skeleton source is available in this condition.

TGCD method names are model-authored and must be copied exactly from sealed_capability_design; they
are not selected from an effect catalog. The public invocation ABI is sealed in that design. Study
how each method consumes its request according to invocation_abi and the capability's request_schema,
using public_smoke_request when supplied. Do not impose a task_id/task_parameters envelope on a
capability_request design.

You must request at least one bounded local development probe that loads the canonical public scene
and advances real MuJoCo physics with mujoco.mj_step. Each probe request must contain a short probe_id
and a complete Python script string. A probe may inspect the canonical public scene, run MuJoCo
physics, and print diagnostics. It may not read private validation data or claim a final validation
result. Return one JSON object with condition, findings, implementation_plan, probe_requests, and
(for skeleton-assisted) skeleton_inspection."""


GENERATE_PROMPT = """You are the AutoAdapter 1.0 GENERATE stage. Using the supplied public package,
sealed capability design, STUDY record, and bounded local-probe facts, write exactly one executable
candidate driver. Return one JSON object containing driver_filename='driver.py', driver_source,
and a short generation_note.

The source must define build() and every exact public capability method name from the sealed design.
TGCD method names are arbitrary model-authored identifiers, not an effect-library selection. Preserve
each exact sealed-design name and implement its sealed public invocation ABI. Every capability method
must accept a keyword-compatible ``request`` object. For keyword_request, use its declared
task_id/task_parameters envelope; for capability_request, consume the capability-native object
declared by that capability's request_schema. Use the sealed design and public Morphology ABI to
interpret it; do not invent a different public signature or task/effect allowlist. Define a driver class, make build()
return an instance of it, and define every capability as an instance method with the exact signature
``def <method_name>(self, request)``; top-level functions do not satisfy the ABI. ``request`` is a
plain dict; read only the fields declared by the sealed ABI and capability request schema.
Skeleton-assisted may import and use only the supplied trusted skeleton family. From-scratch must
not import, copy, or call any skeleton and must implement actuator mapping, control, and physics
stepping with the supplied runtime primitives. The Framework owns the canonical model/data session;
do not load a model, reset it, teleport state, or access private validation definitions. Do not return
a verdict or a fixed/reference driver in place of model-authored source. Use direct, statically
auditable syntax: mapping fields via subscripts and object APIs via normal attributes; do not use
getattr, setattr, eval, exec, or dynamic binding.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
)


STUDY_REACT_SYSTEM = """You are the interactive AutoAdapter 1.0 STUDY stage for one
Direct-MuJoCo generation condition. The initial public input already contains the complete public
robot-package projection, selected MJCF closure, sealed Capability Design, and every condition-
eligible skeleton source. Ground the implementation plan in the design's invocation_abi and each
capability's request_schema; a capability_request design does not use the legacy task envelope. Do
not spend remote turns listing or rereading those inputs. Your first
action must be one public-only MuJoCo probe intended to advance physics. Use no private Harness,
reference driver, repository path, network, or other condition artifact. In every probe, load the
only canonical scene with ``import os, mujoco`` and
``mujoco.MjModel.from_xml_path(os.environ["AUTOADAPTER_PROBE_SCENE"])``. Probe-only ``os.environ``
access is allowed. Never guess a relative scene path, search with ``sys``/filesystem introspection,
construct a fallback scene, or import the trusted skeleton inside this liveness probe; its source is
already inline for STUDY. Keep the probe to canonical load, ``MjData``, public-name inspection when
useful, and one or more ``mujoco.mj_step`` calls. If that first probe fails,
use exactly one recovery turn to correct and rerun it; otherwise do not probe again. As soon as a
probe succeeds, call submit_study with non-empty findings and a non-empty capability-by-capability
implementation_plan. The third turn is reserved only for submission or correction of a rejected
submission. In skeleton-assisted mode, submit a non-empty ``skeleton_inspection`` object as well.
Do not write the final driver in STUDY."""


GENERATE_REACT_SYSTEM = """You are the interactive AutoAdapter 1.0 GENERATE/GEN_ALGO stage.
The public input contains the complete interface-only driver.py stub derived from the sealed
Capability Design. It contains exact method names and (self, request) placeholders but no controller
or request interpretation. Implement the complete source yourself and follow the invocation ABI and
per-capability request schemas sealed in the design. ``request`` is always a plain Python mapping:
use item access such as ``request["task_parameters"]`` for sealed fields; never
``request.task_parameters``. For keyword_request, read ``request["task_id"]`` and
``request["task_parameters"]``; for capability_request, use item access for fields declared by its
request_schema. Use public files and at most three optional
MuJoCo development probes when genuinely needed. Your normal first action is one check_driver call
containing the complete source and exactly one ABI-conforming public request per sealed capability. The
Framework writes the source, audits it, imports/builds it, and runs all capability physics smokes in
that same tool execution. The returned public controls, state, and named-position observations are
development feedback only: a successful check proves ABI/import/physics liveness, not capability
behavior or private validation. Never call it capability success. Revise the complete source when
the observations expose a defect, then rerun check_driver. Finish only with submit_driver. Never
access private Harness definitions,
reference code, the other condition, or credentials, and never claim the final verdict.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
)

STUDY_REACT_TASK = """Use the complete supplied public inputs directly. First call
run_mujoco_probe with a focused real-physics check that loads only
os.environ["AUTOADAPTER_PROBE_SCENE"]. After a successful observation, call
submit_study on the next turn with non-empty grounded findings and a non-empty implementation plan.
Only when the first probe fails may you use the next turn for one corrected probe before submitting
on the reserved final turn. Do not list or reread staged files, write the driver, or merely print a
JSON answer."""

GENERATE_REACT_TASK = """Develop the complete executable driver from the supplied
driver_interface_stub. Do not spend a turn reading or separately writing driver.py. Call check_driver
with the complete implementation source and exactly one ABI-conforming public request for every sealed
capability. If that atomic write-and-check succeeds, call submit_driver on the next turn. If it fails,
revise the complete source from the returned public diagnostics and call check_driver again. A changed
source creates a new revision and invalidates the earlier check. Do not call separate
write/read/audit/import/per-capability smoke tools, and do not merely print source in a JSON answer."""


@dataclass(frozen=True)
class ModelCallEvidence:
    """Safe, replay-friendly evidence for one model-authored stage.

    ``inputs`` and ``output`` are the exact JSON objects sent to and returned by
    the generator.  ``client_calls`` contains any concise call records exposed by
    the configured client, such as provider/model/usage, and never contains a key.
    """

    stage: str
    prompt: str
    inputs: dict[str, Any]
    output: dict[str, Any]
    client_calls: tuple[dict[str, Any], ...] = ()
    react_trace: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StudyResult:
    condition: GenerationCondition
    output: dict[str, Any]
    probe_requests: tuple[dict[str, Any], ...]
    call_evidence: ModelCallEvidence
    probe_results: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class GenerationResult:
    condition: GenerationCondition
    attempt: int
    output: dict[str, Any]
    driver_source: str
    driver_path: Path
    source_audit: DriverSourceAudit
    study: StudyResult
    probe_results: tuple[dict[str, Any], ...]
    call_evidence: ModelCallEvidence


def _copy(value: Any) -> Any:
    """Keep model input/output records independent from caller-owned dictionaries."""

    return copy.deepcopy(value)


def _require_condition(condition: str) -> GenerationCondition:
    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise GenerationError(f"unknown generation condition {condition!r}")
    return condition  # type: ignore[return-value]


def _client_call_records(client: JsonGenerator, start: int) -> tuple[dict[str, Any], ...]:
    calls = getattr(client, "calls", ())
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        return ()
    records: list[dict[str, Any]] = []
    for value in calls[start:]:
        if isinstance(value, Mapping):
            records.append(_copy(dict(value)))
    return tuple(records)


def _invoke(
    client: JsonGenerator,
    *,
    stage: str,
    prompt: str,
    inputs: Mapping[str, Any],
) -> tuple[dict[str, Any], ModelCallEvidence]:
    calls = getattr(client, "calls", ())
    start = len(calls) if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)) else 0
    result = client.generate_json(stage=stage, prompt=prompt, inputs=inputs)
    if not isinstance(result, Mapping):
        raise GenerationError(f"{stage} model output must be one JSON object")
    output = _copy(dict(result))
    evidence = ModelCallEvidence(
        stage=stage,
        prompt=prompt,
        inputs=_copy(dict(inputs)),
        output=_copy(output),
        client_calls=_client_call_records(client, start),
    )
    return output, evidence


def _supports_react(client: Any) -> bool:
    return callable(getattr(client, "generate_tool_turn", None))


def _react_evidence(
    client: Any,
    *,
    start: int,
    stage: str,
    prompt: str,
    inputs: Mapping[str, Any],
    output: Mapping[str, Any],
    trace: Sequence[Mapping[str, Any]],
) -> ModelCallEvidence:
    return ModelCallEvidence(
        stage=stage,
        prompt=prompt,
        inputs=_copy(dict(inputs)),
        output=_copy(dict(output)),
        client_calls=_client_call_records(client, start),
        react_trace=tuple(_copy(dict(item)) for item in trace),
    )


def _react_user_prompt(prompt: str, inputs: Mapping[str, Any]) -> str:
    return (
        prompt
        + "\n\nPUBLIC_INPUT_JSON:\n"
        + json.dumps(dict(inputs), ensure_ascii=True, sort_keys=True)
    )


def _source_root(value: str | Path | None) -> Path:
    return (
        Path(value).resolve()
        if value is not None
        else Path(__file__).resolve().parents[2]
    )


def _skeleton_sources(package: RobotPackage) -> list[dict[str, str]]:
    if not package.skeleton_dir.is_dir():
        raise GenerationError("skeleton-assisted generation requires a skeleton directory")
    sources: list[dict[str, str]] = []
    runtime_modules: set[str] = set()
    for path in sorted(package.skeleton_dir.rglob("*.py")):
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            sources.append(
                {
                    "path": path.relative_to(package.skeleton_dir).as_posix(),
                    "source": source,
                }
            )
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                raise GenerationError(
                    f"trusted skeleton inventory is not valid Python: {path.name}"
                ) from exc
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and isinstance(node.module, str)
                    and node.module.startswith("autoadapter2.trusted_skeletons.")
                ):
                    runtime_modules.add(node.module)

    framework_source = _source_root(None)
    trusted_root = (framework_source / "autoadapter2" / "trusted_skeletons").resolve()
    for module in sorted(runtime_modules):
        relative = Path(*module.split(".")).with_suffix(".py")
        implementation = (framework_source / relative).resolve()
        try:
            implementation.relative_to(trusted_root)
        except ValueError as exc:
            raise GenerationError(
                f"trusted skeleton import escapes its source root: {module}"
            ) from exc
        if not implementation.is_file():
            raise GenerationError(
                f"trusted skeleton implementation is unavailable: {module}"
            )
        sources.append(
            {
                "path": f"runtime/{relative.as_posix()}",
                "source": implementation.read_text(encoding="utf-8"),
            }
        )
    if not sources:
        raise GenerationError("skeleton-assisted generation requires Python skeleton source")
    return sources


def _sealed_invocation_abi(design: Mapping[str, Any]) -> dict[str, Any]:
    raw = design.get("invocation_abi")
    if raw is None:
        # Compatibility for older focused fixtures. Audited TGCD artifacts carry
        # this envelope explicitly.
        return {
            "kind": "keyword_request",
            "method_call": "method(request=request)",
            "request_required": ["task_id", "task_parameters"],
        }
    if not isinstance(raw, Mapping):
        raise GenerationError("sealed capability design invocation_abi must be an object")
    kind = raw.get("kind")
    if kind not in {"keyword_request", "capability_request"}:
        raise GenerationError(f"unsupported sealed invocation ABI kind {kind!r}")
    return _copy(dict(raw))


def _public_invocation_facts(invocation_abi: Mapping[str, Any]) -> dict[str, Any]:
    facts = {
        **_copy(dict(invocation_abi)),
        "call_shape": "driver.<sealed_method_name>(request=request)",
        "method_names_are_copied_from_sealed_design": True,
        "effect_catalog_or_task_effect_allowlist": False,
    }
    if invocation_abi.get("kind") == "keyword_request":
        facts["request_schema"] = {
            "task_id": "string",
            "task_parameters": "object",
        }
    else:
        facts["request_schema_source"] = (
            "sealed_capability_design.capabilities[].request_schema"
        )
        facts["smoke_request_source"] = (
            "sealed_capability_design.capabilities[].public_smoke_request"
        )
    return facts


def _runtime_facts(
    runtime_contract: Mapping[str, Any] | None,
    *,
    invocation_abi: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sealed_abi = invocation_abi or _sealed_invocation_abi({})
    base: dict[str, Any] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "candidate_allowed_imports": [
            "mujoco",
            "numpy",
            "math",
            "json",
            "dataclasses",
            "typing",
            "collections",
        ],
        "candidate_forbidden_imports": [
            "os",
            "pathlib",
            "shutil",
            "subprocess",
            "inspect",
            "importlib",
            "socket",
            "urllib",
            "requests",
            "httpx",
        ],
        "canonical_session": {
            "model_and_data_are_framework_owned": True,
            "candidate_must_retain_supplied_objects": True,
            "candidate_must_not_load_or_reset_model": True,
        },
        "control": {
            "write_actuator_commands_to_data_ctrl": True,
            "advance_with_mujoco_physics": True,
        },
        "probe_environment": {
            "scene_env": "AUTOADAPTER_PROBE_SCENE",
            "public_package_env": "AUTOADAPTER_PROBE_PUBLIC_PACKAGE",
            "allowed_utility_imports": ["os", "pathlib"],
            "forbidden_utility_imports": ["sys", "glob", "shutil", "inspect"],
            "canonical_scene_loader": (
                "import os, mujoco; model = mujoco.MjModel.from_xml_path("
                "os.environ['AUTOADAPTER_PROBE_SCENE']); data = mujoco.MjData(model)"
            ),
            "relative_or_synthetic_scene_fallback_forbidden": True,
        },
        "public_invocation_abi": _public_invocation_facts(sealed_abi),
        "morphology_abi": {
            "source": "public_robot_package.morphology",
            "robot_symbols_and_actuator_mapping_must_come_from_morphology": True,
            "driver_must_not_invent_a_substitute_robot_model": True,
        },
    }
    if runtime_contract:
        # Only caller-supplied runtime facts are merged.  The condition-specific
        # artifact below is constructed separately so from-scratch never receives
        # a skeleton path or source through an opaque contract object.
        for key, value in runtime_contract.items():
            if key not in {
                "skeleton",
                "trusted_skeleton",
                "skeleton_path",
                "skeleton_source",
                "public_invocation_abi",
            }:
                base[str(key)] = _copy(value)
    return base


def _from_scratch_artifacts(runtime_contract: Mapping[str, Any] | None) -> dict[str, Any]:
    supplied = runtime_contract or {}
    primitives = supplied.get("primitives", [])
    if not isinstance(primitives, list):
        raise GenerationError("from-scratch runtime contract primitives must be a list")
    return {
        "kind": "from-scratch-runtime-contract",
        "primitives": _copy(primitives),
        "session_contract": {
            "build_receives_canonical_model_and_data": True,
            "driver_owns_no_scene_construction": True,
            "driver_owns_no_trial_reset": True,
        },
        "control_contract": {
            "actuator_input": "data.ctrl",
            "physics_step": "mujoco.mj_step(model, data)",
        },
        "skeleton_available": False,
    }


def build_public_generation_inputs(
    package: RobotPackage,
    design: Mapping[str, Any],
    *,
    condition: GenerationCondition | str,
    experience: Sequence[Mapping[str, Any]] = (),
    runtime_contract: Mapping[str, Any] | None = None,
    study_output: Mapping[str, Any] | None = None,
    probe_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the exact public context sent to STUDY/GENERATE/Repair.

    The representation contains relative public asset/source records rather than
    ``package.root`` or ``private_dir``.  The hidden suite is therefore not part of
    the model input by construction.
    """

    selected_condition = _require_condition(str(condition))
    try:
        asset_manifest = public_asset_closure_manifest(package)
    except ProbeError as exc:
        raise GenerationError(f"canonical public MJCF closure is invalid: {exc}") from exc
    public_package: dict[str, Any] = {
        "robot_configuration_id": package.robot_configuration_id,
        "package_version": package.package_version,
        "morphology": _copy(package.morphology),
        "task_library": {
            "snapshot_id": package.snapshot_id,
            "sources": _copy(list(package.sources)),
            "tasks": _copy(list(package.tasks)),
        },
        "selected_mjcf_closure": asset_manifest,
    }

    artifacts: dict[str, Any]
    if selected_condition == "skeleton-assisted":
        artifacts = {
            "kind": "trusted-skeleton-family",
            "import_root": "autoadapter2.trusted_skeletons",
            "source_files": _skeleton_sources(package),
            "inspection_required": True,
        }
    else:
        artifacts = _from_scratch_artifacts(runtime_contract)

    inputs: dict[str, Any] = {
        "generation_condition": selected_condition,
        "public_robot_package": public_package,
        "sealed_capability_design": _copy(dict(design)),
        "driver_interface_stub": render_interface_stub(_capability_methods(design)),
        "eligible_experience": _copy(list(experience)),
        "allowed_runtime_facts": _runtime_facts(
            runtime_contract,
            invocation_abi=_sealed_invocation_abi(design),
        ),
        "condition_eligible_artifacts": artifacts,
    }
    if study_output is not None:
        inputs["study"] = _copy(dict(study_output))
    inputs["probe_results"] = _copy(list(probe_results))
    return inputs


def _capability_methods(design: Mapping[str, Any]) -> tuple[str, ...]:
    capabilities = design.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise GenerationError("sealed capability design has no capabilities")
    names: list[str] = []
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, Mapping) or not isinstance(capability.get("method_name"), str):
            raise GenerationError(f"capabilities[{index}].method_name is required")
        names.append(str(capability["method_name"]))
    return tuple(names)


def _validate_public_invocation_abi(
    source: str,
    capability_methods: Sequence[str],
) -> None:
    """Require model-authored capability methods to implement request= ABI."""

    try:
        tree = ast.parse(source, filename="driver.py")
    except SyntaxError as exc:
        raise GenerationError(f"driver.py is not valid Python: {exc.msg}") from exc
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for method_name in capability_methods:
        function = functions.get(method_name)
        if function is None:
            raise GenerationError(f"driver.py does not define ABI method {method_name!r}")
        parameters = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        if (
            len(parameters) != 2
            or parameters[0].arg != "self"
            or parameters[1].arg != "request"
            or len(function.args.posonlyargs) > 1
            or function.args.vararg is not None
            or function.args.kwarg is not None
        ):
            raise GenerationError(
                f"capability method {method_name!r} must have signature (self, request)"
            )


def _probe_requests(output: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    requests = output.get("probe_requests", [])
    if requests is None:
        return ()
    if not isinstance(requests, list):
        raise GenerationError("study.probe_requests must be a list")
    normalized: list[dict[str, Any]] = []
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            raise GenerationError(f"probe_requests[{index}] must be an object")
        probe_id = request.get("probe_id")
        script = request.get("script")
        if not isinstance(probe_id, str) or not probe_id.strip():
            raise GenerationError(f"probe_requests[{index}].probe_id is required")
        if not isinstance(script, str) or not script.strip():
            raise GenerationError(f"probe_requests[{index}].script is required")
        normalized.append({"probe_id": probe_id.strip(), "script": script})
    return tuple(normalized)


def _validate_study(output: Mapping[str, Any], condition: GenerationCondition) -> tuple[dict[str, Any], ...]:
    if output.get("condition") != condition:
        raise GenerationError("STUDY output condition does not match the requested condition")
    for field in ("findings", "implementation_plan"):
        if field not in output:
            raise GenerationError(f"STUDY output is missing {field}")
    if condition == "skeleton-assisted":
        inspection = output.get("skeleton_inspection")
        if not isinstance(inspection, Mapping) or not inspection:
            raise GenerationError("skeleton-assisted STUDY must record skeleton_inspection")
    requests = _probe_requests(output)
    if not requests:
        raise GenerationError(
            "STUDY must request at least one real local MuJoCo development probe"
        )
    return requests


def study(
    client: JsonGenerator,
    package: RobotPackage,
    design: Mapping[str, Any],
    *,
    condition: GenerationCondition | str,
    experience: Sequence[Mapping[str, Any]] = (),
    runtime_contract: Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
    probe_budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
) -> StudyResult:
    """Run real-model STUDY with the condition-specific public boundary."""

    selected_condition = _require_condition(str(condition))
    inputs = build_public_generation_inputs(
        package,
        design,
        condition=selected_condition,
        experience=experience,
        runtime_contract=runtime_contract,
    )
    if _supports_react(client):
        if workspace is None:
            raise GenerationError("interactive STUDY requires a condition-local workspace")
        session = PublicDevelopmentSession(
            package=package,
            condition=selected_condition,
            workspace=Path(workspace).resolve() / "study-development",
            budget=probe_budget,
            source_root=_source_root(source_root),
        )

        def run_study_probe(arguments: Mapping[str, Any]) -> dict[str, Any]:
            if session.has_successful_physics_probe():
                raise GenerationError(
                    "STUDY physics requirement is already satisfied; call submit_study now"
                )
            result = session.run_mujoco_probe(arguments)
            if session.has_successful_physics_probe():
                return {
                    **result,
                    "study_requirement_satisfied": True,
                    "required_next_action": (
                        "Call submit_study now; do not run another probe."
                    ),
                }
            return {
                **result,
                "study_requirement_satisfied": False,
                "required_next_action": (
                    "The probe failed. Use the single recovery probe now; do not "
                    "submit until real MuJoCo physics advances successfully."
                ),
            }

        def decoded_container(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value

        def submit_study(arguments: Mapping[str, Any]) -> dict[str, Any]:
            findings = decoded_container(arguments.get("findings"))
            implementation_plan = decoded_container(
                arguments.get("implementation_plan")
            )
            if not isinstance(findings, list) or not findings:
                raise GenerationError("submit_study requires non-empty findings")
            if not isinstance(implementation_plan, list) or not implementation_plan:
                raise GenerationError("submit_study requires a non-empty implementation_plan")
            if not session.has_successful_physics_probe():
                raise GenerationError(
                    "STUDY requires a successful public probe with real MuJoCo physics steps"
                )
            submitted: dict[str, Any] = {
                "condition": selected_condition,
                "findings": _copy(findings),
                "implementation_plan": _copy(implementation_plan),
                "probe_requests": _copy(session.probe_requests),
            }
            inspection = decoded_container(arguments.get("skeleton_inspection"))
            if selected_condition == "skeleton-assisted":
                if not isinstance(inspection, Mapping) or not inspection:
                    raise GenerationError(
                        "skeleton-assisted STUDY requires skeleton_inspection"
                    )
                submitted["skeleton_inspection"] = _copy(dict(inspection))
            elif isinstance(inspection, Mapping) and inspection:
                submitted["skeleton_inspection"] = _copy(dict(inspection))
            return submitted

        submit_schema = {
            "type": "object",
            "properties": {
                "findings": {"type": "array", "items": {}, "minItems": 1},
                "implementation_plan": {
                    "type": "array",
                    "items": {},
                    "minItems": 1,
                },
                "skeleton_inspection": {"type": "object"},
            },
            "required": [
                "findings",
                "implementation_plan",
                *(
                    ["skeleton_inspection"]
                    if selected_condition == "skeleton-assisted"
                    else []
                ),
            ],
            "additionalProperties": False,
        }
        probe_tool = next(
            tool for tool in session.public_tools() if tool.name == "run_mujoco_probe"
        )
        tools = (
            replace(
                probe_tool,
                description=(
                    "Run a minimal STUDY liveness probe. Use only import os, mujoco; load "
                    "os.environ['AUTOADAPTER_PROBE_SCENE']; construct MjData; call "
                    "mujoco.mj_step at least once. Do not import the skeleton, sys, "
                    "importlib, or construct/search for another scene."
                ),
                handler=run_study_probe,
            ),
            ToolSpec(
                "submit_study",
                "Submit the condition-specific findings after at least one successful real-physics public probe.",
                submit_schema,
                submit_study,
                terminal=True,
            ),
        )
        calls = getattr(client, "calls", ())
        start = len(calls) if isinstance(calls, Sequence) else 0
        try:
            react_result = run_react(
                client=client,
                stage="study",
                system_prompt=STUDY_REACT_SYSTEM,
                user_prompt=_react_user_prompt(STUDY_REACT_TASK, inputs),
                tools=tools,
                max_turns=STUDY_REACT_MAX_TURNS,
                max_tool_calls=STUDY_REACT_MAX_TOOL_CALLS,
            )
        except ReactLoopError as exc:
            raise GenerationError(
                f"interactive STUDY did not submit: {exc}",
                react_trace=exc.trace,
                probe_results=session.probe_results,
                model_turns=exc.model_turns,
                tool_calls=exc.tool_calls,
            ) from exc
        if not isinstance(react_result.submission, Mapping):
            raise GenerationError("submit_study must return one study object")
        output = _copy(dict(react_result.submission))
        evidence = _react_evidence(
            client,
            start=start,
            stage="study",
            prompt=STUDY_REACT_SYSTEM,
            inputs=inputs,
            output=output,
            trace=react_result.trace,
        )
        interactive_probe_results = tuple(
            _copy(dict(item)) for item in session.probe_results
        )
    else:
        output, evidence = _invoke(client, stage="study", prompt=STUDY_PROMPT, inputs=inputs)
        interactive_probe_results = ()
    declared_condition = output.get("condition")
    if declared_condition != selected_condition:
        output["model_declared_condition"] = declared_condition
        output["condition"] = selected_condition
    requests = _validate_study(output, selected_condition)
    return StudyResult(
        condition=selected_condition,
        output=output,
        probe_requests=requests,
        call_evidence=evidence,
        probe_results=interactive_probe_results,
    )


def generate(
    client: JsonGenerator,
    package: RobotPackage,
    design: Mapping[str, Any],
    study_result: StudyResult | Mapping[str, Any],
    *,
    condition: GenerationCondition | str,
    workspace: str | Path,
    probe_results: Sequence[Mapping[str, Any]] = (),
    experience: Sequence[Mapping[str, Any]] = (),
    runtime_contract: Mapping[str, Any] | None = None,
    probe_budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
) -> GenerationResult:
    """Generate, audit, compile, and write one model-authored ``driver.py``.

    The function performs no Harness validation and never reads the private suite.
    ``probe_results`` are facts produced by :mod:`autoadapter2.driver_synthesis.probe`.
    """

    selected_condition = _require_condition(str(condition))
    if isinstance(study_result, StudyResult):
        study_output = study_result.output
        recorded_study = study_result
    elif isinstance(study_result, Mapping):
        study_output = dict(study_result)
        requests = _validate_study(study_output, selected_condition)
        recorded_study = StudyResult(
            condition=selected_condition,
            output=_copy(study_output),
            probe_requests=requests,
            call_evidence=ModelCallEvidence(
                stage="study",
                prompt="external study result",
                inputs={},
                output=_copy(study_output),
            ),
        )
    else:
        raise GenerationError("study_result must be StudyResult or a JSON object")
    if recorded_study.condition != selected_condition:
        raise GenerationError("study result condition does not match generation condition")

    inputs = build_public_generation_inputs(
        package,
        design,
        condition=selected_condition,
        experience=experience,
        runtime_contract=runtime_contract,
        study_output=study_output,
        probe_results=probe_results,
    )
    interactive_probe_results: tuple[dict[str, Any], ...] = ()
    if _supports_react(client):
        capability_methods = _capability_methods(design)
        session = PublicDevelopmentSession(
            package=package,
            condition=selected_condition,
            workspace=Path(workspace).resolve() / "generate-development",
            budget=probe_budget,
            source_root=_source_root(source_root),
            capability_methods=capability_methods,
            capability_task_ids=capability_task_ids(design),
            invocation_abi=_sealed_invocation_abi(design),
            seed_interface_stub=True,
        )
        calls = getattr(client, "calls", ())
        start = len(calls) if isinstance(calls, Sequence) else 0
        try:
            react_result = run_react(
                client=client,
                stage="generate",
                system_prompt=GENERATE_REACT_SYSTEM,
                user_prompt=_react_user_prompt(GENERATE_REACT_TASK, inputs),
                tools=session.driver_tools(),
                max_turns=DRIVER_REACT_MAX_TURNS,
                max_tool_calls=DRIVER_REACT_MAX_TOOL_CALLS,
            )
        except ReactLoopError as exc:
            raise GenerationError(
                f"interactive GENERATE did not submit: {exc}",
                react_trace=exc.trace,
                probe_results=session.probe_results,
                candidate_path=session.candidate_path,
                model_turns=exc.model_turns,
                tool_calls=exc.tool_calls,
            ) from exc
        if not isinstance(react_result.submission, Mapping):
            raise GenerationError("submit_driver must return one driver object")
        output = _copy(dict(react_result.submission))
        note = output.pop("note", "")
        output["generation_note"] = note
        evidence = _react_evidence(
            client,
            start=start,
            stage="generate",
            prompt=GENERATE_REACT_SYSTEM,
            inputs=inputs,
            output=output,
            trace=react_result.trace,
        )
        interactive_probe_results = tuple(
            _copy(dict(item)) for item in session.probe_results
        )
    else:
        output, evidence = _invoke(
            client,
            stage="generate",
            prompt=GENERATE_PROMPT,
            inputs=inputs,
        )
    driver_source = output.get("driver_source")
    if not isinstance(driver_source, str) or not driver_source.strip():
        raise GenerationError("GENERATE output must contain non-empty driver_source")
    if output.get("driver_filename", "driver.py") != "driver.py":
        raise GenerationError("GENERATE may write only driver.py")
    try:
        capability_methods = _capability_methods(design)
        source_audit = audit_driver_source(
            driver_source,
            condition=selected_condition,
            capability_methods=capability_methods,
        )
        _validate_public_invocation_abi(driver_source, capability_methods)
        audit_public_source(driver_source, condition=selected_condition)
        compile(driver_source, "driver.py", "exec")
    except (
        DriverSourceError,
        GenerationError,
        ProbeSourceError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise DriverSourceAuditError(
            f"generated driver failed source audit: {exc}",
            driver_source=driver_source,
            model_output=output,
        ) from exc

    destination = Path(workspace).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    driver_path = destination / "driver.py"
    driver_path.write_text(driver_source, encoding="utf-8")
    return GenerationResult(
        condition=selected_condition,
        attempt=0,
        output=output,
        driver_source=driver_source,
        driver_path=driver_path,
        source_audit=source_audit,
        study=recorded_study,
        probe_results=tuple(_copy(dict(item)) for item in probe_results)
        + interactive_probe_results,
        call_evidence=evidence,
    )


__all__ = [
    "GENERATE_PROMPT",
    "DriverSourceAuditError",
    "GenerationCondition",
    "GenerationError",
    "GenerationResult",
    "JsonGenerator",
    "ModelCallEvidence",
    "STUDY_PROMPT",
    "STUDY_REACT_SYSTEM",
    "GENERATE_REACT_SYSTEM",
    "StudyResult",
    "build_public_generation_inputs",
    "generate",
    "study",
]
