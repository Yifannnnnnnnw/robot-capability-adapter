# ReCAP Source Audit

## Frozen source identity

- Method: **ReCAP — Recursive Context-Aware Reasoning and Planning**
- Venue: NeurIPS 2025
- Paper: <https://arxiv.org/abs/2510.23822>
- Official repository: <https://github.com/ReCAP-Stanford/ReCAP>
- Source revision: `2fb112ffad685c7c6f7de86d5487ecca6f566fcc`
- Audited Robotouille implementation:
  `robotouille-recap/MATRIX/MATRIX/chatbot.py`
- License: MIT
- Audit date: 2026-08-21

At the frozen revision, ReCAP maintains a recursive context tree, asks the
model for a complete ordered subtask list, processes the head, and reinjects
the observation and remaining higher-level context before revising the rest.
An empty list completes the current node and returns control to its parent; an
empty list at the root stops the controller.

## AutoAdapter typed-capability adaptation

The implemented B2 core preserves that recursive plan-ahead, head-only
execution, context reinjection, parent return, and remainder-refinement loop.
The domain boundary is adapted as follows:

1. Robotouille's string action leaves become typed capability leaves of the
   form `{kind, capability_name, request}`.
2. `request` is mechanically validated against the sealed B1
   `request_schema`. The call crosses the worker boundary exactly as
   `driver.<capability_name>(request=<capability-native object>)`; there is no
   `task_id` or `task_parameters` wrapper.
3. Only a Framework-produced bounded public operation observation may return
   to the controller. Its fixed envelope contains `operation.status` with one
   of `EXECUTED`, `ERROR`, or `ABORT`, plus a projector-allowlisted public state.
   `ERROR` permits refinement; `ABORT` terminates further physical calls.
   Driver self-report and controller completion are not a physical success
   verdict.
4. The model transport is behind one replaceable-backbone protocol. The same
   backbone performs recursive decomposition and remainder refinement while
   the architecture, prompt, schemas, and budgets stay fixed.
5. AutoAdapter adds fixed model-call, capability-call, recursion-depth,
   invalid-output, plan-width, and history-window budgets. ReCAP-DPO is not
   part of this adapter.

## Implementation and admission boundary

- Typed interface adapter:
  `autoadapter/src/autoadapter2/b2/capability_adapter.py`
- Recursive controller core:
  `autoadapter/src/autoadapter2/b2/recap.py`
- Interface source for the current preparation:
  `experiment/experiment1/fixed_validation_bundles/*/capability_design.json`

This record is source-audited preparation, not a formal B2 admission. Formal
execution remains blocked until the persistent credential-free MuJoCo worker,
robot-specific public feedback projector, B2 compositional task/Harness suite,
exact driver and validation evidence, and full interface-bound validation are
fixed together. None of those blockers may be filled by changing the
controller after backbone outcomes are inspected.
