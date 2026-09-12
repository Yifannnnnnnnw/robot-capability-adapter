#!/usr/bin/env python3
"""Export raw AA1 Piper model-loop records for inspection.

The diagnostic runner writes one ``*.messages.jsonl`` beside each ReAct trace.
This utility only reshapes those recorded events into small per-turn files. It
does not call a model, inspect credentials, or manufacture a response when a
record is absent.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RECORDS = HERE / "records"
INDEX = HERE / "record-index.md"
REPO = HERE.parents[1]

# These are fallback identifiers for the recorded diagnostic chain.  A run
# report's code_commit wins when the run captured one.
PIPELINE_COMMIT = "6d4e8162"
CODE_COMMIT = "a5b6d748"

REQUEST_FIELDS = (
    "event",
    "model",
    "system",
    "tools",
    "max_tokens",
    "messages",
    "gateway_messages",
)


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read JSONL without changing valid event objects; retain parse failures."""

    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(
                    {
                        "line": line_number,
                        "error": str(exc),
                        "raw_line": line.rstrip("\n"),
                    }
                )
                continue
            if isinstance(value, dict):
                events.append(value)
            else:
                errors.append(
                    {
                        "line": line_number,
                        "error": "JSONL event is not an object",
                        "value": value,
                    }
                )
    return events, errors


def pair_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Associate each request with the following response/error event."""

    pairs: list[dict[str, Any]] = []
    for position, event in enumerate(events):
        if event.get("event") != "request":
            continue
        pair: dict[str, Any] = {"request": event, "event_position": position}
        intervening: list[dict[str, Any]] = []
        for later in events[position + 1 :]:
            if later.get("event") == "request":
                break
            if later.get("event") in {"response", "error"}:
                pair.setdefault(later["event"], later)
            else:
                intervening.append(later)
        if intervening:
            pair["intervening_events"] = intervening
        pairs.append(pair)
    return pairs


def trace_path_for(messages_path: Path) -> Path:
    name = messages_path.name
    if name.endswith(".messages.jsonl"):
        name = name[: -len(".messages.jsonl")] + ".jsonl"
    return messages_path.with_name(name)


def stage_id_for(messages_path: Path, run_root: Path) -> str:
    relative = messages_path.relative_to(run_root)
    name = messages_path.name.removesuffix(".messages.jsonl")
    if name == "01_study":
        return "study"
    if name == "02_generate":
        return "generate"
    if name == "trace" and "capability_inputs" in relative.parts:
        return "tgcd"
    repair = re.search(r"(?:^|[_-])repair[_-]?(\d+)", name, re.IGNORECASE)
    if repair:
        return f"repair-{int(repair.group(1))}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower()
    return cleaned or "stage"


def stage_sort_key(stage_id: str) -> tuple[int, str]:
    order = {"study": 0, "tgcd": 1, "generate": 2}
    if stage_id in order:
        return order[stage_id], stage_id
    if stage_id.startswith("repair-"):
        return 3, stage_id
    return 4, stage_id


def has_framework_tool_result(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("role") == "tool":
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def has_gateway_tool_result(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("role") == "tool":
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def appended_tool_messages(
    current: Any, following: Any, predicate: Any
) -> tuple[list[Any], bool]:
    """Return raw tool-result messages appended by the next request."""

    if not isinstance(current, list) or not isinstance(following, list):
        return [], False
    prefix_match = following[: len(current)] == current
    appended = following[len(current) :] if prefix_match else following
    return [message for message in appended if predicate(message)], prefix_match


def text_from_messages(messages: Any, role: str | None = None) -> str:
    """Make a readable snapshot while retaining the raw message JSON separately."""

    if not isinstance(messages, list):
        return ""
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or (role and message.get("role") != role):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
            elif isinstance(block, dict) and block.get("type") not in {
                "tool_use",
                "tool_result",
            }:
                chunks.append(json.dumps(block, ensure_ascii=False, indent=2))
    return "\n\n".join(chunks)


def write_initial_snapshots(stage_dir: Path, request: dict[str, Any]) -> None:
    system = request.get("system")
    if system is not None:
        (stage_dir / "initial-system-prompt.txt").write_text(
            str(system), encoding="utf-8"
        )
    messages = request.get("messages")
    if messages is not None:
        (stage_dir / "initial-framework-messages.txt").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        user_text = text_from_messages(messages, role="user")
        (stage_dir / "initial-user-prompt.txt").write_text(
            user_text, encoding="utf-8"
        )
    gateway = request.get("gateway_messages")
    if gateway is not None:
        (stage_dir / "initial-gateway-messages.txt").write_text(
            json.dumps(gateway, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if request.get("tools") is not None:
        (stage_dir / "initial-tools.txt").write_text(
            json.dumps(request["tools"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def export_stage(
    stage_id: str,
    messages_path: Path,
    run_root: Path,
    stage_root: Path,
) -> dict[str, Any]:
    events, parse_errors = read_jsonl(messages_path)
    pairs = pair_events(events)
    trace_path = trace_path_for(messages_path)
    traces, trace_parse_errors = read_jsonl(trace_path) if trace_path.exists() else ([], [])
    stage_root.mkdir(parents=True, exist_ok=True)

    if pairs:
        first_request = pairs[0]["request"]
        write_initial_snapshots(stage_root, first_request)

    turn_rows: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        request = pair["request"]
        turn_dir = stage_root / f"turn-{index:03d}"
        turn_dir.mkdir(parents=True, exist_ok=True)
        # Preserve the request event and its exact recorded request fields.
        request_copy = {key: request[key] for key in REQUEST_FIELDS if key in request}
        json_dump(turn_dir / "request.json", request_copy)

        response_kind = "missing"
        if "response" in pair:
            json_dump(turn_dir / "response.json", pair["response"])
            response_kind = "response"
        elif "error" in pair:
            json_dump(turn_dir / "response.json", pair["error"])
            response_kind = "error"

        next_pair = pairs[index + 1] if index + 1 < len(pairs) else None
        next_request = next_pair["request"] if next_pair else None
        current_messages = request.get("messages")
        next_messages = next_request.get("messages") if next_request else None
        framework_results, framework_prefix = appended_tool_messages(
            current_messages, next_messages, has_framework_tool_result
        )
        current_gateway = request.get("gateway_messages")
        next_gateway = next_request.get("gateway_messages") if next_request else None
        gateway_results, gateway_prefix = appended_tool_messages(
            current_gateway, next_gateway, has_gateway_tool_result
        )
        trace = traces[index] if index < len(traces) else None
        trace_observations = trace.get("observations", []) if trace else []
        if framework_results or gateway_results:
            result_source = "next_request"
        elif trace_observations:
            result_source = "trace_observations"
        else:
            result_source = "none"
        tool_results = {
            "stage": stage_id,
            "turn": index,
            "source": result_source,
            "next_request_available": next_request is not None,
            "next_request_framework_prefix_match": framework_prefix,
            "next_request_gateway_prefix_match": gateway_prefix,
            "framework_tool_results": framework_results,
            "gateway_tool_results": gateway_results,
            "trace_observations": trace_observations,
            "trace_observations_may_be_truncated": trace is not None,
        }
        json_dump(turn_dir / "tool-results.json", tool_results)
        if trace is not None:
            # The trace object is raw; its observations are intentionally kept
            # alongside the full next-request tool results for comparison.
            json_dump(turn_dir / "trace.json", trace)
        turn_rows.append(
            {
                "turn": index,
                "request_messages": len(current_messages)
                if isinstance(current_messages, list)
                else None,
                "request_gateway_messages": len(current_gateway)
                if isinstance(current_gateway, list)
                else None,
                "response": response_kind,
                "trace": trace is not None,
                "tool_result_source": result_source,
                "framework_tool_result_count": len(framework_results),
                "gateway_tool_result_count": len(gateway_results),
            }
        )

    return {
        "id": stage_id,
        "messages_path": str(messages_path),
        "trace_path": str(trace_path),
        "messages_relative": str(messages_path.relative_to(run_root)),
        "trace_exists": trace_path.exists(),
        "turn_count": len(pairs),
        "trace_count": len(traces),
        "tool_names": [
            tool.get("name")
            for tool in (pairs[0]["request"].get("tools") or [])
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ]
        if pairs
        else [],
        "parse_errors": parse_errors,
        "trace_parse_errors": trace_parse_errors,
        "turns": turn_rows,
    }


def workspace_for(run_root: Path) -> Path | None:
    preferred = run_root / "workspace" / "piper"
    if (preferred / "study.json").exists():
        return preferred
    candidates = sorted(
        path.parent for path in run_root.rglob("study.json") if path.is_file()
    )
    return candidates[0] if candidates else None


def copy_source(
    source_root: Path | None,
    run_root: Path,
    include_public_assets: bool,
) -> list[dict[str, Any]]:
    source_dir = RECORDS / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    workspace = source_root
    # These files belonged to earlier diagnostic variants or are optional
    # inputs.  Do not leave a fabricated "missing" row for a new run that did
    # not create them; the raw prompt/messages remain the source of truth.
    optional_source_names = {
        "study-reuse.json",
        "original-recording-runner.py",
        "public-inputs.json",
        "authoring-brief.json",
        "skeleton-context.json",
        "review-rejection.json",
        "run-ast-repair.py",
        "repair-feedback.txt",
        "repair.log",
        "run.log",
        "parent-interface-review.json",
        "driver-before-ast-repair.py",
        "canary-result-before-repair.json",
        "task-catalog.json",
        "scene-construction-smoke.json",
        "focused-checks.json",
    }
    skeleton_paths = (
        (
            workspace / "capability_inputs/skeleton_context.json"
            if workspace
            else None
        ),
        workspace / "skeleton_context.json" if workspace else None,
        run_root / "skeleton_context.json",
    )
    skeleton_source = next(
        (path for path in skeleton_paths if path is not None and path.is_file()),
        skeleton_paths[0],
    )
    candidates: list[tuple[str, Path | None, str]] = [
        ("task-catalog.json", REPO / "AA1/auto_adapter/task_libraries/piper/1.0.0/catalog.json", "AA1/auto_adapter/task_libraries/piper/1.0.0/catalog.json"),
        ("scene-construction-smoke.json", run_root / "scene_construction_smoke.json", "scene_construction_smoke.json"),
        ("focused-checks.json", run_root / "focused_checks.json", "focused_checks.json"),
        (
            "study.json",
            workspace / "study.json" if workspace else None,
            "workspace/piper/study.json",
        ),
        ("study-reuse.json", run_root / "study_reuse.json", "study_reuse.json"),
        (
            "original-recording-runner.py",
            run_root / "original_recording_runner.py",
            "original_recording_runner.py",
        ),
        (
            "public-inputs.json",
            workspace / "capability_inputs/public_inputs.json" if workspace else None,
            "workspace/piper/capability_inputs/public_inputs.json",
        ),
        (
            "authoring-brief.json",
            workspace / "capability_inputs/authoring_brief.json" if workspace else None,
            "workspace/piper/capability_inputs/authoring_brief.json",
        ),
        (
            "skeleton-context.json",
            skeleton_source,
            "workspace/piper/capability_inputs/skeleton_context.json",
        ),
        (
            "design-raw.json",
            workspace / "capability_inputs/draft/capability_design.json"
            if workspace
            else None,
            "workspace/piper/capability_inputs/draft/capability_design.json",
        ),
        (
            "design-main.json",
            workspace / "capability_inputs/capability_design.json" if workspace else None,
            "workspace/piper/capability_inputs/capability_design.json",
        ),
        (
            "criteria.json",
            workspace / "capability_inputs/criteria.json" if workspace else None,
            "workspace/piper/capability_inputs/criteria.json",
        ),
        (
            "capability-preparation.json",
            workspace / "capability_inputs/capability_preparation.json"
            if workspace
            else None,
            "workspace/piper/capability_inputs/capability_preparation.json",
        ),
        (
            "driver.py",
            workspace / "driver.py" if workspace else None,
            "workspace/piper/driver.py",
        ),
        (
            "driver-before-ast-repair.py",
            workspace / "driver.before_ast_repair.py" if workspace else None,
            "workspace/piper/driver.before_ast_repair.py",
        ),
        ("canary-result.json", run_root / "canary_result.json", "canary_result.json"),
        (
            "canary-result-before-repair.json",
            run_root / "canary_result.before_repair.json",
            "canary_result.before_repair.json",
        ),
        (
            "parent-interface-review.json",
            run_root / "parent_interface_review.json",
            "parent_interface_review.json",
        ),
        ("review-rejection.json", run_root / "review_rejection.json", "review_rejection.json"),
        ("run-canary.py", run_root / "run_canary.py", "run_canary.py"),
        ("run-ast-repair.py", run_root / "run_ast_repair.py", "run_ast_repair.py"),
        ("repair-feedback.txt", run_root / "repair_feedback.txt", "repair_feedback.txt"),
        ("repair.log", run_root / "repair.log", "repair.log"),
        ("run.log", run_root / "run.log", "run.log"),
    ]
    if include_public_assets:
        asset_dir = REPO / "AA1" / "assets" / "mjcf" / "piper"
        candidates.extend(
            [
                ("scene.xml", asset_dir / "scene.xml", "AA1/assets/mjcf/piper/scene.xml"),
                ("piper.xml", asset_dir / "piper.xml", "AA1/assets/mjcf/piper/piper.xml"),
            ]
        )

    copied: list[dict[str, Any]] = []
    for name, source, diagnostic_name in candidates:
        if name in optional_source_names and (
            source is None or not source.is_file()
        ):
            continue
        destination = source_dir / name
        actual_diagnostic_name = diagnostic_name
        if source is not None and source.is_file():
            try:
                actual_diagnostic_name = source.relative_to(run_root).as_posix()
            except ValueError:
                # Public repository assets intentionally remain linked to their
                # repository path rather than being presented as run files.
                actual_diagnostic_name = diagnostic_name
        item: dict[str, Any] = {
            "name": name,
            "source": str(source) if source else None,
            "diagnostic_path": actual_diagnostic_name,
            "copied": False,
            "destination": str(destination.relative_to(HERE)),
        }
        if source is not None and source.is_file():
            shutil.copyfile(source, destination)
            item["copied"] = True
            item["bytes"] = destination.stat().st_size
        copied.append(item)
    json_dump(source_dir / "source-manifest.json", copied)
    return copied


def md_link(path: Path) -> str:
    return path.relative_to(HERE).as_posix()


def stage_label(stage_id: str) -> str:
    return {
        "study": "STUDY：真实 MJCF 研究",
        "tgcd": "TGCD：能力设计与 criteria",
        "generate": "GENERATE：driver.py 生成",
        "repair-1": "REPAIR-1：AST/interface 修复",
    }.get(stage_id, stage_id)


def commit_ids(run_root: Path) -> tuple[str, str]:
    """Read source identifiers from a recorded run report when available."""

    reports = [run_root / "canary_result.json", run_root / "canary_result.before_repair.json"]
    for path in reports:
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict):
            continue
        code = report.get("code_commit") or report.get("code_head") or CODE_COMMIT
        pipeline = report.get("pipeline_commit") or report.get("pipeline_wrapper") or PIPELINE_COMMIT
        return str(pipeline), str(code)
    return PIPELINE_COMMIT, CODE_COMMIT


def write_index(
    run_root: Path,
    workspace: Path | None,
    stages: list[dict[str, Any]],
    source_items: list[dict[str, Any]],
    include_public_assets: bool,
    pipeline_commit: str,
    code_commit: str,
) -> None:
    interface_review_present = any(
        item.get("name") == "parent-interface-review.json" and item.get("copied")
        for item in source_items
    )
    tgcd_stage = next((stage for stage in stages if stage.get("id") == "tgcd"), {})
    tgcd_tools = tgcd_stage.get("tool_names", [])
    if "write_file" in tgcd_tools:
        tgcd_entrypoint = "write_file（当前 write path）"
    elif "submit_design" in tgcd_tools:
        tgcd_entrypoint = "submit_design（本次记录的历史实现快照）"
    else:
        tgcd_entrypoint = "消息中未记录 write_file/submit_design"
    copied_names = {
        str(item.get("name"))
        for item in source_items
        if item.get("copied")
    }
    has_public_inputs = "public-inputs.json" in copied_names
    has_authoring_brief = "authoring-brief.json" in copied_names
    has_skeleton_context = "skeleton-context.json" in copied_names
    saved_input_names = "、".join(
        name
        for name, present in (
            ("authoring brief", has_authoring_brief),
            ("public inputs", has_public_inputs),
        )
        if present
    )
    if has_authoring_brief or has_public_inputs:
        authoring_step = (
            f"3. **TGCD 输入文件**：本次运行实际保存的 {saved_input_names} 见 "
            "`records/source/`；TGCD 首轮 user prompt 给出受限 `read_file` "
            "路径，模型按记录读取这些文件。原始首轮消息见 "
            "[`tgcd/initial-user-prompt.txt`](records/tgcd/initial-user-prompt.txt) "
            "与 [`tgcd/turn-000/request.json`](records/tgcd/turn-000/request.json)。"
        )
    else:
        authoring_step = (
            "3. **TGCD 直接文件输入**：本次未生成 `authoring_brief.json` 或 "
            "`public_inputs.json` 中间副本；首轮 user prompt 给出实际 study.json、"
            "framework robot catalog 和可选 skeleton context 的路径/指令，模型通过受限 "
            "`read_file` 读取。原始首轮消息见 "
            "[`tgcd/initial-user-prompt.txt`](records/tgcd/initial-user-prompt.txt) "
            "与 [`tgcd/turn-000/request.json`](records/tgcd/turn-000/request.json)。"
        )
    if has_public_inputs:
        public_step_one = (
            "公开任务库和 sources 在 `generate_capability_design` 输入构造中读取，"
            "实际公开输入见 [`public-inputs.json`](records/source/public-inputs.json)。"
        )
        public_step_two = (
            "其实际投影保存为 [`public-inputs.json`](records/source/public-inputs.json)。"
        )
    else:
        public_step_one = (
            "本次未生成独立 public inputs 副本；实际 catalog 路径和读取动作保留在 "
            "TGCD 首轮 request 与后续 `read_file` tool result 中。"
        )
        public_step_two = (
            "本次未生成 `public_inputs.json` 快照；该投影若被执行，证据以消息中的实际 "
            "文件路径和工具响应为准。"
        )
    if "write_file" in tgcd_tools:
        criteria_writer = "write_file 后由 Python validator 派生并写出 `criteria.json`"
    elif "submit_design" in tgcd_tools:
        criteria_writer = "`submit_handler` 从 capability criterion 写出 `criteria.json`"
    else:
        criteria_writer = "由消息和实际 `criteria.json` 记录确定的 Python criteria 写出路径"
    skeleton_note = (
        "；可选 `skeleton-context.json` 已随 source manifest 复制"
        if has_skeleton_context
        else "；本次未发现独立 skeleton context 文件"
    )
    lines: list[str] = [
        "# AA1 Piper 原始流程记录索引",
        "",
        "此索引由 `export_records.py` 从实际诊断记录生成。每个 turn 目录中的 JSON 保留模型边界记录；缺少的事件保持缺少，不补写响应或工具结果。",
        "",
        f"- 运行根目录：`{run_root}`",
        f"- 工作区：`{workspace}`" if workspace else "- 工作区：未找到 `workspace/**/study.json`",
        f"- 公共 XML：{'已复制到 `records/source/`' if include_public_assets else '未复制；可用 `--include-public-assets` 显式加入'}",
        f"- 提交标识：initial pipeline integration commit `{pipeline_commit}`；run code HEAD `{code_commit}`（同一 parent repository 中的提交；优先取 run report，缺失时使用 fallback）",
        "- 记录边界：诊断 wrapper 在 `ReactLoop._invoke_with_retry` 处保存 system/tools/max_tokens/messages/gateway_messages；不会保存 transport headers、credentials 或环境变量。",
        "",
        "## 每轮文件",
        "",
        "每个真实请求对应 `request.json`；存在的模型返回对应 `response.json`，调用错误也原样放入该文件；没有后续事件则不创建响应内容。`tool-results.json` 优先从下一轮 request 的新增消息提取完整 tool result，同时保留 trace observations，并明确其可能已被框架截断。`trace.json` 是同一轮的原始 ReAct trace。",
        "",
        "| 阶段 | 消息记录 | trace | 轮数 | 逐轮目录 |",
        "|---|---|---|---:|---|",
    ]
    if not stages:
        lines.append("| — | 未找到 `*.messages.jsonl`，未导出 | — | 0 | — |")
    for stage in stages:
        stage_dir = RECORDS / stage["id"]
        msg_link = f"`{stage['messages_relative']}`"
        trace_link = f"`{Path(stage['trace_path'])}`" if stage["trace_exists"] else "未找到"
        first = stage_dir / "turn-000"
        initial = "、".join(
            f"[{name}]({md_link(stage_dir / name)})"
            for name in (
                "initial-system-prompt.txt",
                "initial-user-prompt.txt",
                "initial-framework-messages.txt",
                "initial-gateway-messages.txt",
            )
            if (stage_dir / name).exists()
        )
        turn_link = (
            f"[turn-000]({md_link(first)}/request.json)"
            if stage["turn_count"]
            else "—"
        )
        lines.append(
            f"| {stage_label(stage['id'])} | {msg_link} | {trace_link} | {stage['turn_count']}（trace {stage['trace_count']}） | {turn_link}；初始快照：{initial or '未生成'} |"
        )
    lines.extend(["", "## 每阶段实际工具清单", ""])
    lines.extend(["| 阶段 | 首轮 request 中记录的 tool 名称 |", "|---|---|"])
    if not stages:
        lines.append("| — | 未找到 request |")
    for stage in stages:
        names = stage.get("tool_names", [])
        lines.append(
            f"| {stage_label(stage['id'])} | "
            + ("、".join(f"`{name}`" for name in names) if names else "未记录")
            + " |"
        )
    lines.extend(["", "逐轮入口："])
    for stage in stages:
        for turn in stage["turns"]:
            turn_dir = RECORDS / stage["id"] / f"turn-{turn['turn']:03d}"
            links = [
                f"[request]({md_link(turn_dir / 'request.json')})",
                f"[tool-results]({md_link(turn_dir / 'tool-results.json')})",
            ]
            if (turn_dir / "response.json").exists():
                links.insert(1, f"[response]({md_link(turn_dir / 'response.json')})")
            if (turn_dir / "trace.json").exists():
                links.append(f"[trace]({md_link(turn_dir / 'trace.json')})")
            lines.append(
                f"- `{stage['id']} turn-{turn['turn']:03d}`：{', '.join(links)}；"
                f" framework messages={turn['request_messages']}，gateway messages={turn['request_gateway_messages']}，"
                f"响应={turn['response']}，tool-results 来源={turn['tool_result_source']}。"
            )
    lines.extend(
        [
            "",
            "## 实际输入、输出与检查来源",
            "",
            "以下条目只列出诊断运行中存在的原始文件；`source-manifest.json` 记录每个候选文件是否实际复制。",
            "",
            "| 文件 | 状态 | 记录副本 | 诊断源 |",
            "|---|---|---|---|",
        ]
    )
    for item in source_items:
        dest = (
            f"[{item['destination']}]({item['destination']})"
            if item["copied"]
            else "未找到，未导出"
        )
        status = f"已复制（{item.get('bytes', 0)} bytes）" if item["copied"] else "缺失"
        lines.append(
            f"| `{item['name']}` | {status} | {dest} | `{item['source'] or item['diagnostic_path']}` |"
        )
    lines.extend(
        [
            "",
            "## 真实代码入口与转换链",
            "",
            "下面是本次链路在仓库中的实际入口；对应的原始模型请求和产物链接到上面的逐轮文件与 `records/source/`。",
            "",
            f"1. **YAML robot binding → catalog**：`task_library_for_robot` 读取 `AA1/auto_adapter/task_libraries/index.yaml`，把 AA1 robot_id 绑定到源任务包和版本，见 [`capability_preparation.py`]({REPO}/AA1/auto_adapter/capability_preparation.py)；{public_step_one}",
            (f"2. **历史公开任务投影**：该次运行投影后的输入见 [public-inputs.json](records/source/public-inputs.json)。" if has_public_inputs else "2. **直接读取公开任务**：模型读取对应机器人的完整 catalog；prompt 要求根据任务描述和 scoring 设计能力，忽略旧 task invocation_schema / request_envelope。实际读取内容保留在 tool result 中，任务库副本见 [task-catalog.json](records/source/task-catalog.json)。"),
            authoring_step + skeleton_note,
            f"4. **TGCD 新 ReactLoop：能力与 criteria 联合设计**：设计阶段构建 `ReactLoop`，本次消息记录的模型写入入口为 `{tgcd_entrypoint}`；相关工具/响应见 [`tgcd/`](records/tgcd/)，Python 接线见 [`capability_preparation.py`]({REPO}/AA1/auto_adapter/capability_preparation.py)。",
            f"5. **设计写入后的 validator**：Python 端调用 `_validate_generated_design`，见 [`capability_preparation.py`]({REPO}/AA1/auto_adapter/capability_preparation.py)；候选原稿和主 design 分别见 [`design-raw.json`](records/source/design-raw.json) 与 [`design-main.json`](records/source/design-main.json)。",
            f"6. **canonical header / task IDs**：任务 ID 由 `_task_ids` 检查，canonical header 补全逻辑在 `_validate_generated_design`，见 [`capability_preparation.py`]({REPO}/AA1/auto_adapter/capability_preparation.py)。实际写出的主 design 见 [`design-main.json`](records/source/design-main.json)。",
            f"7. **derived criteria**：{criteria_writer}，见 [`capability_preparation.py`]({REPO}/AA1/auto_adapter/capability_preparation.py)；实际副本见 [`criteria.json`](records/source/criteria.json)。",
            f"8. **capability_generation_context 使用内存 design**：`design` 参数优先于 catalog fallback，见 [`robot_catalog.py`]({REPO}/AA1/auto_adapter/robot_catalog.py)；GENERATE 的实际 prompt 和消息见 [`generate/initial-user-prompt.txt`](records/generate/initial-user-prompt.txt) 与 [`generate/turn-000/request.json`](records/generate/turn-000/request.json)。",
            f"9. **original generation**：orchestrator 将内存 design 传给 `capability_generation_context` 并启动 `02_generate`，见 [`orchestrator.py`]({REPO}/AA1/auto_adapter/orchestrator.py)；真实生成消息和工具结果见 [`generate/`](records/generate/)。",
            "10. **AST method presence 与接口检查**：本诊断脚本用 Python `ast.parse` 检查生成 `Robot` 的方法声明，父检查另用真实 MuJoCo build 和 `inspect.signature` 核对 request 接口；原始脚本副本见 [`run-canary.py`](records/source/run-canary.py)，生成源码见 [`driver.py`](records/source/driver.py)，检查报告见 [`parent-interface-review.json`](records/source/parent-interface-review.json)（若存在）。这些检查只证明源码/接线可解析，不证明行为完成。",
            "",
            "## 记录边界与失败边界",
            "",
            f"- `tool-results.json` 的 `framework_tool_results` / `gateway_tool_results` 来自**下一轮真实 request 的新增消息**；这保留了框架重新提交给模型的完整 tool result。trace 中的 `observations` 同时保留，但 `trace_observations_may_be_truncated: true`，因为 ReAct trace 使用 [`react_loop.py`]({REPO}/AA1/auto_adapter/agent/react_loop.py) 的 `_truncate`，所以不能把 trace 文本当作完整工具输出。",
            "- 消息文件中出现的 `error` 或缺少的 response 会原样反映在对应 turn；没有消息文件的阶段写为“未找到，未导出”。因此当前导出不把阶段缺失推断为成功，也不把失败请求改写成成功。",
            "- 本目录是诊断记录和静态检查的可读副本。当前链路**没有独立物理 validation**；criteria 阈值、AST 方法存在、文件生成和框架阶段结果都不能替代独立的真实 MuJoCo 正向验证。",
            "- 本导出器不读取或输出 headers、credentials、环境变量，也不访问网络；重新导出只需运行：`python3 outputs/aa1-piper-flow/export_records.py <diagnostic-run-root>`。",
            "",
        ]
    )
    if interface_review_present:
        boundary_heading = "## 记录边界与失败边界"
        boundary_index = lines.index(boundary_heading)
        lines.insert(
            boundary_index + 4,
            "- 本次检查发现的具体限制按原文保存在 [parent-interface-review.json](records/source/parent-interface-review.json)；不将其他运行中的问题套用到本次生成结果。",
        )
    if (run_root / "study_reuse.json").exists():
        lines.insert(lines.index("## 每轮文件"), "本次复用了此前真实运行的 study 和 7 轮原始消息，未重新调用 STUDY 模型；见 [study-reuse.json](records/source/study-reuse.json)。\n")
    INDEX.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostic_dir", type=Path, help="实际诊断运行根目录")
    parser.add_argument(
        "--include-public-assets",
        action="store_true",
        help="复制仓库中已批准的公共 Piper scene.xml 与 included piper.xml",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="清理本导出器拥有的 records/ 后再导出",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = args.diagnostic_dir.expanduser().resolve()
    if not run_root.is_dir():
        raise SystemExit(f"诊断目录不存在：{run_root}")
    if args.clean and RECORDS.exists():
        shutil.rmtree(RECORDS)
    RECORDS.mkdir(parents=True, exist_ok=True)

    message_paths = sorted(
        path
        for path in run_root.rglob("*.messages.jsonl")
        if path.is_file()
    )
    by_stage: dict[str, Path] = {}
    duplicate_stages: list[str] = []
    for path in message_paths:
        stage_id = stage_id_for(path, run_root)
        if stage_id in by_stage:
            duplicate_stages.append(stage_id)
            base_stage_id = stage_id
            serial = 2
            while stage_id in by_stage:
                stage_id = f"{base_stage_id}-{serial}"
                serial += 1
        by_stage[stage_id] = path

    stages: list[dict[str, Any]] = []
    for stage_id in sorted(by_stage, key=stage_sort_key):
        stages.append(
            export_stage(stage_id, by_stage[stage_id], run_root, RECORDS / stage_id)
        )
    workspace = workspace_for(run_root)
    source_items = copy_source(workspace, run_root, args.include_public_assets)
    pipeline_commit, code_commit = commit_ids(run_root)
    write_index(
        run_root,
        workspace,
        stages,
        source_items,
        args.include_public_assets,
        pipeline_commit,
        code_commit,
    )

    summary = {
        "run_root": str(run_root),
        "records_dir": str(RECORDS),
        "index": str(INDEX),
        "pipeline_commit": pipeline_commit,
        "code_commit": code_commit,
        "stages": [
            {
                "id": stage["id"],
                "turns": stage["turn_count"],
                "trace_turns": stage["trace_count"],
                "messages": stage["messages_relative"],
            }
            for stage in stages
        ],
        "source_files_copied": sum(item["copied"] for item in source_items),
        "duplicate_stage_ids": duplicate_stages,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
