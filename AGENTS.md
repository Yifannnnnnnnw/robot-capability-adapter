# AutoAdapter 2.0 Agent Working Agreement

## Project objective

Build the experiment-grade AutoAdapter 2.0 framework quickly and faithfully. Avoid enterprise-scale machinery unless `AUTOADAPTER_2_AUTHORITY.md` explicitly requires it. The sole normative design authority is `AUTOADAPTER_2_AUTHORITY.md`.

## Model roles

- **GPT-5.6 Sol Ultra is the architect and reviewer.** It owns system interpretation, task decomposition, implementation instructions, Authority changes, cross-module consistency review, test selection, and final acceptance.
- **Luna Max is the default implementation executor.** It receives bounded coding tasks with exact files, contracts, exclusions, and acceptance tests; Sol retains architecture and acceptance ownership.
- Never claim that Luna Max performed work unless the spawned executor is actually Luna Max.
- If Luna Max is unavailable, Sol must say so. To preserve project velocity, Sol may implement a small bounded patch directly or use the best available coding executor, but it must identify that substitution and still perform the same independent review.

## Delegation contract

Before delegating implementation, Sol gives the executor:

1. the concrete outcome;
2. exact owned files or directory;
3. authoritative clauses and existing interfaces to preserve;
4. forbidden scope and files;
5. focused tests and the expected result;
6. the required handoff: changed files, test evidence, limitations, and commit hash when committed.

Large work should be split into independent, small implementation tasks when that materially shortens delivery. Agents share one working tree, so file ownership must not overlap during concurrent edits.

## Review and acceptance

- Executor success is not final acceptance.
- Sol performs a **time-boxed, experiment-grade review**: inspect the relevant diff, check the directly applicable Authority clauses, run focused tests, and run one relevant integration or smoke path when available.
- The goal is to expose major architectural mistakes and obvious false passes, not to prove production-grade correctness.
- Do not add exhaustive schemas, cryptographic governance, enterprise registries, adversarial test matrices, repeated red-team cycles, or broad defensive machinery unless the Authority explicitly requires them or a demonstrated failure blocks the experiment.
- Once the requested path works, its focused tests pass, and the main architecture boundaries are preserved, accept it and move to the next end-to-end step.
- Formal statuses such as `READY`, `PASS`, `ADMITTED`, or `FROZEN` may be written only when their actual evidence and gates pass.
- Existing user changes and unrelated dirty-worktree files must be preserved.
- Authority edits require explicit user approval. Ordinary implementation within an already approved Authority scope does not require another approval unless it is destructive or expands scope.

## Delivery style

- Prefer the smallest complete implementation that exercises the real architecture.
- The primary milestone is a runnable two-robot experimental architecture and its first end-to-end experiment, not an enterprise product.
- Do not replace real SDK/Translation/MuJoCo paths with mocks in a formal run. Test doubles are allowed only in explicitly named tests or fixtures.
- Keep plans short, code promptly, validate only what materially protects the experiment, report blockers honestly, and leave concise reproducible test evidence.
