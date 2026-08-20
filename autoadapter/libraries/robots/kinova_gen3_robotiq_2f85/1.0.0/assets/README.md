# Kinova Gen3 with Robotiq 2F-85 (MJCF)

This package is a local MJCF composition of the canonical Kinova Gen3 7-DoF arm and the
official Robotiq 2F-85 model. The canonical entrypoint is `scene.xml`, which includes
`kinova_gen3_robotiq_2f85.xml`.

The arm assets are derived from the pinned MuJoCo Menagerie Kinova Gen3 model. The gripper
source is retained, unchanged, under `robotiq_2f85/`. The derived composite follows the
Kinova Gen3 composition guidance: it omits the Robotiq `base_mount` body, attaches the
Robotiq `base` body at `pos="0 0 -0.06149039" quat="0 -1 1 0"`, and moves the terminal
`pinch_site` to `pos="0 0 -0.181525" quat="0 1 0 0"`.

The Robotiq actuator uses its native control range `[0, 255]`. In this local model, `0` opens
the pads and `255` closes them, as established by actuator-driven pad-separation measurement.
The `pinch_site` is an end-effector
observation site; it does not by itself establish a contact capability.

## Source files and licenses

- Kinova Gen3 source files and license: the files at this directory root and `assets/`.
- Robotiq 2F-85 source files and BSD-2-Clause license: `robotiq_2f85/`.
- Exact source revisions and composition details: `SOURCE.md`.
