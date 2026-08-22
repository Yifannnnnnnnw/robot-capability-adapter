# Experiment 1 — B1 Driver-Synthesis Backbone Comparison Authority

> **Document ID:** `AA2-EXP1`<br>
> **Document role:** sole normative document for Experiment 1<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.30`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.17`<br>
> **Effective date:** 2026-08-22<br>
> **Design status:** active and prospectively fixed; formal execution paused

Revision `0.1.17` prospectively raises only M2's fixed per-request output
budget from `8,192` to `32,768` tokens while retaining the revision `0.1.15`
`600 s` total wall-clock deadline. Before the project owner paused the
four-cell revision `0.1.15` M2 rerun, 22 of 32 successful physical provider
calls returned exactly 8,192
output tokens. Nine of those capped calls returned ordinary assistant text with
no tool call, while capped `submit_study` and nine consecutive capped
`check_driver` calls were rejected before a valid checked Driver revision was
established. Every request recorded `budget_exceeded=false`; the largest input
was 89,176 tokens against M2's fixed 1,000,000-token context limit. One
independent HTTP 500 was retried successfully. The observed repeated failure is
therefore treated as output truncation or provider finish-reason normalization,
not accumulated-history exhaustion and not yet as a pure model-capability
outcome.

Every M2 cell started under revision `0.1.16` or earlier remains unchanged as
historical configuration evidence and does not enter the revision `0.1.17`
formal denominator. Before any M2 formal rerun, one non-formal M2 canary must
exercise the real route and record accepted `submit_study`, `check_driver`, and
`submit_driver` transitions; its validation result is diagnostic and does not
enter the formal denominator or Experience. Compatible non-M2 records remain
unchanged, including the revision `0.1.16` SO-101 compatibility rule. This
correction does not change the cohort, model identity, provider route,
conditions, replicates, fixed validation bundles, attempt budget, context or
history budgets, retry policy, concurrency limit, or analysis boundary. Formal
execution remains paused under revision `0.1.16`'s SO-101 gate and still
requires prospective project-owner authorization to restart.

Revision `0.1.16` prospectively adds SO-101 capability A6,
`set_wrist_roll`, to the fixed public Driver interface and its three-case
Harness validation suite. A real B2 pick-place diagnostic exposed a task-level
need for wrist orientation while the prior A1--A5 position-and-gripper
interface provided no callable wrist-rotation action. The project owner
therefore fixed the reusable design requirement that a manipulator whose tasks
require end-effector orientation control must expose an explicit wrist-rotation
capability. A6 accepts a caller-supplied target roll and bounded duration; its
trusted criterion requires timed target tracking while the other arm joints
and gripper remain within the published unintended-motion bounds. The exact
request schema, bounds, and H1/H2/H3 cases
are fixed in `B1_DRIVER_VALIDATION_CRITERIA.md` and the versioned bundle.

This is a new SO-101 fixed-input configuration. SO-101 cells executed against
the earlier A1--A5 interface remain historical and do not enter the revision
`0.1.16` denominator; the other three robots and their already compatible
records are unchanged. Formal execution is paused until the revised SO-101
bundle and trusted Harness binding pass their focused checks and the project
owner prospectively authorizes a restart. A reference positive control remains
optional diagnostic evidence under Sections 4 and 8, not a B1 start gate.
Experiment 1 still starts every cell with empty Experience;
the reviewed public lesson retained for a later matched run is not an input to
this B1 comparison, and the originating B2 diagnostic is not relabelled as a
B1 outcome.

Revision `0.1.15` prospectively raises only M2's fixed per-request total
wall-clock deadline from `120 s` to `600 s`. In the revision `0.1.14` formal
`b1::aloha_2::M2::r01::from-scratch` cell, STUDY completed but the first
Generate response crossed the local `120 s` deadline at `120.16 s`, before a
completed model turn could be recorded. The longer deadline removes that
observed client-side truncation while retaining a strict bounded call and the
existing rule that a timeout is not retried automatically. All four M2 cells
already dispatched under revision `0.1.14` remain unchanged as historical
configuration records but do not enter the revision `0.1.15` formal
denominator: both ALOHA 2 `r01` conditions and both LEAP Hand `r03` conditions
use new workspaces under the common `600 s` M2 setting. Non-M2 revision
`0.1.13` and `0.1.14` records remain valid. This correction does not change the
cohort, backbones, conditions, replicates, fixed validation bundles, attempt
budget, provider route, concurrency limit, or analysis boundary; formal
execution remains authorized.

Revision `0.1.14` prospectively normalizes one observed provider-facing native
history edge case. In both M2 × ALOHA 2 `r01` conditions, the first Generate
turn returned HTTP 200 at the 8,192-token request limit but supplied an empty
assistant message with no tool call; replaying that empty message on the next
logical turn reproducibly produced HTTP 500 and its one bounded physical retry
also failed. The transport now replaces only an empty assistant turn that has
no tool calls with a fixed neutral observation before the next request. It does
not alter non-empty assistant content, tool calls, tool observations, prompts,
budgets, or retry policy. The two revision `0.1.13` infrastructure-blocker
records remain unchanged. M2 stays paused until a non-formal canary exercises
the corrected continuation, after which affected cells use new workspaces.
Revision `0.1.13` records that did not exercise this empty-turn branch remain
valid. This correction does not change the cohort, backbones, conditions,
replicates, fixed validation bundles, attempt budget, or analysis boundary.

Revision `0.1.13` records the project owner's explicit authorization on
2026-08-22 to restart formal execution under the already fixed four-robot,
six-backbone design. Before the first formal cell, a bounded M6 × SO-101
two-condition diagnostic canary may exercise both generation routes; its
outputs are readiness evidence only and do not enter the formal denominator or
Experience. Formal cells then consume the already frozen global dispatch order,
with isolated concurrency ramping from one to at most eight workers while
infrastructure and evidence completeness are checked. This revision does not
change the cohort, backbones, conditions, replicates, fixed validation bundles,
attempt budget, provider routes, or analysis boundary, and it does not relabel
any earlier diagnostic or formal outcome.

Revision `0.1.12` prospectively supplies the task-neutral skeleton corrections
permitted by the parent Authority. For the skeleton-assisted condition, the
shared serial-arm DLS source now exposes position-only Cartesian polyline
tracking from caller-supplied world-frame points, with bounded reference
progression and fresh-state cross-track feedback; this changes the supplied
source for ALOHA 2 and SO-101. The Go2 source now exposes fresh body-origin and
body-yaw velocity observations plus bounded 40 ms PI velocity tracking, and its
fallback PD gait retains phase across short calls. The raw learned-policy
primitive remains available and unchanged. Explicit Go2 worker allowlisting is
runtime isolation wiring, not another generation condition.

These source changes create a new skeleton-assisted configuration; their
diagnostic checks are not Experiment 1 outcomes or Experience. No prior outcome
is relabelled. If any earlier cells are retained, every executed ALOHA 2,
SO-101, or Go2 skeleton-assisted backbone/replicate cell is affected and must
be regenerated and validated under this revision. LEAP Hand and from-scratch
inputs are unchanged. This revision does not change the fixed capability
interfaces or validation bundles, cohort, backbones, conditions, replicate
plan, attempt budget, provider routes, or analysis boundary, and it does not
authorize a formal restart. Formal execution remains paused. The parent update
to `AA2-AUTH` revision `0.19.30` records its independent B2 delegation and does
not otherwise change Experiment 1.

Revision `0.1.11` prospectively adopts the minimum corrections needed for the
fixed four-robot B1 Harness to implement revision `0.1.10` without known false
successes or unreachable cases. The corrected bundle binds SO-101 A4 to the
complete gripper contact subtree; replaces LEAP L3 targets and L6 offsets with
reachable, discriminating cases; and permits fingertip-target collision while
rejecting contact with a non-corresponding target. The trusted Harness now
enforces request and per-leg timing, actuator control ranges, the published
SO-101 and ALOHA unintended-motion limits, the held precontact gate, and the
complete designed-capability set when applying the two-of-three case rule.
The corrected artifacts use new fixed-design, pass-standard, suite, and bundle
identities so prior outcomes are not relabelled. The earlier Repair
wall-deadline and candidate-feedback-filter observations remain diagnostic run
and implementation records only: consistent with Sections 4 and 8, they do not
become Experiment 1 Experience input. This revision does not authorize a
formal restart or change the cohort, backbones, conditions, replicate plan,
attempt budget, provider routes, or analysis boundary. Formal execution remains
paused.

Revision `0.1.10` prospectively removes `hello_robot_stretch_2` and
M7/Qwen3 32B from the active Experiment 1 matrix by explicit project-owner
instruction. The active matrix is now four robots, six backbones, two
conditions, and three core replicates: 144 core cells with at most 432 Driver
submissions. Each complete extension replicate adds 48 cells and at most 144
submissions; completing both produces 240 cumulative cells and at most 720
submissions. This revision also prospectively fixes capability aggregation:
at least two of the three H1/H2/H3 cases must pass for a capability to pass,
and every capability assigned to the robot must pass for the Driver to pass.
Existing Stretch and M7 packages, fixed bundles, provider files, run records,
and other historical artifacts may remain as inactive history; they are not
inputs to this revision and prior outcomes are not relabelled. Formal
execution remains paused.

Revision `0.1.9` updates the parent-Authority reference after `AA2-AUTH`
revision `0.19.29` permitted task-neutral closed-loop Cartesian path tracking in
the serial-arm trusted skeleton. This permission does not change the currently
supplied skeleton, package snapshot, fixed bundle, retained outcome, execution
pause, cohort, factors, replicate plan, attempt budget, provider route, or
analysis boundary. Implementing or supplying a changed skeleton after outcomes
have been inspected creates a new Experiment 1 configuration and requires every
affected cell to be rerun under Sections 4 and 9.

Revision `0.1.8` prospectively raises only M5's fixed per-request total
wall-clock deadline from `180 s` to `600 s`. A diagnostic M5 × SO-101 cell
completed STUDY, generation, Driver submission, and physical validation, then
received HTTP 200 for a Repair request whose response body crossed the local
`180 s` deadline. The longer deadline removes that observed client-side
truncation while retaining a strict bounded call and the existing rule that a
timeout is not retried automatically. Outcomes recorded under earlier
revisions remain unchanged and are not relabelled. Formal execution remains
paused; this revision does not authorize a restart or change the cohort,
backbones, conditions, replicate plan, attempt budget, provider route, or
analysis boundary.

Revision `0.1.7` pauses formal execution after diagnostic review identified
defects in the retained LEAP Hand L4/L5 fixed validation path. It does not
adopt an unreviewed replacement suite, relabel earlier outcomes, or authorize
a restart. It does not change the cohort, backbones, conditions, replicate
plan, attempt budget, provider routes, or analysis boundary.
Revision `0.1.6` prospectively completes the required provider pins before any cell is
dispatched under this revision: M1--M4 and M6 gain explicit context/output limits and dated
standard-price snapshots, while the M4 and M6 request maxima are corrected to their provider
limits of 5,000 and 8,000 output tokens. It does not change the cohort, model identities or
routes, factors, replicate plan, attempt budget, analysis boundary, or execution status.
Revision `0.1.5` updates only the parent-Authority reference after `AA2-AUTH` revision `0.19.28`
made the existing fixed-input B1 design the sole current Experiment 1. It does not change the
cohort, factors, replicate plan, attempt budget, provider settings, analysis boundary, or execution
status. Revision `0.1.4` admits up to eight concurrently executing isolated cell
workers by explicit project-owner approval. This operational approval applies
only to cells started under this revision or later; it does not retrospectively
admit, validate, or relabel a run started under revision `0.1.3` or earlier, or
alter its recorded evidence, failure classification, or verdict. It does not
change the cohort, factors, replicate plan, attempt budget, or analysis
boundary. Revision `0.1.3` fixes
every Producer deployment to a remotely hosted API and
prohibits local-weight or local-inference substitutions. M1--M4, M6, and M7
use the organization company gateway; M5 uses the official DeepSeek API with
the project owner's account. Revision `0.1.2`
fixes the sole start prerequisite: all five public Driver contracts and their
Harness-evaluable validation criteria must be clear, and the isolated
STUDY-to-generation/Repair/validation route must be usable while recording the
required evidence. Task-blind reference calibration is not a prerequisite or
an Experiment 1 input. Revision `0.1.1` organized Experiment 1 around the experimental object,
required preparation, and required records, and prospectively fixes the
timing, submitted-attempt, physical provider-request, and observable action
trace for every cell. It does not change the cohort, factors, replicate plan,
or attempt budget.

## 0. Authority, scope, and precedence

This is the only normative source for Experiment 1's exact cohort, Producer
backbone families, generation conditions, replicate plan, attempt budget,
analysis boundary, extension rule, and execution blockers. It is the bounded
experiment authority delegated by `AUTOADAPTER_2_AUTHORITY.md` Section 0.1.

The parent Authority continues to govern the project architecture, canonical
robot packages, Direct-MuJoCo execution, trusted Harness, candidate isolation,
physical-integrity rules, authenticity, evidence, and SDK boundary. This file
may narrow an experiment selection but may not weaken those project-wide
requirements. A conflict blocks Experiment 1 until it is prospectively fixed.

`AutoAdapter-Bench/` supplies reusable benchmark protocols, registries,
catalogues, and a generic manifest resolver. It does not own Experiment 1's
selection or denominator. The derived `manifest.json`, run records, analysis
code, README files, thesis text, historical Chapter 3 plans, and the proposed
`autoadapter/B1_DRIVER_CAPABILITY_BENCHMARK_SPEC.md` are non-normative. If any
of them conflicts with this file, this file governs.

This revision supersedes every earlier 10-plus-5, 11-robot, 14-robot, R=5-only,
525-cell, 665-cell, or 770-cell Chapter 3/Experiment 1 matrix. Those designs may
remain in Git history but are not active Experiment 1 requirements.

### 0.1 Three-part experiment contract

Every concrete experiment authority fixes exactly three classes of information:

| Part | Experiment 1 content | Governing sections |
|---|---|---|
| Experimental object | Research claim, four robots, six backbones, two conditions, statistical unit, and replicate plan | Sections 1–3 |
| Required preparation | Clear Driver contracts and criteria plus a usable, isolated, recorded STUDY-to-terminal-validation route | Sections 4, 5, 7, and 8 |
| Required records | Outcomes, evidence, timings, submitted attempts, model/provider calls, tokens, costs, errors, and observable action trace | Section 6 |

No subordinate manifest, benchmark file, implementation, or report may add or
change an Experiment 1-specific object, preparation requirement, or recording
requirement. It may only implement this three-part contract.

## 1. Research object and claim boundary

Experiment 1 is the fixed-input B1 driver-synthesis comparison. For each
selected robot, one prior-designed Driver contract and its validation criteria
are held fixed while six Producer backbone families synthesize a
robot-specific `driver.py` under `skeleton-assisted` and `from-scratch`.

The experiment measures driver-validation success, Repair behaviour, failures,
model use, cost, and time. It does not evaluate TGCD, IVC, Task Demo,
high-level-controller use, Evolution, real-SDK fidelity, hardware performance,
sim-to-real transfer, or universal model/robot superiority. One configuration
represents each selected morphology; morphology results are descriptive and
associational, not causal or morphology-wide estimates.

## 2. Fixed factors

### 2.1 Robot configurations

Experiment 1 contains exactly these four configurations:

| Blocking category | Canonical robot configuration |
|---|---|
| Fixed serial arm | `robotstudio_so101` |
| Quadruped | `unitree-go2-stock-12dof` |
| Dexterous hand | `leap_hand` |
| Bimanual manipulator | `aloha_2` |

All four currently have a canonical trusted-skeleton source and therefore run
both generation conditions. A missing, invalid, or unusable skeleton is an
infrastructure blocker; it does not convert that robot into a scratch-only row
and is not scored as a model failure.

### 2.2 Producer backbone families

The fixed family set contains exactly six entries:

| ID | Family | Vendor |
|---|---|---|
| `M1` | Sonnet 4.6 | Anthropic |
| `M2` | Opus 5 | Anthropic |
| `M3` | Haiku 4.5 | Anthropic |
| `M4` | Nova Pro | Amazon |
| `M5` | DeepSeek V4 Pro | DeepSeek |
| `M6` | Ministral 3 8B | Mistral |

All six Producers are invoked through remotely hosted APIs. M1--M4 and M6 use
the organization company gateway. M5 uses the official DeepSeek API
through the project owner's account and must return the exact
`deepseek-v4-pro` identity. Experiment 1 does not use downloaded weights,
self-hosted inference, or a local model as a substitute. A direct vendor
endpoint or an organization-approved company API gateway is acceptable only
when its exact route and returned model identity are recorded; the deployment
route is fixed per backbone before its first affected cell.

Before cells for one backbone are dispatched, the manifest must pin that
backbone's exact provider model identifier and revision, endpoint/region,
transport, inference settings, context/output limits, timeout, and dated price
snapshot. An unpinned family blocks only its affected cells and is never
silently replaced; it does not prevent an unrelated ready backbone from
starting.

### 2.3 Generation conditions

Every robot, backbone, and replicate runs both:

- `skeleton-assisted`: the model may inspect and use the robot's trusted
  skeleton under the parent-Authority boundary;
- `from-scratch`: the model may not inspect, import, copy, or invoke the
  trusted skeleton and must implement the allowed controller logic itself.

For one robot-backbone-replicate block, the conditions share the same fixed
capability interface, pass standards, complete private validation suite, model
identity/settings, package snapshot, environment, budgets, and evidence rules.
They use isolated model sessions, workspaces, candidates, validation feedback,
Repair histories, and generated drivers. Neither condition receives the other
condition's artifacts or results. Every core and extension cell starts with the
same empty Experience input; no prior-run or same-round Experience enters a
model context.

## 3. Experimental unit, replicates, and accounting

The sole statistical unit is one independent
`robot × backbone × generation condition × replicate` cell. Capabilities,
private cases/repetitions, development turns, validation trials, and Repair
attempts are repeated observations inside that cell and never inflate the
sample size.

### 3.1 Core design

The primary design uses `r01`, `r02`, and `r03`:

```text
4 robots × 6 backbones × 2 conditions × 3 replicates = 144 cells
144 cells × at most 3 submitted drivers = at most 432 submissions
```

Experiment 1 core completion requires all 144 planned cells to have a truthful
terminal verdict or an explicit infrastructure blocker. A blocker remains in
the planned denominator and is not converted into driver failure.

### 3.2 Prespecified precision extension

`r04` and `r05` are optional, prespecified precision extensions. Each is one
complete balanced block:

```text
4 robots × 6 backbones × 2 conditions = 48 additional cells
48 cells × at most 3 submitted drivers = at most 144 additional submissions
```

Completing both produces 240 cumulative cells and at most 720 submissions.
An extension decision may depend only on recorded operational facts such as
remaining calendar time, budget, provider availability, and unchanged frozen
configuration. It may not depend on success rates, backbone rankings, condition
differences, p-values, or failure patterns. No robot, backbone, or condition
may receive selective extra replicates. `r04` must be a complete block before
`r05` begins; an incomplete extension does not alter the R=3 primary analysis.

R=3 remains the primary analysis whether or not extension data are collected.
Complete R=4 or R=5 results are reported as cumulative precision/sensitivity
analyses with execution wave recorded. If R=3 outcomes inform the decision to
collect more data, the later data are a separately reported follow-up rather
than a retrospectively pooled primary sample.

The independent seed map and randomized execution order for `r01` through
`r05` must be frozen before the first formal model call.

## 4. Fixed input and execution path

Before the first cell starts, every selected robot must have one fixed
Driver-and-criteria definition containing:

1. the fixed public capability interface;
2. the fixed capability-level pass standards;
3. the complete private `capability_validation_suite.json` with measurement
   bindings, guards, resets, and verdict rules.

The public method names, request schemas, units, frames, bounds, and pass
standards are recorded in `B1_DRIVER_VALIDATION_CRITERIA.md`. The private suite
supplies only the hidden concrete targets, resets, bindings, and guard data
needed for the trusted Harness to execute those declared criteria. A bundle or
directory is merely an implementation container for these fixed inputs; it is
not a separate admission workflow. No task-blind reference driver or reference
calibration is required. Any such run is optional diagnostic evidence and does
not enter the Experiment 1 denominator or determine whether B1 may start.

That bundle is identical across every backbone, condition, replicate, and
attempt for the robot. A bundle change after outcomes are inspected creates a
new experiment configuration and requires every affected cell to be rerun.

Each cell follows exactly this path:

```text
load fixed bundle
  -> STUDY
  -> GENERATE (skeleton-assisted) or GEN_ALGO (from-scratch)
  -> submit attempt 0
  -> complete private validation 0
  -> if failed: Repair 1 -> submit attempt 1 -> complete validation 1
  -> if failed: Repair 2 -> submit attempt 2 -> complete validation 2
  -> terminal verdict
```

The first pass stops the cell. At most three generated drivers may be
submitted. Development probes and edits before an explicit submission do not
create additional attempts. Repair may change only `driver.py` and receives
the immediately preceding driver plus the complete candidate-facing report
allowed by `AA2-AUTH` Section 3.5. It never receives the private suite
definition, the other condition's artifacts, or another cell's evidence.

Experiment 1 begins at STUDY and stops at the terminal driver-validation
verdict. It does not run TGCD, IVC, Task Demo, a high-level controller, or
Evolution, and none of those resources or outcomes enter its denominator.

## 5. Validation, isolation, and retained evidence

Every submitted driver runs every case and repetition in the same complete
robot-specific suite. Each case passes only when its conjunctive public metric,
temporal/order, closed-loop, canonical actuator-plus-physics,
physical-integrity, source/import/build, and complete-video requirements
inherited from the parent Authority all pass. A capability passes when at least
two of its three H1/H2/H3 cases pass. A Driver passes only when every capability
assigned to its robot passes; there is no compensation between capabilities or
model self-report substitute for a Harness verdict.

Each condition and attempt uses the required isolated workspace and candidate
worker. Every formal validation case/repetition retains a continuous,
Framework-controlled video and matching trace/verdict record. Missing or
corrupt required evidence is an evidence failure, not a pass.

The Framework may expose the parent-Authority candidate-facing report only to
same-cell Repair. Complete reports, private inputs, traces, videos, and results
must not enter another cell, manual adaptation, or Experience. Detailed R=3
evidence remains sealed from outcome-guided experiment changes through the
recorded extension decision. If it is inspected before an extension, later
replicates require a prospectively registered held-out suite and are reported
as a separate follow-up wave.

## 6. Outcomes and analysis boundary

Primary driver-validation outcomes are:

- `pass@0`;
- final pass within at most three submitted drivers;
- Repair gain;
- attempts to first pass;
- valid-driver count and rate; and
- cell completion.

Required resource outcomes cover every applicable STUDY, GENERATE/GEN_ALGO,
and Repair call, including failed calls and failed cells: provider request and
error records, model-call count, provider-reported token categories, billed or
manifest-price-estimated cost, wall time to attempt-0 verdict, wall time to
terminal verdict, and separable model-service, probe, and MuJoCo-validation
time where measurable.

### 6.1 Mandatory timing, attempt, and call trace

For recording and reporting, one Experiment 1 run or "round" means exactly one
`robot × backbone × condition × replicate` cell. Every record retains those
four identities and the cell ID; a scheduler batch is operational metadata and
must not replace the cell as the experimental unit.

Each cell records UTC start/end and monotonic total wall time from STUDY entry
to terminal verdict, time to the attempt-0 verdict, queue and active time when
concurrent, and separate STUDY, GENERATE/GEN_ALGO, Repair, tool/probe, and
validation times where measurable.

STUDY is not a submitted-driver attempt. Each bounded generation or Repair
slot records its target `attempt_index` (0, 1, or 2), start/end and monotonic
wall time, model/tool/validation time, whether `submit_driver` was accepted,
the validation verdict when submitted, and its transition or stop reason. The
cell's actual submitted-attempt count is the number of accepted submission
events and may be zero through three; failed development that never submits a
driver does not inflate that count.

Every physical provider request is a separate ordered call record, including
successes, failures, timeouts, 429/503 responses, and retries. Each record
contains call and model-turn indices, stage, nullable target attempt, retry
relationship, provider request ID when available, UTC start/end, monotonic
elapsed time, requested and returned model identity, status/error,
provider-reported input/output/cache/reasoning/other token categories with
unavailable values stored as null, and per-call cost under the frozen price
snapshot. A retry is another provider call but is not another plotted iteration
unless it returns a completed observable model turn.

Every completed observable model turn records its ordered iteration, stage,
target attempt, elapsed time, tool name/outcome, submission event, and
stage/attempt transition. Its raw action type is one of `observe_or_plan`,
`execute_clean`, `execute_error`, or `submit`, classified only from observable
tool behaviour. For the reference visualization, `observe_or_plan` is the grey
read/plan segment, `execute_clean` and successful `submit` are green,
`execute_error` is red, and each stage transition supplies the black boundary.

Iteration count, execution-error count, provider-error count, retry count,
action stacks, token totals, and STUDY/GENERATE/REPAIR boundary positions must
be derived from the ordered raw records rather than entered manually. Numeric
provider-reported reasoning-token usage may be retained; hidden chain-of-thought
or hidden reasoning content must never be requested or stored.

Primary summaries report counts/rates and equal-robot macro-averages by
backbone and condition, plus matched within-block condition differences.
Per-robot, per-capability, failure-class, Repair, cost, and time breakdowns are
reported without turning inner measurements into independent samples. With
R=3, one exact robot-backbone-condition rate has only four possible values
(0, 1/3, 2/3, 1); the experiment therefore does not claim stable fine-grained
cell rankings, equivalence, or broad morphology generalization. Any additional
inferential model, multiplicity policy, or confidence-interval procedure must
be fixed before outcomes are inspected.

## 7. Scheduling and concurrency

Concurrency is an execution setting, not an experimental factor. From revision
`0.1.4`, the planning target and admitted maximum are eight isolated cell
workers. Each worker must have its own process, client, workspace, and evidence
destination, with recorded queue time, 429/503 handling, retries, and active
wall time. An observed provider, account-quota, or host-capacity problem lowers
the affected dispatch cap until it is resolved. Using a lower concurrency does
not change the matrix or statistical unit.

## 8. Sole start prerequisite

Experiment 1 may start as soon as both parts below are true:

1. all four selected robots have clear fixed Driver interfaces and clear
   Harness-evaluable validation criteria, including the private values needed
   to execute each criterion; and
2. both isolated generation routes are usable from STUDY through
   `GENERATE`/`GEN_ALGO`, conditional bounded Repair, complete validation, and
   terminal verdict, while retaining the records required by Section 6.

There is no task-blind reference-calibration gate and no additional global
admission workflow. Exact provider/model settings must still be fixed and
recorded for each affected backbone before its cells are dispatched; a
transport or account problem blocks those affected cells, not the start of
unrelated ready cells. The already frozen seed/order component determines
dispatch order. Eight-way concurrency is admitted but optional; serial
execution remains valid.

A readable criterion with no working Harness binding, or a route that omits
the mandatory records, does not satisfy these two parts. Directory presence or
a structurally valid manifest alone is not execution evidence.

## 9. Ownership and change control

`experiment/experiment1a_generation/` owns this Authority, its derived manifests, formal
run outputs, and Experiment 1-specific analysis. `AutoAdapter-Bench/` remains a
reusable benchmark dependency and must not restate this experiment's cohort,
R, matrix count, stopping decision, or results.

Any change to the four robots, six backbone families, remotely hosted API
deployment boundary or fixed per-backbone route, two conditions, R=3
primary design, attempt budget, fixed-input boundary, primary outcomes,
mandatory recording hierarchy/action classification, or extension rule
requires a new revision of this file made before affected outcomes are
inspected. Post-outcome changes define a new experiment configuration and
cannot silently overwrite or relabel retained evidence.

## 中文决策摘要（辅助）

Experiment 1 固定为四台机器人、六个 backbone、两种生成条件。核心 `R=3`，共 144 个实验
cell，最多 432 次 driver submission；`r04`、`r05` 只能各自作为完整 48-cell block 追加，全部完成
后为 240 cell、最多 720 次 submission。B1 从 STUDY 开始，在最终 capability validation verdict
结束；不运行 TGCD、IVC、Task Demo、high-level controller 或 Evolution。R=3 始终是 primary，后续
完整 R=4/R=5 只作精度与稳健性扩展。每个 capability 的 H1/H2/H3 至少通过 2 个才算该
capability 通过，而 Driver 必须通过其全部 capability。开跑的唯一前置条件是：四台机器人的
Driver 接口与 Harness
可判定 criteria 全部清晰，以及两条 STUDY-to-generation/Repair/validation 路线可用并能写出第 6 节
要求的记录。不要求 task-blind reference calibration。`0.1.4` 已明确批准最多八个隔离 cell 并发，
但该批准只向前适用于按本 revision 派发的 cell，不追溯改变旧 cell 的 Authority revision、证据、
失败分类或 verdict；八路并发并非必须，串行仍然有效。每个 cell、attempt slot、stage、模型
iteration、物理 provider request 与 tool event 都
必须记录可审计用时；每个 cell 记录实际 submission 次数，每个可观察 iteration 固定分类为
`observe_or_plan`、`execute_clean`、`execute_error` 或 `submit`，并保留错误、token、成本和 stage 边界。
失败与 retry call 不能丢弃；隐藏思维内容不得记录。
