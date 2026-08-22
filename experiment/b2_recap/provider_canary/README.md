# B2 real-model provider canary

`run_real_model_canary.py` runs one diagnostic `mw_sweep_into_goal` R1 episode
through the fixed ReCAP controller, the real M1 provider model, the typed B2
adapter, the fixed SO-101 reference driver, one persistent Direct-MuJoCo
worker, and the independent task Harness.

The runner reads the credential named by the pinned provider JSON from
`.env.company-api`. The credential stays in the Framework parent and is
excluded from the report and candidate-worker environment. The default run
disables video, so it is never a formal B2 episode and cannot clear AA2-B2
Section 7. The ignored diagnostic report retains the code commit, fixed ReCAP
prompt/schema, exact secret-free provider request/response exchanges, call
ledger, controller trace, worker record, and independent Harness result.

Example:

```bash
PYTHONPATH=autoadapter/src \
python experiment/b2_recap/provider_canary/run_real_model_canary.py \
  --output experiment/b2_recap/runs/m1-real-model-canary/report.json
```
