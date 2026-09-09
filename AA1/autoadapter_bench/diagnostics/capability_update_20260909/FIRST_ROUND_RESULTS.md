# capability update 2026-09-09：第一轮结果

本文记录 15 个机器人在 AA1 直接 MuJoCo/Holistic 诊断中的已保存结果。表中的 `wall` 是对应 Holistic 会话记录的墙钟时间，`tokens` 是报告的输入/输出 token（`in/out`）；两者包含工具和重复上下文，不能当作纯推理时间。Holistic 没有保存发票级的实际生成费用，`actual_generation_cost_usd=null` 表示未知，不表示零。

表中 Framework 和 task 都指模型生成 driver 的运行。参考控制单独统计，见文末；模型文字总结不覆盖 trusted physics verdict。视频路径均相对于本文件所在目录。

| 机器人 | 初次 Sonnet 生成结果（本表采用的尝试；wall；tokens） | Framework 结果与范围 | Task 结果与视频 |
|---|---|---|---|
| SO101 | 成功，`generated_resume1/so101/driver.py`；190.6 s；308,230/12,966。旧 `generated/so101` 在写 driver 前中断。 | **6/10 条件**，A1–A3 通过，A4–A5 失败；全套 Framework 否。 | A1 物理 task **通过**，但 `validated_driver_task_ok=false`（全套 Framework 否）。[generated_resume1/so101/.../video.mp4](generated_resume1/so101/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/so101-a1-nominal/video.mp4) |
| PiPER | 成功，`generated_retry1/piper/driver.py`；266.0 s；544,048/21,786。旧 `generated/piper` 生成无 driver。 | 表中采用同一 driver 的 `sampling_recheck`：**2/10 条件**；原始记录为 7/10，两份均保留；全套 Framework 否。 | A1 可信物理 verdict **失败**（hold 仅约 0.09 s），虽有文本成功总结。[generated_retry1/piper/.../sampling_recheck/video.mp4](generated_retry1/piper/task_traces/task_sampling_recheck/A1_diagnostic_t0_physics/piper-a1-nominal/video.mp4) |
| Franka Panda | Sonnet 生成成功，使用 `generated/franka/driver.py`；242.7 s；485,090/20,045。 | 表中采用 `sampling_recheck`：**7/10 条件**，同一生成 driver 重新执行真实物理检查；不是新生成。 | A1 sampling recheck 物理 task **通过**，但全套 Framework 否。[generated/franka/.../sampling_recheck/video.mp4](generated/franka/task_traces/task_sampling_recheck/A1_diagnostic_t0_physics/franka_panda-a1-nominal/video.mp4) |
| UR5e + Robotiq 2F-85 | 成功，`generated/universal_robots_ur5e_robotiq_2f85/driver.py`；372.7 s；547,570/19,660。 | 最终全十条件范围 **5/10**；A1、A2 两项及 A5 boundary 通过，A3、A4 两项及 A5 nominal 失败；全套 Framework 否。 | A1 nominal 物理 task **通过**，但 `validated_driver_task_ok=false`（全套 Framework 否）。[generated/universal_robots_ur5e_robotiq_2f85/.../video.mp4](generated/universal_robots_ur5e_robotiq_2f85/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/universal_robots_ur5e_robotiq_2f85-a1-nominal/video.mp4) |
| xArm7 | 采用 `generated_resume1/ufactory_xarm7`；生成 **失败**，`max_iters=22`，无 driver；168.5 s；414,401/16,878。旧 `generated/ufactory_xarm7` 也在写 driver 前中断。 | `not_run`，上游没有 driver。 | `not_run`，无视频。 |
| KUKA iiwa14 | driver 生成成功；193.6 s；432,534/14,949。 | **0/8 条件**；全套 Framework 否。 | A1 物理 task **失败**；[generated/kuka_iiwa14/.../video.mp4](generated/kuka_iiwa14/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/kuka_iiwa_14-a1-nominal/video.mp4) |
| Kinova Gen3 + Robotiq 2F-85 | 生成 **失败**，`max_iters=22`，无 driver；237.5 s；505,286/14,558。 | `not_run`，生成阶段耗尽；无 Framework repair。 | `not_run`，无视频。 |
| LEAP Hand | driver 生成成功；316.5 s；343,795/13,538。 | 仅本轮单任务范围，`driver_build + four_fingertip_reach` **2/2 通过**。 | four-fingertip reach 物理 task **通过**。[generated/leap_hand/.../video.mp4](generated/leap_hand/recordings/task_live_diagnostic/four_fingertip_reach_t0.mp4) |
| ALOHA 2 | driver 生成成功；191.5 s；480,232/14,314。 | 仅本轮单任务范围，`driver_build + synchronized_dual_reach_hold` **2/2 通过**。 | synchronized dual reach-hold 物理 task **通过**。[generated/aloha_2/.../video.mp4](generated/aloha_2/recordings/task_live_diagnostic/synchronized_dual_reach_hold_t0.mp4) |
| Hello Robot Stretch 2 | driver 生成成功；318.5 s；342,020/13,463。 | 仅本轮单任务范围，`driver_build + base_then_ee_reach` **2/2 通过**。 | base-then-EE reach 物理 task **通过**。[generated/hello_robot_stretch_2/.../video.mp4](generated/hello_robot_stretch_2/recordings/task_live_diagnostic/base_then_ee_reach_t0.mp4) |
| Skydio X2 | from-scratch driver 生成成功；1,354.9 s；452,449/21,958。 | 仅本轮 from-scratch 范围，**7/7 tests 通过**。 | takeoff-to-0.5 m 物理 task **通过**。[generated_from_scratch/skydio_x2/.../video.mp4](generated_from_scratch/skydio_x2/recordings/task_live_diagnostic/takeoff_to_05m_t0.mp4) |
| H1 | from-scratch Sonnet 生成 **失败**，`max_iters=40`，无 `driver_from_scratch.py`；362.2 s；1,099,263/36,756。 | `not_run`。 | `not_run`，无视频。 |
| Go2 | driver 生成成功；241.0 s；429,869/15,784。 | **0/10 条件**；全套 Framework 否。 | G1 diagnostic 物理 task **失败**。[generated/go2/.../video.mp4](generated/go2/task_traces/task_live_diagnostic/G1_diagnostic_t0_physics/go2-g1-nominal/video.mp4) |
| Unitree A1 | 生成 **失败**，`max_iters=22`，无 driver；109.1 s；404,817/10,396。 | `not_run`，没有模型 driver。 | `not_run`，无视频。native A1 参考控制另计。 |
| ANYmal C | driver 生成成功；181.2 s；453,651/19,806。 | 只检查 **G4 nominal canary（1/10）**，canary **失败**；没有全 driver validation。 | G4 物理 task **失败**；[generated/anymal_c/.../video.mp4](generated/anymal_c/task_traces/task_live_diagnostic/G4_diagnostic_t0_physics/anymal_c-g4-nominal/video.mp4) |

因此，LEAP、ALOHA、Stretch、Skydio 的表内诊断链各自在其有限 scope 内完成；这不扩展为全部能力或正式实验的通过率结论。对 SO101、PiPER、Franka、UR5e，表内分别保留了指定的 resumed/retry/sampling/final 选择，同时保留失败的早期或重采样记录：

- Franka 初始 `generated/franka` 的原始采样记录为 3/10；`sampling_recheck` 用同一 driver 得到 7/10，表中采用后者且标明它不是新生成。
- PiPER 的 `generated_retry1` 原始 Framework 记录为 7/10；同一 driver 的 `sampling_recheck` 记录为 2/10，二者均不合并为一个“最佳”结果。
- UR5e 的 A1-only preliminary canary 通过，但最终全十条件为 5/10；表中不以 canary 替代最终范围。

## 独立参考控制

参考控制不使用模型生成 driver，也不进入上表的生成、Framework 或 task 结果。十个 capability reference suites（SO101、PiPER、Franka、xArm7、KUKA、Kinova、UR5e、Go2、Unitree A1、ANYmal C）合计 **86/98** 条件通过。剩余 **12** 条是 native Unitree A1 与 ANYmal C 的 G1–G3 条件失败（每个机器人 3 个 capability、nominal/boundary 两个条件）；native G4/G5 的参考校准另有通过记录。证据见 `AA1/autoadapter_bench/diagnostics/capability_calibration/` 与 `native_quad_reference_review.json`。这 86/98 是参考控制计数，不是 15 个模型运行的 pass rate。

## 先完成的高阶模型诊断

下列为整理首轮表时已完成的高阶诊断。后续有上限的高阶尝试另存 `HIGHER_MODEL_RESULTS.md`，均不替换上表 Sonnet 记录：

- **PiPER Opus**：`generated_opus48_retry1/piper` 的 Framework 为 **8/10**，A1 物理 task **通过**；A4 nominal/boundary 失败。视频在 `generated_opus48_retry1/piper/task_traces/task_live_diagnostic/A1_diagnostic_t0_physics/piper-a1-nominal/video.mp4`。
- **H1 Opus**：`generated_from_scratch_opus48/h1` 的 Framework 为 **7/8**，stand-balance 2 s task **通过**；视频在 `generated_from_scratch_opus48/h1/recordings/task_live_diagnostic/stand_balance_2s_t0.mp4`。
- **xArm7 Opus**：`generated_opus48_retry1/ufactory_xarm7` 在 study 阶段未写出 `study.json`，因此 generation/Framework/task 均未运行。
- **Go2 Opus**：`generated_opus48_retry1/go2` 虽写出生成文件，但 Framework build 因缺少模块级 `driver.build()` 失败，task 未运行。

## Git 边界

提交 `91e3f75f` 已同时包含 UR5e capability batch 与 Kinova baseline evidence；该提交是混合批次，本文不 amend、squash、rebase 或重写它。
