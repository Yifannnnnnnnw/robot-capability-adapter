# Third-Party Asset Licenses

Robot models under `assets/mjcf/` are vendored from
[MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie) and
related upstream sources, each used under its original license. Where upstream
ships a `LICENSE` file it is kept in that model's directory; this table
summarizes provenance.

| Model dir (`assets/mjcf/`) | Source | License |
|---|---|---|
| `robotstudio_so101` | MuJoCo Menagerie / LeRobot | Apache-2.0 |
| `franka_panda` | MuJoCo Menagerie | Apache-2.0 |
| `universal_robots_ur5e` | MuJoCo Menagerie | BSD-3-Clause |
| `kuka_iiwa_14` | MuJoCo Menagerie (Drake) | BSD-3-Clause |
| `piper` | MuJoCo Menagerie (RosenYin) | MIT |
| `unitree_a1` | MuJoCo Menagerie | BSD-3-Clause (Unitree) |
| `h1` | MuJoCo Menagerie | per Unitree terms (research use) |
| `anybotics_anymal_c` | MuJoCo Menagerie | BSD-3-Clause (ANYbotics) |
| `go2` | MuJoCo Menagerie | BSD-3-Clause (Unitree) |
| `skydio_x2` | MuJoCo Menagerie | Apache-2.0 (Skydio) |
| `hello_robot_stretch_3` | MuJoCo Menagerie | Apache-2.0 (not in git; HF dataset) |
| `pushbench` | this project (custom scene) | Apache-2.0 |

SO-ARM101 (`so101`) URDF and meshes (`assets/urdf/meshes/`) derive from the
LeRobot project (Apache-2.0); custom objects (banana, mug, etc.) are released
under Apache-2.0 with this repository.

For full license texts, see each model's upstream repository and the `LICENSE`
file kept alongside it where provided.
