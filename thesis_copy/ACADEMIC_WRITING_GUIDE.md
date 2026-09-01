# Auto-Adapter thesis academic writing guide

Status: **mandatory for prose edits under `thesis/`** unless a direct user,
supervisor, programme, or examination requirement says otherwise.

This guide applies to the MSc thesis. It does not replace
`paper/STYLE.md`, which targets a short conference paper and uses a different
voice, structure, and space budget.

## 1. Purpose and authority

Use this guide to keep the thesis:

- understandable to a technically literate reader encountering Auto-Adapter
  for the first time;
- organised around research questions and evidence rather than repository
  modules;
- precise about what was generated, executed, observed, and judged;
- cautious about simulation, hardware, validation, safety, and generalisation;
- consistent in terminology, spelling, tense, and claim strength.

Apply requirements in this order:

1. UCL programme and module requirements;
2. explicit supervisor or examiner guidance;
3. this guide;
4. local preferences already established in a chapter.

Do not import conference-paper conventions from `paper/STYLE.md` when they
conflict with this guide.

## 2. Reader model

Assume the reader:

- understands general computer science, machine learning, and robotics;
- knows what an API, controller, simulator, and SDK are after expansion at
  first use;
- does **not** know the Auto-Adapter repository, its stage abbreviations, its
  schemas, or its internal ownership model;
- has not read an earlier chapter when reading the abstract;
- will interpret an undefined project term according to its ordinary meaning,
  which may differ from the repository's meaning.

Every abstract and chapter introduction must therefore stand on its own at the
level appropriate to that section. A reader should not need source-code
knowledge to understand the research claim.

## 3. Core writing principles

### 3.1 Lead with the research problem

Present ideas in this order unless the section has a specific reason not to:

1. context;
2. unresolved problem or knowledge gap;
3. research aim or question;
4. method or analytical approach;
5. evidence or result;
6. interpretation and boundary.

Do not begin a research-facing passage with a list of stages, files, agents, or
artefacts. Architecture should appear only after the reader understands why it
is needed.

### 3.2 Define the research object before naming its internals

State in ordinary language what Auto-Adapter produces:

> a reusable, robot-specific Python interface that exposes defined motion,
> sensing, and lifecycle operations to downstream agents or policies

Only then introduce the shorter term *adapter*. Similarly, explain a concept
before introducing its project-specific label.

### 3.3 Prefer concrete meaning to compressed terminology

Use a longer but interpretable phrase when a shorter term would be ambiguous.
For example, prefer:

> robot-description files, approved SDK/API information, and human-defined
> interface and safety requirements

to:

> declarative robot and runtime specifications

The first version tells a new reader what information is actually supplied.

### 3.4 Separate claims that the system separates

Never collapse the following into one notion of "success":

- generating source code;
- passing static source and interface checks;
- satisfying a primitive-level behavioural test;
- completing a downstream task;
- communicating successfully with a transport;
- moving a physical robot as intended;
- establishing deployment safety.

Name the evaluated level whenever using words such as *valid*, *validated*,
*successful*, *working*, or *qualified*.

### 3.5 Make evidence ownership visible when it matters

The central methodological distinction is not merely that tests exist. The
generated adapter must not control the observations, thresholds, or verdict
used to accept itself. When relevant, state:

- who defines the requirement;
- who executes the code;
- who supplies the observation;
- who applies the checker;
- what evidence is retained;
- what happens when evidence is unavailable.

### 3.6 State boundaries with the main claim

Do not postpone all qualifications to a limitations chapter. If a result is
simulator-only, structural, smoke-level, route-specific, or based on privileged
state, say so where the result first appears.

## 4. Terminology for first-time readers

Use the right-hand explanation at first mention. The shorter term may be used
after it has been defined.

| Internal or compressed term | First-reader wording |
| --- | --- |
| adapter / capability adapter | a robot-specific Python interface implementing reusable motion, sensing, and lifecycle operations |
| primitive | a reusable robot operation with a defined input, output, units, limits, and expected outcome |
| primitive contract | a human-authored definition of the operations the adapter must provide and what those operations mean |
| runtime profile | a robot-specific configuration record containing the approved SDK/API surface, units, coordinate conventions, limits, and execution connection |
| runtime specification | avoid alone; name the actual inputs, such as SDK information, configuration, calibration, limits, and route constraints |
| execution route | the simulator, controller, SDK connection, or hardware path through which commands are executed |
| validation obligation | a fixed test requirement derived from a declared operation |
| trusted observation | a measurement supplied independently of the generated adapter and permitted to support a verdict |
| candidate-independent validation | validation whose tests, thresholds, observations, and verdict cannot be changed by the generated candidate |
| adapter-owned failure | a failure attributed to generated adapter code rather than missing hardware, observation, route, or validation infrastructure |
| blocked | not evaluated because a required execution, observation, reset, or safety capability is unavailable |
| evolution | post-validation evidence analysis or controlled improvement experiments; do not imply autonomous deployment or self-modification |
| tool synthesis | creation of the reusable robot-facing software tool itself, rather than use of an existing tool |

Avoid unexplained stage abbreviations such as `RP`, `EN`, `GE`, `VA`, and `EV`
in the abstract. In the main text, introduce an abbreviation only if it is used
repeatedly and materially improves readability.

## 5. Canonical conceptual distinctions

These distinctions must remain explicit throughout the thesis.

### 5.1 Tool use is not tool synthesis

A model may call an existing adapter successfully while failing to generate an
adapter that satisfies the same interface and behavioural requirements.
Results about downstream tool use do not, by themselves, support a driver- or
adapter-synthesis claim.

### 5.2 An adapter is not a task policy

The generated artefact implements reusable capabilities. It is not a
task-specific program such as `pick_banana()`, a high-level planner, a
vision--language--action policy, or a general autonomy system.

### 5.3 A registry entry is not an executable integration

Keep these statuses separate:

1. registered profile;
2. logical validation plan compiled;
3. execution route available;
4. behavioural tests executed;
5. independent observations available;
6. adapter qualified for the tested conditions;
7. hardware deployment reviewed.

Never convert a profile count into a claim about executable or validated robot
integrations.

### 5.4 Transport success is not behavioural success

A command receipt or successful API call establishes communication, not that
the intended motion occurred. Behavioural claims require an appropriate
observation of the resulting state.

### 5.5 Simulation is not hardware evidence

Simulator state can support simulator-grounded behavioural claims. It does not
establish calibration accuracy, latency tolerance, contact reliability,
physical safety, or sim-to-real transfer.

### 5.6 A validation pass is not a safety certificate

A pass supports conformance only for the declared requirements, observations,
thresholds, initial conditions, and execution route. Do not use *safe*,
*certified*, *guaranteed*, or *deployment-ready* unless independent evidence
directly supports that term.

## 6. Voice, tense, and point of view

### 6.1 Default voice

Use formal, direct, argument-led prose. Active voice is acceptable and often
clearer than passive voice. The default thesis framing is:

- `This thesis investigates ...`
- `The study evaluates ...`
- `Auto-Adapter generates ...`
- `The validator records ...`

Avoid switching casually between `I`, `we`, `this work`, and `the thesis`.
Use first person only if the supervisor or programme explicitly prefers it.

### 6.2 Tense

- **Present tense:** stable definitions, thesis arguments, system behaviour,
  and what a figure or table shows.
- **Past tense:** experiments performed, data collected, and implementation
  decisions made during the study.
- **Present perfect:** a continuing research trend where the time span matters;
  do not use it as a default substitute for the past tense.

Examples:

> Auto-Adapter separates code generation from validation.

> The experiment evaluated seven models on the same fixed adapter.

> Table 4 reports task-level pass rates.

### 6.3 British English

Use British spelling consistently:

- `synthesise`, `organisation`, `behaviour`, `artefact`;
- `judgement`, `authorised`, `normalisation`, `analyse`.

Do not change spelling inside code, API names, titles, or direct quotations.

## 7. Sentence and paragraph style

### 7.1 Sentences

- Put the grammatical subject and main verb early.
- Express one principal claim per sentence.
- Prefer concrete verbs over noun-heavy constructions.
- Aim for roughly 15--25 words per sentence; treat this as a readability
  target, not a mechanical rule.
- Split a sentence when it crosses more than two conceptual layers.
- Use parallel grammatical forms in lists.
- Use a semicolon only when the relationship between two complete clauses is
  clearer than it would be in separate sentences.

Avoid long noun stacks such as:

> runtime-profile-grounded contract-obligation execution evidence

Rewrite them as relationships:

> execution evidence for test requirements derived from the runtime profile
> and primitive contract

#### Avoid inventory sentences

Do not substitute a long series of short nouns or noun phrases for an
explanation. A grammatically correct inventory can still hide the relationship
among the items and make the prose sound formulaic. Group details by their
function, state why they matter, and use another sentence when the relationship
cannot be expressed clearly in one.

Avoid:

> Each capability is defined by its semantics, inputs and outputs, units and
> reference frames, preconditions, and measurable acceptance obligations.

Prefer:

> A capability states what the operation means and when it may be requested.
> Its contract fixes the conventions needed to interpret the request and
> explains how the outcome will be judged.

Retain an exhaustive noun-list sentence only when completeness is necessary,
as in a formal specification. Submit every such exception to the thesis author
for individual review before it enters the final text.

### 7.2 Paragraphs

Each paragraph should perform one rhetorical function. A strong analytical
paragraph often follows this sequence:

1. claim or topic sentence;
2. explanation or mechanism;
3. evidence or example;
4. interpretation;
5. qualification or transition, where needed.

Do not make every paragraph follow this sequence mechanically. Use it to
diagnose paragraphs that contain background, method, result, and conclusion
without a clear centre.

### 7.3 Signposting

Use signposting to express logical relationships, not to narrate document
management.

Prefer:

> This distinction matters because transport acceptance does not establish
> physical motion.

Avoid:

> In the following section, we are going to talk about the important issue of
> transport acceptance.

## 8. Claim strength and evidential language

Choose verbs according to the strength of the evidence.

| Wording | Appropriate use |
| --- | --- |
| `reports`, `observes`, `finds` | direct description of the measured result |
| `shows` | the stated relationship follows directly within the evaluated conditions |
| `supports` | evidence is consistent with a bounded claim but is not exhaustive |
| `indicates` | evidence is limited, indirect, or descriptive |
| `suggests` | interpretation is plausible but uncertainty or alternatives remain |
| `demonstrates` | reserve for direct and adequately repeated evidence of the exact claim |
| `proves`, `guarantees` | normally inappropriate for empirical system evaluation |

Do not write `significant` to mean large or interesting. Use it only with a
reported statistical test, or replace it with the measured magnitude.

Avoid marketing language:

- groundbreaking;
- revolutionary;
- remarkable;
- seamless;
- robust, unless robustness was explicitly tested;
- general, universal, or autonomous, unless the evaluation warrants it.

Use one hedge, not a hedge stack. Prefer `suggests` to `may potentially
possibly indicate`.

## 9. Section-specific requirements

### 9.1 Abstract

The abstract must stand alone. Draft it as continuous prose, not an inventory
of experiments, contributions, stages, or chapter numbers.

Use the following moves:

1. **Context:** what broader capability exists?
2. **Gap:** what necessary problem remains unresolved?
3. **Aim:** what does the thesis investigate?
4. **Method:** what is generated, from which concrete inputs, and how is it
   judged independently?
5. **Results:** what are the most decision-relevant quantitative findings?
6. **Conclusion and boundary:** what do the findings support, and what do they
   not establish?

Avoid citations unless a programme convention requires them. Avoid internal
stage abbreviations. Include at most the terms a reader needs to understand the
research question and headline result.

UCL's published doctoral guidance specifies a maximum of 300 words. This is
not treated here as a confirmed MSc rule; until the MSc handbook or supervisor
provides a different limit, use 250--300 words as a conservative working
target.

### 9.2 Introduction

The introduction should establish the research space rather than reproduce the
abstract at greater length. It should:

- motivate the embodiment-execution problem;
- synthesise enough literature to establish the gap;
- define the adapter and other central terms;
- state the overarching question and subordinate research questions;
- explain the research programme and contribution;
- state scope and claim boundaries;
- orient the reader to the chapter structure.

Do not present a contribution before explaining the gap it addresses.

### 9.3 Background and literature review

Organise literature around claims, tensions, and missing connections, not one
paper per paragraph. For each cluster of work:

1. state the relevant idea;
2. compare representative approaches;
3. identify what they assume or leave unresolved;
4. connect that limitation to the thesis question.

Distinguish criticism of a method from absence of evidence. Use cautious
language when proposing reasons for another study's result.

### 9.4 System and methods chapters

Describe the research design, not merely the package tree. For every major
component, clarify:

- input;
- output;
- authority and mutable surface;
- assumptions;
- failure behaviour;
- relation to the research question.

Repository paths may support reproducibility, but they should not replace a
conceptual explanation.

### 9.5 Evaluation

Define the experimental unit, condition, baseline, metric, sample size, seed or
randomisation policy, success threshold, and evidence source before reporting
comparisons. Separate synthesis evaluation from downstream task evaluation.

When reporting numbers:

- give the denominator or sample size;
- include units;
- distinguish mean, variation, and range;
- identify whether a result is trial-level, task-level, robot-level, or
  model-level;
- report negative and mixed results with the same precision as positive ones.

### 9.6 Results

Present results systematically. State the observation before its explanation.
Refer to the relevant table or figure and highlight only the data needed for
the claim. Reserve extended causal explanations for the discussion unless the
chapter intentionally combines results and discussion.

### 9.7 Discussion

Organise the discussion around research questions or major findings. A
discussion cycle should normally:

1. restate the bounded result;
2. interpret it;
3. compare it with prior work or an alternative condition;
4. consider alternative explanations;
5. state implications;
6. identify limitations.

### 9.8 Conclusion

Answer the research questions directly. Do not introduce new experiments,
claims, or literature. Distinguish what the thesis established, what it
indicated, and what remains open.

## 10. Auto-Adapter evidence and source hierarchy

Before writing an empirical claim, verify it against the strongest available
source:

1. trial-level raw result and validation artefacts;
2. `paper/results/canonical.yaml` for historical benchmark numbers;
3. current semantic harness reports for current runtime-harness claims;
4. implementation tests for code behaviour;
5. README or design documents for intended architecture only.

A design document, profile registry, planned experiment, or passing unit test
is not automatically empirical evidence for robot behaviour. Do not copy a
number from prose when a canonical machine-readable result exists.

When historical simulator experiments and the current runtime-aware harness
use different executors or validation standards, describe them as distinct
evidence layers. Do not imply that archived benchmark results were produced by
the current executor unless provenance establishes this.

## 11. Preferred and avoided formulations

### 11.1 Explaining the software boundary

Avoid:

> This assumes that a robot-specific software layer already exists to translate
> high-level operations into low-level motor controller or vendor SDK.

Prefer:

> An agent or policy can operate a particular robot only through a
> robot-specific interface. This interface translates requested operations
> into platform-specific API calls or control commands understood by the
> simulator, robot controller, or vendor software development kit (SDK).

### 11.2 Stating the input

Avoid:

> The adapter is generated from declarative robot and runtime specifications.

Prefer:

> The adapter is generated from robot-description files, approved SDK/API
> information, and human-defined interface and safety requirements.

### 11.3 Reporting validation

Avoid:

> The system validates the generated driver.

Prefer:

> Static checks assess source and interface constraints, while behavioural
> tests judge observed state changes against fixed requirements.

### 11.4 Reporting onboarding

Avoid:

> Auto-Adapter validated eight robots.

Prefer the level actually supported, for example:

> Auto-Adapter produced structurally valid, smoke-tested simulator drivers for
> eight robot models.

### 11.5 Reporting unavailable evidence

Avoid:

> The hardware test failed.

when the hardware or independent observer was unavailable.

Prefer:

> The test was not executed because the route could not provide the independent
> joint-state observation required for a behavioural verdict.

## 12. LaTeX and presentation conventions

- Preserve labels, citations, and cross-references when revising prose.
- Use `robot--runtime` for an en dash in LaTeX compound relationships.
- Use `vision--language--action` where an en dash links equal terms.
- Use `\texttt{}` for API methods and file names in LaTeX prose.
- Expand an acronym at first use in the abstract and again in the main text if
  substantial distance makes this useful.
- Do not define an acronym used only once or twice.
- Place citations immediately after the claim or synthesis they support.
- Do not attach a citation to a paragraph if it is unclear which claim it
  supports.
- Ensure figures and tables are interpreted in the prose; do not merely state
  that they exist.

## 13. Editing workflow

For every substantial thesis edit:

1. identify the paragraph's rhetorical purpose;
2. verify factual and numerical claims against authoritative artefacts;
3. write for the first-time reader before introducing internal terminology;
4. check that synthesis, validation, execution, and downstream use remain
   distinct;
5. calibrate claim verbs to the evidence;
6. add the relevant scope or limitation beside the claim;
7. remove stage inventories, noun stacks, repetition, and meta-commentary;
8. check British spelling, tense, acronym expansion, citations, and LaTeX;
9. read the paragraph without surrounding context to test intelligibility;
10. compile the thesis or report any environment-level build blocker.

## 14. Final review checklist

Before accepting a passage, answer yes to the relevant questions:

- Is the research problem stated before the architecture?
- Can a first-time reader identify what is generated?
- Are all project-specific terms defined at first use?
- Are the concrete input sources named rather than hidden behind
  `specification` or `runtime`?
- Does every use of `validated` state or imply the validation level?
- Are tool synthesis and downstream tool use kept separate?
- Are simulator, transport, and hardware evidence distinguished?
- Is each empirical claim traceable to a result artefact?
- Are numbers accompanied by denominators, units, and experimental level?
- Is causal language supported by the design?
- Are limitations stated where they constrain interpretation?
- Does the paragraph contain one central claim?
- Can any abbreviation, noun stack, or internal stage name be removed?
- Does the conclusion stay within the evaluated conditions?

## 15. Sources informing this guide

Last reviewed: 21 July 2026.

- UCL, *Thesis preparation, submission and publishing*. The published doctoral
  guidance specifies an abstract of no more than 300 words and notes that
  discipline-specific main-body requirements should follow local guidance:
  <https://www.ucl.ac.uk/study/doctoral-school/regulations/essential-procedures-and-policies/thesis-preparation-submission-and-publishing>
- University of Manchester, *Academic Phrasebank: Introducing work*. This
  identifies common thesis moves including context, research gap, aims,
  methods, significance, definitions, and structure:
  <https://www.phrasebank.manchester.ac.uk/introducing-work/>
- University of Manchester, *Academic Phrasebank: Reporting results*. This
  distinguishes systematic result reporting from extended interpretation:
  <https://www.phrasebank.manchester.ac.uk/reporting-results/>
- University of Manchester, *Academic Phrasebank: Discussing findings*. This
  describes discussion cycles and cautious language for explanations and
  implications:
  <https://www.phrasebank.manchester.ac.uk/discussing-findings/>
- University of Edinburgh, *How to Write an Effective Abstract*. This treats an
  abstract as a stand-alone miniature of the larger work containing background,
  purpose, methods, results, and conclusion:
  <https://www.docs.hss.ed.ac.uk/iad/Researchers/Research_staff/Study_Guide_How_to_Write_an_Effective_Abstract_v2.0_.pdf>

These sources inform the rhetorical principles. The Auto-Adapter terminology,
evidence hierarchy, and claim boundaries above are project-specific rules
derived from the thesis and repository.
