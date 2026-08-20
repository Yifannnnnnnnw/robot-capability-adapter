# Universal Robots UR5e with Robotiq 2F-85 (MJCF)

This research-only package composes the canonical six-joint Universal Robots
UR5e arm with the official Robotiq 2F-85 mechanism. The package-relative
entrypoint is scene.xml, which includes the static composite
universal_robots_ur5e_robotiq_2f85.xml.

The arm keeps its canonical position actuators, joint names, attachment_site,
and home keyframe. The gripper keeps its native split tendon, equality-coupled
passive joints, four contact-enabled pad boxes, pinch site, and
fingers_actuator convention: 0 is open and 255 is closed.

The component XML and source material remain local for inspection. This
foundation establishes asset and control liveness only; it does not claim task
success or runnable admission.

## Source files and licenses

- UR5e source files and BSD-3-Clause license: the files at this directory root
  and assets/.
- Robotiq 2F-85 source files and BSD-2-Clause license: robotiq_2f85/.
- Pinned source revisions and composition details: SOURCE.md.
