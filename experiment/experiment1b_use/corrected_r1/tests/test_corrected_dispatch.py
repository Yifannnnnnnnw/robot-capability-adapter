from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter/src"
for root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from experiment.experiment1b_use.corrected_r1.runtime import dispatch  # noqa: E402


def _gate(tmp_path: Path, *, fail_task: str | None = None) -> Path:
    evidence = tmp_path / "positive-control-evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    video = tmp_path / "positive-control.mp4"
    video.write_bytes(b"video")
    gate = tmp_path / "positive_control_index.json"
    gate.write_text(
        json.dumps(
            {
                "artifact_type": "b2_corrected_r1_positive_control_index",
                "schema_version": "1.0",
                "audit_identity": {
                    "document_id": "AA2-B2-CORRECTED-R1",
                    "revision": "1.0.0",
                },
                "required_task_ids": list(dispatch.EXPECTED_TASK_ORDER),
                "results": [
                    {
                        "task_id": task_id,
                        "status": "FAIL" if task_id == fail_task else "PASS",
                        "passed": task_id != fail_task,
                        "trusted_harness": True,
                        "video_complete": True,
                        "record_path": str(evidence),
                        "video_path": str(video),
                    }
                    for task_id in dispatch.EXPECTED_TASK_ORDER
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return gate


def _unit(
    model_id: str = "M1",
    task_id: str = "GO2-T02",
    origin: str = "fresh_corrected",
) -> dispatch.CorrectedUnit:
    robot = (
        "unitree-go2-stock-12dof"
        if task_id.startswith("GO2-")
        else "robotstudio_so101"
    )
    return dispatch.CorrectedUnit(
        unit_id=f"b2-corrected-r1::{robot}::{task_id}::{model_id}::R1",
        robot_configuration_id=robot,
        task_id=task_id,
        model_id=model_id,
        replicate_id="R1",
        execution_origin=origin,
    )


def _manifest(
    tmp_path: Path,
    units: tuple[dispatch.CorrectedUnit, ...],
    *,
    gate: Path,
) -> dispatch.ResolvedCorrectedManifest:
    package_root = tmp_path / "package"
    package_root.mkdir(exist_ok=True)
    driver = tmp_path / "driver.py"
    driver.write_text("# fixed\n", encoding="utf-8")
    design = tmp_path / "capability_design.json"
    design.write_text("{}\n", encoding="utf-8")
    provider_manifest = tmp_path / "provider_manifest.json"
    provider_manifest.write_text(
        json.dumps(
            {
                "common_transport_policy": {
                    "transport": "openai-compatible",
                    "timeout_s": 120,
                    "max_tokens": 4096,
                    "history_char_budget": 80000,
                },
                "common_controller_contract": {"budgets": "fixed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task_suite = tmp_path / "task_suite.json"
    task_suite.write_text("{}\n", encoding="utf-8")
    reference = tmp_path / "selection.json"
    reference.write_text("{}\n", encoding="utf-8")
    source = tmp_path / "M1.json"
    source.write_text("{}\n", encoding="utf-8")
    pin = {
        "backbone_id": "M1",
        "vendor": "company",
        "exact_model_id": "exact-model",
        "provider_model_revision": "fixed-revision",
        "endpoint_base_url": "https://provider.invalid/v1",
        "credential_env": "API_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "price_snapshot": {
            "input_cache_hit": 0.0,
            "input_cache_miss": 1.0,
            "output": 2.0,
        },
    }
    pins = {model_id: {**pin, "backbone_id": model_id} for model_id in {u.model_id for u in units}}
    sources = {model_id: source for model_id in pins}
    selection = {
        "package_root": str(package_root),
        "driver_path": str(driver),
        "capability_design_path": str(design),
    }
    return dispatch.ResolvedCorrectedManifest(
        manifest_path=tmp_path / "manifest.json",
        document={},
        task_suite_path=task_suite,
        provider_manifest_path=provider_manifest,
        reference_selection_path=reference,
        positive_control_index_path=gate,
        provider_pins=pins,
        provider_source_paths=sources,
        selections={
            "unitree-go2-stock-12dof": selection,
            "robotstudio_so101": selection,
        },
        units=units,
    )


def test_failed_positive_control_blocks_before_credential_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _unit()
    manifest = _manifest(
        tmp_path, (unit,), gate=_gate(tmp_path, fail_task="GO2-T06")
    )
    monkeypatch.setattr(dispatch, "resolve_corrected_manifest", lambda _path: manifest)
    credential_called = False

    def credential_loader(_pin, _env):
        nonlocal credential_called
        credential_called = True
        return "secret"

    output = tmp_path / "output"
    with pytest.raises(dispatch.CorrectedDispatchBlocked, match="GO2-T06"):
        dispatch.run_corrected_unit(
            unit.unit_id,
            manifest_path=manifest.manifest_path,
            output_root=output,
            credential_loader=credential_loader,
        )
    assert credential_called is False
    assert output.exists() is False


def test_successful_unit_records_nonformal_identity_harness_video_and_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _unit()
    manifest = _manifest(tmp_path, (unit,), gate=_gate(tmp_path))
    monkeypatch.setattr(dispatch, "resolve_corrected_manifest", lambda _path: manifest)
    monkeypatch.setattr(dispatch.base_b2, "_provider_config", lambda *_args: object())

    class FakeModel:
        provider_call_records = (
            {
                "status": "success",
                "returned_model": "exact-model",
                "input_tokens": 100,
                "output_tokens": 10,
            },
        )
        provider_exchange_records = ({"status": "success", "response_body": {}},)

    def episode_runner(**kwargs):
        assert kwargs["config"].record_video is True
        return {
            "formal_episode": False,
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "model_calls": 1,
                "capability_calls": 1,
            },
            "worker": {"worker_completed": True},
            "harness": {
                "physical_harness_verdict": "PASS",
                "task_metric_passed": True,
                "physical_execution_passed": True,
                "physical_integrity_passed": True,
                "video_complete": True,
            },
        }

    terminal = dispatch.run_corrected_unit(
        unit.unit_id,
        manifest_path=manifest.manifest_path,
        output_root=tmp_path / "output",
        credential_loader=lambda _pin, _env: "parent-secret",
        model_factory=lambda _config, _credential: FakeModel(),
        package_loader=lambda _path: object(),
        episode_runner=episode_runner,
    )
    assert terminal["artifact_type"] == "b2_corrected_r1_unit_terminal"
    assert terminal["formal_episode"] is False
    assert terminal["formal_denominator_entry"] is False
    assert terminal["execution_origin"] == "fresh_corrected"
    assert terminal["classification"] == "harness_pass"
    assert terminal["evaluable"] is True
    assert terminal["success"] is True
    assert terminal["model_identity"]["exact_match"] is True
    assert terminal["harness"]["video_complete"] is True
    assert terminal["provider_calls"] == 1
    assert terminal["total_cost_usd"] == pytest.approx(0.00012)
    provider_record = Path(terminal["provider_record_path"]).read_text(encoding="utf-8")
    episode_record = Path(terminal["episode_record_path"]).read_text(encoding="utf-8")
    assert "parent-secret" not in provider_record
    assert "parent-secret" not in episode_record


def test_scheduler_retries_only_infrastructure_once_and_uses_fixed_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_unit = _unit("M1")
    m5_unit = _unit("M5")
    manifest = _manifest(tmp_path, (company_unit, m5_unit), gate=_gate(tmp_path))
    monkeypatch.setattr(dispatch, "resolve_corrected_manifest", lambda _path: manifest)
    company_env = tmp_path / "company.env"
    company_env.write_text("API_KEY=company\n", encoding="utf-8")
    m5_env = tmp_path / "m5.env"
    m5_env.write_text("API_KEY=m5\n", encoding="utf-8")
    calls: dict[str, int] = {}

    def process_runner(command, **_kwargs):
        unit_id = command[command.index("--unit-id") + 1]
        output = Path(command[command.index("--output") + 1])
        calls[unit_id] = calls.get(unit_id, 0) + 1
        classification = (
            "controller_failure"
            if unit_id == company_unit.unit_id
            else "infrastructure_failure"
            if calls[unit_id] == 1
            else "harness_pass"
        )
        terminal_path = dispatch.corrected_terminal_path(output, unit_id)
        terminal_path.parent.mkdir(parents=True, exist_ok=True)
        terminal_path.write_text(
            json.dumps(
                {
                    "artifact_type": "b2_corrected_r1_unit_terminal",
                    "audit_identity": {
                        "document_id": "AA2-B2-CORRECTED-R1",
                        "revision": "1.0.0",
                    },
                    "formal_episode": False,
                    "formal_denominator_entry": False,
                    "unit_id": unit_id,
                    "execution_origin": "fresh_corrected",
                    "classification": classification,
                    "evaluable": classification == "harness_pass",
                    "success": classification == "harness_pass",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    scheduler = dispatch.run_corrected_scheduler(
        manifest_path=manifest.manifest_path,
        output_root=tmp_path / "scheduler-output",
        company_env_file=company_env,
        m5_env_file=m5_env,
        unit_ids=[company_unit.unit_id, m5_unit.unit_id],
        process_runner=process_runner,
    )
    by_unit = {record["unit_id"]: record for record in scheduler["records"]}
    assert calls[company_unit.unit_id] == 1
    assert by_unit[company_unit.unit_id]["retry_used"] is False
    assert by_unit[company_unit.unit_id]["provider_lane"] == "company"
    assert calls[m5_unit.unit_id] == 2
    assert by_unit[m5_unit.unit_id]["retry_used"] is True
    assert by_unit[m5_unit.unit_id]["provider_lane"] == "m5"
    assert scheduler["company_workers"] == 3
    assert scheduler["m5_workers"] == 1
    assert scheduler["total_worker_limit"] == 4


def test_replacement_terminal_links_original_without_counting_old_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _unit("M2", "mw_push_to_goal", "replacement")
    manifest = _manifest(tmp_path, (unit,), gate=_gate(tmp_path))
    monkeypatch.setattr(dispatch, "resolve_corrected_manifest", lambda _path: manifest)
    monkeypatch.setattr(dispatch.base_b2, "_provider_config", lambda *_args: object())

    class FakeModel:
        provider_call_records = (
            {
                "status": "success",
                "returned_model": "exact-model",
                "input_tokens": 1,
                "output_tokens": 1,
            },
        )
        provider_exchange_records = ()

    terminal = dispatch.run_corrected_unit(
        unit.unit_id,
        manifest_path=manifest.manifest_path,
        output_root=tmp_path / "replacement-output",
        credential_loader=lambda _pin, _env: "parent-credential-value",
        model_factory=lambda _config, _credential: FakeModel(),
        package_loader=lambda _path: object(),
        episode_runner=lambda **_kwargs: {
            "controller": {"status": "CONTROLLER_FINISHED"},
            "worker": {},
            "harness": {
                "physical_harness_verdict": "FAIL",
                "physical_integrity_passed": True,
                "video_complete": True,
            },
        },
    )
    assert terminal["execution_origin"] == "replacement"
    assert terminal["classification"] == "harness_fail"
    assert terminal["original_incomplete_terminal_path"].endswith(
        "b2__robotstudio_so101__mw_push_to_goal__M2__R1.json"
    )
    assert "original_cost" not in terminal
