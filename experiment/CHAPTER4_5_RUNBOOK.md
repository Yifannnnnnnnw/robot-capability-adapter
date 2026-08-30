# Chapter 4–5 preflight and launch runbook

This runbook covers Experiment 2 (Chapter 4 cross-run closure) and Experiment
3 (Chapter 5 cross-configuration evaluation). It does not change or launch
Experiment 1.

## Current retained state — 2026-08-23

- Both prospective design checks pass.
- Experiment 3 expands to exactly 33 cells: 11 declared configurations crossed
  with `r01`–`r03`, Sonnet 4.6, skeleton-assisted only, empty Experience, and
  Evolution disabled.
- The current all-eleven package check passes.
- Exact company-API canaries pass for
  `eu.anthropic.claude-sonnet-4-6` and `eu.anthropic.claude-opus-5`.
- A real company-API Opus terminal-Evolution contract canary returns the exact
  five model-authored public fields; the Framework supplies the wrapper and
  lineage labels.
- The exact Experiment 3 Sonnet request pin passes with temperature `0.0`,
  1,000,000-token context, and a 16,384-token output ceiling.
- A real Franka/Harness recorder canary passes and retains a decodable 800 ×
  600 MP4.
- Focused Framework/Harness checks pass for candidate isolation, canonical
  physics, independent verdicting, and required-video invalidation. The
  manifests validate each named role against that evidence.
- All eleven reference positive controls pass, including the focused SO101
  `mw_pick_place` control. Both executable preflights pass. Neither a valid
  Experiment 2 closure result nor an Experiment 3 cell has entered its
  declared denominator.

One pre-fix Experiment 2 source dispatch is retained at
`autoadapter/runs/experiment2/exp2-so101-source`. Its probe deadlock and
`indeterminate` Framework label make it immutable infrastructure-failure
evidence, not the valid source role; it cannot be reviewed or supplied to the
later run. The single authorised corrected source uses the distinct
`exp2-so101-source-v2` identity and has not started.

The earlier 10/11 status and SO101 failure record remain truthful historical
diagnostics. The later focused SO101 pass is the current readiness evidence;
do not edit the old record and do not rerun SO101 or the other ten controls.
The retained model canaries remain the declared provider evidence, so this
runbook does not add another model canary before formal dispatch.

Formal dispatch must use the final committed revision. Experiment 2 rejects
dirty scoped inputs and records the Git commit plus fixed Authority, manifest,
and protocol revisions in each source/later report and cell row; the accepted
snapshot retains source lineage. Experiment 3 records the same revision fields
in the experiment record and every cell row.

## Offline design checks

Run from the repository root:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  design-check --manifest experiment/experiment2/manifest.json

PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  design-check --manifest experiment/experiment3/manifest.json
```

Expected results are `ok: true`, with two declared Experiment 2 run roles and
exactly 33 Experiment 3 cells.

## Executable preflight

From the final committed revision, run:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  preflight --root autoadapter --manifest experiment/experiment2/manifest.json

PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  preflight --root autoadapter --manifest experiment/experiment3/manifest.json
```

Both commands must return `ok: true`. Experiment 3 preflight performs a current
package check for the exact eleven configurations; it does not rerun the ten
already-passing reference controls. Stop if either preflight fails.

## Experiment 2 — deliberately separated commands

The source run, human disposition, and later run must never be auto-chained.
Use a new or empty output directory for each real run.

Source run:

```sh
env -u AUTOADAPTER_HOLISTICAI_API_KEY \
  PYTHONPATH=autoadapter/src MUJOCO_GL=cgl \
  pyenv exec python -m experiment.experiment2.runner \
  source \
  --root autoadapter \
  --manifest experiment/experiment2/manifest.json \
  --output autoadapter/runs/experiment2/exp2-so101-source-v2 \
  --manual-event operator-manual-launch-exp2-source-v2 \
  --env-file .env.holisticai-api
```

Inspect the retained terminal report and unedited proposal. A review command is
legal only if the runner created `experience_review_queue.json`; an
`indeterminate` outcome or explicit no-reusable-lesson result retains the Opus
record without a queue and stops the closure. Otherwise record exactly one
human decision with a nonempty reason:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  review \
  --source-output autoadapter/runs/experiment2/exp2-so101-source-v2 \
  --disposition accept \
  --reason "REPLACE WITH THE REVIEWER'S NONEMPTY REASON"
```

Use `reject` instead when that is the truthful decision. A rejection is retained
and blocks the Experience-enabled closure; do not select a different source run.

Only after an acceptance and frozen snapshot, manually launch the independent
later run:

```sh
env -u AUTOADAPTER_HOLISTICAI_API_KEY \
  PYTHONPATH=autoadapter/src MUJOCO_GL=cgl \
  pyenv exec python -m experiment.experiment2.runner \
  later \
  --root autoadapter \
  --manifest experiment/experiment2/manifest.json \
  --snapshot autoadapter/runs/experiment2/exp2-so101-source-v2/experience_snapshot.json \
  --output autoadapter/runs/experiment2/exp2-so101-later \
  --manual-event operator-manual-launch-exp2-later \
  --env-file .env.holisticai-api
```

The later report must retain the exact Experience ID in the load event, TGCD
trace, and GENERATE trace. It must contain no Evolution call or review queue.

## Experiment 3 — one formal command

After preflight passes, the following single command predeclares and executes
the fixed 33-cell denominator. The initial `formal` command must use a new
output directory. Only the `resume` command below may reuse that same partial
directory after a process interruption.

```sh
env -u AUTOADAPTER_HOLISTICAI_API_KEY \
  PYTHONPATH=autoadapter/src MUJOCO_GL=cgl \
  pyenv exec python -m experiment.experiment3.runner \
  formal \
  --root autoadapter \
  --manifest experiment/experiment3/manifest.json \
  --output autoadapter/runs/experiment3/experiment3-direct-mujoco-cohort-r3 \
  --env-file .env.holisticai-api
```

Each declared cell is attempted once. A model, package, or Harness failure is
retained at its original robot/replicate row; it is not retried as a replacement
cell and does not shrink the denominator.

If the operator, host, or Python process interrupts the command, resume the same
record with the same committed revision:

```sh
env -u AUTOADAPTER_HOLISTICAI_API_KEY \
  PYTHONPATH=autoadapter/src MUJOCO_GL=cgl \
  pyenv exec python -m experiment.experiment3.runner \
  resume \
  --root autoadapter \
  --manifest experiment/experiment3/manifest.json \
  --output autoadapter/runs/experiment3/experiment3-direct-mujoco-cohort-r3 \
  --env-file .env.holisticai-api
```

Resume never calls a model for `completed` or `failed` rows. A predeclared row
with an existing partial workspace becomes an interrupted infrastructure
failure and is not rerun; only untouched predeclared rows continue.

After completion, produce the descriptive summary:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  summarise \
  --record autoadapter/runs/experiment3/experiment3-direct-mujoco-cohort-r3/experiment3_run_record.json \
  --output autoadapter/runs/experiment3/experiment3-direct-mujoco-cohort-r3/summary.json
```

Report per-configuration counts/proportions and median/range only. Morphology is
descriptive grouping; do not report a causal morphology effect or an Experience
improvement claim.
