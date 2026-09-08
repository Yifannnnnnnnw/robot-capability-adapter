# Fixed-family v1 diagnostic

This is the user-approved 11-robot diagnostic of fixed capabilities and continuous
Driver development. It is not a thesis experiment cohort or a formal denominator.
No TGCD/IVC authoring, TaskDemo, Evolution, or policy training is performed.

Latest single-robot trial: [Opus 5 Franka Panda](OPUS5_FRANKA_RESULTS.md).
Environment and Study passed; Generate recovered from its first timeout, then
stopped at a later timeout after the shared retry allowance was used. No Harness
or Repair ran. The [SO-101 retry](OPUS5_SO101_RETRY_RESULTS.md) and
[first Opus trial](OPUS5_SO101_RESULTS.md) remain separately recorded.

Current execution evidence: [Holistic results](HOLISTIC_RESULTS.md), with V3.2
transport interruptions and the separate low-cost Qwen fallback recorded apart.

Previous execution: [follow-up results](FOLLOWUP_RESULTS.md). The eight new robots
passed basic environment checks, but API HTTP 402 interrupted synthesis. This
does not establish eight completed model outcomes. Initial results remain below
as historical diagnostic evidence, not rescored under the corrected evaluators.

After the user replaced the company API credential, [Holistic connectivity was
verified](HOLISTIC_CONNECTIVITY.md) with DeepSeek V3.2. The gateway does not list
V4 Flash. The user then authorized a cheap model for the remaining six robots;
their separate [V3.2 run configuration](HOLISTIC_RUN_HANDOFF.md) uses the existing
text tool-observation compatibility mode after native tool-history requests
failed at the gateway.

## Scope and executable inputs

| Family | Robots | Capabilities | Cases |
| --- | --- | --- | ---: |
| Gripper arms | SO101, Franka, Kinova Gen3 + 2F85, xArm7, UR5e + 2F85, Piper | A1–A5 | 60 |
| Arm without gripper | KUKA iiwa 14 | A1, A2, A4, A5 | 8 |
| Quadrupeds | Go2, Unitree A1, ANYmal-C, Barkour vB | G1–G5 | 40 |

Each robot directory contains `capability_design.json` and
`capability_validation_suite.json`. Each capability has exactly one nominal and
one boundary case. The `calibrated_boundary` field is the existing ABI role name;
it does not assert that a newly prepared reference control has passed.
The nine new adaptations also contain `robot_bindings.json` with physical mappings.
SO101 and Go2 retain their existing package-local bindings.

The baseline semantics come from `../capability_v2/ivc_worked_references.json`.
Their `b1_driver_validation_criteria` references identify the original SO101/Go2
standard, not independently sourced manufacturer performance guarantees for the
other robots. Cross-robot reuse of these criteria was explicitly selected by the
user. Historical run outcomes are not reused as new successes.

## Common pass rules

The JSON criteria and trusted evaluators are the executable definition. The main
rules are:

| ID | Capability | Main criterion |
| --- | --- | --- |
| A1 | End-effector position | error ≤ 15 mm for 0.5 s |
| A2 | Ordered Cartesian path | waypoint/cross-track ≤ 20 mm; endpoint ≤ 15 mm for 0.5 s |
| A3 | Gripper opening | normalized error ≤ 0.10 for 0.25 s; excursion ≥ 0.50 of range |
| A4 | Controlled contact | precontact ≤ 15 mm; lateral ray error ≤ 10 mm; contact ≥ 0.1 s; post-contact speed ≤ 0.02 m/s; penetration ≤ 5 mm; no unrelated contact |
| A5 | Offset and return | outbound/return ≤ 15 mm; holds 0.25/0.5 s; ≥ 80% requested displacement; return deadline starts after outbound hold completes |
| G1 | Planar twist | final 1 s mean velocity error ≤ 0.10 m/s; yaw-rate error ≤ 0.30 rad/s; direction error ≤ 10° in scored translating commands |
| G2 | Relative body pose | position ≤ 0.10 m; yaw ≤ 0.0873 rad; speed ≤ 0.10 m/s for 0.5 s |
| G3 | Planar path | waypoint/endpoint ≤ 0.10 m; cross-track ≤ 0.15 m; terminal speed ≤ 0.10 m/s for 0.5 s |
| G4 | Body height | height error ≤ 0.03 m; attitude ≤ 0.1745 rad; planar drift ≤ 0.05 m; yaw drift ≤ 0.0873 rad |
| G5 | Stance recovery | recover from prescribed 5°/8° roll within 0.5 s; preserve stance, height, drift, speed and four-foot support limits |

Arm movement guards retain gripper drift ≤ 5% of its joint opening range. A3
retains TCP drift ≤ 15 mm and arm joint drift ≤ 0.03 rad. KUKA has no A3 or
gripper-drift requirement. A6 is excluded for all arms.

G5 accepts real actuator force during canonical physics steps as control evidence
even when a native servo maintains the same target. Other capabilities retain
the control-change rule. This does not waive any stance, support or contact
criterion; it does not claim to detect re-writing an identical command.

The gripper measurement is the existing normalized physical joint-opening proxy:
`(measured_joint - closed_position) / (open_position - closed_position)`.
Franka/Piper use a prismatic-joint range in metres; Robotiq/xArm use the linked
driver-joint angle in radians. This is not a universal fingertip gap in metres.
  Closed/open directions, native actuator commands, passive coupling and contact
geometry remain robot-specific.

## Physical adaptation

- Arm A1/A2 targets are obtained by FK from each home configuration with 0.14/0.22
  rad perturbations of an arm joint, selecting the largest resulting TCP change.
  A4 uses a separate mocap target fixture; its placement is recorded in the private
  instance. A5 retains the original small base-frame offsets. These are provisional
  physical instances until the corresponding reference trial passes.
- Franka exposes `fixed_tcp` at hand-local `[0, 0, 0.107]` m in a separate scene.
  Existing task scenes are preserved. Other arms retain their existing TCP sites.
- A1 and ANYmal-C assets come from MuJoCo Menagerie commit
  `8161bba264d7fa7c99ca301e91e7fb44737676ad`, with package licenses retained.
  Both source MJCFs have native position actuators. Their public skeleton uses
  `QuadrupedSpec(actuation='joint_position')`; no Go2/Barkour weights are reused.
- Go2/Barkour retain their own learned helper policies. A1/ANYmal-C use the simple
  PD/periodic-gait helper. Results therefore do not establish a controller-matched
  ranking of robot difficulty.
- New quadruped G4 request ranges are 0.8–1.2 times home height, with nominal and
  boundary targets at 0.94/1.08 times home height. Absolute pass tolerances remain
  unchanged. G5 roll perturbations are applied by Framework reset to free-base
  qpos; the candidate never sets qpos to execute a capability.
- Kinova/UR5e closed-gripper reset states retain the full passive-joint
  configuration obtained by four seconds of real actuator settling. A1/Barkour
  tilted stance resets lift the base only enough to remove initial floor
  interpenetration while retaining the prescribed roll. Subsequent dynamic
  penetration remains subject to the unchanged 5 mm limit.
- The approved follow-up adds KUKA gravity-bias compensation through native
  position commands in its reference controller; actuator gains and limits remain
  unchanged. Public control notes describe the compensation for model development.
- A1, Barkour and ANYmal-C now use diagnostic model copies with foot `solimp`
  `[0.9, 0.95, 0.001, 0.5, 2]`. Original assets, home targets and policy weights
  remain intact. Public development and private validation use the same adapted
  scene. A separate `fixed_settled` reset records three seconds of physical
  settling from 0.5 mm foot-floor clearance. G4 heights use the measured settled
  baseline; G5 retains the prescribed tilt. Normal standing is physically usable,
  but existing locomotion policies may still fail under these contact conditions.

## Running and evidence

From `autoadapter/`, using the project Python with MuJoCo installed:

```sh
PYTHONPATH=src python scripts/run_fixed_family_diagnostic.py --robots robotstudio_so101 unitree-go2-stock-12dof
PYTHONPATH=src python scripts/run_fixed_family_diagnostic.py --reference-only
```

Omit `--robots` for the full cohort. `--output` must name a fresh directory. The
runner reads `configs/diagnostics/fixed-family-v1.json` and audits fixed inputs,
real scene resets, and measurement bindings before model calls. A short real
worker checks native actuator response; quadrupeds also perform an upright
two-second hold. Invalid resets, absent control response, worker failures or
deep contact penetration block that robot and are reported directly.
Full capability reference success is not required. `--reference-only` remains
available for local calibration, while normal synthesis skips the complete
reference suite and reports its result as unexecuted, not failed or passed.
The approved follow-up samples only the eight previously unsampled robots;
SO101, Go2 and Piper are not resampled.

Default model: DeepSeek V4 Flash, thinking disabled, skeleton-assisted, one fresh
cell per robot, at most three Harness submissions. Study uses 16 model turns;
Generate and each Repair use 22. Generate/Repair share their conversation and
development worker, including the existing 4,000-step execution budget. The fixed
thresholds and instances must not be relaxed after candidate failure.

Raw results live under `runs/diagnostic/`: reference and candidate reports,
per-trial videos, generated source revisions, feedback and API call usage.
`diagnostic_summary.json` separates preparation, reference completion and candidate
results. See `diagnostic_results.json` and `DIAGNOSTIC_RESULTS.md` here for the
completed run index. Only real failed submissions followed by actual continued
model calls count as evidence of continuous Repair.
