"""Draw the five existing PiPER process traces without running an experiment.

First run extract_stage_traces.py; then run this script with matplotlib installed.
Outputs an editable SVG and a PNG under data/runs/figures, outside thesis sources.
The --thesis layout also exports a vector PDF at the thesis's 140 mm text width.
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


OUT = Path(__file__).resolve().parent / "data/runs/figures"
INK, MUTED = "#243746", "#65717A"
TURN_COLOURS = {
    "read_plan": "#DADFE3",
    "write": "#628FB6",
    "execute": "#3F9B8C",
    "error": "#CE5264",
    "recap_plan": "#8F7CAC",
}
STAGE_COLOURS = {
    "Study": "#9ABCCD", "Design": "#9C88B7",
    "Generate": "#648FAC", "Validation": "#78838B",
    "Repair": "#CFA36C", "Export": "#70A598",
    "ReCAP": "#B28EA6",
}
PASS, FAIL = "#377EA3", "#D17A50"


def draw_turns(ax, x, y, turns, stage):
    n = len(turns)
    for j, turn in enumerate(turns):
        ax.add_patch(Rectangle((x + j, y - .17), .96, .34,
                               facecolor=TURN_COLOURS[turn["category"]],
                               edgecolor="white", linewidth=.3))
    ax.plot([x, x + n], [y + .23, y + .23], color="#85929A", lw=.65)
    ax.text(x + n / 2, y + .32, stage, ha="center", va="bottom",
            fontsize=8, color=INK)
    return x + n


def draw_validation(ax, x, y, phase, number):
    verdict = phase["validation"]
    ax.scatter([x + 2], [y], marker="D", s=37,
               color=PASS if verdict["all_ok"] else FAIL, zorder=4)
    ax.text(x + 2, y + .32, f"V{number}", ha="center", va="bottom",
            fontsize=7.8, color=INK)
    ax.text(x + 2, y - .26, f'{verdict["n_passed"]}/{verdict["n_total"]}',
            ha="center", va="top", fontsize=8, color=MUTED)
    return x + 4


def row_width(run):
    return sum(4 if p["stage"] == "Validation" else p["n_turns"] + 1.7
               for p in run["phases"] if not p.get("not_executed")) + (
                   run["recap"]["n_turns"] + 1.7 if run["recap"] else 0)


def draw_thesis(runs):
    """Use physical points so labels remain readable at 140 mm on an A4 page."""
    width, height = 140 / 25.4 * 72, 380
    fig = plt.figure(figsize=(width / 72, height / 72), facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set(xlim=(0, width), ylim=(height, 0))
    ax.axis("off")

    def label(x, y, text, size=8, colour=INK, **kwargs):
        return ax.text(x, y, text, fontsize=size, color=colour,
                       va="center", **kwargs)

    def turns(x, y, records, stage):
        pitch = 3.0
        label_width = 34 if stage.startswith("Repair") else 28 if stage == "Export" else 26
        span = max(label_width, len(records) * pitch)
        start = x + (span - len(records) * pitch) / 2
        for j, turn in enumerate(records):
            ax.add_patch(Rectangle((start + j * pitch, y - 4), pitch - .4, 8,
                                   facecolor=TURN_COLOURS[turn["category"]],
                                   edgecolor="none"))
        ax.plot([x, x + span], [y - 6, y - 6], color="#85929A", lw=.55)
        label(x + span / 2, y - 12, stage, ha="center")
        return x + span + 3.5

    def validation(x, y, phase, number):
        verdict = phase["validation"]
        ax.plot([x + 7, x + 7], [y - 4, y + 4], lw=1.3,
                color=PASS if verdict["all_ok"] else FAIL,
                solid_capstyle="butt", zorder=4)
        label(x + 7, y - 12, f"V{number}", ha="center")
        count = f'{verdict["n_passed"]}/{verdict["n_total"]}'
        label(x + 7, y + 10, count, colour=MUTED, ha="center")
        return x + 14

    label(5, 9, "A  Recorded model turns through the Auto-Adapter pipeline",
          size=9.5, weight="bold")
    label(5, 22, "PiPER · Claude Opus 4.8 · five original runs", colour=MUTED)
    label(5, 34, "One rectangle = one model turn; vertical lines = independent validation suites (V1–V4).",
          colour=MUTED)

    for i, run in enumerate(runs):
        top = 45 + 32 * i
        y = top + 15
        if i % 2 == 0:
            ax.add_patch(Rectangle((3, top), width - 6, 31,
                                   facecolor="#F4F7F8", zorder=0))
        label(3, y, run["run"].replace("run_0", "Run "), weight="bold")
        x, number = 28, 0
        for phase in run["phases"]:
            if phase.get("not_executed"):
                continue
            if phase["stage"] == "Validation":
                number += 1
                x = validation(x, y, phase, number)
            elif phase["n_turns"]:
                x = turns(x, y, phase["turns"], phase["stage"])
        recap = run["recap"]
        if recap:
            x = turns(x, y, recap["turns"], "ReCAP")
            success = recap["physical_task_success"]
            label(x + 3, y - 4, "Task passed" if success else "Task failed",
                  colour=PASS if success else FAIL)
            label(x + 3, y + 8, f'{recap["n_capability_calls"]} capability calls',
                  colour=MUTED)

    def legend_item(x, y, text, colour, tick=False):
        if tick:
            ax.plot([x + 4, x + 4], [y - 3.5, y + 3.5], lw=1.3,
                    color=colour, solid_capstyle="butt")
        else:
            ax.add_patch(Rectangle((x, y - 2.5), 9, 5, facecolor=colour))
        label(x + 14, y, text)

    for x, y, key, text in [
        (8, 216, "read_plan", "Read / plan / respond"),
        (155, 216, "write", "Write file"),
        (260, 216, "execute", "Execute / probe"),
        (8, 228, "error", "Tool error"),
        (88, 228, "recap_plan", "ReCAP planning"),
    ]:
        legend_item(x, y, text, TURN_COLOURS[key])
    legend_item(225, 228, "Suite passed", PASS, tick=True)
    legend_item(313, 228, "Suite failed", FAIL, tick=True)

    label(5, 244, "B  Effort by stage", size=9.5, weight="bold")
    label(35, 253, "Recorded phase duration (minutes)", colour=MUTED)
    label(252, 242, "Generation + selected ReCAP turns", colour=MUTED)
    label(252, 253, "Generation output tokens", colour=MUTED)
    duration = fig.add_axes((35 / width, (height - 320) / height,
                            204 / width, 57 / height))
    for i, run in enumerate(runs):
        left = 0
        for stage, colour in STAGE_COLOURS.items():
            seconds = sum(p["duration_sec"] for p in run["phases"]
                          if p["stage"].split()[0] == stage)
            if stage == "ReCAP" and run["recap"]:
                seconds = run["recap"]["duration_sec"]
            minutes = seconds / 60
            duration.barh(4 - i, minutes, left=left, height=.58,
                          color=colour, edgecolor="white", lw=.4)
            left += minutes
        recap_n = run["recap"]["n_turns"] if run["recap"] else 0
        duration.text(16.9, 4 - i,
                      f'{run["generation_model_turns"]} + {recap_n} turns   ·   '
                      f'{run["generation_tokens"]["out"] / 1000:.1f}k out',
                      color=MUTED, va="center", fontsize=8)
    duration.set_yticks(range(5), [f"Run {n}" for n in range(5, 0, -1)])
    duration.set(xlim=(0, 16), ylim=(-.6, 4.6))
    duration.set_xticks([0, 4, 8, 12, 16])
    duration.tick_params(length=0, colors=MUTED, labelsize=8, pad=3)
    duration.spines[["top", "right", "left"]].set_visible(False)
    duration.spines["bottom"].set_color("#CFD7DC")
    for (stage, colour), (x, y) in zip(STAGE_COLOURS.items(), [
        (8, 342), (53, 342), (101, 342), (160, 342),
        (221, 342), (268, 342), (315, 342),
    ]):
        legend_item(x, y, stage, colour)
    label(5, 359, "ReCAP shows pick-and-place L1 only. Tool errors and physical verdicts", colour=MUTED)
    label(5, 370, "are distinct. Stage gaps do not represent elapsed time.", colour=MUTED)
    # Keep the physical size fixed: tight cropping would silently rescale labels.
    for ext in ("svg", "png", "pdf"):
        target = OUT / f"exp1_stage_traces_thesis.{ext}"
        fig.savefig(target, dpi=300)
        print(target)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thesis", action="store_true",
                        help="Use a portrait A4 layout at the thesis's 140 mm text width.")
    args = parser.parse_args()
    evidence = json.loads((OUT / "exp1_stage_traces_data.json").read_text())
    runs = evidence["runs"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "svg.fonttype": "none", "axes.linewidth": .6})
    if args.thesis:
        draw_thesis(runs)
        return
    fig = plt.figure(figsize=(13.8, 8.4), facecolor="white")
    traces = fig.add_axes((.095, .47, .875, .39))
    duration = fig.add_axes((.095, .12, .68, .22))
    xmax = max(map(row_width, runs)) + 22
    for i, run in enumerate(runs):
        y = 4 - i
        if i % 2 == 0:
            traces.add_patch(Rectangle((-.8, y - .44), xmax + 1, .98,
                                      facecolor="#F4F7F8", zorder=0))
        traces.text(-1.8, y, run["run"].replace("run_0", "Run "),
                    ha="right", va="center", fontsize=9.5, color=INK)
        x, validation_number = 0, 0
        for phase in run["phases"]:
            if phase.get("not_executed"):
                continue
            if phase["stage"] == "Validation":
                validation_number += 1
                x = draw_validation(traces, x, y, phase, validation_number)
            elif phase["n_turns"]:
                x = draw_turns(traces, x, y, phase["turns"], phase["stage"]) + 1.7
        recap = run["recap"]
        if recap:
            x = draw_turns(traces, x, y, recap["turns"], "ReCAP") + 1.7
            success = recap["physical_task_success"]
            traces.text(x, y, "Task passed" if success else "Task failed",
                        color=PASS if success else FAIL, va="center", fontsize=8.5)
            traces.text(x, y - .28, f'{recap["n_capability_calls"]} capability calls',
                        color=MUTED, va="center", fontsize=7.5)
        else:
            traces.text(x + .3, y, "Export / ReCAP", color=MUTED, va="center", fontsize=8)
            traces.text(x + .3, y - .27, "not executed", color=MUTED,
                        va="center", fontsize=8)
    traces.set(xlim=(-.8, xmax), ylim=(-.55, 4.8))
    traces.axis("off")
    fig.text(.04, .955, "A  Recorded model turns through the Auto-Adapter pipeline",
             weight="bold", color=INK, fontsize=12)
    fig.text(.04, .92, "PiPER · Claude Opus 4.8 · five original runs", color=MUTED, fontsize=10)
    fig.text(.04, .89, "One rectangle = one model turn; diamonds = independent validation suites (V1–V4).",
             color=MUTED, fontsize=9)
    handles = [Patch(facecolor=TURN_COLOURS[k], label=lab) for k, lab in [
        ("read_plan", "Read / plan / respond"), ("write", "Write file"),
        ("execute", "Execute / probe"), ("error", "Tool error"),
        ("recap_plan", "ReCAP planning")]]
    handles += [Line2D([], [], marker="D", ls="", color=PASS, label="Suite passed"),
                Line2D([], [], marker="D", ls="", color=FAIL, label="Suite failed")]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(.52, .43), ncol=7,
               frameon=False, fontsize=8, handlelength=1.4, columnspacing=1.5)

    for i, run in enumerate(runs):
        left = 0
        for stage, colour in STAGE_COLOURS.items():
            seconds = sum(p["duration_sec"] for p in run["phases"]
                          if p["stage"].split()[0] == stage)
            if stage == "ReCAP" and run["recap"]:
                seconds = run["recap"]["duration_sec"]
            minutes = seconds / 60
            duration.barh(4 - i, minutes, left=left, height=.6,
                          color=colour, edgecolor="white", lw=.6)
            left += minutes
        generation = run["generation_model_turns"]
        recap_n = run["recap"]["n_turns"] if run["recap"] else 0
        duration.text(16.5, 4 - i,
                      f'{generation} + {recap_n} turns   ·   '
                      f'{run["generation_tokens"]["out"] / 1000:.1f}k out',
                      color=MUTED, va="center", fontsize=8.5)
    duration.set_yticks(range(5), [f"Run {n}" for n in range(5, 0, -1)])
    duration.set(xlim=(0, 16), ylim=(-.6, 4.6))
    duration.set_xticks([0, 4, 8, 12, 16])
    duration.set_xlabel("Recorded phase duration (minutes)", color=MUTED, fontsize=9)
    duration.tick_params(length=0, colors=MUTED, labelsize=8.5)
    duration.spines[["top", "right", "left"]].set_visible(False)
    duration.spines["bottom"].set_color("#CFD7DC")
    fig.text(.04, .38, "B  Effort by stage", weight="bold", fontsize=12, color=INK)
    fig.text(.794, .352, "Generation + selected ReCAP turns\nGeneration output tokens",
             fontsize=8.5, color=MUTED, linespacing=1.5)
    fig.legend(handles=[Patch(facecolor=c, label=s) for s, c in STAGE_COLOURS.items()],
               loc="center", bbox_to_anchor=(.45, .055), ncol=7, frameon=False,
               fontsize=8.5, handlelength=1.4, columnspacing=1.8)
    fig.text(.04, .013,
             "ReCAP shows pick-and-place L1 only. Tool errors and physical verdicts are distinct. "
             "Stage gaps do not represent elapsed time.", fontsize=8, color=MUTED)
    for ext in ("svg", "png"):
        target = OUT / f"exp1_stage_traces.{ext}"
        fig.savefig(target, dpi=190, bbox_inches="tight")
        print(target)
    plt.close(fig)


if __name__ == "__main__":
    main()
