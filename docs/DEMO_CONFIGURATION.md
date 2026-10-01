# Fixed DEMO inputs

`auto_adapter/demo_tasks.yaml` stores one fixed task, scene path, initial state,
public goal parameters and independent `success` specification per configuration
ID. Paths are relative to the repository root.
The YAML does not prescribe capability method names: task execution discovers
the current export's tools through MCP.

There are 17 robot families and 22 configuration IDs. Five additional entries
are existing model or scene variants: `menagerie_so101`, `so101_push`,
`piper_push`, `franka_push`, and bare `ur5e`. This inventory is configuration
coverage, not an experiment cohort or a count of independent robots.

| Inputs | Location |
| --- | --- |
| Fixed tasks, public parameters and initial states | `auto_adapter/demo_tasks.yaml` |
| Fixed DEMO success types, physical bindings and tolerances | Each configuration's `success` in the same YAML |
| Task-family physical calculations | `auto_adapter/demo_evaluation.py` |
| Nine preserved fixed DEMO scenes | `assets/mjcf/demo_scenes/<robot>/` |
| Other DEMO scenes | Existing base-model or push-scene paths named in the YAML |
| Public task requirements used by DESIGN | `auto_adapter/task_libraries/` |
| A run's generated criteria | `<workspace>/<robot>/design/capability_design.json` |
| That design's validation scenes and cases | `<workspace>/<robot>/design/scene_cases.yaml` and its referenced scene files |
| Validation measurements and pass/fail results | `<workspace>/<robot>/validate_report.json` and referenced case outputs |

The nine relocated scene sets preserve their XML contents, initial keyframes
and relative mesh references. They were copied from the retired fixed
validation scenes so removing that validation implementation does not remove
the configured DEMO environments. Base meshes remain shared.

G1 and Barkour use the actual model assets retained from the project's earlier
model import. Their `ASSET_NOTES.md` and bundled licenses record that source.
Both have 20-task public library bindings. G1 uses from-scratch generation;
Barkour can use the quadruped skeleton with an explicit MJCF path. They were
checked locally only; no G1/Barkour model-generation calls were made.

Each DEMO starts a fresh scene and then uses one persistent exported robot for
its action sequence. VALIDATION separately restores a fresh environment for
each generated case. Capability criteria belong to the current design and are
not duplicated in this fixed task YAML. DEMO success measures the complete
fixed task's physical goals, sequence and timing.

## Scoring handoff

```text
demo_tasks.yaml: task, parameters, scene, initial_state, success
  → recap._task_inputs() → task_execution.run_task(success_spec=...)
  → exported MCP server / ExportRuntime
  → DemoTrace records the same live MuJoCo model/data at each native step
  → demo_evaluation.evaluate_demo_task(success_spec, parameters, samples)
  → task_evaluation.json and task_report.json
```

Only the task description and public parameters are supplied to the ReCAP
planner. The evaluator does not inspect its claims or require particular MCP
method names. It uses named bodies, sites, geoms and scalar joints from the
configured scene, including local surface points where gripper geometry needs
them. Sampling is independent of renderer availability and video frame limits;
missing native steps or nonfinite state make the trace unscorable. The sampler
refreshes derived kinematics after integration without advancing another world.

Each run saves `physics_samples.jsonl` (simulation time, named physical state
and solver contact body pairs with their integration interval),
`runtime_report.json` (trace coverage and video),
`task_evaluation.json` (per-check values, thresholds and verdict), and
`task_report.json` (execution and physical task results). The report also saves
the actual parameters and success specification used for that run.

- `execution_ok` reports controller, MCP, simulation and video execution.
- `physical_task_success` is true/false only when the physical data can be
  evaluated; missing/invalid data yields null with `evaluation_error`.
- For a scored DEMO, `ok` requires both execution and physical task success.
  A DEMO failure does not change capability validation or trigger driver repair.
- An explicitly supplied standalone task may omit its success specification;
  its physical result remains null and its scope says no predicate was evaluated.
  Overriding the fixed task text does not reuse the old task's success rules.

Tolerances are manually authored diagnostic requirements, not calibrated
robot performance claims. Gripper fractions refer to measured geometric opening,
not actuator control fractions. SO101's original model has a visual moving jaw;
its opening check uses actual mesh tip points, not contact grasping performance.
Both SO101 waypoint configurations use 3 mm because their checkpoints are only
about 11 mm apart. KUKA's 20 mm offset uses a separate 5 mm tolerance; the
ordinary waypoint tolerance must not admit a short motion as the full offset.
Custom `demo_config_path` files also need a `success` specification matching
their task and scene. An old task's rules are not silently applied to a new task.

Local checks on 2026-09-14: all 22 scene/initial-state configurations loaded
through real MuJoCo; the focused configuration checks passed (28 tests).
G1 and Barkour also advanced 25 physics steps with finite state. These checks
do not establish generated-driver or physical task success.
