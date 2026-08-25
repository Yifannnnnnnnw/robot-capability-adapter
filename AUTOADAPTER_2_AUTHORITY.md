# Auto-Adapter 2.0 Direct-MuJoCo Mainline Authority / Direct-MuJoCo 主线权威文档

> **Document ID / 文档编号：** `AA2-AUTH`<br>
> **Document role / 文档角色：** sole project-wide normative document; bounded experiment authorities require explicit delegation in Section 0.1 / 项目范围唯一规范性文档；限定实验权威必须由第 0.1 节明确委派<br>
> **Normative language / 规范语言：** English / 英文<br>
> **Chinese text / 中文文本：** auxiliary reading support only / 仅作辅助阅读<br>
> **Document revision / 文档版本：** `0.20.0`<br>
> **Effective date / 生效日期：** 2026-08-25<br>
> **Current direction / 当前方向：** Direct-MuJoCo is the default mainline; real-SDK and Translation work is an independent extension / Direct-MuJoCo 是默认主线；真实 SDK 与 Translation 工作是独立扩展线

Revision `0.20.0` clarifies the IVC authorship boundary. IVC authors each exact
task-neutral nominal and calibrated-boundary request and the complete private
suite; it does not select or copy a pre-authored case. Framework-private
instances supply only trusted physical execution context such as scene/reset,
measurement binding, guards and optional calibrated request domains or
anchors. The Harness passes the IVC-authored native request to a candidate and
keeps any task envelope or reference adapter private.

**中文辅助说明。** 修订 `0.20.0` 明确 IVC 的创作边界：IVC 自己写每个 nominal 与
calibrated-boundary case 的精确 task-neutral request 及完整私有 suite，不选择或复制预写 case。
Framework 私有 instance 只提供可信 scene/reset、measurement binding、guard，以及可选的已校准
request domain/anchor；Harness 对 candidate 只传 IVC 写出的 native request，task envelope 或
reference adapter 始终保持私有。

The same revision replaces the unintegrated `primitive-v1` draft with the one
canonical `capability-v2` mainline and restores the observable AutoAdapter 1.0
file-workspace semantics from commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586` inside the existing AutoAdapter 2.0
modules. It creates no `aa1_runtime` and no parallel protocol stack. The
canonical sequence is STUDY, TGCD, implementation-blind IVC, Generate/Repair,
trusted capability validation, ReCAP Task Demo, and (only when enabled) one
Evolution call. The model writes `study.json`, `capability_design.json`,
`capability_validation_suite.json`, and `driver.py`; Framework validation at a
normal phase end replaces all `submit_*` and `check_driver` tools. A valid
artifact written on the last turn is accepted. Only a source/import-valid,
sealed `driver.py` consumes one of at most three Harness attempts.

All model-authored file phases receive `read_file`, `write_file`, and
`execute_python`; skeleton inspection is additionally visible only to
skeleton-assisted Generate/Repair. STUDY has 16 turns for both conditions;
TGCD and IVC have six turns; skeleton Generate/Repair have 22/22 turns and
from-scratch Generate/Repair have 40/20 turns. There is no aggregate tool-call
limit. ReCAP receives only tools derived from capabilities whose nominal and
calibrated-boundary cases both passed, plus `finish`, and is bounded to 16
planning turns and 12 capability calls per task.

TGCD authors three to ten reusable single-effect capability contracts and a
support-only many-to-many `task_support` relation. Candidate requests are
closed and task-neutral: they contain no task ID, whole task, scene/reset,
private criterion, macro, or call plan. Every numeric bound or threshold has a
public-standard or retained real-calibration source. IVC sees the sealed
design, private instances/bindings/guards, and sanitised validation examples;
it never sees a candidate Driver, Repair history, or verdict. It compiles
exactly one nominal and one calibrated-boundary case per capability and must
pass both through the package-private reference Driver before sealing in a
formal run. A `formal=false` diagnostic may explicitly skip this positive
control and remains non-formal evidence.

Experience model output contains exactly `observation`, `lesson`,
`recommendation`, `scope`, and `public_evidence`; the Framework appends
`provenance` and `outcome`. Human disposition is accept or reject with a
nonempty reason and no content editing. An accepted snapshot is visible only
to a later run's STUDY, TGCD, Generate, and Repair, never IVC, Harness, or
ReCAP. This implementation batch prepares Experiment 1a and Experiment 3 and
runs only zero-model checks plus a non-formal DeepSeek canary. It does not run
formal Experiment 1a or Experiment 3 cells, does not rerun Experiment 1b, and
does not treat retained Experiment 2 files or tests as an acceptance gate.

**中文辅助说明。** 修订 `0.20.0` 用唯一的 `capability-v2` 主线取代未接通的
`primitive-v1` 草稿，并在现有 AutoAdapter 2.0 模块内恢复 AA1 的文件工作区语义；不建立
`aa1_runtime` 或平行协议栈。模型以文件交付四个 canonical artifact，Framework 在阶段正常结束时
审核，最后一回合写出的有效 artifact 仍可接受；只有通过源码与 import 边界并封存的 `driver.py`
才消耗最多三次 Harness attempt。预算分别为 STUDY 16、TGCD/IVC 各 6、skeleton
Generate/Repair 22/22、scratch Generate/Repair 40/20；ReCAP 每 task 为 16 turns/12 capability
calls。TGCD 生成 3--10 个 capability 和仅表示支持关系的 many-to-many `task_support`；request
不得包含 task dispatch、scene/reset、私有标准或 task macro。IVC 对实现不可见，每项 capability
恰好生成 nominal 与 calibrated-boundary case；正式 run 经私有 reference Driver 正控后封存，
`formal=false` diagnostic 可显式跳过且仍只属于非正式证据。Experience
只在后续 run 的 STUDY/TGCD/Generate/Repair 可见。本批仅准备 Exp1a/Exp3、运行零模型检查及
非正式 DeepSeek canary；不启动正式 cell、不重跑 Exp1b，也不把保留的 Exp2 文件或测试作为验收门禁。

All revision notes below this paragraph and before Section 0 are retained only
as historical change records. They are non-normative under revision `0.20.0`.

Revision `0.19.33` completes the prospective Chapter 3 pre-formal
configuration without starting either formal matrix. Experiment 1a/B1 remains
the fresh 84-cell two-robot driver-synthesis comparison, and Experiment 1b/B2
remains the fresh 210-episode two-robot capability-interface-use comparison;
both use active M1--M6 and M8 while M7 remains inactive history. M8 is pinned
to returned identity `openai.gpt-5.6-sol`, the OpenAI public 1,050,000-token
context and 128,000-token maximum output, and the dated 2026-08-23 public
Standard price schedule. That price is a reproducible public reference
estimate, not a claim about the organization gateway's actual invoice; the
upstream revision is recorded as not independently verifiable rather than
invented. The SO-101 `mw_pick_place` v6 public request fixes start
`[0.34, 0.08, 0.18]`, grasp `[0.34, 0.08, 0.195]`, and
`grasp_gripper=0.26`, while preserving its target, release, reset,
`40 s / 10000` budget, `0.07 m` task threshold, and `0.005 m` physical
integrity threshold.

The B2 readiness gate reuses, without rewriting or rerunning, the complete
33-case full-video reference calibration retained as
`reference-calibration-a6-356c400-video` together with the retained
`pick-place-v6-oracle-full-video` typed-Driver delta evidence. The formal
resolver must validate both evidence sets, current task inputs, and required
videos before credentials are read or a model client is created. No new M5,
M8, or other pre-formal LLM canary is required. The synchronized manifests may
therefore enable formal dispatch after their zero-model checks pass, but this
revision does not itself launch or create any formal B1 or B2 observation.

**中文辅助说明。** 修订 `0.19.33` 完成 Chapter 3 正式实验前配置，但不启动任何正式矩阵。
Experiment 1a/B1 仍为两台机器人、84 个全新 driver-synthesis cell；Experiment 1b/B2
仍为两台机器人、210 个全新 capability-interface-use episode。两者均使用 active M1--M6
与 M8，M7 继续作为 inactive 历史配置。M8 固定返回身份
`openai.gpt-5.6-sol`、OpenAI 公开的 1,050,000-token context、128,000-token 最大输出，
以及日期为 2026-08-23 的公开 Standard 价格；该价格只用于可复现的公开参考估算，不代表
organization gateway 的实际账单，无法独立验证的 upstream revision 保持如实记录。
SO-101 `mw_pick_place` v6 固定 start `[0.34, 0.08, 0.18]`、grasp
`[0.34, 0.08, 0.195]` 和 `grasp_gripper=0.26`，其 target、release、reset、
`40 s / 10000` budget、`0.07 m` task threshold 与 `0.005 m` 物理完整性阈值不变。
正式门禁直接机械复用已有 33-case 全视频 reference calibration 与 v6 typed-Driver oracle，
不改写历史记录、不重跑，也不增加 M5、M8 或其他 LLM canary。零模型检查通过后 manifest
可开启 formal dispatch，但本修订本身不启动正式 B1/B2，也不产生正式观测。

Revision `0.19.32` prospectively delegates two bounded downstream protocols.
Experiment 2 (`experiment/experiment2/EXPERIMENT_2_AUTHORITY.md`, `AA2-EXP2`)
is the SO-101 cross-run closure: a Sonnet 4.6 skeleton-assisted source run
with empty Experience through the Task Demo stage (an admitted-driver demo or
truthful `not-run` outcome), one terminal Opus 5 Evolution proposal, human
accept/reject disposition with a nonempty reason and without content editing, and one
manually launched independent Sonnet 4.6 Experience-enabled later run when
the proposal is accepted. It is mechanism evidence only and cannot support an
improvement or causal Experience claim. Experiment 3
(`experiment/experiment3/EXPERIMENT_3_AUTHORITY.md`, `AA2-EXP3`) is the exact
eleven-configuration Direct-MuJoCo cohort, Sonnet 4.6 only, skeleton-assisted
only, `r01`--`r03`, fresh end-to-end TGCD and IVC per cell, empty Experience,
at most three submitted drivers, Task Demo, then stop. Evolution is disabled;
the denominator is 33 cells and morphology is descriptive only. These
protocols supersede the former formal matched Experience-effect RQ3 design
and the former all-robot two-condition/Ministral/Evolution obligations.
RQ1/Experiment 1 remains governed by its existing delegated authority.

**中文辅助说明。** `0.19.32` 事先委派两个有界的后续 protocol。
Experiment 2（`experiment/experiment2/EXPERIMENT_2_AUTHORITY.md`，`AA2-EXP2`）是 SO-101
跨运行 closure：Sonnet 4.6 skeleton-assisted、空 Experience 的 source run 必须运行到
Task Demo stage（准入 driver 执行 demo，或真实 `not-run`），再由 Opus 5 做一次终态 Evolution proposal；人工只能
accept/reject 并提供非空 reason，不能编辑内容；
proposal 被接受后手动启动一次独立的 Sonnet 4.6 Experience-enabled later run。它只产生机制证据，
不能支持 improvement 或因果 Experience claim。Experiment 3
（`experiment/experiment3/EXPERIMENT_3_AUTHORITY.md`，`AA2-EXP3`）是精确十一配置
Direct-MuJoCo cohort，仅 Sonnet 4.6、仅 skeleton-assisted，`r01`--`r03`，每个 cell 新鲜执行
TGCD/IVC，空 Experience，最多三次 driver submission，运行 Task Demo 后停止；Evolution 禁用，
denominator 为 33，morphology 仅作描述。上述 protocol 取代旧的 matched Experience-effect RQ3
设计及旧的全机器人双条件/Ministral/Evolution 义务。RQ1/Experiment 1 仍由原有委派权威管理。

Revision `0.19.31` fixes Chapter 3 and RQ1 as two bounded subexperiments under the
project-wide construction cohort. Experiment 1a/B1 is the fixed-input driver-synthesis
comparison over exactly `robotstudio_so101` and `unitree-go2-stock-12dof`, active Producer
models M1--M6 and M8, skeleton-assisted and from-scratch conditions, and `r01`--`r03`:
`2 × 7 × 2 × 3 = 84` cells with at most 252 submitted drivers. A prospective complete `r04`
or `r05` block adds 28 cells and at most 84 submissions; through `r05` the cumulative maximum
is 140 cells and 420 submissions. Experiment 1b/B2 is the fixed ReCAP capability-interface-use
comparison over the same two robots, five tasks per robot, the same seven active models, and
fresh `R1`--`R3` episodes at each robot-task canonical reset: `2 × 5 × 7 × 3 = 210` episodes.
M8 is pinned to the verified returned identity `openai.gpt-5.6-sol`; M7/Qwen3 32B remains
inactive historical configuration. B2 is a Chapter 3/RQ1 subexperiment and is not Experiment 2
or RQ2 evidence. The eleven-configuration construction and initial shakedown cohort in
Section 1.3 remains unchanged and is separate from both Chapter 3 denominators. Formal B1 and
B2 execution remains paused under their delegated authorities until each authority's stated
readiness prerequisites are synchronized.

**中文辅助说明。** `0.19.31` 将 Chapter 3 和 RQ1 固定为全项目建设 cohort 下的两个有界子实验。
Experiment 1a/B1 只使用 `robotstudio_so101` 与 `unitree-go2-stock-12dof`，active Producer 为
M1--M6 与 M8，包含 skeleton-assisted 和 from-scratch 两种条件以及 `r01`--`r03`：
`2 × 7 × 2 × 3 = 84` 个 cell，最多提交 252 个 driver。事先声明并完整执行的 `r04` 或 `r05`
各增加 28 个 cell、最多 84 次提交；到 `r05` 的累计上限是 140 个 cell、420 次提交。
Experiment 1b/B2 使用相同两台机器人、每台五项 task、相同七个 active model，并在每个
robot-task 的 canonical reset 上为 `R1`--`R3` 建立 fresh episode：`2 × 5 × 7 × 3 = 210` 个
episode。M8 固定为已验证的返回身份 `openai.gpt-5.6-sol`；M7/Qwen3 32B 保留为 inactive
历史配置。B2 是 Chapter 3/RQ1 子实验，不是 Experiment 2，也不产生 RQ2 证据。第 1.3 节的
十一配置建设与首轮 shakedown cohort 保持不变，并与两个 Chapter 3 denominator 分开。B1、B2
正式执行在各自委派权威的 readiness 前置条件同步完成前保持暂停。

Revision `0.19.30` delegates `experiment/experiment1b_use/B2_RECAP_AUTHORITY.md` (`AA2-B2`)
as the sole bounded authority for the B2 ReCAP capability-interface-use extension. B2 is an
extension identifier, not Experiment 2, and creates no Experiment 2 or RQ2 evidence. It fixes a
two-robot, seven-backbone, five-task-per-robot, three-replicate core of 210 episodes in which the
only experimental factor is the high-level-controller backbone. Only within this extension, the
fixed high-level controller is ReCAP rather than the general post-admission ReAct path described
in Section 3.6. Every backbone must use the same fixed, validated reference
driver–capability-interface–adapter combination for each robot, with the reference source hidden
from the controller. Formal execution is paused until `AA2-B2`'s complete video reference
calibration, task-Harness, provider-pin, and scripted-oracle-canary prerequisites are satisfied.

**中文辅助说明。** `0.19.30` 将
`experiment/experiment1b_use/B2_RECAP_AUTHORITY.md`（`AA2-B2`）委派为 B2 ReCAP
capability-interface-use 扩展的唯一限定权威。B2 是扩展标识，不是 Experiment 2，
也不产生 Experiment 2 或 RQ2 证据。它固定两台机器人、七个 backbone、每台机器人
五项 task 和三次 replicate，共 210 个 episode；唯一实验因素是 high-level-controller
backbone。仅在该扩展内，固定 high-level controller 为 ReCAP，而非第 3.6 节的通用
ReAct 路径。每台机器人的全部 backbone 必须使用同一套已验证的固定 reference
driver–capability-interface–adapter 组合，且 controller 不得读取 reference 源码。在
`AA2-B2` 要求的完整视频 reference calibration、task Harness、provider pin 和 scripted
oracle canary 全部完成前，正式执行保持暂停。

Revision `0.19.29` permits the capability-neutral serial-arm trusted skeleton to provide bounded,
actuator-only, closed-loop Cartesian path-tracking primitives in addition to point-target DLS. Such
a primitive may interpolate caller-supplied finite segments or polylines, apply bounded Cartesian
reference progression, and correct cross-track error from fresh canonical MuJoCo state. It must not
embed a task identifier, capability dispatch, a fixed task trajectory or target, private criterion,
scene construction, reset, or reference-driver logic. The model-authored Driver remains responsible
for translating the public request into path geometry, timing, controller parameters, and capability
composition. This assistance remains exclusive to the skeleton-assisted condition; from-scratch
must implement the same behavior from the permitted primitives. Existing retained outcomes are not
retroactively relabelled, and a changed supplied skeleton after outcomes have been inspected creates
a new Experiment 1 configuration under `AA2-EXP1`.

**中文辅助说明。** `0.19.29` 允许 capability-neutral 串联机械臂 trusted skeleton 在单目标
DLS 之外提供有界、仅经 actuator 的闭环 Cartesian 路径跟踪 primitive。该 primitive 可对调用方
传入的有限 segment 或 polyline 进行插值，使用有界 Cartesian reference progression，并根据
最新 canonical MuJoCo state 修正 cross-track error。它不得内置 task id、capability dispatch、固定
任务轨迹或目标、私有 criterion、scene/reset 或 reference-driver 逻辑。模型创作的 Driver 仍负责
将公开 request 转换为路径几何、时间、控制参数和 capability 组合。该辅助仍仅属于
skeleton-assisted 条件；from-scratch 必须用允许的 primitive 自行实现同等行为。已保留的
结果不得追溯重标；在结果已被检查后修改输入的 skeleton，将按 `AA2-EXP1` 构成新的
Experiment 1 配置。

Revision `0.19.28` aligns the three research questions with the approved thesis experiment
structure. RQ1 and Experiment 1 are the delegated fixed-input B1 comparison of robot-specific
driver synthesis. RQ2 and Experiment 2 cover only Task-Grounded Capability Design and
source-grounded capability-level pass-criteria design with implementation-blind IVC audit and
compilation. Continued Evolution becomes RQ3 and Experiment 3 and may support an improvement claim
only through a prospectively matched later-run Experience/no-Experience comparison. The former
formal cross-morphology Experiment 3 is removed; morphology remains a public robot-package fact and
may be used to describe the construction cohort, but it is not a current research
question or experiment-level effect claim. Capability-interface use remains a distinct concept and
may be studied only through a separately declared extension; it is not part of the current
delegated Experiment 1. Experiments 2 and 3 still require prospective scoped authorities before
formal execution.

**中文辅助说明。** `0.19.28` 使三个研究问题与已批准的论文实验结构一致。RQ1 与 Experiment 1
是已委派的固定输入 B1 robot-specific driver synthesis 比较。RQ2 与 Experiment 2 只包含
Task-Grounded Capability Design，以及有来源依据的 capability-level pass-criteria 设计与实现
不可见 IVC 审计和编译。Continued Evolution 改为 RQ3 与 Experiment 3；只有事先声明并匹配的后续
Experience/no-Experience 比较才能支持改善主张。原正式 cross-morphology Experiment 3 被删除；
morphology 仍是公开 robot package fact，也可用于描述 construction cohort，但不再是当前
研究问题或实验级 effect claim。Capability-interface use 仍是独立概念，只能由另行声明的扩展
研究，不属于当前已委派的 Experiment 1。Experiment 2 和 3 在正式执行前仍须拥有前瞻性限定权威。

Revision `0.19.27` delegates Experiment 1's sole start prerequisite to `AA2-EXP1`: its five fixed
Driver contracts and Harness-evaluable criteria must be clear, and its isolated recorded
STUDY-to-generation/Repair/validation route must be usable. Experiment 1 does not require a
task-blind reference driver or reference calibration. Optional reference runs remain diagnostic
only. Exact provider settings are fixed before dispatching the affected backbone's cells; one
unready provider does not prevent unrelated ready cells from starting.

**中文辅助说明。** `0.19.27` 将 Experiment 1 的唯一开跑前置条件交由 `AA2-EXP1`：五台机器人
固定的 Driver contract 与 Harness 可判定 criteria 必须清晰，且隔离、带完整记录的
STUDY-to-generation/Repair/validation 路线必须可用。Experiment 1 不要求 task-blind reference
driver 或 reference calibration；可选 reference run 仅作诊断。准确 provider 设置在派发该 backbone
cell 前固定；某一 provider 未就绪不阻止无关的 ready cell 开始。

Revision `0.19.26` separates reusable benchmark definitions from concrete experiment ownership.
`AutoAdapter-Bench/` owns reusable protocols, registries, catalogues, and manifest-resolution
contracts; it does not own a current experiment cohort, replicate count, run directory, or analysis
denominator. The sole delegated authority for the fixed-input B1 backbone comparison called
Experiment 1 is `experiment/experiment1a_generation/EXPERIMENT_1_AUTHORITY.md`. The eleven-configuration cohort
in Section 1.3 remains the required all-robot mainline construction and shakedown cohort, but it is
not the automatic denominator for every downstream experiment.

**中文辅助说明。** `0.19.26` 将可复用 benchmark 定义与具体实验归属分开。
`AutoAdapter-Bench/` 只拥有可复用 protocol、registry、catalogue 与 manifest 解析合同，不拥有当前
实验 cohort、replicate 数、run 目录或分析 denominator。固定输入 B1 backbone 比较（Experiment 1）
唯一被委派的实验权威是 `experiment/experiment1a_generation/EXPERIMENT_1_AUTHORITY.md`。第 1.3 节的十一配置
仍是全机器人主线建设与 shakedown 的必需 cohort，但不再自动成为每个后续实验的 denominator。

Revision `0.19.25` reconciles the all-robot construction work with the fixed-input Chapter 3
protocol. The declared mainline cohort is the exact eleven configurations in Section 1.3;
`boston_dynamics_spot_with_arm`, `google_barkour_vb`, and `unitree_g1` remain optional research
backups. The initial two-condition shakedown therefore contains twenty-two cells per replicate and
uses the manifest-pinned Ministral 8B configuration with empty Experience input, terminal
Evolution, and post-round Experience disposition.

Each indexed package is eligible for this shakedown only when its complete canonical package and
source-backed Task Library load, physical task geometry uses robot-compatible collision layers,
every private reset avoids contact deeper than 5 mm, applicable position-held environments remain
within that bound during a focused 100-step settling check, and the shared Harness reports task
metric success separately from physical integrity. Any observed penetration deeper than 5 mm fails
physical integrity and the trial even when the numerical task metric passes. Hidden handwritten
reference drivers remain diagnostic calibration material; planner failure alone is not a package
admission blocker once these simulator, interface, metric, and Harness checks pass. This shakedown
does not by itself become a formal B1 result: Chapter 3 B1 continues to consume the prior-designed,
reference-calibrated fixed interface and validation bundle required by Revision `0.19.22`.

**中文辅助说明。** `0.19.25` 合并全机器人建设结果与 Chapter 3 固定输入实验协议。主线 cohort
是第 1.3 节列出的十一台精确配置；`boston_dynamics_spot_with_arm`、`google_barkour_vb` 和
`unitree_g1` 仅作为可选研究备份。首轮两条件 shakedown 因而包含二十二个 cell，统一使用 manifest
固定的 Ministral 8B、空 Experience 输入、终态 Evolution 和整轮后的 Experience disposition。

进入该 shakedown 的 package 必须完整加载 canonical package 与有来源 Task Library，使用真实可碰撞
task geometry，所有 private reset 的穿透不得深于 5 mm，适用的 position-held 环境还必须通过聚焦
100-step 沉降检查；共享 Harness 必须分别报告 task metric 与 physical integrity。任何深于 5 mm 的
穿透都会使 physical integrity 和 trial 失败，即使数值 metric 已通过。手写 reference driver 只保留
为诊断校准材料；上述仿真器、interface、metric 与 Harness 检查通过后，reference planner 自身失败
不再阻塞 package 准入。该 shakedown 本身不自动构成正式 B1 结果；Chapter 3 B1 仍按 `0.19.22`
消费预先设计并经 reference calibration 的固定 interface 与 validation bundle。

Revision `0.19.22` separates the upstream capability-design experiment from the Chapter 3 B1
backbone comparison. B1 consumes one versioned, reference-calibrated capability interface,
capability-level pass standard, and complete private validation suite per robot. That fixed bundle
is identical across every B1 backbone, replicate, and generation condition. B1 begins at STUDY and
contains no TGCD or IVC model call; its dynamic path is STUDY, GENERATE/GEN_ALGO, private
validation, bounded Repair, and final validation. TGCD and IVC remain framework components and
separate RQ2 objects, but their outputs must be produced, audited, calibrated, and frozen before B1.

**中文辅助说明。** `0.19.22` 将上游 capability-design 实验与 Chapter 3 B1 backbone 比较分开。
B1 对每台机器人只消费一套已版本化并通过 reference calibration 的 capability interface、
capability-level pass standard 和完整私有 validation suite；该固定 bundle 在全部 B1 backbone、
replicate 和生成条件间完全相同。B1 从 STUDY 开始，不包含 TGCD 或 IVC 模型调用；动态链路为
STUDY、GENERATE/GEN_ALGO、私有 validation、有界 Repair 和最终 validation。TGCD 与 IVC 仍是
framework component 和独立 RQ2 研究对象，但其产物必须在 B1 前完成生成、审计、校准和封存。

Revision `0.19.21` permits a reviewed package reference driver to be preselected as the fixed
benchmark driver for a separately declared B2 capability-interface-use comparison. Before formal
B2 execution, the exact reference driver, frozen capability interface, and audited adapter must be
fixed together, and that combination must pass the complete interface-bound capability validation
suite. The reference source remains unavailable to the compared controller backbones and to every
B1 generation condition. Reference-driver selection does not constitute B1 synthesis evidence, and
reference calibration without the declared high-level controller does not constitute B2 use
evidence.

**中文辅助说明。** `0.19.21` 允许在单独声明的 B2 capability-interface-use 比较中，预先选择
经审查的 package reference driver 作为固定 benchmark driver。正式执行 B2 前，必须共同固定准确的
reference driver、封存的 capability interface 与已审计 adapter，并让该组合通过完整的 interface-bound
capability validation suite。参与比较的 controller backbone 和全部 B1 生成条件仍不得读取 reference
源码。选择 reference driver 不构成 B1 synthesis 证据；未包含已声明 high-level controller 的 reference
校准也不构成 B2 use 证据。

Revision `0.19.20` updates three planned RQ1 Producer model families to their current declared
versions: Opus 4.8 becomes Opus 5, DeepSeek V3.2 becomes DeepSeek V4 Pro, and Ministral 8B becomes
Ministral 3 8B. The seven-family scope and all experimental factors remain unchanged; exact
provider identifiers and inference settings are still frozen only by the versioned experiment
manifest.

**中文辅助说明。** `0.19.20` 将三个规划中的 RQ1 Producer model family 更新为当前声明版本：
Opus 4.8 更新为 Opus 5，DeepSeek V3.2 更新为 DeepSeek V4 Pro，Ministral 8B 更新为
Ministral 3 8B。七模型范围和全部实验因素保持不变；准确 provider identifier 与 inference
setting 仍仅由版本化 experiment manifest 固定。

Revision `0.19.19` restores the seven-family planned RQ1 Producer-backbone coverage that was
declared before the Direct-MuJoCo Authority rewrite, while leaving exact provider identifiers,
endpoints, inference settings, and formal price snapshots to the versioned experiment manifest.
It also makes model calls, input/output tokens, model cost, and wall time primary B1 resource
outcomes alongside driver-validation outcomes. This is a protocol-accounting clarification; it
does not change the two generation conditions, attempt budget, robot inputs, or Harness authority.

**中文辅助说明。** `0.19.19` 恢复 Direct-MuJoCo Authority 重写前已声明的七个 RQ1 Producer
backbone family；准确 provider identifier、endpoint、inference 配置和正式价格快照仍由版本化
experiment manifest 固定。本修订还将模型调用次数、输入/输出 token、模型费用和 wall time 与
driver validation 结果一同列为 B1 主要资源结果。这只是 protocol accounting 澄清，不改变两种
生成条件、attempt 预算、机器人输入或 Harness 判定权。

Revision `0.19.18` declares the fixed bounded ReAct high-level-controller path for post-admission
Task Demo capability-interface use. Model calls remain in the Framework parent while capability
invocations execute in one persistent credential-free candidate worker and canonical MuJoCo
session per trial. Controller completion remains separate from the trusted Harness verdict.

**中文辅助说明。** `0.19.18` 明确将固定、有界的 ReAct high-level controller 用于准入后的
Task Demo capability-interface use。模型调用保留在 Framework 父进程；每个 trial 的 capability
调用则在同一个无 credential 的持久 candidate worker 和 canonical MuJoCo session 中执行。
Controller 完成状态仍与可信 Harness verdict 严格分离。

Revision `0.19.17` replaces **robot capability layer** with **robot capability interface** and
**capability-layer use** with **capability-interface use** across the normative research
terminology. The new name identifies the reusable caller-facing contract boundary without implying
a standard layered robotics architecture. This terminology change does not alter the capability
contracts, the implementation obligations of the robot-specific driver, experiment conditions,
validation contract, code or artefact identifiers, or evidence boundaries.

**中文辅助说明。** `0.19.17` 在规范研究术语中以 **robot capability interface** 替代
**robot capability layer**，并以 **capability-interface use** 替代 **capability-layer use**。
新名称明确表示面向调用方的可复用合同边界，不暗示行业中存在统一的分层机器人架构。本次术语
调整不改变 capability contracts、robot-specific driver 的实现责任、实验条件、validation 合同、
代码或产物标识以及证据边界。

Revision `0.19.16` designated `autoadapter/` as the sole canonical Direct-MuJoCo mainline path.
At that revision, `demo3/` was only the temporary migration source, while the SDK-grounded material
in the former `general_demo/` belongs under `extensions/sdk/`. It also separates future robot
research, runnable robot admission, and per-run selection into `research/robots/index.json`,
`libraries/robots/index.json`, and `configs/experiments/*.json`, respectively. This is a repository
and ownership clarification; it did not alter the research questions, experiment conditions,
then-declared robot scope, validation contract, or any existing evidence classification.

**中文辅助说明。** `0.19.16` 当时将 `autoadapter/` 指定为唯一 canonical Direct-MuJoCo 主线路径。
在该修订时，`demo3/` 仅是迁移前的临时源码目录，原 `general_demo/` 中基于 SDK 的材料归入
`extensions/sdk/`。本修订还把未来机器人研究、可运行机器人准入和单次运行选择分别放在
`research/robots/index.json`、`libraries/robots/index.json` 和 `configs/experiments/*.json`。
这只是仓库路径与所有权澄清，当时不改变研究问题、实验条件、当时声明的机器人范围、validation 合同或任何
已有证据分类。

Revision `0.19.15` aligns the STUDY submission schema with its condition-specific handler after a
real skeleton-assisted run completed a successful recovery probe but omitted `skeleton_inspection`.
That field is now schema-required only for skeleton-assisted STUDY. The STUDY probe contract is also
limited to canonical scene loading, public-name inspection, and real `mj_step` liveness; skeleton
imports and host-search utilities are unnecessary in this probe because skeleton source is already
present in the inline public context.

**中文辅助说明。** `0.19.15` 根据一次真实 skeleton-assisted run 修正 STUDY submission schema
与条件化 handler 的错位：该 run 已完成成功的恢复 probe，但遗漏了 `skeleton_inspection`。现在该
字段仅在 skeleton-assisted STUDY 中由 schema 明确设为必需。STUDY probe 也收紧为 canonical
scene 加载、公开名称检查和真实 `mj_step` liveness；skeleton 源码已在内联公开上下文中，因此该
probe 不需要导入 skeleton 或使用 host 搜索工具。

Revision `0.19.14` removes an observed STUDY interface contradiction. Candidate import restrictions
and probe-only utility imports are now labelled separately; every model-authored probe receives the
single canonical scene loader through `AUTOADAPTER_PROBE_SCENE`, with relative, searched, or
synthetic fallback scenes forbidden. The `submit_study` boundary also parses a JSON-encoded array or
object when the OpenAI-compatible transport stringifies an otherwise schema-valid nested container.
It does not synthesize missing content or relax the successful real-physics probe requirement.

**中文辅助说明。** `0.19.14` 消除了真实 STUDY 暴露的一处接口矛盾：candidate import 限制和
probe 专用 utility import 现在分别标注；每个模型编写的 probe 都必须通过
`AUTOADAPTER_PROBE_SCENE` 使用唯一 canonical scene，禁止相对路径、搜索路径或合成 fallback
scene。当 OpenAI-compatible transport 把原本符合 schema 的嵌套数组或对象字符串化时，
`submit_study` 边界会用 JSON parser 恢复其容器；它不会补写缺失内容，也不会放宽真实物理 probe
成功这一要求。

Revision `0.19.13` restores the AutoAdapter 1.0 runtime feedback-loop contract in GENERATE and
Repair: every dynamic capability must make bounded state-dependent corrections from fresh canonical
MuJoCo observations, directly or through an allowed trusted-skeleton primitive. Repair additionally
receives a deterministic failure-focus index containing failed-trial requests, measurements, guards,
contact summaries, and initial/final public state. This index does not replace, redact, or weaken the
complete candidate-facing report, which remains available in the same Repair context.

**中文辅助说明。** `0.19.13` 在 GENERATE 和 Repair 中恢复 AutoAdapter 1.0 运行时反馈闭环契约：
每个动态 capability 必须直接或通过允许的可信 skeleton primitive，根据最新 canonical MuJoCo
观测执行有界、依赖状态的修正。Repair 还会收到一份确定性的失败焦点索引，其中包含失败 trial 的
实际请求、测量值、guard、接触摘要和公开初末状态。该索引不会替代、删减或弱化同一 Repair
上下文中继续完整提供的 candidate-facing report。

Revision `0.19.12` restores the AutoAdapter 1.0 Implementation Bundle rule that every runtime input
has complete public semantics. A private instance may supply only task parameters declared required
by its public invocation schema. TGCD mechanically carries each source-schema parameter description
into the Capability Design interface, so entity targets, end-effector waypoints, routes, grasp
controls, and release points cannot collapse into type-only vectors. This exposes no private value,
scene, criterion, binding, guard, trajectory, or reference implementation.

**中文辅助说明。** `0.19.12` 恢复 AutoAdapter 1.0 Implementation Bundle 的规则：每个运行时
输入都必须具有完整公开语义。私有 instance 只能提供公开 invocation schema 中声明为 required 的
task parameter；TGCD 将来源 schema 的参数描述机械带入 Capability Design interface，使 entity
target、末端 waypoint、route、grasp control 与 release point 不再退化为只有类型的向量。该改动
不会暴露任何私有值、scene、criterion、binding、guard、trajectory 或 reference 实现。

Revision `0.19.11` makes Driver Synthesis tools state-aware after the bundled public check. Once the
current revision passes source audit, import, and every capability physics smoke, the next model
request exposes only `submit_driver`; public browsing, probes, and `check_driver` are unavailable
until a changed revision is actually needed. The submit-only state permits at most two model turns,
providing one correction for a rejected submission payload while preventing the twelve-turn driver
budget from becoming repeated full-source generation. Explicit model submission remains mandatory.

**中文辅助说明。** `0.19.11` 让 Driver Synthesis 在 bundled public check 后按状态收敛工具面。
当前 revision 通过 source audit、import 和全部 capability physics smoke 后，下一模型请求只暴露
`submit_driver`；公开浏览、probe 和 `check_driver` 不再可用。submit-only 状态最多两个模型回合，
允许一次被拒提交参数的纠正，但不会让十二回合 driver 预算退化为重复生成完整源码；模型显式提交
仍然是强制要求。

Revision `0.19.10` gives each remote model request a strict total wall-clock deadline rather than
relying only on a socket-inactivity timeout. The default is 180 seconds, with an explicit bounded
environment override from 30 through 600 seconds. Expiry fails that model-authored stage without an
automatic retry. This restores the bounded-call intent of AutoAdapter 1.0 while allowing for
Demo3's larger public context and driver output; it does not convert transport failure into a
candidate or Harness verdict.

**中文辅助说明。** `0.19.10` 为每个远程模型请求增加严格的整次 wall-clock deadline，而不是
只依赖 socket inactivity timeout。默认 180 秒，可通过环境变量在 30 至 600 秒内显式调整；到期后
该模型创作阶段失败且不自动重试。它恢复 AutoAdapter 1.0 的有界调用意图，同时为 Demo3 更大的公开
上下文与 driver 输出留出时间；transport failure 不会被转换成 candidate 或 Harness verdict。

Revision `0.19.9` restores one bounded AutoAdapter 1.0-style STUDY recovery opportunity after the
dynamic run showed that a hard two-turn limit converts an ordinary failed probe or rejected submit
payload into a terminal cell failure. The normal path remains one public physics probe followed by
submission in two model turns. STUDY may use at most three turns and three tool calls: at most two
public probes, where the second is permitted only after the first fails, followed by a reserved
submission turn. A successful first probe still forbids another probe. No public input or private
boundary changes.

**中文辅助说明。** `0.19.9` 恢复一次有界的 AutoAdapter 1.0 式 STUDY 纠错机会。真实动态运行
证明，硬性两回合会把普通 probe 失败或提交参数被拒直接变成整个 cell 的终止失败。正常路径仍是
一次公开物理 probe 后提交，共两个模型回合；STUDY 最多三个回合、三个工具调用，最多两个公开
probe，且只有首次失败时才允许第二次，随后保留最终提交回合。首次 probe 成功后仍禁止重复 probe；
公开输入和私有边界均不改变。

Revision `0.19.8` strengthens only the capability-neutral serial-arm skeleton primitive surface
observed to be missing in the dynamic run. It provides actuator-only closed-loop Cartesian DLS with
optional wrist-roll pinning, arbitrary finite gripper targets, and a physics hold operation. It
contained no task identifier, dispatch, trajectory, target, scene construction, reset, criterion,
or reference-driver logic; the model remained the sole author of capability composition and policy.
Revision `0.19.29` supersedes only that historical trajectory exclusion as specified above.

**中文辅助说明。** `0.19.8` 仅补齐真实动态运行中确认缺失的 capability-neutral 串联机械臂
skeleton primitive：actuator-only 闭环 Cartesian DLS、可选 wrist-roll 固定、任意有限夹爪目标和
physics hold。该版本当时不包含 task id、dispatch、trajectory、target、scene/reset、criterion 或
reference driver 逻辑；capability 组合与策略当时仍完全由模型创作。`0.19.29` 只按上述
新条款取代这一历史 trajectory 禁止。

Revision `0.19.7` restores the bounded public observation role of the AutoAdapter 1.0 development
sandbox. Each `check_driver` capability smoke now returns terminal actuator controls, generalized
state, and positions for names already declared in public Morphology. The result is explicitly
source/import/physics-liveness feedback, not capability validation or a private Harness verdict.
No private scene, reset, binding, criterion, threshold, guard, or expected trajectory is exposed.

**中文辅助说明。** `0.19.7` 恢复 AutoAdapter 1.0 development sandbox 的有界公开观察作用。
每项 `check_driver` capability smoke 返回终态 actuator control、通用状态及公开 Morphology 已声明
名称的位置。结果只代表 source/import/physics liveness 开发反馈，不是 capability validation 或私有
Harness verdict；不会暴露私有 scene、reset、binding、criterion、threshold、guard 或 expected trajectory。

Revision `0.19.6` removes redundant STUDY file-discovery turns. The initial STUDY input already
contains the complete public robot-package projection, selected MJCF closure, sealed Capability
Design, and every condition-eligible skeleton source. STUDY therefore exposes one bounded
`run_mujoco_probe` action followed by one reserved `submit_study` action, for a hard maximum of two
model turns. This changes no public information, private boundary, or physical-probe requirement.

**中文辅助说明。** `0.19.6` 删除 STUDY 中重复的文件发现回合。STUDY 初始输入已包含完整公开
robot-package projection、selected MJCF closure、封存 Capability Design 以及该条件允许的全部
skeleton 源码，因此只暴露一次有界 `run_mujoco_probe`，下一回合固定为 `submit_study`，硬上限
为两个模型回合。公开信息、私有边界及真实物理 probe 要求均不改变。

Revision `0.19.5` restores the AutoAdapter 1.0 implementation-action granularity. The initial
GENERATE/GEN_ALGO context contains the mechanically generated interface-only driver stub, and the
initial Repair context contains the complete previous driver and candidate-facing report. Neither
stage spends remote model turns reading or separately writing that source. One
`check_driver(source, checks)` action supplies the complete candidate revision plus exactly one
covered public request per sealed capability; the Framework atomically writes it and performs the
Revision `0.19.4` audit/import/all-capability physics check. A successful normal path then uses one
separate explicit `submit_driver` turn. Each driver stage permits at most three discretionary public
development probes in addition to one mandatory import and one smoke per capability, is bounded to
twelve model turns, and uses a 16,384-token model-output ceiling. STUDY is bounded to six model turns.
These are execution limits, not private validation information, and private Harness execution still
starts only after explicit submission.

**中文辅助说明。** `0.19.5` 恢复 AutoAdapter 1.0 的实现动作粒度。GENERATE/GEN_ALGO 首次上下文
直接含机械生成的纯接口 driver stub；Repair 首次上下文直接含完整旧 driver 和候选可见报告，
两者都不再为读取或单独写入源码消耗远程模型回合。一次 `check_driver(source, checks)` 同时提交
完整候选 revision 和每项 sealed capability 恰好一个覆盖范围内的公开请求；Framework 原子地
写入并执行 `0.19.4` 的审计、导入及全部 capability 物理检查。正常成功路径随后仅再用一个显式
`submit_driver` 回合。每个 driver 阶段除 mandatory import 和逐 capability smoke 外，最多有三个
自由公开 development probe，最多十二个模型回合，模型输出上限为 16,384 token；STUDY 最多
六个模型回合。这些仅是执行预算，不是私有验证信息；显式提交前仍不会进入私有 Harness。

Revision `0.19.4` keeps interactive Driver Synthesis while removing one model round trip per
public check. For each current driver revision, GENERATE/GEN_ALGO and Repair provide one bundled
`check_driver` request containing exactly one covered public invocation per sealed capability. The
Framework performs source audit, canonical import/build, and every capability physics smoke inside
that single tool execution and returns per-capability public diagnostics. The model may revise and
rerun the bundled check, and must still explicitly submit the checked current revision before any
private Harness execution. Individual audit, import, and per-capability smoke calls are not separate
model turns on the mainline.

**中文辅助说明。** `0.19.4` 保留交互式 Driver Synthesis，但不再让每项公开检查各占一次模型
往返。GENERATE/GEN_ALGO 和 Repair 针对当前 driver revision 一次提交 `check_driver`，其中每项
sealed capability 恰好提供一个覆盖范围内的公开调用。Framework 在这一次工具执行内完成源码
审计、canonical import/build 及全部 capability physics smoke，并一次返回逐项公开诊断。模型仍可
修改后重新检查，且私有 Harness 前仍必须显式提交已检查的当前 revision。

Revision `0.19.3` makes capability admission proportional to the designed capability interface
rather than to the size of the source Task Library. Each capability has exactly one source-backed primary
validation contract and may have at most one source-backed robustness contract when a materially
different scene, metric, or temporal obligation cannot be represented by the primary case. The IVC
therefore compiles one primary private case and at most one justified robustness case per
capability; it must not mechanically emit one capability case for every source task or scoring
clause. The complete Task Library remains the grounding and abstraction input. Separately, Task
Demo uniformly samples five original Task Library tasks and compiles all scoring clauses belonging
to those selected tasks. This revision also applies the AutoAdapter 1.0 bounded-history principle
to tool-using Driver Synthesis: the model-facing projection retains the initial public context, one
current driver source, and the three most recent completed tool-interaction groups; superseded
large tool payloads are represented by deterministic tool name, revision, character count, and
result status metadata. Full traces remain on disk and no model-written summary is introduced.

**中文辅助说明。** `0.19.3` 让 capability 准入规模与设计出的 capability interface 对齐，而不是
随来源 Task Library 大小机械增长。每项 capability 恰好具有一个有来源的 primary validation
contract；只有 primary case 无法表达确实不同的 scene、metric 或 temporal obligation 时，才可
再有最多一个有来源的 robustness contract。IVC 因而为每项 capability 编译一个 primary 私有
case，并在有明确理由时增加最多一个 robustness case；不得按每项来源 task 或 scoring clause
机械生成 capability case。完整 Task Library 仍用于 grounding 和能力抽象。Task Demo 则另行
均匀随机抽取五项原始 Task Library task，并编译这些被选 task 的全部 scoring clause。本修订还
把 AutoAdapter 1.0 的有界历史原则用于带工具的 Driver Synthesis：发给模型的投影保留首次公开
上下文、一份当前 driver 源码和最近三个已完成工具交互组；已被取代的大型工具 payload 只保留
确定性的 tool 名称、revision、字符数和结果状态。完整 trace 仍保存在磁盘，且不引入模型摘要。

Revision `0.19.2` restores the AutoAdapter 1.0 separation between capability validation and the
later Demo phase. The complete implementation-blind IVC output is sealed as
`capability_validation_suite.json`; it covers every designed capability and source-standard clause
and is the only suite used for reference calibration, generated-driver admission, and same-run
Repair. After a generated driver passes that suite, the Framework runs a separately recorded random
five-case `task_demo_suite.json` with the fixed admitted driver. Task Demo has its own verdict, does
not gate or overwrite the driver-synthesis verdict, and is never same-run Repair input. Its terminal
result may be supplied to the non-blocking Evolution sidecar for a later run. This revision
supersedes Revision `0.18.14` where the random five-case sample was used as formal driver validation.

**中文辅助说明。** `0.19.2` 恢复 AutoAdapter 1.0 中 capability validation 与后续 Demo 阶段的
分离。实现不可见的 IVC 完整输出封存为 `capability_validation_suite.json`，覆盖每项设计
capability 和每条来源标准 clause，并且是 reference 校准、生成 driver 准入和本轮 Repair 唯一
使用的 suite。生成 driver 通过该 suite 后，Framework 再用固定的已准入 driver 运行一套另行
记录、随机抽取五个 case 的 `task_demo_suite.json`。Task Demo 有独立 verdict，不作为
driver-synthesis 的通过门槛，不覆盖其 verdict，也绝不进入本轮 Repair；其终态结果可以交给
非阻塞 Evolution sidecar，供后续 run 使用。本修订取代 `0.18.14` 中用随机五-case 样本进行正式
driver validation 的规则。

Revision `0.19.1` locks the generated software product as the **robot-specific driver**. The robot
capability interface is the reusable caller-facing contract surface; it is
designed and exposed, not treated as a separate executable artefact. Driver Synthesis produces a
candidate robot-specific driver for one declared robot configuration, and independent validation
determines whether that driver passes the sealed complete capability validation suite.
Capability-interface use therefore holds both the public interface and the validated driver that
implements it fixed.

**中文辅助说明。** `0.19.1` 将生成的软件产物锁定为 **robot-specific driver**。Robot capability
interface 是面向调用方的可复用合同接口；它被设计和暴露，不作为独立的可执行
产物。Driver Synthesis 为一个明确的机器人配置生成 candidate robot-specific driver，随后由独立
validation 判断该 driver 是否通过封存的完整 capability validation suite。因此，capability-interface
use 必须同时固定公开 interface 及实现它的已验证 driver。

Revision `0.19.0` aligns the Authority with the three thesis experiments. It separates the
robot-software synthesis track from capability-interface use, defines the three Auto-Adapter component
analyses, and distinguishes the earlier narrow engineering acceptance milestone from the declared
cross-morphology experiment. It also makes explicit that cross-morphology results describe
associations rather than causal morphology effects.

**中文辅助说明。** `0.19.0` 使本 Authority 与论文的三个实验保持一致。它区分 robot-software
synthesis track 与 capability-interface use，明确 Auto-Adapter 的三项组件分析，并把早期小范围工程
验收里程碑与正式声明的 cross-morphology 实验区分开。它同时明确：cross-morphology 结果描述关联，
而不能解释为 morphology 的因果效应。

The Direct-MuJoCo direction retained by this revision replaces the former rule that the initial
formal acceptance path had to execute through a real SDK and an SDK-specific Translation Layer.
The default Auto-Adapter 2.0 experiment studies model-generated, robot-specific Direct-MuJoCo
drivers under both preserved AutoAdapter 1.0 generation conditions: trusted-skeleton-assisted and
controller-from-scratch. SDK-grounded execution remains valuable, but it is developed and evaluated
separately and does not block the mainline.

Revision `0.18.3` also removes the pre-authored fixed-task-to-effect policy from the formal
experiment. Each declared robot instead supplies an admitted source-backed Task Library of at
least twenty applicable tasks and pass standards. Task-Grounded Capability Design must produce five
to ten reusable capability designs, their effects and interfaces, and source-traceable public
validation contracts from that Library. The Independent Validation Compiler audits and compiles
those contracts into the private executable suite; it does not let the design phase judge the
implementation.

Revision `0.18.4` changes the normative phase names to match their actual outputs. `Task-Grounded
Capability Design` (`TGCD`) replaces `Stage 1`; `Independent Validation Compiler` (`IVC`) replaces
`Blue Line`; and `Driver Synthesis` names the later phase that produces executable code. The term
`synthesis` is not used for TGCD because TGCD outputs a design contract, not an executable
robot-specific driver. Historical Demo2 code and evidence may retain the former names only when
describing that historical implementation.

Revision `0.18.5` designates `demo3/` as the active implementation workspace and makes a complete,
source-backed Task Library an admission condition for every robot exposed as runnable by Demo3.
Every scoring clause must identify the benchmark or industrial
standard source that supports it and any explicit adaptation to the selected robot and MuJoCo
configuration. An incomplete robot package may remain a research candidate but cannot enter the
runnable index or a formal run.

Revision `0.18.6` makes Demo3 self-contained at the project boundary. All project-authored code,
contracts, prompts, task/source records, private evaluation inputs, robot assets, skeletons,
AutoAdapter 1.0 generation routes, and calibration references required by a run must live under
`demo3/`. A run may not import or read another repository subtree, an absolute developer-machine
path, or a runtime-downloaded asset. The only permitted external dependencies are the explicitly
declared base runtime packages and configured real-model service required to execute the experiment.

Revision `0.18.7` classifies the known Demo2 false-success, isolation, evidence, environment, and
scope defects as construction-time obligations for Demo3 rather than a post-hoc Demo2 repair list.
Demo2 remains a historical baseline; Demo3 must implement the corresponding protections and
evidence semantics as each affected path is built.

Revision `0.18.8` restores the full-information Repair behavior of AutoAdapter 1.0 while retaining
the private-validation boundary. Driver Synthesis may inspect all run-relevant public robot inputs
and use a complete, budget-bounded local Python/MuJoCo development probe. After an attempt, Repair
receives the complete candidate-facing attempt report and media; only the private IVC/Harness
validation definitions and unrelated secrets remain unavailable.

Revision `0.18.9` makes the calibration/reference boundary explicit. A package reference driver is
trusted positive-control material used only by the Framework for pre-dynamic calibration. Its source,
derived driver logic, and generated wrappers are never inputs to TGCD, IVC, STUDY, GENERATE, Repair,
Evolution, or a dynamic development probe, and cannot be admitted as Experience. This prevents
either generation condition from copying the positive control.

Revision `0.18.10` separates model-authored capability interfaces from the stable callable transport
ABI. TGCD still authors every capability/effect and arbitrary public method name, but every method is
invoked as `method(request=...)`, where the public `request` contains `task_id` and the task's declared
`task_parameters`. Each Task Library task publishes that request schema. The fixed envelope is runtime
plumbing, not an effect catalog, capability template, or task-to-effect allowlist; it prevents private
instances from having to predict model-authored Python parameter names.

Revision `0.18.11` clarifies bounded model-facing transports observed in the first complete Demo3
run. The Framework owns and canonicalizes the generation-condition enum and public `request`
envelope; the generated driver exposes each capability as an instance method on the object returned
by `build()`, and each method receives `request` as a plain mapping. A development-probe source-audit
rejection is reported but never executed and does not by itself prevent Repair from producing revised
source. Evolution may
receive a bounded terminal projection that preserves outcome counts and failed-trial diagnostics
while omitting repeated full trajectory samples; the complete report remains retained as evidence
and the complete attempt report remains available to Repair.

Revision `0.18.12` restores the interactive AutoAdapter 1.0 execution behavior for Driver
Synthesis. STUDY, GENERATE/GEN_ALGO, and Repair are bounded multi-turn ReAct phases in which the
model can inspect public files, run public-only Python/MuJoCo probes, write and revise its own
candidate, and react to source-audit, import, and public smoke diagnostics before explicitly
submitting a formal driver attempt. A one-shot model response followed immediately by Framework
submission is not the mainline Driver Synthesis path.

Revision `0.18.13` permits one mechanical interface-only starting stub derived from the sealed
Capability Design. The same stub is supplied to both generation conditions and may contain only a
generic driver class, `build(model, data)`, each exact model-authored capability name, the
`(self, request)` ABI, and `NotImplementedError` bodies. It contains no task dispatch, controller,
actuator mapping, state target, control value, physics step, skeleton choice, or reference-derived
logic. The model must replace the placeholder bodies with its own executable implementation before
explicit submission; the stub is not a submitted attempt and cannot satisfy source audit or public
physics smoke by itself.

Revision `0.18.14` originally limited formal physical validation to five cases sampled from the
complete IVC output. Revision `0.19.2` supersedes that role: the complete IVC output now performs
capability validation, while the recorded five-case sample is retained only for the later Task Demo.

**中文辅助说明。** 本修订保留的 Direct-MuJoCo 方向取代此前“早期正式验收路径
必须经过真实 SDK 和 SDK 专属 Translation Layer 执行”的规则。Auto-Adapter 2.0 的默认实验
现在研究由模型在可信 skeleton 辅助和 controller-from-scratch 两种保留的 AutoAdapter 1.0
生成条件下，生成面向特定机器人的 Direct-MuJoCo driver。基于 SDK 的执行仍有研究价值，但将
独立开发和评估，且不再阻塞主线。

`0.18.3` 还从正式实验中移除了预先编写的固定 task→effect 策略。每个声明的机器人改为提供一份
已准入、具有来源依据的 Task Library，其中至少包含二十项适用于该机器人的任务及通过标准。
Task-Grounded Capability Design 必须从该 Library 产生五至十项可复用 capability 设计、对应
effect 和接口，以及可追溯到来源的公开 validation contract。Independent Validation Compiler
负责审计并把这些合同编译成私有可执行 suite；设计阶段不能为后续实现做最终判定。

`0.18.4` 根据各阶段的真实产物统一规范名称：`Task-Grounded Capability Design`（`TGCD`）取代
`Stage 1`；`Independent Validation Compiler`（`IVC`）取代 `Blue Line`；后续真正生成可执行
代码的阶段称为 `Driver Synthesis`。TGCD 只输出设计合同而不输出可执行 robot-specific driver，
因此不使用 `synthesis` 一词。旧名称只允许在明确描述历史 Demo2 代码或证据时保留。

`0.18.5` 指定 `demo3/` 为活动实现 workspace，并把完整、有来源依据的 Task Library 设为 Demo3
每个 runnable 机器人的准入条件。每条评分 clause 都必须标明支持
它的 benchmark 或工业标准来源，以及向所选机器人和 MuJoCo 配置所做的明确改编。Task Library
不完整的机器人可以保留为 research candidate，但不能进入 runnable index 或正式 run。

`0.18.6` 要求 Demo3 在项目边界内完全自包含。一次运行所需的全部项目代码、合同、prompt、
task/source record、私有评估输入、机器人资产、skeleton、AutoAdapter 1.0 两条生成路径和校准
reference 都必须位于 `demo3/`。运行不得导入或读取仓库其他目录、开发者机器绝对路径或运行时
下载的资产。唯一允许的外部依赖是执行实验所必需且显式声明的基础 runtime package 和已配置的
真实模型服务。

`0.18.7` 明确把已知的 Demo2 假成功、隔离、证据、环境和范围缺陷定义为 Demo3 搭建阶段必须
落实的义务，而不是事后修补 Demo2 的清单。Demo2 保留为历史基线；Demo3 在建立每条相关路径时
必须同时实现对应保护和证据语义。

`0.18.8` 在保留私有 validation 边界的同时，恢复 AutoAdapter 1.0 的完整信息 Repair 行为。
Driver Synthesis 可以检查与本次 run 相关的全部公开机器人输入，并使用完整但有预算上限的本地
Python/MuJoCo 开发 probe。一次 attempt 结束后，Repair 可以读取完整的 candidate-facing attempt
报告和媒体；只有 IVC/Harness 私有 validation 定义及无关 secret 保持不可见。

`0.18.10` 将模型创作的 capability interface 与稳定的 callable transport ABI 分开。TGCD 仍然
自主设计 capability/effect 和任意公开方法名，但 Framework 始终以 `method(request=...)` 调用，
其中公开 `request` 包含 `task_id` 和该任务声明的 `task_parameters`。每项 Task Library task 都要
公开该 request schema。固定调用信封只是 runtime plumbing，不是 effect 库、capability 模板或
task→effect allowlist；它避免私有 instance 必须预先猜测模型生成的 Python 参数名。

`0.18.11` 明确首次完整 Demo3 run 暴露出的有界模型接口：Framework 负责规范化 generation
condition 枚举和公开 `request` 信封；生成 driver 必须把每个 capability 暴露为 `build()` 返回对象
上的实例方法，并把 `request` 当作普通 mapping。未通过公开源码审计的 development probe 只返回拒绝诊断，
不得执行，也不应单独阻止 Repair 继续生成修订源码。Evolution 可以读取保留终态、计数和完整
失败 trial 诊断的有界投影，并用 sample count 代替重复轨迹 sample；完整报告仍作为证据保留，
完整 attempt 报告仍提供给 Repair。

`0.18.12` 恢复 AutoAdapter 1.0 的交互式 Driver Synthesis 执行行为。STUDY、
GENERATE/GEN_ALGO 和 Repair 都是有预算的多轮 ReAct phase；模型可以读取公开文件、运行仅含
公开输入的 Python/MuJoCo probe、编写并反复修改自己的 candidate，并在明确提交正式 driver
attempt 前根据 source audit、import 和公开 smoke 诊断自行修正。一次模型调用直接输出代码并由
Framework 立即提交，不再是主线 Driver Synthesis。

`0.18.13` 允许 Framework 根据已封存 Capability Design 机械生成一份纯接口起始 stub，并向
两种生成条件提供完全相同的版本。stub 只能包含通用 driver class、`build(model, data)`、每个
模型设计的准确 capability 名称、`(self, request)` ABI 和 `NotImplementedError` 占位体；不得
包含 task dispatch、controller、actuator mapping、状态目标、控制值、physics step、skeleton
选择或任何 reference 派生逻辑。模型必须在明确提交前把占位体替换成自己创作的可执行实现；stub
本身不是正式 attempt，也不能独自通过 source audit 或公开 physics smoke。

`0.18.14` 原本把正式物理 validation 限制为从 IVC 完整输出中抽取五个 case；`0.19.2` 已取代
这一用途：IVC 完整输出现在负责 capability validation，而有记录的五-case 样本只保留给后续
Task Demo。

---

## 0. Authority and precedence / 权威性与优先级

### 0.1 Project authority and scoped experiment delegation / 项目权威与限定实验委派

This file is the only normative source for the current project objective, architecture boundary,
research questions, mainline acceptance, and SDK-extension relationship. README files, project
plans, prompts, schemas, source code, Library records, and historical runs may implement or
provide evidence for this Authority, but their existence does not create requirements or override
it. When they conflict, this file governs and the conflicting material must be corrected.

One experiment or declared extension may have one active scoped authority only when this section
names its exact path and bounded scope. The current delegations are:

- `experiment/experiment1a_generation/EXPERIMENT_1_AUTHORITY.md` (`AA2-EXP1`), which governs Experiment 1a/B1's
  exact robot selection, Producer-backbone families, generation conditions, replicate plan,
  attempt budget, analysis boundary, extension rule, and execution blockers; and
- `experiment/experiment1b_use/B2_RECAP_AUTHORITY.md` (`AA2-B2`), which governs only the Chapter 3 Experiment 1b/B2 ReCAP
  capability-interface-use extension's exact robots, tasks, controller architecture, backbone
  factor, replicate plan, fixed-input requirements, analysis boundary, and execution blockers.
  B2 is not Experiment 2. Within B2 only, `AA2-B2` selects its fixed ReCAP controller consistently
  with the active capability and isolation boundaries in Sections 0.1.1, 3.0, and 4; and
- `experiment/experiment2/EXPERIMENT_2_AUTHORITY.md` (`AA2-EXP2`, revision `0.2.0`), retained as a
  historical scoped protocol only. It is not active under revision `0.20.0`; its pre-capability-v2
  terms do not govern the current mainline, and it may not be dispatched or used as an input, test
  gate, or acceptance gate until a separately approved migration reactivates it; and
- `experiment/experiment3/EXPERIMENT_3_AUTHORITY.md` (`AA2-EXP3`, revision `0.2.0`), which governs
  the exact capability-v2 eleven Direct-MuJoCo configurations, Sonnet-only skeleton-assisted condition,
  `r01`--`r03` fresh STUDY/TGCD/IVC cells, three-frozen-Driver budget, ReCAP Task Demo stop point, 33-cell
  denominator, descriptive morphology boundary, and execution blockers.

Each delegated authority implements, and may not otherwise weaken or override, this project's
architecture, isolation, Harness, physical-integrity, evidence, or authenticity requirements.
Within a delegated scope, manifests, run records, analysis code, thesis prose, and benchmark
documents remain subordinate to the applicable scoped authority. Any actual conflict between a
scoped authority and this project-wide Authority blocks the affected execution until one of the two
is prospectively corrected.

English clauses are normative. Chinese headings, tables, and paragraphs are faithful reading aids
and must not add, remove, weaken, or strengthen a requirement. If the two languages diverge, the
English text governs and the Chinese text must be corrected.

Git history preserves superseded designs. The repository must not create an undelegated parallel
authority, a second active authority for the same experiment, or retain an obsolete design as a
parallel normative source.

**中文辅助说明。** 本文件是当前项目目标、架构边界、研究问题、主线验收标准以及 SDK 扩展
关系的唯一项目范围规范来源。只有本节点名精确路径和限定范围后，一个实验或已声明扩展才可以
拥有一份有效的限定权威。当前委派为：
`experiment/experiment1a_generation/EXPERIMENT_1_AUTHORITY.md`（`AA2-EXP1`）负责 Chapter 3 Experiment 1a/B1 的限定
实验设计；`experiment/experiment1b_use/B2_RECAP_AUTHORITY.md`（`AA2-B2`）仅负责 B2 ReCAP
capability-interface-use 扩展的机器人、task、controller 架构、backbone 因素、replicate、固定输入、
分析边界和执行 blocker。B2 不是 Experiment 2；仅在 B2 范围内，`AA2-B2` 按第 0.1.1、3.0 与
4 节的当前 capability 和隔离边界选择固定 ReCAP controller。任何限定权威都不能在其他方面削弱或覆盖本文件的
架构、隔离、Harness、物理完整性、证据或真实性要求。README、manifest、run record、分析代码、论文文本和
benchmark 文档都不能覆盖相应权威。英文条款具有规范效力；中文文本只作辅助阅读。Git 历史保留被取代
设计；仓库不得创建未经委派的并行权威、同一实验或扩展的第二份有效权威，或把旧设计保留为并行规范来源。

`experiment/experiment2/EXPERIMENT_2_AUTHORITY.md`（`AA2-EXP2`）只作为历史 scoped protocol
保留，在修订 `0.20.0` 下不生效；其中 capability-v2 之前的条款不约束当前主线，未经另行批准的迁移
不得 dispatch，也不是输入、测试 gate 或验收 gate；
`experiment/experiment3/EXPERIMENT_3_AUTHORITY.md`（`AA2-EXP3`）负责精确十一配置、
Sonnet-only skeleton-assisted、`r01`--`r03`、每 cell 新鲜 STUDY/TGCD/IVC、最多三个 frozen Driver、
ReCAP Task Demo stop、33-cell denominator 和 morphology descriptive-only 边界。Experiment 3
禁用 Evolution 和 Experience。

### 0.1.1 Capability-v2 and file-artifact contract / Capability-v2 与文件产物合同

`capability-v2` is the sole current mainline capability protocol. A capability
is one package-bound public callable operation with bounded inputs, bounded
execution, and one composable physical effect. TGCD authors three to ten such
contracts; it does not select a package-owned primitive catalogue. One
capability may support several tasks and one task may use several capabilities,
but `task_support` records only that relation and rationale. It never records
ordered calls, waypoints, a task macro, or a dispatch table.

The request schema is closed and task-neutral. Candidate Driver code may not
read or branch on `task_id`, an entire task/request envelope, scene/reset data,
private criteria, an oracle plan, or a task macro. Package-private reference
Drivers may internally dispatch on private task fixtures solely for IVC
positive control and Harness calibration; their source, task IDs, calls, and
plans are never exposed to TGCD, Driver Synthesis, Repair, or ReCAP.

The four model-authored phase products are the canonical files `study.json`,
`capability_design.json`, `capability_validation_suite.json`, and `driver.py`.
The Framework validates the expected file when a phase ends normally. An
invalid file produces a bounded error in the same conversation when turns
remain; a valid file ends and seals the phase, including when it was written on
the final turn. No `submit_study`, `submit_capability_design`,
`submit_validation_suite`, `check_driver`, or `submit_driver` tool is part of
the active protocol. Invalid JSON, a stub, missing capability methods, source
boundary failure, or import failure is not a formal Driver attempt.

IVC is implementation-blind and receives the sealed design plus private
instances, bindings, guards, and sanitised validation examples. It compiles
exactly one nominal and one calibrated-boundary case for every capability,
copies the sealed criteria without weakening them, and runs both case types
against the private package reference Driver before sealing in a formal run.
A `formal=false` diagnostic may explicitly skip that positive control; the
result remains diagnostic and cannot be counted as formal evidence. ReCAP derives its
tools dynamically from only those capabilities for which both case types pass.
It composes capability calls for the current Task Demo task but does not persist
that composition as a capability. A controller `finish` or self-report is never
a trusted Harness verdict.

The SO-101 six-capability and Go2 five-capability public reference projections
include complete request parameters and criteria with source-backed bounds and
thresholds. They contain no task mapping, `task_support`, Exp1b oracle plan,
waypoint plan, or concrete call sequence. References inform model design; they
do not predetermine another robot's capability set.

### 0.2 Experiment-grade governance / 实验级治理

The project uses direct check results and concise run evidence. It does not implement lifecycle,
document, artifact, admission, readiness, promotion, freeze, or experiment status machines.
Artifacts use human-readable IDs, versions, paths, run IDs, and ordinary result fields. They do
not require cryptographic hashes, content addressing, signatures, attestations, evidence chains,
registries, activation/revocation events, or governance locks.

Words such as “completed,” “passed,” and “failed” describe one concrete execution. They are not
persistent states. Scientific measures such as `pass@0` and `pass@k` are outcome metrics, not
workflow states.

**中文辅助说明。** 项目采用直接检查结果和简洁运行证据，不实现生命周期、文档、产物、准入、
就绪、晋级、冻结或实验状态机。产物使用人类可读的 ID、版本、路径、run ID 和普通结果字段，
不要求密码学哈希、内容寻址、签名、证明、证据链、注册表、激活/撤销事件或治理锁。
“completed”“passed”“failed”等词只描述某一次具体执行，不是持久状态；`pass@0`、`pass@k`
等科学指标是结果度量，而不是工作流状态。

### 0.3 Location of exact details / 精确细节的位置

This Authority defines stable boundaries and acceptance rules. Exact JSON schemas, prompts,
private thresholds, task instances, model endpoint settings, dependency locks, skeleton source,
and robot asset paths belong in the implementation or versioned Libraries. They must not be
duplicated here unless needed to distinguish the research claim.

When several implementations satisfy this document, use the smallest one that runs the real
experiment. Do not delay a real run for broad refactoring, production hardening, speculative
extension points, or exhaustive testing.

**中文辅助说明。** 本文件定义稳定边界和验收规则。精确 JSON schema、prompt、私有阈值、
任务实例、模型 endpoint 设置、依赖锁、skeleton 来源和机器人资产路径应保存在实现代码或版本化
Library 中；除非为了区分研究主张，否则不得在此重复。当多种实现都满足本文件时，应选择能够
跑通真实实验的最小实现。不得为了大范围重构、生产级加固、推测性扩展点或穷尽式测试而延迟
真实运行。

### 0.4 Normative terminology and naming / 规范术语与命名

The following terms are locked across this Authority, experiment reports, and thesis prose:

| Term | Normative meaning |
|---|---|
| **Auto-Adapter** | The current research framework. Research and design prose uses the hyphenated form. `AutoAdapter` is retained only in code or directory identifiers and when naming the historical `AutoAdapter 1.0` system or its preserved artefacts. |
| **high-level controller** | The task-level component that selects and sequences robot capabilities. In an LLM-backbone comparison, its architecture, prompt, tool exposure, and decision loop remain fixed while only the LLM backbone changes. |
| **robot capability interface** | The reusable, caller-facing contract surface through which a high-level controller invokes robot operations. It is formed by the public capability contracts and is designed, exposed, and used; it is not the executable software product. |
| **capability** | One implementation-independent contract for a callable robot operation exposed by the robot capability interface, defined by its semantics, inputs, outputs, preconditions, and measurable acceptance obligations. `Skill` is reserved for a task-level, temporally extended behaviour and is not a synonym for a capability. |
| **robot-specific driver** | The primary executable, application-level robot-integration artefact initially produced by Driver Synthesis and, when required, revised by Repair for one declared robot configuration. It implements the robot capability interface; it is not an operating-system device driver. |
| **capability-level pass criterion** | A measurable acceptance condition for one capability. It is derived from source-backed task pass standards and is independently audited, compiled, and evaluated outside the candidate driver. |
| **Task Demo** | A ReCAP-controlled demonstration that runs the protocol-selected recorded Task Library tasks with the final frozen Driver and only the capabilities whose nominal and calibrated-boundary cases both passed. It has a separate trusted-Harness verdict, is not a driver-synthesis gate, and cannot trigger same-run Repair. |
| **robot-software synthesis** | The experiment-level process of designing a robot capability interface and its capability-level pass criteria, synthesising a robot-specific driver that implements the interface, and independently validating that driver. The primary executable product is the robot-specific driver; TGCD still produces only a design contract, and `Driver Synthesis` remains the phase that produces executable code. |
| **capability-interface use** | The use of a fixed robot capability interface, backed by the same fixed and validated robot-specific driver, by an otherwise matched high-level controller whose LLM backbone is the experimental variable. Experiment 1b/B2 is the bounded Chapter 3/RQ1 use comparison; its evidence remains separate from driver-synthesis evidence and does not support Experiment 2 or RQ2. |
| **low-level motion-control capability** | A capability whose implementation converts a requested robot operation into robot-specific actuation, kinematic or locomotion control, and physics stepping. It must not be called a low-level motion-control skill. |
| **trusted skeleton** | Robot-control implementation assistance available only in the skeleton-assisted generation condition. It is distinct from the high-level controller. |
| **robot morphology** | A robot's physical form and joint arrangement. It is distinct from a **robot configuration**, which identifies the exact model, assets, actuators, and control setup used in a run. In the current formal programme, morphology is a required package fact and cohort descriptor, not an experimental factor. |

The labels `L0` and `L1` are not normative terms and must not be used in research claims or thesis
prose. Exact phase names such as `Task-Grounded Capability Design`, `Independent Validation
Compiler`, `Driver Synthesis`, `Repair`, `Task Demo`, and `Evolution` retain the meanings defined in
Sections 0.1.1 and 3.0.

**中文辅助说明。** 上表中的术语在本 Authority、实验报告和论文正文中保持一致。当前研究框架
写作 `Auto-Adapter`；`AutoAdapter` 仅保留给代码或目录标识，以及历史系统 `AutoAdapter 1.0`
及其产物。`high-level controller` 负责在任务层选择和编排 capability；在 LLM backbone 对比中，
其架构、prompt、tool exposure 和 decision loop 保持固定，只有 backbone 改变。`robot capability
interface` 是由公开 capability contract 构成、供该 controller 调用机器人操作的可复用接口；它不是
可执行软件产物。`capability` 表示该接口暴露的一项与实现无关的可调用机器人操作合同；`skill`
只表示任务层的时间扩展行为，不得作为 capability 的同义词。`robot-specific driver` 是针对一个
准确机器人配置、由 Driver Synthesis 首次生成并在需要时由 Repair 修订的主要可执行、应用层
机器人集成产物；它负责实现该 interface，不是操作系统 device driver。
`robot-software synthesis` 表示从能力接口设计、capability-level pass criteria 设计、driver 合成到
独立验证的实验级过程，但 TGCD 本身仍不称为 synthesis。`capability-interface use` 必须同时固定公开
interface 及实现它的同一套固定且已验证的 driver；Experiment 1b/B2 在 Chapter 3/RQ1 中对该用途做
有界比较，其证据不能代替 driver-synthesis 证据，也不支持 Experiment 2 或 RQ2。`low-level
motion-control capability` 不得写作 low-level motion-control skill。`trusted skeleton` 是
skeleton-assisted 条件中的机器人控制实现辅助，不是 high-level controller。`robot morphology`
指物理形态和关节排列；`robot configuration` 指一次运行使用的准确模型、资产、actuator 和控制
设置。在当前正式研究 programme 中，morphology 是必需 package fact 和 cohort descriptor，不是
实验 factor。`Task Demo` 由 ReCAP 使用最终 frozen Driver 以及 nominal 与 calibrated-boundary
两类 case 都通过的 capability 白名单，执行协议选定且录制的 Task Library tasks；它由可信 Harness
独立判定，不是 driver-synthesis gate，也不能触发本轮 Repair。
`L0`、`L1` 不属于规范术语。

---

## 1. Project objective and claim boundary / 项目目标与主张边界

### 1.1 Research objective / 研究目标

Build the simplest experiment-grade Auto-Adapter 2.0 framework and evaluation needed to determine,
under controlled Direct-MuJoCo conditions, how LLM backbones differ in fixed-input robot-specific
driver synthesis, whether reusable capability and validation contracts can be derived from
source-backed tasks, and how the exact declared construction cohort behaves under a bounded
end-to-end route. The generic Evolution/Experience handoff remains implemented for a separately
authorised later run and the non-formal DeepSeek diagnostic, but no formal Experiment 2 claim is
active under revision `0.20.0`.

The research programme separates three objects that must not be conflated. Upstream capability and
pass-criteria design determines the fixed contract that a driver must implement and how its outcomes
will be judged. Fixed-input driver synthesis concerns whether a generated driver implements that
sealed contract and passes independent validation. Evolution may propose future-run Experience only
after a terminal outcome and can never change the source run. Experiment 3 starts each cell with empty
Experience and disables Evolution.

Capability-interface use remains conceptually separate from driver synthesis. Chapter 3/RQ1
therefore has two bounded delegated subexperiments: Experiment 1a/B1 evaluates fixed-input
robot-specific driver synthesis, while Experiment 1b/B2 evaluates the usability of the deployed
model and endpoint through the fixed ReCAP capability interface. B2 is separate evidence and is
not Experiment 2 or RQ2.

The current construction milestone is preparation for Experiment 3: the exact eleven-configuration cohort in
Section 1.3, Sonnet 4.6, skeleton-assisted only, `r01`--`r03`, fresh TGCD/IVC per cell, empty
Experience, at most three frozen Drivers, and Task Demo followed by a stop. This revision does not
authorise dispatch of its 33 formal cells; dispatch requires separate user approval. When executed,
it is mechanism and descriptive evidence, not a production platform, universal robot-support claim,
or morphology effect.

**中文辅助说明。** 构建最简单的实验级 Auto-Adapter 2.0 框架和评估，以在受控
Direct-MuJoCo 条件下研究：不同 LLM backbone 在固定输入 robot-specific driver synthesis 中有何
差异；能否从有来源任务中产生可复用 capability 与 validation contract；以及精确声明 cohort 在
有界端到端 route 下如何表现。通用 Evolution/Experience handoff 只为另行授权的 later run 与
非正式 DeepSeek 诊断保留；修订 `0.20.0` 下没有 active formal Experiment 2 claim。研究必须区分
三个不能混为一谈的
对象：上游 capability 与 pass-criteria 设计决定 driver 必须实现什么以及如何判定；固定输入 driver
synthesis 研究生成 driver 能否实现封存 contract 并通过独立 validation；Evolution 只能在 terminal
outcome 后提出供未来 run 使用的 Experience，不能改变产生证据的当前 run。Capability-interface use
与 driver synthesis 仍须明确区分；Chapter 3/RQ1 因此
包含两个有界委派子实验：Experiment 1a/B1 评估固定输入的 robot-specific driver synthesis，
Experiment 1b/B2 评估已部署 model 和 endpoint 通过固定 ReCAP capability interface 的可用性。B2 的
证据独立存在，不是 Experiment 2 或 RQ2。当前工程里程碑是准备 Experiment 3 的精确 33-cell
construction cohort；本修订不授权正式 dispatch，仍需用户另行批准。执行后它也只属于描述性建设
证据，不是生产平台或通用机器人支持主张。

### 1.2 Claim boundary / 主张边界

The project may claim only what the corresponding experiment evidence directly supports:

- comparative robot-specific driver-synthesis outcomes for the declared LLM backbones under the
  skeleton-assisted and from-scratch generation conditions, with the Auto-Adapter configuration,
  robot inputs, source-backed Task Library, robot capability interface, capability-level pass
  standards, complete private validation suite, evaluation protocol, and resource budgets fixed
  per robot across every backbone, replicate, and condition;
- task-grounded model design of reusable capabilities from at least twenty source-backed tasks;
- model-authored and source-grounded capability-level pass criteria that the implementation-blind
  IVC independently audits and compiles, and that the Harness evaluates under declared calibration
  and false-success checks, as separate RQ2 evidence rather than B1-varying inputs;
- diagnostic execution of the generic Evolution, human-disposition, snapshot,
  and later-run load mechanism when separately authorised, without treating it
  as formal Experiment 2 evidence or an improvement/causal-effect claim;
- independent Direct-MuJoCo validation, first-attempt and post-Repair outcomes, failure patterns,
  and resource use.
- bounded usability of the deployed model and endpoint in Experiment 1b/B2, using the fixed ReCAP
  controller, validated capability stack, canonical resets, and trusted Harness on the declared
  ten robot-task blocks.
- Experiment 3's per-cell TGCD/IVC, driver-validation, Repair, Task Demo, failure, resource, and
  evidence-completeness outcomes for the exact eleven-configuration cohort, with morphology used
  only as a descriptive package label.

The current formal evidence does **not** establish real-SDK fidelity, hardware validity,
sim-to-real transfer, visual perception, production reliability, universal model or robot
superiority, capability-interface-use performance beyond the bounded Experiment 1b/B2 design,
Task Demo completion for tasks outside the protocol-selected set,
autonomous or same-run self-improvement, any formal Experiment 2 or Experience-effect claim,
or a morphology effect from Experiment 3. A capability-level pass criterion
is not established as reliable merely because the model wrote it or the compiler accepted its
syntax. A diagnostic Evolution proposal is future-run material only and is not evidence of
later-run improvement.

**中文辅助说明。** 项目只能提出对应实验直接证据支持的结论：对每台机器人，在全部 backbone、
replicate 与生成条件间固定 Auto-Adapter 配置、机器人输入、有来源 Task Library、robot capability
interface、capability-level pass standard、完整私有 validation suite、评估协议和资源预算后，不同
LLM backbone 在 skeleton-assisted 与 from-scratch 条件中的 robot-specific driver 合成结果；
模型根据至少二十项有来源任务完成的 task-grounded capability 设计；由模型设计、具有来源依据且由实现
不可见 IVC 独立审计和编译，并由 Harness 在已声明校准和 false-success 检查下评估的
capability-level pass criteria，但它属于独立 RQ2 证据而不是 B1 中变化的输入；另行授权时可以诊断
通用 Evolution、人工 disposition、snapshot 与 later-run load 机制，但不能当作 formal Experiment 2
证据，也不支持 improvement 或因果 effect；独立 Direct-MuJoCo
验证、首次与 Repair 后结果、失败模式和资源使用。当前三个正式实验不证明真实 SDK 保真度、硬件
有效性、sim-to-real 迁移、视觉感知、生产可靠性、模型或机器人的普遍优越性、下游 capability-interface-use
表现、协议选定集合之外任务的 Task Demo 完成结果、自主或本轮 self-improvement、任何 formal
Experiment 2/Experience effect claim，或 morphology
effect。Experiment 3 的 morphology 只作描述性 package label。模型写出 criterion 或 compiler 接受其
语法，本身都不足以证明 criterion 可靠。诊断 Evolution proposal 只属于未来 run material，不证明
后续运行得到改善。

### 1.3 Declared construction cohort and Experiment 3 boundary / 声明建设 cohort 与 Experiment 3 边界

For this Authority, **all robots** means the following exact eleven-configuration cohort. It does
not mean every robot that exists, every asset in the repository, or a dynamically changing index.

| Morphology category | Robot configuration | Required public Task Library snapshot |
|---|---|---|
| Fixed serial arm | `robotstudio_so101` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Quadruped | `unitree-go2-stock-12dof` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `franka_panda` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `kinova_gen3_robotiq_2f85` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `ufactory_xarm7` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `universal_robots_ur5e_robotiq_2f85` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `piper` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Fixed serial arm | `kuka_iiwa_14` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Dexterous hand | `leap_hand` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Mobile manipulator | `hello_robot_stretch_2` | 20 or more distinct, applicable, source-backed tasks and pass standards |
| Bimanual manipulator | `aloha_2` | 20 or more distinct, applicable, source-backed tasks and pass standards |

`unitree_g1` and `google_barkour_vb` remain optional research backups outside this exact formal
cohort. Their retained assets, tasks, skeletons, policies, or calibration results do not block the
formal Experiment 3 cohort and do not count as cohort evidence.

Every configuration uses its complete local MJCF closure with `mujoco==3.3.6`. Its exact model,
assets, admitted task records and source standards, private instances, resets, measurement adapters
and guards, trusted skeleton family, from-scratch contract, and calibration reference are owned by
one versioned mainline package. An asset-only package, a five-task package, a package with
pre-authored effects, non-colliding physical task fixtures, a reset deeper than the 5 mm penetration
limit, or a Harness path that cannot reject dynamic penetration is incomplete. For environments
whose reset actuator controls hold position, the focused 100-step passive-settling check must also
remain within the same limit. Torque-driven robots need not remain standing without an active
policy, but their initial reset must be valid and any later fall or penetration remains a physical
failure under the shared Harness.

This section is the construction and calibration boundary for the mainline. It does not impose a
second generation condition, a Ministral round, or Evolution on the declared cohort. Experiment 3's
formal manifest must contain this complete cohort. Before Experiment 3, every listed configuration
must pass its package and simulator-integrity checks and must appear in `libraries/robots/index.json`.
A missing or incomplete configuration remains a visible blocker; it may not be silently omitted,
replaced, or treated as a failed model cell. Adding or removing a configuration from Experiment 3
requires an explicit project-Authority revision made before outcomes are inspected.

Per-robot package checks, hidden reference diagnostics, and real-model canaries may execute as soon
as that package is ready. They are diagnostic construction/calibration evidence and do not satisfy
the formal Experiment 3 cohort. Reference source or derived logic is never model input. This complete
cohort governs Experiment 3's construction run, not every downstream experiment. A delegated
experiment authority may prospectively select a fixed subset without changing the mainline package
inventory or claiming an all-robot result. Experiment 3 contains the eleven robots crossed with
`skeleton-assisted` only and `r01`--`r03`, for exactly thirty-three cells. It has no Ministral
shakedown, from-scratch counterpart, Evolution, or Experience-disposition requirement.

**中文辅助说明。** 在本 Authority 中，**全部机器人**特指上表声明的十一个精确配置，不表示
世界上所有机器人、仓库内每个 asset，也不是运行时动态变化的集合。每个配置使用自己的完整本地
MJCF closure 和 `mujoco==3.3.6`；其精确模型、资产、已准入任务与来源标准、私有 instance、
reset、measurement adapter、guard、可信 skeleton family、from-scratch contract 和 calibration
reference 均归一个版本化主线 package 所有。只有 asset、只有五项任务、含预写 effect，或没有
真实碰撞 fixture、reset 穿透深于 5 mm，或 Harness 无法拒绝运行中穿透的 package 都不完整。
由 position control 保持 reset 的环境还必须在聚焦的 100-step 被动沉降检查中维持同一边界。
torque-driven 机器人无需在缺少主动 policy 时保持站立，但其初始 reset 必须有效；之后的倒地或
穿透仍由共享 Harness 判为 physical failure。

`unitree_g1` 与 `google_barkour_vb` 作为可选研究备份保留，不属于上述正式 cohort；它们不阻塞
正式 Experiment 3 cohort，其资产、task、skeleton、policy 或校准结果也不计作 cohort 证据。

本节是主线的建设与校准边界，不额外要求第二种生成条件、Ministral round 或 Evolution。Experiment 3
的正式 manifest 必须包含完整十一配置集合。Experiment 3 开始前，每个配置都必须通过 package check
和 simulator-integrity check，并进入 `libraries/robots/index.json`。缺失或不完整配置是显式 blocker，
不能静默省略、替换，也不能算作模型失败 cell；从 Experiment 3 增删配置必须在查看结果前通过项目
Authority 修订。单机器人 package check、隐藏 reference 诊断和真实模型 canary 可在对应 package 就绪
后提前执行，但只属于建设/校准诊断证据，不能替代正式 Experiment 3 cohort；reference 源码或衍生逻辑
绝不成为模型输入。完整十一配置只约束
Experiment 3 的建设 run，不自动约束每个后续实验。经第 0.1 节委派的实验权威可以在查看结果前固定一个
子集，但不能借此改变主线 package inventory 或声称全机器人结果。Experiment 3 只使用
skeleton-assisted，每个 replicate 为十一配置，即 `r01`--`r03` 共三十三个 cell；没有 Ministral
shakedown、from-scratch、Evolution 或 Experience disposition 要求。

### 1.4 Active Chapter 3 LLM coverage / 当前 Chapter 3 LLM 覆盖

The active Chapter 3 Producer-backbone set contains seven model families (`M = 7`):

| ID | Model family | Vendor | Protocol status |
|---|---|---|---|
| `M1` | Sonnet 4.6 | Anthropic | Exact deployment pin in the active experiment manifests |
| `M2` | Opus 5 | Anthropic | Exact deployment pin in the active experiment manifests |
| `M3` | Haiku 4.5 | Anthropic | Exact deployment pin in the active experiment manifests |
| `M4` | Nova Pro | Amazon | Exact deployment pin in the active experiment manifests |
| `M5` | DeepSeek V4 Pro | DeepSeek | Exact deployment pin in the active experiment manifests |
| `M6` | Ministral 3 8B | Mistral | Exact deployment pin in the active experiment manifests |
| `M8` | GPT-5.6 Sol | OpenAI | Returned identity `openai.gpt-5.6-sol`; public limits and dated reference price pinned |

`M7`/Qwen3 32B is an inactive historical configuration and its ID is not reused in the active
Chapter 3 matrices. M8 is the only active model assigned the new M8 ID; the exact returned identity
is fixed by the experiment manifests for B1 and B2.

Experiment 3 uses Sonnet 4.6 only, not the historical M6 Ministral shakedown. Its exact provider
identifier, revision, endpoint, inference settings, context limit, and price snapshot must be pinned
before the first call. The experiment starts each of its thirty-three cells with empty Experience,
uses only skeleton-assisted generation, and stops after Task Demo; Evolution is disabled. Experiment
3 is not a seven-family B1 comparison.

Inclusion defines active Chapter 3/RQ1 scope. The versioned B1 and B2 manifests
pin the exact provider routes, returned identities, inference settings,
context/output limits, and dated prices used for formal accounting. For M8,
the public OpenAI limit and price pin is explicitly a reference estimate; its
organization-gateway upstream revision and actual billing remain independently
unverified. An unavailable family must remain a visible infrastructure terminal
and may not be silently replaced or removed after outcomes are inspected.

**中文辅助说明。** Chapter 3/RQ1 当前 active Producer backbone 集合包含上述七个模型 family（`M = 7`）。
其中 M7/Qwen3 32B 是 inactive historical configuration，不得复用 M7 编号；M8 只表示已通过公司
gateway 验证并返回 `openai.gpt-5.6-sol` 的 GPT-5.6 Sol。
纳入表格表示 Chapter 3/RQ1 的 active 范围；B1/B2 的版本化 manifest 固定正式核算所用的 provider
route、返回 identity、inference 配置、context/output limit 和价格。M8 的 OpenAI 公开 limit 与价格
只作为参考估算，其 organization-gateway upstream revision 与实际账单无法独立验证。Experiment 3 只使用 Sonnet 4.6，并非历史 M6 Ministral shakedown；
每个 cell 空 Experience 开始，仅 skeleton-assisted，Task Demo 后停止，Evolution 禁用。准确 provider
identifier、revision、endpoint、inference 配置、context limit 和价格快照须在首次调用前固定；不可用
provider 保持显式 blocker，不能静默替换。

### 1.5 Research questions / 研究问题

The project evaluates three controlled research questions:

1. **RQ1---Fixed-input comparison of LLM backbones in robot-specific driver synthesis.**

   With the Auto-Adapter framework, robot inputs, one prior-designed robot
   capability interface, capability-level pass standards, complete private validation suite,
   Harness measurement and verdict rules, and resource budget held fixed per robot, how do LLM
   backbones differ in synthesising a robot-specific driver under the skeleton-assisted and
   from-scratch generation conditions?

   Chapter 3/RQ1 is operationalised by two bounded delegated subexperiments. Experiment 1a/B1
   answers this fixed-input driver-synthesis question. Experiment 1b/B2 holds the validated
   capability stack and ReCAP controller fixed and measures the usability of the deployed model
   and endpoint across the same two robots and five tasks per robot; it is a separate RQ1 result,
   not an Experiment 2 or RQ2 result.

2. **RQ2—Framework construction and cross-run closure.** Can Auto-Adapter transform source-backed robot tasks into a validated robot-specific driver through task-grounded capability design, implementation-blind validation, and bounded Repair, and can its terminal Evolution stage produce reviewed Experience that is demonstrably consumed by a later generation run?

3. **RQ3—Cross-configuration evaluation.** With Sonnet 4.6, the skeleton-assisted generation condition, Framework version, resource budget, and evidence protocol held fixed, how do end-to-end synthesis, validation, Repair, and Task Demo outcomes vary across the eleven declared Direct-MuJoCo robot configurations?

Under revision `0.20.0`, the framework-construction portion of RQ2 is active.
Its formal Experiment 2 operationalisation is inactive and historical; only
the non-formal DeepSeek canary may exercise the cross-run machinery within the
separate user-approval boundary in Section 5.2.

For RQ1 driver synthesis, each robot has one prior-designed and fixed B1 Driver-and-criteria definition:
the public capability interface and capability-level pass standards plus the complete private task
instances, measurement bindings, guards, and Harness verdict rules. The versioned experiment
manifest freezes that bundle before any B1 outcome is inspected. It remains identical across all
backbones, replicates, and generation conditions for that robot. Capability design and criteria are
therefore fixed B1 inputs, not B1 model outputs. B1 begins at STUDY; the two generation conditions
differ only in their authorised access to the trusted skeleton.

B1 primary reporting contains both driver-validation and resource outcomes:

| Primary result family | Required measures |
|---|---|
| Driver validation | Initial `pass@0`, final pass within the maximum three frozen Driver attempts, Repair gain, attempts to first pass, and cell completion |
| Model use | Model-call count plus provider-reported input, output, cached, and reasoning tokens where available |
| Monetary cost | Actual billed model cost when exposed by the provider; otherwise an estimate using the manifest-pinned currency, unit-price snapshot, and token categories |
| Time | Wall time to the attempt-0 validation verdict and to the terminal capability-validation verdict, with model-service, development/probe, and MuJoCo-validation time reported separately where measurable |

The B1 resource ledger counts every applicable model call in STUDY, GENERATE/GEN_ALGO, and Repair,
including failed calls and failed cells. Upstream TGCD, IVC, and fixed-input review resources are
reported with the experiment that produced the B1 inputs and are not charged to any B1 backbone.
Optional reference diagnostics are not Experiment 1 inputs or start gates. Task Demo resources
remain separate from B1 driver-synthesis resources.

Reference drivers are positive controls for the Framework and Direct-MuJoCo execution route. They
are not B1 model conditions and cannot be counted as evidence of model-based robot-specific driver
synthesis. A reviewed reference driver supports Experiment 1b/B2 only under
the interface-binding, validation, isolation, and controller-execution requirements in `AA2-B2`
and active Sections 0.1.1, 3.0, and 4;
its calibration result alone is not capability-interface-use evidence. B2 is the bounded Chapter
3/RQ1 use subexperiment and is not Experiment 2 or RQ2. No formal Experiment 2 report or dispatch is
authorised under revision `0.20.0`.

**中文辅助说明。** 项目评估三个受控研究问题：

1. **RQ1——LLM backbone 在固定输入 robot-specific driver synthesis 中的比较。**
   对每台机器人固定 Auto-Adapter framework、机器人输入、一套由前序实验
   设计的 robot capability interface、capability-level pass standard、完整私有 validation suite、
   Harness 测量与判定规则以及资源预算时，不同 LLM backbone 在 skeleton-assisted 与
   from-scratch 条件下合成 robot-specific driver 的能力有何差异？
   Chapter 3/RQ1 由两个有界委派子实验具体实现：Experiment 1a/B1 回答上述固定输入的
   driver-synthesis 问题；Experiment 1b/B2 固定已验证 capability stack 和 ReCAP controller，
   在相同两台机器人及每台五项 task 上测量已部署 model 和 endpoint 的可用性。B2 是独立的
   RQ1 结果，不是 Experiment 2 或 RQ2 结果。
2. **RQ2——Framework construction 与 cross-run closure。** Auto-Adapter 能否通过 task-grounded
   capability design、implementation-blind validation 和 bounded Repair，把有来源 robot task 转化为
   validated robot-specific driver；其 terminal Evolution stage 能否产生 reviewed Experience，并被
   later generation run 明确消费？
3. **RQ3——Cross-configuration evaluation。** 在固定 Sonnet 4.6、skeleton-assisted generation condition、
   Framework version、resource budget 和 evidence protocol 时，十一个声明的 Direct-MuJoCo robot
   configuration 之间的 end-to-end synthesis、validation、Repair 和 Task Demo outcome 如何变化？

修订 `0.20.0` 只激活 RQ2 的 Framework construction 部分；formal Experiment 2 具体化属于历史且
不生效，只有第 5.2 节的非正式 DeepSeek canary 可在用户另行批准边界内测试 cross-run machinery。

在 RQ1 driver synthesis 中，每台机器人只有一套由前序实验设计并固定的 B1 Driver 与 criteria
定义：公开 capability interface 与 capability-level pass standard，以及完整私有 task instance、
measurement binding、guard 和 Harness 判定规则。版本化 experiment manifest 必须在查看任何 B1
outcome 前封存该 bundle，并在该机器人的全部 backbone、replicate 和生成条件间保持完全一致。
因此 capability design 与 criteria 是固定 B1 输入，不是 B1 模型输出。B1 从 STUDY 开始；两种生成
条件只在是否获准访问 trusted skeleton 这一点上不同。B1 主要结果同时包含 driver validation 与
资源结果：首次 `pass@0`、最多三个 frozen Driver 预算内的最终通过、Repair gain、首次通过所需 attempt
和 cell completion；全部适用模型调用及 provider 可提供的 input/output/cached/reasoning token；
实际账单费用或按 manifest 固定价格快照估算的模型费用；以及到 attempt-0 verdict 和最终 terminal
verdict 的 wall time，并在可测量时分别报告 model service、development/probe 和 MuJoCo validation
时间。B1 resource ledger 只计 STUDY、GENERATE/GEN_ALGO 和 Repair 的适用模型调用；上游 TGCD、
IVC 和 fixed-input review 资源归产生 B1 输入的前序实验，不计入任何 B1 backbone。可选 reference
诊断不是 Experiment 1 输入或开跑 gate。Task Demo 资源与 B1 driver synthesis 分开报告。
Reference driver 是 Framework 和 Direct-MuJoCo 执行路径的正向对照，不是 B1 模型条件，不能
计为 model-based robot-specific driver synthesis 的证据。经审查的 reference driver 只有在满足
`AA2-B2` 及第 0.1.1、3.0、4 节的 interface 绑定、validation、隔离和 controller 执行要求后，才可支持另行声明的
capability-interface-use 扩展；单独的 reference calibration 结果不是 capability-interface-use 证据。
该 B2 子实验不属于 Experiment 2 或 RQ2。RQ3 只报告 Experiment 3 的逐 cell 与描述性 cohort
outcome；不进行 Experience effect、morphology effect 或 matched later-run comparison。

---

## 2. Repository and dependency boundary / 仓库与依赖边界

### 2.1 Target repository layout / 目标仓库结构

```text
autoadapter/                 # sole canonical Direct-MuJoCo mainline / 唯一 canonical 主线
  pyproject.toml             # complete declared third-party runtime dependencies / 完整声明依赖
  .python-version
  README.md
  configs/
    experiments/             # run selection and budgets only / 仅运行选择与预算
      mainline.json           # complete declared cohort / 完整声明 cohort
      canaries/
        <robot_configuration_id>.json
  src/
    autoadapter2/
      capability_design/
      validation_compiler/
      driver_synthesis/
      harness/
  libraries/
    robots/
      index.json             # only complete runnable robot packages / 仅完整可运行 package
    experience/
  research/
    robots/
      index.json             # non-runtime future/build list / 非运行时未来建设列表
  evidence/                  # concise tracked run summaries / 精简的版本化运行摘要
  tests/
  runs/                      # ignored raw run evidence / 被忽略的原始运行证据

demo2/                       # preserved policy-constrained historical baseline / 保留的旧基线

extensions/
  sdk/                        # real-SDK + Translation experimental extension / 实验扩展线
```

`autoadapter/` is already the sole canonical implementation root for this Authority. `demo3/` is a
superseded migration source and must not receive new mainline behavior or remain a second mainline.
`demo2/` remains a preserved policy-constrained historical baseline and must not be incrementally
transformed into the new design. The SDK-grounded material in `general_demo/` is historical input
to `extensions/sdk/`, never a canonical mainline.

`research/robots/index.json` is a non-runtime planning list and does not admit a robot. Only
complete packages may appear in `libraries/robots/index.json`. A diagnostic canary configuration
may select one runnable package, but the formal `configs/experiments/mainline.json` must select the
complete Section 1.3 cohort and its run budgets. Experiment configuration does not contain robot
implementation, private validation definitions, credentials, or SDK configuration.

**中文辅助说明。** `autoadapter/` 已经是本 Authority 唯一 canonical 实现根目录。`demo3/` 是
已经失效的迁移来源，不得继续接收主线行为，也不得作为第二条主线。`demo2/` 保留为受 policy
约束的历史基线，不得通过持续改造变成新设计。`general_demo/` 中基于 SDK 的材料只是
`extensions/sdk/` 的历史输入，绝不是 canonical 主线。

`research/robots/index.json` 是运行时不会读取的规划列表，不构成机器人准入。只有完整 package
可以进入 `libraries/robots/index.json`。诊断 canary 可以选择一个 runnable package；正式
`configs/experiments/mainline.json` 必须选择第 1.3 节完整 cohort 及其运行预算。实验配置中不得
放置机器人实现、私有 validation 定义、凭据或 SDK 配置。

### 2.2 Dependency direction / 依赖方向

The canonical mainline must be installable, testable, and runnable without importing, reading,
executing, or resolving files from `demo2/`, `demo3/`, `general_demo/`, `extensions/`, `thesis/`, a temporary
audit checkout, a user home path, or any other repository-external or sibling source tree. It must
not use an absolute developer-machine path, an escaping symlink, Git submodule content, or a
runtime network download to obtain code, prompts, task data, standards-derived scoring records,
MJCF, meshes, textures, skeletons, reference drivers, or AutoAdapter 1.0 orchestration.

All project-authored imports and file resolutions must remain under `autoadapter/`. In particular,
the mainline must vendor the minimum required AutoAdapter 1.0 STUDY/GENERATE and from-scratch routes
and every declared cohort robot's exact asset closure. Source URLs in `sources.json` are citations, not
runtime dependencies; the structured task and scoring content required for a run is stored locally.

Literal zero-dependency execution is not the project contract. The mainline may depend only on the small
third-party runtime set declared in `autoadapter/pyproject.toml`, including Python, MuJoCo, numerical and
video support, plus the configured real-model provider/service needed for model-authored phases.
The environment is installed before a formal run. During a formal run, network access is allowed
only to the configured model service from the Framework model adapter; candidate execution,
MuJoCo evaluation, IVC compilation, and Harness verdicting do not fetch remote content.

The SDK extension may later consume a small, explicit canonical-mainline contract, but the
dependency must never point back from the mainline into the extension.

The default formal command resolves the complete declared cohort. Focused tests and diagnostic
canaries may select one robot explicitly, but their output cannot be reported as formal all-robot
evidence. SDK tests use explicit extension commands. The two existing Tasks and Morphology Library
families must not be merged by matching file paths: conflicting records remain in their owning
mainline or extension namespace until a later evidence-backed deduplication is justified.

**中文辅助说明。** canonical 主线必须能够在不导入、读取、执行或解析 `demo2/`、`demo3/`、
`general_demo/`、`extensions/`、`thesis/`、临时 audit checkout、用户 home 路径或其他仓库外/
同级源码树文件的情况下安装、测试和运行。不得通过开发者机器绝对路径、逃逸 symlink、Git
submodule 或运行时网络下载获得代码、prompt、task 数据、来源标准评分记录、MJCF、mesh、texture、
skeleton、reference driver 或 AutoAdapter 1.0 orchestration。

全部项目自编 import 和文件解析都必须停留在 `autoadapter/` 下。主线必须内置最小所需的 AutoAdapter
1.0 STUDY/GENERATE 与 from-scratch 路径，以及每个声明 cohort 机器人的准确完整 asset closure。
`sources.json` 中的 URL 只是 citation，不是运行依赖；运行所需的结构化任务和评分内容必须本地
保存。

项目不声称可以脱离所有第三方 runtime 执行。主线只能依赖 `autoadapter/pyproject.toml` 明确声明的
小型第三方运行集合，包括 Python、MuJoCo、数值和视频支持，以及模型创作阶段所需的已配置真实
model provider/service。正式 run 前完成环境安装；正式 run 中只有 Framework model adapter 可以
访问已配置模型服务，candidate 执行、MuJoCo evaluation、IVC compilation 和 Harness verdict
不得下载远程内容。SDK 扩展以后可以消费一个小型 canonical-mainline contract，但依赖方向绝不
能从主线指向扩展。

默认正式命令解析完整声明 cohort。聚焦测试和诊断 canary 可以显式选择一个机器人，但其输出不能
报告为正式全机器人证据；SDK 测试使用明确的扩展命令。现有两套 Tasks 与 Morphology Library
不能依据相同文件路径直接合并；存在冲突的记录继续保留在
各自所属的 mainline 或 extension namespace 中，直到后续证据证明去重合理。

### 2.3 Mainline inputs / 主线输入

One robot run resolves a coherent package containing:

- one robot/configuration identity;
- a complete MJCF entrypoint and asset closure;
- public Morphology and control facts;
- one frozen, admitted Task Library snapshot with at least twenty applicable source-backed tasks;
- each task's public source lineage and machine-expressible pass standard;
- Framework-private concrete instances, resets, execution and measurement bindings, and guards;
- a trusted skeleton family for the skeleton-assisted condition;
- the preserved from-scratch generation contract and permitted MuJoCo/NumPy/Python primitives;
- optional reviewed Experience; and
- a reference driver used for calibration and, only when a separately declared
  capability-interface-use extension manifest explicitly selects it under the `AA2-B2` scoped Authority, as that
  extension's fixed benchmark driver.

The package must resolve without an SDK Entry, no-SDK placeholder, Translation Layer, integration
manifest, or SDK Readiness gate. Missing or inconsistent required files stop the run as an input or
infrastructure failure, not a model failure.

**中文辅助说明。** 单次机器人运行需要解析出一个一致的软件包，其中包含：一个机器人/配置
身份；完整的 MJCF 入口及资产闭包；公开的 Morphology 与控制事实；一份已封存、已准入且至少
包含二十项适用来源任务的 Task Library 快照；每项任务公开的来源 lineage 和机器可表达通过
标准；Framework 私有的具体实例、reset、执行与测量 binding 和 guard；供 skeleton-assisted
条件使用的可信 skeleton family；保留的 from-scratch 生成合同及获准的 MuJoCo/NumPy/Python
primitives；可选且经过审阅的 Experience；以及用于校准、并且只有在另行声明的
capability-interface-use extension manifest 按 `AA2-B2` scoped Authority 明确选择时，才可作为该扩展固定 benchmark
driver 的 reference driver。

该软件包必须在没有 SDK Entry、no-SDK placeholder、Translation Layer、integration manifest
或 SDK Readiness gate 的情况下完成解析。缺失或不一致的必需文件应使运行以输入错误或基础设施
错误停止，不能归因于模型失败。

### 2.4 Source-backed Task Library / 有来源依据的任务库

Every robot listed in the mainline runnable robot index must have one complete frozen Task
Library snapshot containing at least twenty distinct tasks that are common and physically
applicable to that exact robot configuration. This rule applies to every declared cohort robot and
every later Authority-approved addition without exception. A task is admitted only when a human reviewer confirms all of the
following:

- at least one traceable primary benchmark or industrial-production standard source, recorded with
  exact title, publisher or owning organization, version or date, stable locator, and the specific
  section, table, protocol, or evaluation definition used;
- the source task or operation being adapted, the robot-applicability rationale, and any adaptation
  from the source to the selected MuJoCo configuration;
- a public task description and measurable pass standard, including metric, unit, comparator,
  threshold or allowed range, temporal requirement when applicable, and aggregation rule; and
- a source reference on every scoring clause showing which benchmark or standard supports that
  metric, comparator, threshold/range, temporal requirement, and aggregation; and
- enough scene and observation assumptions for the independent compiler to bind a private physical
  instance without changing the task's meaning; and
- a public Task Demo invocation schema, including the exact task-specific
  `task_parameters` fields, types, units, and frames that ReCAP and the trusted task compiler may
  consume. TGCD uses their semantics to design task-neutral capability request schemas; candidate
  Drivers never receive the task envelope or `task_id`.

A citation attached only to the task title does not establish scoring provenance. Numeric bounds,
dwell requirements, success rates, weights, and aggregation rules must each be directly supported
or have an explicit, reviewable adaptation from a cited source. Unit/frame conversion and
robot-specific parameter substitution are permitted only when the transformation is recorded and
does not weaken the source obligation. A Framework-invented score or threshold cannot enter a
formal Task Library under a benchmark or industrial-standard label.

Near-duplicate wording, unsupported source claims, and multiple parameterizations of the same
operation do not count as distinct tasks solely to reach twenty. The formal snapshot is fixed before
TGCD starts and remains identical across the two generation conditions for that robot/model
replicate.

The admitted task IDs, descriptions, source lineage, and pass standards are TGCD inputs. Concrete
evaluation instances, scene perturbations, seeds, exact reset state, simulator symbol bindings,
measurement implementation, anti-false-pass guards, and executable suite remain Framework-private.
The formal dynamic path must not provide a pre-authored effect catalog, capability contract catalog,
or task-to-effect allowlist to TGCD. A robot with fewer than twenty admitted tasks, an incomplete
scoring clause, or unresolved source lineage may be listed only in the non-runtime
`research/robots/index.json` build list and must fail closed if selected for a formal run.

The minimum admitted robot package layout is:

```text
autoadapter/libraries/robots/<robot_configuration_id>/<package_version>/
  morphology.json
  tasks/
    sources.json             # exact benchmark and industrial-standard records
    catalog.json             # 20+ tasks; every scoring clause references sources.json
    private/
      instances.json
      bindings.json
      guards.json
  assets/                    # complete local MJCF closure
  skeleton/                  # skeleton-assisted condition only
  reference/                 # calibration; optional fixed driver for a separately declared use extension
```

`sources.json` and the public scoring clauses are TGCD-visible. Files under `tasks/private/` remain
IVC/Harness-only. Directory presence alone does not admit a package; the loader must validate the
complete source and scoring contract before adding it to the runnable index.

**中文辅助说明。** 主线 runnable robot index 中的每个机器人都必须具有一份完整、
已封存的 Task Library 快照，至少包含二十项彼此不同、常见且在该精确机器人配置上物理适用的
任务；该规则同样适用于每个声明 cohort 机器人及以后由 Authority 批准加入的机器人，没有例外。只有经过人工审查并确认
以下内容，任务才能准入：具有可追溯的 primary benchmark 或工业生产标准来源，并记录精确标题、
发布者/所有组织、版本/日期、稳定定位及所使用的具体 section、table、protocol 或 evaluation
definition；说明所采用的来源任务或操作、机器人适用理由及向所选 MuJoCo 配置的改编；提供公开
任务描述和可测量通过标准，包括 metric、unit、comparator、threshold/允许范围、适用时的时间
要求及 aggregation；每条评分 clause 都标明支持其 metric、comparator、threshold/range、时间
要求和 aggregation 的来源；提供足够的 scene 与 observation 假设，使独立编译器能够绑定
私有物理实例而不改变任务含义；同时公开 Task Demo 调用信封的 schema，包括 ReCAP 与可信 task
compiler 可读取的 task-specific `task_parameters` 字段、类型、unit 和 frame。TGCD 用其语义设计
task-neutral capability request；候选 Driver 不接收 task envelope 或 `task_id`。

仅在任务标题上挂一个 citation 不能证明评分来源。数值 bound、dwell、成功率、权重和 aggregation
rule 必须得到来源直接支持，或具有明确、可审查的来源改编。只有在记录转换且不弱化来源义务时，
才允许 unit/frame 转换和机器人专用参数替换。Framework 自行发明的 score 或 threshold 不能以
benchmark 或工业标准名义进入正式 Task Library。

不得仅靠近似重复措辞、无依据的来源声明或同一操作的多个参数版本凑足二十项。正式快照必须在
TGCD 前固定，并在同一 robot/model replicate 的两种生成条件间保持一致。TGCD 可以读取
已准入 task ID、描述、来源 lineage 和通过标准；具体评估实例、scene perturbation、seed、精确
reset 状态、simulator symbol binding、measurement 实现、anti-false-pass guard 和可执行 suite
保持 Framework 私有。正式动态路径不得向 TGCD 提供预写 effect catalog、capability contract
catalog 或 task→effect allowlist。少于二十项已准入任务、存在不完整评分 clause 或来源 lineage
未解决的机器人，只能列在运行时不会读取的 `research/robots/index.json` 建设列表中；一旦被正式
run 选择，必须 fail closed。

每个已准入机器人 package 至少按上述结构提供 `morphology.json`、`tasks/sources.json`、包含
二十项以上任务且逐评分 clause 引用来源的 `tasks/catalog.json`、私有 instances/bindings/guards、
完整本地 MJCF closure、仅供 skeleton-assisted 条件使用的 skeleton，以及用于校准、并可被另行声明
的 capability-interface-use 扩展选择为固定 benchmark driver 的 reference。
`sources.json` 和公开评分 clause 对 TGCD 可见，`tasks/private/` 下文件仅供 IVC/Harness 使用。
只有目录存在并不构成准入；loader 必须在加入 runnable index 前核验完整来源和评分合同。

---

## 3. Mainline end-to-end contract / 主线端到端合同

### 3.0 Revision 0.20 active workflow / 修订 0.20 当前工作流

The following revision-0.20 contract supersedes every inconsistent earlier
statement remaining in Sections 3.1--3.7, including the former three-turn
STUDY, submit/check tools, pre-STUDY TGCD ordering, package `primitive_family`,
exactly-one task coverage, primary/robustness case vocabulary, and the rule
that ReCAP may run only after a whole-suite pass:

```text
public package + eligible prior-run Experience
  -> STUDY / study.json
  -> TGCD / capability_design.json
  -> implementation-blind IVC
       + private reference positive control in formal runs
       (explicitly skippable only when formal=false)
       / capability_validation_suite.json
  -> Generate / frozen driver.py / trusted capability Harness
  -> up to two in-place Repairs, never more than three frozen Drivers total
  -> ReCAP with the nominal+boundary-pass capability whitelist
       / trusted Task Demo Harness verdict and videos
  -> at most one Evolution call when the selected protocol enables it
```

Each model-authored phase uses one isolated file workspace and persistent,
credential-free Python/MuJoCo session. `read_file`, `write_file`, and
`execute_python` are the common tools. Only skeleton-assisted Generate/Repair
also receives `list_skeletons` and `inspect_skeleton`; ReCAP instead receives
the dynamically derived capability tools and `finish`. Multiple tool calls in
one model turn are allowed, errors return to the same conversation, and there
is no phase-wide tool-call ceiling. Individual paths, permissions, execution
time, MuJoCo steps/simulated time, and output size remain bounded.

Persistent Python state is promised only across successful calls. A timed-out,
exited, or protocol-corrupt worker is killed and its failed call is never
automatically replayed. The next explicit `execute_python` call may start a
clean credential-free worker; phase call accounting is preserved and unknown
native step use conservatively exhausts the remaining phase physics-step
budget, so restart cannot enlarge physical execution authority. The clean
worker may still perform pure-Python and Driver import/build checks.

STUDY completes before TGCD. The canonical artifact is the delivery: normal
model completion triggers Framework audit, and an invalid artifact returns a
bounded correction message while turns remain. The last two turns expose only
`write_file`: the penultimate delivery is audited with one final correction
opportunity, and a valid final-turn artifact is accepted without a separate
closing message. TGCD and IVC each have six turns;
STUDY has 16 for either condition; skeleton Generate/Repair have 22/22 and
from-scratch Generate/Repair have 40/20. A Driver attempt begins only after the
current `driver.py` passes source and import checks and is copied to an
immutable attempt snapshot. The private Harness never runs a missing, invalid,
stub, or non-importable revision.

Capability Validation reports each capability separately. A capability enters
the ReCAP whitelist only when both its nominal and calibrated-boundary case
pass. This permits truthful partial diagnostics without converting a partial
pass into whole-Driver validation success. ReCAP has at most 16 planning turns
and 12 capability calls per task; the trusted Task Demo Harness, not controller
completion text, owns the task verdict. Experience is visible only in a later
run's STUDY, TGCD, Generate, and Repair. It is absent from IVC, reference
positive control, both Harness paths, and ReCAP.

Experiment 1a is the fixed-input exception: it begins at STUDY and loads the
manifest-pinned Design and suite without rerunning TGCD/IVC. Experiment 3 uses
the full per-cell route above and stops after ReCAP, with empty Experience and
Evolution disabled. Experiment 1b remains completed historical evidence and is
not rerun. Retained Experiment 2 material is outside this round and is not a
Framework acceptance gate.

**中文辅助说明。** 本节的修订 0.20 合同优先于第 3.1--3.7 节内仍保留的冲突旧语句。
主线顺序固定为 STUDY→TGCD→IVC→Generate/Repair→Capability Validation→ReCAP→按配置启用的
一次 Evolution。文件即交付；最后两个 turns 只暴露 `write_file`，倒数第二回合审核后保留一次修正，
最后一回合写出的有效文件也接受。持久 Python state 只保证跨成功 calls 保留；timeout/worker loss
不自动重放，下一次显式调用可启动干净 worker，且调用计数不清零、未知物理步数按预算耗尽处理。
只有源码/import 合格并复制为
不可变 attempt snapshot 的 `driver.py` 才计一次正式 attempt。ReCAP 只获得 nominal 与 boundary
同时通过的 capability 白名单；partial pass 只能作为真实诊断，不能包装成整套 Driver pass。
Experience 只进入后续 run 的 STUDY/TGCD/Generate/Repair。本轮 Exp1a 只做固定输入准备，Exp3
只做 33-cell 准备，Exp1b 不重跑，Exp2 不作为验收 gate。

> **Historical-only block / 仅历史文本：** Everything from the
> pre-0.20 diagram below through the end of Section 3.7 is retained only to
> interpret historical records. It is non-normative under revision `0.20.0`;
> none of its old ordering, tool, capability-count, coverage, case-role,
> attempt, admission, Task Demo, Experiment 2, or Evolution-schema rules
> applies to the current mainline. The active contract is Sections 0.1.1 and
> 3.0 together with Section 4. / 从下方旧图直到第 3.7 节结束的全部内容只用于解释历史记录，
> 在本修订下不具规范效力；其中旧 ordering、tool、数量、coverage、case-role、attempt、
> admission、Task Demo、Experiment 2 与 Evolution schema 均不约束当前主线。当前合同以
> 第 0.1.1、3.0 与第 4 节为准。

The following pre-0.20 diagram is retained only to explain historical records;
it is non-normative wherever it differs from Section 3.0:

```text
Morphology + >=20 sourced Tasks/pass standards
       + protocol-eligible Experience (empty for Exp2 source and every Exp3 cell)
                              |
                              v
 Task-Grounded Capability Design (5-10 capability contracts)
                              |
                              v
             Independent Validation Compiler
                              |
                              v
 capability_design.json + complete capability_validation_suite.json
                       |
          +------------+------------+
          |                         |
          v                         v
 complete MJCF + skeleton     complete MJCF + permitted
 + sealed Capability Design   primitives + sealed Capability Design
          |                         |
          v                         v
 1.0 STUDY -> GENERATE        1.0 STUDY -> GEN_ALGO
          |                         |
          v                         v
 skeleton-assisted driver      from-scratch driver
          |                         |
          +------------+------------+
                       |
                       v
      same trusted Direct-MuJoCo Harness
                       |
              failure | success
                       v
      condition-local bounded implementation Repair
                       |
                       v
      capability admission verdict + per-trial videos
                       |
             pass only |
                       v
       random five-task task_demo_suite.json
                       |
                       v
        separate Task Demo verdict + videos
                       |
                       v
          non-blocking Evolution sidecar
                       |
                       v
       proposal/outcome for the applicable protocol's review
                       |
                       v
     versioned Experience disposition for later runs only
```

The vendored AutoAdapter 1.0 STUDY/GENERATE substrate is derived from commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`. Later changes may make the code self-contained, but
must preserve the observable contract below rather than silently substitute a fixed driver.

For RQ1/Experiment 1, the two generation conditions are separate experimental cells. For one
robot/model replicate they reuse the same sealed `capability_design.json`, the same complete
`capability_validation_suite.json`, the same recorded five-task `task_demo_suite.json`, frozen Task
Library snapshot, model identity, and environment.
They run in isolated workspaces; neither condition may
inspect or reuse the other condition's candidate, trace, validation result, Repair history, or
generated driver. Each condition has its own maximum of three submitted driver attempts and
receives an independent capability-validation verdict. Only an admitted final driver enters Task
Demo; that later verdict is recorded separately and never causes same-run Repair. Results are never
merged into one ambiguous driver result. Experiment 2 and Experiment 3 do not inherit this
two-condition branch: their delegated authorities fix the SO-101 source/later route and the
Sonnet-only 33-cell skeleton-assisted route, respectively.

**中文辅助说明。** 下图只用于解释 0.20 之前的历史记录；凡与第 3.0 节冲突均不具规范效力：

```text
Morphology + >=20 项有来源 Tasks/通过标准
          + protocol-eligible Experience（Exp2 source 与每个 Exp3 cell 为空）
                              |
                              v
 Task-Grounded Capability Design（5-10 项 capability contract）
                              |
                              v
              Independent Validation Compiler
                              |
                              v
 capability_design.json + 完整 capability_validation_suite.json
                       |
          +------------+------------+
          |                         |
          v                         v
 完整 MJCF + skeleton         完整 MJCF + 获准 primitives
 + 已封存 Capability Design   + 已封存 Capability Design
          |                         |
          v                         v
 1.0 STUDY -> GENERATE        1.0 STUDY -> GEN_ALGO
          |                         |
          v                         v
 skeleton-assisted driver      from-scratch driver
          |                         |
          +------------+------------+
                       |
                       v
        同一可信 Direct-MuJoCo Harness
                       |
              失败     | 成功
                       v
          各生成条件独立的有限实现 Repair
                       |
                       v
       capability 准入 verdict + 每次 trial 视频
                       |
              仅通过时 |
                       v
       随机五-task task_demo_suite.json
                       |
                       v
          独立 Task Demo verdict + 视频
                       |
                       v
             非阻塞 Evolution sidecar
                       |
                       v
          供适用 protocol 审查的 proposal/outcome
                       |
                       v
         仅供后续运行的版本化 Experience disposition
```

仓库内置的 AutoAdapter 1.0 STUDY/GENERATE 基础来自提交
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`。后续可以把代码改为自包含，但必须保持下述
可观察合同，不能悄悄改为输出固定 driver。

对于 RQ1/Experiment 1，两种生成条件是相互独立的实验 cell。对于同一个机器人/model replicate，它们复用同一份已封存
`capability_design.json`、同一套完整 `capability_validation_suite.json`、同一套有记录的五-task
`task_demo_suite.json`、已封存 Task Library 快照、模型身份和环境，
并在隔离的 workspace 中运行；任一条件都不得检查或复用另一条件的 candidate、trace、验证结果、
Repair 历史或生成的 driver。每种条件各自最多提交三个 driver attempt，并分别获得 Harness
capability-validation verdict。只有通过准入的最终 driver 才进入 Task Demo；后续 Demo verdict
单独记录，绝不触发本轮 Repair。结果不得合并成一个含糊的 driver 结果。Experiment 2 与
Experiment 3 不继承这个双条件分支：两者分别由委派权威固定 SO-101 source/later route 和仅
Sonnet 的 33-cell skeleton-assisted route。

### 3.1 Task-Grounded Capability Design / 任务驱动的能力设计

Task-Grounded Capability Design (`TGCD`) receives public robot/configuration facts, the complete
frozen Task Library snapshot,
including each admitted task's source lineage and pass standard, and eligible design-level
Experience. It must design, rather than select from a pre-authored effect library or implement,
between five and ten reusable capability contracts for the robot.

The sealed `capability_design.json` must define for each designed capability:

- a unique model-authored `capability_id` and effect name with an implementation-independent
  semantic description;
- the exact set of covered Task Library task IDs and a concise explanation of the reusable common
  effect that justifies their abstraction;
- typed inputs and outputs, units, frames, preconditions, temporal semantics, invariants, required
  public action and observation affordances, and public failure behavior; and
- a public validation contract containing exactly one `primary` clause selected from the covered
  tasks as the authoritative capability-level criterion and, only when materially different
  validation semantics require it, at most one `robustness` clause. Each selected clause retains
  its metric semantics, unit, comparator, threshold or allowed range, dwell or other temporal rule,
  aggregation, and exact source-task/standard lineage.

Every admitted task must be covered by exactly one designed capability for an accepted Design.
The five-to-ten bound forces reuse across the at-least-twenty tasks; arbitrary merging without a
common robot effect is invalid. The primary criterion must be representative of that common effect
and cannot be a weaker rewrite of its selected source clause. A robustness clause must identify a
different source task and explain the scene, metric, or temporal obligation that the primary case
does not exercise. Omitted source clauses remain in the sealed Task Library and remain eligible for
Task Demo; their omission from capability admission must not be reported as if those tasks passed.

TGCD may design names, abstractions, interfaces, and validation contracts, but it cannot
invent unsupported robot affordances, units, frames, source claims, or less demanding standards.
The semantic interface is transported through the fixed `method(request=...)` ABI; TGCD does not
rename that Python envelope and may use only task-parameter fields declared by the covered tasks.
For an otherwise declared and type-correct task-parameter input, the Framework may mechanically
canonicalize `required_for_task_ids` to the exact covered tasks whose public invocation schemas
require that parameter; it may not add or remove an input or change its type, unit, or frame.
It must not receive private instances, seeds, exact reset state, simulator symbols, measurement
implementation, guards, executable cases, expected outcomes, validation reports, or candidate
implementation information.

**中文辅助说明。** Task-Grounded Capability Design（`TGCD`）接收公开的机器人/配置事实、
完整的已封存 Task Library 快照（包括
每项已准入任务的来源 lineage 和通过标准）以及符合条件的设计级 Experience。它必须为该机器人
设计五至十项可复用 capability contract，而不是从预写 effect 库中选择，也不在本阶段生成实现。

封存的 `capability_design.json` 必须为每项 capability 定义：模型设计且唯一的 `capability_id` 和
effect 名称及与实现无关的语义；所覆盖的精确 Task Library task ID，以及这些任务为何共享同一
可复用机器人 effect 的简要理由；带类型的 inputs/outputs、unit、frame、precondition、时间语义、
invariant、所需公开 action/observation affordance 和公开失败行为；以及公开 validation contract。
该 validation contract 必须恰好包含一个从被覆盖 task 中选择的 `primary` clause，作为权威的
capability-level criterion；只有 validation 语义确实不同且 primary 无法表达时，才可再包含最多
一个 `robustness` clause。每个被选 clause 必须保留其 metric 语义、unit、comparator、
threshold/允许范围、dwell 或其他时间规则、aggregation 和精确来源 task/standard lineage。

被接受的 Design 必须让每项已准入任务恰好由一个设计 capability 覆盖。至少二十项任务只能设计
五至十项 capability，这一边界用于迫使模型形成复用抽象；没有共同机器人 effect 的任意合并无效。
Primary criterion 必须代表该共同 effect，且不得把其所选来源 clause 改写得更宽松。Robustness
clause 必须来自另一项 task，并说明 primary 未覆盖的 scene、metric 或 temporal obligation。未被
选为 capability admission criterion 的来源 clause 继续保存在封存 Task Library 中，并可被 Task
Demo 抽取；不得把它们未进入 capability suite 错报成相应 task 已通过。TGCD 可以设计名称、抽象、接口和
validation contract，但不能发明无依据的机器人 affordance、unit、frame、来源声明或更宽松标准；
语义 interface 通过固定的 `method(request=...)` ABI 传输，TGCD 不得改名该 Python 调用信封，且
只能使用被覆盖任务公开声明的 task-parameter 字段。对于已声明且 type 正确的 task-parameter
input，Framework 可以把 `required_for_task_ids` 机械规范化为公开 invocation schema 确实要求该
参数的被覆盖 task；不得借此增加或删除 input，也不得改变 type、unit 或 frame；
也不得接收私有实例、seed、精确 reset 状态、simulator symbol、measurement 实现、guard、可执行
case、预期结果、validation report 或 candidate 实现信息。

### 3.2 Independent Validation Compiler / 独立验证编译器

The Independent Validation Compiler (`IVC`) consumes the sealed Capability Design, the same
source-backed task records, and Framework-private task instances and execution bindings. Before
initial Driver Synthesis begins, it audits and compiles the designed public validation contracts
into the complete private `capability_validation_suite.json`. It is implementation-blind: it cannot inspect `driver.py`,
candidate traces, candidate output, Repair history, or validation results.

For every designed capability, the IVC must:

1. verify exactly one primary contract and at most one justified robustness contract, each copied
   from a covered task's source pass-standard clause;
2. reject an invented, missing, incomparable, or weaker metric, comparator, threshold, temporal
   rule, or aggregation;
3. bind the public metric semantics to trusted MuJoCo observations and Framework-owned measurement
   code;
4. author each exact task-neutral case request while referencing concrete private scene/reset
   state, repetitions, termination, measurement binding, and anti-false-pass guards; and
5. produce deterministic per-case, per-capability, and whole-suite verdict rules.

The IVC may preserve a TGCD threshold or make a private case stricter when justified, but
it cannot silently relax a selected source obligation. A structural/reference checker must confirm
five to ten capabilities, exactly one primary and at most one justified robustness case per
capability, valid simulator bindings, and no weaker-standard substitutions before Driver Synthesis
receives the sealed Capability Design.

The complete capability validation suite is sealed before STUDY and must remain identical for the
robot's reference calibration, both generation conditions, and every Repair attempt. Its
whole-suite verdict requires every compiled case to pass, so every designed capability primary
case and every selected robustness case is exercised before the generated driver is admitted.

After the audit and before STUDY, the Framework also uniformly samples exactly five original Task
Library task IDs without replacement using a recorded run seed. It compiles every scoring clause
of those five tasks through their existing private instances, bindings, and guards and seals the
result as `task_demo_suite.json`. The selected task IDs and seed remain Harness-private. This
artifact is not sampled from or treated as a reduced capability-validation suite: it is held for
the post-admission Task Demo and is not executed during generation or Repair.

The executable suite, concrete cases, bindings, and guards remain Harness-private. Driver Synthesis
may see the public capability interfaces and validation-contract semantics from the sealed
Capability Design, but not the private realization of those standards. Creating the suite before
Driver Synthesis is not sufficient isolation if candidate code can later read it. The final
pass/fail verdict belongs only to the trusted Harness, never to TGCD or IVC model self-report.

**中文辅助说明。** Independent Validation Compiler（`IVC`）接收已封存 Capability Design、
同一批有来源 task record，以及 Framework 私有的任务实例和执行 binding。在首次 Driver
Synthesis 开始前，它审计并把 TGCD 设计的公开 validation contract 编译成完整的私有 case
suite，即 `capability_validation_suite.json`。IVC 对实现不可见，不得检查 `driver.py`、
candidate trace、candidate 输出、Repair 历史或验证结果。

对每项设计 capability，IVC 必须：确认恰好一个 primary contract 和最多一个有明确理由的
robustness contract，且各自精确复制某项被覆盖 task 的来源通过标准；拒绝
虚构、缺失、不可比较或更宽松的 metric、comparator、threshold、时间规则或 aggregation；把公开
metric 语义绑定到可信 MuJoCo observation 和 Framework measurement 代码；自行写出精确且
task-neutral 的 case request，同时引用具体私有 scene/reset 状态、重复次数、终止条件、measurement
binding 和 anti-false-pass guard；并生成逐 case、
逐 capability 及整套 suite 的确定性 verdict 规则。

有依据时 IVC 可以保留 TGCD threshold 或让私有 case 更严格，但不能悄悄放宽被选来源义务。
Driver Synthesis 收到封存 Capability Design 前，结构/reference checker 必须确认 capability 数量
为五至十、每项 capability 恰好有一个 primary case 且最多有一个有明确理由的 robustness case、
simulator binding 有效且不存在弱化标准的替换。完整 capability validation suite 在 STUDY 前封存，并在该机器人 reference
校准、两种生成条件及全部 Repair attempt 间保持完全一致；整套 verdict 要求所有编译 case
通过，因此 driver 准入前会执行每项 capability 的 primary case 及所有被选 robustness case。

完成审计后且在 STUDY 前，Framework 还使用已记录的 run seed，从原始 Task Library 中无放回
均匀随机抽取恰好五项 task，并通过现有私有 instance、binding 和 guard 编译这些 task 的全部
scoring clause，封存为 `task_demo_suite.json`。被选 task ID 和 seed 仅对 Harness 可见。这份
artifact 不是缩减版 capability validation suite，只供 driver 准入后的 Task Demo 使用，在生成和
Repair 阶段都不执行。可执行 suite、具体 case、binding 和 guard
始终属于 Harness 私有数据。Driver Synthesis 可以看到封存 Capability Design
中公开的 capability 接口及 validation-contract 语义，但不能看到这些标准的私有实现。最终
pass/fail verdict 只属于可信 Harness，不能来自 TGCD 或 IVC 模型自述。

Sections 3.1 and 3.2 define the Framework mechanisms evaluated in Experiment 2 and produce the
class of upstream input consumed by the fixed-input Experiment 1 B1 comparison. They are not B1
stages.
Before B1 begins, one reviewed `capability_design.json` and one complete
`capability_validation_suite.json` per robot must be copied into a versioned fixed-input bundle and
connected to the trusted Harness. The B1 manifest must pin the fixed-input identity. A bundle is
only an implementation container; Experiment 1 requires no task-blind reference driver or
reference calibration. No B1 backbone reruns TGCD or IVC, and no B1 outcome may change the
fixed interface, criteria, private cases, bindings, guards, or verdict rules. A changed bundle
defines a new experiment configuration and requires all affected B1 units to be rerun.

第 3.1 与 3.2 节定义 Experiment 2 评估的 Framework 机制，并产生固定输入 Experiment 1 B1 比较所
消费的上游输入类型；它们不属于 B1 stage。B1 开始前，每台
机器人必须把一份经过审查的 `capability_design.json` 和一份完整
`capability_validation_suite.json` 固定进版本化 input container，并连接到可信 Harness。该 container
只是实现载体；Experiment 1 不要求 task-blind reference driver 或 reference calibration。B1 manifest
必须固定 input identity。任何 B1 backbone 都不
重新运行 TGCD 或 IVC，任何 B1 outcome 也不得改变固定 interface、criteria、私有 case、binding、
guard 或 verdict rule。修改 bundle 将构成新的实验配置，并要求受影响的全部 B1 unit 重新运行。

### 3.3 Driver Synthesis / Driver 合成

A dynamic mainline run uses the configured real model for every model-authored stage. In Experiment 1
B1 those stages begin at STUDY; the run loads the manifest-pinned fixed interface and validation
bundle without invoking TGCD or IVC. Both preserved AutoAdapter 1.0 generation conditions may
inspect all run-relevant public inputs: the complete public robot package, including public
Morphology, Task Library catalog and source records, eligible Experience, the complete selected
MJCF closure, STUDY output, and the same fixed sealed Capability Design.
They may use a complete local Python/MuJoCo development probe within explicit call, simulated-time,
control-step, output, and wall-time budgets, and may write and revise one executable candidate in
their own workspace. The probe may load the canonical public scene, run model-authored scripts,
inspect simulator state, exercise candidate methods, and tune actuator-driven behavior; it may not
invoke or inspect the private Harness suite.

Candidate-source import restrictions and probe-only utility permissions are distinct public facts.
A probe may use `os.environ` or `pathlib` only to access its staged public runtime and must load the
canonical scene through `AUTOADAPTER_PROBE_SCENE`; it may not guess a relative path, search the host,
or construct a substitute scene. If an OpenAI-compatible tool transport JSON-encodes an otherwise
schema-valid nested array or object as text, the terminal tool may parse that container before its
ordinary type and non-empty checks. It may not infer or synthesize missing submission content.

STUDY and GENERATE/GEN_ALGO execute through the self-contained AutoAdapter 1.0-style ReAct loop,
not as one-shot code-generation responses. STUDY receives the complete run-relevant public inputs
inline. Its normal path uses one public Python/MuJoCo probe turn followed by submission. If the first
probe fails, it may run exactly one corrected probe; a third and final turn is reserved for
submission or correction of a rejected submission. A successful first probe forbids a second probe.
The STUDY probe is a minimal canonical-scene liveness action and does not re-import the already
inline skeleton source. The terminal schema requires `skeleton_inspection` exactly in the
skeleton-assisted condition and omits that requirement in the from-scratch condition; terminal
handler requirements must not be stricter than the schema shown to the model.
GENERATE/GEN_ALGO may still list and read staged public files when needed, inspect the trusted
skeleton only in the skeleton-assisted condition, and run bounded public Python/MuJoCo probes. Each
candidate revision is one AutoAdapter 1.0-style implementation action:
`check_driver(source, checks)` carries the complete
source and one covered public invocation per capability. The Framework writes that source and
returns its bundled source-audit, import, and public-method physics-smoke diagnostics in the same
tool result. Separate model-visible `read_driver`, `write_driver`, audit, import, and per-method
smoke actions are not part of the mainline. Tool errors are returned to the same conversation so the
model can revise the candidate before submission. The Framework counts a formal attempt only after
the model explicitly submits `driver.py`; development revisions and rejected pre-submission checks
do not consume one of the three Harness attempts.

The runtime default permits one complete bundled check. Experiment 2 and Experiment 3 alone
explicitly pin `max_complete_driver_checks = 2`; under that pin, a stage with `C` sealed
capabilities reserves at most `2 × (C + 1) + 3` local probe calls: two complete bundles of one
canonical import/build plus one public physics smoke per capability, and no more than three
separately counted discretionary probes. A changed source invalidates prior-revision smokes, so the
second bundle validates only the corrected current revision. This capacity is local development
validation, not an additional model turn or submitted-driver attempt. The interface stub and the
actual interactive Generate/Repair prompts state that `request` is an ordinary Python mapping;
candidate fields are accessed with mapping subscription or mapping methods rather than attribute
access.

**中文辅助说明。** runtime 默认允许一次完整 bundled check。只有 Experiment 2/3 显式固定
`max_complete_driver_checks = 2`；在该配置下，含 `C` 项 capability 的
stage 最多保留 `2 × (C + 1) + 3` 次本地调用：两套“canonical import/build 加逐 capability
公开 physics smoke”，以及独立计数且最多三次的自由 probe。源码改变后旧 revision 的 smoke
失效，第二套只验证修正后的当前 revision。这是本地开发检查容量，不增加模型回合或正式 driver
submission。接口 stub 和真实 Generate/Repair 交互 prompt 都明确 `request` 是普通 Python
mapping，字段必须使用下标或 mapping 方法访问，而非属性访问。

The Framework applies one deterministic bounded projection when serializing this tool conversation
for the next model turn. It retains the initial public task context, exactly one latest complete
`driver.py` snapshot, and the three most recent completed tool-interaction groups. Superseded
`check_driver` source payloads are replaced by tool name, driver revision, source character count,
and result status; similarly superseded development-probe scripts may be replaced
by probe identity, script character count, and result status while their bounded diagnostics remain
available. If a hard history-character budget still requires eviction, oldest completed groups are
removed first and represented by the same deterministic metadata. This model-facing projection does
not modify the complete on-disk trace, does not use another model to summarize history, and cannot
introduce private Harness information.

**上下文辅助说明。** Framework 在向下一模型回合序列化工具对话时应用一份确定性的有界投影：
保留首次公开任务上下文、恰好一份最新完整 `driver.py`，以及最近三个已完成工具交互组。已被取代
的 `check_driver` 源码 payload 改为 tool 名称、driver revision、源码字符数和结果
状态；已被取代的 development probe 脚本也可改为 probe 标识、脚本字符数和结果状态，同时保留
有界诊断。若仍超过硬性历史字符预算，则优先移除最旧的已完成组并用同样的确定性元数据表示。
该模型侧投影不修改磁盘中的完整 trace、不调用另一模型摘要历史，也不得引入私有 Harness 信息。

The Framework mechanically generates the same interface-only starting stub for both conditions
from the sealed Capability Design. It may contain only a generic candidate class,
`build(model, data)`, every exact capability method with `(self, request)`, and placeholder bodies
that raise `NotImplementedError`. It must not contain task dispatch, control implementation,
actuator mapping, state targets, control values, physics stepping, a selected skeleton, or any
reference-derived logic. The model may inspect and edit or replace this stub, and remains the sole
author of the executable implementation submitted in both conditions. An unchanged or partially
implemented stub is rejected before Harness submission.

The conditions differ only in implementation assistance:

1. **Skeleton-assisted.** The model may list, inspect, import, and instantiate the trusted skeleton
   family, then fill its robot-specific Spec and add the exact sealed capability methods.
2. **From-scratch.** The model must write the robot controller, kinematics or locomotion algorithm,
   actuator mapping, observations, stepping, and lifecycle code itself using only the permitted
   MuJoCo, NumPy, and Python-standard-library primitives. It may not inspect, import, copy, or call
   the trusted skeleton implementation.

For serial arms, the trusted skeleton family may expose a task-neutral Cartesian path-tracking
primitive. It may accept caller-supplied finite segments or polylines and public control parameters,
interpolate the Cartesian reference, and use fresh-state DLS feedback to limit cross-track error
while advancing the canonical physics session through `data.ctrl`. It must remain independent of
capability names and task identities and must not contain a fixed task path, target, private
acceptance threshold, scene/reset behavior, success verdict, or reference-driver policy. The
model-authored Driver selects the primitive, constructs its path and parameters from the public
request, and composes it with the other capability behavior.

The implementation model may read the sealed public capability interfaces and validation contracts,
including every source-derived threshold already public in the Capability Design. The hidden boundary
is limited to the private IVC/Harness validation definition: private suite files, unreleased case and
reset construction, private stricter standards, executable criteria and measurement bindings,
private guards and expected trajectories, and Harness source/configuration. Model-provider and
repository credentials and the other generation condition's artifacts also remain unavailable.
This restriction does not prevent Repair from receiving the complete candidate-facing report after
an attempt under Section 3.5.

The package reference driver is not a public Driver Synthesis input. Neither condition, its STUDY,
its development probe, GENERATE, nor Repair may receive the reference source, a reference-derived
wrapper, reference trajectory, or reference controller output. Reference calibration runs in a
Framework-owned workspace, and its implementation never becomes Experience for a dynamic run.

Each condition submits one model-authored executable as `driver.py` to the same physical-validation
contract. It exposes `build()` and the exact public capability methods required by the sealed
Design. `build()` returns the driver object, every required capability is an instance method on that
object with exact `(self, request)` transport parameters, and `request` is the plain public mapping
defined by Section 2.4 rather than an object with attribute semantics. The implementation may
additionally retain the original 1.0
`driver_from_scratch.py` filename as condition-local evidence, but no fixed or Framework-authored
controller may replace the model-authored candidate. Neither condition may substitute a different
scene, encode a private case, claim its own final verdict, or receive information from the other
condition.

Every capability that produces a dynamic robot effect must implement a bounded state-dependent
feedback loop, directly or through an allowed trusted-skeleton primitive. It repeatedly reads fresh
canonical model/data state, computes the next actuator command from that observation and the public
request, writes through `data.ctrl`, advances the same canonical MuJoCo session, and corrects from
fresh state until observed public convergence or a bounded timeout. A fixed waypoint sequence may
provide supervisory targets, but one-read-many-write, pure polling, sleep-only behavior, fixed
open-loop trajectories, and self-reported completion do not satisfy this runtime contract.

**中文辅助说明。** 动态主线运行中，每个由模型创作的阶段都必须调用已配置的真实模型。在
Experiment 1 B1 中，这些阶段从 STUDY 开始；run 直接加载 manifest 固定的 interface 与 validation
bundle，不调用 TGCD 或 IVC。两种保留的 AutoAdapter 1.0 生成条件都可以检查与本次 run 相关的
全部公开输入，包括完整公开机器人 package 中的 Morphology、Task Library catalog 与 source
record、符合条件的 Experience、所选 MJCF 完整闭包、STUDY 输出和同一份固定且已封存的
Capability Design。两者都可以使用完整的本地
Python/MuJoCo 开发 probe，但该 probe 受明确的调用次数、模拟时间、control step、输出和 wall-time
预算约束。probe 可以加载 canonical 公开 scene、运行模型编写的脚本、检查 simulator state、执行
candidate 方法并调试 actuator-driven 行为，但不能调用或检查私有 Harness suite。

candidate 源码 import 限制与 probe 专用 utility 权限是两组不同的公开事实。probe 只能通过
`os.environ` 或 `pathlib` 访问 staged 公开运行环境，并且必须通过
`AUTOADAPTER_PROBE_SCENE` 加载 canonical scene；不得猜测相对路径、搜索 host 或构造替代
scene。如果 OpenAI-compatible tool transport 将原本符合 schema 的嵌套数组或对象编码成文本，
terminal tool 可以在常规类型与非空检查前解析该容器，但不得推断或补写缺失提交内容。

STUDY 与 GENERATE/GEN_ALGO 必须通过主线自包含的 AutoAdapter 1.0 式 ReAct loop 执行，
不能退化为一次性代码生成响应。STUDY 在初始上下文直接取得本次运行相关的完整公开输入；正常路径
只用一次公开 Python/MuJoCo probe 回合和随后的提交回合。仅当首次 probe 失败时允许一次纠正后的
probe；第三个最终回合只用于提交或修正被拒提交，首次 probe 成功后禁止第二次 probe。
STUDY probe 是最小 canonical-scene liveness 动作，不会再次导入已经内联提供的 skeleton 源码。
terminal schema 只在 skeleton-assisted 条件要求 `skeleton_inspection`，在 from-scratch 条件不
要求；terminal handler 的要求不得比展示给模型的 schema 更严格。
GENERATE/GEN_ALGO 仍可在需要时列出并读取
staged 公开文件；仅在 skeleton-assisted 条件查看可信 skeleton；运行有预算的公开 Python/MuJoCo
probe；并通过原子 `check_driver(source, checks)` 获得源码审计、import 及全部公开方法 smoke
诊断。工具错误返回同一个模型 conversation，使模型可以在提交前自行修订。
只有模型明确提交 `driver.py` 后，Framework 才计入一次正式 attempt；开发过程中的改写和未通过的
提交前 audit 不消耗最多三次 Harness attempt。

Framework 根据已封存 Capability Design 为两种条件机械生成完全相同的纯接口起始 stub。它只能
包含通用 candidate class、`build(model, data)`、每个准确 capability method 的
`(self, request)` 和抛出 `NotImplementedError` 的占位体；不得包含 task dispatch、控制实现、
actuator mapping、状态目标、控制值、physics step、选定 skeleton 或任何 reference 派生逻辑。
模型可以检查、修改或完全替换该 stub，并且仍是两种条件最终提交的 executable implementation
的唯一作者。未修改或未完整实现的 stub 必须在进入 Harness 前被拒绝。

两种条件仅在实现辅助上不同：

1. **Skeleton-assisted。** 模型可以列出、检查、导入并实例化可信 skeleton family，填写机器人
   专用 Spec，并补齐已封存 Capability Design 精确要求的 capability 方法。
2. **From-scratch。** 模型必须只使用获准的 MuJoCo、NumPy 和 Python 标准库 primitives，自行
   编写机器人 controller、kinematics 或 locomotion algorithm、actuator mapping、observation、
   stepping 和 lifecycle；不得检查、导入、复制或调用可信 skeleton 实现。

对串联机械臂，可信 skeleton family 可以暴露 task-neutral Cartesian 路径跟踪 primitive。它可以
接收调用方传入的有限 segment 或 polyline 及公开控制参数，插值 Cartesian reference，并通过
基于最新状态的 DLS feedback 限制 cross-track error，只经 `data.ctrl` 推进 canonical physics
session。它必须与 capability name 和 task id 无关，不得包含固定任务路径或目标、私有验收
threshold、scene/reset 行为、success verdict 或 reference-driver policy。模型创作的 Driver 仍负责
选择该 primitive，从公开 request 构造路径和参数，并将其与其他 capability 行为组合。

实现模型可以读取封存的公开 capability 接口和 validation contract，包括 Capability Design 中
原本公开的全部来源 threshold。不可见边界只覆盖 IVC/Harness 私有 validation 定义：私有 suite
文件、未公开的 case/reset 构造、私有加严标准、可执行 criterion 与 measurement binding、私有
guard 和 expected trajectory，以及 Harness 源码/配置。模型供应商与仓库 credential、另一生成
条件的产物也保持不可见。本限制不妨碍 Repair 按 3.5 节在 attempt 结束后读取完整的
candidate-facing 报告。每种条件各自只提交一个模型生成的 `driver.py` 进入相同的
物理验证合同；它必须暴露 `build()` 以及已封存 Capability Design 精确要求的公开 capability 方法。实现可
额外保留原始 1.0 的 `driver_from_scratch.py` 文件名作为该条件的证据，但不得用固定或 Framework
编写的 controller 替换模型 candidate。任一条件都不得替换 scene、编码私有 case、自行声明最终
verdict，或接收另一生成条件的信息。

每个产生动态机器人效果的 capability 都必须直接或通过允许的可信 skeleton primitive 实现有界、
依赖状态的反馈闭环。它必须反复读取最新 canonical model/data 状态，根据该观测和公开 request
计算下一 actuator command，通过 `data.ctrl` 写入，在同一 canonical MuJoCo session 中推进物理，
再根据最新状态修正，直至观察到公开目标收敛或达到有界 timeout。固定 waypoint 序列可以作为上层
目标，但一次读取后多次写入、纯 polling、仅 sleep、固定开环 trajectory 和自报完成均不满足该
运行时契约。

package 中的 reference driver 不是 Driver Synthesis 的公开输入。两种条件及其 STUDY、开发
probe、GENERATE 和 Repair 均不得接收 reference 源码、由 reference 派生的 wrapper、reference
轨迹或 controller 输出。reference calibration 只在 Framework 拥有的 workspace 中执行，其实现
不得成为 dynamic run 的 Experience。

### 3.4 Trusted Direct-MuJoCo Harness / 可信 Direct-MuJoCo Harness

The Framework-owned Harness:

1. selects and verifies the canonical robot package and MJCF closure;
2. creates or verifies the canonical MuJoCo session used by the driver;
3. imports the candidate in an isolated worker with only approved public files and dependencies;
4. resets each trial independently;
5. invokes the required public driver method;
6. advances real MuJoCo physics and collects trusted simulator state;
7. evaluates the private sealed criterion; and
8. writes the authoritative structured verdict and video manifest.

Candidate return values, logs, textual explanations, self-reported success, or the fact that a
method completed are never physical success evidence.

**中文辅助说明。** Framework 拥有的 Harness 必须：

1. 选择并核验 canonical 机器人包和 MJCF 闭包；
2. 创建或核验 driver 使用的 canonical MuJoCo session；
3. 在隔离 worker 中导入 candidate，仅提供获准的公开文件和依赖；
4. 对每次 trial 独立 reset；
5. 调用要求的公开 driver 方法；
6. 推进真实 MuJoCo physics 并采集可信 simulator state；
7. 依据私有封存 criterion 进行评估；
8. 写出权威的结构化 verdict 和 video manifest。

candidate 返回值、日志、文字说明、自报成功或方法正常结束，都不能作为物理成功证据。

### 3.5 Repair / 实现修复

The first submitted driver is attempt `0` for scientific reporting. The mainline permits at most
three generated driver attempts in total: one initial generation and no more than two Repair
attempts. A Repair may change only `driver.py`.

Repair uses the same bounded AutoAdapter 1.0-style ReAct development loop as initial Driver
Synthesis.
Within one Repair attempt, the model may repeatedly inspect the preceding candidate-facing report,
edit `driver.py`, run public-only probes, and react to audit/import/smoke diagnostics before it
explicitly submits the replacement. These development turns remain part of one Repair attempt and
cannot inspect or invoke the private Harness.

Repair receives the previous driver and the complete candidate-facing report for the immediately
preceding capability-validation attempt. This report includes every trial and clause outcome, opaque private-case identity,
the public and runtime invocation inputs actually supplied to the driver, actual measured values,
available state and trajectory diagnostics, exceptions, logs, guard outcomes, recordings, videos,
and the complete per-capability, per-clause, and per-case result structure. Repair may use the same
complete, budget-bounded local Python/MuJoCo development probe as initial generation.

Alongside that complete report, the Framework provides one deterministic failure-focus index derived
only from the same candidate-facing fields. It lists passed cases concisely and, for failed cases,
surfaces the actual public request, measured value, exception/error, guard outcome, contact summary,
and available initial/final public state. This is a navigation aid for the model, not a replacement
report, new verdict, model-authored summary, or additional private disclosure.

The report omits only the private validation definition and unrelated secrets: private suite files
and source, unreleased case/reset/seed construction, private stricter thresholds or expected values,
executable criterion expressions, measurement bindings, private guard definitions, hidden expected
trajectories, Harness source/configuration, internal private paths, credentials, and artifacts from
the other generation condition. Public source-derived standards already present in the sealed
Capability Design remain visible. The report and media become available only after the evaluated
candidate worker exits; they are never mounted into that worker during evaluation. Every revision is
evaluated against the same complete pre-generated `capability_validation_suite.json`. A Task Demo
report is never supplied to same-run Repair.

A model-requested Repair probe that fails the public source boundary is not executed. Its rejection
diagnostic is returned in the Repair context, and Repair may still produce revised `driver.py`
source within the same bounded attempt. A rejected optional probe is not a physical-validation
result and does not weaken the private suite.

Reports must separate first-pass `pass@0` from cumulative success after Repair. A repaired pass may
contribute to `pass@k`, but it must never be presented as an initial pass or an independent new
suite estimate.

**中文辅助说明。** 首次提交的 driver 在科研报告中记为 attempt `0`。主线最多允许三个生成
driver attempt：一次首次生成，加上最多两次 Repair。Repair 只能修改 `driver.py`。

Repair 使用与首次 Driver Synthesis 相同的有预算 AutoAdapter 1.0 式 ReAct 开发循环。在一次 Repair
attempt 内，模型可以反复检查上一 attempt 的 candidate-facing 报告、修改 `driver.py`、运行仅含
公开输入的 probe，并根据 audit/import/smoke 诊断继续修正，直到明确提交替代版本。这些开发 turn
仍属于同一次 Repair attempt，且不能检查或调用私有 Harness。

Repair 接收上一版 driver，以及紧邻上一 capability-validation attempt 的完整 candidate-facing
报告。该报告包含每个
trial 和 clause 的结果、不透明的 private-case 标识、实际提供给 driver 的公开/runtime 调用输入、
实际测量值、可用 state/trajectory 诊断、exception、log、guard 结果、录像、视频，以及完整的逐
capability、逐 clause 和逐 case 结果结构。Repair 可以使用与首次生成相同的完整但有预算上限的
本地 Python/MuJoCo 开发 probe。

除完整报告外，Framework 还会仅从同一批 candidate-facing 字段机械生成一份确定性的失败焦点
索引。它简要列出通过 case，并针对失败 case 突出实际公开 request、测量值、exception/error、
guard 结果、接触摘要及可用公开初末状态。该索引只用于帮助模型定位，不是替代报告、新 verdict、
模型生成摘要或额外私有披露。

报告只删除私有 validation 定义和无关 secret：私有 suite 文件及源码、未公开的 case/reset/seed
构造、私有加严 threshold 或 expected value、可执行 criterion 表达式、measurement binding、私有
guard 定义、隐藏 expected trajectory、Harness 源码/配置、内部私有路径、credential 和另一生成
条件的产物。封存 Capability Design 中原本公开的来源标准继续可见。报告和媒体只有在受评
candidate worker 退出后才交给 Repair，在 evaluation 期间绝不挂载进该 worker。每个修订版本都
必须使用同一套预先生成的完整 `capability_validation_suite.json` 评估；Task Demo 报告绝不提供
给本轮 Repair。报告必须区分首次 `pass@0` 与 Repair 后累计成功；
Repair 后通过可以计入 `pass@k`，但不得包装成首次通过，也不得作为独立的新 suite 估计。

### 3.6 Task Demo / 任务演示

Task Demo begins only after a condition's final generated driver passes the complete capability
validation suite. The Framework fixes that admitted driver, executes the sealed five-task
`task_demo_suite.json` once through the same trusted Direct-MuJoCo Harness, and records a separate
verdict and per-trial videos. Task Demo does not reopen generation, consume a driver attempt, or
trigger same-run Repair. A failed or incomplete Task Demo must be reported truthfully but does not
change the preceding driver-synthesis pass.

The five selected tasks and all of their scoring clauses are a bounded Demo sample, not evidence
that every Task Library task was run. This Demo alone also does not establish a
capability-interface-use result unless the declared fixed high-level-controller path was actually
part of the run. Its terminal report may be included in the
input projection for Evolution, which can influence only a later matched run.

The declared Direct-MuJoCo mainline high-level controller is one fixed, bounded ReAct loop. The
Framework parent process gives it only the current public task description and invocation request
plus tool schemas mechanically derived from the sealed public capability interface. Capability
calls execute against the admitted driver in one persistent credential-free candidate worker and
one canonical MuJoCo session for that trial; only a bounded public operation observation returns to
the controller. The controller architecture, system prompt, tool derivation, and turn/call budgets
remain fixed across matched LLM backbones. A controller finish action or self-report is never a
Harness verdict, and private criteria, bindings, guards, reference inputs, and model credentials are
never sent to the candidate worker.

For delegated Experiment 1b/B2, the versioned experiment manifest may
preselect a reviewed package reference driver instead of a B1-generated driver. The manifest must
pin the exact driver identity and validation evidence before outcomes are inspected. Formal
extension execution remains blocked
until one capability interface and its adapter are frozen for that driver, the complete
interface-bound capability validation suite passes, and the same fixed driver-interface-adapter
combination is used for every compared backbone. The controller receives only the public task and
interface-derived tools; it never receives the reference source or reference-only calibration data.
B2 is the bounded Chapter 3/RQ1 capability-interface-use subexperiment and is not Experiment 2 or
RQ2 evidence. It does not expose reference material to B1 or convert the fixed driver into a model
condition.

**中文辅助说明。** 只有某一条件的最终生成 driver 通过完整 capability validation suite 后，
Task Demo 才开始。Framework 固定该已准入 driver，通过同一可信 Direct-MuJoCo Harness 运行一次
已封存的五-task `task_demo_suite.json`，并单独记录 verdict 和逐 trial 视频。Task Demo 不会重开
生成、不消耗 driver attempt，也不触发本轮 Repair。Demo 失败或证据不完整必须如实报告，但不
改变此前已经得到的 driver-synthesis pass。

这五项被选 task 及其全部 scoring clause 只是有界 Demo 样本，不能证明 Task Library 的全部任务
均已运行。如果本次 run 没有
实际包含已声明且固定的 high-level-controller 路径，仅有该 Demo 也不能建立
capability-interface-use 结果。其终态报告可以进入 Evolution 的输入投影，但只能影响后续匹配 run。

Direct-MuJoCo 主线声明的 high-level controller 是一套固定且有界的 ReAct loop。Framework
父进程只向它提供当前公开 task 描述与调用 request，以及从封存公开 capability interface 机械生成的
tool schema。Capability 调用在单个 trial 内通过同一个无 credential 的持久 candidate worker，作用于
同一份已准入 driver 和 canonical MuJoCo session；controller 只能收到有界的公开操作反馈。匹配的
LLM backbone 比较中，controller 架构、system prompt、tool 派生规则和 turn/call budget 均保持不变。
Controller 的 finish action 或自述绝不构成 Harness verdict；私有 criterion、binding、guard、reference
输入和模型 credential 也绝不发送给 candidate worker。

对于委派的 Experiment 1b/B2 capability-interface-use 子实验，版本化 experiment manifest 可以预先选择经审查的
package reference driver，而不必选择 B1 生成的 driver。Manifest 必须在查看 outcome 前固定准确
driver identity 和 validation evidence。只有当该 driver 对应的一套 capability interface 及其 adapter
已封存、完整的 interface-bound capability validation suite 已通过，并且全部被比较 backbone 使用
同一套固定 driver-interface-adapter 组合时，该扩展才可正式执行。Controller 只接收公开 task 与从
interface 派生的 tools，绝不接收 reference 源码或 reference-only calibration data。B2 是 Chapter 3/RQ1
的有界 capability-interface-use 子实验，不是 Experiment 2 或 RQ2，也不会向 B1 暴露 reference
material 或把固定 driver 变成模型条件。

### 3.7 Evolution / 经验演化

Evolution is a non-blocking sidecar that reads a bounded projection of the terminal structured
report and proposes either one candidate Experience record for a later run or an explicit
no-reusable-lesson outcome. The projection retains terminal execution/verdict facts, attempt
summaries, outcome counts, and complete failed-trial diagnostics, but may replace repeated full
trajectory samples with sample counts because the full report remains retained as evidence and has
already been available to Repair. Evolution cannot change the current driver, suite, criterion,
verdict, retry decision, or run inputs. Failure of Evolution does not turn a completed validation
run into a driver-synthesis failure.

Experiment 2 is the only current formal protocol that invokes Evolution. Its SO-101 source run
starts with empty Experience, records a terminal Task Demo-stage outcome (executed with an admitted
driver or truthful `not-run` with a nonempty reason), and then makes exactly one terminal Opus 5
Evolution call. Opus writes only `observation`, `lesson`, `recommendation`, `scope`, and a public-only
`evidence` list. The Framework assigns `source_robot`, `generation_condition`, and a `positive` or
`negative` `terminal_outcome_label` from retained source-run verdict facts. A human reviewer records
exactly `accept` or `reject` with a nonempty reason for the unedited, Framework-labelled proposal;
the reviewer cannot edit any content or assigned label. An accepted proposal becomes globally eligible
after the fixed disposition and frozen reviewed snapshot. If accepted, one manually launched
independent Sonnet 4.6 later run loads the exact snapshot unchanged, at which point it becomes
effective. The source run never consumes its own proposal, and no same-round input is allowed.

Experiment 3 disables Evolution and Experience entirely. Its exact eleven-configuration,
Sonnet-only, skeleton-assisted `r01`--`r03` cells execute fresh TGCD/IVC, driver synthesis,
validation, and Task Demo, then stop. It has no matched no-Experience control and cannot support an
improvement, causal Experience, or morphology-effect claim.

A terminal driver-synthesis result remains truthful if the Experiment 2 Evolution call fails, but
the Experiment 2 closure must report the missing proposal and cannot claim an Experience-enabled
later run. Experiment 3 has no Evolution call to rerun. A failed sidecar or provider request may be
diagnosed from the retained terminal projection without altering any candidate, suite, verdict, or
run input.

**中文辅助说明。** Evolution 是非阻塞 sidecar：它读取最终结构化报告的有界投影，并为后续
运行提出一条 candidate Experience，或明确记录没有可复用 lesson。该投影保留终态
execution/verdict 事实、attempt 摘要、结果计数和完整失败 trial 诊断；由于完整报告已保留且已提供
给 Repair，重复的完整 trajectory sample 可以替换为 sample count。Evolution 不得改变当前
driver、suite、criterion、verdict、retry 决定或 run input。Evolution 失败不会把已经完成的
validation run 变成 driver-synthesis failure。

当前只有 Experiment 2 调用 Evolution：SO-101 source run 以空 Experience 开始，记录终态 Task Demo
stage（准入 driver 执行，或无准入 driver 时以非空理由记录 `not-run`），再由 Opus 5 做一次终态
Evolution call。Opus 只能写 `observation`、`lesson`、`recommendation`、`scope` 和 `evidence`；`evidence`
列表只能包含公开证据；
Framework 根据保留的 source-run verdict facts 赋值 `source_robot`、`generation_condition` 以及
`positive`/`negative` `terminal_outcome_label`。人工 reviewer 对未编辑且带 Framework label 的公开
proposal 只记录 `accept` 或 `reject` 及非空 reason，不得修改内容或 assigned label。接受后，固定
disposition 与 frozen reviewed snapshot 使其 globally eligible；手动启动的独立 Sonnet 4.6 later
run 原样加载该 snapshot 后，Experience 才 effective。Source run 不得读取自己的 proposal，同轮不得互相输入。

Experiment 3 完全禁用 Evolution 和 Experience。精确十一配置、Sonnet-only、skeleton-assisted、
`r01`--`r03` 的 cell 新鲜执行 TGCD/IVC、driver synthesis、validation 和 Task Demo 后停止；没有
matched no-Experience control，也不支持 improvement、因果 Experience 或 morphology effect claim。

如果 Experiment 2 的 Evolution 调用失败，terminal driver-synthesis 结果仍保持真实，但 closure 必须
记录 proposal 缺失，不能声称 Experience-enabled later run 已完成。Experiment 3 没有 Evolution call
可补跑。失败的 sidecar 或 provider request 可以基于保留的终态 projection 做诊断，但不得改变任何
candidate、suite、verdict 或 run input。

---

## 4. Isolation and false-success prevention / 隔离与防止假成功

### 4.1 Candidate-visible boundary / Candidate 可见边界

The candidate validation worker receives only the generated driver, the trusted public skeleton
when allowed by its condition, canonical public MJCF closure, public invocation arguments, sealed
public capability contracts, and approved runtime dependencies. Private IVC files, full task
instance records, executable criteria and measurement bindings, private guards, reports, videos,
Harness source/configuration, repository-wide files, and unrelated environment variables are
unavailable.

The minimum implementation should use a separate worker process with an explicit working
directory and environment allowlist. Model/API, cloud, and repository credentials must be removed
before candidate execution. This is an experiment isolation boundary, not a general security
sandbox product.

This worker boundary applies while candidate code is executing. After the worker exits, the
Framework may publish the complete candidate-facing attempt report and associated media to the
condition-local generation workspace for Repair, subject only to the private-definition redactions
in Sections 0.1.1 and 6.

**中文辅助说明。** candidate 验证 worker 只能接收生成的 driver、可信公开 skeleton、canonical
公开 MJCF 闭包、公开调用参数、封存的公开 capability contract 和获准的 runtime 依赖。
candidate 不得访问私有 Blue 文件、完整任务实例记录、可执行 criterion 与 measurement binding、
私有 guard、报告、视频、Harness 源码/配置、仓库全局文件或无关环境变量。

最小实现应使用独立 worker 进程，并显式限定工作目录和环境变量 allowlist。执行 candidate 前
必须移除模型/API、云服务和仓库凭据。这是实验隔离边界，不是通用安全 sandbox 产品。

该 worker 边界约束 candidate 代码正在执行的时期。worker 退出后，Framework 可以把完整的
candidate-facing attempt 报告及相关媒体发布到该条件自己的生成 workspace，供 Repair 使用；
唯一需要删除的是第 0.1.1 和 6 节规定的私有 validation 定义字段。

### 4.2 Authentic physical control / 真实物理控制

Normal evaluated motion must result from actuator control through `data.ctrl` or the selected
model's equivalent actuator input followed by MuJoCo physics stepping. Neither a generated driver
nor a reference driver may create success by directly overwriting `qpos`, `qvel`, body pose,
contacts, sensor values, time, or trusted measurement data.

Direct state restoration is allowed only inside Framework-owned reset/initialization code before a
trial. Kinematic calculation and `mj_forward` may be used for analysis, but they cannot replace
actuator-driven execution of the evaluated effect. The candidate must use the canonical scene and
must not load a simpler substitute model.

Focused checks must reject at least:

- direct candidate writes to `qpos` or `qvel`;
- candidate-owned reset or teleport during an evaluated method;
- a driver that ignores or replaces the selected canonical scene; and
- a driver whose claimed success occurs without actuator/physics-step evidence.

These checks apply to the submitted `driver.py`, not to every vendored dependency or the entire
repository.

**中文辅助说明。** 正常受评运动必须由 `data.ctrl` 或所选模型的等效 actuator input 产生，
并随后推进 MuJoCo physics。无论生成 driver 还是 reference driver，都不得通过直接覆写
`qpos`、`qvel`、body pose、contact、sensor value、time 或可信 measurement data 来制造成功。

只有 Framework 拥有的 trial 前 reset/初始化代码可以直接恢复状态。运动学计算和 `mj_forward`
可以用于分析，但不能替代受评效果的 actuator-driven physics execution。candidate 必须使用
canonical scene，不能加载更简单的替代模型。

聚焦检查至少必须拒绝：candidate 直接写入 `qpos`/`qvel`；在受评方法中自行 reset 或 teleport；
driver 忽略或替换 canonical scene；以及没有 actuator/physics-step 证据却声称成功的 driver。
这些检查只针对提交的 `driver.py`，不要求扫描每个 vendored 依赖或整个仓库。

### 4.3 Verdict independence / 判定独立性

The Harness owns both private suites, trusted-state acquisition, aggregation, and both independent verdicts.
The candidate may observe only the public state declared by its package. It cannot access a
MuJoCo truth handle separate from the approved driver/session interface or read the values used
only for evaluation.

The implementation must report at least these independent facts:

- `pipeline_completed`;
- `dynamic_model_called`;
- `driver_generated_in_run`;
- `capability_validation_executed`;
- `initial_capability_validation_passed`;
- `final_capability_validation_passed`;
- `task_demo_executed`;
- `task_demo_passed`;
- admitted Task Library task count, designed capability count, and covered task count;
- passed and total source-standard clause and private-case counts;
- generation/Repair attempt count; and
- video completeness.

A generic value such as `RUN_COMPLETED`, a zero process exit code, or `structural_ok` cannot stand
in for `final_capability_validation_passed`. A smoke command intended to prove driver-synthesis
success must return non-zero
when its required physical verdict or evidence is incomplete, while retaining the run artifacts.

**中文辅助说明。** Harness 独占两套私有 suite、可信状态采集、结果 aggregation 和两个独立 verdict。
candidate 只能观察机器人包声明的公开 state；不得绕过获准的 driver/session interface 访问额外
MuJoCo truth handle，也不得读取仅供评估使用的数值。

实现至少必须独立报告：`pipeline_completed`、`dynamic_model_called`、
`driver_generated_in_run`、`capability_validation_executed`、
`initial_capability_validation_passed`、`final_capability_validation_passed`、
`task_demo_executed`、`task_demo_passed`、已准入 Task Library task 数、设计 capability 数、已覆盖 task 数、
已通过和总来源标准 clause/私有 case 数、生成/Repair attempt 数，以及视频完整性。

`RUN_COMPLETED`、零进程退出码或 `structural_ok` 等通用值不能替代
`final_capability_validation_passed`。用于证明 driver-synthesis 成功的 smoke 命令，在所要求的
物理 verdict 或证据不完整
时必须返回非零，同时保留运行产物。

### 4.4 Video evidence / 视频证据

Every formal capability-validation and Task Demo case and repetition has its own continuous Framework-controlled video
from post-reset/pre-invocation state through terminal observation, failure, exception, or timeout.
The manifest records robot/configuration, run, attempt, requirement/trial, simulation start/end,
frame count, video path, and completion result.

Candidate code, TGCD, IVC, STUDY/GENERATE, and Repair cannot read or control the recorder.
Video is audit evidence, not a verdict source; human visual judgment cannot replace the structured
Harness criterion. A missing, zero-frame, undecodable, truncated, or interval-incomplete required
video invalidates that trial as evidence failure. It does not become a candidate pass.

**中文辅助说明。** 每个正式 capability-validation 和 Task Demo case 的每次 trial 和 repetition
都必须有独立、连续、由
Framework 控制的视频。录像从 reset 后、方法调用前的状态开始，持续到终止 observation、失败、
exception 或 timeout。manifest 必须记录 robot/configuration、run、attempt、requirement/trial、
仿真起止时间、帧数、视频路径和完成结果。

candidate 代码、TGCD、IVC、STUDY/GENERATE 和 Repair 均不得读取或控制 recorder。
视频是审计证据，不是判定来源；人工视觉判断不能替代 Harness 的结构化 criterion。正式所需
视频如果缺失、零帧、无法解码、被截断或没有覆盖完整区间，则该 trial 因证据失败而无效，绝不能
转化为 candidate pass。

---

## 5. Evidence semantics / 证据语义

The following categories describe what one run demonstrates. They are reporting terms, not a
persistent state machine.

**中文辅助说明。** 以下类别描述单次运行能够证明什么。它们是报告术语，不是持久状态机。

| Evidence category | Required facts | Supported claim | Unsupported claim |
|---|---|---|---|
| Reference calibration | Reviewed reference driver, real MuJoCo, complete `capability_validation_suite.json` Harness verdicts, and complete videos | The selected assets, controller baseline, complete capability-validation route, Harness, and recording path are feasible | Any model generated the driver or any Task Demo passed |
| Shakedown generation-condition executed | Source-backed 20+ task snapshot, real-model TGCD design of 3–10 capability contracts without a pre-authored effect policy, implementation-blind IVC `capability_validation_suite.json`, named skeleton-assisted or from-scratch condition, real model identities/calls, model-generated frozen `driver.py`, condition-appropriate STUDY/Generate trace, and real MuJoCo capability validation reaching a terminal verdict | That capability design and generation condition executed end to end in the declared shakedown | The capability requirements passed, formal B1 executed, Task Demo ran, or the other condition executed |
| B1 generation-condition executed | Manifest-pinned prior-designed Driver interface and complete fixed `capability_validation_suite.json`, named skeleton-assisted or from-scratch condition, real model identities/calls beginning at STUDY, model-generated `driver.py`, condition-appropriate STUDY/GENERATE trace, and real MuJoCo capability validation reaching a terminal verdict | That fixed-input B1 generation condition executed end to end | The capability requirements passed, a reference calibration ran, TGCD or IVC ran in B1, Task Demo ran, or the other condition executed |
| Historical cross-run Experience closure executed | A protocol-authorised source run reaches a trusted Task Demo terminal outcome; the model writes exactly `observation`, `lesson`, `recommendation`, `scope`, and `public_evidence`; Framework appends `provenance` and `outcome`; a human records accept/reject with a nonempty reason without editing; and, when accepted, a separately authorised later run loads the exact frozen snapshot | The declared cross-run mechanism and Experience handoff executed; the loaded snapshot is effective in the later run | Improvement, causal Experience effect, matched-control estimate, autonomous self-improvement, or global generalisation |
| Experiment 3 declared cohort executed | Exactly eleven configurations × `r01`--`r03`; Sonnet 4.6 only; skeleton-assisted only; fresh STUDY/TGCD/IVC per cell; empty Experience; at most three frozen Drivers; whitelist-limited ReCAP Task Demo then stop; no Evolution | The declared 33-cell Direct-MuJoCo construction route produced the reported per-cell evidence | A morphology effect, Experience effect, model ranking, from-scratch comparison, or universal robot support |
| Single-robot condition success | Dynamic condition evidence plus every case in the complete capability validation suite passes within that condition's declared attempt budget | The generated robot-specific driver passed capability admission for that robot, condition, and run | Any Task Demo passed, or the paired condition, all-robot round, or SDK path succeeded |
| Task Demo executed | A final source/import-valid frozen Driver, a nonempty Framework-derived nominal-plus-boundary capability whitelist, protocol-selected Task Demo cases, real ReCAP capability calls, a separate trusted-Harness verdict, and complete required videos | The selected tasks reached a truthful Task Demo outcome using only the validated capability subset | The whole Driver passed capability validation, every Task Library task passed, or capability-interface use succeeded without ReCAP |
| Per-robot two-condition experiment completed | Both generation conditions reach capability-validation terminal verdicts for one declared robot using the same sealed `capability_design.json`, `capability_validation_suite.json`, and declared experiment configuration | That robot's two-cell Direct-MuJoCo comparison executed | Either cell passed, the complete cohort ran, or either Task Demo ran |
| Experiment 3 33-cell cohort evidence | All 33 declared robot-by-replicate cells use Sonnet 4.6, skeleton-assisted generation, empty Experience, fresh STUDY/TGCD/IVC, at most three frozen Drivers, terminal whitelist-limited Task Demo outcome or truthful not-run reason, and complete evidence | The exact descriptive construction cohort executed under the declared route | Every cell passed, a morphology effect, an Experience effect, a backbone comparison, or universal applicability |
| Experiment 3 descriptive cohort summary | Per-cell outcomes are retained for all 11 configurations and 3 replicates, with morphology shown only as a package label | The reported configuration-level and descriptive morphology-stratified outcomes | Causal morphology effect, model superiority, SDK fidelity, hardware validity, or sim-to-real |
| SDK-grounded extension evidence | Real SDK application logic and robot-specific Translation execute bidirectionally with MuJoCo | The named SDK-extension route executed | Hardware equivalence or mainline replacement |

**中文辅助表。**

| 证据类别 | 必须具备的事实 | 可以支持的结论 | 不能支持的结论 |
|---|---|---|---|
| 参考校准 | 经审查的 reference driver、真实 MuJoCo、完整 `capability_validation_suite.json` 的 Harness verdict 和完整视频 | 所选资产、controller baseline、完整 capability-validation 路径、Harness 和录像路径可行 | driver 由任何模型生成，或任何 Task Demo 已通过 |
| Shakedown 生成条件已执行 | 有来源的 20+ task 快照、没有预写 effect policy 的真实模型 TGCD 三至十项 capability contract 设计、implementation-blind IVC `capability_validation_suite.json`、明确的 skeleton-assisted 或 from-scratch 条件、真实模型身份和调用、模型生成并封存的 `driver.py`、符合该条件的 STUDY/Generate trace，以及到达最终 verdict 的真实 MuJoCo capability validation | capability 设计及该生成条件已在声明的 shakedown 中完成端到端执行 | capability 要求已通过、正式 B1 已执行、Task Demo 已运行，或另一条件已执行 |
| B1 生成条件已执行 | Manifest 固定的前序实验 Driver interface 与完整固定 `capability_validation_suite.json`、明确的 skeleton-assisted 或 from-scratch 条件、从 STUDY 开始的真实模型身份与调用、模型生成的 `driver.py`、符合该条件的 STUDY/GENERATE trace，以及到达最终 verdict 的真实 MuJoCo capability validation | 固定输入的 B1 生成条件已完成端到端执行 | capability 要求已通过、运行了 reference calibration、B1 中运行了 TGCD 或 IVC、Task Demo 已运行，或另一条件执行 |
| 历史跨运行 Experience closure 已执行 | 经协议授权的 source run 到达可信 Task Demo 终态；模型只写 `observation`、`lesson`、`recommendation`、`scope`、`public_evidence`；Framework 添加 `provenance` 与 `outcome`；人工以非空 reason 记录 accept/reject 且不编辑；接受时，经另行授权的 later run 原样加载 frozen snapshot | 声明的跨运行机制和 Experience handoff 已执行，且加载后的 snapshot 在 later run 生效 | improvement、因果 Experience effect、matched control estimate、自主 self-improvement 或全局推广 |
| Experiment 3 声明 cohort 已执行 | 精确十一配置×`r01`--`r03`；仅 Sonnet 4.6、仅 skeleton-assisted；每 cell 新鲜 STUDY/TGCD/IVC；空 Experience；最多三个 frozen Driver；白名单限定的 ReCAP Task Demo 后停止；Evolution 禁用 | 声明的 33-cell Direct-MuJoCo construction route 产生了逐 cell evidence | morphology effect、Experience effect、model ranking、from-scratch comparison 或普遍 robot support |
| 单机器人条件成功 | 具备动态条件证据，且完整 capability validation suite 中每个 case 均在该条件声明的 attempt 预算内通过 | 该机器人、该生成条件和该 run 生成的 robot-specific driver 通过 capability 准入 | 任何 Task Demo 已通过，或配对条件、全机器人轮次或 SDK 路径成功 |
| Task Demo 已执行 | 最终 source/import 合格的 frozen Driver、Framework 得出的非空 nominal+boundary capability 白名单、协议选定 Task Demo cases、真实 ReCAP capability 调用、独立可信 Harness verdict 和完整必需视频 | 所选 tasks 只用已验证 capability 子集得到真实 Task Demo outcome | 整套 Driver 通过 capability validation、Task Library 全部 tasks 通过，或没有 ReCAP 时 capability-interface use 成功 |
| 单机器人双条件实验已完成 | 某一声明机器人在相同封存 `capability_design.json`、`capability_validation_suite.json` 和实验配置下，让两种生成条件都到达 capability-validation 最终 verdict | 该机器人的两个 Direct-MuJoCo cell 已执行 | 任一 cell 已通过、完整 cohort 已运行，或任一 Task Demo 已运行 |
| Experiment 3 33-cell cohort evidence | 全部 33 个 robot×replicate cell 使用 Sonnet 4.6、skeleton-assisted、空 Experience、新鲜 STUDY/TGCD/IVC、最多三个 frozen Driver，并保留白名单限定的终态 Task Demo 或真实 not-run reason 与完整 evidence | 精确描述性 construction cohort 按声明 route 执行 | 每个 cell 都通过、morphology effect、Experience effect、backbone comparison 或普遍适用性 |
| Experiment 3 描述性 cohort summary | 11 个 configuration 和 3 个 replicate 的逐 cell outcome 全部保留，morphology 只作为 package label 展示 | 按 configuration 和描述性 morphology strata 报告 outcome | 因果 morphology effect、model superiority、SDK fidelity、hardware validity 或 sim-to-real |
| 基于真实 SDK 的扩展证据 | 真实 SDK 应用逻辑和机器人专用 Translation 与 MuJoCo 双向执行 | 指定的 SDK 扩展路径已执行 | 与硬件等效，或可替代主线 |

A dynamic run must retain enough evidence to verify its declared protocol. A
skeleton-assisted run shows trusted-skeleton inspection and a generated driver; a
fresh TGCD/IVC cell shows model-authored design and implementation-blind compilation; an
Experience-enabled later run shows the unchanged public proposal load event. The run shows an
attempted real local MuJoCo probe and proves that the final frozen Driver was generated in that run. At minimum,
the concise run summary records:

- run ID and code version;
- robot/configuration, package versions, Task Library snapshot, admitted task count, and source
  coverage;
- model provider, exact model identifier, and generation condition;
- fixed Driver or cell-local Capability Design, capability-level pass-standard, and validation-suite identities;
- TGCD/IVC where declared, STUDY, GENERATE, capability validation, Repair, Task Demo, and Evolution outcomes;
- attempt count, separate initial/final capability-validation verdicts, and the separate Task Demo verdict;
- per-capability, source-standard-clause, and private-case results and video completeness; and
- any infrastructure or candidate failure reason.

Raw media need not be committed to Git. A concise tracked evidence record must point to the retained
run package and must not invent a success when the underlying run is ignored or unavailable.
Artifact hashes, signing, and an evidence registry are not required.

**中文辅助说明。** 动态运行必须保留足够证据，以核验声明的生成条件：skeleton-assisted run
需要证明模型检查了可信 skeleton；from-scratch run 需要证明提交源码没有导入、复制或调用 skeleton
实现。两者都必须证明尝试了真实本地 MuJoCo probe，并在本次运行中生成最终提交的 driver。简洁
运行摘要至少记录：run ID 和代码版本；机器人/配置及 package 版本、Task Library 快照、已准入
task 数量和来源覆盖；模型 provider、准确模型标识和 generation condition；固定 Driver 或 cell-local
Capability Design、capability-level pass standard 和 validation suite identity；在 protocol 要求时的
TGCD/IVC、STUDY、GENERATE、Capability Validation、Repair、Task Demo 和 Evolution 结果；attempt 次数、分开的首次
与最终 capability-validation verdict，以及单独的 Task Demo verdict；
逐 capability、来源标准 clause 和私有 case 结果及视频完整性；基础设施或 candidate 失败原因。

原始媒体无需提交到 Git。受版本控制的简洁证据记录必须指向保留的运行包；当底层运行被忽略或
不可获取时，不得虚构成功。无需 artifact hash、签名或证据 registry。

### 5.1 Current evidence boundary / 当前证据边界

As of revision `0.20.0`, tracked package/reference runs, Demo2 runs, earlier
shakedowns, and any `formal=false` canary are diagnostic or historical evidence
only. A diagnostic IVC may explicitly skip its reference positive control; that
skip is recorded and the run cannot enter a formal denominator. Experiment 1b
is completed historical evidence and is neither rerun nor modified by this
round. Retained Experiment 2 files and results are historical and out of scope;
this Authority authorises no corrected Experiment 2 dispatch. Experiment 3
remains at zero of thirty-three formal cells. This round may prepare and
preflight its exact manifest and runner, but passing preflight does not
authorise model dispatch: all 33 formal cells await separate user approval.
No retained diagnostic, reference result, proposal, or preflight may be
relabeled as an Experiment 1a or Experiment 3 result.

**中文辅助说明。** 在修订 `0.20.0` 下，现有 package/reference run、Demo2、早期 shakedown 与
任何 `formal=false` canary 都只属于诊断或历史证据。诊断 IVC 可以显式跳过 reference 正控，但必须
记录该事实，且不得进入正式 denominator。Experiment 1b 已完成，本轮不重跑也不修改。保留的
Experiment 2 文件和结果属于历史且超出本轮范围；本 Authority 不授权 corrected Exp2 dispatch。
Experiment 3 仍为 0/33，本轮只可准备和 preflight 精确 manifest/runner；preflight 通过不等于授权
模型请求，33 个正式 cell 均等待用户另行批准。任何诊断、reference 结果、proposal 或 preflight
都不得改写成 Experiment 1a 或 Experiment 3 的正式结果。

### 5.2 Non-formal DeepSeek mainline canary / 非正式 DeepSeek 主线 canary

The only model run authorised by this implementation round is one independent
DeepSeek diagnostic using SO-101 `1.0.4`, skeleton-assisted generation, empty
Experience, and `formal=false`. It executes the real
STUDY→TGCD→IVC→Generate/Repair→ReCAP→Evolution mainline and calls no other LLM.
The diagnostic may explicitly skip IVC reference positive control. It need not
pass the whole capability suite, but its final frozen Driver must pass both the
nominal and calibrated-boundary case for at least one capability. ReCAP receives
only that final whitelist and must make at least one real capability call. Task
Demo may FAIL, but it must execute real MuJoCo physics and retain a trusted
Harness verdict plus required video. Evolution then produces either the exact
five-field proposal or an explicit no-lesson/failure outcome. The source run
stops after that Evolution outcome is shown to the user. A later Experience-enabled run is
not authorised until the user explicitly accepts the unedited proposal with a
nonempty reason; rejection ends the diagnostic. Neither run enters an
Experiment 1a or Experiment 3 denominator.

**中文辅助说明。** 本轮唯一获准的模型运行是一条独立 DeepSeek 诊断：SO-101 `1.0.4`、
skeleton-assisted、空 Experience、`formal=false`，真实执行 STUDY→TGCD→IVC→
Generate/Repair→ReCAP→Evolution，且不调用其他 LLM。诊断可显式跳过 IVC reference 正控；
不要求整套 capability 通过，但最终 frozen Driver 至少有一项 capability 的 nominal 与
calibrated-boundary 都通过。ReCAP 只获得该最终白名单，且至少真实调用一次 capability。Task Demo
可以 FAIL，但必须真实执行 MuJoCo、保留可信 Harness verdict 和必需视频。Evolution 后向用户展示
五字段 proposal 或明确 no-lesson/failure，然后 source run 停止；只有用户以非空理由明确 accept
未编辑 proposal 后，才可启动独立 Experience-enabled later run，reject 则结束。两轮均不进入
Experiment 1a 或 Experiment 3 denominator。

---

## 6. All-robot mainline readiness and experiment gates / 全机器人主线就绪与实验门槛

The following are construction obligations, not optional cleanup after the mainline appears to run. Mainline
implementation work must satisfy each obligation when the affected component or path is introduced:

1. establish the self-contained mainline project and declared reproducible environment before using
   a run as evidence;
2. admit only complete versioned robot packages with applicable source-backed tasks, scoring-clause
   lineage, public observations, private instances/bindings/guards, and local asset closure;
3. implement real-model STUDY, TGCD, and implementation-blind IVC without a fixed task projection,
   pre-authored effect catalogue, task-to-effect allowlist, or candidate-visible private material;
4. derive three to ten task-neutral capabilities and exactly one nominal plus one calibrated-boundary
   IVC case per capability, then give ReCAP only the final Driver's double-pass capability whitelist;
5. build private-suite isolation, candidate-process isolation, canonical-session enforcement,
   anti-teleport checks, actuator-plus-physics-step evidence, and per-step contact-penetration evidence
   into the Harness path itself, with independent task-metric and physical-integrity verdicts;
6. expose to Repair only the candidate-facing attempt report and media projection, redact private
   validation definitions, freeze and validate at most three source/import-valid Drivers, preserve the
   previous frozen Driver after a rejected Repair, keep the suite unchanged, and never feed the same-run
   Task Demo report back into Repair;
7. record one complete Framework-controlled video and manifest entry for every required
   capability-validation or Task Demo case repetition, with incomplete required media invalidating
   that trial's evidence;
8. report pipeline execution, model calls, in-run generation, capability validation, the final-only
   capability whitelist, Task Demo execution/verdict, task-metric verdict, physical-integrity verdict,
   case counts, frozen attempts, development rejections, and video completeness separately;
9. keep package reference Drivers hidden from TGCD, candidates, Repair, and ReCAP. Their formal IVC
   positive-control role does not make them candidate input; a `formal=false` diagnostic may explicitly
   skip the control and remains non-formal; and
10. keep Evolution terminal and non-blocking, restrict model output to the five public fields, add
    Framework provenance/outcome, require accept/reject plus a nonempty reason without editing, and
    expose an accepted snapshot only to a separately launched later run's STUDY/TGCD/Generate/Repair.

These obligations govern all new mainline code even when the corresponding Demo2 defect is retained as a
historical regression fixture. Passing a later acceptance gate does not excuse bypassing the
construction boundary while implementing an earlier component.

**中文辅助说明。** 以下内容是主线搭建义务，不是系统看似能运行之后才选择处理的清理项。
实现每个相关组件或路径时，必须同步满足对应要求：

1. 在把任何 run 用作证据前，先建立自包含的主线项目和已声明、可复现的运行环境；
2. runnable index 只准入具备适用有来源 tasks、完整 scoring-clause lineage、public observations、
   私有 instances/bindings/guards 与本地 asset closure 的版本化 package；
3. 实现真实模型 STUDY、TGCD 和对实现不可见的 IVC，不得使用固定 task projection、预写 effect
   catalogue、task→effect allowlist 或 candidate-visible private material；
4. 生成三至十项 task-neutral capability，每项恰好一个 nominal 与一个 calibrated-boundary IVC
   case；ReCAP 只获得最终 Driver 的双 case 通过白名单；
5. 在 Harness 路径内直接实现私有 suite 隔离、candidate 进程隔离、canonical session、
   anti-teleport、actuator+physics-step 与逐 step 接触穿透证据，并独立报告 task metric 与物理完整性；
6. Repair 只接收 candidate-facing attempt 报告和媒体投影，删除私有 validation 定义；最多封存并
   验证三个 source/import 合格 Driver，被拒 Repair 后保留上一 frozen Driver，suite 不变，且本轮
   Task Demo 报告绝不回传给 Repair；
7. 每个必需 capability-validation 或 Task Demo case repetition 都有独立、完整、Framework 控制的
   视频和 manifest；必需媒体不完整时该 trial 证据无效；
8. 分开报告 pipeline 执行、模型调用、本次生成、capability validation、final-only capability
   白名单、Task Demo 执行/verdict、task metric、physical integrity、case 数、frozen attempts、
   development rejections 与视频完整性；
9. package reference Driver 对 TGCD、candidate、Repair 与 ReCAP 保持隐藏；其正式 IVC 正控角色
   不使其成为 candidate 输入，`formal=false` diagnostic 可显式跳过且仍不得计正式证据；
10. Evolution 位于 terminal verdict 后且不阻塞；模型只写五个公开字段，Framework 添加
    provenance/outcome；人工只能以非空理由 accept/reject 且不得编辑，accepted snapshot 只进入
    另行启动 later run 的 STUDY/TGCD/Generate/Repair。

即使对应 Demo2 缺陷被保留为历史 regression fixture，这些义务仍约束所有新主线代码。后续
验收门槛通过，不能成为搭建早期组件时绕开上述边界的理由。

### 6.1 Ready for the revision-0.20 mainline and Experiment 3 preflight / 修订 0.20 主线与 Experiment 3 预检就绪

`autoadapter/` remains the canonical mainline. This round is ready only when a
zero-model preflight confirms all of the following input and path prerequisites:

1. the environment declared by `autoadapter/pyproject.toml` installs and focused mainline checks run
   without importing or resolving project-authored code or assets from a sibling tree;
2. every one of the eleven configurations in Section 1.3 resolves from a complete versioned package
   containing Morphology, applicable source-backed Tasks and criteria, public observations, private
   instances/bindings/guards, a complete local asset closure, and a trusted skeleton; every exact ID is
   in `libraries/robots/index.json`;
3. focused checks cover artifact completion/correction, final-turn acceptance, Driver freeze/counting,
   condition-specific file visibility, credential-free Python/MuJoCo worker recovery, TGCD/IVC recovery,
   candidate task-ID prohibition, IVC implementation blindness, ReCAP budgets, Experience visibility,
   all eleven public-observation contracts, and the 33-cell manifest cross-product;
4. Experiment 1a's fixed SO bundle is rebound to SO-101 `1.0.4`, its Go2 bundle remains frozen, and its
   runner/check-only path reflects the 16/22/40/22/20 file-workflow budgets without sending a model request;
5. Experiment 3's manifest selects exactly the eleven Section 1.3 configurations and `r01`--`r03`
   (33 cells), uses Sonnet 4.6 and skeleton assistance only, starts every cell with empty Experience,
   runs fresh STUDY/TGCD/IVC, permits at most three frozen Drivers, runs whitelist-limited ReCAP Task
   Demo then stops, and disables Evolution;
6. the recorder and video paths have passed focused checks and are configured to require one complete
   Framework-controlled video and matching manifest entry for every executed capability-validation
   and Task Demo case, with missing media invalidating formal evidence rather than being relabelled as
   model synthesis; and
7. no formal Experiment 1a or Experiment 3 model request has been sent. A completed preflight records
   readiness only; formal dispatch still requires separate user approval.

Optional backup robots may remain in `research/robots/index.json`, and focused
package checks or canaries may run independently. Those diagnostics do not
waive any item above and cannot replace, omit, or relabel a declared formal cell.

**中文辅助说明。** `autoadapter/` 仍是 canonical 主线。本轮只有零模型 preflight 同时确认以下
input 与 path 前置条件，才算准备完成：

1. `autoadapter/pyproject.toml` 声明的环境可以安装，主线聚焦检查不从同级源码树导入或解析任何
   项目自编代码和资产；
2. 第 1.3 节十一个配置都能从完整版本化 package 解析 Morphology、适用有来源 Tasks/criteria、
   public observations、私有 instances/bindings/guards、完整本地 asset closure 与可信 skeleton，
   且所有精确 ID 都进入 `libraries/robots/index.json`；
3. focused checks 覆盖 artifact 完成/纠错、final-turn acceptance、Driver freeze/counting、条件化文件
   可见性、无 credential Python/MuJoCo worker recovery、TGCD/IVC recovery、candidate task-ID 禁止、
   IVC implementation blindness、ReCAP budgets、Experience visibility、11 项 public-observation
   contract 与 33-cell manifest 笛卡尔积；
4. Experiment 1a 固定 SO bundle 重绑 SO-101 `1.0.4`，Go2 bundle 保持 frozen，runner/check-only
   反映 16/22/40/22/20 文件工作流预算且不发送模型请求；
5. Experiment 3 manifest 精确选择十一个配置与 `r01`--`r03`（33 个 cell），仅用 Sonnet 4.6、
   skeleton-assisted 与空 Experience；每 cell 新鲜 STUDY/TGCD/IVC、最多三个 frozen Driver、
   白名单限定 ReCAP Task Demo 后停止，并禁用 Evolution；
6. recorder 与 video 路径已通过聚焦检查，并被配置为每个实际执行的 capability-validation 与
   Task Demo case 都必须保留一份完整 Framework 控制视频及匹配 manifest entry；缺失媒体会使正式
   evidence 无效，不能改写成 model synthesis；
7. 没有发送正式 Experiment 1a 或 Experiment 3 模型请求；preflight 完成只记录准备就绪，正式
   dispatch 仍需用户另行批准。

可选备份机器人可以留在 `research/robots/index.json`；package check 或 canary 可独立运行，但
不能免除上述任何一项，也不能替换、省略或改写正式 cell。

### 6.2 Experiment 3 cohort completion / Experiment 3 cohort 完成

If and only if separately approved for dispatch, Experiment 3 is complete when
all 33 manifest-declared cells have truthful terminal records. Every cell uses
its exact one of eleven package IDs, `r01`--`r03`, Sonnet 4.6,
skeleton-assisted generation, empty Experience, fresh STUDY/TGCD/IVC, no more
than three frozen Drivers, a whitelist-limited ReCAP Task Demo or an explicit
not-run reason, required Framework/Harness evidence and videos, and no
Evolution. The report retains all 33 outcomes, separates the six SO/Go
reference-seen controls from the 27 transfer cells, treats morphology only as
a descriptive package label, and makes no morphology-effect, model-ranking,
Experience-effect, from-scratch, quadruped-transfer, SDK, hardware, or universal
support claim. Completion is distinct from success: failures remain in the
denominator. Experiment 2 remains historical and out of scope under revision
`0.20.0`; no Experiment 2 completion or dispatch rule is active here.

**中文辅助说明。** 只有用户另行批准正式 dispatch 后，Experiment 3 才可能完成。完成要求全部
33 个 manifest cell 都有真实终态：精确 11 个 package ID×`r01`--`r03`、Sonnet 4.6、
skeleton-assisted、空 Experience、每 cell 新鲜 STUDY/TGCD/IVC、最多三个 frozen Driver、
白名单限定 ReCAP Task Demo 或明确 not-run reason、完整 Framework/Harness 证据和必需视频、
Evolution 禁用。报告区分 6 个 SO/Go reference-seen control 与 27 个 transfer cell；morphology
只作描述标签，不声称 morphology effect、model ranking、Experience effect、from-scratch、
quadruped transfer、SDK、hardware 或 universal support。完成不等于成功，失败仍留在 denominator。
修订 `0.20.0` 下 Experiment 2 属于历史且超出范围，本节不授权其 completion 或 dispatch。

> **Historical-only remainder / 以下仅历史文本：** Everything after
> this marker through the end of Section 6.2 is retained solely to interpret
> pre-0.20 plans and is non-normative under revision `0.20.0`.

1. Experiment 2 has one SO-101 source run with empty Experience that reaches
   the real Sonnet skeleton-assisted route through the Task Demo stage, records
   an admitted-driver demo or a truthful `not-run` outcome with a nonempty
   reason, and then makes one terminal Opus 5 Evolution proposal or records an
   explicit terminal transport failure;
2. the proposal retains the Opus-written observation/lesson/recommendation/
   scope/evidence fields, with `evidence` restricted to public material, and
   Framework-assigned source robot,
   generation condition, and positive/negative terminal-outcome label; a human
   records exactly `accept` or `reject` with a nonempty reason without editing,
   merging, or replacing content;
3. after that fixed disposition, exactly one later run is manually launched in
   a fresh independent workspace and process. An accepted proposal plus its
   frozen reviewed snapshot becomes globally eligible; it must be loaded
   unchanged by the later Sonnet 4.6 Experience-enabled run to become
   effective. A rejected proposal or infrastructure failure is reported as a
   blocked formal Experience-enabled closure, not silently converted into a
   result;
4. the proposal is never a same-run, same-round, or pre-disposition input, and
   the source and later outcomes are reported separately without an improvement
   or causal Experience claim;
5. Experiment 3 has exactly the eleven Section 1.3 configuration IDs crossed
   with `r01`--`r03`, for 33 declared cells, and no hidden substitute or omitted
   configuration;
6. every Experiment 3 cell uses Sonnet 4.6, skeleton assistance only, empty
   Experience, fresh end-to-end TGCD and implementation-blind IVC, at most
   three submitted driver attempts, and its own Task Demo after an admitted
   final driver. The cell stops after Task Demo and has Evolution disabled;
7. every Experiment 3 cell has a truthful terminal capability/Task Demo
   outcome or an explicit infrastructure-blocker/not-run reason, with complete
   Framework/Harness evidence and required videos. A generated-driver failure
   is named as such; a package, simulator, Harness, transport, or video
   prerequisite failure remains an infrastructure blocker;
8. Experiment 3 reports all 33 per-cell outcomes and the declared denominator
   of 33. Morphology is shown only descriptively as a package/configuration
   label or stratum; no morphology effect, model comparison, Experience
   effect, or from-scratch comparison is computed or claimed;
9. the Direct-MuJoCo mainline remains independent of the SDK extension and
   repository siblings, and no formal evaluation downloads unpinned code,
   data, standards, or robot assets; and
10. any claim of completion names missing or blocked records rather than
    treating a partial cohort, diagnostic canary, or reference-driver result
    as formal evidence.

Completion is distinct from success. Experiment 2 can report a mechanism
closure only when the accepted public proposal is loaded by the next
independent later run; an accepted frozen snapshot is globally eligible before
that load and becomes effective at the load, while a rejection is a recorded
disposition and a blocked formal Experience-enabled closure. Experiment 3 can report execution of its
33-cell denominator when every cell has a truthful terminal record, including
an infrastructure blocker, but it may report only the per-cell and descriptive
cohort outcomes allowed above. Neither protocol supports an improvement claim.

**中文辅助说明。** 只有各自的前瞻性记录满足以下边界，项目才能声明委派协议完成：

1. Experiment 2 有一个空 Experience 的 SO-101 source run，真实执行 Sonnet
   skeleton-assisted 路径直至 Task Demo stage；有准入 driver 时执行 demo，无准入 driver 时以非空
   reason 记录真实 `not-run`；随后由 Opus 5 执行一次 terminal Evolution proposal，或明确记录
   terminal transport failure；
2. proposal 保留 Opus 写入的 observation/lesson/recommendation/scope/evidence 字段，其中
   `evidence` 仅限公开材料；proposal 同时保留 Framework 根据 verdict facts 赋值的 source robot、
   generation condition 和 positive/negative
   terminal-outcome label；人工严格记录一次 `accept` 或 `reject` 及非空 reason，不得编辑、合并或替换内容；
3. 固定 disposition 后手动启动且仅启动一个全新、独立 workspace/process 的 later run。接受的 proposal
   加 frozen reviewed snapshot 后 globally eligible；later Sonnet 4.6 Experience-enabled run 必须
   原样加载该 snapshot 才会 effective。拒绝或基础设施失败必须报告为 formal Experience-enabled
   closure blocked，不能静默变成结果；
4. proposal 不能作为同 run、同 round 或 disposition 前的 input；source 与 later 分开报告，不能声称
   improvement 或因果 Experience effect；
5. Experiment 3 精确使用第 1.3 节十一个 configuration ID 与 `r01`--`r03` 的笛卡尔积，共 33 个
   cell，不得隐藏替换或省略 configuration；
6. 每个 Experiment 3 cell 仅用 Sonnet 4.6、仅 skeleton assistance、空 Experience、新鲜端到端
   TGCD 和 implementation-blind IVC、最多三次 submitted-driver attempt；准入 final driver 后运行
   自己的 Task Demo，随后停止且禁用 Evolution；
7. 每个 cell 都有真实 terminal capability/Task Demo outcome，或明确 infrastructure-blocker/not-run
   reason，并具备完整 Framework/Harness evidence 和必需视频；生成 driver 失败要点名，package、simulator、
   Harness、transport 或视频前置失败保持为 infrastructure blocker；
8. Experiment 3 报告全部 33 个逐 cell outcome 和 denominator 33；morphology 只作为 package/configuration
   label 或 stratum 描述，不计算或声称 morphology effect、model comparison、Experience effect 或
   from-scratch comparison；
9. Direct-MuJoCo 主线仍独立于 SDK 扩展和仓库同级目录，正式 evaluation 不下载未固定的 code、data、
   standards 或 robot assets；
10. 任何完成声明都必须点名缺失或 blocked record，不能把 partial cohort、diagnostic canary 或
    reference-driver result 当作 formal evidence。

“完成”和“成功”必须区分。Experiment 2 只有在 accepted public proposal 被下一独立 later run
加载时，才能报告 mechanism closure；accept 加 frozen snapshot 后 proposal 已 globally eligible，
在 load 时才 effective；reject 只是已记录的 disposition，并意味着 formal Experience-enabled closure
blocked。Experiment 3 在每个 cell 都有真实 terminal record（包括
infrastructure blocker）时，可以报告 33-cell denominator 的执行情况，但只能报告上述允许的逐 cell
和描述性 cohort outcome。两项实验都不支持 improvement claim。

---

## 7. SDK extension contract / SDK 扩展合同

SDK integration is an independent research and engineering extension under `extensions/sdk/`.
It may retain SDK dossiers, real upstream package pins, robot-specific device/transport hooks,
Translation implementations, integration checks, and SDK-specific tests from the former
General Demo.

**中文辅助说明。** SDK 集成是位于 `extensions/sdk/` 下的独立研究与工程扩展。它可以保留
原 General Demo 中的 SDK dossier、真实上游 package pin、机器人专用 device/transport hook、
Translation 实现、集成检查和 SDK 专用测试。

Only a run whose control and observation traffic actually follows this path may be called
SDK-grounded:

```text
generated or fixed extension client
        -> pinned real SDK application layer
        -> robot- and SDK-specific Translation Layer
        -> MuJoCo actuator control and physics
        -> Translation Layer
        -> real SDK-compatible observation path
```

```text
生成的或固定的扩展 client
        -> 固定版本的真实 SDK application layer
        -> 机器人和 SDK 专用的 Translation Layer
        -> MuJoCo actuator control 和 physics
        -> Translation Layer
        -> 与真实 SDK 兼容的 observation path
```

An API-compatible SDK mock cannot substitute for the real SDK in SDK-grounded evidence. Test
doubles remain allowed in explicitly named tests and fixtures. The extension must not claim
hardware equivalence or sim-to-real merely because the real SDK application package executed.

**中文辅助说明。** 只有控制和 observation 流量确实经过上述路径的 run，才能称为 SDK-grounded。
在这类证据中，与 SDK API 兼容的 mock 不能替代真实 SDK；仅在明确命名的 test 和 fixture 中允许
test double。扩展不能仅因真实 SDK application package 已执行，就声称与硬件等效或实现了
sim-to-real。

SDK extension failure, missing platform support, or incomplete Translation does not block the
Direct-MuJoCo mainline. SDK success does not upgrade or replace mainline evidence. Extension
results are reported separately and identify the exact SDK, Translation, robot configuration,
runtime, and MuJoCo route exercised.

**中文辅助说明。** SDK 扩展失败、缺少平台支持或 Translation 不完整，不阻塞 Direct-MuJoCo
主线。SDK 成功也不能升级或替代主线证据。扩展结果必须单独报告，并注明所执行的确切 SDK、
Translation、机器人配置、runtime 和 MuJoCo 路径。

The project must not build a universal robot API, dynamic plugin registry, generic SDK lifecycle
platform, or shared cross-robot Translation abstraction solely for future extensibility. Reuse a
small explicit mainline seam only when a current extension run needs it.

**中文辅助说明。** 项目不得仅为未来扩展性而构建 universal robot API、dynamic plugin
registry、generic SDK lifecycle platform 或跨机器人共享的 Translation abstraction。只有
当前扩展 run 确实需要时，才复用一个小而明确的主线接口。

---

## 8. Explicitly excluded scope / 明确排除的范围

The current project must not add or expand:

- project-wide lifecycle, readiness, admission, promotion, freeze, or experiment status systems;
- artifact hashing, signing, attestations, provenance chains, registries, or governance services;
- exhaustive schemas, defensive abstraction layers, plugin systems, or future-robot frameworks;
- broad regression, fuzz, property, coverage-driven, or adversarial-security test programs;
- silently omitting, replacing, or relabelling a declared cohort robot in a formal declared-cohort round;
- reference-driver substitution in a dynamic run;
- candidate-visible private suites, exact criteria, private definition-bearing reports, or
  privileged verdict state; candidate-facing Repair reports remain required by Sections 0.1.1 and 6;
- direct state teleport as a substitute for actuator control and physics;
- video or model self-report as a substitute for the Harness verdict; or
- hardware, perception, SDK-fidelity, or sim-to-real claims from Direct-MuJoCo evidence.

The minimum focused package, simulator-integrity, and shared-Harness checks for every declared
cohort robot take priority over broader cleanup. Hidden reference diagnostics and per-robot canaries
may run as packages become ready, but the formal Experiment 3 cohort waits for the complete declared
cohort.

**中文辅助说明。** 当前项目不得新增或扩展：

- 项目级 lifecycle、readiness、admission、promotion、freeze 或 experiment status 系统；
- artifact hash、签名、attestation、provenance chain、registry 或 governance service；
- 穷尽式 schema、防御性 abstraction layer、plugin system 或面向未来机器人的 framework；
- 广泛的 regression、fuzz、property、coverage-driven 或 adversarial-security 测试计划；
- 在正式声明 cohort 轮次中静默省略、替换声明 cohort 机器人，或改写其身份；
- 在 dynamic run 中用 reference driver 替换生成 driver；
- 向 candidate 暴露 private suite、精确 criterion、含私有定义的报告或 privileged verdict
  state；第 0.1.1 和 6 节要求的 candidate-facing Repair 报告仍必须提供；
- 用直接 state teleport 替代 actuator control 和 physics；
- 用视频或模型自述替代 Harness verdict；
- 根据 Direct-MuJoCo 证据声称 hardware、perception、SDK fidelity 或 sim-to-real 成果。

每个声明 cohort 机器人的最低限度 package、simulator-integrity 和共享 Harness 聚焦检查优先于
更广泛的整理。各 package 就绪后可运行隐藏 reference 诊断和逐机器人 canary，但正式 Experiment 3
cohort 必须等待完整声明 cohort。
