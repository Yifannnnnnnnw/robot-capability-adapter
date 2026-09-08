# Basic-environment diagnostic entry

This directly implemented batch replaces full reference success as a model-call
prerequisite. No separate Luna conversation was used. It implements the approved
diagnostic amendment; thesis framing and formal experiment workspaces are unchanged.

Owned files: `scripts/run_fixed_family_diagnostic.py`,
`tests/test_fixed_diagnostic_entry.py`, this note, the fixed-family README, and one
Repair summary field in `src/autoadapter2/driver_synthesis/repair.py`.

The runner first audits fixed contracts/bindings and physically loads all selected
case resets. Resets must be finite, within actuator control ranges, and free of
contact penetration deeper than 5 mm. A separate real Harness worker applies a
small native actuator command and checks that the mapped joint responds, with
canonical steps, valid controls, no direct state writes, and complete video.
Quadrupeds additionally hold their ordinary reset posture for two simulation
seconds, checking upright attitude (10 degrees) and contact integrity. These are
environment checks, not capability success. Full reference tests remain available
through `--reference-only`; normal synthesis records their result as unexecuted.

Two focused runner tests demonstrate that a passed basic check starts synthesis
without calling the full reference, and a failed basic check starts no model
client. The model doubles are confined to that explicitly named test file.

Real integration: `runs/diagnostic/fixed-family-v1-environment-smoke-20260908/`
contains Franka's ten reset checks and real worker video. All checks passed;
the commanded joint responded by 0.01910 rad. No model API was used for this check.

G5's new `actuator_force_nonzero_step_count` is included in compact Repair feedback
alongside the unchanged control-change field, so model feedback explains held
native-servo control evidence consistently with the full report.

The follow-up model cohort is the eight previously unsampled robots. Existing
SO101/Go2/Piper candidate results are preserved and are not new samples. No
candidate source, fixed pass threshold, model budget or historical run is changed.
