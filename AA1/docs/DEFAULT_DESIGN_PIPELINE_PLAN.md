# AA1 自动设计默认流程：修改计划

状态：用户已批准实施，改动统一在 `main` 整合。固定 DEMO 场景迁移和配置已提交为 `daa0b8fe`，MCP EXPORT 运行时已提交为 `c5dce2fb`。默认入口和旧固定能力路径清理正在验收；本文件保留已批准的边界，不将计划描述当成运行证据。

## 目标与保留的职责

AA1 的默认流程统一为：

```text
orchestrator.run()
  STUDY：读取调用者提供的实际 MJCF
  DESIGN：生成能力、criteria 和 scene_cases，或加载已有设计
  GENERATE：实现本次 design 中的 Robot 方法
  VALIDATE：每个 case 使用 fresh 仿真、采样并计算指标
  失败且可修复：将本次结果交回 GENERATE，重新验证
  EXPORT：导出本次方法的 MCP 调用接口
```

orchestrator 继续负责阶段顺序、配置、模型调用预算、修复循环、错误传播和结果记录。`capability_design.py`、`scene_runtime.py`、`design_validation.py`、`design_measurements.py` 是被它调用的 Python 模块，不是另一套外部流水线。

标准生成与 from-scratch 两个入口保留，区别是是否使用 skeleton。两者使用相同的阶段顺序和 DESIGN 产物；本批不为了统一入口合并两种生成器的大量代码。仍以当前 local 执行为范围，不同时扩建 DGX 执行。

## 第一批：统一默认入口

涉及 `auto_adapter/orchestrator.py`、`orchestrator_from_scratch.py` 及直接调用这些入口的运行示例。

1. `run()` 默认执行目前的自动设计流程，纳入已完成的 EXPORT。保留 `stop_after`，可停在 study/design/generate/validate/export。
2. 取消用 `prepare_capabilities` / `_dynamic_capabilities` 选择“新旧两套流程”。`prepare_capabilities` 这个开关不再有必要；在实际改动前用引用搜索列出并同步修改当前运行入口和测试，不留下仍需要该开关的示例。
3. 继续保留 `capability_design_path`、`scene_cases_path`、`max_iters_capability_design=30`。没有现成设计时调用 DESIGN agent；提供设计时在同一个 DESIGN 阶段加载。进入 validation 必须有对应 scene_cases；只有能力设计时可以停在 generate。
4. STUDY 始终使用调用者传入的 MJCF，不因旧 catalog 的 `capability_mjcf` 自动替换模型。
5. generation 与 repair 都使用 `self.capability_design`。不能在某个调用点遗漏 design 后又加载旧 capability profile。

这一步改变默认行为，需要明确更新使用说明；不能把它描述为仅重命名或完全兼容旧配置。

## 第二批：删除 AA1 主入口中的旧路径

“删除旧路径”指移除以下可选执行入口，不是删除 orchestrator 本身。

| 位置 | 拟修改 |
| --- | --- |
| 标准 `run()` 的原 PHASES 循环与新旧分流 | 收敛为一个阶段循环；复用已有自动设计、repair 和错误处理逻辑 |
| `_phase_validate()` 中 `validate_mode` 的旧固定检查 / agent 编写 validate.py 分支 | 主流程统一调用 `validate_design_driver()` |
| `_phase_validate_framework()`、`_validate_arm()`、`_validate_quadruped()`、`_validate_new_morphology()` | 核对所有引用；从主流程移除旧固定能力验证路径，删除不再被调用的专用 helper |
| from-scratch `_validate_from_scratch_driver()` 中旧验证分支 | 保留按本次 design/cases 验证的实现，去除按旧类别/固定 suite 验证的分支 |
| 主流程读取旧 capability profile / suite 的调用 | 移除自动回退；DESIGN 失败或材料不完整时停止，不套用旧标准继续运行 |
| 仅供被删验证入口使用的 prompt、工具配置和参数 | 确认无其他调用后删除；不按变量名批量删除 |

`robot_catalog.py` 仍负责机器人身份、模型和 skeleton 绑定。用户随后批准成组删除旧固定能力体系；相应旧 suite 消费者一并移除，共享的 benchmark 和录像功能保留。新的 AA1 主流程不再调用旧 suite 给本次动态设计判定通过。

旧诊断输出、论文材料、模型资源、用户已有删除项和未跟踪文件均不属于清理范围。不是看到“旧文件”就删除。

## 第三批：用真实 public run 验证交接

此前 Piper 证据是实际阶段调用并复用已有 STUDY，不是一口气完成 `SelfAssemble.run()`。入口统一后，需要明确补一次从 public run 开始的真实诊断。

- 使用一个新的 `workspace_root`，推荐 `AA1/artifacts/<run_id>`，产物实际位于 `<workspace_root>/piper/`。
- 用实际 Piper MJCF、已人工收紧的当前任务库以及项目现有模型 transport；不替换 reference driver。
- 从 `run(stop_after="export")` 跑通 study/design/generate/validate/export，或在真实失败时保留完整失败结果和修复记录。不得为报告成功而放宽本次标准。
- 检查 study、design、criteria、case YAML、prepared scenes、driver、每 case samples/视频、export messages、MCP server、summary/narrative 都指向本次运行或明确记录的输入。
- 真实 MCP 客户端列出本次 capability 方法，并调用至少一个真实 driver 方法。MCP 转发成功、物理 criterion 通过和任务可达性是不同结论，应分别报告。

这是诊断运行，不创建新的正式实验协议或计入论文实验分母。

## 最小检查

1. 默认配置会进入 DESIGN；提供设计文件只跳过模型设计调用，不跳过加载检查。
2. STUDY/DESIGN 失败不会进入 GENERATE；本次失败不能由旧文件或旧 suite 掩盖。
3. generation/repair/validation/export 使用同一份 design；旧 `load_capability_suite` 等入口不得从新主流程被调用。
4. case 使用各自 fresh 环境；所需测量算子不存在或采样不足时明确失败。
5. 执行上述一次实际 public-run 诊断，并验证真实 MCP 调用。只运行直接涉及的已有检查，不扩建大测试框架。

## 一个 case 怎样调用测量

本节描述当前已实现行为，用于理解修改边界。Piper 的 `case_move_to_position` 在 scene_cases.yaml 中包含：

```yaml
capability_id: move_to_position
request:
  target_position: [0.45, 0.10, 0.35]
  duration_s: 3.0
measurements:
  - criterion_index: 0
    operator: site_position_error
    bindings:
      site: ee_site
      target_request_field: target_position
```

实际调用链：

```text
orchestrator._phase_validate()
  → validate_design_driver(driver_path, design, scene_cases_path, scene_paths, ...)
  → 每 case 新进程加载 driver 和准备好的 scene XML
  → reset → apply_initial_state(model, data, case)
  → recorder.install()
  → robot.<design 指定的方法>(request=case["request"])
       MuJoCo mj_step 后 capture_sample(model, data)
  → evaluate_measurements(design, case, recorder.samples)
  → 每条 measurement 的值、阈值、比较符、ok 或 error
  → case 结果 → validate_report.json → orchestrator 的修复反馈
```

`criterion_index: 0` 把 case 的测量绑定连接到该 capability 的第0条 criteria。阈值、单位、时间窗口和聚合方式读主 design；YAML 不再维护一份阈值。

`evaluate_measurements()` 的计算顺序为：定位 criterion → 选择规定时间段的 samples → 根据 operator 计算数值序列 → 聚合 → 按 comparator 与 threshold 比较。case 是否成功还包含执行无错误、状态有效和视频成功等检查，因此不只是一次数值比较。

| 当前算子 | 计算含义 |
| --- | --- |
| `site_position_error` | site 实际位置与 request 目标位置的欧氏距离 |
| `joint_position_error` | 标量关节位置与目标值的绝对差 |
| `joint_drift` | 标量关节相对动作初始采样值的绝对变化 |
| `joint_relation_error` | `abs(q - multiplier * other_q - offset)` |
| `ordered_site_targets_error` | 在严格按时间顺序选择的轨迹点中，求各目标匹配误差的最大值的最小可能值；本身不额外证明最终停留 |
| `contact_normal_force` | 指定 geom 对的真实接触法向力之和，原始力来自 `mj_contactForce()` |

聚合支持 last/max/min/mean；接触力还支持按采样时间的梯形积分，积分单位为 N·s。读取不存在的绑定、未知算子或不完整时间窗口会报错，不能作为通过。

这可以理解为固定算法的测量计算器。它不能执行任意新指标名称；超出上述算子的 criteria 需要新增实际测量实现。新补的任务库是需求输入，不能因此声称全部任务已拥有可执行 validation。
