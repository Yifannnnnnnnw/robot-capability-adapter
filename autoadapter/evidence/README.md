# AutoAdapter 2.0 Mainline Evidence Status

## Current Qualification

The real-model, Direct-MuJoCo mainline orchestration is operational, but the
current robot Task Libraries and private scenes have not passed the Authority's
required human source-and-applicability review. The latest run is therefore
retained as a diagnostic engineering baseline, not as formal benchmark evidence.

The implementation includes:

- real-model TGCD over the complete public Task Library;
- AutoAdapter 1.0-style multi-turn STUDY, GENERATE/GEN_ALGO, and Repair;
- deterministic tool-event context projection retaining the initial task, one
  current driver snapshot, and at most three recent interaction groups;
- interface-only stubs generated from sealed capability names and exact
  `(self, request)` signatures;
- bounded public Python/MuJoCo development probes;
- source audit, import checks, and mandatory public physics smoke for every
  capability on the submitted revision;
- explicit submission before a candidate consumes a Harness attempt;
- Framework-owned canonical MuJoCo sessions, actuator/physics-step guards, and
  direct-state-write rejection; and
- complete IVC-authored Capability Validation followed, only after admission,
  by a separately reported random five-task Task Demo; and
- independent per-case video and separate pipeline, Capability Validation,
  Task Demo, and video verdicts.

## Franka Package-Wide Reference Positive Control

Run `franka-reference-positive-control-20260820T034710Z`, on mainline commit
`0d047c7`, passed all 20 fixed Franka Task Library cases with the reviewed
Framework-owned reference driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- the complete canonical package loader check passed for the fixed package
  identity, 20 tasks, one source record, snapshot, and local MJCF closure;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control observed before physics stepping and changed from reset in
  every trial;
- all guards passed, with no direct `qpos` or `qvel` write detected;
- maximum trial length: 8,680 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete, independently decodable at `800x600`, with 4 to
  175 frames per case; and
- the corrected `mw_handle_pull` case started at `vertical_handle_slide=-0.055`,
  physically moved to about `-0.0126`, and finished with `0.0126 m` error; and
- representative object-case and handle-pull videos were visually checked for
  nonblank, task-readable framing.

The ignored raw run is retained locally at
`autoadapter/runs/franka-reference-positive-control-20260820T034710Z/`.
Its `reference_report.json`, 20-case suite, and per-case videos remain together.

The earlier runs ending `013634Z` and `013256Z` were diagnostics and have been
removed from local raw evidence; neither is current positive-control evidence.
The former predates commit `0d047c7`:
its handle-pull reset already satisfied the public `<=0.05 m` criterion, so its
reported 20/20 is superseded even though the driver also moved the handle. The
latter additionally failed video completeness because the requested `800x600`
renderer exceeded the prior `640x480` offscreen framebuffer.

This result establishes package-wide reference feasibility for the canonical
Franka assets, controller baseline, trusted measurements, Harness, and video
path. It is not a model-generated condition, does not establish Task Demo or
driver-synthesis success, and does not admit Franka to the runnable index. A
real-model dynamic canary and final admission review remain required.

## xArm7 Package-Wide Reference Positive Control

Run `xarm7-reference-positive-control-20260820T034432Z`, on mainline commit
`0d047c7`, passed all 20 fixed xArm7 Task Library cases with the reviewed
Framework-owned reference driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control observed before physics stepping and changed from reset in
  every trial;
- all guards passed, with no direct `qpos` or `qvel` write detected;
- maximum trial length: 6,935 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600`, with 25
  to 140 frames per case; and
- the corrected `mw_handle_pull` case started at `vertical_handle_slide=-0.055`,
  physically moved to about `-0.0214`, and finished with `0.0215 m` error; and
- pick-place, sweep, and handle-pull terminal frames were visually checked for
  nonblank, task-readable framing and visible terminal task state.

The ignored raw run is retained locally at
`autoadapter/runs/xarm7-reference-positive-control-20260820T034432Z/`.
Its `reference_report.json`, 20-case suite, and per-case videos remain together.
The earlier runs ending `031649Z` and `031554Z` were diagnostics and have been
removed from local raw evidence. The `031649Z` run's handle-pull reset already
satisfied the public criterion, so its reported 20/20 is superseded. The
sandboxed `031554Z` run also predates the fix and failed requested video
creation because macOS CoreGraphics was unavailable.

This result establishes package-wide reference feasibility for the canonical
xArm7 assets, controller baseline, trusted measurements, Harness, and video
path. It is not a model-generated condition, does not establish Task Demo or
driver-synthesis success, and does not admit xArm7 to the runnable index. A
real-model dynamic canary and final admission review remain required.

## Piper Package-Wide Reference Positive Control

Run `piper-reference-positive-control-20260820T044929Z`, on mainline commit
`7c9658b`, passed all 20 fixed Piper Task Library cases with the reviewed
Framework-owned reference driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control observed before physics stepping and changed from reset in
  every trial;
- all guards passed, with no direct `qpos` or `qvel` write detected;
- maximum trial length: 8,514 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600`, with 5
  to 172 frames per case;
- the corrected `mw_handle_pull` case started at
  `vertical_handle_slide=-0.055`, physically moved to about `0.00222`, and
  finished with `0.00223 m` error; and
- pick-place, peg-insertion, sweep, and handle-pull terminal frames were
  visually checked for nonblank, task-readable framing and visible terminal
  task state.

The ignored raw run is retained locally at
`autoadapter/runs/piper-reference-positive-control-20260820T044929Z/`.
Its `reference_report.json`, 20-case suite, and per-case videos remain together.
The sandboxed run ending `044817Z` was a diagnostic and has been removed from
local raw evidence. All 20 physical criteria passed, but macOS CoreGraphics was
unavailable and every requested video had zero frames.

This result establishes package-wide reference feasibility for the canonical
Piper assets, controller baseline, trusted measurements, Harness, and video
path. It is not a model-generated condition, does not establish Task Demo or
driver-synthesis success, and does not admit Piper to the runnable index. A
real-model dynamic canary and final admission review remain required.

## KUKA iiwa 14 Package-Wide Reference Positive Control

Run `kuka-reference-positive-control-20260820T070515Z`, on mainline commit
`3459d17`, passed all 20 fixed KUKA iiwa 14 Task Library cases with the reviewed
Framework-owned reference driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all three core guards passed, all 19 contact tasks passed their exact
  `link7_contact_geom` to task-geometry guard, and no direct `qpos` or `qvel`
  write was detected;
- maximum trial length: 7,940 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600`, with 44
  to 160 frames per case; and
- push-to-goal, drawer-open, lever-pull, and window-close trajectories were
  visually checked for nonblank, task-readable framing and visible task change.

The ignored raw run is retained locally at
`autoadapter/runs/kuka-reference-positive-control-20260820T070515Z/`.
Its `reference_report.json`, complete 20-case suite, and per-case videos remain
together. The sandboxed run ending `070441Z` was a diagnostic and has been
removed from local raw evidence. All metrics, physical execution, and guards
passed, but macOS CoreGraphics was unavailable and every requested video had
zero frames.

This result establishes package-wide reference feasibility for the canonical
KUKA assets, compact actuator trajectory baseline, trusted measurements,
exact-contact guards, Harness, and video path. It is not a model-generated
condition, does not establish Task Demo or driver-synthesis success, and does
not admit KUKA to the runnable index. A real-model dynamic canary and final
admission review remain required.

## ALOHA 2 Package-Wide Reference Positive Control

Run `aloha-reference-positive-control-20260820T100317Z`, on mainline commit
`2e156e7`, passed all 20 fixed ALOHA selected-arm Task Library cases with the
reviewed Framework-owned reference driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all four core guards passed in every trial, including nonselected-arm neutral
  enforcement and direct-state-write rejection;
- all 19 contact tasks passed their exact selected-finger-to-task-geometry
  contact guard;
- maximum trial length: 6,600 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600`, with 12
  to 133 frames per case; and
- pick-place, sweep-into-goal, door-open, and soccer terminal frames were
  visually checked for nonblank, task-readable framing and visible terminal
  task state.

The ignored raw run is retained locally at
`autoadapter/runs/aloha-reference-positive-control-20260820T100317Z/`.
Its `reference_report.json`, complete 20-case suite, and per-case videos remain
together. The sandboxed run ending `100244Z` is diagnostic only: macOS
CoreGraphics was unavailable and every requested video had zero frames.

This result establishes package-wide reference feasibility for the canonical
ALOHA assets, selected-arm actuator trajectory baseline, trusted measurements,
other-arm neutrality guard, exact-contact guards, Harness, and video path. It
is not a model-generated condition, does not establish Task Demo or
driver-synthesis success, and does not admit ALOHA to the runnable index. A
real-model dynamic canary and final admission review remain required.

## Kinova Gen3 + Robotiq 2F-85 Package-Wide Reference Positive Control

Run `kinova-reference-positive-control-20260820T110159Z`, on mainline commit
`a493170`, passed all 20 fixed Kinova Gen3 plus Robotiq 2F-85 Task Library
cases with the reviewed skeleton-assisted calibration driver and real MuJoCo
3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- the exact package loader check passed for robot identity, version, public
  snapshot, 20 tasks, one source, local closure, and reference-driver loading;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all core guards passed, including exact task-contact checks where applicable,
  and no direct `qpos` or `qvel` write was detected;
- maximum trial length: 6,130 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600`, with 7
  to 124 frames per case; and
- push-to-goal, pick-place, sweep-into-goal, drawer-open, door-open, side peg
  insertion, and bin-picking terminal frames were visually checked for
  nonblank, task-readable framing and visible terminal task state.

The ignored raw run is retained locally at
`autoadapter/runs/kinova-reference-positive-control-20260820T110159Z/`.
Its `reference_report.json`, complete 20-case suite, and per-case videos remain
together.

This result establishes package-wide reference feasibility for the canonical
Kinova Gen3 plus Robotiq 2F-85 assets, skeleton-assisted actuator trajectory
baseline, trusted measurements, guards, Harness, package loader, and video
path. It is calibration evidence only: it is not model-generated or dynamic
evidence, does not establish Task Demo or driver-synthesis success, and does
not admit this configuration to the runnable index. A real-model dynamic
canary and final admission review remain required.

## UR5e + Robotiq 2F-85 Package-Wide Reference Positive Control

Run `ur5e-robotiq-reference-positive-control-20260820T150621Z`, on mainline
commit `4b08753`, passed all 20 fixed UR5e plus Robotiq 2F-85 Task Library cases
with the reviewed skeleton-assisted calibration driver and real MuJoCo 3.3.6
physics:

- pipeline, physical execution, and structured Harness validation: passed;
- the focused package and reference checks passed for the exact robot identity,
  public snapshot, 20 tasks, private package, local asset closure, and reference
  driver;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all core guards, all 19 task-contact guards, all three applicable object
  stability guards, and the reach gripper-neutral guard passed, with no direct
  `qpos` or `qvel` write detected;
- maximum trial length: 6,980 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600` and 10
  fps, with 25 to 141 frames per case; and
- push-to-goal, drawer-open, door-close, side peg insertion, pick-place, and
  faucet-open terminal frames were visually checked for nonblank, task-readable
  framing and visible terminal task state.

The ignored raw run is retained locally at
`autoadapter/runs/ur5e-robotiq-reference-positive-control-20260820T150621Z/`.
Its `reference_report.json`, complete 20-case suite, capability design, and
per-case videos remain together.

This result establishes package-wide reference feasibility for the canonical
UR5e plus Robotiq 2F-85 assets, pose-DLS actuator trajectory baseline, trusted
measurements, guards, Harness, package loader, and video path. It is calibration
evidence only: it is not model-generated or dynamic evidence, does not
establish Task Demo or driver-synthesis success, and does not admit this
configuration to the runnable index. A real-model dynamic canary and final
admission review remain required.

## SO-101 Package-Wide Reference Positive Control

Run `so101-reference-positive-control-20260820T152422Z`, on repository commit
`33946d9`, passed all 20 cases in the current
`robotstudio-so101-source-protocols-2026-08-18-v5` Task Library snapshot with
the reviewed calibration driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- the focused package check passed for the exact SO-101 identity, current
  public snapshot, 20 tasks, private package, local scene closure, skeleton,
  and reference driver;
- tasks, source clauses, and private cases: `20/20` each;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all core guards passed and no direct `qpos` or `qvel` write was detected;
- maximum trial length: 3,710 physics steps, below the 10,000-step case limit;
- videos: `20/20` complete H.264, independently decoded at `800x600` and 10
  fps, with 8 to 187 frames per case; and
- push-to-goal, pick-place, drawer-open, door-close, side peg insertion, and
  faucet-open terminal frames were visually checked for nonblank, task-readable
  framing and visible terminal task state.

The ignored raw run is retained locally at
`autoadapter/runs/so101-reference-positive-control-20260820T152422Z/`. Its
`reference_report.json`, complete 20-case suite, capability design, and
per-case videos remain together.

This run supersedes the old five-case SO-101 reference gate and its low-resolution
historical replay for package calibration. It establishes current-package
reference feasibility only: it is not model-generated or dynamic evidence,
does not establish Task Demo or driver-synthesis success, and does not replace
the required current-model canary and final all-cohort admission review.

## Barkour Flat-Controller Calibration Gate

Run `barkour-flat-bridge-20260820T125848Z` retained a locally trained official
MuJoCo Playground v0.0.5 `BarkourJoystick` actor and replayed it on the
unmodified canonical Barkour vB CPU scene with real MuJoCo 3.3.6 physics:

- the official PPO run completed 100,270,080 environment steps and improved
  evaluation reward from `1.510` to `36.961`;
- the retained actor includes the observation normalizer and matched JAX
  inference on 256 probes with maximum absolute error `3.10e-06`;
- the CPU bridge wrote only `data.ctrl` and held each policy action for exactly
  20 canonical 1 ms physics steps;
- a five-second zero command remained upright, with `0.055918 m/s`
  post-settling local planar drift;
- all eight independently reset `0.4 m/s` direction checks completed 5,000
  physics steps without a fall; minimum post-settling local speed was
  `0.503131 m/s` and maximum direction error was `7.525` degrees; and
- all nine H.264 videos decode completely at `640x480`, 25 fps, and 126 frames;
  four representative cases, including the worst direction error, were
  visually checked for nonblank, readable follow-camera framing.

The ignored raw record is retained locally at
`autoadapter/runs/barkour-flat-bridge-20260820T125848Z/`, including the actor,
per-case measurements, summary, and videos. This is research calibration only,
not a Framework/Harness package positive control. It does not establish the
3 rad/s turn, steps, trap terrain, A-frame, broad jump, complete course,
real-model synthesis, or runnable-index admission.

## Latest Historical Diagnostic Run

This run predates Authority `0.19.2`: its five sampled cases were used directly
for driver validation, so it is not evidence that the restored
Capability Validation -> Task Demo flow has executed.

- Run: `deepseek-full-convergence-20260818T144502Z`
- Model/provider: `deepseek-v4-pro` through the configured DeepSeek API
- Matrix: `robotstudio_so101` and `unitree-go2-stock-12dof`, each under
  `skeleton-assisted` and `from-scratch`
- Package checks: passed
- Reference gate: 5/5 sampled cases for both robots
- Real model called: yes
- Driver generated in-run: yes in all four cells
- Physical validation executed: yes in all four cells
- Pipeline completed: yes
- Overall strict verdict: fail, because one of four cells did not pass its final
  five-case suite
- Primary report retained locally at
  `autoadapter/runs/historical-demo3/deepseek-full-convergence-20260818T144502Z/experiment_report.json`

| Cell | Attempt trajectory | Final physical verdict |
|---|---|---|
| SO-101 / skeleton-assisted | `1/5 -> 3/5 -> 3/5` | fail |
| SO-101 / from-scratch | `3/5 -> 3/5 -> 5/5` | pass |
| Go2 / skeleton-assisted | `5/5` | pass on first submission |
| Go2 / from-scratch | `5/5` | pass on first submission |

These results demonstrate that the interactive generation and report-driven
Repair mechanism can complete and can improve a real generated driver. They do
not establish performance on the cited benchmarks because the current private
MuJoCo instances are local proxies.

## Video Evidence

The original SO-101 skeleton attempts were recorded at `160x120`, which is too
small for useful visual inspection. Commit `880faac` raises both robot packages,
the Harness fallback, and package render helpers to `640x480` and adds focused
regression checks. A trusted replay of the frozen SO-101 skeleton final driver
produced five complete `640x480` videos without changing its `3/5` physical
result:

`autoadapter/runs/historical-demo3/deepseek-full-convergence-20260818T144502Z/cells/robotstudio_so101/skeleton-assisted/high-resolution-replay/`

SO-101 from-scratch attempt 2 and both later Go2 cells were recorded directly at
`640x480`. Visual inspection also found that the SO-101 default camera is too
distant. Camera framing must be corrected as part of the scene rebuild; raising
resolution alone is not sufficient evidence quality.

## Source And Scene Audit

The robot morphology assets have traceable pinned upstreams:

- SO-101: TheRobotStudio SO-ARM100/SO-101 MJCF asset lineage recorded in its
  `morphology.json`;
- Go2: Google DeepMind MuJoCo Menagerie commit
  `71f066ad0be9cd271f7ed58c030243ef157af9f4`.

The validation environments do not currently have equivalent fidelity:

- the 20 SO-101 tasks map to ten locally authored primitive fixture scenes,
  rather than the pinned MetaWorld task environments;
- pick-place/bin-picking, push/sweep, faucet/dial, three slide-fixture tasks,
  and three press-fixture tasks contain pairs or groups with the same scene,
  reset, and task parameters;
- faucet and dial consequently produced identical physical measurements;
- the local peg-insertion object is a cube, and its ordinary body-centre
  Euclidean metric does not reproduce MetaWorld's peg-head, axis-weighted
  distance;
- MetaWorld scoring values are generally traceable, but catalog line anchors
  refer to an older source layout and do not match the pinned v3.1.1 files; and
- Go2 uses the pinned Menagerie robot model, but its terrains and several
  progress, body-height, and joint-range thresholds are local experimental
  proxies rather than reproductions of the cited papers' full protocols.

Under `AUTOADAPTER_2_AUTHORITY.md` section 2.4, unresolved source lineage or an
adaptation that changes task meaning must fail closed. Accordingly, the current
`5/5` cell verdicts mean only "passed the current local proxy suite." They must
not be reported as MetaWorld, locomotion-paper, industrial-standard, or formal
AutoAdapter 2.0 benchmark success.

## Required Before Formal Evidence

1. Verify every task clause against a pinned primary source and correct its exact
   section, table, protocol, or source-line anchor.
2. Port or faithfully adapt task-distinct fixtures and initialization semantics;
   document every robot-specific transform without weakening the source task.
3. Bind source-equivalent metrics, temporal rules, and aggregation, including
   geometry-specific measurements such as peg-head alignment.
4. Add close evidence cameras and verify readable per-case videos.
5. Complete the required human task-admission review for every robot in the
   Authority-declared fourteen-configuration cohort.
6. Recalibrate every package reference, then run the 28-cell initial
   all-robot shakedown with the manifest-pinned minimal model, per-cell
   Evolution outcomes, and post-round Experience dispositions. The historical
   four-cell diagnostic above cannot satisfy or reduce that scope.

## Local Verification

- Full suite after canonical migration: `198 passed, 32 subtests passed`.
- Video-resolution focused checks: `3 passed`.
- Current video-resolution commit: `880faac`.
- The unrelated dirty Demo2 worktree was not staged or modified by these mainline
  changes.

## Historical Runs

`deepseek-v4-pro-20260818T020155Z` remains a pre-interactive-loop failure
baseline. `deepseek-react-20260818T101705Z` stopped at TGCD with HTTP 402. Neither
run is synthesis-success evidence.
