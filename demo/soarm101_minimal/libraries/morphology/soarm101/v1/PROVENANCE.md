# SO-ARM101 morphology provenance

This entry is a self-contained, generation-visible robot morphology bundle. It
was filtered from the predecessor repository's
`assets/mjcf/robotstudio_so101` directory at local checkout commit
`40c39e23fbdb5330fcace8757ebb8d7e67ca950a`.

The predecessor model states that it derives from The Robot Studio's public
SO-101 MJCF and lists source revision
`aec17bbc256d1a7342d53aaa4950595d4c30b40d`. The source model and this bundle
are distributed under Apache-2.0; the full license is included in `LICENSE`.

Only `so101.xml` and its 18 referenced robot meshes were selected. No scene,
evaluation object, object placement, task fixture, historical adapter,
validation code, or execution trace was copied. The STL bytes are unchanged.
The only local MJCF change is the packaging-only `meshdir="meshes"` path; the
robot dynamics, kinematics, collision geometry, actuators, sites, and cameras
are unchanged.

`sources.yaml` is the machine-readable provenance record. `compile_probe.json`
records an actual standalone MuJoCo compile of the packaged entrypoint.

`kinematics_reference.py` and the structured contact profile in
`kinematics.yaml` are locally derived morphology evidence, not copied upstream
code and not task data.  The pure-`math` nested-transform FK is regression
checked against the compiled `gripperframe` pose over 250 deterministic random
joint configurations.  Gripper aperture/tool-center samples and the tabletop
pinch baseline were measured from the pinned MJCF and then exercised in real
MuJoCo with framework-owned deterministic stepping through only the public
LeRobot-compatible action and observation facade.  The calibration contains
no visible or pilot-held-out task target coordinates.

The deterministic real-MuJoCo regression
`tests/test_gripper_contact_profile.py::test_command_20_profile_places_releases_and_retreats_with_post_conversion_fk_bound`
is the named local evidence source for the 0.0006 m selected-open
controlled-point bound.  The test physically replays approach, descent,
opposing-jaw close, lift, transport, placement lower, release dwell, vertical
retreat, and settle through the public LeRobot-compatible facade.  It directly
measures the grasp, lift, supported placement, release, retreat, settled-object
state, and forbidden conditions from framework-owned MuJoCo state.

Separately, the same real episode directly checks a numerical transformation
chain at every controlled-point waypoint, including placement lower: apply the
effective-interior joint clamp, convert to SDK degrees, reconstruct the
runtime-accepted radians, and recompute exact reference FK for the same
selected aperture sample's `tool_center_in_frame_m`.  The checked translation
error is no more than 0.0006 m.  Requiring consumers to repeat that fail-closed
check for clearance-critical approach, descent, and lower commands is a safety
contract derived from this local regression.  It is not an upstream
manufacturer claim, copied upstream code, or task data.

Release clearance has a separate local evidence boundary.  The deterministic
real-MuJoCo regression
`tests/test_gripper_release_clearance.py::test_independent_release_aperture_prevents_cylinder_retreat_recontact`
repeats a generic 30 mm vertical-cylinder episode three times through only the
public LeRobot-compatible action/observation facade.  Grasp selection remains
the smallest sampled aperture at object extent plus 0.002 m total clearance
(command 20 for this profile).  Release and retreat independently select the
smallest sample at object extent plus 0.008 m total clearance (command 24), and
use that release sample's command and `tool_center_in_frame_m` throughout the
release dwell and vertical retreat.  Framework-owned MuJoCo evidence checks
that both jaws are clear before the first retreat waypoint, that retreat does
not recontact the object, and that the terminal cylinder is supported, upright,
within position tolerance, below 0.03 m/s, and free of forbidden evidence.
This is a locally verified SOARM101 P0 simulation contract, not an upstream
manufacturer or real-hardware claim, and it contains no task target coordinates
or pilot-held-out content.
