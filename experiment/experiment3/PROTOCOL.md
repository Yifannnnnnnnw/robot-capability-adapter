# Experiment 3 protocol — declared eleven-configuration cohort

This protocol implements `EXPERIMENT_3_AUTHORITY.md`. It is a direct,
experiment-grade run recipe, not a new lifecycle or readiness state machine.

## Matrix and fixed inputs

The formal matrix is exactly:

```text
11 robot configurations × 3 replicates × 1 condition × 1 model = 33 cells
```

The robot IDs are:

```text
robotstudio_so101
unitree-go2-stock-12dof
franka_panda
kinova_gen3_robotiq_2f85
ufactory_xarm7
universal_robots_ur5e_robotiq_2f85
piper
kuka_iiwa_14
leap_hand
hello_robot_stretch_2
aloha_2
```

Each ID is crossed with exactly `r01`, `r02`, and `r03`. Every cell uses the
same declared run contract but its own fresh workspace, canonical reset,
TGCD output, IVC output, private validation suite, candidate, trace, and
evidence. The run condition is `skeleton-assisted` only. There is no
from-scratch counterpart and no model/backbone factor.

The only model is Sonnet 4.6, `eu.anthropic.claude-sonnet-4-6`, with
temperature `0.0`, context limit `1,000,000` tokens, and maximum output
`16,384` tokens. The provider route, endpoint/region, transport, timeout,
retry rule, dated price snapshot, and returned identity are fixed in the
manifest before the first formal call. A provider or package failure blocks
the affected cell without shrinking the denominator.

Every cell begins with empty Experience. No prior cell may write an Experience
record, and no cell may read another cell's candidate, design, suite, report,
video, or trace.

## Per-cell execution sequence

For each robot and replicate, execute the following in one isolated run:

```text
public Morphology + >=20 source-backed tasks
       -> Sonnet TGCD (fresh for this cell)
       -> implementation-blind IVC (fresh for this cell)
       -> Sonnet STUDY
       -> skeleton-assisted GENERATE
       -> trusted capability validation
       -> bounded Repair, at most two after attempt 0
       -> final capability verdict
       -> Task Demo (only for an admitted final driver)
       -> stop
```

TGCD must design five to ten reusable capabilities without receiving a
pre-authored capability catalogue or task-to-capability mapping. IVC must be
implementation-blind and compile the complete private capability-validation
suite before Driver Synthesis. The IVC suite remains fixed across the three
submitted-driver attempts in that cell, but is not reused by another
replicate. Candidate execution uses the canonical Direct-MuJoCo scene,
actuator control, real physics stepping, isolated worker, and trusted Harness.

Attempt `0` is the initial submitted driver. Attempts `1` and `2` are the only
possible Repair submissions. Development probes and rejected pre-submission
checks do not consume the three-submission budget. Task Demo is a separate
five-task verdict with separate videos; it cannot trigger same-run Repair.

If the final driver is not admitted, record a truthful Task Demo not-run reason
and stop that cell. Do not substitute a reference driver or convert a
candidate/controller self-report into a pass.

## Explicit stop and exclusion rules

Evolution is disabled for every cell. Do not call an Evolution model, create a
proposal, request human disposition, produce an Experience snapshot, or launch
a later run from this experiment. After the Task Demo verdict (or its truthful
not-run reason), the cell is terminal for this protocol.

No cell may be counted twice, added to compensate for failure, or removed
because a model, package, provider, Harness, or video path failed. An
infrastructure failure is recorded visibly and remains in the 33-cell
denominator; it is not relabelled as a model-synthesis failure.

## Required cell record and descriptive analysis

Every cell record retains:

- robot/configuration ID, public morphology label, package and Task Library
  snapshots, replicate ID, run ID, manifest/protocol revision, and condition;
- exact Sonnet model/provider identity and settings, request IDs, model calls,
  token categories, cost inputs, and wall time;
- fresh TGCD and IVC traces and artifact identities;
- skeleton inspection and generation trace proving the condition;
- attempt count, initial/final capability-validation verdicts, private case
  outcomes, Repair transitions, terminal failure class, and Task Demo verdict
  or not-run reason; and
- complete Framework-controlled videos and video-manifest entries for every
  required capability-validation and Task Demo case/repetition.

The report must state `33` as the denominator and show all 33 cell IDs, even
when a cell is blocked or non-evaluable. Aggregate tables may be grouped by
configuration and may show morphology labels as descriptive strata. They must
not call a difference a morphology effect, estimate a causal morphology term,
or generalise beyond the exact cohort. Do not report a model ranking or a
condition effect because neither varies here.

## Minimum focused checks

Before formal dispatch, run the smallest checks that establish:

1. all eleven exact package IDs resolve from complete local MJCF closures and
   source-backed task snapshots;
2. the Sonnet pin returns the declared identity and accepts the declared
   temperature, context, and output settings;
3. the shared Harness enforces candidate isolation, canonical physics,
   independent verdicts, anti-teleport checks, and complete videos;
4. one selected diagnostic cell demonstrates fresh TGCD and IVC, empty
   Experience, skeleton-assisted generation, the three-submission ceiling,
   and Task Demo recording; and
5. no Evolution call or Experience input is possible from the Experiment 3
   runner/protocol boundary.

Focused checks are diagnostic and do not enter the 33-cell denominator. They
must not silently replace an unavailable robot or model.
