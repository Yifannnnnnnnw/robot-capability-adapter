# Fixed-family diagnostic results — 2026-09-08

All 11 packages and 108 fixed cases passed loading/contract audit. Reference feasibility and model success are reported separately. These are diagnostic results only.

| Robot | Reference trials | Candidate submissions | Model calls | Result |
| --- | ---: | --- | ---: | --- |
| robotstudio_so101 | [10/10](../../runs/diagnostic/fixed-family-v1-initial-20260908/robotstudio_so101/reference/reference_positive_control.json) | generation rejected before Harness | 37 | candidate failed |
| unitree-go2-stock-12dof | [10/10](../../runs/diagnostic/fixed-family-v1-initial-20260908/unitree-go2-stock-12dof/reference/reference_positive_control.json) | 7/10 → 6/10 → 6/10 | 79 | candidate failed |
| franka_panda | [8/10](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/franka_panda/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| kinova_gen3_robotiq_2f85 | [7/10](../../runs/diagnostic/fixed-family-v1-gripper-reset-20260908/kinova_gen3_robotiq_2f85/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| ufactory_xarm7 | [6/10](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/ufactory_xarm7/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| universal_robots_ur5e_robotiq_2f85 | [6/10](../../runs/diagnostic/fixed-family-v1-gripper-reset-20260908/universal_robots_ur5e_robotiq_2f85/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| piper | [10/10](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/piper/reference/reference_positive_control.json) | 0/10 → 6/10 | 80 | candidate failed |
| kuka_iiwa_14 | [0/8](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/kuka_iiwa_14/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| google_barkour_vb | [0/10](../../runs/diagnostic/fixed-family-v1-stance-reset-20260908/google_barkour_vb/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| unitree_a1 | [0/10](../../runs/diagnostic/fixed-family-v1-stance-reset-20260908/unitree_a1/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |
| anybotics_anymal_c | [0/10](../../runs/diagnostic/fixed-family-v1-extension-20260908/anybotics_anymal_c/reference/reference_positive_control.json) | not started | 0 | reference preparation gap |

## Interpretation

- Continuous Repair was observed on: unitree-go2-stock-12dof, piper. Historical messages and latest driver revision were retained on the actual repair calls; each submission still received a fresh private Harness session.
- SO101 did not submit a valid Driver in 22 Generate turns. The final artifact failed the skeleton-assisted trusted-import boundary; its final attempted Python tool call was outside the write-only final-turn budget.
- Go2 completed three submissions (7/10 → 6/10 → 6/10). Final failures were G1 and G5. This demonstrates execution of continuous Repair, not an improvement in pass rate or token savings.
- Piper submitted 0/10, then 6/10 after continuous Repair. The final repair exhausted its 22-turn artifact budget and produced no third Harness submission.
- No API synthesis was started for robots whose full reference control failed. Reference failures must not be counted as model failures or excluded silently from the preparation checklist.
- The first extension reference run contained a reference-source initialization defect for six arms (`np` was used before import in generated constants). It was corrected, all nine reference modules were built on real models, and the six arms were rerun in `fixed-family-v1-arm-controls-20260908`. The superseded results are retained in the earlier run directory.
- Linked-gripper A3 resets were subsequently corrected using a physically settled full joint configuration; Kinova and UR5e A3 both pass. A1/Barkour stance reset heights were corrected to retain roll without initial floor interpenetration. Neither correction changed pass thresholds. Remaining calibration includes arm contact/return control and quadruped tracking, stance and dynamic contact penetration. Exact failed guards and deepest contacts are recorded in `diagnostic_results.json`; thresholds were not relaxed.

## Reproduction and handoff

See [README](README.md) for fixed standards and commands, [configuration](../../configs/diagnostics/fixed-family-v1.json), and [Luna handoff](LUNA_HANDOFF.md) for the bounded Franka reference follow-up. This batch was implemented directly; no separate Luna conversation ran.

Checks: 12 focused checks passed, including all 11 package contracts / 108 cases and real MuJoCo native-position skeleton smoke checks. Earlier timeout/fixed-input checks also passed. Implementation commits: `62019e3`, `66ab110`, `0c14a3b`, `758455f`, `09de700`. Native-actuation follow-up: 7 focused checks passed. Reset calibration follow-up: 4 focused checks passed. Raw per-trial videos, model calls, source revisions and feedback remain under the linked run directories.
