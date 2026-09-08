# Opus 5: SO-101 single-robot diagnostic

The user requested sequential trials and at most one Repair. This first trial
contains SO-101 only; the other ten fixed-family robots are not dispatched by
this configuration. It remains diagnostic and does not change the thesis cohort.

## Fixed conditions

- Configuration: `configs/diagnostics/fixed-family-v1-holistic-opus5-so101-one-repair.json`.
- Existing runner and `fixed_inputs_from=references/fixed_family_v1`; no TGCD/IVC
  authoring, TaskDemo, Evolution, reference-driver substitution or policy training.
- Requested model: `eu.anthropic.claude-opus-5`; gateway alias, not an independently
  verified immutable revision. Adaptive thinking is requested; no explicit effort
  setting is sent. Native tool history and the existing 120-second request limit.
- Study 16 turns; Generate 22; one Repair of at most 22 turns. At most two Harness
  submissions, with no Repair after a first-submission pass. Each suite has ten
  cases. Existing 4,000-step / 20-second development execution budgets remain.
- Original fixed capabilities, thresholds, instances, assets and private guards
  are unchanged. The fixed-mode Study prompt now acknowledges the provided public
  capability design and criteria; dynamic Study retains its pre-TGCD wording.

## Connectivity and price interpretation

The earlier credential returned HTTP 401 with `Invalid or revoked API key` on
generation, although `/models` returned HTTP 200. After the user updated the local
credential, two Opus 5 requests completed a real native tool round trip: 1,007
input and 78 output tokens in total. Evidence:
`runs/diagnostic/holistic-opus5-so101-preflight-20260908/tool_roundtrip_native_new_credential.json`.
No robot materials were sent by that protocol probe.

The config's USD 5.50 input / 27.50 output per million tokens is the public
Opus 5 base price plus the published 10% regional premium. It is a reference
estimate, not a verified Holistic billing rate. Prompt-cache discounts are not
assumed. Report actual returned usage, including any reasoning/cache fields;
missing usage on failed requests is unknown. Source:
https://platform.claude.com/docs/en/about-claude/pricing

## Run and handoff

From the repository root, with the local ignored company credential:

```sh
PYTHONPATH=autoadapter/src python3.11 autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-opus5-so101-one-repair.json \
  --holistic --robots robotstudio_so101 \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-opus5-so101-NEW
```

Use a fresh output directory. The runner performs its normal real environment
check before calling the model. Report submitted source, per-case verdicts,
failure details, videos, completed Repair/resubmission, tokens and estimated
cost before dispatching another robot. A generated file, reference result or
API interruption must not be reported as a model capability pass.

Focused preparation checks: fixed Study prompt regression (interactive and JSON
paths), existing dynamic Study checks, and single-robot configuration/budget audit.
Current-session implementation and read-only agent review were used; no separate
Luna conversation was used.
