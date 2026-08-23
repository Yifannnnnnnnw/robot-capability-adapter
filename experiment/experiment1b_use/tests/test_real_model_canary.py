from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REAL_MODEL_ROOT = HERE.parent / "diagnostics" / "real_model"
if str(REAL_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(REAL_MODEL_ROOT))

import run_real_model_canary as canary
from run_real_model_canary import _load_env_values, _provider_inputs


def _pin() -> dict[str, object]:
    return {
        "transport": "openai-compatible",
        "exact_model_id": "pinned-model",
        "endpoint_base_url": "https://model.example/v1",
        "endpoint_path": "/chat/completions",
        "credential_env": "CANARY_SECRET",
        "auth_header": "X-Api-Key",
        "auth_prefix": "",
        "vendor": "Example",
        "inference_settings": {
            "temperature": 0.0,
            "thinking": None,
            "max_tokens": 4096,
            "tool_history_mode": "native",
            "history_char_budget": 80000,
            "timeout_s": 120,
        },
    }


def test_env_loader_does_not_mutate_process_environment(tmp_path: Path) -> None:
    path = tmp_path / ".env.company-api"
    path.write_text(
        "# comment\nexport FIRST='one'\nSECOND=\"two\"\n",
        encoding="utf-8",
    )
    before = dict(os.environ)

    assert _load_env_values(path) == {"FIRST": "one", "SECOND": "two"}
    assert dict(os.environ) == before


def test_provider_inputs_keep_credential_out_of_config_and_repr() -> None:
    secret = "must-remain-parent-only"
    config, credential, credential_env = _provider_inputs(
        provider_pin=_pin(),
        env_values={"CANARY_SECRET": secret},
    )

    assert credential == secret
    assert credential_env == "CANARY_SECRET"
    assert config.model == "pinned-model"
    assert config.auth_header == "X-Api-Key"
    assert secret not in repr(config)
    assert secret not in json.dumps(config.__dict__)


def test_video_request_reaches_episode_and_is_required_for_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_path = tmp_path / "provider.json"
    provider_path.write_text(json.dumps(_pin()), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("CANARY_SECRET=parent-only\n", encoding="utf-8")
    captured = {}

    class _Model:
        provider_call_records = (
            {"status": "success", "returned_model": "pinned-model"},
        )
        provider_exchange_records = ()

        def __init__(self, **_kwargs) -> None:
            pass

    def _episode(*, config, **_kwargs):
        captured["config"] = config
        return {
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "model_calls": 1,
                "capability_calls": 1,
            },
            "worker": {"worker_completed": True},
            "harness": {
                "task_metric_passed": True,
                "physical_execution_passed": True,
                "physical_integrity_passed": True,
                "video_complete": True,
                "physical_harness_verdict": "PASS",
            },
        }

    monkeypatch.setattr(canary, "ReCAPJsonModelClient", _Model)
    monkeypatch.setattr(canary, "load_robot_package", lambda _path: SimpleNamespace())
    monkeypatch.setattr(canary, "run_b2_diagnostic_episode", _episode)
    monkeypatch.setattr(canary, "_code_version", lambda: {"git_commit": "test"})

    report = canary.run(
        env_path=env_path,
        provider_path=provider_path,
        output_path=tmp_path / "report.json",
        wall_timeout_s=30.0,
        record_video=True,
        task_id="mw_pick_place",
    )

    assert captured["config"].record_video is True
    assert captured["config"].robot_configuration_id == "robotstudio_so101"
    assert captured["config"].task_id == "mw_pick_place"
    assert report["video_requested"] is True
    assert report["summary"]["video_requirement_satisfied"] is True
    assert report["summary"]["diagnostic_chain_completed"] is True


def test_go2_selection_reaches_the_same_episode_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_path = tmp_path / "provider.json"
    provider_path.write_text(json.dumps(_pin()), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("CANARY_SECRET=parent-only\n", encoding="utf-8")
    captured = {}

    class _Model:
        provider_call_records = (
            {"status": "success", "returned_model": "pinned-model"},
        )
        provider_exchange_records = ()

        def __init__(self, **_kwargs) -> None:
            pass

    def _episode(*, config, **_kwargs):
        captured["config"] = config
        return {
            "controller": {
                "status": "CONTROLLER_FINISHED",
                "model_calls": 1,
                "capability_calls": 1,
            },
            "worker": {"worker_completed": True},
            "harness": {
                "task_metric_passed": False,
                "physical_execution_passed": True,
                "physical_integrity_passed": True,
                "video_complete": True,
                "physical_harness_verdict": "FAIL",
            },
        }

    monkeypatch.setattr(canary, "ReCAPJsonModelClient", _Model)
    monkeypatch.setattr(canary, "load_robot_package", lambda _path: SimpleNamespace())
    monkeypatch.setattr(canary, "run_b2_diagnostic_episode", _episode)
    monkeypatch.setattr(canary, "_code_version", lambda: {"git_commit": "test"})

    report = canary.run(
        env_path=env_path,
        provider_path=provider_path,
        output_path=tmp_path / "report.json",
        wall_timeout_s=30.0,
        record_video=True,
        robot_id="unitree-go2-stock-12dof",
        task_id="GO2-T02",
    )

    assert captured["config"].robot_configuration_id == "unitree-go2-stock-12dof"
    assert captured["config"].task_id == "GO2-T02"
    assert report["summary"]["independent_harness_verdict"] == "FAIL"
    assert report["summary"]["diagnostic_chain_completed"] is True
