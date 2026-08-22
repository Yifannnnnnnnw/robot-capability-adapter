# B2 real-model provider canary

`run_real_model_canary.py` runs one diagnostic SO-101 R1 episode through the
fixed ReCAP controller, a selected real pinned provider model, the typed B2
adapter, the fixed SO-101 reference driver, one persistent Direct-MuJoCo
worker, and the independent task Harness. `--task` selects one of the sealed
SO-101 B2 tasks; it defaults to `mw_sweep_into_goal`.

The runner reads the credential named by the pinned provider JSON from
`.env.company-api`. The credential stays in the Framework parent and is
excluded from the report and candidate-worker environment. Video is disabled
by default and can be enabled with `--record-video`. A provider canary remains
diagnostic even with complete video; it cannot enter the formal denominator
until all AA2-B2 Section 7 gates are cleared. The ignored report retains the
code commit, fixed ReCAP prompt/schema, exact secret-free provider
request/response exchanges, call ledger, controller trace, worker record, and
independent Harness result.

Example:

```bash
PYTHONPATH=autoadapter/src \
python experiment/b2_recap/provider_canary/run_real_model_canary.py \
  --record-video \
  --output experiment/b2_recap/runs/m1-real-model-canary/report.json
```

For example, to let the pinned M2 Opus 5 backbone plan and execute the harder
pick-and-place task without a scripted oracle:

```bash
PYTHONPATH=autoadapter/src \
python experiment/b2_recap/provider_canary/run_real_model_canary.py \
  --provider-config experiment/experiment1/providers/M2-company-api-opus-5.json \
  --task mw_pick_place \
  --record-video \
  --output experiment/b2_recap/runs/m2-opus5-pick-place/report.json
```
