# AutoAdapter 2.0 当前主线实现说明

> 本文解释当前代码怎样运行，便于开发、审查和读取实验证据；它不是新的实验规范。
> 当前没有 active experiment workspace，也没有获批的新实验 manifest/protocol。正式编译链
> `thesis/Main.tex` 决定当前论文编号，但不因此授权任何实验运行。新实验需另行确定后再建立。

本文保留原 `AA2-AUTH` revision `0.20.1` 时期的实现说明作为代码沿革：没有 `aa1_runtime`，也没有另一套
并行协议。AA1 commit `585eb1f1fde33f17f5f9a1e169a18dd41f97b586` 中有用的
ReAct、文件工作区、持久 Python/MuJoCo session 和 expected-artifact 完成语义，被合并到
现有 AutoAdapter 2.0 模块；多模型、自动 Capability Design、自动 IVC、可信 Harness、
ReCAP 和 Experience 仍由 AA2 当前模块负责。

当前仓库边界是：

- `experiment/` 下只保留通用 path-layout 支持；没有 active runner、manifest 或实验分母。
- 四套旧实验、runner-specific tests、runbook、ignored runs、冻结源码和视频整体保存在
  `experiment/archive/pre_thesis_realign_2026-08-31/`。
- archive 只保存历史证据。不得从 archived runner 发起新实验，也不得把旧结果并入未来分母。
- 通用 Framework/Direct-MuJoCo 代码仍可做共享实现检查；任何新实验运行必须等待单独确定的
  thesis-aligned manifest/protocol。

## 1. 一张图看完整主线

```text
indexed Robot Package
  public: morphology + sources + Task Library + assets + skeleton
  private: instances + bindings + guards + reference Driver
          |
          v
STUDY (study.json, public-only, 16 turns)
          |
          v
TGCD (capability_design.json, 3--10 capabilities, 6 turns)
          |
          v
IVC (capability_validation_suite.json, implementation-blind, 6 turns)
          |
          +--> audit request/schema/source + inline operator/entity/unit/guards
          |       --invalid--> same IVC conversation correction; exhausted --> IVC failure
          |
          v
Generate (driver.py; skeleton 22 turns / scratch 40 turns)
          |
          +--> source + import/build boundary --invalid--> no Driver attempt consumed
          |
          v
freeze attempt N --> trusted Capability Harness + videos
          |
          +--> FAIL and N < 3 --> Repair same driver.py
          |                       (skeleton 22 / scratch 20 turns)
          |                              |
          |                              +--> freeze next attempt --> Harness
          |
          v
derive whitelist: nominal AND calibrated_boundary both pass
          |
          +--> empty --> truthful Task Demo not-run
          |
          v
ReCAP Task Demo (16 planning turns, 12 capability calls per task)
          |
          v
trusted Task Demo Harness verdict + video
          |
          v
optional one-call Evolution --> proposal only --> human accept/reject + reason
                                               --> accepted snapshot for a later run
```

四个 canonical phase artifacts 是：

| 阶段 | Canonical artifact | 谁写 | 谁审核/封存 |
|---|---|---|---|
| STUDY | `study.json` | 模型 | Framework 的 STUDY validator |
| TGCD | `capability_design.json` | 模型 | `capability_design.protocol` |
| IVC | `capability_validation_suite.json` | 模型 | inline request/operator/entity/guard/criteria audit |
| Generate / Repair | `driver.py` | 模型 | source audit + public import/build boundary |

不存在模型可见的 `submit_study`、`submit_capability_design`、
`submit_validation_suite`、`submit_driver` 或 `check_driver`。文件本身就是交付物。

## 2. 代码职责地图

| 文件或目录 | 当前职责 |
|---|---|
| `src/autoadapter2/model_api.py` | 统一模型调用层：JSON、带消息历史的 JSON、原生 tool-use、认证、超时、有限传输重试和脱敏调用证据 |
| `src/autoadapter2/react.py` | 多轮 trace、同 turn 多工具、工具错误回传、普通 terminal-tool ReAct，以及 canonical artifact 完成循环 |
| `src/autoadapter2/driver_synthesis/interactive.py` | condition-local public 文件工作区、IVC isolated artifact workspace、只读 admitted asset closure 和模型工具 |
| `src/autoadapter2/driver_synthesis/probe.py` | 公开投影、持久 Python/MuJoCo 进程、源码隔离、超时/步数/输出边界 |
| `src/autoadapter2/driver_synthesis/generation.py` | STUDY、Generate/GEN_ALGO、公开输入投影和 interface-only stub |
| `src/autoadapter2/driver_synthesis/repair.py` | candidate-facing 报告脱敏/压缩，以及在旧 `driver.py` 上持续 Repair |
| `src/autoadapter2/driver_synthesis/source_check.py` | candidate Driver 的静态来源、ABI、状态写入和 task-dispatch 边界 |
| `src/autoadapter2/capability_design/` | 唯一的 `capability-v2` TGCD schema、审核和阶段执行 |
| `references/capability_v2/` | SO-101/Go2 capability 参考，以及 22-case 完整 IVC worked references；不含 task mapping/plan/verdict |
| `src/autoadapter2/validation_compiler/` | implementation-blind IVC 输入投影、inline suite 审核和六回合 artifact workflow |
| `src/autoadapter2/harness/operators.py` | 可信 measurement operator catalog、参数/request path/unit/entity 的 pre-worker 审核 |
| `src/autoadapter2/harness/` | inline suite 执行、canonical MuJoCo worker、parent-side measurement/guard/aggregation、视频和权威 verdict |
| `src/autoadapter2/task_demo/recap.py` | canonical ReCAP Task Demo controller |
| `src/autoadapter2/b2/` | ReCAP worker/adapter/Harness 的薄兼容入口；不再拥有第二份 controller |
| `src/autoadapter2/evolution.py` | 一次 terminal proposal、人工 disposition、review queue 和 later-run snapshot |
| `src/autoadapter2/pipeline.py` | 按顺序组合阶段、建立可见性边界、冻结 Driver、派生 whitelist、落盘证据 |
| `experiment/path_layout.py` | 当前通用的 repository/path-layout 支持；不定义实验 |
| `experiment/archive/pre_thesis_realign_2026-08-31/` | 旧实验、runner 和证据的只读历史快照；不是活动主线 |

## 3. 统一模型调用层

正式名称是“统一模型调用层”。`JsonModelClient` 保留 AA2 的多模型需求，而不是退回
AA1 原版只直接调用 Bedrock/Converse 的写法。

### 3.1 三种调用接口

1. `generate_json(stage, prompt, inputs)`

   发送 system 指令“只返回一个 JSON object”，把 `inputs` 放入
   `PUBLIC_INPUT_JSON`，使用 `response_format={"type":"json_object"}`。主要用于
   Evolution 和明确的 JSON boundary。

2. `generate_message_json(stage, system_prompt, messages)`

   保留调用方提供的 user/assistant 历史并返回一个 JSON object；同时保存脱敏后的
   request/response exchange。当前主要用于仍需保留角色历史的 JSON boundary；旧 B2 JSON
   ReCAP fixture 也只通过一个窄兼容 adapter 使用它，不是正式主线 controller 接口。

3. `generate_tool_turn(stage, system_prompt, messages, tools)`

   发送原生 tool definitions，解析一个 assistant turn 中的全部 tool calls，并保留
   `content`、`reasoning_content`、`finish_reason`、call ID、tool name、原始 arguments
   和 JSON 解析错误。四类 canonical artifact 工作阶段（Generate/Repair 共用 `driver.py`）和
   canonical ReCAP 动态 capability tools 都使用此入口。

### 3.2 环境变量

真实 client 从以下环境变量读取配置；API key 不写入 manifest 或运行证据：

| 环境变量 | 含义 |
|---|---|
| `AUTOADAPTER_MODEL_PROVIDER` | `openai` 或 `openai-compatible` |
| `AUTOADAPTER_MODEL_VENDOR` | 证据中的 vendor；可省略，DeepSeek hostname 会被识别 |
| `AUTOADAPTER_MODEL_ID` | 请求的精确 model ID |
| `AUTOADAPTER_MODEL_API_BASE_URL` | HTTPS base URL；代码补 `/chat/completions` |
| `AUTOADAPTER_MODEL_API_KEY` | credential，只进入请求 header |
| `AUTOADAPTER_MODEL_API_AUTH_HEADER` | 默认 `Authorization` |
| `AUTOADAPTER_MODEL_API_AUTH_PREFIX` | 默认 `Bearer ` |
| `AUTOADAPTER_MODEL_THINKING` | 可选 thinking control |
| `AUTOADAPTER_MODEL_MAX_TOKENS` | 1,024--65,536；默认 16,384 |
| `AUTOADAPTER_MODEL_TIMEOUT_S` | 单个物理 HTTP 请求的总 wall deadline，30--600 秒；默认 180 秒 |
| `AUTOADAPTER_MODEL_TOOL_HISTORY_MODE` | `native` 或 `text-observation` |
| `AUTOADAPTER_MODEL_HISTORY_CHARS` | 8,192--500,000；默认 80,000 |

### 3.3 传输和重试

- 使用 OpenAI-compatible `/chat/completions` JSON transport。
- 每次逻辑调用最多两个物理 HTTP requests。
- 只对 HTTP `429/500/502/503/504` 做一次、间隔 1 秒的有限重试。
- wall timeout、非 HTTP transport error、响应解码错误和不合法模型内容不会被静默无限重试。
- 总 wall deadline 不只依赖 socket inactivity；主线程环境用计时器覆盖整个 request。
- assistant 既无内容也无 tool call 时，native history 会放入固定中性文本，避免下一次请求形成
  非法空消息。
- Agent Context Manager 在每次 tool turn 前按配置投影历史；记录投影统计，但不调用另一个
  summarisation 模型改写历史。

### 3.4 每次调用保留的证据

`client.calls[]` 为每个 physical HTTP request 保留一条记录；同一逻辑调用的第二次物理请求通过
`retry_of_call_index`/`retry_index` 与第一次关联。每条记录包含：

- `call_index`、cell、stage、目标 attempt、retry lineage；
- UTC 起止时间、elapsed time、status、HTTP status 和脱敏 error；
- requested/returned model、provider、API protocol、finish reason 和 tool names；
- input/output/cache-read/cache-write/reasoning/total tokens，以及其他 provider token 类别；
- tool history mode 和 context projection 统计；
- provider request ID（若响应或 header 提供）。

credential 不会进入这些记录。`generate_message_json` 的 raw exchange 也会先深拷贝并检查
配置的 API key 没有出现在 request/response evidence 中。

## 4. 文件即交付：AA1 expected-artifact 语义

`run_artifact_react` 是四个 canonical file phases 的共同完成逻辑。

一次 phase 的循环如下：

1. Framework 建立隔离 workspace、初始 user prompt 和本阶段允许的 tools。
2. 每个 model turn 可以包含零个、一个或多个 tool calls；同一 turn 的 calls 按返回顺序执行。
3. tool arguments malformed、unknown tool、unavailable tool、handler exception、timeout 或非零执行
   状态，都包装成可观察错误返回同一 conversation，不直接伪装成 phase 完成。
4. 模型正常 `end_turn`/`stop`，或返回没有 tool call 的普通完成时，Framework 审核 canonical path。
5. artifact 不存在或无效且仍有 turns 时，确定性错误会追加到同一 conversation，模型继续修改
   同一个文件。
6. artifact 有效时，phase 立即完成并返回 artifact、完整 trace、model-turn count、tool-call count
   和完成事件。
7. 最后两个 turns 是有界交付窗口，只暴露 `write_file`。倒数第二个 turn 写出的 artifact 会立即
   审核；若无效，确定性错误回到同一 conversation，保留最后一次修正机会。
8. 最后一个 turn 即使只包含 `write_file` 而没有额外 closing message，也会在 `final_turn`
   事件后审核；有效文件照常接受。
9. turn 预算耗尽仍无有效 artifact，抛出带 trace 的 `ReactLoopError`。这不是 Driver attempt。

文件 phase 不设另一个 aggregate tool-call ceiling。限制位于每个具体工具：路径、文件/代码大小、
每次执行 wall time、每阶段 MuJoCo step/simulated-time、输出大小和权限。

所有 file-phase tools 还共享一层 ReAct observation boundary：返回给下一模型 turn 的序列化 tool
envelope 最多 24,000 characters，trace 中保存的 raw arguments 最多 4,000 characters。因而下文的
200,000 file/code limit 是 handler 的输入/读取拒绝线，不表示单次 `read_file` 的 200,000 characters
一定会全部进入模型上下文；当前 mainline 的 `execute_python` 12,000-character output limit 更窄，
会先行生效。

`generation.py`、TGCD 和 IVC 中仍可看到只实现 `generate_json` 的兼容分支，它们用于既有 focused
fixtures 和旧 caller。真实 `JsonModelClient` 提供 `generate_tool_turn`，因此共享主线会选择上述
文件工作流；兼容分支不能作为未来正式实验的执行证据。

### 4.1 Driver freeze 与 attempt

`driver.py` 写出来不等于正式 attempt。Generate/Repair artifact 必须依次通过：

1. UTF-8、非空且可解析/compile；
2. 定义 top-level `build(model, data)`；
3. 对 sealed design 的每个 method 都显式定义 `def method(self, request)`；
4. candidate source boundary；
5. condition boundary（skeleton-assisted 必须使用允许的 trusted skeleton；from-scratch 不得使用）；
6. public-only import，并在 canonical public MJCF 上成功 `driver.build(model, data)`。

通过后，pipeline 把当时的文件复制为不可随 Repair 覆盖的
`frozen-driver-attempts/attempt-N/driver.py`。只有这个 frozen copy 才交给私有 Harness，并且
才使 `frozen_driver_attempt_count` 加一。最多三份 frozen Driver；stub、缺文件、schema/ABI
错误、source audit 失败或 import/build 失败都只进入 `development_rejections`，不消耗三次预算。
如果 Generate 后已经有 frozen Driver，而后续 Repair artifact 被 source audit 拒绝，Framework
保留上一份 frozen Driver、它的终态 Capability Validation 报告和由此派生的 whitelist；被拒 revision
只记为 development rejection，不会清空已有 partial closure，也不会再次调用 Harness。

实验结果使用最后一份 frozen Driver 的 verdict，不在失败记录中 cherry-pick “best attempt”。

## 5. 模型工具参考

### 5.1 `read_file`

```json
{"path": "relative/path.txt"}
```

- `path` 必须是非空相对路径；路径先统一分隔符并规范化，绝对路径和任何保留下来的 `..`
  被拒绝，最终 resolved path 还必须位于获准 root 内。
- STUDY、TGCD、Generate、Repair 先解析本阶段 workspace，再解析 staged public package。
- IVC 使用更窄的 isolated artifact workspace，只能读其中的 `ivc_inputs.json` 和模型在该阶段
  写出的文件；不能借 `read_file` 读取 morphology、tasks、assets、skeleton、Driver 或 package root。
- 只能读 UTF-8 text；读取前的文件大小 gate 是 200,000 bytes。
- from-scratch 条件不能通过路径读取 skeleton。
- 不存在、越界、非 UTF-8 或过大的文件以 recoverable tool error 返回。

返回 `path`、来源 root（`workspace` 或 `public_package`）和 `content`。

### 5.2 `write_file`

```json
{"path": "driver.py", "content": "...complete UTF-8 source..."}
```

- 只能写 condition/stage workspace，不能写 staged public package、仓库其他目录或未投影的私有目录。
- `content` 必须是 text，最多 200,000 characters。
- parent directory 可在 workspace 内创建。
- Generate/Repair 写 `driver.py` 时递增 source revision（内容未变则不增），并同步到 public probe
  projection；下一次 `execute_python` 会 invalidate cached `driver` module。其他 artifact 不触发
  Driver revision。

返回 path、bytes written、内容是否变化和 Driver revision。

### 5.3 `execute_python`

```json
{"code": "import os, mujoco\n..."}
```

- 每个 phase 使用一个 credential-free 持久 Python/MuJoCo subprocess；同一 phase 的 calls 共享
  globals，便于逐步计算，或在公开开发阶段保留 `model/data`、修改代码后复测。
- STUDY、TGCD、Generate、Repair 使用 public development session：环境只保留解释器/MuJoCo
  所需的最小 host 值和公开 staging path；canonical public scene 通过
  `AUTOADAPTER_PROBE_SCENE` 提供，公开 package 和获准的 skeleton 使用隔离 staging roots。
- IVC 使用 isolated artifact session：没有 skeleton、Driver、Repair、private package 或模型凭证；
  `read_file` 仍只能访问 phase workspace，但 `AUTOADAPTER_PROBE_PUBLIC_PACKAGE` 指向只读复制的
  admitted `assets/` closure。IVC 可用 MuJoCo 检查真实 MJCF 和 model entity，不能读取
  `tasks/private/`、reference source 或候选实现；Framework 另把脱敏 instance、guard、operator
  catalog、scene entity catalog 和 worked references 写入 `ivc_inputs.json`。
- 两种 session 都不继承 API key、cloud/proxy credential 或 host `TMPDIR`；Framework 把 `TMPDIR`
  重定向到 phase workspace。子进程环境只保留解释器/MuJoCo 必需值和上面的公开 staging paths。
- parent AST audit 先拒绝 network/process imports、dynamic import/compilation、Framework/Harness/
  reference/private 访问、private path literal，以及直接的 `os.system`、`popen`、`fork`、
  `posix_spawn*`、`spawn*`、`exec*`、`kill` 和 `killpg`。probe 为读取 canonical scene 可以 import `os/pathlib`；
  candidate Driver 自身仍不能取得文件系统权限。
- child 内还安装不可由模型移除的 Python audit hook，覆盖静态分析看不到的 `getattr` 调用、
  `open`/`os.open`/`pathlib`、process creation/signals、socket/network、`ctypes` 以及 MuJoCo plugin
  loading。它是
  defense in depth，不被当作 native MuJoCo 的 OS sandbox 替代品。
- 当前 Direct-MuJoCo probe 必须成功进入 macOS Seatbelt；系统不支持、`sandbox-exec` 缺失或当前
  host 禁止 nested profile 时 fail closed，拒绝执行模型代码。Seatbelt deny network/process，deny
  所有 writes 后只放行 phase workspace；对 `/Users`、`/Volumes`、`/etc`、`/home`、`/root`、
  Keychains 与 private temp/database roots 先 deny reads，再只放行解释器 `sys.prefix/base_prefix`、
  phase workspace、staged public package，以及 skeleton-assisted 时复制出的 trusted Python root。
  原始 package、private/reference、仓库 `.env` 因而不可读。
- persistent `execute_python` 与兼容的 one-shot `run_probes` 共用同一 AST、audit-hook、Seatbelt 和
  minimal-environment boundary；不存在一个权限更宽的旧 probe subprocess。
- `mujoco.mj_step` 被 wrapper 计数；超过每阶段 step 或 simulated-time budget 会失败。
- `ProbeBudget` 的代码默认是每 call 30 秒、24,000 输出字符、每 phase 4,000 physics steps、
  每 phase 20 simulated seconds；通用 mainline config 可把输出进一步固定为 12,000。
  Pipeline 会把同一 `execute_python` block 传给 STUDY、TGCD、IVC、Generate 和 Repair。历史实验
  的 runner/config 取值只记录在 archive 中；未来正式运行以届时获批的 config/runner 为准。
  当前 Generate 与后续 Repair 共用一个开发 Session，其 physics-step budget 累计使用，
  不会在进入 Repair 时重新补充；每次模型调用的时间和输出限制仍独立生效。
- code 最多 200,000 characters。stdout/stderr 被合并在边界内，超出部分明确标记 truncated。
- timeout、worker exit 或协议损坏会终止并丢弃当前持久进程；失败代码不会自动重放。下一次显式
  `execute_python` 会启动干净 worker，并返回 `session_restarted=true`，此前 Python globals 不再
  保证存在。phase 的 tool-call 计数不会清零；因为丢失 worker 后无法确认已执行的 native steps，
  Framework 会保守视为该 phase 的 physics-step budget 已耗尽。新 worker 仍可做纯 Python 和
  Driver import/build 审核，但不能借重启取得新的物理执行预算。

结果区分 `successful`、`timed_out`、`exit_code`、`spawn_error`、stdout/stderr、输出截断、
wall time、本 call 和累计 physics steps、`session_restarted`/`session_lost`，以及结构化 exception。

### 5.4 `list_skeletons`

```json
{}
```

只在 skeleton-assisted Generate/Repair 可见。返回 staged `skeleton/` 下的 Python 文件名和
相对路径；STUDY、TGCD、IVC 和 from-scratch 均不可见。

### 5.5 `inspect_skeleton`

```json
{"name": "arm_serial_dls.py"}
```

只读取 `list_skeletons` 列出的单个 trusted skeleton source；名称必须是安全相对路径，缺失
`.py` 时 Framework 补齐。读取前的文件大小 gate 是 200,000 bytes。

### 5.6 ReCAP capability tools 与 `finish`

ReCAP 不取得 `read_file`、`write_file`、`execute_python` 或 skeleton tools。Framework 从最终
Capability Validation 的 whitelist 动态建立 capability tools：只有同一 capability 的
`nominal` 和 `calibrated_boundary` cases 都通过，才可出现在 controller catalogue。

每个 capability tool 名称就是 sealed `method_name`，参数就是 native `request` object 本身：
例如 schema 定义 `x/y/z`，tool arguments 就直接是 `{"x": ..., "y": ..., "z": ...}`，
不是 `{"request": {...}}` 包装。参数必须精确满足 sealed closed `request_schema`；多字段、缺字段、
越界值、malformed JSON 或未知 capability 都在调用 worker 前被拒绝并作为 tool observation 返回。

同一 assistant turn 可以按返回顺序调用多个 capability tools。有效调用在当前 task 的同一个
credential-free MuJoCo worker 上执行，并只返回脱敏的 `operation` 与 package-specific public
observation。保留工具 `finish` 的 schema 是一个不允许额外字段的空 object，因此调用形式为
`finish({})`；至少有一次通过 schema binding 并被路由到 worker 的 capability invocation 后才接受
（该 invocation 的物理 outcome 仍可为 error/abort）。`finish` 只记录
`CONTROLLER_FINISHED`，不声明 PASS，最终 task verdict 始终由可信 Task Demo Harness 计算。

16-turn budget 统计每次模型 planning turn；12-call budget 只在参数已经通过 closed-schema binding、
即将路由到 worker 时递增。无 tool 的 turn、unknown tool、malformed/out-of-bounds arguments 和过早
`finish` 会增加 `invalid_outputs` 并消耗 planning turn，但不消耗 capability-call budget。worker
返回 ERROR/ABORT 的 schema-valid invocation 已经消耗一个 capability call。每条返回模型上下文的
ReCAP tool observation 最多 4,096 characters；超长结果只保留明确标注的 preview。

canonical 结果保留 `status`、`planning_turns`、`capability_calls`、`invalid_outputs`、逐 call
`trace[]` 和 `context_tree`。trace 区分参数 binding、worker outcome、预算耗尽、controller 自报完成；
它不能替代 Harness verdict。旧 `generate_recap_json` 分支只为已保留 B2 fixtures/测试兼容；同时
提供两种接口的真实 client 总是走 `generate_tool_turn`。

## 6. 阶段级输入、输出、可见性和预算

| 阶段 | 模型可见输入 | 模型不可见 | 输出 | 预算 |
|---|---|---|---|---:|
| STUDY | public package projection、morphology、sources、Task Library、runtime facts、eligible Experience | capability design、skeleton source、private suite/reference | `study.json` | 16 turns，两 condition 相同 |
| TGCD | completed public STUDY、public package/tasks/sources、SO/Go capability reference、eligible Experience | candidate Driver、private IVC inputs、reference source | `capability_design.json` | 6 turns |
| IVC | sealed design/`task_support`、source lineage、脱敏 capability+task instances/guards、non-addressable measurement examples、46-kind trusted operator catalog、scene entities、22-case SO/Go worked references、只读 assets | Experience、candidate/Repair/history/verdict、task/reference request、waypoint/task plan、reference source | `capability_validation_suite.json` | 6 turns |
| Generate | sealed public design、STUDY、public package、runtime/probe facts、eligible Experience；skeleton condition 另见 skeleton | private suite/bindings/guards/reference/Harness | `driver.py` | skeleton 22；scratch 40 turns |
| Capability Harness | frozen Driver、sealed design、IVC-authored inline suite、private scene mechanics/guards | 模型上下文、Experience；candidate 看不到 measurement/criteria/guards/task envelope | report + videos | 最多 3 frozen attempts |
| Repair | 延续 Generate 的对话、文件与开发 Python Session；追加紧前 attempt 的失败摘要，完整公开诊断按需读取 | private definitions/reference/Harness source | 修改同一 `driver.py` | skeleton 22；scratch 20 turns |
| ReCAP Task Demo | public task、whitelisted capability schemas、public observations | Experience、未通过 capability、private criteria/guards/reference/verdict | controller trace | 每 task 16 planning turns / 12 capability calls |
| Task Demo Harness | persistent worker evidence、private task clauses/bindings/guards | controller 无权读的私有定义 | task verdict + video | package task budget |
| Evolution | 压缩后的 terminal candidate-facing facts | private definitions、当前 run mutation channels | `{}` 或五字段 proposal | 最多 1 call |

Experience 的精确 later-run visibility 是 STUDY、TGCD、Generate 和 Repair；它不进入 IVC、
Capability Harness、ReCAP 或 Task Demo Harness。同一 run 不能消费自己刚产生的 Experience。

## 7. 每个阶段做什么

### 7.1 STUDY

STUDY 在 TGCD 之前，不依赖已设计的 capability。两种 generation condition 使用相同 procedure，
trusted skeleton 不暴露。模型需要：

- 阅读公开 morphology、tasks、sources、MJCF closure 和 runtime contract；
- 用 canonical `AUTOADAPTER_PROBE_SCENE` 建立真实 `MjModel/MjData`；
- 至少执行一次产生正 physics-step count 的 `mujoco.mj_step`；
- 写一个 JSON object，包含 `condition`、`findings`、`implementation_plan` 和非空
  `probe_requests[]`（每项含 `probe_id`、`script`）。

Framework 不把模型自称“probe 成功”当证据；必须看到真实、无 timeout/spawn error、退出码为
0 且 physics steps 大于 0 的执行结果。

### 7.2 TGCD（Task-Grounded Capability Design）

TGCD 从公开 task evidence 自主设计 3--10 个 package-bound、single-effect capabilities。
`task_support` 只回答“某 capability 能支持某 task，为什么”，不能变成 ordered calls、waypoints、
task macro 或 oracle plan。

公开参考完整保存在：

- `references/capability_v2/so101.json`：六项
  `move_end_effector_to_position`、`trace_cartesian_path`、`set_gripper_opening`、
  `approach_until_contact`、`move_cartesian_offset_and_return`、`set_wrist_roll`；
- `references/capability_v2/go2.json`：五项
  `track_planar_twist`、`move_body_relative_pose`、`trace_planar_path`、
  `set_body_height`、`hold_stable_stance`。

这两份 JSON 给出完整参数 schema、bounds、units/frames 和真实 criteria，但 loader 会拒绝
`task_id`、`task_support`、task mapping、calls、waypoints、macro 和 plan，避免把 archived Exp1b
oracle 使用方案泄漏成 TGCD 答案。其他九台机器人可以参考合同形式，必须自己设计 target-specific
capabilities。

下面是当前 reference 的核对索引；JSON 文件本身才是完整机器可读版本。每个 request numeric
bound 都有自己的 `evidence_refs`，每个 structured criterion 则以 `source_refs` 绑定其 primary
threshold、temporal/aggregation 义务和同一 criterion 内的补充 checks；这些数值来自公开标准或
保留的真实校准，不是运行时临时编出的数值。

| SO-101 method | Closed request（单位/坐标系/范围） | Structured criterion |
|---|---|---|
| `move_end_effector_to_position` | `target_position_m`: 3D world m，每轴 `[-1,1]`；`max_duration_s`: `[0.25,8]` s | `end_effector_position_error <= 0.015 m`，连续 `0.5 s` |
| `trace_cartesian_path` | `waypoints_m`: 2--8 个 world XYZ，每轴 `[-1,1]` m；`max_duration_per_segment_s`: `[0.25,5]` s | `cartesian_path_error <= 0.02 m`，ordered waypoints + terminal hold `0.5 s` |
| `set_gripper_opening` | `opening_fraction`: `[0,1]` ratio；`max_duration_s`: `[0.25,8]` s | `normalised_aperture_error <= 0.1`，连续 `0.25 s` 且包含双向 excursion |
| `approach_until_contact` | `precontact_position_m`: 3D world m，每轴 `[-1,1]`；`approach_direction_unit`: 3D world unit vector 分量 `[-1,1]`；`max_travel_m`: `(0,0.08]`；`max_approach_speed_m_s`: `(0,0.05]`；`max_duration_s`: `[0.25,8]` | composite `contact_integrity == 1`，ordered precontact/ray/contact + contact hold `0.1 s` |
| `move_cartesian_offset_and_return` | `offset_robot_base_m`: 3D robot-base m，每轴 `[-0.06,0.06]`；`max_duration_per_leg_s`: `[0.25,5]` s | `ordered_outbound_return_error <= 0.015 m`，outbound hold `0.25 s`、return hold `0.5 s` |
| `set_wrist_roll` | `target_roll_rad`: `[-2.7438473,2.7438473]` rad/joint；`max_duration_s`: `[0.25,8]` s | `wrist_roll_error <= 0.03 rad`，连续 `0.25 s` 并保持其他 joints |

| Go2 method | Closed request（单位/坐标系/范围） | Structured criterion |
|---|---|---|
| `track_planar_twist` | `linear_velocity_body_m_s`: 2D body m/s，每轴 `[-0.4,0.4]`；`yaw_rate_rad_s`: `[-1,1]`；`duration_s`: `[2,4]` | `planar_velocity_error <= 0.1 m/s`，final window `1 s`，同时聚合 yaw rate |
| `move_body_relative_pose` | `translation_initial_yaw_m`: 2D initial-body-yaw m，每轴 `[-0.3,0.3]`；`yaw_delta_rad`: `[-0.6,0.6]`；`max_duration_s`: `[0.25,8]` | `terminal_planar_position_error <= 0.1 m`，terminal hold `0.5 s`，同时聚合 yaw/speed |
| `trace_planar_path` | `waypoints_initial_yaw_m`: 2--8 个 2D points，每轴 `[-0.4,0.4]` m；`max_duration_s`: `[0.25,8]` | `planar_path_error <= 0.1 m`，ordered waypoints + terminal hold `0.5 s` |
| `set_body_height` | `target_height_m`: `[0.22,0.36]` world m；`max_duration_s`: `[0.25,8]` | `body_height_error <= 0.03 m`，terminal hold `0.5 s`，同时聚合 attitude/drift/yaw |
| `hold_stable_stance` | `duration_s`: `[1,2]` s | composite `stable_stance_integrity == 1`，disturbance recovery `0.5 s` 后保持支撑/接触完整性 |

这些是 capability contract 参考，不是 task macro。尤其 `trace_*` 中的 waypoint 是该单一
path-tracking capability 的调用参数类型；reference 文件本身仍不含任何具体 task 的 waypoint、调用
顺序或 `task_support`。

### 7.3 IVC（Independent Validation Compiler）

IVC 是 implementation-blind compiler。它只使用 sealed design 与 Framework-private execution
contexts，自己写出每个 capability 恰好两个完整 cases：

- `nominal`：典型有效范围；
- `calibrated_boundary`：真实校准边界附近、仍符合 contract 的范围。

IVC 的完整输入由 `build_ivc_inputs()` 建立：

- `sealed_capability_design`：包含 `task_support`，只用于理解支持关系，不提供调用顺序；
- `source_task_lineage`：公开 sources 与 task 描述/评分来源；
- `private_instances`：脱敏 scene/reset、request domain、calibration profile、mandatory guard IDs、
  repetitions 与 timeout；task request envelope、`task_id`、preinvoke 和 task plan 被移除；
- `trusted_measurement_examples`：现有 private binding 的参数化范例，其中 `binding_id` 改成不可引用的
  `example_id`；历史 `b1_contract` opaque dispatch 不进入模型上下文；
- `private_guards`、`measurement_operator_catalog` 与从每个真实 MJCF 解析出的
  `scene_entity_catalog`；
- `complete_so101_go2_worked_references`：
  `references/capability_v2/ivc_worked_references.json` 中 SO-101 12 cases + Go2 10 cases，合计
  22 个真实 request/measurement/criteria/source/guard 范例。它们不含 `task_support`、task mapping、
  task plan 或 expected verdict。

若 package 同时有 `capability_validation/private/` 和 `tasks/private/`，两者按 ID 无冲突合并；专用
五项/六项 context 不能遮蔽其他 task-backed scenes。若只有 task namespace，则 Framework 向 IVC
隐藏 task envelope 后使用其 scene/reset/measurement calibration。Task Demo 仍使用原始
`tasks/private/`，与 capability suite 是两条不同的可信执行路径。

每个动态 case 必须精确包含：

```json
{
  "case_id": "A1-nominal",
  "case_role": "nominal",
  "capability_id": "A1",
  "method_name": "move_end_effector_to_position",
  "request": {"target_position_m": [0.4, 0.1, 0.2], "max_duration_s": 4.0},
  "request_grounding_refs": [
    {"source_id": "...", "specific_reference": "..."}
  ],
  "instance_id": "so101-a1-nominal",
  "measurement_binding": {
    "metric": "end_effector_position_error",
    "unit": "m",
    "kind": "so101_end_effector_regulation",
    "parameters": {"site_name": "gripperframe", "side_effect_guard_profile": "so101"}
  },
  "guard_ids": ["so101-control", "so101-no-direct-write", "so101-canonical", "so101-control-range"],
  "repetitions": 1,
  "timeout_sim_s": 8.0,
  "criteria": ["<sealed criterion copied byte-for-byte as JSON value>"]
}
```

`measurement_binding` 不是模型代码。当前 catalog 把每个 kind 的 description、closed parameter
schema、允许输出 unit、request-path parameters、MuJoCo entity parameters 和 evaluation mode 都写成
机器可读 JSON。IVC 可以为 reference bank 之外的新 capability 组合已有可信 operator；若真实
criterion 无法由 catalog 表达，Framework 返回可修正错误，六回合耗尽后如实 `IVC failure`，不会
选择“最接近”的旧 binding，也不允许 IVC 写任意 Python measurement。

Framework 在 candidate worker 前依次审核：case fields/count/roles、method 与 criteria exact copy、
closed request schema 和可选 request domain、grounding ref、nominal/boundary request 不同、instance
role、mandatory guards、repetitions/timeout、operator kind/parameters、metric/unit、`request.*` path，
以及所选 scene 中的真实 body/site/joint/geom/actuator/keyframe。任何非有限数、`binding_id`、code、
task dispatch/`task_id`、Driver/Repair/verdict/self-PASS material 都拒绝。

封存后的 candidate Harness 只把 native `{"request": <case.request>}` 交给 Driver method。inline
measurement、sealed criteria、scene mechanics、guards、temporal/aggregation、视频和 verdict 均在
Framework parent side。reference-only adapter 若在单独诊断中需要私有 task envelope，也不能改变
candidate payload 或 report。archived Exp1a B1 fixed suite 保留自己的冻结 `binding_id`/contract，
不受动态 `capability-v2` 接口迁移影响；共享代码若仍需读取该历史固定输入，必须使用明确的 archive
路径。

### 7.4 Generate / GEN_ALGO

Framework 先从 sealed design 机械生成 interface-only stub：每个 method 只有
`def method(self, request)` 和 `NotImplementedError`，另有未实现 `build(model, data)`；stub 没有
controller、task dispatch 或 reference logic。

- skeleton-assisted 可以查看并 import 允许的 capability-neutral trusted skeleton。
- from-scratch 不得查看/import skeleton，且 source audit 必须观察到 actuator `data.ctrl` 和
  physics-step path。
- 两者都必须保留 Framework-owned canonical `model/data`，不得另载/重置模型、直接写 qpos/qvel、
  teleport state、访问私有 criteria 或根据 `task_id` dispatch。
- dynamic effect 使用 bounded、fresh-state feedback loop：读最新 state、写 actuator control、推进
  同一 MuJoCo session、重新观察并修正。

### 7.5 Capability Validation 与 Repair

Harness 对每个 case/repetition 在独立 worker reset 下执行 frozen Driver，收集 canonical physics、
method invocation、actuator path、samples、contacts、exceptions、logs 和视频。可信测量层再计算：

- temporal rule 和 metric value；
- aggregation rule；
- private guards；
- canonical model/data、无 direct state write、确有 actuator-controlled physics steps；
- 最大几何穿透不超过共享 `0.005 m` integrity bound；
- video completeness（需要视频时）；
- case、capability 和 whole-suite verdict。

Generate 与后续 Repair 共用同一开发对话、workspace 和公开 Python Session。Repair 只追加
`DRIVER_VALIDATION_FEEDBACK_JSON`：失败项的调用参数、实测值、异常、失败 guard、日志摘要，
以及通过项的简短列表。公开资料和整份源码不再每次重新注入。历史压缩保留最新验证反馈与
当前源码快照；开发 Python 的执行预算跨提交累计，修改后必须重新加载代码再检查。

`repair.py` 沿用脱敏规则，移除 suite、binding/guard definitions、hidden expected values/trajectory、
reference/Harness source、credential 和 private paths。完整 candidate-facing 报告（dense samples
仍按既有规则压缩）与各失败 trial 的诊断保存在 `files/validation_feedback/attempt-N/`，模型可以
按需读取日志、轨迹和媒体信息。Harness 仍对每个 case/repetition 独立初始化，不共享开发物理状态。

Repair 原地修改同一 `driver.py`；必须相对上一提交产生源码变化，并通过 source/import boundary，
才会复制为新的 frozen attempt。提交次数与各阶段模型轮次仍有上限。开发 Session 在最后一次
验证或退出修复循环时关闭；终态使用最后一份实际提交给 Harness 的 frozen Driver 报告。

### 7.6 ReCAP Task Demo

ReCAP 是 post-validation high-level controller，不是 Driver synthesis，也不是 verdict owner。
Framework 从 final Capability Validation 报告按 capability ID 聚合 case roles；只有 nominal 与
calibrated-boundary 两类全部 trials 都通过的 capability 才进入 whitelist。

Task Demo 选择最多五个、由 whitelist 的 `task_support` 覆盖的公开 tasks。每个 task 建立一个持续的
credential-free MuJoCo worker；同一个 task 内的所有 capability calls 都作用于同一 `model/data`
状态。每次调用前先按 closed request schema 验证，执行后只返回 package 的 public observation。
controller 完成只表示“不再调用”；Task Demo Harness 随后独立读取 worker evidence，计算所有
私有 task clauses/guards 并给出 PASS/FAIL 与视频证据。

若至少一个 capability 进入 whitelist，即使完整 Capability Validation 没有全套通过，诊断主线也可
运行 ReCAP；这使 DeepSeek canary 能诚实展示 partial capability closure。若 whitelist 为空，cell
report 中的 Task Demo outcome 保存明确的 `skipped=true` 和非空 `skip_reason`，且不会伪造一个已执行
的 `task_demo_report.json`。

### 7.7 Evolution 与 Experience

Evolution 在 cell 已经 terminal 后最多调用一次，而且是 non-blocking sidecar。模型只能返回 `{}`
或以下五个字段：

```json
{
  "observation": "从公开 terminal report 观察到的事实",
  "lesson": "受证据边界约束的经验",
  "recommendation": "给未来独立 run 的建议",
  "scope": "建议适用范围",
  "public_evidence": ["公开证据引用或摘要"]
}
```

四个 text fields 都必须非空，`public_evidence` 必须是非空 text array；字段集合必须精确相等，
不能夹带 Framework label、Driver 修改或 review 决定。

Framework 不允许 proposal 改 Driver、suite、criterion、verdict、retry 或当前 run input。随后由
Framework 添加：

```json
{
  "provenance": {
    "experience_id": "<source-run>:<cell>",
    "source_run_id": "...",
    "source_robot": "...",
    "generation_condition": "...",
    "review": {"decision": "accept", "reason": "non-empty"}
  },
  "outcome": {"terminal_label": "positive|negative"}
}
```

人工输入只接受 `accept`/`reject` 和非空 `reason`。review API 不接受 proposal 字段，因此不能在
审核时编辑模型内容。只有 accepted records 进入 `autoadapter_experience_snapshot`；snapshot 的
`usage_scope` 是 `next_independent_run_only`，并且同源 run ID 会被拒绝。

若 final Driver 只有 partial capability closure，只要 Framework whitelist 非空，且后续 Task Demo
确实完成物理执行、顶层与报告内 verdict 一致、必需视频完整，Framework 仍按 Task Demo verdict
赋 `positive` 或 `negative` outcome，允许人工处置。整套 Capability Validation 仍保持 FAIL，不能
改写为 full Driver PASS。Task Demo 缺失、未真实执行、视频不完整、verdict 冲突或 infrastructure
failure 时 outcome 保持 `indeterminate`，即使人工填写 accept 也不能生成 snapshot。

代码边界对应 `build_experience_review_queue(...)` → `apply_experience_review(...)` →
`build_experience_snapshot(...)`。主线 source run 只生成 pending queue；没有通用 CLI 会替用户自动
accept。必须先把 proposal 展示给用户，再由人工提供 disposition 与理由，随后才能显式构建 snapshot
并在另一个 run 的 `experience.input` 中加载。

## 8. Artifact schema 速查

### 8.1 `capability_design.json`

顶层 canonical fields：

```text
artifact_type = capability_design
schema_version = 2.0
capability_protocol_version = capability-v2
robot_configuration_id / package_version / task_snapshot_id
invocation_abi = {
  kind: capability_request,
  method_call: method(request=request),
  request_required: [request]
}
capabilities[3..10]
task_support[]
```

每项 capability canonical fields：

```text
capability_id, method_name, description, effect,
request_schema,
preconditions[], temporal_semantics{}, invariants[], failure_behavior,
criteria[], evidence_refs[]
```

`method_name` 必须是唯一、非 keyword、且不以 `_` 开头的 Python identifier；Framework 保留
`build`、`close`、`data`、`describe`、`finish`、`finish_task_demo`、`from_session`、`model`、
`render`、`settle`、`spec` 和 `step`。`request_schema` 是受限 JSON Schema：

- root/嵌套 object 必须 `additionalProperties=false`，并要求所有 properties；
- field name 不能含 task/scene/reset/private/criterion/oracle/macro/plan/sequence/call；
- numeric fields 必须有 finite bounds 和非空 `evidence_refs`；
- array 必须有 `minItems/maxItems`；
- 非 object fields 必须有非空 `unit` 和 `frame`；
- runtime request 必须严格符合 schema，不能多字段或漏字段。

每项 criterion 必须有：

```text
metric, unit, comparator, threshold,
temporal{}, aggregation{}, source_refs[]
```

`comparator` 只能是 `< <= > >= == between`；每个 numeric threshold/bound 的 evidence ref 包含
`source_id`、`specific_reference`、`support=direct|adapted`，adapted 时还必须解释 `adaptation`。
当前 executable IVC boundary 要求每项 capability 的 `criteria[]` 恰好包含一个 primary
criterion；数组形态保留合同的结构边界，但不能在本实现中塞入多个独立 executable criteria。

`task_support[]` 每项只能有 `task_id`、`capability_id`、`rationale`；所有 tasks 和 capabilities 都要
至少被覆盖一次，pair 不得重复。

### 8.2 `capability_validation_suite.json`

```text
artifact_type = capability_validation_suite
schema_version = 2.0
capability_protocol_version = capability-v2
robot_configuration_id / package_version / task_snapshot_id
whole_suite_aggregation = {kind: all_cases}
cases[]  # exact 2 * capability_count
```

每项 case 字段必须精确为：

```text
case_id, case_role, capability_id, method_name,
request, request_grounding_refs[],
instance_id, measurement_binding{}, guard_ids[],
criteria[], repetitions, timeout_sim_s
```

`case_role` 只能为 `nominal` 或 `calibrated_boundary`，每个 capability 每种恰好一个；criteria 与
sealed design 完全相等，不能调 threshold；`request` 必须严格满足该 capability 的 closed
`request_schema` 和所选 instance 的可选 `request_domain`，且同一 capability 的 nominal 与 boundary
requests 不能相同。`request_grounding_refs` 必须解析到 sealed schema 或真实 calibration evidence。
`measurement_binding` 精确包含 `metric`、`unit`、`kind`、`parameters`；前两项与 sealed criterion
一致，kind/parameters 通过 trusted operator catalog 和真实 scene entity 审核。动态 suite 中
`binding_id` 是 forbidden field。

### 8.3 `driver.py`

最小结构：

```python
class CapabilityDriver:
    def <sealed_method>(self, request):
        ...

def build(model, data):
    return CapabilityDriver(...)
```

`build` 接收 Framework-owned canonical MuJoCo objects；每个 method 只读自己的 closed request。
candidate audit 禁止动态 binding/execution、network/process/filesystem/private Framework access、
direct MuJoCo state write 和 task-level request dispatch。package-private reference Driver 使用显式
trusted-reference boundary，不因此放松 candidate。

### 8.4 ReCAP controller trace

至少保留 planning turn、选择的 capability、public arguments、schema binding 是否有效、worker
operation outcome、call budget、controller completion 和 context tree/plan revision。该 trace 是
controller evidence，不是 Harness verdict。

### 8.5 Capability/Task Demo report

顶层把事实分开记录：

- `pipeline_completed`：执行/聚合路径是否完整；
- `physical_validation_executed`：是否发生完整 canonical physics；
- `validation_passed`：可信 metric + integrity + video + aggregation verdict；
- passed/total cases、capabilities、tasks 和 source clauses；
- `video_complete` 与逐 trial `video_manifest`；
- `trials[]` 中的 public arguments、measurement、temporal/aggregation、guards、exception、
  physical integrity 和 final `trial_passed`。

不要用 controller 自报完成替代 `validation_passed`，也不要用“有视频文件”替代
`video.complete=true`。

## 9. 可见性矩阵

| 数据 | STUDY | TGCD | IVC | Generate | Repair | Cap Harness | ReCAP | Task Harness | Evolution |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| public package/tasks/sources | ✓ | ✓ | source lineage + read-only assets | ✓ | ✓ | ✓ | public task only | ✓ | compact facts only |
| STUDY | writer | ✓ | — | ✓ | ✓ | — | — | — | compact facts only |
| eligible Experience | ✓ | ✓ | — | ✓ | ✓ | — | — | — | — |
| trusted skeleton source | — | — | — | skeleton only | skeleton only | worker import only | — | — | — |
| sealed capability design | — | writer | ✓ | ✓ | ✓ | ✓ | whitelist projection | ✓ | compact facts only |
| private instances/bindings/guards | — | — | sanitized instances/guards + non-addressable examples | — | — | full parent-side | — | task path full | — |
| operator + scene entity catalog | — | — | ✓ | — | — | parent-side executor | — | — | — |
| SO/Go 22-case worked references | — | public capability refs only | ✓ | — | — | — | — | — | — |
| validation suite | — | — | writer | — | definition redacted | ✓ | — | — | — |
| reference Driver source | — | — | — | — | — | optional separate diagnostic only | — | — | — |
| candidate Driver | — | — | — | writer | prior source | ✓ | worker only | worker only | source omitted |
| candidate verdict/history | — | — | — | — | immediate public report | owner | — | owner | compact public facts |

## 10. 运行目录和生产者/消费者

一个普通 single-robot/single-condition run 的关键树如下；具体 trial/video 文件数取决于 suite：

```text
runs/<run-id>/
├── package_check.json                         # package loader -> preflight/report
├── model_preflight.json                       # model pin check -> report
├── experiment_report.json                     # pipeline final aggregate
├── experience_review_queue.json               # only when Evolution enabled
└── cells/<robot>/<condition>/
    ├── files/
    │   ├── study.json                         # model STUDY artifact
    │   └── driver.py                          # mutable current Generate/Repair file
    ├── study_evidence.json                    # STUDY trace + real probe evidence
    ├── design/
    │   ├── capability_design.json             # sealed TGCD artifact
    │   ├── tgcd_artifact_trace.json            # public ReAct/artifact events
    │   └── workspace/
    │       ├── tgcd_inputs.json                # model-visible phase projection
    │       └── capability_design.json          # working canonical artifact
    ├── private/
    │   ├── capability_validation_suite.json   # sealed IVC artifact; candidate-invisible
    │   ├── ivc_artifact_trace.json             # private callbacks + model-call evidence
    │   ├── workspace/
    │   │   ├── ivc_inputs.json                 # sealed design + private compiler inputs
    │   │   └── capability_validation_suite.json # working canonical artifact
    │   └── reference-positive-control/...     # only when a scoped protocol separately requests it
    ├── experience_input.json                  # IDs/records visible only to allowed phases
    ├── probe_results.json
    ├── frozen-driver-attempts/
    │   ├── attempt-1/driver.py
    │   ├── attempt-2/driver.py
    │   └── attempt-3/driver.py
    ├── attempt-0/
    │   ├── generation_evidence.json
    │   ├── capability_validation_report.json
    │   └── capability-validation/videos/...
    ├── attempt-1/...                          # Repair 1 when needed
    ├── attempt-2/...                          # Repair 2 when needed
    ├── task-demo/                              # 仅非空 whitelist 并实际执行时生成
    │   ├── capability_design.json             # whitelist projection
    │   ├── framework_task_inputs.json          # Framework-private task evaluation input
    │   ├── task_demo_report.json
    │   └── videos/...
    └── cell_report.json
```

主要生产者/消费者关系：

| Artifact | Producer | Consumers |
|---|---|---|
| public Robot Package | package author + loader | STUDY, TGCD, Generate/Repair projection, Harness, Task Demo |
| `study.json` | STUDY model | TGCD, Generate, Repair, evidence reader |
| `capability_design.json` + TGCD trace | TGCD model + validator | IVC, stub generator, source audit, Harness, whitelist/ReCAP, public evidence reader |
| private suite | IVC model + deterministic inline audit | candidate capability Harness；可选 scoped reference diagnostic 不改变 suite |
| private IVC trace | Framework callback/model-call recorder | private compiler audit only；aggregate 只保留 summary/count/path |
| mutable `driver.py` | Generate/Repair model | source/import validator, then freezer |
| frozen Driver | Framework | one Harness attempt；final copy 仅以双过 whitelist 的 capability 喂给 Task Demo worker |
| capability report | trusted Harness | next Repair, whitelist derivation, terminal report/Evolution |
| Task Demo report | ReCAP worker + trusted Harness | terminal report/Evolution |
| Evolution proposal | Evolution model | human review queue only |
| accepted Experience snapshot | Framework after human review | later run STUDY/TGCD/Generate/Repair |

## 11. 终态和错误语义

### 11.1 Model/transport failure

- provider HTTP retryable error：最多一次物理重试；每次 request 都保留独立 call record。
- timeout/transport/response parse error：stage evidence 标注实际 error；不会伪装成模型正常失败。
- file phase 在未得到有效 artifact 前耗尽 turns：phase error；若还没 freeze Driver，attempt count 为 0。

### 11.2 Tool/artifact failure

- malformed tool arguments、unknown/unavailable tool、路径越界、代码 audit、Python exception 和
  invalid artifact 都作为同 conversation observation；仍有 turns 时模型可以修。
- final turn 有效 artifact 接受；final turn 无效则 phase exhausted。
- artifact validator 的 exception 会转成稳定、截断的模型可见错误，而不是泄漏 traceback/private data；
  Driver import/build failure 优先回传 worker 的结构化 terminal exception，避免长 traceback 把根因截掉。

### 11.3 Driver/Repair failure

- pre-freeze source/import failure：`development_rejections`，不运行 Harness，不消耗 attempt。
- frozen Driver Harness FAIL：消耗一个 attempt，并在剩余预算内触发 Repair。
- Harness infrastructure/worker/video failure：保留其真实类别和证据，不改写成 candidate PASS。
- 三个 frozen attempts 用尽：final frozen report 是 terminal Capability Validation。

### 11.4 Inline audit 与可选 reference diagnostic failure

动态 IVC 的 seal gate 是 request/schema/source、inline operator、metric/unit、真实 entity、mandatory
guards 和 criteria-copy 审核；其中任一项不成立就把可修正错误返回同一 IVC conversation，六回合耗尽
则停在 IVC failure，不启动 candidate。某个 scoped protocol 若另外运行 private reference diagnostic，
其失败只说明 reference adapter/calibration 路径没有闭合，不能用来改写 IVC case，也不能让 Framework
回退到旧 `binding_id`。该 diagnostic 不是本轮主线实现的验收门禁。

### 11.5 Partial diagnostic

“完整 Driver 通过”要求整套 capability suite 通过；但 diagnostic canary 还单独报告每个 capability。
只要至少一个 capability 的 nominal 与 boundary 都通过，ReCAP 可以拿到非空 whitelist 并真实调用；
这不把整套 Driver 标成 PASS。Task Demo 也可以 FAIL，只要真实物理执行、Harness verdict 和视频证据
被保留。

### 11.6 Evolution failure

Evolution 是 non-blocking。proposal schema error、模型失败或 `{}` 都不改变已完成 cell、Driver、
Harness verdict 或 retry 决策。没有人工 accept/reject + reason 就不会生成 later-run Experience。

## 12. AA1 TaskPlanner 与当前 ReCAP

AA1 TaskPlanner 是旧 Demo controller：它把预先写好的 arm、quadruped 等机器人方法包装成工具，
让 ReAct 根据自然语言任务选择调用并录制视频。它假设这些上层方法已经存在；它不负责 TGCD、
Driver synthesis、implementation-blind validation，也不是可信 verdict owner。

当前 ReCAP 的关键差别是：

| AA1 TaskPlanner | 当前 ReCAP Task Demo |
|---|---|
| 工具来自预写 robot controller | 工具动态来自本 cell sealed design 的通过 whitelist |
| 接口不证明 capability 已验证 | 只有 nominal+boundary 双过 capability 可见 |
| 旧 Demo 自己组织执行 | 每 task 一个持续、credential-free canonical MuJoCo worker |
| Demo completion 容易与成功混在一起 | controller completion 与可信 Harness verdict 明确分离 |
| 不是主线 synthesis 的下游证据 | 是 Generate/Repair/Capability Validation 后的独立 Task Demo |

因此新版本不复制 AA1 TaskPlanner，也不引入 AWS CodeInterpreter、SSH/SCP、EXPORT、MCP 或旧
Demo。`autoadapter2.b2` 下保留的入口只是已有 worker/Harness import 的兼容层。

## 13. Archived fixed-input Driver comparison

原 Exp1a/后改名 Exp2a 的固定输入 Driver comparison 已整体归档到：

`experiment/archive/pre_thesis_realign_2026-08-31/experiment1a_generation/`

其中保留原 manifest、runner、tests、runs 和 fixed bundles；历史固定输入位于
`validation/fixed_validation_bundles/`。这些文件只用于复核旧证据，不是当前可运行的实验主线，
旧矩阵与结果也不进入未来实验分母。本文不再提供 archived runner 的启动命令。

## 14. Archived fixed-input cross-robot cohort

原固定 33-cell cross-robot cohort 已整体归档到：

`experiment/archive/pre_thesis_realign_2026-08-31/experiment3_fixed_input_cross_robot/`

其中保留原 manifest、protocol、fixed inputs、diagnostics 和 runner。它们记录当时的实现与结果，
但不构成新实验设计、readiness gate 或受支持的 launch interface。新 cross-robot 实验必须等待
另行确认的 thesis-aligned manifest/protocol；本文不再提供 archived runner 的启动命令。

## 15. 通用零模型检查和主线 CLI

安装/环境检查：

```bash
pyenv exec python -m pip install -e 'autoadapter[test]'
PYTHONPATH=autoadapter/src pyenv exec python -m autoadapter2 check-only \
  --root autoadapter \
  --config autoadapter/configs/experiments/mainline.json
```

`package`/`check-only` 只加载 config、环境、自包含边界和 indexed packages，不创建模型 client。
inline IVC 的完整零模型主线 preflight 是：

```bash
PYTHONPATH=autoadapter/src pyenv exec python \
  autoadapter/scripts/check_ivc_inline_mainline.py --root autoadapter
```

它逐一解析 11 个 indexed packages 的 capability+task contexts、真实 MJCF entities 和同一 trusted
operator catalog；随后用确定性 artifact 执行 TGCD audit → IVC audit → SO-101 capability Harness →
双 case whitelist → scripted ReCAP/Task Harness，并另跑 Go2 inline nominal/boundary MuJoCo smoke。
输出必须明确为 `external_model_requests=0`、`experiment_cells_created=0` 和
`evidence_class=local zero-model diagnostic only`。Task Demo verdict 可以 FAIL，但必须有真实 capability
call、physics 和 Harness verdict。

当前真实主线入口是：

```bash
PYTHONPATH=autoadapter/src pyenv exec python -m autoadapter2 full \
  --root autoadapter \
  --config autoadapter/configs/experiments/<explicit-config>.json \
  --output autoadapter/runs/<new-run-id> \
  --run-id <new-run-id>
```

输出目录应为新的诊断目录。当前默认 `mainline.json` 明确是 `formal=false`；不能把它的输出升级成
正式 evidence，也不能用它替代尚未确定的新实验 manifest。未来正式实验的 matrix 和 dispatch gate
由届时获批的 thesis-aligned manifest/protocol 控制。
当前 `full` CLI 不提供 `--reuse-sealed-inputs-from` 或 `--skip-reference-calibration`：每个 cell 都使用
fresh TGCD/IVC artifact；动态主线默认依靠 inline suite 的 deterministic audit，不运行 reference
Driver diagnostic。

以上 `full` 命令只说明共享程序入口；只有获得针对某次 diagnostic run 的明确批准后才可实际调用，
且它不会因此成为正式实验 evidence。

## 16. DeepSeek SO-101 `1.0.4` 全线 canary（配置保留，当前锁定）

> revision `0.20.1` 未授权下面的真实 source/later run。只有零模型 config/evidence checker 可以
> 执行；不得读取 credential、创建 provider client 或发送 DeepSeek 请求，直到用户再次明确批准。

专用配置是 `configs/diagnostics/deepseek-so101-1.0.4-canary.json`，证据检查器是
`scripts/check_deepseek_so101_canary.py`。以下命令都从仓库根目录运行。先做零模型 gate：

```bash
PYTHONPATH=autoadapter/src:. pyenv exec python \
  autoadapter/scripts/check_deepseek_so101_canary.py preflight \
  --root autoadapter \
  --config autoadapter/configs/diagnostics/deepseek-so101-1.0.4-canary.json
```

该命令必须报告 `model_client_constructed=false`、`model_requests=0`、indexed package
`1.0.4`、empty Experience、完整 IVC worked references，以及所有固定预算。它不读取 credential，
也不证明未来任意 TGCD design 一定可以通过 IVC。这个零模型 checker 不启动 probe child；真实
source run 还要求当前 host 可以应用第 5.3 节的 macOS Seatbelt profile，不能从禁止 nested Seatbelt
的外层 sandbox 中启动。

以下环境变量与 `full` 命令仅记录以后获批时的操作接口，当前不得执行：

```bash
export AUTOADAPTER_MODEL_PROVIDER=openai-compatible
export AUTOADAPTER_MODEL_VENDOR=deepseek
export AUTOADAPTER_MODEL_ID=deepseek-v4-pro
export AUTOADAPTER_MODEL_API_BASE_URL=https://api.deepseek.com
export AUTOADAPTER_MODEL_THINKING=disabled
export AUTOADAPTER_MODEL_MAX_TOKENS=16384
export AUTOADAPTER_MODEL_TIMEOUT_S=600
export AUTOADAPTER_MODEL_TOOL_HISTORY_MODE=native

PYTHONPATH=autoadapter/src:. pyenv exec python -m autoadapter2 full \
  --root autoadapter \
  --config autoadapter/configs/diagnostics/deepseek-so101-1.0.4-canary.json \
  --output autoadapter/runs/diagnostic/deepseek-so101-104-source \
  --run-id diagnostic-deepseek-so101-104-source
```

`full` 使用通用严格 success claim：如果只有 partial capability closure 而非 whole-suite PASS，
它可能在已经完整写出诊断 artifacts 后返回 exit code 1。这不能单凭退出码判定本 canary；随后必须
运行 canary-specific evidence gate：

```bash
PYTHONPATH=autoadapter/src:. pyenv exec python \
  autoadapter/scripts/check_deepseek_so101_canary.py evidence \
  --run autoadapter/runs/diagnostic/deepseek-so101-104-source \
  --config autoadapter/configs/diagnostics/deepseek-so101-1.0.4-canary.json
```

只有下列证据全部存在时，这个 checker 才 exit 0；成功 JSON 会直接打印
`evolution_proposal`，同时标记 `human_disposition_required=true`、`later_run_started=false`。

DeepSeek canary 的判读条件不是“整套必须 PASS”，而是同时检查：

1. 使用 indexed SO-101 `1.0.4`、skeleton-assisted、`formal=false`；
2. 真实执行 STUDY → TGCD → IVC → Generate/Repair → ReCAP → Evolution；
3. IVC suite 在 candidate 之前完成 request/schema/source + inline operator/entity/unit/guard audit；
   任何单独 reference diagnostic 状态均需如实记录，但不能代替该 audit；
4. 至少一个 capability 的 nominal 和 boundary 都 PASS；
5. ReCAP 只获得双过 whitelist，并至少真实调用一个 capability；
6. Task Demo 有真实 physics、可信 Harness verdict 和视频；其 verdict 可以 FAIL；
7. Evolution proposal 在 source run 后展示给用户；在用户 accept/reject 并给非空 reason 前，
   不建立/启动 later run；
8. 不调用其他 LLM，不写入任何 archived 或未来正式实验 denominator。

source evidence gate 通过后先在对话里展示打印出的 proposal。此时
`experience_review_queue.json` 必须仍为 pending，且 `experience_snapshot.json` 不存在；只有用户明确
给出 `accept`/`reject` 和非空理由后，才能调用第 7.7 节的 review/snapshot API。若接受，later run
也必须由用户另行启动；上述 canary 命令不会自动创建或消费 Experience snapshot。

### 16.1 Archived Sonnet cross-robot diagnostics

旧 Sonnet cross-robot diagnostic runner、tests 和其历史输出随旧 cross-robot workspace 保存在：

`experiment/archive/pre_thesis_realign_2026-08-31/experiment3_fixed_input_cross_robot/diagnostics/`

活跃配置目录不再保留这些一次性 Sonnet 诊断配置，也没有对应的 active experiment
runner。不要从 archive 启动该诊断，也不要把旧输出计入未来分母；若新实验
需要相同机制，应在新 manifest/protocol 确定后建立新的入口。

## 17. 怎样读一份运行证据

建议按以下顺序审查，不要只看最后一个布尔值：

1. `package_check.json`：package version、task/source 数和环境是否正确。
2. `model_preflight.json` 与 `experiment_report.json.producer_model`：requested/returned identity 是否
   与 config 一致。
3. `stage_evidence`：STUDY/TGCD/IVC 是否 fresh、完成，Experience IDs 是否符合 visibility；IVC
   应显示 `candidate_driver_visible=false`、`experience_ids=[]`。TGCD 的完整 public artifact events
   同时写入 `design/tgcd_artifact_trace.json`；IVC 的外部 stage record 只给 event counts/summary，
   完整 callbacks 与 model-call records 留在 private `ivc_artifact_trace.json`，避免把 compiler 私有
   payload 复制到公共 aggregate。
4. `study_evidence.json`：是否真的有成功 physics steps，而不只是模型描述。
5. sealed TGCD/IVC artifacts：capability 数、task support、case roles、distinct requests、grounding
   refs、inline operator/entity/unit/guard audit 和 criteria exact copy；若 scoped run 另有 reference
   diagnostic，再单独读取其结果。
6. `frozen_driver_attempt_count` 与 `development_rejections`：区分“模型写过文件”和“正式 Harness
   attempt”。
7. 每个 attempt 的 `capability_validation_report.json`：先看 execution/integrity/video，再看 metric
   和 aggregate verdict。
8. `passed_capability_whitelist`：必须由 final report 的 nominal+boundary 双过派生。
9. whitelist 非空时，`task_demo_task_counts` 与 `task_demo_report.json` 中的 controller trace、真实
   capability call、worker evidence、Harness verdict 和视频应各自一致；whitelist 为空时改查
   `cell_report.json` 的非空 skip reason，并确认没有伪造的 report。
10. `cells[].outcomes.Evolution`：proposal 是 future lesson，不是当前 verdict；review queue 应仍是
    pending，直到人工 disposition。
11. `client.calls[]`/resource summary：物理 retries、returned model、tokens 和 wall time都要计入；
    HTTP 200 不等于 phase artifact 有效。

## 18. 不应作出的解释

- reference Driver PASS 只证明 package/IVC/Harness 正控路径，不证明 LLM Driver synthesis 成功。
- STUDY completed 不等于 Driver generated，也不等于 Harness executed。
- source/import-valid frozen Driver 不等于 capability PASS。
- 一个 capability 双过不等于完整 Driver 全套通过。
- ReCAP `finish`/controller completion 不等于 Task Demo PASS。
- 有 MP4 path 不等于视频证据完整。
- DeepSeek canary 是历史 diagnostic，不进入 archived 或未来正式实验 denominator。
- archived cross-robot cohort 的 `reference-seen`/`transfer` 是暴露描述，不支持 morphology、
  quadruped-transfer 或因果主张。
- Experience proposal 本身不支持 improvement claim；只有后续另行设计的 matched evidence 才可能
  讨论效果，本轮没有这样的实验。
