# Experiment 1 — B1 Driver-Synthesis Backbone Comparison Authority

> **Document ID:** `AA2-EXP1`<br>
> **Document role:** sole normative document for Experiment 1<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.27`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.3`<br>
> **Effective date:** 2026-08-21<br>
> **Design status:** active and prospectively fixed; pre-run preparation active

Revision `0.1.3` fixes every Producer deployment to a remotely hosted API and
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
| Experimental object | Research claim, five robots, seven backbones, two conditions, statistical unit, and replicate plan | Sections 1–3 |
| Required preparation | Clear Driver contracts and criteria plus a usable, isolated, recorded STUDY-to-terminal-validation route | Sections 4, 5, 7, and 8 |
| Required records | Outcomes, evidence, timings, submitted attempts, model/provider calls, tokens, costs, errors, and observable action trace | Section 6 |

No subordinate manifest, benchmark file, implementation, or report may add or
change an Experiment 1-specific object, preparation requirement, or recording
requirement. It may only implement this three-part contract.

## 1. Research object and claim boundary

Experiment 1 is the fixed-input B1 driver-synthesis comparison. For each
selected robot, one prior-designed Driver contract and its validation criteria
are held fixed while seven Producer backbone families synthesize a
robot-specific `driver.py` under `skeleton-assisted` and `from-scratch`.

The experiment measures driver-validation success, Repair behaviour, failures,
model use, cost, and time. It does not evaluate TGCD, IVC, Task Demo,
high-level-controller use, Evolution, real-SDK fidelity, hardware performance,
sim-to-real transfer, or universal model/robot superiority. One configuration
represents each selected morphology; morphology results are descriptive and
associational, not causal or morphology-wide estimates.

## 2. Fixed factors

### 2.1 Robot configurations

Experiment 1 contains exactly these five configurations:

| Blocking category | Canonical robot configuration |
|---|---|
| Fixed serial arm | `robotstudio_so101` |
| Quadruped | `unitree-go2-stock-12dof` |
| Dexterous hand | `leap_hand` |
| Mobile manipulator | `hello_robot_stretch_2` |
| Bimanual manipulator | `aloha_2` |

All five currently have a canonical trusted-skeleton source and therefore run
both generation conditions. A missing, invalid, or unusable skeleton is an
infrastructure blocker; it does not convert that robot into a scratch-only row
and is not scored as a model failure.

### 2.2 Producer backbone families

The fixed family set contains exactly seven entries:

| ID | Family | Vendor |
|---|---|---|
| `M1` | Sonnet 4.6 | Anthropic |
| `M2` | Opus 5 | Anthropic |
| `M3` | Haiku 4.5 | Anthropic |
| `M4` | Nova Pro | Amazon |
| `M5` | DeepSeek V4 Pro | DeepSeek |
| `M6` | Ministral 3 8B | Mistral |
| `M7` | Qwen3 32B | Alibaba |

All seven Producers are invoked through remotely hosted APIs. M1--M4, M6, and
M7 use the organization company gateway. M5 uses the official DeepSeek API
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
5 robots × 7 backbones × 2 conditions × 3 replicates = 210 cells
210 cells × at most 3 submitted drivers = at most 630 submissions
```

Experiment 1 core completion requires all 210 planned cells to have a truthful
terminal verdict or an explicit infrastructure blocker. A blocker remains in
the planned denominator and is not converted into driver failure.

### 3.2 Prespecified precision extension

`r04` and `r05` are optional, prespecified precision extensions. Each is one
complete balanced block:

```text
5 robots × 7 backbones × 2 conditions = 70 additional cells
70 cells × at most 3 submitted drivers = at most 210 additional submissions
```

Completing both produces 350 cumulative cells and at most 1,050 submissions.
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
robot-specific suite. Passing requires the conjunctive public metric,
temporal/order, closed-loop, canonical actuator-plus-physics, physical-integrity,
source/import/build, and complete-video requirements inherited from the parent
Authority. There is no majority vote, partial capability credit, compensation
between capabilities, or model self-report substitute for a Harness verdict.

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

Concurrency is an execution setting, not an experimental factor. The planning
target is at most eight isolated cell workers. Eight-way execution is not
currently admitted. It may be enabled only after realistic 1-to-2-to-4-to-8
provider canaries and account-quota checks establish a safe global cap and
per-provider caps, and after the runner provides one process, client, unique
workspace, and evidence destination per cell with recorded queue time,
429/503 handling, retries, and active wall time. A lower admitted concurrency
does not change the matrix or statistical unit.

## 8. Sole start prerequisite

Experiment 1 may start as soon as both parts below are true:

1. all five selected robots have clear fixed Driver interfaces and clear
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
dispatch order. Eight-way concurrency is optional and may remain disabled;
serial execution is valid.

A readable criterion with no working Harness binding, or a route that omits
the mandatory records, does not satisfy these two parts. Directory presence or
a structurally valid manifest alone is not execution evidence.

## 9. Ownership and change control

`experiment/experiment1/` owns this Authority, its derived manifests, formal
run outputs, and Experiment 1-specific analysis. `AutoAdapter-Bench/` remains a
reusable benchmark dependency and must not restate this experiment's cohort,
R, matrix count, stopping decision, or results.

Any change to the five robots, seven backbone families, remotely hosted API
deployment boundary or fixed per-backbone route, two conditions, R=3
primary design, attempt budget, fixed-input boundary, primary outcomes,
mandatory recording hierarchy/action classification, or extension rule
requires a new revision of this file made before affected outcomes are
inspected. Post-outcome changes define a new experiment configuration and
cannot silently overwrite or relabel retained evidence.

## 中文决策摘要（辅助）

Experiment 1 固定为五台机器人、七个 backbone、两种生成条件。核心 `R=3`，共 210 个实验
cell，最多 630 次 driver submission；`r04`、`r05` 只能各自作为完整 70-cell block 追加，全部完成
后为 350 cell、最多 1,050 次 submission。B1 从 STUDY 开始，在最终 capability validation verdict
结束；不运行 TGCD、IVC、Task Demo、high-level controller 或 Evolution。R=3 始终是 primary，后续
完整 R=4/R=5 只作精度与稳健性扩展。开跑的唯一前置条件是：五台机器人的 Driver 接口与 Harness
可判定 criteria 全部清晰，以及两条 STUDY-to-generation/Repair/validation 路线可用并能写出第 6 节
要求的记录。不要求 task-blind reference calibration，也不要求先启用八路并发；可从串行 ready cell
开始。每个 cell、attempt slot、stage、模型 iteration、物理 provider request 与 tool event 都
必须记录可审计用时；每个 cell 记录实际 submission 次数，每个可观察 iteration 固定分类为
`observe_or_plan`、`execute_clean`、`execute_error` 或 `submit`，并保留错误、token、成本和 stage 边界。
失败与 retry call 不能丢弃；隐藏思维内容不得记录。
