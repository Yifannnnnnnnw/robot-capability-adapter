# SO-101 retry with preserved interruption evidence

The user authorized another single SO-101 diagnostic after the original Study
request timed out. Use a fresh run; the original missing conversation and closed
MuJoCo worker cannot be recovered. The other ten robots remain undispatched.

## Conditions and invocation

Keep the existing Opus 5 SO-101 configuration: Holistic, adaptive thinking,
skeleton-assisted, fixed capability inputs, Study 16 / Generate 22 / one Repair
22 turns, at most two Harness submissions, and the original physics budgets,
instances and thresholds. The request deadline remains 120 seconds. This is a
diagnostic and does not modify the thesis experiment or historical results.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-so101-one-repair.json \
  --holistic --robots robotstudio_so101 --retry-timeout-once \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908
```

The flag permits one timeout retry across all stages of this robot's client.
Each logical request still permits at most two physical HTTP sends, including
existing transient HTTP retries. The retry resends the same projected body while
the same development worker and workspace remain alive. It does not rerun tools
or consume a Harness submission or Repair. Every send has its own usage record;
missing timeout usage remains unknown. HTTP 401/402 remain terminal.

## Reviewable implementation batches

1. **HTTP evidence and timeout retry — `7f1cf0a`, `1d39b2f`.** Owned files:
   `src/autoadapter2/model_api.py`, `scripts/run_fixed_family_diagnostic.py`,
   `tests/test_model_api.py`. Request bodies are written before sending, followed
   by response phase/timing, response text and final call metadata. Authentication
   headers are excluded and the configured credential is redacted. Selected
   response request/trace IDs are preserved for gateway log lookup. The runner
   keeps this evidence at `<robot>/model_requests/`, outside the public workspace.
   Eight focused model checks and nine subtests passed; three entry checks passed.
2. **Failed Study evidence.** Owned files: `src/autoadapter2/react.py`,
   `driver_synthesis/generation.py`, `driver_synthesis/repair.py`, `pipeline.py`,
   and `tests/test_interrupted_study_evidence.py`. Preserve the original model
   exception and the completed conversation, tools and probes; write failure
   evidence before cleanup. A successful earlier probe must remain visible
   without accepting an unfinished Study. Luna Max implemented this bounded batch
   in a sub-session; root reviewed it before the real run. The focused interrupted
   Study check plus existing fixed-prompt and entry checks passed (five checks,
   six subtests).

Minimum acceptance: a scripted API interruption after a completed tool leaves
on-disk Study evidence and no accepted Study or Harness call; timeout retry
preserves request bytes, has a finite shared allowance and never retries 401/402;
the real SO-101 diagnostic then exercises the normal model and MuJoCo path.
Forbidden scope: robot/controller changes, private standard changes, extra model
turns or Repair phases, new runner/framework, formal experiment or thesis edits.

## Interpretation and deliverable

`awaiting_response_headers` means the client has not received HTTP headers. It
cannot distinguish connection problems, gateway queueing or upstream inference.
`reading_response_body` establishes that headers arrived before interruption.
No client record alone proves an upstream root cause when no response is returned.

Return the new run directory, actual completed stages, Harness verdicts/videos
when reached, whether timeout retry was exercised, returned tokens and reference
cost with unknown charges identified, remaining blockers, and batch commit IDs.
Do not launch additional robots as part of this single-robot retry.
