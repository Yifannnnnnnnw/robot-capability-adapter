# AA1 / AA2 generation implementation audit

2026-09-09. This is a source comparison and migration recommendation, not a new
experiment protocol or an implemented migration. Earlier statements that the
implementations were the same were too broad and are withdrawn.

Baselines: AA1 `981526092/auto-adapter` at
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`; AA2 at `4e5faca`.
AA1 orchestrator files were read from the prior snapshot labeled with that commit under
`/private/tmp/aa1-primitives-review-585eb1-20260907/auto_adapter/` and
the fixed-version `tools.py`/`react_loop.py` fetched through authenticated access under
`/private/tmp/aa1-tool-read-audit-20260909/`. The old orchestrator copy was not
downloaded again during this audit.
The comparison does not claim that another AA1 revision behaves identically.

## Confirmed differences

| Area | AA1 source behavior | AA2 behavior at the audited commit |
|---|---|---|
| Generated object | Skeleton branch fills a Spec and exposes `build()` returning an AA1 skeleton; from-scratch builds a `Robot` from an MJCF path. | Driver exposes fixed capability methods and `build(model, data)` using Framework-owned physics. |
| Initial inputs | Short task instructions and workspace paths; model reads Study/MJCF through tools. | Fixed Generate keeps complete capability design/criteria and morphology inline, with Study/MJCF/skeleton file indexes. Dynamic Generate retains a different, larger input package. |
| Skeleton inspection | `inspect_skeleton` returns class documentation and Spec field schema. | The identically named tool returns full source text. These are different tool contracts. |
| File reading | Complete file text, no pagination. | Complete file text after `8458967`; earlier fixed-input paging was an AA2 addition. File size/path limits remain different. |
| Model conversation | Each phase/retry creates a new `ReactLoop` and message list. A loop appends all its own tool turns without history pruning. | Study is separate. Generate/Repair share a message list, with 80k character budgeting and, by default, only three recent groups plus summaries/snapshots. The recent diagnostic bypassed the three-group cutoff only. |
| Development execution | AgentCore Python session may persist across phases; `local_exec` launches a new shell subprocess per call in the same workspace. | Study worker closes before Generate. Generate/Repair share one public MuJoCo Python worker and its remaining step budget. No equivalent shell tool is exposed. |
| Editing | `write_file` overwrites a file. There is no dedicated patch tool, but `local_exec` can edit existing files using scripts. | `write_file` overwrites or appends. The prompt asks for complete source, and Repair revision bookkeeping is tied to file-tool writes. A 37k Driver was repeatedly re-emitted. |
| Failure feedback | Failed tests' explicit `detail` strings are included in the next Generate/Repair prompt. | Feedback is appended to the existing conversation, but trusted B1 checks produce binary results without branch reasons. Repair further removes intermediate trajectory samples. Reading the diagnostic file cannot recover details that were never recorded. |
| Completion | The phase wrapper usually checks expected artifact existence; validation behavior depends on branch and mode. | ReAct reserves final turns for `write_file`; completion requires source/ABI checks and import/build, followed separately by trusted Harness validation. |
| Model/transport | Bedrock-backed clients; Anthropic SDK timeout 300 seconds, configured output/retry settings. | Holistic-compatible HTTP transport, adaptive Opus and 16,384 output tokens in this diagnostic. Its 300-second timeout is a test-script override. |

AA1's inspected defaults are Study/Generate/Repair 16/22/22 for the skeleton
branch and 14/40/20 for from-scratch, with 8,000 output tokens per turn. These are
source defaults, not verified settings for every historical AA1 run. AA2's matching
22-turn setting does not make its tool or feedback behavior identical.

Evidence anchors:

- AA1 `orchestrator.py:529–557, 645–664, 1099–1122`:
  new phase loop, failed-detail summarization, retry back into Generate.
- AA1 `orchestrator_from_scratch.py:466–501`: failed-detail prompt and in-place repair.
- AA1 `react_loop.py:115–127, 255–260, 334–336`: provider construction,
  fresh phase messages, within-loop append.
- AA1 `tools.py:123–137, 227–270, 494–523`: full file read, Spec inspection,
  shell subprocess execution.
- AA2 `pipeline.py:2224–2322`, `repair.py:819–906`, `react.py:585–586`:
  same development object and appending feedback.
- AA2 `agent_context.py:84, 574–614`: history retention and summarization.
- AA2 `harness/b1_contracts.py:1718–1826`, `harness/runner.py:1047–1062`,
  `driver_synthesis/repair.py:227–288, 513–561`: binary contract results and
  compacted repair feedback.

The strings “AA1-style” in AA2 comments are descriptions, not evidence of code or
behavioral equivalence. Matching phase names or turn counts also does not establish it.

## What can be reused, and what must remain explicit

Recommendation: reuse/port the inspected AA1 generation execution core and its
tool/prompt behavior, with identified AA2 boundary adapters. Do not bolt a second
complete experiment pipeline onto AA2 or import AA1's old verdict as the new verdict.
This is not a promise of a copy-only migration: AA1 imports its own Bedrock/AgentCore
clients and skeleton/Robot APIs, so those bindings need actual adaptation.
The inspected snapshots do not include all dependencies (`agent/converse_client.py`,
`agent/__init__.py`, `skeletons/__init__.py`, `skeletons/base.py`, and
`skeletons/grasp_backends.py`, among others). An untouched AA1 entry-point run
has not been verified. TGCD/IVC and a robot SDK Translation path are outside this
generation-source comparison; no equivalence claim is made for them.

Required AA2 boundaries are the fixed capability inputs, Framework-owned
`model/data` Driver ABI, public/private separation, and the existing trusted
Harness. Its thresholds and cases must remain unchanged. Existing approved
diagnostic model/step/submission budgets are settings to record explicitly, not
evidence of AA1 implementation identity.

The user's earlier request to preserve Generate→Repair conversation is an
approved behavioral departure from this AA1 snapshot. Preserve it only as an
explicitly named departure; do not describe it as original AA1 behavior. The
three-group summarizer is a separate AA2 choice, not implied by continuous history.

The next implementation should make these operations concrete: read Study and
public files, inspect a skeleton through a useful Spec contract, edit the existing
Driver locally, execute development checks, and append explicit failed-check
details. The comparison must cover actual tool schemas, payloads, files and
feedback, not just the sequence “Study → Generate → Validate → Repair”.

Old AA1 validation cannot be copied unexamined: the skeleton framework branch
uses a structural success rule that can accept a run despite behavioral threshold
failures (`orchestrator.py:790–820`). The existing AA2 Harness stays authoritative.

## Current work state

The paid Franka run was stopped at the user's request: one Harness submission,
6/10, no completed Repair. See [run results](NO_PAGING_FRANKA_RESULTS.md).
Pagination removal and those results are committed (`8458967`, `4e5faca`).

An untested draft for local editing, snapshot synchronization and tool-output
diagnostics was preserved at `/private/tmp/aa2-repair-output-wip-20260909.patch`
and removed from the working tree while the alignment scope is reconsidered.
It is not implemented or accepted work. Failure-detail propagation and the
`reference` false rejection are also not fixed at this baseline. No new paid
model call or AA1 migration was started during this audit.

Direct audit with independent read-only agents; Luna Max was not used.
