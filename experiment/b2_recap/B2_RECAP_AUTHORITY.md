# B2 ReCAP Capability-Interface-Use Extension Authority

> **Document ID:** `AA2-B2`<br>
> **Document role:** sole normative document for the B2 ReCAP capability-interface-use extension<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.30`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.0`<br>
> **Effective date:** 2026-08-22<br>
> **Design status:** active and prospectively fixed; formal execution paused

Revision `0.1.0` prospectively fixes a bounded comparison of one ReCAP high-level-controller
architecture across seven replaceable LLM backbones. It fixes two robots, five source-backed
compositional tasks per robot, three core replicates, and 210 formal episodes. The same validated
reference driver, capability interface, typed adapter, controller logic, public-observation
projection, task Harness, and episode budgets are held fixed within each robot; only the controller
backbone varies. Diagnostic calibration and oracle canaries are not formal episodes. Formal
execution remains paused under Section 7.

**中文辅助说明。** `0.1.0` 事先固定一项有界 ReCAP 高层控制比较：同一架构替换七个
LLM backbone，使用两台机器人、每台五项有来源的组合 task 和三次正式 replicate，共 210 个
episode。每台机器人的 reference driver、capability interface、typed adapter、controller 逻辑、公开观测投影、
task Harness 和 episode budget 在所有 backbone 间保持一致；只有 controller backbone 变化。诊断校准和
oracle canary 不计入正式 episode。第 7 节的 blocker 解除前，正式执行暂停。

## 0. Authority, identity, and precedence

This is the only normative source for B2's exact controller architecture, robots, tasks, backbone
factor, replicate plan, fixed inputs, analysis boundary, and execution blockers. It is the bounded
extension authority delegated by `AUTOADAPTER_2_AUTHORITY.md` Section 0.1. The parent Authority
continues to govern Direct-MuJoCo execution, canonical robot packages, candidate isolation, trusted
Harness verdicts, physical integrity, authenticity, evidence, and the SDK boundary. A conflict
blocks B2 until it is prospectively corrected.

`B2` is an extension identifier. It is **not Experiment 2**, does not execute Task-Grounded
Capability Design or implementation-blind IVC, and cannot support an Experiment 2 or RQ2 claim. It
also does not evaluate Driver Synthesis, Evolution, real SDKs, hardware, or sim-to-real transfer.
Within B2 only, this authority selects ReCAP in place of the general post-admission ReAct path in
the parent Authority's Section 3.6.

Manifests, source code, benchmark documents, run records, analysis, and thesis prose implement or
report this design but cannot change it. A later revision may fill a prerequisite pin before formal
dispatch; it may not silently add a robot, task, backbone, condition, replicate, or outcome after
formal outcomes have been inspected.

**中文辅助说明。** 本文件是 B2 架构、机器人、task、backbone 因素、replicate、固定输入、分析边界
和执行 blocker 的唯一规范来源。B2 只是扩展标识，**不是 Experiment 2**；它不执行 TGCD 或
implementation-blind IVC，也不支持 Experiment 2 或 RQ2 主张。仅在 B2 内，ReCAP 取代父权威第 3.6 节的
通用 ReAct 路径。从属 manifest、代码、run record、分析和论文文本不得修改本设计。

## 1. Research object and claim boundary

B2 asks a narrow question: with the robot, source-backed task, fixed validated
driver–interface–adapter stack, ReCAP algorithm, prompt, public feedback, budgets, and Harness held
constant, how does the replaceable LLM backbone affect task-level physical success and resource use?

The sole manipulated factor is the ReCAP controller's LLM backbone (`M1`–`M7`). Robot, task, and
replicate are fixed blocking dimensions, not additional experimental factors. Results may support
only a bounded comparison on the ten selected Direct-MuJoCo task configurations. They do not show
that ReCAP is superior to ReAct or another controller, that one model is universally superior, that
the reference driver was synthesized, or that untested tasks, robots, or hardware work.

The primary outcome is the trusted Harness's binary task-success verdict for one episode. Secondary
records are per-clause verdicts, physical-integrity verdict, controller terminal status, model and
capability call counts, invalid outputs, tokens, model cost, simulation time, and wall time. A
controller stop, empty root plan, or self-report is never task success.

**中文辅助说明。** B2 只问：在机器人、有来源 task、已验证 driver–interface–adapter、ReCAP
逻辑、prompt、公开反馈、budget 和 Harness 都固定时，替换 LLM backbone 如何影响物理 task 成功和资源
使用。唯一操作因素是 `M1`–`M7`；robot、task 和 replicate 是固定 blocking dimension。主 outcome 仅由可信
Harness 的 episode-level 二元 task verdict 给出。

## 2. Fixed controller architecture and execution boundary

### 2.1 ReCAP algorithm

Every formal episode uses one fixed bounded ReCAP controller with these semantics:

1. the current node is recursively decomposed into an ordered list containing abstract subtasks or
   typed capability leaves;
2. only the head item is processed before the next model revision;
3. an abstract subtask creates a child context, while a capability leaf invokes exactly one public
   capability with its capability-native request;
4. after execution, one bounded public operation observation is returned and ReCAP replaces or
   refines the remaining plan; and
5. an empty list completes its current node, while an empty root list stops the controller without
   asserting physical success.

The response schema, system prompt, context construction, tool derivation, recursion semantics,
model/capability/depth/invalid-output/history budgets, timeout policy, and public-observation
profiles are fixed before formal dispatch and identical across `M1`–`M7`. A provider transport may
perform only a mechanical adaptation needed to obtain the same schema-conforming response. It may
not add provider-specific planning logic, examples, retries, tools, feedback, or semantic repair.

### 2.2 Persistent physical session

Each episode creates one fresh credential-free candidate worker and one canonical MuJoCo
model/data session, applies the task reset once, and keeps that same worker, Driver instance, and
MuJoCo state across every ReCAP capability invocation. Restarting, reloading, or resetting between
leaves or scoring clauses invalidates the episode. A subsequent formal episode uses a fresh worker
and reset.

Model calls and credentials remain in the Framework parent. Capability execution crosses only the
fixed typed adapter and exact ABI
`driver.<capability_name>(request=<capability-native request>)`. The candidate worker never receives
credentials, model internals, Harness source, or a task-level macro request.

### 2.3 Independent verdict and video

The trusted task Harness observes the same persistent session through private measurement bindings
that are unavailable to ReCAP. It computes task clauses, physical integrity, and the terminal
episode verdict independently of controller output. Every formal episode retains one continuous
video covering reset, all capability calls, and terminal scoring, plus the structured controller
trace and Harness report. A missing or discontinuous required video makes that episode evidentially
incomplete and cannot be reported as a success.

**中文辅助说明。** 每个 episode 使用同一套固定有界 ReCAP：递归分解、每次只执行队首、根据有界公开
观测重新规划。一次 episode 只有一个新 worker 和一个 canonical MuJoCo session；所有 capability call 共享同一
Driver 和状态，中途不得 restart、reload 或 reset。模型仅在 Framework parent 内调用，worker 只通过精确 capability ABI
执行。可信 Harness 使用私有 binding 独立产生 clause、physical-integrity 和终态 verdict。每个正式 episode 必须保留
覆盖 reset、全部 call 和终态判定的连续视频。

## 3. Fixed robots, capability stacks, and task blocks

### 3.1 Robot configurations

B2 contains exactly two canonical package configurations:

| Robot block | Canonical configuration ID | Fixed public capability family |
|---|---|---|
| Serial manipulator | `robotstudio_so101` | A1–A5 from the fixed B1 SO-101 capability design |
| Quadruped | `unitree-go2-stock-12dof` | G1–G5 from the fixed B1 Go2 capability design |

For each robot, the B2 manifest must jointly pin one reviewed
`reference/fixed_capability_driver.py`, its exact capability-design and complete validation-suite
snapshot, the typed B2 capability adapter, the public-observation profile, and the successful full
video calibration record. That exact combination is identical for every backbone, task, and
replicate for the robot. It is not a factor and cannot be repaired or replaced during the formal
core.

### 3.2 Source-backed compositional tasks

The SO-101 task block contains exactly:

| Task ID | Public task identity |
|---|---|
| `mw_push_to_goal` | Push an object to a goal |
| `mw_sweep_into_goal` | Sweep an object into a goal |
| `mw_pick_place` | Pick and place an object |
| `mw_pick_place_wall` | Pick and place around a wall |
| `mw_bin_picking` | Pick an object from a bin |

The Go2 task block contains exactly:

| Task ID | Public task identity |
|---|---|
| `GO2-T02` | Single-step ascent |
| `GO2-T03` | Single-step descent |
| `GO2-T06` | Fixed mixed-obstacle path |
| `GO2-T16` | Barkour pause-table transition |
| `GO2-T17` | Barkour weave poles |

These ten IDs must resolve to the source-backed Task Library records in their canonical package
snapshots. Before formal dispatch, one sealed B2 task suite must pin each public task projection,
scene, private reset/seed, complete source-lineaged scoring clauses, measurement bindings, guards,
budgets, and rendering configuration. No task may be substituted because its oracle or controller
run is difficult.

### 3.3 Source and private-state isolation

ReCAP receives only the fixed public task projection, mechanically derived public capability names,
descriptions and request schemas, the initial allowlisted public state, and bounded public operation
observations. The controller and every provider request are denied the reference-driver source,
reference-only calibration traces, scripted oracle plan or output, private reset and seed,
criterion thresholds or expressions, measurement bindings, guards, hidden expected trajectory,
raw MuJoCo state, Harness source/configuration, and final verdict.

The reference source and logic also remain unavailable to B1 generation and Repair. B2 execution
must not place them in model-visible prompts, workspaces, files, tool descriptions, error messages,
or observations. The Framework and trusted Harness may load private material outside the
credential-free candidate worker as required to execute and score the episode.

**中文辅助说明。** B2 只包含 `robotstudio_so101` 和 `unitree-go2-stock-12dof`。SO-101 固定使用
A1–A5，Go2 固定使用 G1–G5。每台机器人的 reference driver、capability design、完整 validation suite、typed
adapter、公开观测 profile 和完整视频校准记录必须共同固定，并在所有 backbone、task 和 replicate 中一致。
上表十个 task ID 必须解析到 canonical package 中有来源的 Task Library 记录。Controller 仅可看到固定公开 task、
interface-derived tool 和有界公开反馈；不得看到 reference 源码、oracle、私有 reset/criterion/binding/guard、raw state、
Harness 或最终 verdict。

## 4. Backbone factor

The fixed factor has seven levels:

| ID | LLM family |
|---|---|
| `M1` | Sonnet 4.6 |
| `M2` | Opus 5 |
| `M3` | Haiku 4.5 |
| `M4` | Nova Pro |
| `M5` | DeepSeek V4 Pro |
| `M6` | Ministral 3 8B |
| `M7` | Qwen3 32B |

Before the first formal episode, the manifest must pin for every level its remotely hosted provider,
exact returned model identity and revision, endpoint/region, transport, inference settings,
context/output limits, strict per-call timeout, retry rule, and dated price snapshot. A level that
cannot be pinned or called remains an explicit infrastructure blocker; it is not silently replaced
by another model and does not reduce the denominator. Local weights or local inference cannot
substitute for a declared family.

No backbone receives additional examples, task-specific hints, different public state, different
semantic retry, or a different ReCAP budget. The provider adapter records request/response
timestamps, model identity, token use, cost inputs, structured output, transport error, and timeout
without exposing credentials to the worker.

**中文辅助说明。** 固定因素共七个 level，即表中 `M1`–`M7`。首个正式 episode 之前，manifest 必须
对每个 level 固定远程 provider、准确 model identity/revision、endpoint/region、transport、inference setting、context/output
limit、严格 timeout、retry 规则和价格快照。不可用其他模型或本地推理静默替代，也不可为某个 backbone 增加额外
示例、task hint、公开状态、semantic retry 或不同 ReCAP budget。

## 5. Replicate plan and formal denominator

The formal core crosses:

`2 robots × 5 tasks per robot × 7 backbones × 3 replicates = 210 episodes`.

Replicate IDs are `R1`, `R2`, and `R3`. Each robot–task–backbone combination receives exactly one
fresh episode at each replicate. The sealed task suite fixes the three reset seeds and their mapping
to replicate IDs before any formal episode. Scheduling may interleave cells for operational
reasons, but cannot change inputs, reuse MuJoCo state, condition retries on outcomes, or add an
episode to compensate for controller failure.

Reference calibration, Harness development runs, provider connectivity checks, scripted oracle
canaries, prompt dry runs, and any run made while Section 7 remains unsatisfied are diagnostic and
excluded from the 210. A formal episode is not rerun for controller or model failure. A confirmed
infrastructure failure is recorded separately and may be rerun once with the same frozen inputs;
both the original incident and replacement execution remain in the record. Planned, executed,
evaluable, and missing denominators must be reported separately.

An extension replicate or additional task/backbone is not automatic. It requires a prospective
authority revision made before inspecting the formal core outcomes and must add a complete balanced
block; otherwise the core denominator remains 210.

**中文辅助说明。** 正式 core 为 `2 × 5 × 7 × 3 = 210` 个 episode。`R1`、`R2`、`R3` 各自使用在首次正式执行
前封存的 reset seed，每个 episode 都是新 worker 和新 MuJoCo session。reference calibration、Harness 开发运行、provider
连通性检查、scripted oracle canary、prompt dry run 及 blocker 未解除时的运行都是诊断运行，不计入 210。Controller 或
模型失败不自动重跑；经确认的 infrastructure failure 只可在相同封存输入下重跑一次，并同时保留原事件与替代运行。

## 6. Analysis and required records

For each backbone, the primary summary is its Harness task-success proportion over the 30 planned
episodes (`2 robots × 5 tasks × 3 replicates`). Report the numerator, planned denominator,
evaluable denominator, and missing/infrastructure count. Also report robot-stratified and
task-stratified success, every task-clause and physical-integrity result, and the secondary resource
records from Section 1. With only three replicates per task block, claims and pairwise contrasts are
descriptive and bounded; no universal ranking or unplanned subgroup claim is permitted.

Every episode record must include: run and code version; authority and manifest revisions; robot
package, task, scene, reset and replicate IDs; fixed driver/interface/adapter/observation/Harness
identities; backbone and returned provider model identity; fixed prompt/schema/budgets; model-call
and capability-call trace; tokens, pricing inputs, cost, simulation and wall times; worker terminal
record; controller status; independent Harness clauses, physical-integrity result and task verdict;
video path and completeness; and any model, candidate, infrastructure, or evidence failure.

Raw provider messages, worker protocol evidence, Harness report, and media are retained in the run
bundle. A concise tracked index may point to large ignored media but cannot claim success when the
underlying report or required video is absent. The project does not add hashes, signatures,
attestations, registries, or a lifecycle state machine.

**中文辅助说明。** 每个 backbone 的主摘要是 30 个计划 episode 上的 Harness task-success 比例，必须同时报告
分子、计划 denominator、可判定 denominator 和 missing/infrastructure 数。另外报告 robot/task 分层、全部 clause、physical
integrity 和资源记录。三次 replicate 只支持有界描述性比较。每个 episode 都必须记录固定输入身份、完整
model/capability trace、独立 Harness verdict、物理完整性、资源使用和视频完整性。

## 7. Formal-execution blockers

Formal B2 execution is paused. No run may enter the 210-episode denominator until all four
prerequisites below are complete and fixed together in one versioned B2 manifest:

1. **Complete video reference calibration.** For both robots, the exact reviewed reference driver,
   fixed A1–A5 or G1–G5 interface, complete interface-bound capability-validation suite, typed
   adapter, canonical scenes, and public-observation profile are pinned. The complete suite passes
   through that adapter on the real Direct-MuJoCo path, with every required case video retained and
   physical integrity passing. Calibration remains diagnostic and is not B2 task-use evidence.
2. **Complete task Harness.** All ten selected tasks have sealed public projections, scenes,
   replicate resets/seeds, source-lineaged scoring clauses, private measurement bindings, guards,
   session budgets, independent terminal verdicts, and continuous-video capture. Focused
   false-success checks demonstrate that controller completion alone and a deliberately unmet task
   cannot pass.
3. **Complete provider pins.** `M1`–`M7` have the exact remote provider identities, endpoint and
   inference settings, limits, timeout/retry behavior, structured-output transport, and dated
   pricing required by Section 4. The fixed common ReCAP prompt, response schema, context rules,
   budgets, and model-client behavior are pinned at the same time.
4. **Scripted oracle canaries.** A task-specific scripted oracle, kept hidden from ReCAP, exercises
   the same fixed driver–interface–adapter and persistent-session path for every one of the ten task
   Harness entries. Every canary produces a physically successful independent Harness verdict and
   complete video. Oracle runs are diagnostic, excluded from 210, and never become model prompts,
   examples, tools, feedback, or retry input.

A prerequisite failure remains a visible construction blocker. It is not a failed backbone
episode, and the corresponding robot, task, or model may not be silently removed. Once all four
are satisfied, the manifest and a prospective authority revision must record their exact identities
before the first formal model request; only then may formal execution start.

**中文辅助说明。** B2 正式执行当前暂停。两台机器人的完整视频 reference calibration、十项 task 的完整独立
Harness、`M1`–`M7` 的准确 provider pin，以及十项 task 的 scripted oracle canary 全部通过并在同一份版本化
manifest 中固定之前，任何 run 都不得进入 210 个 episode 的正式 denominator。Oracle 和 calibration 仅是诊断证据，
必须对 ReCAP 隔离，不得进入 prompt、example、tool、feedback 或 retry input。

## 8. Acceptance and non-claims

B2 is complete only when all 210 planned episodes have either an evaluable retained record or an
explicitly reported unresolved infrastructure absence, and the analysis reports the fixed primary
and secondary summaries without changing the denominator. Completion does not mean every task
passed.

The resulting evidence supports only the declared high-level-controller-backbone comparison using
the fixed reference capability stacks on the ten selected Direct-MuJoCo tasks. It is not Driver
Synthesis evidence, reference-driver superiority evidence, an Experiment 2 outcome, an RQ2 result,
a ReCAP-versus-ReAct comparison, all-task or all-robot evidence, hardware evidence, or a real-SDK or
sim-to-real claim.

**中文辅助说明。** 只有当 210 个计划 episode 全部拥有可判定记录，或对仍未解决的 infrastructure 缺失作出
显式报告，且分析按固定 denominator 报告时，B2 才算执行完成；这不等于所有 task 都通过。证据仅支持该有界
high-level-controller-backbone 比较，不支持 Driver Synthesis、reference driver 更优、Experiment 2/RQ2、
ReCAP-versus-ReAct、全部 task/robot、硬件、真实 SDK 或 sim-to-real 主张。
