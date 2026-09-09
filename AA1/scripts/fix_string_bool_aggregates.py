"""Repair the numpy-bool-serialized-as-string bug across all result JSONs.

Root cause: an evaluator returned a numpy bool; json.dumps(default=str) saved
it as the STRING "True"/"False"; the bare string "False" is truthy in Python,
so the aggregator counted failed trials as passes (inflating ours_* files by
~5 trials each, ~8pp).

This script (NO re-running of any eval):
  1. backs up every affected file to results/_prefix_bugfix_backup/
  2. converts string "True"/"False" -> real bool in trial physics_ok/llm_ok/
     agreement
  3. recomputes the aggregate with the fixed _build_aggregate
Prints before/after physics_pass_rate per file.
"""
import json, glob, shutil, sys
from pathlib import Path

sys.path.insert(0, "autoadapter_bench")
from eval import _build_aggregate  # the FIXED version

BACKUP = Path("autoadapter_bench/results/_prefix_bugfix_backup")
BACKUP.mkdir(parents=True, exist_ok=True)

def _norm(v):
    if v == "True":  return True
    if v == "False": return False
    return v

changed = []
for f in sorted(glob.glob("autoadapter_bench/results/**/*.json", recursive=True)):
    if "_prefix_bugfix_backup" in f:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if "suites" not in d:
        continue
    n_str = 0
    for s in d.get("suites", {}).values():
        for tk in s.get("tasks", []):
            for t in tk.get("trials", []):
                for k in ("physics_ok", "llm_ok", "agreement"):
                    if isinstance(t.get(k), str):
                        t[k] = _norm(t[k]); n_str += 1
    if n_str == 0:
        continue
    before = (d.get("aggregate", {}) or {}).get("physics_pass_rate")
    # back up untouched original
    rel = f.split("results/", 1)[1].replace("/", "__")
    shutil.copy2(f, BACKUP / rel)
    _build_aggregate(d)  # recompute from normalized trials
    after = d["aggregate"]["physics_pass_rate"]
    Path(f).write_text(json.dumps(d, indent=2, default=str))
    changed.append((f, before, after, n_str))

print(f"Repaired {len(changed)} files (backups in {BACKUP}/)\n")
print(f"{'file':50s}{'before':>8s}{'after':>8s}{'fixed':>7s}")
for f, b, a, n in changed:
    bs = f"{b:.0%}" if isinstance(b, (int, float)) else str(b)
    print(f"  {f.split('results/')[1]:48s}{bs:>8s}{a:>8.0%}{n:>7d}")
