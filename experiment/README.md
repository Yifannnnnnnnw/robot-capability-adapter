# Experiment Workspace

This top-level directory owns concrete experiment authorities, manifests, run
artifacts, and experiment-specific analysis. It is separate from
`AutoAdapter-Bench/`, which owns only reusable benchmark definitions,
protocols, registries, catalogues, and generic manifest-resolution code.

Each experiment may have at most one authority explicitly delegated by
`AUTOADAPTER_2_AUTHORITY.md`. README files, manifests, run records, and analysis
outputs are non-normative implementations or evidence and cannot override the
applicable authority.

Current experiment:

- `experiment1/EXPERIMENT_1_AUTHORITY.md` — the sole authority for Experiment 1.
- `experiment1/B1_DRIVER_VALIDATION_CRITERIA.md` — non-normative, human-readable
  criteria for the five Experiment 1 robots, derived from the matching LaTeX
  appendix; it is not an executable validation suite.
