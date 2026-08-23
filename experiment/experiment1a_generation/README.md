# Experiment 1a — B1 generation

This is the active B1 generation workspace.  Its design authority is
`EXPERIMENT_1_AUTHORITY.md`; the manifest, configuration, validation bundles,
diagnostics, runtime, tests, and run entry points are kept together here.

The current prospective matrix is exactly two robots (`robotstudio_so101` and
`unitree-go2-stock-12dof`), seven active backbones (`M1`–`M6` and `M8`), two
generation conditions, and `r01`–`r03`: 84 core cells with at most 252
submissions. Complete `r04` and `r05` extension blocks add 28 cells each,
for 140 cumulative cells and at most 420 submissions. `M7` remains an
inactive historical configuration and is not reused. Formal dispatch remains
enabled after the zero-model readiness checks; no formal cell has been started
by that configuration change. M8 cost accounting uses its dated public OpenAI
price reference rather than claiming the company gateway's actual bill.

The `runs/` directory is an active local-output placeholder.  Historical B1
runs are under `../archive/runs/experiment1/` and are resolved read-only by
`../path_layout.py` when an old path is missing.

Useful entry points:

```bash
python experiment/experiment1a_generation/run_b1.py --help
python experiment/experiment1a_generation/run_b1_parallel.py --help
python experiment/experiment1a_generation/aggregate_b1.py --help
```
