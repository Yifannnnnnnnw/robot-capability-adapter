# Fixed DEMO inputs

`auto_adapter/demo_tasks.yaml` stores one fixed task, scene path, initial state
and public goal parameters per configuration ID. Paths are relative to `AA1/`.
The YAML does not prescribe capability method names: task execution discovers
the current export's tools through MCP.

There are 17 robot families and 22 configuration IDs. Five additional entries
are existing model or scene variants: `menagerie_so101`, `so101_push`,
`piper_push`, `franka_push`, and bare `ur5e`. This inventory is configuration
coverage, not an experiment cohort or a count of independent robots.

| Inputs | Location |
| --- | --- |
| Fixed tasks, public parameters and initial states | `auto_adapter/demo_tasks.yaml` |
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
each generated case. DEMO execution status and video are not a physical task
verdict: until independent DEMO predicates are configured and evaluated,
`physical_task_success` remains `null`. Capability criteria belong to the
current design and are not duplicated in this fixed task YAML.

Local checks on 2026-09-14: all 22 scene/initial-state configurations loaded
through real MuJoCo; the focused configuration checks passed (28 tests).
G1 and Barkour also advanced 25 physics steps with finite state. These checks
do not establish generated-driver or physical task success.
