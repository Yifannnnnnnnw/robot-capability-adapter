# Verified results — 2026-08-05

All commands and test claims in this document refer to
`demo/soarm101_minimal/.venv`; the host Python environment is not used.

## Current local verification

- Python 3.12.13;
- LeRobot 0.6.0 hardware-free API probe: pass;
- MuJoCo 3.11.0 SO-ARM101 model and bridge probes: pass;
- full test collection: 522 tests;
- most recent full test execution: one environment-dependent skip, exit 0;
- deterministic offline end-to-end fixture: `SEALED`;
- offline fixture Demo: 3/3 visible and 3/3 pilot-held-out;
- offline video index is intentionally empty and is not physical evidence.

The local fixes after the latest AWS experiment are unit/regression verified.
They have **not** yet been proven by a new AWS replay, so the current source
must not be described as having passed the model-backed end-to-end experiment.

## Model-backed experiment history

These runs are immutable historical evidence. Later source fixes do not alter
their outcomes.

| Run | Generation / suite | Validation and Demo outcome | Video evidence | Terminal result |
|---|---|---|---:|---|
| `aws-research-minimal-20260805b` | Stage 1 3/3, Stage 2 30/30, suite 3/3 | direct 5/5; Demo 5/6 | 5 Validation + 6 Demo = 11 | `INFRASTRUCTURE_FAILED`: terminal-report construction failed after the complete Demo report; not a sealed success |
| `aws-research-minimal-20260805c` | Stage 1 3/3, Stage 2 30/30, suite 3/3 | direct 4/5; repair began, then a provider timeout terminated the second repair epoch | 5 Validation, 0 Demo | `GENERATION_FAILED` |
| `aws-research-minimal-20260805d` | Stage 1 3/3, Stage 2 30/30, suite 3/3; all 10 repair rounds used | final direct 4/5; Demo was never entered | 35 Validation, 0 Demo | `VALIDATION_FAILED` |

In run `...05d`, repair rounds 7, 8, and 9 produced
`no_package_change`; the other seven rounds changed the package. Recording
this distinction matters: consuming a repair budget is not evidence that the
candidate changed, and a no-change round cannot be credited with a later
behavioral improvement.

The retained MP4 counts above come from each run's `video_index.json`. They are
formal, actual-MuJoCo Validation/Demo recordings rather than renderer
preflight clips. A `partial` index means the run did not seal the complete
evidence set; it does not imply that every retained and fully decoded MP4 is
corrupt.

## Changes made after the last AWS run

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

These changes are proven by local tests only. A clean AWS run from newly
frozen inputs is the outstanding evidence needed to show that they improve the
end-to-end Generation → Validation → Demo path.

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
- local regression coverage for the post-run fixes listed above.

## What remains unproven

- the current post-fix source has not completed a fresh AWS end-to-end replay;
- no model-backed run has yet sealed the entire current
  Generation → Validation → six-task Demo protocol;
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
