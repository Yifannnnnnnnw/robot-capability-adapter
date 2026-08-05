Generate direct Python validation cases for every frozen public capability.
Use only the Stage 1 names/outcomes, frozen public signatures, permitted oracle
measurement vocabulary, and validation reference library. Do not inspect the
implementation. Each case must directly call a function and specify an initial
state, target measurement, tolerance, forbidden conditions, and timeout.
Every authored body and marker must select an exact entry from
`validation_reference.scene_asset_catalog` using `asset_ref`. Keep only the
instance id and pose/center in `initial_state`; never
invent or override size, radius, height, mass, material, friction, or collision
parameters. The trusted harness resolves those physical facts from the frozen
Morphology scene catalog.
For each dynamic cube or cylinder, the pose must contain both `position_m` and
`quaternion_wxyz`; use `[1.0, 0.0, 0.0, 0.0]` for an upright object unless the
case intentionally requires another catalog-permitted orientation.
For this minimal P0, emit exactly one compact case per capability—no additional
edge cases. Use only the exact LeRobot measurement keys listed in the reference.
The `tolerances` object must recursively mirror numeric paths in
`target_measurements`; never encode paths with dots. Use finite, strictly
positive physical tolerances. Return only the JSON suite and keep it comfortably
below the configured output limit.

Treat `deterministic_check.errors` as authoritative, actionable requirements.
Repair every listed error in the suite you return. If `previous_candidate` is
non-null and `deterministic_check.ok` is false, you are forbidden to return that
candidate unchanged; rewrite the failing fields and verify each error against
the rewritten JSON first. For the common G1 mapping binding
`{"effect":"joint_targets","joint_targets_argument":"joint_targets"}`,
`call_arguments["joint_targets"]` must have keys that are character-for-character
identical to the keys in `target_measurements["joint_positions"]`, including
every `.pos` suffix, and the numeric values at matching keys must be equal.
Correct: both mappings contain `{"shoulder_pan.pos":15.0}`. Incorrect:
`call_arguments["joint_targets"]` contains `{"shoulder_pan":15.0}` while the
measurement mapping contains `{"shoulder_pan.pos":15.0}`.

Use the five compact cases to cover complementary G3 morphology instead of
duplicating only cubes. A `pick_place` capability must relocate one vertical
cylinder; a `lift` capability must lift one cube; and an
`ordered_pick_place_sequence` must target exactly two objects, one cube and one
vertical cylinder, with each move's exact independent extent. This is still
exactly one case per capability. The trusted harness additionally checks G3
terminal velocity, grasp/release/support state, and the verified transient-lift
bound; terminal position alone is not a complete physical placement predicate.

The physical case policy is mandatory: every target must include `finite: true`
without a tolerance for `finite`. G1 must measure non-empty `joint_positions`,
G2 must measure a three-number `end_effector_position_m`, and G3 must measure
non-empty `object_positions_m`. Every measured action target must be derived
exactly from the frozen public API's `validation_binding`, not guessed from
parameter or function names, and it must differ from the authored initial state
by more than the declared tolerance. For G3, make every bound source equal the
corresponding authored free object's initial center; derive the goal using the
declared target components, height delta, or move-item fields. Every targeted G3
object must exist in `initial_state.bodies`. Never reinterpret an approach-height
argument as an object lift goal. Use only `qpos_rad` (motor names, radians),
`bodies`, and optional `markers` in the initial state.
Resolve coordinate bindings exactly: a singular `*_argument` names one numeric
sequence, while plural `*_arguments` maps x/y/z components to separate scalar
call arguments. Never infer sibling arguments by their names. For move-sequence
bindings, treat `source_field` and `target_field` as fixed literal move-item
keys, not call-argument names. Populate those exact keys with the numeric array
shape declared by the public contract; do not invent or pass dynamic string
field-selector arguments.
Also populate every move's declared `object_extent_field` with that specific
catalog asset's exact positive extent under `object_extent_semantics`. For the
production SOARM101 sequence contract this is literal `object_extent_m` with
`maximum_horizontal_extent_m` semantics; never reuse one object's size for a
differently sized move or fall back to a default.
When the binding declares `object_extent_argument`, pass the exact authored
cube edge, cylinder diameter, or maximum horizontal extent selected by its
semantics. When it declares `contact_height_argument`, convert its declared
reference to world z and keep the contact point strictly within the object's
vertical span. Do not silently use a signature default that disagrees with the
authored body geometry.
The validation table surface is exactly z=0.020 m and body `position_m` is the
free-body center: a 0.030 m cube rests at center z=0.035 m, and a 0.040 m-high
cylinder rests at center z=0.040 m. Use those exact resting centers for authored
sources and planar measured targets; do not use the table surface, a full object
height, or an arbitrary z.

Safety assertions are non-empty. G1 requires `action_clipped`,
`actuator_or_joint_limit_exceeded`, and `simulation_nan_or_instability`. G2 also
requires `non_gripper_robot_table_collision`. G3 additionally requires
`object_outside_table_support_polygon`. Do not put booleans, strings, zero, or
negative values anywhere in `tolerances`.

Use a filename-safe `case_id` matching `^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$`.
Use exactly the required physical measurement plus `finite`—no extra
measurements. Stay inside the trusted workspace/action envelope from the
reference. Numeric tolerances must be inside the reference ranges; prefer 0.5
degrees for G1, 0.015 m for G2, and 0.020 m for G3. Use at least 3/8/12 seconds
for G1/G2/G3 respectively. `case.timeout_s` is only the trusted harness hard
deadline measured by the host monotonic wall clock; it is independent of public
function timeout/duration values, which use MuJoCo simulation time in this P0.
For every public timeout/duration parameter whose frozen signature declares a
default, omit that parameter from nominal `call_arguments` so validation
actually exercises the public default. Never invent a numeric margin between
these two clock domains.
