# AA1 阶段一诊断汇报

代码与统一入口已交付；本轮真实生成尚未全部完成。Franka 完整运行到三次 repair 上限，SO-101 第三次 repair 被真实 Holistic DNS 解析错误中断，随后八台未启动。未执行 export、demo、旧任务评测或 ReCAP，也没有重跑已通过的五台。

本报告保存本目录中断时的状态。用户随后授权继续，后续调用将在新的运行目录记录。

## 新运行

| 机器人 | 初次 Framework | repair 1 | repair 2 | repair 3 | 结论 |
|---|---|---|---|---|---|
| Franka | 2/10 | 5/10 | 9/10 | 8/10 | 已走完；全套未通过 |
| SO-101 | 缺 build，物理条件未运行 | 6/10 | 8/10 | API 中断，未运行 Framework | 外部阻塞；只完成两次有效 repair |
| KUKA | 未启动 | — | — | — | 同一 DNS 阻塞后停止调用 |
| xArm7 | 未启动 | — | — | — | 同上 |
| Kinova + Robotiq | 未启动 | — | — | — | 同上 |
| UR5e + Robotiq | 未启动 | — | — | — | 同上 |
| Go2 | 未启动 | — | — | — | 同上 |
| Unitree A1 | 未启动 | — | — | — | 同上；G1–G3 参考正控缺口未变 |
| ANYmal-C | 未启动 | — | — | — | 同上；G1–G3 参考正控缺口未变 |
| H1 | 未启动 | — | — | — | 同一 DNS 阻塞后停止调用 |

表中分数为能力条件，不包含 driver_build。Franka 每轮都是 driver_build 加全部十个条件。SO-101 第一轮只执行了失败的构建检查，不能写成十个物理条件均失败；其最后完整验证的候选是 repair_2，repair_3 的候选未验证。剩余八台仅有协调器写入的阻塞说明，没有伪造模型调用、候选或物理结果。

Franka 最后失败的是 A4 nominal 和 boundary；最佳的 9/10 也不算整台通过。SO-101 repair_2 同样只剩 A4 两项未通过。能力定义、阈值、资产及动力学参数均未为这些结果改动。

## 证据和计量

- [批次摘要](batch_summary.json)
- [Franka 逐轮审查](review_franka.json)、[原始摘要](summary_franka.json)
- [SO-101 逐轮审查](review_so101.json)、[原始摘要](summary_so101.json)
- [真实阻塞 trace](repair_3/so101/traces/03_repair_3.jsonl)：最后一条为 invoke_error，URLError/Errno 8，主机名解析失败。这不是此前的 Bedrock 支付错误，也不能据此断言 Holistic 计费或账户失效。

累计 provider 报告的 token：输入 **6,070,976**，输出 **142,987**，包括 SO-101 第三次 repair 中已经返回的调用。实际费用未提供，保持 null。

原始 SO-101 启动器每台耗时使用 monotonic 时钟（929.942 秒），pipeline 使用墙钟（3643.746 秒），本次出现差异。两种原始数值和 UTC 起止时间全部保留，没有改写历史记录。Franka pipeline 墙钟为 1137.013 秒。Framework 已记录的累计物理时间分别为 113.390 秒和 13.476 秒。

各实际执行条件的 trace.json 与 video.mp4 留在本地，路径记入 result.json。Franka 初始十个视频还独立完成首帧解码与 trace 时间核对。提交 driver、Study、生成/修复 JSONL、脱敏反馈、指标和报告；不提交大体积物理 trace 与视频。

## 保留的五台

| 机器人 | 已有验证范围 | 处理 |
|---|---|---|
| PiPER | driver_build + A1–A5 十个条件，11/11 | 保留 repair_2 的全套通过 |
| LEAP | driver_build + 四指尖共同到达，2/2 | 保留既有单任务诊断范围 |
| Stretch 2 | driver_build + 底盘先移动、末端后到达，2/2 | 保留既有范围 |
| ALOHA 2 | driver_build + 两臂分别到达并共同保持，2/2 | 保留既有范围 |
| Skydio X2 | 既有 from-scratch Framework 7/7 | 保留既有范围 |

这些既有通过的验证范围不同，不合并宣称完整新能力库通过率；本批结果也不进入正式实验分母。H1 本轮没有真实新诊断结果，不能宣称其新生成驱动已完成两秒站立。

## 代码与检查

- ced8ec4e：修正机械臂、四足骨架的 Spec-only 提示冲突。
- 32017395：独立薄启动脚本、from-scratch 本地路由、可信目录路线、H1 trace/video 与说明。
- 39ebf986：统一 run_stage1，完整 Framework 子进程、独立轮次、最多三次 repair、阶段一结果和通信错误处理。
- e2eefade：保留 H1 失败时的实际物理时长和证据。
- e50a52c7：Franka 四轮真实诊断证据。
- 024b08b5：SO-101 诊断与中断证据。
- e6c8115b：启动器统一记录墙钟耗时，另存 monotonic 耗时；原始记录不改写。

本会话复跑的最小检查为 **31 passed**：test_stage1、test_from_scratch_local_mode、test_stage1_launcher、test_h1_framework_duration、test_resume_repair、test_stop_after_validate。真实 Franka 运行覆盖初次生成加三次 repair；SO-101 真实错误验证了中止后的候选不会伪报生成成功或继续 Framework。

实现由 Luna Max 子会话完成，本会话审查并直接执行真实 Holistic 调用。所有提交仅在本地，没有自动推送远端。原始历史结果、未提交的用户修改和既有五台结果保持原状。
