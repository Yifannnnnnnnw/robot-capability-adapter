from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from soarm_demo.demo_runner import FrozenDemoRunner
from soarm_demo.model_client import ScriptedModelClient


class _Environment:
    def __init__(self) -> None:
        self.runtime = object()
        self.closed = False

    def reset(self, instance: Mapping[str, Any]) -> None:
        del instance

    def score(
        self, task: Mapping[str, Any], instance: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        del task, instance
        raise AssertionError("score must not run after Agent budget failure")

    def close(self) -> None:
        self.closed = True


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def _demo_inputs(tmp_path: Path) -> dict[str, Path]:
    ordered = []
    visible_tasks = []
    heldout_tasks = []
    instances = tmp_path / "instances"
    for ordinal in range(1, 7):
        task_id = f"task-{ordinal}"
        partition = "visible" if ordinal <= 3 else "pilot-held-out"
        instance_name = f"instance-{ordinal}.json"
        ordered.append(
            {
                "ordinal": ordinal,
                "partition": partition,
                "task_id": task_id,
                "instance_ref": f"initial_states/{instance_name}",
            }
        )
        task = {
            "task_id": task_id,
            "language": {"canonical": f"do {task_id}"},
            "split": {
                "visibility": (
                    "generation_visible" if partition == "visible" else "demo_heldout"
                )
            },
        }
        (visible_tasks if partition == "visible" else heldout_tasks).append(task)
        _write_json(
            instances / instance_name,
            {"task_id": task_id, "agent_input": {}},
        )

    batch_path = tmp_path / "demo_batch.json"
    visible_path = tmp_path / "visible.jsonl"
    heldout_path = tmp_path / "heldout.jsonl"
    _write_json(
        batch_path,
        {"status": "fixed_before_generation", "ordered_instances": ordered},
    )
    _write_jsonl(visible_path, visible_tasks)
    _write_jsonl(heldout_path, heldout_tasks)
    oracle_path = tmp_path / "task_oracles.yaml"
    oracle_path.write_text("schema_version: test.oracle.v1\n", encoding="utf-8")
    return {
        "batch": batch_path,
        "visible": visible_path,
        "heldout": heldout_path,
        "instances": instances,
        "oracles": oracle_path,
    }


def _runner(tmp_path: Path) -> FrozenDemoRunner:
    # Each malformed response consumes one Agent turn. The subsequent loop
    # iteration hits the per-task budget before another client request.
    client = ScriptedModelClient(["not-json"] * 6, model="scripted-demo")
    return FrozenDemoRunner(
        client=client,
        system_prompt="Use validated tools.",
        combined_catalog={"package_sha256": "0" * 64, "tools": []},
        tool_factory=lambda runtime: {},
        environment_factory=lambda instance: _Environment(),
        output_dir=tmp_path / "output",
        max_model_calls_per_task=1,
        oracle_evaluator_id="tests.test_demo_runner:_Environment.score",
        oracle_evaluator_path=Path(__file__),
    )


def test_demo_budget_failure_preserves_agent_turns_and_usage(tmp_path: Path) -> None:
    paths = _demo_inputs(tmp_path)
    runner = _runner(tmp_path)

    report = runner.run(
        run_id="budget-failure",
        demo_batch_path=paths["batch"],
        visible_tasks_path=paths["visible"],
        heldout_tasks_path=paths["heldout"],
        private_instances_root=paths["instances"],
        oracle_definitions_path=paths["oracles"],
    )

    assert len(report["tasks"]) == 6
    assert all(task["agent_status"] == "budget_exhausted" for task in report["tasks"])
    assert all(task["model_calls"] == task["agent_turns"] == 1 for task in report["tasks"])
    assert all(task["provider_http_attempts"] == 0 for task in report["tasks"])
    assert all(task["provider_retries"] == 0 for task in report["tasks"])
    assert all(task["client_kind"] == "scripted_fixture" for task in report["tasks"])
    assert all(task["scripted"] is True for task in report["tasks"])
    assert all(
        task["termination_reason"] == "agent_turn_budget_exhausted"
        for task in report["tasks"]
    )
    assert all(
        task["oracle"] == {
            "passed": False,
            "agent_turn_budget_exhausted": True,
        }
        for task in report["tasks"]
    )
    assert all(not Path(task["trace_path"]).is_absolute() for task in report["tasks"])
    assert all(task["agent_turn_budget"]["limit"] == 1 for task in report["tasks"])
    assert all(task["agent_turn_budget"]["used"] == 1 for task in report["tasks"])
    assert all(
        task["provider_attempt_budget_policy"]["scope"] == "not_applicable"
        for task in report["tasks"]
    )
    assert all(task["usage"]["response_count"] == 1 for task in report["tasks"])
    assert all(task["agent_session_id"] for task in report["tasks"])
    accounting = report["summary"]["model_accounting"]
    assert accounting["agent_turns"] == accounting["model_calls"] == 6
    assert accounting["provider_http_attempts"] == 0
    assert accounting["provider_retries"] == 0
    assert accounting["agent_turn_limit_per_task"] == 1
    assert accounting["usage"]["response_count"] == 6
    assert accounting["usage"]["input_tokens_total"] == 0
    assert accounting["usage"]["output_tokens_total"] == 0
    assert accounting["usage"]["cost_total"] == 0.0
    freeze = json.loads((tmp_path / "output/freeze.json").read_text(encoding="utf-8"))
    assert freeze["schema_version"] == "robot_capability.demo_freeze.v2"
    assert freeze["package_sha256"] == "0" * 64
    assert freeze["agent_turn_budget_per_task"] == 1
    assert [item["ordinal"] for item in freeze["tasks"]] == list(range(1, 7))
    assert len({item["task_id"] for item in freeze["tasks"]}) == 6
    assert len({item["instance_sha256"] for item in freeze["tasks"]}) == 6
    assert report["demo_freeze_sha256"]


def test_scene_facts_factory_drives_context_reset_prompt_and_score(
    tmp_path: Path,
) -> None:
    paths = _demo_inputs(tmp_path)
    client = ScriptedModelClient(
        ['{"type":"final","content":"done"}'] * 6,
        model="derived-scene-facts",
    )
    contexts: list[Mapping[str, Any]] = []
    resets: list[Mapping[str, Any]] = []
    scores: list[Mapping[str, Any]] = []

    class Environment:
        runtime = object()

        def __init__(self, context: Mapping[str, Any]) -> None:
            contexts.append(context)

        def reset(self, instance: Mapping[str, Any]) -> None:
            resets.append(instance)

        def score(
            self, task: Mapping[str, Any], instance: Mapping[str, Any]
        ) -> Mapping[str, Any]:
            del task
            scores.append(instance)
            return {"passed": True}

        def close(self) -> None:
            return None

    def derive(instance: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "coordinate_frame": "robot_base",
            "objects": {
                "catalog_object": {
                    "position_m": [float(str(instance["task_id"]).split("-")[-1]), 0.0, 0.0]
                }
            },
            "goals": {"source": "task_only"},
        }

    runner = FrozenDemoRunner(
        client=client,
        system_prompt="Use validated tools.",
        combined_catalog={"package_sha256": "0" * 64, "tools": []},
        tool_factory=lambda runtime: {},
        environment_factory=Environment,
        scene_facts_factory=derive,
        output_dir=tmp_path / "output-derived",
        oracle_evaluator_id="tests.test_demo_runner:Environment.score",
        oracle_evaluator_path=Path(__file__),
    )
    report = runner.run(
        run_id="derived-scene-facts",
        demo_batch_path=paths["batch"],
        visible_tasks_path=paths["visible"],
        heldout_tasks_path=paths["heldout"],
        private_instances_root=paths["instances"],
        oracle_definitions_path=paths["oracles"],
    )

    assert len(contexts) == len(resets) == len(scores) == 6
    assert all(
        value["agent_input"]["goals"] == {"source": "task_only"}
        for value in [*contexts, *resets, *scores]
    )
    prompt_facts = [json.loads(request[-1].content)["scene_facts"] for request in client.requests]
    assert all(facts["goals"] == {"source": "task_only"} for facts in prompt_facts)
    assert all(task["passed"] is True for task in report["tasks"])


def test_demo_rejects_task_claimed_in_wrong_partition_before_freeze(tmp_path: Path) -> None:
    paths = _demo_inputs(tmp_path)
    batch = json.loads(paths["batch"].read_text(encoding="utf-8"))
    batch["ordered_instances"][0]["partition"] = "pilot-held-out"
    batch["ordered_instances"][3]["partition"] = "visible"
    _write_json(paths["batch"], batch)

    runner = _runner(tmp_path)
    try:
        runner.run(
            run_id="wrong-partition",
            demo_batch_path=paths["batch"],
            visible_tasks_path=paths["visible"],
            heldout_tasks_path=paths["heldout"],
            private_instances_root=paths["instances"],
            oracle_definitions_path=paths["oracles"],
        )
    except ValueError as exc:
        assert "is not a member of declared" in str(exc)
    else:
        raise AssertionError("wrong-partition batch was accepted")
    assert not (tmp_path / "output/freeze.json").exists()


def test_demo_does_not_swallow_system_level_interrupts(tmp_path: Path) -> None:
    paths = _demo_inputs(tmp_path)
    environments: list[_Environment] = []

    class InterruptingEnvironment(_Environment):
        def reset(self, instance: Mapping[str, Any]) -> None:
            del instance
            raise KeyboardInterrupt("operator interrupted Demo")

    def environment_factory(instance: Mapping[str, Any]) -> _Environment:
        del instance
        environment = InterruptingEnvironment()
        environments.append(environment)
        return environment

    runner = _runner(tmp_path)
    runner.environment_factory = environment_factory

    with pytest.raises(KeyboardInterrupt, match="operator interrupted Demo"):
        runner.run(
            run_id="operator-interrupt",
            demo_batch_path=paths["batch"],
            visible_tasks_path=paths["visible"],
            heldout_tasks_path=paths["heldout"],
            private_instances_root=paths["instances"],
            oracle_definitions_path=paths["oracles"],
        )

    assert len(environments) == 1
    assert environments[0].closed is True
    assert not (tmp_path / "output/demo_report.json").exists()
