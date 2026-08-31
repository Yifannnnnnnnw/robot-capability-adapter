# Benchmark Manifest Resolver

`manifest.py` is generic benchmark code. It resolves an explicitly supplied
external B1 or B2 manifest, verifies referenced benchmark contracts and counts,
checks canonical package/index availability, and emits deterministic unit IDs.
It does not own an experiment default, invoke a model, or run MuJoCo.

There is no current experiment manifest or supported experiment launch command.
The former concrete manifests and experiment-owned runners are preserved under
`../../experiment/archive/pre_thesis_realign_2026-08-31/` for historical
inspection only. Do not treat an archived runner as the active mainline or its
matrix as a future denominator.

When a new experiment is separately specified, this resolver may validate its
explicit manifest. A structurally valid manifest is not execution evidence;
the selected Driver criteria and recorded execution route must work.
