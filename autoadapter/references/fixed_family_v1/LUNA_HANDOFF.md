# Separate Luna Max implementation handoff: Franka reference calibration

This is a prompt for a separate user-started Luna Max conversation. Luna Max did
not implement or review the current batch.

## Outcome

Resolve the observed Franka fixed-reference A4 boundary and A5 boundary failures
without changing the approved pass criteria. The latest baseline is 8/10 reference
trials in `runs/diagnostic/fixed-family-v1-arm-controls-20260908/franka_panda/reference/`.
Read the actual per-trial report and physical evidence first; do not infer the
cause from the capability name. This task does not include a new model synthesis.

## Owned files

- `autoadapter/libraries/robots/franka_panda/1.0.0/reference/fixed_family_driver.py`
- `autoadapter/references/fixed_family_v1/FRANKA_CALIBRATION_HANDOFF.md` (new concise evidence note)

Use a fresh directory under `autoadapter/runs/diagnostic/` for run artifacts. Do
not edit assets, fixtures, criteria, other robots, generated candidate drivers,
the Harness, pipeline, thesis, experiment archive, model configuration or budgets.
If fixing the reference requires another owned file, report the specific reason
to the reviewer rather than silently expanding scope.

## Interfaces and boundaries

Follow root `AGENTS.md` and the user-approved fixed-family diagnostic in this
directory's README. The relevant framework description is the Framework-owned
canonical session and trusted evaluation/feedback boundary in
`thesis/C_03_framework.tex`. This work supplies diagnostic calibration only; it
does not change thesis Experiment 2a or Experiment 3 claims.

The reference must expose `build(model, data)` and the exact five `(self, request)`
capability methods. Keep motion actuator-only through the supplied canonical
MuJoCo objects and `mj_step`. No direct qpos/qvel edits, reset, private criterion
inspection, replay of a passed trajectory or weakening of side-effect/contact
checks. Preserve A1/A2/A3 nominal and boundary behavior.

## Minimum check and handoff

Run one full real reference suite with videos:

```sh
cd autoadapter
PYTHONPATH=src python scripts/run_fixed_family_diagnostic.py --robots franka_panda --reference-only
```

Inspect all ten trusted trial verdicts, contact integrity, guards and videos. A
completed process or criterion-only success is insufficient. Stop after the
reference reaches 10/10, or return the concrete remaining physical limitation;
do not start policy training, broad tests or other robot work.

Return changed files, the before/after failed-trial measurements, run path,
limitations, and commit ID. Stage only owned files, inspect the staged diff, and
make a new commit after the focused check. Final acceptance belongs to the
architect/reviewer.
