# B1 Generated-Driver Validation Criteria

> **Document role:** public Experiment 1 Driver-contract and validation-criteria register<br>
> **Source rendering:** `appendix_b1_driver_validation_table.tex`<br>
> **Experiment 1 authority:** `EXPERIMENT_1_AUTHORITY.md`<br>
> **Execution status:** five fixed capability designs and their H1/H2/H3
> trusted-Harness suites are connected through `fixed_validation_bundles/`

This document projects the generated-driver methods, numerical validation
criteria, and conjunctive acceptance rule in the LaTeX appendix onto the five
robot configurations selected for Experiment 1. The LaTeX source also contains
rows for configurations outside Experiment 1; those rows are intentionally not
reproduced here.

This is the readable public criteria register required by the Authority, not
an independent source of Experiment 1 scope or a disclosure of hidden private
targets/resets. If it conflicts with
`EXPERIMENT_1_AUTHORITY.md`, the Authority governs.

## Experiment 1 applicability

| Experiment 1 configuration | Criteria used | Capability count |
|---|---:|---:|
| `robotstudio_so101` | A1–A5 | 5 |
| `unitree-go2-stock-12dof` | G1–G5 | 5 |
| `leap_hand` | L1–L6 | 6 |
| `hello_robot_stretch_2` | ST1–ST8 | 8 |
| `aloha_2` | AL1–AL6 | 6 |
| **Total** |  | **30** |

With three hidden cases per capability, one complete validation attempt across
the five Experiment 1 robots contains 90 trials.

## Common Driver ABI

Every candidate is one isolated, model-generated `driver.py` with:

```python
def build(*, model, data): ...

class Driver:
    def <capability_method>(self, request): ...
```

`build` binds the exact Framework-owned canonical MuJoCo `model` and `data`.
Every capability method accepts exactly one request object. All fields listed
below are required; unlisted properties are rejected. All numbers must be
finite. The Driver must use fresh physical observations in a bounded feedback
loop, write through `data.ctrl`, and advance the same canonical session through
real MuJoCo physics. A return value or self-reported success never substitutes
for the trusted Harness verdict. Task-, scene-, case-, source-, or hidden-value
dispatch is forbidden.

For every `max_duration_per_segment_s`, segment 1 starts at invocation and a
later segment starts at first qualifying entry into the preceding waypoint.
The terminal dwell is a separate Harness interval and is not charged to the
last segment. Private cases use only values inside the public package-bound
safe/reachable domains; the concrete H1/H2/H3 values and resets remain hidden.

## Common acceptance rule

Each capability is evaluated on three hidden combinations of target, initial
state, and parameters. A capability passes only if all three cases pass. A
generated driver passes B1 only if every capability assigned to its robot
passes.

Every trial must also satisfy the common:

- closed-loop check;
- canonical-physics check;
- control-range check;
- unintended-motion check;
- contact-integrity check;
- maximum-penetration check; and
- complete-video check.

## Executable bundle connection

The Experiment 1 manifest points to the five fixed Driver-and-criteria
definitions in `fixed_validation_bundles/`. Each robot bundle contains the
closed request schemas and exactly three fixed H1/H2/H3 cases per capability,
including scene, reset, request, measurement binding, guards, criterion, and
execution budgets. The trusted Harness consumes those inline cases and retains
one continuous Framework-controlled video per trial. The directory remains an
implementation container, not a separate admission workflow; no task-blind
reference driver or reference calibration is required.

## SO-101

Experiment 1 configuration: `robotstudio_so101`. The source table shares these
criteria with a broader fixed-arm group; only SO-101 belongs to Experiment 1.
The executable suite binds the end effector to site `gripperframe` and the
gripper to joint `gripper`.

| ID | Generated-driver method | Required request fields | Validation criterion |
|---|---|---|---|
| A1 | `move_end_effector_to_position` | `target_position_m: number[3]` in world frame; `max_duration_s` | End-effector position error $\leq 15$ mm continuously for $0.5$ s. |
| A2 | `trace_cartesian_path` | `waypoints_m: number[N][3]` in world frame, $2\leq N\leq8$; `max_duration_per_segment_s` | Every waypoint entered in order within $20$ mm and within its segment budget; maximum trace-phase cross-track error $\leq20$ mm; final error $\leq15$ mm continuously for $0.5$ s. |
| A3 | `set_gripper_opening` | `opening_fraction` in $[0,1]$, where 0 is closed and 1 is open; `max_duration_s` | Normalised physical-aperture error $\leq0.10$ for $0.25$ s; each hidden case calls both directions and measured excursion in each direction is $\geq50\%$ of declared full travel. |
| A4 | `approach_until_contact` | `precontact_position_m: number[3]` in world frame; `approach_direction_unit: number[3]` in world frame; `max_travel_m`; `max_approach_speed_m_s` in $(0,0.05]$; `max_duration_s` | Ordered precontact, ray approach, and contact gates pass; allowed tool-target contact persists $0.1$ s; post-contact tool-point speed $\leq0.02$ m/s; penetration $\leq5$ mm; unrelated-contact count 0. |
| A5 | `move_cartesian_offset_and_return` | `offset_robot_base_m: number[3]`; `max_duration_per_leg_s` | Outbound error $\leq15$ mm for $0.25$ s, then return error $\leq15$ mm for $0.5$ s; correct phase order; each leg within budget; maximum displacement $\geq80\%$ of requested offset. |

SO-101 binds the controlled end effector to site `gripperframe` and the
physical aperture to joint `gripper`. A1/A2/A4/A5 hold normalised gripper
aperture within `0.05` of call time. A3 holds end-effector position within
`0.015 m` and every arm joint within `0.03 rad` of call time. For A4, the
precontact point must be held within `0.015 m` for `0.10 s` without target
contact; axial progress may range only from `-0.002 m` to
`max_travel_m + 0.002 m`, may not backtrack more than `0.002 m`, lateral
deviation is at most `0.010 m`, and accumulated path length is at most
`1.10 * max_travel_m`.

## Unitree Go2

Experiment 1 configuration: `unitree-go2-stock-12dof`.

| ID | Generated-driver method | Required request fields | Validation criterion |
|---|---|---|---|
| G1 | `track_planar_twist` | `linear_velocity_body_m_s: number[2]`; `yaw_rate_rad_s`; `duration_s` | Over the final $1.0$ s, mean planar velocity-vector error $\leq0.10$ m/s and mean yaw-rate error $\leq0.30$ rad/s; when requested speed is at least $0.20$ m/s, mean direction error $\leq10^\circ$. |
| G2 | `move_body_relative_pose` | `translation_initial_yaw_m: number[2]`; `yaw_delta_rad`; `max_duration_s` | Terminal planar-position error $\leq0.10$ m, wrapped yaw error $\leq0.0873$ rad, and planar speed $\leq0.10$ m/s continuously for $0.5$ s. |
| G3 | `trace_planar_path` | `waypoints_initial_yaw_m: number[N][2]`, $2\leq N\leq8$; `max_duration_s` | Waypoints reached in order within $0.10$ m; maximum cross-track error $\leq0.15$ m; endpoint error and speed each $\leq0.10$ in their stated units for $0.5$ s. |
| G4 | `set_body_height` | `target_height_m` in $[0.22,0.36]$; `max_duration_s` | Height error $\leq0.03$ m, absolute roll/pitch each $\leq0.1745$ rad, planar displacement $\leq0.05$ m, and yaw drift $\leq0.0873$ rad continuously for $0.5$ s. |
| G5 | `hold_stable_stance` | `duration_s` in $[1.0,2.0]$ | After one registered roll/pitch reset disturbance, recover to absolute roll/pitch $\leq0.0524$ rad within $0.5$ s; thereafter each $\leq0.0873$ rad, height drift $\leq0.03$ m, planar drift $\leq0.05$ m, mean planar speed $\leq0.05$ m/s, terminal vertical speed $\leq0.05$ m/s, valid four-foot support, and no base/head-floor contact. |

G1 interprets the requested velocity in the instantaneous horizontal body-yaw
frame at every physics step. Its scored public domain is planar-speed norm 0
or `[0.20, 0.40] m/s`, yaw-rate magnitude 0 or `[0.20, 1.00] rad/s`, duration
`[2.0, 4.0] s`, with at least one non-zero component. G5 starts from
four-foot support; immediately before invocation the Framework applies exactly
one sealed world-frame roll or pitch disturbance of signed magnitude
`[0.0873, 0.1396] rad`. Each foot has allowed floor support in at least 90% of
post-disturbance samples and no continuous support loss exceeds `0.10 s`.

## LEAP Hand

Experiment 1 configuration: `leap_hand`.

| ID | Generated-driver method | Required request fields | Validation criterion |
|---|---|---|---|
| L1 | `move_hand_to_joint_pose` | `target_joint_positions_rad: number[16]`; `max_duration_s` | Maximum absolute joint error $<0.1745329252$ rad within 4 s and at every physics sample of a subsequent $0.5$ s hold. |
| L2 | `trace_hand_joint_path` | `joint_waypoints_rad: number[N][16]`, $2\leq N\leq8$; `max_duration_per_segment_s` | Every waypoint entered in order with maximum joint error $\leq0.05$ rad before its segment budget expires; final $0.5$ s hold also passes at every physics sample. |
| L3 | `reach_fingertips_to_targets` | `target_fingertip_positions_palm_m: number[12]` in index/middle/ring/thumb xyz order; `max_control_steps: 50` (constant) | Concatenated four-fingertip Cartesian L2 error $<0.00894427191$ m at terminal control step 50. |
| L4 | `establish_fingertip_contact_pattern` | `required_fingers: unique enum[k]`; `contact_target_positions_palm_m: number[k][3]`; `approach_directions_palm_unit: number[k][3]`; `max_travel_m`; `max_approach_speed_m_s` in $(0,0.05]$; `max_duration_s` | Every requested fingertip passes its ray gate, enters the $0.010$ m target region, and contacts its aligned private target for $0.10$ s; nonrequested-finger guards pass; penetration $\leq0.005$ m. |
| L5 | `hold_fingertip_contacts` | `required_fingers: unique enum[k]`; `separation_directions_palm_unit: number[k][3]`; `duration_s` in $[1.0,2.0]$ | After the registered separation, each disturbed fingertip follows $\geq80\%$ of target displacement and restores relative gap $\leq0.002$ m within $0.20$ s; contact occupancy $\geq90\%$, no continuous loss $>0.20$ s, and nonrequested-finger guards pass. |
| L6 | `move_fingertip_offset_and_return` | `finger_names: unique enum[k]`; `offsets_palm_m: number[k][3]`; `max_duration_per_leg_s` | Selected fingertip outbound/return errors each $\leq0.010$ m; ordered phases within leg budgets, each held $0.25$ s; unselected-finger guards pass. |

For L4–L6, `1 <= k <= 4` and array order matches the named-finger order.
For each L4 finger, axial progress remains in `[0, max_travel_m]`, lateral
deviation is at most `0.010 m`, path length at most `1.10 * max_travel_m`, and
contact relative speed at most `max_approach_speed_m_s + 0.01 m/s`. L5 begins
with $0.10$ s established contact; at $0.25$ s the Framework moves at least one
target smoothly over $0.05$ s by a sealed $[0.006,0.008]$ m distance. Every
nonrequested fingertip moves at most `0.010 m` and each of its four joints at
most `0.05 rad` from call time.

## Hello Robot Stretch 2

Experiment 1 configuration: `hello_robot_stretch_2`.

| ID | Generated-driver method | Required request fields | Validation criterion |
|---|---|---|---|
| ST1 | `move_base_relative` | `translation_initial_yaw_m: number[2]`; `max_duration_s` | Planar-position error $\leq0.020$ m, yaw drift $\leq0.020$ rad, and planar speed $\leq0.020$ m/s for $0.5$ s. |
| ST2 | `turn_base_to_heading` | `target_yaw_world_rad`; `max_duration_s` | Wrapped yaw error $\leq0.020$ rad, planar drift $\leq0.020$ m, and yaw rate $\leq0.020$ rad/s for $0.5$ s. |
| ST3 | `move_tool_to_position` | `target_position_world_m: number[3]`; `max_duration_s` | Tool-position error $\leq0.025$ m continuously for $0.5$ s; side-effect guards pass. |
| ST4 | `trace_tool_cartesian_path` | `waypoints_world_m: number[N][3]`, $2\leq N\leq8$; `max_duration_per_segment_s` | Ordered waypoint/cross-track error $\leq0.030$ m; segments meet budgets; endpoint error $\leq0.025$ m for $0.5$ s; side-effect guards pass. |
| ST5 | `set_gripper_opening` | `opening_fraction` in $[0,1]$; `max_duration_s` | Physical gripper-slide error $\leq0.003$ m for $0.25$ s; hidden bidirectional excursion $\geq0.015$ m. |
| ST6 | `set_wrist_yaw` | `target_yaw_rad`; `max_duration_s` | Absolute, non-wrapped wrist-joint error $\leq0.030$ rad for $0.5$ s. |
| ST7 | `approach_until_contact` | Same five geometry/time fields as A4 | The complete A4 positive-contact, ray, speed, stopping, unrelated-contact, and penetration gates apply to the Stretch tool binding. |
| ST8 | `move_tool_offset_and_return` | `offset_call_time_base_m: number[3]`; `max_duration_per_leg_s` | Outbound/return errors each $\leq0.025$ m, in order and within budgets; holds $0.25$ s/$0.5$ s; base remains within $0.020$ m and $0.030$ rad of call-time pose throughout. |

ST3/ST4 may move the base at most `0.10 m` and `0.35 rad` from call time.
ST5 maps opening fraction to physical joint target
`-0.005 m + 0.045 m * opening_fraction`; hidden calls differ by at least 0.50.
Both head joints stay within `0.03 rad` for every method. ST1/ST2 additionally
hold lift/extension within `0.01 m`, wrist within `0.03 rad`, and gripper slide
within `0.003 m`. ST3/ST4/ST7 hold wrist, gripper, and head at those bounds.
ST5/ST6 hold base within `0.02 m`/`0.03 rad` and lift/extension within `0.01 m`.
ST8 also holds wrist, gripper, and head at the common bounds.

## ALOHA 2

Experiment 1 configuration: `aloha_2`.

| ID | Generated-driver method | Required request fields | Validation criterion |
|---|---|---|---|
| AL1 | `move_arm_to_position` | `arm: enum["left","right"]`; `target_position_world_m: number[3]`; `max_duration_s` | Selected end-effector error $\leq0.015$ m for $0.5$ s; every unselected-side guard passes. |
| AL2 | `trace_arm_cartesian_path` | `arm`; `waypoints_world_m: number[N][3]`, $2\leq N\leq8$; `max_duration_per_segment_s` | Ordered waypoint/cross-track error $\leq0.020$ m; each segment meets its budget; endpoint error $\leq0.015$ m throughout an additional $0.5$ s dwell; unselected-side guards pass. |
| AL3 | `set_gripper_opening` | `arm`; `opening_fraction` in $[0,1]$; `max_duration_s` | Selected normalised aperture error $\leq0.10$ for $0.25$ s; mirrored-finger normalised disagreement $\leq0.05$; unselected-side guards pass. |
| AL4 | `move_bimanual_to_positions` | `left_target_position_world_m: number[3]`; `right_target_position_world_m: number[3]`; `max_duration_s` | Both end-effector errors simultaneously $\leq0.015$ m for $0.5$ s; final-dwell first-entry time difference $\leq0.10$ s. |
| AL5 | `approach_until_contact` | `arm` plus the five A4 geometry/time fields | Complete A4 gates applied to the selected arm/contact group; unselected-side guards pass. |
| AL6 | `move_bimanual_offsets_and_return` | `left_offset_arm_base_m: number[3]`; `right_offset_arm_base_m: number[3]`; `max_duration_per_leg_s` | Both arms complete outbound then return within leg budgets; every per-arm error $\leq0.015$ m; outbound final-entry skew $\leq0.10$ s; each phase holds $0.25$ s. |

For AL1/AL2/AL3/AL5 the unselected side stays within: end-effector drift
`0.015 m`, finger displacement `0.0005 m`, and call-time joint drift
`0.002 rad` waist, `0.003 rad` shoulder, `0.030 rad` elbow, `0.002 rad`
forearm roll, `0.035 rad` wrist angle, and `0.002 rad` wrist rotate.
AL1/AL2/AL4/AL5/AL6 hold selected gripper fingers within `0.0005 m`; AL3
holds selected end-effector position within `0.015 m` and applies the same
selected-arm joint-drift limits. AL3 maps opening fraction to
`0.002 m + 0.035 m * opening_fraction` and normalises both controlled and
mirrored-finger measurements by `0.035 m`.
