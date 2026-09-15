#!/usr/bin/env python3
"""Render the supplementary Chapter 3 process cases from their own logs.

Only ``expriment/chapter3_process_cases/data/runs`` is read.  A cell may still
be running: unfinished rows are labelled pending without a partial timeline.
Completed cells use the phase metadata in ``summary.json``
or ``run_result.json``.  The script never starts a model, DEMO, or network
request.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "data" / "runs"
OUT = HERE / "data" / "figures"
CONFIG = HERE / "config.json"
HELPERS = ROOT / "expriment" / "chapter3_piper"
sys.path.insert(0, str(HELPERS))
from extract_stage_traces import read_json, stage_name  # noqa: E402


# The maintained AA1 environment owns the working numpy used by MuJoCo.  The
# global install supplies matplotlib; import numpy first so matplotlib reuses
# that already-loaded, compatible numpy module.
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(OUT / ".mplconfig"))
(OUT / ".mplconfig").mkdir(parents=True, exist_ok=True)
import numpy  # noqa: E402,F401

GLOBAL_SITE = "/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages"
if GLOBAL_SITE not in sys.path:
    sys.path.append(GLOBAL_SITE)
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402


EXPECTED_STAGES = ("Study", "Design", "Generate", "Validation", "Export", "ReCAP")
ROBOT_LABELS = {"piper": "PiPER", "go2": "Go2", "leap_hand": "LEAP Hand"}
MODEL_LABELS = {
    "eu.anthropic.claude-opus-4-8": "Opus 4.8",
    "eu.anthropic.claude-sonnet-4-6": "Sonnet 4.6",
    "global.openai.gpt-6-astra": "GPT-6 Astra",
}
TURN_COLOURS = {
    "read_plan": "#D7E0E5",
    "write": "#6794BB",
    "execute": "#3B9B8C",
    "error": "#D15668",
    "recap_plan": "#8E78A8",
}
STAGE_COLOURS = {
    "Study": "#9ABCCD",
    "Design": "#9C88B7",
    "Generate": "#648FAC",
    "Validation": "#78838B",
    "Repair": "#CFA36C",
    "Export": "#70A598",
    "ReCAP": "#B28EA6",
}
INK = "#243746"
MUTED = "#65717A"
PASS = "#377EA3"
FAIL = "#D17A50"
PENDING = "#9BA8AE"


def _json(path: Path) -> dict | None:
    try:
        value = read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _jsonl(path: Path) -> tuple[list[dict], int]:
    """Read complete JSONL objects and ignore a concurrently written tail."""
    rows: list[dict] = []
    incomplete = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return rows, incomplete
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            incomplete += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows, incomplete


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _token_pair(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"in": 0, "out": 0}
    input_value = value.get("in", value.get("input_tokens", 0))
    output_value = value.get("out", value.get("output_tokens", 0))
    return {
        "in": int(input_value) if isinstance(input_value, (int, float)) and not isinstance(input_value, bool) else 0,
        "out": int(output_value) if isinstance(output_value, (int, float)) and not isinstance(output_value, bool) else 0,
    }


def _sum_tokens(rows: list[dict]) -> dict[str, int]:
    totals = {"in": 0, "out": 0}
    for row in rows:
        pair = _token_pair(row.get("tokens"))
        totals["in"] += pair["in"]
        totals["out"] += pair["out"]
    return totals


def _message_usage(row: dict) -> dict:
    usage = row.get("usage")
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except (TypeError, ValueError):
            usage = None
    if isinstance(usage, dict):
        return usage
    response = row.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        return response["usage"]
    return {}


def _trace_model(trace_path: Path | None) -> str | None:
    if trace_path is None:
        return None
    message_path = trace_path.with_suffix(".messages.jsonl")
    rows, _ = _jsonl(message_path)
    for row in rows:
        model = row.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _turns(trace_path: Path, *, recap: bool = False) -> tuple[list[dict], dict]:
    """Classify one JSONL row as one model turn; tool calls stay subdivisions."""
    rows, incomplete = _jsonl(trace_path)
    if recap:
        # ReCAP model usage is recorded in model_messages.jsonl.  The
        # model_turns file has no reliable usage field in the maintained path.
        turns = []
        for index, row in enumerate(rows):
            turns.append({
                "iter": index,
                "category": "recap_plan",
                "error": False,
                "tokens": _token_pair(_message_usage(row)),
            })
        return turns, {
            "incomplete_lines": incomplete,
            "tokens": _sum_tokens(turns),
            "model": next((row.get("model") for row in rows if isinstance(row.get("model"), str)), None),
        }

    message_path = trace_path.with_suffix(".messages.jsonl")
    message_rows, message_incomplete = _jsonl(message_path)
    tool_results = {
        row.get("iter"): row.get("results", [])
        for row in message_rows
        if row.get("event") == "tool_results"
    }
    turns: list[dict] = []
    for row in rows:
        actions = row.get("actions") or []
        observations = tool_results.get(row.get("iter"), row.get("observations") or [])
        if not isinstance(actions, list):
            actions = []
        if not isinstance(observations, list):
            observations = []
        action_records = []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            observation = observations[index] if index < len(observations) and isinstance(observations[index], dict) else {}
            content = observation.get("content")
            payload = None
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except (TypeError, ValueError):
                    pass
            exit_code = payload.get("exit_code") if isinstance(payload, dict) else None
            explicit_error = observation.get("is_error") is True
            command_error = action.get("name") == "local_exec" and isinstance(exit_code, int) and exit_code != 0
            action_name = action.get("name", "unknown")
            category = "execute" if action_name in {"local_exec", "probe_case"} else "write" if action_name == "write_file" else "read_plan"
            action_records.append({
                "name": action_name,
                "category": category,
                "error": bool(explicit_error or command_error),
                "exit_code": exit_code,
            })
        is_error = bool(row.get("stop_reason") == "invoke_error" or any(item["error"] for item in action_records))
        categories = {item["category"] for item in action_records}
        category = "error" if is_error else "execute" if "execute" in categories else "write" if "write" in categories else "read_plan"
        turns.append({
            "iter": row.get("iter", len(turns)),
            "category": category,
            "error": is_error,
            "actions": action_records,
            "tokens": row.get("token_usage") or {},
            "stop_reason": row.get("stop_reason"),
        })
    return turns, {
        "incomplete_lines": incomplete + message_incomplete,
        "tokens": _sum_tokens(turns),
        "tool_error_count": sum(item["error"] for turn in turns for item in turn.get("actions", [])),
        "model": _trace_model(trace_path),
    }


def _validation_payload(report: dict | None) -> dict | None:
    if not isinstance(report, dict):
        return None
    tests = report.get("tests")
    return {
        "all_ok": report.get("all_ok") if isinstance(report.get("all_ok"), bool) else None,
        "n_passed": report.get("n_passed"),
        "n_total": report.get("n_total"),
        "n_cases": len(tests) if isinstance(tests, list) else None,
    }


def _phase_status(phase: dict) -> tuple[str, str | None]:
    error = str(phase.get("error") or "")
    if error.startswith("not run") or error.startswith("skipped"):
        return "not_executed", error
    return "executed", None


def _phase_from_summary(phase: dict, workspace: Path) -> dict | None:
    raw_name = phase.get("name")
    if raw_name == "demo":
        return None
    status, reason = _phase_status(phase)
    stage = stage_name(raw_name) if isinstance(raw_name, str) else "unknown"
    trace = phase.get("trace_path")
    trace_path = Path(trace) if isinstance(trace, str) and trace else None
    if trace_path is not None and not trace_path.is_absolute():
        trace_path = workspace / trace_path
    turns, trace_info = _turns(trace_path) if trace_path and trace_path.is_file() else ([], {})
    tokens = _token_pair(phase.get("token_usage"))
    if tokens == {"in": 0, "out": 0}:
        tokens = trace_info.get("tokens", tokens)
    metadata = phase.get("metadata") if isinstance(phase.get("metadata"), dict) else {}
    return {
        "name": raw_name,
        "stage": stage,
        "status": status,
        "reason": reason,
        "ok": phase.get("ok") if status == "executed" and isinstance(phase.get("ok"), bool) else None,
        "turns": turns,
        "n_turns": len(turns),
        "n_error_turns": sum(turn.get("error", False) for turn in turns),
        "tool_error_count": trace_info.get("tool_error_count", 0),
        "duration_sec": _number(phase.get("duration_sec")) if status == "executed" else None,
        "tokens": tokens,
        "validation": _validation_payload(metadata.get("validation_report")) if stage == "Validation" else None,
        "observed_model": trace_info.get("model"),
        "source_paths": [_relative(trace_path)] if trace_path and trace_path.is_file() else [],
    }


def _placeholder(stage: str, status: str, reason: str | None = None) -> dict:
    return {
        "name": stage,
        "stage": stage,
        "status": status,
        "reason": reason,
        "ok": None,
        "turns": [],
        "n_turns": 0,
        "n_error_turns": 0,
        "tool_error_count": 0,
        "duration_sec": None,
        "tokens": {},
        "validation": None,
        "observed_model": None,
        "source_paths": [],
    }


def _recap_phase(cell: Path, *, terminal: bool, export_ok: bool) -> dict:
    task_dir = cell / "demo"
    report_path = task_dir / "task_report.json"
    if report_path.is_file():
        report = _json(report_path) or {}
        message_path = task_dir / "model_messages.jsonl"
        turns, info = _turns(message_path, recap=True) if message_path.is_file() else ([], {})
        event_path = task_dir / "trace.jsonl"
        events, event_incomplete = _jsonl(event_path) if event_path.is_file() else ([], 0)
        capability_events = [event for event in events if event.get("event") == "capability_result"]
        tool_errors = sum(
            1
            for event in capability_events
            if isinstance(event.get("feedback"), dict)
            and event.get("feedback", {}).get("operation", {}).get("status") not in {None, "EXECUTED"}
        )
        physical = report.get("physical_task_success")
        task_verdict = "pass" if physical is True else "fail" if physical is False else "unscored"
        source_paths = [_relative(report_path)]
        for path in (message_path, task_dir / "model_turns.jsonl", event_path):
            if path.is_file():
                source_paths.append(_relative(path))
        return {
            "name": "demo",
            "stage": "ReCAP",
            "status": "executed",
            "reason": None,
            "ok": report.get("ok") if isinstance(report.get("ok"), bool) else None,
            "turns": turns,
            "n_turns": len(turns),
            "n_error_turns": 0,
            "tool_error_count": tool_errors,
            "duration_sec": _number(report.get("duration_sec")),
            "tokens": info.get("tokens", {}),
            "validation": None,
            "physical_task_success": physical,
            "task_verdict": task_verdict,
            "evaluation_error": report.get("evaluation_error"),
            "controller_status": report.get("status"),
            "execution_ok": report.get("execution_ok"),
            "capability_calls": report.get("controller_result", {}).get("capability_calls") if isinstance(report.get("controller_result"), dict) else None,
            "trace_incomplete_lines": event_incomplete,
            "observed_model": info.get("model"),
            "source_paths": source_paths,
        }
    if terminal:
        phase = _placeholder("ReCAP", "not_executed", "DEMO not eligible" if not export_ok else "no DEMO report recorded")
    else:
        phase = _placeholder("ReCAP", "pending")
    # Keep the physical verdict explicitly null whenever no task report was
    # recorded; controller completion is never used as a substitute.
    phase.update({"physical_task_success": None, "task_verdict": phase["status"]})
    return phase


def _condition_metadata(config: dict) -> list[dict]:
    metadata = []
    for item in config.get("conditions", []):
        if not isinstance(item, dict):
            continue
        robot_id = item["robot_id"]
        model = item["bedrock_model"]
        metadata.append({
            "id": item["id"],
            "robot_id": robot_id,
            "model": model,
            "recap_model": config.get("recap", {}).get("model"),
            "label": f"{ROBOT_LABELS.get(robot_id, robot_id)} · {MODEL_LABELS.get(model, model)}",
            "group": "piper" if robot_id == "piper" else "other",
        })
    return metadata


def _record(condition: dict) -> dict:
    condition_id = condition["id"]
    robot_id = condition["robot_id"]
    cell = DATA / condition_id
    run_result_path = cell / "run_result.json"
    terminal = run_result_path.is_file()
    run_result = _json(run_result_path) if terminal else None
    workspace = cell / "generation" / robot_id
    summary = _json(workspace / "summary.json")
    if summary is None and isinstance(run_result, dict):
        summary = run_result.get("generation") if isinstance(run_result.get("generation"), dict) else None

    phases: list[dict] = []
    if isinstance(summary, dict) and isinstance(summary.get("phases"), list):
        for raw_phase in summary["phases"]:
            if isinstance(raw_phase, dict):
                parsed = _phase_from_summary(raw_phase, workspace)
                if parsed is not None:
                    phases.append(parsed)

    if terminal and not phases:
        reason = (run_result or {}).get("error", "cell ended before a generation result")
        phases = [_placeholder(stage, "not_executed", str(reason)) for stage in EXPECTED_STAGES[:-1]]

    export_phases = [phase for phase in phases if phase["stage"] == "Export"]
    export_ok = bool(export_phases and export_phases[-1].get("ok") is True)
    recap = _recap_phase(cell, terminal=terminal, export_ok=export_ok)
    phases.append(recap)
    suite_verdicts = [
        phase["validation"]
        for phase in phases
        if phase["stage"] == "Validation" and phase.get("validation") is not None
    ]
    source_paths = []
    observed_models = set()
    for phase in phases:
        source_paths.extend(phase.get("source_paths", []))
        if isinstance(phase.get("observed_model"), str):
            observed_models.add(phase["observed_model"])
    if (cell / "config.json").is_file():
        source_paths.append(_relative(cell / "config.json"))
    status = "pending" if not cell.exists() else "terminal" if terminal else "active"
    return {
        **condition,
        "status": status,
        "terminal": terminal,
        "error": (run_result or {}).get("error") if isinstance(run_result, dict) else None,
        "observed_models": sorted(observed_models),
        "phases": phases,
        "suite_verdicts": suite_verdicts,
        "recap": recap,
        "source_paths": sorted(set(source_paths)),
    }


def collect() -> dict:
    config = _json(CONFIG) or {}
    return {
        "scope": "supplementary Chapter 3 process cases; current cohort only",
        "source_root": _relative(DATA),
        "source_rule": "Only files below the new chapter3_process_cases/data/runs root are read; no historical PiPER runs or synthetic turns.",
        "tile_unit": "one recorded model turn; tool calls are retained as subdivisions and error counts",
        "verdict_rule": "Framework validation suite verdicts and physical DEMO task verdicts remain separate from tool errors; null physical_task_success is unscored.",
        "conditions": [_record(condition) for condition in _condition_metadata(config)],
    }


def _phase_colour(stage: str) -> str:
    return STAGE_COLOURS.get("Repair" if stage.startswith("Repair") else stage, MUTED)


def _verdict_label(recap: dict) -> tuple[str, str]:
    if recap.get("status") == "pending":
        return "Task pending", PENDING
    if recap.get("status") == "not_executed":
        return "ReCAP not executed", MUTED
    verdict = recap.get("task_verdict")
    if verdict == "pass":
        return "Task pass", PASS
    if verdict == "fail":
        return "Task fail", FAIL
    return "Task unscored", MUTED


def _phase_width(phase: dict) -> float:
    if phase.get("turns"):
        return len(phase["turns"]) + 0.8
    return 4.2 if phase.get("stage") == "Validation" else 4.5


def _row_width(record: dict) -> float:
    return sum(_phase_width(phase) for phase in record["phases"])


def _draw_phase(ax, phase: dict, y: float, x: float, validation_number: int) -> tuple[float, int]:
    stage = phase["stage"]
    turns = phase.get("turns", [])
    if turns:
        start = x
        for index, turn in enumerate(turns):
            ax.add_patch(Rectangle(
                (x + index, y - 0.15), 0.94, 0.30,
                facecolor=TURN_COLOURS.get(turn.get("category"), MUTED),
                edgecolor="white", linewidth=0.35,
            ))
        x += len(turns)
        ax.plot([start, x], [y + 0.23, y + 0.23], color=_phase_colour(stage), lw=0.8)
        ax.text(start + max(len(turns) / 2, 0.4), y + 0.29, stage,
                ha="center", va="bottom", fontsize=7.0, color=_phase_colour(stage))
        return x + 0.8, validation_number

    if stage == "Validation":
        validation_number += 1
        verdict = phase.get("validation") or {}
        all_ok = verdict.get("all_ok")
        colour = PASS if all_ok is True else FAIL if all_ok is False else PENDING
        center = x + 1.3
        ax.scatter([center], [y], marker="D", s=36, facecolor=colour if all_ok is not None else "white",
                   edgecolor=colour, linewidth=0.8, zorder=4)
        count = f'{verdict.get("n_passed", "?")}/{verdict.get("n_total", "?")}' if all_ok is not None else phase.get("status", "pending")
        ax.text(center, y + 0.30, f"V{validation_number}",
                ha="center", va="bottom", fontsize=7.0, color=colour)
        ax.text(center, y - 0.25, count, ha="center", va="top", fontsize=6.2, color=colour)
        return x + 4.2, validation_number

    status = phase.get("status", "pending")
    colour = PENDING if status == "pending" else MUTED
    width = 3.8
    ax.add_patch(Rectangle((x, y - 0.14), width, 0.28, facecolor="white", edgecolor=colour, linewidth=0.8))
    ax.text(x + width / 2, y + 0.24, stage, ha="center", va="bottom", fontsize=6.2, color=colour)
    marker = "P" if status == "pending" else "N"
    ax.text(x + width / 2, y, marker, ha="center", va="center", fontsize=6.1, color=colour)
    return x + 4.5, validation_number


def _token_summary(record: dict) -> str:
    totals = {"in": 0, "out": 0}
    for phase in record["phases"]:
        pair = _token_pair(phase.get("tokens"))
        totals["in"] += pair["in"]
        totals["out"] += pair["out"]

    def compact(value: int) -> str:
        return f"{value / 1e6:.1f}M" if value >= 1_000_000 else f"{value / 1_000:.1f}k" if value >= 1_000 else str(value)

    return f'{compact(totals["in"])} in / {compact(totals["out"])} out' if any(totals.values()) else "tokens pending"


def render(evidence: dict) -> tuple[Path, Path]:
    records = evidence["conditions"]
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "svg.fonttype": "none",
        "axes.linewidth": 0.6,
    })
    fig = plt.figure(figsize=(14.5, 8.5), facecolor="white")
    label_ax = fig.add_axes((0.035, 0.49, 0.155, 0.37))
    trace_ax = fig.add_axes((0.20, 0.49, 0.76, 0.37))
    effort_ax = fig.add_axes((0.20, 0.135, 0.55, 0.20))
    effort_text_ax = fig.add_axes((0.765, 0.135, 0.20, 0.20))

    max_width = max((_row_width(record) for record in records), default=20.0)
    verdict_x = max_width + 1.6
    x_right = verdict_x + 12.5
    row_positions = [4.5, 3.4, 2.3, 0.85, -0.25]
    trace_ax.set(xlim=(0, x_right), ylim=(-0.85, 5.4))
    trace_ax.axis("off")
    label_ax.set(xlim=(0, 1), ylim=(-0.85, 5.4))
    label_ax.axis("off")

    fig.text(0.035, 0.955, "Supplementary process cases", fontsize=17, weight="bold", color=INK)
    fig.text(0.035, 0.925, "Recorded model turns under PiPER model and robot changes", fontsize=10.5, color=MUTED)
    fig.text(0.035, 0.899, "One rectangle = one recorded model turn; diamonds = independent validation suites. N = stage not executed.", fontsize=8.5, color=MUTED)

    for index, record in enumerate(records):
        y = row_positions[index]
        if index in (0, 3):
            group = "PiPER model conditions" if index == 0 else "Other robots · Opus 4.8"
            trace_ax.text(0, y + 0.58, group, fontsize=9.5, weight="bold", color=INK)
        if index % 2 == 0:
            trace_ax.add_patch(Rectangle((0, y - 0.43), max_width + 0.6, 0.88, facecolor="#F4F7F8", zorder=0))
        label_ax.text(0.98, y, record["label"], ha="right", va="center", fontsize=9, color=INK)
        if not record["terminal"]:
            status = "Running — complete trace pending" if record["status"] == "active" else "Not started"
            trace_ax.text(0.5, y, status, va="center", fontsize=8.5, color=MUTED)
            continue
        x = 0.0
        validation_number = 0
        for phase in record["phases"]:
            x, validation_number = _draw_phase(trace_ax, phase, y, x, validation_number)
        verdict, colour = _verdict_label(record["recap"])
        trace_ax.text(verdict_x, y + 0.05, verdict, ha="left", va="center", fontsize=8.5, weight="bold", color=colour)
        if record["recap"].get("capability_calls") is not None:
            trace_ax.text(verdict_x, y - 0.24, f'{record["recap"]["capability_calls"]} capability calls', ha="left", va="center", fontsize=7.2, color=MUTED)

    turn_handles = [Patch(facecolor=TURN_COLOURS[key], label=label) for key, label in (
        ("read_plan", "Read / plan"), ("write", "Write file"), ("execute", "Execute / probe"),
        ("error", "Model / tool error"), ("recap_plan", "ReCAP planning"),
    )]
    turn_handles.extend([
        Line2D([], [], marker="D", ls="", color=PASS, label="Suite pass"),
        Line2D([], [], marker="D", ls="", color=FAIL, label="Suite fail"),
    ])
    fig.legend(handles=turn_handles, loc="upper center", bbox_to_anchor=(0.60, 0.475), ncol=8,
               frameon=False, fontsize=7.6, handlelength=1.2, columnspacing=1.2)

    fig.text(0.035, 0.375, "Recorded effort by stage", fontsize=11.5, weight="bold", color=INK)
    fig.text(0.035, 0.352, "Bars use recorded phase durations only; token totals are recorded model usage.", fontsize=8.4, color=MUTED)
    duration_totals = [
        sum(_number(phase.get("duration_sec")) for phase in record["phases"] if phase.get("duration_sec") is not None) / 60
        for record in records
    ]
    max_minutes = max(1.0, max(duration_totals, default=0.0) * 1.15)
    for index, record in enumerate(records):
        y = 4 - index
        if not record["terminal"]:
            effort_ax.text(0.02, y, "Pending", ha="left", va="center", fontsize=8, color=PENDING)
            continue
        left = 0.0
        for phase in record["phases"]:
            if phase.get("duration_sec") is None:
                continue
            minutes = _number(phase["duration_sec"]) / 60
            effort_ax.barh(y, minutes, left=left, height=0.56, color=_phase_colour(phase["stage"]), edgecolor="white", linewidth=0.5)
            left += minutes
        if left == 0:
            effort_ax.text(0.02, y, "duration pending", ha="left", va="center", fontsize=7.4, color=PENDING)
        generation = [phase for phase in record["phases"] if phase["stage"] != "ReCAP"]
        turns = sum(phase["n_turns"] for phase in generation)
        errors = sum(phase["n_error_turns"] for phase in generation)
        error_label = "error turn" if errors == 1 else "error turns"
        effort_text_ax.text(0.0, y,
                            f"{turns} generation turns · {errors} {error_label}\n{_token_summary(record)}",
                            ha="left", va="center", fontsize=7.3, color=MUTED)

    effort_ax.set(xlim=(0, max_minutes), ylim=(-0.65, 4.65), xlabel="Recorded phase duration (minutes)")
    effort_ax.set_yticks([4 - i for i in range(5)], [record["label"] for record in records], fontsize=8, color=INK)
    effort_ax.tick_params(axis="x", colors=MUTED, labelsize=7.5, length=3)
    effort_ax.tick_params(axis="y", length=0)
    effort_ax.spines[["top", "right", "left"]].set_visible(False)
    effort_ax.spines["bottom"].set_color("#CFD7DC")
    effort_text_ax.set(xlim=(0, 1), ylim=(-0.65, 4.65))
    effort_text_ax.axis("off")

    stage_handles = [Patch(facecolor=colour, label=stage) for stage, colour in STAGE_COLOURS.items()]
    fig.legend(handles=stage_handles, loc="lower center", bbox_to_anchor=(0.50, 0.055), ncol=7,
               frameon=False, fontsize=7.5, handlelength=1.2, columnspacing=1.5)
    fig.text(0.035, 0.013, "One run per condition. Stage gaps are not elapsed time. Each exported driver uses its robot's fixed DEMO with Astra ReCAP.", fontsize=7.7, color=MUTED)

    png_path = OUT / "process_stage_traces.png"
    svg_path = OUT / "process_stage_traces.svg"
    fig.savefig(png_path, dpi=220, bbox_inches="tight")
    fig.savefig(svg_path, bbox_inches="tight")
    plt.close(fig)
    return png_path, svg_path


def main() -> int:
    evidence = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    data_path = OUT / "process_stage_traces_data.json"
    data_path.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    png_path, svg_path = render(evidence)
    print(data_path)
    print(png_path)
    print(svg_path)
    for record in evidence["conditions"]:
        turns = sum(phase.get("n_turns", 0) for phase in record["phases"])
        task = record["recap"].get("status") if record["recap"].get("status") == "pending" else record["recap"].get("task_verdict")
        print(record["id"], record["status"], "turns", turns, "task", task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
