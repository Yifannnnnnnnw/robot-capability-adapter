"""Small HTTP adapter for the experiment's stage and ReAct model calls."""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from ..foundation.errors import ContractError


DEFAULT_BASE_URL = "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws"
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"


IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT = """
RUNTIME FEEDBACK-LOOP CONTRACT (public and robot-agnostic):
Every bound capability function that produces a dynamic robot effect must implement a
bounded closed loop inside the capability itself. Repeatedly read fresh public state,
compute the next action from the newest observation, send that action, allow
Framework-owned execution time or physics to advance, read fresh public state again,
and issue a state-dependent correction. Continue until the public requested effect
converges or a bounded timeout is reached, then return a public result or error.

Fixed trajectories, one-read-many-write behavior, pure polling, sleep-only behavior,
and self-report do not satisfy the Sandbox. A command must be computed from a fresh
observation, and later commands must be able to correct from later observations. Use
only the exact public SDK names, state shapes, action shapes, and timing operations
supplied by the Implementation Bundle and Sandbox contract; do not invent them. Do
not infer hidden acceptance rules, numeric limits, test scenarios, privileged state,
simulator internals, or robot-specific failure injection.
""".strip()


_GENERIC_SDK_GUIDANCE = """
SDK boundary rules: `_sdk` is a module-like injected facade, not a robot object with
invented endpoint attributes or endpoint instances on the facade. Use only the exact
members listed in the binding and implementation_bundle. Do not depend on a module-global `_sdk`: every helper that uses the SDK must receive `_sdk` explicitly,
or receive a local endpoint/message object explicitly after the capability constructs
it from `_sdk`. Do not call `ChannelFactoryInitialize` or otherwise reinitialize the
SDK's global communication. Bundle-listed publisher/subscriber constructors are
allowed local endpoints; construct them, call their listed `Init()`, and use their
listed `Read()`/`Write()` operations. Such local endpoints are not a second SDK
connection. Keep examples to SDK wiring and message-field shapes only.
""".strip()

_IMPLEMENTATION_AGENT_SYSTEM_IDENTITY = (
    "You are the isolated AutoAdapter Implementation Agent. Return exactly one JSON "
    "object and no Markdown. Stage 2 implementation and Repair are one continuous "
    "public implementation conversation. Use only the supplied public implementation "
    "state and the public SDK bundle facts."
)
_IMPLEMENTATION_AGENT_STAGES = frozenset({"stage2", "repair"})
_PUBLIC_IMPLEMENTATION_INPUTS = {
    "stage2": frozenset({
        "capability_design",
        "binding_contract",
        "starter_skeleton",
        "blue_line_authorization",
        "implementation_bundle",
        "sandbox_contract",
        "submission_requirements",
        "working_capability.py",
        "sandbox_feedback",
        "public_diagnostics",
    }),
    "repair": frozenset({
        "repair_index",
        "capability.py",
        "binding_contract",
        "implementation_bundle",
        "implementation_bundle_hash",
        "design_hash",
        "run_snapshot_hash",
        "diagnostics",
        "ledger",
    }),
}
_IMPLEMENTATION_DELTA_INPUTS = {
    "stage2": frozenset({
        "working_capability.py",
        "sandbox_feedback",
        "public_diagnostics",
        "sandbox_contract",
        "submission_requirements",
    }),
    "repair": frozenset({
        "repair_index",
        "capability.py",
        "diagnostics",
        "ledger",
        "run_snapshot_hash",
        "implementation_bundle_hash",
        "design_hash",
    }),
}
_IMPLEMENTATION_HISTORY_TURN_PAIRS = 3


def _safe_exception_text(value: Any, *, limit: int = 160) -> str:
    """Normalize provider text without carrying request or secret material."""

    text = " ".join(str(value).split())
    text = re.sub(r"https?://\S+", "<url>", text)
    text = re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    lowered = text.lower()
    if any(marker in lowered for marker in ("input_json", "request body", "payload", "messages", "capability.py")):
        text = "<redacted provider/framework detail>"
    if any(character in text for character in "{}[]"):
        text = "<redacted provider/framework detail>"
    return text[:limit] or "<no provider detail>"


def _compact_implementation_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the initial public turn and the latest bounded implementation turns."""

    max_messages = 2 + 2 * _IMPLEMENTATION_HISTORY_TURN_PAIRS
    if len(history) <= max_messages:
        return copy.deepcopy(history)
    return copy.deepcopy(history[:2] + history[-2 * _IMPLEMENTATION_HISTORY_TURN_PAIRS:])


def _render_public_implementation_bundle(bundle: Any) -> str:
    """Render only public bundle facts, deterministically, for the implementation agent."""

    sections: dict[str, Any] = {}
    if isinstance(bundle, Mapping):
        for name in (
            "sdk_implementation_projection",
            "robot_implementation_facts",
            "implementation_experience",
        ):
            value = bundle.get(name)
            if isinstance(value, Mapping):
                sections[name] = dict(value)
            elif name == "implementation_experience" and isinstance(value, list):
                sections[name] = copy.deepcopy(value)
    rendered = json.dumps(sections, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "PUBLIC IMPLEMENTATION BUNDLE (authoritative; do not invent absent facts):\n"
        f"{rendered}\n\n"
        "GENERAL SDK BOUNDARY RULES:\n"
        f"{_GENERIC_SDK_GUIDANCE}"
    )


def _public_implementation_inputs(stage: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _PUBLIC_IMPLEMENTATION_INPUTS[stage]
    return {
        key: copy.deepcopy(value)
        for key, value in inputs.items()
        if key in allowed
    }


def _implementation_request_inputs(
    stage: str,
    inputs: Mapping[str, Any],
    *,
    initial: bool,
) -> dict[str, Any]:
    public_inputs = _public_implementation_inputs(stage, inputs)
    if initial:
        return public_inputs
    allowed = _IMPLEMENTATION_DELTA_INPUTS[stage]
    return {
        key: copy.deepcopy(value)
        for key, value in public_inputs.items()
        if key in allowed
    }


def _repair_instruction(request: Mapping[str, Any]) -> str:
    instruction = (
        "Repair only capability.py using the supplied public diagnostics and binding. "
        "Return exactly {\"capability.py\": <complete raw parseable Python source>, "
        "\"llm_calls\": 1}. The capability.py string must contain the complete one-file "
        "source with no Markdown fences, backticks, explanation, or omitted code. Follow "
        "the same source grammar: imports only math, time, or numpy (optionally as np); "
        "safe literal constants; private non-dunder helpers; exact bound functions and "
        "signatures; approved math/time/numpy members, safe numeric builtins, and bound _sdk "
        "members only. No dynamic import, eval, exec, open, dunder access, extra public "
        "symbols. Do not alter the supplied candidate except to return the complete corrected source.\n\n"
        f"{IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT}"
    )
    return instruction


@dataclass(frozen=True)
class ModelApiConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: float = 125.0

    @classmethod
    def from_environment(cls) -> "ModelApiConfig":
        api_key = os.environ.get("AUTOADAPTER_MODEL_API_KEY", "").strip()
        if not api_key:
            raise ContractError("AUTOADAPTER_MODEL_API_KEY is required")
        return cls(api_key=api_key)


class ModelApiClient:
    """JSON-only provider used by Stage 1, Blue Line, Stage 2, Repair and ReAct."""

    def __init__(self, config: ModelApiConfig):
        if not isinstance(config, ModelApiConfig):
            raise ContractError("ModelApiClient requires ModelApiConfig")
        self.config = config
        self.calls: list[dict[str, Any]] = []
        self._implementation_history: list[dict[str, str]] = []

    def _complete_json(self, *, stage: str, instruction: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        implementation_agent = stage in _IMPLEMENTATION_AGENT_STAGES
        initial_implementation_context = implementation_agent and not self._implementation_history
        request_inputs = (
            _implementation_request_inputs(
                stage,
                inputs,
                initial=initial_implementation_context,
            )
            if implementation_agent
            else dict(inputs)
        )
        if implementation_agent and IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT not in instruction:
            instruction = instruction.rstrip() + "\n\n" + IMPLEMENTATION_FEEDBACK_LOOP_CONTRACT
        if initial_implementation_context:
            instruction = instruction.rstrip() + "\n\n" + _render_public_implementation_bundle(
                request_inputs.get("implementation_bundle")
            )
        user_content = instruction + "\n\nINPUT_JSON:\n" + json.dumps(
            request_inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if implementation_agent:
            messages = [
                {"role": "system", "content": _IMPLEMENTATION_AGENT_SYSTEM_IDENTITY},
                *copy.deepcopy(self._implementation_history),
                {"role": "user", "content": user_content},
            ]
        else:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object and no Markdown. "
                        f"You are the isolated AutoAdapter stage: {stage}."
                    ),
                },
                {"role": "user", "content": user_content},
            ]
        request_body = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "X-Api-Key": self.config.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        ca_file = os.environ.get("SSL_CERT_FILE")
        ssl_context = (
            ssl.create_default_context(cafile=ca_file)
            if ca_file
            else ssl.create_default_context()
        )
        payload: Any = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_s, context=ssl_context
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if attempt == 1:
                    detail = _safe_exception_text(exc)
                    raise ContractError(
                        f"model API call failed for {stage}: {type(exc).__name__}: {detail}"
                    ) from exc
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ContractError("model API response lacks choices")
        returned_models: list[str] = []
        candidates: list[Any] = [payload.get("model")] if isinstance(payload, dict) else []
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if isinstance(metadata, Mapping):
            candidates.append(metadata.get("model"))
        candidates.append(choices[0].get("model"))
        for candidate in candidates:
            if candidate is None:
                continue
            if not isinstance(candidate, str) or not candidate.strip():
                raise ContractError("model API response contains an invalid model identity")
            returned_models.append(candidate.strip())
        if returned_models and any(model != self.config.model for model in returned_models):
            raise ContractError("model API response model identity does not match the frozen model")
        if len(set(returned_models)) > 1:
            raise ContractError("model API response contains conflicting model identities")
        returned_model = returned_models[0] if returned_models else None
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            raise ContractError("model API response lacks message content")
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
                if text.startswith("json\n"):
                    text = text[5:]
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractError(f"model API returned non-JSON output for {stage}") from exc
        if not isinstance(result, dict):
            raise ContractError("model API must return one JSON object")
        if implementation_agent:
            self._implementation_history.extend([
                {"role": "user", "content": user_content},
                {
                    "role": "assistant",
                    "content": json.dumps(
                        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    ),
                },
            ])
            self._implementation_history = _compact_implementation_history(
                self._implementation_history
            )
        self.calls.append(
            {
                "stage": stage,
                "model": self.config.model,
                "returned_model": returned_model,
                "usage": payload.get("usage"),
                "metadata": payload.get("metadata"),
            }
        )
        return result

    def generate_json(self, stage: str, prompt: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        return self._complete_json(stage=stage, instruction=prompt, inputs=inputs)

    def repair(self, request: Mapping[str, Any]) -> dict[str, Any]:
        result = self._complete_json(
            stage="repair",
            instruction=_repair_instruction(request),
            inputs=request,
        )
        if set(result) != {"capability.py", "llm_calls"}:
            raise ContractError("repair response must contain exactly capability.py and llm_calls")
        if result.get("llm_calls") != 1:
            raise ContractError("repair response llm_calls must be exactly 1")
        source = result.get("capability.py")
        if not isinstance(source, str) or not source.strip() or "```" in source or "`" in source:
            raise ContractError("repair capability.py must be complete raw source without Markdown")
        try:
            ast.parse(source, filename="capability.py")
        except SyntaxError as exc:
            raise ContractError("repair capability.py must be parseable Python") from exc
        return result

    def react(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._complete_json(
            stage="react_consumer",
            instruction=(
                "Choose one visible capability action, or finish. Return either "
                "{\"thought\": <short text>, \"action\": {\"capability_id\": <id>, "
                "\"arguments\": <object>}} or {\"thought\": <short text>, \"final\": <JSON value>}."
            ),
            inputs=request,
        )
