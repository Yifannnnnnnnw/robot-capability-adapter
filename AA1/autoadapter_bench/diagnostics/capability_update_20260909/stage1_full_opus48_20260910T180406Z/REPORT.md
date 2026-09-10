# AA1：15 台机器人阶段一结果整理

**阶段一诊断流程已全部结束：6 台在各自验证范围内通过，9 台未通过；待跑 0 台，未解决中断 0 台。** SO-101 已完成剩余第三次 repair 及完整 Framework。其余 14 台保留此前最终结果，没有额外重跑。

“流程结束”表示取得完整通过、修复预算耗尽后的失败或构建失败等终止结果，不表示所有机器人都通过。本轮仅到 Framework，未运行 export、demo 或 ReCAP。不同形态和历史结果的验证范围不同，这不是正式实验统一分母下的 6/15 通过率。

## 全部机器人

能力分数不含构建检查；保留的新形态和 Skydio、H1 使用各自既有检查数。所有最后候选文件均存在，Unitree A1 的文件缺少 build()，不能把“有文件”当成有效驱动。

| 机器人 | 最终 Framework | 验证范围或失败项 |
|---|---|---|
| [SO-101](summary_so101.json) | 未通过 8/10 | A4 两项未形成规定目标接触窗口 |
| [PiPER](../repair3_opus48_20260910/repair_2/piper/validate_report.json) | 通过 11/11 | A1–A5 全部十个条件；另含构建检查；保留历史结果 |
| [Franka](../stage1_full_opus48_20260910T120531Z/summary_franka.json) | 未通过 8/10 | A4 两项未通过 |
| [KUKA](../stage1_full_opus48_20260910T133740Z/summary_kuka_iiwa14.json) | 未通过 6/8 | A4 两项未通过；无夹爪能力 |
| [xArm7](../stage1_full_opus48_20260910T142842Z/summary_ufactory_xarm7.json) | 通过 10/10 | A1–A5 全部十个条件通过 |
| [Kinova + Robotiq](../stage1_full_opus48_20260910T142842Z/summary_kinova_gen3_robotiq_2f85.json) | 未通过 9/10 | A2 boundary 路径偏差 0.03056 m > 0.020 m |
| [UR5e + Robotiq](../stage1_full_opus48_20260910T142842Z/summary_universal_robots_ur5e_robotiq_2f85.json) | 未通过 8/10 | A4 两项表面相对速度超限；真值已有接触 |
| [Go2](../stage1_full_opus48_20260910T142842Z/summary_go2.json) | 未通过 8/10 | G5 两项高度漂移超限；G1–G4 通过 |
| [Unitree A1](../stage1_full_opus48_20260910T142842Z/summary_unitree_a1.json) | 构建失败，物理条件未运行 | 最终文件缺 build()；未进入物理条件，上一轮 1/10 |
| [ANYmal-C](../stage1_full_opus48_20260910T142842Z/summary_anymal_c.json) | 未通过 1/10 | 仅 G2 nominal 通过；其余九条件未通过 |
| [LEAP](../generated/leap_hand/validate_report.json) | 通过 2/2 | 构建 + LEAP 四指尖共同到达；保留历史结果 |
| [Stretch 2](../generated/hello_robot_stretch_2/validate_report.json) | 通过 2/2 | 构建 + 底盘先移动、末端后到达；保留历史结果 |
| [ALOHA 2](../generated/aloha_2/validate_report.json) | 通过 2/2 | 构建 + 两臂分别到达并共同保持；保留历史结果 |
| [H1](../stage1_full_opus48_20260910T142842Z/summary_h1.json) | 未通过 7/8 既有检查 | 真实两秒站立通过；humanoid_walk 未通过 |
| [Skydio X2](../generated_from_scratch/skydio_x2/validate_report.json) | 通过 7/7 | 既有 from-scratch 七项检查；保留历史结果 |

## SO-101 补齐结果

从 repair_2 的已验证候选和公共 Study 重试中断的第三次 repair，真实调用 Holistic `eu.anthropic.claude-opus-4-8`。原模型对话不能从 JSONL 自动恢复，因此本次是重试该 slot，原部分调用、费用计量与错误仍保留。公开的全新生成入口未增加历史续跑模式；这次单独调用原 pipeline 中既有 repair 和完整 Framework 函数，调用源与来源路径见 [运行说明](RUN_NOTES.md) 及 run_context。

A4 准备点保持已达到 0.204 / 0.206 s，超过要求的 0.1 s；后续未形成目标接触窗口。nominal 在行程用尽后停止，boundary 到时限停止。最终 **A1、A2、A3、A5 的八条件通过，A4 两条件失败**，没有增加第四次有效 repair。

完整十条件均执行真实物理，累计 13.288 s；trace 的步数、样本数、data.time 和同一模型/状态一致。未发现真实状态或模型参数直接改写。十段视频全部独立完整解码通过。[审查记录](review_so101.json)、[trace / 视频索引](video_trace_review.json)；本批 commit：`fd364e7e`。

## 逐轮记录与提交

| 机器人 | 初次 | repair 1 | repair 2 | repair 3 | 最新证据 commit |
|---|---|---|---|---|---|
| Franka | 2/10 | 5/10 | 9/10 | 8/10 | e50a52c7 |
| SO-101 | 构建/接口失败；未进入物理条件 | 6/10 | 8/10 | 8/10 | fd364e7e |
| KUKA | 5/8 | 构建/接口失败；未进入物理条件 | 6/8 | 6/8 | cbf2dab5 |
| xArm7 | 构建/接口失败；未进入物理条件 | 6/10 | 10/10 | — | 7ced281a |
| Kinova + Robotiq | 1/10 | 9/10 | 9/10 | 9/10 | 3fb31734 |
| UR5e + Robotiq | 1/10 | 构建/接口失败；未进入物理条件 | 8/10 | 8/10 | 0a227b8d |
| Go2 | 6/10 | 8/10 | 8/10 | 8/10 | da4cf60a |
| Unitree A1 | 构建/接口失败；未进入物理条件 | 1/10 | 1/10 | 构建/接口失败；未进入物理条件 | f40a291b |
| ANYmal-C | 0/10 | 0/10 | 构建/接口失败；未进入物理条件 | 1/10 | 49076cad |
| H1 | 缺驱动；未进入物理检查 | 缺驱动；未进入物理检查 | 缺驱动；未进入物理检查 | 7/8 既有检查 | e4463457 |

H1 三次 repair 都实际尝试；前两次未产出正式驱动，只有第三次产出，因此原字段 effective_repairs 为 1，不能据此再增加两次修复。其最终站立检查真实推进 2 秒 / 1000 步，但全套因行走失败，结果为 7/8。

SO-101 原 repair_3 的 DNS 中断、此次受限环境下首个请求前的 DNS 失败都保留，后者没有返回模型 token。批准在受限环境之外执行后，DNS 检查成功，Holistic repair 及 Framework 正常结束。累计记录包含原中断已返回的 token，未把重试伪装成一次从未中断的运行。

## 计量与证据

十台需要新运行的机器人（含 SO-101 历史中断及成功完成的第三次 repair）累计：

- 输入 token：**32,475,972**；输出 token：**787,358**。
- 各机器人累计墙钟之和：**16702.759 s**。另有本地 DNS 失败 0.381 s，合计 16703.140 s。
- Framework 已记录物理仿真：**504.610 s**。
- 实际费用未返回：**null**。保留五台历史运行的费用和时间未并入上述十台总数。

墙钟包含等待及可能的环境暂停，不能当作纯推理耗时；原 SO-101 时钟差异和 H1 长间隔都保留。物理总数仅覆盖已记录部分，不包含生成自测、全部初始化或 H1 其他未仪器化动作。

逐轮 driver、公共 Study、生成/repair trace、脱敏反馈、指标、子进程报告和代码 commit 都有记录。大体积物理 trace 与视频留在本地，路径写入报告。[机器可读总表](batch_summary.json) 逐项链接旧结果和新结果；[补齐前快照](../stage1_full_opus48_20260910T142842Z/REPORT.md) 保持不变。

## 仍需保留的解释限制

- Unitree A1、ANYmal-C 的 G1–G3 参考正控仍有缺口，失败不能全部归因于模型。
- Kinova 最后构建阶段在额外 MjData 中做夹爪校准，未进入能力 trace；不能宣称全部物理执行共用一个记录世界。
- UR5e 的候选自报 no_contact，但真值已有持续接触，失败门为表面速度；不能按候选返回值解释结果。
- A4 修正前后运行分属不同代码版本。旧 trace 复评分、参考正控和新模型生成分别保留，数值门槛、资产、动力学参数未改变。SO-101 nominal 参考控制的表面速度问题仍见 [A4 修正报告](../stage1_full_opus48_20260910T133740Z/a4_correction_20260910T142252Z/REPORT.md)。
- PiPER 保留完整 A1–A5 范围；LEAP、Stretch、ALOHA 保留各自首项形态任务，Skydio 保留既有 from-scratch 范围，不宣称它们经过统一的全任务库测试。

本次仅补跑和整理证据，没有修改 pipeline、骨架、评分、资产或论文。所有提交仅在本地 Git，未推送远端。
