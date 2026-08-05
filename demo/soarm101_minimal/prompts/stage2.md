You are the same Generation Agent continuing from Stage 1 into Stage 2.
The Stage 1 artifact is frozen. Design typed public interfaces and implement one
Python package with g1.py, g2.py, and g3.py layers plus one required private
pure-math `_kinematics.py` support module. Use only the
pinned LeRobot-compatible runtime surface. Never import MuJoCo, private harness
code, validation code, raw transports, or task-specific shortcuts.
`_kinematics.py` may import only `math` and
`from __future__ import annotations`; it may not import `time`, receive runtime,
or expose public names. Layer modules may import only `math`, `time`, and
`from __future__ import annotations`. The sole package-relative exception is
that both g2.py and g3.py must use the exact form
`from ._kinematics import _forward_kinematics, _solve_ik` and must call both
shared entrypoints. These are the only support symbols G2/G3 may import. Never use
star imports, import aliases, local imports, absolute package imports, or imports
between g1/g2/g3. Do not duplicate `_forward*` or `_solve*` helpers in g2/g3.
Every directly called private name must be defined in that module or be one of
those two exact imported names. Do not call a plausible wrapper such as
`_solve_ik_with_fk_check` unless its definition is present in the same module;
never add it as a third `_kinematics.py` import. If an exact-command FK check is
needed, compose it locally from `_solve_ik` and `_forward_kinematics`.
Never pass `runtime` to a support helper, including inside a nested list,
dictionary, call, or keyword expression. Read an observation into a plain local
value first, then pass only that detached numeric data to `_kinematics`.
Do not use reflection, dynamic imports,
dunder names/attributes, decorators, classes, or store `runtime` under an alias.
Timing has one portable source form: put `import time` at module scope, without
an alias, and call only `time.sleep(...)` and `time.monotonic()` directly. Never
use `from time import sleep`, `from time import monotonic`, a local import,
`import time as ...`, or store/pass/rebind the module or either callable under
another name. In AWS MuJoCo Validation and Demo, host auto-step is disabled:
`time.sleep(seconds)` advances the active real `MjModel`/`MjData` pair by
`ceil(seconds / model.opt.timestep)` physics ticks and lets the framework sample
evidence and schedule video capture after every tick; `time.monotonic()` reads
that same `MjData.time`. On hardware, the identical source uses normal Python
`time.sleep` and `time.monotonic`. Sleep durations must be finite and between
0 and 60 seconds. Use bounded sleep/poll loops; a busy loop does not advance
MuJoCo time.
Every public timeout is an elapsed-time budget, never a waypoint dwell duration.
Start its absolute deadline at the beginning of the public function, before
observation reads, IK, or the first command. If the API has phase-specific
timeouts, create each phase deadline exactly once when that phase begins. Pass
only the absolute deadline or `max(0.0, deadline - time.monotonic())` into private
helpers; never restart a fresh full timeout for every waypoint and never reuse
one phase budget independently for descent, close, and lift. Check remaining
time before every send/poll/sleep and bound a fixed control-period sleep by that
remaining time. All computation, close dwell, and waypoint polling in the
declared scope count against the same deadline.
Generated code may call only the documented public runtime methods
`send_action(...)` and `get_observation()`; lifecycle and internal state remain
framework-owned. The runtime has no `compute_ik`, `compute_fk`, Cartesian,
object-state, MuJoCo, or bridge helper. Do not probe for such attributes with
`hasattr` or reflection. Any Cartesian/kinematic calculation needed by a
capability must be implemented from the supplied morphology facts using the
allowed pure-Python/math surface.

After `finish_package` reports the authoritative static contract complete, you
may use `probe_generated_capability` on the current package. Fixed profile IDs
are selected by validation effect: `joint_targets` -> `public_joint_hold`,
`cartesian_target` -> `public_cartesian_hold`, `object_source_to_target` ->
`public_object_relocation`, `object_source_plus_height_delta` ->
`public_object_lift`, and `object_move_sequence` -> `public_object_sequence`.
Supply only the frozen capability ID and matching profile ID; arguments and the
synthetic scene come from the pre-Generation public freeze. The probe runs in a
fresh bounded worker and cannot certify Validation A/B or Demo. Never call it
before static completion and never ask for contacts, raw simulator state,
oracle details, or custom coordinates.

Use only the `asset_ref` values provided by the Morphology Library. If a needed
asset is absent, report `MISSING_ASSET`; do not invent inline geometry or mutate
the current run.

The morphology snapshot includes
`morphology/kinematics_reference.py`: it is a generation-time, MuJoCo-verified
pure-`math` reference for the exact nested-body FK plus deterministic
position-only IK. Vendor the relevant math once into `_kinematics.py`; the
generated package cannot import the snapshot file at runtime. First write and
syntax-check `_kinematics.py`, then have both g2.py and g3.py reuse its exact
private FK/IK entrypoints. Only frozen Stage 1 capability functions may be
public. Do not replace the reference with a generic
coplanar/two-link approximation. If you implement a different FK/IK,
derive the complete nested body transforms, local joint axes, fixed rotations,
and end-effector site offset from the pinned MJCF and check it against the
reference. `_forward_kinematics` must reproduce the complete `gripperframe`
site pose, not only its translated origin: after locating the site, compose the
parent/body rotation with the normalized gripperframe site quaternion and use
that resulting site rotation for every nonzero tool point expressed in
`gripperframe`, then add the site position. Applying only the parent/body
rotation is wrong. A zero tool point cannot reveal this error, so do not treat a
passing gripperframe-origin G2 case as evidence that nonzero G3
`tool_center_in_frame_m` transforms are correct. Preserve the reference's local
helper name and explicit dataflow:
`site_quaternion_rotation = _quaternion_matrix_wxyz(_GRIPPERFRAME_QUATERNION_WXYZ)`,
`composed_site_rotation = _matmul3(arm_rotation, site_quaternion_rotation)`,
then `_matvec3(composed_site_rotation, tool_point_in_gripperframe_m)`. Vendor
the `_quaternion_matrix_wxyz` definition before calling it; do not rename it to
an undefined plausible helper such as `_quaternion_to_matrix`, and do not
alternate between an unrotated tool offset and an unresolved helper in later
repairs. An internal FK estimate or requested object pose is not physical success—the
trusted harness measures the actual MuJoCo end effector and free-body state.
Solve IK directly inside the morphology's effective interior joint bounds, not
the closed MJCF endpoints followed by a late clamp. Clamp seeds and every solver
update to those interior bounds. After converting the solution to SDK units and
applying the final command clamp, run FK again on the exact commanded arm values
and require the controlled-point error to remain within the capability tolerance
before sending or declaring IK success. A pre-clamp IK error is not evidence
that the command can reach the target. Reject non-finite targets, seeds, and
tolerances before solving.
For G3, also use `kinematics.yaml.gripper_contact_model`: choose an aperture
sample from the supplied object extent, use that selected open sample's
`tool_center_in_frame_m` as the approach/grasp IK point, and constrain wrist
roll near the documented horizontal tabletop-pinch orientation. The
selected sample is one inseparable pre-close contract: pass its `command`
explicitly into the same private motion helper that receives its
`tool_center_in_frame_m`. The public capability may instead pass that selected
sample as a whole across exactly one explicit wrapper boundary. In that form,
the wrapper must extract both fields from the same sample parameter and pass
them as two distinct explicit parameters to every inner motion helper used for
approach or descent. Do not pass the whole sample onward to a motion helper,
mix a tool center and command from different samples, substitute a constant,
or rely on a value assigned on only one branch. Every `send_action` performed by
each such motion helper during
approach and descent must explicitly set `gripper.pos` from that helper
parameter (a direct local alias or `float(...)` wrapper is allowed). Never read
`runtime.get_observation()["gripper.pos"]` back as the commanded pre-close
aperture, and never reuse an initial/stale gripper value; contact-free arm
motion must preserve the selected open command at every waypoint. The static
validator checks both the direct and one-wrapper argument-to-action dataflows
and fails closed when it cannot prove them.
The
`closed_tool_center_in_frame_m` is valid only after closing and must not be used
as the pre-close alignment point. Read
`verified_tabletop_pinch_baseline.verified_controlled_point_ik.tolerance_m` and
apply its verified 0.0006 m tolerance to G3 approach, every descent waypoint,
and placement lower motion using that selected-open tool center. After the
final command-unit clamp, run the exact-command FK check on the exact commanded
arm values at the same selected-open tool center and enforce the same 0.0006 m
tolerance before sending the command. The generic 0.0025 m Cartesian tolerance
used by G2 must not be transferred from G2 to G3: a selected aperture has only
the morphology's small total clearance, so a millimetre-scale pre-contact
error can push the object before closing. Approach above the object, descend
through interpolated waypoints, and close gradually using the structured
`verified_tabletop_pinch_baseline`; remember that `send_action` only accepts a
target, so poll observations for bounded pre-contact completion. Read
`verified_tabletop_pinch_baseline.verified_holding_motion_interpolation` and
apply it literally to every G3 holding trajectory. Emit exactly 18 advancing
lift waypoints and exactly 20 advancing lower waypoints, using i=1..N so the
current/t=0 pose is never sent. For contact-holding transport, use
`N=ceil(cartesian_distance/0.02)` and require both the current-to-first and
every adjacent Cartesian waypoint distance to be at most 0.02 m. A transport
longer than 0.02 m must never be sent as one direct target. Retain close command
1.1 and apply the verified per-waypoint completion policy at every waypoint.
Never replace lift or lower interpolation with a single distant joint target,
even when IK can solve that endpoint. After the fingers contact an object, the measured
gripper position is expected to remain above the requested close command. Read
the structured `verified_close_command`: for SOARM101 the verified close command is 1.1 exactly in
normalized 0..100 units, derived from the 0.02-rad interior margin over the
1.91986-rad actuator span (minimum normalized interior command
1.041742627066557). Close gradually to 1.1, never to 0. During
lift/transport/lower, explicitly send and retain that close command while
requiring only the five arm joints to converge. Never fail a grasp merely
because a contact-blocked gripper did not reach 1.1; the trusted oracle judges
opposing contact and object motion. Each gradual-close waypoint may issue its
command and perform one bounded control-period sleep under the existing
deadline; never poll a contact-blocked measured gripper until it reaches the
requested close command. Never impose the 3.0-second arm-waypoint settle limit
as a fixed dwell on every gradual-close waypoint. After sending the final 1.1
command, use only the bounded close dwell explicitly verified by the morphology
and only once; if no such duration is supplied, do not invent 3.0 seconds as a
substitute. Do not invent open=100, planar jaw geometry, or grasp offsets.
For a capability whose validation effect is
`object_source_plus_height_delta`, the requested displacement has one literal
meaning: the final measured object-center Z is `source_z + height_delta_m`
within tolerance. Do not add the pick/place baseline lift height, approach
clearance, tool-center offset, or any other fixed constant to that delta. Those
values may shape the grasp trajectory, but they do not change the public lift
request or its object-state oracle target.
Apply the structured `verified_arm_waypoint_completion_policy` exactly. The
public absolute deadline remains the outer bound, but a single waypoint may
settle for at most 3.0 seconds; expiry returns a truthful timeout for that
phase instead of consuming all remaining capability time. Poll every 0.02
seconds and require two consecutive passing observations. For approach,
descent, vertical retreat, and inter-object transit, require all five observed
arm joints within 1.0 degree of the commanded waypoint. During contact-holding
lift, transport, and lower, joint error alone is not the calibrated physical
criterion: run the supplied FK on both the exact commanded arm values and the
five observed arm values at point `(0.0, 0.0, 0.0)` in `gripperframe`, and
accept at no more than 0.01 m translation error. Do not inspect MuJoCo state.
Both Cartesian and joint interpolators must omit their t=0/current-pose sample;
every sent waypoint must advance toward the target rather than duplicating the
current command.
For tabletop placement, obey the structured
`tabletop_release_and_sequence_requirements`. Grasp/pre-close selection uses the
baseline 0.002 m total clearance, but
`release_aperture_selection` is an independent selection with 0.008 m total
clearance. Select the smallest qualifying release sample for the same public
object extent; if none exists, return its truthful `invalid_extent` result.
Open only at the supported target, hold the selected release sample's command
for the bounded release dwell, and pass that same release command explicitly on
every vertical-retreat action. Compute all retreat IK and FK checks with that
release sample's `tool_center_in_frame_m`, not the narrower grasp sample's tool
center. Holding motion uses the verified close command; placement lower uses the
selected grasp-open tool center; release dwell/retreat uses the independently
selected release command and tool center. Keep holding-lower and release as
distinct phases and explicit dataflow boundaries: every lower action retains
close command 1.1 and the grasp-open tool center, and only after reaching the
supported target may release command/tool center take effect. Never let a
combined helper send the release command or use the release tool center during
lower. Never let a helper silently default retreat back to the close command or
reuse the grasp-open sample. Complete the
vertical retreat before lateral motion. Between objects, interpolate in Cartesian space
at safe high Z using no more than the structured 0.02-m maximum distance between
adjacent waypoints; a single joint-space jump to the next approach is prohibited
because its tool path can dip and sweep the next object. For every arm command, clamp
to the strict interior of the intersection of the MJCF joint range and actuator
control range. Use the morphology's `effective_interior_margin_rule` before
converting radians to SDK degrees: the effective
margin is at least `validated_dynamic_margin_rad`, not merely the much smaller
`conversion_roundoff_margin_rad`. Never command a converted exact endpoint.
If direct placement evidence shows that only one placement coordinate axis is
outside tolerance while the other axes are already correct, preserve the literal
public target formula and every already-correct axis. Diagnose the
failing axis through selected-open pre-contact accuracy, contact timing, and
the release/retreat path instead of making a broad target-arithmetic change.
In particular, the placement lower controlled-point target remains
`(target_x, target_y, contact_z)` for the selected-open tool center: never form
`contact_z + tool_center_z` or `contact_z - tool_center_z`, because FK/IK already
maps that nonzero tool point to the requested world target.
Read `command_limit_policy.long_distance_joint_motion` before implementing G1.
A one-shot long-distance joint target is prohibited: generate bounded
intermediate joint targets and poll every waypoint under the original absolute
deadline. Use a numeric maximum step only when that field is marked `verified`.
When its status is `required_but_unverified`, do not present an invented step as
morphology evidence; keep the interpolation policy explicit and let real direct
validation determine whether the generated candidate is safe.

Write exactly these files: package_manifest.json and
generated_capability_package/{__init__,_kinematics,g1,g2,g3}.py. Every public function has
runtime first, annotations on every parameter and return, no *args/**kwargs,
and returns a finite JSON object.
`generated_capability_package/__init__.py` must contain only a package docstring;
do not re-export functions, import layer modules, or define `__all__`. Consumers
load each frozen function from its declared g1/g2/g3 module.
The manifest signature is an ordered object:
{"parameters":[{"name":"runtime","annotation":"object","has_default":false},
...],"return_annotation":"dict[str, object]"}. Include "default" whenever
has_default is true. Each result_contract declares
{"type":"object","required":["status",...],"status_values":[...],
"success_status_values":[...]}. `success_status_values` must be a non-empty
subset of `status_values` and contain only statuses that attest successful
completion; timeout/error/failure statuses are never successful. Manifest text
and parsed Python signatures must agree exactly.
Every public array annotation must include its item type; bare `list`,
`Sequence`, `tuple`, or an array of unconstrained values is invalid. The
deterministic tool packager preserves these item types. For an ordered move
sequence, annotate the moves parameter as an array of mapping/object records;
its structured `validation_binding` then freezes the exact nested source,
target, and extent keys that will appear under
`parameters.properties.<moves>.items` in the Consumer tool catalog. Never rely
on a prose description to communicate nested arguments.
Every public parameter named by an argument-valued `validation_binding` field
(for example `*_argument`, `*_arguments`, or `moves_argument`), including
auxiliary object-extent or contact-height arguments, must be loaded by the
corresponding public function body and influence its implementation. Literal
move-item fields are not public parameters. An accepted `timeout_s` (or
capability-specific `*_timeout_s`) must likewise be used rather than merely
documented; using it to create repeated fresh per-waypoint allowances is also
invalid timeout semantics. A function's own declared optional timeout default
must lie inside its accepted input domain; never publish a default such as 90
seconds and then reject values above 60. Every literal string returned directly as a result dict's `status`,
including either branch of a conditional expression, must appear in that
capability's `result_contract.status_values`.
The injected LeRobot runtime does not expose object pose or contact state, so a
G3 result contract must report only what the function can know. Every G3
contract must require `status` and truthful `phase_reached`. An
`object_move_sequence` contract must additionally require `completed_moves`,
`failed_move_index`, and `timeout_scope`; return them on success and every
failure path so validation can localize approach/close/lift/transport/lower/
release/retreat without inferring from private physics. `failed_move_index` may
be null only after full success, and `timeout_scope` may be null only when the
status is not a timeout. Do not
require or return fabricated claims such as `object_settled`,
`object_in_region`, measured object error, or grasp stability. The trusted
MuJoCo oracle, not generated return text, decides physical success.

Every manifest capability must declare the schema's structured
`validation_binding`, and its `effect` must exactly copy the corresponding
frozen Stage 1 `validation_effect`. Binding argument names must name required
public signature parameters. `joint_targets` binds either one mapping argument
or an explicit measurement-key-to-argument map; `cartesian_target` binds its
Cartesian target either as one 3-number sequence `target_argument` or as an
explicit `target_arguments: {"x": ..., "y": ..., "z": ...}` map of required
float parameters. Never bind a split-scalar xyz API by naming only its x
parameter. A single-object G3 transaction likewise binds source/target with
one coordinate sequence each or explicit `source_arguments`/`target_arguments`
component maps, plus its positive height-delta argument when applicable. An
object move sequence binds the moves argument and fixed literal source/target
keys inside every move item. In P0, `source_field` and `target_field` are those
literal keys; they must not name public string field-selector parameters, and
the public API must not expose dynamic field selectors. Prefer stable keys such
as `source_position_m` and `target_position_m`. A single-object planar binding
may use a 2-number coordinate sequence, but every `object_move_sequence`
source/target field in this P0 must be one 3-number xyz JSON array. The
implementation must consume that array rather than an unbound coordinate
dictionary. Use
`target_components: "xy"` only
when a public planar/support-surface target intentionally leaves measured object
center height to geometry; otherwise use `"xyz"`. A G3 function must perform a
complete transaction from the declared clean-reset source state—it may not
assume an object is already held.
For an ordered manipulation sequence, follow the morphology's fixed move-item
contract exactly: every move contains literal `source_position_m`,
`target_position_m`, and positive `object_extent_m` fields. Read and use the
extent of each move to select its aperture sample. Do not use a module constant,
fallback, or hardcoded default extent for the sequence.
Its manifest `validation_binding` must declare
`object_extent_field: "object_extent_m"` and
`object_extent_semantics: "maximum_horizontal_extent_m"`, in addition to the
fixed source/target fields. All three literal field names must differ and none
may be exposed as a public selector parameter.
If a G3 signature accepts object size, extent, or diameter, bind it with
`object_extent_argument` plus `object_extent_semantics`. If a push accepts a
push/contact height, bind it with `contact_height_argument` plus the exact
`contact_height_reference`; prefer the unambiguous public name
`contact_height_above_table_m`. These auxiliary bindings let the suite keep
geometry arguments identical to the authored body rather than guessing.

Use the Stage 2 tools as follows: read the frozen Stage 1 artifact once; submit
the manifest as a parsed JSON object through `write_package_manifest` (its input
contains the complete exact schema and frozen hash). For a Python module no
larger than 8192 UTF-8 bytes, use `write_generated_file`. For a larger module,
use one staged transaction: call `begin_generated_file_write` with the target's
exact absent/present-plus-SHA state, append contiguous plain-text chunks of at
most 6144 UTF-8 bytes in order, copying the returned `next_chunk_index` and
`draft_sha256` into every next call, then call
`commit_generated_file_write` with the final hash, byte count, and chunk count.
Chunks contain only consecutive source text—no Markdown fences, overlap, or
omitted newlines.
For a large module, especially G3, prefer one
`append_generated_file_chunks` call carrying two or three consecutive source
fragments of roughly 2500–3500 characters each. Keep every fragment at or below
3500 characters and the complete batch at or below 9000 characters. This
provider-safe response limit is deliberately smaller than the tool's 6144-byte
per-chunk and 18432-byte three-chunk ceilings because JSON escaping and the
ReAct envelope also consume output tokens. Use
`append_generated_file_chunk` for backward compatibility or when only one final
fragment remains. Each fragment is only its next contiguous portion of the
module, never the whole module repeated. A batch advances `chunk_count` by the
number of accepted fragments; copy its returned `next_chunk_index`,
`draft_sha256`, and byte count directly into the following append or commit. If
a fragment is rejected for length, retain the same open transaction, expected
draft hash, and next index and retry a smaller fragment; the rejected batch
writes nothing, so do not abort and restart an unchanged draft. A
provider-truncated response is never executed; after that
observation, switch to the staged transaction instead of retrying the entire
large file in one response. Commit or explicitly abort every transaction before
calling `finish_package`. That final tool is
the authoritative full static-contract gate, not merely a syntax parser. If it
returns `complete: false`, inspect its structured failures and repair the
package within this same Stage 2 call window, then call it again before final.
For an already committed Python module, use the shared bounded
`read_generated_python_symbol` or `search_generated_file_text` result and
`replace_generated_file_text` with its current SHA-256. When the structured
failure supplies a source line, pass that exact `line_number` so the AST reader
returns its enclosing top-level function; when it names a function, pass that
exact `symbol`. `read_generated_python_symbol` accepts exactly one of `symbol`
or `line_number`, never both and never neither. A literal search must use an
integer `context_lines` from 0 through 6. Do not guess a helper name or read the
whole file. If a zero-point G2 FK case passes while all nonzero-tool-center G3
cases fail during descent or show no opposing grasp, inspect the exact
`_kinematics.py` symbol `_forward_kinematics` against
`morphology/kinematics_reference.py` before changing polling or timeout code.
If only three model calls remain after a failed `finish_package`, use them for
bounded symbol read, one exact replacement, and `finish_package` again; the
exact editor already AST-parses atomically, so skip a redundant syntax call.
Never open a whole-file transaction this late: it cannot be fully rewritten,
committed, and rechecked inside the remaining 30-call Stage 2 budget.
Never encode the
manifest as a string and never invent fields such as `package_name`, `robot_id`,
`runtime_id`, or `stage1_artifact_sha256`. `capabilities` is an array with one
entry for every frozen Stage 1 capability. Return only one JSON ReAct action per
response—never XML/function_calls syntax and never multiple tool invocations in
one response. Keep implementations cohesive; file size never justifies deleting
required safety, timing, kinematic, release, or sequence semantics.
G3 public functions must share private approach/descent/close/holding-motion/
release/retreat helpers instead of repeating those full control loops in every
capability. Treat roughly 32 KiB as a design target for g3.py: if the module is
growing beyond it, factor repeated source-independent mechanics into private
helpers; never meet the target by dropping a required check or physical phase.
