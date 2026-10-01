# Source and provenance

## Universal Robots UR5e arm

- URL: https://github.com/981526092/auto-adapter
- Fixed revision: 585eb1f1fde33f17f5f9a1e169a18dd41f97b586
- Local source model: ur5e.xml and local assets/*.obj files.
- License: BSD-3-Clause; see LICENSE.
- Source path in the pinned material: assets/mjcf/universal_robots_ur5e/.

## Robotiq 2F-85 gripper

- URL: https://github.com/google-deepmind/mujoco_menagerie/tree/da76818e269b82289eba39808e2fb91d679d6994/robotiq_2f85
- Fixed revision: da76818e269b82289eba39808e2fb91d679d6994
- Local source model: robotiq_2f85/2f85.xml and local robotiq_2f85/assets/*.stl files.
- License: BSD-2-Clause; see robotiq_2f85/LICENSE.

## Composite derivation

- scene.xml includes only the package-local universal_robots_ur5e_robotiq_2f85.xml.
- The composite retains the canonical UR5e arm body, six actuators, attachment
  site transform, and home arm coordinates.
- A fixed robotiq_mount wrapper is placed under wrist_3_link at
  pos="0 0.1 0" quat="-1 1 0 0". The native base_mount and base transforms
  remain unchanged below that attachment wrapper; the colliding base body is
  named robotiq_base.
- The Robotiq driver, passive linkage, pad, contact, tendon, equality, and
  native fingers_actuator definitions are retained. The public pinch_site
  preserves the native pinch transform while the native pinch site remains
  available.
- All runtime references resolve within this package. There are no symlinks,
  sibling package references, absolute paths, submodules, or downloads.
