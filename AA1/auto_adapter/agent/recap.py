# SPDX-License-Identifier: Apache-2.0
"""AA1 environment/transport adapter around the vendored official ReCAP generator.

Task decomposition, traversal and parent-context prompts run in upstream.chatbot.
This module supplies native actions, model I/O, diagnostic tracing and host budgets.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile
from typing import Any

from .vendor.recap import chatbot as upstream

UPSTREAM_REVISION = "2fb112ffad685c7c6f7de86d5487ecca6f566fcc"
UPSTREAM_CONTROLLER = "auto_adapter.agent.vendor.recap.chatbot.chatbot"


class CapabilityAdapterError(RuntimeError):
    """The proposed capability name or request is invalid."""


class CapabilityInvocationError(CapabilityAdapterError):
    """A valid capability could not be executed."""


class RecapControllerError(ValueError):
    """The controller's public input or proposed plan is invalid."""


@dataclass(frozen=True)
class RecapBudgets:
    max_planning_turns: int = 16
    max_capability_calls: int = 12
    max_depth: int = 6
    max_subtasks_per_plan: int = 8
    max_invalid_outputs: int = 3
    context_window_messages: int = 32

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise RecapControllerError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RecapControllerResult:
    status: str
    planning_turns: int
    capability_calls: int
    invalid_outputs: int
    trace: tuple[Mapping[str, Any], ...]
    context_tree: Mapping[str, Any]

    @property
    def model_turns(self):
        return self.planning_turns

    @property
    def tool_calls(self):
        return self.capability_calls


# Upstream catches Exception to retry malformed plans. Host termination must bypass
# that handler; catch this specific sentinel only, never KeyboardInterrupt/SystemExit.
class _StopRecap(BaseException):
    def __init__(self, status):
        self.status = status


class _Actions:
    """A schema-backed action set for upstream's `action in valid_actions` test.

    Membership has no side effects: upstream probes both abstract tasks and leaves.
    Requests are continuous, so a finite list of preselected actions cannot describe it.
    """
    def __init__(self, adapter):
        self.adapter = adapter

    def decode(self, action):
        value = json.loads(action)
        if not isinstance(value, dict) or set(value) != {"capability_name", "request"}:
            raise CapabilityAdapterError("action must contain capability_name and request")
        if not isinstance(value["capability_name"], str) or not isinstance(value["request"], dict):
            raise CapabilityAdapterError("action needs a string capability_name and object request")
        # Reject nonfinite JSON even when the underlying schema permits a number.
        json.dumps(value, allow_nan=False)
        return value["capability_name"], self.adapter.validate_request(
            value["capability_name"], value["request"])

    def __contains__(self, action):
        try:
            self.decode(action)
            return True
        except (ValueError, TypeError, CapabilityAdapterError):
            return False

    def __repr__(self):
        return "AA1 capabilities and native request schemas supplied in the environment rules"


SYSTEM_PROMPT = """You control a robot through recursive ReCAP task planning.
Respond with one JSON object: {"think": "brief plan summary", "subtasks": ["..."]}.
The think field is a short action summary, not private chain-of-thought.
Subtasks are strings: either abstract task descriptions or JSON-encoded primitive actions
with exactly {"capability_name": "a published method name", "request": {native fields}}.
ReCAP treats a SINGLE subtask while descending as a primitive action to execute. To
DECOMPOSE a task, return at least two subtasks. Start a multi-phase goal with meaningful
abstract groups. Only the first item is expanded/executed; remaining siblings are pending.
After returning to a parent, revise its remaining plan from the supplied observations.
Return [] when the current task has no remaining work. Preserve unfinished requirements.
Do not invent methods, reset the world, regenerate a driver or use private test criteria.
An EXECUTED operation means the method returned normally, not that its physical goal was
independently verified. Inspect observations and errors. Controller completion is not a
physical task verdict. Budget limits are shared across the whole task tree.
"""


def run_recap(*, public_task, adapter, model, budgets=None, initial_public_state=None, log_dir=None):
    """Drive the official generator with AA1 JSON model turns and native capabilities."""
    budgets = budgets or RecapBudgets()
    actions = _Actions(adapter)
    trace, nodes = [], []
    planning_turns = capability_calls = invalid_outputs = 0
    last_status = None

    def event(event_name, **payload):
        trace.append({"event": event_name, **payload})

    def invalid(message):
        nonlocal invalid_outputs
        invalid_outputs += 1
        event("invalid_output", message=message)
        if invalid_outputs >= budgets.max_invalid_outputs:
            raise _StopRecap("INVALID_OUTPUT_BUDGET_EXHAUSTED")

    class ObservedNode(upstream.Node):
        def __init__(self, task_name, parent=None):
            super().__init__(task_name, parent)
            self.node_id = f"n{len(nodes)}"
            self.depth = parent.depth + 1 if parent is not None else 0
            if self.depth > budgets.max_depth:
                raise _StopRecap("DEPTH_BUDGET_EXHAUSTED")
            nodes.append(self)
            event("node_created", node_id=self.node_id,
                  parent_id=parent.node_id if parent is not None else None,
                  depth=self.depth, task_name=task_name)

        def set_info(self, info):
            if (not isinstance(info, dict) or not isinstance(info.get("think"), str)
                    or not isinstance(info.get("subtasks"), list)
                    or len(info["subtasks"]) > budgets.max_subtasks_per_plan
                    or any(not isinstance(s, str) or not s.strip() for s in info["subtasks"])):
                invalid("expected think string and bounded subtasks string list")
                raise ValueError("expected think string and bounded subtasks string list")
            super().set_info(info)
            event("plan_revision", node_id=self.node_id, plan=info)

        def set_obs(self, obs):
            super().set_obs(obs)
            event("observation", node_id=self.node_id, observation=obs)

    class RobotPrompt(upstream.Prompt):
        def generate_init_prompt(self, **kwargs):
            return super().generate_init_prompt(**kwargs).replace(
                "Now you need to make a new meal.", "Now you need to perform a robot task.")

        def _return(self, prompt, kind, kwargs):
            event("parent_return", kind=kind, **kwargs)
            return prompt.replace("You have successfully completed the task:",
                                  "The child task attempt has returned. Inspect its observations and operation status:")

        def generate_leaf_up_prompt(self, **kwargs):
            return self._return(super().generate_leaf_up_prompt(**kwargs), "leaf", kwargs)

        def generate_leaf_judge_done_prompt(self, **kwargs):
            return self._return(super().generate_leaf_judge_done_prompt(**kwargs), "leaf", kwargs)

        def generate_nonleaf_up_prompt(self, **kwargs):
            return self._return(super().generate_nonleaf_up_prompt(**kwargs), "subtask", kwargs)

        def generate_nonleaf_judge_done_prompt(self, **kwargs):
            return self._return(super().generate_nonleaf_judge_done_prompt(**kwargs), "subtask", kwargs)

        def generate_leaf_up_fail_prompt(self, **kwargs):
            try:
                actions.decode(kwargs["fail_task_name"])
            except (ValueError, TypeError, CapabilityAdapterError) as exc:
                explanation = str(exc)
            else:
                explanation = "invalid primitive action"
            invalid(explanation)
            prompt = super().generate_leaf_up_fail_prompt(**kwargs)
            start = prompt.index("Because the task name")
            end = prompt.index("\n\n", start)
            prompt = prompt[:start] + (
                "The primitive action failed AA1 native request validation: " + explanation
                + ". Use a published capability and its request schema. An abstract decomposition "
                  "needs at least two subtasks; a descending singleton must be executable.") + prompt[end:]
            event("parent_return", kind="invalid_action", **kwargs)
            return prompt

    class Memory(upstream.ChatGPTWithMemory):
        def __init__(self):
            # Retain upstream's shared history and [2:4] window eviction. AA1 supplies
            # transport instead of the upstream standalone OpenAI client and billing.
            self.fixed_prompt = {"role": "user", "content": "Use the ReCAP JSON plan format."}
            self.truncated_chat_history = [self.fixed_prompt]
            self.ctx_len = budgets.context_window_messages
            self.model = "aa1-transport"

        def invoke(self, user_prompt):
            nonlocal planning_turns
            if planning_turns >= budgets.max_planning_turns:
                raise _StopRecap("PLANNING_TURN_BUDGET_EXHAUSTED")
            if len(self.truncated_chat_history) > self.ctx_len:
                del self.truncated_chat_history[2:4]
            self.truncated_chat_history.append({"role": "user", "content": user_prompt})
            planning_turns += 1
            try:
                response = model.generate_json(messages=self.truncated_chat_history)
            except Exception as exc:
                event("model_error", error_type=type(exc).__name__, message=str(exc))
                raise _StopRecap("MODEL_ERROR") from exc
            self.truncated_chat_history.append({"role": "assistant", "content": response})
            try:
                json.loads(upstream.remove_json_fence(response))
            except (ValueError, TypeError) as exc:
                invalid(str(exc))
            return response

    memory = Memory()
    temporary = tempfile.TemporaryDirectory(prefix="aa1-recap-") if log_dir is None else None
    output = Path(temporary.name if temporary else log_dir)
    output.mkdir(parents=True, exist_ok=True)
    task = json.dumps(public_task, ensure_ascii=False, allow_nan=False)
    rule = json.dumps({"capabilities": adapter.public_catalog(), "budgets": asdict(budgets)},
                      ensure_ascii=False, allow_nan=False)
    generator = upstream.chatbot(
        system_prompt=SYSTEM_PROMPT, few_shot_list=[], task_name=task,
        init_obs=json.dumps(initial_public_state or {}, ensure_ascii=False, allow_nan=False),
        rule=rule, valid_actions=actions, ctx_len=budgets.context_window_messages,
        llm=memory, prompt_obj=RobotPrompt(), node_factory=ObservedNode, log_dir=str(output))
    event("controller_started", controller=UPSTREAM_CONTROLLER, revision=UPSTREAM_REVISION)
    try:
        action = next(generator)
        while True:
            if capability_calls >= budgets.max_capability_calls:
                raise _StopRecap("CAPABILITY_CALL_BUDGET_EXHAUSTED")
            name, request = actions.decode(action)
            capability_calls += 1
            event("capability_call", capability_name=name, request=request)
            result = adapter.execute(name, request)
            observation = json.dumps(result, ensure_ascii=False, allow_nan=False)
            last_status = result.get("operation", {}).get("status")
            event("capability_result", capability_name=name, feedback=result)
            if last_status == "WORKER_ABORTED":
                raise _StopRecap("WORKER_ABORTED")
            action = generator.send((observation, actions))
    except StopIteration:
        status = ("NO_ACTION_EXECUTED" if not capability_calls else
                  "LAST_ACTION_FAILED" if last_status != "EXECUTED" else "CONTROLLER_FINISHED")
    except _StopRecap as stop:
        status = stop.status
    except Exception as exc:
        event("runtime_error", error_type=type(exc).__name__, message=str(exc))
        status = "RUNTIME_ERROR"
    finally:
        generator.close()
        # Also save partial trees/history after host budget termination.
        if nodes:
            upstream.save_tree_to_json(nodes[0], str(output / "tree.json"))
        memory.save_history_to_json(str(output / "history.json"))
        if temporary:
            temporary.cleanup()
    event("controller_ended", status=status)
    return RecapControllerResult(status, planning_turns, capability_calls, invalid_outputs,
                                 tuple(trace), upstream.tree_to_dict(nodes[0]) if nodes else {})
