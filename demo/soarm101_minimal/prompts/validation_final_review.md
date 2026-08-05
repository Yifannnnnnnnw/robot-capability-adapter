Perform the final independent review of the complete validation suite. Correct
remaining executable or oracle errors without reading generated implementation
source and without weakening targets or tolerances. Return the final suite.
Enforce exactly one case per capability, exact reference measurement names, and
nested finite positive tolerances. Return only the complete compact JSON object.
Require an exact frozen `asset_ref` for every body and marker; retain only id
and pose/center. Kind, geometry, mass, material, friction, and
collision facts must come only from the Morphology scene catalog supplied in
the immutable validation reference.
Require `position_m` and `quaternion_wxyz` for every dynamic cube/cylinder;
upright objects use identity `[1.0, 0.0, 0.0, 0.0]`.

Before returning, use `deterministic_check.errors` as an authoritative
line-by-line repair checklist and fix every error. If
`deterministic_check.ok` is false, returning `previous_candidate` unchanged is
forbidden. Rewrite the failing fields and re-evaluate every listed error. For a
G1 binding of
`{"effect":"joint_targets","joint_targets_argument":"joint_targets"}`,
the keys of `call_arguments["joint_targets"]` must be character-for-character
equal to the keys of `target_measurements["joint_positions"]`, including each
`.pos` suffix, and their values must be equal. Correct: both contain
`{"shoulder_pan.pos":15.0}`. Incorrect: the call mapping contains
`{"shoulder_pan":15.0}` while the measurement mapping contains
`{"shoulder_pan.pos":15.0}`.

Reject duplicated all-cube G3 coverage: the one `pick_place` case must use a
vertical cylinder, the one `lift` case a cube, and the one ordered sequence case
exactly one cube plus one vertical cylinder with independent exact extents.

Reject a suite that could pass a no-op implementation: `finite` alone is never a
capability predicate, targets must correspond exactly to the frozen
`validation_binding`, and G1/G3 targets must be outside the declared tolerance
of their authored baseline. For each G3 case, verify both source-to-reset binding
and goal relation; never treat an approach offset as a lift delta. Check the
complete required safety set for each layer and return only the two suite fields
`schema_version` and `cases`.
Reconstruct every bound coordinate from either its declared sequence argument
or its explicit component map; do not infer y/z from an x argument name.
For move sequences, `source_field` and `target_field` are fixed literal keys in
each move item, never public selector arguments; do not add selector call
arguments.
Require the distinct literal `object_extent_field` in every move and verify its
positive value against that move's authored body using
`object_extent_semantics`; do not accept a shared or default extent.
For the final G3 check, use table z=0.020 m plus half object height: default cube
center z=0.035 m and default cylinder center z=0.040 m. Planar goals preserve
that center z.
Reject any bound object extent that differs from initial-state geometry and any
bound contact height that resolves outside the object's vertical span.

Recheck the filename-safe case ID, exact two-measurement contract, trusted
tolerance range, workspace/action envelope, and 3/8/12-second timeout floors
for the trusted harness. `case.timeout_s` is a host-monotonic wall-clock hard
deadline and is independent of public timeout/duration values, which use MuJoCo
simulation time in this P0; never compare the two numerically. Omit every
optional defaulted public timeout/duration from nominal `call_arguments` so the
frozen public default is exercised.
