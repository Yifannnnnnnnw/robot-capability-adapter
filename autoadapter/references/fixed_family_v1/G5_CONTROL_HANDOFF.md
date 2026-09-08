# G5 constant native-servo control evidence

Implemented directly in this session; Luna Max was not used. This is a diagnostic
Harness correction, with no new model synthesis or formal experiment claim.

The old control guard required `ctrl` to differ from the Framework reset value.
A position actuator can physically restore and hold posture with the same target,
so that rule rejected valid native-servo actuation.

`TrackedMuJoCoSession` now records `actuator_force_nonzero_step_count` after each
original MuJoCo physics step. A step counts when every actuator force is finite
and at least one has magnitude above `1e-12`. Reset, forward-only, and terminal
observations do not increment the count.

The parent Harness enables the alternative only for the trusted
`go2_stable_stance_recovery` and `quadruped_stable_stance_recovery` operators.
Their control guard still requires observed controls and actual physics steps;
it then accepts either a changed target or nonzero applied actuator force.
Other operators retain the changed-target requirement. Recovery, hold duration,
drift, support, forbidden contacts, control ranges, direct-state-write detection,
video requirements, and the global 5 mm penetration check remain unchanged.

This evidence establishes physical actuation, not the number of command writes.
As explicitly accepted for this diagnostic, stepping an inherited native-servo
target can count when it produces actual force and meets all capability checks.
An inert zero-actuation simulation or a method that never steps still fails.
Historical reports lack the new field and are not retroactively marked passed.

## Focused evidence

- `tests/test_g5_held_control.py`: **6 passed**. A real position-servo fixture
  moves a joint toward its constant target while the old/default guard rejects
  it and the G5 alternative accepts it. Passive zero-force and no-step fixtures
  remain rejected.
- The same test file runs three real MuJoCo worker subprocesses through
  `run_private_suite`: both trusted G5 operators accept the held control, while
  G4 retains the previous rule. The short G5 trajectories still fail their
  required physical hold duration and final verdict.
- Existing `tests/test_harness_worker.py`: **7 passed**, including canonical
  sessions, direct state writes, and out-of-range control checks.

Commands (from repository root, Python 3.11.9):

```sh
PYTHONPATH=autoadapter/src /Users/wangyifan/.pyenv/versions/3.11.9/bin/python3.11 -m pytest -q autoadapter/tests/test_g5_held_control.py
PYTHONPATH=autoadapter/src /Users/wangyifan/.pyenv/versions/3.11.9/bin/python3.11 -m pytest -q autoadapter/tests/test_harness_worker.py
```

Owned changes: `harness/session.py`, `harness/measurements.py`,
`harness/runner.py`, the new focused test, and this handoff. Robot assets,
reference controllers, policy weights, fixed thresholds, and A5 were not edited
by this batch.
