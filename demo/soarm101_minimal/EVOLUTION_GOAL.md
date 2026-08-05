# Evolution goal — revised scope

This document is the authoritative scope amendment for the active SO-ARM101
Evolution goal.

Evolution is a read-only, independent global audit Agent. It may inspect the
frozen inputs, Generation, Validation, repair, Demo, prompts, schemas, source,
budgets, videos, and terminal evidence of a completed run. It must rank the
observed problems and select exactly one primary finding.

For that finding, Evolution produces one falsifiable, evidence-grounded
Experience lesson and one recommended change for a future run. The
recommendation is data, not an automatically applied patch. Evolution must not
edit source code, prompts, schemas, task oracles, thresholds, frozen inputs, or
historical evidence.

An independent deterministic trusted evaluator—not the Agent—checks evidence
binding, claim strength, privacy, and schema validity. Only a record that passes
this gate may be appended as a versioned Experience record. The next
Generation run receives only the approved, sanitized Generation view.

The outcomes are:

- `accepted`: the Experience claim is supported and safe to publish;
- `rejected`: the claim is contradicted, unsupported, or unsafe;
- `inconclusive`: the available evidence cannot justify the claim.

These outcomes concern the Experience claim only. They do not claim that a
capability improved. Improvement requires a later, fresh
Generation/Validation experiment.

Autonomous framework modification is outside this demo. If desired later, it
should be designed separately as Meta-Evolution with its own permissions and
evaluation protocol.
