# AA1 阶段一最终诊断汇总

当前没有正在运行或尚未启动的机器人。15 台的已有记录为：**6 台在各自记录的范围内通过、8 台用完 repair 预算后未通过、1 台 SO-101 保留网络中断记录**。本轮要求新跑的十台中，xArm7 全套通过，八台未通过，SO-101 未完整结束。保留的五台没有重跑。

本轮仅执行 Study、Generate 和完整 Framework，最多三次 repair；未运行 export、demo、旧任务评测或 ReCAP。Holistic 模型保持 `eu.anthropic.claude-opus-4-8`。这些记录用于接入诊断，验证范围及 A4 修正前后条件不同，不作为正式实验通过率的分母。

## 十台新运行

下表机械臂/四足分数仅指能力条件，不包含构建检查；H1 单列其既有八项检查。不同候选的通过项不能合并为一台通过。

| 机器人 | 初次 Framework | repair 1 | repair 2 | repair 3 | 最终结果 | 证据提交 |
|---|---|---|---|---|---|---|
| [Franka](../stage1_full_opus48_20260910T120531Z/summary_franka.json) | 2/10 | 5/10 | 9/10 | 8/10 | 未通过 | e50a52c7 |
| [SO-101](../stage1_full_opus48_20260910T120531Z/summary_so101.json) | 构建/接口失败；未进入物理条件 | 6/10 | 8/10 | API 中断；Framework 未运行 | 历史 API 中断 | 024b08b5 |
| [KUKA](../stage1_full_opus48_20260910T133740Z/summary_kuka_iiwa14.json) | 5/8 | 构建/接口失败；未进入物理条件 | 6/8 | 6/8 | 未通过 | cbf2dab5 |
| [xArm7](summary_ufactory_xarm7.json) | 构建/接口失败；未进入物理条件 | 6/10 | 10/10 | — | 完整通过 | 7ced281a |
| [Kinova + Robotiq](summary_kinova_gen3_robotiq_2f85.json) | 1/10 | 9/10 | 9/10 | 9/10 | 未通过 | 3fb31734 |
| [UR5e + Robotiq](summary_universal_robots_ur5e_robotiq_2f85.json) | 1/10 | 构建/接口失败；未进入物理条件 | 8/10 | 8/10 | 未通过 | 0a227b8d |
| [Go2](summary_go2.json) | 6/10 | 8/10 | 8/10 | 8/10 | 未通过 | da4cf60a |
| [Unitree A1](summary_unitree_a1.json) | 构建/接口失败；未进入物理条件 | 1/10 | 1/10 | 构建/接口失败；未进入物理条件 | 未通过 | f40a291b |
| [ANYmal-C](summary_anymal_c.json) | 0/10 | 0/10 | 构建/接口失败；未进入物理条件 | 1/10 | 未通过 | 49076cad |
| [H1](summary_h1.json) | 缺驱动；未进入物理检查 | 缺驱动；未进入物理检查 | 缺驱动；未进入物理检查 | 7/8 既有检查 | 未通过 | e4463457 |

SO-101 第三次 repair 报 `URLError / Errno 8` 主机名解析失败；最后完整候选为 repair_2。之后其余机器人调用成功，因此不能称当前 Holistic 服务仍整体不可用。原中断记录保留，没有增加调用。

H1 三次 repair 均实际调用；初次及 repair_1/2 耗尽工具迭代预算，未产出驱动，repair_3 才保存完整候选。原始 `effective_repairs=1` 表示只在这一轮产生候选，不表示只尝试一次 repair。最终两秒站立通过：真实 1000 步、最低高度 0.950 m、最低 upright 0.998；行走横移约 -0.127 m、偏航变化约 179°，因此全套 7/8 未通过。
[H1 站立视频](repair_3/h1/recordings/stand_balance_2s.mp4)；[H1 逐轮审查](review_h1.json)。

## 关键失败与证据限制

- Franka、KUKA 最后仍失败于 A4 两项；SO-101 最后已验证候选也剩 A4 两项。旧记录的 A4 离线重评分没有使它们整台通过。
- Kinova 最后一轮 9/10，A5 已修复，但 A2 boundary 路径偏差 0.03056 m 超过 0.020 m。其构建阶段另有未进入能力 trace 的夹爪校准仿真，不能宣称全部物理执行都位于记录世界。
- UR5e 最后一轮 8/10，A4 两项失败。MuJoCo 真值显示已经持续接触；首次失败指标是表面相对速度 0.05093 m/s 超过 0.03 / 0.04 m/s。候选自报 no_contact 与真值不符。
- Go2 最后一轮 G1–G4 全通过，G5 两项高度漂移约 0.1074 / 0.1113 m，超过 0.03 m。
- Unitree A1 最后候选缺 build()，没有该候选的物理 trace/video；上一轮为 1/10。ANYmal-C 最后一轮仅 G2 nominal 通过（1/10）。两台 G1–G3 参考正控缺口仍在，不能把失败全部归因于生成模型。
- xArm7、Kinova、UR5e、Go2、ANYmal-C 最终候选的视频完成完整解码，能力 trace 与官方物理采样核对；A1 核对的是最后有物理执行的 repair_2。H1 独立核对并解码两秒站立的 trace/video，其余既有检查保留指标报告。Franka、SO-101、KUKA 的检查范围见各自逐轮审查记录。

A4 的公开语义、评分、反馈与真实接触速度观测修正已分批提交；数值阈值、资产和动力学参数保持原样。旧 trace 复评分及 xArm7 真实参考正控见 [A4 修正报告](../stage1_full_opus48_20260910T133740Z/a4_correction_20260910T142252Z/REPORT.md)。参考正控与模型生成记录分开，旧结果未覆盖。

## 保留的五台

| 机器人 | 已有验证范围 | 检查结果 |
|---|---|---|
| [PiPER](../repair3_opus48_20260910/repair_2/piper/validate_report.json) | A1–A5 全部十个条件；另含构建检查 | 11/11 |
| [LEAP](../generated/leap_hand/validate_report.json) | 构建 + LEAP 四指尖共同到达 | 2/2 |
| [Stretch 2](../generated/hello_robot_stretch_2/validate_report.json) | 构建 + 底盘先移动、末端后到达 | 2/2 |
| [ALOHA 2](../generated/aloha_2/validate_report.json) | 构建 + 两臂分别到达并共同保持 | 2/2 |
| [Skydio X2](../generated_from_scratch/skydio_x2/validate_report.json) | 既有 from-scratch 七项检查 | 7/7 |

Stretch、ALOHA 的旧五阶段 `.ok=false` 受到跳过后续阶段影响；它们的 Framework 通过。LEAP、Stretch、ALOHA 的既有单任务诊断不代表完整跨任务能力库通过。

## 时间、token 与文件

十台新运行累计输入 token **32,210,095**、输出 **775,967**；已记录 pipeline 墙钟之和 **16517.400 秒**，Framework 已记录物理时间之和 **491.322 秒**。实际费用未返回，保持 `null`。此处不包含保留五台、参考正控或离线重评分。

墙钟包含等待和环境暂停的可能影响，不能称为纯模型推理时间。SO-101 的两种时钟差异保留；H1 有一次 2628 秒的调用墙钟间隔后恢复，来源无法仅凭日志确定。物理时长只汇总已仪器化记录，不包含模型自测、未记录的初始化或全部 H1 其他行为。

逐轮候选、Study、模型 trace、脱敏反馈、指标、Framework 子进程报告和 run_context 已分别保存。大体积物理 trace/video 按既有规则留在本地；JSON 中保留真实路径。原始完整模型 HTTP 请求不在当前 trace 内，不能宣称有完整请求抓包。

统一入口与薄启动脚本继续使用原有实现；本次收尾没有新增 pipeline、实验 runner、manifest 或管理目录。[机器可读汇总](batch_summary.json) 包含十台逐轮原始路径、五台保留证据和限制。

每台紧凑证据均已独立本地提交，见表中 commit。未推送远端；未修改历史目录、论文或用户的其他未提交改动。
