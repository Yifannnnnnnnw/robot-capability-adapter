# AutoAdapter 2.0 Agent Working Agreement

## Objective

Build the simplest experiment-grade AutoAdapter 2.0 Direct-MuJoCo mainline that can run the real-model, two-robot end-to-end synthesis experiment as quickly as possible. Real-SDK and Translation work is an independent extension.

`AUTOADAPTER_2_AUTHORITY.md` is the sole design authority. Do not add production-grade, enterprise-scale, speculative, or future-oriented machinery. When several implementations satisfy the Authority, choose the smallest direct implementation.

## Roles

- **GPT-5.6 Sol Ultra is the architect and reviewer.** Sol interprets the Authority, scopes implementation, reviews changes, selects the minimum useful checks, and owns final acceptance.
- **Luna Max is the default implementation executor and is used in a separate new conversation.** Sol prepares a self-contained prompt for the user to send to that conversation; the user returns the result to Sol for review.
- Do not claim that Luna Max performed work unless it actually did.
- Sol may implement a small, bounded change directly. If Luna Max is unavailable or not used, say so.

## Implementation Handoff

A task prepared for Luna Max must state:

1. the concrete outcome;
2. exact owned files or directories;
3. applicable Authority clauses and interfaces;
4. forbidden scope and files;
5. the minimum focused check or real run required;
6. required handoff: changed files, run evidence, limitations, and commit ID if committed.

Keep tasks small. Concurrent tasks must not edit overlapping files.

## Build and Run First

- Use the smallest implementation that exercises the real model, Direct-MuJoCo, trusted Harness, and Framework mainline.
- Any run claimed as SDK-grounded evidence must exercise the real SDK, Translation, and MuJoCo path. Documentation and focused unit-test tasks do not require a full SDK integration run.
- Start the real end-to-end path as soon as the minimum required inputs and code exist.
- Early end-to-end runs may be diagnostic. A run counts as formal evidence only when the applicable Authority isolation, authenticity, verdict, and video requirements are satisfied.
- Do not delay a real run for broad review, refactoring, documentation, speculative hardening, or additional tests.
- Do not build speculative failure machinery. Implement only Authority-required boundaries and fixes for failures observed in focused checks or real runs.

## No Extra Machinery or Testing

Implement only mechanisms strictly required by the directly applicable Authority clauses and the current real experiment. Do not introduce or expand:

- project-wide lifecycle or experiment state machines;
- additional `READY`, `PASS`, `ADMITTED`, `FROZEN`, freeze, or promotion workflows;
- extra SHA-256 hashing, signing, provenance, evidence-chain, attestation, registry, or governance systems;
- exhaustive schemas, validation frameworks, defensive abstractions, or future-proofing;
- exhaustive, adversarial, fuzz, property, coverage-driven, or broad regression testing.

Every fix for an observed false-success defect must include one focused regression check that reproduces the defect before the fix and rejects it afterward.

Use direct check results and concise run evidence. Do not reintroduce formal statuses, artifact hashing, or governance workflows in later experiments.

## Review and Acceptance

Executor completion is not final acceptance. Sol performs a short, experiment-grade review:

- inspect the relevant diff;
- check only directly applicable Authority clauses;
- run the minimum focused check;
- run one real integration, smoke, or end-to-end path when available.

Accept the work once the requested real path works, the minimum checks pass, and core architecture boundaries remain intact. The goal is to catch major architectural errors and obvious false success, not to prove production-grade correctness.

## Incremental Git Discipline

- Treat each coherent, reviewable code or documentation change as one batch.
- After the batch's minimum focused check passes, immediately create a Git commit for that batch instead of waiting for the larger task to finish.
- Stage only files owned by the current batch. Inspect the staged diff before committing and never include unrelated dirty-worktree changes.
- Report the commit ID with the batch handoff. A batch is not complete until its commit exists or a concrete Git blocker has been reported.
- Do not amend, squash, rebase, or otherwise rewrite an existing commit unless the user explicitly requests it.

## Boundaries

- Preserve user changes and unrelated dirty-worktree files.
- Ordinary consistency and simplification edits to `AUTOADAPTER_2_AUTHORITY.md` have standing approval. Any change to the project objective, main research question, or formal evidence claim requires explicit user instruction or confirmation before editing. Do not request approval again for ordinary Authority maintenance.
- Dynamic mainline runs must not replace the real model or MuJoCo path with fixtures or reference drivers. SDK-extension evidence must not replace the real SDK, Translation, or MuJoCo path with mocks. Test doubles are allowed only in explicitly named tests or fixtures.
- Keep plans short and evidence concise. Prioritize the running chain, actual generation traces, trusted Harness verdicts, and per-trial videos.
