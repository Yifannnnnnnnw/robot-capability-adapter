"""Plot recorded model turns from the first automatic success in each group.

This reads existing experiment records only. It does not invoke a model or
execute a driver. One tile represents a compact-trace model response, not a
tool call or a physics step.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "aa2-mpl"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


HERE = Path(__file__).resolve().parent
MODELS = {
    "sonnet46": "Sonnet 4.6",
    "opus5": "Opus 5",
    "deepseek_v4_pro": "DeepSeek V4 Pro",
}
ROBOTS = {"so101": "SO-101", "go2": "Unitree Go2"}
STAGES = {
    "01_study": "Study",
    "design": "Design",
    "02_generate": "Generate",
    "03_repair_1": "Repair 1",
    "03_repair_2": "Repair 2",
}
READ_TOOLS = {"read_file", "list_skeletons", "inspect_skeleton"}
ACTION_TOOLS = {"write_file", "local_exec", "probe_case"}
COLOURS = {"read_plan": "#dadddf", "tool_action": "#3e9d8c", "tool_error": "#d84b61"}
PRICING = {
    "accessed": "2026-09-15",
    "unit": "USD per million tokens",
    "sources": ["https://platform.claude.com/docs/en/about-claude/pricing",
                "https://api-docs.deepseek.com/quick_start/pricing/"],
    "rates": {
        "sonnet46": {"in": 3.30, "cache_read": 0.33, "out": 16.50},
        "opus5": {"in": 5.50, "cache_read": 0.55, "out": 27.50},
        "deepseek_v4_pro": {"in": 0.66, "cache_read": 0.022, "out": 1.98},
    },
    "basis": "Claude EU regional rates include the 10% premium; DeepSeek uses off-peak V4 Pro rates.",
    "cache_accounting": "Claude gateway cache breakdown is unavailable: all reported prompt tokens use the input rate. DeepSeek input excludes separately recorded cache reads; its selected responses report zero cache creation tokens.",
    "scope": "Public-rate token-cost estimates, not provider invoices; no taxes, gateway adjustments or local compute costs.",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def turn_category(turn: dict) -> str:
    """Any tool/command error wins; otherwise action wins over read/plan."""
    for observation in turn.get("observations", []):
        if observation.get("is_error") is True:
            return "tool_error"
        content = observation.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                content = None
        if isinstance(content, dict) and content.get("exit_code") not in (None, 0):
            return "tool_error"
    names = {a["name"] for a in turn.get("actions", [])}
    unknown = names - READ_TOOLS - ACTION_TOOLS
    if unknown:
        raise ValueError(f"Unclassified tools: {sorted(unknown)}")
    return "tool_action" if names & ACTION_TOOLS else "read_plan"


def extract_runs(batch: Path) -> list[dict]:
    cells = load_json(batch / "plan.json")["cells"]
    selected = []
    for robot in ROBOTS:
        for model in MODELS:
            candidates = []
            for cell in cells:
                if cell["robot"]["id"] != robot or cell["model"]["id"] != model:
                    continue
                folder = batch / cell["id"]
                process = load_json(folder / "process.json")
                pipeline = load_json(folder / "result.json")["pipeline"]
                if (process["returncode"] == 0 and not process.get("timed_out")
                        and not process.get("interrupted") and pipeline.get("stage1_ok") is True):
                    candidates.append((cell["id"], pipeline))
            trial_id, pipeline = min(candidates, key=lambda item: item[0])
            process = load_json(batch / trial_id / "process.json")
            started = datetime.fromisoformat(process["started_at"])
            finished = datetime.fromisoformat(process["finished_at"])
            if model == "deepseek_v4_pro":
                # The selected trials must fit entirely within an off-peak window.
                assert 0 < (finished - started).total_seconds() < 3600
                assert all(t.weekday() >= 5 or not (1 <= t.hour < 4 or 6 <= t.hour < 10)
                           for t in (started, finished))
            usage = {key: sum(p.get("token_usage", {}).get(key, 0) for p in pipeline["phases"])
                     for key in ("in", "cache_read", "out")}
            cost = sum(usage[key] * rate for key, rate in PRICING["rates"][model].items()) / 1_000_000
            workspace = batch / trial_id / "generation" / Path(pipeline["workspace"]).name
            verdict = load_json(workspace / "validate_report.json")
            assert verdict["all_ok"] and verdict["n_passed"] == verdict["n_total"] > 0
            stages = []
            for phase in pipeline["phases"]:
                name = phase["name"]
                if name not in STAGES:
                    continue
                trace = (workspace / "design" / "trace.jsonl" if name == "design"
                         else workspace / "traces" / f"{name}.jsonl")
                records = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
                turns = [{"category": turn_category(t),
                          "output_limit": t.get("stop_reason") == "max_tokens",
                          "output_tokens": (t.get("token_usage") or {}).get("out", 0)}
                         for t in records]
                assert sum(t["output_tokens"] for t in turns) == phase["token_usage"]["out"]
                stages.append({"name": STAGES[name], "trace": str(trace.relative_to(batch)), "turns": turns})
            turns = [t for stage in stages for t in stage["turns"]]
            counts = Counter(t["category"] for t in turns)
            assert sum(t["output_tokens"] for t in turns) == usage["out"]
            selected.append({"trial_id": trial_id, "robot": robot, "model": model,
                             "generation_seconds": (finished - started).total_seconds(),
                             "recorded_process_seconds": process["duration_seconds"],
                             "estimated_cost_usd": cost, "token_usage": usage,
                             "replicate": int(trial_id[1:3]), "stages": stages,
                             "turn_count": len(turns),
                             "counts": {k: counts[k] for k in COLOURS},
                             "output_tokens": sum(t["output_tokens"] for t in turns),
                             "output_limit_turns": sum(t["output_limit"] for t in turns),
                             "cases_passed": verdict["n_passed"], "cases_total": verdict["n_total"]})
    return selected


def style_axis(ax, row_positions, runs):
    ax.set_xlim(-0.6, 76)
    ax.set_ylim(-0.6, 6.7)
    ax.set_yticks(row_positions)
    ax.set_yticklabels([MODELS[r["model"]] for r in runs], fontsize=11.5)
    ax.tick_params(axis="y", length=0, pad=8)
    for spine in ax.spines.values():
        spine.set_visible(False)


def robot_groups(ax, positions):
    for start, robot, tint in ((0, "so101", "#f0f6f4"), (3, "go2", "#f5f5f1")):
        ys = positions[start:start + 3]
        low, high = min(ys) - 0.39, max(ys) + 0.63
        ax.axhspan(low, high, color=tint, zorder=0)
        ax.plot([-0.32, -0.32], [low, high], transform=ax.get_yaxis_transform(),
                color="#3e9d8c" if robot == "so101" else "#9d9f90", lw=2.5, clip_on=False)
        ax.text(-0.337, (low + high) / 2, ROBOTS[robot], rotation=90,
                va="center", ha="right", transform=ax.get_yaxis_transform(),
                fontsize=10.5, color="#41645c" if robot == "so101" else "#747866")


def draw(runs: list[dict], output: Path):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig = plt.figure(figsize=(9.0, 4.55), facecolor="white")
    top = fig.add_axes([0.225, 0.18, 0.575, 0.70])
    positions = [5.85, 4.88, 3.91, 2.28, 1.31, 0.34]
    fig.text(0.035, 0.96, "Recorded model turns", fontsize=13, weight="bold", color="#253b45")
    fig.text(0.035, 0.915, "Earliest automatic suite pass per model and robot", fontsize=10, color="#727a7e")
    style_axis(top, positions, runs)
    robot_groups(top, positions)
    top.set_xticks([])
    for run, y in zip(runs, positions):
        x = 0
        for stage_index, stage in enumerate(run["stages"]):
            length = len(stage["turns"])
            top.text(x + length / 2 - 0.04, y + 0.32, stage["name"], ha="center", va="bottom",
                     fontsize=9.1, color="#53666e")
            if stage_index:
                top.plot([x - 0.06, x - 0.06], [y - 0.25, y + 0.27], color="#303b40", lw=1.0, zorder=4)
            for turn in stage["turns"]:
                tile = Rectangle((x, y - 0.23), 0.92, 0.46,
                                 facecolor=COLOURS[turn["category"]], edgecolor="white", linewidth=0.3)
                top.add_patch(tile)
                if turn["output_limit"]:
                    top.add_patch(Rectangle((x, y - 0.23), 0.92, 0.46, facecolor="none",
                                            edgecolor="#5e6670", linewidth=0.3, hatch="///"))
                x += 1
        top.text(1.015, y, f'{run["turn_count"]} turns', transform=top.get_yaxis_transform(),
                 va="center", fontsize=10.5, color="#4c555a")

    legend = [Patch(facecolor=COLOURS["read_plan"], label="Read / plan"),
              Patch(facecolor=COLOURS["tool_action"], label="Tool action"),
              Patch(facecolor=COLOURS["tool_error"], label="Tool error"),
              Line2D([0], [0], color="#303b40", lw=1.2, label="Stage boundary"),
              Patch(facecolor="white", edgecolor="#5e6670", hatch="///", label="Output limit")]
    fig.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.045), ncol=5,
               frameon=False, fontsize=9.5, handlelength=1.5, columnspacing=1.2)
    for extension in ("png", "pdf", "svg"):
        path = output.with_suffix(f".{extension}")
        fig.savefig(path, dpi=220, facecolor="white", bbox_inches="tight", pad_inches=0.08)
        if extension == "svg":
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=HERE / "data/runs/exp2a_8000_01")
    parser.add_argument("--output-dir", type=Path, default=HERE / "figures")
    args = parser.parse_args()
    runs = extract_runs(args.batch.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "exp2a_successful_run_turns"
    metadata = {"batch": args.batch.name,
                "pricing": PRICING,
                "time_basis": "Elapsed UTC time from process.json started_at to finished_at, covering the complete generation and validation process. The separate recorded_process_seconds field retains the original monotonic timer.",
                "selection": "Earliest automatically passing final driver per model and robot; no reviewed-only successes.",
                "unit": "One compact-trace model response; validation and messages traces excluded.",
                "classification": "Error if any observation is_error or decoded command exit_code is nonzero; otherwise write/exec/probe are tool actions, with read/inspect/list/no-tool responses read/plan.",
                "output_limit": "stop_reason=max_tokens, marked separately and not automatically an error.",
                "runs": runs}
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    draw(runs, output)
    for run in runs:
        print(run["trial_id"], run["turn_count"], run["counts"], run["output_tokens"], "output tokens")
    print(output)


if __name__ == "__main__":
    main()
