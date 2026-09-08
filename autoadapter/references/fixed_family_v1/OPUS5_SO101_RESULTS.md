# Opus 5 SO-101: interrupted during Study

The first single-robot trial ended on 2026-09-08. SO-101 passed its real basic
environment check, but the fifth Study model request exceeded the configured
120-second client total wall deadline. No accepted Study artifact, generated
Driver, Harness submission or Repair was produced. The other ten robots were
not started with Opus 5. This is an incomplete diagnostic, not a capability fail.

## Observed execution

| Item | Result |
| --- | --- |
| Condition | Holistic `eu.anthropic.claude-opus-5`, adaptive thinking requested, native tool history |
| Submission budget | First generation plus at most one Repair; maximum two Harness submissions |
| Environment | Ten reset checks and the real native actuator worker passed |
| Model requests | 5 Study requests: 4 successful responses, then 1 timeout |
| Successful request durations | 10.52, 17.22, 12.16 and 21.41 seconds |
| Interrupted request | 120.03 seconds; no response usage |
| Driver / Harness / Repair | Not reached |

The four successful model responses requested `execute_python`. The interrupted
Study did not retain execution-result evidence, so these requests alone are not
claimed as four successful physical probes. The separate basic environment
worker does establish real physical control response (0.02890 rad).

## Returned usage and reference cost

| Scope | Input tokens | Output tokens | Public regional price estimate |
| --- | ---: | ---: | ---: |
| Successful SO-101 Study requests | 211,554 | 4,446 | $1.285812 |
| Successful two-request protocol probe | 1,007 | 78 | $0.007684 |
| **Combined known usage** | **212,561** | **4,524** | **$1.293496** |

These estimates use USD 5.50 input / 27.50 output per million tokens, with no
assumed cache discount. This is public Opus 5 pricing plus the published 10%
regional premium, not a verified Holistic charge. Cache and reasoning breakdowns
were unreported. Reasoning is not added again to the reported output total.
The timed-out request and two earlier HTTP 401 authentication probes have no
returned usage; their eventual charges are unknown.
[Public price reference](https://platform.claude.com/docs/en/about-claude/pricing).

## Evidence and handoff

- [Run summary](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-20260908/diagnostic_summary.json)
- [Environment check](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-20260908/robotstudio_so101/environment/environment_check.json)
- [Model calls](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-20260908/robotstudio_so101/model_calls.json)
- [Cell report](../../runs/diagnostic/fixed-family-v1-holistic-opus5-so101-20260908/robotstudio_so101/candidate/cells/robotstudio_so101/skeleton-assisted/cell_report.json)
- [Machine-readable result](OPUS5_SO101_RESULTS.json)
- [Configuration and launch instructions](OPUS5_SO101_HANDOFF.md)

No candidate, private standard, instance or budget was changed after the run.
The timeout is observed at the client; these records do not identify why the
upstream response failed to arrive within the deadline. Additional robots remain
undispatched while this first trial's interruption and cost are reported.

Preparation commits: `05c7c3a` (fixed Study instructions) and `46646a0` (single-robot
configuration). Four focused checks and six subtests passed; the real environment
worker and native Opus 5 tool round trip also passed. Work was done in the current
session with read-only agent review; no separate Luna conversation was used.
