# AA1 generation stages and ReCAP tasks

Both `SelfAssemble` and `FromScratchOrchestrator` now use:

```text
Study → Design (when requested) → Generate ↔ Validate → Export → optional Demo
```

The fixed catalog and task-grounded design routes share the stage sequence.
Generation implementations remain separate. The selected design is passed to
generation, validation, export and task execution. A final validation failure
blocks export. Export writes `mcp_server.py`; standard drivers retain
`driver.py:build()`, and scratch drivers retain
`driver_from_scratch.py:Robot.build_from_mjcf(mjcf_path)`.

Both config classes accept `enable_demo=False` and `demo_config_path=None`.
Set `enable_demo=True` for automatic local task execution after export. An
inclusive `stop_after` still stops the pipeline, including before a configured
demo. Disabled or deliberately skipped stages do not fail the run. A demo
failure does not change generation/validation results or trigger repair.

## One task entry point

`auto_adapter.agent.task_execution.run_task` receives an existing driver,
its current capability design, validation cases and report, and an explicit
task, scene, initial state and public parameters. It never studies, generates,
repairs or selects another driver. Standard tasks call `build()` from a copied
module beside the selected `mjcf.xml` link; scratch tasks call
`Robot.build_from_mjcf()` with that scene's resolved path.

Each task creates a fresh output directory and a new MuJoCo world. Within one
task all calls share the same driver/model/data. Input copies and the scene
link live in the task directory; the generation workspace's `mjcf.xml` is
unchanged. Use a new `output_dir` for each invocation.

The compatibility CLI uses the same runner:

```sh
PYTHONPATH=AA1 AA1/.venv/bin/python -m auto_adapter.agent.recap_demo \
  --workspace /absolute/generated/piper --robot-id piper \
  --model eu.anthropic.claude-sonnet-4-6
```

Add `--from-scratch` for `driver_from_scratch.py`. The wrapper loads the saved
design/cases, or the matching fixed catalog when no generated design exists.
An explicitly selected generated design never falls back to another catalog.
Automatic demos call `run_configured_demo`, which dispatches an isolated task
process and returns its report to the orchestrator.

For a manually chosen independent task, write a JSON object containing the
`run_task` keyword inputs and pass `--input-json /absolute/task-inputs.json`:

| Input | Value |
| --- | --- |
| `driver_path`, `from_scratch`, `robot_id` | Existing driver and construction route |
| `capability_design` | Current public design object |
| `validation_suite`, `validation_report` | Corresponding case and result objects |
| `task_description`, `parameters` | Natural-language task and public goal parameters |
| `scene_path`, `initial_state` | Explicit scene and AA1 reset-state object |
| `required_capabilities` | Optional required method names; missing or unvalidated names block execution |
| `model`, `provider`, `region`, `max_tokens` | Existing AA1 model transport configuration |
| `output_dir` | New task directory |

No task library or formal experiment runner is introduced here.

## Controller and outputs

`agent/recap.py` adapts the **official ReCAP controller** vendored at
`agent/vendor/recap/chatbot.py`, revision
`2fb112ffad685c7c6f7de86d5487ecca6f566fcc` of
[ReCAP-Stanford/ReCAP](https://github.com/ReCAP-Stanford/ReCAP).
The complete source, MIT license, source URL and small integration patch are
kept together in that directory. The previous custom `submit_plan` loop is removed.

The model returns the official JSON format with a brief `think` summary and an
ordered list of string `subtasks`. Abstract strings become task-tree nodes.
Primitive strings encode a capability name and native request as JSON. The
upstream generator expands the first subtask, yields executable actions, returns
to parent nodes and builds prompts containing the parent's previous summary and
remaining tasks. AA1 validates and executes each yielded native request, then
sends back the actual operation status and public observations.

The official downward singleton rule is retained: one subtask denotes a
primitive action; an abstract decomposition needs at least two subtasks. After
a leaf returns, the parent revises its remaining plan. An empty abstract-node
plan returns to its parent. Root termination ends the controller only; AA1
rejects a diagnostic with no executed action or a final failed action.

All nodes share 16 model calls, 12 capability calls and a maximum depth of 6
(root depth is zero; primitive nodes count). Budget exhaustion and errors are
explicit results. Model history uses the upstream message-window policy, with
a default threshold of 32 messages. AA1 replaces the cooking few-shot setup and
wording with robot task/schema instructions, and avoids claiming an attempted
action succeeded before its status is inspected. These adaptations do not
replace the official tree traversal or parent-context construction.

The task bridge reuses AA1's model client; it does not call `ReactLoop.run()`.
Generation and export retain their existing ReAct loops. Ordinary method errors
return as observations for replanning; world corruption stops task execution.

The task directory contains `task_report.json`, `trace.jsonl`,
`model_turns.jsonl`, copied inputs, `recap/tree.json`, `recap/history.json`, and
`video.mp4` when recording is available. The official timestamped tree/history
files are retained too. Tree JSON uses upstream's nested `task_name`, `children`,
`info_list` and `obs_list` structure, including primitive nodes. The report names
the official controller and its source revision. `ok` records completion of the
diagnostic execution/recording chain; `physical_task_success` remains `null`
because no independent task predicate is evaluated.

## Fixed demos and current limitations

`demo_tasks.yaml` holds one public task, scene and initial state per robot.
Relative scene paths resolve from the AA1 root. Its 10 robots with existing
public profiles declare required method names. A differently named dynamic
design needs a matching custom demo configuration. The other 5 robots still
need an explicitly supplied, validated capability design; their configuration
does not invent interfaces or admit reference drivers.

Go2, A1 and ANYmal C request: stable stance for 1.2 s, forward 10 cm, stable
stance for 1.2 s, shallow crouch, restore standing height, stable stance for
1.2 s. Crouch/restore heights are respectively 0.26/0.32, 0.23/0.26 and
0.34/0.374 m. Current generated-driver reports do not admit the full sequence:

| Robot | Existing generated driver | Validation missing for the fixed demo |
| --- | --- | --- |
| Go2 | `stage1_full_opus48_20260910T142842Z/repair_3/go2` | Both stable-stance cases |
| Unitree A1 | `stage1_full_opus48_20260910T142842Z/repair_2/unitree_a1` | Path, height and stable-stance boundary cases |
| ANYmal C | `stage1_full_opus48_20260910T142842Z/repair_3/anymal_c` | Path, height and stable-stance cases |

These paths are under `autoadapter_bench/diagnostics/capability_update_20260909`.
All three actual drivers loaded their configured scenes and initial states and
exposed the requested methods. Task preflight returned `UNAVAILABLE` with zero
model or capability calls. Their new crouch/restore behavior is therefore not
claimed as physically verified. No reference driver, replacement model or
generation repair was used.

The extra arm terminal-hold requirement found in the first diagnostic was
removed: the approved arm task is two checkpoints followed by gripper actions
(or the KUKA offset/return), with no invented holding interface. A regression
check failed before that configuration correction and passed afterward.

## Official-source integration check, 2026-09-13

The earlier `artifacts/recap_migration_20260913/` diagnostics exercised the
previous custom AA1 controller. They are **not evidence of official ReCAP source
integration**. Root `autoadapter/` and the retired root benchmark were
subsequently removed by the approved repository cleanup.

Focused checks now exercise the actual vendored generator, parent-summary
reinjection, pending-sibling revision, invalid native requests, host budgets,
empty-root rejection, and operation errors. A subprocess blocks imports of
`autoadapter2` as well as the upstream standalone OpenAI/Together/token-counting
SDKs. Standard and from-scratch task lifecycle checks use actual MuJoCo worlds
and recordings with explicitly named fixture models/drivers.

The real diagnostic uses the existing generated Piper driver at
`autoadapter_bench/diagnostics/capability_update_20260909/repair3_opus48_20260910/repair_2/piper/driver.py`.
It loads Piper's fixed task and parameters directly from `demo_tasks.yaml`:
the two listed checkpoints, then gripper opening 20% and 80%, in the configured
scene and `home` keyframe. Its existing Framework report only filters available
capabilities; private validation predicates are not supplied to the planner.
There is no manually supplied action plan or extra task decomposition instruction.

New real-run outputs are under `artifacts/recap_official_20260913/piper/`.
`integration_check.json` records actual execution of the vendored generator
while `autoadapter2` imports are blocked. This is a diagnostic integration run,
not a formal experiment or independent physical task-success measurement.

The official-source Piper run completed in 18.3 s with 7 model calls, 3 native
capability calls, no invalid plans, and 204 decoded video frames. Simulation
advanced from 0.500 s to 6.916 s. The tree contains the root and three action
nodes; each returned to the root for replanning. Multi-level non-leaf returns
were checked separately with a named fixture model, not claimed for this real
run. The initial, middle and final video frames were visually inspected. All
three native calls returned `EXECUTED`; `physical_task_success` remains `null`.
The focused controller/bridge, task-lifecycle and stage checks passed: 69 tests.
