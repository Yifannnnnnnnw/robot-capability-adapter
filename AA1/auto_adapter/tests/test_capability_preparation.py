from __future__ import annotations

import copy
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

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
                    {
                        "task_id": "task-a",
                        "name": "Reach",
                        "description": "reach",
                        "scoring": [{
                            "metric": "terminal_error",
                            "unit": "m",
                            "comparator": "<=",
                            "threshold": 0.05,
                            "source_refs": [{
                                "source_id": "fixture-source-record",
                                "specific_reference": "fixture scoring",
                            }],
                        }],
                    },
                    {
                        "task_id": "task-b",
                        "name": "Move",
                        "description": "move",
                        "scoring": [{
                            "metric": "terminal_error",
                            "unit": "m",
                            "comparator": "<=",
                            "threshold": 0.05,
                            "source_refs": [{
                                "source_id": "fixture-source-record",
                                "specific_reference": "fixture scoring",
                            }],
                        }],
                    },
                    {
                        "task_id": "task-c",
                        "name": "Hold",
                        "description": "hold",
                        "scoring": [{
                            "metric": "terminal_error",
                            "unit": "m",
                            "comparator": "<=",
                            "threshold": 0.05,
                            "source_refs": [{
                                "source_id": "fixture-source-record",
                                "specific_reference": "fixture scoring",
                            }],
                        }],
                    },
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


def _scene_design(*, extra_criterion: bool = False) -> dict:
    design = _design()
    for capability in design["capabilities"]:
        for criterion in capability["criteria"]:
            criterion["temporal"] = {"kind": "terminal"}
            criterion["aggregation"] = {"kind": "last"}
        if extra_criterion and capability["capability_id"] == "C1":
            second = copy.deepcopy(capability["criteria"][0])
            second["metric"] = "terminal_error_secondary"
            capability["criteria"].append(second)
    return design


def _scene_suite(design: dict) -> dict:
    cases = []
    for index, capability in enumerate(design["capabilities"]):
        measurements = [
            {
                "criterion_index": criterion_index,
                "operator": "site_position_error",
                "bindings": {
                    "site": "ee",
                    "target_request_field": "target_m",
                },
            }
            for criterion_index, _criterion in enumerate(capability["criteria"])
        ]
        cases.append(
            {
                "case_id": f"case-{index + 1}",
                "scene": "empty_scene",
                "capability_id": capability["capability_id"],
                "request": {"target_m": [0.0, 0.0, 0.0], "duration_s": 0.5},
                "initial_state": {
                    "robot": {},
                    "free_bodies": {},
                    "ctrl_by_actuator": {},
                    "settle_s": 0.0,
                },
                "execution": {
                    "max_sim_time_s": 1.0,
                    "wall_timeout_s": 2.0,
                },
                "measurements": measurements,
            }
        )
    return {
        "scenes": {"empty_scene": {"objects": []}},
        "cases": cases,
    }


class _FakeDesignLoop:
    design: dict = _scene_design()
    suite: dict = _scene_suite(design)
    mode = "success"
    last_kwargs = {}
    last_prompt = ""
    tool_names = []

    def __init__(self, *, tools, **kwargs):
        self._tools = tools
        self.reader = next(tool for tool in tools if tool.name == "read_file")
        self.writer = next(tool for tool in tools if tool.name == "write_file")
        self.prober = next(tool for tool in tools if tool.name == "probe_case")
        type(self).last_kwargs = kwargs
        type(self).tool_names = [tool.name for tool in tools]

    def run(self, prompt):
        type(self).last_prompt = prompt
        paths = {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if ":" in line
        }
        json.loads(self.reader.handler({"path": paths["study_path"]})["content"])
        json.loads(self.reader.handler({"path": paths["catalog_path"]})["content"])
        if type(self).mode == "no_write":
            return SimpleNamespace(
                ok=True,
                error=None,
                total_tokens={"in": 4, "out": 5},
                trace=[SimpleNamespace(stop_reason="end_turn")],
            )
        self.writer.handler(
            {
                "path": "draft/capability_design.json",
                "content": json.dumps(type(self).design),
            }
        )
        self.writer.handler(
            {
                "path": "draft/scene_cases.yaml",
                "content": yaml.safe_dump(type(self).suite, sort_keys=False),
            }
        )
        if type(self).mode == "probe":
            self.prober.handler(
                {
                    "scene_cases_path": "draft/scene_cases.yaml",
                    "case_id": "case-1",
                }
            )
        return SimpleNamespace(
            ok=True,
            error=None,
            total_tokens={"in": 6, "out": 7},
            trace=[SimpleNamespace(stop_reason="end_turn")],
        )


class _FakeLoop:
    mode = "success"
    last_prompt = ""
    last_kwargs = {}
    tool_names = []
    read_paths = []
    write_errors = []
    local_exec_result = None

    def __init__(self, *, tools, **kwargs):
        self.writer = next(tool for tool in tools if tool.name == "write_file")
        self.reader = next(tool for tool in tools if tool.name == "read_file")
        self._tools = tools
        type(self).tool_names = [tool.name for tool in tools]
        type(self).read_paths = []
        type(self).write_errors = []
        type(self).local_exec_result = None
        type(self).last_kwargs = kwargs

    def run(self, prompt):
        type(self).last_prompt = prompt
        paths = {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if ":" in line
        }
        study_path = paths["study_path"]
        catalog_path = paths["catalog_path"]
        actual_mjcf_path = paths["actual_mjcf_path"]
        assert Path(study_path).is_absolute()
        assert Path(catalog_path).is_absolute()
        assert Path(actual_mjcf_path).is_absolute()
        if self.mode == "probe_local_exec":
            local_exec = next(tool for tool in self._tools if tool.name == "local_exec")
            script = (
                "import os, mujoco; "
                f"model = mujoco.MjModel.from_xml_path({json.dumps(actual_mjcf_path)}); "
                "print(os.getcwd()); print('mujoco_imported', mujoco.__version__); "
                "print('nq', model.nq)"
            )
            result = local_exec.handler({"command": f"python -c {shlex.quote(script)}"})
            type(self).local_exec_result = result
            assert result["exit_code"] == 0, result
        study = json.loads(self.reader.handler({"path": study_path})["content"])
        catalog = json.loads(self.reader.handler({"path": catalog_path})["content"])
        relative_catalog = json.loads(
            self.reader.handler({"path": "catalog.json"})["content"]
        )
        type(self).read_paths = [study_path, catalog_path]
        assert study["robot_id"] == "fixture-aa1"
        assert catalog["tasks"][0]["scoring"][0]["source_refs"]
        assert relative_catalog["robot_configuration_id"] == "fixture-source"
        assert relative_catalog["tasks"][0]["task_id"] == catalog["tasks"][0]["task_id"]
        assert "fixture-source-record" not in prompt
        valid_write = {
            "path": "draft/capability_design.json",
            "content": json.dumps(_design()),
        }
        if self.mode == "invoke_error":
            self.writer.handler(valid_write)
            return SimpleNamespace(
                ok=False,
                error="gateway failed",
                total_tokens={"in": 2, "out": 3},
                trace=[SimpleNamespace(stop_reason="invoke_error")],
            )
        if self.mode == "invalid_then_success":
            raw_design = valid_write["content"]
            try:
                self.writer.handler(
                    {
                        "path": "draft/capability_design.json",
                        "content": raw_design[:-1],
                    }
                )
            except ValueError as exc:
                type(self).write_errors.append(str(exc))
            else:
                raise AssertionError("malformed draft was accepted")
            self.writer.handler(
                {
                    "path": "draft/capability_design.json",
                    "content": raw_design[-1:],
                    "append": True,
                }
            )
        else:
            self.writer.handler(valid_write)
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
            lambda d: d["capabilities"][0]["request_schema"].pop("additionalProperties"),
            "additionalProperties",
        ),
        (
            lambda d: d["capabilities"][0]["request_schema"]["properties"].update(
                object_body={"type": "string"}
            ),
            "scene entity",
        ),
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


def test_invoke_error_rejects_current_write_and_stale_output(
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


@pytest.mark.parametrize("requested_max_iters", [30, 31])
def test_valid_write_writes_main_draft_and_criteria(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_max_iters: int,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    (output / "catalog.json").write_text(
        json.dumps({"robot_configuration_id": "shadow", "tasks": []}),
        encoding="utf-8",
    )
    _FakeLoop.mode = "success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeLoop)
    design = preparation.generate_capability_design(
        robot_id="fixture-aa1",
        study={"robot_id": "fixture-aa1", "dof": 1, "caller_field": "preserved"},
        mjcf_path=mjcf,
        task_library_dir=library,
        output_dir=output,
        model="fixture-model",
        provider="holistic",
        region="fixture-region",
        max_iters=requested_max_iters,
    )
    assert design["robot_configuration_id"] == "fixture-aa1"
    assert (output / "capability_design.json").exists()
    assert (output / "draft" / "capability_design.json").exists()
    assert (output / "study.json").exists()
    assert json.loads((output / "study.json").read_text())["caller_field"] == "preserved"
    assert not (output / "authoring_brief.json").exists()
    assert not (output / "public_inputs.json").exists()
    assert _FakeLoop.read_paths[0].endswith("/study.json")
    assert _FakeLoop.read_paths[1].endswith("/library/catalog.json")
    derived = json.loads((output / "criteria.json").read_text())
    assert len(derived["criteria"]) == 3
    assert "capability_protocol_version" not in design
    assert "schema_version" not in design
    assert "capability_protocol_version" not in derived
    metadata = json.loads((output / "capability_preparation.json").read_text())
    assert metadata["error"] is None
    assert metadata["token_usage"] == {"in": 4, "out": 5}
    assert "\n" in _FakeLoop.last_prompt
    assert _FakeLoop.last_kwargs["max_iters"] == requested_max_iters
    assert _FakeLoop.last_kwargs["max_tokens_per_turn"] == 8000
    assert _FakeLoop.tool_names == ["read_file", "write_file", "local_exec"]


def test_local_exec_probes_actual_mjcf_in_output_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    output = tmp_path / "output"
    _FakeLoop.mode = "probe_local_exec"
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
    assert f"actual_mjcf_path: {mjcf.resolve()}" in _FakeLoop.last_prompt
    probe = _FakeLoop.local_exec_result
    assert probe is not None
    assert probe["stdout"].splitlines()[0] == str(output.resolve())
    assert "mujoco_imported" in probe["stdout"]


def test_invalid_draft_gets_feedback_then_corrected_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    _FakeLoop.mode = "invalid_then_success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeLoop)
    output = tmp_path / "output"
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
    assert _FakeLoop.write_errors
    assert "malformed JSON" in _FakeLoop.write_errors[0]
    assert json.loads((output / "draft" / "capability_design.json").read_text())[
        "capabilities"
    ]


def test_actual_study_path_is_read_without_duplicate_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    study_path = tmp_path / "study.json"
    study_path.write_text(
        json.dumps({"robot_id": "fixture-aa1", "dof": 1}),
        encoding="utf-8",
    )
    _FakeLoop.mode = "success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeLoop)
    output = tmp_path / "output"
    preparation.generate_capability_design(
        robot_id="fixture-aa1",
        study={"robot_id": "fixture-aa1", "dof": 1},
        study_path=study_path,
        mjcf_path=mjcf,
        task_library_dir=library,
        output_dir=output,
        model="fixture-model",
        provider="holistic",
        region="fixture-region",
    )
    assert not (output / "study.json").exists()
    assert _FakeLoop.read_paths[0] == str(study_path.resolve())


def test_design_mode_writes_two_current_drafts_and_real_probe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text(
        "<mujoco><option timestep='0.01'/><worldbody>"
        "<body name='tool'><site name='ee'/></body>"
        "</worldbody></mujoco>",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    _FakeDesignLoop.design = _scene_design()
    _FakeDesignLoop.suite = _scene_suite(_FakeDesignLoop.design)
    _FakeDesignLoop.mode = "probe"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeDesignLoop)

    design = preparation.generate_capability_design(
        robot_id="fixture-aa1",
        study={"robot_id": "fixture-aa1", "dof": 1},
        mjcf_path=mjcf,
        task_library_dir=library,
        output_dir=output,
        model="fixture-model",
        provider="holistic",
        region="fixture-region",
        prepare_scene_cases=True,
    )

    assert design["robot_configuration_id"] == "fixture-aa1"
    assert _FakeDesignLoop.tool_names == [
        "read_file",
        "write_file",
        "local_exec",
        "probe_case",
    ]
    assert "max_iters=30" in _FakeDesignLoop.last_prompt
    assert "Batch-read the supplied" in _FakeDesignLoop.last_prompt
    assert "bounded local_exec exploration" in _FakeDesignLoop.last_prompt
    assert "site_position_error" in _FakeDesignLoop.last_kwargs["system"]
    assert (output / "draft" / "capability_design.json").is_file()
    assert (output / "draft" / "scene_cases.yaml").is_file()
    assert (output / "capability_design.json").is_file()
    assert (output / "scene_cases.yaml").is_file()
    criteria = json.loads((output / "criteria.json").read_text(encoding="utf-8"))
    assert len(criteria["criteria"]) == 3
    assert [item["criterion_index"] for item in criteria["criteria"]] == [0, 0, 0]
    report = json.loads((output / "probe_report.json").read_text(encoding="utf-8"))
    assert report["reports"]["case-1"]["ok"] is True
    metadata = json.loads(
        (output / "capability_preparation.json").read_text(encoding="utf-8")
    )
    assert metadata["scene_cases_path"] == str((output / "scene_cases.yaml").resolve())
    assert metadata["probe_report_path"] == str((output / "probe_report.json").resolve())
    assert metadata["scene_paths"]["empty_scene"].endswith("/scenes/empty_scene/scene.xml")


def test_design_mode_exports_multiple_criteria_with_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text(
        "<mujoco><worldbody><body name='tool'><site name='ee'/></body>"
        "</worldbody></mujoco>",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    _FakeDesignLoop.design = _scene_design(extra_criterion=True)
    _FakeDesignLoop.suite = _scene_suite(_FakeDesignLoop.design)
    _FakeDesignLoop.mode = "success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeDesignLoop)

    preparation.generate_capability_design(
        robot_id="fixture-aa1",
        study={"robot_id": "fixture-aa1", "dof": 1},
        mjcf_path=mjcf,
        task_library_dir=library,
        output_dir=output,
        model="fixture-model",
        provider="holistic",
        region="fixture-region",
        prepare_scene_cases=True,
    )

    criteria = json.loads((output / "criteria.json").read_text(encoding="utf-8"))
    assert [(row["capability_id"], row["criterion_index"]) for row in criteria["criteria"]] == [
        ("C1", 0),
        ("C1", 1),
        ("C2", 0),
        ("C3", 0),
    ]


def test_design_mode_ignores_old_artifacts_when_current_loop_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text("<mujoco/>", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    stale = _scene_design()
    (output / "capability_design.json").write_text(json.dumps(stale), encoding="utf-8")
    (output / "scene_cases.yaml").write_text(
        yaml.safe_dump(_scene_suite(stale)),
        encoding="utf-8",
    )
    (output / "probe_report.json").write_text(
        json.dumps({"reports": {"case-1": {"ok": True}}}),
        encoding="utf-8",
    )
    _FakeDesignLoop.mode = "no_write"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeDesignLoop)

    with pytest.raises(
        preparation.CapabilityPreparationError,
        match="without a valid current draft write",
    ):
        preparation.generate_capability_design(
            robot_id="fixture-aa1",
            study={"robot_id": "fixture-aa1", "dof": 1},
            mjcf_path=mjcf,
            task_library_dir=library,
            output_dir=output,
            model="fixture-model",
            provider="holistic",
            region="fixture-region",
            prepare_scene_cases=True,
        )


def test_design_mode_rejects_current_failed_probe_over_old_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_adapter import scene_runtime

    library = _library(tmp_path)
    mjcf = tmp_path / "scene.xml"
    mjcf.write_text(
        "<mujoco><worldbody><body name='tool'><site name='ee'/></body>"
        "</worldbody></mujoco>",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()
    stale = _scene_design()
    (output / "capability_design.json").write_text(json.dumps(stale), encoding="utf-8")
    (output / "scene_cases.yaml").write_text(
        yaml.safe_dump(_scene_suite(stale)),
        encoding="utf-8",
    )
    (output / "probe_report.json").write_text(
        json.dumps({"reports": {"case-1": {"ok": True}}}),
        encoding="utf-8",
    )
    _FakeDesignLoop.design = _scene_design()
    _FakeDesignLoop.suite = _scene_suite(_FakeDesignLoop.design)
    _FakeDesignLoop.mode = "success"
    monkeypatch.setattr(preparation, "ReactLoop", _FakeDesignLoop)

    def failed_probe(**_kwargs):
        return {"ok": False, "errors": ["current model failed to compile"]}

    monkeypatch.setattr(scene_runtime, "probe_case", failed_probe)
    with pytest.raises(
        preparation.CapabilityPreparationError,
        match="did not report ok=true",
    ):
        preparation.generate_capability_design(
            robot_id="fixture-aa1",
            study={"robot_id": "fixture-aa1", "dof": 1},
            mjcf_path=mjcf,
            task_library_dir=library,
            output_dir=output,
            model="fixture-model",
            provider="holistic",
            region="fixture-region",
            prepare_scene_cases=True,
        )
    report = json.loads((output / "probe_report.json").read_text(encoding="utf-8"))
    assert report["reports"]["case-1"]["ok"] is False
