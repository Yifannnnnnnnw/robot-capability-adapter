# SO-ARM101 minimal Evolution design

## Purpose

Evolution converts one terminal experiment into one evidence-grounded lesson
for a later Generation run. It is a global observer, not an autonomous
framework maintainer.

```text
frozen source run
  -> deterministic Evidence Compiler
  -> independent read-only Audit Agent
  -> one ranked finding + one Experience claim
  -> deterministic trusted evaluator
  -> accepted: append one Experience version at a time
     rejected/inconclusive: append nothing
  -> a later Generation run loads the sanitized approved view
```

No step edits source, prompts, schemas, validation thresholds, frozen inputs,
or historical evidence. A recommended change is a future experimental
hypothesis, not a patch applied by Evolution.

## Why this architecture

The overall architecture is **Evidence-grounded
Audit–Synthesize–Judge–Publish**. The Audit Agent uses a small ReAct loop only
as its inspection mechanism: it adaptively chooses which safe evidence and
public files to read. ReAct is not the authority and does not define the whole
system.

The design combines useful parts of several agent patterns without importing
their unnecessary complexity:

| Pattern | Retained | Not used here |
|---|---|---|
| [ReAct](https://arxiv.org/abs/2210.03629) | Interleave bounded observation and evidence lookup | Write/shell actions |
| [Plan-and-Solve](https://aclanthology.org/2023.acl-long.147/) | Explicitly rank findings before selecting one | A long autonomous execution plan |
| [Reflexion](https://arxiv.org/abs/2303.11366) / [Self-Refine](https://arxiv.org/abs/2303.17651) | Turn failure evidence into linguistic memory | Let the same model certify its own conclusion |
| [LATS](https://arxiv.org/abs/2310.04406) | Compare alternatives | Multi-branch search and high model cost |
| [Agentless](https://arxiv.org/abs/2407.01489) | Separate localization from deterministic validation | Automatic patch generation |

This is appropriate for the research Demo because it preserves adaptive global
inspection, causal clarity, privacy, and objective evaluation with one Agent
and one trusted gate.

## Components

### 1. Evidence Compiler

`src/soarm_demo/evolution.py` is the single deterministic evidence and privacy
authority. It rebuilds evidence in memory from the historical run; it never
trusts or overwrites an old `candidate_bundle.json`.

For the target run, it reads the complete 598,979-byte terminal report under a
1 MiB default limit and 2 MiB hard ceiling, verifies the exact SHA, terminal
schema, run-manifest binding, the existing terminal/video semantic audit, and
artifact semantics, then emits a bounded projection. The current projection contains five redacted
`confirmed_failure` candidates. It excludes raw model messages, held-out task
content, private Oracle data, hidden measurements, tracebacks, secrets, and
absolute paths.

### 2. Read-only Audit Agent

The Agent has a new identity, session, context, budget (at most 30 model
requests), and trace. It is distinct from Generation and Demo.

Its only tools are:

1. read one evidence-projection section;
2. list frozen public repository files;
3. read a bounded public file excerpt;
4. search the frozen public surface;
5. submit one audit.

There is no write, replace, patch, shell, test-runner, threshold, private-file,
or source-run mutation tool. Public file reads are SHA-bound and exclude `.env`,
`runs`, `private`, tests, fixtures, held-out/Oracle paths, and raw Experience
records. Remaining public files are line-sanitized; each returned excerpt says
whether its current hash matched, changed since, or was absent from the source
run's `run_inputs.json`, preventing current code from masquerading as historical
evidence.

### 3. Audit artifact

The Agent submits `robot_capability.evolution_audit.v1`:

- 2–8 findings with contiguous ranks;
- one selected finding;
- one `positive | negative | unresolved` Experience claim;
- observation, falsifiable hypothesis, alternatives, confidence, and evidence
  refs;
- one future recommendation and a supporting/falsifying test;
- fixed epistemic statements: source verified, no framework change, no proven
  capability improvement, and a fresh Generation/Validation run required.

All free text is bounded. The complete artifact is limited to 64 KiB.
The first valid submission is frozen; later calls cannot replace it.

### 4. Trusted evaluator

The evaluator is deterministic Python, not another LLM. It independently:

- recompiles and rehashes the source projection;
- confirms the Agent-readable framework snapshot did not change;
- validates schema, rank order, source identity, and the single-claim rule;
- rejects unknown or unselected evidence refs;
- requires every claim to cite at least one run-projection evidence ref;
- permits a positive claim only with `confirmed_success` evidence and a
  negative claim only with `confirmed_failure` evidence;
- treats success/failure as execution outcomes, not proof of a particular
  mechanism; the compiled Generation lesson explicitly marks root causes and
  remedies unvalidated;
- scans the audit, raw Experience record, and Generation view for private or
  secret material;
- makes the 64 KiB limit a rejecting gate and rejects schema-invalid artifacts.

`accepted`, `rejected`, and `inconclusive` describe the Experience claim—not a
code patch and not robot capability improvement.

### 5. Experience publication

Only `accepted` can append one hash-bound version to the sole raw authority:

```text
libraries/experience/v1/records.jsonl
```

`status: approved` means the record passed evidence and privacy gates. The
scientific result is separately represented by `conclusion_kind`. The publisher
is private behind the combined trusted evaluate→publish API and verifies the
accepted evaluation's exact record hash and fixed scientific flags. It rejects
symlinks throughout the authority path, is idempotent, rejects conflicting
`(experience_id, version)` pairs, and updates manifest count/hash. A storage
failure leaves the claim accepted but makes the overall process inconclusive.
There is no second persistent Experience database or copied approved ledger.

A later Generation run derives `selected_records.json` through the strict
allowlist projector. Raw origin, run provenance, evidence references,
measurements, private material, and ambiguous `repair_succeeded` fields never
enter the Generation snapshot. The ledger retains the original `1.0.0` wording
and the hardened `1.1.0` correction; latest-version selection exposes only the
conservative `1.1.0` lesson. The current source run is explicitly excluded by
source run ID.

The accepted report contract is schema- and semantic-validated before any
ledger mutation. Publication also fail-closes on a missing required payload
hash, stale ledger hash/count, non-canonical JSONL, ambiguous semantic version,
or an Experience manifest that attempts to expose raw records. The single
checked projector validates record schema, approved/latest status, field
allowlist, and content privacy. The deterministic 1.1.0 replay receipt is
[`EVOLUTION_CORRECTION_RECEIPT.json`](EVOLUTION_CORRECTION_RECEIPT.json); it
adds no model calls and does not modify the historical AWS report.

One intentionally accepted single-machine residual remains: a filesystem I/O
failure after an atomic Experience append but before the final run-report write
can leave a verified ledger entry with only the run-local trusted evaluation
and approved-record artifacts. Avoiding that narrow failure would require a
transaction or two-phase journal, which is disproportionate for this research
Demo; the next audit can reconcile the hash-bound idempotent record.

## Scientific interpretation

Three statements remain separate:

1. **Evolution process completed** — the Agent submitted a valid audit and the
   evaluator ran.
2. **Experience claim accepted** — the selected lesson is evidence-grounded and
   safe to expose to later Generation.
3. **Capability improvement proven** — always false in this Evolution run;
   only a fresh Generation/Validation experiment can establish it.

Autonomous prompt/schema/source modification is intentionally outside this
Demo. If later needed, it should be a separately authorized Meta-Evolution
experiment with its own intervention and causal evaluation protocol.
