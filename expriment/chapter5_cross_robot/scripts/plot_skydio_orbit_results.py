#!/usr/bin/env python3
"""Plot the two recorded Skydio X2-T07 orbit attempts.

This is a read-only analysis of the saved physics traces and task reports.  It
does not import the simulator or execute a controller.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parents[3]
RUN_ROOT = ROOT / "expriment/chapter5_cross_robot/data/runs/skydio_single_20260915_01"
DEFAULT_OUTPUT = ROOT / "expriment/chapter5_cross_robot/data/previews/skydio_orbit_analysis_20260915"
COMPACT_OUTPUT = DEFAULT_OUTPUT.parent / "skydio_compact_20260915"

ATTEMPTS = (
    {
        "key": "original",
        "label": "First attempt",
        "trace_rel": "tasks_astra_diagnostic/X2-T07_central/physics_samples.jsonl",
        "report_rel": "tasks_astra_diagnostic/X2-T07_central/task_report.json",
        "color": "#2f6fbb",
    },
    {
        "key": "retry",
        "label": "Clarified retry",
        "trace_rel": "tasks_astra_orbit_clarified_20260915/X2-T07_central/physics_samples.jsonl",
        "report_rel": "tasks_astra_orbit_clarified_20260915/X2-T07_central/task_report.json",
        "color": "#d95f02",
    },
)

EXPECTED_TERMINAL_DEG = {
    "original": 359.95258566462996,
    "retry": 361.19115216201334,
}


def _wrap_angle(angle: float) -> float:
    """Match robots/skydio_x2/task_evaluation.py::_wrap_angle."""

    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def _orbit_progress_deg(samples: list[dict[str, Any]], center: tuple[float, float]) -> list[float]:
    """Mirror the evaluator's positive signed orbit-progress calculation."""

    angles = [
        math.atan2(
            float(sample["state"]["base"]["position"][1]) - center[1],
            float(sample["state"]["base"]["position"][0]) - center[0],
        )
        for sample in samples
    ]
    progress = [0.0]
    signed = 0.0
    for previous, current in zip(angles, angles[1:]):
        signed += _wrap_angle(current - previous)
        progress.append(math.degrees(signed))
    return progress


def _metric(report: dict[str, Any], check: str) -> dict[str, Any]:
    for metric in report.get("task_metrics", []):
        if metric.get("check") == check:
            return metric
    raise KeyError(f"missing task metric {check!r}")


def _load_attempt(definition: dict[str, str]) -> dict[str, Any]:
    trace_path = RUN_ROOT / definition["trace_rel"]
    report_path = RUN_ROOT / definition["report_rel"]
    samples = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    report = json.loads(report_path.read_text())
    center_raw = report["parameters"]["center_xy_m"]
    center = (float(center_raw[0]), float(center_raw[1]))
    times = [float(sample["time"]) for sample in samples]
    positions = [sample["state"]["base"]["position"] for sample in samples]
    progress_deg = _orbit_progress_deg(samples, center)
    report_progress_deg = math.degrees(float(_metric(report, "signed_orbit_progress_rad")["value"]))
    if not math.isclose(progress_deg[-1], report_progress_deg, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(
            f"{definition['key']} trace/report orbit progress mismatch: "
            f"{progress_deg[-1]:.12f} vs {report_progress_deg:.12f} deg"
        )
    expected = EXPECTED_TERMINAL_DEG[definition["key"]]
    if not math.isclose(progress_deg[-1], expected, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(
            f"{definition['key']} terminal orbit progress mismatch: "
            f"{progress_deg[-1]:.12f} vs expected {expected:.12f} deg"
        )
    rmse_metric = _metric(report, "orbit_bearing_rmse_deg")
    return {
        **definition,
        "trace_path": trace_path,
        "report_path": report_path,
        "samples": samples,
        "times": times,
        "positions": positions,
        "progress_deg": progress_deg,
        "report": report,
        "terminal_progress_deg": progress_deg[-1],
        "terminal_progress_rad": float(_metric(report, "signed_orbit_progress_rad")["value"]),
        "first_360_time_s": next(
            (time for time, progress in zip(times, progress_deg) if progress >= 360.0),
            None,
        ),
        "maximum_progress_deg": max(progress_deg),
        "bearing_rmse_deg": float(rmse_metric["value"]),
        "duration_s": times[-1] - times[0],
        "physical_task_success": bool(report.get("physical_task_success", False)),
    }


def _plot(attempts: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    center = (0.0, 0.0)
    radius = 1.5
    criterion_time_s = 60.0
    criterion_progress_deg = 360.0
    threshold_rmse_deg = 12.0

    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.9), constrained_layout=False)
    fig.patch.set_facecolor("white")
    fig.suptitle("Skydio X2-T07 orbit: first attempt and clarified retry", fontsize=13, y=0.965)

    # (a) Recorded horizontal trajectories and the requested 1.5 m orbit.
    ax = axes[0]
    orbit = plt.Circle(center, radius, fill=False, linestyle="--", linewidth=1.2,
                       color="#555555", label="1.5 m target circle")
    ax.add_patch(orbit)
    for attempt in attempts:
        x = [float(position[0]) for position in attempt["positions"]]
        y = [float(position[1]) for position in attempt["positions"]]
        ax.plot(x, y, color=attempt["color"], linewidth=1.0, alpha=0.9)
        ax.scatter(x[0], y[0], color=attempt["color"], marker="o", s=26,
                   edgecolor="white", linewidth=0.6, zorder=3)
        ax.scatter(x[-1], y[-1], color=attempt["color"], marker="X", s=42,
                   edgecolor="white", linewidth=0.6, zorder=3)
    ax.scatter([center[0]], [center[1]], color="#555555", marker="+", s=40, linewidth=1.2)
    ax.set_title("(a) Horizontal trajectory", fontsize=10.5)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-1.85, 1.85)
    ax.set_ylim(-1.85, 1.85)
    ax.grid(True, linewidth=0.45, alpha=0.35)
    ax.legend(handles=[
        Line2D([0], [0], color=attempts[0]["color"], linewidth=1.5, label="First attempt"),
        Line2D([0], [0], color=attempts[1]["color"], linewidth=1.5, label="Clarified retry"),
        Line2D([0], [0], color="#555555", linestyle="--", linewidth=1.2, label="1.5 m target circle"),
    ], loc="lower left", fontsize=8, frameon=False)

    # (b) Cumulative signed angle using the same wrapped per-sample increments
    # as the trusted T07 evaluator.
    ax = axes[1]
    for attempt in attempts:
        ax.plot(attempt["times"], attempt["progress_deg"], color=attempt["color"],
                linewidth=1.0, label=attempt["label"])
        ax.scatter([attempt["times"][-1]], [attempt["terminal_progress_deg"]],
                   color=attempt["color"], marker="X", s=42, edgecolor="white", linewidth=0.6,
                   zorder=3)
    ax.annotate(
        f"{attempts[0]['terminal_progress_deg']:.4f}°\n@ {attempts[0]['times'][-1]:.2f} s",
        xy=(attempts[0]["times"][-1], attempts[0]["terminal_progress_deg"]),
        xytext=(66.0, 331.0), textcoords="data", color=attempts[0]["color"],
        fontsize=8.2, ha="left", va="top",
        arrowprops={"arrowstyle": "-", "color": attempts[0]["color"], "linewidth": 0.7},
    )
    ax.annotate(
        f"{attempts[1]['terminal_progress_deg']:.4f}°\n@ {attempts[1]['times'][-1]:.2f} s",
        xy=(attempts[1]["times"][-1], attempts[1]["terminal_progress_deg"]),
        xytext=(22.0, 338.0), textcoords="data", color=attempts[1]["color"],
        fontsize=8.2, ha="left", va="top",
        arrowprops={"arrowstyle": "-", "color": attempts[1]["color"], "linewidth": 0.7},
    )
    ax.axhline(criterion_progress_deg, color="#555555", linestyle="--", linewidth=1.0)
    ax.axvline(criterion_time_s, color="#777777", linestyle=":", linewidth=1.0)
    ax.text(0.03, 0.96, "360° by 60 s", transform=ax.transAxes, ha="left", va="top",
            fontsize=8.2, color="#444444")
    ax.set_title("(b) Net angular progress", fontsize=10.5)
    ax.set_xlabel("simulation time (s)")
    ax.set_ylabel("net angular progress (deg)")
    ax.set_xlim(0.0, max(61.0, max(attempt["times"][-1] for attempt in attempts) + 1.0))
    ax.set_ylim(0.0, max(370.0, max(attempt["terminal_progress_deg"] for attempt in attempts) + 8.0))
    ax.grid(True, linewidth=0.45, alpha=0.35)
    ax.legend(loc="lower right", fontsize=8, frameon=False)

    # (c) The report's circuit RMSE values.  This is a circuit-level RMSE,
    # not a pointwise instantaneous-error limit.
    ax = axes[2]
    labels = [attempt["label"].replace(" ", "\n", 1) for attempt in attempts]
    values = [attempt["bearing_rmse_deg"] for attempt in attempts]
    bars = ax.bar(labels, values, color=[attempt["color"] for attempt in attempts], width=0.58)
    ax.axhline(threshold_rmse_deg, color="#555555", linestyle="--", linewidth=1.0)
    ax.text(0.03, threshold_rmse_deg + 0.35, "12° RMSE threshold", ha="left", va="bottom",
            transform=ax.get_yaxis_transform(), fontsize=8.2, color="#444444")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, value + 0.35, f"{value:.3f}°",
                ha="center", va="bottom", fontsize=8.8)
    ax.set_title("(c) Inward-bearing RMSE", fontsize=10.5)
    ax.set_ylabel("RMSE over circuit (deg)")
    ax.set_ylim(0.0, max(16.0, max(values) + 1.6))
    ax.grid(True, axis="y", linewidth=0.45, alpha=0.35)

    caption = (
        "Units: x/y and target radius in m; time in s; angles and bearing RMSE in degrees. "
        "Each colour is one complete saved X2-T07 trace: first attempt/original diagnostic (68.93 s) "
        "or clarified retry (30.81 s). Panel (c) reports circuit-level inward-bearing "
        "RMSE; 12° is its aggregate threshold, not an instantaneous error limit. "
        "The first attempt briefly exceeds 360° before ending below it."
    )
    fig.subplots_adjust(left=0.06, right=0.985, top=0.87, bottom=0.15, wspace=0.31)

    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "skydio_orbit_analysis.png"
    fig.savefig(png_path, dpi=300, facecolor="white")
    plt.close(fig)

    source = {
        "figure": "skydio_orbit_analysis.png",
        "analysis": "read-only plot of saved X2-T07 physics traces and task reports",
        "source_run_root": str(RUN_ROOT.relative_to(ROOT)),
        "center_xy_m": list(center),
        "target_radius_m": radius,
        "criterion": {
            "cumulative_positive_angle_deg": criterion_progress_deg,
            "deadline_s": criterion_time_s,
            "inward_bearing_rmse_threshold_deg": threshold_rmse_deg,
            "rmse_is_circuit_level": True,
        },
        "angle_algorithm": (
            "For each saved sample, atan2(y-center_y, x-center_x); cumulative signed "
            "positive progress is the sum of wrap((angle[i]-angle[i-1])) in [-pi, pi), "
            "matching robots/skydio_x2/task_evaluation.py::_t07."
        ),
        "attempts": [
            {
                "label": attempt["label"],
                "trace": str(attempt["trace_path"].relative_to(ROOT)),
                "report": str(attempt["report_path"].relative_to(ROOT)),
                "sample_count": len(attempt["samples"]),
                "start_time_s": attempt["times"][0],
                "end_time_s": attempt["times"][-1],
                "terminal_progress_deg": attempt["terminal_progress_deg"],
                "terminal_progress_rad": attempt["terminal_progress_rad"],
                "first_at_least_360_time_s": attempt["first_360_time_s"],
                "maximum_progress_deg": attempt["maximum_progress_deg"],
                "report_bearing_rmse_deg": attempt["bearing_rmse_deg"],
                "physical_task_success": attempt["physical_task_success"],
            }
            for attempt in attempts
        ],
        "caption": caption,
    }
    source_path = output_dir / "skydio_orbit_analysis_source.json"
    source_path.write_text(json.dumps(source, indent=2) + "\n")
    return png_path, source_path


def _plot_compact(attempts: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    """Show saved XY trajectories and report RMSE, without changing verdicts."""
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.5))
    ax = axes[0]
    ax.add_patch(plt.Circle((0.0, 0.0), 1.5, fill=False, linestyle=":",
                           linewidth=1.2, color="#555555", zorder=1))
    for attempt, style in zip(attempts, ("-", "--"), strict=True):
        x = [float(position[0]) for position in attempt["positions"]]
        y = [float(position[1]) for position in attempt["positions"]]
        ax.plot(x, y, color=attempt["color"], linestyle=style, linewidth=1.2,
                label=attempt["label"])
        ax.scatter(x[0], y[0], color=attempt["color"], marker="o", s=24,
                   edgecolor="white", linewidth=0.5, zorder=4)
        ax.scatter(x[-1], y[-1], color=attempt["color"], marker="X", s=36,
                   edgecolor="white", linewidth=0.5, zorder=4)
    ax.scatter(0, 0, color="#555555", marker="+", s=24)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], color="#555555", linestyle=":", linewidth=1.2))
    labels.append("1.5 m reference circle")
    ax.legend(handles, labels, loc="center", bbox_to_anchor=(0.5, 0.62),
              fontsize=8.5, frameon=False)
    ax.set(title="(a) Horizontal trajectory", xlabel="x (m)", ylabel="y (m)",
           xlim=(-1.8, 1.8), ylim=(-1.8, 1.8), aspect="equal")
    ax.grid(True, linewidth=0.45, alpha=0.3)

    ax = axes[1]
    values = [attempt["bearing_rmse_deg"] for attempt in attempts]
    bars = ax.bar([attempt["label"] for attempt in attempts], values,
                  color=[attempt["color"] for attempt in attempts], width=0.5)
    ax.axhline(12.0, color="#555555", linestyle="--", linewidth=1.0)
    ax.text(0.02, 12.25, "12° threshold", transform=ax.get_yaxis_transform(),
            fontsize=9, ha="left", va="bottom", color="#444444")
    for bar, value in zip(bars, values, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.25, f"{value:.2f}°",
                ha="center", va="bottom", fontsize=10)
    ax.set(title="(b) Inward-bearing RMSE", ylabel="RMSE over circuit (deg)",
           ylim=(0.0, 16.0))
    ax.grid(True, axis="y", linewidth=0.45, alpha=0.3)
    ax.set_axisbelow(True)
    fig.tight_layout(pad=0.8, w_pad=2.0)

    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "skydio_orbit_compact.png"
    fig.savefig(png_path, dpi=300, facecolor="white")
    plt.close(fig)
    source_path = output_dir / "skydio_orbit_compact_source.json"
    source = {
        "figure": png_path.name,
        "generated_by": str(Path(__file__).resolve().relative_to(ROOT)),
        "target_center_xy_m": [0.0, 0.0],
        "target_radius_m": 1.5,
        "inward_bearing_rmse_threshold_deg": 12.0,
        "attempts": [
            {
                "label": attempt["label"],
                "trace": str(attempt["trace_path"].relative_to(ROOT)),
                "report": str(attempt["report_path"].relative_to(ROOT)),
                "sample_count": len(attempt["samples"]),
                "simulation_time_range_s": [attempt["times"][0], attempt["times"][-1]],
                "report_bearing_rmse_deg": attempt["bearing_rmse_deg"],
                "original_report_success": attempt["physical_task_success"],
            }
            for attempt in attempts
        ],
        "caption": (
            "Complete recorded XY trajectories of the first orbit attempt and its "
            "clarified retry; the dotted circle has radius 1.5 m. Circles mark starts "
            "and crosses mark ends. Bars show circuit-level inward-bearing RMSE "
            "from the original reports; 12 degrees is an aggregate threshold, not an "
            "instantaneous limit. No new simulation or task re-evaluation was performed."
        ),
    }
    source_path.write_text(json.dumps(source, indent=2) + "\n")
    return png_path, source_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--compact", action="store_true",
                        help="Plot only XY trajectories and circuit-bearing RMSE in two panels.")
    args = parser.parse_args()
    attempts = [_load_attempt(definition) for definition in ATTEMPTS]
    output_dir = args.output_dir
    if args.compact and output_dir.resolve() == DEFAULT_OUTPUT.resolve():
        output_dir = COMPACT_OUTPUT
    plot = _plot_compact if args.compact else _plot
    png_path, source_path = plot(attempts, output_dir)
    for attempt in attempts:
        print(
            f"{attempt['label']}: samples={len(attempt['samples'])} "
            f"duration_s={attempt['duration_s']:.12f} "
            f"terminal_progress_deg={attempt['terminal_progress_deg']:.12f} "
            f"bearing_rmse_deg={attempt['bearing_rmse_deg']:.12f}"
        )
    print(f"PNG: {png_path}")
    print(f"Source: {source_path}")


if __name__ == "__main__":
    main()
