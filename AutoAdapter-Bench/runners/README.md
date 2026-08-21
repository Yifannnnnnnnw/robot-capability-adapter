# Benchmark Runners

`manifest.py` resolves component references, verifies declared counts, checks
canonical package and runnable-index availability, and emits deterministic B1
unit IDs. It does not invoke a model or MuJoCo.

`run_b1.py` is intentionally not present until all fixed per-robot B1
validation bundles are versioned and the canonical mainline exposes the
approved STUDY-to-final-validation stopping boundary without rerunning TGCD or
IVC. `run_b2.py` is
intentionally not present until at least one high-level-controller adapter and
the B2 task set pass audit. This avoids a runner that silently executes Task
Demo in B1 or a placeholder B2 implementation.

From this directory:

```bash
python runners/manifest.py validate
python runners/manifest.py b1-matrix --output /tmp/chapter3-b1-units.json
```
