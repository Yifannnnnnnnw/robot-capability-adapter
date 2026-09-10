# A4 修正与旧轨迹复评分

本批修正公开规范、评分和失败反馈的一致性，保留所有数值门槛、场景、动力学和历史结果。没有新模型调用，费用不适用；参考物理检查与模型生成结果分开记录。

## 代码与检查

- `555a1137`：七台机械臂公开 A4 的准备阶段、实际起点、接近与接触速度、连续窗口和时限语义。模型方法、请求 schema、原有 criteria 数值及其他能力保持原样；实际生成上下文已核对。
- `837b07cf`：同一真实世界逐步采集最近碰撞面相对速度与首次接触法向速度，支持已有匿名几何名称。观测只读取 canonical model/data。
- `3826be63`：评分按准备阶段末端实测位置计算方向约束，按历史最大进度检查回退；评分和反馈共用 A4 判定函数，缺少新观测明确失败。
- `3160c289`：即使未形成完整接触保持窗口，反馈仍报告实际最长接触时长。旧实现的 `KeyError('longest_target_contact_s')` 已复现，修复后对应检查通过。
- `716abbc5`：xArm7 参考控制在当前姿态已满足准备点容差时保持实测姿态，避免重新居中造成约 2.738 mm 回退。只修改参考 A4 的动作顺序。

审查命令：

```sh
cd AA1
.venv/bin/python -B -m pytest -q \
  auto_adapter/tests/test_a4_metrics.py \
  auto_adapter/tests/test_a4_observations.py \
  auto_adapter/tests/test_capability_physics.py
```

最终结果为 **16 passed**。检查覆盖实际起点、停留不足、接触窗口与反馈一致、回退、速度观测、缺观测、真实 MuJoCo 接触速度与世界一致性。没有扩展全仓库测试。

## 已生成候选的离线复评分

`rescore/rescore_results.json` 保存完整分析命令、原始路径、重建核对、原分数、新分数及具体失败条件。分析仅将保存的 qpos/qvel/ctrl 与目标位置恢复到独立计算数据中调用 `mj_forward`；没有执行 driver 或 `mj_step`。仅在重建位置与接触对匹配原始记录时补算速度观测。

本轮 Franka、SO-101、KUKA 的 18 条已执行 A4 条件均可复评分，二元分数全部与原结果一致：1 条通过，17 条失败。PiPER 先前已通过驱动的 2 条 A4 也仍然通过；没有重新生成或执行 PiPER。

最近候选的具体失败原因：

| 候选 | nominal | boundary |
|---|---|---|
| SO-101 repair 2 | 准备阶段 0.098 s，要求 0.100 s | 0.092 s，要求 0.100 s |
| KUKA repair 3 | 准备阶段 0.076 s，要求 0.100 s | 同左 |
| Franka repair 3 | 准备阶段通过；接触仅连续 0.008 s | 准备阶段通过；接触仅连续 0.002 s |

复评分不是新生成结果，也没有重写任何 `summary_<robot>.json`、原始验证报告或 repair 次数。

## 参考检查与限制

14 条旧参考 A4 中，8 条的状态和派生观测可一致重建：Kinova 与 UR5e 共 4 条通过，SO-101 boundary 通过；SO-101 nominal 表面相对速度 0.035137 m/s 超过 0.030 m/s；xArm7 两条因实际回退约 2.738 mm 失败。

更早的 Franka、KUKA、PiPER 共 6 条参考记录，其保存的派生位置与 qpos 时间点存在偏差；没有将这些混合时间观测用于新规则复评分，结果明确记为 unavailable。这些历史结果保持原样。

xArm7 的参考动作顺序修正后，使用官方 `run_capability_case` 进行了真实物理检查：

| 条件 | 结果 | 实际物理时长 | 步数 |
|---|---|---:|---:|
| nominal | PASS | 0.846 s | 423 |
| boundary | PASS | 1.408 s | 704 |

证据在 `xarm_reference_after_reset/`，包括逐条件报告、指标、物理 trace 与 video。两段视频均完整解码，新的速度观测覆盖所有样本，真实世界与执行器检查通过。

`xarm_reference/` 保留第一次诊断的调用设置错误：控制器在 Framework reset 之前构造，内部关节目标与重置状态不一致，两个条件失败。后一次将构造放入 reset 后的 execute 回调，保持同一 model/data。前一次没有计作正控通过或模型生成结果。

SO-101 nominal 参考在修正后速度标准下的失败仍是未解决限制；没有为其放宽条件。接下来使用现有 Stage 1 入口启动尚未运行的 xArm7，完整验证并最多三次 repair。A4 修正前后的记录分别关联各自代码，不混作同一正式实验分母。
