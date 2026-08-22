# B2 ReCAP scripted-oracle canary

This directory contains the hidden diagnostic oracle for the ten tasks fixed by
`AA2-B2` Section 3.2. `oracle_plans.json` contains only ordered, typed
capability leaves. Each request is capability-native and crosses the exact
`driver.<capability_name>(request=<request>)` ABI; no task macro is used as a
leaf.

Validate the asset against the sealed task coverage and the two B2-resolved
fixed capability designs:

```bash
python3 experiment/b2_recap/oracle_canary/validate_oracle_plans.py
python3 -m unittest experiment/b2_recap/oracle_canary/test_oracle_plans.py
```

Run a diagnostic R1 pass through the current persistent worker and independent
task Harness with video deliberately disabled:

```bash
python3 experiment/b2_recap/oracle_canary/run_oracle_canary.py
```

Use `--task TASK_ID` for one task. The runner always reports
`formal_oracle_prerequisite_cleared: false`: a no-video diagnostic cannot meet
the Authority's complete-video requirement, even if its task metric and
physical integrity pass. The plans remain provisional until all ten are run,
physically tuned where necessary, and pass the independent Harness with
continuous video.

`diagnostic_status.json` records the latest attempted diagnostic. In the
current environment all ten tasks remained unexecuted because MuJoCo was not
installed; this is an infrastructure limitation, not task success or failure.
The B2 formal-execution blocker is therefore still active.
