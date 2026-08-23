# Experiment 2 protocol — SO-101 cross-run closure

This protocol implements `EXPERIMENT_2_AUTHORITY.md`. The authority controls
the design; this file is an execution checklist and record contract. It does
not create an additional status or promotion workflow.

## Declared closure

The closure has exactly two run roles:

1. `exp2-so101-source`: a real Sonnet 4.6, skeleton-assisted SO-101 run with
   empty Experience. It starts at TGCD, performs implementation-blind IVC,
   runs STUDY and GENERATE, submits at most three drivers through trusted
   capability validation and bounded Repair, and records the Task Demo stage.
   An admitted final driver executes the five-task Task Demo; if no driver is
   admitted, the stage records `not-run` with a nonempty reason. Only after
   that terminal stage record does one Opus 5 Evolution call run.
2. `exp2-so101-later`: one fresh, manually launched Sonnet 4.6,
   skeleton-assisted run. It can consume the accepted source proposal exactly
   as public Experience and runs its own TGCD/IVC, synthesis, validation, and
   Task Demo path. It does not invoke a second Evolution call.

The source and later run use the exact model settings in `manifest.json`:

| Role | Model ID | Temperature | Context | Max output |
|---|---|---:|---:|---:|
| Producer and later-run model-authored stages | `eu.anthropic.claude-sonnet-4-6` | 0.0 | 1,000,000 | 16,384 |
| Source terminal Evolution only | `eu.anthropic.claude-opus-5` | 0.0 | 1,000,000 | 32,768 |

No other model, local weight, or silent provider substitution is permitted. The
Opus 5 identifier and inference limits are pinned in the manifest and must be
callable before the source request; an unavailable exact pin is an
infrastructure blocker.

## Source run sequence

The operator records a fresh run ID, package snapshot, manifest revision,
provider identity, and empty Experience input before the first model request.
The Framework then executes the real path:

```text
public SO-101 package and >=20 sourced tasks
        -> Sonnet TGCD
        -> implementation-blind IVC
        -> Sonnet STUDY -> skeleton-assisted GENERATE
        -> trusted capability validation / bounded Repair (<=3 submissions)
        -> Task Demo stage (five sampled tasks, or truthful not-run)
        -> bounded terminal report projection
        -> one Opus terminal Evolution proposal
```

TGCD receives no pre-authored capability catalogue or task-to-capability
mapping. IVC cannot inspect the candidate, source trace, Repair history, or
validation result, and the Harness retains verdict authority. Task Demo has its
own verdict and video set; it cannot trigger same-run Repair. If the source
driver is not admitted, the source run must record a truthful `not-run` outcome
with a nonempty reason rather than inventing a Task Demo result. Terminal
Evolution may still produce a public, evidence-labelled negative proposal from
that failure or not-run outcome.

Evolution is terminal and non-blocking. It reads only the bounded public
projection permitted by the parent Authority. Its single response is either a
candidate Experience proposal or an explicit no-reusable-lesson/failure
record. It cannot change the source driver, suite, criterion, Repair decision,
Task Demo verdict, or source inputs.

## Proposal and human review

The proposal record must contain, at minimum:

- a proposal ID, source run ID, robot/package snapshot, and terminal-stage
  lineage;
- the model-written fields `observation`, `lesson`, `recommendation`, `scope`,
  and `evidence` (a public-only list);
- Framework-assigned `source_robot`, `generation_condition`, and exactly one
  `terminal_outcome_label` of `positive` or `negative`, derived from retained
  source-run verdict facts;
- public evidence that points to retained source-run facts (for example,
  terminal verdict, failure class, or Task Demo-stage result); and
- no reference-driver content, private suite definitions, hidden thresholds,
  credentials, or raw private paths.

The reviewer receives the Framework-labelled proposal and public evidence and writes a
separate disposition record. The disposition is exactly `accept` or `reject`,
with a nonempty reason. The reviewer may not edit the proposal, change its
model-written fields, Framework-assigned labels, select a new lesson, or merge
it with another record. The proposal,
frozen reviewed snapshot, and review are ordinary records; no hash, registry,
or lifecycle machinery is needed.

An accepted negative proposal remains a valid negative Experience record. A
rejected proposal is not eligible for the Experience-enabled later run. If the
operator cannot obtain an accepted proposal, the operator may retain a
diagnostic later run with empty Experience, but it must be labelled as not
meeting the formal closure and cannot be counted as an Experience-enabled
result.

## Later run and isolation

After the disposition is fixed, the operator manually launches exactly one
fresh later run. For an accepted proposal, the Framework freezes the reviewed
snapshot, loads the exact unedited public proposal, and records:

- proposal ID and source-run lineage;
- the fixed human disposition;
- Framework-assigned positive/negative terminal-outcome label and public
  evidence; and
- the timestamp and run event at which the Experience was loaded.

The later run has a fresh workspace, model conversation, candidate worker,
canonical MuJoCo session, reset sequence, TGCD/IVC output, private validation
suite, and evidence package. It may receive only the eligible public
Experience projection at the authorised model-facing boundary. It cannot read
the source candidate, source private suite, source Harness configuration,
source worker, provider credential, or reference driver.

No source proposal is available to the source run itself, and no record is
made available to another same-round cell. An accepted proposal becomes
globally eligible after its fixed human disposition and frozen reviewed
snapshot. It becomes effective only when the next independent run explicitly
loads that unchanged snapshot. A rejected proposal remains evidence but is not
eligible. This is a simple eligibility rule for the declared closure, not a
general Experience registry.

## Evidence and analysis

Retain one concise record for each run role plus the proposal, disposition, and
load records. At minimum, record:

- exact model ID, endpoint/provider pin, temperature, context and output
  limits, request IDs, token categories, cost inputs, and wall time;
- stage traces for TGCD, IVC, STUDY, GENERATE, private validation, Repair,
  Task Demo, and source Evolution;
- submitted driver attempts, initial/final capability verdicts, independent
  task-metric and physical-integrity verdicts, and terminal failure class;
- every required capability-validation and Task Demo video and its manifest
  entry;
- model-written proposal fields, Framework-assigned terminal-outcome label,
  public evidence, source lineage, disposition, and later-run load outcome; and
- manual-launch event and run/workspace identifiers proving independence.

The denominator is two declared run roles, not a cell matrix. Report source
and later outcomes separately. This protocol has no no-Experience control and
therefore cannot estimate an Experience effect or improvement. A later pass,
failure, or changed failure pattern is a mechanism observation only; it must
not be described as caused by Experience.

## Minimum focused checks

Before formal dispatch, run the smallest checks that establish:

1. the canonical SO-101 package and source-backed task snapshot load;
2. Sonnet and Opus return the pinned identities with the manifest settings;
3. the skeleton-assisted source route reaches real MuJoCo and trusted Harness
   terminal evidence in a diagnostic canary;
4. the terminal Evolution response schema yields a public positive/negative
   proposal without private fields; and
5. a manually launched independent run records Experience load or, after a
   rejection, visibly records that the formal Experience-enabled closure is
   blocked.

These checks are diagnostic and do not enter the two-run denominator. Provider,
package, Harness, isolation, or video failures remain infrastructure blockers.
