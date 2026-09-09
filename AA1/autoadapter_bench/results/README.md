# AutoAdapter-Bench — results layout

Every number in the paper traces to `paper/results/canonical.yaml`; the JSONs
here are its sources. **Two orthogonal axes** (not a full robot×model cube —
that would be prohibitively expensive, esp. Opus):

- **Model axis** = **SO-101 × 7 LLMs** (the only model-swept robot).
- **Robot axis** = **fixed Sonnet × many robots/morphologies** (each robot run with one model).

## Directories (current / paper)
| Dir | What | Axis |
|---|---|---|
| `clean/` | SO-101 `ours_*`/`cap_*` per model (13-task, N=5, **all 7 models** — fuller than the loose `*_n5`) + push-suite runs (⚠️ push NOT in paper) | model |
| `fewshot/` | SO-101 CaP few-shot per model | model |
| `leaderboard_b_graspverified/` | **Self (honest, grasp-verified)**: opus .46, haiku .46, deepseek .32; sonnet/nova/min/qwen synth-FAILED. ⏳ replaces canonical `exp11.writes_api` (needs sign-off) | model |
| `leaderboard_b/` | 🚫 **OLD Self** (pre grasp-grader fix — inflated) — SUPERSEDED, audit-only, do not use | model |
| `xrobot_selfsynth/` | 🏃 cross-morphology Self (go2/skydio/h1 × 7 models) — RUNNING | both |
| `quadruped_v2/` | Go2 / ANYmal, Sonnet 4.5 (+4.6 ablation), 10-task locomotion | robot |
| `aerial/` | Skydio X2 quadrotor, ours+CaP, 8-task | robot |
| `humanoid/` | Unitree H1 (onboarding-level; videos) | robot |
| `arm_extra/` | KUKA showcase video | robot |
| `*_n5.json` (loose) | SO-101 hard N=5 (3 models, Jun 2) — ⛔ canonical-cited, DO NOT MOVE. NOTE: `clean/` holds the fuller 7-model superset (Jun 5) | model |
| `LEADERBOARD.json` | machine-readable summary of all of the above | — |
| `videos/` | **curated one-video-per-cell matrix** (see `videos/README.md`) | both |

## `_superseded/`  ← ignore this
Old/stale runs kept only for provenance, **not used by the paper**:
`phase1/2/3` (early sweeps), `quadruped/` (v1, replaced by `quadruped_v2/`),
`_prefix_bugfix_backup/` (pre-bugfix JSON backups), and assorted loose
spike/smoke JSONs. Safe to delete; excluded from the GitHub release.

## Naming note (the "v1/v2" confusion)
`quadruped/` = **v1** (early cross-model exploration, superseded) → moved to
`_superseded/`. `quadruped_v2/` = **the paper's** quad data (strict locomotion
validator, Sonnet 4.5). When in doubt, the paper uses `*_v2` / the dirs above.
