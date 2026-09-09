# Source

- URL: https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/leap_hand
- Commit: `da76818e269b82289eba39808e2fb91d679d6994`
- License: MIT; see `LICENSE` (Ananye Agarwal).
- Entrypoint: `scene_left.xml` (model `left_hand.xml`); `scene_right.xml` is also included.

## Cube Reorientation Policy Scene

- URL: https://github.com/google-deepmind/mujoco_playground
- Commit: `e74217bb89c77a74ba02e4789263991864375799`
- License: Apache-2.0; see `MUJOCO_PLAYGROUND_LICENSE.txt`.
- Upstream model paths: `mujoco_playground/_src/manipulation/leap_hand/xmls/scene_mjx_cube.xml`, `leap_rh_mjx.xml`, and `reorientation_cube.xml`.
- Upstream policy path: `mujoco_playground/experimental/sim2sim/onnx/leap_reorient_policy.onnx`.
- Local entrypoint: `leap_cube_policy_scene.xml`.
- Modifications: asset/include paths resolve inside this package, the deployed `0.002` second timestep is explicit in `leap_rh_mjx.xml`, and the already-vendored byte-identical MuJoCo Menagerie hand meshes are reused.
