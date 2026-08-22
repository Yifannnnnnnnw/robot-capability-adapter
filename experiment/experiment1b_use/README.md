# Experiment 1b — B2 use

This is the active B2 ReCAP-use workspace.  Its design authority is
`B2_RECAP_AUTHORITY.md`; configuration, fixed reference validation,
diagnostics, runtime, tests, and local run outputs are grouped below this
directory.  Formal B2 matrix orchestration remains a later implementation
batch.

The `runs/` directory is an active local-output placeholder.  Historical B2
runs are under `../archive/runs/b2_recap/` and are resolved read-only by
`../path_layout.py` when an old path is missing.

Current diagnostic entry points:

```bash
python experiment/experiment1b_use/config/providers/validate_manifest.py --help
python experiment/experiment1b_use/validation/reference/build_suites.py --help
python experiment/experiment1b_use/diagnostics/oracle/validate_oracle_plans.py --help
```
