# Experiment Workspace

This top-level directory owns concrete experiment authorities, manifests, run
artifacts, and experiment-specific analysis. It is separate from
`AutoAdapter-Bench/`, which owns only reusable benchmark definitions,
protocols, registries, catalogues, and generic manifest-resolution code.

Each experiment may have at most one authority explicitly delegated by
`AUTOADAPTER_2_AUTHORITY.md`. README files, manifests, run records, and analysis
outputs are non-normative implementations or evidence and cannot override the
applicable authority.

Active experiments:

- `experiment1a_generation/` — Experiment 1a/B1 generation, with its authority,
  manifests, validation bundles, diagnostics, runtime, tests, and active run
  placeholder.
- `experiment1b_use/` — Experiment 1b/B2 ReCAP use, with its authority,
  configuration, fixed reference validation, diagnostics, runtime, tests, and
  active run placeholder.

Historical run evidence is read-only and is kept under `archive/runs/`; see
`archive/README.md`.  The archive move preserved the raw files without
rewriting records.  `path_layout.py` resolves an old run prefix to the archive
only when the original path is absent; it does not create compatibility
directories or edit historical JSON.
