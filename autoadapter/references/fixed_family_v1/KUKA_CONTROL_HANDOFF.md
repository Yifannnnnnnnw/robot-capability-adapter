# KUKA native position-control correction

The KUKA fixed-family reference now offsets each position command by the
canonical `data.qfrc_bias` divided by its actuator's position gain, then clips
the command to the original actuator control range. This supplies the
gravity/bias torque missing from the previous reference without changing
the model, joint target limits, actuator gains, trusted skeleton or physics.
Public guidance is included in the package's `assets/README.md` so generated
Drivers can use the same publicly observable actuator mapping.

## Verification

The two existing A1 instances reproduce the original defect in real MuJoCo:
the uncorrected reference leaves final TCP errors of 16.429 mm and 16.368 mm,
both outside the unchanged 15 mm tolerance. The focused regression failed
both cases before the correction and passed both afterwards.

```sh
PYTHONPATH=autoadapter/src /Users/wangyifan/.pyenv/versions/3.11.9/bin/python3.11 -m pytest autoadapter/tests/test_kuka_fixed_control.py -q
```

The corrected reference was then run through `run_private_suite` with the
two unchanged A1 cases selected from the fixed suite, isolated real MuJoCo
workers, the original 120 s worker timeout and video recording. Both cases
passed the capability metric, all four guards and contact integrity; both
videos are complete and decodable. Final sampled TCP errors were below
1e-12 m for these two numerical simulation runs.

- [Trusted A1 report](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/reference_a1_control.json)
- [Exact checked suite](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/checked_suite.json)
- [Submitted reference source](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/driver.py)
- [Nominal video](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/validation/videos/kuka_iiwa_14-a1-nominal-r00.mp4)
- [Boundary video](../../runs/diagnostic/fixed-family-v1-kuka-bias-20260908T105849Z/validation/videos/kuka_iiwa_14-a1-calibrated_boundary-r00.mp4)

This establishes basic reachable-target position control. A2, A4 and A5
were not rerun in this batch, so this is **2/2 A1 checks**, not a claim that
the complete eight-case reference suite passes. Original historical results
are retained. No model synthesis or API calls were made, and no separate
Luna Max conversation was used.
