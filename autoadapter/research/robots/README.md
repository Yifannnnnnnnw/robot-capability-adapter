# Research robot build index

This directory is planning-only. index.json is an ordered list of future robot
research candidates and does not admit a robot, add a package to runtime, or
select a run.

The three selection layers remain separate:

1. autoadapter/research/robots/index.json records planning inputs only.
2. autoadapter/libraries/robots/index.json is the only runnable package index.
3. autoadapter/configs/experiments/*.json selects a subset of already runnable
   packages for an experiment.

The proposed order begins with fixed serial arms that may reuse the existing
arm_serial_dls family. Hand, quadruped, humanoid, mobile-manipulator,
bimanual, and legged-arm candidates follow in later waves.

The inspected future-robot catalogs currently contain five task records per
candidate, not the required twenty distinct applicable source-backed tasks.
The Franka canonical asset and morphology foundation now exists, but Franka is
still non-runtime until its remaining package and evidence work is complete.
The General Demo Franka, KUKA, Piper, and UR5e closure records explicitly mark
UPSTREAM_CACHE_REQUIRED; their upstream mesh materialization is incomplete.
Demo2 contributes additional legacy assets and historical strict summaries and
validation reports, but those files are migration inputs rather than current
canonical mainline evidence. Untracked Demo2 morphology and reference files are
not required by this index or its test.

Each candidate lists concrete work needed for a complete canonical package:
local MuJoCo closure, current public morphology and affordances, a 20-plus task
library with exact source lineage and machine-expressible standards, private
instances/bindings/guards, an applicable skeleton and reference driver, package
checks, a focused Direct-MuJoCo positive control, and a dynamic canary. No
candidate in this file is a runtime selection.
