# Experiment 3 Direct-MuJoCo cohort authority

> **Document ID:** `AA2-EXP3`<br>
> **Revision:** `0.2.1`<br>
> **Effective date:** 2026-08-25<br>
> **Parent authority:** `AUTOADAPTER_2_AUTHORITY.md`<br>
> **Status:** configuration and zero-model preflight only; formal dispatch is not authorised in this preparation round

This document is the bounded authority for Experiment 3. The parent Authority
continues to govern the shared Framework, isolation, trusted-Harness and
physical-evidence boundaries. If this document is silent, the parent Authority
applies. Experiment 1b evidence is retained unchanged, and Experiment 2 is not
an input, gate or activity of this preparation round.

## 1. Question and claim boundary

Experiment 3 records whether the current AutoAdapter construction route can be
executed on the exact declared Direct-MuJoCo cohort. It is a descriptive cohort,
not a factorial morphology study.

It may report per-cell construction, Capability Validation, Repair and ReCAP
Task Demo outcomes. It must not claim a morphology effect, quadruped transfer,
an Experience effect, a model comparison, a generation-condition comparison,
hardware fidelity or universal robot support.

## 2. Fixed cohort and denominator

The matrix is exactly:

```text
11 robot configurations × 3 replicates × 1 model × 1 condition = 33 cells
```

The configuration order is:

1. `robotstudio_so101`
2. `unitree-go2-stock-12dof`
3. `franka_panda`
4. `kinova_gen3_robotiq_2f85`
5. `ufactory_xarm7`
6. `universal_robots_ur5e_robotiq_2f85`
7. `piper`
8. `kuka_iiwa_14`
9. `leap_hand`
10. `hello_robot_stretch_2`
11. `aloha_2`

Each configuration is crossed once with `r01`, `r02` and `r03`. Every cell
uses Sonnet 4.6, skeleton assistance and empty Experience. The 33 rows remain
the denominator even when a row ends in a model, transport, package, Harness,
video or infrastructure failure. A failed row is never replaced.

For reporting only, the six SO-101/Go2 rows are `reference-seen controls`:
their complete capability references are present in the model-visible TGCD
reference. The remaining 27 rows are `transfer cells`. This label records
reference exposure; it is not a transfer-effect factor and must not be
interpreted as a quadruped-transfer result.

## 3. Fresh per-cell route

Each cell receives a new workspace, model conversation, canonical MuJoCo
session, STUDY artifact, TGCD design, IVC suite, candidate and evidence record.
No cell may read another cell's artifacts.

```text
STUDY -> TGCD -> IVC -> Generate -> Capability Validation
      -> at most two Repair revisions -> ReCAP Task Demo -> stop
```

- STUDY writes and seals `study.json` before TGCD.
- TGCD writes and seals `capability_design.json`.
- IVC writes and seals `capability_validation_suite.json` only after the
  Framework accepts its inline request, measurement, source, entity, guard
  and exact-criteria audit. No reference-driver positive control is a gate.
- Generate/Repair writes `driver.py`. A revision becomes a formal attempt only
  after source and import checks freeze it successfully.
- The trusted private Harness runs only on a frozen Driver. There are at most
  three frozen and validated Driver attempts in total.
- ReCAP receives only capabilities for which both the nominal and calibrated
  boundary cases passed. Its completion text is not a verdict; the Task Demo
  Harness owns the verdict and required videos.
- The cell stops after Task Demo, or after a truthful Task Demo not-run record
  when no Driver was admitted. Evolution and Experience output are disabled.

## 4. File-delivery and phase budgets

There are no `submit_study`, `submit_capability_design`,
`submit_validation_suite` or `submit_driver` tools. A phase ends by normal
model completion followed by Framework validation of its canonical artifact.
An invalid artifact is returned as a deterministic tool observation to the
same conversation while turns remain. A valid artifact written on the last
turn is accepted without another closing message.

| Phase | Maximum model turns |
|---|---:|
| STUDY | 16 |
| TGCD | 6 |
| IVC | 6 |
| Skeleton Generate | 22 |
| Skeleton Repair | 22 |
| ReCAP, per task | 16 planning turns |

ReCAP additionally permits at most 12 capability calls per task. There is no
aggregate tool-call ceiling for STUDY, TGCD, IVC, Generate or Repair. Each
individual file and Python tool call remains constrained by workspace paths,
execution timeout, MuJoCo step/simulated-time limits, output size and the
credential-free environment.

## 5. Capability and IVC contract

TGCD autonomously creates three to ten reusable capabilities. Every capability
defines its method, description, physical effect, closed request schema,
units, frames, calibrated bounds and evidence sources, preconditions, temporal
semantics, invariants, public failure behaviour and structured capability
criteria. Numeric values must resolve to a public source or retained real
calibration.

The top-level many-to-many `task_support` relation records only which
capabilities can support which source tasks and why. It cannot encode a call
order, waypoint sequence, task macro or oracle plan. Candidate requests and
Driver code cannot contain `task_id` dispatch, scene/reset construction, a
complete task or private criteria.

The complete model-visible reference contains the six SO-101 and five Go2
capability designs, parameters and real criteria only. It contains no task
mapping, `task_support`, oracle plan or concrete call programme.

IVC sees the complete sealed design including `task_support`, source-task
lineage, sanitised scene/reset and mandatory-guard context, observable MuJoCo
entities, the closed trusted measurement-operator catalogue, all task-backed
measurement examples, and the complete SO-101/Go2 worked references (22 cases).
It may inspect the admitted MJCF asset closure with credential-free
`execute_python`. It never sees candidate code, Repair history, candidate
traces or verdicts, Experience, old task/reference requests, or an execution
plan.

For every capability, IVC authors exactly one `nominal` and one
`calibrated_boundary` case with different closed native requests and an inline
`measurement_binding`. It copies the sealed criteria exactly and resolves the
boundary request to the sealed schema or retained real calibration. Dynamic
`binding_id`, arbitrary code, task dispatch, expected outcomes and self-reported
verdicts are forbidden. Before candidate code starts, the Framework checks the
request paths, sources, operator and closed parameters, metric/unit, selected
scene entities, mandatory guards, finite numbers and exact criteria copy. The
Harness then sends only `method(request=<IVC-authored request>)` to the candidate;
measurements, guards, criteria and verdicts stay in the trusted parent.

## 6. Isolation, evidence and analysis

Experience is empty and invisible in every Experiment 3 cell. IVC, the private
Harness and ReCAP never receive Experience. Candidate workers and public
Python/MuJoCo sessions contain no model, cloud or repository credentials.
Private suites, bindings, guards, reference source and Harness implementation
remain unavailable to STUDY, TGCD, Generate, Repair, Driver and ReCAP.

Every formal row must retain exact model and returned-model identity, token and
wall-time evidence, canonical artifacts and traces, frozen Driver-attempt
count, Capability Validation reports, Task Demo report or truthful not-run
reason, and complete per-trial videos when execution occurred. Results are
summarised by exact configuration and by the reference-seen/transfer exposure
labels only; morphology remains descriptive metadata.

## 7. Preparation gate and dispatch boundary

Before any formal request, zero-model checks must establish:

- the manifest expands to all and only the 33 declared rows;
- all eleven indexed packages and their `public_observations` load;
- all eleven package scene/entity/operator inputs build, with task and
  capability contexts merged rather than one hiding the other;
- deterministic artifacts pass TGCD-to-IVC audit, and real SO-101 plus Go2
  nominal/boundary inline-measurement smokes produce trusted Harness verdicts;
- canonical artifact completion, invalid-artifact recovery, final-turn
  acceptance and frozen-attempt accounting pass focused checks;
- IVC is candidate-blind and candidate source rejects `task_id` dispatch;
- skeleton visibility, empty Experience, ReCAP 16/12 and Evolution exclusion
  are enforced.

Passing this preparation gate does not authorise the 33 model cells. Formal
dispatch requires a later explicit project-owner approval. The runner's
`design-check` and `preflight` commands are allowed now; `formal` and `resume`
remain present for the later approved run but must not be invoked in this
preparation round.
