# Unitree Go2 source

The retained `go2.xml` is byte-identical to the model in the imported
[Auto-Adapter baseline](https://github.com/981526092/auto-adapter/tree/585eb1f1fde33f17f5f9a1e169a18dd41f97b586/assets/mjcf/go2).
That model derives from MuJoCo Menagerie's `unitree_go2` model.

The missing license text was restored from
[MuJoCo Menagerie at revision `da76818e269b82289eba39808e2fb91d679d6994`](https://github.com/google-deepmind/mujoco_menagerie/blob/da76818e269b82289eba39808e2fb91d679d6994/unitree_go2/LICENSE).
The exact upstream text is retained in [LICENSE](LICENSE), including Unitree's
2016–2022 copyright notice. This revision identifies the license retrieval and
model comparison source; the original asset import revision was not recorded.

Compared with that Menagerie model, the imported model has adaptations to body
names, joint damping and contact parameters, plus formatting changes. The local
`go2_scene.xml` and `go2_test_scene.xml` provide project scene wrappers. This
release preserves the imported XML and mesh data.
