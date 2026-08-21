# AutoAdapter 2.0 Mainline Evidence Status

## Current Qualification

The real-model Direct-MuJoCo orchestration is operational. Authority `0.19.24`
qualifies robot construction through complete canonical packages, source-backed
Task Libraries, real fixture collision layers, resets and applicable passive
settling within the 5 mm penetration limit, and a shared Harness that reports
task-metric success separately from physical integrity. No retained run yet
contains the complete 22-cell all-robot Ministral 8B shakedown, so this file does
not claim that the model-generated mainline has completed or succeeded.

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
  direct-state-write rejection;
- per-step contact-penetration evidence that rejects physical integrity below
  `-0.005 m`, independently of the task metric; and
- complete IVC-authored Capability Validation followed, only after admission,
  by a separately reported random five-task Task Demo; and
- independent per-case video and separate pipeline, Capability Validation,
  Task Demo, and video verdicts.

All historical package-wide reference diagnostic sections below are retained
records from the earlier calibration phase. They predate the per-step
penetration verdict introduced by commit `d705641`. Their reported `20/20`
values describe the task metrics, then-current guards, actuator evidence, and
videos recorded at that time; they do not independently establish current
physical success or runnable admission under Authority `0.19.24`. Hidden
reference drivers remain useful diagnostic oracles, but their planner success
is not a mainline gate.

## Current Simulator-Integrity Verification

The Authority `0.19.24` construction checks were rerun on 2026-08-21 after the
11-robot runnable index and LEAP package were integrated:

- `python -m autoadapter2 check-only` passed for all 11 indexed canonical
  packages, with 20 public tasks per package and a live MuJoCo 3.3.6 physics
  smoke;
- 12 focused robot checks passed in 155.41 seconds, covering real fixture
  collision layers, every Framework reset, and applicable 100-step settling
  without contact penetration below `-0.005 m`;
- LEAP separately passed all 48 private execution resets over 4,800 settling
  steps and all 12 unique scene/reset pairs over 1,200 `ctrl=qpos` hold steps;
  its worst initial and settling distances were `-0.004199963 m` and
  `-0.004219817 m` respectively;
- the shared pipeline, Harness, worker, runnable-index, and backup-index checks
  passed with 60 tests and 15 subtests; and
- the LEAP scenes rendered under an approved graphical context. The restricted
  terminal cannot create a CoreGraphics connection, so the formal DGX run must
  still produce and validate its required per-trial videos.

This is simulator and package construction evidence only. It does not claim a
model-generated driver success, Evolution success, or completed 22-cell
shakedown.

## Historical Franka Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical Franka assets, controller baseline, measurements, Harness, and video
path. Under Authority `0.19.24` it is neither current physical-success nor
admission evidence and does not establish Task Demo or driver-synthesis success.

## Historical xArm7 Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical xArm7 assets, controller baseline, measurements, Harness, and video
path. Under Authority `0.19.24` it is neither current physical-success nor
admission evidence and does not establish Task Demo or driver-synthesis success.

## Historical Piper Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical Piper assets, controller baseline, measurements, Harness, and video
path. Under Authority `0.19.24` it is neither current physical-success nor
admission evidence and does not establish Task Demo or driver-synthesis success.

## Historical KUKA iiwa 14 Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical KUKA assets, actuator trajectory baseline, measurements, guards,
Harness, and video path. Under Authority `0.19.24` it is neither current
physical-success nor admission evidence and does not establish Task Demo or
driver-synthesis success.

## Historical ALOHA 2 Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical ALOHA assets, selected-arm actuator trajectory baseline, measurements,
guards, Harness, and video path. Under Authority `0.19.24` it is neither current
physical-success nor admission evidence and does not establish Task Demo or
driver-synthesis success.

## Historical Kinova Gen3 + Robotiq 2F-85 Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical Kinova Gen3 plus Robotiq 2F-85 assets, trajectory baseline,
measurements, guards, Harness, package loader, and video path. Under Authority
`0.19.24` it is neither current physical-success nor admission evidence and does
not establish Task Demo or driver-synthesis success.

## Historical UR5e + Robotiq 2F-85 Package-Wide Reference Diagnostic

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

This historical result records diagnostic reference feasibility for the then
canonical UR5e plus Robotiq 2F-85 assets, pose-DLS trajectory baseline,
measurements, guards, Harness, package loader, and video path. Under Authority
`0.19.24` it is neither current physical-success nor admission evidence and does
not establish Task Demo or driver-synthesis success.

## Historical Stretch 2 Package-Wide Reference Diagnostic

Run `stretch-reference-positive-control-20260820T215313Z`, on repository commit
`85eddd2`, passed all 20 canonical Stretch 2 Task Library cases with the
reviewed skeleton-assisted calibration driver and real MuJoCo 3.3.6 physics:

- pipeline, physical execution, and structured Harness validation: passed;
- tasks, source clauses, and private cases: `20/20` each, including the v2
  window-open, window-close, and faucet-close routes;
- actuator control was observed before physics stepping and changed from reset
  in every trial;
- all core guards passed, all canonical model/data checks passed, and no direct
  `qpos` or `qvel` write was detected;
- maximum trial length: 22,088 physics steps, within its declared private-case
  budget;
- videos: `20/20` complete H.264, independently decoded at `800x600` and 10
  fps, with 12 to 443 frames per case; and
- pick-place, window-open, window-close, faucet-close, and lever-pull terminal
  frames were visually checked for nonblank, task-readable framing and visible
  terminal task state.

The ignored raw run is retained locally at
`autoadapter/runs/stretch-reference-positive-control-20260820T215313Z/`. Its
`reference_report.json`, complete 20-case suite, capability design, and
per-case videos remain together. The final run includes the evidence-framebuffer
fix from `1c299ed` and the window-camera visibility fix from `f63f057`.

This historical result records diagnostic reference feasibility for the then
canonical Stretch 2 assets, feedback baseline, measurements, guards, Harness,
package loader, and video path. Under Authority `0.19.24` it is neither current
physical-success nor admission evidence and does not establish Task Demo or
driver-synthesis success.

## Historical SO-101 Package-Wide Reference Diagnostic

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

This historical run superseded the older five-case SO-101 diagnostic and its
low-resolution replay at the time. Under Authority `0.19.24` it records
reference feasibility only: it is neither current physical-success nor
admission evidence and does not establish Task Demo or driver-synthesis success.

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

## Historical Source And Scene Audit

The following findings describe the retained 2026-08-18 SO-101/Go2 diagnostic
and its then-current packages. They explain why that four-cell run is historical;
they are not a current audit of the rebuilt eleven-robot package set.

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

## Required Before Initial Shakedown Evidence

1. Resolve all eleven exact package IDs from the runnable index and pass the
   self-contained package check.
2. Pass the Authority `0.19.24` simulator-integrity checks for every package:
   physical fixture collisions, valid private resets, applicable 100-step
   passive settling, and the fixed 5 mm penetration bound.
3. Pass the focused shared-Harness regressions that keep task-metric success and
   physical integrity independent and reject metric-only false success.
4. Run the complete 22-cell manifest-pinned Ministral 8B shakedown with empty
   prior Experience. Hidden reference diagnostics may be run or skipped; they
   do not gate generated cells.
5. Retain one Evolution outcome for every terminal cell, then record one
   reviewed Experience disposition per cell for later runs. The historical
   four-cell diagnostic above cannot satisfy or reduce that scope.

## Historical Local Verification

- Full suite after canonical migration: `198 passed, 32 subtests passed`.
- Video-resolution focused checks: `3 passed`.
- Current video-resolution commit: `880faac`.
- The unrelated dirty Demo2 worktree was not staged or modified by these mainline
  changes.

## Historical Runs

`deepseek-v4-pro-20260818T020155Z` remains a pre-interactive-loop failure
baseline. `deepseek-react-20260818T101705Z` stopped at TGCD with HTTP 402. Neither
run is synthesis-success evidence.
