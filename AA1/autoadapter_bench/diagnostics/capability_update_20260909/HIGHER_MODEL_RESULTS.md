# 有上限的高阶模型诊断结果

首轮 Sonnet 结果见 [FIRST_ROUND_RESULTS.md](FIRST_ROUND_RESULTS.md)。本表单独保存未整体验证通过的 11 台各一次高阶重试；请求模型标识为 `eu.anthropic.claude-opus-4-8`，通过 Holistic API 调用。这里记录的是请求标识，不推断网关内部实际路由。

每次骨架生成最多 22 轮模型/工具交互；H1 从零生成最多 40 轮。Framework 后的自动 repair 上限为 0，没有人工补写候选驱动，也没有追加运行直到通过。Study/Generate 内的模型调试属于这一次有上限的生成过程。

| 机器人 | 生成产物 | Framework | 指定任务的物理结果 |
|---|---|---|---|
| SO-101 | 已写出文件 | 0/10 条件通过；全套未通过 | 失败；[视频](generated_opus48_retry1/so101/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/so101-a1-nominal/video.mp4) |
| PiPER | 已写出文件 | 8/10 条件通过；全套未通过 | 通过；[视频](generated_opus48_retry1/piper/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/piper-a1-nominal/video.mp4) |
| Franka | 已写出文件 | 7/10 条件通过；全套未通过 | 通过；[视频](generated_opus48_retry1/franka/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/franka_panda-a1-nominal/video.mp4) |
| KUKA iiwa14 | 已写出文件 | 缺少 build()；构建失败，物理条件未运行 | 未运行 |
| xArm7 | 未产出驱动 | Study 产物未落在本地；后续未运行（工具路由缺陷，已修复） | 未运行 |
| Kinova + Robotiq | 已写出文件 | 6/10 条件通过；全套未通过 | 通过；[视频](generated_opus48_retry1/kinova_gen3_robotiq_2f85/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/kinova_gen3_robotiq_2f85-a1-nominal/video.mp4) |
| UR5e + Robotiq | 已写出文件 | 5/10 条件通过；全套未通过 | 通过；[视频](generated_opus48_retry1/universal_robots_ur5e_robotiq_2f85/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/universal_robots_ur5e_robotiq_2f85-a1-nominal/video.mp4) |
| Go2 | 已写出文件 | 缺少 build()；构建失败，物理条件未运行 | 未运行 |
| Unitree A1 | 已写出文件 | 缺少 G1–G5 必需方法；物理验证未运行 | 未运行 |
| ANYmal-C | 已写出文件 | 仅 G4 nominal：0/1；全套 10 条件未验证 | 失败；[视频](generated_opus48_retry1/anymal_c/task_traces/task_live_diagnostic/G4_diagnostic_t0_physics/anymal_c-g4-nominal/video.mp4) |
| H1 | 已写出文件 | 7/8 检查通过；行走失败 | 通过，真实站立 2.002 s；[视频](generated_from_scratch_opus48/h1/recordings/task_verdict_recheck/stand_balance_2s_t0.mp4) |

这 11 次重试均未取得全套 Framework 通过。部分指定任务通过，保留为局部物理结果；不能提升为完整能力验证通过。首轮已通过本轮规定范围的 LEAP、ALOHA 2、Stretch 2、Skydio X2 未追加高阶尝试。

生成过程与任务的 trace、视频路径和指标保存在各尝试目录。生成墙钟时间、输入/输出 tokens 汇总见 [generation_usage_summary.json](generation_usage_summary.json)；Holistic 实际费用未知，任务 JSON 的旧价格表估算不能充当实际生成费用。

这些是接入诊断，不是正式模型比较：采样时序、Go2 公开策略默认姿态、local 工具路由在接入过程中有定向修复。修复和旧结果均保留；没有按生成结果放宽评分门槛。A1/ANYmal-C 的 G1–G3 参考控制仍有 12 个失败条件，所以两台生成驱动只做了已校准 G4 的物理诊断，并检查必需方法是否存在。

Git 已逐批保存本地提交。父仓库 GitHub 推送在已核对目的地后仍被自动审批拒绝，等待用户对具体仓库和分支的明确授权；本地证据完整。
