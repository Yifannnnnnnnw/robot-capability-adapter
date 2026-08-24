# Experiment 3 execution protocol

> **Protocol revision:** `0.2.0`<br>
> **Authority:** `EXPERIMENT_3_AUTHORITY.md` revision `0.2.0`<br>
> **Current permission:** design and zero-model preflight only

## 1. Fixed design

The manifest must expand to exactly 33 rows in replicate-major order:

```text
r01 × 11 configurations
r02 × 11 configurations
r03 × 11 configurations
```

All rows use Sonnet 4.6, `skeleton-assisted`, empty Experience, fresh phase
artifacts and no Evolution. SO-101 and Go2 contribute six `reference-seen
control` rows; the other configurations contribute 27 `transfer` rows. These
are descriptive exposure groups, not an effect estimate.

## 2. Allowed preparation commands

From the repository root:

```bash
PYTHONPATH=autoadapter/src python3 -m experiment.experiment3.runner \
  design-check --manifest experiment/experiment3/manifest.json

PYTHONPATH=autoadapter/src python3 -m experiment.experiment3.runner \
  preflight --root autoadapter \
  --manifest experiment/experiment3/manifest.json
```

Neither command constructs a model client or sends a model request. The first
checks the fixed matrix and protocol pins. The second also loads all eleven
packages, checks public observations and validates retained reference and
Framework evidence.

Do not invoke `formal` or `resume` in this preparation round. A later explicit
project-owner approval is required before either command may create a model
client.

## 3. Formal per-cell recipe for the later approved run

For each predeclared row, use a new workspace and a new unified-model-call
client. Run:

1. STUDY, at most 16 model turns, sealing `study.json`.
2. TGCD, at most 6 turns, sealing `capability_design.json` with 3–10
   capabilities and a many-to-many `task_support` relation.
3. IVC, at most 6 turns, sealing
   `capability_validation_suite.json` only after schema/count/binding audit
   and a real private reference-driver positive control.
4. Skeleton Generate, at most 22 turns, freezing `driver.py` only after source
   and import checks.
5. Trusted Capability Validation with complete video evidence.
6. On failure, at most two Repairs, each at most 22 turns and each editing the
   prior `driver.py`; freeze and validate no more than three Driver revisions
   total.
7. ReCAP Task Demo for an admitted final Driver, with at most 16 planning turns
   and 12 validated-capability calls per task, persistent credential-free
   MuJoCo execution and a trusted Harness verdict.
8. Stop. Do not call Evolution or write an Experience review queue/snapshot.

A phase artifact written validly on its final turn is accepted without an
extra completion turn. Missing, invalid, stub or non-importable files do not
consume the three-attempt budget. There is no phase-wide tool-call ceiling;
individual tools retain their path, timeout, simulation-step and output limits.

## 4. Visibility checks

- STUDY sees public package inputs, its condition and empty Experience.
- TGCD sees the completed public STUDY artifact, public package/task inputs,
  empty Experience and only the SO-101/Go2 capability reference.
- IVC sees no Experience or candidate material. It receives sealed design and
  private instances/bindings/guards plus sanitised examples.
- Generate/Repair sees the sealed public design and its own condition-local
  files. Skeleton files are visible only in this experiment's skeleton
  condition.
- Repair sees only the immediately prior candidate-facing report; private
  definitions remain redacted.
- ReCAP receives only the public task, public observations and capability tools
  whose nominal and boundary validation both passed.
- Candidate/Task Demo workers have no credentials. The Harness and reference
  implementation remain Framework-private.

## 5. Required row evidence

Every formal row must retain:

- exact cell, package/version, replicate, model/provider and returned-model IDs;
- STUDY/TGCD/IVC artifacts and model/tool traces;
- source/import audit and frozen Driver-attempt accounting;
- initial and final Capability Validation outcomes and Repair transitions;
- ReCAP calls and trusted Task Demo outcome or a nonempty not-run reason;
- required complete per-trial video manifests; and
- token categories, model calls, transport attempts and wall time.

Failures remain named rows in denominator 33. Do not retry a completed/failed
formal row, substitute a package/reference, add compensating rows or relabel an
infrastructure failure as model success/failure.

## 6. Reporting

Report all 33 row IDs. Show the six reference-seen controls separately from the
27 transfer cells and report exact-configuration counts/proportions plus
attempt and resource summaries. Morphology labels are descriptive only. Do not
report quadruped transfer, causal morphology, Experience, model or condition
effects.
