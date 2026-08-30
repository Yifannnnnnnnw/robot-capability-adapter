from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import runtime.b2 as b2
import runtime.parallel as parallel
from runtime.b2 import FormalDispatchBlocked, resolve_manifest, run_formal_unit


def _enabled_manifest():
    resolved = resolve_manifest()
    document = dict(resolved.document)
    document["formal_dispatch_enabled"] = True
    document["blockers"] = []
    return replace(resolved, document=document, provider_formal_ready=True)


def _disabled_manifest():
    resolved = resolve_manifest()
    document = dict(resolved.document)
    document["formal_dispatch_enabled"] = False
    document["blockers"] = [
        {"code": "TEST_PAUSE", "message": "Focused paused-manifest fixture."}
    ]
    return replace(resolved, document=document, provider_formal_ready=True)


def test_disabled_manifest_rejects_before_model_factory_and_writes_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def model_factory(*_args):
        nonlocal called
        called = True
        raise AssertionError("model factory must not be called while B2 is paused")

    manifest = _disabled_manifest()
    unit_id = manifest.units[0].unit_id
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)
    with pytest.raises(FormalDispatchBlocked):
        run_formal_unit(
            unit_id,
            output_root=tmp_path,
            model_factory=model_factory,
        )

    assert called is False
    terminal_path = b2.terminal_path_for_unit(tmp_path, unit_id)
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["classification"] == "not_run"
    assert terminal["success"] is False
    assert "credential" not in terminal


def test_one_enabled_unit_calls_existing_episode_runner_once_with_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)
    calls: list[object] = []

    class FakeModel:
        provider_call_records = (
            {
                "status": "success",
                "returned_model": "eu.anthropic.claude-sonnet-4-6",
                "input_tokens": 10,
                "output_tokens": 5,
            },
        )
        provider_exchange_records = (
            {"status": "success", "request_messages": [], "response_body": {}},
        )

    def episode_runner(**kwargs):
        calls.append(kwargs)
        config = kwargs["config"]
        assert config.record_video is True
        assert config.robot_configuration_id == unit.robot_configuration_id
        assert config.task_id == unit.task_id
        assert config.replicate_id == unit.replicate_id
        return {
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "model_calls": 1,
                "capability_calls": 1,
                "trace": [{"turn_index": 1}],
            },
            "harness": {
                "physical_harness_verdict": "PASS",
                "physical_integrity_passed": True,
                "video_complete": True,
                "task_metric_passed": True,
                "task_clause_results": [{"clause_id": "test", "passed": True}],
            },
            "worker": {
                "worker_completed": True,
                "candidate_log": [{"event": "worker_completed"}],
            },
        }

    terminal = run_formal_unit(
        unit.unit_id,
        manifest_path=manifest.manifest_path,
        output_root=tmp_path,
        credential_loader=lambda _pin, _env: "parent-secret",
        model_factory=lambda _config, _credential: FakeModel(),
        package_loader=lambda path: {"package_root": str(path)},
        episode_runner=episode_runner,
    )

    assert len(calls) == 1
    assert terminal["classification"] == "harness_pass"
    assert terminal["success"] is True
    serialized = b2.terminal_path_for_unit(tmp_path, unit.unit_id).read_text(
        encoding="utf-8"
    )
    assert "parent-secret" not in serialized
    episode_record = json.loads(
        Path(terminal["episode_record_path"]).read_text(encoding="utf-8")
    )
    provider_record = json.loads(
        Path(terminal["provider_record_path"]).read_text(encoding="utf-8")
    )
    assert episode_record["episode"]["controller"]["trace"]
    assert episode_record["episode"]["worker"]["worker_completed"] is True
    assert episode_record["episode"]["harness"]["task_clause_results"]
    assert provider_record["calls"][0]["input_tokens"] == 10
    assert provider_record["raw_secret_free_exchanges"]
    assert provider_record["total_cost_usd"] is not None
    assert provider_record["holisticai_route_profile"]["profile_id"] == (
        "holisticai-gateway-long-request-eu-west-2-v1"
    )
    assert provider_record["resolved_route"]["credential_env"] == (
        "AUTOADAPTER_HOLISTICAI_API_KEY"
    )
    assert terminal["model_identity"]["resolved_route"]["endpoint_url"].endswith(
        "/v1/chat/completions"
    )
    assert "parent-secret" not in json.dumps(episode_record)
    assert "parent-secret" not in json.dumps(provider_record)


def test_provider_construction_failure_is_infrastructure_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _enabled_manifest()
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)

    terminal = run_formal_unit(
        manifest.units[0].unit_id,
        output_root=tmp_path,
        credential_loader=lambda _pin, _env: "parent-secret",
        model_factory=lambda _config, _credential: (_ for _ in ()).throw(
            RuntimeError("provider construction failed")
        ),
        package_loader=lambda _path: object(),
        episode_runner=lambda **_kwargs: pytest.fail("episode must not run"),
    )

    assert terminal["classification"] == "infrastructure_failure"
    assert terminal["success"] is False
    assert terminal["utc_finished_at"]


def test_episode_runner_exception_is_infrastructure_not_controller_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)

    class FakeModel:
        provider_call_records = ()
        provider_exchange_records = ()

    terminal = run_formal_unit(
        manifest.units[0].unit_id,
        output_root=tmp_path,
        credential_loader=lambda _pin, _env: "parent-secret",
        model_factory=lambda _config, _credential: FakeModel(),
        package_loader=lambda _path: object(),
        episode_runner=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("renderer failed")
        ),
    )

    assert terminal["classification"] == "infrastructure_failure"
    assert terminal["evaluable"] is False


def test_existing_formal_output_is_rejected_before_credential_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)
    terminal_path = b2.terminal_path_for_unit(tmp_path, unit.unit_id)
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text("{}\n", encoding="utf-8")
    credential_loaded = False

    def credential_loader(_pin, _env):
        nonlocal credential_loaded
        credential_loaded = True
        return "parent-secret"

    with pytest.raises(b2.B2FormalError, match="refusing to overwrite"):
        run_formal_unit(
            unit.unit_id,
            output_root=tmp_path,
            credential_loader=credential_loader,
        )
    assert credential_loaded is False


def test_existing_episode_directory_is_rejected_before_credential_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    monkeypatch.setattr(b2, "resolve_manifest", lambda _path: manifest)
    b2.episode_output_for_unit(tmp_path, unit.unit_id).mkdir(parents=True)
    credential_loaded = False

    def credential_loader(_pin, _env):
        nonlocal credential_loaded
        credential_loaded = True
        return "parent-secret"

    with pytest.raises(b2.B2FormalError, match="output is not fresh"):
        run_formal_unit(
            unit.unit_id,
            output_root=tmp_path,
            credential_loader=credential_loader,
        )
    assert credential_loaded is False


def test_default_credential_files_separate_holisticai_and_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[Path] = []

    def load_dotenv(path: Path) -> dict[str, str]:
        selected.append(path)
        return {
            "AUTOADAPTER_HOLISTICAI_API_KEY": "holisticai-secret",
            "AUTOADAPTER_MODEL_API_KEY": "direct-secret",
        }

    monkeypatch.setattr(b2, "_load_dotenv", load_dotenv)
    manifest = resolve_manifest()
    holisticai_pin = b2._provider_runtime_pin(
        manifest.provider_pins["M1"], manifest.provider_route_profiles["M1"]
    )
    m5_pin = b2._provider_runtime_pin(
        manifest.provider_pins["M5"], manifest.provider_route_profiles["M5"]
    )

    assert b2._default_credential_loader(holisticai_pin, None) == "holisticai-secret"
    assert selected[-1].name == ".env.holisticai-api"
    assert b2._default_credential_loader(m5_pin, None) == "direct-secret"
    assert selected[-1].name == ".env"


def test_integrity_guard_failure_is_an_evaluable_harness_fail() -> None:
    class FakeModel:
        provider_call_records = (
            {
                "status": "success",
                "returned_model": "eu.anthropic.claude-sonnet-4-6",
            },
        )

    classification = b2._classify_episode(
        {
            "controller": {"status": "CONTROLLER_FINISHED"},
            "harness": {
                "physical_harness_verdict": "FAIL",
                "physical_integrity_passed": False,
                "video_complete": True,
            },
        },
        model=FakeModel(),
        expected_model="eu.anthropic.claude-sonnet-4-6",
    )

    assert classification[:3] == ("harness_fail", True, False)


def test_cost_uses_base_rates_at_exact_long_context_threshold() -> None:
    price = {
        "input_cache_hit": 0.5,
        "input_cache_miss": 5.0,
        "output": 30.0,
        "long_context": {
            "applies_when_input_tokens_gt": 272000,
            "input_cache_hit": 1.0,
            "input_cache_miss": 10.0,
            "output": 45.0,
        },
    }

    cost = b2._call_cost_usd(
        {
            "input_tokens": 272000,
            "cache_read_tokens": 72000,
            "output_tokens": 1000,
        },
        price,
    )

    assert cost == pytest.approx(
        (72000 * 0.5 + 200000 * 5.0 + 1000 * 30.0) / 1_000_000
    )


def test_cost_switches_whole_request_to_long_context_rates_above_threshold() -> None:
    price = {
        "input_cache_hit": 0.5,
        "input_cache_miss": 5.0,
        "output": 30.0,
        "long_context": {
            "applies_when_input_tokens_gt": 272000,
            "input_cache_hit": 1.0,
            "input_cache_miss": 10.0,
            "output": 45.0,
        },
    }

    cost = b2._call_cost_usd(
        {
            "input_tokens": 272001,
            "cache_read_tokens": 72000,
            "output_tokens": 1000,
        },
        price,
    )

    assert cost == pytest.approx(
        (72000 * 1.0 + 200001 * 10.0 + 1000 * 45.0) / 1_000_000
    )


def test_cost_accepts_explicit_cache_hit_and_miss_split() -> None:
    cost = b2._call_cost_usd(
        {
            "input_cache_hit_tokens": 100000,
            "input_cache_miss_tokens": 200000,
            "output_tokens": 2000,
        },
        {
            "input_cache_hit": 0.5,
            "input_cache_miss": 5.0,
            "output": 30.0,
            "long_context": {
                "applies_when_input_tokens_gt": 272000,
                "input_cache_hit": 1.0,
                "input_cache_miss": 10.0,
                "output": 45.0,
            },
        },
    )

    assert cost == pytest.approx(
        (100000 * 1.0 + 200000 * 10.0 + 2000 * 45.0) / 1_000_000
    )


def test_scheduler_rejects_existing_output_before_subprocess_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    monkeypatch.setattr(parallel, "resolve_manifest", lambda _path: manifest)
    terminal_path = b2.terminal_path_for_unit(tmp_path, unit.unit_id)
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text("{}\n", encoding="utf-8")
    spawned = False

    def run_one(**_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("stale output must be rejected before spawning")

    monkeypatch.setattr(parallel, "_run_one", run_one)
    with pytest.raises(b2.B2FormalError, match="output is not fresh"):
        parallel.run_scheduler(
            manifest_path=manifest.manifest_path,
            output_root=tmp_path,
            unit_ids=[unit.unit_id],
        )
    assert spawned is False


def test_scheduler_worker_never_accepts_a_preexisting_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    terminal_path = b2.terminal_path_for_unit(tmp_path, unit.unit_id)
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text('{"classification":"harness_pass"}\n', encoding="utf-8")
    spawned = False

    def subprocess_run(*_args, **_kwargs):
        nonlocal spawned
        spawned = True
        return SimpleNamespace(returncode=99)

    monkeypatch.setattr(parallel.subprocess, "run", subprocess_run)
    record = parallel._run_one(
        unit=unit,
        expected_model=str(manifest.provider_pins[unit.model_id]["exact_model_id"]),
        manifest_path=manifest.manifest_path,
        output_root=tmp_path,
        env_file=None,
    )

    assert spawned is False
    assert record["status"] == "infrastructure_absence"


def test_scheduler_worker_writes_terminal_when_subprocess_produces_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _enabled_manifest()
    unit = manifest.units[0]
    monkeypatch.setattr(
        parallel.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=99),
    )

    record = parallel._run_one(
        unit=unit,
        expected_model=str(manifest.provider_pins[unit.model_id]["exact_model_id"]),
        manifest_path=manifest.manifest_path,
        output_root=tmp_path,
        env_file=None,
    )

    terminal = json.loads(
        b2.terminal_path_for_unit(tmp_path, unit.unit_id).read_text(encoding="utf-8")
    )
    assert record["status"] == "terminal"
    assert terminal["classification"] == "infrastructure_failure"
    assert terminal["success"] is False


def test_disabled_scheduler_rejects_before_spawning_unit_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = False

    def run_one(**_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("scheduler must not spawn while B2 is paused")

    monkeypatch.setattr(parallel, "_run_one", run_one)
    manifest = _disabled_manifest()
    monkeypatch.setattr(parallel, "resolve_manifest", lambda _path: manifest)
    with pytest.raises(FormalDispatchBlocked):
        parallel.run_scheduler(
            output_root=tmp_path,
            unit_ids=[manifest.units[0].unit_id],
        )
    assert spawned is False
