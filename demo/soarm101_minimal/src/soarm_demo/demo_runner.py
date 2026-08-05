"""Independent ReAct Consumer Agent and frozen six-task Demo runner."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .audit import (
    BudgetCounter,
    BudgetExceeded,
    atomic_write_json,
    sha256_bytes,
    sha256_file,
    sha256_json,
    utc_now,
)
from .model_client import ModelClient
from .private_execution_bundle import (
    PrivateExecutionBundle,
    PrivateExecutionBundleError,
)
from .react_agent import DemoReActAgent, ToolSpec
from .task_oracle_contract import (
    DEFAULT_TASK_ORACLE_SCHEMA,
    FrozenTaskOracleContract,
    TaskOracleContractError,
    parse_task_oracle_contract,
    trusted_environment_context,
)


class DemoTaskEnvironment(Protocol):
    """Trusted per-task world. Private scoring never reaches the Agent."""

    runtime: Any

    def reset(self, instance: Mapping[str, Any]) -> None: ...

    def score(self, task: Mapping[str, Any], instance: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


DemoEnvironmentFactory = Callable[[Mapping[str, Any]], DemoTaskEnvironment]
ToolFactory = Callable[[Any], Mapping[str, ToolSpec]]
SceneFactsFactory = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class DemoTaskRecord:
    ordinal: int
    partition: str
    task_id: str
    agent_session_id: str
    agent_episode_id: str
    # Kept for v1 consumers; this is exactly the Agent-turn count.
    model_calls: int
    agent_turns: int
    provider_http_attempts: int
    provider_retries: int
    client_kind: str
    scripted: bool
    agent_turn_budget: Mapping[str, Any]
    provider_attempt_budget_policy: Mapping[str, Any]
    usage: Mapping[str, Any]
    agent_status: str
    termination_reason: str
    agent_final: str
    oracle: Mapping[str, Any]
    passed: bool
    trace_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "partition": self.partition,
            "task_id": self.task_id,
            "agent_session_id": self.agent_session_id,
            "agent_episode_id": self.agent_episode_id,
            "model_calls": self.model_calls,
            "agent_turns": self.agent_turns,
            "provider_http_attempts": self.provider_http_attempts,
            "provider_retries": self.provider_retries,
            "client_kind": self.client_kind,
            "scripted": self.scripted,
            "agent_turn_budget": self.agent_turn_budget,
            "provider_attempt_budget_policy": self.provider_attempt_budget_policy,
            "usage": self.usage,
            "agent_status": self.agent_status,
            "termination_reason": self.termination_reason,
            "agent_final": self.agent_final,
            "oracle": self.oracle,
            "passed": self.passed,
            "trace_path": self.trace_path,
        }


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object at {path}")
    return value


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    values = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number} is not an object")
            values.append(value)
    return values


def _empty_task_accounting(client: ModelClient) -> dict[str, Any]:
    return {
        "agent_turns": 0,
        "model_calls": 0,
        "provider_http_attempts": 0,
        "provider_retries": 0,
        "client_kind": client.client_kind,
        "scripted": client.scripted,
        "agent_turn_budget": {},
        "provider_attempt_budget_policy": client.audit_snapshot().get(
            "attempt_budget_policy", {}
        ),
        "usage": {
            "response_count": 0,
            "input_tokens_total": None,
            "output_tokens_total": None,
            "cost_total": None,
            "remaining_budget_latest": None,
            "remaining_budget_minimum": None,
            "input_tokens_reported": 0,
            "output_tokens_reported": 0,
            "cost_reported": 0,
            "remaining_budget_reported": 0,
        },
    }


def _aggregate_task_usage(records: Sequence[DemoTaskRecord]) -> dict[str, Any]:
    usages = [record.usage for record in records]

    def total_or_none(total_name: str, reported_name: str) -> int | float | None:
        reported = sum(int(usage.get(reported_name, 0)) for usage in usages)
        if reported == 0:
            return None
        return sum(
            value
            for usage in usages
            if isinstance((value := usage.get(total_name)), (int, float))
        )

    remaining = [
        value
        for usage in usages
        if isinstance((value := usage.get("remaining_budget_latest")), (int, float))
    ]
    minimums = [
        value
        for usage in usages
        if isinstance((value := usage.get("remaining_budget_minimum")), (int, float))
    ]
    return {
        "response_count": sum(int(usage.get("response_count", 0)) for usage in usages),
        "input_tokens_total": total_or_none(
            "input_tokens_total", "input_tokens_reported"
        ),
        "output_tokens_total": total_or_none(
            "output_tokens_total", "output_tokens_reported"
        ),
        "cost_total": total_or_none("cost_total", "cost_reported"),
        "remaining_budget_latest": remaining[-1] if remaining else None,
        "remaining_budget_minimum": min(minimums) if minimums else None,
        "input_tokens_reported": sum(
            int(usage.get("input_tokens_reported", 0)) for usage in usages
        ),
        "output_tokens_reported": sum(
            int(usage.get("output_tokens_reported", 0)) for usage in usages
        ),
        "cost_reported": sum(int(usage.get("cost_reported", 0)) for usage in usages),
        "remaining_budget_reported": sum(
            int(usage.get("remaining_budget_reported", 0)) for usage in usages
        ),
    }


def public_model_identity(client: ModelClient) -> dict[str, Any]:
    """Return reproducibility-relevant client facts without credentials/payloads."""

    audit = client.audit_snapshot()
    identity: dict[str, Any] = {
        "model": client.model,
        "client_kind": client.client_kind,
        "scripted": client.scripted,
        "transport": audit.get("transport"),
        "provider_attempt_budget_policy": audit.get("attempt_budget_policy", {}),
        "request_defaults": {
            "max_tokens": None,
            "temperature": None,
        },
    }
    config = getattr(client, "config", None)
    if config is not None:
        # Deliberately exclude api_key_env, headers, request bodies, response
        # bodies, and any credential-bearing field.
        identity["request_defaults"] = {
            name: getattr(config, name)
            for name in (
                "route",
                "max_tokens",
                "temperature",
                "short_timeout_s",
                "long_timeout_s",
                "max_retries",
            )
            if hasattr(config, name)
        }
    return identity


def _indexed_tasks(
    records: Sequence[Mapping[str, Any]], *, source: str
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for record in records:
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"{source} contains a task without a valid task_id")
        if task_id in index:
            raise ValueError(f"{source} contains duplicate task_id {task_id!r}")
        index[task_id] = record
    return index


class FrozenDemoRunner:
    """Run exactly the precommitted 3 visible + 3 pilot-held-out batch."""

    def __init__(
        self,
        *,
        client: ModelClient,
        system_prompt: str,
        combined_catalog: Mapping[str, Any],
        tool_factory: ToolFactory,
        environment_factory: DemoEnvironmentFactory,
        output_dir: str | Path,
        scene_facts_factory: SceneFactsFactory | None = None,
        max_model_calls_per_task: int = 30,
        generation_session_id: str | None = None,
        oracle_evaluator_id: str,
        oracle_evaluator_path: str | Path,
    ) -> None:
        self.client = client
        self.system_prompt = system_prompt
        self.combined_catalog = dict(combined_catalog)
        self.tool_factory = tool_factory
        self.environment_factory = environment_factory
        self.scene_facts_factory = scene_facts_factory
        self.output_dir = Path(output_dir)
        self.max_model_calls_per_task = max_model_calls_per_task
        self.generation_session_id = generation_session_id
        self.oracle_evaluator_id = oracle_evaluator_id
        self.oracle_evaluator_path = Path(oracle_evaluator_path)

    def _effective_instance(self, instance: Mapping[str, Any]) -> dict[str, Any]:
        """Attach catalog-derived scene facts without changing frozen source bytes."""

        effective = deepcopy(dict(instance))
        if self.scene_facts_factory is None:
            return effective
        scene_facts = self.scene_facts_factory(instance)
        if not isinstance(scene_facts, Mapping):
            raise TypeError("scene_facts_factory must return one mapping")
        effective["agent_input"] = deepcopy(dict(scene_facts))
        return effective

    def run(
        self,
        *,
        run_id: str,
        private_execution_bundle: PrivateExecutionBundle | None = None,
        demo_batch_path: str | Path | None = None,
        visible_tasks_path: str | Path | None = None,
        heldout_tasks_path: str | Path | None = None,
        private_instances_root: str | Path | None = None,
        oracle_definitions_path: str | Path | None = None,
    ) -> dict[str, Any]:
        bundle = private_execution_bundle
        if bundle is not None:
            # This verification is deliberately before any Demo Agent turn.
            bundle.verify()
            batch = bundle.read_json("demo_batch")
            visible_records = bundle.read_jsonl("visible_tasks")
            heldout_records = bundle.read_jsonl("heldout_tasks")
            batch_file_sha256 = bundle.sha256_for_role("demo_batch")
            visible_file_sha256 = bundle.sha256_for_role("visible_tasks")
            heldout_file_sha256 = bundle.sha256_for_role("heldout_tasks")
            oracle_file_sha256 = bundle.sha256_for_role("task_oracles")
            oracle_payload = bundle.read_bytes("task_oracles")
            oracle_contract: FrozenTaskOracleContract | None = (
                parse_task_oracle_contract(oracle_payload)
            )
            if oracle_contract.source_sha256 != oracle_file_sha256:
                raise PrivateExecutionBundleError(
                    "parsed task oracle contract is not bound to the frozen role hash"
                )
            batch_path = None
            visible_path = None
            heldout_path = None
            oracle_path = None
        else:
            # Compatibility path for isolated unit tests and external v1 callers.
            # The formal pipeline always supplies ``private_execution_bundle``.
            if any(
                value is None
                for value in (
                    demo_batch_path,
                    visible_tasks_path,
                    heldout_tasks_path,
                    private_instances_root,
                    oracle_definitions_path,
                )
            ):
                raise ValueError("Demo requires one frozen private execution bundle")
            batch_path = Path(demo_batch_path)  # type: ignore[arg-type]
            visible_path = Path(visible_tasks_path)  # type: ignore[arg-type]
            heldout_path = Path(heldout_tasks_path)  # type: ignore[arg-type]
            oracle_path = Path(oracle_definitions_path)  # type: ignore[arg-type]
            batch = load_json(batch_path)
            visible_records = load_jsonl(visible_path)
            heldout_records = load_jsonl(heldout_path)
            batch_file_sha256 = sha256_file(batch_path)
            visible_file_sha256 = sha256_file(visible_path)
            heldout_file_sha256 = sha256_file(heldout_path)
            oracle_file_sha256 = sha256_file(oracle_path)
            # The path-based branch is retained only for isolated v1 unit
            # callers. Formal runs always supply the private bundle above and
            # therefore always use the strict immutable scoring contract.
            oracle_contract = None
        if batch.get("status") != "fixed_before_generation":
            raise ValueError("Demo batch must be fixed before generation")
        ordered = batch.get("ordered_instances", [])
        if len(ordered) != 6:
            raise ValueError("P0 Demo batch must contain exactly six tasks")
        partitions = [str(item.get("partition")) for item in ordered]
        if partitions.count("visible") != 3 or partitions.count("pilot-held-out") != 3:
            raise ValueError("P0 Demo batch must be 3 visible + 3 pilot-held-out")
        ordinals = [item.get("ordinal") for item in ordered]
        if ordinals != list(range(1, 7)):
            raise ValueError("P0 Demo ordinals must be unique and exactly 1..6 in order")
        task_ids = [item.get("task_id") for item in ordered]
        if len(set(task_ids)) != 6:
            raise ValueError("P0 Demo task_id values must be unique")
        instance_refs = [item.get("instance_ref") for item in ordered]
        if len(set(instance_refs)) != 6:
            raise ValueError("P0 Demo instance_ref values must be unique")
        if oracle_contract is not None:
            for task_id in task_ids:
                if not isinstance(task_id, str):
                    raise TaskOracleContractError("Demo batch has an invalid task_id")
                oracle_contract.predicate_for_task(task_id)

        visible_index = _indexed_tasks(visible_records, source="visible task library")
        heldout_index = _indexed_tasks(heldout_records, source="held-out task library")
        overlap = set(visible_index) & set(heldout_index)
        if overlap:
            raise ValueError(f"task IDs occur in both partitions: {sorted(overlap)}")
        if oracle_contract is not None and set(
            oracle_contract.predicates_by_task
        ) != set(visible_index) | set(heldout_index):
            raise TaskOracleContractError(
                "frozen oracle contract does not exactly cover the frozen 9+3 task catalogs"
            )

        instances_root = (
            None
            if bundle is not None
            else Path(private_instances_root).resolve()  # type: ignore[arg-type]
        )
        selected: list[
            tuple[
                Mapping[str, Any],
                Mapping[str, Any],
                dict[str, Any],
                Path | None,
                str | None,
            ]
        ] = []
        frozen_tasks: list[dict[str, Any]] = []
        for entry in ordered:
            partition = str(entry["partition"])
            task_id = str(entry["task_id"])
            expected_index = (
                visible_index if partition == "visible" else heldout_index
            )
            if task_id not in expected_index:
                other = "pilot-held-out" if partition == "visible" else "visible"
                raise ValueError(
                    f"Demo task {task_id!r} is not a member of declared {partition!r} "
                    f"partition (it may belong to {other!r})"
                )
            task = expected_index[task_id]
            declared_visibility = task.get("split", {}).get("visibility")
            expected_visibility = (
                "generation_visible" if partition == "visible" else "demo_heldout"
            )
            if declared_visibility != expected_visibility:
                raise ValueError(
                    f"task {task_id!r} visibility is {declared_visibility!r}, "
                    f"expected {expected_visibility!r}"
                )
            instance_ref = Path(str(entry["instance_ref"]))
            if (
                instance_ref.is_absolute()
                or len(instance_ref.parts) != 2
                or instance_ref.parts[0] != "initial_states"
                or instance_ref.parts[1] in {"", ".", ".."}
            ):
                raise ValueError(f"invalid private instance_ref for {task_id}: {instance_ref}")
            instance_role = f"instance_{int(entry['ordinal']):02d}"
            if bundle is not None:
                instance_path = None
                instance = bundle.read_json(instance_role)
                instance_sha256 = bundle.sha256_for_role(instance_role)
            else:
                assert instances_root is not None
                instance_path = (instances_root / instance_ref.name).resolve()
                if instance_path.parent != instances_root or not instance_path.is_file():
                    raise ValueError(f"instance file is missing or escaped root for {task_id}")
                instance = load_json(instance_path)
                instance_sha256 = sha256_file(instance_path)
            if instance.get("task_id") != task_id:
                raise ValueError(f"instance/task mismatch for {task_id}")
            ordinal = int(entry["ordinal"])
            trace_ref = f"traces/{ordinal:02d}_{task_id}.jsonl"
            frozen_tasks.append(
                {
                    "ordinal": ordinal,
                    "partition": partition,
                    "task_id": task_id,
                    "instance_ref": instance_ref.as_posix(),
                    "task_sha256": sha256_json(task),
                    "instance_sha256": instance_sha256,
                    "trace_path": trace_ref,
                }
            )
            selected.append((entry, task, instance, instance_path, instance_role))

        if not self.oracle_evaluator_path.is_file():
            raise ValueError("Demo oracle evaluator source is missing")
        if bundle is not None:
            # Re-bind the exact parsed bytes immediately before freeze. The
            # contract was already schema/semantically parsed before any Agent
            # was constructed, and it is immutable for the complete Demo.
            if sha256_bytes(bundle.read_bytes("task_oracles")) != oracle_contract.source_sha256:
                raise PrivateExecutionBundleError(
                    "task oracle bytes changed after semantic parsing"
                )
        elif oracle_path is None or not oracle_path.is_file():
            raise ValueError("Demo oracle definitions are missing")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        catalog_hash = sha256_json(self.combined_catalog)
        package_hash = self.combined_catalog.get("package_sha256")
        if not isinstance(package_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", package_hash):
            raise ValueError("combined catalog must bind one SHA-256 generated-package hash")
        prompt_hash = sha256_bytes(self.system_prompt.encode("utf-8"))
        freeze = {
            "schema_version": "robot_capability.demo_freeze.v2",
            "run_id": run_id,
            "batch": {
                "file_sha256": batch_file_sha256,
                "canonical_sha256": sha256_json(batch),
                "task_order_seed": batch.get("task_order_seed"),
            },
            "task_sources": {
                "visible_sha256": visible_file_sha256,
                "pilot_heldout_sha256": heldout_file_sha256,
            },
            "tasks": frozen_tasks,
            "catalog_sha256": catalog_hash,
            "package_sha256": package_hash,
            "consumer_prompt_sha256": prompt_hash,
            "consumer_model": public_model_identity(self.client),
            "agent_turn_budget_per_task": self.max_model_calls_per_task,
            "oracle": {
                "definitions_sha256": oracle_file_sha256,
                "contract_schema_sha256": sha256_file(DEFAULT_TASK_ORACLE_SCHEMA),
                "parser_source_sha256": sha256_file(
                    Path(__file__).resolve().parent / "task_oracle_contract.py"
                ),
                "oracle_set_id": (
                    oracle_contract.oracle_set_id
                    if oracle_contract is not None
                    else "legacy_path_unparsed"
                ),
                "contract_version": (
                    oracle_contract.version
                    if oracle_contract is not None
                    else "legacy_path_unparsed"
                ),
                "evaluator_id": self.oracle_evaluator_id,
                "evaluator_source_sha256": sha256_file(self.oracle_evaluator_path),
            },
        }
        atomic_write_json(
            self.output_dir / "freeze.json",
            freeze,
        )
        freeze_hash = sha256_file(self.output_dir / "freeze.json")
        # This object is categorically separate from Generation. Per-task reset
        # below also replaces its session, episode, history, budget, tools, trace.
        agent = DemoReActAgent(
            agent_id="soarm101-demo-consumer-agent",
            role="demo_consumer",
            client=self.client,
            system_prompt=self.system_prompt,
            tools={},
            budget=BudgetCounter("demo_uninitialized", 1),
            trace_path=self.output_dir / "bootstrap_trace.jsonl",
        )
        if self.generation_session_id and agent.session_id == self.generation_session_id:
            raise RuntimeError("Demo and Generation session identities unexpectedly collide")
        results: list[DemoTaskRecord] = []
        seen_sessions: set[str] = set()
        for entry, task, instance, _instance_path, instance_role in selected:
            if bundle is not None:
                # Reverify immediately before each task consumes private facts.
                # A drift aborts the whole gate and is never converted into an
                # ordinary task miss or sent to the Consumer Agent.
                bundle.verify()
                assert instance_role is not None
                instance = bundle.read_json(instance_role)
            ordinal = int(entry["ordinal"])
            partition = str(entry["partition"])
            task_id = str(entry["task_id"])
            trace_ref = f"traces/{ordinal:02d}_{task_id}.jsonl"
            trace_path = self.output_dir / trace_ref
            environment: DemoTaskEnvironment | None = None
            agent_result = None
            episode_started = False
            accounting = _empty_task_accounting(self.client)
            infrastructure_error: str | None = None
            agent_budget_exhausted = False
            termination_reason = "not_started"
            oracle: dict[str, Any] = {"passed": False}
            passed = False
            try:
                effective_instance = self._effective_instance(instance)
                factory_context = (
                    trusted_environment_context(effective_instance, oracle_contract)
                    if oracle_contract is not None
                    else effective_instance
                )
                environment = self.environment_factory(factory_context)
                environment.reset(effective_instance)
                tools = dict(self.tool_factory(environment.runtime))
                agent.reset_task_episode(
                    task_id=task_id,
                    tools=tools,
                    call_limit=self.max_model_calls_per_task,
                    trace_path=trace_path,
                )
                episode_started = True
                if agent.session_id in seen_sessions:
                    raise RuntimeError("Demo per-task session was not reset")
                seen_sessions.add(agent.session_id)
                if self.generation_session_id and agent.session_id == self.generation_session_id:
                    raise RuntimeError("Demo task reused the Generation session")
                instruction = json.dumps(
                    {
                        "task_id": task_id,
                        "instruction": task["language"]["canonical"],
                        "scene_facts": effective_instance["agent_input"],
                        "reporting_rule": "Use only validated tools. Finish when the task is done or impossible.",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                agent_result = agent.run_task(instruction)
                if bundle is not None:
                    bundle.verify()
                    if sha256_bytes(bundle.read_bytes("task_oracles")) != oracle_contract.source_sha256:
                        raise PrivateExecutionBundleError(
                            "task oracle bytes changed before scoring"
                        )
                oracle = dict(environment.score(task, effective_instance))
                passed = bool(oracle.get("passed", False))
            except PrivateExecutionBundleError:
                raise
            except Exception as exc:
                # Model-client exceptions are sanitized by the provider layer.
                # Keep the error useful, but never add request/response payloads.
                if isinstance(exc, BudgetExceeded):
                    agent_budget_exhausted = True
                    termination_reason = "agent_turn_budget_exhausted"
                else:
                    infrastructure_error = f"{type(exc).__name__}: {exc}"
                    termination_reason = "infrastructure_failure"
            finally:
                if episode_started:
                    accounting = agent.accounting_state()
                if environment is not None:
                    try:
                        environment.close()
                    except Exception as exc:
                        close_error = f"{type(exc).__name__}: {exc}"
                        infrastructure_error = (
                            close_error
                            if infrastructure_error is None
                            else f"{infrastructure_error}; close_failed={close_error}"
                        )
            if infrastructure_error is not None:
                termination_reason = "infrastructure_failure"
                oracle = {
                    "passed": False,
                    "infrastructure_error": infrastructure_error,
                }
                passed = False
            elif agent_budget_exhausted:
                oracle = {
                    "passed": False,
                    "agent_turn_budget_exhausted": True,
                }
                passed = False
            else:
                termination_reason = (
                    "agent_final_oracle_passed"
                    if passed
                    else "agent_final_oracle_failed"
                )
            task_session_id = agent.session_id if episode_started else ""
            task_episode_id = agent.episode_id if episode_started else ""
            results.append(
                DemoTaskRecord(
                    ordinal=ordinal,
                    partition=partition,
                    task_id=task_id,
                    agent_session_id=task_session_id,
                    agent_episode_id=task_episode_id,
                    model_calls=int(accounting["agent_turns"]),
                    agent_turns=int(accounting["agent_turns"]),
                    provider_http_attempts=int(accounting["provider_http_attempts"]),
                    provider_retries=int(accounting["provider_retries"]),
                    client_kind=str(accounting["client_kind"]),
                    scripted=bool(accounting["scripted"]),
                    agent_turn_budget=dict(accounting["agent_turn_budget"]),
                    provider_attempt_budget_policy=dict(
                        accounting["provider_attempt_budget_policy"]
                    ),
                    usage=dict(accounting["usage"]),
                    agent_status=(
                        "infrastructure_failed"
                        if infrastructure_error is not None
                        else "budget_exhausted"
                        if agent_budget_exhausted
                        else agent_result.status
                        if agent_result is not None
                        else "infrastructure_failed"
                    ),
                    termination_reason=termination_reason,
                    agent_final="" if agent_result is None else agent_result.content,
                    oracle=oracle,
                    passed=passed,
                    trace_path=trace_ref,
                )
            )
        visible = [item for item in results if item.partition == "visible"]
        heldout = [item for item in results if item.partition == "pilot-held-out"]
        report = {
            "schema_version": "robot_capability.demo_report.v1",
            "run_id": run_id,
            "catalog_sha256": catalog_hash,
            "package_sha256": package_hash,
            "demo_freeze_sha256": freeze_hash,
            "tasks": [item.to_dict() for item in results],
            "summary": {
                "visible": {
                    "passed": sum(item.passed for item in visible),
                    "total": len(visible),
                    "success_rate": sum(item.passed for item in visible) / len(visible),
                },
                "pilot-held-out": {
                    "passed": sum(item.passed for item in heldout),
                    "total": len(heldout),
                    "success_rate": sum(item.passed for item in heldout) / len(heldout),
                },
                "overall": {
                    "passed": sum(item.passed for item in results),
                    "total": len(results),
                    "success_rate": sum(item.passed for item in results) / len(results),
                },
                "demo_repair_enabled": False,
                "consumer_agent_id": agent.agent_id,
                "distinct_task_sessions": len(seen_sessions),
                "model_accounting": {
                    "client_kind": self.client.client_kind,
                    "scripted": self.client.scripted,
                    "agent_turns": sum(item.agent_turns for item in results),
                    "model_calls": sum(item.agent_turns for item in results),
                    "provider_http_attempts": sum(
                        item.provider_http_attempts for item in results
                    ),
                    "provider_retries": sum(item.provider_retries for item in results),
                    "agent_turn_limit_per_task": self.max_model_calls_per_task,
                    "provider_attempt_budget_policy": self.client.audit_snapshot().get(
                        "attempt_budget_policy", {}
                    ),
                    "usage": _aggregate_task_usage(results),
                },
            },
            "sealed_at": utc_now(),
        }
        atomic_write_json(self.output_dir / "demo_report.json", report)
        return report
