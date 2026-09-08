# Quadruped environment batch — 2026-09-08

This batch implements the approved diagnostic environment corrections for Unitree A1, Barkour vB and ANYmal-C. It was implemented in the current Codex collaboration; no separate Luna Max conversation was used. These are environment/reference checks, not model synthesis results.

## Owned changes and boundaries

Owned robot packages: `libraries/robots/{unitree_a1,google_barkour_vb,anybotics_anymal_c}/1.0.0/`. Owned fixed inputs: the corresponding three directories under this document's directory. Focused check: `tests/test_fixed_quadruped_environment.py`.

Each package has `assets/fixed_scene.xml` and `assets/fixed_robot.xml`. The original source XML, licenses and recorded source revision remain intact. The robot XML copy changes only the four foot collision geoms' `solimp` to `0.9 0.95 0.001 0.5 2`. Public morphology and all private fixed instances select the same diagnostic scene. No mass, inertia, friction, actuator gains, control limits or policy weights are changed.

The original `home` keyframe, its joint targets and controls remain intact. This includes Barkour's policy action center. A separate `fixed_settled` keyframe contains full qpos after 3 seconds of real native home-target control, starting with the lowest actual foot 0.5 mm above the floor; its qvel is zero and ctrl remains the original home ctrl. Ordinary instances select that keyframe. G5 applies the original 5°/8° roll to the settled orientation and raises only the base enough to restore 0.5 mm minimum foot-floor clearance. Full calibration state and preparation measurements are recorded in each package's `capability_validation/private/fixed_environment_calibration.json`.

Public height metadata, the reference's CONFIG and fixed robot bindings use measured settled heights. G4 bounds remain 80–120% and requests remain 94%/108% of that baseline. Absolute acceptance thresholds are unchanged. No new gait or policy was trained. Shared Harness, skeleton and diagnostic runner changes belong to other batches.

## Checks and evidence

Run from the repository root:

```sh
PYTHONPATH=autoadapter/src /Users/wangyifan/.pyenv/versions/3.11.9/bin/python3.11 -m pytest -q autoadapter/tests/test_fixed_quadruped_environment.py
```

Result: **3 passed**. Each parameterized check loads real MuJoCo models, confirms unchanged original home/controls and physical properties, reproduces more than 5 mm dynamic contact penetration with the original scene, checks the fixed scene's ordinary standing for 2 seconds, checks fixed interface/instance contracts, and verifies both tilted resets retain their disturbances without initial deep contact.

Nine additional isolated trusted-worker executions completed with video: three ordinary 2-second stance checks and six actual fixed G5 cases. Evidence root: [fixed-family-v1-quad-environment-20260908](../../runs/diagnostic/fixed-family-v1-quad-environment-20260908).

| Robot | Settled height (m) | Ordinary stance deepest contact (mm) | G5 ordinary/boundary | G5 deepest contact (mm) |
|---|---:|---:|---|---|
| Unitree A1 | 0.260802876 | 0.182 | Pass / Pass | 1.951 / 1.680 |
| Barkour vB | 0.256369563 | 0.622 | Fail / Fail | 6.719 / 4.381 |
| ANYmal-C | 0.373747381 | 0.986 | Fail / Fail | 11.946 / 5.499 |

Per robot, `ordinary_standing_worker.json` and `ordinary-standing.mp4` record the basic stance check; `environment_g5_report.json` and `videos/` record the two fixed G5 cases. All workers completed without candidate exceptions, and all nine videos are complete. All six G5 trials retained `ctrl_changed_from_reset=false` but recorded nonzero physical actuator force on every step; the fixed-control guard passed with the separately implemented G5 rule.

## Remaining limits and review handoff

All three ordinary environments now support native control and standing without the previous deep-contact preparation defect. Barkour and ANYmal-C still fail the full disturbed-stance criterion with the unchanged reference controller; some disturbed cases still exceed the 5 mm dynamic penetration threshold. These are retained failures, and basic environment readiness does not require G1–G5 reference success. No model API was called by this batch.

For a separate Luna Max review, own only the paths listed above. Check the focused diff, run the stated three-case test, inspect the nine worker reports/videos, and return changed files (if any), run evidence, remaining failures and commit ID. Do not edit shared Harness/runner/skeleton code, other robots, paper claims, fixed numerical acceptance tolerances or model budgets. The next model runs must use these fixed inputs before synthesis; do not retune them after a candidate failure.
