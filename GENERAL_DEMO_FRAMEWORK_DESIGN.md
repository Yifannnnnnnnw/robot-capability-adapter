# AutoAdapter 2.0 General Demo — Framework and File Design

## 0. Document control

| Field | Value |
|---|---|
| Status | `ACTIVE_NON_NORMATIVE_DESIGN` |
| Implementation authorization | `EXPERIMENT_GRADE_FULL_DEMO_AUTHORIZED` |
| Sole normative authority | `AUTOADAPTER_2_AUTHORITY.md` revision `0.15.0` |
| Companion plan | `GENERAL_DEMO_PLAN.md` |
| Initial robot scope | SO-ARM101 follower + stock gripper; Unitree Go2 |
| Initial Demo Consumer | ReAct LLM Agent |
| Purpose | Specify the intended repository, package, artifact, security, and test structure before any implementation files are scaffolded |

This is a non-normative implementation design. Authority-defined names and layouts are preserved.
Every additional filename, module split, schema name, and configuration path remains subordinate
to the applicable frozen Authority contract. If this document and the Authority conflict, the
Authority wins. Authority revision `0.15.0` authorizes the complete experiment-grade two-robot G2
Demo path. The implementation should be small enough to understand and change quickly.

The integration core consists of one `integration_manifest.json` per robot, one
`run_snapshot.json` per run, and one matching `readiness_report.json`. It does not include schema
bootstrap admission, registry configs, trusted issuers, activation/revocation logs, signatures, or
receipt/event chains. Any later tree or catalog entry in this document describing those withdrawn
0.14.0 mechanisms is superseded and must not be implemented.

The following user decisions are reflected here. Authority revision `0.15.0` freezes the two exact
robot configurations, SDK/runtime pins, integration manifests, Translation routes, six-check
Readiness rules, and complete experiment-grade G2 path for implementation:

- the first complete Demo covers exactly SO-ARM101 follower with stock gripper and Unitree Go2;
- both robots must each complete at least one full `READY` downstream route before wholesale
  migration;
- the first Demo Consumer is one ReAct Agent, while other Consumer types receive an extension
  interface only;
- the first complete architecture Demo executes G2 only for both independent single-RIM robot runs;
  G2 is intended to synthesize reusable semantic capabilities above lower-level SDK primitives,
  including capability-scoped IK, feedback, trajectories, posture/gait logic, and composition;
- `VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md` is the initial human-authored source for
  Project Validation Standards Reference records; formal Blue Line runs consume only reviewed
  records in a frozen snapshot, never the live Markdown;
- missing MuJoCo/integration material is researched first in the pinned AutoAdapter 1.0 repository
  and then in primary web sources, subject to new admission; and
- after the Demo, the project writes and validates a robot-onboarding guide, then adds robots one
  at a time.

### 0.1 Path/status legend

| Marker | Meaning |
|---|---|
| `AUTHORITY-NAMED BASENAME` | The Authority fixes the basename but not this document's containing path or schema fields |
| `AUTHORITY-NAMED RELATIVE LAYOUT` | The Authority fixes a relative subtree; `general_demo/` remains the proposed containing root |
| `PROPOSED` | This design proposes the filename or split; it is blocked only while its owning Authority contract remains `OPEN`. Authority-0.14.0 integration homes may now be implemented; the marker continues to block unrelated open components |
| `SOURCE` | Version-controlled human/framework-authored source or admitted data |
| `DERIVED` | Reproducibly generated index/projection that does not become a second authority |
| `RUNTIME` | Per-run or per-revision artifact stored outside the source tree |
| `PRIVATE` | Framework/Harness-visible only; never materialized in model, candidate, Repair, or Consumer workspaces unless explicitly allowed |

## 1. Design rules

1. `general_demo/` is the proposed name of the independent implementation root required by
   `REQ-GD-001`; the requirement does not itself freeze this directory name.
2. The generic Framework never branches on `soarm101`, `go2`, arm/leg names, gripper presence,
   fixed/free base, a particular SDK method, or a transport technology.
3. Generic `src/autoadapter2` modules never branch on robot identity. Robot-scoped facts/plugins
   may occur only in governed Library entries, one thin RIM, an SDK-specific Translation package,
   runtime profile, readiness probe, Generation-owned development probe, robot-scoped Blue Line
   standards/measurement applicability, Demo criterion asset, private Harness adapter, or admitted
   source/provenance record.
4. The real upstream SDK application layer executes in every formal Sandbox, Validation B, and
   Demo run. API-compatible facades remain test fixtures.
5. Translation maps SDK transport/device commands and state to MuJoCo and back. It never supplies
   capability behavior. Generated `capability.py` is where Design-authorized reusable control,
   IK, trajectory, gait, and composition logic may live.
6. Git paths are not security boundaries. Each recipient receives a newly materialized workspace
   containing only allowlisted files. Private data is withheld by mount/copy policy, process
   isolation, and import/network controls.
7. Immutable content is addressed by SHA-256 and linked through parent hashes. Mutable discovery
   indices never own substantive metadata.
8. Source files and run artifacts are separate. Model responses, candidates, suites, traces, and
   verdicts are not committed into the implementation source tree.
9. A schema, prompt, budget, public type, IPC contract, or exact state machine that remains `OPEN`
   in the Authority is represented as a proposed design item, not an implemented default.
10. Full task records and human validation references may be readable to authorized maintainers,
    but Stage 1, Stage 2, Repair, candidate workers, and ReAct receive only their declared views.
11. Every formal Validation B case/repetition and Demo task trial/repetition records mandatory
    Framework-private Evaluation Video. Video is mandatory non-verdict audit evidence, never an SDK/robot
    observation, State-Provided value, model/Consumer input, or verdict source.
12. The first two-robot architecture Demo pins one frozen G2 profile for both independent runs and
    generates no G1 or G3 layer. Profile resolution remains generic so later G1/G2/G3 conditions are
    still separate runs rather than branches inside one layer.

## 2. Logical architecture and dependency direction

```text
Authority + frozen contracts
             |
             v
Four Libraries + one selected integration manifest
             |
             v
freeze run snapshot -> six-check Integration Readiness -> recipient-specific Run Input
                                               |
                                               v
Stage 1 -> Design conformance -> sealed capability_design.json
                                               |
                frozen private Standards/Measurement/Policy
                                               |
                                               v
                  Blue Line LLM + checker/compiler -> sealed private suite
                                               |
                                               v
Stage 2 -> candidate -> Validation A -> Binding Overlay -> same sealed Validation B
                         ^                                  |
                         |                                  +-- candidate-attributable A or B failure
                         |                                                          |
                         +--------------------------- new child candidate <-- Repair
                                                            |
                                                     complete A+B pass
                                                            v
                                                validated layer -> explicit freeze

task description + public structured state -> ReAct -> typed Router -> frozen layer
                                                               |
                                      private Harness observes execution independently
                                                               |
                                                               v
                                                authoritative Demo verdict
                                                               |
                                                               v
                                 run closure -> external Evolution case -> future Experience
```

Formal execution under either robot uses a separate path owned by the Session Runner:

```text
capability.py
  -> injected real SDK object
  -> real SDK application code
  -> admitted device/transport hook
  -> robot/SDK Translation
  -> MuJoCo actuators + physics stepping
  -> translated SDK-compatible observation
  -> capability.py
```

The intended Python package dependency direction is acyclic:

```text
foundation
  <- contracts + artifact_store + model_runtime
  <- library_access + integration + runtime + public_state
  <- generation + blue_line + validation + repair + consumer + demo + evolution + reporting
  <- orchestration
  <- cli
```

`generation` cannot import `validation`, `demo`, or private Harness modules. `consumer` cannot
import `generation`, SDK integration, candidate source, or Harness modules. Robot integration
packages can depend on the Translation/Runner contracts but not on Stage 1, Stage 2, Blue Line,
Repair, or task semantics.

## 3. Repository tree — frozen integration homes and proposed later components

```text
auto_adapter2.0/
├── AUTOADAPTER_2_AUTHORITY.md
├── TASKS_LIBRARY.md
├── VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md
├── GENERAL_DEMO_PLAN.md
├── GENERAL_DEMO_FRAMEWORK_DESIGN.md
├── research_assets/
│   └── autoadapter_high_level_framework_2026-08-08.png
└── general_demo/                                      # implementation root; 0.14.0 subset authorized
    ├── README.md
    ├── pyproject.toml
    ├── dependency-lock.json
    ├── THIRD_PARTY_NOTICES.md
    ├── .gitignore
    ├── docs/
    │   ├── ARCHITECTURE.md
    │   ├── SECURITY_AND_VISIBILITY.md
    │   ├── ARTIFACT_LIFECYCLE.md
    │   ├── OPERATIONS_RUNBOOK.md
    │   ├── IMPLEMENTATION_TRACEABILITY.md
    │   └── ROBOT_ONBOARDING_GUIDE.md                  # created only after Demo audit
    ├── design_proposals/
    │   └── <OQ-ID>/                                   # never loadable by production code
    ├── contracts/
    │   ├── schemas/                               # small project schemas
    │   │   ├── common/
    │   │   ├── evidence/
    │   │   ├── orchestration/
    │   │   ├── model_runtime/
    │   │   ├── libraries/
    │   │   ├── integration/
    │   │   ├── public_state/
    │   │   ├── generation/
    │   │   ├── blue_line/
    │   │   ├── validation/
    │   │   ├── consumer/
    │   │   ├── demo/
    │   │   └── evolution/
    │   ├── profiles/<profile_family>/<profile_id>/<version>/
    │   │   ├── profile.yaml
    │   │   └── manifest.yaml
    │   └── policies/<policy_id>/<version>/
    │       ├── policy.yaml
    │       └── manifest.yaml
    ├── config/
    │   ├── models/<role>/<config_version>.yaml
    │   ├── budgets/<budget_role>/<budget_version>.yaml
    │   ├── demo/<campaign_id>/<campaign_version>.yaml
    │   ├── runs/<run_profile_id>/<run_profile_version>.yaml
    │   ├── experiments/<experiment_id>/<experiment_version>.yaml
    │   └── logging/<log_profile_id>/<log_profile_version>.yaml
    ├── prompts/
    │   ├── stage1/<prompt_version>/
    │   ├── blue_line/<prompt_version>/
    │   ├── stage2/<prompt_version>/
    │   ├── repair/<prompt_version>/
    │   └── consumer_react/<prompt_version>/
    ├── generation_assets/
    │   └── development_probes/                       # Generation-owned public probes
    ├── libraries/
    │   ├── morphology/                               # Authority-approved layout
    │   ├── sdks/                                     # Authority-approved layout
    │   ├── tasks/                                    # proposed machine-readable layout
    │   └── experience/                               # proposed machine-readable layout
    ├── private_governance/
    │   ├── blue_line/
    │   │   ├── source_registry.yaml
    │   │   ├── standards/
    │   │   ├── measurement_catalog/
    │   │   └── generation_policy/
    │   ├── demo/
    │   │   └── criterion_assets/
    │   └── harness_adapters/
    │       ├── validation/
    │       └── demo/
    ├── integrations/
    │   ├── manifests/
    │   ├── translations/
    │   ├── readiness/
    │   └── source_evidence/
    ├── environments/
    │   ├── framework/Containerfile
    │   ├── integrations/<runtime_profile_id>/Containerfile
    │   └── runtime_profiles/<runtime_profile_id>/<version>/
    │       ├── record.json
    │       └── profile.yaml
    ├── ci/
    │   ├── test_matrix.yaml
    │   ├── formal_integration_requirements.yaml
    │   └── core_diff_policy.yaml
    ├── src/
    │   └── autoadapter2/
    │       ├── foundation/
    │       ├── artifact_store/
    │       ├── contracts/
    │       ├── model_runtime/
    │       ├── orchestration/
    │       ├── library_access/
    │       ├── integration/
    │       ├── runtime/
    │       ├── public_state/
    │       ├── generation/
    │       ├── blue_line/
    │       ├── validation/
    │       ├── repair/
    │       ├── consumer/
    │       ├── demo/
    │       ├── evolution/
    │       ├── reporting/
    │       └── cli/
    ├── tools/
    │   ├── verify_repository.py
    │   ├── capture_source.py
    │   ├── rebuild_catalogs.py
    │   ├── validate_contracts.py
    │   └── audit_run.py
    └── tests/
        ├── unit/
        ├── contracts/
        ├── security/
        ├── libraries/
        ├── integration/
        ├── generation/
        ├── blue_line/
        ├── validation/
        ├── repair/
        ├── consumer/
        ├── demo/
        ├── evolution/
        ├── cross_robot/
        ├── end_to_end/
        └── fixtures/
```

The runtime artifact store and stage workspaces are deliberately absent from this Git tree. Their
layout is defined in Section 9.

### 3.1 Proposed format convention

- YAML: human-reviewed governed source records, profiles, policies, and configuration;
- JSON: machine-produced runtime artifacts, reports, manifests, snapshots, and seals;
- JSONL: time-ordered execution traces only;
- Markdown: human documentation and the existing human reference/catalog views;
- Python: generic Framework and Python Translation/adapter implementations;
- XML/MJCF plus binary assets: admitted simulation payloads;
- native source/build files: only inside a versioned Translation/runtime profile when an SDK seam
  requires them.

Experiment artifacts use stable UTF-8 JSON and SHA-256; non-JSON payloads use exact stored bytes.
No schema-package bootstrap or attestation layer is required.

## 4. Version-controlled file catalog

### 4.1 Root and build files

| Planned path | Marker | Responsibility |
|---|---|---|
| `general_demo/README.md` | `PROPOSED SOURCE` | Supported scope, non-claims, prerequisites, authoritative links, minimal commands, and explicit distinction between formal routes and fixtures |
| `general_demo/pyproject.toml` | `PROPOSED SOURCE` | Python package metadata, command entry point, base dependencies, test/lint configuration; exact Python/dependency profile must be frozen first |
| `general_demo/dependency-lock.json` | `PROPOSED SOURCE` | Platform-aware exact hashes for Framework dependencies; robot SDK artifacts remain owned by SDK Entry pins rather than this file |
| `general_demo/THIRD_PARTY_NOTICES.md` | `PROPOSED DERIVED` | Auditable summary of admitted third-party assets/code and licences, generated from provenance records |
| `general_demo/.gitignore` | `PROPOSED SOURCE` | Excludes runtime stores, workspaces, SDK caches, model caches, credentials, local environments, compiled native files, and traces |
| `docs/ARCHITECTURE.md` | `PROPOSED SOURCE` | Realized module diagram and dependency rules; cannot override Authority |
| `docs/SECURITY_AND_VISIBILITY.md` | `PROPOSED SOURCE` | Recipient matrix, process boundaries, mounts, imports, network, secrets, and private-data threat model |
| `docs/ARTIFACT_LIFECYCLE.md` | `PROPOSED SOURCE` | Content addressing, seals, parent lineage, mutation rules, retention, promotion, and run closure |
| `docs/OPERATIONS_RUNBOOK.md` | `PROPOSED SOURCE` | Environment setup, source capture, readiness, run execution, failure recovery, cleanup, and report generation |
| `docs/IMPLEMENTATION_TRACEABILITY.md` | `PROPOSED DERIVED` | Mapping from implemented modules/tests to frozen Authority IDs; generated from annotations where possible |
| `docs/ROBOT_ONBOARDING_GUIDE.md` | `POST-DEMO SOURCE` | Evidence-backed instructions written after the two-robot audit and validated by the first additional robot |
| `ci/test_matrix.yaml` | `PROPOSED SOURCE` | Unit/contract/security/integration/end-to-end jobs and supported runtime profiles |
| `ci/formal_integration_requirements.yaml` | `PROPOSED SOURCE` | Conditions that distinguish real-SDK formal jobs from fixture-only jobs |
| `ci/core_diff_policy.yaml` | `PROPOSED SOURCE` | Post-Demo gate requiring an architecture finding when robot onboarding changes generic core |

No credential file is planned. Device addresses, tokens, provider keys, and local paths enter only
through an external secret/config provider and are redacted from artifacts.

### 4.2 Contract profiles and policies

These files are proposed concrete homes for items that remain open in the Authority. They are not
created until their exact content is approved and frozen.

| Planned path | Responsibility | Owning open contract |
|---|---|---|
| `contracts/profiles/granularity/<profile_id>/<version>/profile.json` | Generic, robot-independent, exactly-one-profile-per-run envelope; the first campaign loads only frozen `g2-reusable-effect@1.0.0`, while later campaigns may add separately frozen G1/G3 profiles through the same resolver | first-Demo G2: `DEC-GRAN-001`; later G1/G3 and RQ2 protocol: `OQ-EXP-001` |
| `contracts/profiles/observation/state_provided/<version>/{profile,manifest}.yaml` | Public entity/state schema, frame/unit rules, timestamps, precision, update cadence, noise/latency declaration | `OQ-OBS-001` |
| `contracts/profiles/python_binding/one_file/<version>/{profile,manifest}.yaml` | Permitted Python binding/result envelope and one-file candidate profile | `OQ-GEN-001` |
| `contracts/policies/canonicalization/<version>/{policy,manifest}.yaml` | Generic Generation/Blue Line/full-run normalization and hash rules; excludes the Authority-frozen integration-v1 JCS/raw-byte rule | `OQ-GEN-001`, `OQ-BLUE-001` |
| `contracts/policies/dependency/<version>/{policy,manifest}.yaml` | Stage 2 and candidate dependency allowlist/denylist and environment pin rules | `OQ-GEN-001`, `OQ-VAL-001` |
| `contracts/policies/recipient_visibility/<version>/{policy,manifest}.yaml` | File/field visibility for Stage 1, Blue Line, Stage 2, Repair, candidate, ReAct, Harness, and maintainers | multiple OQs |
| `contracts/policies/candidate_isolation/<version>/{policy,manifest}.yaml` | Process, filesystem, import, network, hardware, resource, and IPC restrictions | `OQ-GEN-001`, `OQ-CONSUMER-001` |
| `contracts/policies/artifact_retention/<version>/{policy,manifest}.yaml` | Private retention for prompts, responses, traces, failures, reports, raw/partial video, extracted frames, private camera metadata, and unredacted recording manifests; Validation/Demo own capture-era retention while Evolution owns post-closure redaction/declassification and release | `OQ-VAL-001`, `OQ-DEMO-001`, `OQ-EVO-001` |
| `contracts/profiles/recording/formal_trial/<version>/{profile,manifest}.yaml` | Mandatory evaluation-only external-camera view/framing, capture interval, encoding, simulator-time synchronization, completeness and resource limits; artifact retention/redaction/declassification remain in the separate retention policy | `OQ-VAL-001`, `OQ-DEMO-001` |

Every released profile/policy version is immutable: its manifest binds ID, version, payload hash,
review/freeze record, and Authority/OQ ownership. A run pins the manifest and payload hashes.

### 4.3 Proposed homes for eventually frozen run configuration

Every path in this subsection is `PROPOSED` and is owned by the corresponding open Authority
contract. A file becomes loadable only after its exact schema/content is frozen. All loadable
configurations are version-namespaced and named by exact reference; production code rejects
unversioned files and anything under `design_proposals/`. Configuration files select already
admitted/frozen contracts; they must not redefine them.

| Planned path | Responsibility |
|---|---|
| `config/models/<role>/<config_version>.yaml` | Exact producer, isolated Blue Line, or ReAct provider endpoint, model identity, decoding, retry/cache, and role-specific settings |
| `config/budgets/<budget_role>/<budget_version>.yaml` | Frozen Stage 1, Blue Line, Stage 2, Sandbox, Repair, ReAct, or Demo accounting and termination policy |
| `config/runs/<run_profile_id>/<run_profile_version>.yaml` | Exactly one RIM and one fixed robot configuration plus observation/granularity profiles, snapshots, prompt/config refs, task-collection ref, and report profile |
| `config/demo/<campaign_id>/<campaign_version>.yaml` | Campaign-level ordered refs to the SO-ARM101 run profile and Go2 run profile; never embeds two RIMs in one run |
| `config/experiments/<experiment_id>/<experiment_version>.yaml` | Future RQ cell matrix and frozen controlled-artifact refs; not used to claim final RQ results in the architecture Demo |
| `config/logging/<log_profile_id>/<log_profile_version>.yaml` | Structured log levels, event fields, redaction classes, and sinks |

The two-robot Demo is therefore one campaign containing two independent, single-RIM runs. It may
share frozen Framework contracts and comparison inputs, but a run never merges robot sessions,
RIMs, Library projections, candidates, suites, task collections, or verdicts.

The intended initial role/instance set (version token still to be frozen) is:

```text
config/models/{producer,blue_line,consumer_react}/<version>.yaml
config/budgets/{stage1,blue_line,stage2,sandbox,repair,consumer_react,demo}/<version>.yaml
config/runs/{soarm101_follower_stock_gripper,unitree_go2}/<version>.yaml
config/demo/two_robot_general_demo/<version>.yaml
config/experiments/{rq1,rq2,rq3}/<version>.yaml
config/logging/structured_default/<version>.yaml
```

### 4.4 Proposed homes for eventually frozen prompt files

Prompts are versioned Framework inputs, not documentation and not hidden structural authorities.
The paths below are relative to `prompts/<role>/<prompt_version>/`; the version directory and exact
bytes must be frozen before a loader can resolve them. Their hashes are recorded in every applicable
manifest. Drafts live only under `design_proposals/<OQ-ID>/` and are rejected by production loaders.

| Planned path | Recipient/use |
|---|---|
| `prompts/stage1/<prompt_version>/system.md` | Stage 1 responsibility, visibility, semantic contract rules |
| `prompts/stage1/<prompt_version>/request.md.j2` | Deterministic rendering of the Stage 1 Design Bundle |
| `prompts/stage1/<prompt_version>/conformance_correction.md.j2` | Public structured Stage 1 conformance errors only |
| `prompts/blue_line/<prompt_version>/system.md` | Private reference-guided validation-spec role and prohibitions |
| `prompts/blue_line/<prompt_version>/request.md.j2` | Sealed Design + frozen measurement/standards/policy projection |
| `prompts/blue_line/<prompt_version>/checker_correction.md.j2` | Deterministic checker errors for at most two corrections |
| `prompts/stage2/<prompt_version>/system.md` | Implementation-only responsibility, SDK and isolation rules |
| `prompts/stage2/<prompt_version>/request.md.j2` | Stage 2 Implementation Bundle and generated binding/skeleton |
| `prompts/stage2/<prompt_version>/sandbox_feedback.md.j2` | Redacted public development-probe feedback |
| `prompts/repair/<prompt_version>/continued_context.md.j2` | Authorized continued-context Repair update |
| `prompts/repair/<prompt_version>/fresh_snapshot.md.j2` | Fresh-snapshot Repair input and redacted diagnostic |
| `prompts/consumer_react/<prompt_version>/system.md` | ReAct role, Router-only tool use, no self-verdict, stop rules |
| `prompts/consumer_react/<prompt_version>/task.md.j2` | Task description and public structured state only |

There is no prompt that exposes the full Tasks Library, private criteria, Blue Line suite, raw
MuJoCo truth, Translation internals, or an expected solution to Stage 1, Stage 2, Repair, or ReAct.

## 5. Generic Framework source-file catalog

All paths below are under `general_demo/src/autoadapter2/`. They are implementation homes rather
than independent authority: files that implement the Authority-0.14.0 frozen integration subset
are authorized now, while files owned by an `OPEN` contract remain `PROPOSED SOURCE`. Exact
signatures outside the frozen subset remain open. The split is intentionally fine-grained so
robot-specific behavior cannot hide in an omnibus runner.

### 5.1 Package entry and foundation

| File | Single responsibility |
|---|---|
| `__init__.py` | Package version and no side-effect imports |
| `__main__.py` | Invoke the CLI entry point only |
| `foundation/errors.py` | Framework error classes separated into contract, infrastructure, candidate, Design, standard, task, and provider failures |
| `foundation/identifiers.py` | Parse and validate immutable IDs/versions/references without resolving them |
| `foundation/canonical.py` | Canonical serialization according to the frozen policy |
| `foundation/hashing.py` | SHA-256 byte/file/tree hashing with explicit algorithms |
| `foundation/seals.py` | Create and verify typed seals and parent references |
| `foundation/artifacts.py` | Artifact descriptor, media type, owner, visibility class, and lineage edges |
| `foundation/provenance.py` | Source locator, license, transformation, evidence, and applicability records |
| `foundation/results.py` | Typed success/error result envelopes; no robot semantics |
| `foundation/redaction.py` | Field-level redaction primitives driven only by frozen policies |
| `foundation/clock.py` | Monotonic wall/process/simulation clock value types; does not step simulation |
| `foundation/jsonl.py` | Append-only structured event encoding and verification |

### 5.2 Contract loading, validation, and projection

| File | Single responsibility |
|---|---|
| `contracts/schema_registry.py` | Resolve a pinned schema ID/version to exact bytes/hash |
| `contracts/schema_validator.py` | Validate an artifact against its declared schema without mutating it |
| `contracts/profile_registry.py` | Resolve frozen granularity, observation, and language-binding profiles |
| `contracts/policy_registry.py` | Resolve canonicalization, dependency, visibility, isolation, and retention policies |
| `contracts/reference_resolver.py` | Resolve typed content-addressed refs and reject ambiguity/floating versions |
| `contracts/projection_engine.py` | Apply versioned allowlist projection rules to canonical records |
| `contracts/visibility_guard.py` | Check recipient field/file allowlists and forbidden namespaces |
| `contracts/compatibility.py` | Check declared version/scope compatibility; never treats a declaration as readiness evidence |
| `contracts/traceability.py` | Link artifacts/checks to Authority requirement IDs and frozen contract versions |

### 5.3 Artifact store and run orchestration

| File | Single responsibility |
|---|---|
| `artifact_store/protocol.py` | Robot- and backend-independent immutable object/ref/run-index interface |
| `artifact_store/local_store.py` | Initial repository-external local content-addressed backend implementing the protocol |
| `artifact_store/run_index.py` | Maintain small typed run refs without mutating content-addressed objects |
| `artifact_store/permissions.py` | Enforce owner/recipient visibility classes at put/get/materialize boundaries |
| `artifact_store/atomic.py` | Atomic object/ref/event writes, fsync/rename discipline, and collision/tamper checks |
| `orchestration/campaign.py` | Resolve one campaign into ordered independent single-RIM run requests and aggregate only after each run closes |
| `orchestration/run_coordinator.py` | Coordinate exactly one run across readiness, Stage 1, Blue Line, Stage 2, Validation, Repair, freeze, Demo, and closure |
| `orchestration/run_input_assembler.py` | Resolve exactly one RIM and create recipient-specific inputs/views from frozen source records and configuration |
| `orchestration/state_machine.py` | Pure governed run-state transitions; no stage implementation or robot behavior |
| `orchestration/gate_controller.py` | Require seals/verdicts/statuses before releasing the next stage and reject bypasses |
| `orchestration/run_closure.py` | Seal the immutable run boundary and emit the closure hash used by external reports/Evolution cases |

The orchestrator owns ordering, not semantics. It cannot repair a Design, invent a standard, select a
capability implementation, interpret a task verdict, or invoke a robot outside the Session Runner.

### 5.4 Library access and snapshots

| File | Single responsibility |
|---|---|
| `library_access/catalog.py` | Read generated discovery indices and verify every entry against its owning manifest |
| `library_access/morphology.py` | Load admitted Morphology robot/environment entries and their hashes |
| `library_access/sdk.py` | Load admitted SDK dossiers, artifact pins, API facts, sources, examples, and admission evidence |
| `library_access/tasks.py` | Load full approved task records; never expose full records directly to a model/Consumer |
| `library_access/task_views.py` | Produce description-only Stage 1/ReAct views and private Harness criterion views |
| `library_access/experience.py` | Resolve admitted Design/Implementation Experience by exact version and applicability |
| `library_access/experience_views.py` | Produce distinct Stage 1 Design, Stage 2 Implementation, and future-recipient projections from one selected version set |
| `library_access/snapshots.py` | Build and verify immutable run-specific Library/Experience snapshot manifests |
| `library_access/admission.py` | Read-only resolution of effective admission state from the trusted append-only registry; never trusts subject status fields |
| `library_access/catalog_builder.py` | Rebuild derived catalogs from manifests deterministically |

### 5.5 RIM, Translation contracts, and readiness

| File | Single responsibility |
|---|---|
| `integration/schema_bootstrap.py` | Verify the Authority-frozen JCS schema package and bind its exact hash through the schema bootstrap trust anchor |
| `integration/registry_config.py` | Load the five exact Authority-bound registry/trust-anchor configs; candidate callers cannot replace them |
| `integration/event_log.py` | Append/verify JCS event chains with global and per-subject heads, sequence/fork/cycle/replay rejection |
| `integration/admission_authority.py` | Sole Framework-private writer for admission, RIM activation, Readiness issuance and integration-gate logs |
| `integration/state_reducer.py` | Compute effective admitted/revoked and active/inactive state from trusted chains |
| `integration/rim.py` | Parse and validate a thin Robot Integration Manifest |
| `integration/rim_resolver.py` | Resolve the exact fixed robot configuration, Morphology entry, simulation profile, SDK Entry and Translation plus externally bound compatibility evidence; task environment is separate |
| `integration/registry.py` | Discover installed integration packages by manifest; distinct from the trusted operational event logs |
| `integration/translation_protocol.py` | Internal typed protocol a robot-specific Translation package must implement; never exposed to candidate/Consumer |
| `integration/readiness_protocol.py` | Six required readiness probe contracts and evidence categories |
| `integration/readiness_runner.py` | Execute the six checks via Session Runner, classify infrastructure failure and produce only the private immutable report |
| `integration/readiness_issuer.py` | Trusted-authority append of a PASS receipt event after independently verifying the private report; ordinary callers cannot mint it |
| `integration/formal_gate.py` | Issue trusted-log `RIM_RESOLVED` or `INTEGRATION_READY` events after recomputing exact refs and registry chains; never issues final `READY_FOR_STAGE1` |
| `integration/environment_resolver.py` | Resolve task environment templates/assets without inserting goals or criteria into RIM |

### 5.6 Runtime and isolation

| File | Single responsibility |
|---|---|
| `runtime/session_runner.py` | Own one admitted SDK–Translation–MuJoCo session lifecycle; no capability semantics or public robot API |
| `runtime/sdk_lifecycle.py` | Construct/connect/inject/close the exact upstream SDK object according to the SDK Entry |
| `runtime/translation_loader.py` | Load only the Translation artifact pinned by the resolved RIM |
| `runtime/simulator_lifecycle.py` | Load canonical MJCF, reset, step, and close MuJoCo under the simulation profile |
| `runtime/process_supervisor.py` | Start/stop isolated model, candidate, Consumer, and integration processes and enforce cleanup |
| `runtime/workspace_materializer.py` | Create a recipient workspace from an explicit allowlist; never bind-mount the repository root |
| `runtime/candidate_worker.py` | Import one sealed `capability.py`, inject the SDK object through Binding, invoke verified symbols, and return typed results |
| `runtime/resource_limits.py` | Enforce wall time, CPU/memory, process, file, output, episode, and simulation limits |
| `runtime/network_policy.py` | Disable or allow network endpoints by recipient and phase |
| `runtime/import_policy.py` | Enforce allowed packages and forbid MuJoCo/Translation/Framework-private imports in candidate space |
| `runtime/reset_manager.py` | Apply predeclared reset through the private simulator/session boundary and verify no cross-trial residue |
| `runtime/trace_recorder.py` | Record SDK calls, Translation route receipts, timing, process events, and artifact hashes with visibility tags |
| `runtime/evaluation_video.py` | Harness-only lifecycle for mandatory external-view MuJoCo capture, simulator-time frame timestamps, completion/integrity status, and content-addressed media finalization; exposes no robot sensor or public observation |
| `runtime/evaluation_video_manifest.py` | Bind video hash, frame/drop counts, camera/recording profile and simulation-time range/timestamps to run, resolved RIM/simulation profile, append-only infrastructure-execution-attempt identity, candidate revision or frozen layer, and either sealed suite+case+repetition or Demo collection+task+trial+repetition+Consumer identity |
| `runtime/ipc.py` | Internal authenticated message envelopes; no robot control semantics |

The recorder is started only by the trusted Validation B or Demo Harness for an actual formal
execution; Validation A, Stage 1 conformance, Blue Line generation, and ordinary Sandbox probes do
not satisfy or consume this requirement. Once an execution starts, capture continues through its
declared terminal event even on candidate/Consumer failure, exception, or timeout. Capture,
encoding, hash, or seal failure yields `INCOMPLETE` or `CAPTURE_FAILED` infrastructure evidence,
never a candidate diagnostic or Repair input. The recorder must be observational: enabling or
disabling it cannot change simulation stepping, control ordering, public inputs, trusted
measurements, or evaluator inputs.

### 5.7 State-Provided public observation channel

| File | Single responsibility |
|---|---|
| `public_state/profile.py` | Load the frozen State-Provided observation schema, public frames/units, cadence, precision, noise, and latency declarations |
| `public_state/projector.py` | Deterministically project exact simulator state into allowed public entity/state values and public frame/unit conventions |
| `public_state/provider.py` | Serve one frozen typed projection through separately authorized Sandbox-probe, Validation-B-invocation, and Demo-Consumer channels |
| `public_state/invocation_binding.py` | Bind public structured values to declared capability/probe inputs without inventing or extending SDK observation keys |
| `public_state/cadence.py` | Enforce the declared zero/finite latency and deterministic update/caching semantics using simulation timestamps |
| `public_state/trace.py` | Record recipient-tagged exact timestamped deliveries for Sandbox probe episodes, Validation B cases, and Demo/ReAct trials, with channel isolation |

For the first controlled condition, the intended contract is exact MuJoCo-derived state, declared
public frames/units, zero added noise, zero added latency, and deterministic refresh. Sandbox,
Validation B, and Demo use the same pinned profile/projector semantics, while separate recipient
bindings expose only what that caller may receive. Stage 2 receives only the redacted Development
Feedback, not raw/private state; public state is a Framework channel and is never injected as an
invented SDK observation key. This does not make raw MuJoCo names, private contact truth, success
labels, thresholds, or verdicts public.

### 5.8 Shared model runtime and Generation scaffold

| File | Single responsibility |
|---|---|
| `model_runtime/model_client.py` | Provider-neutral request/response interface shared by Generation, Repair, Blue Line, and ReAct, with exact provider/model identity |
| `model_runtime/call_accounting.py` | Account inference, retry, cache, token, wall-time, tool, and cost quantities by role |
| `model_runtime/prompt_renderer.py` | Render pinned prompt templates from schema-validated role inputs |
| `model_runtime/response.py` | Preserve raw responses privately and extract a declared payload without repair-by-parser |
| `model_runtime/budget.py` | Enforce a role's frozen call/tool/token/time policy without owning stage semantics |
| `generation/scaffold.py` | Common Stage 1/Stage 2 producer invocation loop, tool permission, termination, and logging scaffold |
| `generation/stage_workspace.py` | Request recipient workspaces from `workspace_materializer.py` and verify their manifest |

Stage 1 and Stage 2 use the same frozen producer identity and Generation Scaffold but fresh
recipient contexts. Repair uses the same frozen producer identity and neutral transport under its
own invocation/state/budget contract, and is not a Generation stage. The isolated Blue Line and
ReAct Agent use the same neutral model transport, not the Generation package or producer context.

### 5.9 Stage 1 design

| File | Single responsibility |
|---|---|
| `generation/stage1/bundle_builder.py` | Build the recipient-specific Design Bundle from public target/readiness, Morphology, SDK Overview, task descriptions/opaque refs, observation/granularity profiles, and Design Experience |
| `generation/stage1/generator.py` | Run the Stage 1 LLM invocation through the common Scaffold |
| `generation/stage1/artifact_builder.py` | Combine model-authored semantic body with Framework binding/lineage fields |
| `generation/stage1/conformance.py` | Deterministic schema and semantic checker for coverage, refs, units/frames, affordances, gaps, and forbidden implementation/private content |
| `generation/stage1/correction_loop.py` | Bounded public-diagnostic pre-seal correction; distinct from post-Validation Repair |
| `generation/stage1/sealer.py` | Seal the first conformed `capability_design.json` and prevent downstream mutation |

### 5.10 Stage 2 binding, implementation, and Sandbox

| File | Single responsibility |
|---|---|
| `generation/stage2/binding_generator.py` | Deterministically derive the Python Binding Contract from sealed semantic Design |
| `generation/stage2/skeleton_generator.py` | Render starter `capability.py` with only authorized signatures/result envelopes |
| `generation/stage2/bundle_builder.py` | Build the Implementation Bundle: Design, Binding, public SDK implementation projection, public robot facts, implementation Experience, and READY receipt |
| `generation/stage2/generator.py` | Run Stage 2 through the common Scaffold under the 30-inference ceiling |
| `generation/stage2/sandbox_tool.py` | Accept explicit LLM tool requests for public development probes only |
| `generation/stage2/development_probe.py` | Resolve and execute a frozen public probe manifest through Session Runner |
| `generation/stage2/feedback_projection.py` | Redact private rollout data and return bounded non-verdict development feedback |
| `generation/stage2/submission.py` | Accept at most one candidate submission per Stage 2 invocation and seal exact source bytes |
| `generation/stage2/implementation_manifest.py` | Framework-derive manifest from Binding, source bytes, verified symbols, SDK pin, and environment; no name heuristics |
| `generation/stage2/blocked.py` | Represent an authorized `IMPLEMENTATION_BLOCKED` result after its schema is frozen |

### 5.11 Blue Line

| File | Single responsibility |
|---|---|
| `blue_line/source_registry.py` | Preparation-only ingestion registry for the root human reference and future approved human sources; never read during a formal run |
| `blue_line/reference_loader.py` | Formal-run loader for exact reviewed records in the frozen Standards Snapshot only; it never reads live Markdown/source registries |
| `blue_line/measurement_catalog.py` | Resolve trusted physical/interface measurements and applicability; never infer candidate behavior |
| `blue_line/bundle_builder.py` | Build the private sealed-Design + facts + standards + measurement + policy request |
| `blue_line/generator.py` | Invoke the fixed isolated Blue Line LLM once initially and at most twice for checker corrections |
| `blue_line/spec_parser.py` | Extract the structured private Validation Spec without inventing missing fields |
| `blue_line/checker.py` | Check capability/effect/robot/config/protocol applicability, refs, entity/unit/frame, finite values, provenance, privacy, false-pass analysis, cases, and that no Design-external API or obligation was added |
| `blue_line/compiler.py` | Deterministically materialize exact cases, measurements, criteria, seeds, temporal rules, and aggregation for `READY` only |
| `blue_line/review_gate.py` | Return `NEEDS_REVIEW` for every unreviewed `PROPOSED`, material `ADAPTED`, or missing reference/measurement; `READY` requires exact approved scope plus checker pass |
| `blue_line/manifest.py` | Bind model/prompt/config, Design, snapshots, policy, calls, spec, status/reasons, and optional suite hash |
| `blue_line/sealer.py` | Seal spec/suite/manifest before any Stage 2 inference and emit only a non-sensitive READY receipt downstream |

The lineage labels have one operational meaning: `COPIED` reproduces an approved record without a
scope or rule change; `ADAPTED` changes an approved record and records the parent and rationale;
`PROPOSED` has no applicable approved parent. A change of robot/configuration, capability/effect,
measured entity, frame/unit, threshold, temporal rule, case/trial distribution, aggregation, or
protocol applicability is material unless a frozen policy explicitly proves it non-material. Any
unreviewed material `ADAPTED` or `PROPOSED` item stops at `NEEDS_REVIEW`. Human approval creates a
new standards version/snapshot and a new Blue Line run; it never edits an in-flight or sealed run.

### 5.12 Validation and promotion

| File | Single responsibility |
|---|---|
| `validation/a_static.py` | File count/hash, AST/import/dependency, signature, initialization, and forbidden-access checks |
| `validation/a_dynamic.py` | Isolated import and typed invocation checks that do not grade physical success |
| `validation/a_contract.py` | Exact Design–Binding–Manifest–source consistency, unit/frame/error checks |
| `validation/a_runner.py` | Combine A evidence into the authoritative A gate report |
| `validation/binding_overlay.py` | Bind suite semantic IDs to A-verified concrete symbols without copying private suite content |
| `validation/measurement_adapter_protocol.py` | Robot-independent private interface for trusted Validation measurements; implementations live under private governance |
| `validation/b_harness.py` | Execute sealed suite through the resolved real-SDK route, require Evaluation Video for every case/repetition, and collect trusted structured evidence |
| `validation/criterion_evaluator.py` | Apply only sealed measurements/criteria/temporal/aggregation rules; candidate self-report has no verdict authority |
| `validation/failure_evidence.py` | Store raw private failure evidence with provenance and visibility tags |
| `validation/failure_classifier.py` | Separate candidate, Design/contract, infrastructure, standard, task, and provider failures |
| `validation/diagnostic_redactor.py` | Produce bounded Repair diagnostics that cannot reconstruct case/seed/criterion/expected result |
| `validation/promotion.py` | Promote the first revision passing complete A+B, then separately freeze the layer |
| `validation/metrics.py` | Compute `pass@0`, cumulative `pass@k`, Repair gain, conditional success, attempts, cost, and suite executions correctly |

### 5.13 Repair

| File | Single responsibility |
|---|---|
| `repair/orchestrator.py` | Manage at most ten implementation-only Repair invocations and stop at first pass/exhaustion/design gap |
| `repair/invocation.py` | Execute one Repair model/tool loop through the neutral model runtime, record calls, and terminate on its authorized submission/budget |
| `repair/context.py` | For `continued_context`, resume only the authorized Stage 2/Repair provider history plus the new redacted diagnostic; for `fresh_snapshot`, inherit no provider history and create a new context containing the failed source, immutable Stage 2 inputs, bounded ledger summary, and same redacted diagnostic |
| `repair/ledger.py` | Append candidate hashes, parent links, diagnostics, attempts, budgets, tools, and outcomes |
| `repair/diagnostic_projection.py` | Deliver only the redacted diagnostic and bounded ledger summary |
| `repair/budget.py` | Enforce a Repair-specific budget independent of Stage 2 |
| `repair/submission.py` | Accept at most one new `capability.py` per invocation and route it back to A then the same B suite |

### 5.14 Router and ReAct Consumer

| File | Single responsibility |
|---|---|
| `consumer/router_contract.py` | Robot-independent typed semantic operation and result/error envelope |
| `consumer/router.py` | Validate/serialize/route calls to a frozen candidate worker and mediate lifecycle only |
| `consumer/layer_registry.py` | Resolve exactly one frozen layer for a selected run/condition |
| `consumer/adapter_protocol.py` | Future Consumer extension contract shared by ReAct, policies, programs, and planners |
| `consumer/react/bundle_builder.py` | Provide task description, public structured state contract, Router tool view, and frozen budget only |
| `consumer/react/tool_adapter.py` | Present Router operations as ReAct tools without leaking source, SDK, or private state |
| `consumer/react/agent.py` | Execute the bounded reasoning/action/observation loop using the frozen model/prompt/config |
| `consumer/react/trace.py` | Record model/tool steps, invalid calls, latency, tokens, and declared stop reason; not a success verdict |

No policy/program/planner implementation file is created in the first Demo. A future implementation
adds an adapter behind `adapter_protocol.py`; it does not modify `router.py` or robot integrations.

### 5.15 Demo, Evolution, reporting, and CLI

| File | Single responsibility |
|---|---|
| `demo/task_view.py` | Materialize the selected task's description and public state view without criterion or fixed-membership leakage |
| `demo/runner.py` | Execute every task in the frozen five-task collection through ReAct and Router |
| `demo/harness.py` | Acquire independent private task evidence, require Evaluation Video for every trial/repetition, and call the task criterion evaluator |
| `demo/measurement_adapter_protocol.py` | Robot-independent private interface for task-specific physical evidence adapters; implementations live under private governance |
| `demo/criterion_evaluator.py` | Apply the authored private task criterion and common task gates only |
| `demo/metrics.py` | Aggregate task success, invalid calls, latency, invocation/tool/token cost, and failure attribution |
| `demo/report.py` | Produce public/private Demo reports with explicit regression status and no self-verdict substitution |
| `evolution/evidence_collector.py` | Collect evidence only after run closure |
| `evolution/candidate_compiler.py` | Build bounded Design or Implementation Experience Candidates from closed-run evidence |
| `evolution/declassifier.py` | Remove private criteria/cases/seeds/truth/Translation/task solutions and prevent raw video, extracted frames, private camera metadata, or unredacted manifests from entering Experience/publication; test reconstruction risk for any proposed derivative |
| `evolution/review.py` | Record authorized review/admission/quarantine/supersession/revocation; no auto-admission |
| `evolution/snapshot_builder.py` | Build deterministic future recipient-specific Experience snapshots from ADMITTED versions |
| `reporting/run_report.py` | Summarize gates, lineage, budgets, failures, validation, Demo, and Evolution without leaking private artifacts |
| `reporting/audit.py` | Verify all seals, parents, frozen-input consistency, event order, and visibility claims |
| `reporting/experiment_metrics.py` | Produce RQ-ready metric tables without pooling incompatible conditions |
| `cli/main.py` | Command registration only |
| `cli/library_commands.py` | Verify/rebuild/admit-ready Library artifacts; human admission remains external |
| `cli/integration_commands.py` | Resolve RIM and run readiness |
| `cli/run_commands.py` | Prepare/start/resume/inspect a governed run without bypassing gates |
| `cli/audit_commands.py` | Verify run/repository lineage and produce reports |
| `cli/onboard_commands.py` | Post-Demo robot-onboarding checklist orchestration after its contract is frozen |

## 6. Governed data and Library file design

### 6.1 Morphology Library (`AUTHORITY-NAMED RELATIVE LAYOUT`)

```text
general_demo/libraries/morphology/
├── catalog.yaml                                      # DERIVED index
├── robots/
│   └── <robot_model_id>/<morphology_version>/
│       ├── record.json                               # formal JCS morphology-entry subject
│       ├── morphology_facts.json                     # formal typed physical facts
│       ├── simulation/
│       │   └── record.json                           # formal JCS simulation-profile subject
│       ├── source_views/                             # review-only, never formal authority
│       │   ├── manifest.yaml
│       │   ├── morphology.yaml
│       │   └── simulation.yaml
│       ├── model/
│       │   ├── robot.xml                             # canonical formal MJCF
│       │   ├── source.urdf                           # optional provenance only
│       │   ├── meshes/
│       │   └── textures/
│       ├── provenance.yaml
│       └── licenses/
└── environments/
    ├── assets/
    │   └── <asset_id>/<version>/
    │       ├── manifest.yaml
    │       ├── asset.yaml
    │       ├── model/
    │       ├── provenance.yaml
    │       └── licenses/
    └── templates/
        └── <environment_id>/<version>/
            ├── manifest.yaml
            ├── environment.yaml
            ├── world.xml                            # optional base MJCF
            └── provenance.yaml
```

`record.json`, `morphology_facts.json`, and `simulation/record.json` are the formal machine inputs
for the frozen v1 integration subset. The YAML files are generated or checked review views and
cannot replace those JCS records. Morphology facts never contain IK, gait, task logic, capability
implementation, SDK mapping, or validation criteria. The simulation profile names MuJoCo
joints/actuators/sensors for private integration use, while Stage 1 receives a generated design
projection without simulator-internal names. Effective admission is resolved from the external
Admission registry and is not stored as mutable status in any subject.

### 6.2 SDKs Library (`AUTHORITY-NAMED RELATIVE LAYOUT`)

```text
general_demo/libraries/sdks/
├── catalog.yaml                                      # DERIVED index
└── entries/
    └── <sdk_entry_id>/<entry_version>/
        ├── record.json                               # formal JCS sdk-entry subject
        ├── artifact.json                             # formal exact upstream pin/install record
        ├── public_api.json                           # formal admitted public semantics record
        ├── sources.json                              # formal claim-to-primary-evidence record
        ├── source_views/                             # review-only YAML projections
        │   ├── manifest.yaml
        │   ├── artifact.yaml
        │   ├── public_api.yaml
        │   └── sources.yaml
        ├── examples/                                 # bounded checked examples
        └── licenses/
```

The real installed SDK, virtual environment, source checkout, build cache, device secrets, or an
API-compatible facade is not stored in the SDK Library. Formal `artifact.json` resolves the real artifact
into an isolated execution environment. Recipient projections are runtime artifacts, not extra
canonical files inside the Entry. The four formal JSON records, not the YAML review views, govern
identity, pins, API facts, and source lineage. Admission reports and events are owned by the
external Framework-private Admission registry rather than written into the immutable SDK subject.

### 6.3 Tasks Library (`PROPOSED` machine layout)

The root `TASKS_LIBRARY.md` remains the reviewed human-readable catalog. The following future
machine layout is proposed only after `OQ-TASK-001/002` are frozen:

```text
general_demo/libraries/tasks/
├── catalog.yaml                                      # DERIVED index
└── robots/
    └── <robot_configuration_id>/<catalog_version>/
        ├── manifest.yaml
        ├── records/
        │   └── <task_record_id>.yaml                 # complete approved record
        ├── collections/
        │   └── <demo_collection_id>.yaml             # fixed five-task refs only
        ├── provenance.yaml
        └── admission/
            └── report.json
```

Each complete task record owns the description, robot-conditioned applicability/difficulty,
source/adaptation, Harness-private pass criterion, evidence requirements, and review record. It
does not own a capability-validation suite. `task_views.py` derives:

- Stage 1 view: description + run-local opaque requirement ref only;
- ReAct view: current description only;
- Demo Harness view: criterion + required evidence + executable asset refs;
- human view: complete reviewed record.

The SO-ARM101 source catalog currently contains 28 approved records with criteria but no selected
fixed five. Go2 records, criteria, and the exact cross-robot meaning of the five-task collection
remain to be reviewed.

### 6.4 Experience Library (`PROPOSED` machine layout)

```text
general_demo/libraries/experience/
├── catalog.yaml                                      # DERIVED ADMITTED-only index
├── records/
│   ├── design/<record_id>/<version>/
│   │   ├── record.yaml
│   │   ├── provenance.yaml
│   │   └── admission.json
│   └── implementation/<record_id>/<version>/
│       ├── record.yaml
│       ├── provenance.yaml
│       └── admission.json
└── revocations/
    └── <record_id>/<version>.yaml
```

Run-specific Experience selection manifests and recipient projections are `RUNTIME` artifacts and
are not written here. Stage 1 receives only admitted Design Experience projections; Stage 2 and
Repair receive only admitted Implementation Experience projections selected for their role; ReAct
and Demo receive none unless a future Authority contract expressly adds a Consumer-recipient
projection. An initial empty selection is permitted only as an explicit sealed runtime artifact.
`OBSERVED`, `CANDIDATE`, and quarantined records remain in the external Evolution case store; only
reviewed `ADMITTED` versions move into this Library.

### 6.5 Private Blue Line and Demo governance (`not a Library`)

```text
general_demo/private_governance/
├── blue_line/
│   ├── source_registry.yaml                         # preparation sources, versions, hashes
│   ├── standards/
│   │   └── records/<standard_id>/<version>/
│   │       ├── record.yaml
│   │       ├── source_locator.yaml
│   │       └── review.json
│   ├── measurement_catalog/
│   │   ├── catalog.yaml                              # DERIVED private index
│   │   └── records/<measurement_id>/<version>.yaml
│   └── generation_policy/
│       └── <policy_version>.yaml
├── demo/
│   └── criterion_assets/
│       └── <criterion_asset_id>/<version>/
│           ├── manifest.yaml
│           └── payloads/
└── harness_adapters/
    ├── validation/<adapter_id>/<version>/
    │   ├── manifest.yaml
    │   ├── src/
    │   └── tests/
    └── demo/<adapter_id>/<version>/
        ├── manifest.yaml
        ├── src/
        └── tests/
```

`source_registry.yaml` treats `VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md` as the initial
human-authored source of Validation B suite/reference examples from which reviewed project
Validation Standards records may be selected. It is neither a live executable suite nor the only
permanent possible source. The registry binds exact source bytes/hash and import/review lineage;
formal Blue Line runs never read it or browse. Machine standard records are admitted extractions,
approved adaptations, or new human-approved records originating from reviewed `PROPOSED` criteria.
A comparison-specific Frozen Standards Snapshot, Measurement Catalog snapshot, and Blue Line
policy selection are `RUNTIME PRIVATE` artifacts sealed before Blue Line; they do not live in the
source tree. Approval always creates a new immutable record/snapshot and never retrofits the run
that raised the proposal.

Only its validation examples and numerical/temporal criteria are eligible for reviewed Standards
records. Historical runtime-adapter or driver-surface columns provide context only and cannot define
the current Capability Design, Python Binding Contract, SDK surface, RIM, or Stage 2 deliverable.

Demo criterion assets contain trusted evaluation code/data referenced by approved task records.
They are never mounted into Generation, candidate, Repair, or ReAct workspaces.

Harness adapters implement the generic Validation/Demo measurement protocols and may see
simulator-private truth. They are Framework-private governed plugins, not RIM contents and not
Translation behavior. They receive only the compiled suite or task criterion plus the applicable
binding and runtime handles; they do not receive the Blue Line prompt, raw Standards Snapshot, or
LLM Validation Spec.

### 6.6 Generation-owned development probes (`not Validation data`)

```text
general_demo/generation_assets/development_probes/
└── <probe_set_id>/<version>/
    ├── manifest.yaml
    ├── probes/
    ├── public_result_schema.json
    ├── provenance.yaml
    └── admission.json
```

These probes support Stage 2 development through the Sandbox tool. They may expose public SDK or
robot-development facts through the same readiness-passed RIM and real-SDK route, but cannot reuse
Blue Line cases, thresholds, expected outputs, Demo criteria, or privileged truth. Their projected
feedback is explicitly non-verdict.

## 7. Robot-integration file design

RIMs, Translation code, probe definitions, and execution reports have different owners and are
stored separately:

```text
general_demo/integrations/
├── manifests/
│   └── <rim_id>/<rim_version>/
│       ├── record.json                               # formal JCS thin RIM subject
│       ├── rim.yaml                                  # generated/checkable review view only
│       └── provenance.yaml
├── translations/
│   └── <translation_id>/<translation_version>/
│       ├── record.json                               # formal JCS translation-manifest subject
│       ├── mapping.json                              # formal typed field/unit/time mapping
│       ├── protocol.json                             # formal lifecycle/error/timing contract
│       ├── source_views/
│       │   ├── manifest.yaml
│       │   ├── mapping.yaml
│       │   └── protocol.yaml
│       ├── pyproject.toml                            # when implemented in Python
│       ├── src/                                      # hook + command/state mapping
│       ├── native/                                   # optional C/C++ build, if required
│       ├── provenance.yaml
│       └── licenses/
├── readiness/
│   └── profiles/<readiness_profile_id>/<version>/
│       ├── record.json                               # formal JCS readiness-profile subject
│       ├── profile.yaml                              # review view only
│       └── probes/
├── registry_configs/                                 # bootstrap trust anchors; private at install
│   ├── schema/record.json
│   ├── admission/record.json
│   ├── rim_activation/record.json
│   ├── readiness/record.json
│   └── integration_gate/record.json
└── source_evidence/
    └── <source_capture_id>/<version>/
        ├── manifest.yaml
        └── payloads/
```

Generation-owned Sandbox probes are stored separately at
`generation_assets/development_probes/<probe_set_id>/<version>/`. Simulator-private Validation and
Demo adapters are stored under `private_governance/harness_adapters/`; neither class is part of a
robot's thin RIM or Translation package.

The formal RIM `record.json` references exactly one fixed robot configuration, Morphology entry,
simulation profile, SDK Entry, Translation version, and runtime profile. Compatibility and
Translation-conformance reports are external formal evidence consumed by Admission; neither is a
field in the immutable RIM or Translation subject. A RIM contains no task, Experience, criterion,
generated layer, controller, or secret. YAML is review material only.

Each Translation package may contain only:

- device/transport hook integration needed to make the real upstream SDK application layer run;
- SDK-command to MuJoCo-control mapping;
- MuJoCo-state to SDK-observation mapping;
- unit, frame, timing, freshness, clipping, and lifecycle mapping required by the SDK contract; and
- infrastructure traces needed to prove the route.

It may not contain SO-ARM IK/trajectory logic or Go2 stand/sit/move/gait behavior. Those belong in
the generated `capability.py` when required by the sealed Design.

Readiness execution reports, SDK probe outputs, simulator traces, and receipts are runtime
artifacts. Only immutable probe definitions and their admitted evidence belong in source control.

The append-only operational state is stored outside every model, candidate, Repair, and Consumer
workspace in one Framework-private integration state root:

```text
$INTEGRATION_STATE_ROOT/
├── schema_bootstrap/record.json                      # exact bootstrap record
├── reports/                                          # immutable typed evidence records
│   ├── admission/
│   ├── compatibility/
│   ├── translation_conformance/
│   └── readiness/
└── registries/
    ├── admission/{events/,head.json}
    ├── rim_activation/{events/,head.json}
    ├── readiness/{events/,head.json}
    └── integration_gate/{events/,head.json}
```

Only the configured local Integration Admission Authority may append. Gate and state-reducer code
has read-only access and recomputes each canonical chain; run callers never supply trusted event or
receipt payloads. Admission reports, RIM activation, private Readiness reports, trusted Readiness
receipts, and `RIM_RESOLVED`/`INTEGRATION_READY` gate events therefore remain separate from source
records and from ordinary run artifacts.

### 7.1 Two initial integration instances

Authority revision 0.14.0 freezes the following exact Wave 3/4 integration instances. Their
records must still pass the formal admission, activation and Readiness lifecycle before a run may
use them:

| Required item | SO-ARM101 follower + stock gripper | Unitree Go2 |
|---|---|---|
| Robot/configuration IDs | `so-arm101` / `so-arm101-follower-stock-gripper` | `unitree-go2` / `unitree-go2-stock-12dof` |
| Morphology entry | `so-arm101-follower-stock-gripper@1.0.0`; pinned fixed-base canonical MJCF | `unitree-go2-stock-12dof@1.0.0`; pinned free-base stock-12-DoF canonical MJCF |
| Simulation profile | `so-arm101-follower-stock-gripper-simulation@1.0.0` | `unitree-go2-stock-12dof-simulation@1.0.0` |
| SDK Entry | `lerobot-so101-follower@1.0.0`; real LeRobot 0.6.0 + Feetech 1.0.0 | `unitree-sdk2-go2-lowlevel@1.0.0`; real SDK2Py 1.0.1 + CycloneDDS 0.10.2, low-level surface only |
| RIM | `so-arm101-follower-stock-gripper-mujoco@1.0.0` | `unitree-go2-stock-12dof-mujoco@1.0.0` |
| Translation | `lerobot-so101-feetech-pty-mujoco@1.0.0`; real serial path through project-owned PTY device boundary | `unitree-sdk2-go2-dds-mujoco@1.0.0`; DDS domain-1 low-command/state route; no synthesized gait |
| Runtime | `so-arm101-linux-amd64@1.0.0`; Ubuntu 24.04, CPython 3.12, MuJoCo 3.3.6 | `unitree-go2-linux-amd64@1.0.0`; Ubuntu 22.04, CPython 3.10, MuJoCo 3.3.6, CycloneDDS 0.10.2 |
| Readiness profile | `general-demo-integration-readiness@1.0.0`, six frozen checks | the same frozen profile, including DDS timing/reset/isolation checks |
| Development probes | Generation-owned public capability-development facts, not Validation cases | Generation-owned public low-level/posture/motion facts, not Demo/Validation cases |
| Private Harness adapters | arm/gripper state and task evidence | base/joint/contact/posture/motion evidence as required by approved criteria |
| Task records | existing 28 reviewed records; select fixed collection later | new reviewed catalog and private criteria required |
| Standards/measurements | reviewed reference records + robot-scoped bindings | reviewed/adapted/proposed records completed before formal use |

Absence of a convenient Go2 high-level method does not cause the Framework to add it to Translation.
Stage 1 may design, and Stage 2 may synthesize, reusable G2 stand/sit/move capabilities above the
admitted lower-level SDK actions and observations.

### 7.2 Execution environments

```text
general_demo/environments/
├── framework/Containerfile
├── integrations/
│   └── <runtime_profile_id>/Containerfile
└── runtime_profiles/
    └── <runtime_profile_id>/<version>/
        ├── record.json                               # formal JCS runtime-profile subject
        └── profile.yaml                              # review view only
```

These are the construction locations for reproducible build environments, not SDK evidence. The
two v1 runtime identities and required platforms are frozen by Authority 0.14.0, but their subject
records remain unadmitted until an actual build captures non-placeholder OCI image, interpreter,
dependency-lock, and platform-fingerprint hashes. The SDK Entry still owns the upstream SDK pin
and installation semantics. The build verifies that the installed bytes match the Entry.
Robot/network/device secrets and host paths are injected externally and never written into
Containerfiles, profiles, or artifacts.

Separate profiles are allowed when SDK platform requirements differ, but they must implement the
same internal Runner/Translation protocols and cannot fork the generic Framework lifecycle.

## 8. Schema-file inventory

Authority revision 0.14.0 freezes the integration-v1 schema contract below; its exact bytes and
package hash are generated, independently checked, and bootstrapped before any formal record is
admitted. Outside that explicitly listed subset, the Authority currently names only the basename
`capability_design.schema.json`; every other schema basename/ID below remains proposed. No schema
is stored at a mutable flat path. Every proposed logical table entry of the form
`contracts/schemas/<family>/<basename>.schema.json` expands to this actual versioned layout:

```text
contracts/schemas/<family>/<schema_id>/<schema_version>/
├── <basename>.schema.json
└── manifest.yaml                 # ID/version/hash/freeze record; immutable after release
```

The shorter paths in the inventory are logical schema-ID/basename notation, not loadable filesystem
paths. The registry resolves only an exact family + schema ID + schema version + manifest/payload
hash. It rejects flat/unversioned schema files, overwritten released versions, and all proposals
until their owning OQ is frozen.

### 8.0 Authority-frozen integration-v1 schema package

```text
contracts/schema_packages/general-demo-integration-schemas/1.0.0/
├── package_manifest.json
└── schemas/
    └── <logical_schema_id>.schema.json
```

The package contains exactly one version-`1.0.0` member for each of these logical IDs and no other
member:

| Frozen logical schemas |
|---|
| `exact-reference`, `schema-bootstrap`, `subject-record` |
| `admission-report`, `admission-event`, `rim-activation-event` |
| `morphology-entry`, `morphology-facts`, `simulation-profile` |
| `sdk-entry`, `sdk-artifact`, `sdk-public-api`, `sdk-sources` |
| `translation-manifest`, `translation-mapping`, `translation-protocol`, `translation-conformance-report` |
| `robot-integration-manifest`, `compatibility-report`, `runtime-profile` |
| `readiness-profile`, `readiness-report`, `readiness-receipt` |
| `registry-config`, `integration-selection`, `resolved-combination`, `integration-gate-receipt` |

These schemas, package canonicalization, bootstrap sequence, exact references, event transitions,
and acceptance vectors are governed directly by Authority 0.14.0. The implementation may not
substitute the similarly named proposed schemas below, add mutable status to a subject, or treat a
YAML review view as a formal record.

### 8.1 Common, orchestration, model-runtime, Library, integration, and public-state schemas

| Planned schema | Governs |
|---|---|
| `contracts/schemas/common/identifier.schema.json` | versioned IDs and typed refs |
| `contracts/schemas/common/content_hash.schema.json` | algorithm-tagged content hashes |
| `contracts/schemas/common/seal.schema.json` | artifact type/hash/parents/canonicalizer/seal event |
| `contracts/schemas/common/provenance.schema.json` | source/version/license/locator/transformation/applicability |
| `contracts/schemas/common/evidence.schema.json` | evidence item, owner, visibility, realization and verification status |
| `contracts/schemas/common/artifact_descriptor.schema.json` | artifact type/owner/visibility/media type including `video/*`, hash and parents |
| `contracts/schemas/evidence/recording_profile.schema.json` | frozen evaluation-only camera/view, timing, encoding, completeness and resource constraints |
| `contracts/schemas/evidence/video_recording_manifest.schema.json` | mandatory private video binding: run, resolved RIM/simulation, append-only infrastructure attempt, candidate revision or layer, sealed suite/case/repetition or Demo collection/task/trial/repetition/Consumer, recording profile/camera, simulation-time frames, media hash/bytes/frame/drop counts, completion and integrity status |
| `contracts/schemas/orchestration/campaign.schema.json` | ordered independent single-RIM run-profile refs |
| `contracts/schemas/orchestration/run_profile.schema.json` | one-RIM frozen run selection and role/config refs |
| `contracts/schemas/orchestration/run_request.schema.json` | one concrete run identity and parent campaign/cell refs |
| `contracts/schemas/orchestration/run_manifest.schema.json` | authoritative run inputs, states, artifacts, budgets, and event-log ref |
| `contracts/schemas/orchestration/run_closure.schema.json` | immutable terminal run boundary and all terminal artifact hashes, including every required video manifest and media blob |
| `contracts/schemas/model_runtime/call_record.schema.json` | role/model/context/request/response/usage/retry/cache accounting |
| `contracts/schemas/model_runtime/budget.schema.json` | versioned role-specific inference/tool/time/cost budget |
| `contracts/schemas/libraries/environment_asset.schema.json` | reusable environment assets |
| `contracts/schemas/libraries/environment_template.schema.json` | layouts/frames without tasks/criteria |
| `contracts/schemas/libraries/task_record.schema.json` | complete approved task + private criterion |
| `contracts/schemas/libraries/task_collection.schema.json` | immutable fixed task refs |
| `contracts/schemas/libraries/experience_record.schema.json` | Design/Implementation Experience lifecycle |
| `contracts/schemas/libraries/experience_snapshot.schema.json` | future-run exact admitted record versions |
| `contracts/schemas/public_state/profile.schema.json` | public entity/state/frame/unit/cadence/noise/latency contract |
| `contracts/schemas/public_state/update.schema.json` | one timestamped structured-state delivery |
| `contracts/schemas/public_state/trace.schema.json` | recipient-tagged ordered public deliveries for a Sandbox probe episode, Validation B case, or Demo task trial |

### 8.2 Generation and Blue Line schemas

| Planned schema | Governs |
|---|---|
| `contracts/schemas/generation/stage1_bundle.schema.json` | exact Stage 1 recipient input |
| `contracts/schemas/generation/capability_design.schema.json` | Proposed home for the `AUTHORITY-NAMED BASENAME` robot-independent semantic schema |
| `contracts/schemas/generation/stage1_conformance_report.schema.json` | public pre-seal diagnostics |
| `contracts/schemas/generation/python_binding_contract.schema.json` | semantic-to-Python binding |
| `contracts/schemas/generation/stage2_bundle.schema.json` | exact Stage 2 recipient input |
| `contracts/schemas/generation/implementation_manifest.schema.json` | Framework-derived source/binding/SDK/dependency record |
| `contracts/schemas/generation/implementation_blocked.schema.json` | authorized implementation gap |
| `contracts/schemas/generation/sandbox_request.schema.json` | LLM-requested public probe call |
| `contracts/schemas/generation/development_feedback.schema.json` | redacted non-verdict Sandbox response |
| `contracts/schemas/blue_line/standards_record.schema.json` | reviewed human project validation reference record |
| `contracts/schemas/blue_line/standards_snapshot.schema.json` | exact frozen records for a comparison |
| `contracts/schemas/blue_line/measurement_record.schema.json` | trusted measurand/signal/frame/unit/metric applicability |
| `contracts/schemas/blue_line/measurement_snapshot.schema.json` | exact frozen measurement records |
| `contracts/schemas/blue_line/generation_policy.schema.json` | cases/trials/repetitions/resources/provenance rules |
| `contracts/schemas/blue_line/validation_spec.schema.json` | LLM-authored `blue_line_validation_spec.json` |
| `contracts/schemas/blue_line/validation_b_suite.schema.json` | exact private executable suite |
| `contracts/schemas/blue_line/blue_line_manifest.schema.json` | calls/lineage/status/reasons/spec/suite refs |

### 8.3 Validation, Consumer, Demo, and Evolution schemas

| Planned schema | Governs |
|---|---|
| `contracts/schemas/validation/validation_a_report.schema.json` | A checks/evidence/verdict |
| `contracts/schemas/validation/binding_overlay.schema.json` | suite-to-verified-symbol mapping only |
| `contracts/schemas/validation/validation_b_report.schema.json` | suite execution and authoritative verdict; every started case/repetition lists all append-only infrastructure attempts and selects at most one complete verdict-eligible recording ref/status |
| `contracts/schemas/validation/failure_evidence.schema.json` | append-only infrastructure-attempt-scoped private raw evidence |
| `contracts/schemas/validation/failure_classification.schema.json` | candidate/design/infrastructure/etc. taxonomy |
| `contracts/schemas/validation/repair_diagnostic.schema.json` | redacted bounded Repair input |
| `contracts/schemas/validation/repair_ledger.schema.json` | attempts, hashes, parents, budgets, outcomes |
| `contracts/schemas/validation/layer_manifest.schema.json` | validated/frozen layer identity |
| `contracts/schemas/consumer/router_contract.schema.json` | typed semantic operations/results/errors |
| `contracts/schemas/consumer/router_view.schema.json` | run/condition-specific Consumer surface |
| `contracts/schemas/consumer/react_bundle.schema.json` | description/state/tools/budget only |
| `contracts/schemas/consumer/react_trace.schema.json` | reasoning/tool/invocation accounting, not verdict |
| `contracts/schemas/demo/task_public_view.schema.json` | ReAct-visible task projection |
| `contracts/schemas/demo/task_private_view.schema.json` | Harness criterion/evidence projection |
| `contracts/schemas/demo/demo_trial.schema.json` | reset/scene/Consumer/layer refs, trial evidence, all append-only infrastructure attempts, and at most one selected complete verdict-eligible recording ref/status |
| `contracts/schemas/demo/demo_verdict.schema.json` | private authoritative task verdict |
| `contracts/schemas/demo/demo_report.schema.json` | five-task aggregation and costs |
| `contracts/schemas/evolution/experience_candidate.schema.json` | OBSERVED/CANDIDATE proposal |
| `contracts/schemas/evolution/declassification_report.schema.json` | private-data removal/reconstruction tests |
| `contracts/schemas/evolution/review_record.schema.json` | admission/quarantine/supersede/revoke decision |

Draft schemas may be discussed under a future `design_proposals/<OQ-ID>/` path carrying a
`PROPOSED_NOT_IMPLEMENTABLE` marker. Production schema/profile loaders reject that entire path.
Only reviewed/frozen versions move into `contracts/`.

## 9. Runtime artifact and workspace design

Run data is stored under a configured repository-external root. The environment variable name and
backend remain to be frozen; `$ARTIFACT_ROOT` below is explanatory only.

### 9.1 Content-addressed object store

```text
$ARTIFACT_ROOT/
├── objects/
│   └── sha256/<first-two>/<full-hash>                # immutable exact bytes
├── runs/
│   └── <run_id>/                                     # small typed refs and indices
├── workspaces/
│   └── <run_id>/<recipient>/<workspace_nonce>/       # temporary allowlisted materialization
├── evolution_cases/
│   └── <case_id>/                                    # post-closure refs to one or more closure hashes
├── derived_reports/
│   └── <closure_hash>/<report_profile_hash>/         # reproducible post-closure views
├── sdk_environments/                                 # installed pinned SDKs, not evidence authority
├── caches/                                           # provider/build/model caches, never run inputs implicitly
└── quarantine/                                       # malformed/untrusted payloads inaccessible to stages
```

The object store owns bytes once. Human-friendly run paths contain typed references, hashes, and
sealed manifests rather than mutable copies. A run index cannot change an object. Deletion and
retention follow the frozen policy; credentials are never objects. Evaluation Video media and
temporary rendered frames never enter the Git/source tree or a model/candidate workspace. Only the
private recording manifest is placed in the run graph; the exact media bytes live under
`objects/sha256/`.
Recording parameters and provenance are reproducible, but encoded video bytes are not required to
be identical across reruns. Each execution records and seals the hash of the bytes it actually
produced; byte equality is never a Validation or Demo acceptance condition.

### 9.2 Per-run artifact graph

```text
runs/<run_id>/
├── run_manifest.json
├── events.jsonl
├── 00_inputs/
│   ├── run_request.json
│   ├── authority_contract_set.json
│   ├── resolved_rim.json
│   ├── library_snapshot_manifest.json
│   ├── experience_selection_manifest.json           # PRIVATE selected versions + applicability
│   ├── experience_views/
│   │   ├── stage1_design.json
│   │   └── stage2_implementation.json
│   ├── observation_profile.json
│   ├── granularity_profile.json
│   ├── ready_for_stage1_receipt_ref.json             # later composite gate; schema still OPEN
│   └── budget_and_model_config_manifest.json
├── 01_readiness/
│   ├── readiness_report_ref.json                     # exact ref to PRIVATE immutable report
│   ├── rim_resolved_receipt_ref.json                 # exact trusted gate-log event ref
│   ├── readiness_receipt_ref.json                    # exact trusted readiness-log event ref
│   ├── integration_ready_receipt_ref.json            # exact trusted gate-log event ref
│   └── trace_refs.json
├── 02_stage1/
│   ├── stage1_bundle.json
│   ├── workspace_manifest.json
│   ├── calls/<call_index>/request.json
│   ├── calls/<call_index>/response.json
│   ├── calls/<call_index>/usage.json
│   ├── revisions/<revision>/capability_design.json
│   ├── conformance/<revision>/report.json
│   ├── capability_design.json                        # AUTHORITY-NAMED BASENAME, sealed winner
│   └── capability_design.seal.json
├── 03_blue_line/
│   └── private/
│       ├── standards_snapshot.json
│       ├── measurement_snapshot.json
│       ├── generation_policy.json
│       ├── blue_line_bundle.json
│       ├── calls/<call_index>/request.json
│       ├── calls/<call_index>/response.json
│       ├── calls/<call_index>/usage.json
│       ├── checker/<revision>/report.json
│       ├── blue_line_validation_spec.json            # AUTHORITY-NAMED BASENAME
│       ├── validation_b_suite.json                    # AUTHORITY-NAMED BASENAME; READY only
│       ├── blue_line_manifest.json                    # AUTHORITY-NAMED BASENAME
│       └── seals/
├── 04_stage2/
│   ├── ready_receipt.json                            # no suite identity/hash
│   ├── python_binding_contract.json
│   ├── starter/capability.py
│   ├── stage2_bundle.json
│   ├── workspace_manifest.json
│   ├── calls/<call_index>/request.json
│   ├── calls/<call_index>/response.json
│   ├── calls/<call_index>/usage.json
│   ├── sandbox/<tool_call_id>/
│   │   ├── request.json
│   │   ├── public_state_trace_ref.json
│   │   ├── private_trace_refs.json
│   │   └── development_feedback.json
│   └── submissions/rev_000/
│       ├── capability.py                             # AUTHORITY-NAMED BASENAME candidate
│       ├── candidate.seal.json
│       └── implementation_manifest.json
├── 05_validation/
│   └── rev_000/
│       ├── validation_a_report.json
│       ├── binding_overlay.json                      # only after A pass
│       ├── private/executions/<case_id>/repetition_<n>/execution_attempt_<infra_retry_n>/
│       │   ├── public_state_trace_ref.json           # only when declared by suite/Design
│       │   ├── private_failure_evidence.json         # only when that attempt fails
│       │   └── video_recording_manifest.json         # media bytes live in object store
│       ├── validation_b_report.json                  # only after A pass
│       └── repair_diagnostic.json
├── 06_repair/
│   ├── repair_ledger.json
│   └── attempt_<01..10>/
│       ├── authorized_input_manifest.json
│       ├── calls/
│       ├── sandbox/
│       ├── capability.py
│       ├── candidate.seal.json
│       ├── implementation_manifest.json
│       └── validation/                               # new A/Overlay/B, same suite hash
│           ├── validation_a_report.json
│           ├── binding_overlay.json                  # only after A pass
│           ├── private/executions/<case_id>/repetition_<n>/execution_attempt_<infra_retry_n>/
│           │   ├── public_state_trace_ref.json       # only when declared by suite/Design
│           │   ├── private_failure_evidence.json     # only when that attempt fails
│           │   └── video_recording_manifest.json
│           └── validation_b_report.json
├── 07_layer/
│   ├── validated_layer_manifest.json
│   ├── frozen_layer_manifest.json
│   └── freeze_receipt.json
├── 08_demo/
│   ├── public/
│   │   ├── router_view.json
│   │   └── tasks/<task_ref>/trial_<trial_n>/repetition_<repetition_n>/execution_attempt_<infra_retry_n>/
│   │       ├── react_bundle.json                  # this task/trial only
│   │       ├── public_state_trace.jsonl           # timestamped values delivered
│   │       └── consumer_trace.jsonl
│   └── private/
│       ├── demo_collection_ref.json                    # never visible to ReAct
│       ├── tasks/<task_ref>/trial_<trial_n>/repetition_<repetition_n>/execution_attempt_<infra_retry_n>/
│       │   ├── reset_and_scene_refs.json
│       │   ├── criterion_ref.json
│       │   ├── private_evidence.json
│       │   ├── video_recording_manifest.json         # mandatory private video ref
│       │   └── verdict.json
│       └── demo_report.json
└── 09_closure/
    ├── preclosure_audit.json
    ├── run_closure.json
    └── run_closure.seal.json
```

Every stage branch is gate-conditional, but every terminal path writes and seals `09_closure/`. A
`NEEDS_REVIEW` run stops stage progression in `03_blue_line/private/` without Stage 2, candidate, or
formal suite seal; a failed readiness run stops before Stage 1; Design gaps, Repair exhaustion, and
infrastructure-terminal outcomes stop at their owning stage. Skipped stage directories are not
created, then the terminal state, existing refs, and reason are captured in the closure. Once
`run_closure.seal.json` exists, nothing under `runs/<run_id>/` may be added, replaced, or deleted.
Evolution cases and human/public derived reports are separate objects under `evolution_cases/` and
`derived_reports/`; each records the immutable closure hash rather than extending the closed run.

### 9.3 Post-closure Evolution and reporting artifacts

```text
evolution_cases/<case_id>/
├── closure_refs.json                              # one or more immutable run-closure hashes
├── evidence_manifest.json
├── experience_candidate.json
├── declassification_report.json
└── reviews/
    └── <review_event_id>.json                    # append-only review/admit/supersede/revoke event

derived_reports/<closure_hash>/<report_profile_hash>/
├── derivation_manifest.json                     # closure + report code/config hashes
├── audit_report.json
├── run_report.json
└── metrics.json
```

These artifacts are descendants of a closure, not children that can mutate a closed run. Rebuilding
the same report profile may create the same bytes/hash; changing report logic/config creates a new
profile hash. An Evolution review can admit a new future Library version but never rewrite either
the originating run or its closure.

### 9.4 Recipient workspaces

Each workspace has a generated `workspace_manifest.json` listing every materialized file/hash and
one explicit recipient. The parent artifact store and repository are not mounted.

| Workspace | Files allowed | Files explicitly absent |
|---|---|---|
| Stage 1 | pinned prompt/config, Design Bundle, capability Design schema, public conformance errors, output directory | raw Libraries, exact SDK API, MJCF names, RIM/Translation, task criteria/IDs/difficulty/Demo membership, standards/suite |
| Blue Line | pinned private prompt/config, sealed Design, resolved approved facts, frozen standards/measurements/policy, spec schema | Stage 2 code/context, producer identity, candidate/Sandbox/Validation/Repair/Demo outcomes |
| Stage 2 | pinned prompt/config, Design, Binding, skeleton, SDK Implementation Projection, public robot facts, implementation Experience, Sandbox endpoint | Tasks, Blue spec/manifest/suite identity or hash, criteria/cases/seeds, MJCF/Translation/private truth |
| Repair | failed code, same Stage 2 inputs, Binding/Design, bounded ledger, redacted diagnostic, authorized Sandbox endpoint | task descriptions, standards, Blue artifacts, private failure evidence, raw truth, expected solution |
| Candidate worker | one sealed `capability.py`, verified Binding invocation, injected real SDK object, approved dependencies, typed public call inputs | repository, Libraries, model prompts, MuJoCo/Translation modules, Harness, network/hardware, other candidates |
| ReAct | Consumer prompt/config, task description, public structured state, Router tool view, budget | implementation source/object, SDK object, Libraries, task criterion, Validation suite, private state |
| Validation Harness | compiled sealed suite, Binding Overlay, candidate invocation endpoint, RIM/runtime access, admitted Validation adapter, and frozen Evaluation Video recording profile/recorder | raw Standards Snapshot, Blue Line prompt/spec/model output, task criteria, and any authority to alter Design/suite/candidate; video never enters candidate/Repair feedback |
| Demo Harness | private task criterion, Router invocation trace, RIM/runtime access, admitted Demo adapter, and frozen Evaluation Video recording profile/recorder | Blue Line inputs/spec, Validation suite internals, ReAct hidden context, and any authority to alter the authored criterion; video never enters ReAct |

Workspace deletion follows run policy, while its manifest and every model/tool input/output hash
remain auditable. Hidden reasoning is not claimed to be captured if a provider does not expose it.

### 9.5 Seal and event order invariants

The audit module rejects any run violating these partial-order rules:

1. selected Library, exactly one RIM, contracts, and config hashes precede readiness, and the same
   RIM hash persists through Sandbox, Validation B, and Demo;
2. trusted `INTEGRATION_READY` and the later composite `READY_FOR_STAGE1` receipt both precede the
   first Stage 1 call; Readiness alone is insufficient;
3. Stage 1 conformance pass precedes Design seal;
4. Design seal precedes the first Blue Line call;
5. `READY` spec/suite/manifest seals precede Binding release and every Stage 2 call;
6. candidate source/Implementation Manifest seal precedes Validation A;
7. A pass precedes Binding Overlay, and Overlay precedes B. For every started B case/repetition:
   reset completion precedes recorder activation and a post-reset/pre-invocation frame; recorder
   activation precedes invocation; the terminal event precedes recorder finalization; and the
   sealed `COMPLETE`, `INCOMPLETE`, or `CAPTURE_FAILED` manifest precedes verdict eligibility or
   infrastructure disposition. Any partial-media/incomplete manifest is sealed before rerun
   scheduling; every retry has a new append-only infrastructure-attempt ID under the same logical
   case/repetition and never overwrites an earlier attempt. Only one `COMPLETE`, verdict-eligible
   attempt may contribute at the current Repair index;
8. a Repair attempt starts only after candidate-attributable failure and submits one child source;
9. every revision reruns A and references the same suite hash in B;
10. the first complete A+B pass precedes layer validation/freeze and terminates selection;
11. layer freeze precedes every Demo task;
12. for every started Demo task trial/repetition: reset completion precedes recorder activation and
    a post-reset/pre-task frame; recorder activation precedes task execution; the terminal event
    precedes recorder finalization; and the sealed capture manifest precedes verdict eligibility or
    infrastructure disposition. Any partial-media/incomplete manifest is sealed before rerun
    scheduling; every retry has a new append-only infrastructure-attempt ID under the same logical
    task/trial/repetition and never overwrites an earlier attempt. Only one `COMPLETE`,
    verdict-eligible attempt may enter Demo metrics, and all five selected tasks complete before
    Demo aggregation;
13. run closure precedes Evolution evidence compilation; and
14. Experience admission, if any, occurs outside the closed run and affects future snapshots only.

### 9.6 Principal artifact ownership and visibility

| Artifact | Authoritative owner | Authorized recipients | Seal/freeze point |
|---|---|---|---|
| Canonical Morphology/SDK/Task/Experience records | corresponding Library maintainer/admission authority | Framework resolvers; recipients receive projections only | entry admission |
| RIM | integration admission authority | resolver, readiness, Session Runner, Validation/Demo orchestration | before run selection |
| State-Provided profile | observation-contract admission authority | projector/provider, Stage 1 profile projection, Sandbox/Validation/Demo invocation bindings | before run preparation |
| State-Provided update/trace | Framework public-state provider/recorder | exact authorized caller plus audit/reporting; no cross-role history | every delivered update/event |
| Readiness full report | Framework readiness runner/gate, using Session Runner evidence | Framework audit and readiness gate only | before receipt |
| Readiness receipt | Framework readiness gate | Stage 1 and run gates | before Stage 1 |
| Stage 1 Design Bundle | Run Input assembler | Stage 1 only | before first Stage 1 call |
| Model request/response/usage record | neutral model runtime, namespaced by role/context | owning role and audit; only authorized Stage 2/Repair history may continue into `continued_context`, while Blue records remain private, fresh Repair has no history, and Stage 1 history never enters Stage 2 | after each provider event |
| `capability_design.json` | Stage 1 semantic body + Framework artifact builder/conformance/sealer | Blue Line; after READY, Stage 2 and Validation A | conformance pass before Blue Line |
| Standards/Measurement/Policy snapshots | private assessment-governance snapshot authority | Blue Line loader/generator/checker/compiler/audit only | before first Blue Line call/formal comparison |
| Blue Line Validation Spec and Manifest | Blue Line LLM body + deterministic parser/checker/manifest/sealer | private Blue Line governance and audit only | READY before every Stage 2 call |
| `validation_b_suite.json` | deterministic Blue Line compiler/sealer | Validation B Harness and audit only | READY before every Stage 2 call |
| Python Binding Contract/skeleton | Framework deterministic generator | Stage 2, Repair, Validation A | generated from Design; released only after READY |
| Stage 2 Bundle | Run Input assembler | Stage 2 only; frozen subset reused by Repair | before first Stage 2 call |
| `capability.py` revision | Stage 2 or one Repair invocation | candidate worker, A/B, later Repair for failed revision | every submission before A |
| Implementation Manifest | Framework manifest builder | Validation A/B and audit only | after source seal before A |
| Development Feedback Projection | Framework Sandbox feedback redactor | only the requesting Stage 2 or Repair invocation plus audit | after private probe execution; raw trace remains private |
| Private Validation/Demo Harness adapter version | private adapter admission authority | applicable Harness loader and audit only | adapter admission before comparison |
| Evaluation Video recording profile, media and manifest | recording-profile admission authority; media/manifest produced by trusted runtime recorder under Harness control | private Harness/audit/report derivation only; redacted/declassified derivative requires separate authority | profile before comparison; one content-addressed manifest per B case/repetition and Demo trial/repetition |
| Binding Overlay | trusted binder | Validation B Harness only | after A pass before B, per revision |
| Raw Validation A failure evidence | Validation A runner | failure classifier/audit only | after A execution |
| Raw Validation B failure evidence | trusted Validation B Harness | failure classifier/audit only | after B execution |
| Repair diagnostic | redactor | authorized Repair invocation only | after failure classification |
| Frozen capability layer manifest | promoter/freezer | Router/runtime/audit | first complete A+B pass then explicit freeze |
| ReAct bundle/router view | Framework Consumer assembler | ReAct only | per task/trial; never contains collection membership |
| Full task criterion | Tasks Library admission authority | Demo Harness/audit only | task record/collection freeze before comparison |
| Private Demo evidence/verdict | trusted Demo Harness/evaluator | Demo audit/report derivation only | after each trial/aggregation |
| ReAct trace | Consumer runtime | reporting/audit only; publication requires a separately frozen redaction/declassification policy | after each model/tool event |
| Run closure | Framework closure authority | immutable source for audit/reporting/Evolution | after all in-run events; no later child writes |
| Experience Candidate | Evolution compiler | reviewer/declassifier/audit | only after run closure |
| ADMITTED Experience record | authorized Experience reviewer | future deterministic retrieval and recipient projections | admission outside originating run |

No downstream recipient can become the owner of an upstream artifact by copying it into its
workspace. All copies remain references to the same content hash and visibility class.

## 10. Test-file design

Mocks and API-compatible shims are allowed only below `tests/fixtures/`. Formal integration and
end-to-end tests carry a marker that requires an admitted real SDK environment and rejects fixture
provenance.

### 10.1 Unit and contract tests

```text
tests/unit/
├── test_identifiers.py
├── test_canonical_serialization.py
├── test_hashing_and_tree_hashes.py
├── test_seals_and_parent_lineage.py
├── test_redaction_primitives.py
├── test_budget_accounting.py
├── test_error_classification.py
└── test_event_log_integrity.py

tests/contracts/
├── test_all_frozen_schemas_are_valid.py
├── test_proposal_schemas_are_not_loadable.py
├── test_unversioned_schema_is_not_loadable.py
├── test_released_schema_version_cannot_be_overwritten.py
├── test_reference_resolution_is_exact.py
├── test_catalogs_rebuild_from_manifests.py
├── test_recipient_projection_allowlists.py
├── test_state_provided_profile_and_update_contract.py
├── test_run_gate_state_machine.py
├── test_event_order_invariants.py
├── test_content_addressed_immutability.py
├── test_recording_profile_is_versioned_frozen_and_evaluation_only.py
├── test_evaluation_video_manifest_identity_time_hash_and_completion.py
├── test_run_closure_covers_every_video_manifest_and_media_blob.py
├── test_closed_run_index_events_and_refs_reject_all_writes.py
├── test_stage1_design_is_robot_independent_schema.py
├── test_design_binding_manifest_consistency.py
├── test_suite_overlay_non_mutation.py
└── test_traceability_to_frozen_authority.py
```

### 10.2 Security and visibility tests

```text
tests/security/
├── test_stage1_cannot_see_private_task_fields.py
├── test_stage1_cannot_see_exact_sdk_or_mujoco_names.py
├── test_blue_line_cannot_see_candidate_or_outcomes.py
├── test_stage2_cannot_see_tasks_or_blue_line_identity.py
├── test_repair_cannot_reconstruct_case_or_criterion.py
├── test_repair_workspace_excludes_implementation_manifest_and_blue_artifacts.py
├── test_candidate_cannot_import_mujoco_or_translation.py
├── test_candidate_cannot_open_repository_or_artifact_root.py
├── test_candidate_cannot_create_second_sdk_or_use_hardware.py
├── test_consumer_cannot_bypass_router.py
├── test_react_cannot_see_code_sdk_or_criterion.py
├── test_harness_private_truth_never_enters_feedback.py
├── test_evaluation_video_never_enters_blue_line_stage1_stage2_sandbox_repair_candidate_or_consumer.py
├── test_raw_video_frames_camera_metadata_and_manifest_require_declassification_before_publication.py
├── test_video_contains_no_criterion_threshold_score_expected_solution_private_debug_or_model_reasoning.py
├── test_raw_video_frames_and_unredacted_manifest_never_enter_experience.py
├── test_candidate_and_consumer_cannot_start_stop_configure_read_or_suppress_recorder.py
├── test_harness_adapter_receives_suite_or_criterion_not_raw_standards_or_blue_spec.py
├── test_network_policy_by_recipient.py
├── test_workspace_manifest_matches_materialized_files.py
├── test_cross_run_process_and_storage_isolation.py
├── test_source_tree_contains_no_runtime_artifacts_sdk_installs_workspaces_video_or_temp_frames.py
└── test_fixture_provenance_rejected_after_copy_or_rename.py
```

### 10.3 Library and source tests

```text
tests/libraries/
├── test_morphology_payload_hashes_and_licenses.py
├── test_canonical_mjcf_loads_with_all_assets.py
├── test_morphology_has_no_behavior_fields.py
├── test_environment_templates_have_no_tasks_or_criteria.py
├── test_sdk_artifact_pin_and_installed_bytes.py
├── test_sdk_api_claims_have_version_matched_evidence.py
├── test_sdk_admission_report_closure.py
├── test_every_admitted_task_has_private_criterion.py
├── test_task_public_and_private_views.py
├── test_fixed_demo_collection_is_exact_and_complete.py
├── test_experience_catalog_contains_admitted_only.py
├── test_experience_declassification_and_applicability.py
├── test_human_validation_reference_import_lineage.py
└── test_auto_adapter_1_assets_are_re_admitted_not_inherited.py
```

### 10.4 Integration and readiness tests

```text
tests/integration/
├── test_integration_schema_package_exact_members_and_jcs.py
├── test_schema_bootstrap_and_registry_trust_anchors.py
├── test_admission_and_activation_event_chains.py
├── test_forged_caller_events_and_receipts_are_rejected.py
├── test_registry_forks_cycles_stale_priors_and_replay_are_rejected.py
├── test_revoked_or_deactivated_rim_is_rejected.py
├── test_rim_resolved_must_precede_integration_ready.py
├── test_readiness_report_and_receipt_require_prior_rim_resolved.py
├── test_direct_duplicate_or_cross_selection_integration_ready_is_rejected.py
├── test_readiness_profile_must_match_selection_report_and_receipt.py
├── test_wrong_run_selection_combination_or_log_head_is_rejected.py
├── test_rim_is_thin_and_all_refs_resolve.py
├── test_one_run_resolves_exactly_one_rim.py
├── test_rim_hash_unchanged_readiness_sandbox_validation_demo.py
├── test_real_sdk_pin_loads.py
├── test_device_transport_hook_installs.py
├── test_real_sdk_application_method_executes.py
├── test_command_reaches_mujoco_actuators.py
├── test_state_returns_through_real_sdk_observation.py
├── test_reset_and_close_are_clean.py
├── test_normal_motion_does_not_overwrite_sim_state.py
├── test_translation_contains_no_capability_behavior.py
├── test_session_runner_exposes_no_public_robot_api_or_capability_semantics.py
├── test_evaluation_video_camera_is_harness_only_not_sdk_sensor_or_public_state.py
├── test_evaluation_video_covers_pre_invocation_through_terminal_sim_time.py
├── test_evaluation_video_continues_through_candidate_failure_exception_and_timeout.py
├── test_recorder_enabled_or_disabled_does_not_change_state_trace_control_order_or_evaluator_inputs.py
├── test_recording_camera_frames_robot_resource_and_scene_under_frozen_profile.py
├── test_recording_interval_drop_and_resource_limits_follow_frozen_profile.py
├── test_partial_media_is_sealed_and_retained_as_incomplete.py
├── test_sdk_translation_units_frames_and_freshness.py
├── test_timeout_cleanup_and_last_command_clear.py
└── test_concurrent_sessions_do_not_cross_talk.py

tests/integration/<soarm101_test_profile>/
├── test_full_six_part_readiness.py
├── test_arm_gripper_command_state_roundtrip.py
└── test_truth_adapter_against_mujoco.py

tests/integration/<go2_test_profile>/
├── test_full_six_part_readiness.py
├── test_low_level_command_state_roundtrip.py
├── test_timing_reset_and_transport_isolation.py
└── test_truth_adapter_base_joint_contact_state.py
```

The profile directory names are placeholders until exact IDs are frozen.

### 10.5 Generation, Blue Line, and Sandbox tests

```text
tests/generation/
├── test_stage1_bundle_visibility_and_hash.py
├── test_every_run_resolves_exactly_one_granularity_profile.py
├── test_first_campaign_accepts_only_the_frozen_g2_profile.py
├── test_first_campaign_rejects_g1_g3_and_multi_granularity_layers.py
├── test_generic_resolver_can_select_one_future_frozen_profile.py
├── test_stage1_requirement_coverage.py
├── test_stage1_unit_frame_affordance_conformance.py
├── test_stage1_forbids_implementation_and_private_content.py
├── test_stage1_correction_is_preseal_only.py
├── test_stage1_and_stage2_share_producer_identity_and_scaffold_but_use_fresh_contexts.py
├── test_python_binding_and_skeleton_are_deterministic.py
├── test_stage2_bundle_excludes_tasks.py
├── test_implementation_manifest_is_framework_derived.py
├── test_stage2_30_call_ceiling_accounting.py
├── test_sandbox_is_llm_invoked_and_public_probe_only.py
├── test_sandbox_uses_same_readiness_passed_rim_and_real_sdk_route.py
├── test_public_state_profile_shared_by_sandbox_validation_and_demo_without_sdk_injection.py
└── test_sandbox_feedback_is_non_verdict_and_redacted.py

tests/blue_line/
├── test_reference_library_is_private_reference_not_task_library.py
├── test_frozen_snapshot_not_live_markdown_governs.py
├── test_all_capabilities_receive_false_pass_analysis_and_cases.py
├── test_physical_effect_requires_trusted_physical_state.py
├── test_receipt_and_self_report_cannot_be_sole_truth.py
├── test_wrong_entity_unit_frame_stale_and_transient_guards.py
├── test_copied_adapted_proposed_lineage.py
├── test_material_adaptation_requires_review.py
├── test_robot_config_effect_and_protocol_scope_applicability.py
├── test_reference_cannot_add_design_external_api_or_obligation.py
├── test_unreviewed_proposed_or_material_adapted_never_ready.py
├── test_approved_proposal_creates_new_version_snapshot_and_run_only.py
├── test_checker_does_not_invent_or_change_threshold.py
├── test_blue_line_three_call_ceiling.py
├── test_ready_seals_spec_suite_manifest_before_stage2.py
├── test_needs_review_has_no_formal_suite_or_candidate.py
└── test_compiler_is_byte_reproducible.py
```

### 10.6 Validation and Repair tests

```text
tests/validation/
├── test_validation_a_file_hash_signature_binding.py
├── test_validation_a_dependency_import_isolation.py
├── test_validation_a_failure_creates_no_overlay_and_never_runs_b.py
├── test_validation_a_precedes_overlay_and_b.py
├── test_overlay_references_manifest_and_same_suite.py
├── test_overlay_copies_no_private_values.py
├── test_validation_b_uses_real_sdk_route.py
├── test_validation_b_harness_owns_verdict.py
├── test_every_validation_b_case_and_repetition_has_video.py
├── test_repair_revalidation_records_new_video_for_every_case_and_repetition.py
├── test_missing_corrupt_or_incomplete_validation_video_is_infrastructure_not_candidate_failure.py
├── test_validation_video_failure_emits_no_candidate_diagnostic_and_consumes_no_repair.py
├── test_validation_video_failure_reruns_same_revision_suite_case_and_repetition.py
├── test_validation_infrastructure_rerun_uses_new_attempt_and_never_overwrites_failed_artifacts.py
├── test_validation_attempt_scopes_public_trace_private_failure_evidence_and_video_manifest.py
├── test_invalid_video_capture_excluded_and_valid_rerun_counts_at_same_repair_index.py
├── test_video_never_overrides_structured_validation_verdict.py
├── test_false_success_receipt_status_and_stale_state_fail.py
├── test_candidate_and_infrastructure_failure_are_distinct.py
├── test_first_complete_pass_is_promoted.py
└── test_validated_and_frozen_are_distinct_states.py

tests/repair/
├── test_initial_candidate_submission_is_not_a_repair_invocation.py
├── test_pass0_is_initial_complete_a_plus_b_outcome_before_diagnostic.py
├── test_maximum_ten_repair_invocations.py
├── test_repair_changes_only_capability_py.py
├── test_every_revision_has_hash_parent_manifest_overlay.py
├── test_every_revision_repeats_a_and_same_b_suite.py
├── test_continued_and_fresh_modes_have_same_authority.py
├── test_fresh_context_has_no_provider_history.py
├── test_continued_context_contains_only_authorized_history.py
├── test_repair_modes_share_diagnostic_budget_and_visibility.py
├── test_stage2_and_repair_budgets_are_independent.py
├── test_infrastructure_failure_consumes_no_attempt.py
├── test_design_gap_terminates_without_stage1_reopen.py
├── test_no_retrospective_best_revision_selection.py
└── test_pass0_passk_gain_and_conditional_metrics.py
```

### 10.7 Consumer, Demo, Evolution, cross-robot, and end-to-end tests

```text
tests/consumer/
├── test_router_is_semantic_and_robot_independent.py
├── test_router_adds_no_planning_control_or_recovery.py
├── test_react_tools_derive_from_frozen_router_view.py
├── test_react_sees_description_and_public_state_only.py
├── test_public_state_exact_frame_unit_noise_latency_and_cadence.py
├── test_public_state_trace_matches_every_value_in_each_authorized_channel.py
├── test_public_state_traces_never_cross_sandbox_validation_demo_channels.py
├── test_react_budget_termination_and_invalid_calls.py
└── test_future_adapter_protocol_is_not_react_specific.py

tests/demo/
├── test_only_frozen_validated_layer_enters.py
├── test_demo_uses_same_readiness_passed_rim_and_real_sdk_application_route.py
├── test_all_fixed_tasks_execute_no_runtime_selection.py
├── test_consumer_self_report_is_not_verdict.py
├── test_private_harness_uses_authored_task_criterion.py
├── test_every_demo_task_trial_and_repetition_has_video.py
├── test_missing_corrupt_or_incomplete_demo_video_invalidates_trial_as_infrastructure.py
├── test_demo_video_continues_through_consumer_failure_exception_and_timeout.py
├── test_demo_video_failure_reruns_same_consumer_layer_task_trial_repetition_and_protocol.py
├── test_demo_infrastructure_rerun_uses_new_attempt_and_never_overwrites_failed_artifacts.py
├── test_invalid_demo_video_is_outside_denominator_and_only_valid_rerun_counts.py
├── test_video_never_overrides_authored_demo_criterion_or_verdict.py
├── test_public_and_private_task_views_are_disjoint.py
├── test_demo_metrics_and_failure_attribution.py
└── test_reused_tasks_are_marked_regression.py

tests/evolution/
├── test_evolution_starts_after_run_close.py
├── test_single_run_creates_candidate_not_admission.py
├── test_no_current_run_feedback_to_repair_or_standards.py
├── test_private_fields_and_task_solutions_are_declassified.py
├── test_conflicts_quarantine_not_average.py
├── test_review_events_are_append_only_and_event_ids_cannot_be_rewritten.py
├── test_admitted_versions_are_immutable.py
└── test_future_snapshot_recipient_and_applicability.py

tests/cross_robot/
├── test_same_generic_orchestrator_resolves_both_rims.py
├── test_first_two_robot_runs_pin_same_exact_g2_profile_and_no_g1_or_g3.py
├── test_profile_resolver_is_generic_despite_g2_only_first_campaign.py
├── test_generic_source_has_no_robot_ids_or_joint_names.py
├── test_design_router_runner_are_arm_quadruped_neutral.py
├── test_robot_scoped_plugins_do_not_import_generation.py
├── test_no_assumed_gripper_end_effector_or_fixed_base.py
└── test_third_robot_onboarding_core_diff_gate_fixture.py

tests/end_to_end/
├── test_soarm101_g2_full_ready_route.py
├── test_go2_g2_full_ready_route.py
├── test_react_sees_only_current_run_g2_layer.py
├── test_needs_review_stops_before_stage2.py
├── test_repair_route_with_same_suite.py
├── test_repair_exhaustion_route.py
├── test_infrastructure_failure_route.py
├── test_react_demo_private_verdict_route.py
├── test_run_close_to_future_experience_route.py
├── test_every_terminal_path_closes_and_closed_run_rejects_late_writes.py
├── test_clean_environment_reproducibility.py
└── test_all_artifact_seals_and_visibility_claims.py
```

### 10.8 Fixture tree

```text
tests/fixtures/
├── schemas/                                           # synthetic only
├── morphology/
├── sdk_mocks/                                        # explicitly forbidden in formal loader
├── translation_mocks/
├── model_responses/
├── capability_designs/
├── candidates/
│   ├── valid/
│   ├── binding_violations/
│   ├── isolation_violations/
│   └── physical_false_passes/
├── blue_line_references/
├── validation_suites/
├── demo_tasks/
├── failure_injections/
└── provenance/
    └── TEST_FIXTURE_ONLY.yaml
```

Formal loaders reject `TEST_FIXTURE_ONLY` provenance even if a fixture is copied outside this
directory. The rule is enforced by artifact metadata, not merely by path.

## 11. Developer tools and commands

The `tools/` scripts are thin offline/maintainer wrappers around Framework modules and cannot
bypass admission or run gates.

| File | Intended command/use |
|---|---|
| `tools/verify_repository.py` | Check frozen contracts, source hashes, licenses, catalogs, forbidden files, and traceability |
| `tools/capture_source.py` | Capture a pinned local/web/AutoAdapter 1.0 source with hash/license/locator; never auto-admit |
| `tools/rebuild_catalogs.py` | Regenerate Morphology/SDK/Tasks/Experience/private measurement indices from manifests |
| `tools/validate_contracts.py` | Validate frozen schemas/profiles/policies and prove proposal paths are rejected |
| `tools/audit_run.py` | Verify runtime seals, parents, event ordering, budgets, visibility, and formal-route evidence |

Proposed installed CLI shape after contracts freeze:

```text
aa2 library verify
aa2 library rebuild-catalogs
aa2 integration resolve --rim <ref>
aa2 integration readiness --rim <ref>
aa2 run prepare --config <frozen-config>
aa2 run execute <run-id>
aa2 run inspect <run-id>
aa2 run audit <run-id>
aa2 report run <run-id>
aa2 onboard check <robot-onboarding-record>
```

There is no `--skip-blue-line`, `--skip-validation`, `--trust-candidate-success`, `--use-mock-as-formal`,
or `--ignore-readiness` command.

## 12. File-creation sequence and gates

No wave starts merely because this design names its files.

| Wave | Prerequisite | Files/modules created | Exit evidence |
|---|---|---|---|
| 0 — design only | current user request | this design document and revisions only | user confirms system/file understanding |
| 1 — normative freeze | user approves design choices | Authority updates plus reviewed frozen schemas/profiles/policies/prompts/config contracts; proposal drafts remain outside production paths | each implementation item maps to `FROZEN` content |
| 2 — foundation/contracts | Wave 1 | package/build files, foundation, schema/profile/policy loaders, artifact store interface, traceability, contract/security tests | byte reproducibility, proposal rejection, seals/visibility/state gates pass |
| 3 — governed data | relevant Library contracts frozen | Morphology/SDK exact Authority layouts, Tasks/Experience machine records, private standards/measurement records, catalogs and admission tooling | both robot source/license/hash/admission closures; task/criterion views pass |
| 4 — robot integration | RIM/Translation/Readiness contracts frozen | RIM records, two Translation packages, runtime profiles, readiness probes, Generation-owned development probes, private Harness adapters, Session Runner | six readiness checks pass independently for both real-SDK routes |
| 5 — Stage 1 + Blue Line | Generation/Blue contracts and exact G2 profile frozen | common Scaffold, generic profile resolver, Stage 1, private Blue Line, prompts/configs, deterministic checking/compilation | each robot has one conformed sealed G2 Design and at least one Blue `READY`; no first-campaign G1/G3 artifact exists; negative cases reach `NEEDS_REVIEW` |
| 6 — Stage 2 + Validation + Repair | binding/Sandbox/Validation/Repair and recording contracts frozen | Binding/skeleton, Stage 2/Sandbox, candidate worker, A/B, overlay, private formal-trial video recorder, Repair, promotion | pass@0 and controlled failure/Repair/exhaustion/infrastructure/video-evidence branches verified |
| 7 — ReAct + Demo + Evolution | Router/ReAct/Demo/Evolution contracts and fixed task collection frozen | Router, ReAct adapter, private Demo Harness, mandatory Demo-trial recording, metrics, closure, Evolution candidate/declassification/review mechanics | both robots complete the agreed G2-only downstream route with complete private recording manifests |
| 8 — migration/onboarding | two-robot audit passes | migration report and evidence-derived `ROBOT_ONBOARDING_GUIDE.md` | guide is subsequently validated by one additional robot |

Within a wave, tests for a contract/module are written with or before implementation. A robot mock
may unblock unit tests but cannot satisfy a Wave 4 or end-to-end exit gate.

## 13. Decisions requested from the user

The following table distinguishes a recommendation from a silently frozen decision.

| Topic | Recommendation in this design | Why / consequence |
|---|---|---|
| Implementation root | Keep one independent `general_demo/` root and migrate it wholesale after audit | Directly satisfies General Demo isolation and avoids polluting root authority documents |
| Python package | Use one generic `autoadapter2` package; robot integration is installed/discovered from versioned manifests | Prevents two robot-specific Framework copies |
| Governed source formats | Human-reviewed YAML; runtime JSON; traces JSONL; canonical bytes always explicit | Readable admission plus deterministic execution/audit |
| Proposed contracts | Draft only under `design_proposals/<OQ-ID>/`; production loader rejects them | Enforces “implement only frozen contracts” mechanically |
| Runtime data | Repository-external local content-addressed filesystem backend first, behind an artifact-store interface | Simple to debug/migrate without making source paths a security boundary |
| Isolation | Separate recipient workspaces and processes/containers; never mount repo/artifact-root parents | Directory names alone cannot protect private criteria or suites |
| Integration discovery | Exact RIM/Translation manifests and package refs; no robot-name branches or SDK universal wrapper | Third robot should add records/packages, not core conditionals |
| Formal shim meaning | Only real SDK + device/transport hook + Translation; facades fixture-only | Required by `REQ-GD-004/013/014` |
| Two-robot migration gate | Require one complete `READY` downstream run for each robot | Stronger than Authority minimum but necessary to call it a two-robot architectural Demo |
| First granularity | Authority selects the eventually frozen G2 profile as the only profile in both first complete robot runs; implement profile selection generically | Exercises low-level SDK to reusable capability synthesis without claiming the final RQ2 experiment or deleting future G1/G3 support |
| Demo task collection | Recommended: one fixed five-task collection per fixed robot configuration, every record with private authored criterion | Both robot routes can be exercised meaningfully; requires changing/fixing the Authority's currently singular/open wording |
| Demo Consumer | ReAct only for the first Demo; freeze one model/prompt/tool/budget; reserve a generic adapter protocol | Meets the user decision without fake policy/program results |
| Blue Line source | Root human Validation reference -> reviewed machine records -> comparison snapshot -> LLM spec -> deterministic suite | Preserves human reference role without live Markdown or a fifth Library |
| Evaluation Video | One generic Harness-side recorder plus a frozen evaluation-only recording profile; media bytes live only in the external content-addressed store | Gives complete private audit evidence without creating a robot sensor, model input, or second verdict path |
| Go2 capability boundary | Admit the real lower-level SDK actions/observations that are actually supported; synthesize stand/sit/move in `capability.py`, not Translation | Tests AutoAdapter rather than relying on convenient high-level SDK services |
| Formal execution environments | Pin Framework and integration runtime image/toolchain digests separately from SDK Entry pins | Accommodates SDK platform differences without forking the lifecycle |
| Post-Demo expansion | Add one robot at a time and apply a core-diff gate; core changes trigger Authority update and rerun of both originals | Turns onboarding into a validation of architectural generality |

### 13.1 Exact items still requiring Authority freeze before their files are loadable

- campaign/single-RIM run-profile schemas, governed run state machine/gates, artifact-store
  immutability/permissions, full-run closure, and structured logging contracts beyond the frozen
  Wave 3/4 integration registry and gate;
- State-Provided structured observation schema, projector/binding semantics, cadence, noise/latency,
  and Sandbox/Validation/Demo recipient rules;
- task machine records, the two-robot interpretation of the fixed five-task requirement, and
  executable criterion/Harness contracts;
- Stage 1/Stage 2 Bundles, Design schema/checker, Binding/skeleton/Manifest, prompts, and provider
  accounting;
- Blue Line fixed model/prompt/config, standards/measurement/policy schemas, checker/compiler,
  cases/trials, review/versioning, privacy, and seals;
- Validation A/B checks, private Harness adapter contracts, failure taxonomy, aggregation, reset and repetition;
- Evaluation Video recording-profile and manifest schemas, camera/view/framing, renderer,
  frame rate/resolution/codec/container/encoder pins, pre/post-roll and simulation-time alignment,
  dropped-frame/interval completeness, capture overhead and resource limits, infrastructure rerun,
  private retention, and redaction/declassification mechanics;
- Sandbox/Repair diagnostics, redaction, budgets, ledger and termination;
- Router/IPC/worker lifecycle and errors;
- ReAct model, prompt, tool encoding, memory/context, budget, termination and reporting;
- Demo reset/trials/repetitions/evidence/metrics/aggregation; and
- Evolution record/review/admission/declassification/retrieval/conflict/invalidation contracts.

## 14. Architecture review outcome

The repository architecture remains the approved construction map. Authority revision 0.14.0 now
authorizes implementation of the frozen Wave 3/4 two-robot integration subset: exact Morphology,
SDK/runtime, RIM, Translation, admission/activation registries, Readiness and integration gate.
That authorization does not extend to any `OPEN` later-wave contract. Work outside the frozen
subset still requires its owning Authority decision and requirement to be frozen first.

Rejection or modification should identify the section/path and the intended ownership or data-flow
change. The directory/file catalog will be revised before code for the affected open contract is
written.
