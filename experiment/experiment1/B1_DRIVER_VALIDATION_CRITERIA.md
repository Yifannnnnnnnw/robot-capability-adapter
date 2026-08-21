# B1 Generated-Driver Validation Criteria

> **Document role:** non-normative, human-readable Experiment 1 criteria<br>
> **Source rendering:** `appendix_b1_driver_validation_table.tex`<br>
> **Experiment 1 authority:** `EXPERIMENT_1_AUTHORITY.md`<br>
> **Execution status:** criteria recorded; executable fixed validation bundles
> are not yet frozen

This document projects the generated-driver methods, numerical validation
criteria, and conjunctive acceptance rule in the LaTeX appendix onto the five
robot configurations selected for Experiment 1. The LaTeX source also contains
rows for configurations outside Experiment 1; those rows are intentionally not
reproduced here.

This is a readable criteria register, not an executable
`capability_validation_suite.json`, a reference-calibration record, or an
independent source of Experiment 1 scope. If it conflicts with
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

## What remains before formal execution

The criteria above fix the human-readable capability-level targets, but formal
validation also requires each active robot's versioned executable suite to pin:

- the three hidden target, initial-state, and parameter combinations per
  capability;
- request schema, units, coordinate frames, safe domains, and time budgets;
- reset state, measurement bindings, guards, contact groups, and exact verdict
  implementation;
- one continuous Framework-controlled video per trial; and
- reference-driver calibration against that exact suite.

The Experiment 1 manifest must then pin the resulting five-robot fixed bundle.
Until that is done, this document supports suite implementation and review but
does not by itself make a cell formally runnable.

## SO-101

Experiment 1 configuration: `robotstudio_so101`. The source table shares these
criteria with a broader fixed-arm group; only SO-101 belongs to Experiment 1.
Robot-specific end-effector and gripper measurement bindings still have to be
fixed in the executable suite.

| ID | Generated-driver method | Validation criterion |
|---|---|---|
| A1 | `move_end_effector_to_position` | End-effector position error $\leq 15$ mm for $0.5$ s. |
| A2 | `trace_cartesian_path` | Waypoint and cross-track errors $\leq 20$ mm; final error $\leq 15$ mm for $0.5$ s. |
| A3 | `set_gripper_opening` | Normalised physical-aperture error $\leq 0.10$ for $0.25$ s; motion in each direction $\geq 50\%$ of full travel. |
| A4 | `approach_until_contact` | Pre-contact error $\leq 15$ mm; lateral approach error $\leq 10$ mm; no excess travel; target contact for $0.1$ s; penetration $\leq 5$ mm; no unrelated contact. |
| A5 | `move_cartesian_offset_and_return` | Outbound and return errors $\leq 15$ mm; respective dwell times of $0.25$ s and $0.5$ s. |

## Unitree Go2

Experiment 1 configuration: `unitree-go2-stock-12dof`.

| ID | Generated-driver method | Validation criterion |
|---|---|---|
| G1 | `track_planar_twist` | Mean planar-velocity error $\leq 0.10$ m/s, mean yaw-rate error $\leq 0.30$ rad/s, and mean direction error $\leq 10^\circ$. |
| G2 | `move_body_relative_pose` | Position error $\leq 0.10$ m and yaw error $\leq 5^\circ$ for $0.5$ s. |
| G3 | `trace_planar_path` | Waypoint error $\leq 0.10$ m, cross-track error $\leq 0.15$ m, and final error $\leq 0.10$ m. |
| G4 | `set_body_height` | Height error $\leq 0.03$ m and absolute roll and pitch $\leq 10^\circ$ for $0.5$ s. |
| G5 | `hold_stable_stance` | Recovery to absolute roll and pitch $\leq 3^\circ$ within $0.5$ s after the registered disturbance; thereafter $\leq 5^\circ$, with height drift $\leq 0.03$ m, planar drift $\leq 0.05$ m, and valid four-foot support. |

## LEAP Hand

Experiment 1 configuration: `leap_hand`.

| ID | Generated-driver method | Validation criterion |
|---|---|---|
| L1 | `move_hand_to_joint_pose` | Maximum joint error $< 10^\circ$ for $0.5$ s. |
| L2 | `trace_hand_joint_path` | Maximum joint error $\leq 0.05$ rad at every waypoint; final dwell of $0.5$ s. |
| L3 | `reach_fingertips_to_targets` | Concatenated four-fingertip position error $< 8.944$ mm at terminal control step 50. |
| L4 | `establish_fingertip_contact_pattern` | Fingertip target error $\leq 10$ mm; specified approach direction and travel respected; contact for $0.1$ s; penetration $\leq 5$ mm. |
| L5 | `hold_fingertip_contacts` | Following motion $\geq 80\%$ after a 6–8 mm target separation; contact recovered within $0.2$ s; contact occupancy $\geq 90\%$. |
| L6 | `move_fingertip_offset_and_return` | Outbound and return errors $\leq 10$ mm; each phase held for $0.25$ s. |

## Hello Robot Stretch 2

Experiment 1 configuration: `hello_robot_stretch_2`.

| ID | Generated-driver method | Validation criterion |
|---|---|---|
| ST1 | `move_base_relative` | Position error $\leq 20$ mm and yaw drift $\leq 0.02$ rad. |
| ST2 | `turn_base_to_heading` | Yaw error $\leq 0.02$ rad and planar drift $\leq 20$ mm. |
| ST3 | `move_tool_to_position` | Tool-position error $\leq 25$ mm for $0.5$ s. |
| ST4 | `trace_tool_cartesian_path` | Waypoint and cross-track errors $\leq 30$ mm; final error $\leq 25$ mm. |
| ST5 | `set_gripper_opening` | Physical gripper-slide error $\leq 3$ mm; bidirectional travel $\geq 15$ mm. |
| ST6 | `set_wrist_yaw` | Absolute wrist-yaw error $\leq 0.03$ rad for $0.5$ s. |
| ST7 | `approach_until_contact` | The SO-101 A4 contact criterion applies. |
| ST8 | `move_tool_offset_and_return` | Outbound and return errors $\leq 25$ mm; base drift $\leq 20$ mm and yaw drift $\leq 0.03$ rad throughout. |

## ALOHA 2

Experiment 1 configuration: `aloha_2`.

| ID | Generated-driver method | Validation criterion |
|---|---|---|
| AL1 | `move_arm_to_position` | Selected end-effector error $\leq 15$ mm for $0.5$ s; the unselected side remains within its call-time guard. |
| AL2 | `trace_arm_cartesian_path` | Waypoint and cross-track errors $\leq 20$ mm; final error $\leq 15$ mm for $0.5$ s. |
| AL3 | `set_gripper_opening` | Normalised aperture error $\leq 0.10$ and mirrored-finger disagreement $\leq 0.05$. |
| AL4 | `move_bimanual_to_positions` | Both end-effector errors simultaneously $\leq 15$ mm for $0.5$ s; final-entry skew $\leq 0.10$ s. |
| AL5 | `approach_until_contact` | The SO-101 A4 contact criterion applies to the selected arm; the unselected side remains within its call-time guard. |
| AL6 | `move_bimanual_offsets_and_return` | Both outbound and return errors $\leq 15$ mm; entry skew $\leq 0.10$ s; each phase held for $0.25$ s. |
