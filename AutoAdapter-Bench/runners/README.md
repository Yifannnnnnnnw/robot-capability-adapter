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

The experiment-owned B1 runner lives with the concrete experiment rather than
in this reusable benchmark directory. A structurally valid manifest is not
execution evidence; the selected Driver criteria and recorded
STUDY-to-terminal-validation route must work.
