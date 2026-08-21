# Benchmark Manifest Resolver

`manifest.py` is generic benchmark code. It resolves an explicitly supplied
external B1 or B2 manifest, verifies referenced benchmark contracts and counts,
checks canonical package/index availability, and emits deterministic unit IDs.
It does not own an experiment default, invoke a model, or run MuJoCo.

From the repository root, the current Experiment 1 core manifest can be checked
with:

```bash
python3 AutoAdapter-Bench/runners/manifest.py validate \
  --b1-recipe experiment/experiment1/manifest.json

python3 AutoAdapter-Bench/runners/manifest.py b1-matrix \
  --recipe experiment/experiment1/manifest.json \
  --output /tmp/experiment1-b1-units.json
```

`run_b1.py` is intentionally absent until the canonical mainline exposes the
approved STUDY-to-terminal-validation boundary and the selected fixed bundles
are versioned and reference-calibrated. A structurally valid manifest is not
formal readiness.
