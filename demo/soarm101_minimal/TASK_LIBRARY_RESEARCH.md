# SO-ARM101 P0 Task-Library Research and Adaptation Record

> Status: task-schema and split design complete; physical scene/reachability probes still gate Demo freeze.
> Research date: 2026-08-04.
> Scope: the task and empty Experience input libraries for `demo/soarm101_minimal`.

## Outcome

The P0 library contains twelve locally authored SO-ARM101 tabletop templates:

- nine are visible to the continuous Generation Agent;
- three are private `pilot-held-out` templates;
- exact task instances, MuJoCo reset state, and all Oracle criteria are private;
- the Consumer receives no image but does receive the object/target geometry and coordinates explicitly required by the selected template;
- a six-task Demo batch is fixed before generation: three visible and all three pilot-held-out templates.

This is a systems-pipeline corpus. Its results must not be presented as LIBERO, LIBERO-PRO, RLBench, CALVIN, ManiSkill, or RoboCasa results, nor as formal held-out evidence.

## Primary sources and what was adapted

Only official project repositories and documentation were used. The complete machine-readable list is in [`sources.yaml`](libraries/tasks/soarm101_tabletop/v1/sources.yaml).

| Source | Direct evidence used | P0 adaptation |
|---|---|---|
| [LIBERO official repository](https://github.com/Lifelong-Robot-Learning/LIBERO) and [procedural-generation docs](https://lifelong-robot-learning.github.io/LIBERO/html/procedural_generation/task_generation.html) | LIBERO separates controlled spatial/object/goal shifts, represents task structure beyond language alone, and evaluates from fixed initial states. | Separate language, structured scene facts, initial-state reference, goal predicates, and generalization axes. |
| [LIBERO-PRO official repository](https://github.com/Zxy-MLlab/LIBERO-PRO) | It names object, position, semantic, task, and environment as distinct generalization dimensions. | Record all five axes on every template and describe each held-out change explicitly. |
| [RLBench official task library](https://github.com/stepjam/RLBench/tree/master/rlbench/tasks), including [ReachTarget](https://github.com/stepjam/RLBench/blob/master/rlbench/tasks/reach_target.py) and [StackBlocks](https://github.com/stepjam/RLBench/blob/master/rlbench/tasks/stack_blocks.py) | A task can have variations, multiple descriptions, and composable success conditions. RLBench also warns that swapping arms does not guarantee solvability. | Keep paraphrases with their template group, represent success as private predicate conjunctions, and require a SO-ARM101 reachability probe before freezing. |
| [CALVIN official repository](https://github.com/mees/calvin) | CALVIN evaluates ordered sequences of language-conditioned manipulation goals and resets the robot before independent sequences. | Include ordered two-object composite templates and reset world, runtime, and Consumer context per Demo task. |
| [ManiSkill official tabletop task cards](https://maniskill.readthedocs.io/en/latest/tasks/table_top_gripper/index.html) and [official task source tree](https://github.com/haosulab/ManiSkill/tree/main/mani_skill/envs/tasks/tabletop) | The task cards cover pick/move, push, receptacle placement, and stacking with geometric terminal conditions; placement/stacking often require a static and released end state. | Cover reach, push, grasp/hold, lift, planar place, receptacle place, stack, and composite families; use locally chosen geometric tolerances plus released/static predicates. |
| [RoboCasa foundation-model benchmark docs](https://robocasa.ai/docs/build/html/benchmarking/foundation_model_learning.html) and [official repository](https://github.com/robocasa/robocasa) | The benchmark distinguishes Atomic-Seen, Composite-Seen, and Composite-Unseen task sets. | Select the visible Demo subset across atomic and composite tasks, and include a private composite-logic shift. |

## Adaptation and licensing boundary

No external code, task file, BDDL, scene, asset, waypoint, demonstration, policy, coordinate range, initial state, or Oracle threshold is included. The library borrows only high-level task-design ideas and records a direct provenance link.

All twelve descriptions, JSON records, SO-ARM101 coordinates, object/receptacle geometry, split decisions, similarity features, Demo selection, and Oracle values were authored for this project. The cited projects use different robots, simulators, sensing assumptions, workspaces, and evaluation protocols. In particular, P0 replaces their usual vision-based observation path with explicit structured coordinates, so apparent task-name similarity does not imply benchmark equivalence.

The source-specific license notes in `sources.yaml` are provenance metadata, not an attempt to relicense any upstream material.

## Structured input contract

The common convention is defined in [`taxonomy.yaml`](libraries/tasks/soarm101_tabletop/v1/taxonomy.yaml):

- right-handed `robot_base` coordinates;
- metres for positions and radians for angles;
- `x` forward, `y` left, `z` upward;
- table surface at `z=0.02 m`;
- conservative P0 authoring envelope `x=[0.22,0.46]`, `y=[-0.14,0.14]`, `z=[0.02,0.22]`.

Every template lists exact required paths such as:

```text
objects.red_cube.position_m
objects.red_cube.size_m
receptacles.tray.center_m
receptacles.tray.inner_size_m
targets.push_region.center_m
targets.push_region.radius_m
```

At Demo time, the framework loads the private instance and gives the Consumer only its `agent_input` object plus the natural-language instruction. Raw MuJoCo state, contacts, velocities, Oracle definitions, hidden split metadata, and target tolerances remain private.

## Twelve-template catalog

| Partition | Task ID | Family / level | Intended coverage |
|---|---|---|---|
| visible | `soarm101_p0_reach_target_pose` | reach / atomic | Cartesian reach |
| visible | `soarm101_p0_push_cube_to_region` | push / atomic | forward prismatic push |
| visible | `soarm101_p0_grasp_cube_hold` | grasp-hold / atomic | stable pinch grasp |
| visible | `soarm101_p0_lift_cube_by_height` | lift / atomic | grasped vertical displacement |
| visible | `soarm101_p0_place_cube_on_target` | pick-place / atomic | planar placement and release |
| visible | `soarm101_p0_place_cube_in_tray` | pick-place / atomic | cube containment |
| visible | `soarm101_p0_place_cylinder_in_tray` | pick-place / atomic | round-object containment and uprightness |
| visible | `soarm101_p0_stack_red_on_green` | stack / atomic | support, alignment, release, stability |
| visible | `soarm101_p0_place_two_objects_in_tray` | composite / composite | ordered repeated placement |
| pilot-held-out | `soarm101_p0_push_cylinder_lateral` | push / atomic | near shift: shape plus push direction |
| pilot-held-out | `soarm101_p0_place_cube_in_bowl_new_region` | pick-place / atomic | medium shift: source region plus receptacle relation |
| pilot-held-out | `soarm101_p0_sort_two_cubes_matching_trays` | composite / composite | farther shift: matching logic plus two targets |

## Similarity, grouping, and split

The split unit is a `similarity_group`, not an instruction string or rollout seed. Each semantic template, all its paraphrases, and all future seeded realizations must stay together. The fixed seed is `10120260804`; the selected result is 9 visible and 3 pilot-held-out.

Structured distance is:

```text
0.35 * normalized skill-sequence edit distance
+ 0.25 * goal-predicate Jaccard distance
+ 0.15 * object-affordance Jaccard distance
+ 0.15 * spatial/receptacle Jaccard distance
+ 0.10 * horizon/precision ordinal distance
```

[`similarity_matrix.json`](private/task_library/soarm101_tabletop/v1/similarity_matrix.json) stores the full 12×12 matrix and all five components for all 66 unordered pairs. Its cross-split audit confirms no template/paraphrase or similarity-group overlap. The nearest visible distances for the three held-out templates are respectively `0.300`, `0.275`, and `0.375`; the differences are intentional generalization probes, not accidental duplicate instructions.

Text embeddings are deliberately non-authoritative. A later loader may use them to flag paraphrases for human review, but they cannot move a template across the split.

## Fixed Demo batch

[`demo_batch.json`](private/task_library/soarm101_tabletop/v1/demo_batch.json) interleaves:

1. visible cube push;
2. pilot-held-out lateral cylinder push;
3. visible cube-to-tray placement;
4. pilot-held-out cube-to-bowl placement;
5. visible ordered two-object placement;
6. pilot-held-out two-cube matching sort.

Each task resets MuJoCo, the LeRobot-compatible runtime, the separate Demo ReAct Agent context, and that task's 30-call counter. Demo failures never return to Generation or repair.

## Private initial states and Oracles

The private partition contains one concrete instance per template plus a common robot/table reset. The robot reset candidate comes from the predecessor SO-101 tabletop MJCF keyframe; it is private and must be compiled and checked in the new bridge rather than treated as trusted runtime truth.

[`task_oracles.yaml`](private/task_library/soarm101_tabletop/v1/task_oracles.yaml) defines local pass conditions for geometric error, containment/support, grasp/contact, release, stability, ordering, safety, and timeout. The tolerances are authored starting values. Before the Demo is frozen, the bridge/harness must:

1. compile every object and receptacle;
2. prove the reset state stable and collision-free;
3. probe source, approach, transit, and target reachability;
4. test each Oracle at known pass/fail/boundary states;
5. repeat scripted trajectories to identify unstable thresholds;
6. freeze and hash the resulting private files in the run manifest.

Until these checks pass, `authored_requires_reachability_probe` means the data format is ready but the physical task instance is not yet a validated benchmark instance.

## Hash reproducibility

Single-file entries use lowercase SHA-256 over raw bytes. Bundle hashes are calculated by sorting paths relative to the declared bundle root and, for each file, appending:

```text
UTF-8 relative path + NUL + lowercase raw-file SHA-256 + newline
```

The SHA-256 of that concatenation is the bundle hash. The public split manifest exposes counts and content hashes but not held-out instructions, IDs, initial states, or Oracle definitions to Generation.
