# AutoAdapter 2.0 当前主线实现说明

> 本文解释当前代码怎样运行，便于开发、审查和读取实验证据；它不是新的实验规范。
> 项目级规则仍以仓库根目录的 `AUTOADAPTER_2_AUTHORITY.md` 为准，Exp1a 与 Exp3
> 分别以各自的 scoped Authority 为准。若本文与适用 Authority 冲突，以 Authority 为准。

本文对应 `AA2-AUTH` revision `0.20.0` 的原地改造：没有 `aa1_runtime`，也没有另一套
并行协议。AA1 commit `585eb1f1fde33f17f5f9a1e169a18dd41f97b586` 中有用的
ReAct、文件工作区、持久 Python/MuJoCo session 和 expected-artifact 完成语义，被合并到
现有 AutoAdapter 2.0 模块；多模型、自动 Capability Design、自动 IVC、可信 Harness、
ReCAP 和 Experience 仍由 AA2 当前模块负责。

当前实验边界是：

- Exp1a 只准备新的文件工作流、SO-101 `1.0.4` fixed bundle 和零模型检查；84 个正式
  cells 仍锁定，未获批准前不能发送正式模型请求。
- Exp1b 已完成。本轮不重跑，不修改其 Authority、配置或结果。
- Exp2 本轮不作为活动、输入或验收门禁；保留的文件和测试不因此被改写。
- Exp3 只允许 `design-check` 和 `preflight`。正式 33 cells 仍锁定。
- DeepSeek 只用于独立、`formal=false` 的全线诊断 canary，不进入 Exp1a 或 Exp3
  denominator。

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
          +--> private reference Driver positive control --FAIL--> stop before candidate
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
| IVC | `capability_validation_suite.json` | 模型 | IVC audit + private reference positive control |
| Generate / Repair | `driver.py` | 模型 | source audit + public import/build boundary |

不存在模型可见的 `submit_study`、`submit_capability_design`、
`submit_validation_suite`、`submit_driver` 或 `check_driver`。文件本身就是交付物。

## 2. 代码职责地图

| 文件或目录 | 当前职责 |
|---|---|
| `src/autoadapter2/model_api.py` | 统一模型调用层：JSON、带消息历史的 JSON、原生 tool-use、认证、超时、有限传输重试和脱敏调用证据 |
| `src/autoadapter2/react.py` | 多轮 trace、同 turn 多工具、工具错误回传、普通 terminal-tool ReAct，以及 canonical artifact 完成循环 |
| `src/autoadapter2/driver_synthesis/interactive.py` | condition-local public 文件工作区、IVC isolated artifact workspace 和模型工具 |
| `src/autoadapter2/driver_synthesis/probe.py` | 公开投影、持久 Python/MuJoCo 进程、源码隔离、超时/步数/输出边界 |
| `src/autoadapter2/driver_synthesis/generation.py` | STUDY、Generate/GEN_ALGO、公开输入投影和 interface-only stub |
| `src/autoadapter2/driver_synthesis/repair.py` | candidate-facing 报告脱敏/压缩，以及在旧 `driver.py` 上持续 Repair |
| `src/autoadapter2/driver_synthesis/source_check.py` | candidate Driver 的静态来源、ABI、状态写入和 task-dispatch 边界 |
| `src/autoadapter2/capability_design/` | 唯一的 `capability-v2` TGCD schema、审核和阶段执行 |
| `references/capability_v2/` | 模型可见的 SO-101 六项与 Go2 五项完整 capability 参考；不含 task mapping/plan |
| `src/autoadapter2/validation_compiler/` | implementation-blind IVC、两类 case 审核和 reference-positive-control seam |
| `src/autoadapter2/harness/` | 私有 suite 执行、canonical MuJoCo worker、measurement/guard/aggregation、视频和权威 verdict |
| `src/autoadapter2/task_demo/recap.py` | canonical ReCAP Task Demo controller |
| `src/autoadapter2/b2/` | ReCAP worker/adapter/Harness 的薄兼容入口；不再拥有第二份 controller |
| `src/autoadapter2/evolution.py` | 一次 terminal proposal、人工 disposition、review queue 和 later-run snapshot |
| `src/autoadapter2/pipeline.py` | 按顺序组合阶段、建立可见性边界、冻结 Driver、派生 whitelist、落盘证据 |
| `experiment/experiment1a_generation/` | 固定输入的 84-cell Exp1a 配置、runner、fixed bundle 与零模型诊断 |
| `experiment/experiment3/` | 固定 33-cell Exp3 Authority、manifest、preflight runner 和报告边界 |

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
fixtures 和旧 caller。真实 `JsonModelClient` 提供 `generate_tool_turn`，因此当前主线必然选择上述
文件工作流；兼容分支不能作为 Exp1a、Exp3 或 DeepSeek canary 的执行证据。

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
- IVC 使用 isolated artifact session：没有 robot package、public assets、skeleton 或 Driver staging；
  `AUTOADAPTER_PROBE_SCENE` 只指向 workspace 内的空 MuJoCo scene，供有界的数值/校准计算使用。
  真实 robot reference execution 由 Framework positive-control hook 完成，不由 IVC 模型进程完成。
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
  每 phase 20 simulated seconds；当前通用 mainline 与 Exp3 config 把输出进一步固定为 12,000。
  Pipeline 会把同一 `execute_python` block 传给 STUDY、TGCD、IVC、Generate 和 Repair。Exp1a 的
  scoped runner 只固定 turn budgets，未另行覆盖时使用 24,000 的代码默认。具体运行以适用的
  scoped config/runner 为准。
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
| IVC | sealed design、private instances/bindings/guards、sanitised examples | Experience、candidate/Repair/history/verdict | `capability_validation_suite.json` | 6 turns |
| Generate | sealed public design、STUDY、public package、runtime/probe facts、eligible Experience；skeleton condition 另见 skeleton | private suite/bindings/guards/reference/Harness | `driver.py` | skeleton 22；scratch 40 turns |
| Capability Harness | frozen Driver、sealed design、private suite/package | 模型上下文、Experience | report + videos | 最多 3 frozen attempts |
| Repair | prior Driver、紧前 attempt 的 candidate-facing report/media、public inputs、eligible Experience；按 condition 见 skeleton | private definitions/reference/Harness source | 修改同一 `driver.py` | skeleton 22；scratch 20 turns |
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
`task_id`、`task_support`、task mapping、calls、waypoints、macro 和 plan，避免把 Exp1b oracle
使用方案泄漏成 TGCD 答案。其他九台机器人可以参考合同形式，必须自己设计 target-specific
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

这里的“脱敏示例”特指
`references/capability_v2/ivc_examples.json` 中的 structure-only pair example：它只示范
nominal/boundary 两个 case 应包含哪些字段、怎样引用 Framework 已提供的 scene/reset
instance、binding 与 guard ID，以及怎样原样复制 sealed `criteria[]`。它不含真实 ID、request、数值、threshold、task mapping、
waypoint、call sequence 或 expected verdict，因此不是候选 Driver 的 oracle。

确定性审核要求 case 数量准确、capability/method 对应、criteria 原样复制、request 严格符合
sealed closed schema；如果所引用的 private instance 还给出了真实 `request_domain`，request 也必须
落在该 domain 内。`request_anchors` 只提供私有物理参考，既不是预写 case，也不要求 IVC 复制。
同一 capability 的 nominal/boundary request 必须不同；instance/binding/guard IDs 来自 supplied private records、binding
确实属于所选 instance、role/capability binding 不被改变、
binding 声明的 metric/unit 与 sealed criterion 一致，以及 repetitions/timeout 不被弱化。审核通过后，Framework 用
package-private reference Driver 执行完整 suite；正控失败时不
封存 IVC artifact，而把脱敏 calibration error 返回同一 IVC conversation（仍有 turns 时可修正）。

Package 可以在 `capability_validation/private/` 提供专用 capability execution context；若没有，
IVC/Harness 使用现有 `tasks/private/` 的可信 scene/reset、binding 与 guards。两种路径都只给
Framework 提供物理执行和测量上下文，不给 IVC 预写 request。Task Demo Harness 始终按原来的
task-private records 执行，candidate 也始终看不到 private task ID。

SO-101 `1.0.4` 的专用 context 保留 Exp1 fixed suite v6 的 A1--A6 `H1`/`H3` 场景、reset 与
物理参考 anchors，共 12 个 nominal/boundary fixtures 和 6 个 trusted `b1_contract` bindings；
最终 request 仍由本次 IVC 写出。其 native reference renderer
接受 3--6 项已知 profile 子集，但每项 request-schema 物理签名与 criterion metric/unit 必须唯一
匹配 A1--A6 之一；随后把 TGCD 的 method names 包装到 fixed task-blind capability Driver。未知、
重复、混合新旧协议或其他 open-world schema 都会 fail closed，不能据此声称任意 TGCD design 已被
正控覆盖。

Capability Harness 对 candidate 只传 native `{"request": <case.request>}`，capability-v2 report
也不写 `task_id`。trusted reference boundary 可以使用 Framework-private execution envelope，但不会
因此放松 candidate 的 `candidate_request_boundary=True`。对于 SO-101 的 `b1_contract`，Harness
执行可信物理合同并以 `0/1` 结果做 all-trials 判定；它仍要求 suite 的单项 sealed criterion 与
binding metric/unit 一致，而不会把二进制合同结果错误地拿去和公开的 metre/radian threshold 比较。

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

Repair 只收到紧前 attempt 的 candidate-facing report。`repair.py` 移除 suite、binding/guard
definitions、hidden expected values/trajectory、reference/Harness source、credential 和 private paths；
保留对修复有用的公开 request、实测值、exception、guard outcome、trajectory endpoint summary 和
视频 manifest。dense samples 会压缩，不把整个私有执行定义交给模型。

Repair 直接从 prior source 初始化同一 workspace 的 `driver.py`，然后覆盖该文件；每次通过
source/import boundary 的版本被复制为新的 frozen attempt。三次都未通过时，终态使用第三份
frozen Driver 的报告。

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

每项 case 至少绑定：

```text
case_id, case_role, capability_id, method_name,
request,
instance_id, binding_id, guard_ids[],
criteria[], repetitions, timeout_sim_s
```

`case_role` 只能为 `nominal` 或 `calibrated_boundary`，每个 capability 每种恰好一个；criteria 与
sealed design 完全相等，不能调 threshold；`request` 必须严格满足该 capability 的 closed
`request_schema`，且同一 capability 的 nominal 与 boundary requests 不能相同。

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
| public package/tasks/sources | ✓ | ✓ | design identity only | ✓ | ✓ | ✓ | public task only | ✓ | compact facts only |
| STUDY | writer | ✓ | — | ✓ | ✓ | — | — | — | compact facts only |
| eligible Experience | ✓ | ✓ | — | ✓ | ✓ | — | — | — | — |
| trusted skeleton source | — | — | — | skeleton only | skeleton only | worker import only | — | — | — |
| sealed capability design | — | writer | ✓ | ✓ | ✓ | ✓ | whitelist projection | ✓ | compact facts only |
| private instances/bindings/guards | — | — | ✓ | — | — | ✓ | — | ✓ | — |
| validation suite | — | — | writer | — | definition redacted | ✓ | — | — | — |
| reference Driver source | — | — | Framework hook only | — | — | — | — | — | — |
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
    │   └── reference-positive-control/
    │       ├── reference_positive_control.json
    │       └── validation/...                 # private reference Harness evidence/videos
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
| private suite | IVC model + deterministic audit | private reference positive control，随后 candidate capability Harness |
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

### 11.4 Reference failure

正式 run 中，IVC private reference positive control 是 suite seal gate。它验证 Framework、case binding、scene、
reference Driver 和 Harness 的组合，不是模型 candidate 成绩。正控失败时 candidate generation 不应
开始；错误归于 IVC/reference calibration 路径。通用 `full` CLI 不提供 skip 参数；本项目的显式
`formal=false` Sonnet/DeepSeek diagnostic runner 可以调用底层 diagnostic-only skip，而 scoped formal
runner 不允许跳过。任何 skip 记录都必须是 `skipped=true`、有 reason、
`passed=false`，且不能进入正式 denominator。

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

## 13. Exp1a：固定输入 Driver comparison

Exp1a 不在每个 cell 重新跑 TGCD/IVC。每台机器人使用预先封存的 capability design、pass standard
和完整 private suite，从 STUDY 开始比较 LLM Driver synthesis。

固定矩阵：

```text
2 robots × 7 LLMs × 2 conditions × 3 replicates = 84 cells
```

- robots：`robotstudio_so101` `1.0.4`、`unitree-go2-stock-12dof`（冻结 bundle）；
- LLMs：M1--M6、M8；M7 仅保留历史，不复用编号；
- conditions：skeleton-assisted、from-scratch；
- replicates：`r01--r03`；
- 每 cell 最多三个 frozen Driver attempts；
- 不运行 Task Demo、high-level controller、Evolution；Experience 为空；
- 最终统计 final frozen Driver，不使用 best attempt。

表格中的 `Study completed / 3` 表示同一 robot × LLM × condition 的三个 replicates 中，有几次
写出并通过审核的 `study.json`。`Driver fully validated / 3` 也按每个 replicate 的最后一份 frozen
Driver 计算：SO-101 必须通过 fixed suite 的 `18/18` cases，Go2 必须通过 `15/15` cases，才把该
replicate 计为一次 fully validated；较早 attempt 曾通过部分 cases 或取得更高分都不能替代 final
frozen attempt。这就是“按照最后一次正式提交是否整套通过”的成功率，不是 best-attempt 成功率。

本轮只允许：manifest resolution、`--check-only`、fixed-bundle checks 和 reference positive control。
`manifest.json` 的 `formal_dispatch_enabled=false` 是硬门；runner 在创建正式 client 前拒绝 dispatch。

零模型例子：

```bash
PYTHONPATH=autoadapter/src:. pyenv exec python \
  experiment/experiment1a_generation/run_b1.py \
  --manifest experiment/experiment1a_generation/manifest.json \
  --unit-id 'b1::robotstudio_so101::M1::r01::skeleton-assisted' \
  --check-only

PYTHONPATH=autoadapter/src:. pyenv exec python \
  experiment/experiment1a_generation/diagnostics/reference_positive_control.py \
  --robot robotstudio_so101 \
  --output autoadapter/evidence/diagnostic/exp1a-reference/robotstudio_so101
```

第二个命令运行真实 MuJoCo/Harness reference diagnostic，但模型调用数为 0；它不是正式 cell。

## 14. Exp3：固定 33-cell cohort

Exp3 是描述性 cross-configuration cohort：

```text
11 configurations × r01--r03 × Sonnet 4.6 × skeleton-assisted = 33 cells
```

11 个 configuration IDs 固定为：

```text
robotstudio_so101
unitree-go2-stock-12dof
franka_panda
kinova_gen3_robotiq_2f85
ufactory_xarm7
universal_robots_ur5e_robotiq_2f85
piper
kuka_iiwa_14
leap_hand
hello_robot_stretch_2
aloha_2
```

每个 indexed package 都必须提供非空 `morphology.public_observations`，因为 ReCAP worker 只能把
这些 package-declared public observations 返回模型；缺少 observation adapter 的 package 不能靠
通用 state dump 冒充 Exp3 readiness。

每 cell 都 fresh STUDY、TGCD、IVC、workspace、conversation 和 reset，empty Experience，最多三个
frozen attempts，ReCAP Task Demo 后停止，Evolution disabled。SO-101/Go2 的六行单列为
`reference-seen controls`，其余 27 行为 `transfer cells`；这只是 reference exposure 标签，不是
morphology 或 quadruped-transfer effect。

runner 为未来获批的正式 row 机械写入 `formal=true`，并固定
`skip_reference_calibration=false`；因此每个 fresh IVC 都必须在 candidate generation 前通过自己的
private reference positive control，不能拿 preflight 的历史 readiness 文件代替。final Driver 只要有
至少一个 nominal+boundary 双过 capability，就必须用该 whitelist 运行 ReCAP；正式 evidence gate
要求执行的 Task Demo 有 1--5 个由该 whitelist 支持的 tasks 和完整视频（有至少五个 eligible tasks
时由 pipeline 取五个）。若 whitelist 为空，则必须保留非空 not-run
reason。两种终态都进入 33-cell 描述性 denominator，不把 partial capability closure 改写为 full-suite
Driver PASS。

当前允许的零模型命令：

```bash
PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  design-check --manifest experiment/experiment3/manifest.json

PYTHONPATH=autoadapter/src pyenv exec python -m experiment.experiment3.runner \
  preflight --root autoadapter \
  --manifest experiment/experiment3/manifest.json
```

`manifest.json` 保持 `formal_dispatch_authorised=false`。本轮不要调用 runner 中为将来保留的
`formal` 或 `resume` 子命令。

这里的 preflight 证明 11 个 indexed packages、配置和既有 reference-readiness evidence 可用；它
不能预先证明任意未来 TGCD 自创的 criterion metric 都能匹配 package-private binding。这个兼容性
只能由每个 fresh cell 的 IVC schema/binding audit 与随后真实 reference positive control 判定。

## 15. 通用零模型检查和主线 CLI

安装/环境检查：

```bash
pyenv exec python -m pip install -e 'autoadapter[test]'
PYTHONPATH=autoadapter/src pyenv exec python -m autoadapter2 check-only \
  --root autoadapter \
  --config autoadapter/configs/experiments/mainline.json
```

`package`/`check-only` 只加载 config、环境、自包含边界和 indexed packages，不创建模型 client。
当前真实主线入口是：

```bash
PYTHONPATH=autoadapter/src pyenv exec python -m autoadapter2 full \
  --root autoadapter \
  --config autoadapter/configs/experiments/<explicit-config>.json \
  --output autoadapter/runs/<new-run-id> \
  --run-id <new-run-id>
```

输出目录应为新的诊断/实验目录。当前默认 `mainline.json` 明确是 `formal=false`；不要用它的通用
cohort 配置代替 Exp1a 或 Exp3 scoped runner，也不能把它的输出升级成正式 evidence。正式实验的
matrix 和 dispatch gate 由各自 Authority/manifest 控制。
当前 `full` CLI 不提供 `--reuse-sealed-inputs-from` 或 `--skip-reference-calibration`：每个 cell 都使用
fresh TGCD/IVC artifact，并在 candidate generation 前运行本 cell 的 private reference positive
control。

## 16. DeepSeek SO-101 `1.0.4` 全线 canary

专用配置是 `configs/experiments/deepseek-so101-1.0.4-canary.json`，证据检查器是
`scripts/check_deepseek_so101_canary.py`。以下命令都从仓库根目录运行。先做零模型 gate：

```bash
PYTHONPATH=autoadapter/src:. pyenv exec python \
  autoadapter/scripts/check_deepseek_so101_canary.py preflight \
  --root autoadapter \
  --config autoadapter/configs/experiments/deepseek-so101-1.0.4-canary.json
```

该命令必须报告 `model_client_constructed=false`、`model_requests=0`、indexed package
`1.0.4`、empty Experience、一个 sanitised IVC example，以及所有固定预算。它不读取 credential，
也不证明未来任意 TGCD design 一定可以通过 IVC。这个零模型 checker 不启动 probe child；真实
source run 还要求当前 host 可以应用第 5.3 节的 macOS Seatbelt profile，不能从禁止 nested Seatbelt
的外层 sandbox 中启动。

真实 source run 前，私下设置 `AUTOADAPTER_MODEL_API_KEY`，并把其他 runtime identity 与 config
对齐；不要把 key 写进 shell history、config 或输出目录：

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
  --config autoadapter/configs/experiments/deepseek-so101-1.0.4-canary.json \
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
  --config autoadapter/configs/experiments/deepseek-so101-1.0.4-canary.json
```

只有下列证据全部存在时，这个 checker 才 exit 0；成功 JSON 会直接打印
`evolution_proposal`，同时标记 `human_disposition_required=true`、`later_run_started=false`。

DeepSeek canary 的判读条件不是“整套必须 PASS”，而是同时检查：

1. 使用 indexed SO-101 `1.0.4`、skeleton-assisted、`formal=false`；
2. 真实执行 STUDY → TGCD → IVC → Generate/Repair → ReCAP → Evolution；
3. IVC suite 在 candidate 之前完成 schema/binding audit；`formal=false` canary 可显式跳过 private
   reference positive control，但必须在报告中记录 skip，且仍只算 diagnostic；
4. 至少一个 capability 的 nominal 和 boundary 都 PASS；
5. ReCAP 只获得双过 whitelist，并至少真实调用一个 capability；
6. Task Demo 有真实 physics、可信 Harness verdict 和视频；其 verdict 可以 FAIL；
7. Evolution proposal 在 source run 后展示给用户；在用户 accept/reject 并给非空 reason 前，
   不建立/启动 later run；
8. 不调用其他 LLM，不写入 Exp1a/Exp3 denominator。

source evidence gate 通过后先在对话里展示打印出的 proposal。此时
`experience_review_queue.json` 必须仍为 pending，且 `experience_snapshot.json` 不存在；只有用户明确
给出 `accept`/`reject` 和非空理由后，才能调用第 7.7 节的 review/snapshot API。若接受，later run
也必须由用户另行启动；上述 canary 命令不会自动创建或消费 Experience snapshot。

### 16.1 Sonnet 其余机器人 skeleton 诊断

`configs/experiments/sonnet-exp3-remaining-skeleton-diagnostic.json` 固定 Exp3 的 Sonnet 4.6
model、file-workflow budgets、skeleton-assisted、empty Experience 和 Evolution disabled，但明确
保持 `formal=false`。它包含除 SO-101 外的 10 台机器人。诊断入口每次只取其中一台，显式从
`.env.company-api` 加载 company credential，调用 fresh STUDY/TGCD/IVC/Generate/Repair/ReCAP
主线，并按本轮指示跳过 reference positive control：

```bash
PYTHONPATH=autoadapter/src pyenv exec python \
  experiment/experiment3/diagnostics/run_sonnet_skeleton_diagnostic.py \
  --robot unitree-go2-stock-12dof \
  --output autoadapter/runs/diagnostic/sonnet-go2-skeleton-20260825 \
  --run-id sonnet-go2-skeleton-20260825
```

其他允许值是 `franka_panda`、`kinova_gen3_robotiq_2f85`、`ufactory_xarm7`、
`universal_robots_ur5e_robotiq_2f85`、`piper`、`kuka_iiwa_14`、`leap_hand`、
`hello_robot_stretch_2` 和 `aloha_2`。入口在发出模型请求前先验证 exact model pin、credential
来源和单机器人 package；结束时打印 stage/Driver/ReCAP 摘要、requested/returned model、累计 token
以及 evidence path。这些运行不占 Exp3 的 33-cell denominator，也不授权正式 dispatch。

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
5. sealed TGCD/IVC artifacts：capability 数、task support、case roles、criteria copy，以及 reference
   positive control 是通过还是在 `formal=false` 诊断中被显式跳过。
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
- DeepSeek canary 是 diagnostic，不进入 Exp1a/Exp3 denominator。
- Exp3 的 `reference-seen`/`transfer` 是暴露描述，不支持 morphology、quadruped-transfer 或因果主张。
- Experience proposal 本身不支持 improvement claim；只有后续另行设计的 matched evidence 才可能
  讨论效果，本轮没有这样的实验。
