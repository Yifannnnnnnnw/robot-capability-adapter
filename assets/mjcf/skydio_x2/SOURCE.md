# Skydio X2 source

The retained `x2.xml` is byte-identical to the model in the imported
[Auto-Adapter baseline](https://github.com/981526092/auto-adapter/tree/585eb1f1fde33f17f5f9a1e169a18dd41f97b586/assets/mjcf/skydio_x2).
The model and its visual assets derive from MuJoCo Menagerie's `skydio_x2`
package.

The missing license text was restored from
[MuJoCo Menagerie at revision `da76818e269b82289eba39808e2fb91d679d6994`](https://github.com/google-deepmind/mujoco_menagerie/blob/da76818e269b82289eba39808e2fb91d679d6994/skydio_x2/LICENSE).
The exact Apache-2.0 text is retained in [LICENSE](LICENSE). This revision
identifies the license retrieval and model comparison source; the original
asset import revision was not recorded.

The retained model differs from that Menagerie model only in whitespace in two
actuator `gear` attributes. `scene.xml` is the local scene entrypoint. This
release preserves the imported XML, mesh and texture data.
