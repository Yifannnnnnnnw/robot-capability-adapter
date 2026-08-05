# Verified results — 2026-08-05

All commands and test claims in this document refer to
`demo/soarm101_minimal/.venv`; the host Python environment is not used.

## Current local verification

- Python 3.12.13;
- LeRobot 0.6.0 hardware-free API probe: pass;
- MuJoCo 3.11.0 SO-ARM101 model and bridge probes: pass;
- full test collection: 522 tests;
- most recent full test execution: one environment-dependent skip, exit 0;
- deterministic offline end-to-end fixture
  `offline-postfix-closure-20260805a`: `SEALED`, with zero report-schema or
  cross-artifact semantic-audit issues;
- offline fixture Demo: 3/3 visible and 3/3 pilot-held-out;
- offline video index is intentionally empty and is not physical evidence.

The local fixes were exercised by the post-fix AWS source run documented
below. That run reached repeated real MuJoCo direct validation but did not pass
the Validation gate. It is valid research evidence that the protocol ran; it
is **not** evidence that the generated capability passed.

## Model-backed experiment history

These runs are immutable historical evidence. Later source fixes do not alter
their outcomes.

| Run | Generation / suite | Validation and Demo outcome | Video evidence | Terminal result |
|---|---|---|---:|---|
| `aws-research-minimal-20260805b` | Stage 1 3/3, Stage 2 30/30, suite 3/3 | direct 5/5; Demo 5/6 | 5 Validation + 6 Demo = 11 | `INFRASTRUCTURE_FAILED`: terminal-report construction failed after the complete Demo report; not a sealed success |
| `aws-research-minimal-20260805c` | Stage 1 3/3, Stage 2 30/30, suite 3/3 | direct 4/5; repair began, then a provider timeout terminated the second repair epoch | 5 Validation, 0 Demo | `GENERATION_FAILED` |
| `aws-research-minimal-20260805d` | Stage 1 3/3, Stage 2 30/30, suite 3/3; all 10 repair rounds used | final direct 4/5; Demo was never entered | 35 Validation, 0 Demo | `VALIDATION_FAILED` |
| `aws-postfix-evolution-source-20260805b` | source commit `a494207`, tag `soarm101-pre-evolution-v0.1.1`; Stage 1 3/3, Stage 2 30/30, suite 3/3; all 10 repair rounds used | five direct-validation executions, each 2/5; G1/G2 passed and all three G3 cases failed; Demo was correctly not entered | 25 Validation, 0 Demo | `GENERATION_FAILED`: repair round 10 exceeded the bounded request context before request 6 |

In run `...05d`, repair rounds 7, 8, and 9 produced
`no_package_change`; the other seven rounds changed the package. Recording
this distinction matters: consuming a repair budget is not evidence that the
candidate changed, and a no-change round cannot be credited with a later
behavioral improvement.

### Post-fix Evolution source run

Run `aws-postfix-evolution-source-20260805b` is the newly frozen, real-model
source experiment for the next Evolution phase. Its immutable source identity
is commit `a494207`, tagged `soarm101-pre-evolution-v0.1.1`.

- Stage 1 used exactly 3/3 calls, Stage 2 used 30/30, and validation-suite
  generation used 3/3.
- Direct validation executed five cases in rounds 00, 03, 04, 05, and 06.
  Each execution produced five actual MuJoCo task recordings, for 25 MP4s in
  total. Every execution finished without a simulator exception or timeout.
- Every direct-validation execution scored 2/5: G1 and G2 passed, while all
  three G3 cases failed. These repeat failures are useful causal evidence for
  Evolution, but the robot-specific capability must not be called validated.
- All ten repair rounds were consumed. Rounds 1, 2, 7, 8, and 9 recorded
  `no_package_change`; rounds 3 through 6 recorded `package_changed`. Round 10
  ended before its sixth provider request when the local bounded context check
  raised `RequestContextLimitExceeded` (`288426 > 262144`), so it produced no
  accepted replacement package.
- Accounting closed with 95 logical attempts and 95 HTTP attempts: 95 provider
  successes, zero failed provider requests, and zero retries. The round-10
  context rejection happened locally before another HTTP request.
- The terminal status is `GENERATION_FAILED`. Its terminal report passed both
  schema and semantic audit with zero issues.
- Frozen-input verification covers 80 fixed files and 31 source records, with
  zero mismatches; the recorded source-tree digest matches the source run.
- Demo was not entered because the generated capability never passed the
  Validation gate. This is expected protocol behavior, not missing Demo
  evidence that can be inferred as a pass.

The current deterministic Evolution Evidence Compiler refused to verify this
run's 598,979-byte terminal report as a source report: it classified the input
as oversized, set `source_report_verified` to false, and emitted zero
candidates. This preserves the old evidence rather than truncating or silently
accepting it. Supporting a valid large terminal report is an input-adaptation
problem for the next Evolution goal; the historical report must not be edited
to work around it.

The retained MP4 counts above come from each run's `video_index.json`. They are
formal, actual-MuJoCo Validation/Demo recordings rather than renderer
preflight clips. A `partial` index means the run did not seal the complete
evidence set; it does not imply that every retained and fully decoded MP4 is
corrupt.

## Changes exercised by the post-fix AWS run

The current source contains regression-tested changes for problems exposed by
those runs:

- Validation A no longer treats a post-close release command as the earliest
  tool-centred G3 boundary;
- repair ledger v2 carries a bounded, privacy-safe process audit into the next
  repair epoch, including writes, protocol rejections, hashes, and unfinished
  reads without raw model/source content;
- oversized schema failures report lengths, hashes, and constraints instead
  of echoing a large generated value;
- G3 results expose explicit phase and sequence-progress fields;
- direct-validation evidence names SDK degree residuals and separates host
  wall-clock time from MuJoCo simulation time;
- validation-suite contract v2 defines the host-monotonic harness deadline,
  preserves public simulation-time semantics, and exercises optional timeout
  defaults rather than overriding them in nominal physical cases.

The post-fix run proves that the newly frozen inputs, continuous Generation
flow, repair accounting, repeated real MuJoCo validation, videos, and terminal
audits execute together. It does not prove that the fixes make the generated
G3 implementations behaviorally correct, because all three G3 cases still
failed and the run never crossed the Validation gate.

## What the evidence proves

- the four-library visibility boundary and deterministic input hashing;
- one continuous Generation Agent across Stage 1, Stage 2, and independently
  budgeted repair epochs;
- exact three-call validation-suite generation followed by code-executed
  direct validation;
- fresh, task-local MuJoCo worlds resolved just in time from the frozen
  Morphology scene catalog;
- actual Validation and Demo video capture and binding;
- a separate Demo Consumer identity/session/tool/trace boundary;
- terminal budget/accounting preservation on failed runs;
- local regression coverage for the post-run fixes listed above;
- a reproducible post-fix AWS failure run whose complete direct-validation
  evidence can be used by the next Evolution experiment.

## What remains unproven

- no model-backed run has yet sealed the entire current
  Generation → Validation → six-task Demo protocol;
- the post-fix source has not produced a capability that passes all five direct
  cases; its fresh replay terminated before Demo after repeated 2/5 results;
- `pilot-held-out` remains a pilot partition, not a formal generalization
  claim;
- simulation results do not establish real-robot performance;
- the global-observer ReAct Evolution Agent, trusted replay evaluator, and
  automatic versioned Experience publisher are designed but not yet
  implemented or executed.

The currently implemented `evolution.py` should therefore be interpreted only
as a deterministic, privacy-filtering post-run Evidence Compiler. Its
candidate bundle is useful audit input; it is not evidence that an Evolution
patch or Experience publication has occurred.
