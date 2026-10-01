#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""AutoAdapter-Bench leaderboard aggregator.

Walks results/*.json (one per (robot, model) run from eval.py), produces:
  - A robot × model matrix of pass rates
  - Per-suite breakdown
  - Per-task breakdown
  - Cost analysis
  - Markdown table for paper

Usage:
    python autoadapter_bench/leaderboard.py
    python autoadapter_bench/leaderboard.py --results-dir custom/dir --output board.md
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_results(results_dir: Path) -> list[dict]:
    """Each result.json from eval.py is one (robot, model) run."""
    rows: list[dict] = []
    for f in sorted(results_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text())
            if "meta" not in d or "aggregate" not in d:
                continue
            d["_source_file"] = f.name
            rows.append(d)
        except Exception:
            continue
    return rows


def _short_model(m: str) -> str:
    """Friendly short name from a full Bedrock model ID."""
    for needle, short in [
        ("claude-haiku-4-5",  "Haiku 4.5"),
        ("claude-sonnet-4-6", "Sonnet 4.6"),
        ("claude-sonnet-4-5", "Sonnet 4.5"),
        ("claude-opus-4-8",   "Opus 4.8"),
        ("claude-opus-4-9",   "Opus 4.9"),
        ("ministral",         "Ministral 8B"),
        ("qwen3",             "Qwen3-32B"),
        ("deepseek.v3",       "DeepSeek V3"),
        ("nova-pro",          "Nova Pro"),
        ("gpt-5",             "GPT-5"),
        ("gpt-4",             "GPT-4"),
        ("gemini",            "Gemini"),
    ]:
        if needle in m.lower():
            return short
    return m.split(".")[-1][:20]


def make_robot_x_model_matrix(rows: list[dict]) -> tuple[list[str], list[str], dict]:
    """Robots are rows, models are columns. Cell = physics_pass_rate."""
    robots = sorted({r["meta"]["robot"] for r in rows})
    models = sorted({_short_model(r["meta"]["model"]) for r in rows})
    cell: dict[tuple[str, str], dict] = {}
    for r in rows:
        rk = r["meta"]["robot"]
        mk = _short_model(r["meta"]["model"])
        cell[(rk, mk)] = r["aggregate"]
    return robots, models, cell


def render_markdown(rows: list[dict], output_path: Path) -> None:
    out: list[str] = []
    out.append("# AutoAdapter-Bench Leaderboard")
    out.append("")
    out.append("_Auto-generated from `autoadapter_bench/results/*.json`. "
               "Each cell shows **physics_pass_rate** (LLM-self-report in parens) "
               "across all trials._")
    out.append("")

    robots, models, cell = make_robot_x_model_matrix(rows)

    # ── Robot × Model: overall physics_pass_rate ─────────────────────────
    out.append("## Robot × Model — overall pass rate")
    out.append("")
    header = "| Robot | " + " | ".join(models) + " |"
    sep = "|" + "---|" * (len(models) + 1)
    out.append(header)
    out.append(sep)
    for r in robots:
        row = [r]
        for m in models:
            agg = cell.get((r, m))
            if agg is None:
                row.append("—")
            else:
                phys = agg["physics_pass_rate"]
                llm = agg["llm_pass_rate"]
                row.append(f"**{phys*100:.0f}%** ({llm*100:.0f}%)")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # ── Cost matrix ──────────────────────────────────────────────────────
    out.append("## Cost matrix (USD per full run)")
    out.append("")
    out.append("| Robot | " + " | ".join(models) + " |")
    out.append(sep)
    for r in robots:
        row = [r]
        for m in models:
            agg = cell.get((r, m))
            row.append(f"${agg['total_cost_usd']:.2f}" if agg else "—")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # ── Wall-clock matrix ────────────────────────────────────────────────
    out.append("## Wall-clock matrix (seconds per full run)")
    out.append("")
    out.append("| Robot | " + " | ".join(models) + " |")
    out.append(sep)
    for r in robots:
        row = [r]
        for m in models:
            agg = cell.get((r, m))
            row.append(f"{agg['wall_clock_sec']:.0f}s" if agg else "—")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # ── Per-suite physics pass rates (model = best per robot) ───────────
    out.append("## Per-suite pass rate (best model per robot)")
    out.append("")
    suite_names = set()
    for r in rows:
        suite_names.update(r["suites"].keys())
    suite_names = sorted(suite_names)
    header = "| Robot | Model | " + " | ".join(suite_names) + " |"
    out.append(header)
    out.append("|" + "---|" * (len(suite_names) + 2))
    by_robot: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_robot[r["meta"]["robot"]].append(r)
    for robot in robots:
        # Pick best result by physics_pass_rate
        best = max(by_robot[robot], key=lambda r: r["aggregate"]["physics_pass_rate"])
        row = [robot, _short_model(best["meta"]["model"])]
        for sname in suite_names:
            s = best["suites"].get(sname)
            if s is None:
                row.append("—"); continue
            tasks = s["tasks"]
            n_phys = sum(t["physics_pass_rate"] for t in tasks) / max(len(tasks), 1)
            row.append(f"{n_phys*100:.0f}%")
        out.append("| " + " | ".join(row) + " |")
    out.append("")

    # ── Per-task detail (one model only — first by name) ────────────────
    if rows:
        out.append("## Per-task detail")
        out.append("")
        for r in rows:
            out.append(f"### {r['meta']['robot']} — {_short_model(r['meta']['model'])} "
                       f"(git {r['meta']['git_sha']})")
            for sname, s in r["suites"].items():
                out.append(f"\n**Suite: {sname}**")
                out.append("")
                out.append("| Task | Trials | LLM pass | Physics pass | Agreement | Mean calls | Cost |")
                out.append("|---|---|---|---|---|---|---|")
                for t in s["tasks"]:
                    out.append(
                        f"| {t['id']} | {t['n_trials']} | "
                        f"{t['llm_pass_rate']*100:.0f}% | "
                        f"{t['physics_pass_rate']*100:.0f}% | "
                        f"{t['agreement_rate']*100:.0f}% | "
                        f"{t['mean_tool_calls']:.1f} | "
                        f"${t['cost_usd']:.2f} |"
                    )
            out.append("")

    # ── Aggregate stats ──────────────────────────────────────────────────
    out.append("## Aggregate stats")
    out.append("")
    n_rows = len(rows)
    if n_rows:
        all_aggs = [r["aggregate"] for r in rows]
        mean_phys = sum(a["physics_pass_rate"] for a in all_aggs) / n_rows
        mean_llm = sum(a["llm_pass_rate"] for a in all_aggs) / n_rows
        mean_agree = sum(a["agreement_rate"] for a in all_aggs) / n_rows
        tot_cost = sum(a["total_cost_usd"] for a in all_aggs)
        tot_trials = sum(a["n_trials_total"] for a in all_aggs)
        tot_wall = sum(a["wall_clock_sec"] for a in all_aggs)
        out.append(f"- runs: **{n_rows}**")
        out.append(f"- trials: **{tot_trials}** total")
        out.append(f"- mean physics_pass_rate: **{mean_phys*100:.0f}%**")
        out.append(f"- mean llm_pass_rate: **{mean_llm*100:.0f}%**")
        out.append(f"- mean agreement: **{mean_agree*100:.0f}%**  "
                   "(higher = LLM honest about success/failure)")
        out.append(f"- total cost: **${tot_cost:.2f}**")
        out.append(f"- total wall clock: **{tot_wall:.0f}s** ({tot_wall/60:.1f} min)")
    out.append("")

    # ── Reproducibility ─────────────────────────────────────────────────
    out.append("## Reproducibility")
    out.append("")
    out.append("Any cell can be reproduced by:")
    out.append("```bash")
    out.append("python autoadapter_bench/eval.py \\")
    out.append("    --robot <robot_id> --suites <suites> \\")
    out.append("    --model <bedrock_model_id> \\")
    out.append("    --output autoadapter_bench/results/<robot>_<model>.json")
    out.append("```")

    output_path.write_text("\n".join(out))
    print(f"Wrote: {output_path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir",
                   default=str(REPO_ROOT / "autoadapter_bench" / "results"))
    p.add_argument("--output",
                   default=str(REPO_ROOT / "autoadapter_bench" / "LEADERBOARD.md"))
    args = p.parse_args()
    rows = load_results(Path(args.results_dir))
    if not rows:
        print(f"No result JSONs found under {args.results_dir}")
        return
    print(f"Loaded {len(rows)} results")
    render_markdown(rows, Path(args.output))


if __name__ == "__main__":
    main()
