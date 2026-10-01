# Robot inputs

The catalog describes available models and generation routes. It is not a list
of successful generated drivers or a formal experiment cohort. The launcher
uses `autoadapter_bench/spec/robot_zoo.yaml`; automatic Design reads the binding
in `auto_adapter/task_libraries/index.yaml`.

| Catalog ID | Generation route | Automatic Design input |
| --- | --- | --- |
| `so101` | skeleton | No automatic Design binding |
| `menagerie_so101` | skeleton | 20 public tasks |
| `so101_push` | skeleton | No automatic Design binding |
| `piper_push` | skeleton | 20 public tasks |
| `franka_push` | skeleton | 20 public tasks |
| `piper` | skeleton | 20 public tasks |
| `franka` | skeleton | 20 public tasks |
| `ur5e` | skeleton | No automatic Design binding |
| `kuka_iiwa14` | skeleton | 20 public tasks |
| `go2` | skeleton | 20 public tasks |
| `unitree_a1` | skeleton | 20 public tasks |
| `anymal_c` | skeleton | 20 public tasks |
| `kinova_gen3_robotiq_2f85` | skeleton | 20 public tasks |
| `ufactory_xarm7` | skeleton | 20 public tasks |
| `universal_robots_ur5e_robotiq_2f85` | skeleton | 20 public tasks |
| `leap_hand` | skeleton | 20 public tasks |
| `hello_robot_stretch_2` | skeleton | 20 public tasks |
| `aloha_2` | skeleton | 20 public tasks |
| `skydio_x2` | from_scratch | 20 public tasks |
| `h1` | from_scratch | 20 public tasks |

The legacy `so101`, `so101_push` and bare `ur5e` models have no automatic Design
binding. For SO-101 generation, use `menagerie_so101`. Bench's historical
`so101` task suites use a different model and driver contract; changing the ID
does not make those tasks interchangeable.

The task library also contains G1 and Barkour packages/model assets that do not
have public launcher catalog entries. Lower-level Python APIs accept explicit
robot/model/design inputs; the launcher does not infer missing catalog entries.

Task descriptions and proposed numerical requirements are inputs to Design.
Only the actual run's physical validation establishes which generated
capabilities passed, and downstream tasks require their own physical scoring.
