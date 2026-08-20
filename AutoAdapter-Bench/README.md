# AutoAdapter-Bench

This directory is the experiment layer for the Auto-Adapter 2.0 benchmark. It
owns the benchmark protocol, run selection, published high-level-controller
adapters, thin runners, and analysis. It does not duplicate the canonical
robot packages or the Direct-MuJoCo runtime.

## Boundary

- `../autoadapter/libraries/robots/` owns each fixed robot package, including
  morphology, source-backed tasks, private evaluation inputs, the fixed
  capability interface, and the benchmark-supplied validated reference driver.
- `../autoadapter/src/autoadapter2/` owns driver synthesis, Direct-MuJoCo
  execution, the trusted Harness, reporting, and shared runtime code.
- This directory selects those inputs and methods for B1 and B2. It must refer
  to canonical paths instead of copying robot assets, drivers, or private
  suites.

## Benchmark split

- **B1 Driver Synthesis:** every Producer receives the same fixed public
  capability interface for a robot and is evaluated by the same fixed private
  validation suite. The model performs only STUDY, GENERATE/GEN_ALGO, and
  bounded Repair.
- **B2 Published High-Level Control:** every controller uses the same
  benchmark-supplied, validated reference driver for a robot. B2 does not
  select or consume a B1-generated driver.

## Proposed structure

```text
AutoAdapter-Bench/
  README.md                 # ownership and navigation
  benchmark.md              # sole benchmark protocol
  configs/                  # fixed cohort, models, methods, budgets, and run selection
  methods/                  # one faithful published-method adapter per directory
  runners/                  # thin B1 and B2 experiment entry points
  analysis/                 # summaries, confidence intervals, and report tables
  tests/                    # focused benchmark-contract and false-success checks
  runs/                     # ignored local outputs; formal evidence stays run-addressable
```

Directories gain implementation files only when their corresponding protocol
decision is fixed. This avoids creating a second robot library, Harness, or
experiment framework inside the benchmark.
