# Experiment 2 — SO-101 Cross-Run Experience Closure Authority

> **Document ID:** `AA2-EXP2`<br>
> **Document role:** sole normative document for Experiment 2<br>
> **Parent authority:** `AA2-AUTH` revision `0.19.32`<br>
> **Normative language:** English<br>
> **Chinese text:** auxiliary reading support only<br>
> **Revision:** `0.1.1`<br>
> **Effective date:** 2026-08-23<br>
> **Design status:** active and prospectively fixed; ready for formal dispatch after final preflight

Revision `0.1.1` changes no robot, model role, run, attempt budget, review
boundary, or claim. It fixes the ordinary manifest/protocol revision pins and
authorises dispatch only while the final focused preflight confirms the
already-declared readiness evidence.

## 0. Authority, scope, and precedence

This is the only normative source for Experiment 2's SO-101 cross-run closure,
its two run roles, model assignments, human disposition rule, Experience
boundary, evidence claim, and execution blockers. It is delegated by
`AUTOADAPTER_2_AUTHORITY.md` Section 0.1. The parent Authority continues to
govern Direct-MuJoCo execution, candidate isolation, the trusted Harness,
physical integrity, video evidence, and authenticity. A conflict blocks this
experiment until it is prospectively corrected.

Experiment 2 is deliberately a small mechanism-closure experiment. It is not a
backbone comparison, a matched no-Experience control, a capability-interface-use
comparison, or an improvement test. Its formal unit is the declared two-run
closure: one source run and one later run. The later run is independent and is
launched manually after the source proposal has received its fixed human
disposition.

The manifest, protocol, source code, run records, analysis, and thesis prose
implement or report this design but cannot change it. A later revision may fix a
provider or infrastructure prerequisite before dispatch; it may not add a
robot, model role, run, control condition, replicate, or improvement claim after
outcomes are inspected.

## 1. Research object and claim boundary

The object is whether one reviewed terminal Experience proposal can complete
the prescribed public cross-run handoff from a real SO-101 source run into one
independent later Sonnet run. The source and later runs use the same declared
SO-101 package, Direct-MuJoCo route, skeleton-assisted generation condition,
model configuration, attempt budget, and evidence rules. They are separate run
directories and separate model conversations.

The source run must execute the real model-authored path through Task-Grounded
Capability Design (TGCD), implementation-blind Independent Validation Compiler
(IVC), STUDY, GENERATE, private capability validation, bounded Repair, and the
Task Demo stage. If a final driver is admitted, the Framework executes the
five-task Task Demo; if no driver is admitted, it records a truthful Task Demo
`not-run` outcome and reason. After that terminal Task Demo-stage outcome, the
terminal Evolution call is made by Opus 5. Opus is used only for this terminal
Evolution proposal; it is not a Producer, TGCD, IVC, Driver Synthesis, Repair,
or Task Demo model in this experiment.

The Opus proposal payload is limited to `observation`, `lesson`,
`recommendation`, `scope`, and `evidence`; the `evidence` list is public-only.
The Framework adds the source
robot, generation condition, and `positive` or `negative`
`terminal_outcome_label` from retained source-run verdict facts; neither Opus
nor the reviewer authors or edits those fields. The human reviewer records
exactly one disposition for the unedited, Framework-labelled proposal:
`accept` or `reject`, with a nonempty reason. The reviewer may not rewrite,
merge, delete, or add content. An accepted proposal is the only record that
may be supplied to the manually launched later run. If the proposal is
rejected, the rejection is itself a truthful closure outcome; a later run may
be launched only as a diagnostic no-Experience run and cannot be counted as
an Experience-enabled closure.

The formal Experiment 2 closure is complete only when an accepted, public,
evidence-labelled proposal is loaded unchanged by one manually launched,
independent later Sonnet run, or when the run is explicitly reported as blocked
by a rejection or infrastructure failure. An accepted proposal becomes globally
eligible after its fixed disposition and frozen reviewed snapshot; it becomes
effective only when the next independent run explicitly loads that unchanged
snapshot. No source-run cell consumes its own proposal, and no same-round or
same-process feedback is permitted.

Experiment 2 supports only these mechanism claims:

- the source run reached the declared real-model and Direct-MuJoCo stages and
  produced the recorded terminal evidence;
- Opus 5 produced a terminal Evolution proposal whose model-written fields are
  limited to observation, lesson, recommendation, scope, and a public-only
  evidence list;
- the Framework assigned the source robot, generation condition, and
  positive/negative terminal-outcome label from retained verdict facts;
- a human fixed an accept/reject disposition with a nonempty reason without
  editing the proposal; and
- when accepted, the exact public proposal was loaded as Experience by the
  next independent manually launched Sonnet run.

It does **not** support an improvement claim, a causal Experience effect, a
matched Experience/no-Experience comparison, autonomous self-improvement,
global model or robot superiority, or a claim beyond this SO-101 closure.

## 2. Fixed robot and model roles

Experiment 2 contains exactly one robot configuration:
`robotstudio_so101`. The canonical package, public Task Library snapshot,
trusted skeleton, private instances, Harness bindings, and video settings are
fixed for both run roles. Reference-driver source and calibration outputs are
Framework-only and never become model input or Experience.

The exact remotely hosted model configurations are:

| Role | Family | Exact model identifier | Temperature | Context limit | Max output |
|---|---|---|---:|---:|---:|
| Producer | Sonnet 4.6 | `eu.anthropic.claude-sonnet-4-6` | `0.0` | `1,000,000` tokens | `16,384` tokens |
| Terminal Evolution only | Opus 5 | `eu.anthropic.claude-opus-5` | `0.0` | `1,000,000` tokens | `32,768` tokens |

Sonnet is the only model used for TGCD, any model-authored IVC/criteria
design input, STUDY, GENERATE, Repair, and Task Demo controller calls in both
run roles. Opus is called only once for the source run's terminal Evolution
proposal after the terminal Task Demo-stage outcome. Provider identity, endpoint/region, transport,
timeouts, retry policy, price snapshot, token categories, and returned model
identity are pinned in `manifest.json` before dispatch. A failed or unpinned
provider is an infrastructure blocker, not a model result and not a reason to
substitute another model. The Opus configuration was pinned prospectively from
the company-provider configuration and its returned identifier was checked in a
non-formal connectivity canary. If either exact configuration is uncallable,
Experiment 2 remains blocked.

Both run roles use `skeleton-assisted` and start with a maximum of three
submitted driver attempts: attempt `0` plus no more than two bounded Repair
attempts. Development probes and rejected pre-submission checks do not consume
that budget. The same complete private capability-validation suite is used for
all attempts within one run; the later run has its own fresh run-local TGCD,
IVC, suite, and trace.

## 3. Run roles and sequence

### 3.1 Source run

The source run has an empty Experience input and is manually or explicitly
dispatched under the source-run manifest entry. It must execute:

1. real Sonnet TGCD from the public SO-101 morphology and at least twenty
   source-backed tasks, with no pre-authored capability catalogue or
   task-to-capability mapping;
2. implementation-blind IVC audit and compilation of the model-authored,
   source-grounded capability contracts and criteria;
3. Sonnet STUDY and skeleton-assisted GENERATE;
4. trusted Direct-MuJoCo capability validation and bounded Repair, with at most
   three submitted drivers; and
5. the Task Demo stage: execute the separately recorded five-task Task Demo
   using the final admitted driver, or retain a truthful `not-run` outcome and
   nonempty reason when no final driver is admitted.

The source run cannot produce the required Experiment 2 closure evidence if it
stops before recording the terminal Task Demo-stage outcome. Task Demo remains
a separate verdict (including `not-run`) and does not reopen synthesis or
trigger Repair. After that stage, the source terminal report is projected
through the parent Evolution boundary and sent to the exact Opus configuration
for one terminal proposal. Evolution is non-blocking with respect to the
already recorded driver and Task Demo-stage outcome, and may produce a
negative, evidence-labelled proposal after a driver failure or not-run outcome.
The closure record must retain either its proposal or its explicit failure.

### 3.2 Human disposition

The reviewer receives the Framework-labelled proposal and its public evidence,
not private IVC/Harness definitions or credentials. The reviewer writes one
fixed disposition, exactly `accept` or `reject`, and a nonempty reason. The
reviewer cannot edit the model-written content or change the Framework-assigned
source robot, generation condition, or positive/negative terminal-outcome
label. The proposal, frozen reviewed snapshot, and disposition are retained as
separate ordinary records.

An accepted proposal may carry either Framework-assigned `positive` or
`negative` terminal-outcome label.
A negative proposal is not a failure to be hidden: it is public
evidence-labelled Experience describing a source-run failure or limitation. A
rejected proposal is not eligible input;
the protocol must report that the requested Experience-enabled closure did not
complete rather than silently replacing it.

### 3.3 Later run

Only after the disposition is fixed may the operator manually launch one fresh
later run. For an accepted proposal, the later run uses the exact unedited
public proposal from the frozen reviewed snapshot as Experience input and
records the proposal ID, source-run lineage, disposition,
`terminal_outcome_label`, and explicit load event. It then executes the real
Sonnet skeleton-assisted route through
TGCD/IVC, STUDY, GENERATE, private validation, bounded Repair, and the Task
Demo stage under a fresh run directory and fresh canonical resets. It does not
run a second Evolution call for this closure. After a rejection, any later run
is diagnostic no-Experience execution and is not an Experience-enabled result.

The later run is independent in process, workspace, model conversation, and
MuJoCo session. It may read only the eligible public Experience projection; it
must not read the source candidate, source private suite, source Harness
configuration, source credentials, or source worker state. The Experience is
not injected into the source run and is not added after a later run begins.

## 4. Experience eligibility and global boundary

An Experience record is globally eligible for a later run only when all of the
following are true:

- it is derived from the terminal source projection, not from reference-driver
  material, private validation definitions, or credentials;
- it is public and evidence-labelled, including a Framework-assigned
  `positive` or `negative` terminal-outcome label and source-run evidence
  references;
- the human disposition is fixed as `accept` with a nonempty reason; and
- the public proposal, its evidence labels, and that disposition are frozen as
  one reviewed snapshot.

A rejected proposal remains retained evidence but is not globally eligible.
Eligibility is not effectiveness: an eligible accepted snapshot becomes
effective only when the next independent run explicitly loads it unchanged.

No record proposed during a run may be read by that same run, another
same-round cell, or a later run that began before the disposition and snapshot
freeze. The record remains evidence-labelled when reported, including when its
valence is negative.

This is a simple experiment boundary, not a lifecycle, promotion, registry,
hashing, or governance system. Human-readable run IDs, proposal IDs, paths, and
ordinary disposition fields are sufficient.

## 5. Required evidence and analysis

The source and later run records must separately retain:

- run role, run ID, robot/package snapshot, model/provider identity, condition,
  Experience input state, and manual-launch/operator event;
- TGCD, IVC, STUDY, GENERATE, capability-validation, Repair, and Task Demo
  traces and verdicts;
- submitted-driver attempt count and separate initial/final capability
  validation outcomes;
- Opus Evolution request/response identity and terminal proposal or failure;
- the proposal's model-written fields, Framework-assigned terminal-outcome
  label, public evidence, source-run lineage, exact disposition, and whether
  the unchanged record loaded in the later run;
- trusted Harness facts, independent task/physical verdicts, and continuous
  per-case videos; and
- model calls, token categories, cost inputs, and wall-time measurements.

The analysis denominator is the declared two-run closure (`source = 1`,
`later = 1` when accepted and launched). Report mechanism completion and
failure/blocker reasons per run. Do not compute `pass@k`, improvement, causal
effect, or a matched-control estimate from this design. A source pass and a
later pass are separate outcomes, not evidence that Experience caused a
difference.

## 6. Start blockers

Formal dispatch is authorised only while all of the following remain recorded
in the versioned manifest or its referenced evidence:

1. the canonical SO-101 package, twenty or more source-backed tasks, trusted
   skeleton, private Harness bindings, and video route pass focused checks;
2. the exact Sonnet and Opus provider identities and settings are callable and
   pinned before the source call;
3. source and later run workspaces, credential-free candidate workers, and
   independent canonical MuJoCo sessions are available;
4. focused runtime/path checks establish that the source runner cannot
   construct or call Opus before an executed Task Demo or truthful `not-run`
   terminal state, and the terminal Evolution transport can return the exact
   five-field public proposal contract; and
5. the manual disposition and later-run load records can be retained without
   exposing private validation material.

Focused canaries are diagnostic only. They do not enter the two-run closure or
create Experience. A package, transport, Harness, or video failure remains an
infrastructure blocker and is not relabelled as a model or Experience result.
