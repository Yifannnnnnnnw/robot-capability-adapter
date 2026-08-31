# AutoAdapter-Bench

AutoAdapter-Bench is the reusable benchmark-definition layer for Auto-Adapter
2.0. It owns protocols, registries, catalogues, reusable component inventories,
and generic manifest-resolution code. It does not select a current experiment
cohort, set an experiment replicate count, retain formal run outputs, or own an
experiment-specific analysis denominator.

There is currently no active experiment workspace. The former experiment
manifests, runners, tests, and evidence are retained for historical inspection
under
`../experiment/archive/pre_thesis_realign_2026-08-31/`; they are not a
supported execution mainline and do not contribute to a future experiment
denominator. A new experiment workspace will be added only after its
thesis-aligned manifest and protocol are separately agreed.

## Ownership boundary

- The formally compiled thesis rooted at `../thesis/Main.tex` supplies the
  current research numbering. It does not by itself activate an experiment
  runner.
- `../autoadapter/libraries/robots/` owns canonical robot packages, assets,
  Task Libraries, capability interfaces, private suites, and reference drivers.
- `../autoadapter/src/autoadapter2/` owns environment creation, driver
  synthesis, Direct-MuJoCo execution, trusted Harness verdicts, and evidence.
- This directory owns reusable benchmark protocols, model and controller
  catalogues, component inventories, generic resolvers, and focused contract
  checks.
- A future approved `../experiment/<experiment-id>/` workspace will own its
  exact selection, manifest, run artifacts, and experiment-specific analysis.

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
analysis directory here. A benchmark protocol may constrain a future
experiment, but it never silently supplies that experiment's cohort, R,
stopping rule, or denominator.
