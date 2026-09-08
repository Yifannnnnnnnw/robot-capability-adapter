# Fixed-family follow-up results

Snapshot: 2026-09-08T11:30:45.586786+00:00

**本轮因 DeepSeek API HTTP 402 中断，尚未完成。** 官方文档将 402 定义为账号余额不足。[DeepSeek 错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/)

Franka 和 Barkour 已有部分 Harness 提交，随后在 Repair 中断；其余六款在第一次 Study 请求即被拒绝，尚未获得模型回复或生成 Driver。两条队列虽已结束，这不是“8 款模型均失败”，也不能计算为 0/8 合成成功率。

本轮为诊断：8 款此前未调用模型的机器人开展新合成；SO101、Go2、Piper 保留前轮结果。三款旧结果没有重跑或按新判定器重新计分。

本轮 8 款共 78 个用例／完整提交轮；11 款固定清单仍为 108 个。

新一轮全部完成：否；未完成项目不作最终成功或失败结论。

| 机器人 | 轮次 | 基本环境 | Harness 每次通过数 | 当前记录 | 请求 / 成功回复 |
|---|---|---|---|---|---:|
| robotstudio_so101 | 前轮保留 | 本轮未检查 | 未提交 | [最终未通过；生成/修复错误](../../runs/diagnostic/fixed-family-v1-initial-20260908/robotstudio_so101/candidate/experiment_report.json) | 37 / 37 |
| unitree-go2-stock-12dof | 前轮保留 | 本轮未检查 | [7/10](../../runs/diagnostic/fixed-family-v1-initial-20260908/unitree-go2-stock-12dof/candidate/cells/unitree-go2-stock-12dof/skeleton-assisted/attempt-0/capability_validation_report.json) → [6/10](../../runs/diagnostic/fixed-family-v1-initial-20260908/unitree-go2-stock-12dof/candidate/cells/unitree-go2-stock-12dof/skeleton-assisted/attempt-1/capability_validation_report.json) → [6/10](../../runs/diagnostic/fixed-family-v1-initial-20260908/unitree-go2-stock-12dof/candidate/cells/unitree-go2-stock-12dof/skeleton-assisted/attempt-2/capability_validation_report.json) | [最终未通过](../../runs/diagnostic/fixed-family-v1-initial-20260908/unitree-go2-stock-12dof/candidate/experiment_report.json) | 79 / 79 |
| franka_panda | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/franka_panda/environment/environment_check.json) | [0/10](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-0/capability_validation_report.json) → [6/10](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-1/capability_validation_report.json) | [402：Repair 中断；生成/修复错误](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/franka_panda/candidate/experiment_report.json) | 68 / 67 |
| kinova_gen3_robotiq_2f85 | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/kinova_gen3_robotiq_2f85/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/kinova_gen3_robotiq_2f85/candidate/experiment_report.json) | 1 / 0 |
| ufactory_xarm7 | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/ufactory_xarm7/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/ufactory_xarm7/candidate/experiment_report.json) | 1 / 0 |
| universal_robots_ur5e_robotiq_2f85 | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/universal_robots_ur5e_robotiq_2f85/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/universal_robots_ur5e_robotiq_2f85/candidate/experiment_report.json) | 1 / 0 |
| piper | 前轮保留 | 本轮未检查 | [0/10](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/piper/candidate/cells/piper/skeleton-assisted/attempt-0/capability_validation_report.json) → [6/10](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/piper/candidate/cells/piper/skeleton-assisted/attempt-1/capability_validation_report.json) | [最终未通过；生成/修复错误](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/piper/candidate/experiment_report.json) | 80 / 80 |
| kuka_iiwa_14 | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/kuka_iiwa_14/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-arms-20260908/kuka_iiwa_14/candidate/experiment_report.json) | 1 / 0 |
| google_barkour_vb | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/google_barkour_vb/environment/environment_check.json) | [2/10](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/google_barkour_vb/candidate/cells/google_barkour_vb/skeleton-assisted/attempt-0/capability_validation_report.json) | [402：Repair 中断；生成/修复错误](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/google_barkour_vb/candidate/experiment_report.json) | 57 / 56 |
| unitree_a1 | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/unitree_a1/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/unitree_a1/candidate/experiment_report.json) | 1 / 0 |
| anybotics_anymal_c | 本轮新合成 | [通过](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/anybotics_anymal_c/environment/environment_check.json) | 未提交 | [402：尚未生成](../../runs/diagnostic/fixed-family-v1-followup-quadrupeds-20260908/anybotics_anymal_c/candidate/experiment_report.json) | 1 / 0 |

## 请求与 token

| 数据来源 | API 请求 | 成功回复 | HTTP 402 | 输入 token | 输出 token | 输入缓存命中 |
|---|---:|---:|---:|---:|---:|---:|
| 本轮 8 款 | 131 | 123 | 8 | 7,251,495 | 272,050 | 5,861,888 |
| 前轮 3 款，保留 | 196 | 196 | 0 | 12,462,525 | 380,130 | 10,097,280 |

输入缓存命中是输入 token 的子集。402 请求没有成功回复，也未提供 token 用量；表中没有据此推算账单金额。

独立的 Holistic 最小连通性检查返回 HTTP 401，错误为 `Invalid or revoked API key`。这次检查没有成功回复，没有更换实验提供商；其 1 次请求不计入上面的 131 次实验请求。[连通性记录](../../runs/diagnostic/holistic-connectivity-20260908/probe_flash.json)

## 独立的局部参考检查

下列参考结果只覆盖 A1 或 G5，不等于整套能力通过，不计作模型成功。历史完整参考结果单独保存在 JSON 的 `historical_reference`。

| 机器人 | 当前参考范围 | 通过数 |
|---|---|---:|
| kuka_iiwa_14 | A1 only; two unchanged cases | [2/2](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/reference_a1_control.json) |
| google_barkour_vb | G5 only; two disturbed-stance cases | [0/2](../../runs/diagnostic/fixed-family-v1-quad-environment-20260908/google_barkour_vb/environment_g5_report.json) |
| unitree_a1 | G5 only; two disturbed-stance cases | [2/2](../../runs/diagnostic/fixed-family-v1-quad-environment-20260908/unitree_a1/environment_g5_report.json) |
| anybotics_anymal_c | G5 only; two disturbed-stance cases | [0/2](../../runs/diagnostic/fixed-family-v1-quad-environment-20260908/anybotics_anymal_c/environment_g5_report.json) |

## 记录与限制

- 完整失败项、guard、worker、视频、调用分阶段统计、token 和连续会话证据见 [FOLLOWUP_RESULTS.json](FOLLOWUP_RESULTS.json)。
- 调用数及 token 标为“至少”时，只计已持久化阶段记录；当前活跃阶段未必包含在内。
- 同一会话的消息数与源码 revision 记录用于检查上下文延续；改善效果仍以实际 Harness 提交结果为准。
- JSON 将 API 中断的 `candidate_passed` 记为 null，同时保留原始 pipeline false、最后一次实际提交分数和错误阶段。
- 固定阈值、每机器人最多三次 Harness 提交及 Study/Generate/Repair 预算未因失败扩额。
- 原始历史报告和初轮 diagnostic_results.json 均未覆盖。
- 当前队列已停止。需先恢复账户余额；六款尚未取得回复的机器人可以重新开始。Franka、Barkour 的实时开发 worker 已关闭，不能声称可自动原会话续跑；继续这两款需另行明确恢复方式或新样本边界。本批未新增恢复功能。

## 已完成的修复与检查

- A5 返回计时：`0bcf783`；G5 恒定目标施力证据：`589fc6b`。
- KUKA 补偿及基本环境入口：共同提交 `0a3a768`；三款四足场景与复位：`66a446f`；标准与分批交接：`6a0c9a7`。
- 18 项本批聚焦检查通过，另有 7 项既有 worker 检查通过；其中全清单检查覆盖 11 款、108 个固定用例。不是全仓库测试声明。
- 八款基本环境检查均有真实 worker 和视频。KUKA 两个 A1 用例、三款四足的普通站立和六个 G5 用例均有单独真实运行记录。
- 当前会话及子代理直接实施，没有启动独立 Luna Max 会话。[各批文件范围和交接](LUNA_HANDOFF.md)
