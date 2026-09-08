# Opus failed-request replay with a 300-second client deadline

**Actual result (2026-09-09): the unchanged Opus request returned HTTP 200 in
72.834 seconds.** It proposed one `execute_python` call containing a 1,711-character
public control probe. The code passes a static Python syntax check but was not
executed. No Driver was written and no Harness or Repair phase was run.
Preparation commit: `22b8125`.

| Measurement | Actual result |
| --- | --- |
| Physical requests / retries | 1 / 0 |
| Original versus replay request body | Identical serialized bytes: 91,079 |
| Connection ready | 0.082522 s |
| First response byte | 72.833992 s |
| Wait after connection ready | 72.751470 s |
| Total duration | 72.834434 s |
| HTTP / curl exit | 200 / 0 |
| Returned model / stop reason | `eu.anthropic.claude-opus-5` / `tool_calls` |
| Returned input / output tokens | 35,364 / 5,402 |
| Configured reference estimate | USD 0.343057; actual Holistic bill unknown |

Because this success arrived before 120 seconds, it does not establish that the
longer deadline caused recovery, or that this Opus backend supports waits beyond
120 seconds. It establishes that the same request body that timed out twice can
also return successfully. Upstream load, response-generation variability,
caching and transport differences have not been isolated. A 300-second server
ceiling is neither proved nor disproved by this replay. The earlier cheap-model
test independently returned after a 143.5-second wait.

The successful response contains no visible prose, but reports 5,402 output
tokens and one tool request. No reasoning-token breakdown is supplied; total
latency cannot be attributed entirely to thinking. Returned content remains
an unexecuted proposal, not a robot synthesis success.

Evidence: [result](../../runs/diagnostic/holistic-opus5-300s-replay-20260909/result.json),
[request](../../runs/diagnostic/holistic-opus5-300s-replay-20260909/request.json),
[response](../../runs/diagnostic/holistic-opus5-300s-replay-20260909/response.json),
[connection timings](../../runs/diagnostic/holistic-opus5-300s-replay-20260909/curl_timing.json),
[settings and source request](../../runs/diagnostic/holistic-opus5-300s-replay-20260909/test_settings.json).

Two focused offline checks passed; actual wire bytes, timings, usage and returned
tool type were checked after the request. An initial automatic approval rejection
prevented process launch. Read-only checks confirmed this was the same previously
authorized public-input payload and destination; re-review allowed the unchanged
command. Only the subsequently launched request incurred model usage. The result
batch updates this document only; mainline defaults remain unchanged.

The user requested changing the wait to 300 seconds and testing. Replay exactly
the saved `request_body` of Franka continuation test `call-0007`, which previously
timed out at 120 seconds and again on its retry. This sends one Opus request;
it does not restart Study, Generate's tool loop, Harness or Repair. Returned tool
calls are recorded, never executed by this HTTP test.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/test_holistic_request_deadline.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-franka-one-repair.json \
  --replay-request autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/model_requests/call-0007.json \
  --output autoadapter/runs/diagnostic/holistic-opus5-300s-replay-20260909
```

Keep the original model, messages, tools, adaptive thinking, 16,384 output-token
limit, temperature and absent effort/stream fields. Serialize the body exactly
as the original client did. The test uses the previously verified curl HTTP/1.1
transport to record connection and first-byte timings, with a 300-second total
deadline, 15-second connect timeout and zero retries. The prior robot client used
urllib; record this transport difference rather than claim every environmental
variable is identical. No mainline provider profile is changed.

A successful response after 120 seconds would establish that this Opus request
can complete beyond the old local limit. A 300-second client timeout would not
by itself establish a server-side 300-second cap. Neither outcome establishes a
robot capability score. Report response/tool type, timing, returned usage and
unknown timeout usage, without executing returned code or launching another run.

This small direct-implementation batch owns this document, the existing deadline
script's optional saved-request/config inputs, and its focused replay check.
Luna Max is not used. Minimum check: two focused offline checks and actual saved
body equality, then one real request after preparation commit. Deliver the
preparation/result commit IDs and request/response/timing evidence. No robot,
threshold, thesis, formal experiment or historical result changes are in scope.
