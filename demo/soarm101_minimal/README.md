# SO-ARM101 Minimal Capability Demo

This folder is a small, executable sample of the robot-capability generation
workflow. It is deliberately not the future multi-robot repository. A second
robot should be addable by supplying the same four versioned input-library
contracts, a compatible runtime bridge, and compatible task records.

The detailed design is in [`DEMO_PLAN.md`](DEMO_PLAN.md). The benchmark research
behind the task taxonomy is in [`TASK_LIBRARY_RESEARCH.md`](TASK_LIBRARY_RESEARCH.md).

## What is implemented

```text
four generation-visible library views
  -> one continuous ReAct Generation Agent
       Stage 1: freeze G1/G2/G3 JSON
       Stage 2: generate g1.py/g2.py/g3.py, at most 30 calls
       repair: same identity/session, bounded ledger context,
               at most 10 independent 6-call rounds
  -> Validation A: AST-only static validation
  -> Validation B: exactly three LLM validation-suite passes,
                   then trusted direct calls to every Python function
  -> deterministic G1/G2/G3/combined tool catalogs
  -> separate ReAct Demo Consumer
       fresh session/history/tools/budget/trace for each task
       3 visible + 3 pilot-held-out tasks
  -> sealed report
  -> deterministic post-run Evolution Evidence Compiler
       bounded, privacy-filtered run evidence and candidate records
```

Validation is serial. Validation A applies deterministic static checks first.
Only after A passes does the three-call validation-suite generator prepare the
cases for Validation B; it has no role in Validation A. Validation B does
**not** use a Consumer Agent or natural-language judgment at execution time.
The trusted harness imports a validated function and executes
`function(runtime, **case.call_arguments)`, then a private oracle measures the
postcondition.

## Reproduce in the Demo's own venv

From this directory:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lerobot]'
.venv/bin/python -m pytest -q
.venv/bin/python run_pipeline.py --mode offline
```

The verified local environment is Python 3.12.13 with MuJoCo 3.11.0,
NumPy 2.2.6, and LeRobot 0.6.0. Direct pins are recorded in
[`lockfiles/core-python312.txt`](lockfiles/core-python312.txt).

The offline command is a deterministic no-LLM orchestration fixture. It still
executes the complete state machine, ReAct protocols, Stage 1/2 continuity,
Validation A, three-pass Validation B suite generation, direct calls,
packaging, and six-task Demo. Its tabletop score is workflow evidence, not
robot-performance evidence.

Generation accounting is deliberately split: Stage 1 has 3 calls, initial
Stage 2 has 30 calls, and every repair round receives a new 6-call budget for
at most 10 rounds. Repair calls do not decrement Stage 2's 30-call counter;
all windows retain the same Generation Agent identity and session and are
reported separately with provider attempts and token/cost usage.

The Agent's append-only history and trace remain audit evidence, but a new
repair round does not resend that raw full history to the provider. Each round
starts a bounded provider-context epoch containing the current structured
failure and a bounded `repair_ledger`. The ledger carries prior-round failure
signatures, package before/after/change facts, subsequent validation state,
Agent call/attempt accounting, and a content-free process audit. The v2 process
audit records bounded tool/action order, target and result hashes, successful
writes, protocol rejections, and unfinished reads; it does not store model
responses, source contents, hidden reasoning, tracebacks, measurements, or raw
diagnostics. Only bounded, allowlisted diagnostic signatures may remain.
Budget-exhausted, provider-error, and other abnormal rounds are completed in
the ledger and persisted as run evidence rather than silently disappearing.

The complete 9-visible + 3-pilot-held-out task set and all twelve authored
initial states are frozen before Generation starts, together with the selected
3+3 Demo batch, task oracles, and `_common_reset`. The run-local private
execution bundle has an exact detached manifest, raw-byte hashes, and read-only
copies; Validation and Demo reverify the whole bundle at every consumption
boundary and never return its payloads to Generation. Before provider request
1, a framework-only gate proves the 12-way task/state bijection, required scene
facts, finite values, asset IDs, and catalog-owned geometry. That gate is
static in both AWS and offline modes: it freezes twelve task descriptions but
creates zero MuJoCo worlds. A fresh world is built only when an actual
Generation probe, Validation B case, or one of the selected 3+3 Demo tasks is
executed; unselected tasks are not materialized. The Validation B suite
generator neither selects nor changes the six Demo tasks. Demo begins only
after serial Validation A then B and tool packaging pass.

Before the first provider request, the pipeline writes one compact
`run_inputs.json` research manifest. It records byte counts and SHA-256 hashes
for configs, prompts, schemas, fixtures, libraries, private-evaluation inputs,
and the evaluator source actually imported by the run. One `source_tree` hash
summarizes the validator, bridge, oracle, pipeline, and related Python source.
The source itself is not copied into every run; reproducibility comes from the
delivered repository plus these fingerprints. The ignored `.env` and API key
are never included, and this framework-side manifest is not visible in the
Generation snapshot.

On a failed run, final budget and model-attempt accounting is saved before
`run_manifest_terminal_snapshot.json` is copied. That snapshot must equal the
terminal report's state, inputs, artifacts, and budgets. After the report is
written, the live manifest may differ only by its timestamp and the single
`terminal_report` hash/event; the semantic audit enforces this boundary to
avoid either an incomplete terminal snapshot or a report-hash cycle.

## AWS model-backed run

No key is committed. Either export the complete key:

```bash
export AWS_MODEL_API_KEY='...'
.venv/bin/python run_pipeline_with_video.py --mode aws
```

or copy `.env.example` to the ignored `.env` and fill
`AWS_MODEL_API_KEY`. The loader accepts only the four documented AWS variables
and never writes them to a trace.

Long generation requests use:

```text
https://q7s6v6seerne7eyh5ttsovjjcu0hxbou.lambda-url.eu-west-2.on.aws/v1/chat/completions
```

The short API Gateway endpoint remains configured for small requests. Both use
the `X-Api-Key` header, model
`anthropic.claude-sonnet-4-5-20250929-v1:0`, and normalize Anthropic text blocks
plus the documented cost/quota fields.

AWS mode deliberately refuses a direct `run_pipeline.py --mode aws` launch.
Use `run_pipeline_with_video.py`: it always replaces itself with the Demo
venv's Python. On macOS the process must run in a usable CoreGraphics session;
offscreen `mujoco.Renderer` does not require the `mjpython` passive-viewer
trampoline. Other platforms default `MUJOCO_GL` to EGL. The launcher does not
create a standalone renderer-preflight world; renderer and `mp4v` failures are
detected while recording formal Validation B cases or Demo tasks and remain
infrastructure failures.

## MuJoCo video evidence

AWS runs record the actual `MjModel`/`MjData` scene through
`mujoco.Renderer`, at the frozen 640x480, 20 fps, `mp4v` settings:

- Validation B writes one MP4 for every directly executed case in every
  executed validation/repair round under `validation/videos/round_NN/`.
- Demo writes one MP4 per frozen task under `demo/videos/`; a structurally
  complete run therefore has six Demo videos (3 visible and 3 pilot-held-out),
  including tasks whose private oracle reports a performance miss.
- Every MP4 has a framework-written `.metadata.json` sidecar. Before evidence
  is accepted, the harness fully decodes all frames and checks the sidecar's
  renderer, codec, frame count, fps, dimensions, and simulation-time interval.
- `video_index.json` records run-relative paths, byte counts, and SHA-256 hashes
  for each MP4 and sidecar. Validation records also bind the round, case ID,
  frozen-suite hash, generated-package hash, and direct report; Demo records
  bind the frozen task ID. The sealed or terminal report embeds and hashes this
  same index.

If an AWS run fails after formal recording begins, its terminal report keeps a best-effort
`partial` index of complete, decodable, bound videos without replacing the
original failure reason. `partial` describes an unsealed evidence collection;
it does not label the complete MP4s retained in that index as damaged.
Incomplete or orphaned files are not promoted as evidence. A completed,
structurally valid six-task Demo may seal with oracle misses: task success is a
reported performance outcome, not the systems-plumbing criterion. Provider,
runtime, renderer, malformed-report, and other infrastructure faults remain
`INFRASTRUCTURE_FAILED`. Historical runs may retain the legacy `DEMO_FAILED`
classification for an exact-6/6 gate, but new runs do not use it. The
deterministic offline fixture creates no physical
videos at all; its index is explicitly empty and labels itself a deterministic
fixture with no fake physical video. The pipeline rejects an offline run that
contains such files.

## Input libraries

| Library | SO-ARM101 P0 content | Generation sees |
|---|---|---|
| Morphology | Apache-2.0 MJCF, 18 meshes, measured kinematics/provenance, plus the versioned thin tabletop scene catalog | Kinematics, sources, compile facts, robot MJCF and public scene/catalog records; not binary meshes |
| SDK/runtime | LeRobot 0.6.0 dossier and hardware-free API probe | Sanitized API surface, runtime contract, minimal example |
| Tasks | 12 templates and structure-aware similarity model | Only 9 visible templates, taxonomy, sources, and 9/3 policy counts; no split seed, private path/hash, or held-out content |
| Experience | Versioned repair-experience interface | Empty committed `records.jsonl`; selection returns `[]` |

The three pilot-held-out templates, all concrete task instances, oracle
thresholds, similarity audit, and fixed Demo batch remain in `private/` and
are never materialized into the Generation workspace.

### Thin scene library and isolated task worlds

`scene` is not a fifth Generation input. It is a versioned child entry of the
Morphology Library at
`libraries/morphology/scenes/soarm101_tabletop/v1/`. The entry contains one
tabletop definition and a small catalog of reusable primitive profiles (cube,
cylinder, tray, bowl, and target markers). A task or validation case supplies
only `asset_ref`, instance ID, and pose; geometry, mass, material, friction,
and collision settings come from that immutable catalog.

The catalog does not replace runtime isolation. Generation public probes,
every Validation B case, and every Demo task each construct fresh
`MjModel`/`MjData` state. Conversely, isolation does not replace the catalog:
without a canonical asset version and hash, two isolated runs could silently
use different physical definitions. All three phases therefore use the same
resolver freeze and catalog, while each case/task receives a newly compiled,
state-independent world.

The same freeze also closes over the robot model itself: it binds the selected
MJCF and all 18 externally referenced mesh resources by relative path and
SHA-256. Every formal compile/reset rechecks that complete closure and passes
the just-verified bytes directly to MuJoCo. A task-local world therefore cannot
silently pick up a changed robot XML or mesh after input freezing.

P0 has no Scene Builder Agent or scene-candidate lifecycle. The required
primitives are ordinary Morphology Library assets pre-scanned from the task
catalog. An unknown `asset_ref` is a plain `MISSING_ASSET` input error; add the
asset to a later Morphology Library version and start a new run. The current
run never invents inline geometry or mutates its asset catalog.

## Current and planned Evolution boundary

The currently implemented `evolution.py` is a deterministic post-run
**Evidence Compiler**, not a global audit Agent. It builds the run-local
`evolution/candidate_bundle.json` from bounded, redacted Validation, repair,
and Demo artifacts. Candidate records remain `status: candidate`; this current
compiler neither edits source nor writes the Experience Library.

Each candidate also carries a detached `candidate_payload_sha256`: the
canonical SHA-256 of every candidate field except that digest field itself.
Any later evaluator must bind this full payload hash, not only the shorter
evidence-oriented `candidate_id`. The digest remains run-local and is not
written back into the sealed report or run manifest.

The current candidate format records independent schema, evidence, privacy,
and replay facts. A failed repair may produce only negative `avoid` evidence,
and incomplete or infrastructure-tainted evidence remains unresolved; neither
is presented as a successful repair.

Every indexed causal artifact separately records `hash_bound` and
`semantic_valid`. Manifest binding proves which bytes ended the run; it does
not prove that those bytes tell a coherent story. Evolution therefore audits
failure feedback against its schema and checks Static, Direct, repair-gate,
and Demo cross-field invariants. A bound contradiction is retained as
`semantic_invalid`, but it cannot support a successful/confirmed outcome or
the evidence gate.

The proposed next phase is a separate, global-observer ReAct Evolution Agent:
it will audit the frozen inputs, Generation/repair process, Validation, Demo,
videos, budgets, and historical runs; select one evidence-backed intervention;
produce one atomic candidate patch; and end before the trusted replay judge
runs. This Agent and its evaluator/publisher are **designed but not yet
implemented**. Once that design is approved and implemented, a successful
independent replay and deterministic privacy/evidence gates may automatically
publish one immutable, versioned record to the Experience Library. The
published record becomes visible only to a newly frozen later run, never to the
run that produced it. See [`RESULTS.md`](RESULTS.md) for the present evidence
boundary.

## Evidence boundary

- The LeRobot 0.6.0 probe runs in the local Python 3.12 venv without opening a
  serial port and is stored in `libraries/sdk_runtime/.../api_probe.json`.
- Before any AWS model client is constructed, the pipeline schema-validates
  that probe, verifies its manifest hash, and requires its package, version,
  commit, runtime ID, imports/aliases, public methods, configuration fields,
  and six observation/action keys to agree with `manifest.yaml`,
  `api_surface.yaml`, and `runtime_contract.yaml`. One compact
  `framework/sdk_activation.json` records those verified facts and the four
  source hashes; the source files are not copied into every run. Offline performs the same
  reference check but records `offline_reference_only` and never claims that
  the live AWS gate was activated.
- The real SO-ARM101 MJCF compiles and the LeRobot-compatible MuJoCo bridge is
  tested for its six keys, units, clipping, target receipts, simulation
  progress, and a direct generated G1 call.
- AWS Validation B and Demo video evidence comes only from the real
  MuJoCo runtime; no separate renderer/encoder preflight world is created.
- The object-task fixture is deterministic and explicitly labelled
  `not_physical_evidence` in run reports.
- The 3 private tasks are called `pilot-held-out`, not formal held-out evidence.
  Formal object-contact calibration and real-robot evaluation remain later
  experiment gates.

See [`RESULTS.md`](RESULTS.md) for the historical runs, current local
verification, and remaining post-fix AWS replay gate.
