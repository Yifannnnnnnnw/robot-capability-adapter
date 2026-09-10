# Capability repair diagnostics

`resume_capability_diagnostics.py` 是 Exp1 第一阶段的有界续跑入口。它从已有候选开始，最多调用 3 次真实 Holistic Framework repair；每次 repair 后由独立的 `AA1/.venv/bin/python` 子进程重新加载候选、运行真实 Framework 和 MuJoCo，并把结果写入新的 workspace。脚本不调用旧 `eval.py`、TaskPlanner 或 ReCAP；完整 Framework 通过后只记录 `not_run_pending_recap`，第二阶段另行执行。

## CLI

```bash
AA1/.venv/bin/python AA1/autoadapter_bench/resume_capability_diagnostics.py \
  --robot piper \
  --source AA1/autoadapter_bench/diagnostics/capability_update_20260909/generated_opus48_retry1/piper \
  --output-root AA1/autoadapter_bench/diagnostics/capability_update_20260909/repair3_opus48_20260910 \
  --model eu.anthropic.claude-opus-4-8 \
  --max-repairs 3
```

`--max-repairs` 只接受 `0..3`，默认 `3`。每个输出根目录对同一 robot 只允许运行一次：若 `summary_<robot>.json`、baseline report 或任一 `repair_N/<robot>/` 已存在，脚本会明确拒绝并退出，不覆盖旧结果。

每轮目录为 `repair_N/<robot>/`。标准候选只复制 `driver.py` 和存在的 `study.json`；H1 只复制 `driver_from_scratch.py`、`driver.py` wrapper 和存在的 `study.json`。MJCF 由可信 robot zoo 通过 symlink 提供。源报告只在根摘要中保留路径指针；repair workspace 只收到 `feedback_input.json` 中的通过状态、测量值和错误，不复制完整 `validate_report.json`、suite、reference 或其他私有报告。

如果源目录没有 `driver.py`（H1 则缺少 `driver_from_scratch.py` 或 wrapper），摘要状态为 `not_resumable`，不会自行重新生成。普通 robot 只接受源目录的完整 `validate_report.json`；A1/ANYmal 才可读取 `capability_validation_canary/suite_result.json` 作为 scoped feedback。若没有可用报告，第一轮先在独立的 `baseline/<robot>/` workspace 做一次真实 Framework baseline，保留该目录的完整 evidence，正式 `repair_N/<robot>/` 仍只包含候选文件和 feedback，然后才进入 repair。

标准 robot 使用 `SelfAssemble._phase_repair(feedback: str, attempt: int)`；H1 使用 `FromScratchOrchestrator.phase_gen_repair(feedback: str, attempt: int)`。两者都直接接收已由 `validate_failure_feedback(report)` 生成的字符串；repair 阶段保留 trace、token usage、duration 和错误摘要。Framework 子进程保存 `framework_subprocess.json`、真实 `validate_report.json`，以及 Framework 生成的 trace/video 路径；脚本不复制或合成物理帧，也不使用评分替代物。

Unitree A1 和 ANYmal C 只检查可信 capability interface 和已校准的 G4 nominal。它们的报告明确包含 `conditions_checked=1`、`required_conditions_total=10`、`scope_all_ok`，并且强制 `all_ok=false`；即使 scoped canary 通过，也不会被记录为完整 Framework 通过，状态为 `native_scoped_pass` / `not_eligible_scope_incomplete`。

摘要 `summary_<robot>.json` 每个阶段都会落盘，包含源候选/报告指针、每一轮 feedback、repair、Framework 状态和最终 `task_status`。完整 Framework 失败的候选不会进入任务阶段，状态为 `not_eligible_framework_failed`；完整通过的候选状态为 `not_run_pending_recap`。

## 最小检查

```bash
AA1/.venv/bin/python -m py_compile AA1/autoadapter_bench/resume_capability_diagnostics.py
AA1/.venv/bin/python AA1/autoadapter_bench/resume_capability_diagnostics.py --help
```

这两个命令不会启动模型、Framework 或仿真。真实 repair 运行由 root 在审查接口后按 robot 单独启动。
