# Your first Auto Adapter run

Start with the included Piper model. This guide follows one run from local
simulation setup to its generated files. Complete the [installation](../README.md#install)
and run every command below from the repository root.

## What goes in and what comes out

Auto Adapter reads a MuJoCo robot model and public task requirements. Study
examines the model; Design proposes capability requests, physical criteria and
scene cases; Generate writes a driver; Validate measures its behavior in fresh
simulation processes. A failed validation can lead to a bounded repair attempt.
After validation passes, Export writes a Model Context Protocol (MCP) server for
that design's capabilities. The optional ReCAP Demo then attempts a separate
configured task through that server.

For this example, the robot is `assets/mjcf/piper/scene.xml`, and the task inputs
are under `auto_adapter/task_libraries/piper/1.0.0/`. The generated method names
and request schemas belong to the run's Design output. See [robot inputs](ROBOTS.md)
for other catalog entries and their generation routes.

## Check local simulation without a model call

This command loads the real Piper MJCF and advances ten MuJoCo steps. It does
not require provider credentials or a renderer.

```sh
python - <<'PY'
from pathlib import Path
import mujoco
import numpy as np

model = mujoco.MjModel.from_xml_path(
    str(Path("assets/mjcf/piper/scene.xml").resolve())
)
data = mujoco.MjData(model)
for _ in range(10):
    mujoco.mj_step(model, data)
assert np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()
print(f"Piper loaded: {model.nq} position coordinates, {model.nu} actuators")
print(f"Advanced to simulation time {data.time:.3f} s with finite state")
PY
```

A successful check confirms that the local MuJoCo installation and these asset
paths work. Driver generation, capability validation and video rendering are
separate steps.

## Generate and export a driver

Configure the normal AWS credential chain and select a Bedrock model or
inference-profile ID enabled in your region. This command makes real model
calls and uses the provider's normal billing.

```sh
export AUTO_ADAPTER_MODEL='your-enabled-bedrock-model-id'
python scripts/run/run_stage1.py \
  --robots piper --provider bedrock --region us-east-1 \
  --model "$AUTO_ADAPTER_MODEL" --output-root runs/piper-first
```

The default run ends after Export and allows up to three repair attempts after
initial generation. Choose a fresh output directory for an independent run.
Use `--stop-after validate` when you want only generation and physical
validation. Add `--enable-demo` to a new run to attempt the configured ReCAP
task after Export; task recording needs working OpenGL and FFmpeg support.

## Read the result in order

The launcher writes `runs/piper-first/launch_<timestamp>.json`. Its `results`
list contains one row for Piper, including the actual `workspace` and
`summary_path`. If setup fails early, read that row's `error`; some later
artifacts may not exist.

For the command above, the robot workspace is `runs/piper-first/piper/`.
After the run finishes, inspect the files available from the stages it reached:

| File relative to the robot workspace | What it tells you |
| --- | --- |
| `summary.json` | Requested-stage outcome and per-phase errors, traces and artifacts |
| `study.json` | The model's structural analysis of the robot |
| `design/capability_design.json` | This run's capability methods, request schemas and physical criteria |
| `design/scene_cases.yaml` | Prepared scene cases used to validate that design |
| `driver.py` | Generated Piper driver, using the catalog's skeleton-assisted route |
| `validate_report.json` | Physical validation results, including `all_ok` and individual checks |
| `validation/` | Case outputs referenced by the validation report |
| `mcp_server.py` | Exported server for the generated capabilities, if Export succeeded |
| `narrative.md` | A readable timeline with links to the run's artifacts |

The Piper `summary.json` stores `generation_ok`, `framework_ok`, `stage1_ok`,
`ok` and a `phases` list. `generation_ok` means the final generation or repair
produced a candidate. `framework_ok` means the final physical validation passed.
`stage1_ok` also requires Study and Design to have succeeded. The aggregate `ok`
applies to the requested stages, so an intentional early stop can be successful
without producing or validating a driver.

The launcher's result row additionally records `export_ok` and `demo_ok`.
These two convenience fields are not top-level fields of Piper's persisted
`summary.json`; use its Export/Demo entries in `phases` for details. A disabled Demo or an
intentional early stop has `demo_ok: null` in the launch result. If an earlier
stage fails, the skipped Demo can instead have `demo_ok: false`; read its phase
`error` to see why it did not run. Driver validation and export do not establish
downstream task success.

## Inspect an optional ReCAP task

A configured Demo creates a fresh directory under
`runs/piper-first/piper/demos/recap-<unique>/`. Its `request.json` records the task
inputs, `worker.log` captures the worker output, and `task/` contains the reports
and recordings. Read `task/task_report.json` first:

- `execution_ok` describes completion of the controller, MCP, simulation and
  video execution chain.
- `physical_task_success` is the independent physical task verdict. A null
  value means no valid physical verdict is available; inspect both `error` and
  `evaluation_error` when present.
- For the configured task, `ok` requires both execution and physical success.

`task/task_evaluation.json` gives the physical check values and verdicts.
`task/physics_samples.jsonl` records the measured state; `task/trace.jsonl` and
`task/model_turns.jsonl` record execution and model interactions. Open the file
named by `video_path` in the report to inspect the recording. An early failure
can leave only a partial set of files. A Demo failure does not change the
completed capability validation or trigger driver repair.

For task execution using an existing export, see
[ReCAP tasks](../auto_adapter/RECAP_TASKS.md). For driver-workspace evaluation
with AutoAdapter-Bench, read its [guide](../autoadapter_bench/README.md) first:
its existing direct-driver interface differs from the dynamic MCP export used
here.
