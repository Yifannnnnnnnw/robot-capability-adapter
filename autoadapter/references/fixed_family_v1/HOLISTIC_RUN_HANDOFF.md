# Remaining six robots through Holistic — 2026-09-08

The user authorized a cheap model for the six robots that received no successful
model response in the V4 Flash follow-up: Kinova, xArm7, UR5e, KUKA, Unitree A1 and
ANYmal-C. Each starts one independent V3.2 diagnostic. Existing V4 Flash results,
including interrupted Franka and Barkour candidates, are preserved.

Owned files are `scripts/run_fixed_family_diagnostic.py`,
`configs/diagnostics/fixed-family-v1-holistic-v32.json`,
`tests/test_fixed_diagnostic_entry.py` and these fixed-family documentation files.
This small batch was implemented in the current session with a read-only agent
review; a separate Luna conversation was not used. It changes no robot assets,
private instances, thresholds, Harness, continuous Repair implementation or
formal thesis experiment.

## Route and budget

The existing Holistic profile supplies the endpoint, `X-Api-Key` authentication
and 120-second request timeout. `--holistic` reads the ignored local
`.env.company-api`; no credential enters the tracked configuration. Omitting the
flag with the gateway config is rejected before loading official credentials.

The actual requested and returned model is `deepseek.v3.2`. Its gateway alias is
recorded without claiming an immutable model revision. Thinking is requested as
disabled. Study remains 16 turns, Generate and each Repair 22, with at most three
Harness submissions. Development execution budgets remain 4,000 steps, 20 seconds
of simulated time and 30 seconds per call. TaskDemo and Evolution remain off.

Native tool history fails at the gateway's upstream message deserializer with
HTTP 500. The existing `text-observation` mode passes a real two-request tool
round trip: [probe](../../runs/diagnostic/holistic-connectivity-20260908/tool_roundtrip_v32_text.json).
This mode presents prior tool requests and results as text while retaining real
tool calls and the same development conversation/workspace. It is a recorded
condition difference from the V4 Flash diagnostic, not a new execution framework.

The configuration uses the [AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-deepseek-deepseek-v3-2.html)
164K context specification and [AWS public pricing](https://aws.amazon.com/bedrock/pricing/)
of USD 0.62 input / 1.85 output per million tokens as a reference estimate only.
Holistic's actual billing rate is not known; report observed tokens separately
and do not present that estimate as an account charge.

## Check and run

From the repository root, using the installed Python 3.11:

```bash
PYTHONPATH=autoadapter/src python3.11 -m pytest -q autoadapter/tests/test_fixed_diagnostic_entry.py
PYTHONPATH=autoadapter/src python3.11 autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-v32.json \
  --holistic --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-v32-NEW
```

Use a fresh output directory. Real MuJoCo worker checks precede each robot's
model calls; a full reference pass is not required. Keep generated sources,
failure details, videos, per-call usage and final reports. Report generated
Driver, actual Harness score and completed Repair separately. Do not increase
budgets or alter fixed inputs after a candidate failure.

Minimum validation: three focused entry tests, manifest/runtime matching, the
real tool round trip, and the six real robot paths. Handoff includes each run
directory, submissions, scores, failures, tokens and the batch commit ID.
