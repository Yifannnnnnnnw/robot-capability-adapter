# SPDX-License-Identifier: Apache-2.0
"""Onboarding-time benchmark: run from-scratch synthesis on EVERY robot
in the zoo. The headline number for the paper.

For each robot:
  - INPUT: just an MJCF file (no driver, no skeleton, no prior info)
  - OUTPUT: working Robot class + driver, validated by physics
  - MEASURED: wall clock + token cost + n LLM iterations

Output:
  auto_adapter_onboarding/
    {robot}_summary.json
    onboarding_scoreboard.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from auto_adapter.orchestrator_from_scratch import (
    FromScratchConfig,
    FromScratchOrchestrator,
)


# Robots ALREADY done in earlier sessions (don't redo, save tokens)
DONE = {"so101", "franka", "go2"}

# All 8 robots in the zoo + which MJCF to use
ROBOT_MJCF = {
    "so101":       "assets/mjcf/so101_mujoco.xml",
    "piper":       "assets/mjcf/piper/scene.xml",
    "franka":      "assets/mjcf/franka_panda/scene.xml",
    "ur5e":        "assets/mjcf/universal_robots_ur5e/scene.xml",
    "kuka_iiwa14": "assets/mjcf/kuka_iiwa_14/scene.xml",
    "go2":         "assets/mjcf/go2/go2_scene.xml",
    "unitree_a1":  "assets/mjcf/unitree_a1/scene.xml",
    "anymal_c":    "assets/mjcf/anybotics_anymal_c/scene.xml",
}


def collect_existing(robot: str) -> dict | None:
    """For robots we've already done, pull metrics from saved artifacts."""
    art = REPO_ROOT / f"auto_adapter_from_scratch_{robot}_artifacts"
    if not (art / "validate_report.json").exists():
        return None
    vrep = json.loads((art / "validate_report.json").read_text())
    # We don't have wall-clock for old runs; report from prior logs
    known_metrics = {
        "so101":  {"duration_sec": 554.5, "tokens_in": 1026272, "tokens_out": 41461,
                    "cost_usd": 3.70, "lines_of_code": 546,
                    "ik_method": "damped_least_squares"},
        "franka": {"duration_sec": 405.3, "tokens_in": 678823, "tokens_out": 29736,
                    "cost_usd": 2.48, "lines_of_code": 527,
                    "ik_method": "damped_least_squares"},
        "go2":    {"duration_sec": 616.4, "tokens_in": 1345356, "tokens_out": 44804,
                    "cost_usd": 4.71, "lines_of_code": 546,
                    "ik_method": "n/a (PD trot, no IK)"},
    }
    base = known_metrics.get(robot, {})
    return {
        "robot": robot,
        "from_cache": True,
        "structural_ok": vrep.get("structural_ok", False),
        "all_ok": vrep.get("all_ok", False),
        "n_passed": vrep.get("n_passed", 0),
        "n_total": vrep.get("n_total", 0),
        "source_metrics": vrep.get("source_metrics", {}),
        **base,
    }


def run_one(robot: str, mjcf_rel: str, out_root: Path) -> dict:
    """Run from-scratch on one robot, return summary dict."""
    mjcf_path = (REPO_ROOT / mjcf_rel).resolve()
    if not mjcf_path.exists():
        return {"robot": robot, "error": f"MJCF missing: {mjcf_path}"}

    workspace_root = Path(tempfile.mkdtemp(
        prefix=f"onboarding_{robot}_"))
    cfg = FromScratchConfig(
        robot_id=f"{robot}_onboard",
        mjcf_path=mjcf_path,
        workspace_root=workspace_root,
        bedrock_model=os.environ.get(
            "VECTOR_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        max_iters_study=22,
        max_iters_gen_algo=50,
    )
    print(f"\n=== {robot} from-scratch (workspace: {workspace_root}) ===")
    t0 = time.time()
    try:
        with FromScratchOrchestrator(cfg) as orch:
            result = orch.run()
    except Exception as e:  # noqa: BLE001
        return {"robot": robot, "error": f"{type(e).__name__}: {e}",
                "wall_clock_sec": time.time() - t0}

    summary = {
        "robot": robot,
        "from_cache": False,
        "study_ok": result.study_ok,
        "gen_ok": result.gen_ok,
        "validate_ok": result.validate_ok,
        "duration_sec": result.total_duration_sec,
        "tokens_in": result.total_tokens.get("in", 0),
        "tokens_out": result.total_tokens.get("out", 0),
        "cost_usd": (result.total_tokens.get("in", 0) * 3e-6
                     + result.total_tokens.get("out", 0) * 15e-6),
        "driver_path": str(result.driver_path) if result.driver_path else None,
        "n_passed": result.validate_report.get("n_passed", 0),
        "n_total": result.validate_report.get("n_total", 0),
        "structural_ok": result.validate_report.get("structural_ok", False),
        "source_metrics": result.validate_report.get("source_metrics", {}),
        "error": result.error,
    }
    print(f"  -> ok={summary['validate_ok']} dur={summary['duration_sec']:.1f}s "
          f"cost=${summary['cost_usd']:.2f}")

    # Save artifacts to a stable path
    import shutil
    final_dir = REPO_ROOT / f"auto_adapter_from_scratch_{robot}_artifacts"
    if (workspace_root / cfg.robot_id).exists() and not final_dir.exists():
        shutil.copytree(workspace_root / cfg.robot_id, final_dir)
        # Re-symlink mjcf.xml
        link = final_dir / "mjcf.xml"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(mjcf_path)
        print(f"  artifacts saved → {final_dir}")
    return summary


def main() -> None:
    out_root = REPO_ROOT / "auto_adapter_onboarding"
    out_root.mkdir(exist_ok=True)

    print("=" * 78)
    print("ONBOARDING-TIME BENCHMARK — 8/8 robots, from-scratch")
    print("=" * 78)
    print(f"  Already done (using cached): {sorted(DONE)}")
    print(f"  To run fresh: {sorted(set(ROBOT_MJCF) - DONE)}")

    all_results: list[dict] = []

    for robot in ROBOT_MJCF:
        if robot in DONE:
            s = collect_existing(robot)
            if s is not None:
                all_results.append(s)
                print(f"\n[{robot}] using cached: dur={s['duration_sec']:.0f}s, "
                      f"cost=${s['cost_usd']:.2f}, "
                      f"struct_ok={s['structural_ok']}")
            continue
        # Run fresh
        s = run_one(robot, ROBOT_MJCF[robot], out_root)
        all_results.append(s)

    (out_root / "scoreboard.json").write_text(json.dumps(all_results, indent=2, default=str))

    print("\n" + "=" * 78)
    print("SCOREBOARD — 8/8 onboarding")
    print("=" * 78)
    print(f"{'Robot':<14} {'time (s)':>9} {'cost ($)':>9} "
          f"{'driver ok':>10} {'lines':>6} {'IK':>10}")
    print("-" * 78)
    tot_time = tot_cost = 0.0
    n_ok = 0
    for s in all_results:
        if "error" in s and not s.get("structural_ok"):
            print(f"{s['robot']:<14} ERR  {s.get('error','?')[:60]}")
            continue
        ok = "✓" if s.get("structural_ok") else "✗"
        if s.get("structural_ok"): n_ok += 1
        sm = s.get("source_metrics", {}) or {}
        lines = sm.get("n_lines", s.get("lines_of_code", "—"))
        ik = sm.get("ik_method_hint", s.get("ik_method", "—"))
        t = s.get("duration_sec", 0)
        c = s.get("cost_usd", 0)
        tot_time += t; tot_cost += c
        print(f"{s['robot']:<14} {t:>9.1f} {c:>9.2f} {ok:>10} "
              f"{str(lines):>6} {ik:>10}")
    print("-" * 78)
    print(f"{'TOTAL':<14} {tot_time:>9.1f} {tot_cost:>9.2f} "
          f"{n_ok}/{len(all_results)} ok")
    print(f"\n  Avg per robot: {tot_time/max(len(all_results),1):.1f}s, "
          f"${tot_cost/max(len(all_results),1):.2f}")
    print(f"  vs. CaP-style human eng (~1-4 weeks per robot):")
    print(f"    @ $40/hr × 40h/wk × 2wk × 8 robots ≈ $25,600 / 16 weeks")
    print(f"    ratio: ~{25600/max(tot_cost,1):.0f}× cheaper, ~{16*7*24*3600/max(tot_time,1):.0f}× faster")
    print(f"\n  Output: {out_root / 'scoreboard.json'}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
