# Analysis

Analysis reads immutable run results and produces raw cell tables plus concise
derived summaries.

- B1 uses one independent driver-generation replicate as the statistical unit.
- B2 uses one controller episode, blocked by robot, task, seed, architecture,
  and applicable backbone.
- Report per-robot results and the macro-average across all 14 robots.
- Compare backbones only within a replaceable architecture's valid `B_a`.
- Keep B1 and B2 scores separate and retain failures in every denominator.
- Report model calls, token categories, cost, wall time, and cost per success.
