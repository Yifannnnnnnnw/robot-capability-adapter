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
- Ten reference positive controls pass. Only the SO101 `mw_pick_place`
  reference control fails. Therefore no Experiment 2 or Experiment 3 formal
  run has started.

The checked-in preflight is intentionally blocked by exactly this retained
fact:

```text
Experiment 2: readiness_evidence.reference_positive_control has not passed
Experiment 3: readiness_evidence.reference_positive_controls.robotstudio_so101 has not passed
```

Do not bypass the gate, weaken the `0.07 m` task criterion, weaken the
`0.005 m` integrity guard, substitute a model, or remove SO101 from the
denominator.

## Close the one remaining gate

The SO101-owning workstream should calibrate grasp alignment/two-jaw engagement
and rerun only:

```sh
cd autoadapter
pyenv exec python -m pytest -q \
  tests/test_so101_package.py::test_so101_pick_place_uses_a_physical_fixture_and_reference_passes
```

Do not rerun the other ten reference controls. If and only if this focused test
passes, retain a new JSON evidence file with at least this semantic identity:

```json
{
  "artifact_type": "experiment3_reference_positive_control",
  "robot_configuration_id": "robotstudio_so101",
  "passed": true
}
```

The artifact may add the focused command, physical verdict, guard, metric, and
video details; it must not omit or contradict the fields above. Then update
these two manifest entries to point to it with `"passed": true`:

- Experiment 2: `readiness_evidence.reference_positive_control`
- Experiment 3:
  `readiness_evidence.reference_positive_controls.robotstudio_so101`

Keep the 2026-08-23 failed evidence unchanged as failure history.

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

After the new SO101 pass evidence is connected, run:

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
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  source \
  --root autoadapter \
  --manifest experiment/experiment2/manifest.json \
  --output autoadapter/runs/experiment2/exp2-so101-source \
  --manual-event operator-source-launch-2026-08-24 \
  --env-file .env \
  --env-file .env.company-api
```

Inspect the retained terminal report and unedited proposal. Then record exactly
one human decision with a nonempty reason:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  review \
  --source-output autoadapter/runs/experiment2/exp2-so101-source \
  --disposition accept \
  --reason "REPLACE WITH THE REVIEWER'S NONEMPTY REASON"
```

Use `reject` instead when that is the truthful decision. A rejection is retained
and blocks the Experience-enabled closure; do not select a different source run.

Only after an acceptance and frozen snapshot, manually launch the independent
later run:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment2.runner \
  later \
  --root autoadapter \
  --manifest experiment/experiment2/manifest.json \
  --snapshot autoadapter/runs/experiment2/exp2-so101-source/experience_snapshot.json \
  --output autoadapter/runs/experiment2/exp2-so101-later \
  --manual-event operator-later-launch-2026-08-24 \
  --env-file .env \
  --env-file .env.company-api
```

The later report must retain the exact Experience ID in the load event, TGCD
trace, and GENERATE trace. It must contain no Evolution call or review queue.

## Experiment 3 — one formal command

After preflight passes, the following single command predeclares and executes
the fixed 33-cell denominator. Use a new output directory; never reuse a failed
or partial directory.

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  formal \
  --root autoadapter \
  --manifest experiment/experiment3/manifest.json \
  --output autoadapter/runs/experiment3/formal-2026-08-24 \
  --env-file .env \
  --env-file .env.company-api
```

Each declared cell is attempted once. A model, package, or Harness failure is
retained at its original robot/replicate row; it is not retried as a replacement
cell and does not shrink the denominator.

After completion, produce the descriptive summary:

```sh
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  summarise \
  --record autoadapter/runs/experiment3/formal-2026-08-24/experiment3_run_record.json \
  --output autoadapter/runs/experiment3/formal-2026-08-24/summary.json
```

Report per-configuration counts/proportions and median/range only. Morphology is
descriptive grouping; do not report a causal morphology effect or an Experience
improvement claim.
