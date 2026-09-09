"""Reproducible significance statistics for AutoAdapter-Bench (SO-101 13-task).

Reads the per-trial result JSONs and computes, from a SINGLE place so the paper
numbers are reproducible:
  * per-cell physics pass rate + Wilson 95% CI (n = 13 tasks x 5 trials = 65),
  * model-level paired tests over the 7 LLMs (sign test, Wilcoxon) — UNDERPOWERED
    (n=7), reported for transparency,
  * task-level paired tests over (model, task) cells (n up to 91) — the powered
    test the paper should cite,
  * pooled trial rates.

Self column (model writes + runs its own driver) is read from the grasp-verified
diagnostics; models that failed synthesis contribute no Self cell.

Usage:  python autoadapter_bench/stats.py [--json out.json]
No side effects beyond optionally writing the report JSON.
"""
from __future__ import annotations
import json, math, argparse
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "autoadapter_bench/results"
MODELS = ["sonnet46", "opus48", "haiku45", "novapro", "deepseek", "ministral8b", "qwen32"]
COVERAGE_TASK = "visit_all_objects"  # the 1 coverage-graded task (12-task physics subset excludes it)


def _load_task_rates(path: Path) -> dict[str, tuple[int, int]]:
    """task_id -> (n_pass, n_trials) from a per-trial diag/result JSON."""
    d = json.loads(path.read_text())
    out: dict[str, tuple[int, int]] = {}
    for _suite, sd in d.get("suites", {}).items():
        for t in sd.get("tasks", []):
            tr = t.get("trials", [])
            n = len(tr)
            if n:
                out[t["id"]] = (sum(1 for x in tr if x.get("physics_ok")), n)
    return out


def wilson_ci(n_pass: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = n_pass / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / den), min(1.0, (centre + half) / den))


def binom_two_sided(k_more: int, n: int) -> float:
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) * 0.5 ** n for i in range(k_more, n + 1))
    return min(1.0, 2 * tail)


def sign_test(diffs: list[float]) -> dict:
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    return {"wins": pos, "losses": neg, "ties": len(diffs) - n,
            "n_effective": n, "p_two_sided": binom_two_sided(max(pos, neg), n)}


def wilcoxon_signed_rank(diffs: list[float]) -> dict:
    """Wilcoxon signed-rank with average ranks for ties; normal approx p (continuity-
    corrected). Self-contained so the package has no scipy dependency."""
    nz = [d for d in diffs if d != 0]
    n = len(nz)
    if n == 0:
        return {"W": 0.0, "p_two_sided": 1.0, "n": 0}
    order = sorted(range(n), key=lambda i: abs(nz[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nz[order[j + 1]]) == abs(nz[order[i]]):
            j += 1
        avg = (i + 1 + j + 1) / 2.0  # average rank (1-based) for the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_plus = sum(ranks[i] for i in range(n) if nz[i] > 0)
    w_minus = sum(ranks[i] for i in range(n) if nz[i] < 0)
    W = min(w_plus, w_minus)
    mean = n * (n + 1) / 4.0
    sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    if sd == 0:
        return {"W": W, "p_two_sided": 1.0, "n": n}
    z = (W - mean + 0.5) / sd  # continuity correction toward the mean
    p = 2 * 0.5 * math.erfc(abs(z) / math.sqrt(2))
    return {"W": W, "z": z, "p_two_sided": min(1.0, p), "n": n,
            "w_plus": w_plus, "w_minus": w_minus}


def build_report() -> dict:
    uses, fs = {}, {}
    for m in MODELS:
        uses[m] = {
            "aa": _load_task_rates(RES / f"clean/ours_so101_{m}.json"),
            "cap": _load_task_rates(RES / f"clean/cap_so101_{m}.json"),
        }
        fp = RES / f"fewshot/cap_so101_{m}.json"
        fs[m] = _load_task_rates(fp) if fp.exists() else {}

    # ---- task-level paired (model, task) cells ----
    def paired(a_key: str, b_src: dict) -> dict:
        diffs, cells = [], []
        for m in MODELS:
            a = uses[m]["aa"]
            b = b_src[m]
            for t in set(a) & set(b):
                ra = a[t][0] / a[t][1]
                rb = b[t][0] / b[t][1]
                diffs.append(ra - rb)
                cells.append((m, t, ra, rb))
        return {"n_cells": len(diffs),
                "mean_diff_pp": (sum(diffs) / len(diffs) * 100) if diffs else 0.0,
                "sign_test": sign_test(diffs),
                "wilcoxon": wilcoxon_signed_rank(diffs)}

    aa_vs_cap_task = paired("aa", {m: uses[m]["cap"] for m in MODELS})
    aa_vs_capfs_task = paired("aa", fs)

    # ---- model-level paired (7 aggregate rates) ----
    def cell_rate(d: dict) -> float:
        tp = sum(p for p, n in d.values())
        tn = sum(n for p, n in d.values())
        return tp / tn if tn else 0.0

    aa_rates = [cell_rate(uses[m]["aa"]) for m in MODELS]
    cap_rates = [cell_rate(uses[m]["cap"]) for m in MODELS]
    capfs_rates = [cell_rate(fs[m]) if fs[m] else None for m in MODELS]
    d_model = [a - c for a, c in zip(aa_rates, cap_rates)]
    d_model_fs = [a - c for a, c in zip(aa_rates, capfs_rates) if c is not None]

    # ---- per-cell Wilson CIs ----
    per_cell = {}
    for m in MODELS:
        row = {}
        for key in ("aa", "cap"):
            tp = sum(p for p, n in uses[m][key].values())
            tn = sum(n for p, n in uses[m][key].values())
            lo, hi = wilson_ci(tp, tn)
            row[key] = {"rate": round(tp / tn, 3) if tn else None, "n": tn,
                        "ci95": [round(lo, 3), round(hi, 3)]}
        per_cell[m] = row

    pooled_aa = sum(sum(p for p, n in uses[m]["aa"].values()) for m in MODELS)
    pooled_cap = sum(sum(p for p, n in uses[m]["cap"].values()) for m in MODELS)
    pooled_n = sum(sum(n for p, n in uses[m]["aa"].values()) for m in MODELS)

    return {
        "suite": "SO-101 13-task (12 physics + 1 coverage), N=5 trials/task",
        "headline": {
            "task_level_paired_A-A_vs_CaP": aa_vs_cap_task,
            "task_level_paired_A-A_vs_CaP_fs": aa_vs_capfs_task,
            "model_level_A-A_vs_CaP_n7": {
                "mean_diff_pp": round(sum(d_model) / len(d_model) * 100, 1),
                "sign_test": sign_test(d_model),
                "wilcoxon": wilcoxon_signed_rank(d_model)},
            "model_level_A-A_vs_CaP_fs": {
                "sign_test": sign_test(d_model_fs),
                "wilcoxon": wilcoxon_signed_rank(d_model_fs)},
        },
        "pooled_trials": {"aa": pooled_aa, "cap": pooled_cap, "n": pooled_n,
                          "aa_rate": round(pooled_aa / pooled_n, 3),
                          "cap_rate": round(pooled_cap / pooled_n, 3)},
        "per_cell_wilson_ci": per_cell,
    }


def _fmt_p(p: float) -> str:
    return f"{p:.2e}" if p < 1e-3 else f"{p:.3f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default=None, help="write full report JSON here")
    args = ap.parse_args()
    r = build_report()
    h = r["headline"]
    print(f"# AutoAdapter-Bench significance  ({r['suite']})\n")
    t = h["task_level_paired_A-A_vs_CaP"]
    print("TASK-LEVEL paired A-A vs CaP (the powered test):")
    print(f"  n_cells={t['n_cells']}  mean diff {t['mean_diff_pp']:+.1f} pp  "
          f"Wilcoxon p={_fmt_p(t['wilcoxon']['p_two_sided'])}  "
          f"sign p={_fmt_p(t['sign_test']['p_two_sided'])} "
          f"({t['sign_test']['wins']}W/{t['sign_test']['losses']}L/{t['sign_test']['ties']}T)")
    tf = h["task_level_paired_A-A_vs_CaP_fs"]
    print(f"TASK-LEVEL paired A-A vs CaP_fs:")
    print(f"  n_cells={tf['n_cells']}  mean diff {tf['mean_diff_pp']:+.1f} pp  "
          f"Wilcoxon p={_fmt_p(tf['wilcoxon']['p_two_sided'])}")
    m = h["model_level_A-A_vs_CaP_n7"]
    print(f"MODEL-LEVEL A-A vs CaP (n=7, underpowered):")
    print(f"  mean {m['mean_diff_pp']:+.1f} pp  sign p={_fmt_p(m['sign_test']['p_two_sided'])} "
          f"Wilcoxon p={_fmt_p(m['wilcoxon']['p_two_sided'])}")
    pl = r["pooled_trials"]
    print(f"POOLED trials: A-A {pl['aa']}/{pl['n']}={pl['aa_rate']:.3f}  "
          f"CaP {pl['cap']}/{pl['n']}={pl['cap_rate']:.3f}")
    print("\nPER-CELL 95% Wilson CI:")
    for mdl, row in r["per_cell_wilson_ci"].items():
        a, c = row["aa"], row["cap"]
        sep = "NO " if (a["ci95"][0] > c["ci95"][1] or c["ci95"][0] > a["ci95"][1]) else "yes"
        print(f"  {mdl:11} A-A {a['rate']} {a['ci95']}  CaP {c['rate']} {c['ci95']}  CI-overlap={sep}")
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
