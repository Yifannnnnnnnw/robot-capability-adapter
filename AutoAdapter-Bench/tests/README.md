# Focused Benchmark Checks

Benchmark checks cover reusable contracts only:

- the reusable robot inventory retains unique canonical/research IDs;
- the declared Producer set resolves to the seven registry entries;
- B1 starts at STUDY, uses at most three submitted drivers, and cannot enable
  Task Demo or a high-level controller;
- no concrete experiment recipe or run-output directory is owned by
  `AutoAdapter-Bench/`; and
- every high-level-controller catalogue entry resolves to its audit files.

Concrete matrix accounting belongs to the tests of a separately approved
experiment workspace. There is no active experiment test suite at present.
The former runner-specific tests are preserved for historical inspection in
`../../experiment/archive/pre_thesis_realign_2026-08-31/shared_tests/`; they
are not collected as current benchmark checks.
