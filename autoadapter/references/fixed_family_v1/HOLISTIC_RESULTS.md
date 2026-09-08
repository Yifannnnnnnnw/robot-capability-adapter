# Remaining six robots: Holistic diagnostics

Last evidence read: 2026-09-08 12:29 UTC.

These are diagnostic runs, outside thesis experiments. The user authorized a cheap model for the six robots whose earlier official V4 Flash calls were blocked by balance. All six passed real basic environment checks; full reference capability tests were not required or rerun.

V3.2 and Qwen are separate model conditions and independent conversations. A Qwen run does not resume or replace its interrupted V3.2 run. Fixed robot inputs, thresholds, Study/Generate/Repair turn budgets, three-submission limit and physics budgets were not relaxed.

## Holistic DeepSeek V3.2

| Robot | Accepted Driver | Harness scores by submission | Successful/API requests | Input / output tokens | Result |
| --- | --- | --- | ---: | ---: | --- |
| [Kinova](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/kinova_gen3_robotiq_2f85/candidate/experiment_report.json) | No | Not submitted | 10/11 | 457,234 / 12,090 | study API timeout; incomplete |
| [xArm7](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/ufactory_xarm7/candidate/experiment_report.json) | No | Not submitted | 33/34 | 1,613,846 / 22,733 | generate API timeout; incomplete |
| [UR5e](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/universal_robots_ur5e_robotiq_2f85/environment/environment_check.json) | Yes | Not submitted | 36/36 | 1,978,715 / 37,521 | Running; stage evidence may lag |
| [KUKA](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/kuka_iiwa_14/candidate/experiment_report.json) | No | Not submitted | 6/7 | 208,539 / 3,618 | study API timeout; incomplete |
| [Unitree A1](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/unitree_a1/candidate/experiment_report.json) | No | Not submitted | 14/15 | 357,875 / 17,070 | generate API timeout; incomplete |
| [ANYmal-C](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/anybotics_anymal_c/candidate/experiment_report.json) | No | Not submitted | 32/33 | 1,085,681 / 31,147 | generate API timeout; incomplete |

## Holistic Qwen3 Coder Next

| Robot | Accepted Driver | Harness scores by submission | Successful/API requests | Input / output tokens | Result |
| --- | --- | --- | ---: | ---: | --- |
| [ANYmal-C](../../runs/diagnostic/fixed-family-v1-holistic-qwen-anymal-20260908/anybotics_anymal_c/candidate/experiment_report.json) | Yes | 0/10 → 0/10 → 0/10 | 53/53 | 1,685,812 / 29,499 | Cases remain failed |
| [Kinova](../../runs/diagnostic/fixed-family-v1-holistic-qwen-followup-20260908/kinova_gen3_robotiq_2f85/candidate/experiment_report.json) | Yes | 5/10 → 5/10 → 5/10 | 64/64 | 3,661,295 / 53,451 | Cases remain failed |
| [Unitree A1](../../runs/diagnostic/fixed-family-v1-holistic-qwen-followup-20260908/unitree_a1/environment/environment_check.json) | Yes | 5/10 | 26/26 | 589,798 / 17,308 | Running; stage evidence may lag |
| [KUKA](../../runs/diagnostic/fixed-family-v1-holistic-qwen-kuka-20260908/kuka_iiwa_14/candidate/experiment_report.json) | Yes | 4/8 → 3/8 → 3/8 | 32/32 | 1,508,804 / 12,863 | Cases remain failed |
| [xArm7](../../runs/diagnostic/fixed-family-v1-holistic-qwen-xarm-20260908/ufactory_xarm7/candidate/experiment_report.json) | Yes | 0/10 | 34/35 | 1,733,384 / 58,626 | repair API timeout; incomplete |

Qwen was used only after V3.2 was interrupted before an accepted Driver. A source file alone is not a success: interface stubs and unsubmitted workspace drafts are excluded from accepted Driver counts. API-interrupted outcomes remain incomplete, including a Repair timeout after a failed submission.

## Concrete findings

- KUKA: final 3/8. A2 boundary takes 4.9 s for a segment with a 4 s limit. A4 penetrates 8.314/8.226 mm against the 5 mm limit and misses the precontact stability window. Both A5 cases throw the candidate's own IK error at 13.388/12.510 mm residual because it selected a tighter 12 mm internal limit. All eight workers finished; two A5 videos are marked incomplete because candidate exceptions stopped the behavior.
- Kinova: final 5/10. Both A2 cases skip the first waypoint (nearest 51.090/43.061 mm against 20 mm). Both A4 cases never reach target contact. A5 boundary achieves enough displacement and returns within tolerance but does not hold the return for 0.5 s. All ten final workers, videos and physical guards pass.
- xArm7: the first submission scores 0/10. Eight movement cases fail on a candidate `np` local-variable scope error before stepping. The two gripper cases step physically but fail capability criteria. The later Repair API timeout does not establish a completed repaired submission.

The observed continuation evidence is reported per robot below; it requires prior failure feedback, existing history, changed source and another Harness submission, not merely entering the Repair stage.

| Qwen robot | Completed continuous Repair and resubmission |
| --- | --- |
| ANYmal-C | Yes |
| Kinova | Yes |
| Unitree A1 | Not established |
| KUKA | Yes |
| xArm7 | Not established |

## Eleven-robot checklist

| Robot | This Holistic follow-up | Latest retained model evidence |
| --- | --- | --- |
| SO101 | Not rerun | Earlier V4 Flash generation failed before Harness |
| Go2 | Not rerun | Earlier V4 Flash: 7/10 → 6/10 → 6/10 |
| Piper | Not rerun | Earlier V4 Flash: 0/10 → 6/10; later Repair artifact budget exhausted |
| Franka | Not rerun | Earlier V4 Flash: 0/10 → 6/10; final Repair interrupted by HTTP 402 |
| Barkour | Not rerun | Earlier V4 Flash: 2/10; Repair interrupted by HTTP 402 |
| Kinova | Basic environment passed; Qwen Cases remain failed | 5/10 → 5/10 → 5/10 |
| xArm7 | Basic environment passed; Qwen repair API timeout; incomplete | 0/10 |
| UR5e | Basic environment passed; V3.2 Running; stage evidence may lag | No Harness submission |
| KUKA | Basic environment passed; Qwen Cases remain failed | 4/8 → 3/8 → 3/8 |
| Unitree A1 | Basic environment passed; Qwen Running; stage evidence may lag | 5/10 |
| ANYmal-C | Basic environment passed; Qwen Cases remain failed | 0/10 → 0/10 → 0/10 |

The five retained rows refer to [earlier results](FOLLOWUP_RESULTS.md), not new successes in this follow-up. The complete fixed-family definition still contains 108 cases across eleven robots; this follow-up targets six robots / 58 cases per complete submission round.

## Usage, artifacts and checks

- V3.2: 131/136 successful responses/requests; reported input **5,701,890**, output **124,179** tokens.
- Qwen: 209/210 successful responses/requests; reported input **9,179,093**, output **171,747** tokens.

Active-stage calls, where present, are not yet fully persisted. Timeout usage is unknown; these response token totals are not a billing statement. Connectivity probes are recorded separately and excluded from robot totals. Pricing in configs is an AWS public reference estimate; Holistic billing rates are unknown.

Per-robot report links retain submitted source, per-case physical measurements, exceptions and videos. [Machine-readable snapshot](HOLISTIC_RESULTS.json) also links stage calls, immutable submitted sources, partial drafts and continuity evidence. [V3.2 setup](HOLISTIC_RUN_HANDOFF.md) and [Qwen setup](QWEN_RUN_HANDOFF.md) give executable commands and condition differences.

Minimal checks passed: three focused entry tests, runtime/manifest matching, actual tool round trips, and real environment workers for each started robot. No private thresholds or candidate code were edited by this review. Implementation commits: `78f8d03`, `420848a`, `9205033`. Current-session implementation and read-only sub-agent review were used; no separate Luna conversation was used.
