# Paper writing style — MANDATORY for all paper/*.md edits

Submission target: **EMNLP 2026 Industry Track** (deadline 2026-06-16
AoE, 6-page main + unlimited appendix, double-blind).

Style derived from two close-to-our-paper accepted samples at
EMNLP 2025 Industry Track:

- **Lango & Dušek 2025** — "LLM Agents Implement an NLG System from
  Scratch" (https://aclanthology.org/2025.emnlp-industry.142/)
- **Rayfield et al. 2025 (IBM)** — "ReAct Meets Industrial IoT:
  Language Agents for Data Access"
  (https://aclanthology.org/2025.emnlp-industry.24/)

**Every edit to `paper/*.md` MUST match the rules below.** Read this
file before writing.

---

## 1. Macro structure (6-page main)

Recommended skeleton, in order:

| § | Title (descriptive, not generic) | Words | Notes |
|---|---|---:|---|
| Abstract | — | 120–200 | pain → system → headline number → scope → code URL |
| §1 | Introduction | ~700 | concrete worked example in first 5 sentences; flowing-prose contributions (no numbered list); related work folded into final paragraph |
| §2 | Auto-Adapter Pipeline | ~900 | system/method merged here. Figure 1 + Algorithm 1 |
| §3 | Robot zoo and evaluation protocol | ~300 | 8 robots + task suites |
| §4 | Onboarding cost across 8 robots | ~300 | core production figure ($/min/100%) |
| §5 | Capability evaluation | ~900 | 5–6 short subsections, one aspect each |
| §6 | Deployment scoping | ~300 | where it works / current ceiling / lesson learned |
| §7 | Summary | ~150 | recap + release URL + one future-work pointer |
| Limitations | (mandatory) | 50–250 | bulleted, honest, OK to admit incompleteness |

**Total ~4,000–4,500 words** for body, well under 6-page limit.

**Section naming rule:** descriptive, never generic.
- ✅ "Auto-Adapter Pipeline"
- ❌ "Method"
- ✅ "Onboarding cost across 8 robots"
- ❌ "Results"
- ✅ "Deployment scoping"
- ❌ "Limitations" (this one stays as "Limitations" — required header)

**Related Work placement:** fold into last paragraph of §1 Intro
(IBM-style). Saves a page. Standalone §RW only if comparison density
demands it (we don't).

---

## 2. Voice and sentence-level patterns

### MUST do

- **First-person plural, active, present tense.** "We present", "We
  introduce", "We compare".
- **Open with a concrete worked example** within first ~5 sentences.
  - Our example: "You have a new SO-ARM101's MJCF and want a tool-
    using LLM to drive it. Today: write driver (engineer-days), or
    finetune a VLA (10–100 demos), or wait for RoboNeuron. Our
    pipeline: $2.98, 8 min, MJCF in / driver out."
- **Short paragraphs**, 3–5 sentences each.
- **Numbers always anchored.** "$2.98 per robot", "20/70 trials",
  "168 lines of code", not bare numbers.
- **Hedged claims** when honest. "competitive with X on metric Y",
  not "state-of-the-art".
- **Failures in main text**, not buried. Quote a Lango pattern:
  "evaluation on the GEM datasets produced some errors as the
  generated programs were not robust enough to handle differences
  in date formatting." Match this honesty level.
- **Cite at end of paragraph**, grouped. Not one citation per
  sentence.
- **Practitioner takeaway** at end of each result subsection.
  Italic or bold sentence: *"Haiku 4.5 + Ours at $0.23 is the
  cheapest config with both 75% physics and the lower over-claim
  rate."*

### MUST NOT do

- ❌ "We make the following N contributions:" — flowing prose only.
- ❌ "State-of-the-art" / "remarkable" / "breakthrough" /
  "significant" without a number attached.
- ❌ "Significantly outperform" with no statistic — replace with
  "consistently outperforms by N pp" or "in K of L conditions".
- ❌ Heavy hedge stacks: "may potentially possibly indicate" — pick
  one hedge word, drop the rest.
- ❌ Long meta-paragraphs: "In this section we describe how we
  evaluate X." Just describe X.
- ❌ Citation forests: 3+ citations per sentence in body prose.
  Group at end of paragraph.
- ❌ "(see Fig. 1)" — write "As shown in Figure 1, ...".
- ❌ Marketing voice: "Our novel approach revolutionizes..." — drop
  the adjectives; let the table speak.

---

## 3. Industry-track-specific moves

These show up in BOTH accepted samples and are expected by Industry
Track reviewers:

1. **Decision tables in §2 or end of §1.** "Today, if you need
   LLM-on-new-robot, your options are A/B/C with engineering-day /
   data / dollar costs." Make the reader's choice visible.

2. **Algorithm boxes for pipeline pseudocode.** LaTeX `algorithm`
   environment. Both samples use them.

3. **Decision-relevant ablations.** Multi-model cost spectrum
   (Haiku $0.23 vs Sonnet 4.5 $0.70) is more useful than
   best-model-only.

4. **Code release URL in both abstract and intro.** Anonymous repo
   for submission, deanonymize at camera-ready.

5. **Real-domain terminology used naturally** (we use "MJCF",
   "DLS-IK", "ee_pose"). IBM uses "SCADA", "AHU", "SkySpark".
   Demonstrates we have actually deployed in the domain.

6. **Lessons learned section is welcomed.** Our §6.2.1 methodology
   bug → "align eval target with what `get_ee_pose` returns
   first" is exactly the kind of practitioner-takeaway content
   Industry Track explicitly accepts (CFP: "negative results,
   lessons learned").

---

## 4. Length budget per section

Hard limits — going over forces appendix relocation:

```
Abstract        150 words
§1 Intro        700 words   (60% pain, 40% approach + folded RW)
§2 Pipeline     900 words   (incl. Figure 1, Algorithm 1)
§3 Protocol     300 words   (table-heavy)
§4 Onboarding   300 words   (single table + reading + takeaway)
§5 Capability   900 words   (5 subsections × ~180w + tables)
§6 Scoping      300 words   (3 short subsections)
§7 Summary      150 words
Limitations     200 words   (bulleted)
TOTAL          ~3,900 words
```

Compare to ACL/LREC formatting: ~250–300 words per single-column
page or ~500–600 per double-column page. 3,900 words ≈ 6.5 pages
ACL double-column with tables/figures, fits in 6.

---

## 5. Formatting & LaTeX conventions

- Section names sentence case: "Auto-Adapter pipeline", not "Auto-
  Adapter Pipeline" (matches ACL house style).
- Numbers in body: spell out 0–9 except when paired with units
  ("8 robots" stays; "we present 3 contributions" → "we present
  three contributions"). When in tables, all digits.
- Models: full Anthropic name on first mention ("Claude Sonnet 4.5"),
  short form afterward ("Sonnet 4.5").
- Costs: 2 decimals with $ sign ("$2.98").
- Tolerances: cm not m for distances under 1 m ("2 cm tolerance",
  "53.85 cm").
- Inline code: `move_cartesian`, `get_ee_pose`.
- References: ACL natbib style (`\citep{}` and `\citet{}`).

---

## 6. Sample paragraph (use as voice reference)

This is the level of directness + concreteness to match:

> "Across eight embodiments — 5-DoF SO-101 to 12-DoF ANYmal-C — the
> pipeline produces validated drivers in 7.8 ± 2.4 min at
> $2.98 ± $0.84 per robot, with 100% structural validation pass
> (Table 1). The synthesis output is a self-contained Python
> module that any tool-using LLM can drive without further
> integration."

Note: number with sd, table reference, single concrete claim, no
hedge stack, no adjectives.

---

## 7. Pre-merge checklist for every paper/*.md PR

Before merging an edit:

- [ ] No numbered "Contributions:" list anywhere
- [ ] No "state-of-the-art" / "novel" / "remarkable" / "significant"
      without a number
- [ ] No "we propose to propose" / "may potentially" stacks
- [ ] Numbers always have units / context
- [ ] Failures admitted in main text (not just in Limitations)
- [ ] Practitioner takeaway present at end of each result section
- [ ] Section name is descriptive, not generic
- [ ] Citations grouped at paragraph end, not per sentence
- [ ] Code/repo URL placed in abstract + intro
- [ ] Word count under section's hard limit (§4 above)

If any of the above fails, revise before merging.
