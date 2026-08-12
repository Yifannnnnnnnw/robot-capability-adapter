"""Small HTTP adapter for the experiment's stage and ReAct model calls."""

from __future__ import annotations

import ast
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from ..foundation.errors import ContractError


DEFAULT_BASE_URL = "https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws"
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"


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

_UNITREE_GO2_SDK_GUIDANCE = """
This implementation_bundle is the Unitree/Go2 low-level projection. Use this exact
public SDK shape, and no other facade attributes:
- `_sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)`, then call `Init()` on the
  local publisher;
- `_sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)` and
  `_sdk.ChannelSubscriber("rt/sportmodestate", _sdk.SportModeState_)`, then call
  `Init()` on each local subscriber;
- pass `_sdk.LowState_` and `_sdk.SportModeState_` as IDL type classes, not strings;
- create a command message with `_sdk.unitree_go_msg_dds__LowCmd_()`, assign its
  `motor_cmd` fields, and pass that message instance to publisher `Write()`;
- pass `_sdk` or these local endpoint/message objects explicitly into helpers. Do not
  use `_sdk.low_cmd_publisher`, `_sdk.low_state_subscriber`, or
  `_sdk.sport_state_subscriber`.
Do not call `ChannelFactoryInitialize`; the framework owns global SDK setup. This is
only the allowed SDK wiring shape, not a fixed robot behavior implementation.
""".strip()

_SO_ARM101_SDK_GUIDANCE = """
This implementation_bundle is the SO-ARM101 direct-facade projection. `_sdk` is the
already-open injected SO facade. Call `_sdk.get_observation()` and
`_sdk.send_action({...six fields...})` directly. The action object uses exactly these
six ordered fields: `shoulder_pan.pos`, `shoulder_lift.pos`, `elbow_flex.pos`,
`wrist_flex.pos`, `wrist_roll.pos`, and `gripper.pos`; the arm fields use degree and
`gripper.pos` uses normalized_0_100. The observation contains those same six fields,
and `public_task_state` is returned inside the observation. Do not construct
`SO101Follower`, `SOFollower`, or `FeetechMotorsBus`; do not call `connect` or
`disconnect`. Opening and closing are Framework session lifecycle. This is API wiring only, not IK/control behavior.
""".strip()


def _is_unitree_sdk_bundle(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    projection = value.get("sdk_implementation_projection")
    if not isinstance(projection, Mapping):
        return False
    permitted_types = projection.get("permitted_types")
    if not isinstance(permitted_types, (list, tuple)):
        return False
    return {
        "ChannelPublisher",
        "ChannelSubscriber",
        "LowCmd_",
        "LowState_",
        "SportModeState_",
    }.issubset(permitted_types)


def _is_so_sdk_bundle(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    projection = value.get("sdk_implementation_projection")
    if not isinstance(projection, Mapping):
        return False
    entry_id = projection.get("sdk_entry_id")
    if entry_id in {"lerobot-so101-follower", "lerobot-so-arm101"}:
        return True
    operations = projection.get("permitted_operations")
    return operations == ["send_action", "get_observation"]


def _repair_instruction(request: Mapping[str, Any]) -> str:
    instruction = (
        "Repair only capability.py using the supplied public diagnostics and binding. "
        "Return exactly {\"capability.py\": <complete raw parseable Python source>, "
        "\"llm_calls\": 1}. The capability.py string must contain the complete one-file "
        "source with no Markdown fences, backticks, explanation, or omitted code. Follow "
        "the same Validation A grammar: imports only math, time, or numpy (optionally as np); "
        "safe literal constants; private non-dunder helpers; exact bound functions and "
        "signatures; approved math/time/numpy members, safe numeric builtins, and bound _sdk "
        "members only. No dynamic import, eval, exec, open, dunder access, extra public "
        "symbols. Do not alter the supplied candidate except to return the complete corrected source."
    )
    guidance = _GENERIC_SDK_GUIDANCE
    if _is_unitree_sdk_bundle(request.get("implementation_bundle")):
        guidance += "\n\n" + _UNITREE_GO2_SDK_GUIDANCE
    elif _is_so_sdk_bundle(request.get("implementation_bundle")):
        guidance += "\n\n" + _SO_ARM101_SDK_GUIDANCE
    return instruction + "\n\nFRAMEWORK SDK RULES:\n" + guidance


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

    def _complete_json(self, *, stage: str, instruction: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        request_body = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object and no Markdown. "
                        f"You are the isolated AutoAdapter stage: {stage}."
                    ),
                },
                {
                    "role": "user",
                    "content": instruction + "\n\nINPUT_JSON:\n" + json.dumps(
                        dict(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ),
                },
            ],
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
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_s, context=ssl_context
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ContractError(f"model API call failed for {stage}") from exc
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
