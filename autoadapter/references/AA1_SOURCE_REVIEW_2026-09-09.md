# AA1 源码技术底稿：机制、证据边界与 Chapter 3 结构

日期：2026-09-09。用途：后续论文讨论的源码依据；这是技术审读记录，不是拟写入 thesis 的正式正文，也不是新实验设计、协议或运行结果。

## 阅读范围与版本

本次分模块全文审读覆盖 AA1 自有的 **106 个 Python 文件、22,481 行**，包括两个生成入口、ReAct 与工具层、所有 controller skeleton、下游 planner、全部 benchmark 评分分支、PPO/DP/VLA/CaP baselines、真实机器人接口、测试及辅助脚本。下方覆盖表与快照文件清单逐项匹配：没有漏项或重复计数。主审直接阅读 from-scratch 主线及辅助脚本，并复核其余模块的机制记录和跨模块调用关系；各模块的全文阅读范围在对应记录中明确列出。

另外全文读取六份 benchmark YAML、六个 shell 入口、项目依赖声明，以及控制层记录中列出的 Franka 生成 driver 和配套 MJCF/JSON。没有遍历全部历史生成 artefacts、实验结果、网格资源或第三方依赖内部实现。读取源码不等于验证其运行正确性；此次没有调用付费模型、运行仿真、训练、硬件或新实验。

阅读期间 AA1 工作树由其他活动持续更新，因此使用 2026-09-09 **16:24:43.421233 UTC** 的本地快照。复制开始与结束时 nested AA1 HEAD 均为 `ff486bf3499c14d105939ac43350b0f00baf206f`，但快照包括当时未提交的改动，不能简称为该 commit 的干净版本，也不代表之后的最新代码。快照在 `/private/tmp/aa1-thesis-source-review-20260909/source_snapshot/`；元数据和逐文件清单在同目录。下文模块记录中的源码行号均指此快照，另行读取的 supporting artefacts 明确例外。快照是临时阅读副本，长期保留的本文件记录机制与版本边界，不复制整个代码库。

## 1. 首先区分五条实际路径

| 路径 | 真正生成或执行的东西 | 关键边界 |
|---|---|---|
| `SelfAssemble` | Study 后生成 robot-specific Spec、构造代码和 `driver.py`，实例化已有 skeleton；有 Generate–Validate 外层循环 | 控制器数学主要由 skeleton 提供；不是每次从零合成。默认 framework validation 与可选 agent validation 的判定不同。 |
| `FromScratchOrchestrator` | 生成完整 `Robot`、FK/IK/控制等代码到 `driver_from_scratch.py`，失败后修复 | 这是独立 orchestrator；API 仍主要由按形态编写的提示词规定，并有具体算法提示。 |
| `TaskPlanner` | 在已有 driver 上让 ReAct 选择、组合工具调用 | 不生成 driver；局部 Python 包装器直接调用方法；不是 ReCAP 实现，也没有经过 MCP transport。 |
| canonical benchmark | 构造初始观测实例、live planner 实例和 replay 实例；回放调用并执行 YAML 指定的 grader | live 视频与评分回放来自不同执行；多个旧测量仍经过候选 getter。 |
| real hardware | LeRobot 执行与 MuJoCo shadow FK/IK；另有 one-shot driver synthesis 脚本 | 与动态 MuJoCo driver 路径不同；不构成相同实现的硬件验证。 |

所以，讨论 AA1 时不能把 skeleton、from-scratch、MCP Export、TaskPlanner、benchmark grader 和硬件 demo 合并成一个拥有全部保证的系统。用户确认的主线是以 AA1 为基础，加入 task-library-derived capabilities 与自动形成的 validation criteria；后两者必须与继承的执行机制明确衔接。

## 2. 适配的核心是具体绑定，而非笼统“识别机器人”

串联机械臂的 Spec 指定末端 site/body、关节名、actuator 名、关节限位、home、gripper 参数和 backend。运行时分别解析：

- joint name → joint ID → `jnt_qposadr`，用于位置读写；
- joint ID → `jnt_dofadr`，用于速度及 Jacobian 列；
- actuator name → `data.ctrl` 索引，负责执行；
- site/body name → world-frame pose 与对应 Jacobian。

这些索引不能混用。自由关节的 qpos 与速度维数不同；Go2 的关节和 Spec 顺序为 FL/FR/RL/RR，而模型 actuator 声明顺序为 FR/FL/RR/RL。分别建立映射，才会把同一个控制向量作用到预期的腿。ArmSpec 仅检查有限的结构条件，不自动证明 actuator transmission、位置控制单位或每个名字的语义都正确。

这也是 skeleton-assisted synthesis 的具体工作量：agent 需要产生正确的机器人绑定与参数，复用的是已有数值控制实现。From-scratch 则还要编写控制实现，但其提示词已经规定许多方法名和建议算法。两条路径的人工提供量与生成量不同，论文需要准确说清。

## 3. 串联机械臂：位置 IK 与动态执行分为两步

包内 `ArmSerialDLSSkeleton.ik` 只求 world-frame XYZ 位置，不控制姿态。每轮将候选关节向量暂时写入 qpos，运行 `mj_forward` 得到末端位置和位置 Jacobian，使用映射后的 dof 列计算：

```text
e = target - p(q)
lambda = lambda_base * max(1, 0.05 / max(norm(e), 1e-6))
dq = J.T @ solve(J @ J.T + lambda**2 * I, e)
q_next = clip(q + norm_clamped(dq), joint_lower, joint_upper)
```

阻尼按残差变化，没有 SVD、显式奇异值判据、nullspace posture objective 或碰撞规划。关键是 `finally` 恢复原先全部 qpos，再做 `mj_forward`；数值搜索本身没有执行物理步。

随后 `move_joints` 从当前 **ctrl target** 到目标关节角做线性插值，每步写 mapped `data.ctrl` 并调用 `self.step(1)`。实际位置伺服由 MJCF actuator 实现。这不是直线 Cartesian trajectory，也不是每步重新做 Cartesian IK。返回 `True` 仅表示插值循环正常结束，没有终端跟踪误差检查。

单独检查的历史生成 Franka driver 恰好说明这条边界为何重要：它在 IK 迭代中写 live qpos 和 ctrl，却不恢复；之后运动插值从已被 IK 改写的位置开始。这里的问题是把数值候选状态当成实际机器人状态。不能仅因调用过 `mj_step`，就认定整个到达过程都由 actuator 和动力学产生。

## 4. 四足、手、移动机械臂与双臂各自有什么机制

| 控制器 | 真实机制 | 不能从它直接推导的能力 |
|---|---|---|
| Quadruped | 周期 thigh/calf 关节目标 + 每步 torque PD；默认对角相位；速度参数缩放摆幅；首次正反 thigh-sign 探测后缓存方向 | 没有测量速度闭环、body-frame `cmd_vel`、接触相位估计或全身平衡规划。探测恢复 qpos/qvel/ctrl，但不恢复 time 与完整状态。 |
| LEAP | 每个真实物理步读取四个 fingertip geom 的位置，堆叠 12 维误差与 12×16 Jacobian 做 DLS，写 ctrl 后步进 | 是四个点的位置控制；不直接等于抓握、指端姿态、接触力或物体操纵。 |
| Stretch | 底座实际位移/航向反馈；依据 canonical 模型的侧向伸缩几何解析 yaw/lift/extension，先转底座再移动手臂 | 依赖特定 tendon、坐标方向与耦合滑轨；不是通用 whole-body IK。`speed` 对应 motor 输入上限。 |
| Bimanual | 两个独立 arm solver 共享同一 MjModel/MjData；所有 child steps 汇入 parent；home 同步给两臂 ctrl | 共享世界与同步关节插值不等于耦合双臂 IK、互相避碰或合作力控制。构造时会直接初始化 home qpos。 |

正文应择取一条主例解释抽象如何落到模拟器；这些家族差异可以支撑实验范围和附录，不必每个 skeleton 单设一个 framework 小节。

## 5. 同名 gripper API 的物理假设不同

`gripper_close` 的返回语义由 backend 决定。Weld backend 根据 EE 与物体 body origin 的距离选择候选，计算当前相对变换，更新 equality 参数并激活约束；Contact backend 不建立 weld，但其 holding 判断只看关闭后的距离；RealContact backend 实际按 `abs(contact.dist)+1.0` 累积接触分数，关闭后采样一次，之后返回缓存值。它没有实现文档声称的持续接触力验证或实时滑落检测。NoOp 则不执行抓取。

SO101 配套场景的手臂和手指碰撞被关闭，物体搬运依靠预置 weld。这样的 lift 可以展示约束辅助的操作组合，不能据此宣称真实摩擦抓取。报告这些差异的目的，是确定同一个 capability 的成功需要观察什么，而不是由 backend 名称或方法 Boolean 代替验证标准。

## 6. 两种反馈循环及其状态如何延续

内层 ReAct 把模型输出的 tool calls 顺序执行，将 observation 加入下一轮消息。每个 phase/repair 创建新的 ReactLoop 和新对话；工作目录以及一个远端 CodeInterpreter session 可以跨阶段保留。远端 Python 状态、本地 subprocess 的状态和本地 MuJoCo 实例不是同一个运行上下文。

外层则是 Generate → framework Validate → 失败诊断 → Generate/Repair。默认 skeleton 路径总共三次 Generate–Validate 尝试；from-scratch 默认初次生成后允许两次 Repair。这提供了基于执行反馈的修正机制，但不会自动保证每次 edit 都被运行、每个 capability 都在独立 reset 下测试，或 candidate 不能访问 checker。

两个入口的记录也不同：SelfAssemble 的重复 Generate 覆写同名 trace，汇总只保留最终尝试的 phase tokens/time；from-scratch 使用编号 repair traces，并累加各次 tokens。开发反馈、最终接受和资源统计应分别解释，不能由一个 `ok` 字段统一代表。

## 7. “通过”在实际代码中有多个层次

| 层次 | 源码判定的例子 | 能支持的结论 |
|---|---|---|
| 文件产出 | Study/Generate 的预期文件存在 | 有文件；不证明来自当前成功尝试或内容正确。 |
| 工具执行 | 方法未抛异常，即使返回 False，live call log 仍可记 ok=True | 调用正常返回；不证明行为达到。 |
| 规划结束 | ReactLoop end_turn，或特定无异常的迭代耗尽情形 | 控制器停止规划；不等于完成任务。 |
| capability smoke | skeleton arm 一次位置误差 <2 cm；home/gripper 主要看无异常；quad walk 无异常即通过 | 特定 case 的实际 predicate，强度并不统一。 |
| 任务 grader | 回放后的物体/末端最终位置、调用快照、部分 tool-name 或 summary 文本规则 | YAML 类型对应的测量；不是所有 `physics_ok` 都是纯物理标准。 |

默认 framework validator 的代码由框架提供，比 agent 自己写 report 更明确；但候选在同一进程构造并持有 model/data，多个测试共用一实例，很多读数来自 getter。可选 agent validation 更弱，只要求 report 文件存在。快照中新增的 `PhysicsTrace` 能按指定 body/site/geom 直接采样真实 MuJoCo 对象，但 canonical evaluator 尚未传入必要参数，也未统一使用这些 samples。

因此，当前 thesis 所要求的独立测量、固定判据、按 case 新建状态、禁止用直接改写 evaluated qpos 代替运动，属于需要明确落实和说明的设计边界。不能把它们追溯认定为 AA1 所有已有路径已经提供的保证。此审读不判定当前父仓库的新 Harness 实现是否满足这些要求。

## 8. 用户确认的新主线究竟改了哪里

AA1 当前的 capability 方法列表、planner 参数 schema 和 behaviour grader 主要是分别手写后靠方法名连接。未知方法 fallback 只提供空参数 schema 并执行 `method()`，无法一般性承接新生成的带参数 capability。FastMCP Export 又是单独的生成包装器路径，现有 TaskPlanner 不消费该服务器。

所以 task-library extension 的方法解释需要闭合以下关系；这是对已经确认的主线的技术展开，不是额外的实验或协议提案：

1. **需求 → contract。** 哪些不同任务共享一种物理操作；它的请求参数、单位、坐标系、适用范围和行为要求是什么。capability 不包含某一个任务的完整动作顺序。
2. **contract → implementation 与 tool schema。** generator 实现前一步要求的方法；planner 使用同一参数含义构造调用。比如 JSON 的 x/y/z 如何成为 driver 的 world-frame 三维向量，不能靠两份独立清单恰好一致。
3. **行为要求 → executable case 与 measurement。** criterion 描述要达到的关系；compiler 确定初始化、请求、独立观测对象、测量时刻及 decision rule。这区别于让实现者自行设计容易通过的测试。
4. **verdict → repair 与可调用能力。** 失败回馈应定位实现与要求之间的偏差；判据不能随着修复被放宽。capability 是否可供任务组合使用与整个 downstream task 是否完成，是两个不同判断。

以 reaching 为例，同一目标 p* 应同时进入可调用参数和 evaluator 的误差计算。若要求包含保持时间，则需要在仿真时间轴上连续观测，不是只看方法返回时的位置。task-level placement 还可能要求 release；不能由 reaching 的位置通过顺带推导。这些内容属于研究方法的实质，不是再重述四/五个 phase。

## 9. 对 Chapter 3 的结构判断

**建议保留三个小节：Framework overview；Task-Grounded Capability and Validation Design；Driver Synthesis and Validation。** Overview 已经承担流程导航；第二节解释如何形成值得实现的能力与标准；第三节负责把 contract 连接到执行、独立测量和修复。现有 Driver Synthesis 与 Independent Assessment 两节可围绕这条链合并，减少重复。

第三节只需三至四个紧密连接的段落：说明生成量与 skeleton 提供量；用一个具体动作解释 name/address binding、数值求解与 actuator stepping；明确谁测量什么、据何判定、失败如何进入下一次修复；最后交代经验证的接口如何供下游组合调用。Export、工具调用示例和总体反馈箭头无需再次展开。

如果第二节已经完整包含上述实现与测量连接，两节结构也可以成立；但目前它主要停留在 design/compile/positive control，读者尚不能由此理解生成 contract 如何实际控制机器人并接受检验。因此，此时在第二节后直接结束 framework 会缺少技术闭合。优秀 thesis 的标准是使这些关键决策和因果关系可审查，而不是增加标题数量。

所有以下详细记录保留源码级函数、参数、返回与失败语义，供后续精确写作取用。它们不是建议全部塞入正文。学习 baseline 的训练细节、云工具、成本与绘图脚本通常放在对应实验或实现材料；硬件与历史图像生成不能自动进入当前主线的证据范围。此次未修改 thesis 正文或 AA1 源码。
## 完整阅读覆盖表

计数含注释及空文件，不含 supporting artefacts。每个快照文件恰由一个模块覆盖；主审的交叉阅读未重复计入。

| 模块 | Python 文件 | 行数 |
|---|---:|---:|
| From-scratch 与辅助脚本 | 22 | 3,209 |
| Generation、ReAct、工具与相关检查 | 22 | 4,136 |
| Control、hardware 与相关检查 | 17 | 4,478 |
| Planner、evaluation、CaP 与相关检查 | 17 | 6,038 |
| 其他 baselines 与绘图 | 28 | 4,620 |
| 合计 | 106 | 22,481 |

| AA1 相对路径 | 行数 | 全文阅读模块 |
|---|---:|---|
| `auto_adapter/__init__.py` | 21 | generation |
| `auto_adapter/agent/__init__.py` | 13 | generation |
| `auto_adapter/agent/converse_client.py` | 156 | generation |
| `auto_adapter/agent/react_loop.py` | 350 | generation |
| `auto_adapter/agent/task_planner.py` | 888 | evaluation |
| `auto_adapter/agent/tools.py` | 664 | generation |
| `auto_adapter/agent/vision_tool.py` | 207 | generation |
| `auto_adapter/orchestrator.py` | 1247 | generation |
| `auto_adapter/orchestrator_from_scratch.py` | 1204 | root |
| `auto_adapter/robot_catalog.py` | 26 | generation |
| `auto_adapter/skeletons/__init__.py` | 36 | control |
| `auto_adapter/skeletons/arm_serial_dls.py` | 421 | control |
| `auto_adapter/skeletons/base.py` | 266 | control |
| `auto_adapter/skeletons/bimanual_serial_dls.py` | 255 | control |
| `auto_adapter/skeletons/grasp_backends.py` | 542 | control |
| `auto_adapter/skeletons/hand_fingertip_dls.py` | 402 | control |
| `auto_adapter/skeletons/quadruped_pd_gait.py` | 380 | control |
| `auto_adapter/skeletons/stretch_mobile_manipulation.py` | 461 | control |
| `auto_adapter/tests/__init__.py` | 0 | evaluation |
| `auto_adapter/tests/benchmark_baseline_ablation.py` | 322 | evaluation |
| `auto_adapter/tests/benchmark_contact_rich.py` | 331 | evaluation |
| `auto_adapter/tests/benchmark_from_scratch_tasks.py` | 199 | root |
| `auto_adapter/tests/benchmark_generalization.py` | 433 | evaluation |
| `auto_adapter/tests/benchmark_hard_tasks.py` | 281 | evaluation |
| `auto_adapter/tests/benchmark_onboarding_8_robots.py` | 204 | root |
| `auto_adapter/tests/test_added_assets.py` | 40 | generation |
| `auto_adapter/tests/test_arm_so101_smoke.py` | 112 | control |
| `auto_adapter/tests/test_bedrock_sdk_compat.py` | 19 | generation |
| `auto_adapter/tests/test_bimanual_serial_dls.py` | 152 | control |
| `auto_adapter/tests/test_execute_python_smoke.py` | 85 | generation |
| `auto_adapter/tests/test_export_demo.py` | 145 | generation |
| `auto_adapter/tests/test_framework_verdict.py` | 43 | generation |
| `auto_adapter/tests/test_from_scratch_franka.py` | 124 | root |
| `auto_adapter/tests/test_from_scratch_go2.py` | 113 | root |
| `auto_adapter/tests/test_from_scratch_piper_pickbench.py` | 64 | root |
| `auto_adapter/tests/test_from_scratch_so101.py` | 96 | root |
| `auto_adapter/tests/test_full_pipeline_franka.py` | 105 | generation |
| `auto_adapter/tests/test_full_pipeline_go2.py` | 113 | generation |
| `auto_adapter/tests/test_full_pipeline_piper.py` | 114 | generation |
| `auto_adapter/tests/test_full_pipeline_so101.py` | 153 | generation |
| `auto_adapter/tests/test_hand_fingertip_dls.py` | 172 | control |
| `auto_adapter/tests/test_orchestrator_so101_partial.py` | 150 | generation |
| `auto_adapter/tests/test_quadruped_go2_smoke.py` | 109 | control |
| `auto_adapter/tests/test_react_loop_smoke.py` | 117 | generation |
| `auto_adapter/tests/test_real_contact_grasp.py` | 185 | control |
| `auto_adapter/tests/test_stretch_mobile_manipulation.py` | 64 | control |
| `auto_adapter/tests/test_task_planner_complex.py` | 165 | evaluation |
| `auto_adapter/tests/test_task_planner_showcase.py` | 148 | evaluation |
| `auto_adapter/tests/test_tools_smoke.py` | 100 | generation |
| `auto_adapter/tests/test_validate_only.py` | 114 | generation |
| `auto_adapter/tests/test_vision_perception.py` | 154 | generation |
| `autoadapter_bench/__init__.py` | 0 | evaluation |
| `autoadapter_bench/baselines/__init__.py` | 0 | baselines |
| `autoadapter_bench/baselines/code_as_policies/__init__.py` | 4 | evaluation |
| `autoadapter_bench/baselines/code_as_policies/runner.py` | 637 | evaluation |
| `autoadapter_bench/baselines/dp/collect_demos.py` | 107 | baselines |
| `autoadapter_bench/baselines/dp/collect_demos_multi.py` | 72 | baselines |
| `autoadapter_bench/baselines/dp/collect_pick_demos.py` | 146 | baselines |
| `autoadapter_bench/baselines/dp/eval_dp.py` | 153 | baselines |
| `autoadapter_bench/baselines/dp/train_dp.py` | 226 | baselines |
| `autoadapter_bench/baselines/eval_cross_skill.py` | 150 | baselines |
| `autoadapter_bench/baselines/eval_dp_cross_skill.py` | 150 | baselines |
| `autoadapter_bench/baselines/eval_franka_reach_n10.py` | 232 | baselines |
| `autoadapter_bench/baselines/eval_multi.py` | 191 | baselines |
| `autoadapter_bench/baselines/eval_piper_pick.py` | 386 | baselines |
| `autoadapter_bench/baselines/eval_piper_pick_v2.py` | 241 | baselines |
| `autoadapter_bench/baselines/render_filmstrips.py` | 436 | baselines |
| `autoadapter_bench/baselines/render_paper_figures.py` | 96 | baselines |
| `autoadapter_bench/baselines/rl/__init__.py` | 0 | baselines |
| `autoadapter_bench/baselines/rl/cartesian_env.py` | 297 | baselines |
| `autoadapter_bench/baselines/rl/eval_ppo.py` | 110 | baselines |
| `autoadapter_bench/baselines/rl/pick_env.py` | 181 | baselines |
| `autoadapter_bench/baselines/rl/rl_env.py` | 187 | baselines |
| `autoadapter_bench/baselines/rl/skel_adapter.py` | 199 | baselines |
| `autoadapter_bench/baselines/rl/train_eval_cartesian.py` | 210 | baselines |
| `autoadapter_bench/baselines/rl/train_pick.py` | 100 | baselines |
| `autoadapter_bench/baselines/rl/train_ppo.py` | 117 | baselines |
| `autoadapter_bench/baselines/rl/train_ppo_multi.py` | 114 | baselines |
| `autoadapter_bench/baselines/vla/__init__.py` | 0 | baselines |
| `autoadapter_bench/baselines/vla/diag_openvla_harness.py` | 112 | baselines |
| `autoadapter_bench/baselines/vla/eval_openvla.py` | 250 | baselines |
| `autoadapter_bench/baselines/vla/eval_openvla_sweep.py` | 157 | baselines |
| `autoadapter_bench/eval.py` | 1734 | evaluation |
| `autoadapter_bench/eval_baseline.py` | 400 | evaluation |
| `autoadapter_bench/leaderboard.py` | 236 | evaluation |
| `autoadapter_bench/physics.py` | 95 | evaluation |
| `autoadapter_bench/run_multi_model.py` | 150 | evaluation |
| `autoadapter_bench/stats.py` | 214 | evaluation |
| `experiments/rebuttal_franka_ik/run.py` | 181 | control |
| `paper/figures_wip/plot_synth_trajectory.py` | 70 | root |
| `real_robot/real_so101.py` | 411 | control |
| `real_robot/synth_real_driver.py` | 329 | control |
| `scripts/aws_bedrock_probe.py` | 86 | root |
| `scripts/aws_capability_probe.py` | 89 | root |
| `scripts/build_supplementary.py` | 107 | root |
| `scripts/fix_string_bool_aggregates.py` | 61 | root |
| `scripts/replay_traces_to_video.py` | 257 | root |
| `scripts/run/feedback_ablation.py` | 56 | root |
| `scripts/run/preflight_regrade_all.py` | 151 | root |
| `scripts/run/synth_drone_aerial_v2.py` | 36 | root |
| `scripts/run/synth_humanoid_h1_v4.py` | 36 | root |
| `scripts/run/synth_menagerie_so101.py` | 37 | root |
| `scripts/run/synth_quad_v2.py` | 56 | root |
| `scripts/run/synth_xmodel_one.py` | 44 | root |
| `scripts/run/synth_xmodel_robot.py` | 59 | root |
| `scripts/upload_hf.py` | 60 | root |

另行全文读取的 supporting files（不计入上述 106）：

- `artifacts/auto_adapter_from_scratch_franka_artifacts/driver.py`
- `artifacts/auto_adapter_from_scratch_franka_artifacts/study.json`
- `assets/mjcf/franka_panda/panda.xml`
- `assets/mjcf/go2/go2.xml`
- `assets/mjcf/go2/go2_scene.xml`
- `assets/mjcf/hello_robot_stretch_2/scene.xml`
- `assets/mjcf/hello_robot_stretch_2/stretch.xml`
- `assets/mjcf/leap_hand/right_hand.xml`
- `assets/mjcf/leap_hand/scene_right.xml`
- `assets/mjcf/so101_mujoco.xml`
- `autoadapter_bench/spec/robot_zoo.yaml`
- `autoadapter_bench/spec/tasks_aerial.yaml`
- `autoadapter_bench/spec/tasks_arm.yaml`
- `autoadapter_bench/spec/tasks_humanoid.yaml`
- `autoadapter_bench/spec/tasks_quadruped.yaml`
- `autoadapter_bench/spec/tasks_wheeled.yaml`
- `paper/latex/build.sh`
- `pyproject.toml`
- `real_robot/context/calibration.json`
- `scripts/run/eval_aerial.sh`
- `scripts/run/eval_quad_v2.sh`
- `autoadapter_bench/baselines/rl/myriad_cart_rl.sh`
- `autoadapter_bench/baselines/vla/myriad_openvla_canon.sh`
- `autoadapter_bench/baselines/vla/myriad_openvla_sweep.sh`

快照时工作树状态（仅用于界定所读版本，不是未来实验输入）：

```text
A  artifacts/diagnostics/aa1_15_20260909/reference/hello_robot_stretch_2/physics_trace.json
A  artifacts/diagnostics/aa1_15_20260909/reference/hello_robot_stretch_2/result.json
 M auto_adapter/agent/tools.py
 M auto_adapter/orchestrator.py
M  auto_adapter/skeletons/__init__.py
A  auto_adapter/skeletons/stretch_mobile_manipulation.py
A  auto_adapter/tests/test_stretch_mobile_manipulation.py
 M autoadapter_bench/eval.py
?? artifacts/diagnostics/aa1_15_20260909/generated_attempt_1/
?? artifacts/diagnostics/aa1_15_20260909/generated_attempt_2/
?? auto_adapter/skeletons/bimanual_serial_dls.py
?? auto_adapter/tests/test_bimanual_serial_dls.py
```

## 详细模块记录

以下为各模块的完整技术记录。各记录中的“未修改仓库”等表述描述该模块审读动作；最终由主审汇编为本文件。Source paths 均相对 AA1 或各记录明确指定的子目录。

## A. Generation and orchestration

## AA1 generation and orchestration full-source reading

### Scope and version

All 22 paths in generation_coverage.json were read fully: eight source files and fourteen tests. No models, network, hardware, tests or simulations were run. Only this memo and coverage file were written under /private/tmp. Thesis AGENTS.md and ACADEMIC_WRITING_GUIDE.md were read fully before analysis; no thesis content was edited.

Final reference is the parent's stable snapshot `/private/tmp/aa1-thesis-source-review-20260909/source_snapshot`, captured 2026-09-09 at 16:24:43Z with HEAD ff486bf stable at capture boundaries. Initial reading used the live checkout at 08daf93, which advanced externally through 7cbf572 to e56b1c6. A committed diff over all original 21 assigned files between 08daf93 and e56b1c6 was empty. The initial reads included dirty orchestrator/tools changes. Comparing to final snapshot showed those changes identical, plus a five-line expansion in react_loop.py routing Anthropic temperature through extra_body. That complete changed block and the new SDK compatibility test were read. All line references here use the final snapshot; ReactLoop references after line 224 account for the five added lines. Parent snapshot inventory/metadata is authoritative for full snapshot provenance.

The separate from-scratch orchestrator, skeleton internals, TaskPlanner, benchmark evaluator and trial artefacts are outside this memo's full-read scope. Their stronger or different mechanisms must not be inferred from this SelfAssemble route. In particular, this entrypoint has no task-library input, capability-contract synthesis or validation compiler.

### Entry point, objects and lifetime

`auto_adapter/__init__.py:9–21` re-exports SelfAssemble, its config, aggregate result and phase result. `agent/__init__.py:3–13` imports ReAct data/executor classes and separately implemented TaskPlanner. There is no CLI in these files. Reviewed integration scripts call `with SelfAssemble(cfg) as sa: sa.run()` or individual private phases.

`SelfAssembleConfig` (`orchestrator.py:50–104`) contains robot ID, MJCF source and workspace root, execution/validation mode, cloud/model binding, per-phase limits and outer attempt cap. Defaults: local, framework validation, Study16/Generate22/agentValidate25/Export8/Demo18 turns, 8000 response tokens, three total Generate+Validate attempts, 900-second CI session. They are defaults, not experiment evidence. Strings are not enum validated: mode other than local selects DGX, validate_mode other than framework selects agent validation.

`PhaseResult` (:107–121) contains phase status/timing/trace/artefact paths/final text/error/token use/metadata. `SelfAssembleResult` (:124–151) holds robot/workspace/phases/overall flag; to_json serialises paths and metadata but omits per-phase final_text. Overall flag is all PhaseResult.ok, not independently computed robot-task success.

`SelfAssemble.__init__` (:434–464) reads catalogue class, rejects conflict with explicit expected class, creates workspace/traces/recordings, replaces mjcf.xml with symlink to resolved source, and initialises CI slots. It leaves prior study/driver/report/videos in place. It does not copy MJCF meshes/includes into a self-contained bundle, validate robot ID path components or validate source existence before symlinking. `__enter__` (:468–481) creates real boto3 AgentCore client/session and Python tool. One CI session persists across phases/retries. `__exit__` (:483–491) best-effort stops it, suppressing errors. Even Export-only with-context starts unused CI. Deterministic framework Validate works without context, whereas Study/Generate assert the Python tool exists.

`_read_extra_roots` (:495–507) adds skeleton package and MJCF source directory. `_local_tools` (:509–513) is write/read only. `_skeleton_tools` (:515–516) is list/inspect. `_dgx_tools` (:518–527) binds SSH/SCP; `_local_runtime_tools` (:529–541) binds shell with repo on PYTHONPATH; `_runtime_tools` (:543–544) selects by mode. These actual bundles differ from historical tools-module header descriptions.

### What each phase asks for and actually guarantees

**Study**, prompt :159–198, runner :612–627: asks LLM to read MJCF, analyse body/joint/actuator/site/equality structure, infer morphology and write study.json with declared fields. This capability map is structural metadata, not task-derived operations or acceptance criteria. Current dirty prompt adds hand/mobile/bimanual/aerial and cautions against treating composite robots as simple arms. Study tools are file read/write and remote execute_python, with no local shell or skeleton inspector. Local files are not automatically transferred to remote CI: the model may embed text from read_file into submitted code, but local path availability remotely is not guaranteed. Schema, parsing correctness and classification are prompt instructions; runner checks only output existence.

**Generate**, prompt :201–258, runner :629–661: model selects a supplied skeleton, gets its Spec schema, derives robot-specific mapping/configuration, writes driver.py importing skeleton+Spec with build(). This is configuration/construction generation around preimplemented control, not merely seeing a skeleton outline and independently writing controller maths. It asks for end-effector site/body selection, limits/home pose, grasp backend and concrete parameters. The active gripper probe simulates both control-range endpoints for 200 steps and infers which closes from joint displacement; it is a prompt heuristic, not an automatically run framework function or universal kinematic guarantee. It asks a final import/build/describe smoke. Actual tools include file, skeleton, CI and local-or-DGX runtime. Prompt still literally requests local_exec in DGX mode, where only SSH/SCP are supplied. No starter interface-only driver is emitted by the orchestrator. Failed-validation text is appended to next user message, with instruction to read full report. No code-enforced driver-only mutation or hidden-checker boundary exists here.

**Optional agent validation**, dispatcher :685–711: local prompt :281–330 asks LLM to write/run validate.py, infer tests from generated estimated_class, record video and author report. Arm prompt uses home+[.05,0,.05] target/error<.01m and lift>.05m. Quadruped prompt uses final stand height>.20m, forward dx>.05m and sit below .7*target height. DGX prompt :261–278 asks a simpler representative smoke and parsed JSON report. The framework checks only validate_report.json existence; it never parses all_ok in this branch. A failed-test or invalid/stale report can satisfy that phase gate. Thus this path is not a candidate-independent fixed validator, and differs from default framework path below.

**Export**, prompt :333–365, runner :1047–1063: asks LLM to introspect actual skeleton methods and produce FastMCP mcp_server.py with lazy build, matching signatures, JSON returns, stdio main and import smoke. Runner only requires file existence. It does not bundle an artefact directory, copy model assets, write README, test tool invocation, or apply a per-capability pass whitelist. Bundle descriptions elsewhere must identify another route.

**Demo**, local prompt :385–418 / DGX :368–382, runner :1065–1088: LLM writes and runs representative demo.py, records root demo.mp4 and timestamped copy. Local quadruped prompt suggests skipping walking if report says it failed. Acceptance is demo.mp4 existence only: no independent task predicate, state replay, ReCAP invocation, continuous-video requirement or decoded/nonempty-video check in this method. This script-generation demo and separate TaskPlanner task execution must not be merged in thesis descriptions.

### Phase acceptance, retry control and evidence accounting

`_run_phase` (:548–608) constructs a new ReactLoop for each phase/attempt; conversations do not carry across, though files and remote CI state persist. Expected artefacts are checked with Path.exists only: no current-attempt freshness, regular-file requirement, parsing or semantic check. Existing artefacts can make phase ok despite API failure or exhausted turns. This intentionally counts output produced at final turn, but does not prove behavioural validity. With an empty artefact list it uses loop end_turn status.

`_summarise_validate_failures` (:663–683) reads mutable report; missing/unreadable report gives diagnostic fallback. Every falsy per-test ok becomes name/detail, even though strings say structural failure. Top-level error or generic fallback is returned if no failed tests listed. No threshold/request sanitisation or immutable diagnostic channel is implemented.

`run` (:1094–1200) executes Study, then up to max(1,int(max_outer_gen_val_iters)) Generate+Validate attempts, then Export/Demo if validation passes. Default3 means initial+two repairs, not three retries. Generate failure exits before further validation; validation failure regenerates with text; pass exits. Only final Generate and Validate PhaseResults are added (:1138–1144), omitting earlier durations/tokens. Repeated Generate uses same 02_generate.jsonl and each new ReactLoop truncates it; agent Validate does likewise. Shared CI remains alive, but prior model conversation is not replayed. After all cap attempts fail, outer_iter equals cap and metadata/error say outer_iter+1, overreporting one attempt; when success occurs this count is correct.

stop_after='generate' actually includes Validate because validation runs inside Generate branch, then adds a padded bare validate result. On successful validation, subsequent validate branch continues before stop gate, so stop_after='validate' does not stop before Export/Demo. Failed validation with skipping may produce duplicate validate entries. Padded phases are false, so partial-run overall ok is false. These are static control-flow findings, not reproduced tests (:1113–1188).

`_write_narrative` (:1202–1247) emits stored per-phase timing/tokens/errors, trace line count, 240-character closing text and existing MP4 list sorted by mtime. It does not parse simulator evidence or verify every attempted simulation is represented; reused workspace may include old videos, overwritten traces omit attempts. Summary JSON/narrative writing errors propagate. The narrative heading claiming every sim attempt is broader than enforced recording.

### Default framework validation: ownership, dispatch, every predicate

`_phase_validate_framework` (:713–840) runs locally inside orchestrator process irrespective of mode. It adds workspace sys.path, pops cached driver key, changes cwd, side-loads driver.py and calls generated build(). No evaluator-created MjModel/MjData, isolated child or behaviour timeout is present. One returned skeleton is used sequentially for all tests. It restores cwd/sys.path finally, but not arbitrary globals/import side effects and does not close skeleton.

Missing driver writes report/error. Import/build exceptions record driver_build false and return. Normal build records returned type name. It compares type(skel).__name__ with catalogue expected skeleton string and only dispatches exact ArmSerialDLSSkeleton/QuadrupedPDGaitSkeleton; expected mismatch and unsupported class fail (:790–808). This is a string comparison, not class-identity check. Dirty hand/Stretch/bimanual discovery does not add validator branches. Behaviour-dispatch errors record behavior_smoke false. structural_ok covers selected build/dispatch entries; all_ok covers all tests and is actual phase acceptance (:814–839). Thus failed measured tests now trigger repair even if build succeeds, but individual predicates remain limited.

`_validate_arm` (:844–968):

- home_settle calls home then skeleton get_ee_pose and passes any normal return; metric=wall-clock seconds, no settling tolerance and bool ignored. Exception fails and returns early, omitting subsequent tests (:866–881).
- ik_roundtrip reads current EE via getter, target=current+[.03,0,.03]m, invokes 1.5s move, accepts getter-based Euclidean error<.02m. Return bool ignored; exceptions fail (:883–900).
- gripper_cycle passes open/close/open if no exception when generated Spec lists actuators; no observed jaw displacement. No listed gripper is a passing skipped test (:902–921).
- grasp_lift exists only if generated spec.weld_graspable_bodies nonempty and uses first object. Read initial position from getter; approach +.05m, descend +.02m, close, lift to original+.12m, reread object. Pass lift>.05m and EE gap<.12m. An EE getter exception sets gap=0, so missing gap evidence can pass that part. Empty candidate list omits test (:923–957). Object membership is candidate-controlled, unlike expected morphology.

`_validate_quadruped` (:970–1045): stand_up(2s), accept getter height>.15m, log bool but don't use it; sit(1.5s), accept height<.7*generated spec.body_height_target; stand_up(1.5s), walk_forward(1s,.15), measure dx through getter but pass any exception-free walk, including zero/backward movement (:988–1035). No uprightness predicate. Threshold and observations thus are not fully candidate-independent.

Both monkeypatch skel.step, run original one step at a time and render at most 600 frames. Arm condition (len(frames)*10)%10==0 is always true and capture_every=10 unused (:853–864); quadruped also captures each wrapped step. Later movements after cap contribute no frames; code bypassing skel.step bypasses hook. Finally restores step, saves one combined MP4 named to second precision, attaches path only to last test. Render/save errors suppressed; video absence cannot fail. These are not guaranteed fresh-per-case simulations or continuous per-case videos.

### Catalogue

`robot_catalog.py:8–15` maps five morphology strings to skeleton names. find_robot_definition (:18–26) reparses zoo YAML on every call, returning first entry matching ID OR resolved MJCF path. Missing/malformed YAML propagates. Earlier path match can precede a later ID match; no separate consistency check. Unknown yields None. SelfAssemble consumes class only, not returned observation bindings. Generated study does not determine expected class in framework validation.

### ReAct engine and compatibility client

`react_loop.py`: _vlog (:25–29) conditionally prints stderr. ToolSpec (:37–49) is name/description/schema/synchronous handler; TraceStep (:52–62) stores assistant visible text, actions, truncated observations, latency/token/stop; ReactResult (:65–73) is conversational outcome. __init__ (:99–150) selects Anthropic SDK by model substring with timeout300/retries0 or Converse otherwise; rejects duplicate tool names; sends schemas without local JSON validation; truncates trace path immediately. _dump_step (:154–158) append-writes JSON lines, default=str; no fsync or resume/replay reader.

_execute_tools (:160–198) executes tools sequentially, using sentinel attr/dict extraction so empty input is preserved. Unknown name, handler and serialisation errors become is_error text. Extraction of missing name/id/input occurs outside try and can escape. Dict/list output JSON-encodes, other output stringifies. Handler-returned error dictionaries with exit=-1 remain normal tool results unless they raise. _truncate (:200–202) limits trace observations to 2000 chars only, not next-turn model context.

_invoke_with_retry (:204–256 in snapshot) sends system/messages/tools/token cap, temperature zero except opus-4-8/4-9. Snapshot SDK fix uses extra_body={temperature:0} for Anthropic, named temperature for Converse (:224–230). Up to default6 attempts; retry classification is substring search in exception name/message for throttling/429/timeout/connection/500/503/internalserver/modelnotready. It is not full status-code-based 5xx detection. Delay=min(base**attempt*uniform[.75,1.25],max30), first around1s. Nontransient error rethrows. No automatic tool replay.

run (:260–350 snapshot) starts fresh user conversation, calls model, accumulates visible text/tool blocks/tokens. Invocation failure returns partial trace/failure. end_turn records success immediately even if a tool block accompanies it, without executing that block. Other stop reasons execute tools, append trace, and append full returned tool content. max_tokens is not special resumable code handling; malformed input may produce a diagnostic. Max iterations returns false. No mandatory physics check, successful code return or semantic answer check. duration_ms is captured before tools, so excludes execution time. Context grows without summarisation; trace truncation does not truncate model input. API-failed tokens unavailable; cache token total reported on ordinary termination.

`converse_client.py`: _Block/_Usage/_Resp (:20–47) mimic Anthropic shape; cache_read always0. _Messages init (:60–70) configures boto3 read300/connect30 and adaptive max_attempts4, layered beneath ReAct retries. create (:72–115) maps tool schemas/messages/system/temp to Converse, removes empty message contents, calls API, retains only text/toolUse outputs and maps stop reason. Content-filtered/guardrail/stop_sequence/unknown become end_turn (:50–57,:113), hence conversational success in ReAct. _to_converse_msg (:117–144) accepts strings, own _Block and dict text/tool_result, preserves tool IDs/error status, makes tool result text. No general multimodal adapter. ConverseClient (:147–151) exposes messages; is_anthropic_model (:154–156) checks substrings anthropic/claude. Vision uses separate direct SDK.

### Every tool factory and material boundary

`tools.py`:

- _resolve_under (:32–40) resolves symlinks/path and checks final containment. Absolute paths or '..' that resolve inside root can pass despite description. write_file (:43–72) makes parent dirs, overwrites text, returns path/bytes; no atomic write, semantic/file-name restriction or report protection. External symlink writes fail helper containment.
- read_file (:75–139) rejects literal '..', lexically checks absolute root or tries relative names under root list in order, deliberately follows external symlinks. Returns full text/bytes without output cap. It is not isolation against a model with arbitrary local_exec. Source roots contain skeletons and MJCF source dir.
- _list_skeletons (:147–171) returns exported classes ending Skeleton with first doc line/module and **all exported Spec names** for each, not precise matching. make_list_skeletons_tool (:174–189) takes no args.
- _inspect_spec (:192–223) enumerates dataclass field name/type/default/required; calls default factory, failure becomes None but still optional; non-basic defaults stringify. Does not expose field prose except class doc. inspect_skeleton (:226–276) checks exported class and explicit Spec mapping; valid unknown mapping returns warning/null schema. Dirty mapping adds hand/Stretch/bimanual, but classes must actually be package-exported; file presence alone insufficient.
- execute_python (:284–376) checks code key/type, not 150-line cap; sends executeCode/python to existing remote CI. Invocation failure returns stderr/exit=-1; stream failure propagates. Reads only result events. Successive nonempty stdout/stderr replace, not concatenate; absent result may leave exit_code None. Truncates each stream16000 chars. Preserves remote session state, does not autonomously load draft/fresh simulator/smoke or upload local files.
- _run_subprocess (:389–405) invokes argv, caps stdout16000/stderr4000; timeout returns partial output/exit=-1; other exceptions raise.
- ssh_dgx_exec (:408–455) uses BatchMode=yes, optional SSH options, quotes configured remote cwd and executes supplied command inside it. Timeout model-settable, no command confinement or remote mkdir, no physics verdict.
- local_exec (:463–560) snapshots environment at tool creation, overlays env, prepends repo/workspace PYTHONPATH and venv bin PATH, sets VIRTUAL_ENV. Uses sys.prefix/bin to avoid resolved uv symlink interpreter issue. explicit python_executable supplies bin directory, not forced exact executable per command. Handler is arbitrary shell=True with cwd, model-settable timeout default120 and capped captured streams. cwd is not filesystem containment; no enforced no-pip, driver-only writes or hidden-test access policy.
- scp_to/from (:563–640) bind host/workspaces, use resolved local containment, quote concatenated remote target. To requires source existence; From makes local parents. Remote '..' is not rejected and directories not created. The MJCF external symlink passes read_file but fails scp_to containment, conflicting with DGX prompt asking to copy it. No complete asset closure transfer.
- make_mvp_local_tools (:648–664) bundles write/read/list/inspect for convenience, unlike actual Study bundle.

### Vision module

`vision_tool.py` is optional and not added by SelfAssemble. _encode_image_base64 (:65–72) casts image uint8 and PIL encodes PNG. _parse_perception_json (:75–92) strips object code fences or leading prose, json.loads, syntax failure -> objects=[]/_parse_error; no schema/type/coordinate/confidence validation, no trailing prose recovery. Valid non-dict JSON can fail later.

make_look_at_scene_tool (:95–207) captures skeleton/cameras/image sizes/model/hint/save dir/log. Each call creates direct Anthropic client; renders specified cameras, on any TypeError falls back to plain render; saves look_at_scene_cam<ID>.png (docstring says last_view.png incorrectly). Sends image(s)+optional scene hint to model; does not attach calibrated extrinsics or depth. max_tokens2000, no explicit local timeout/retry/temp. API exception -> objects=[]/_api_error and no log entry. Parsed result gets token count; call log ok=True even parse failure. Vision positions are guesses, not trusted measurements. Camera geometry claimed by system prompt is not supplied as structured data.

### All fully read tests and actual asserted scope

No tests executed. All below except added_assets/framework_verdict/bedrock_sdk_compat are live main-guard scripts, so ordinary pytest collection does not execute main. Names/documentation can claim more than assertions.

1. test_added_assets.py (:12–40): six parameterised real MJCF closures; load/forward/eight dynamics steps; finite qpos/qvel/ctrl/time. Asset smoke, not driver/behaviour qualification.
2. test_execute_python_smoke.py (:28–85): starts CI, real model requested hash via Python, final answer compared with local hash, cleanup finally. No assertion tool actually called; no MuJoCo. Success then os._exit0.
3. test_export_demo.py (:27–145): discovers preexisting workspace by reverse lexical directory order, not newest mtime; requires driver/report presence, deletes stale export/demo. Runs Export, independently imports FastMCP, asserts tools nonempty/home+move_cartesian substring. No wrapper execution. Runs Demo, size>5KiB, no decode/task criterion; unused CI session per phase.
4. test_framework_verdict.py (:9–43): fake class driver/minimal fixture, unknown class rejected, expected mobile/bimanual cannot masquerade as arm, monkeypatched behavioural false/error yields validator false. Despite name, does not actually run generation retry loop. Focused predicate regressions, no physics.
5. test_full_pipeline_franka.py (:29–105): live full run, asserts only Study/Generate, driver build and ee_body_name/no site. No required validation/demo success; joints/backend printed, not verified.
6. test_full_pipeline_go2.py (:28–113): live full run, asserts Study/Generate and class string Quadruped; later phases informational, explicit soft pass, no gait behaviour assertion.
7. test_full_pipeline_piper.py (:32–114): asserts Study/Generate/build and backend containing Contact OR NoOp, not successful physical grasp; later phases informational.
8. test_full_pipeline_so101.py (:36–153): all five phase flags, build, |close|<|open| heuristic, report all_ok=True, video>5KiB, some recording/narrative. MCP invented names only warn; no tool execution, actual probe occurrence, decoded video or independent per-test physics audit.
9. test_orchestrator_so101_partial.py (:32–150): side-load helper; fresh workspace/run stop_after generate; asserts study arm/intDOF>=5/joints>=5/build/home/describe. Gripper inversion warning only. Current stop control includes Validate despite docstring. Keeps workspace.
10. test_react_loop_smoke.py (:27–117): add/echo logging, real model; asserts both called/answer contains5, trace keys/count. No call-order/argument correctness, crash durability or recovery testing.
11. test_tools_smoke.py (:28–100): logging wrapper; asserts list/inspect/write calls and chosen arm+ArmSpec JSON. No confinement/runtime/new morphology checks. os._exit inside try bypasses finally cleanup on success.
12. test_validate_only.py (:25–114): reuses workspace, replaces model link, removes old report, CI+default framework Validate. Prints failed grasp as expected, but does not require failure, success or cause; exits0 if any report exists.
13. test_vision_perception.py (:38–154): subclass removes get_object_position only and adds VLM. Explicit hint supplies object names/colours including red lego; camera need not move closer after arm movement despite task asking refinement. Executes four tasks with inherited TaskPlanner, copies videos/writes inherited r.ok scoreboard, no independent assertions on calls/semantic identity/position error/grasp. Other privileged channels depend on separate planner review.
14. test_bedrock_sdk_compat.py (:7–19): constructs loop/client, captures installed SDK create signature, monkeypatches call to signature.bind, checks extra_body.temperature0. No network request or robot/model run. Focused request-argument compatibility, not API acceptance by server.

### Dirty changes and conclusions bounded to this route

Final snapshot orchestrator prompts/tool map expand skeleton discovery, but no extra deterministic morphology branches or task-derived tests are introduced. The SDK fix changes request argument construction and adds a focused local signature test. Other active changes to skeletons/evaluator belong to sibling reviews; no project changes were made here.

This SelfAssemble route supports an LLM tool loop and explicit outer diagnostic repair. It does not justify statements that every edit is automatically checked by fresh simulation, that stdout is an independent measurement, that each capability is tested in its own isolated reset, or that all recordings/criteria are protected from generated code. Its basic phase success flags mean different things: artefact existence, fixed smoke predicates or end-turn. Skeleton configuration synthesis, complete from-scratch driver synthesis, MCP export, representative video demonstration and TaskPlanner evaluation are distinct research objects. Stronger independent sampling implemented in the benchmark evaluator must be cited to that exact path.

Remaining uncertainty: no result/trace was assessed, so no empirical claim about generation success, cost, convergence or historical use of these defaults. Callee skeleton dynamics/from-scratch/TaskPlanner/benchmark oracle were intentionally left to assigned sibling readers. No task-library capability or pass-criterion generator appears in this fully read source scope. All exact full-read paths are in generation_coverage.json.
## B. From-scratch and ancillary runners

## From-scratch synthesis and ancillary runners: complete source reading

Reading basis: source snapshot captured 2026-09-09 16:24:43 UTC at nested AA1 HEAD `ff486bf3499c14d105939ac43350b0f00baf206f`, including the working-tree edits listed in `snapshot_metadata.json`. This is source inspection, not a new experiment or evidence of successful execution. The reader inspected the complete 1,204-line `auto_adapter/orchestrator_from_scratch.py`, all six from-scratch benchmark/test drivers, all Python files under `scripts/`, and the synthesis trajectory plot script. The six shell entrypoints and `pyproject.toml` were also read. Other modules are covered by companion memos.

### 1. What from-scratch actually adds

`FromScratchOrchestrator` is a separate orchestrator, not a flag that removes a template from `SelfAssemble`. Its prompt, class API, file name, control library access, validation dispatch and aggregate result differ. `_GEN_ALGO_SYSTEM` supplies explicit class-dependent method lists and control guidance. Arms are told to expose `move_cartesian`, `get_ee_pose`, gripper and object-observation APIs; quadrupeds receive stand/sit/walk; wheeled bases receive drive/turn; aerial robots takeoff/move/hover/land; humanoids stand/squat and optional walk. Thus the current from-scratch route generates implementations of a largely pre-authored interface. It does not derive arbitrary capabilities from a task library.

The phrase “agent chooses the algorithm” has a precise boundary. No numerical solver implementation is injected, and skeleton imports are prohibited; however the prompt recommends DLS/Newton/pseudoinverse by arm type, PD and a diagonal gait for quadrupeds, cascaded position/attitude control for aerial platforms, and a quasi-static four-phase micro-step strategy for humanoids. The method is algorithm authoring with strong textual guidance, not unconditioned discovery. The prompt describes five-joint arms as non-redundant, although redundancy depends on the selected task dimension; its position-only task should not be assigned that label solely from total DOF. Sources: `orchestrator_from_scratch.py:157–323`.

### 2. Workspace, model access and lifetime

`__init__` creates `<workspace_root>/<robot_id>`, `traces/` and `recordings/`, then recreates `mjcf.xml` as a symlink to the original model. It does not empty an existing workspace. This preserves MJCF-relative dependencies but means the work directory is not a portable bundle merely because `mjcf.xml` exists. A previous `study.json` or driver can also satisfy later existence checks if a caller reuses the same directory. This is a source-level consequence; no such contaminated run was executed here.

`__enter__` opens one Bedrock AgentCore CodeInterpreter session. The client specifies 180-second read and 20-second connection timeouts and a boto retry setting of `max_attempts=1`; the comment's “no silent double-execute” assertion should not substitute for verifying the SDK's interpretation. That session is reused across all phase calls until `__exit__` best-effort stops it. Python state in this remote interpreter therefore differs from the local driver's simulator state.

`_read_extra_roots` exposes the original MJCF directory and installed MuJoCo source through `read_file`. `_common_tools` provides read/write; generation and repair additionally get `execute_python` and `local_exec`. `local_exec` deliberately receives no added AutoAdapter package path. This is not a general execution sandbox or import-proof boundary: the process still has its actual environment, and enforcement must be assessed from tool implementation. Source: `orchestrator_from_scratch.py:337–413`.

### 3. Conversation, phase entry and repair

Every `_run_loop` constructs a new `ReactLoop`, phase-specific system prompt, tool set and JSONL trace path. Study, initial algorithm generation and each repair therefore have separate model conversations. They share workspace files and the remote Python session, not an uninterrupted model-message history. Phase traces use `01_study`, `02_gen_algo`, and numbered `03_gen_repair_<attempt>` names.

`phase_study` requests a fixed JSON robot description and has no local execution tool. `phase_gen_algo` requests a complete `Robot` class in `driver_from_scratch.py`, including FK/IK/motion/gripper implementation. `phase_gen_repair` extracts failed test names and their `detail` strings, includes top-level errors when present, and asks the next model conversation to read and modify the existing file. These feedback messages report symptoms and preserve the fixed prompt-defined API. Source: `orchestrator_from_scratch.py:417–501`.

`run()` decides `study_ok` and `gen_ok` by file existence, not by `ReactResult.ok` or source freshness. The initial driver is validated even if the model exhausted its turns. If validation fails, a driver exists, and the outer retry limit is not exhausted, it enters repair and revalidates. Default outer retries are two after the initial attempt. Unlike `SelfAssemble`, token counts here accumulate Study, initial Generate and all Repairs, and total duration spans the whole run. `validate_ok` selects `structural_ok` if present, otherwise `all_ok`. This flag name is misleading: its selected set includes many behavioural tests. Source: `orchestrator_from_scratch.py:1113–1180`.

### 4. Validation construction and authority

The validator checks for direct AST `import` or `from` references whose module name starts with `auto_adapter.skeletons`. It ignores comments/docstrings; syntax errors fall back to import-line string checks, followed by actual import. This checks direct import syntax, not dynamic import, copied code or every dependency.

The evaluator imports the candidate into its own process, temporarily modifies `cwd` and `sys.path`, and calls the candidate's `Robot.build_from_mjcf` with the model's real path. The candidate constructs and owns its model/data. The evaluator creates neither a separate trusted MuJoCo pair nor an isolated process for each capability. All behavioural checks run sequentially on this one object. Import/build failures are recorded and returned as failed reports; cwd/path restoration uses `finally`.

Class dispatch is inferred from the candidate's available methods, in order: arm → quadruped → wheeled → aerial → humanoid. A composite class with arm methods therefore takes the arm branch. The evaluator does not check this selection against an independent robot-class requirement. Some promised APIs are optional in execution: `home`, gripper methods and quadruped walking are only checked when present. Sources: `orchestrator_from_scratch.py:505–655`.

Measurements mostly call candidate getters. `_scene_movable_bodies` scans free joints directly but first obtains `model` or `_model` from the candidate. It blocks an empty object-name list from skipping manipulation tests when that model exposes movable bodies. It does not independently establish that the candidate loaded the intended immutable model. An empty scene yields an explicitly skipped grasp case marked `ok=True`.

### 5. Exact behavioural criteria

| Branch/check | Execution and measurement | Passing condition / limitation |
|---|---|---|
| Home | Calls `home()` if present | No exception; returned Boolean and target error are ignored. |
| Arm positioning | Target is reported initial EE + `[0.03,0,0.03]`; calls `move_cartesian(...,duration=1.5)`; re-reads EE | Euclidean error `<0.03 m`; candidate getters supply both observations. |
| Gripper cycle | open → close → open if both methods exist | No exception; no finger-position or grasp criterion. |
| Object perception | Calls both discovery and position methods; cross-checks empty discovery against model free joints | A nonempty result must return a 3-vector for the first object; no general finite-value or coordinate-correctness check. |
| Grasp/lift | Approach `p0+[0,0,.08]`, lower to `+.005`, close, lift to `+.20`; some movement exceptions are swallowed | Object rise `>.03 m` AND final EE distance `<.10 m`. Other-object movement is diagnostic only, not a rejection term. No release test here. |
| Quadruped stand | `stand_up(2s)`, reported body heights before/after | Final height `>.15 m` AND no drop larger than `.02 m`; no full-duration stability measurement. |
| Quadruped sit | Runs after stand on same object | Final height `<0.8 × previous stand height`, with nonzero recorded stand height. |
| Quadruped walk | Re-stand then `walk_forward(1.5s,.2)` if method exists | World-X displacement `>.03 m` AND reported final height `>.12 m`; no lateral or heading condition. |
| Wheeled forward | `drive_forward(.3m,1.0)` | Horizontal displacement magnitude `>.05 m`; does not establish signed forward motion or arrival at `.3 m`. |
| Wheeled turn | `turn(.785rad,1.0)` | Absolute wrapped yaw change `>.2 rad`; requested sign and `.785 rad` accuracy are not checked. |
| Aerial takeoff | `takeoff(.5m)` | Altitude gain `>.15m` AND final `R[2,2]>.7`; no `.5m` arrival/hover-duration test. |
| Aerial move | Target current position + `[.3,0,0]`; calls with `tol=.15` | Final distance `<.25m` AND final `R[2,2]>.7`; checker tolerance differs from call parameter. |
| Humanoid stand | `stand_balance(2s)` | Final torso height `>.6m` AND `R[2,2]>.7`. |
| Humanoid squat | Re-stand; sample reported height in a wall-clock thread every `.01s` during blocking squat | Observed dip `≥.05m`, final height `>.6m`, uprightness `>.7`. Wall-clock sampling is not simulator-step sampling. |
| Humanoid walk | Optional; sample height/uprightness while walking | Start-heading forward `>.05m`, lateral `<.08m` absolute, yaw change `<.35rad`, minimum height `>.80m`, minimum uprightness `>.65`. This test is excluded from `structural_ok`. |
| Describe | Calls `describe()` | Accepts either dict or string; excluded from critical set. |

Sources: arm/perception/grasp `orchestrator_from_scratch.py:638–785`; quadruped `:787–849`; wheeled `:851–895`; aerial `:897–940`; humanoid `:942–1050`; aggregation `:1088–1109`.

`structural_ok` is the conjunction over all emitted tests whose names appear in `critical_tests`, requiring a nonempty list. It is not a required-case completeness check. `gripper_cycle`, `describe`, and `humanoid_walk` are not critical, while IK, perception, grasp, stand/sit/walk, aerial and wheeled tests are. `all_ok` includes all emitted tests. Consequently “structural” cannot be uniformly translated as “import-only” or as “all behaviours”.

The humanoid sampling threads are stopped/joined on the normal path, with no encompassing `finally` around the blocking candidate call. A raised squat/walk call can leave sampling active. This is an observed control-flow issue, not a runtime reproduction.

The validator's docstring mentions frame capture, but this function contains no implemented video recording. Creating `recordings/` does not establish retained videos. `_guess_ik_method` is a lexical keyword detector, including comments and strings; it does not establish the algorithm actually executed.

### 6. Full runner/test inventory interpretation

The four robot-specific from-scratch tests configure different Study/Generate budgets, invoke the real paid orchestrator, print metrics and copy artefacts. They are run scripts, not isolated unit tests. They commonly terminate with `os._exit(0)` regardless of printed success. Their fixed cost formula assumes one model's prices. Franka/Go2 script descriptions and keyword scans are proposed interpretations, not proofs of controller choice. The Piper script prints an `auto_adapter_<robot>_artifacts` destination that differs from the orchestrator's actual `<workspace_root>/<robot_id>` directory.

`benchmark_from_scratch_tasks.py` temporarily replaces `driver.py` with a symlink to the generated source, uses `TaskPlanner`, and records `TaskResult.ok`. It does not invoke the independent benchmark's physics scorer. It skips missing robot artefacts and therefore changes its realised denominator. Its success table should not be read as independent task-verdict evidence without an additional grading path.

`benchmark_onboarding_8_robots.py` mixes three cached robot records with new runs. Cached timings, tokens and costs are literal historical constants; cached records may be silently omitted when missing. A pre-existing export directory is not overwritten even after a new successful run. The displayed labour-cost comparison assumes human hours and wage rather than measuring them. This explains historical script behaviour; those figures are not future thesis evidence.

### 7. Reproduction scripts and their actual contrasts

- `scripts/run/synth_xmodel_one.py`, `synth_xmodel_robot.py`, `synth_quad_v2.py`, `synth_menagerie_so101.py`, `synth_drone_aerial_v2.py`, `synth_humanoid_h1_v4.py` instantiate the same from-scratch class with different models/scenes/budgets. On passing validation, they write a small `driver.py` wrapper resolving the real MJCF path and delegating to `Robot.build_from_mjcf`. They do not copy all MJCF dependencies into a portable export.
- The general cross-robot script installs a 3000-second SIGALRM, writes a failed report stub, and exits immediately on timeout. Its 2700-second remote session cap is a different limit. The alarm is not explicitly cancelled and `os._exit` bypasses session cleanup.
- `feedback_ablation.py` changes outer repair count between zero and two. Both conditions still have development execution and final validation. It therefore compares additional validation-driven repair opportunity; it does not isolate text versus physics feedback under equal repair budgets. Its “seed” is used in run names and records, not passed to an RNG/model configuration.
- `preflight_regrade_all.py` rebuilds and replays saved traces separately for old/new scorers, comparing counts for six changed grading types. Missing traces and exceptions can be omitted. It prints differences without a nonzero failure exit. Although its docstring mentions Self, its final loop covers only A-A and CaP.
- `eval_quad_v2.sh` runs only ours for Go2/ANYmal despite mentioning a CaP comparison in its comment; Unitree A1 is excluded. `eval_aerial.sh` runs ours and CaP sequentially on the same declared workspace. Neither shell script uses `set -e`.
- The Myriad shell wrappers choose environments, CUDA/render settings and training/evaluation budgets. The Cartesian RL wrapper launches nine background jobs and aggregates per-seed percentages with population standard deviation. Those settings belong in evaluation reporting, not framework novelty.

### 8. Artefact, infrastructure and plotting utilities

- `replay_traces_to_video.py` re-imports saved drivers, reconstructs several possible constructors, can inject missing render/step methods, and dispatches recorded calls using the fixed tool registry. Unknown/failed actions are skipped while later actions continue. A produced MP4 establishes that a partial replay rendered; it does not establish identical successful replay. Selection favours `driver.py` before `driver_from_scratch.py`. It records new simulation video, not recovered original frames.
- `build_supplementary.py` copies a predefined selection of videos/drivers and canonical YAML into a ZIP, skips missing inputs, and generates README prose. It neither reruns experiments nor establishes source anonymity automatically.
- `fix_string_bool_aggregates.py` repairs only exact strings `True`/`False` in three trial fields, backs up affected result files, then calls the current aggregation function. Unknown strings remain strings; this is a historical data correction, not general schema validation.
- `upload_hf.py` creates the dataset repository and uploads a README before honouring its `--dry-run` branch. Its upload allow-list also includes driver files despite a contrary introductory sentence. No upload was performed during this reading.
- `aws_bedrock_probe.py` attempts real small Converse calls; `aws_capability_probe.py` also contains a Converse call although its prose calls the activity read-only. Neither was run. Their service errors are infrastructure diagnostics, not robot evidence.
- `plot_synth_trajectory.py` classifies trace turns using execution-tool names plus exception/exit-code text. It concatenates Study and initial Generate only, omitting repair traces. Colour labels and iteration/token totals therefore reflect that selected trace slice, not the complete lifecycle.
- `paper/latex/build.sh` enforces the paper's six-page format; it does not define MSc thesis structure or method-detail requirements.

### 9. Consequence for the task-library extension and thesis

The extension changes two explicit decisions that AA1 currently supplies manually: the callable capability contract and the behaviour-specific pass rules. Its downstream integration must account for AA1's fixed tool registry, class dispatch and pre-authored skeleton methods; generating a JSON capability description alone does not make a new method callable by the existing planner or measurable by the existing validator.

The thesis needs an exact division of authored/supplied work, the difference between development execution and behavioural acceptance, and how generated requirements reach the execution/scoring boundary. These are technical methods explanations. Complete tool-schema internals, cloud configuration, cost-printing scripts and plotting utilities can remain reproducibility detail. No architectural change or new experiment was implemented during this reading.
## C. Controllers, state and hardware

## AA1 control-source review for thesis Chapter 3

Scope: all 8 Python files under `auto_adapter/skeletons/`, both `real_robot/*.py`, `experiments/rebuttal_franka_ik/run.py`, and the 6 assigned focused controller tests. All 17 files were read completely, not searched selectively. Their contents were compared byte-for-byte with `/private/tmp/aa1-thesis-source-review-20260909/source_snapshot/`: all match. This is the captured working tree at **2026-09-09T16:24:43.421233Z**, nested AA1 HEAD **ff486bf3499c14d105939ac43350b0f00baf206f**; it includes staged Stretch and untracked bimanual work. Do not describe all of it as the unchanged historical AA1 release. `snapshot_metadata.json` records exact dirty status. No simulation, model/API request, hardware action, network access, or repository mutation was performed for this review. Findings are source-derived, not new experimental evidence.

Additional supporting material fully read: the generated Franka driver and study it loads; SO101 scene; Go2 scene/model; Franka Panda model; LEAP scene/model; Stretch scene/model; real SO101 calibration JSON. Preserved copies are under `control_support/`. Supporting assets/artifacts were read after the Python snapshot, so are separately captured supporting material, not claimed as part of that original snapshot. ALOHA model/actuator/equality excerpts were inspected to check the bimanual assumptions, but its entire model was not read and is excluded from full-read coverage. The coverage JSON lists relative AA1 paths fully read.

### 1. What deserves methods text, and why

A framework subsection cannot be technically complete merely by stating task-library-to-capability and automatically generated pass criteria. The control source exposes a second essential question: **what exactly does an implementation do when a capability is invoked, and what counts as independent evidence that the intended physical effect happened?** A compact treatment should explain (i) binding generated names/interfaces to MuJoCo state and actuators, (ii) the division of fixed controller mathematics versus generated implementation, (iii) how execution advances the real simulation and exposes state, and (iv) why capability return values are not themselves the final verdict. These are architectural mechanisms, not a need to catalogue every controller class in the chapter.

Three concrete source examples make this necessary:

1. Go2 joint/spec ordering is FL, FR, RL, RR, but the MJCF actuator ordering is FR, FL, RR, RL. Correct separate maps from joint names to qpos/dof addresses and actuator names to ctrl indices make the same flattened PD vector act on the intended legs. A generic statement that the system “reads the robot model” misses the actual binding problem.
2. Package serial-arm IK uses temporary qpos writes and restores the physical configuration before actuator interpolation. The historical generated Franka driver does not restore those writes and also changes ctrl during its numerical search. It reaches the IK solution kinematically before its subsequent physics motion. “Uses MuJoCo” is therefore insufficient to establish dynamic execution authenticity.
3. `move_joints` in the package serial arm reports success when its loop ends; `contact` grasp reports proximity; `real_contact` does not implement the sustained-force verification claimed by its documentation. A thesis needs to define which simulator observations establish success independently of such implementation returns.

For a concise thesis section, these mechanisms can be consolidated into one subsection on driver synthesis and execution plus one on independent validation/repair, or woven into an existing technical synthesis/validation subsection. Full gain tables, every robot joint name, and every helper function belong in implementation notes/appendix. Avoid adding standalone sections for bimanual, LEAP, Stretch, or hardware unless they are in the confirmed research scope. The task-grounded capability/criteria extension remains the principal new idea; the AA1 execution substrate still needs enough explanation to make that idea reproducible and its evidence interpretable.

### 2. Shared representation, model ownership, and units

#### `auto_adapter/skeletons/base.py`

- `ArmSpec` (16–122) is a mutable dataclass, not a parsed capability graph. It holds an EE site or fallback body, ordered joint names, ordered actuator names, a name-to-limit dictionary, optional home posture, IK parameters, gripper control constants, backend selection, grasp candidate names/radius, and timing hints. The generation agent is expected to supply these bindings; the spec itself performs no MJCF parsing.
- `ArmSpec.__post_init__` (106) requires at least one EE reference, nonempty arm joint/actuator lists, and equal list lengths. It does not require unique names, finite/ordered limits, correct actuator transmission, or that an actuator actually drives the corresponding joint. If both EE fields are provided the site wins at runtime. Home vector length is checked later by the skeleton.
- Serial defaults: damping 0.001; 30 iterations; XYZ tolerance 0.001 m; joint-update vector norm cap 0.2; raise on nonconvergence; close/open controls 0/1; gripper settle 30 simulation ticks; grasp radius 0.08 m. The same vector norm cap is applied to hinge radians and slide metres if a mixed chain is supplied: there is no unit normalization. `joint_limits` is documented in radians although slide joints are accepted.
- `QuadrupedSpec` (126–191) supplies torso body name; leg→ordered hip/thigh/calf joints and actuators; flattened standing posture; scalar kp/kd; gait frequency/amplitudes/sign/phases; target height; vx/vy/vyaw limits; timing. There is no dataclass post-init validation. Defaults kp=30, kd=1.5, frequency=1.5 Hz, thigh amplitude=.12 rad, calf amplitude=.18 rad, thigh sign=-1, target height=.30 m. **vx_max, vy_max, vyaw_max are unused by this controller.**
- `SkeletonBase.__init__` (202) stores the provided model/data objects by reference. There is no copy, ownership guard, trusted state wrapper, transaction, or isolation layer. Caller-injected objects are the actual live objects the controller subsequently mutates.
- `SkeletonBase.from_mjcf` (216) resolves the file's real path (important for symlinked MJCF relative meshes/includes), constructs a fresh MjModel and MjData, calls `mj_forward`, and invokes the concrete constructor. It **does not select/reset a named home keyframe and does not step/settle physics**. Its comment “Settle the initial qpos” refers only to derived-state computation, not dynamic settling. `qpos0` and an XML `<key name="home">` are different concepts.
- `step(n)` (235) calls `mj_step` n times, advancing the entire shared world, with no real-time wait. `settle(secs)` (240) uses floor(secs/model timestep), minimum one tick. Both use the model timestep; `ArmSpec.sim_dt` and `QuadrupedSpec.sim_dt` are unused by their motion routines.
- `render` (247) caches one lazy MuJoCo Renderer, recreates it when requested resolution changes, updates its scene from current data and returns an image copy. It suppresses errors closing the old renderer; renderer creation/update/render errors propagate. `close` (259) only releases the renderer, suppressing errors; it does not reset the model/data or enforce runtime shutdown.
- `skeletons/__init__.py` fully exports arm, quadruped, hand, Stretch and grasp types. The captured file does **not** export/import the bimanual types; bimanual tests import its module directly. Package docstrings call these “production-tested”; the source/tests reviewed here are not evidence for production maturity.

### 3. Serial-arm execution: exact math and state effects

#### Structural resolution: `arm_serial_dls.py:ArmSerialDLSSkeleton._resolve_indices` (56)

Constructor (47) stores the shared objects then resolves bindings once. The EE site is preferred and resolved via `mj_name2id(SITE)`; if no site is specified a body ID is resolved instead. Missing references raise ValueError. The choice is remembered by `_ee_use_site` (85), so all pose/Jacobian calls consistently target the same reference point.

Each specified arm joint is resolved independently, required to be HINGE or SLIDE, and contributes three distinct things: model joint ID, `jnt_qposadr[jid]`, and `jnt_dofadr[jid]` (87–102). This avoids assuming qpos and qvel indices coincide when the scene has a floating base or free objects. Actuators are separately resolved by name (104–116); equal count is checked, but actuator transmission type, joint target, gain/control mode, gear scaling, duplicates, and ctrlrange are not checked. Gripper actuator IDs are similarly resolved (119–126).

Backend selection/setup occurs before home and limit-vector creation (128–135). The older `_weld_eq_ids` attribute is a compatibility alias of the backend map. Home is the explicit vector (length checked), otherwise the model qpos0 values at arm addresses (137–147). Limits must have an entry for every arm name and are copied into `_q_lo/_q_hi` (149–156). Despite the comment “cross-check MJCF,” **no comparison to MJCF joint ranges occurs**.

#### Observations and FK

- `get_joint_positions`/`get_joint_velocities` (165/168) read actual data.qpos/qvel at mapped addresses into new arrays.
- `_ee_pos_now`/`_ee_rot_now` (171/177) read world-frame `site_xpos/site_xmat` or `xpos/xmat`; rotation is a 3×3 matrix, no Euler/quaternion convention ambiguity at the public surface.
- `_ee_jac_pos` (183) uses `mj_jacSite` or `mj_jacBody`, requesting only the positional Jacobian. Site versus body is substantive: a body origin is not necessarily a grasp centre/fingertip. The Franka test uses body `hand`, while SO101 uses the site between fingers.
- `get_ee_pose` (190) runs mj_forward then returns the chosen world-frame position/rotation. `get_object_position` (196) resolves a named body each call, raises ValueError if absent, and reads world-frame xpos **without its own mj_forward**; it relies on the current derived state already having been refreshed.
- `is_holding` (202) and compatibility property `_held_body` (205) delegate entirely to backend logic; they do not measure grasp independently.
- `fk(q=None)` (214): current-state case calls get_ee_pose. Candidate-vector case checks length, snapshots **all** data.qpos, writes only arm addresses, forwards, copies EE pose, restores all qpos in `finally`, and forwards again. It does not step physics or advance simulation time. It does not clip candidate q or validate finiteness. It does not snapshot all other MjData fields; the meaningful preserved primary state here is qpos, with qvel/ctrl not explicitly changed by this function.

#### Exact DLS: `ik` (243–323)

Input target is reshaped to a 3-vector; q is q_init copied or actual current arm q, with shape `(dof,)` required. Only **position** is solved; returned rotation observations do not imply orientation control. With candidate q inserted into live data and mj_forward run, compute world-frame error `e = target - p(q)` and positional Jacobian columns `J = jacp[:, arm_dof_addresses]`.

For each iteration, the actual solve is:

```
r = ||e||_2
lambda = lambda_base * max(1, 0.05 / max(r, 1e-6))
dq = J.T @ solve(J @ J.T + lambda**2 * I_3, e)
dq *= min(1, step_clamp / ||dq||_2)  # implemented only when norm exceeds cap
q_next = clip(q + dq, spec_joint_lower, spec_joint_upper)
```

Convergence checks `r < tolerance` before computing the next update. Damping is residual-dependent only. Comments mention singular values and joint-limit chatter, but **no SVD, smallest singular value, joint-limit distance, nullspace objective, restart, line search, collision check, or posture bias is used**. At smaller residuals under 5 cm the damping increases; it is not the generated Franka driver's damping law (below).

The full qpos snapshot is restored in `finally` and mj_forward rerun even if the numerical solve raises (283–319). No actual physics step is taken during IK. Numerical LinAlgError propagates; there is no finite-value handling. `IKUnreachableError` (21/29) stores residual, tolerance and a copy of q_final. After max iterations it is raised when `last_err > tol`, unless disabled per call or spec. Two exact reporting limitations: last_err is measured **before** the final q update (q_final may have a different residual), and equality to tolerance is not a break condition but also not a final exception condition. There is no final FK residual recomputation. With zero iterations last_err stays infinite. Suppressing the exception returns a best-effort joint vector.

#### From solution to physical actuation

- `set_arm_actuators` (329) checks vector length and assigns q elements directly to mapped ctrl indices. It assumes position-control units; no clipping here.
- `move_joints` (336) requires shape `(dof,)`, clamps target to spec limits, and starts interpolation from **current actuator targets**, not measured qpos. With `N=max(1,floor(duration/model_dt))`, every step sets `u_i = u_start + ((i+1)/N)(q_target-u_start)`, then calls `self.step(1)`. Routing through self.step deliberately permits tracing/video wrappers to observe each tick. The actual closed-loop servo is supplied by the MJCF actuator gains/damping; the Python code does not compute joint PD for this arm.
- There is no final tracking tolerance, dwell, velocity, collision, joint error, or finite-state check. Return True means the interpolation loop completed. Duration is simulated time, floored to integer ticks; a zero/negative duration still yields one tick.
- `move_cartesian` (365) solves IK at the current state then performs joint-space interpolation. This is not a straight-line Cartesian path and not continuously replanned Cartesian feedback.
- `home` (370) uses move_joints to the stored home, preserving dynamic motion semantics. Unlike bimanual construction, this method does not teleport qpos.
- `gripper_open` (378) and `gripper_close` (386) can override settle_steps by **mutating the shared ArmSpec**, so the override persists on subsequent calls. Open returns True after disengage; close returns whether engage returned a body name, not whether the finger actuator attained its closed target.
- `describe` (400) reports bindings, limits, home, grasp description and IK parameters; it does not independently validate them, and omits `ik_raise_on_unreachable` from its IK subobject.

#### Scene-specific grounding: SO101

The fully read `assets/mjcf/so101_mujoco.xml` defines a fixed base at table height .02 m, five arm hinges with exactly the names/ranges supplied by the hand-written smoke spec, position actuators with kp=100,100,80,60,60, plus a visual jaw position actuator kp=20 (218–223). `ee_site` is at `(0,0,-.098)` in gripper_link (135–137). Six free objects are connected to gripper_link by initially inactive unnamed welds (205–211).

Critically, robot meshes are noncolliding (18–22), jaw_visual mesh is noncolliding (122–123), and both nominal finger box geoms explicitly have contype=conaffinity=0 (126–133). The earlier comment claiming only the fingers have real collision (19–20) is stale. In this scene, a successful lift is weld-assisted transport with actual world dynamics around an equality constraint, **not frictional finger grasping**. An excellent thesis must bound what such a run establishes.

### 4. Grasp backends: same interface, different truth conditions

#### Interface/factory

`grasp_backends.py:GraspBackend` (40) is a typing Protocol: setup, engage→optional held body name, disengage, is_holding, held_body, describe. It provides no enforcement or measurement isolation. Implementations receive the skeleton and can mutate model/data directly.

`make_grasp_backend` (519) chooses an explicit registry name first (`weld`, `contact`, `real_contact`, `noop`); an unknown explicit value raises ValueError. Otherwise no gripper actuators→noop, any nonempty `weld_graspable_bodies` list→weld, otherwise contact. **It does not inspect whether weld constraints actually exist.** Providing a candidate list for contact therefore requires selecting contact explicitly; otherwise that list causes weld selection. Explicit selection may override absence of gripper actuators.

#### Weld: `WeldGraspBackend` (76)

`setup` (92) scans all model equalities of type mjEQ_WELD and maps each listed candidate name to an equality if either endpoint body matches. It does not require the other endpoint to be the configured gripper body. Multiple matching equalities can overwrite earlier entries; unknown names/missing welds are silently omitted. Empty candidates produce an empty map. Neither setup nor construction disables existing equalities.

`engage` (129) assigns close control, steps configured settle count if positive, and exits None if no weld map. Otherwise it forwards, compares object **body-origin** positions to the chosen EE reference, and picks the nearest candidate strictly within grasp_radius. If none, `_held=None` and return None; existing equality activation is not explicitly cleared in this branch. If a candidate exists, read both equality endpoint poses `(p1,R1),(p2,R2)`; compute `t12=R1.T@(p2-p1)`, `R12=R1.T@R2`; convert relative rotation to MuJoCo quaternion using mju_mat2Quat; write model.eq_data anchor[0:3]=0, relative translation[3:6]=t12, quaternion[6:10]=q12; leave torquescale[10] unchanged. This captures the present relative transform so activating the weld does not enforce a stale XML anchor. Then set only that candidate's equality active among the backend's known map, step **50 additional ticks**, cache held body, return its name.

`disengage` (182) sends open control, disables all mapped equalities, optionally settles, and clears cached held state. `is_holding`/`held_body` (191/194) return the cache; they do not inspect equality activation, constraint residual, attachment forces, current separation, or actual object retention. `describe` (197) reports map and cache.

#### Proximity-based contact: `ContactGraspBackend` (210)

`setup` (228) uses an explicit candidate list or discovers bodies whose first owned joint is FREE; unnamed free bodies are excluded. It does not exclude a free-floating robot itself. Explicit missing candidate names are permitted and later skipped.

`engage` (250) writes close ctrl, steps configured settle count, forwards, then picks nearest candidate body origin strictly inside grasp_radius. Missing bodies raising KeyError/ValueError are skipped. It records no contact count, force, lift, finger geometry, friction, or sustained observation. There is no equality mutation; the simulation can physically hold objects if appropriate, but **the success detector itself is proximity-only**. `disengage` (273) writes open controls, settles and clears cache. `is_holding`/`held_body` (280/283) are cached, so later slip does not change them.

#### Noop: `NoOpGraspBackend` (299)

Setup/open perform nothing; close returns None; is_holding=False; held_body=None; describe says noop. Thus ArmSerial's gripper_open can return True even though nothing was actuated; gripper_close returns False. This asymmetry is part of the public-method truth semantics.

#### “Real contact”: actual implementation contradicts its description

`RealContactGraspBackend` (336) says sustained contact forces across N steps, slip sensing, and live is_holding. Source does **none** of these:

- Constructor (363) stores force_threshold=.5 and check_steps=30. setup (372) discovers candidate free bodies/valid IDs, but `_candidate_body_ids` is never used in detection.
- Gripper-body discovery (400–425) reads `actuator_trnid[aid,0]` as a joint ID without checking actuator transmission type. From that purported joint body it adds up to 8 ancestors and all descendants up to 16 ancestor hops. Even for a direct joint, this can classify multiple arm links/base as gripper bodies. For Panda's actuator8 the MJCF explicitly uses tendon `split` (panda.xml:253–257,276–277); the backend misinterprets tendon index as a joint index. For the canonical first tendon / first joint ordering this starts at joint1/link1, expanding much of the robot rather than identifying both fingertips by transmission traversal.
- `_check_held_via_contact` (427) calls mj_forward, loops over data.contact, accepts contacts with exactly one body in this broad gripper set, walks the other body's ancestors to a candidate name, and adds **`abs(con.dist) + 1.0`** per matching contact (463). It never calls mj_contactForce or reads efc_force. The “force threshold” is actually a contact-count/separation proxy threshold; a single ordinary contact contributes roughly 1 and passes the .5 default. There is no bilateral opposing-finger condition, normal-force threshold in physical units, tangential-force/friction verification, or grasp stability calculation.
- `engage` (478) sends close ctrl, runs check_steps ticks, samples the contact detector once after those ticks, caches the result. It does not require sustained contact across the ticks, and ignores ArmSpec.gripper_settle_steps overrides.
- `is_holding`/`held_body` (494/497) read only cached state. No recomputation occurs after moving/lifting/slipping. `disengage` (488) sends open ctrl, waits check_steps and clears cache. `describe` (500) still labels the proxy threshold force_threshold.

Consequently the documentation's equivalence to a real robot holding an object, and the claim that is_holding tracks later slip, must not enter the thesis as implemented facts. This is a source-level discrepancy, not a new empirical failure measurement. It motivates judging actual object pose/retention from the independent runtime rather than trusting backend names or bool returns.

### 5. Quadruped PD gait: exact phases, calibration and checks

#### Mapping and observation

`quadruped_pd_gait.py:QuadrupedPDGaitSkeleton.__init__/_resolve_indices` (45/54) resolves torso body; takes **dictionary insertion order** of leg_joint_names; requires exactly 4 legs, matching actuator-map keys, exactly 3 joints/actuators per leg; verifies hinge type; separately caches joint IDs, qpos addresses, dof addresses and actuator IDs. It requires home length 12 and caches ctrlrange as torque limits. It does not verify actuator transmission/control type, ctrllimited, finite values, or joint range coverage. Unbounded actuator ctrlrange defaults could be inappropriate because it always clips to recorded limits.

`get_joint_positions/velocities` (156/162) read mapped arrays; `get_base_pose` (165) forwards and returns world XYZ/rotation; `get_body_height` (172) reads world xpos z without forwarding. The torso reference is a body origin, not necessarily centre of mass. There is no public velocity command or heading target implemented despite cmd_vel-limit fields in spec.

Actual Go2 MJCF has free base qpos first (7 coordinates, 6 dofs), followed by leg hinges in FL/FR/RL/RR body order; actuator declarations are FR/FL/RR/RL (go2.xml:248–260). Thus using the first 12 qpos/ctrl values as leg angles/torques would be wrong. The class's separate address maps solve a real model-binding issue. Go2 has body/sensor IMU/velocity entries (263–308), but this controller reads qpos/qvel/xpos/xmat directly, not those sensor outputs. Its XML home keyframe (311–313) is not loaded by base.from_mjcf.

#### PD and trajectories

`_apply_pd` (179): `tau = kp*(q_des-q) + kd*(qd_des-qd)`, with default qd_des=zeros(12), then elementwise clip to actuator ctrlrange and assign mapped controls. Behavior calls never supply analytic qd_des for the interpolated/gait trajectory, so D term damps actual velocity toward zero rather than tracking trajectory velocity. No feedforward gravity/inertial term, force allocation, contact phase estimation, foot IK, centroidal balance, MPC, or learning exists.

`stand_up` (194) linearly interpolates **measured joint angles** to home over max(1,floor(duration/dt)) ticks, recomputes PD every tick, then holds home with PD for floor(.5/dt) further ticks. Returns `abs(body_z-target_z)<.05`. It does not command body height directly; body_height_target only sets the return criterion. There is no orientation criterion. Initial pose/home feasibility comes from spec/model.

`_run_trot` (221) saves initial world torso position, uses frequency omega=2*pi*f and phase offsets explicitly supplied or leg slots (0,3)=0, (1,2)=pi. This yields FL+RR versus FR+RL for conventional ordering; arbitrary leg labels are accepted but arbitrary ordering does **not** guarantee physical diagonals. With `a_thigh = swing_amp_thigh*(speed/.3)*thigh_sign` and `a_calf=swing_amp_calf*(speed/.3)`, each phase phi=omega*k*dt+offset gives:

```
swing=max(0,sin(phi)); stance=min(0,sin(phi))
q_thigh = q_home_thigh + a_thigh*(swing + .5*stance)
q_calf = q_home_calf - a_calf*swing
q_hip = q_home_hip
```

Every tick applies PD and steps the whole world. Speed only scales joint amplitudes; it is **not measured forward speed feedback**, not a metres-per-second promise, and not clipped by vx_max. No target joint-limit clipping, fall check, yaw correction or lateral correction occurs. Gait phase restarts at k=0 on **every call**, which matters when short commands are repeated. The return is raw world Δx and Δy.

`calibrate_walk_direction` (265) first-call probes +abs(spec_sign) then -abs(spec_sign), each .6 s at speed .2 by default, and chooses +1 if dx_positive>dx_negative, else -1 (ties choose -1); it does not require either probe to move forward. It snapshots/restores qpos, qvel and ctrl before the second probe and after probing, calling mj_forward; it **does not restore data.time or the full simulation state** (e.g. warm-start/actuation/plugin state). Both probes route through self.step, so instrumentation can observe probe ticks and then a restored configuration. Description “full sim state” is too strong. It caches sign/probe displacement statistics and skips re-probing thereafter. Non-unit spec sign magnitude affects probe amplitude but the cached result is unit sign.

`walk_forward` (308) uses calibrated sign by default, runs trot, returns **world Δx>0.05 m**, not body-heading displacement and not ≥ as its docstring says. Lateral/yaw/uprightness are not part of return. `sit` (335) adds .6 to each home thigh and subtracts .5 from each calf, interpolates from actual q to this folded posture using PD, and returns body height <.7*target without an added final hold. `describe` (357) reports spec sign, not cached calibrated sign/probe values; actual executed sign can therefore differ from the description.

### 6. Bimanual shared-world extension

`bimanual_serial_dls.py:BimanualSerialDLSSpec` (23) simply pairs two ArmSpecs. Wrapper constructor (33) requires that spec type, retains one model/data, constructs two ArmSerialDLSSkeleton children against **the very same objects**, aliases them as left/right and _left/_right, rejects overlapping arm bindings, wires child stepping into the parent, and initializes a neutral posture.

- `_make_child` (51) temporarily replaces MuJoCo enum HINGE/SLIDE class attributes with plain ints to work around the package arm's NumPy-scalar versus enum membership check, then restores them in finally. This is global module/class mutation during construction, not a separate model implementation. No concurrency guard is supplied.
- `_validate_disjoint_arm_bindings` (64) rejects left/right overlap in arm joint names/actuator names and resolved arm joint/actuator IDs, reporting names. It does not check overlap involving gripper IDs or equality maps, nor coupling through shared kinematic ancestors, nor collision interaction.
- `_wire_child_steps_to_parent` (100) replaces each child `.step` with a closure calling parent.step. All inherited arm interpolation and grasp backend ticks thereby advance the same world and go through the parent instrumentation path; no double stepping occurs merely from having two children.
- `_initialize_safe_home` (112) **writes both live arm qpos and ctrl to home immediately during construction**, opens gripper ctrl, and calls `_set_actuated_joint_open` before mj_forward. It does not interpolate, reset qvel, verify collision freedom, or ask MuJoCo to solve a neutral posture. “Collision-free” is a property of the chosen fixture tested later, not a general guarantee.
- `_set_actuated_joint_open` (125) supports direct joint gripper transmissions, obtains qpos address, clips to joint range if limited, writes the driven joint, then scans joint equality partners and writes the same scalar to each partner. It does not interpret equality polynomial coefficients. ALOHA's inspected equality uses q1=q2 (aloha.xml:291–293), which matches this initialization; it is not a general coupled-joint solver. ALOHA gripper's ctrl interval [.002,.037] is displacement in metres, not the real hardware's angular convention (aloha.xml:51–80; joint_position_actuators.xml:9,17).
- `_arm` (152) insists on explicit string left/right; invalid/implicit “both” fails. `get_ee_pose`, `get_joint_positions`, `move_cartesian`, `move_joints`, gripper methods (197–231) delegate to the chosen child. Cartesian solves are **independent**, position-only and performed while the other child retains existing controls. The other arm can still move physically during each world tick.
- `_move_both_joints` (161) validates and clips each target, reads each arm's starting ctrl, updates **both** targets at each interpolation fraction and calls parent.step once per tick. `home` (193) uses this simultaneous interpolation. This is joint trajectory synchronization; it is not coupled bimanual IK, interarm collision planning, relative object-pose control or cooperative force control.
- `hold` (233) snapshots all unique arm+gripper ctrl IDs, reassigns those values every tick for max(1,floor(duration/dt)) ticks, and returns True. It does not hold the measured pose by adding feedback beyond MJCF actuators or validate end poses. `describe` (247) correctly reports object-identity shared_model/shared_data and child descriptions.

### 7. Four-fingertip LEAP extension

`hand_fingertip_dls.py` provides a different control architecture from serial-arm solve-then-execute. `_name` (18) trims nonempty strings; `_finite` (24) converts to float and rejects nonfinite. `HandFingertipDLSSpec` (32) is frozen but stores ordinary mutable dictionaries for mappings; not deep immutability. It fixes exactly `index,middle,ring,thumb` as the fingertip task order (15).

Spec `__post_init__` (48) validates equal nonempty unique joint/actuator lists; exactly four distinct geom names; explicit joint partition or even consecutive splitting by finger order; exact once-only partition of joint list; finite optional matching home; positive finite bounds/gains. The six scalar bounds are checked after float conversion but are not reassigned to float, though execution casts them. A finger partition is metadata; runtime Jacobian construction uses the entire resolved joint list rather than per-finger subsolves. Palm name is stored/resolved but the runtime does not prove the base is fixed or use a palm-relative transform.

`_resolve_indices` (148) resolves palm; requires each hand joint a limited hinge with valid finite ordered range; records qpos and dof addresses. Each actuator is required to use JOINT transmission to the expected paired joint and to have finite ordered limited ctrlrange. It does not directly inspect gain/bias to establish position-control mode or account for arbitrary gear scaling; canonical LEAP MJCF uses direct position actuators kp=3, kv=.01 and joint damping=.03 (right_hand.xml:28–29,109–132), satisfying the intended convention. Each fingertip geom is resolved. Effective command bounds are intersection of joint and actuator ranges; empty intersection errors. Home is qpos0 slice or explicit finite vector, clipped to effective bounds; positive finite model timestep is cached.

- `_duration_steps` (237) requires finite nonnegative duration and uses ceil(duration/dt), minimum one. There is no fixed maximum step budget besides supplied duration.
- `_joint_vector` (243) validates exact finite vector. `_read_joint_positions` (252) reads qpos only. `_write_joint_command` (258) clips and writes ctrl only. `_target_array` (263) demands exactly all four canonical fingers and finite XYZ vectors. `_fingertip_positions_array` (281) forwards and reads geom_xpos, so the controlled point is the **MuJoCo geometry frame origin**, not a contact point selected on the tip surface. `get_joint_positions` (288) and `get_fingertip_positions` (291) expose those real state observations/copies.
- `move_joints` (298) clips target once; every real physics tick reads actual q and commands `q + clip(target-q, ±max_joint_command_delta)`, default .1 rad. This is a bound on current-to-target command offset per tick, **not linear time interpolation and not measured angular velocity limiting**. After its full duration it returns whether every actual joint is within .03 rad of the **clipped target**. A request outside joint range can succeed after reaching the clipped boundary, so the public semantics should specify clamping.
- `home` (320) uses the same actuator-only controller to clipped home. There is no qpos teleport in hand runtime control.
- `move_fingertips` (323) is an online Cartesian feedback loop advancing physics at every DLS update. At each tick forward; stack four 3-vector world errors in canonical order and stack four `mj_jacGeom` positional Jacobians, restricted to mapped hand dof columns. For canonical 16 joints, J is 12×16. Solve `dq = J.T solve(J J.T + lambda² I_12, e_stack)` with fixed lambda=.02. If solve raises LinAlgError or dq is nonfinite, use zero update (continue physical holding and eventually report failure). Cap ||dq||₂ to .08; command `q_actual + position_gain*dq` (gain=1), clip elementwise to actual q±.1 then to effective joint/ctrl bounds, assign ctrl and step once. No candidate qpos writes, no scratch world, no nullspace term.
- It breaks when **maximum per-finger Euclidean error**≤.003 m, possibly before any physics tick if all goals are already met. After loop it rereads actual tips and returns the same condition. It does not assert orientation, contact, manipulability, finger force or object grasp. No fingertip direction/pinch relation is encoded: it is four point positioning goals.
- `describe` (379) returns dof, palm, name partitions, geom references, home and selected IK parameters. It omits some control tolerances/bounds that still affect behavior.

The model has fixed palm at world `(0,0,.1)` with quaternion `(0,1,0,0)` (right_hand.xml:163); each of four articulated fingers has four hinge joints and an end tip mesh geom (227,280,333,384). The module uses world coordinates, so it automatically incorporates this flipped palm frame through MuJoCo Jacobians; it does not manually negate axes or use sensor entries. Collision filters and mesh friction are present in XML, but controller success does not assess contact behavior.

### 8. Stretch model-specific mobile manipulation

`stretch_mobile_manipulation.py` uses no DLS at all. Helpers `_finite` (15, also rejects bool), `_positive` (24), `_name` (31) and `_wrap` (37, atan2(sin,cos)) validate scalars/names/angles. `StretchUnreachableError` (41) marks geometric workspace failure. Frozen `StretchMobileManipulationSpec` (46) fixes canonical base/tool, forward/turn/lift/extension/wrist/grip actuator names, four extension slide joints, expected dt=.002 s, turn_gain=5, drive_gain=8, max drive distance=1 m, max control iterations=6000, settle=75 ticks; its post-init (73) normalizes names and enforces four unique arm joints and positive bounds/counts.

Constructor/_resolve (111/125) retains caller model/data, runs resolution once, requires model timestep match expected within 1e-12, resolves base/tool bodies and requires a free joint owned at the base's first joint address. All six used actuators need finite bounded ctrlranges. Forward, turn, arm_extend must be TENDON transmission. `_joint_qpos` (184) requires lift/4 extension/grip SLIDE and wrist HINGE and records qpos addresses. It does not check the exact tendon composition/coefficient or confirm lift/wrist/grip actuators target their corresponding joints. Correct behavior is explicitly canonical-model-specific.

#### Why the model matters

The fully read Stretch XML defines forward tendon=.5*left_wheel+.5*right_wheel, turn=-.5*left+.5*right, extend=sum of four slider displacements (385–399). Three equality constraints pair the four slides (402–405), so their total 0–.52 m is controlled as one coupled extension; each slider has 0–.13 m range. Forward/turn are **motor** tendon commands with gear=3, ctrlrange [-1,1], not velocity actuators; lift and extension are affine position-like general actuators; lift and multiple arm bodies have model gravity compensation. Therefore Python `speed` parameters cap motor input amplitude; the code does not enforce a measured linear/angular speed in SI units. Gripper slide controls both opening joints through equality coefficient 10 (406–408). Canonical tool reference `link_gripper_slider` is a body frame upstream of rubber finger tips (296–329), so a tool-position success is not fingertip contact at that world point.

#### State and common checks

`_base_position/_base_rotation/_base_yaw` (199/202/205) read world base state; yaw=atan2(R[1,0],R[0,0]). Public get_base_pose/yaw/ee_pose (224/229/234) forward before returning copies. `_clip` (190) bounds ctrl. `_steps` (193) requires positive duration, returns min(6000,max(1,ceil(duration/dt))). `_stop_base` (209) zeros forward/turn. `_settle` (213) zeros base controls then runs 75 actual ticks while other controls remain. `_assert_safe_state` (218) checks all qpos/qvel finite and R_base[2,2]≥.95; this runs after motions/settles, not continuously at each tick, and is not collision detection.

#### Yaw and straight drive

`turn` (242) wraps current yaw+finite relative angle, validates positive speed, delegates `_turn_to_yaw` (249). Angle wrapping means large rotations are reduced to a principal yaw target, not cumulative turns. Optional hold_arm snapshots current lift and summed extension as actuator targets. Each turn tick computes wrapped yaw error e; sets forward=0 and turn command `-sign(e)*min(speed,5*abs(e))`, clipped to ctrlrange; steps once; counts 20 consecutive **pre-step** errors≤.01 rad. Failure to obtain dwell in 6000 ticks zeros base and raises RuntimeError. Success zeros base/settles 75 ticks, checks finite/upright, and requires settled |yaw error|≤.02 rad. It returns used step count including settle and target/final/error yaw. The minus sign is canonical wheel-tendon convention, not a universal robot-frame rule.

`drive_forward` (277) requires |distance|≤1 m and positive speed. It takes current base position/yaw and world forward axis `R_base @ [-1,0,0]`; thus unrotated positive drive is toward world -X. If |distance|≤.01 it settles and returns travelled=0 without attempting that small requested movement. Otherwise each tick projects actual displacement onto the **initial** forward axis; computes remaining distance and wrapped initial-heading error; sets forward `sign(error)*min(speed,8*abs(error))`, and turn `-5*yaw_error`, each clipped. It requires 20 consecutive pre-step distance errors≤.01 m and yaw errors≤.02 rad. After settle/safety it requires final longitudinal distance error≤.02 m. It does not test lateral drift, full XY target error or an obstacle path. In nonconvergence it zeros base and raises. The result reports projected travelled distance, not odometry integrated from wheel rotations.

#### Geometric reach and arm control

`_arm_geometry` (321) computes current tool coordinates in base frame, actual total extension s=sum(q_slide), lateral offset a=tool_local_x, extension origin b=tool_local_y+s, and lift origin c=tool_local_z-q_lift. For target world XY relative to current base with radius r, it rejects r≤|a|+1e-6, chooses `y_local = -sqrt(r²-a²)`, then `extension_target=b-y_local`, `lift_target=(R_base.T@(target-base))[2]-c`, `yaw_target=wrap(atan2(dy,dx)-atan2(y_local,a))`. This assumes the side arm extends along base negative Y and base yaw exposes the radial point, with current wrist/tool geometry treated as fixed. It is an analytic canonical planar geometry calculation, not general mobile-manipulator IK, task-space optimization, or whole-body planning.

`_hold_arm` (344) commands measured lift and total extension clipped to actuator ranges. `move_cartesian` (352) validates a finite XYZ vector and gets a capped arm-phase duration budget. It computes yaw target and **executes base rotation first**, then recomputes geometry and only then checks lift/extension ranges. An unreachable height/extension request can therefore rotate the base before throwing StretchUnreachableError. Base translation is never commanded by this method.

During the arm phase, lift/extension targets are fixed; forward/turn controls are zero; each tick steps physics then measures tool error. It tracks minimum error and requires 20 consecutive post-step errors≤.01 m and yaw discrepancy≤.02 rad. This is actuator tracking with measured stopping, not repeated geometric/Jacobian target updates. Timeout raises RuntimeError after stopping base. After settle/safety, final tool error must be≤.025 m. The returned `physics_steps` counts **arm phase plus its settle only**, omitting the preceding yaw phase and yaw settle; it is not total call simulation ticks. Total call duration can exceed the duration parameter by the entire yaw sequence and settle intervals.

`home` (399) linearly interpolates lift, total extension and wrist ctrl from current ctrl to model qpos0 values for the requested capped ticks, zeros base controls, settles/checks safety, returns True without measuring home joint error. It does not reset the base, gripper, world objects, qvel, or select a named XML keyframe. `_set_gripper` (423) writes bounded grip target and steps 75 ticks, checks safe state and returns True. Open/close (430/434) use ctrl upper/lower limits. They do **not** detect holding, object contact or achieved aperture; they also do not explicitly zero existing base controls. `describe` (438) identifies model-specific channels and coupled extension.

### 9. Real hardware is a separate boundary, not Direct-MuJoCo execution

#### `real_robot/real_so101.py`

The header describes reuse of synthesized-driver math, but this file actually implements its own `RealSO101` class; it imports neither a generated driver nor ArmSerialDLSSkeleton. It uses MuJoCo strictly as a **shadow kinematic model** and LeRobot as real actuation/observation. No mj_step call occurs anywhere. This is not SDK→Translation→real MuJoCo dynamic evidence.

- `__init__` (76) loads MJCF/model data; resolves five hardcoded SO101 arm joint qpos addresses and site `ee_site`; obtains MJCF joint ranges; then constructs LeRobot SO101Follower with use_degrees=True, torque-disable-on-disconnect=True, and connect(calibrate=False). It immediately reads servo angles and syncs shadow. Missing joints/site raise ValueError; hardware connection exceptions propagate. It does not load calibration JSON directly—LeRobot's configuration/calibration mechanism is assumed. Max_step_deg=20 is stored but **never used** anywhere; no actual 20-degree cap is implemented.
- `_read_real_joint_angles_rad` (152) reads five `.pos` observation entries and converts degrees→radians. `_refresh_shadow_from_real` (158) writes those angles into shadow qpos and calls mj_forward. It does not synchronize the gripper or model-base/world transform. Thus pose accuracy depends on calibration/axis/zero convention matching the MJCF; code does not estimate that alignment.
- `get_joint_positions` (166) reads hardware directly. `get_ee_pose` (170) refreshes shadow and returns world-frame FK, **not externally measured physical EE pose**. `compute_jacobian` (209) obtains a site positional Jacobian and takes its first five columns; unlike package arm it does not resolve dof addresses, assuming arm joints lead model nv.
- `inverse_kinematics` (215) iterates shadow qpos writes/FK, error e, lambda=.1*(1+||e||) by default, solve `(J.T@J+lambda²I_5)dq=J.T e`, step multiplier min(1,.5/(||dq||+1e-6)), and individual MJCF joint clipping. No state restoration is needed for physical dynamics because it is a shadow, but the public shadow is left at its last candidate. On exhaustion returns q,False,last pre-update residual. Default 100 iterations, tol .005 m. No orientation solve, collision avoidance, multi-start or nullspace objective. `max_iter or default` and `tol or default` mean a zero override is ignored.
- `home` (177) reads current servo degrees once, interpolates to all-zero joint angles in 8 coarse waypoints (default .5 s sleeps), inserts each prospective waypoint into shadow FK, refuses if EE z<floor-.03 (default .07 m), sends arm angles plus gripper-open=100, sleeps, finally refreshes shadow and returns True without a measured home tolerance. Abort leaves already executed hardware motion and candidate shadow state; no rollback/stop/park is issued.
- `move_cartesian` (246) first rejects requested z outside [.10,.50] m; solves IK from current real q; **prints a miss but executes best effort even when IK returns False**. It checks final IK-predicted EE z≥floor-.02, then linearly interpolates the initial measured q→IK q across 8 waypoints, each checked at z≥floor-.03. It does not read actual q before every waypoint, check the continuous path or other link collision geometry, enforce target ceiling along the path, or use the stored max_step_deg. Sends degrees to hardware and sleeps max(duration/steps,.2). At completion it refreshes and then reads EE again, returning actual-joint-FK residual<2*ik_tol (default 1 cm). These checks are limited programmed z filters, not demonstrated comprehensive hardware safety.
- Gripper open/close (294/299) send 100/0 and sleep .8 s, return True. `is_holding` (304) reads measured gripper position<50. Its comment mentions current/load and commanded-versus-measured state, but **neither current nor commanded-state comparison is implemented**. Empty closed gripper can meet this heuristic.
- `step` (312) is a no-op compatibility method. `get_object_position` (316) explicitly raises NotImplementedError because no privileged object state is available; fallback vision is only suggested, not implemented here. `describe` (324) returns a small identity/name summary.
- `disconnect` (139) calls hardware disconnect, suppresses/logs errors, and may disable torque by config; it does not home/park/open first. Context exit (148) calls it. The four local smoke helpers (335/344/360/372) print positions/errors or execute a gripper cycle but assert nothing and ignore motion return failures. CLI main (381) selects them, defaults to hardware home_only and artifact MJCF. None was run in this review.

#### `real_robot/synth_real_driver.py`

This is a standalone one-shot generator, not an agent tool loop connecting live hardware. At import it creates the artifact output directory (31–33); main loads an artifact MJCF and trims from worldbody through end, loads calibration JSON (six servo records), composes literal API/context/safety strings, makes one AnthropicBedrock messages.create request, strips a code fence, writes driver.py and synthesis_meta.json. It never imports/calls the actual LeRobot/Vector hardware or RealSense devices, executes generated code, compiles it, or validates returned safety behavior.

The API block `lerobot_api` (54) actually instructs use of **vector_os_nano SO101Arm/SO101Gripper**, with radians and explicit encoder calibration, warns it differs from LeRobot and says not to mix. But the surrounding user prompt (180–229), surface comments (161), and implementation note (248–251) still demand LeRobot SO101Follower, degrees/calibrate=False, and send_action. These are conflicting calibration/actuation instructions in the very same prompt. Do not claim a single rigorously consistent translation contract from this script.

RealSense API text (97) contains RGB/depth setup, alignment and pixel/depth backprojection; mount context (125) admits unknown camera-to-EE extrinsics and says world EE pose is FK, with vision only for relative checks. There is **no implemented extrinsic estimation or camera observation here**. The actual demanded visual_self_check is a white-foreground-blob heuristic (253–255), not calibrated EE localization. Top docstring's closed-loop visual pose synthesis is a goal/prompt description, not evidence that the generated file achieved it.

Safety text (140) asks for a 20° per-step bound, live read before every action, z floor and open before disconnect. The system message (264) reiterates refusal and no mj_step; **all are instructions to the model, not enforcement in this script**. Main uses max_tokens=12000, temperature=0 retry only for deprecation error string (288–297), aggregates text blocks, estimates cost with hardcoded rates, and saves response metadata. Version/date/pricing comments are historical literals, not verified current facts. Output driver runtime dependencies are broader than numpy+mujoco (the prompt asks hardware SDK, RealSense and OpenCV); this path cannot substantiate the simulation export's two-library dependency claim.

### 10. Historical generated Franka driver and oracle diagnostic

#### Separately read artifact: `artifacts/auto_adapter_from_scratch_franka_artifacts/driver.py` (527 lines)

This is generated output, not the fixed package skeleton. It uses only standard library/numpy/mujoco at import, and reads `study.json` at construction. `Robot.__init__` (36) parses dof/names/EE/gripper/limits; hardcodes home posture `[0,0,0,-1.57079,0,1.57079,-.7853]`, max IK iterations=200,tol=.001,lambda_init=.01,gripper ctrls255/0,force threshold5. `build_from_mjcf` (110) resolves path, changes cwd temporarily for loading XML, creates MjData without home keyframe reset, loads study.json from caller's original cwd (not model directory), and constructs Robot. That caller-relative study dependency is part of actual artifact portability.

`_resolve_indices` (82) maps joint IDs/qpos addresses and actuator/EE IDs but never rejects mj_name2id=-1; a missing name can index -1. It does not map qvel/dof addresses. `compute_jacobian` (187) stacks body position+rotation Jacobians and slices the **first dof columns**; IK uses only the first three rows. `get_ee_pose` (169) forwards and returns the `hand` body origin world pose.

**Central state-mutation defect:** `set_joint_positions` (151) clips and writes both live data.qpos and matching data.ctrl. `ik_dls` (201, loop238–241) invokes it at every numerical candidate and never restores original state. Solve is `(J.T J + [.01*(1+||e||)]² I_7)dq=J.T e`; total dq norm clipped to .5, then joint clipping. Convergence returns True at 1 mm; exhaustion raises only if the last pre-update residual>.02 m, otherwise still returns True. No finite checks/collision/posture objective. Current live qpos is the last *evaluated* candidate, while returned q may include one further update on exhaustion.

`move_cartesian` (341) captures q_current, solves this destructive IK, and calls `move_to_joint_positions` (307). That latter method obtains q_start **after** IK changed live qpos. On normal convergence it is already at the solution; the intended start→goal motion has been replaced by kinematic state assignment followed by simulated holding/settling. `move_to_joint_positions` linearly commands q_start→target over floor(duration/dt) steps using **direct mj_step**, returns True unconditionally; if duration gives zero ticks it still returns True. No per-step call through robot.step for instrumentation.

Home (298) uses that dynamic joint routine but does not open the gripper. Gripper open/close (383/399) directly step with constant255/0 for supplied duration and always return True. `is_holding` (415) checks any gripper actuator |force|>5, with no commanded-close test despite its docstring, no candidate object, and no sustained retention check. Unlike package real_contact it actually reads an actuator force, but actuator load alone is not object grasp truth. `step` (436) simply steps; `render` (446) creates a fresh renderer every call and never closes it explicitly. `describe` (471) exposes current FK/state/heuristic holding and IK settings. `test_basic` (507) builds, homes and prints but asserts nothing.

The supporting study JSON confirms seven arm joints/actuators, EE body hand, gripper actuator8 and position hint. The supporting Panda XML confirms high-gain affine position controls, gripper tendon, home keyframe, and a free data default different from that keyframe. No exported-driver dynamic result is newly claimed here.

#### `experiments/rebuttal_franka_ik/run.py`

`load_robot` (47) imports this generated file freshly per target, changes cwd to its artifact workspace (satisfying caller-relative study loading), and constructs from mjcf.xml with **no home()**. `REACH_VARIANTS` (35) is ±5cm each world axis plus (+.035,+.035,0), target=fresh EE+offset, success tolerance .02 m. Script uses oracle target coordinates, no model calls.

`make_nullspace_ik` (61) replaces only the robot method at the interface, but changes multiple internal mechanisms together: fixed lambda=.001, default500 iterations (stock200), damped pseudoinverse `Jplus=solve(J.T J+lambda²I,J.T)`, task step `Jplus e`, and approximate damped nullspace posture correction `(I-Jplus J)*.05*(home_q-q)`; total update norm capped .5 and same joint clipping. Because Jplus is damped, I-Jplus J is an approximate projector, not an exact nullspace projector. It preserves the same live qpos+ctrl candidate mutations. Default tol uses robot.ik_tol, despite docstring wording “tighter tol default”; the function does not itself tighten it. Exhaustion accepts if last residual≤2 cm and returns True.

`exec_gravcomp` (108) computes q0 from the **already IK-mutated robot**, linearly sets joint controls, writes data.qfrc_applied[:dof]=data.qfrc_bias[:dof] and directly mj_steps every tick. This is a dynamics-execution modification rather than a new inverse-kinematics algorithm; the indexing assumes first dofs are the arm. Applied generalized forces are left in data at exit. No independent save/restore fixes the initial IK teleport in this arm either.

`run_arm` (120) loops fresh robot per variant and applies stock/nullspace/stock5x/gravcomp intervention; stock5x multiplies ik_max_iter by5. Non-gravity arms use robot.move_cartesian. Exceptions are caught, final EE still measured, but any exception forces failure even if final error small. Successful outcome requires no exception **and measured final world error<.02** (146), not method-return bool. `main` (157) actually runs four arms, although top docstring lists three, writes result.json and prints totals. Metadata says deterministic1run=N, stock/stock5x bit-identical, but this script does not execute repeats or assert equality of their per-variant numbers. Those words are not a determinism proof.

The oracle removes LLM target-selection variation for this driver/scene. It does **not** by itself exonerate driver synthesis, since the generated driver's state mutation is part of the tested chain. Nullspace arm changes damping/budget/posture jointly, so its comparison cannot isolate posture bias alone. This is useful technical context for separating kinematic convergence, physical tracking and generated-code defects in the thesis, but these archived runs must not enter a future formal denominator.

### 11. What the assigned tests actually establish

These are source interpretations; no test was executed by this reviewer.

- `test_arm_so101_smoke.py:main` (18): hand-constructs the mapping/spec; asserts finite initial FK; solves one nearby (+.02,+.02,+.05) IK round trip with <2cm; solves banana+8cm round trip <3cm; dynamically moves and **only prints** measured reach error; closes gripper and **only prints** bool/held state; then moves to banana+20cm and asserts actual banana z rises>5cm. This is the strongest direct behavioral assertion, but the scene is weld-assisted and has no finger collision. It is a developer-script main, not a pytest test function; ordinary test collection would not execute its main sequence.
- `test_quadruped_go2_smoke.py:main` (27): hand spec kp30/kd1.5/f2Hz; calls stand_up(.3) eight times (each also includes .5s hold), asserts final body height>.20 m; calls walk_forward(.15) twenty times (each resets phase; first also calibrates), prints success/warning from aggregate +X displacement but **does not assert walking**; calls sit and prints height without assertion. Renders frames after each coarse segment and writes an MP4. Its final “[ALL OK]” message can appear despite no forward walking or failed sit. It does not validate a whole height trajectory despite docstring. Also a script main.
- `test_real_contact_grasp.py:_make_scene_file` (68) writes a scene file into the Franka asset directory; this is why blindly running it is not read-only. `_build_franka_spec` (76) selects EE body hand, actuator8 tendon, explicit test_cube and backend. `run_scenario` (100) homes, approaches cube or moves **.5m in Y** (docstring says20cm), closes, optionally lifts10cm, reads cached holding flags and actual cube z. `main` (149) compares `contact` and `real_contact`, **not weld** as header says. It catches/prints all exceptions, has no assertions, no deliberate slipping-object intervention and no live slip detection. The printed interpretation claiming no slide-out during lift is unsupported by backend code. Thus it does not establish its own stated “fake grasp fixed” goal.
- `test_hand_fingertip_dls.py`: `_spec` (79) binds canonical16 LEAP joints/actuators, four named tip geoms, explicit all-zero home. `_target_positions` (96) creates a **separate MjData** sharing model, sets a fixed modestly bent target posture, forwards and reads world tip goals. This is legitimate fixture target construction, distinct from writing the controlled data.qpos. Main test (118) homes, commands2s online DLS, asserts controller reports success, actual q moved, every tip moved>1cm, stacked residual<.00894427191, then home1s returns true and joints within.03rad; checks description. Since success also requires each tip≤.003m, that max-tip criterion is stronger than the aggregate bound. Missing-finger test (157) asserts ValueError. Unreachable-goal test (168) asserts [3,3,3] for all tips returns False after .02s actual simulated time. These are meaningful local physics/control checks, not model-generated capability design or task evaluation.
- `test_stretch_mobile_manipulation.py:test_stretch_base_then_fixed_world_reach` (22): canonical scene, home1s, drive .10m at motor cap .10, asserts actual projected/horizontal displacement>.05 and base final XY within4cm of fixed(-.09,0). Then reach fixed world(-.02,-.24,.67), permit rotation but require base translation during reach<1cm; returned and independently reread EE errors<3cm; qpos/qvel finite and uprightRzz>.95. It tests one canonical base+arm goal, not universal reachability, object manipulation or grip retention.
- `test_bimanual_serial_dls.py`: hardcoded6-joint ArmSpec per side, all home `[0,-.96,1.16,0,-.30,0]`, position-only IK100iters,tol.0001, gripper contact backend. Main (85) asserts exact model/data identity, dof12, home controls, initial ncon=0; homes dynamically, commands left then right to **fixed precomputed world targets**, holds.5s, asserts simulation time increase and retained ctrl. `_task_gate_passes` (65) requires each arm's q movement norm>.03 and each EE error<3cm; finite state. Tests closing left changes only left gripper ctrl, and rejects arm='both'. One-arm-only test (132) asserts this combined gate fails, protecting against accepting a one-arm partial result. Overlapping-bindings test (144) asserts construction ValueError. These checks establish shared-world coordination under this fixture, not simultaneous Cartesian cooperative control or collision-free arbitrary paths.

### 12. Claim boundary checklist for the parent thesis review

Supported by reviewed source:

- A robot-specific mapping can bind an agent-supplied serial/leg spec to MuJoCo joints, generalized-coordinate addresses, actuator indices and EE references.
- Fixed controller skeletons contain substantial numerical/controller logic; in skeleton-assisted mode those mathematics are supplied by the framework rather than newly invented each generation.
- Serial-arm package IK is world-position-only DLS with residual-dependent damping, vector-norm step clipping and joint-limit clipping; actuation then steps MuJoCo through interpolated position targets.
- The quadruped template is hand-tuned periodic joint targets with joint-space torque PD, a limited sign-probe heuristic and simple final displacement/height returns.
- Different grasp backends enact different simulation assumptions, and generated/driver bool values do not share a uniform physical success meaning.
- Current bimanual implementation shares one model/data; LEAP repeatedly recomputes real-state DLS; Stretch is a constrained canonical geometry/feedback controller. Each has explicit limits described above.

Contradicted or unproven by reviewed source:

- “All motion methods return true only after the requested capability physically passes.” False for arm, bimanual hold/home and many gripper/home routines.
- “RealContact checks sustained contact forces and detects slip on every is_holding call.” False; one post-settle contact proxy and cached state.
- “Arm limits are cross-checked against MJCF” or “adaptive damping detects singularity/joint-limit chatter.” Comments overstate execution.
- “Cartesian movement follows a straight Cartesian line / controls orientation.” Neither serial package nor generated artifact does so.
- “Go2 accepts closed-loop body-frame cmd_vel and performs balance/MPC.” Not implemented; speed is amplitude scaling and success is world Δx.
- “Calibration restores full simulation state.” It omits simulation time and other state.
- “All generated paths preserve simulator state during kinematic search.” Historical Franka output permanently writes qpos and ctrl.
- “A clean bool/test script ALL OK establishes physically authentic success.” Multiple scripts print success without asserting their advertised behavior.
- “Bimanual neutral initialization is dynamic and universally collision-free.” Construction writes qpos; absence of contact checked only for the chosen ALOHA fixture.
- “Stretch physics_steps is total invocation cost.” It excludes its entire initial yaw subroutine.
- “Real hardware uses exactly the same implementation / successful vision calibration.” real_so101 has separately implemented shadow FK+hardware commands; synth script is a conflicting one-shot prompt with no execution check.
- “Two-library self-contained export implies equivalent physical hardware portability.” Reviewed hardware scripts require SDK/camera dependencies and missing calibration/extrinsic guarantees.

Remaining uncertainties are narrow and explicit: this memo does not inspect the parent-owned agent/orchestrator/runtime/harness/export implementations, so enforcement outside these controllers must be checked there; no claim about whether they detect the defects above is made. No environment-specific enum behavior, contact outcome, gait stability, hardware calibration, latency, physical safety, or historical result is newly reproduced. The reviewer has fully read the assigned source and listed supporting inputs; third-party engine/SDK internals and mesh geometry files were not read. ALOHA supporting XML was excerpt-read only. Historical implementation observations explain design boundaries and do not authorize new experiment work or rewrite the approved thesis framing.
## D. Task execution and every evaluator branch

## AA1 evaluation and downstream execution: complete assigned-source review

### Scope and provenance

This is a static source-comprehension memo, not run evidence, a new protocol, or an assertion that the code is correct. No model calls, simulations, experiments, or project edits were performed. Only this memo and its companion `evaluation_coverage.json` were written by this reviewer.

The final reference is the parent's stable snapshot at `/private/tmp/aa1-thesis-source-review-20260909/source_snapshot`, captured at **2026-09-09 16:24:43.421233 UTC**. Its Git HEAD at start and finish was `ff486bf3499c14d105939ac43350b0f00baf206f`, but it contains dirty working-tree code. It must therefore be described as that working-tree snapshot, not simply the committed `ff486bf` implementation. All line numbers below refer to files inside that snapshot. The original live checkout had initially been read at `08daf93`; live `eval.py` changed during reading, so the final review switched to the stable snapshot and read the changed blocks in full. The assigned files other than `eval.py` match `ff486bf`; `physics.py` was newly present relative to `08daf93`. The metadata lists `eval.py` as modified. Changes outside this assigned scope remain the responsibility of other reviewers.

All 17 assigned Python files, including two empty `__init__.py` files, were fully read. All six available `autoadapter_bench/spec/*.yaml` files were also read in full. Function bodies, prompts, registries, constants, CLI paths, and score branches were included. There are **no unreviewed functions within these assigned Python files**. External dependencies and modules outside the assigned scope were not recursively audited; specifically, the correctness of individual skeleton controllers, ReactLoop/provider internals, the generation orchestrators, MJCF assets, and published result JSONs is not established by this memo.

### 1. What the downstream route actually is

`auto_adapter/agent/task_planner.py` is a natural-language **tool-using task controller** over an already available driver. It does not synthesise the driver, derive capabilities from a task library, generate pass criteria, implement ReCAP, or itself issue a physical task verdict. Its concrete engine is `ReactLoop` (`task_planner.py:748–757`). The module docstring calls the tools MCP-style, but local execution directly calls Python driver methods; no MCP server/client or transport is exercised (`:2–24`, `:624–655`).

The canonical benchmark `eval.py` normally constructs **three separate driver instances per trial**: a before-state instance, the live planner instance, and a replay instance (`eval.py:1432–1480`, `task_planner.py:714–729`, `eval.py:1502–1513`). Each instance is created by the same candidate module. Loading prefers module-level `build()` and otherwise calls `Robot.build_from_mjcf('mjcf.xml')` (`task_planner.py:520–552`). It removes the module cache entry named `driver`, temporarily changes cwd to the workspace, adds workspace/repository import paths, and executes the module in the evaluator's Python process. This is a fresh driver/model construction convention, **not process isolation or a protected measurement boundary**. Best-effort `home()` calls can raise and be ignored. No equality check establishes that the three instances have identical initial state, assets, or random seed. The deterministic-world wording is a prompt assumption (`task_planner.py:823–847`).

The planner's live run collects API calls and a video; `eval.py` then replays logged API calls on its own fresh instance and applies a YAML-selected scoring function. Thus a replay score and the live video come from different executions. No replay-equivalence metric proves that they match. No video completeness requirement gates the canonical benchmark verdict.

### 2. Tool API construction, argument conversion, and dispatch

#### 2.1 The public tool set is mainly hand-curated

`task_planner.py:73–323` contains five manually authored dictionaries. Two exact class names are registered directly (`:326–329`): `ArmSerialDLSSkeleton` and `QuadrupedPDGaitSkeleton`. For other classes, the planner tests method presence in priority order arm, quadruped, wheeled, aerial, humanoid (`:572–587`). A mobile manipulator satisfying the arm predicate therefore gets only the arm registry; a bimanual robot's side-specific ABI is not inferred. There is no manifest-derived arbitrary capability schema in this file.

| Family | Exposed operations and input conversion |
|---|---|
| Arm | `home(duration=2.0)`; `move_cartesian(x,y,z,duration=2.0)` converts three scalar inputs into a float64 3-vector; `gripper_open`, `gripper_close`, `get_ee_pose`, `is_holding`, `get_joint_positions` take no arguments; `get_object_position(body_name)` coerces its name to string. |
| Quadruped | `stand_up(duration=2.0)`, `sit(duration=2.0)`, `walk_forward(secs=2.0,speed=0.2)` coerce numbers to float; `get_body_height`, `get_base_pose` take no arguments. |
| Wheeled | `drive_forward(distance_m=0.3,speed=1.0)`; `turn(angle_rad,speed=1.0)` accepts an `angle` fallback although the schema requires `angle_rad`; no-arg `get_base_pose`, `get_base_yaw`. |
| Aerial | `takeoff(height=0.5)`; `move_to(x,y,z,tol=0.1)` uses three scalar positional arguments; `hover(secs=2.0)`, `land()`, `get_base_pose()`. |
| Humanoid | `stand_balance(secs=3.0)`, `squat(depth=0.15,secs=3.0)`, `get_torso_height()`, `get_base_pose()`. |

Only registry methods actually present and callable are exposed (`:602–610`). If no family matches, every public callable except `close`, `step`, `render`, and `settle` becomes an introspected **no-argument tool**; its signature appears in the description, but the handler always invokes `method()` and publishes an empty-object schema (`:589–600`, `:658–691`). This cannot generally support parameterised unknown capabilities. Ordinary JSON schemas here specify types/required keys, not physical limits, finite-value checks, or robot-specific safety bounds. Family hints are attached only for the two exact registered class names (`:739–745`, `:851–866`).

#### 2.2 The wrapper observes execution completion, not motion success

For curated tools, input conversion occurs before the `try` block (`:624–628`). A missing x/y/z key or failed float conversion can therefore fail without a corresponding entry in `call_log`. Inside the try, the driver is invoked, an end-of-call frame is captured, and `{tool,input,dur_ms,ok:True}` is appended (`:629–640`). A driver returning `False` is still logged `ok:True`: that field means no exception. Exceptions are logged with their type/message and re-raised. The return value goes through `_to_jsonable`, which recursively converts NumPy arrays, scalar numbers, booleans, tuples/lists, and dicts (`:874–888`). Unsupported objects are returned unchanged. Descriptions promising a position/rotation dict do not transform a driver's tuple into that dict; a tuple becomes a JSON array.

The original input dict is logged, without result value or a deep copy. Return conversion happens after the successful log append; if conversion itself failed, the except block could append a second failure record. The same live object supplies observations and control methods. There is no evaluator-provided independent observation stream in this planner.

#### 2.3 Planner result and failure behaviour

`execute_task` always reloads the driver; `reset_world=False` raises (`:714–717`). Default TaskPlanner caps are 25 model iterations and 6,000 tokens per turn; canonical `eval.py` uses 50 iterations unless `AUTOADAPTER_MAX_ITERS` overrides it (`eval.py:1657–1667`). These are planning budgets, not a simulation-step, tool-call, or full-trial time bound.

The video patch is uninstalled in a `finally` around `loop.run`. `duration_sec` measures that call only; it excludes construction/home and video encoding (`task_planner.py:759–783`). `TaskResult.ok` is `ReactResult.ok OR (max_iters error AND at least one logged call AND no logged call exception)` (`:791–800`). It can be true after a normal model completion even if some earlier tools failed; it can be true when the iteration budget ends. This is not a parser of the final sentence and not a behavioural judgement. The benchmark label `llm_ok` and leaderboard wording “LLM-self-report” are consequently loose descriptions of an execution status. Traces retain the ReactLoop conversation externally, while returned `TaskResult` retains the full call log, token usage, summary, error, video and trace paths (`:802–815`).

### 3. Recording, simulator state, and mutation boundaries

#### 3.1 TaskPlanner video

`_FrameCapture` first seeks `_model`/`model` and `_data`/`data`, creates a 480×360 MuJoCo renderer, frames the model from its statistics, and follows qpos[0:3] only when the first joint is free (`task_planner.py:351–391`). It globally monkey-patches `mujoco.mj_step`; the wrapper forwards a possibly batched `nstep` call and increases its counter **once per Python invocation**, not once per simulated substep (`:394–401`). Every fourth invocation is normally sampled, with a 3,000-frame cap. It does not filter model/data identity before counting; it renders the originally bound robot. The fallback monkey-patches `skel.step(n)`, explicitly steps one at a time, and calls candidate `render()` (`:406–420`). Rendering failures are swallowed. End-of-tool snapshots and the initial snapshot add frames independently of simulation elapsed time (`:434–437`, `:731–733`).

Encoding uses H.264, fixed 30 fps, yuv420p and faststart (`:768–779`). Consequently video seconds are not simulation seconds. A 3,000-frame recording need not cover the whole trial; no cut-off/completeness flag is retained. Even `capture_video=False` still constructs capture and gathers frames; it disables saving only. Recorder construction can fall back to `skel.step` that a driver does not expose and raise. There is no explicit final driver close/dispose in the planner.

#### 3.2 The newer `physics.py` observer

This 95-line module is fully read. `find_mujoco` searches candidate instance attributes by actual `MjModel`/`MjData` type and requires both (`physics.py:7–16`). `sample_state` refreshes kinematics on those live objects, uses externally supplied named bindings, and records simulation time and finite qpos/qvel/ctrl. Depending on `state_refs`, it reads base position/upright/yaw, EE body/site position, both EE sites, four fingertip geom positions, and named arm joint qpos (`:19–58`). It does not save complete qpos/qvel/ctrl arrays or contact data. `ee_site` overrides `ee_body` when both are supplied. “finite” covers state/control arrays; individual sampled entity values receive no separate finite audit there.

`PhysicsTrace` records an initial sample and patches `mj_step` only for the exact model/data identities captured at construction; batched nstep is expanded and each real step is sampled with the active tool/index labels (`:61–95`). Calls on different model/data are forwarded without sampling. The patch is restored on context exit. This is stronger measurement access than driver getters, but it is still global monkey-patching inside the same process, using model/data discovered on the candidate. It does not prohibit candidate state writes, protect observer code from candidate imports, or certify actuator-mediated motion. `sample_state` itself calls `mj_kinematics`, a derived-state update rather than a physical step.

**At the stable snapshot, the canonical `run_task` and `eval_baseline.run_one_trial` still call `_replay_tool_calls` with only its original three arguments. They do not pass `state_refs` or retain `_physics_samples`; before-state capture likewise does not populate `fingertips`. The new observer is therefore not connected to those CLI evaluation paths.** New score functions requiring those samples fail for absent evidence when invoked through that path. Zoo entries and helper availability do not establish executable integration.

### 4. Fresh-instance replay

The snapshot's dirty `_replay_tool_calls` is at `eval.py:245–407`. It preserves the old Boolean return and adds optional `state_refs` and `trace_samples`. It optionally constructs PhysicsTrace; initialization failure sets `all_clean=False` but permits replay without the observer. Exceptions from context entry/initial snapshot are outside that constructor catch and can propagate.

Dispatch now handles Cartesian vector or x/y/z targets (optionally arm-labelled), four fingertip target aliases, joint targets, body-name aliases, quadruped, wheeled, aerial, humanoid, and bimanual `hold` requests (`:279–350`). The fallback inspects a method signature, passes input keys matching parameters, and retries with no arguments after TypeError/ValueError (`:357–364`). Because the try includes the actual method call, an internal TypeError/ValueError after side effects could trigger a second invocation. Home/stand/sit similarly retry without duration after TypeError. Defaults are not perfectly identical to live wrappers: replay without duration may use the driver's own default, whereas the live wrapper explicitly sends 2.0.

Missing/non-callable tools now set replay failure, while malformed non-dict input becomes `{}`. Boolean False and Boolean-valued dict fields `ok`, `success`, or `reached` set replay failure (`:295–302`, `:378–399`). Exceptions do so too; replay continues after each failure. This differs from the live log's no-exception `ok` semantics. A per-call snapshot still uses **driver getters**, even when a PhysicsTrace is active (`:261–277`). It records height, base xyz, and EE xyz individually, replacing unavailable readings with None.

Canonical `run_task` appends `REPLAY DIVERGED` and a metric flag when `all_clean` is false, but **does not force `physics_ok=False`** (`eval.py:1519–1521`). The original grader can pass after a failed return/exception/missing operation. The newer liveness grader checks `_replay_clean`, but the canonical caller does not populate it, so its default remains true. Call logs are open-loop replayed: model decisions are not recomputed from replay observations, and generated CaP branches are not re-run. Replay success is not proof that the video/live execution had the same trajectory.

### 5. Complete scoring-family inventory

The following covers every dispatch family in `evaluate_success` (`eval.py:410–1415`), including aliases. Unless stated otherwise, positions/heights/orientations come from candidate getters. `_obj_pos` prefers `get_object_position` and only falls back to named MuJoCo body/geom/site state if that method is absent (`:218–242`); errors from an existing getter do not trigger the fallback. Most checks use strict `<` tolerances. A `physics_ok` field includes non-physical reporting and tool-coverage checks; the field name is not an evidence taxonomy.

| Type and starting line | Exact decision, observations, and material limits |
|---|---|
| `ee_pose_returned_home`, 418 | Final EE error below tolerance **and** peak excursion from initial home at least `min_excursion_m` (default 0.01 m). Prefer finite EE `_physics_samples`, fall back to per-call `_phase_snapshots`; final getter preferred, trace final used only if getter raises. Added excursion rejects complete inaction, but does not establish +X/+Z direction, a 5 cm move, a square, or a dwell. |
| `tool_executed_without_crash`, 445 | Required tool names must occur in call log; `_replay_clean` must be true, default true if absent. Does not itself inspect each log entry's `ok`. Canonical caller does not fill the new replay flag. |
| `fingertip_targets_reached` / `four_fingertips_reached`, 457 | Exactly index/middle/ring/thumb targets required. Uses initial fingertips plus final fingertip PhysicsTrace sample; every goal/start/final vector must be finite 3D. Every finger error <= per-finger tolerance (default 0.00894427191 m), aggregate concatenated 12D error <= aggregate tolerance (same default), every final displacement >= minimum (default 0.01 m). Missing truth fails. End displacement rather than peak excursion is required; no hold period. |
| `mobile_manipulation_ordered` / `base_then_arm_reach` / `stretch_base_then_ee`, 505 | Needs >=2 finite PhysicsTrace samples, each with base, EE, arm qpos. Finds first arm-joint-norm motion >= threshold (default 0.01); base must be within 0.025 m of target then, and peak base translation >=0.05 m. Final base within tolerance, arm moved, final EE within default 0.025 m. Filters invalid samples rather than rejecting whole trace; no binding of 'peak base motion' to a time before arm motion beyond the target-at-first-arm condition. |
| `bimanual_reach_hold` / `bimanual_synchronized_hold`, 553 | Needs left/right targets and finite-labelled PhysicsTrace samples with both EEs. Both final errors <= default 0.025 m; each EE peak motion >= default 0.01 m. Longest interval of sampled simultaneous target occupancy >= default 0.5 simulation seconds. It filters invalid samples, which can omit gaps; there is no separate global continuous-trace completeness test. |
| `llm_reports_failure`, 606 | Lowercases model summary and matches any configured substring. It verifies a report token, not an unreachable command, home return, or actual failed motion. |
| `object_lifted_by`, 613 | At least one accepted object's final z gain is strictly greater than tolerance and its final EE gap <= `held_tolerance_m` (default 0.12 m). Optional y-argmax object chosen from before-state must be among these. Exceptions on individual objects are skipped. If EE getter fails, EE=None disables the distance condition (gap treated as zero). Proximity is not a contact/release/grasp measurement. |
| `ee_at_midpoint`, 665 | Mean initial positions of configured objects, plus 0.05 m in z; final EE distance < tolerance. No observed path required. |
| `ee_waypoint_trace`, 675 | Ordered home-relative waypoints matched to per-call EE snapshots with a monotonically advancing cursor (default 0.025 m); final home error < default 0.04 m. Samples only call boundaries, not the path within one call. |
| `object_above_object`, 707 | Top-minus-bottom z strictly between default 0.005 and 0.15 m; XY distance < supplied tolerance. Does not consume YAML `settle_steps`, inspect release/contact, or prove the upper object rests on the lower. |
| `object_close_to_object_xy`, 726 | XY distance < tolerance and absolute z gap < default 0.10 m. Does not consume `settle_steps` or require a movement from start, actual containment, or release. |
| `object_did_not_move`, 739 | Named object's final 3D displacement < limit and EE final absolute target error < tolerance. No path collision test. |
| `objects_swapped`, 753 | Both final XY positions near the other's initial XY. No height, release, sequence, or contact constraint. |
| `object_placed_near_target`, 766 | Optional `skel.step(1)` settle loop; final XY target error < tolerance and final rise from initial z < default 0.08 m. No release check or lower z bound. |
| `objects_in_tower`, 782 | Optional settle; every adjacent stack pair XY-aligned and z gap between default 0.01 and 0.15 m. Empty/singleton stacks have no adjacent failing pair. No support/contact/release test. |
| `two_swaps_completed`, 806 | Optional settle; all four final XY errors relative to original partner positions < tolerance. No order/release/height condition. |
| `cleared_and_visited`, 835 | Optional settle; all designated stay objects move less than max displacement and final EE near cleared object's **original** xyz plus default 0.05 z. Does **not** verify that the cleared object was moved/parked. Final EE criterion conflicts with an unconditional final 'return home' instruction unless home happens to satisfy it. |
| `object_pose_match`, 865 | Optional settle; final absolute XY error < limit and final z rise < default 0.02 m. End-state test cannot establish that no lifting happened earlier. |
| `object_pose_match_relative`, 885 | As above, target = object's initial XY + delta; additionally final rise > -0.05 m. |
| `push_relative_obstacle_safe`, 905 | Relative push target + final rise band (-0.05, default +0.02 m) + obstacle final 3D displacement below bound. No observation of mid-trajectory collision/lift. |
| `all_objects_translated`, 930 | Optional settle; every named object's final XY near start+delta, final rise below default 0.03 and above -0.05 m. Empty object lists vacuously pass. |
| `ee_returned_no_object_disturbance`, 950 | EE final home error and largest tracked object's final displacement below bounds. Per-object read exceptions are swallowed, so absent observations need not fail the 'no disturbance' part. |
| `object_slid_not_lifted`, 975 | Separate implementation equivalent to absolute object_pose_match: optional settle, XY target, final-rise upper bound. No trajectory-wide no-lift condition. |
| `push_to_target_obstacle_safe`, 992 | Absolute push target, final-rise upper bound, obstacle final displacement bound; no lower-height bound. |
| `all_objects_in_halfspace`, 1015 | Optional settle; every object's selected coordinate greater/less than threshold, final z rise below default 0.03 m. Any comparison value other than literal `greater` takes the less-than branch. |
| `ee_returned_obstacle_safe`, 1037 | Final EE near initial home and obstacle final displacement below bound. No required excursion or path safety evidence. |
| `body_height_above`, 1053; `body_height_below`, 1058 | Final driver body-height reading greater/less than threshold; no duration or actual step requirement. |
| `forward_displacement_at_least`, 1063 | Final base x gain >= min_m; optional must_not_fall only requires final base z >0.10 m. |
| `ee_visited_n_positions`, 1072 | If per-call EE trajectory and initial object coordinates exist, >=n distinct objects have any EE XY snapshot within tolerance (default 0.06 m). Ignores z, ordering, holding status and home return. If either data source absent, falls back to count of distinct commanded XY targets rounded to centimetres, explicitly labelled in detail. |
| `multi_phase_height_trajectory`, 1117 | Counts stand_up and sit names against required phase counts; neither ordering nor heights measured. |
| `llm_reports_pose`, 1131 | Summary substring match, no numerical consistency check. |
| `body_height_ratio`, 1138 | Final/initial body height constrained by optional min/max ratio; if initial <=1e-6, ratio becomes zero. |
| `forward_progress`, 1149 | Final +X progress >=min, optional max, optional absolute Y drift, optional minimum height ratio. If no valid positive initial body height, height ratio defaults to one. No duration, command-count or intermediate upright constraint. |
| `turn_to_heading`, 1166 | Wrapped final-minus-initial yaw error relative to signed target < default 0.26 rad. Uses getter yaw if present, otherwise atan2 on getter rotation; initial yaw defaults zero. |
| `drive_and_turn`, 1184 | Maximum per-call base XY excursion >= min_drive and absolute final yaw change >= min_turn. Does not establish drive-before-turn order or turn sign. |
| `phase_trajectory`, 1210 | Resolves final, after:tool:k, before:tool:k to snapshots; computes height ratio, +X gain and optional retained-progress ratio. Missing required occurrence fails; unavailable height/xyz inside an existing snapshot defaults to initial values. 'Before first call' uses initial state. Requested bounds applied in declared phase order; no actual elapsed hold time measured. |
| `takeoff_to_height`, 1277 | Final absolute altitude >= min_altitude, else relative gain >= legacy min_climb; final upright >= default 0.7. |
| `reach_3d_target`, 1293 | Final base xyz error < tolerance and final upright; target absolute or initial+offset. |
| `returned_to_start_xy`, 1302 | Final XY error < tolerance, peak per-call XY excursion >= default 0.25 m, final upright. No continuous upright constraint. |
| `hover_stability`, 1321 | Final target error, maximum distance of **per-call** xyz snapshots to target, final upright. No minimum hover duration; within-call drift can be missed; if no snapshots, max drift defaults to final error. |
| `waypoint_3d_trace`, 1336 | Ordered absolute/initial-relative 3D waypoints matched to per-call base snapshots, default tolerance 0.2 m; final upright. No return requirement unless encoded as a waypoint. |
| `torso_upright_height`, 1385 | Final torso height >= bound and final upright. Getter torso height accepted only within [base z-0.05, base z+0.5], otherwise base z retained; base pose itself is a driver reading. No duration. |
| `stayed_in_place`, 1390 | Final XY drift < bound plus final torso height/upright. Does not constrain earlier drift. |
| `squat_and_recover`, 1399 | At least two per-call base xyz samples; sampled max-min z >= min_drop, final z >= max minus recovery tolerance, final upright. Does not see a squat that lowers and recovers inside one call, nor verify two squats or a sustained hold. |

Unknown success types raise ValueError, which ordinary canonical grading catches and converts to physics failure. There is no universal requirement for nonempty command log, minimum physical steps, finite trajectory, video, independent observer, release, temporal stability, or actuator-only mutation across all legacy scorers. Some operations may settle during grading via candidate `skel.step`; that modifies the replay world **after** the logged policy sequence.

### 6. Canonical execution, persistence, aggregation, and costs

`eval.py:1423–1540` captures initial arm/base/yaw/body state opportunistically, swallowing absent/failed getters. It scans a fixed eleven-name list for objects (banana, mug, bottle, screwdriver, duck, lego, tee, obs, cube_red/green/blue); arbitrary new task objects are not discovered from YAML. The `robot_dict` argument is unused. Before-instance and replay-instance construction errors are outside the try for live `execute_task` and can abort a task/run. A live exception becomes a trial with both statuses false, agreement true, zero calls/frames/duration and empty tokens. This preserves a failure in the denominator but can erase resource consumption and partial motion.

After successful return, grading catches its own exceptions. Trial output records statuses, agreement, call count, frame count, duration, tokens, first 300 summary characters, error/detail/metrics. It does **not** persist the returned `mp4_path`, `trace_path` or full call log into this trial JSON, despite the header's example containing mp4_path. Those artefacts remain separately in workspace directories.

`main` (`:1548–1730`) reads robot zoo/class and task YAML, locates a pre-existing driver directory by fixed priority, and rewrites `workspace/mjcf.xml` as a symlink if needed. CLI workspace validation accepts `driver_from_scratch.py`, but TaskPlanner itself requires `driver.py`, unlike CaP. Only task prompt is sent to TaskPlanner; the success spec stays in evaluator-side YAML, but both run in the same process and workspace access is not isolated by this route. Git SHA records commit identity, not dirty-state identity.

Resume trusts an existing result without checking model/robot/spec/budget consistency. It skips a task when trial count >= recorded n_trials (default zero if absent); a partial task is removed and rerun from the start. Checkpoints occur **after each complete task**, not each trial. Interrupted trials within that task may therefore be lost. Unknown suite names are printed and skipped. Trial count is CLI override `or` YAML `success.n_trials`, so zero does not override and negative values are not explicitly rejected here. No seed policy is implemented in these files.

`_build_aggregate` (`:167–201`) recomputes from persisted trial rows at each checkpoint. It counts only literal True or string `True`, protecting against the string `False` truthiness issue at this layer. The denominator is all stored trials; task count includes task records. It reports pooled trial-level physics/LLM/agreement rates, token sums and a model-substring price estimate. It does not merge different evidence levels or preserve a planned denominator for absent tasks. Crash/timeout zero-token fallback understates actual cost if calls already occurred. Token-cost mapping is a static list with a Sonnet-priced fallback, not a live bill or verified current price. Cost excludes synthesis, tool execution, simulator compute, unreported provider retries/cache terms, and other infrastructure charges.

Task-level rates divide by requested n_trials; aggregate rates divide by actual persisted trial counts. Run wall time begins after configuration/resume preparation, includes subsequent evaluation/encoding, and on resume records only the current invocation's elapsed time even though trial/token totals include previous invocations. The old grand token counters are retallied but final aggregate is independently recomputed. `llm_ok == physics_ok` is agreement between execution status and grader, not a calibrated measure of honesty.

### 7. Code-as-Policies baseline: actual comparison

`autoadapter_bench/baselines/code_as_policies/__init__.py` only exports CaPPlanner and CaPResult. `runner.py` is a local adaptation inspired by Code as Policies, not an execution of the original project's full system. Its introductory claims of a 'strong'/'fair' baseline are author interpretation, not facts established by this source. Both planners use a pre-existing robot driver here. The downstream comparison therefore isolates aspects of task-policy generation/execution, not the cost of synthesising the interface on one side only.

#### 7.1 Program generation and API

CaP performs one provider call per task, extracts the first fenced Python block (generic fence fallback, else whole response), and executes it once (`runner.py:398–406,497–637`). Its program can contain loops, conditions, repeated observations and try/except; **single-shot LLM generation does not imply open-loop robot control**. There is no LLM repair after a program exception. `_CAP_SYSTEM` documents the same broad families, meters/world coordinates, and a grasp recipe, and asks for no print/imports beyond preloaded numpy/math/time (`:70–149`). These are prompts, not enforcement.

`_build_sandbox` exposes the entire Python `__builtins__`, NumPy, math, and time (`:260–268`); execution is ordinary `exec(compile(...), sandbox)` in the current process (`:583–593`). This is not a security sandbox, import boundary, file boundary, or physics-mutation restriction. An arbitrary Python program can obtain resources beyond the documented primitives. The code does not execute such access during this review; this is a direct property of the supplied globals.

For arm drivers, the public `move_to(xyz,duration)` alias validates a 3-vector and forwards to `skel.move_cartesian`; log records canonical `move_cartesian` so replay works (`:370–383`). Aerial `move_to` is the native scalar x/y/z method. Available primitives are the union of manually listed arm/quad/wheeled/aerial/humanoid names that the driver has (`:356–390`), unlike TaskPlanner's one-family priority selection. Availability uses hasattr, not a callable check. Input serialization maps positional/keyword arguments to TaskPlanner-shaped x/y/z/duration, body_name, secs/speed, etc. (`:271–330`). Serialization is before try/log, so its failures can be absent from the log. Observation and gripper methods serialize to `{}`. `get_object_position` supports positional or keyword `name`; native `body_name=` calls are not serialized by that branch. Non-exception False returns still log ok=True. Method result is returned directly to the Python program, without JSON conversion.

Optional few-shot mode prepends two user/assistant examples (`:160–224,539–542`). `_fewshot_messages` chooses quadruped only if its class string contains 'quad'; every other class gets arm examples, even aerial/humanoid/wheeled. The comment that unmatched classes get none is not implemented. No recursive undefined-function synthesis or original CaP few-shot library machinery is implemented here.

#### 7.2 Execution, recording and failure differences

CaP driver loading supports `driver.py` or a real fallback `driver_from_scratch.py` (`:461–482`); TaskPlanner does not have the filename fallback despite the identical-loader comment. CaP homes best-effort before execution. Recording patches only `skel.step`, default every tenth step with 3,000-frame cap; it uses candidate `render()` and has no initial/end-of-tool snapshots or direct `mujoco.mj_step` fallback (`:232–257`). Thus code calling native mj_step directly may not produce recorded frames. The docstring claiming the same frame capture as TaskPlanner is stale. Encoding is otherwise 30-fps H.264.

Anthropic uses AnthropicBedrock(timeout=300,max_retries=0); other models use ConverseClient. max_tokens defaults 4,000; temperature=0 except hard-coded Opus 4.8/4.9 names (`:526–550`). A provider exception returns an all-empty failure record and zero tokens. The raw model answer, extracted program, task, class and primitive list are written to a text trace before execution (`:570–578`). Captured stderr makes `error` nonempty even if execution completed; stdout is not captured. The finally block resets sys.stderr to sys.__stderr__, not the prior stream. `CaPResult.ok` requires no error, no logged exception and >0 calls (`:613–621`); False-returning calls do not fail it. Caught API exceptions still leave a failed call, so even recovered programs can have ok=False while final physics passes. Duration includes model generation, trace-file write and Python execution, but excludes initial construction/home and video encoding.

The evaluator replays only API calls, not the CaP source. It reproduces logged decisions without recomputing Python conditions; arbitrary direct state modifications or unlogged operations would not be replayed. The use of the same final grader does not make live execution, observation access, resource caps or recording equivalent.

#### 7.3 `eval_baseline.py`

The registry only offers `ours` and `cap`, despite the docstring mentioning RL/VLA/random (`:48–56`). It reuses helpers from `eval.py` but has its own runner (`:88–180`). It lacks the canonical initial base-yaw capture and `_obj_pos` fallback, using direct get_object_position for the same fixed object names. It also runs before/live/replay instances, uses three-argument replay, and marks divergence without rejecting the physical score. Thus heading checks default initial yaw to zero on this path.

`ours` is configured for 30 iterations/6,000 tokens and no run_tag; canonical eval uses 50 or environment override and namespaced output. CaP uses 4,000 tokens and output-stem run_tag (`:302–318`). Those are comparison conditions, not matched budgets. Ours videos/traces can overwrite between baseline runs using identical task IDs. Resume, checkpoint, trial aggregation and costs mirror eval's logic.

An optional per-trial SIGALRM covers `run_one_trial`; `_TrialTimeout` inherits BaseException so broad Exception handlers do not swallow it (`:64–85,341–354`). Timeout records failure, alarm seconds as duration, but zero tokens/calls/frames and no partial trace summary. The alarm is cancelled afterward; the prior signal handler is not restored. Without the option, there is no whole-trial cap. Non-timeout construction/replay errors can propagate and abort the run.

### 8. Statistics, leaderboard, and run orchestration

#### 8.1 `stats.py` (all 214 lines)

The hard-coded report reads seven model result pairs from `results/clean`, optional few-shot pairs from `results/fewshot`, collects task-level counts and rates, then forms matched (model,task) cells by intersection (`:25–39,98–125`). Duplicate task IDs across suites overwrite earlier entries. Unlike `_build_aggregate`, loading uses ordinary truthiness of physics_ok, so string `False` would count as success. Files are assumed valid; missing required files raise. Few-shot missing files simply yield no matched comparisons.

Implemented functions: Wilson interval with z=1.96 (`:42–49`); exact two-sided binomial sign test over non-tied wins/losses (`:52–64`); manually implemented Wilcoxon signed ranks with average ranks for ties and a normal approximation/continuity correction (`:67–95`). The Wilcoxon variance is the untied formula: it does not subtract a tie correction. No exact small-n distribution, resampling, multiplicity correction, task/model cluster model, or dependence adjustment is implemented. Calling the model×task comparison 'powered' is a docstring/output claim; no power calculation establishes that, and independence of those cells is not addressed by the code.

The report includes model-level differences, model×task differences, per-model Wilson intervals, and pooled counts (`:127–172`). CaP pooled rate uses the AA total denominator, assuming matching counts. It does not calculate separate CaP pooled n. The `COVERAGE_TASK` constant is declared but never used to compute a 12-task subset. The header's Self-column claim is stale: `build_report` reads only AA, CaP and optional few-shot; no Self source. `a_key` and the constructed cells list in nested `paired` are unused. The suite label hard-codes 13 tasks ×5 even if files differ. Per-cell intervals are rounded, and main prints interval overlap without a separate overlap-based significance procedure. No empirical statistics were computed in this review.

#### 8.2 `leaderboard.py` (all 236 lines)

Reads only top-level `*.json` with meta+aggregate, silently skips unreadable/unshaped files (`:28–40`), and trusts stored aggregate instead of recomputing it. Robot×short-model cells overwrite on collisions, last sorted filename wins (`:64–73`); baseline, suite, seed, run condition and full model identifier are not cell keys. A directory containing AA/CaP runs or different suites can therefore produce a mixed/overwritten matrix. The short-name mapper collapses model versions.

Per-suite table selects the highest overall physics rate among rows for that robot, then uses an unweighted mean of task rates (`:133–157`). Overall tables use per-run aggregate trial rates; final global mean is an unweighted average of run rates (`:183–202`), with costs/trials/wall summed. These are three distinct weighting choices. Per-task detail emits all rows despite its comment saying one model. Missing aggregate wall time can raise. The phrase agreement means the LLM is honest is not justified because llm_ok is the runner status and some graders are themselves linguistic/coverage checks. The generic reproduction command omits baseline, few-shot, exact tasks/trials, driver workspace and resume settings; it cannot reconstruct every possible mixed row solely from that snippet.

#### 8.3 `run_multi_model.py` (all 150 lines)

Three fixed presets schedule model-major, robot-minor sequential `eval.py` subprocesses (smoke: 2×3 cells; headline:4×3; full:8×4). There is no randomisation, parallelism, cell retry, timeout or resume. A subprocess nonzero exit is printed and execution continues; final text still says ALL RUNS COMPLETE, then calls the leaderboard regardless of individual failures (`:109–146`). Dry-run still creates results_dir. Class extraction is a naive YAML text scan; every non-arm class uses the quadruped task filter (`:64–75,111–123`). The smoke preset's quadruped IDs (`stand_up,sit,walk_forward_1s`) do not exist in the v2 task YAML in the snapshot, so that filtered route selects no quadruped tasks. Header/count statements describe scheduling, not completed valid cells. Model IDs and pricing in these files are repository literals, not verified current provider offerings.

### 9. Standalone benchmark/showcase scripts: distinct evidence levels

These files have main entrypoints and can launch real paid tasks. They are not ordinary isolated assertions merely because they live under tests/. They were read but not run.

| File | Full mechanism and evidence scope |
|---|---|
| `benchmark_baseline_ablation.py` (322 lines) | Only three SO-101 tasks despite docstring mentioning SO-101+Franka. Framework TaskPlanner gets 20 iterations/5,000 tokens; raw ReAct gets30/6,000. Raw execute_python launches a new subprocess per call with inherited environment, 180s timeout, 8,000/4,000-char stdout/stderr caps. It pickles only serialisable non-underscore locals to `_raw_state/state.pkl`; imports/modules generally do not persist, so the claim that imports/state fully persist is too broad. It clears the state at each task, permits arbitrary Python, and passes workspace in PYTHONPATH. Tool logs code character count but final summary drops that log and computes no promised lines-of-code metric. Both sides' 'pass' is runner `ok`, with no independent task grader. It always runs framework then raw in the same workspace, estimates all costs at Sonnet rates, saves at end, then os._exit(0). Relevant code:102–219,227–245,253–318. |
| `benchmark_contact_rich.py` (331 lines) | Five SO-101 tasks; independent replay via its own older handwritten dispatcher, not canonical replay. Initial measurement instance is homed, replay instance is not explicitly homed. Missing methods skipped; first replay exception breaks the sequence, yet it still scores partial state. Stack: top z>bottom z and XY<5cm, no advertised settle. Containment: XY<4cm, duck z<mug z+5cm, duck moved>5cm. Collision avoidance: bottle displacement<2cm and EE target error<5cm. Swap: both XY errors<8cm. Stable place:500 candidate steps, XY<5cm and z<0.1m, despite docstring saying3cm. All observations via driver getters; no blanket release/contact tests. Copies live video, stores physics and runner statuses, reports agreement and fixed Sonnet costs. No per-task exception containment around planner call/grader, no repeat trials, final-only JSON save. See51–125,217–295. |
| `benchmark_generalization.py` (433 lines) | Part A runs four declared new robots through SelfAssemble, records phase statuses/tokens/artifacts and errors (145–212). Part B mixes four pre-existing workspaces with new drivers, even drivers from failed pipelines if driver.py exists (403–414). Missing drivers are skipped. Robot class for task selection is inferred from a text substring `QuadrupedPDGaitSkeleton`; other classes get five arm tasks (248–251). Five tasks per included robot receive TaskPlanner25/5,000; 'task pass' is only res.ok, no replay/physics scorer. Crashes are retained with zero resources. Symlink helper replaces mjcf.xml. Scoreboard labels frame_count×10×0.002 as simulation time, although current recorder defaults every4 steps plus extra snapshots, so it is not a measurement. Token cost hard-coded Sonnet; Part B denominator only rows actually attempted. Pipeline and downstream statuses are separately stored but neither means hardware validation. |
| `benchmark_hard_tasks.py` (281 lines) | Five arm workspaces, six task strings per selected class. A source-text heuristic detects weld graspables; it can accept a textual empty `weld_graspable_bodies=[` line (166–177). Other arms get a different gripperless suite, so cross-robot task identity differs. TaskPlanner30/6,000; no independent grader, only r.ok. It saves full call logs and videos, catches per-task crashes with zero-resource failures, skips unavailable drivers, totals fixed Sonnet costs, writes final-only summary. Statements in docstrings about finding the breaking point are motivation, not demonstrated coverage. |
| `test_task_planner_complex.py` (165 lines) | Five authored tasks: three SO-101, one Franka, one Go2; grouped by workspace, missing driver skipped. Planner30/6,000, full call log/video/summary, no physical pass criterion and no exception isolation per task. Reported 5–15 seconds/continuous behaviour is a docstring expectation; duration estimates use frame_count×10×0.002 despite current capture-every4 and end-call frames (123–126,156). An ok result is not evidence that every requested phase happened. |
| `test_task_planner_showcase.py` (148 lines) | Nine task strings across four workspaces; Planner20/4,000, records/copies videos and only first five calls in summary. Missing drivers skipped. No replay or physical score; result.ok is counted as task success. Saving video does not establish all requested operations. Per-robot/top-level JSON and terminal scoreboard; no per-task exception catch. |

All seven assigned script paths, including empty `tests/__init__.py`, are covered. Apparent earlier '100%' or costs in comments were not verified against results, and should not be used as empirical thesis numbers.

### 10. Task specs and catalogue: what is fixed by humans

The six YAML files are data definitions, not generated capability contracts. `robot_zoo.yaml` stores robot/class/model source, dof and selected trusted state bindings. The snapshot includes hand, mobile-manipulator and bimanual classes in both zoo and `load_task_suite`, but **their three referenced task YAML files are absent from the snapshot**. Ordinary CLI evaluation of those classes therefore cannot load a task suite from the captured tree. Some older entries lack state_refs; state_refs are not consumed by the canonical run_task at this snapshot anyway. Counts of registered robots do not establish successfully executed integrations.

`tasks_arm.yaml` defines simple, hard, contact_rich, contact_rich_l4 and push groups, with prompts separate from success dictionaries and task-specific repeat counts (3 or5). Examples of scope mismatches: simple +X/+Z/square tasks share return-home scoring; dirty excursion now proves movement but not requested geometry. `conditional_pick` accepts either banana or mug and does not grade the X-condition choice, whereas `spatial_query_pick` explicitly requires the Y-argmax. Stack/containment YAML request 500 settle_steps but their scoring branches ignore it. Clear-then-reach asks eventual return home but scoring demands final EE at the old bottle position; it does not test that bottle was cleared. Push predicates mostly use final-height bounds, not a never-lifted trajectory condition. These are concrete distinctions between prompt intent and implemented metric.

`tasks_quadruped.yaml` is v2 with ten tasks, all five trials. It uses height ratios, +X bands and per-call selectors; it does not use the old multi_phase call-count grader. A prompt saying at most three walks is not enforced by forward_progress. 'Exactly one more exploratory correction' is not an exact-call-count condition in its phase_trajectory spec. Height ratios approximate staying up; no whole-trajectory upright/time rule is added by YAML.

`tasks_aerial.yaml` has eight five-trial tasks, absolute targets calibrated to an asserted demonstrated range. Final upright and call-boundary drift/waypoint criteria do not establish the prompt's 'stay upright the whole time'. `tasks_humanoid.yaml` explicitly warns that it was not used for paper leaderboard results: inaction can satisfy static checks and per-call snapshots miss an intra-call squat dip. There are eight five-trial tasks, including different prompts sharing the same squat metric; double squat is not a counted two-dip requirement. `tasks_wheeled.yaml` has eight five-trial drive/turn/composed tasks; composite scoring proves peak drive and final absolute turn, not requested order/sign. These source declarations should remain bounded to their actual graders.

### 11. Exact snapshot changes relevant to interpretation

Within the assigned Python scope, comparison of the stable snapshot against committed `ff486bf` identifies **only `autoadapter_bench/eval.py` as dirty**. `physics.py` is committed by that HEAD but was absent at initially inspected `08daf93`. Other assigned Python files match the unchanged earlier versions. The metadata's other dirty/staged/untracked files are outside this reviewer scope and were neither modified nor used to infer extra behaviour.

Dirty eval adds/reworks: replay input forms for new composite/hand methods; signature-based fallback; Boolean/dict-return failure detection; missing-tool failures; optional PhysicsTrace integration and returned samples; minimum real EE excursion for return-home; a replay-clean flag in tool-liveness; fingertip, ordered mobile manipulation, and bimanual simultaneous-hold scorers. These bodies were read in full. The preceding full scorer table records their exact conditions and weaknesses. At the captured point, canonical run_task still omits observer inputs, sample retention and `_replay_clean`, and still reports divergence without globally rejecting a score. This is an **unfinished connection in that snapshot**, not proof of a final intended architecture or a claim about changes made afterward.

### 12. Implications for thesis explanation

The inherited AA1 path can be explained compactly as live tool composition, state feedback, logged execution, then task-specific replay scoring. The new thesis contribution—task-library-derived capabilities and automatically formed criteria—must be described separately from these hand-authored registries and YAML predicates. It is not implemented merely by the existence of `TaskPlanner` or the benchmark suite.

The essential methodological explanation is who fixes each requirement, what quantity is observed, when the grader runs, and which limitations apply to the actual observation route. Direct MuJoCo access is stronger than a model summary, but same-process candidate getters and optional unconnected tracing do not establish complete candidate independence. A final-state positional pass does not automatically establish release, contact stability, trajectory safety, ordered phases, or a long hold. The current source has several explicit structural/reporting/coverage outcomes, and those should not be presented as one universal physical-success metric. A user-approved future implementation can strengthen these boundaries, but those stronger claims must not be retroactively attributed to this captured AA1 source or historical results.
## E. Learned baselines and rendering

## AA1 learned baselines, task spot-checks and rendering: full-source reading

### Coverage and limits

Read all 28 Python files under autoadapter_bench/baselines except code_as_policies (owned by a sibling reviewer): 4,620 lines. Exact paths are in baselines_coverage.json. This includes the eight nonempty top-level baseline scripts, three empty __init__.py files, all DP/RL/VLA Python modules. There is no separate root baselines/ directory in the snapshot. Shell launch scripts were not assigned/read. Final source is the stable source_snapshot captured at 2026-09-09 16:24:43Z, HEAD ff486bf. No code was executed, imported, trained, evaluated, rendered, downloaded or modified. No checkpoint/result artefacts were inspected, so every result-like sentence below is a property of code, not empirical performance evidence. References are relative to autoadapter_bench/baselines/.

### Research objects and control surfaces

The files implement several distinct evaluations, not one universal baseline protocol:

1. Joint-space PPO/DP: policy observes joint state and privileged EE/goal positions and predicts joint deltas (plus optional gripper). Robot control and state access are supplied through an existing generated or skeleton driver. This evaluates downstream use of that driver, not whether PPO/DP synthesize one.
2. Cartesian PPO: policy gets compact EE/gripper/goal state and predicts xyz delta+gripper; supplied driver IK converts it to joint actuator targets. This shares a level of abstraction with high-level agents but is not literally invoking move_cartesian with its full duration/interpolation behaviour.
3. OpenVLA: image+language model emits a seven-vector; only xyz delta is used, multiplied by a scalar and added directly in robot world coordinates, then full move_cartesian is called. State-derived targets exist only in evaluator; model gets qualitative directions, not the numeric target used by PPO/DP.
4. Franka/Piper LLM spot-check scripts: call TaskPlanner then replay logs and score terminal getters. Piper v1 explicitly injects kinematic object-carry logic; v2 assumes patched generated artefacts and changes acceptance reference.
5. Rendering scripts: run hand-scripted driver motions to produce still images, not extraction of original trial video or a replay of retained generation traces.

These distinctions are necessary when stating baseline fairness, zero-shot transfer or physics-grounded evidence. Module introductions containing claims such as ANY task, clean same API or harness sound are aspirations/interpretations, not mechanically established conclusions.

### Shared driver loading and compatibility adapter

`rl/rl_env.py:37–56` _load_skeleton_from_workspace removes module cache key driver, prefers driver.py over driver_from_scratch.py, side-loads under changed cwd, calls module build() if available, else Robot.build_from_mjcf('mjcf.xml'), restoring cwd. It does not validate provenance, restrict methods, inspect a pass report or build an evaluator-owned simulator. If neither file exists, the final missing path fails import. No skeleton cleanup in this helper or env reset.

`rl/skel_adapter.py` normalises heterogeneous generated APIs. __init__ (:29–50) caches first present setter name set_arm_actuators/set_joint_controls/set_joint_positions and first IK name ik/inverse_kinematics/ik_dls; dof from dof, n_joints, or model.nu. _pick_method (:53–57) tests hasattr, not callable/signature. _q_limits (:59–69) uses existing arrays, then joint_limits matrix, then first dof model joint ranges (an index-order assumption). _q_lo/_q_hi properties expose this. __getattr__ (:80–83) forwards anything else.

set_arm_actuators (:86–90) forwards chosen method or raises. get_joint_velocities (:92–108) uses driver's getter, else zero when no handles, else _arm_joint_ids/joint_ids/first dof and jnt_dofadr mapping. ik (:110–134) tries kwargs, positional q_init, then target-only on TypeError; this can retry after a method's internal TypeError, not just signature mismatch. Tuple outputs discard all but first q component, including any success flag/residual. get_object_position (:136–146) forwards or reads named body data.xpos directly; settle (:148–162) forwards, or steps duration/timestep with driver step or raw mj_step; absent handles silently return. Joint-position fallback (:164–178) maps jnt_qposadr. get_ee_pose/home/step/gripper/render/move_cartesian (:180–199) forward unchanged. The reviewed default envs import raw loader and do not automatically wrap with this adapter; its existence does not imply every generated driver is accepted by RL environments.

### Joint-space reach environment

`rl/rl_env.py:64–187` ReachXYZEnv supports arms despite module header also describing unimplemented stand-up RL. Constructor stores fixed offset or target pool, max_steps200, delta scale.05rad, tol.02m, optional ignored gripper action and default task-onehot. Builds driver to obtain dof and requires get_ee_pose plus set_arm_actuators. Action Box is dof(+1) in [-1,1]; observation is [onehot(reach=0,pick=1), q, qdot, ee, target, target-ee], dimension 2*dof+11 by default. Header's opposite error sign is stale. Constructor seed makes custom np.default_rng and optionally calls reset, but reset(seed=...) only seeds Gym's RNG, not this _rng.

_get_obs (:132–142) reads driver getters; reset (:144–156) rebuilds entire driver, homes, defines target from post-home EE plus fixed/random offset, resets counter. There is no noise in fixed-offset episodes. step (:158–182) clips action, slices first dof, targets current measured q+scale*delta (not prior commanded ctrl), clips optional limits, writes setter then driver.step(5). Action's extra gripper dimension is ignored. Reward=-distance-.001, +10 when error<tol; terminated=success and truncated=step_count>=max. Info gives error/EE/target. render forwards. No independent trajectory recorder or stability condition.

### Joint-space pick environment

`rl/pick_env.py:30–181` PickBananaEnv defaults random banana XY x[.10,.22], y[.04,.20], 200 steps, joint scale.05, lift criterion.02m and approach radius.04m. Obs has same dimensional arrangement as reach, but goal slot is current banana and onehot pick=1. Action dof+1, >0 last component closes else opens. It requires only gripper_close initially though later assumes more methods.

_get_banana_pos/get_obs (:94–108) expose privileged driver positions. _find_banana_qpos_adr (:110–118) finds first joint name containing banana, not explicit free-joint/type binding. reset (:120–142) rebuilds/homes/opens, writes sampled banana qpos XYZ with z=.06, zeroes qvel using the **qpos** address slice adr:adr+6, forwards and settles. Qpos address is not generally the freejoint dof address; this is a concrete layout assumption. Custom _rng again not reset by Gym seed. Start height is measured after settling.

step (:144–178) writes current q+scaled clipped deltas then invokes gripper open/close on every step, which may itself consume simulation steps; then driver.step(5). Reward=-EE-object distance-.001, +5 **each step** within approach tolerance with positive gripper command, +20 when lift>=.02. Success is height gain only, with no held-object, distance-to-gripper or persistence predicate. The per-step approach bonus can dominate eventual-success return; code is different from the corrected Cartesian reward. Closed is inferred from command sign, not measured jaw state. Action length is not explicitly checked inside step; e.g. shorter dof-length vectors can make last joint also serve as gripper signal. Comments claiming reach/pick necessarily incompatible action shapes are stale because current reach supports extra gripper dimension and both default onehots.

### Cartesian reach, pick and multitask environments

`rl/cartesian_env.py:32–82` _CartesianArmBase builds a raw driver once. Obs=[EE3, internal open flag1, goal3], action=[dx,dy,dz,gripper], default subtype scale.03m. Open flag is last commanded state, not measured gripper fraction. _goal abstract; _get_obs combines getters. _apply_cartesian clips action, nextEE=current+scale*xyz, calls driver's ik(nextEE,q_init,raise_on_unreachable=False), setter(q_des), conditionally toggles gripper only on requested-state change, then driver.step(5). No full move_cartesian duration/interpolation; no exception handling here. render forwards.

CartesianReachEnv (:85–127) uses target offset/pool,60 steps,tol.02. reset does mj_resetData/mj_forward **in place**, then home/gripper_open, target from current EE. This resets data but not any changed model eq_data/eq_active0 or arbitrary Python driver state; home/open may handle some, requiring callee review. Reward/termination same reach. Custom _rng constructor-only seeding as above.

MultiTaskCartesianEnv (:130–229) adds leading task bit (8D obs) and samples reach/pick at p_pick=.5 or force_task. Reach pool six ±5cm axis offsets. Pick randomisation similar but absent banana joint leaves adr=None then indexes without explicit missing-joint error; qvel uses qpos address. Goal is reach target or live banana. Reward reach as above; pick=-dist-.001+30*positive_lift, +50 at lift>=.02; termination solely lift threshold. approach_tol_m is stored but unused. Default max80. Task force lets one trained policy be tested per skill.

CartesianPickEnv (:232–297) is standalone 7D pick version, max80. Same in-place reset/randomisation with explicit absent-joint RuntimeError, same qvel-address assumption, same lift-dominant reward without per-step proximity bonus. It stores unused approach_tol_m. No held-object/persistence check. This corrects the specific reward-farming design but not the behavioural meaning of lift-only success.

### PPO training and evaluation scripts

`rl/train_ppo.py`: ProgressCallback (:25–54) captures Monitor episode rewards and terminal err<.02, logs last50 every5000 steps; unused buffers exist. main (:57–117) trains fixed +X5cm Reach max100 with PPO MlpPolicy, lr3e-4, rollout2048,batch64,epochs10,gamma.99,GAE.95,clip.2, default100k requested steps/device auto. No explicit seed, separate heldout validation or early stop; final checkpoint and JSON report training last50, not evaluation success. Requested steps logged may differ from SB3 rollout-rounded actual count.

`rl/train_ppo_multi.py`: TRAIN_TARGETS (:30–37) six ±axis5cm; callback (:40–63) logs last100. main (:66–114) max100 Reach target pool, with_gripper=True and default with_task_id=True (contradicting header no explicit task ID, although constant reach bit carries no within-family target information). PPO same parameters, default300k, no explicit seed. Writes target list/requested steps/time/last100 train rate. This trains multiple reach goals, not both reach and pick skills.

`rl/train_pick.py`: PickProgress (:22–49) records final lift and lift>=.02, last50 reward/success. main (:52–100) default200k steps, scale.1 (differs env default.05), max200, same PPO hyperparameters/no explicit seed; log is training stats.

`rl/eval_ppo.py`: eval_policy_on_task (:26–53) fixed-offset Reach reset repeatedly, deterministic model.predict, max100, success final info.err<.02. main (:56–110) five offsets (+X,+Y,+Z,diag3cm,-X), labels +X in-distribution and others OOD, averages rates/errors/steps. No random seed/initial perturbation; repeated fixed episodes can be deterministic repeats. No LLM is evaluated in this script despite comparison rhetoric.

`rl/train_eval_cartesian.py`: _suffix (:36–37) builds seed suffix. train_reach/train_pick/train_multitask (:40–76) use corresponding Cartesian envs, explicit seed, CPU PPO lr3e-4,n_steps2048,batch256,gamma.99 and other library defaults; save hardcoded so101_cart_* names. Default requested250k reach/300k pick/500k multitask. eval_multitask (:79–107) force_task, four fixed reach variants (not all six trained), pick seed123 random episodes, deterministic predict, max80. eval_on_reach (:110–125) max60; eval_on_pick (:128–140) max80; same .02 reach/lift thresholds. New env per model/evaluation with constructor seed123 pairs pick random draw sequences; reset(seed) still unused. main (:143–210) selects train/crosseval/eval_multitask, loads seeded files, reports four matrix cells and metadata. Payload step counts reflect CLI inputs, not checkpoint-verified training metadata. All evaluations score same env getters as policy observations, not independent Harness traces.

### DP demonstrations and training mechanism

`dp/collect_demos.py`: collect_one_episode (:26–64) resets Reach, at each step computes subtarget min(.01m,error) towards goal, calls supplied IK, derives delta=(q_des-current)/ctrl_scale clipped[-1,1], logs prior obs and action then env.step. On IK exception it first calls move_cartesian for .05s and reads new q, then still logs old obs and applies delta again; this fallback has already changed simulator outside the recorded env action, so recorded transition can differ from replayed action. stop_tol=.005 but env terminates at .02, making finer stop mostly moot. main (:67–107) defaults200 episodes, stores flattened obs/act and target_offset. It includes failed episode transitions too; no episode boundaries or success filtering, deterministic reset has no diversity by default.

`dp/collect_demos_multi.py`: six axis targets (:19–26), default100 demos each, same collector and flattened arrays (:29–72). Reach instantiated without with_gripper, so arm-only action vectors. It records train_targets, not episode indices. Joint-space PPO multi, by contrast, requests an extra ignored gripper dimension.

`dp/collect_pick_demos.py`: collect_one_pick_episode (:26–102) scripted state machine: approach initial banana+.03m until EE within.02; hold current joints/close for4 env steps; move initial banana+.10m until measured banana lift>=env threshold. Full-goal IK each step, exceptions stop episode, action appends ±1 gripper. Environment termination can end any phase once lift threshold reached. main (:105–146) defaults200 randomized episodes, ctrl_scale.1/max200, onehot=True; flattens all successful and failed transitions, records episode count/success count/scale/max but no episode boundaries.

`dp/train_dp.py` header says conditional U-Net/canonical setup, but actual network is flat Conv1D residual stack:

- SinusoidalEmb (:33–43): sin/cos frequencies for timestep.
- ResBlock1D (:46–61): Conv3 -> GroupNorm8/Mish, additive linear condition bias, second Conv3/GN/Mish, residual (1x1 if channel mismatch).
- CondConvStack1D (:64–99): 128D timestep MLP and128D state MLP concatenated;1x1 input projection to256 channels; five residual blocks;1x1 output. No temporal down/up sampling, U-Net skip hierarchy, visual encoder, observation history or attention. CondUNet1D is alias (:102–103).
- ActionChunkDataset (:111–128): for every flattened index, use its single observation and next horizon16 actions, only padding at **end of whole dataset** with final action. It ignores episode boundaries, so chunks cross reset/different-goal episodes; no dataset split.
- main (:131–226): load npz, mean/std+1e-6 normalisation per obs/action dimension, shuffled DataLoader, DDPM scheduler100 train steps, cosine squaredcos_cap_v2, epsilon prediction, clip_sample=True; noise action chunks, random diffusion timestep, MSE predicted noise, AdamW lr1e-4, default200 epochs/batch256. No explicit seed, EMA, holdout validation, early stopping or trajectory-level split. Auto device chooses MPS else CPU, not CUDA. Save weights, normalisation, dims,horizon,training seconds,final loss/loss history, not optimiser state/training seed/full dataset metadata. Constant task-onehot channels have std~1e-6; cross-skill unseen bit flips can create very large normalised inputs.

`dp/eval_dp.py`: load_dp (:28–35) reconstructs default architecture and torch.loads weights_only=False. predict_action_chunk (:38–57) normalises current single state, samples fresh Gaussian [1,A,horizon], sets10 DDIM timesteps, predicts epsilon/prev_sample repeatedly, denormalises output. Thus greedy DDIM transition does not make total policy deterministic: fresh noise is drawn every replan. Inference clip_sample acts in normalised action coordinates. eval_dp_on_task (:60–95) builds Reach, executes first8 of16 actions without new observation conditioning within chunk, replans, max100, success err<.02. main evaluates same five targets and in/OOD summaries; no seed or training seen-target check. Auto MPS/CPU.

### Cross-skill and multigoal evaluators

`eval_cross_skill.py`: run_policy_on_reach (:40–65) enables gripper action for shape parity, default onehot, four reach variants, deterministic predictions. run_policy_on_pick (:68–85) random banana,scale.1,max200 and lift>=.02. main (:88–150) loads two PPO checkpoints, evaluates 2x2 matrix, records metadata. Does not ensure same random pick episodes between model evaluations because each env constructor seed=None. Header's padding/truncation claim is stale: no explicit action conversion is performed.

`eval_dp_cross_skill.py`: local load_dp (:33–40), eval_on_reach (:43–68) with_gripper=True/onehot=True; eval_on_pick (:71–94) with_task_id=True,scale.1; DDIM100/10, execute8, thresholds as above. main (:97–150) loads reach/pick checkpoints and runs matrix. No dimensional compatibility assertion or gripper padding. Since reviewed reach collector emits dof actions, a checkpoint from it can provide only dof actions to Pick: its last joint can become gripper command because Pick step consumes action[-1], unless another unreviewed data preparation changed checkpoint action dimension. Shape parity must be verified from actual checkpoints, not inferred from these scripts.

`eval_multi.py`: TASKS (:31–46) six trained-axis5cm plus diag(.03,.03,.03),mag(.10,0,0),mixed(.05,.05,0),far_diag(.04,.04,-.02). eval_ppo (:54–86) and eval_dp (:94–142) use Reach max100, terminal err<.02; summaries (:150–158) macro-average six/four groups. main (:161–191) optional model paths/output. No fresh training or target-list agreement verified; a supplied checkpoint's exposure is assumed. Reach action-space defaults no gripper, though extra model output gets sliced by env. DP remains stochastic fresh noise, PPO deterministic repeated fixed starts. Results here alone cannot establish general task-family transfer.

### OpenVLA baseline and diagnostic

`vla/eval_openvla.py`: OpenVLAPlanner (:48–84) downloads/loads AutoProcessor and AutoModelForVision2Seq with trust_remote_code, bfloat16, device CPU/CUDA, no finetuning here. predict_action creates PIL image, language template, BF16 inputs, calls model.predict_action(unnorm_key='bridge_orig',do_sample=False), returns7-vector. No supplied camera calibration/frame rotation transformation; first3 outputs are interpreted directly as world xyz delta. Rotation and gripper are ignored for reach. Prompt directions are qualitative (forward/left/up); evaluator numeric goal is5cm or3cm each diagonal, not disclosed as a numeric goal to model.

rollout_one_episode (:101–157) homes, repeatedly renders and reads EE/error, early pass<.02, predicts, multiplies xyz by ee_delta_scale(default CLI2), sets nextEE=current+delta, move_cartesian duration.5. Failure logs string then tries raw IK/setter/20 steps using either ik or inverse_kinematics branch; fallback errors suppressed. Trajectory records pre-action EE, commanded delta/target, inference time and first failure, not complete simulator trace/video. main (:160–250) loads once, computes each target from another fresh homed driver, fresh driver per episode, default3 episodes/15 model steps, score final error. Repeated deterministic views/starts are repeated conditions, not randomized independent challenges. Labels in_dist_x+5 and OOD inherited from reach-training comparison do not establish VLA training exposure to those task labels or embodiment.

`vla/diag_openvla_harness.py`: adapter_probe (:39–57) commands axis5cm with duration.5 and prints whether largest achieved axis/sign matches. oracle_rollout (:60–98) uses privileged target-current capped.05m, but calls move_cartesian duration **.1**, unlike current .5 eval; catches failures, max15, tol.02 and prints sweeping SOUND/BROKEN conclusions. It has no assertions/result file. Its commentary that this is identical control loop is stale, and a passed directional oracle would establish feasibility of those commands, not validate all VLA image/action normalisation. main runs both only; though diagnostic says no model, importing TASKS from eval_openvla imports torch/transformers/PIL dependencies.

`vla/eval_openvla_sweep.py`: run_cell (:49–90) fresh homed drivers,5 targets,scale+unnorm key,move_cartesian.5, suppress exceptions, threshold.02; no original eval's IK fallback. main (:93–157) discovers model norm_stats, intersects with candidate list or falls back to first5 available, then sweeps six scales [.25,.5,1,2,5,10] at canonical key and other selected keys at scale2. **Not all keys and not full Cartesian product**, despite header claiming every key vs wide range. No camera/frame/wording/orientation sweep. Outputs per-cell rates/errors and best, not calibrated configuration selection or robot-generic transfer proof. Loaded once, greedy image action prediction; no retained per-action traces/video in sweep output.

### LLM reach/pick spot-checks and replay limitations

`eval_franka_reach_n10.py`: _fresh_ee (:49–63) loads Robot from a fixed artefact workspace without home, matching intended planner fresh state; _build_prompt (:91–101) asks relative5cm axes or3.5cm XY diagonal. Seven variants, default10 trials. _replay_and_measure (:66–88) loads another fresh driver without home and replays logged methods; move args converted, home/gripper no args, other methods also called no args, unknown skipped; first exception prints and breaks, then terminal EE still measured. Replay is not guaranteed equivalent for unhandled parameterised tools or failed original calls.

main (:104–232) computes one fresh target per variant, executes TaskPlanner with capture_video=False, replays, success Euclidean<.02 independent of r.ok. Crashes appended as failure. If replay throws, err=None then following print uses err*100 and can itself raise; summary conditional can float(None) if all errors unavailable. Costs hardcoded$3/$15 per million even CLI model can vary; durations exclude replay. No trial video/independent raw state observer in this script; getters define measurement. No empirical n10 outcome read.

`eval_piper_pick.py` v1: fixed empty-scene-synthesized Piper artefact loaded against pickbench (:53–67). _attach_pickbench_tools (:70–183) injects named-object positions, grasp bookkeeping and weld support. Close forwards original and only on truthy return selects nearest cube within **.06m** (header says.04), computes current relative pose via quaternion inverse/rotation and writes model.eq_data, model.eq_active0 and data.eq_active. Open deactivates held weld regardless of original return; is_holding is Python held-cube flag.

Most crucial, injected move_cartesian (:151–176) preserves cube world offset to EE, calls original motion, then **directly sets cube freejoint qpos XYZ to EE_after+offset and calls mj_forward**. Comment says generated driver uses kinematic mj_forward, so weld dynamics do not propagate. This supplies object transport through evaluator-owned kinematic editing, not actuator-driven contact/weld dynamics. _build_planner (:186–202) overrides load to rebuild/home/patch every run (passed skel argument unused). _replay_and_measure_pick (:205–240) replays selected tool args, unknown skips/error break then measures getters; lift against hardcoded initial CUBES z.215, holding any cube not specifically target. main six coloured reach/pick tasks supplies full procedural prompts, capture_video=False, default3; reach tol.04 above fixed cube+.05, pick held flag and target height gain>.05. JSON/cost helper summaries; not driver-only independent grasp evidence.

`eval_piper_pick_v2.py`: docstring/metadata disclose using a re-synthesized pickbench artefact with post-synthesis get_object_position and weld-relative-pose patches, but the patch implementation itself is not in this script and was not inspected in this assignment. _build_planner (:50–54) plain TaskPlanner; _replay_and_measure (:57–88) always home before replay, whereas agent initialisation equivalence requires planner/artifact review. Same skip/error-break semantics/getter measurements.

main (:91–241) default5 trials, full procedural10cm lift prompts, no video. Reach x/y use hardcoded initial cube while z target uses **terminal cube z+.05**. Pick pass is held flag and final cube z-.205>**.04m** (:163–172), not declared unused LIFT_HEIGHT_M=.05 or requested.10m. Report says patches5/8lines while docstring says5-line detail; neither proves those precise artefact edits. This route cannot support unassisted synthesis or strict Direct-MuJoCo motion claims just from its labels.

### Rendering modules: all functions and what images represent

`render_paper_figures.py`: module import immediately creates output directory (:20–22). ROBOTS (:25–34) eight curated MJCF/camera configs. render_scene (:40–64) new raw model/data, optional home keyframe, forward+200 uncontrolled dynamics steps, free camera, Renderer630x420, PIL save. main (:67–96) renders zoo and pickbench, missing/failing entries print/skip and returns0. These are raw asset thumbnails, not validated generated-driver motion; GL/PIL/MuJoCo dependencies required and no explicit headless backend set.

`render_filmstrips.py`: import creates paper figure output dir; load_module (:29–34) inserts module in sys.modules; make_cam (:37–44) fills free camera; render (:47–52) opens Renderer480x320 per frame and saves PNG. step_partial (:55–65) interpolates data.qpos indexed by presumed joint_ids to q_target, resolves actuators by act_<armname>, writes ctrl/mj_step; this is a hand-written execution helper, not driver's move_cartesian. Missing actuator silently skipped.

- filmstrip_so101 (:68–105): loads specific fromscratch artefact, home, target+X.05, uses inverse_kinematics tuple; three separate interpolation chunks each recompute start and move all the way to same q_target, so captions33/66% do not mean one trajectory's corresponding fractional target. Final frame explicitly returns zero joints/home, unlike reach task that stops target.
- filmstrip_piper (:108–151): specific patched pickbench artefact; scripted green-cube approach+.08, descend+.005,close,lift+.12; exceptions printed, frames saved regardless; no success predicate.
- filmstrip_go2 (:154–202): despite filenames walk and header stand+walk, actually sit/stand/sit, calling stand_up repeatedly in subdurations, avoiding known failing walking. These are scripts' stated choices, not independently checked historical outcomes.
- filmstrip_letter (:205–237): scripted L down.04,+X.04,return; nested goto solves IK then hand-coded step_partial even when IK ok false; warnings only.
- filmstrip_skydio (:241–260): explicit takeoff.5,waypoint(.4,0,.5),return,land via specific artefact.
- filmstrip_h1 (:263–295): home/stand balance, monkeypatch global mujoco.mj_step during squat.15m/3s; capture at quarter/half/.78 expected steps, restore finally. Counter increments per call rather than nstep value; temporal fractions may mismatch batched stepping.
- filmstrip_swap (:299–348): loads named reference skeleton artefact, scripts banana/bottle swap with hardcoded park and nested pick/place waypoints; prints final positions, no Harness criterion, no LLM trace replay. Figure labels about graded task rate are unverified commentary here.
- filmstrip_anymal (:352–370): despite sit/stand docstring, home/stand then four2s walks, renders and prints planar norm and final height; no task verdict.
- filmstrip_patrol (:373–392): scripted takeoff.6 and square waypoint calls.
- main (:395–436): tries all nine separately, prints exceptions, returns0 even if all fail. No original trial IDs, original trajectory/video extraction or acceptance checks. Images illustrate motions achievable by scripted calls under chosen artefacts, not automatically the evidence used for reported trials.

### Evidence implications and unresolved details

PPO/DP training exposure includes privileged state and repeated simulation or IK-generated demonstrations; zero task training for a high-level LLM is different from zero pretraining or zero supplied controller code. Joint versus Cartesian baselines receive different control abstraction and timing. OpenVLA receives images and qualitative instructions while state baselines receive metric goals; shared actuator backend alone does not isolate model capability. Training rewards, environment terminal conditions and independent task-Harness verdicts are separate concepts. In these files most metrics come from driver getters or environment-owned bookkeeping; stronger benchmark oracle elsewhere must be identified explicitly.

Several scripts are historical spot checks or illustration generators and contain kinematic assistance, changed reset conventions or threshold choices. They should not be silently promoted into future formal Direct-MuJoCo evidence. No new protocol, experiment workspace, runner or thesis changes were created. Actual checkpoint dimensions/training metadata, result denominators, action traces, dynamics authenticity, paired random initial conditions, driver patches and renderer output remain artefact-level questions for separate review; code alone does not answer them.
