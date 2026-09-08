# A5 return timing correction

The return-entry deadline now starts at the end of the mandatory outbound
hold, rather than its first sample. Outbound entry is still timed from the
call start. Return entry must occur within `max_duration_per_leg_s` of the
outbound hold ending, followed by the existing return hold. This is a
trusted, observed phase boundary; it does not use candidate-reported timing.

The 15 mm position tolerance, 0.25/0.5 s holds, 80% displacement requirement,
side-effect checks, fixtures and budgets are unchanged. Stretch ST8 uses the
same helper and receives the same timing correction. No public API changes.

## Focused checks

Before changing the evaluator, the new regression produced 1 failed and
2 passed: the valid return was charged for the outbound hold. After the
change, all 4 selected timing checks passed, including genuinely late return
entry and insufficient excursion despite overlapping tolerance regions.

```sh
PYTHONPATH=autoadapter/src /Users/wangyifan/.pyenv/versions/3.11.9/bin/python3.11 -m pytest autoadapter/tests/test_b1_harness.py -q -k 'offset_return_budget or over_budget_offset_legs'
```

## Replay of existing trusted MuJoCo evidence

The current evaluator was run against the original A5 samples, public
requests and complete fixed-family measurement bindings. Original run
reports were read only and retain their original verdicts.

| Robot and case | Recorded metric | Replayed metric |
|---|---:|---:|
| Franka nominal | 1 | 1 |
| Franka boundary | 0 | 0 |
| xArm7 nominal | 0 | 1 |
| xArm7 boundary | 0 | 0 |
| UR5e nominal | 0 | 1 |
| UR5e boundary | 0 | 0 |

For xArm7 nominal, outbound hold is 0.2–0.5 s and return entry is 3.4 s:
the return interval changes from 3.2 s to 2.9 s against a 3 s budget.
For UR5e nominal, the corresponding values are 0.3–0.6 s and 3.4 s:
the interval changes from 3.1 s to 2.8 s.

Boundary maximum displacement before the scheduled return remains only
15.252 mm for Franka, 12.528 mm for xArm7 and 12.416 mm for UR5e, below the
required 16 mm. The timing correction does not make these cases pass.

Sources:

- [Franka reference](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/franka_panda/reference/reference_positive_control.json)
- [xArm7 reference](../../runs/diagnostic/fixed-family-v1-arm-controls-20260908/ufactory_xarm7/reference/reference_positive_control.json)
- [UR5e reference](../../runs/diagnostic/fixed-family-v1-gripper-reset-20260908/universal_robots_ur5e_robotiq_2f85/reference/reference_positive_control.json)

This is evaluator replay evidence, not a fresh simulation or model synthesis
result. No model API calls were made. The batch was implemented directly;
no separate Luna Max conversation was used.
