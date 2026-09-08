# Low-cost coding-model fallback — 2026-09-08

The user asked for a cheap model to test synthesis of the remaining robots.
Holistic V3.2 completed real tool exchanges but hit the unchanged 120-second
request deadline in KUKA/Kinova Study and A1 Generate before any Driver submission.
Those records remain failed/interrupted V3.2 runs. They are not overwritten or
relabelled as Qwen, and are not capability failures.

The first bounded check used `qwen.qwen3-coder-next` on KUKA and exercised real
generation, Harness and continuous Repair. [Current results](HOLISTIC_RESULTS.md)
record subsequent fallback runs separately. Only robots
interrupted before an accepted Driver may be considered for this separate
fallback condition. It does not resume the terminated V3.2 conversation, and
must not be described as the same independent synthesis sample. No candidate
failure triggers relaxed standards or extra Harness submissions.

The [Qwen official model card](https://huggingface.co/Qwen/Qwen3-Coder-Next)
describes a non-thinking coding-agent model with 262,144-token context. The
[AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-coder-next.html)
lists a 16K output limit. The config retains the original 16,384 requested output
ceiling, Study 16, Generate/Repair 22, three Harness submissions and all existing
physical execution budgets. No model API or pipeline code changed in this batch.

The existing Holistic route, credentials and 120-second timeout apply. Native
tool history also returned HTTP 500 for Qwen; the existing text-observation mode
passed [the real tool round trip](../../runs/diagnostic/holistic-connectivity-20260908/tool_roundtrip_qwen_coder_next_text.json).
Short requests took 0.74 and 0.36 seconds; this is not a full-synthesis speed claim.

[AWS public US pricing](https://aws.amazon.com/bedrock/pricing/) of USD 0.50 input
and 1.20 output per million tokens is a reference estimate, not Holistic's actual
account charge. AWS European rates differ and the gateway's backend region is
unknown. Always report actual response usage separately.

```bash
PYTHONPATH=autoadapter/src python3.11 autoadapter/scripts/run_fixed_family_diagnostic.py \
  --config autoadapter/configs/diagnostics/fixed-family-v1-holistic-qwen-coder-next.json \
  --holistic --robots kuka_iiwa_14 \
  --output autoadapter/runs/diagnostic/fixed-family-v1-holistic-qwen-kuka-NEW
```

Owned files are the new diagnostic config and this handoff. This small batch was
prepared in the current session with a read-only source check; no separate Luna
conversation was used. Check config parsing, unchanged budgets, the real tool
round trip and the KUKA real environment/model/Harness path. Return source and
Harness outcomes separately, along with run directory, token usage, limitations
and commit ID. Do not edit robot packages, fixed standards or prior run evidence.
