# Isolated Study-result continuation test

Completed: [real test result](STUDY_CONTINUATION_TEST_RESULTS.md). Preparation
commit `9f14450`; Study reuse and Generate scene loading worked, but the next
request and retry timed out before Driver/Harness/Repair.

The user requested a separate test script to continue from an existing Study
result through Generate, Harness and at most one Repair. The script
`scripts/test_study_continuation.py` calls the existing fixed diagnostic runner;
it does not implement another synthesis or validation loop. `--study-from` is
required. The accepted original Franka Study and historical probes are reused,
with zero new Study calls and a fresh Generate development session.

This test changes only native history retention: all groups can remain until
the existing 80,000-character budget requires summarization. The default mainline
context manager remains unchanged. The test records its retention policy in
request metadata and `continuation_test_settings.json`. Holistic Opus 5 adaptive,
Generate 22 / one Repair 22 turns, two maximum Harness submissions, 120-second
request timeout, one shared timeout retry, and existing physics budgets/criteria
remain unchanged. No other robot is dispatched.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/test_study_continuation.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-franka-one-repair.json \
  --holistic --robots franka_panda --retry-timeout-once \
  --study-from autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908
```

The small batch owns this file, the new test script, the existing runner's
optional `argv`/`client_factory` entry parameters, and
`tests/test_study_continuation_context.py`. No model, robot, Harness, thesis,
formal experiment or historical result is edited. This session implements the
bounded test directly with read-only review; Luna Max is not used.

Minimum checks: five focused tests across `test_study_continuation_context.py`
and `test_fixed_diagnostic_entry.py` pass. The four-page fixture reproduces lost
content with the old cutoff, retains it in the test under budget, and still
prunes over budget. The actual previous Generate conversation was replayed
without API calls: at 10 / 12 messages, retained groups increase from 3 to 4 / 5;
at 14 / 16 messages the budget still summarizes 1 / 2 groups. Every replay stays
within 80,000 characters. Evidence:
`runs/diagnostic/study-continuation-context-replay-20260908/check_result.json`.

Commit preparation before launch, then run the exact real path above. Deliver
actual completed stages, Driver/Harness/video evidence if reached, new tokens
and unknown timeout usage, remaining limitations, changed files and commit IDs.
Do not claim a Harness score before execution, or Repair without real failure
feedback and a later model edit. The test does not promise to eliminate upstream
timeouts or preserve unbounded file history.
