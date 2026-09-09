# Quadruped Task Suite Design for Auto-Adapter

Grounding first, before proposing anything:

- The current quadruped benchmark is only five tasks in two tiers, all in [autoadapter_bench/spec/tasks_quadruped.yaml](autoadapter_bench/spec/tasks_quadruped.yaml:7). The five task ids are `stand_up`, `sit`, `report_pose`, `stand_sit_stand`, and `walk_forward_1s` [autoadapter_bench/spec/tasks_quadruped.yaml](autoadapter_bench/spec/tasks_quadruped.yaml:12).
- The current quadruped evaluator is not yet arm-suite-rigorous. `walk_forward_1s` accepts `min_m: -0.5`, i.e. effectively any forward displacement as long as the robot does not fall [autoadapter_bench/spec/tasks_quadruped.yaml](autoadapter_bench/spec/tasks_quadruped.yaml:50). `multi_phase_height_trajectory` is graded by counting `stand_up` and `sit` calls rather than measuring actual phase trajectories [autoadapter_bench/eval.py](autoadapter_bench/eval.py:630). `llm_reports_pose` is graded by keyword match in the model summary [autoadapter_bench/eval.py](autoadapter_bench/eval.py:644).
- What matters for "can the agent do this now?" is the agent-visible tool surface, not just raw public methods. For from-scratch quadrupeds, `TaskPlanner` dispatches them to the curated quadruped registry [auto_adapter/agent/task_planner.py](auto_adapter/agent/task_planner.py:378). That registry currently exposes only `stand_up`, `walk_forward`, `sit`, `get_body_height`, and `get_base_pose` [auto_adapter/agent/task_planner.py](auto_adapter/agent/task_planner.py:156).
- Eval actually loads `driver.py`, and all three quadruped `driver.py` files are just thin wrappers around `driver_from_scratch.Robot`: Go2 [artifacts/auto_adapter_from_scratch_go2_artifacts/driver.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver.py:1), A1 [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver.py:1), ANYmal-C [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver.py:1).
- Raw public methods on Go2 `driver_from_scratch.py` are `get_joint_positions` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:166), `get_joint_velocities` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:175), `step` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:185), `render` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:195), `stand_up` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:259), `sit` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:277), `walk_forward` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:295), `get_body_height` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:357), and `get_base_pose` [artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_go2_artifacts/driver_from_scratch.py:366).
- Raw public methods on A1 `driver_from_scratch.py` are `get_joint_positions` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:152), `get_joint_velocities` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:161), `step` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:182), `render` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:192), `get_body_height` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:239), `get_base_pose` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:248), `stand_up` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:300), `sit` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:313), and `walk_forward` [artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_unitree_a1_artifacts/driver_from_scratch.py:326).
- Raw public methods on ANYmal-C `driver_from_scratch.py` are `stand_up` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:125), `sit` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:137), `walk_forward` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:149), `get_joint_positions` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:236), `get_body_height` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:245), `get_base_pose` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:254), `step` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:265), and `render` [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:275). Unlike Go2 and A1, ANYmal-C does not define `get_joint_velocities`; the method list above is exhaustive over its public state-accessor block [artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py](artifacts/auto_adapter_from_scratch_anymal_c_artifacts/driver_from_scratch.py:236).
- No `turn_in_place`, `move_to`, `walk_with_velocity`, or `recover_from_fall` definitions appear in the current Go2, A1, or ANYmal-C `driver_from_scratch.py` files, and none are exposed in the quadruped tool registry [auto_adapter/agent/task_planner.py](auto_adapter/agent/task_planner.py:156).
- Existing downstream result files support the prompt's ceiling claims: `ours_go2_*` is 0.92-1.0 physics pass rate, `ours_anymal_*` is 1.0, and `ours_a1_*` is 0.6 in `autoadapter_bench/results/quadruped/*.json`.

## Q1. Classic, reviewer-recognized quadruped benchmarks to align with

- [Barkour: Benchmarking Animal-level Agility with Quadruped Robots](https://arxiv.org/abs/2305.14654): the cleanest agility citation for reviewer recognition; use it to justify turning, waypoint courses, and "course-style" locomotion, while being explicit that full Barkour is beyond the current driver.
- [Learning to Walk in Minutes Using Massively Parallel Deep Reinforcement Learning](https://arxiv.org/abs/2109.11978): the standard modern reference for command-conditioned quadruped locomotion, especially forward velocity tracking, stability, and rough-terrain traversal.
- [Walk These Ways: Tuning Robot Control for Generalization with Multiplicity of Behavior](https://arxiv.org/abs/2212.03238): strong citation for command tracking plus behavior diversity; relevant for posture variation, straight/curved walking, and small gait-variation tasks.
- [A Reactive Controller Framework for Quadrupedal Locomotion on Challenging Terrain](https://iit-dlslab.github.io/papers/barasuol13icra.pdf): classic terrain/disturbance-recovery lineage; good justification for rough-terrain traversal and push-recovery as "known standard" locomotion stress tests.
- Learning Complex Motor Skills for Legged Robot Fall Recovery: the right citation for self-righting and fall-recovery tasks; use it to frame recovery as a legitimate but separate skill family from command following.
- [AllGaits: Learning All Quadruped Gaits and Transitions](https://arxiv.org/abs/2411.04787): the clearest recent citation for gait-blending and gait-transition evaluation, but this should be framed as stretch territory rather than a main-paper benchmark for the current driver.

## Q2. Candidate-task classification against the current tool surface

| task_name | status | extension_cost |
| --- | --- | --- |
| Relative posture tasks (`sit`, `sit->stand`, stand/sit/stand height checks) | CAN_DO_NOW | `0 LoC`; already supported by `stand_up`, `sit`, and `get_body_height` |
| Straight-line forward-progress tasks (5-20 cm, multi-bout) | CAN_DO_NOW | `0 LoC`; already supported by `walk_forward` and `get_base_pose`, though A1 is a known weak point |
| Closed-loop stop-in-band tasks ("end between 8 and 12 cm") | CAN_DO_NOW | `0 LoC`; agent can iteratively query `get_base_pose` between short `walk_forward` calls |
| `turn_in_place` / 90 deg yaw target | NEEDS_EXTENSION | Add `turn_in_place(secs, yaw_rate)` plus tool-registry exposure; about `40-80 LoC/driver` and `20-30 LoC` in planner/eval; `medium-high` physics risk |
| Forward velocity command tracking | NEEDS_EXTENSION | Add `walk_with_velocity(vx_mps, yaw_rate_rps, secs)`; about `80-150 LoC/driver` plus replay-time trajectory metrics; `high` physics risk, especially on A1 |
| Push-disturbance recovery while locomoting | NEEDS_EXTENSION | Needs a persistent closed-loop gait command interface plus evaluator-side impulse injection; about `120-200 LoC` total and `high` controller risk |
| Fall recovery / self-righting | NEEDS_EXTENSION | Add `recover_from_fall()` with explicit self-righting routine; about `150-300 LoC/driver`; `very high` physics risk and retuning burden |
| Rough-terrain traversal | HONEST_CEILING | Not a driver-only extension; it also needs terrain assets, foothold robustness, and likely perception/state features absent from the current setup |
| Barkour full course | HONEST_CEILING | Requires turning, speed/yaw tracking, obstacle negotiation, and much stronger locomotion than the current hand-tuned trot; not a fair headline task now |
| Gait blending / gait transitions | HONEST_CEILING | Needs a gait-conditioned controller family rather than one more primitive; this is beyond the current single-trot architecture |

## Q3. Proposed tiered quadruped suite

```yaml
version: "v2_proposed"
robot_class: quadruped
suites:
  simple:
    description: "Single-skill and short-composition locomotion tasks with physics-only scoring."
    tasks:
      - id: sit_down_from_home
        tier: simple
        prompt: "Sit down from the current standing pose."
        success_spec:
          type: threshold
          metric: final_body_height_ratio_to_start
          threshold:
            max_ratio: 0.75
            unit: ratio_to_start_height
        required_primitives: [sit]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Walk These Ways"
        honest_ceiling: false
        notes: "A basic posture primitive, but scored by relative body-height change rather than return value or text."

      - id: sit_then_stand_to_nominal
        tier: simple
        prompt: "Sit down, then stand back up to approximately the starting standing height."
        success_spec:
          type: sequence
          metric: body_height_ratio_after_each_posture_transition
          threshold:
            - phase: after_sit
              max_ratio: 0.75
              unit: ratio_to_start_height
            - phase: final
              min_ratio: 0.90
              unit: ratio_to_start_height
        required_primitives: [sit, stand_up]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Walk These Ways"
        honest_ceiling: false
        notes: "This is the minimal multi-step posture-recovery task and is more honest than the current call-count heuristic."

      - id: walk_forward_5cm
        tier: simple
        prompt: "Walk forward a short distance from the current pose and remain upright."
        success_spec:
          type: threshold
          metric: final_base_x_displacement_m_with_upright_height_ratio
          threshold:
            x_min_m: 0.05
            height_ratio_min: 0.60
            unit:
              x: m
              height: ratio_to_start_height
        required_primitives: [walk_forward]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "This replaces the current 'any motion counts' walk task with a real forward-progress threshold."

      - id: walk_forward_10cm
        tier: simple
        prompt: "Walk forward at least 10 cm from the starting pose and remain upright."
        success_spec:
          type: threshold
          metric: final_base_x_displacement_m_with_upright_height_ratio
          threshold:
            x_min_m: 0.10
            height_ratio_min: 0.60
            unit:
              x: m
              height: ratio_to_start_height
        required_primitives: [walk_forward]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "A second forward-progress rung gives useful dynamic range without needing a new controller."

      - id: two_short_walks
        tier: simple
        prompt: "Take two short forward walks with a base-pose check between them."
        success_spec:
          type: sequence
          metric: base_x_progress_after_walk_1_and_final
          threshold:
            - phase: after_walk_1
              min_m: 0.03
              unit: m
            - phase: final
              min_m: 0.10
              height_ratio_min: 0.60
              unit:
                x: m
                height: ratio_to_start_height
        required_primitives: [walk_forward, get_base_pose]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "This is the bridge from primitive execution to observable multi-turn locomotion."

  hard:
    description: "Reasoning, thresholding, and iterative correction using repeated pose observations."
    tasks:
      - id: target_x_band_10cm
        tier: hard
        prompt: "Advance roughly 10 cm in the +X direction from the starting pose. Use repeated short walks and base-pose checks, and stop once you are inside the target band."
        success_spec:
          type: numeric_range
          metric: final_base_x_displacement_m
          threshold:
            min_m: 0.08
            max_m: 0.12
            unit: m
          tolerance:
            symmetric_m: 0.02
        required_primitives: [walk_forward, get_base_pose]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "This is the quadruped analogue of arm midpoint/band-stop tasks: the model must measure and stop, not just fire one open-loop command."

      - id: target_x_band_20cm
        tier: hard
        prompt: "Advance roughly 20 cm in the +X direction using at most three short walk commands, checking base pose after each command."
        success_spec:
          type: numeric_range
          metric: final_base_x_displacement_m
          threshold:
            min_m: 0.16
            max_m: 0.24
            unit: m
          tolerance:
            symmetric_m: 0.04
        required_primitives: [walk_forward, get_base_pose]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "A wider distance target keeps the task cross-robot while still requiring iterative correction."

      - id: explore_then_correct_12cm
        tier: hard
        prompt: "First take one exploratory 0.5 second walk. Measure the x gain. Then choose exactly one additional walk so the final x progress ends near 12 cm."
        success_spec:
          type: sequence
          metric: dx_after_exploration_then_final_dx
          threshold:
            - phase: after_walk_1
              min_m: 0.01
              max_m: 0.08
              unit: m
            - phase: final
              min_m: 0.10
              max_m: 0.14
              unit: m
        required_primitives: [walk_forward, get_base_pose]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Walk These Ways"
        honest_ceiling: false
        notes: "This explicitly forces embodiment-specific feedback use because the second command should depend on the measured first-step gain."

      - id: walk_sit_stand_keep_progress
        tier: hard
        prompt: "Move forward about 10 to 20 cm, sit down, then stand back up without losing most of that forward progress."
        success_spec:
          type: sequence
          metric: forward_progress_retention_and_final_height_ratio
          threshold:
            - phase: before_sit
              min_m: 0.08
              unit: m
            - phase: final
              min_progress_retention_ratio: 0.75
              min_height_ratio: 0.90
              unit:
                progress: ratio_to_pre_sit_forward_progress
                height: ratio_to_start_height
        required_primitives: [walk_forward, get_base_pose, sit, stand_up]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Walk These Ways"
        honest_ceiling: false
        notes: "This is a good locomotion-only analogue of a multi-step arm task because it compounds motion, posture change, and state retention."

      - id: bounded_drift_forward
        tier: hard
        prompt: "Move forward at least 10 cm while keeping sideways drift small. Use short walks and pose checks rather than one long blind command."
        success_spec:
          type: threshold
          metric: final_base_x_displacement_m_and_absolute_y_drift_m
          threshold:
            x_min_m: 0.10
            abs_y_max_m: 0.18
            height_ratio_min: 0.60
            unit:
              x: m
              y: m
              height: ratio_to_start_height
        required_primitives: [walk_forward, get_base_pose]
        can_do_now: true
        requires_driver_extension: none
        reviewer_citation: "Walk These Ways"
        honest_ceiling: false
        notes: "This tests whether the agent can trade off duration and speed to get usable forward progress without obvious instability."

  agility:
    description: "Extension-facing tasks aligned with standard quadruped agility benchmarks; some are documented limits, not main-paper headline tasks."
    tasks:
      - id: turn_in_place_90deg
        tier: agility
        prompt: "Rotate about 90 degrees to the left in place while staying roughly where you started in XY."
        success_spec:
          type: numeric_range
          metric: final_yaw_delta_deg_and_xy_drift_m
          threshold:
            yaw_min_deg: 75
            yaw_max_deg: 105
            xy_max_m: 0.10
            height_ratio_min: 0.60
            unit:
              yaw: deg
              xy: m
              height: ratio_to_start_height
          tolerance:
            yaw_deg: 15
        required_primitives: [turn_in_place, get_base_pose]
        can_do_now: false
        requires_driver_extension: turn_in_place
        reviewer_citation: "Barkour"
        honest_ceiling: false
        notes: "If one extension is added, turning is the highest-leverage one because it unlocks real waypoint and course tasks."

      - id: right_angle_waypoint_course
        tier: agility
        prompt: "Turn left about 90 degrees, walk forward to the waypoint, and finish within the waypoint tolerance while remaining upright."
        success_spec:
          type: sequence
          metric: yaw_then_waypoint_reach
          threshold:
            - phase: after_turn
              yaw_min_deg: 75
              yaw_max_deg: 105
              unit: deg
            - phase: finish
              position_tolerance_m: 0.15
              height_ratio_min: 0.60
              unit:
                position: m
                height: ratio_to_start_height
        required_primitives: [turn_in_place, walk_forward, get_base_pose]
        can_do_now: false
        requires_driver_extension: turn_in_place
        reviewer_citation: "Barkour"
        honest_ceiling: true
        notes: "This is a realistic Barkour-lite task, but the current straight-line trot is too weak to make it a fair headline metric."

      - id: velocity_track_forward_10s
        tier: agility
        prompt: "Track a forward body-frame velocity command of 0.30 m/s for 10 seconds with low drift."
        success_spec:
          type: threshold
          metric: mean_absolute_forward_velocity_error_mps_and_absolute_y_drift_m
          threshold:
            mean_abs_vx_error_max_mps: 0.10
            abs_y_drift_max_m: 0.25
            no_fall: true
            unit:
              vx_error: m_per_s
              y_drift: m
        required_primitives: [walk_with_velocity, get_base_pose]
        can_do_now: false
        requires_driver_extension: walk_with_velocity
        reviewer_citation: "Learning to Walk in Minutes"
        honest_ceiling: false
        notes: "This is the cleanest command-tracking task if you decide to add exactly one locomotion-control extension after turning."

      - id: recover_from_supine_to_stand
        tier: agility
        prompt: "Recover from an overturned or supine pose to a stable stand."
        success_spec:
          type: boolean
          metric: upright_base_orientation_and_final_height_ratio_within_time_limit
          threshold:
            max_roll_pitch_deg: 20
            min_height_ratio: 0.90
            completion_time_max_s: 8
            unit:
              angle: deg
              height: ratio_to_nominal_stand_height
              time: s
        required_primitives: [recover_from_fall, get_base_pose, get_body_height]
        can_do_now: false
        requires_driver_extension: recover_from_fall
        reviewer_citation: "Learning Complex Motor Skills for Legged Robot Fall Recovery"
        honest_ceiling: true
        notes: "This is a legitimate robotics task, but it is a distinct controller family rather than a light extension of the current benchmark."

      - id: rough_terrain_step_over
        tier: agility
        prompt: "Traverse a short rough-terrain lane with one low step or discrete stepping-stone section while remaining upright."
        success_spec:
          type: boolean
          metric: finish_reached_without_fall_on_rough_lane
          threshold:
            lane_length_m: 1.0
            completion_time_max_s: 12
            no_fall: true
            unit:
              length: m
              time: s
        required_primitives: [walk_with_velocity, get_base_pose]
        can_do_now: false
        requires_driver_extension: walk_with_velocity
        reviewer_citation: "A Reactive Controller Framework for Quadrupedal Locomotion on Challenging Terrain"
        honest_ceiling: true
        notes: "Terrain traversal is reviewer-recognized, but with the present driver and assets it belongs in documented limits, not the headline benchmark."
```

## Q4. Documented limits, explicitly framed as limits rather than headlines

- `right_angle_waypoint_course`: even with `turn_in_place`, the current locomotion stack is still a fragile straight-line trot; the task is reviewer-recognizable, but it will mostly measure controller weakness rather than agent reasoning.
- `rough_terrain_step_over`: this needs more than one more primitive. It effectively asks for terrain robustness, contact adaptation, and probably new assets, so low score would not be scientifically surprising.
- `recover_from_supine_to_stand`: self-righting is a different skill family from stand/sit/walk; adding it close to deadline would create a large implementation and retuning burden.
- Full Barkour course: do not headline this. Cite Barkour for task-family legitimacy, but present any Barkour-like course only as an appendix-style stretch task or a documented non-goal for this paper.
- Push-disturbance recovery during locomotion: also keep this out of the headline suite for now. It is standard in locomotion papers, but in this benchmark it would be much more about controller robustness than about the LLM's multi-turn reasoning.
- Gait blending / gait transitions: cite it as future work, not as a core evaluation axis, because the current driver architecture has only one hand-tuned trot and no gait-conditioned interface.

## Q5. Is velocity command tracking worth adding?

Short answer: not for the main paper by the 2026-06-16 deadline.

Why I would skip it now:

- It is not just a small API addition. The real change is controller architecture: the current `walk_forward(secs, speed)` implementations are open-loop trot routines, not true command trackers. Go2 and A1 expose the raw method surface for locomotion, but the agent-visible registry still only offers five quadruped tools [auto_adapter/agent/task_planner.py](auto_adapter/agent/task_planner.py:156).
- Cross-robot retuning risk is high. The existing results already show that A1 is the weak quadruped (`ours_a1_*` is 0.6 total physics pass rate), so adding a command-tracking benchmark now will mostly amplify embodiment-specific controller failures.
- It also needs evaluator work. A fair metric cannot be a final-position check; it must sample the replay trajectory over time and score tracking error over a time window.

If you do add it anyway, keep it minimal and appendix-only:

- Driver API:
  - `walk_with_velocity(vx_mps: float, yaw_rate_rps: float, secs: float) -> bool`
  - Do not include `vy_mps` for this paper; the current robots have no lateral locomotion support, so a lateral command would just manufacture guaranteed failures.
- Tool exposure:
  - Add `walk_with_velocity` to `_QUADRUPED_TOOL_SPECS` in `TaskPlanner`.
- Eval design:
  - During replay, sample base pose at 50 Hz.
  - Estimate realized body-frame forward velocity `vx` and yaw rate `wz` from pose differences.
  - Ignore the first 1.0 s as warm-up.
  - Score `mean_abs_vx_error` and `mean_abs_wz_error` over the remaining window.
  - Declare success only if:
    - `mean_abs_vx_error <= 0.10 m/s`
    - `mean_abs_wz_error <= 0.20 rad/s` for turning tasks
    - `abs(y_final - y_start) <= 0.25 m`
    - no fall, where "fall" means base height drops below `0.60 x` nominal standing height for more than 0.25 s.

Pragmatic recommendation:

- For this paper, add `turn_in_place` before `walk_with_velocity`. Turning is cheaper, more reviewer-visible, and enough to unlock a real "Barkour-lite" right-angle course without turning the benchmark into a full locomotion-controller project.
