# AutoAdapter 2.0 Two-Robot General Demo Plan

## Document control

| Field | Value |
|---|---|
| Status | `ACTIVE_NON_NORMATIVE_PLAN` |
| Implementation authorization | `EXPERIMENT_GRADE_FULL_DEMO_AUTHORIZED` |
| Sole normative authority | `AUTOADAPTER_2_AUTHORITY.md` |
| Authority revision read for this plan | `0.15.0` |
| Detailed Framework/file design | `GENERAL_DEMO_FRAMEWORK_DESIGN.md` |
| Demo purpose | Discover architectural and contract problems before mainline construction |
| Migration intent | If the Demo passes its migration gates, migrate its structure and files into the main Framework rather than reimplementing them; then document and exercise a repeatable one-robot-at-a-time onboarding process |
| Covered robot A | SO-ARM101 follower with stock gripper — user approved |
| Covered robot B | Unitree Go2 quadruped — user approved |
| First Demo Consumer | One ReAct LLM Agent — user approved; non-LLM Consumers are future extension points |
| First architecture-Demo granularity | G2 only for both independent robot runs — user approved; not an RQ2 comparison |

This plan is non-normative. If it conflicts with `AUTOADAPTER_2_AUTHORITY.md`, the Authority wins.
An exact schema, prompt, API, budget, threshold, directory contract, or experimental allocation that
the Authority still marks `OPEN` remains open here. Authority revision `0.15.0` authorizes rapid
implementation of the complete two-robot G2 experimental path. The implementation must preserve
experimental correctness and information isolation, but it must not build enterprise deployment,
attestation, registry, activation/revocation, or distributed-governance machinery.

The Phase-2 implementation contract is intentionally small: one content-addressed
`integration_manifest.json` per robot; one frozen `run_snapshot.json` and one matching
`readiness_report.json` per run. Six Readiness checks plus cleanup must all pass before Stage 1.
Any later sections of this plan that mention admission/activation registries, trusted issuers, or
receipt chains are superseded by this direct experiment-grade rule.

## 1. Purpose and scope

The Demo is the first complete implementation rehearsal of AutoAdapter 2.0. Its purpose is not to
obtain final RQ results or prove robot performance. Its purpose is to expose architectural defects,
unclear contracts, information leakage, integration failures, robot-specific assumptions, and
non-migratable code while the covered population is still small.

The only intended **architectural scope reduction** from the future mainline is robot population:

- Morphology, SDK, Tasks, validation-standard, measurement, Translation, and Robot Integration
  records are populated initially for two robots only;
- all Framework stages, privacy boundaries, artifact seals, gates, execution routes, Repair,
  Consumer routing, Demo evaluation, and Evolution paths remain present;
- a missing Library entry may terminate a run at its declared gate, but may not justify deleting a
  Framework stage;
- robot-specific behavior must not be moved into generic Framework code merely to make the Demo
  pass.

The first campaign additionally executes G2 only. This is a staged experiment/run selection, not
an architectural reduction or an omitted Framework mechanism: the generic profile resolver remains part of the migratable architecture,
while G1 and G3 are deferred to later separate runs and the formal RQ2 comparison.

The two robots are intentionally different: a fixed-base serial manipulator with a stock gripper
and a free-base quadruped. This is an architectural stress test against building the Framework
around one arm topology, one command style, or one validation vocabulary.

The two-robot Demo is not the end of robot coverage. After it passes migration review, the project
must first write a robot-onboarding guide from the actual implementation evidence and failure
history, and then add other approved robots sequentially. A robot is never declared supported from
documentation similarity alone.

## 2. Non-negotiable interpretation of the formal path

For each admitted robot configuration, formal Validation B and Demo execution must use:

```text
generated capability layer
  -> pinned real robot SDK application layer
  -> admitted device/transport hook
  -> robot- and SDK-specific Translation Layer
  -> MuJoCo actuator control and physics stepping
  -> Translation Layer state mapping
  -> the same real SDK observation path
  -> generated capability layer
```

The term `shim` in informal planning is therefore split into two meanings:

- the formal component is the Authority-defined robot- and SDK-specific `Translation Layer` below
  the real SDK surface; it maps control, state, timing, reset, and transport semantics but adds no
  capability behavior, controller, IK, gait, planning, recovery, or task solution;
- an API-compatible mock or shim may exist only under test fixtures and can never satisfy the
  formal execution or readiness gates.

That restriction applies to the Translation Layer, not to the generated Capability Layer. The
purpose of `capability.py` is precisely to synthesize Design-authorized reusable behavior above the
admitted SDK surface. For a Go2 SDK Entry exposing low-level command/state messages, Stage 1 may
declare higher-level semantic capabilities such as stand, sit, body-pose control, or bounded move,
and Stage 2 may implement the required capability-scoped closed loop, IK, trajectory/gait logic,
state feedback, and composition by calling those SDK primitives. Such implementation must remain
task-independent, respect the sealed Design, and never bypass the SDK to control MuJoCo directly.

Therefore, the absence of simulator support for a convenient high-level method such as
`SportClient.Sit()` or `SportClient.Move()` is not by itself a coverage failure. AutoAdapter may be
expected to synthesize the corresponding capability from the admitted lower-level SDK surface. It
is a genuine blocking gap only when the admitted SDK observations/actions are insufficient to
implement the sealed semantic contract, or when the required implementation lies outside the
frozen capability/granularity scope.

Normal motion must be produced through MuJoCo actuators and stepping, not by overwriting simulated
state. Generated code cannot import or call MuJoCo, the Translation Layer, privileged state, or a
replacement robot API.

## 3. System boundary to be implemented

### 3.1 Four Libraries and robot selection

The Demo must implement the same four Library roles as the main Framework:

1. `Morphology Library`: physical structure, resources, canonical admitted MJCF, simulation and
   reset facts; no behavior recipes, IK, or task strategy.
2. `SDKs Library`: versioned dossiers and admitted projections for the actual upstream SDK; it is
   neither a wrapper library nor a copy of the SDK source tree.
3. `Tasks Library`: reviewed task descriptions, private criteria separation, and a fixed five-task
   Demo collection; it is not the Validation B suite.
4. `Experience Library`: governed, immutable, future-run Design and Implementation experience;
   current-run evidence cannot feed back directly.

Each active configuration is selected by a thin `Robot Integration Manifest` that references the
admitted Morphology, simulation profile, SDK Entry, and Translation Layer. The Manifest is not a
fifth Library and cannot contain hidden behavior.

`VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md` has a different role from the Tasks Library. It
is the human-authored, Framework-private reference collection that the Blue Line LLM may consult
when generating Validation B specifications. Its records illustrate complete or partial validation
approaches: what to measure, how to define physical success, which cases and false-pass guards to
include, and which threshold, temporal, repetition, and aggregation rules to use. It is not exposed
to Stage 1, Stage 2, Repair, or Consumers, and it is not itself a fifth Generation Library.

The Demo population is:

| Robot | Frozen Wave 3/4 integration boundary | Remaining later-wave work |
|---|---|---|
| SO-ARM101 follower | `so-arm101-follower-stock-gripper@1.0.0`; fixed base, five arm joints plus stock gripper; pinned SO MJCF; real LeRobot 0.6.0 + Feetech 1.0.0; `lerobot-so101-feetech-pty-mujoco@1.0.0` | State-Provided observation, exact G2, Tasks/Demo criteria, Generation/Validation/Consumer contracts |
| Unitree Go2 | `unitree-go2-stock-12dof@1.0.0`; free base, stock 12 DoF, joint/IMU state; pinned Unitree MJCF; real SDK2Py 1.0.1 + CycloneDDS 0.10.2; `unitree-sdk2-go2-dds-mujoco@1.0.0` | State-Provided observation, exact G2, Tasks/Demo criteria, Generation/Validation/Consumer contracts |

Approving a robot model does not approve a guessed SDK surface or a high-level behavior service.
Those facts must be established through the SDK/Morphology/RIM admission process.

### 3.2 Three protected paths

The implementation must preserve three distinguishable paths:

- `Run Input Path`: frozen Library snapshots and the selected RIM provide only the projections
  authorized for each recipient.
- `Simulation Execution Path`: Consumer calls a Framework-owned typed Capability Router, which
  invokes the isolated generated layer and the formal SDK-to-MuJoCo route.
- `Private Evaluation Path`: Validation B and Demo Harnesses may read privileged MuJoCo/evaluation
  data and own mandatory external-view Evaluation Video capture, but Blue Line, Stage 1, Stage 2,
  Sandbox models, the generated layer, Repair, and Consumers cannot read or control that path.

The private Harness is trusted evaluation infrastructure, not a Consumer and not a source of
candidate feedback beyond an authorized, redacted diagnostic projection. Evaluation Video is a
private audit attachment, not a robot sensor, public observation, evaluator input, or verdict
source.

The two-robot Demo is one campaign containing two independent runs. Each run resolves exactly one
fixed robot configuration and one RIM, owns its own snapshots, Design, suite, candidate, Validation,
task collection, and verdicts, and closes independently. Campaign reporting may compare/aggregate
only after both closures and must not merge the two execution sessions or their authorities.

### 3.3 Source discovery, review, and freezing

Missing MuJoCo models, scenes, mappings, integration facts, and related robot materials are researched in
the following order:

1. the current Authority, admitted current-project records, and already captured local sources;
2. the permitted AutoAdapter 1.0 source repository at
   `https://github.com/981526092/auto-adapter`, using the Authority-pinned audit commit
   `585eb1f1fde33f17f5f9a1e169a18dd41f97b586` rather than a moving branch;
3. official upstream robot, SDK, MuJoCo, and model/asset repositories or documentation on the web;
4. other public sources only as discovery leads when primary sources are unavailable.

Finding material does not admit it. Every reused or newly captured item must record its source,
version/commit, content hash, license and redistribution status, dependencies, claimed meaning,
robot/configuration scope, and any transformation performed. It then passes the applicable
Morphology, SDK, Translation, standards, or task review before becoming a frozen Demo input.

Legacy schemas, prompts, private thresholds, controller recipes, task solutions, names, defaults,
or previous success claims do not become AutoAdapter 2.0 contracts merely because they exist in
AutoAdapter 1.0. They may supply candidate assets or evidence only. If an asset is transformed, the
original and derived hashes and the deterministic transformation must both remain traceable.

Open-web research is a preparation activity. Formal comparison runs consume frozen local snapshots;
the formal Blue Line, Stage 1, Stage 2, Validation, Repair, and Consumer paths do not browse or
refresh these sources.

## 4. End-to-end lifecycle

The Demo implements the following lifecycle rather than a shortened happy-path script:

```text
freeze applicable contracts and run inputs
  -> write and freeze run_snapshot.json
  -> load the selected READY integration_manifest.json
  -> six-part Simulation Integration Readiness Check
  -> write matching readiness_report.json
  -> direct pre-Stage-1 gate checks six PASS results, hashes, G2, observation, run-input, and experiment contracts
  -> Generation Stage 1 semantic design
  -> Stage 1 schema + semantic conformance loop
  -> seal capability_design.json
  -> private Blue Line generation/check/compile
       -> NEEDS_REVIEW: terminate before Stage 2
       -> READY: seal spec + manifest + Validation B suite
  -> Generation Stage 2 implementation
  -> seal capability.py and Framework-derived Implementation Manifest
  -> Validation A
  -> candidate-specific Validation Execution Binding Overlay
  -> Validation B through the real SDK path
       -> record and seal mandatory private video for every started case/repetition
       -> pass: freeze validated layer
       -> candidate failure: implementation-only Repair, then the same A+B gates
       -> design/contract gap: terminate the run
  -> typed Capability Router
  -> ReAct Demo Consumer
  -> fixed five-task Demo with private authoritative Harness verdict
       -> record and seal mandatory private video for every started task trial/repetition
  -> close run
  -> Evolution proposal and governed future Experience route
```

The initial Stage 2 submission is `pass@0`. At most ten Validation-guided Repair invocations may
replace only `capability.py`; the suite, Design, standards, task set, robot inputs, and other frozen
artifacts remain unchanged. Stage 2 has at most 30 accounted LLM inference calls. Sandbox
executions are LLM-invoked tool use and are accounted separately under a still-open exact Sandbox
budget. Repair has its own budget and cannot consume unused Stage 2 resources.

## 5. Generation and assessment responsibilities

### 5.1 Stage 1 — semantic capability design

Stage 1 receives the selected target plus matching run snapshot/Readiness report, Morphology Design Projection, SDK Overview,
opaque task-description requirements, a single granularity profile, the State-Provided observation
condition, and any admitted Design Experience. It produces robot-specific but implementation-free
semantic capability contracts in `capability_design.json`.

Stage 1 owns capability meaning: public IDs, typed inputs/outputs, units/frames/ranges, temporal
semantics, preconditions, effects, invariants, failures, composition, requirement coverage, and
blocking gaps. It does not own Python names, exact SDK calls, controllers, or private validation
criteria. Once sealed for a run, the Design cannot be changed by Validation or Repair.

The first complete two-robot architecture Demo runs G2 only. SO-ARM101 and Go2 remain independent
single-RIM runs, but both select the same exact frozen robot-independent G2 profile and each exposes
only its own G2 layer to ReAct. G2 is intended to expose reusable synthesized capabilities above raw
SDK primitives—for example, building semantic stand/sit/move or arm end-effector operations from
lower-level commands and observations—and is not limited to forwarding an existing convenient SDK
method. The selected profile is `g2-reusable-effect@1.0.0`; exactly one profile is allowed per run. G1 and G3 are
not built or executed in this first Demo, although generic profile selection remains in the
Framework for later separate runs and the future RQ2 comparison.

### 5.2 Blue Line — private project-standard generation

The Blue Line is one fixed, isolated, implementation-blind LLM plus deterministic checking and
suite compilation. It receives the sealed Design, resolved robot/configuration and simulation
facts, a private Measurement Catalog, a frozen project Validation Standards Snapshot, and a frozen
Generation Policy. It does not receive Stage 2 code, candidate behavior, Sandbox results, Repair,
or Demo outcomes, and it does not execute the robot.

The initial human reference source for that Standards Snapshot is
`VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md`. The Blue Line LLM uses its reviewed
human-authored Validation B suite examples as in-context design references; it is not restricted to
looking up a single numeric threshold. A raw Markdown edit does not change a formal run: only the
exact reviewed records selected and frozen into that comparison's Snapshot are authoritative.

For every capability it must:

1. state what physical or interface-level result constitutes success;
2. select the trusted entity, signal, unit, frame, metric, and temporal interpretation;
3. identify plausible false-pass situations such as receipt-only success, self-reported success,
   wrong entity, wrong unit/frame, stale state, transient achievement, or missing conjuncts;
4. obtain threshold, dwell, timeout, repetition, and aggregation rules from the frozen human
   project standards, recording each rule as `COPIED`, `ADAPTED`, or `PROPOSED`;
5. transform the applicable human reference into a capability-specific structured private
   Validation Spec, including measurement, cases, guards, criteria, and aggregation.

The Reference Library is guidance, not the final executable artifact. `COPIED` records exact reuse;
`ADAPTED` records a traceable adjustment to an applicable human example; `PROPOSED` records a
missing-reference candidate that requires review. The deterministic compiler—not the Markdown
file—materializes the sealed `validation_b_suite.json` after the resulting spec is accepted.

One initial LLM call and at most two checker-driven correction calls are allowed. A deterministic
checker verifies schema, coverage, resolved references and applicability, units/frames, allowed
operators, bounded values, provenance labels, privacy, false-pass analysis, and that the spec adds
no public API or semantic obligation absent from the sealed Design. It does not invent or silently
rewrite semantics or numbers.

The Blue Line returns only:

- `READY`: seal `blue_line_validation_spec.json`, `validation_b_suite.json`, and
  `blue_line_manifest.json` before the first Stage 2 inference;
- `NEEDS_REVIEW`: stop before Stage 2 because a proposed/materially adapted standard, missing
  measurement/reference, or unresolved checker failure requires pre-experiment review.

Human review creates and freezes a new standards version; it never patches a candidate-conditioned
suite in place. The formal Blue Line is rerun from the frozen inputs.

### 5.3 Stage 2 — SDK-grounded implementation

Stage 2 starts in a fresh context after Blue Line `READY`. It receives the sealed Design, the
Framework-generated Python Binding Contract and skeleton, public robot implementation facts, the
SDK Implementation Projection, and admitted Implementation Experience. It does not receive task
descriptions or Blue Line internals.

Stage 1 and Stage 2 use the same selected producer backbone and the same model-independent Agent
Scaffold in separate stage-scoped contexts. They are not two cooperating Agents and no Stage 1
conversation history enters Stage 2. The fixed Blue Line LLM is a separate private assessment
component.

Its sole model-authored executable deliverable is `capability.py`. The Framework owns SDK object
construction, connection, injection, reset, close, dependency policy, the Implementation Manifest,
and isolation. Stage 2 can use only the injected SDK object and admitted public SDK surface.

Within that boundary, Stage 2 is allowed to implement the control logic needed by the sealed
capability contract, including capability-scoped IK, feedback control, trajectories, gait logic,
and composition. It may not turn a reusable capability into a hidden solution for a particular
Demo task, add public semantics absent from the Design, modify the SDK/Translation layer, or use
privileged simulator truth.

## 6. Validation, Consumer, Demo, and Evolution

### 6.1 Validation A and B

Validation A checks exact Design-to-code binding, file/source identity, public type/unit/frame/error
contracts, admitted dependencies, injected-SDK use, import and process isolation, and the
Framework-derived manifest. It does not grade physical success.
Because it launches no physical robot episode, Validation A has no mandatory Evaluation Video;
Sandbox or development recordings cannot substitute for formal Validation B evidence.

Validation B joins the immutable semantic suite with a candidate-specific non-mutating Binding
Overlay and executes through the selected RIM. The Harness observes private truth and owns the
verdict. SDK receipts, returned booleans, logs, and candidate claims are evidence only when the
sealed criterion explicitly calls for them; they never replace a required physical outcome.

For every started Validation B case and repetition, including every Repair revision, a Harness
sidecar records an external evaluation-camera view from post-reset/pre-invocation state through the
declared terminal observation, exception, or timeout. It must continue when the candidate fails or
times out. The sealed recording manifest binds the run, resolved RIM and simulation profile,
candidate revision, sealed suite and case, repetition, recording profile and camera configuration,
simulation-time interval and frame timestamps, media hash, and completion/integrity status. Media
bytes remain in the private content-addressed artifact store. Every infrastructure retry receives a
new append-only execution-attempt identity beneath the same logical candidate/case/repetition; it
never overwrites a failed attempt's manifest, partial media, traces, or evidence.

The recording does not enter the suite criterion, trusted measurement calculation, candidate
diagnostic, Repair context, or verdict. Missing, corrupt, truncated, or interval-incomplete video
is an infrastructure/evidence-capture failure: the execution contributes neither pass nor failure,
produces no candidate diagnostic, consumes no Repair invocation, and reruns the same immutable
revision under the frozen infrastructure policy. Only the valid rerun contributes at the same
Repair index; the invalid capture is excluded from all Validation denominators. Infrastructure
failures are reported separately and do not count as model/candidate failures.

### 6.2 Consumers and fixed Demo

Only a validated, frozen layer enters the Demo. The first Demo executes one ReAct LLM Agent as its
Consumer. The Agent receives task descriptions and declared public structured state, treats the
Framework-owned typed Capability Router operations as tools, and performs a bounded
reasoning/action/observation loop. It never sees `capability.py`, the implementation object, the SDK
object, or private criteria.

The Router and Consumer Adapter boundary must reserve a robot-independent extension point for
future policy, program, and planner Consumers, all of which will call the same typed Router
operations. Those alternative Consumers are not implemented or evaluated in the first Demo and
must not be represented by placeholder results. The ReAct-only Demo decision, exact Agent model,
prompt, tool presentation, memory/context policy, action/tool budget, termination, retry, and
reporting contract must be written to and frozen in the Authority before implementation.

The Demo uses a fixed collection of five tasks, executes all five, and has no visible/held-out
split. The Consumer sees descriptions and declared public structured state; the private Demo
Harness independently evaluates the authored criteria. Once results enter Evolution/Experience,
those tasks are a regression set rather than untouched evaluation.

The exact interpretation of a five-task collection in the two-robot Demo—one harmonized collection,
one robot-scoped collection per robot, or a declared mixed structure—remains under `OQ-TASK-001`,
`OQ-TASK-002`, `OQ-RQ1-001`, and `OQ-RQ3-001` and must be frozen before task execution.

For every started Demo task trial and repetition, the private Demo Harness records the same class
of external-view video from post-reset/pre-task state through terminal observation, exception, or
timeout. The manifest binds the task collection/task, trial and repetition, ReAct Consumer, frozen
layer, resolved RIM and simulation profile, recording profile and camera configuration,
simulation-time interval and frame timestamps, media hash, and completion status. Video remains
invisible to ReAct and the layer, contains no criterion/threshold/score/private-state overlay, and
cannot replace or override the authored criterion or structured Harness verdict. Capture or
integrity failure invalidates only that trial as infrastructure and causes a policy-governed rerun;
it is not a Consumer or task failure and does not enter Demo metric denominators. The rerun retains
the same immutable Consumer, layer, task, trial/repetition identity, reset/case, and protocol. Public
Each infrastructure retry has a distinct append-only execution-attempt identity, so the failed and
valid attempts remain independently auditable. Public release requires a separate
redaction/declassification decision after run closure. Raw video,
extracted frames, private camera metadata, and the unredacted
manifest cannot enter Experience or publication; only a separately governed, reconstruction-tested
redacted/declassified derivative may leave the private audit boundary.

### 6.3 Evolution

Evolution runs only after closure. It may form an `OBSERVED -> CANDIDATE` Experience proposal from
Validation, Repair, execution, and Demo evidence. It cannot change the completed run or auto-admit
the record. Admission, declassification, conflict handling, versioning, applicability, and future
retrieval remain governed. Private thresholds, cases, seeds, privileged state, and winning task
solutions cannot be copied into Experience.

## 7. Repository architecture

Authority 0.14.0 authorizes the listed implementation homes for its frozen integration subset;
their exact machine records still require successful bootstrap/admission. Directory branches owned
by later `OPEN` contracts remain proposed and unloadable. The intended separation is:

```text
general_demo/
  contracts/                   # frozen schemas/profiles/policies only; no run-specific views
  src/autoadapter2/            # generic Framework source packages
  libraries/
    morphology/                # Authority-defined robot/environment layout
    sdks/                      # Authority-defined SDK Entry layout
    tasks/                     # proposed machine records
    experience/                # ADMITTED records only
  private_governance/          # explicitly not a fifth Library
    blue_line/                 # human standards records, measurements and policy
    demo/                      # private criterion assets
    harness_adapters/          # private Validation/Demo robot-scoped evidence adapters
  generation_assets/
    development_probes/        # Generation-owned Sandbox-only public probes
  integrations/
    manifests/                 # thin RIM records
    translations/              # versioned SDK/device mappings
    readiness/                 # probe definitions; reports are run artifacts
    source_evidence/           # captured/reviewed integration source material
  tests/
    contract/
    isolation/
    fixtures/                  # mocks/shims allowed only here
    integration/
    cross_robot/
    end_to_end/
```

The complete proposed tree and per-file responsibilities are maintained in
`GENERAL_DEMO_FRAMEWORK_DESIGN.md`. Run-specific snapshots, Stage Bundles, candidates, suites,
traces, and verdicts live in a repository-external content-addressed artifact store, not in this
source tree.

Generic Framework packages must not branch on `soarm101`, `go2`, arm joint names, leg names, gripper
presence, fixed/free base, SDK method names, DDS/serial assumptions, or robot-specific measurements.
Those facts may occur only in governed robot-scoped records/plugins: Library entries, the selected
RIM, Translation package, runtime profile, readiness and Generation-owned development probes,
Blue Line standards/measurement applicability, Demo criterion assets, private Harness adapters,
and admitted source/provenance records. They may not appear as robot-name branches in generic
Framework source.

## 8. Construction waves after alignment

Construction Wave 0 was the completed design-only activity that produced and aligned this plan.
The remaining numbering below is canonical across this plan and
`GENERAL_DEMO_FRAMEWORK_DESIGN.md`.

### Wave 1 — normative freeze

- record and freeze the user-approved two-robot Demo population in the sole Authority; the plan
  document is not a substitute for that normative adoption;
- turn every exact contract required by the first implementation from `OPEN` into reviewed and
  frozen Authority content before implementing that component;
- freeze the two fixed robot configurations and their initial RIM boundaries;
- freeze the Stage Bundles, Design schema/checker, Binding/Manifest contracts, Blue Line inputs and
  artifacts, Validation A/B and Repair records, Router, Demo, and Evolution minimum schemas;
- freeze the Evaluation Video recording profile, manifest schema, capture interval and integrity
  rules, infrastructure retry, private storage/retention, and publication declassification boundary;
- freeze model identifiers, prompts/configuration, accounting rules, the selected G2 granularity profile,
  State-Provided observation contract, task collections, criteria, and standards snapshots needed
  for the first runs.

Exit: an implementation checklist can map every module to a frozen Authority requirement and no
module relies on an unreviewed legacy default.

### Wave 2 — foundation and contract tests

- create the independent General Demo tree;
- implement canonical serialization, content hashing, seals, artifact lineage, recipient
  projections, run-state/gate enforcement, and privacy checks;
- implement robot-independent schemas and negative contract/mutation tests before robot behavior.

Exit: the generic core can load two distinct manifest families without robot-name branches.

### Wave 3 — two-robot governed-data population

At Authority revision `0.14.0`, only the Morphology, SDK, runtime, RIM, exact-source/admission
tooling subset of this wave is authorized. Tasks, Experience, Validation Standards, Measurement
Catalog, and Demo criteria remain blocked by their owning open contracts.

- build and review Morphology, SDK, Tasks, Experience snapshots, Validation Standards Snapshot,
  Measurement Catalog, and RIM records for both robots;
- retain the reviewed 28-task SO-ARM101 catalog as source material and construct the Go2 catalog
  under the Authority's more-than-20-tasks-per-robot rule when appropriate; do not include tasks
  that the selected configuration cannot perform;
- treat an initially empty Experience snapshot as a valid frozen input only if the exact snapshot
  and retrieval route exist; do not omit the Library or the later admission path;
- capture exact source/version/hash/probe evidence and keep discovery facts separate from admitted
  contracts, including source identity, license, content hash, and asset dependency closure;
- author and privately bind the fixed Demo task criteria.

Exit: both RIMs resolve without implicit paths or mutable external defaults.

### Wave 4 — Translation, Session Runner, and Readiness

- implement one robot/SDK-specific bidirectional Translation Layer per robot;
- implement the generic minimal Session Runner for SDK lifecycle, reset, clock, limits, cleanup,
  isolation, and traces;
- run all six readiness checks for each exact RIM combination;
- add failure-injection tests for wrong pins, missing hooks, stale commands/state, reset leakage,
  timeout, process cleanup, and transport separation.

Exit: both robots demonstrate real SDK application code -> Translation -> MuJoCo -> SDK state
round trips without generated capability code.

### Wave 5 — Generation Stage 1 and Blue Line

- implement the common Agent Scaffold and recipient-specific Stage 1 Bundle;
- run Stage 1, deterministic conformance, bounded conformance repair, and sealing;
- implement the isolated Blue Line, three-call gate, deterministic checker/compiler,
  `READY/NEEDS_REVIEW`, and pre-Stage-2 seals;
- verify candidate-independent standards and suite generation for both robot families.

Exit: one sealed G2 Design per robot reaches `READY`, no G1/G3 artifact is generated in the first
campaign, and controlled missing-standard and invalid-reference fixtures correctly stop at
`NEEDS_REVIEW`.

### Wave 6 — Stage 2, Sandbox, Validation, and Repair

- implement Binding Contract/skeleton generation and the isolated Stage 2 context;
- implement SDK-grounded Sandbox access and redaction;
- derive the Implementation Manifest and run Validation A;
- create the non-mutating Binding Overlay and run Validation B through each real SDK path;
- record, hash, and seal private Evaluation Video for every started Validation B case/repetition,
  including revalidation after Repair, without changing simulation or verdict inputs;
- exercise pass@0, candidate failure, infrastructure failure, design-gap termination, and the
  same-suite implementation-only Repair loop, including budget exhaustion and first-pass promotion;

Exit: each robot has at least one validated/frozen layer, and controlled fixtures prove that
Repair cannot alter Design, standards, suite, task data, or Framework code.

### Wave 7 — Router, ReAct Consumer, fixed Demo, and Evolution

- implement the typed Router, its robot-independent Consumer Adapter contract, and the first ReAct
  Agent adapter; reserve but do not fabricate policy/program/planner implementations;
- execute all frozen Demo tasks through the validated layer and private Harness, recording and
  sealing private Evaluation Video for every started task trial/repetition;
- close the run, create an Experience Candidate, run declassification/review mechanics, and prove
  that no current-run feedback route exists.

Exit: both robots complete the full architectural route, including downstream consumption and the
future-only evidence path.

### Wave 8 — migration audit

- rerun all contract, isolation, integration, cross-robot, and end-to-end tests from clean
  environments;
- audit all generic modules for robot-specific assumptions;
- record open limitations and distinguish Framework, infrastructure, candidate, Design, standard,
  and task-coverage failures;
- write the first `ROBOT_ONBOARDING_GUIDE.md` from the actual two-robot implementation, including
  every required input, gate, artifact, test, failure class, and evidence obligation;
- approve, revise, or reject whole-structure migration.

## 9. Verification matrix

| Boundary | Required evidence before migration |
|---|---|
| Authority compliance | Every implemented component links to frozen Authority content; OPEN items are not silently implemented |
| Two-robot generality | Both RIMs run through the same generic orchestration; no core robot-name branches |
| Formal SDK route | Real SDK application code executes for both robots; mocks remain fixture-only |
| Translation purity | Mapping only; no hidden IK, gait, planning, recovery, or task solution |
| Readiness | All six checks pass independently for each pinned RIM combination |
| Stage separation | Fresh Stage 1/Stage 2 contexts; only sealed artifact crosses |
| First-granularity selection | Both independent robot runs pin the same exact frozen G2 profile, generate/expose only G2, and retain a robot-independent profile resolver for future separate G1/G3 runs |
| Blue Line independence | Suite sealed before Stage 2 and unaffected by candidate/outcomes |
| Isolation | Candidate cannot access MuJoCo, Translation, Harness, private standards, cases, or task criteria |
| Validation integrity | A precedes B; immutable suite plus candidate-specific overlay; Harness owns verdict |
| Evaluation-video integrity | Every started formal B case/repetition and Demo trial/repetition has private, content-addressed, simulation-time-linked recording evidence; infrastructure retries append new attempt records without overwriting failures, and video is never a model input or verdict source |
| Repair integrity | Initial plus at most ten implementation-only revisions; same suite; separate budget |
| Consumer integrity | ReAct uses only the typed Router; the future adapter contract is robot-independent and does not expose implementation objects |
| Demo integrity | Frozen five-task structure, complete execution, private authoritative criteria, no self-verdict |
| Evolution integrity | Closed-run, future-only, reviewed and declassified; no current-run feedback |
| Reproducibility | Pinned inputs, canonical hashes, parent lineage, clean reset/cleanup, deterministic declared conditions |
| Migration readiness | Repository boundaries and files can move without redesigning robot-specific assumptions into the core |

## 10. Failure scenarios the Demo must deliberately exercise

The architecture is not considered tested if it observes only one successful run. Fixture-based or
controlled negative tests must cover at least:

- unresolved RIM or failed readiness before Generation;
- malformed or semantically inconsistent Stage 1 Design;
- an attempted first-campaign G1/G3 run or multi-granularity layer, which must be rejected by the
  frozen campaign/run-profile gate;
- Blue Line missing/reference-inapplicable standard and `NEEDS_REVIEW`;
- Stage 2 forbidden dependency, SDK lifecycle violation, direct MuJoCo/Translation access, or wrong
  public binding;
- candidate physical false-pass attempt using receipt, self-report, stale state, wrong entity, or
  wrong unit/frame;
- Translation/Runner infrastructure failure distinguished from candidate failure;
- missing, corrupt, truncated, or interval-incomplete Validation video, proving infrastructure
  classification, no candidate diagnostic, no Repair consumption, and same-revision rerun;
- Repair attempt to mutate a frozen artifact or infer a private case;
- exhausted Repair budget without selecting a retrospective “best” revision, and separate reporting
  of `pass@0`, cumulative `pass@k`, Repair gain, and conditional Repair success;
- Consumer attempt to bypass the Router or inspect implementation state;
- Demo self-reported success that conflicts with private Harness truth;
- Demo recording failure and candidate/Consumer timeout, proving capture continues to the terminal
  boundary, incomplete evidence invalidates the trial, and video never overrides the criterion;
- Evolution attempt to admit current-run/private evidence directly;
- restart/reset contamination and concurrent-run cross-talk;
- a generic-core test that passes for one robot but fails because it assumed an arm, gripper,
  fixed base, synchronous command, or a particular transport.

## 11. Demo acceptance and migration gates

This plan proposes a stricter two-robot acceptance target than the Authority's minimum statement
that at least one run must reach `READY`: before whole-structure migration, **each of the two
approved robot configurations** should have at least one actual full run that reaches Blue Line
`READY` under the frozen G2 profile, continues through Stage 2, Validation, a frozen G2 layer,
Router-mediated Demo execution, run
closure, and the Evolution proposal route. This is a Demo-specific proposed acceptance rule and
must be confirmed/frozen before implementation.

Migration is approved only if:

1. both robot routes satisfy the verification matrix;
2. no formal route uses a mock SDK shim or direct simulated-state overwrite;
3. all required Framework stages exist even when a particular run terminates early;
4. the same generic code orchestrates both robots from governed records and plugin boundaries;
5. failure attribution and artifact lineage remain inspectable end to end;
6. the implementation and tests disclose unresolved limitations rather than adding hidden defaults;
7. moving the structure to the main Framework requires population growth and contract evolution,
   not a rewrite of its core architecture.

If these conditions fail, the Demo remains a diagnostic branch. Its individual mechanisms may be
reused only after the failure is understood; the structure is not migrated wholesale by default.

## 12. Remaining alignment before later construction waves

The Authority has frozen and authorized the two exact robot configurations, SDK/runtime pins,
integration-manifest/Translation routes, direct Readiness gate, and first-Demo G2 profile. Their
implementation may proceed. The following are the remaining
discussion/freeze checkpoints for later waves and must not be guessed:

1. confirm the proposed stricter requirement that both robots, not merely one, must complete a
   full run before migration;
2. decide and freeze the State-Provided structured observation contract;
3. define/freeze the Stage 1 Design schema/checker and Stage 2 Binding/Manifest contracts;
4. populate/freeze both task catalogs, the exact five-task Demo structure, and private criteria;
5. freeze the fixed Blue Line model/prompt/configuration, standards snapshot, Measurement Catalog,
   Generation Policy, exact artifacts/checker/compiler, and review flow;
6. freeze Validation A/B, Repair, Router, Demo Harness, and Evolution minimum machine contracts;
7. freeze the Evaluation Video recording/manifest/integrity/private-retention contracts and
    infrastructure rerun rule without turning video into an observation or verdict input;
8. freeze exact LLM/tool/Sandbox/Repair/Consumer/Demo budgets and provider-failure accounting;
9. freeze the first producer and ReAct Consumer model/prompt/tool/budget configuration without
    treating the architecture Demo as final RQ evidence;
10. approve the proposed repository boundary or replace it before later-wave files are scaffolded.

These are implementation details to settle while building; they do not block the authorized
experiment-grade end-to-end Demo and must not trigger new enterprise subsystems.

## 13. Post-Demo robot onboarding

After the two-robot Demo passes its migration gates, robot expansion follows a serial process. The
project selects and approves one next robot, records its fixed configuration and scope in the
Authority, completes all required evidence and gates, and closes its onboarding report before
starting the next robot. The ordered robot list remains a future user/Authority decision.

`ROBOT_ONBOARDING_GUIDE.md` must be derived from the working Demo rather than written as an
untested idealized guide. At minimum it will explain:

1. how to select and freeze one robot configuration;
2. source, version, license, hash, asset, and environment capture;
3. Morphology, SDK, Tasks, Experience, Validation Standards, and Measurement Catalog population;
4. SDK Entry admission and projection generation;
5. RIM creation and exact reference resolution;
6. device/transport hook and bidirectional Translation implementation without adding behavior;
7. the six Readiness checks and their evidence;
8. task applicability, fixed-Demo-task, criterion, and Blue Line standard preparation;
9. Stage 1, Blue Line, Stage 2, Validation, Repair, Router, Demo, and Evolution execution;
10. evaluation-only camera/profile applicability, mandatory private video evidence, isolation,
    failure attribution, reproducibility, regression, and migration checks;
11. which files are robot-scoped and which generic Framework files must remain unchanged; and
12. the evidence required before the robot can be called covered rather than merely proposed.

The guide is considered validated only after it is used to onboard the first additional robot. If
that onboarding requires an unplanned generic-core modification, the change is treated as an
architectural finding: update and re-freeze the applicable Authority contract, add a cross-robot
regression test, rerun the two original robot routes, revise the guide, and only then continue to
the next robot. No batch registration, documentation-only support claim, or readiness waiver is
allowed.
