# Experiment 2 protocol — SO-101 primitive-v1 cross-run closure

> **Protocol revision:** `0.2.0`<br>
> **Effective date:** 2026-08-23

This protocol implements `EXPERIMENT_2_AUTHORITY.md`. It is a direct run
recipe, not a lifecycle or readiness state machine. It is prospective:
geometry and primitive-calibration evidence references remain manifest
placeholders until real evidence is produced.

## Fixed roles and pins

The formal role IDs are:

```text
source = exp2-so101-source-v3
later  = exp2-so101-later-v2  (only after accepted source snapshot)
```

The source is one fresh Sonnet 4.6 skeleton-assisted run with empty
Experience. The later is one manually launched, independent Sonnet 4.6
skeleton-assisted run using the exact accepted source snapshot only at TGCD,
STUDY, GENERATE, and Repair. The later has its own fresh TGCD, IVC,
candidate, canonical session, ReCAP trace, and Task Demo evidence.

Sonnet is `eu.anthropic.claude-sonnet-4-6`, temperature `0.0`, context limit
`1,000,000`, and maximum output `16,384`. Opus is
`eu.anthropic.claude-opus-5`, temperature `0.0`, context limit `1,000,000`,
and maximum output `32,768`; it is source-terminal Evolution only and has at
most one call. Provider identities, endpoint/region, transport, retry policy,
price snapshot, package revision, `primitive_family` grammar revision,
geometry-calibration reference, primitive-calibration reference, and all
Authority/manifest/protocol pins are recorded before the first formal model
request.

The `capability_protocol` is `primitive-v1`. Each run starts a fresh package
grammar projection and TGCD selects/names 3--10 primitives within that
grammar. `task_support` is many-to-many. A primitive is one single-call,
composable robot operation; it cannot branch on `task_id`, embed a complete
task, task criterion, task macro, scene/reset, or reference-driver logic. A
task macro is the ephemeral skill formed when ReCAP composes primitive calls
for the Task Demo.

IVC is implementation-blind primitive conformance only. It creates exactly one
hidden nominal and one hidden boundary case for every selected primitive
family. It does not compile or use the original Task Library task standards;
those standards are used only by the trusted Task Demo Harness.

## Run sequence

The source route is:

```text
empty Experience
  -> Sonnet TGCD (package-bound primitive_family grammar, 3–10 primitives)
  -> implementation-blind IVC (nominal + boundary per family)
  -> Sonnet STUDY
  -> skeleton-assisted GENERATE
  -> trusted primitive validation
  -> bounded Repair (attempt 0 + at most 2 submitted repairs)
  -> ReCAP Task Demo
  -> one terminal Opus Evolution call
```

The later route is the same through ReCAP Task Demo, with the accepted
snapshot exposed only to TGCD, STUDY, GENERATE, and Repair. The later never
exposes the snapshot to IVC, candidate/Harness, hidden-reference adjudication,
or ReCAP/Task Demo, and it makes no Evolution call.

When the final driver is not admitted, record a truthful Task Demo `not-run`
outcome with a nonempty reason. This is the terminal Task Demo-stage record;
it does not consume another driver attempt and does not trigger Repair.

Formal ReCAP has at most 16 planning turns and 12 primitive calls per Task
Demo task. A controller finish, log, returned observation, or self-report is
not a Harness verdict. ReCAP composes a run-local task macro from primitive
calls; no task macro is stored as a capability or fed to IVC.

## Driver-stage budget

For `C` selected primitives, each Generate or Repair stage may use at most
`2 × (C + 1) + 3` local calls: two complete current-revision import/all-
primitive bundles and no more than three discretionary public probes. With the
3--10 primitive bound, the manifest's ceiling is 25 calls. These development
calls do not increase the three submitted-driver attempts (`0`, `1`, `2`).
Candidate methods use the ordinary mapping request ABI; they do not use
`request.field` attribute dispatch.

## Exact-reference adjudication and failure handling

After any Task Demo task failure, run exact hidden-reference adjudication with
the same package revision, task, seed, geometry, and Harness route.

```text
exact reference passes  -> candidate/task failure
exact reference fails   -> run infrastructure failure
```

If the reference fails, is unavailable, or is incomplete, do not rerun the
candidate, substitute another reference, add a task, or alter a denominator.
For the source, an infrastructure failure suppresses the Opus Evolution call,
human review, and Experience snapshot. A candidate failure backed by a
reference pass may reach terminal Evolution as a negative, public proposal.
The same rule applies to a later run; it remains an infrastructure outcome.

Package, geometry, primitive-calibration, provider, worker-isolation, Harness,
video, transport, IVC, or reference failures are infrastructure failures and
are not relabelled as model failures.

## Source Evolution contract

Evolution occurs only after a terminal Task Demo verdict or truthful `not-run`
record and only if the source run has no infrastructure failure. Opus may write
only these JSON-root fields:

```json
{
  "observation": "...",
  "lesson": "...",
  "recommendation": "...",
  "scope": "...",
  "public_evidence": []
}
```

The Framework appends root `provenance` and `outcome` from retained source
facts. Opus may not write, edit, or choose those fields, private IVC/Harness
content, credentials, reference implementation details, or a task macro.
Evolution transport failure or an explicit no-reusable-lesson result is
retained, but produces no human-review queue.

The reviewer receives the unchanged Framework-labelled object and its public
evidence. The only human-written fields are `disposition` (`accept` or
`reject`) and a nonempty `reason`; content editing and label editing are
forbidden. Only `accept` freezes a snapshot eligible for the independent
later run. Rejection blocks the formal later role.

## Legacy exclusion

`exp2-so101-source-v2`, the former later run, and any accepted Experience
snapshot derived from that macro-level route remain retained legacy diagnostics.
They cannot be primitive-v1 evidence and cannot be loaded by either new run.

## Evidence and denominator

Retain, separately for each role, the exact revision pins; package grammar and
calibration evidence references; TGCD primitive names/families and many-to-many
`task_support`; IVC nominal/boundary cases; STUDY/GENERATE/Repair traces;
attempt count; ReCAP planning/call counts and task macro trace; Task Demo
verdicts and videos; exact-reference adjudications; source Evolution fields
and Framework additions; disposition/reason; and the later snapshot load
event. Report `source = 1` and `later = 1` only when an accepted snapshot is
actually loaded by the independent later run. Do not compute improvement,
causal Experience effect, or a matched-control estimate.

Focused checks are diagnostic and do not enter this closure denominator. They
must establish the primitive-only IVC boundary, grammar-bound 3--10 design,
16/12 ReCAP budget, exact-reference rule, Evolution schema, four-stage later
Experience projection, legacy snapshot rejection, and Evolution suppression on
infrastructure failure. Placeholder calibration references are not passed
evidence and cannot satisfy formal readiness.
