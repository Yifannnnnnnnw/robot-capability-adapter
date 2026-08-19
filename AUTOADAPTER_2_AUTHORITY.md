# Auto-Adapter 2.0 Direct-MuJoCo Mainline Authority / Direct-MuJoCo 主线权威文档

> **Document ID / 文档编号：** `AA2-AUTH`<br>
> **Document role / 文档角色：** sole normative project document / 项目唯一规范性文档<br>
> **Normative language / 规范语言：** English / 英文<br>
> **Chinese text / 中文文本：** auxiliary reading support only / 仅作辅助阅读<br>
> **Document revision / 文档版本：** `0.19.4`<br>
> **Effective date / 生效日期：** 2026-08-19<br>
> **Current direction / 当前方向：** Direct-MuJoCo is the default mainline; real-SDK and Translation work is an independent extension / Direct-MuJoCo 是默认主线；真实 SDK 与 Translation 工作是独立扩展线

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

Revision `0.19.3` makes capability admission proportional to the designed capability layer rather
than to the size of the source Task Library. Each capability has exactly one source-backed primary
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

**中文辅助说明。** `0.19.3` 让 capability 准入规模与设计出的 capability layer 对齐，而不是
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
capability layer is the reusable caller-facing interface and set of capability contracts; it is
designed and exposed, not treated as a separate executable artefact. Driver Synthesis produces a
candidate robot-specific driver for one declared robot configuration, and independent validation
determines whether that driver passes the sealed complete capability validation suite. Capability-layer use
therefore holds both the public layer and the validated driver that implements it fixed.

**中文辅助说明。** `0.19.1` 将生成的软件产物锁定为 **robot-specific driver**。Robot capability
layer 是面向调用方的可复用接口和 capability contract 集合；它被设计和暴露，不作为独立的可执行
产物。Driver Synthesis 为一个明确的机器人配置生成 candidate robot-specific driver，随后由独立
validation 判断该 driver 是否通过封存的完整 capability validation suite。因此，capability-layer use 必须
同时固定公开 layer 及实现它的已验证 driver。

Revision `0.19.0` aligns the Authority with the three thesis experiments. It separates the
robot-software synthesis track from capability-layer use, defines the three Auto-Adapter component
analyses, and distinguishes the initial two-case engineering acceptance milestone from the
declared cross-morphology experiment. It also makes explicit that cross-morphology results describe
associations rather than causal morphology effects.

**中文辅助说明。** `0.19.0` 使本 Authority 与论文的三个实验保持一致。它区分 robot-software
synthesis track 与 capability-layer use，明确 Auto-Adapter 的三项组件分析，并把首轮双案例工程验收
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
robot-specific driver. Historical Demo2 code and evidence may retain the former names only when
describing that historical implementation.

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
代码的阶段称为 `Driver Synthesis`。TGCD 只输出设计合同而不输出可执行 robot-specific driver，
因此不使用 `synthesis` 一词。旧名称只允许在明确描述历史 Demo2 代码或证据时保留。

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
| **robot capability layer** | The reusable, caller-facing software interface formed by the public capability contracts through which a high-level controller invokes robot operations. It is designed, exposed, and used; it is not the executable software product. |
| **capability** | One implementation-independent contract for a callable robot operation exposed by the robot capability layer, defined by its semantics, inputs, outputs, preconditions, and measurable acceptance obligations. `Skill` is reserved for a task-level, temporally extended behaviour and is not a synonym for a capability. |
| **robot-specific driver** | The primary executable, application-level robot-integration artefact initially produced by Driver Synthesis and, when required, revised by Repair for one declared robot configuration. It implements the robot capability layer; it is not an operating-system device driver. |
| **capability-level pass criterion** | A measurable acceptance condition for one capability. It is derived from source-backed task pass standards and is independently audited, compiled, and evaluated outside the candidate driver. |
| **Task Demo** | A post-admission demonstration that runs five recorded, uniformly sampled original Task Library tasks, including all scoring clauses of each selected task, with the fixed capability-validated driver. It has a separate verdict, is not a driver-synthesis gate, and cannot trigger same-run Repair. |
| **robot-software synthesis** | The experiment-level process of designing a robot capability layer and its capability-level pass criteria, synthesising a robot-specific driver that implements the layer, and independently validating that driver. The primary executable product is the robot-specific driver; TGCD still produces only a design contract, and `Driver Synthesis` remains the phase that produces executable code. |
| **capability-layer use** | The use of a fixed robot capability layer, backed by the same fixed and validated robot-specific driver, by an otherwise matched high-level controller whose LLM backbone is the experimental variable. Use evidence is reported separately and does not establish driver synthesis. |
| **low-level motion-control capability** | A capability whose implementation converts a requested robot operation into robot-specific actuation, kinematic or locomotion control, and physics stepping. It must not be called a low-level motion-control skill. |
| **trusted skeleton** | Robot-control implementation assistance available only in the skeleton-assisted generation condition. It is distinct from the high-level controller. |
| **robot morphology** | A robot's physical form and joint arrangement. It is distinct from a **robot configuration**, which identifies the exact model, assets, actuators, and control setup used in a run. |

The labels `L0` and `L1` are not normative terms and must not be used in research claims or thesis
prose. Exact phase names such as `Task-Grounded Capability Design`, `Independent Validation
Compiler`, `Driver Synthesis`, `Repair`, `Task Demo`, and `Evolution` retain the meanings defined in
Sections 3.1--3.7.

**中文辅助说明。** 上表中的术语在本 Authority、实验报告和论文正文中保持一致。当前研究框架
写作 `Auto-Adapter`；`AutoAdapter` 仅保留给代码或目录标识，以及历史系统 `AutoAdapter 1.0`
及其产物。`high-level controller` 负责在任务层选择和编排 capability；在 LLM backbone 对比中，
其架构、prompt、tool exposure 和 decision loop 保持固定，只有 backbone 改变。`robot capability
layer` 是由公开 capability contract 构成、供该 controller 调用机器人操作的可复用接口；它不是
可执行软件产物。`capability` 表示能力层暴露的一项与实现无关的可调用机器人操作合同；`skill`
只表示任务层的时间扩展行为，不得作为 capability 的同义词。`robot-specific driver` 是针对一个
准确机器人配置、由 Driver Synthesis 首次生成并在需要时由 Repair 修订的主要可执行、应用层
机器人集成产物；它负责实现该 layer，不是操作系统 device driver。
`robot-software synthesis` 表示从能力层设计、capability-level pass criteria 设计、driver 合成到
独立验证的实验级过程，但 TGCD 本身仍不称为 synthesis。`capability-layer use` 必须同时固定公开
layer 及实现它的同一套固定且已验证的 driver；其证据不能代替 driver-synthesis 证据。`low-level
motion-control capability` 不得写作 low-level motion-control skill。`trusted skeleton` 是
skeleton-assisted 条件中的机器人控制实现辅助，不是 high-level controller。`robot morphology`
指物理形态和关节排列；`robot configuration` 指一次运行使用的准确模型、资产、actuator 和控制
设置。`Task Demo` 是 driver 通过 capability validation 后，使用该固定 driver 运行五项均匀随机
抽取的原始 Task Library task 及其全部 scoring clause 的演示阶段；它有独立 verdict，不是
driver-synthesis gate，也不能触发本轮 Repair。
`L0`、`L1` 不属于规范术语。

---

## 1. Project objective and claim boundary / 项目目标与主张边界

### 1.1 Research objective / 研究目标

Build the simplest experiment-grade Auto-Adapter 2.0 framework and evaluation needed to determine,
under controlled conditions, when LLM backbones can synthesise robot-specific drivers and use the
reusable robot capability layers those drivers implement, which selected Auto-Adapter components
produce measurable differences in robot-software synthesis quality, and how robot-specific driver
synthesis for low-level motion-control capabilities varies across robot morphologies.

The research programme separates two outcomes that must not be conflated. The robot-specific
driver-synthesis outcome concerns whether, under the Auto-Adapter workflow, the generated driver
implements the sealed layer design and passes independent validation. Capability-layer use concerns
whether an LLM, acting through the same fixed layer, the same fixed and validated driver, and the
same high-level-controller implementation, can complete matched MuJoCo tasks. Success in one outcome
is not evidence of success in the other.

The initial two-robot, two-generation-condition mainline remains an engineering acceptance
milestone. It is not the complete thesis experiment set, a production platform, or a claim of
universal robot support.

**中文辅助说明。** 构建最简单的实验级 Auto-Adapter 2.0 框架和评估，以在受控条件下研究：
不同 LLM backbone 何时能够合成 robot-specific driver，并使用这些 driver 所实现的可复用 robot
capability layer；所选 Auto-Adapter 组件在匹配比较中是否产生可测量的 robot-software synthesis
质量差异；以及为 low-level motion-control capabilities 合成 robot-specific driver 的结果如何随
robot morphology 而变化。研究必须区分两种不能混为一谈的结果：robot-specific driver-synthesis
outcome 研究在 Auto-Adapter workflow 下，生成的 driver 是否实现封存的 layer 设计并通过独立验证；
capability-layer use 研究 LLM 能否通过同一套固定 layer、实现它的同一套固定且已验证的 driver，
以及相同 high-level-controller 实现完成匹配的 MuJoCo 任务。任一结果成功都不能作为另一结果成功
的证据。首轮双机器人、双生成条件主线仍是工程验收里程碑；它不是完整论文实验集、生产平台或
通用机器人支持主张。

### 1.2 Claim boundary / 主张边界

The project may claim only what the corresponding experiment evidence directly supports:

- comparative robot-specific driver-synthesis outcomes for the declared LLM backbones under the
  skeleton-assisted and from-scratch generation conditions, with the Auto-Adapter configuration,
  robot inputs, source-backed Task Library, source-task pass standards, evaluation protocol, and
  resource budgets fixed within each condition;
- comparative outcomes for capability-layer use by the declared LLM backbones using the same fixed robot
  capability layer, the same fixed and validated robot-specific driver that implements it, and the
  same high-level-controller implementation;
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
visual perception, production reliability, universal model or robot superiority, Task Demo
completion for tasks outside the five-task sample, or a causal morphology effect. A capability-level pass criterion
is not established as reliable merely because the model wrote it or the compiler accepted its
syntax. A cross-morphology comparison is associational because morphology co-varies with actuation,
dynamics, task applicability, and MuJoCo control structure.

**中文辅助说明。** 项目只能提出对应实验直接证据支持的结论：在每种生成条件内固定
Auto-Adapter 配置、机器人输入、有来源 Task Library、来源 task 通过标准、评估协议和资源预算后，
不同 LLM backbone 在 skeleton-assisted 与 from-scratch 条件中的 robot-specific driver 合成结果；
不同 backbone 通过同一套固定 robot capability layer、实现它的同一套固定且已验证的
robot-specific driver，以及相同 high-level-controller 实现所得的 capability-layer use 结果；模型根据至少
二十项有来源任务完成的 task-grounded capability 设计；由模型设计、具有来源依据且由实现
不可见 IVC 独立审计和编译，并由 Harness 在已声明校准和 false-success 检查下评估的
capability-level pass criteria；只有在 Experience-enabled run 与匹配的 no-Experience control
比较时，才能报告与 reviewed Evolution Experience 相关的后续运行差异；独立 Direct-MuJoCo
验证、首次与 Repair 后结果、失败模式和资源使用；以及固定实验控制下、针对已声明机器人 cohort
的描述性 cross-morphology 差异。这些证据不证明真实 SDK 保真度、硬件有效性、sim-to-real
迁移、视觉感知、生产可靠性、模型或机器人的普遍优越性、五-task 样本外任务的 Task Demo
完成结果或 morphology
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

The two configurations above define only the first mainline engineering-acceptance milestone for
robot-specific driver synthesis. They do not define the complete Experiment 3 cohort and do not by
themselves support a general or causal morphology claim.

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

上述两个配置只定义首轮 robot-specific driver synthesis 主线工程验收里程碑，不构成 Experiment 3
的完整 cohort，也不能单独支持一般性或因果性的 morphology 主张。Experiment 3 开始前，版本化 experiment
manifest 必须声明全部纳入的机器人配置及其 morphology 类别。每个配置必须满足相同的 Task
Library、来源 lineage、asset closure、validation 和 evidence 要求。只有 MJCF 或 URDF asset
的机器人不属于已准入实验案例。

### 1.4 Research questions / 研究问题

The project evaluates three controlled research questions:

1. **RQ1---Comparison of LLM backbones in robot-specific driver synthesis and capability-layer use.**

   **Driver synthesis.** With the Auto-Adapter framework, robot inputs, source-backed Task Library,
   source-task pass standards, private-case construction policy, Harness measurement and verdict
   rules, and resource budget held fixed, how do LLM backbones differ in synthesising a
   robot-specific driver that implements the designed reusable robot capability layer under the
   skeleton-assisted and from-scratch generation conditions?

   **Use.** For each robot configuration, with the same fixed robot capability layer, the same fixed
   and validated robot-specific driver that implements it, high-level-controller implementation,
   matched task inputs, evaluation protocol, and resource budget held fixed, how do the same LLM
   backbones differ in using that layer to complete the tasks?

2. **RQ2---Component analysis of the Auto-Adapter framework.**

   **(a) Task-grounded capability design.** Can an LLM derive five to ten reusable capabilities
   from at least twenty source-backed tasks without receiving a pre-authored capability catalogue
   or task-to-capability mapping?

   **(b) Capability-level pass-criteria design.** Can the LLM design source-grounded
   capability-level pass criteria without human predefinition at the capability level, and can the
   implementation-blind IVC independently audit and compile those criteria into executable
   validation?

   **(c) Continued Evolution.** Does reviewed evidence from completed runs improve robot-software
   synthesis quality in matched later runs relative to the same condition without that Experience?

3. **RQ3---Cross-morphology analysis of robot-specific driver synthesis for low-level
   motion-control capabilities.**

   With the LLM backbone, high-level-controller implementation, Auto-Adapter configuration,
   generation condition, evaluation protocol, and resource budget held fixed, how are
   robot-morphology differences associated with validation success, failure patterns, and resource
   use when synthesising robot-specific drivers that implement low-level motion-control
   capabilities?

For RQ1 driver synthesis, fixed validation criteria means fixed source-task pass standards, private
task instances, measurement semantics, and Harness verdict rules. Model-authored capability-level
pass criteria remain an evaluated output and may therefore differ across LLM backbones. Within each
generation condition, all backbones receive the same public inputs; the two generation conditions
differ only in their authorised access to the trusted skeleton.

Reference drivers are positive controls for the Framework and Direct-MuJoCo execution route. They
are not model conditions and cannot be counted as evidence of model-based robot-specific driver
synthesis or capability-layer use. RQ3 is descriptive and associational: the declared design does
not identify a causal morphology effect.

**中文辅助说明。** 项目评估三个受控研究问题：

1. **RQ1——LLM backbone 在 robot-specific driver synthesis 与 capability-layer use 中的比较。**
   **Driver synthesis：** 在固定 Auto-Adapter framework、机器人输入、有来源 Task Library、来源 task
   通过标准、private-case 构造 policy、Harness 测量与判定规则以及资源预算时，不同 LLM backbone
   在 skeleton-assisted 与 from-scratch 条件下合成实现已设计 robot capability layer 的
   robot-specific driver，其能力有何差异？
   **Use：** 对每个机器人配置，在固定 robot capability layer、实现它的同一套固定且已验证的
   robot-specific driver、high-level-controller 实现、匹配任务输入、评估协议和资源预算时，相同的
   一组 LLM backbone 使用该 layer 完成任务的能力有何差异？
2. **RQ2——Auto-Adapter framework 的组件分析。**
   **(a) Task-grounded capability design：** 在不接收预写 capability catalogue 或
   task-to-capability mapping 的情况下，LLM 能否从至少二十项有来源任务中归纳五至十项可复用
   capabilities？
   **(b) Capability-level pass-criteria design：** 在 capability level 没有人工预定义的情况下，
   LLM 能否设计有来源依据的 capability-level pass criteria，且实现不可见的 IVC 能否独立审计并
   将其编译为可执行 validation？
   **(c) Continued Evolution：** 与不使用该 Experience 的匹配条件相比，来自已完成运行且经过
   审查的证据能否提升后续匹配运行的 robot-software synthesis 质量？
3. **RQ3——为 low-level motion-control capabilities 合成 robot-specific driver 的
   cross-morphology 分析。**
   在固定 LLM backbone、high-level-controller 实现、Auto-Adapter 配置、生成条件、评估协议和
   资源预算时，不同 robot morphology 与实现 low-level motion-control capabilities 的
   robot-specific driver 合成中的 validation success、failure pattern 和 resource use 差异具有何种
   关联？

在 RQ1 driver synthesis 中，固定 validation criteria 是指固定来源 task 通过标准、私有 task instance、
测量语义和 Harness 判定规则。由模型生成的 capability-level pass criteria 仍是被评估输出，因此
可以随 LLM backbone 不同而变化。在每种生成条件内，所有 backbone 接收相同公开输入；两种生成
条件只在是否获准访问 trusted skeleton 这一点上不同。Reference driver 是 Framework 和
Direct-MuJoCo 执行路径的正向对照，不是模型条件，不能计为 model-based robot-specific driver
synthesis 或 capability-layer use 的证据。RQ3 只能进行描述性和关联性解释；当前设计不能识别
morphology 的因果效应。

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
```

The vendored AutoAdapter 1.0 STUDY/GENERATE substrate is derived from commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`. Later changes may make the code self-contained, but
must preserve the observable contract below rather than silently substitute a fixed driver.

The two generation conditions are separate experimental cells. For one robot/model replicate they
reuse the same sealed `capability_design.json`, the same complete
`capability_validation_suite.json`, the same recorded five-task `task_demo_suite.json`, frozen Task
Library snapshot, model identity, and environment.
They run in isolated workspaces; neither condition may
inspect or reuse the other condition's candidate, trace, validation result, Repair history, or
generated driver. Each condition has its own maximum of three submitted driver attempts and
receives an independent capability-validation verdict. Only an admitted final driver enters Task
Demo; that later verdict is recorded separately and never causes same-run Repair. Results are never
merged into one ambiguous driver result.

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
```

仓库内置的 AutoAdapter 1.0 STUDY/GENERATE 基础来自提交
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`。后续可以把代码改为自包含，但必须保持下述
可观察合同，不能悄悄改为输出固定 driver。

两种生成条件是相互独立的实验 cell。对于同一个机器人/model replicate，它们复用同一份已封存
`capability_design.json`、同一套完整 `capability_validation_suite.json`、同一套有记录的五-task
`task_demo_suite.json`、已封存 Task Library 快照、模型身份和环境，
并在隔离的 workspace 中运行；任一条件都不得检查或复用另一条件的 candidate、trace、验证结果、
Repair 历史或生成的 driver。每种条件各自最多提交三个 driver attempt，并分别获得 Harness
capability-validation verdict。只有通过准入的最终 driver 才进入 Task Demo；后续 Demo verdict
单独记录，绝不触发本轮 Repair。结果不得合并成一个含糊的 driver 结果。

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
4. select concrete private case inputs, scene/reset state, repetitions, termination, and
   anti-false-pass guards; and
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
metric 语义绑定到可信 MuJoCo observation 和 Framework measurement 代码；选择具体私有 case
输入、scene/reset 状态、重复次数、终止条件和 anti-false-pass guard；并生成逐 case、
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

The Framework applies one deterministic bounded projection when serializing this tool conversation
for the next model turn. It retains the initial public task context, exactly one latest complete
`driver.py` snapshot, and the three most recent completed tool-interaction groups. Superseded
`read_driver` and `write_driver` source payloads are replaced by tool name, driver revision, source
character count, and result status; similarly superseded development-probe scripts may be replaced
by probe identity, script character count, and result status while their bounded diagnostics remain
available. If a hard history-character budget still requires eviction, oldest completed groups are
removed first and represented by the same deterministic metadata. This model-facing projection does
not modify the complete on-disk trace, does not use another model to summarize history, and cannot
introduce private Harness information.

**上下文辅助说明。** Framework 在向下一模型回合序列化工具对话时应用一份确定性的有界投影：
保留首次公开任务上下文、恰好一份最新完整 `driver.py`，以及最近三个已完成工具交互组。已被取代
的 `read_driver`/`write_driver` 源码 payload 改为 tool 名称、driver revision、源码字符数和结果
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
that every Task Library task was run. This
Demo alone also does not establish the RQ1 capability-layer-use result unless the declared fixed
high-level-controller path was actually part of the run. Its terminal report may be included in the
input projection for Evolution, which can influence only a later matched run.

**中文辅助说明。** 只有某一条件的最终生成 driver 通过完整 capability validation suite 后，
Task Demo 才开始。Framework 固定该已准入 driver，通过同一可信 Direct-MuJoCo Harness 运行一次
已封存的五-task `task_demo_suite.json`，并单独记录 verdict 和逐 trial 视频。Task Demo 不会重开
生成、不消耗 driver attempt，也不触发本轮 Repair。Demo 失败或证据不完整必须如实报告，但不
改变此前已经得到的 driver-synthesis pass。

这五项被选 task 及其全部 scoring clause 只是有界 Demo 样本，不能证明 Task Library 的全部任务
均已运行。如果本次 run 没有
实际包含已声明且固定的 high-level-controller 路径，仅有该 Demo 也不能建立 RQ1
capability-layer-use 结果。其终态报告可以进入 Evolution 的输入投影，但只能影响后续匹配 run。

### 3.7 Evolution / 经验演化

Evolution is a non-blocking sidecar that reads a bounded projection of the terminal structured
report and may propose a reviewed Experience record for a later run. The projection retains
terminal execution/verdict facts, attempt summaries, outcome counts, and complete failed-trial
diagnostics, but may replace repeated full trajectory samples with sample counts because the full
report remains retained as evidence and has already been available to Repair. Evolution cannot
change the current driver, suite, criterion, verdict, retry decision, or run inputs. Failure of
Evolution does not turn a completed validation run into a driver-synthesis failure.

**中文辅助说明。** Evolution 是非阻塞 sidecar：它读取最终结构化报告的有界投影，并可为后续
运行提出一条经过审查的 Experience 记录。该投影保留终态执行/verdict 事实、attempt 摘要、结果
计数和完整失败 trial 诊断；由于完整报告已保留且已提供给 Repair，重复的完整轨迹 sample 可以
替换为 sample count。Evolution 不得改变当前 driver、suite、criterion、verdict、retry 决定或
运行输入。Evolution 失败不会把已经完成的验证运行变成一次 driver-synthesis failure。

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
| Dynamic generation-condition executed | Source-backed 20+ task snapshot, real-model TGCD design of 5–10 capability contracts without a pre-authored effect policy, complete IVC-audited `capability_validation_suite.json`, named skeleton-assisted or from-scratch condition, real model identities/calls, model-generated `driver.py`, condition-appropriate STUDY/GENERATE trace, and real MuJoCo capability validation reaching a terminal verdict | That capability-design and generation condition executed end to end | The capability requirements passed, Task Demo ran, or the other condition executed |
| Single-robot condition success | Dynamic condition evidence plus every case in the complete capability validation suite passes within that condition's declared attempt budget | The generated robot-specific driver passed capability admission for that robot, condition, and run | Any Task Demo passed, or the paired condition, two-robot mainline, or SDK path succeeded |
| Task Demo executed | A capability-validated fixed driver, sealed random five-task `task_demo_suite.json`, separate Harness verdict, and complete videos | The five selected tasks and all of their scoring clauses were demonstrated with that admitted driver | Every Task Library task passed, driver synthesis failed, or RQ1 capability-layer use succeeded without the declared high-level controller |
| Paired two-condition experiment completed | Both generation conditions reach capability-validation terminal verdicts for both fixed robots using the same sealed per-robot `capability_design.json`, `capability_validation_suite.json`, and declared experiment configuration | The four-cell Direct-MuJoCo comparison executed | Every cell passed or every Task Demo ran |
| Two-condition, two-robot mainline success | All four robot-by-generation-condition cells independently satisfy single-robot condition success | The first paired Direct-MuJoCo mainline experiment succeeded | SDK fidelity, hardware validity, sim-to-real, or universal applicability |
| SDK-grounded extension evidence | Real SDK application logic and robot-specific Translation execute bidirectionally with MuJoCo | The named SDK-extension route executed | Hardware equivalence or mainline replacement |

**中文辅助表。**

| 证据类别 | 必须具备的事实 | 可以支持的结论 | 不能支持的结论 |
|---|---|---|---|
| 参考校准 | 经审查的 reference driver、真实 MuJoCo、完整 `capability_validation_suite.json` 的 Harness verdict 和完整视频 | 所选资产、controller baseline、完整 capability-validation 路径、Harness 和录像路径可行 | driver 由任何模型生成，或任何 Task Demo 已通过 |
| 动态生成条件已执行 | 有来源的 20+ task 快照、没有预写 effect policy 的真实模型 TGCD 五至十项 capability contract 设计、IVC 完整审计的 `capability_validation_suite.json`、明确的 skeleton-assisted 或 from-scratch 条件、真实模型身份和调用、模型生成的 `driver.py`、符合该条件的 STUDY/GENERATE trace，以及到达最终 verdict 的真实 MuJoCo capability validation | capability 设计及该生成条件已完成端到端执行 | capability 要求已通过、Task Demo 已运行，或另一条件已执行 |
| 单机器人条件成功 | 具备动态条件证据，且完整 capability validation suite 中每个 case 均在该条件声明的 attempt 预算内通过 | 该机器人、该生成条件和该 run 生成的 robot-specific driver 通过 capability 准入 | 任何 Task Demo 已通过，或配对条件、双机器人主线或 SDK 路径成功 |
| Task Demo 已执行 | 固定的 capability-validated driver、封存的随机五-task `task_demo_suite.json`、独立 Harness verdict 和完整视频 | 该已准入 driver 完成了所选五项 task 及其全部 scoring clause 的演示 | Task Library 全部任务通过、driver synthesis 失败，或在没有声明 high-level controller 时 RQ1 capability-layer use 成功 |
| 双条件配对实验已完成 | 两种生成条件在两个固定机器人上均使用相同的每机器人封存 `capability_design.json`、`capability_validation_suite.json` 和声明的实验配置到达 capability-validation 最终 verdict | 四个 Direct-MuJoCo 实验 cell 已执行 | 每个 cell 均通过，或每个 Task Demo 均已运行 |
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
- TGCD, IVC, STUDY, GENERATE, capability validation, Repair, Task Demo, and Evolution outcomes;
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
task 数量和来源覆盖；模型 provider、准确模型标识和 generation condition；TGCD 设计的
capability 数量、task→capability 覆盖、来源标准覆盖及 IVC 审计结果；TGCD、IVC、STUDY、
GENERATE、Capability Validation、Repair、Task Demo 和 Evolution 结果；attempt 次数、分开的首次
与最终 capability-validation verdict，以及单独的 Task Demo verdict；
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
   same sealed per-robot Capability Design and complete capability validation suite, then run the
   same sealed random five-task Task Demo only for each capability-validated final driver;
5. build private-suite isolation, candidate-process isolation, canonical-session enforcement,
   anti-teleport checks, and actuator-plus-physics-step evidence into the Harness path itself;
6. expose to Repair the complete candidate-facing attempt report and media while redacting only the
   private validation definitions, enforce three total driver attempts per condition, preserve the
   full budget-bounded local Python/MuJoCo development probe, keep the capability suite unchanged,
   and never feed the same-run Task Demo report back into Repair;
7. record one complete Framework-controlled video and manifest entry for every required capability-validation or Task Demo case
   repetition, with incomplete media invalidating that trial's evidence;
8. report pipeline execution, model calls, in-run generation, capability validation, initial and final
   capability verdicts, Task Demo execution/verdict, clause/case counts, attempts, and video completeness separately, and make success-oriented
   commands fail when required physical evidence fails;
9. calibrate both reference drivers against the resulting complete capability suites before making dynamic claims,
   then retain truthful terminal evidence for all four robot-by-generation-condition cells; and
10. keep Evolution terminal and non-blocking so that it cannot alter the current candidate, either suite,
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
   封存 Capability Design 和同一套完整 capability validation suite；仅当最终 driver 通过准入后，
   才使用同一套封存的随机五-task Task Demo；
5. 在 Harness 路径内直接实现私有 suite 隔离、candidate 进程隔离、canonical session、
   anti-teleport 检查以及 actuator 加 physics-step 证据；
6. Repair 可以接收完整的 candidate-facing attempt 报告和媒体，只删除私有 validation 定义；
   每种条件最多三个 driver attempt，保留完整但有预算上限的本地 Python/MuJoCo 开发 probe，且
   capability suite 始终不变，且本轮 Task Demo 报告绝不回传给 Repair；
7. 每个必需 capability-validation 或 Task Demo case repetition 都有独立、完整、Framework 控制的视频和 manifest 记录；
   媒体不完整时该 trial 的证据无效；
8. 分开报告 pipeline 执行、模型调用、本次生成、capability validation、首次/最终 capability
   verdict、Task Demo 执行/verdict、clause/case 数、
   attempt 和视频完整性；用于证明成功的命令在必需物理证据失败时必须失败；
9. 先让两个 reference driver 通过最终封存的完整 capability suite 校准，再提出动态结论，并为四个机器人乘生成
   条件 cell 保留真实的最终证据；
10. Evolution 必须位于 terminal verdict 之后且不阻塞主线，不能改变当前 candidate、任一 suite、retry
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
   or task-to-effect allowlist, and IVC confirms one source-backed primary plus at most one
   justified robustness case per capability;
4. anti-teleport, canonical-scene, and private-suite isolation checks pass;
5. both reference drivers pass the resulting sealed complete capability validation suite under the same rules
   used for generated drivers, with complete per-trial videos;
6. at least one real dynamic canary run reaches a terminal physical verdict and leaves model and
   generation traces, even if its candidate fails a requirement; and
7. the dependency environment declared by `demo3/pyproject.toml` can install before the run, execute
   the focused checks, and run the canary without undeclared packages or files.

Directory movement is mechanical and uses separate commits from behavioral repair. Moving the
implementation to the canonical path does not itself prove paired two-condition driver-synthesis
success.

**中文辅助说明。** 只有满足以下全部条件，实现才可以从 `demo3/` 迁移到 `general_demo/`：

1. Demo3 self-containment 检查证明全部项目自编 import 和文件解析都位于 `demo3/` 下，不依赖
   同级/外部源码树、机器绝对路径、逃逸 symlink、submodule 或运行时资产下载；
2. 两个验收 package 均能从主线拥有的 Morphology、Tasks、Experience、assets、skeletons 和
   references 中解析；runnable index 暴露的每个机器人均通过 Task Library 准入；每个机器人
   至少二十项适用任务中的每条评分 clause 都具有精确来源 lineage 和机器可表达通过标准；
3. 一次真实 TGCD run 在没有预写 effect catalog 或 task→effect allowlist 的情况下设计五至
   十项 capability contract，并由 IVC 确认每项 capability 一个有来源 primary case 及最多一个
   有明确理由的 robustness case；
4. anti-teleport、canonical-scene 和 private-suite isolation 检查通过；
5. 两个 reference driver 均在与生成 driver 相同的规则下通过最终封存的完整 capability validation suite，并
   具有完整的逐 trial 视频；
6. 至少一次真实 dynamic canary 运行到达最终物理 verdict，并留下模型和生成 trace，即使
   candidate 未通过某项要求；
7. `demo3/pyproject.toml` 声明的依赖环境可以在 run 前完成安装，并在没有未声明 package 或文件
   的情况下运行聚焦检查和 canary。

目录移动必须是机械操作，并与行为修复分开提交。实现移动到 canonical path 本身，并不能证明
双条件配对 driver synthesis 成功。

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
4. one real-model complete `capability_validation_suite.json` produced by IVC passes the
   no-weaker-selected-standard and per-capability primary/robustness audit and remains unchanged
   across reference calibration, both generation conditions, and all Repair attempts; the Framework
   separately seals one recorded random five-task `task_demo_suite.json`, while each condition runs its own
   real STUDY and GENERATE or GEN_ALGO calls and retains their model/call records;
5. the two conditions use isolated workspaces and do not exchange candidates, traces, validation
   results, Repair history, or generated code;
6. each submitted `driver.py` was generated inside its declared condition and was not replaced by a
   reference driver or by the other condition's candidate;
7. every case in the complete capability validation suite passes in each of the four cells within
   that condition's maximum three total generated-driver attempts; each admitted final driver then
   executes the separately reported five-task Task Demo without that Demo changing the synthesis
   verdict or triggering Repair;
8. condition-specific `pass@0` and post-Repair results are reported separately, alongside a paired
   comparison that never hides a failed cell in an aggregate;
9. actuator/physics-step, isolation, canonical-scene, and from-scratch no-skeleton checks pass;
10. every required capability-validation and Task Demo trial has complete decodable video and a matching manifest; and
11. the mainline has no runtime import or dependency on the SDK extension or any repository sibling,
    and no formal evaluation step downloads code, data, standards, or robot assets.

If all four cells reach terminal verdicts but any cell fails one or more requirements, the correct
claim is “the paired two-condition experiment completed; the named cell or cells failed driver
synthesis,” not “the two-condition mainline succeeded.” If only a reference driver passes, the
correct claim is “reference calibration passed.”

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
4. 一套由 IVC 生成的完整 `capability_validation_suite.json` 通过“不弱化所选来源标准”和逐
   capability primary/robustness 审计，并在 reference 校准、两种生成条件和所有 Repair attempt
   间保持不变；Framework 另行封存一套有记录的随机五-task `task_demo_suite.json`；每种条件分别执行自己的真实 STUDY 与
   GENERATE 或 GEN_ALGO 调用，并保留模型和调用记录；
5. 两种条件使用隔离 workspace，不能交换 candidate、trace、验证结果、Repair 历史或生成代码；
6. 每个提交的 `driver.py` 都在所声明的条件内生成，且未被 reference driver 或另一条件的
   candidate 替换；
7. 四个 cell 的完整 capability validation suite 中每个 case 均在该条件最多三次生成 driver
   attempt 内通过；随后每个已准入最终 driver 执行单独报告的五-task Task Demo，该 Demo 不改变
   synthesis verdict，也不触发 Repair；
8. 分条件报告 `pass@0` 和 Repair 后结果，并提供不得用 aggregate 隐藏失败 cell 的配对比较；
9. actuator/physics-step、isolation、canonical-scene 和 from-scratch no-skeleton 检查通过；
10. 每个必需 capability-validation 和 Task Demo trial 都有完整、可解码的视频及匹配 manifest；
11. 主线在 runtime 不导入或依赖 SDK 扩展或任何仓库同级目录，且正式 evaluation 的任何步骤都
    不下载代码、数据、标准或机器人资产。

如果四个 cell 都到达最终 verdict，但任何 cell 未通过一项或多项要求，正确表述是“双条件配对
实验已完成；所指明的 cell driver synthesis 失败”，不能表述为“双条件主线成功”。如果只有
reference driver 通过，正确表述是“参考校准通过”。

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
