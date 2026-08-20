# Chapter 3 Experiment Plan

> **Status:** working experiment plan recording the agreed Chapter 3 design<br>
> **Scope:** AutoAdapter-Bench Experiment 1<br>
> **Primary object:** LLM-backbone performance in driver synthesis and capability-interface use

## 1. Purpose and evidence boundary

Chapter 3 compares LLM backbones in two separate experiments. Experiment 1.1
tests whether different Producer backbones can synthesise robot-specific
drivers under two generation conditions. Experiment 1.2 tests whether different
backbones can use fixed robot capability interfaces within selected, fixed
high-level-controller architectures.

The two experiments report separate outcomes. Capability-interface use does
not establish driver synthesis, and driver-validation success does not establish
high-level task completion. Robot configuration is an evaluation block in
Chapter 3, not the explanatory variable. Per-robot results may be reported, but
Chapter 3 does not estimate or interpret morphology effects.

## 2. Experiment 1.1: Driver-Synthesis Backbone Comparison

### 2.1 Research question

With the Auto-Adapter workflow, robot inputs, source-backed task standards,
validation policy, Harness rules, and resource budget held fixed, how do seven
Producer LLM backbones differ in synthesising robot-specific drivers under the
skeleton-assisted and from-scratch generation conditions?

### 2.2 Prespecified robot cases

The minimum formal Chapter 3 cohort contains five contrasting robot
configurations. These are case robots selected to provide varied execution
contexts; they are not claimed to be statistically representative of all robot
morphologies.

| Category | Robot configuration | Selection role |
|---|---|---|
| Fixed serial manipulator | `robotstudio_so101` | Compact arm and existing mainline acceptance case |
| Fixed serial manipulator | `franka_panda` | Second arm with a different kinematic and control configuration |
| Quadruped | `unitree-go2-stock-12dof` | Legged-locomotion case and existing mainline acceptance case |
| Mobile manipulator | `hello_robot_stretch_2` | Coupled base-and-arm execution case |
| Humanoid | `unitree_g1` | Whole-body articulated execution case |

Every case must satisfy the same package-admission, source-lineage,
asset-closure, validation, and evidence requirements before its formal cells
run. A blocked case remains visible and is not silently replaced after outcomes
are inspected.

### 2.3 Experimental matrix

The formal minimum is:

```text
5 robot configurations
  x 7 Producer LLM backbones
  x 2 generation conditions
  x 5 independent generation replicates
= 350 generation-condition replicates
```

Each generation condition permits at most three submitted driver attempts, so
the design may execute up to 1,050 driver attempts. Repair attempts are nested
within a generation replicate and are not additional independent samples.

For each `robot x backbone x replicate` block, one capability design and one
complete validation suite are sealed and reused across the two matched
generation conditions. The conditions use isolated workspaces and do not share
candidates, generation traces, validation results, or Repair histories.

### 2.4 Experimental factors and controls

The manipulated factors are:

- Producer LLM backbone;
- generation condition: `skeleton-assisted` or `from-scratch`.

The following remain fixed within the applicable matched comparison:

- Auto-Adapter workflow and Framework implementation;
- exact robot package and Direct-MuJoCo environment;
- source-backed Task Library and source-task pass standards;
- private-case construction policy, measurement semantics, and Harness verdict rules;
- public inputs, model-call policy, attempt limit, and resource budget;
- sealed capability design and complete validation suite across the two conditions.

The generated driver is the evaluated output and therefore varies by backbone,
condition, and replicate. Model-authored capability design and capability-level
criteria may vary across backbones; their source obligations and independent
evaluation policy remain fixed.

### 2.5 Outcomes and reporting

Primary driver-validation outcomes are `pass@0`, final pass within three
attempts, Repair gain, attempts to first pass, and cell completion. Primary
resource outcomes are model calls, provider-reported token categories, model
cost, attempt-0 wall time, and terminal wall time. Failure classes and
per-capability or per-case results are secondary diagnostics.

Results are reported for every robot-backbone-condition cell and as a
robot-level macro-average. Failed or blocked cells remain in the declared
denominator. Validation cases and Repair attempts are not pooled as though they
were independent generated drivers.

## 3. Experiment 1.2: Capability-Interface Use under Controller Architectures

### 3.1 Research question

Within each selected and fixed high-level-controller architecture, how do seven
LLM backbones differ in using a fixed robot capability interface, backed by the
same fixed validated driver, to complete matched compositional tasks?

A secondary analysis examines whether relative backbone performance is
consistent across controller architectures. It does not assume that one
backbone is globally best independently of architecture.

### 3.2 Robot and architecture scope

The formal minimum uses two contrasting cases drawn from Experiment 1.1:

- `robotstudio_so101`;
- `unitree-go2-stock-12dof`.

The minimum formal controller architecture is Code-BT. A prompting/in-context
learning variant of LLM-as-BT-Planner is added as the second formal architecture
only if its published-method reproduction and fixed-interface integration pass
the pre-run audit. Inner Monologue and other architectures remain declared
extensions until separately admitted; they do not delay the minimum formal
experiment.

For each robot, the benchmark fixes one capability interface and one
benchmark-supplied validated driver before any controller outcome is inspected.
Experiment 1.2 does not select a driver based on Experiment 1.1 results and does
not count use evidence as synthesis evidence.

### 3.3 Experimental matrix

The minimum per-architecture design is:

```text
2 robot configurations
  x 7 eligible LLM backbones
  x 1 fixed controller architecture
  x 5 compositional task templates
  x 2 matched private seeds
  x 2 independent controller episodes
= 280 controller episodes per architecture
```

With two admitted controller architectures, the planned total is 560 episodes.
If an architecture cannot validly support one of the seven backbones, that
ineligibility is decided before formal task outcomes are inspected and remains
visible in the report. Comparisons use the complete matched backbone subset for
that architecture.

### 3.4 Experimental factors and controls

The primary manipulated factor is the LLM backbone within one fixed controller
architecture. Controller architecture is a secondary blocking and exploratory
factor when two architectures are admitted.

For each robot, the following remain fixed across all eligible backbone cells:

- robot capability interface and validated driver;
- compositional task templates, private instances, and seeds;
- task pass standards, public observations, and Harness verdict rules;
- capability-call limit, physical timeout, and outer execution budget.

Within each architecture, its system prompt, in-context examples, model role,
output grammar, parser or converter, feedback mechanism, executor, and
turn/call budget remain fixed. Every model-backed role that belongs to the
declared architecture must have its backbone treatment specified before the
formal run.

### 3.5 Outcomes and reporting

The primary outcome is complete-task physical success decided by the trusted
Harness. Secondary outcomes include valid capability selection and argument
binding, plan/code/BT validity, capability-chain completion, inadmissible or
unnecessary calls, model calls, tokens, cost, wall time, and failure patterns.

Backbone comparisons are first reported separately within each controller
architecture. Cross-architecture analysis is limited to matched backbones and
is interpreted as possible backbone-by-architecture dependence. Results from
different or unbalanced eligible-backbone sets are not collapsed into one
unconditional global leaderboard.

## 4. Expansion policy

The benchmark retains reusable robot, method, runner, and analysis scripts so
that additional robots, seeds, tasks, or controller architectures can be run
later. The Chapter 3 formal denominator nevertheless remains the prespecified
minimum above unless an expansion is declared before formal outcomes are
inspected and executed under the same protocol.

Data added after the initial analysis are labelled as extension or robustness
evidence and are reported separately. Additional robot cases do not turn
Chapter 3 into a morphology analysis. Chapter 5 retains that purpose by fixing
the relevant model and workflow factors and making morphology the focal
explanatory dimension.

## 5. Decisions to pin before formal runs

Before execution, the versioned manifests must pin:

1. exact provider model identifiers and inference settings for all seven backbones;
2. robot package and Task Library snapshot versions for the five Experiment 1.1 cases;
3. generation budgets, random-state policy, and the five independent replicate identifiers;
4. the fixed Experiment 1.2 interfaces, drivers, tasks, pass standards, seeds, and budgets;
5. the exact Code-BT version and whether LLM-as-BT-Planner passes the formal method audit;
6. the model-backed role or roles changed by the backbone treatment in each architecture;
7. the analysis tables, macro-averaging rule, confidence-interval method, and treatment of blocked cells.

This plan supersedes the earlier assumption that all 14 available robot
configurations must enter the Chapter 3 formal denominator. The Authority,
thesis Introduction, and `benchmark.md` must be aligned with these decisions
before the runs are presented as formal thesis evidence.
