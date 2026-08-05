Review the complete direct-function validation suite for missing coverage,
invalid signatures, data leakage, unmeasurable goals, and weak predicates.
Rewrite the complete suite while preserving independent targets and thresholds.
There must be exactly one case per frozen capability. Correct invalid LeRobot
measurement keys, flattened tolerance keys, object-center heights, or any
non-positive tolerance. Return one complete compact JSON object only.
Reject every body or marker without a valid frozen `asset_ref`, and remove all
inline kind, geometry, mass, material, friction, or collision overrides. Verify
that its pose fields satisfy the referenced Morphology scene asset contract.
Every dynamic cube/cylinder must include `position_m` and
`quaternion_wxyz`; use identity `[1.0, 0.0, 0.0, 0.0]` for upright objects.

`deterministic_check.errors` is an authoritative repair checklist: fix every
listed error, not just the first one. When `deterministic_check.ok` is false,
returning `previous_candidate` unchanged is forbidden; rewrite the implicated
fields and recheck every error against the new JSON. In particular, for
`{"effect":"joint_targets","joint_targets_argument":"joint_targets"}`,
`call_arguments["joint_targets"]` and
`target_measurements["joint_positions"]` must use character-for-character equal
keys, including `.pos`, and equal numeric values. Correct: both contain
`{"shoulder_pan.pos":15.0}`. Incorrect: the call mapping contains
`{"shoulder_pan":15.0}` while the measurement mapping contains
`{"shoulder_pan.pos":15.0}`.

Recheck complementary G3 morphology: `pick_place` uses one vertical cylinder,
`lift` uses one cube, and `ordered_pick_place_sequence` uses exactly one cube
plus one vertical cylinder. Do not add cases; rewrite the existing case bodies,
sources, targets, and exact per-object extents. These shapes exercise the
trusted terminal dynamics and trajectory postconditions that position-only
cube duplication previously missed.

Apply the deterministic physical policy before returning: include `finite: true`
plus the layer-specific measurement (G1 joints, G2 end-effector position, G3
object positions); remove any tolerance for boolean/string targets; require all
layer safety conditions; validate qpos/body shapes and targeted object IDs; and
make every target traceable through the frozen `validation_binding` and
non-trivial relative to the initial state. For G3, ensure each bound public
source equals its free object's authored initial center and derive the target by
the declared relation; approach height is not object lift. Remove all undeclared
suite-level fields.
Resolve singular coordinate arguments as sequences and plural component maps
as explicit x/y/z scalar arguments; never bind a vector to only its x scalar.
Recompute resting center z from the fixed 0.020 m table surface plus half object
height (default 0.030 m cube -> 0.035 m center; 0.040 m cylinder -> 0.040 m
center), and keep that center z for planar source-to-target cases.
Recheck auxiliary geometry bindings: exact object extent must agree with the
authored body, and the declared contact-height reference must resolve to a
point inside that body's vertical span.
For object move sequences, recheck that `source_field` and `target_field` are
used as fixed literal keys in every move item. They are not public selector
arguments, so remove any invented selector call arguments.
Recheck the declared literal `object_extent_field` independently for every
matched move: its positive value must equal that authored body's geometry under
the declared `object_extent_semantics`.

Reject filename-unsafe case IDs, extra measurements, targets outside the
trusted action/workspace envelope, tolerances outside the reference ranges, and
case timeouts below the per-module floor. Treat `case.timeout_s` only as the
trusted host-monotonic harness hard deadline. It is independent of public
timeouts/durations, which use MuJoCo simulation time in this P0, so never impose
a numeric margin across them. Omit every optional defaulted public
timeout/duration from nominal `call_arguments` so the frozen public default is
actually exercised.
