# Experiment 3 — Exact Eleven-Configuration Direct-MuJoCo Cohort Authority

> **Document ID:** `AA2-EXP3`<br>
> **Document role:** sole normative document for Experiment 3<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.32`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.1`<br>
> **Effective date:** 2026-08-23<br>
> **Design status:** active and prospectively fixed; ready for formal dispatch after final preflight

Revision `0.1.1` changes no cohort member, replicate, model, condition,
attempt budget, denominator, or claim. It fixes ordinary revision evidence and
permits only interruption continuation for untouched cells in the same formal
record; it never permits retry or replacement of a cell that has acquired a
workspace or terminal outcome.

## 0. Authority, scope, and precedence

This is the only normative source for Experiment 3's exact Direct-MuJoCo
cohort, Sonnet-only model role, skeleton-assisted condition, replicate plan,
fresh TGCD/IVC rule, denominator, stop point, Evolution exclusion, and
descriptive analysis boundary. It is delegated by
`AUTOADAPTER_2_AUTHORITY.md` Section 0.1. The parent Authority continues to
govern canonical packages, candidate isolation, trusted Harness verdicts,
physical integrity, authenticity, and evidence. A conflict blocks this
experiment until it is prospectively corrected.

Experiment 3 is a bounded construction-cohort run, not a two-condition
shakedown and not an Experience study. It contains one cell for each exact
declared robot configuration and replicate. There is no Producer-backbone
factor, no from-scratch condition, no matched Experience control, and no
Evolution sidecar. The experiment stops after the separately recorded Task
Demo for an admitted driver.

Manifests, protocol files, code, run records, analysis, and thesis prose
implement or report this design but cannot change it. A later revision may fix
an infrastructure prerequisite before dispatch; it may not silently omit,
replace, add, or relabel a cohort configuration or change the 33-cell
denominator after outcomes are inspected.

## 1. Research object and claim boundary

Experiment 3 records whether the complete declared Direct-MuJoCo package and
real model path can execute one bounded skeleton-assisted end-to-end run per
replicate across the project construction cohort. Every cell independently
performs fresh model-authored TGCD and implementation-blind IVC, then Sonnet
STUDY, skeleton-assisted GENERATE, trusted capability validation, and bounded
Repair. If an admitted driver is available, the Framework executes the sealed
five-task Task Demo and then stops.

The exact statistical unit is one `robot_configuration × replicate` cell. The
denominator is exactly 33 cells:

```text
11 declared configurations × 3 replicates (`r01`, `r02`, `r03`) = 33 cells
```

Every cell starts with empty Experience. No cell runs Evolution, creates a
proposal, receives another cell's output, or receives a same-round Experience
record. A cell may record a truthful Task Demo not-run reason when its final
driver is not admitted; it may not turn that absence into a Task Demo pass.

Experiment 3 supports descriptive mechanism evidence: per-configuration
TGCD/IVC execution, driver-validation and Repair outcomes, Task Demo outcomes,
failure classes, resource use, and evidence completeness. Morphology is a
public package fact and a descriptive grouping label only. It is not an
experimental factor, treatment, causal variable, effect estimate, or basis for
a morphology-superiority claim. The experiment does not support an Experience
effect, improvement claim, all-model comparison, from-scratch comparison,
hardware or SDK claim, or generalisation beyond the 33 declared cells.

## 2. Exact cohort and fixed factors

The cohort is the exact eleven configurations declared in parent Authority
Section 1.3. Their IDs, categories, and required task-library snapshot are:

| Morphology category | Exact robot configuration | Public Task Library requirement |
|---|---|---|
| Fixed serial arm | `robotstudio_so101` | 20 or more applicable source-backed tasks |
| Quadruped | `unitree-go2-stock-12dof` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `franka_panda` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `kinova_gen3_robotiq_2f85` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `ufactory_xarm7` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `universal_robots_ur5e_robotiq_2f85` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `piper` | 20 or more applicable source-backed tasks |
| Fixed serial arm | `kuka_iiwa_14` | 20 or more applicable source-backed tasks |
| Dexterous hand | `leap_hand` | 20 or more applicable source-backed tasks |
| Mobile manipulator | `hello_robot_stretch_2` | 20 or more applicable source-backed tasks |
| Bimanual manipulator | `aloha_2` | 20 or more applicable source-backed tasks |

Every cell uses:

- the exact canonical package selected for its configuration;
- Sonnet 4.6 for every model-authored stage;
- `skeleton-assisted` and no `from-scratch` cell;
- an empty Experience input;
- one fresh `r01`, `r02`, or `r03` run with a fresh canonical reset and
  independent workspace; and
- at most three submitted drivers: initial attempt `0` and no more than two
  bounded Repair attempts.

The model and inference settings are fixed as follows:

| Role | Family | Exact model identifier | Temperature | Context limit | Max output |
|---|---|---|---:|---:|---:|
| All model-authored stages | Sonnet 4.6 | `eu.anthropic.claude-sonnet-4-6` | `0.0` | `1,000,000` tokens | `16,384` tokens |

The provider, endpoint/region, transport, timeout, retry policy, dated price
snapshot, and returned identity are pinned before the first formal call. An
unavailable provider is a visible infrastructure blocker and does not remove a
cell from the denominator or permit a substitute model.

## 3. Fresh end-to-end per-cell route

Experiment 3 deliberately does not reuse a design or validation bundle across
replicates. Each cell performs:

1. real Sonnet TGCD from that robot's public Morphology and complete admitted
   source-backed Task Library, without a pre-authored capability catalogue or
   task-to-capability mapping;
2. implementation-blind IVC audit and compilation of that cell's model-authored
   source-grounded criteria and private capability-validation suite;
3. Sonnet STUDY and skeleton-assisted GENERATE using that cell's sealed public
   Capability Design and trusted task-neutral skeleton;
4. trusted Direct-MuJoCo capability validation and bounded Repair, with no more
   than three submitted drivers and the same private suite across attempts; and
5. the separately recorded random five-task Task Demo after a final driver is
   admitted, followed immediately by the stop point.

The complete TGCD output and IVC suite are cell-local evidence. A later
replicate may not inspect, copy, or use an earlier replicate's candidate,
capability design, suite, trace, private report, Task Demo, or failure report.
The two generation conditions are not both run: only skeleton-assisted is
declared. The Task Demo verdict is separate from driver synthesis and never
triggers Repair. Evolution is disabled, so there is no terminal proposal,
human disposition, Experience snapshot, or post-round feedback path.

A host, operator, or Python-process interruption may be continued only from
the same formal run record under the exact recorded Authority, manifest,
protocol, and Git revisions. The continuation leaves every `completed` or
`failed` row unchanged and makes no model call for it. Any non-terminal row
whose cell workspace already exists is retained as an interrupted
infrastructure failure and is not rerun. Only a `predeclared` row with no
workspace may begin. Continuation never retries, replaces, adds, or removes a
formal cell and never changes the denominator of 33.

## 4. Evidence and analysis boundary

The top-level experiment record and each of its 33 cell rows must retain the
exact Authority, manifest, protocol, and Git revisions used for dispatch.
Each cell must also retain a concise run record containing:

- exact robot/configuration, morphology label, package and Task Library
  snapshots, replicate ID, run ID, Authority revision, manifest revision,
  protocol revision, and Git commit;
- Sonnet provider/model identity and settings, model calls, tokens, cost inputs,
  and wall-time measurements;
- TGCD and implementation-blind IVC traces and artifact identities;
- generation condition and skeleton inspection evidence;
- submitted-driver attempts, initial/final capability-validation verdicts,
  private case outcomes, Repair transitions, failure class, and Task Demo
  verdict or truthful not-run reason; and
- complete Framework-controlled videos and video manifest entries for every
  required capability-validation and Task Demo case/repetition.

Report the 33-cell denominator explicitly. Keep blocked or non-evaluable cells
visible and distinguish package, provider, Harness, video, candidate, and
model failures. Summaries may group outcomes by robot configuration and may
show morphology labels descriptively, but no aggregate may be called a
morphology effect or interpreted causally.

The experiment stops after Task Demo. Do not report Evolution outcomes,
Experience dispositions, later-run effects, pass rates for a missing condition,
or a model ranking from this protocol. A Task Demo not-run condition is not a
pass and not silently imputed.

## 5. Readiness blockers

Formal dispatch is authorised only while:

1. all eleven exact packages resolve with complete local MJCF closure,
   twenty or more source-backed tasks, private instances/bindings/guards,
   trusted skeleton, and package/simulator-integrity checks;
2. the exact Sonnet model identity and settings are pinned and callable;
3. candidate-worker isolation, canonical-scene/actuator authenticity,
   independent Harness verdicting, and recorder checks pass; and
4. focused runtime/path checks enforce a fresh singleton workspace and client,
   cell-local completed TGCD and IVC traces, empty Experience, at most three
   driver submissions, required Task Demo/video evidence, and no Evolution
   call or review queue.

Focused package checks and canaries are diagnostic. A missing or incomplete
configuration is an explicit blocker, not a failed model cell; the 33-cell
denominator is not reduced.
