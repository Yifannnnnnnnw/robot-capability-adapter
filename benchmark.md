# Auto-Adapter 2.0 Benchmark Protocol

> **Status:** protocol draft; non-normative until aligned with `AUTOADAPTER_2_AUTHORITY.md`<br>
> **Authority baseline:** `AA2-AUTH` revision `0.19.18`<br>
> **Prepared:** 2026-08-20<br>
> **Execution scope:** canonical `autoadapter/` Direct-MuJoCo mainline only<br>
> **Excluded from denominators:** real SDK, Translation, hardware, historical `demo2/`, and incomplete research candidates

## 0. Executive decision

The benchmark is not a flat comparison of generic Agent patterns. It contains two primary
sub-benchmarks and one derived analysis:

| Track | Scientific object | Manipulated factor | Fixed core |
|---|---|---|---|
| **B1: Driver Synthesis** | A generated robot-specific driver | Producer LLM backbone and the two Authority-defined generation conditions | Auto-Adapter workflow, robot inputs, suites, Harness, budgets |
| **B2: Capability-Interface Use** | A high-level controller using one fixed interface and one fixed validated generated driver | Consumer LLM backbone | Controller architecture, prompt, capability exposure, driver, tasks, budgets |
| **B3: Cross-Morphology Analysis** | Differences in B1 outcomes across the declared cohort | No new intervention | B1 data and fixed controls |

No single aggregate score combines B1 and B2. A model may be good at synthesising drivers and poor
at using capabilities, or the reverse. B3 is descriptive and associational, not a causal morphology
claim.

The recommended B2 controller is a genuine robot task-level controller:

```text
public task + public observation
              |
              v
     LLM/VLM task planner
              |
              v
 typed Behavior Tree or task graph
              |
              v
 deterministic executive + bounded recovery
              |
              v
       Capability Router
              |
              v
 fixed capability interface + fixed validated generated driver
              |
              v
          Direct MuJoCo
              |
              v
 Framework-owned public outcome monitor -> executive/planner replan input

Private Harness state and verdict remain on an independent path.
```

`ReAct`, `CodeAct`, and `Plan-and-Execute` are reasoning or orchestration patterns, not sufficient
robot high-level-control architectures by themselves. ReAct may be used inside the planner's
bounded decision loop, but the executive, capability boundary, feedback path, and recovery semantics
must be explicit.

**Authority decision required.** Revision `0.19.18` currently declares one fixed bounded ReAct
controller for formal capability-interface use. This draft recommends a planner plus deterministic
BT/task-graph executive as the formal B2 architecture. Until the Authority is explicitly aligned,
the new controller can run only as an exploratory pilot; evidence under the current Authority must
continue to use its declared ReAct path. This draft does not silently override the Authority.

## 1. Benchmark boundaries

1. The driver remains the only component that converts a requested capability into robot-specific
   actuation, state-dependent low-level corrections, and MuJoCo stepping.
2. The high-level controller may select, parameterise, sequence, branch over, and retry public
   capabilities. It may not write actuator commands, call MuJoCo directly, inspect private suites,
   or reproduce low-level motion control.
3. The trusted Harness alone decides physical success. Controller completion, BT root success, a
   model self-report, or a normal Python return is never a benchmark verdict.
4. Every formal robot is an exact robot configuration with a complete local MJCF closure,
   `mujoco==3.3.6`, at least 20 admitted source-backed tasks, private validation bindings and guards,
   a skeleton family, and reference calibration.
5. Formal evaluation performs no runtime download of code, models, task standards, or robot assets.
6. Reference drivers and hand-authored controllers are calibration controls. They are not model
   conditions and do not establish model-based synthesis or capability-interface use.

## 2. Robot population

### 2.1 Current acceptance pair

The current runnable index contains only:

| Robot configuration | Morphology | Benchmark role |
|---|---|---|
| `robotstudio_so101` | Fixed-base serial manipulator | Initial arm engineering acceptance |
| `unitree-go2-stock-12dof` | Free-base quadruped | Initial locomotion engineering acceptance |

These two robots are the first mainline acceptance pair. They are not the complete Experiment 3
cohort and cannot support a general morphology claim by themselves.

### 2.2 Planned multi-robot universe

The current non-runtime research index adds 12 candidates:

| Morphology category | Planned configurations |
|---|---|
| Fixed serial arm | `franka_panda`, `kinova_gen3`, `ufactory_xarm7`, `universal_robots_ur5e`, `piper`, `kuka_iiwa_14` |
| Hand | `leap_hand` |
| Quadruped | `google_barkour_vb` |
| Humanoid | `unitree_g1` |
| Mobile manipulator | `hello_robot_stretch_2` |
| Bimanual | `aloha_2` |
| Legged arm | `boston_dynamics_spot_with_arm` |

Together with the acceptance pair, this is a planning universe of 14 configurations across seven
morphology categories. It is not yet the benchmark denominator. Directory presence, an MJCF/URDF,
or a historical run does not make a robot an admitted case.

### 2.3 Formal cohort rule

Before Experiment 3, one versioned experiment manifest must declare:

- every included `robot_configuration_id` and morphology category;
- exact package and Task Library snapshot versions;
- B1 and B2 eligibility;
- Producer and Consumer backbone sets;
- generation replicate count, task instances, seeds, and budgets.

Let `N` be the number of admitted, manifest-declared robots. All B1 headline results use this fixed
`N`. B2 should use the same cohort. If a robot cannot support the compositional use suite or does not
produce the prespecified validated generated driver, the B2 manifest must report it as ineligible or
missing before Consumer results are inspected; it must not disappear silently from the denominator.

## 3. B1: Driver Synthesis

### 3.1 Experimental matrix

The primary matrix is:

```text
N admitted robots
  x M Producer LLM backbones
  x R independent generation replicates
  x 2 generation conditions
```

The two conditions are exactly:

- `skeleton-assisted`
- `from-scratch`

They are generation conditions, not high-level-controller architectures.

### 3.2 Independent generation replicate

One independent generation replicate is a fresh model-generation experiment, not one Repair and
not one validation trial. For robot `n`, Producer model `m`, and replicate `r`:

1. Start fresh model sessions and random state for TGCD and generation.
2. Produce one real-model `capability_design.json` with 5-10 capabilities from the same fixed 20+
   task snapshot.
3. Compile one complete `capability_validation_suite.json` and one recorded five-task
   `task_demo_suite.json`.
4. Reuse those sealed artefacts across the two matched generation conditions for that replicate.
5. Run the two conditions in isolated workspaces with independent traces and no cross-condition
   candidate sharing.

Within each condition, attempt `0` is the initial generated driver. Attempts `1` and `2` are bounded
Repairs of that same replicate. They are not additional replicates. Validation cases and Task Demo
episodes are nested observations under the generated driver and are not independent generation
samples.

Recommended counts:

| Stage | `R` | Purpose |
|---|---:|---|
| Diagnostic canary | 1 | Confirm the real chain reaches a trusted terminal verdict |
| Multi-robot pilot | 3 | Estimate failure modes and rough variance |
| Formal benchmark | 5 minimum; 10 preferred when budget permits | Estimate model and condition differences without treating Repair attempts as samples |

### 3.3 Fixed and varied inputs

Within a matched `robot x Producer backbone x replicate` block, both conditions share the same
robot package, source-backed tasks, capability design, validation suite, Task Demo sample, model
identity, environment, and total attempt limit. The only authorised difference is access to the
trusted skeleton versus permitted from-scratch primitives.

Across Producer backbones, keep model-independent public inputs, Framework code, Harness rules,
private-case construction policy, endpoints' decoding policy, and resource budgets fixed. Exact
model versions and endpoints must be pinned in the experiment manifest immediately before the run.

### 3.4 B1 outcomes

Primary metrics:

| Metric | Definition |
|---|---|
| `pass@0` | Fraction of independent generated-driver replicates passing the complete capability validation suite on attempt `0` |
| Final pass | Fraction passing within the maximum three submitted driver attempts |
| Repair gain | Final-pass proportion minus `pass@0` for the same cell |
| Attempts to pass | Submitted driver count until first complete pass; failures retain the attempt cap |
| Cell completion | Whether the named robot/model/condition/replicate reached a trusted terminal verdict |

Required secondary reporting:

- TGCD structural validity, task coverage, capability count, and IVC audit outcome;
- per-capability, per-clause, and per-case validation results;
- failure class: design/audit, import/API, smoke/runtime, physical criterion, guard, isolation, or
  resource limit;
- model calls, input/output tokens, wall time, development probes, MuJoCo steps, and estimated cost;
- Task Demo verdict reported separately from driver synthesis;
- paired condition contrast for every robot/model/replicate, including failed cells.

Do not pool away a failed robot or report a repaired pass as an initial pass.

## 4. B2: Capability-Interface Use

### 4.1 Experimental object

B2 measures whether different Consumer LLM backbones can use the same robot capability interface to
complete compositional physical tasks. For each robot, fix before Consumer trials:

- one model-generated capability interface;
- one fixed, validated model-generated driver implementing it;
- one Capability Router and public observation projection;
- one controller architecture, prompt, BT/task-graph schema, decision budget, and recovery policy;
- one task suite, private instances, seeds, timeouts, and Harness rules.

The driver must be selected by a prespecified, Consumer-blind rule from an admitted generation cell.
Record its Producer model, generation condition, replicate, and final attempt. Do not select the
driver after observing which one helps a Consumer model most.

The B2 matrix is:

```text
N eligible admitted robots
  x M Consumer LLM backbones
  x T compositional task templates
  x S matched private seeds
  x C independent controller episodes
```

The same backbone set should be used in B1 and B2 where endpoints support both roles, but the
Producer and Consumer results remain separate.

### 4.2 Recommended controller architecture

The primary controller should use this fixed architecture across all Consumer backbones:

1. **Task planner.** An LLM consumes only the public task, public capability contracts, bounded
   public state, and prior public call observations. It emits or patches a typed BT/task graph.
2. **Static checker.** Framework code rejects unknown capabilities, invalid request envelopes,
   cycles outside declared bounds, and plans exceeding call/turn budgets.
3. **Deterministic executive.** The Framework executes ordering, branching, bounded retry, timeout,
   and fallback semantics. It invokes only the Capability Router.
4. **Capability Router.** It maps a typed node to the fixed `method(request=request)` ABI. Every
   request contains `task_id` and `task_parameters` accepted by the public contract.
5. **Public outcome monitor.** After each synchronous capability call, the Framework returns only
   the exception, bounded public post-call state, and public progress observations. The planner may
   repair the remaining tree within a fixed replan budget.
6. **Independent Harness.** Private state, bindings, guards, criteria, and the physical verdict stay
   outside the controller path.

The current driver ABI is synchronous and generated methods commonly return `None`. The first B2
pilot therefore does not require a driver ABI redesign. The Framework can form post-call
observations from public state plus exceptions after each call. Event-triggered feedback during a
long-running capability is a later extension only if an observed pilot failure requires it.

Use an LLM over structured public observations by default. A VLM is eligible only when the same
declared public visual observation is available to every Consumer; the current benchmark does not
claim visual perception merely because recent hierarchical-control papers use VLMs.

### 4.3 Architecture controls and non-primary alternatives

| ID | Controller | Role |
|---|---|---|
| H0 | Hand-authored deterministic BT/task graph | Solvability and execution-path positive control; not a model leaderboard entry |
| H1 | Current bounded ReAct direct-call loop | Authority `0.19.18` baseline and ablation |
| H2 | LLM-generated/repaired BT or task graph with deterministic executive | Recommended formal B2 architecture |
| H3 | PDDL/HTN planner compiled to a BT executive | Optional later classical baseline after public symbolic preconditions/effects exist |

H1 versus H2 is an architecture-selection pilot, not part of the formal RQ1 backbone matrix. Once
H2 is selected and the Authority is aligned, formal B2 varies only the Consumer backbone. A formal
architecture factorial would be a new research question and must not be mixed into the current
backbone claim.

Direct VLA control, full task-and-motion planning, MPC, or policies that emit actuator commands are
not primary B2 alternatives because they bypass or duplicate the generated driver. Hierarchical VLA
research remains useful evidence for planner/executor separation, monitoring, and bounded replanning.

### 4.4 Compositional task suite

The existing five-task Task Demo is not sufficient by itself: a source task can map to one
capability and one driver call. B2 needs a separately sealed use suite that forces composition.

Recommended formal suite per robot:

- `T = 9` source-backed task templates;
- three sequential tasks, three conditional/recovery tasks, and three parameter/generalisation
  tasks;
- each nominal solution requires 2-5 capability calls and at least two distinct capabilities;
- `S = 3` matched private initialisations/seeds per template;
- `C = 2` independent controller episodes per template/seed;
- identical public task wording, seeds, call budget, and timeout across Consumer backbones.

Illustrative task families:

| Morphology | Compositional pattern |
|---|---|
| Fixed arm or bimanual | reach/open -> grasp -> transfer/place -> release/close |
| Hand | pre-shape -> grasp -> reorient -> stabilise -> release |
| Quadruped | stand -> move -> turn -> traverse -> stop, with bounded recover-stand on instability |
| Humanoid or legged arm | stabilise -> approach -> manipulate -> verify -> retreat |
| Mobile manipulator | navigate -> align base -> manipulate -> stow -> leave region |

The actual task definitions and thresholds must come from admitted source standards and the exact
robot's public affordances. The examples above are patterns, not pre-authored task contracts.

### 4.5 B2 outcomes

Primary metric:

- **Physical task success:** complete-task Harness success rate over the fixed matched episode set.

Secondary metrics:

| Dimension | Metric |
|---|---|
| Plan construction | Typed-plan parse/check rate; unknown-capability and invalid-request rate |
| Capability selection | Admissible selection rate under the public contracts and preconditions; unnecessary-call rate |
| Chaining | Valid ordering and argument binding; distinct-capability chain completion |
| Feedback use | Replan trigger count; recovery success after a failed or perturbed call |
| Progress | Fraction of source-backed task clauses or declared subgoals physically satisfied |
| Efficiency | Capability calls, planner turns, wall time, model tokens, and cost per episode |
| Safety and integrity | Guard violations, timeouts, direct-control attempts, and private-boundary violations |

Controller self-reported success is logged only as a diagnostic. It never replaces the Harness
verdict.

## 5. B3: Cross-Morphology Analysis

B3 reuses B1 outcomes from the fixed Experiment 3 cohort. Report:

- per-configuration outcomes before any morphology aggregation;
- morphology-category distributions of `pass@0`, final pass, failure class, attempts, model cost,
  and MuJoCo resource use;
- interactions between morphology category and generation condition as descriptive estimates;
- exact robot configuration, actuation, dynamics, task applicability, and control structure beside
  every morphology summary.

Do not state that morphology caused a difference. Morphology co-varies with actuators, dynamics,
tasks, assets, and controller structure.

## 6. Pairing and analysis

### 6.1 Statistical units

- B1 unit: one independently generated driver replicate.
- B1 nested observations: capabilities, cases, clauses, attempts, and Task Demo trials.
- B2 unit: one controller episode, blocked by robot, task template, seed, and controller replicate.
- B3 unit: the B1 driver replicate, with robot configuration retained explicitly.

### 6.2 Reporting

1. Publish raw numerator/denominator counts and confidence intervals for every named cell.
2. Pair the two B1 generation conditions within robot/model/generation replicate.
3. Pair B2 Consumer backbones on the same robot/task/seed/controller-replicate block.
4. Use a hierarchical logistic model or block bootstrap only as a secondary summary when sample
   size supports it; never let a model-derived aggregate hide raw failed cells.
5. Report per-robot results and macro-average across robots. Do not micro-average all validation
   cases as if they were independent drivers.
6. Keep exploratory architecture-pilot results separate from formal backbone comparisons.

## 7. Execution phases

| Phase | Scope | Exit evidence |
|---|---|---|
| P0: Calibration | Reference drivers, Harness, false-success checks, and video path | Complete trusted calibration reports and videos |
| P1: Mainline acceptance | Two robots x two generation conditions, real model, `R=1` diagnostic | Four named cells reach terminal verdicts; success claims follow Authority Section 6.2 |
| P2: Robot admission | Build candidates in research-index order; admit only complete packages | Versioned runnable index and Experiment 3 manifest with `N > 2` |
| P3: Multi-robot synthesis pilot | All selected robots, `M` Producer models, `R=3` | Variance/failure report; no protocol changes after formal inputs are fixed |
| P4: High-level-controller pilot | H0, H1, and H2 on one arm and one quadruped | Architecture choice, observed ABI gaps, bounded controller contract |
| P5: Formal B1/B2/B3 | Fixed cohort, models, suites, budgets, `R>=5` | Complete cell reports, per-trial videos, paired analysis, declared limitations |

P1 remains valuable engineering evidence but is not substituted for P5. Start real runs as soon as
the minimum path exists; do not delay them for speculative controller middleware or broad schemas.

## 8. Required run artefacts

For each B1 generation replicate retain:

- experiment configuration and exact robot/package/task snapshot IDs;
- real model provider, exact model ID, decoding settings, and call records;
- `capability_design.json` and IVC audit;
- unchanged `capability_validation_suite.json` identity across matched conditions and Repairs;
- condition-specific STUDY/GENERATE trace and every submitted `driver.py`;
- candidate-facing reports, trusted terminal verdicts, resource use, and per-trial videos;
- separate Task Demo report and videos.

For each B2 episode retain:

- fixed interface/driver identity and its originating B1 cell;
- public task, public observations, typed BT/task graph, static-check result, calls, and replans;
- controller model calls and budget use;
- independent Harness report, guard results, trajectory summary, and video.

Human-readable IDs, versions, paths, and run IDs are sufficient. This protocol does not add
cryptographic evidence chains, registries, lifecycle states, or promotion workflows.

## 9. Literature basis

The architecture and metrics are grounded in four lines of robotics research:

1. **Classical task planning and TAMP** establish the distinction between discrete task-level
   decisions and continuous robot execution, but do not justify bypassing the generated driver.
2. **Affordance-grounded and feedback-driven LLM planning** motivate capability selection and
   closed-loop replanning from observed execution outcomes.
3. **Behavior-tree generation and evaluation** motivate a typed, reactive executive and explicit
   plan validity, progress, and robustness metrics.
4. **Hierarchical VLA and embodied-tool work** motivates bounded low-level executors, explicit
   capability selection, progress feedback, and capability-chain evaluation. Their learned action
   policies or registry machinery are not copied into the Direct-MuJoCo mainline.

Core references now present in the Zotero library; the 13 newly imported records are in the
`Auto_Adapter` collection:

| Topic | Reference |
|---|---|
| Robot task planning | Karpas and Magazzeni, [Automated Planning for Robotics](https://doi.org/10.1146/annurev-control-082619-100135), 2020 |
| Task and motion planning | Garrett et al., [Integrated Task and Motion Planning](https://doi.org/10.1146/annurev-control-091420-084139), 2021 |
| Behavior Trees | Colledanchise and Ogren, [Behavior Trees in Robotics and AI](https://arxiv.org/abs/1709.00084), 2018 |
| Affordance-grounded capability selection | Ahn et al., [Do As I Can, Not As I Say](https://arxiv.org/abs/2204.01691), 2022 |
| Closed-loop language feedback | Huang et al., [Inner Monologue](https://proceedings.mlr.press/v205/huang23c.html), 2023 |
| Interactive capability execution | Li et al., [Interactive Task Planning with Language Models](https://openreview.net/forum?id=VmfWywWuYQ), 2025 |
| LLM-generated BTs | Ao et al., [LLM as BT-Planner](https://arxiv.org/abs/2409.10444), ICRA 2025 |
| Code-mediated BT generation | Zhang et al., [Code-BT](https://www.ijcai.org/proceedings/2025/980), IJCAI 2025 |
| BT benchmark design | Chen et al., [BTPG](https://www.ijcai.org/proceedings/2025/969), IJCAI 2025 |
| Hierarchical VLA control | Shi et al., [Hi Robot](https://arxiv.org/abs/2502.19417), 2025 |
| VLM planning and monitoring | Schakkal et al., [Hierarchical Vision-Language Planning for Multi-Step Humanoid Manipulation](https://arxiv.org/abs/2506.22827), 2025 |
| Steerable hierarchical policies | Chen et al., [Steerable Vision-Language-Action Policies](https://arxiv.org/abs/2602.13193), 2026 preprint |
| Capability-driven planning | Xu et al., [RoboAgent](https://openaccess.thecvf.com/content/CVPR2026/html/Xu_RoboAgent_Chaining_Basic_Capabilities_for_Embodied_Task_Planning_CVPR_2026_paper.html), CVPR 2026 |
| VLA tools and progress feedback | Lei et al., [Towards Long-Horizon Embodied Agents with Tool-Aligned Vision-Language-Action Models](https://arxiv.org/abs/2605.13119), 2026 preprint |
| Embodied tool-use dimensions | Zhou et al., [Enabling Extensible Embodied Capabilities with Tools](https://arxiv.org/abs/2605.26637), 2026 preprint |

The 2026 preprints are architecture and metric inputs, not established benchmark standards. Formal
claims must remain bounded to this project's independent Direct-MuJoCo evidence.

## 10. Decisions before formal runs

1. Confirm whether to revise the Authority from the current direct ReAct controller to H2, or keep
   ReAct as the formal path and treat H2 only as a separate exploratory experiment.
2. Admit and declare the Experiment 3 robot cohort; do not use the two-robot acceptance pair as the
   cohort by default.
3. Select and pin at least three Producer/Consumer backbone endpoints using a provider-diverse,
   predeclared rule.
4. Fix `R`, B2 task templates, seeds, controller episode count, call/turn budgets, and driver
   selection rule before inspecting formal model outcomes.
5. Run P1 immediately, then use only observed P3/P4 failures to justify any additional mechanism.
