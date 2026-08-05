# Demo execution plan

The authoritative working design is
[`SOARM101_MINIMAL_DEMO_PLAN.md`](../../SOARM101_MINIMAL_DEMO_PLAN.md). This file
maps that design to the executable folder.

## Runtime sequence

1. Verify raw hashes for all four library entries.
2. Materialize an allowlist-only Generation snapshot, create a detached exact
   file/tree manifest, and reverify it at every Generation tool and continuity
   boundary. Resolve the selected Morphology child scene and freeze the MJCF,
   all 18 referenced meshes, catalog, and source hashes. Before provider call
   1 this is a static closure/integrity check: it creates no MuJoCo world.
   Scene remains inside Morphology rather than becoming a fifth input library.
3. Freeze the full 9-visible + 3-pilot-held-out catalogs, all twelve authored
   states, the selected six-task Demo batch, oracles, and `_common_reset` as
   one run-local private execution bundle before provider call 1. Verify the
   12-way task/state bijection, required scene facts, agent-input geometry, the
   3/3 Demo selection, finite authored values, and all asset refs. This is a
   static catalog-integrity check in both modes: freezing twelve descriptions
   does not create twelve MuJoCo environments. Runtime state is constructed
   only on demand for an actual Generation probe, a Validation B case, or one
   of the six selected Demo tasks; the six unselected tasks create no world.
   Each such execution resolves only its referenced assets and just-in-time
   compiles fresh `MjModel`/`MjData` state from the same immutable scene
   catalog.
   In AWS mode, launch through `run_pipeline_with_video.py`; do not create a
   standalone renderer-preflight world. The real renderer, `mp4v` encoder,
   sidecar, and full-frame decoder are checked on formal Validation B and Demo
   recordings.
4. Construct one Generation ReAct Agent.
5. Let it read the complete visible snapshot in one tool call, submit the
   G1/G2/G3 Stage 1 JSON, validate it, and freeze its canonical hash.
6. Expand the same Agent's tool allowlist. Preserve its identity, session,
   episode, workspace, append-only audit history, and trace while it writes the
   Stage 2 package.
7. Run Validation A: parse Stage 2 with AST-only static checks. Freeze the first
   passing public API. Return structured A failures to the same Agent when
   repair is permitted.
8. Only after Validation A passes, begin Validation B. Give Stage 1, the frozen
   public signatures, schemas, and private validation references—but not
   implementation source—to the three-call B suite designer: generate,
   review/rewrite, final review/rewrite. The suite designer serves Validation B
   only and never participates in Validation A.
9. Freeze the Validation B suite. Directly import and invoke every Python
   function from clean runtime state. After a package-changing B repair, run A
   again before re-running the whole frozen B suite. In AWS mode, record one
   actual-MuJoCo MP4 per B case under that direct-validation round; Validation A
   itself does not produce a video. Validation-suite contract v2 distinguishes
   the harness's host-monotonic hard deadline from public capability timeout
   semantics in MuJoCo simulation seconds; no pass/fail comparison mixes those
   clocks. Nominal physical cases omit optional timeout/duration overrides so
   the advertised defaults are exercised.
10. Package only passing functions into G1, G2, G3, and combined catalogs; hide
   the injected `runtime` parameter.
11. Construct a different Demo ReAct Agent. For every task, replace its
    session, episode, history, tool bindings, 30-call counter, trace, and world.
12. Execute the fixed interleaved 3 visible + 3 pilot-held-out batch, score with
    private oracles, disable Demo-to-Generation repair, and record one
    actual-MuJoCo MP4 per task.
13. Fully decode every MP4, verify its metadata sidecar, and freeze
    `video_index.json`. Bind Validation videos to round/case, suite/package
    hashes, and direct report; bind Demo videos to frozen task IDs. Embed the
    same index and hashes in the sealed report before sealing budgets.
14. After the run has sealed or reached a terminal failure, an independent
    read-only Evolution Agent audits the deterministic privacy-safe evidence
    projection, ranks findings, and submits one Experience claim. A separate
    trusted evaluator recompiles the source evidence and checks grounding,
    privacy, and claim strength. An accepted claim may append one versioned
    Experience record; Evolution never edits framework files or historical
    evidence and never claims capability improvement without a later fresh run.

## Hard budgets

| Phase | Limit |
|---|---:|
| Stage 1 | 3 model calls |
| Initial Stage 2 implementation | 30 model calls |
| Repair | at most 10 rounds, each with a fresh 6-call counter |
| Validation B suite design | exactly 3 model calls |
| Demo | 30 model calls per task |

Local tool execution, schema checks, MuJoCo steps, and direct Python calls do
not consume LLM-call budget.

Repair preserves the same Generation Agent identity, session, episode,
workspace, append-only audit history, and trace, but never consumes the closed
initial Stage 2 counter. Provider requests do not repeatedly receive the raw
full history. Each repair round gets a bounded context epoch with current
structured feedback plus a bounded `repair_ledger` of prior failure signatures,
package before/after/change facts, subsequent validation status, Agent
accounting, and a v2 content-free process audit. That audit records bounded
tool/action order, target/result hashes, successful writes, protocol rejection
counts, and whether the final action left an unfinished read. It excludes raw
messages, source contents, hidden reasoning, tracebacks, measurements, and raw
diagnostics; only bounded, allowlisted diagnostic signatures may remain. It is
projected into the next repair epoch and persisted as run evidence, including
rounds that end in budget exhaustion, provider error, or another exception.

## Evolution and Experience closure

The implemented architecture is Evidence-grounded
Audit–Synthesize–Judge–Publish. `evolution.py` remains the single deterministic
Evidence Compiler, evaluator, privacy gate, and publisher; the independent
Agent runtime owns only its read-only tools, model identity/session/budget, and
trace.

The compiler rebuilds evidence in memory and never treats an older run-local
candidate bundle as authority. It records artifact binding and artifact meaning
separately. Manifest-bound but contradictory artifacts remain
`semantic_invalid` and cannot support a causal claim. The 598,979-byte target
terminal report is read in full and exact-byte hashed under the 1 MiB default
and 2 MiB hard ceiling.

The Agent ranks several global findings but selects exactly one primary
finding and one falsifiable Experience claim. It has no mechanism to write a
patch or alter prompts, schemas, thresholds, source, frozen inputs, or history.
A deterministic evaluator then checks source re-verification, evidence refs,
claim strength, privacy, total artifact size, and the Generation projection.
It treats a confirmed failure as outcome evidence rather than proof of a
specific mechanism. Only its private, hash-bound evaluate→publish API can
append one version at a time.

Publication status and scientific sign are deliberately separate. An approved
record may be positive, negative, or unresolved. All records produced by this
Evolution run state that no framework change was applied, no capability
improvement was proved, and a fresh Generation/Validation run is needed. Raw
provenance/evidence remains framework-only; the next run receives only the
sanitized selected view and explicitly excludes records derived from its own
run ID. The first real negative claim retains its original `1.0.0` ledger line;
a post-publication boundary audit appended conservative `1.1.0` wording, and
latest-version selection exposes only `1.1.0`. The correction does not rewrite
the source run or claim that the Agent's proposed mechanism was proved.

The Python evaluator itself is also part of the frozen experiment input. A
fixed source allowlist covers entrypoints, pipeline, validation-suite/static/
direct gates, MuJoCo/fixture bridges, oracle, packaging, and report-audit
modules. `run_inputs.json` records every allowlisted source file's byte hash
and one detached source-tree digest before any LLM call; the whole Python
harness is not copied into every run. This provenance is framework-side and
outside the Agent-readable input snapshot. Both sealed and failure reports
re-open the same source fingerprints during semantic audit, so changing a
validator after the run cannot silently masquerade as the original harness.

Failure reporting uses a two-cut manifest rule: first finalize budgets and
model/provider accounting and save the live manifest; then copy the terminal
snapshot and build the terminal report; finally append exactly one report-hash
event to the live manifest. No other difference between the snapshot and live
manifest is permitted.

## Important P0 choices

- The scene registry is intentionally thin: one tabletop template plus the
  reusable primitive profiles needed by the 12 scanned tasks. Tasks/cases own
  only `asset_ref`, ID, and pose; catalog assets own geometry and physics.
- P0 has no Scene Builder Agent or candidate lifecycle. An unknown `asset_ref`
  is simply a `MISSING_ASSET` input error; add the required primitive to a new
  immutable Morphology Library version and start a fresh run from input
  freezing.
- No camera is configured. Each task supplies the object/target coordinates
  that the Agent is permitted to use; scoring state remains private.
- The combined catalog is used once to prove plumbing. It is not a G1/G2/G3
  comparison experiment.
- The committed fixture includes a transparent Cartesian mapping solely for
  repeatable orchestration tests. Its Stage 1 records that calibrated physical
  kinematics remain unresolved.
- Every G3 result contract exposes `phase_reached`; object-move sequences also
  expose `completed_moves`, `failed_move_index`, and `timeout_scope`. This lets
  direct validation and repair localize grasp/hold/lower/release progress
  without inferring phases from natural language.
- Demo selection is completed and frozen before Generation. The later
  Validation B suite generator cannot inspect, select, reorder, or replace the
  six Demo instances.
- AWS mode is available only after the SDK probe is `ready` and a complete API
  key is supplied through the environment. It must use
  `.venv/bin/python run_pipeline_with_video.py --mode aws`; the launcher always
  executes the dedicated venv Python and requires a usable graphics session.
- A later AWS failure preserves a best-effort `partial` video index containing
  only complete, decodable, artifact-bound recordings and keeps the original
  terminal cause. A successful AWS run requires exact per-case/per-round
  Validation coverage and all six Demo videos.
- Offline mode records an explicit empty video index and rejects physical-video
  files instead of fabricating evidence for its deterministic fixture.
