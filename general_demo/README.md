# AutoAdapter 2.0 General Demo: Phase 1

This is a small local-only foundation. It implements canonical JSON, SHA-256
content hashes, seals, exact references, visibility allowlisting, an immutable
content-addressed store, and a minimal run state machine.

The built-in validator is explicitly named TEST_FIXTURE_SCHEMA_SUBSET. It is a
small test-fixture checker, not a full JSON Schema engine and not a claim that
the project's later formal schema contract is frozen. A standards-compliant
schema engine belongs to a later frozen contract.

The fixtures are synthetic test data only. They are not official robot, SDK,
morphology, or simulation records. The implementation intentionally does not
include robot shims, LLM calls, Blue Line, Stage 2, Validation, or Demo logic.

Run pytest -q from this directory.
