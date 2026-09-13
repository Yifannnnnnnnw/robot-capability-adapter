AA1 自动能力设计与场景验证：讨论整理（2026-09-13）
====================================================

本文整理当前代码事实、已澄清的概念，以及讨论中的扩展方案。核对基线为分支 `codex/aa1-task-grounded-capabilities`，代码提交 `7f37f27e`，已有记录提交 `9cdab41c`。场景与动态 criteria 验证尚未接入主线；本文不是正式实验协议。

**1. 我们要建立的关系**

```text
公开任务需求
  → 设计供调用者使用的 capability 接口
  → 定义该接口调用后应该产生的机器人效果
  → 给出可测量的 criteria
  → 准备对应场景、初态、具体 request 和观测绑定
  → 在实际仿真中调用接口并检查效果
```

capability 是被调用的控制接口。driver 实现接口内部的控制逻辑。准确表述是“调用 cap、执行控制、检查调用效果是否满足 criteria”。任务由调用者通过一个或多个 cap 调用来执行；task_support 是设计中声明的支持关系，不能当成任务成功证据。

本次已经实现的顺序是 `真实 MJCF STUDY → TGCD → GENERATE`。TGCD 位于本次 study 成功之后，不要求用户提前提供一次已经完成的 study。它发生在 driver generation 之前。没有实施“study 之前直接决定全部 cap”的另一条路线。

**2. 固定术语，避免 framework 指代混乱**

| 名称 | 在这轮讨论中的含义 |
|---|---|
| AA1 系统 | 包含流程程序、LLM agent、工具、控制库、生成产物及验证代码的整套系统 |
| 流程框架 / 宿主程序 | 以 `orchestrator.py` 为入口的 Python 程序，组织各阶段、传输入、接产物、运行验证和构造修复反馈 |
| Agent | 框架通过 ReactLoop 启动的模型工具循环，用于 study、设计、代码生成或修复 |
| Skeleton | 预先编写的 Python 控制库，提供 IK、关节控制、仿真推进及加载辅助函数；它不是机器人物理模型 |
| Driver | 本次生成的 Python 控制程序，提供可调用的 cap 接口 |
| Validator | 框架调用的验证代码，执行 case、采集状态、计算指标、写报告 |
| MuJoCo model | 机器人和场景的结构、物理参数、关节与执行器信息 |
| MuJoCo data | 当前时间、位置、速度、控制量等仿真运行状态 |
| ArmSpec 等控制 spec | 机器人控制绑定和控制参数，例如关节名、执行器名、末端 site 名；不是 MuJoCo 的 MjSpec |
| MuJoCo MjSpec | 用于加载、编辑和编译机器人及场景描述的对象 |

从工作过程看，driver 读取状态、计算控制、写入执行器控制量并推进仿真，再继续读取状态。framework 负责安排调用及评价结果。

`SkeletonBase.__init__(model, data, spec)` 是 Robot 创建时使用的初始化函数：保存已经传入的对象。加载 XML、创建 model/data 的工作位于 `from_mjcf()` 加载辅助函数中。二者不能混称为构造器在创建整个世界。

**3. 目前已经实现的能力设计部分**

当前配置：

```python
prepare_capabilities: bool = False
capability_design_path: Path | None = None
max_iters_capability_design: int = 6
```

- 自动模式在 study 成功后运行 TGCD。
- 指定已有 design 文件时，study 后加载它并跳过模型 TGCD。
- 两个选项互斥；都未指定时保留旧 catalog 路线。
- 动态模式保留调用者的实际 MJCF；不自动替换成旧 capability profile 对应场景。
- study/TGCD 失败时阻止 generate，不回退旧 design。
- 动态模式目前只允许到 study 或 generate，不能进入旧 suite 宣告新 criteria 通过。

公开任务库已经放在 `AA1/auto_adapter/task_libraries/`：15 个包，13 个非空，共 260 条任务。`index.yaml` 只绑定 AA1 robot ID 与任务包路径，尚不做环境装配。Piper 包含 20 条公开任务。来源文件保留，但不作为独立模型输入。

TGCD 初始 user 消息给出：

```text
robot_configuration_id: piper
study_path: <本次真实 study.json 的路径>
catalog_path: <Piper 任务包 catalog.json 的路径>
skeleton_context_path: <可选控制库说明文件路径>
```

模型通过 `read_file` 读取 study 和对应机器人的 catalog。任务包身份也从 catalog 读取。Skeleton-assisted 模式额外提供底层控制说明，from-scratch 不提供。

当前没有额外输入 `sources.json`；但任务内已有的来源引用及输出 criteria 的 `source_refs` 仍保留。不能把“不单独输入来源文件”理解为“删除所有来源依据”。

新运行不再生成 `authoring_brief.json` 和 `public_inputs.json` 这两份重复整理输入；历史诊断中的旧文件保留。

实际 MJCF 路径仍传给 TGCD 的 Python 接口，但当前 TGCD agent 并没有 MJCF 的 read_file 权限或 MuJoCo 执行工具，也没有把完整 MJCF 文本再放入其初始 prompt。运行时场景构造仍需要实际 MJCF 文件，study 不能替代物理模型。

**4. TGCD 的工作形式和消息交接**

TGCD 当前是一个 ReactLoop，联合生成 cap、criteria 和 task_support，工具只有：

```text
read_file
write_file
```

没有新增 `submit_design`，也没有 TGCD `local_exec`。模型必须在本轮读取 study 与实际 catalog 后，才写 `draft/capability_design.json`；允许分块追加。Python 在写入后解析和检查草稿，错误作为工具结果返回同一循环，在最多六次模型调用内修正。

输出组织：

```text
capabilities[]
  capability_id / method_name / description / effect
  request_schema
  preconditions / temporal_semantics / invariants / failure_behavior
  criteria[]

task_support[]
  task_id / capability_id / rationale
```

`capability_design.json` 是主文件；`criteria.json` 由 Python 导出供查看，不是另一份独立可编辑标准。当前实现要求每个 cap 一条 criterion；不是任意数量 criteria 已经全部支持。

Python 只做必要结构、身份、唯一性、任务引用及数值检查。这不能证明标准合理、目标可达或机器人调用能够满足标准。无直接依据的数字应标明提议值，不声称已经校准。

两段交接方式目前不同：

| 交接 | 当前实际行为 |
|---|---|
| study / catalog → TGCD LLM | 初始 prompt 给路径；read_file 内容作为工具结果进入消息历史 |
| TGCD → orchestrator | 函数返回 design 字典，并保存主 design 文件 |
| orchestrator → generation / repair LLM | `capability_generation_context(design=self.capability_design)` 将同一份内存 design 用 JSON 文本直接拼入 user prompt |
| generation → framework | 模型写 `driver.py`，阶段结束后框架导入产物 |

因此，“后续 LLM 一律只接收路径”是此前讨论过的接口建议，并非目前已经实现的行为。

当前阶段工具：

| 阶段 | 标准 local 路线工具 |
|---|---|
| Study | read_file、write_file、local_exec |
| TGCD | read_file、write_file |
| Generation / repair | read_file、write_file、list_skeletons、inspect_skeleton、local_exec |
| 默认 framework validation | 普通 Python 执行，不运行验证 LLM |

**5. 当前 AA1 是如何创建和验证环境的**

旧 catalog 路线可能先选择既有 `capability_mjcf`。框架把选定的真实 MJCF 以 `workspace/mjcf.xml` 符号链接暴露给工作区，以保留相对 includes 和 mesh 路径的解析。

当前标准路线：

```text
framework 导入 driver.py
  → 调用 driver.build()
  → build 调用 skeleton 的 Robot.from_mjcf(...)
  → 加载实际 XML，创建 model/data
  → 返回已绑定仿真的 Robot 控制器
  → validator 调用 cap(request)，读取同一份 model/data
```

From-scratch 路线目前通过 `Robot.build_from_mjcf(path)` 构建。

“driver 创建环境”的准确含义是：framework 调用 driver 暴露的初始化入口，入口调用模型加载代码。driver 仍是被使用的控制程序，并不负责安排整个生成与验证流程。

旧 capability suite 只构建一次 Robot，然后对不同 case 复用同一个 Robot/model/data，调用 `mj_resetDataKeyframe` 或 `mj_resetData`，再按名字覆盖初态。它不是每 case fresh 新建模型，也不显式重建 driver 的 Python 缓存。

旧验证代码会采集物理状态、轨迹及视频，driver 返回值只作为记录；通过与否由固定评分代码决定。部分实现目前位于 `AA1/autoadapter_bench/capability_eval.py`，由 orchestrator 调用，不能把目录名等同于运行职责。

**6. 讨论中的扩展：同一准备阶段设计场景与 case**

我们建议沿用一个准备阶段 ReactLoop，按顺序完成：

```text
读 study 和任务库
  → cap / criteria 草案
  → 根据效果写场景与具体 case
  → 调用框架试装配工具
  → 根据实际错误或观测修改草案
  → 保存本次设计及场景/case 描述
```

准备 agent 决定需要什么物体、放在哪里、形状尺寸、固定或自由、质量和摩擦、机器人与物体初态、request、执行时间及观测绑定。框架 Python 根据描述执行装配。

建议首版只额外暴露一个 `probe_case(scene_cases_path, case_id)` 工具，内部调用与验证相同的场景构造函数，返回编译错误、实体绑定、初态接触和短时运行观测。`local_exec` 可以作为额外诊断工具，但不能代替一套前后共用的场景构造函数。

准备阶段调用 probe 是一次工具交互；driver 提交后的正式 validation 是 framework 自己调用 Python。二者可以复用构造代码，但不复用运行中的仿真状态。

准备阶段的主要产物建议为：

```text
capability_design.json   # 本次 cap 和 criteria
scene_cases.yaml         # 场景、初态、request、观测绑定
probe_report.json        # 框架试装配和探测记录
```

场景可从原始机器人 MJCF 和 YAML 在内存装配。导出的完整 `scene.xml` 可供检查或重放；不能把 XML 与 YAML 都变成独立手改的主描述。若选择以完整场景 XML 作为执行入口，必须处理并验证 includes、meshes 等资源路径。

建议 YAML 最少包含以下内容。此处是格式示意，尚未实现；省略的数值与字段必须在实际 case 中补齐：

```yaml
scenes:
  contact_scene:
    objects:
      - name: contact_block
        shape: box
        motion: free
        # half_size_m、pos_m、quat_wxyz、mass_kg、friction

cases:
  example_case:
    scene: contact_scene
    capability_id: apply_contact_displacement
    criterion_index: 0
    # initial_state: 机器人初态、物体初态、控制初值
    # request: 满足本次 request_schema 的具体调用参数
    # observation: 需要观测的 site/body/geom 与指标绑定
```

当前 design 的 criterion 没有独立 `criterion_id`，首版可用 capability_id 加 criteria 索引引用。阈值继续从主 design 读取，不在 YAML 再复制一套。

场景实体名属于 case/观测绑定；保持 cap request 的任务中立接口，不把具体测试物体名字硬塞进通用控制接口。

**7. driver 提交后，framework 创建 fresh 环境**

拟新增的验证入口：

```python
validate_prepared_driver(
    driver_path=workspace / "driver.py",
    robot_mjcf_path=cfg.mjcf_path,
    scene_cases_path=scene_cases_path,
    design=capability_design,
)
```

每个 case 的执行顺序：

```text
MjSpec.from_file(实际机器人 MJCF)
  → 根据 YAML 添加 body / geom / freejoint 等
  → compile 得到新的 MjModel
  → 新建 MjData
  → 恢复机器人 keyframe 或指定初态
  → 按当前关节名称地址设置新增物体位姿和速度、需要覆盖的控制初值
  → mj_forward
  → 创建并绑定新的 Robot 控制器
  → 必要的明确 settle；记录实际动作起点
  → 调用本次 cap(request)
  → 在同一份 model/data 上采样、计算指标
```

首版建议要求 generation 和 repair 统一支持：

```python
class Robot(ChosenSkeleton):
    @classmethod
    def from_model(cls, model, data):
        return cls(model, data, spec=_build_spec())
```

framework 已创建环境时，不能再调用一个会重新加载 XML 的 `from_mjcf()`。标准与 from-scratch 两条路线都需要明确这一新接口。当前 Piper 可用 `Robot(model, data, _build_spec())` 验证这一机制，但 `_build_spec` 是生成文件的私有实现细节，不能直接当成通用框架接口。

每 case、每次修复重测和独立 reference 调用，都创建各自的 model/data/Robot。复用描述与资源，不复用已经被控制过的对象。这里的 fresh 说明对象重建；它本身不等于已完成进程级隔离。

初始化的重要细节：

- 新增自由物体会增加 nq/nv，不能沿用机器人原始 qpos 数组长度；按名称查 jnt_qposadr / jnt_dofadr。
- 必须先恢复机器人 keyframe，再设置自由物体位姿和速度。实际 Piper 诊断中，原 home keyframe 把新增物体位置重置到了原点。
- 不能用 driver.home() 替代 reset；home 是一次真实控制调用，会改变 case 的起点。
- 当一次 cap 调用已经在推进仿真时，观测代码应采集该次推进的数据，不额外运行另一套仿真来评分。

**8. 场景能运行，还不等于 criteria 能被验证**

自由文字标准必须有明确的可执行测量绑定。当前旧评分器只接受固定 A1–A5/G1–G5 等既有合约，且存在固定阈值，不能给动态 cap 换名字后直接复用。

| 效果 | 至少需要的测量 |
|---|---|
| 末端到目标位置 | 绑定实际末端 site/body 与 request 目标，计算位置误差 |
| 指定腕部角度或姿态 | 测量约定的关节角或末端姿态误差，不能只测末端位置 |
| 经过中间点再到终点 | 轨迹中的经过误差和时间先后关系，不能只测最终位置 |
| 接触位移 | 接触实体与物理观测；按本次效果约定测接触和位移，不能只测末端终点 |
| 连续保持 | 在时间窗内持续采样，按约定判断持续满足 |

需要区分四种证据：

1. 模型编译成功、初态符合描述。
2. 某个目标配置存在，例如 FK/IK 给出满足约束的配置；这不证明从 case 起点存在可执行的轨迹。
3. 合适的独立 reference 从同样初态经过真实控制和 MuJoCo 推进，满足该 case。
4. criteria 确实表达任务需要的效果，阈值有依据。

独立 reference 的正向检查不能由待测 driver 的自测替代。同一准备 agent 提出标准并让一个 case 跑通，也不自动证明标准合理或整个请求范围可行。动态 criteria 目前没有接好独立 reference。

对无法装配的场景、无法解析的观测或尚无测量实现的 criterion，应报告具体缺口，不算通过。有效 case 的执行不满足 criteria 时，才形成候选 driver 的修复反馈；不能为了使某个 driver 通过而在修复循环内自动放宽标准或改简单场景。

**9. Validation 如何把信息交回 generation**

AA1 默认 `validate_mode="framework"`：验证由普通 Python 执行。另有旧 agent validation 备选模式，不能把默认行为概括成所有模式都不调用 LLM。

当前已有的后半段：

```text
framework 写 validate_report.json
  → _summarise_validate_failures()
  → validate_failure_feedback(report)
  → _phase_repair(feedback, attempt)
  → 构造新的 user_msg
  → 新 ReactLoop.run(user_msg)
```

`validate_failure_feedback()`提取失败 check 的标识、detail/error/errors、`metrics.measurements`。完整 request、reset、内部评分参数和轨迹不会被这个默认摘要函数自动复制到 prompt。

因此，修复输入是“已有 driver + 公开设计与接口信息 + framework 的失败反馈”。完整验证 case 和原始报告不应因为加了场景设计就默认全部输入 generation。若需要公开环境信息，应明确给出必要的物理接口说明；测试初态、具体 request 和 reference 等按现有反馈边界留在验证侧。

这描述的是当前消息构造规则，不代表现有 read_file/local_exec 已提供完整文件隔离。

Repair 是 generation 的修复模式，复用模型配置、生成 system prompt 和工具。每次修复新建 ReactLoop，并非延续首次 generation 的完整消息历史；它通过已有 driver 和失败反馈接续工作。不要把它画成另配的一套专门 repair agent。

**10. 最小接入改动与未完成部分**

| 文件或位置 | 建议增加的内容 |
|---|---|
| capability_preparation.py | 同一准备 agent 输出 scene_cases.yaml；扩大必要的 read/write 范围；挂入 probe_case；检查本轮场景/设计引用是否一致 |
| 新 scene_runtime.py | 直接实现 YAML → 实际 MJCF 装配 → fresh model/data → 初态；准备探测与验证共用 |
| generation/repair 的提示与接口要求 | 标准、from-scratch 均要求 Robot.from_model(model,data)；保留 cap(request) ABI |
| 新的动态验证入口 | 接受本次 design、scene/case 文件与实际 MJCF；每 case 新建对象；执行支持的测量方法并记录结果 |
| 两个 orchestrator | 保存本次场景路径；在 driver 提交后调用动态验证；复用已有报告→反馈→修复流程 |

默认 catalog 路线保持原有行为。动态设计不转入旧 suite。先实现所选诊断 cap 实际需要的形状和测量代码，不建设通用场景语言或大型 schema 框架。

仍需确定或实施：首版实际支持哪些效果/指标、具体可执行 case 参数、哪些 independent reference 可用，以及动态 validation 的预算和停止位置。任务库、cap/criteria 生成和场景构造属于 AA1 主线设计范围；正式实验的 cohort、manifest、protocol、结果表和统计口径尚未确认，不能从本轮诊断推断正式实验已经设计完成。

**11. 已有证据与可查看记录**

- 最新 Piper 记录复用了一次已完成的真实 study（保留 7 轮历史 study 消息）；随后真实 TGCD 3 轮，generation 23 轮。产生 5 个 cap，task_support 引用了全部 20 条任务，方法存在且接受 request=，生成 driver 能在实际模型上构建。未运行独立物理 criteria suite。
- 36 项聚焦框架检查已经通过，记录在 `records/source/focused-checks.json`；本次整理没有重跑这些测试。
- 原始每轮输入、system prompt、工具列表、模型请求、响应、工具结果见 [record-index.md](record-index.md)。不能用简化 trace 代替原始完整 messages。
- 已观察到的设计缺口见 [parent-interface-review.json](records/source/parent-interface-review.json)：腕部、接触和中间点条件仅测终点位置，尚不足以验证相应效果。
- 后续临时 MuJoCo 构造诊断：在实际 Piper 上添加桌面和自由方块，nq 8→15、nu 仍为 7；控制器与 framework 持有相同 model/data；两个 fresh 实例在其中一个推进 100 步后，时间分别为 0.2 秒和 0 秒。该诊断的物体位于机械臂工作区之外，只验证装配、初态与共享/独立对象关系，不验证任何 cap 的可达性。

临时构造代码和记录：

- [construction_example.py](/tmp/aa1-scene-injection-GDZtNy/construction_example.py)
- [scene_cases.yaml](/tmp/aa1-scene-injection-GDZtNy/scene_cases.yaml)
- [result.json](/tmp/aa1-scene-injection-GDZtNy/result.json)

本次整理只新增本文，没有修改 AA1 运行代码、重新调用模型、创建正式实验或改变已有证据的性质。
