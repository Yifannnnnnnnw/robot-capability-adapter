# AutoAdapter 2.0 General Demo Tasks Library

This directory is the canonical Tasks Library for the two-robot General Demo. The project authority
is [`AUTOADAPTER_2_AUTHORITY.md`](../../../AUTOADAPTER_2_AUTHORITY.md); this Library owns downstream
Demo tasks, not capability Validation B standards.

## Information boundary

Each robot package intentionally separates four artifacts:

| Artifact | Contents | Permitted recipient |
|---|---|---|
| `catalog.json` | Approved task identity, description, type, provenance, and reviewed metadata | Researchers; Framework task selector |
| `demo_collection.json` | The exact ordered five-task Demo subset | Framework run freezer |
| `stage1_projection.json` | Internal fixed-five template (`task_id` + description); the Framework replaces `task_id` with a run-local opaque requirement ref | Framework run-input assembler only |
| `evaluation_private.json` | Approved pass criteria, physical definitions, and guards | Trusted Demo Evaluation Harness only |

The Go2 catalog contains 26 human-approved task descriptions for the exact stock 12-DoF
configuration. The first Demo collection remains an independent, frozen selection of only
`G01`–`G05`; catalog approval does not add `G06`–`G26` to Stage 1 or the first Demo. Their approved
semantic criterion templates remain non-executable until a future task selection separately
freezes every required numerical parameter, scene asset, observation, safety rule, and aggregation.

The runtime Stage 1 projection contains exactly `requirement_id` and `description`; its opaque
requirement IDs are derived for that run and do not reveal `T01`, `G01`, robot identity, source, or
fixed-Demo membership. Private criteria are never inputs to Stage 1, Stage 2, generated `capability.py`, Repair, or the
Demo Consumer. The Blue Line validation-reference library is also a different collection: it
governs capability validation and neither owns nor replaces the downstream tasks here.

## Experimental AutoAdapter 1.0 migration population

The following six packages are source-backed experimental candidates and are **2026-08-12
user-approved Library task packages for this exact experimental configuration/task package**. Their
loader-compatible catalog fields remain in the existing schema, they remain on the
DIRECT_MUJOCO_EXPERIMENTAL route, and their exact five-task packages are indexed for explicit
loader/Stage 1/Demo selection. This approval does not automatically add any of them to the first
two-robot fixed Demo; that fixed selection remains the SO-ARM101 and Go2 packages described below.
It also does not create a real SDK record.

| Configuration | Source-backed fixed five | MJCF scene facts |
|---|---:|---|
| franka_panda | F01–F05 | panda.xml + pushbench.xml; red/green/blue cubes and obstacle |
| kuka_iiwa_14 | K01–K05 | iiwa14.xml + scene.xml; attachment-site pose suite |
| piper | P01–P05 | piper.xml + pickbench.xml/pushbench.xml; three cubes |
| universal_robots_ur5e | U01–U05 | ur5e.xml + scene.xml; attachment-site pose suite |
| pushbench | B01–B05 | inline SO101 pushbench; T-shape, three cubes, obstacle |
| robotstudio_so101 | S01–S05 | so101.xml + scene_box.xml; free box and gripper frame |

Each migration package has catalog.json, stage1_projection.json, evaluation_private.json,
demo_collection.json, and task_instances_private.json. The corresponding morphology record
and general_demo/libraries/assets/<configuration>/1.0.0/asset_closure.json pin the upstream
repository, commit, entry MJCF SHA-256, recursive include/mesh paths, and per-file hashes. Small
XMLs are vendored for inspection; large meshes remain an explicitly declared upstream-cache
requirement and are not copied into this repository.

## Current population

| Exact robot configuration | Approved catalog | Fixed Demo set | Approved but outside fixed Demo |
|---|---:|---:|---:|
| `so-arm101-follower-stock-gripper` | 28 | 5 | 23 |
| `unitree-go2-stock-12dof` | 26 | 5 | 21 |

The two robots run separately, with one robot configuration and one five-task collection per run.
All five selected tasks execute; none is described as held out.

## SO-ARM101 approved catalog

Configuration: fixed base, five arm joints, stock gripper, no assumed camera. Required target and
scene facts must be supplied through the run's approved public observation interface. The full
approved source metadata, difficulty rationale, and all 28 private criteria are in the versioned
JSON package.

| ID | Task description | Type | Difficulty | Fixed Demo |
|---|---|---|---|---|
| T01 | Move the gripper reference point to the specified 3-D target and stop. | Reach | D1 | Yes |
| T02 | Touch the specified face of the target object without moving the object. | Controlled contact | D1 | Yes |
| T03 | Push the cube into the specified planar goal region. | Planar pushing | D1 | Yes |
| T04 | Push the cylinder laterally into the specified goal region while keeping it upright. | Oriented pushing | D2 | No |
| T05 | Roll the ball into the specified circular goal region. | Dynamic planar manipulation | D2 | No |
| T06 | Rotate the rectangular block to the specified planar orientation without moving it outside its local region. | Planar reorientation | D3 | No |
| T07 | Push the T-shaped block until it matches the target silhouette. | Precision planar pose | D4 | No |
| T08 | Grasp the target cube and hold it securely just above the table. | Grasp and hold | D2 | Yes |
| T09 | Lift the target cube to the specified height and hold it there. | Lift | D2 | No |
| T10 | Move the grasped cube to the specified 3-D goal and keep holding it. | Grasped transport | D3 | No |
| T11 | Place the cube on the specified planar target and release it. | Pick and place | D3 | No |
| T12 | Place the cube completely inside the shallow tray and release it. | Containment placement | D3 | No |
| T13 | Place the sphere inside the shallow bin and release it. | Dynamic containment | D3 | No |
| T14 | Place the cylinder upright on the specified target and release it. | Oriented placement | D3 | No |
| T15 | Stack the target cube on top of the base cube and release it. | Stacking | D4 | No |
| T16 | Raise the horizontal peg to an upright pose and release it on the table. | Object reorientation | D4 | No |
| T17 | Place the square ring over the specified square peg and release it. | Peg-and-ring assembly | D4 | No |
| T18 | Insert the specified end of the peg into the side hole. | Side insertion | D4 | No |
| T19 | Put the specified shape into its matching sorter slot and release it. | Shape insertion | D4 | No |
| T20 | Press the specified large button until it activates. | Button actuation | D2 | Yes |
| T21 | Open the shallow drawer by its handle. | Articulated fixture | D4 | No |
| T22 | Push the shallow drawer fully closed. | Articulated fixture | D2 | No |
| T23 | Slide the tabletop door to its fully open position. | Sliding fixture | D3 | No |
| T24 | Grasp the lid handle and remove the lid from the container. | Handle removal | D3 | No |
| T25 | Place each of the two specified objects into its matching tray. | Two-object sorting | D4 | No |
| T26 | Place object A into the tray, then place object B into the tray. | Ordered manipulation | D4 | No |
| T27 | Put the specified object inside the open drawer and then close the drawer. | Compound fixture task | D4 | No |
| T28 | Use the provided tool to pull the initially unreachable cube into the specified reachable region. | Tool use | D4 | No |

SO task concepts were adapted from the [prior AutoAdapter benchmark](https://github.com/981526092/auto-adapter/tree/585eb1f1fde33f17f5f9a1e169a18dd41f97b586),
[RLBench](https://github.com/stepjam/RLBench/tree/master/rlbench/tasks),
[ManiSkill](https://maniskill.readthedocs.io/en/latest/tasks/table_top_gripper/index.html),
[LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO),
[CALVIN](https://github.com/mees/calvin), and
[RoboCasa](https://robocasa.ai/docs/build/html/tasks/atomic_tasks.html). Source provenance does not
transfer an upstream difficulty or evaluation protocol.

### SO-ARM101 fixed five and approved private criteria

| ID | Harness-private success criterion |
|---|---|
| T01 | Tip-position error ≤ 20 mm and tip speed ≤ 10 mm/s continuously for 0.5 s. |
| T02 | Intended tip-face contact lasts ≥ 0.3 s; target-object displacement ≤ 10 mm; no other object is contacted. |
| T03 | Cube-center planar error ≤ 25 mm; cube remains table-supported; cube linear speed ≤ 0.010 m/s and angular speed ≤ 0.10 rad/s continuously for 1.0 s. |
| T08 | Cube rises ≥ 20 mm; gripper-relative slip ≤ 5 mm over 1.0 s; cube remains held. |
| T20 | Specified-button displacement ≥ 3 mm and activation lasts ≥ 0.25 s; no other button is activated. |

## Unitree Go2 approved catalog

Configuration: free base, stock 12-DoF Go2, joint/IMU state, no camera/depth/lidar. Official
`SportClient` methods are task-concept provenance only. The AutoAdapter implementation must
synthesize stand, sit, movement, and posture behavior from the admitted `LowCmd`/`LowState` route.
All task descriptions, exact-configuration applicability decisions, qualitative difficulty
rationales, and semantic criterion templates are human approved. No ordinal Go2 difficulty level
is assigned, and no absent numerical threshold is inferred.

### Fixed first-Demo selection

| ID | Task description | Type |
|---|---|---|
| G01 | Stand up from the specified supported starting posture and hold a stable upright stance. | Posture transition |
| G02 | Sit down from a stable standing posture and hold the supported sitting posture without falling. | Posture transition |
| G03 | Maintain a stable upright stance at the current location for the requested interval. | State maintenance |
| G04 | Move forward in the robot's initial heading direction, then stop in a stable upright stance. | Bounded locomotion |
| G05 | Adjust the robot body to the specified target height and hold it upright at the current location. | Body-height control |

Common private definitions: `upright_score` is body-z dot world-z; forward/lateral displacement is
measured in the initial-body-yaw frame; `standing_height` is a trusted frozen reference captured
before the trial; NaN/Inf, missing required physical state, or body/head floor contact fails; a
model report or command receipt is never physical success evidence.

| ID | Harness-private success criterion |
|---|---|
| G01 | Body height > 0.15 m, upright score ≥ 0.7, and planar speed ≤ 0.05 m/s continuously for 1.0 s; no body/head floor contact. |
| G02 | Body height < 0.8 × frozen standing height, upright score ≥ 0.7, and planar speed ≤ 0.05 m/s continuously for 1.0 s; a fallen posture cannot pass. |
| G03 | Body height > 0.15 m, upright score ≥ 0.7, and horizontal drift ≤ 0.05 m continuously over 2.0 s; no body/head floor contact. |
| G04 | Forward displacement > 0.03 m; during motion body height > 0.12 m and upright score ≥ 0.7; absolute lateral displacement ≤ 0.05 m; final planar speed ≤ 0.05 m/s continuously for 0.5 s; no body/head floor contact. |
| G05 | Absolute body-height error ≤ 0.03 m, upright score ≥ 0.7, and horizontal drift ≤ 0.05 m continuously for 1.0 s; no body/head floor contact. |

### Additional approved Go2 records — outside the first Demo

The 21 entries below are approved catalog records for the same Go2 configuration. They remain
outside `demo_collection.json` and `stage1_projection.json`, so they cannot enter the first Demo.
Their Harness-private semantic criteria are approved, but their numerical and executable scene
parameters are deliberately unset; a future reviewed Demo collection must freeze those parameters
before execution.

| ID | Approved task description | Type |
|---|---|---|
| G06 | Move backward in the robot's initial heading frame, then stop in a stable upright stance. | Bounded locomotion |
| G07 | Move left in the robot's initial heading frame, then stop in a stable upright stance. | Bounded locomotion |
| G08 | Move right in the robot's initial heading frame, then stop in a stable upright stance. | Bounded locomotion |
| G09 | Turn in place to the specified yaw heading and stop upright. | Yaw control |
| G10 | Track the specified forward, lateral, and yaw velocity command for the requested interval. | Velocity tracking |
| G11 | Visit the specified ordered sequence of planar waypoints and finish upright at the final waypoint. | Waypoint following |
| G12 | Stop from the specified ongoing planar motion and settle into a stable upright stance. | Motion arrest |
| G13 | Adjust to the specified body roll while maintaining support and location. | Body-attitude control |
| G14 | Adjust to the specified body pitch while maintaining support and location. | Body-attitude control |
| G15 | Recover from the specified permitted fallen starting pose to a stable upright stance. | Fall recovery |
| G16 | Maintain and recover the specified stance after the declared external planar disturbance. | Disturbance rejection |
| G17 | Traverse the specified irregular rough-terrain segment and finish upright in the goal region. | Rough-terrain locomotion |
| G18 | Ascend the specified slope and finish upright on its goal platform. | Slope traversal |
| G19 | Descend the specified slope and finish upright on its goal platform. | Slope traversal |
| G20 | Traverse the specified staircase and finish upright on its goal platform. | Stair traversal |
| G21 | Traverse the specified sparse box-and-step field and finish upright in the goal region. | Discrete obstacle terrain |
| G22 | Weave through the specified ordered poles and stop upright in the goal region. | Agility slalom |
| G23 | Traverse the specified A-frame obstacle and stop upright in the goal region. | Agility ramp |
| G24 | Cross the specified broad-jump obstacle and land in a stable upright state in the goal region. | Agility jump |
| G25 | Cross the specified high obstacle and land in a stable upright state in the goal region. | Agility high obstacle |
| G26 | Complete the specified ordered quadruped agility course and stop upright in the final goal region. | Compound agility course |

Primary research links:

- [Unitree SDK2 Python examples](https://github.com/unitreerobotics/unitree_sdk2_python/tree/65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5/example)
- [Unitree Go2 SportClient interface](https://github.com/unitreerobotics/unitree_sdk2/blob/main/include/unitree/robot/go2/sport/sport_client.hpp)
- [Unitree Go2 low-level stand example](https://github.com/unitreerobotics/unitree_sdk2/blob/main/example/go2/go2_stand_example.cpp)
- [Unitree RL Lab Go2 velocity/terrain environment](https://github.com/unitreerobotics/unitree_rl_lab/blob/main/source/unitree_rl_lab/unitree_rl_lab/tasks/locomotion/robots/go2/velocity_env_cfg.py)
- [Unitree MuJoCo](https://github.com/unitreerobotics/unitree_mujoco/tree/ae6a8403e272733e9996ef59990880330496177f)
- [ETH legged_gym](https://github.com/leggedrobotics/legged_gym)
- [DeepMind Control Suite quadruped tasks](https://github.com/google-deepmind/dm_control/blob/master/dm_control/suite/README.md)
- [DeepMind Barkour repository](https://github.com/google-deepmind/barkour_robot) and [paper](https://arxiv.org/abs/2305.14654)
- [Extreme Parkour paper](https://arxiv.org/abs/2309.14341)

## Versioned files

```text
tasks/
├── index.json
├── README.md
├── so-arm101-follower-stock-gripper/1.0.0/
│   ├── catalog.json
│   ├── demo_collection.json
│   ├── stage1_projection.json                         # internal fixed-five projection template
│   └── evaluation_private.json
└── unitree-go2-stock-12dof/1.0.0/
    ├── catalog.json
    ├── demo_collection.json
    ├── stage1_projection.json                         # internal fixed-five projection template
    └── evaluation_private.json
```
