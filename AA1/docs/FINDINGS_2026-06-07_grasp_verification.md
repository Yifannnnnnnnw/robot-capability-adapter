# Findings — grasp verification gap in self-contained synthesis (2026-06-07)

**Status: RESOLVED (2026-06-09) — Self column re-graded and integrated into the paper; see RESULTS_MASTER.md.**
This document is the audit trail for a bug found while QA-ing the Self-experiment
videos. Read this before touching `leaderboard_b` / the "Self" results.

---

## 1. What was found

The **self-contained ("writes its own driver") synthesis path** produces drivers
whose **grasp is broken**: on `gripper_close` the grasped object is **flung ~35 cm
away from the gripper** (and in some scenes the whole tabletop scatters), instead
of being held. Verified by direct, render-free physics replay:

| | object→gripper gap after lift | scene disturbance |
|---|---|---|
| A-A / CaP (spec mode, our skeleton) | **1–2 cm** (real grasp) | 10 cm (target only) |
| Self (model's own driver) | **35 cm** (flung) | up to 160 cm (whole scene) |

Root cause: the self-authored weld grasp toggles `eq_active` but **never sets
`eq_data`** (the weld's relative pose). The correct logic lives in our framework
(`auto_adapter/skeletons/grasp_backends.py:157-172`) which spec-mode drivers
import; the self-contained drivers reimplement grasp from scratch and omit it.
All 5 original self drivers (`artifacts/from_scratch_xmodel/so101_{sonnet46,opus48,
haiku45,deepseek,qwen32}`) have `eq_data` count = 0.

## 2. Why nothing caught it (the real root cause)

There are **two orchestrators with different verification coverage**:

- `orchestrator.py` (**spec mode**, §5 A-A/CaP): GEN prompt self-tests grasp
  (line ~291) AND the Validate harness has a `grasp_lift` test (line ~905).
- `orchestrator_from_scratch.py` (**self-contained**, Self column + §4 onboarding):
  had **NO grasp test**. Validate ran `home, ik_roundtrip, gripper_cycle,
  object_perception` — `gripper_cycle` only opens/closes the *empty* jaw; nothing
  ever grasps an object. So broken-grasp drivers passed (`all_ok=True`).

Secondary: the eval grader `object_lifted_by` (`autoadapter_bench/eval.py`) checks
only `dz > 0.05m` (height), **not** gripper proximity — so a flung object that
rises ~20 cm scores a **false PASS**. This inflated the Self column on the
`object_lifted_by` tasks (`conditional_pick`, `spatial_query_pick`).

## 3. Capability vs coverage — the experiment

Added a real `grasp_lift` test (object must end <10 cm from the EE) to
`orchestrator_from_scratch.py` as a CRITICAL validate test, then re-synthesized
with the **same models** (distinct workspaces `so101_*_gt*`, originals untouched):

| model | grasp_lift pass | mechanism |
|---|---|---|
| **opus48** | **3/3** | first-gen broken → VAL→GEN **repair fixed it** (1 round each) |
| **sonnet46** | **1/4** | 1 lucky first-try; repair ran 2 full rounds (922s, 77k tok) and **still couldn't add `eq_data`** |
| haiku45 | (running) | |
| deepseek | (running) | |

**Conclusion:** it is BOTH (a) a coverage gap — the pipeline never tested grasp,
so broken drivers shipped — AND (b) a real, model-dependent capability limit:
given explicit physics feedback ("object flung 35 cm"), opus48 reliably repairs
its grasp, sonnet46 mostly cannot. The verify→repair mechanism works; its success
depends on the model. This is a *stronger* version of the paper's thesis.

## 4. What is / isn't affected

- **NOT affected (clean, no re-run):** A-A, CaP, CaP_fs (Tables 3/4/5 main
  columns — spec mode, our skeleton grasps correctly); quad/aerial/humanoid (no
  grasp); RL/VLA/DP baselines.
- **Affected:** the **"Self" column** in Table 4 / `results/leaderboard_b/diag_*.json`.
- **To re-check:** §4 onboarding's 5 arms (so101/piper/ur5e/franka/kuka) were
  self-contained + validated grasp-blind → re-validate with the new `grasp_lift`
  (validate only, no re-synthesis) to see which actually grasp.

## 5. Decisions made

- **Keep all broken artifacts as EVIDENCE** (archive under `archive/_superseded/`,
  never delete). They are the data behind the finding.
- Do NOT ship the explosion videos in the public showcase; do NOT report the
  inflated Self numbers.

## 6. PENDING work (for whoever picks this up)

1. **Grader fix (cheap, necessary):** add a proximity guard to `object_lifted_by`
   in `eval.py`; re-grade ALL methods' saved traces uniformly (A-A unchanged,
   Self false-passes flip). Produce a before/after shift table.
2. **Self column — decide:** (a) re-grade old broken drivers (honest low numbers),
   or (b) re-synthesize all 4 with the grasp-verified pipeline + re-run eval
   (fair "writes API" numbers), or (c) report both as a "verification matters"
   ablation. Reliability batch (`scripts/run/grasp_reliability_batch*.sh`) informs
   feasibility of (b).
3. **Paper:** rewrite the Self discussion honestly; disclose the grader fix;
   optionally fold in the verify→repair reliability result.
4. **Onboarding arms:** re-validate with `grasp_lift`.

## 7. Code changes already made (this session)

- `orchestrator_from_scratch.py`: added critical `grasp_lift` validate test
  (proximity-aware) + added `bedrock-agentcore` client read/connect timeouts
  (was hanging forever on dead sockets) + `AA_VERBOSE` tracing.
- `react_loop.py`, `agent/tools.py`: `AA_VERBOSE` per-iter / per-CI-call tracing.
- Probe scripts: `scripts/run/grasp_repair_probe.py`,
  `grasp_reliability_batch{,2}.sh`.

Verbose runs: `AA_VERBOSE=1 python scripts/run/synth_xmodel_one.py <short> <model_id> <workspace_id>`

---

## RESOLUTION (2026-06-07, later) — integrity sweep complete

After the grasp bug we audited the WHOLE grader/validation surface for the same
class of bug (a check satisfiable by degenerate physics without doing the task).
**10 fixes, each verified; A-A main results provably unchanged.**

| # | problem | file | fix |
|---|---|---|---|
| 1 | self-contained validate never tested grasp | orchestrator_from_scratch.py | added critical `grasp_lift` |
| 2 | `object_lifted_by` height-only | eval.py | +held-at-gripper (<12cm) |
| 3 | `object_above_object` unbounded gap | eval.py | +max_dz (0.15) |
| 4 | `object_close_to_object_xy` no z | eval.py | +z check (<10cm) |
| 5 | `object_placed_near_target` no height | eval.py | +rise check (<8cm) |
| 6 | `objects_in_tower` unbounded gap | eval.py | +max_dz |
| 7 | humanoid trusts driver self-reported height | eval.py | bounded to sim z |
| 8 | empty get_object_names() bypasses validation | orchestrator_from_scratch.py | ground-truth scene check (`_scene_movable_bodies`) |
| 9 | spec-mode validate `grasp_lift` height-only | orchestrator.py | +held check |
| 10 | AgentCore CI no timeout (hangs forever) | orchestrator_from_scratch.py + react_loop.py + tools.py | read/connect timeout + AA_VERBOSE |

VERIFIED SAFE (no fix needed): replay arg-forwarding (all arg tools handled;
`land` no-arg+unused), `ee_at_midpoint`/`ee_waypoint_trace` (sim-truth point),
locomotion validate (outcome-checked), aerial/humanoid graders (upright+excursion).

PRE-FLIGHT (scripts/run/preflight_regrade_all.py): re-graded A-A on ALL 7 models
× 6 changed-grader tasks (42/42 cells) with the real evaluate_success — OLD==NEW
on every cell, ZERO regression. Self false-passes correctly flip to fail.

CROSS-MODEL grasp-synthesis reliability (verified pipeline, grasp_lift in loop):
opus48 3/3 (repair fixes it), haiku45 2/3 (fragile), sonnet46 1/4, deepseek 0/3.
=> verify→repair WORKS but capability is model-dependent.

DONE (2026-06-07): A1 re-ran the Self 13-task eval with the verified drivers
under one consistent protocol + fixed graders. Result in
`autoadapter_bench/results/leaderboard_b_graspverified/SUMMARY.md`:
opus 0.46, haiku 0.46, deepseek 0.32; sonnet/nova/ministral/qwen = synth FAILED.
NOTE: `canonical.yaml::exp11.writes_api` still holds the pre-fix inflated
numbers (sonnet pass/0.46, opus 0.52, haiku 0.38, deepseek 0.28) — replace on
paper integration; see `RESULTS_MASTER.md`.

---

## Appendix — an EARLIER integrity catch (canonical-pose mis-diagnosis, 2026-06-01)

[Archived from the now-retired STATE.md, kept because the lesson generalizes —
it is the same lesson the grasp bug taught: *too clean = investigate the eval.*]

A "LLM canonical-pose memorization" finding worked up across 9 LLMs, 5 prompt
ablations, and 3 robots was consistent and reproducible — but ultimately an
artifact of comparing the agent's output against a target derived from a
DIFFERENT initial qpos than the agent observed (`home()_ee + offset` vs
`MJCF-default-qpos_ee + offset`). It looked clean because:
- 4 Anthropic models emitted byte-identical "wrong" targets (they were the
  CORRECT targets from MJCF-default qpos).
- Every prompt-engineering intervention "failed" (they all produced the same
  correct output that disagreed with the wrong target).
- Multiple 7-DoF robots all "failed" (all had large home/fresh qpos diffs, so
  the error was bigger on them).
- The exact value (0.138, 0, 0.926) "looked like" a Franka canonical pose
  (it IS Franka's MJCF-default forward kinematics, by definition).

Retracted before publication. Lesson: when a finding looks too clean — too many
independent mechanisms agreeing — that is evidence to investigate the eval
setup, not evidence the finding is real.
