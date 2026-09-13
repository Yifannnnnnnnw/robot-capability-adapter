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

`agent/recap.py` is the local task controller. `submit_plan` replaces the current
node's ordered remaining plan. Only its first item executes: an abstract
subtask opens a child; a capability leaf crosses the validated
`driver.method(request=...)` boundary. After observations, the model revises
the current plan. An empty child plan returns to its parent for replanning.
An empty root plan ends the controller after at least one executed capability.
The model can also use capability leaves directly in a node.

All nodes share 16 planning turns, 12 capability calls and a maximum depth of 6.
Budget exhaustion, malformed plans and runtime failures are explicit results.
Ordinary method errors return to the model for replanning. The controller does
not call `ReactLoop.run()`; generation and export retain their existing ReAct
loops and the task bridge reuses only AA1's model transport.

The task directory contains `task_report.json`, `trace.jsonl`,
`model_turns.jsonl`, copied inputs and `video.mp4` when recording is available.
The report distinguishes controller status, actual method calls, method
errors, observations and simulation advancement. `ok` records controller and
recording completion, not independent physical success.
`physical_task_success` remains `null` because no task predicate is evaluated.

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

## Migration check, 2026-09-13

Focused checks cover recursion, request rejection, budget termination, standard
and scratch stage dispatch, current-design forwarding, repair gating, world
isolation, recordings, and all 15 actual scene/initial-state loads. A stale
standard-export artifact was also reproduced as a false success, then rejected
by a focused regression after fixing the export boundary.

Real diagnostic outputs live under `artifacts/recap_migration_20260913/`.
Piper uses the existing generated driver from
`repair3_opus48_20260910/repair_2/piper`, with the real Holistic model and MuJoCo.
The recursive diagnostic explicitly requests two abstract planning groups while
keeping the approved physical goal and parameters. No formal experiment or
physical success claim is made.

The final `piper_recursive` run completed in 26.9 s: 8 planning turns, 3
capability calls, 3 context-tree nodes and 204 decoded video frames. Its trace
is `n0 → n1 → n0 → n2 → n0`: checkpoint traversal in the first child, gripper
close/open in the second child, with parent replanning after each return.
All three calls returned normally. Simulation advanced from 0.500 s after
initial settling to 6.916 s. The report retains `physical_task_success: null`.
Core/stage/task checks passed (73 tests), as did the fixed-input checks
(21 tests); the video requires an available local offscreen graphics context.

AA1 task execution requires no root `autoadapter/` code or installed
`autoadapter2` package. The real diagnostic installs an import blocker for that
package. The separate retained `AutoAdapter-Bench/runners/manifest.py` still
reads `autoadapter/libraries/robots`; deleting the old directory would disable
those old B1/B2 manifest commands. This migration does not modify that runner
or delete the old directory.
