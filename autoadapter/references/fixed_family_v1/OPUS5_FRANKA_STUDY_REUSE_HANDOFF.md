# Franka diagnostic: resume from accepted Study

The user requested one robot after the fixed Generate file-input change
(`5995cfe`), explicitly allowing reuse of Study. Use the accepted Franka Study
from `fixed-family-v1-holistic-opus5-franka-20260908`, whose robot metadata,
public scene, capability design and private suite match the current inputs.
The original Study's 13 calls and recorded probes remain historical evidence.
This run makes zero new Study calls and starts a fresh Generate Python session;
it does not restore or replay the old development process. Original Study token
usage is excluded from the new run's usage and cost estimate.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-franka-one-repair.json \
  --holistic --robots franka_panda --retry-timeout-once \
  --study-from autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908
```

Keep Opus 5 through Holistic, adaptive thinking, skeleton-assisted, fixed A1–A5,
ten private cases, Generate 22 / one Repair 22 turns, at most two Harness
submissions, existing execution budgets, 120-second request deadline and one
timeout retry across the client. Run the real basic environment check first.
Do not adjust the robot, standards, model settings or budget after failures.

The small implementation batch owns `scripts/run_fixed_family_diagnostic.py`,
`src/autoadapter2/driver_synthesis/generation.py` (StudyResult metadata),
`src/autoadapter2/pipeline.py` (reuse evidence),
`tests/test_fixed_diagnostic_entry.py` and this handoff. It adds only a
single-robot diagnostic `--study-from` hook. Dynamic runs are unchanged.
No thesis or formal experiment material is in scope. This session implements
the bounded change directly; Luna Max was not used for this batch.

Minimum checks: four focused runner tests, actual source Study loading and
fixed-design validation, diff review, then the real environment/Generate/Harness
path above. Commit preparation before launch. Deliver the run directory,
fresh model calls and returned tokens, generated source and Harness metrics
if reached, actual Repair occurrence, limitations and a separate result commit.
Reused Study and probes must be visibly marked, never counted as fresh calls
or fresh physics. Unknown timeout usage is not treated as zero billing.
