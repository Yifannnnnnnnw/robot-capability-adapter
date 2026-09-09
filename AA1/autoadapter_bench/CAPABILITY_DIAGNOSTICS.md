# AA1 capability integration diagnostics

This is the approved integration diagnostic, not a formal experiment protocol.
AA1 is tracked by the parent repository on `codex/aa1-capability-integration`.
Model calls use the existing Holistic transport and credentials; reference
controllers are never labelled as model-generated drivers.

The robot zoo selects `capability_profile`, `capability_mjcf` and the required
`capability_skeleton`. Generation receives only the public design plus Study,
MJCF and low-level skeleton APIs. `build()` returns a generated subclass whose
capability methods accept `request`. Framework loads private conditions from
the catalog and samples the same MuJoCo world that executes those methods.
TaskPlanner uses the public request schemas and the same physical scorer.

PiPER is the first complete profile. Its two conditions for each of A1–A5
passed the independent reference controller and the shared physical evaluator
(10/10), including real rendered videos. The result is at
`diagnostics/capability_calibration/piper/framework_reference/suite_result.json`.
Per-case result files include the local raw trace and MP4 paths. Large raw
physics traces and videos remain local; compact results and code are committed.

The first real PiPER generation exhausted 22 turns after truncated `write_file`
arguments omitted `content`. It produced no driver and did not run Framework.
Its original evidence remains under
`diagnostics/capability_update_20260909/generated/piper/`.
The follow-up uses bounded file chunks with `append=true`; it generated a real
driver and passed 7/10 Framework conditions. Both A4 conditions and the A5
nominal condition failed. This is not a fully validated robot. Evidence is
under `diagnostics/capability_update_20260909/generated_retry1/piper/`.
The historical retry summary incorrectly labels one generation attempt as two
and duplicates the validation phase; the recorded generation trace is one
attempt. The orchestrator accounting is now fixed without rewriting that run.

Run from the AA1 directory using `.venv/bin/python`. Real generation example:

```python
from pathlib import Path
from auto_adapter.orchestrator import SelfAssemble, SelfAssembleConfig

cfg = SelfAssembleConfig(
    robot_id="piper", mjcf_path=Path("assets/mjcf/piper/scene.xml").resolve(),
    workspace_root=Path("/tmp/aa1-piper-canary").resolve(),
    model_provider="holistic", bedrock_model="eu.anthropic.claude-sonnet-4-6",
    max_outer_gen_val_iters=1,
)
with SelfAssemble(cfg) as pipeline:
    print(pipeline.run(stop_after="validate").to_json())
```

Task diagnostic for a generated driver:

```sh
.venv/bin/python -m autoadapter_bench.eval --robot piper --suites capability \
  --n-trials 1 --provider holistic --model eu.anthropic.claude-sonnet-4-6 \
  --driver-workspace /tmp/aa1-piper-canary/piper \
  --output /tmp/aa1-piper-canary/piper/task_diagnostic.json
```

Task reports retain `framework_ok` and `validated_driver_task_ok`. A task pass
cannot erase failed capability conditions. Builds that fail cannot run tasks.
The demo remains on the existing ReAct flow; the proposed ReCAP change is deferred.

Focused checks for this common interface batch:

```sh
.venv/bin/python -m pytest -q auto_adapter/tests/test_stop_after_validate.py \
  auto_adapter/tests/test_capability_interface.py \
  auto_adapter/tests/test_capability_physics.py \
  auto_adapter/tests/test_generation_file_chunks.py
```

The checks cover generated subclasses, missing methods, trusted morphology,
canonical physical stepping, direct state/model writes, unusable video, chunked
generation recovery and accurate phase accounting. Named fixtures are tests,
not substitutes for the real reference and Holistic runs above.
