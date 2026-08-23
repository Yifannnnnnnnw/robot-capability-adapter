# Experiment 1b — B2 use

This is the active B2 ReCAP-use workspace.  Its design authority is
`B2_RECAP_AUTHORITY.md`; configuration, fixed reference validation,
diagnostics, formal runtime, tests, and local run outputs are grouped below
this directory.  The formal manifest is `manifest.json`; its zero-model
readiness gate is enabled with no blockers, but no formal episode is launched
by that configuration change.

The `runs/` directory is an active local-output placeholder.  Historical B2
runs are under `../archive/runs/b2_recap/` and are resolved read-only by
`../path_layout.py` when an old path is missing.

The first seven-unit batch has one closed, project-owner-authorised reporting
adjudication in `batch001_manual_adjudication.json`: M1, M3, M5, and M8 are
reported as task successes, M4 and M6 remain failures, and M2 remains evidence
incomplete. The sidecar preserves the original Harness outcomes and applies to
those exact unit IDs only; later episodes use the ordinary Harness without an
adjudication.

Useful entry points:

```bash
python experiment/experiment1b_use/config/providers/validate_manifest.py --help
python experiment/experiment1b_use/validation/reference/build_suites.py --help
python experiment/experiment1b_use/diagnostics/oracle/validate_oracle_plans.py --help
python experiment/experiment1b_use/run_b2.py --help
python experiment/experiment1b_use/run_b2_parallel.py --help
python experiment/experiment1b_use/aggregate_b2.py --help
```

The formal resolver mechanically reads the task-suite, provider, reference
selection, archived 33-case calibration, and retained pick-place v6 oracle
before credential loading. It enumerates exactly 210 unique units. No new
reference run or pre-formal LLM canary is required. `run_b2.py` runs one exact
unit only when explicitly invoked, and `run_b2_parallel.py` schedules selected
units in fresh subprocesses. The aggregator retains the fixed 210,
30-per-model, 15-per-robot-model, and 3-per-robot-task-model denominators;
`--require-complete` rejects missing planned IDs rather than dropping them.
