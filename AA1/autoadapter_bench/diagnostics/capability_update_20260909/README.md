# API and environment check — 2026-09-09

AA1 baseline: `064e8af`. Work branch: `aa1/capability-v2-native-control`.
Python: `.venv/bin/python` 3.13.13; MuJoCo 3.13.0; NumPy 2.5.3.

The user selected the existing Holistic company API instead of Bedrock.
`ReactLoop(..., provider="holistic", model="eu.anthropic.claude-sonnet-4-6")`
uses the existing gateway, with `AUTOADAPTER_HOLISTICAI_API_KEY` or
`AUTOADAPTER_COMPANY_API_KEY`. The existing parent `.env.company-api` is
also supported; credentials are not copied to this repository or traces.

`holistic_tool_preflight.jsonl` records a **real** native tool call to a
local addition tool followed by the model's final answer. Its JSON summary
records success (1,301 input and 121 output tokens). This confirms transport
and tool-call compatibility only; no robot driver or task was evaluated.

The earlier `api_preflight_network` records the actual Bedrock rejection:
HTTP 403, `INVALID_PAYMENT_INSTRUMENT`. The unprivileged `api_preflight`
was a network connection failure. Neither is a robot generation result.

Focused transport checks:

```sh
.venv/bin/python -m pytest -q auto_adapter/tests/test_holistic_transport.py auto_adapter/tests/test_bedrock_sdk_compat.py
```

Result: 3 passed. These named test fixtures check message conversion,
credential redaction and legacy SDK request compatibility; the separate
preflight trace is the real API evidence.
