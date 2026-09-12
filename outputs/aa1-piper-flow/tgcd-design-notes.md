# TGCD 输入、交付与场景准备

当前实现是 `真实 STUDY → TGCD → GENERATE`。TGCD 联合生成 capability、criteria 和 task-support；当前 Python 检查不证明物理可行性。下面的“场景准备”是后续设计建议，尚未接入运行线。

## 当前输入与工具

初始 user 消息给出 AA1 robot ID 和文件路径。模型通过 `read_file` 获取实际内容，文件内容作为 tool message 进入下一轮请求。

- `study_path`：原 study 产生的 `study.json`。AA1 两条生成路线直接传入原文件路径。直接调用 TGCD API 时如果只提供 study 字典，才保存一个单独的 study 文件。
- `catalog_path`：`index.yaml` 为该机器人绑定的公开任务库。例如 Piper 为 `AA1/auto_adapter/task_libraries/piper/1.0.0/catalog.json`。
- `skeleton_context_path`：可选的底层控制工具说明文件；from-scratch 不提供。

独立的 `sources.json` 不作为模型输入；任务自带的评分条款和 `source_refs` 保留。任务库身份从 catalog 读取，Python 检查输出身份。任务内容不再复制进 `authoring_brief.json` 或 `public_inputs.json`；新运行不生成这两个文件。旧诊断文件保留为历史记录。

模型工具只有 `read_file` 和 `write_file`。模型读完 study 与 catalog 后，将完整 JSON 写入 `draft/capability_design.json`，可以分块追加。Python 解析和检查草稿，错误随工具结果反馈，在最多六次模型调用内修正。通过后保存 `capability_design.json`，并从它导出 `criteria.json`。没有独立的 `submit_design` 工具，也没有第二个模型生成 criteria。

Python 检查身份、必要字段、唯一性、任务引用、数值类型等。它不校准阈值，不证明任务覆盖或物理效果。原文件存在也不能把本次模型调用失败变成成功。

## 后续建议：准备好场景，执行时 fresh 创建

cap 自由设计，场景应在 cap 草案之后准备。建议在 capability preparation 内按顺序完成：

1. TGCD 生成 cap / criteria 草案。
2. 根据每个效果生成场景需求、具体 request、初始条件和测量方式。
3. 框架在实际机器人 MJCF 上加入必要场景元素，编译并检查模型。
4. 检查目标配置、碰撞、接触观测和实际动力学执行；有合适的独立 reference 时执行正向检查。
5. 保留修正原因，确定场景、case 和本次设计，再进入后续生成。

LLM 提出所需环境，框架负责构建。关节运动通常不需要新物体；末端运动需要具体目标；接触能力需要表面或物体及其物理参数。机器人自身的模型来自实际 MJCF。

对于 Piper 末端运动，可在有效独立臂关节范围内采样配置，用正向运动学生成匹配的末端位置和姿态，再过滤碰撞。这给出一个目标配置的存在性示例，不证明从指定初态出发的轨迹无碰撞或控制器能完成。测试执行必须从该 case 的初态开始，经真实控制和仿真推进；目标采样时设置 qpos 不能当作执行成功。

前置阶段保存可复用的场景文件和 case 初始条件。后续 framework 每次读取同一份描述，新建 MuJoCo model/data 和 driver，设置 case 初态，再执行和测量；不继承前一次探测后的仿真状态，也不在每次 trial 重新请求 LLM 设计环境。

MuJoCo 的模型编辑接口支持从现有 XML 加载模型描述、添加场景元素并编译。[官方模型编辑文档](https://mujoco.readthedocs.io/en/stable/python.html#model-editing)

需要分别报告：几何配置可行、动力学执行完成、以及标准本身的依据。Reference 通过特定 case 不自动证明标准合理或整个请求空间可行。若没有适合动态能力的独立 reference，应保留这个缺口，不能把候选 driver 的自测当作独立正向检查。

这些场景与 case 步骤尚未实现，也不构成已批准的正式实验协议。
