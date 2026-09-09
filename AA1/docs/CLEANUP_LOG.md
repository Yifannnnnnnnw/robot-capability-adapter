# Repo cleanup log (pre-GitHub, 2026-06)

Safety protocol: pure junk is deleted; everything else is MOVED to
`archive/_superseded/` (reversible). Every action verified (paper compiles,
modules import) before proceeding. Nothing canonical/paper/eval-referenced is touched.

## Step 1 — pure junk (deleted, regenerable)
- 45 `__pycache__/` dirs, 128 `*.pyc`, 13 `.DS_Store` — deleted. Imports re-verified OK.
- 5 zero-byte files inspected: all `__init__.py` (package markers, load-bearing) — KEPT, not deleted.

## Step 2 — structural (artifacts version sprawl, scripts, naming): PENDING Codex plan.

## 2026-06-07 — cleanup resumed (handoff prep, mid grasp-investigation)
SAFE junk swept (excluding active artifacts/from_scratch_xmodel/): 18 __pycache__,
34 *.pyc, 1 MUJOCO_LOG.TXT removed; imports verified OK. .DS_Store: 0.
- HELD: structural reorg of artifacts/ + scripts/ until (a) the reliability batch
  finishes writing to artifacts/from_scratch_xmodel/, and (b) Codex's cleanup plan
  lands, and (c) the Self-column paper-integrity issue is resolved (which determines
  which artifacts are "stale" vs "the new canonical evidence").
- Wrote FINDINGS_2026-06-07_grasp_verification.md — critical handoff knowledge.
- Codex cleanup-review running (read-only plan).
- RULE reaffirmed: archive (archive/_superseded/), never hard-delete; broken
  self drivers are EVIDENCE.

## 2026-06-07 (later) — stale + test-scratch cleanup (all REVERSIBLE moves)
Archived to archive/_superseded/session_2026-06-07/ :
- scripts: grasp_repair_probe.py, verify_grader_fixes.py, regrade_object_lifted.py
  (one-off session scratch; kept preflight_regrade_all.py as a regression test)
- artifacts (0 live refs, verified): auto_adapter_so101_push_v2_artifacts (197M),
  auto_adapter_{franka,piper}_push_from_scratch_artifacts, from_scratch_xmodel_v1_noperception
  -> PUSH suite is NOT in the paper (no push graders in any results JSON); archived
     (NOT deleted) because pending task #81 may resume push work — restore from archive if so.
Deleted (throwaway): /tmp/smoke, /tmp/vidfill.
KEPT (verified still needed): all §4 onboarding artifacts, auto_adapter_so101_v2
(A-A/CaP), from_scratch_xmodel (Self + gt* reliability + A1), auto_adapter_real_so101
(Track B, used by real_robot/synth_real_driver.py), all canonical-referenced dirs.
Result: artifacts/ 782M -> 562M. imports OK, 0 live references broken.

## 2026-06-07 (later still) — scripts/run cleanup
Archived 16 one-off operational scripts to archive/_superseded/session_2026-06-07/scripts/:
probes (repair_probe, synth_xmodel_probe), abandoned fill_missing_videos.sh,
rerun_* / resume_* / fix_errors (one-off reruns), push runners (finish_cap_push,
push_finish), phase runners (phase2_cap_opus, phase3_openmodels), and operational
one-offs (go2_downstream, quad_a1_anymal, bridge_menagerie_ws, eval_diagonal_b).
KEPT canonical runners: synth_xmodel_one.py (A1), synth_xmodel_sweep.sh,
replay_self_videos.py, synth_quad_v2/menagerie/drone/humanoid, eval_aerial/quad,
rerender_*, + this session's grasp_reliability_batch*.sh + preflight_regrade_all.py.
scripts/run/: 38 -> 22 files. All moves reversible; imports OK.

## 2026-06-08 — session-script archive + doc consolidation (REVERSIBLE)
- Archived 4 confirmed-idle arm-era scripts to .../session_2026-06-07/scripts/:
  autonomous_overnight.py, grasp_reliability_batch{,2}.sh, grasp_fullrange_sweep.sh
  (verified NOT in pgrep before moving). 4 active xrobot scripts untouched.
- Doc consolidation (root .md 9 -> 7): archived STALE STATE.md (2026-06-01) +
  PROJECT_STATUS.md to .../session_2026-06-07/docs/ (both pre numpy-bool & pre
  grasp-grader fix; numbers wrong). Rescued STATE.md's unique canonical-pose
  mis-diagnosis lesson into FINDINGS (Appendix). Updated ONBOARDING leaderboard
  to canonical exp11 numbers. Fixed 3 README links (STATE.md -> HANDOFF/RESULTS_MASTER).
  Live root docs: README, ONBOARDING, HANDOFF, RESULTS_MASTER, FINDINGS, HF_DATASET, this log.
- FLAGGED (needs sign-off): canonical exp11.writes_api (Self column) STILL holds
  pre-fix inflated numbers; honest grasp-verified truth recorded in RESULTS_MASTER
  + leaderboard_b_graspverified/SUMMARY.md. Not edited (canonical = single source).
- scripts/run reorg DONE (partial, safe subset): archived 8 to .../scripts/ —
  superseded versions (synth_humanoid_h1{,_v2,_v3}, synth_drone_aerial v1) +
  done-session (clean_sweep.py, fewshot_cap_full.sh, synth_xmodel_sweep.sh) +
  paper-not-claimed (synth_stretch_wheeled.py). Reference graph checked first
  (no active-proc / doc / canonical refs). scripts/run/: 24 -> 16. Added
  scripts/run/README.md index. Kept: 4 active xrobot scripts, canonical synth/eval
  runners (latest version each), video tools (HANDOFF reproduce), grasp tools.
  HELD until DONE: the 4 active xrobot scripts (live sweep references them).
- Added autoadapter_bench/results/README.md refresh (graspverified/xrobot/superseded).
