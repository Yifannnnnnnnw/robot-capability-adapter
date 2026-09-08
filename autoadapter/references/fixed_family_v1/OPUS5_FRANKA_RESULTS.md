# Franka Panda: Study passed, Generate interrupted after timeout recovery

The user-requested single Franka Panda diagnostic finished on 2026-09-08.
Its real basic environment check and Study passed. Generate's first request
timed out, but its identical retry succeeded in 119.979 seconds. The next model
turn succeeded; the third logical Generate turn timed out. The one shared timeout
retry had already been used, so the run stopped. No other robot was dispatched.

| Item | Actual result |
| --- | --- |
| Fixed inputs | A1–A5, ten defined private cases; original criteria and instances |
| Environment | Real control check and all reset checks passed; video retained |
| Study | Accepted after 13 successful model requests |
| Study probes | Six records; two clean physical probes with 1,000 and 2,500 steps |
| Study budget | One probe used the final 500 steps; a later step was rejected after the 4,000-step limit; both failures retained |
| Generate, logical turn 1 | Original send: 120.012 s timeout; identical retry: 119.979 s success |
| Generate, logical turn 2 | Success in 21.753 s |
| Generate, logical turn 3 | 120.008 s timeout; no further retry |
| Generate tools | Four completed calls: skeleton listing, skeleton inspection and two Python checks; the Python checks advanced zero physics steps |
| Model Driver / Harness / Repair | None; zero submitted or executed Harness cases |

This is an incomplete diagnostic, not a 0/10 capability score. The framework's
interface-only `files/driver.py` stub is not a model-generated Driver. No model
candidate was accepted or frozen for Harness validation.

## What the run establishes

The successful retry resubmitted exactly the same Generate request body. It
continued the existing development session without rerunning Study or prior
tools. The final interruption retained seven conversation messages, four completed
tool trace entries, one model-failure trace entry and both Generate Python check
results. The original `ModelInvocationError` classification remained intact.

Both timed-out sends stopped before response headers arrived; neither returned
HTTP status, provider request ID or token usage. The successful retry shows that
the same request can return, and its 119.979-second duration is extremely close
to the configured 120-second limit. A longer supported request duration warrants
investigation, but client records still cannot distinguish upstream inference,
gateway queueing or network delay. This does not establish an upstream root cause.

## Returned usage and reference cost

There were **17 physical requests: 15 successes and 2 timeouts**. Successful
responses reported **908,912 input and 46,171 output tokens**, giving a configured
public-price estimate of **$6.268719**. Including both earlier SO-101 trials and
the successful Opus protocol preflight, returned usage across these Opus pilots
has a **$10.378929** reference estimate. Other historical model runs are excluded.

The configured USD 5.50 input / 27.50 output per million tokens is a public
regional estimate, not a verified Holistic bill. Cache and reasoning breakdowns
were not reported, and no cache discount is assumed. Both timeout requests have
unknown usage and may have incurred charges. [Public price reference](https://platform.claude.com/docs/en/about-claude/pricing).

## Evidence and handoff

- [Machine-readable result](OPUS5_FRANKA_RESULTS.json)
- [Run summary](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/diagnostic_summary.json)
- [HTTP calls](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/model_calls.json)
- [Study evidence](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/study_evidence.json)
- [Generate failure and retained conversation](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-0/generation_evidence.json)
- [Original Generate request](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/model_requests/call-0013.json)
- [Successful identical retry](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/model_requests/call-0014.json)
- [Final interrupted request](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/model_requests/call-0016.json)
- [Environment video](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-20260908/franka_panda/environment/probe.mp4)
- [Configuration, owned files and launch command](OPUS5_FRANKA_HANDOFF.md)

Preparation commit: `ce4c4d7`. Only the robot and experiment ID differ from the
SO-101 config; loader checks passed before the real environment/model run.
The current session prepared the small configuration batch and performed the
run, with independent read-only evidence review; no new Luna implementation
session was used. No runtime code, robot asset, criterion, instance, thinking
setting or budget changed during this run. It exercised timeout recovery, but
did not demonstrate Driver synthesis completion or continuous Repair.
