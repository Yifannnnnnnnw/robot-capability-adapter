Audit the verified source run globally. Inspect at least the source,
accounting/budget, validation, repair, video, and compiler-candidate evidence;
inspect public prompt/schema/source files only when they materially test a
hypothesis.

Submit `robot_capability.evolution_audit.v1` with architecture
`evidence_grounded_audit_synthesize_judge_publish`. Rank 2–8 findings with
contiguous ranks, select one finding, and create one Experience claim. The
claim must include:

- `conclusion_kind`: positive, negative, or unresolved;
- SO-ARM101 scope;
- a bounded symptom and falsifiable hypothesis;
- alternatives considered;
- one recommended change for a future run;
- a concise Generation-facing summary;
- a future test with an action, supporting result, and falsifying result;
- evidence refs returned by tools;
- the fixed epistemic flags: verified source=true, framework change=false,
  capability improvement=false, new Generation/Validation required=true;
- all privacy flags=false.

For a negative claim, include at least one cited `candidate:*` ref whose
projection says `confirmed_failure`; for a positive claim, use
`confirmed_success`. If the chosen issue is supported only by terminal,
accounting, validation-summary, or repository observations, mark the claim
`unresolved`. Claim refs must be a subset of the selected finding's refs. Use
robot ID `soarm101` and runtime ID `lerobot_soarm101_0_6_0`. Submit by turn 20 and use any remaining
turns to correct schema feedback.

`confirmed_failure` supports only the observed negative outcome. It does not
localize a transformation, timing, contact, calibration, prompt, or code root
cause. Label mechanism hypotheses and recommended remedies as unverified
future tests. Repository excerpts marked changed or not recorded for the
source run may support a present-day framework observation, but not a claim
about the historical generated package.

Do not choose a named issue in advance. Let the verified evidence determine the
ranking. Do not write or apply any candidate patch.
