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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from autoadapter2.libraries import RobotPackage
from autoadapter2.react import (
    ReactLoopError,
    run_artifact_react,
)

from .interactive import (
    DriverDevelopmentConversation,
    PublicDevelopmentSession,
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

# File-producing phases use the bounded AutoAdapter-1 turn budgets.  The
# historical ``DRIVER_REACT_MAX_TURNS`` name remains as a compatibility alias;
# the interactive mainline does not use a global aggregate tool-call limit.
STUDY_REACT_MAX_TURNS = 16
STUDY_MAX_TURNS = STUDY_REACT_MAX_TURNS
GENERATE_SKELETON_MAX_TURNS = 22
GENERATE_SCRATCH_MAX_TURNS = 40
REPAIR_SKELETON_MAX_TURNS = 22
REPAIR_SCRATCH_MAX_TURNS = 20
DRIVER_REACT_MAX_TURNS = GENERATE_SKELETON_MAX_TURNS


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


STUDY_PROMPT = """You are the Auto-Adapter STUDY stage for a Direct-MuJoCo driver.
Study only the supplied public robot package, runtime contract, and eligible experience. This is a
pre-TGCD study: no capability design, task dispatch, private criteria, or whole-task request is
available or needed. Produce one concise JSON study record from the public morphology, source
records, canonical MuJoCo scene, and public Python primitives. Keep the same study procedure for
both eventual generation conditions; trusted skeleton source is unavailable in this phase.

You must request at least one bounded local development probe that loads the canonical public scene
and advances real MuJoCo physics with mujoco.mj_step. Each probe request must contain a short probe_id
and a complete Python script string. A probe may inspect the canonical public scene, run MuJoCo
physics, and print diagnostics. It may not read private validation data or claim a final validation
result. Return one JSON object with condition, findings, implementation_plan, and probe_requests."""


GENERATE_PROMPT = """You are the Auto-Adapter GENERATE stage. Using the supplied public package,
sealed capability design, STUDY record, and bounded local-probe facts, write exactly one executable
candidate driver. Return one JSON object containing driver_filename='driver.py', driver_source,
and a short generation_note.

The source must define build() and every exact public capability method name from the sealed design.
TGCD method names are arbitrary model-authored identifiers, not an effect-library selection. Preserve
each exact sealed-design name and implement its sealed public invocation ABI. Every capability method
must accept the exact ``request`` object declared by the sealed capability request_schema. Consume the
capability-native mapping directly; do not add task, scene, reset, private-criteria, or whole-task
fields, and do not invent a different public signature or task/effect allowlist. Define a driver class, make build()
return an instance of it, and define every capability as an instance method with the exact signature
``def <method_name>(self, request)``; top-level functions do not satisfy the ABI. ``request`` is a
plain dict; read only the fields declared by the sealed ABI and capability request schema.
Candidate imports are closed to __future__, math, json, typing, collections, dataclasses, numpy,
mujoco, and, only in skeleton-assisted mode, autoadapter2.trusted_skeletons.
Skeleton-assisted may import and use only the supplied trusted skeleton family. From-scratch must
not import, copy, or call any skeleton and must implement actuator mapping, control, and physics
stepping with the supplied runtime primitives. The Framework owns the canonical model/data session;
do not load a model, reset it, teleport state, or access private validation definitions. Do not return
a verdict or a fixed/reference driver in place of model-authored source. Use direct, statically
auditable syntax: mapping fields via subscripts and object APIs via normal attributes; do not use
getattr, setattr, eval, exec, or dynamic binding.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
)


STUDY_REACT_SYSTEM = """You are the interactive Auto-Adapter STUDY stage.
STUDY is identical across skeleton-assisted and from-scratch conditions: use only the supplied
public package projection, runtime contract, and eligible Experience. This is pre-TGCD, so no
capability design, task dispatch, private criteria, or whole-task request is available or needed.
The trusted skeleton is not available in this phase. Use read_file only for public inputs,
execute_python for one credential-free persistent public Python/MuJoCo session, and write_file for
the canonical study.json artifact in the condition workspace. Do not access private Harness data,
reference drivers, repository paths, network, or credentials.

Before finishing,
execute a real public probe that loads only
``mujoco.MjModel.from_xml_path(os.environ['AUTOADAPTER_PROBE_SCENE'])`` and advances physics with
``mujoco.mj_step``. Keep state across execute_python calls and keep code bounded. Write one JSON
object to study.json containing condition, non-empty findings, non-empty implementation_plan, and
at least one probe_requests entry with probe_id and script. Skeleton inspection is not part of
STUDY. When the file is complete, end the turn; the Framework validates study.json.

Use the complete supplied public inputs directly. Use execute_python to run a
focused real-physics probe against os.environ["AUTOADAPTER_PROBE_SCENE"], then write study.json
with non-empty findings, implementation_plan, and probe_requests. Do not write driver.py or inspect
skeletons. End the turn only after study.json is complete."""


_FIXED_STUDY_CONTEXT = """This run uses a predeclared capability design. Study the supplied public robot
package, runtime contract, eligible Experience, and sealed_capability_design, including its method
names, request_schema, and public criteria. Use these requirements to guide your implementation
plan and public physics probes. Do not redefine the capability interface or its criteria. Private
test cases and measurement bindings are unavailable in this phase."""

FIXED_STUDY_PROMPT = STUDY_PROMPT.replace(
    """Study only the supplied public robot package, runtime contract, and eligible experience. This is a
pre-TGCD study: no capability design, task dispatch, private criteria, or whole-task request is
available or needed.""",
    _FIXED_STUDY_CONTEXT,
)

FIXED_STUDY_REACT_SYSTEM = STUDY_REACT_SYSTEM.replace(
    """STUDY is identical across skeleton-assisted and from-scratch conditions: use only the supplied
public package projection, runtime contract, and eligible Experience. This is pre-TGCD, so no
capability design, task dispatch, private criteria, or whole-task request is available or needed.""",
    "STUDY is identical across skeleton-assisted and from-scratch conditions.\n" + _FIXED_STUDY_CONTEXT,
)


GENERATE_REACT_SYSTEM = """You are the interactive Auto-Adapter GENERATE/GEN_ALGO stage.
Use read_file and execute_python on the supplied public projection and use write_file to create the
canonical condition-workspace artifact driver.py. The interface-only stub, when present, is only a
starting point: replace every placeholder with a complete executable driver. Follow the sealed
invocation ABI and each capability's closed request_schema directly. Read only schema-declared fields;
do not add task, scene, reset, private-criteria, or whole-task fields. Candidate imports are closed
to __future__, math, json, typing, collections, dataclasses, numpy, mujoco, and, only in
skeleton-assisted mode, autoadapter2.trusted_skeletons. Skeleton-assisted may list_skeletons and
inspect_skeleton, while
from-scratch must not read or import skeleton source. Use one persistent credential-free public
Python/MuJoCo session for bounded development probes. Do not use private Harness definitions,
reference code, the other condition, credentials, or network. Finish by ending a turn once driver.py
is written; the Framework validates its source and public import/build boundary.""" + (
    "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
) + """

Develop the complete executable driver from the supplied public inputs.
Use write_file to write the complete source to driver.py, and use execute_python for any bounded
public checks needed while developing it. Skeleton discovery is available only in the
skeleton-assisted condition. Do not merely print source in a JSON answer; leave the valid canonical
driver.py artifact in the workspace and end the turn.
After submission, the Framework may append DRIVER_VALIDATION_FEEDBACK_JSON to this same conversation.
Continue editing the current driver using those failures; preserve behavior for passed checks.
Read the referenced public diagnostic files only when needed. The development Python session and
its budget persist across submissions. Reload changed driver code and rebuild development instances
before checking a revision; existing Python objects can still contain the old implementation."""


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


def _react_user_prompt(inputs: Mapping[str, Any]) -> str:
    return "PUBLIC_INPUT_JSON:\n" + json.dumps(
        dict(inputs), ensure_ascii=True, sort_keys=True
    )


def _artifact_turn_budget(stage: str, condition: GenerationCondition) -> int:
    if stage == "study":
        return STUDY_REACT_MAX_TURNS
    if stage == "generate":
        return (
            GENERATE_SKELETON_MAX_TURNS
            if condition == "skeleton-assisted"
            else GENERATE_SCRATCH_MAX_TURNS
        )
    if stage == "repair":
        return (
            REPAIR_SKELETON_MAX_TURNS
            if condition == "skeleton-assisted"
            else REPAIR_SCRATCH_MAX_TURNS
        )
    raise ValueError(f"unknown artifact stage {stage!r}")


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
    if not isinstance(raw, Mapping):
        raise GenerationError(
            "sealed capability design invocation_abi must be the closed capability-v2 object"
        )
    kind = raw.get("kind")
    if kind != "capability_request":
        raise GenerationError(f"unsupported sealed invocation ABI kind {kind!r}")
    return _copy(dict(raw))


def _public_invocation_facts(invocation_abi: Mapping[str, Any]) -> dict[str, Any]:
    facts = {
        **_copy(dict(invocation_abi)),
        "call_shape": "driver.<sealed_method_name>(request=request)",
        "method_names_are_copied_from_sealed_design": True,
        "effect_catalog_or_task_effect_allowlist": False,
    }
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
        "morphology_abi": {
            "source": "public_robot_package.morphology",
            "robot_symbols_and_actuator_mapping_must_come_from_morphology": True,
            "driver_must_not_invent_a_substitute_robot_model": True,
        },
    }
    if invocation_abi is not None:
        base["public_invocation_abi"] = _public_invocation_facts(invocation_abi)
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
    design: Mapping[str, Any] | None,
    *,
    condition: GenerationCondition | str,
    experience: Sequence[Mapping[str, Any]] = (),
    runtime_contract: Mapping[str, Any] | None = None,
    study_output: Mapping[str, Any] | None = None,
    probe_results: Sequence[Mapping[str, Any]] = (),
    include_condition_artifacts: bool = True,
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
    if selected_condition == "skeleton-assisted" and include_condition_artifacts:
        artifacts = {
            "kind": "trusted-skeleton-family",
            "import_root": "autoadapter2.trusted_skeletons",
            "source_files": _skeleton_sources(package),
            "inspection_required": True,
        }
    elif selected_condition == "skeleton-assisted":
        artifacts = {
            "kind": "study-neutral-public-projection",
            "skeleton_available": False,
        }
    else:
        artifacts = _from_scratch_artifacts(runtime_contract)

    inputs: dict[str, Any] = {
        "generation_condition": selected_condition,
        "public_robot_package": public_package,
        "eligible_experience": _copy(list(experience)),
        "allowed_runtime_facts": _runtime_facts(runtime_contract),
        "condition_eligible_artifacts": artifacts,
    }
    if design is not None:
        inputs["sealed_capability_design"] = _copy(dict(design))
        inputs["driver_interface_stub"] = render_interface_stub(_capability_methods(design))
        inputs["allowed_runtime_facts"] = _runtime_facts(
            runtime_contract,
            invocation_abi=_sealed_invocation_abi(design),
        )
    if study_output is not None:
        inputs["study"] = _copy(dict(study_output))
    inputs["probe_results"] = _copy(list(probe_results))
    return inputs


def _build_study_inputs(
    package: RobotPackage,
    design: Mapping[str, Any] | None,
    *,
    condition: GenerationCondition,
    experience: Sequence[Mapping[str, Any]],
    runtime_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the condition-neutral STUDY view.

    STUDY must exercise the same public workflow in both generation
    conditions.  Trusted skeleton source is therefore withheld here; it is
    only projected and discoverable during skeleton-assisted Generate/Repair.
    The requested generation condition is intentionally omitted from the model
    view; the Framework canonicalizes it on the resulting study artifact.
    """

    # Build the view from one fixed neutral branch.  Passing the requested
    # generation condition through this helper would leak a condition-specific
    # artifact description (and make the two STUDY conversations differ) even
    # though STUDY is deliberately shared.
    # New mainline callers pass no design before TGCD.  Keep the old Exp1a
    # fixed-bundle seam only for an explicitly sealed capability artifact;
    # arbitrary/unsealed mappings must never leak into pre-TGCD STUDY.
    study_design = (
        design
        if isinstance(design, Mapping)
        and design.get("artifact_type")
        in {"capability_design", "b1_fixed_capability_design"}
        and isinstance(design.get("invocation_abi"), Mapping)
        and design["invocation_abi"].get("kind") == "capability_request"
        else None
    )
    inputs = build_public_generation_inputs(
        package,
        study_design,
        condition="from-scratch",
        experience=experience,
        runtime_contract=runtime_contract,
        include_condition_artifacts=False,
    )
    inputs["generation_condition"] = "study"
    inputs["condition_eligible_artifacts"] = {
        "kind": "study-neutral-public-projection",
        "skeleton_available": False,
    }
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


def _validate_study(
    output: Mapping[str, Any],
    condition: GenerationCondition,
) -> tuple[dict[str, Any], ...]:
    if output.get("condition") != condition:
        raise GenerationError("STUDY output condition does not match the requested condition")
    for field in ("findings", "implementation_plan"):
        if field not in output:
            raise GenerationError(f"STUDY output is missing {field}")
    requests = _probe_requests(output)
    if not requests:
        raise GenerationError(
            "STUDY must request at least one real local MuJoCo development probe"
        )
    return requests


def study(
    client: JsonGenerator,
    package: RobotPackage,
    design: Mapping[str, Any] | None = None,
    *,
    condition: GenerationCondition | str,
    experience: Sequence[Mapping[str, Any]] = (),
    runtime_contract: Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None),
    source_root: str | Path | None = None,
) -> StudyResult:
    """Run the condition-neutral pre-TGCD public STUDY phase.

    ``design`` is optional for the new ordering.  A supplied design is retained
    only as a compatibility input for fixed-bundle callers; the normal
    pre-TGCD path passes ``None`` and therefore exposes no design to STUDY.
    """

    selected_condition = _require_condition(str(condition))
    inputs = _build_study_inputs(
        package,
        design,
        condition=selected_condition,
        experience=experience,
        runtime_contract=runtime_contract,
    )
    fixed_design = "sealed_capability_design" in inputs
    system_prompt = FIXED_STUDY_REACT_SYSTEM if fixed_design else STUDY_REACT_SYSTEM
    json_prompt = FIXED_STUDY_PROMPT if fixed_design else STUDY_PROMPT
    if _supports_react(client):
        if workspace is None:
            raise GenerationError("interactive STUDY requires a condition-local workspace")
        session = PublicDevelopmentSession(
            package=package,
            # STUDY is intentionally condition-neutral.  In particular, the
            # skeleton-assisted model must not gain a hidden import path here;
            # skeleton discovery is reserved for Generate/Repair.
            condition="from-scratch",
            # Keep the canonical artifact at the condition workspace root so
            # callers can hand the same ``study.json`` to Generate.
            workspace=Path(workspace).resolve(),
            budget=probe_budget,
            source_root=_source_root(source_root),
        )
        calls = getattr(client, "calls", ())
        start = len(calls) if isinstance(calls, Sequence) else 0

        study_path = session.workspace / "study.json"

        def validate_study_file(path: Path) -> dict[str, Any]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GenerationError(f"study.json is not valid JSON: {exc}") from exc
            if not isinstance(value, Mapping):
                raise GenerationError("study.json must contain one JSON object")
            if not session.has_successful_physics_probe():
                raise GenerationError(
                    "study.json requires a successful public execute_python MuJoCo probe"
                )
            canonical = dict(value)
            declared_condition = canonical.get("condition")
            if declared_condition != selected_condition:
                canonical["model_declared_condition"] = declared_condition
                canonical["condition"] = selected_condition
            _validate_study(
                canonical,
                selected_condition,
            )
            return _copy(canonical)

        try:
            react_result = run_artifact_react(
                client=client,
                stage="study",
                system_prompt=system_prompt,
                user_prompt=_react_user_prompt(inputs),
                tools=session.artifact_tools(include_skeleton=False),
                artifact_name="study.json",
                artifact_path=study_path,
                validate_artifact=validate_study_file,
                max_turns=_artifact_turn_budget("study", selected_condition),
            )
        except ReactLoopError as exc:
            raise GenerationError(
                f"interactive STUDY did not produce a valid study.json: {exc}",
                react_trace=exc.trace,
                probe_results=session.probe_results,
                model_turns=exc.model_turns,
                tool_calls=exc.tool_calls,
            ) from exc
        finally:
            interactive_probe_results = tuple(
                _copy(dict(item)) for item in session.probe_results
            )
            session.close()
        if not isinstance(react_result.artifact, Mapping):
            raise GenerationError("study.json must contain one study object")
        output = _copy(dict(react_result.artifact))
        evidence = _react_evidence(
            client,
            start=start,
            stage="study",
            prompt=system_prompt,
            inputs=inputs,
            output=output,
            trace=react_result.trace,
        )
    else:
        output, evidence = _invoke(client, stage="study", prompt=json_prompt, inputs=inputs)
        interactive_probe_results = ()
    declared_condition = output.get("condition")
    if declared_condition != selected_condition:
        output["model_declared_condition"] = declared_condition
        output["condition"] = selected_condition
    requests = _validate_study(
        output,
        selected_condition,
    )
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
    probe_budget: ProbeBudget = ProbeBudget(max_requests=None),
    source_root: str | Path | None = None,
    development: DriverDevelopmentConversation | None = None,
    max_turns: int | None = None,
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
            workspace=Path(workspace).resolve(),
            budget=probe_budget,
            source_root=_source_root(source_root),
            capability_methods=capability_methods,
            seed_interface_stub=True,
        )
        if development is not None:
            development.session = session
        calls = getattr(client, "calls", ())
        start = len(calls) if isinstance(calls, Sequence) else 0

        driver_path = session.workspace / "driver.py"

        def validate_driver_file(path: Path) -> dict[str, Any]:
            try:
                driver_source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise GenerationError(f"driver.py cannot be read: {exc}") from exc
            if not driver_source.strip():
                raise GenerationError("driver.py is empty")
            try:
                source_audit = audit_driver_source(
                    driver_source,
                    condition=selected_condition,
                    capability_methods=capability_methods,
                    candidate_request_boundary=True,
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
                raise GenerationError(f"driver.py source boundary failed: {exc}") from exc
            import_result = session.validate_driver_artifact(path)
            return {
                "driver_filename": "driver.py",
                "driver_source": driver_source,
                "source_audit": asdict(source_audit),
                "import": import_result.get("import", {}),
            }

        try:
            react_result = run_artifact_react(
                client=client,
                stage="generate",
                system_prompt=GENERATE_REACT_SYSTEM,
                user_prompt=_react_user_prompt(inputs),
                tools=session.artifact_tools(
                    include_skeleton=selected_condition == "skeleton-assisted"
                ),
                artifact_name="driver.py",
                artifact_path=driver_path,
                validate_artifact=validate_driver_file,
                max_turns=max_turns if max_turns is not None else _artifact_turn_budget("generate", selected_condition),
                conversation=development.messages if development is not None else None,
            )
        except ReactLoopError as exc:
            raise GenerationError(
                f"interactive GENERATE did not produce a valid driver.py: {exc}",
                react_trace=exc.trace,
                probe_results=session.probe_results,
                candidate_path=session.candidate_path,
                model_turns=exc.model_turns,
                tool_calls=exc.tool_calls,
            ) from exc
        finally:
            interactive_probe_results = tuple(
                _copy(dict(item)) for item in session.probe_results
            )
            if development is None:
                session.close()
        if not isinstance(react_result.artifact, Mapping):
            raise GenerationError("driver.py validation did not return an artifact record")
        output = _copy(dict(react_result.artifact))
        output.setdefault("generation_note", "")
        evidence = _react_evidence(
            client,
            start=start,
            stage="generate",
            prompt=GENERATE_REACT_SYSTEM,
            inputs=inputs,
            output=output,
            trace=react_result.trace,
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
            candidate_request_boundary=True,
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
    "GENERATE_SCRATCH_MAX_TURNS",
    "GENERATE_SKELETON_MAX_TURNS",
    "DriverSourceAuditError",
    "GenerationCondition",
    "GenerationError",
    "GenerationResult",
    "JsonGenerator",
    "ModelCallEvidence",
    "REPAIR_SCRATCH_MAX_TURNS",
    "REPAIR_SKELETON_MAX_TURNS",
    "STUDY_MAX_TURNS",
    "STUDY_PROMPT",
    "STUDY_REACT_SYSTEM",
    "GENERATE_REACT_SYSTEM",
    "StudyResult",
    "build_public_generation_inputs",
    "generate",
    "study",
]
