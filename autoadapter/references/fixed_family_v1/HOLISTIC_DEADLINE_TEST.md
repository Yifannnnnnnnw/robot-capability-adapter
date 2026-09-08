# Cheap-model test of the gateway's 120-second boundary

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
