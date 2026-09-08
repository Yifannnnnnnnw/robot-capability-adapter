# Study-result continuation test: Generate reached, timeout still blocks Driver

The user-requested standalone test ran Franka through Holistic Opus 5 on
2026-09-08. It loaded the accepted Study artifact and entered the existing
Generate implementation directly. The fresh real environment check passed.
Seven model responses succeeded, including a Python check that imported the
skeleton and loaded the real Franka MuJoCo scene. The next request and its
identical retry both timed out before response headers. No model Driver was
written, so Harness and Repair were not reached.

| Item | Observed result |
| --- | --- |
| Script / preparation | `scripts/test_study_continuation.py`, commit `9f14450` |
| Study | Zero new calls or probes; original accepted artifact reused |
| Model and budget | Same Holistic Opus 5 adaptive configuration; Generate 22 / one Repair 22; unchanged physics and request limits |
| Test adjustment | Native history retained within the existing 80,000-character budget; no fixed three-group cutoff |
| Requests | Nine physical sends: seven success, two timeout |
| Timeout durations | 120.015 s original / 120.029 s identical retry |
| Generate tools | Eight completed; one Python check succeeded with zero physics steps |
| Driver / Harness / Repair | None / zero submissions and zero executed cases / not entered |
| Returned usage | 198,337 input / 4,933 output tokens |
| Configured reference estimate | USD 1.226511; old Study excluded, timeout charges unknown |

This is an incomplete synthesis, not a 0/10 capability result. The remaining
`driver.py` is the framework interface stub. Successful model-side scene loading
is not evidence of a generated controller or capability success. Basic physical
control was checked separately by the environment worker, whose video is retained.

The first request body and full diagnostic configuration match the previous
Study-reuse trial. Live request metadata confirms the test retention policy and
the original 80,000-character limit. Before call 0005, all five historical groups
were retained with no summaries. Later history exceeded the budget and older
groups were summarized as intended; the model then re-read the Study first page.
The test removes premature three-group eviction, not all future file rereading.

This establishes that the Study result can enter the downstream generation
process and that the isolated history adjustment executes on the real model
path. It did not eliminate the observed timeout. Both failed sends returned no
HTTP status, response headers or token usage. These records cannot distinguish
upstream inference, gateway queueing or network delay. No model setting, robot,
criterion, instance or budget was changed after candidate failures, and no
further robot or extra retry was dispatched.

All 16 conversation messages, eight tool observations, the successful Python
probe result and the final failure event remain in the Generate evidence.
The cost uses the existing USD 5.50 input / 27.50 output per million token
configuration; it is an estimate of returned usage, not verified Holistic billing.
The two timeouts may have incurred additional charges.

- [Exact script command, scope and five passing focused checks](STUDY_CONTINUATION_TEST_HANDOFF.md)
- [Run summary](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/diagnostic_summary.json)
- [Model calls and usage](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/model_calls.json)
- [Test settings](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/continuation_test_settings.json)
- [Study reuse evidence](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/study_evidence.json)
- [Generate conversation, tools and failure](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/candidate/cells/franka_panda/skeleton-assisted/attempt-0/generation_evidence.json)
- [Environment video](../../runs/diagnostic/fixed-family-v1-holistic-opus5-franka-continuation-test-20260908/franka_panda/environment/probe.mp4)

The result batch owns this report and its handoff link. It adds no runtime
changes. The session implemented and ran this bounded test directly with
independent read-only review; Luna Max was not used. Mainline history defaults,
formal experiments and historical results remain unchanged.
