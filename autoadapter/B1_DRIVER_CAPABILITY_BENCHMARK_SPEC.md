# B1 Generated-Driver Capability Benchmark Specification

**Status:** proposed experiment specification; not yet a formal frozen B1 input bundle

**Scope decision:** all six profiles, LEAP contact maintenance, and both ALOHA bimanual
capabilities approved on 2026-08-21; numeric gates remain proposed until threshold approval and
reference calibration

**Scope:** the exact eleven-configuration cohort declared by `AUTOADAPTER_2_AUTHORITY.md`

**Purpose:** define what every generated `driver.py` must expose and exactly how B1 admits it

This specification replaces the task-wrapper design of `fixed_driver_v1` as the proposed B1
direction. It does not change the current executable benchmark by itself. The interfaces,
private cases, reference drivers, Harness bindings, and Authority clauses identified in Section
11 must be implemented and calibrated before a formal B1 run.

## 中文决策摘要

这份规格把 11 台正式测试机器人划分为 6 个 capability profile：

- 6 台带夹爪固定串联臂：5 项通用末端、路径、夹爪、接触和往返能力；
- KUKA iiwa 14：沿用其中 4 项，以末端旋转往返替代夹爪能力；
- Unitree Go2：5 项平面速度、相对位姿、路径、机身高度和稳定站立能力；
- LEAP Hand：6 项关节位姿/路径、指尖目标、接触建立/保持和指尖往返能力；
- Stretch 2：8 项移动底盘、工具、夹爪、腕部、接触和往返能力；
- ALOHA 2：6 项左右单臂与真正双臂协调能力。

所有 public driver method 都接收 capability 自己的物理参数，禁止出现 `task_id`、
`task_parameters`、任务名称、场景/测试 case 身份或 task-to-capability mapping。完整 task 仅由
B2 controller 看见并拆解。

B1 对每项 capability 使用 3 个互不相同的隐藏参数/reset variant。单项 capability 必须 3/3
全部通过；一台 driver 必须所有 capability 全部通过；每个 cell 最多提交 3 个 attempt。指标不能
平均抵消失败，candidate 的返回值不能证明成功，且每个 trial 必须同时通过 closed-loop、真实
`data.ctrl`/`mj_step`、5 mm 穿透、接触/支撑完整性和完整视频门槛。

本文的低层路径、接触、往返和协调阈值属于拟预注册的实验标准，不冒充现有 Task Library 的完整
任务门槛。正式封存前必须先修订 Authority 的 task-specific ABI，再由 task-blind reference driver
对完全相同的隐藏 suite 做正控制校准。

## 1. Decision

B1 evaluates **task-agnostic, parameterised robot effects**, not complete benchmark tasks.

```text
complete task and task identity
        B2 controller only
                 |
                 v
capability-specific request -> generated B1 driver -> actuator/MuJoCo effect
                 |
                 v
        trusted Harness verdict
```

The generated driver must not receive `task_id`, `task_parameters`, source-task identity,
`covered_task_ids`, scene identity, case identity, seed, or a task-to-capability mapping. A
capability request contains only the typed physical target needed by that method.

The names in the earlier SO-101 table remain useful **benchmark-scenario labels**:

| Scenario label | Public driver capability used by the scenario |
|---|---|
| `reach_above_object` | `move_end_effector_to_position` after the Framework resolves the above-object target point |
| `trace_cartesian_path` | `trace_cartesian_path` |
| `cycle_gripper` | two or more calls to `set_gripper_opening` |
| `establish_controlled_contact` | `approach_until_contact` |
| `move_cartesian_offset_and_return` | `move_cartesian_offset_and_return` |

This distinction matters. `set_gripper_opening(0.2)` can be composed by B2, whereas a fixed
`cycle_gripper()` procedure cannot express “close and keep holding”. Similarly, object identity
belongs to B2/Harness; the driver receives only a Cartesian target or approach geometry.

## 2. Common driver ABI

Every candidate is one isolated, model-generated `driver.py` with:

```python
def build(*, model, data): ...

class Driver:
    def <capability_method>(self, request): ...
```

Requirements:

- `build` binds the exact Framework-owned canonical `MjModel` and `MjData` objects.
- Every method accepts exactly one capability-native `request` object. Undeclared properties are
  rejected.
- Units and frames are part of the public contract. Position arrays are finite `number[3]`.
- The method may return bounded execution diagnostics, but its return value never establishes
  success.
- The method must use fresh physical observations in a bounded feedback loop, write through
  `data.ctrl`, and advance the same canonical session through real MuJoCo physics.
- Numeric branching needed for control is allowed. Dispatch by task, source, scene, case, opaque
  identity, or known hidden value is forbidden.

For every position, path, offset, posture, and aperture target, the Framework publishes the safe
input domain before generation. Hidden cases may only sample inside that domain.

All fields listed in the profile tables are required unless a field is explicitly marked optional;
unlisted properties are forbidden. The tables below freeze the intended semantic interface, but
they are not a substitute for the machine-readable schema. Before a bundle can be frozen, each
method must have a JSON Schema with `additionalProperties: false`, exact array lengths, enum/const
values, finite-number requirements, numeric minima/maxima, path-length bounds, frame, unit, and the
robot-specific safe/reachable domain. The public schema and the Harness validator must be the same
artifact; prose-only constraints do not make an executable benchmark.

For every `max_duration_per_segment_s` field, the timer starts at invocation for segment 1 and at
the first qualifying entry into the preceding waypoint for later segments. It stops at first
qualifying entry into the current waypoint. A fixed terminal dwell is added to the Harness budget
and is not charged to the final segment; this timing rule is identical for A2, L2, ST4, and AL2.

### 2.1 Threshold provenance

The thresholds below are proposed, pre-registered **benchmark-authored experimental standards**.
They must not be described as manufacturer limits or as copied Task Library task-success gates.
The existing Task Library directly supports only a small subset, such as arm reach, Go2 velocity
tracking, and two LEAP tracking metrics.

Once this specification is approved, thresholds are frozen before reference calibration. The
package-local task-blind reference driver must then pass the exact private suite. A failed positive
control requires fixing the package, binding, case, or pre-run specification; thresholds may not be
loosened after generated-driver results are observed.

### 2.2 What the B1 benchmark bundle contains

| Bundle part | Minimum content | Visibility during generation/Repair |
|---|---|---|
| Public driver contract | profile method names; strict request schemas; units and frames; public safe domains; observations and actuators; budgets; metrics, thresholds, temporal aggregation, side-effect limits, and public smoke examples | visible to initial generation and Repair |
| Robot package binding | exact MJCF entrypoint/version; canonical actuator/joint/body/site/geom names; measurement equations; base/tool frames; aperture endpoints; contact and support allowlists | public except scene-private target identities |
| Private scored suite | exactly H1/H2/H3 per capability; exact requests, resets, fixture poses, disturbance values, seed, timeout, and video setup | sealed from generation and Repair until the registered round ends |
| Trusted executable verdict | schema validator; reset owner; metric/guard evaluators; source/build audit; canonical-session trace collector; video recorder | executable only by Framework/Harness |
| Positive control | task-blind reference driver evaluated on the exact sealed suite | result visible before generation; source and case-level trace need not be exposed |
| Experiment manifest | 11 robot configurations, package versions, seven backbones, two generation conditions, five replicates, three-attempt cap, and terminal-cell accounting | public, except sealed seeds/case identities |

Neither the public contract nor the private suite contains a B2 task name, Task Library entry, or
task-to-capability plan. Public smoke inputs are disjoint from all scored requests and cannot count
as B1 evidence.

## 3. Cohort and capability profiles

| Robot configuration | Morphology | Profile | Capability count |
|---|---|---:|---:|
| `robotstudio_so101` | 5-DoF fixed arm + gripper | A | 5 |
| `franka_panda` | 7-DoF fixed arm + gripper | A | 5 |
| `kinova_gen3_robotiq_2f85` | 7-DoF fixed arm + gripper | A | 5 |
| `ufactory_xarm7` | 7-DoF fixed arm + gripper | A | 5 |
| `universal_robots_ur5e_robotiq_2f85` | 6-DoF fixed arm + gripper | A | 5 |
| `piper` | 6-DoF fixed arm + gripper | A | 5 |
| `kuka_iiwa_14` | 7-DoF fixed arm, no gripper | K | 5 |
| `unitree-go2-stock-12dof` | free-base quadruped | G | 5 |
| `leap_hand` | fixed 16-DoF dexterous hand | L | 6 |
| `hello_robot_stretch_2` | free-base mobile manipulator | ST | 8 |
| `aloha_2` | fixed-base bimanual manipulator | AL | 6 |

Sharing a profile means sharing semantic granularity and public thresholds. Robot-specific MJCF
symbols, reachable domains, controls, and measurement bindings remain in each robot package.
The tables contain 31 profile-level method contracts but 27 distinct method names because
`set_gripper_opening` and `approach_until_contact` are deliberately reused across compatible
profiles. Applied to all robots, they produce 60 robot-capability slots.

## 4. Profile A: six fixed serial arms with grippers

These six robots expose the same five public methods. SO-101 is position-only at this layer; the
benchmark does not require arbitrary 6D pose control from its five arm joints.

| ID | Method | Exact request fields | Measured effect |
|---|---|---|---|
| A1 | `move_end_effector_to_position` | `target_position_m` (world, `number[3]`), `max_duration_s` | EE reaches and holds the supplied point |
| A2 | `trace_cartesian_path` | `waypoints_m` (world, `number[N][3]`, `2 <= N <= 8`), `max_duration_per_segment_s` | EE traverses the ordered polyline |
| A3 | `set_gripper_opening` | `opening_fraction` (`0` closed, `1` open), `max_duration_s` | measured physical aperture reaches the request |
| A4 | `approach_until_contact` | `precontact_position_m` (world), `approach_direction_unit` (world), `max_travel_m`, `max_approach_speed_m_s` in `(0, 0.05]`, `max_duration_s` | EE approaches, makes tool-target contact, and stops |
| A5 | `move_cartesian_offset_and_return` | `offset_robot_base_m` (`number[3]`), `max_duration_per_leg_s` | EE reaches the relative offset and returns to its call-time position |

### 4.1 Profile A gates

| ID | Public B1 PASS standard |
|---|---|
| A1 | Euclidean EE target error `<= 0.015 m` continuously for `0.5 s`. |
| A2 | Every waypoint is entered in order within `0.020 m` and no segment exceeds `request.max_duration_per_segment_s`; maximum trace-phase polyline cross-track error `<= 0.020 m`; final-point error `<= 0.015 m` continuously for `0.5 s`. |
| A3 | Normalised physical-aperture error `<= 0.10` continuously for `0.25 s`. Each hidden case calls the method in both directions; measured excursion in each direction must be at least `0.50` of declared full travel. |
| A4 | The ordered precontact, ray-approach, and contact gates below all pass; the private tool-target contact pair then persists for `0.10 s`; post-contact tool-point speed is `<= 0.02 m/s`; penetration is `<= 0.005 m`; unrelated contact count is `0`. |
| A5 | Outbound error `<= 0.015 m` for `0.25 s`, followed by return error `<= 0.015 m` for `0.5 s`; phase order is correct; neither leg exceeds `request.max_duration_per_leg_s`; maximum displacement reaches at least `80%` of requested offset magnitude. |

A4 first requires the controlled tool-frame origin to enter `0.015 m` of
`precontact_position_m` for `0.10 s` without target contact. The measured tool position at the end
of that dwell becomes `p_start`; the approach phase uses `d = approach_direction_unit`:

- axial progress `s = dot(p - p_start, d)` stays in
  `[-0.002 m, request.max_travel_m + 0.002 m]` and never falls more than `0.002 m` below its prior
  running maximum;
- lateral deviation `||(p - p_start) - s*d||` stays `<= 0.010 m`;
- accumulated tool-origin path length stays `<= 1.10 * request.max_travel_m`;
- before contact, the Harness uses `mj_geomDistance` on the frozen allowed tool/target geom pairs to
  obtain the closest two surface points, derives their velocities from the canonical body
  Jacobians, and requires their relative-speed norm to be
  `<= request.max_approach_speed_m_s + 0.01 m/s`;
- at first contact, contact-normal closing speed `max(0, -d(gap)/dt)` computed from the two
  contact-point velocities obeys the same speed bound.

Contact before the precontact dwell, reverse progress, lateral sweep, or travel beyond the request
outside the stated `0.002 m` numerical tolerance fails the trial even if the desired pair eventually
touches.

Robot-specific measurement bindings:

| Robot | EE binding | One physical aperture scalar `a` |
|---|---|---|
| SO-101 | site `gripperframe` | joint position of `gripper` |
| Franka | body `hand` until a TCP site is frozen | `finger_joint1 + finger_joint2` slide positions |
| Kinova + 2F-85 | site `pinch_site` | distance between declared left/right pad-centre sites |
| xArm7 | site `link_tcp` | distance between declared left/right pad-centre sites |
| UR5e + 2F-85 | site `pinch_site` | distance between declared left/right pad-centre sites |
| Piper | site `ee_site` | physical separation `joint7 - joint8` |

For A3, every package freezes measured closed/open endpoints `a_closed` and `a_open`, with
`a_open != a_closed`. The request maps to the physical target
`a_target = a_closed + opening_fraction * (a_open - a_closed)`, and the Harness scores
`f_measured = (a - a_closed) / (a_open - a_closed)`. It does not score actuator command or a
driver-reported value. Pad-centre sites and both endpoints are missing package bindings until they
are explicitly added and reference-calibrated.

For A5, `robot_base` is the package-declared fixed arm-base body/site. Its world transform and the
call-time EE position are sampled at invocation and frozen for both legs; the driver cannot redefine
the frame after moving. Each package must name this binding in the machine-readable specification.

Method-specific side-effect guards are also conjunctive. A1, A2, A4, and A5 keep normalised gripper
aperture within `0.05` of its call-time value. A3 keeps EE position within `0.015 m` and every arm
joint within `0.03 rad` of its call-time value. For every arm-motion method, each package-declared
continuous/unlimited joint has maximum unwrapped displacement `<= pi rad` from call time; a
multi-turn wind-up cannot hide behind an equivalent wrapped angle. These proposed drift bounds must
pass the package reference controls before freeze.

No contact-force threshold in newtons is specified yet. The current packages do not expose a
calibrated force sensor or a defensible cross-model force bound. Positive target contact, approach
speed, stopping, unrelated contacts, and the shared 5 mm penetration limit are the normative
controlled-contact evidence.

## 5. Profile K: KUKA iiwa 14 without a gripper

KUKA uses A1, A2, A4, and A5 unchanged. It replaces A3 with:

| ID | Method | Exact request fields | Measured effect |
|---|---|---|---|
| K3 | `rotate_end_effector_and_return` | `rotation_axis_unit` (`number[3]`), `rotation_delta_rad` with `0.1745 <= abs(value) <= 0.7854`, `frame="end_effector"` (const), `max_duration_per_leg_s` | tool orientation rotates about the supplied local axis and returns |

K3 passes only when:

- outbound orientation geodesic error is `<= 0.0873 rad` (5 degrees);
- EE position drift is `<= 0.015 m` at every physics sample of both outbound and return legs;
- return orientation error is `<= 0.0873 rad` and position return error is `<= 0.015 m`;
- outbound and return phases occur in order, each finishes inside
  `request.max_duration_per_leg_s`, and each holds for `0.25 s`.

KUKA uses the centre and orientation of `link7_contact_geom` as its one controlled tool frame for
A1, A2, A4, A5, and K3. The current `attachment_site` is not mixed into B1 scoring because its
origin is offset from the contact geom and would create two incompatible tool definitions.
For KUKA A1, A2, A4, and A5, tool-orientation geodesic drift from call time remains
`<= 0.1745 rad`; Profile A's continuous-joint wind-up guard also applies. K3 is the only K method
that intentionally changes tool orientation.

K3 freezes call-time orientation `R0`, local unit axis `a`, and target
`R_target = R0 * Exp([a]_x * rotation_delta_rad)`. At every outbound physics sample it computes
`r = Log(R0^T * R)` in the call-time tool frame, signed progress
`p = sign(rotation_delta_rad) * dot(a, r)`, and off-axis rotation `||r - dot(a,r)*a||`. Progress
stays in `[-0.035, |rotation_delta_rad| + 0.035] rad`, may backtrack by at most `0.035 rad`, and
off-axis rotation stays `<= 0.0873 rad`. Cumulative geodesic angular path is at most
`1.25 * |rotation_delta_rad| + 0.0873 rad`. The return leg applies the same constraints to the
reverse path from `R_target` to `R0`. Thus an axis-incorrect rotation, overshoot, orientation detour,
or extra full turn fails even if the terminal orientation is correct.

## 6. Profile G: Unitree Go2

Terrain names, steps, parkour obstacles, and courses are B2 tasks and never driver methods.

| ID | Method | Exact request fields | Public B1 PASS standard |
|---|---|---|---|
| G1 | `track_planar_twist` | `linear_velocity_body_m_s: number[2]`, `yaw_rate_rad_s`, `duration_s` | Over the final `1.0 s`, mean planar velocity-vector error `<= 0.10 m/s`, mean yaw-rate error `<= 0.30 rad/s`, and when requested planar speed is at least `0.20 m/s`, mean direction error `<= 10 deg`. |
| G2 | `move_body_relative_pose` | `translation_initial_yaw_m: number[2]`, `yaw_delta_rad`, `max_duration_s` | terminal planar-position error `<= 0.10 m`, yaw error `<= 0.0873 rad`, and planar speed `<= 0.10 m/s` continuously for `0.5 s`. |
| G3 | `trace_planar_path` | `waypoints_initial_yaw_m: number[N][2]`, `2 <= N <= 8`, `max_duration_s` | every waypoint is reached in order within `0.10 m`; maximum cross-track error `<= 0.15 m`; endpoint error `<= 0.10 m` and speed `<= 0.10 m/s` for `0.5 s`. |
| G4 | `set_body_height` | `target_height_m` in the proposed scored range `[0.22, 0.36]`, `max_duration_s` | body-height error `<= 0.03 m`, absolute roll/pitch each `<= 0.1745 rad`, planar displacement `<= 0.05 m`, and yaw drift `<= 0.0873 rad`, continuously for `0.5 s`. |
| G5 | `hold_stable_stance` | `duration_s` in `[1.0, 2.0]` | recover to absolute roll/pitch `<= 0.0524 rad` within `0.5 s`; thereafter absolute roll/pitch each `<= 0.0873 rad`, height drift from invocation `<= 0.03 m`, planar displacement `<= 0.05 m`, mean planar speed `<= 0.05 m/s`, terminal vertical speed `<= 0.05 m/s`, all four feet retain floor support, and no base/head-floor contact occurs. |

For G1, the requested planar velocity is expressed in the **instantaneous** horizontal body-yaw
frame at every physics sample. The Harness rotates measured world velocity by the inverse of the
current `base_link` yaw before computing error; it does not freeze the body frame at invocation.
The proposed scored subdomain is planar-speed norm `0` or `[0.20, 0.40] m/s`, yaw-rate magnitude
`0` or `[0.20, 1.00] rad/s`, and duration `[2.0, 4.0] s`, with at least one nonzero component.
Direction error is undefined and therefore omitted below `0.20 m/s`.

Over final-window physics samples `i`, the Harness first computes
`v_body_i = Rz(-yaw_i) * v_world_xy_i`. The reported velocity error is
`mean_i(||v_body_i - v_request||_2)`, and yaw-rate error is
`mean_i(|wz_i - wz_request|)`; signed errors are never averaged before taking magnitude. When
direction is scored, each sample contributes the shortest angle between requested and measured
planar velocity. A measured speed below `0.05 m/s` contributes `pi rad`, rather than being omitted,
and the arithmetic mean of those non-negative angles must be `<= 0.1745 rad`.

The `1.2 m/s` planar and `3.0 rad/s` yaw clips in the current controller are implementation safety
bounds, not the scored benchmark domain. G1 hidden variants include different translation
directions and both yaw signs. The existing source-backed `0.4 m/s` eight-direction and `10 deg`
heading protocol is reported as lineage for applicable G1 variants, but does not turn terrain-
completion tasks into capabilities.

For G4, `target_height_m` is the world vertical distance from the `floor` plane to the origin of
body `base_link`. The three hidden heights must be pairwise separated by more than `0.060 m` so a
fixed-height controller cannot satisfy two cases. The proposed range is grounded only by the
current `0.29 m` home pose and `0.27 m` retained policy target; it remains a pre-freeze calibration
item, not an already validated safe domain.

Every G5 variant starts from four-foot support with zero base velocity. Immediately before
invocation, the Framework applies exactly one reset-quaternion disturbance: world-frame roll or
pitch, signed magnitude in `[0.0873, 0.1396] rad`, with the other angle zero; it does not apply a
force-pulse alternative or settle the disturbed state before calling the driver. Axis, sign, and
exact magnitude are sealed case values, while this operator/domain is public. Candidate input
remains only `duration_s`, and a nominal unperturbed wait is not a scored G5 case. The Harness
resolves feet as all contact geoms descended from `FL_foot`, `FR_foot`,
`RL_foot`, and `RR_foot`, and resolves support surfaces from a frozen scene allowlist. It samples
every physics step: each foot must have allowed support in at least `90%` of post-disturbance samples
and no continuous support loss may exceed `0.10 s`.

## 7. Profile L: LEAP Hand

Object identities, EC pattern names, DClaw fixtures, Baoding, and complete in-hand tasks are not
public driver methods.

| ID | Method | Exact request fields | Public B1 PASS standard |
|---|---|---|---|
| L1 | `move_hand_to_joint_pose` | `target_joint_positions_rad: number[16]`, `max_duration_s` | maximum absolute joint error `< 0.1745329252 rad` within `4 s`, then at every physics sample of a `0.5 s` hold. |
| L2 | `trace_hand_joint_path` | `joint_waypoints_rad: number[N][16]`, `2 <= N <= 8`, `max_duration_per_segment_s` | every waypoint is entered in order with maximum joint error `<= 0.05 rad` before its segment budget expires; every physics sample of the final `0.5 s` hold also passes. |
| L3 | `reach_fingertips_to_targets` | `target_fingertip_positions_palm_m: number[12]` in `[index,middle,ring,thumb]` xyz order, `max_control_steps: 50` (const) | concatenated four-fingertip Cartesian L2 error `< 0.00894427191 m` at terminal control step `50`. |
| L4 | `establish_fingertip_contact_pattern` | `required_fingers: unique enum[k]`, `contact_target_positions_palm_m: number[k][3]`, `approach_directions_palm_unit: number[k][3]`, `max_travel_m`, `max_approach_speed_m_s` in `(0, 0.05]`, `max_duration_s` | every requested fingertip passes the per-finger ray gate, enters its `0.010 m` target region, and contacts its aligned private target for `0.10 s`; nonrequested-finger guards pass; penetration `<= 0.005 m`. |
| L5 | `hold_fingertip_contacts` | `required_fingers: unique enum[k]`, `separation_directions_palm_unit: number[k][3]`, `duration_s` in `[1.0, 2.0]` | after the registered separating-target perturbation, every disturbed fingertip follows at least `80%` of target displacement and re-establishes a relative gap `<= 0.002 m` within `0.20 s`; required-contact occupancy is at least `90%`, no continuous loss exceeds `0.20 s`, and nonrequested-finger guards pass. |
| L6 | `move_fingertip_offset_and_return` | `finger_names: unique enum[k]`, `offsets_palm_m: number[k][3]`, `max_duration_per_leg_s` | selected fingertip outbound and return Euclidean errors are each `<= 0.010 m`; phases occur in order, each stays inside its leg budget and holds `0.25 s`; unselected-finger guards pass. |

L1 and L3 retain the existing source-derived numeric gates. L2, L4, L5, and L6 are
benchmark-authored low-level protocol standards and require reference calibration.

For L3, one control step is exactly `10` MuJoCo physics steps, so the const horizon is `50` control
steps = `500` physics steps. The request may not alter that horizon. The palm frame is body `palm`
sampled at each physics step, and `k` in L4-L6 is `1 <= k <= 4`; array order exactly matches the
order of `required_fingers` or `finger_names`.

L4 supplies contact geometry, not a joint-pose answer: a hidden target fixture is placed at each
requested position and only its physical parameterisation reaches the candidate. For each required
finger, its call-time tip position is the ray origin and the supplied approach direction is its ray
axis. Until first contact, axial progress must remain in `[0, max_travel_m]`, lateral deviation must
remain `<= 0.010 m`, accumulated fingertip path length must remain
`<= 1.10 * max_travel_m`, and contact-point relative speed must remain
`<= max_approach_speed_m_s + 0.01 m/s`. First-contact incoming normal speed uses the MuJoCo contact
normal and obeys the same bound.

For L5, the reset establishes every requested contact for `0.10 s` before invocation. At `0.25 s`,
the Harness moves at least one target smoothly over `0.05 s` by a sealed distance in
`[0.006, 0.008] m`, along the supplied separation direction away from its fingertip so the initial
contact opens. This is the only L5 disturbance operator. The target-following projection and final
relative gap are measured for every disturbed fingertip; a fixed call-time joint vector must fail a
pre-freeze negative control. Contact occupancy/loss are computed from every physics step.

For L4, L5, and L6, every nonrequested finger keeps fingertip displacement `<= 0.010 m` and each of
its four joints within `0.05 rad` of call time. Nonrequested fingers may not evade the guard by
moving freely without touching the fixture.

## 8. Profile ST: Hello Robot Stretch 2

| ID | Method | Exact request fields | Public B1 PASS standard |
|---|---|---|---|
| ST1 | `move_base_relative` | `translation_initial_yaw_m: number[2]`, `max_duration_s` | planar-position error `<= 0.020 m`, yaw drift `<= 0.020 rad`, and planar speed `<= 0.020 m/s` for `0.5 s`. |
| ST2 | `turn_base_to_heading` | `target_yaw_world_rad`, `max_duration_s` | wrapped yaw error `<= 0.020 rad`, planar drift `<= 0.020 m`, and yaw rate `<= 0.020 rad/s` for `0.5 s`. |
| ST3 | `move_tool_to_position` | `target_position_world_m: number[3]`, `max_duration_s` | tool-position error `<= 0.025 m` continuously for `0.5 s`; whole-body motion remains inside the declared side-effect bounds. |
| ST4 | `trace_tool_cartesian_path` | `waypoints_world_m: number[N][3]`, `2 <= N <= 8`, `max_duration_per_segment_s` | ordered waypoint and maximum cross-track error `<= 0.030 m`; every segment stays inside its requested budget; endpoint error `<= 0.025 m` for `0.5 s`; whole-body motion remains inside the declared side-effect bounds. |
| ST5 | `set_gripper_opening` | `opening_fraction` in `[0,1]`, `max_duration_s` | physical gripper-slide error `<= 0.003 m` for `0.25 s`; bidirectional hidden sequence excursion `>= 0.015 m`. |
| ST6 | `set_wrist_yaw` | `target_yaw_rad`, `max_duration_s` | absolute, non-wrapped wrist-joint error `<= 0.030 rad` for `0.5 s`. |
| ST7 | `approach_until_contact` | same geometry fields as A4 | same positive contact, speed, stopping, unrelated-contact, and penetration gates as A4. |
| ST8 | `move_tool_offset_and_return` | `offset_call_time_base_m: number[3]`, `max_duration_per_leg_s` | outbound and return error each `<= 0.025 m`, in order and inside each leg budget; outbound holds `0.25 s`, return holds `0.5 s`; base remains within `0.020 m` and `0.030 rad` of its call-time pose throughout. |

The current trusted Stretch skeleton already supports base yaw, bounded drive, tool targeting,
gripper position, and wrist yaw with comparable internal tolerances. ST7 cannot be frozen until the
package declares the exact tool contact geom/group; generic `contact_state` alone is insufficient
for a positive tool-target contact verdict.

For ST3 and ST4, coordinated base/arm motion is allowed because this is a mobile manipulator, but
the base may translate at most `0.10 m` and change yaw by at most `0.35 rad` from its call-time pose.
Both maxima and the terminal base pose are recorded. ST8 freezes the full world pose of body
`base_link` at invocation; `offset_call_time_base_m` is transformed through that frozen pose for
both legs, even if the live base moves.

For ST5, physical slide target is exactly
`q(f) = -0.005 m + 0.045 m * opening_fraction`, using joint `joint_gripper_slide`. The hidden
bidirectional calls differ by at least `0.50` opening fraction. ST6 is not circular: its declared
joint range is `[-1.75, 4.00] rad`, so wrapping error would incorrectly equate distinct admissible
joint states.

Stretch side-effect guards use call-time physical state. Both head joints always stay within
`0.03 rad` unless a future capability explicitly selects them. ST1/ST2 additionally hold lift and
total arm extension within `0.01 m`, wrist yaw within `0.03 rad`, and gripper slide within
`0.003 m`. ST3/ST4/ST7 hold wrist yaw, gripper, and head at those same bounds while allowing only
the declared `0.10 m`/`0.35 rad` base excursion. ST5 holds base within `0.02 m`/`0.03 rad`, lift and
extension within `0.01 m`, and wrist within `0.03 rad`. ST6 applies the same base/lift/extension
guards and holds gripper within `0.003 m`. ST8 holds base throughout at its stricter table bound and
holds wrist, gripper, and head at the common bounds. These are per-sample maxima, not terminal-only
checks.

## 9. Profile AL: ALOHA 2

ALOHA must exercise both physical sides. Existing single-arm Task Library lineage is insufficient
to claim a source-backed bimanual task; AL4 and AL6 are explicitly benchmark-authored coordination
effects.

| ID | Method | Exact request fields | Public B1 PASS standard |
|---|---|---|---|
| AL1 | `move_arm_to_position` | `arm: enum["left", "right"]`, `target_position_world_m: number[3]`, `max_duration_s` | selected EE error `<= 0.015 m` for `0.5 s`; every unselected-side pose guard passes. |
| AL2 | `trace_arm_cartesian_path` | `arm`, `waypoints_world_m: number[N][3]`, `max_duration_per_segment_s` | ordered waypoint/cross-track error `<= 0.020 m`; each segment stays inside its requested budget; endpoint error `<= 0.015 m` at every physics sample of an additional `0.5 s` dwell; every unselected-side pose guard passes. |
| AL3 | `set_gripper_opening` | `arm`, `opening_fraction` in `[0,1]`, `max_duration_s` | selected normalised aperture error `<= 0.10` for `0.25 s`; mirrored-finger normalised disagreement `<= 0.05`; every unselected-side pose guard passes. |
| AL4 | `move_bimanual_to_positions` | `left_target_position_world_m: number[3]`, `right_target_position_world_m: number[3]`, `max_duration_s` | both EE errors are simultaneously `<= 0.015 m` for `0.5 s`; final-dwell first-entry time difference is `<= 0.10 s`. |
| AL5 | `approach_until_contact` | `arm` plus A4 geometry fields | A4 gates applied to the selected arm and its declared contact group; every unselected-side pose guard passes. |
| AL6 | `move_bimanual_offsets_and_return` | `left_offset_arm_base_m: number[3]`, `right_offset_arm_base_m: number[3]`, `max_duration_per_leg_s` | both arms complete outbound then return inside each leg budget; every per-arm error `<= 0.015 m`; final-dwell outbound first-entry skew `<= 0.10 s`; both phases hold `0.25 s`. |

The unselected-side guard used by AL1, AL2, AL3, and AL5 is conjunctive: unselected EE drift
`<= 0.015 m`, finger displacement `<= 0.0005 m`, and maximum call-time joint drift of
`0.002 rad` waist, `0.003 rad` shoulder, `0.030 rad` elbow, `0.002 rad` forearm roll,
`0.035 rad` wrist angle, and `0.002 rad` wrist rotate. Merely keeping the unselected EE still while
moving its null-space joints or gripper cannot pass.

Selected-side effects are bounded too. AL1, AL2, AL4, AL5, and AL6 hold every selected gripper
finger within `0.0005 m` of call time. AL3 holds selected EE position within `0.015 m` and applies
the same per-joint drift thresholds listed above to the selected arm. Thus a driver cannot satisfy
an arm target while cycling a gripper, or satisfy a gripper target while moving the arm.

For AL3, the controlled finger target is exactly
`q(f) = 0.002 m + 0.035 m * opening_fraction`. The Harness normalises the measured controlled
finger as `(q - 0.002) / 0.035` and the absolute controlled/mirrored disagreement by `0.035 m`.
It never scores the actuator command.

AL4/AL6 first entry means the first physics sample that begins the final uninterrupted dwell window,
not a transient threshold crossing and not the first low-rate video frame. AL6 freezes the package-
declared left and right arm-base transforms separately at invocation and uses those transforms for
both outbound and return scoring.

The current reference path is right-arm task dispatch and is not a positive control for this
profile. A task-blind bimanual reference implementation is required before freeze.

## 10. B1 hidden suite and verdict hierarchy

### 10.1 Fixed experiment constants

- Exactly `K = 3` private scored variants per capability.
- Each variant uses a fresh reset, one scripted capability test, and one complete video.
- Each B1 cell permits at most `A = 3` submitted driver attempts: attempt 0 and two Repairs.
- Each robot × backbone × condition uses `R = 5` independent generation replicates.
- With seven backbones, the formal matrix is `11 × 7 × 2 × 5 = 770` cells and at most
  `2,310` submitted drivers.

The cohort contains 60 robot-specific capability slots. At three variants per slot, one one-attempt
eleven-robot slice executes 180 scored trials; a single robot driver attempt executes between 15 and
24 trials depending on its profile.

The sole statistical unit is one `robot × backbone × condition × generation replicate` cell.
Capabilities, hidden variants, and Repair attempts are repeated measurements inside that cell and
must not be treated as independent samples or used to inflate sample size. `R = 5` is the set of
operationally independent generation replicates for each robot/backbone/condition combination:
each replicate starts a fresh model conversation/provider request and seed, isolated workspace,
candidate source, and Repair history. All five use the same pre-frozen Experience snapshot, but no
candidate, scored report, manual observation, or Repair information may cross replicate boundaries.

A scripted variant may call the same capability more than once when its effect requires a sequence,
such as testing both gripper directions. It never calls a second public capability while scoring the
first one. Every invocation in the declared sequence must complete and every order rule must pass.
After source/import/build admission, an early candidate failure does not short-circuit the attempt:
all executable variants are run and recorded so logical-trial coverage can be compared.

Candidate failure is never selectively rerun. An infrastructure-invalid trial may be replayed only
under a pre-registered policy using the same immutable driver and exact same case, without exposing
new feedback to Repair; `replay_index` and `replay_reason` are recorded. An unresolved replay stays
infrastructure-invalid. A replay does not create another logical trial: records distinguish
`expected_logical_trial_count`, `resolved_logical_trial_count`, `physical_execution_count`, and
`replay_count`, while retaining every invalid execution and replay.

The three variants are:

1. `H1 nominal_parameter`: a normal request different from the public smoke;
2. `H2 parameter_shift`: a different target, sign, direction, path, side, or amplitude;
3. `H3 reset_and_parameter_shift`: a different valid reset plus a third request.

Rules:
- At least two distinct resets are used.
- The reset must not already satisfy the requested effect.
- Position/pose acceptance regions must not overlap.
- Signed effects cover both signs; left/right interfaces cover both sides.
- All hidden values remain inside the public safe/reachable domain.
- Metric, threshold, temporal rule, and aggregation are public and identical across variants.
- The exact same sealed variants are used across backbones, conditions, replicates, and attempts.
- A task-blind reference driver passes all variants before formal generation starts.

Profile-specific coverage is also mandatory:

| Profile | Required variation across the three variants |
|---|---|
| A | A1 uses three separated reachable points; A2 uses straight, cornered, and non-coplanar paths; A3 changes initial aperture and command order; A4 changes approach axis and contact-fixture pose; A5 covers at least two offset axes and both signs overall. |
| K | A1/A2/A4/A5 follow Profile A; K3 covers two rotation axes and both rotation signs overall. |
| G | G1 covers forward, lateral, and combined translation/yaw commands with both signs represented; G2/G3 change displacement and path shape; G4 uses three feasible heights; every G5 case uses a distinct bounded posture disturbance. |
| L | L1 uses open, pinch-like, and tripod-like poses; L2 uses three distinct joint paths; L3 uses three reachable four-fingertip target sets; L4/L5 vary required finger subsets; L6 covers one-, two-, and four-finger offsets. |
| ST | base translation and yaw cover both signs; tool paths contain 3–5 points; gripper commands cover both directions; contact uses three distinct fixture poses; tool offsets cover at least two axes. |
| AL | AL1–AL3 and AL5 cover both arms; AL4/AL6 use symmetric, asymmetric, and unequal-travel target pairs. |

Every Cartesian path's first waypoint must coincide with the reset EE/tool position within that
capability's endpoint tolerance. Cross-track scoring starts at invocation, so the approach to an
unrelated first waypoint cannot be hidden outside the path metric. Private Cartesian and fingertip
targets are admitted only by pre-registered, implementation-independent geometry, joint-limit,
collision, and reachability rules. Exact cases are sealed before the reference driver runs. A
reference failure blocks calibration or requires a recorded pre-model protocol revision; it may not
silently replace a case, weaken a threshold, or keep sampling until the positive control passes.

### 10.2 Strict verdicts

```text
trial_outcome in {PASS, CANDIDATE_FAIL, INFRA_INVALID}

trial_outcome = PASS only if
    every scripted invocation completed within budget and in order
    AND every capability-specific metric passed
    AND every temporal/order rule passed
    AND closed-loop trace passed
    AND canonical actuator-plus-physics evidence passed
    AND every physical-integrity guard passed
    AND trace/video evidence is complete

capability_outcome =
    INFRA_INVALID  if any of its 3 variants remains INFRA_INVALID
    PASS           if all 3 variants are PASS
    CANDIDATE_FAIL otherwise

attempt_outcome in {PASS, CANDIDATE_FAIL, INFRA_INVALID}
cell_outcome    in {PASS, MODEL_FAIL, INCOMPLETE}
```

An attempt is `PASS` only when source/import/build audits pass, every expected logical trial has
exactly one evidence-complete resolved outcome, resolved logical-trial count equals expected logical-
trial count, and every capability is `PASS`. A valid submission that fails an audit, invocation,
metric, or guard is `CANDIDATE_FAIL`. Candidate exception, candidate-caused process exit/crash, and
candidate wall/step-budget timeout are also `CANDIDATE_FAIL`. Provider failure, package failure,
worker launch failure, or Framework IPC/sandbox/host/Harness/recorder failure is `INFRA_INVALID`.
Failure origin is assigned from trusted process/worker evidence, never from candidate claims. A
normally completed model call that submits no admissible driver is candidate-side
`no_valid_submission`.

A cell is `PASS` if an allowed attempt passes; it is `MODEL_FAIL` only after all allowed attempts
terminate with candidate-side failure or `no_valid_submission` and no infrastructure result remains
unresolved. It is `INCOMPLETE` if infrastructure prevents a terminal model verdict. `trial_pass`,
`capability_pass`, `attempt_pass`, and `cell_pass` may be emitted as convenience booleans only for
the corresponding `PASS` outcome; a false boolean without its outcome is not a valid record.

There is no majority vote, averaging across variants, partial capability credit, or compensation by
another capability. Development probes and public smokes are not scored evidence.

Attempt 0 success is reported separately as `pass@0`. Success after Repair is
`final_pass_within_three_attempts`; it must not be presented as first-attempt success.

A backbone is not assigned one normative pass/fail label. Report `passed / completed / planned`
cells, separately by robot and generation condition, plus Repair gain and failure distribution.
Manifest accounting is complete when every planned cell has either a terminal verdict or an
explicit infrastructure blocker. The formal B1 run itself is complete only when all 770 cells have
real terminal verdicts and their required evidence; any blocker leaves the formal run incomplete.
Infrastructure failure remains visible in the planned denominator and is never converted into
driver failure.

### 10.3 Shared physical-integrity gates

Every trial additionally requires:

- candidate uses the canonical Framework `model`/`data` and does not create or replace a session;
- no direct write to `qpos`, `qvel`, body/site pose, contact, sensor, simulation time, or model
  parameters; no teleport or candidate reset;
- finite state and control at every Harness sample;
- actuator commands and finite joint states remain inside declared ranges;
- at least two state-dependent feedback iterations, each ordered as fresh read, control computation,
  `data.ctrl` write, `mj_step`, fresh read;
- for motion-changing capabilities, nontrivial requested motion occurs; for maintenance capabilities
  G5 and L5, the registered disturbance occurs and the trace proves state-dependent recovery or
  rejection of that disturbance. One-read-many-write, pure waiting, polling, and self-reporting
  cannot pass either class;
- dynamic penetration never exceeds `0.005 m`;
- every observed contact belongs either to the capability-specific target-contact allowlist or to a
  frozen morphology support-contact allowlist; every other contact is unrelated and fails the
  trial. Go2 foot/floor contacts and Stretch wheel/floor contacts are support contacts, not
  capability-target contacts. Base/support integrity and morphology-specific guards pass;
- continuous video and physical trace span reset, invocation, terminal state, and any failure;
- candidate return values and logs contribute no success evidence.

Focused false-success checks must reject both (a) source containing task-ID/name dispatch and (b) a
driver that ignores requests and always replays one fixed trajectory.

The current Authority-wide `0.005 m` maximum penetration remains global, including allowed support
contacts, unless Authority is explicitly revised before freeze. Contact allowlisting changes which
pairs are permitted; it does not waive the penetration guard.

### 10.4 Repair-facing projection

The private suite would cease to be hidden if attempt 0's exact request, reset, trajectory, or video
were returned to Repair and then reused. Therefore:

- the complete report, trace, request, reset, and video are retained by the trusted Framework;
- before the registered round terminates, Repair receives only capability name, public
  metric/threshold, pass count, a fixed coarse error bucket (`within_gate`, `up_to_2x_gate`,
  `over_2x_gate`, or `unavailable`), guard-failure category, and a sanitised exception type;
- Repair does not receive scored target/path values, seed, reset, case ID, raw logs, state trace, or
  video;
- submitted candidate source is immutable during scored execution; every trial receives a distinct
  temporary writable directory which is destroyed afterward, and trials/attempts share no writable
  filesystem or process state;
- only the explicitly white-listed Repair projection may cross from one attempt to the next;
- complete scored requests, resets, traces, and videos remain sealed until every relevant cell in
  the pre-registered B1 manifest is terminal. During that round they may not enter Experience,
  prompts, manual revisions, or later cells;
- a later extension with additional replicates must either keep the evidence sealed through the
  extension or pre-register a new held-out suite before inspecting the earlier suite.

### 10.5 Minimum evidence to record

The trusted record is richer than the Repair projection. It must be sufficient to recompute every
verdict without trusting candidate output:

| Level | Required record |
|---|---|
| Cell/generation | `run_id`, `cell_id`, specification/protocol version, Framework Git commit/version, environment-lock identity, MuJoCo version, robot configuration/package/MJCF identity, backbone, exact provider/model ID and inference settings, condition, replicate, interface/suite ID, fresh provider request/seed/conversation identity, fixed Experience snapshot, and replicate-isolation audit |
| Attempt/submission | `attempt_index`, generate/Repair stage, explicit-submission flag, candidate source and submission time, every model-call ID/status and input/output/cached/reasoning tokens and cost, generation/Repair wall time, Harness validation wall time, source/exact-interface/anti-task-dispatch/condition-isolation/import/build audit results, prior attempt, Repair projection/version, and hidden-input-leak audit |
| Trial identity and trusted input | `trial_id`, capability ID/method, private variant ID, exact request/reset/fixture/disturbance/seed snapshots, scene, invocation sequence, timeout and step budgets; these inputs are marked trusted-private and remain sealed under Section 10.4 |
| Canonical execution | worker/invocation completion, simulation start/end, physics-step and feedback-iteration counts, every `data.ctrl` write, relevant joint/body/site/geom states per physics sample, feedback ordering, timeout/exception and failure origin, direct-state-write, finiteness, range, and canonical-session checks |
| Physical effect | `metric_results[]` with metric ID, observed value, unit, comparator, threshold and metric/temporal/order booleans; requested/measured target/path/aperture/contact/support quantities; per-sample error where scored; threshold-entry/dwell windows, phase order, contact pairs, minimum contact distance/maximum penetration, disturbance response, and morphology guards |
| Evidence integrity | trace/video identity, replay index/reason, temporary-directory isolation, frame count, FPS, decodability, reset-to-terminal interval coverage, and trace/video completeness |
| Coverage/verdict | expected capability count, `expected_logical_trial_count`, `resolved_logical_trial_count`, `physical_execution_count`, `replay_count`, evidence-complete/candidate-failed/infrastructure-invalid logical-trial counts, per-capability outcomes, `trial_outcome`, `attempt_outcome`, `cell_outcome`, convenience pass booleans, `pass@0`, first passing attempt, primary failure class, `no_valid_submission` reason, and infrastructure blocker |

The aggregate report records `passed / completed / planned` cells by robot, backbone, condition,
replicate, and attempt, plus Repair gain and failure-category counts. `cell_completed` is true only
for `PASS` or `MODEL_FAIL`; `INCOMPLETE` stays in the planned denominator. Capability/trial
summaries are descriptive repeated-measure diagnostics, not additional independent samples.

## 11. Required pre-freeze work

This proposal is not executable evidence until the following minimum work is complete:

1. Revise the Authority's task-specific request envelope, exact-one task clustering, B1 Task
   Library exposure, source-task-only capability criterion, and Repair disclosure rules.
2. Replace the current `reach_task`/`contact_task`/task-family benchmark and generated-driver
   prompts with the capability-native ABI above.
3. Materialise one strict JSON Schema per method, including exact safe/reachable numeric domains,
   duration/path bounds, required fields, frames, finite values, and
   `additionalProperties: false`; private targets may vary inside but never extend those domains.
4. Freeze every physical measurement binding: arm/tool/base frames, gripper aperture scalar and
   measured endpoints, pad-centre sites, target-contact groups, and morphology support contacts.
   This includes a Franka TCP instead of indefinite use of the `hand` body origin.
5. Reference-calibrate the proposed Go2 height domain, all maintenance disturbances, Stretch
   whole-body bounds, and the LEAP contact fixtures before sealing cases.
6. Implement one private three-variant suite and task-blind reference driver per profile/robot.
7. Freeze missing gripper directions/endpoints for SO-101, Franka, xArm7, and Stretch, and missing
   tool contact groups for SO-101, Franka, Piper, and Stretch.
8. Replace KUKA's task-ID plan replay and ALOHA's right-arm-only reference path.
9. Add the two focused false-success checks required by Section 10.3 and verify Repair receives only
   the Section 10.4 projection.
10. Run every package reference driver through the exact suite before any formal generated model is
    evaluated.

Until these items are complete, this file is a concrete interface and acceptance proposal, not a
claim that the current B1 runner or fixed bundle already implements it.
