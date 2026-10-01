# AutoAdapter-Bench

AutoAdapter-Bench contains the existing robot catalog, task specifications, MuJoCo
evaluators and baseline source. It evaluates a supplied robot driver. This
source release includes no generated drivers, recorded results, videos,
demonstration datasets or trained baseline checkpoints.

## Scope

`eval.py` uses the earlier `auto_adapter.agent.task_planner.TaskPlanner`
ReAct controller, which calls the driver directly. The current AutoAdapter
mainline uses ReCAP through an exported MCP server. These are separate
execution interfaces: a driver generated for a new Design capability interface
is not automatically compatible with the earlier benchmark's tool methods.

The retained evaluators are not a complete reproduction runner for the current
thesis's fixed-design synthesis and driver-use experiments. No new experiment
cohort or protocol is defined by this release.

| Source | Purpose |
| --- | --- |
| `spec/robot_zoo.yaml` | Robot scenes, morphology and available observation bindings; also consumed by AutoAdapter's catalog |
| `spec/tasks_*.yaml` | Task prompts and success criteria grouped by morphology |
| `eval.py` | Legacy ReAct task execution, live-world grading and evidence checks |
| `eval_baseline.py` | Code-as-Policies or legacy ReAct execution, graded through a fresh-world tool-call replay |
| `physics.py` | MuJoCo state sampling and physical trace helpers |
| `baselines/` | Optional CaP, PPO, Diffusion Policy and OpenVLA implementations |

## Inputs and execution

Install the repository's base package as described in the root README. Run the
following commands from the repository root. They call the selected model and
write result files; model access and a working MuJoCo video renderer are required.

Supply a writable workspace containing a compatible `driver.py` with either
module-level `build()` or `Robot.build_from_mjcf(mjcf_path)`. The driver must
implement the selected morphology's legacy tool methods and expose its real
MuJoCo model and data. Select a robot and task supported by that driver and scene.
The evaluator sets `workspace/mjcf.xml` to the selected catalog scene and writes
recordings and traces into the workspace, so use a dedicated evaluation copy.

```bash
python -m autoadapter_bench.eval \
  --robot so101 --suites simple --tasks move_x_plus_5cm \
  --driver-workspace /absolute/path/to/compatible-workspace \
  --provider bedrock --model YOUR_BEDROCK_MODEL_ID \
  --n-trials 1 --output /absolute/path/to/results/react.json

python -m autoadapter_bench.eval_baseline \
  --baseline cap --robot so101 --suites simple --tasks move_x_plus_5cm \
  --driver-workspace /absolute/path/to/compatible-workspace \
  --model YOUR_BEDROCK_MODEL_ID \
  --n-trials 1 --output /absolute/path/to/results/cap.json
```

`eval.py` supports Bedrock and the public DeepSeek endpoint; use
`--provider deepseek --model YOUR_DEEPSEEK_MODEL_ID` with `DEEPSEEK_API_KEY` for
the latter. `eval_baseline.py` uses Bedrock for both supported baselines. Model
identifiers must be available to the caller's account. The retained cost helper
uses historical price estimates; it is not a current billing quote.

`eval.py` requires nonempty generation and physics traces plus a readable video
before marking a trial successful. It reports `framework_ok` separately from
task success; `validated_driver_task_ok` requires both. The baseline evaluator
uses replay and does not apply the same live-world evidence checks. A replay
exception, missing method or failed motion makes its trial fail. Graders include
both physical predicates and explicitly behavioral checks; older object and
pose graders can still read driver observation methods. Interpret results with
the selected task criterion and evaluator, rather than as interchangeable scores.

## Optional learned baselines

The PPO and Diffusion Policy sources use the `rl` extra:

```bash
pip install -e '.[rl]'
```

Their trainers and evaluators accept explicit workspace, demonstration and
checkpoint paths; inspect each script's `--help` after installing its optional
dependencies. Supply or train those inputs separately. The Gym environments
remain in `baselines/rl/`. The cross-skill scripts accept separate reach and pick
checkpoints.

OpenVLA uses the `vla` extra and its pinned dependency versions; use a separate
compatible environment. Its evaluators load the model named by `--model-id`
(default `openvla/openvla-7b`) and may download external model weights. The local
driver workspace is still required. These optional training and model inference
paths were not rerun for this source-only cleanup.

## Historical helpers

`run_multi_model.py`, `leaderboard.py` and `stats.py` retain the earlier study's
presets and aggregation conventions. The robot-specific `eval_piper_pick*.py`
and `eval_franka_reach_n10.py`, `render_*.py`, and `vla/diag_openvla_harness.py`
also retain historical workspace or result assumptions. They remain available
as reference source and are not the public starting commands. Their historical
drivers, datasets and result files are not distributed here; the old model IDs
and output claims do not establish current reproducibility.

## Focused local check

With pytest installed, the replay regression runs without a model request:

```bash
python -m pytest -q autoadapter_bench/tests/test_baseline_verdict.py
```

The test uses an explicitly named fixture and is not robot-performance evidence.
