# High-Level Controllers

This catalogue records B2 controller architectures independently of whether
they enter the Chapter 3 experiment. Directory presence means that the
architecture is in the audit pool; it does not mean that a reproduction or
adapter is complete.

Each controller owns:

- `SOURCE.md`: paper, official code, pinned revision, architecture variant,
  and declared deviations;
- `controller.json`: model roles, backbone replacement classification,
  eligible robot and backbone sets, prompts, grammar, feedback, and budgets;
- `adapter.py` only after audit: public observation and capability-ABI mapping.

An adapter may not add planning, memory, recovery, scoring, low-level control,
or private Harness access absent from the declared method. A trained checkpoint
that requires retraining after a model change is not classified as a
replaceable-backbone controller.
