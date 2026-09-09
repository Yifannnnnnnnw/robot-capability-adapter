# AutoAdapter-Bench — videos (organized by axis)

`MANIFEST.csv` lists **every** video (861 rows): `axis, robot, model, method, task, trial, path`.

The benchmark has **two targeted slices, not a full robot×model×task cube** — a
full cube (esp. Opus × every robot × every task) is prohibitively expensive and
would *weaken* the efficiency argument that is part of the contribution. So:

## `model_axis/so101/` — "which model is best, embodiment fixed?"
**Dense per-task evidence.** SO-101 × 7 LLMs × {ours, CaP} × 13 tasks × 5 trials.
```
model_axis/so101/{ours,cap}/<model>/<task>_t<trial>.mp4
   <model> ∈ sonnet46 opus48 haiku45 novapro deepseek ministral8b qwen32
```
**844 videos** (symlinks into `artifacts/.../recordings/`, no duplication).
Known video gaps (the *result JSONs are complete* — only the mp4s are short):
`cap/opus48` = 4/65, `cap/ministral8b` = 60/65. (Re-runnable on request.)

## `robot_axis/<robot>/fixed_sonnet*/` — "does it transfer across embodiments, model fixed?"
**Qualitative morphology evidence**, fixed Sonnet agent. One subtree per robot:
arm {so101, piper, ur5e, franka, kuka}, quadruped {go2, anymal}, aerial {skydio},
humanoid {h1}. Files are named **`driver_demo_<task>.mp4`** — these are
**regenerated demonstrations from the validated synthesized drivers** (the
fixed renderer; kinematic-IK arms like Piper via waypoint interpolation), **NOT
the agent's exact evaluation-trial replays** (per-trial tool logs weren't
stored). They support the transfer claim qualitatively; the *quantitative*
results are the JSONs in `../`.

## Why no full cube
SO-101 is the model-swept robot (model axis); every other robot is run with a
fixed Sonnet agent (robot-diversity axis). The two axes answer two distinct,
clean questions; a sparse expensive cube would confound them. See `../README.md`.
