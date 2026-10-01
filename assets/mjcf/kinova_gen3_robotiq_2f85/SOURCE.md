# Source and provenance

## Kinova Gen3 arm

- URL: https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/kinova_gen3
- Commit: `da76818e269b82289eba39808e2fb91d679d6994`
- License: BSD-3-Clause; see `LICENSE` (Kinova Inc.).
- Local source model: `gen3.xml` and its local `assets/*.stl` files.

## Robotiq 2F-85 gripper

- URL: https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/robotiq_2f85
- Commit: `da76818e269b82289eba39808e2fb91d679d6994`
- License: BSD-2-Clause; see `robotiq_2f85/LICENSE`.
- Local source model: `robotiq_2f85/2f85.xml` and its local `assets/*.stl` files.

## Composite derivation

- `scene.xml` is the package-relative canonical scene entrypoint.
- `kinova_gen3_robotiq_2f85.xml` is derived from the local Kinova model and retains the
  official Robotiq mechanism definitions, contacts, tendon, equality constraints, and
  native `fingers_actuator`.
- The Robotiq `base_mount` body and its visual/collision use are omitted as directed by the
  Kinova composition guidance. The official `base` body is mounted at
  `pos="0 0 -0.06149039" quat="0 -1 1 0"` under `bracelet_link`.
- The arm `pinch_site` is moved to `pos="0 0 -0.181525" quat="0 1 0 0"` and remains an
  observation site on `bracelet_link`.
- All runtime references resolve to files within this package. No runtime download or
  sibling package is required.
