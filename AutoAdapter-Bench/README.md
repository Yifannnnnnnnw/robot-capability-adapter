# AutoAdapter-Bench

AutoAdapter-Bench is the composable experiment layer for Auto-Adapter 2.0. It
defines the complete benchmark, selects reusable components through experiment
recipes, invokes the canonical Direct-MuJoCo mainline, and retains raw results
for later analysis.

## Ownership boundary

- `../autoadapter/libraries/robots/` owns canonical robot packages, assets,
  Task Libraries, capability interfaces, private suites, and reference drivers.
- `../autoadapter/src/autoadapter2/` owns environment creation, driver
  synthesis, Direct-MuJoCo execution, trusted Harness verdicts, and evidence.
- This directory owns benchmark protocols, component selection, high-level
  controller adapters, thin runners, and experiment recipes.

Robot sets contain canonical configuration IDs only. They never copy MJCF,
task, driver, or private Harness files from the mainline.

## Layout

```text
benchmark.md                   complete scientific protocol
backbones/                     declared Producer model registry
protocols/                     locked B1 and B2 execution and recording rules
components/                    reusable robot, backbone, task, and replicate sets
high_level_controllers/        B2 controller catalogue and audited adapters
experiments/                   concrete Chapter 3, pilot, and full recipes
runners/                       manifest resolution and thin execution entry points
analysis/                      independent post-run analysis added when data exist
tests/                         focused benchmark-contract checks
runs/                          ignored local outputs
```

## Benchmark split

- **B1 Driver Synthesis** ends after private driver validation and bounded
  Repair. It does not run Task Demo or a high-level controller.
- **B2 Capability-Interface Use** uses one fixed validated driver and interface,
  runs compositional tasks under a selected high-level controller, and uses the
  trusted Harness for physical task verdicts.

The approved Chapter 3 B1 recipe contains 490 full-cohort
`skeleton-assisted` replicates and 175 selected-case `from-scratch` replicates,
for 665 generation-condition replicates in total. B2 contributes 280 controller
episodes per admitted architecture in the two-robot Chapter 3 recipe.

Run `python runners/manifest.py validate` from this directory to resolve the
recipes, report missing mainline inputs, and verify the declared matrix sizes.
No runner reads credentials from a checked-in file.
