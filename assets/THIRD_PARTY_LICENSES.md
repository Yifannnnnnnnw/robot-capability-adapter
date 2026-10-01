# Third-party asset licenses

Robot models retain the licenses of their original authors. The project-level
Apache-2.0 license does not replace these model-specific terms. The table covers
the model directories included in this source release; links lead to the retained
license texts. Model READMEs and SOURCE files record derivation details where
available.

| Model directory | Recorded source | Retained license |
|---|---|---|
| `aloha_2` | MuJoCo Menagerie / Trossen Robotics | [BSD-3-Clause](mjcf/aloha_2/LICENSE) |
| `anybotics_anymal_c` | MuJoCo Menagerie / ANYbotics | [BSD-3-Clause](mjcf/anybotics_anymal_c/LICENSE) |
| `franka_panda` | MuJoCo Menagerie | [Apache-2.0](mjcf/franka_panda/LICENSE) |
| `go2` | Auto-Adapter import, derived from MuJoCo Menagerie / Unitree; [source record](mjcf/go2/SOURCE.md) | [BSD-3-Clause](mjcf/go2/LICENSE) |
| `google_barkour_vb` | MuJoCo Menagerie / Google | [Apache-2.0](mjcf/google_barkour_vb/LICENSE) |
| `h1` | MuJoCo Menagerie / Unitree | [BSD-3-Clause](mjcf/h1/LICENSE) |
| `hello_robot_stretch_2` | MuJoCo Menagerie / Hello Robot | [Clear BSD](mjcf/hello_robot_stretch_2/LICENSE) |
| `kinova_gen3_robotiq_2f85` | MuJoCo Menagerie / Kinova and Robotiq; [composition record](mjcf/kinova_gen3_robotiq_2f85/SOURCE.md) | [Arm: BSD-3-Clause](mjcf/kinova_gen3_robotiq_2f85/LICENSE); [gripper: BSD-2-Clause](mjcf/kinova_gen3_robotiq_2f85/robotiq_2f85/LICENSE) |
| `kuka_iiwa_14` | MuJoCo Menagerie / Drake | [BSD-3-Clause](mjcf/kuka_iiwa_14/LICENSE) |
| `leap_hand` | MuJoCo Menagerie / Ananye Agarwal; [source record](mjcf/leap_hand/SOURCE.md) | [MIT](mjcf/leap_hand/LICENSE) |
| `piper` | MuJoCo Menagerie / RosenYin | [MIT](mjcf/piper/LICENSE) |
| `robotstudio_so101` | MuJoCo Menagerie / The Robot Studio SO-ARM100 project | [Apache-2.0](mjcf/robotstudio_so101/LICENSE) |
| `skydio_x2` | Auto-Adapter import, derived from MuJoCo Menagerie / Skydio; [source record](mjcf/skydio_x2/SOURCE.md) | [Apache-2.0](mjcf/skydio_x2/LICENSE) |
| `ufactory_xarm7` | MuJoCo Menagerie / UFACTORY | [BSD-3-Clause](mjcf/ufactory_xarm7/LICENSE) |
| `unitree_a1` | MuJoCo Menagerie / Unitree | [BSD-3-Clause](mjcf/unitree_a1/LICENSE) |
| `unitree_g1` | MuJoCo Menagerie / Unitree | [BSD-3-Clause](mjcf/unitree_g1/LICENSE) |
| `universal_robots_ur5e` | MuJoCo Menagerie / ROS Industrial | [BSD-3-Clause](mjcf/universal_robots_ur5e/LICENSE) |
| `universal_robots_ur5e_robotiq_2f85` | Auto-Adapter UR5e and MuJoCo Menagerie Robotiq; [composition record](mjcf/universal_robots_ur5e_robotiq_2f85/SOURCE.md) | [Arm: BSD-3-Clause](mjcf/universal_robots_ur5e_robotiq_2f85/LICENSE); [gripper: BSD-2-Clause](mjcf/universal_robots_ur5e_robotiq_2f85/robotiq_2f85/LICENSE) |

The custom `pushbench/` scene and project-authored scene additions use the
[project Apache-2.0 license](../LICENSE). Scenes in `demo_scenes/` reuse robot
models and meshes from the directories above; those assets retain their own
licenses.

The imported Auto-Adapter asset record attributes SO-ARM101 meshes under
`urdf/meshes/` to LeRobot under Apache-2.0 and the custom object meshes (banana,
mug and other props) to Auto-Adapter under Apache-2.0. That record did not retain
an exact upstream revision for these meshes. The directory contains meshes,
not a standalone URDF package. The legacy `mjcf/so101_mujoco.xml` scene was
compiled from that SO-101 URDF and uses these meshes, as recorded in its header;
it is retained from the Apache-2.0 Auto-Adapter import.

The retained Go2 control policy has a separate license and source record under
[`auto_adapter/skeletons/data/`](../auto_adapter/skeletons/data/); it is not a
robot mesh asset. See [the asset guide](../docs/ASSETS.md) for path conventions.
