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
from .sandbox import CallbackSandbox


STAGE2_PROMPT = (
    "Return exactly one Stage 2 action object. Allowed actions are sandbox, submit, "
    "or blocked. A submit contains only action and capability.py; never return a manifest, "
    "additional file, validation content, or private evaluation information."
)
_ACTION_FIELDS = {
    "sandbox": {"action", "capability.py", "probe"},
    "submit": {"action", "capability.py"},
    "blocked": {"action", "reason"},
}
_PRIVATE_TERMS = ("private", "criterion", "validation", "suite", "threshold", "mujoco", "translation")


@dataclass(frozen=True)
class Stage2Config:
    max_llm_calls: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.max_llm_calls, int) or isinstance(self.max_llm_calls, bool) or not 1 <= self.max_llm_calls <= 30:
            raise ContractError("Stage 2 max_llm_calls must be between 1 and 30")


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
    call_log: tuple[dict[str, Any], ...]
    sandbox_log: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]
    blocked_reason: str | None

    @property
    def implementation_bundle_hash(self) -> str:
        """Explicit alias used by downstream manifest and validation contracts."""

        return self.bundle_hash


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


class Stage2Runner:
    """A 30-accounted-call, action-only Stage 2 runner for the demo."""

    def __init__(self, generator: JsonGenerator, sandbox: CallbackSandbox | None = None, config: Stage2Config = Stage2Config()):
        if sandbox is not None and not isinstance(sandbox, CallbackSandbox):
            raise ContractError("Stage 2 Sandbox must be the callback-only Sandbox boundary")
        self.generator = generator
        self.sandbox = sandbox
        self.config = config

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
        base_inputs = {
            "capability_design": design,
            "binding_contract": copy.deepcopy(binding.contract),
            "starter_skeleton": binding.starter_skeleton,
            "blue_line_authorization": authorization,
            "implementation_bundle": bundle.artifact,
        }
        calls: list[dict[str, Any]] = []
        sandbox_log: list[dict[str, Any]] = []
        diagnostics: list[dict[str, str]] = []
        working_source: str | None = None
        last_feedback: dict[str, Any] | None = None
        for attempt in range(self.config.max_llm_calls):
            inputs: dict[str, Any] = copy.deepcopy(base_inputs)
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
                    })
                    continue
                last_feedback = self.sandbox.run(working_source, output_dict["probe"])
                sandbox_log.append({
                    "sandbox_call": len(sandbox_log) + 1,
                    "executed": True,
                    "source_hash": content_hash(working_source.encode("utf-8")),
                    "probe_hash": content_hash(canonical_bytes(output_dict["probe"])),
                    "feedback_hash": content_hash(canonical_bytes(last_feedback)),
                })
                continue
            if action == "blocked":
                return self._result(
                    "IMPLEMENTATION_BLOCKED", bundle.bundle_hash, binding, None, None, None, None,
                    calls, sandbox_log, diagnostics, output_dict["reason"],
                )
            # Submit starts the immutable candidate boundary.  Only basic parsing and
            # required-symbol presence are checked here; Validation A owns semantics.
            source = output_dict["capability.py"]
            try:
                symbols = verify_capability_source(source, binding.contract)
            except ContractError as exc:
                diagnostics = [_issue("SOURCE_BINDING", str(exc))]
                calls[-1]["diagnostics"] = copy.deepcopy(diagnostics)
                working_source = source
                continue
            source_hash = content_hash(source.encode("utf-8"))
            source_seal = create_seal("capability.py", source_hash, [binding.contract_hash, binding.contract["design_hash"]])
            manifest, manifest_hash, manifest_seal = derive_implementation_manifest(
                design_hash=binding.contract["design_hash"],
                binding_hash=binding.contract_hash,
                implementation_bundle_hash=bundle.bundle_hash,
                source_hash=source_hash,
                symbols=symbols,
            )
            return self._result(
                "SUBMITTED", bundle.bundle_hash, binding, source, source_hash, source_seal,
                (manifest, manifest_hash, manifest_seal), calls, sandbox_log, (), None,
            )
        return self._result(
            "CALL_LIMIT_EXHAUSTED", bundle.bundle_hash, binding, None, None, None, None,
            calls, sandbox_log, diagnostics, None,
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
            call_log=tuple(calls),
            sandbox_log=tuple(sandbox_log),
            diagnostics=tuple(diagnostics),
            blocked_reason=blocked_reason,
        )
