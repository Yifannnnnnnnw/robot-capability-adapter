# Private Blue Line governance

This directory contains Framework-private preparation material for capability-level Validation B.
It is **not** a fifth Generation Library and it is **not** the downstream Tasks Library.

## Inputs and ownership

- `standards/reference_candidates.json` contains the cleaned seed records migrated from the
  temporary root `VALIDATION_TASK_GENERATION_REFERENCE_LIBRARY.md`.
- Downstream Demo task descriptions and private Demo criteria live only under
  `general_demo/libraries/tasks/`.
- Morphology, SDK, observation, frame, and entity facts come from their owning mainline records;
  they are not redefined here.

The candidate catalog is preparation material. A formal Blue Line run never reads it directly.
Before a formal comparison, a human reviews exact record scope and the Framework creates a frozen,
content-addressed Validation Standards Snapshot containing only the selected records.

## Rules for the Blue Line LLM

Every generated criterion uses one provenance label:

- `COPIED`: measurement, metric, comparator, value, unit, temporal rule, aggregation, robot and
  configuration scope, protocol, effect, and intended use are unchanged from one selected record;
- `ADAPTED`: one or more of those fields changed; material changes require pre-experiment review;
- `PROPOSED`: no applicable record exists; the result is `NEEDS_REVIEW` until a human approves a
  new record and a new snapshot is frozen.

The Blue Line must not transfer a number merely because two descriptions look similar. It must not
use an SDK receipt, candidate `success` value, controller target, physical range, simulation
timestep, or observed candidate performance as verdict truth or as an acceptance threshold.
Physical-effect claims use Framework-trusted physical state and name the entity, signal, unit,
frame, metric, comparator, time window, aggregation, and false-pass guards.

The Blue Line cannot inspect Stage 2 source, Sandbox output, candidate behavior, Repair history,
Validation outcomes, or Demo outcomes. Criteria, cases, seeds, and trusted measurements remain
private from Stage 2 and Consumers.

## Migration note

The temporary root reference mixed validation criteria with historical runtime adapters, drivers,
and orchestrators for unrelated morphologies. Those implementation columns and unrelated records
were intentionally discarded. The retained records cover only the first Demo's SO-ARM101 and
Unitree Go2 families and remain explicitly scoped reference candidates.
