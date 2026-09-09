# scripts/run/ — index (2026-06-09)

Reusable runners for reproducing the paper. One-off / superseded / completed
scripts are archived under `archive/_superseded/session_2026-06-07/scripts/`
(reversible).

## Synthesis runners (from-scratch driver synthesis, per morphology)
| script | robot / paper section |
|---|---|
| `synth_menagerie_so101.py` | SO-101 arm |
| `synth_quad_v2.py` | Go2 / ANYmal quadruped (exp9, §5) |
| `synth_drone_aerial_v2.py` | Skydio X2 quadrotor (App. J) — v2 canonical |
| `synth_humanoid_h1_v4.py` | Unitree H1 humanoid (App. J) — v4 canonical |
| `synth_xmodel_one.py` | arm, single model from scratch (Self leaderboard; FINDINGS) |
| `synth_xmodel_robot.py` | **generic** single-(model, robot) from-scratch runner; drove the cross-morphology study (App. J Tables) — usable for any robot/model |

## Eval runners
| script | role |
|---|---|
| `eval_aerial.sh` | aerial A-A + CaP eval (HANDOFF reproduce entry) |
| `eval_quad_v2.sh` | quadruped locomotion eval |
| `preflight_regrade_all.py` | A-A zero-regression re-grade after the grasp-grader fixes (FINDINGS) |

## Archived (completed / one-off — in archive/_superseded/.../scripts/)
- Cross-morphology sweep orchestration (DONE 2026-06-09): `autonomous_xrobot.py`
  (engine), `grasp_xrobot_sweep.sh` (sweep), `xrobot_reaper.sh` (watchdog).
  Results in `autoadapter_bench/results/xrobot_selfsynth/` (SUMMARY + DONE).
- Video re-render tools (`rerender_*_videos.py`, `replay_self_videos.py`),
  superseded synth versions (`synth_humanoid_h1` v1–v3, `synth_drone_aerial` v1),
  done-session sweeps. Restore from archive if needed.

Reproducible stats: `autoadapter_bench/stats.py` (significance numbers in App. I).
