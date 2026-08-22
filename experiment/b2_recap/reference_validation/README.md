# B2 reference-driver validation

This directory resolves the two B2 reference-driver positive-control suites
from the Experiment 1 fixed capability bundles. It does not modify the
Experiment 1 inputs.

The committed `resolved/` JSON files are the fixed B2 snapshots. They were
copied mechanically from the Experiment 1 capability designs and suites; they
do not silently follow later Experiment 1 edits. The Go2 snapshot is unchanged
from its selected source. The SO-101 snapshot applies the bounded B2
calibration corrections below:

Each resolved suite has its own `b2-fixed-reference-suite::<robot>::v1`
identity and retains the originating Experiment 1 `source_suite_id`; a
corrected B2 snapshot never reuses its source suite identity.
Both snapshots name the active
`experiment1-b1-driver-validation-criteria-v2` pass standard used by the
trusted Harness.

- `A3-H3` starts the gripper at the canonical open limit `1.7453292` in both
  reset maps.
- Every `A3` case fixes a three-call validation sequence that crosses at least
  half the declared aperture in both directions and ends at the case's public
  request target. Its sequence-level simulation timeout covers all three
  individually bounded calls.
- Each `A4` binding uses the explicit calibrated distal-geom whitelist,
  excludes `geom_37` and the proximal jaw boxes, and sets
  `precontact_gate="held_window_then_ray"`.

Validate the committed fixed inputs:

```bash
python experiment/b2_recap/reference_validation/build_suites.py
```

An intentional reviewed update may explicitly replace the snapshots only when
the selected Experiment 1 source identities still match the recorded v1
provenance:

```bash
python experiment/b2_recap/reference_validation/build_suites.py \
  --refresh-from-experiment1
```

Normal calibration never performs this refresh.
If Experiment 1 has advanced, the command refuses to write; updating B2 then
requires a prospective B2 snapshot revision instead of silently inheriting the
new source.

Run both fixed reference drivers through the B2 typed adapter and persistent
worker on the real Direct-MuJoCo path. Each case gets one fresh physical
session; A3's three requests share that session. Video recording is enabled by
default, and reports are written beneath the required output directory:

```bash
python experiment/b2_recap/reference_validation/run_reference_calibration.py \
  --output experiment/b2_recap/runs/reference-calibration
```

For a diagnostic run only, select one robot and explicitly disable video:

```bash
python experiment/b2_recap/reference_validation/run_reference_calibration.py \
  --robot unitree-go2-stock-12dof \
  --output /tmp/b2-reference-smoke \
  --no-video
```

The no-video mode records `diagnostic_execution_passed` but deliberately keeps
`validation_passed=false` and exits non-zero. Likewise, a one-robot run cannot
set the index-level `prerequisite_passed`; Section 7 requires the complete
two-robot video cohort.

The runner records the Git revision and checks the reference-validation
inputs, typed-adapter/worker/Harness code, fixed drivers, trusted skeletons,
and canonical assets for tracked changes. Dirty relevant inputs remain useful
for diagnostics but cannot set `validation_passed` or `prerequisite_passed`.

The fixed drivers are selected in `selection.json` and always resolve to each
package's `reference/fixed_capability_driver.py`; the legacy task-macro
reference drivers are not used here.
