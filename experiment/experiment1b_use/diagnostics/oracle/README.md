# B2 ReCAP legacy scripted-oracle diagnostic

This directory retains the optional hidden diagnostic oracle for the ten tasks fixed by
`AA2-B2` Section 3.2. `oracle_plans.json` contains only ordered, typed
capability leaves. Each request is capability-native and crosses the exact
`driver.<capability_name>(request=<request>)` ABI; no task macro is used as a
leaf. As of `AA2-B2` revision `0.1.1`, this diagnostic is not a formal-execution
prerequisite and may be skipped.

Validate the asset against the sealed task coverage and the two B2-resolved
fixed capability designs:

```bash
python3 experiment/experiment1b_use/diagnostics/oracle/validate_oracle_plans.py
python3 -m unittest experiment/experiment1b_use/diagnostics/oracle/test_oracle_plans.py
```

Run all ten R1 canaries through the common task-aware diagnostic episode
runner, persistent worker, independent task Harness, and continuous-video
path. Reports and videos are retained below the selected output directory:

```bash
PYTHONPATH=autoadapter/src python3 \
  experiment/experiment1b_use/diagnostics/oracle/run_oracle_canary.py \
  --output experiment/experiment1b_use/runs/oracle-canary
```

The aggregate report is `oracle_canary_index.json`; each task directory keeps
its complete controller, worker, Harness, error, and video record. The runner
sets `diagnostic_cohort_passed: true` only for a complete ten-task cohort in
which every independent Harness verdict is `PASS`, physical integrity is true,
and video is complete. Oracle canaries remain optional diagnostics, set
`formal_episode: false`, and never enter the 210-episode denominator.

Use `--task TASK_ID` for a one-task video smoke. Use `--no-video` only for a
faster execution diagnostic. A subset or no-video run deliberately exits
non-zero because it does not establish the optional full-cohort diagnostic,
even if every selected task otherwise passes.

On macOS, video-required runs need a process with access to the active
CoreGraphics session. A restricted/headless process reports
`invalid CoreGraphics connection`; run the unchanged command in an authorized
graphical execution context rather than disabling the required video.
