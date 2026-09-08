# Franka Panda single-robot Opus diagnostic

Completed run: [Franka results](OPUS5_FRANKA_RESULTS.md). Study passed;
Generate recovered once from timeout, then a later timeout ended the run before
Harness. Preparation commit: `ce4c4d7`.

The user requested a different robot after the SO-101 retry completed Study but
timed out during Generate. This run selects Franka Panda only. It is a fresh
diagnostic; no SO-101 conversation, Driver or result is reused as Franka evidence.

The configuration differs from the SO-101 configuration only in `robots` and
`experiment_id`: Holistic `eu.anthropic.claude-opus-5`, adaptive thinking, native
tool history, skeleton-assisted, fixed A1–A5 inputs and ten private cases. Study
16 / Generate 22 / at most one Repair 22 turns, at most two Harness submissions,
120-second model request deadline, 4,000-step / 20-second development execution
budgets and all existing standards remain unchanged. Full reference calibration
is optional; the runner must pass its real basic environment check first.

```sh
PYTHONPATH=autoadapter/src python3.11 -u autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-franka-one-repair.json \
  --holistic --robots franka_panda --retry-timeout-once \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908
```

One timeout retry is permitted across the entire robot client; each logical
request still allows at most two physical sends. Request bodies, response timing,
selected gateway trace IDs and failures are recorded by the existing runner.
The existing evidence fixes are `7f1cf0a`, `1d39b2f` and `0bec852`. No new runtime
code, robot assets, criteria, retry framework or thesis material is changed.

Preparation owns this file and the new single-robot JSON configuration. The
current session prepares this small batch directly, with read-only agent review;
no new Luna implementation session is needed. Minimum check: parse with
`ExperimentConfig`, verify all non-robot conditions match the SO-101 configuration,
load the registered package and both fixed inputs with the existing validators,
then run the real environment and model path. Commit the preparation batch
before launch. Record the result and its commit separately.

Deliver actual completed stages, generated source and Harness metrics/videos
when reached, whether Repair and timeout retry occurred, returned token usage,
unknown timeout usage and the public-price estimate. The configured USD 5.50
input / 27.50 output per million tokens is a reference estimate, not a verified
Holistic bill. A client timeout cannot by itself identify the upstream root cause.
Stop after this one robot; no other robot is dispatched by this configuration.
