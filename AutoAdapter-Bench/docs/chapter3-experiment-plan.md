# Chapter 3 Experiment Plan

> **Status:** approved working design<br>
> **Object:** LLM-backbone performance in driver synthesis and
> capability-interface use

## Experiment 1.1: Driver synthesis

The primary B1 analysis compares seven Producer backbones under the
`skeleton-assisted` condition across a prespecified morphology-diverse
10-configuration cohort. A prespecified five-configuration subset also runs
`from-scratch` as a scaffolding ablation.

```text
Primary:  10 robots x 7 backbones x R=5 = 350 replicates
Ablation:  5 robots x 7 backbones x R=5 = 175 replicates
Total:                                      525 replicates
```

The primary cohort contains `robotstudio_so101`, `franka_panda`,
`universal_robots_ur5e_robotiq_2f85`, `leap_hand`,
`unitree-go2-stock-12dof`, `google_barkour_vb`, `unitree_g1`,
`hello_robot_stretch_2`, `aloha_2`, and
`boston_dynamics_spot_with_arm`. The four other fixed serial-manipulator
configurations remain benchmark expansion assets rather than Chapter 3 units.

The ablation cases are `robotstudio_so101`, `franka_panda`,
`unitree-go2-stock-12dof`, `hello_robot_stretch_2`, and `unitree_g1`.
Their matched conditions share sealed capability designs and validation suites.

B1 ends after complete private driver validation and at most two bounded
Repairs. It does not execute Task Demo. The primary outcomes are `pass@0`,
final pass within three submissions, valid-driver rate, Repair gain, attempts
to first pass, and resource use.

## Experiment 1.2: Capability-interface use

B2 preselects the package reference drivers for `robotstudio_so101` and
`unitree-go2-stock-12dof`. Each driver must still pass the complete validation
suite after its B2 capability interface and adapter are frozen. Within Code-BT,
seven eligible LLM backbones are compared on five compositional task templates,
two private seeds, and two independent episodes.

```text
2 robots x 7 backbones x 5 tasks x 2 seeds x 2 episodes
= 280 controller episodes per architecture
```

Code-BT remains blocked until its source, replacement, and adapter audits pass,
the capability interfaces are selected, and the selected reference drivers
pass interface-bound validation.
LLM-as-BT-Planner may be added as a second architecture after an independent
audit. Results are interpreted within architecture; Chapter 3 does not infer a
controller-independent global model ranking.

## Evidence boundary

Driver-validation evidence and controller task evidence remain separate.
Robot configuration blocks the Chapter 3 comparisons but is not treated as the
explanatory morphology variable. Missing packages, unavailable providers, and
ineligible controller pairings stay visible.
