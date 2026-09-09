# Rebuttal draft — EMNLP 2026 Industry Track #187 (v5, fully anchored)

v5: every factual claim now carries a precise paper anchor (§ / Appendix /
Table / Figure), verified against the SUBMITTED PDF's numbering via
main_final.aux (identical to the revision for all cited anchors; table
numbers ≥16 shifted in the revision, so none are cited). Content that
exists only in the revision is explicitly marked "(revision)".
BUGFIX from the anchor pass: aerial tie is Appendix L, not J.

Anchor map used (submitted PDF): §2.1 five phases; §2.2 verifier;
§2.3 modes; §2.4 anti-hard-coding lint; §4 onboarding; §5.1–5.4; §6
scoping; §7 summary; Table 2 cross-skill; Table 3 portability;
Table 15 Franka per-variant; Figure 2 synthesis traces; Figure 3 hero;
Appendix B strict-validation audit; Appendix J task graders;
Appendix K statistics (K.2 Franka); Appendix L morphology/aerial;
Appendix M physical bring-up.

Format: text-only, no links, no score-begging. Post: one global comment +
one per review thread.

---

## 0) Global comment

We thank all reviewers for careful, constructive reviews.

Two clarifications recur, so we state them once, with paper locations.
(1) Scope: the paper claims a *simulator-side* synthesis pipeline; it makes
no sim-to-real claim (§1 "Our proposal"; Limitations "Sim only"), and the
strict-validator discrepancy is disclosed in our own Limitations
("Lenient behavioral validation") with the full per-robot audit in
Appendix B. (2) Design: the same-API Code-as-Policies baseline drives the
*same frozen synthesized driver* as our agent (§5.3, Table 3 "Uses API"
column), so the downstream comparison isolates agent style *given* the
driver; driver synthesis is evaluated separately (§4 scoreboard; §5.3
synthesis ablation and Self track, Table 3 "Writes API").

In addition, prompted by Reviewer K6sW's question, we ran a small
deterministic ablation on the Franka failures; results in that thread, and
added as Appendix K.2 in the revision.

---

## 1) Reviewer EwJm (Overall 2.5)

We thank the reviewer; all three concerns are fair, and we respond to each
within the paper's stated scope.

**1. Human-in-the-loop LLM-coding baseline.** We agree this is a relevant
comparison and we want to be precise about what we do and do not claim: we
did not study human-assisted coding workflows, and we do not claim to beat
them. The paper's contribution is (a) a fully automated, rerunnable, logged
pipeline producing a structurally validated, smoke-tested driver at a fixed
cost profile — $2.98 ± $0.84 and 7.8 ± 1.6 min per robot (§4; per-robot
detail in Appendix B) — and (b) the cross-model dissociation that all
seven tested LLMs *operate* a synthesized API while only three *write* a
validated driver (§5.3, Table 3). We recognize (b) does not answer the
reviewer's cost question; it is a second, model-level finding that stands
independently of workflow choice. On cost, we can defend a narrower
statement than superiority: automation repeats each additional robot at a
logged marginal cost, while human-assisted effort also amortizes —
quantifying that crossover is precisely what the requested study should
measure, and we agree it is the right experiment. The cost claim is framed
against hand-authoring practice (abstract: "hand-authored, at days to weeks
of effort per robot"; §1), not against assisted workflows; the revision
makes the boundary explicit with a "No human-in-the-loop baseline"
limitation stating we do not claim to beat such workflows, and states the
controlled human+copilot onboarding study as open work there and in
Appendix M's future-work paragraph.

**2. Safety of write–execute–revise.** We agree this discussion belongs in
the paper, and we thank the reviewer for prompting it. Two factual points
first. (1) In this paper the loop never touches hardware by design: all
generation, execution, and revision happen in MuJoCo (§2.1–2.2), and the
exported driver is a static, auditable artifact (§2.1 "Export"). (2) Our
only physical bring-up session used conservative manual operation with
z-bounds and joint-limit checks in the bridge (Appendix M). For any
physical extension we consider at minimum the following *requirements* (to
be stated as such, not as existing features): non-overridable driver-level
clamps on joint limits, velocity, and torque; a workspace bounding volume
enforced outside generated code; sim-first re-validation of any revised
driver before hardware execution; collision/force monitoring with automatic
stop; a hardware e-stop; and a human approval gate with staged slowed
rollout for the first execution of any newly generated driver. This now
appears as a safety paragraph in Appendix M (revision), referenced from
Limitations.

**3. Positioning.** We largely agree: the contribution is LLM-agent
synthesis of the robot-facing software layer, verified against simulator
physics — robotics-oriented software synthesis, not a broad
embodied-intelligence claim. The abstract and Limitations already restrict
claims to simulator drivers; the revision aligns the introduction's
rhetoric with that scope (§1 "Our proposal" now states "robotics-oriented
software synthesis" explicitly).

**4. Question: pretrained priors vs. embodiment reasoning.** Honest answer:
both, and we can partially separate them. Structure must come from the
spec: the validator's anti-hard-coding lint rejects drivers with hard-coded
joint counts or limb labels, and the same prompt and skeleton produce
drivers for a 5-joint SO-101 and a 12-joint ANYmal-C (§2.4). Controller
*classes* are human-prescribed (§2.1 "Study"), and models plainly draw on
pretrained IK/PD idioms — stated as the "Algorithm-class prior" limitation.
Two observations suggest priors alone are insufficient: under identical
prompts only three of seven models produce a validated driver (§5.3,
Table 3), and trace analysis shows every successful synthesis required at
least one physics-feedback error recovery — 73% of loop iterations executed
code; no run succeeded in one shot (Figure 2, §2.2). The differentiating
capability appears to be spec-grounded debugging against physical state,
not recall of control-code patterns. The revision adds this discussion to
the "Algorithm-class prior" limitation.

---

## 2) Reviewer cnSr (Overall 3.5)

We thank the reviewer for an accurate reading — and we accept both points.

**1. The 8/8 onboarding claim vs. the strict validator's 0/8.** We agree,
and we state the strict outcome plainly first: **under the released strict
harness, 0/8 robots fully pass** (Limitations "Lenient behavioral
validation"; full per-robot audit in Appendix B). The original behavioral
grading was genuinely lenient for the quadrupeds (walk graded at smoke
level; one sit accepted that did not lower the body — Appendix B) — that is
a real leniency issue, not a phrasing issue, and it is why we built the
stricter harness and disclosed the 0/8. The precise breakdown (Appendix B):
all eight robots keep every structural test; the five arms fail a
scene-perception check that was added after the synthesis runs; the three
quadrupeds fail one to two physics-graded locomotion tests. No downstream
§5 capability number depends on the lenient checks: the §5 results are
graded by independent per-task physics criteria (§3; grader definitions in
Appendix J). In the revision we correct the presentation: (a) the
strict-harness 0/8 now appears in §4's opening paragraph, directly next to
the 8/8 scoreboard, (b) the headline reads "structurally validated,
smoke-tested drivers" throughout (abstract, §1, §7), and (c) §4's
practitioner takeaway is qualified to "structural onboarding" — i.e.,
reliable structural driver generation, with behavioral certification an
explicitly open gap. This matches the reviewer's own reading of the
evidence.

**2. Synthesis vs. planner conflation.** The design separates them — the
same-API baseline drives the *same frozen synthesized driver* (§5.3,
Table 3), so Auto-Adapter vs. CaP measures agent style *given* the driver —
but we agree the presentation blends the two questions, and the revision
fixes that: §5 now opens by stating which question each subsection answers,
and §5.3 explicitly credits the Auto-Adapter-vs-CaP gap to the multi-turn
planner, not to driver quality. Appendix L reports the boundary case where
the planner advantage vanishes: on the aerial suite both agent styles reach
100% because point-to-point flight is single-shot solvable, and that
appendix credits the synthesized flight controller, not the agent loop.
Driver synthesis itself is evaluated on separate axes (§4 scoreboard; §5.3
using-vs-synthesizing ablation and Self track, Table 3). One dependence we
state explicitly: the §5.4 quadruped capability runs execute on drivers
that fail one to two strict locomotion tests (Appendix B); their task
grading remains independent per-task physics (Appendix J).

---

## 3) Reviewer K6sW (Overall 3.5, Soundness 4)

We thank the reviewer for the thorough review. Responses to the concerns
and the three questions:

**1. Privileged state / RGB.** Correct and intentional scope, disclosed as
the "Privileged observation" limitation. The perception primitives are
ordinary driver methods — `get_object_position`, `get_ee_pose` (§2.1) — so
the abstraction is designed to be swappable: replacing privileged getters
with RGB-D estimators changes the driver's implementation, not the task
interface or the benchmark. We claim no RGB capability anywhere in the
paper; a perception-grounded variant on physical hardware (wrist-mounted
RGB-D) is our concrete next step, now stated in the "Privileged
observation" limitation (revision).

**2. Franka: solver choice or synthesis errors?** Prompted by this question
we ran a deterministic ablation that removes the LLM entirely — added as
Appendix K.2 in the revision, next to the existing per-variant breakdown
(Table 15): a scripted oracle issues the exact Cartesian target for the
same seven variants under the same methodology and 2 cm tolerance. Three
findings. (i) The oracle reproduces the published failure signature exactly
(2/7; 2–4 cm errors on the same four directions; 11.1 cm on z-5) with no
LLM in the loop — so the failures are deterministic driver-template
behavior, not agent errors and not synthesis noise (determinism is checked
empirically: the stock and 5×-iteration arms produce bit-identical
per-variant errors). (ii) Neither a 5× iteration budget nor a
nullspace-regularized DLS variant repairs it (both 2/7), so a stronger
solver configuration alone is not the fix either. (iii) A per-variant
decomposition shows two distinct mechanisms: z-5 is a true IK failure at
the workspace/joint-limit boundary, while three of the four 2–4 cm cases
have sub-millimeter IK residuals (the fourth, x-5, is mixed at 1.1 cm) and
lose accuracy in the execution layer — the wrist joint settles ≈11° from
its commanded value in these reaches, which stay near the arm's default
configuration; consistent with this, gravity-compensated execution of the
same stock IK solutions recovers one variant (3/7). This does not change
the reported failure pattern or the ceiling claim; it narrows the cause
from solver choice alone to the broader prescribed arm-control template.
Accordingly, §6's attribution is refined in the revision from "the DLS
solver chooses poorly" to "the prescribed control template" — a more
precise statement of the same scoping claim, consistent with the same
template achieving sub-centimeter reach on the non-redundant arms (SO-101,
Piper, UR5e): the ceiling is specific to the redundant 7-joint embodiment.

**3. Per-model N=5.** We agree per-model significance is underpowered, and
the paper is explicit about it: Appendix K reports the per-model Wilcoxon
as non-significant (n=7, p=0.15) and rests the claim at the task level — a
Wilcoxon signed-rank over the 91 paired model×task cells gives p<0.01, and
the 12-task physics-only subset preserves the full ranking (Appendix K).
We claim an aggregate lead, not per-model wins, and the revision makes that
framing explicit in §5.3.

**4. Scaling physical validation.** Our roadmap, now an explicit
future-work paragraph in Appendix M (revision): perception-grounded
reach/place on a physical SO-ARM101 with a wrist-mounted RGB-D camera,
executed under the safety requirements in our response to Reviewer EwJm
(Appendix M safety paragraph), taking the four bring-up gaps documented in
Appendix M as the concrete work items.

---

## Posting checklist
- [x] LEADERBOARD.json 4/7 -> 3/7 fixed on main + release + 4open mirror.
- [x] All anchors verified against main_final.aux (submitted PDF numbering).
- [ ] Confirm the Industry Track author-response window is open (PC email).
- [ ] Text only; backticks/bold OK, no URLs.
- [ ] Post global first, then per-reviewer replies in-thread.
- [ ] Do not mention the AutoEval spike or unpublished experiments
      (the Franka oracle ablation IS mentioned — complete, in
      experiments/rebuttal_franka_ik/ and revision Appendix K.2).

---
# POSTED STATUS (24 Jul 2026)
All four comments are live on OpenReview forum z5Nt4gLMdO:
- Global response from the authors (note uINxyBqm4h)
- Response to Reviewer EwJm (note 4or7BcAqDc; edited twice post-submit)
- Response to Reviewer cnSr (note OLT9QcFPC1)
- Response to Reviewer K6sW (includes a new ablation for Q2)

As-posted deltas vs the v5.1 text above (apply if re-deriving):
1. ALL em-dashes removed (— replaced with commas/colons/parentheses/periods).
2. "$2.98 ± $0.84" -> "$2.98 ± 0.84" (two $ pair up as MathJax on
   OpenReview and swallow the amounts; single $ renders literally).
3. Plain-language pass: "prompting it"->"raising it" equivalent phrasing;
   "dissociation" kept only where load-bearing; "rhetoric"->"wording";
   "IK/PD idioms" kept in EwJm (mirrors reviewer's own words);
   "embodiment"->"arm" in K6sW closing; verbless fragments given verbs
   ("Correct and intentional scope"->"This is a deliberate scope choice";
   "Our roadmap, now..."->"Our roadmap is now...").
4. K6sW anchor fix: perception primitives cited at §2.2 (not §2.1).
5. cnSr: "qualified to"->"now says"; "One dependence we state explicitly"
   ->"One dependence worth stating explicitly"; em-dash pair around the
   frozen-driver clause -> colon + "Still, we agree...".
6. "model×task" -> "model-by-task" (K6sW), "5×" -> "5x", "2–4" -> "2-4",
   "≈11°" -> "about 11 degrees" (avoid symbol-render risk in comments).

---
# CaP-X ADDITIONS POSTED (24 Jul 2026)
Codex-reviewed (POST WITH EDITS), then posted via edits to two threads only:
- Global comment: added concurrent-work paragraph (ENPIRE + CaP-X, arXiv
  2603.22435, 12 models; "we synthesize the layer they assume/ablate").
- EwJm response point 1: added CaP-X citation with the verbatim finding
  ("performance improves with human-crafted abstractions but degrades as these
  priors are removed, exposing a dependence on designer scaffolding") + Codex-
  required limiter ("concerns dependence on a hand-crafted interface layer, not
  a human-in-the-loop coding comparison"). EwJm trimmed to 4940 chars (<5000).
Deliberately NOT added to cnSr or K6sW (K6sW: avoid foregrounding CaP-X's
RGB-D perception tiers / human-88.5% number against our privileged-state scope).
Generic-agent proxy experiments (experiments/rebuttal_generic_agent/) were NOT
posted: both the UR5e reach and the Piper grasp runs came back favoring a strong
generic agent (grasp: held object, reproduced the weld-relpose fix in ~5 min),
so they were kept internal as red-team evidence; CaP-X (12-model, third-party)
carries the abstraction-layer point instead.
For camera-ready: add CaP-X + ENPIRE to Related Work as "assumes/ablates the
layer we synthesize."
