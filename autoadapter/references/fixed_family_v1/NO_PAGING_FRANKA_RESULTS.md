# Franka complete-file continuation: stopped during Repair

2026-09-09 local date. Code: `8458967`. Diagnostic only. Direct implementation and
independent read-only review; Luna Max was not used.

The user stopped the paid run during Repair. Generate and one real Harness
submission completed; Repair produced no changed Driver and no second submission.
This is evidence of failure-feedback continuation, not successful continuous Repair.

| Capability | Ordinary | Boundary |
|---|---|---|
| A1 positioning | pass | pass |
| A2 Cartesian path | pass | pass |
| A3 gripper | fail | fail |
| A4 controlled contact | fail | fail |
| A5 offset and return | pass | pass |

Overall: **6/10**, all physical integrity checks passed, all ten trial videos
complete. The complete reference suite was not run. Accepted historical Study
was reused with **zero new Study calls**.

## Transport and output evidence

- 30 physical HTTP requests: 29 returned successfully; call 29 was interrupted
  by the user's stop instruction (process exit 130). Its usage is unknown.
- No request timeout occurred. Thirteen successful requests exceeded 120 seconds;
  the longest took 206.957 seconds under the diagnostic's 300-second deadline.
  This establishes that this Opus route can return after 120 seconds; it does not
  establish a server-side 300-second ceiling.
- Actual request call 2 contained complete Study (21,429 characters) and skeleton
  runtime (35,984 characters), exactly equal to the staged files. No paging keys
  or file truncation remained.
- Twelve `write_file` responses omitted required parameters: six during Generate
  and six during Repair. All reported 16,384 output tokens and `tool_calls` finish
  reason. Raw HTTP bodies already lacked the parameters; the local file handler
  did not drop them. The available evidence cannot separate upstream reasoning
  exhaustion from gateway handling of an incomplete upstream response.
- Known new usage: **1,066,069 input + 270,069 output tokens**, estimated
  **$13.290277** at the configuration's proxy prices. This is not a Holistic bill;
  it excludes unknown usage for the interrupted request.

## Repair findings

The current mainline creates one `DriverDevelopmentConversation` outside the
attempt loop. Generate and Repair receive its same messages, session and workspace.
Repair appends `DRIVER_VALIDATION_FEEDBACK_JSON`; the ReAct loop appends it as a
user message. The system instruction remains the same. Call 22 retained the
current revision 6 Driver snapshot and Generate history, summarized two of 22
tool groups under the existing 80k budget, and appended 4,990 characters of
feedback. The exhausted 4,000-step development budget remained in that history.
Repair successfully read the A3/A4 trial diagnostic files (6,001/6,801 characters).

The feedback has a separate weakness: the trusted contract reports binary
failure, while candidate-facing trajectory compaction keeps initial/final samples.
For example, A3's file reports `temporal_passed=false` / `value=0`, not which hold
window failed. Merely appending this file is less informative than an explicit
failed-check detail. Offline replay of the preserved Harness samples found:

- A3: no 0.25-second target hold window; the continuous qualifying sampled window
  was about 0.10 seconds. Final normalized errors were about 0.000863 (limit 0.10),
  and aperture excursion about 31.97 mm (minimum 20 mm).
- A4: sampled contact windows span 0.054/0.094 seconds, below 0.10. Per-step evidence
  has 51 contact steps and approximately 0.10 seconds from first contact to end;
  sparse sampling may cause a false rejection. This needs separate measurement
  review and is not proof of a real hold-duration shortfall. Penetration was only
  0.380/0.538 mm, below 5 mm. No criteria were changed during this run.

AA1 source comparison used repository `981526092/auto-adapter`, commit
`585eb1f1fde33f17f5f9a1e169a18dd41f97b586`: its outer retry injects failed tests'
`detail` into a new Generate/Repair prompt and reuses workspace files; each
`ReactLoop.run` starts a new message list. Its within-loop messages are appended.
AA2 now also preserves conversation history across the Harness boundary.

Other observed issues: whole-file edits repeatedly require returning approximately
37k source characters; incomplete responses receive only generic missing-text
errors. A Generate source check also falsely rejected a local NumPy variable and
dictionary key named `reference` via `probe.py:853–862`; the model renamed these
before the accepted submission. These findings do not justify changing scores.

## Evidence

- [Run result and interruption record](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-no-paging-20260909/stopped_run_result.json)
- [Complete-file delivery check](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-no-paging-20260909/complete_file_delivery_check.json)
- [Model calls](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-no-paging-20260909/franka_panda/model_calls.json)
- [First Harness report](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-no-paging-20260909/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-0/capability_validation_report.json)
- [Submitted Driver](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-no-paging-20260909/franka_panda/candidate/cells/franka_panda/skeleton-assisted/frozen-driver-attempts/attempt-1/driver.py)

The interrupted request's raw record still says `in_progress` but has an end time;
it is preserved unchanged. The separate stop record documents the interruption.
Repair did not complete, so a final pipeline-success report was not produced.
