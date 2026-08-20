# Google Barkour vB reference controller review

Review date: 2026-08-20

This is a research and calibration record. It does not add a runnable-package
controller, establish package-wide task success, or admit Google Barkour vB to
the runnable package index.

## Decision

The first flat-ground reference is now a locally trained actor from the
official MuJoCo Playground `BarkourJoystick` environment, pinned to release
`v0.0.5` at commit `81dfe512c9f2f03107fda1e31de585d04bb30bc4`.
The actor, observation normalizer, and interface metadata are retained with the
calibration run. No external checkpoint is treated as evidence.

The inspected official repositories and release assets provide the vB model,
training environment, PPO entrypoint, robot hardware software, and the older v0
course scene/scorer. They do not publish a pretrained Barkour vB policy
checkpoint that can be restored directly. This is a bounded finding for the
official material inspected on the review date, not a claim about every
third-party artifact on the internet.

Release `v0.0.5` is the latest inspected Playground release compatible with the
current MuJoCo 3.3.6 mainline before `v0.1.0` changes its dependency floor to
MuJoCo 3.4. It is therefore the first reproduction target.

The successful reproduction used NVIDIA's `nvcr.io/nvidia/jax:25.10-py3`
container, its bundled JAX `0.7.2` build, and an isolated Brax `0.13.0`
overlay. Brax `0.12.4` could not serialize this checkpoint shape, and `0.12.5`
could save it but could not restore it with the container's Orbax `0.11.25`.
Brax `0.13.0` passed training, save, restore, deterministic export, and
inference without replacing the pinned Playground environment.

## Primary material

- MuJoCo Playground `v0.0.5` release and pinned source:
  https://github.com/google-deepmind/mujoco_playground/releases/tag/v0.0.5
- Pinned Barkour joystick environment:
  https://github.com/google-deepmind/mujoco_playground/blob/81dfe512c9f2f03107fda1e31de585d04bb30bc4/mujoco_playground/_src/locomotion/barkour/joystick.py
- Pinned locomotion PPO configuration:
  https://github.com/google-deepmind/mujoco_playground/blob/81dfe512c9f2f03107fda1e31de585d04bb30bc4/mujoco_playground/config/locomotion_params.py
- Official MuJoCo MJX locomotion tutorial:
  https://github.com/google-deepmind/mujoco/blob/eacad44a1a67afe520b263c9b15dab82f62a10aa/mjx/tutorial.ipynb
- Pinned Barkour vB model already used by the canonical package:
  https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/google_barkour_vb
- Barkour robot repository and its simulation guidance:
  https://github.com/google-deepmind/barkour_robot/tree/e7c0f6fa5c164347b39a9a8c3ba36bab60312c9b
- Barkour benchmark paper:
  https://arxiv.org/abs/2305.14654v1

## Policy interface to preserve

The official joystick environment and the canonical package have compatible
model dimensions and named ordering: `nq=19`, `nv=18`, `nu=12`, with the same
four-leg sequence and three actions per leg.

The actor input is a 15-frame history of a 31-value observation, for 465 values
in total. One frame contains:

- scaled local yaw rate: 1 value;
- projected gravity: 3 values;
- scaled planar velocity and yaw command: 3 values;
- 12 joint positions relative to the home pose; and
- the previous 12-value policy action.

The actor emits a normalized 12-value action. The official environment converts
it to joint targets as `home_joint_position + 0.3 * action`, then clips to its
reviewed joint bounds. Observation normalization is enabled by the official
Brax PPO configuration, so a usable artifact must retain the observation
normalizer together with the actor parameters and network definition.

The official control period is 20 ms and its training physics step is 4 ms. The
canonical CPU scene uses a 1 ms physics step. A Direct-MuJoCo bridge must hold
each actor output for exactly 20 canonical CPU `mj_step` calls, while recording
controls and physical state through the existing Harness. It must never write
`qpos` or `qvel` directly.

## Compatibility risk

Action names and dimensions match, but dynamics do not yet match. The official
Playground environment loads the MJX scene, removes the source friction loss,
sets joint damping to `0.5239`, and changes the position-servo proportional gain
to `35`. The canonical CPU package preserves a 1 ms step, joint damping `0.024`,
friction loss `0.13`, and proportional gain `50`.

Therefore an official-environment replay is necessary but insufficient. The
same retained policy must pass a canonical CPU cross-simulator gate. If it does
not, retrain or fine-tune with dynamics randomization that includes the exact
canonical target. Do not silently alter the canonical asset to fit the policy.

## Completed flat-ground calibration gate

The official PPO configuration completed 100,270,080 environment steps from a
100 million-step request. Evaluation reward rose from `1.510` at step zero to
`36.961` at the final checkpoint. The retained deterministic actor export
matched its JAX inference function on 256 probes with maximum absolute error
`3.0994415283203125e-06`, below the `5e-05` export tolerance.

The same artifact then ran against the unmodified canonical CPU scene with
MuJoCo 3.3.6. The bridge wrote only `data.ctrl`, held each action for 20
canonical 1 ms physics steps, preserved the official delayed previous-action
observation, and used the official 15 by 31 observation history. It did not
write `qpos` or `qvel` and did not replace the canonical damping, friction loss,
or servo gain.

The five-second zero command stayed upright with post-settling mean local
planar speed `0.055918 m/s`; this is a stable stand calibration, not a strict
zero-drift claim. All eight independently reset `0.4 m/s` direction trials ran
for 5,000 canonical physics steps without a fall. Post-settling local planar
speed ranged from `0.503131` to `0.836905 m/s`, and direction error ranged from
`0.2854` to `7.5250` degrees. These measurements clear the flat calibration
gate corresponding to the public speed and heading bounds, but they are not a
Framework/Harness task verdict.

All nine retained case videos decode fully as H.264 at 640 by 480, 25 fps, and
126 frames. Stand, forward, 90-degree, and worst-heading 225-degree cases were
visually checked for nonblank, readable follow-camera framing and an upright
robot. The ignored raw record is retained at
`autoadapter/runs/barkour-flat-bridge-20260820T125848Z/`.

## Coverage boundary

`BarkourJoystick` is now a sound low-level reference candidate for stand,
commanded planar locomotion, orientation, and waypoint-following experiments.
It is not evidence for the complete 20-task public catalog.

The public catalog also contains a 3 rad/s turn, steps, trap terrains, an
A-frame, a broad jump, and the complete timed course. The Barkour paper solves
the course with specialist locomotion policies plus a high-level waypoint and
policy-switching controller, or with a distilled generalist policy. It does not
release those policy parameters in the inspected official repositories.

The experiment-grade reference should consequently grow in two stages:

1. one reviewed joystick actor for flat locomotion and route-following; then
2. separate reviewed step, slope/A-frame, and jump specialists, coordinated by
   a Framework-owned calibration planner for obstacle and full-course cases.

The planner belongs only in the calibration reference. The public skeleton must
remain capability-neutral and must not contain task IDs, private waypoints, or
private verdict logic.

## Minimum acceptance sequence

1. Preserve the completed deterministic actor export, source/runtime pins, CPU
   bridge measurements, and videos as calibration inputs.
2. Convert the reviewed interface into a capability-neutral trusted skeleton
   and a separately owned calibration reference without leaking task IDs,
   private waypoints, bindings, or verdict logic.
3. Create Framework-private fixtures and reference
   cases for step and navigation tasks. Add specialist policies one obstacle
   family at a time, each with its own focused positive control.
4. Run the complete package-wide reference positive control before any
   real-model dynamic canary or runnable-index review.
