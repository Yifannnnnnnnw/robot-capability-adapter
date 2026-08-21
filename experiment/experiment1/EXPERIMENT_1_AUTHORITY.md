# Experiment 1 — B1 Driver-Synthesis Backbone Comparison Authority

> **Document ID:** `AA2-EXP1`<br>
> **Document role:** sole normative document for Experiment 1<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.26`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.0`<br>
> **Effective date:** 2026-08-21<br>
> **Design status:** active and prospectively fixed; formal execution blocked

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

## 1. Research object and claim boundary

Experiment 1 is the fixed-input B1 driver-synthesis comparison. For each
selected robot, one prior-designed and reference-calibrated capability bundle
is held fixed while seven Producer backbone families synthesize a
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
both generation conditions. A missing, invalid, or uncalibrated skeleton is an
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

Before the first formal call, the manifest must pin each exact provider model
identifier and revision, endpoint/region, transport, inference settings,
context/output limits, timeout, and dated price snapshot. A family that cannot
be pinned remains blocked until a prospective authority revision; it is never
silently replaced.

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

Before any cell starts, every selected robot must have one versioned bundle
containing:

1. the fixed public capability interface;
2. the fixed capability-level pass standards;
3. the complete private `capability_validation_suite.json` with measurement
   bindings, guards, resets, and verdict rules; and
4. reference-calibration evidence showing that the exact suite and execution
   route are feasible.

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

## 8. Formal execution blockers

Formal Experiment 1 remains blocked until all of the following are true:

1. all five fixed validation bundles are versioned and reference-calibrated;
2. exact configurations for all seven backbone families are frozen and their
   transports pass real tool-calling canaries;
3. `r01` through `r05` seeds and randomized block order are frozen;
4. the canonical mainline exposes the B1 STUDY-to-terminal-validation boundary
   without TGCD, IVC, Task Demo, controller, or Evolution stages;
5. the synthesis-only B1 runner records the required identities, resources,
   traces, verdicts, and videos; and
6. focused package, isolation, private-suite, physical-integrity, recorder, and
   reference-calibration checks pass for all five robots.

Directory presence, a structurally valid manifest, or a diagnostic canary does
not clear these blockers or constitute formal evidence.

## 9. Ownership and change control

`experiment/experiment1/` owns this Authority, its derived manifests, formal
run outputs, and Experiment 1-specific analysis. `AutoAdapter-Bench/` remains a
reusable benchmark dependency and must not restate this experiment's cohort,
R, matrix count, stopping decision, or results.

Any change to the five robots, seven backbone families, two conditions, R=3
primary design, attempt budget, fixed-input boundary, primary outcomes, or
extension rule requires a new revision of this file made before affected
outcomes are inspected. Post-outcome changes define a new experiment
configuration and cannot silently overwrite or relabel retained evidence.

## 中文决策摘要（辅助）

Experiment 1 固定为五台机器人、七个 backbone、两种生成条件。核心 `R=3`，共 210 个实验
cell，最多 630 次 driver submission；`r04`、`r05` 只能各自作为完整 70-cell block 追加，全部完成
后为 350 cell、最多 1,050 次 submission。B1 从 STUDY 开始，在最终 capability validation verdict
结束；不运行 TGCD、IVC、Task Demo、high-level controller 或 Evolution。R=3 始终是 primary，后续
完整 R=4/R=5 只作精度与稳健性扩展。八路并发目前只是待 canary 与 quota 验证的调度目标，不是已
准入能力。当前 fixed bundle、七模型配置、seed、B1 runner 与正式并发路径均未冻结，因此正式运行
保持 blocked。
