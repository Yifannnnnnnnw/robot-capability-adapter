# AA1：15 台机器人接入与诊断记录

本轮沿用 Direct-MuJoCo，保留 LEAP。7 台单臂、3 台四足及 LEAP、Stretch 2、ALOHA 2 使用骨架；H1、Skydio X2 沿用从零生成。这里记录接入和诊断，不构成正式实验协议或结果分母。

## 环境与外部阻塞

2026-09-09 本机实际使用 Python 3.13.13、MuJoCo 3.13.0、NumPy 2.5.3、Anthropic SDK 1.4.0。系统另有 Python 3.13.0a5，不能使用它创建本项目环境。

```bash
cd AA1
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -e . pytest
```

macOS 视频渲染需要可用的 CoreGraphics 连接；本次使用获准的本机图形访问完成渲染。沙箱内会报 `invalid CoreGraphics connection`。

Bedrock 模型仍为 `us.anthropic.claude-sonnet-4-6`，区域 `us-east-1`，使用原有 AWS 配置。AgentCore 会话创建成功。真实生成调用被 AWS 返回的 `403 INVALID_PAYMENT_INSTRUMENT` 拒绝，需要修复账户支付方式/Marketplace 订阅。没有改换供应商，也没有把参考驱动当成模型生成结果。

真实生成尝试保存在 [LEAP 调用结果](../artifacts/diagnostics/aa1_15_20260909/generated_attempt_2/leap_hand/result.json)、[Stretch 调用结果](../artifacts/diagnostics/aa1_15_20260909/generated_attempt_3/hello_robot_stretch_2/result.json) 和 [ALOHA 调用结果](../artifacts/diagnostics/aa1_15_20260909/generated_attempt_3/aloha_2/result.json)，均在 Study 阶段返回同一 403，生成和验证阶段未运行。最早一次 LEAP 调用暴露 SDK 参数兼容问题；已根据 [Anthropic 官方迁移说明](https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md) 将 Sonnet 的 temperature 通过 `extra_body` 传入，之后已实际到达 Bedrock。旧逻辑在 API 拒绝时留下空 trace；现已修复，Stretch、ALOHA 的 `traces/01_study.jsonl` 含真实 `invoke_error` 记录。

## 已执行的真实检查

六个新资产入口均通过模型加载、`mj_forward` 和短步进检查。15 台指定机器人的 MuJoCo 真值绑定均已加载并逐步采样核实，见 [15 台资产与真值检查](../artifacts/diagnostics/aa1_15_20260909/asset_truth_checks.json)。新增 Kinova、xArm7、UR5e + Robotiq 分别使用原有 AA1 单臂骨架完成真实到达检查。资产加载和短步进不代表任务通过。

| 参考正控 | 实际运动 | 误差与时长 |
|---|---|---|
| LEAP 四指到达 | 每指位移 6.84–8.76 cm | 聚合误差 5.19 mm；门槛 8.944 mm |
| Stretch 底盘后机械臂 | 底盘位移 9.23 cm；臂运动阶段底盘漂移 1.29 mm | 末端误差 1.48 mm |
| ALOHA 左右共同保持 | 两侧末端分别移动约 2.46、2.43 cm | 保持 0.5 s；期间最大误差 16.59、17.11 mm |

固定目标写在三个新任务 YAML 中。LEAP 是借鉴 HandReach 的四指适配任务，不是官方 Shadow HandReach。目标来自独立 FK 的安全姿态或经真实控制校准的可达位置。评分读取 zoo 配置指定的 MuJoCo 几何、site、body 和关节；不会依据生成驱动的自报结果为新形态评分。

参考正控结果与逐步真值 trace 位于 `artifacts/diagnostics/aa1_15_20260909/reference/{leap_hand,hello_robot_stretch_2,aloha_2}/`。随后三个参考驱动均经现有 `SelfAssemble._phase_validate_framework()` 完成真实任务回放、MuJoCo 评分与 MP4 输出，分别为 2/2 检查通过。它们明确标注 `model_generated: false`，用于校准与验证接入链，不是新生成驱动结果。

| Framework 参考检查 | 报告 | 本机视频 |
|---|---|---|
| LEAP | [真值评分](../artifacts/diagnostics/aa1_15_20260909/framework_reference/leap_hand/validate_report.json) | [MP4](../artifacts/diagnostics/aa1_15_20260909/framework_reference/leap_hand/recordings/framework_four_fingertip_reach_1788972190253611000.mp4) |
| Stretch | [顺序与到达评分](../artifacts/diagnostics/aa1_15_20260909/framework_reference/hello_robot_stretch_2/validate_report.json) | [MP4](../artifacts/diagnostics/aa1_15_20260909/framework_reference/hello_robot_stretch_2/recordings/framework_base_then_ee_reach_1788972193561168000.mp4) |
| ALOHA | [两侧共同保持评分](../artifacts/diagnostics/aa1_15_20260909/framework_reference/aloha_2/validate_report.json) | [MP4](../artifacts/diagnostics/aa1_15_20260909/framework_reference/aloha_2/recordings/framework_synchronized_dual_reach_hold_1788972199689240000.mp4) |

上述报告均列出完整逐步 trace 路径。Stretch 最初正控的默认视角被桌子遮挡，现有通用相机已改为读取模型视角并跟踪底盘；上表链接是已核对可见底盘和机械臂的后续视频。MP4 沿用仓库既有忽略规则，保存在本机；JSON/JSONL 证据随代码提交。

定向检查共 **34 passed in 5.18s**，涵盖资产、3 台新增单臂、3 种新骨架、形态拒绝、SDK 请求参数、H1 物理时长、工具参数与评分。回放异常、空动作的往返任务、未推进仿真的 H1 以及新形态关键部位未动，均有明确命名的回归 fixture；评分修复前选定的 4 项回归实际失败，修复后全部通过。fixture 结果不作为机器人运行证据。

复核本批涉及的检查（不执行旧的全仓库模型调用测试）：

```bash
.venv/bin/python -m pytest -q \
  auto_adapter/tests/test_added_assets.py \
  auto_adapter/tests/test_added_arm_controls.py \
  auto_adapter/tests/test_hand_fingertip_dls.py \
  auto_adapter/tests/test_stretch_mobile_manipulation.py \
  auto_adapter/tests/test_bimanual_serial_dls.py \
  auto_adapter/tests/test_framework_verdict.py \
  auto_adapter/tests/test_bedrock_sdk_compat.py \
  auto_adapter/tests/test_h1_framework_duration.py \
  auto_adapter/tests/test_new_shape_tools.py \
  auto_adapter/tests/test_bench_verdict.py
```

## 15 台逐项诊断情况

下表的“生成”和“任务”专指新生成驱动闭环。参考正控不会填入这些列。

| 机器人 ID | 路线 | 选定诊断任务 | 新驱动生成 | 新驱动任务结果 |
|---|---|---|---|---|
| so101 | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| piper | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| franka | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| kuka_iiwa14 | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| ufactory_xarm7 | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| kinova_gen3_robotiq_2f85 | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| universal_robots_ur5e_robotiq_2f85 | 单臂骨架 | move_x_plus_5cm | 未尝试：Bedrock 阻塞 | 未运行 |
| go2 | 四足骨架 | sit_then_stand_to_nominal | 未尝试：Bedrock 阻塞 | 未运行 |
| unitree_a1 | 四足骨架 | sit_then_stand_to_nominal | 未尝试：Bedrock 阻塞 | 未运行 |
| anymal_c | 四足骨架 | sit_then_stand_to_nominal | 未尝试：Bedrock 阻塞 | 未运行 |
| leap_hand | 手部骨架 | four_fingertip_reach | Study 调用 403，未生成 | 未运行 |
| hello_robot_stretch_2 | 移动机械臂骨架 | base_then_ee_reach | Study 调用 403，未生成 | 未运行 |
| aloha_2 | 双臂骨架 | synchronized_dual_reach_hold | Study 调用 403，未生成 | 未运行 |
| h1 | 从零生成 | stand_balance_2s | 未尝试：Bedrock 阻塞 | 未运行 |
| skydio_x2 | 从零生成 | takeoff_to_05m | 未尝试：Bedrock 阻塞 | 未运行 |

## 恢复运行

修复 AWS 支付问题后，使用新目录生成，勿复用历史驱动。13 台骨架路线使用现有 `SelfAssemble` API，例如：

```python
import json
from pathlib import Path
from auto_adapter import SelfAssemble, SelfAssembleConfig
from auto_adapter.robot_catalog import find_robot_definition

robot = find_robot_definition("leap_hand")
cfg = SelfAssembleConfig(
    robot_id=robot["id"], mjcf_path=Path(robot["mjcf"]),
    workspace_root=Path("artifacts/diagnostics/aa1_15_retry"),
    expected_robot_class=robot["class"],
    bedrock_model="us.anthropic.claude-sonnet-4-6",
)
with SelfAssemble(cfg) as runner:
    result = runner.run(stop_after="validate")
    (runner.workspace / "result.json").write_text(json.dumps(result.to_json(), indent=2))
    # stop_after 会将 export/demo 标为未运行，不能用聚合 ok 判断前三阶段。
    assert all(p.ok for p in result.phases[:3]), result.to_json()
```

随后用同一个新驱动进入现有评测入口；本轮明确 `--n-trials 1`：

```bash
.venv/bin/python autoadapter_bench/eval.py \
  --robot leap_hand --suites simple --tasks four_fingertip_reach --n-trials 1 \
  --model us.anthropic.claude-sonnet-4-6 --region us-east-1 \
  --driver-workspace artifacts/diagnostics/aa1_15_retry/leap_hand \
  --output artifacts/diagnostics/aa1_15_retry/leap_hand/task_result.json
```

H1 和 Skydio 使用现有从零生成入口，选取未用过的 run_n，以免覆盖旧结果。例如 H1：

```bash
.venv/bin/python scripts/run/synth_xmodel_robot.py \
  sonnet46diag20260909 us.anthropic.claude-sonnet-4-6 h1 assets/mjcf/h1/scene.xml 1
```

完成后，将相应的新 workspace 明确传给 `eval.py --driver-workspace`。H1 的站立验证与任务评分均要求实际推进物理仿真，并检查持续站立状态；仅返回成功或停留在初始站姿不会通过。

本轮不运行掌内转块、移动抓放、双臂交接，也不迁移 20 项任务库。未改论文、AA2 源资产及历史结果。

## 提交与当前限制

以下 commit 均位于独立的 `AA1` Git 仓库。Luna Max 在独立上下文中实施资产及三类骨架、评分和工具的小批次；本会话审查、运行验证并直接完成必要的定向修复。

| Commit | 内容 |
|---|---|
| `96d0c23` | Framework 拒绝未知骨架、行为失败和异常 |
| `f12577f` | 从可信 zoo 传递预期形态 |
| `08daf93` | 六个资产配置与完整依赖、许可证 |
| `7cbf572` | LEAP DLS 与真实正控 |
| `e56b1c6` | 15 台 MuJoCo 真值绑定与逐步采样 |
| `ff486bf` | 当前 Anthropic SDK 参数兼容 |
| `27e407b` | Stretch 底盘、机械臂与真实正控 |
| `9197e2b` | MuJoCo/NumPy 关节类型比较修复 |
| `26bbf87` | H1 验证要求实际仿真时长 |
| `067928e` | ALOHA 共享世界双臂与真实正控 |
| `6c7492e` | 三类 TaskPlanner 工具、显式 arm 参数与相机修复 |
| `acc71dd` | 三类固定任务、真值评分与误通过回归 |
| `86fb6e1` | 三类生成提示、Framework 任务/视频正控及 ALOHA 安全 home |
| `19add57` | 真实模型拒绝 trace 与捕获钩子清理 |

接入代码和上述正控已经完成；**15 台使用新生成驱动的诊断任务尚未完成**。当前阻塞是 AWS 账户支付/Marketplace 模型访问，不能用已有驱动、参考驱动或空跑补足。恢复访问后仍需逐台执行生成 → Framework → TaskPlanner 评测 → 视频，并分别填入生成与任务结果；由诊断发现的控制失败还需当时处理。正式实验样本量、重复次数及协议尚未确定，本轮没有启动正式实验。
