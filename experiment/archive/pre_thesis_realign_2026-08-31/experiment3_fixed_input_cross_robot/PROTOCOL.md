# Experiment 3 execution protocol

> **Protocol revision:** `0.3.0`<br>
> **Authority:** `EXPERIMENT_3_AUTHORITY.md` revision `0.3.0`<br>
> **Current permission:** formal route approved, subject to complete preflight

## 1. Fixed design

The manifest expands to exactly 33 rows in replicate-major order:

```text
r01 x 11 configurations
r02 x 11 configurations
r03 x 11 configurations
```

All rows use Sonnet 4.6, `skeleton-assisted`, empty Experience, at most three
frozen Driver attempts, Task Demo and no Evolution. Report by exact robot
configuration. Do not create reference-seen, control or transfer groups.

The manifest points to `experiment/experiment3/fixed_inputs/index.json`. That
index must contain exactly one robot-specific sealed `capability_design.json`
and private `capability_validation_suite.json` pair for each declared robot.
Each pair is validated before any model client is constructed and is reused
unchanged across that robot's three replicates.
Every design must contain at least five capabilities and every suite exactly two
cases per capability. Report the resulting aggregate case count from the audited
index; do not maintain a second hand-written total.
The paired `smoke_report.json` must also match the indexed input-set ID and
retain one passed real scene/reset/trusted-operator wiring smoke with a finite
measurement for every robot. It is not a Driver or reference-success result.
The paired `calibration_report.json` must match the same ID. Every criterion is
either tied to the protocol's explicit public/tracked source list or to the
matching capability's retained three-repeat real-MuJoCo calibration. The runner
checks its request targets, durations, instance/scene, trusted operator, numeric
tolerance and no-op discrimination before constructing a model client.

## 2. Commands and dispatch gate

From the repository root:

```bash
PYTHONPATH=autoadapter/src python3 -m experiment.experiment3.runner \
  design-check --manifest experiment/experiment3/manifest.json

PYTHONPATH=autoadapter/src python3 -m experiment.experiment3.runner \
  preflight --root autoadapter \
  --manifest experiment/experiment3/manifest.json

PYTHONPATH=autoadapter/src python3 -m experiment.experiment3.runner \
  formal --root autoadapter \
  --manifest experiment/experiment3/manifest.json \
  --output <new-output-directory>
```

`design-check` and `preflight` make no model request. `formal` is authorised
only when the complete preflight passes. It must not construct a client or
start a cell after a failed preflight.

Use `resume` with the same manifest, committed revision and output directory
only after an interrupted/systemically stopped run. It continues untouched
`predeclared` rows; it never reruns terminal rows.

## 3. Formal per-cell recipe

1. Select the robot's fixed pair already loaded and audited by preflight, then
   create a fresh isolated cell workspace, model conversation and MuJoCo reset.
2. Run STUDY with the sealed public design for at most 16 turns and seal
   `study.json`; keep the private suite hidden.
3. Record
   TGCD and IVC as skipped, incomplete and zero-model-call stages.
4. Run Skeleton Generate for at most 22 turns. Freeze `driver.py` only after
   source and import checks.
5. Run the complete fixed Capability Validation suite with trusted measurements
   and complete video evidence.
6. On failure, allow at most two Repairs, each at most 22 turns and each editing
   the prior `driver.py`. Freeze and validate no more than three revisions total.
7. If at least one capability passed both its nominal and calibrated-boundary
   cases, run ReCAP Task Demo with exactly that whitelist. Use at most 16
   planning turns and 12 capability calls per task, persistent credential-free
   MuJoCo execution and the trusted Task Demo Harness verdict.
8. Stop. Do not call Evolution or create Experience review/snapshot output.

A valid artifact written on its final turn is accepted without an extra closing
turn. Missing, invalid, stub or non-importable Driver files do not consume the
three-attempt budget.

## 4. Visibility

- STUDY sees public package inputs, the sealed public design, the skeleton
  condition and empty Experience. It cannot see the private suite.
- Generate/Repair sees STUDY, the sealed public design and its own
  condition-local files. Skeleton files are visible because this experiment has
  only the skeleton-assisted condition.
- The private suite is never copied into a model-facing workspace.
- Candidate validation workers receive only the fixed native request.
  Measurement, criteria, guards, scene mechanics and verdicts remain in the
  trusted Framework parent.
- Repair sees only the immediately prior candidate-facing report.
- ReCAP sees only the public task, public observations and capabilities whose
  two fixed cases passed.
- Candidate and Task Demo workers have no credentials. The Harness and
  reference implementation remain Framework-private.

## 5. Failure and evidence rules

Every row retains model identities, all provider calls/retries and token
categories, phase timing, STUDY and Driver artifacts, fixed-input provenance,
TGCD/IVC zero-call skip evidence, frozen attempts, Capability Validation
reports/videos and Task Demo evidence or a nonempty not-run reason.

Ordinary study, generation, import, capability or task failures remain that
row's result and execution continues. A systemic authentication,
provider-transport or infrastructure failure retains the current row, writes
the run record and stops before untouched rows. No failed row is replaced or
automatically rerun.

## 6. Reporting

Report all 33 row IDs and exact-configuration counts/proportions, attempts,
model calls, tokens, estimated cost and wall time. Morphology labels are
descriptive metadata only. Do not report quadruped transfer, reference-seen
controls, causal morphology, Experience, model or condition effects.
