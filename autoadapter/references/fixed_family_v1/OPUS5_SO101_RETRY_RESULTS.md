# SO-101 retry: Study passed, Generate interrupted

The single SO-101 diagnostic finished on 2026-09-08. Its real basic environment
check and Study passed. Generate's first request and its one permitted retry
both reached the unchanged 120-second client deadline before response headers
arrived. No model Driver, Harness submission or Repair was completed. The other
ten robots were not dispatched. This is an incomplete diagnostic, not a robot
capability score.

| Stage | Observed result |
| --- | --- |
| Environment | Real native actuator and ten reset checks passed; video retained |
| Study | Eight successful model responses; accepted `study.json` |
| Study physical probes | Two successful probes, with 500 and 1,500 steps respectively |
| Study budget | Fourth probe reached the original 4,000-step / 20-second limit; its failure remained visible; no extra steps were granted |
| Fifth Study model request | Completed in 92.765 s; returned 7,376 output tokens and requested `write_file` |
| Generate | First logical turn: original send timed out at 120.014 s; retry timed out at 120.023 s |
| Retry behavior | Same request body; one shared timeout retry consumed; no repeated tool execution |
| Driver / Harness / Repair | No model Driver, zero Harness submissions, no Repair |

The two Generate bodies contain the initial system/user messages and no old tool
history or summarized groups. Therefore old-history compression is not necessary
for the observed timeout symptom. Both sends remained at
`awaiting_response_headers`; neither produced an HTTP status, request ID or usage.
These records cannot establish whether network delay, gateway queueing or upstream
inference caused the wait. The long successful Study response shows a request
can approach the configured limit, but does not prove the previous run's cause.

The real interruption retained Generate's original `ModelInvocationError`, its
initial conversation and a `model_call_failed` trace. The HTTP layer saved each
projected request before sending. Successful responses also retain selected
gateway request/trace IDs. The framework-created `files/driver.py` interface stub
is not counted as a model-generated Driver. Study evidence and its five tool
probe results remain available after the later Generate failure.

## Returned usage and cost

| Scope | Input tokens | Output tokens | Public regional price estimate |
| --- | ---: | ---: | ---: |
| This retry's eight successful requests | 445,890 | 13,248 | $2.816715 |
| Both trials plus the earlier successful protocol probe | 658,451 | 17,772 | $4.110211 |

The estimate uses the configured USD 5.50 input / 27.50 output per million tokens,
without an assumed cache discount. It is not a verified Holistic bill. Cache and
reasoning breakdowns were unreported. The two timeout requests in this run have
unknown usage; the earlier trial's timeout and rejected authentication probes
also retain unknown charges. [Public price reference](https://platform.claude.com/docs/en/about-claude/pricing).

## Evidence and implementation

- [Machine-readable result](OPUS5_SO101_RETRY_RESULTS.json)
- [Run summary](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/diagnostic_summary.json)
- [HTTP calls](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/model_calls.json)
- [Original Generate request](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/model_requests/call-0008.json)
- [Identical Generate retry](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/model_requests/call-0009.json)
- [Study evidence](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/candidate/cells/robotstudio_so101/skeleton-assisted/study_evidence.json)
- [Generate failure evidence](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/candidate/cells/robotstudio_so101/skeleton-assisted/attempt-0/generation_evidence.json)
- [Environment video](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-retry-20260908/robotstudio_so101/environment/probe.mp4)
- [Conditions, owned files and checks](OPUS5_SO101_RETRY_HANDOFF.md)

Implementation commits: `7f1cf0a` (HTTP evidence and opt-in retry), `1d39b2f`
(gateway trace IDs), `0bec852` (interrupted Study/Generate/Repair evidence).
Luna Max implemented the latter in a sub-session; root reviewed the diff, ran
the focused checks and performed this actual model/MuJoCo diagnostic. A scripted
Study interruption test verified failed-Study persistence; this real run verified
successful Study retention and Generate interruption persistence with bounded
same-request retry. It did not demonstrate continuous Driver Repair.

No robot package, instance, threshold, thinking setting, turn budget or physics
budget changed during the run. Further retries were not launched. The remaining
blocker is the model request path; investigate the gateway's supported request
duration and provider-side logs before another paid synthesis attempt.
