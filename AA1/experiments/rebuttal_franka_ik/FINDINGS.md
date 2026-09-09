# Franka oracle IK ablation — findings (rebuttal add-on, K6sW Q2)

Deterministic oracle (scripted move_cartesian(exact target), fresh skel no
home(), same 7 variants / 2cm tol / measurement as llm_franka_proper_reach_n10).
No LLM anywhere.

## Results
| arm | pass | note |
|---|---|---|
| stock DLS driver          | 2/7 | reproduces the published signature exactly: 2-4cm on x-5/y+-5/diag, 11.07cm IK-fail on z-5 |
| stock, 5x iterations      | 2/7 | identical errors -> not iteration budget |
| nullspace-regularized DLS | 2/7 | fixes x-5 (0.13cm) but loses x+5 (2.21cm); y+- still 3.2cm; z-5 still fails |
| stock + gravity-comp exec | 3/7 | diag passes; y+- unchanged -> partially execution-layer, not pure gravity (in result.json as arm "gravcomp") |

## Decomposition (stock)
| variant | IK residual | exec settling | total |
|---|---|---|---|
| y+5 | 0.02cm | 2.74cm | 2.73cm FAIL |
| y-5 | 0.02cm | 2.75cm | 2.74cm FAIL |
| diag | 0.02cm | 2.05cm | 2.04cm FAIL |
| x-5 | 1.10cm | 2.90cm | 3.95cm FAIL |
| z-5 | IK cannot reach (boundary/limits) | — | 11.07cm FAIL |

Root signal: wrist joint j5 settles ~11 deg away from its commanded value
(back at its default 11 deg), near a light self-contact; IK itself solves
y+-/diag to 0.2mm (x-5 is mixed: 1.10cm IK residual + execution error). So
three of the four 2-4cm cases are EXECUTION-layer (actuator tracking /
posture selection near default config), one is mixed, and the 11cm case is
a true IK boundary failure. Determinism check: stock and stock-5x produce
bit-identical per-variant errors.

## Rebuttal-grade conclusions
1. AGENT EXONERATED: the exact published failure signature reproduces with
   zero LLM involvement -> failures are deterministic driver-template
   behavior, not synthesis noise, not the agent. (Directly answers K6sW:
   "solver choice or synthesis errors?" -> neither stochastic synthesis
   errors nor the agent; the prescribed control template.)
2. REFINEMENT of Sec.6 wording: "generic DLS solver chooses poorly" should
   broaden to "prescribed arm-control template (IK posture selection at the
   workspace boundary + execution-layer tracking)": solver swap alone does
   not repair it (2/7), and the dominant 2-4cm class is execution, not solve.
3. Consistent with the paper's cross-robot evidence: same template gets
   sub-cm on SO-101/Piper/UR5e (lighter, non-redundant arms) -> ceiling is
   embodiment-specific template behavior, the paper's stated "current
   ceiling," now with a precise mechanism.
