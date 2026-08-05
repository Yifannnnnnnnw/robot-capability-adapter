"""A small auditable ReAct engine with distinct Generation and Demo roles."""

from __future__ import annotations

import copy
import json
import re
import signal
import threading
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .audit import BudgetCounter, JsonlTrace, canonical_json, sha256_bytes, sha256_json
from .model_client import (
    ModelAPIError,
    ModelClient,
    ModelMessage,
    ModelUsage,
    summarize_model_usage,
)
from .schema_validation import validate_json_schema


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler = field(compare=False, repr=False)
    timeout_s: float = field(default=30.0, compare=False)

    def prompt_view(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class AgentResult:
    status: str
    content: str
    # Backward-compatible alias: a "model call" here means one Agent turn,
    # not one provider HTTP attempt. Use the explicit fields below for audits.
    model_calls: int
    agent_turns: int
    provider_http_attempts: int
    provider_retries: int
    client_kind: str
    scripted: bool
    agent_turn_budget: Mapping[str, Any]
    provider_attempt_budget_policy: Mapping[str, Any]
    usage: Mapping[str, int | float | None]
    session_id: str
    episode_id: str


class ReactProtocolError(RuntimeError):
    pass


class RequestContextLimitExceeded(RuntimeError):
    """Raised before a model turn when its projected context is too large."""


class ToolDeadlineExceeded(BaseException):
    """BaseException prevents generated ``except Exception`` from swallowing it."""


_MAX_LLM_SCHEMA_ISSUE_BYTES = 512

_PROCESS_READ_TOOLS = {
    "read_generation_snapshot",
    "read_frozen_stage1",
    "read_generated_file",
    "list_generated_files",
    "search_generated_file_text",
    "read_generated_python_symbol",
}
_PROCESS_WRITE_TOOLS = {
    "write_generated_file",
    "begin_generated_file_write",
    "append_generated_file_chunk",
    "append_generated_file_chunks",
    "commit_generated_file_write",
    "abort_generated_file_write",
    "write_package_manifest",
    "replace_generated_file_text",
}
_PROCESS_CHECK_TOOLS = {
    "check_generated_syntax",
    "finish_package",
    "probe_generated_capability",
}
_SAFE_PROCESS_TARGET_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")


def _safe_process_digest(value: Any) -> str | None:
    try:
        return sha256_json(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _llm_safe_schema_issue(issue: Any) -> dict[str, Any]:
    """Keep oversized rejected arguments out of the next model context.

    ``jsonschema`` embeds the complete offending string in messages such as
    ``'<10 KiB of source>' is too long``.  Repeating that source in every later
    ReAct request wastes context and can itself trigger provider truncation.
    The append-only trace keeps a content-addressed diagnostic instead.
    """

    message = str(issue.message)
    keyword = getattr(issue, "keyword", None)
    constraint = getattr(issue, "constraint", None)
    observed = getattr(issue, "observed", None)
    encoded = message.encode("utf-8")
    if keyword in {"maxLength", "minLength"} and isinstance(observed, Mapping):
        relation = "exceeds" if keyword == "maxLength" else "is below"
        safe: dict[str, Any] = {
            "path": str(issue.path),
            "message": f"string {relation} the schema {keyword} at this path",
            "observed_characters": observed.get("characters"),
            "observed_utf8_bytes": observed.get("utf8_bytes"),
            "observed_sha256": observed.get("sha256"),
            "constraint_keyword": keyword,
            "limit_characters": constraint,
        }
        # Keep the diagnostic hash for continuity with older traces while the
        # value hash lets the Agent recognize an exact repeated bad chunk.
        if len(encoded) > _MAX_LLM_SCHEMA_ISSUE_BYTES:
            safe["diagnostic_utf8_bytes"] = len(encoded)
            safe["diagnostic_sha256"] = sha256_bytes(encoded)
        return safe
    if len(encoded) <= _MAX_LLM_SCHEMA_ISSUE_BYTES:
        return {"path": str(issue.path), "message": message}
    if message.endswith(" is too long"):
        summary = "value exceeds the schema maxLength at this path"
    elif message.endswith(" is too short"):
        summary = "value is below the schema minLength at this path"
    else:
        summary = "oversized schema diagnostic omitted; inspect the constraint at this path"
    return {
        "path": str(issue.path),
        "message": summary,
        "diagnostic_utf8_bytes": len(encoded),
        "diagnostic_sha256": sha256_bytes(encoded),
    }


def _execute_with_deadline(handler: ToolHandler, arguments: dict[str, Any], timeout_s: float) -> Any:
    if timeout_s <= 0 or timeout_s > 30 or not isinstance(timeout_s, (int, float)):
        raise ValueError("tool timeout_s must be in (0, 30]")
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        # P0 runs Agents on the main thread on POSIX.  Refuse to pretend a
        # non-interruptible fallback is a hard deadline.
        raise RuntimeError("hard tool deadlines require the POSIX main thread")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def deadline_handler(_: int, __: object) -> None:
        raise ToolDeadlineExceeded(f"tool exceeded {timeout_s:g}s wall-clock deadline")

    signal.signal(signal.SIGALRM, deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        return handler(arguments)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


_LEGACY_TOOL_TAG_RE = re.compile(
    r"<\s*/?\s*(?:function_calls|invoke|parameter)\b",
    flags=re.IGNORECASE,
)
_XML_MARKUP_RE = re.compile(
    r"<\s*(?:[!?/]|[A-Za-z_])[^<>]*(?:>|\Z)",
)
_XML_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}\Z")


def _embedded_react_action_spans(
    text: str,
) -> list[tuple[dict[str, Any], int, int]]:
    """Find complete JSON ReAct objects together with their source spans."""

    decoder = json.JSONDecoder()
    actions: list[tuple[dict[str, Any], int, int]] = []
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") in {"tool", "final"}:
            actions.append((value, offset, end))
    return actions


def _embedded_react_actions(text: str) -> list[dict[str, Any]]:
    """Find every complete JSON object carrying a ReAct discriminator."""

    return [action for action, _start, _end in _embedded_react_action_spans(text)]


def _strict_json_parameter(text: str) -> Any:
    """Decode valid JSON parameter text, otherwise preserve it as a string."""

    candidate = text.strip()
    if not candidate:
        return ""

    def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject_non_json_constant(value: str) -> None:
        raise ValueError(f"non-JSON numeric constant: {value}")

    try:
        return json.loads(
            candidate,
            object_pairs_hook=object_without_duplicate_keys,
            parse_constant=reject_non_json_constant,
        )
    except (json.JSONDecodeError, ValueError):
        return candidate


def _parse_legacy_xml_tool_action(text: str) -> dict[str, Any]:
    """Normalize one tightly constrained legacy Claude XML tool invocation."""

    if _embedded_react_actions(text):
        raise ReactProtocolError(
            "legacy XML tool response must not also contain a JSON ReAct action"
        )

    root_openings = list(re.finditer(r"<function_calls\b[^<>]*>", text))
    root_closings = list(re.finditer(r"</function_calls\s*>", text))
    if len(root_openings) != 1 or len(root_closings) != 1:
        raise ReactProtocolError(
            "legacy XML tool response must contain exactly one complete function_calls block"
        )
    opening = root_openings[0]
    closing = root_closings[0]
    if closing.start() <= opening.end():
        raise ReactProtocolError("legacy XML function_calls block is malformed")

    xml_block = text[opening.start() : closing.end()]
    outside = text[: opening.start()] + text[closing.end() :]
    if _XML_MARKUP_RE.search(outside):
        raise ReactProtocolError(
            "legacy XML tool response contains extra XML markup outside function_calls"
        )
    if "<!" in xml_block or "<?" in xml_block:
        raise ReactProtocolError(
            "legacy XML tool response must not contain declarations, comments, or CDATA"
        )

    try:
        root = ET.fromstring(xml_block)
    except ET.ParseError as exc:
        raise ReactProtocolError(f"legacy XML tool response is malformed: {exc}") from exc

    if root.tag != "function_calls" or root.attrib:
        raise ReactProtocolError(
            "legacy XML root must be function_calls with no attributes"
        )
    if root.text and root.text.strip():
        raise ReactProtocolError("legacy XML function_calls contains unexpected text")
    invokes = list(root)
    if len(invokes) != 1 or invokes[0].tag != "invoke":
        raise ReactProtocolError(
            "legacy XML function_calls must contain exactly one invoke"
        )

    invoke = invokes[0]
    if set(invoke.attrib) != {"name"}:
        raise ReactProtocolError("legacy XML invoke must have only a name attribute")
    tool_name = invoke.attrib["name"]
    if not _XML_NAME_RE.fullmatch(tool_name):
        raise ReactProtocolError("legacy XML invoke name is not a safe tool identifier")
    if invoke.text and invoke.text.strip():
        raise ReactProtocolError("legacy XML invoke contains unexpected text")
    if invoke.tail and invoke.tail.strip():
        raise ReactProtocolError("legacy XML invoke contains unexpected trailing text")

    arguments: dict[str, Any] = {}
    for parameter in invoke:
        if parameter.tag != "parameter" or set(parameter.attrib) != {"name"}:
            raise ReactProtocolError(
                "legacy XML invoke may contain only parameter elements with one name attribute"
            )
        parameter_name = parameter.attrib["name"]
        if not _XML_NAME_RE.fullmatch(parameter_name):
            raise ReactProtocolError(
                "legacy XML parameter name is not a safe identifier"
            )
        if parameter_name in arguments:
            raise ReactProtocolError(
                f"legacy XML invoke contains duplicate parameter: {parameter_name}"
            )
        if list(parameter):
            raise ReactProtocolError(
                f"legacy XML parameter {parameter_name} contains an extra tag"
            )
        if parameter.tail and parameter.tail.strip():
            raise ReactProtocolError(
                "legacy XML invoke contains unexpected text between parameters"
            )
        arguments[parameter_name] = _strict_json_parameter(parameter.text or "")

    return {"type": "tool", "tool": tool_name, "arguments": arguments}


def _parse_react_action_with_source(text: str) -> tuple[dict[str, Any], str]:
    candidate = text.strip()
    stripped_single_fence = False
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
            stripped_single_fence = True
        candidate = "\n".join(lines).strip()
    try:
        action = json.loads(candidate)
        source = "json_fenced_single_action" if stripped_single_fence else "json"
    except json.JSONDecodeError as direct_error:
        # A provider may report a normal stop while omitting only the outermost
        # JSON object's final closer.  Accept exactly that one mechanically
        # recoverable form: every string and nested array/object must already
        # be complete, and the repaired value still goes through the ordinary
        # ReAct discriminator and tool-input Schema checks.  The raw response
        # remains unchanged in the audit trace and normalization is recorded.
        stack: list[str] = []
        in_string = False
        escaped = False
        structurally_invalid = False
        for character in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in "[{":
                stack.append(character)
            elif character in "]}":
                expected = "[" if character == "]" else "{"
                if not stack or stack[-1] != expected:
                    structurally_invalid = True
                    break
                stack.pop()
        if (
            not structurally_invalid
            and not in_string
            and not escaped
            and stack == ["{"]
            and candidate.startswith("{")
        ):
            try:
                action = json.loads(candidate + "}")
                source = "json_single_missing_root_closer"
            except json.JSONDecodeError:
                action = None
        else:
            action = None

        if action is not None:
            pass
        elif _LEGACY_TOOL_TAG_RE.search(text):
            action = _parse_legacy_xml_tool_action(text)
            source = "legacy_xml_single_tool"
        # Some providers occasionally wrap the single requested action in one
        # fenced block despite the protocol instruction. Accept exactly one
        # such block, but reject ambiguous/multi-block prose.
        else:
            fenced = list(re.finditer(
                r"```(?:json)?\s*([\s\S]*?)\s*```",
                text,
                flags=re.IGNORECASE,
            ))
            if len(fenced) == 1:
                fenced_match = fenced[0]
                prefix = text[: fenced_match.start()].strip()
                suffix = text[fenced_match.end() :].strip()
                if suffix or re.search(r"[{}\[\]]|```", prefix):
                    raise ReactProtocolError(
                        "model response is not one JSON action: fenced action has "
                        "ambiguous surrounding content"
                    )
                try:
                    action = json.loads(fenced_match.group(1).strip())
                    source = (
                        "json_fenced_with_provider_preamble"
                        if prefix
                        else "json_fenced_single_action"
                    )
                except json.JSONDecodeError as fenced_error:
                    raise ReactProtocolError(
                        f"model response fenced action is invalid JSON: {fenced_error}"
                    ) from fenced_error
            elif len(fenced) > 1:
                raise ReactProtocolError(
                    "model response is not one JSON action: multiple fenced actions"
                )
            else:
                # Also tolerate concise provider prose followed by one raw JSON
                # action. Scan for complete JSON objects and accept only when
                # exactly one has the ReAct discriminator; nested argument objects
                # do not qualify and multiple actions remain an error.
                embedded = _embedded_react_action_spans(text)
                if len(embedded) != 1:
                    raise ReactProtocolError(
                        f"model response is not one JSON action: {direct_error}"
                    ) from direct_error
                action, start, end = embedded[0]
                prefix = text[:start].strip()
                suffix = text[end:].strip()
                if suffix or re.search(r"[{}\[\]]|```", prefix):
                    raise ReactProtocolError(
                        "model response is not one JSON action: embedded action has "
                        "ambiguous surrounding content"
                    )
                source = "json_with_provider_preamble"
    if not isinstance(action, dict) or action.get("type") not in {"tool", "final"}:
        raise ReactProtocolError("action must be an object with type='tool' or type='final'")
    return action, source


def parse_react_action(text: str) -> dict[str, Any]:
    action, _source = _parse_react_action_with_source(text)
    return action


class ReactAgent:
    """Generic engine. Role subclasses own lifecycle and isolation policy."""

    def __init__(
        self,
        *,
        agent_id: str,
        role: str,
        client: ModelClient,
        system_prompt: str,
        tools: Mapping[str, ToolSpec],
        budget: BudgetCounter,
        trace_path: str | Path,
        session_id: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.client = client
        self.system_prompt = system_prompt
        self.tools = dict(tools)
        self.budget = budget
        self.trace = JsonlTrace(trace_path)
        self.session_id = session_id or uuid.uuid4().hex
        self.episode_id = uuid.uuid4().hex
        self.history: list[ModelMessage] = []
        self.phase = "initial"
        # ``history`` is the complete append-only Agent transcript.  A context
        # epoch changes only the projection sent to the stateless provider; it
        # never deletes transcript entries or changes Agent/session identity.
        self._context_epoch = 0
        self._context_floor: int | None = None
        self._request_context_limit_bytes: int | None = None
        self._reset_accounting_window()
        self._append_system_prompt()

    def _reset_accounting_window(self) -> None:
        self._agent_turns = 0
        self._provider_http_attempts = 0
        self._provider_retries = 0
        self._usage_samples: list[ModelUsage] = []
        # This is an observable action audit, not a model-thought transcript.
        # It is deliberately small enough to carry into the next repair epoch
        # and into private Evolution review without copying source, prompts, or
        # validation measurements.
        self._window_tool_process: list[dict[str, Any]] = []
        self._window_protocol_errors = 0
        self._window_truncated_responses = 0
        self._window_provider_failures = 0
        self._window_final_rejections = 0
        self._window_last_action = "none"

    @staticmethod
    def _process_target(arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Return bounded, non-content locators for one tool invocation."""

        target: dict[str, Any] = {}
        for key in ("path", "symbol", "profile_id", "capability_id"):
            value = arguments.get(key)
            if isinstance(value, str) and _SAFE_PROCESS_TARGET_RE.fullmatch(value):
                target[key] = value
        line_number = arguments.get("line_number")
        if (
            isinstance(line_number, int)
            and not isinstance(line_number, bool)
            and 1 <= line_number <= 1_000_000
        ):
            target["line_number"] = line_number
        for key in ("expected_sha256", "sha256"):
            value = arguments.get(key)
            if (
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
            ):
                target[key] = value
        query = arguments.get("query")
        if isinstance(query, str):
            encoded = query.encode("utf-8")
            target["query"] = {
                "utf8_bytes": len(encoded),
                "sha256": sha256_bytes(encoded),
            }
        return target

    def process_audit(self) -> dict[str, Any]:
        """Expose a privacy-safe summary of the current accounting window.

        This makes cross-round repair continuity scientifically auditable while
        intentionally excluding raw model prose, source fragments, tool return
        bodies, tracebacks, and physical measurements.
        """

        successful_writes = sum(
            1
            for item in self._window_tool_process
            if item["operation"] == "write" and item["outcome"] == "ok"
        )
        last_tool = (
            None if not self._window_tool_process else self._window_tool_process[-1]
        )
        unfinished_read = (
            copy.deepcopy(last_tool)
            if isinstance(last_tool, Mapping)
            and last_tool.get("operation") == "read"
            else None
        )
        return {
            "schema_version": "robot_capability.repair_process_audit.v1",
            "tool_call_count": len(self._window_tool_process),
            "successful_write_calls": successful_writes,
            "protocol_error_count": self._window_protocol_errors,
            "truncated_response_count": self._window_truncated_responses,
            "provider_failure_count": self._window_provider_failures,
            "final_rejection_count": self._window_final_rejections,
            "last_action": self._window_last_action,
            "unfinished_read": unfinished_read,
            "tool_calls": copy.deepcopy(self._window_tool_process),
            "privacy": {
                "raw_model_text_included": False,
                "raw_tool_arguments_included": False,
                "raw_tool_results_included": False,
                "raw_source_included": False,
            },
        }

    @property
    def context_epoch(self) -> int:
        return self._context_epoch

    @staticmethod
    def _client_counter(snapshot: Mapping[str, Any], name: str) -> int:
        value = snapshot.get(name, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    def _record_client_delta(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> tuple[int, int, int]:
        requests = max(
            0,
            self._client_counter(after, "logical_requests")
            - self._client_counter(before, "logical_requests"),
        )
        attempts = max(
            0,
            self._client_counter(after, "provider_http_attempts")
            - self._client_counter(before, "provider_http_attempts"),
        )
        retries = max(
            0,
            self._client_counter(after, "provider_retries")
            - self._client_counter(before, "provider_retries"),
        )
        self._provider_http_attempts += attempts
        self._provider_retries += retries
        return requests, attempts, retries

    def accounting_state(self) -> dict[str, Any]:
        """Return the current phase/task window without model payloads."""

        client_audit = self.client.audit_snapshot()
        provider_policy = dict(client_audit.get("attempt_budget_policy", {}))
        per_request_limit = provider_policy.get("limit_per_request", 0)
        if not isinstance(per_request_limit, int) or per_request_limit < 0:
            per_request_limit = 0
        window_limit = self.budget.limit * per_request_limit
        provider_policy["agent_window"] = {
            "limit": window_limit,
            "used": self._provider_http_attempts,
            "remaining": max(0, window_limit - self._provider_http_attempts),
            "derivation": "agent_turn_limit * provider_limit_per_request",
        }
        return {
            "schema_version": "robot_capability.agent_model_accounting.v1",
            "model": self.client.model,
            "client_kind": self.client.client_kind,
            "scripted": self.client.scripted,
            "agent_turns": self._agent_turns,
            "model_calls": self._agent_turns,
            "agent_turn_budget": self.budget.to_dict(),
            "provider_http_attempts": self._provider_http_attempts,
            "provider_retries": self._provider_retries,
            "provider_attempt_budget_policy": provider_policy,
            "usage": summarize_model_usage(tuple(self._usage_samples)).to_dict(),
        }

    def accounting_result(self, *, status: str, content: str = "") -> AgentResult:
        """Snapshot the current accounting window, including failed runs.

        ``run()`` normally returns this information only after a valid final
        action.  Generation needs the same payload when an initial Stage 2 or
        repair window exhausts its independent budget (or the provider fails),
        so terminal reports never lose the calls already made.
        """

        accounting = self.accounting_state()
        return AgentResult(
            status=status,
            content=content,
            model_calls=self._agent_turns,
            agent_turns=self._agent_turns,
            provider_http_attempts=self._provider_http_attempts,
            provider_retries=self._provider_retries,
            client_kind=self.client.client_kind,
            scripted=self.client.scripted,
            agent_turn_budget=accounting["agent_turn_budget"],
            provider_attempt_budget_policy=accounting[
                "provider_attempt_budget_policy"
            ],
            usage=accounting["usage"],
            session_id=self.session_id,
            episode_id=self.episode_id,
        )

    def _append_system_prompt(self) -> None:
        protocol = {
            "response_protocol": {
                "tool": {"type": "tool", "tool": "tool_name", "arguments": {}},
                "final": {"type": "final", "content": "result"},
            },
            "available_tools": [tool.prompt_view() for tool in self.tools.values()],
        }
        self.history.append(
            ModelMessage(
                "system",
                self.system_prompt
                + "\nReturn exactly one JSON action per response. Do not use XML, "
                + "<function_calls>, native tool-call syntax, prose, or multiple actions.\n"
                + json.dumps(protocol, ensure_ascii=False, sort_keys=True),
            )
        )

    @staticmethod
    def _context_metrics(messages: tuple[ModelMessage, ...]) -> dict[str, Any]:
        serialized = canonical_json([message.to_dict() for message in messages]).encode(
            "utf-8"
        )
        return {
            "message_count": len(messages),
            "utf8_bytes": len(serialized),
            "sha256": sha256_bytes(serialized),
        }

    def _request_messages(self) -> tuple[ModelMessage, ...]:
        if self._context_floor is None:
            return tuple(self.history)
        # The original system contract is immutable and remains first.  Every
        # message from the current epoch (checkpoint, transition, instruction,
        # actions, and observations) follows it in append order.
        return (self.history[0], *self.history[self._context_floor :])

    def _begin_context_epoch(
        self,
        checkpoint: Mapping[str, Any],
        *,
        max_request_context_bytes: int,
    ) -> None:
        if (
            isinstance(max_request_context_bytes, bool)
            or not isinstance(max_request_context_bytes, int)
            or max_request_context_bytes <= 0
        ):
            raise ValueError("max_request_context_bytes must be a positive integer")
        if not isinstance(checkpoint, Mapping):
            raise TypeError("repair context checkpoint must be a mapping")
        if not self.history or self.history[0].role != "system":
            raise RuntimeError("context epochs require the original system message")

        prior_history = tuple(self.history)
        prior_history_sha256 = sha256_json(
            [message.to_dict() for message in prior_history]
        )
        self._context_epoch += 1
        self._context_floor = len(self.history)
        self._request_context_limit_bytes = max_request_context_bytes
        checkpoint_message = ModelMessage(
            "user",
            canonical_json(
                {
                    "repair_context_epoch": {
                        "schema_version": "robot_capability.repair_context_epoch.v1",
                        "epoch": self._context_epoch,
                        "continuity": {
                            "agent_id": self.agent_id,
                            "session_id": self.session_id,
                            "episode_id": self.episode_id,
                            "same_agent_session": True,
                            "prior_history_message_count": len(prior_history),
                            "prior_history_sha256": prior_history_sha256,
                        },
                        "checkpoint": dict(checkpoint),
                    }
                }
            ),
        )
        self.history.append(checkpoint_message)
        self.trace.append(
            {
                "event": "context_epoch_started",
                "agent_id": self.agent_id,
                "role": self.role,
                "session_id": self.session_id,
                "episode_id": self.episode_id,
                "phase": self.phase,
                "context_epoch": self._context_epoch,
                "prior_history_message_count": len(prior_history),
                "prior_history_sha256": prior_history_sha256,
                "checkpoint_message_sha256": sha256_json(
                    checkpoint_message.to_dict()
                ),
                "full_history_message_count": len(self.history),
                "request_context_floor": self._context_floor,
                "max_request_context_bytes": max_request_context_bytes,
            }
        )

    def set_phase(
        self,
        phase: str,
        *,
        tools: Mapping[str, ToolSpec] | None = None,
        instruction: str | None = None,
    ) -> None:
        previous = self.phase
        self.phase = phase
        if tools is not None:
            self.tools = dict(tools)
        transition = {
            "phase_transition": {"from": previous, "to": phase},
            "available_tools": [tool.prompt_view() for tool in self.tools.values()],
        }
        if instruction:
            transition["instruction"] = instruction
        self.history.append(ModelMessage("user", json.dumps(transition, ensure_ascii=False)))
        self.trace.append(
            {
                "event": "phase_transition",
                "agent_id": self.agent_id,
                "role": self.role,
                "session_id": self.session_id,
                "episode_id": self.episode_id,
                "previous": previous,
                "current": phase,
            }
        )

    def run(
        self,
        instruction: str,
        *,
        final_guard: Callable[[], str | None] | None = None,
    ) -> AgentResult:
        self.history.append(ModelMessage("user", instruction))
        while True:
            request_messages = self._request_messages()
            context_metrics = self._context_metrics(request_messages)
            context_limit = self._request_context_limit_bytes
            if (
                context_limit is not None
                and context_metrics["utf8_bytes"] > context_limit
            ):
                self.trace.append(
                    {
                        "event": "model_request_context_rejected",
                        "agent_id": self.agent_id,
                        "role": self.role,
                        "session_id": self.session_id,
                        "episode_id": self.episode_id,
                        "phase": self.phase,
                        "accounting_window": self.budget.name,
                        "context_epoch": self._context_epoch,
                        "full_history_message_count": len(self.history),
                        "request_message_count": context_metrics["message_count"],
                        "request_context_bytes": context_metrics["utf8_bytes"],
                        "request_context_sha256": context_metrics["sha256"],
                        "max_request_context_bytes": context_limit,
                    }
                )
                raise RequestContextLimitExceeded(
                    "projected model request context exceeds configured byte limit: "
                    f"bytes={context_metrics['utf8_bytes']}, limit={context_limit}"
                )
            remaining = self.budget.consume()
            self._agent_turns += 1
            agent_turn = self._agent_turns
            before = self.client.audit_snapshot()
            self.trace.append(
                {
                    "event": "model_request",
                    "agent_id": self.agent_id,
                    "role": self.role,
                    "session_id": self.session_id,
                    "episode_id": self.episode_id,
                    "phase": self.phase,
                    "model": self.client.model,
                    "client_kind": self.client.client_kind,
                    "scripted": self.client.scripted,
                    "accounting_window": self.budget.name,
                    "agent_turn_limit": self.budget.limit,
                    "agent_turn": agent_turn,
                    "remaining_agent_turns": remaining,
                    "context_epoch": self._context_epoch,
                    "context_compacted": self._context_floor is not None,
                    "full_history_message_count": len(self.history),
                    "request_message_count": context_metrics["message_count"],
                    "request_context_bytes": context_metrics["utf8_bytes"],
                    "request_context_sha256": context_metrics["sha256"],
                }
            )
            try:
                response = self.client.complete(request_messages)
            except BaseException as exc:
                self._window_provider_failures += 1
                self._window_last_action = "provider_failure"
                after = self.client.audit_snapshot()
                logical_requests, provider_attempts, provider_retries = (
                    self._record_client_delta(before, after)
                )
                model_error_retryable = (
                    isinstance(exc, ModelAPIError) and exc.retryable
                )
                retryable_model_failure = (
                    model_error_retryable
                    and logical_requests == 1
                    and provider_attempts == 1
                    and provider_retries == 0
                )
                failure_kind = (
                    exc.failure_kind
                    if isinstance(exc, ModelAPIError)
                    else "non_model_client_error"
                )
                self.trace.append(
                    {
                        "event": "model_request_failed",
                        "agent_id": self.agent_id,
                        "role": self.role,
                        "session_id": self.session_id,
                        "episode_id": self.episode_id,
                        "phase": self.phase,
                        "model": self.client.model,
                        "client_kind": self.client.client_kind,
                        "scripted": self.client.scripted,
                        "accounting_window": self.budget.name,
                        "agent_turn_limit": self.budget.limit,
                        "agent_turn": agent_turn,
                        "context_epoch": self._context_epoch,
                        "request_message_count": context_metrics["message_count"],
                        "request_context_bytes": context_metrics["utf8_bytes"],
                        "request_context_sha256": context_metrics["sha256"],
                        "logical_requests_this_turn": logical_requests,
                        "provider_http_attempts_this_turn": provider_attempts,
                        "provider_retries_this_turn": provider_retries,
                        "model_error_retryable": model_error_retryable,
                        "retryable_with_another_agent_turn": retryable_model_failure,
                        "failure_kind": failure_kind,
                        # Do not serialize exception text: a transport/provider
                        # error can contain a raw body or other sensitive data.
                        "error_type": type(exc).__name__,
                    }
                )
                if retryable_model_failure:
                    # This is deliberately not a transparent provider retry.
                    # The failed HTTP request above already consumed this Agent
                    # turn.  A following request, if budget remains, consumes a
                    # new declared turn and is independently visible in trace
                    # and provider-attempt accounting.  With no remaining turn,
                    # the next loop iteration raises BudgetExceeded normally.
                    self.trace.append(
                        {
                            "event": "transient_model_failure_continued",
                            "agent_id": self.agent_id,
                            "role": self.role,
                            "session_id": self.session_id,
                            "episode_id": self.episode_id,
                            "phase": self.phase,
                            "accounting_window": self.budget.name,
                            "failed_agent_turn": agent_turn,
                            "replay_request_context_sha256": context_metrics[
                                "sha256"
                            ],
                            "remaining_agent_turns": remaining,
                            "next_request_uses_new_agent_turn": remaining > 0,
                            "provider_retry": False,
                            "failure_kind": failure_kind,
                        }
                    )
                    continue
                raise
            after = self.client.audit_snapshot()
            logical_requests, provider_attempts, provider_retries = self._record_client_delta(
                before, after
            )
            self._usage_samples.append(response.usage)
            self.trace.append(
                {
                    "event": "model_response",
                    "agent_id": self.agent_id,
                    "role": self.role,
                    "session_id": self.session_id,
                    "episode_id": self.episode_id,
                    "phase": self.phase,
                    "model": self.client.model,
                    "client_kind": self.client.client_kind,
                    "scripted": self.client.scripted,
                    "accounting_window": self.budget.name,
                    "agent_turn_limit": self.budget.limit,
                    "agent_turn": agent_turn,
                    "model_call": agent_turn,
                    "remaining_agent_turns": remaining,
                    "remaining_calls": remaining,
                    "context_epoch": self._context_epoch,
                    "request_message_count": context_metrics["message_count"],
                    "request_context_bytes": context_metrics["utf8_bytes"],
                    "request_context_sha256": context_metrics["sha256"],
                    "logical_requests_this_turn": logical_requests,
                    "provider_http_attempts_this_turn": provider_attempts,
                    "provider_retries_this_turn": provider_retries,
                    "completion": {
                        "stop_reason": response.stop_reason,
                        "output_truncated": response.output_truncated,
                        "requested_max_tokens": response.requested_max_tokens,
                    },
                    "response_text": response.text,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cost": response.usage.cost,
                        "remaining_budget": response.usage.remaining_budget,
                    },
                }
            )
            if response.output_truncated is True:
                # The raw provider text remains in the append-only audit trace,
                # but it is not trustworthy as a complete ReAct action and can
                # be very large. Keep only a content-addressed placeholder in
                # model history and never parse or execute the partial payload.
                response_bytes = response.text.encode("utf-8")
                placeholder = {
                    "rejected_model_response": {
                        "reason": "output_truncated",
                        "utf8_bytes": len(response_bytes),
                        "sha256": sha256_bytes(response_bytes),
                    }
                }
                self.history.append(
                    ModelMessage("assistant", canonical_json(placeholder))
                )
                self._window_protocol_errors += 1
                self._window_truncated_responses += 1
                self._window_last_action = "truncated_response"
                observation = {
                    "observation": "protocol_error",
                    "error": (
                        "provider output was truncated; no action was parsed "
                        "or executed"
                    ),
                }
                self.history.append(
                    ModelMessage("user", canonical_json(observation))
                )
                self.trace.append(
                    {
                        "event": "model_response_rejected",
                        "agent_id": self.agent_id,
                        "role": self.role,
                        "session_id": self.session_id,
                        "episode_id": self.episode_id,
                        "phase": self.phase,
                        "accounting_window": self.budget.name,
                        "agent_turn": agent_turn,
                        "reason": "output_truncated",
                        "response_utf8_bytes": len(response_bytes),
                        "response_sha256": sha256_bytes(response_bytes),
                    }
                )
                continue
            self.history.append(ModelMessage("assistant", response.text))
            try:
                action, action_source = _parse_react_action_with_source(response.text)
            except ReactProtocolError as exc:
                self._window_protocol_errors += 1
                self._window_last_action = "protocol_error"
                self.history.append(
                    ModelMessage(
                        "user",
                        json.dumps({"observation": "protocol_error", "error": str(exc)}),
                    )
                )
                self.trace.append(
                    {
                        "event": "model_response_rejected",
                        "agent_id": self.agent_id,
                        "role": self.role,
                        "session_id": self.session_id,
                        "episode_id": self.episode_id,
                        "phase": self.phase,
                        "accounting_window": self.budget.name,
                        "agent_turn": agent_turn,
                        "reason": "react_protocol_error",
                        "response_utf8_bytes": len(response.text.encode("utf-8")),
                        "response_sha256": sha256_bytes(
                            response.text.encode("utf-8")
                        ),
                    }
                )
                continue
            if action_source != "json":
                self.trace.append(
                    {
                        "event": "model_action_normalized",
                        "agent_id": self.agent_id,
                        "role": self.role,
                        "session_id": self.session_id,
                        "episode_id": self.episode_id,
                        "phase": self.phase,
                        "accounting_window": self.budget.name,
                        "agent_turn": agent_turn,
                        "source": action_source,
                        "tool": action.get("tool"),
                    }
                )
            if action["type"] == "final":
                rejection = None if final_guard is None else final_guard()
                if rejection:
                    self._window_final_rejections += 1
                    self._window_last_action = "final_rejected"
                    observation = {
                        "observation": "final_rejected",
                        "error": rejection,
                    }
                    self.history.append(
                        ModelMessage(
                            "user",
                            json.dumps(observation, ensure_ascii=False),
                        )
                    )
                    self.trace.append(
                        {
                            "event": "final_rejected",
                            "agent_id": self.agent_id,
                            "role": self.role,
                            "session_id": self.session_id,
                            "episode_id": self.episode_id,
                            "phase": self.phase,
                            "accounting_window": self.budget.name,
                            "reason": rejection,
                        }
                    )
                    continue
                content = str(action.get("content", ""))
                self._window_last_action = "final"
                accounting = self.accounting_state()
                return AgentResult(
                    status="completed",
                    content=content,
                    model_calls=self._agent_turns,
                    agent_turns=self._agent_turns,
                    provider_http_attempts=self._provider_http_attempts,
                    provider_retries=self._provider_retries,
                    client_kind=self.client.client_kind,
                    scripted=self.client.scripted,
                    agent_turn_budget=accounting["agent_turn_budget"],
                    provider_attempt_budget_policy=accounting[
                        "provider_attempt_budget_policy"
                    ],
                    usage=accounting["usage"],
                    session_id=self.session_id,
                    episode_id=self.episode_id,
                )
            name = action.get("tool")
            arguments = action.get("arguments", {})
            observation = self._execute_tool(name, arguments)
            self._window_last_action = "tool"
            self.history.append(ModelMessage("user", json.dumps({"observation": observation}, ensure_ascii=False)))
            # Stage 1 has a hard three-request ceiling. If the last permitted
            # request successfully corrects/submits the artifact, the accepted
            # tool result is the terminal condition; do not require a fourth,
            # semantically empty `final` request.
            if (
                isinstance(self, GenerationReActAgent)
                and self.phase == "stage1"
                and name == "submit_stage1"
                and observation.get("ok") is True
                and isinstance(observation.get("result"), Mapping)
                and observation["result"].get("accepted") is True
                and self.budget.remaining == 0
            ):
                accounting = self.accounting_state()
                return AgentResult(
                    status="completed",
                    content="Stage 1 artifact accepted on the final allowed request.",
                    model_calls=self._agent_turns,
                    agent_turns=self._agent_turns,
                    provider_http_attempts=self._provider_http_attempts,
                    provider_retries=self._provider_retries,
                    client_kind=self.client.client_kind,
                    scripted=self.client.scripted,
                    agent_turn_budget=accounting["agent_turn_budget"],
                    provider_attempt_budget_policy=accounting[
                        "provider_attempt_budget_policy"
                    ],
                    usage=accounting["usage"],
                    session_id=self.session_id,
                    episode_id=self.episode_id,
                )

    def _execute_tool(self, name: Any, arguments: Any) -> dict[str, Any]:
        if not isinstance(name, str) or name not in self.tools:
            result = {"ok": False, "error": f"tool not allowed in {self.phase}: {name!r}"}
        elif not isinstance(arguments, dict):
            result = {"ok": False, "error": "tool arguments must be an object"}
        else:
            issues = validate_json_schema(
                arguments,
                self.tools[name].input_schema,
                instance_path="$.arguments",
            )
            if issues:
                result = {
                    "ok": False,
                    "error": "tool arguments failed JSON Schema validation",
                    "issues": [_llm_safe_schema_issue(issue) for issue in issues],
                }
            else:
                try:
                    value = _execute_with_deadline(
                        self.tools[name].handler,
                        arguments,
                        self.tools[name].timeout_s,
                    )
                    result = {"ok": True, "result": value}
                except ToolDeadlineExceeded as exc:
                    result = {"ok": False, "error": str(exc), "timed_out": True}
                except Exception as exc:  # tool failures are observations, not Agent crashes
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        safe_name = (
            name
            if isinstance(name, str) and _SAFE_PROCESS_TARGET_RE.fullmatch(name)
            else "invalid_tool"
        )
        operation = (
            "read"
            if safe_name in _PROCESS_READ_TOOLS
            else "write"
            if safe_name in _PROCESS_WRITE_TOOLS
            else "check"
            if safe_name in _PROCESS_CHECK_TOOLS
            else "other"
        )
        if result.get("ok") is True:
            outcome = "ok"
        elif result.get("timed_out") is True:
            outcome = "timeout"
        elif result.get("error") == "tool arguments failed JSON Schema validation":
            outcome = "schema_rejected"
        else:
            outcome = "error"
        process_entry = {
            "ordinal": len(self._window_tool_process) + 1,
            "agent_turn": self._agent_turns,
            "tool": safe_name,
            "operation": operation,
            "outcome": outcome,
            "target": (
                self._process_target(arguments)
                if isinstance(arguments, Mapping)
                else {}
            ),
            "arguments_sha256": (
                _safe_process_digest(arguments)
                if isinstance(arguments, Mapping)
                else None
            ),
            "result_sha256": _safe_process_digest(result),
        }
        self._window_tool_process.append(process_entry)
        self.trace.append(
            {
                "event": "tool_result",
                "agent_id": self.agent_id,
                "role": self.role,
                "session_id": self.session_id,
                "episode_id": self.episode_id,
                "phase": self.phase,
                "tool": name,
                "result": result,
            }
        )
        return result


class GenerationReActAgent(ReactAgent):
    """One persistent Agent spanning Stage 1, Stage 2, and repair."""

    def begin_stage1(self, instruction: str) -> AgentResult:
        if self.phase != "initial":
            raise RuntimeError("Stage 1 can only begin from the initial phase")
        self.set_phase("stage1")
        return self.run(instruction)

    def continue_stage2(
        self,
        instruction: str,
        *,
        tools: Mapping[str, ToolSpec],
        stage2_budget: BudgetCounter,
    ) -> AgentResult:
        if self.phase != "stage1":
            raise RuntimeError("Stage 2 requires the existing Stage 1 Agent session")
        self.budget = stage2_budget
        self._reset_accounting_window()
        self.set_phase("stage2", tools=tools, instruction="Stage 1 is frozen; implement it without changing it.")
        return self.run(instruction)

    def resume_repair(
        self,
        feedback: dict[str, Any],
        *,
        repair_budget: BudgetCounter,
        context_checkpoint: Mapping[str, Any],
        max_request_context_bytes: int,
        instruction: str = "",
        final_guard: Callable[[], str | None] | None = None,
        tools: Mapping[str, ToolSpec] | None = None,
    ) -> AgentResult:
        if self.phase not in {"stage2", "repair"}:
            raise RuntimeError("repair requires the existing Stage 2 Agent session")
        # A repair round is not charged to the initial Stage 2 allowance.  It
        # keeps the same session/history/workspace but owns a fresh, bounded
        # accounting window.
        self.budget = repair_budget
        self._reset_accounting_window()
        self._begin_context_epoch(
            context_checkpoint,
            max_request_context_bytes=max_request_context_bytes,
        )
        self.set_phase(
            "repair",
            tools=tools,
            instruction=(
                "Repair implementation only; public Stage 1 semantics remain frozen."
            ),
        )
        structured_feedback = json.dumps(
            {"validation_feedback": feedback}, ensure_ascii=False
        )
        repair_instruction = instruction.strip()
        if repair_instruction:
            repair_instruction += "\n\n"
        return self.run(
            repair_instruction + structured_feedback,
            final_guard=final_guard,
        )


class DemoReActAgent(ReactAgent):
    """Separate downstream Consumer Agent; never receives Generation state."""

    def reset_task_episode(
        self,
        *,
        task_id: str,
        tools: Mapping[str, ToolSpec],
        call_limit: int,
        trace_path: str | Path,
    ) -> None:
        self.tools = dict(tools)
        self.budget = BudgetCounter(f"demo:{task_id}", call_limit)
        self.trace = JsonlTrace(trace_path)
        self.session_id = uuid.uuid4().hex
        self.episode_id = uuid.uuid4().hex
        self.phase = "demo"
        self.history = []
        self._reset_accounting_window()
        self._append_system_prompt()
        self.trace.append(
            {
                "event": "demo_episode_reset",
                "agent_id": self.agent_id,
                "role": self.role,
                "session_id": self.session_id,
                "episode_id": self.episode_id,
                "task_id": task_id,
            }
        )

    def run_task(self, instruction: str) -> AgentResult:
        if self.phase != "demo":
            raise RuntimeError("reset_task_episode must be called before each Demo task")
        return self.run(instruction)
