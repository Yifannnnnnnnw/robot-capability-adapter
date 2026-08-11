from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from autoadapter2.foundation.errors import ContractError
from autoadapter2.generation import FixtureJsonGenerator, Stage1Runner
from autoadapter2.libraries import TasksLibrary


TASKS_ROOT = Path(__file__).parents[1] / "libraries" / "tasks"
G2 = {"profile_id": "g2-reusable-effect", "version": "1.0.0", "granularity": "G2"}
ROBOT = {
    "robot_model_id": "so-arm101",
    "robot_configuration_id": "so-arm101-follower-stock-gripper",
    "action_affordances": ["joint target command"],
    "observation_affordances": ["joint position observation"],
    "unit_allowlist": ["rad"],
    "frame_allowlist": ["joint", "base"],
}


def _copy_library(tmp_path: Path) -> Path:
    target = tmp_path / "tasks"
    shutil.copytree(TASKS_ROOT, target)
    return target


def _rewrite(path: Path, mutate) -> None:
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))


def _stage1_body(requirement_ids: list[str]) -> dict:
    return {
        "capabilities": [{
            "capability_id": "public-joint-action",
            "kind": "action",
            "requirement_ids": requirement_ids,
            "inputs": [{"name": "target", "type": "number", "shape": "scalar", "unit": "rad", "frame": "joint", "required": True}],
            "outputs": [],
            "effect": "Moves a selected public joint target.",
            "preconditions": ["robot is connected"],
            "invocation_semantics": "Invoke with one public target.",
            "temporal_semantics": "Returns after bounded observation.",
            "invariants": ["reports public errors"],
            "required_action_affordances": ["joint target command"],
            "required_observation_affordances": ["joint position observation"],
            "errors": [{"code": "TARGET_REJECTED", "message": "Target cannot be accepted."}],
            "unsupported_scope": [],
        }],
        "unsupported_requirement_ids": [],
        "blocking_requirement_ids": [],
    }


def test_loads_exact_admitted_packages_and_keeps_stage1_projection_private_free() -> None:
    library = TasksLibrary(TASKS_ROOT)
    so = library.load("so-arm101-follower-stock-gripper", "1.0.0")
    go2 = library.load("unitree-go2-stock-12dof", "1.0.0")

    so_stage1 = so.stage1_projection("so-run-1")
    go2_stage1 = go2.stage1_projection("go2-run-1")
    assert len(so_stage1) == len(so.demo_public_tasks("so-run-1")) == len(so.demo_private_criteria()) == 5
    assert len(go2_stage1) == len(go2.demo_public_tasks("go2-run-1")) == len(go2.demo_private_criteria()) == 5
    assert [item["task_id"] for item in go2.demo_public_tasks("go2-run-1")] == ["G01", "G02", "G03", "G04", "G05"]
    assert all(set(task) == {"requirement_id", "description"} for task in so_stage1 + go2_stage1)
    public = json.dumps(go2_stage1)
    assert "criterion" not in public.lower() and "G06" not in public
    assert all(item["requirement_id"].startswith("req-") for item in so_stage1 + go2_stage1)
    assert not any(token in public for token in ("G01", "unitree", "go2"))
    assert go2.stage1_projection("go2-run-1") == go2.stage1_projection("go2-run-1")
    assert go2.stage1_projection("go2-run-1") != go2.stage1_projection("go2-run-2")
    assert [item["requirement_id"] for item in go2_stage1] == [item["requirement_id"] for item in go2.demo_public_tasks("go2-run-1")]
    static_template = json.loads(
        (TASKS_ROOT / "unitree-go2-stock-12dof/1.0.0/stage1_projection.json").read_text()
    )
    assert all(set(item) == {"task_id", "description"} for item in static_template["tasks"])
    assert "requirement_id" not in json.dumps(static_template)


def test_projection_is_copied_and_can_be_consumed_by_stage1_without_private_data() -> None:
    package = TasksLibrary(TASKS_ROOT).load("so-arm101-follower-stock-gripper", "1.0.0")
    tasks = package.stage1_projection("tasks-library-stage1")
    pristine = copy.deepcopy(tasks)
    tasks[0]["description"] = "mutated caller value"
    assert package.stage1_projection("tasks-library-stage1") == pristine

    fixture = FixtureJsonGenerator([_stage1_body([task["requirement_id"] for task in pristine])])
    result = Stage1Runner(fixture).run("tasks-library-stage1", ROBOT, pristine, G2)
    assert result.status == "SEALED"
    assert fixture.calls[0]["inputs"]["task_descriptions"] == pristine
    assert "criterion" not in json.dumps(fixture.calls[0]["inputs"]).lower()


def test_go2_candidates_are_review_only_and_unknown_ids_fail_closed() -> None:
    package = TasksLibrary(TASKS_ROOT).load("unitree-go2-stock-12dof", "1.0.0")
    candidates = package.review_candidates()
    assert len(candidates) == 21
    assert {item["task_id"] for item in candidates} == {f"G{number:02d}" for number in range(6, 27)}
    assert [item["task_id"] for item in package.review_candidates(["G06", "G26"])] == ["G06", "G26"]
    with pytest.raises(ContractError, match="unknown review candidate"):
        package.review_candidates(["G01"])


@pytest.mark.parametrize(
    ("relative", "mutate"),
    [
        ("index.json", lambda value: value["catalogs"][0].__setitem__("catalog_path", "../outside.json")),
        ("index.json", lambda value: value["catalogs"][0].__setitem__("task_count", 27)),
        ("so-arm101-follower-stock-gripper/1.0.0/catalog.json", lambda value: value.__setitem__("catalog_review_status", "NOT_APPROVED")),
        ("so-arm101-follower-stock-gripper/1.0.0/catalog.json", lambda value: value["tasks"][0].__setitem__("task_review_status", "PROPOSED_REVIEW_REQUIRED")),
        ("so-arm101-follower-stock-gripper/1.0.0/demo_collection.json", lambda value: value.__setitem__("review_status", "NOT_APPROVED")),
        ("so-arm101-follower-stock-gripper/1.0.0/demo_collection.json", lambda value: value["task_ids"].__setitem__(0, "G01")),
        ("so-arm101-follower-stock-gripper/1.0.0/stage1_projection.json", lambda value: value["tasks"][0].__setitem__("private_criterion", "leak")),
        ("so-arm101-follower-stock-gripper/1.0.0/evaluation_private.json", lambda value: value["criteria"].__setitem__(0, {"task_id": "unknown", "criterion_status": "HUMAN_APPROVED"})),
        ("unitree-go2-stock-12dof/1.0.0/candidate_review_queue.json", lambda value: value["candidates"].__setitem__(0, {"task_id": "G01", "requirement_id": "unitree-go2-g01", "description": "bad duplicate"})),
        ("unitree-go2-stock-12dof/1.0.0/candidate_review_queue.json", lambda value: value["candidates"][0].__setitem__("private_criterion_status", "HUMAN_APPROVED")),
    ],
)
def test_rejects_tampering_cross_robot_mismatch_counts_and_private_leakage(tmp_path: Path, relative: str, mutate) -> None:
    root = _copy_library(tmp_path)
    _rewrite(root / relative, mutate)
    library = TasksLibrary(root)
    configuration = "unitree-go2-stock-12dof" if relative.startswith("unitree") else "so-arm101-follower-stock-gripper"
    with pytest.raises(ContractError):
        library.load(configuration, "1.0.0")
