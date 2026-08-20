# Benchmark Runners

Runners are thin entry points over `autoadapter2`; they do not implement robot
control or a second Harness.

```text
run_b1.py   # fixed interface -> STUDY -> GENERATE/GEN_ALGO -> validation -> Repair
run_b2.py   # published controller -> Capability Router -> fixed reference driver
```

Both runners load the frozen config, preserve failed and blocked cells, record
model usage and wall time, and write one result per experimental unit. B2 grades
the actual formal rollout and never substitutes call-log replay for execution.
