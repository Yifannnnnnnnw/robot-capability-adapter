# AutoAdapter-Bench Protocol

> **Status:** reusable benchmark definition; not an experiment authority<br>
> **Authority baseline:** `AA2-AUTH` revision `0.19.27`<br>
> **Prepared:** 2026-08-21<br>
> **Runtime boundary:** canonical `autoadapter/` Direct-MuJoCo mainline

## 1. Benchmark role

AutoAdapter-Bench defines reusable scientific and recording contracts for two
tracks. A concrete experiment chooses a prospective cohort, factors,
replicates, and stopping rule in its own delegated authority and manifest.
Nothing in this benchmark document creates a current experiment denominator.

| Track | Statistical unit | Compared factor | Primary result family |
|---|---|---|---|
| B1: Driver Synthesis | One independent generation-condition replicate | Producer backbone and generation condition | Complete private driver-validation outcome within the attempt budget |
| B2: Capability-Interface Use | One physical controller episode | Backbone within one admitted controller architecture | Complete task physical verdict from the trusted Harness |

B1 and B2 have different units, inputs, stage boundaries, and outcomes. They
are never merged into one aggregate score.

## 2. Reusable inventories and registries

The benchmark may catalogue more robots, backbones, task sets, or controller
architectures than one experiment selects. Inventory membership, canonical
package availability, runnable-index admission, and experiment membership are
separate facts.

- `components/robot_sets/all-14.json` is a reusable inventory of canonical and
  research configuration IDs; it is not a formal cohort.
- `components/backbone_sets/declared-seven.json` identifies the planned seven
  Producer families. `backbones/registry.json` owns proposed exact provider
  identifiers, transports, settings, context/output limits, and price
  snapshots. A concrete experiment must freeze the applicable values before
  formal calls.
- `high_level_controllers/` catalogues B2 architectures and source audits.
  Catalogue presence does not select a controller for an experiment or prove
  that its adapter is admitted.

Credentials remain environment-only. A manifest may reference reusable IDs but
must not replace a missing or unavailable item after outcomes are inspected.

## 3. B1 reusable contract

### 3.1 Fixed-input boundary

B1 consumes one versioned, prior-designed fixed Driver-and-criteria definition
per selected robot. It contains the public capability interface,
capability-level pass standards, and complete private validation suite. It
remains identical across every selected
backbone, generation condition, replicate, and Repair attempt for that robot.

The two supported conditions are `skeleton-assisted` and `from-scratch`. A
concrete experiment declares which eligible robots run each condition. Paired
conditions share the fixed bundle and declared model/environment settings but
isolate model sessions, workspaces, candidates, reports, Repair histories, and
generated drivers.

### 3.2 Stage and attempt boundary

B1 executes only:

```text
fixed bundle -> STUDY -> GENERATE or GEN_ALGO
             -> complete private validation
             -> on failure, bounded Repair and complete revalidation
             -> terminal driver outcome
```

B1 does not run TGCD, IVC, Task Demo, a high-level controller, or controller
task-success evaluation. A concrete experiment separately declares whether
Evolution is outside its stopping boundary. Attempt 0 is the first submitted driver; attempts 1 and 2
are bounded Repairs. Development turns, probes, capabilities, suite cases,
repetitions, videos, and Repair attempts are not independent experimental
units.

### 3.3 Result families

Reusable B1 outcomes are `pass@0`, final pass within the declared attempt
budget, valid-driver count/rate, Repair gain, attempts to first pass, cell
completion, failure class, per-capability diagnostics, model calls, provider
token categories, cost, and stage/total wall time.

## 4. B2 reusable contract

B2 fixes one validated driver, capability interface, and audited adapter per
selected robot before controller outcomes are inspected. Backbone comparison is
valid only within a controller whose declared model role is replaceable without
retraining or changing method logic. Each concrete experiment declares the
admitted controller, robot/backbone eligibility, task set, private seed policy,
episodes, fixed driver/interface identities, and denominator.

The primary B2 result is complete-task physical success decided by the trusted
Harness. Controller completion, plan/code/BT validity, capability selection and
argument binding, capability-chain completion, resource use, and failure class
remain separate fields.

## 5. Composition and execution boundary

An external experiment manifest may reference one locked benchmark protocol
plus reusable inventories or component sets. It may narrow a selection, but it
cannot override the trusted Harness, public pass standards, candidate
isolation, private input policy, attempt accounting, evidence requirements, or
required recording fields.

The generic resolver checks paths, IDs, declared counts, package/index state,
and visible blockers. Structural resolution does not admit an experiment or
invoke a model or MuJoCo. Formal runners call the canonical mainline rather
than copying robot packages or implementing a second Harness.

Provider, package, recorder, or infrastructure failures remain explicit. A
blocked or unavailable unit is not silently removed or converted into model
failure after outcomes are inspected.

## 6. Required recording contract

Every unit records, as applicable:

- run, unit, protocol, manifest, code, robot, backbone, condition/controller,
  replicate, task, seed, episode, and paired-block identities;
- vendor, exact model ID/revision, transport, endpoint/region, inference
  settings, context/output limit, timeout, provider request ID, retry count,
  and provider error;
- UTC start/end, monotonic total and stage wall times, queue/active time when
  concurrent, and model/probe/Harness time where separable;
- provider-reported input, output, cache-read, cache-write, reasoning, and
  other token categories, preserving unavailable values as null;
- dated price snapshot, currency, unit prices, per-call cost, and unit total;
- model-turn/action/tool/submission/stage-transition evidence without hidden
  chain-of-thought; and
- candidate, report, trace, video, and trusted-evidence paths relative to the
  retained experiment run.

B1 additionally records fixed-bundle identities, attempt index, source/import
audit, public smoke, complete private verdict structure, Repair transition,
terminal verdict, no-valid-submission reason, and failure class. B2 additionally
records controller/driver/interface versions, capability calls and arguments,
clause verdicts, and the final physical task verdict.

API keys, credentials, private suite definitions, and candidate-inaccessible
Harness state are never written to public model traces.

## 7. Analysis and readiness

An experiment authority fixes its primary outcomes, denominators, weighting,
failed/blocked-unit treatment, extension/stopping rule, and inferential scope
before inspecting outcomes. Experiment-specific raw data and analysis stay
with that experiment, not in this benchmark directory.

A concrete experiment authority defines its own sole start prerequisite. The
reusable benchmark requires clear Harness-evaluable Driver criteria and a
working isolated, recorded stage boundary; it does not impose a task-blind
reference-calibration gate. Provider settings are fixed before dispatching the
affected cells. The presence of benchmark files or a structurally valid
manifest is never execution evidence by itself.
