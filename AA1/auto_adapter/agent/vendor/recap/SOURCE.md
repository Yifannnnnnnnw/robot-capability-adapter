# Official ReCAP source in AA1

- Repository: https://github.com/ReCAP-Stanford/ReCAP
- Revision: `2fb112ffad685c7c6f7de86d5487ecca6f566fcc`
- File: [robotouille-recap/MATRIX/MATRIX/chatbot.py](https://github.com/ReCAP-Stanford/ReCAP/blob/2fb112ffad685c7c6f7de86d5487ecca6f566fcc/robotouille-recap/MATRIX/MATRIX/chatbot.py)
- License: the upstream MIT license is reproduced in `LICENSE`.
- Retrieved directly from that revision on 2026-09-13.

`chatbot.py` is the complete upstream file with the small integration changes
recorded in `AA1.patch`. It is executed by `auto_adapter.agent.recap.run_recap`.
The patch makes OpenAI/token billing imports lazy, removes the unused Together
import, and adds explicit injection parameters for memory, prompts, node
construction and the output directory. The upstream task-tree dispatch loop is
unchanged except that node construction calls the injected `node_factory`.

AA1 integration lives outside this vendored file, in `agent/recap.py`:

- `Memory` replaces the standalone OpenAI transport with AA1's existing model
  client and uses a JSON format reminder instead of the Robotouille sandwich
  few-shot introduction. It retains the shared history and upstream `[2:4]`
  message eviction policy, with a default 32-message threshold.
- `RobotPrompt` inherits the official parent/child prompts. It changes cooking
  terminology, explains native-schema rejection, and replaces unconditional
  action-success wording with an instruction to inspect the returned status.
  Parent summaries, pending siblings and observations remain in these prompts.
- `ObservedNode` subclasses the official `Node` for tracing, plan-shape checks
  and the depth budget. It does not traverse or return between tasks.
- A schema-backed action set implements the official membership test. Primitive
  strings contain JSON `{ "capability_name": "...", "request": {...} }`;
  membership only validates. The host executes the yielded action and sends
  the resulting public observation back into the official generator.
- Host budgets bound actual model calls and capability executions. A dedicated
  termination exception bypasses the upstream malformed-plan retry handler.

The original native plan format is `{"think": "brief summary", "subtasks":
["string", "string"]}`. The former AA1 `submit_plan` controller has been removed.
The Robotouille environment, task data and other benchmark implementations are
not required by AA1. No code is imported from the retired `autoadapter2` package.
