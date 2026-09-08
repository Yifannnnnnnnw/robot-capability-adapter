# Franka: Study reused, smaller Generate input, timeout remains

The single-robot diagnostic completed on 2026-09-08 using Holistic Opus 5.
The real basic environment check passed. The accepted prior Franka Study was
reused with zero new Study calls or probes; Generate started a fresh development
session. Seven Generate requests succeeded, then the eighth logical request
and its identical retry both timed out at 120 seconds. The run stopped at the
configured retry limit without producing a Driver or entering Harness/Repair.

| Item | This run |
| --- | --- |
| Fixed inputs | Unchanged A1–A5, ten private cases |
| Study | Reused accepted artifact; six probes marked historical |
| First Generate input | 10,468 tokens, previously 78,352; 86.6% reduction |
| Initial user message | 20,282 characters, previously 187,131 |
| Successful requests | Seven, taking 1.99–9.84 seconds each |
| Final logical request | 120.030-second timeout; identical retry 120.039-second timeout |
| Model Driver / Harness / Repair | None; zero submissions and zero executed cases |
| Returned usage | 170,471 input / 1,371 output tokens |
| Configured cost estimate | USD 0.975293 for returned usage only |

This is an incomplete synthesis diagnostic, not a 0/10 capability score.
The 917-character `files/driver.py` is the framework interface stub. The source
Study's tokens are excluded from the new usage. The two timed-out requests
returned no usage and may have incurred charges. The estimate uses the existing
USD 5.50 input / 27.50 output per million token configuration; actual Holistic
billing is unknown.

## What worked and what remains

The first actual Generate request contains the complete fixed capability design,
public standards and file index. Original tasks and inline full MJCF/skeleton
sources are absent. The model successfully read staged Study and skeleton files.
Study reuse metadata records zero new model calls and no new Study physics.
All eight completed tool observations, 16 conversation messages and the final
model-failure event were preserved. No Generate physics probe was attempted.

The remaining timeouts both occurred before response headers. A smaller input
did not eliminate this failure. The client evidence cannot distinguish upstream
inference, gateway queueing or network delay, and does not establish a provider
root cause.

The real requests also expose a concrete context problem: the existing history
projection keeps only the most recent three groups, then reduces older tool
results to 300-character summaries even when below the character budget
([history split](../../src/autoadapter2/agent_context.py#L574),
[tool summary](../../src/autoadapter2/agent_context.py#L310)). Public file pages
are not canonical workspace artifacts, so their full contents are not retained
in the artifact snapshot. Call 0005 re-reads Study after its first page has been
summarized; call 0006 re-reads the skeleton first page after that page has been
summarized. Call 0007 has lost the skeleton middle page. This mismatch between
paging and the three-group window is directly observable; it is not proof of
the timeout cause. It should be addressed before another paid generation trial.

One earlier turn also passed an undeclared `offset` to `inspect_skeleton`.
That handler returned page zero again because it ignores the extra argument
([handler](../../src/autoadapter2/driver_synthesis/interactive.py#L434)). The
following turn correctly used `read_file` with the offset. The model never
requested the second Study page. No context policy or tool behavior was changed
during this candidate.

## Evidence and batch handoff

- [Machine-readable result](OPUS5_FRANKA_STUDY_REUSE_RESULTS.json)
- [Run summary](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908/diagnostic_summary.json)
- [HTTP calls](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908/franka_panda/model_calls.json)
- [Study reuse evidence](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/study_evidence.json)
- [Generate evidence and conversation](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-0/generation_evidence.json)
- [Environment video](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-study-reuse-20260908/franka_panda/environment/probe.mp4)
- [Exact launch command and scope](OPUS5_FRANKA_STUDY_REUSE_HANDOFF.md)

File-input implementation: `5995cfe`. Study reuse preparation: `7139c9b`.
Four focused runner tests and actual source Study/fixed-design loading passed
before launch. This run exercised the real environment and Holistic model path;
Harness was never reached. Model settings, physics budgets, thresholds and
instances remained unchanged. This result batch owns only these two result
files and the link in the handoff. The session implemented the small reuse
change and ran the diagnostic directly, with independent read-only review;
Luna Max was not used for these batches. No formal experiment or historical
result was altered, and no other robot was dispatched.
