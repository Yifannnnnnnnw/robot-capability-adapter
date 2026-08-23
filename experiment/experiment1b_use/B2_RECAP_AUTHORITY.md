# Experiment 1b — B2 ReCAP Capability-Interface-Use Extension Authority

> **Document ID:** `AA2-B2`<br>
> **Document role:** sole normative document for Experiment 1b — B2<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.33`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.4`<br>
> **Effective date:** 2026-08-23<br>
> **Design status:** active and prospectively fixed; formal dispatch authorised but not started

Revision `0.1.4` completes the synchronized pre-formal B2 configuration and
authorises, but does not start, the fresh 210-episode core. The two robots,
five tasks per robot, active M1--M6 and M8, fixed ReCAP controller, and fresh
`R1`--`R3` episode design remain unchanged. The SO-101 `mw_pick_place` v6
public request is sealed at start `[0.34, 0.08, 0.18]`, grasp
`[0.34, 0.08, 0.195]`, and `grasp_gripper=0.26`; its target, release,
`shoulder_pan=-1.9` reset, `40 s / 10000` budget, `0.07 m` task threshold,
and `0.005 m` physical-integrity threshold remain unchanged.

The formal manifest mechanically combines two retained diagnostic evidence
sets without rewriting or rerunning them. The archived
`reference-calibration-a6-356c400-video` run at code revision
`356c4000f1956af90820d602b638328930457c39` supplies the complete A1--A6 and
G1--G5 typed-adapter reference calibration: SO-101 18/18 and Go2 15/15, with
all 33 required videos. The retained `pick-place-v6-oracle-full-video` report
supplies the v5-to-v6 task delta through the selected typed fixed-capability
Driver: Harness PASS, physical integrity PASS, terminal error within `0.07 m`,
penetration within `0.005 m`, and a complete video. These diagnostic records
remain outside the 210 denominator. The resolver validates their identities,
trials, current suite inputs, oracle trace, Harness facts, and media before it
may read credentials or create a model client; no new 33-case calibration or
pre-formal M5, M8, or other LLM canary is required.

M8 is pinned to returned identity `openai.gpt-5.6-sol`, the OpenAI public
1,050,000-token context and 128,000-token maximum output, and the dated
2026-08-23 public Standard price schedule. The short-context rates are USD 5
input, USD 0.50 cached input, and USD 30 output per million tokens; a request
with more than 272,000 input tokens uses USD 10, USD 1, and USD 45 respectively
for the full request. This is a public reference estimate, not a claim about
actual company-gateway billing; the unavailable upstream revision is recorded
as not independently verifiable. Enabling dispatch records readiness only and
does not launch an episode.

**中文辅助说明。** 修订 `0.1.4` 完成 B2 正式实验前同步，并授权但不启动全新的 210-episode
core。两台机器人、每台五项 task、active M1--M6 与 M8、固定 ReCAP controller 及全新
`R1`--`R3` 设计不变。SO-101 `mw_pick_place` v6 固定 start `[0.34, 0.08, 0.18]`、
grasp `[0.34, 0.08, 0.195]`、`grasp_gripper=0.26`、`shoulder_pan=-1.9` reset、
`40 s / 10000` budget、`0.07 m` task threshold 与 `0.005 m` 物理完整性阈值。
正式门禁不改写、不重跑已有证据，而是机械组合 archive 中
`reference-calibration-a6-356c400-video` 的 SO-101 18/18、Go2 15/15 和 33 个视频，
以及 `pick-place-v6-oracle-full-video` 的 typed fixed-capability Driver、Harness PASS、物理
完整性 PASS、误差/穿透过线和完整视频。两组诊断记录均不进入 210 denominator；resolver
必须在读取 credential 或创建 model client 前验证其身份、trial、当前 suite 参数、oracle trace、
Harness 事实与视频。不再运行 33-case calibration，也不增加 M5、M8 或其他正式实验前 LLM
canary。M8 使用 2026-08-23 OpenAI 公开 limit 与 Standard 价格作参考估算，不代表 company
gateway 实际账单，无法独立验证的 upstream revision 如实记录。开启 dispatch 只表示就绪，
不会自动启动 episode。

Revision `0.1.3` prospectively fixes Experiment 1b — B2 to the active
backbones `M1`–`M6` and `M8` across the same two robots and five tasks per
robot. `M7`/Qwen3 32B remains inactive historical configuration and its ID is
not reused; M8 must return exactly `openai.gpt-5.6-sol` through the verified
company gateway. The estimand is deployed model-plus-endpoint usability within
the fixed ReCAP, not intrinsic LLM-family ability. Each fresh `R1`–`R3`
episode uses a new worker and session but the exact same canonical
scene/reset/initial state within its robot–task block, so the replicates test
repeatability only and not initial-state robustness. The formal cohort is 210
exact fresh episodes, 30 per active backbone; old diagnostic and formal runs
remain historical and outside this denominator.

The SO-101 `mw_pick_place` v6 task is prospectively sealed with start
`[0.34, 0.08, 0.18]`, grasp `[0.34, 0.08, 0.195]`, target
`[0.38, -0.08, 0.26]`, release/tool `[0.371, -0.078, 0.272]`,
`shoulder_pan` reset `-1.9`, a `40 s / 10000` episode budget, and the
`≤0.07 m` threshold unchanged. Formal execution remains paused pending the
v6 task suite, complete video calibration, active provider pins, and formal
runner-plus-aggregator readiness. B2 completion means every planned episode
has an explicit trustworthy terminal record—an evaluable PASS/FAIL or a visible
non-evaluable infrastructure/evidence terminal—not that every episode
succeeds.

**中文辅助说明。** 修订 `0.1.3` 事先将 Experiment 1b — B2 固定为同样的两台机器人、每台五项
task，以及 active backbone `M1`–`M6` 与 `M8`。`M7`/Qwen3 32B 仅保留为 inactive 历史配置，不能复用其
ID；M8 必须经 verified company gateway 返回准确的 `openai.gpt-5.6-sol`。Estimand 是固定 ReCAP
内已部署 model+endpoint 的 usability，而非 LLM family 的 intrinsic ability。每个全新的 `R1`–`R3`
episode 使用新 worker 和新 session，但在同一 robot–task block 内严格使用相同 canonical
scene/reset/initial state；因此 replicate 只测试 repeatability，不测试 initial-state robustness。
正式 cohort 为准确的 210 个全新 episode，每个 active backbone 30 个；旧 diagnostic 和 formal run
均为历史记录，不进入本 denominator。SO-101 `mw_pick_place` v6 事先封存 start
`[0.34, 0.08, 0.18]`、grasp `[0.34, 0.08, 0.195]`、target `[0.38, -0.08, 0.26]`、
release/tool `[0.371, -0.078, 0.272]`、`shoulder_pan` reset `-1.9`、`40 s / 10000` budget，
以及不变的 `≤0.07 m` threshold。v6 task suite、完整视频 calibration、provider pin 和 formal
runner+aggregator readiness 完成前，正式执行保持暂停。B2 完成只要求每个 planned episode 有可信的
显式 terminal record：可评估 PASS/FAIL，或可见的 non-evaluable infrastructure/evidence terminal；
不要求每个 episode 都成功。

Revision `0.1.2` prospectively raises only the fixed simulation budget for the
three multi-stage SO-101 pick tasks `mw_pick_place`, `mw_pick_place_wall`, and
`mw_bin_picking` from `20.0 s / 5000 steps` to `40.0 s / 10000 steps`. In the
revision `0.1.1` real-model `mw_pick_place` diagnostic, the newly exposed A6
correctly reached the requested wrist roll twice and the controller detected a
failed first grasp, re-centred, restored wrist orientation, and began the
second closure; the worker then stopped at `20.005 s` before the second grasp,
test lift, transport, or release could complete. The retained failed run is not
relabelled. All task semantics, scoring clauses, resets, controller/model-call
budgets, capability interfaces, backbone levels, replicates, and the
210-episode denominator remain unchanged. The already completed interface
reference calibration remains applicable because this revision changes only
task-episode budgets. Formal execution remains paused under Section 7.

**中文辅助说明。** `0.1.2` 只把三项多阶段 SO-101 pick task 的仿真预算从
`20.0 s / 5000 steps` 提高到 `40.0 s / 10000 steps`。真实模型诊断中，A6 已两次正确
达到目标腕角，controller 也识别了首次抓取失败并开始第二次抓取，但 worker 在
`20.005 s` 被预算终止。旧失败记录不重标；其他任务、评分、controller budget、接口、因素、
replicate 和 210-episode denominator 均不改变。

Revision `0.1.1` prospectively fixes a bounded comparison of one ReCAP high-level-controller
architecture across seven replaceable LLM backbones. It fixes two robots, five source-backed
compositional tasks per robot, three core replicates, and 210 formal episodes. The same validated
reference driver, capability interface, typed adapter, controller logic, public-observation
projection, task Harness, and episode budgets are held fixed within each robot; only the controller
backbone varies. Diagnostic calibration and real-model canaries are not formal episodes. Revision
`0.1.1` removes scripted-oracle canaries from the formal-execution prerequisites and consumes the
new B1 SO-101 A6 wrist-roll leaf capability required by the fixed pick tasks. It does not change
the task block, backbone factor, replicate plan, or 210-episode denominator. Formal execution
remains paused under Section 7.

**中文辅助说明。** `0.1.1` 事先固定一项有界 ReCAP 高层控制比较：同一架构替换七个
LLM backbone，使用两台机器人、每台五项有来源的组合 task 和三次正式 replicate，共 210 个
episode。每台机器人的 reference driver、capability interface、typed adapter、controller 逻辑、公开观测投影、
task Harness 和 episode budget 在所有 backbone 间保持一致；只有 controller backbone 变化。诊断校准和
真实模型 canary 不计入正式 episode。`0.1.1` 删除 scripted-oracle canary 的正式执行前置门槛，并增加 pick task
所需、已纳入 B1 的 SO-101 A6 转腕 leaf capability；task、backbone、replicate 和 210-episode denominator 均不改变。
第 7 节的 blocker 解除前，正式执行暂停。

## 0. Authority, identity, and precedence

This is the only normative source for Experiment 1b — B2's exact controller architecture, robots,
tasks, active backbone factor, replicate plan, fixed inputs, analysis boundary, and execution
blockers. It is the bounded extension authority delegated by
`AUTOADAPTER_2_AUTHORITY.md` Section 0.1. The parent Authority
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

**中文辅助说明。** 本文件是 Experiment 1b — B2 架构、机器人、task、active backbone（`M1`–`M6`、`M8`）因素、replicate、
固定输入、分析边界和执行 blocker 的唯一规范来源；`M7`/Qwen3 是 inactive 历史配置且不能复用 ID。B2 只是扩展标识，
**不是 Experiment 2**；它不执行 TGCD 或
implementation-blind IVC，也不支持 Experiment 2 或 RQ2 主张。仅在 B2 内，ReCAP 取代父权威第 3.6 节的
通用 ReAct 路径。从属 manifest、代码、run record、分析和论文文本不得修改本设计。

## 1. Research object and claim boundary

B2 asks a narrow question: with the robot, source-backed task, fixed validated
driver–interface–adapter stack, ReCAP algorithm, prompt, public feedback, budgets, and Harness held
constant, how does the deployed model-plus-endpoint configuration affect task-level physical success
and resource use?

The sole manipulated factor is the deployed ReCAP model-plus-endpoint configuration (`M1`–`M6` and
`M8`). Robot, task, and replicate are fixed blocking dimensions, not additional experimental
factors. The estimand is deployed model-plus-endpoint usability within this fixed ReCAP, not
intrinsic LLM-family ability. Results may support only a bounded comparison on the ten selected
Direct-MuJoCo task configurations. They do not show that ReCAP is superior to ReAct or another
controller, that one model is universally superior, that the reference driver was synthesized, or
that untested tasks, robots, or hardware work.

The primary outcome is the trusted Harness's binary task-success verdict for one episode. Secondary
records are per-clause verdicts, physical-integrity verdict, controller terminal status, model and
capability call counts, invalid outputs, tokens, model cost, simulation time, and wall time. A
controller stop, empty root plan, or self-report is never task success.

**中文辅助说明。** B2 只问：在机器人、有来源 task、已验证 driver–interface–adapter、ReCAP
逻辑、prompt、公开反馈、budget 和 Harness 都固定时，已部署的 model+endpoint configuration 如何影响物理
task 成功和资源使用。唯一操作因素是 `M1`–`M6` 与 `M8` 的 deployed model+endpoint；robot、task 和
replicate 是固定 blocking dimension。Estimand 是固定 ReCAP 内的 deployed usability，而不是 LLM family
的 intrinsic ability。主 outcome 仅由可信 Harness 的 episode-level 二元 task verdict 给出。

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
profiles are fixed before formal dispatch and identical across active `M1`–`M6` and `M8`. A provider transport may
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
| Serial manipulator | `robotstudio_so101` | A1–A6 from the fixed B1 SO-101 capability design |
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
budgets, and rendering configuration. No task may be substituted because its controller
run is difficult.

The SO-101 `mw_pick_place` v6 task record is prospectively sealed with start
`[0.34, 0.08, 0.18]`, grasp `[0.34, 0.08, 0.195]`,
`grasp_gripper=0.26`, target
`[0.38, -0.08, 0.26]`, release/tool `[0.371, -0.078, 0.272]`, and
`shoulder_pan` reset `-1.9`. Its episode budget is `40 s / 10000` and its
`≤0.07 m` threshold is unchanged. Within each robot–task block, R1, R2, and
R3 use exactly the same sealed canonical scene, reset, and initial state, while
each episode still uses a new worker and MuJoCo session. This replicate plan
tests repeatability only; it is not an initial-state-robustness design.

### 3.3 Source and private-state isolation

ReCAP receives only the fixed public task projection, mechanically derived public capability names,
descriptions and request schemas, the initial allowlisted public state, and bounded public operation
observations. The controller and every provider request are denied the reference-driver source,
reference-only calibration plans and traces, private reset and seed,
criterion thresholds or expressions, measurement bindings, guards, hidden expected trajectory,
raw MuJoCo state, Harness source/configuration, and final verdict.

The reference source and logic also remain unavailable to B1 generation and Repair. B2 execution
must not place them in model-visible prompts, workspaces, files, tool descriptions, error messages,
or observations. The Framework and trusted Harness may load private material outside the
credential-free candidate worker as required to execute and score the episode.

**中文辅助说明。** B2 只包含 `robotstudio_so101` 和 `unitree-go2-stock-12dof`。SO-101 固定使用
B1 A1–A6（含 `set_wrist_roll`），Go2 固定使用 G1–G5。每台机器人的 reference driver、capability design、完整 validation suite、typed
adapter、公开观测 profile 和完整视频校准记录必须共同固定，并在所有 active backbone（`M1`–`M6`、`M8`）、task 和 replicate 中一致。
上表十个 task ID 必须解析到 canonical package 中有来源的 Task Library 记录。SO-101 `mw_pick_place` v6 固定
start `[0.34, 0.08, 0.18]`、grasp `[0.34, 0.08, 0.195]`、`grasp_gripper=0.26`、target `[0.38, -0.08, 0.26]`、
release/tool `[0.371, -0.078, 0.272]`、`shoulder_pan` reset `-1.9`、`40 s / 10000` budget，
以及不变的 `≤0.07 m` threshold。每个 robot–task block 的 R1–R3 使用相同的 canonical scene/reset/initial state，
但每个 episode 使用新 worker 和新 MuJoCo session，因此只测试 repeatability，不测试 initial-state robustness。
Controller 仅可看到固定公开 task、interface-derived tool 和有界公开反馈；不得看到 reference 源码、校准计划或轨迹、私有
reset/criterion/binding/guard、raw state、Harness 或最终 verdict。

## 4. Backbone factor

The active fixed factor has seven deployed model-plus-endpoint levels:

| ID | LLM family |
|---|---|
| `M1` | Sonnet 4.6 |
| `M2` | Opus 5 |
| `M3` | Haiku 4.5 |
| `M4` | Nova Pro |
| `M5` | DeepSeek V4 Pro |
| `M6` | Ministral 3 8B |
| `M8` | GPT-5.6 Sol |

`M7`/Qwen3 32B remains inactive historical configuration and its ID is not reused. M8 must return
exactly `openai.gpt-5.6-sol` through the verified company gateway. Before the first formal episode,
the manifest must pin for every active level its remotely hosted provider, exact returned model
identity and available revision or truthful revision status, endpoint/region, transport, inference settings, context/output limits, strict
per-call timeout, retry rule, and dated price snapshot. A level that cannot be pinned or called
remains an explicit infrastructure blocker; it is not silently replaced by another model and does
not reduce the denominator. Local weights or local inference cannot substitute for a declared family.

For M8, the provider source records the returned identity
`openai.gpt-5.6-sol` and an upstream revision status of
`not_independently_verifiable`. Its 2026-08-23 OpenAI public Standard price is
used as a reference estimate: USD 5/0.50/30 per million input/cached-input/output
tokens up to 272,000 input tokens, and USD 10/1/45 for the full request when
input exceeds 272,000 tokens. This does not represent independently verified
company-gateway billing.

No active backbone receives additional examples, task-specific hints, different public state,
different semantic retry, or a different ReCAP budget. The provider adapter records request/response
timestamps, model identity, token use, cost inputs, structured output, transport error, and timeout
without exposing credentials to the worker.

**中文辅助说明。** active 固定因素共七个 deployed model+endpoint level，即表中 `M1`–`M6` 与 `M8`。
`M7`/Qwen3 32B 仅保留为 inactive 历史配置，不能复用其 ID；M8 必须经 verified company gateway 返回准确的
`openai.gpt-5.6-sol`。首个正式 episode 之前，manifest 必须对每个 active level 固定远程 provider、准确 model
identity/revision、endpoint/region、transport、inference setting、context/output limit、严格 timeout、retry 规则和价格快照。
不可用其他模型或本地推理静默替代，也不可为某个 active backbone 增加额外示例、task hint、公开状态、semantic retry
或不同 ReCAP budget。

## 5. Replicate plan and formal denominator

The formal core contains exactly 210 fresh episodes and crosses:

`2 robots × 5 tasks per robot × 7 active model-plus-endpoints × 3 replicates = 210 episodes`.

Replicate IDs are `R1`, `R2`, and `R3`. Each robot–task–active-model-plus-endpoint combination
receives exactly one fresh episode at each replicate, so each active model has 30 planned episodes.
Within each robot–task block, the sealed task suite fixes one canonical scene, reset, and initial
state, and R1–R3 each use exactly that same state with a new worker and MuJoCo session. Scheduling
may interleave cells for operational reasons, but cannot change inputs, reuse MuJoCo state, condition
retries on outcomes, or add an episode to compensate for controller failure.

Reference calibration, Harness development runs, provider connectivity checks, real-model
canaries, prompt dry runs, and any run made while Section 7 remains unsatisfied are diagnostic and
excluded from the 210. Prior diagnostic and formal runs remain historical and outside this fresh
denominator; no old record is cherry-picked into it. A formal episode is not rerun for controller
or model failure. A confirmed
infrastructure failure is recorded separately and may be rerun once with the same frozen inputs;
both the original incident and replacement execution remain in the record. Planned, executed,
evaluable, and missing denominators must be reported separately.

An extension replicate or additional task/backbone is not automatic. It requires a prospective
authority revision made before inspecting the formal core outcomes and must add a complete balanced
block; otherwise the core denominator remains 210.

**中文辅助说明。** 正式 core 为准确的全新 `2 × 5 × 7 × 3 = 210` 个 episode，每个 active model 30 个。
每个 robot–task block 的 R1、R2、R3 使用完全相同的 canonical scene/reset/initial state，但每个 episode 都是新
worker 和新 MuJoCo session，因此只测试 repeatability，不测试 initial-state robustness。reference calibration、Harness 开发运行、
provider 连通性检查、真实模型 canary、prompt dry run 及 blocker 未解除时的运行都是诊断运行；旧 diagnostic 和 formal
run 均为历史记录，不计入本 210 denominator，也不得 cherry-pick。Controller 或模型失败不自动重跑；经确认的
infrastructure failure 只可在相同封存输入下重跑一次，并同时保留原事件与替代运行。

## 6. Analysis and required records

For each active deployed model-plus-endpoint, the primary summary is its Harness task-success
proportion over the 30 planned episodes (`2 robots × 5 tasks × 3 replicates`). Report the numerator,
planned denominator,
evaluable denominator, and missing/infrastructure count. Also report robot-stratified and
task-stratified success, every task-clause and physical-integrity result, and the secondary resource
records from Section 1. With only three replicates per task block, claims and pairwise contrasts are
descriptive and bounded; no universal ranking, intrinsic-family-ability claim, or unplanned subgroup
claim is permitted.

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

**中文辅助说明。** 每个 active deployed model+endpoint 的主摘要是 30 个计划 episode 上的 Harness task-success 比例，必须同时
报告分子、计划 denominator、可判定 denominator 和 missing/infrastructure 数。另外报告 robot/task 分层、全部 clause、physical
integrity 和资源记录。三次 replicate 只支持有界描述性比较，不能作 intrinsic family ability 或 universal ranking 主张。每个
episode 都必须记录固定输入身份、完整 model/capability trace、独立 Harness verdict、物理完整性、资源使用和视频完整性。

## 7. Formal-execution blockers

Revision `0.1.4` records that all four prerequisites below are complete and
fixed together in the versioned B2 manifest. Formal dispatch is authorised,
but no run enters the 210-episode denominator until it is deliberately launched
under that exact manifest:

1. **Complete video reference calibration.** For both robots, the exact reviewed reference driver,
   fixed SO-101 A1–A6 or Go2 G1–G5 interface, complete interface-bound capability-validation suite, typed
   adapter, calibration-case scenes, and public-observation profile are pinned. The retained
   `reference-calibration-a6-356c400-video` cohort passes through that adapter on the real
   Direct-MuJoCo path, with SO-101 18/18, Go2 15/15, every required case video retained, and
   physical integrity passing. Calibration remains diagnostic and is not B2 task-use evidence.
2. **Complete task Harness and v6 suite.** All ten selected tasks have sealed public projections,
   scenes, the exact same canonical scene/reset/initial state across R1–R3 within each robot–task
   block, source-lineaged scoring clauses, private measurement bindings, guards, session budgets,
   independent terminal verdicts, and continuous-video capture. The SO-101 `mw_pick_place` v6
   record is sealed with the fixed coordinates, `shoulder_pan` reset, `40 s / 10000` budget, and
   unchanged `≤0.07 m` threshold in Section 3.2. The retained
   `pick-place-v6-oracle-full-video` typed-Driver delta report and focused false-success checks
   demonstrate that controller completion alone and a deliberately unmet task cannot pass.
3. **Complete provider pins.** Active `M1`–`M6` and `M8` have the exact remote provider identities,
   endpoint and inference settings, limits, timeout/retry behavior, structured-output transport,
   and dated pricing required by Section 4. M8's returned identity is exactly
   `openai.gpt-5.6-sol` through the verified company gateway. The fixed common ReCAP prompt,
   response schema, context rules, budgets, and model-client behavior are pinned at the same time.
4. **Formal runner and aggregator readiness.** The formal runner creates a fresh worker and
   MuJoCo session for every episode, applies the sealed repeated state exactly, retains the
   continuous video and trusted terminal record, and the aggregator reports the 210 planned
   episodes and 30-episode per-active-model denominators without silently dropping or replacing
   infrastructure/evidence terminals.
A resolver failure remains a visible construction blocker. It is not a failed
backbone episode, and the corresponding robot, task, or model may not be
silently removed. The resolver must validate the paired retained evidence and
all current pins before credential loading or model-client creation. No new
reference calibration or pre-formal LLM canary is an additional prerequisite.

**中文辅助说明。** 修订 `0.1.4` 已在同一份版本化 manifest 中固定两台机器人的完整视频 reference
calibration、十项 task 与 SO-101 `mw_pick_place` v6 suite、active `M1`–`M6` 与 `M8` 的准确 provider pin，
以及 formal runner+aggregator readiness；formal dispatch 已获授权但尚未启动。v6 固定第 3.2 节坐标、
`grasp_gripper=0.26`、`shoulder_pan` reset、`40 s / 10000` budget 和不变的 `≤0.07 m` threshold；R1–R3 在每个
robot–task block 使用相同 canonical scene/reset/initial state，但每个 episode 使用新 worker/session。Aggregator 必须
保留 210 planned episode 与每个 active model 30 个 episode 的 denominator，并保留 non-evaluable infrastructure/evidence
terminal。Calibration 仅是诊断证据，必须对 ReCAP 隔离，不得进入 prompt、example、tool、feedback 或 retry input。
Resolver 必须在读取 credential 或创建 model client 前验证复用证据与当前 pin；不增加 reference rerun 或
正式实验前 LLM canary。

## 8. Acceptance and non-claims

B2 is complete only when every one of the 210 planned fresh episodes has an explicit trustworthy
terminal record: either an evaluable Harness PASS/FAIL or a visible non-evaluable
infrastructure/evidence terminal, and the analysis reports the fixed primary and secondary
summaries without changing the denominator. Prior diagnostic and formal runs remain historical and
outside this denominator. Completion does not mean every task passed.

The resulting evidence supports only the declared high-level-controller deployed
model-plus-endpoint usability comparison within fixed ReCAP, using the fixed reference capability
stacks on the ten selected Direct-MuJoCo tasks. It is not an intrinsic LLM-family-ability claim,
Driver Synthesis evidence, reference-driver superiority evidence, an Experiment 2 outcome, an RQ2
result, a ReCAP-versus-ReAct comparison, all-task or all-robot evidence, hardware evidence, or a
real-SDK or sim-to-real claim.

**中文辅助说明。** 只有当 210 个计划的全新 episode 每个都有可信的显式 terminal record（可评估的 Harness
PASS/FAIL，或可见的 non-evaluable infrastructure/evidence terminal），且分析按固定 denominator 报告时，B2 才算
执行完成；旧 diagnostic 和 formal run 不进入本 denominator。这不等于所有 task 都通过。证据仅支持固定 ReCAP 内
deployed model+endpoint usability 的有界 high-level-controller 比较，不支持 intrinsic LLM family ability、Driver Synthesis、
reference driver 更优、Experiment 2/RQ2、ReCAP-versus-ReAct、全部 task/robot、硬件、真实 SDK 或 sim-to-real 主张。
