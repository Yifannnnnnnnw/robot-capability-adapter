# Auto-Adapter 2.0 Benchmark Protocol

> **Status:** protocol draft; non-normative until aligned with `AUTOADAPTER_2_AUTHORITY.md`<br>
> **Authority baseline:** `AA2-AUTH` revision `0.19.18`<br>
> **Prepared:** 2026-08-20<br>
> **Execution scope:** canonical `autoadapter/` Direct-MuJoCo mainline only<br>
> **Robot cohort:** one fixed cohort of 14 configurations listed in Section 2<br>
> **Excluded from denominators:** real SDK, Translation, hardware, and historical `demo2/`

## 0. Executive decision

The benchmark is not a flat comparison of generic Agent patterns. It contains two separate
sub-benchmarks:

| Track | Scientific object | Manipulated factor | Fixed core |
|---|---|---|---|
| **B1: Driver Synthesis** | A generated robot-specific driver | Producer LLM backbone and the two Authority-defined generation conditions | Auto-Adapter workflow, robot inputs, suites, Harness, budgets |
| **B2: Published High-Level Control** | A published high-level-controller method using one fixed interface and one fixed validated generated driver | Published architecture `a`, plus `b in B_a` only when that architecture exposes a replaceable LLM/VLM backbone | Driver, capability exposure, tasks, seeds, outer execution budgets, Harness |

No single aggregate score combines B1 and B2. B1 asks which models can synthesise drivers. B2 does
not claim a new Auto-Adapter high-level controller: it compares selected methods from the robotics
literature above the same generated driver and capability boundary.

Every B2 method follows this common integration boundary while retaining its published planning,
feedback, and execution logic:

```text
selected published controller method a
  [backbone b in B_a only when the method permits replacement]
              |
              v
 thin method adapter: public observation and capability-name/ABI mapping only
              |
              v
       Capability Router
              |
              v
 fixed capability interface + fixed validated generated driver
              |
              v
          Direct MuJoCo

Private Harness state and verdict remain on an independent path.
```

There is no global `M`-backbone factor in B2. A selected architecture `a` receives a predeclared
valid backbone set `B_a` only when its published high-level model is replaceable; B2 then evaluates
`a x B_a x other factors`. If the method is a fixed trained checkpoint, or replacement would
require retraining or alter the published method, it has no backbone factor and B2 evaluates only
`a x other factors` with that system pinned. The resulting design is intentionally unbalanced
across architectures.

**Authority decision required.** Revision `0.19.18` currently declares one fixed bounded ReAct
controller for formal capability-interface use. This draft instead requires a predeclared set of
published high-level-controller methods and architecture-specific backbone treatment. Until the
Authority is explicitly aligned, these comparisons can run only as exploratory pilots; evidence
under the current Authority must continue to use its declared ReAct path. This draft does not
silently override the Authority.

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

## 2. Fixed 14-robot cohort

The benchmark defines one scientific cohort with `N = 14`. `robotstudio_so101` and
`unitree-go2-stock-12dof` are members of this same cohort, not a separate benchmark group. Current
package readiness and implementation order do not create different scientific populations or
denominators.

| Morphology category | Robot configurations |
|---|---|
| Fixed serial manipulator | `robotstudio_so101`, `franka_panda`, `kinova_gen3`, `ufactory_xarm7`, `universal_robots_ur5e`, `piper`, `kuka_iiwa_14` |
| Hand | `leap_hand` |
| Quadruped | `unitree-go2-stock-12dof`, `google_barkour_vb` |
| Humanoid | `unitree_g1` |
| Mobile manipulator | `hello_robot_stretch_2` |
| Bimanual | `aloha_2` |
| Legged arm | `boston_dynamics_spot_with_arm` |

### 2.1 Formal cohort rule

Before Experiment 3, one versioned experiment manifest must declare all 14 configurations and:

- each `robot_configuration_id` and morphology category;
- exact package and Task Library snapshot versions;
- the Producer backbone set and, for every selected B2 method, its source/version,
  backbone-replacement classification, and valid `B_a` or fixed-system identity;
- generation replicate count, task instances, seeds, and budgets.

Every configuration must satisfy the same Authority-defined Task Library, source-lineage,
asset-closure, validation, and evidence requirements before its formal cell runs. Package completion
is an execution prerequisite, not cohort membership. Diagnostic cells may run as implementations
become available, but the formal benchmark is incomplete until all 14 configurations have the
required B1 and B2 results. No ready subset becomes a replacement headline cohort.

**Authority consistency note.** `AA2-AUTH` revision `0.19.18` still uses an older robot-set and
research-question structure. The Authority must be synchronised with this fixed 14-robot,
two-sub-benchmark scope before formal benchmark evidence is collected; until then, the Authority
remains normative.

## 3. B1: Driver Synthesis

### 3.1 Experimental matrix

The primary matrix is:

```text
14 fixed-cohort robots (N = 14)
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

## 4. B2: Published High-Level Control

### 4.1 Experimental object

B2 compares selected published high-level-controller methods that can use the same generated robot
capabilities to complete compositional physical tasks. For each robot, fix before any method trial:

- one model-generated capability interface;
- one fixed, validated model-generated driver implementing it;
- one Capability Router and bounded public observation projection;
- one task suite, private instances, seeds, physical timeouts, capability-call caps, and Harness
  rules.

The controller algorithm, prompts, planners, scorers, feedback loop, and execution semantics are not
globally fixed: they define the published method being compared. They must instead remain fixed
within each architecture `a`, apart from an explicitly valid backbone replacement.
Method-native planning or replan budgets are likewise frozen within `a`; the outer physical timeout
and capability-call cap remain common across methods.

The driver must be selected by a prespecified, controller-method-blind rule from an admitted B1
cell. Record its Producer model, generation condition, replicate, and final attempt. Do not select a
driver after observing which one helps a controller or backbone most. If the selected generated
driver is unavailable for a robot, complete that blocking cell before formal B2 runs; do not shrink
the 14-robot cohort.

### 4.2 Architecture-specific experimental matrix

Let `A` be the predeclared set of published controller methods. Each method is assigned one of three
backbone modes before formal task outcomes are inspected:

- `replaceable`: the published high-level LLM/VLM is an external module and `B_a` is the set of
  compatible backbones that can be substituted without retraining or changing method logic;
- `fixed_system`: the controller is a trained checkpoint or coupled learned system; it has no
  backbone factor and its exact published checkpoint/system identity is pinned;
- `no_learned_backbone`: the method is classical or deterministic; it has no backbone factor and
  one published algorithm configuration is pinned.

The formal design is the following union of method-specific blocks:

```text
for every replaceable method a in A_replaceable:
  14 fixed-cohort robots (N = 14)
    x every valid b in B_a
    x T compositional task templates
    x S matched private seeds
    x E independent controller episodes

for every method a in A_fixed or A_no_learned_backbone:
  14 fixed-cohort robots (N = 14)
    x T compositional task templates
    x S matched private seeds
    x E independent controller episodes
```

Thus a replaceable method contributes `architecture a x |B_a| backbones x other factors`. A fixed
trained method contributes only `architecture a x other factors`, with its checkpoint held fixed.
There is no global B2 backbone count, no requirement that all `B_a` have the same size, and no
invented backbone swap added merely to make a rectangular table. B1 Producer models and B2
backbone sets are selected independently because they answer different questions.

Within a replaceable architecture, matched `b in B_a` comparisons estimate a backbone effect for
that method. Across architectures, the default estimand is a complete published-system comparison.
An `architecture x backbone` interaction may be estimated only on the subset of architectures and
backbones that form a genuinely matched factorial block.

### 4.3 Backbone-replacement audit

Before declaring a method `replaceable` or admitting a model to `B_a`, the reproduction audit must
establish all of the following from the paper and pinned public implementation:

1. The high-level foundation model is exposed as a separable inference module.
2. Replacing it does not retrain or replace controller policies, affordance/value functions,
   low-level skills, capability implementations, or feedback modules.
3. Every proposed replacement supports the method's required interface, such as option likelihoods,
   tool calls, code generation, structured output, or visual input.
4. The method's algorithm, prompts or in-context examples, planning/execution semantics, decoding
   policy, budgets, and non-backbone weights remain fixed across `B_a`. Only provider transport and
   mechanically equivalent schema formatting may differ.
5. A held-out compatibility check, separate from formal tasks, confirms that the replacement can
   enter and exit the published method interface without method-specific repair.

If conditions 1-2 fail, treat an otherwise executable released method as `fixed_system`. If a
candidate model fails conditions 3-5, exclude that model from `B_a`; do not repair the method around
it. A method with neither a valid replacement path nor an executable fixed system is not admitted.
Do not call a retrained alternative a backbone substitution. Training the same architecture around
several foundation models would be a separate training benchmark and is outside B2.

### 4.4 Candidate methods and preliminary classification

The following literature methods are candidates for the driver-compatibility reproduction audit.
The labels are preliminary until the P4 paper/code audit is recorded; passing that audit, rather
than popularity, determines the final set `A`.

| Published method or variant | High-level mechanism | Preliminary backbone treatment | Fit above the generated driver |
|---|---|---|---|
| SayCan | LM option scoring combined with a fixed skill affordance/value score | `replaceable` among LMs exposing comparable option scores; keep the affordance component fixed | Conditional: every capability needs a predeclared public affordance/value scorer; driver mapping alone is insufficient |
| Inner Monologue | Prompted LM planning with execution-success, scene, or human-language feedback | `replaceable` among compatible text LMs; keep the feedback projection and prompts fixed | Strong for post-call public feedback and replanning |
| Interactive Task Planning with Language Models | Function/tool-calling task planner with interaction history and replanning | `replaceable` only among models supporting the required tool-call contract | Strong because public capabilities are the tools |
| LLM as BT-Planner, prompting/ICL variant | LLM emits a Behavior Tree under a published grammar and examples | `replaceable` among models satisfying the same tree-output contract | Strong if the published BT executor calls only public capabilities |
| LLM as BT-Planner, fine-tuned variant | A separately fine-tuned smaller LLM emits the Behavior Tree | `fixed_system`; no backbone factor; pin the exact evaluated checkpoint if reproducibly available | Conditional on checkpoint/code availability; another base model is not a valid swap without repeating training |
| Code-BT | LLM generates API-using code whose control flow is extracted into a Behavior Tree | Candidate `replaceable` set limited to code-capable LMs; final classification requires code audit | Strong if API leaves map exactly to public capabilities and the published parser/executor is unchanged |
| HBTP | LLM reasoning supplies a heuristic path, action-space pruning, and reflective feedback to a fixed BT planner | Candidate `replaceable` set limited to LMs satisfying the same symbolic heuristic contract; keep BT expansion fixed | Strong when action models are derived only from public capability preconditions/effects |
| UHBTP from BTPG | Domain-independent heuristics guide published symbolic BT planning | `no_learned_backbone`; no backbone factor and one algorithm configuration is pinned | Strong classical control if its action model can be derived without private evaluation state |

If a paper provides several independently trained checkpoints, predeclare each selected checkpoint
as a distinct fixed-system variant. Their comparison is a complete-system comparison, not a
backbone factor.

RoboAgent, Hi Robot, hierarchical humanoid VLM planning, Steerable VLA, and VLAs-as-Tools are not
automatically admitted B2 cells. Their published systems couple high-level reasoning to trained
VLM/VLA capability or action components. A selected fixed system from this group may enter only if
its high-level component can invoke the same generated driver without retraining or replacing
published method logic; it then contributes only `architecture x other factors`, with the released
system fixed. Otherwise it remains literature context because it would bypass or duplicate the
driver.

The current bounded ReAct controller remains the Authority `0.19.18` baseline until the Authority is
revised. It is not treated as a sufficient new robot high-level-control architecture merely to fill
the comparison table. A hand-authored deterministic controller remains a solvability calibration,
not a leaderboard method.

### 4.5 Method-faithful integration rule

The Auto-Adapter integration layer may only:

- map published action, skill, or tool names to fixed public capability IDs;
- convert a published call into the fixed `method(request=request)` envelope;
- project the common bounded public observation into the method's declared input form; and
- normalize provider transport or mechanically equivalent schemas across a valid `B_a`.

It may not add a planner, BT executive, affordance scorer, retry policy, recovery state machine,
memory, or feedback channel absent from the published method. It may not combine components from
different papers, expose private Harness state, emit actuator commands, or repair one method's
outputs with another model. Method-specific dependencies such as SayCan's affordance score or a
published BT executor are part of architecture `a` and remain fixed across that architecture's
backbone block. Every nontrivial adaptation and deviation from public code must be reported.

The synchronous driver ABI may remain unchanged for the first pilot. A method receives only the
public post-call state and exceptions that its published feedback contract permits. A VLM method is
eligible only if the same declared public visual observation is available in every matched cell.

### 4.6 Compositional task suite

The existing five-task Task Demo is not sufficient by itself: a source task can map to one
capability and one driver call. B2 needs a separately sealed use suite that forces composition.

Recommended formal suite per robot:

- `T = 9` source-backed task templates;
- three sequential tasks, three conditional/recovery tasks, and three parameter/generalisation
  tasks;
- each nominal solution requires 2-5 capability calls and at least two distinct capabilities;
- `S = 3` matched private initialisations/seeds per template;
- `E = 2` independent controller episodes per template/seed;
- identical public task wording, seeds, capability-call budget, and timeout for every eligible
  architecture and, where replaceable, every `b in B_a`.

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

### 4.7 B2 outcomes

Primary metric:

- **Physical task success:** complete-task Harness success rate over the fixed matched episode set.

Universal secondary metrics:

| Dimension | Metric |
|---|---|
| Capability use | Valid public-capability selection and argument-binding rate; unknown or inadmissible call rate |
| Chaining | Valid ordering; distinct-capability chain completion; unnecessary-call rate |
| Progress | Fraction of source-backed task clauses or declared subgoals physically satisfied |
| Efficiency | Capability calls, model/planner turns where applicable, wall time, tokens, and cost per episode |
| Safety and integrity | Guard violations, timeouts, direct-control attempts, and private-boundary violations |

Method-specific diagnostics, such as LM option scores, BT parse/check rate, generated-code validity,
or replan/recovery counts, are reported only where the published method produces them. Mark other
cells `not applicable`; do not score an architecture as failing for lacking another architecture's
internal artefact. Controller self-reported success is diagnostic only and never replaces the
Harness verdict.

## 5. Pairing and analysis

### 5.1 Statistical units

- B1 unit: one independently generated driver replicate.
- B1 nested observations: capabilities, cases, clauses, attempts, and Task Demo trials.
- B2 unit: one controller episode, blocked by architecture and, where applicable, valid backbone;
  then by robot, task template, seed, and episode replicate.

### 5.2 Reporting

1. Publish raw numerator/denominator counts and confidence intervals for every named cell.
2. Pair the two B1 generation conditions within robot/model/generation replicate.
3. Within each `replaceable` B2 architecture, pair its valid backbones on the same
   robot/task/seed/episode block.
4. Compare different B2 architectures as complete systems unless a common-backbone matched block
   supports an explicit `architecture x backbone` analysis. Never infer a global backbone ranking
   from the unbalanced union of `B_a` sets.
5. Use a hierarchical logistic model or block bootstrap only as a secondary summary when sample
   size supports it; never let a model-derived aggregate hide raw failed cells.
6. Report per-robot results and macro-average across robots. Do not micro-average all validation
   cases as if they were independent drivers.
7. Report fixed trained systems without a counterfactual backbone effect and keep failed or
   unsupported method cells visible.

## 6. Execution phases

| Phase | Scope | Exit evidence |
|---|---|---|
| P0: Calibration | Reference drivers, Harness, false-success checks, and video path | Complete trusted calibration reports and videos |
| P1: Cohort package completion | Complete the Authority-required package inputs for all 14 robots; run named diagnostics as each implementation becomes available | One manifest declares all 14 and every package resolves through the canonical loader |
| P2: Full-cohort canary | 14 robots x two generation conditions, one real model, `R=1` | All 28 named cells reach trusted terminal verdicts; failures remain visible |
| P3: Full-cohort synthesis pilot | All 14 robots, `M` Producer models, `R=3` | Variance/failure report; no protocol changes after formal inputs are fixed |
| P4: Published-controller audit and pilot | Reproduce each candidate method, decide driver compatibility and backbone mode, then run one held-out compatibility cell | Final `A`; every applicable `B_a`; pinned fixed-system/algorithm identities, paper/code versions, adaptation record, observed ABI gaps |
| P5: Formal B1/B2 | Fixed 14-robot cohort, declared method-specific matrices, suites, budgets, `R>=5` | Complete cell reports, per-trial videos, within-method backbone contrasts, whole-system method comparisons, declared limitations |

Early diagnostic cells are implementation evidence within the same cohort. Start them as soon as
the minimum path exists, but retain the fixed 14-robot denominator for P2-P5 and do not delay real
runs for speculative controller middleware or broad schemas.

## 7. Required run artefacts

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
- published method name, paper/code version, architecture variant, backbone mode, and exact
  backbone/checkpoint/algorithm identity;
- the applicable predeclared `B_a` or no-backbone classification, replacement audit, and method
  adaptation/deviation record;
- public task, public observations, capability calls, and method-native plans, trees, code, scores,
  feedback, or replans where applicable;
- controller/model calls and budget use where applicable;
- independent Harness report, guard results, trajectory summary, and video.

Human-readable IDs, versions, paths, and run IDs are sufficient. This protocol does not add
cryptographic evidence chains, registries, lifecycle states, or promotion workflows.

## 8. Literature basis

The references below supply candidate published controller methods, their method-specific
dependencies, and benchmark metrics. They do not define a new Auto-Adapter controller assembled
from selected paper components. The reproduction audit must use each cited paper together with its
pinned public implementation to make the final `replaceable`, `fixed_system`, or
`no_learned_backbone` decision.

The candidates and metrics are grounded in four lines of robotics research:

1. **Classical task planning and TAMP** establish the distinction between discrete task-level
   decisions and continuous robot execution, but do not justify bypassing the generated driver.
2. **Affordance-grounded and feedback-driven LLM planning** motivate capability selection and
   closed-loop replanning from observed execution outcomes.
3. **Behavior-tree generation and evaluation** motivate a typed, reactive executive and explicit
   plan validity, progress, and robustness metrics.
4. **Hierarchical VLA and embodied-tool work** motivates bounded low-level executors, explicit
   capability selection, progress feedback, and capability-chain evaluation. Their learned action
   policies or registry machinery are not copied into the Direct-MuJoCo mainline.

Core references now present in the Zotero library; the 14 newly imported records are in the
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
| LLM-guided BT planning | Cai et al., [HBTP](https://doi.org/10.1109/ICRA55743.2025.11127999), ICRA 2025 |
| BT benchmark and UHBTP | Chen et al., [BTPG](https://www.ijcai.org/proceedings/2025/969), IJCAI 2025 |
| Hierarchical VLA control | Shi et al., [Hi Robot](https://arxiv.org/abs/2502.19417), 2025 |
| VLM planning and monitoring | Schakkal et al., [Hierarchical Vision-Language Planning for Multi-Step Humanoid Manipulation](https://arxiv.org/abs/2506.22827), 2025 |
| Steerable hierarchical policies | Chen et al., [Steerable Vision-Language-Action Policies](https://arxiv.org/abs/2602.13193), 2026 preprint |
| Capability-driven planning | Xu et al., [RoboAgent](https://openaccess.thecvf.com/content/CVPR2026/html/Xu_RoboAgent_Chaining_Basic_Capabilities_for_Embodied_Task_Planning_CVPR_2026_paper.html), CVPR 2026 |
| VLA tools and progress feedback | Lei et al., [Towards Long-Horizon Embodied Agents with Tool-Aligned Vision-Language-Action Models](https://arxiv.org/abs/2605.13119), 2026 preprint |
| Embodied tool-use dimensions | Zhou et al., [Enabling Extensible Embodied Capabilities with Tools](https://arxiv.org/abs/2605.26637), 2026 preprint |

The 2026 preprints are architecture and metric inputs, not established benchmark standards. Formal
claims must remain bounded to this project's independent Direct-MuJoCo evidence.

## 9. Decisions before formal runs

1. Confirm whether to revise the Authority from one fixed ReAct controller to the predeclared set
   of published B2 methods with architecture-specific backbone treatment; until then, keep the new
   method comparison exploratory.
2. Declare all 14 robot configurations in one Experiment 3 manifest and use one common cohort
   denominator.
3. Select and pin the B1 Producer endpoints independently. For B2, freeze final `A` and record each
   method's paper/code version, applicable `B_a` or pinned no-backbone system/algorithm identity,
   and driver adapter.
4. Fix `R`, B2 task templates, seeds, `E`, common outer execution budgets, each architecture's
   method-native internal budget, and the controller-method-blind driver-selection rule before
   inspecting formal outcomes.
5. Run named diagnostics as packages become available, then use only observed P3/P4 failures to
   justify any additional mechanism.
