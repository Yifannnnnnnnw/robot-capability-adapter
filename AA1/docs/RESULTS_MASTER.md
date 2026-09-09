# RESULTS MASTER INDEX (2026-06-08)

Single source of navigation for the Auto-Adapter / AutoAdapter-Bench results.
Read this first. EMNLP deadline 2026-06-16.

---

## TL;DR story
Using a validated driver is **easy** (all 7 LLMs operate one well); synthesizing a
**correct** low-level driver from scratch is **hard, model-dependent, and
morphology-dependent**. → The contribution is the *validated-synthesis pipeline*
(study → generate → **physics-validate** → export), not raw LLM code-writing.

## The 3 methods (who writes the driver)
- **A-A** = agent fills a spec, runtime builds from our *validated* skeleton. (core)
- **CaP** = same skeleton, single-shot baseline.
- **Self** = model writes the *entire* driver from scratch (the ablation; this is
  what the whole grasp investigation was about).

---

## RESULTS — what / where / status

### 1. CORE (paper headline) — CLEAN, untouched by the bug
- A-A vs CaP vs CaP_fs, SO-101, 7 models (Tables 3/4/5): A-A 0.57–0.69, best 6/7.
- 8-robot onboarding scoreboard (§4).
- Morphology breadth, single-model Sonnet (App J): aerial, humanoid.
- RL / VLA / DP baselines.
- Source: `paper/results/canonical.yaml`, `autoadapter_bench/results/leaderboard_b/` (A-A/CaP cells), `results/clean/`.
- **Verified zero regression after the grader fixes (42/42 A-A cells unchanged).**

### 2. THE GRASP BUG + INTEGRITY FIXES — DONE
- Self-contained drivers had a broken weld grasp (objects flung); grader
  `object_lifted_by` was height-only (false passes); validation never tested grasp.
- 10 grader/validation holes fixed in `eval.py` + `orchestrator*.py`; each verified.
- Full audit trail: `FINDINGS_2026-06-07_grasp_verification.md`.

### 3. ARM "Self", grasp-verified (honest) — DONE
- Dir: `autoadapter_bench/results/leaderboard_b_graspverified/` (SUMMARY.md + diag_*.json)
- Synthesis reliability (can the model produce a validated, non-hardcoded driver?):
  opus48 2/2, haiku45 1/2, deepseek 1/2, sonnet46 0/2, novapro/ministral8b/qwen32 0 (some HARDCODED → rejected).
- Task success (A1, 13 tasks, N=5, fixed graders): **opus48 0.46, haiku45 0.46,
  deepseek 0.32**; sonnet/nova/ministral/qwen = synthesis failed.

### 4. CROSS-MODEL × MORPHOLOGY "Self" — DONE (2026-06-09)
- Dir: `autoadapter_bench/results/xrobot_selfsynth/` (SUMMARY.md on completion)
- Phase 1 synthesis reliability: {go2, skydio_x2, h1} × 7 models × 2.
  - go2 (quad) so far: opus 2/2, sonnet 2/2, haiku 1/2, rest fail.
  - **Key nuance: locomotion is easier to self-synthesize than grasp** (sonnet
    2/2 gait vs 0/2 grasp) → difficulty is morphology-dependent.
- Phase 2 task-eval: queued (CANDIDATE TO CUT — marginal value, ~15h compute).
- Engine: `scripts/run/autonomous_xrobot.py`; sweep `grasp_xrobot_sweep.sh`;
  OS reaper `xrobot_reaper.sh`; supervised by 2 cron backstops.

### 5. REPO CLEANUP — DONE
- ~220 MB stale artifacts + 19 one-off scripts archived to
  `archive/_superseded/session_2026-06-07/` (reversible). `CLEANUP_LOG.md`.

---

## What's clean vs what needs PAPER integration (the real to-do)
- ✅ Core results (A-A / CaP `uses_api`): final, in the paper, match canonical.
- ✅ **Self column — INTEGRATED (2026-06-09).** Grasp-verified truth now in
  canonical `exp11.writes_api`, Table 4, appendix-I, §5, abstract, Summary:
  `{opus 0.46, haiku 0.46, deepseek 0.28 (full 13-task rerun); sonnet/nova/
  ministral/qwen = synth FAILED}`; "four"→"three" throughout; grasp held-check
  disclosed in appendix-I. Paper recompiled 6pp / 0-undef.
- ✅ **Cross-morphology appendix — ADDED.** `appendix_j` now has 2 tables
  (synthesis reliability + task success, 7 models × {go2,skydio,h1}) +
  morphology-gradient narrative. Source: `xrobot_selfsynth/SUMMARY.md` (DONE).
- ✅ **Statistical significance — ADDED.** appendix-I: task-level Wilcoxon
  p<0.01 + per-cell Wilson CI; `autoadapter_bench/stats.py` reproduces it.
- ⏳ §4 onboarding arms: re-validate with the new grasp test (quick) — optional.
- 🚫 Do NOT use `results/leaderboard_b/` Self numbers (superseded/inflated).

## Open decisions
1. Cut Phase-2 task-eval for non-gripper? (recommended: yes — marginal, expensive.)
2. How to present Self in the paper: honest re-graded numbers (have them) + the
   morphology-reliability nuance.

## Pointers
- Bug/fix detail: `FINDINGS_2026-06-07_grasp_verification.md`
- Cleanup log: `CLEANUP_LOG.md`
- Live supervision trail: `logs/overnight_backstop.log`
