# Rebuttal preparation (internal, not for submission)

Pre-drafted responses to the six most likely reviewer attacks, with
pointers to where the paper already answers them. Tone for the real
response: concede what is true, cite the section that already
discloses it, quantify.

## 1. "This is robotics, not NLP deployment" (venue fit)

Concede the embodied setting; reframe to what the paper measures:
the engineering cost of the tool layer that every tool-using LLM
agent needs (title, abstract first sentence). The language
technology under test is LLM-agent orchestration plus code
generation across seven LLMs and five vendors; the closest accepted
precedents in this track are Lango & Dušek 2025 (LLM agents write an
NLG system from scratch, no deployment) and Rayfield et al. 2025
(language agents on industrial physical systems). The CFP explicitly
welcomes "novel applications," "development under practical
constraints," and "negative results, lessons learned" (Appendix K is
deployment lessons).

## 2. "Sim only; will not survive real hardware" (deployment gap)

Do not argue: agree and point to Appendix K, which reports our own
physical bring-up attempt and decomposes the failure into four gaps
(servo reproducibility, hand-eye calibration, close-range depth,
contact sensing), plus the boundary claim that the gaps are
LLM-independent. The paper never claims sim-to-real transfer
(Limitations bullet 1). If pressed on value: the algorithmic layer
(kinematics, control, primitive API) is what synthesis automates,
and that layer is also needed by any hand-written driver.

## 3. "Novelty over CodeAct / SWE-agent / test-driven repair"

The loop structure is shared and is cited as such (Sec. 1 design
choices). The deltas are stated in Sec. 1 and Sec. 2.2: (i) output
is a reusable driver API rather than a per-task script; (ii) input
is a formal MJCF/URDF spec; (iii) the verifier signal is post-step
simulator state, which cannot be satisfied by well-formed text (the
unit-test contrast in Sec. 2.2). The tool-use-vs-synthesis
dissociation (only 3/7 models write a passing driver while 7/7
operate one) is an empirical finding none of those systems report.

## 4. "Spec mode vs self-contained mode is confusing / hides templates"

Point to Table tab:provenance (Appendix A), which maps every result
block to its driver mode and any post-synthesis human code (one
13-line perception patch, disclosed in Sec. 5.5). The headline
onboarding numbers are self-contained mode (no skeleton import,
AST-checked). Spec mode exists so RL baselines can read the same
joint interface and is used only for Sec. 5.1/5.2 and as the frozen
reference driver in Sec. 5.3.

## 5. "N=5 is too small / significance overstated"

Agree on per-cell resolution and point to Appendix M (statistical
significance): the claim rests on the task-level paired Wilcoxon
(n=91 cells, p<0.01); the model-level test is reported as
underpowered; per-cell, only the two largest gaps clear
non-overlapping Wilson intervals and the paper says differences
below ~15 points are not separately resolvable. We do not claim
per-model significance anywhere.

## 6. "Validation is lenient / 8/8 is inflated"

The paper discloses this in four places before any reviewer finds
it: abstract ("structural plus smoke-level behavioral checks"),
Sec. 2 Validate paragraph, Appendix A, and the Limitations bullet.
Appendix A now also reports a STRICT RE-VALIDATION: the released
physics-graded harness re-run on the same eight drivers gives 8/8
on every structural test, arms failing only a perception-API check
added after synthesis, and quadrupeds failing 1-2 behavioral tests
each with exact numbers (Go2 sit 0.28 m vs 0.22 threshold, A1 walk
-2.7 cm, ANYmal stand 0.61->0.47 m and walk 0.0 cm) -- 0/8 fully
passing the released harness, all failures concentrated in
quadruped locomotion, exactly where the paper said the leniency
was. Downstream capability numbers never use that harness; they
are graded by the benchmark's independent physics evaluators.

## Submission-form notes

- Suggested area/keywords: LLM agents and tool use; system design,
  efficiency and scalability; novel applications; benchmarks.
- Supplementary: upload supplementary.zip (videos incl. one honest
  failure, ten drivers, canonical results); build with
  scripts/build_supplementary.py.
- Artifact link: insert the anonymous.4open.science URL in place of
  the two "(link redacted for review)" sites once created.

## 7. "Same-API CaP comparison tests agent-loop style, not synthesis"

Concede the loop-style difference is real and disclosed (CaP is
single-shot by construction, Sec. 5.1 caption). Then reframe: the
load-bearing claims do not depend on A-A beating CaP. They are
(i) the synthesized API is operable by all seven models including
8B open-weight ones; (ii) synthesizing the API is much harder than
using it (3/7 vs 7/7); (iii) physics-grounded synthesis is feasible
under scoped controller priors at ~$3/robot. The A-A-vs-CaP margin
is a secondary observation about closed-loop repair, and the one
place CaP wins (Qwen) is reported plainly.

## 8. "Model names look future-dated"

All model identifiers are real AWS Bedrock endpoints current at
submission time (June 2026); Appendix F lists exact IDs and
May-2026 list pricing. Nothing is anonymized or placeholder.
