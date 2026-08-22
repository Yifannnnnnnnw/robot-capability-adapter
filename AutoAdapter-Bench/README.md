# AutoAdapter-Bench

AutoAdapter-Bench is the reusable benchmark-definition layer for Auto-Adapter
2.0. It owns protocols, registries, catalogues, reusable component inventories,
and generic manifest-resolution code. It does not select a current experiment
cohort, set an experiment replicate count, retain formal run outputs, or own an
experiment-specific analysis denominator.

Concrete experiments live under `../experiment/`. Experiment 1 is governed
only by `../experiment/experiment1a_generation/EXPERIMENT_1_AUTHORITY.md`; its JSON manifest
is a subordinate machine-readable implementation of that authority.

## Ownership boundary

- `../AUTOADAPTER_2_AUTHORITY.md` owns project-wide architecture and evidence
  requirements and delegates bounded experiment authorities.
- `../autoadapter/libraries/robots/` owns canonical robot packages, assets,
  Task Libraries, capability interfaces, private suites, and reference drivers.
- `../autoadapter/src/autoadapter2/` owns environment creation, driver
  synthesis, Direct-MuJoCo execution, trusted Harness verdicts, and evidence.
- This directory owns reusable benchmark protocols, model and controller
  catalogues, component inventories, generic resolvers, and focused contract
  checks.
- `../experiment/<experiment-id>/` owns the exact experimental selection,
  manifest, run artifacts, and experiment-specific analysis.

Robot inventories contain canonical configuration IDs only. They never copy
MJCF, task, driver, or private Harness files from the mainline.

## Layout

```text
benchmark.md                   reusable scientific benchmark contract
backbones/                     Producer model registry
protocols/                     B1 and B2 execution/recording contracts
components/                    reusable inventories and component sets
high_level_controllers/        B2 controller catalogue and audited adapters
runners/                       generic manifest resolution
tests/                         focused benchmark-contract checks
```

There is intentionally no `experiments/`, `runs/`, or experiment-specific
analysis directory here. A benchmark protocol may constrain an experiment, but
it never silently supplies that experiment's cohort, R, stopping rule, or
denominator.
