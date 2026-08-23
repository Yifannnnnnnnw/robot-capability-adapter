# Experiment Workspace

This top-level directory owns concrete experiment authorities, manifests, run
artifacts, and experiment-specific analysis. It is separate from
`AutoAdapter-Bench/`, which owns only reusable benchmark definitions,
protocols, registries, catalogues, and generic manifest-resolution code.

Each experiment may have at most one authority explicitly delegated by
`AUTOADAPTER_2_AUTHORITY.md`. README files, manifests, run records, and analysis
outputs are non-normative implementations or evidence and cannot override the
applicable authority.

Chapter 3 workspaces:

- `experiment1a_generation/` — Experiment 1a/B1 generation, with its authority,
  manifests, validation bundles, diagnostics, runtime, tests, and active run
  placeholder.
- `experiment1b_use/` — Experiment 1b/B2 ReCAP use, with its authority,
  configuration, fixed reference validation, diagnostics, runtime, tests, and
  active run placeholder.

Other delegated experiments:

- `experiment2/` — Chapter 4's bounded SO-101 cross-run Experience closure.
- `experiment3/` — Chapter 5's exact eleven-configuration Direct-MuJoCo
  cohort.

Shared support:

- `path_layout.py` — read-only compatibility resolution for moved historical
  B1/B2 run paths.
- `archive/runs/` — immutable historical B1/B2 run evidence; it is not an
  active output directory.
- `tests/` — cross-experiment focused checks.
- `CHAPTER4_5_RUNBOOK.md` — launch instructions scoped only to Experiments 2
  and 3.

Historical run evidence is read-only and is kept under `archive/runs/`; see
`archive/README.md`.  The archive move preserved the raw files without
rewriting records.  `path_layout.py` resolves an old run prefix to the archive
only when the original path is absent; it does not create compatibility
directories or edit historical JSON.
