# AA1 robot illustrations

These scripts read the `id` and `mjcf` fields in
`AA1/autoadapter_bench/spec/robot_zoo.yaml` and load the referenced AA1 assets.
They do not read the sibling `autoadapter/` packages or modify AA1 source assets.

Run from the repository root using an environment with MuJoCo, NumPy, Pillow
and PyYAML installed (the current `AA1/.venv` has these dependencies):

```bash
AA1/.venv/bin/python tools/render_robot_mujoco_scenes.py --robot so101 --robot go2
AA1/.venv/bin/python tools/render_robot_transparent_svgs.py --robot so101 --robot go2
AA1/.venv/bin/python tools/generate_robot_scene_graphs.py --robot so101 --robot go2
```

| Script | Default output directory | Output |
|---|---|---|
| `render_robot_mujoco_scenes.py` | `outputs/aa1-renders/scenes/` | `<robot-id>.png` and a contact sheet of the current selection |
| `render_robot_transparent_svgs.py` | `outputs/aa1-renders/robot_svgs/` | `<robot-id>.svg`, embedding a transparent PNG |
| `generate_robot_scene_graphs.py` | `outputs/aa1-renders/scene_graphs/` | `<robot-id>.png`, showing the MJCF body hierarchy |

Use AA1 catalogue IDs, such as `so101`, `franka`, `go2`, `aloha_2` and
`skydio_x2`. Omit `--robot` to render every catalogue entry, including scene
variants. Repeat `--robot` to select several entries. `--aa1-root` selects an
AA1 checkout; `--output-dir` changes the destination. The old `--robots-root`
option and versioned `morphology.json` package layout are no longer used.

Scene PNGs show the catalogue's base `mjcf` scene, including its surroundings.
Transparent SVGs keep the robot body subtrees identified through joint or site
actuators; this includes both ALOHA arms and Skydio's thrust-driven body. They
omit the environment. Rendering uses a home/stand keyframe when available,
otherwise the model's initial pose. These are static simulation illustrations.
