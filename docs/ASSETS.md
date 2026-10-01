# Robot models and scene assets

The source release keeps the MJCF models, referenced meshes and textures under
`assets/`, including the inputs used by AA and AA-Bench. Use the repository
checkout layout when running the examples: XML includes and mesh paths are
relative to these model directories.

The shared robot catalog is
[`autoadapter_bench/spec/robot_zoo.yaml`](../autoadapter_bench/spec/robot_zoo.yaml).
Its `mjcf` paths are relative to the repository root. Multiple catalog entries
can use the same robot model with different scenes. The task-library inputs live
under [`auto_adapter/task_libraries/`](../auto_adapter/task_libraries/), and the
fixed downstream Demo scene mapping is in
[`auto_adapter/demo_tasks.yaml`](../auto_adapter/demo_tasks.yaml).

## Contents and licenses

- `assets/mjcf/` contains robot model directories and the `pushbench/` and
  `demo_scenes/` scene collections, plus the legacy `so101_mujoco.xml` scene.
- `assets/urdf/meshes/` contains retained SO-ARM101 and object meshes; its name
  does not indicate that a standalone URDF package is included.
- [The asset license table](../assets/THIRD_PARTY_LICENSES.md) links every
  included robot model to its retained license. Composite arm/gripper packages
  retain both component licenses.
- [The Go2 policy source record](../auto_adapter/skeletons/data/go2_velocity_policy.json)
  and [policy license](../auto_adapter/skeletons/data/go2_velocity_policy_LICENSE.txt)
  are separate from the robot model license.

Existing source records and per-model READMEs are retained. The Go2 and Skydio
license texts were recovered from a pinned primary upstream revision, with the
imported model comparison documented in each model's `SOURCE.md`. Some older
asset records do not identify an exact original upstream import revision; the
release does not assign one retrospectively.

Scene files may reference a shared sibling model or mesh directory. Copy the
complete `assets/` tree when moving the source checkout. Inclusion in the
catalog describes available inputs, not a claim that every robot succeeds at
synthesis or every task.
