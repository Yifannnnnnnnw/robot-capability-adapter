"""PRE-FLIGHT: re-grade every changed-grader task across ALL models with the
REAL eval.evaluate_success (new graders), and compute the OLD verdict inline.
Confirms: A-A/CaP unchanged everywhere (old==new), Self flips where expected.
Exercises the real grading path (replay + evaluate_success) at scale.
"""
import sys, os, json, importlib.util
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import autoadapter_bench.eval as ev
from autoadapter_bench.eval import _replay_tool_calls, evaluate_success

REF = ROOT / "artifacts/auto_adapter_so101_v2_artifacts"
XM = ROOT / "artifacts/from_scratch_xmodel"
OBJS = ["banana", "mug", "bottle", "duck", "lego", "screwdriver"]
# tasks whose grader was CHANGED -> the only ones that could shift
CHANGED = {
    "conditional_pick": "object_lifted_by",
    "spatial_query_pick": "object_lifted_by",
    "place_duck_inside_mug": "object_close_to_object_xy",
    "stack_lego_on_duck": "object_above_object",
    "stable_place_screwdriver": "object_placed_near_target",
    "tower_3_objects": "objects_in_tower",
}
MODELS = ["sonnet46", "opus48", "haiku45", "novapro", "deepseek", "ministral8b", "qwen32"]


def load_log(jf):
    log = []
    for line in open(jf):
        line = line.strip()
        if not line: continue
        o = json.loads(line)
        for a in (o.get("actions") or ([o] if o.get("name") else [])):
            nm = a.get("name") or a.get("tool")
            if nm: log.append({"tool": nm, "input": a.get("input") or {}})
    return log


def build(ws, drv):
    sys.path.insert(0, str(ws))
    for m in ("driver", "driver_from_scratch"): sys.modules.pop(m, None)
    spec = importlib.util.spec_from_file_location("driver", str(ws / drv))
    mod = importlib.util.module_from_spec(spec); cwd = os.getcwd(); os.chdir(ws)
    try:
        spec.loader.exec_module(mod)
        return mod.build() if hasattr(mod, "build") else mod.Robot.build_from_mjcf(
            os.path.realpath(ws / "mjcf.xml"))
    finally:
        os.chdir(cwd)
        if str(ws) in sys.path: sys.path.remove(str(ws))


def get_spec(task):
    d = json.load(open(ROOT / "autoadapter_bench/results/leaderboard_b/diag_opus48.json"))
    for s in d["suites"].values():
        for t in s["tasks"]:
            if t["id"] == task: return t["success_spec"]


def old_verdict(typ, spec, before, r):
    """The PRE-FIX grader logic, computed inline."""
    if typ == "object_lifted_by":
        ca = spec.get("require_correct_choice_y_argmax", False)
        correct = None
        if ca:
            ys = {o: float(np.array(before["objects"][o])[1]) for o in spec["accept_objects"]}
            correct = max(ys, key=ys.get)
        for o in spec["accept_objects"]:
            try:
                dz = float(ev._obj_pos(r, o)[2] - np.array(before["objects"][o])[2])
                if dz > spec["tolerance_m"]:
                    return not (ca and o != correct)
            except Exception: continue
        return False
    if typ == "object_close_to_object_xy":
        a = ev._obj_pos(r, spec["obj_a"]); b = ev._obj_pos(r, spec["obj_b"])
        return float(np.linalg.norm(a[:2] - b[:2])) < spec["xy_tolerance_m"]
    if typ == "object_above_object":
        t = ev._obj_pos(r, spec["top"]); b = ev._obj_pos(r, spec["bottom"])
        return bool(t[2] > b[2] + 0.005 and np.linalg.norm(t[:2] - b[:2]) < spec["xy_tolerance_m"])
    if typ == "object_placed_near_target":
        for _ in range(int(spec.get("settle_steps", 0))): r.step(1)
        p = ev._obj_pos(r, spec["object"])
        return float(np.linalg.norm(p[:2] - np.array(spec["target_xy"]))) < spec["xy_tolerance_m"]
    if typ == "objects_in_tower":
        for _ in range(int(spec.get("settle_steps", 0))): r.step(1)
        st = spec["stack"]; tol = spec["xy_tolerance_m"]; mind = spec.get("min_dz_m", 0.01)
        pos = {n: ev._obj_pos(r, n) for n in st}
        return all(np.linalg.norm(pos[u][:2] - pos[l][:2]) < tol and (pos[u][2] - pos[l][2]) > mind
                   for l, u in zip(st[:-1], st[1:]))
    return None


class Dummy:
    summary = ""; tool_call_log = []


def cell(method, ws, drv, td, task):
    typ = CHANGED[task]; spec = get_spec(task)
    traces = sorted((ws / td).glob(f"{task}_t*.jsonl")) or sorted((ws / td).glob(f"{task}.jsonl"))
    if not traces: return None
    olds, news = [], []
    for jf in traces:
        try:
            r = build(ws, drv)
            if hasattr(r, "home"):
                try: r.home()
                except Exception: pass
            before = {"objects": {o: np.asarray(ev._obj_pos(r, o), float).tolist()
                                  for o in OBJS if _ok(r, o)}}
            log = load_log(jf)
            # OLD on a fresh replay
            _replay_tool_calls(r, log)
            ov = bool(old_verdict(typ, spec, before, r))
            # NEW on a fresh replay (real grader)
            r2 = build(ws, drv)
            if hasattr(r2, "home"):
                try: r2.home()
                except Exception: pass
            before2 = {"objects": {o: np.asarray(ev._obj_pos(r2, o), float).tolist()
                                   for o in OBJS if _ok(r2, o)}}
            _replay_tool_calls(r2, log)
            nv, _, _ = evaluate_success(r2, before2, spec, Dummy(), task)
            olds.append(ov); news.append(bool(nv))
        except Exception as e:  # noqa: BLE001
            print(f"      {method}/{task}/{jf.name}: ERR {type(e).__name__}: {e}", file=sys.stderr)
    if not olds: return None
    return sum(olds), sum(news), len(olds)


def _ok(r, o):
    try: ev._obj_pos(r, o); return True
    except Exception: return False


regress = 0
print(f"{'method':6s}{'model':12s}{'task':24s}{'OLD':6s}{'NEW':6s}{'flip?'}")
print("-" * 64)
for method, mk in (("A-A", "ours_so101"), ("CaP", "cap_so101")):
    for model in MODELS:
        for task in CHANGED:
            res = cell(method, REF, "driver.py", f"task_traces/{mk}_{model}", task)
            if res is None: continue
            o, n, k = res
            flip = "" if o == n else (" <== REGRESSION!" if method in ("A-A", "CaP") else " (self-fix)")
            if o != n and method in ("A-A", "CaP"): regress += 1
            if o != n:
                print(f"{method:6s}{model:12s}{task:24s}{o}/{k:<4}{n}/{k:<4}{flip}")
print(f"\nA-A/CaP regression cells (old!=new): {regress}  (MUST be 0)")
print("(only cells that differ are printed; silent = old==new = no change)")
