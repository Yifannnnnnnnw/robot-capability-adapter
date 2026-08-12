"""Small experiment-grade Stage 2 implementation loop.

It deliberately owns no validation semantics.  It gates on a non-sensitive
Blue Line authorization, accounts LLM and sandbox activity separately, and
seals only a submitted ``capability.py`` source artifact.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from ..blue_line import Stage2Authorization
from ..foundation.canonical import canonical_bytes
from ..foundation.errors import ContractError
from ..foundation.hashing import content_hash
from ..foundation.seals import create_seal
from ..generation.llm import JsonGenerator
from .binding import PythonBinding, derive_implementation_manifest, derive_python_binding, verify_capability_source
from .bundle import ImplementationBundle, validate_implementation_bundle
from .sandbox import CallbackSandbox, get_sandbox_contract


STAGE2_PROMPT = """
Return exactly one JSON action object and no Markdown or prose. The action must be one of:
{"action":"sandbox","capability.py":"<complete raw Python>","probe":<public object>},
{"action":"submit","capability.py":"<complete raw Python>"}, or
{"action":"blocked","reason":"<public reason>"}. Use exactly the fields shown for the
selected action. Never return a manifest, another file, diagnostics, private evaluation
data, or execution outcomes.

The capability.py string is one complete parseable Python module, with no Markdown fences,
backticks, explanation, or omission. It may contain an optional module docstring, safe
literal/numeric module constants, imports only `math`, `time`, and `numpy` (optionally as
`np`), private non-dunder helper functions, and exactly the bound public functions with
the signatures in binding_contract (`..., *, _sdk`). Do not add public functions/classes,
decorators, dynamic imports, eval/exec/open, dunder access, or invented attributes on
the injected `_sdk` facade.

Calls may use only approved math/time/numpy members, the small safe numeric builtins
(`range`, `len`, `min`, `max`, `abs`, `sum`, `enumerate`, `zip`, `float`, `int`, `bool`,
`str`, `list`, `tuple`, `dict`, `RuntimeError`, and related safe numeric helpers), module-local
helpers/bound functions, and the exact `_sdk` members listed in the implementation_bundle.
The bundle is authoritative for SDK types, factories, constructors, and operations: do not
invent endpoint attributes or endpoint instances on the injected facade.
Do not call `ChannelFactoryInitialize` or otherwise reinitialize global SDK communication.
When the bundle lists publisher/subscriber constructors, use them to create local endpoints,
call their listed `Init()`, and use their listed `Read()`/`Write()` operations; these local
endpoints are allowed and are not a second SDK connection. Pass `_sdk` explicitly to every
helper that uses it, or pass a locally constructed endpoint/message explicitly; never rely on
a module-global `_sdk`. When a bundle lists direct facade operations and no constructors, call
those operations directly and leave lifecycle to Framework. Keep the one-file binding contract
and public result fields exactly as supplied; physical behavior is assessed after submission.

INPUT_JSON includes a closed sandbox_contract and public submission_requirements. The Sandbox is
callback-only: use only varied public probes described by sandbox_contract and never request a
direct simulator or private evaluation handle. Revise the working source from public Sandbox feedback.
Do not submit before every submission requirement is met; a premature submit is
rejected with public diagnostics, while its working source is retained and the loop continues.
""".strip()
_ACTION_FIELDS = {
    "sandbox": {"action", "capability.py", "probe"},
    "submit": {"action", "capability.py"},
    "blocked": {"action", "reason"},
}
_PRIVATE_TERMS = (
    "private", "criterion", "validation", "suite", "threshold", "mujoco", "translation",
    "truth", "video", "score", "verdict",
)
_REPAIR_PRIVATE_TERMS = _PRIVATE_TERMS + ("measurement", "case", "seed", "input")
REPAIR_MAX_LLM_CALLS = 20


@dataclass(frozen=True)
class Stage2Config:
    max_llm_calls: int = 30
    min_llm_calls_before_submit: int = 1
    min_successful_sandbox_calls_before_submit: int = 0
    required_sandbox_capability_ids: tuple[str, ...] = ()
    require_all_design_capability_probes: bool = False
    required_sandbox_probe_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.max_llm_calls, int) or isinstance(self.max_llm_calls, bool) or not 1 <= self.max_llm_calls <= 30:
            raise ContractError("Stage 2 max_llm_calls must be between 1 and 30")
        if (
            not isinstance(self.min_llm_calls_before_submit, int)
            or isinstance(self.min_llm_calls_before_submit, bool)
            or not 1 <= self.min_llm_calls_before_submit <= self.max_llm_calls
        ):
            raise ContractError(
                "Stage 2 min_llm_calls_before_submit must be between 1 and max_llm_calls"
            )
        if (
            not isinstance(self.min_successful_sandbox_calls_before_submit, int)
            or isinstance(self.min_successful_sandbox_calls_before_submit, bool)
            or self.min_successful_sandbox_calls_before_submit < 0
        ):
            raise ContractError(
                "Stage 2 min_successful_sandbox_calls_before_submit must be non-negative"
            )
        if self.required_sandbox_probe_ids is not None:
            if not isinstance(self.required_sandbox_probe_ids, tuple):
                raise ContractError("Stage 2 required_sandbox_probe_ids must be a tuple")
            if self.required_sandbox_capability_ids and (
                self.required_sandbox_capability_ids != self.required_sandbox_probe_ids
            ):
                raise ContractError(
                    "Stage 2 capability and legacy probe coverage IDs disagree"
                )
            object.__setattr__(
                self,
                "required_sandbox_capability_ids",
                self.required_sandbox_probe_ids,
            )
        if not isinstance(self.required_sandbox_capability_ids, tuple):
            raise ContractError("Stage 2 required_sandbox_capability_ids must be a tuple")
        if any(
            not isinstance(capability_id, str) or not capability_id.strip()
            for capability_id in self.required_sandbox_capability_ids
        ):
            raise ContractError(
                "Stage 2 required_sandbox_capability_ids must contain non-empty strings"
            )
        if len(set(self.required_sandbox_capability_ids)) != len(self.required_sandbox_capability_ids):
            raise ContractError("Stage 2 required_sandbox_capability_ids must be unique")
        if not isinstance(self.require_all_design_capability_probes, bool):
            raise ContractError("Stage 2 require_all_design_capability_probes must be a boolean")


@dataclass(frozen=True)
class Stage2Result:
    status: str
    bundle_hash: str
    binding_contract: dict[str, Any]
    binding_hash: str
    binding_seal: dict[str, Any]
    starter_skeleton: str
    starter_skeleton_hash: str
    capability_source: str | None
    source_hash: str | None
    source_seal: dict[str, Any] | None
    implementation_manifest: dict[str, Any] | None
    manifest_hash: str | None
    manifest_seal: dict[str, Any] | None
    llm_calls: int
    sandbox_calls: int
    successful_sandbox_calls: int
    covered_sandbox_capability_ids: tuple[str, ...]
    call_log: tuple[dict[str, Any], ...]
    sandbox_log: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]
    blocked_reason: str | None

    @property
    def implementation_bundle_hash(self) -> str:
        """Explicit alias used by downstream manifest and validation contracts."""

        return self.bundle_hash

    @property
    def covered_sandbox_probe_ids(self) -> tuple[str, ...]:
        """Backward-compatible alias for the capability-keyed coverage result."""

        return self.covered_sandbox_capability_ids


@dataclass(frozen=True)
class _ActionLoopResult:
    """Public result of one continuous implementation action episode."""

    status: str
    source: str | None
    calls: tuple[dict[str, Any], ...]
    sandbox_log: tuple[dict[str, Any], ...]
    successful_sandbox_calls: int
    covered_capability_ids: tuple[str, ...]
    diagnostics: tuple[dict[str, str], ...]
    blocked_reason: str | None
    submitted: bool


@dataclass(frozen=True)
class _Stage2Session:
    """State shared by the initial Stage 2 and all Repair episodes."""

    bundle: ImplementationBundle
    binding: PythonBinding
    base_inputs: dict[str, Any]
    sandbox_contract: dict[str, Any]
    required_capability_ids: tuple[str, ...]
    known_capability_ids: tuple[str, ...]


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _public_reason(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(term in value.lower() for term in _PRIVATE_TERMS)


def _authorization(value: Any, design_hash: str) -> dict[str, Any]:
    """Accept only the opaque handle issued by the READY Blue Line branch."""

    if not isinstance(value, Stage2Authorization):
        raise ContractError("Stage 2 requires a READY Blue Line authorization")
    return value._verified_payload(design_hash)


def _action_issues(output: Mapping[str, Any]) -> list[dict[str, str]]:
    action = output.get("action")
    if action not in _ACTION_FIELDS:
        return [_issue("MODEL_ACTION", "Stage 2 model output must select sandbox, submit, or blocked")]
    expected = _ACTION_FIELDS[action]
    if set(output) != expected:
        return [_issue("MODEL_ACTION_FIELDS", f"{action} action must contain exactly {sorted(expected)}")]
    if action in {"sandbox", "submit"} and (not isinstance(output.get("capability.py"), str) or not output["capability.py"].strip()):
        return [_issue("CAPABILITY_SOURCE", f"{action} action needs one non-empty capability.py string")]
    if action == "sandbox" and not isinstance(output.get("probe"), Mapping):
        return [_issue("SANDBOX_PROBE", "sandbox action needs one public probe object")]
    if action == "blocked" and not _public_reason(output.get("reason")):
        return [_issue("BLOCKED_REASON", "blocked action needs one public reason")]
    return []


def _submission_requirements(
    config: Stage2Config,
    required_capability_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "max_llm_calls": config.max_llm_calls,
        "min_llm_calls_before_submit": config.min_llm_calls_before_submit,
        "min_successful_sandbox_calls_before_submit": config.min_successful_sandbox_calls_before_submit,
        "required_sandbox_capability_ids": list(required_capability_ids),
        "require_all_design_capability_probes": config.require_all_design_capability_probes,
    }


def _coverage_identity(probe: Mapping[str, Any]) -> tuple[str, str] | None:
    probe_id = probe.get("probe_id")
    capability_id = probe.get("capability_id")
    if (
        not isinstance(probe_id, str)
        or not probe_id.strip()
        or not isinstance(capability_id, str)
        or not capability_id.strip()
    ):
        return None
    return probe_id, capability_id


def _submission_issues(
    *,
    llm_calls: int,
    successful_sandbox_calls: int,
    covered_capability_ids: set[str],
    required_capability_ids: tuple[str, ...],
    config: Stage2Config,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if llm_calls < config.min_llm_calls_before_submit:
        issues.append(_issue(
            "SUBMISSION_REQUIREMENTS",
            "Submit rejected: min_llm_calls_before_submit is "
            f"{config.min_llm_calls_before_submit}, but the current call count is {llm_calls}.",
        ))
    if successful_sandbox_calls < config.min_successful_sandbox_calls_before_submit:
        issues.append(_issue(
            "SUBMISSION_REQUIREMENTS",
            "Submit rejected: min_successful_sandbox_calls_before_submit is "
            f"{config.min_successful_sandbox_calls_before_submit}, but only "
            f"{successful_sandbox_calls} successful public sandbox calls are recorded.",
        ))
    missing_capability_ids = sorted(set(required_capability_ids) - covered_capability_ids)
    if missing_capability_ids:
        issues.append(_issue(
            "SUBMISSION_REQUIREMENTS",
            "Submit rejected: missing required public sandbox capability IDs: "
            + ", ".join(missing_capability_ids) + ".",
        ))
    return issues


def _public_repair_diagnostics(value: Any) -> list[dict[str, str]]:
    """Keep only sanitized, candidate-facing Repair diagnostics."""

    if not isinstance(value, list):
        return []
    diagnostics: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, str] = {}
        for field in ("gate", "code", "candidate_error"):
            candidate = item.get(field)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            if any(term in candidate.lower() for term in _REPAIR_PRIVATE_TERMS):
                continue
            safe[field] = " ".join(candidate.split())[:320]
        if "gate" in safe and "code" in safe:
            diagnostics.append(safe)
    return diagnostics


def _public_sandbox_feedback(value: Any) -> dict[str, Any]:
    """Project callback feedback again before it reaches the implementation agent."""

    def contains_private(item: Any) -> bool:
        if isinstance(item, Mapping):
            return any(
                any(term in str(key).lower() for term in _REPAIR_PRIVATE_TERMS)
                or contains_private(child)
                for key, child in item.items()
            )
        if isinstance(item, list):
            return any(contains_private(child) for child in item)
        return isinstance(item, str) and any(
            term in item.lower() for term in _REPAIR_PRIVATE_TERMS
        )

    if not isinstance(value, Mapping) or contains_private(value):
        return {
            "status": "ERROR",
            "summary": "Sandbox returned non-public feedback.",
            "observations": {},
            "exception": "sandbox_feedback_contract_error",
        }
    return copy.deepcopy(dict(value))


class Stage2Runner:
    """A continuous Stage 2 implementation agent with bounded action episodes."""

    def __init__(self, generator: JsonGenerator, sandbox: CallbackSandbox | None = None, config: Stage2Config = Stage2Config()):
        if sandbox is not None and not isinstance(sandbox, CallbackSandbox):
            raise ContractError("Stage 2 Sandbox must be the callback-only Sandbox boundary")
        self.generator = generator
        self.sandbox = sandbox
        self.config = config
        self._session: _Stage2Session | None = None
        self._working_source: str | None = None
        self._last_repair_trace: dict[str, Any] | None = None

    @property
    def last_repair_trace(self) -> dict[str, Any] | None:
        """Return the latest public inner Repair trace for orchestration evidence."""

        return copy.deepcopy(self._last_repair_trace)

    def run(
        self,
        capability_design: Mapping[str, Any],
        design_seal: Mapping[str, Any],
        blue_line_authorization: Stage2Authorization,
        implementation_bundle: Mapping[str, Any] | ImplementationBundle,
    ) -> Stage2Result:
        bundle = validate_implementation_bundle(implementation_bundle)
        binding = derive_python_binding(capability_design, design_seal)
        authorization = _authorization(
            blue_line_authorization, binding.contract["design_hash"]
        )
        design = copy.deepcopy(dict(capability_design))
        design_capability_ids = tuple(
            str(entry["capability_id"])
            for entry in binding.contract["bindings"]
        )
        required_capability_ids = tuple(dict.fromkeys(
            (*self.config.required_sandbox_capability_ids, *(
                design_capability_ids if self.config.require_all_design_capability_probes else ()
            ))
        ))
        base_inputs: dict[str, Any] = {
            "capability_design": design,
            "binding_contract": copy.deepcopy(binding.contract),
            "starter_skeleton": binding.starter_skeleton,
            "blue_line_authorization": authorization,
            "implementation_bundle": bundle.artifact,
        }
        sandbox_contract = self.sandbox.contract if self.sandbox is not None else get_sandbox_contract()
        self._session = _Stage2Session(
            bundle=bundle,
            binding=binding,
            base_inputs=copy.deepcopy(base_inputs),
            sandbox_contract=copy.deepcopy(sandbox_contract),
            required_capability_ids=required_capability_ids,
            known_capability_ids=design_capability_ids,
        )
        self._working_source = None
        self._last_repair_trace = None
        episode = self._run_action_loop(
            base_inputs=base_inputs,
            sandbox_contract=sandbox_contract,
            config=self.config,
            required_capability_ids=required_capability_ids,
            known_capability_ids=set(design_capability_ids),
        )
        self._working_source = episode.source
        if episode.status != "SUBMITTED" or episode.source is None:
            return self._result(
                episode.status,
                bundle.bundle_hash,
                binding,
                episode.source,
                None,
                None,
                None,
                list(episode.calls),
                list(episode.sandbox_log),
                episode.successful_sandbox_calls,
                set(episode.covered_capability_ids),
                list(episode.diagnostics),
                episode.blocked_reason,
            )
        source = episode.source
        symbols = verify_capability_source(source, binding.contract)
        source_hash = content_hash(source.encode("utf-8"))
        source_seal = create_seal(
            "capability.py",
            source_hash,
            [binding.contract_hash, binding.contract["design_hash"]],
        )
        manifest, manifest_hash, manifest_seal = derive_implementation_manifest(
            design_hash=binding.contract["design_hash"],
            binding_hash=binding.contract_hash,
            implementation_bundle_hash=bundle.bundle_hash,
            source_hash=source_hash,
            symbols=symbols,
        )
        return self._result(
            "SUBMITTED",
            bundle.bundle_hash,
            binding,
            source,
            source_hash,
            source_seal,
            (manifest, manifest_hash, manifest_seal),
            list(episode.calls),
            list(episode.sandbox_log),
            episode.successful_sandbox_calls,
            set(episode.covered_capability_ids),
            [],
            None,
        )

    def repair_episode(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run one bounded Generate↔Sandbox Repair episode on the same agent state.

        The callback intentionally returns only the legacy RepairRunner payload.  The
        public inner trace is exposed through ``last_repair_trace`` so RepairRunner can
        persist it without adding fields to the model-facing response contract.
        """

        session = self._session
        if session is None:
            raise ContractError("Stage 2 Repair requires a completed Stage 2 episode")
        if not isinstance(request, Mapping):
            raise ContractError("Stage 2 Repair request must be an object")
        requested_source = request.get("capability.py")
        if not isinstance(requested_source, str) or not requested_source.strip():
            requested_source = self._working_source
        if not isinstance(requested_source, str) or not requested_source.strip():
            raise ContractError("Stage 2 Repair requires a working capability.py source")

        diagnostics = _public_repair_diagnostics(request.get("diagnostics"))
        repair_config = Stage2Config(
            max_llm_calls=REPAIR_MAX_LLM_CALLS,
            min_llm_calls_before_submit=1,
            min_successful_sandbox_calls_before_submit=1,
        )
        episode = self._run_action_loop(
            base_inputs=session.base_inputs,
            sandbox_contract=session.sandbox_contract,
            config=repair_config,
            required_capability_ids=(),
            known_capability_ids=set(session.known_capability_ids),
            working_source=requested_source,
            initial_diagnostics=diagnostics,
            episode="repair",
        )
        self._working_source = episode.source or requested_source
        self._last_repair_trace = {
            "status": episode.status,
            "submitted": episode.submitted,
            "llm_calls": len(episode.calls),
            "working_source": self._working_source,
            "call_log": copy.deepcopy(list(episode.calls)),
            "sandbox_log": copy.deepcopy(list(episode.sandbox_log)),
            "diagnostics": copy.deepcopy(list(episode.diagnostics)),
            "blocked_reason": episode.blocked_reason,
        }
        return {
            "capability.py": self._working_source,
            "llm_calls": len(episode.calls),
        }

    def _run_action_loop(
        self,
        *,
        base_inputs: Mapping[str, Any],
        sandbox_contract: Mapping[str, Any],
        config: Stage2Config,
        required_capability_ids: tuple[str, ...],
        known_capability_ids: set[str],
        working_source: str | None = None,
        initial_diagnostics: list[dict[str, str]] | None = None,
        episode: str = "stage2",
    ) -> _ActionLoopResult:
        calls: list[dict[str, Any]] = []
        sandbox_log: list[dict[str, Any]] = []
        diagnostics = copy.deepcopy(initial_diagnostics or [])
        last_feedback: dict[str, Any] | None = None
        successful_sandbox_calls = 0
        covered_capability_ids: set[str] = set()
        blocked_reason: str | None = None
        for attempt in range(config.max_llm_calls):
            inputs: dict[str, Any] = copy.deepcopy(dict(base_inputs))
            inputs["sandbox_contract"] = copy.deepcopy(dict(sandbox_contract))
            inputs["submission_requirements"] = _submission_requirements(
                config, required_capability_ids
            )
            if working_source is not None:
                inputs["working_capability.py"] = working_source
            if last_feedback is not None:
                inputs["sandbox_feedback"] = copy.deepcopy(last_feedback)
            if diagnostics:
                inputs["public_diagnostics"] = copy.deepcopy(diagnostics)
            output = self.generator.generate_json("stage2", STAGE2_PROMPT, inputs)
            if not isinstance(output, Mapping):
                raise ContractError("Stage 2 generator must return an action object")
            output_dict = dict(output)
            action = output_dict.get("action")
            diagnostics = _action_issues(output_dict)
            calls.append({
                "call": attempt + 1,
                "stage": "stage2",
                "episode": episode,
                "action": action if isinstance(action, str) else None,
                "input_hash": content_hash(canonical_bytes(inputs)),
                "output_hash": content_hash(canonical_bytes(output_dict)),
                "diagnostics": copy.deepcopy(diagnostics),
            })
            if diagnostics:
                continue
            if action == "sandbox":
                working_source = output_dict["capability.py"]
                if self.sandbox is None:
                    last_feedback = {
                        "status": "ERROR",
                        "summary": "Sandbox is unavailable for this run.",
                        "observations": {},
                        "exception": "sandbox_unavailable",
                    }
                    sandbox_log.append({
                        "sandbox_call": len(sandbox_log) + 1,
                        "executed": False,
                        "source_hash": content_hash(working_source.encode("utf-8")),
                        "probe_hash": content_hash(canonical_bytes(output_dict["probe"])),
                        "feedback_hash": content_hash(canonical_bytes(last_feedback)),
                        "feedback": copy.deepcopy(last_feedback),
                        "status": last_feedback["status"],
                        "successful": False,
                        "probe_id": output_dict["probe"].get("probe_id"),
                        "capability_id": output_dict["probe"].get("capability_id"),
                        "coverage_counted": False,
                    })
                    continue
                last_feedback = _public_sandbox_feedback(
                    self.sandbox.run(working_source, output_dict["probe"])
                )
                identity = _coverage_identity(output_dict["probe"])
                successful = last_feedback.get("status") == "OK"
                coverage_counted = (
                    successful
                    and identity is not None
                    and identity[1] in known_capability_ids
                )
                if coverage_counted:
                    covered_capability_ids.add(identity[1])
                if successful:
                    successful_sandbox_calls += 1
                sandbox_log.append({
                    "sandbox_call": len(sandbox_log) + 1,
                    "executed": True,
                    "source_hash": content_hash(working_source.encode("utf-8")),
                    "probe_hash": content_hash(canonical_bytes(output_dict["probe"])),
                    "feedback_hash": content_hash(canonical_bytes(last_feedback)),
                    "feedback": copy.deepcopy(last_feedback),
                    "status": last_feedback.get("status"),
                    "successful": successful,
                    "probe_id": identity[0] if identity is not None else None,
                    "capability_id": identity[1] if identity is not None else None,
                    "coverage_counted": coverage_counted,
                })
                continue
            if action == "blocked":
                blocked_reason = output_dict["reason"]
                return _ActionLoopResult(
                    status="IMPLEMENTATION_BLOCKED",
                    source=working_source,
                    calls=tuple(copy.deepcopy(calls)),
                    sandbox_log=tuple(copy.deepcopy(sandbox_log)),
                    successful_sandbox_calls=successful_sandbox_calls,
                    covered_capability_ids=tuple(sorted(covered_capability_ids)),
                    diagnostics=tuple(copy.deepcopy(diagnostics)),
                    blocked_reason=blocked_reason,
                    submitted=False,
                )
            # Submit starts the immutable candidate boundary.  Only basic parsing and
            # required-symbol presence are checked here; Validation A owns semantics.
            source = output_dict["capability.py"]
            working_source = source
            diagnostics = _submission_issues(
                llm_calls=len(calls),
                successful_sandbox_calls=successful_sandbox_calls,
                covered_capability_ids=covered_capability_ids,
                required_capability_ids=required_capability_ids,
                config=config,
            )
            if diagnostics:
                calls[-1]["diagnostics"] = copy.deepcopy(diagnostics)
                continue
            try:
                verify_capability_source(source, self._session.binding.contract if self._session else {})
            except ContractError as exc:
                diagnostics = [_issue("SOURCE_BINDING", str(exc))]
                calls[-1]["diagnostics"] = copy.deepcopy(diagnostics)
                continue
            return _ActionLoopResult(
                status="SUBMITTED",
                source=source,
                calls=tuple(copy.deepcopy(calls)),
                sandbox_log=tuple(copy.deepcopy(sandbox_log)),
                successful_sandbox_calls=successful_sandbox_calls,
                covered_capability_ids=tuple(sorted(covered_capability_ids)),
                diagnostics=(),
                blocked_reason=None,
                submitted=True,
            )
        return _ActionLoopResult(
            status="CALL_LIMIT_EXHAUSTED",
            source=working_source,
            calls=tuple(copy.deepcopy(calls)),
            sandbox_log=tuple(copy.deepcopy(sandbox_log)),
            successful_sandbox_calls=successful_sandbox_calls,
            covered_capability_ids=tuple(sorted(covered_capability_ids)),
            diagnostics=tuple(copy.deepcopy(diagnostics)),
            blocked_reason=None,
            submitted=False,
        )

    @staticmethod
    def _result(
        status: str,
        implementation_bundle_hash: str,
        binding: PythonBinding,
        source: str | None,
        source_hash: str | None,
        source_seal: dict[str, Any] | None,
        manifest_data: tuple[dict[str, Any], str, dict[str, Any]] | None,
        calls: list[dict[str, Any]],
        sandbox_log: list[dict[str, Any]],
        successful_sandbox_calls: int,
        covered_capability_ids: set[str],
        diagnostics: list[dict[str, str]],
        blocked_reason: str | None,
    ) -> Stage2Result:
        manifest, manifest_hash, manifest_seal = manifest_data if manifest_data is not None else (None, None, None)
        return Stage2Result(
            status=status,
            bundle_hash=implementation_bundle_hash,
            binding_contract=binding.contract,
            binding_hash=binding.contract_hash,
            binding_seal=binding.contract_seal,
            starter_skeleton=binding.starter_skeleton,
            starter_skeleton_hash=binding.starter_skeleton_hash,
            capability_source=source,
            source_hash=source_hash,
            source_seal=source_seal,
            implementation_manifest=manifest,
            manifest_hash=manifest_hash,
            manifest_seal=manifest_seal,
            llm_calls=len(calls),
            sandbox_calls=sum(1 for item in sandbox_log if item["executed"]),
            successful_sandbox_calls=successful_sandbox_calls,
            covered_sandbox_capability_ids=tuple(sorted(covered_capability_ids)),
            call_log=tuple(calls),
            sandbox_log=tuple(sandbox_log),
            diagnostics=tuple(diagnostics),
            blocked_reason=blocked_reason,
        )
