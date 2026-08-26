from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoadapter2.driver_synthesis.generation import (
    GENERATE_SCRATCH_MAX_TURNS,
    GENERATE_SKELETON_MAX_TURNS,
    REPAIR_SCRATCH_MAX_TURNS,
    REPAIR_SKELETON_MAX_TURNS,
    STUDY_REACT_MAX_TURNS,
    _artifact_turn_budget,
    _build_study_inputs,
)
from autoadapter2.driver_synthesis.interactive import PublicDevelopmentSession
from autoadapter2.driver_synthesis.probe import ProbeBudget
from autoadapter2.libraries import RobotPackage
from autoadapter2.react import (
    ReactLoopError,
    ToolCall,
    ToolSpec,
    ToolTurn,
    run_artifact_react,
)


def _call(call_id: str, name: str, arguments: Mapping[str, Any]) -> ToolCall:
    encoded = json.dumps(dict(arguments), sort_keys=True)
    return ToolCall(call_id, name, dict(arguments), encoded)


class _ScriptedArtifactClient:
    def __init__(self, turns: Sequence[ToolTurn]) -> None:
        self.turns = list(turns)
        self.messages: list[list[dict[str, Any]]] = []
        self.tools: list[list[dict[str, Any]]] = []

    def generate_tool_turn(
        self,
        *,
        stage: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> ToolTurn:
        del stage, system_prompt
        self.messages.append([dict(message) for message in messages])
        self.tools.append([dict(tool) for tool in tools])
        return self.turns.pop(0)


def _json_validator(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("ready") is not True:
        raise ValueError("ready JSON object required")
    return value


class ArtifactWorkflowTests(unittest.TestCase):
    def test_default_file_workflow_has_no_aggregate_tool_call_ceiling(self) -> None:
        self.assertIsNone(ProbeBudget().max_requests)

    def test_invalid_artifact_gets_deterministic_same_conversation_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "study.json"
            artifact.write_text("not-json", encoding="utf-8")

            def write_file(arguments: Mapping[str, Any]) -> dict[str, Any]:
                path = Path(directory) / str(arguments["path"])
                path.write_text(str(arguments["content"]), encoding="utf-8")
                return {"path": str(arguments["path"])}

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(content="done", finish_reason="end_turn"),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "write-1",
                                "write_file",
                                {"path": "study.json", "content": '{"ready": true}'},
                            ),
                        ),
                    ),
                ]
            )
            result = run_artifact_react(
                client=client,
                stage="study",
                system_prompt="write the study",
                user_prompt="produce study.json",
                tools=(
                    ToolSpec(
                        "write_file",
                        "write",
                        {"type": "object"},
                        write_file,
                    ),
                ),
                artifact_name="study.json",
                artifact_path=artifact,
                validate_artifact=_json_validator,
                max_turns=2,
            )

            self.assertEqual(result.artifact, {"ready": True})
            self.assertEqual(result.completed_on, "final_turn")
            recovery = "\n".join(
                str(message.get("content", ""))
                for message in client.messages[1]
                if message.get("role") == "user"
            )
            self.assertIn("Artifact validation failed for study.json", recovery)
            self.assertIn("1 model turns remain", recovery)

    def test_final_turn_valid_artifact_is_accepted_without_closing_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "driver.py"

            def write_file(arguments: Mapping[str, Any]) -> dict[str, Any]:
                artifact.write_text(str(arguments["content"]), encoding="utf-8")
                return {"path": "driver.py"}

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "write-1",
                                "write_file",
                                {"path": "driver.py", "content": "ready"},
                            ),
                        ),
                    )
                ]
            )
            result = run_artifact_react(
                client=client,
                stage="generate",
                system_prompt="write the driver",
                user_prompt="produce driver.py",
                tools=(
                    ToolSpec(
                        "write_file",
                        "write",
                        {"type": "object"},
                        write_file,
                    ),
                ),
                artifact_name="driver.py",
                artifact_path=artifact,
                validate_artifact=lambda path: path.read_text(encoding="utf-8") == "ready",
                max_turns=1,
            )

            self.assertEqual(result.artifact, artifact)
            self.assertEqual(result.completed_on, "final_turn")
            self.assertEqual(result.model_turns, 1)

    def test_missing_artifact_gets_a_two_turn_deadline_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "study.json"

            def write_file(arguments: Mapping[str, Any]) -> dict[str, Any]:
                artifact.write_text(str(arguments["content"]), encoding="utf-8")
                return {"path": "study.json"}

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(_call("observe", "observe", {}),),
                    ),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "write-after-warning",
                                "write_file",
                                {"path": "study.json", "content": '{"ready": true}'},
                            ),
                        ),
                    ),
                ]
            )
            result = run_artifact_react(
                client=client,
                stage="study",
                system_prompt="write the study",
                user_prompt="produce study.json",
                tools=(
                    ToolSpec("observe", "observe", {"type": "object"}, lambda _: {}),
                    ToolSpec("write_file", "write", {"type": "object"}, write_file),
                ),
                artifact_name="study.json",
                artifact_path=artifact,
                validate_artifact=_json_validator,
                max_turns=3,
            )

            warning = "\n".join(
                str(message.get("content", ""))
                for message in client.messages[1]
                if message.get("role") == "user"
            )
            self.assertIn("Artifact deadline: 2 model turns remain", warning)
            self.assertEqual(client.tools[1], [{
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "write",
                    "parameters": {"type": "object"},
                },
            }])
            self.assertEqual(result.completed_on, "artifact_delivery_turn")

    def test_large_artifact_can_use_four_write_only_delivery_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "capability_design.json"

            def write_file(arguments: Mapping[str, Any]) -> dict[str, Any]:
                content = str(arguments["content"])
                before = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
                combined = before + content if arguments.get("append") is True else content
                artifact.write_text(combined, encoding="utf-8")
                return {
                    "path": "capability_design.json",
                    "append": arguments.get("append", False),
                    "file_chars": len(combined),
                }

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(_call("inspect-1", "observe", {}),),
                    ),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(_call("inspect-2", "observe", {}),),
                    ),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "chunk-1",
                                "write_file",
                                {
                                    "path": "capability_design.json",
                                    "content": '{"ready":',
                                    "append": False,
                                },
                            ),
                        ),
                    ),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "chunk-2",
                                "write_file",
                                {
                                    "path": "capability_design.json",
                                    "content": "true}",
                                    "append": True,
                                },
                            ),
                        ),
                    ),
                ]
            )
            result = run_artifact_react(
                client=client,
                stage="tgcd",
                system_prompt="write the design in bounded chunks",
                user_prompt="produce capability_design.json",
                tools=(
                    ToolSpec("observe", "observe", {"type": "object"}, lambda _: {}),
                    ToolSpec("write_file", "write", {"type": "object"}, write_file),
                ),
                artifact_name="capability_design.json",
                artifact_path=artifact,
                validate_artifact=_json_validator,
                max_turns=6,
                delivery_turns=4,
            )

            self.assertEqual(result.artifact, {"ready": True})
            self.assertEqual(result.model_turns, 4)
            self.assertEqual(
                [tool[0]["function"]["name"] for tool in client.tools[2:]],
                ["write_file", "write_file"],
            )
            recovery = "\n".join(
                str(message.get("content", ""))
                for message in client.messages[3]
                if message.get("role") == "user"
            )
            self.assertIn("Artifact validation failed", recovery)

    def test_artifact_recovery_keeps_validator_detail_beyond_1000_chars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "capability_design.json"

            def write_file(arguments: Mapping[str, Any]) -> dict[str, Any]:
                artifact.write_text(str(arguments["content"]), encoding="utf-8")
                return {"path": "capability_design.json"}

            def validate(path: Path) -> dict[str, Any]:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if parsed.get("ready") is not True:
                    raise ValueError("A" * 1500 + "TAIL_CRITERION_ERROR")
                return parsed

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "invalid",
                                "write_file",
                                {
                                    "path": "capability_design.json",
                                    "content": '{"ready":false}',
                                },
                            ),
                        ),
                    ),
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(
                            _call(
                                "valid",
                                "write_file",
                                {
                                    "path": "capability_design.json",
                                    "content": '{"ready":true}',
                                },
                            ),
                        ),
                    ),
                ]
            )

            result = run_artifact_react(
                client=client,
                stage="tgcd",
                system_prompt="write the design",
                user_prompt="produce capability_design.json",
                tools=(ToolSpec("write_file", "write", {"type": "object"}, write_file),),
                artifact_name="capability_design.json",
                artifact_path=artifact,
                validate_artifact=validate,
                max_turns=2,
            )

            recovery = "\n".join(
                str(message.get("content", ""))
                for message in client.messages[1]
                if message.get("role") == "user"
            )
            self.assertIn("TAIL_CRITERION_ERROR", recovery)
            self.assertEqual(result.artifact, {"ready": True})

    def test_final_turn_rejects_a_hallucinated_non_write_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "study.json"
            executed: list[bool] = []

            def execute_python(_arguments: Mapping[str, Any]) -> dict[str, Any]:
                executed.append(True)
                return {"ok": True}

            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=(_call("hallucinated", "execute_python", {"code": "pass"}),),
                    )
                ]
            )
            with self.assertRaises(ReactLoopError) as raised:
                run_artifact_react(
                    client=client,
                    stage="study",
                    system_prompt="write the study",
                    user_prompt="produce study.json",
                    tools=(
                        ToolSpec("write_file", "write", {"type": "object"}, lambda _: {}),
                        ToolSpec(
                            "execute_python",
                            "execute",
                            {"type": "object"},
                            execute_python,
                        ),
                    ),
                    artifact_name="study.json",
                    artifact_path=artifact,
                    validate_artifact=_json_validator,
                    max_turns=1,
                )

            self.assertEqual(client.tools[0][0]["function"]["name"], "write_file")
            self.assertEqual(executed, [])
            self.assertFalse(raised.exception.trace[0]["ok"])
            self.assertIn("tool unavailable", raised.exception.trace[0]["observation"])

    def test_artifact_loop_has_no_aggregate_tool_call_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "study.json"
            artifact.write_text('{"ready": true}', encoding="utf-8")
            client = _ScriptedArtifactClient(
                [
                    ToolTurn(
                        content=None,
                        finish_reason="tool_calls",
                        tool_calls=tuple(
                            _call(str(index), "observe", {}) for index in range(100)
                        ),
                    ),
                    ToolTurn(content="done", finish_reason="stop"),
                ]
            )
            result = run_artifact_react(
                client=client,
                stage="study",
                system_prompt="observe then finish",
                user_prompt="produce study.json",
                tools=(ToolSpec("observe", "observe", {"type": "object"}, lambda _: {}),),
                artifact_name="study.json",
                artifact_path=artifact,
                validate_artifact=_json_validator,
                max_turns=3,
            )
            self.assertEqual(result.tool_calls, 100)
            self.assertEqual(result.completed_on, "end_turn")

    def test_artifact_tool_surface_has_no_submission_or_atomic_check_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_root = Path(directory) / "package"
            (package_root / "assets").mkdir(parents=True)
            (package_root / "tasks").mkdir()
            (package_root / "skeleton").mkdir()
            (package_root / "reference").mkdir()
            (package_root / "assets" / "scene.xml").write_text(
                "<mujoco model='tiny'><worldbody/></mujoco>\n", encoding="utf-8"
            )
            (package_root / "morphology.json").write_text("{}", encoding="utf-8")
            (package_root / "tasks" / "catalog.json").write_text("{}", encoding="utf-8")
            (package_root / "tasks" / "sources.json").write_text("{}", encoding="utf-8")
            package = RobotPackage(
                root=package_root,
                robot_configuration_id="tiny",
                package_version="1.0.0",
                snapshot_id="tiny",
                morphology={},
                sources=(),
                tasks=(),
                mjcf_path=package_root / "assets" / "scene.xml",
                skeleton_dir=package_root / "skeleton",
                reference_driver=package_root / "reference" / "driver.py",
                private_dir=package_root / "tasks" / "private",
            )
            source_root = Path(__file__).resolve().parents[1] / "src"
            for condition, include_skeleton, expected in (
                (
                    "from-scratch",
                    False,
                    {"read_file", "write_file", "execute_python"},
                ),
                (
                    "skeleton-assisted",
                    True,
                    {
                        "read_file",
                        "write_file",
                        "execute_python",
                        "list_skeletons",
                        "inspect_skeleton",
                    },
                ),
            ):
                workspace = Path(directory) / condition
                session = PublicDevelopmentSession(
                    package=package,
                    condition=condition,
                    workspace=workspace,
                    budget=ProbeBudget(max_requests=4),
                    source_root=source_root,
                )
                try:
                    tools = session.artifact_tools(include_skeleton=include_skeleton)
                    self.assertEqual({tool.name for tool in tools}, expected)
                    self.assertTrue(all(not tool.terminal for tool in tools))
                    write_file = next(tool for tool in tools if tool.name == "write_file")
                    self.assertIn("append", write_file.input_schema["properties"])
                    write_file.handler(
                        {
                            "path": "study.json",
                            "content": '{"ready":',
                            "append": False,
                        }
                    )
                    appended = write_file.handler(
                        {
                            "path": "study.json",
                            "content": "true}",
                            "append": True,
                        }
                    )
                    self.assertTrue(appended["append"])
                    self.assertEqual(
                        (workspace / "study.json").read_text(encoding="utf-8"),
                        '{"ready":true}',
                    )
                finally:
                    session.close()

            self.assertEqual(
                _build_study_inputs(
                    package,
                    None,
                    condition="skeleton-assisted",
                    experience=(),
                    runtime_contract={"primitives": ["mujoco.mj_step"]},
                ),
                _build_study_inputs(
                    package,
                    None,
                    condition="from-scratch",
                    experience=(),
                    runtime_contract={"primitives": ["mujoco.mj_step"]},
                ),
            )

    def test_pre_tgcd_study_inputs_are_package_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_root = Path(directory) / "package"
            (package_root / "assets").mkdir(parents=True)
            (package_root / "tasks").mkdir()
            (package_root / "reference").mkdir()
            scene = package_root / "assets" / "scene.xml"
            scene.write_text("<mujoco model='tiny'><worldbody/></mujoco>\n", encoding="utf-8")
            (package_root / "morphology.json").write_text("{}", encoding="utf-8")
            (package_root / "tasks" / "catalog.json").write_text("{}", encoding="utf-8")
            (package_root / "tasks" / "sources.json").write_text("{}", encoding="utf-8")
            package = RobotPackage(
                root=package_root,
                robot_configuration_id="tiny",
                package_version="1.0.0",
                snapshot_id="tiny",
                morphology={},
                sources=(),
                tasks=(),
                mjcf_path=scene,
                skeleton_dir=package_root / "skeleton",
                reference_driver=package_root / "reference" / "driver.py",
                private_dir=package_root / "tasks" / "private",
            )
            from autoadapter2.driver_synthesis.generation import _build_study_inputs

            inputs = _build_study_inputs(
                package,
                None,
                condition="from-scratch",
                experience=(),
                runtime_contract=None,
            )
            self.assertNotIn("sealed_capability_design", inputs)
            self.assertNotIn("driver_interface_stub", inputs)
            self.assertNotIn("public_invocation_abi", inputs["allowed_runtime_facts"])

    def test_owned_workflow_source_has_no_retired_tool_names(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "autoadapter2"
        paths = [
            source_root / "react.py",
            source_root / "driver_synthesis" / "generation.py",
            source_root / "driver_synthesis" / "repair.py",
            source_root / "driver_synthesis" / "interactive.py",
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        retired = (
            "submit" + "_driver",
            "check" + "_driver",
            "list" + "_public_files",
            "read" + "_public_file",
        )
        for name in retired:
            self.assertNotIn(name, source)

    def test_artifact_turn_budgets_match_aa1_conditions(self) -> None:
        self.assertEqual(_artifact_turn_budget("study", "from-scratch"), 16)
        self.assertEqual(STUDY_REACT_MAX_TURNS, 16)
        self.assertEqual(
            _artifact_turn_budget("generate", "skeleton-assisted"),
            GENERATE_SKELETON_MAX_TURNS,
        )
        self.assertEqual(
            _artifact_turn_budget("generate", "from-scratch"),
            GENERATE_SCRATCH_MAX_TURNS,
        )
        self.assertEqual(
            _artifact_turn_budget("repair", "skeleton-assisted"),
            REPAIR_SKELETON_MAX_TURNS,
        )
        self.assertEqual(
            _artifact_turn_budget("repair", "from-scratch"),
            REPAIR_SCRATCH_MAX_TURNS,
        )


if __name__ == "__main__":
    unittest.main()
