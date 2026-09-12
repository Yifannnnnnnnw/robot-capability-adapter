#!/usr/bin/env python3
"""Build the compact AA1 Piper flow dataset from one diagnostic run.

This is a read-only collector for the in-conversation fragment.  The raw
records exporter remains the source of complete per-turn files.  Here the
last full framework/gateway conversation is stored once per stage and each
turn keeps only its recorded message counts; the template slices that shared
prefix when a turn is selected.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEST = HERE / "piper-flow.json"

# These are fallback identifiers for the same parent repository.  A recorded
# run report supplies the actual code and pipeline integration commits.
PIPELINE_COMMIT = "6d4e8162"
CODE_COMMIT = "a5b6d748"

OLD_FAILURE_ROOT = Path(
    "/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-22ogt83k"
)
INTERRUPTED_ROOT = Path(
    "/var/folders/06/wkt8rx111gsfkcpys_m16_th0000gn/T/aa1-tgcd-piper-recorded-7g44h84e"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            values.append(value)
    return values


def record(label: str, value: Any, status: str | None = None) -> dict[str, Any]:
    item = {"label": label, "value": value}
    if status is not None:
        item["status"] = status
    return item


def artifact(path: Path, label: str | None = None) -> dict[str, Any] | None:
    """Return an actual source artifact, or None when it is absent."""

    if not path.is_file():
        return None
    if path.suffix == ".json":
        value = read_json(path)
    else:
        value = path.read_text(encoding="utf-8")
    return {"label": label or path.name, "value": value, "path": str(path)}


def append_if_present(items: list[dict[str, Any]], item: dict[str, Any] | None) -> None:
    if item is not None:
        items.append(item)


def pair_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair each request with the following raw response or error event."""

    pairs: list[dict[str, Any]] = []
    for position, event in enumerate(events):
        if event.get("event") != "request":
            continue
        pair: dict[str, Any] = {"request": event}
        for later in events[position + 1 :]:
            if later.get("event") == "request":
                break
            if later.get("event") in {"response", "error"}:
                pair.setdefault(later["event"], later)
        pairs.append(pair)
    return pairs


def messages_path_for(trace_path: Path) -> Path:
    return trace_path.with_name(trace_path.name.removesuffix(".jsonl") + ".messages.jsonl")


def tool_result_messages(current: Any, following: Any) -> tuple[list[Any], bool]:
    """Extract raw tool-result messages appended by the next request."""

    if not isinstance(current, list) or not isinstance(following, list):
        return [], False
    prefix_match = following[: len(current)] == current
    appended = following[len(current) :] if prefix_match else following
    result: list[Any] = []
    for message in appended:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            result.append(message)
            continue
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        ):
            result.append(message)
    return result, prefix_match


def result_blocks_for_ids(messages: list[Any], ids: set[str]) -> list[Any]:
    """Select raw result blocks for a known read_file call without duplication."""

    blocks: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            if message.get("tool_call_id") in ids:
                blocks.append(message)
            continue
        content = message.get("content")
        if isinstance(content, list):
            blocks.extend(
                block
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in ids
            )
    return blocks


def response_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Index tool names while leaving complete tool_use blocks in response."""

    if not isinstance(response, dict) or not isinstance(response.get("content"), list):
        return []
    calls: list[dict[str, Any]] = []
    for block in response["content"]:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        calls.append(
            {
                "label": str(block.get("name", "tool")),
                "value": {"id": block.get("id"), "name": block.get("name")},
            }
        )
    return calls


def visible_text(messages: Any, role: str | None = None) -> str:
    if not isinstance(messages, list):
        return ""
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or (role and message.get("role") != role):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
    return "\n\n".join(chunks)


def first_user_prompt(messages_path: Path) -> str:
    pairs = pair_events(read_jsonl(messages_path))
    if not pairs:
        return ""
    return visible_text(pairs[0]["request"].get("messages"), role="user")


def file_based_handoff(messages_path: Path) -> dict[str, Any] | None:
    """Capture the actual TGCD path/instruction → read_file → result chain."""

    pairs = pair_events(read_jsonl(messages_path))
    if len(pairs) < 1:
        return None
    first = pairs[0]
    response_event = first.get("response", {})
    response = response_event.get("response", {}) if isinstance(response_event, dict) else {}
    calls = [
        block
        for block in (response.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "read_file"
    ]
    if not calls:
        return None
    next_request = pairs[1]["request"] if len(pairs) > 1 else None
    framework, framework_prefix = tool_result_messages(
        first["request"].get("messages"),
        next_request.get("messages") if next_request else None,
    )
    gateway, gateway_prefix = tool_result_messages(
        first["request"].get("gateway_messages"),
        next_request.get("gateway_messages") if next_request else None,
    )
    read_ids = {
        call.get("id") for call in calls if isinstance(call.get("id"), str)
    }
    # The gateway array is retained once in stage.gateway_conversation. Keep
    # one exact framework result block here to make the first handoff legible
    # without embedding the same large payload twice.
    framework_result_blocks = result_blocks_for_ids(framework, read_ids)
    return {
        "first_user_prompt": visible_text(first["request"].get("messages"), role="user"),
        "read_file_tool_use": calls,
        "next_request_framework_tool_results": framework_result_blocks,
        "gateway_tool_result_ids": [
            message.get("tool_call_id")
            for message in gateway
            if isinstance(message, dict) and isinstance(message.get("tool_call_id"), str)
        ],
        "framework_prefix_match": framework_prefix,
        "gateway_prefix_match": gateway_prefix,
        "next_request_present": next_request is not None,
    }


def stage_from_recording(
    messages_path: Path,
    stage_id: str,
    label: str,
    summary: str,
    status: str,
) -> dict[str, Any]:
    """Build one compact stage, deduplicating full conversation prefixes."""

    events = read_jsonl(messages_path)
    pairs = pair_events(events)
    trace_path = messages_path.with_name(
        messages_path.name.removesuffix(".messages.jsonl") + ".jsonl"
    )
    traces = read_jsonl(trace_path)
    stage: dict[str, Any] = {
        "id": stage_id,
        "label": label,
        "status": status,
        "summary": summary,
        "turns": [],
    }
    if not pairs:
        return stage

    first_request = pairs[0]["request"]
    last_request = pairs[-1]["request"]
    stage["request"] = {
        key: last_request[key]
        for key in ("model", "system", "tools", "max_tokens")
        if key in last_request
    }
    # The raw exporter keeps every framework message.  The fragment embeds
    # the gateway representation once per stage; it is the complete transport
    # conversation used by the prompt inspector and avoids a second large copy.
    shared_framework = last_request.get("messages", [])
    stage["gateway_conversation"] = last_request.get("gateway_messages", [])
    stage["tool_inventory"] = [
        tool.get("name")
        for tool in (first_request.get("tools") or [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    stage["conversation_prefix_shared"] = True

    for index, pair in enumerate(pairs):
        request = pair["request"]
        framework_messages = request.get("messages")
        gateway_messages = request.get("gateway_messages")
        framework_shared = (
            isinstance(shared_framework, list)
            and shared_framework[: len(framework_messages or [])] == framework_messages
        )
        gateway_shared = (
            isinstance(stage["gateway_conversation"], list)
            and stage["gateway_conversation"][: len(gateway_messages or [])] == gateway_messages
        )
        if not framework_shared or not gateway_shared:
            stage["conversation_prefix_shared"] = False

        turn: dict[str, Any] = {
            "id": f"{stage_id}-{index}",
            "label": f"第 {index + 1} 轮",
            "message_count": len(framework_messages)
            if isinstance(framework_messages, list)
            else None,
            "gateway_message_count": len(gateway_messages)
            if isinstance(gateway_messages, list)
            else None,
        }
        if not framework_shared or not gateway_shared:
            turn["request"] = {
                key: request[key]
                for key in (
                    "model",
                    "system",
                    "tools",
                    "max_tokens",
                    "messages",
                    "gateway_messages",
                )
                if key in request
            }

        if "response" in pair:
            raw_response = pair["response"].get("response")
            turn["response"] = raw_response
            if isinstance(raw_response, dict):
                turn["status"] = raw_response.get("stop_reason", "已返回")
                turn["tool_calls"] = response_tool_calls(raw_response)
        elif "error" in pair:
            turn["status"] = "调用失败"
            turn["outputs"] = [record("模型调用错误", pair["error"])]
        else:
            turn["status"] = "调用期间未取得响应"

        if index < len(traces):
            trace = traces[index]
            thought = trace.get("thought")
            if thought:
                turn.setdefault("outputs", []).append(
                    record("模型可见 trace thought", thought)
                )
            if "observations" in trace:
                turn["observations"] = [
                    record(
                        "ReAct 工具执行结果（trace，可能截断）",
                        trace.get("observations"),
                        "可能截断",
                    )
                ]
            turn["checks"] = [
                record(
                    "本轮 trace 元数据",
                    {
                        key: trace.get(key)
                        for key in ("iter", "stop_reason", "token_usage", "duration_ms")
                        if key in trace
                    },
                )
            ]
            if any(
                isinstance(observation, dict) and observation.get("is_error")
                for observation in (trace.get("observations") or [])
            ):
                turn["status"] = f"{turn.get('status', '')} / 工具报错".strip(" /")
        stage["turns"].append(turn)
    return stage


def stage_messages(workspace: Path, name: str) -> Path:
    return workspace / "traces" / f"{name}.messages.jsonl"


def required_methods(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    value = result.get("required_methods")
    return value if isinstance(value, list) else []


def missing_methods(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    value = result.get("missing_methods")
    return value if isinstance(value, list) else []


def stage_status_for_result(result: Any, prefix: str) -> str:
    if not isinstance(result, dict):
        return f"{prefix}：canary result 缺失"
    missing = missing_methods(result)
    if missing:
        return f"{prefix}：AST 缺失 {', '.join(str(item) for item in missing)}"
    if result.get("diagnostic_complete") is True:
        return f"{prefix}：诊断 AST/build 链完成"
    return f"{prefix}：只记录到诊断阶段"


def commit_ids(*reports: Any) -> tuple[str, str]:
    """Use recorded run metadata, falling back to the supplied snapshot IDs."""

    pipeline = PIPELINE_COMMIT
    code = CODE_COMMIT
    for report in reports:
        if not isinstance(report, dict):
            continue
        pipeline = str(
            report.get("pipeline_commit")
            or report.get("pipeline_wrapper")
            or pipeline
        )
        code = str(report.get("code_commit") or report.get("code_head") or code)
        if report.get("code_commit") or report.get("code_head"):
            break
    return pipeline, code


def history_stage(root: Path, stage_id: str, label: str, summary: str) -> dict[str, Any] | None:
    trace_path = root / "workspace/piper/capability_inputs/trace.jsonl"
    traces = read_jsonl(trace_path)
    if not traces:
        return None
    stage: dict[str, Any] = {
        "id": stage_id,
        "label": label,
        "status": "历史记录",
        "summary": summary,
        "turns": [],
        "inputs": [record("原始 trace 路径", str(trace_path))],
        "outputs": [],
        "checks": [],
    }
    for trace in traces:
        index = trace.get("iter", len(stage["turns"]))
        turn: dict[str, Any] = {
            "id": f"{stage_id}-{index}",
            "label": f"第 {int(index) + 1} 轮" if isinstance(index, int) else str(index),
            "status": "trace 记录",
            "outputs": [record("实际 trace", trace)],
        }
        stage["turns"].append(turn)
    for name, label_text in (
        ("canary_result.json", "canary result"),
        ("review_rejection.json", "review rejection"),
        ("interruption.json", "interruption"),
    ):
        append_if_present(stage["outputs"], artifact(root / name, label_text))
    return stage


def find_workspace(run_root: Path) -> Path:
    preferred = run_root / "workspace" / "piper"
    if preferred.is_dir():
        return preferred
    candidates = sorted(path.parent for path in run_root.rglob("study.json"))
    if candidates:
        return candidates[0]
    raise SystemExit(f"未找到运行工作区：{run_root}/workspace/**/piper")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic_run_root", type=Path, help="实际诊断运行根目录")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEST,
        help=f"dataset 输出路径（默认：{DEST}）",
    )
    args = parser.parse_args()
    run_root = args.diagnostic_run_root.expanduser().resolve()
    if not run_root.is_dir():
        raise SystemExit(f"诊断目录不存在：{run_root}")
    workspace = find_workspace(run_root)

    before_result_path = run_root / "canary_result.before_repair.json"
    study_result = read_json(before_result_path) if before_result_path.exists() else {}
    final_result = read_json(run_root / "canary_result.json") if (run_root / "canary_result.json").exists() else {}
    pipeline_commit, code_commit = commit_ids(final_result, study_result)
    study_reuse_path = run_root / "study_reuse.json"
    design_path = workspace / "capability_inputs/capability_design.json"
    criteria_path = workspace / "capability_inputs/criteria.json"

    study = stage_from_recording(
        stage_messages(workspace, "01_study"),
        "study",
        "1 · STUDY",
        "真实模型通过 STUDY 读取并探测调用者实际 MJCF",
        "STUDY 记录",
    )
    if study_reuse_path.exists():
        study["status"] = "复用 study；本次运行未新发 STUDY model call"
        study["summary"] = (
            f"study.json 与 {len(study.get('turns', []))} 轮 STUDY trace 从已记录运行复用，"
            "作为本次 TGCD/generation 输入"
        )
    study["inputs"] = [
        record(
            "实际阶段 tool 清单",
            study.get("tool_inventory", []),
            "来自首轮 request.tools",
        ),
        record(
            "运行配置",
            {
                "robot_id": "piper",
                "mjcf_path": str(REPO / "AA1/assets/mjcf/piper/scene.xml"),
                "mode": "local",
                "max_tokens_per_turn": study.get("request", {}).get("max_tokens"),
            },
        ),
    ]
    append_if_present(
        study["inputs"],
        artifact(study_reuse_path, "study 复用记录"),
    )
    study["outputs"] = []
    append_if_present(study["outputs"], artifact(workspace / "study.json"))
    append_if_present(
        study["outputs"],
        artifact(REPO / "AA1/assets/mjcf/piper/scene.xml", "实际 scene.xml"),
    )
    append_if_present(
        study["outputs"],
        artifact(REPO / "AA1/assets/mjcf/piper/piper.xml", "scene.xml include 的 piper.xml"),
    )
    study["checks"] = [
        record(
            "阶段边界",
            "STUDY 消息、trace 和 study.json 均按实际文件记录；本阶段没有独立物理 verdict。",
        )
    ]

    tgcd_messages = workspace / "capability_inputs/trace.messages.jsonl"
    tgcd = stage_from_recording(
        tgcd_messages,
        "tgcd",
        "2 · TGCD",
        "公开任务与 study → capability-v2 design → criteria",
        "TGCD 记录",
    )
    tgcd_protocol = (
        "read_file + write_file"
        if "write_file" in tgcd.get("tool_inventory", [])
        else "read_file + submit_design"
        if "submit_design" in tgcd.get("tool_inventory", [])
        else "消息中未见 read/write TGCD 工具"
    )
    if "submit_design" in tgcd.get("tool_inventory", []):
        tgcd["summary"] = "公开任务与 study → capability-v2 design → criteria（read_file + submit_design 历史实现快照）"
    elif "write_file" in tgcd.get("tool_inventory", []):
        tgcd["summary"] = "公开任务与 study → capability-v2 design → criteria（read_file + write_file 记录）"
    tgcd["inputs"] = [
        record(
            "实际阶段 tool 清单",
            tgcd.get("tool_inventory", []),
            "本次 file-based TGCD 首轮 request.tools",
        ),
        record("TGCD 实现入口", tgcd_protocol),
    ]
    append_if_present(
        tgcd["inputs"],
        artifact(
            workspace / "capability_inputs/authoring_brief.json",
            "模型通过 read_file 读取的 authoring brief",
        ),
    )
    append_if_present(
        tgcd["inputs"],
        artifact(
            workspace / "capability_inputs/public_inputs.json",
            "完整公开输入记录（模型可按受限路径读取）",
        ),
    )
    skeleton_paths = (
        workspace / "capability_inputs/skeleton_context.json",
        workspace / "skeleton_context.json",
        run_root / "skeleton_context.json",
    )
    skeleton_path = next((path for path in skeleton_paths if path.is_file()), None)
    append_if_present(
        tgcd["inputs"],
        artifact(skeleton_path, "可选 skeleton context") if skeleton_path else None,
    )
    handoff = file_based_handoff(tgcd_messages)
    if handoff is not None:
        tgcd["inputs"].append(
            record(
                "首轮文件路径 → read_file → 工具响应",
                handoff,
                "全部字段来自实际 request/response/下一轮 request",
            )
        )
    else:
        tgcd["inputs"].append(
            record(
                "TGCD 首轮输入路径",
                first_user_prompt(tgcd_messages) or "本次消息文件没有可读的首轮 user prompt。",
                "来自实际首轮 request.messages；未找到 read_file tool_use",
            )
        )
    tgcd["outputs"] = []
    for path, label_text in (
        (workspace / "capability_inputs/draft/capability_design.json", "模型设计原稿"),
        (design_path, "主 capability design"),
        (criteria_path, "从主 design 派生的 criteria"),
        (
            workspace / "capability_inputs/capability_preparation.json",
            "TGCD preparation 记录",
        ),
    ):
        append_if_present(tgcd["outputs"], artifact(path, label_text))
    design = read_json(design_path) if design_path.exists() else {}
    tgcd["checks"] = [
        record(
            "结构检查记录",
            {
                "capabilities": len(design.get("capabilities", []))
                if isinstance(design, dict)
                else None,
                "task_ids": len(design.get("task_ids", []))
                if isinstance(design, dict)
                else None,
                "supported_tasks": len(
                    {item.get("task_id") for item in design.get("task_support", [])}
                )
                if isinstance(design, dict)
                else None,
            },
        ),
        record(
            "标准含义",
            "criteria 阈值如未有来源支持只是设计提议；本阶段没有独立 reference 正向物理检查或阈值校准。",
        ),
    ]

    generate_messages = stage_messages(workspace, "02_generate")
    generation_result = study_result if before_result_path.exists() else final_result
    generation_result_path = before_result_path if before_result_path.exists() else run_root / "canary_result.json"
    generation = stage_from_recording(
        generate_messages,
        "generate",
        "3 · GENERATE",
        "原生成器消费本次内存 design，产生 driver.py",
        stage_status_for_result(generation_result, "生成"),
    )
    generation["inputs"] = [
        record("实际阶段 tool 清单", generation.get("tool_inventory", []), "来自首轮 request.tools"),
        record(
            "生成输入",
            {
                "study": "study.json",
                "design": "本次 TGCD 返回并在内存中传入 generation context",
                "required_methods": required_methods(study_result)
                or required_methods(final_result),
                "model": generation.get("request", {}).get("model"),
            },
        ),
    ]
    generation["outputs"] = []
    append_if_present(
        generation["outputs"],
        artifact(workspace / "driver.before_ast_repair.py", "修复前 driver.py"),
    )
    if not generation["outputs"]:
        append_if_present(
            generation["outputs"],
            artifact(workspace / "driver.py", "生成 driver.py"),
        )
    generation["checks"] = [
        record(
            "AST 与 request 接口核对",
            {
                "required_methods": required_methods(study_result)
                or required_methods(final_result),
                "missing_methods": missing_methods(generation_result),
                "request_keyword_incompatible_methods": generation_result.get("request_keyword_incompatible_methods", []),
                "source": str(generation_result_path),
                "available": generation_result_path.exists(),
            },
        ),
        record(
            "证据边界",
            "原始生成可解析和方法声明不代表 capability 行为完成，也没有独立物理验证。",
        ),
    ]

    stages: list[dict[str, Any]] = [study, tgcd, generation]
    repair_messages = workspace / "traces/03_repair_1.messages.jsonl"
    if repair_messages.exists():
        repair = stage_from_recording(
            repair_messages,
            "repair-1",
            "4 · REPAIR-1",
            "对修复前 AST/interface 反馈进行一次真实 ReactLoop 修复",
            stage_status_for_result(final_result, "修复后"),
        )
        repair["inputs"] = [
            record("实际阶段 tool 清单", repair.get("tool_inventory", []), "来自首轮 request.tools"),
        ]
        append_if_present(
            repair["inputs"],
            artifact(workspace / "driver.before_ast_repair.py", "修复前 driver.py"),
        )
        append_if_present(
            repair["inputs"],
            artifact(run_root / "canary_result.before_repair.json", "修复前 canary result"),
        )
        append_if_present(
            repair["inputs"],
            artifact(run_root / "repair_feedback.txt", "修复反馈"),
        )
        repair["outputs"] = []
        append_if_present(repair["outputs"], artifact(workspace / "driver.py", "修复后 driver.py"))
        append_if_present(repair["outputs"], artifact(run_root / "canary_result.json", "修复后 canary result"))
        append_if_present(repair["outputs"], artifact(run_root / "parent_interface_review.json", "父接口检查"))
        repair["checks"] = [
            record(
                "修复后结构结果",
                {
                    "diagnostic_complete": final_result.get("diagnostic_complete")
                    if isinstance(final_result, dict)
                    else None,
                    "missing_methods": missing_methods(final_result),
                    "request_keyword_incompatible_methods": final_result.get(
                        "request_keyword_incompatible_methods", []
                    )
                    if isinstance(final_result, dict)
                    else [],
                },
            ),
            record(
                "行为边界",
                "修复阶段只覆盖真实 AST/interface feedback；没有独立 physics validation。",
            ),
        ]
        stages.append(repair)

    checks_outputs: list[dict[str, Any]] = []
    for path, label_text in (
        (run_root / "canary_result.before_repair.json", "修复前 canary result"),
        (run_root / "canary_result.json", "最终 canary result"),
        (run_root / "parent_interface_review.json", "父接口检查"),
        (run_root / "focused_checks.json", "框架聚焦检查"),
        (run_root / "scene_construction_smoke.json", "场景构建入口检查（不是 capability case）"),
        (run_root / "run_canary.py", "真实 canary 脚本"),
        (run_root / "run_ast_repair.py", "真实 repair 脚本"),
        (run_root / "repair.log", "repair 日志"),
    ):
        append_if_present(checks_outputs, artifact(path, label_text))
    checks = {
        "id": "checks",
        "label": f"{len(stages) + 1} · 检查与边界",
        "status": "诊断检查；不构成物理 verdict",
        "summary": "输入接线、动态 design 传递、AST/build/interface 检查与失败边界",
        "turns": [],
        "outputs": checks_outputs,
        "checks": [
            record(
                "提交标识",
                {"pipeline_commit": pipeline_commit, "code_head": code_commit},
                "同一 parent repository 中的 pipeline integration commit 与 run code HEAD",
            ),
            record(
                "诊断接口检查结果",
                {
                    "initial_missing_methods": missing_methods(study_result),
                    "final_missing_methods": missing_methods(final_result),
                    "final_diagnostic_complete": final_result.get("diagnostic_complete")
                    if isinstance(final_result, dict)
                    else None,
                },
            ),
            record(
                "独立物理验证",
                "未执行；动态 criteria 未由独立 reference 正向检查，AST/build/interface 链不能替代真实行为验证。",
            ),
        ],
    }
    interface_review_path = run_root / "parent_interface_review.json"
    if interface_review_path.exists():
        checks["checks"].insert(
            2,
            record(
                "本次检查发现的限制",
                read_json(interface_review_path).get("observed_implementation_limitations", []),
            ),
        )
    stages.append(checks)

    for root, stage_id, label_text, summary in (
        (
            OLD_FAILURE_ROOT,
            "history-failure",
            "历史 · 首次 TGCD 失败",
            "旧诊断中模型输出达到 token 上限并提交空设计；保留实际 trace，不重构缺失响应。",
        ),
        (
            INTERRUPTED_ROOT,
            "history-interrupted",
            "历史 · 中断重试",
            "旧诊断的中断记录；仅在实际 trace/artifact 存在时展示。",
        ),
    ):
        historical = history_stage(root, stage_id, label_text, summary)
        if historical is not None:
            stages.append(historical)

    model = None
    for stage in stages:
        model = stage.get("request", {}).get("model") if stage.get("request") else None
        if model:
            break
    run_model = model or (
        final_result.get("model") if isinstance(final_result, dict) else None
    )
    data = {
        "title": "Piper · 从任务需求到生成代码",
        "run": {
            "model": run_model,
            "code_head": code_commit,
            "pipeline_commit": pipeline_commit,
            "source": str(run_root),
            "tgcd_entrypoint": tgcd_protocol,
            "性质": "真实模型/真实 ReactLoop 诊断；无独立物理验证",
        },
        "inputs": [
            record("诊断运行根目录", str(run_root)),
            record("提交标识", {"pipeline_commit": pipeline_commit, "code_head": code_commit}),
            record(
                "交互边界",
                "阶段与轮次来自真实 messages.jsonl/trace.jsonl；完整 per-turn 原文位于 record exporter 输出目录。",
            ),
        ],
        "stages": stages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "stages": [(stage["id"], len(stage.get("turns", []))) for stage in stages],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
