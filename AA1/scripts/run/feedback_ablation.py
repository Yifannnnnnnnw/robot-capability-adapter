#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Feedback-channel ablation (reviewer request): does grounding the repair
loop in independent post-step PHYSICS state matter, vs accepting the agent's
self-reported completion (text/unit-test style)?

  cond=physics    : max_outer_retries=2  -> independent physics validator
                    re-checks post-step state and drives repair (ours).
  cond=selfreport : max_outer_retries=0  -> accept the agent's first
                    self-completed driver (it ran its own code, no
                    independent physics re-check forces a repair).

Both are then graded by the SAME independent physics validator (validate_ok).
gen_ok = the agent self-reported "done" (wrote a driver). The selfreport gap
between gen_ok (self-report) and validate_ok (physics) quantifies self-report
bias; physics-vs-selfreport validate_ok quantifies the value of physics feedback.

Usage: feedback_ablation.py [conds] [seeds]   e.g. physics,selfreport 1,2,3
"""
import sys, json, time
from pathlib import Path
from auto_adapter.orchestrator_from_scratch import FromScratchConfig, FromScratchOrchestrator

MJCF = Path("assets/mjcf/robotstudio_so101/scene.xml").resolve()
WS_ROOT = Path("artifacts/feedback_ablation").resolve()
CONDS = {"physics": 2, "selfreport": 0}

conds = sys.argv[1].split(",") if len(sys.argv) > 1 else ["physics", "selfreport"]
seeds = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [1, 2, 3]
WS_ROOT.mkdir(parents=True, exist_ok=True)
out = WS_ROOT / "results.jsonl"

for cond in conds:
    for s in seeds:
        rid = f"abl_{cond}_s{s}"
        cfg = FromScratchConfig(robot_id=rid, mjcf_path=MJCF, workspace_root=WS_ROOT,
                                max_outer_retries=CONDS[cond])
        t0 = time.time()
        print(f"=== START {rid} (max_outer_retries={CONDS[cond]}) {time.strftime('%H:%M:%S')} ===", flush=True)
        try:
            with FromScratchOrchestrator(cfg) as orch:
                r = orch.run()
            rep = r.validate_report or {}
            rec = {"cond": cond, "seed": s, "gen_ok": r.gen_ok, "validate_ok": r.validate_ok,
                   "outer_attempts": rep.get("outer_attempts"),
                   "n_fail": sum(1 for t in rep.get("tests", []) if not t.get("ok")),
                   "failing": [t.get("test") for t in rep.get("tests", []) if not t.get("ok")],
                   "dur_sec": round(time.time() - t0), "tok_out": r.total_tokens.get("out"),
                   "err": r.error}
        except Exception as e:
            rec = {"cond": cond, "seed": s, "EXCEPTION": str(e)[:300],
                   "dur_sec": round(time.time() - t0)}
        print("RESULT " + json.dumps(rec), flush=True)
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
print("=== ABLATION DONE ===", flush=True)
