# Fixed Driver Benchmark v1

This document fixes one small, source-traceable robot-driver interface for the
eleven-robot benchmark cohort. The machine-readable authority for this slice is
[`configs/benchmarks/fixed_driver_v1.json`](configs/benchmarks/fixed_driver_v1.json).

This is a fixed-interface benchmark slice, not a replacement for formal
Task-Grounded Capability Design. A full mainline run still requires the model to
derive 5-10 capabilities from the complete 20-task public library. Results from
this fixed slice must not be reported as full TGCD, complete Task Library
coverage, or Task Demo success.

## Driver contract

Every experimental implementation is an isolated, model-generated `driver.py`.
It exposes `build(model, data)` and the exact methods below through the stable
`method(request=request)` ABI. The package-local reference control is a hidden
positive control used only to calibrate the scene, Harness, and video path. Its
source is never model input and it is not the experimental driver.

| Robot configuration | Fixed generated-driver methods | Hidden reference positive control |
| --- | --- | --- |
| SO-101 (`robotstudio_so101`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceSO101Driver`, Framework-rendered adapter |
| Unitree Go2 (`unitree-go2-stock-12dof`) | `calibrate_planar_motion`, `calibrate_step_transition`, `calibrate_course_navigation`, `calibrate_rough_terrain`, `calibrate_parkour_obstacles`, `calibrate_parkour_course` | `ReferenceGo2Driver`, Framework-rendered adapter |
| Franka Panda (`franka_panda`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceFrankaPandaDriver`, Framework-rendered adapter |
| Kinova Gen3 + Robotiq 2F-85 (`kinova_gen3_robotiq_2f85`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceKinovaGen3Robotiq2F85Driver`, Framework-rendered adapter |
| UFACTORY xArm7 (`ufactory_xarm7`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceUfactoryXarm7Driver`, Framework-rendered adapter |
| UR5e + Robotiq 2F-85 (`universal_robots_ur5e_robotiq_2f85`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceUR5eRobotiq2F85Driver`, Framework-rendered adapter |
| Piper (`piper`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferencePiperDriver`, Framework-rendered adapter |
| KUKA iiwa 14 (`kuka_iiwa_14`) | `reach_task`, `contact_task`, `fixture_task`, `rotation_task` | `ReferenceKukaIiwa14Driver`, Framework-rendered adapter |
| LEAP Hand (`leap_hand`) | `ec_task`, `reach_task`, `block_task`, `pose_task`, `fixture_task`, `hold_task`, `baoding_task` | `ReferenceLeapHandDriver`, direct fixed interface |
| Hello Robot Stretch 2 (`hello_robot_stretch_2`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceStretch2Driver`, Framework-rendered adapter |
| ALOHA 2 (`aloha_2`) | `reach_task`, `contact_task`, `object_task`, `fixture_task`, `rotation_task` | `ReferenceAloha2Driver`, Framework-rendered adapter |

KUKA has no `object_task` in this slice because the declared canonical
configuration has no gripper. Its four-method slice is not a claim that a formal
KUKA TGCD design may ignore the mainline 5-10 capability requirement.

The two Go2 `parkour_*` methods refer to source-derived tasks inside the Go2
Task Library. They do not add the optional `google_barkour_vb` robot to the
cohort; that robot and Unitree G1 remain research backups.

## Arm standards

Eight gripper-equipped configurations use all five rows. KUKA uses the four
rows other than `object_task`.

| Method | Selected public source clause | Original source gate | Fixed benchmark gate |
| --- | --- | --- | --- |
| `reach_task` | `mw_reach_target / terminal_endpoint` | terminal end-effector-to-target distance `<= 0.050 m` | `<= 0.020 m` |
| `contact_task` | `mw_push_to_goal / object_goal_distance` | terminal object-to-goal distance after pushing `<= 0.050 m` | `<= 0.020 m` |
| `object_task` | `mw_pick_place / placed_object_distance` | terminal object-to-goal distance after release `<= 0.070 m` | `<= 0.030 m` |
| `fixture_task` | `mw_drawer_open / open_handle_error` | terminal drawer-handle-to-open-target distance after pulling `<= 0.030 m` | `<= 0.015 m` |
| `rotation_task` | `mw_faucet_open / faucet_target_distance` | terminal faucet-handle-to-target distance after rotation `<= 0.070 m` | `<= 0.030 m` |

The original values are copied exactly from the selected public Task Library
clauses and remain separately reportable source-standard results. The fixed
benchmark values are stricter, pre-registered study thresholds approved before
the model experiments. They replace only the numerical bound: metric semantics,
unit, comparator, temporal rule, aggregation, and source lineage remain
unchanged. They are not industrial tolerances and are not estimated from
reference-driver outcomes.

## Go2 standards

| Method | Selected public source clause | Exact capability gate |
| --- | --- | --- |
| `calibrate_planar_motion` | `GO2-T01 / tracking_speed` | mean planar body speed `>= 0.4 m/s` at the terminal evaluation |
| `calibrate_step_transition` | `GO2-T02 / step_ascent_completion` | all four feet complete the step within `10.0 s` (`== 1`) |
| `calibrate_course_navigation` | `GO2-T06 / mixed_course_completion` | ordered obstacle completion ratio eventually equals `1.0` |
| `calibrate_rough_terrain` | `GO2-T12 / travel_distance` | mean maximum travel distance `>= 4.983 m` at terminal evaluation |
| `calibrate_parkour_obstacles` | `GO2-T16 / table_step_off` | distance from the start-table centre eventually reaches `>= 0.7 m` |
| `calibrate_parkour_course` | `GO2-T20 / course_order` | ordered course completion ratio eventually equals `1.0` |

## LEAP Hand standards

| Method | Selected public source clause | Exact capability gate |
| --- | --- | --- |
| `ec_task` | `ec_pinch / three_source_pattern_successes` | exactly `3` successful source-pattern trials in exactly `3` attempts |
| `reach_task` | `gym_hand_reach_all_fingertips / all_fingertip_goal_error` | concatenated four-fingertip Cartesian L2 error `< 0.00894427191 m` at step `50` |
| `block_task` | `gym_hand_manipulate_block_full_pose / block_position_error` | block position error `< 0.01 m` at step `100`, under the source same-state conjunction semantics |
| `pose_task` | `robel_dclaw_pose_fixed / maximum_joint_pose_error` | maximum absolute error over all 16 joints `< 0.1745329252 rad` at step `80` / `4 s` |
| `fixture_task` | `robel_dclaw_turn_fixed / fixture_target_angle_error` | absolute wrapped fixture target-angle error `< 0.1 rad` at step `40` / `4 s` |
| `hold_task` | `myosuite_object_hold_fixed / minimum_solved_steps` | object remains within the source solved radius for more than `5` steps in a `75`-step horizon |
| `baoding_task` | `myosuite_baoding_p1 / perfect_two_ball_tracking_fraction` | mean two-ball solved fraction equals `1.0` over `200` steps |

## Verdict rule

A numerical task metric never passes a capability by itself. The trusted
Harness must also pass every private physical-integrity guard for that case,
including canonical model/data use, actuator-driven MuJoCo stepping, prohibition
of direct state writes, and the applicable contact, support/release, fixture, or
collision-integrity checks. This is the boundary that distinguishes metric
success from physical success and rejects interpenetration-based false success.

A robot passes only when every selected capability passes. For the five arm
operations, this means the fixed benchmark gate rather than only the looser
source gate; the source result is still recorded separately. Go2 and LEAP Hand
retain their selected source gate until a separate domain-specific strict gate
is approved. The eleven-robot slice passes only when every robot passes. A
formal evidence claim additionally requires one complete Framework-controlled
video for every executed case. A candidate return value or self-reported
success is never evidence.

Only the selected primary clause underlies each capability gate listed here.
Other clauses and tasks remain in the frozen public Task Library; when a full
original task is selected for Task Demo, all of that task's scoring clauses are
evaluated.
