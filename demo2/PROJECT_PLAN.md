# Demo2 one-hour MuJoCo MVP plan

## Fixed objective

`demo2/` is a separate direct-MuJoCo research line. Generation and validation
use the AutoAdapter 1.0 `SelfAssemble` structure. The only main-line changes are:

1. the existing 2.0 Stage 1 runs first and seals which capabilities must exist;
2. an isolated Blue Line selects the frozen validation input and threshold;
3. Evolution reads the terminal report as a non-blocking sidecar.

There is no `capability.py`, Validation A, `ValidationBRunner`, SDK route,
Translation, DDS, guard, or dwell gate in Demo2.

```text
Morphology + Tasks + Experience
              |
              v
       2.0 Stage 1 seal
              |
        +-----+-------------------+
        |                         |
        v                         v
isolated Blue Line       1.0 STUDY + GENERATE
(input + threshold)      (full MJCF, skeleton inspect,
        |                 direct MuJoCo probes -> driver.py)
        +-------------+-----------+
                      v
            1.0 direct validator
        (driver.build + real physics state + MP4)
                      |
                      v
              Evolution sidecar
```

The Blue suite is generated before implementation but is not written into the
agent workspace until after the first `driver.py` exists. The initial generator
therefore cannot tune against private cases or thresholds. A failed validation
may expose the complete 1.0-style failure report to the next GENERATE attempt.

## First three robots

| Robot | Sealed MVP capabilities | 1.0 family | Complete MJCF |
|---|---|---|---|
| `robotstudio_so101` | `move_joints`, `move_to_cartesian` | `ArmSerialDLSSkeleton` | `legacy_assets/robotstudio_so101/scene.xml` |
| `franka_panda` | `move_joints`, `move_to_cartesian` | `ArmSerialDLSSkeleton` | `legacy_assets/franka_panda/scene.xml` |
| `unitree-go2` | `stand_up`, `sit` | `QuadrupedPDGaitSkeleton` | `legacy_assets/go2/go2_scene.xml` |

SO-101 G3 task material is retained under the Tasks Library as reference-only;
it is not part of this G1/G2 MVP verdict.

## Generation contract

The vendored runtime is fixed to AutoAdapter 1.0 commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`. Its original STUDY/GENERATE
prompts, ReAct loop, skeleton listing/inspection, file tools, AgentCore Python
tool and local MuJoCo execution tool remain intact.

The generator receives only one new public file, `sealed_design.json`. It must
write `driver.py` with `build()` and expose the exact sealed effects on the
returned object. For arm G2, `move_to_cartesian` is a real public alias over the
1.0 skeleton's `move_cartesian`; the capability name is not silently changed.

The initial generator does not receive `blue_line.json`.

## Validation and Repair contract

Blue Line emits one direct probe per sealed capability:

```json
{
  "capability_id": "move_joints",
  "method": "move_joints",
  "inputs": {"target": [0.05, -0.04, 0.03, -0.02, 0.01], "duration": 0.5},
  "measurement": "joint_position_l2_error_rad",
  "comparator": "<=",
  "threshold": 0.03
}
```

The validator preserves the 1.0 mechanism: side-load `driver.py`, call
`build()`, invoke the method directly, read the post-step MuJoCo state, compare
the metric, capture frames through `step()`, and write `validate_report.json`
plus an MP4 when rendering is available.

`all_ok` is the physical verdict. `structural_ok` only means the driver built
and the probes completed without exceptions; it must never be reported as a
physical pass.

Repair is the 1.0 outer `GENERATE <- VALIDATE` loop, up to three attempts in
the MVP. Feedback includes capability, method, case, actual metric, comparator,
threshold, exception/detail, and video path. It is not the restricted 2.0
Repair capsule.

## One-hour execution plan

### 0-15 minutes — substrate

- Vendor exact 1.0 skeleton/runtime sources and complete MJCF closures.
- Load and advance all three models.
- Keep the work isolated under `demo2/`.

### 15-30 minutes — inputs

- Add only `morphology`, `tasks`, and `experience` libraries.
- Run 2.0 Stage 1 from public Tasks input.
- Generate an implementation-blind Blue direct-validation suite.

### 30-50 minutes — main flow

- Inject `sealed_design.json` into the original 1.0 generator.
- Generate `driver.py` after full MJCF/skeleton inspection and direct probes.
- Run the Blue inputs through the 1.0 validator for all three robots.

### 50-60 minutes — closeout

- Add the non-blocking Evolution proposal.
- Run the three-robot reference calibration to distinguish code/physics issues
  from model-provider availability.
- Run one formal dynamic smoke as soon as a working model endpoint is available.

## Commands and outputs

```bash
python -m demo2.cli inspect --robot robotstudio_so101
python -m demo2.cli calibrate-all --output /tmp/demo2-calibration
python -m demo2.cli smoke --robot robotstudio_so101 --output /tmp/demo2-formal
python -m demo2.cli run-all --output /tmp/demo2-three-robot-formal
```

Each successful robot workspace contains:

```text
sealed_design.json
stage1.json
blue_line.json
study.json
driver.py
generation.json
generation_probe.json
validate_report.json
recordings/*.mp4        # when rendering/encoding succeeds
evolution.json
summary.json
traces/*.jsonl          # formal dynamic generation
```

`calibrate-all` is explicitly non-formal: it uses reviewed 1.0-derived
reference drivers to prove the three physical/validator paths. It must not be
reported as dynamic synthesis. A formal claim requires `smoke` or `run-all`
with a real model call and generator trace.

## Stop conditions

Report a blocker instead of substituting a fixed driver when:

- the configured model credential or endpoint fails;
- the model cannot use the 1.0 tools;
- AgentCore or local MuJoCo execution is unavailable;
- a complete MJCF closure cannot load;
- `driver.py` was not model-generated in a formal run.
