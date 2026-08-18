from __future__ import annotations

import json
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from autoadapter2.react import (
    ReactLoopError,
    ToolCall,
    ToolSpec,
    ToolTurn,
    run_react,
)


class ScriptedClient:
    def __init__(self, turns: Sequence[ToolTurn]) -> None:
        self.turns = list(turns)
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_tools: list[list[dict[str, Any]]] = []

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del stage, system_prompt
        self.seen_messages.append([dict(message) for message in messages])
        self.seen_tools.append([dict(tool) for tool in tools])
        return self.turns.pop(0)


def call(call_id: str, name: str, arguments: Mapping[str, Any]) -> ToolCall:
    raw = json.dumps(dict(arguments), sort_keys=True)
    return ToolCall(
        id=call_id,
        name=name,
        arguments=dict(arguments),
        raw_arguments=raw,
    )


class ReactLoopTests(unittest.TestCase):
    def test_tool_observation_is_returned_before_explicit_submission(self) -> None:
        client = ScriptedClient(
            [
                ToolTurn(content=None, tool_calls=(call("one", "inspect", {"x": 2}),)),
                ToolTurn(
                    content=None,
                    tool_calls=(call("two", "submit", {"answer": 5}),),
                ),
            ]
        )
        result = run_react(
            client=client,
            stage="GENERATE",
            system_prompt="Work through tools.",
            user_prompt="Build an artifact.",
            tools=(
                ToolSpec(
                    name="inspect",
                    description="Inspect one value.",
                    input_schema={"type": "object"},
                    handler=lambda arguments: {"seen": arguments["x"]},
                ),
                ToolSpec(
                    name="submit",
                    description="Submit the artifact.",
                    input_schema={"type": "object"},
                    handler=lambda arguments: dict(arguments),
                    terminal=True,
                ),
            ),
        )

        self.assertEqual(result.submission, {"answer": 5})
        self.assertEqual(result.model_turns, 2)
        second_turn_messages = client.seen_messages[1]
        observation = next(
            message for message in second_turn_messages if message["role"] == "tool"
        )
        self.assertTrue(json.loads(observation["content"])["ok"])

    def test_tool_failure_is_observed_and_can_be_repaired_in_same_conversation(self) -> None:
        def fail(_arguments: Mapping[str, Any]) -> None:
            raise ValueError("probe did not import")

        client = ScriptedClient(
            [
                ToolTurn(content=None, tool_calls=(call("one", "probe", {}),)),
                ToolTurn(content=None, tool_calls=(call("two", "submit", {}),)),
            ]
        )
        result = run_react(
            client=client,
            stage="STUDY",
            system_prompt="Use tools.",
            user_prompt="Study.",
            tools=(
                ToolSpec("probe", "Run a probe.", {"type": "object"}, fail),
                ToolSpec(
                    "submit",
                    "Submit findings.",
                    {"type": "object"},
                    lambda arguments: dict(arguments),
                    terminal=True,
                ),
            ),
        )

        self.assertEqual(result.model_turns, 2)
        failed = json.loads(client.seen_messages[1][-1]["content"])
        self.assertFalse(failed["ok"])
        self.assertIn("probe did not import", failed["error"])

    def test_malformed_arguments_are_returned_as_a_tool_error(self) -> None:
        called = False

        def should_not_run(_arguments: Mapping[str, Any]) -> None:
            nonlocal called
            called = True

        malformed = ToolCall(
            id="one",
            name="inspect",
            arguments=None,
            raw_arguments="{",
            argument_error="tool arguments are malformed JSON",
        )
        client = ScriptedClient(
            [
                ToolTurn(content=None, tool_calls=(malformed,)),
                ToolTurn(content=None, tool_calls=(call("two", "submit", {}),)),
            ]
        )
        run_react(
            client=client,
            stage="GENERATE",
            system_prompt="Use tools.",
            user_prompt="Build.",
            tools=(
                ToolSpec("inspect", "Inspect.", {"type": "object"}, should_not_run),
                ToolSpec(
                    "submit",
                    "Submit.",
                    {"type": "object"},
                    lambda arguments: dict(arguments),
                    terminal=True,
                ),
            ),
        )

        self.assertFalse(called)
        failed = json.loads(client.seen_messages[1][-1]["content"])
        self.assertFalse(failed["ok"])

    def test_text_without_submit_is_bounded(self) -> None:
        client = ScriptedClient(
            [ToolTurn(content="done"), ToolTurn(content="still done")]
        )
        with self.assertRaisesRegex(ReactLoopError, "without submission"):
            run_react(
                client=client,
                stage="GENERATE",
                system_prompt="Use tools.",
                user_prompt="Build.",
                tools=(
                    ToolSpec(
                        "submit",
                        "Submit.",
                        {"type": "object"},
                        lambda arguments: dict(arguments),
                        terminal=True,
                    ),
                ),
                max_turns=2,
            )

    def test_last_turn_exposes_only_submission_tool(self) -> None:
        client = ScriptedClient(
            [
                ToolTurn(content=None, tool_calls=(call("one", "inspect", {}),)),
                ToolTurn(content=None, tool_calls=(call("two", "submit", {}),)),
            ]
        )

        result = run_react(
            client=client,
            stage="GENERATE",
            system_prompt="Use tools.",
            user_prompt="Build.",
            tools=(
                ToolSpec("inspect", "Inspect.", {"type": "object"}, lambda _: {}),
                ToolSpec(
                    "submit",
                    "Submit.",
                    {"type": "object"},
                    lambda arguments: dict(arguments),
                    terminal=True,
                ),
            ),
            max_turns=2,
        )

        self.assertEqual(result.submitted_with, "submit")
        final_tool_names = [
            tool["function"]["name"] for tool in client.seen_tools[-1]
        ]
        self.assertEqual(final_tool_names, ["submit"])
        self.assertIn(
            "reserved final submission turn",
            str(client.seen_messages[-1][-1]["content"]),
        )

    def test_limit_error_retains_trace_and_budget_counts(self) -> None:
        client = ScriptedClient([ToolTurn(content="draft"), ToolTurn(content="done")])

        with self.assertRaises(ReactLoopError) as raised:
            run_react(
                client=client,
                stage="STUDY",
                system_prompt="Use tools.",
                user_prompt="Study.",
                tools=(
                    ToolSpec(
                        "submit",
                        "Submit.",
                        {"type": "object"},
                        lambda arguments: dict(arguments),
                        terminal=True,
                    ),
                ),
                max_turns=2,
            )

        self.assertEqual(raised.exception.model_turns, 2)
        self.assertEqual(raised.exception.tool_calls, 0)
        self.assertTrue(raised.exception.trace)
        self.assertEqual(raised.exception.trace[-2]["event"], "final_submission_turn")


if __name__ == "__main__":
    unittest.main()
