"""Demo2's public Stage-1 policy and sealed-decision scope.

The canonical Tasks Library supplies opaque run-local requirements.  This module
adds only public robot capability contracts, then verifies that the sealed Design
is the sole source of the downstream capability and requirement scope.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


POLICY_PROMPT = """
DEMO2 PUBLIC CAPABILITY POLICY:
`robot_public_projection.effect_contracts` is authoritative.  For every selected
effect, copy its exact kind, inputs, outputs, required_action_affordances, and
required_observation_affordances into the capability.  A requirement may be
covered only by an effect listed for that opaque requirement in
`robot_public_projection.requirement_effect_allowlist`.  You decide which
requirements can be grouped into one reusable capability and which requirements
must be placed in exactly one disposition array.  Never expose or infer static
Library task IDs, evaluation cases, thresholds, scenes, or implementation details.
""".strip()


class PolicyAwareGenerator:
    """Append Demo2's public decision policy to the existing Stage-1 prompt."""

    def __init__(self, base: Any) -> None:
        self.base = base

    def generate_json(
        self,
        stage: str,
        prompt: str,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        instruction = prompt
        if stage == "stage1":
            instruction = prompt.rstrip() + "\n\n" + POLICY_PROMPT
        return self.base.generate_json(stage, instruction, inputs)


@dataclass(frozen=True)
class DecisionScope:
    selected_capability_ids: tuple[str, ...]
    selected_effects: tuple[str, ...]
    covered_requirement_ids: tuple[str, ...]
    covered_task_ids: tuple[str, ...]
    unsupported_requirement_ids: tuple[str, ...]
    blocking_requirement_ids: tuple[str, ...]
    requirement_to_capability: Mapping[str, str]
    requirement_to_task: Mapping[str, str]
    full_task_coverage: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_capability_ids": list(self.selected_capability_ids),
            "selected_effects": list(self.selected_effects),
            "covered_requirement_ids": list(self.covered_requirement_ids),
            "covered_task_ids": list(self.covered_task_ids),
            "unsupported_requirement_ids": list(self.unsupported_requirement_ids),
            "blocking_requirement_ids": list(self.blocking_requirement_ids),
            "requirement_to_capability": dict(self.requirement_to_capability),
            "requirement_to_task": dict(self.requirement_to_task),
            "full_task_coverage": self.full_task_coverage,
        }


def validate_policy(value: Mapping[str, Any], task_ids: Sequence[str]) -> dict[str, Any]:
    policy = copy.deepcopy(dict(value))
    if policy.get("artifact_type") != "demo2_stage1_capability_policy":
        raise ValueError("Stage1 capability policy has the wrong artifact_type")
    if policy.get("schema_version") != "1.0.0":
        raise ValueError("Stage1 capability policy has an unsupported schema_version")
    contracts = policy.get("effect_contracts")
    bindings = policy.get("task_effect_allowlist")
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError("Stage1 capability policy requires effect_contracts")
    if not isinstance(bindings, dict) or set(bindings) != set(task_ids):
        raise ValueError("Stage1 capability policy must bind every fixed task exactly once")
    contract_fields = {
        "kind",
        "description",
        "inputs",
        "outputs",
        "required_action_affordances",
        "required_observation_affordances",
    }
    for effect, contract in contracts.items():
        if not isinstance(effect, str) or not effect.isidentifier():
            raise ValueError("Stage1 effects must be valid public method identifiers")
        if not isinstance(contract, dict) or set(contract) != contract_fields:
            raise ValueError(f"Stage1 effect contract {effect!r} has an invalid shape")
        for field in ("inputs", "outputs", "required_action_affordances", "required_observation_affordances"):
            if not isinstance(contract[field], list):
                raise ValueError(f"Stage1 effect contract {effect!r}.{field} must be an array")
    for task_id, effects in bindings.items():
        if not isinstance(effects, list) or not effects or len(set(effects)) != len(effects):
            raise ValueError(f"Stage1 task {task_id!r} needs a non-empty unique effect allowlist")
        if any(effect not in contracts for effect in effects):
            raise ValueError(f"Stage1 task {task_id!r} references an unknown effect")
    return policy


def public_projection(
    morphology: Mapping[str, Any],
    policy: Mapping[str, Any],
    public_tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    stage1 = morphology.get("stage1")
    if not isinstance(stage1, Mapping):
        raise ValueError("morphology.stage1 is required")
    task_ids = [str(task["task_id"]) for task in public_tasks]
    checked = validate_policy(policy, task_ids)
    by_task = checked["task_effect_allowlist"]
    requirement_effects = {
        str(task["requirement_id"]): copy.deepcopy(by_task[str(task["task_id"])])
        for task in public_tasks
    }
    return {
        "robot_model_id": morphology["robot_model_id"],
        "robot_configuration_id": morphology["robot_configuration_id"],
        "execution_route": "DIRECT_MUJOCO_EXPERIMENTAL",
        "action_affordances": copy.deepcopy(list(stage1["action_affordances"])),
        "observation_affordances": copy.deepcopy(list(stage1["observation_affordances"])),
        "effect_allowlist": list(checked["effect_contracts"]),
        "unit_allowlist": copy.deepcopy(list(stage1["unit_allowlist"])),
        "frame_allowlist": copy.deepcopy(list(stage1["frame_allowlist"])),
        "effect_contracts": copy.deepcopy(checked["effect_contracts"]),
        "requirement_effect_allowlist": requirement_effects,
        "decision_rule": (
            "Choose reusable capabilities only from the exact effect contracts; "
            "cover each requirement once or place it in one disposition array."
        ),
    }


def reference_design_body(
    projection: Mapping[str, Any],
    task_descriptions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deterministic full-coverage fixture used only by the reference baseline."""

    contracts = projection["effect_contracts"]
    requirement_effects = projection["requirement_effect_allowlist"]
    grouped: dict[str, list[str]] = defaultdict(list)
    for task in task_descriptions:
        requirement_id = str(task["requirement_id"])
        effect = str(requirement_effects[requirement_id][0])
        grouped[effect].append(requirement_id)
    capabilities: list[dict[str, Any]] = []
    for effect, requirement_ids in grouped.items():
        contract = contracts[effect]
        capabilities.append({
            "capability_id": effect,
            "kind": contract["kind"],
            "requirement_ids": requirement_ids,
            "inputs": copy.deepcopy(contract["inputs"]),
            "outputs": copy.deepcopy(contract["outputs"]),
            "effect": effect,
            "preconditions": ["robot model is reset to the selected task scene"],
            "invocation_semantics": contract["description"],
            "temporal_semantics": "Return after the bounded physical motion completes.",
            "invariants": ["Report only motion executed by the returned driver object."],
            "required_action_affordances": copy.deepcopy(contract["required_action_affordances"]),
            "required_observation_affordances": copy.deepcopy(contract["required_observation_affordances"]),
            "errors": [{"code": "MOTION_REJECTED", "message": "The requested physical effect failed."}],
            "unsupported_scope": [],
        })
    return {
        "capabilities": capabilities,
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }


def decision_scope(
    design: Mapping[str, Any],
    public_tasks: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> DecisionScope:
    """Validate Demo2 policy semantics and derive the sole downstream scope."""

    task_ids = [str(task["task_id"]) for task in public_tasks]
    checked = validate_policy(policy, task_ids)
    req_to_task = {
        str(task["requirement_id"]): str(task["task_id"])
        for task in public_tasks
    }
    contracts = checked["effect_contracts"]
    task_effects = checked["task_effect_allowlist"]
    unsupported = tuple(str(value) for value in design.get("unsupported_requirement_ids", []))
    blocking = tuple(str(value) for value in design.get("blocking_requirement_ids", []))
    if set(unsupported) & set(blocking):
        raise ValueError("Stage1 unsupported and blocking dispositions must be disjoint")

    selected_ids: list[str] = []
    selected_effects: list[str] = []
    req_to_capability: dict[str, str] = {}
    covered_in_order: list[str] = []
    for capability in design.get("capabilities", []):
        if not isinstance(capability, Mapping):
            raise ValueError("Stage1 capability must be an object")
        capability_id = str(capability["capability_id"])
        effect = str(capability["effect"])
        if effect not in contracts:
            raise ValueError(f"Stage1 capability {capability_id!r} uses an unknown effect")
        contract = contracts[effect]
        for field in (
            "kind",
            "inputs",
            "outputs",
            "required_action_affordances",
            "required_observation_affordances",
        ):
            if capability.get(field) != contract[field]:
                raise ValueError(
                    f"Stage1 capability {capability_id!r} must copy exact {effect!r} {field}"
                )
        selected_ids.append(capability_id)
        selected_effects.append(effect)
        for requirement_id in capability["requirement_ids"]:
            requirement = str(requirement_id)
            if requirement in req_to_capability:
                raise ValueError(
                    f"Demo2 requires one selected capability per requirement; duplicate {requirement!r}"
                )
            task_id = req_to_task.get(requirement)
            if task_id is None:
                raise ValueError(f"Stage1 capability references unknown requirement {requirement!r}")
            if effect not in task_effects[task_id]:
                raise ValueError(
                    f"Stage1 requirement {requirement!r} cannot be covered by effect {effect!r}"
                )
            req_to_capability[requirement] = capability_id
            covered_in_order.append(requirement)

    supplied = tuple(req_to_task)
    declared = set(covered_in_order) | set(unsupported) | set(blocking)
    if declared != set(supplied):
        raise ValueError("Stage1 decision does not disposition every Tasks Library requirement")
    if set(covered_in_order) & (set(unsupported) | set(blocking)):
        raise ValueError("Stage1 covered requirements cannot also be unresolved")
    covered = tuple(requirement for requirement in supplied if requirement in req_to_capability)
    covered_tasks = tuple(req_to_task[requirement] for requirement in covered)
    return DecisionScope(
        selected_capability_ids=tuple(selected_ids),
        selected_effects=tuple(dict.fromkeys(selected_effects)),
        covered_requirement_ids=covered,
        covered_task_ids=covered_tasks,
        unsupported_requirement_ids=unsupported,
        blocking_requirement_ids=blocking,
        requirement_to_capability=dict(req_to_capability),
        requirement_to_task=dict(req_to_task),
        full_task_coverage=not unsupported and not blocking and len(covered) == len(supplied),
    )
