# Verified results — 2026-08-05

All commands and test claims in this document refer to
`demo/soarm101_minimal/.venv`; the host Python environment is not used.

## Current local verification

- Python 3.12.13;
- LeRobot 0.6.0 hardware-free API probe: pass;
- MuJoCo 3.11.0 SO-ARM101 model and bridge probes: pass;
- full test collection: 555 tests;
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

The deterministic Evolution Evidence Compiler now reads this complete
598,979-byte terminal report under a 1 MiB default limit and 2 MiB hard ceiling.
It verifies the original exact SHA and run-manifest/schema binding without
editing the historical report, reports zero invalid/oversized and zero unbound
source artifacts, and derives five redacted `confirmed_failure` candidates.
The older run-local candidate bundle created under the former 512 KiB limit is
not treated as authority and is not overwritten; each Evolution run recompiles
the source evidence in memory.

The retained MP4 counts above come from each run's `video_index.json`. They are
formal, actual-MuJoCo Validation/Demo recordings rather than renderer
preflight clips. A `partial` index means the run did not seal the complete
evidence set; it does not imply that every retained and fully decoded MP4 is
corrupt.

## Read-only Evolution experiment

The finalized Evolution architecture is
Evidence-grounded Audit–Synthesize–Judge–Publish. Its ReAct inspection and
synthesis windows are one Agent identity/session/history with a single 30-call
ceiling: at most 20 calls for global read-only inspection and the remaining 10
for schema-valid synthesis. The synthesis phase disables repository browsing;
it retains only privacy-safe evidence reads and audit submission.

Development runs were retained rather than overwritten:

| Evolution run | Real model outcome | Trusted result | Change motivated |
|---|---:|---|---|
| `aws-evolution-readonly-20260805a` | 0 successful responses; 30 sandbox transport failures | `inconclusive`, no publication | rerun with permitted network access |
| `aws-evolution-readonly-20260805b` | 30/30 responses | `rejected`: selected claim exceeded evidence and used refs outside the selected finding | expose exact schema and align claim/evidence contract |
| `aws-evolution-readonly-postfix-20260805a` | 30/30 responses | `inconclusive`: Agent kept browsing and submitted no audit | enforce 20-call audit + 10-call synthesis phases |
| `aws-evolution-readonly-phased-20260805a` | 22/22 responses | `rejected`: wrong robot scope and negative claim lacked a confirmed-failure candidate | move existing evaluator rules into submit preflight/schema |
| `aws-evolution-readonly-final-20260805a` | 22/22 responses | `accepted`; one negative claim published, then conservatively version-corrected after trusted-boundary audit | final model-backed result |

The final run used model
`anthropic.claude-sonnet-4-5-20250929-v1:0`, one independent Agent session, 22
logical requests, 22 HTTP attempts, zero retries, 290,496 reported input tokens,
and 4,729 reported output tokens. The endpoint did not report cost/quota fields
for these responses, so no dollar-cost claim is made.

The Agent ranked five findings and selected
`g3_oracle_mismatch_all_candidates`: G1/G2 passed consistently, while all three
G3 capabilities failed five direct-validation executions and five compiler
candidates carried `confirmed_failure`. The Agent proposed tool-point/site-frame
transformation or object-interaction logic as a mechanism hypothesis, while
retaining timeout, gripper/contact, calibration, and kinematics-transcription
alternatives. Evolution did not apply the proposed prompt change.

The trusted evaluator independently recompiled the unchanged 598,979-byte
source report and passed all eight gates: source integrity, framework snapshot,
audit schema, audit semantics, evidence binding, claim strength, privacy, and
Experience schema. It published:

```text
experience_id: evolution.b9ee1c81d3c1c516c075000e
version: 1.0.0
status: approved
conclusion_kind: negative
```

Here `approved` means safe and sufficiently grounded for publication; it does
not make the result positive. The record states
`framework_change_applied=false`, `capability_improvement_proven=false`, and
`new_generation_validation_required=true`.

A post-publication code audit then found that the negative execution fact was
supported, but the specific mechanism text was stronger than the evidence: the
Stage 2 prompt already requested the cited site-quaternion composition, and the
generated kinematics implementation contained that composition. Historical
run artifacts and the original `1.0.0` ledger line were not rewritten. The
hardened trusted compiler re-ran the same accepted audit and appended `1.1.0`
for the same Experience identity. It preserves the repeated G3 failure fact,
sets `repair_succeeded=null`, and states that the exact mechanism and remedy
remain unproven. Latest-version selection exposes only `1.1.0` to Generation.

The hardening also binds publication to the evaluator-approved record hash,
rejects symlinks throughout the Experience authority path, makes the 64 KiB
audit limit a real gate, freezes the first valid submission, reuses the full
terminal/video semantic audit, sanitizes public source lines and labels their
source-run hash status, and treats publication failure as process-inconclusive
rather than scientific rejection. The complete accepted-report schema and
cross-field semantics are now checked before the shared Experience ledger is
mutated. Existing ledger bytes, record count, canonical JSONL form, required
payload hashes, schema, and latest-version semantics are all verified before
either an append or an idempotent response.

An independent post-publication probe verified the Experience library schema,
two immutable raw versions, manifest count/hash, and strict latest-version
projection. A newly created run materialized exactly one non-empty
`selected_records.json` at version `1.1.0`; it contained no raw origin, artifact
refs, before/after measurements, ambiguous repair outcome, private task, or
Oracle payload. The source run itself was unchanged and cannot consume its own
record.

The deterministic correction is recorded in
[`EVOLUTION_CORRECTION_RECEIPT.json`](EVOLUTION_CORRECTION_RECEIPT.json). It
hash-binds the immutable historical report and audit, the complete source
terminal report, the eight-gate 1.1.0 re-evaluation, the published record,
ledger, manifest, and sole Generation view. Replaying this correction uses zero
additional model calls. The historical AWS report remains byte-for-byte
unchanged even though it predates the hardened report schema.

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
  evidence was consumed by the reported Evolution experiment;
- the read-only global Evolution Agent, deterministic trusted evaluator, and
  versioned Experience publisher completed the accepted real-model run
  reported above, and a later Generation run can load its sanitized record.

## What remains unproven

- no model-backed run has yet sealed the entire current
  Generation → Validation → six-task Demo protocol;
- the post-fix source has not produced a capability that passes all five direct
  cases; its fresh replay terminated before Demo after repeated 2/5 results;
- `pilot-held-out` remains a pilot partition, not a formal generalization
  claim;
- simulation results do not establish real-robot performance.

Evolution acceptance is an Experience-claim result only. It does not mean that
a framework patch occurred—the Agent is read-only—or that robot capability
improved. Any recommended change must be tested by a later fresh
Generation/Validation experiment.
