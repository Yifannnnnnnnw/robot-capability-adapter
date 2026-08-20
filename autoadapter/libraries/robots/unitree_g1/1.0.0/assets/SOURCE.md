# Sources

## Public Menagerie Model

- URL: https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/unitree_g1
- Commit: `da76818e269b82289eba39808e2fb91d679d6994`
- License: BSD-3-Clause; see `LICENSE` (Unitree Robotics).
- Entrypoint: `scene.xml` (model `g1.xml`).

## Retained Velocity-Policy Model

- URL: https://github.com/unitreerobotics/unitree_rl_mjlab/tree/1425b15f73bd4095f0df53709d7c389c3eb9e790
- Commit: `1425b15f73bd4095f0df53709d7c389c3eb9e790`
- License: Apache-2.0; see `reference/g1_velocity_policy_LICENSE.txt`.
- Entrypoint: `g1_mjlab_policy_scene.xml` (model `g1_mjlab.xml`).
- The 35 mesh files referenced by `g1_mjlab.xml` are byte-identical to the
  files already retained from MuJoCo Menagerie. The policy scene records the
  source training timestep, collision classes, position gains, damping,
  effort limits, armatures, and HOME reset used by the retained policy.
