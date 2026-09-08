# Opus failed-request replay with a 300-second client deadline

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
