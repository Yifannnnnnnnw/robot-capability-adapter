# AutoAdapter-Bench Protocol

> **Status:** approved design draft; formal evidence remains blocked until the
> applicable manifests and mainline admission gates are frozen<br>
> **Authority baseline:** `AA2-AUTH` revision `0.19.21`<br>
> **Prepared:** 2026-08-21<br>
> **Runtime boundary:** canonical `autoadapter/` Direct-MuJoCo mainline

## 1. Research design

AutoAdapter-Bench contains two separate tracks. B1 compares LLM backbones in
robot-specific driver synthesis. B2 compares LLM backbones in using a fixed
robot capability interface under selected high-level-controller architectures.
The tracks have different experimental units and outcomes and are never merged
into one aggregate score.

| Track | Experimental unit | Manipulated factor | Primary outcome |
|---|---|---|---|
| B1: Driver Synthesis | One independent generation-condition replicate | Producer backbone; generation condition on the declared subset | Driver passes the complete private validation suite within the attempt budget |
| B2: Capability-Interface Use | One physical controller episode | Backbone within an admitted fixed controller architecture | Complete task physically passes the trusted Harness standard |

Robot configuration is a blocking factor in Chapter 3, not the explanatory
object. Per-robot outcomes are reported, but Chapter 3 does not estimate
morphology effects.

## 2. Complete benchmark scope

### 2.1 Robot inventory and Chapter 3 cohort

The reusable full-benchmark inventory contains 14 exact configurations. Chapter
3 selects ten of them before outcomes are inspected:

| Category | Chapter 3 B1 configurations | Expansion-only configurations |
|---|---|---|
| Fixed serial manipulator | `robotstudio_so101`, `franka_panda`, `universal_robots_ur5e_robotiq_2f85` | `kinova_gen3_robotiq_2f85`, `ufactory_xarm7`, `piper`, `kuka_iiwa_14` |
| Hand | `leap_hand` | None |
| Quadruped | `unitree-go2-stock-12dof`, `google_barkour_vb` | None |
| Humanoid | `unitree_g1` | None |
| Mobile manipulator | `hello_robot_stretch_2` | None |
| Bimanual | `aloha_2` | None |
| Legged arm | `boston_dynamics_spot_with_arm` | None |

The ten-case selection retains every non-fixed-arm form factor while reducing
the seven closely related fixed serial-manipulator configurations to three
representatives. Inventory membership, Chapter 3 selection, package
availability, and runnable admission are separate facts. As of this protocol
revision,
`boston_dynamics_spot_with_arm` has research records but no canonical package
under `autoadapter/libraries/robots/`; only `robotstudio_so101` and
`unitree-go2-stock-12dof` are currently in the mainline runnable index. These
gaps block affected formal units and do not authorise silent replacement.

Robot-set files select canonical IDs. Environment creation, assets, tasks,
interfaces, drivers, and private suites always resolve through the mainline.

### 2.2 Producer backbones

| ID | Model family | Vendor |
|---|---|---|
| M1 | Sonnet 4.6 | Anthropic |
| M2 | Opus 5 | Anthropic |
| M3 | Haiku 4.5 | Anthropic |
| M4 | Nova Pro | Amazon |
| M5 | DeepSeek V4 Pro | DeepSeek |
| M6 | Ministral 3 8B | Mistral |
| M7 | Qwen3 32B | Alibaba |

The backbone registry owns exact provider identifiers, transports, inference
settings, context and output limits, and price snapshots. Credentials remain
environment-only. Formal runs require fixed identifiers rather than movable
`latest` aliases.

## 3. B1: Driver synthesis

### 3.1 Condition coverage

The primary condition is `skeleton-assisted` across the ten selected Chapter 3
robots:

```text
10 robots x 7 backbones x 1 condition x 5 replicates
= 350 generation-condition replicates
```

The `from-scratch` condition is a prespecified scaffolding ablation on five
contrasting cases:

```text
5 robots x 7 backbones x 1 condition x 5 replicates
= 175 generation-condition replicates
```

The selected cases are `robotstudio_so101`, `franka_panda`,
`unitree-go2-stock-12dof`, `hello_robot_stretch_2`, and `unitree_g1`.
The complete B1 design therefore contains 525 generation-condition replicates.
At no more than three submitted attempts per condition, the maximum is 1,575
driver submissions.

For every selected five-robot `robot x backbone x replicate` block, the two
conditions reuse one sealed capability design and one sealed complete
validation suite. Their model sessions, workspaces, candidate drivers,
validation feedback, and Repair histories remain isolated. The other five
robots enter only the primary condition.

### 3.2 B1 execution boundary

B1 executes:

```text
TGCD -> IVC and sealed suite -> STUDY -> GENERATE
     -> private driver validation -> bounded Repair -> final driver outcome
```

B1 stops after final driver validation. It does not execute Task Demo, a
high-level controller, compositional B2 tasks, or controller task-success
evaluation. Reference calibration establishes suite feasibility but is not a
model condition.

Attempt 0 is the initial submitted driver. Attempts 1 and 2 are bounded Repairs
of the same replicate. Repairs and validation cases are not independent
samples.

### 3.3 B1 outcomes

Primary outcomes are `pass@0`, final pass within three attempts, valid-driver
count and rate, Repair gain, attempts to first pass, and cell completion.
Resource outcomes are model calls, provider-reported token categories, model
cost, stage and total wall time. Failure classes and per-capability validation
outcomes are secondary diagnostics.

## 4. B2: Capability-interface use

B2 fixes one validated driver and capability interface per robot before
controller outcomes are inspected. Chapter 3 preselects the two package
reference drivers, avoiding any post-outcome choice among B1-generated drivers.
They are not formally admitted until each driver is bound to the selected
interface through the audited adapter and passes the complete interface-bound
validation suite.

Code-BT is the first planned Chapter 3 architecture. LLM-as-BT-Planner may be
added after a separate audit. Inner Monologue, SayCan, ReAct, Code as Policies,
UHBTP, and HBTP remain catalogued candidates even when they are not selected
for the Chapter 3 formal recipe.

Backbone comparison is valid only within a controller whose declared model role
can be replaced without retraining or changing method logic. Each architecture
therefore declares eligible robot and backbone sets before formal task outcomes.

For one admitted architecture with all seven backbones eligible, the Chapter 3
matrix is:

```text
2 robots x 7 backbones x 5 task templates x 2 private seeds x 2 episodes
= 280 controller episodes
```

The two robots are `robotstudio_so101` and
`unitree-go2-stock-12dof`. The primary B2 outcome is complete-task physical
success decided by the trusted Harness. Secondary outcomes include valid
capability selection and argument binding, plan/code/BT validity,
capability-chain completion, invalid or unnecessary calls, resource use, and
failure class.

## 5. Composition and execution

An experiment recipe selects one locked protocol plus reusable robot, backbone,
task, and replicate components. A recipe may reduce a cohort or select an
architecture; it may not override the Harness, pass standards, attempt budget,
private input policy, driver-selection rule, or required recording fields.

Before execution, the runner resolves all references into
`runs/<run-id>/resolved_manifest.json`, including the exact unit list and
expected count. It then calls the canonical mainline for environment creation,
synthesis, validation, and evidence. Benchmark runners do not copy robot
packages or implement a second Harness.

Provider or package failures remain explicit unit outcomes. An unavailable
model, ineligible architecture pairing, missing package, or unadmitted robot is
not removed from the declared denominator after outcomes are inspected.

## 6. Required recording contract

Every unit records:

- run ID, unit ID, protocol version, experiment recipe, Git commit, robot,
  backbone, condition or controller, replicate, task, seed, and episode as
  applicable;
- vendor, exact model ID, transport, inference settings, token limit, timeout,
  provider request ID, retry count, and provider error;
- UTC start and end timestamps plus monotonic total and stage wall times;
- provider-reported input, output, cache-read, cache-write, reasoning, and
  other token categories when available, preserving unavailable fields as
  null rather than estimating them;
- frozen price-snapshot date, currency, unit prices, per-call cost, and unit
  total cost;
- event stage, model-turn index, tool name, tool outcome, elapsed time,
  submission event, and stage transition;
- candidate, report, event trace, video, and trusted-evidence paths relative
  to the retained run.

B1 additionally records capability-design and suite identity, condition-pair
identity, attempt index, source-audit outcome, public-smoke outcome, private
case counts and verdicts, Repair transition, final validation verdict,
no-valid-submission reason, and terminal failure class. TGCD and IVC shared
resources are counted once per `robot x backbone x replicate` block and are
not duplicated across paired conditions.

For synthesis-loop visualisation, each observable model turn is classified as
`observe_or_plan`, `execute_clean`, `execute_error`, or `submit`. STUDY,
GENERATE, and REPAIR boundaries are explicit. The benchmark never requests or
records hidden chain-of-thought. A clean tool execution is not a Harness pass.

B2 additionally records controller version and model role, fixed driver and
interface identity, task template, private seed, episode index, controller
output validity, public capability-call sequence and arguments, argument
binding, capability execution outcomes, call-budget use, controller
self-reported completion, every trusted task-clause result, and final Harness
physical verdict. Controller completion and Harness success are separate
fields.

API keys and private candidate-inaccessible Harness state are never written to
model traces or public result records.

## 7. Analysis and readiness

Raw run outputs are immutable inputs to later analysis. The `analysis/`
directory initially contains only its boundary documentation. Table, figure,
cost, and failure-analysis scripts are added independently after real data
exist; they do not run automatically.

The protocol nevertheless fixes primary outcomes, statistical units,
denominators, macro-averaging, and failed/blocked-cell treatment before formal
outcomes are inspected. B1 reports robot-backbone-condition cells and
robot-level macro-averages. B2 reports backbone comparisons within each
architecture; cross-architecture interaction analysis requires a genuinely
matched subset and does not imply a universal backbone ranking.

Formal execution remains blocked until the applicable robot packages are
admitted, all seven provider transports pass canaries, exact model and price
settings are frozen, the B1 synthesis-only stage boundary is available, and
the selected B2 controller adapters pass their source and capability-boundary
audits.
