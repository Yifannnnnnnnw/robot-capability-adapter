# Remaining six robots: Holistic diagnostics

Last evidence read: 2026-09-08 12:38 UTC.

All runs have ended. **All six robots produced an accepted Driver and reached a real Harness submission; none has demonstrated all fixed capabilities passing.** KUKA, Kinova and ANYmal-C completed three submissions; xArm7, A1 and UR5e were interrupted during Repair by API errors. Complete continuous Repair and resubmission are established for KUKA, Kinova and ANYmal-C.

These are diagnostic runs, outside thesis experiments. The user authorized a cheap model for the six robots whose earlier official V4 Flash calls were blocked by balance. All six passed real basic environment checks; full reference capability tests were not required or rerun.

V3.2 and Qwen are separate model conditions and independent conversations. A Qwen run does not resume or replace its interrupted V3.2 run. Fixed robot inputs, thresholds, Study/Generate/Repair turn budgets, three-submission limit and physics budgets were not relaxed.

## Holistic DeepSeek V3.2

| Robot | Accepted Driver | Harness scores by submission | Successful/API requests | Input / output tokens | Result |
| --- | --- | --- | ---: | ---: | --- |
| [Kinova](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/kinova_gen3_robotiq_2f85/candidate/experiment_report.json) | No | Not submitted | 10/11 | 457,234 / 12,090 | study API timeout; incomplete |
| [xArm7](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/ufactory_xarm7/candidate/experiment_report.json) | No | Not submitted | 33/34 | 1,613,846 / 22,733 | generate API timeout; incomplete |
| [UR5e](../../runs/diagnostic/fixed-family-v1-holistic-v32-arms-20260908/universal_robots_ur5e_robotiq_2f85/candidate/experiment_report.json) | Yes | 2/10 | 44/45 | 2,541,562 / 49,633 | repair HTTP 401; incomplete |
| [KUKA](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/kuka_iiwa_14/candidate/experiment_report.json) | No | Not submitted | 6/7 | 208,539 / 3,618 | study API timeout; incomplete |
| [Unitree A1](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/unitree_a1/candidate/experiment_report.json) | No | Not submitted | 14/15 | 357,875 / 17,070 | generate API timeout; incomplete |
| [ANYmal-C](../../runs/diagnostic/fixed-family-v1-holistic-v32-mixed-20260908/anybotics_anymal_c/candidate/experiment_report.json) | No | Not submitted | 32/33 | 1,085,681 / 31,147 | generate API timeout; incomplete |

## Holistic Qwen3 Coder Next

| Robot | Accepted Driver | Harness scores by submission | Successful/API requests | Input / output tokens | Result |
| --- | --- | --- | ---: | ---: | --- |
| [ANYmal-C](../../runs/diagnostic/fixed-family-v1-holistic-qwen-anymal-20260908/anybotics_anymal_c/candidate/experiment_report.json) | Yes | 0/10 → 0/10 → 0/10 | 53/53 | 1,685,812 / 29,499 | Cases remain failed |
| [Kinova](../../runs/diagnostic/fixed-family-v1-holistic-qwen-followup-20260908/kinova_gen3_robotiq_2f85/candidate/experiment_report.json) | Yes | 5/10 → 5/10 → 5/10 | 64/64 | 3,661,295 / 53,451 | Cases remain failed |
| [Unitree A1](../../runs/diagnostic/fixed-family-v1-holistic-qwen-followup-20260908/unitree_a1/candidate/experiment_report.json) | Yes | 5/10 | 42/43 | 1,085,841 / 39,550 | repair API timeout; incomplete |
| [KUKA](../../runs/diagnostic/fixed-family-v1-holistic-qwen-kuka-20260908/kuka_iiwa_14/candidate/experiment_report.json) | Yes | 4/8 → 3/8 → 3/8 | 32/32 | 1,508,804 / 12,863 | Cases remain failed |
| [xArm7](../../runs/diagnostic/fixed-family-v1-holistic-qwen-xarm-20260908/ufactory_xarm7/candidate/experiment_report.json) | Yes | 0/10 | 34/35 | 1,733,384 / 58,626 | repair API timeout; incomplete |

Qwen was used only after V3.2 was interrupted before an accepted Driver. A source file alone is not a success: interface stubs and unsubmitted workspace drafts are excluded from accepted Driver counts. API-interrupted outcomes remain incomplete, including a Repair timeout after a failed submission.

## Concrete findings

- KUKA: final 3/8. A2 boundary takes 4.9 s for a segment with a 4 s limit. A4 penetrates 8.314/8.226 mm against the 5 mm limit and misses the precontact stability window. Both A5 cases throw the candidate's own IK error at 13.388/12.510 mm residual because it selected a tighter 12 mm internal limit. All eight workers finished; two A5 videos are marked incomplete because candidate exceptions stopped the behavior.
- Kinova: final 5/10. Both A2 cases skip the first waypoint (nearest 51.090/43.061 mm against 20 mm). Both A4 cases never reach target contact. A5 boundary achieves enough displacement and returns within tolerance but does not hold the return for 0.5 s. All ten final workers, videos and physical guards pass.
- xArm7: the first submission scores 0/10. Eight movement cases fail on a candidate `np` local-variable scope error before stepping. The two gripper cases step physically but fail capability criteria. The later Repair API timeout does not establish a completed repaired submission.
- ANYmal-C: 0/10 across all three submissions. Final G1 velocity and G2 yaw errors exceed their thresholds; G3 exceeds its 8 s execution budget. G4 misses height and contact limits. G5 recovers attitude and maintains four-foot contact, but height drops by 162.33/174.74 mm against 30 mm, with 9.230/9.576 mm penetration against 5 mm. The G5 control-evidence guards pass.
- Unitree A1: first submission 5/10, then Repair API timeout. G1 velocity, G2 yaw and the G3 boundary terminal hold fail. Both G5 cases pass: recovery in 0.1/0.2 s, maximum height drift 18.876/21.151 mm and penetration 2.926/2.195 mm. Nonzero actuator force is recorded throughout, but ctrl also changed, so these trials alone do not isolate the held-target exception.
- UR5e: first V3.2 submission 2/10, passing the two A2 path cases. A1 ends within position tolerance but holds too briefly. A3 reverses the opening mapping (requests 0.2/0.8, measured about 0.798/0.197). A4 never establishes precontact stability or target contact. A5 misses the outbound hold; the boundary displacement reaches only 4.49 mm against the required 16 mm. All ten workers, physical guards and videos are normal. Repair was interrupted by HTTP 401 on request 45, after 44 successful responses. No second submission was made; the authentication error is distinct from the other request timeouts.

ANYmal-C G5 visual evidence: [first/last-frame comparison](../../runs/diagnostic/fixed-family-v1-holistic-qwen-anymal-20260908/g5_visual_review/anymal_g5_first_last.png), [nominal video](../../runs/diagnostic/fixed-family-v1-holistic-qwen-anymal-20260908/anybotics_anymal_c/candidate/cells/anybotics_anymal_c/skeleton-assisted/attempt-2/capability-validation/videos/anybotics_anymal_c-g5-nominal-r00.mp4), [boundary video](../../runs/diagnostic/fixed-family-v1-holistic-qwen-anymal-20260908/anybotics_anymal_c/candidate/cells/anybotics_anymal_c/skeleton-assisted/attempt-2/capability-validation/videos/anybotics_anymal_c-g5-calibrated_boundary-r00.mp4). Frames show body collapse into a low crouch; millimetre penetration figures come from MuJoCo contact measurements, not visual estimation.

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
| UR5e | Basic environment passed; V3.2 repair HTTP 401; incomplete | 2/10 |
| KUKA | Basic environment passed; Qwen Cases remain failed | 4/8 → 3/8 → 3/8 |
| Unitree A1 | Basic environment passed; Qwen repair API timeout; incomplete | 5/10 |
| ANYmal-C | Basic environment passed; Qwen Cases remain failed | 0/10 → 0/10 → 0/10 |

The five retained rows refer to [earlier results](FOLLOWUP_RESULTS.md), not new successes in this follow-up. The complete fixed-family definition still contains 108 cases across eleven robots; this follow-up targets six robots / 58 cases per complete submission round.

## Usage, artifacts and checks

- V3.2: 139/145 successful responses/requests; reported input **6,264,737**, output **136,291** tokens.
- Qwen: 225/227 successful responses/requests; reported input **9,675,136**, output **193,989** tokens.

Combined: **372 robot API requests, 364 successful responses, seven request timeouts and one HTTP 401**. Reported input is **15,939,873** tokens and output **330,280** tokens. All persisted call records are final; timeout usage is unknown, so these response totals are not a billing statement. Connectivity probes are recorded separately and excluded from robot totals. Pricing in configs is an AWS public reference estimate; Holistic billing rates are unknown.

Per-robot report links retain submitted source, per-case physical measurements, exceptions and videos. [Machine-readable snapshot](HOLISTIC_RESULTS.json) also links stage calls, immutable submitted sources, partial drafts and continuity evidence. [V3.2 setup](HOLISTIC_RUN_HANDOFF.md) and [Qwen setup](QWEN_RUN_HANDOFF.md) give executable commands and condition differences.

Minimal checks passed: three focused entry tests, runtime/manifest matching, actual tool round trips, and real environment workers for each started robot. No private thresholds or candidate code were edited by this review. Implementation commits: `78f8d03`, `420848a`, `9205033`. Current-session implementation and read-only sub-agent review were used; no separate Luna conversation was used.
