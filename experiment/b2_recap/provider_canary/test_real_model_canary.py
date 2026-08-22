from __future__ import annotations

import json
import os
from pathlib import Path

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
