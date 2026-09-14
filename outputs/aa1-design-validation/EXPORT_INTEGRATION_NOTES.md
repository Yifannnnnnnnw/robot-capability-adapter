# AA1 artifacts、EXPORT 与自动 DESIGN 接入

> **接入前的历史审阅记录。** 下文保留当时的代码状态和建议；其中“动态
> EXPORT/DEMO 未开启”“尚未声明 MCP 依赖”等描述不再代表当前实现。
> 当前接线、真实运行结果和待完成验证见
> [主线集成记录](../../AA1/docs/MAINLINE_INTEGRATION_20260914.md)。

这是现有代码审阅和下一批接入建议，不是已实施的动态 EXPORT，也不是正式实验协议。审阅基于本支线当前代码，并对照接入前的 ba5a0278 中对应实现。

## 原来定义在哪里

| 内容 | 定义或实现 |
|---|---|
| 工作区布局 `<workspace_root>/<robot_id>/` | `AA1/auto_adapter/orchestrator.py` 的 `SelfAssembleConfig` 文档和构造器 |
| `artifacts/` 存放惯例 | `AA1/README.md`；`AA1/scripts/run/synth_xmodel_robot.py` 配置 `artifacts/from_scratch_xrobot` |
| 各阶段产物和通过边界 | `_phase_study`、`_phase_generate`、`_phase_validate`、`_phase_export` 和 `_run_phase` |
| 每阶段的结果字段 | `PhaseResult`：name、ok、duration_sec、trace_path、artifact_paths、final_text、error、token_usage、metadata |
| 汇总字段 | `SelfAssembleResult.to_json()`；`_finish_result()` 保存 `summary.json` |
| 可读概览 | `_write_narrative()` 保存 `narrative.md` |
| ReAct 记录 | `agent/react_loop.py` 的 `TraceStep`：iter、thought、actions、observations、duration_ms、token_usage、stop_reason |
| EXPORT | `_EXPORT_SYSTEM` 和 `_phase_export()`，预期产物只有 `mcp_server.py` |
| 论文 ZIP | `AA1/scripts/build_supplementary.py`，挑选 driver、视频、canonical_results.yaml 并写 README |

这里没有找到一个覆盖全部运行文件的统一导出包 schema。代码注释引用了 `DESIGN.md`，但当前 AA1 树中未找到该文件；不把这条注释当成已读到的规范。

## 原来的记录与 EXPORT 各做什么

原来的标准流程在工作区内留下 `study.json`、`driver.py`、`validate_report.json`、各阶段 `traces/*.jsonl`、`recordings/`，完整运行还会有 `mcp_server.py`、demo 产物、`summary.json` 和 `narrative.md`。是否有某个文件取决于对应阶段有没有执行并写出，目录本身不保证每项存在。

旧 `TraceStep` 把单条工具 observation 截断至 2000 字符，并没有保存完整 system prompt、工具 schema 和每次累计 messages。本支线已补充旁路的 `*.messages.jsonl`，保存实际请求、回复和完整工具返回。旧紧凑 trace 仍保留。

EXPORT 是一个 ReAct 阶段：读取 driver 和 validation report，用 introspection 查看方法，再生成 FastMCP 包装文件。它不是归档全部记录的 ZIP 导出器。旧 prompt 让模型选择“planner 可能调用”的 skeleton 方法；这不足以保证只导出本次动态设计的接口。当前 `AA1/.venv` 中实际 `import mcp` 失败，`AA1/pyproject.toml` 也没有声明该依赖；旧 prompt 中“already installed”不能直接沿用。

from-scratch 主入口原来以生成 driver 和验证报告为主，没有与标准流程相同的 `_phase_export()`。

## 已接入的动态路径

`study → design → generate → validate` 已接入，两类 orchestrator 都使用同一 DESIGN 模块；动态 EXPORT/DEMO 仍未开启。

DESIGN 保存 capability_design.json（criteria 的主定义）、导出的 criteria.json、scene_cases.yaml、prepared scene MJCF、probe_report.json、capability_preparation.json 和完整 messages。动态 validator 每个 case 使用新进程，保存该候选副本、result.json、samples.json 和 video.mp4。

本次诊断为了保留失败尝试、复用真实 STUDY，分目录调用了实际阶段。`outputs/aa1-design-validation/` 是人工整理的审阅副本，不是框架已经实现的自动导出机制。

## 建议沿用的单次运行目录

```text
AA1/artifacts/<run_id>/<robot_id>/
  mjcf.xml
  study.json
  design/
    capability_design.json
    criteria.json
    scene_cases.yaml
    scenes/<scene_id>/scene.xml
    probe_report.json
    capability_preparation.json
    trace.jsonl
    trace.messages.jsonl
  driver.py
  traces/
    01_study.jsonl
    01_study.messages.jsonl
    02_generate.jsonl
    02_generate.messages.jsonl
    <repair traces when executed>
  validation/<attempt>/<case_id>/
    driver.py
    result.json
    samples.json
    video.mp4
  validate_report.json
  summary.json
  narrative.md
  mcp_server.py  # 仅在未来动态 EXPORT 真正执行后
```

继续由 `workspace_root` 控制位置，不把临时目录写死。reference 检查应单独保存，注明检查了哪些 case；不要把 reference driver 放进候选的模型输入材料。

## 下一批最小修改

1. **补全阶段产物登记。** `_phase_design()` 的 `PhaseResult.trace_path` 应指向真实 DESIGN trace，artifact_paths 登记 design、criteria、case YAML 和 probe；当前 summary 的 metadata 有这些路径，但 narrative 还没有完整展示。
2. **补全可读记录。** `_write_narrative()` 从 validation report 的准确路径列出各 case 指标、标准、结果和视频；当前仅扫描 `recordings/*.mp4`，会漏掉动态 validation 的嵌套视频。继续复用 `summary.json`，不新增另一套状态文件。
3. **动态 EXPORT 使用本次 design。** `_phase_export()` 的新模式接收内存 design 和当前 driver。注册的控制工具严格对应 `capabilities[].method_name`，请求约束来自该 capability 的 request_schema；需要的观察接口单独明确。不要通过遍历所有继承方法任意扩展工具集，也不要让 EXPORT 重写 criteria。
4. **包装转发保持透明。** MCP 调用最终转成 `robot.<method_name>(request=...)`；使用者能够看到请求字段、单位、坐标系和失败返回。server 创建/绑定的运行场景与 validation case 场景区分清楚，后续使用者可以提供自己的场景；验证 case 的 fresh 规则不等于每个连续控制调用都重置机器人。
5. **记录和搬运由 Python 负责。** 若仅在本机仓库中使用，工作区即可；若需要搬到另一台机器，再复制实际 MJCF include/mesh/texture 依赖并改用包内相对路径。当前 `mjcf.xml` 是源模型软链接，prepared scene 仍引用本机资源绝对路径，单独复制 XML 不是可搬运包。skeleton-assisted driver 还依赖 AA1 skeleton 代码，不能称为独立单文件 driver。
6. **启用顺序。** 先完成上述接口和依赖检查，再把 `export` 加入动态阶段列表及 `stop_after`。保存失败运行的记录不以数值检查全通过为条件；记录中的结果保持真实。

## 最小检查

- 一次临时 artifacts 工作区中，所有阶段记录指向本次文件，DESIGN 和各 case 视频能从 narrative 打开。
- 导出的 MCP 工具名与本次 capability 方法一一对应，参数 schema 一致，不混入旧 catalog 的能力；缺方法/坏参数返回错误。
- 真实导入生成的 server，并通过 MCP 客户端调用一个已有动态方法，核对它转发到了当前 driver/真实 MuJoCo 实例。先补齐声明的 MCP 依赖。
- 只有实际实现可搬运包时，才增加换目录后重载 MJCF 和一次控制调用的检查；本批不预建通用打包系统。

## 数值标准的后续修订

用户明确认为当前容差本身太大；5 cm 不是定稿。下一批人工任务要求应与原始来源 scoring 区分，记录标准版本/输入文件即可，不把改写后的数值声称为 Meta-World 原数值。已有诊断保留原标准和结果；新标准下的结果另行运行和记录。
