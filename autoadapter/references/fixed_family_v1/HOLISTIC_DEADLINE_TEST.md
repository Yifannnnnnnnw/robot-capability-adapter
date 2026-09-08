# Cheap-model test of the gateway's 120-second boundary

**Actual result (2026-09-08): HTTP 200 after 143.508 seconds from connection
readiness to the first response byte.** The gateway returned the full response
successfully beyond 120 seconds for `deepseek.v3.2`. This contradicts a universal
120-second cutoff on this gateway; it does not establish the Opus backend's
behavior. Preparation commit: `4a0d20c`.

| Measurement | Actual result |
| --- | --- |
| Physical requests / retries | 1 / 0 |
| Connection ready | 0.072048 s |
| First response byte from request start | 143.579776 s |
| Wait after connection ready | 143.507728 s |
| Total duration | 143.584511 s |
| HTTP / curl exit | 200 / 0 |
| Returned model | `deepseek.v3.2` |
| Returned input / output tokens | 100 / 8,192 |
| Configured usage estimate | USD 0.0152172; actual Holistic charge unknown |

Returned usage equals the configured output limit; the gateway reports
`finish_reason=stop`, so no native provider stop reason is inferred. Completion
of the fictional record list is irrelevant to this transport test. No robot or
tool code was executed. The client used HTTP/1.1, non-streaming output, disabled
thinking, a 300-second limit and no retries. Connection timing confirms that the
extra wait was not DNS/TLS setup. The measured wait includes request upload and
upstream processing; it is not a direct measurement of model inference time.

Evidence: [test result](../../runs/diagnostic/holistic-v32-deadline-test-20260908/result.json),
[curl timings](../../runs/diagnostic/holistic-v32-deadline-test-20260908/curl_timing.json),
[request](../../runs/diagnostic/holistic-v32-deadline-test-20260908/request.json),
[response and usage](../../runs/diagnostic/holistic-v32-deadline-test-20260908/response.json),
[test settings](../../runs/diagnostic/holistic-v32-deadline-test-20260908/test_settings.json).

The focused offline check passed before launch; actual result/timing/token
fields were checked after completion. This result update owns this document
only. No mainline timeout, paging tool, model setting or robot result is changed.

The user explicitly requested a cheap-model check of whether Holistic enforces
a 120-second server limit. Use the existing authenticated route and known
`deepseek.v3.2` model, one non-streaming text request, thinking disabled, 8,192
maximum output tokens, a 300-second client wait and no retries. Curl records DNS,
connection/TLS and first-byte timings separately, using the existing route/client
configuration for endpoint and authentication. The credential is passed through
stdin, never argv or evidence files. No robot, tool or
generated code is executed. This test does not alter the mainline route profile.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/test_holistic_request_deadline.py \
  --output autoadapter/runs/diagnostic/holistic-v32-deadline-test-20260908
```

The short prompt requests enough fictional inspection records to exercise a long
output. The output content has no experimental meaning. At the existing
configured USD 1.85 per million output tokens, the maximum returned output costs
about USD 0.01516, plus the small input cost at USD 0.62 per million. This is a
configured estimate; actual Holistic billing and timeout usage may differ.

Success with the first response byte arriving more than 120 seconds after the
connection was ready contradicts a universal
120-second gateway cutoff for this request/model/path. Success before 120 seconds
is inconclusive about longer waits. An error or timeout also does not establish
a universal limit. A cheap-model result does not establish the behavior of the
Opus backend. No claim will be based on artificially sleeping in the client.

This bounded batch owns `scripts/test_holistic_request_deadline.py`,
`tests/test_holistic_deadline_probe.py` and this document. It is implemented
directly in the current session with read-only review; Luna Max is not used.
Minimum check: one offline regression validates fast-success/failure boundaries,
then execute the single real HTTP request above after committing preparation.
Deliver actual elapsed/header timing, HTTP result, returned tokens, estimate,
request evidence and commit IDs. No thesis, robot standard or formal experiment
is changed.
