# AA1 Piper flow visualization

The current recording uses code commit `7f37f27e` on
`codex/aa1-task-grounded-capabilities`: three real TGCD turns with
`read_file`/`write_file`, followed by 23 real generation turns. It reuses the
previously completed Piper study and its seven recorded turns; see
`records/source/study-reuse.json`. All five requested methods accept the
`request=` interface and the generated driver builds with the actual model.
The 36 focused framework checks are recorded separately. No independent
capability physics suite was executed. Known gaps in criteria are preserved
in `records/source/parent-interface-review.json`.

Start with [the raw-record index](record-index.md),
[the initial TGCD user prompt](records/tgcd/initial-user-prompt.txt), or
[the current design notes](tgcd-design-notes.md). `linked-record-extract/`
preserves the older inline-prompt files explicitly referenced by the user.

This directory contains a small in-conversation visualization fragment and a
data embedding helper. It is intentionally limited to inspection of one
recorded AA1 Piper run: `STUDY → TGCD → GENERATE → REPAIR`. It does not run
AA1, call a model, fetch files, or assign experiment statuses. The recorded
read-file/submit-design TGCD is a historical implementation snapshot; a later
run that changes the TGCD write path is recorded as its own source run. The
collector detects the tools actually present in that run.

The collector keeps the final gateway message array once per stage, then stores
each turn's exact recorded framework and gateway message counts. The fragment
reconstructs the selected gateway turn by slicing that array, which keeps the
embedded artifact below the 1 MB fragment limit. The raw exporter retains every
framework and gateway request for complete inspection.

## Raw records and dataset collection

`export_records.py` reads the actual diagnostic root and writes per-stage,
per-turn files under `records/`:

```bash
python3 outputs/aa1-piper-flow/export_records.py \
  --clean --include-public-assets path/to/diagnostic-run-root
```

Each turn contains the recorded request fields (`model`, `system`, `tools`,
`max_tokens`, `messages`, `gateway_messages`), its response or error when
present, full tool results recovered from the next request when available,
and the raw trace step. Trace observations are marked as possibly truncated;
they are not substituted for the full next-request tool results. Missing
responses remain missing. The exporter also copies pre-repair driver/canary
artifacts, repair traces, repair script/logs when they exist, optional study
reuse/skeleton files when present, and the public Piper XML assets when
explicitly requested.

`collect_flow.py` then creates the compact dataset consumed by `build_flow.py`:

```bash
python3 outputs/aa1-piper-flow/collect_flow.py \
  path/to/diagnostic-run-root \
  -o outputs/aa1-piper-flow/piper-flow.json
python3 outputs/aa1-piper-flow/build_flow.py \
  outputs/aa1-piper-flow/piper-flow.json
```

The collector keeps original generation and `REPAIR-1` as separate stages.
It includes the file-based TGCD handoff when present: the first user path and
instruction, the `read_file` tool call, and the actual framework tool-result
block carried into the next request. It also records the exact phase tool
inventories and marks reused study inputs. Commit metadata comes from the
recorded run report, with the historical snapshot fallback identifying the
initial pipeline integration commit `6d4e8162` and run code HEAD `a5b6d748`;
both are commits in the same parent repository and are source identifiers, not
acceptance claims.

## Build

Create a cleaned JSON dataset at the path supplied to the builder, then run:

```bash
python3 outputs/aa1-piper-flow/build_flow.py path/to/piper-flow.json
```

The default output is `outputs/aa1-piper-flow/aa1-piper-flow.html`. A different
destination can be selected with `-o`. The builder embeds the dataset as a
literal JavaScript value, escapes HTML-sensitive characters, and rejects a
fragment at or above 1,000,000 bytes. The recorded dataset must not contain
credentials or request headers.

## Dataset contract

The root object is deliberately small and preserves source data rather than
normalizing it into a second reporting schema:

```json
{
  "title": "AA1 Piper · STUDY → TGCD → GENERATE",
  "run": {
    "run_id": "...",
    "robot_id": "piper",
    "source": "...",
    "recorded_at": "..."
  },
  "inputs": [
    {"label": "...", "value": "..."}
  ],
  "stages": [
    {
      "id": "study",
      "label": "STUDY",
      "status": "success",
      "summary": "...",
      "request": {
        "model": "...",
        "system": "完整 system prompt",
        "tools": [],
        "max_tokens": 8000
      },
      "gateway_conversation": [],
      "inputs": [
        {"label": "...", "value": "..."}
      ],
      "turns": [
        {
          "id": "study-0",
          "label": "第 1 轮",
          "status": "success",
          "message_count": 0,
          "gateway_message_count": 0,
          "inputs": [
            {"label": "...", "value": "..."}
          ],
          "request": {
            "model": "...",
            "system": "完整 system prompt",
            "tools": [],
            "max_tokens": 8000,
            "messages": [],
            "gateway_messages": []
          },
          "response": {
            "content": "完整 response content",
            "stop_reason": "...",
            "usage": {}
          },
          "tool_calls": [],
          "observations": [],
          "outputs": [
            {"label": "...", "value": "..."}
          ],
          "checks": [
            {"label": "...", "status": "...", "value": "..."}
          ]
        }
      ],
      "outputs": [],
      "checks": []
    }
  ]
}
```

`stage.request` stores request metadata shared by the stage and
`stage.gateway_conversation` stores the last full gateway message array from
the recording. For a selected turn, the fragment merges `stage.request` with
`turn.request` (when present), then takes the first `gateway_message_count`
entries from that shared array. This reconstructs the exact gateway request
while keeping repeated conversation payloads out of the dataset. The raw
exporter keeps the complete framework `messages` and gateway messages for every
turn. A turn may instead use `prompt`, `messages`, or `gateway_messages` when
the source capture has those fields directly. Failed or truncated turns stay
in the same `turns` array with their source `status`, response, tool calls,
observations, and checks; do not drop them during dataset preparation.

Every `value` may be a string, number, boolean, null, object, or array. Strings
are shown verbatim; structured values are shown as indented JSON. `inputs`,
`outputs`, and `checks` are display records, so a record without `label` is
still accepted and shown as an indexed item. Stage order and turn order are
preserved. Values and prompts longer than 1,200 characters start collapsed in
native disclosure controls; their original content remains available by
expanding the control.

## Interaction

The fragment shows a compact clickable stage strip. Selecting a stage and then
a turn updates one detail area with four tabs: `输入`, `Prompt 与消息`, `输出`,
and `检查`. The prompt/message tab shows one selected turn at a time so full
messages remain inspectable without copying every turn into a permanent wall
of text. Any model trace thought fields supplied in the dataset are displayed
as model-visible text; the visual does not infer or expose private reasoning.

The checked-in `piper-flow.json` contains the actual recording used by the
fragment. Historical stages are labeled separately from the current run. The flow
records AST/build/interface checks and explicitly reports that no independent
physical validation was run; it does not claim behavioral completeness.
