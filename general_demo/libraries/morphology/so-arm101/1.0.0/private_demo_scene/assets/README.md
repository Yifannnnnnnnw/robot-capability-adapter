# Private SO-ARM101 Demo Scene Assets

The fixed Demo uses deterministic primitive table, cube, target-face, goal,
button, and reference-marker geometry declared in `../scene.xml`.  The official
SO-ARM101 robot meshes are intentionally not copied here: the Session Runner
composes this fragment around the exact caller-selected, pinned canonical MJCF
and resolves its original asset closure without changing it.

These assets are Harness-private task/evaluation geometry.  They are not SDK
inputs, robot sensors, Generation inputs, Consumer inputs, or Translation
behavior.
