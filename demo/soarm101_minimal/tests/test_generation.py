from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import soarm_demo.generation as generation_module

from soarm_demo.audit import BudgetCounter, sha256_bytes, sha256_json
from soarm_demo.generation import (
    ContinuousGeneration,
    GenerationWorkspace,
    GenerationWorkspaceError,
    _manifest_binding_semantic_issues,
    repair_ledger_semantic_issues,
    repair_tools,
    stage1_tools,
    stage2_tools,
)
from soarm_demo.model_client import ModelAPIError, ScriptedModelClient
from soarm_demo.react_agent import GenerationReActAgent, ToolSpec
from soarm_demo.schema_validation import validate_json_schema


ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path: Path) -> GenerationWorkspace:
    snapshot = tmp_path / "input_snapshot"
    snapshot.mkdir()
    (snapshot / "tasks.json").write_text('{"task":"visible"}\n', encoding="utf-8")
    return GenerationWorkspace(
        input_snapshot=snapshot,
        run_root=tmp_path / "run",
        allow_orchestration_fixture_evidence=True,
    )


def _commit_ascii_python_via_staging(
    workspace: GenerationWorkspace,
    *,
    path: str,
    content: str,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Publish an ASCII test module through the production chunk protocol."""

    assert content.isascii()
    state = workspace.begin_generated_file_write(
        {
            "path": path,
            "expected_target": expected_target or {"state": "absent"},
        }
    )
    for index, start in enumerate(range(0, len(content), 6_000)):
        state = workspace.append_generated_file_chunk(
            {
                "transaction_id": state["transaction_id"],
                "chunk_index": index,
                "expected_draft_sha256": state["draft_sha256"],
                "content": content[start : start + 6_000],
            }
        )
    return workspace.commit_generated_file_write(
        {
            "transaction_id": state["transaction_id"],
            "expected_draft_sha256": state["draft_sha256"],
            "expected_chunk_count": state["chunk_count"],
            "expected_bytes": len(content.encode("utf-8")),
        }
    )


def _repair_only_workspace(package_root: Path) -> SimpleNamespace:
    def checkpoint(*, repair_round: int, package_tree_digest: str) -> dict[str, Any]:
        return {
            "schema_version": "test.repair_context_checkpoint.v1",
            "repair_round": repair_round,
            "generated_package": {"tree_sha256": package_tree_digest},
        }

    return SimpleNamespace(
        package_root=package_root,
        repair_context_checkpoint=checkpoint,
        replace_generated_file_text=lambda arguments: dict(arguments),
        search_generated_file_text=lambda arguments: dict(arguments),
        read_generated_python_symbol=lambda arguments: dict(arguments),
    )


def _stage1_script(stage1: dict[str, Any]) -> list[str]:
    return [
        json.dumps(
            {
                "type": "tool",
                "tool": "read_generation_snapshot",
                "arguments": {},
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "review_stage1",
                "arguments": {"artifact": stage1},
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "submit_stage1",
                "arguments": {"artifact": stage1},
            }
        ),
    ]


def _review_and_submit_stage1(
    workspace: GenerationWorkspace,
    artifact: dict[str, Any],
) -> None:
    workspace.read_generation_snapshot()
    review = workspace.review_stage1({"artifact": artifact})
    assert review["reviewed"] is True
    assert review["valid"] is True
    assert workspace.submit_stage1({"artifact": artifact})["accepted"] is True


def test_stage1_script_uses_exactly_three_calls_and_freezes_artifact(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    client = ScriptedModelClient(_stage1_script(stage1_artifact))
    generation = ContinuousGeneration.create(
        client=client,
        workspace=workspace,
        system_prompt="Read inputs, then submit Stage 1.",
        trace_path=tmp_path / "generation.jsonl",
        stage1_call_limit=3,
    )

    frozen = generation.run_stage1("Propose G1, G2, and G3 capabilities.")

    assert len(client.requests) == 3
    assert generation.stage1_result is not None
    assert generation.stage1_result.model_calls == 3
    assert "accepted on the final allowed request" in generation.stage1_result.content
    assert frozen.sha256 == sha256_json(stage1_artifact)
    assert json.loads(frozen.path.read_text(encoding="utf-8")) == stage1_artifact
    assert (workspace.stage1_dir / "freeze.json").is_file()
    assert generation.agent.phase == "stage1"
    first_request = "\n".join(message.content for message in client.requests[0])
    assert "robot_capability.stage1.v2" in first_request
    assert '"additionalProperties": false' in first_request


def test_workspace_persists_detached_ordered_public_snapshot_manifest(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    manifest_path = workspace.input_snapshot_manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path.parent == workspace.run_root
    assert workspace.input_snapshot not in manifest_path.parents
    assert manifest["schema_version"] == (
        "robot_capability.generation_input_snapshot_manifest.v1"
    )
    assert manifest["files"] == [
        {
            "path": "tasks.json",
            "bytes": 19,
            "sha256": sha256_bytes(b'{"task":"visible"}\n'),
        }
    ]
    assert manifest["file_count"] == 1
    assert manifest["total_bytes"] == 19
    assert manifest["privacy_contract"]["file_content_embedded"] is False
    assert '"task"' not in json.dumps(manifest, ensure_ascii=False)
    assert manifest_path.stat().st_mode & 0o222 == 0
    assert workspace.input_snapshot_manifest_audit == {
        "schema_version": manifest["schema_version"],
        "path": "generation_input_snapshot_manifest.json",
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "tree_sha256": manifest["tree_sha256"],
        "file_count": 1,
        "total_bytes": 19,
    }


def test_stage2_refuses_snapshot_modified_after_stage1_without_model_call(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    client = ScriptedModelClient(_stage1_script(stage1_artifact))
    generation = ContinuousGeneration.create(
        client=client,
        workspace=workspace,
        system_prompt="Read, review, and submit Stage 1.",
        trace_path=tmp_path / "generation.jsonl",
    )
    generation.run_stage1("Propose capabilities.")
    requests_after_stage1 = len(client.requests)

    (workspace.input_snapshot / "tasks.json").write_text(
        '{"task":"changed"}\n',
        encoding="utf-8",
    )

    with pytest.raises(GenerationWorkspaceError, match="integrity violation"):
        generation.run_stage2("Implement the frozen capabilities.")

    assert len(client.requests) == requests_after_stage1 == 3
    assert generation.stage2_result is None


@pytest.mark.parametrize("mutation", ["add", "delete"])
def test_every_generation_tool_rechecks_exact_snapshot_allowlist(
    tmp_path: Path,
    mutation: str,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    tool = stage2_tools(workspace)["write_generated_file"]
    if mutation == "add":
        (workspace.input_snapshot / "injected.json").write_text(
            '{}\n',
            encoding="utf-8",
        )
    else:
        (workspace.input_snapshot / "tasks.json").unlink()

    with pytest.raises(GenerationWorkspaceError, match="integrity violation"):
        tool.handler(
            {
                "path": "generated_capability_package/g1.py",
                "content": "x = 1\n",
            }
        )

    assert not (
        workspace.package_root / "generated_capability_package/g1.py"
    ).exists()


def test_stage1_accepts_corrected_artifact_after_failed_review_on_final_call(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "read_generation_snapshot",
                    "arguments": {},
                }
            ),
            json.dumps(
                {
                    "type": "tool",
                    "tool": "review_stage1",
                    "arguments": {"artifact": {}},
                }
            ),
            json.dumps(
                {
                    "type": "tool",
                    "tool": "submit_stage1",
                    "arguments": {"artifact": stage1_artifact},
                }
            ),
        ]
    )
    generation = ContinuousGeneration.create(
        client=client,
        workspace=workspace,
        system_prompt="Read once, review, then submit or correct.",
        trace_path=tmp_path / "generation.jsonl",
        stage1_call_limit=3,
    )

    frozen = generation.run_stage1("Propose capabilities.")

    assert len(client.requests) == 3
    assert generation.stage1_result is not None
    assert generation.stage1_result.agent_turn_budget["remaining"] == 0
    assert "accepted on the final allowed request" in generation.stage1_result.content
    assert frozen.sha256 == sha256_json(stage1_artifact)


def test_stage1_three_call_flow_survives_one_missing_outer_closer(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    actions = [
        json.dumps(
            {
                "type": "tool",
                "tool": "read_generation_snapshot",
                "arguments": {},
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "review_stage1",
                "arguments": {"artifact": {}},
            }
        )[:-1],
        json.dumps(
            {
                "type": "tool",
                "tool": "submit_stage1",
                "arguments": {"artifact": stage1_artifact},
            }
        )[:-1],
    ]
    workspace = _workspace(tmp_path)
    generation = ContinuousGeneration.create(
        client=ScriptedModelClient(actions),
        workspace=workspace,
        system_prompt="Read, review, then submit within three calls.",
        trace_path=tmp_path / "generation.jsonl",
        stage1_call_limit=3,
    )

    frozen = generation.run_stage1("Propose capabilities.")

    assert frozen.sha256 == sha256_json(stage1_artifact)
    records = [
        json.loads(line)
        for line in (tmp_path / "generation.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    normalized = [
        record
        for record in records
        if record.get("event") == "model_action_normalized"
    ]
    assert [record["source"] for record in normalized] == [
        "json_single_missing_root_closer",
        "json_single_missing_root_closer",
    ]


def test_stage1_freeze_cannot_be_resubmitted_or_mutated_in_memory(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()
    original = copy.deepcopy(stage1_artifact)

    with pytest.raises(GenerationWorkspaceError, match="cannot be changed"):
        workspace.submit_stage1({"artifact": stage1_artifact})
    with pytest.raises(GenerationWorkspaceError, match="cannot be changed"):
        workspace.review_stage1({"artifact": stage1_artifact})

    # A caller may receive the frozen object, but mutating that value must not
    # alter the authoritative Stage 1 subsequently read by Stage 2/repair.
    try:
        frozen.artifact["target"]["robot_id"] = "mutated"  # type: ignore[index]
    except TypeError:
        pass
    assert workspace.read_frozen_stage1()["artifact"] == original
    assert workspace.frozen_stage1.sha256 == sha256_json(original)


def test_stage1_submission_requires_snapshot_read_first(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)

    rejected = workspace.submit_stage1({"artifact": stage1_artifact})

    assert rejected["accepted"] is False
    assert "read_generation_snapshot" in rejected["issues"][0]["message"]
    with pytest.raises(GenerationWorkspaceError, match="without an accepted artifact"):
        workspace.freeze_stage1()

    workspace.read_generation_snapshot()
    missing_review = workspace.submit_stage1({"artifact": stage1_artifact})
    assert missing_review["accepted"] is False
    assert "review_stage1" in missing_review["issues"][0]["message"]
    assert workspace.review_stage1({"artifact": stage1_artifact})["valid"] is True
    assert workspace.submit_stage1({"artifact": stage1_artifact})["accepted"] is True


def test_stage1_valid_review_is_non_mutating_and_locks_canonical_content(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    workspace.read_generation_snapshot()

    review = workspace.review_stage1({"artifact": stage1_artifact})

    assert review == {
        "reviewed": True,
        "valid": True,
        "issues": [],
        "sha256": sha256_json(stage1_artifact),
    }
    assert not (workspace.stage1_dir / "candidate.json").exists()
    with pytest.raises(GenerationWorkspaceError, match="without an accepted artifact"):
        workspace.freeze_stage1()

    changed = copy.deepcopy(stage1_artifact)
    changed["assumptions"] = [*changed["assumptions"], "Changed after review."]
    rejected = workspace.submit_stage1({"artifact": changed})
    assert rejected["accepted"] is False
    assert "canonically identical" in rejected["issues"][0]["message"]
    assert not (workspace.stage1_dir / "candidate.json").exists()

    reordered = {key: stage1_artifact[key] for key in reversed(stage1_artifact)}
    assert workspace.submit_stage1({"artifact": reordered})["accepted"] is True


def test_stage1_failed_review_allows_one_fully_validated_correction(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    workspace.read_generation_snapshot()

    review = workspace.review_stage1({"artifact": {}})

    assert review["reviewed"] is True
    assert review["valid"] is False
    assert review["issues"]
    assert not (workspace.stage1_dir / "candidate.json").exists()
    assert workspace.submit_stage1({"artifact": stage1_artifact})["accepted"] is True


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_stage1_rejects_non_finite_json_numbers(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
    number: float,
) -> None:
    workspace = _workspace(tmp_path)
    workspace.read_generation_snapshot()
    artifact = copy.deepcopy(stage1_artifact)
    artifact["assumptions"] = [number]

    review = workspace.review_stage1({"artifact": artifact})
    rejected = workspace.submit_stage1({"artifact": artifact})

    assert review["valid"] is False
    assert rejected["accepted"] is False
    assert any("non-finite" in issue["message"] for issue in rejected["issues"])


def test_stage1_tools_expose_review_before_schema_enforced_submission(
    tmp_path: Path,
) -> None:
    tools = stage1_tools(_workspace(tmp_path))

    assert list(tools) == [
        "read_generation_snapshot",
        "review_stage1",
        "submit_stage1",
    ]
    assert tools["review_stage1"].input_schema["properties"]["artifact"] == {}
    assert (
        tools["submit_stage1"].input_schema["properties"]["artifact"]["$id"]
        == "robot_capability.stage1.v2"
    )


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "generated_capability_package/../../escape.py",
        "/tmp/escape.py",
        "generated_capability_package/private.py",
        "generated_capability_package/g1.py/child",
    ],
)
def test_generated_file_writer_enforces_exact_path_allowlist(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(GenerationWorkspaceError, match="allowlist"):
        workspace.write_generated_file({"path": path, "content": "x = 1\n"})

    assert not (tmp_path / "escape.py").exists()


def test_generated_file_writer_accepts_only_declared_package_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = workspace.write_generated_file(
        {
            "path": "generated_capability_package/g1.py",
            "content": "def command_joint(runtime: object, position: float) -> dict[str, object]:\n    return {'status': 'ok'}\n",
        }
    )

    assert result["path"] == "generated_capability_package/g1.py"
    assert (workspace.package_root / result["path"]).is_file()


def test_direct_python_write_requires_chunk_transaction_above_8192_utf8_bytes(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(GenerationWorkspaceError, match="8192-byte limit"):
        workspace.write_generated_file(
            {
                "path": "generated_capability_package/g3.py",
                "content": "#" * 8_193,
            }
        )
    # Character count is not a substitute for the enforced UTF-8 byte count.
    with pytest.raises(GenerationWorkspaceError, match="8192-byte limit"):
        workspace.write_generated_file(
            {
                "path": "generated_capability_package/g3.py",
                "content": "界" * 3_000,
            }
        )


def test_staged_chunk_write_keeps_target_unchanged_until_ast_checked_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = workspace.write_generated_file({"path": path, "content": "old = 1\n"})
    begin = workspace.begin_generated_file_write(
        {
            "path": path,
            "expected_target": {
                "state": "present",
                "sha256": original["sha256"],
            },
        }
    )

    assert len(begin["transaction_id"]) == 32
    assert set(begin["transaction_id"]) <= set("0123456789abcdef")
    draft = workspace.generated_write_staging_root / (
        begin["transaction_id"] + ".draft"
    )
    assert draft.is_file()
    assert workspace.package_root not in draft.parents
    blocked = workspace.finish_package()
    assert blocked["complete"] is False
    assert blocked["errors"][0]["code"] == "OPEN_GENERATED_FILE_TRANSACTION"

    appended = workspace.append_generated_file_chunk(
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "content": "def broken(:\n",
        }
    )
    with pytest.raises(GenerationWorkspaceError, match="AST parsing"):
        workspace.commit_generated_file_write(
            {
                "transaction_id": begin["transaction_id"],
                "expected_draft_sha256": appended["draft_sha256"],
                "expected_chunk_count": 1,
                "expected_bytes": appended["bytes"],
            }
        )
    assert (workspace.package_root / path).read_text(encoding="utf-8") == "old = 1\n"
    assert draft.is_file()

    aborted = workspace.abort_generated_file_write(
        {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
        }
    )
    assert aborted["aborted"] is True
    assert not draft.exists()


def test_staged_chunk_write_enforces_sequence_hash_size_and_base_preconditions(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    begin = workspace.begin_generated_file_write(
        {"path": path, "expected_target": {"state": "absent"}}
    )
    target = workspace.package_root / path

    invalid_appends = [
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 1,
            "expected_draft_sha256": begin["draft_sha256"],
            "content": "x = 1\n",
        },
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 0,
            "expected_draft_sha256": "0" * 64,
            "content": "x = 1\n",
        },
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "content": "界" * 2_049,
        },
    ]
    for arguments in invalid_appends:
        with pytest.raises(GenerationWorkspaceError):
            workspace.append_generated_file_chunk(arguments)
        assert not target.exists()

    appended = workspace.append_generated_file_chunk(
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "content": "x = 2\n",
        }
    )
    for overrides in (
        {"expected_draft_sha256": "0" * 64},
        {"expected_chunk_count": 2},
        {"expected_bytes": appended["bytes"] + 1},
    ):
        commit = {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
            "expected_chunk_count": 1,
            "expected_bytes": appended["bytes"],
        }
        commit.update(overrides)
        with pytest.raises(GenerationWorkspaceError, match="precondition"):
            workspace.commit_generated_file_write(commit)
        assert not target.exists()

    # A competing writer outside this workspace invalidates the begin-time
    # absent base. In-workspace direct writes are separately ownership-blocked.
    generation_module._atomic_write_text(target, "competitor = 3\n")
    with pytest.raises(GenerationWorkspaceError, match="expected absent"):
        workspace.commit_generated_file_write(
            {
                "transaction_id": begin["transaction_id"],
                "expected_draft_sha256": appended["draft_sha256"],
                "expected_chunk_count": 1,
                "expected_bytes": appended["bytes"],
            }
        )
    assert target.read_text(encoding="utf-8") == "competitor = 3\n"
    workspace.abort_generated_file_write(
        {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
        }
    )


def test_open_staged_transaction_owns_path_against_direct_python_write(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    begin = workspace.begin_generated_file_write(
        {"path": path, "expected_target": {"state": "absent"}}
    )

    with pytest.raises(GenerationWorkspaceError, match="already owns this path"):
        workspace.write_generated_file(
            {
                "path": path,
                # Ownership is checked before decoding or validating content.
                "content": object(),
            }
        )

    assert not (workspace.package_root / path).exists()
    workspace.abort_generated_file_write(
        {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": begin["draft_sha256"],
        }
    )


def test_batched_chunk_append_advances_logical_indexes_and_remains_single_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    begin = workspace.begin_generated_file_write(
        {"path": path, "expected_target": {"state": "absent"}}
    )
    chunks = ["first = 1\n", "second = 2\n", "third = 3\n"]

    batched = workspace.append_generated_file_chunks(
        {
            "transaction_id": begin["transaction_id"],
            "start_chunk_index": begin["next_chunk_index"],
            "expected_draft_sha256": begin["draft_sha256"],
            "chunks": chunks,
        }
    )

    assert batched["accepted_start_chunk_index"] == 0
    assert batched["accepted_chunk_count"] == 3
    assert batched["chunk_count"] == batched["next_chunk_index"] == 3
    assert batched["bytes"] == len("".join(chunks).encode("utf-8"))
    assert batched["accepted_chunks"] == [
        {
            "chunk_index": index,
            "bytes": len(chunk.encode("utf-8")),
            "sha256": sha256_bytes(chunk.encode("utf-8")),
        }
        for index, chunk in enumerate(chunks)
    ]

    # The existing one-chunk operation remains compatible with the index/hash
    # returned by a batch.
    final = workspace.append_generated_file_chunk(
        {
            "transaction_id": batched["transaction_id"],
            "chunk_index": batched["next_chunk_index"],
            "expected_draft_sha256": batched["draft_sha256"],
            "content": "fourth = 4\n",
        }
    )
    committed = workspace.commit_generated_file_write(
        {
            "transaction_id": final["transaction_id"],
            "expected_draft_sha256": final["draft_sha256"],
            "expected_chunk_count": 4,
            "expected_bytes": final["bytes"],
        }
    )
    assert committed["chunk_count"] == 4
    assert (workspace.package_root / path).read_text(encoding="utf-8") == (
        "".join(chunks) + "fourth = 4\n"
    )


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"start_chunk_index": 1}, "start_chunk_index"),
        ({"expected_draft_sha256": "0" * 64}, "hash chain"),
        ({"chunks": []}, "between 1 and 3"),
        ({"chunks": ["a", "b", "c", "d"]}, "between 1 and 3"),
        ({"chunks": ["valid first", ""]}, "must be non-empty"),
        ({"chunks": ["valid first", "界" * 2_049]}, "6144 UTF-8 bytes"),
    ],
)
def test_batched_chunk_append_rejects_entire_batch_without_partial_write(
    tmp_path: Path,
    overrides: dict[str, Any],
    error: str,
) -> None:
    workspace = _workspace(tmp_path)
    begin = workspace.begin_generated_file_write(
        {
            "path": "generated_capability_package/g3.py",
            "expected_target": {"state": "absent"},
        }
    )
    draft = workspace.generated_write_staging_root / (
        begin["transaction_id"] + ".draft"
    )
    arguments: dict[str, Any] = {
        "transaction_id": begin["transaction_id"],
        "start_chunk_index": 0,
        "expected_draft_sha256": begin["draft_sha256"],
        "chunks": ["valid = 1\n", "also_valid = 2\n"],
    }
    arguments.update(overrides)

    with pytest.raises(GenerationWorkspaceError, match=error):
        workspace.append_generated_file_chunks(arguments)

    assert draft.read_bytes() == b""
    # An invalid batch did not advance any in-memory transaction state either.
    accepted = workspace.append_generated_file_chunks(
        {
            "transaction_id": begin["transaction_id"],
            "start_chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "chunks": ["recovered = True\n"],
        }
    )
    assert accepted["accepted_start_chunk_index"] == 0
    assert accepted["chunk_count"] == 1
    workspace.abort_generated_file_write(
        {
            "transaction_id": accepted["transaction_id"],
            "expected_draft_sha256": accepted["draft_sha256"],
        }
    )


@pytest.mark.parametrize("batched", [False, True])
def test_chunk_atomic_write_failure_preserves_all_transaction_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batched: bool,
) -> None:
    workspace = _workspace(tmp_path)
    begin = workspace.begin_generated_file_write(
        {
            "path": "generated_capability_package/g3.py",
            "expected_target": {"state": "absent"},
        }
    )
    draft = workspace.generated_write_staging_root / (
        begin["transaction_id"] + ".draft"
    )
    before_bytes = draft.read_bytes()
    original_atomic_write = generation_module._atomic_write_text

    def fail_atomic_write(path: Path, content: str) -> None:
        raise OSError("injected atomic replacement failure")

    monkeypatch.setattr(generation_module, "_atomic_write_text", fail_atomic_write)
    with pytest.raises(OSError, match="injected atomic replacement failure"):
        if batched:
            workspace.append_generated_file_chunks(
                {
                    "transaction_id": begin["transaction_id"],
                    "start_chunk_index": 0,
                    "expected_draft_sha256": begin["draft_sha256"],
                    "chunks": ["first = 1\n", "second = 2\n"],
                }
            )
        else:
            workspace.append_generated_file_chunk(
                {
                    "transaction_id": begin["transaction_id"],
                    "chunk_index": 0,
                    "expected_draft_sha256": begin["draft_sha256"],
                    "content": "first = 1\n",
                }
            )

    transaction = workspace._generated_file_transactions[begin["transaction_id"]]
    assert draft.read_bytes() == before_bytes == b""
    assert transaction.draft_sha256 == begin["draft_sha256"]
    assert transaction.chunk_count == begin["chunk_count"] == 0
    assert transaction.bytes_written == begin["bytes"] == 0

    # The same precondition remains usable once the injected storage fault is
    # removed, proving the failed call did not consume the index/hash chain.
    monkeypatch.setattr(
        generation_module, "_atomic_write_text", original_atomic_write
    )
    accepted = workspace.append_generated_file_chunks(
        {
            "transaction_id": begin["transaction_id"],
            "start_chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "chunks": ["recovered = True\n"],
        }
    )
    assert accepted["chunk_count"] == 1
    workspace.abort_generated_file_write(
        {
            "transaction_id": accepted["transaction_id"],
            "expected_draft_sha256": accepted["draft_sha256"],
        }
    )


@pytest.mark.parametrize("batched", [False, True])
def test_chunk_append_has_no_fallible_hash_read_after_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batched: bool,
) -> None:
    workspace = _workspace(tmp_path)
    begin = workspace.begin_generated_file_write(
        {
            "path": "generated_capability_package/g3.py",
            "expected_target": {"state": "absent"},
        }
    )
    original_sha256_file = generation_module.sha256_file
    hash_reads = 0

    def reject_post_write_hash(path: Path) -> str:
        nonlocal hash_reads
        hash_reads += 1
        if hash_reads > 1:
            raise OSError("post-write hash read must not occur")
        return original_sha256_file(path)

    monkeypatch.setattr(generation_module, "sha256_file", reject_post_write_hash)
    contents = ["first = 1\n", "second = 2\n"] if batched else ["first = 1\n"]
    if batched:
        appended = workspace.append_generated_file_chunks(
            {
                "transaction_id": begin["transaction_id"],
                "start_chunk_index": 0,
                "expected_draft_sha256": begin["draft_sha256"],
                "chunks": contents,
            }
        )
    else:
        appended = workspace.append_generated_file_chunk(
            {
                "transaction_id": begin["transaction_id"],
                "chunk_index": 0,
                "expected_draft_sha256": begin["draft_sha256"],
                "content": contents[0],
            }
        )

    assert hash_reads == 1  # the pre-write transaction integrity check only
    expected_bytes = "".join(contents).encode("utf-8")
    assert appended["draft_sha256"] == sha256_bytes(expected_bytes)
    transaction = workspace._generated_file_transactions[begin["transaction_id"]]
    assert transaction.draft_sha256 == appended["draft_sha256"]
    assert transaction.bytes_written == len(expected_bytes)
    assert transaction.chunk_count == len(contents)
    # Restore normal integrity reads before asking the public abort operation
    # to verify and remove the draft.
    monkeypatch.setattr(generation_module, "sha256_file", original_sha256_file)
    workspace.abort_generated_file_write(
        {
            "transaction_id": appended["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
        }
    )


def test_present_target_change_rejects_batched_transaction_commit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    target = workspace.package_root / path
    original = workspace.write_generated_file({"path": path, "content": "old = 1\n"})
    begin = workspace.begin_generated_file_write(
        {
            "path": path,
            "expected_target": {"state": "present", "sha256": original["sha256"]},
        }
    )
    appended = workspace.append_generated_file_chunks(
        {
            "transaction_id": begin["transaction_id"],
            "start_chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "chunks": ["replacement = 2\n", "another = 3\n"],
        }
    )

    generation_module._atomic_write_text(target, "competitor = 4\n")
    with pytest.raises(GenerationWorkspaceError, match="SHA-256 changed"):
        workspace.commit_generated_file_write(
            {
                "transaction_id": appended["transaction_id"],
                "expected_draft_sha256": appended["draft_sha256"],
                "expected_chunk_count": 2,
                "expected_bytes": appended["bytes"],
            }
        )

    assert target.read_text(encoding="utf-8") == "competitor = 4\n"
    workspace.abort_generated_file_write(
        {
            "transaction_id": appended["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
        }
    )


def test_17500_byte_g3_three_chunk_commit_passes_authoritative_static_validation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    fixture_root = ROOT / "fixtures/generated_pass"
    manifest = json.loads(
        (fixture_root / "package_manifest.json").read_text(encoding="utf-8")
    )
    assert workspace.write_package_manifest({"manifest": manifest})["accepted"] is True
    for relative in (
        "generated_capability_package/__init__.py",
        "generated_capability_package/_kinematics.py",
        "generated_capability_package/g1.py",
        "generated_capability_package/g2.py",
    ):
        workspace.write_generated_file(
            {
                "path": relative,
                "content": (fixture_root / relative).read_text(encoding="utf-8"),
            }
        )

    relative = "generated_capability_package/g3.py"
    base = (fixture_root / relative).read_text(encoding="utf-8")
    large_g3 = base + "#" * (17_500 - len(base.encode("utf-8")))
    assert len(large_g3.encode("utf-8")) == 17_500
    chunks = (large_g3[:6_000], large_g3[6_000:12_000], large_g3[12_000:])
    assert all(len(chunk.encode("utf-8")) <= 6_144 for chunk in chunks)
    state = workspace.begin_generated_file_write(
        {"path": relative, "expected_target": {"state": "absent"}}
    )
    for index, chunk in enumerate(chunks):
        state = workspace.append_generated_file_chunk(
            {
                "transaction_id": state["transaction_id"],
                "chunk_index": index,
                "expected_draft_sha256": state["draft_sha256"],
                "content": chunk,
            }
        )
    committed = workspace.commit_generated_file_write(
        {
            "transaction_id": state["transaction_id"],
            "expected_draft_sha256": state["draft_sha256"],
            "expected_chunk_count": 3,
            "expected_bytes": 17_500,
        }
    )

    assert committed["committed"] is True
    assert committed["chunk_count"] == 3
    assert (workspace.package_root / relative).read_text(encoding="utf-8") == large_g3
    finished = workspace.finish_package()
    assert finished["complete"] is True, finished["errors"]


def test_stage2_exposes_strict_chunk_transaction_schemas(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    tools = stage2_tools(workspace)

    assert {
        "begin_generated_file_write",
        "append_generated_file_chunk",
        "append_generated_file_chunks",
        "commit_generated_file_write",
        "abort_generated_file_write",
    } <= set(tools)
    assert tools["write_generated_file"].input_schema["properties"]["content"][
        "maxLength"
    ] == 8_192
    append_schema = tools["append_generated_file_chunk"].input_schema
    assert append_schema["additionalProperties"] is False
    assert append_schema["properties"]["content"]["maxLength"] == 6_144
    assert append_schema["properties"]["transaction_id"]["pattern"] == (
        "^[0-9a-f]{32}$"
    )
    batch_schema = tools["append_generated_file_chunks"].input_schema
    assert batch_schema["additionalProperties"] is False
    assert set(batch_schema["required"]) == {
        "transaction_id",
        "start_chunk_index",
        "expected_draft_sha256",
        "chunks",
    }
    assert batch_schema["properties"]["chunks"]["minItems"] == 1
    assert batch_schema["properties"]["chunks"]["maxItems"] == 3
    assert batch_schema["properties"]["chunks"]["items"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 6_144,
    }
    expected_target = tools["begin_generated_file_write"].input_schema[
        "properties"
    ]["expected_target"]
    assert len(expected_target["oneOf"]) == 2
    assert "write_package_manifest" in tools
    assert "package_manifest.json" not in tools["write_generated_file"].input_schema[
        "properties"
    ]["path"]["enum"]
    support_path = "generated_capability_package/_kinematics.py"
    assert support_path in tools["write_generated_file"].input_schema["properties"][
        "path"
    ]["enum"]
    assert support_path in tools["begin_generated_file_write"].input_schema[
        "properties"
    ]["path"]["enum"]
    assert support_path in tools["read_generated_file"].input_schema["properties"][
        "path"
    ]["enum"]
    repair = repair_tools(workspace)
    assert "append_generated_file_chunks" in repair
    repair_with_stage2_base = repair_tools(workspace, base_tools=tools)
    assert "append_generated_file_chunks" in repair_with_stage2_base
    assert (
        repair_with_stage2_base["append_generated_file_chunks"].input_schema
        == tools["append_generated_file_chunks"].input_schema
    )
    assert support_path in repair["replace_generated_file_text"].input_schema[
        "properties"
    ]["path"]["enum"]
    assert support_path in repair["search_generated_file_text"].input_schema[
        "properties"
    ]["path"]["enum"]
    fixture_support = (
        ROOT
        / "fixtures/generated_pass/generated_capability_package/_kinematics.py"
    )
    assert fixture_support.stat().st_size <= 8_192


def test_stage2_batch_writes_three_chunks_in_one_turn_without_raising_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    fixture_root = ROOT / "fixtures/generated_pass"
    workspace.write_package_manifest(
        {
            "manifest": json.loads(
                (fixture_root / "package_manifest.json").read_text(encoding="utf-8")
            )
        }
    )
    written: dict[str, dict[str, Any]] = {}
    for relative in (
        "generated_capability_package/__init__.py",
        "generated_capability_package/_kinematics.py",
        "generated_capability_package/g1.py",
        "generated_capability_package/g2.py",
        "generated_capability_package/g3.py",
    ):
        written[relative] = workspace.write_generated_file(
            {
                "path": relative,
                "content": (fixture_root / relative).read_text(encoding="utf-8"),
            }
        )

    path = "generated_capability_package/g3.py"
    source = (fixture_root / path).read_text(encoding="utf-8")
    boundaries = (len(source) // 3, 2 * len(source) // 3)
    chunks = [
        source[: boundaries[0]],
        source[boundaries[0] : boundaries[1]],
        source[boundaries[1] :],
    ]
    transaction_id = "a" * 32
    monkeypatch.setattr(
        "soarm_demo.generation.uuid.uuid4",
        lambda: SimpleNamespace(hex=transaction_id),
    )
    responses = [
        json.dumps(
            {
                "type": "tool",
                "tool": "begin_generated_file_write",
                "arguments": {
                    "path": path,
                    "expected_target": {
                        "state": "present",
                        "sha256": written[path]["sha256"],
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "append_generated_file_chunks",
                "arguments": {
                    "transaction_id": transaction_id,
                    "start_chunk_index": 0,
                    "expected_draft_sha256": "e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855",
                    "chunks": chunks,
                },
            }
        ),
        json.dumps(
            {
                "type": "tool",
                "tool": "commit_generated_file_write",
                "arguments": {
                    "transaction_id": transaction_id,
                    "expected_draft_sha256": sha256_bytes(source.encode("utf-8")),
                    "expected_chunk_count": 3,
                    "expected_bytes": len(source.encode("utf-8")),
                },
            }
        ),
        json.dumps({"type": "final", "content": "batched module complete"}),
    ]
    client = ScriptedModelClient(responses)
    trace_path = tmp_path / "generation-batch-budget.jsonl"
    agent = GenerationReActAgent(
        agent_id="generation-batch-budget",
        role="generation",
        client=client,
        system_prompt="test",
        tools=stage1_tools(workspace),
        budget=BudgetCounter("generation_stage1", 3),
        trace_path=trace_path,
    )
    agent.set_phase("stage1")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        stage1_result=agent.accounting_result(status="completed"),
    )

    generation.run_stage2("rewrite G3 in one three-chunk batch", initial_call_limit=30)

    assert generation.stage2_result is not None
    assert generation.stage2_result.agent_turn_budget == {
        "name": "generation_stage2_initial",
        "limit": 30,
        "used": 4,
        "remaining": 26,
    }
    assert (workspace.package_root / path).read_text(encoding="utf-8") == source
    records = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    batch_result = next(
        record
        for record in records
        if record["event"] == "tool_result"
        and record["tool"] == "append_generated_file_chunks"
    )
    assert batch_result["result"]["ok"] is True
    assert batch_result["result"]["result"]["accepted_chunk_count"] == 3


def test_truncated_batched_append_action_is_never_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    begin = workspace.begin_generated_file_write(
        {
            "path": "generated_capability_package/g3.py",
            "expected_target": {"state": "absent"},
        }
    )
    truncated_action = json.dumps(
        {
            "type": "tool",
            "tool": "append_generated_file_chunks",
            "arguments": {
                "transaction_id": begin["transaction_id"],
                "start_chunk_index": 0,
                "expected_draft_sha256": begin["draft_sha256"],
                "chunks": ["must_not = 1\n", "execute = 2\n"],
            },
        }
    )
    client = ScriptedModelClient(
        [truncated_action, json.dumps({"type": "final", "content": "recovered"})]
    )
    original_complete = client.complete

    def complete_with_first_response_truncated(*args: object, **kwargs: object):
        response = original_complete(*args, **kwargs)
        if len(client.requests) == 1:
            return replace(
                response,
                output_truncated=True,
                stop_reason="max_tokens",
                requested_max_tokens=8192,
            )
        return response

    monkeypatch.setattr(client, "complete", complete_with_first_response_truncated)
    tools = stage2_tools(workspace)
    agent = GenerationReActAgent(
        agent_id="generation-truncated-batch",
        role="generation",
        client=client,
        system_prompt="Never execute truncated actions.",
        tools=tools,
        budget=BudgetCounter("generation_stage2_initial", 2),
        trace_path=tmp_path / "generation-truncated-batch.jsonl",
    )
    agent.set_phase("stage2", tools=tools)

    result = agent.run("Append only from a complete response.")

    assert result.status == "completed"
    draft = workspace.generated_write_staging_root / (
        begin["transaction_id"] + ".draft"
    )
    assert draft.read_bytes() == b""
    transaction = workspace._generated_file_transactions[begin["transaction_id"]]
    assert transaction.chunk_count == transaction.bytes_written == 0
    assert transaction.draft_sha256 == begin["draft_sha256"]
    workspace.abort_generated_file_write(
        {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": begin["draft_sha256"],
        }
    )


def test_stage2_can_bounded_read_and_exact_patch_after_late_finish_failure(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    path = "generated_capability_package/_kinematics.py"
    original = (
        "def _solve_ik(value: float) -> float:\n"
        "    def controlled_point(current: float) -> float:\n"
        "        return current\n"
        "    return controlled_point(value)\n"
    )
    written = workspace.write_generated_file({"path": path, "content": original})
    tools = stage2_tools(workspace)

    bounded = tools["read_generated_python_symbol"].handler(
        {"path": path, "line_number": 3}
    )
    replacement = (
        "def _solve_ik(value: float) -> float:\n"
        "    return value\n"
    )
    patched = tools["replace_generated_file_text"].handler(
        {
            "path": path,
            "old_text": bounded["source"],
            "new_text": replacement,
            "expected_sha256": bounded["sha256"],
        }
    )

    assert bounded["sha256"] == written["sha256"]
    assert bounded["symbol"] == "_solve_ik"
    assert bounded["requested_line_number"] == 3
    assert patched["before_sha256"] == written["sha256"]
    assert patched["after_sha256"] != written["sha256"]
    assert (workspace.package_root / path).read_text(encoding="utf-8") == replacement
    assert workspace.abort_all_generated_file_writes() == []


@pytest.mark.parametrize("terminal", ["final", "budget", "provider_error"])
def test_stage2_terminal_paths_abort_unpublished_chunk_drafts(
    tmp_path: Path,
    terminal: str,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    fixture_root = ROOT / "fixtures/generated_pass"
    manifest = json.loads(
        (fixture_root / "package_manifest.json").read_text(encoding="utf-8")
    )
    assert workspace.write_package_manifest({"manifest": manifest})["accepted"] is True
    for relative in sorted(
        {
            "generated_capability_package/__init__.py",
            "generated_capability_package/_kinematics.py",
            "generated_capability_package/g1.py",
            "generated_capability_package/g2.py",
            "generated_capability_package/g3.py",
        }
    ):
        workspace.write_generated_file(
            {
                "path": relative,
                "content": (fixture_root / relative).read_text(encoding="utf-8"),
            }
    )
    path = "generated_capability_package/g3.py"
    target = workspace.package_root / path
    before_sha256 = next(
        item["sha256"]
        for item in workspace.list_generated_files()["files"]
        if item["path"] == path
    )
    begin_action = json.dumps(
        {
            "type": "tool",
            "tool": "begin_generated_file_write",
            "arguments": {
                "path": path,
                "expected_target": {"state": "present", "sha256": before_sha256},
            },
        }
    )
    responses = [begin_action]
    call_limit = 1 if terminal == "budget" else 2
    if terminal == "final":
        responses.append(json.dumps({"type": "final", "content": "done"}))
    client = ScriptedModelClient(responses)
    agent = GenerationReActAgent(
        agent_id="generation-cleanup",
        role="generation",
        client=client,
        system_prompt="test",
        tools=stage1_tools(workspace),
        budget=BudgetCounter("generation_stage1", 3),
        trace_path=tmp_path / "generation.jsonl",
    )
    agent.set_phase("stage1")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        stage1_result=agent.accounting_result(status="completed"),
    )

    if terminal == "provider_error":
        with pytest.raises(ModelAPIError, match="queue exhausted"):
            generation.run_stage2("open a draft", initial_call_limit=call_limit)
    else:
        generation.run_stage2("open a draft", initial_call_limit=call_limit)

    assert target.is_file()
    assert next(
        item["sha256"]
        for item in workspace.list_generated_files()["files"]
        if item["path"] == path
    ) == before_sha256
    assert list(workspace.generated_write_staging_root.glob("*.draft")) == []
    assert workspace.finish_package()["complete"] is True


def test_repair_incremental_editor_applies_one_hash_guarded_unique_replacement(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "def move() -> str:\n    return 'before'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})

    result = workspace.replace_generated_file_text(
        {
            "path": path,
            "old_text": "return 'before'",
            "new_text": "return 'after'",
            "expected_sha256": initial["sha256"],
        }
    )

    expected = original.replace("return 'before'", "return 'after'")
    assert (workspace.package_root / path).read_text(encoding="utf-8") == expected
    assert result == {
        "path": path,
        "before_sha256": initial["sha256"],
        "after_sha256": workspace.list_generated_files()["files"][0]["sha256"],
        "before_bytes": len(original.encode("utf-8")),
        "after_bytes": len(expected.encode("utf-8")),
    }


def test_repair_incremental_editor_patches_module_larger_than_47_kib(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "#" + ("padding" * 7_100) + "\nvalue = 'before'\n"
    assert len(original.encode("utf-8")) > 47 * 1_024
    initial = _commit_ascii_python_via_staging(
        workspace,
        path=path,
        content=original,
    )

    result = workspace.replace_generated_file_text(
        {
            "path": path,
            "old_text": "value = 'before'",
            "new_text": "value = 'after'",
            "expected_sha256": initial["sha256"],
        }
    )

    expected = original.replace("value = 'before'", "value = 'after'")
    target = workspace.package_root / path
    assert target.read_text(encoding="utf-8") == expected
    assert result["before_sha256"] == initial["sha256"]
    assert result["after_sha256"] != result["before_sha256"]
    assert result["after_bytes"] > 47 * 1_024


def test_repair_search_returns_bounded_context_hash_and_exact_match_count(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    content = (
        "def first() -> dict[str, object]:\n"
        "    return {'status': 'timeout', 'phase_reached': 'lower'}\n"
        "\n"
        "def second() -> dict[str, object]:\n"
        "    return {'status': 'timeout', 'phase_reached': 'lower'}\n"
    )
    written = workspace.write_generated_file({"path": path, "content": content})

    result = workspace.search_generated_file_text(
        {"path": path, "query": "phase_reached", "context_lines": 1}
    )

    assert result["path"] == path
    assert result["sha256"] == written["sha256"]
    assert result["bytes"] == len(content.encode("utf-8"))
    assert result["line_match_count"] == 2
    assert result["matches_truncated"] is False
    assert [item["line_number"] for item in result["matches"]] == [2, 5]
    assert all("phase_reached" in item["snippet"] for item in result["matches"])
    assert all(item["snippet_truncated"] is False for item in result["matches"])

    zero_match = workspace.search_generated_file_text(
        {"path": path, "query": "hallucinated_assignment", "context_lines": 1}
    )
    assert zero_match["line_match_count"] == 0
    assert "Do not guess another source literal" in zero_match["zero_match_next_step"]

    for invalid in (
        {"path": "package_manifest.json", "query": "status", "context_lines": 1},
        {"path": path, "query": "", "context_lines": 1},
        {"path": path, "query": "status", "context_lines": 7},
    ):
        with pytest.raises(GenerationWorkspaceError):
            workspace.search_generated_file_text(invalid)


def test_repair_symbol_read_returns_complete_real_function_and_current_hash(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    spacer = "".join(f"    waypoint_{index} = {index}\n" for index in range(120))
    content = (
        "def before() -> int:\n"
        "    return 0\n"
        "\n"
        "def lift_object(height_delta_m: float) -> float:\n"
        "    delta = float(height_delta_m)\n"
        f"{spacer}"
        "    total_lift_height = _LIFT_HEIGHT_M + delta\n"
        "    return total_lift_height\n"
        "\n"
        "def after() -> int:\n"
        "    return 1\n"
    )
    written = workspace.write_generated_file({"path": path, "content": content})

    result = workspace.read_generated_python_symbol(
        {"path": path, "symbol": "lift_object"}
    )

    assert result["found"] is True
    assert result["path"] == path
    assert result["sha256"] == written["sha256"]
    assert result["symbol_match_count"] == 1
    assert result["source"].startswith("def lift_object(")
    assert "total_lift_height = _LIFT_HEIGHT_M + delta" in result["source"]
    assert "def after" not in result["source"]
    assert result["source_bytes"] == len(result["source"].encode("utf-8"))
    assert result["start_line"] == 4
    assert result["end_line"] > result["start_line"] + 100


def test_repair_symbol_read_missing_name_returns_bounded_real_candidates(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    content = "def lift_object() -> None:\n    pass\n\ndef place_object() -> None:\n    pass\n"
    written = workspace.write_generated_file({"path": path, "content": content})

    result = workspace.read_generated_python_symbol(
        {"path": path, "symbol": "HALLUCINATED_LIFT"}
    )

    assert result == {
        "path": path,
        "sha256": written["sha256"],
        "bytes": len(content.encode("utf-8")),
        "symbol": "HALLUCINATED_LIFT",
        "symbol_match_count": 0,
        "found": False,
        "available_top_level_functions": ["lift_object", "place_object"],
        "available_functions_truncated": False,
        "next_step": (
            "Choose an exact available top-level function or a real static-"
            "failure line number; do not guess source literals."
        ),
    }

    for invalid in (
        {"path": "package_manifest.json", "symbol": "lift_object"},
        {"path": path, "symbol": "not a symbol"},
        {"path": path, "symbol": "x" * 129},
        {"path": path},
        {"path": path, "line_number": 0},
        {"path": path, "line_number": True},
        {"path": path, "symbol": "lift_object", "line_number": 1},
    ):
        with pytest.raises(GenerationWorkspaceError):
            workspace.read_generated_python_symbol(invalid)


def test_repair_symbol_read_rejects_function_above_context_byte_bound(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    content = "def huge() -> int:\n" + "".join(
        f"    value_{index} = {index}\n" for index in range(2_500)
    ) + "    return value_2499\n"
    assert len(content.encode("utf-8")) > 32_768
    _commit_ascii_python_via_staging(workspace, path=path, content=content)

    with pytest.raises(GenerationWorkspaceError, match="32768-byte bounded"):
        workspace.read_generated_python_symbol({"path": path, "symbol": "huge"})


def test_repair_incremental_editor_rejects_syntax_break_atomically(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "def move() -> str:\n    return 'before'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})
    target = workspace.package_root / path

    with pytest.raises(GenerationWorkspaceError, match="AST parsing"):
        workspace.replace_generated_file_text(
            {
                "path": path,
                "old_text": "def move() -> str:",
                "new_text": "def move(:",
                "expected_sha256": initial["sha256"],
            }
        )

    assert target.read_text(encoding="utf-8") == original
    assert workspace.list_generated_files()["files"][0]["sha256"] == initial["sha256"]


def test_repair_incremental_editor_does_not_disturb_open_staged_transaction(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "value = 'before'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})
    begin = workspace.begin_generated_file_write(
        {
            "path": path,
            "expected_target": {
                "state": "present",
                "sha256": initial["sha256"],
            },
        }
    )

    with pytest.raises(GenerationWorkspaceError, match="transaction already owns"):
        workspace.replace_generated_file_text(
            {
                "path": path,
                "old_text": "before",
                "new_text": "repair",
                "expected_sha256": initial["sha256"],
            }
        )

    assert (workspace.package_root / path).read_text(encoding="utf-8") == original
    draft = workspace.generated_write_staging_root / (
        begin["transaction_id"] + ".draft"
    )
    assert draft.read_text(encoding="utf-8") == ""
    appended = workspace.append_generated_file_chunk(
        {
            "transaction_id": begin["transaction_id"],
            "chunk_index": 0,
            "expected_draft_sha256": begin["draft_sha256"],
            "content": "value = 'staged'\n",
        }
    )
    committed = workspace.commit_generated_file_write(
        {
            "transaction_id": begin["transaction_id"],
            "expected_draft_sha256": appended["draft_sha256"],
            "expected_chunk_count": 1,
            "expected_bytes": appended["bytes"],
        }
    )
    assert committed["committed"] is True
    assert (workspace.package_root / path).read_text(encoding="utf-8") == (
        "value = 'staged'\n"
    )


def test_repair_incremental_editor_validation_failures_leave_file_unchanged(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "marker = 'same'\nmarker = 'same'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})
    target = workspace.package_root / path
    invalid_calls = [
        {
            "path": path,
            "old_text": "",
            "new_text": "replacement",
            "expected_sha256": initial["sha256"],
        },
        {
            "path": path,
            "old_text": "absent",
            "new_text": "replacement",
            "expected_sha256": initial["sha256"],
        },
        {
            "path": path,
            "old_text": "marker = 'same'",
            "new_text": "replacement",
            "expected_sha256": initial["sha256"],
        },
        {
            "path": path,
            "old_text": original,
            "new_text": "",
            "expected_sha256": initial["sha256"],
        },
        {
            "path": path,
            "old_text": "marker = 'same'\nmarker = 'same'",
            "new_text": "x" * 500_001,
            "expected_sha256": initial["sha256"],
        },
        {
            "path": path,
            "old_text": "marker",
            "new_text": "replacement",
            "expected_sha256": "0" * 64,
        },
    ]

    for arguments in invalid_calls:
        with pytest.raises(GenerationWorkspaceError):
            workspace.replace_generated_file_text(arguments)
        assert target.read_text(encoding="utf-8") == original
        assert workspace.list_generated_files()["files"][0]["sha256"] == initial["sha256"]


def test_incremental_editor_excludes_manifest_and_is_shared_with_stage2(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    manifest = workspace.write_generated_file(
        {"path": "package_manifest.json", "content": "{}\n"}
    )

    with pytest.raises(GenerationWorkspaceError, match="generated-Python allowlist"):
        workspace.replace_generated_file_text(
            {
                "path": "package_manifest.json",
                "old_text": "{}",
                "new_text": '{"changed": true}',
                "expected_sha256": manifest["sha256"],
            }
        )

    assert (workspace.package_root / "package_manifest.json").read_text(
        encoding="utf-8"
    ) == "{}\n"
    stage2_toolset = stage2_tools(workspace)
    repair_toolset = repair_tools(workspace)
    for tool_name in (
        "replace_generated_file_text",
        "search_generated_file_text",
        "read_generated_python_symbol",
    ):
        assert tool_name in stage2_toolset
        assert tool_name in repair_toolset
        assert (
            stage2_toolset[tool_name].input_schema
            == repair_toolset[tool_name].input_schema
        )
    search_tool = repair_toolset["search_generated_file_text"]
    assert "context_lines must be an integer from 0 through 6" in search_tool.description
    assert "nonexistent helper" in search_tool.description
    assert search_tool.input_schema["additionalProperties"] is False
    assert set(search_tool.input_schema["required"]) == {
        "path",
        "query",
        "context_lines",
    }
    assert search_tool.input_schema["properties"]["context_lines"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 6,
    }
    symbol_tool = repair_toolset["read_generated_python_symbol"]
    assert "exactly one locator: symbol or line_number" in symbol_tool.description
    assert "do not guess nonexistent helpers" in symbol_tool.description
    assert "_kinematics.py symbol _forward_kinematics" in symbol_tool.description
    assert symbol_tool.input_schema["additionalProperties"] is False
    assert set(symbol_tool.input_schema["required"]) == {"path"}
    assert symbol_tool.input_schema["properties"]["symbol"] == {
        "type": "string",
        "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$",
    }
    assert symbol_tool.input_schema["properties"]["line_number"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert len(symbol_tool.input_schema["oneOf"]) == 2
    edit_tool = repair_toolset["replace_generated_file_text"]
    assert edit_tool.input_schema["additionalProperties"] is False
    assert set(edit_tool.input_schema["required"]) == {
        "path",
        "old_text",
        "new_text",
        "expected_sha256",
    }
    assert edit_tool.input_schema["properties"]["path"]["enum"] == [
        "generated_capability_package/__init__.py",
        "generated_capability_package/_kinematics.py",
        "generated_capability_package/g1.py",
        "generated_capability_package/g2.py",
        "generated_capability_package/g3.py",
    ]


def test_repair_incremental_editor_write_failure_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    path = "generated_capability_package/g3.py"
    original = "value = 'before'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})

    def fail_replace(_: object, __: object) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("soarm_demo.generation.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        workspace.replace_generated_file_text(
            {
                "path": path,
                "old_text": "before",
                "new_text": "after",
                "expected_sha256": initial["sha256"],
            }
        )

    assert (workspace.package_root / path).read_text(encoding="utf-8") == original
    assert list((workspace.package_root / "generated_capability_package").glob(".g3.py.*")) == []


def test_continuous_repair_injects_and_executes_incremental_editor(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    path = "generated_capability_package/g3.py"
    original = "value = 'before'\n"
    initial = workspace.write_generated_file({"path": path, "content": original})
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "replace_generated_file_text",
                    "arguments": {
                        "path": path,
                        "old_text": "before",
                        "new_text": "after",
                        "expected_sha256": initial["sha256"],
                    },
                }
            ),
            json.dumps({"type": "final", "content": "repair complete"}),
        ]
    )
    stage2_toolset = stage2_tools(workspace)
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="test",
        tools=stage2_toolset,
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2", tools=stage2_toolset)
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        require_repair_artifact_change=True,
        stage2_result=agent.accounting_result(status="completed"),
    )

    generation.repair({"repair_round": 1, "failures": ["wrong literal"]})

    assert (workspace.package_root / path).read_text(encoding="utf-8") == (
        "value = 'after'\n"
    )
    assert agent.phase == "repair"
    assert "replace_generated_file_text" in agent.tools
    assert generation.repair_results[0].agent_turn_budget["used"] == 2


def test_stage2_manifest_is_a_schema_driven_tool_not_an_escaped_file_string(
    tmp_path: Path,
) -> None:
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()
    tools = stage2_tools(workspace)

    assert "package_manifest.json" not in tools["write_generated_file"].input_schema[
        "properties"
    ]["path"]["enum"]
    manifest_schema = tools["write_package_manifest"].input_schema["properties"][
        "manifest"
    ]
    assert manifest_schema["properties"]["stage1_sha256"] == {
        "const": frozen.sha256
    }

    invalid = workspace.write_package_manifest(
        {
            "manifest": {
                "schema_version": "robot_capability.package_manifest.v2",
                "stage1_artifact_sha256": frozen.sha256,
                "capabilities": {},
            }
        }
    )
    assert invalid["accepted"] is False
    assert any("stage1_sha256" in issue["message"] for issue in invalid["issues"])

    valid = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    valid["stage1_sha256"] = frozen.sha256
    accepted = workspace.write_package_manifest({"manifest": valid})
    assert accepted["accepted"] is True
    assert accepted["path"] == "package_manifest.json"


def test_finish_package_runs_authoritative_static_contract(
    tmp_path: Path,
) -> None:
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    fixture_root = ROOT / "fixtures/generated_pass"
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    workspace.write_package_manifest(
        {
            "manifest": json.loads(
                (fixture_root / "package_manifest.json").read_text(encoding="utf-8")
            )
        }
    )
    for relative in (
        "generated_capability_package/__init__.py",
        "generated_capability_package/_kinematics.py",
        "generated_capability_package/g1.py",
        "generated_capability_package/g2.py",
        "generated_capability_package/g3.py",
    ):
        workspace.write_generated_file(
            {
                "path": relative,
                "content": (fixture_root / relative).read_text(encoding="utf-8"),
            }
        )

    passed = workspace.finish_package()

    assert passed["ok"] is True
    assert passed["complete"] is True
    assert passed["static_validation"]["passed"] is True

    workspace.write_generated_file(
        {
            "path": "generated_capability_package/g1.py",
            "content": "import os\n",
        }
    )
    failed = workspace.finish_package()

    assert failed["ok"] is False
    assert failed["complete"] is False
    assert failed["static_validation"]["passed"] is False
    assert {failure["code"] for failure in failed["errors"]} >= {
        "FORBIDDEN_IMPORT",
        "MISSING_CAPABILITY_FUNCTION",
    }


def test_stage2_manifest_rejects_scalar_vector_binding_then_accepts_component_map(
    tmp_path: Path,
) -> None:
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()

    manifest = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["stage1_sha256"] = frozen.sha256
    g2_api = next(item for item in manifest["capabilities"] if item["module"] == "g2")
    existing_parameters = g2_api["signature"]["parameters"]
    g2_api["signature"]["parameters"] = [
        existing_parameters[0],
        {"name": "target_x_m", "annotation": "float", "has_default": False},
        {"name": "target_y_m", "annotation": "float", "has_default": False},
        {"name": "target_z_m", "annotation": "float", "has_default": False},
        *existing_parameters[2:],
    ]
    # This is the run-f failure mode: one scalar is falsely declared to be an
    # entire Cartesian vector, leaving y/z semantically unbound.
    g2_api["validation_binding"] = {
        "effect": "cartesian_target",
        "target_argument": "target_x_m",
    }

    rejected = workspace.write_package_manifest({"manifest": manifest})

    assert rejected["accepted"] is False
    assert "VECTOR_BINDING_SHAPE_MISMATCH" in "\n".join(
        issue["message"] for issue in rejected["issues"]
    )

    g2_api["validation_binding"] = {
        "effect": "cartesian_target",
        "target_arguments": {
            "x": "target_x_m",
            "y": "target_y_m",
            "z": "target_z_m",
        },
    }

    accepted = workspace.write_package_manifest({"manifest": manifest})

    assert accepted["accepted"] is True
    assert accepted["path"] == "package_manifest.json"


def test_stage2_manifest_rejects_dynamic_move_field_selector_parameters(
    tmp_path: Path,
) -> None:
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()
    manifest = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["stage1_sha256"] = frozen.sha256
    sequence_api = next(
        item
        for item in manifest["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )
    sequence_api["signature"]["parameters"][2:2] = [
        {"name": "source_field", "annotation": "str", "has_default": False},
        {"name": "target_field", "annotation": "str", "has_default": False},
    ]
    sequence_api["validation_binding"]["source_field"] = "source_field"
    sequence_api["validation_binding"]["target_field"] = "target_field"

    rejected = workspace.write_package_manifest({"manifest": manifest})

    assert rejected["accepted"] is False
    assert "MOVE_FIELD_SELECTOR_UNSUPPORTED" in "\n".join(
        issue["message"] for issue in rejected["issues"]
    )


@pytest.mark.parametrize(
    ("moves_annotation", "expected_code"),
    [
        ("list", "ARRAY_ITEM_SCHEMA_MISSING"),
        ("list[float]", "MOVE_ITEM_SCHEMA_INVALID"),
    ],
)
def test_stage2_manifest_rejects_sequence_without_structured_move_items(
    tmp_path: Path,
    moves_annotation: str,
    expected_code: str,
) -> None:
    stage1_artifact = json.loads(
        (ROOT / "fixtures/stage1_capabilities.json").read_text(encoding="utf-8")
    )
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()
    manifest = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["stage1_sha256"] = frozen.sha256
    sequence_api = next(
        item
        for item in manifest["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )
    moves_argument = sequence_api["validation_binding"]["moves_argument"]
    moves_parameter = next(
        item
        for item in sequence_api["signature"]["parameters"]
        if item["name"] == moves_argument
    )
    moves_parameter["annotation"] = moves_annotation

    rejected = workspace.write_package_manifest({"manifest": manifest})

    assert rejected["accepted"] is False
    assert expected_code in "\n".join(
        issue["message"] for issue in rejected["issues"]
    )


def test_manifest_semantic_check_treats_unhashable_move_fields_as_data() -> None:
    manifest = json.loads(
        (ROOT / "fixtures/generated_pass/package_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    sequence_api = next(
        item
        for item in manifest["capabilities"]
        if item["validation_binding"]["effect"] == "object_move_sequence"
    )
    sequence_api["validation_binding"]["object_extent_field"] = [
        "not",
        "hashable",
    ]

    issues = _manifest_binding_semantic_issues(manifest)

    assert "MOVE_ITEM_FIELDS_NOT_DISTINCT" in "\n".join(
        issue["message"] for issue in issues
    )

    sequence_api["validation_binding"]["object_extent_field"] = sequence_api[
        "validation_binding"
    ]["target_field"]
    issues = _manifest_binding_semantic_issues(manifest)
    assert "MOVE_ITEM_FIELDS_NOT_DISTINCT" in "\n".join(
        issue["message"] for issue in issues
    )


def test_generation_snapshot_rejects_symlinks_instead_of_skipping_them(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    secret = tmp_path / "private_oracle.txt"
    secret.write_text("PRIVATE_ORACLE_SECRET", encoding="utf-8")
    link = workspace.input_snapshot / "oracle-link.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("filesystem does not permit symlinks")

    with pytest.raises(GenerationWorkspaceError, match="symlink"):
        workspace.read_generation_snapshot()

    manifest = workspace.input_snapshot_manifest_path.read_text(encoding="utf-8")
    assert "oracle-link.txt" not in manifest
    assert "PRIVATE_ORACLE_SECRET" not in manifest


def test_repair_context_checkpoint_is_deterministic_and_contains_no_input_text(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    frozen = workspace.freeze_stage1()

    first = workspace.repair_context_checkpoint(
        repair_round=1,
        package_tree_digest="a" * 64,
    )
    second = workspace.repair_context_checkpoint(
        repair_round=1,
        package_tree_digest="a" * 64,
    )

    assert first == second
    assert first["frozen_stage1"]["sha256"] == frozen.sha256
    assert first["generated_package"]["tree_sha256"] == "a" * 64
    assert first["generation_input_index"] == [
        {
            "path": "tasks.json",
            "bytes": 19,
            "sha256": first["generation_input_index"][0]["sha256"],
        }
    ]
    assert '"task":"visible"' not in json.dumps(first, ensure_ascii=False)


def test_snapshot_tamper_between_repair_rounds_fails_before_next_model_call(
    tmp_path: Path,
    stage1_artifact: dict[str, Any],
) -> None:
    workspace = _workspace(tmp_path)
    _review_and_submit_stage1(workspace, stage1_artifact)
    workspace.freeze_stage1()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "round one complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="test",
        tools=stage2_tools(workspace),
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        stage2_result=agent.accounting_result(status="completed"),
    )

    generation.repair({"repair_round": 1, "failures": []})
    requests_after_round_one = len(client.requests)
    (workspace.input_snapshot / "tasks.json").write_text(
        '{"task":"changed-between-repairs"}\n',
        encoding="utf-8",
    )

    with pytest.raises(GenerationWorkspaceError, match="integrity violation"):
        generation.repair({"repair_round": 2, "failures": []})

    assert len(client.requests) == requests_after_round_one == 1
    assert generation.repair_rounds == 1


def test_repair_rounds_have_fresh_budgets_without_charging_stage2(
    tmp_path: Path,
) -> None:
    client = ScriptedModelClient(
        [
            *("not a JSON action" for _ in range(6)),
            json.dumps({"type": "final", "content": "second repair complete"}),
        ],
        model="scripted-repair-budget",
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="test",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    initial_stage2_result = agent.accounting_result(status="completed")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(tmp_path),
        stage2_result=initial_stage2_result,
    )
    session_id = agent.session_id
    history_before_repairs = len(agent.history)

    generation.repair(
        {"repair_round": 1, "failures": []},
        max_model_calls_per_round=6,
    )

    assert generation.stage2_result is initial_stage2_result
    assert initial_stage2_result.agent_turn_budget == {
        "name": "generation_stage2_initial",
        "limit": 30,
        "used": 0,
        "remaining": 30,
    }
    assert generation.repair_results[0].status == "budget_exhausted"
    assert generation.repair_results[0].agent_turn_budget["used"] == 6
    assert generation.repair_results[0].usage["response_count"] == 6

    generation.repair(
        {"repair_round": 2, "failures": []},
        max_model_calls_per_round=6,
    )

    assert generation.repair_rounds == 2
    assert generation.repair_results[1].status == "completed"
    assert generation.repair_results[1].agent_turn_budget["limit"] == 6
    assert generation.repair_results[1].agent_turn_budget["used"] == 1
    assert all(result.session_id == session_id for result in generation.repair_results)
    assert len(agent.history) > history_before_repairs


def test_repair_instruction_is_injected_into_same_agent_session(tmp_path: Path) -> None:
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})],
        model="scripted-repair-prompt",
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="stage one system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(tmp_path),
        repair_instruction="FRESH_REPAIR_PROMPT_SENTINEL",
        stage2_result=agent.accounting_result(status="completed"),
    )

    generation.repair({"repair_round": 1, "failures": []})

    assert agent.phase == "repair"
    request_text = "\n".join(message.content for message in client.requests[0])
    assert "FRESH_REPAIR_PROMPT_SENTINEL" in request_text
    assert '"validation_feedback"' in request_text


def test_third_repair_epoch_sees_bounded_prior_round_ledger_and_current_feedback(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    (package_root / "g1.py").write_text("VALUE = 1\n", encoding="utf-8")
    trace_path = tmp_path / "generation_trace.jsonl"
    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": "ROUND_ONE_RESPONSE"}),
            json.dumps({"type": "final", "content": "ROUND_TWO_RESPONSE"}),
            json.dumps({"type": "final", "content": "ROUND_THREE_RESPONSE"}),
        ],
        model="scripted-repair-ledger",
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="stage one system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=trace_path,
        session_id="continuous-ledger-session",
    )
    agent.set_phase("stage2")
    episode_id = agent.episode_id
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    feedback_one = {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": "static",
        "repair_round": 1,
        "failures": [
            {
                "code": "STATIC_SIGNATURE_MISMATCH",
                "capability_id": "G1",
                "case_id": None,
                "message": "ROUND_ONE_PRIVATE_MESSAGE",
                "traceback": "ROUND_ONE_PRIVATE_TRACEBACK\nfull private stack",
            }
        ],
    }
    feedback_two = {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": "direct_function",
        "repair_round": 2,
        "failures": [
            {
                "code": "DIRECT_EXCEPTION",
                "capability_id": "G2",
                "case_id": "visible-case-2",
                "message": (
                    "name 'helper_pose' is not defined; "
                    "ROUND_TWO_PRIVATE_MESSAGE"
                ),
                "traceback": "ROUND_TWO_PRIVATE_TRACEBACK\nfull private stack",
            }
        ],
    }
    feedback_three = {
        "schema_version": "robot_capability.failure_feedback.v1",
        "stage": "static",
        "repair_round": 3,
        "failures": [
            {
                "code": "STATIC_SIGNATURE_MISMATCH",
                "capability_id": "G1",
                "case_id": None,
                "message": "CURRENT_ROUND_MESSAGE",
                "traceback": "CURRENT_ROUND_TRACEBACK",
            }
        ],
    }

    generation.repair(feedback_one)
    generation.repair(feedback_two)
    generation.repair(feedback_three)

    third_request = client.requests[2]
    third_request_text = "\n".join(message.content for message in third_request)
    epoch_message = next(
        json.loads(message.content)
        for message in third_request
        if "repair_context_epoch" in message.content
    )
    epoch = epoch_message["repair_context_epoch"]
    ledger = epoch["checkpoint"]["repair_ledger"]

    assert ledger["schema_version"] == "robot_capability.repair_ledger.v2"
    assert ledger["through_repair_round"] == 2
    assert ledger["round_count"] == 2
    assert ledger["utf8_bytes"] <= ledger["max_utf8_bytes"]
    assert [item["round"] for item in ledger["rounds"]] == [1, 2]
    assert validate_json_schema(
        ledger,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []

    round_one, round_two = ledger["rounds"]
    round_one_ref = round_one["feedback"]["failure_refs"][0]
    round_one_signature = ledger["failure_registry"][round_one_ref]
    assert round_one["feedback"] == {
        "stage": "static",
        "feedback_repair_round": 1,
        "result": "failed",
        "failure_count": 1,
        "failure_refs": [round_one_ref],
    }
    assert round_one_signature == {
        "code": "STATIC_SIGNATURE_MISMATCH",
        "capability_ref": round_one_signature["capability_ref"],
        "case_ref": None,
        "signature_sha256": round_one_signature["signature_sha256"],
    }
    assert round_one_signature["capability_ref"].startswith("cap_")
    assert len(round_one_signature["capability_ref"]) == 28
    assert round_one["package"]["before_sha256"]
    assert round_one["package"]["inspection_status"] == "complete"
    assert round_one["package"]["after_sha256"]
    assert round_one["package"]["changed"] is False
    assert round_one["agent"] == {
        "status": "completed",
        "model_calls": 1,
        "agent_turns": 1,
        "provider_http_attempts": 0,
        "provider_retries": 0,
    }
    assert round_one["subsequent_validation"]["stage"] == "direct_function"
    assert round_one["subsequent_validation"]["against_round_feedback"] == {
        "same_stage": False,
        "resolution_status": "not_revalidated_different_stage",
        "unresolved": None,
        "matching_failure_count": 0,
    }
    assert round_two["feedback"]["stage"] == "direct_function"
    direct_signature = ledger["failure_registry"][
        round_two["feedback"]["failure_refs"][0]
    ]
    assert direct_signature["diagnostic"] == {
        "kind": "python_unresolved_symbol",
        "unresolved_symbol": "helper_pose",
    }
    assert round_two["subsequent_validation"]["stage"] == "static"

    unresolved = {
        (item["origin_round"], ledger["failure_registry"][failure_ref]["code"]): (
            item["resolution_status"]
        )
        for item in ledger["unresolved_prior_failures"]
        for failure_ref in item["failure_refs"]
    }
    assert unresolved == {
        (1, "STATIC_SIGNATURE_MISMATCH"): "still_unresolved",
        (2, "DIRECT_EXCEPTION"): "not_revalidated",
    }

    # Prior raw evidence remains in append-only local history, but context
    # projection carries only its safe signature into the third provider call.
    assert any(
        "ROUND_ONE_PRIVATE_TRACEBACK" in message.content
        for message in agent.history
    )
    assert "ROUND_ONE_PRIVATE_MESSAGE" not in third_request_text
    assert "ROUND_ONE_PRIVATE_TRACEBACK" not in third_request_text
    assert "ROUND_TWO_PRIVATE_MESSAGE" not in third_request_text
    assert "ROUND_TWO_PRIVATE_TRACEBACK" not in third_request_text
    assert "CURRENT_ROUND_MESSAGE" in third_request_text
    assert "CURRENT_ROUND_TRACEBACK" in third_request_text
    assert epoch["continuity"]["same_agent_session"] is True
    assert epoch["continuity"]["session_id"] == "continuous-ledger-session"
    assert epoch["continuity"]["episode_id"] == episode_id
    assert all(
        result.session_id == "continuous-ledger-session"
        and result.episode_id == episode_id
        for result in generation.repair_results
    )
    trace_text = trace_path.read_text(encoding="utf-8")
    assert "ROUND_ONE_RESPONSE" in trace_text
    assert "ROUND_TWO_RESPONSE" in trace_text

    public_audit = generation.repair_ledger_audit()
    assert public_audit == generation.repair_ledger_audit()
    assert public_audit["round_count"] == 3
    assert validate_json_schema(
        public_audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []
    pristine_audit = copy.deepcopy(public_audit)
    public_audit["rounds"][0]["agent"]["status"] = "failed"
    assert generation.repair_ledger_audit() == pristine_audit


def test_next_repair_epoch_receives_privacy_safe_prior_tool_process(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    (package_root / "g1.py").write_text(
        "def move_joint():\n    return None\n",
        encoding="utf-8",
    )
    client = ScriptedModelClient(
        [
            json.dumps(
                {
                    "type": "tool",
                    "tool": "read_generated_python_symbol",
                    "arguments": {"path": "g1.py", "symbol": "move_joint"},
                }
            ),
            json.dumps({"type": "final", "content": "read but did not edit"}),
            json.dumps({"type": "final", "content": "second round"}),
        ]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("stage2", 1),
        trace_path=tmp_path / "generation.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    feedback = {
        "stage": "static",
        "repair_round": 1,
        "failures": [
            {
                "code": "STATIC_FAILURE",
                "capability_id": "G1.move_joint",
                "case_id": None,
                "message": "PRIVATE_FAILURE_TEXT",
            }
        ],
    }

    generation.repair(feedback, max_model_calls_per_round=2)
    generation.repair({**feedback, "repair_round": 2}, max_model_calls_per_round=1)

    second_epoch = next(
        json.loads(message.content)["repair_context_epoch"]
        for message in client.requests[2]
        if "repair_context_epoch" in message.content
    )
    process = second_epoch["checkpoint"]["repair_ledger"]["rounds"][0]["process"]
    assert process["tool_call_count"] == 1
    assert process["successful_write_calls"] == 0
    assert process["last_action"] == "final"
    assert process["tool_calls"][0]["tool"] == "read_generated_python_symbol"
    assert process["tool_calls"][0]["target"] == {
        "path": "g1.py",
        "symbol": "move_joint",
    }
    assert process["unfinished_read"] == process["tool_calls"][0]
    assert process["privacy"] == {
        "raw_model_text_included": False,
        "raw_tool_arguments_included": False,
        "raw_tool_results_included": False,
        "raw_source_included": False,
    }
    assert "PRIVATE_FAILURE_TEXT" not in json.dumps(process)
    audit = generation.repair_ledger_audit()
    assert audit["rounds"][0]["process"] == process
    assert repair_ledger_semantic_issues(audit) == []


def test_repair_ledger_size_limit_fails_closed_before_next_model_call(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "round one"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        repair_ledger_max_bytes=1_024,
        stage2_result=agent.accounting_result(status="completed"),
    )
    feedback_one = {
        "stage": "static",
        "repair_round": 1,
        "failures": [
            {
                "code": "STATIC_SIGNATURE_MISMATCH",
                "capability_id": "G1",
                "case_id": None,
                "traceback": "must never be copied into the ledger",
            }
        ],
    }

    generation.repair(feedback_one)

    with pytest.raises(GenerationWorkspaceError, match="ledger exceeds"):
        generation.repair(
            {
                "stage": "direct_function",
                "repair_round": 2,
                "failures": [
                    {
                        "code": "DIRECT_ORACLE_MISMATCH",
                        "capability_id": "G2",
                        "case_id": "case-2",
                    }
                ],
            }
        )

    assert generation.repair_rounds == 1
    assert len(generation.repair_results) == 1
    assert len(client.requests) == 1


def test_provider_failure_round_is_complete_in_public_repair_ledger_audit(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    (package_root / "g1.py").write_text("VALUE = 1\n", encoding="utf-8")
    client = ScriptedModelClient([])
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )

    with pytest.raises(ModelAPIError, match="queue exhausted"):
        generation.repair(
            {
                "stage": "direct_function",
                "repair_round": 1,
                "failures": [
                    {
                        "code": "DIRECT_EXCEPTION",
                        "capability_id": "G2",
                        "case_id": "case-1",
                        "message": "name 'missing_helper' is not defined",
                        "traceback": "PRIVATE_PROVIDER_ROUND_TRACEBACK",
                    }
                ],
            }
        )

    audit = generation.repair_ledger_audit()
    record = audit["rounds"][0]
    assert record["package"]["before_sha256"]
    assert record["package"]["inspection_status"] == "complete"
    assert record["package"]["after_sha256"]
    assert record["package"]["changed"] is False
    assert record["secondary_errors"] == []
    assert record["agent"]["status"] == "failed"
    assert record["agent"]["model_calls"] == 1
    assert record["subsequent_validation"] is None
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_PROVIDER_ROUND_TRACEBACK" not in serialized
    assert "missing_helper" in serialized
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


def test_duplicate_failure_signatures_use_unique_refs_but_preserve_count(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    duplicate = {
        "code": "DIRECT_EXCEPTION",
        "capability_id": "G2.move_tool_center",
        "case_id": "duplicate-case",
        "message": "name 'duplicate_helper' is not defined",
    }

    generation.repair(
        {
            "stage": "direct_function",
            "repair_round": 1,
            "failures": [duplicate, copy.deepcopy(duplicate)],
        }
    )

    audit = generation.repair_ledger_audit()
    record = audit["rounds"][0]
    assert record["feedback"]["failure_count"] == 2
    assert len(record["feedback"]["failure_refs"]) == 1
    assert len(record["failure_resolution"][0]["failure_refs"]) == 1
    assert len(audit["unresolved_prior_failures"][0]["failure_refs"]) == 1
    assert len(audit["failure_registry"]) == 1
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


def test_repair_ledger_semantic_validator_rejects_cross_field_contradictions(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    generation.repair(
        {
            "stage": "static",
            "repair_round": 1,
            "failures": [
                {
                    "code": "STATIC_SIGNATURE_MISMATCH",
                    "capability_id": "G1.command_joint",
                }
            ],
        }
    )
    valid = generation.repair_ledger_audit()
    assert repair_ledger_semantic_issues(valid) == []

    def recount(value: dict[str, Any]) -> dict[str, Any]:
        value["utf8_bytes"] = 0
        for _ in range(4):
            size = len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if value["utf8_bytes"] == size:
                break
            value["utf8_bytes"] = size
        return value

    contradictions: list[tuple[str, dict[str, Any]]] = []
    wrong_count = copy.deepcopy(valid)
    wrong_count["round_count"] = 0
    contradictions.append(("round_count", recount(wrong_count)))
    wrong_through = copy.deepcopy(valid)
    wrong_through["through_repair_round"] = 0
    contradictions.append(("through_repair_round", recount(wrong_through)))
    wrong_sequence = copy.deepcopy(valid)
    wrong_sequence["rounds"][0]["round"] = 2
    contradictions.append(("round sequence", recount(wrong_sequence)))
    wrong_result = copy.deepcopy(valid)
    wrong_result["rounds"][0]["feedback"]["result"] = "passed"
    contradictions.append(("passed result", recount(wrong_result)))
    missing_ref = copy.deepcopy(valid)
    missing_ref["rounds"][0]["feedback"]["failure_refs"] = ["f640"]
    contradictions.append(("missing registry", recount(missing_ref)))
    wrong_package = copy.deepcopy(valid)
    wrong_package["rounds"][0]["package"]["changed"] = True
    contradictions.append(("package.changed", recount(wrong_package)))
    wrong_resolution = copy.deepcopy(valid)
    wrong_resolution["rounds"][0]["failure_resolution"][0]["unresolved"] = False
    contradictions.append(("latest same-stage", recount(wrong_resolution)))
    wrong_unresolved = copy.deepcopy(valid)
    wrong_unresolved["unresolved_prior_failures"] = []
    contradictions.append(("unresolved_prior_failures", recount(wrong_unresolved)))
    wrong_maximum = copy.deepcopy(valid)
    wrong_maximum["max_utf8_bytes"] = 1
    contradictions.append(("exceed max_utf8_bytes", recount(wrong_maximum)))

    for expected_issue, contradictory in contradictions:
        assert any(
            expected_issue in issue
            for issue in repair_ledger_semantic_issues(contradictory)
        )

    stale_size = copy.deepcopy(valid)
    stale_size["utf8_bytes"] += 1
    assert any(
        "utf8_bytes" in issue
        for issue in repair_ledger_semantic_issues(stale_size)
    )


def test_cleanup_failure_does_not_mask_primary_repair_exception(
    tmp_path: Path,
) -> None:
    class CleanupAuditFailure(RuntimeError):
        pass

    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    workspace = _repair_only_workspace(package_root)

    def fail_cleanup() -> None:
        raise CleanupAuditFailure("PRIVATE_CLEANUP_FAILURE_DETAIL")

    workspace.abort_all_generated_file_writes = fail_cleanup
    client = ScriptedModelClient([])
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=workspace,
        stage2_result=agent.accounting_result(status="completed"),
    )

    with pytest.raises(ModelAPIError, match="queue exhausted"):
        generation.repair(
            {
                "stage": "static",
                "repair_round": 1,
                "failures": [{"code": "STATIC_SIGNATURE_MISMATCH"}],
            }
        )

    audit = generation.repair_ledger_audit()
    record = audit["rounds"][0]
    assert record["agent"]["status"] == "failed"
    assert record["package"]["inspection_status"] == "complete"
    assert record["secondary_errors"] == [
        {
            "operation": "abort_generated_file_writes",
            "error_type": "CleanupAuditFailure",
        }
    ]
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_CLEANUP_FAILURE_DETAIL" not in serialized
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


def test_package_hash_failure_does_not_mask_primary_repair_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HashAuditFailure(RuntimeError):
        pass

    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    real_package_tree_sha256 = generation_module.package_tree_sha256
    hash_calls = 0

    def fail_final_hash(path: Path) -> str:
        nonlocal hash_calls
        hash_calls += 1
        if hash_calls == 2:
            raise HashAuditFailure("PRIVATE_HASH_FAILURE_DETAIL")
        return real_package_tree_sha256(path)

    monkeypatch.setattr(
        generation_module,
        "package_tree_sha256",
        fail_final_hash,
    )
    client = ScriptedModelClient([])
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )

    with pytest.raises(ModelAPIError, match="queue exhausted"):
        generation.repair(
            {
                "stage": "direct_function",
                "repair_round": 1,
                "failures": [{"code": "DIRECT_EXCEPTION"}],
            }
        )

    audit = generation.repair_ledger_audit()
    record = audit["rounds"][0]
    assert hash_calls == 2
    assert record["agent"]["status"] == "failed"
    assert record["package"] == {
        "before_sha256": record["package"]["before_sha256"],
        "inspection_status": "hash_failed",
        "after_sha256": None,
        "changed": None,
    }
    assert record["secondary_errors"] == [
        {
            "operation": "hash_generated_package_after_repair",
            "error_type": "HashAuditFailure",
        }
    ]
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_HASH_FAILURE_DETAIL" not in serialized
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


def test_validation_observations_close_last_direct_repair_without_cross_stage_guess(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    generation.repair(
        {
            "stage": "direct_function",
            "repair_round": 1,
            "failures": [
                {
                    "code": "DIRECT_EXCEPTION",
                    "capability_id": "G2",
                    "case_id": "case-1",
                    "message": (
                        "name 'missing_helper' is not defined; "
                        "PRIVATE_ORIGIN_MESSAGE"
                    ),
                    "traceback": "PRIVATE_ORIGIN_TRACEBACK",
                }
            ],
        }
    )

    static_pass = {
        "stage": "static",
        "repair_round": 1,
        "passed": True,
        "failures": [],
    }
    assert generation.observe_validation_feedback(
        static_pass,
        after_repair_round=1,
    ) is True
    assert generation.observe_validation_feedback(
        static_pass,
        after_repair_round=1,
    ) is False
    after_static = generation.repair_ledger_audit()
    assert after_static["rounds"][0]["failure_resolution"][0][
        "resolution_status"
    ] == "not_revalidated"

    direct_pass = {
        "stage": "direct_function",
        "repair_round": 1,
        "passed": True,
        "failures": [],
    }
    assert generation.observe_validation_feedback(
        direct_pass,
        after_repair_round=1,
    ) is True
    assert generation.observe_validation_feedback(
        direct_pass,
        after_repair_round=1,
    ) is False

    audit = generation.repair_ledger_audit()
    record = audit["rounds"][0]
    assert [
        observation["stage"]
        for observation in record["validation_observations"]
    ] == ["static", "direct_function"]
    assert record["subsequent_validation"]["stage"] == "static"
    assert record["subsequent_validation"]["against_round_feedback"][
        "unresolved"
    ] is None
    assert record["failure_resolution"][0]["resolution_status"] == (
        "resolved_by_later_same_stage_pass"
    )
    assert audit["unresolved_prior_failures"] == []
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_ORIGIN_MESSAGE" not in serialized
    assert "PRIVATE_ORIGIN_TRACEBACK" not in serialized
    assert "missing_helper" in serialized
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


@pytest.mark.parametrize(
    ("observation", "error_match"),
    [
        (
            {
                "stage": "direct_function",
                "repair_round": 1,
                "failures": [],
            },
            "explicit boolean",
        ),
        (
            {
                "stage": "direct_function",
                "repair_round": 1,
                "passed": "false",
                "failures": [{"code": "DIRECT_EXCEPTION"}],
            },
            "explicit boolean",
        ),
        (
            {
                "stage": "direct_function",
                "repair_round": 1,
                "passed": False,
                "failures": [],
            },
            "at least one failure",
        ),
        (
            {
                "stage": "direct_function",
                "repair_round": 2,
                "passed": True,
                "failures": [],
            },
            "repair_round must match",
        ),
    ],
)
def test_validation_observation_fails_closed_for_inconclusive_payloads(
    tmp_path: Path,
    observation: dict[str, Any],
    error_match: str,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [json.dumps({"type": "final", "content": "repair complete"})]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    generation.repair(
        {
            "stage": "direct_function",
            "repair_round": 1,
            "failures": [
                {
                    "code": "DIRECT_EXCEPTION",
                    "capability_id": "G2",
                    "case_id": "case-1",
                }
            ],
        }
    )
    before = generation.repair_ledger_audit()

    with pytest.raises(GenerationWorkspaceError, match=error_match):
        generation.observe_validation_feedback(
            observation,
            after_repair_round=1,
        )

    assert generation.repair_ledger_audit() == before


def test_ten_round_fourteen_max_length_ids_fit_ledger_and_provider_projection(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": f"round {repair_round}"})
            for repair_round in range(1, 11)
        ]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )

    def max_identifier(prefix: str, *, symbol_group: int, case: int) -> str:
        stem = f"{prefix}{symbol_group:02d}{case:02d}"
        return stem + "x" * (160 - len(stem))

    def direct_feedback(*, symbol_group: int, repair_round: int) -> dict[str, Any]:
        return {
            "stage": "direct_function",
            "repair_round": repair_round,
            "passed": False,
            "failures": [
                {
                    "code": "DIRECT_EXCEPTION",
                    "capability_id": max_identifier(
                        "cap", symbol_group=symbol_group, case=case
                    ),
                    "case_id": max_identifier(
                        "case", symbol_group=symbol_group, case=case
                    ),
                    "message": (
                        f"name 'round_{symbol_group:02d}_missing_{case:02d}' "
                        "is not defined; PRIVATE_VALIDATION_DETAIL"
                    ),
                    "traceback": "PRIVATE_VALIDATION_TRACEBACK",
                }
                for case in range(1, 15)
            ],
        }

    feedback = direct_feedback(symbol_group=1, repair_round=1)
    for repair_round in range(1, 11):
        generation.repair(feedback)
        next_group = min(repair_round + 1, 10)
        observation = direct_feedback(
            symbol_group=next_group,
            repair_round=repair_round,
        )
        assert generation.observe_validation_feedback(
            observation,
            after_repair_round=repair_round,
        ) is True
        feedback = direct_feedback(
            symbol_group=next_group,
            repair_round=min(repair_round + 1, 10),
        )

    audit = generation.repair_ledger_audit()
    schema = json.loads(
        (ROOT / "schemas/repair_ledger.schema.json").read_text(encoding="utf-8")
    )
    assert validate_json_schema(audit, schema) == []
    assert audit["round_count"] == 10
    assert len(audit["failure_registry"]) == 140
    assert audit["utf8_bytes"] <= 64 * 1024
    assert audit["utf8_bytes"] == len(
        json.dumps(
            audit,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    tenth_epoch = next(
        json.loads(message.content)["repair_context_epoch"]
        for message in client.requests[9]
        if "repair_context_epoch" in message.content
    )
    provider_ledger = tenth_epoch["checkpoint"]["repair_ledger"]
    assert provider_ledger["through_repair_round"] == 9
    assert provider_ledger["utf8_bytes"] <= 64 * 1024
    assert validate_json_schema(provider_ledger, schema) == []
    assert audit["rounds"][0]["validation_observations"][0][
        "failure_refs"
    ] == audit["rounds"][1]["feedback"]["failure_refs"]
    assert all(
        (signature["capability_ref"] or "").startswith("cap_")
        and len(signature["capability_ref"] or "") == 28
        and (signature["case_ref"] or "").startswith("case_")
        and len(signature["case_ref"] or "") == 29
        for signature in audit["failure_registry"].values()
    )
    assert audit["privacy_contract"]["identifier_pseudonymization"] == {
        "algorithm": "salted_sha256_truncated_96bit",
        "scope": "generation_agent_instance",
        "cross_round_linkable": True,
        "cross_run_linkable": False,
        "raw_identifiers_retained": False,
    }
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_VALIDATION_DETAIL" not in serialized
    assert "PRIVATE_VALIDATION_TRACEBACK" not in serialized
    assert max_identifier("cap", symbol_group=1, case=1) not in serialized
    assert max_identifier("case", symbol_group=10, case=14) not in serialized
    provider_serialized = json.dumps(provider_ledger, ensure_ascii=False)
    assert max_identifier("cap", symbol_group=1, case=1) not in provider_serialized
    assert max_identifier("case", symbol_group=9, case=14) not in provider_serialized


def test_tenth_repair_static_failure_is_observed_and_remains_unresolved(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": f"round {repair_round}"})
            for repair_round in range(1, 11)
        ]
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="system",
        tools={},
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        stage2_result=agent.accounting_result(status="completed"),
    )
    for repair_round in range(1, 11):
        generation.repair(
            {
                "stage": "static",
                "repair_round": repair_round,
                "failures": [
                    {
                        "code": "STATIC_SIGNATURE_MISMATCH",
                        "capability_id": "G1",
                        "case_id": None,
                        "message": f"private round {repair_round}",
                    }
                ],
            }
        )

    final_failure = {
        "stage": "static",
        "repair_round": 10,
        "passed": False,
        "failures": [
            {
                "code": "STATIC_SIGNATURE_MISMATCH",
                "capability_id": "G1",
                "case_id": None,
                "message": "PRIVATE_FINAL_STATIC_FAILURE",
                "traceback": "PRIVATE_FINAL_STATIC_TRACEBACK",
            }
        ],
    }
    assert generation.observe_validation_feedback(
        final_failure,
        after_repair_round=10,
    ) is True

    audit = generation.repair_ledger_audit()
    final_record = audit["rounds"][-1]
    assert final_record["subsequent_validation"]["stage"] == "static"
    assert final_record["subsequent_validation"]["result"] == "failed"
    assert final_record["subsequent_validation"]["against_round_feedback"][
        "unresolved"
    ] is True
    assert final_record["failure_resolution"][0]["resolution_status"] == (
        "still_unresolved"
    )
    assert any(
        item["origin_round"] == 10
        and item["resolution_status"] == "still_unresolved"
        for item in audit["unresolved_prior_failures"]
    )
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_FINAL_STATIC_FAILURE" not in serialized
    assert "PRIVATE_FINAL_STATIC_TRACEBACK" not in serialized
    assert validate_json_schema(
        audit,
        json.loads(
            (ROOT / "schemas/repair_ledger.schema.json").read_text(
                encoding="utf-8"
            )
        ),
    ) == []


def test_repair_rejects_no_change_final_within_same_independent_budget(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generated_package"
    package_root.mkdir()
    candidate = package_root / "g2.py"
    candidate.write_text("OLD\n", encoding="utf-8")

    def update(_: dict[str, object]) -> dict[str, object]:
        candidate.write_text("NEW\n", encoding="utf-8")
        return {"changed": True}

    client = ScriptedModelClient(
        [
            json.dumps({"type": "final", "content": "no change"}),
            json.dumps({"type": "tool", "tool": "update", "arguments": {}}),
            json.dumps({"type": "final", "content": "changed"}),
        ],
        model="scripted-repair-change-guard",
    )
    agent = GenerationReActAgent(
        agent_id="generation",
        role="generation",
        client=client,
        system_prompt="stage one system",
        tools={
            "update": ToolSpec(
                "update",
                "change candidate",
                {"type": "object", "additionalProperties": False},
                update,
            )
        },
        budget=BudgetCounter("generation_stage2_initial", 30),
        trace_path=tmp_path / "generation_trace.jsonl",
    )
    agent.set_phase("stage2")
    generation = ContinuousGeneration(
        agent=agent,
        workspace=_repair_only_workspace(package_root),
        repair_instruction="repair it",
        require_repair_artifact_change=True,
        stage2_result=agent.accounting_result(status="completed"),
    )

    generation.repair(
        {"repair_round": 1, "failures": [{"code": "still_failing"}]},
        max_model_calls_per_round=6,
    )

    assert candidate.read_text(encoding="utf-8") == "NEW\n"
    assert generation.repair_results[0].agent_turns == 3
    trace = (tmp_path / "generation_trace.jsonl").read_text(encoding="utf-8")
    assert '"event":"final_rejected"' in trace
