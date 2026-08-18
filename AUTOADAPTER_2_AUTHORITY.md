# Auto-Adapter 2.0 Direct-MuJoCo Mainline Authority / Direct-MuJoCo 主线权威文档

> **Document ID / 文档编号：** `AA2-AUTH`<br>
> **Document role / 文档角色：** sole normative project document / 项目唯一规范性文档<br>
> **Normative language / 规范语言：** English / 英文<br>
> **Chinese text / 中文文本：** auxiliary reading support only / 仅作辅助阅读<br>
> **Document revision / 文档版本：** `0.19.0`<br>
> **Effective date / 生效日期：** 2026-08-18<br>
> **Current direction / 当前方向：** Direct-MuJoCo is the default mainline; real-SDK and Translation work is an independent extension / Direct-MuJoCo 是默认主线；真实 SDK 与 Translation 工作是独立扩展线

Revision `0.19.0` aligns the Authority with the three thesis experiments. It separates
capability-layer synthesis from capability-layer use, defines the three Auto-Adapter component
analyses, and distinguishes the initial two-case engineering acceptance milestone from the
declared cross-morphology experiment. It also makes explicit that cross-morphology results describe
associations rather than causal morphology effects.

**中文辅助说明。** `0.19.0` 使本 Authority 与论文的三个实验保持一致。它区分 capability-layer
synthesis 与 capability-layer use，明确 Auto-Adapter 的三项组件分析，并把首轮双案例工程验收
里程碑与正式声明的 cross-morphology 实验区分开。它同时明确：cross-morphology 结果描述关联，
而不能解释为 morphology 的因果效应。

The Direct-MuJoCo direction retained by this revision replaces the former rule that the first
formal two-robot path had to execute through a real SDK and an SDK-specific Translation Layer.
The default Auto-Adapter 2.0 experiment studies model-generated, robot-specific Direct-MuJoCo
drivers under both preserved AutoAdapter 1.0 generation conditions: trusted-skeleton-assisted and
controller-from-scratch. SDK-grounded execution remains valuable, but it is developed and evaluated
separately and does not block the mainline.

Revision `0.18.3` also removes the pre-authored fixed-task-to-effect policy from the formal
experiment. Each acceptance robot instead supplies an admitted source-backed Task Library of at
least twenty applicable tasks and pass standards. Task-Grounded Capability Design must produce five
to ten reusable capability designs, their effects and interfaces, and source-traceable public
validation contracts from that Library. The Independent Validation Compiler audits and compiles
those contracts into the private executable suite; it does not let the design phase judge the
implementation.

Revision `0.18.4` changes the normative phase names to match their actual outputs. `Task-Grounded
Capability Design` (`TGCD`) replaces `Stage 1`; `Independent Validation Compiler` (`IVC`) replaces
`Blue Line`; and `Driver Synthesis` names the later phase that produces executable code. The term
`synthesis` is not used for TGCD because TGCD outputs a design contract, not an executable
capability. Historical Demo2 code and evidence may retain the former names only when describing
that historical implementation.

Revision `0.18.5` designates `demo3/` as the active implementation workspace and makes a complete,
source-backed Task Library an admission condition for every robot exposed as runnable by Demo3,
not only the first acceptance pair. Every scoring clause must identify the benchmark or industrial
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
derived controller logic, and generated wrappers are never inputs to TGCD, IVC, STUDY, GENERATE, Repair,
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
envelope; generated capabilities are instance methods on the object returned by `build()` and
receive `request` as a plain mapping. A development-probe source-audit rejection is reported but
never executed and does not by itself prevent Repair from producing revised source. Evolution may
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

Revision `0.18.14` limits each robot's formal physical validation to five private cases sampled
uniformly without replacement from the complete IVC-audited private case pool. The Framework makes
and records this selection before Driver Synthesis, keeps it Harness-private, and reuses the same
sealed five-case suite for reference calibration, both generation conditions, and every Repair
attempt for that robot. TGCD and IVC still preserve and audit the complete Task Library and all
source scoring clauses; passing the sampled suite is evidence only for the selected five cases and
must not be reported as physical validation of unselected tasks or clauses.

**中文辅助说明。** 本修订保留的 Direct-MuJoCo 方向取代此前“首个正式双机器人路径
必须经过真实 SDK 和 SDK 专属 Translation Layer 执行”的规则。Auto-Adapter 2.0 的默认实验
现在研究由模型在可信 skeleton 辅助和 controller-from-scratch 两种保留的 AutoAdapter 1.0
生成条件下，生成面向特定机器人的 Direct-MuJoCo driver。基于 SDK 的执行仍有研究价值，但将
独立开发和评估，且不再阻塞主线。

`0.18.3` 还从正式实验中移除了预先编写的固定 task→effect 策略。每个验收机器人改为提供一份
已准入、具有来源依据的 Task Library，其中至少包含二十项适用于该机器人的任务及通过标准。
Task-Grounded Capability Design 必须从该 Library 产生五至十项可复用 capability 设计、对应
effect 和接口，以及可追溯到来源的公开 validation contract。Independent Validation Compiler
负责审计并把这些合同编译成私有可执行 suite；设计阶段不能为后续实现做最终判定。

`0.18.4` 根据各阶段的真实产物统一规范名称：`Task-Grounded Capability Design`（`TGCD`）取代
`Stage 1`；`Independent Validation Compiler`（`IVC`）取代 `Blue Line`；后续真正生成可执行
代码的阶段称为 `Driver Synthesis`。TGCD 只输出设计合同而不输出可执行 capability，因此不使用
`synthesis` 一词。旧名称只允许在明确描述历史 Demo2 代码或证据时保留。

`0.18.5` 指定 `demo3/` 为活动实现 workspace，并把完整、有来源依据的 Task Library 设为 Demo3
每个 runnable 机器人的准入条件，而不只约束首轮验收机器人对。每条评分 clause 都必须标明支持
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
condition 枚举和公开 `request` 信封；生成 capability 必须是 `build()` 返回对象上的实例方法，
并把 `request` 当作普通 mapping。未通过公开源码审计的 development probe 只返回拒绝诊断，
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

`0.18.14` 将每个机器人的正式物理验证限制为五个私有 case：Framework 从 IVC 已完整审计的
private case pool 中无放回均匀随机抽取五个，在 Driver Synthesis 前记录并封存选择，保持其仅对
Harness 可见，并让该机器人的 reference 校准、两种生成条件和所有 Repair attempt 复用同一组
五个 case。TGCD 和 IVC 仍须保留并审计完整 Task Library 及全部来源评分 clause；通过抽样 suite
只证明被选中的五个 case，不能表述为未抽中 task 或 clause 已完成物理验证。

---

## 0. Authority and precedence / 权威性与优先级

### 0.1 Sole authority / 唯一权威

This file is the only normative source for the current project objective, architecture boundary,
research questions, mainline acceptance, and SDK-extension relationship. README files, project
plans, prompts, schemas, source code, Library records, and historical runs may implement or
provide evidence for this Authority, but their existence does not create requirements or override
it. When they conflict, this file governs and the conflicting material must be corrected.

English clauses are normative. Chinese headings, tables, and paragraphs are faithful reading aids
and must not add, remove, weaken, or strengthen a requirement. If the two languages diverge, the
English text governs and the Chinese text must be corrected.

Git history preserves superseded designs. The repository must not create a second active authority
file or retain an obsolete design as a parallel normative source.

**中文辅助说明。** 本文件是当前项目目标、架构边界、研究问题、主线验收标准以及 SDK 扩展
关系的唯一规范来源。README、项目计划、prompt、schema、源代码、Library 记录和历史运行可以
实现本文件或提供证据，但它们自身不会产生新要求，也不能覆盖本文件。发生冲突时，以本文件为准，
并必须修正冲突材料。英文条款具有规范效力；中文标题、表格和段落只提供忠实的辅助阅读，不得
增加、删除、削弱或强化要求。如果两种语言出现偏差，以英文为准，并必须修正中文。Git 历史用于
保留已被取代的设计；仓库不得创建第二份仍然有效的权威文件，也不得将过时设计保留为并行规范
来源。

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
| **robot capability layer** | The reusable software interface through which a high-level controller invokes robot operations while robot-specific execution details remain behind the interface. |
| **capability** | One callable robot operation exposed by the robot capability layer, defined by its semantics, inputs, outputs, preconditions, and measurable acceptance obligations. `Skill` is reserved for a task-level, temporally extended behaviour and is not a synonym for a capability. |
| **robot-specific driver** | The executable implementation of the robot capability layer for one declared robot configuration. |
| **capability-level pass criterion** | A measurable acceptance condition for one capability. It is derived from source-backed task pass standards and is independently audited, compiled, and evaluated outside the candidate driver. |
| **capability-layer synthesis** | The experiment-level process of designing a robot capability layer, defining its capability-level pass criteria, implementing its robot-specific driver, and subjecting the result to independent validation. This term does not rename TGCD: `Driver Synthesis` remains the phase that produces executable code. |
| **capability-layer use** | The use of a fixed, validated robot capability layer by an otherwise matched high-level controller whose LLM backbone is the experimental variable. Use evidence is reported separately and does not establish synthesis. |
| **low-level motion-control capability** | A capability whose implementation converts a requested robot operation into robot-specific actuation, kinematic or locomotion control, and physics stepping. It must not be called a low-level motion-control skill. |
| **trusted skeleton** | Robot-control implementation assistance available only in the skeleton-assisted generation condition. It is distinct from the high-level controller. |
| **robot morphology** | A robot's physical form and joint arrangement. It is distinct from a **robot configuration**, which identifies the exact model, assets, actuators, and control setup used in a run. |

The labels `L0` and `L1` are not normative terms and must not be used in research claims or thesis
prose. Exact phase names such as `Task-Grounded Capability Design`, `Independent Validation
Compiler`, `Driver Synthesis`, `Repair`, and `Evolution` retain the meanings defined in Sections
3.1--3.6.

**中文辅助说明。** 上表中的术语在本 Authority、实验报告和论文正文中保持一致。当前研究框架
写作 `Auto-Adapter`；`AutoAdapter` 仅保留给代码或目录标识，以及历史系统 `AutoAdapter 1.0`
及其产物。`high-level controller` 负责在任务层选择和编排 capability；在 LLM backbone 对比中，
其架构、prompt、tool exposure 和 decision loop 保持固定，只有 backbone 改变。`robot capability
layer` 是该 controller 调用机器人操作的可复用接口，`robot-specific driver` 是该接口在一个准确
机器人配置上的可执行实现。`capability` 表示能力层暴露的一项可调用机器人操作；`skill` 只表示
任务层的时间扩展行为，不得作为 capability 的同义词。`capability-layer synthesis` 表示从能力层
设计、capability-level pass criteria 设计、driver 实现到独立验证的实验级过程，但 TGCD 本身仍
不称为 synthesis。`capability-layer use` 的证据不能代替 synthesis 证据。`low-level
motion-control capability` 不得写作 low-level motion-control skill。`trusted skeleton` 是
skeleton-assisted 条件中的机器人控制实现辅助，不是 high-level controller。`robot morphology`
指物理形态和关节排列；`robot configuration` 指一次运行使用的准确模型、资产、actuator 和控制
设置。`L0`、`L1` 不属于规范术语。

---

## 1. Project objective and claim boundary / 项目目标与主张边界

### 1.1 Research objective / 研究目标

Build the simplest experiment-grade Auto-Adapter 2.0 framework and evaluation needed to determine,
under controlled conditions, when LLM backbones can synthesise and use reusable robot capability
layers, which selected Auto-Adapter components produce measurable differences in synthesis quality,
and how low-level motion-control capability synthesis varies across robot morphologies.

The research programme separates two outcomes that must not be conflated. Capability-layer
synthesis concerns whether an LLM can design and implement a robot capability layer that passes
independent validation. Capability-layer use concerns whether an LLM, acting through the same fixed
and validated layer and the same high-level-controller implementation, can complete matched MuJoCo
tasks. Success in one outcome is not evidence of success in the other.

The initial two-robot, two-generation-condition mainline remains an engineering acceptance
milestone. It is not the complete thesis experiment set, a production platform, or a claim of
universal robot support.

**中文辅助说明。** 构建最简单的实验级 Auto-Adapter 2.0 框架和评估，以在受控条件下研究：
不同 LLM backbone 何时能够合成和使用可复用 robot capability layer；所选 Auto-Adapter 组件在
匹配比较中是否产生可测量的合成质量差异；以及 low-level motion-control capability 的合成结果
如何随 robot morphology 而变化。研究必须区分两种不能混为一谈的结果：capability-layer
synthesis 研究 LLM 能否设计并实现通过独立验证的 robot capability layer；capability-layer use
研究 LLM 能否通过同一套固定且已验证的 layer 和相同 high-level-controller 实现完成匹配的
MuJoCo 任务。任一结果成功都不能作为另一结果成功的证据。首轮双机器人、双生成条件主线仍是
工程验收里程碑；它不是完整论文实验集、生产平台或通用机器人支持主张。

### 1.2 Claim boundary / 主张边界

The project may claim only what the corresponding experiment evidence directly supports:

- comparative synthesis outcomes for the declared LLM backbones under the skeleton-assisted and
  from-scratch generation conditions, with the Auto-Adapter configuration, robot inputs,
  source-task pass standards, evaluation protocol, and resource budgets fixed within each
  condition;
- comparative capability-use outcomes for the declared LLM backbones using the same fixed and
  validated robot capability layer and the same high-level-controller implementation;
- task-grounded model design of reusable capabilities from at least twenty source-backed tasks;
- model-authored and source-grounded capability-level pass criteria that the implementation-blind
  IVC independently audits and compiles, and that the Harness evaluates under declared calibration
  and false-success checks;
- later-run differences associated with reviewed Evolution Experience only when an
  Experience-enabled run is compared with its matched no-Experience control;
- independent Direct-MuJoCo validation, first-attempt and post-Repair outcomes, failure patterns,
  and resource use; and
- descriptive cross-morphology differences for the declared robot cohort under the fixed
  experimental controls.

The evidence does **not** establish real-SDK fidelity, hardware validity, sim-to-real transfer,
visual perception, production reliability, universal model or robot superiority, physical
validation of unselected tasks, or a causal morphology effect. A capability-level pass criterion
is not established as reliable merely because the model wrote it or the compiler accepted its
syntax. A cross-morphology comparison is associational because morphology co-varies with actuation,
dynamics, task applicability, and MuJoCo control structure.

**中文辅助说明。** 项目只能提出对应实验直接证据支持的结论：在每种生成条件内固定
Auto-Adapter 配置、机器人输入、来源 task 通过标准、评估协议和资源预算后，不同 LLM backbone
在 skeleton-assisted 与 from-scratch 条件中的合成结果；不同 backbone 通过同一套固定且已验证
的 robot capability layer 和相同 high-level-controller 实现所得的 capability-use 结果；模型根据
至少二十项有来源任务完成的 task-grounded capability 设计；由模型设计、具有来源依据且由实现
不可见 IVC 独立审计和编译，并由 Harness 在已声明校准和 false-success 检查下评估的
capability-level pass criteria；只有在 Experience-enabled run 与匹配的 no-Experience control
比较时，才能报告与 reviewed Evolution Experience 相关的后续运行差异；独立 Direct-MuJoCo
验证、首次与 Repair 后结果、失败模式和资源使用；以及固定实验控制下、针对已声明机器人 cohort
的描述性 cross-morphology 差异。这些证据不证明真实 SDK 保真度、硬件有效性、sim-to-real
迁移、视觉感知、生产可靠性、模型或机器人的普遍优越性、未抽中任务的物理验证结果或 morphology
的因果效应。模型写出 criterion 或 compiler 接受其语法，本身都不足以证明 criterion 可靠。
由于 morphology 与 actuation、dynamics、任务适用性和 MuJoCo 控制结构共同变化，
cross-morphology 比较只能解释为关联。

### 1.3 Initial mainline acceptance pair and experimental robot cohort / 首轮主线验收对与实验机器人集合

The fixed mainline acceptance pair is:

| Role / 角色 | Robot configuration / 机器人配置 | Required public Task Library snapshot / 所需公开任务库快照 |
|---|---|---|
| Arm case / 机械臂案例 | `robotstudio_so101` | One admitted snapshot containing at least 20 distinct, applicable, source-backed tasks and pass standards |
| Quadruped case / 四足机器人案例 | `unitree-go2-stock-12dof` | One admitted snapshot containing at least 20 distinct, applicable, source-backed tasks and pass standards |

Both use their complete local MJCF closures and `mujoco==3.3.6`. Exact models, assets, admitted
task records and source standards, private instances, resets, measurement adapters and guards,
driver skeleton families, and from-scratch generation contracts are owned by versioned mainline
packages. A current five-task package or a package with pre-authored effects does not satisfy this
acceptance input.

Other complete admitted robot packages may be declared as members of the Experiment 3 cohort
through the versioned experiment manifest. Packages not declared in that manifest remain coverage
and expansion assets. Their execution does not block repository migration or the initial
two-condition, two-robot mainline acceptance. Incomplete research candidates are not runnable
packages.

The two configurations above define only the first synthesis-mainline engineering acceptance
milestone. They do not define the complete Experiment 3 cohort and do not by themselves support a
general or causal morphology claim.

Before Experiment 3 begins, a versioned experiment manifest must declare every included robot
configuration and its morphology category. Each included configuration must satisfy the same Task
Library, source-lineage, asset-closure, validation, and evidence requirements. A robot for which
only an MJCF or URDF asset exists is not an admitted experimental case.

**中文辅助说明。** 两个验收对象均使用各自完整的本地 MJCF 依赖闭包和
`mujoco==3.3.6`。精确模型、资产、已准入任务记录和来源标准、私有实例、reset、measurement
adapter 与 guard、driver skeleton family 和 from-scratch 生成合同归版本化主线 package 所有。
当前只有五项任务或预先写好 effect 的 package 不满足该验收输入。其他完整且已准入的机器人
package 可以通过版本化 experiment manifest 声明为 Experiment 3 cohort 的成员；未在该 manifest
中声明的 package 仍属于覆盖与扩展资产。它们的执行不阻塞仓库迁移或首轮双条件、双机器人主线
验收。不完整 research candidate 不是 runnable package。

上述两个配置只定义首轮 synthesis-mainline 工程验收里程碑，不构成 Experiment 3 的完整 cohort，
也不能单独支持一般性或因果性的 morphology 主张。Experiment 3 开始前，版本化 experiment
manifest 必须声明全部纳入的机器人配置及其 morphology 类别。每个配置必须满足相同的 Task
Library、来源 lineage、asset closure、validation 和 evidence 要求。只有 MJCF 或 URDF asset
的机器人不属于已准入实验案例。

### 1.4 Research questions / 研究问题

The project evaluates three controlled research questions:

1. **RQ1---Comparison of LLM backbones in capability-layer synthesis and use.**

   **Synthesis.** With the Auto-Adapter framework, robot inputs, source-backed Task Library,
   source-task pass standards, private-case construction policy, Harness measurement and verdict
   rules, and resource budget held fixed, how do LLM backbones differ in synthesising a reusable
   robot capability layer under the skeleton-assisted and from-scratch generation conditions?

   **Use.** For each robot configuration, with the validated robot capability layer,
   high-level-controller implementation, matched task inputs, evaluation protocol, and resource
   budget held fixed, how do the same LLM backbones differ in using that layer to complete the
   tasks?

2. **RQ2---Component analysis of the Auto-Adapter framework.**

   **(a) Task-grounded capability design.** Can an LLM derive five to ten reusable capabilities
   from at least twenty source-backed tasks without receiving a pre-authored capability catalogue
   or task-to-capability mapping?

   **(b) Capability-level pass-criteria design.** Can the LLM design source-grounded
   capability-level pass criteria without human predefinition at the capability level, and can the
   implementation-blind IVC independently audit and compile those criteria into executable
   validation?

   **(c) Continued Evolution.** Does reviewed evidence from completed runs improve synthesis
   quality in matched later runs relative to the same condition without that Experience?

3. **RQ3---Cross-morphology analysis of low-level motion-control capability synthesis.**

   With the LLM backbone, high-level-controller implementation, Auto-Adapter configuration,
   generation condition, evaluation protocol, and resource budget held fixed, how are
   robot-morphology differences associated with validation success, failure patterns, and resource
   use when synthesising low-level motion-control capabilities?

For RQ1 synthesis, fixed validation criteria means fixed source-task pass standards, private task
instances, measurement semantics, and Harness verdict rules. Model-authored capability-level pass
criteria remain an evaluated output and may therefore differ across LLM backbones. Within each
generation condition, all backbones receive the same public inputs; the two generation conditions
differ only in their authorised access to the trusted skeleton.

Reference drivers are positive controls for the Framework and Direct-MuJoCo execution route. They
are not model conditions and cannot be counted as model synthesis or capability-use results. RQ3
is descriptive and associational: the declared design does not identify a causal morphology
effect.

**中文辅助说明。** 项目评估三个受控研究问题：

1. **RQ1——LLM backbone 在 capability-layer synthesis 与 use 中的比较。**
   **Synthesis：** 在固定 Auto-Adapter framework、机器人输入、有来源 Task Library、来源 task
   通过标准、private-case 构造 policy、Harness 测量与判定规则以及资源预算时，不同 LLM backbone
   在 skeleton-assisted 与 from-scratch 条件下合成可复用 robot capability layer 的能力有何差异？
   **Use：** 对每个机器人配置，在固定已验证 robot capability layer、high-level-controller 实现、
   匹配任务输入、评估协议和资源预算时，相同的一组 LLM backbone 使用该 layer 完成任务的能力
   有何差异？
2. **RQ2——Auto-Adapter framework 的组件分析。**
   **(a) Task-grounded capability design：** 在不接收预写 capability catalogue 或
   task-to-capability mapping 的情况下，LLM 能否从至少二十项有来源任务中归纳五至十项可复用
   capabilities？
   **(b) Capability-level pass-criteria design：** 在 capability level 没有人工预定义的情况下，
   LLM 能否设计有来源依据的 capability-level pass criteria，且实现不可见的 IVC 能否独立审计并
   将其编译为可执行 validation？
   **(c) Continued Evolution：** 与不使用该 Experience 的匹配条件相比，来自已完成运行且经过
   审查的证据能否提升后续匹配运行的合成质量？
3. **RQ3——Low-level motion-control capability synthesis 的 cross-morphology 分析。**
   在固定 LLM backbone、high-level-controller 实现、Auto-Adapter 配置、生成条件、评估协议和
   资源预算时，不同 robot morphology 与 low-level motion-control capability 合成中的 validation
   success、failure pattern 和 resource use 差异具有何种关联？

在 RQ1 synthesis 中，固定 validation criteria 是指固定来源 task 通过标准、私有 task instance、
测量语义和 Harness 判定规则。由模型生成的 capability-level pass criteria 仍是被评估输出，因此
可以随 LLM backbone 不同而变化。在每种生成条件内，所有 backbone 接收相同公开输入；两种生成
条件只在是否获准访问 trusted skeleton 这一点上不同。Reference driver 是 Framework 和
Direct-MuJoCo 执行路径的正向对照，不是模型条件，不能计为 model synthesis 或 capability-use
结果。RQ3 只能进行描述性和关联性解释；当前设计不能识别 morphology 的因果效应。

---

## 2. Repository and dependency boundary / 仓库与依赖边界

### 2.1 Target repository layout / 目标仓库结构

```text
demo3/                       # active Direct-MuJoCo implementation workspace / 活动实现 workspace
  pyproject.toml             # complete declared third-party runtime dependencies / 完整声明依赖
  libraries/
    robots/                  # only complete admitted robot packages / 仅完整已准入机器人 package
    experience/
  capability_design/
  validation_compiler/
  driver_synthesis/
  harness/
  references/                # calibration-only studies and drivers / 仅供校准
  tests/
  runs/                      # ignored raw run evidence / 被忽略的原始运行证据
  research_candidates/       # incomplete, non-runnable robot/task research / 不完整且不可运行

demo2/                       # preserved policy-constrained historical baseline / 保留的旧基线

general_demo/                # post-acceptance canonical Direct-MuJoCo mainline / 验收后 canonical 主线
  libraries/
    robots/
    experience/
  references/
  tests/
  evidence/                  # concise tracked run summaries / 精简的版本化运行摘要

extensions/
  sdk/                        # real-SDK + Translation experimental extension / 实验扩展线
```

`demo3/` is the only active implementation workspace for this Authority. `demo2/` remains a
preserved policy-constrained historical baseline and must not be incrementally transformed into
the new design. The current SDK-grounded `general_demo/` is the implementation to be moved to
`extensions/sdk/`. After the Demo3 acceptance gates pass, moving `demo3/` to the canonical
`general_demo/` path is a mechanical migration rather than a redesign.

**中文辅助说明。** `demo3/` 是本 Authority 唯一活动实现 workspace。`demo2/` 保留为受 policy
约束的历史基线，不得通过持续改造变成新设计。当前基于 SDK 的 `general_demo/` 将迁移到
`extensions/sdk/`。Demo3 通过验收门槛后，再把 `demo3/` 迁移到 canonical `general_demo/`
路径；这应当是机械迁移而不是重新设计。

### 2.2 Dependency direction / 依赖方向

Demo3 must be installable, testable, and runnable without importing, reading, executing, or
resolving files from `demo2/`, the current `general_demo/`, `extensions/`, `thesis/`, a temporary
audit checkout, a user home path, or any other repository-external or sibling source tree. It must
not use an absolute developer-machine path, an escaping symlink, Git submodule content, or a
runtime network download to obtain code, prompts, task data, standards-derived scoring records,
MJCF, meshes, textures, skeletons, reference drivers, or AutoAdapter 1.0 orchestration.

All project-authored imports and file resolutions must remain under `demo3/`. In particular,
Demo3 must vendor the minimum required AutoAdapter 1.0 STUDY/GENERATE and from-scratch routes and
the exact two acceptance-robot asset closures. Source URLs in `sources.json` are citations, not
runtime dependencies; the structured task and scoring content required for a run is stored locally.

Literal zero-dependency execution is not the project contract. Demo3 may depend only on the small
third-party runtime set declared in `demo3/pyproject.toml`, including Python, MuJoCo, numerical and
video support, plus the configured real-model provider/service needed for model-authored phases.
The environment is installed before a formal run. During a formal run, network access is allowed
only to the configured model service from the Framework model adapter; candidate execution,
MuJoCo evaluation, IVC compilation, and Harness verdicting do not fetch remote content.

The SDK extension may later consume a small, explicit canonical-mainline contract, but the
dependency must never point back from Demo3 into the extension.

Default commands and default focused tests exercise only the mainline acceptance pair. Broader
robot coverage and SDK tests use explicit commands. The two existing Tasks and Morphology Library
families must not be merged by matching file paths: conflicting records remain in their owning
mainline or extension namespace until a later evidence-backed deduplication is justified.

**中文辅助说明。** Demo3 必须能够在不导入、读取、执行或解析 `demo2/`、当前
`general_demo/`、`extensions/`、`thesis/`、临时 audit checkout、用户 home 路径或其他仓库外/
同级源码树文件的情况下安装、测试和运行。不得通过开发者机器绝对路径、逃逸 symlink、Git
submodule 或运行时网络下载获得代码、prompt、task 数据、来源标准评分记录、MJCF、mesh、texture、
skeleton、reference driver 或 AutoAdapter 1.0 orchestration。

全部项目自编 import 和文件解析都必须停留在 `demo3/` 下。Demo3 必须内置最小所需的 AutoAdapter
1.0 STUDY/GENERATE 与 from-scratch 路径，以及两个验收机器人的完整 asset closure。
`sources.json` 中的 URL 只是 citation，不是运行依赖；运行所需的结构化任务和评分内容必须本地
保存。

项目不声称可以脱离所有第三方 runtime 执行。Demo3 只能依赖 `demo3/pyproject.toml` 明确声明的
小型第三方运行集合，包括 Python、MuJoCo、数值和视频支持，以及模型创作阶段所需的已配置真实
model provider/service。正式 run 前完成环境安装；正式 run 中只有 Framework model adapter 可以
访问已配置模型服务，candidate 执行、MuJoCo evaluation、IVC compilation 和 Harness verdict
不得下载远程内容。SDK 扩展以后可以消费一个小型 canonical-mainline contract，但依赖方向绝不
能从 Demo3 指向扩展。

默认命令和默认聚焦测试只运行主线验收机器人对；更广泛的机器人覆盖和 SDK 测试使用显式命令。
现有两套 Tasks 与 Morphology Library 不能依据相同文件路径直接合并；存在冲突的记录继续保留在
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
- a reference driver used only for calibration.

The package must resolve without an SDK Entry, no-SDK placeholder, Translation Layer, integration
manifest, or SDK Readiness gate. Missing or inconsistent required files stop the run as an input or
infrastructure failure, not a model failure.

**中文辅助说明。** 单次机器人运行需要解析出一个一致的软件包，其中包含：一个机器人/配置
身份；完整的 MJCF 入口及资产闭包；公开的 Morphology 与控制事实；一份已封存、已准入且至少
包含二十项适用来源任务的 Task Library 快照；每项任务公开的来源 lineage 和机器可表达通过
标准；Framework 私有的具体实例、reset、执行与测量 binding 和 guard；供 skeleton-assisted
条件使用的可信 skeleton family；保留的 from-scratch 生成合同及获准的 MuJoCo/NumPy/Python
primitives；可选且经过审阅的 Experience；以及仅用于校准的 reference driver。

该软件包必须在没有 SDK Entry、no-SDK placeholder、Translation Layer、integration manifest
或 SDK Readiness gate 的情况下完成解析。缺失或不一致的必需文件应使运行以输入错误或基础设施
错误停止，不能归因于模型失败。

### 2.4 Source-backed Task Library / 有来源依据的任务库

Every robot listed in Demo3's admitted or runnable robot index must have one complete frozen Task
Library snapshot containing at least twenty distinct tasks that are common and physically
applicable to that exact robot configuration. This rule applies to the acceptance pair and every
later robot without exception. A task is admitted only when a human reviewer confirms all of the
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
- a public invocation schema for the common `request` envelope, including the exact task-specific
  `task_parameters` fields, types, units, and frames that Driver Synthesis may consume.

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
scoring clause, or unresolved source lineage belongs under a non-runnable `research_candidates/`
area and must fail closed if selected for a formal run.

The minimum admitted robot package layout is:

```text
demo3/libraries/robots/<robot_configuration_id>/<package_version>/
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
  reference/                 # calibration only
```

`sources.json` and the public scoring clauses are TGCD-visible. Files under `tasks/private/` remain
IVC/Harness-only. Directory presence alone does not admit a package; the loader must validate the
complete source and scoring contract before adding it to the runnable index.

**中文辅助说明。** Demo3 admitted 或 runnable robot index 中的每个机器人都必须具有一份完整、
已封存的 Task Library 快照，至少包含二十项彼此不同、常见且在该精确机器人配置上物理适用的
任务；该规则同样适用于首轮验收对及以后加入的每个机器人，没有例外。只有经过人工审查并确认
以下内容，任务才能准入：具有可追溯的 primary benchmark 或工业生产标准来源，并记录精确标题、
发布者/所有组织、版本/日期、稳定定位及所使用的具体 section、table、protocol 或 evaluation
definition；说明所采用的来源任务或操作、机器人适用理由及向所选 MuJoCo 配置的改编；提供公开
任务描述和可测量通过标准，包括 metric、unit、comparator、threshold/允许范围、适用时的时间
要求及 aggregation；每条评分 clause 都标明支持其 metric、comparator、threshold/range、时间
要求和 aggregation 的来源；提供足够的 scene 与 observation 假设，使独立编译器能够绑定
私有物理实例而不改变任务含义；同时公开统一 `request` 调用信封的 schema，包括 Driver
Synthesis 可读取的 task-specific `task_parameters` 字段、类型、unit 和 frame。

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
未解决的机器人，只能放在不可运行的 `research_candidates/` 区域；一旦被正式 run 选择，必须
fail closed。

每个已准入机器人 package 至少按上述结构提供 `morphology.json`、`tasks/sources.json`、包含
二十项以上任务且逐评分 clause 引用来源的 `tasks/catalog.json`、私有 instances/bindings/guards、
完整本地 MJCF closure、仅供 skeleton-assisted 条件使用的 skeleton，以及仅供校准的 reference。
`sources.json` 和公开评分 clause 对 TGCD 可见，`tasks/private/` 下文件仅供 IVC/Harness 使用。
只有目录存在并不构成准入；loader 必须在加入 runnable index 前核验完整来源和评分合同。

---

## 3. Mainline end-to-end contract / 主线端到端合同

The required mainline flow is:

```text
Morphology + >=20 sourced Tasks/pass standards + reviewed Experience
                              |
                              v
 Task-Grounded Capability Design (5-10 capability contracts)
                              |
                              v
             Independent Validation Compiler
                              |
                              v
 capability_design.json + complete private_case_pool.json
                              |
                              v
        recorded random five-case private_validation_suite.json
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
      separate terminal reports + per-trial videos
                       |
                       v
          non-blocking Evolution sidecar
```

The vendored AutoAdapter 1.0 STUDY/GENERATE substrate is derived from commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`. Later changes may make the code self-contained, but
must preserve the observable contract below rather than silently substitute a fixed driver.

The two generation conditions are separate experimental cells. For one robot/model replicate they
reuse the same sealed `capability_design.json`, the same pre-generated sampled five-case
`private_validation_suite.json`, frozen Task Library snapshot, model identity, and environment.
They run in isolated workspaces; neither condition may
inspect or reuse the other condition's candidate, trace, validation result, Repair history, or
generated controller. Each condition has its own maximum of three submitted driver attempts and
receives an independent Harness verdict. Results are never merged into one ambiguous driver result.

**中文辅助说明。** 主线必须遵循以下流程：

```text
Morphology + >=20 项有来源 Tasks/通过标准 + 已审查 Experience
                              |
                              v
 Task-Grounded Capability Design（5-10 项 capability contract）
                              |
                              v
              Independent Validation Compiler
                              |
                              v
 capability_design.json + 完整 private_case_pool.json
                              |
                              v
       有记录的随机五-case private_validation_suite.json
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
          分开的最终报告 + 每次 trial 视频
                       |
                       v
             非阻塞 Evolution sidecar
```

仓库内置的 AutoAdapter 1.0 STUDY/GENERATE 基础来自提交
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`。后续可以把代码改为自包含，但必须保持下述
可观察合同，不能悄悄改为输出固定 driver。

两种生成条件是相互独立的实验 cell。对于同一个机器人/model replicate，它们复用同一份已封存
`capability_design.json`、同一套预先生成的抽样五-case `private_validation_suite.json`、已封存
Task Library 快照、模型身份和环境，
并在隔离的 workspace 中运行；任一条件都不得检查或复用另一条件的 candidate、trace、验证结果、
Repair 历史或生成的 controller。每种条件各自最多提交三个 driver attempt，并分别获得 Harness
verdict；结果不得合并成一个含糊的 driver 结果。

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
- a public validation contract containing every measurable obligation needed to preserve the pass
  standards of all covered tasks: metric semantics, unit, comparator, threshold or allowed range,
  dwell or other temporal rule, aggregation, and source-task/standard lineage.

Every admitted task must be covered by exactly one designed capability for an accepted Design.
The five-to-ten bound forces reuse across the at-least-twenty tasks; arbitrary merging without a
common robot effect is invalid. A capability validation contract may contain multiple clauses and
task-specific cases. It must never discard or weaken a covered task's source pass standard merely
to create one common threshold.

TGCD may design names, abstractions, interfaces, and validation contracts, but it cannot
invent unsupported robot affordances, units, frames, source claims, or less demanding standards.
The semantic interface is transported through the fixed `method(request=...)` ABI; TGCD does not
rename that Python envelope and may use only task-parameter fields declared by the covered tasks.
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
该 validation contract 必须保留所有被覆盖任务的可测量义务，包括 metric 语义、unit、comparator、
threshold/允许范围、dwell 或其他时间规则、aggregation 和来源 task/standard lineage。

被接受的 Design 必须让每项已准入任务恰好由一个设计 capability 覆盖。至少二十项任务只能设计
五至十项 capability，这一边界用于迫使模型形成复用抽象；没有共同机器人 effect 的任意合并无效。
一个 capability 的 validation contract 可以包含多个 clause 和 task-specific case，绝不能为了
得到一个共同 threshold 而删除或弱化某项来源通过标准。TGCD 可以设计名称、抽象、接口和
validation contract，但不能发明无依据的机器人 affordance、unit、frame、来源声明或更宽松标准；
语义 interface 通过固定的 `method(request=...)` ABI 传输，TGCD 不得改名该 Python 调用信封，且
只能使用被覆盖任务公开声明的 task-parameter 字段；
也不得接收私有实例、seed、精确 reset 状态、simulator symbol、measurement 实现、guard、可执行
case、预期结果、validation report 或 candidate 实现信息。

### 3.2 Independent Validation Compiler / 独立验证编译器

The Independent Validation Compiler (`IVC`) consumes the sealed Capability Design, the same
source-backed task records, and Framework-private task instances and execution bindings. Before
initial Driver Synthesis begins, it audits and compiles the designed public validation contracts
into a complete private case pool. It is implementation-blind: it cannot inspect `driver.py`,
candidate traces, candidate output, Repair history, or validation results.

For every designed capability, the IVC must:

1. verify that every covered task and source pass-standard clause is represented;
2. reject an invented, missing, incomparable, or weaker metric, comparator, threshold, temporal
   rule, or aggregation;
3. bind the public metric semantics to trusted MuJoCo observations and Framework-owned measurement
   code;
4. select concrete private case inputs, scene/reset state, repetitions, termination, and
   anti-false-pass guards; and
5. produce deterministic per-clause, per-task, per-capability, and whole-suite verdict rules.

The IVC may preserve a TGCD threshold or make a private case stricter when justified, but
it cannot silently relax a source obligation. A structural/reference checker must confirm five to
ten capabilities, complete task and source-standard coverage in the case pool, valid simulator
bindings, and no weaker-standard substitutions before Driver Synthesis receives the sealed
Capability Design.

After that audit, the Framework uniformly samples exactly five cases without replacement from the
complete pool using a recorded run seed. A pool with fewer than five cases is invalid. The selected
case IDs and seed remain Harness-private. The resulting five-case suite is sealed before STUDY and
must remain identical for the robot's reference calibration, both generation conditions, and every
Repair attempt. Its whole-suite verdict requires all five selected cases to pass. Reports must keep
complete design coverage distinct from selected physical evaluation and must not count an
unselected task or clause as physically validated.

The executable suite, concrete cases, bindings, and guards remain Harness-private. Driver Synthesis
may see the public capability interfaces and validation-contract semantics from the sealed
Capability Design, but not the private realization of those standards. Creating the suite before
Driver Synthesis is not sufficient isolation if candidate code can later read it. The final
pass/fail verdict belongs only to the trusted Harness, never to TGCD or IVC model self-report.

**中文辅助说明。** Independent Validation Compiler（`IVC`）接收已封存 Capability Design、
同一批有来源 task record，以及 Framework 私有的任务实例和执行 binding。在首次 Driver
Synthesis 开始前，它审计并把 TGCD 设计的公开 validation contract 编译成完整的私有 case
pool。IVC 对实现不可见，不得检查 `driver.py`、
candidate trace、candidate 输出、Repair 历史或验证结果。

对每项设计 capability，IVC 必须：确认每项被覆盖任务和每条来源通过标准都得到保留；拒绝
虚构、缺失、不可比较或更宽松的 metric、comparator、threshold、时间规则或 aggregation；把公开
metric 语义绑定到可信 MuJoCo observation 和 Framework measurement 代码；选择具体私有 case
输入、scene/reset 状态、重复次数、终止条件和 anti-false-pass guard；并生成逐 clause、逐 task、
逐 capability 及整套 suite 的确定性 verdict 规则。

有依据时 IVC 可以保留 TGCD threshold 或让私有 case 更严格，但不能悄悄放宽来源义务。
Driver Synthesis 收到封存 Capability Design 前，结构/reference checker 必须确认 capability 数量
为五至十、case pool 中 task 与来源标准覆盖完整、simulator binding 有效且不存在弱化标准的
替换。完成该审计后，Framework 使用已记录的 run seed，从完整 pool 中无放回均匀随机抽取恰好
五个 case；不足五个 case 的 pool 无效。被选 case ID 和 seed 仅对 Harness 可见。五-case suite
在 STUDY 前封存，并在该机器人 reference 校准、两种生成条件及全部 Repair attempt 间保持完全
一致；整套 verdict 要求五个 case 全部通过。报告必须区分完整设计覆盖与抽样物理验证，不得把
未抽中的 task 或 clause 计为已物理验证。可执行 suite、具体 case、binding 和 guard 始终属于 Harness 私有数据。Driver Synthesis 可以看到封存 Capability Design
中公开的 capability 接口及 validation-contract 语义，但不能看到这些标准的私有实现。最终
pass/fail verdict 只属于可信 Harness，不能来自 TGCD 或 IVC 模型自述。

### 3.3 Driver Synthesis / Driver 合成

A dynamic mainline run uses the configured real model for every model-authored stage. Both preserved
AutoAdapter 1.0 generation conditions may inspect all run-relevant public inputs: the complete public
robot package, including public Morphology, Task Library catalog and source records, eligible
Experience, the complete selected MJCF closure, STUDY output, and the same sealed Capability Design.
They may use a complete local Python/MuJoCo development probe within explicit call, simulated-time,
control-step, output, and wall-time budgets, and may write and revise one executable candidate in
their own workspace. The probe may load the canonical public scene, run model-authored scripts,
inspect simulator state, exercise candidate methods, and tune actuator-driven behavior; it may not
invoke or inspect the private Harness suite.

STUDY and GENERATE/GEN_ALGO execute through the self-contained AutoAdapter 1.0-style ReAct loop,
not as one-shot code-generation responses. Within explicit model-turn and tool budgets, the model
may repeatedly list and read staged public files, inspect the trusted skeleton only in the
skeleton-assisted condition, write or replace its own condition-local development files, run
bounded public Python/MuJoCo probes, and request source-audit, import, and public-method smoke
diagnostics for its current `driver.py`. Tool errors are returned to the same model conversation so
it can revise the candidate before submission. The Framework counts a formal attempt only after the
model explicitly submits `driver.py`; development rewrites and rejected pre-submission audits do not
consume one of the three Harness attempts.

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

**中文辅助说明。** 动态主线运行中，每个由模型创作的阶段都必须调用已配置的真实模型。两种
保留的 AutoAdapter 1.0 生成条件都可以检查与本次 run 相关的全部公开输入，包括完整公开机器人
package 中的 Morphology、Task Library catalog 与 source record、符合条件的 Experience、所选
MJCF 完整闭包、STUDY 输出和同一份已封存 Capability Design。两者都可以使用完整的本地
Python/MuJoCo 开发 probe，但该 probe 受明确的调用次数、模拟时间、control step、输出和 wall-time
预算约束。probe 可以加载 canonical 公开 scene、运行模型编写的脚本、检查 simulator state、执行
candidate 方法并调试 actuator-driven 行为，但不能调用或检查私有 Harness suite。

STUDY 与 GENERATE/GEN_ALGO 必须通过 Demo3 自包含的 AutoAdapter 1.0 式 ReAct loop 执行，
不能退化为一次性代码生成响应。在明确的模型 turn 和工具预算内，模型可以反复列出并读取 staged
公开文件；仅在 skeleton-assisted 条件查看可信 skeleton；编写或替换自己在本条件 workspace 中的
开发文件；运行有预算的公开 Python/MuJoCo probe；并对当前 `driver.py` 请求 source audit、import
及公开方法 smoke 诊断。工具错误返回同一个模型 conversation，使模型可以在提交前自行修订。
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

Repair uses the same bounded AutoAdapter 1.0-style ReAct development loop as initial synthesis.
Within one Repair attempt, the model may repeatedly inspect the preceding candidate-facing report,
edit `driver.py`, run public-only probes, and react to audit/import/smoke diagnostics before it
explicitly submits the replacement. These development turns remain part of one Repair attempt and
cannot inspect or invoke the private Harness.

Repair receives the previous driver and the complete candidate-facing report for the immediately
preceding attempt. This report includes every trial and clause outcome, opaque private-case identity,
the public and runtime invocation inputs actually supplied to the driver, actual measured values,
available state and trajectory diagnostics, exceptions, logs, guard outcomes, recordings, videos,
and the complete per-capability, per-clause, and per-case result structure. Repair may use the same
complete, budget-bounded local Python/MuJoCo development probe as initial generation.

The report omits only the private validation definition and unrelated secrets: private suite files
and source, unreleased case/reset/seed construction, private stricter thresholds or expected values,
executable criterion expressions, measurement bindings, private guard definitions, hidden expected
trajectories, Harness source/configuration, internal private paths, credentials, and artifacts from
the other generation condition. Public source-derived standards already present in the sealed
Capability Design remain visible. The report and media become available only after the evaluated
candidate worker exits; they are never mounted into that worker during evaluation. Every revision is
evaluated against the same pre-generated private validation suite.

A model-requested Repair probe that fails the public source boundary is not executed. Its rejection
diagnostic is returned in the Repair context, and Repair may still produce revised `driver.py`
source within the same bounded attempt. A rejected optional probe is not a physical-validation
result and does not weaken the private suite.

Reports must separate first-pass `pass@0` from cumulative success after Repair. A repaired pass may
contribute to `pass@k`, but it must never be presented as an initial pass or an independent new
suite estimate.

**中文辅助说明。** 首次提交的 driver 在科研报告中记为 attempt `0`。主线最多允许三个生成
driver attempt：一次首次生成，加上最多两次 Repair。Repair 只能修改 `driver.py`。

Repair 使用与首次 synthesis 相同的有预算 AutoAdapter 1.0 式 ReAct 开发循环。在一次 Repair
attempt 内，模型可以反复检查上一 attempt 的 candidate-facing 报告、修改 `driver.py`、运行仅含
公开输入的 probe，并根据 audit/import/smoke 诊断继续修正，直到明确提交替代版本。这些开发 turn
仍属于同一次 Repair attempt，且不能检查或调用私有 Harness。

Repair 接收上一版 driver，以及紧邻上一 attempt 的完整 candidate-facing 报告。该报告包含每个
trial 和 clause 的结果、不透明的 private-case 标识、实际提供给 driver 的公开/runtime 调用输入、
实际测量值、可用 state/trajectory 诊断、exception、log、guard 结果、录像、视频，以及完整的逐
capability、逐 clause 和逐 case 结果结构。Repair 可以使用与首次生成相同的完整但有预算上限的
本地 Python/MuJoCo 开发 probe。

报告只删除私有 validation 定义和无关 secret：私有 suite 文件及源码、未公开的 case/reset/seed
构造、私有加严 threshold 或 expected value、可执行 criterion 表达式、measurement binding、私有
guard 定义、隐藏 expected trajectory、Harness 源码/配置、内部私有路径、credential 和另一生成
条件的产物。封存 Capability Design 中原本公开的来源标准继续可见。报告和媒体只有在受评
candidate worker 退出后才交给 Repair，在 evaluation 期间绝不挂载进该 worker。每个修订版本都
必须使用同一套预先生成的私有 validation suite 评估。报告必须区分首次 `pass@0` 与 Repair 后累计成功；
Repair 后通过可以计入 `pass@k`，但不得包装成首次通过，也不得作为独立的新 suite 估计。

### 3.6 Evolution / 经验演化

Evolution is a non-blocking sidecar that reads a bounded projection of the terminal structured
report and may propose a reviewed Experience record for a later run. The projection retains
terminal execution/verdict facts, attempt summaries, outcome counts, and complete failed-trial
diagnostics, but may replace repeated full trajectory samples with sample counts because the full
report remains retained as evidence and has already been available to Repair. Evolution cannot
change the current driver, suite, criterion, verdict, retry decision, or run inputs. Failure of
Evolution does not turn a completed validation run into a synthesis failure.

**中文辅助说明。** Evolution 是非阻塞 sidecar：它读取最终结构化报告的有界投影，并可为后续
运行提出一条经过审查的 Experience 记录。该投影保留终态执行/verdict 事实、attempt 摘要、结果
计数和完整失败 trial 诊断；由于完整报告已保留且已提供给 Repair，重复的完整轨迹 sample 可以
替换为 sample count。Evolution 不得改变当前 driver、suite、criterion、verdict、retry 决定或
运行输入。Evolution 失败不会把已经完成的验证运行变成一次 synthesis failure。

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
in Section 3.5.

**中文辅助说明。** candidate 验证 worker 只能接收生成的 driver、可信公开 skeleton、canonical
公开 MJCF 闭包、公开调用参数、封存的公开 capability contract 和获准的 runtime 依赖。
candidate 不得访问私有 Blue 文件、完整任务实例记录、可执行 criterion 与 measurement binding、
私有 guard、报告、视频、Harness 源码/配置、仓库全局文件或无关环境变量。

最小实现应使用独立 worker 进程，并显式限定工作目录和环境变量 allowlist。执行 candidate 前
必须移除模型/API、云服务和仓库凭据。这是实验隔离边界，不是通用安全 sandbox 产品。

该 worker 边界约束 candidate 代码正在执行的时期。worker 退出后，Framework 可以把完整的
candidate-facing attempt 报告及相关媒体发布到该条件自己的生成 workspace，供 Repair 使用；
唯一需要删除的是 3.5 节规定的私有 validation 定义字段。

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

The Harness owns the private validation suite, trusted-state acquisition, aggregation, and final verdict.
The candidate may observe only the public state declared by its package. It cannot access a
MuJoCo truth handle separate from the approved driver/session interface or read the values used
only for evaluation.

The implementation must report at least these independent facts:

- `pipeline_completed`;
- `dynamic_model_called`;
- `driver_generated_in_run`;
- `physical_validation_executed`;
- `initial_validation_passed`;
- `final_validation_passed`;
- admitted Task Library task count, designed capability count, and covered task count;
- passed and total source-standard clause and private-case counts;
- generation/Repair attempt count; and
- video completeness.

A generic value such as `RUN_COMPLETED`, a zero process exit code, or `structural_ok` cannot stand
in for `final_validation_passed`. A smoke command intended to prove success must return non-zero
when its required physical verdict or evidence is incomplete, while retaining the run artifacts.

**中文辅助说明。** Harness 独占私有 validation suite、可信状态采集、结果 aggregation 和最终 verdict。
candidate 只能观察机器人包声明的公开 state；不得绕过获准的 driver/session interface 访问额外
MuJoCo truth handle，也不得读取仅供评估使用的数值。

实现至少必须独立报告：`pipeline_completed`、`dynamic_model_called`、
`driver_generated_in_run`、`physical_validation_executed`、`initial_validation_passed`、
`final_validation_passed`、已准入 Task Library task 数、设计 capability 数、已覆盖 task 数、
已通过和总来源标准 clause/私有 case 数、生成/Repair attempt 数，以及视频完整性。

`RUN_COMPLETED`、零进程退出码或 `structural_ok` 等通用值不能替代
`final_validation_passed`。用于证明成功的 smoke 命令，在所要求的物理 verdict 或证据不完整
时必须返回非零，同时保留运行产物。

### 4.4 Video evidence / 视频证据

Every formal private validation case and repetition has its own continuous Framework-controlled video
from post-reset/pre-invocation state through terminal observation, failure, exception, or timeout.
The manifest records robot/configuration, run, attempt, requirement/trial, simulation start/end,
frame count, video path, and completion result.

Candidate code, TGCD, IVC, STUDY/GENERATE, and Repair cannot read or control the recorder.
Video is audit evidence, not a verdict source; human visual judgment cannot replace the structured
Harness criterion. A missing, zero-frame, undecodable, truncated, or interval-incomplete required
video invalidates that trial as evidence failure. It does not become a candidate pass.

**中文辅助说明。** 每个正式私有 validation case 的每次 trial 和 repetition 都必须有独立、连续、由
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
| Reference calibration | Reviewed reference driver, real MuJoCo, Harness verdicts, and complete videos for the sampled five cases | The selected assets, controller baseline, sampled cases, Harness, and recording path are feasible | Any model generated the driver or unselected tasks were physically validated |
| Dynamic generation-condition executed | Source-backed 20+ task snapshot, real-model TGCD design of 5–10 capability contracts without a pre-authored effect policy, complete IVC-audited case pool, sealed sampled five-case `private_validation_suite.json`, named skeleton-assisted or from-scratch condition, real model identities/calls, model-generated `driver.py`, condition-appropriate STUDY/GENERATE trace, and real MuJoCo validation reaching a terminal verdict | That capability-design and generation condition executed end to end | The selected physical requirements passed, unselected tasks were physically run, or the other condition executed |
| Single-robot condition success | Dynamic condition evidence plus all five selected private cases pass within that condition's declared attempt budget | Model synthesis passed the sampled suite for that robot, condition, and run | Unselected tasks passed physical validation, or the paired condition, two-robot mainline, or SDK path succeeded |
| Paired two-condition experiment completed | Both generation conditions reach terminal verdicts for both fixed robots using the same sealed per-robot `capability_design.json` and `private_validation_suite.json` and declared experiment configuration | The four-cell Direct-MuJoCo comparison executed | Every cell passed |
| Two-condition, two-robot mainline success | All four robot-by-generation-condition cells independently satisfy single-robot condition success | The first paired Direct-MuJoCo mainline experiment succeeded | SDK fidelity, hardware validity, sim-to-real, or universal applicability |
| SDK-grounded extension evidence | Real SDK application logic and robot-specific Translation execute bidirectionally with MuJoCo | The named SDK-extension route executed | Hardware equivalence or mainline replacement |

**中文辅助表。**

| 证据类别 | 必须具备的事实 | 可以支持的结论 | 不能支持的结论 |
|---|---|---|---|
| 参考校准 | 经审查的 reference driver、真实 MuJoCo、抽样五个 case 的 Harness verdict 和完整视频 | 所选资产、controller baseline、抽样 case、Harness 和录像路径可行 | driver 由任何模型生成，或未抽中 task 已被物理验证 |
| 动态生成条件已执行 | 有来源的 20+ task 快照、没有预写 effect policy 的真实模型 TGCD 五至十项 capability contract 设计、IVC 完整审计的 case pool、封存的抽样五-case `private_validation_suite.json`、明确的 skeleton-assisted 或 from-scratch 条件、真实模型身份和调用、模型生成的 `driver.py`、符合该条件的 STUDY/GENERATE trace，以及到达最终 verdict 的真实 MuJoCo 验证 | capability 设计及该生成条件已完成端到端执行 | 抽中的物理要求已通过、未抽中的 task 已物理执行，或另一条件已执行 |
| 单机器人条件成功 | 具备动态条件证据，且抽中的五个 private case 均在该条件声明的 attempt 预算内通过 | 该机器人、该生成条件和该 run 通过抽样 suite | 未抽中 task 已通过物理验证，或配对条件、双机器人主线或 SDK 路径成功 |
| 双条件配对实验已完成 | 两种生成条件在两个固定机器人上均使用相同的每机器人封存 `capability_design.json`、`private_validation_suite.json` 和声明的实验配置到达最终 verdict | 四个 Direct-MuJoCo 实验 cell 已执行 | 每个 cell 均通过 |
| 双条件双机器人主线成功 | 四个机器人×生成条件 cell 均独立满足单机器人条件成功 | 首次配对 Direct-MuJoCo 主线实验成功 | SDK 保真度、硬件有效性、sim-to-real 或普遍适用性 |
| 基于真实 SDK 的扩展证据 | 真实 SDK 应用逻辑和机器人专用 Translation 与 MuJoCo 双向执行 | 指定的 SDK 扩展路径已执行 | 与硬件等效，或可替代主线 |

A dynamic run must retain enough evidence to verify the declared generation condition. A
skeleton-assisted run shows trusted-skeleton inspection; a from-scratch run shows that the
submitted source does not import, copy, or call the skeleton implementation. Both show an attempted
real local MuJoCo probe and prove that the submitted driver was generated in that run. At minimum,
the concise run summary records:

- run ID and code version;
- robot/configuration, package versions, Task Library snapshot, admitted task count, and source
  coverage;
- model provider, exact model identifier, and generation condition;
- TGCD designed capability count, task-to-capability coverage, source-standard coverage, and IVC
  audit result;
- TGCD, IVC, STUDY, GENERATE, validation, Repair, and Evolution outcomes;
- attempt count and separate initial/final verdicts;
- per-capability, source-standard-clause, and private-case results and video completeness; and
- any infrastructure or candidate failure reason.

Raw media need not be committed to Git. A concise tracked evidence record must point to the retained
run package and must not invent a success when the underlying run is ignored or unavailable.
Artifact hashes, signing, and an evidence registry are not required.

**中文辅助说明。** 动态运行必须保留足够证据，以核验声明的生成条件：skeleton-assisted run
需要证明模型检查了可信 skeleton；from-scratch run 需要证明提交源码没有导入、复制或调用 skeleton
实现。两者都必须证明尝试了真实本地 MuJoCo probe，并在本次运行中生成最终提交的 driver。简洁
运行摘要至少记录：run ID 和代码版本；机器人/配置及 package 版本、Task Library 快照、已准入
task 数量和来源覆盖；模型 provider、准确模型标识和 generation condition；TGCD 设计的
capability 数量、task→capability 覆盖、来源标准覆盖及 IVC 审计结果；TGCD、IVC、STUDY、
GENERATE、Validation、Repair 和 Evolution 结果；attempt 次数以及分开的初始和最终 verdict；
逐 capability、来源标准 clause 和私有 case 结果及视频完整性；基础设施或 candidate 失败原因。

原始媒体无需提交到 Git。受版本控制的简洁证据记录必须指向保留的运行包；当底层运行被忽略或
不可获取时，不得虚构成功。无需 artifact hash、签名或证据 registry。

### 5.1 Current evidence boundary / 当前证据边界

As of this revision, repository-visible Demo2 runs are reference calibrations. They demonstrate
that reference drivers can enter real MuJoCo and reach the Harness/video/report path. They do not
contain a verifiable dynamic model generation trace, the current five-task/effect-policy packages
do not satisfy the `0.18.3` input contract, and no recorded robot passes every required designed
capability validation clause. Therefore the current evidence supports “reference Direct-MuJoCo
path executed,” not “the model-generated mainline succeeded.”

**中文辅助说明。** 截至本版，仓库可见的 Demo2 运行均属于参考校准。它们证明 reference
driver 可以进入真实 MuJoCo，并到达 Harness、视频和报告路径；但不包含可核验的动态模型生成
trace；当前五任务/effect-policy package 不满足 `0.18.3` 输入合同，也没有任何已记录机器人通过
全部所需设计 capability validation clause。因此，当前证据只支持“参考 Direct-MuJoCo 路径已
执行”，不支持“模型生成的主线已经成功”。

---

## 6. Mainline acceptance and migration gates / 主线验收与迁移门槛

The following are construction obligations, not optional cleanup after Demo3 appears to run. Demo3
implementation work must satisfy each obligation when the affected component or path is introduced:

1. establish the self-contained `demo3/` project and declared reproducible environment before using
   a run as evidence;
2. admit only robot packages with at least twenty applicable, source-backed tasks and complete
   scoring-clause lineage, keeping incomplete packages non-runnable;
3. implement real-model TGCD and implementation-blind IVC without carrying forward Demo2's fixed
   five-task projection, pre-authored effect catalog, or task-to-effect allowlist;
4. preserve and independently execute both AutoAdapter 1.0 Driver Synthesis conditions against the
   same sealed per-robot Capability Design and the same sampled five-case private suite;
5. build private-suite isolation, candidate-process isolation, canonical-session enforcement,
   anti-teleport checks, and actuator-plus-physics-step evidence into the Harness path itself;
6. expose to Repair the complete candidate-facing attempt report and media while redacting only the
   private validation definitions, enforce three total driver attempts per condition, preserve the
   full budget-bounded local Python/MuJoCo development probe, and keep the private suite unchanged;
7. record one complete Framework-controlled video and manifest entry for every required private case
   repetition, with incomplete media invalidating that trial's evidence;
8. report pipeline execution, model calls, in-run generation, physical validation, initial and final
   verdicts, clause/case counts, attempts, and video completeness separately, and make success-oriented
   commands fail when required physical evidence fails;
9. calibrate both reference drivers against the resulting sealed suites before making dynamic claims,
   then retain truthful terminal evidence for all four robot-by-generation-condition cells; and
10. keep Evolution terminal and non-blocking so that it cannot alter the current candidate, suite,
    retry decision, verdict, or run inputs.

These obligations govern new Demo3 code even when the corresponding Demo2 defect is retained as a
historical regression fixture. Passing a later acceptance gate does not excuse bypassing the
construction boundary while implementing an earlier component.

**中文辅助说明。** 以下内容是 Demo3 搭建义务，不是系统看似能运行之后才选择处理的清理项。
实现每个相关组件或路径时，必须同步满足对应要求：

1. 在把任何 run 用作证据前，先建立自包含的 `demo3/` 项目和已声明、可复现的运行环境；
2. runnable index 只准入至少具有二十项适用、有来源任务且评分 clause lineage 完整的机器人
   package；不完整 package 保持不可运行；
3. 实现真实模型 TGCD 和对实现不可见的 IVC，不得继承 Demo2 的固定五任务 projection、预写
   effect catalog 或 task→effect allowlist；
4. 保留并独立执行 AutoAdapter 1.0 两种 Driver Synthesis 条件；同一机器人的两种条件使用相同
   封存 Capability Design 和同一套抽样得到的五-case 私有 suite；
5. 在 Harness 路径内直接实现私有 suite 隔离、candidate 进程隔离、canonical session、
   anti-teleport 检查以及 actuator 加 physics-step 证据；
6. Repair 可以接收完整的 candidate-facing attempt 报告和媒体，只删除私有 validation 定义；
   每种条件最多三个 driver attempt，保留完整但有预算上限的本地 Python/MuJoCo 开发 probe，且
   私有 suite 始终不变；
7. 每个必需 private case repetition 都有独立、完整、Framework 控制的视频和 manifest 记录；
   媒体不完整时该 trial 的证据无效；
8. 分开报告 pipeline 执行、模型调用、本次生成、物理验证、首次/最终 verdict、clause/case 数、
   attempt 和视频完整性；用于证明成功的命令在必需物理证据失败时必须失败；
9. 先让两个 reference driver 通过最终封存 suite 的校准，再提出动态结论，并为四个机器人乘生成
   条件 cell 保留真实的最终证据；
10. Evolution 必须位于 terminal verdict 之后且不阻塞主线，不能改变当前 candidate、suite、retry
    决定、verdict 或 run input。

即使对应 Demo2 缺陷被保留为历史 regression fixture，这些义务仍约束所有新 Demo3 代码。后续
验收门槛通过，不能成为搭建早期组件时绕开上述边界的理由。

### 6.1 Ready for mechanical directory migration / 可以进行机械目录迁移

The implementation may move from `demo3/` to `general_demo/` after all of the following are true:

1. Demo3's self-containment check proves that all project-authored imports and file resolutions stay
   under `demo3/`, with no sibling/external source-tree dependency, absolute machine path, escaping
   symlink, submodule requirement, or runtime asset download;
2. the two acceptance packages resolve from mainline-owned Morphology, Tasks, Experience, assets,
   skeletons, and references; every robot exposed in the runnable index passes Task Library
   admission; and each scoring clause has exact source lineage and a machine-expressible pass
   standard across at least twenty applicable tasks per robot;
3. a real TGCD run designs five to ten capability contracts without a pre-authored effect catalog
   or task-to-effect allowlist, and IVC confirms complete task/source-standard coverage;
4. anti-teleport, canonical-scene, and private-suite isolation checks pass;
5. both reference drivers pass the resulting sealed sampled five-case suite under the same rules
   used for generated drivers, with complete per-trial videos;
6. at least one real dynamic canary run reaches a terminal physical verdict and leaves model and
   generation traces, even if its candidate fails a requirement; and
7. the dependency environment declared by `demo3/pyproject.toml` can install before the run, execute
   the focused checks, and run the canary without undeclared packages or files.

Directory movement is mechanical and uses separate commits from behavioral repair. Moving the
implementation to the canonical path does not itself prove paired two-condition synthesis success.

**中文辅助说明。** 只有满足以下全部条件，实现才可以从 `demo3/` 迁移到 `general_demo/`：

1. Demo3 self-containment 检查证明全部项目自编 import 和文件解析都位于 `demo3/` 下，不依赖
   同级/外部源码树、机器绝对路径、逃逸 symlink、submodule 或运行时资产下载；
2. 两个验收 package 均能从主线拥有的 Morphology、Tasks、Experience、assets、skeletons 和
   references 中解析；runnable index 暴露的每个机器人均通过 Task Library 准入；每个机器人
   至少二十项适用任务中的每条评分 clause 都具有精确来源 lineage 和机器可表达通过标准；
3. 一次真实 TGCD run 在没有预写 effect catalog 或 task→effect allowlist 的情况下设计五至
   十项 capability contract，并由 IVC 确认 task/来源标准覆盖完整；
4. anti-teleport、canonical-scene 和 private-suite isolation 检查通过；
5. 两个 reference driver 均在与生成 driver 相同的规则下通过最终封存的抽样五-case suite，并
   具有完整的逐 trial 视频；
6. 至少一次真实 dynamic canary 运行到达最终物理 verdict，并留下模型和生成 trace，即使
   candidate 未通过某项要求；
7. `demo3/pyproject.toml` 声明的依赖环境可以在 run 前完成安装，并在没有未声明 package 或文件
   的情况下运行聚焦检查和 canary。

目录移动必须是机械操作，并与行为修复分开提交。实现移动到 canonical path 本身，并不能证明
双条件配对合成成功。

### 6.2 Two-condition, two-robot mainline success / 双条件双机器人主线成功

The project may state that the new mainline has run successfully end to end only when:

1. `robotstudio_so101` and `unitree-go2-stock-12dof` each complete both a skeleton-assisted and a
   from-scratch real dynamic run, producing four robot-by-generation-condition cells under the same
   Framework, model/provider configuration, frozen source-backed Task Library snapshots, and
   environment;
2. each acceptance Task Library snapshot has at least twenty admitted tasks, every scoring clause
   has valid benchmark or industrial-standard lineage and a machine-expressible pass standard, and
   no incomplete robot package is exposed as runnable by Demo3;
3. each robot/model replicate uses one real-model `capability_design.json` containing five to ten
   genuinely designed capabilities/effects/interfaces and source-traceable validation contracts, with no
   pre-authored effect catalog or task-to-effect allowlist;
4. one real-model complete private case pool produced by IVC passes the no-weaker-standard and
   complete-coverage audit; the Framework then seals one recorded random five-case sample that
   remains unchanged across reference calibration, the two generation conditions, and all Repair
   attempts, while each condition runs its own real STUDY and GENERATE or GEN_ALGO calls and retains
   their model/call records;
5. the two conditions use isolated workspaces and do not exchange candidates, traces, validation
   results, Repair history, or generated code;
6. each submitted `driver.py` was generated inside its declared condition and was not replaced by a
   reference driver or by the other condition's candidate;
7. all five selected private cases in each of the four cells pass independent physical validation
   within that condition's maximum three total generated-driver attempts; complete TGCD/IVC design
   coverage is reported separately and no claim is made that unselected tasks were physically run;
8. condition-specific `pass@0` and post-Repair results are reported separately, alongside a paired
   comparison that never hides a failed cell in an aggregate;
9. actuator/physics-step, isolation, canonical-scene, and from-scratch no-skeleton checks pass;
10. every required trial has complete decodable video and a matching manifest; and
11. the mainline has no runtime import or dependency on the SDK extension or any repository sibling,
    and no formal evaluation step downloads code, data, standards, or robot assets.

If all four cells reach terminal verdicts but any cell fails one or more requirements, the correct
claim is “the paired two-condition experiment completed; the named cell or cells failed synthesis,”
not “the two-condition mainline succeeded.” If only a reference driver passes, the correct claim is
“reference calibration passed.”

**中文辅助说明。** 只有满足以下全部条件，项目才可以声明新主线已经成功完成端到端运行：

1. `robotstudio_so101` 和 `unitree-go2-stock-12dof` 均完成 skeleton-assisted 与 from-scratch
   两种真实 dynamic run，在相同 Framework、model/provider 配置、已封存有来源 Task Library
   快照和环境下形成四个机器人×生成条件 cell；
2. 每份验收 Task Library 快照至少具有二十项已准入任务，每条评分 clause 都有有效 benchmark
   或工业标准 lineage 及机器可表达通过标准，且 Demo3 不把任何不完整机器人 package 暴露为
   runnable；
3. 每个 robot/model replicate 使用一份真实模型设计的 `capability_design.json`，其中包含五至
   十项真正设计的 capability/effect/interface 及可追溯来源的 validation contract，不存在预写 effect
   catalog 或 task→effect allowlist；
4. 一套由 IVC 生成的完整 private case pool 通过“不弱化来源标准”和完整覆盖审计；Framework
   随后封存一套有记录的随机五-case 样本，并让它在 reference 校准、两种生成条件和所有 Repair
   attempt 间保持不变；每种条件分别执行自己的真实 STUDY 与 GENERATE 或 GEN_ALGO 调用，并
   保留模型和调用记录；
5. 两种条件使用隔离 workspace，不能交换 candidate、trace、验证结果、Repair 历史或生成代码；
6. 每个提交的 `driver.py` 都在所声明的条件内生成，且未被 reference driver 或另一条件的
   candidate 替换；
7. 四个 cell 各自抽中的五个 private case 均在该条件最多三次生成 driver attempt 内通过独立
   物理验证；完整 TGCD/IVC 设计覆盖另行报告，且不得声称未抽中的 task 已被物理执行；
8. 分条件报告 `pass@0` 和 Repair 后结果，并提供不得用 aggregate 隐藏失败 cell 的配对比较；
9. actuator/physics-step、isolation、canonical-scene 和 from-scratch no-skeleton 检查通过；
10. 每个必需 trial 都有完整、可解码的视频及匹配 manifest；
11. 主线在 runtime 不导入或依赖 SDK 扩展或任何仓库同级目录，且正式 evaluation 的任何步骤都
    不下载代码、数据、标准或机器人资产。

如果四个 cell 都到达最终 verdict，但任何 cell 未通过一项或多项要求，正确表述是“双条件配对
实验已完成；所指明的 cell 合成失败”，不能表述为“双条件主线成功”。如果只有 reference driver
通过，正确表述是“参考校准通过”。

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
- an all-robot run as a gate for the two-condition, two-robot mainline;
- reference-driver substitution in a dynamic run;
- candidate-visible private suites, exact criteria, private definition-bearing reports, or
  privileged verdict state; complete candidate-facing Repair reports remain required by Section 3.5;
- direct state teleport as a substitute for actuator control and physics;
- video or model self-report as a substitute for the Harness verdict; or
- hardware, perception, SDK-fidelity, or sim-to-real claims from Direct-MuJoCo evidence.

The minimum focused checks, one reference positive control per acceptance robot, and the earliest
available real dynamic run take priority over broader cleanup.

**中文辅助说明。** 当前项目不得新增或扩展：

- 项目级 lifecycle、readiness、admission、promotion、freeze 或 experiment status 系统；
- artifact hash、签名、attestation、provenance chain、registry 或 governance service；
- 穷尽式 schema、防御性 abstraction layer、plugin system 或面向未来机器人的 framework；
- 广泛的 regression、fuzz、property、coverage-driven 或 adversarial-security 测试计划；
- 把全机器人运行作为双条件双机器人主线的门槛；
- 在 dynamic run 中用 reference driver 替换生成 driver；
- 向 candidate 暴露 private suite、精确 criterion、含私有定义的报告或 privileged verdict
  state；3.5 节要求的完整 candidate-facing Repair 报告仍必须提供；
- 用直接 state teleport 替代 actuator control 和 physics；
- 用视频或模型自述替代 Harness verdict；
- 根据 Direct-MuJoCo 证据声称 hardware、perception、SDK fidelity 或 sim-to-real 成果。

最低限度的聚焦检查、每个验收机器人一次 reference positive control，以及最早可执行的真实
dynamic run，优先于更广泛的整理。
