"""Deterministic bounded projection for tool-using model conversations."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_DRIVER_SOURCE_TOOLS = frozenset({"write_driver", "check_driver"})


@dataclass(frozen=True)
class ContextProjection:
    messages: tuple[dict[str, Any], ...]
    stats: dict[str, Any]


@dataclass
class _ProjectedGroup:
    messages: list[dict[str, Any]]
    summary: dict[str, Any]


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _decode_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return copy.deepcopy(dict(decoded)) if isinstance(decoded, Mapping) else None


def _bounded_text(value: Any, limit: int = 800) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 16] + "...[truncated]"


def _append_message(messages: list[dict[str, Any]], message: Mapping[str, Any]) -> None:
    current = dict(message)
    role = current.get("role")
    content = current.get("content")
    if (
        messages
        and role in {"user", "assistant"}
        and messages[-1].get("role") == role
        and isinstance(messages[-1].get("content"), str)
        and isinstance(content, str)
    ):
        messages[-1]["content"] += "\n\n" + content
        return
    messages.append(current)


class AgentContextManager:
    """Project full canonical history into a bounded model-facing text history."""

    def __init__(self, *, history_char_budget: int = 80000, recent_groups: int = 3):
        if history_char_budget < 8192:
            raise ValueError("history_char_budget must be at least 8192")
        if recent_groups < 1:
            raise ValueError("recent_groups must be positive")
        self.history_char_budget = history_char_budget
        self.recent_groups = recent_groups

    @staticmethod
    def _tool_calls(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, Mapping)]

    @staticmethod
    def _call_parts(call: Mapping[str, Any]) -> tuple[str, str | None, Any]:
        function = call.get("function")
        if not isinstance(function, Mapping):
            return str(call.get("id") or ""), None, None
        name = function.get("name")
        return (
            str(call.get("id") or ""),
            str(name) if isinstance(name, str) else None,
            function.get("arguments"),
        )

    @staticmethod
    def _observation_payload(content: Any) -> dict[str, Any] | None:
        return _decode_object(content)

    def _latest_driver_snapshot(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any] | None:
        calls: dict[str, tuple[str | None, Any]] = {}
        snapshot: dict[str, Any] | None = None
        for message in messages:
            if message.get("role") == "assistant":
                for call in self._tool_calls(message):
                    call_id, name, raw_arguments = self._call_parts(call)
                    calls[call_id] = (name, raw_arguments)
                continue
            if message.get("role") != "tool":
                continue
            call_id = str(message.get("tool_call_id") or "")
            name, _arguments = calls.get(call_id, (None, None))
            payload = self._observation_payload(message.get("content"))
            if payload is None:
                continue
            result = payload.get("result")
            revision = result.get("revision") if isinstance(result, Mapping) else None
            if name in _DRIVER_SOURCE_TOOLS and payload.get("ok") is True:
                arguments = _decode_object(calls.get(call_id, (None, None))[1])
                source = arguments.get("source") if arguments is not None else None
                if isinstance(source, str):
                    snapshot = {
                        "source": source,
                        "source_chars": len(source),
                        "revision": revision if isinstance(revision, int) else None,
                        "observed_via": name,
                        "tool_call_id": call_id,
                    }
            source = result.get("source") if isinstance(result, Mapping) else None
            if (
                name == "read_driver"
                and payload.get("ok") is True
                and isinstance(source, str)
            ):
                snapshot = {
                    "source": source,
                    "source_chars": len(source),
                    "revision": revision if isinstance(revision, int) else None,
                    "observed_via": "read_driver",
                    "tool_call_id": call_id,
                }
        return snapshot

    @staticmethod
    def _compact_arguments(name: str | None, raw_arguments: Any) -> tuple[Any, dict[str, Any]]:
        metadata: dict[str, Any] = {
            "argument_chars": (
                len(raw_arguments)
                if isinstance(raw_arguments, str)
                else _json_chars(raw_arguments)
            )
        }
        arguments = _decode_object(raw_arguments)
        if arguments is None:
            return raw_arguments, metadata
        if name in _DRIVER_SOURCE_TOOLS and isinstance(arguments.get("source"), str):
            source = arguments.pop("source")
            metadata["source_chars"] = len(source)
            arguments.update(
                {
                    "source_chars": len(source),
                    "source_history": "omitted; current source is retained separately",
                }
            )
        if name == "run_mujoco_probe" and isinstance(arguments.get("script"), str):
            script = arguments.pop("script")
            metadata["script_chars"] = len(script)
            arguments.update(
                {
                    "script_chars": len(script),
                    "script_history": "omitted after execution",
                }
            )
        return arguments, metadata

    @staticmethod
    def _compact_observation(
        name: str | None, content: Any
    ) -> tuple[str, dict[str, Any]]:
        text = content if isinstance(content, str) else ""
        event: dict[str, Any] = {
            "result_chars": len(text),
            "result_status": "unknown",
        }
        payload = _decode_object(text)
        if payload is None:
            return text, event
        ok = payload.get("ok")
        if isinstance(ok, bool):
            event["result_status"] = "ok" if ok else "error"
        if payload.get("error") is not None:
            event["error"] = _bounded_text(payload.get("error"), 500)
        result = payload.get("result")
        if isinstance(result, Mapping):
            result = copy.deepcopy(dict(result))
            revision = result.get("revision")
            if isinstance(revision, int):
                event["revision"] = revision
            if name == "read_driver" and isinstance(result.get("source"), str):
                source = result.pop("source")
                result["source_chars"] = len(source)
                result["source_history"] = "omitted; current source is retained separately"
                event["source_chars"] = len(source)
            payload["result"] = result
            event["result_summary"] = _bounded_text(
                json.dumps(result, ensure_ascii=True, sort_keys=True), 300
            )
        return json.dumps(payload, ensure_ascii=True, sort_keys=True), event

    def _project_group(self, raw: Sequence[Mapping[str, Any]]) -> _ProjectedGroup:
        messages: list[dict[str, Any]] = []
        events: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        call_names: dict[str, str | None] = {}
        for message in raw:
            role = message.get("role")
            content = message.get("content") if isinstance(message.get("content"), str) else ""
            calls = self._tool_calls(message)
            if role == "assistant" and calls:
                requests = []
                for call in calls:
                    call_id, name, raw_arguments = self._call_parts(call)
                    projected_arguments, metadata = self._compact_arguments(
                        name, raw_arguments
                    )
                    call_names[call_id] = name
                    event = {
                        "tool_call_id": call_id,
                        "tool": name,
                        "result_status": "pending",
                        **metadata,
                    }
                    events[call_id] = event
                    order.append(call_id)
                    requests.append(
                        {
                            "tool_call_id": call_id,
                            "tool": name,
                            "arguments": projected_arguments,
                        }
                    )
                parts = [content] if content.strip() else []
                parts.append(
                    "TOOL_REQUESTS_JSON:\n"
                    + json.dumps(requests, ensure_ascii=True, sort_keys=True)
                )
                _append_message(
                    messages,
                    {"role": "assistant", "content": "\n\n".join(parts)},
                )
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                projected_content, result_metadata = self._compact_observation(
                    call_names.get(call_id), message.get("content")
                )
                event = events.setdefault(
                    call_id,
                    {
                        "tool_call_id": call_id,
                        "tool": call_names.get(call_id),
                    },
                )
                if call_id not in order:
                    order.append(call_id)
                event.update(result_metadata)
                observation = "TOOL_OBSERVATION_JSON:\n" + json.dumps(
                    {
                        "tool_call_id": call_id,
                        "content": projected_content,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
                _append_message(messages, {"role": "user", "content": observation})
                continue
            if role in {"assistant", "user"}:
                if role == "assistant" and not content.strip():
                    content = "No tool call or terminal submission was produced."
                _append_message(messages, {"role": role, "content": content})
            else:
                _append_message(messages, message)
        summary: dict[str, Any] = {
            "message_count": len(raw),
            "projected_chars": _json_chars(messages),
        }
        if order:
            summary["events"] = [events[call_id] for call_id in order]
        else:
            summary["roles"] = [str(message.get("role")) for message in raw]
        return _ProjectedGroup(messages=messages, summary=summary)

    def _project_native_group(
        self, raw: Sequence[Mapping[str, Any]]
    ) -> _ProjectedGroup:
        messages: list[dict[str, Any]] = []
        events: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        call_names: dict[str, str | None] = {}
        for message in raw:
            role = message.get("role")
            calls = self._tool_calls(message)
            if role == "assistant" and calls:
                projected_message = copy.deepcopy(dict(message))
                projected_calls = []
                for call in calls:
                    call_id, name, raw_arguments = self._call_parts(call)
                    projected_arguments, metadata = self._compact_arguments(
                        name, raw_arguments
                    )
                    call_names[call_id] = name
                    events[call_id] = {
                        "tool_call_id": call_id,
                        "tool": name,
                        "result_status": "pending",
                        **metadata,
                    }
                    order.append(call_id)
                    projected_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": (
                                    projected_arguments
                                    if isinstance(projected_arguments, str)
                                    else json.dumps(
                                        projected_arguments,
                                        ensure_ascii=True,
                                        sort_keys=True,
                                    )
                                ),
                            },
                        }
                    )
                projected_message["tool_calls"] = projected_calls
                messages.append(projected_message)
                continue
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                projected_content, result_metadata = self._compact_observation(
                    call_names.get(call_id), message.get("content")
                )
                event = events.setdefault(
                    call_id,
                    {
                        "tool_call_id": call_id,
                        "tool": call_names.get(call_id),
                    },
                )
                if call_id not in order:
                    order.append(call_id)
                event.update(result_metadata)
                projected_message = copy.deepcopy(dict(message))
                projected_message["content"] = projected_content
                messages.append(projected_message)
                continue
            messages.append(copy.deepcopy(dict(message)))
        summary: dict[str, Any] = {
            "message_count": len(raw),
            "projected_chars": _json_chars(messages),
        }
        if order:
            summary["events"] = [events[call_id] for call_id in order]
        else:
            summary["roles"] = [str(message.get("role")) for message in raw]
        return _ProjectedGroup(messages=messages, summary=summary)

    @staticmethod
    def _groups(messages: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
        groups: list[list[Mapping[str, Any]]] = []
        current: list[Mapping[str, Any]] = []
        for message in messages:
            if message.get("role") == "assistant" and current:
                groups.append(current)
                current = []
            current.append(message)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _summary_text(groups: Sequence[_ProjectedGroup]) -> str:
        if not groups:
            return ""
        return "AGENT_CONTEXT_SUMMARY_JSON:\n" + json.dumps(
            {
                "omitted_group_count": len(groups),
                "groups": [group.summary for group in groups],
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    @staticmethod
    def _snapshot_text(snapshot: Mapping[str, Any] | None) -> str:
        if snapshot is None:
            return ""
        return "CURRENT_DRIVER_SNAPSHOT_JSON:\n" + json.dumps(
            {
                "observed_via": snapshot.get("observed_via"),
                "revision": snapshot.get("revision"),
                "source": snapshot.get("source"),
                "source_chars": snapshot.get("source_chars"),
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    def _assemble(
        self,
        messages: Sequence[Mapping[str, Any]],
        projected_groups: list[_ProjectedGroup],
        *,
        mode: str,
        merge_messages: bool,
    ) -> ContextProjection:
        if not messages:
            return ContextProjection(
                messages=(),
                stats={
                    "mode": mode,
                    "history_char_budget": self.history_char_budget,
                    "original_message_count": 0,
                    "projected_message_count": 0,
                },
            )
        initial = copy.deepcopy(dict(messages[0]))
        split = max(0, len(projected_groups) - self.recent_groups)
        summarized = projected_groups[:split]
        retained = projected_groups[split:]
        snapshot = self._latest_driver_snapshot(messages)

        def projected_history_chars() -> int:
            appendix = [
                text
                for text in (
                    self._summary_text(summarized),
                    self._snapshot_text(snapshot),
                )
                if text
            ]
            return _json_chars(appendix) + sum(
                _json_chars(group.messages) for group in retained
            )

        while projected_history_chars() > self.history_char_budget and len(retained) > 1:
            summarized.append(retained.pop(0))

        appendix = [
            text
            for text in (
                self._summary_text(summarized),
                self._snapshot_text(snapshot),
            )
            if text
        ]
        if appendix:
            appendix_text = "\n\n".join(appendix)
            if initial.get("role") == "user" and isinstance(initial.get("content"), str):
                initial["content"] += "\n\n" + appendix_text
            else:
                retained.insert(
                    0,
                    _ProjectedGroup(
                        messages=[{"role": "user", "content": appendix_text}],
                        summary={"synthetic": "context_appendix"},
                    ),
                )

        output = [initial]
        for group in retained:
            for message in group.messages:
                if merge_messages:
                    _append_message(output, message)
                else:
                    output.append(copy.deepcopy(dict(message)))
        history_chars = projected_history_chars()
        stats = {
            "mode": mode,
            "history_char_budget": self.history_char_budget,
            "original_message_count": len(messages),
            "projected_message_count": len(output),
            "original_history_chars": _json_chars([dict(item) for item in messages[1:]]),
            "projected_history_chars": history_chars,
            "tool_group_count": len(projected_groups),
            "retained_group_count": len(retained),
            "summarized_group_count": len(summarized),
            "current_driver_revision": snapshot.get("revision") if snapshot else None,
            "current_driver_source_chars": snapshot.get("source_chars") if snapshot else 0,
            "budget_exceeded": history_chars > self.history_char_budget,
        }
        return ContextProjection(messages=tuple(output), stats=stats)

    def project_text_observation(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> ContextProjection:
        projected_groups = [
            self._project_group(group) for group in self._groups(messages[1:])
        ]
        return self._assemble(
            messages,
            projected_groups,
            mode="text-observation",
            merge_messages=True,
        )

    def project_native(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> ContextProjection:
        projected_groups = [
            self._project_native_group(group) for group in self._groups(messages[1:])
        ]
        return self._assemble(
            messages,
            projected_groups,
            mode="native",
            merge_messages=False,
        )
