from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_adapter import capability_preparation as preparation


def _library(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    library.mkdir()
    (library / "catalog.json").write_text(
        json.dumps(
            {
                "robot_configuration_id": "fixture-source",
                "package_version": "1.0.0",
                "snapshot_id": "fixture-snapshot",
                "tasks": [
                    {"task_id": "task-a", "name": "Reach", "description": "reach"},
                    {"task_id": "task-b", "name": "Move", "description": "move"},
                    {"task_id": "task-c", "name": "Hold", "description": "hold"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (library / "sources.json").write_text(
        json.dumps(
            {
                "robot_configuration_id": "fixture-source",
                "package_version": "1.0.0",
                "sources": [
                    {
                        "source_id": "fixture-source-record",
                        "specific_reference": "fixture standard",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return library


def _schema() -> dict:
    evidence = [
        {
            "source_id": "fixture-source-record",
            "specific_reference": "fixture MJCF bound",
        }
    ]
    return {
        "type": "object",
        "properties": {
            "target_m": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "unit": "m",
                "frame": "world",
                "items": {
                    "type": "number",
                    "unit": "m",
                    "frame": "world",
                    "minimum": -1.0,
                    "maximum": 1.0,
                    "evidence_refs": evidence,
                },
            },
            "duration_s": {
                "type": "number",
                "unit": "s",
                "frame": "none",
                "minimum": 0.25,
                "maximum": 8.0,
                "evidence_refs": evidence,
            },
        },
        "required": ["target_m", "duration_s"],
        "additionalProperties": False,
    }


def _design() -> dict:
    evidence = [
        {
            "source_id": "fixture-source-record",
            "specific_reference": "fixture threshold",
        }
    ]
    capabilities = []
    for index in range(3):
        capabilities.append(
            {
                "capability_id": f"C{index + 1}",
                "method_name": f"move_effect_{index + 1}",
                "description": "Perform one bounded physical effect.",
                "effect": "A bounded fixture-independent motion.",
                "request_schema": _schema(),
                "preconditions": ["The public robot state is finite."],
                "temporal_semantics": {"kind": "bounded_terminal_effect"},
                "invariants": ["Commands remain within public limits."],
                "failure_behavior": "Stop and report a bounded capability error.",
                "criteria": [
                    {
                        "metric": "terminal_error",
                        "unit": "m",
                        "comparator": "<=",
                        "threshold": 0.05,
                        "temporal": {"kind": "terminal_state"},
                        "aggregation": {"kind": "single_trial"},
                        "source_refs": evidence,
                    }
                ],
            }
        )
    return {
        "artifact_type": "capability_design",
        "schema_version": "2.0",
        "capability_protocol_version": "capability-v2",
        "robot_configuration_id": "fixture-aa1",
        "package_version": "1.0.0",
        "task_snapshot_id": "fixture-snapshot",
        "invocation_abi": dict(preparation.CAPABILITY_INVOCATION_ABI),
        "task_ids": ["task-a", "task-b", "task-c"],
        "capabilities": capabilities,
        "task_support": [
            {"task_id": "task-a", "capability_id": "C1", "rationale": "Reach support."},
            {"task_id": "task-b", "capability_id": "C2", "rationale": "Move support."},
            {"task_id": "task-c", "capability_id": "C3", "rationale": "Hold support."},
        ],
    }


class _FakeLoop:
    mode = "success"
    last_prompt = ""
    last_kwargs = {}

    def __init__(self, *, tools, **kwargs):
        self.tool = tools[0]
        type(self).last_kwargs = kwargs

    def run(self, prompt):
        type(self).last_prompt = prompt
        if self.mode == "invoke_error":
            accepted = self.tool.handler({"design": _design()})
            return SimpleNamespace(
                ok=False,
                error="gateway failed",
                total_tokens={"in": 2, "out": 3},
                trace=[SimpleNamespace(stop_reason="invoke_error")],
            )
        self.tool.handler({"design": _design()})
        return SimpleNamespace(
            ok=True,
            error=None,
            total_tokens={"in": 4, "out": 5},
            trace=[SimpleNamespace(stop_reason="end_turn")],
        )


def test_task_library_lookup_uses_aa1_to_source_index() -> None:
    path = preparation.task_library_for_robot("piper")
    assert path.name == "1.0.0"
    assert path.parent.name == "piper"


def test_valid_generated_style_design_loads(tmp_path: Path) -> None:
    design = _design()
    path = tmp_path / "capability_design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    loaded = preparation.load_capability_design_file(
        path,
        expected_robot_id="fixture-aa1",
    )
    assert loaded["capabilities"][0]["method_name"] == "move_effect_1"


@pytest.mark.parametrize(
    "mutation,pattern",
    [
        (lambda d: d.update(robot_configuration_id="other"), "robot_configuration_id"),
        (
            lambda d: d["capabilities"].__setitem__(1, dict(d["capabilities"][0])),
            "duplicate",
        ),
        (lambda d: d["capabilities"][0].update(criteria=[]), "criteria"),
        (
            lambda d: d["task_support"][0].update(task_id="unknown"),
            "unknown task",
        ),
        (
            lambda d: d["capabilities"][0]["criteria"][0].update(threshold="0.05"),
            "threshold",
        ),
    ],
)
def test_loader_rejects_identity_duplicates_missing_criteria_and_bad_links(
    tmp_path: Path,
    mutation,
    pattern: str,
) -> None:
    design = _design()
    mutation(design)
    path = tmp_path / "capability_design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    with pytest.raises(ValueError, match=pattern):
        preparation.load_capability_design_file(
            path,
            expected_robot_id="fixture-aa1",
        )


def test_invoke_error_rejects_submission_and_stale_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text(
        "<mujoco><worldbody><body name='tool'><site name='ee'/></body>"
        "</worldbody><actuator><motor name='m' joint='j' ctrlrange='-1 1'/>"
        "</actuator></mujoco>",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()
    stale = _design()
    stale["capabilities"][0]["method_name"] = "stale_method"
    (output / "capability_design.json").write_text(
        json.dumps(stale),
        encoding="utf-8",
    )
    _FakeLoop.mode = "invoke_error"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeLoop)
    with pytest.raises(preparation.CapabilityPreparationError, match="invoke_error"):
        preparation.generate_capability_design(
            robot_id="fixture-aa1",
            study={"robot_id": "fixture-aa1", "dof": 1},
            mjcf_path=mjcf,
            task_library_dir=library,
            output_dir=output,
            model="fixture-model",
            provider="holistic",
            region="fixture-region",
        )
    metadata = json.loads((output / "capability_preparation.json").read_text())
    assert "invoke_error" in metadata["error"]
    assert metadata["token_usage"] == {"in": 2, "out": 3}
    # The stale file is not used as the return value or success signal.
    assert json.loads((output / "capability_design.json").read_text())["capabilities"][0][
        "method_name"
    ] == "move_effect_1"


def test_valid_submission_writes_main_draft_and_criteria(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    output = tmp_path / "output"
    _FakeLoop.mode = "success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeLoop)
    design = preparation.generate_capability_design(
        robot_id="fixture-aa1",
        study={"robot_id": "fixture-aa1", "dof": 1},
        mjcf_path=mjcf,
        task_library_dir=library,
        output_dir=output,
        model="fixture-model",
        provider="holistic",
        region="fixture-region",
    )
    assert design["robot_configuration_id"] == "fixture-aa1"
    assert (output / "capability_design.json").exists()
    assert (output / "draft" / "capability_design.json").exists()
    derived = json.loads((output / "criteria.json").read_text())
    assert len(derived["criteria"]) == 3
    metadata = json.loads((output / "capability_preparation.json").read_text())
    assert metadata["error"] is None
    assert metadata["token_usage"] == {"in": 4, "out": 5}
    assert "\n\n" in _FakeLoop.last_prompt
    assert _FakeLoop.last_kwargs["max_iters"] == 6
    assert _FakeLoop.last_kwargs["max_tokens_per_turn"] == 8000
