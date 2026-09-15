"""Render existing Exp1 results and the separate ReCAP model diagnostic.

Run with a Python environment containing matplotlib. Reads completed local
reports only; does not execute experiments or change their results.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle


ROOT = Path(__file__).resolve().parent / "data" / "runs"
DIAGNOSTIC = ROOT.parent / "diagnostics" / "recap_gpt6_astra_20260914" / "run_04"
BLUE, ORANGE, GREY, INK = "#377EA3", "#D17A50", "#E9ECEF", "#243746"
MUTED = "#5B6470"


def read_json(path):
    return json.loads(path.read_text())


def outcome(ax, x, y, passed, width=.9, height=.68):
    ax.add_patch(Rectangle((x - width / 2, y - height / 2), width, height,
                           facecolor=BLUE if passed else ORANGE, edgecolor="white"))
    ax.text(x, y, "✓" if passed else "×", color="white", ha="center",
            va="center", fontsize=14)


def main():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.fonttype": "none"})
    fig = plt.figure(figsize=(13.7, 6.2))
    a = fig.add_axes((.055, .245, .325, .545))
    b = fig.add_axes((.465, .575, .525, .215))
    c = fig.add_axes((.465, .235, .525, .115))
    eligible = []

    for i in range(5):
        rid = f"run_{i + 1:02d}"
        result = read_json(ROOT / rid / "run_result.json")
        reports = [p["metadata"]["validation_report"]
                   for p in result["generation"]["phases"]
                   if p["name"] == "03_validate"]
        for j in range(4):
            if j >= len(reports):
                a.add_patch(Rectangle((j - .44, i - .31), .88, .36,
                                      facecolor=GREY, edgecolor="white"))
                a.text(j, i + .24, "–", ha="center", va="center", color=MUTED)
                continue
            tests = reports[j]["tests"]
            count = sum(t["ok"] for t in tests)
            for k in range(len(tests)):
                a.add_patch(Rectangle((j - .44 + k * .176, i - .31), .166, .36,
                                      facecolor=BLUE if k < count else ORANGE))
            a.text(j, i + .24, f"{count}/{len(tests)}", ha="center",
                   va="center", color=INK)
        passed = bool(result["export_ok"])
        a.text(4.38, i - .03, "Yes" if passed else "No", ha="center", va="center",
               color=BLUE if passed else INK, fontweight="bold" if passed else "normal")
        if passed:
            eligible.append((i + 1, rid))

    for row, (_, rid) in enumerate(eligible):
        for j, (task, layout) in enumerate((t, l) for t in ["reach", "push", "pick_place"]
                                          for l in ["L1", "L2", "L3"]):
            report = read_json(ROOT / rid / "episodes" / task / layout / "task/task_report.json")
            outcome(b, j + (j // 3) * .25, row * 1.1, report["physical_task_success"])

    # The comparison selects retry_L1 because its first attempt had incomplete
    # recording. L2/L3 use their original diagnostic attempts. Read verdicts from
    # the selected reports, retaining the original cohort above without changes.
    comparison = read_json(DIAGNOSTIC / "comparison.json")
    baseline, astra = [], []
    for i, episode in enumerate(comparison["valid_episodes"]):
        layout = episode["layout"]
        original = read_json(ROOT / comparison["source_driver"] / "episodes" /
                             "pick_place" / layout / "task/task_report.json")
        diagnostic = read_json(Path(episode["report"]))
        baseline.append(original["physical_task_success"])
        astra.append(diagnostic["physical_task_success"])
        outcome(c, i, 0, baseline[-1], height=.72)
        outcome(c, i, 1.15, astra[-1], height=.72)

    for ax in (a, b, c):
        ax.tick_params(axis="both", length=0)
        ax.tick_params(axis="x", pad=8)
        ax.xaxis.tick_top()
        for spine in ax.spines.values():
            spine.set_visible(False)
    a.set(xlim=(-.62, 4.86), ylim=(4.55, -.8))
    a.set_yticks(range(5), [f"Run {i}" for i in range(1, 6)])
    a.set_xticks([0, 1, 2, 3, 4.38], ["Initial", "Repair 1", "Repair 2", "Repair 3", "Export"])
    a.axvline(3.76, color="#CCD3D9", lw=.8, ymin=.05, ymax=.98)
    fig.text(.055, .94, "A  Capability-case results across submissions", fontsize=11, weight="bold")
    fig.text(.055, .89, "All-case validation and Export: 2/5 runs", color=INK)

    b.set(xlim=(-.62, 8.98), ylim=(1.8, -.65))
    b.set_yticks([0, 1.1], [f"Run {n}" for n, _ in eligible])
    b.set_xticks([j + (j // 3) * .25 for j in range(9)], ["L1", "L2", "L3"] * 3)
    fig.text(.465, .94, "B  Original task outcomes with Claude Opus 4.8", fontsize=11, weight="bold")
    for x, label in [(1, "Reaching"), (4.25, "Pushing"), (7.5, "Pick-and-place")]:
        b.text(x, 1.46, label, transform=b.get_xaxis_transform(), ha="center")
    fig.text(.465, .555, "18 task episodes executed using two exported drivers", color=INK, fontsize=9)
    fig.text(.465, .518, "Runs 2, 3 and 5: 27 planned episodes not executed (no Export).",
             color=MUTED, fontsize=9)

    fig.text(.465, .46, "C  Pick-and-place after changing the ReCAP model", fontsize=11, weight="bold")
    fig.text(.465, .418, "Same Run 4 driver; scenes, prompts and budgets held fixed", color=INK, fontsize=9)
    c.set(xlim=(-4.1, 3.8), ylim=(1.7, -.55))
    c.set_yticks([])
    c.set_xticks([0, 1, 2, 3.25], ["L1", "L2", "L3", "Success"])
    for y, label, successes in [(0, "Claude Opus 4.8", baseline), (1.15, "GPT-6 Astra", astra)]:
        c.text(-4.1, y, label, va="center", color=INK,
               weight="bold" if y else "normal")
        c.text(3.25, y, f"{sum(successes)}/{len(successes)}", va="center", ha="center",
               color=BLUE if y else INK, weight="bold")
    fig.text(.465, .195, "Successful placement with the unchanged driver supports a", color=INK, fontsize=9)
    fig.text(.465, .164, "planning-model limitation in these layouts.", color=INK, fontsize=9)
    fig.text(.465, .12, "Post-hoc diagnostic; excluded from the original cohort.", color=MUTED, fontsize=9)

    fig.text(.055, .19, "Five-part bars show case counts,", color=MUTED, fontsize=9)
    fig.text(.055, .159, "not matching capability identities.", color=MUTED, fontsize=9)
    fig.text(.055, .12, "Cases were generated per run and fixed during repair.", color=MUTED, fontsize=9)
    fig.legend(handles=[Patch(facecolor=BLUE, label="Case passed / task successful"),
                        Patch(facecolor=ORANGE, label="Case failed / task unsuccessful"),
                        Patch(facecolor=GREY, label="Submission not required")],
               loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.51, .025), fontsize=9)
    out = ROOT / "figures"
    for ext in ("svg", "png"):
        fig.savefig(out / f"exp1_outcomes_v2.{ext}", dpi=220, bbox_inches="tight")
    print(f"Saved {out / 'exp1_outcomes_v2.png'} and SVG")
    print(f"Run 4 pick-and-place: Opus {sum(baseline)}/3; Astra {sum(astra)}/3")


if __name__ == "__main__":
    main()
