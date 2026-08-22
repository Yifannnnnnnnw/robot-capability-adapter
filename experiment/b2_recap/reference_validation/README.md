# B2 reference-driver validation

This directory resolves the two B2 reference-driver positive-control suites
from the Experiment 1 fixed capability bundles. It does not modify the
Experiment 1 inputs.

`build_suites.py` copies both capability designs and suites mechanically. The
Go2 outputs are unchanged. The SO-101 suite applies only the two prospective
B2 calibration corrections:

- `A3-H3` starts the gripper at the canonical open limit `1.7453292` in both
  reset maps.
- Each `A4` binding uses the explicit calibrated distal-geom whitelist,
  excludes `geom_37` and the proximal jaw boxes, and sets
  `precontact_gate="held_window_then_ray"`.

Build or check the committed resolved inputs:

```bash
python experiment/b2_recap/reference_validation/build_suites.py
python experiment/b2_recap/reference_validation/build_suites.py --check
```

Run both fixed reference drivers through the real `run_private_suite` MuJoCo
path. Video recording is enabled by default, and reports are written beneath
the required output directory:

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

The fixed drivers are selected in `selection.json` and always resolve to each
package's `reference/fixed_capability_driver.py`; the legacy task-macro
reference drivers are not used here.
