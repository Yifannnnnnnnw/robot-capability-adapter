# Chapter 3 Experiment Plan

> **Status:** approved working design<br>
> **Object:** LLM-backbone performance in driver synthesis and
> capability-interface use

## Experiment 1.1: Driver synthesis

The primary B1 analysis compares seven Producer backbones under the
`skeleton-assisted` condition across the complete 14-configuration target
cohort. A prespecified five-configuration subset also runs `from-scratch` as a
scaffolding ablation.

```text
Primary:  14 robots x 7 backbones x R=5 = 490 replicates
Ablation:  5 robots x 7 backbones x R=5 = 175 replicates
Total:                                      665 replicates
```

The ablation cases are `robotstudio_so101`, `franka_panda`,
`unitree-go2-stock-12dof`, `hello_robot_stretch_2`, and `unitree_g1`.
Their matched conditions share sealed capability designs and validation suites.

B1 ends after complete private driver validation and at most two bounded
Repairs. It does not execute Task Demo. The primary outcomes are `pass@0`,
final pass within three submissions, valid-driver rate, Repair gain, attempts
to first pass, and resource use.

## Experiment 1.2: Capability-interface use

B2 holds one validated driver and capability interface fixed for
`robotstudio_so101` and `unitree-go2-stock-12dof`. Within Code-BT, seven
eligible LLM backbones are compared on five compositional task templates, two
private seeds, and two independent episodes.

```text
2 robots x 7 backbones x 5 tasks x 2 seeds x 2 episodes
= 280 controller episodes per architecture
```

Code-BT remains blocked until its source, replacement, and adapter audits pass.
LLM-as-BT-Planner may be added as a second architecture after an independent
audit. Results are interpreted within architecture; Chapter 3 does not infer a
controller-independent global model ranking.

## Evidence boundary

Driver-validation evidence and controller task evidence remain separate.
Robot configuration blocks the Chapter 3 comparisons but is not treated as the
explanatory morphology variable. Missing packages, unavailable providers, and
ineligible controller pairings stay visible.
