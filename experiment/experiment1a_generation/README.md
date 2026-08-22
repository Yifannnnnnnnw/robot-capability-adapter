# Experiment 1a — B1 generation

This is the active B1 generation workspace.  Its design authority is
`EXPERIMENT_1_AUTHORITY.md`; the manifest, configuration, validation bundles,
diagnostics, runtime, tests, and run entry points are kept together here.

The `runs/` directory is an active local-output placeholder.  Historical B1
runs are under `../archive/runs/experiment1/` and are resolved read-only by
`../path_layout.py` when an old path is missing.

Useful entry points:

```bash
python experiment/experiment1a_generation/run_b1.py --help
python experiment/experiment1a_generation/run_b1_parallel.py --help
python experiment/experiment1a_generation/aggregate_b1.py --help
```
