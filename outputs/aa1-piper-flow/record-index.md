# AA1 Piper 原始流程记录索引

此索引由 `export_records.py` 从实际诊断记录生成。每个 turn 目录中的 JSON 保留模型边界记录；缺少的事件保持缺少，不补写响应或工具结果。

- 运行根目录：`/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy`
- 工作区：`/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper`
- 公共 XML：已复制到 `records/source/`
- 提交标识：initial pipeline integration commit `6d4e8162`；run code HEAD `7f37f27e67cd0fb10856b2d957df96cbcfd32ccf`（同一 parent repository 中的提交；优先取 run report，缺失时使用 fallback）
- 记录边界：诊断 wrapper 在 `ReactLoop._invoke_with_retry` 处保存 system/tools/max_tokens/messages/gateway_messages；不会保存 transport headers、credentials 或环境变量。

本次复用了此前真实运行的 study 和 7 轮原始消息，未重新调用 STUDY 模型；见 [study-reuse.json](records/source/study-reuse.json)。

## 每轮文件

每个真实请求对应 `request.json`；存在的模型返回对应 `response.json`，调用错误也原样放入该文件；没有后续事件则不创建响应内容。`tool-results.json` 优先从下一轮 request 的新增消息提取完整 tool result，同时保留 trace observations，并明确其可能已被框架截断。`trace.json` 是同一轮的原始 ReAct trace。

| 阶段 | 消息记录 | trace | 轮数 | 逐轮目录 |
|---|---|---|---:|---|
| STUDY：真实 MJCF 研究 | `workspace/piper/traces/01_study.messages.jsonl` | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/traces/01_study.jsonl` | 7（trace 7） | [turn-000](records/study/turn-000/request.json)；初始快照：[initial-system-prompt.txt](records/study/initial-system-prompt.txt)、[initial-user-prompt.txt](records/study/initial-user-prompt.txt)、[initial-framework-messages.txt](records/study/initial-framework-messages.txt)、[initial-gateway-messages.txt](records/study/initial-gateway-messages.txt) |
| TGCD：能力设计与 criteria | `workspace/piper/capability_inputs/trace.messages.jsonl` | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/trace.jsonl` | 3（trace 3） | [turn-000](records/tgcd/turn-000/request.json)；初始快照：[initial-system-prompt.txt](records/tgcd/initial-system-prompt.txt)、[initial-user-prompt.txt](records/tgcd/initial-user-prompt.txt)、[initial-framework-messages.txt](records/tgcd/initial-framework-messages.txt)、[initial-gateway-messages.txt](records/tgcd/initial-gateway-messages.txt) |
| GENERATE：driver.py 生成 | `workspace/piper/traces/02_generate.messages.jsonl` | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/traces/02_generate.jsonl` | 23（trace 23） | [turn-000](records/generate/turn-000/request.json)；初始快照：[initial-system-prompt.txt](records/generate/initial-system-prompt.txt)、[initial-user-prompt.txt](records/generate/initial-user-prompt.txt)、[initial-framework-messages.txt](records/generate/initial-framework-messages.txt)、[initial-gateway-messages.txt](records/generate/initial-gateway-messages.txt) |

## 每阶段实际工具清单

| 阶段 | 首轮 request 中记录的 tool 名称 |
|---|---|
| STUDY：真实 MJCF 研究 | `write_file`、`read_file`、`local_exec` |
| TGCD：能力设计与 criteria | `read_file`、`write_file` |
| GENERATE：driver.py 生成 | `write_file`、`read_file`、`list_skeletons`、`inspect_skeleton`、`local_exec` |

逐轮入口：
- `study turn-000`：[request](records/study/turn-000/request.json), [response](records/study/turn-000/response.json), [tool-results](records/study/turn-000/tool-results.json), [trace](records/study/turn-000/trace.json)； framework messages=1，gateway messages=2，响应=response，tool-results 来源=next_request。
- `study turn-001`：[request](records/study/turn-001/request.json), [response](records/study/turn-001/response.json), [tool-results](records/study/turn-001/tool-results.json), [trace](records/study/turn-001/trace.json)； framework messages=3，gateway messages=5，响应=response，tool-results 来源=next_request。
- `study turn-002`：[request](records/study/turn-002/request.json), [response](records/study/turn-002/response.json), [tool-results](records/study/turn-002/tool-results.json), [trace](records/study/turn-002/trace.json)； framework messages=5，gateway messages=7，响应=response，tool-results 来源=next_request。
- `study turn-003`：[request](records/study/turn-003/request.json), [response](records/study/turn-003/response.json), [tool-results](records/study/turn-003/tool-results.json), [trace](records/study/turn-003/trace.json)； framework messages=7，gateway messages=9，响应=response，tool-results 来源=next_request。
- `study turn-004`：[request](records/study/turn-004/request.json), [response](records/study/turn-004/response.json), [tool-results](records/study/turn-004/tool-results.json), [trace](records/study/turn-004/trace.json)； framework messages=9，gateway messages=11，响应=response，tool-results 来源=next_request。
- `study turn-005`：[request](records/study/turn-005/request.json), [response](records/study/turn-005/response.json), [tool-results](records/study/turn-005/tool-results.json), [trace](records/study/turn-005/trace.json)； framework messages=11，gateway messages=13，响应=response，tool-results 来源=next_request。
- `study turn-006`：[request](records/study/turn-006/request.json), [response](records/study/turn-006/response.json), [tool-results](records/study/turn-006/tool-results.json), [trace](records/study/turn-006/trace.json)； framework messages=13，gateway messages=15，响应=response，tool-results 来源=none。
- `tgcd turn-000`：[request](records/tgcd/turn-000/request.json), [response](records/tgcd/turn-000/response.json), [tool-results](records/tgcd/turn-000/tool-results.json), [trace](records/tgcd/turn-000/trace.json)； framework messages=1，gateway messages=2，响应=response，tool-results 来源=next_request。
- `tgcd turn-001`：[request](records/tgcd/turn-001/request.json), [response](records/tgcd/turn-001/response.json), [tool-results](records/tgcd/turn-001/tool-results.json), [trace](records/tgcd/turn-001/trace.json)； framework messages=3，gateway messages=6，响应=response，tool-results 来源=next_request。
- `tgcd turn-002`：[request](records/tgcd/turn-002/request.json), [response](records/tgcd/turn-002/response.json), [tool-results](records/tgcd/turn-002/tool-results.json), [trace](records/tgcd/turn-002/trace.json)； framework messages=5，gateway messages=8，响应=response，tool-results 来源=none。
- `generate turn-000`：[request](records/generate/turn-000/request.json), [response](records/generate/turn-000/response.json), [tool-results](records/generate/turn-000/tool-results.json), [trace](records/generate/turn-000/trace.json)； framework messages=1，gateway messages=2，响应=response，tool-results 来源=next_request。
- `generate turn-001`：[request](records/generate/turn-001/request.json), [response](records/generate/turn-001/response.json), [tool-results](records/generate/turn-001/tool-results.json), [trace](records/generate/turn-001/trace.json)； framework messages=3，gateway messages=5，响应=response，tool-results 来源=next_request。
- `generate turn-002`：[request](records/generate/turn-002/request.json), [response](records/generate/turn-002/response.json), [tool-results](records/generate/turn-002/tool-results.json), [trace](records/generate/turn-002/trace.json)； framework messages=5，gateway messages=8，响应=response，tool-results 来源=next_request。
- `generate turn-003`：[request](records/generate/turn-003/request.json), [response](records/generate/turn-003/response.json), [tool-results](records/generate/turn-003/tool-results.json), [trace](records/generate/turn-003/trace.json)； framework messages=7，gateway messages=10，响应=response，tool-results 来源=next_request。
- `generate turn-004`：[request](records/generate/turn-004/request.json), [response](records/generate/turn-004/response.json), [tool-results](records/generate/turn-004/tool-results.json), [trace](records/generate/turn-004/trace.json)； framework messages=9，gateway messages=12，响应=response，tool-results 来源=next_request。
- `generate turn-005`：[request](records/generate/turn-005/request.json), [response](records/generate/turn-005/response.json), [tool-results](records/generate/turn-005/tool-results.json), [trace](records/generate/turn-005/trace.json)； framework messages=11，gateway messages=14，响应=response，tool-results 来源=next_request。
- `generate turn-006`：[request](records/generate/turn-006/request.json), [response](records/generate/turn-006/response.json), [tool-results](records/generate/turn-006/tool-results.json), [trace](records/generate/turn-006/trace.json)； framework messages=13，gateway messages=16，响应=response，tool-results 来源=next_request。
- `generate turn-007`：[request](records/generate/turn-007/request.json), [response](records/generate/turn-007/response.json), [tool-results](records/generate/turn-007/tool-results.json), [trace](records/generate/turn-007/trace.json)； framework messages=15，gateway messages=18，响应=response，tool-results 来源=next_request。
- `generate turn-008`：[request](records/generate/turn-008/request.json), [response](records/generate/turn-008/response.json), [tool-results](records/generate/turn-008/tool-results.json), [trace](records/generate/turn-008/trace.json)； framework messages=17，gateway messages=20，响应=response，tool-results 来源=next_request。
- `generate turn-009`：[request](records/generate/turn-009/request.json), [response](records/generate/turn-009/response.json), [tool-results](records/generate/turn-009/tool-results.json), [trace](records/generate/turn-009/trace.json)； framework messages=19，gateway messages=22，响应=response，tool-results 来源=next_request。
- `generate turn-010`：[request](records/generate/turn-010/request.json), [response](records/generate/turn-010/response.json), [tool-results](records/generate/turn-010/tool-results.json), [trace](records/generate/turn-010/trace.json)； framework messages=21，gateway messages=24，响应=response，tool-results 来源=next_request。
- `generate turn-011`：[request](records/generate/turn-011/request.json), [response](records/generate/turn-011/response.json), [tool-results](records/generate/turn-011/tool-results.json), [trace](records/generate/turn-011/trace.json)； framework messages=23，gateway messages=26，响应=response，tool-results 来源=next_request。
- `generate turn-012`：[request](records/generate/turn-012/request.json), [response](records/generate/turn-012/response.json), [tool-results](records/generate/turn-012/tool-results.json), [trace](records/generate/turn-012/trace.json)； framework messages=25，gateway messages=28，响应=response，tool-results 来源=next_request。
- `generate turn-013`：[request](records/generate/turn-013/request.json), [response](records/generate/turn-013/response.json), [tool-results](records/generate/turn-013/tool-results.json), [trace](records/generate/turn-013/trace.json)； framework messages=27，gateway messages=30，响应=response，tool-results 来源=next_request。
- `generate turn-014`：[request](records/generate/turn-014/request.json), [response](records/generate/turn-014/response.json), [tool-results](records/generate/turn-014/tool-results.json), [trace](records/generate/turn-014/trace.json)； framework messages=29，gateway messages=32，响应=response，tool-results 来源=next_request。
- `generate turn-015`：[request](records/generate/turn-015/request.json), [response](records/generate/turn-015/response.json), [tool-results](records/generate/turn-015/tool-results.json), [trace](records/generate/turn-015/trace.json)； framework messages=31，gateway messages=34，响应=response，tool-results 来源=next_request。
- `generate turn-016`：[request](records/generate/turn-016/request.json), [response](records/generate/turn-016/response.json), [tool-results](records/generate/turn-016/tool-results.json), [trace](records/generate/turn-016/trace.json)； framework messages=33，gateway messages=36，响应=response，tool-results 来源=next_request。
- `generate turn-017`：[request](records/generate/turn-017/request.json), [response](records/generate/turn-017/response.json), [tool-results](records/generate/turn-017/tool-results.json), [trace](records/generate/turn-017/trace.json)； framework messages=35，gateway messages=38，响应=response，tool-results 来源=next_request。
- `generate turn-018`：[request](records/generate/turn-018/request.json), [response](records/generate/turn-018/response.json), [tool-results](records/generate/turn-018/tool-results.json), [trace](records/generate/turn-018/trace.json)； framework messages=37，gateway messages=40，响应=response，tool-results 来源=next_request。
- `generate turn-019`：[request](records/generate/turn-019/request.json), [response](records/generate/turn-019/response.json), [tool-results](records/generate/turn-019/tool-results.json), [trace](records/generate/turn-019/trace.json)； framework messages=39，gateway messages=42，响应=response，tool-results 来源=next_request。
- `generate turn-020`：[request](records/generate/turn-020/request.json), [response](records/generate/turn-020/response.json), [tool-results](records/generate/turn-020/tool-results.json), [trace](records/generate/turn-020/trace.json)； framework messages=41，gateway messages=44，响应=response，tool-results 来源=next_request。
- `generate turn-021`：[request](records/generate/turn-021/request.json), [response](records/generate/turn-021/response.json), [tool-results](records/generate/turn-021/tool-results.json), [trace](records/generate/turn-021/trace.json)； framework messages=43，gateway messages=46，响应=response，tool-results 来源=next_request。
- `generate turn-022`：[request](records/generate/turn-022/request.json), [response](records/generate/turn-022/response.json), [tool-results](records/generate/turn-022/tool-results.json), [trace](records/generate/turn-022/trace.json)； framework messages=45，gateway messages=48，响应=response，tool-results 来源=none。

## 实际输入、输出与检查来源

以下条目只列出诊断运行中存在的原始文件；`source-manifest.json` 记录每个候选文件是否实际复制。

| 文件 | 状态 | 记录副本 | 诊断源 |
|---|---|---|---|
| `task-catalog.json` | 已复制（125861 bytes） | [records/source/task-catalog.json](records/source/task-catalog.json) | `/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/task_libraries/piper/1.0.0/catalog.json` |
| `scene-construction-smoke.json` | 已复制（353 bytes） | [records/source/scene-construction-smoke.json](records/source/scene-construction-smoke.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/scene_construction_smoke.json` |
| `focused-checks.json` | 已复制（427 bytes） | [records/source/focused-checks.json](records/source/focused-checks.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/focused_checks.json` |
| `study.json` | 已复制（1655 bytes） | [records/source/study.json](records/source/study.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/study.json` |
| `study-reuse.json` | 已复制（420 bytes） | [records/source/study-reuse.json](records/source/study-reuse.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/study_reuse.json` |
| `original-recording-runner.py` | 已复制（3800 bytes） | [records/source/original-recording-runner.py](records/source/original-recording-runner.py) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/original_recording_runner.py` |
| `skeleton-context.json` | 已复制（1265 bytes） | [records/source/skeleton-context.json](records/source/skeleton-context.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/skeleton_context.json` |
| `design-raw.json` | 已复制（26134 bytes） | [records/source/design-raw.json](records/source/design-raw.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/draft/capability_design.json` |
| `design-main.json` | 已复制（27561 bytes） | [records/source/design-main.json](records/source/design-main.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/capability_design.json` |
| `criteria.json` | 已复制（4314 bytes） | [records/source/criteria.json](records/source/criteria.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/criteria.json` |
| `capability-preparation.json` | 已复制（603 bytes） | [records/source/capability-preparation.json](records/source/capability-preparation.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/capability_inputs/capability_preparation.json` |
| `driver.py` | 已复制（14265 bytes） | [records/source/driver.py](records/source/driver.py) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/workspace/piper/driver.py` |
| `canary-result.json` | 已复制（1529 bytes） | [records/source/canary-result.json](records/source/canary-result.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/canary_result.json` |
| `parent-interface-review.json` | 已复制（852 bytes） | [records/source/parent-interface-review.json](records/source/parent-interface-review.json) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/parent_interface_review.json` |
| `run-canary.py` | 已复制（2845 bytes） | [records/source/run-canary.py](records/source/run-canary.py) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/run_canary.py` |
| `run.log` | 已复制（1529 bytes） | [records/source/run.log](records/source/run.log) | `/private/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-readwrite-pz7rqgpy/run.log` |
| `scene.xml` | 已复制（865 bytes） | [records/source/scene.xml](records/source/scene.xml) | `/Users/wangyifan/Projects/auto_adapter2.0/AA1/assets/mjcf/piper/scene.xml` |
| `piper.xml` | 已复制（14980 bytes） | [records/source/piper.xml](records/source/piper.xml) | `/Users/wangyifan/Projects/auto_adapter2.0/AA1/assets/mjcf/piper/piper.xml` |

## 真实代码入口与转换链

下面是本次链路在仓库中的实际入口；对应的原始模型请求和产物链接到上面的逐轮文件与 `records/source/`。

1. **YAML robot binding → catalog**：`task_library_for_robot` 读取 `AA1/auto_adapter/task_libraries/index.yaml`，把 AA1 robot_id 绑定到源任务包和版本，见 [`capability_preparation.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/capability_preparation.py)；本次未生成独立 public inputs 副本；实际 catalog 路径和读取动作保留在 TGCD 首轮 request 与后续 `read_file` tool result 中。
2. **直接读取公开任务**：模型读取对应机器人的完整 catalog；prompt 要求根据任务描述和 scoring 设计能力，忽略旧 task invocation_schema / request_envelope。实际读取内容保留在 tool result 中，任务库副本见 [task-catalog.json](records/source/task-catalog.json)。
3. **TGCD 直接文件输入**：本次未生成 `authoring_brief.json` 或 `public_inputs.json` 中间副本；首轮 user prompt 给出实际 study.json、framework robot catalog 和可选 skeleton context 的路径/指令，模型通过受限 `read_file` 读取。原始首轮消息见 [`tgcd/initial-user-prompt.txt`](records/tgcd/initial-user-prompt.txt) 与 [`tgcd/turn-000/request.json`](records/tgcd/turn-000/request.json)。；可选 `skeleton-context.json` 已随 source manifest 复制
4. **TGCD 新 ReactLoop：能力与 criteria 联合设计**：设计阶段构建 `ReactLoop`，本次消息记录的模型写入入口为 `write_file（当前 write path）`；相关工具/响应见 [`tgcd/`](records/tgcd/)，Python 接线见 [`capability_preparation.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/capability_preparation.py)。
5. **设计写入后的 validator**：Python 端调用 `_validate_generated_design`，见 [`capability_preparation.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/capability_preparation.py)；候选原稿和主 design 分别见 [`design-raw.json`](records/source/design-raw.json) 与 [`design-main.json`](records/source/design-main.json)。
6. **canonical header / task IDs**：任务 ID 由 `_task_ids` 检查，canonical header 补全逻辑在 `_validate_generated_design`，见 [`capability_preparation.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/capability_preparation.py)。实际写出的主 design 见 [`design-main.json`](records/source/design-main.json)。
7. **derived criteria**：write_file 后由 Python validator 派生并写出 `criteria.json`，见 [`capability_preparation.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/capability_preparation.py)；实际副本见 [`criteria.json`](records/source/criteria.json)。
8. **capability_generation_context 使用内存 design**：`design` 参数优先于 catalog fallback，见 [`robot_catalog.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/robot_catalog.py)；GENERATE 的实际 prompt 和消息见 [`generate/initial-user-prompt.txt`](records/generate/initial-user-prompt.txt) 与 [`generate/turn-000/request.json`](records/generate/turn-000/request.json)。
9. **original generation**：orchestrator 将内存 design 传给 `capability_generation_context` 并启动 `02_generate`，见 [`orchestrator.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/orchestrator.py)；真实生成消息和工具结果见 [`generate/`](records/generate/)。
10. **AST method presence 与接口检查**：本诊断脚本用 Python `ast.parse` 检查生成 `Robot` 的方法声明，父检查另用真实 MuJoCo build 和 `inspect.signature` 核对 request 接口；原始脚本副本见 [`run-canary.py`](records/source/run-canary.py)，生成源码见 [`driver.py`](records/source/driver.py)，检查报告见 [`parent-interface-review.json`](records/source/parent-interface-review.json)（若存在）。这些检查只证明源码/接线可解析，不证明行为完成。

## 记录边界与失败边界

- `tool-results.json` 的 `framework_tool_results` / `gateway_tool_results` 来自**下一轮真实 request 的新增消息**；这保留了框架重新提交给模型的完整 tool result。trace 中的 `observations` 同时保留，但 `trace_observations_may_be_truncated: true`，因为 ReAct trace 使用 [`react_loop.py`](/Users/wangyifan/Projects/auto_adapter2.0/AA1/auto_adapter/agent/react_loop.py) 的 `_truncate`，所以不能把 trace 文本当作完整工具输出。
- 消息文件中出现的 `error` 或缺少的 response 会原样反映在对应 turn；没有消息文件的阶段写为“未找到，未导出”。因此当前导出不把阶段缺失推断为成功，也不把失败请求改写成成功。
- 本次检查发现的具体限制按原文保存在 [parent-interface-review.json](records/source/parent-interface-review.json)；不将其他运行中的问题套用到本次生成结果。
- 本目录是诊断记录和静态检查的可读副本。当前链路**没有独立物理 validation**；criteria 阈值、AST 方法存在、文件生成和框架阶段结果都不能替代独立的真实 MuJoCo 正向验证。
- 本导出器不读取或输出 headers、credentials、环境变量，也不访问网络；重新导出只需运行：`python3 outputs/aa1-piper-flow/export_records.py <diagnostic-run-root>`。
